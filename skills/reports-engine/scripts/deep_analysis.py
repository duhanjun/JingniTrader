# -*- coding: utf-8 -*-
"""深度解读注入（LLM 占位符替换）公共模块。

从 engine.py 抽出的技术/基本面深度解读渲染与注入逻辑，供插件路径复用：
- ``_run_plugin_many``（技术+基本面多命中聚合）
- ``fallback_report`` 兜底插件（自包含默认个股报告）

模块零 engine 依赖：仅用标准库 + prompt 中的因子数值做规则兜底渲染，
LLM 成功时用 LLM 输出，失败/未配置时用规则模板兜底，保证深度解读章节
始终有内容（与原内置 _run_template_report 行为一致）。
"""
from __future__ import annotations

import html as _html_lib
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger("reports-engine.deep_analysis")


def inject_deep_analysis(
    html_paths: List[str],
    llm_responses: Dict[str, Any],
    llm_prompts: Dict[str, Any],
) -> None:
    """将 LLM 深度解读内容注入 HTML 报告（替换占位符）。

    LLM 成功时用 LLM 输出；失败时用规则模板从因子数据生成兜底内容。
    """
    for html_path in html_paths:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        modified = False

        # 技术面占位符
        if "<!--LLM_TECHNICAL_ANALYSIS_PLACEHOLDER-->" in html_content:
            resp = llm_responses.get("technical")
            if not resp:
                resp = build_fallback_technical(llm_prompts.get("technical", {}))
            rendered = render_technical_analysis(resp)
            html_content = html_content.replace(
                "<!--LLM_TECHNICAL_ANALYSIS_PLACEHOLDER-->", rendered
            )
            modified = True

        # 基本面占位符
        if "<!--LLM_FUNDAMENTAL_ANALYSIS_PLACEHOLDER-->" in html_content:
            resp = llm_responses.get("fundamental")
            if not resp:
                resp = build_fallback_fundamental(llm_prompts.get("fundamental", {}))
            rendered = render_fundamental_analysis(resp)
            html_content = html_content.replace(
                "<!--LLM_FUNDAMENTAL_ANALYSIS_PLACEHOLDER-->", rendered
            )
            modified = True

        if modified:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"深度解读已注入: {html_path}")


def render_technical_analysis(resp: Dict[str, Any]) -> str:
    """渲染技术面深度解读 HTML"""
    score = float(resp.get("technical_score", 0))
    sc = "positive" if score >= 60 else ("negative" if score < 40 else "")
    opt = []
    for key, title, tag in [("capital_flow_analysis", "资金面分析", True),
                             ("dragon_tiger_analysis", "龙虎榜解读", True),
                             ("price_limit_analysis", "涨跌停分析", False)]:
        v = resp.get(key, "")
        if v:
            t = " <span class='llm-tag-a'>A股特色</span>" if tag else ""
            opt.append(f"<h4>{title}{t}</h4><p>{_html_lib.escape(v)}</p>")
    return (
        f'<div class="llm-analysis-header">'
        f'<span class="llm-badge llm-badge-{sc}">技术评分 {score:.0f}</span>'
        f'<span class="llm-badge">趋势：{_html_lib.escape(resp.get("trend_direction", ""))}</span>'
        f'<span class="llm-badge">置信度：{_html_lib.escape(resp.get("trend_confidence", ""))}</span>'
        f'</div>'
        f'<div class="llm-analysis-body">'
        f'<h4>整体评估</h4><p>{_html_lib.escape(resp.get("overall_assessment", ""))}</p>'
        f'<h4>多周期趋势分析</h4><p>{_html_lib.escape(resp.get("trend_analysis", ""))}</p>'
        f'<h4>技术指标信号解读</h4><p>{_html_lib.escape(resp.get("indicator_analysis", ""))}</p>'
        f'<h4>关键价位分析</h4><p>{_html_lib.escape(resp.get("key_levels", ""))}</p>'
        f'<h4>风险信号</h4><p>{_html_lib.escape(resp.get("risk_signals", ""))}</p>'
        f'<h4>短期展望</h4><p>{_html_lib.escape(resp.get("short_term_outlook", ""))}</p>'
        f'{"".join(opt)}'
        f'</div>'
    )


