# -*- coding: utf-8 -*-
"""组合优化报告插件 — 渲染器（阶段 B：自包含）。

自包含化：本插件不再依赖 engine.py 内部函数 / _engine_access。
复用公共库（scripts.config.CHART_THEME + scripts.templates.portfolio_report），
在插件内完成：定位 PORTFOLIO 产物 → 提取 metadata → 生成 HTML → 写文件。

数据缺失容错：任一上游字段缺失时显示「数据不可用」占位，不阻塞生成。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from scripts.config import CHART_THEME
from scripts.templates.portfolio_report import build_portfolio_report


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染组合优化报告。

    参数:
        data: 按 requires 收集的产物（含 "PORTFOLIO" 路径）
        ctx:  Context 对象
        output_path: 输出 HTML 路径
    """
    # 1. 定位 PORTFOLIO 产物
    portfolio_artifact = ctx.get_artifact("PORTFOLIO") if hasattr(ctx, 'get_artifact') else None
    if not portfolio_artifact:
        raise RuntimeError("未找到 PORTFOLIO 产物，无法生成组合优化报告。请先执行组合优化。")

    # 2. 提取 metadata（portfolio-risk-engine run() 返回的 metadata 通过 ctx.metadata 传递）
    meta = getattr(ctx, 'metadata', {}) or {}
    portfolio_metadata = meta.get("portfolio_metadata", {}) or {}

    # 3. 加载 FACTOR 产物路径（用于行业着色，可选）
    factor_path = ctx.get_artifact("FACTOR") if hasattr(ctx, 'get_artifact') else None

    # 4. 生成 HTML 报告
    html_content = build_portfolio_report(
        portfolio_artifact_path=portfolio_artifact,
        portfolio_metadata=portfolio_metadata,
        factor_path=factor_path,
        chart_theme=CHART_THEME,
    )

    # 5. 写入输出文件
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return html_content
