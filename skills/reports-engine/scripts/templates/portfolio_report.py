"""组合优化报告模板（F-P0-2 权重分布图）。

生成组合优化引擎的可视化 HTML 报告，包含：
1. 权重分布环形图（Plotly，按行业分组着色）
2. 权重偏差双向条形图（纯 SVG，超配红/低配绿）
3. 优化指标卡片
4. 约束检查结果
5. VaR/CVaR 风险指标

数据来源：
- portfolio-risk-engine run() 返回的 metadata
- portfolio_weights.json（artifact_path 落盘文件）
- FACTOR 产物的 industry 列（可选，用于行业着色）

数据缺失容错：
- FACTOR 产物缺 industry 列时，环形图退化为单色扇区
- 任一上游字段缺失时显示「数据不可用」占位，不阻塞生成
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import pandas as pd

import plotly.graph_objects as go

logger = logging.getLogger("reports-engine.portfolio_template")

# 行业配色板（按行业分组着色）
INDUSTRY_COLORS = [
    "#3b82f6", "#ef4444", "#10b981", "#f59e0b",
    "#8b5cf6", "#ec4899", "#06b6d4", "#84cc16",
    "#f97316", "#6366f1", "#14b8a6", "#adfa1b",
]


def load_industry_map(factor_path: Optional[str]) -> Dict[str, str]:
    """从 FACTOR 产物加载 {code: industry} 映射。

    数据缺失容错：路径不存在或无 industry 列时返回空 dict。
    """
    if not factor_path or not os.path.exists(factor_path):
        return {}
    try:
        df = pd.read_parquet(factor_path)
        if "industry" not in df.columns or "code" not in df.columns:
            return {}
        latest = df[df["date"] == df["date"].max()]
        return dict(zip(latest["code"], latest["industry"]))
    except Exception as e:
        logger.warning(f"加载行业映射失败（退化为单色）: {e}")
        return {}


def make_weight_pie_chart(
    weights: Dict[str, float],
    industry_map: Dict[str, str],
    optimization_method: str = "",
    num_assets: int = 0,
    chart_theme: str = "plotly_white",
) -> str:
    """生成权重分布环形图（Plotly）。

    每资产一个扇区，按行业分组着色；中心显示资产数量 + 优化方法。
    """
    if not weights:
        return '<div class="chart-placeholder">无权重数据</div>'

    codes = list(weights.keys())
    values = [weights[c] for c in codes]
    industries = [industry_map.get(c, "其他") for c in codes]

    # 按行业分组分配颜色
    unique_industries = sorted(set(industries))
    color_map = {
        ind: INDUSTRY_COLORS[i % len(INDUSTRY_COLORS)]
        for i, ind in enumerate(unique_industries)
    }
    colors = [color_map[ind] for ind in industries]

    # 悬停文本
    hover_text = [
        f"{code}<br>权重: {w * 100:.2f}%<br>行业: {ind}"
        for code, w, ind in zip(codes, values, industries)
    ]

    fig = go.Figure(data=[go.Pie(
        labels=codes,
        values=values,
        hole=0.55,
        marker=dict(colors=colors),
        hovertext=hover_text,
        hoverinfo="text",
        textinfo="label+percent",
        textposition="outside",
    )])

    # 中心注释
    center_text = f"{num_assets} 资产<br>{optimization_method}"
    fig.add_annotation(
        text=center_text,
        x=0.5, y=0.5,
        font=dict(size=14, color="#1f2937"),
        showarrow=False,
    )

    fig.update_layout(
        title="权重分布（按行业着色）",
        height=450,
        template=chart_theme,
        showlegend=True,
        legend=dict(orientation="h", y=-0.1),
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def build_portfolio_html(
    weights: Dict[str, float],
    metadata: Dict[str, Any],
    industry_map: Dict[str, str],
    weight_deviation_svg: str,
    pie_chart_html: str,
    chart_theme: str = "plotly_white",
    p1p2_charts_html: str = "",
) -> str:
    """构建组合优化报告完整 HTML。

    参数:
        p1p2_charts_html: P1/P2 扩展图表 HTML 片段（风险贡献、有效前沿、协方差等），
                         为空字符串时不渲染该区块。
    """
    from datetime import datetime
    from scripts.renderers.svg_components import COLORS, render_metric_card
    from scripts.templates.common_components import (
        build_page_css, build_nav_bar_html, build_footer_html,
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 优化指标卡片
    metrics = metadata.get("metrics", {}) or {}
    optimization_method = metadata.get("optimization_method", "unknown")
    num_assets = metadata.get("num_assets", len(weights))

    cards_html = []
    if metrics:
        expected_ret = metrics.get("expected_return")
        volatility = metrics.get("volatility")
        sharpe = metrics.get("sharpe_ratio")
        if expected_ret is not None:
            cards_html.append(render_metric_card(
                "预期收益", f"{expected_ret * 100:.2f}%",
                color=COLORS["up"] if expected_ret >= 0 else COLORS["down"],
            ))
        if volatility is not None:
            cards_html.append(render_metric_card(
                "波动率", f"{volatility * 100:.2f}%",
                color=COLORS["warning"],
            ))
        if sharpe is not None:
            cards_html.append(render_metric_card(
                "夏普比率", f"{sharpe:.3f}",
                color=COLORS["success"] if sharpe >= 0 else COLORS["danger"],
            ))
        if "actual_turnover" in metrics:
            cards_html.append(render_metric_card(
                "实际换手率", f"{metrics['actual_turnover'] * 100:.2f}%",
            ))

    # VaR/CVaR 风险指标
    var_cvar = metadata.get("var_cvar", {}) or {}
    var_cvar_html = ""
    if var_cvar:
        var_val = var_cvar.get("VaR", 0)
        cvar_val = var_cvar.get("CVaR", 0)
        confidence = var_cvar.get("confidence", 0.95)
        var_cvar_html = f"""
        <div class="section">
            <h2>风险指标（VaR/CVaR）</h2>
            <div class="metrics-grid">
                {render_metric_card("VaR", f"{var_val * 100:.2f}%", f"置信度 {confidence * 100:.0f}%", color=COLORS["warning"])}
                {render_metric_card("CVaR", f"{cvar_val * 100:.2f}%", f"Expected Shortfall", color=COLORS["danger"])}
            </div>
        </div>
        """
    else:
        var_cvar_html = '<div class="section"><h2>风险指标（VaR/CVaR）</h2><div class="placeholder">数据不可用</div></div>'

    # 约束检查结果
    constraint_check = metadata.get("constraint_check", {}) or {}
    constraint_rows = ""
    if constraint_check:
        for key, val in constraint_check.items():
            label_map = {
                "max_single_weight": "单标的权重上限",
                "weights_sum_one": "权重和为1",
            }
            label = label_map.get(key, key)
            status = "✓ 满足" if val else "✗ 违反"
            cls = "positive" if val else "negative"
            constraint_rows += f'<tr><td>{label}</td><td class="{cls}">{status}</td></tr>'
    else:
        constraint_rows = '<tr><td colspan="2">无约束检查数据</td></tr>'

    # 止损信号（来自 portfolio-risk-engine 的 stop_signals）
    stop_signals = metadata.get("stop_signals", {}) or {}
    stop_signals_html = ""
    if stop_signals:
        portfolio_stop = stop_signals.get("portfolio_stop", {}) or {}
        individual_stops = stop_signals.get("individual_stops", {}) or {}
        any_triggered = stop_signals.get("any_triggered", False)

        stop_rows = ""
        if portfolio_stop:
            triggered = portfolio_stop.get("triggered", False)
            daily_ret = portfolio_stop.get("daily_return", 0)
            threshold = portfolio_stop.get("threshold", 0)
            reason = portfolio_stop.get("reason", "")
            status = "✓ 触发" if triggered else "○ 未触发"
            cls = "negative" if triggered else "positive"
            stop_rows += (
                f'<tr><td>组合日亏损止损</td><td class="{cls}">{status}</td>'
                f'<td>{daily_ret * 100:.2f}%</td>'
                f'<td>{threshold * 100:.2f}%</td>'
                f'<td>{reason or "-"}</td></tr>'
            )

        triggered_codes = [c for c, t in individual_stops.items() if t]
        if triggered_codes:
            stop_rows += (
                f'<tr><td>个股止损触发</td><td class="negative">{len(triggered_codes)} 只</td>'
                f'<td colspan="3">{", ".join(triggered_codes[:10])}'
                f'{"..." if len(triggered_codes) > 10 else ""}</td></tr>'
            )
        elif individual_stops:
            stop_rows += '<tr><td>个股止损</td><td class="positive">○ 无触发</td><td colspan="3">-</td></tr>'

        alert_cls = "alert-danger" if any_triggered else "alert-ok"
        stop_signals_html = f"""
        <div class="section">
            <h2>止损信号</h2>
            <div class="alert {alert_cls}">
                {'⚠ 存在触发的止损信号' if any_triggered else '✓ 无止损信号触发'}
            </div>
            <table>
                <thead><tr><th>类型</th><th>状态</th><th>当前值</th><th>阈值</th><th>说明</th></tr></thead>
                <tbody>{stop_rows}</tbody>
            </table>
        </div>
        """
    else:
        stop_signals_html = '<div class="section"><h2>止损信号</h2><div class="placeholder">数据不可用</div></div>'

    cards_html_str = "".join(cards_html) if cards_html else '<div class="placeholder">无优化指标数据</div>'

    page_css = build_page_css()
    nav_html = build_nav_bar_html()
    footer_html = build_footer_html(disclaimer=(
        "本报告由 JingniTrader 基于历史行情、风险模型与优化参数自动生成，仅供学习研究用途，不构成任何投资建议。"
        "组合权重为模型在既定收益预期、风险预算与约束条件下的求解结果，基于历史数据与参数假设，"
        "实际市场相关性、波动率与收益预期均可能变化，模型输出对参数较为敏感。"
        "优化权重不代表未来最优配置，不构成买卖或仓位指令，实盘交易有风险，请谨慎决策。"
    ))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>组合优化报告</title>
<style>
    /* 统一页面布局 + 导航栏 + 底部版权栏（与其余报告一致） */
    {page_css}
    /* ── 组合优化报告特有样式 ── */
    .svg-container {{ width: 100%; overflow-x: auto; padding: 8px 0; }}
    .placeholder {{ padding: 32px; text-align: center; color: var(--text-muted);
                   background: #f9fafb; border-radius: 8px; font-size: 13px; }}
    tr:hover {{ background: #fafafa; }}
    .positive {{ color: var(--success); }}
    .negative {{ color: var(--danger); }}
    .alert {{ padding: 12px 16px; border-radius: 6px; margin-bottom: 12px; font-size: 14px; font-weight: 500; }}
    .alert-danger {{ background: #fee2e2; color: var(--danger); border-left: 3px solid var(--danger); }}
    .alert-ok {{ background: #d1fae5; color: var(--success); border-left: 3px solid var(--success); }}
</style>
</head>
<body>
{nav_html}

<div class="header">
    <h1>组合优化报告</h1>
    <p>生成时间: {now_str}</p>
</div>

<div class="section">
    <h2>优化指标</h2>
    <div class="metrics-grid">{cards_html_str}</div>
</div>

<div class="section">
    <h2>权重分布</h2>
    <div class="chart-container">{pie_chart_html}</div>
</div>

<div class="section">
    <h2>优化对比</h2>
    <div class="svg-container">{weight_deviation_svg}</div>
</div>

{p1p2_charts_html}

{var_cvar_html}

<div class="section">
    <h2>约束检查</h2>
    <table>
        <thead><tr><th>约束项</th><th>状态</th></tr></thead>
        <tbody>{constraint_rows}</tbody>
    </table>
</div>

{stop_signals_html}

{footer_html}
</body>
</html>"""


