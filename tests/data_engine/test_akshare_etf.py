"""akshare 适配器 ETF 支持测试。

覆盖纯逻辑（不联网）：
- _is_etf_code 判断：沪/深 ETF 与 A 股股票的区分
- get_daily 中 ETF 走 fund_etf_hist_em 分支、股票走 stock_zh_a_hist 分支
"""
import os
import sys
import importlib.util as ilu

import pytest
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(ROOT, "skills", "data-engine", "scripts")


def _load_akshare_adapter():
    """加载 akshare 适配器模块（复用 test_data_sources_live.py 模式）。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts", init_py, submodule_search_locations=[SCRIPTS_DIR],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    adapter_path = os.path.join(SCRIPTS_DIR, "adapters", "akshare_adapter.py")
    full_mod_name = "scripts.adapters.akshare_adapter"
    aspec = ilu.spec_from_file_location(full_mod_name, adapter_path)
    mod = ilu.module_from_spec(aspec)
    sys.modules[full_mod_name] = mod
    aspec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def akshare_mod():
    return _load_akshare_adapter()


class TestIsEtfCode:
    def test_etf_sh(self, akshare_mod):
        adapter = akshare_mod.AkshareAdapter()
        assert adapter._is_etf_code("510300") is True   # 沪深300ETF
        assert adapter._is_etf_code("512880") is True   # 证券ETF
        assert adapter._is_etf_code("588000") is True   # 科创50ETF

    def test_etf_sz(self, akshare_mod):
        adapter = akshare_mod.AkshareAdapter()
        assert adapter._is_etf_code("159915") is True   # 创业板ETF
        assert adapter._is_etf_code("159901") is True   # 深100ETF

    def test_stock_not_etf(self, akshare_mod):
        adapter = akshare_mod.AkshareAdapter()
        assert adapter._is_etf_code("600000") is False  # 浦发银行
        assert adapter._is_etf_code("000001") is False  # 平安银行
        assert adapter._is_etf_code("300750") is False  # 宁德时代
        assert adapter._is_etf_code("002594") is False  # 比亚迪

    def test_short_code(self, akshare_mod):
        adapter = akshare_mod.AkshareAdapter()
        assert adapter._is_etf_code("510") is False     # 位数不足
        assert adapter._is_etf_code("") is False


class TestGetDailyEtfBranch:
    def test_etf_uses_fund_etf_hist_em(self, akshare_mod, monkeypatch):
        """ETF 应调用 fund_etf_hist_em 而非 stock_zh_a_hist。"""
        _mod = akshare_mod
        adapter = _mod.AkshareAdapter()

        called = {"fund": False, "stock": False}

        def fake_fund_etf_hist_em(**kwargs):
            called["fund"] = True
            return pd.DataFrame({
                "日期": ["2025-01-02"], "开盘": [4.0], "收盘": [3.9],
                "最高": [4.0], "最低": [3.8], "成交量": [21729803],
                "成交额": [8.5e9], "振幅": [3.5], "涨跌幅": [-2.0],
                "涨跌额": [-0.08], "换手率": [8.0],
            })

        def fake_stock_zh_a_hist(**kwargs):
            called["stock"] = True
            return pd.DataFrame()

        monkeypatch.setattr(_mod.ak, "fund_etf_hist_em", fake_fund_etf_hist_em)
        monkeypatch.setattr(_mod.ak, "stock_zh_a_hist", fake_stock_zh_a_hist)

        df = adapter.get_daily(["510300.SH"], "2025-01-01", "2025-01-10", adjust="hfq")
        assert called["fund"] is True
        assert called["stock"] is False
        assert not df.empty
        assert df["volume"].iloc[0] == 21729803
        assert df["code"].iloc[0] == "510300.SH"

    def test_stock_uses_stock_zh_a_hist(self, akshare_mod, monkeypatch):
        """A 股股票应调用 stock_zh_a_hist 而非 fund_etf_hist_em。"""
        _mod = akshare_mod
        adapter = _mod.AkshareAdapter()

        called = {"fund": False, "stock": False}

        def fake_stock_zh_a_hist(**kwargs):
            called["stock"] = True
            return pd.DataFrame({
                "日期": ["2025-01-02"], "开盘": [10.0], "收盘": [10.1],
                "最高": [10.2], "最低": [9.9], "成交量": [1000000],
                "成交额": [1e7], "振幅": [3.0], "涨跌幅": [1.0],
                "涨跌额": [0.1], "换手率": [0.5],
            })

        def fake_fund_etf_hist_em(**kwargs):
            called["fund"] = True
            return pd.DataFrame()

        monkeypatch.setattr(_mod.ak, "stock_zh_a_hist", fake_stock_zh_a_hist)
        monkeypatch.setattr(_mod.ak, "fund_etf_hist_em", fake_fund_etf_hist_em)

        df = adapter.get_daily(["600000.SH"], "2025-01-01", "2025-01-10", adjust="hfq")
        assert called["stock"] is True
        assert called["fund"] is False
        assert not df.empty
        assert df["code"].iloc[0] == "600000.SH"
