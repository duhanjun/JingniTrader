"""组合优化报告 P1/P2 扩展图表模块。

实现 PRD 中组合优化的 P1/P2 功能：
- F-P1-2: 风险贡献分解图（柱状图 + 散点图 + 集中度环形图）
- F-P1-3: 有效前沿可视化（散点 + 曲线 + 关键点标记）
- F-P2-1: 协方差矩阵热力图
- F-P2-2: 行业配置偏差图（SVG 双向条形图）
- F-P2-3: 优化前后对比（表格 + 雷达图）

数据来源：通过 cross_engine_adapter 从 DATA 产物重算（薄包装动态加载）。
数据缺失容错：上游不可用时返回占位符，不阻塞报告生成。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger("reports-engine.portfolio_charts_p1p2")


def make_risk_contribution_chart(
    weights: Dict[str, float],
    cov_matrix: pd.DataFrame,
    chart_theme: str = "plotly_white",
) -> str:
    """F-P1-2: 风险贡献分解图。

    包含：
    - 风险贡献柱状图（per-asset 边际贡献占比）
    - 风险贡献 vs 权重散点图（识别高权重低贡献资产）
    - 风险集中度指标卡（前3大持仓风险贡献占比）
    - 分散化比率环形图（1 - HHI）
    """
    from scripts.cross_engine_adapter import compute_risk_contributions
    from scripts.renderers.svg_components import render_ring_chart

    if not weights or cov_matrix.empty:
        return '<div class="chart-placeholder">无风险贡献数据（需 DATA 产物重算协方差）</div>'

    rc = compute_risk_contributions(weights, cov_matrix)
    if rc.empty:
        return '<div class="chart-placeholder">风险贡献计算失败</div>'

    # 柱状图 + 散点图（双子图）
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("风险贡献占比", "风险贡献 vs 权重"),
        horizontal_spacing=0.15,
    )

    # 柱状图
    fig.add_trace(
        go.Bar(
            x=rc.index,
            y=rc.values * 100,
            marker_color=["#ef4444" if v > 1.0 / len(rc) else "#3b82f6" for v in rc.values],
            text=[f"{v * 100:.2f}%" for v in rc.values],
            textposition="outside",
            name="风险贡献",
        ),
        row=1, col=1,
    )
    fig.update_xaxes(title_text="标的", row=1, col=1)
    fig.update_yaxes(title_text="风险贡献占比 (%)", row=1, col=1)

    # 散点图
    w_series = pd.Series(weights)
    common = rc.index.intersection(w_series.index)
    fig.add_trace(
        go.Scatter(
            x=[w_series[c] * 100 for c in common],
            y=[rc[c] * 100 for c in common],
            mode="markers+text",
            marker=dict(size=10, color="#8b5cf6"),
            text=list(common),
            textposition="top center",
            name="资产",
        ),
        row=1, col=2,
    )
    fig.update_xaxes(title_text="权重占比 (%)", row=1, col=2)
    fig.update_yaxes(title_text="风险贡献占比 (%)", row=1, col=2)

    fig.update_layout(
        height=400, template=chart_theme, showlegend=False,
        title_text="风险贡献分解",
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")

    # 风险集中度：前3大持仓风险贡献占比
    top3_rc = float(rc.nlargest(3).sum())
    concentration_ring = render_ring_chart(
        value=top3_rc, threshold=1.0,
        label=f"{top3_rc * 100:.1f}%", sublabel="前3集中度",
        color_override="#ef4444" if top3_rc > 0.5 else "#10b981",
    )

    # 分散化比率：1 - HHI
    w_arr = np.array(list(weights.values()))
    hhi = float((w_arr ** 2).sum())
    diversification = max(0, 1 - hhi)
    div_ring = render_ring_chart(
        value=diversification, threshold=1.0,
        label=f"{diversification * 100:.1f}%", sublabel="分散化比率",
        color_override="#10b981" if diversification > 0.5 else "#f59e0b",
    )

    rings_html = (
        f'<div style="display:flex;gap:24px;justify-content:center;margin-top:16px;">'
        f'<div style="text-align:center;">{concentration_ring}</div>'
        f'<div style="text-align:center;">{div_ring}</div>'
        f'</div>'
    )

    return chart_html + rings_html


def make_efficient_frontier_chart(
    returns: pd.DataFrame,
    weights: Dict[str, float],
    chart_theme: str = "plotly_white",
) -> str:
    """F-P1-3: 有效前沿可视化。

    包含：
    - 随机/采样组合散点（灰色）
    - 有效前沿曲线（黄色高亮）
    - 关键点标记：当前组合（红星）、等权、最大Sharpe、最小方差
    - 资本市场线（CML）
    """
    from scripts.cross_engine_adapter import compute_efficient_frontier_points

    if returns.empty or not weights:
        return '<div class="chart-placeholder">无有效前沿数据（需 DATA 产物重算）</div>'

    points_df = compute_efficient_frontier_points(returns, weights)
    if points_df.empty:
        return '<div class="chart-placeholder">有效前沿采样失败</div>'

    fig = go.Figure()

    # 采样点散点
    mask_no_label = ~points_df.index.isin(points_df[points_df.get("label", "") != ""].index)
    if "label" in points_df.columns:
        scatter_df = points_df[points_df["label"] == ""]
    else:
        scatter_df = points_df

    if not scatter_df.empty:
        fig.add_trace(go.Scatter(
            x=scatter_df["volatility"] * 100,
            y=scatter_df["return"] * 100,
            mode="markers",
            marker=dict(size=5, color=scatter_df["sharpe"], colorscale="Viridis", showscale=True,
                        colorbar=dict(title="Sharpe")),
            name="模拟组合",
            hovertext=[f"波动率: {v * 100:.2f}%<br>收益: {r * 100:.2f}%<br>Sharpe: {s:.3f}"
                       for v, r, s in zip(scatter_df["volatility"], scatter_df["return"], scatter_df["sharpe"])],
        ))

    # 关键点标记
    if "label" in points_df.columns:
        labeled = points_df[points_df["label"] != ""]
        marker_config = {
            "当前组合": ("star", "#ef4444", 18),
            "等权": ("circle", "#3b82f6", 14),
            "最大Sharpe": ("diamond", "#f59e0b", 14),
            "最小方差": ("triangle-up", "#10b981", 14),
        }
        for _, row in labeled.iterrows():
            label = row["label"]
            symbol, color, size = marker_config.get(label, ("circle", "#6b7280", 10))
            fig.add_trace(go.Scatter(
                x=[row["volatility"] * 100],
                y=[row["return"] * 100],
                mode="markers+text",
                marker=dict(symbol=symbol, size=size, color=color),
                text=[label],
                textposition="top center",
                name=label,
                showlegend=False,
            ))

    fig.update_layout(
        title="有效前沿",
        xaxis_title="年化波动率 (%)",
        yaxis_title="年化收益 (%)",
        height=500,
        template=chart_theme,
        showlegend=True,
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def make_covariance_heatmap(
    weights: Dict[str, float],
    cov_matrix: pd.DataFrame,
    chart_theme: str = "plotly_white",
) -> str:
    """F-P2-1: 协方差矩阵热力图。

    色阶：绿（负相关）→ 白（0）→ 红（正相关）
    仅展示当前持仓的协方差矩阵。
    """
    if not weights or cov_matrix.empty:
        return '<div class="chart-placeholder">无协方差数据（需 DATA 产物重算）</div>'

    codes = [c for c in weights.keys() if c in cov_matrix.index]
    if not codes:
        return '<div class="chart-placeholder">协方差矩阵与权重不匹配</div>'

    sub_cov = cov_matrix.loc[codes, codes]

    # 转为相关系数矩阵便于展示
    std = np.sqrt(np.diag(sub_cov.values))
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = sub_cov.values / np.outer(std, std)
    corr = np.nan_to_num(corr, nan=0.0)

    fig = go.Figure(data=go.Heatmap(
        z=corr,
        x=codes,
        y=codes,
        colorscale=[[0, "#12a05c"], [0.5, "#ffffff"], [1, "#e02b2b"]],
        zmin=-1, zmax=1,
        hovertemplate="%{y} × %{x}<br>相关系数: %{z:.3f}<extra></extra>",
    ))

    fig.update_layout(
        title="持仓相关系数矩阵",
        height=max(400, len(codes) * 30 + 100),
        template=chart_theme,
        xaxis=dict(tickangle=-45),
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def make_industry_deviation_chart(
    weights: Dict[str, float],
    industry_map: Dict[str, str],
    max_deviation: float = 0.05,
) -> str:
    """F-P2-2: 行业配置偏差图（SVG 双向条形图）。

    复用 svg_components.render_weight_deviation_bar。
    基准：等权基准 = 1/行业数（简化方案）。
    超限行业（偏差 > max_deviation）红色高亮。
    """
    from scripts.cross_engine_adapter import compute_industry_weights
    from scripts.renderers.svg_components import COLORS, render_weight_deviation_bar

    if not weights:
        return '<div class="chart-placeholder">无权重数据</div>'

    ind_df = compute_industry_weights(weights, industry_map)
    if ind_df.empty:
        return '<div class="chart-placeholder">无行业数据（需 FACTOR 产物 industry 列）</div>'

    # 构造权重字典和基准字典
    actual = dict(zip(ind_df["industry"], ind_df["actual_weight"]))
    benchmark = dict(zip(ind_df["industry"], ind_df["benchmark_weight"]))

    svg = render_weight_deviation_bar(actual, benchmark=benchmark)

    # 超限提示
    over_limit = ind_df[abs(ind_df["deviation"]) > max_deviation]
    alert_html = ""
    if not over_limit.empty:
        over_text = ", ".join(f"{r['industry']} ({r['deviation'] * 100:+.2f}%)" for _, r in over_limit.iterrows())
        alert_html = (
            f'<div style="background:#fee2e2;border-left:3px solid {COLORS["danger"]};'
            f'padding:10px 14px;border-radius:6px;margin-top:12px;font-size:13px;color:{COLORS["danger"]};">'
            f'⚠ 行业偏差超限（阈值 ±{max_deviation * 100}%）: {over_text}</div>'
        )

    return svg + alert_html


def make_comparison_table_and_radar(
    weights: Dict[str, float],
    returns: pd.DataFrame,
    current_metrics: Dict[str, float],
    chart_theme: str = "plotly_white",
) -> str:
    """F-P2-3: 优化前后对比（表格 + 雷达图）。

    对比维度：预期收益、波动率、Sharpe、最大回撤、分散化程度、换手率
    雷达图维度：收益、风险、分散化、流动性、换手率、集中度
    """
    from scripts.cross_engine_adapter import compute_current_metrics, compute_equal_weight_metrics

    if not weights or returns.empty:
        return '<div class="chart-placeholder">无对比数据（需 DATA 产物重算）</div>'

    # 当前组合指标（若 metadata 缺失则重算）
    cur_metrics = current_metrics or compute_current_metrics(returns, weights)
    eq_metrics = compute_equal_weight_metrics(returns, weights)

    if not cur_metrics or not eq_metrics:
        return '<div class="chart-placeholder">指标计算失败</div>'

    # 对比表格
    dimensions = [
        ("预期收益", "expected_return", True, True),   # (标签, key, 是否百分比, 越大越好)
        ("波动率", "volatility", True, False),
        ("夏普比率", "sharpe_ratio", False, True),
        ("最大回撤", "max_drawdown", True, False),
        ("分散化程度", "diversification", True, True),
        ("换手率", "turnover", True, False),
    ]

    table_rows = ""
    for label, key, is_pct, _ in dimensions:
        cur_val = cur_metrics.get(key, 0)
        eq_val = eq_metrics.get(key, 0)
        if is_pct:
            cur_str = f"{cur_val * 100:.2f}%"
            eq_str = f"{eq_val * 100:.2f}%"
        else:
            cur_str = f"{cur_val:.3f}"
            eq_str = f"{eq_val:.3f}"
        diff = cur_val - eq_val
        diff_str = f"{diff * 100:+.2f}%" if is_pct else f"{diff:+.3f}"
        diff_cls = "positive" if diff >= 0 else "negative"
        table_rows += (
            f'<tr><td>{label}</td><td>{eq_str}</td><td>{cur_str}</td>'
            f'<td class="{diff_cls}">{diff_str}</td></tr>'
        )

    table_html = f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:12px;">
        <thead><tr style="background:#f9f9f9;">
            <th style="padding:10px;text-align:left;">指标</th>
            <th style="padding:10px;text-align:left;">等权基准</th>
            <th style="padding:10px;text-align:left;">优化组合</th>
            <th style="padding:10px;text-align:left;">差异</th>
        </tr></thead>
        <tbody>{table_rows}</tbody>
    </table>
    """

    # 雷达图（归一化到 0-1）
    radar_dims = ["收益", "风险调整", "分散化", "低集中度", "低换手", "流动性"]
    # 简化：用已有指标映射
    def _norm(val, max_val, inverse=False):
        v = min(1, max(0, abs(val) / max_val)) if max_val > 0 else 0
        return 1 - v if inverse else v

    cur_radar = [
        _norm(cur_metrics.get("expected_return", 0), 0.3),
        _norm(cur_metrics.get("sharpe_ratio", 0), 2),
        _norm(cur_metrics.get("diversification", 0), 1),
        _norm(cur_metrics.get("diversification", 0), 1),  # 低集中度 = 高分散化
        1.0,  # 低换手（简化）
        1.0,  # 流动性（简化）
    ]
    eq_radar = [
        _norm(eq_metrics.get("expected_return", 0), 0.3),
        _norm(eq_metrics.get("sharpe_ratio", 0), 2),
        _norm(eq_metrics.get("diversification", 0), 1),
        _norm(eq_metrics.get("diversification", 0), 1),
        1.0,
        1.0,
    ]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=cur_radar + [cur_radar[0]],
        theta=radar_dims + [radar_dims[0]],
        fill="toself",
        name="优化组合",
        line=dict(color="#3b82f6"),
    ))
    fig.add_trace(go.Scatterpolar(
        r=eq_radar + [eq_radar[0]],
        theta=radar_dims + [radar_dims[0]],
        fill="toself",
        name="等权基准",
        line=dict(color="#9ca3af"),
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        height=400,
        template=chart_theme,
        showlegend=True,
        title="多维对比雷达图",
    )

    radar_html = fig.to_html(full_html=False, include_plotlyjs="cdn")

    return table_html + radar_html
