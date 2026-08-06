# -*- coding: utf-8 -*-
"""插件访问 reports-engine 引擎的内部生成函数的辅助工具。

内置报告插件需要复用 engine.py 中的 _run_attribution_report / _run_portfolio_report /
_run_execution_report 等生成逻辑。本模块提供稳定加载 engine 模块的方式，
并封装"调用生成函数 → 返回 HTML 内容"的通用流程。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, Dict


def _load_engine_module():
    """加载 reports-engine/engine.py 为独立模块并返回。

    通过绝对路径加载，避免依赖外部 sys.modules 状态（脚本包切换）。
    返回 engine 模块对象。
    """
    engine_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine.py")
    module_name = "reports_engine_engine_plugin_access"
    # 已加载则直接返回
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, engine_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_engine_report(func_name: str, ctx, output_path: str) -> str:
    """调用 engine.py 中的生成函数，返回生成的 HTML 内容。

    参数:
        func_name: engine.py 中的函数名（如 _run_attribution_report）
        ctx:       Context 对象
        output_path: 预期输出路径（仅用于校验产物存在）
    返回:
        生成的 HTML 字符串（从生成函数的 artifact_path 读取）。
    """
    mod = _load_engine_module()
    func = getattr(mod, func_name, None)
    if func is None:
        raise RuntimeError(f"engine.py 中不存在函数 {func_name}")

    result = func(ctx)
    if not result or not result.get("success"):
        raise RuntimeError(result.get("error") if isinstance(result, dict) else "报告生成失败")

    artifact = result.get("artifact_path") or output_path
    if os.path.exists(artifact):
        with open(artifact, "r", encoding="utf-8") as f:
            return f.read()

    raise RuntimeError(f"报告生成后未找到输出文件: {artifact}")
