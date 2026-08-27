"""IfindAdapter 单元测试。

验证 ifind_adapter.py 的核心逻辑，不依赖真实 iFinD 服务：
- 模块可正常 import
- THS_HQ 调用参数（indicators/params/CPS）正确
- DataFrame 字段映射到标准 schema
- 复权方式 hfq/qfq/none 映射到 CPS 1/2/无
- 股票代码自动加 CPS，非股票不加
- 登录失败/查询错误抛出 DataSourceError/NetworkError

设计：用 unittest.mock 注入假的 iFinDPy 模块，验证适配器调用 THS_HQ 的参数。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
from unittest import mock

import pytest
import pandas as pd
import numpy as np


# ============================================================================
# 模块加载：把 data-engine/scripts 注册为 scripts 包，加载 ifind_adapter
# ============================================================================

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")


def _load_ifind_adapter(monkeypatch, username="test_user", password="test_pass"):
    """加载 ifind_adapter 模块，注入假的 iFinDPy。"""
    # 清理 scripts 缓存
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # 注册 data-engine/scripts 为 scripts 包
    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts",
        init_py,
        submodule_search_locations=[SCRIPTS_DIR],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 注入假的 iFinDPy（ifind_adapter 在 __init__ 中 from iFinDPy import ...）
    fake_THS_iFinDLogin = mock.MagicMock(return_value=0)  # 0 = 登录成功
    fake_THS_HQ = mock.MagicMock()
    fake_THSData = mock.MagicMock()
    fake_THS_HistoryQuotes = mock.MagicMock()
    fake_THS_RealtimeQuotes = mock.MagicMock()
    fake_THS_ReportQuery = mock.MagicMock()
    fake_ifind_module = mock.MagicMock()
    fake_ifind_module.THS_iFinDLogin = fake_THS_iFinDLogin
    fake_ifind_module.THS_HQ = fake_THS_HQ
    fake_ifind_module.THSData = fake_THSData
    fake_ifind_module.THS_HistoryQuotes = fake_THS_HistoryQuotes
    fake_ifind_module.THS_RealtimeQuotes = fake_THS_RealtimeQuotes
    fake_ifind_module.THS_ReportQuery = fake_THS_ReportQuery
    sys.modules["iFinDPy"] = fake_ifind_module

    # 注入假的 IFIND_USERNAME/PASSWORD（config 从环境变量读）
    monkeypatch.setenv("IFIND_USERNAME", username)
    monkeypatch.setenv("IFIND_PASSWORD", password)
    # 重置 scripts.config 缓存，使其重新读取环境变量
    sys.modules.pop("scripts.config", None)

    # 加载 ifind_adapter
    adapter_path = os.path.join(SCRIPTS_DIR, "adapters", "ifind_adapter.py")
    spec = ilu.spec_from_file_location("scripts.adapters.ifind_adapter", adapter_path)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.adapters.ifind_adapter"] = mod
    spec.loader.exec_module(mod)
    # 新 mock（THS_HistoryQuotes/THS_RealtimeQuotes/THS_ReportQuery）挂在
    # sys.modules["iFinDPy"] 上，测试按需取用：sys.modules["iFinDPy"].THS_HistoryQuotes
    return mod, fake_THS_iFinDLogin, fake_THS_HQ


# ============================================================================
# 辅助：构造 iFinD THS_HQ 返回的 result 对象
# ============================================================================


def _make_hq_result(symbol: str, n: int = 5):
    """构造 THS_HQ 返回的 result（含 .errorcode 和 .data）。"""
    dates = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame(
        {
            "time": dates.strftime("%Y-%m-%d"),
            "open": np.linspace(10, 11, n),
            "high": np.linspace(11, 12, n),
            "low": np.linspace(9, 10, n),
            "close": np.linspace(10.5, 11.5, n),
            "volume": np.arange(100, 100 + n) * 1000,
            "amount": np.arange(200, 200 + n) * 1000,
            "openInterest": [0] * n,
        }
    )
    result = mock.MagicMock()
    result.errorcode = 0
    result.data = df
    return result


# ============================================================================
# 测试用例
# ============================================================================


class TestIfindAdapterInit:
    """IfindAdapter 初始化测试"""

    def test_init_success_with_credentials(self, monkeypatch):
        mod, fake_login, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        assert adapter._inited is True
        fake_login.assert_called_once_with("test_user", "test_pass")

    def test_init_raises_when_credentials_missing(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        # 清除环境变量并重置模块缓存的 config 值，模拟未配置
        monkeypatch.delenv("IFIND_USERNAME", raising=False)
        monkeypatch.delenv("IFIND_PASSWORD", raising=False)
        # ifind_adapter 在模块顶层已 import IFIND_USERNAME/PASSWORD，
        # 需用 mock.patch 覆盖为 None 才能触发凭证缺失分支
        with mock.patch.object(mod, "IFIND_USERNAME", None), mock.patch.object(mod, "IFIND_PASSWORD", None):
            with pytest.raises(Exception) as exc_info:
                mod.IfindAdapter()
        assert "IFIND_USERNAME" in str(exc_info.value) or "账号" in str(exc_info.value)

    def test_init_raises_when_login_fails(self, monkeypatch):
        mod, fake_login, _ = _load_ifind_adapter(monkeypatch)
        fake_login.return_value = -1  # 登录失败
        with pytest.raises(Exception) as exc_info:
            mod.IfindAdapter()
        assert "登录失败" in str(exc_info.value) or "错误码" in str(exc_info.value)

    def test_init_raises_when_ifindpy_missing(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        # 模拟 iFinDPy 缺失：将 sys.modules["iFinDPy"] 置为 None 会触发
        # "import of iFinDPy halted; None in sys.modules"，从而抛 ImportError，
        # 相比 pop 更可靠（iFinDPy 真实安装时 pop 会触发重新导入而不报错）。
        with mock.patch.dict(sys.modules, {"iFinDPy": None}):
            with pytest.raises(Exception) as exc_info:
                mod.IfindAdapter()
        assert "iFinDPy" in str(exc_info.value) or "ifind" in str(exc_info.value).lower()


class TestIfindAdapterGetDaily:
    """get_daily 核心逻辑测试"""

    def test_get_daily_returns_standard_schema(self, monkeypatch):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        symbol = "600000.SH"
        fake_hq.return_value = _make_hq_result(symbol)

        df = adapter.get_daily([symbol], "2024-01-01", "2024-01-07", adjust="hfq")

        assert not df.empty
        required_cols = {
            "code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "pre_close",
            "change_pct",
            "is_st",
            "is_limit_up",
            "is_limit_down",
        }
        assert required_cols.issubset(set(df.columns))
        assert (df["code"] == symbol).all()
        assert len(df) == 5

    def test_get_daily_passes_correct_indicators(self, monkeypatch):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        symbol = "000001.SZ"
        fake_hq.return_value = _make_hq_result(symbol)

        adapter.get_daily([symbol], "2024-01-01", "2024-01-05", adjust="hfq")

        fake_hq.assert_called_once()
        args, _ = fake_hq.call_args
        # THS_HQ(symbol, indicators, params, start, end)
        indicators = args[1]
        assert "open" in indicators
        assert "high" in indicators
        assert "low" in indicators
        assert "close" in indicators
        assert "volume" in indicators
        # 验证日期格式化为 YYYY-MM-DD HH:MM:SS
        assert args[3] == "2024-01-01 00:00:00"
        assert args[4] == "2024-01-05 00:00:00"

    @pytest.mark.parametrize(
        "adjust,expected_cps",
        [
            ("hfq", "3"),  # 后复权 -> iFinD CPS=3
            ("qfq", "2"),  # 前复权 -> iFinD CPS=2
        ],
    )
    def test_get_daily_stock_adds_cps_param(self, monkeypatch, adjust, expected_cps):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        symbol = "600000.SH"  # 股票
        fake_hq.return_value = _make_hq_result(symbol)

        adapter.get_daily([symbol], "2024-01-01", "2024-01-05", adjust=adjust)

        _, kwargs = fake_hq.call_args
        # 第3个位置参数是 params
        args, _ = fake_hq.call_args
        params = args[2]
        assert f"CPS:{expected_cps}" in params

    def test_get_daily_none_adjust_cps1(self, monkeypatch):
        """Turing REVISE 第3轮 ⑨：none(不复权) 映射 iFinD CPS=1，须显式传 CPS:1。

        旧版 none->None 静默不加 CPS（依赖接口默认不复权，行为不显式）；
        修正后 none->"1"（官方 CPS=1=不复权），adapter 显式传 CPS:1，语义明确。
        """
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        symbol = "600000.SH"
        fake_hq.return_value = _make_hq_result(symbol)

        adapter.get_daily([symbol], "2024-01-01", "2024-01-05", adjust="none")

        args, _ = fake_hq.call_args
        params = args[2]
        assert "CPS:1" in params

    def test_get_daily_raises_on_error_code(self, monkeypatch):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        result = mock.MagicMock()
        result.errorcode = -1
        result.data = None
        fake_hq.return_value = result

        with pytest.raises(Exception) as exc_info:
            adapter.get_daily(["600000.SH"], "2024-01-01", "2024-01-05")
        assert "错误码" in str(exc_info.value) or "THS_HQ" in str(exc_info.value)

    def test_get_daily_returns_empty_when_no_data(self, monkeypatch):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        result = mock.MagicMock()
        result.errorcode = 0
        result.data = pd.DataFrame()
        fake_hq.return_value = result

        df = adapter.get_daily(["600000.SH"], "2024-01-01", "2024-01-05")
        assert df.empty

    def test_get_daily_rejects_invalid_symbol(self, monkeypatch):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        with pytest.raises(Exception) as exc_info:
            adapter.get_daily(["600000"], "2024-01-01", "2024-01-05")
        assert "不支持" in str(exc_info.value) or "格式" in str(exc_info.value)

    def test_get_daily_symbol_uppercased(self, monkeypatch):
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        symbol = "600000.sh"  # 小写后缀
        fake_hq.return_value = _make_hq_result("600000.SH")

        adapter.get_daily([symbol], "2024-01-01", "2024-01-05", adjust="hfq")

        args, _ = fake_hq.call_args
        # 代码会被 upper()
        assert args[0] == "600000.SH"


class TestIfindAdapterOtherMethods:
    """get_adj_factor / get_financial 测试"""

    def test_get_adj_factor_returns_empty(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        df = adapter.get_adj_factor(["600000.SH"], "2024-01-01", "2024-01-05")
        assert df.empty
        assert "code" in df.columns and "date" in df.columns

    def test_get_financial_returns_standard_schema(self, monkeypatch):
        """P0-1 PIT 契约：get_financial 返回标准 schema（含 disclosure_date）。

        iFinD 财务接口字段映射已实现，但 mock 环境下 THS_BD 返回 errorcode，
        各指标留空；code/report_date/disclosure_date 仍填充。
        """
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        df = adapter.get_financial(["600000.SH"], "20240930", [])
        # 1 行（每个 symbol 一行，code/report_date/disclosure_date 已填充）
        assert not df.empty
        assert len(df) == 1
        # 标准 schema 列（含 P0-1 新增 disclosure_date）
        assert "code" in df.columns
        assert "pe_ttm" in df.columns
        assert "disclosure_date" in df.columns
        # P0-1 PIT 契约：ifind 无原生披露日，回填为 report_date（保守降级）
        assert df["code"].iloc[0] == "600000.SH"
        assert df["report_date"].iloc[0] == "20240930"
        assert df["disclosure_date"].iloc[0] == "20240930"
        # mock 环境下 THS_BD 返回 errorcode，财务指标应留空
        assert pd.isna(df["pe_ttm"].iloc[0])

    def test_get_financial_empty_symbols(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        df = adapter.get_financial([], "20240930", [])
        assert df.empty


class TestIfindAdapterKlineRealtime:
    """get_kline / get_realtime_quote 测试（免费版 dict 式接口）。

    策略：mock iFinDPy.THS_HistoryQuotes / THS_RealtimeQuotes 返回 dict，
    并 mock 适配器自身的 _parse_history_dict 返回标准 DataFrame，验证：
    - 方法调用了正确的底层接口与参数
    - 输出经 _finalize 统一为标准 schema
    - 高频周期（min/5min）抛 DataSourceError（免费版高频额度受限）
    """

    def _fake_hist_dict(self, symbol):
        """构造 THS_HistoryQuotes 返回的 dict（含 errorcode/tables/time/table）。"""
        dates = pd.bdate_range("2024-01-01", periods=5).strftime("%Y-%m-%d").tolist()
        return {
            "errorcode": 0,
            "errmsg": "Success!",
            "tables": [{
                "thscode": symbol,
                "time": dates,
                "table": {
                    "open": [10.0] * 5, "high": [11.0] * 5, "low": [9.0] * 5,
                    "close": [10.5] * 5, "volume": [1000] * 5, "amount": [2000] * 5,
                },
            }],
        }

    def test_get_kline_calls_history_quotes(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_hist = sys.modules["iFinDPy"].THS_HistoryQuotes
        fake_hist.return_value = self._fake_hist_dict("600000.SH")
        # 隔离外部 dict 解析（模块级函数）：_parse_history_dict 返回已解析的标准 DataFrame
        # （含 date 列，模拟真实 _parse_history_dict 输出，供 _finalize 使用）
        _parsed_df = _make_hq_result("600000.SH").data.copy()
        _parsed_df["date"] = pd.to_datetime(_parsed_df["time"])
        _parsed_df["code"] = "600000.SH"
        with mock.patch.object(mod, "_parse_history_dict", return_value=_parsed_df):
            df = adapter.get_kline(["600000.SH"], "day", "2024-01-01", "2024-01-07", "hfq")
        fake_hist.assert_called_once()
        args, _ = fake_hist.call_args
        # THS_HistoryQuotes(symbol, indicators, params, begin, end)
        assert args[0] == "600000.SH"
        assert "open" in args[1] and "close" in args[1]
        assert "CPS:3" in args[2]  # hfq -> 后复权 iFinD CPS=3（Turing REVISE 第3轮 ⑨ 修正）
        assert "Period:D" in args[2]
        assert not df.empty
        assert (df["code"] == "600000.SH").all()

    def test_get_kline_hfq_uses_cps3_not_cps1(self, monkeypatch):
        """Turing REVISE 第3轮 ⑨：hfq 必须映射 CPS=3（后复权），不得静默返回不复权价(CPS=1)。

        捕获 get_kline 传给 THS_HistoryQuotes 的 params，断言 CPS:3 且不含 CPS:1。
        """
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_hist = sys.modules["iFinDPy"].THS_HistoryQuotes
        fake_hist.return_value = self._fake_hist_dict("600000.SH")
        _parsed_df = _make_hq_result("600000.SH").data.copy()
        _parsed_df["date"] = pd.to_datetime(_parsed_df["time"])
        _parsed_df["code"] = "600000.SH"
        with mock.patch.object(mod, "_parse_history_dict", return_value=_parsed_df):
            adapter.get_kline(["600000.SH"], "day", "2024-01-01", "2024-01-07", "hfq")
        args, _ = fake_hist.call_args
        params = args[2]
        assert "CPS:3" in params, f"hfq 应映射 CPS:3，实际 params={params}"
        assert "CPS:1" not in params, f"hfq 不得映射不复权 CPS:1，实际 params={params}"

    def test_get_daily_cps1_vs_cps3_returns_distinct_prices(self, monkeypatch):
        """Turing REVISE 第3轮 ⑨：CPS:1（不复权）与 CPS:3（后复权）应返回不同价格。

        用 mock 让 adapter 直接返回不同 close（模拟 iFinD 对不同 CPS 的真实返回），
        断言 adjust='none'(CPS:1) 取到的 close 与 adjust='hfq'(CPS:3) 取到的 close 不同——
        即映射差异真实生效，hfq 不再静默返回不复权价。
        """
        mod, _, fake_hq = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()

        # CPS:1（不复权）mock 返回 1685.06；CPS:3（后复权）mock 返回 9397.69（600519 实证）
        def _side_effect(symbol, indicators, params, begin, end):
            result = mock.MagicMock()
            result.errorcode = 0
            close_val = 9397.69 if "CPS:3" in params else 1685.06
            df = _make_hq_result(symbol).data.copy()
            df["close"] = close_val
            result.data = df
            return result

        fake_hq.side_effect = _side_effect

        df_none = adapter.get_daily(["600519.SH"], "2024-03-01", "2024-03-01", adjust="none")
        df_hfq = adapter.get_daily(["600519.SH"], "2024-03-01", "2024-03-01", adjust="hfq")

        # none -> CPS:1 不复权价；hfq -> CPS:3 后复权价；二者必须不同
        assert abs(df_none["close"].iloc[0] - 1685.06) < 1e-6
        assert abs(df_hfq["close"].iloc[0] - 9397.69) < 1e-6
        assert df_none["close"].iloc[0] != df_hfq["close"].iloc[0]

    def test_get_kline_minute_raises_transient_quota(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        with pytest.raises(Exception) as exc_info:
            adapter.get_kline(["600000.SH"], "5min", "2024-01-01", "2024-01-07")
        assert "高频" in str(exc_info.value) or "受限" in str(exc_info.value)

    def test_get_kline_registry_periods_map_correctly(self, monkeypatch):
        """Turing REVISE #1：注册表标准命名周期须正确映射，不得落 D 静默冒充。

        验证 period 为注册表 KLINE_PERIODS 标准命名（day/week/month/season/year）
        时，get_kline 向 THS_HistoryQuotes 传入正确的 Period 码，而非静默降级 day('D')。
        用 mock 捕获 params 断言 Period:<code>。
        （分钟级 m1/m5/... 属高频额度受限，单独用例验证抛错，见
        test_get_kline_minute_raises_transient_quota / test_get_kline_minute_registry_naming_raises）
        """
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_hist = sys.modules["iFinDPy"].THS_HistoryQuotes

        cases = [
            ("day", "D"), ("week", "W"), ("month", "M"),
            ("season", "Q"), ("year", "Y"),
            ("daily", "D"), ("weekly", "W"), ("monthly", "M"),
            ("seasonal", "Q"), ("yearly", "Y"),
        ]
        for period, expected_code in cases:
            fake_hist.return_value = self._fake_hist_dict("600000.SH")
            _parsed_df = _make_hq_result("600000.SH").data.copy()
            _parsed_df["date"] = pd.to_datetime(_parsed_df["time"])
            _parsed_df["code"] = "600000.SH"
            with mock.patch.object(mod, "_parse_history_dict", return_value=_parsed_df):
                adapter.get_kline(["600000.SH"], period, "2024-01-01", "2024-01-07", "hfq")
            args, _ = fake_hist.call_args
            params = args[2]
            assert f"Period:{expected_code}" in params, (
                f"period={period} 期望 Period:{expected_code}，实际 params={params}"
            )

    def test_get_kline_minute_registry_naming_raises(self, monkeypatch):
        """Turing REVISE #1：注册表分钟级命名(m1/m5/m15/m30/m60/m120)须被识别，
        但因属高频额度受限而显式抛 DataSourceError（不得静默落 D 冒充）。

        与旧版 bug 对比：旧 _PERIOD_MAP 缺 m5 等键，period='m5' 落 'D' 静默返回日线冒充
        5 分钟线——此用例确保不再发生。
        """
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        for period in ("m1", "m5", "m15", "m30", "m60", "m120"):
            with pytest.raises(Exception) as exc_info:
                adapter.get_kline(["600000.SH"], period, "2024-01-01", "2024-01-07")
            assert "高频" in str(exc_info.value) or "受限" in str(exc_info.value)

    def test_get_kline_unknown_period_raises_no_silent_daily(self, monkeypatch):
        """Turing REVISE #1：未识别周期必须显式抛 DataSourceError，不得静默降级日线。

        验证 period='banana'（非注册表命名）时 get_kline 抛错，
        而非静默落 'D' 返回日线冒充错误粒度。同时验证未触达 THS_HistoryQuotes
        （即未发起任何底层查询，证明是抛错而非降级取数）。
        """
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_hist = sys.modules["iFinDPy"].THS_HistoryQuotes
        with pytest.raises(Exception) as exc_info:
            adapter.get_kline(["600000.SH"], "banana", "2024-01-01", "2024-01-07")
        assert "不支持" in str(exc_info.value) or "周期" in str(exc_info.value)
        # 关键：未识别周期在周期映射阶段即抛错，不得触达底层 THS_HistoryQuotes 查询
        fake_hist.assert_not_called()
        # 错误消息不得暗示"已降级为日线返回"——只应提示改用日级
        msg = str(exc_info.value)
        assert "静默" not in msg
        assert "降级为日线" not in msg

    def test_supported_data_types_includes_registry_keys(self, monkeypatch):
        """Turing REVISE #2：SUPPORTED_DATA_TYPES 须含注册表 key，使 engine 路由可达。

        验证 market_kline/market_realtime 在声明集内（保留旧键兼容）。
        """
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        supported = mod.IfindAdapter.SUPPORTED_DATA_TYPES
        assert "market_kline" in supported
        assert "market_realtime" in supported
        # 保留旧键兼容
        assert "kline" in supported
        assert "realtime" in supported
        assert "daily" in supported
        assert "financial" in supported

    def test_get_realtime_calls_realtime_quotes(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_rt = sys.modules["iFinDPy"].THS_RealtimeQuotes
        fake_rt.return_value = {
            "errorcode": 0,
            "tables": [{
                "thscode": "600000.SH",
                "table": {"open": [10.0], "high": [11.0], "low": [9.0],
                          "volume": [1000], "amount": [2000]},
            }],
        }
        _rt_parsed = _make_hq_result("600000.SH").data.copy()
        _rt_parsed["date"] = pd.to_datetime(_rt_parsed["time"])
        _rt_parsed["code"] = "600000.SH"
        # 产品层 realtime 走 _parse_realtime_dict（IR 层复用 _parse_history_dict），
        # mock 实际被调用的解析函数
        _rt_parser = getattr(mod, "_parse_realtime_dict", None) or mod._parse_history_dict
        with mock.patch.object(mod, _rt_parser.__name__, return_value=_rt_parsed):
            df = adapter.get_realtime_quote("600000.SH")
        fake_rt.assert_called_once()
        args, _ = fake_rt.call_args
        assert args[0] == "600000.SH"
        assert "last" in args[1]
        assert not df.empty


class TestIfindAdapterAnnouncement:
    """get_announcement 测试（免费版额度敏感，空返回降级）。"""

    def test_get_announcement_empty_degrade(self, monkeypatch):
        """免费版 THS_ReportQuery errorcode=0 但 data 空 -> 降级空 DF，不报错。"""
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_rpt = sys.modules["iFinDPy"].THS_ReportQuery
        rpt_result = mock.MagicMock()
        rpt_result.errorcode = 0
        rpt_result.data = None  # 免费版空返回
        fake_rpt.return_value = rpt_result
        df = adapter.get_announcement(["600000.SH"], "2024-01-01", "2024-01-31")
        # 免费版空返回降级：code 填充、公告内容字段为空，不抛错、不阻塞主流程
        assert len(df) == 1
        assert df["code"].iloc[0] == "600000.SH"
        assert df["title"].iloc[0] is None
        assert "code" in df.columns

    def test_get_announcement_passes_date_param(self, monkeypatch):
        mod, _, _ = _load_ifind_adapter(monkeypatch)
        adapter = mod.IfindAdapter()
        fake_rpt = sys.modules["iFinDPy"].THS_ReportQuery
        rpt_result = mock.MagicMock()
        rpt_result.errorcode = 0
        rpt_result.data = None
        fake_rpt.return_value = rpt_result
        adapter.get_announcement(["600000.SH"], "2024-01-01", "2024-01-31")
        args, _ = fake_rpt.call_args
        # date:YYYY-MM-DD,YYYY-MM-DD
        assert args[2].startswith("date:2024-01-01,2024-01-31")
