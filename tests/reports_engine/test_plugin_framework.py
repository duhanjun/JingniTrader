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
        assert "fallback_report" in loaded
        p = registry.get("fallback_report")
        assert p is not None
        assert p.label == "个股分析兜底"
        assert p.render is not None  # render.py 已导入

    def test_reload_refreshes(self):
        plugins = _load_plugins()
        loader = plugins["loader"]
        registry = plugins["registry"]
        loader.reload(PLUGINS_DIR)
        assert registry.get("fallback_report") is not None


@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestPluginE2E:
    """插件端到端（run() 触发内置报告插件生成报告）。"""

    def test_run_triggers_plugin(self):
        _load_engine()
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)

        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_plugin_e2e",
            user_intent="分析 002594.SZ 的技术面情况",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        ctx.metadata["report_intent"] = "technical"
        ctx.update_artifact("DATA", data_path)
        os.environ["QUANT_WORK_DIR"] = "./workspace"

        mod = _load_engine()
        result = mod.run(ctx)
        assert result.get("success") is True
        path = result.get("artifact_path", "")
        assert path and "technical_report" in str(path)
        assert os.path.exists(path)
        html = open(path, encoding="utf-8").read()
        assert "技术分析" in html
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


@pytest.mark.skill_reports_engine
@pytest.mark.integration
class TestPluginSelfContainment:
    """自包含化验收（阶段 A-D）：内置报告迁移为自包含插件。

    校验：
    - 迁移的 7 个插件 render.py 不再依赖 engine.py 内部函数 /
      _engine_access / ReportTemplateEngine.generate() / _run_*_report。
    - 插件声明了兼容的 report_type 元数据。
    - 触发机制：技术/基本面/归因/组合/执行/因子分析插件可被 find_by_trigger 命中。
    """

    # 已自包含化的内置报告插件及其兼容 report_type
    _MIGRATED = {
        "technical_report": "technical",
        "fundamental_report": "fundamental",
        "attribution_report": "attribution",
        "portfolio_report": "portfolio",
        "execution_report": "execution",
        "backtest_report": "backtest",
        "factor_analysis_report": "factor_analysis",
    }
    # render.py 中不允许出现的 engine 内部依赖（正则匹配）
    _FORBIDDEN = [
        r"\bfrom\s+engine\b",
        r"\bimport\s+engine\b",
        r"_engine_access",
        r"ReportTemplateEngine\.generate\(",
        r"from\s+engine\s+import",
    ]

    def _load_with_engine(self):
        """加载 engine（设置 scripts 包）并扫描插件，返回 registry。"""
        _load_engine()
        plugins = _load_plugins()
        plugins["loader"].scan(PLUGINS_DIR)
        return plugins["registry"]

    def test_all_migrated_plugins_registered(self):
        """7 个迁移插件均已注册且 report_type 兼容字段正确。"""
        registry = self._load_with_engine()
        for pid, expected_rt in self._MIGRATED.items():
            p = registry.get(pid)
            assert p is not None, f"插件未注册: {pid}"
            assert p.report_type == expected_rt, f"{pid} report_type 应为 {expected_rt}"
            assert p.render is not None, f"{pid} render 未加载"

    def test_migrated_render_self_contained(self):
        """迁移插件的 render.py 不依赖 engine.py 内部函数 / _engine_access。

        仅检查代码（剔除注释与文档字符串），避免 docstring 中的说明文字误报。
        """
        import re
        # 去掉注释行与三引号文档字符串
        def _strip_comments(src):
            src = re.sub(r'"""[\s\S]*?"""', "", src)
            src = re.sub(r"'''[\s\S]*?'''", "", src)
            lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
            return "\n".join(lines)

        for pid in self._MIGRATED:
            render_path = os.path.join(PLUGINS_DIR, pid, "render.py")
            assert os.path.exists(render_path), f"{pid} 缺少 render.py"
            src = open(render_path, "r", encoding="utf-8").read()
            code = _strip_comments(src)
            for pat in self._FORBIDDEN:
                assert not re.search(pat, code), (
                    f"{pid}/render.py 包含禁用依赖: {pat}"
                )

    def test_trigger_mechanism_registered(self):
        """技术/基本面/归因/组合/执行插件可通过 find_by_trigger 命中。"""
        registry = self._load_with_engine()

        Context = _get_context_class()
        data_path = _make_data_artifact()

        cases = {
            "technical_report": ("technical", ["DATA"]),
            "fundamental_report": ("fundamental", ["DATA"]),
            "attribution_report": ("attribution", ["EXECUTION"]),
            "portfolio_report": ("portfolio", ["PORTFOLIO"]),
            "execution_report": ("execution", ["EXECUTION"]),
        }
        for pid, (intent, artifacts) in cases.items():
            ctx = Context(
                task_id=f"test_trigger_{pid}",
                user_intent=f"分析 {intent}",
                current_stage="REPORT",
                stock_pool=["002594.SZ"],
                benchmark="000300.SH",
            )
            ctx.metadata["report_intent"] = intent
            if "DATA" in artifacts:
                ctx.update_artifact("DATA", data_path)
            if "EXECUTION" in artifacts:
                ctx.update_artifact("EXECUTION", data_path)
            if "PORTFOLIO" in artifacts:
                ctx.update_artifact("PORTFOLIO", data_path)
            matched = [x.id for x in registry.find_by_trigger(ctx)]
            assert pid in matched, f"{pid} 未被 find_by_trigger 命中"

    def test_factor_analysis_artifact_trigger(self):
        """P0: factor_analysis_report 通过 {artifact: FACTOR} 触发契约。

        修复前 plugin.yaml 使用 {field: artifact, op: exists, value: FACTOR}
        声明式写法，与 registry._match_rule 的 {artifact: KEY} 契约不匹配，
        导致 find_by_trigger 永不命中该插件。本测试固化修复后的契约。
        """
        registry = self._load_with_engine()
        Context = _get_context_class()

        ctx = Context(
            task_id="test_factor_artifact",
            user_intent="分析因子",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        # 无 FACTOR 产物 → 不触发
        assert "factor_analysis_report" not in [
            x.id for x in registry.find_by_trigger(ctx)
        ]
        # 有 FACTOR 产物 → 触发
        factor_path = _make_data_artifact()
        ctx.update_artifact("FACTOR", factor_path)
        matched = [x.id for x in registry.find_by_trigger(ctx)]
        assert "factor_analysis_report" in matched, (
            "factor_analysis_report 未通过 {artifact: FACTOR} 契约命中（P0 未生效）"
        )


@pytest.mark.integration
class TestPluginOutputFile:
    """output_file 自定义输出文件名（回测报告 → report.html）。"""

    def test_backtest_plugin_output_file_declared(self):
        """backtest_report 声明 output_file=report.html，无需复制改名。"""
        _load_engine()  # 先加载 engine，设置 scripts 包，保证 render.py 可导入
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)
        registry = plugins["registry"]

        plugin = registry.get("backtest_report")
        assert plugin is not None
        assert plugin.output_file == "report.html", (
            "backtest_report 应声明 output_file=report.html"
        )

    def test_run_backtest_outputs_report_html(self):
        """run() 有 BACKTEST 产物时直接产出 report.html（含 metrics 元数据）。"""
        _load_engine()
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)

        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_backtest_output",
            user_intent="获取近3年A股数据做一个反转因子选股回测并生成绩效报告",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        ctx.update_artifact("DATA", data_path)
        ctx.update_artifact("BACKTEST", data_path)
        os.environ["QUANT_WORK_DIR"] = "./workspace"

        mod = _load_engine()
        result = mod.run(ctx)
        assert result.get("success") is True, result.get("error")
        path = result.get("artifact_path", "")
        assert os.path.basename(path) == "report.html", (
            "回测报告应直接输出 report.html（output_file 生效，无需复制改名）"
        )
        assert os.path.exists(path)
        meta = result.get("metadata", {})
        # 兼容旧回测路由 metadata 契约
        assert "report_data_path" in meta
        assert "metrics" in meta
        assert "num_charts" in meta


