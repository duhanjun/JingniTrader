"""
本地数据适配器（私有化回退通道）

仅读取本地已落盘的 Parquet/CSV 缓存，不发起任何网络请求。
作为 westock 公网通道不可用时的最后一道本地回退；命中率优先（Damon 确认 local
优先级高于 westock）。

REQ-2026-08-16 扩展：
  - SUPPORTED_DATA_TYPES 从 {daily, financial} 扩至全 16 项（与 data_types.DATA_TYPES 对齐）
  - 读取时按 TTL 做新鲜度校验（过期视为未命中，触发上层联网重取）
  - 全局型数据项（basic_stock_list / basic_trade_calendar，无 symbol 维度）统一读 all.parquet
"""
from __future__ import annotations

import os
import logging
from typing import List, Dict, Any
import pandas as pd

from scripts.base.base_data_provider import BaseDataProvider
from scripts.errors import DataNotFoundError
from scripts.config import (
    GLOBAL_CACHE_TYPES,
    cache_is_fresh,
    CACHE_WRITE_BACK,  # noqa: F401 仅引用以显式关联开关语义（写回在 engine 侧）
)
from scripts.data_types import DATA_TYPES

logger = logging.getLogger("data-engine.local_adapter")


class LocalAdapter(BaseDataProvider):
    """本地缓存数据适配器（纯本地 IO，不联网）"""

    SUPPORTED_DATA_TYPES = {
        "daily",
        "capital_flow",
        "dragon_tiger",
        "shareholder",
        "financial",
        "basic_stock_list",
        "basic_stock_info",
        "basic_trade_calendar",
        "basic_adjust_factor",
        "market_kline",
        "market_realtime",
        "financial_report",
        "ref_dividend",
        "ref_suspend_resume",
        "ref_locked_shares",
        "ref_forecast",
    }

    def __init__(self, cache_dir: str | None = None, **kwargs):
        self._cache_dir = cache_dir or os.environ.get("DATA_CACHE_DIR", "./workspace/data_cache")

    # ── 通用读取（带 TTL 新鲜度校验）────────────
    def _read(self, symbol: str, data_type: str) -> pd.DataFrame | None:
        path = os.path.join(self._cache_dir, data_type, f"{symbol}.parquet")
        if not os.path.exists(path):
            return None
        # TTL 新鲜度校验：meta 缺失或过期 → 视为未命中（删除并触发重取）
        if not cache_is_fresh(path, data_type):
            try:
                os.remove(path)
                meta_p = path + ".meta.json"
                if os.path.exists(meta_p):
                    os.remove(meta_p)
            except OSError:
                pass
            logger.info("本地缓存过期已剔除: %s（触发联网重取）", path)
            return None
        try:
            return pd.read_parquet(path)
        except Exception as e:  # noqa
            logger.warning("本地缓存读取失败 %s: %s", path, e)
            return None

    def _read_global(self, data_type: str) -> pd.DataFrame | None:
        """读取无 symbol 维度的全局缓存（all.parquet）。"""
        return self._read("all", data_type)

    # ── 行情 market ──
    def get_daily(self, symbols: List[str], start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "daily")
            if df is not None:
                frames.append(df)
        if not frames:
            raise FileNotFoundError(f"本地缓存无 {symbols} 的日线数据，本地缓存不可用。")
        return pd.concat(frames, ignore_index=True)

    def get_kline(self, symbols: List[str], start_date: str, end_date: str, period: str = "day", adjust: str = "qfq", **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "market_kline")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的 K 线数据")
        return pd.concat(frames, ignore_index=True)

    def get_realtime_quote(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "market_realtime")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的实时行情")
        return pd.concat(frames, ignore_index=True)

    # ── 财务 financial ──
    def get_financial(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "financial")
            if df is not None:
                frames.append(df)
        if not frames:
            raise FileNotFoundError(f"本地缓存无 {symbols} 的财务数据。")
        return pd.concat(frames, ignore_index=True)

    def get_financial_report(self, symbols: List[str], report_date: str | None = None, **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "financial_report")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的财报数据")
        return pd.concat(frames, ignore_index=True)

    # ── 参考 reference ──
    def get_capital_flow(self, symbols: List[str], start_date: str, end_date: str, **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "capital_flow")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的资金面数据")
        return pd.concat(frames, ignore_index=True)

    def get_dragon_tiger(self, symbols: List[str], start_date: str, end_date: str, **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "dragon_tiger")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的龙虎榜数据")
        return pd.concat(frames, ignore_index=True)

    def get_shareholder(self, symbols: List[str], report_date: str | None = None, **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "shareholder")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的股东结构数据")
        return pd.concat(frames, ignore_index=True)

    def get_dividend(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "ref_dividend")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的分红派息数据")
        return pd.concat(frames, ignore_index=True)

    def get_suspend_resume(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "ref_suspend_resume")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的停复牌数据")
        return pd.concat(frames, ignore_index=True)

    def get_locked_shares(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "ref_locked_shares")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的限售解禁数据")
        return pd.concat(frames, ignore_index=True)

    def get_forecast(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "ref_forecast")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的业绩预告数据")
        return pd.concat(frames, ignore_index=True)

    # ── 基础 basic ──
    def get_stock_list(self) -> pd.DataFrame:
        # 全市场股票列表：本地缓存命中（all.parquet）则直接返回；否则显式不支持触发上层重取
        df = self._read_global("basic_stock_list")
        if df is not None and not df.empty:
            return df
        raise NotImplementedError("local 无全市场股票列表缓存；请经其他渠道获取 stock_list")

    def get_stock_info(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "basic_stock_info")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的股票基本信息")
        return pd.concat(frames, ignore_index=True)

    def get_trade_calendar(self, **kwargs) -> pd.DataFrame:
        # 交易日历：全局型，读 all.parquet
        df = self._read_global("basic_trade_calendar")
        if df is not None and not df.empty:
            return df
        raise DataNotFoundError("local", "本地缓存无交易日历数据")

    def get_adj_factor(self, symbols: List[str], start_date: str, end_date: str, **kwargs) -> pd.DataFrame:
        frames = []
        for sym in symbols:
            df = self._read(sym, "basic_adjust_factor")
            if df is not None:
                frames.append(df)
        if not frames:
            raise DataNotFoundError("local", f"本地缓存无 {symbols} 的复权因子数据")
        return pd.concat(frames, ignore_index=True)

    # ── 统一 dispatch（fetch_by_type 经 BaseDataProvider.fetch 调用）──
    def fetch(self, data_type: str, **kwargs) -> pd.DataFrame:
        """按 data_type 路由到对应 get_* 方法（覆盖全部 16 项）。"""
        method = getattr(self, DATA_TYPES[data_type].method_name, None)
        if method is None:
            raise DataNotFoundError("local", f"local 未实现 {data_type} 的读取方法")
        # 全局型数据项（无 symbols 维度）不传 symbols
        if data_type in GLOBAL_CACHE_TYPES:
            return method()
        return method(symbols=kwargs.get("symbols", []), **kwargs)
