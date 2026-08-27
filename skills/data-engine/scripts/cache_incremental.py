"""
local 缓存增量更新（REQ-2026-08-16 优化：增量优先 + 修正感知）

背景：缓存写回已上线（整删整取）。本模块在「序列类数据」上实现增量优先：
  - 保留旧缓存，仅联网拉取「缓存截止日之后」的缺失区间，与旧缓存按日期去重合并后写回；
  - 快照/事件类数据（实时行情/资金面/龙虎榜/股东/参考/基础）无增量可补 → 维持整取；
  - 修正感知：meta.json 记录复权口径(adjust)与数据指纹(data_signature)；若上游返回数据与
    旧缓存口径不一致（复权方式变化 / 重叠区间价格跳变 → 上游修正）则回退全量重取。

设计原则：
  - 纯函数 + 零外部依赖（仅 pandas / 标准库 / scripts.config 的缓存 meta 助手），便于单测；
  - 不打印/不落盘 token；meta 仅含时间戳、口径与指纹，无敏感信息；
  - 所有「是否走增量」的决策集中在本模块，engine 侧只调用 plan/merge 两个入口。
"""

import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger("data-engine.cache_incremental")

# ── 序列类数据（支持增量补充）────────────────────────────
# 多期序列，含可排序的时间/报告期维度，且适配器支持 start_date 边界查询。
SERIES_TYPES: set = {
    "daily",          # 日线行情（date 维度）
    "market_kline",   # K 线（date 维度）
    "financial",      # 财务指标（report_date 维度）
    "financial_report",  # 三大报表（report_date 维度）
}

# ── 快照/事件类（维持整取，无增量可补）──────────────────
SNAPSHOT_TYPES: set = {
    "market_realtime",
    "capital_flow",
    "dragon_tiger",
    "shareholder",
    "ref_dividend",
    "ref_suspend_resume",
    "ref_locked_shares",
    "ref_forecast",
    "basic_stock_list",
    "basic_stock_info",
    "basic_trade_calendar",
    "basic_adjust_factor",
}

# 时间/期维度列名（用于截止日计算与去重合并）
_DATE_COL: Dict[str, str] = {
    "daily": "date",
    "market_kline": "date",
    "financial": "report_date",
    "financial_report": "report_date",
}

# 合并去重主键（序列类按 标的+期 唯一）
_MERGE_KEY: Dict[str, List[str]] = {
    "daily": ["code", "date"],
    "market_kline": ["code", "date"],
    "financial": ["code", "report_date"],
    "financial_report": ["code", "report_date"],
}

# 修正感知比对的数值列（重叠窗口内价格/估值跳变即视为上游修正）
_VALUE_COLS: Dict[str, List[str]] = {
    "daily": ["close", "open", "high", "low"],
    "market_kline": ["close", "open", "high", "low"],
    "financial": ["pe_ttm", "pb", "roe"],
    "financial_report": ["revenue", "net_profit", "total_assets"],
}

# 价格跳变容忍相对误差（复权修正通常远大于此；正常刷新误差应 < 1e-6）
_CORRECTION_TOL = 1e-3


def is_series_type(data_type: str) -> bool:
    """该数据项是否为序列类（可增量补充）。"""
    return data_type in SERIES_TYPES


def date_col_of(data_type: str) -> str | None:
    """返回序列类数据的时间/期维度列名；非序列返回 None。"""
    return _DATE_COL.get(data_type)


def _parse_date(v) -> pd.Timestamp | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return pd.to_datetime(v)
    except Exception:
        return None


def cached_end_date(old_df: pd.DataFrame, data_type: str) -> pd.Timestamp | None:
    """计算旧缓存覆盖的截止时间（时间/期列 max）。"""
    col = date_col_of(data_type)
    if col is None or col not in old_df.columns:
        return None
    ts = old_df[col].dropna().apply(_parse_date)
    ts = ts.dropna()
    if ts.empty:
        return None
    return ts.max()


