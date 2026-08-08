"""
组合优化与风控引擎主逻辑
负责权重优化、协方差估计、风险归因、VaR/CVaR、止损机制
"""
import os
import sys
import json
import logging
from typing import Dict, Any, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from scripts.config import (
    PORTFOLIO_DIR, OPTIMIZATION_METHOD, RISK_FREE_RATE,
    MAX_SINGLE_STOCK_WEIGHT, MAX_INDUSTRY_DEVIATION,
    MAX_TURNOVER, COVARIANCE_METHOD, EXPECTED_RETURNS_METHOD,
    MAX_DAILY_LOSS_RATIO, INDIVIDUAL_STOP_LOSS,
    VAR_CONFIDENCE, CVAR_CONFIDENCE, MIN_WEIGHT,
)

logger = logging.getLogger("portfolio-risk-engine")

try:
    from pypfopt import EfficientFrontier, risk_models, expected_returns, HRPOpt
    HAS_PYPFOPT = True
except Exception:
    # 库已安装但导入期抛非 ImportError（如 cvxpy 版本不兼容导致加载期异常）
    # 也视为不可用，避免上层 import 失败击穿运行流程
    HAS_PYPFOPT = False

try:
    import cvxpy as cp
    HAS_CVXPY = True
except Exception:
    HAS_CVXPY = False


