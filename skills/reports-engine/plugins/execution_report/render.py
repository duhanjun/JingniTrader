# -*- coding: utf-8 -*-
"""执行监控报告插件 — 渲染器（阶段 B：自包含）。

自包含化：本插件不再依赖 engine.py 内部函数 / _engine_access。
复用公共库（scripts.templates.execution_report），
在插件内完成：定位 EXECUTION 产物 → 提取 metadata/止损信号 → 读风控阈值
→ 生成 HTML → 写文件。

数据缺失容错：任一上游字段缺失时显示「数据不可用」占位，不阻塞生成。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from scripts.templates.execution_report import build_execution_report


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染执行监控报告。

    参数:
        data: 按 requires 收集的产物（含 "EXECUTION" 路径）
        ctx:  Context 对象
        output_path: 输出 HTML 路径
    """
    # 1. 定位 EXECUTION 产物
    execution_artifact = ctx.get_artifact("EXECUTION") if hasattr(ctx, 'get_artifact') else None
    if not execution_artifact:
        raise RuntimeError("未找到 EXECUTION 产物，无法生成执行监控报告。请先执行模拟/实盘交易。")

    execution_dir = os.path.dirname(execution_artifact) if os.path.isfile(execution_artifact) else execution_artifact
    audit_log_path = os.path.join(execution_dir, "trade_log.jsonl")
    ledger_path = os.path.join(execution_dir, "ledger.jsonl")

    # 2. 提取 metadata
    # 主引擎（engine.py run_pipeline 第 609 行）将 EXECUTION 阶段的 metadata 存入
    # ctx.metadata["EXECUTION"]（而非 "execution_metadata"）。此前读取 key 不匹配，
    # 导致 mode/backend/账户快照全部丢失、报告 mode 永远回退为 paper。
    # 优先读 "EXECUTION"，并兼容旧 "execution_metadata"。
    meta = getattr(ctx, 'metadata', {}) or {}
    execution_metadata = (
        meta.get("EXECUTION") if isinstance(meta.get("EXECUTION"), dict) else {}
    ) or (meta.get("execution_metadata") if isinstance(meta.get("execution_metadata"), dict) else {})

    # 3. 提取止损信号（跨引擎引用，来自 portfolio-risk-engine）
    portfolio_metadata = meta.get("portfolio_metadata", {}) or {}
    stop_signals = portfolio_metadata.get("stop_signals", {}) or {}

    # 4. 读取风控阈值（从环境变量或使用默认值）
    max_daily_loss_ratio = float(os.environ.get(
        "MAX_DAILY_LOSS_RATIO",
        os.environ.get("QUANT_MAX_DAILY_LOSS_RATIO", "0.02")
    ))
    max_single_order_ratio = float(os.environ.get(
        "MAX_SINGLE_ORDER_RATIO",
        os.environ.get("QUANT_MAX_SINGLE_ORDER_RATIO", "0.10")
    ))
    max_order_frequency = int(os.environ.get(
        "MAX_ORDER_FREQUENCY",
        os.environ.get("QUANT_MAX_ORDER_FREQUENCY", "2")
    ))

    # 5. 生成 HTML 报告
    html_content = build_execution_report(
        execution_metadata=execution_metadata,
        audit_log_path=audit_log_path,
        ledger_path=ledger_path,
        stop_signals=stop_signals,
        max_daily_loss_ratio=max_daily_loss_ratio,
        max_single_order_ratio=max_single_order_ratio,
        max_order_frequency=max_order_frequency,
    )

    # 6. 写入输出文件
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return html_content
