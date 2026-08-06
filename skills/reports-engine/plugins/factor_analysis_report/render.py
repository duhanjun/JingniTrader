# -*- coding: utf-8 -*-
"""因子分析报告插件 — 渲染器（注册性质）。

因子分析报告由 reports-engine 的 _maybe_render_factor_analysis_report 作为个股分析
流程的附加产物生成（非独立路由）。本插件仅注册到插件框架，默认不拦截内置路由。
此 render 保留为接口兼容（实际不触发）。
"""
from __future__ import annotations

from typing import Any, Dict


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """占位渲染（实际因子分析报告由个股分析流程附带生成）。"""
    raise RuntimeError(
        "factor_analysis_report 插件不应直接触发；因子分析报告为个股分析流程附加产物。"
    )
