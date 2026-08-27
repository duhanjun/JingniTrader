# -*- coding: utf-8 -*-
"""回测/归因绩效可视化报告生成器（公共库，阶段 B 自包含化提取）。

从 engine.py 中提取的 ReportGenerator 类，供 backtest_report 插件与
attribution_report 插件共同复用（二者原本都依赖 engine.py 内的该类）。

依赖的公共常量来自 scripts.config：
    REPORT_TITLE, RISK_FREE_RATE, BENCHMARK, CHART_THEME, INDUSTRY_STANDARD
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from scripts.config import (
    REPORT_TITLE,
    RISK_FREE_RATE,
    BENCHMARK,
    CHART_THEME,
    INDUSTRY_STANDARD,
)
from scripts.templates.common_components import render_page


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
        """净值曲线（上）+ 回撤（中）+ 组合因子暴露时序（下）联动三面板图。"""
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
        """生成滚动收益曲线（rolling window 累计收益）。"""
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
        """生成组合在单个因子上的逐日暴露时序折线图（单股/少股模式）。"""
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
        """生成组合因子暴露时序图（单图 + 下拉切换因子）。"""
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
