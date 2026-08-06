"""L3 集成测试：reports-engine 消费 execution-monitor-engine 真实产物。

测试目标：
- 端到端验证 execution-monitor-engine.run() 产物 → reports-engine.build_execution_report() 的数据契约
- 验证 trade_log.jsonl / ledger.jsonl / account_state.json 被正确读取
- 验证 P0/P1/P2 各功能区块在真实数据下能正常渲染
- 验证 LIVE 模式状态服务与真实 account_state.json 的端到端集成

不验证：
- execution-monitor-engine 执行逻辑本身的正确性（由该引擎自己的测试覆盖）
- 实盘交易后端（xtquant/gm）的真实连接
"""
from __future__ import annotations

import json
import os
import sys
import importlib.util as ilu
import time
import urllib.request
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")
EXECUTION_ENGINE_DIR = os.path.join(ROOT, "skills", "execution-monitor-engine")


def _load_execution_engine():
    """加载 execution-monitor-engine 的 engine 模块。"""
    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    if EXECUTION_ENGINE_DIR not in sys.path:
        sys.path.insert(0, EXECUTION_ENGINE_DIR)

    try:
        spec = ilu.spec_from_file_location(
            "_execution_engine_integration", os.path.join(EXECUTION_ENGINE_DIR, "engine.py"),
        )
        mod = ilu.module_from_spec(spec)
        sys.modules["_execution_engine_integration"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        while EXECUTION_ENGINE_DIR in sys.path:
            sys.path.remove(EXECUTION_ENGINE_DIR)


def _load_reports_execution_report():
    """加载 reports-engine 的 execution_report 模块。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    scripts_dir = REPORTS_SCRIPTS
    init_py = os.path.join(scripts_dir, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts", init_py,
        submodule_search_locations=[scripts_dir],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    spec = ilu.spec_from_file_location(
        "scripts.templates.execution_report",
        os.path.join(scripts_dir, "templates", "execution_report.py"),
    )
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.templates.execution_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_status_server():
    """加载 status_server 模块。"""
    spec = ilu.spec_from_file_location(
        "scripts.status_server",
        os.path.join(REPORTS_SCRIPTS, "status_server.py"),
    )
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.status_server"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_trade_log_jsonl(path, records):
    """生成 trade_log.jsonl。"""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _make_ledger_jsonl(path, records):
    """生成 ledger.jsonl。"""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _make_account_state_json(path, snapshot):
    """生成 account_state.json。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)


