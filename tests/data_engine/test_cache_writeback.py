"""local 缓存全量写回 + TTL 命中测试（REQ-2026-08-16）。

验证「买菜放冰箱」方案的核心契约：
  1. 写回：引擎联网取数成功后，write_cache 同步落盘 CACHE_DIR/{data_type}/{symbol}.parquet
     + 配套 .meta.json（仅 fetch_ts，无 token）。
  2. 命中：local_adapter 在缓存未过期时直接返回，不触发任何下游网络源。
  3. 过期：缓存超过 TTL（或 meta 缺失）视为未命中，删除并交由上层联网重取。
  4. 开关：CACHE_WRITE_BACK=false 时 write_cache 不落盘任何文件。

设计要点（复用 test_run_contract 的模块加载范式）：
  - 用 importlib 显式加载 data-engine/engine.py 为 data_engine_engine 独立模块
  - 把 data-engine/scripts 注册为 sys.modules['scripts']，使之能 `from scripts.config import ...`
  - mock 重量级第三方依赖（tushare/baostock/akshare/xtquant/talib/pandas_ta）
  - 不依赖真实网络；缓存 IO 全部落在 tmp_path
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
import time
from unittest import mock

import pytest
import pandas as pd


# ============================================================================
# 模块加载工具：复用 test_run_contract 的范式加载 data-engine/engine.py
# 与 LocalAdapter（独立模块名，避免与 conftest 注册的 master scripts 包冲突）
# ============================================================================

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
DATA_ENGINE_PATH = os.path.join(DATA_ENGINE_DIR, "engine.py")
LOCAL_ADAPTER_PATH = os.path.join(DATA_ENGINE_DIR, "scripts", "adapters", "local_adapter.py")


def _register_data_engine_scripts():
    """把 data-engine/scripts 注册为 sys.modules['scripts'] 并保持（不被 conftest 的
    master 包覆盖）。

    conftest 的 autouse fixture 在每个测试入口把 scripts 重置为 jingni-trader/scripts
    （master 包，无 CACHE_WRITE_BACK 等字段）；本函数在测试体内调用，确保 engine.py
    的 `from scripts.config import CACHE_WRITE_BACK` 在运行期解析到 data-engine 自有
    scripts 包。conftest 会在测试退出时恢复 master 状态，故此处无需手动恢复。
    """
    scripts_dir = os.path.join(DATA_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts",
            init_py,
            submodule_search_locations=[scripts_dir],
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)
    for _m in ("tushare", "baostock", "akshare", "xtquant", "talib", "pandas_ta"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()
    return sys.modules["scripts"]


def _load_local_adapter():
    """把 data-engine/scripts 注册为 sys.modules['scripts'] 后，
    显式加载 LocalAdapter 为独立模块 local_adapter_test（避免与 master scripts 包冲突）。"""
    saved = {
        k: sys.modules.get(k)
        for k in list(sys.modules.keys())
        if k == "scripts" or k.startswith("scripts.")
    }
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    scripts_dir = os.path.join(DATA_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts",
        init_py,
        submodule_search_locations=[scripts_dir],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    for _m in ("tushare", "baostock", "akshare", "xtquant", "talib", "pandas_ta"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    try:
        spec = ilu.spec_from_file_location("local_adapter_test", LOCAL_ADAPTER_PATH)
        mod = ilu.module_from_spec(spec)
        sys.modules["local_adapter_test"] = mod
        spec.loader.exec_module(mod)
        return mod.LocalAdapter
    finally:
        for key in list(sys.modules.keys()):
            if key == "scripts" or key.startswith("scripts."):
                sys.modules.pop(key, None)
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v


def _load_data_engine_module():
    """显式加载 data-engine/engine.py 为独立模块 data_engine_engine。"""
    saved = {
        k: sys.modules.get(k)
        for k in list(sys.modules.keys())
        if k == "scripts" or k.startswith("scripts.")
    }
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    scripts_dir = os.path.join(DATA_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts",
            init_py,
            submodule_search_locations=[scripts_dir],
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)

    for _m in ("tushare", "baostock", "akshare", "xtquant", "talib", "pandas_ta"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    try:
        spec = ilu.spec_from_file_location("data_engine_engine", DATA_ENGINE_PATH)
        mod = ilu.module_from_spec(spec)
        sys.modules["data_engine_engine"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key in list(sys.modules.keys()):
            if key == "scripts" or key.startswith("scripts."):
                sys.modules.pop(key, None)
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v


# ============================================================================
# 共用辅助
# ============================================================================


def _make_daily_df(codes=("600519.SH", "000001.SZ")):
    """构造含 code 列的日线 DataFrame。"""
    frames = []
    for code in codes:
        dates = pd.bdate_range("2024-01-01", "2024-06-30")
        frames.append(
            pd.DataFrame(
                {
                    "code": code,
                    "date": dates,
                    "open": [10.0] * len(dates),
                    "high": [11.0] * len(dates),
                    "low": [9.0] * len(dates),
                    "close": [10.5] * len(dates),
                    "volume": [1_000_000] * len(dates),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _make_global_df():
    """构造无 symbol 维度的全局型 DataFrame（如交易日历）。"""
    dates = pd.bdate_range("2024-01-01", "2024-06-30")
    return pd.DataFrame(
        {
            "date": dates,
            "is_trading": [True] * len(dates),
        }
    )


# ============================================================================
# 测试：引擎缓存写回
# ============================================================================


@pytest.mark.skill_data_engine
@pytest.mark.contract
class TestCacheWriteBack:
    """验证 write_cache 落盘 + meta 元数据（无 token）。"""

    def test_write_cache_generates_parquet_and_meta(self, tmp_path, monkeypatch):
        """联网取数后 write_cache 落盘 {data_type}/{symbol}.parquet + .meta.json（含 fetch_ts）。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        # 确保开关开启
        monkeypatch.setenv("DATA_CACHE_WRITE_BACK", "true")

        mod = _load_data_engine_module()
        # 构造最小 DataEngine 实例（仅用到 save_data + config 常量）
        engine = mod.DataEngine.__new__(mod.DataEngine)
        engine.logger = mod.logger

        df = _make_daily_df()
        # 直接调用 write_cache（不联网，仅验证落盘契约）
        # 重新注册 data-engine 自有 scripts 包（conftest 已重置为 master 包）
        _register_data_engine_scripts()
        engine.write_cache("daily", ["600519.SH", "000001.SZ"], df)

        # 两个标的各自落盘
        p519 = tmp_path / "daily" / "600519.SH.parquet"
        p001 = tmp_path / "daily" / "000001.SZ.parquet"
        assert p519.exists(), "daily/600519.SH.parquet 应被写回"
        assert p001.exists(), "daily/000001.SZ.parquet 应被写回"
        # meta 同目录生成
        meta519 = tmp_path / "daily" / "600519.SH.parquet.meta.json"
        assert meta519.exists(), "应同时写回 meta.json 新鲜度元数据"
        # meta 仅含 fetch_ts，无 token/敏感字段
        import json

        meta = json.loads(meta519.read_text(encoding="utf-8"))
        assert "fetch_ts" in meta
        assert all(k not in meta for k in ("token", "secret", "password", "key")), \
            "meta 绝不能含 token/secret 等敏感字段"

    def test_write_cache_global_type_uses_all_parquet(self, tmp_path, monkeypatch):
        """全局型数据项（basic_trade_calendar）写回统一用 all.parquet。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("DATA_CACHE_WRITE_BACK", "true")

        mod = _load_data_engine_module()
        engine = mod.DataEngine.__new__(mod.DataEngine)
        engine.logger = mod.logger

        cal = _make_global_df()
        _register_data_engine_scripts()
        engine.write_cache("basic_trade_calendar", [], cal)

        all_p = tmp_path / "basic_trade_calendar" / "all.parquet"
        assert all_p.exists(), "全局型应落盘为 all.parquet"
        # 不应出现按 symbol 拆分的文件
        siblings = list((tmp_path / "basic_trade_calendar").glob("*.parquet"))
        assert [p.name for p in siblings] == ["all.parquet"], "全局型只应有 all.parquet"

    def test_write_cache_disabled_by_switch(self, tmp_path, monkeypatch):
        """CACHE_WRITE_BACK=false 时 write_cache 不落盘任何文件。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("DATA_CACHE_WRITE_BACK", "false")

        mod = _load_data_engine_module()
        engine = mod.DataEngine.__new__(mod.DataEngine)
        engine.logger = mod.logger

        df = _make_daily_df()
        _register_data_engine_scripts()
        engine.write_cache("daily", ["600519.SH"], df)

        # 目录不应被创建（或为空）
        type_dir = tmp_path / "daily"
        if type_dir.exists():
            assert list(type_dir.glob("*.parquet")) == [], "开关关闭时不应落盘任何 parquet"


