"""跨引擎薄包装模块（动态加载，不硬依赖）。

遵循项目「跨 Skill 依赖通过薄包装动态加载」的并行开发约定：
- 用 importlib 动态加载 portfolio-risk-engine 的 PortfolioOptimizer
- 上游不可用时降级为 numpy/pandas 直接重算
- 不硬依赖上游模块，reports-engine 可独立运行

提供组合优化报告 P1/P2 功能所需的重算能力：
- load_returns_from_data: 从 DATA 产物加载收益率 DataFrame
- compute_cov_matrix: 协方差矩阵重算（F-P1-2 / F-P2-1）
- compute_risk_contributions: 风险贡献分解（F-P1-2）
- compute_efficient_frontier_points: 有效前沿采样（F-P1-3）
- compute_equal_weight_metrics: 等权基准指标（F-P2-3）
- compute_industry_weights: 行业权重聚合（F-P2-2）
"""
from __future__ import annotations

import importlib.util as ilu
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("reports-engine.cross_engine_adapter")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PORTFOLIO_ENGINE_DIR = os.path.join(ROOT, "skills", "portfolio-risk-engine")
PORTFOLIO_ENGINE_PATH = os.path.join(PORTFOLIO_ENGINE_DIR, "engine.py")


def load_returns_from_data(data_path: Optional[str]) -> pd.DataFrame:
    """从 DATA 产物加载收益率 DataFrame。

    DATA 产物为 parquet，含 date/code/close 列。
    返回透视后的收益率 DataFrame（index=date, columns=code）。

    数据缺失容错：路径不存在或无 close 列时返回空 DataFrame。
    """
    if not data_path or not os.path.exists(data_path):
        logger.warning("DATA 产物路径无效，无法重算收益率")
        return pd.DataFrame()
    try:
        df = pd.read_parquet(data_path)
        if "close" not in df.columns or "code" not in df.columns or "date" not in df.columns:
            logger.warning("DATA 产物缺 close/code/date 列")
            return pd.DataFrame()
        pivot = df.pivot(index="date", columns="code", values="close").sort_index()
        returns = pivot.pct_change().dropna(how="all")
        return returns
    except Exception as e:
        logger.warning(f"加载 DATA 产物失败: {e}")
        return pd.DataFrame()


def _load_portfolio_optimizer():
    """动态加载 portfolio-risk-engine 的 PortfolioOptimizer（薄包装）。

    遵循项目约定：用 importlib 动态加载，不污染 sys.modules['scripts']。
    加载失败返回 None，调用方降级为 numpy 直接计算。
    """
    if not os.path.exists(PORTFOLIO_ENGINE_PATH):
        logger.warning("portfolio-risk-engine/engine.py 不存在")
        return None
    try:
        # 保存当前 scripts 缓存状态
        saved_scripts = {
            k: sys.modules.get(k)
            for k in list(sys.modules.keys())
            if k == "scripts" or k.startswith("scripts.")
        }

        # 临时把 portfolio-risk-engine 目录加入 sys.path
        if PORTFOLIO_ENGINE_DIR not in sys.path:
            sys.path.insert(0, PORTFOLIO_ENGINE_DIR)

        spec = ilu.spec_from_file_location("_portfolio_optimizer_proxy", PORTFOLIO_ENGINE_PATH)
        mod = ilu.module_from_spec(spec)
        sys.modules["_portfolio_optimizer_proxy"] = mod
        spec.loader.exec_module(mod)

        optimizer = mod.PortfolioOptimizer()
        return optimizer
    except Exception as e:
        logger.warning(f"动态加载 PortfolioOptimizer 失败，降级为 numpy 重算: {e}")
        return None
    finally:
        # 恢复 scripts 缓存，避免污染 reports-engine 的 scripts 包
        for key in list(sys.modules.keys()):
            if key == "scripts" or key.startswith("scripts."):
                sys.modules.pop(key, None)
        for k, v in saved_scripts.items():
            if v is not None:
                sys.modules[k] = v
        while PORTFOLIO_ENGINE_DIR in sys.path:
            sys.path.remove(PORTFOLIO_ENGINE_DIR)


def compute_cov_matrix(returns: pd.DataFrame, method: str = "ledoit_wolf") -> pd.DataFrame:
    """协方差矩阵重算（F-P1-2 / F-P2-1）。

    优先用 PortfolioOptimizer.estimate_covariance；不可用时用 pandas cov。
    """
    if returns.empty:
        return pd.DataFrame()

    optimizer = _load_portfolio_optimizer()
    if optimizer is not None:
        try:
            return optimizer.estimate_covariance(returns, method=method)
        except Exception as e:
            logger.warning(f"PortfolioOptimizer.estimate_covariance 失败，降级: {e}")

    # 降级：pandas sample cov
    return returns.cov()