@pytest.mark.integration
class TestPluginMultiAggregation:
    """多插件命中联合生成（report_template=both → 技术面 + 基本面）。"""

    def _build_ctx(self):
        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_both_agg",
            user_intent="分析 002594.SZ",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        ctx.update_artifact("DATA", data_path)
        ctx.metadata["report_template"] = "both"
        os.environ["QUANT_WORK_DIR"] = "./workspace"
        return ctx

    def test_find_by_trigger_matches_both_template_plugins(self):
        """report_template=both 时 technical_report 与 fundamental_report 同时命中。"""
        _load_engine()  # 设置 scripts 包，保证 render.py 可导入
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)
        registry = plugins["registry"]

        ctx = self._build_ctx()
        matched = [x.id for x in registry.find_by_trigger(ctx)]
        assert "technical_report" in matched, "report_template=both 未命中 technical_report"
        assert "fundamental_report" in matched, "report_template=both 未命中 fundamental_report"

    def test_run_both_generates_two_reports(self):
        """run() report_template=both 联合生成技术面 + 基本面两份报告并汇总。"""
        mod = _load_engine()
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)

        ctx = self._build_ctx()
        result = mod.run(ctx)
        assert result.get("success") is True, result.get("error")

        meta = result.get("metadata", {})
        artifacts = meta.get("all_artifacts", [])
        artifact_str = str(artifacts)
        assert any("technical" in str(p) for p in artifacts), (
            f"缺少技术面报告: {artifacts}"
        )
        assert any("fundamental" in str(p) for p in artifacts), (
            f"缺少基本面报告: {artifacts}"
        )
        # report_data.json 汇总
        rdp = meta.get("report_data_path")
        assert rdp and os.path.exists(rdp)
        import json as _json
        with open(rdp, encoding="utf-8") as f:
            rpt = _json.load(f)
        assert rpt.get("report_template") == "both"
        assert "artifacts" in rpt