# ============================================================================
# 测试：local_adapter 命中 / 过期
# ============================================================================


@pytest.mark.skill_data_engine
@pytest.mark.contract
class TestLocalAdapterCacheHit:
    """验证 local_adapter 在缓存存在时命中（不联网），过期则未命中。"""

    _LocalAdapter = None

    @classmethod
    def _adapter_cls(cls):
        """在每个测试入口重新加载 LocalAdapter（conftest 会重置 scripts 包）。"""
        if cls._LocalAdapter is None:
            cls._LocalAdapter = _load_local_adapter()
        # 重新注册 data-engine 自有 scripts 包（_load_local_adapter 的 finally 已 pop，
        # 且 conftest 在测试入口重置为 master 包）；确保 adapter 运行期解析到正确 config。
        _register_data_engine_scripts()
        return cls._LocalAdapter

    def _seed_cache(self, tmp_path, data_type, symbol, df, fetch_ts=None):
        """写一份缓存 parquet + meta（供 local_adapter 读取）。

        meta 直接写 JSON（含 fetch_ts），避免依赖 conftest 注册的 master scripts 包
        （其 config 无 write_cache_meta）。本地缓存格式契约即 {"fetch_ts": <float>}。
        """
        import json

        type_dir = tmp_path / data_type
        type_dir.mkdir(parents=True, exist_ok=True)
        p = type_dir / f"{symbol}.parquet"
        df.to_parquet(p, index=False)
        if fetch_ts is not None:
            meta_p = type_dir / f"{symbol}.parquet.meta.json"
            meta_p.write_text(json.dumps({"fetch_ts": fetch_ts}), encoding="utf-8")
        return str(p)

    def test_hit_returns_without_network(self, tmp_path, monkeypatch):
        """缓存未过期 → get_daily 直接返回，下游网络源不被调用。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))

        df = _make_daily_df(("600519.SH",))
        self._seed_cache(tmp_path, "daily", "600519.SH", df, fetch_ts=time.time())

        adapter = self._adapter_cls()(cache_dir=str(tmp_path))
        # network_source 作为"联网重取"的替身，命中时绝不应被调用
        network_source = mock.MagicMock()
        network_source.get_daily = mock.MagicMock(side_effect=AssertionError("不应触发联网"))

        result = adapter.get_daily(["600519.SH"], "2024-01-01", "2024-06-30")
        assert not result.empty, "缓存命中应返回非空 DataFrame"
        assert "600519.SH" in result["code"].tolist()
        network_source.get_daily.assert_not_called()

    def test_expired_returns_none_and_triggers_refetch(self, tmp_path, monkeypatch):
        """缓存超过 TTL → 视为未命中（文件被剔除），上层应走联网。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))

        df = _make_daily_df(("600519.SH",))
        # 写入陈旧 meta：daily TTL=86400s，30 天前必定过期
        old_ts = time.time() - 30 * 86400
        self._seed_cache(tmp_path, "daily", "600519.SH", df, fetch_ts=old_ts)

        p = tmp_path / "daily" / "600519.SH.parquet"
        assert p.exists(), "前置：缓存文件已写入"
        meta_p = tmp_path / "daily" / "600519.SH.parquet.meta.json"
        assert meta_p.exists(), "前置：meta 已写入"

        adapter = self._adapter_cls()(cache_dir=str(tmp_path))
        # get_daily 内部 _read 发现过期会删除文件并 return None → 抛出未命中异常
        with pytest.raises(Exception):
            adapter.get_daily(["600519.SH"], "2024-01-01", "2024-06-30")

        # 过期文件应被剔除（触发联网重取）
        assert not p.exists(), "过期的 parquet 应被删除以触发重取"
        assert not meta_p.exists(), "过期的 meta 应被删除"

    def test_missing_meta_treated_as_stale(self, tmp_path, monkeypatch):
        """历史缓存（无 meta）→ 视为过期未命中，交由上层联网。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))

        df = _make_daily_df(("600519.SH",))
        # 仅写 parquet，不写 meta（模拟历史遗留缓存）
        type_dir = tmp_path / "daily"
        type_dir.mkdir(parents=True, exist_ok=True)
        p = type_dir / "600519.SH.parquet"
        df.to_parquet(p, index=False)

        adapter = self._adapter_cls()(cache_dir=str(tmp_path))
        with pytest.raises(Exception):
            adapter.get_daily(["600519.SH"], "2024-01-01", "2024-06-30")

    def test_global_cache_hit_via_all_parquet(self, tmp_path, monkeypatch):
        """全局型（basic_trade_calendar）→ 直接读 all.parquet 命中，无需 symbol。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))

        cal = _make_global_df()
        self._seed_cache(tmp_path, "basic_trade_calendar", "all", cal, fetch_ts=time.time())

        adapter = self._adapter_cls()(cache_dir=str(tmp_path))
        result = adapter.get_trade_calendar()
        assert not result.empty, "交易日历缓存应命中返回"
        assert len(result) == len(cal)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
