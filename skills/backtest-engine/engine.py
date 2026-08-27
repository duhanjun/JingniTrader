"""
回测引擎主逻辑
统一接口，调度原生后端，计算绩效，生成报告
"""
import os
import sys
import json
import logging
from typing import Dict, Any
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

from scripts.config import (
    BACKTEST_DIR, INIT_CAPITAL,
    COMMISSION_RATE, MIN_COMMISSION, STAMP_TAX_RATE,
    TRANSFER_FEE_RATE, SLIPPAGE, BENCHMARK, RISK_FREE_RATE
)

logger = logging.getLogger("backtest-engine")

# 说明：策略回测的 HTML 绩效报告由 reports-engine 统一生成（report.html），
# 本引擎不再生成 quantstats 第三方样式的 backtest_report_*.html。


class BacktestEngine:
    """统一回测引擎"""

    def __init__(self):
        from scripts.adapters.native_adapter import NativeAdapter
        self.adapter = NativeAdapter()

    def run(
        self,
        data: pd.DataFrame,
        signals: pd.DataFrame,
        init_capital: float = INIT_CAPITAL,
        benchmark: str = BENCHMARK,
        commission_rate: float = COMMISSION_RATE,
        stamp_tax_rate: float = STAMP_TAX_RATE,
        slippage: float = SLIPPAGE,
        t_plus_1: bool = True,
        price_limit: bool = True,
    ) -> Dict[str, Any]:
        """执行回测"""
        logger.info("开始回测，后端: native")
        return self.adapter.run_backtest(
            data=data,
            signals=signals,
            init_capital=init_capital,
            benchmark=benchmark,
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            t_plus_1=t_plus_1,
            price_limit=price_limit,
            slippage=slippage,
            transfer_fee_rate=TRANSFER_FEE_RATE,
            min_commission=MIN_COMMISSION,
        )

