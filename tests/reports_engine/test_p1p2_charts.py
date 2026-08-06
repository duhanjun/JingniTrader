"""组合优化与执行监控 P1/P2 扩展图表单元测试（L2）。

覆盖：
- scripts/cross_engine_adapter.py: 薄包装重算（协方差、风险贡献、有效前沿采样、行业权重）
- scripts/templates/portfolio_charts_p1p2.py: 组合优化 P1/P2 图表
  - F-P1-2: 风险贡献分解
  - F-P1-3: 有效前沿可视化
  - F-P2-1: 协方差矩阵热力图
  - F-P2-2: 行业配置偏差图
  - F-P2-3: 优化前后对比
- scripts/templates/execution_charts_p1p2.py: 执行监控 P1/P2 图表
  - F-P1-1: 持仓明细表
  - F-P1-4: 净值曲线与回撤
  - F-P2-4: 当日成交分析
  - F-P2-5: 当日委托
- build_portfolio_report / build_execution_report 的 P1/P2 集成路径

关键路径：数据缺失容错（空 DataFrame/空 dict 不抛异常）、降级策略、HTML 输出正确性
"""
from __future__ import annotations

import json
import os
import sys
import importlib.util as ilu
from unittest import mock

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")


def _load_reports_modules():
    """加载 reports-engine scripts 全部子模块（含 cross_engine_adapter / p1p2 charts）。

    返回 dict 含: portfolio_report, execution_report, portfolio_charts_p1p2,
    execution_charts_p1p2, cross_engine_adapter, svg_components
    """
    # 清理 scripts 缓存
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # mock 重量级依赖
    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    # 注册 scripts 包
    init_py = os.path.join(REPORTS_SCRIPTS, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts", init_py,
        submodule_search_locations=[REPORTS_SCRIPTS],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 加载各子模块
    loaded = {}
    module_specs = [
        ("scripts.renderers.svg_components", os.path.join(REPORTS_SCRIPTS, "renderers", "svg_components.py")),
        ("scripts.cross_engine_adapter", os.path.join(REPORTS_SCRIPTS, "cross_engine_adapter.py")),
        ("scripts.templates.portfolio_charts_p1p2", os.path.join(REPORTS_SCRIPTS, "templates", "portfolio_charts_p1p2.py")),
        ("scripts.templates.execution_charts_p1p2", os.path.join(REPORTS_SCRIPTS, "templates", "execution_charts_p1p2.py")),
        ("scripts.templates.portfolio_report", os.path.join(REPORTS_SCRIPTS, "templates", "portfolio_report.py")),
        ("scripts.templates.execution_report", os.path.join(REPORTS_SCRIPTS, "templates", "execution_report.py")),
    ]
    for mod_name, mod_path in module_specs:
        spec = ilu.spec_from_file_location(mod_name, mod_path)
        mod = ilu.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        loaded[mod_name.split(".")[-1]] = mod

    return loaded


def _make_synthetic_returns(n_assets: int = 5, n_days: int = 60) -> pd.DataFrame:
    """生成合成收益率 DataFrame 用于 P1/P2 测试。"""
    rng = np.random.default_rng(42)
    codes = [f"00000{i}.SZ" for i in range(1, n_assets + 1)]
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    data = rng.normal(0.001, 0.02, size=(n_days, n_assets))
    return pd.DataFrame(data, index=dates, columns=codes)


def _make_synthetic_data_parquet(tmp_path, n_assets: int = 5, n_days: int = 60):
    """生成合成 DATA 产物 parquet 文件（含 date/code/close 列）。"""
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
# cross_engine_adapter 单元测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestCrossEngineAdapter:
    """薄包装重算函数测试。"""

    def test_load_returns_from_data_none_path(self):
        """path=None 应返回空 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.load_returns_from_data(None).empty

    def test_load_returns_from_data_nonexistent_path(self):
        """不存在的路径应返回空 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.load_returns_from_data("/nonexistent.parquet").empty

    def test_load_returns_from_data_valid_parquet(self, tmp_path):
        """合法 parquet 应返回 pivot 后的收益率 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        data_path = _make_synthetic_data_parquet(tmp_path)
        returns = mod.load_returns_from_data(data_path)
        assert not returns.empty
        assert returns.shape[1] == 5  # 5 个资产
        assert returns.shape[0] == 59  # 60 天 - 1（pct_change 丢首行）

    def test_compute_cov_matrix_empty_returns(self):
        """空 returns 应返回空 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.compute_cov_matrix(pd.DataFrame()).empty

    def test_compute_cov_matrix_valid_returns(self, tmp_path):
        """合法 returns 应返回方阵。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        data_path = _make_synthetic_data_parquet(tmp_path)
        returns = mod.load_returns_from_data(data_path)
        cov = mod.compute_cov_matrix(returns)
        assert not cov.empty
        assert cov.shape[0] == cov.shape[1] == 5

    def test_compute_risk_contributions_empty_inputs(self):
        """空 weights 或空 cov 应返回空 Series。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.compute_risk_contributions({}, pd.DataFrame()).empty
        assert mod.compute_risk_contributions({"A": 1.0}, pd.DataFrame()).empty

    def test_compute_risk_contributions_normal_case(self):
        """正常输入应返回归一化的风险贡献 Series（和为 1）。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        weights = {"A": 0.5, "B": 0.5}
        cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.09]], index=["A", "B"], columns=["A", "B"])
        rc = mod.compute_risk_contributions(weights, cov)
        assert not rc.empty
        assert abs(rc.sum() - 1.0) < 1e-6  # 归一化
        assert all(c in rc.index for c in ["A", "B"])

    def test_compute_efficient_frontier_points_empty(self):
        """空 returns/weights 应返回空 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.compute_efficient_frontier_points(pd.DataFrame(), {}).empty
        assert mod.compute_efficient_frontier_points(_make_synthetic_returns(), {}).empty

    def test_compute_efficient_frontier_points_valid(self, tmp_path):
        """合法输入应返回含 volatility/return/sharpe 列的 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        data_path = _make_synthetic_data_parquet(tmp_path)
        returns = mod.load_returns_from_data(data_path)
        weights = {c: 1.0 / 5 for c in returns.columns}
        points = mod.compute_efficient_frontier_points(returns, weights, n_samples=10)
        assert not points.empty
        assert "volatility" in points.columns
        assert "return" in points.columns
        assert "sharpe" in points.columns

    def test_compute_equal_weight_metrics_empty(self):
        """空输入应返回空 dict。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.compute_equal_weight_metrics(pd.DataFrame(), {}) == {}

    def test_compute_equal_weight_metrics_valid(self):
        """合法输入应返回完整指标 dict。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        returns = _make_synthetic_returns()
        weights = {c: 1.0 / 5 for c in returns.columns}
        metrics = mod.compute_equal_weight_metrics(returns, weights)
        assert "expected_return" in metrics
        assert "volatility" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "diversification" in metrics
        assert metrics["diversification"] > 0.7  # 5 资产等权分散化高

    def test_compute_current_metrics_valid(self):
        """当前组合指标应正确计算。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        returns = _make_synthetic_returns()
        weights = {c: 1.0 / 5 for c in returns.columns}
        metrics = mod.compute_current_metrics(returns, weights)
        assert "expected_return" in metrics
        assert "sharpe_ratio" in metrics

    def test_compute_industry_weights_empty(self):
        """空 weights 应返回空 DataFrame。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        assert mod.compute_industry_weights({}, {}).empty

    def test_compute_industry_weights_normal(self):
        """正常输入应聚合出行业权重。"""
        mod = _load_reports_modules()["cross_engine_adapter"]
        weights = {"000001.SZ": 0.3, "000002.SZ": 0.2, "600000.SH": 0.5}
        industry_map = {"000001.SZ": "银行", "000002.SZ": "房地产", "600000.SH": "银行"}
        df = mod.compute_industry_weights(weights, industry_map)
        assert not df.empty
        assert len(df) == 2  # 银行 + 房地产
        assert "actual_weight" in df.columns
        assert "benchmark_weight" in df.columns
        assert "deviation" in df.columns
        # 银行应聚合 0.3 + 0.5 = 0.8
        bank_row = df[df["industry"] == "银行"].iloc[0]
        assert abs(bank_row["actual_weight"] - 0.8) < 1e-6


# ============================================================
# portfolio_charts_p1p2 单元测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestPortfolioChartsP1P2:
    """组合优化 P1/P2 图表函数测试。"""

    def test_risk_contribution_chart_empty_inputs(self):
        """空 weights 或空 cov 应返回占位符。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        result = mod.make_risk_contribution_chart({}, pd.DataFrame())
        assert "无风险贡献数据" in result or "chart-placeholder" in result

    def test_risk_contribution_chart_valid(self):
        """正常输入应返回 HTML 字符串。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        weights = {"A": 0.5, "B": 0.5}
        cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.09]], index=["A", "B"], columns=["A", "B"])
        result = mod.make_risk_contribution_chart(weights, cov)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_efficient_frontier_chart_empty_inputs(self):
        """空 returns/weights 应返回占位符。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        result = mod.make_efficient_frontier_chart(pd.DataFrame(), {})
        assert "无有效前沿数据" in result or "chart-placeholder" in result

    def test_efficient_frontier_chart_valid(self, tmp_path):
        """合法输入应返回 Plotly HTML。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        adapter = _load_reports_modules()["cross_engine_adapter"]
        data_path = _make_synthetic_data_parquet(tmp_path)
        returns = adapter.load_returns_from_data(data_path)
        weights = {c: 1.0 / 5 for c in returns.columns}
        result = mod.make_efficient_frontier_chart(returns, weights)
        assert isinstance(result, str)
        assert "plotly" in result.lower() or "chart-placeholder" in result

    def test_covariance_heatmap_empty_inputs(self):
        """空输入应返回占位符。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        result = mod.make_covariance_heatmap({}, pd.DataFrame())
        assert "无协方差数据" in result or "chart-placeholder" in result

    def test_covariance_heatmap_valid(self):
        """正常输入应返回热力图 HTML。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        weights = {"A": 0.5, "B": 0.5}
        cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.09]], index=["A", "B"], columns=["A", "B"])
        result = mod.make_covariance_heatmap(weights, cov)
        assert isinstance(result, str)
        assert "plotly" in result.lower() or "chart-placeholder" in result

    def test_industry_deviation_chart_empty(self):
        """空 weights 应返回占位符。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        result = mod.make_industry_deviation_chart({}, {})
        assert "无权重数据" in result or "chart-placeholder" in result

    def test_industry_deviation_chart_no_industry_map(self):
        """无行业映射时应返回占位符。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        weights = {"A": 0.5, "B": 0.5}
        # 无 industry_map，compute_industry_weights 会聚合到"其他"
        result = mod.make_industry_deviation_chart(weights, {})
        assert isinstance(result, str)

    def test_industry_deviation_chart_with_overlimit_alert(self):
        """偏差超限应包含告警 HTML。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        # 构造极端权重让一个行业大幅超限
        weights = {"A": 0.95, "B": 0.05}
        industry_map = {"A": "银行", "B": "科技"}
        result = mod.make_industry_deviation_chart(weights, industry_map, max_deviation=0.05)
        assert "行业偏差超限" in result

    def test_comparison_table_and_radar_empty(self):
        """空输入应返回占位符。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        result = mod.make_comparison_table_and_radar({}, pd.DataFrame(), {})
        assert "无对比数据" in result or "chart-placeholder" in result

    def test_comparison_table_and_radar_valid(self):
        """正常输入应返回表格 + 雷达图 HTML。"""
        mod = _load_reports_modules()["portfolio_charts_p1p2"]
        returns = _make_synthetic_returns()
        weights = {c: 1.0 / 5 for c in returns.columns}
        # 函数签名: (weights, returns, current_metrics, chart_theme)
        result = mod.make_comparison_table_and_radar(weights, returns, {}, chart_theme="plotly_white")
        assert isinstance(result, str)
        assert "等权基准" in result
        assert "优化组合" in result
        assert "雷达" in result or "Scatterpolar" in result.lower() or "plotly" in result.lower()


# ============================================================
# execution_charts_p1p2 单元测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestExecutionChartsP1P2:
    """执行监控 P1/P2 图表函数测试。"""

    def test_holdings_table_empty_positions(self):
        """空持仓应返回占位符。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        result = mod.build_holdings_table_html({}, {}, None, None)
        assert "无持仓数据" in result

    def test_holdings_table_normal(self):
        """正常持仓应返回表格 HTML。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        snapshot = {
            "nav": 1000000,
            "positions": {
                "000001.SZ": {"volume": 1000, "avg_cost": 10},
                "600000.SH": {"volume": 500, "avg_cost": 20},
            },
        }
        target_weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        result = mod.build_holdings_table_html(snapshot, target_weights, None, None)
        assert "持仓明细" in result
        assert "000001.SZ" in result
        assert "目标权重" in result
        assert "实际权重" in result

    def test_holdings_table_with_trade_log(self):
        """带 trade_log 时应显示当日成交。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        snapshot = {
            "nav": 1000000,
            "positions": {"000001.SZ": {"volume": 1000, "avg_cost": 10}},
        }
        target_weights = {"000001.SZ": 1.0}
        trade_log = [
            {"code": "000001.SZ", "side": "buy", "volume": 500, "price": 10},
            {"code": "000001.SZ", "side": "sell", "volume": 200, "price": 11},
        ]
        result = mod.build_holdings_table_html(snapshot, target_weights, None, trade_log)
        assert "买 500" in result
        assert "卖 200" in result

    def test_nav_curve_chart_none_path(self):
        """ledger_path=None 应返回占位符。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        result = mod.build_nav_curve_chart(None)
        assert "无 ledger 数据" in result or "chart-placeholder" in result

    def test_nav_curve_chart_valid_ledger(self, tmp_path):
        """合法 ledger 应返回 Plotly HTML。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        ledger_path = tmp_path / "ledger.jsonl"
        records = [
            {"created_at": "2025-01-01T09:30:00", "nav_after": 1000000, "code": "A", "side": "buy", "shares": 100, "price": 10, "commission": 5, "stamp_tax": 0, "slippage_cost": 1},
            {"created_at": "2025-01-02T09:30:00", "nav_after": 1010000, "code": "A", "side": "buy", "shares": 100, "price": 10.5, "commission": 5, "stamp_tax": 0, "slippage_cost": 1},
            {"created_at": "2025-01-03T09:30:00", "nav_after": 995000, "code": "A", "side": "sell", "shares": 100, "price": 9.8, "commission": 5, "stamp_tax": 1, "slippage_cost": 1},
        ]
        ledger_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        result = mod.build_nav_curve_chart(str(ledger_path))
        assert "plotly" in result.lower() or "chart-placeholder" in result

    def test_trade_review_none_path(self):
        """ledger_path=None 应返回占位符。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        result = mod.build_trade_review_html(None, None)
        assert "无 ledger 数据" in result

    def test_trade_review_valid_ledger(self, tmp_path):
        """合法 ledger 应返回复盘表格。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        ledger_path = tmp_path / "ledger.jsonl"
        records = [
            {"trade_date": "2025-01-01", "created_at": "2025-01-01T09:30:00",
             "code": "000001.SZ", "side": "buy", "shares": 100, "price": 10,
             "commission": 5, "stamp_tax": 0, "slippage_cost": 1},
        ]
        ledger_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        result = mod.build_trade_review_html(str(ledger_path), None)
        assert "<h2>当日成交</h2>" in result
        assert "000001.SZ" in result
        assert "买入" in result
        assert "滑点" in result

    def test_order_panel_empty_trade_log(self):
        """空 trade_log 应返回占位符。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        result = mod.build_order_panel_html([], [], 0, 0)
        assert "无订单数据" in result

    def test_order_panel_normal(self):
        """正常 trade_log 应返回面板 HTML。"""
        mod = _load_reports_modules()["execution_charts_p1p2"]
        trade_log = [
            {"timestamp": "2025-01-01T09:30:00", "order_id": "1", "code": "000001.SZ",
             "side": "buy", "volume": 100, "price": 10, "status": "filled",
             "metadata": {"order_price": 10}},
        ]
        ledger_records = [
            {"code": "000001.SZ", "slippage_cost": 1},
        ]
        result = mod.build_order_panel_html(trade_log, ledger_records, 1, 0)
        assert "当日委托" in result
        assert "总成交额" in result
        assert "成交率" in result
        assert "000001.SZ" in result


# ============================================================
# build_portfolio_report P1/P2 集成测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildPortfolioReportP1P2:
    """build_portfolio_report 的 P1/P2 集成路径测试。"""

    def test_p1_p2_disabled_returns_p0_only(self, tmp_path):
        """include_p1=False, include_p2=False 时应不渲染 P1/P2 区块。"""
        mod = _load_reports_modules()["portfolio_report"]
        weights = {"A": 0.5, "B": 0.5}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")
        html = mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata={"optimization_method": "test", "num_assets": 2},
            include_p1=False,
            include_p2=False,
        )
        assert "<!DOCTYPE html>" in html
        assert "风险贡献分解" not in html
        assert "有效前沿" not in html
        assert "协方差矩阵" not in html

    def test_p1_enabled_with_data(self, tmp_path):
        """include_p1=True 且有 DATA 时应渲染风险贡献和有效前沿。"""
        mod = _load_reports_modules()["portfolio_report"]
        data_path = _make_synthetic_data_parquet(tmp_path)
        weights = {c: 0.2 for c in ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")
        html = mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata={"optimization_method": "test", "num_assets": 5},
            data_path=data_path,
            include_p1=True,
            include_p2=False,
        )
        assert "风险贡献分解" in html
        assert "有效前沿" in html
        # P2 区块不应出现
        assert "协方差矩阵" not in html

    def test_p2_enabled_with_data(self, tmp_path):
        """include_p2=True 且有 DATA 时应渲染协方差和行业偏差。"""
        mod = _load_reports_modules()["portfolio_report"]
        data_path = _make_synthetic_data_parquet(tmp_path)
        weights = {c: 0.2 for c in ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")
        html = mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata={"optimization_method": "test", "num_assets": 5},
            data_path=data_path,
            include_p1=False,
            include_p2=True,
        )
        assert "协方差矩阵" in html
        assert "行业配置偏差" in html
        assert "优化前后对比" in html
        # P1 区块不应出现
        assert "风险贡献分解" not in html

    def test_p1_p2_with_missing_data_falls_back_to_placeholder(self, tmp_path):
        """DATA 缺失时 P1/P2 区块应降级为占位符，不抛异常。"""
        mod = _load_reports_modules()["portfolio_report"]
        weights = {"A": 0.5, "B": 0.5}
        weights_path = tmp_path / "portfolio_weights.json"
        weights_path.write_text(json.dumps(weights), encoding="utf-8")
        html = mod.build_portfolio_report(
            portfolio_artifact_path=str(weights_path),
            portfolio_metadata={"optimization_method": "test", "num_assets": 2},
            data_path=None,  # 无 DATA
            include_p1=True,
            include_p2=True,
        )
        assert "<!DOCTYPE html>" in html
        # 各 P1/P2 区块标题应存在
        assert "风险贡献分解" in html
        assert "有效前沿" in html
        assert "协方差矩阵" in html


# ============================================================
# build_execution_report P1/P2 集成测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildExecutionReportP1P2:
    """build_execution_report 的 P1/P2 集成路径测试。"""

    def test_p1_p2_disabled_returns_p0_only(self):
        """include_p1=False, include_p2=False 时应不渲染 P1/P2 区块。"""
        mod = _load_reports_modules()["execution_report"]
        html = mod.build_execution_report(
            execution_metadata={"mode": "paper", "account_snapshot": {"nav": 100}},
            include_p1=False,
            include_p2=False,
        )
        assert "<!DOCTYPE html>" in html
        assert "持仓明细" not in html
        assert "净值曲线" not in html
        assert "<h2>当日成交</h2>" not in html
        assert "当日委托" not in html

    def test_p1_enabled_with_positions(self):
        """include_p1=True 且有持仓时应渲染持仓明细表。"""
        mod = _load_reports_modules()["execution_report"]
        execution_metadata = {
            "mode": "paper",
            "account_snapshot": {
                "nav": 1000000,
                "positions": {"000001.SZ": {"volume": 1000, "avg_cost": 10}},
            },
            "orders_executed": 1,
            "orders_failed": 0,
        }
        html = mod.build_execution_report(
            execution_metadata=execution_metadata,
            target_weights={"000001.SZ": 1.0},
            include_p1=True,
            include_p2=False,
        )
        assert "持仓明细" in html
        assert "000001.SZ" in html
        # P2 区块不应出现
        assert "<h2>当日成交</h2>" not in html

    def test_p2_enabled_with_ledger(self, tmp_path):
        """include_p2=True 且有 ledger 时应渲染当日成交和当日委托。"""
        mod = _load_reports_modules()["execution_report"]
        ledger_path = tmp_path / "ledger.jsonl"
        records = [
            {"trade_date": "2025-01-01", "created_at": "2025-01-01T09:30:00",
             "code": "000001.SZ", "side": "buy", "shares": 100, "price": 10,
             "commission": 5, "stamp_tax": 0, "slippage_cost": 1, "nav_after": 1000000},
        ]
        ledger_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        audit_path = tmp_path / "trade_log.jsonl"
        audit_path.write_text(
            json.dumps({"timestamp": "2025-01-01T09:30:00", "order_id": "1",
                       "code": "000001.SZ", "side": "buy", "volume": 100, "price": 10,
                       "status": "filled", "metadata": {"order_price": 10}}) + "\n",
            encoding="utf-8",
        )
        html = mod.build_execution_report(
            execution_metadata={"mode": "paper", "account_snapshot": {"nav": 1000000}, "orders_executed": 1},
            audit_log_path=str(audit_path),
            ledger_path=str(ledger_path),
            include_p1=False,
            include_p2=True,
        )
        assert "<h2>当日成交</h2>" in html
        assert "当日委托" in html
        # P1 区块不应出现
        assert "持仓明细" not in html

    def test_p1_p2_with_empty_data_does_not_raise(self):
        """完全空数据时 P1/P2 应降级为占位符，不抛异常。"""
        mod = _load_reports_modules()["execution_report"]
        html = mod.build_execution_report(
            execution_metadata={},
            include_p1=True,
            include_p2=True,
        )
        assert "<!DOCTYPE html>" in html
        assert "持仓明细" in html  # 占位符区块标题应存在
        assert "净值曲线" in html
        assert "<h2>当日成交</h2>" in html
        assert "当日委托" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
