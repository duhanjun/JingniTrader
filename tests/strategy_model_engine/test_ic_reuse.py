"""IC 计算统一复用回归测试（Damon 批准项 A）。

验证 strategy-model-engine 的 IC 计算已统一复用 factor-engine 的
ic_vectorized.ic_series_pearson，且：
- 无真实日期时降级为全局 corr（与原实现数值一致，不破坏既有输出语义）；
- 有真实截面日期时走截面 IC 均值（量化标准口径，有意升级）。
使用轻量合成数据 + 真实 sklearn（参照 test_model_persistence.py 的加载约定，
撤销 conftest 对 sklearn 的 MagicMock，避免 RandomForest/IC 计算被 mock 污染）。
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
STRATEGY_ENGINE_DIR = os.path.join(ROOT, "skills", "strategy-model-engine")
STRATEGY_ENGINE_PATH = os.path.join(STRATEGY_ENGINE_DIR, "engine.py")


def _load_strategy_engine_module():
    """显式加载 strategy-model-engine/engine.py（撤销 sklearn mock，用真实库）。"""
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

    # 撤销 conftest 对 sklearn/joblib 的 MagicMock，使 IC 计算用真实库
    for _real in ("sklearn", "sklearn.linear_model", "sklearn.ensemble",
                  "sklearn.model_selection", "joblib", "mlflow",
                  "lightgbm", "catboost", "talib", "pandas_ta"):
        sys.modules.pop(_real, None)

    # mlflow 懒加载守卫（沿用 OPEN-2026-0814-13 既有方案）：
    # 上面 pop 掉 mlflow 后重新 import，mlflow 3.x 的 `mlflow.store` 子模块
    # 不会自动挂回顶层命名空间，导致后续 mlflow.set_experiment() →
    # sqlalchemy_store 报 `AttributeError: module 'mlflow' has no attribute 'store'`。
    # 该现象只在「本文件在别的测试之后运行」时出现（此时 mlflow 已被前述测试
    # 导入过，pop 后残留部分初始化状态），单跑本文件则不复现。
    # 修复：显式 import 后手动挂回属性，与 strategy-model-engine/engine.py
    # 内的 mlflow 导入守卫保持一致。
    try:
        import mlflow as _mlflow
        import importlib as _importlib
        _importlib.import_module("mlflow.store")
        if not hasattr(_mlflow, "store"):
            _mlflow.store = sys.modules.get("mlflow.store")
    except Exception:  # pragma: no cover - mlflow 缺失时本就走降级路径
        pass

    try:
        spec = ilu.spec_from_file_location("strategy_model_engine_engine", STRATEGY_ENGINE_PATH)
        mod = ilu.module_from_spec(spec)
        sys.modules["__main__"] = mod  # 部分脚本依赖 __main__ 探查
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


def _synthetic(n_codes=3, n_dates=40, n_features=4, seed=0):
    rng = np.random.RandomState(seed)
    codes = [f"00000{i}.SZ" for i in range(n_codes)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    rows = []
    for code in codes:
        for d in dates:
            rows.append({
                "code": code,
                "date": d,
                "close": 10.0 + rng.normal(0, 1),
                **{f"f{i}": rng.normal(0, 1) for i in range(n_features)},
            })
    factor_df = pd.DataFrame(rows)
    price_df = factor_df[["code", "date", "close"]].copy()
    feature_cols = [f"f{i}" for i in range(n_features)]
    return factor_df, price_df, feature_cols


@pytest.mark.skill_strategy_model_engine
class TestICReuse:
    def test_no_dates_falls_back_to_global_corr(self, monkeypatch, tmp_path):
        """无真实日期时 IC = 全局 corr，与原实现数值一致（不破坏既有语义）。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()

        rng = np.random.RandomState(1)
        pred = pd.Series(rng.normal(0, 1, 100))
        y = pd.Series(rng.normal(0, 1, 100))

        ic = engine._compute_ic(pred, y, dates_series=None)
        expected = float(pred.corr(y))
        assert abs(ic - expected) < 1e-12, f"无日期降级应等于全局 corr，实际 {ic} vs {expected}"

    def test_with_dates_uses_factor_engine_ic(self, monkeypatch, tmp_path):
        """有真实截面日期时复用 factor-engine 截面 IC 均值，且等于直接调用。

        截面结构：同一日期跨多只股票重复（每个日期一个 cross-section），
        与 prepare_data 返回的 dates Series 结构一致。
        """
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()

        rng = np.random.RandomState(2)
        n_dates = 10
        n_per = 6
        date_vals = np.repeat(
            pd.date_range("2024-01-01", periods=n_dates, freq="D").values, n_per
        )
        idx = list(range(len(date_vals)))
        pred = pd.Series(rng.normal(0, 1, len(date_vals)), index=idx)
        y = pd.Series(rng.normal(0, 1, len(date_vals)), index=idx)
        dates = pd.Series(date_vals, index=idx)

        ic = engine._compute_ic(pred, y, dates_series=dates)

        # 直接调用 factor-engine 的 ic_series_pearson（同一加载逻辑）
        ic_func = mod._load_factor_ic()
        assert ic_func is not None, "factor-engine ic_vectorized 应成功加载"
        expected_series = ic_func(pred, y, dates=dates, min_obs=1)
        expected = float(expected_series.mean())

        assert abs(ic - expected) < 1e-12, f"截面 IC 均值应等于直接调用，实际 {ic} vs {expected}"
        assert -1.0 <= ic <= 1.0, f"IC 应在 [-1,1]，实际 {ic}"

    def test_train_ic_finite_and_in_range(self, monkeypatch, tmp_path):
        """train() 在提供 test_dates（仅测试行索引）时产出的 IC 有限且落在 [-1,1]。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        # 强制 random_forest（沿用 test_model_persistence.py 的既有约定）：
        # 本用例撤销了 sklearn 的 MagicMock 以验证真实 IC 计算，此时若沿用默认
        # MODEL_TYPE=lightgbm，lightgbm.sklearn 在真实 sklearn 环境下会抛
        # LightGBMError（其自身环境探测依赖被 mock 的 sys.modules 状态）。
        # 本用例验证目标是 IC 计算口径，不是训练器选型，故固定为 random_forest。
        monkeypatch.setenv("MODEL_TYPE", "random_forest")
        mod = _load_strategy_engine_module()
        engine = mod.ModelEngine()

        factor_df, price_df, feature_cols = _synthetic(n_codes=10, n_dates=100, seed=5)
        X, y, dates = engine.prepare_data(factor_df, price_df, feature_cols)

        # test_dates 仅含测试行索引（与 train() 的 train_mask 语义一致）
        n = len(X)
        split = int(n * 0.7)
        test_idx = X.index[split:]
        test_dates = dates.loc[test_idx]

        model, metrics, predictions, _ = engine.train(X, y, test_dates=test_dates)
        assert "ic" in metrics, "train() 应产出 ic 指标"
        assert np.isfinite(metrics["ic"]), "IC 应为有限值"
        assert -1.0 <= metrics["ic"] <= 1.0, f"IC 应在 [-1,1]，实际 {metrics['ic']}"

    def test_ic_func_loader_caches(self, monkeypatch, tmp_path):
        """_load_factor_ic 缓存生效，重复调用返回同一函数对象。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))
        mod = _load_strategy_engine_module()
        f1 = mod._load_factor_ic()
        f2 = mod._load_factor_ic()
        assert f1 is f2, "ic 加载器应缓存同一函数对象"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
