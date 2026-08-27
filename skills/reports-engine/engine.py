"""
绩效归因与可视化报告引擎主逻辑
整合全流程产物，生成 HTML 报告 + JSON 数据
"""
import os
import sys
import json
import logging
from typing import Dict, Any, List
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from scripts.config import (
    REPORT_DIR, ENABLE_PLUGIN
)
# 公共可视化报告生成器（阶段 B 提取：供归因/回测报告共用）
from scripts.report_generator import ReportGenerator  # noqa: E402

# 报告插件框架（懒加载避免循环依赖）
_PLUGIN_LOADED = False


def _ensure_plugins_loaded() -> None:
    """确保插件已扫描注册（懒加载，仅一次）。"""
    global _PLUGIN_LOADED
    if _PLUGIN_LOADED:
        return
    try:
        from plugins.loader import scan as _plugin_scan
        plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
        _plugin_scan(plugins_dir)
        _PLUGIN_LOADED = True
    except Exception as e:  # 插件框架异常不影响主流程
        logger.warning(f"报告插件加载异常（已跳过）: {e}")


logger = logging.getLogger("reports-engine")


def _resolve_report_output_dir(ctx) -> str:
    """解析报告输出目录。

    报告产物直接写入归档目录（master engine 在 REPORT 阶段注入
    ``ctx.metadata["report_output_dir"]`` = 归档 step 的 artifacts 目录），
    实现"报告直接生成在归档目录"；未注入时回退到默认 REPORT_DIR
    （保持 reports-engine 独立运行兼容）。

    返回: 报告输出目录绝对路径（目录不存在时自动创建）
    """
    meta = getattr(ctx, "metadata", None) or {}
    out_dir = meta.get("report_output_dir") or ""
    if out_dir and os.path.isdir(out_dir):
        return out_dir
    # 兜底：默认报告目录
    os.makedirs(REPORT_DIR, exist_ok=True)
    return REPORT_DIR


def _detect_report_template(ctx) -> str:
    """
    识别报告模板：technical / fundamental / both

    统一路由逻辑，不再有量化/非量化标签区分。
    优先级：
    1. ctx.metadata["report_template"] 显式指定
    2. ctx.metadata["report_intent"] 兼容旧字段
    3. 通过用户意图关键词识别
    4. 默认 both（同时生成技术面与基本面两份报告）
    """
    meta = getattr(ctx, 'metadata', {}) or {}

    # 1. 显式指定模板
    explicit = meta.get("report_template")
    if explicit in ("technical", "fundamental", "both"):
        return explicit

    # 2. 兼容旧的 report_intent 字段
    intent = meta.get("report_intent")
    if intent in ("technical", "fundamental", "both"):
        return intent

    # 3. 关键词识别
    text = (meta.get("user_intent") or meta.get("user_query") or "").lower()
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

    # 4. 默认两份都生成
    return "both"


