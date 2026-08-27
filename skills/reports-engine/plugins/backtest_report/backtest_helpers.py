# -*- coding: utf-8 -*-
"""策略回测报告插件 — 归因计算辅助（阶段 C：从 engine.py 迁入，自包含）。

包含：
- compute_portfolio_exposure_timeseries：持仓组合逐日加权因子暴露（M3 单股/少股兜底）
"""
from __future__ import annotations

from typing import List

import pandas as pd


def compute_portfolio_exposure_timeseries(
    portfolio_weights: pd.DataFrame,
    factor_df: pd.DataFrame,
    cols: List[str],
) -> pd.DataFrame:
    """计算持仓组合在指定因子上的逐日加权暴露（M3 单股/少股兜底）。

    暴露_t = Σ_i (权重_i,t × 因子值_i,t)。返回含 date + cols 的 DataFrame。
    """
    if portfolio_weights is None or portfolio_weights.empty:
        return pd.DataFrame()
    if factor_df is None or factor_df.empty:
        return pd.DataFrame()

    avail = [c for c in cols if c in factor_df.columns]
    if not avail:
        return pd.DataFrame()

    pw = portfolio_weights[["date", "code", "weight"]].copy()
    merged = pw.merge(factor_df[["date", "code"] + avail], on=["date", "code"], how="left")
    merged["weight"] = pd.to_numeric(merged["weight"], errors="coerce")

    def _agg(g):
        out = {}
        for c in avail:
            v = (g[c] * g["weight"]).sum()
            out[c] = v if pd.notna(v) else None
        return pd.Series(out)

    try:
        return merged.groupby("date").apply(_agg, include_groups=False).reset_index()
    except TypeError:  # 兼容旧版 pandas
        return merged.groupby("date").apply(_agg).reset_index()
