# -*- coding: utf-8 -*-
"""因子分析报告插件 — 渲染器（阶段 C：自包含）。

自包含化：本插件不再依赖 engine.py 内部函数。复用公共库
（scripts.templates.common_components）与本插件 factor_analysis_report.html.j2
模板完成因子分析汇总报告：读 reports/alphalens/<task_id>/*_metrics.json →
聚合因子指标卡片 → 渲染 → 写文件。

触发条件（plugin.yaml）：存在 FACTOR 产物且 alphalens 目录存在（find_by_trigger）。
"""
from __future__ import annotations

import glob as _glob
import json
import os
from datetime import datetime
from typing import Any, Dict, List

from scripts.templates.common_components import (
    build_page_css,
    build_nav_bar_html,
    build_footer_html,
    render_page,
)

_DISCLAIMER = (
    "本报告由 JingniTrader 基于历史样本数据自动生成，仅供量化研究与学习用途，不构成任何投资建议。"
    "因子有效性指标（IC、多空收益、夏普等）基于历史截面样本统计，属于历史检验结果；"
    "统计上显著不代表未来仍有效，因子可能因市场结构变化、样本外失效或拥挤交易而衰减。"
    "因子分析结论不构成选股或交易依据，实盘交易有风险，请谨慎决策。"
)


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染因子分析汇总报告。

    参数:
        data: 按 requires 收集的产物
        ctx:  Context 对象
        output_path: 输出 HTML 路径（factor_analysis_report.html）
    """
    _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    task_id = getattr(ctx, "task_id", "") or "default"
    alphalens_dir = os.path.join(_work_dir, "reports", "alphalens", task_id)
    if not os.path.isdir(alphalens_dir):
        raise RuntimeError(f"未找到 alphalens 因子分析目录: {alphalens_dir}")

    metrics_files = sorted(_glob.glob(os.path.join(alphalens_dir, "*_metrics.json")))
    if not metrics_files:
        raise RuntimeError(f"alphalens 目录下无 *_metrics.json 文件: {alphalens_dir}")

    # 读取所有 metrics.json
    metrics_list = []
    for mf in metrics_files:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                metrics_list.append(json.load(f))
        except Exception:
            pass

    if not metrics_list:
        raise RuntimeError("alphalens metrics 解析结果为空")

    # 渲染汇总卡片（统一惊泥科技样式）
    cards_html: List[str] = []
    for m in metrics_list:
        verdict = m.get("suggested_verdict", "REVIEW")
        verdict_cls = {
            "ACCEPT": "signal-bullish", "REVIEW": "signal-warning", "REJECT": "signal-bearish"
        }.get(verdict, "signal-neutral")
        factor_name = m.get("factor", "unknown")
        factor_html = os.path.join(alphalens_dir, f"{factor_name}_report.html")
        # relpath 基准使用运行时 work_dir/reports，与 output_path 一致
        _runtime_report_dir = os.path.join(_work_dir, "reports")
        factor_link = (
            f'<a href="{os.path.relpath(factor_html, _runtime_report_dir)}" target="_blank" '
            f'class="factor-link">查看详情</a>'
            if os.path.exists(factor_html) else ""
        )
        cards_html.append(f"""
        <div class="factor-card">
          <h3>{factor_name} <span class="signal-tag {verdict_cls}">{verdict}</span></h3>
          <div class="metrics-row">
            <span><b>IC 均值</b>: {m.get('ic_mean', 0):.4f}</span>
            <span><b>IC IR</b>: {m.get('ic_ir', 0):.4f}</span>
            <span><b>多空夏普</b>: {m.get('long_short_sharpe', 0):.4f}</span>
            <span><b>多空收益</b>: {m.get('long_short_return', 0):.4f}</span>
            <span><b>Top 换手率</b>: {m.get('avg_turnover_top_quantile', 0):.4f}</span>
          </div>
          <div class="link-row">{factor_link}</div>
        </div>""")

    accept_count = sum(1 for m in metrics_list if m.get('suggested_verdict') == 'ACCEPT')
    review_count = sum(1 for m in metrics_list if m.get('suggested_verdict') == 'REVIEW')

    # 渲染模板
    html = render_page(
        "factor_analysis_report.html.j2",
        extra_template_dirs=[os.path.dirname(os.path.abspath(__file__))],
        page_css=build_page_css(),
        nav_html=build_nav_bar_html(),
        footer_html=build_footer_html(disclaimer=_DISCLAIMER),
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        task_id=task_id,
        num_factors=len(metrics_list),
        accept_count=accept_count,
        review_count=review_count,
        cards_html=cards_html,
    )

    # 写入文件（去末尾换行，与内置输出逐字节一致）
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    html = html.rstrip("\n")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html
