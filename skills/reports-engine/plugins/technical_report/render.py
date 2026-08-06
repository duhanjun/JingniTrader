# -*- coding: utf-8 -*-
"""技术分析报告插件 — 渲染器。

内置报告插件化迁移示范：复用 reports-engine 的 ReportTemplateEngine，
完整调用技术分析报告生成逻辑（指标计算/模板渲染），保证插件输出与内置一致。
"""
from __future__ import annotations

import os
from typing import Any, Dict


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染技术分析报告。

    参数:
        data: 按 requires 收集的产物（含 "DATA" 行情路径）
        ctx:  Context 对象
        output_path: 输出 HTML 路径
    """
    from scripts.template_engine import ReportTemplateEngine

    # 复用内置技术报告生成逻辑（读 DATA 产物 + 计算指标 + 渲染 technical.html.j2）
    engine = ReportTemplateEngine()
    result = engine.generate("technical", ctx, output_path)

    if not result.get("success"):
        raise RuntimeError(result.get("error", "技术分析报告生成失败"))

    # 读取生成好的 HTML 返回（供插件框架统一处理）
    artifact = result.get("artifact_path") or output_path
    if os.path.exists(artifact):
        with open(artifact, "r", encoding="utf-8") as f:
            return f.read()

    # generate 未写文件（异常兜底）
    raise RuntimeError("技术分析报告生成后未找到输出文件")