def compute_risk_contributions(
    weights: Dict[str, float],
    cov_matrix: pd.DataFrame,
) -> pd.Series:
    """风险贡献分解（F-P1-2）。

    计算 per-asset 对组合总风险的边际贡献。
    公式：RC_i = w_i * (Σw)_i / sqrt(w'Σw)

    返回 Series（index=code, values=风险贡献占比，和为 1）。
    """
    if not weights or cov_matrix.empty:
        return pd.Series(dtype=float)

    # 对齐权重与协方差矩阵
    codes = [c for c in weights.keys() if c in cov_matrix.index]
    if not codes:
        return pd.Series(dtype=float)

    w = np.array([weights[c] for c in codes])
    cov = cov_matrix.loc[codes, codes].values

    # 组合波动率
    port_var = float(w @ cov @ w)
    if port_var <= 0:
        return pd.Series(1.0 / len(codes), index=codes)

    port_vol = np.sqrt(port_var)

    # 边际风险贡献：MRC_i = (Σw)_i / sqrt(w'Σw)
    mrc = (cov @ w) / port_vol

    # 风险贡献：RC_i = w_i * MRC_i
    rc = w * mrc

    # 归一化为占比
    total_rc = rc.sum()
    if total_rc > 0:
        rc_pct = rc / total_rc
    else:
        rc_pct = np.ones(len(codes)) / len(codes)

    return pd.Series(rc_pct, index=codes)


def compute_efficient_frontier_points(
    returns: pd.DataFrame,
    current_weights: Dict[str, float],
    n_samples: int = 50,
) -> pd.DataFrame:
    """有效前沿采样（F-P1-3）。

    在有效前沿上采样 n_samples 个点，返回 DataFrame 含 [volatility, return, sharpe]。
    同时返回当前组合、等权、最大 Sharpe、最小方差四个关键点。

    降级策略：用 numpy 随机生成大量组合，取上包络近似有效前沿。
    """
    if returns.empty or not current_weights:
        return pd.DataFrame()

    codes = list(current_weights.keys())
    returns = returns[codes] if all(c in returns.columns for c in codes) else returns

    optimizer = _load_portfolio_optimizer()
    if optimizer is not None and not getattr(optimizer, "_fallback", True):
        try:
            expected_rets = optimizer.estimate_expected_returns(returns)
            cov_matrix = optimizer.estimate_covariance(returns)

            # 采样：在 min_variance 和 max_sharpe 之间插值目标收益
            from pypfopt import EfficientFrontier
            from pypfopt import risk_models

            min_var_ef = EfficientFrontier(expected_rets, cov_matrix)
            min_var_ef.min_volatility()
            min_ret = min_var_ef.portfolio_performance()[0]
            min_vol = min_var_ef.portfolio_performance()[1]

            max_sharpe_ef = EfficientFrontier(expected_rets, cov_matrix)
            max_sharpe_ef.max_sharpe()
            max_sharpe_ret = max_sharpe_ef.portfolio_performance()[0]
            max_sharpe_vol = max_sharpe_ef.portfolio_performance()[1]

            target_rets = np.linspace(min_ret, max_sharpe_ret, n_samples)
            points = []
            for tr in target_rets:
                try:
                    ef = EfficientFrontier(expected_rets, cov_matrix)
                    ef.efficient_return(target_return=tr)
                    perf = ef.portfolio_performance()
                    points.append({"volatility": perf[1], "return": perf[0], "sharpe": perf[2]})
                except Exception:
                    continue

            # 等权组合
            n = len(codes)
            eq_w = np.ones(n) / n
            eq_ret = float(expected_rets @ eq_w)
            eq_vol = float(np.sqrt(eq_w @ cov_matrix.values @ eq_w))
            eq_sharpe = (eq_ret - 0.03) / eq_vol if eq_vol > 0 else 0
            points.append({"volatility": eq_vol, "return": eq_ret, "sharpe": eq_sharpe, "label": "等权"})

            # 当前组合
            cur_w = np.array([current_weights[c] for c in codes])
            cur_ret = float(expected_rets @ cur_w)
            cur_vol = float(np.sqrt(cur_w @ cov_matrix.values @ cur_w))
            cur_sharpe = (cur_ret - 0.03) / cur_vol if cur_vol > 0 else 0
            points.append({"volatility": cur_vol, "return": cur_ret, "sharpe": cur_sharpe, "label": "当前组合"})

            # 最大 Sharpe / 最小方差标记
            points.append({"volatility": max_sharpe_vol, "return": max_sharpe_ret, "sharpe": max_sharpe_ret / max_sharpe_vol, "label": "最大Sharpe"})
            points.append({"volatility": min_vol, "return": min_ret, "sharpe": min_ret / min_vol, "label": "最小方差"})

            return pd.DataFrame(points)
        except Exception as e:
            logger.warning(f"PortfolioOptimizer 有效前沿采样失败，降级为随机采样: {e}")

    # 降级：numpy 随机采样
    try:
        cov = returns.cov().values
        mu = returns.mean().values * 252
        n_assets = len(codes)
        rng = np.random.default_rng(42)
        points = []
        for _ in range(n_samples * 3):
            w = rng.random(n_assets)
            w = w / w.sum()
            ret = float(mu @ w)
            vol = float(np.sqrt(w @ cov @ w))
            sharpe = (ret - 0.03) / vol if vol > 0 else 0
            points.append({"volatility": vol, "return": ret, "sharpe": sharpe})

        df = pd.DataFrame(points)
        # 取上包络（按 vol 排序，保留 cummax return）
        df = df.sort_values("volatility")
        df = df[df["return"].cummax() == df["return"]].drop_duplicates("volatility")

        # 当前组合
        cur_w = np.array([current_weights[c] for c in codes])
        cur_ret = float(mu @ cur_w)
        cur_vol = float(np.sqrt(cur_w @ cov @ cur_w))
        df["label"] = ""
        df.loc[len(df)] = {"volatility": cur_vol, "return": cur_ret, "sharpe": (cur_ret - 0.03) / cur_vol if cur_vol > 0 else 0, "label": "当前组合"}
        return df
    except Exception as e:
        logger.warning(f"numpy 随机采样失败: {e}")
        return pd.DataFrame()


