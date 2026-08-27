"""断路器 v2 —— 跨 skill 共享入口（薄壳，权威实现在 portfolio-risk-engine）。

背景
----
本文件原与 `portfolio-risk-engine/scripts/optimizations/circuit_breaker_v2.py`
逐字节相同（md5 `17cf54ac2a792a889ec7d665d49bb8ba`），属 DRY 违例。

现改为薄壳：本模块只负责加载并转出 `CircuitBreakerV2`，实现唯一落在
portfolio-risk-engine 侧。

方向选择依据
------------
共享层（core/ir/_shared/skills/jingni-trader/）按红线剔除
execution-monitor-engine 的运行代码（仅保留 references/）。若反过来让
portfolio-risk-engine 引用本文件，共享层内的 portfolio engine.py 会因
execution 代码缺失而 ImportError。故权威实现必须落在 portfolio 侧——该侧
在共享层中完整镜像，本侧薄壳即使被红线剔除也不影响 portfolio 运行。

原模块的设计说明（滞回 / fail-open / 最小样本量 / JSON 持久化 / 多日滚动 PnL）
现由 `portfolio-risk-engine/scripts/optimizations/circuit_breaker_v2.py` 承载，
本文件不再重复。

加载方式
--------
沿用本仓库既有跨 skill 复用先例（strategy-model-engine 的 `_load_factor_ic`
加载 factor-engine 的 ic_vectorized）：以独立包名
`pre_optimizations_circuit_breaker` 加载，保证模块内相对导入可解析。

加载失败时**抛出 ImportError 而非静默降级**——风控组件缺失必须显式暴露，
避免「以为有风控实际没有」的 fail-open 误用。

对外接口保持不变：`from ...quant_circuit_breaker import CircuitBreakerV2`。
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

__all__ = ["CircuitBreakerV2"]


def _load_circuit_breaker_v2():
    """加载 portfolio-risk-engine 的权威 CircuitBreakerV2 实现。"""
    # 本文件位于 <root>/skills/execution-monitor-engine/scripts/optimizations/
    # → 上溯四级得到 skill 根目录（root）：
    #   optimizations → scripts → execution-monitor-engine → skills → root
    #
    # 注意：测试以 spec_from_file_location 加载时 __file__ 可能是相对路径
    # （如 "skills\\execution-monitor-engine\\scripts\\optimizations\\quant_circuit_breaker.py"），
    # 直接 dirname 链会错位。先用 __file__ 自身做锚点稳健解析：
    #   统一先取 abspath，再逐级剥离，并校验末尾落在 skills/<name>/scripts 结构。
    _here = _os.path.abspath(__file__)
    _opt_dir_self = _os.path.dirname(_here)                 # .../scripts/optimizations
    _scripts_dir = _os.path.dirname(_opt_dir_self)          # .../scripts
    _engine_dir = _os.path.dirname(_scripts_dir)            # .../execution-monitor-engine
    _skill_root = _os.path.dirname(_engine_dir)             # <root>/skills
    _root = _os.path.dirname(_skill_root)                   # <root>
    _opt_dir = _os.path.join(
        _root, "skills", "portfolio-risk-engine", "scripts", "optimizations"
    )
    _init_path = _os.path.join(_opt_dir, "__init__.py")
    _cb_path = _os.path.join(_opt_dir, "circuit_breaker_v2.py")

    if not _os.path.exists(_cb_path):
        raise ImportError(
            "portfolio-risk-engine 的 circuit_breaker_v2.py 不存在: %s。"
            "断路器为风控关键组件，不做静默降级——请确认 skill 目录完整。" % _cb_path
        )

    _pkg_name = "pre_optimizations_circuit_breaker"
    if _pkg_name not in _sys.modules:
        _init_spec = _ilu.spec_from_file_location(
            _pkg_name, _init_path, submodule_search_locations=[_opt_dir]
        )
        _pkg = _ilu.module_from_spec(_init_spec)
        _sys.modules[_pkg_name] = _pkg
        _init_spec.loader.exec_module(_pkg)

    _mod_name = _pkg_name + ".circuit_breaker_v2"
    if _mod_name not in _sys.modules:
        _spec = _ilu.spec_from_file_location(_mod_name, _cb_path)
        _mod = _ilu.module_from_spec(_spec)
        _sys.modules[_mod_name] = _mod
        _spec.loader.exec_module(_mod)

    return getattr(_sys.modules[_mod_name], "CircuitBreakerV2")


CircuitBreakerV2 = _load_circuit_breaker_v2()