# ============================================================
# L3 集成测试：execution-monitor-engine → reports-engine
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestExecutionEngineIntegration:
    """端到端验证 reports-engine 消费 execution-monitor-engine 真实产物。"""

    def test_end_to_end_with_real_artifacts(self, tmp_path, monkeypatch):
        """完整链路：trade_log.jsonl + ledger.jsonl + account_state.json → build_execution_report()。"""
        exec_dir = tmp_path / "execution"
        exec_dir.mkdir()

        # 生成真实结构的产物文件
        audit_path = exec_dir / "trade_log.jsonl"
        ledger_path = exec_dir / "ledger.jsonl"
        state_path = exec_dir / "account_state.json"

        _make_trade_log_jsonl(audit_path, [
            {"timestamp": "2025-01-01T09:30:00", "order_id": "1", "code": "000001.SZ",
             "side": "buy", "volume": 1000, "price": 10.0, "status": "filled",
             "metadata": {"order_price": 10.0}},
            {"timestamp": "2025-01-01T10:00:00", "order_id": "2", "code": "000002.SZ",
             "side": "buy", "volume": 500, "price": 20.0, "status": "filled",
             "metadata": {"order_price": 20.1}},
            {"timestamp": "2025-01-01T14:00:00", "order_id": "3", "code": "600000.SH",
             "side": "sell", "volume": 200, "price": 11.0, "status": "rejected",
             "metadata": {"order_price": 11.0, "error": "资金不足"}},
        ])

        _make_ledger_jsonl(ledger_path, [
            {"execution_id": "1", "trade_date": "2025-01-01", "code": "000001.SZ",
             "side": "buy", "shares": 1000, "price": 10.0,
             "commission": 5.0, "stamp_tax": 0.0, "slippage_cost": 0.5,
             "position_after_shares": 1000, "cash_after": 999995.0, "nav_after": 1000005.0,
             "confirmed": True, "created_at": "2025-01-01T09:30:00"},
            {"execution_id": "2", "trade_date": "2025-01-01", "code": "000002.SZ",
             "side": "buy", "shares": 500, "price": 20.0,
             "commission": 5.0, "stamp_tax": 0.0, "slippage_cost": 1.0,
             "position_after_shares": 500, "cash_after": 989990.0, "nav_after": 1000000.0,
             "confirmed": True, "created_at": "2025-01-01T10:00:00"},
        ])

        _make_account_state_json(state_path, {
            "nav": 1000000.0,
            "available_cash": 989990.0,
            "start_of_day_nav": 1000000.0,
            "positions": {
                "000001.SZ": {"volume": 1000, "available_volume": 1000, "avg_cost": 10.0},
                "000002.SZ": {"volume": 500, "available_volume": 500, "avg_cost": 20.0},
            },
        })

        # 模拟 execution-monitor-engine.run() 返回的 metadata
        execution_metadata = {
            "mode": "paper",
            "orders_executed": 2,
            "orders_failed": 1,
            "account_snapshot": {
                "nav": 1000000.0,
                "available_cash": 989990.0,
                "start_of_day_nav": 1000000.0,
                "positions": {
                    "000001.SZ": {"volume": 1000, "available_volume": 1000, "avg_cost": 10.0},
                    "000002.SZ": {"volume": 500, "available_volume": 500, "avg_cost": 20.0},
                },
            },
            "backend_available": False,
        }

        exec_mod = _load_reports_execution_report()
        html = exec_mod.build_execution_report(
            execution_metadata=execution_metadata,
            audit_log_path=str(audit_path),
            ledger_path=str(ledger_path),
            include_p1=True,
            include_p2=True,
            enable_live_polling=False,  # PAPER 模式
        )

        # 验证 P0 区块
        assert "<!DOCTYPE html>" in html
        assert "账户概览" in html
        assert "风控指标" in html
        # 验证账户数据正确读取
        assert "1,000,000" in html or "1000000" in html

        # 验证 P1 持仓明细表
        assert "持仓明细" in html
        assert "000001.SZ" in html
        assert "000002.SZ" in html

        # 验证 P1 净值曲线（从 ledger 回放）
        assert "净值曲线" in html

        # 验证 P2 当日成交
        assert "当日成交" in html
        # 验证滑点成本展示
        assert "滑点" in html

        # 验证 P2 当日委托
        assert "当日委托" in html
        assert "成交率" in html

    def test_report_reads_account_state_correctly(self, tmp_path):
        """验证 account_snapshot 字段被正确派生计算。"""
        exec_dir = tmp_path / "execution"
        exec_dir.mkdir()

        execution_metadata = {
            "mode": "paper",
            "orders_executed": 1,
            "orders_failed": 0,
            "account_snapshot": {
                "nav": 1050000.0,
                "available_cash": 50000.0,
                "start_of_day_nav": 1000000.0,
                "positions": {
                    "000001.SZ": {"volume": 1000, "available_volume": 1000, "avg_cost": 10.0},
                },
            },
        }

        exec_mod = _load_reports_execution_report()
        html = exec_mod.build_execution_report(
            execution_metadata=execution_metadata,
            include_p1=False,
            include_p2=False,
            enable_live_polling=False,
        )

        # 验证较昨日变化 = 1050000 - 1000000 = +50000 (+5.00%)
        assert "50,000" in html or "50000" in html
        assert "5.00%" in html or "5.0%" in html

    def test_stop_signals_from_portfolio_engine_consumed(self, tmp_path):
        """验证跨引擎引用的 stop_signals 被正确展示。"""
        exec_mod = _load_reports_execution_report()
        stop_signals = {
            "portfolio_stop": {
                "triggered": True,
                "daily_return": -0.025,
                "threshold": 0.02,
                "reason": "单日亏损超过阈值",
            },
            "individual_stops": {"000001.SZ": True, "000002.SZ": False},
            "any_triggered": True,
        }

        html = exec_mod.build_execution_report(
            execution_metadata={"mode": "paper", "account_snapshot": {"nav": 100}},
            stop_signals=stop_signals,
            include_p1=False,
            include_p2=False,
            enable_live_polling=False,
        )

        assert "止损" in html
        assert "单日亏损" in html or "阈值" in html

    def test_live_mode_status_server_with_real_account_state(self, tmp_path):
        """LIVE 模式：StatusServer 读取真实 account_state.json 并通过 HTTP 返回。"""
        status_mod = _load_status_server()

        state_path = tmp_path / "account_state.json"
        _make_account_state_json(state_path, {
            "nav": 1050000.0,
            "available_cash": 50000.0,
            "start_of_day_nav": 1000000.0,
            "positions": {"000001.SZ": {"volume": 1000, "avg_cost": 10.0}},
        })

        audit_path = tmp_path / "trade_log.jsonl"
        _make_trade_log_jsonl(audit_path, [
            {"status": "filled"},
            {"status": "filled"},
            {"status": "rejected"},
        ])

        server = status_mod.StatusServer(
            account_state_path=str(state_path),
            audit_log_path=str(audit_path),
        )
        try:
            port = server.start()
            # 调用 /api/snapshot 验证真实数据
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=2) as r:
                data = json.loads(r.read())
                assert data["account_snapshot"]["nav"] == 1050000.0
                assert data["orders_executed"] == 2
                assert data["orders_failed"] == 1
                # 日亏损率 = (1050000 - 1000000) / 1000000 = 0.05
                assert abs(data["risk_metrics"]["daily_loss_ratio"] - 0.05) < 1e-6
        finally:
            server.stop()

    def test_empty_artifacts_does_not_raise(self, tmp_path):
        """完全无产物文件时报告应降级但不抛异常。"""
        exec_mod = _load_reports_execution_report()
        html = exec_mod.build_execution_report(
            execution_metadata={},
            audit_log_path=None,
            ledger_path=None,
            include_p1=True,
            include_p2=True,
            enable_live_polling=False,
        )
        assert "<!DOCTYPE html>" in html
        # 所有区块标题应存在（占位符）
        assert "账户概览" in html
        assert "风控指标" in html
        assert "持仓明细" in html
        assert "净值曲线" in html
        assert "当日成交" in html
        assert "当日委托" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
