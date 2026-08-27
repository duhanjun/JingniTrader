"""
策略模型引擎主逻辑
负责数据准备、模型训练、超参优化、实验管理
"""
import os
import sys
import json
import logging
import warnings
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

try:
    from catboost import CatBoostRegressor, CatBoostClassifier
    HAS_CATBOOST = True
except ImportError:
    HAS_CATBOOST = False

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

try:
    import mlflow
    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False

from scripts.config import (
    MODEL_TYPE, MODEL_DIR,
    OPTUNA_TRIALS, OPTUNA_TIMEOUT,
    TRAIN_WINDOW_MONTHS, VALIDATION_WINDOW_MONTHS, TEST_WINDOW_MONTHS,
    PURGE_GAP_DAYS, FORWARD_PERIOD, LABEL_TYPE,
    MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT_NAME
)

warnings.filterwarnings('ignore')
logger = logging.getLogger("strategy-model-engine")


# ---------------------------------------------------------------------------
# 跨 skill 复用 factor-engine 的截面 IC 计算（统一口径，避免重复实现）
# ---------------------------------------------------------------------------
# factor-engine/scripts/optimizations/ic_vectorized.py 已实现向量化逐日截面
# IC（ic_series_pearson / ic_series_spearman），本引擎训练评估阶段的 IC 计算
# 改为复用之，消除与 factor-engine 的重复实现。
#
# 加载方式：以独立包名 fe_optimizations 加载 factor-engine 的 optimizations 包
# 及其 ic_vectorized 子模块，使模块内 `from . import resolve_backend` 相对导入
# 能正确解析（避免 spec_from_file_location 单文件加载导致的 "no known parent
# package" 错误）。加载失败时降级为全局 corr（与原实现一致）。
_FACTOR_IC_CACHE: Dict[str, Any] = {"func": None, "loaded": False}


def _load_factor_ic():
    """加载 factor-engine 的 ic_series_pearson（带缓存，失败返回 None）。"""
    if _FACTOR_IC_CACHE["loaded"]:
        return _FACTOR_IC_CACHE["func"]
    _FACTOR_IC_CACHE["loaded"] = True
    try:
        import importlib.util as _ilu
        import sys as _sys

        _fe_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        _opt_dir = os.path.join(
            _fe_root, "skills", "factor-engine", "scripts", "optimizations"
        )
        _init_path = os.path.join(_opt_dir, "__init__.py")
        _ic_path = os.path.join(_opt_dir, "ic_vectorized.py")
        if not os.path.exists(_ic_path):
            logger.warning("factor-engine ic_vectorized.py 不存在，IC 降级全局 corr")
            return None

        # 以 fe_optimizations 包名加载，保证 ic_vectorized 内相对导入可解析
        _init_spec = _ilu.spec_from_file_location(
            "fe_optimizations", _init_path,
            submodule_search_locations=[_opt_dir],
        )
        _opt_pkg = _ilu.module_from_spec(_init_spec)
        _sys.modules["fe_optimizations"] = _opt_pkg
        _init_spec.loader.exec_module(_opt_pkg)

        _ic_spec = _ilu.spec_from_file_location(
            "fe_optimizations.ic_vectorized", _ic_path
        )
        _ic_mod = _ilu.module_from_spec(_ic_spec)
        _sys.modules["fe_optimizations.ic_vectorized"] = _ic_mod
        _ic_spec.loader.exec_module(_ic_mod)

        _FACTOR_IC_CACHE["func"] = getattr(_ic_mod, "ic_series_pearson", None)
    except Exception as _e:  # pragma: no cover - 加载失败兜底
        logger.warning("加载 factor-engine ic_vectorized 失败，IC 降级全局 corr: %s", _e)
    return _FACTOR_IC_CACHE["func"]


