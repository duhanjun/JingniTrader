"""
绩效归因与可视化报告引擎主逻辑
整合全流程产物，生成 HTML 报告 + JSON 数据
"""
import os
import sys
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from scripts.config import (
    REPORT_DIR, REPORT_TITLE,
    INDUSTRY_STANDARD, BENCHMARK, RISK_FREE_RATE,
    INCLUDE_HEATMAP,
    INCLUDE_ATTRIBUTION, CHART_THEME, ENABLE_PLUGIN
)
from scripts.templates.common_components import (
    build_page_css, build_nav_bar_css, build_nav_bar_html, build_footer_html,
    render_page,
)

# 报告插件框架（懒加载避免循环依赖）
_PLUGIN_LOADED = False


def _ensure_plugins_loaded() -> None:
    """确保插件已扫描注册（懒加载，仅一次）。"""
    global _PLUGIN_LOADED
    if _PLUGIN_LOADED:
        return
    try:
        from plugins.loader import scan as _plugin_scan
        plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
        _plugin_scan(plugins_dir)
        _PLUGIN_LOADED = True
    except Exception as e:  # 插件框架异常不影响主流程
        logger.warning(f"报告插件加载异常（已跳过）: {e}")


logger = logging.getLogger("reports-engine")


class ReportGenerator:
    """报告生成器"""

    def __init__(self, title: str = REPORT_TITLE):
        self.title = title
        self.charts: List[str] = []
        self.metrics: Dict[str, Any] = {}
        self.factor_summary_html: str = ""  # M4: 策略因子清单章节（可空）

    def calc_performance_metrics(
        self,
        equity_curve: pd.DataFrame,
        risk_free_rate: float = RISK_FREE_RATE,
    ) -> Dict[str, float]:
        """计算全面绩效指标"""
        if equity_curve.empty or 'equity' not in equity_curve.columns:
            return {}

        eq = equity_curve.set_index('date')['equity']
        if len(eq) < 2:
            logger.warning("净值数据不足，无法计算绩效")
            return {}

        returns = eq.pct_change().dropna()
        if len(returns) < 2:
            return {}

        cumulative = (1 + returns).cumprod()
        total_return = float(cumulative.iloc[-1] - 1)
        n_days = len(returns)
        annual_return = float((1 + total_return) ** (252 / n_days) - 1)
        volatility = float(returns.std() * np.sqrt(252))
        max_drawdown = float((eq / eq.cummax() - 1).min())
        sharpe = float((annual_return - risk_free_rate) / volatility) if volatility > 0 else 0
        calmar = float(annual_return / abs(max_drawdown)) if max_drawdown != 0 else 0
        win_rate = float((returns > 0).mean())
        daily_var_95 = float(np.percentile(returns, 5))
        sortino_ratio = float(
            (annual_return - risk_free_rate) /
            (returns[returns < 0].std() * np.sqrt(252))
            if len(returns[returns < 0]) > 0 else 0
        )

        # 盈亏比（Profit Factor）= 总盈利 / 总亏损（量化 quantstats 维度）
        gross_profit = float(returns[returns > 0].sum())
        gross_loss = float(-returns[returns < 0].sum())
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        # 回测区间（起止日期 + 交易日数）
        eq_idx = eq.index
        backtest_start = str(pd.Timestamp(eq_idx[0]).strftime("%Y-%m-%d")) if len(eq_idx) > 0 else ""
        backtest_end = str(pd.Timestamp(eq_idx[-1]).strftime("%Y-%m-%d")) if len(eq_idx) > 0 else ""
        calendar_days = int((pd.Timestamp(eq_idx[-1]) - pd.Timestamp(eq_idx[0])).days) if len(eq_idx) > 1 else 0

        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "calmar_ratio": calmar,
            "win_rate": win_rate,
            "daily_var_95": daily_var_95,
            "sortino_ratio": sortino_ratio,
            "profit_factor": profit_factor,
            "backtest_start": backtest_start,
            "backtest_end": backtest_end,
            "backtest_days": calendar_days,
            "n_trading_days": n_days,
        }

    def make_equity_chart(
        self,
        equity_curve: pd.DataFrame,
        benchmark_data: Optional[pd.DataFrame] = None,
    ) -> str:
        """生成净值曲线 + 回撤子图"""
        if equity_curve.empty or 'equity' not in equity_curve.columns:
            return ""

        eq = equity_curve.set_index('date')['equity']
        returns = eq.pct_change().dropna()
        nav = (1 + returns).cumprod()

        drawdown = nav / nav.cummax() - 1

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.7, 0.3],
            subplot_titles=("净值曲线", "回撤"),
        )

        fig.add_trace(
            go.Scatter(x=nav.index, y=nav.values, mode='lines',
                       name='策略净值', line=dict(color='#1f77b4', width=2)),
            row=1, col=1
        )

        if benchmark_data is not None and not benchmark_data.empty:
            bench_eq = benchmark_data.set_index('date')['close']
            bench_nav = bench_eq / bench_eq.iloc[0] if len(bench_eq) > 0 else pd.Series()
            if len(bench_nav) > 0:
                fig.add_trace(
                    go.Scatter(x=bench_nav.index, y=bench_nav.values, mode='lines',
                               name=BENCHMARK, line=dict(color='gray', width=1, dash='dash')),
                    row=1, col=1
                )

        fig.add_trace(
            go.Scatter(x=drawdown.index, y=drawdown.values, mode='lines',
                       fill='tozeroy', name='回撤',
                       line=dict(color='#d62728', width=1),
                       fillcolor='rgba(214,39,40,0.2)'),
            row=2, col=1
        )

        fig.update_layout(
            title=None,
            height=700,
            template=CHART_THEME,
            hovermode='x unified',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        fig.update_yaxes(title_text="净值", row=1, col=1)
        fig.update_yaxes(title_text="回撤 %", tickformat=".1%", row=2, col=1)
        fig.update_xaxes(title_text="日期", row=2, col=1)

        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    def make_equity_exposure_linked_chart(
        self,
        equity_curve: pd.DataFrame,
        exposures_df: pd.DataFrame,
        factor_cols: List[str],
        factor_labels: Dict[str, str],
        benchmark_data: Optional[pd.DataFrame] = None,
    ) -> str:
        """净值曲线（上）+ 回撤（中）+ 组合因子暴露时序（下）联动三面板图。

        共享 x 轴（日期），可垂直对比净值/回撤走势与对应时点因子暴露，
        判断某段上涨/回撤是由哪个风格因子驱动。下面板支持下拉切换因子。

        equity_curve: 含 date + equity 列。
        exposures_df: 含 date + 各 factor_cols 列（组合逐日暴露）。
        factor_cols: 下面板展示的因子列（按显示顺序）。
        factor_labels: 因子列 -> 中文名映射。
        """
        if equity_curve.empty or 'equity' not in equity_curve.columns:
            return ""
        if exposures_df is None or exposures_df.empty:
            return ""
        cols = [c for c in factor_cols if c in exposures_df.columns]
        if not cols:
            return ""

        eq = equity_curve.set_index('date')['equity']
        returns = eq.pct_change().dropna()
        nav = (1 + returns).cumprod()
        drawdown = nav / nav.cummax() - 1

        exp = exposures_df[["date"] + cols].copy()
        exp["date"] = pd.to_datetime(exp["date"])
        exp = exp.sort_values("date")

        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.4, 0.25, 0.35],
        )

        # 上：净值 + 基准
        fig.add_trace(
            go.Scatter(x=nav.index, y=nav.values, mode='lines',
                       name='策略净值', line=dict(color='#1f77b4', width=2)),
            row=1, col=1
        )
        if benchmark_data is not None and not benchmark_data.empty:
            bench_eq = benchmark_data.set_index('date')['close']
            bench_nav = bench_eq / bench_eq.iloc[0] if len(bench_eq) > 0 else pd.Series()
            if len(bench_nav) > 0:
                fig.add_trace(
                    go.Scatter(x=bench_nav.index, y=bench_nav.values, mode='lines',
                               name=BENCHMARK, line=dict(color='gray', width=1, dash='dash')),
                    row=1, col=1
                )

        # 中：回撤（始终显示）
        fig.add_trace(
            go.Scatter(x=drawdown.index, y=drawdown.values, mode='lines',
                       fill='tozeroy', name='策略回撤',
                       line=dict(color='#d62728', width=1),
                       fillcolor='rgba(214,39,40,0.2)'),
            row=2, col=1
        )

        # 下：各因子暴露曲线（默认显示第一个，其余通过下拉切换）
        n = len(cols)
        for i, c in enumerate(cols):
            label = factor_labels.get(c, c)
            visible = i == 0
            fig.add_trace(
                go.Scatter(x=exp["date"], y=exp[c], mode="lines+markers",
                           name=f"{label} 因子", line=dict(width=2), visible=visible),
                row=3, col=1
            )

        # 下拉菜单：切换下面板显示的因子（不再更新子图标题，标题已删除）
        # trace 顺序：净值(1) + 基准(0|1) + 回撤(1) + 因子(n)
        upper_count = 2 + (1 if benchmark_data is not None and not benchmark_data.empty else 0)
        buttons = []
        for i, c in enumerate(cols):
            label = factor_labels.get(c, c)
            # 净值/基准/回撤恒可见；下面板仅当前因子可见
            vis = [True] * upper_count + [j == i for j in range(n)]
            buttons.append(dict(
                label=label,
                method="update",
                args=[{"visible": vis}],
            ))

        fig.update_layout(
            title=None,
            height=900,
            template=CHART_THEME,
            hovermode='x unified',
            margin=dict(t=80, b=50, l=60, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            updatemenus=[dict(
                buttons=buttons,
                direction="down",
                showactive=True,
                x=1.0, xanchor="right",
                y=1.0, yanchor="top",
                pad=dict(r=10, t=10),
                font=dict(size=12),
                bgcolor="#ffffff",
                bordercolor="#d1d5db",
                borderwidth=1,
            )],
        )
        fig.update_yaxes(title_text="净值", row=1, col=1)
        fig.update_yaxes(title_text="回撤 %", row=2, col=1, tickformat=".1%")
        fig.update_yaxes(title_text="暴露度", row=3, col=1, zeroline=True, zerolinecolor="black")
        fig.update_xaxes(title_text="日期", row=3, col=1)
        return fig.to_html(full_html=False, include_plotlyjs="cdn")

    def make_monthly_heatmap(self, equity_curve: pd.DataFrame) -> str:
        """生成月度收益热力图"""
        if equity_curve.empty:
            return ""

        eq = equity_curve.set_index('date')['equity']
        returns = eq.pct_change().dropna()
        if returns.empty:
            return ""

        monthly = returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
        monthly_df = pd.DataFrame({
            'year': monthly.index.year,
            'month': monthly.index.month,
            'return': monthly.values
        })
        pivot = monthly_df.pivot(index='year', columns='month', values='return')

        month_names = ['1月', '2月', '3月', '4月', '5月', '6月',
                       '7月', '8月', '9月', '10月', '11月', '12月']
        pivot.columns = [month_names[c - 1] for c in pivot.columns]

        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=[str(y) for y in pivot.index],
            colorscale=[[0, '#d62728'], [0.5, '#ffffff'], [1, '#2ca02c']],
            zmid=0,
            text=[[f"{v:.2%}" if not np.isnan(v) else "" for v in row] for row in pivot.values],
            texttemplate="%{text}",
            textfont={"size": 11},
            colorbar=dict(title="月收益"),
        ))

        fig.update_layout(
            title=None,
            height=400,
            template=CHART_THEME,
            xaxis=dict(title="月份", side="top"),
            yaxis=dict(title="年份", autorange="reversed"),
        )

        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    def make_rolling_return_chart(
        self,
        equity_curve: pd.DataFrame,
        window: int = 30,
    ) -> str:
        """生成滚动收益曲线（rolling window 累计收益）。

        对应 quantstats 的 rolling return 维度，展示任意 window 个交易日区间
        的累计收益随时间变化，帮助观察策略盈利能力的稳定性/衰减。
        """
        if equity_curve.empty or 'equity' not in equity_curve.columns:
            return ""

        eq = equity_curve.set_index('date')['equity']
        returns = eq.pct_change().dropna()
        if len(returns) < window + 1:
            return ""

        rolling = (1 + returns).rolling(window).apply(
            lambda x: (1 + x).prod() - 1, raw=False
        ).dropna()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(x=rolling.index, y=rolling.values, mode='lines',
                       name=f'{window}日滚动收益',
                       line=dict(color='#9467bd', width=2),
                       fill='tozeroy',
                       fillcolor='rgba(148,103,189,0.15)')
        )

        fig.update_layout(
            title=None,
            height=350,
            template=CHART_THEME,
            hovermode='x unified',
            yaxis_title=f'{window}日滚动收益',
            xaxis_title='日期',
            yaxis_tickformat='.1%',
            showlegend=False,
        )

        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    def make_style_exposure_chart(self, exposures: Dict[str, float]) -> str:
        """生成风格暴露条形图"""
        if not exposures:
            return ""

        styles = list(exposures.keys())
        values = list(exposures.values())

        colors = ['#2ca02c' if v >= 0 else '#d62728' for v in values]

        fig = go.Figure(data=[
            go.Bar(x=styles, y=values, marker_color=colors,
                   text=[f"{v:.3f}" for v in values], textposition='outside')
        ])

        fig.update_layout(
            title="风格因子暴露",
            height=400,
            template=CHART_THEME,
            yaxis=dict(title="暴露度", zeroline=True, zerolinecolor='black'),
            xaxis=dict(title="风格因子"),
            showlegend=False,
        )

        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    def make_factor_exposure_timeseries_chart(
        self,
        exposures_df: pd.DataFrame,
        factor_col: str,
        label: str,
    ) -> str:
        """生成组合在单个因子上的逐日暴露时序折线图（单股/少股模式）。

        exposures_df: 需含 date 与 factor_col 列（组合逐日暴露）。
        factor_col: 因子列名；label: 图表标题中的中文名。
        """
        if exposures_df is None or exposures_df.empty or factor_col not in exposures_df.columns:
            return ""
        df = exposures_df[["date", factor_col]].dropna().sort_values("date")
        if df.empty:
            return ""

        fig = go.Figure(data=[
            go.Scatter(x=df["date"], y=df[factor_col], mode="lines+markers",
                       name=label, line=dict(color="#263859", width=2))
        ])
        fig.update_layout(
            title=f"组合因子暴露时序（{label}）",
            height=380,
            template=CHART_THEME,
            yaxis=dict(title="暴露度", zeroline=True, zerolinecolor="black"),
            xaxis=dict(title="日期"),
            showlegend=False,
        )
        return fig.to_html(full_html=False, include_plotlyjs="cdn")

    def make_factor_exposure_multi_chart(
        self,
        exposures_df: pd.DataFrame,
        factor_cols: List[str],
        factor_labels: Dict[str, str],
    ) -> str:
        """生成组合因子暴露时序图（单图 + 下拉切换因子）。

        相比逐个因子生成多张图，本方法用 Plotly updatemenus 下拉菜单，
        在一个图表内切换显示不同因子的逐日暴露，避免页面过长。

        exposures_df: 需含 date 及各 factor_cols 列。
        factor_cols: 要展示的因子列（按显示顺序）。
        factor_labels: 因子列 -> 中文名映射。
        """
        if exposures_df is None or exposures_df.empty:
            return ""
        cols = [c for c in factor_cols if c in exposures_df.columns]
        if not cols:
            return ""

        df = exposures_df[["date"] + cols].copy()
        df["date"] = pd.to_datetime(df["date"])

        traces = []
        for c in cols:
            label = factor_labels.get(c, c)
            traces.append(go.Scatter(
                x=df["date"], y=df[c], mode="lines+markers",
                name=label, line=dict(width=2),
            ))

        # 下拉菜单：每个按钮显示对应因子曲线，其余隐藏
        n = len(cols)
        buttons = []
        for i, c in enumerate(cols):
            label = factor_labels.get(c, c)
            vis = [False] * n
            vis[i] = True
            buttons.append(dict(
                label=label,
                method="update",
                args=[{"visible": vis},
                      {"title": {"text": f"组合因子暴露时序（{label}）"}}],
            ))
        # 默认显示第一个因子
        traces[0].visible = True
        for t in traces[1:]:
            t.visible = False

        fig = go.Figure(data=traces)
        fig.update_layout(
            title=f"组合因子暴露时序（{factor_labels.get(cols[0], cols[0])}）",
            height=400,
            template=CHART_THEME,
            margin=dict(t=80, b=50, l=60, r=20),
            yaxis=dict(title="暴露度", zeroline=True, zerolinecolor="black"),
            xaxis=dict(title="日期"),
            updatemenus=[dict(
                buttons=buttons,
                direction="down",
                showactive=True,
                x=1.0, xanchor="right",
                y=1.0, yanchor="top",
                pad=dict(r=10, t=10),
                font=dict(size=12),
                bgcolor="#ffffff",
                bordercolor="#d1d5db",
                borderwidth=1,
            )],
        )
        return fig.to_html(full_html=False, include_plotlyjs="cdn")

    def make_industry_attribution_chart(
        self,
        contributions: Dict[str, float]
    ) -> str:
        """生成行业利润贡献图"""
        if not contributions:
            return ""

        industries = list(contributions.keys())
        values = list(contributions.values())

        sorted_items = sorted(zip(industries, values), key=lambda x: x[1], reverse=True)
        industries, values = zip(*sorted_items) if sorted_items else ([], [])

        colors = ['#2ca02c' if v >= 0 else '#d62728' for v in values]

        fig = go.Figure(data=[
            go.Bar(x=list(industries), y=list(values), marker_color=colors,
                   text=[f"{v:.4f}" for v in values], textposition='outside')
        ])

        fig.update_layout(
            title=f"行业利润贡献 ({INDUSTRY_STANDARD.upper()}行业)",
            height=500,
            template=CHART_THEME,
            yaxis=dict(title="超额收益贡献"),
            showlegend=False,
        )

        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    def build_html_report(self) -> str:
        """构建完整 HTML 报告（统一骨架 base.html.j2 渲染，阶段一）"""
        # 组装绩效指标卡片数据（含格式化 + 正负样式），交给模板渲染
        metrics_display: List[Dict[str, Any]] = []
        metric_order = [
            ("annual_return", "年化收益", "positive"),
            ("sharpe_ratio", "夏普比率", "positive"),
            ("max_drawdown", "最大回撤", "negative"),
            ("calmar_ratio", "Calmar比率", "positive"),
            ("volatility", "年化波动率", ""),
            ("win_rate", "胜率", ""),
            ("total_return", "累计收益", "positive"),
            ("sortino_ratio", "Sortino比率", "positive"),
            ("profit_factor", "盈亏比", "positive"),
        ]

        for key, label, cls in metric_order:
            val = self.metrics.get(key)
            if val is not None and val != float("inf"):
                if key in ("annual_return", "total_return", "volatility", "max_drawdown", "win_rate"):
                    formatted = f"{val * 100:.2f}%"
                elif key == "profit_factor":
                    formatted = f"{val:.2f}"
                else:
                    formatted = f"{val:.3f}"
                pos_cls = cls if (val >= 0 and cls) else ("negative" if val < 0 else "")
                metrics_display.append({"formatted": formatted, "label": label, "pos_cls": pos_cls})
            elif val == float("inf"):
                # 盈亏比为 inf（无亏损日），显示为 "∞"
                metrics_display.append({"formatted": "∞", "label": label, "pos_cls": "positive"})

        subtitle = (
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"基准: {BENCHMARK} | 行业标准: {INDUSTRY_STANDARD.upper()} | "
            f"回测区间: {self.metrics.get('backtest_start', '—')} ~ {self.metrics.get('backtest_end', '—')} "
            f"（{self.metrics.get('backtest_days', 0)} 个自然日 / {self.metrics.get('n_trading_days', 0)} 个交易日）"
        )

        return render_page(
            "backtest.html.j2",
            title=self.title,
            subtitle=subtitle,
            metrics_display=metrics_display,
            charts=self.charts,
            factor_summary_html=self.factor_summary_html or "",
            disclaimer=(
                "本报告由 JingniTrader 基于历史行情与交易模拟自动生成，仅供学习研究用途，不构成任何投资建议。"
                "回测结果基于历史数据与既定策略规则计算，历史业绩不代表未来收益；"
                "回测可能存在过拟合、幸存者偏差、未完全反映市场冲击与流动性成本等局限，"
                "实盘交易结果可能与回测存在显著差异。请结合自身风险承受能力谨慎决策。"
            ),
        )


