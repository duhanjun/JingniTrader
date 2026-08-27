"""
Alphalens 数据格式适配器与报告生成
====================================

将 jingni-trader 内部数据结构适配为 alphalens（alphalens-reloaded）
期望的 MultiIndex Series + price pivot，并生成完整的因子分析报告。

设计要点
--------
- 默认开关由环境变量 ``QUANT_ALPHALENS_REPORT`` 控制（0/1，默认 0）
- alphalens-reloaded 缺失时 try/except 静默跳过，主流程不阻塞
- matplotlib 使用 Agg 无头模式，避免 Windows 显示问题
- 每个因子输出 4 PNG + 1 HTML + 1 JSON（8 必填字段）
- 若 alphalens-reloaded 不可用，自动降级到方案 C（自研轻量分层回测）

PRD：docs/01-archive/prd/factor-alphalens-integration.md（历史归档，实现口径以本文件与 config_guide.md 为准）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("alphalens_adapter")

# Windows 多进程 spawn 兼容：spawn 子进程重建解释器时不继承父进程动态注入的
# sys.path，导致子进程反序列化 worker（来自 scripts.alphalens_adapter 模块）时
# 抛 ModuleNotFoundError: No module named 'scripts.alphalens_adapter'。
# 模块顶层即把本脚本所在目录（scripts 包的父目录）注入 sys.path，使无论是主进程
# 还是 spawn 子进程 import scripts.* 均可成功。
_FACTOR_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _FACTOR_ENGINE_DIR not in sys.path:
    sys.path.insert(0, _FACTOR_ENGINE_DIR)

# 单因子完整报告（alphalens 原生 tear sheet 计算）的超时护栏（秒）。
# alphalens-reloaded 在极端/合成数据上可能长时间不收敛甚至挂起，
# 超时后降级到方案 C，避免阻塞整个 FACTOR→REPORT 流程。
ALPHALENS_FULL_REPORT_TIMEOUT = float(os.environ.get("ALPHALENS_REPORT_TIMEOUT", "60"))


def _mp_init_path():
    """spawn 子进程 initializer：将 factor-engine 目录注入 sys.path。

    Windows 多进程 spawn 重建解释器时不继承父进程动态注入的 sys.path，子进程反序列化
    worker（来自 scripts.alphalens_adapter）时抛 ModuleNotFoundError。initializer 在子进程
    启动早期注入 path，确保 import scripts.* 成功。
    """
    if _FACTOR_ENGINE_DIR not in sys.path:
        sys.path.insert(0, _FACTOR_ENGINE_DIR)


def _alphalens_full_worker(factor_df, price_df, output_dir, factor_name):
    """子进程 worker：执行 alphalens 数据格式转换 + 完整报告生成。

    结果通过文件落地（metrics.json / report.html），主进程超时后读文件判定，
    避免 Windows 下 multiprocessing.Queue/Pipe 在子进程被 kill 后的死锁挂起。
    """
    factor_data = AlphalensAdapter.to_alphalens_format(factor_df, price_df, factor_name)
    return AlphalensAdapter.generate_full_report(factor_data, output_dir, factor_name)


def _run_alphalens_full_with_timeout(factor_df, price_df, output_dir, factor_name):
    """带超时的 alphalens 原生全链路（格式转换 + 报告生成）。

    使用 spawn 子进程（initializer 注入 path），超时（ALPHALENS_FULL_REPORT_TIMEOUT）
    后 terminate 子进程并降级方案 C。结果判定基于子进程落地的 metrics.json 文件是否存在，
    而非 Queue 回收，彻底避免 Windows 下 Queue 死锁导致主进程挂起。
    返回路径字典；超时或失败时返回 None（调用方降级方案 C）。
    """
    import multiprocessing as mp

    # Windows spawn 子进程继承 PYTHONPATH 但不继承父进程运行期动态插入的 sys.path；
    # 双保险：显式把 factor-engine 目录写入 PYTHONPATH（initializer 之外再覆盖一层）。
    _pypath = os.environ.get("PYTHONPATH", "")
    if _FACTOR_ENGINE_DIR not in _pypath.split(os.pathsep):
        os.environ["PYTHONPATH"] = _FACTOR_ENGINE_DIR + os.pathsep + _pypath

    metrics_path = os.path.join(output_dir, f"{factor_name}_metrics.json")
    # 预删旧 metrics，避免超时后误判历史成功文件
    if os.path.exists(metrics_path):
        try:
            os.remove(metrics_path)
        except OSError:
            pass

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_alphalens_full_worker,
        args=(factor_df, price_df, output_dir, factor_name),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=ALPHALENS_FULL_REPORT_TIMEOUT)
    if proc.is_alive():
        proc.kill()
        logger.warning(
            "alphalens 原生全链路超时（>%ss，因子 %s），降级方案 C",
            ALPHALENS_FULL_REPORT_TIMEOUT,
            factor_name,
        )
        return None
    # 子进程正常结束：依据 metrics.json 是否落地判定成功（不依赖 Queue/Pipe）
    if os.path.exists(metrics_path):
        return {
            "metrics_json": metrics_path,
            "html": os.path.join(output_dir, f"{factor_name}_report.html"),
        }
    logger.warning("alphalens 原生全链路未产出 metrics，降级方案 C（因子 %s）", factor_name)
    return None


# ---------------------------------------------------------------------------
# 环境变量与可用性检查
# ---------------------------------------------------------------------------


def is_alphalens_enabled() -> bool:
    """检查环境变量是否启用 alphalens 报告"""
    return os.environ.get("QUANT_ALPHALENS_REPORT", "0") == "1"


def _alphalens_available() -> bool:
    """检查 alphalens-reloaded 是否可用"""
    try:
        import alphalens  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 数据格式适配器（T3-3）
# ---------------------------------------------------------------------------


class AlphalensAdapter:
    """将 jingni-trader 内部数据结构适配为 alphalens 输入格式。

    jingni-trader 内部：
        factor_df: columns=[code, date, factor1, factor2, ...]
        price_df:  columns=[code, date, close]

    alphalens 期望：
        factor: pd.Series，MultiIndex(date, code)，值为因子值
        prices: pd.DataFrame，index=date，columns=code，值为价格
    """

    # ------------------------------------------------------------------
    # 数据格式转换
    # ------------------------------------------------------------------

    @staticmethod
    def to_alphalens_format(
        factor_df: pd.DataFrame,
        price_df: pd.DataFrame,
        factor_name: str,
        forward_periods: Tuple[int, ...] = (1, 5, 20),
        quantiles: int = 5,
        max_loss: float = 0.25,
    ):
        """转 alphalens 期望的 factor_data（已清洗的 MultiIndex）。

        参数
        ----
        factor_df:        jingni-trader 内部因子 DataFrame
        price_df:         含 code/date/close 的价格 DataFrame
        factor_name:      要分析的因子列名
        forward_periods:  前瞻期，alphalens 用此计算 forward returns
        quantiles:        分层数，默认 5
        max_loss:         允许的最大数据丢失率（0-1）

        返回
        ----
        alphalens factor_data（MultiIndex DataFrame），失败抛出异常
        """
        import alphalens as al

        # 1. factor Series，索引为 (date, code)
        required = {"code", "date", factor_name}
        missing = required - set(factor_df.columns)
        if missing:
            raise ValueError(f"factor_df 缺少必要列: {missing}")

        factor_series = (
            factor_df[["date", "code", factor_name]]
            .dropna(subset=[factor_name])
            .set_index(["date", "code"])[factor_name]
            .sort_index()
        )

        # 2. price pivot，索引为 date，列为 code
        price_pivot = (
            price_df[["date", "code", "close"]]
            .dropna(subset=["close"])
            .pivot(index="date", columns="code", values="close")
            .sort_index()
        )

        # 3. alphalens 标准清洗（自动计算 forward returns + 分层）
        factor_data = al.utils.get_clean_factor_and_forward_returns(
            factor=factor_series,
            prices=price_pivot,
            quantiles=quantiles,
            periods=list(forward_periods),
            max_loss=max_loss,
        )
        return factor_data

    # ------------------------------------------------------------------
    # 报告生成（T3-4）
    # ------------------------------------------------------------------

    @staticmethod
    def generate_full_report(
        factor_data,
        output_dir: Path,
        factor_name: str,
    ) -> Dict[str, str]:
        """生成完整 alphalens 报告：4 PNG + 1 HTML + 1 JSON。

        参数
        ----
        factor_data:  AlphalensAdapter.to_alphalens_format 的返回值
        output_dir:   输出目录
        factor_name:  因子名（用于文件命名）

        返回
        ----
        {"returns_png": ..., "ic_png": ..., "turnover_png": ...,
         "summary_png": ..., "html": ..., "metrics_json": ...}
        """
        import matplotlib

        matplotlib.use("Agg")  # 无头模式，避免 Windows 显示问题
        import matplotlib.pyplot as plt
        import alphalens as al

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        prefix = factor_name
        paths = {
            "returns_png": str(output_dir / f"{prefix}_returns.png"),
            "ic_png": str(output_dir / f"{prefix}_ic.png"),
            "turnover_png": str(output_dir / f"{prefix}_turnover.png"),
            "summary_png": str(output_dir / f"{prefix}_summary.png"),
            "html": str(output_dir / f"{prefix}_report.html"),
            "metrics_json": str(output_dir / f"{prefix}_metrics.json"),
        }

        # 4 张 PNG 分别保存
        # 兼容 alphalens-reloaded 0.4.x：tear sheet 已移除 save_fig 参数，
        # 改为调用后捕获 matplotlib 当前 figure 再保存（每个 tear sheet 生成 1 个 figure）。
        def _save_current_fig(target: str) -> None:
            """保存当前 matplotlib figure 到 target，随后关闭避免残留。"""
            fig = plt.gcf()
            fig.savefig(target, bbox_inches="tight")
            plt.close(fig)

        try:
            al.tears.create_returns_tear_sheet(factor_data)
            _save_current_fig(paths["returns_png"])
        except Exception as e:
            logger.warning(f"alphalens returns tear sheet 生成失败: {e}")
        try:
            al.tears.create_information_tear_sheet(factor_data)
            _save_current_fig(paths["ic_png"])
        except Exception as e:
            logger.warning(f"alphalens information tear sheet 生成失败: {e}")
        try:
            al.tears.create_turnover_tear_sheet(factor_data)
            _save_current_fig(paths["turnover_png"])
        except Exception as e:
            logger.warning(f"alphalens turnover tear sheet 生成失败: {e}")
        try:
            al.tears.create_summary_tear_sheet(factor_data)
            _save_current_fig(paths["summary_png"])
        except Exception as e:
            logger.warning(f"alphalens summary tear sheet 生成失败: {e}")

        # 提取关键指标到 JSON（供 reports-engine 引用）
        metrics = AlphalensAdapter._extract_metrics(factor_data, factor_name)
        with open(paths["metrics_json"], "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

        # HTML 全报告（分文件引用 PNG）
        AlphalensAdapter._generate_html_report(output_dir, factor_name, metrics, paths)

        logger.info(f"alphalens 报告已生成: {output_dir}/{prefix}_*")
        return paths

    # ------------------------------------------------------------------
    # metrics 提取（T3-5）
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_metrics(factor_data, factor_name: str) -> Dict[str, Any]:
        """从 alphalens factor_data 提取 8 个必填字段。

        字段：
            factor, top_quantile_return, bottom_quantile_return,
            long_short_return, long_short_sharpe,
            ic_mean, ic_ir, avg_turnover_top_quantile, suggested_verdict
        """
        import alphalens as al

        # 1. 分层收益（factor_returns: index=date, columns=quantile period）
        try:
            returns_data = al.performance.factor_returns(factor_data)
        except Exception as e:
            logger.warning(f"alphalens factor_returns 失败: {e}")
            returns_data = pd.DataFrame()

        # 2. IC（mean_information_coefficient: Series indexed by period）
        try:
            ic_data = al.performance.factor_information_coefficient(factor_data)
        except Exception as e:
            logger.warning(f"alphalens factor_information_coefficient 失败: {e}")
            ic_data = pd.DataFrame()

        # 3. 换手率：top quantile 的平均换手率
        # 兼容 alphalens-reloaded 0.4.x：已移除 factor_top_bottom_quantile_turnover，
        # 改为基于 factor_quantile 列 + quantile_turnover(quantile_factor, quantile) 计算。
        avg_turnover_top = 0.0
        try:
            if "factor_quantile" in factor_data.columns:
                quantile_factor = factor_data["factor_quantile"].dropna()
                if not quantile_factor.empty:
                    top_q = float(quantile_factor.max())
                    turn = al.performance.quantile_turnover(quantile_factor, top_q)
                    # turn 为 Series（index=date，Name=quantile），取均值
                    turn = pd.Series(turn).dropna()
                    if not turn.empty:
                        avg_turnover_top = float(turn.mean())
        except Exception as e:
            logger.warning(f"alphalens turnover 失败: {e}")

        # ── 计算指标 ──
        # 分层收益：取最短周期列，最高分层 - 最低分层
        top_quantile_return = 0.0
        bottom_quantile_return = 0.0
        long_short_return = 0.0
        long_short_sharpe = 0.0
        if not returns_data.empty:
            # factor_returns 列为前瞻期（如 "1D", "5D", "20D"），取最短
            first_col = returns_data.columns[0] if len(returns_data.columns) > 0 else 0
            series = returns_data[first_col]
            top_quantile_return = float(series.mean()) if len(series) > 0 else 0.0
            # 若有多个分层列（按 quantile），取最高-最低
            if isinstance(returns_data, pd.DataFrame) and len(returns_data.columns) >= 2:
                cols_sorted = sorted(returns_data.columns)
                top_series = returns_data[cols_sorted[-1]]
                bot_series = returns_data[cols_sorted[0]]
                top_quantile_return = float(top_series.mean())
                bottom_quantile_return = float(bot_series.mean())
                ls_series = top_series - bot_series
                long_short_return = float(ls_series.mean())
                ls_std = float(ls_series.std())
                # 使用容差避免浮点精度导致 std 为极小非零值时除以趋近于 0 的数
                long_short_sharpe = float(long_short_return / ls_std * np.sqrt(252)) if ls_std > 1e-10 else 0.0

        # IC：取所有周期平均
        ic_mean = 0.0
        ic_ir = 0.0
        if not ic_data.empty:
            ic_series = ic_data.mean(axis=1)  # 每日跨周期平均
            ic_mean = float(ic_series.mean())
            ic_std = float(ic_series.std())
            ic_ir = float(ic_mean / ic_std) if ic_std > 1e-10 else 0.0

        # 建议结论（参考 RuleJudge 阈值：IC_IR ≥ 0.5, Sharpe ≥ 0.8）
        suggested_verdict = "ACCEPT" if (ic_ir >= 0.5 and long_short_sharpe >= 0.8) else "REVIEW"

        return {
            "factor": factor_name,
            "top_quantile_return": round(top_quantile_return, 4),
            "bottom_quantile_return": round(bottom_quantile_return, 4),
            "long_short_return": round(long_short_return, 4),
            "long_short_sharpe": round(long_short_sharpe, 4),
            "ic_mean": round(ic_mean, 4),
            "ic_ir": round(ic_ir, 4),
            "avg_turnover_top_quantile": round(avg_turnover_top, 4),
            "suggested_verdict": suggested_verdict,
        }

    # ------------------------------------------------------------------
    # HTML 报告生成
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_html_report(
        output_dir: Path,
        factor_name: str,
        metrics: Dict[str, Any],
        paths: Dict[str, str],
    ) -> None:
        """生成 HTML 全报告，分文件引用 PNG。"""
        prefix = factor_name
        verdict_color = {
            "ACCEPT": "#28a745",
            "REVIEW": "#ffc107",
            "REJECT": "#dc3545",
        }.get(metrics.get("suggested_verdict", "REVIEW"), "#6c757d")

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{factor_name} 因子分析报告</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 24px; color: #333; }}
  h1 {{ color: #1a3a6c; border-bottom: 2px solid #1a3a6c; padding-bottom: 8px; }}
  h2 {{ color: #2c5282; margin-top: 32px; }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }}
  .metric-card {{ background: #f8f9fa; padding: 12px; border-radius: 4px; border-left: 3px solid #2c5282; }}
  .metric-name {{ font-size: 12px; color: #6c757d; }}
  .metric-value {{ font-size: 18px; font-weight: bold; color: #2c5282; }}
  .verdict {{ display: inline-block; padding: 4px 12px; border-radius: 12px; color: white; background: {verdict_color}; }}
  .chart {{ margin: 24px 0; text-align: center; }}
  .chart img {{ max-width: 100%; border: 1px solid #dee2e6; border-radius: 4px; }}
  footer {{ margin-top: 48px; color: #6c757d; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<h1>{factor_name} 因子分析报告</h1>
<p>生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

<h2>关键指标</h2>
<div class="metrics-grid">
  <div class="metric-card"><div class="metric-name">Top 分层收益</div><div class="metric-value">{metrics["top_quantile_return"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">Bottom 分层收益</div><div class="metric-value">{metrics["bottom_quantile_return"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">多空收益</div><div class="metric-value">{metrics["long_short_return"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">多空夏普</div><div class="metric-value">{metrics["long_short_sharpe"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">IC 均值</div><div class="metric-value">{metrics["ic_mean"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">IC IR</div><div class="metric-value">{metrics["ic_ir"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">Top 分层换手率</div><div class="metric-value">{metrics["avg_turnover_top_quantile"]:.4f}</div></div>
  <div class="metric-card"><div class="metric-name">建议结论</div><div class="metric-value"><span class="verdict">{metrics["suggested_verdict"]}</span></div></div>
</div>

<h2>分层净值与累积收益</h2>
<div class="chart"><img src="{prefix}_returns.png" alt="分层净值"></div>

<h2>IC 时序与累积 IC</h2>
<div class="chart"><img src="{prefix}_ic.png" alt="IC 分析"></div>

<h2>分层换手率</h2>
<div class="chart"><img src="{prefix}_turnover.png" alt="换手率"></div>

<h2>综合统计</h2>
<div class="chart"><img src="{prefix}_summary.png" alt="综合统计"></div>

<footer>由 jingni-trader factor-engine + alphalens-reloaded 自动生成</footer>
</body>
</html>"""

        html_path = Path(paths["html"])
        html_path.write_text(html, encoding="utf-8")

    # ------------------------------------------------------------------
    # 入口：完整流程（含 fallback）
    # ------------------------------------------------------------------

    @staticmethod
    def generate_for_factor(
        factor_df: pd.DataFrame,
        price_df: pd.DataFrame,
        factor_name: str,
        output_dir: Path,
        forward_periods: Tuple[int, ...] = (1, 5, 20),
        quantiles: int = 5,
    ) -> Dict[str, str] | None:
        """端到端：单因子报告生成（含 fallback 到方案 C）。

        返回
        ----
        生成的文件路径字典；失败返回 None
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 路径 A：alphalens-reloaded 原生路径
        # Windows 护栏：alphalens-reloaded 的 get_clean_factor_and_forward_returns
        # 在真实行情数据上会长时间不收敛甚至挂起（与超时机制无关，spawn 子进程被
        # kill 后主进程仍可能卡在回收），导致 FACTOR 阶段在 Windows 下卡死。
        # 因此 Windows 默认跳过原生路径，直接走方案 C（纯 pandas 分层回测，绝不挂起）；
        # 仅当用户显式设置 QUANT_ALPHALENS_NATIVE=1 且平台非 win32 时才走原生路径。
        _native_allowed = sys.platform != "win32" and os.environ.get("QUANT_ALPHALENS_NATIVE", "0") == "1"
        if _alphalens_available() and _native_allowed:
            try:
                # 带超时护栏：to_alphalens_format + generate_full_report 一并放入 spawn
                # 子进程（后者 60s 超时），alphalens-reloaded 对真实数据可能挂起/长算，
                # 超时即降级方案 C，避免 FACTOR 阶段卡死。
                paths = _run_alphalens_full_with_timeout(factor_df, price_df, output_dir, factor_name)
                if paths is not None:
                    return paths
            except Exception as e:
                logger.warning(f"alphalens 路径失败，降级到方案 C（因子 {factor_name}）: {e}")
        elif _alphalens_available() and not _native_allowed:
            logger.info(
                "Windows 平台或 QUANT_ALPHALENS_NATIVE 未开启，跳过 alphalens 原生路径，直接走方案 C（因子 %s）",
                factor_name,
            )

        # 路径 C：自研轻量分层回测（不生成 PNG，仅 JSON）
        return _fallback_layered_backtest(
            factor_df,
            price_df,
            factor_name,
            output_dir,
            forward_periods=forward_periods,
            quantiles=quantiles,
        )


# ---------------------------------------------------------------------------
# 方案 C：自研轻量分层回测（T3-11，alphalens 不可用时降级）
# ---------------------------------------------------------------------------


def _is_numeric_str(s) -> bool:
    """判断字符串是否可转 float"""
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _fallback_layered_backtest(
    factor_df: pd.DataFrame,
    price_df: pd.DataFrame,
    factor_name: str,
    output_dir: Path,
    forward_periods: Tuple[int, ...] = (1, 5, 20),
    quantiles: int = 5,
) -> Dict[str, str] | None:
    """方案 C：自研轻量分层回测，仅输出 metrics.json + 简单 HTML。

    触发条件：
        - alphalens-reloaded import 失败
        - alphalens get_clean_factor_and_forward_returns 报错
    """
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = factor_name

        # 1. 合并因子与价格，按 date 截面分层
        merged = (
            factor_df[["date", "code", factor_name]]
            .merge(price_df[["date", "code", "close"]], on=["date", "code"], how="inner")
            .dropna(subset=[factor_name, "close"])
        )

        if merged.empty:
            logger.warning(f"方案 C: 因子 {factor_name} 合并后为空")
            return None

        # 2. 计算前瞻收益
        merged = merged.sort_values(["code", "date"])
        for p in forward_periods:
            merged[f"fwd_{p}d"] = merged.groupby("code")["close"].shift(-p) / merged["close"] - 1

        # 3. 按截面因子值分层（quantile 1=最低，quantile=最高）
        def _quantile_assign(group):
            try:
                return pd.qcut(group[factor_name], q=quantiles, labels=False, duplicates="drop")
            except Exception:
                return pd.Series([np.nan] * len(group), index=group.index)

        merged["quantile"] = merged.groupby("date", group_keys=False).apply(_quantile_assign)

        # 4. 计算各分层前瞻收益均值（取最短周期）
        period_col = f"fwd_{forward_periods[0]}d"
        if period_col not in merged.columns:
            logger.warning(f"方案 C: 缺少前瞻期列 {period_col}")
            return None

        quantile_returns = merged.dropna(subset=["quantile", period_col]).groupby("quantile")[period_col].mean()

        if quantile_returns.empty:
            return None

        top_q = int(quantile_returns.index.max())
        bot_q = int(quantile_returns.index.min())
        top_quantile_return = float(quantile_returns.loc[top_q])
        bottom_quantile_return = float(quantile_returns.loc[bot_q])
        long_short_return = top_quantile_return - bottom_quantile_return

        # 5. 多空收益时序与夏普
        ls_series = (
            merged.dropna(subset=["quantile", period_col])
            .assign(
                is_top=lambda x: x["quantile"] == top_q,
                is_bot=lambda x: x["quantile"] == bot_q,
            )
            .groupby("date")
            .apply(lambda g: g.loc[g.is_top, period_col].mean() - g.loc[g.is_bot, period_col].mean())
            .dropna()
        )
        ls_std = float(ls_series.std()) if len(ls_series) > 1 else 0.0
        long_short_sharpe = float(long_short_return / ls_std * np.sqrt(252)) if ls_std > 1e-10 else 0.0

        # 6. IC（因子值 vs 前瞻收益的 Spearman 相关）
        ic_series = (
            merged.dropna(subset=[factor_name, period_col])
            .groupby("date")
            .apply(lambda g: g[factor_name].corr(g[period_col], method="spearman"))
            .dropna()
        )
        ic_mean = float(ic_series.mean()) if not ic_series.empty else 0.0
        ic_std = float(ic_series.std()) if len(ic_series) > 1 else 0.0
        ic_ir = float(ic_mean / ic_std) if ic_std > 1e-10 else 0.0

        # 7. Top 分层换手率（近似：相邻截面 Top 分层成员变化比例）
        top_members = (
            merged.dropna(subset=["quantile"])
            .assign(is_top=lambda x: x["quantile"] == top_q)
            .groupby("date")["is_top"]
            .apply(lambda s: set(s.index[s]))
        )
        turnover_list = []
        prev_set = None
        for _d, members in top_members.items():
            if prev_set is not None and (len(prev_set) + len(members)) > 0:
                union = prev_set | members
                inter = prev_set & members
                turnover_list.append(1.0 - len(inter) / len(union))
            prev_set = members
        avg_turnover_top = float(np.mean(turnover_list)) if turnover_list else 0.0

        # 8. 建议结论
        suggested_verdict = "ACCEPT" if (ic_ir >= 0.5 and long_short_sharpe >= 0.8) else "REVIEW"

        metrics = {
            "factor": factor_name,
            "top_quantile_return": round(top_quantile_return, 4),
            "bottom_quantile_return": round(bottom_quantile_return, 4),
            "long_short_return": round(long_short_return, 4),
            "long_short_sharpe": round(long_short_sharpe, 4),
            "ic_mean": round(ic_mean, 4),
            "ic_ir": round(ic_ir, 4),
            "avg_turnover_top_quantile": round(avg_turnover_top, 4),
            "suggested_verdict": suggested_verdict,
            "_backend": "fallback_lite",  # 标识来源
        }

        # 写 JSON
        metrics_path = output_dir / f"{prefix}_metrics.json"
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

        # 写简化 HTML（无 PNG）
        html_path = output_dir / f"{prefix}_report.html"
        html_path.write_text(_fallback_html(factor_name, metrics), encoding="utf-8")

        logger.info(f"方案 C 报告已生成（无 PNG）: {metrics_path}")
        return {
            "metrics_json": str(metrics_path),
            "html": str(html_path),
            "backend": "fallback_lite",
        }
    except Exception as e:
        logger.warning(f"方案 C 失败（因子 {factor_name}）: {e}")
        return None


def _fallback_html(factor_name: str, metrics: Dict[str, Any]) -> str:
    """方案 C 的简化 HTML（无 PNG 引用，仅指标卡片）"""
    verdict_color = {
        "ACCEPT": "#28a745",
        "REVIEW": "#ffc107",
        "REJECT": "#dc3545",
    }.get(metrics.get("suggested_verdict", "REVIEW"), "#6c757d")
    cards = []
    for label, key in [
        ("Top 分层收益", "top_quantile_return"),
        ("Bottom 分层收益", "bottom_quantile_return"),
        ("多空收益", "long_short_return"),
        ("多空夏普", "long_short_sharpe"),
        ("IC 均值", "ic_mean"),
        ("IC IR", "ic_ir"),
        ("Top 分层换手率", "avg_turnover_top_quantile"),
    ]:
        cards.append(
            f'<div class="metric-card"><div class="metric-name">{label}</div>'
            f'<div class="metric-value">{metrics.get(key, 0):.4f}</div></div>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{factor_name} 因子分析报告（精简版）</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 24px; color: #333; }}
  h1 {{ color: #1a3a6c; border-bottom: 2px solid #1a3a6c; padding-bottom: 8px; }}
  h2 {{ color: #2c5282; margin-top: 32px; }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }}
  .metric-card {{ background: #f8f9fa; padding: 12px; border-radius: 4px; border-left: 3px solid #2c5282; }}
  .metric-name {{ font-size: 12px; color: #6c757d; }}
  .metric-value {{ font-size: 18px; font-weight: bold; color: #2c5282; }}
  .verdict {{ display: inline-block; padding: 4px 12px; border-radius: 12px; color: white; background: {verdict_color}; }}
  .notice {{ background: #fff3cd; border: 1px solid #ffe69c; padding: 12px; border-radius: 4px; margin: 16px 0; }}
  footer {{ margin-top: 48px; color: #6c757d; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<h1>{factor_name} 因子分析报告（精简版）</h1>
<p>生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<div class="notice">⚠️ alphalens-reloaded 不可用，已降级到自研轻量分层回测（仅 JSON 指标，无图）。</div>
<h2>关键指标</h2>
<div class="metrics-grid">
  {"".join(cards)}
  <div class="metric-card"><div class="metric-name">建议结论</div><div class="metric-value"><span class="verdict">{metrics["suggested_verdict"]}</span></div></div>
</div>
<footer>由 jingni-trader factor-engine 自研轻量分层回测生成</footer>
</body>
</html>"""