def _maybe_render_factor_analysis_report(ctx) -> str:
    """T3-8: 若存在 alphalens 因子分析报告目录，则聚合各因子 metrics.json
    生成独立的因子分析汇总报告 HTML（阶段 C：委托给 factor_analysis_report 插件自包含生成）。

    读取 workspace/reports/alphalens/<task_id>/*_metrics.json，渲染单页 HTML。

    返回
    ----
    生成的 HTML 路径；不存在或无 metrics.json 时返回空字符串
    """
    _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
    task_id = getattr(ctx, "task_id", "") or "default"
    alphalens_dir = os.path.join(_work_dir, "reports", "alphalens", task_id)
    if not os.path.isdir(alphalens_dir):
        return ""

    # 委托因子分析插件自包含生成（直接调用插件 render，不进门户）
    _ensure_plugins_loaded()
    from plugins.registry import get as _plugin_get
    plugin = _plugin_get("factor_analysis_report")
    if plugin is None or not plugin.render:
        logger.warning("factor_analysis_report 插件未注册，跳过因子分析报告")
        return ""

    _report_dir = _resolve_report_output_dir(ctx)
    os.makedirs(_report_dir, exist_ok=True)
    output_path = os.path.join(_report_dir, "factor_analysis_report.html")
    try:
        plugin.render(None, ctx, output_path)
        logger.info(f"因子分析汇总报告已生成: {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"因子分析汇总报告生成失败: {e}")
        return ""


def _collect_plugin_data(ctx, requires: List[str]) -> Dict[str, Any]:
    """按插件 requires 声明收集产物数据。

    支持的产物：
      - DATA:     行情数据 DataFrame（读 parquet）
      - FACTOR:   因子数据 DataFrame（读 parquet）
      - BACKTEST: 回测产物（返回 backtest_result.json 解析后的 dict + equity_curve）
      其余未知产物：返回其 artifact 路径字符串（由插件自行读取）。
    """
    import pandas as _pd

    data: Dict[str, Any] = {}
    for key in requires:
        if not hasattr(ctx, "get_artifact"):
            continue
        path = ctx.get_artifact(key)
        if not path:
            continue
        try:
            if key in ("DATA", "FACTOR"):
                data[key] = _pd.read_parquet(path) if os.path.exists(path) else None
            elif key == "BACKTEST":
                # 回测目录：返回 dir 路径 + 净值曲线
                bt = {"dir": os.path.dirname(path) if os.path.exists(path) else ""}
                eq_path = os.path.join(os.path.dirname(path), "equity_curve.parquet")
                bt["equity_curve"] = _pd.read_parquet(eq_path) if os.path.exists(eq_path) else None
                data[key] = bt
            else:
                data[key] = path
        except Exception as e:
            logger.warning(f"插件产物[{key}]收集失败: {e}")
            data[key] = None
    return data


def _run_plugin(ctx, plugin) -> Dict[str, Any]:
    """运行报告插件：收集产物 → 调 render → 写文件 → 注册门户。

    参数:
        ctx:    Context 对象
        plugin: ReportPlugin（已由 loader 注册，含 render 函数）
    返回:
        与其它 _run_*_report 一致的结果 dict {success, artifact_path, ...}
    """
    if plugin.render is None:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": f"插件[{plugin.id}]无 render 函数",
        }

    try:
        # 收集产物数据
        data = _collect_plugin_data(ctx, plugin.requires)

        # 输出路径：报告直接写入解析后的输出目录（默认 <QUANT_WORK_DIR>/reports，
        # master engine 注入 report_output_dir 时写入归档 step 目录）。
        # 文件名：插件可声明 output_file（如回测报告固定 report.html），缺省为 {id}.html。
        out_dir = _resolve_report_output_dir(ctx)
        os.makedirs(out_dir, exist_ok=True)
        out_name = getattr(plugin, "output_file", "") or f"{plugin.id}.html"
        output_path = os.path.join(out_dir, out_name)

        # 调用插件 render
        html = plugin.render(data, ctx, output_path)

        if not html or not os.path.exists(output_path):
            # render 未写文件则框架代写
            if html:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(html)
            else:
                return {
                    "success": False, "artifact_path": "", "metadata": {},
                    "error": f"插件[{plugin.id}]未返回 HTML",
                }

        # 注册门户（report_type 使用插件声明的兼容类型，默认=插件 id）
        plugin_report_type = getattr(plugin, "report_type", None) or plugin.id
        try:
            _update_report_portal(
                output_path,
                report_type=plugin_report_type,
                title=f"{plugin.label}报告",
                task_id=ctx.task_id if hasattr(ctx, "task_id") else "",
            )
        except Exception as e:
            logger.warning(f"插件[{plugin.id}]门户注册失败: {e}")

        logger.info(f"插件[{plugin.id}]报告已生成: {output_path}")
        return {
            "success": True,
            "artifact_path": output_path,
            "metadata": {"plugin_id": plugin.id, "report_type": plugin_report_type},
            "error": "",
        }
    except Exception as e:
        logger.error(f"插件[{plugin.id}]运行异常: {e}")
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": str(e),
        }


