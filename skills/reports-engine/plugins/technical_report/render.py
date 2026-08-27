# -*- coding: utf-8 -*-
"""技术分析报告插件 — 渲染器（阶段 A：自包含）。

自包含化：本插件不再依赖 engine.py 内部函数 / 已移除的模板引擎 HTML 组装入口。
而是复用 reports-engine 的「公共计算」compute_report_data（读 DATA/FACTOR →
选股 → 渲染行情/因子/深度/风险章节），再用本插件自带的 technical_report.html.j2
模板渲染，输出与内置 _assemble_html 一致。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict

from scripts.template_engine import compute_report_data
from scripts.templates.common_components import (
    build_nav_bar_css,
    build_nav_bar_html,
    build_footer_html,
    render_page,
)

# 技术分析报告定制免责声明（与内置 _assemble_html 一致）
_DISCLAIMER = (
    "本报告由 JingniTrader 基于历史行情数据自动生成，仅供学习研究用途，不构成任何投资建议。"
    "技术分析指标（均线、MACD、RSI、KDJ 等）均基于历史价格计算，具有滞后性，"
    "对未来走势的预测存在较大不确定性。技术信号可能因市场环境变化而失效，"
    "请勿仅依据本报告技术分析结论进行交易决策，实盘交易有风险，请谨慎操作。"
)


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染技术分析报告。

    参数:
        data: 按 requires 收集的产物（含 "DATA" 行情路径）
        ctx:  Context 对象
        output_path: 输出 HTML 路径
    """
    # 1. 公共计算：读 DATA/FACTOR → 选股 → 渲染各章节
    comp = compute_report_data("technical", ctx)
    if not comp.get("success"):
        raise RuntimeError(comp.get("error", "技术分析报告数据计算失败"))

    # 2. 组装公共组件（与内置 _assemble_html 一致）
    nav_css = build_nav_bar_css()
    nav_html = build_nav_bar_html(report_title="")
    footer_html = build_footer_html(disclaimer=_DISCLAIMER)
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 3. 用本插件模板渲染（完整独立 HTML）
    html = render_page(
        "technical_report.html.j2",
        extra_template_dirs=[os.path.dirname(os.path.abspath(__file__))],
        report_title=comp.get("template_name", "技术分析报告"),
        stock_code=comp["stock_code"],
        stock_name=comp["stock_name"],
        current_price=comp["current_price"],
        data_date=comp["data_date"],
        generated_at=generated_at,
        nav_css=nav_css,
        nav_html=nav_html,
        footer_html=footer_html,
        market_html=comp["market_html"],
        factor_sections_html=comp["factor_sections_html"],
        deep_html=comp["deep_html"],
        risk_html=comp["risk_html"],
    )

    # 4. 写入输出文件（去除模板文件自带的末尾换行，与内置 _assemble_html 输出逐字节一致）
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    html = html.rstrip("\n")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html