class PortfolioOptimizer:
    """组合优化器，基于 PyPortfolioOpt"""

    def __init__(self):
        if not HAS_PYPFOPT:
            logger.warning("PyPortfolioOpt 未安装，使用等权组合")
            self._fallback = True
        else:
            self._fallback = False

    def estimate_expected_returns(
        self,
        returns: pd.DataFrame,
        method: str = EXPECTED_RETURNS_METHOD
    ) -> pd.Series:
        """估计预期收益

        注意：调用方传入的是已经计算好的收益率 DataFrame（非原始价格），
        因此必须显式传 returns_data=True，否则 pypfopt 1.5+ 会默认按价格
        处理收益率数据，导致 ema_historical_return 返回 inf，进而使优化
        求解报 ValueError: Problem data contains NaN or Inf。

        当 pypfopt 未安装（_fallback=True）时，直接用历史均值收益率作为
        预期收益的朴素估计，避免引用未定义的 expected_returns 抛 NameError。
        """
        if self._fallback:
            # 无 pypfopt：用年化历史均值作为预期收益的朴素估计
            ann_factor = 252
            return returns.mean() * ann_factor
        if method == "ema_historical":
            return expected_returns.ema_historical_return(returns, returns_data=True, frequency=252)
        elif method == "mean_historical":
            return expected_returns.mean_historical_return(returns, returns_data=True, frequency=252)
        elif method == "capm_return":
            return expected_returns.capm_return(returns, returns_data=True)
        else:
            return expected_returns.mean_historical_return(returns, returns_data=True, frequency=252)

    def estimate_covariance(
        self,
        returns: pd.DataFrame,
        method: str = COVARIANCE_METHOD
    ) -> pd.DataFrame:
        """估计协方差矩阵

        与 estimate_expected_returns 同理，传入的是收益率数据而非价格，
        必须设置 returns_data=True，避免 pypfopt 1.5+ 按价格误处理。

        输入校验：CovarianceShrinkage.ledoit_wolf 在样本不足（< 资产数）或
        存在 NaN/常数列时会抛 ``ValueError: not enough values to unpack`` 等异常。
        这里先做最小样本量 / 缺失 / 恒定列检测，失败时降级到样本协方差或
        对角线收缩，避免直接击穿上层调用。
        """
        if self._fallback:
            return returns.cov()

        # ── 输入校验与降级 ──
        try:
            _returns = returns.copy()
            n_obs, n_assets = _returns.shape
            if n_assets < 1 or n_obs < 2:
                logger.warning(
                    "协方差估计跳过：样本不足 (obs=%s, assets=%s)，降级为对角线收缩",
                    n_obs, n_assets,
                )
                return self._diagonal_shrink_cov(_returns)
            # 缺失值检测
            if _returns.isnull().any().any():
                logger.warning("协方差估计：检测到 NaN，先按列前向填充并置零兜底")
                _returns = _returns.ffill().fillna(0.0)
            # 常数列检测（方差为 0 会导致 ledoit_wolf 解包失败）
            const_cols = [c for c in _returns.columns if _returns[c].nunique(dropna=True) <= 1]
            if const_cols:
                logger.warning(
                    "协方差估计：检测到 %d 个常数列，降级为对角线收缩: %s",
                    len(const_cols), const_cols,
                )
                return self._diagonal_shrink_cov(_returns)
            # 最小样本量校验：ledoit_wolf 需要 obs >= assets，否则解包失败
            if n_obs < n_assets:
                logger.warning(
                    "协方差估计：样本数(%s) < 资产数(%s)，ledoit_wolf 易失败，降级样本协方差",
                    n_obs, n_assets,
                )
                return _returns.cov()
        except Exception as e:
            logger.warning("协方差输入校验异常，降级对角线收缩: %s", e)
            return self._diagonal_shrink_cov(returns)

        try:
            if method == "ledoit_wolf":
                return risk_models.CovarianceShrinkage(returns, returns_data=True).ledoit_wolf()
            elif method == "sample_cov":
                return risk_models.sample_cov(returns, returns_data=True)
            elif method == "shrinkage":
                return risk_models.CovarianceShrinkage(returns, returns_data=True).ledoit_wolf(shrinkage_target="constant_correlation")
            else:
                return risk_models.CovarianceShrinkage(returns, returns_data=True).ledoit_wolf()
        except Exception as e:
            logger.warning(
                "协方差估计(%s)失败，降级为对角线收缩: %s", method, e
            )
            return self._diagonal_shrink_cov(returns)

    @staticmethod
    def _diagonal_shrink_cov(returns: pd.DataFrame) -> pd.DataFrame:
        """兜底协方差：对角线收缩（仅保留各资产方差，协方差置 0）。

        保证矩阵正定且形状正确，避免上层优化器因奇异/解包失败而崩溃。
        """
        try:
            var = returns.var().clip(lower=1e-8)
            cov = pd.DataFrame(np.diag(var.values), index=returns.columns, columns=returns.columns)
            return cov
        except Exception:
            # 极端兜底：单位矩阵
            n = returns.shape[1] if returns.shape[1] > 0 else 1
            cols = list(returns.columns) if returns.shape[1] > 0 else [f"a{i}" for i in range(n)]
            return pd.DataFrame(np.eye(n), index=cols, columns=cols)

    def optimize(
        self,
        expected_rets: pd.Series,
        cov_matrix: pd.DataFrame,
        method: str = OPTIMIZATION_METHOD,
        constraints: Optional[Dict[str, float]] = None,
        current_weights: Optional[pd.Series] = None,
        returns: Optional[pd.DataFrame] = None,
    ) -> Tuple[pd.Series, Dict[str, float]]:
        """执行组合优化

        参数:
            expected_rets: 预期收益 Series
            cov_matrix: 协方差矩阵 DataFrame
            method: 优化方法 (max_sharpe/min_variance/max_return/
                     risk_parity/hierarchical_risk_parity/hrp/cvar/black_litterman)
            constraints: 约束字典
            current_weights: 当前持仓权重（用于换手率约束）
            returns: 原始收益率 DataFrame（HRP/CVaR 方法需要）
        """
        if self._fallback:
            n = len(expected_rets)
            weights = pd.Series(1.0 / n, index=expected_rets.index)
            return weights, {"method": "equal_weight", "note": "PyPortfolioOpt 未安装，使用等权"}

        if constraints is None:
            constraints = {}

        max_weight = constraints.get("max_weight", MAX_SINGLE_STOCK_WEIGHT)
        min_weight = constraints.get("min_weight", MIN_WEIGHT)

        # HRP 需要 returns
        if method in ("risk_parity", "hierarchical_risk_parity", "hrp"):
            return self._optimize_hrp(returns)

        # CVaR 需要 returns
        if method == "cvar":
            return self._optimize_cvar(returns, constraints)

        # Black-Litterman 独立处理
        if method == "black_litterman":
            return self._black_litterman(expected_rets, cov_matrix, constraints)

        # 带换手率约束的优化
        if current_weights is not None and len(current_weights) > 0:
            return self._optimize_with_turnover(
                expected_rets, cov_matrix, current_weights, constraints
            )

        # 标准均值-方差优化
        # max_sharpe 在合成/非理想输入下可能 infeasible（约束无可行解），
        # 抛 pypfopt.exceptions.OptimizationError。这里做降级链：
        # max_sharpe → min_variance → 等权，确保始终产出可用权重，不击穿上层流程。
        # 根因之一：单股上限 MAX_SINGLE_STOCK_WEIGHT 过严（如 0.10）且资产数少时，
        # max_weight * n_assets < 1 无可行解。构造前先放宽上界到至少 1/n_assets。
        n_assets = len(expected_rets)
        eff_max_weight = max_weight
        if n_assets > 0 and eff_max_weight * n_assets < 1.0 - 1e-6:
            eff_max_weight = 1.0 / n_assets
            logger.warning(
                "权重上界过严(max=%.3f, 资产数=%d)导致无可行解，放宽到 %.3f",
                max_weight, n_assets, eff_max_weight,
            )
        try:
            ef = EfficientFrontier(expected_rets, cov_matrix, weight_bounds=(min_weight, eff_max_weight))
        except Exception as e:
            logger.warning("EfficientFrontier 构造失败，降级等权: %s", e)
            return self._equal_weight(expected_rets.index), {
                "method": "equal_weight", "note": "EF 构造失败", "error": str(e),
            }

        try:
            if method == "max_sharpe":
                weights = ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)
            elif method == "min_variance":
                weights = ef.min_volatility()
            elif method == "max_return":
                weights = ef.max_quadratic_utility()
            else:
                weights = ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)
            cleaned = ef.clean_weights()
            perf = ef.portfolio_performance(risk_free_rate=RISK_FREE_RATE)
            return pd.Series(cleaned), {
                "expected_return": float(perf[0]),
                "volatility": float(perf[1]),
                "sharpe_ratio": float(perf[2]),
            }
        except Exception as e:
            # 首选方法失败（如 max_sharpe infeasible）：降级 min_variance
            logger.warning("组合优化(%s)失败，降级 min_variance: %s", method, e)
            try:
                weights = ef.min_volatility()
                cleaned = ef.clean_weights()
                return pd.Series(cleaned), {
                    "method": "min_variance_fallback", "note": f"首选 {method} 不可行",
                    "error": str(e),
                }
            except Exception as e2:
                # min_variance 也失败：兜底等权
                logger.warning("min_variance 亦失败，兜底等权: %s", e2)
                return self._equal_weight(expected_rets.index), {
                    "method": "equal_weight", "note": "优化器全部失败",
                    "error": str(e2),
                }

    def _optimize_hrp(
        self,
        returns: Optional[pd.DataFrame],
    ) -> Tuple[pd.Series, Dict[str, float]]:
        """分层风险平价 —— 修复版（接收真实 returns DataFrame）"""
        if returns is None or returns.empty:
            logger.warning("HRP 收到空 returns，返回等权")
            cols = returns.columns if returns is not None else []
            return self._equal_weight(cols), {"method": "hrp_fallback", "note": "空收益"}

        try:
            hrp = HRPOpt(returns)
            weights = hrp.optimize()
            cleaned = hrp.clean_weights()
            try:
                perf = hrp.portfolio_performance()
                meta = {
                    "method": "hrp",
                    "expected_return": float(perf[0]),
                    "volatility": float(perf[1]),
                    "sharpe_ratio": float(perf[2]),
                }
            except Exception:
                meta = {"method": "hrp"}
            return pd.Series(cleaned), meta
        except Exception as exc:
            logger.warning(f"HRP 优化失败，降级等权: {exc}")
            return self._equal_weight(returns.columns), {"method": "hrp_fallback", "error": str(exc)}

    def _optimize_cvar(
        self,
        returns: Optional[pd.DataFrame],
        constraints: Dict[str, float],
    ) -> Tuple[pd.Series, Dict[str, float]]:
        """CVaR 最小化优化 —— cvxpy 实现（Rockafellar-Uryasev 公式）"""
        if returns is None or returns.empty:
            cols = returns.columns if returns is not None else []
            return self._equal_weight(cols), {"method": "cvar_fallback", "note": "空收益"}

        assets = list(returns.columns)
        n = len(assets)
        if n == 0:
            return pd.Series(), {"method": "cvar", "note": "无资产"}

        max_weight = constraints.get("max_weight", MAX_SINGLE_STOCK_WEIGHT)
        min_weight = constraints.get("min_weight", MIN_WEIGHT)
        confidence = CVAR_CONFIDENCE

        if not HAS_CVXPY:
            logger.warning("cvxpy 未安装，CVaR 降级等权")
            return self._equal_weight(assets), {"method": "cvar_fallback", "note": "无 cvxpy"}

        try:
            R = returns[assets].values  # T x n
            T = R.shape[0]
            w = cp.Variable(n)
            alpha = cp.Variable()
            z = cp.Variable(T)

            cvar = alpha + cp.sum(z) / ((1 - confidence) * T)
            constraints_cvx = [
                z >= -R @ w - alpha,
                z >= 0,
                cp.sum(w) == 1,
                w >= min_weight,
                w <= max_weight,
            ]
            prob = cp.Problem(cp.Minimize(cvar), constraints_cvx)
            prob.solve()

            if w.value is None:
                logger.warning("CVaR 求解失败，降级等权")
                return self._equal_weight(assets), {"method": "cvar_fallback", "note": "求解失败"}

            weights = pd.Series(w.value, index=assets)
            weights = weights.clip(lower=0)
            weights = weights / weights.sum()

            portfolio_returns = returns[assets].values @ weights.values
            var = np.percentile(portfolio_returns, (1 - confidence) * 100)
            cvar_val = float(portfolio_returns[portfolio_returns <= var].mean())

            return weights, {
                "method": "cvar",
                "cvar": cvar_val,
                "var": float(var),
                "confidence": confidence,
            }
        except Exception as exc:
            logger.warning(f"CVaR 优化异常，降级等权: {exc}")
            return self._equal_weight(assets), {"method": "cvar_fallback", "error": str(exc)}

    def _optimize_with_turnover(
        self,
        expected_rets: pd.Series,
        cov_matrix: pd.DataFrame,
        current_weights: pd.Series,
        constraints: Dict[str, float],
    ) -> Tuple[pd.Series, Dict[str, float]]:
        """带换手率约束的最大夏普优化 —— 修复版（显式传入惩罚强度）"""
        max_weight = constraints.get("max_weight", MAX_SINGLE_STOCK_WEIGHT)
        max_turnover = constraints.get("max_turnover", MAX_TURNOVER)

        try:
            ef = EfficientFrontier(
                expected_rets, cov_matrix,
                weight_bounds=(0, max_weight),
            )
            cw = current_weights.reindex(expected_rets.index, fill_value=0.0).values
            turnover_penalty = 1.0
            if HAS_CVXPY:
                ef.add_objective(lambda w: turnover_penalty * cp.norm1(w - cw))

            weights = ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)
            cleaned = ef.clean_weights()
            w_series = pd.Series(cleaned)

            actual_turnover = float(
                (w_series - current_weights.reindex(w_series.index, fill_value=0.0)).abs().sum()
            )
            perf = ef.portfolio_performance(risk_free_rate=RISK_FREE_RATE)
            return w_series, {
                "method": "max_sharpe_with_turnover",
                "expected_return": float(perf[0]),
                "volatility": float(perf[1]),
                "sharpe_ratio": float(perf[2]),
                "actual_turnover": actual_turnover,
                "max_turnover_target": max_turnover,
                "turnover_constraint_met": actual_turnover <= max_turnover,
            }
        except Exception as exc:
            logger.warning(f"带换手率约束优化失败，降级等权: {exc}")
            return self._equal_weight(expected_rets.index), {"method": "fallback", "error": str(exc)}

    def _black_litterman(
        self,
        expected_rets: pd.Series,
        cov_matrix: pd.DataFrame,
        constraints: Dict[str, float],
    ) -> Tuple[pd.Series, Dict[str, float]]:
        """Black-Litterman 模型"""
        from pypfopt import black_litterman
        market_caps = pd.Series(1.0, index=expected_rets.index)
        bl = black_litterman.BlackLittermanModel(
            cov_matrix, pi="market", market_caps=market_caps,
            risk_aversion=2.5
        )
        rets_bl = bl.bl_returns()
        ef = EfficientFrontier(rets_bl, cov_matrix)
        weights = ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)
        cleaned = ef.clean_weights()
        return pd.Series(cleaned), {"method": "black_litterman"}

    @staticmethod
    def _equal_weight(index) -> pd.Series:
        n = len(index)
        if n == 0:
            return pd.Series()
        return pd.Series(1.0 / n, index=index)


