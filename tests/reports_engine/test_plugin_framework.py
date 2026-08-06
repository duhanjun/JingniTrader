"""报告插件框架（Report Plugin）测试。

覆盖：
- registry: ReportPlugin.matches() 三种触发方式 + 注册/注销/查询
- loader:   扫描/加载示例插件
- 端到端:   run() 路由触发插件生成报告
"""
from __future__ import annotations

import os
import sys
import importlib.util as ilu
from unittest import mock

import numpy as np
import pandas as pd
import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_ENGINE_PATH = os.path.join(REPORTS_ENGINE_DIR, "engine.py")
PLUGINS_DIR = os.path.join(REPORTS_ENGINE_DIR, "plugins")

if REPORTS_ENGINE_DIR not in sys.path:
    sys.path.insert(0, REPORTS_ENGINE_DIR)

_CONTEXT_MODULE = None
_ENGINE_MOD = None
_PLUGIN_MODULES = {}


def _get_context_class():
    """获取 Context 类（处理 scripts 包切换）。"""
    global _CONTEXT_MODULE
    if _CONTEXT_MODULE is not None:
        return _CONTEXT_MODULE
    context_path = os.path.join(ROOT, "scripts", "context.py")
    if os.path.exists(context_path):
        spec = ilu.spec_from_file_location("jingni_context_plugin", context_path)
        mod = ilu.module_from_spec(spec)
        sys.modules["jingni_context_plugin"] = mod
        spec.loader.exec_module(mod)
        _CONTEXT_MODULE = mod.Context
        return mod.Context
    raise ImportError("无法加载 Context 类")