def render_fundamental_analysis(resp: Dict[str, Any]) -> str:
    """渲染基本面深度解读 HTML"""
    score = float(resp.get("fundamental_score", 0))
    sc = "positive" if score >= 60 else ("negative" if score < 40 else "")
    opt = []
    for key, title, tag in [("industry_analysis", "行业分析与景气度", False),
                             ("financial_statement_analysis", "财务报表分析", False),
                             ("shareholder_analysis", "股东结构与资本运作", True)]:
        v = resp.get(key, "")
        if v:
            t = " <span class='llm-tag-a'>A股特色</span>" if tag else ""
            opt.append(f"<h4>{title}{t}</h4><p>{_html_lib.escape(v)}</p>")
    return (
        f'<div class="llm-analysis-header">'
        f'<span class="llm-badge llm-badge-{sc}">基本面评分 {score:.0f}</span>'
        f'<span class="llm-badge">估值：{_html_lib.escape(resp.get("valuation_level", ""))}</span>'
        f'<span class="llm-badge">评级：{_html_lib.escape(resp.get("investment_rating", ""))}</span>'
        f'</div>'
        f'<div class="llm-analysis-body">'
        f'<h4>整体评估</h4><p>{_html_lib.escape(resp.get("overall_assessment", ""))}</p>'
        f'<h4>估值分析</h4><p>{_html_lib.escape(resp.get("valuation_analysis", ""))}</p>'
        f'<h4>盈利能力分析</h4><p>{_html_lib.escape(resp.get("profitability_analysis", ""))}</p>'
        f'<h4>成长性分析</h4><p>{_html_lib.escape(resp.get("growth_analysis", ""))}</p>'
        f'<h4>风险因素</h4><p>{_html_lib.escape(resp.get("risk_factors", ""))}</p>'
        f'{"".join(opt)}'
        f'</div>'
    )