def _run_plugin_by_id(ctx, plugin_id: str) -> str:
    """按插件 id 运行插件并返回生成的 HTML 路径。

    阶段 B：内置 _run_*_report 委托给自包含插件时使用。插件缺失或生成失败时抛异常。

    参数:
        ctx: Context 对象
        plugin_id: 插件 id（如 portfolio_report / execution_report / attribution_report）
    返回:
        生成的 HTML 文件绝对路径
    抛出:
        RuntimeError: 插件未注册或生成失败
    """
    _ensure_plugins_loaded()
    from plugins.registry import get as _plugin_get
    plugin = _plugin_get(plugin_id)
    if plugin is None:
        raise RuntimeError(f"插件未注册: {plugin_id}")

    result = _run_plugin(ctx, plugin)
    if not result or not result.get("success"):
        raise RuntimeError(result.get("error") if isinstance(result, dict) else f"插件[{plugin_id}]生成失败")
    return result.get("artifact_path")


def _augment_backtest_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
    """为回测报告结果补充兼容 metadata（metrics / report_data_path / num_charts）。

    回测插件 render 已落盘 report_data.json（含 metrics），但 _run_plugin 的
    返回值仅含 plugin_id/report_type。此处从 report_data.json 还原原回测路由
    的 metadata 契约，保证下游（master engine / test_skill_pairs）兼容。
    """
    if not result or not result.get("success"):
        return result

    html_path = result.get("artifact_path", "")
    _report_dir = os.path.dirname(html_path) if html_path else ""
    data_path_out = os.path.join(_report_dir, "report_data.json") if _report_dir else ""

    report_data = {}
    if data_path_out and os.path.exists(data_path_out):
        try:
            with open(data_path_out, "r", encoding="utf-8") as f:
                report_data = json.load(f)
        except Exception as e:
            logger.warning("读取 report_data.json 失败: %s", e)

    metadata = dict(result.get("metadata") or {})
    metadata["metrics"] = report_data.get("metrics", {}) or {}
    metadata["report_data_path"] = data_path_out
    metadata["num_charts"] = report_data.get("num_charts", 0)
    result["metadata"] = metadata
    return result


def _augment_fallback_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
    """为兜底插件结果补充兼容 metadata（report_data_path / report_template）。

    兜底插件（fallback_report，自包含）委托技术/基本面插件生成默认个股报告，
    其 render 仅返回主报告 HTML，_run_plugin 只保留 plugin_id/report_type。
    此处从输出目录的 report_data.json 还原默认报告的 metadata 契约，供下游读取。
    """
    if not result or not result.get("success"):
        return result

    html_path = result.get("artifact_path", "")
    _report_dir = os.path.dirname(html_path) if html_path else ""
    data_path_out = os.path.join(_report_dir, "report_data.json") if _report_dir else ""

    report_data = {}
    if data_path_out and os.path.exists(data_path_out):
        try:
            with open(data_path_out, "r", encoding="utf-8") as f:
                report_data = json.load(f)
        except Exception as e:
            logger.warning("读取 report_data.json 失败: %s", e)

    metadata = dict(result.get("metadata") or {})
    metadata["report_data_path"] = data_path_out
    metadata["report_template"] = report_data.get("report_template", "both")
    if report_data.get("artifacts"):
        metadata["all_artifacts"] = report_data["artifacts"]
    metadata["llm_status"] = report_data.get("llm_status", "")
    result["metadata"] = metadata
    return result


# 模板类报告插件（技术/基本面）的 report_type → 门户 type / 标题 / LLM analyst_type
_TEMPLATE_PLUGIN_INFO = {
    "technical_report": {
        "portal_type": "technical_report",
        "portal_title": "个股分析-技术面",
        "analyst_type": "technical",
    },
    "fundamental_report": {
        "portal_type": "fundamental_report",
        "portal_title": "个股分析-基本面",
        "analyst_type": "fundamental",
    },
}


