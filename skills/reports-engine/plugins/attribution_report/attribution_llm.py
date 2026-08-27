# -*- coding: utf-8 -*-
"""绩效归因 LLM 解读（阶段 B：从 engine.py 迁入插件，自包含）。

包含：
- build_attribution_llm_prompt：构建归因 LLM prompt
- build_fallback_attribution：LLM 不可用时的规则兜底解读
- render_attribution_analysis：渲染归因解读 HTML
- inject_attribution_analysis：将解读注入报告（替换占位符）
"""
from __future__ import annotations

import html as _html_lib
import logging
import re
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger("reports-engine.plugins.attribution_llm")


def build_attribution_llm_prompt(
    rt_stats: dict,
    pnl_by_stock: pd.DataFrame,
    exec_quality: dict,
    stress_perf: dict,
) -> dict:
    """构建绩效归因 LLM prompt"""
    system_prompt = (
        "你是一位专业的量化投资绩效分析师。请根据以下绩效归因数据，"
        "生成一份结构化的绩效归因分析报告。要求：\n"
        "1. 分析盈亏的主要来源（按标的/按持仓时间/按交易方向）\n"
        "2. 评估交易执行质量（费用占比/滑点）\n"
        "3. 识别交易模式中的优势与不足\n"
        "4. 给出具体的改进建议\n"
        "请以 JSON 格式返回，包含以下字段：\n"
        '{"overall_summary": "总体评价", "pnl_source_analysis": "盈亏来源分析", '
        '"execution_quality_analysis": "执行质量分析", "pattern_analysis": "交易模式分析", '
        '"improvement_suggestions": "改进建议", "risk_assessment": "风险评估"}'
    )

    # 构建用户 prompt
    parts = []
    if rt_stats:
        parts.append("=== Round-Trip 统计 ===")
        parts.append(f"闭环交易数: {rt_stats.get('total_round_trips', 0)}")
        parts.append(f"胜率: {rt_stats.get('win_rate', 0) * 100:.1f}%")
        parts.append(f"总净盈亏: {rt_stats.get('total_net_pnl', 0):,.2f}")
        parts.append(f"盈亏比: {rt_stats.get('profit_factor', 0):.2f}")
        parts.append(f"平均持仓天数: {rt_stats.get('avg_holding_days', 0):.1f}")

    if pnl_by_stock is not None and not pnl_by_stock.empty:
        parts.append("\n=== 按标的盈亏（前10） ===")
        for _, row in pnl_by_stock.head(10).iterrows():
            parts.append(
                f"{row['code']}: 盈亏={row['total_pnl']:,.2f}, "
                f"交易次数={row['trade_count']}, 胜率={row['win_rate']:.1f}%, "
                f"平均收益={row['avg_return_pct']:.2f}%"
            )

    if exec_quality:
        parts.append("\n=== 执行质量 ===")
        parts.append(f"总成交额: {exec_quality.get('total_turnover', 0):,.2f}")
        parts.append(f"成本占比: {exec_quality.get('cost_ratio_bps', 0):.2f} bps")
        parts.append(f"滑点占比: {exec_quality.get('slippage_ratio_bps', 0):.2f} bps")

    if stress_perf:
        parts.append("\n=== 压力期表现 ===")
        for name, data in stress_perf.items():
            parts.append(f"{name}: 收益={data['return_pct']:.2f}%, 回撤={data['max_drawdown_pct']:.2f}%")

    return {
        "system_prompt": system_prompt,
        "user_prompt": "\n".join(parts),
    }


