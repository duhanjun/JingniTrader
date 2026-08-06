# -*- coding: utf-8 -*-
"""执行监控报告插件 — 渲染器（复用 engine._run_execution_report）。"""
from __future__ import annotations

from typing import Any, Dict


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染执行监控报告。"""
    from _engine_access import run_engine_report
    return run_engine_report("_run_execution_report", ctx, output_path)
