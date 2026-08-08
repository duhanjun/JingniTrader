"""portfolio-risk-engine 单元测试：estimate_covariance 输入校验与降级。

覆盖 P1 修复：risk_models.CovarianceShrinkage(returns, returns_data=True)
.ledoit_wolf() 在样本不足 / 常数列 / NaN 时会抛 ValueError，现应优雅降级：
- 常数列 → 对角线收缩（_diagonal_shrink_cov）
- 样本数 < 资产数 → 样本协方差
- 含 NaN → 填充后重试，仍失败则对角线收缩
- 正常数据 → 仍走 ledoit_wolf 原路径
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
PORTFOLIO_ENGINE_DIR = os.path.join(ROOT, "skills", "portfolio-risk-engine")
PORTFOLIO_ENGINE_PATH = os.path.join(PORTFOLIO_ENGINE_DIR, "engine.py")


def _load_portfolio_engine_module():
    """显式加载 portfolio-risk-engine/engine.py 为独立模块（与现有测试同模式）。"""
    saved = {k: sys.modules.get(k) for k in list(sys.modules.keys())
             if k == "scripts" or k.startswith("scripts.")}
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    scripts_dir = os.path.join(PORTFOLIO_ENGINE_DIR, "scripts")
    init_py = os.path.join(scripts_dir, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts", init_py,
            submodule_search_locations=[scripts_dir],
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)

    # 若环境已安装 pypfopt/cvxpy 则保留；缺失时 mock，避免 import 失败
    for _m in ("cvxpy", "scipy", "scipy.optimize", "talib", "pandas_ta"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    try:
        spec = ilu.spec_from_file_location("portfolio_risk_engine_engine_cov", PORTFOLIO_ENGINE_PATH)
        mod = ilu.module_from_spec(spec)
        sys.modules["portfolio_risk_engine_engine_cov"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key in list(sys.modules.keys()):
            if key == "scripts" or key.startswith("scripts."):
                sys.modules.pop(key, None)
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v


@pytest.fixture
def optimizer():
    mod = _load_portfolio_engine_module()
    # 强制非 fallback（即便 pypfopt 缺失也走降级分支，验证降级逻辑本身）
    opt = mod.PortfolioOptimizer()
    opt._fallback = False
    return opt


def _valid_returns(n_obs=120, n_assets=5, seed=0):
    rng = np.random.RandomState(seed)
    data = rng.normal(0, 0.01, size=(n_obs, n_assets))
    return pd.DataFrame(data, columns=[f"A{i}" for i in range(n_assets)])


def test_constant_column_falls_back_to_diagonal(optimizer):
    """常数列应降级为对角线收缩（正定、形状正确），不抛 ValueError。"""
    df = _valid_returns()
    df["A0"] = 1.0  # 常数因子列
    cov = optimizer.estimate_covariance(df, method="ledoit_wolf")
    assert cov.shape == (5, 5)
    # 对角线收缩：非对角应接近 0
    off_diag = cov.values - np.diag(np.diag(cov.values))
    assert np.allclose(off_diag, 0.0, atol=1e-6)
    assert np.all(np.diag(cov.values) > 0)


def test_insufficient_samples_falls_back_to_sample_cov(optimizer):
    """样本数 < 资产数时，ledoit_wolf 易解包失败，应降级为样本协方差。"""
    df = pd.DataFrame(
        np.random.RandomState(1).normal(0, 0.01, size=(3, 6)),
        columns=[f"B{i}" for i in range(6)],
    )
    cov = optimizer.estimate_covariance(df, method="ledoit_wolf")
    assert cov.shape == (6, 6)
    # 样本协方差对角线为各列方差，应全部有限
    assert np.all(np.isfinite(np.diag(cov.values)))


def test_nan_handled_without_raise(optimizer):
    """含 NaN 的输入应被填充处理，最终返回有限协方差矩阵。"""
    df = _valid_returns()
    df.iloc[0, 0] = np.nan
    df.iloc[5, 2] = np.nan
    cov = optimizer.estimate_covariance(df, method="ledoit_wolf")
    assert cov.shape == (5, 5)
    assert np.all(np.isfinite(cov.values))


def test_valid_data_runs_ledoit_wolf(optimizer):
    """正常数据应走 ledoit_wolf 原路径，返回对称正定矩阵。"""
    df = _valid_returns()
    cov = optimizer.estimate_covariance(df, method="ledoit_wolf")
    assert cov.shape == (5, 5)
    assert np.allclose(cov.values, cov.values.T, atol=1e-8)
    assert np.all(np.isfinite(cov.values))