def build_fallback_attribution(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，生成规则兜底绩效归因解读"""
    user_prompt = prompt_data.get("user_prompt", "")

    # 从 prompt 中提取关键数据
    win_rate_match = re.search(r'胜率:\s*([\d.]+)%', user_prompt)
    win_rate = float(win_rate_match.group(1)) if win_rate_match else 0

    pnl_match = re.search(r'总净盈亏:\s*([-\d,.]+)', user_prompt)
    total_pnl = float(pnl_match.group(1).replace(',', '')) if pnl_match else 0

    pf_match = re.search(r'盈亏比:\s*([\d.]+)', user_prompt)
    profit_factor = float(pf_match.group(1)) if pf_match else 0

    cost_match = re.search(r'成本占比:\s*([\d.]+)\s*bps', user_prompt)
    cost_bps = float(cost_match.group(1)) if cost_match else 0

    # 规则生成
    if total_pnl > 0:
        overall = f"本期交易整体盈利，总净盈亏 {total_pnl:,.2f} 元，胜率 {win_rate:.1f}%。"
    elif total_pnl < 0:
        overall = f"本期交易整体亏损，总净盈亏 {total_pnl:,.2f} 元，胜率 {win_rate:.1f}%。"
    else:
        overall = "本期交易盈亏基本持平。"

    if profit_factor > 1.5:
        pnl_analysis = f"盈亏比 {profit_factor:.2f}，盈利交易的规模显著大于亏损交易，风险控制良好。"
    elif profit_factor > 1.0:
        pnl_analysis = f"盈亏比 {profit_factor:.2f}，略高于1，盈利略大于亏损，有改善空间。"
    else:
        pnl_analysis = f"盈亏比 {profit_factor:.2f}，低于1，亏损交易规模大于盈利，需加强止损管理。"

    if cost_bps > 30:
        exec_analysis = f"交易成本占比 {cost_bps:.2f} bps，偏高，建议减少交易频率或优化下单方式。"
    elif cost_bps > 10:
        exec_analysis = f"交易成本占比 {cost_bps:.2f} bps，适中，处于合理范围。"
    else:
        exec_analysis = f"交易成本占比 {cost_bps:.2f} bps，较低，执行效率良好。"

    if win_rate > 60:
        pattern = f"胜率 {win_rate:.1f}%，交易胜率较高，说明选股策略有一定的有效性。"
    elif win_rate > 40:
        pattern = f"胜率 {win_rate:.1f}%，胜率中等，建议结合盈亏比综合评估策略效果。"
    else:
        pattern = f"胜率 {win_rate:.1f}%，胜率偏低，建议优化入场条件或增加过滤条件。"

    suggestions = []
    if profit_factor < 1.5:
        suggestions.append("建议设置更严格的止损规则，控制单笔亏损规模。")
    if cost_bps > 20:
        suggestions.append("建议减少短线交易频率，降低交易成本对收益的侵蚀。")
    if win_rate < 50:
        suggestions.append("建议增加入场信号过滤条件，提高交易胜率。")
    if not suggestions:
        suggestions.append("当前策略表现稳定，建议持续监控并定期复盘。")

    return {
        "overall_summary": overall,
        "pnl_source_analysis": pnl_analysis,
        "execution_quality_analysis": exec_analysis,
        "pattern_analysis": pattern,
        "improvement_suggestions": " ".join(suggestions),
        "risk_assessment": "绩效归因基于历史交易数据，不构成未来收益保证。建议持续监控策略表现，及时调整。",
    }


def render_attribution_analysis(resp: Dict[str, Any]) -> str:
    """渲染绩效归因 LLM 解读 HTML"""
    return (
        f'<div class="llm-analysis-body">'
        f'<h4>总体评价</h4><p>{_html_lib.escape(resp.get("overall_summary", ""))}</p>'
        f'<h4>盈亏来源分析</h4><p>{_html_lib.escape(resp.get("pnl_source_analysis", ""))}</p>'
        f'<h4>执行质量分析</h4><p>{_html_lib.escape(resp.get("execution_quality_analysis", ""))}</p>'
        f'<h4>交易模式分析</h4><p>{_html_lib.escape(resp.get("pattern_analysis", ""))}</p>'
        f'<h4>改进建议</h4><p>{_html_lib.escape(resp.get("improvement_suggestions", ""))}</p>'
        f'<h4>风险评估</h4><p>{_html_lib.escape(resp.get("risk_assessment", ""))}</p>'
        f'</div>'
    )


def inject_attribution_analysis(
    html_path: str,
    llm_responses: Dict[str, Any],
    llm_prompts: Dict[str, Any],
) -> None:
    """将 LLM 绩效归因解读注入 HTML 报告（替换占位符）"""
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    if "<!--LLM_ATTRIBUTION_PLACEHOLDER-->" in html_content:
        resp = llm_responses.get("attribution")
        if not resp:
            resp = build_fallback_attribution(llm_prompts.get("attribution", {}))
        rendered = render_attribution_analysis(resp)
        html_content = html_content.replace(
            "<!--LLM_ATTRIBUTION_PLACEHOLDER-->", rendered
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"绩效归因解读已注入: {html_path}")