def _compute_portfolio_exposure_timeseries(
    portfolio_weights: pd.DataFrame,
    factor_df: pd.DataFrame,
    cols: List[str],
) -> pd.DataFrame:
    """计算持仓组合在指定因子上的逐日加权暴露（M3 单股/少股兜底）。

    暴露_t = Σ_i (权重_i,t × 因子值_i,t)。返回含 date + cols 的 DataFrame。
    """
    if portfolio_weights is None or portfolio_weights.empty:
        return pd.DataFrame()
    if factor_df is None or factor_df.empty:
        return pd.DataFrame()

    avail = [c for c in cols if c in factor_df.columns]
    if not avail:
        return pd.DataFrame()

    pw = portfolio_weights[["date", "code", "weight"]].copy()
    merged = pw.merge(factor_df[["date", "code"] + avail], on=["date", "code"], how="left")
    merged["weight"] = pd.to_numeric(merged["weight"], errors="coerce")

    def _agg(g):
        out = {}
        for c in avail:
            v = (g[c] * g["weight"]).sum()
            out[c] = v if pd.notna(v) else None
        return pd.Series(out)

    try:
        return merged.groupby("date").apply(_agg, include_groups=False).reset_index()
    except TypeError:  # 兼容旧版 pandas
        return merged.groupby("date").apply(_agg).reset_index()


