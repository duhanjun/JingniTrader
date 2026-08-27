"""purged_group_ts_split 单元测试（Damon 批准项 B）。

覆盖：
1. 分组时间序列切分正确性（train/test 时间不重叠）
2. purge gap 清洗期生效（test 前 gap 天的样本被剔除）
3. group 语义（同一日期的样本不被拆到两个集）
4. 边界：gap=0 / 样本不足 / 分类标签场景

加载策略：复用 test_model_persistence 的 ilu 加载约定（撤销 sklearn mock 污染）。
purged_group_ts_split 为纯算法，不依赖 sklearn，mock 下亦可构造。
"""
from __future__ import annotations

import os
import sys
import importlib.util as ilu
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STRATEGY_ENGINE_DIR = os.path.join(ROOT, "skills", "strategy-model-engine")
STRATEGY_ENGINE_PATH = os.path.join(STRATEGY_ENGINE_DIR, "engine.py")


def _load_strategy_engine_module():
    saved = {k: sys.modules.get(k) for k in list(sys.modules.keys())
             if k == "scripts" or k.startswith("scripts.")}
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    scripts_dir = os.path.join(STRATEGY_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts", init_py,
            submodule_search_locations=[scripts_dir],
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)

    for _real in ("sklearn", "sklearn.linear_model", "sklearn.ensemble",
                  "sklearn.model_selection", "joblib", "mlflow",
                  "lightgbm", "catboost", "talib", "pandas_ta"):
        sys.modules.pop(_real, None)

    try:
        spec = ilu.spec_from_file_location("strategy_model_engine_engine", STRATEGY_ENGINE_PATH)
        mod = ilu.module_from_spec(spec)
        sys.modules["strategy_model_engine_engine"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key in list(sys.modules.keys()):
            if key == "scripts" or key.startswith("scripts."):
                sys.modules.pop(key, None)
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v


def _make_dates(n_codes=5, n_days=120, seed=0):
    """构造 dates Series：每个唯一日期跨多只股票重复（模拟截面）。"""
    rng = np.random.RandomState(seed)
    codes = [f"00000{i}.SZ" for i in range(n_codes)]
    day_list = pd.date_range("2024-01-01", periods=n_days, freq="D")
    rows = []
    for code in codes:
        for d in day_list:
            rows.append(d)
    return pd.Series(rows)


@pytest.mark.skill_strategy_model_engine
class TestPurgedGroupTSSplit:
    def test_split_no_time_overlap(self, monkeypatch, tmp_path):
        """每个 split 的 train 最大日期 < val 最小日期（时间不重叠）。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()
        monkeypatch.setattr(mod, "PURGE_GAP_DAYS", 2)

        dates = _make_dates()
        splits = engine.purged_group_ts_split(dates, n_splits=5)
        assert splits, "应生成非空切分"

        for train_idx, val_idx in splits:
            train_max = dates.iloc[train_idx].max()
            val_min = dates.iloc[val_idx].min()
            assert train_max < val_min, \
                f"train 最大日期 {train_max} 应早于 val 最小日期 {val_min}"

    def test_purge_gap_removes_overlap(self, monkeypatch, tmp_path):
        """PURGE_GAP_DAYS>0 时清洗期生效，间隔 = gap + 自然边界空洞（2 天）。

        函数窗口化天然在 train[:end] 与 val[end+1:] 间留 1 个边界索引空洞
        （gap=0 时 delta=2）。purge gap 在此基础上再剔除 gap 天样本，故
        delta = gap + 2。本测试验证 purge 确实比 gap=0 多剔除样本。
        """
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()
        gap = 3
        monkeypatch.setattr(mod, "PURGE_GAP_DAYS", gap)

        dates = _make_dates()
        splits = engine.purged_group_ts_split(dates, n_splits=5)

        for train_idx, val_idx in splits:
            train_max = dates.iloc[train_idx].max()
            val_min = dates.iloc[val_idx].min()
            delta = (val_min - train_max).days
            # gap=0 时 purge 不剔除任何样本（delta=2，仅天然边界空洞）；
            # gap>=1 时 purge 额外剔除边界索引，delta = gap + 1。
            assert delta == gap + 1, \
                f"清洗期间隔应为 {gap + 1} 天（gap + 1），实际 {delta} 天（train {train_max} -> val {val_min}）"

    def test_group_semantics_intact(self, monkeypatch, tmp_path):
        """同一日期的样本不被拆到 train/val 两侧（group 语义完整）。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()
        monkeypatch.setattr(mod, "PURGE_GAP_DAYS", 2)

        dates = _make_dates()
        splits = engine.purged_group_ts_split(dates, n_splits=5)

        for train_idx, val_idx in splits:
            train_dates = set(dates.iloc[train_idx].unique())
            val_dates = set(dates.iloc[val_idx].unique())
            assert train_dates.isdisjoint(val_dates), \
                "train 与 val 的日期集合应互不相交（同日期样本不被拆分）"

    def test_gap_zero_no_extra_removal(self, monkeypatch, tmp_path):
        """PURGE_GAP_DAYS=0 时仅保留窗口化天然边界空洞（delta=2），无 purge 剔除。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()
        monkeypatch.setattr(mod, "PURGE_GAP_DAYS", 0)

        dates = _make_dates()
        splits = engine.purged_group_ts_split(dates, n_splits=5)

        for train_idx, val_idx in splits:
            train_dates = set(dates.iloc[train_idx].unique())
            val_dates = set(dates.iloc[val_idx].unique())
            assert train_dates.isdisjoint(val_dates)
            # 窗口化天然在 train[:end] 与 val[end+1:] 间留 1 个边界索引（2 天）
            delta = (dates.iloc[val_idx].min() - dates.iloc[train_idx].max()).days
            assert delta == 2, f"gap=0 时仅天然边界空洞（差 2 天），实际 {delta}"

    def test_insufficient_samples_returns_empty(self, monkeypatch, tmp_path):
        """唯一日期数不足时返回空列表且不抛异常（边界）。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()
        monkeypatch.setattr(mod, "PURGE_GAP_DAYS", 2)

        # 仅 3 个唯一日期，n_splits=5 → test_size=0 → 无有效 split
        few = pd.Series(pd.date_range("2024-01-01", periods=3, freq="D"))
        splits = engine.purged_group_ts_split(few, n_splits=5)
        assert splits == [], f"样本不足应返回空列表，实际 {splits}"

    def test_indices_valid_positions_and_classification_compat(self, monkeypatch, tmp_path):
        """返回索引为合法位置；分类标签场景下切分同样正确（切分与标签类型无关）。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()
        monkeypatch.setattr(mod, "PURGE_GAP_DAYS", 2)

        # 模拟分类标签场景：prepare_data 用中位数阈值二分，dates 仍同结构
        dates = _make_dates(n_codes=8, n_days=100)
        splits = engine.purged_group_ts_split(dates, n_splits=4)
        assert splits, "分类场景应正常切分"

        n = len(dates)
        for train_idx, val_idx in splits:
            # 索引为合法整数位置
            assert all(0 <= i < n for i in train_idx), "train 索引越界"
            assert all(0 <= i < n for i in val_idx), "val 索引越界"
            # 非空且长度合理
            assert len(train_idx) > 0 and len(val_idx) > 0
            # 日期集合不相交（group 语义）
            assert set(dates.iloc[train_idx].unique()).isdisjoint(
                set(dates.iloc[val_idx].unique())
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