class _NullContext:
    """MLflow 不可用时的空上下文管理器"""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class ModelEngine:
    """策略模型引擎"""

    def __init__(self):
        if HAS_MLFLOW:
            mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
            mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    def prepare_data(
        self,
        factor_df: pd.DataFrame,
        price_df: pd.DataFrame,
        feature_cols: List[str],
        label_col: str = 'forward_return'
    ) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
        """准备训练数据"""
        logger.info("准备训练数据...")

        price_df = price_df.sort_values(['code', 'date'])
        price_df['forward_return'] = price_df.groupby('code')['close'].transform(
            lambda x: x.shift(-FORWARD_PERIOD) / x - 1
        )

        data = factor_df[['code', 'date'] + feature_cols].merge(
            price_df[['code', 'date', 'forward_return']],
            on=['code', 'date'],
            how='inner'
        )

        if LABEL_TYPE == 'classification':
            threshold = data.groupby('date')['forward_return'].transform('median')
            data['label'] = (data['forward_return'] > threshold).astype(int)
            y_col = 'label'
        else:
            y_col = 'forward_return'

        data = data.dropna(subset=feature_cols + [y_col])

        X = data[feature_cols]
        y = data[y_col]
        dates = data['date']

        logger.info(f"数据准备完成: {len(X)} 样本, {len(feature_cols)} 特征")
        return X, y, dates

    def purged_group_ts_split(
        self,
        dates: pd.Series,
        n_splits: int = 5
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Purged Group Time Series Split"""
        unique_dates = sorted(dates.unique())
        n_dates = len(unique_dates)

        splits = []
        test_size = n_dates // (n_splits + 1)

        for i in range(n_splits):
            train_end_idx = n_dates - (n_splits - i) * test_size
            val_start_idx = train_end_idx + 1
            val_end_idx = min(val_start_idx + test_size, n_dates)

            if val_start_idx >= n_dates:
                break

            train_dates = unique_dates[:train_end_idx]
            val_dates = unique_dates[val_start_idx:val_end_idx]

            if PURGE_GAP_DAYS > 0:
                purge_date = unique_dates[train_end_idx] - timedelta(days=PURGE_GAP_DAYS)
                train_dates = [d for d in train_dates if d <= purge_date]

            train_idx = dates[dates.isin(train_dates)].index.values
            val_idx = dates[dates.isin(val_dates)].index.values

            if len(train_idx) > 0 and len(val_idx) > 0:
                splits.append((train_idx, val_idx))

        logger.info(f"生成 {len(splits)} 个交叉验证分割")
        return splits

    def _compute_ic(
        self,
        pred_series: pd.Series,
        y_series: pd.Series,
        dates_series: Optional[pd.Series] = None,
    ) -> float:
        """计算模型预测 IC（信息系数），统一复用 factor-engine 截面 IC。

        语义（重要）：
        - 若传入真实截面日期 ``dates_series``（与 pred_series 同索引对齐），
          则按日期分组计算逐日截面 Pearson IC，取均值作为标量 IC。这是量化
          标准 IC 口径（避免跨时序混算），比全局 corr 更正确。
        - 若未传入 dates_series（如 run() 无 test_dates 的默认路径），则降级
          为全样本全局 Pearson 相关，与原实现 ``pred_series.corr(y_series)``
          数值一致，保证既有输出语义不变。
        - factor-engine 模块不可用时同样降级全局 corr。
        """
        if dates_series is not None and len(dates_series) == len(pred_series):
            try:
                ic_func = _load_factor_ic()
                if ic_func is not None:
                    ic_series = ic_func(
                        pred_series, y_series, dates=dates_series, min_obs=1
                    )
                    if ic_series is not None and len(ic_series) > 0:
                        return float(ic_series.mean())
            except Exception as _e:  # pragma: no cover - 计算兜底
                logger.warning("截面 IC 计算失败，降级全局 corr: %s", _e)

        # 降级：全局 Pearson 相关（与原实现一致）
        return float(pred_series.corr(y_series))

    def create_model(self, trial: Optional['optuna.Trial'] = None) -> Any:
        """根据配置创建模型

        首选 ML 库（lightgbm/catboost）未安装时自动降级到 sklearn 的
        random_forest（不抛错），保证 MODEL 阶段在缺依赖环境下稳定运行。
        """
        model_type = MODEL_TYPE
        if model_type == 'lightgbm' and not HAS_LGB:
            logger.warning("LightGBM 未安装，自动降级为 random_forest")
            model_type = 'random_forest'
        elif model_type == 'catboost' and not HAS_CATBOOST:
            logger.warning("CatBoost 未安装，自动降级为 random_forest")
            model_type = 'random_forest'

        if model_type == 'lightgbm':
            if trial is not None and HAS_OPTUNA:
                params = {
                    'n_estimators': trial.suggest_int('n_estimators', 50, 500),
                    'max_depth': trial.suggest_int('max_depth', 3, 15),
                    'num_leaves': trial.suggest_int('num_leaves', 20, 300),
                    'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
                    'subsample': trial.suggest_float('subsample', 0.5, 1.0),
                    'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
                    'random_state': 42,
                    'n_jobs': -1,
                    'verbosity': -1,
                }
            else:
                params = {'random_state': 42, 'n_jobs': -1, 'verbosity': -1}

            if LABEL_TYPE == 'classification':
                return lgb.LGBMClassifier(**params)
            else:
                return lgb.LGBMRegressor(**params)

        elif model_type == 'catboost':
            if trial is not None and HAS_OPTUNA:
                params = {
                    'iterations': trial.suggest_int('iterations', 50, 500),
                    'depth': trial.suggest_int('depth', 3, 12),
                    'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
                    'random_seed': 42,
                    'verbose': False,
                }
            else:
                params = {'random_seed': 42, 'verbose': False}

            if LABEL_TYPE == 'classification':
                return CatBoostClassifier(**params)
            else:
                return CatBoostRegressor(**params)

        elif model_type == 'logistic_regression':
            if LABEL_TYPE != 'classification':
                raise ValueError(
                    "logistic_regression 仅支持分类任务，请设置 LABEL_TYPE=classification"
                )
            if trial is not None and HAS_OPTUNA:
                params = {
                    'C': trial.suggest_float('C', 0.01, 10.0, log=True),
                    'penalty': trial.suggest_categorical('penalty', ['l1', 'l2']),
                    'solver': 'saga',
                    'max_iter': 1000,
                    'random_state': 42,
                }
            else:
                params = {'max_iter': 1000, 'random_state': 42}
            return LogisticRegression(**params)

        elif model_type == 'random_forest':
            if trial is not None and HAS_OPTUNA:
                params = {
                    'n_estimators': trial.suggest_int('n_estimators', 50, 500),
                    'max_depth': trial.suggest_int('max_depth', 3, 20),
                    'min_samples_split': trial.suggest_int('min_samples_split', 2, 20),
                    'min_samples_leaf': trial.suggest_int('min_samples_leaf', 1, 10),
                    'random_state': 42,
                    'n_jobs': -1,
                }
            else:
                params = {'random_state': 42, 'n_jobs': -1}
            if LABEL_TYPE == 'classification':
                return RandomForestClassifier(**params)
            else:
                return RandomForestRegressor(**params)
        else:
            raise ValueError(f"不支持的模型类型: {MODEL_TYPE}")

    def optimize_hyperparams(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        dates: pd.Series,
        n_trials: int = OPTUNA_TRIALS
    ) -> Dict[str, Any]:
        """使用 Optuna 进行超参数优化"""
        if not HAS_OPTUNA:
            logger.warning("Optuna 未安装，跳过超参数优化")
            return {}

        logger.info(f"开始超参数优化 (模型: {MODEL_TYPE}, trials: {n_trials})")

        splits = self.purged_group_ts_split(dates, n_splits=3)
        if not splits:
            logger.warning("无法生成交叉验证分割，使用默认参数")
            return {}

        def objective(trial):
            model = self.create_model(trial)
            scores = []

            for train_idx, val_idx in splits:
                X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
                y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

                model.fit(X_train, y_train)

                if LABEL_TYPE == 'classification':
                    score = model.score(X_val, y_val)
                else:
                    from sklearn.metrics import mean_squared_error
                    pred = model.predict(X_val)
                    score = -mean_squared_error(y_val, pred)

                scores.append(score)

            return np.mean(scores)

        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
        )
        study.optimize(objective, n_trials=n_trials, timeout=OPTUNA_TIMEOUT)

        logger.info(f"超参数优化完成，最佳分数: {study.best_value}")
        return study.best_params

    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        best_params: Dict[str, Any] = None,
        test_dates: pd.Series = None
    ) -> Tuple[Any, Dict[str, float], Optional[np.ndarray], Optional[str]]:
        """训练模型并评估

        返回: (model, metrics, predictions, model_path)
        model_path 为模型文件落盘路径（joblib 不可用时为 None）
        """
        logger.info("训练最终模型...")

        # 整个训练+评估+记录流程包裹在单个 MLflow run 上下文中
        mlflow_ctx = mlflow.start_run() if HAS_MLFLOW else _NullContext()

        with mlflow_ctx:
            if HAS_MLFLOW:
                mlflow.log_params({
                    "model_type": MODEL_TYPE,
                    "label_type": LABEL_TYPE,
                    "forward_period": FORWARD_PERIOD,
                    "features": ", ".join(X.columns.tolist()),
                })
                if best_params:
                    mlflow.log_params(best_params)

            model = self.create_model()
            if best_params:
                model.set_params(**best_params)

            if test_dates is not None and len(test_dates) > 0:
                train_mask = ~X.index.isin(test_dates.index)
                X_train = X.loc[train_mask]
                y_train = y.loc[train_mask]
                X_test = X.loc[~train_mask]
                y_test = y.loc[~train_mask]
            else:
                X_train, y_train = X, y
                X_test, y_test = None, None

            model.fit(X_train, y_train)

            metrics = {}
            predictions = None

            if X_test is not None:
                predictions = model.predict(X_test)

                if LABEL_TYPE == 'classification':
                    from sklearn.metrics import accuracy_score, f1_score
                    metrics['accuracy'] = accuracy_score(y_test, predictions)
                    metrics['f1'] = f1_score(y_test, predictions, average='weighted')
                else:
                    from sklearn.metrics import mean_squared_error, r2_score
                    metrics['mse'] = mean_squared_error(y_test, predictions)
                    metrics['rmse'] = np.sqrt(metrics['mse'])
                    metrics['r2'] = r2_score(y_test, predictions)

                pred_series = pd.Series(predictions, index=X_test.index)
                y_test_aligned = y_test.loc[X_test.index]
                # 截面 IC 需要真实日期；test_dates（测试行索引）按 X_test 对齐。
                # test_dates 为 None（run() 默认路径）或仅含测试行时，reindex 后
                # 与 X_test.index 同序；若无真实日期则降级全局 corr（语义不变）。
                ic_dates = None
                if test_dates is not None:
                    try:
                        ic_dates = test_dates.reindex(X_test.index)
                        if ic_dates.isna().all():
                            ic_dates = None
                    except Exception:
                        ic_dates = None
                metrics['ic'] = self._compute_ic(pred_series, y_test_aligned, ic_dates)
            else:
                if hasattr(model, 'score'):
                    metrics['train_score'] = model.score(X_train, y_train)

            if HAS_MLFLOW:
                mlflow.log_metrics(metrics)

                if hasattr(model, 'feature_importances_'):
                    importance_dict = dict(zip(X.columns, model.feature_importances_))
                    mlflow.log_dict(importance_dict, "feature_importance.json")

            # 模型落盘（单次保存）
            model_path = None
            os.makedirs(MODEL_DIR, exist_ok=True)
            model_path = os.path.join(MODEL_DIR, f"model_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl")
            if HAS_JOBLIB:
                joblib.dump(model, model_path)
            if HAS_MLFLOW and HAS_JOBLIB:
                mlflow.log_artifact(model_path)

        logger.info(f"模型训练完成，指标: {metrics}")
        return model, metrics, predictions, model_path

def run(ctx) -> Dict[str, Any]:
    """
    strategy-model-engine 的 run 函数

    参数:
        ctx: Context 对象，需包含:
            - artifacts['DATA']: 清洗后数据路径
            - artifacts['FACTOR']: 因子数据路径

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "predictions_path": str,
            "metadata": {...},
            "error": str
        }
    """
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)

        factor_path = ctx.get_artifact("FACTOR")
        if not factor_path or not os.path.exists(factor_path):
            return {
                "success": False,
                "artifact_path": "",
                "metadata": {},
                "error": "因子产物不存在，请先运行 a-share-factor-engine"
            }

        existing = ctx.get_artifact("MODEL")
        if existing and os.path.exists(existing):
            return {
                "success": True,
                "artifact_path": existing,
                "metadata": {"source": "cache"},
                "error": ""
            }

        engine = ModelEngine()

        factor_df = pd.read_parquet(factor_path)

        data_path = ctx.get_artifact("DATA")
        if not data_path or not os.path.exists(data_path):
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "行情数据不存在"}

        price_df = pd.read_parquet(data_path)

        feature_cols = [c for c in factor_df.columns
                      if c not in ['code', 'date', 'industry', 'alpha_score']]
        if 'alpha_score' in factor_df.columns:
            feature_cols.append('alpha_score')
        # 过滤掉全NaN的列
        feature_cols = [c for c in feature_cols if not factor_df[c].isna().all()]

        # ── M4: 因子→策略闭环 ──
        # 可选开关 QUANT_USE_ALPHALENS_VALID=1：仅使用 alphalens verdict 为 ACCEPT/REVIEW 的因子
        # 并输出 strategy_factors.json（策略因子清单 + alphalens 有效性），供回测报告交叉引用。
        _alphalens_valid = os.environ.get("QUANT_USE_ALPHALENS_VALID", "0") == "1"
        _factor_verdict: Dict[str, str] = {}
        if _alphalens_valid:
            _work = os.environ.get("QUANT_WORK_DIR", "./workspace")
            _tid = getattr(ctx, "task_id", "") or "default"
            _a_dir = os.path.join(_work, "reports", "alphalens", _tid)
            _m = os.path.join(_a_dir, "metrics.json")
            if os.path.exists(_m):
                try:
                    with open(_m, "r", encoding="utf-8") as _f:
                        _mm = json.load(_f)
                    _factor_verdict = {
                        x.get("factor"): x.get("suggested_verdict", "REVIEW")
                        for x in _mm if isinstance(x, dict)
                    }
                except Exception as _e:
                    logger.warning("读取 alphalens verdict 失败: %s", _e)
            else:
                # 兼容旧的按因子拆分的 *_metrics.json
                import glob as _glob
                for _mf in sorted(_glob.glob(os.path.join(_a_dir, "*_metrics.json"))):
                    try:
                        with open(_mf, "r", encoding="utf-8") as _f:
                            _d = json.load(_f)
                        if isinstance(_d, dict) and _d.get("factor"):
                            _factor_verdict[_d["factor"]] = _d.get("suggested_verdict", "REVIEW")
                    except Exception:
                        pass
            _keep = [f for f in feature_cols if _factor_verdict.get(f, "REVIEW") != "REJECT"]
            if _keep != feature_cols:
                logger.info("M4: 按 alphalens verdict 过滤因子 %d -> %d", len(feature_cols), len(_keep))
                feature_cols = _keep

        # 写出策略因子清单（供回测报告"因子清单"章节交叉引用）
        _strategy_factors = {
            "factors": [
                {"name": f, "verdict": _factor_verdict.get(f, "NA")} for f in feature_cols
            ],
            "alphalens_filter": _alphalens_valid,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            _sf_path = os.path.join(MODEL_DIR, "strategy_factors.json")
            with open(_sf_path, "w", encoding="utf-8") as _f:
                json.dump(_strategy_factors, _f, ensure_ascii=False, indent=2)
            logger.info("M4: 策略因子清单已生成: %s (%d 个因子)", _sf_path, len(feature_cols))
        except Exception as _e:
            logger.warning("M4: strategy_factors.json 写出失败: %s", _e)
            _sf_path = ""

        X, y, dates = engine.prepare_data(factor_df, price_df, feature_cols)

        best_params = engine.optimize_hyperparams(X, y, dates)

        # train() 内部完成模型落盘，run() 不再重复保存
        model, metrics, predictions, model_path = engine.train(X, y, best_params)

        if predictions is not None:
            signal_df = factor_df[['code', 'date']].copy()
            signal_df['pred'] = predictions
            signal_path = os.path.join(MODEL_DIR, "predictions.parquet")
            signal_df.to_parquet(signal_path, index=False)
        else:
            signal_path = ""

        return {
            "success": True,
            "artifact_path": model_path or "",
            "predictions_path": signal_path,
            "metadata": {
                "model_type": MODEL_TYPE,
                "metrics": metrics,
                "feature_cols": feature_cols,
                "strategy_factors_path": _sf_path,  # M4
            },
            "error": ""
        }

    except Exception as e:
        logger.exception("模型引擎执行失败")
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
            task_id="test_model",
            stock_pool=[],
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        ctx.update_artifact("DATA", "./workspace/data/cleaned_data.parquet")
        ctx.update_artifact("FACTOR", "./workspace/factors/factor_data.parquet")

    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
