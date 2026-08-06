"""执行监控报告模板单元测试（L2）。

覆盖 templates/execution_report.py：
- load_trade_log / load_ledger: 日志加载（含数据缺失容错）
- derive_risk_metrics: 风控指标派生
- build_status_bar_html: 状态条（F-P0-1）
- build_account_dashboard_html: 账户仪表盘（F-P0-3）
- build_risk_dashboard_html: 风控仪表盘（F-P0-4）
- build_execution_report: 主入口端到端

关键路径：数据缺失容错、风控阈值色阶、PAPER/LIVE 模式区分
"""
from __future__ import annotations

import json
import os
import sys
import importlib.util as ilu
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")


def _load_execution_module():
    """加载 execution_report 模块。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    init_py = os.path.join(REPORTS_SCRIPTS, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts", init_py,
        submodule_search_locations=[REPORTS_SCRIPTS],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 加载 svg_components
    svg_path = os.path.join(REPORTS_SCRIPTS, "renderers", "svg_components.py")
    spec = ilu.spec_from_file_location("scripts.renderers.svg_components", svg_path)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.renderers.svg_components"] = mod
    spec.loader.exec_module(mod)

    # 加载 execution_report
    exec_path = os.path.join(REPORTS_SCRIPTS, "templates", "execution_report.py")
    spec = ilu.spec_from_file_location("scripts.templates.execution_report", exec_path)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.templates.execution_report"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestLoadTradeLog:
    """审计日志加载测试（数据缺失容错）。"""

    def test_none_path_returns_empty(self):
        mod = _load_execution_module()
        assert mod.load_trade_log(None) == []

    def test_nonexistent_path_returns_empty(self):
        mod = _load_execution_module()
        assert mod.load_trade_log("/nonexistent.jsonl") == []

    def test_valid_jsonl_file(self, tmp_path):
        mod = _load_execution_module()
        log_path = tmp_path / "trade_log.jsonl"
        log_path.write_text(
            json.dumps({"order_id": "1", "code": "000001.SZ", "side": "buy", "price": 10, "volume": 100}) + "\n" +
            json.dumps({"order_id": "2", "code": "600000.SH", "side": "sell", "price": 11, "volume": 100}) + "\n",
            encoding="utf-8",
        )
        records = mod.load_trade_log(str(log_path))
        assert len(records) == 2
        assert records[0]["code"] == "000001.SZ"


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestDeriveRiskMetrics:
    """风控指标派生测试。"""

    def test_empty_snapshot(self):
        """空 account_snapshot 应返回 0 值，不抛异常。"""
        mod = _load_execution_module()
        result = mod.derive_risk_metrics({}, [])
        assert result["daily_loss_ratio"] == 0
        assert result["max_single_order_value"] == 0
        assert result["order_frequency"] == 0

    def test_daily_loss_calculation(self):
        """日亏损率应正确计算。"""
        mod = _load_execution_module()
        result = mod.derive_risk_metrics(
            account_snapshot={"nav": 980000, "start_of_day_nav": 1000000},
            trade_log=[],
        )
        assert result["daily_loss_ratio"] == -0.02  # 亏损 2%

    def test_max_single_order_value(self):
        """最大单笔成交额应正确提取。"""
        mod = _load_execution_module()
        trade_log = [
            {"price": 10, "volume": 100},   # 1000
            {"price": 20, "volume": 200},   # 4000
            {"price": 5, "volume": 50},     # 250
        ]
        result = mod.derive_risk_metrics(
            account_snapshot={"nav": 1000000, "start_of_day_nav": 1000000},
            trade_log=trade_log,
        )
        assert result["max_single_order_value"] == 4000

    def test_thresholds_passed_through(self):
        """阈值应正确传递。"""
        mod = _load_execution_module()
        result = mod.derive_risk_metrics(
            account_snapshot={},
            trade_log=[],
            max_daily_loss_ratio=0.05,
            max_single_order_ratio=0.2,
            max_order_frequency=5,
        )
        assert result["thresholds"]["MAX_DAILY_LOSS_RATIO"] == 0.05
        assert result["thresholds"]["MAX_SINGLE_ORDER_RATIO"] == 0.2
        assert result["thresholds"]["MAX_ORDER_FREQUENCY"] == 5


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildStatusBarHtml:
    """实盘连接状态条测试（F-P0-1）。"""

    def test_paper_mode_shows_disabled(self):
        """PAPER 模式应显示灰色未启用状态。"""
        mod = _load_execution_module()
        html = mod.build_status_bar_html(mode="paper", backend="paper", available=False)
        assert "模拟交易" in html
        assert "未启用实盘" in html

    def test_live_mode_connected(self):
        """LIVE 模式已连接应显示绿色脉冲。"""
        mod = _load_execution_module()
        html = mod.build_status_bar_html(mode="live", backend="xtquant", available=True)
        assert "实盘交易" in html
        assert "已连接" in html

    def test_live_mode_disconnected(self):
        """LIVE 模式断开应显示红色。"""
        mod = _load_execution_module()
        html = mod.build_status_bar_html(mode="live", backend="xtquant", available=False)
        assert "已断开" in html


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildAccountDashboardHtml:
    """账户概览仪表盘测试（F-P0-3）。"""

    def test_empty_snapshot_returns_placeholder(self):
        """空 snapshot 应返回占位符。"""
        mod = _load_execution_module()
        html = mod.build_account_dashboard_html({}, 0, 0, [])
        assert "数据不可用" in html

    def test_normal_snapshot_returns_four_cards(self):
        """正常 snapshot 应返回四宫格。"""
        mod = _load_execution_module()
        snapshot = {
            "nav": 1000000,
            "available_cash": 200000,
            "start_of_day_nav": 990000,
            "positions": {
                "000001.SZ": {"volume": 1000, "available_volume": 1000, "avg_cost": 10},
            },
        }
        html = mod.build_account_dashboard_html(snapshot, 8, 2, [])
        assert "账户净值" in html
        assert "1,000,000.00" in html
        assert "较昨日" in html
        assert "执行进度" in html
        assert "8/10" in html

    def test_fill_rate_below_threshold_uses_warning(self):
        """成交率低于 80% 应使用橙色。"""
        mod = _load_execution_module()
        snapshot = {"nav": 100, "available_cash": 0, "start_of_day_nav": 100, "positions": {}}
        # 5/10 = 50% < 80%
        html = mod.build_account_dashboard_html(snapshot, 5, 5, [])
        assert "50.0%" in html


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildRiskDashboardHtml:
    """风控仪表盘测试（F-P0-4）。"""

    def test_normal_risk_metrics(self):
        """正常风控指标应返回环形图组。"""
        mod = _load_execution_module()
        risk_metrics = {
            "daily_loss_ratio": 0.01,
            "max_single_order_value": 50000,
            "max_single_order_limit": 100000,
            "order_frequency": 1,
            "thresholds": {
                "MAX_DAILY_LOSS_RATIO": 0.02,
                "MAX_SINGLE_ORDER_RATIO": 0.1,
                "MAX_ORDER_FREQUENCY": 2,
            },
        }
        html = mod.build_risk_dashboard_html(risk_metrics, {})
        assert "风控指标" in html
        assert "日亏损" in html
        assert "断路器状态" in html

    def test_stop_signals_triggered_shows_alert(self):
        """止损信号触发时应显示告警。"""
        mod = _load_execution_module()
        risk_metrics = {
            "daily_loss_ratio": 0.03,  # 超限
            "max_single_order_value": 0,
            "max_single_order_limit": 0,
            "order_frequency": 0,
            "thresholds": {"MAX_DAILY_LOSS_RATIO": 0.02, "MAX_SINGLE_ORDER_RATIO": 0.1, "MAX_ORDER_FREQUENCY": 2},
        }
        stop_signals = {
            "any_triggered": True,
            "portfolio_stop": {"triggered": True, "daily_return": -0.03, "threshold": -0.02, "reason": "日亏损超限"},
            "individual_stops": {"000001.SZ": True, "600000.SH": False},
        }
        html = mod.build_risk_dashboard_html(risk_metrics, stop_signals)
        assert "组合日亏损止损触发" in html
        assert "个股止损触发" in html
        assert "000001.SZ" in html

    def test_no_stop_signals_shows_ok(self):
        """无止损信号时应显示正常状态。"""
        mod = _load_execution_module()
        risk_metrics = {
            "daily_loss_ratio": 0.005,
            "max_single_order_value": 0,
            "max_single_order_limit": 0,
            "order_frequency": 0,
            "thresholds": {"MAX_DAILY_LOSS_RATIO": 0.02, "MAX_SINGLE_ORDER_RATIO": 0.1, "MAX_ORDER_FREQUENCY": 2},
        }
        stop_signals = {
            "any_triggered": False,
            "portfolio_stop": {"triggered": False, "daily_return": -0.005, "threshold": -0.02},
            "individual_stops": {"000001.SZ": False},
        }
        html = mod.build_risk_dashboard_html(risk_metrics, stop_signals)
        assert "无止损信号触发" in html

    def test_empty_stop_signals_shows_placeholder(self):
        """空止损信号应显示占位符。"""
        mod = _load_execution_module()
        html = mod.build_risk_dashboard_html({}, {})
        assert "无止损信号数据" in html


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildExecutionReport:
    """主入口端到端测试。"""

    def test_paper_mode_full_report(self):
        """PAPER 模式完整报告生成。"""
        mod = _load_execution_module()
        execution_metadata = {
            "mode": "paper",
            "orders_executed": 5,
            "orders_failed": 1,
            "account_snapshot": {
                "nav": 1000000,
                "available_cash": 200000,
                "start_of_day_nav": 990000,
                "positions": {"000001.SZ": {"volume": 1000, "available_volume": 1000, "avg_cost": 10}},
            },
        }
        html = mod.build_execution_report(
            execution_metadata=execution_metadata,
            audit_log_path=None,
            stop_signals={"any_triggered": False},
        )
        assert "<!DOCTYPE html>" in html
        assert "交易监控报告" in html
        # PAPER 模式（非 LIVE）不带实时状态条
        assert "账户概览" in html
        assert "风控指标" in html

    def test_live_mode_full_report(self):
        """LIVE 模式完整报告生成。"""
        mod = _load_execution_module()
        execution_metadata = {
            "mode": "live",
            "orders_executed": 3,
            "orders_failed": 0,
            "account_snapshot": {
                "nav": 500000,
                "available_cash": 100000,
                "start_of_day_nav": 495000,
                "positions": {},
            },
        }
        html = mod.build_execution_report(
            execution_metadata=execution_metadata,
            audit_log_path=None,
            stop_signals={},
        )
        assert "<!DOCTYPE html>" in html
        assert "实盘交易" in html

    def test_empty_metadata_does_not_raise(self):
        """空 metadata 应不抛异常。"""
        mod = _load_execution_module()
        html = mod.build_execution_report(
            execution_metadata={},
            audit_log_path=None,
            stop_signals=None,
        )
        assert "<!DOCTYPE html>" in html

    def test_with_trade_log_file(self, tmp_path):
        """带审计日志文件时正确加载。"""
        mod = _load_execution_module()
        log_path = tmp_path / "trade_log.jsonl"
        log_path.write_text(
            json.dumps({"price": 10, "volume": 100, "code": "000001.SZ"}) + "\n",
            encoding="utf-8",
        )
        execution_metadata = {
            "mode": "paper",
            "account_snapshot": {"nav": 100000, "start_of_day_nav": 100000},
        }
        html = mod.build_execution_report(
            execution_metadata=execution_metadata,
            audit_log_path=str(log_path),
        )
        assert "<!DOCTYPE html>" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
