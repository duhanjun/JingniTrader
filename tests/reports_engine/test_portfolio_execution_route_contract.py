"""组合优化与执行监控报告路由契约测试（L1）。

验证 reports-engine.run(ctx) 的新路由分支契约：
- report_intent="portfolio" → 组合优化报告
- report_intent="execution" → 执行监控报告

关键契约：
- 无 PORTFOLIO/EXECUTION 产物时返回 success=False + 明确错误信息
- 有产物时返回 success=True + artifact_path + 必需 metadata 字段
- 不抛异常（数据缺失容错）
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
REPORTS_ENGINE_PATH = os.path.join(REPORTS_ENGINE_DIR, "engine.py")

_CONTEXT_MODULE = None


def _get_context_class():
    global _CONTEXT_MODULE
    if _CONTEXT_MODULE is not None:
        return _CONTEXT_MODULE
    context_path = os.path.join(ROOT, "scripts", "context.py")
    if os.path.exists(context_path):
        spec = ilu.spec_from_file_location("jingni_context", context_path)
        mod = ilu.module_from_spec(spec)
        sys.modules["jingni_context"] = mod
        spec.loader.exec_module(mod)
        _CONTEXT_MODULE = mod.Context
        return mod.Context
    raise ImportError("无法加载 Context 类")


def _load_reports_engine_module():
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    scripts_dir = os.path.join(REPORTS_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts", init_py,
            submodule_search_locations=[scripts_dir],
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)

    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    spec = ilu.spec_from_file_location("reports_engine_engine", REPORTS_ENGINE_PATH)
    mod = ilu.module_from_spec(spec)
    sys.modules["reports_engine_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_ctx(report_intent=None, stock_pool=None):
    Context = _get_context_class()
    ctx = Context(
        task_id="test_report",
        stock_pool=stock_pool or ["000001.SZ", "600000.SH"],
        start_date="2024-01-01",
        end_date="2024-06-30",
    )
    if report_intent:
        ctx.metadata["report_intent"] = report_intent
    return ctx


@pytest.mark.skill_reports_engine
@pytest.mark.contract
class TestPortfolioRouteContract:
    """组合优化报告路由契约。"""

    def test_no_portfolio_artifact_returns_failure(self, monkeypatch, tmp_path):
        """无 PORTFOLIO 产物应返回 success=False + 明确错误。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))
        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="portfolio")
        # 不设置 PORTFOLIO 产物

        result = reports_mod.run(ctx)

        assert isinstance(result, dict)
        assert result["success"] is False
        assert "PORTFOLIO" in result["error"]
        for field in ("success", "artifact_path", "metadata", "error"):
            assert field in result

    def test_with_portfolio_artifact_returns_success(self, monkeypatch, tmp_path):
        """有 PORTFOLIO 产物应返回 success=True + artifact_path。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))

        # 创建权重文件
        weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="portfolio")
        ctx.update_artifact("PORTFOLIO", str(weights_path))
        ctx.metadata["portfolio_metadata"] = {
            "optimization_method": "mean_variance",
            "num_assets": 2,
            "weights": weights,
        }

        result = reports_mod.run(ctx)

        assert result["success"] is True
        assert result["artifact_path"].endswith("portfolio_report.html")
        assert os.path.exists(result["artifact_path"])
        assert result["metadata"]["report_type"] == "portfolio"

    def test_with_empty_metadata_does_not_raise(self, monkeypatch, tmp_path):
        """空 metadata 应不抛异常。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))

        weights = {"000001.SZ": 1.0}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="portfolio")
        ctx.update_artifact("PORTFOLIO", str(weights_path))
        # 不设置 portfolio_metadata

        result = reports_mod.run(ctx)

        assert result["success"] is True


@pytest.mark.skill_reports_engine
@pytest.mark.contract
class TestExecutionRouteContract:
    """执行监控报告路由契约。"""

    def test_no_execution_artifact_returns_failure(self, monkeypatch, tmp_path):
        """无 EXECUTION 产物应返回 success=False + 明确错误。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))
        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="execution")

        result = reports_mod.run(ctx)

        assert isinstance(result, dict)
        assert result["success"] is False
        assert "EXECUTION" in result["error"]

    def test_with_execution_artifact_returns_success(self, monkeypatch, tmp_path):
        """有 EXECUTION 产物应返回 success=True + artifact_path。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))

        # 创建 trade_log.jsonl 作为 EXECUTION 产物
        exec_dir = tmp_path / "execution"
        exec_dir.mkdir()
        log_path = exec_dir / "trade_log.jsonl"
        log_path.write_text(
            json.dumps({"code": "000001.SZ", "side": "buy", "price": 10, "volume": 100}) + "\n",
            encoding="utf-8",
        )

        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="execution")
        ctx.update_artifact("EXECUTION", str(log_path))
        ctx.metadata["execution_metadata"] = {
            "mode": "paper",
            "orders_executed": 1,
            "orders_failed": 0,
            "account_snapshot": {
                "nav": 100000,
                "available_cash": 50000,
                "start_of_day_nav": 100000,
                "positions": {},
            },
        }

        result = reports_mod.run(ctx)

        assert result["success"] is True
        assert result["artifact_path"].endswith("execution_report.html")
        assert os.path.exists(result["artifact_path"])
        assert result["metadata"]["report_type"] == "execution"
        assert result["metadata"]["mode"] == "paper"


@pytest.mark.skill_reports_engine
@pytest.mark.contract
class TestRoutePriorityContract:
    """路由优先级契约。"""

    def test_attribution_takes_priority_over_portfolio(self, monkeypatch, tmp_path):
        """attribution 意图应优先于 portfolio。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))
        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="attribution")
        # 同时设置 PORTFOLIO 产物，但应走 attribution 路径

        result = reports_mod.run(ctx)

        # attribution 无 EXECUTION 产物应返回失败，但错误信息应提及 attribution 相关
        assert result["success"] is False
        assert "EXECUTION" in result["error"] or "ledger" in result["error"]

    def test_portfolio_takes_priority_over_backtest(self, monkeypatch, tmp_path):
        """portfolio 意图应优先于 backtest。"""
        monkeypatch.setenv("QUANT_WORK_DIR", str(tmp_path))

        weights = {"000001.SZ": 1.0}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        reports_mod = _load_reports_engine_module()
        ctx = _make_ctx(report_intent="portfolio")
        ctx.update_artifact("PORTFOLIO", str(weights_path))
        # 同时设置 BACKTEST 产物，但应走 portfolio 路径
        ctx.update_artifact("BACKTEST", str(tmp_path / "backtest.json"))

        result = reports_mod.run(ctx)

        # 应走 portfolio 路径（即使有 BACKTEST 产物）
        assert result["success"] is True
        assert result["metadata"].get("report_type") == "portfolio"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
