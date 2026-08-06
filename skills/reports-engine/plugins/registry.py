# -*- coding: utf-8 -*-
"""报告插件注册表。

定义 ReportPlugin 数据结构与插件注册表（REGISTRY），供引擎路由使用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("reports-engine.plugins.registry")


# 渲染器统一签名：render(data: dict, ctx, output_path: str) -> str
RenderFunc = Callable[[Dict[str, Any], Any, str], str]


@dataclass
class ReportPlugin:
    """一份报告插件。

    属性:
        id:          插件唯一 ID（与文件夹名一致）
        label:       报告显示名称
        icon:        门户图标（emoji）
        version:     插件版本
        enabled:     是否启用
        triggers:    触发规则列表。每条规则为 dict，支持三种方式：
                       {field, equals}   —— 按 ctx.metadata 字段精确匹配
                       {keyword: [...]}  —— 按用户意图关键词识别
                       {artifact: KEY}   —— 按阶段产物存在性触发
        requires:    所需产物（DATA/FACTOR/BACKTEST/PORTFOLIO/...），
                     生成前校验，缺任一则跳过
        data_contract: 输入数据契约（strict + 各产物字段约束），暂为文档/校验参考
        portal:      门户元信息 {enabled, display_order, ...}
        render:      渲染函数（由 loader 从 render.py 注入）
        base_dir:    插件目录（绝对路径）
    """

    id: str
    label: str = ""
    icon: str = "📄"
    version: str = "1.0.0"
    enabled: bool = True
    triggers: List[Dict[str, Any]] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    data_contract: Dict[str, Any] = field(default_factory=dict)
    portal: Dict[str, Any] = field(default_factory=lambda: {"enabled": True, "display_order": 99})
    render: Optional[RenderFunc] = None
    base_dir: str = ""

    # ── 触发匹配 ──────────────────────────────────────────────
    def matches(self, ctx) -> bool:
        """判断当前 ctx 是否触发本插件（任一 trigger 规则满足即触发）。"""
        if not self.enabled:
            return False

        # 1) 产物存在性触发（requires 声明的产物若缺失，则不触发）
        for key in self.requires:
            if not self._artifact_exists(ctx, key):
                logger.debug(f"插件[{self.id}] 所需产物缺失: {key}，不触发")
                return False

        # 2) trigger 规则匹配
        if not self.triggers:
            # 无显式 trigger，默认仅在"模板化个股分析"场景兜底触发
            return False

        for rule in self.triggers:
            if self._match_rule(rule, ctx):
                return True

        return False

    def _match_rule(self, rule: Dict[str, Any], ctx) -> bool:
        if "field" in rule:
            # 按 ctx.metadata 字段精确匹配
            field_val = self._get_meta_field(ctx, rule["field"])
            if field_val is not None and str(field_val) == str(rule["equals"]):
                return True
        if "keyword" in rule:
            # 按用户意图关键词识别
            user_input = ""
            if hasattr(ctx, "user_intent") and ctx.user_intent:
                user_input = str(ctx.user_intent)
            for kw in rule["keyword"]:
                if kw and kw in user_input:
                    return True
        if "artifact" in rule:
            # 按阶段产物存在性触发
            if self._artifact_exists(ctx, rule["artifact"]):
                return True
        return False

    @staticmethod
    def _get_meta_field(ctx, field: str) -> Optional[Any]:
        if not hasattr(ctx, "metadata") or not ctx.metadata:
            return None
        if field in ctx.metadata:
            return ctx.metadata[field]
        # 支持点号路径（如 report_intent）
        return None

    @staticmethod
    def _artifact_exists(ctx, key: str) -> bool:
        if not hasattr(ctx, "get_artifact"):
            return False
        try:
            path = ctx.get_artifact(key)
            return bool(path) and os.path.exists(path)
        except Exception:
            return False


# ── 注册表 ────────────────────────────────────────────────────
REGISTRY: Dict[str, ReportPlugin] = {}


def register(plugin: ReportPlugin, overwrite: bool = False) -> bool:
    """注册插件。id 冲突时按 overwrite 决定覆盖或跳过。"""
    if plugin.id in REGISTRY and not overwrite:
        logger.warning(f"插件已存在，跳过注册: {plugin.id}")
        return False
    REGISTRY[plugin.id] = plugin
    logger.info(f"插件已注册: [{plugin.id}] {plugin.label}")
    return True


def unregister(plugin_id: str) -> bool:
    """注销插件。"""
    return REGISTRY.pop(plugin_id, None) is not None


def get(plugin_id: str) -> Optional[ReportPlugin]:
    return REGISTRY.get(plugin_id)


def all_plugins() -> List[ReportPlugin]:
    return list(REGISTRY.values())


def find_by_trigger(ctx) -> List[ReportPlugin]:
    """返回当前 ctx 下所有满足触发条件的插件（按 portal.display_order 升序）。"""
    matched = [p for p in REGISTRY.values() if p.matches(ctx)]
    matched.sort(key=lambda p: p.portal.get("display_order", 99))
    return matched


# 延迟 import os（避免循环依赖）
import os  # noqa: E402