def compute_data_signature(old_df: pd.DataFrame, data_type: str) -> str:
    """计算旧缓存的数据指纹（用于修正感知比对）。

    轻量实现：对 (主键 + 数值列) 做稳定排序后 sha256，避免逐行比对开销。
    仅覆盖序列类；非序列返回空串（不参与修正感知）。
    """
    if data_type not in SERIES_TYPES:
        return ""
    key = _MERGE_KEY.get(data_type, [])
    val_cols = _VALUE_COLS.get(data_type, [])
    cols = [c for c in key + val_cols if c in old_df.columns]
    if not cols:
        return ""
    try:
        sub = old_df[cols].copy()
        # 统一数值精度，避免浮点表示差异导致指纹抖动
        for c in val_cols:
            if c in sub.columns:
                sub[c] = pd.to_numeric(sub[c], errors="coerce").round(4)
        sub = sub.sort_values(cols).fillna("")
        payload = sub.to_csv(index=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    except Exception as e:  # noqa
        logger.debug("数据指纹计算失败（忽略）: %s", e)
        return ""


def plan_incremental(
    data_type: str,
    cached_meta: Dict[str, Any],
    requested_start: str,
    requested_end: str,
    adjust: str = "qfq",
    incremental_enabled: bool = True,
) -> str | None:
    """规划增量取数的有效起始日。

    返回：
      - None           → 不应走增量（维持整取 / 全量重取）
      - "<date>"       → 增量起始日（= 缓存截止日 + 1），调用方据此构造只取缺失区间的查询

    决策逻辑：
      1. 开关关闭 / 非序列类 → None（整取）
      2. 缓存 meta 缺失（历史缓存）→ None（全量，保证策略生效）
      3. 复权口径(adjust)不一致 → None（回退全量，避免前/后复权混写）
      4. 缓存截止日 >= 请求结束日 → None（无需补充）
      5. 否则返回 cached_end + 1 作为增量起始日
    """
    if not incremental_enabled or data_type not in SERIES_TYPES:
        return None
    if not cached_meta:
        return None
    # 修正感知：复权口径变化 → 全量重取
    if cached_meta.get("adjust") is not None and cached_meta.get("adjust") != adjust:
        logger.info("增量规划：复权口径变化(%s→%s)，回退全量重取", cached_meta.get("adjust"), adjust)
        return None

    end_ts = _parse_date(requested_end)
    cached_end = _parse_date(cached_meta.get("cached_end"))
    if cached_end is None or end_ts is None:
        return None
    if cached_end >= end_ts:
        return None  # 已覆盖请求区间，无需补充

    # 增量起始日 = 缓存截止日 + 1 自然日（交易日历由上游适配器自行裁剪）
    next_day = (cached_end + pd.Timedelta(days=1))
    return next_day.strftime("%Y-%m-%d")


def correction_detected(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
    data_type: str,
) -> bool:
    """检测上游是否发生修正（重叠区间数值跳变）。

    仅在 new_df 含与 old_df 重叠的 (主键) 行时比对数值列；若任一数值列相对误差超阈值
    （且非整体平移，如复权导致的全序列价格缩放已在 plan 阶段由 adjust 不一致拦截）→ 判定修正。
    """
    key = _MERGE_KEY.get(data_type, [])
    val_cols = _VALUE_COLS.get(data_type, [])
    if not key or not val_cols:
        return False
    if old_df is None or new_df is None or old_df.empty or new_df.empty:
        return False
    common = [c for c in key + val_cols if c in old_df.columns and c in new_df.columns]
    if len(common) < len(key) + 1:
        return False
    try:
        old_k = old_df[key + [c for c in val_cols if c in old_df.columns]].dropna(subset=key)
        new_k = new_df[key + [c for c in val_cols if c in new_df.columns]].dropna(subset=key)
        merged = old_k.merge(new_k, on=key, how="inner", suffixes=("_old", "_new"))
        if merged.empty:
            return False  # 无重叠 → 纯新增，未触发修正
        for c in val_cols:
            oc, nc = f"{c}_old", f"{c}_new"
            if oc not in merged.columns or nc not in merged.columns:
                continue
            o = pd.to_numeric(merged[oc], errors="coerce")
            n = pd.to_numeric(merged[nc], errors="coerce")
            denom = o.abs().replace(0, pd.NA)
            rel = ((n - o).abs() / denom).dropna()
            if (rel > _CORRECTION_TOL).any():
                logger.info("增量修正感知：%s 列重叠窗口相对误差超阈值，判定上游修正", c)
                return True
        return False
    except Exception as e:  # noqa
        logger.debug("修正比对异常（按无修正处理）: %s", e)
        return False


def merge_incremental(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
    data_type: str,
) -> pd.DataFrame:
    """旧缓存 + 增量新数据 → 去重合并后的完整 DataFrame。

    - 以主键(标的+期)去重，new_df 覆盖同键旧行（增量优先取新值）
    - 合并后按主键排序，时间/期列保持原类型
    - 任一为空则回退另一份
    """
    key = _MERGE_KEY.get(data_type, [])
    if old_df is None or old_df.empty:
        return new_df if new_df is not None else old_df
    if new_df is None or new_df.empty:
        return old_df
    if not key or not all(c in old_df.columns and c in new_df.columns for c in key):
        # 无主键信息 → 直接返回新数据（保守：整取语义）
        logger.warning("增量合并：缺主键(%s)，退回整取新数据", key)
        return new_df

    try:
        combined = pd.concat([old_df, new_df], ignore_index=True)
        # new_df 在后 → 去重时保留最后出现（即新值覆盖旧值）
        combined = combined.drop_duplicates(subset=key, keep="last")
        col = date_col_of(data_type)
        if col and col in combined.columns:
            combined = combined.sort_values(key + [col] if col not in key else key)
        combined = combined.reset_index(drop=True)
        return combined
    except Exception as e:  # noqa
        logger.warning("增量合并失败（退回新数据）: %s", e)
        return new_df
