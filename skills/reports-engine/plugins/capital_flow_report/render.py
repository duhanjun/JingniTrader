# -*- coding: utf-8 -*-
"""资金流分析报告插件 — 渲染器（示例）。

演示报告插件统一接口 render(data, ctx, output_path)：
从 DATA 产物计算量价代理指标（此处以成交量/涨跌模拟资金流），
继承 base.html.j2 渲染统一骨架报告，并写入 output_path。
"""
from __future__ import annotations

import os
from typing import Any, Dict

import pandas as pd


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染资金流分析报告。

    参数:
        data: 按 requires 收集的产物数据，含 "DATA"（行情 DataFrame）
        ctx:  Context 对象
        output_path: 输出 HTML 路径
    """
    from scripts.templates.common_components import render_page as _render_page

    price_df = data.get("DATA")
    stock_code = (ctx.stock_pool[0] if getattr(ctx, "stock_pool", None) else "") or "000001.SZ"

    # ── 从行情数据计算量价代理指标（演示资金流） ──
    main_inflow = "—"
    north_inflow = "—"
    vol_trend = "—"
    if price_df is not None and not price_df.empty:
        df = price_df.copy()
        if "date" in df.columns and "volume" in df.columns:
            # 近5日 vs 前20日成交量均值，作为"资金活跃度"代理
            vol = df["volume"].astype(float)
            vol5 = float(vol.tail(5).mean()) if len(vol) >= 5 else float(vol.mean())
            vol20 = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean())
            ratio = vol5 / vol20 if vol20 > 0 else 1.0
            vol_trend = f"{ratio:.2f}×"
            main_inflow = f"{ratio * 0.8:.2f}亿"
            north_inflow = f"{ratio * 0.15:.2f}亿"
        else:
            main_inflow = f"{float(len(df)) / 100:.2f}亿"

    # ── 计算 K 线（供图表演示） ──
    charts = ""
    try:
        import plotly.graph_objects as go
        if price_df is not None and not price_df.empty and "close" in price_df.columns:
            p = price_df.sort_values("date")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=p["date"], y=p["close"], mode="lines",
                                     name="收盘价", line=dict(color="#2ca02c", width=2)))
            fig.update_layout(title=None, height=320, template="plotly_white",
                              yaxis_title="价格", xaxis_title="日期")
            charts = fig.to_html(full_html=False, include_plotlyjs="cdn")
    except Exception:
        charts = ""

    # ── 渲染（继承 base.html.j2 统一骨架） ──
    #    插件模板在插件目录下，需通过 extra_template_dirs 追加搜索路径
    html = _render_page(
        "capital_flow_report.html.j2",
        extra_template_dirs=[os.path.dirname(os.path.abspath(__file__))],
        stock_code=stock_code,
        main_inflow=main_inflow,
        north_inflow=north_inflow,
        vol_trend=vol_trend,
        charts=charts,
        title=f"{stock_code} 资金流分析报告",
        subtitle="由报告插件框架生成（示例插件 capital_flow_report）",
    )

    # 写入输出文件
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html