def build_fallback_technical(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，从 prompt 中的因子数据生成规则兜底解读。

    解析 user_prompt 中的因子数值，基于简单规则生成分析文本。
    """
    user_prompt = prompt_data.get("user_prompt", "")

    # 提取因子数值（prompt 格式如 "MA5=310.35", "DIF=0.997" 等）
    factors = {}
    for match in re.finditer(r'(\w+)[=：]\s*(-?[\d.]+)', user_prompt):
        key, val = match.group(1).lower(), match.group(2)
        try:
            factors[key] = float(val)
        except ValueError:
            pass

    # 规则生成（prompt 中用 DIF/DEA/柱 等简写名）
    ma5 = factors.get("ma5", 0)
    ma10 = factors.get("ma10", 0)
    ma20 = factors.get("ma20", 0)
    ma60 = factors.get("ma60", 0)
    current = factors.get("current_price", factors.get("close", 0))
    macd_dif = factors.get("macd_dif", factors.get("dif", 0))
    macd_dea = factors.get("macd_dea", factors.get("dea", 0))
    macd_hist = factors.get("macd_hist", factors.get("柱", 0))
    kdj_k = factors.get("kdj_k", factors.get("k", 0))
    kdj_d = factors.get("kdj_d", factors.get("d", 0))
    kdj_j = factors.get("kdj_j", factors.get("j", 0))
    boll_ub = factors.get("boll_ub", factors.get("上轨", 0))
    boll_lb = factors.get("boll_lb", factors.get("下轨", 0))
    boll_mid = factors.get("boll_mid", factors.get("中轨", 0))

    # 趋势方向
    if ma5 > ma10 > ma20 and current > ma60:
        trend = "看涨"
        score = 70
    elif ma5 < ma10 < ma20 and current < ma60:
        trend = "看跌"
        score = 30
    else:
        trend = "震荡"
        score = 55

    # MACD 信号
    if macd_dif > macd_dea and macd_hist > 0:
        macd_desc = f"MACD金叉（DIF={macd_dif:.2f} > DEA={macd_dea:.2f}），柱状图转正（{macd_hist:.2f}），短期动量转强"
        score = min(score + 5, 100)
    elif macd_dif < macd_dea and macd_hist < 0:
        macd_desc = f"MACD死叉（DIF={macd_dif:.2f} < DEA={macd_dea:.2f}），柱状图为负（{macd_hist:.2f}），短期动量偏弱"
        score = max(score - 5, 0)
    else:
        macd_desc = f"MACD处于转换期（DIF={macd_dif:.2f}, DEA={macd_dea:.2f}），趋势不明朗"

    # KDJ 信号
    if kdj_j > 80:
        kdj_desc = f"KDJ超买（K={kdj_k:.1f}, D={kdj_d:.1f}, J={kdj_j:.1f}），短期有回调风险"
    elif kdj_j < 20:
        kdj_desc = f"KDJ超卖（K={kdj_k:.1f}, D={kdj_d:.1f}, J={kdj_j:.1f}），短期有反弹机会"
    else:
        kdj_desc = f"KDJ中性区域（K={kdj_k:.1f}, D={kdj_d:.1f}, J={kdj_j:.1f}），方向待选择"

    # 布林带
    boll_width = 0
    if boll_ub > 0 and boll_lb > 0:
        boll_width = boll_ub - boll_lb
        if current >= boll_ub * 0.98:
            boll_desc = f"价格接近布林上轨（{boll_ub:.2f}），短期偏强但注意回落"
        elif current <= boll_lb * 1.02:
            boll_desc = f"价格接近布林下轨（{boll_lb:.2f}），短期偏弱但关注支撑"
        else:
            boll_desc = f"价格在布林带中轨（{boll_mid:.2f}）附近运行，带宽{boll_width:.2f}"
    else:
        boll_desc = "布林带数据暂缺"

    # 均线分析
    if ma5 > ma10 > ma20:
        ma_desc = f"短期均线多头排列（MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20:.2f}），短期趋势偏多"
    elif ma5 < ma10 < ma20:
        ma_desc = f"短期均线空头排列（MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20:.2f}），短期趋势偏空"
    else:
        ma_desc = f"短期均线粘合（MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f}），方向待选择"

    ma60_note = f"MA60={ma60:.2f}" if ma60 > 0 else "MA60数据暂缺"

    # 龙虎榜
    lhb_count = int(factors.get("lhb_count_5d", 0))
    lhb_note = f"近5日上榜{lhb_count}次" if lhb_count > 0 else "近5日无龙虎榜记录"

    return {
        "trend_direction": trend,
        "trend_confidence": "中",
        "technical_score": score,
        "overall_assessment": f"当前技术面{trend}，{macd_desc.split('，')[0]}。",
        "trend_analysis": f"{ma_desc}。{ma60_note}。多周期共振情况需结合周线/月线判断。",
        "indicator_analysis": f"{macd_desc}。{kdj_desc}。{boll_desc}。",
        "key_levels": f"MA20({ma20:.2f})为短期支撑，MA60({ma60:.2f})为中期阻力。布林上轨({boll_ub:.2f})和下轨({boll_lb:.2f})为极端位置参考。",
        "risk_signals": "量能数据缺失，无法判断量价配合。布林带带宽变化需关注突破方向。" if boll_width < 30 else "布林带较宽，波动正常。",
        "short_term_outlook": f"关注MA20({ma20:.2f})支撑和MA60({ma60:.2f})阻力的突破方向。",
        "capital_flow_analysis": "",
        "dragon_tiger_analysis": lhb_note,
        "price_limit_analysis": "",
    }


def build_fallback_fundamental(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，从 prompt 中的因子数据生成规则兜底解读"""
    user_prompt = prompt_data.get("user_prompt", "")

    factors = {}
    for match in re.finditer(r'(\w+)[=：]\s*(-?[\d.]+)', user_prompt):
        key, val = match.group(1).lower(), match.group(2)
        try:
            factors[key] = float(val)
        except ValueError:
            pass

    roe = factors.get("roe_ttm", factors.get("roe", 0))
    debt = factors.get("debt_ratio", factors.get("资产负债率", 0))
    current = factors.get("current_ratio", factors.get("流动比率", 0))
    pe = factors.get("pe_ttm", factors.get("pe", 0))
    pb = factors.get("pb", 0)

    # 评分
    score = 50
    if roe > 15:
        score += 15
    elif roe > 10:
        score += 8
    elif roe < 5:
        score -= 10

    if debt > 70:
        score -= 10
    elif debt < 50:
        score += 5

    if current < 1:
        score -= 8
    elif current > 2:
        score += 5

    score = max(0, min(100, score))

    # 估值判断
    if pe > 0:
        if pe < 15:
            valuation = "低估"
        elif pe < 30:
            valuation = "合理"
        elif pe < 50:
            valuation = "偏高"
        else:
            valuation = "高估"
    else:
        valuation = "合理"

    # 评级
    if score >= 70:
        rating = "买入"
    elif score >= 60:
        rating = "增持"
    elif score >= 40:
        rating = "中性"
    elif score >= 30:
        rating = "减持"
    else:
        rating = "卖出"

    roe_desc = f"ROE(TTM)为{roe:.2f}%，" + ("股东回报效率优秀" if roe > 15 else "股东回报效率偏低" if roe < 8 else "股东回报效率适中")
    debt_desc = f"资产负债率{debt:.2f}%，" + ("杠杆水平偏高" if debt > 70 else "杠杆水平适中" if debt > 50 else "杠杆水平较低")
    current_desc = f"流动比率{current:.2f}，" + ("短期偿债能力偏弱" if current < 1 else "短期偿债能力良好" if current > 2 else "短期偿债能力一般")

    risk_items = []
    if debt > 70:
        risk_items.append(f"资产负债率{debt:.1f}%偏高，财务杠杆风险较大")
    if current < 1:
        risk_items.append(f"流动比率{current:.2f}低于1，短期偿债压力较大")
    if roe < 5:
        risk_items.append(f"ROE仅{roe:.1f}%，盈利能力偏弱")
    if not risk_items:
        risk_items.append("未发现重大基本面风险信号")

    return {
        "valuation_level": valuation,
        "fundamental_score": score,
        "investment_rating": rating,
        "overall_assessment": f"{roe_desc}。{debt_desc}。综合评估给予{rating}评级。",
        "valuation_analysis": f"PE(TTM)={pe:.1f}，PB={pb:.2f}，估值水平{valuation}。" if pe > 0 else "估值数据暂缺，无法判断估值高低。",
        "profitability_analysis": roe_desc + "。建议关注毛利率和净利率的改善趋势。",
        "growth_analysis": "营收和利润增速数据暂缺，建议关注后续财报和行业增速变化。",
        "risk_factors": "；".join(risk_items) + "。",
        "industry_analysis": "",
        "financial_statement_analysis": f"{debt_desc}。{current_desc}。",
        "shareholder_analysis": "",
    }
