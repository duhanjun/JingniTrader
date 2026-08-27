"""local 缓存增量更新测试（REQ-2026-08-16 优化：增量优先 + 修正感知）。

验证：
  1. 增量合并：旧缓存 + 新区间数据按主键去重合并正确，截止日计算正确
  2. 修正感知：复权口径变化 / 重叠窗口数值跳变 → 回退全量（plan 返回 None）
  3. 快照类：维持整取（plan 返回 None，不走增量）
  4. 开关 off：CACHE_INCREMENTAL=false 时 _maybe_incremental 不生效（走整取）
  5. engine 层 _maybe_incremental：有旧缓存只拉缺失区间并合并；无旧缓存走整取

复用 test_run_contract 的模块加载范式：importlib 显式加载 engine.py 为 data_engine_engine，
scripts 包指向 data-engine/scripts；mock 重量级第三方依赖。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
import json
import time
from unittest import mock

import pytest
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
DATA_ENGINE_PATH = os.path.join(DATA_ENGINE_DIR, "engine.py")


def _load_data_engine_module():
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
            "scripts", init_py, submodule_search_locations=[scripts_dir]
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


def _register_data_engine_scripts():
    """把 data-engine/scripts 注册为 sys.modules['scripts'] 并保持（绕过 conftest 的 master 包）。"""
    scripts_dir = os.path.join(DATA_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts", init_py, submodule_search_locations=[scripts_dir]
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)
    for _m in ("tushare", "baostock", "akshare", "xtquant", "talib", "pandas_ta"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()
    return sys.modules["scripts"]


def _seed_cache(tmp_path, data_type, symbol, df, meta=None):
    """写缓存 parquet + meta.json（供 _read_cached_series / plan_incremental 读取）。"""
    type_dir = tmp_path / data_type
    type_dir.mkdir(parents=True, exist_ok=True)
    p = type_dir / f"{symbol}.parquet"
    df.to_parquet(p, index=False)
    if meta is not None:
        (type_dir / f"{symbol}.parquet.meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
    return str(p)


def _daily_df(codes, start, end):
    frames = []
    for code in codes:
        dates = pd.bdate_range(start, end)
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


@pytest.mark.skill_data_engine
@pytest.mark.contract
class TestCacheIncrementalPure:
    """验证 cache_incremental 纯函数。"""

    @pytest.fixture(autouse=True)
    def _setup_scripts(self):
        # conftest 会把 scripts 重置为 master 包；本类纯函数需 data-engine/scripts 包
        _register_data_engine_scripts()
        yield

    def test_plan_incremental_series_returns_next_day(self):
        from scripts.cache_incremental import plan_incremental

        meta = {"adjust": "qfq", "cached_end": "2024-06-30"}
        plan = plan_incremental("daily", meta, "2024-01-01", "2024-07-31", "qfq", True)
        assert plan == "2024-07-01", "增量起始日应为缓存截止日 +1"

    def test_plan_incremental_adjust_mismatch_rolls_back(self):
        from scripts.cache_incremental import plan_incremental

        meta = {"adjust": "qfq", "cached_end": "2024-06-30"}
        # 请求 hfq，但缓存是 qfq → 口径不一致 → 全量
        assert plan_incremental("daily", meta, "2024-01-01", "2024-07-31", "hfq", True) is None

    def test_plan_incremental_snapshot_returns_none(self):
        from scripts.cache_incremental import plan_incremental

        meta = {"adjust": "qfq", "cached_end": "2024-06-30"}
        # 快照类始终整取
        assert plan_incremental("market_realtime", meta, "2024-01-01", "2024-07-31", "qfq", True) is None
        assert plan_incremental("capital_flow", meta, "2024-01-01", "2024-07-31", "qfq", True) is None

    def test_plan_incremental_switch_off(self):
        from scripts.cache_incremental import plan_incremental

        meta = {"adjust": "qfq", "cached_end": "2024-06-30"}
        assert plan_incremental("daily", meta, "2024-01-01", "2024-07-31", "qfq", False) is None

    def test_plan_incremental_already_covered(self):
        from scripts.cache_incremental import plan_incremental

        # 缓存已覆盖请求区间 → 无需补充
        meta = {"adjust": "qfq", "cached_end": "2024-12-31"}
        assert plan_incremental("daily", meta, "2024-01-01", "2024-07-31", "qfq", True) is None

    def test_merge_incremental_dedup_and_concat(self):
        from scripts.cache_incremental import merge_incremental

        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-30")
        new = _daily_df(["600519.SH"], "2024-07-01", "2024-07-31")
        merged = merge_incremental(old, new, "daily")
        # 旧 130 + 新 23 = 153（bdate_range 计数差异由数据自身决定，这里只验证无重复主键）
        assert not merged.duplicated(subset=["code", "date"]).any(), "合并后不应有重复主键"
        assert len(merged) == len(old) + len(new), "合并行数应为新旧之和（无重叠）"

    def test_merge_incremental_overlap_new_wins(self):
        from scripts.cache_incremental import merge_incremental

        # 注意 bdate_range 排除周末：2024-06 最后交易日为 06-28，用其做重叠键
        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-28")
        # 新数据含重叠交易日（2024-06-28 收盘修正为 11.0），应覆盖旧值
        new = _daily_df(["600519.SH"], "2024-06-28", "2024-07-31")
        new.loc[new["date"] == pd.Timestamp("2024-06-28"), "close"] = 11.0
        merged = merge_incremental(old, new, "daily")
        jun28 = merged[(merged["code"] == "600519.SH") & (merged["date"] == pd.Timestamp("2024-06-28"))]
        assert len(jun28) == 1, "重叠主键应去重为 1 行"
        assert jun28["close"].iloc[0] == 11.0, "重叠行应取新值（new 在后覆盖）"

    def test_correction_detected_on_value_jump(self):
        from scripts.cache_incremental import correction_detected

        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-30")
        # 重叠区间 close 从 10.5 跳到 20.5（> 容忍阈值）→ 判定修正
        new = _daily_df(["600519.SH"], "2024-06-01", "2024-07-31")
        new.loc[new["date"] >= pd.Timestamp("2024-06-01"), "close"] = 20.5
        assert correction_detected(old, new, "daily") is True

    def test_correction_not_detected_on_pure_append(self):
        from scripts.cache_incremental import correction_detected

        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-30")
        new = _daily_df(["600519.SH"], "2024-07-01", "2024-07-31")
        assert correction_detected(old, new, "daily") is False


@pytest.mark.skill_data_engine
@pytest.mark.contract
class TestEngineIncremental:
    """验证 engine._maybe_incremental 编排（有旧缓存只拉缺失区间并合并）。"""

    def _make_engine(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("DATA_CACHE_INCREMENTAL", "true")
        _register_data_engine_scripts()
        mod = _load_data_engine_module()
        engine = mod.DataEngine.__new__(mod.DataEngine)
        engine.logger = mod.logger
        engine.backend = "westock"
        engine.is_synthetic = False
        engine.data_sources = ["westock"]
        return mod, engine

    def test_incremental_merges_old_and_new(self, tmp_path, monkeypatch):
        mod, engine = self._make_engine(monkeypatch, tmp_path)

        # 旧缓存：600519 截至 2024-06-30（qfq）
        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-30")
        _seed_cache(tmp_path, "daily", "600519.SH", old,
                    meta={"adjust": "qfq", "cached_end": "2024-06-30", "fetch_ts": time.time()})

        # 让增量区间 [2024-07-01, 2024-07-31] 返回新数据，整段 [2024-01-01, ...] 也返回（验证只用了增量）
        new_seg = _daily_df(["600519.SH"], "2024-07-01", "2024-07-31")
        full = _daily_df(["600519.SH"], "2024-01-01", "2024-07-31")
        calls = {"incremental": 0, "full": 0}

        def fake_fetch_and_clean(symbols=None, start_date=None, end_date=None, adjust="qfq",
                                external_data=None, override_start=None, **kw):
            if override_start == "2024-07-01":
                calls["incremental"] += 1
                return new_seg
            calls["full"] += 1
            return full

        engine.fetch_and_clean = fake_fetch_and_clean

        _register_data_engine_scripts()
        merged, used = engine._maybe_incremental(
            data_type="daily", symbols=["600519.SH"],
            start_date="2024-01-01", end_date="2024-07-31", adjust="qfq"
        )
        assert used is True, "应走增量"
        assert calls["incremental"] == 1 and calls["full"] == 0, "应只拉增量区间，不拉全量"
        assert not merged.duplicated(subset=["code", "date"]).any()
        assert len(merged) == len(old) + len(new_seg)

    def test_incremental_rolls_back_on_correction(self, tmp_path, monkeypatch):
        mod, engine = self._make_engine(monkeypatch, tmp_path)

        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-30")
        _seed_cache(tmp_path, "daily", "600519.SH", old,
                    meta={"adjust": "qfq", "cached_end": "2024-06-30", "fetch_ts": time.time()})

        # 增量区间返回的数据与旧缓存重叠窗口价格跳变 → 修正感知 → 回退全量
        new_seg = _daily_df(["600519.SH"], "2024-07-01", "2024-07-31")
        # 把重叠部分（无，因 new 从 07-01 起）改为含 06 月重叠：制造重叠跳变
        overlap = _daily_df(["600519.SH"], "2024-06-25", "2024-07-31")
        overlap.loc[overlap["date"] >= pd.Timestamp("2024-06-25"), "close"] = 99.9

        engine.fetch_and_clean = lambda *a, **k: overlap

        _register_data_engine_scripts()
        merged, used = engine._maybe_incremental(
            data_type="daily", symbols=["600519.SH"],
            start_date="2024-01-01", end_date="2024-07-31", adjust="qfq"
        )
        assert used is False, "检测到修正应回退全量（used=False）"
        assert merged is None

    def test_incremental_no_cache_falls_to_full(self, tmp_path, monkeypatch):
        mod, engine = self._make_engine(monkeypatch, tmp_path)
        # 不写任何旧缓存
        engine.fetch_and_clean = lambda *a, **k: _daily_df(["600519.SH"], "2024-01-01", "2024-07-31")
        _register_data_engine_scripts()
        merged, used = engine._maybe_incremental(
            data_type="daily", symbols=["600519.SH"],
            start_date="2024-01-01", end_date="2024-07-31", adjust="qfq"
        )
        assert used is False and merged is None

    def test_incremental_switch_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("DATA_CACHE_INCREMENTAL", "false")
        _register_data_engine_scripts()
        mod = _load_data_engine_module()
        engine = mod.DataEngine.__new__(mod.DataEngine)
        engine.logger = mod.logger
        engine.data_sources = ["westock"]
        old = _daily_df(["600519.SH"], "2024-01-01", "2024-06-30")
        _seed_cache(tmp_path, "daily", "600519.SH", old,
                    meta={"adjust": "qfq", "cached_end": "2024-06-30", "fetch_ts": time.time()})
        engine.fetch_and_clean = mock.MagicMock()
        _register_data_engine_scripts()
        merged, used = engine._maybe_incremental(
            data_type="daily", symbols=["600519.SH"],
            start_date="2024-01-01", end_date="2024-07-31", adjust="qfq"
        )
        assert used is False and merged is None
        engine.fetch_and_clean.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