class AShareConstraints:
    """A股组合约束管理"""

    def validate_constraints(
        self,
        weights: pd.Series,
        constraints: Dict[str, float],
    ) -> Dict[str, bool]:
        """验证组合是否满足所有约束"""
        result = {}
        max_w = constraints.get("max_weight", MAX_SINGLE_STOCK_WEIGHT)
        result["max_single_weight"] = all(weights <= max_w)
        result["weights_sum_one"] = abs(weights.sum() - 1.0) < 0.001
        return result


class RiskManager:
    """风险计算与止损管理"""

    def __init__(self):
        self._start_nav = 0.0

    def reset_daily(self, nav: float):
        """交易日开始时重置基准净值"""
        self._start_nav = nav

    def check_portfolio_stop(self, current_nav: float) -> Dict[str, Any]:
        """检查组合层面止损"""
        daily_return = (current_nav - self._start_nav) / self._start_nav if self._start_nav > 0 else 0
        triggered = daily_return <= -MAX_DAILY_LOSS_RATIO
        return {
            "triggered": triggered,
            "daily_return": float(daily_return),
            "threshold": MAX_DAILY_LOSS_RATIO,
            "reason": "单日亏损超过阈值" if triggered else "",
        }

    def check_individual_stop(
        self,
        current_prices: pd.Series,
        entry_prices: pd.Series,
    ) -> pd.Series:
        """检查个股止损信号"""
        returns = (current_prices - entry_prices) / entry_prices
        return returns <= -INDIVIDUAL_STOP_LOSS

    def calc_var(
        self,
        returns: pd.Series,
        confidence: float = VAR_CONFIDENCE
    ) -> float:
        """历史模拟法 VaR"""
        return float(np.percentile(returns, (1 - confidence) * 100))

    def calc_cvar(
        self,
        returns: pd.Series,
        confidence: float = CVAR_CONFIDENCE
    ) -> float:
        """CVaR (Expected Shortfall)"""
        var = np.percentile(returns, (1 - confidence) * 100)
        return float(returns[returns <= var].mean())

    def calc_portfolio_var_cvar(
        self,
        returns_df: pd.DataFrame,
        weights: pd.Series,
        confidence: float = VAR_CONFIDENCE
    ) -> Dict[str, float]:
        """计算组合的 VaR 和 CVaR"""
        aligned_weights = weights.reindex(returns_df.columns, fill_value=0)
        portfolio_returns = returns_df.dot(aligned_weights)
        return {
            "VaR": self.calc_var(portfolio_returns, confidence),
            "CVaR": self.calc_cvar(portfolio_returns, confidence),
            "confidence": confidence,
        }

    def generate_stop_loss_signals(
        self,
        data: pd.DataFrame,
        portfolio_weights: pd.Series,
        nav: float,
    ) -> Dict[str, Any]:
        """综合生成所有止损信号"""
        self.reset_daily(nav)
        stop_result = self.check_portfolio_stop(nav)

        individual_stops = pd.Series(False, index=portfolio_weights.index)
        if 'close' in data.columns:
            latest_prices = data.groupby('code')['close'].last()
            entry_prices = data.groupby('code')['close'].apply(lambda x: x.iloc[-20] if len(x) >= 20 else x.iloc[0])
            aligned_indices = portfolio_weights.index.intersection(latest_prices.index).intersection(entry_prices.index)
            if len(aligned_indices) > 0:
                individual_stops.loc[aligned_indices] = self.check_individual_stop(
                    latest_prices.loc[aligned_indices],
                    entry_prices.loc[aligned_indices]
                )

        return {
            "portfolio_stop": stop_result,
            "individual_stops": individual_stops.to_dict(),
            "any_triggered": stop_result["triggered"] or individual_stops.any(),
        }