@pytest.mark.integration
class TestFallbackPlugin:
    """兜底插件（无任何插件命中时生成默认个股报告）。"""

    def test_fallback_plugin_declared(self):
        """存在 fallback=true 且无 trigger 的兜底插件，且 find_fallback 可找到。"""
        _load_engine()  # 先加载 engine，设置 scripts 包，保证 render.py 可导入
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)
        registry = plugins["registry"]

        fb = registry.find_fallback()
        assert fb is not None, "find_fallback 应返回兜底插件"
        assert fb.id == "fallback_report"
        assert fb.fallback is True
        assert not fb.triggers

    def test_fallback_runs_when_no_plugin_matches(self):
        """无显式意图/产物时，兜底插件生成默认报告（技术面 + 基本面）。"""
        mod = _load_engine()
        plugins = _load_plugins()
        loader = plugins["loader"]
        loader.scan(PLUGINS_DIR)

        Context = _get_context_class()
        data_path = _make_data_artifact()
        ctx = Context(
            task_id="test_fallback_run",
            user_intent="分析 002594.SZ",
            current_stage="REPORT",
            stock_pool=["002594.SZ"],
            benchmark="000300.SH",
        )
        # 仅提供 DATA（无 report_intent / report_template / BACKTEST），
        # 应落入兜底插件生成默认个股报告。
        ctx.update_artifact("DATA", data_path)
        os.environ["QUANT_WORK_DIR"] = "./workspace"

        result = mod.run(ctx)
        assert result.get("success") is True, result.get("error")
        meta = result.get("metadata", {})
        assert "report_data_path" in meta, "兜底报告应产出 report_data.json"
