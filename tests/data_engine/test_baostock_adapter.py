"""baostock 适配器单元测试（任务 #32：三项 reference 方法）。

覆盖：
- get_dividend（query_dividend_data）→ 标准分红契约 code/date/dividend_type/cash_dividend_ratio
- get_trade_calendar（query_trade_dates）→ 标准日历契约 date/is_trading/month_end/quarter_end/year_end
- get_forecast（query_forecast_report）→ 标准预告契约 code/report_date/profit_mean/min/max
- 失败/空返回 → 抛 DataNotFoundError（触发引擎降级，不静默空返回）
- SUPPORTED_DATA_TYPES 含 8 项（含新增 3 项）

设计：mock baostock 模块后动态加载适配器，构造假 ResultSet 模拟官方 API 返回。
锚点 600519（贵州茅台）用于 schema 校验。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
from unittest import mock

import pytest
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")
ADAPTER_PATH = os.path.join(SCRIPTS_DIR, "adapters", "baostock_adapter.py")
ERRORS_PATH = os.path.join(SCRIPTS_DIR, "errors.py")


class _FakeResultSet:
    """模拟 baostock 返回的 ResultSet：error_code / next / get_row_data / fields。"""

    def __init__(self, fields, rows, error_code="0"):
        self._fields = fields
        self._rows = list(rows)
        self._idx = 0
        self.error_code = error_code

    def next(self):
        if self._idx < len(self._rows):
            self._idx += 1
            return True
        return False

    def get_row_data(self):
        return self._rows[self._idx - 1]

    @property
    def fields(self):
        return self._fields


def _make_bs_mock(dividend_rs=None, calendar_rs=None, forecast_rs=None):
    """构造 baostock MagicMock，login 与三个 query 方法按需在 return_value 上替换。"""
    bs = mock.MagicMock()
    bs.login.return_value = "0"
    if dividend_rs is not None:
        bs.query_dividend_data.return_value = dividend_rs
    if calendar_rs is not None:
        bs.query_trade_dates.return_value = calendar_rs
    if forecast_rs is not None:
        bs.query_forecast_report.return_value = forecast_rs
    return bs


def _load_modules(bs_mock):
    """mock baostock 模块后动态加载 adapter + errors。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)
    sys.modules["baostock"] = bs_mock

    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    err_spec = ilu.spec_from_file_location("scripts.errors", ERRORS_PATH)
    err_mod = ilu.module_from_spec(err_spec)
    sys.modules["scripts.errors"] = err_mod
    err_spec.loader.exec_module(err_mod)

    ad_spec = ilu.spec_from_file_location("scripts.adapters.baostock_adapter", ADAPTER_PATH)
    ad_mod = ilu.module_from_spec(ad_spec)
    sys.modules["scripts.adapters.baostock_adapter"] = ad_mod
    ad_spec.loader.exec_module(ad_mod)
    return ad_mod, err_mod


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestBaostockSupportedTypes:
    def test_declares_8_types(self):
        bs = _make_bs_mock()
        mod, _ = _load_modules(bs)
        expected = {
            "daily", "kline", "financial", "basic_stock_list", "basic_adjust_factor",
            "ref_dividend", "basic_trade_calendar", "ref_forecast",
        }
        assert mod.BaostockAdapter.SUPPORTED_DATA_TYPES == expected


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestBaostockGetDividend:
    def test_normalizes_to_standard_contract(self):
        bs = _make_bs_mock(
            dividend_rs=_FakeResultSet(
                ["code", "dividendPeriod", "year", "dividendType", "cashDividendRatio"],
                [["sh.600519", "2023年中报", "2023", "10派X元", "1.5000"]],
            )
        )
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        df = adapter.get_dividend(["600519.SH"])
        assert not df.empty
        assert set(["code", "date", "dividend_type", "cash_dividend_ratio"]).issubset(df.columns)
        assert (df["code"] == "600519.SH").all()
        assert df["date"].iloc[0] == "2023"  # 报告期年份前缀
        assert df["dividend_type"].iloc[0] == "10派X元"
        assert abs(float(df["cash_dividend_ratio"].iloc[0]) - 1.5) < 1e-6

    def test_empty_raises_data_not_found(self):
        bs = _make_bs_mock(dividend_rs=_FakeResultSet(["code"], []))
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        with pytest.raises(errs.DataNotFoundError):
            adapter.get_dividend(["600519.SH"])

    def test_no_symbols_raises(self):
        bs = _make_bs_mock()
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        with pytest.raises(errs.DataNotFoundError):
            adapter.get_dividend([])


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestBaostockGetTradeCalendar:
    def test_normalizes_to_standard_contract(self):
        bs = _make_bs_mock(
            calendar_rs=_FakeResultSet(
                ["date", "is_trading_day"],
                [["2024-01-02", "1"], ["2024-01-01", "0"], ["2024-01-31", "1"]],
            )
        )
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        df = adapter.get_trade_calendar("2024-01-01", "2024-01-31")
        assert not df.empty
        assert set(["date", "is_trading", "month_end", "quarter_end", "year_end"]).issubset(df.columns)
        # 2024-01-02 为交易日，2024-01-01 非交易
        row_02 = df[df["date"] == "2024-01-02"].iloc[0]
        row_01 = df[df["date"] == "2024-01-01"].iloc[0]
        assert bool(row_02["is_trading"]) is True
        assert bool(row_01["is_trading"]) is False
        # 2024-01-31 是月末且交易日 → month_end 应为 True
        row_31 = df[df["date"] == "2024-01-31"].iloc[0]
        assert bool(row_31["month_end"]) is True

    def test_empty_raises_data_not_found(self):
        bs = _make_bs_mock(calendar_rs=_FakeResultSet(["date"], []))
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        with pytest.raises(errs.DataNotFoundError):
            adapter.get_trade_calendar("2024-01-01", "2024-01-31")

    def test_error_code_raises(self):
        bs = _make_bs_mock(calendar_rs=_FakeResultSet(["date"], [], error_code="1"))
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        with pytest.raises(errs.DataNotFoundError):
            adapter.get_trade_calendar("2024-01-01", "2024-01-31")


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestBaostockGetForecast:
    def test_normalizes_to_standard_contract(self):
        bs = _make_bs_mock(
            forecast_rs=_FakeResultSet(
                ["code", "statDate", "profitMean", "profitMin", "profitMax"],
                [["sh.600519", "2024-06-30", "3500000000", "3000000000", "4000000000"]],
            )
        )
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        df = adapter.get_forecast(["600519.SH"])
        assert not df.empty
        assert set(["code", "report_date", "profit_mean", "profit_min", "profit_max"]).issubset(df.columns)
        assert (df["code"] == "600519.SH").all()
        assert df["report_date"].iloc[0] == "2024-06-30"
        assert abs(float(df["profit_mean"].iloc[0]) - 3.5e9) < 1.0

    def test_empty_raises_data_not_found(self):
        bs = _make_bs_mock(forecast_rs=_FakeResultSet(["code"], []))
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        with pytest.raises(errs.DataNotFoundError):
            adapter.get_forecast(["600519.SH"])

    def test_no_symbols_raises(self):
        bs = _make_bs_mock()
        mod, errs = _load_modules(bs)
        adapter = mod.BaostockAdapter()
        with pytest.raises(errs.DataNotFoundError):
            adapter.get_forecast([])
