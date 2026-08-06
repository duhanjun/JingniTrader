# -*- coding: utf-8 -*-
"""组合优化报告插件 — 渲染器（复用 engine._run_portfolio_report）。"""
from __future__ import annotations

from typing import Any, Dict


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染组合优化报告。"""
    from _engine_access import run_engine_report
    return run_engine_report("_run_portfolio_report", ctx, output_path)
