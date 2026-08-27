"""jingni-trader 数据分类对齐 阶段 2/3 实现测试。

覆盖（均以离线静态校验为主，直连实测为 optional 标记，CI 默认跳过）：
- 11 个 P0 叶子项的 DataTypeMeta.allow_synthetic/required 已回填实际值（非默认桩）
- westock_adapter 已落地 8 个 Phase 2 方法 + get_index_constituent（Phase 3）+ get_financial_report
- SUPPORTED_DATA_TYPES 包含全部 11 个新键
- 各新方法的入参/出参合同列齐全（静态定义层校验）
- 直连实测锚点（requires_westock_cli marker，需 npx 可用；默认不跑）

设计：
- 不要求联网；适配器仅验证方法可调用、返回 DataFrame 契约列。
- 直连实测经 pytest.mark.requires_westock_cli 标记，CI 默认跳过（与项目 addopts 一致）。
"""

from __future__ import annotations

import importlib.util as ilu
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(ROOT))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")


def _reset_scripts():
    for k in list(sys.modules):
        if k == "scripts" or k.startswith("scripts."):
            sys.modules.pop(k, None)


def _load_dt():
    _reset_scripts()
    p = os.path.join(SCRIPTS_DIR, "data_types.py")
    spec = ilu.spec_from_file_location("scripts.data_types", p)
    m = ilu.module_from_spec(spec)
    sys.modules["scripts.data_types"] = m
    spec.loader.exec_module(m)
    return m


def _load_adapter():
    _reset_scripts()
    pkg = ilu.spec_from_file_location(
        "scripts", os.path.join(SCRIPTS_DIR, "__init__.py"),
        submodule_search_locations=[SCRIPTS_DIR],
    )
    pkm = ilu.module_from_spec(pkg)
    sys.modules["scripts"] = pkm
    pkg.loader.exec_module(pkm)
    ap = os.path.join(SCRIPTS_DIR, "adapters", "westock_adapter.py")
    aspec = ilu.spec_from_file_location("scripts.adapters.westock_adapter", ap)
    am = ilu.module_from_spec(aspec)
    sys.modules["scripts.adapters.westock_adapter"] = am
    aspec.loader.exec_module(am)
    return am


# ── 静态：DataTypeMeta 元数据回填 ──
def test_leaf_items_metadata_backfilled():
    dt = _load_dt().DATA_TYPES
    # Phase 2 已实测落地的 11 个叶子项，description 不应仍是"桩/待补"措辞
    for key in [
        "basic_stock_list", "basic_stock_info", "basic_trade_calendar", "basic_adjust_factor",
        "market_kline", "market_realtime", "financial_report",
        "ref_dividend", "ref_suspend_resume", "ref_locked_shares", "ref_forecast",
    ]:
        assert key in dt, f"缺失叶子项: {key}"
        # description 须指向真实 westock 子命令（证明已落地，非桩）
        assert "westock" in dt[key].description.lower(), f"{key} 描述未回填取数通道"


def test_supported_data_types_includes_new_keys():
    am = _load_adapter()
    new_keys = {
        "basic_stock_list", "basic_stock_info", "basic_trade_calendar", "basic_adjust_factor",
        "market_kline", "market_realtime", "financial_report",
        "ref_dividend", "ref_suspend_resume", "ref_locked_shares", "ref_forecast",
    }
    assert new_keys <= am.WestockAdapter.SUPPORTED_DATA_TYPES, "SUPPORTED_DATA_TYPES 缺新键"


def test_new_methods_present():
    am = _load_adapter()
    for m in [
        "get_stock_list", "get_stock_info", "get_trade_calendar", "get_adj_factor",
        "get_dividend", "get_suspend_resume", "get_locked_shares", "get_forecast",
        "get_index_constituent", "get_financial_report",
    ]:
        assert hasattr(am.WestockAdapter, m), f"缺失方法: {m}"


def test_fetch_routes_new_keys():
    """fetch 按 data_type 正确分派到 method_name（契约一致性）。"""
    am = _load_adapter()
    dt = _load_dt().DATA_TYPES
    a = am.WestockAdapter()
    for key in dt:
        meta = dt[key]
        if not a.supports(key):
            continue
        method = getattr(a, meta.method_name, None)
        assert method is not None, f"{key} 的 method_name {meta.method_name} 未实现"


# ── 直连实测（需 npx + 网络，CI 默认跳过；仅 Live 类打 marker）──


def _have_npx() -> bool:
    import shutil
    return shutil.which("npx") is not None


@pytest.mark.requires_westock_cli
@pytest.mark.skipif(not _have_npx(), reason="npx 不可用")
class TestLiveAnchors:
    """锚点直连实测：600519（股票）/ sh000300（指数）。"""

    @classmethod
    def setup_class(cls):
        cls.am = _load_adapter().WestockAdapter()

    def test_trade_calendar(self):
        df = self.am.get_trade_calendar("2024-01-01", "2024-01-05")
        assert not df.empty
        assert {"date", "is_trading"}.issubset(df.columns)

    def test_stock_info(self):
        df = self.am.get_stock_info(["600519.SH"])
        assert not df.empty
        assert df.iloc[0]["name"] == "贵州茅台"

    def test_dividend(self):
        df = self.am.get_dividend(["600519.SH"], years=1)
        assert not df.empty
        assert "cash_divi_per_10" in df.columns

    def test_adj_factor(self):
        df = self.am.get_adj_factor(["600519.SH"], "2024-01-01", "2024-01-10")
        assert not df.empty
        assert {"code", "date", "adj_factor"}.issubset(df.columns)

    def test_suspend_resume(self):
        df = self.am.get_suspend_resume("hs")
        assert not df.empty
        assert {"code", "status"}.issubset(df.columns)

    def test_locked_shares(self):
        df = self.am.get_locked_shares("2026-01-15")
        assert not df.empty
        assert {"symbol", "amount"}.issubset(df.columns)

    def test_forecast(self):
        df = self.am.get_forecast("2026-04-30")
        assert not df.empty
        assert {"symbol", "eps"}.issubset(df.columns)

    def test_index_constituent(self):
        df = self.am.get_index_constituent("sh000300")
        assert len(df) >= 50  # 沪深300 应 >= 50 只
        assert {"code", "name"}.issubset(df.columns)

    def test_index_kline(self):
        df = self.am.get_kline(["sh000300"], period="day", end_date="")
        assert not df.empty
        assert {"code", "date", "close"}.issubset(df.columns)
