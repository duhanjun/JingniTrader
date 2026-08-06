# -*- coding: utf-8 -*-
"""策略回测报告插件 — 渲染器（注册性质）。

回测报告生成逻辑内联在 reports-engine engine.py 的 run() 中（内置路由优先级5），
本插件仅注册到插件框架，不拦截内置路由。此 render 保留为接口兼容（实际不触发）。
"""
from __future__ import annotations

from typing import Any, Dict


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """占位渲染（实际回测报告由内置路由生成）。"""
    raise RuntimeError(
        "backtest_report 插件不应直接触发；回测报告由内置路由生成。"
    )
