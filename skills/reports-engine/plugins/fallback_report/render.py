# -*- coding: utf-8 -*-
"""兜底插件 — 渲染器（自包含）。

在无任何插件命中（无显式意图/产物）时承担默认个股报告。
默认报告 = 技术面 + 基本面两份报告（含 LLM 深度解读注入）。

自包含化：本插件不再回调 engine.py 内部函数（原 ``engine._run_template_report``），
而是直接委托 technical_report / fundamental_report 插件渲染（二者复用
``scripts.template_engine.compute_report_data`` 公共计算 + 各自 .j2 模板），
并复用 ``scripts.deep_analysis`` 做 LLM 深度解读注入，保持与旧默认兜底行为
一致（keyword 检测 technical/fundamental/both、LLM 注入、门户注册、
report_data.json 汇总）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger("reports-engine.plugins.fallback_report")

# 模板类插件 id → 输出文件名 / 门户类型 / 门户标题 / LLM analyst_type
_TEMPLATE_PLUGINS = {
    "technical_report": {
        "output_file": "technical_report.html",
        "portal_type": "technical_report",
        "portal_title": "个股分析-技术面",
        "analyst_type": "technical",
    },
    "fundamental_report": {
        "output_file": "fundamental_report.html",
        "portal_type": "fundamental_report",
        "portal_title": "个股分析-基本面",
        "analyst_type": "fundamental",
    },
}


def _detect_template_choice(ctx) -> str:
    """识别默认报告模板：technical / fundamental / both（自包含实现）。

    优先级与旧内置 _detect_report_template 一致：
    1. metadata["report_template"] 显式指定
    2. metadata["report_intent"] 兼容旧字段
    3. 用户意图关键词识别
    4. 默认 both
    """
    meta = getattr(ctx, "metadata", {}) or {}

    explicit = meta.get("report_template")
    if explicit in ("technical", "fundamental", "both"):
        return explicit

    intent = meta.get("report_intent")
    if intent in ("technical", "fundamental", "both"):
        return intent

    text = (meta.get("user_intent") or meta.get("user_query")
            or getattr(ctx, "user_intent", "") or "").lower()
    tech_keywords = [
        "技术面", "技术分析", "k线", "k 线", "趋势", "支撑", "阻力",
        "形态", "macd", "rsi", "kdj", "boll", "均线", "量价", "资金流",
        "龙虎榜", "涨跌停", "北向",
    ]
    fund_keywords = [
        "基本面", "财务", "估值", "roe", "毛利率", "净利率", "营收",
        "利润", "pe", "pb", "ps", "股东", "分红", "股息", "现金流",
        "资产负债", "成长性", "盈利能力", "解禁", "回购",
    ]
    tech_hits = sum(1 for kw in tech_keywords if kw in text)
    fund_hits = sum(1 for kw in fund_keywords if kw in text)

    if tech_hits > 0 and fund_hits == 0:
        return "technical"
    if fund_hits > 0 and tech_hits == 0:
        return "fundamental"
    if tech_hits > 0 and fund_hits > 0:
        if tech_hits - fund_hits >= 2:
            return "technical"
        if fund_hits - tech_hits >= 2:
            return "fundamental"
        return "both"
    return "both"


def _resolve_out_dir(ctx, output_path: str) -> str:
    """解析报告输出目录：优先 output_path 所在目录，其次 metadata。"""
    if output_path:
        return os.path.dirname(os.path.abspath(output_path))
    meta = getattr(ctx, "metadata", None) or {}
    out_dir = meta.get("report_output_dir") or ""
    if out_dir and os.path.isdir(out_dir):
        return out_dir
    work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    return os.path.join(work_dir, "reports")


def _collect_llm_prompts(ctx, analyst_types: List[str]) -> Dict[str, Any]:
    """复用公共计算提取技术/基本面深度解读 LLM prompt。"""
    prompts: Dict[str, Any] = {}
    try:
        from scripts.template_engine import compute_report_data
    except Exception:
        return prompts
    for analyst_type in analyst_types:
        try:
            comp = compute_report_data(analyst_type, ctx)
            if comp and comp.get("llm_prompt"):
                prompts[analyst_type] = comp["llm_prompt"]
        except Exception as e:
            logger.warning(f"[{analyst_type}] LLM prompt 收集失败: {e}")
    return prompts


def _update_portal(html_path: str, report_type: str, title: str, ctx) -> None:
    """更新报告门户（不阻断）。"""
    try:
        from scripts.report_portal import upsert_report, generate_portal
        _report_dir = os.path.dirname(html_path)
        upsert_report(
            report_dir=_report_dir,
            report_type=report_type,
            file_path=html_path,
            title=title or None,
            task_id=ctx.task_id if hasattr(ctx, "task_id") else "",
        )
        generate_portal(_report_dir)
    except Exception as e:
        logger.warning(f"门户注册失败（不阻断）: {e}")


def render(data: Dict[str, Any], ctx, output_path: str) -> str:
    """渲染默认个股报告（技术面 + 基本面，委托模板类插件自包含渲染）。

    参数:
        data: 按 requires 收集的产物（空）
        ctx:  Context 对象
        output_path: 输出 HTML 路径（此处为兼容插件签名，实际产物以
                     各模板插件生成的文件为准；主报告路径为首个生成文件）
    """
    from plugins.registry import get as _plugin_get
    from scripts.deep_analysis import inject_deep_analysis

    template_choice = _detect_template_choice(ctx)
    logger.info(f"兜底默认报告模板: {template_choice}")

    out_dir = _resolve_out_dir(ctx, output_path)
    os.makedirs(out_dir, exist_ok=True)

    plugin_ids: List[str] = []
    if template_choice in ("technical", "both"):
        plugin_ids.append("technical_report")
    if template_choice in ("fundamental", "both"):
        plugin_ids.append("fundamental_report")

    # ── 1. 逐个委托模板类插件渲染 ──
    generated_paths: List[str] = []
    errors: List[str] = []
    for pid in plugin_ids:
        plugin = _plugin_get(pid)
        if plugin is None or plugin.render is None:
            errors.append(f"模板插件[{pid}]未注册或无 render")
            continue
        info = _TEMPLATE_PLUGINS[pid]
        out_path = os.path.join(out_dir, info["output_file"])
        try:
            plugin.render(data, ctx, out_path)
            if os.path.exists(out_path):
                generated_paths.append(out_path)
            else:
                errors.append(f"模板插件[{pid}]未产出 HTML")
        except Exception as e:
            errors.append(f"模板插件[{pid}]渲染失败: {e}")
            logger.warning(f"模板插件[{pid}]渲染失败: {e}")

    if not generated_paths:
        raise RuntimeError("默认个股报告生成失败: " + "；".join(errors))

    # ── 2. 收集 LLM prompt 并注入深度解读 ──
    analyst_types = [_TEMPLATE_PLUGINS[pid]["analyst_type"] for pid in plugin_ids]
    llm_prompts = _collect_llm_prompts(ctx, analyst_types)
    llm_responses: Dict[str, Any] = {}
    llm_status = "skipped"
    if llm_prompts:
        try:
            from scripts.llm_client import generate_analysis, is_available
            if is_available():
                logger.info("开始调用 LLM 生成深度解读...")
                for analyst_type, prompt_data in llm_prompts.items():
                    resp = generate_analysis(prompt_data)
                    if resp:
                        llm_responses[analyst_type] = resp
                    else:
                        logger.warning(f"  {analyst_type}: LLM 返回为空，使用规则模板兜底")
                llm_status = "success" if llm_responses else "failed"
            else:
                llm_status = "skipped"
                logger.info("未配置 QUANT_LLM_API_KEY，跳过 LLM 调用，深度解读使用规则模板兜底")
        except Exception as e:
            llm_status = "failed"
            logger.warning(f"LLM 调用异常: {e}，深度解读使用规则模板兜底")
        # 无论 LLM 是否成功，都替换占位符（失败时用规则模板生成兜底内容）
        inject_deep_analysis(generated_paths, llm_responses, llm_prompts)

    # ── 3. 逐个注册门户（技术/基本面分别注册） ──
    for path in generated_paths:
        fname = os.path.basename(path)
        info = None
        if "technical" in fname:
            info = _TEMPLATE_PLUGINS["technical_report"]
        elif "fundamental" in fname:
            info = _TEMPLATE_PLUGINS["fundamental_report"]
        _rtype = info["portal_type"] if info else "report"
        _title = info["portal_title"] if info else ""
        _update_portal(path, _rtype, _title, ctx)

    # ── 4. 汇总 report_data.json（供 engine._augment_fallback_metadata 读取） ──
    report_data = {
        "report_template": template_choice,
        "generated_at": datetime.now().isoformat(),
        "artifacts": generated_paths,
        "llm_status": llm_status,
    }
    data_path_out = os.path.join(out_dir, "report_data.json")
    with open(data_path_out, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

    # ── 5. 返回主报告 HTML（首个生成文件） ──
    primary = generated_paths[0]
    # 若插件框架传入的 output_path 与主报告文件名不同，则同步一份副本（兼容框架代写）
    if output_path and os.path.basename(output_path) != os.path.basename(primary):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(primary, "r", encoding="utf-8") as _src, \
                open(output_path, "w", encoding="utf-8") as _dst:
            _dst.write(_src.read())

    with open(primary, "r", encoding="utf-8") as f:
        return f.read()