def _detect_report_template(ctx) -> str:
    """
    识别报告模板：technical / fundamental / both

    统一路由逻辑，不再有量化/非量化标签区分。
    优先级：
    1. ctx.metadata["report_template"] 显式指定
    2. ctx.metadata["report_intent"] 兼容旧字段
    3. 通过用户意图关键词识别
    4. 默认 both（同时生成技术面与基本面两份报告）
    """
    meta = getattr(ctx, 'metadata', {}) or {}

    # 1. 显式指定模板
    explicit = meta.get("report_template")
    if explicit in ("technical", "fundamental", "both"):
        return explicit

    # 2. 兼容旧的 report_intent 字段
    intent = meta.get("report_intent")
    if intent in ("technical", "fundamental", "both"):
        return intent

    # 3. 关键词识别
    text = (meta.get("user_intent") or meta.get("user_query") or "").lower()
    tech_keywords = [
        "技术面", "技术分析", "k线", "k 线", "趋势", "支撑", "阻力",
        "形态", "macd", "rsi", "kdj", "boll", "均线", "量价", "资金流",
        "龙虎榜", "涨跌停", "北向",
    ]
    fund_keywords = [
        "基本面", "财务", "估值", "roe", "毛利率", "净利率", "营收",
        "利润", "pe", "pb", "ps", "股东", "分红", "股息", "现金流",
        "资产负债", "成长性", "盈利能力", "解禁", "回购",
    ]

    tech_hits = sum(1 for kw in tech_keywords if kw in text)
    fund_hits = sum(1 for kw in fund_keywords if kw in text)

    if tech_hits > 0 and fund_hits == 0:
        return "technical"
    if fund_hits > 0 and tech_hits == 0:
        return "fundamental"
    if tech_hits > 0 and fund_hits > 0:
        if tech_hits - fund_hits >= 2:
            return "technical"
        if fund_hits - tech_hits >= 2:
            return "fundamental"
        return "both"

    # 4. 默认两份都生成
    return "both"


def _maybe_render_factor_analysis_report(ctx) -> str:
    """T3-8: 若存在 alphalens 因子分析报告目录，则聚合各因子 metrics.json
    生成独立的因子分析汇总报告 HTML。

    读取 workspace/reports/alphalens/<task_id>/*_metrics.json，
    拼接为单页 HTML，含各因子关键指标卡片 + 原 HTML 报告链接。

    返回
    ----
    生成的 HTML 路径；不存在或无 metrics.json 时返回空字符串
    """
    import glob as _glob

    _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    task_id = getattr(ctx, "task_id", "") or "default"
    alphalens_dir = os.path.join(_work_dir, "reports", "alphalens", task_id)
    if not os.path.isdir(alphalens_dir):
        return ""

    metrics_files = sorted(_glob.glob(os.path.join(alphalens_dir, "*_metrics.json")))
    if not metrics_files:
        return ""

    # 读取所有 metrics.json
    metrics_list = []
    for mf in metrics_files:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                metrics_list.append(json.load(f))
        except Exception as e:
            logger.warning(f"读取 alphalens metrics 失败: {mf}: {e}")

    if not metrics_list:
        return ""

    # 渲染汇总 HTML（M5: 统一惊泥科技样式）
    cards_html = []
    for m in metrics_list:
        verdict = m.get("suggested_verdict", "REVIEW")
        verdict_cls = {
            "ACCEPT": "signal-bullish", "REVIEW": "signal-warning", "REJECT": "signal-bearish"
        }.get(verdict, "signal-neutral")
        factor_name = m.get("factor", "unknown")
        # 找到对应的因子报告 HTML（同目录下 <factor>_report.html）
        factor_html = os.path.join(alphalens_dir, f"{factor_name}_report.html")
        # relpath 基准使用运行时 work_dir/reports，与 output_path 一致
        _runtime_report_dir = os.path.join(_work_dir, "reports")
        factor_link = (
            f'<a href="{os.path.relpath(factor_html, _runtime_report_dir)}" target="_blank" '
            f'class="factor-link">查看详情</a>'
            if os.path.exists(factor_html) else ""
        )
        cards_html.append(f"""
        <div class="factor-card">
          <h3>{factor_name} <span class="signal-tag {verdict_cls}">{verdict}</span></h3>
          <div class="metrics-row">
            <span><b>IC 均值</b>: {m.get('ic_mean', 0):.4f}</span>
            <span><b>IC IR</b>: {m.get('ic_ir', 0):.4f}</span>
            <span><b>多空夏普</b>: {m.get('long_short_sharpe', 0):.4f}</span>
            <span><b>多空收益</b>: {m.get('long_short_return', 0):.4f}</span>
            <span><b>Top 换手率</b>: {m.get('avg_turnover_top_quantile', 0):.4f}</span>
          </div>
          <div class="link-row">{factor_link}</div>
        </div>""")

    page_css = build_page_css()
    nav_html = build_nav_bar_html()
    footer_html = build_footer_html(disclaimer=(
        "本报告由 JingniTrader 基于历史样本数据自动生成，仅供量化研究与学习用途，不构成任何投资建议。"
        "因子有效性指标（IC、多空收益、夏普等）基于历史截面样本统计，属于历史检验结果；"
        "统计上显著不代表未来仍有效，因子可能因市场结构变化、样本外失效或拥挤交易而衰减。"
        "因子分析结论不构成选股或交易依据，实盘交易有风险，请谨慎决策。"
    ))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>因子分析报告</title>
