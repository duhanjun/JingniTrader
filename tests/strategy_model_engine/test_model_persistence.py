"""strategy-model-engine 核心路径增厚测试（OPEN-2026-021 加固项 7）。

覆盖 MLflow 实验命名 / 模型持久化（joblib 落盘）/ 加载往返 三条核心路径：
- experiment 命名：ModelEngine.__init__ 须以配置的 MLFLOW_EXPERIMENT_NAME 调用 set_experiment
- 模型持久化：train() 须将模型落盘为 .pkl 且路径非空
- 加载往返：joblib.load(model_path) 重建模型，predict 与原模型一致（不依赖重型原生栈）

不 mock sklearn/joblib/mlflow，使用轻量合成数据 + sklearn RandomForest（lightgbm 缺失时
引擎自动降级）完成真实训练往返，确定性、无 cvxpy/alphalens 重型计算。
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
    """显式加载 strategy-model-engine/engine.py 为独立模块（不 mock 重型依赖）。

    与 test_run_contract.py 不同：本文件需要真实 sklearn/joblib 完成模型往返，
    因此不把 sklearn / joblib / mlflow 替换为 MagicMock。
    """
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

    # 撤销全局 conftest 对 sklearn/joblib 的 MagicMock 替换，使本文件能用真实 sklearn
    # 完成模型训练与 joblib 往返（否则 RandomForest 会是 MagicMock，joblib 无法 pickle）
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


def _synthetic_data(n_codes=3, n_dates=60, n_features=4, seed=0):
    """构造 prepare_data 所需的 factor_df / price_df 合成数据。"""
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
class TestStrategyModelCorePaths:
    def test_mlflow_experiment_named_from_config(self, monkeypatch, tmp_path):
        """ModelEngine.__init__ 须以配置的 MLFLOW_EXPERIMENT_NAME 命名实验。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))

        strategy_mod = _load_strategy_engine_module()
        captured = {}

        if strategy_mod.HAS_MLFLOW:
            with mock.patch.object(strategy_mod.mlflow, "set_experiment",
                                   side_effect=lambda name: captured.setdefault("name", name)):
                strategy_mod.ModelEngine()
            assert captured.get("name") == strategy_mod.MLFLOW_EXPERIMENT_NAME, \
                f"实验命名应与配置一致，实际={captured.get('name')}"
        else:
            # mlflow 不可用时不抛错（降级 _NullContext），仅验证构造成功
            engine = strategy_mod.ModelEngine()
            assert engine is not None

    def test_train_persists_model_to_disk(self, monkeypatch, tmp_path):
        """train() 须将模型落盘为 .pkl，路径非空且文件存在。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))

        strategy_mod = _load_strategy_engine_module()
        engine = strategy_mod.ModelEngine()

        factor_df, price_df, feature_cols = _synthetic_data()
        X, y, dates = engine.prepare_data(factor_df, price_df, feature_cols)

        model, metrics, predictions, model_path = engine.train(X, y)

        assert model_path, "train() 未返回模型落盘路径"
        assert model_path.endswith(".pkl"), "模型应落盘为 .pkl"
        assert os.path.exists(model_path), "模型文件不存在"

    def test_model_load_roundtrip(self, monkeypatch, tmp_path):
        """joblib.load(model_path) 重建模型，predict 与原模型一致（加载往返）。"""
        monkeypatch.setenv("QUANT_MODEL_DIR", str(tmp_path))

        strategy_mod = _load_strategy_engine_module()
        engine = strategy_mod.ModelEngine()

        factor_df, price_df, feature_cols = _synthetic_data(seed=7)
        X, y, dates = engine.prepare_data(factor_df, price_df, feature_cols)

        model, _metrics, _preds, model_path = engine.train(X, y)

        # 取一份测试样本
        X_test = X.iloc[:5]
        orig_pred = list(model.predict(X_test))

        # 重新加载并对比
        assert strategy_mod.HAS_JOBLIB, "joblib 不可用无法验证往返"
        reloaded = strategy_mod.joblib.load(model_path)
        reload_pred = list(reloaded.predict(X_test))

        # 浮点预测用容差比较（RandomForest 重加载后数值微小漂移）
        assert np.allclose(reload_pred, orig_pred, rtol=1e-9, atol=1e-9), \
            "加载往返后 predict 不一致"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