def _collect_plugin_llm_prompts(ctx, plugins) -> Dict[str, Any]:
    """收集模板类报告插件的 LLM prompt（供深度解读注入）。

    技术/基本面插件渲染时已通过 compute_report_data 产出带占位符的 HTML，
    但未在插件内做 LLM 调用；此处复用同一公共计算提取 prompt，交由
    聚合层统一注入深度解读。
    """
    prompts: Dict[str, Any] = {}
    try:
        from scripts.template_engine import compute_report_data
    except Exception:
        return prompts

    for plugin in plugins:
        info = _TEMPLATE_PLUGIN_INFO.get(getattr(plugin, "id", ""))
        if not info:
            continue
        template_id = info["analyst_type"]  # technical / fundamental
        try:
            comp = compute_report_data(template_id, ctx)
            if comp and comp.get("llm_prompt"):
                prompts[template_id] = comp["llm_prompt"]
        except Exception as e:
            logger.warning(f"插件[{plugin.id}] LLM prompt 收集失败: {e}")
    return prompts


def _run_plugin_many(ctx, plugins: List) -> Dict[str, Any]:
    """多插件联合生成 + 统一汇总（门户注册 / LLM 解读 / report_data.json）。

    用于 report_template="both" 等技术+基本面多命中场景：
    1. 逐个运行插件生成 HTML（支持各自 output_file）
    2. 收集 LLM prompt → 注入深度解读（技术/基本面占位符）
    3. 逐个注册门户（技术/基本面分别注册，同类型不同文件共存）
    4. 汇总 report_data.json（artifacts + llm_status）

    返回:
        result dict {success, artifact_path, metadata, error}；
        metadata 含 report_data_path / all_artifacts。
    """
    _out_dir = _resolve_report_output_dir(ctx)
    os.makedirs(_out_dir, exist_ok=True)

    generated_paths: List[str] = []
    llm_prompts: Dict[str, Any] = {}
    errors: List[str] = []

    # ── 1. 逐个运行插件生成 HTML ──
    for plugin in plugins:
        try:
            result = _run_plugin(ctx, plugin)
            if result and result.get("success"):
                generated_paths.append(result["artifact_path"])
            else:
                err = (result or {}).get("error") or f"插件[{plugin.id}]生成失败"
                errors.append(err)
                logger.error(f"插件[{plugin.id}]生成失败: {err}")
        except Exception as e:
            errors.append(f"插件[{getattr(plugin, 'id', '?')}]异常: {e}")
            logger.error(f"插件[{getattr(plugin, 'id', '?')}]异常: {e}")

    if not generated_paths:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "所有插件报告生成失败: " + "；".join(errors),
        }

    # ── 2. 收集 LLM prompt 并注入深度解读 ──
    llm_prompts = _collect_plugin_llm_prompts(ctx, plugins)
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
        from scripts.deep_analysis import inject_deep_analysis
        inject_deep_analysis(generated_paths, llm_responses, llm_prompts)

    # ── 3. 逐个注册门户（技术/基本面分别注册） ──
    for path in generated_paths:
        fname = os.path.basename(path)
        info = None
        for pid, cfg in _TEMPLATE_PLUGIN_INFO.items():
            if fname.startswith(pid.split("_")[0]) and pid in fname:
                info = cfg
                break
        if info is None:
            # 按文件名猜测（兼容非标准命名）
            if "technical" in fname:
                info = _TEMPLATE_PLUGIN_INFO["technical_report"]
            elif "fundamental" in fname:
                info = _TEMPLATE_PLUGIN_INFO["fundamental_report"]
        _rtype = info["portal_type"] if info else "report"
        _title = info["portal_title"] if info else ""
        try:
            _update_report_portal(
                path, _rtype, title=_title,
                task_id=ctx.task_id if hasattr(ctx, "task_id") else "",
            )
        except Exception as e:
            logger.warning(f"门户注册失败（不阻断）: {e}")

    # ── 4. 汇总 report_data.json ──
    template_choice = _detect_report_template(ctx)
    report_data = {
        "report_template": template_choice,
        "generated_at": datetime.now().isoformat(),
        "artifacts": generated_paths,
        "llm_status": llm_status,
    }
    data_path_out = os.path.join(_out_dir, "report_data.json")
    with open(data_path_out, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

    # T3-8: 若存在 alphalens 因子分析报告，则追加为附加产物（不进门户）
    factor_report_path = _maybe_render_factor_analysis_report(ctx)
    if factor_report_path:
        generated_paths.append(factor_report_path)
        report_data["artifacts"] = generated_paths
        with open(data_path_out, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)

    return {
        "success": True,
        "artifact_path": generated_paths[0],
        "metadata": {
            "report_template": template_choice,
            "report_data_path": data_path_out,
            "all_artifacts": generated_paths,
            "llm_prompts": llm_prompts,
            "llm_status": llm_status,
            "factor_report_path": factor_report_path or "",
        },
        "error": "",
    }


# 无兜底插件时抛此异常，由 run() 回退到终极兜底 _run_builtin_fallback
class _NoPluginMatched(Exception):
    """无插件命中且无兜底插件，需回退终极兜底。"""


def _run_plugin_auto(ctx) -> Dict[str, Any]:
    """统一插件执行入口：匹配 → 聚合 → 兜底。

    路由语义（等价收敛后的两分支）：
    1. BACKTEST 产物存在且 backtest_report 命中 → 优先回测报告。
       回测是完整策略管线的明确产物，优先级高于默认 report_template
       （与 master engine 的 REPORT 产物判断一致：有 BACKTEST 时输出 report.html）。
    2. 显式 report_template（technical/fundamental/both，无 BACKTEST）→
       过滤出模板类插件，多命中（both）走 _run_plugin_many，单命中走 _run_plugin。
    3. 其余通用 find_by_trigger 匹配（技术/基本面/资金流等）。
    4. 全部未命中 → 兜底插件（find_fallback）生成默认个股报告。
    5. 无兜底插件 → 抛 _NoPluginMatched，由 run() 回退终极兜底 _run_builtin_fallback。
    """
    meta = getattr(ctx, "metadata", {}) or {}
    explicit_template = meta.get("report_template")
    if explicit_template not in ("technical", "fundamental", "both"):
        explicit_template = None

    _ensure_plugins_loaded()
    from plugins.registry import find_by_trigger as _plugin_find
    matched = _plugin_find(ctx)

    # ── 1. 回测优先：BACKTEST 产物存在且回测插件命中 ──
    backtest = [p for p in matched if getattr(p, "id", "") == "backtest_report"]
    if backtest:
        logger.info("命中回测插件 backtest_report（BACKTEST 产物优先）")
        result = _run_plugin(ctx, backtest[0])
        return _augment_backtest_metadata(result)

    # ── 2. 显式模板请求：仅保留模板类插件 ──
    if explicit_template:
        template_plugins = [
            p for p in matched
            if getattr(p, "id", "") in _TEMPLATE_PLUGIN_INFO
        ]
        if template_plugins:
            if len(template_plugins) > 1:
                logger.info(
                    f"显式模板 [{explicit_template}] 多插件命中: "
                    f"{[p.id for p in template_plugins]}，联合生成"
                )
                return _run_plugin_many(ctx, template_plugins)
            plugin = template_plugins[0]
            logger.info(f"显式模板 [{explicit_template}] 命中插件: [{plugin.id}] {plugin.label}")
            return _run_plugin(ctx, plugin)
        # 模板插件未命中（如缺少 DATA）→ 落入兜底/内置
        logger.warning(f"显式模板 [{explicit_template}] 无插件命中，走兜底")

    # ── 3. 其余通用匹配（排除回测 + 附加产物插件，避免干扰） ──
    # factor_analysis_report 是"附加产物"：始终由 _run_plugin_many 内部
    # _maybe_render_factor_analysis_report 按需追加
    # （alphalens 目录缺失时静默跳过），不进门户、不阻断主报告，故不参与主匹配。
    generic = [
        p for p in matched
        if getattr(p, "id", "") != "backtest_report"
        and getattr(p, "id", "") != "factor_analysis_report"
    ]
    if generic:
        if len(generic) > 1:
            logger.info(f"多插件命中: {[p.id for p in generic]}，联合生成")
            return _run_plugin_many(ctx, generic)
        plugin = generic[0]
        logger.info(f"命中报告插件: [{plugin.id}] {plugin.label}")
        return _run_plugin(ctx, plugin)

    # ── 4. 兜底插件 ──
    from plugins.registry import find_fallback as _plugin_fallback
    fallback = _plugin_fallback()
    if fallback is not None:
        logger.info(f"使用兜底插件: [{fallback.id}] {fallback.label}")
        result = _run_plugin(ctx, fallback)
        return _augment_fallback_metadata(result)

    # ── 5. 无兜底插件 → 回退内置模板 ──
    raise _NoPluginMatched()


def _update_report_portal(
    html_path: str,
    report_type: str,
    title: str = "",
    task_id: str = "",
    live: bool = False,
    **extra: Any,
) -> None:
    """报告生成成功后更新 manifest.json 并刷新门户页 index.html。

    参数:
        html_path: 报告 HTML 文件绝对路径
        report_type: 报告类型（technical_report/fundamental_report/portfolio/execution/attribution/backtest）
        title: 报告标题（空时按类型取默认）
        task_id: 任务 ID
        live: 是否为 LIVE 实时报告
        **extra: 额外字段（如 backend/snapshot_file）
    """
    try:
        from scripts.report_portal import upsert_report, generate_portal
        _report_dir = os.path.dirname(html_path)
        upsert_report(
            report_dir=_report_dir,
            report_type=report_type,
            file_path=html_path,
            title=title or None,
            task_id=task_id,
            live=live,
            **extra,
        )
        generate_portal(_report_dir)
    except Exception as e:
        logger.warning(f"更新报告门户失败（不阻断）: {e}")


def _build_attribution_llm_prompt(
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

    if not pnl_by_stock.empty:
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


def _build_fallback_attribution(prompt_data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 不可用时，生成规则兜底绩效归因解读"""
    user_prompt = prompt_data.get("user_prompt", "")

    # 从 prompt 中提取关键数据
    import re
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


def _render_attribution_analysis(resp: Dict[str, Any]) -> str:
    """渲染绩效归因 LLM 解读 HTML"""
    import html as _html_lib

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


def _inject_attribution_analysis(
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
            resp = _build_fallback_attribution(llm_prompts.get("attribution", {}))
        rendered = _render_attribution_analysis(resp)
        html_content = html_content.replace(
            "<!--LLM_ATTRIBUTION_PLACEHOLDER-->", rendered
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"绩效归因解读已注入: {html_path}")


def _run_plugin_enhanced(ctx, intent: str, plugin_id: str) -> Dict[str, Any]:
    """增强版插件委托：统一承载 attribution/portfolio/execution 三类报告的
    产物校验 + 插件委托 + metadata 组装（合并原三个独立薄包装函数）。

    相比纯 `_run_plugin`，本函数在委托插件前做产物存在性校验，缺失时返回
    明确错误（而非静默落入兜底模板），并对特定插件补充兼容 metadata。

    参数:
        ctx:      Context 对象
        intent:   报告意图（attribution / portfolio / execution）
        plugin_id: 目标插件 id（attribution_report / portfolio_report / execution_report）
    返回:
        与其它 _run_*_report 一致的 result dict {success, artifact_path, metadata, error}
    """
    meta = getattr(ctx, 'metadata', {}) or {}

    # ── 1. 产物前置校验（缺失返回友好错误） ──
    required_artifact = {
        "attribution": "EXECUTION",
        "portfolio": "PORTFOLIO",
        "execution": "EXECUTION",
    }.get(intent)
    if required_artifact:
        artifact = ctx.get_artifact(required_artifact) if hasattr(ctx, 'get_artifact') else None
        if not artifact:
            _hint = {
                "attribution": "请先执行模拟/实盘交易",
                "portfolio": "请先执行组合优化",
                "execution": "请先执行模拟/实盘交易",
            }[intent]
            return {
                "success": False, "artifact_path": "", "metadata": {},
                "error": f"未找到 {required_artifact} 产物，无法生成{_report_names[intent]}。{_hint}。"
            }

    # attribution 额外校验 ledger.jsonl 存在
    if intent == "attribution":
        execution_dir = os.path.dirname(artifact) if os.path.isfile(artifact) else artifact
        ledger_path = os.path.join(execution_dir, "ledger.jsonl")
        if not os.path.exists(ledger_path):
            return {
                "success": False, "artifact_path": "", "metadata": {},
                "error": f"ledger 文件不存在: {ledger_path}"
            }

    # ── 2. 委托插件生成 ──
    try:
        html_path = _run_plugin_by_id(ctx, plugin_id)
    except Exception as e:
        logger.exception(f"报告[{intent}]生成失败")
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": str(e)
        }

    # ── 3. 门户注册 + metadata 组装（与迁移前内置路由返回一致） ──
    if intent == "portfolio":
        portfolio_metadata = meta.get("portfolio_metadata", {}) or {}
        _update_report_portal(
            html_path, "portfolio",
            task_id=ctx.task_id if hasattr(ctx, 'task_id') else "",
        )
        return {
            "success": True, "artifact_path": html_path,
            "metadata": {
                "report_type": "portfolio",
                "num_assets": portfolio_metadata.get("num_assets", 0),
                "optimization_method": portfolio_metadata.get("optimization_method", "unknown"),
            },
            "error": "",
        }

    if intent == "execution":
        # 主引擎将 EXECUTION 阶段 metadata 存于 ctx.metadata["EXECUTION"]，兼容旧 key
        execution_metadata = (
            meta.get("EXECUTION") if isinstance(meta.get("EXECUTION"), dict) else {}
        ) or (meta.get("execution_metadata") if isinstance(meta.get("execution_metadata"), dict) else {})
        _update_report_portal(
            html_path, "execution",
            task_id=ctx.task_id if hasattr(ctx, 'task_id') else "",
            backend=execution_metadata.get("backend", ""),
        )
        return {
            "success": True, "artifact_path": html_path,
            "metadata": {
                "report_type": "execution",
                "mode": execution_metadata.get("mode", "paper"),
                "orders_executed": execution_metadata.get("orders_executed", 0),
                "orders_failed": execution_metadata.get("orders_failed", 0),
            },
            "error": "",
        }

    # attribution：从插件落盘的 report_data.json 还原元数据
    _report_dir = os.path.dirname(html_path)
    data_path_out = os.path.join(_report_dir, "report_data.json")
    report_data = {}
    if os.path.exists(data_path_out):
        try:
            with open(data_path_out, "r", encoding="utf-8") as f:
                report_data = json.load(f)
        except Exception as e:
            logger.warning(f"读取 report_data.json 失败: {e}")

    _update_report_portal(
        html_path, "attribution",
        task_id=ctx.task_id if hasattr(ctx, 'task_id') else "",
    )
    return {
        "success": True, "artifact_path": html_path,
        "metadata": {
            "report_type": "attribution",
            "llm_prompts": {"attribution": {"system_prompt": "", "user_prompt": ""}},
            "llm_status": report_data.get("llm_status", "skipped"),
            "metrics": report_data.get("metrics", {}) or {},
            "tx_stats": report_data.get("tx_stats", {}) or {},
            "rt_stats": report_data.get("rt_stats", {}) or {},
            "report_data_path": data_path_out,
        },
        "error": "",
    }


# 报告意图 → （报告名 / 目标插件 id），用于 run() 统一分发
_INTENT_PLUGIN = {
    "attribution": ("绩效归因报告", "attribution_report"),
    "portfolio": ("组合优化报告", "portfolio_report"),
    "execution": ("执行监控报告", "execution_report"),
}
_report_names = {k: v[0] for k, v in _INTENT_PLUGIN.items()}


def _run_builtin_fallback(ctx) -> Dict[str, Any]:
    """终极兜底：直接运行兜底插件（fallback_report）生成默认个股报告。

    仅在分支 A 不可用（ENABLE_PLUGIN 关闭）或插件匹配机制抛异常时触达。
    兜底插件已自包含（技术面 + 基本面，含 LLM 深度解读注入），不再依赖
    已删除的内置模板内核 _run_template_report。

    兜底插件缺失时返回 success=False（尽力而为契约：不抛异常）。
    """
    _ensure_plugins_loaded()
    from plugins.registry import find_fallback as _plugin_fallback
    fallback = _plugin_fallback()
    if fallback is None:
        return {
            "success": False, "artifact_path": "", "metadata": {},
            "error": "无兜底插件（fallback_report）可用，无法生成默认报告",
        }
    result = _run_plugin(ctx, fallback)
    return _augment_fallback_metadata(result)


def run(ctx) -> Dict[str, Any]:
    """
    reports-engine 的 run 函数

    路由已收敛为两分支（统一走自包含插件路径，不再有内置模板内核重复组装）：
    - 分支 A（ENABLE_PLUGIN）：插件匹配（含多命中聚合 + 自定义输出文件名 +
      fallback 兜底），由 _run_plugin_auto 统一执行。7 份报告均走自包含插件，
      公共计算 compute_report_data 由 scripts.template_engine 提供。
    - 分支 B：终极兜底 _run_builtin_fallback（无插件机制 / 插件匹配异常时），
      直接运行兜底插件 fallback_report 生成默认个股报告。

    保留项（保证兼容）：
    - 意图产物校验：attribution/portfolio/execution 无产物时返回 success=False +
      明确错误（_run_plugin_enhanced 承载，插件 requires 缺失是静默跳过，故需保留显式校验）。

    参数:
        ctx: Context 对象

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "metadata": {...},
            "error": str
        }
    """
    meta = getattr(ctx, 'metadata', {}) or {}

    # ── 意图产物校验 + 委托（attribution / portfolio / execution） ──
    # 这三类报告走独立增强委托（含产物校验 + metadata 组装），不进入通用插件匹配。
    intent = meta.get("report_intent")
    if intent in _INTENT_PLUGIN:
        _name, _pid = _INTENT_PLUGIN[intent]
        logger.info(f"检测到 {_name} 意图，委托插件 [{_pid}]")
        return _run_plugin_enhanced(ctx, intent, _pid)

    # ── 分支 A：插件匹配（含聚合 + 兜底） ──
    if ENABLE_PLUGIN:
        try:
            return _run_plugin_auto(ctx)
        except _NoPluginMatched:
            logger.info("无插件命中且无兜底插件，回退终极兜底")
        except Exception as e:
            logger.warning(f"插件匹配异常，回退终极兜底: {e}")

    # ── 分支 B：终极兜底（无插件机制 / 无插件命中 / 匹配异常） ──
    return _run_builtin_fallback(ctx)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            ctx_dict = json.load(f)
        from scripts.context import Context
        ctx = Context.from_dict(ctx_dict)
    else:
        from scripts.context import Context
        ctx = Context(
            task_id="test_report",
            stock_pool=[],
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        ctx.update_artifact("DATA", "./workspace/data/cleaned_data.parquet")
        ctx.update_artifact("FACTOR", "./workspace/factors/factor_data.parquet")

    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