<style>
  /* 统一页面布局 + 导航栏 + 底部版权栏（与其余报告一致） */
  {page_css}
  /* ── 因子分析报告特有样式 ── */
  .factor-card {{ background: var(--card-bg); padding: 16px; border-radius: 8px; margin: 12px 0;
                 border-left: 4px solid var(--jm-accent); box-shadow: var(--shadow); }}
  .factor-card h3 {{ margin: 0 0 8px 0; color: var(--jm-primary); }}
  .signal-warning {{ background: #fef3c7; color: #d97706; }}
  .metrics-row {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 14px; }}
  .metrics-row span {{ background: var(--bg); padding: 4px 8px; border-radius: 3px; }}
  .link-row {{ margin-top: 8px; font-size: 13px; }}
  .factor-link {{ color: var(--jm-secondary); text-decoration: none; font-weight: 500; }}
  .factor-link:hover {{ color: var(--jm-accent); text-decoration: underline; }}
</style>
</head>
<body>
{nav_html}

<div class="header">
    <h1>因子分析报告</h1>
    <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 任务 ID: {task_id}</p>
</div>

<div class="section">
    <h2>因子汇总</h2>
    共分析 <b>{len(metrics_list)}</b> 个因子。
    ACCEPT 数量：{sum(1 for m in metrics_list if m.get('suggested_verdict') == 'ACCEPT')}
    ｜ REVIEW 数量：{sum(1 for m in metrics_list if m.get('suggested_verdict') == 'REVIEW')}
</div>

{''.join(cards_html)}

{footer_html}
</body>
</html>"""

    # 使用运行时 QUANT_WORK_DIR 而非模块加载时固化的 REPORT_DIR，
    # 避免 monkeypatch.setenv("QUANT_WORK_DIR") 后输出路径不一致
    _report_dir = os.path.join(_work_dir, "reports")
    os.makedirs(_report_dir, exist_ok=True)
    output_path = os.path.join(_report_dir, "factor_analysis_report.html")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"因子分析汇总报告已生成: {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"因子分析汇总报告生成失败: {e}")
        return ""


def _collect_plugin_data(ctx, requires: List[str]) -> Dict[str, Any]:
    """按插件 requires 声明收集产物数据。

    支持的产物：
      - DATA:     行情数据 DataFrame（读 parquet）
      - FACTOR:   因子数据 DataFrame（读 parquet）
      - BACKTEST: 回测产物（返回 backtest_result.json 解析后的 dict + equity_curve）
      其余未知产物：返回其 artifact 路径字符串（由插件自行读取）。
    """
    import pandas as _pd

    data: Dict[str, Any] = {}
    for key in requires:
        if not hasattr(ctx, "get_artifact"):
            continue
        path = ctx.get_artifact(key)
        if not path:
            continue
        try:
            if key in ("DATA", "FACTOR"):
                data[key] = _pd.read_parquet(path) if os.path.exists(path) else None
            elif key == "BACKTEST":
                # 回测目录：返回 dir 路径 + 净值曲线
                bt = {"dir": os.path.dirname(path) if os.path.exists(path) else ""}
                eq_path = os.path.join(os.path.dirname(path), "equity_curve.parquet")
                bt["equity_curve"] = _pd.read_parquet(eq_path) if os.path.exists(eq_path) else None
                data[key] = bt
            else:
                data[key] = path
        except Exception as e:
            logger.warning(f"插件产物[{key}]收集失败: {e}")
            data[key] = None
    return data


def _run_plugin(ctx, plugin) -> Dict[str, Any]:
    """运行报告插件：收集产物 → 调 render → 写文件 → 注册门户。

    参数:
        ctx:    Context 对象
        plugin: ReportPlugin（已由 loader 注册，含 render 函数）
    返回:
        与其它 _run_*_report 一致的结果 dict {success, artifact_path, ...}
    """
    if plugin.render is None:
        return {"success": False, "error": f"插件[{plugin.id}]无 render 函数"}

    try:
        # 收集产物数据
        data = _collect_plugin_data(ctx, plugin.requires)

        # 输出路径：<QUANT_WORK_DIR>/reports/<plugin_id>.html
        _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
        out_dir = os.path.join(_work_dir, "reports")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{plugin.id}.html")

        # 调用插件 render
        html = plugin.render(data, ctx, output_path)

        if not html or not os.path.exists(output_path):
            # render 未写文件则框架代写
            if html:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(html)
            else:
                return {"success": False, "error": f"插件[{plugin.id}]未返回 HTML"}

        # 注册门户
        try:
            _update_report_portal(
                output_path,
                report_type=plugin.id,
                title=f"{plugin.label}报告",
                task_id=ctx.task_id if hasattr(ctx, "task_id") else "",
            )
        except Exception as e:
            logger.warning(f"插件[{plugin.id}]门户注册失败: {e}")

        logger.info(f"插件[{plugin.id}]报告已生成: {output_path}")
        return {
            "success": True,
            "artifact_path": output_path,
            "metadata": {"plugin_id": plugin.id, "report_type": plugin.id},
            "error": "",
        }
    except Exception as e:
        logger.error(f"插件[{plugin.id}]运行异常: {e}")
        return {"success": False, "error": str(e)}


def _run_template_report(ctx) -> Dict[str, Any]:
    """
    模板化报告生成路径：根据 report_template 路由到对应模板

    - technical: 生成技术分析报告
    - fundamental: 生成基本面分析报告
    - both: 同时生成两份报告（默认）

    流程：生成含占位符的 HTML → 调用 LLM 生成深度解读 → 替换占位符 → 输出最终报告。
    LLM 调用在 skill 内部完成，无需 agent 二次介入。
    """
    from scripts.template_engine import ReportTemplateEngine

    os.makedirs(REPORT_DIR, exist_ok=True)

    template_choice = _detect_report_template(ctx)
    logger.info(f"报告模板: {template_choice}")

    engine = ReportTemplateEngine()
    llm_prompts: Dict[str, Any] = {}
    generated_paths: List[str] = []

    templates_to_generate = []
    if template_choice in ("technical", "both"):
        templates_to_generate.append(("technical", "technical_report.html"))
    if template_choice in ("fundamental", "both"):
        templates_to_generate.append(("fundamental", "fundamental_report.html"))

    for tpl_id, filename in templates_to_generate:
        output_path = os.path.join(REPORT_DIR, filename)
        result = engine.generate(tpl_id, ctx, output_path)
        if result.get("success"):
            generated_paths.append(result["artifact_path"])
            llm_prompts.update(result.get("llm_prompts", {}))
            logger.info(f"模板 [{tpl_id}] 报告已生成: {result['artifact_path']}")
        else:
            logger.error(f"模板 [{tpl_id}] 报告生成失败: {result.get('error', '')}")

    if not generated_paths:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "所有模板报告生成失败"
        }

    # ── 调用 LLM 生成深度解读并注入报告 ──
    llm_responses: Dict[str, Any] = {}
    llm_status = "skipped"  # skipped | success | fallback | failed

    if llm_prompts:
        try:
            from scripts.llm_client import generate_analysis, is_available
            if is_available():
                logger.info("开始调用 LLM 生成深度解读...")
                for analyst_type, prompt_data in llm_prompts.items():
                    resp = generate_analysis(prompt_data)
                    if resp:
                        llm_responses[analyst_type] = resp
                        logger.info(f"  {analyst_type}: LLM 解读生成成功")
                    else:
                        logger.warning(f"  {analyst_type}: LLM 返回为空，使用规则模板兜底")
                llm_status = "success" if llm_responses else "failed"
            else:
                llm_status = "skipped"
                logger.info("未配置 QUANT_LLM_API_KEY，跳过 LLM 调用，深度解读使用规则模板兜底")
        except Exception as e:
            llm_status = "failed"
            logger.warning(f"LLM 调用异常: {e}，深度解读使用规则模板兜底")

    # 无论 LLM 是否成功，都替换占位符（LLM 失败时用规则模板生成兜底内容）
    _inject_deep_analysis(generated_paths, llm_responses, llm_prompts)

    # T3-8: 若存在 alphalens 因子分析报告，则聚合各因子 metrics.json 生成独立的因子分析报告
    factor_report_path = _maybe_render_factor_analysis_report(ctx)
    if factor_report_path:
        generated_paths.append(factor_report_path)

    # 更新报告门户（technical 和 fundamental 分别注册，同类型不同文件共存）。
    # 因子分析报告（factor_analysis_report.html）不进门户，生成后直接打开文件。
    for path in generated_paths:
        fname = os.path.basename(path)
        if "factor_analysis" in fname:
            logger.info("因子分析报告不注册到门户（生成后直接打开）: %s", path)
            continue
        if "technical" in fname:
            _title = "个股分析-技术面"
            _rtype = "technical_report"
        elif "fundamental" in fname:
            _title = "个股分析-基本面"
            _rtype = "fundamental_report"
        else:
            _title = ""
            _rtype = "report"
        _update_report_portal(path, _rtype, title=_title,
                              task_id=ctx.task_id if hasattr(ctx, 'task_id') else "")

    primary_path = generated_paths[0]
    report_data = {
        "report_template": template_choice,
        "generated_at": datetime.now().isoformat(),
        "artifacts": generated_paths,
        "llm_status": llm_status,
    }
    data_path_out = os.path.join(REPORT_DIR, "report_data.json")
    with open(data_path_out, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

    return {
        "success": True,
        "artifact_path": primary_path,
        "metadata": {
            "report_template": template_choice,
            "report_data_path": data_path_out,
            "all_artifacts": generated_paths,
            "llm_prompts": llm_prompts,
            "llm_status": llm_status,
            "factor_report_path": factor_report_path,
        },
        "error": ""
    }


def _update_report_portal(
    html_path: str,
    report_type: str,
    title: str = "",
    task_id: str = "",
    live: bool = False,
    **extra: Any,
) -> None:
    """报告生成成功后更新 manifest.json 并刷新门户页 index.html。

    参数:
        html_path: 报告 HTML 文件绝对路径
        report_type: 报告类型（technical_report/fundamental_report/portfolio/execution/attribution/backtest）
        title: 报告标题（空时按类型取默认）
        task_id: 任务 ID
        live: 是否为 LIVE 实时报告
        **extra: 额外字段（如 backend/snapshot_file）
    """
    try:
        from scripts.report_portal import upsert_report, generate_portal
        _report_dir = os.path.dirname(html_path)
        upsert_report(
            report_dir=_report_dir,
            report_type=report_type,
            file_path=html_path,
            title=title or None,
            task_id=task_id,
            live=live,
            **extra,
        )
        generate_portal(_report_dir)
    except Exception as e:
        logger.warning(f"更新报告门户失败（不阻断）: {e}")


def _inject_deep_analysis(
    html_paths: List[str],
    llm_responses: Dict[str, Any],
    llm_prompts: Dict[str, Any],
) -> None:
    """将 LLM 深度解读内容注入 HTML 报告（替换占位符）

    LLM 成功时用 LLM 输出；失败时用规则模板从因子数据生成兜底内容。
    """
    import html as _html_lib

    for html_path in html_paths:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        modified = False

        # 技术面占位符
        if "<!--LLM_TECHNICAL_ANALYSIS_PLACEHOLDER-->" in html_content:
            resp = llm_responses.get("technical")
            if not resp:
                # 兜底：从 prompt 中的因子数据生成规则解读
                resp = _build_fallback_technical(llm_prompts.get("technical", {}))
            rendered = _render_technical_analysis(resp)
            html_content = html_content.replace(
                "<!--LLM_TECHNICAL_ANALYSIS_PLACEHOLDER-->", rendered
            )
            modified = True

        # 基本面占位符
        if "<!--LLM_FUNDAMENTAL_ANALYSIS_PLACEHOLDER-->" in html_content:
            resp = llm_responses.get("fundamental")
            if not resp:
                resp = _build_fallback_fundamental(llm_prompts.get("fundamental", {}))
            rendered = _render_fundamental_analysis(resp)
            html_content = html_content.replace(
                "<!--LLM_FUNDAMENTAL_ANALYSIS_PLACEHOLDER-->", rendered
            )
            modified = True

        if modified:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"深度解读已注入: {html_path}")


def _render_technical_analysis(resp: Dict[str, Any]) -> str:
    """渲染技术面深度解读 HTML"""
    import html as _html_lib
    score = float(resp.get("technical_score", 0))
    sc = "positive" if score >= 60 else ("negative" if score < 40 else "")
    opt = []
    for key, title, tag in [("capital_flow_analysis", "资金面分析", True),
                             ("dragon_tiger_analysis", "龙虎榜解读", True),
                             ("price_limit_analysis", "涨跌停分析", False)]:
        v = resp.get(key, "")
        if v:
            t = " <span class='llm-tag-a'>A股特色</span>" if tag else ""
            opt.append(f"<h4>{title}{t}</h4><p>{_html_lib.escape(v)}</p>")
    return (
        f'<div class="llm-analysis-header">'
        f'<span class="llm-badge llm-badge-{sc}">技术评分 {score:.0f}</span>'
        f'<span class="llm-badge">趋势：{_html_lib.escape(resp.get("trend_direction", ""))}</span>'
        f'<span class="llm-badge">置信度：{_html_lib.escape(resp.get("trend_confidence", ""))}</span>'
        f'</div>'
        f'<div class="llm-analysis-body">'
        f'<h4>整体评估</h4><p>{_html_lib.escape(resp.get("overall_assessment", ""))}</p>'
        f'<h4>多周期趋势分析</h4><p>{_html_lib.escape(resp.get("trend_analysis", ""))}</p>'
        f'<h4>技术指标信号解读</h4><p>{_html_lib.escape(resp.get("indicator_analysis", ""))}</p>'
        f'<h4>关键价位分析</h4><p>{_html_lib.escape(resp.get("key_levels", ""))}</p>'
        f'<h4>风险信号</h4><p>{_html_lib.escape(resp.get("risk_signals", ""))}</p>'
        f'<h4>短期展望</h4><p>{_html_lib.escape(resp.get("short_term_outlook", ""))}</p>'
        f'{"".join(opt)}'
        f'</div>'
    )


def _render_fundamental_analysis(resp: Dict[str, Any]) -> str:
    """渲染基本面深度解读 HTML"""
    import html as _html_lib
    score = float(resp.get("fundamental_score", 0))
    sc = "positive" if score >= 60 else ("negative" if score < 40 else "")
    opt = []
    for key, title, tag in [("industry_analysis", "行业分析与景气度", False),
                             ("financial_statement_analysis", "财务报表分析", False),
                             ("shareholder_analysis", "股东结构与资本运作", True)]:
        v = resp.get(key, "")
        if v:
            t = " <span class='llm-tag-a'>A股特色</span>" if tag else ""
            opt.append(f"<h4>{title}{t}</h4><p>{_html_lib.escape(v)}</p>")
    return (
        f'<div class="llm-analysis-header">'
        f'<span class="llm-badge llm-badge-{sc}">基本面评分 {score:.0f}</span>'
        f'<span class="llm-badge">估值：{_html_lib.escape(resp.get("valuation_level", ""))}</span>'
        f'<span class="llm-badge">评级：{_html_lib.escape(resp.get("investment_rating", ""))}</span>'
        f'</div>'
        f'<div class="llm-analysis-body">'
        f'<h4>整体评估</h4><p>{_html_lib.escape(resp.get("overall_assessment", ""))}</p>'
        f'<h4>估值分析</h4><p>{_html_lib.escape(resp.get("valuation_analysis", ""))}</p>'
        f'<h4>盈利能力分析</h4><p>{_html_lib.escape(resp.get("profitability_analysis", ""))}</p>'
        f'<h4>成长性分析</h4><p>{_html_lib.escape(resp.get("growth_analysis", ""))}</p>'
        f'<h4>风险因素</h4><p>{_html_lib.escape(resp.get("risk_factors", ""))}</p>'
        f'{"".join(opt)}'
        f'</div>'
    )


def _build_fallback_technical(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，从 prompt 中的因子数据生成规则兜底解读

    解析 user_prompt 中的因子数值，基于简单规则生成分析文本。
    """
    import re

    user_prompt = prompt_data.get("user_prompt", "")

    # 提取因子数值（prompt 格式如 "MA5=310.35", "DIF=0.997" 等）
    factors = {}
    for match in re.finditer(r'(\w+)[=：]\s*(-?[\d.]+)', user_prompt):
        key, val = match.group(1).lower(), match.group(2)
        try:
            factors[key] = float(val)
        except ValueError:
            pass

    # 规则生成（prompt 中用 DIF/DEA/柱 等简写名）
    ma5 = factors.get("ma5", 0)
    ma10 = factors.get("ma10", 0)
    ma20 = factors.get("ma20", 0)
    ma60 = factors.get("ma60", 0)
    current = factors.get("current_price", factors.get("close", 0))
    macd_dif = factors.get("macd_dif", factors.get("dif", 0))
    macd_dea = factors.get("macd_dea", factors.get("dea", 0))
    macd_hist = factors.get("macd_hist", factors.get("柱", 0))
    kdj_k = factors.get("kdj_k", factors.get("k", 0))
    kdj_d = factors.get("kdj_d", factors.get("d", 0))
    kdj_j = factors.get("kdj_j", factors.get("j", 0))
    boll_ub = factors.get("boll_ub", factors.get("上轨", 0))
    boll_lb = factors.get("boll_lb", factors.get("下轨", 0))
    boll_mid = factors.get("boll_mid", factors.get("中轨", 0))

    # 趋势方向
    if ma5 > ma10 > ma20 and current > ma60:
        trend = "看涨"
        score = 70
    elif ma5 < ma10 < ma20 and current < ma60:
        trend = "看跌"
        score = 30
    else:
        trend = "震荡"
        score = 55

    # MACD 信号
    if macd_dif > macd_dea and macd_hist > 0:
        macd_desc = f"MACD金叉（DIF={macd_dif:.2f} > DEA={macd_dea:.2f}），柱状图转正（{macd_hist:.2f}），短期动量转强"
        score = min(score + 5, 100)
    elif macd_dif < macd_dea and macd_hist < 0:
        macd_desc = f"MACD死叉（DIF={macd_dif:.2f} < DEA={macd_dea:.2f}），柱状图为负（{macd_hist:.2f}），短期动量偏弱"
        score = max(score - 5, 0)
    else:
        macd_desc = f"MACD处于转换期（DIF={macd_dif:.2f}, DEA={macd_dea:.2f}），趋势不明朗"

    # KDJ 信号
    if kdj_j > 80:
        kdj_desc = f"KDJ超买（K={kdj_k:.1f}, D={kdj_d:.1f}, J={kdj_j:.1f}），短期有回调风险"
    elif kdj_j < 20:
        kdj_desc = f"KDJ超卖（K={kdj_k:.1f}, D={kdj_d:.1f}, J={kdj_j:.1f}），短期有反弹机会"
    else:
        kdj_desc = f"KDJ中性区域（K={kdj_k:.1f}, D={kdj_d:.1f}, J={kdj_j:.1f}），方向待选择"

    # 布林带
    boll_width = 0
    if boll_ub > 0 and boll_lb > 0:
        boll_width = boll_ub - boll_lb
        if current >= boll_ub * 0.98:
            boll_desc = f"价格接近布林上轨（{boll_ub:.2f}），短期偏强但注意回落"
        elif current <= boll_lb * 1.02:
            boll_desc = f"价格接近布林下轨（{boll_lb:.2f}），短期偏弱但关注支撑"
        else:
            boll_desc = f"价格在布林带中轨（{boll_mid:.2f}）附近运行，带宽{boll_width:.2f}"
    else:
        boll_desc = "布林带数据暂缺"

    # 均线分析
    if ma5 > ma10 > ma20:
        ma_desc = f"短期均线多头排列（MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20:.2f}），短期趋势偏多"
    elif ma5 < ma10 < ma20:
        ma_desc = f"短期均线空头排列（MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20:.2f}），短期趋势偏空"
    else:
        ma_desc = f"短期均线粘合（MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f}），方向待选择"

    ma60_note = f"MA60={ma60:.2f}" if ma60 > 0 else "MA60数据暂缺"

    # 龙虎榜
    lhb_count = int(factors.get("lhb_count_5d", 0))
    lhb_note = f"近5日上榜{lhb_count}次" if lhb_count > 0 else "近5日无龙虎榜记录"

    return {
        "trend_direction": trend,
        "trend_confidence": "中",
        "technical_score": score,
        "overall_assessment": f"当前技术面{trend}，{macd_desc.split('，')[0]}。",
        "trend_analysis": f"{ma_desc}。{ma60_note}。多周期共振情况需结合周线/月线判断。",
        "indicator_analysis": f"{macd_desc}。{kdj_desc}。{boll_desc}。",
        "key_levels": f"MA20({ma20:.2f})为短期支撑，MA60({ma60:.2f})为中期阻力。布林上轨({boll_ub:.2f})和下轨({boll_lb:.2f})为极端位置参考。",
        "risk_signals": "量能数据缺失，无法判断量价配合。布林带带宽变化需关注突破方向。" if boll_width < 30 else "布林带较宽，波动正常。",
        "short_term_outlook": f"关注MA20({ma20:.2f})支撑和MA60({ma60:.2f})阻力的突破方向。",
        "capital_flow_analysis": "",
        "dragon_tiger_analysis": lhb_note,
        "price_limit_analysis": "",
    }


def _build_fallback_fundamental(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，从 prompt 中的因子数据生成规则兜底解读"""
    import re

    user_prompt = prompt_data.get("user_prompt", "")

    factors = {}
    for match in re.finditer(r'(\w+)[=：]\s*(-?[\d.]+)', user_prompt):
        key, val = match.group(1).lower(), match.group(2)
        try:
            factors[key] = float(val)
        except ValueError:
            pass

    roe = factors.get("roe_ttm", factors.get("roe", 0))
    debt = factors.get("debt_ratio", factors.get("资产负债率", 0))
    current = factors.get("current_ratio", factors.get("流动比率", 0))
    pe = factors.get("pe_ttm", factors.get("pe", 0))
    pb = factors.get("pb", 0)

    # 评分
    score = 50
    if roe > 15:
        score += 15
    elif roe > 10:
        score += 8
    elif roe < 5:
        score -= 10

    if debt > 70:
        score -= 10
    elif debt < 50:
        score += 5

    if current < 1:
        score -= 8
    elif current > 2:
        score += 5

    score = max(0, min(100, score))

    # 估值判断
    if pe > 0:
        if pe < 15:
            valuation = "低估"
        elif pe < 30:
            valuation = "合理"
        elif pe < 50:
            valuation = "偏高"
        else:
            valuation = "高估"
    else:
        valuation = "合理"

    # 评级
    if score >= 70:
        rating = "买入"
    elif score >= 60:
        rating = "增持"
    elif score >= 40:
        rating = "中性"
    elif score >= 30:
        rating = "减持"
    else:
        rating = "卖出"

    roe_desc = f"ROE(TTM)为{roe:.2f}%，" + ("股东回报效率优秀" if roe > 15 else "股东回报效率偏低" if roe < 8 else "股东回报效率适中")
    debt_desc = f"资产负债率{debt:.2f}%，" + ("杠杆水平偏高" if debt > 70 else "杠杆水平适中" if debt > 50 else "杠杆水平较低")
    current_desc = f"流动比率{current:.2f}，" + ("短期偿债能力偏弱" if current < 1 else "短期偿债能力良好" if current > 2 else "短期偿债能力一般")

    risk_items = []
    if debt > 70:
        risk_items.append(f"资产负债率{debt:.1f}%偏高，财务杠杆风险较大")
    if current < 1:
        risk_items.append(f"流动比率{current:.2f}低于1，短期偿债压力较大")
    if roe < 5:
        risk_items.append(f"ROE仅{roe:.1f}%，盈利能力偏弱")
    if not risk_items:
        risk_items.append("未发现重大基本面风险信号")

    return {
        "valuation_level": valuation,
        "fundamental_score": score,
        "investment_rating": rating,
        "overall_assessment": f"{roe_desc}。{debt_desc}。综合评估给予{rating}评级。",
        "valuation_analysis": f"PE(TTM)={pe:.1f}，PB={pb:.2f}，估值水平{valuation}。" if pe > 0 else "估值数据暂缺，无法判断估值高低。",
        "profitability_analysis": roe_desc + "。建议关注毛利率和净利率的改善趋势。",
        "growth_analysis": "营收和利润增速数据暂缺，建议关注后续财报和行业增速变化。",
        "risk_factors": "；".join(risk_items) + "。",
        "industry_analysis": "",
        "financial_statement_analysis": f"{debt_desc}。{current_desc}。",
        "shareholder_analysis": "",
    }


def _build_attribution_llm_prompt(
    rt_stats: dict,
    pnl_by_stock: pd.DataFrame,
    exec_quality: dict,
    stress_perf: dict,
) -> dict:
    """构建绩效归因 LLM prompt"""
    system_prompt = (
        "你是一位专业的量化投资绩效分析师。请根据以下绩效归因数据，"
        "生成一份结构化的绩效归因分析报告。要求：\n"
        "1. 分析盈亏的主要来源（按标的/按持仓时间/按交易方向）\n"
        "2. 评估交易执行质量（费用占比/滑点）\n"
        "3. 识别交易模式中的优势与不足\n"
        "4. 给出具体的改进建议\n"
        "请以 JSON 格式返回，包含以下字段：\n"
        '{"overall_summary": "总体评价", "pnl_source_analysis": "盈亏来源分析", '
        '"execution_quality_analysis": "执行质量分析", "pattern_analysis": "交易模式分析", '
        '"improvement_suggestions": "改进建议", "risk_assessment": "风险评估"}'
    )

    # 构建用户 prompt
    parts = []
    if rt_stats:
        parts.append("=== Round-Trip 统计 ===")
        parts.append(f"闭环交易数: {rt_stats.get('total_round_trips', 0)}")
        parts.append(f"胜率: {rt_stats.get('win_rate', 0) * 100:.1f}%")
        parts.append(f"总净盈亏: {rt_stats.get('total_net_pnl', 0):,.2f}")
        parts.append(f"盈亏比: {rt_stats.get('profit_factor', 0):.2f}")
        parts.append(f"平均持仓天数: {rt_stats.get('avg_holding_days', 0):.1f}")

    if not pnl_by_stock.empty:
        parts.append("\n=== 按标的盈亏（前10） ===")
        for _, row in pnl_by_stock.head(10).iterrows():
            parts.append(
                f"{row['code']}: 盈亏={row['total_pnl']:,.2f}, "
                f"交易次数={row['trade_count']}, 胜率={row['win_rate']:.1f}%, "
                f"平均收益={row['avg_return_pct']:.2f}%"
            )

    if exec_quality:
        parts.append("\n=== 执行质量 ===")
        parts.append(f"总成交额: {exec_quality.get('total_turnover', 0):,.2f}")
        parts.append(f"成本占比: {exec_quality.get('cost_ratio_bps', 0):.2f} bps")
        parts.append(f"滑点占比: {exec_quality.get('slippage_ratio_bps', 0):.2f} bps")

    if stress_perf:
        parts.append("\n=== 压力期表现 ===")
        for name, data in stress_perf.items():
            parts.append(f"{name}: 收益={data['return_pct']:.2f}%, 回撤={data['max_drawdown_pct']:.2f}%")

    return {
        "system_prompt": system_prompt,
        "user_prompt": "\n".join(parts),
    }


def _build_fallback_attribution(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，生成规则兜底绩效归因解读"""
    user_prompt = prompt_data.get("user_prompt", "")

    # 从 prompt 中提取关键数据
    import re
    win_rate_match = re.search(r'胜率:\s*([\d.]+)%', user_prompt)
    win_rate = float(win_rate_match.group(1)) if win_rate_match else 0

    pnl_match = re.search(r'总净盈亏:\s*([-\d,.]+)', user_prompt)
    total_pnl = float(pnl_match.group(1).replace(',', '')) if pnl_match else 0

    pf_match = re.search(r'盈亏比:\s*([\d.]+)', user_prompt)
    profit_factor = float(pf_match.group(1)) if pf_match else 0

    cost_match = re.search(r'成本占比:\s*([\d.]+)\s*bps', user_prompt)
    cost_bps = float(cost_match.group(1)) if cost_match else 0

    # 规则生成
    if total_pnl > 0:
        overall = f"本期交易整体盈利，总净盈亏 {total_pnl:,.2f} 元，胜率 {win_rate:.1f}%。"
    elif total_pnl < 0:
        overall = f"本期交易整体亏损，总净盈亏 {total_pnl:,.2f} 元，胜率 {win_rate:.1f}%。"
    else:
        overall = "本期交易盈亏基本持平。"

    if profit_factor > 1.5:
        pnl_analysis = f"盈亏比 {profit_factor:.2f}，盈利交易的规模显著大于亏损交易，风险控制良好。"
    elif profit_factor > 1.0:
        pnl_analysis = f"盈亏比 {profit_factor:.2f}，略高于1，盈利略大于亏损，有改善空间。"
    else:
        pnl_analysis = f"盈亏比 {profit_factor:.2f}，低于1，亏损交易规模大于盈利，需加强止损管理。"

    if cost_bps > 30:
        exec_analysis = f"交易成本占比 {cost_bps:.2f} bps，偏高，建议减少交易频率或优化下单方式。"
    elif cost_bps > 10:
        exec_analysis = f"交易成本占比 {cost_bps:.2f} bps，适中，处于合理范围。"
    else:
        exec_analysis = f"交易成本占比 {cost_bps:.2f} bps，较低，执行效率良好。"

    if win_rate > 60:
        pattern = f"胜率 {win_rate:.1f}%，交易胜率较高，说明选股策略有一定的有效性。"
    elif win_rate > 40:
        pattern = f"胜率 {win_rate:.1f}%，胜率中等，建议结合盈亏比综合评估策略效果。"
    else:
        pattern = f"胜率 {win_rate:.1f}%，胜率偏低，建议优化入场条件或增加过滤条件。"

    suggestions = []
    if profit_factor < 1.5:
        suggestions.append("建议设置更严格的止损规则，控制单笔亏损规模。")
    if cost_bps > 20:
        suggestions.append("建议减少短线交易频率，降低交易成本对收益的侵蚀。")
    if win_rate < 50:
        suggestions.append("建议增加入场信号过滤条件，提高交易胜率。")
    if not suggestions:
        suggestions.append("当前策略表现稳定，建议持续监控并定期复盘。")

    return {
        "overall_summary": overall,
        "pnl_source_analysis": pnl_analysis,
        "execution_quality_analysis": exec_analysis,
        "pattern_analysis": pattern,
        "improvement_suggestions": " ".join(suggestions),
        "risk_assessment": "绩效归因基于历史交易数据，不构成未来收益保证。建议持续监控策略表现，及时调整。",
    }


def _render_attribution_analysis(resp: Dict[str, Any]) -> str:
    """渲染绩效归因 LLM 解读 HTML"""
    import html as _html_lib

    return (
        f'<div class="llm-analysis-body">'
        f'<h4>总体评价</h4><p>{_html_lib.escape(resp.get("overall_summary", ""))}</p>'
        f'<h4>盈亏来源分析</h4><p>{_html_lib.escape(resp.get("pnl_source_analysis", ""))}</p>'
        f'<h4>执行质量分析</h4><p>{_html_lib.escape(resp.get("execution_quality_analysis", ""))}</p>'
        f'<h4>交易模式分析</h4><p>{_html_lib.escape(resp.get("pattern_analysis", ""))}</p>'
        f'<h4>改进建议</h4><p>{_html_lib.escape(resp.get("improvement_suggestions", ""))}</p>'
        f'<h4>风险评估</h4><p>{_html_lib.escape(resp.get("risk_assessment", ""))}</p>'
        f'</div>'
    )


def _inject_attribution_analysis(
    html_path: str,
    llm_responses: Dict[str, Any],
    llm_prompts: Dict[str, Any],
) -> None:
    """将 LLM 绩效归因解读注入 HTML 报告（替换占位符）"""
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    if "<!--LLM_ATTRIBUTION_PLACEHOLDER-->" in html_content:
        resp = llm_responses.get("attribution")
        if not resp:
            resp = _build_fallback_attribution(llm_prompts.get("attribution", {}))
        rendered = _render_attribution_analysis(resp)
        html_content = html_content.replace(
            "<!--LLM_ATTRIBUTION_PLACEHOLDER-->", rendered
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"绩效归因解读已注入: {html_path}")


def _run_portfolio_report(ctx) -> Dict[str, Any]:
    """组合优化报告生成路径（F-P0-2 权重分布图）。

    流程：
    1. 定位 PORTFOLIO 产物（portfolio_weights.json）
    2. 从 ctx.metadata 提取 portfolio-risk-engine 的 metadata
    3. 加载 FACTOR 产物的 industry 列（可选，用于行业着色）
    4. 调用 portfolio_report.build_portfolio_report 生成 HTML
    5. 落盘到 REPORT_DIR/portfolio_report.html

    数据缺失容错：任一上游字段缺失时显示「数据不可用」占位，不阻塞生成。
    """
    from scripts.templates.portfolio_report import build_portfolio_report

    _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    _report_dir = os.path.join(_work_dir, "reports")
    os.makedirs(_report_dir, exist_ok=True)

    # 1. 定位 PORTFOLIO 产物
    portfolio_artifact = ctx.get_artifact("PORTFOLIO") if hasattr(ctx, 'get_artifact') else None
    if not portfolio_artifact:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "未找到 PORTFOLIO 产物，无法生成组合优化报告。请先执行组合优化。"
        }

    # 2. 提取 metadata（portfolio-risk-engine run() 返回的 metadata 通过 ctx.metadata 传递）
    meta = getattr(ctx, 'metadata', {}) or {}
    portfolio_metadata = meta.get("portfolio_metadata", {}) or {}

    # 3. 加载 FACTOR 产物路径（用于行业着色，可选）
    factor_path = ctx.get_artifact("FACTOR") if hasattr(ctx, 'get_artifact') else None

    # 4. 生成 HTML 报告
    try:
        html_content = build_portfolio_report(
            portfolio_artifact_path=portfolio_artifact,
            portfolio_metadata=portfolio_metadata,
            factor_path=factor_path,
            chart_theme=CHART_THEME,
        )

        html_path = os.path.join(_report_dir, "portfolio_report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"组合优化报告已生成: {html_path}")

        _update_report_portal(html_path, "portfolio", task_id=ctx.task_id if hasattr(ctx, 'task_id') else "")

        return {
            "success": True,
            "artifact_path": html_path,
            "metadata": {
                "report_type": "portfolio",
                "num_assets": portfolio_metadata.get("num_assets", 0),
                "optimization_method": portfolio_metadata.get("optimization_method", "unknown"),
            },
            "error": ""
        }
    except Exception as e:
        logger.exception("组合优化报告生成失败")
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": str(e)
        }


def _run_execution_report(ctx) -> Dict[str, Any]:
    """执行监控报告生成路径（F-P0-1 状态条 + F-P0-3 仪表盘 + F-P0-4 风控）。

    流程：
    1. 定位 EXECUTION 产物（trade_log.jsonl 所在目录）
    2. 从 ctx.metadata 提取 execution-monitor-engine 的 metadata
    3. 从 ctx.metadata 提取 portfolio-risk-engine 的 stop_signals（跨引擎引用）
    4. 调用 execution_report.build_execution_report 生成 HTML
    5. 落盘到 REPORT_DIR/execution_report.html

    数据缺失容错：任一上游字段缺失时显示「数据不可用」占位，不阻塞生成。
    """
    from scripts.templates.execution_report import build_execution_report

    _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    _report_dir = os.path.join(_work_dir, "reports")
    os.makedirs(_report_dir, exist_ok=True)

    # 1. 定位 EXECUTION 产物
    execution_artifact = ctx.get_artifact("EXECUTION") if hasattr(ctx, 'get_artifact') else None
    if not execution_artifact:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "未找到 EXECUTION 产物，无法生成执行监控报告。请先执行模拟/实盘交易。"
        }

    execution_dir = os.path.dirname(execution_artifact) if os.path.isfile(execution_artifact) else execution_artifact
    audit_log_path = os.path.join(execution_dir, "trade_log.jsonl")
    ledger_path = os.path.join(execution_dir, "ledger.jsonl")

    # 2. 提取 metadata
    meta = getattr(ctx, 'metadata', {}) or {}
    execution_metadata = meta.get("execution_metadata", {}) or {}

    # 3. 提取止损信号（跨引擎引用，来自 portfolio-risk-engine）
    portfolio_metadata = meta.get("portfolio_metadata", {}) or {}
    stop_signals = portfolio_metadata.get("stop_signals", {}) or {}

    # 4. 读取风控阈值（从环境变量或使用默认值）
    max_daily_loss_ratio = float(os.environ.get(
        "MAX_DAILY_LOSS_RATIO",
        os.environ.get("QUANT_MAX_DAILY_LOSS_RATIO", "0.02")
    ))
    max_single_order_ratio = float(os.environ.get(
        "MAX_SINGLE_ORDER_RATIO",
        os.environ.get("QUANT_MAX_SINGLE_ORDER_RATIO", "0.10")
    ))
    max_order_frequency = int(os.environ.get(
        "MAX_ORDER_FREQUENCY",
        os.environ.get("QUANT_MAX_ORDER_FREQUENCY", "2")
    ))

    # 5. 生成 HTML 报告
    try:
        html_content = build_execution_report(
            execution_metadata=execution_metadata,
            audit_log_path=audit_log_path,
            ledger_path=ledger_path,
            stop_signals=stop_signals,
            max_daily_loss_ratio=max_daily_loss_ratio,
            max_single_order_ratio=max_single_order_ratio,
            max_order_frequency=max_order_frequency,
        )

        html_path = os.path.join(_report_dir, "execution_report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"执行监控报告已生成: {html_path}")

        _update_report_portal(
            html_path, "execution",
            task_id=ctx.task_id if hasattr(ctx, 'task_id') else "",
            backend=execution_metadata.get("backend", ""),
        )

        return {
            "success": True,
            "artifact_path": html_path,
            "metadata": {
                "report_type": "execution",
                "mode": execution_metadata.get("mode", "paper"),
                "orders_executed": execution_metadata.get("orders_executed", 0),
                "orders_failed": execution_metadata.get("orders_failed", 0),
            },
            "error": ""
        }
    except Exception as e:
        logger.exception("执行监控报告生成失败")
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": str(e)
        }


