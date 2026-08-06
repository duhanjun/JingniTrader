# -*- coding: utf-8 -*-
"""报告插件加载器。

扫描 plugins/ 目录下的插件文件夹，读取 plugin.yaml 并动态导入 render.py，
构建 ReportPlugin 并注册到 registry。支持 reload() 实现新增插件无需重启。
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from typing import List

from .registry import REGISTRY, ReportPlugin, register, unregister

logger = logging.getLogger("reports-engine.plugins.loader")

# 默认插件目录（相对本文件：plugins/ 的父目录下再找？ 不，插件就在 plugins/ 下）
_DEFAULT_PLUGINS_DIR = os.path.dirname(os.path.abspath(__file__))


def _parse_plugin_yaml(yaml_path: str) -> dict:
    """解析 plugin.yaml，失败返回空 dict。"""
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"解析 plugin.yaml 失败 {yaml_path}: {e}")
        return {}


def _load_render_func(plugin_dir: str):
    """动态导入插件目录下的 render.py，返回其中的 render 函数。

    render.py 需定义 `render(data, ctx, output_path) -> str`。
    """
    render_path = os.path.join(plugin_dir, "render.py")
    if not os.path.exists(render_path):
        return None
    try:
        # 将插件根目录加入 sys.path，使 render.py 内可导入 _engine_access 等辅助模块
        plugins_root = os.path.dirname(plugin_dir)
        if plugins_root not in sys.path:
            sys.path.insert(0, plugins_root)
        module_name = f"report_plugin_{os.path.basename(plugin_dir)}_render"
        spec = importlib.util.spec_from_file_location(module_name, render_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        render_func = getattr(mod, "render", None)
        if render_func is None:
            logger.warning(f"插件 {plugin_dir} 的 render.py 未定义 render() 函数")
        return render_func
    except Exception as e:
        logger.error(f"加载插件 render.py 失败 {plugin_dir}: {e}")
        return None


def load(plugin_dir: str, overwrite: bool = True) -> bool:
    """加载单个插件文件夹，构建 ReportPlugin 并注册。"""
    if not os.path.isdir(plugin_dir):
        logger.warning(f"插件目录不存在: {plugin_dir}")
        return False

    yaml_path = os.path.join(plugin_dir, "plugin.yaml")
    if not os.path.exists(yaml_path):
        logger.debug(f"跳过（无 plugin.yaml）: {plugin_dir}")
        return False

    cfg = _parse_plugin_yaml(yaml_path)
    plugin_id = cfg.get("id") or os.path.basename(plugin_dir)

    # 校验 render.py 存在
    render_path = os.path.join(plugin_dir, "render.py")
    if not os.path.exists(render_path):
        logger.error(f"插件 [{plugin_id}] 缺少 render.py，跳过")
        return False

    render_func = _load_render_func(plugin_dir)
    if render_func is None:
        logger.error(f"插件 [{plugin_id}] render.py 加载失败，跳过")
        return False

    plugin = ReportPlugin(
        id=plugin_id,
        label=cfg.get("label", plugin_id),
        icon=cfg.get("icon", "📄"),
        version=str(cfg.get("version", "1.0.0")),
        enabled=bool(cfg.get("enabled", True)),
        triggers=cfg.get("trigger", []),
        requires=cfg.get("requires", []),
        data_contract=cfg.get("data_contract", {}),
        portal=cfg.get("portal", {"enabled": True, "display_order": 99}),
        render=render_func,
        base_dir=plugin_dir,
    )
    return register(plugin, overwrite=overwrite)


def scan(plugins_dir: str = None) -> List[str]:
    """扫描插件目录下所有含 plugin.yaml 的文件夹并加载。返回加载成功的插件 id。"""
    plugins_dir = plugins_dir or _DEFAULT_PLUGINS_DIR
    loaded: List[str] = []
    if not os.path.isdir(plugins_dir):
        logger.warning(f"插件目录不存在: {plugins_dir}")
        return loaded

    for entry in sorted(os.listdir(plugins_dir)):
        full = os.path.join(plugins_dir, entry)
        if os.path.isdir(full) and os.path.exists(os.path.join(full, "plugin.yaml")):
            if load(full):
                loaded.append(entry)
    return loaded


def reload(plugins_dir: str = None) -> List[str]:
    """重新扫描并刷新注册表（清空后重新加载），支持新增插件无需重启。"""
    # 清空现有注册
    for pid in list(REGISTRY.keys()):
        unregister(pid)
    return scan(plugins_dir)


def get_enabled() -> List[ReportPlugin]:
    """返回当前启用的插件。"""
    return [p for p in REGISTRY.values() if p.enabled]
