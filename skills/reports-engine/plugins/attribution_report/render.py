# -*- coding: utf-8 -*-
"""绩效归因报告插件 — 渲染器（阶段 B：自包含）。

自包含化：本插件不再依赖 engine.py 内部函数 / _engine_access。
复用公共库（scripts.attribution_analyzer、scripts.report_generator、
scripts.templates.attribution_report、scripts.config），
在插件内完成归因全流程：读 EXECUTION → AttributionAnalyzer → 图表 → 渲染 →
LLM 解读 → 注入 → 写文件。
归因专属 LLM prompt/注入逻辑迁入本插件 attribution_llm 模块。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# 确保能导入同目录的 attribution_llm 辅助模块
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import pandas as pd

from scripts.attribution_analyzer import AttributionAnalyzer
from scripts.report_generator import ReportGenerator
from scripts.templates.attribution_report import (
    build_attribution_html,
    make_pnl_by_stock_chart,
    make_round_trip_scatter,
)
from scripts.config import CHART_THEME, INCLUDE_HEATMAP
from attribution_llm import (
    build_attribution_llm_prompt,
    inject_attribution_analysis,
)


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染绩效归因报告。

    参数:
        data: 按 requires 收集的产物（含 "EXECUTION" 路径）
        ctx:  Context 对象
        output_path: 输出 HTML 路径
    """
    # 1. 定位 EXECUTION 产物
    execution_artifact = ctx.get_artifact("EXECUTION") if hasattr(ctx, 'get_artifact') else None
    if not execution_artifact:
        raise RuntimeError("未找到 EXECUTION 产物，无法生成绩效归因报告。请先执行模拟/实盘交易。")

    # ledger.jsonl 位于 execution 目录下
    execution_dir = os.path.dirname(execution_artifact) if os.path.isfile(execution_artifact) else execution_artifact
    ledger_path = os.path.join(execution_dir, "ledger.jsonl")
    trade_log_path = os.path.join(execution_dir, "trade_log.json")

    if not os.path.exists(ledger_path):
        raise RuntimeError(f"ledger 文件不存在: {ledger_path}")

    # 2. 初始化分析器
    analyzer = AttributionAnalyzer(ledger_path, trade_log_path)
    if not analyzer.load():
        raise RuntimeError("ledger 文件为空或无法解析")

    analyzer.build_round_trips()

    # 3. 提取分析数据
    tx_stats = analyzer.get_transaction_stats()
    rt_stats = analyzer.get_round_trip_stats()
    pnl_by_stock = analyzer.get_pnl_by_stock()
    exec_quality = analyzer.get_execution_quality()
    stress_perf = analyzer.get_stress_period_performance()
    consecutive = analyzer.get_consecutive_stats()

    # 4. 生成图表
    generator = ReportGenerator(title="绩效归因报告")
    charts: List[str] = []

    # 净值曲线
    nav_series = analyzer.get_nav_series()
    metrics = {}
    if not nav_series.empty:
        equity_curve = pd.DataFrame({
            "date": nav_series.index,
            "equity": nav_series.values,
        })
        equity_chart = generator.make_equity_chart(equity_curve)
        if equity_chart:
            charts.append(equity_chart)

        # 月度热力图
        if INCLUDE_HEATMAP:
            heatmap = generator.make_monthly_heatmap(equity_curve)
            if heatmap:
                charts.append(heatmap)

        # 计算绩效指标
        metrics = generator.calc_performance_metrics(equity_curve)

    # 按标的盈亏图
    if pnl_by_stock is not None and not pnl_by_stock.empty:
        pnl_chart = make_pnl_by_stock_chart(pnl_by_stock, CHART_THEME)
        if pnl_chart:
            charts.append(pnl_chart)

    # Round-trip 散点图
    if analyzer.round_trips:
        rt_chart = make_round_trip_scatter(analyzer.round_trips, CHART_THEME)
        if rt_chart:
            charts.append(rt_chart)

    # 5. 构建 HTML
    html = build_attribution_html(
        metrics=metrics,
        tx_stats=tx_stats,
        rt_stats=rt_stats,
        pnl_by_stock=pnl_by_stock,
        exec_quality=exec_quality,
        stress_perf=stress_perf,
        consecutive=consecutive,
        charts=charts,
        round_trips=analyzer.round_trips,
        chart_theme=CHART_THEME,
    )

    # 6. 写入文件
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 7. 准备 LLM prompt
    llm_prompts = {
        "attribution": build_attribution_llm_prompt(
            rt_stats, pnl_by_stock, exec_quality, stress_perf
        )
    }

    # 8. 尝试调用 LLM
    llm_status = "skipped"
    llm_responses: Dict[str, Any] = {}
    try:
        from scripts.llm_client import generate_analysis, is_available
        if is_available():
            resp = generate_analysis(llm_prompts["attribution"])
            if resp:
                llm_responses["attribution"] = resp
                llm_status = "success"
            else:
                llm_status = "failed"
    except Exception:
        llm_status = "failed"

    # 9. 替换占位符
    inject_attribution_analysis(output_path, llm_responses, llm_prompts)

    # 10. 落盘 report_data.json（与 reports-engine 归因路由读取一致，供元数据使用）
    try:
        _report_dir = os.path.dirname(output_path)
        report_data = {
            "report_type": "attribution",
            "generated_at": datetime.now().isoformat(),
            "metrics": metrics,
            "tx_stats": tx_stats,
            "rt_stats": rt_stats,
            "llm_status": llm_status,
        }
        data_path_out = os.path.join(_report_dir, "report_data.json")
        with open(data_path_out, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass

    # 11. 返回 HTML（重新读取注入后的内容）
    with open(output_path, "r", encoding="utf-8") as f:
        return f.read()