def _load_engine():
    """加载 reports-engine/engine.py 为独立模块。

    每次调用都重新加载 reports-engine 的 scripts 包（conftest 会在测试间
    重置 scripts 为主包），确保插件 render 内部 import scripts.* 正确解析。
    """
    global _ENGINE_MOD
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)
    scripts_dir = os.path.join(REPORTS_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts", init_py, submodule_search_locations=[scripts_dir]
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)
    for _m in ("talib", "pandas_ta"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()
    spec = ilu.spec_from_file_location("reports_engine_engine_plugin", REPORTS_ENGINE_PATH)
    mod = ilu.module_from_spec(spec)
    sys.modules["reports_engine_engine_plugin"] = mod
    spec.loader.exec_module(mod)
    _ENGINE_MOD = mod
    return mod


def _load_plugins():
    """加载插件框架模块（registry/loader）。"""
    if not _PLUGIN_MODULES:
        from plugins import registry, loader
        _PLUGIN_MODULES["registry"] = registry
        _PLUGIN_MODULES["loader"] = loader
    return _PLUGIN_MODULES


def _make_ctx(stock_pool=None, user_intent=""):
    """构造 reports-engine 标准输入 Context。"""
    Context = _get_context_class()
    return Context(
        task_id="test_plugin",
        user_intent=user_intent,
        stock_pool=stock_pool or ["000001.SZ", "600000.SH"],
        start_date="2024-01-01",
        end_date="2024-06-30",
    )


def _make_data_artifact():
    """构造 DATA 产物（parquet），返回路径。"""
    data_dir = os.path.join(ROOT, "workspace", "test_plugin_data")
    os.makedirs(data_dir, exist_ok=True)
    dates = pd.date_range("2024-01-01", "2024-03-31", freq="B")
    rng = np.random.default_rng(42)
    px = 100.0
    rows = []
    for d in dates:
        px *= (1 + rng.normal(0.001, 0.02))
        rows.append({"date": d, "code": "002594.SZ", "close": px,
                     "volume": int(1e6 + 1e5 * np.random.random()),
                     "amount": px * 1e6, "open": px * 0.99, "high": px * 1.01, "low": px * 0.98})
    path = os.path.join(data_dir, "data.parquet")
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestReportPluginMatches:
    """ReportPlugin.matches() 触发逻辑。"""

    def test_keyword_trigger(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        plugin = registry.ReportPlugin(
            id="kw", label="关键词", triggers=[{"keyword": ["资金流", "北向"]}]
        )
        ctx = _make_ctx(user_intent="分析 002594.SZ 的资金流")
        assert plugin.matches(ctx) is True
        ctx2 = _make_ctx(user_intent="分析 002594.SZ 的技术面")
        assert plugin.matches(ctx2) is False

    def test_field_trigger(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        plugin = registry.ReportPlugin(
            id="fld", label="字段", triggers=[{"field": "report_intent", "equals": "capital"}]
        )
        ctx = _make_ctx()
        ctx.metadata["report_intent"] = "capital"
        assert plugin.matches(ctx) is True
        ctx.metadata["report_intent"] = "attribution"
        assert plugin.matches(ctx) is False

    def test_artifact_trigger(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        plugin = registry.ReportPlugin(
            id="art", label="产物", triggers=[{"artifact": "BACKTEST"}], requires=[]
        )
        ctx = _make_ctx()
        # 无产物 → 不触发
        assert plugin.matches(ctx) is False
        # 有产物 → 触发（trigger 规则命中 + requires 通过）
        data_path = _make_data_artifact()
        ctx.update_artifact("BACKTEST", data_path)
        assert plugin.matches(ctx) is True

    def test_disabled_plugin_not_match(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        plugin = registry.ReportPlugin(
            id="off", label="关闭", enabled=False, triggers=[{"keyword": ["资金流"]}]
        )
        ctx = _make_ctx(user_intent="资金流")
        assert plugin.matches(ctx) is False

    def test_requires_missing_blocks(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        plugin = registry.ReportPlugin(
            id="req", label="需要产物", requires=["FACTOR"],
            triggers=[{"keyword": ["资金流"]}]
        )
        ctx = _make_ctx(user_intent="资金流")
        assert plugin.matches(ctx) is False


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestPluginRegistry:
    """插件注册表。"""

    def test_register_get_unregister(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        p = registry.ReportPlugin(id="reg1", label="测试")
        assert registry.register(p) is True
        assert registry.get("reg1") is p
        assert registry.register(p) is False  # 重复注册跳过
        assert registry.unregister("reg1") is True
        assert registry.get("reg1") is None

    def test_find_by_trigger(self):
        plugins = _load_plugins()
        registry = plugins["registry"]
        p = registry.ReportPlugin(id="find1", label="查找", triggers=[{"keyword": ["资金流"]}])
        registry.register(p)
        ctx = _make_ctx(user_intent="分析资金流")
        assert [x.id for x in registry.find_by_trigger(ctx)] == ["find1"]
        registry.unregister("find1")


@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestPluginLoader:
    """插件加载器（真实示例插件）。"""

    def test_scan_loads_example_plugin(self):
        plugins = _load_plugins()
        loader = plugins["loader"]
        registry = plugins["registry"]
        loaded = loader.scan(PLUGINS_DIR)
        assert "capital_flow_report" in loaded
        p = registry.get("capital_flow_report")
        assert p is not None
        assert p.label == "资金流分析"
        assert p.render is not None  # render.py 已导入

    def test_reload_refreshes(self):
        plugins = _load_plugins()
        loader = plugins["loader"]
        registry = plugins["registry"]
        loader.reload(PLUGINS_DIR)
        assert registry.get("capital_flow_report") is not None


@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestPluginE2E:
    """插件端到端（run() 触发示例插件生成报告）。"""

    def test_run_triggers_plugin(self):
        _load_engine()
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)

        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_plugin_e2e",
            user_intent="分析 002594.SZ 的主力资金流情况",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        ctx.update_artifact("DATA", data_path)
        os.environ["QUANT_WORK_DIR"] = "./workspace"

        mod = _load_engine()
        result = mod.run(ctx)
        assert result.get("success") is True
        path = result.get("artifact_path", "")
        assert path and "capital_flow_report" in str(path)
        assert os.path.exists(path)
        html = open(path, encoding="utf-8").read()
        assert "资金流分析" in html
        assert "JingniTrader" in html  # base 骨架导航


@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestTechnicalReportPlugin:
    """技术分析报告迁入插件框架（内置报告插件化迁移示范）。"""

    def _load_with_scan(self):
        _load_engine()
        plugins = _load_plugins()
        plugins["loader"].scan(PLUGINS_DIR)
        return plugins["registry"]

    def _make_tech_ctx(self):
        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_tech_plugin",
            user_intent="分析 002594.SZ 技术面",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        ctx.metadata["report_intent"] = "technical"
        ctx.update_artifact("DATA", data_path)
        os.environ["QUANT_WORK_DIR"] = "./workspace"
        return ctx

    def test_technical_report_registered(self):
        registry = self._load_with_scan()
        assert registry.get("technical_report") is not None

    def test_technical_report_triggered(self):
        registry = self._load_with_scan()
        ctx = self._make_tech_ctx()
        matched = [p.id for p in registry.find_by_trigger(ctx)]
        assert "technical_report" in matched

    def test_technical_report_plugin_generates(self):
        self._load_with_scan()
        ctx = self._make_tech_ctx()
        mod = _load_engine()
        result = mod.run(ctx)
        assert result.get("success") is True
        path = result.get("artifact_path", "")
        assert path and "technical_report" in str(path)
        assert os.path.exists(path)
        html = open(path, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in html
        assert "JingniTrader" in html       # base 骨架导航
        assert "技术指标" in html or "MACD" in html or "RSI" in html

    def test_fundamental_still_uses_builtin(self):
        """fundamental 场景不应命中技术报告插件，仍走内置路由。"""
        registry = self._load_with_scan()
        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_fund_builtin",
            user_intent="分析 002594.SZ 基本面",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        ctx.metadata["report_intent"] = "fundamental"
        ctx.update_artifact("DATA", data_path)
        # 技术报告插件不应在 fundamental 场景触发
        matched = [p.id for p in registry.find_by_trigger(ctx)]
        assert "technical_report" not in matched