def run(ctx) -> Dict[str, Any]:
    """
    backtest-engine 的 run 函数

    参数:
        ctx: Context 对象，需包含:
            - artifacts['DATA']: 清洗后数据路径
            - artifacts['MODEL'] 或 artifacts['FACTOR']

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "report_path": str,
            "metadata": {...},
            "error": str
        }
    """
    try:
        data_path = ctx.get_artifact("DATA")
        if not data_path or not os.path.exists(data_path):
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "数据产物不存在"}
        data = pd.read_parquet(data_path)

        signal_path = ctx.get_artifact("MODEL")
        if not signal_path or not os.path.exists(signal_path):
            factor_path = ctx.get_artifact("FACTOR")
            if factor_path and os.path.exists(factor_path):
                factor_df = pd.read_parquet(factor_path)
                if 'alpha_score' in factor_df.columns:
                    factor_df['rank'] = factor_df.groupby('date')['alpha_score'].rank(pct=True)
                    signals = factor_df[['code', 'date']].copy()
                    signals['signal'] = 0
                    signals.loc[factor_df['rank'] > 0.8, 'signal'] = 1
                else:
                    return {"success": False, "artifact_path": "", "metadata": {}, "error": "无有效信号"}
            else:
                return {"success": False, "artifact_path": "", "metadata": {}, "error": "无信号数据"}
        else:
            if signal_path.endswith('.parquet'):
                signals = pd.read_parquet(signal_path)
            else:
                import joblib
                model = joblib.load(signal_path)
                factor_path = ctx.get_artifact("FACTOR")
                if factor_path:
                    factor_df = pd.read_parquet(factor_path)
                    # 优先使用 MODEL 阶段训练时的特征列顺序（ctx.metadata["MODEL"]["feature_cols"]）。
                    # 训练/预测特征顺序必须完全一致，否则 sklearn 等模型会因 feature_names 不匹配
                    # 抛 "The feature names should match those that were passed during fit"。
                    feature_cols = None
                    try:
                        trained_cols = ctx.metadata.get("MODEL", {}).get("feature_cols")
                        if isinstance(trained_cols, list) and trained_cols:
                            # 只取 factor_df 中实际存在的列，保持训练顺序；缺失列则回退到本地推导
                            missing = [c for c in trained_cols if c not in factor_df.columns]
                            if not missing:
                                feature_cols = trained_cols
                    except Exception:
                        feature_cols = None
                    if not feature_cols:
                        # 回退：按因子列序推导（训练时 alpha_score 置尾）
                        feature_cols = [c for c in factor_df.columns
                                        if c not in ['code', 'date', 'industry', 'alpha_score']]
                        feature_cols = [c for c in feature_cols if not factor_df[c].isna().all()]
                        if 'alpha_score' in factor_df.columns:
                            feature_cols.append('alpha_score')
                    X = factor_df[feature_cols].fillna(0)
                    preds = model.predict(X)
                    signals = factor_df[['code', 'date']].copy()
                    signals['signal'] = 0
                    signals.loc[preds > np.percentile(preds, 80), 'signal'] = 1
                else:
                    return {"success": False, "artifact_path": "", "metadata": {}, "error": "无法生成信号"}

        if signals.empty:
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "信号为空"}

        os.makedirs(BACKTEST_DIR, exist_ok=True)
        engine = BacktestEngine()
        result = engine.run(data=data, signals=signals)

        result_json = {
            "metrics": result['metrics'],
            "backend": "native",
            "timestamp": datetime.now().isoformat(),
        }
        json_path = os.path.join(BACKTEST_DIR, "backtest_result.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result_json, f, ensure_ascii=False, indent=2, default=str)

        # 标准回测绩效 HTML 报告（report.html）由 reports-engine 统一生成，
        # 本引擎不再生成 quantstats 第三方样式的 backtest_report_*.html。
        report_path = ""

        equity_path = os.path.join(BACKTEST_DIR, "equity_curve.parquet")
        if 'equity_curve' in result and not result['equity_curve'].empty:
            result['equity_curve'].to_parquet(equity_path)

        # ============================================================
        # M2: 因子归因数据（portfolio_weights + factor_exposures）
        # ============================================================
        pw_path = os.path.join(BACKTEST_DIR, "portfolio_weights.parquet")
        if 'portfolio_weights' in result and not result['portfolio_weights'].empty:
            result['portfolio_weights'].to_parquet(pw_path)

        factor_exposure_path = ""
        try:
            fpath = ctx.get_artifact("FACTOR")
            pw = result.get("portfolio_weights", pd.DataFrame())
            if fpath and os.path.exists(fpath) and not pw.empty:
                fdf = pd.read_parquet(fpath)
                # 归因用风格列（M1 在 factor_data 中补齐）与 alpha_score
                expose_cols = [
                    c for c in ["size", "value", "momentum", "volatility", "quality", "growth", "alpha_score"]
                    if c in fdf.columns
                ]
                if expose_cols:
                    pw_f = pw.merge(
                        fdf[["code", "date"] + expose_cols], on=["code", "date"], how="left"
                    )
                    # 组合逐日暴露 = Σ(权重 × 因子值)，按日期聚合（矢量化，避免 apply 索引问题）
                    for col in expose_cols:
                        pw_f[col] = pd.to_numeric(pw_f[col], errors="coerce")
                    pw_f["_weight"] = pd.to_numeric(pw_f["weight"], errors="coerce").fillna(0.0)
                    weighted = pw_f[expose_cols].multiply(pw_f["_weight"], axis=0)
                    weighted["date"] = pw_f["date"]
                    expose = weighted.groupby("date", as_index=False)[expose_cols].sum()
                    factor_exposure_path = os.path.join(BACKTEST_DIR, "factor_exposures.parquet")
                    expose.to_parquet(factor_exposure_path, index=False)
                    logger.info("M2 因子暴露已生成: %s", factor_exposure_path)
        except Exception as exp_e:
            logger.warning("M2 因子暴露计算失败（不阻塞）: %s", exp_e)

        # ── M4: 把策略因子清单复制到 BACKTEST_DIR，供回测报告交叉引用 ──
        strategy_factors_path = ""
        try:
            _work = os.environ.get("QUANT_WORK_DIR", "./workspace")
            _sf = os.path.join(_work, "models", "strategy_factors.json")
            if os.path.exists(_sf):
                _dst = os.path.join(BACKTEST_DIR, "strategy_factors.json")
                with open(_sf, "r", encoding="utf-8") as _fin, open(_dst, "w", encoding="utf-8") as _fout:
                    _fout.write(_fin.read())
                strategy_factors_path = _dst
                logger.info("M4: strategy_factors.json 已复制到 %s", _dst)
        except Exception as _e:
            logger.warning("M4: 复制 strategy_factors.json 失败: %s", _e)

        # ============================================================
        # P0-3 RuleJudge 五硬门评审（PRD P0-3.5）
        # ============================================================
        # 计算完成交易笔数：每次 signal 从 0→1 或 1→0 算一笔
        trade_count = 0
        try:
            if not signals.empty and "signal" in signals.columns:
                sig_sorted = signals.sort_values(["code", "date"])
                sig_diff = sig_sorted.groupby("code")["signal"].diff().abs()
                trade_count = int((sig_diff > 0).sum())
        except Exception as tc_e:
            logger.warning(f"trade_count 计算异常（默认 0）: {tc_e}")

        verdict_dict = {}
        try:
            from scripts.rule_judge import RuleJudge
            judge = RuleJudge()
            equity_curve_for_judge = result.get("equity_curve", pd.DataFrame())
            verdict = judge.judge(
                metrics=result["metrics"],
                equity_curve=equity_curve_for_judge,
                trade_count=trade_count,
            )
            verdict_dict = verdict.to_dict()
            if verdict.recommended_state == "rejected":
                logger.warning(
                    f"P0-3 策略未通过 RuleJudge 评审: failed_gates={verdict.failed_gates}"
                )
            else:
                logger.info(
                    f"P0-3 策略通过 RuleJudge 评审: passed_gates={verdict.passed_gates}"
                )
        except Exception as rj_e:
            logger.warning(f"P0-3 RuleJudge 评审异常（不阻断流程）: {rj_e}")

        return {
            "success": True,
            "artifact_path": json_path,
            "report_path": report_path,
            "metadata": {
                "metrics": result['metrics'],
                "backend": "native",
                "equity_curve_path": equity_path,
                "portfolio_weights_path": pw_path,      # M2
                "factor_exposure_path": factor_exposure_path,  # M2
                "strategy_factors_path": strategy_factors_path,  # M4
                # P0-3.5 新增：评审结果写入 result["verdict"]
                "verdict": verdict_dict,
                "trade_count": trade_count,
            },
            "error": ""
        }

    except Exception as e:
        logger.exception("回测引擎执行失败")
        return {
            "success": False,
            "artifact_path": "",
            "metadata": {},
            "error": str(e)
        }


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
            task_id="test_bt",
            stock_pool=[],
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        ctx.update_artifact("DATA", "./workspace/data/cleaned_data.parquet")
        ctx.update_artifact("FACTOR", "./workspace/factors/factor_data.parquet")

    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
