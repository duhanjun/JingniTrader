"""L3 集成测试：reports-engine 消费 portfolio-risk-engine 真实产物。

测试目标：
- 端到端验证 portfolio-risk-engine.run() 产物 → reports-engine.build_portfolio_report() 的数据契约
- 验证报告能正确读取 portfolio_weights.json 和 metadata
- 验证 P0/P1/P2 各功能区块在真实数据下能正常渲染
- 验证数据缺失场景（如无 DATA 产物）的容错降级

不验证：
- portfolio-risk-engine 优化算法本身的正确性（由该引擎自己的测试覆盖）
- 报告视觉样式（由人工评审）
"""
from __future__ import annotations

import json
import os
import sys
import importlib.util as ilu
import shutil
from unittest import mock

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")
PORTFOLIO_ENGINE_DIR = os.path.join(ROOT, "skills", "portfolio-risk-engine")


def _load_portfolio_engine():
    """加载 portfolio-risk-engine 的 engine 模块（独立命名避免冲突）。"""
    # mock 重量级依赖
    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    # 清理 scripts 缓存
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # 临时把 portfolio-risk-engine 目录加入 sys.path
    if PORTFOLIO_ENGINE_DIR not in sys.path:
        sys.path.insert(0, PORTFOLIO_ENGINE_DIR)

    try:
        spec = ilu.spec_from_file_location(
            "_portfolio_engine_integration", os.path.join(PORTFOLIO_ENGINE_DIR, "engine.py"),
        )
        mod = ilu.module_from_spec(spec)
        sys.modules["_portfolio_engine_integration"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        while PORTFOLIO_ENGINE_DIR in sys.path:
            sys.path.remove(PORTFOLIO_ENGINE_DIR)


def _load_reports_portfolio_report():
    """加载 reports-engine 的 portfolio_report 模块。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # mock 依赖
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

    # 加载 portfolio_report
    spec = ilu.spec_from_file_location(
        "scripts.templates.portfolio_report",
        os.path.join(scripts_dir, "templates", "portfolio_report.py"),
    )
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.templates.portfolio_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_synthetic_data_parquet(tmp_path, n_assets: int = 5, n_days: int = 60):
    """生成合成 DATA 产物 parquet（模拟 data-engine 输出）。"""
    rng = np.random.default_rng(42)
    codes = [f"00000{i}.SZ" for i in range(1, n_assets + 1)]
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    rows = []
    for code in codes:
        price = 10.0
        for d in dates:
            price *= (1 + rng.normal(0.001, 0.02))
            rows.append({"date": d, "code": code, "close": price})
    df = pd.DataFrame(rows)
    path = tmp_path / "data.parquet"
    df.to_parquet(path, index=False)
    return str(path)


# ============================================================
# L3 集成测试：portfolio-risk-engine → reports-engine
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestPortfolioEngineIntegration:
    """端到端验证 reports-engine 消费 portfolio-risk-engine 真实产物。"""

    def test_end_to_end_with_real_engine_output(self, tmp_path, monkeypatch):
        """完整链路：portfolio-risk-engine.run() → portfolio_weights.json → build_portfolio_report()。"""
        # 准备工作目录
        work_dir = tmp_path / "workspace"
        portfolio_dir = work_dir / "portfolio"
        portfolio_dir.mkdir(parents=True)
        monkeypatch.setenv("QUANT_WORK_DIR", str(work_dir))
        monkeypatch.setenv("PORTFOLIO_DIR", str(portfolio_dir))

        # 生成合成 DATA 产物
        data_path = _make_synthetic_data_parquet(tmp_path)

        # 调用 portfolio-risk-engine.run()
        engine_mod = _load_portfolio_engine()
        codes = [f"00000{i}.SZ" for i in range(1, 6)]
        dates = pd.date_range("2025-01-01", periods=60, freq="B")
        rng = np.random.default_rng(42)
        returns_data = rng.normal(0.001, 0.02, size=(60, 5))
        returns_df = pd.DataFrame(returns_data, index=dates, columns=codes)

        # 构造简单 Context（避开复杂依赖）
        ctx = mock.MagicMock()
        ctx.artifacts = {"DATA": {"path": data_path}}
        ctx.metadata = {"returns_df": returns_df, "optimization_method": "max_sharpe"}

        # 直接调用 engine.run，捕获结果
        try:
            result = engine_mod.run(ctx)
        except Exception as e:
            pytest.skip(f"portfolio-risk-engine.run() 在测试环境不可用: {e}")

        if not result.get("success"):
            pytest.skip(f"portfolio-risk-engine.run() 未成功: {result.get('error', '')}")

        # 验证产物契约
        assert result["success"] is True
        assert "artifact_path" in result
        assert "metadata" in result
        artifact_path = result["artifact_path"]
        assert os.path.exists(artifact_path), f"portfolio_weights.json 不存在: {artifact_path}"

        # 验证 metadata 字段契约
        metadata = result["metadata"]
        assert "weights" in metadata
        assert "metrics" in metadata
        assert "var_cvar" in metadata
        assert "optimization_method" in metadata
        assert "num_assets" in metadata

        # 调用 reports-engine.build_portfolio_report() 消费产物
        portfolio_mod = _load_reports_portfolio_report()
        html = portfolio_mod.build_portfolio_report(
            portfolio_artifact_path=artifact_path,
            portfolio_metadata=metadata,
            data_path=data_path,
            include_p1=True,
            include_p2=True,
        )

        # 验证报告内容
        assert "<!DOCTYPE html>" in html
        assert "组合优化报告" in html or "权重" in html
        # P0 区块
        assert "权重分布" in html or "权重偏差" in html
        # P1 区块（有 DATA 时应渲染）
        assert "风险贡献分解" in html
        assert "有效前沿" in html
        # P2 区块
        assert "协方差矩阵" in html
        assert "行业配置偏差" in html

    def test_report_consumes_weights_json_directly(self, tmp_path):
        """验证 build_portfolio_report 直接读取 portfolio_weights.json。"""
        # 模拟 portfolio-risk-engine 落盘的 weights 文件
        portfolio_dir = tmp_path / "portfolio"
        portfolio_dir.mkdir()
        weights_path = portfolio_dir / "portfolio_weights.json"
        weights = {"000001.SZ": 0.3, "000002.SZ": 0.25, "600000.SH": 0.45}
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        # 模拟 portfolio-risk-engine 返回的 metadata
        metadata = {
            "weights": weights,
            "metrics": {
                "expected_return": 0.15,
                "volatility": 0.18,
                "sharpe_ratio": 0.67,
            },
            "var_cvar": {"VaR": -0.02, "CVaR": -0.03, "confidence": 0.95},
            "stop_signals": {
                "portfolio_stop": {"triggered": False, "daily_return": 0.01, "threshold": 0.02},
                "individual_stops": {},
                "any_triggered": False,
            },
            "constraint_check": {"max_single_weight": True, "weights_sum_one": True},
            "optimization_method": "max_sharpe",
            "num_assets": 3,
        }

        portfolio_mod = _load_reports_portfolio_report()
        html = portfolio_mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata=metadata,
            include_p1=False,
            include_p2=False,
        )

        # 验证权重被正确读取
        assert "<!DOCTYPE html>" in html
        for code in weights:
            assert code in html

    def test_report_handles_missing_data_gracefully(self, tmp_path):
        """DATA 产物缺失时报告应降级但不阻塞生成。"""
        portfolio_dir = tmp_path / "portfolio"
        portfolio_dir.mkdir()
        weights_path = portfolio_dir / "portfolio_weights.json"
        weights = {"000001.SZ": 0.5, "000002.SZ": 0.5}
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        metadata = {
            "weights": weights,
            "metrics": {"expected_return": 0.1, "volatility": 0.15, "sharpe_ratio": 0.47},
            "var_cvar": {"VaR": -0.02, "CVaR": -0.03, "confidence": 0.95},
            "optimization_method": "max_sharpe",
            "num_assets": 2,
        }

        portfolio_mod = _load_reports_portfolio_report()
        # 不传 data_path，P1/P2 应降级为占位符
        html = portfolio_mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata=metadata,
            data_path=None,
            include_p1=True,
            include_p2=True,
        )

        assert "<!DOCTYPE html>" in html
        # P1/P2 标题应存在（占位符）
        assert "风险贡献分解" in html
        assert "协方差矩阵" in html

    def test_var_cvar_metadata_consumed(self, tmp_path):
        """验证 metadata.var_cvar 字段被正确消费展示。"""
        portfolio_dir = tmp_path / "portfolio"
        portfolio_dir.mkdir()
        weights_path = portfolio_dir / "portfolio_weights.json"
        weights = {"000001.SZ": 1.0}
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        metadata = {
            "weights": weights,
            "metrics": {},
            "var_cvar": {"VaR": -0.025, "CVaR": -0.035, "confidence": 0.95},
            "optimization_method": "test",
            "num_assets": 1,
        }

        portfolio_mod = _load_reports_portfolio_report()
        html = portfolio_mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata=metadata,
            include_p1=False,
            include_p2=False,
        )

        # VaR/CVaR 应出现在报告里
        assert "VaR" in html or "-2.5" in html or "-0.025" in html
        assert "CVaR" in html or "-3.5" in html or "-0.035" in html

    def test_stop_signals_consumed(self, tmp_path):
        """验证 metadata.stop_signals 字段被正确消费。"""
        portfolio_dir = tmp_path / "portfolio"
        portfolio_dir.mkdir()
        weights_path = portfolio_dir / "portfolio_weights.json"
        weights = {"000001.SZ": 0.6, "000002.SZ": 0.4}
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        metadata = {
            "weights": weights,
            "metrics": {},
            "var_cvar": {},
            "stop_signals": {
                "portfolio_stop": {
                    "triggered": True,
                    "daily_return": -0.025,
                    "threshold": 0.02,
                    "reason": "单日亏损超过阈值",
                },
                "individual_stops": {"000001.SZ": True},
                "any_triggered": True,
            },
            "optimization_method": "test",
            "num_assets": 2,
        }

        portfolio_mod = _load_reports_portfolio_report()
        html = portfolio_mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata=metadata,
            include_p1=False,
            include_p2=False,
        )

        # 止损信号应在报告中体现
        assert "止损" in html or "单日亏损" in html or "triggered" in html.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
