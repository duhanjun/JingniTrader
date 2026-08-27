"""factor_engine 目录级 conftest。

职责：隔离顶层 `config` 模块污染，保证 `jingni_client` 的
`from config import JingniConfig` 始终解析到 jingni-datafeed 的 scripts/config.py。

背景（OPEN-2026-0816-01 根因）：
factor-engine/engine.py 在 `_try_load_factor_from_datafeed` 中**延迟导入**
`jingni_client` 与 `config`；而 `jingni_client.py` 顶层 `from config import JingniConfig`。
同目录其他测试（如 test_alphalens_adapter._load_adapter）加载位于
`skills/factor-engine/scripts/` 下的模块时，会把顶层 `config` 缓存为
factor-engine 自己的 scripts/config.py（不含 JingniConfig），且清理时只 pop
`scripts.*` 而遗漏顶层 `config`。其后执行的 datafeed 测试延迟导入
`jingni_client` 即命中错误的 `config` → ImportError，表现为目录级运行 5 失败、
单文件运行全绿（顺序依赖型污染）。

修复策略（M6 最小侵入、作用域收敛到本目录）：autouse fixture 在每个测试前
确保 `sys.modules['config']` 指向 jingni-datafeed 的 config.py；测试结束恢复。
这样 datafeed 测试不再依赖前序测试的 sys.modules 状态，独立运行与全目录运行均绿。
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATAFEED_SCRIPTS = os.path.join(ROOT, "skills", "jingni-datafeed", "scripts")
DATAFEED_CONFIG = os.path.join(DATAFEED_SCRIPTS, "config.py")


@pytest.fixture(autouse=True)
def _isolate_datafeed_config():
    """每个测试前后确保顶层 `config` 解析到 jingni-datafeed 的 config.py。

    防止兄弟测试（alphalens_adapter 等）加载 factor-engine scripts 时把
    顶层 `config` 缓存为 factor-engine 版本（无 JingniConfig）而污染本目录测试。
    """
    saved = sys.modules.get("config")
    try:
        if DATAFEED_SCRIPTS not in sys.path:
            sys.path.insert(0, DATAFEED_SCRIPTS)
        # 强制把 config 绑定到 jingni-datafeed 的 config.py（含 JingniConfig）
        spec = importlib.util.spec_from_file_location("config", DATAFEED_CONFIG)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["config"] = mod
        spec.loader.exec_module(mod)
        yield
    finally:
        # 恢复测试前状态，避免过度隔离影响其他目录
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)