def compute_equal_weight_metrics(returns: pd.DataFrame, current_weights: Dict[str, float]) -> Dict[str, float]:
    """等权基准指标（F-P2-3）。

    计算等权组合的 expected_return / volatility / sharpe_ratio / max_drawdown，
    用于与当前优化组合对比。
    """
    if returns.empty or not current_weights:
        return {}

    codes = [c for c in current_weights.keys() if c in returns.columns]
    if not codes:
        return {}

    n = len(codes)
    eq_w = np.ones(n) / n
    ret_data = returns[codes]

    cov = ret_data.cov().values
    mu = ret_data.mean().values * 252

    eq_ret = float(mu @ eq_w)
    eq_vol = float(np.sqrt(eq_w @ cov @ eq_w))
    eq_sharpe = (eq_ret - 0.03) / eq_vol if eq_vol > 0 else 0

    # 等权组合净值序列计算最大回撤
    port_returns = ret_data.mean(axis=1)
    nav = (1 + port_returns).cumprod()
    peak = nav.cummax()
    drawdown = (nav - peak) / peak
    eq_mdd = float(drawdown.min()) if not drawdown.empty else 0

    # 分散化比率 = 1 - HHI
    hhi = float((eq_w ** 2).sum())
    diversification = 1 - hhi

    return {
        "expected_return": eq_ret,
        "volatility": eq_vol,
        "sharpe_ratio": eq_sharpe,
        "max_drawdown": eq_mdd,
        "diversification": diversification,
        "turnover": 0.0,  # 等权无换手
    }


def compute_current_metrics(returns: pd.DataFrame, weights: Dict[str, float]) -> Dict[str, float]:
    """当前组合指标（F-P2-3，用于对比表）。

    从权重 + 收益率重算当前组合的指标，补充 metadata.metrics 缺失的维度。
    """
    if returns.empty or not weights:
        return {}

    codes = [c for c in weights.keys() if c in returns.columns]
    if not codes:
        return {}

    w = np.array([weights[c] for c in codes])
    ret_data = returns[codes]

    cov = ret_data.cov().values
    mu = ret_data.mean().values * 252

    cur_ret = float(mu @ w)
    cur_vol = float(np.sqrt(w @ cov @ w))
    cur_sharpe = (cur_ret - 0.03) / cur_vol if cur_vol > 0 else 0

    # 净值序列 + 最大回撤
    port_returns = (ret_data * w).sum(axis=1)
    nav = (1 + port_returns).cumprod()
    peak = nav.cummax()
    drawdown = (nav - peak) / peak
    cur_mdd = float(drawdown.min()) if not drawdown.empty else 0

    hhi = float((w ** 2).sum())
    diversification = 1 - hhi

    return {
        "expected_return": cur_ret,
        "volatility": cur_vol,
        "sharpe_ratio": cur_sharpe,
        "max_drawdown": cur_mdd,
        "diversification": diversification,
        "turnover": 0.0,
    }


def compute_industry_weights(weights: Dict[str, float], industry_map: Dict[str, str]) -> pd.DataFrame:
    """行业权重聚合（F-P2-2）。

    将 per-asset 权重聚合为 per-industry 权重，与等权基准对比。
    基准策略：等权基准 = 1/行业数（简化方案，PRD 标注的可接受简化）。

    返回 DataFrame: [industry, actual_weight, benchmark_weight, deviation]
    """
    if not weights:
        return pd.DataFrame()

    # 按行业聚合
    industry_weight = {}
    for code, w in weights.items():
        ind = industry_map.get(code, "其他")
        industry_weight[ind] = industry_weight.get(ind, 0) + w

    # 等权基准
    n_industries = len(industry_weight) if industry_weight else 1
    bench = 1.0 / n_industries

    rows = []
    for ind, w in sorted(industry_weight.items(), key=lambda x: -x[1]):
        rows.append({
            "industry": ind,
            "actual_weight": w,
            "benchmark_weight": bench,
            "deviation": w - bench,
        })

    return pd.DataFrame(rows)
