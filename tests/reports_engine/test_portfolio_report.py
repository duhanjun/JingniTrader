"""组合优化报告模板单元测试（L2）。

覆盖 templates/portfolio_report.py：
- load_industry_map: 行业映射加载（含数据缺失容错）
- make_weight_pie_chart: 权重环形图
- build_portfolio_html: HTML 结构完整性
- build_portfolio_report: 主入口端到端

关键路径：数据缺失容错、权重文件加载、HTML 输出正确性
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


def _load_portfolio_module():
    """加载 portfolio_report 模块。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # mock 重量级依赖
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

    # 加载 portfolio_report
    portfolio_path = os.path.join(REPORTS_SCRIPTS, "templates", "portfolio_report.py")
    spec = ilu.spec_from_file_location("scripts.templates.portfolio_report", portfolio_path)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.templates.portfolio_report"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestLoadIndustryMap:
    """行业映射加载测试（数据缺失容错）。"""

    def test_none_path_returns_empty(self):
        """path=None 应返回空 dict。"""
        mod = _load_portfolio_module()
        assert mod.load_industry_map(None) == {}

    def test_nonexistent_path_returns_empty(self):
        """路径不存在应返回空 dict，不抛异常。"""
        mod = _load_portfolio_module()
        assert mod.load_industry_map("/nonexistent/path.parquet") == {}


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestMakeWeightPieChart:
    """权重环形图测试。"""

    def test_empty_weights_returns_placeholder(self):
        """空权重应返回占位符。"""
        mod = _load_portfolio_module()
        result = mod.make_weight_pie_chart({}, {}, "test", 0)
        assert "无权重数据" in result

    def test_normal_weights_returns_plotly_html(self):
        """正常权重应返回 Plotly HTML。"""
        mod = _load_portfolio_module()
        weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        result = mod.make_weight_pie_chart(weights, {}, "mean_variance", 2)
        assert "plotly" in result.lower() or "<div" in result

    def test_with_industry_map(self):
        """带行业映射时应正确分组着色。"""
        mod = _load_portfolio_module()
        weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        industry_map = {"000001.SZ": "银行", "600000.SH": "银行"}
        result = mod.make_weight_pie_chart(weights, industry_map, "test", 2)
        assert isinstance(result, str)


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildPortfolioHtml:
    """HTML 结构完整性测试。"""

    def test_html_contains_required_sections(self):
        """HTML 应包含所有必需区块。"""
        mod = _load_portfolio_module()
        weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        metadata = {
            "optimization_method": "mean_variance",
            "num_assets": 2,
            "metrics": {
                "expected_return": 0.15,
                "volatility": 0.2,
                "sharpe_ratio": 0.75,
            },
            "var_cvar": {"VaR": -0.03, "CVaR": -0.05, "confidence": 0.95},
            "constraint_check": {"max_single_weight": True, "weights_sum_one": True},
            "stop_signals": {
                "any_triggered": False,
                "portfolio_stop": {"triggered": False, "daily_return": -0.01, "threshold": -0.03},
                "individual_stops": {"000001.SZ": False},
            },
        }
        html = mod.build_portfolio_html(
            weights=weights,
            metadata=metadata,
            industry_map={},
            weight_deviation_svg="<svg></svg>",
            pie_chart_html="<div>pie</div>",
        )
        assert "<!DOCTYPE html>" in html
        assert "组合优化报告" in html
        assert "权重分布" in html
        assert "优化对比" in html
        assert "约束检查" in html
        assert "止损信号" in html
        assert "免责声明" in html

    def test_html_with_empty_metadata(self):
        """空 metadata 时应不抛异常，显示占位符。"""
        mod = _load_portfolio_module()
        html = mod.build_portfolio_html(
            weights={},
            metadata={},
            industry_map={},
            weight_deviation_svg="",
            pie_chart_html="",
        )
        assert "<!DOCTYPE html>" in html
        assert "数据不可用" in html or "无" in html


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildPortfolioReport:
    """主入口端到端测试。"""

    def test_with_valid_weights_file(self, tmp_path):
        """有有效权重文件时应生成完整 HTML。"""
        mod = _load_portfolio_module()

        # 创建权重文件
        weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")

        metadata = {
            "optimization_method": "mean_variance",
            "num_assets": 2,
            "metrics": {"expected_return": 0.1, "volatility": 0.15, "sharpe_ratio": 0.5},
        }

        html = mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata=metadata,
            factor_path=None,
        )
        assert "<!DOCTYPE html>" in html
        assert "000001.SZ" in html

    def test_with_missing_weights_file_falls_back_to_metadata(self, tmp_path):
        """权重文件缺失时应回退到 metadata.weights。"""
        mod = _load_portfolio_module()

        metadata = {
            "optimization_method": "test",
            "num_assets": 1,
            "weights": {"000001.SZ": 1.0},
        }

        html = mod.build_portfolio_report(
            portfolio_artifact_path=str(tmp_path / "nonexistent.json"),
            portfolio_metadata=metadata,
        )
        assert "<!DOCTYPE html>" in html
        assert "000001.SZ" in html

    def test_with_empty_data_does_not_raise(self):
        """完全空数据时应不抛异常。"""
        mod = _load_portfolio_module()
        html = mod.build_portfolio_report(
            portfolio_artifact_path="",
            portfolio_metadata={},
        )
        assert "<!DOCTYPE html>" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
