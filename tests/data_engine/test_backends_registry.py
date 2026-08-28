"""data-engine backend 注册表与适配器导入/实例化测试（REQ-2026-08-14 Damon 最终拍板）。

覆盖 11 个 backend（westock + local + 9 旧源）的：
- _ADAPTER_REGISTRY 注册完整性（11 个）
- 适配器模块可导入、可实例化（实现全部 4 个抽象方法）
- neodata 不在注册表 / SUPPORTED_BACKENDS（决策：不接入 jingni-trader）
- 默认免费链 5 源：local → westock → baostock → akshare → websearch（local 优先级高于 westock）
- 按需调用组 6 源：tushare/gm/xtquant/tdxquant/wind/ifind（注册可用，不进默认链）

设计：
- 不要求联网实测；适配器仅验证可导入/实例化（接口契约 + 4 抽象方法实现）。
- wind/ifind SDK 缺失时适配器实例化应友好报错（DataSourceError），不崩溃——以 xfail/容错处理。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
from contextlib import contextmanager

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")


# 全部 11 个真实后端（与 engine.py _ADAPTER_REGISTRY 对应）
ALL_BACKENDS = [
    "westock",
    "local",
    "tushare",
    "baostock",
    "akshare",
    "websearch",
    "xtquant",
    "gm",
    "tdxquant",
    "wind",
    "ifind",
]

# 默认免费链 5 源（按优先级）
DEFAULT_FREE_CHAIN = ["local", "westock", "baostock", "akshare", "websearch"]

# 按需调用组（注册可用，不进默认链）
ON_DEMAND_BACKENDS = ["tushare", "gm", "xtquant", "tdxquant", "wind", "ifind"]

# 主调度器 engine 模块的导入期快照。
# 顶层 "engine" 槽位属于主调度器（engine.py / MasterEngine），任何测试都不得删除它：
# 删除会强制后续测试重新 import 主调度器，触发 cvxpy 原生扩展重复加载的
# access violation 段错误（OPEN-2026-0814-13）。此处仅在被误挤占时用于恢复。
_saved_master_engine = sys.modules.get("engine")


def _is_master_engine(mod) -> bool:
    """判断给定模块是否为主调度器 engine（而非本文件加载的 data-engine engine）。"""
    origin = getattr(mod, "__file__", None) or ""
    return origin.endswith(os.path.join("jingni-trader", "engine.py")) or origin.endswith("engine.py")


# 适配器上「会在构造期发起真实外部连接」的方法名。
# 测试实例化这些适配器时必须屏蔽，否则环境一旦装了对应 SDK 且网络不可达，
# 调用会长时间阻塞（pytest-timeout 的 thread 模式中断不了原生阻塞），
# 导致整个测试套件超时卡死。
_EXTERNAL_CONNECTION_METHODS = (
    "_login",       # ifind_adapter：THS_iFinDLogin → 同花顺服务器
    "_connect",     # wind_adapter：WindPy w.start() → Wind 终端
    "_init_client",
    "login",
    "connect",
)


@contextmanager
def _patch_external_connections(adapter_cls):
    """屏蔽适配器类上所有会发起真实外部连接的方法。

    仅 patch 该适配器类自身及其基类上真实存在的同名方法；不存在的方法跳过，
    避免 MagicMock 凭空造出属性而掩盖真实缺陷（如方法被误改名/删除）。
    """
    patched = []
    for name in _EXTERNAL_CONNECTION_METHODS:
        # 在类自身或 MRO 基类上查找真实定义，找不到就不 patch
        target = None
        for klass in getattr(adapter_cls, "__mro__", (adapter_cls,)):
            if name in vars(klass):
                target = name
                break
        if target is None:
            continue
        patched.append((adapter_cls, target, getattr(adapter_cls, target)))
        setattr(adapter_cls, target, lambda self, *a, _n=target, **kw: None)
    try:
        yield
    finally:
        for klass, name, original in patched:
            setattr(klass, name, original)


def _reset_scripts():
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)
    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)


def _load_engine_module():
    # 注意：加载 data-engine 的 engine.py 为独立模块名 data_engine_engine，
    # 避免污染 sys.modules["engine"]（该槽位属于顶层主调度器 engine.py / MasterEngine），
    # 否则与 tests/master/test_parse_intent.py 的 `import engine` 冲突导致跨文件隔离失败。
    _reset_scripts()
    engine_py = os.path.join(DATA_ENGINE_DIR, "engine.py")
    spec = ilu.spec_from_file_location("data_engine_engine", engine_py)
    mod = ilu.module_from_spec(spec)
    sys.modules["data_engine_engine"] = mod
    spec.loader.exec_module(mod)
    # ⚠️ 不要 pop 顶层 "engine" 槽位。
    # 原实现在此执行 sys.modules.pop("engine", None)，意图是"清掉可能误入的顶层 engine 槽位"。
    # 但顶层 engine 槽位属于主调度器（MasterEngine），pop 会强制后续测试重新 import 主调度器，
    # 而主调度器导入链含 cvxpy 等原生扩展，在 Windows 下重复导入会触发 access violation
    # 段错误（OPEN-2026-0814-13），表现为全量跑崩溃、单跑该文件却全绿。
    #
    # 本函数加载的是独立模块名 "data_engine_engine"，本就不会占据 "engine" 槽位，
    # 故该 pop 不仅无必要，而且是全量崩溃的直接诱因。改为：若加载过程中确实误入了
    # 顶层 engine 槽位，则恢复为加载前的原值（而非删除），保证不破坏其他测试的模块状态。
    _loaded_engine = sys.modules.get("engine")
    if _loaded_engine is not None and _is_master_engine(_loaded_engine):
        # 加载 data_engine_engine 时误把主调度器挤掉了 —— 恢复它
        if _saved_master_engine is not None:
            sys.modules["engine"] = _saved_master_engine
    return mod


def _load_config_module():
    _reset_scripts()
    config_py = os.path.join(SCRIPTS_DIR, "config.py")
    spec = ilu.spec_from_file_location("scripts.config", config_py)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.config"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestBackendRegistry:
    """验证 _ADAPTER_REGISTRY 与 SUPPORTED_BACKENDS 完整性。"""

    def test_registry_contains_all_backends(self):
        mod = _load_engine_module()
        missing = [b for b in ALL_BACKENDS if b not in mod._ADAPTER_REGISTRY]
        assert not missing, f"注册表缺失 backend: {missing}"

    def test_registry_count_is_eleven(self):
        mod = _load_engine_module()
        assert len(mod._ADAPTER_REGISTRY) == 11, "应注册 11 个 backend"

    def test_supported_backends_matches_registry(self):
        mod = _load_engine_module()
        assert set(mod.SUPPORTED_BACKENDS) == set(mod._ADAPTER_REGISTRY.keys()), (
            "SUPPORTED_BACKENDS 应与 _ADAPTER_REGISTRY 键一致"
        )

    def test_neodata_not_registered(self):
        mod = _load_engine_module()
        assert "neodata" not in mod._ADAPTER_REGISTRY, "neodata 不得接入 jingni-trader"
        assert "neodata" not in mod.SUPPORTED_BACKENDS, "neodata 不得出现在 SUPPORTED_BACKENDS"

    def test_default_chain_local_first(self):
        """默认免费链：local 置顶（优先级高于 tencent）。"""
        mod = _load_engine_module()
        chain = list(mod.DEFAULT_DATA_SOURCES)
        assert chain[0] == "local", "local 应位于默认链首位（Damon 确认 local 优先于 tencent）"
        assert chain.index("local") < chain.index("westock"), "local 应优先于 westock"

    def test_default_chain_is_free_five(self):
        """默认链恰好为免费链 5 源。"""
        mod = _load_engine_module()
        assert list(mod.DEFAULT_DATA_SOURCES) == DEFAULT_FREE_CHAIN, f"默认链应为 {DEFAULT_FREE_CHAIN}"

    def test_default_chain_excludes_on_demand_sources(self):
        """默认链不含任何按需调用源（tushare/gm/xtquant/tdxquant/wind/ifind）。"""
        mod = _load_engine_module()
        for b in ON_DEMAND_BACKENDS:
            assert b not in mod.DEFAULT_DATA_SOURCES, f"{b} 不应在默认链（属按需调用组）"

    def test_paid_or_special_contains_on_demand(self):
        cfg = _load_config_module()
        assert set(cfg.PAID_OR_SPECIAL_BACKENDS) == set(ON_DEMAND_BACKENDS), (
            "PAID_OR_SPECIAL_BACKENDS 应为按需调用 6 源（tushare 已从免费链移除）"
        )

    def test_tushare_not_in_default_chain(self):
        """tushare 已从免费链移除，归入按需调用组。"""
        mod = _load_engine_module()
        assert "tushare" not in mod.DEFAULT_DATA_SOURCES, "tushare 不应在默认链"
        assert "tushare" in mod.SUPPORTED_BACKENDS, "tushare 仍应被支持（按需调用）"


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestAdapterImportInstantiate:
    """验证各适配器可导入并实例化（实现全部抽象方法）。"""

    @pytest.mark.parametrize("backend", ALL_BACKENDS)
    def test_adapter_importable(self, backend):
        _reset_scripts()
        module_path, class_name, _ = _load_engine_module()._ADAPTER_REGISTRY[backend]
        p = os.path.join(SCRIPTS_DIR, "adapters", f"{backend}_adapter.py")
        spec = ilu.spec_from_file_location(f"scripts.adapters.{backend}_adapter", p)
        mod = ilu.module_from_spec(spec)
        sys.modules[f"scripts.adapters.{backend}_adapter"] = mod
        spec.loader.exec_module(mod)
        cls = getattr(mod, class_name)
        assert cls is not None

    @pytest.mark.parametrize("backend", ALL_BACKENDS)
    def test_adapter_instantiable(self, backend):
        _reset_scripts()
        module_path, class_name, default_kwargs = _load_engine_module()._ADAPTER_REGISTRY[backend]
        p = os.path.join(SCRIPTS_DIR, "adapters", f"{backend}_adapter.py")
        spec = ilu.spec_from_file_location(f"scripts.adapters.{backend}_adapter", p)
        mod = ilu.module_from_spec(spec)
        sys.modules[f"scripts.adapters.{backend}_adapter"] = mod
        spec.loader.exec_module(mod)
        cls = getattr(mod, class_name)
        # 绝大部分适配器仅用标准库/已装包，可直接实例化验证 4 抽象方法；
        # wind/ifind 等 SDK 缺失时实例化会友好抛 DataSourceError —— 视为"已注册可用、缺前置条件"，
        # 用容错断言：要么成功实例化，要么抛 DataSourceError（不得抛 ImportError/TypeError/AttributeError）。
        #
        # ⚠️ 外部连接隔离（本用例此前导致全量套件 600s 超时卡死）：
        # 部分适配器的 __init__ 会直接发起真实外部连接——
        #   ifind_adapter.__init__ → self._login()  → THS_iFinDLogin（同花顺服务器）
        #   wind_adapter.__init__  → self._connect() → WindPy w.start()（Wind 终端）
        # 若环境里恰好装了对应 SDK 且网络不可达，该调用会长时间阻塞，且
        # pytest-timeout 的 thread 模式无法中断原生阻塞，最终拖垮整个套件。
        #
        # 处置：用 mock 屏蔽这些「连接类」方法，只校验实例化逻辑本身（4 个抽象方法实现），
        # 不校验真实登录——后者属集成层职责（由 requires_* 标记的用例承担）。
        # 这不是 skip/xfail：用例仍完整执行，断言仍然生效。
        try:
            with _patch_external_connections(cls):
                cls(**(default_kwargs or {}))
        except Exception as e:  # noqa: BLE001
            assert "DataSourceError" in type(e).__name__, (
                f"{backend} 实例化异常类型应为 DataSourceError（缺前置条件），实际 {type(e).__name__}: {e}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
