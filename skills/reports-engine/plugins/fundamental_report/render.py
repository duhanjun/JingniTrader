# -*- coding: utf-8 -*-
"""基本面分析报告插件 — 渲染器。

复用 reports-engine 的 ReportTemplateEngine，完整调用基本面分析报告生成逻辑，
保证插件输出与内置一致。
"""
from __future__ import annotations

import os
from typing import Any, Dict


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染基本面分析报告。"""
    from scripts.template_engine import ReportTemplateEngine

    engine = ReportTemplateEngine()
    result = engine.generate("fundamental", ctx, output_path)

    if not result.get("success"):
        raise RuntimeError(result.get("error", "基本面分析报告生成失败"))

    artifact = result.get("artifact_path") or output_path
    if os.path.exists(artifact):
        with open(artifact, "r", encoding="utf-8") as f:
            return f.read()

    raise RuntimeError("基本面分析报告生成后未找到输出文件")
