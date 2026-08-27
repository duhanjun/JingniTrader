"""行情全粒度接入测试（REQ-2026-08-15）：get_kline 契约 + 各源周期路由 + 归一化。

覆盖：
1. 契约扩展：data_types.KLINE_PERIODS / PERIOD_DISPLAY / KLINE_CONTRACT_COLS 存在且完整。
2. BaseDataProvider.get_kline 默认 period=day 映射到 get_daily（向后兼容）。
3. tencent：day 走 urllib 直连返回标准化契约；非 day 抛 InvalidParameterError
   （westock 实测仅 day，分钟/周月季年不可信）。
4. baostock：week/m5 经 frequency 映射真实归一化；m1 抛 InvalidParameterError。
5. akshare：day 走 get_daily 链；week/month 经 stock_zh_a_hist(period) 映射；
   m5/m15/m30/m60 经 stock_zh_a_hist_min_em 映射；m1 抛 InvalidParameterError。
6. 归一化：返回 DataFrame 含标准契约列 code/date/open/high/low/close/volume/amount。

策略：
- 不依赖真实网络（mock 底层库 / 直连方法）。
- 遵循仓库隔离纪律：适配器模块以独立名加载，测试后清理 scripts 缓存。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
from unittest import mock

import pandas as pd
import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(ROOT, "skills", "data-engine", "scripts")


def _reset_scripts():
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)
    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)


def _load_adapter_module(mod_name: str, file_name: str):
    """以独立模块名加载 adapters/<file_name>，避免 scripts 包缓存污染。"""
    _reset_scripts()
    path = os.path.join(SCRIPTS_DIR, "adapters", file_name)
    spec = ilu.spec_from_file_location(mod_name, path)
    mod = ilu.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_baostock_result(fields, rows):
    """构造 baostock query_history_k_data_plus 的假 result 对象。

    真实 baostock 用法：while rs.next(): data.append(rs.get_row_data())。
    next() 返回当前行（推进索引），get_row_data() 返回同一当前行。
    """

    class _RS:
        def __init__(self, fields, rows):
            self.fields = fields
            self._rows = list(rows)
            self._idx = -1
            self.error_code = "0"

        def next(self):
            self._idx += 1
            if self._idx < len(self._rows):
                return self._rows[self._idx]
            return None

        def get_row_data(self):
            if 0 <= self._idx < len(self._rows):
                return self._rows[self._idx]
            return None

    return _RS(fields, list(rows))


# ---------------------------------------------------------------------------
# 契约扩展
# ---------------------------------------------------------------------------


@pytest.mark.skill_data_engine
@pytest.mark.unit
def test_kline_period_constants_exist():
    _reset_scripts()
    dt = ilu.module_from_spec(
        ilu.spec_from_file_location("scripts.data_types", os.path.join(SCRIPTS_DIR, "data_types.py"))
    )
    sys.modules["scripts.data_types"] = dt
    ilu.spec_from_file_location("scripts.data_types", os.path.join(SCRIPTS_DIR, "data_types.py"))
    spec = ilu.spec_from_file_location("scripts.data_types", os.path.join(SCRIPTS_DIR, "data_types.py"))
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.data_types"] = mod
    spec.loader.exec_module(mod)

    assert hasattr(mod, "KLINE_PERIODS")
    for p in ("m1", "m5", "m15", "m30", "m60", "m120", "day", "week", "month", "season", "year"):
        assert p in mod.KLINE_PERIODS, f"KLINE_PERIODS 缺少 {p}"
    assert hasattr(mod, "KLINE_CONTRACT_COLS")
    for c in ("code", "date", "open", "high", "low", "close", "volume", "amount"):
        assert c in mod.KLINE_CONTRACT_COLS


# ---------------------------------------------------------------------------
# BaseDataProvider 默认 day → get_daily
# ---------------------------------------------------------------------------


@pytest.mark.skill_data_engine
@pytest.mark.unit
def test_base_get_kline_day_maps_to_daily():
    """period=day 时基类默认映射到 get_daily（不破坏现有调用）。"""
    _reset_scripts()
    base = ilu.module_from_spec(
        ilu.spec_from_file_location(
            "scripts.base.base_data_provider", os.path.join(SCRIPTS_DIR, "base", "base_data_provider.py")
        )
    )
    sys.modules["scripts.base.base_data_provider"] = base
    spec = ilu.spec_from_file_location(
        "scripts.base.base_data_provider", os.path.join(SCRIPTS_DIR, "base", "base_data_provider.py")
    )
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.base.base_data_provider"] = mod
    spec.loader.exec_module(mod)

    calls = {}

    class _Fake(BaseDataProvider := mod.BaseDataProvider):
        def get_daily(self, symbols, start_date="", end_date="", adjust="hfq"):
            calls["daily"] = (symbols, start_date, end_date, adjust)
            return pd.DataFrame({"code": symbols})

        def get_stock_list(self):
            return pd.DataFrame()

        def get_adj_factor(self, *a, **k):
            return pd.DataFrame()

        def get_financial(self, *a, **k):
            return pd.DataFrame()

    fake = _Fake()
    fake.get_kline(["600519.SH"], period="day", start_date="2026-01-01", end_date="2026-08-14")
    assert "daily" in calls
    assert calls["daily"][0] == ["600519.SH"]


# ---------------------------------------------------------------------------
# tencent
# ---------------------------------------------------------------------------


@pytest.mark.skill_data_engine
@pytest.mark.unit
def test_tencent_kline_day_and_nonday():
    """westock: day 走 urllib 直连返回契约；非 day/未知周期抛 InvalidParameterError。

    注：westock CLI 真实支持 m1/m5/m15/m30/m60/m120/day/week/month/season/year，
    故任意合法非 day 周期不再抛错（走 CLI 真实取数）；仅 m250/未知字符串抛 InvalidParameterError。
    """
    mod = _load_adapter_module("test_westock_kline", "westock_adapter.py")
    adapter = mod.WestockAdapter()

    # mock 直连 K 线（绕过网络），返回标准化日线行
    fake_daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-14", "2026-08-13"]),
            "code": ["600519.SH", "600519.SH"],
            "open": [1355.0, 1338.0],
            "high": [1359.0, 1359.6],
            "low": [1338.14, 1337.0],
            "close": [1341.99, 1355.29],
            "volume": [29853.0, 32353.0],
            "amount": [float("nan"), float("nan")],
        }
    )
    with mock.patch.object(adapter, "_get_daily_one", return_value=fake_daily):
        df = adapter.get_kline(["600519.SH"], period="day")
    assert list(df.columns)[:8] == ["date", "code", "open", "high", "low", "close", "volume", "amount"]
    assert len(df) == 2

    # 合法非 day 周期（m5）：westock CLI 真实支持 → 不应抛错（mock _run_westock 返回样本）
    kline_sample = [
        {
            "date": "2026-08-14 15:00:00",
            "open": 1342.84,
            "last": 1341.99,
            "high": 1342.95,
            "low": 1341.99,
            "volume": 1053,
            "amount": 141304631,
        },
        {
            "date": "2026-08-14 14:55:00",
            "open": 1343.3,
            "last": 1342.77,
            "high": 1343.3,
            "low": 1342.08,
            "volume": 733,
            "amount": 98382957,
        },
    ]
    with mock.patch.object(mod, "_run_westock", return_value=kline_sample):
        df_m5 = adapter.get_kline(["600519.SH"], period="m5")
    assert len(df_m5) == 2
    assert list(df_m5.columns)[:8] == ["date", "code", "open", "high", "low", "close", "volume", "amount"]

    # m250 不存在（westock 别名为 m120）→ 抛 InvalidParameterError
    with pytest.raises(mod.InvalidParameterError if hasattr(mod, "InvalidParameterError") else Exception):
        adapter.get_kline(["600519.SH"], period="m250")


# ---------------------------------------------------------------------------
# baostock
# ---------------------------------------------------------------------------


@pytest.mark.skill_data_engine
@pytest.mark.unit
def test_baostock_kline_week_m5_and_unsupported():
    """baostock: week/m5 归一化返回；m1 抛 InvalidParameterError。"""
    mod = _load_adapter_module("test_baostock_kline", "baostock_adapter.py")

    # mock baostock 库
    fake_bs = mock.MagicMock()
    fake_bs.login.return_value = None
    fake_bs.logout.return_value = None
    fake_bs.query_history_k_data_plus.side_effect = lambda *a, **k: _fake_baostock_result(
        ["date", "open", "high", "low", "close", "volume", "amount"],
        [
            ["2026-08-14", "1355", "1359", "1338.14", "1341.99", "29853", "4024070000"],
            ["2026-08-07", "1338", "1359.6", "1337", "1355.29", "32353", "4376210000"],
        ],
    )
    with mock.patch.object(mod, "bs", fake_bs):
        adapter = mod.BaostockAdapter()
        df_week = adapter.get_kline(["600519.SH"], period="week", start_date="2026-01-01", end_date="2026-08-14")
        assert list(df_week.columns) == ["code", "date", "open", "high", "low", "close", "volume", "amount"]
        assert len(df_week) == 2

        df_m5 = adapter.get_kline(["600519.SH"], period="m5", start_date="2026-08-01", end_date="2026-08-14")
        assert len(df_m5) == 2

        # m1 不被 baostock 支持
        with pytest.raises(mod.InvalidParameterError if hasattr(mod, "InvalidParameterError") else Exception):
            adapter.get_kline(["600519.SH"], period="m1")


# ---------------------------------------------------------------------------
# akshare
# ---------------------------------------------------------------------------


@pytest.mark.skill_data_engine
@pytest.mark.unit
def test_akshare_kline_routing_and_unsupported():
    """akshare: day→get_daily 链；week/month→stock_zh_a_hist；m5→stock_zh_a_hist_min_em；m1 抛错。"""
    mod = _load_adapter_module("test_akshare_kline", "akshare_adapter.py")

    fake_ak = mock.MagicMock()

    # day/week/month 走 stock_zh_a_hist
    def _hist(symbol, period, start_date, end_date, adjust):
        if period in ("daily", "weekly", "monthly"):
            return pd.DataFrame(
                {
                    "日期": ["2026-08-14", "2026-08-07"],
                    "开盘": [1355, 1338],
                    "收盘": [1341.99, 1355.29],
                    "最高": [1359, 1359.6],
                    "最低": [1338.14, 1337],
                    "成交量": [29853, 32353],
                    "成交额": [4024070000, 4376210000],
                    "振幅": [1.5, 1.6],
                    "涨跌幅": [1.0, -1.0],
                    "涨跌额": [13, -13],
                    "换手率": [0.2, 0.3],
                }
            )
        return None

    fake_ak.stock_zh_a_hist.side_effect = _hist
    fake_ak.stock_zh_a_hist_min_em.side_effect = lambda *a, **k: pd.DataFrame(
        {
            "时间": ["2026-08-14 09:35", "2026-08-14 09:40"],
            "开盘": [1355, 1356],
            "收盘": [1356, 1357],
            "最高": [1357, 1358],
            "最低": [1354, 1355],
            "成交量": [1000, 1100],
            "成交额": [1355000, 1356000],
        }
    )
    fake_ak.fund_etf_hist_em.return_value = None
    fake_ak.stock_zh_a_daily.side_effect = lambda *a, **k: pd.DataFrame(
        {
            "date": ["2026-08-14", "2026-08-07"],
            "open": [1355, 1338],
            "close": [1341.99, 1355.29],
            "high": [1359, 1359.6],
            "low": [1338.14, 1337],
            "volume": [29853, 32353],
            "amount": [4024070000, 4376210000],
        }
    )

    with mock.patch.object(mod, "ak", fake_ak):
        adapter = mod.AkshareAdapter()

        # day 经 get_daily（stock_zh_a_hist 成功路径）
        df_day = adapter.get_kline(["600519.SH"], period="day", start_date="2026-08-01", end_date="2026-08-14")
        assert len(df_day) == 2
        assert "close" in df_day.columns

        # week 经 stock_zh_a_hist(period=weekly)
        df_week = adapter.get_kline(["600519.SH"], period="week", start_date="2026-01-01", end_date="2026-08-14")
        assert len(df_week) == 2
        assert list(df_week.columns) == ["code", "date", "open", "high", "low", "close", "volume", "amount"]

        # m5 经 stock_zh_a_hist_min_em
        df_m5 = adapter.get_kline(["600519.SH"], period="m5", start_date="2026-08-01", end_date="2026-08-14")
        assert len(df_m5) == 2

        # m1 不支持
        with pytest.raises(mod.InvalidParameterError if hasattr(mod, "InvalidParameterError") else Exception):
            adapter.get_kline(["600519.SH"], period="m1")


@pytest.mark.skill_data_engine
@pytest.mark.unit
def test_akshare_hfq_explicit_passthrough():
    """显式 hfq 必须原样透传（修复 OPEN-2026-0817-01）：fund_etf_hist_em / stock_zh_a_daily
    回退链此前把 hfq 静默降级为 qfq；stock_zh_a_hist / stock_zh_a_hist_min_em 直连路径亦需保持不变。

    覆盖两条 ETF/回退分支 + 两条直连分支，断言 adjust 实参 == "hfq"。
    """
    mod = _load_adapter_module("test_akshare_hfq", "akshare_adapter.py")

    calls = {"etf": [], "daily_hist": [], "daily_fallback": [], "min": []}
    fake_ak = mock.MagicMock()

    def _hist(symbol, period, start_date, end_date, adjust):
        calls["daily_hist"].append(adjust)
        return pd.DataFrame(
            {
                "日期": ["2026-08-14"],
                "开盘": [1355], "收盘": [1341.99], "最高": [1359], "最低": [1338.14],
                "成交量": [29853], "成交额": [4024070000], "振幅": [1.5],
                "涨跌幅": [1.0], "涨跌额": [13], "换手率": [0.2],
            }
        )

    fake_ak.stock_zh_a_hist.side_effect = _hist
    fake_ak.stock_zh_a_hist_min_em.side_effect = lambda *a, **k: (
        calls["min"].append(k.get("adjust")),
        pd.DataFrame(
            {
                "时间": ["2026-08-14 09:35"], "开盘": [1355], "收盘": [1356],
                "最高": [1357], "最低": [1354], "成交量": [1000], "成交额": [1355000],
            }
        ),
    )[1]
    fake_ak.fund_etf_hist_em.side_effect = lambda *a, **k: (
        calls["etf"].append(k.get("adjust")),
        pd.DataFrame(
            {
                "日期": ["2026-08-14"], "开盘": [1355], "收盘": [1341.99], "最高": [1359],
                "最低": [1338.14], "成交量": [29853], "成交额": [4024070000], "振幅": [1.5],
                "涨跌幅": [1.0], "涨跌额": [13], "换手率": [0.2],
            }
        ),
    )[1]
    # 回退链：stock_zh_a_hist 抛错 → 触发 stock_zh_a_daily（记录其 adjust）
    def _hist_fail_then_fallback(symbol, period, start_date, end_date, adjust):
        if period == "daily" and symbol == "600519":
            raise RuntimeError("forced fallback")
        calls["daily_hist"].append(adjust)
        return pd.DataFrame(
            {
                "日期": ["2026-08-14"], "开盘": [1355], "收盘": [1341.99], "最高": [1359],
                "最低": [1338.14], "成交量": [29853], "成交额": [4024070000], "振幅": [1.5],
                "涨跌幅": [1.0], "涨跌额": [13], "换手率": [0.2],
            }
        )

    fake_ak.stock_zh_a_hist.side_effect = _hist_fail_then_fallback
    fake_ak.stock_zh_a_daily.side_effect = lambda *a, **k: (
        calls["daily_fallback"].append(k.get("adjust")),
        pd.DataFrame(
            {
                "date": ["2026-08-14"], "open": [1355], "close": [1341.99], "high": [1359],
                "low": [1338.14], "volume": [29853], "amount": [4024070000],
                "change_pct": [1.0], "turnover_rate": [0.2],
            }
        ),
    )[1]

    with mock.patch.object(mod, "ak", fake_ak):
        adapter = mod.AkshareAdapter()

        # ETF 标的（510300 识别为 ETF）→ fund_etf_hist_em 必须收到 adjust="hfq"
        adapter.get_kline(["510300.SH"], period="day", start_date="2026-08-01", end_date="2026-08-14", adjust="hfq")
        assert calls["etf"] and calls["etf"][-1] == "hfq", f"fund_etf_hist_em adjust 应为 hfq，实际 {calls['etf']}"

        # A 股直连路径：stock_zh_a_hist 收到 adjust="hfq"
        adapter.get_kline(["000001.SZ"], period="day", start_date="2026-08-01", end_date="2026-08-14", adjust="hfq")
        assert "hfq" in calls["daily_hist"], f"stock_zh_a_hist adjust 应含 hfq，实际 {calls['daily_hist']}"

        # 回退链：stock_zh_a_daily 必须收到 adjust="hfq"（修复点：此前被降级为 qfq）
        adapter.get_kline(["600519.SH"], period="day", start_date="2026-08-01", end_date="2026-08-14", adjust="hfq")
        assert calls["daily_fallback"] and calls["daily_fallback"][-1] == "hfq", (
            f"stock_zh_a_daily adjust 应为 hfq，实际 {calls['daily_fallback']}"
        )

        # 分钟线直连：stock_zh_a_hist_min_em 收到 adjust="hfq"
        adapter.get_kline(["000001.SZ"], period="m5", start_date="2026-08-01", end_date="2026-08-14", adjust="hfq")
        assert calls["min"] and calls["min"][-1] == "hfq", f"stock_zh_a_hist_min_em adjust 应为 hfq，实际 {calls['min']}"