def run(ctx) -> Dict[str, Any]:
    """
    portfolio-risk-engine 的 run 函数

    参数:
        ctx: Context 对象，需包含:
            - artifacts['DATA']: 行情数据路径
            - artifacts['FACTOR']: 因子数据路径

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "metadata": {...},
            "error": str
        }
    """
    try:
        os.makedirs(PORTFOLIO_DIR, exist_ok=True)

        data_path = ctx.get_artifact("DATA")
        if not data_path or not os.path.exists(data_path):
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "行情数据不存在"}

        data = pd.read_parquet(data_path)

        strategy_params = getattr(ctx, 'strategy_params', {}) or {}
        method = strategy_params.get("optimization_method", OPTIMIZATION_METHOD)

        pivot_close = data.pivot(index='date', columns='code', values='close')
        returns = pivot_close.pct_change().dropna()

        if returns.empty:
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "收益率数据为空"}

        optimizer = PortfolioOptimizer()

        alpha_available = False
        factor_path = ctx.get_artifact("FACTOR")
        if factor_path and os.path.exists(factor_path):
            factor_df = pd.read_parquet(factor_path)
            if 'alpha_score' in factor_df.columns:
                latest_alphas = factor_df[factor_df['date'] == factor_df['date'].max()]
                if not latest_alphas.empty:
                    expected_rets = latest_alphas.set_index('code')['alpha_score']
                    expected_rets = expected_rets.reindex(returns.columns).fillna(0)
                    alpha_available = True
                    logger.info("使用因子 Alpha 作为预期收益")

        if not alpha_available:
            expected_rets = optimizer.estimate_expected_returns(returns)

        cov_matrix = optimizer.estimate_covariance(returns)

        valid_codes = expected_rets.index.intersection(cov_matrix.index)
        expected_rets = expected_rets.loc[valid_codes]
        cov_matrix = cov_matrix.loc[valid_codes, valid_codes]

        constraints = {
            "max_weight": strategy_params.get("max_weight", MAX_SINGLE_STOCK_WEIGHT),
            "min_weight": strategy_params.get("min_weight", MIN_WEIGHT),
            "max_turnover": strategy_params.get("max_turnover", MAX_TURNOVER),
            "max_industry_deviation": strategy_params.get("max_industry_deviation", MAX_INDUSTRY_DEVIATION),
        }

        weights, metrics = optimizer.optimize(
            expected_rets, cov_matrix, method=method,
            constraints=constraints, returns=returns,
        )

        constraint_checker = AShareConstraints()
        constraint_result = constraint_checker.validate_constraints(weights, constraints)

        risk_mgr = RiskManager()
        var_cvar = {}
        if len(returns.columns) > 1:
            var_cvar = risk_mgr.calc_portfolio_var_cvar(returns, weights)

        nav = 1_000_000
        stop_signals = risk_mgr.generate_stop_loss_signals(data, weights, nav)

        weights_dict = {k: float(v) for k, v in weights.items() if v > 0.0001}
        weights_path = os.path.join(PORTFOLIO_DIR, "portfolio_weights.json")
        with open(weights_path, 'w', encoding='utf-8') as f:
            json.dump(weights_dict, f, ensure_ascii=False, indent=2)

        return {
            "success": True,
            "artifact_path": weights_path,
            "metadata": {
                "weights": weights_dict,
                "metrics": metrics,
                "var_cvar": var_cvar,
                "stop_signals": stop_signals,
                "constraint_check": constraint_result,
                "optimization_method": method,
                "num_assets": len(weights_dict),
            },
            "error": ""
        }

    except Exception as e:
        logger.exception("组合优化引擎执行失败")
        return {"success": False, "artifact_path": "", "metadata": {}, "error": str(e)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            ctx_dict = json.load(f)
        from scripts.context import Context
        ctx = Context.from_dict(ctx_dict)
    else:
        from scripts.context import Context
        ctx = Context(
            task_id="test_portfolio",
            stock_pool=[],
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        ctx.update_artifact("DATA", "./workspace/data/cleaned_data.parquet")
        ctx.update_artifact("FACTOR", "./workspace/factors/factor_data.parquet")

    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# 优化模块入口（已整合到 scripts/optimizations/）
# 使用方式: from engine import optimizations
# ---------------------------------------------------------------------------
from scripts.optimizations.circuit_breaker_v2 import (
    CircuitBreakerV2 as _CircuitBreakerV2,
)
from scripts.optimizations.risk_engine import (
    RiskEngine as _RiskEngine,
)


class optimizations:
    """风控引擎优化模块集合"""
    CircuitBreakerV2 = _CircuitBreakerV2
    RiskEngine = _RiskEngine