def _run_attribution_report(ctx) -> Dict[str, Any]:
    """绩效归因报告生成路径

    流程：
    1. 读取 EXECUTION 产物（ledger.jsonl / trade_log.json）
    2. AttributionAnalyzer 解析和归因
    3. ReportGenerator 生成图表
    4. 渲染 HTML 报告（含 LLM 占位符）
    5. 调用 LLM 生成深度解读（可选）
    6. 替换占位符 → 输出最终报告
    """
    from scripts.attribution_analyzer import AttributionAnalyzer
    from scripts.templates.attribution_report import (
        build_attribution_html,
        make_pnl_by_stock_chart,
        make_round_trip_scatter,
    )

    _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    _report_dir = os.path.join(_work_dir, "reports")
    os.makedirs(_report_dir, exist_ok=True)

    # 1. 定位 EXECUTION 产物
    execution_artifact = ctx.get_artifact("EXECUTION") if hasattr(ctx, 'get_artifact') else None
    if not execution_artifact:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "未找到 EXECUTION 产物，无法生成绩效归因报告。请先执行模拟/实盘交易。"
        }

    # ledger.jsonl 位于 execution 目录下
    execution_dir = os.path.dirname(execution_artifact) if os.path.isfile(execution_artifact) else execution_artifact
    ledger_path = os.path.join(execution_dir, "ledger.jsonl")
    trade_log_path = os.path.join(execution_dir, "trade_log.json")

    if not os.path.exists(ledger_path):
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": f"ledger 文件不存在: {ledger_path}"
        }

    # 2. 初始化分析器
    analyzer = AttributionAnalyzer(ledger_path, trade_log_path)
    if not analyzer.load():
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "ledger 文件为空或无法解析"
        }

    analyzer.build_round_trips()

    # 3. 提取分析数据
    tx_stats = analyzer.get_transaction_stats()
    rt_stats = analyzer.get_round_trip_stats()
    pnl_by_stock = analyzer.get_pnl_by_stock()
    exec_quality = analyzer.get_execution_quality()
    stress_perf = analyzer.get_stress_period_performance()
    consecutive = analyzer.get_consecutive_stats()

    # 4. 生成图表
    generator = ReportGenerator(title="绩效归因报告")
    charts: List[str] = []

    # 净值曲线
    nav_series = analyzer.get_nav_series()
    metrics = {}
    if not nav_series.empty:
        equity_curve = pd.DataFrame({
            "date": nav_series.index,
            "equity": nav_series.values,
        })
        equity_chart = generator.make_equity_chart(equity_curve)
        if equity_chart:
            charts.append(equity_chart)

        # 月度热力图
        if INCLUDE_HEATMAP:
            heatmap = generator.make_monthly_heatmap(equity_curve)
            if heatmap:
                charts.append(heatmap)

        # 计算绩效指标
        metrics = generator.calc_performance_metrics(equity_curve)

    # 按标的盈亏图
    if not pnl_by_stock.empty:
        pnl_chart = make_pnl_by_stock_chart(pnl_by_stock, CHART_THEME)
        if pnl_chart:
            charts.append(pnl_chart)

    # Round-trip 散点图
    if analyzer.round_trips:
        rt_chart = make_round_trip_scatter(analyzer.round_trips, CHART_THEME)
        if rt_chart:
            charts.append(rt_chart)

    # 5. 构建 HTML
    html = build_attribution_html(
        metrics=metrics,
        tx_stats=tx_stats,
        rt_stats=rt_stats,
        pnl_by_stock=pnl_by_stock,
        exec_quality=exec_quality,
        stress_perf=stress_perf,
        consecutive=consecutive,
        charts=charts,
        round_trips=analyzer.round_trips,
        chart_theme=CHART_THEME,
    )

    # 6. 写入文件
    html_path = os.path.join(_report_dir, "attribution_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"绩效归因报告已生成: {html_path}")

    _update_report_portal(html_path, "attribution", task_id=ctx.task_id if hasattr(ctx, 'task_id') else "")

    # 7. 准备 LLM prompt
    llm_prompts = {
        "attribution": _build_attribution_llm_prompt(
            rt_stats, pnl_by_stock, exec_quality, stress_perf
        )
    }

    # 8. 尝试调用 LLM
    llm_status = "skipped"
    llm_responses: Dict[str, Any] = {}
    try:
        from scripts.llm_client import generate_analysis, is_available
        if is_available():
            logger.info("开始调用 LLM 生成绩效归因解读...")
            resp = generate_analysis(llm_prompts["attribution"])
            if resp:
                llm_responses["attribution"] = resp
                llm_status = "success"
            else:
                llm_status = "failed"
    except Exception as e:
        logger.warning(f"LLM 调用异常: {e}")
        llm_status = "failed"

    # 9. 替换占位符
    _inject_attribution_analysis(html_path, llm_responses, llm_prompts)

    # 10. 落盘 report_data.json
    report_data = {
        "report_type": "attribution",
        "generated_at": datetime.now().isoformat(),
        "metrics": metrics,
        "tx_stats": tx_stats,
        "rt_stats": rt_stats,
        "llm_status": llm_status,
    }
    data_path_out = os.path.join(_report_dir, "report_data.json")
    with open(data_path_out, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

    return {
        "success": True,
        "artifact_path": html_path,
        "metadata": {
            "report_type": "attribution",
            "llm_prompts": llm_prompts,
            "llm_status": llm_status,
            "metrics": metrics,
            "report_data_path": data_path_out,
        },
        "error": ""
    }


def run(ctx) -> Dict[str, Any]:
    """
    reports-engine 的 run 函数

    统一路由逻辑（三分支，按优先级）：
    1. 绩效复盘意图（report_intent == "attribution"）→ 绩效归因报告
    2. 有 BACKTEST 产物 → 回测绩效报告（夏普/回撤/归因）
    3. 默认 → 按报告模板生成个股分析报告（技术面/基本面）

    参数:
        ctx: Context 对象

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "metadata": {...},
            "error": str
        }
    """
    # 优先级 1: 绩效复盘意图 → 绩效归因报告（内置独立路由，保持行为一致）
    meta = getattr(ctx, 'metadata', {}) or {}
    if meta.get("report_intent") == "attribution":
        logger.info("检测到绩效复盘意图，生成绩效归因报告")
        return _run_attribution_report(ctx)

    # 优先级 2: 组合优化意图 → 组合优化报告
    if meta.get("report_intent") == "portfolio":
        logger.info("检测到组合优化意图，生成组合优化报告")
        return _run_portfolio_report(ctx)

    # 优先级 3: 执行监控意图 → 执行监控报告
    if meta.get("report_intent") == "execution":
        logger.info("检测到执行监控意图，生成执行监控报告")
        return _run_execution_report(ctx)

    # 优先级 4: 报告插件匹配（通过 plugin.yaml 的 trigger）。
    # 归因/组合/执行已有内置独立路由（优先级 1-3），插件机制补充承载其余报告
    # （技术/基本面/资本流等）。命中插件则优先走插件，未命中回退内置路由。
    if ENABLE_PLUGIN:
        try:
            _ensure_plugins_loaded()
            from plugins.registry import find_by_trigger as _plugin_find
            matched = _plugin_find(ctx)
            if matched:
                plugin = matched[0]
                logger.info(f"命中报告插件: [{plugin.id}] {plugin.label}")
                return _run_plugin(ctx, plugin)
        except Exception as e:
            logger.warning(f"报告插件匹配异常（已回退到内置报告）: {e}")

    # 优先级 5: 有 BACKTEST 产物 → 回测绩效报告
    backtest_path = ctx.get_artifact("BACKTEST") if hasattr(ctx, 'get_artifact') else None
    has_backtest = backtest_path and os.path.exists(backtest_path)

    if not has_backtest:
        # 优先级 6: 默认 → 模板化个股分析报告
        return _run_template_report(ctx)

    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        generator = ReportGenerator()

        portfolio_path = ctx.get_artifact("PORTFOLIO")
        data_path = ctx.get_artifact("DATA")
        factor_path = ctx.get_artifact("FACTOR")

        equity_curve = pd.DataFrame()
        if backtest_path and os.path.exists(backtest_path):
            equity_path = os.path.join(os.path.dirname(backtest_path), "equity_curve.parquet")
            if os.path.exists(equity_path):
                equity_curve = pd.read_parquet(equity_path)
                logger.info(f"加载回测净值曲线: {len(equity_curve)} 行")

        if equity_curve.empty and data_path and os.path.exists(data_path):
            data = pd.read_parquet(data_path)
            pivot = data.pivot(index='date', columns='code', values='close')
            eq = pivot.mean(axis=1)
            eq = eq / eq.iloc[0]
            equity_curve = pd.DataFrame({'date': eq.index, 'equity': eq * 1e6})
            logger.info("从行情数据生成模拟净值")

        if equity_curve.empty:
            logger.warning("无可用净值数据")

        metrics = generator.calc_performance_metrics(equity_curve)
        generator.metrics = metrics

        equity_chart = generator.make_equity_chart(equity_curve)
        if equity_chart:
            generator.charts.append(equity_chart)

        if INCLUDE_HEATMAP:
            heatmap = generator.make_monthly_heatmap(equity_curve)
            if heatmap:
                generator.charts.append(heatmap)

        # ── 补充 quantstats 独有维度：滚动收益 ──
        # 注：回撤可视化已由三联动图（净值+回撤+暴露度）的中间面板覆盖，
        # 不再单独追加水下曲线图，避免回撤图表重复。
        rolling_chart = generator.make_rolling_return_chart(equity_curve)
        if rolling_chart:
            generator.charts.append(rolling_chart)

        if INCLUDE_ATTRIBUTION:
            # ── M2/M3: 因子归因（基于回测持仓权重，而非全股票池截面） ──
            bt_dir = os.path.dirname(backtest_path) if backtest_path else ""
            pw_path = os.path.join(bt_dir, "portfolio_weights.parquet") if bt_dir else ""
            fe_path = os.path.join(bt_dir, "factor_exposures.parquet") if bt_dir else ""

            portfolio_weights = pd.DataFrame()
            if pw_path and os.path.exists(pw_path):
                try:
                    portfolio_weights = pd.read_parquet(pw_path)
                except Exception as e:
                    logger.warning("读取 portfolio_weights 失败: %s", e)

            factor_exposures = pd.DataFrame()
            if fe_path and os.path.exists(fe_path):
                try:
                    factor_exposures = pd.read_parquet(fe_path)
                except Exception as e:
                    logger.warning("读取 factor_exposures 失败: %s", e)

            # 持仓股票数 → 决定截面归因 or 时序归因
            n_held = portfolio_weights["code"].nunique() if not portfolio_weights.empty else 0
            is_single = n_held < 5

            factor_df = pd.DataFrame()
            if factor_path and os.path.exists(factor_path):
                try:
                    factor_df = pd.read_parquet(factor_path)
                except Exception as e:
                    logger.warning("读取 factor_data 失败: %s", e)

            style_map = {
                "size": "市值", "value": "价值", "momentum": "动量",
                "volatility": "波动率", "quality": "质量", "growth": "成长",
            }
            style_cols = [c for c in style_map if c in factor_df.columns]

            if is_single:
                # ── 单股/少股：时序因子暴露（单图 + 下拉切换因子，避免页面过长） ──
                logger.info("M3 归因: 单股/少股模式（持仓 %d 只），展示时序因子暴露（单图切换）", n_held)
                ts_cols = [c for c in ["alpha_score"] + style_cols if c in factor_exposures.columns]
                ts_source = factor_exposures
                if not ts_cols and not factor_df.empty:
                    # 兜底：用持仓组合的逐日加权暴露（无 M2 产物时）
                    try:
                        ts_source = _compute_portfolio_exposure_timeseries(
                            portfolio_weights, factor_df,
                            ["alpha_score"] + style_cols)
                        ts_cols = [c for c in ["alpha_score"] + style_cols if c in ts_source.columns]
                    except Exception as e:
                        logger.warning("时序暴露计算失败: %s", e)
                if ts_cols:
                    ts_labels = {c: style_map.get(c, c) for c in ts_cols}
                    # ── 净值曲线(上) + 回撤(中) + 因子暴露(下) 联动三面板：替换独立净值图，不再追加独立多图 ──
                    linked_chart = generator.make_equity_exposure_linked_chart(
                        equity_curve, ts_source, ts_cols, ts_labels)
                    if linked_chart:
                        # 净值图总是 charts 的首个元素，用联动图替换它
                        if generator.charts:
                            generator.charts[0] = linked_chart
                        else:
                            generator.charts.append(linked_chart)
                    else:
                        # 联动图生成失败时回退：保留独立净值图，追加独立多图
                        multi_chart = generator.make_factor_exposure_multi_chart(
                            ts_source, ts_cols, ts_labels)
                        if multi_chart:
                            generator.charts.append(multi_chart)
            else:
                # ── 多股：持仓权重加权风格暴露 + 行业归因 ──
                logger.info("M3 归因: 多股模式（持仓 %d 只），展示截面归因", n_held)
                # 风格暴露：取最新一期的组合持仓加权暴露
                style_exposures = {}
                if not factor_exposures.empty:
                    latest = factor_exposures.sort_values("date")
                    if not latest.empty:
                        last_row = latest.iloc[-1]
                        for col in style_cols:
                            if col in last_row.index and pd.notna(last_row[col]):
                                style_exposures[style_map[col]] = float(last_row[col])
                if style_exposures:
                    style_chart = generator.make_style_exposure_chart(style_exposures)
                    if style_chart:
                        generator.charts.append(style_chart)

                # 行业归因：按持仓行业分组算 alpha 贡献
                industry_contributions = {}
                if "industry" in factor_df.columns and "alpha_score" in factor_df.columns:
                    latest_factor = factor_df[factor_df['date'] == factor_df['date'].max()]
                    if not portfolio_weights.empty:
                        held = set(portfolio_weights['code'].unique())
                        latest_factor = latest_factor[latest_factor['code'].isin(held)]
                    for ind in latest_factor['industry'].dropna().unique()[:10]:
                        ind_data = latest_factor[latest_factor['industry'] == ind]
                        industry_contributions[ind] = float(ind_data['alpha_score'].mean())
                if industry_contributions:
                    ind_chart = generator.make_industry_attribution_chart(industry_contributions)
                    if ind_chart:
                        generator.charts.append(ind_chart)

        # ── M4: 策略因子清单章节（交叉引用 strategy_factors.json） ──
        _sf_path = os.path.join(os.path.dirname(backtest_path), "strategy_factors.json") if backtest_path else ""
        if _sf_path and os.path.exists(_sf_path):
            try:
                with open(_sf_path, "r", encoding="utf-8") as _f:
                    _sf = json.load(_f)
                _factors = _sf.get("factors", [])
                if _factors:
                    _rows = []
                    for _it in _factors:
                        _name = _it.get("name", "?")
                        _verdict = _it.get("verdict", "NA")
                        _cls = {"ACCEPT": "signal-bullish", "REVIEW": "signal-warning",
                                "REJECT": "signal-bearish"}.get(_verdict, "signal-neutral")
                        _rows.append(
                            f'<tr><td><b>{_name}</b></td>'
                            f'<td><span class="signal-tag {_cls}">{_verdict}</span></td></tr>'
                        )
                    _filtered = "已启用（仅用 ACCEPT/REVIEW）" if _sf.get("alphalens_filter") else "未启用（全部因子）"
                    generator.factor_summary_html = (
                        f'<p class="analysis-hint">策略构建使用的因子清单 '
                        f'（alphalens 有效性筛选：{_filtered}）</p>'
                        f'<table><thead><tr><th>因子</th><th>alphalens 判定</th></tr></thead>'
                        f'<tbody>{"".join(_rows)}</tbody></table>'
                    )
            except Exception as _e:
                logger.warning("M4: 读取 strategy_factors.json 失败: %s", _e)

        html_report = generator.build_html_report()
        html_path = os.path.join(REPORT_DIR, "report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_report)
        logger.info(f"HTML 报告已生成: {html_path}")

        _update_report_portal(html_path, "backtest", task_id=ctx.task_id if hasattr(ctx, 'task_id') else "")

        report_data = {
            "title": REPORT_TITLE,
            "generated_at": datetime.now().isoformat(),
            "benchmark": BENCHMARK,
            "metrics": metrics,
            "num_charts": len(generator.charts),
        }
        data_path_out = os.path.join(REPORT_DIR, "report_data.json")
        with open(data_path_out, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

        return {
            "success": True,
            "artifact_path": html_path,
            "metadata": {
                "metrics": metrics,
                "report_data_path": data_path_out,
                "num_charts": len(generator.charts),
            },
            "error": ""
        }

    except Exception as e:
        logger.exception("报告引擎执行失败")
        return {
            "success": False,
            "artifact_path": "",
            "metadata": {},
            "error": str(e)
        }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            ctx_dict = json.load(f)
        from scripts.context import Context
        ctx = Context.from_dict(ctx_dict)
    else:
        from scripts.context import Context
        ctx = Context(
            task_id="test_report",
            stock_pool=[],
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        ctx.update_artifact("DATA", "./workspace/data/cleaned_data.parquet")
        ctx.update_artifact("FACTOR", "./workspace/factors/factor_data.parquet")

    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
