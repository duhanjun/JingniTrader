# -*- coding: utf-8 -*-
"""策略回测报告插件 — 渲染器（阶段 C：自包含）。

自包含化：本插件不再依赖 engine.py 内部函数。复用公共库
（scripts.report_generator.ReportGenerator、scripts.config、scripts.templates
.common_components）与本插件 backtest_helpers 完成回测绩效报告全流程：
读 BACKTEST 产物 → 净值/指标/图表 → 归因 → M4 因子清单 → 渲染 backtest.html.j2
→ 写 report.html + report_data.json。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# 确保能导入同目录的 backtest_helpers 辅助模块
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import pandas as pd

from scripts.report_generator import ReportGenerator
from scripts.config import (
    REPORT_DIR,
    REPORT_TITLE,
    BENCHMARK,
    INCLUDE_HEATMAP,
    INCLUDE_ATTRIBUTION,
)
from backtest_helpers import compute_portfolio_exposure_timeseries

# 行业/风格映射（M3 归因用）
_STYLE_MAP = {
    "size": "市值", "value": "价值", "momentum": "动量",
    "volatility": "波动率", "quality": "质量", "growth": "成长",
}


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染策略回测绩效报告。

    参数:
        data: 按 requires 收集的产物（含 "BACKTEST" 路径）
        ctx:  Context 对象
        output_path: 输出 HTML 路径（report.html）
    """
    backtest_path = ctx.get_artifact("BACKTEST") if hasattr(ctx, 'get_artifact') else None
    if not backtest_path or not os.path.exists(backtest_path):
        raise RuntimeError("未找到 BACKTEST 产物，无法生成回测报告。请先执行回测。")

    portfolio_path = ctx.get_artifact("PORTFOLIO")
    data_path = ctx.get_artifact("DATA")
    factor_path = ctx.get_artifact("FACTOR")

    generator = ReportGenerator()

    # ── 净值曲线 ──
    equity_curve = pd.DataFrame()
    if backtest_path and os.path.exists(backtest_path):
        equity_path = os.path.join(os.path.dirname(backtest_path), "equity_curve.parquet")
        if os.path.exists(equity_path):
            equity_curve = pd.read_parquet(equity_path)

    if equity_curve.empty and data_path and os.path.exists(data_path):
        data = pd.read_parquet(data_path)
        pivot = data.pivot(index='date', columns='code', values='close')
        eq = pivot.mean(axis=1)
        eq = eq / eq.iloc[0]
        equity_curve = pd.DataFrame({'date': eq.index, 'equity': eq * 1e6})

    metrics = generator.calc_performance_metrics(equity_curve)
    generator.metrics = metrics

    equity_chart = generator.make_equity_chart(equity_curve)
    if equity_chart:
        generator.charts.append(equity_chart)

    if INCLUDE_HEATMAP:
        heatmap = generator.make_monthly_heatmap(equity_curve)
        if heatmap:
            generator.charts.append(heatmap)

    rolling_chart = generator.make_rolling_return_chart(equity_curve)
    if rolling_chart:
        generator.charts.append(rolling_chart)

    if INCLUDE_ATTRIBUTION:
        # ── M2/M3: 因子归因（基于回测持仓权重） ──
        bt_dir = os.path.dirname(backtest_path) if backtest_path else ""
        pw_path = os.path.join(bt_dir, "portfolio_weights.parquet") if bt_dir else ""
        fe_path = os.path.join(bt_dir, "factor_exposures.parquet") if bt_dir else ""

        portfolio_weights = pd.DataFrame()
        if pw_path and os.path.exists(pw_path):
            try:
                portfolio_weights = pd.read_parquet(pw_path)
            except Exception:
                pass

        factor_exposures = pd.DataFrame()
        if fe_path and os.path.exists(fe_path):
            try:
                factor_exposures = pd.read_parquet(fe_path)
            except Exception:
                pass

        n_held = portfolio_weights["code"].nunique() if not portfolio_weights.empty else 0
        is_single = n_held < 5

        factor_df = pd.DataFrame()
        if factor_path and os.path.exists(factor_path):
            try:
                factor_df = pd.read_parquet(factor_path)
            except Exception:
                pass

        style_cols = [c for c in _STYLE_MAP if c in factor_df.columns]

        if is_single:
            # ── 单股/少股：时序因子暴露（单图 + 下拉切换） ──
            ts_cols = [c for c in ["alpha_score"] + style_cols if c in factor_exposures.columns]
            ts_source = factor_exposures
            if not ts_cols and not factor_df.empty:
                try:
                    ts_source = compute_portfolio_exposure_timeseries(
                        portfolio_weights, factor_df,
                        ["alpha_score"] + style_cols)
                    ts_cols = [c for c in ["alpha_score"] + style_cols if c in ts_source.columns]
                except Exception:
                    pass
            if ts_cols:
                ts_labels = {c: _STYLE_MAP.get(c, c) for c in ts_cols}
                linked_chart = generator.make_equity_exposure_linked_chart(
                    equity_curve, ts_source, ts_cols, ts_labels)
                if linked_chart:
                    if generator.charts:
                        generator.charts[0] = linked_chart
                    else:
                        generator.charts.append(linked_chart)
                else:
                    multi_chart = generator.make_factor_exposure_multi_chart(
                        ts_source, ts_cols, ts_labels)
                    if multi_chart:
                        generator.charts.append(multi_chart)
        else:
            # ── 多股：持仓权重加权风格暴露 + 行业归因 ──
            style_exposures = {}
            if not factor_exposures.empty:
                latest = factor_exposures.sort_values("date")
                if not latest.empty:
                    last_row = latest.iloc[-1]
                    for col in style_cols:
                        if col in last_row.index and pd.notna(last_row[col]):
                            style_exposures[_STYLE_MAP[col]] = float(last_row[col])
            if style_exposures:
                style_chart = generator.make_style_exposure_chart(style_exposures)
                if style_chart:
                    generator.charts.append(style_chart)

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
        except Exception:
            pass

    # ── 渲染 HTML ──
    html_report = generator.build_html_report()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_report)

    # ── 落盘 report_data.json ──
    _report_dir = os.path.dirname(output_path)
    report_data = {
        "title": REPORT_TITLE,
        "generated_at": datetime.now().isoformat(),
        "benchmark": BENCHMARK,
        "metrics": metrics,
        "num_charts": len(generator.charts),
    }
    data_path_out = os.path.join(_report_dir, "report_data.json")
    with open(data_path_out, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

    return html_report