def build_portfolio_report(
    portfolio_artifact_path: str,
    portfolio_metadata: Dict[str, Any],
    factor_path: Optional[str] = None,
    data_path: Optional[str] = None,
    chart_theme: str = "plotly_white",
    include_p1: bool = True,
    include_p2: bool = True,
) -> str:
    """构建组合优化报告主入口。

    参数:
        portfolio_artifact_path: portfolio_weights.json 路径
        portfolio_metadata: portfolio-risk-engine run() 返回的 metadata
        factor_path: FACTOR 产物路径（可选，用于行业着色）
        data_path: DATA 产物路径（可选，P1/P2 重算协方差/有效前沿所需）
        chart_theme: Plotly 主题
        include_p1: 是否包含 P1 功能（风险贡献、有效前沿）
        include_p2: 是否包含 P2 功能（协方差热力图、行业偏差、优化前后对比）

    返回:
        完整 HTML 字符串
    """
    from scripts.renderers.svg_components import render_weight_deviation_bar
    from scripts.cross_engine_adapter import load_returns_from_data, compute_cov_matrix

    # 加载权重
    weights = {}
    if portfolio_artifact_path and os.path.exists(portfolio_artifact_path):
        try:
            with open(portfolio_artifact_path, "r", encoding="utf-8") as f:
                weights = json.load(f)
        except Exception as e:
            logger.warning(f"加载权重文件失败: {e}")
    else:
        weights = portfolio_metadata.get("weights", {}) or {}

    # 加载行业映射（数据缺失容错）
    industry_map = load_industry_map(factor_path)
    if not industry_map:
        logger.info("FACTOR 产物缺 industry 列，环形图退化为单色扇区")

    # 生成 P0 图表
    optimization_method = portfolio_metadata.get("optimization_method", "unknown")
    num_assets = portfolio_metadata.get("num_assets", len(weights))

    pie_chart_html = make_weight_pie_chart(
        weights=weights,
        industry_map=industry_map,
        optimization_method=optimization_method,
        num_assets=num_assets,
        chart_theme=chart_theme,
    )

    weight_deviation_svg = render_weight_deviation_bar(weights=weights)

    # P1/P2 扩展图表（通过薄包装重算，数据缺失时降级为占位符）
    p1p2_charts_html = ""
    if include_p1 or include_p2:
        returns_df = load_returns_from_data(data_path)
        cov_matrix = compute_cov_matrix(returns_df) if not returns_df.empty else pd.DataFrame()

        if include_p1:
            # F-P1-2: 风险贡献分解
            try:
                from scripts.templates.portfolio_charts_p1p2 import make_risk_contribution_chart
                rc_html = make_risk_contribution_chart(weights, cov_matrix, chart_theme)
                p1p2_charts_html += f'<div class="section"><h2>风险贡献分解</h2><div class="chart-container">{rc_html}</div></div>'
            except Exception as e:
                logger.warning(f"F-P1-2 风险贡献分解生成失败: {e}")
                p1p2_charts_html += '<div class="section"><h2>风险贡献分解</h2><div class="placeholder">生成失败</div></div>'

            # F-P1-3: 有效前沿
            try:
                from scripts.templates.portfolio_charts_p1p2 import make_efficient_frontier_chart
                ef_html = make_efficient_frontier_chart(returns_df, weights, chart_theme)
                p1p2_charts_html += f'<div class="section"><h2>有效前沿</h2><div class="chart-container">{ef_html}</div></div>'
            except Exception as e:
                logger.warning(f"F-P1-3 有效前沿生成失败: {e}")
                p1p2_charts_html += '<div class="section"><h2>有效前沿</h2><div class="placeholder">生成失败</div></div>'

        if include_p2:
            # F-P2-1: 协方差热力图
            try:
                from scripts.templates.portfolio_charts_p1p2 import make_covariance_heatmap
                cov_html = make_covariance_heatmap(weights, cov_matrix, chart_theme)
                p1p2_charts_html += f'<div class="section"><h2>协方差矩阵</h2><div class="chart-container">{cov_html}</div></div>'
            except Exception as e:
                logger.warning(f"F-P2-1 协方差热力图生成失败: {e}")
                p1p2_charts_html += '<div class="section"><h2>协方差矩阵</h2><div class="placeholder">生成失败</div></div>'

            # F-P2-2: 行业配置偏差
            try:
                from scripts.templates.portfolio_charts_p1p2 import make_industry_deviation_chart
                ind_html = make_industry_deviation_chart(weights, industry_map)
                p1p2_charts_html += f'<div class="section"><h2>行业配置偏差</h2><div class="svg-container">{ind_html}</div></div>'
            except Exception as e:
                logger.warning(f"F-P2-2 行业偏差生成失败: {e}")
                p1p2_charts_html += '<div class="section"><h2>行业配置偏差</h2><div class="placeholder">生成失败</div></div>'

            # F-P2-3: 优化前后对比
            try:
                from scripts.templates.portfolio_charts_p1p2 import make_comparison_table_and_radar
                cmp_html = make_comparison_table_and_radar(
                    weights, returns_df,
                    portfolio_metadata.get("metrics", {}),
                    chart_theme,
                )
                p1p2_charts_html += f'<div class="section"><h2>优化前后对比</h2>{cmp_html}</div>'
            except Exception as e:
                logger.warning(f"F-P2-3 优化前后对比生成失败: {e}")
                p1p2_charts_html += '<div class="section"><h2>优化前后对比</h2><div class="placeholder">生成失败</div></div>'

    return build_portfolio_html(
        weights=weights,
        metadata=portfolio_metadata,
        industry_map=industry_map,
        weight_deviation_svg=weight_deviation_svg,
        pie_chart_html=pie_chart_html,
        chart_theme=chart_theme,
        p1p2_charts_html=p1p2_charts_html,
    )
