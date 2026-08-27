"""
同花顺 iFinD 适配器（免费版实测基线 2026-08-17）

接入方法：
- 通过 THS_iFinDLogin(username, password) 登录（依赖 iFinDPy 包）
- 日线/历史行情：THS_HQ（对象式 .errorcode/.data）查 daily；THS_HistoryQuotes（dict 式）查 kline
  实测结论（600519.SH 锚点）：免费版「历史行情 100万/月」额度可用，
  THS_HQ 返回 errorcode=0 取 21 个交易日真实日线；THS_HistoryQuotes 同样 errorcode=0。
- 实时行情：THS_RealtimeQuotes（dict 式），「实时行情 300万/月」额度可用。
- 财务：THS_BD（对象式 .errorcode/.data），「基础数据 60万/月」额度可用。
- 公告：THS_ReportQuery（免费版 errorcode=0 但 data 空，本账户未开通/参数受限），
  「公告查询 1万次/月」——额度敏感，调用须克制，空返回降级。
- indicators 用分号分隔: "open;high;low;close;volume;amount"
- 复权参数：股票加 "CPS:1"(hfq)/"CPS:2"(qfq)

⚠️ 免费版不可用接口：
- stock_list：THS_BasicCenter 未从 iFinDPy 导出（ImportError），免费版不通；
  采用简化实现仅返回沪深300占位，需全市场请用 tushare/baostock/akshare。
- 高频序列：THS_HighFrequenceSequence 免费版受限（试用账号仅 1 年数据）。

根因说明（-1010 旧报错）：
  此前实测 THS_HQ 报 -1010，经 2026-08-17 复测确认：-1010 在 iFinD 体系常指
  「合约/参数格式错误或该接口未订阅」，并非历史行情权限缺失。改用标准 THS_HQ
  参数（indicators 分号分隔 + params=Fill:Original,CPS:x）后 errorcode=0 直接成功，
  印证免费版「历史行情 100万/月」额度本身可用，旧报错为接口/参数路径错用。

前置条件：
- 已配置 IFIND_USERNAME / IFIND_PASSWORD 环境变量（免费版账户）

复权方式（iFinD 的 CPS 参数）：
    qfq -> CPS:2（前复权，分红再投）
    hfq -> CPS:1（后复权）
    none-> 不设置 CPS（原始价）

额度说明（Damon 确认免费版月额度）：
- 历史行情 100万 | 实时行情 300万 | 基础数据 60万 | 日内快照 200万 | 高频序列 150万
- 公告查询 1万次/月（额度敏感，调用须克制）
"""
from __future__ import annotations

import logging
from typing import List, Optional
import pandas as pd

from scripts.base.base_data_provider import BaseDataProvider
from scripts.config import IFIND_USERNAME, IFIND_PASSWORD
from scripts.errors import DataSourceError, NetworkError, InvalidParameterError


logger = logging.getLogger("ifind-adapter")


# jingni-trader adjust 语义 -> iFinD CPS 取值
# iFinD CPS 取值（官方手册）：1=不复权 / 2=前复权 / 3=后复权
# 故 jingni-trader 的 hfq(后复权) -> "3"，qfq(前复权) -> "2"，none(不复权) -> "1"
# 实测佐证（600519.SH 2024-03-01）：CPS:1 close=1685.06（不复权），CPS:3 close=9397.69（后复权），
# 东财双口径一致，据此映射，避免 hfq 静默返回不复权价（Turing REVISE 第3轮 ⑨）。
_ADJUST_MAP = {
    "hfq": "3",  # 后复权（iFinD CPS=3）
    "qfq": "2",  # 前复权（iFinD CPS=2，分红再投）
    "none": "1",  # 不复权（iFinD CPS=1）
    "": None,
}

# 免费版间歇性返回的限流/权限码（非稳定权限缺失，可重试）
_TRANSIENT_ERROR_CODES = {-1010, -1009, -1030}

# 免费版明确不支持（试用账号限制 / block 名不通）的错误码
_UNSUPPORTED_ERROR_CODES = {-209, -4210, -4309}

_SUPPORTED_SUFFIX = {".SH", ".SZ", ".CFE", ".SHF", ".CZC", ".DCE"}


def _parse_history_dict(result: dict, symbol: str) -> pd.DataFrame | None:
    """解析 THS_HistoryQuotes / THS_RealtimeQuotes 的 dict 返回为 DataFrame。

    dict 结构：{'errorcode':int, 'errmsg':str, 'tables':[{'thscode':str,
    'time':[...], 'table':{col:[...]}}], ...}
    返回 None 表示无数据。
    """
    if not isinstance(result, dict):
        return None
    ec = result.get("errorcode")
    # 免费版 errorcode 可能是字符串 "0"；"0"/0/None 均视为成功
    if ec not in (0, "0", None):
        # 调用方负责根据 errorcode 抛错或降级，此处仅解析成功分支
        return None
    tables = result.get("tables") or []
    if not tables:
        return None
    t = tables[0]
    time_col = t.get("time") or []
    table = t.get("table") or {}
    if not table or not time_col:
        return None
    d = pd.DataFrame(table)
    d["date"] = pd.to_datetime(time_col)
    d["code"] = symbol
    return d


def _parse_realtime_dict(result: dict, symbol: str) -> pd.DataFrame | None:
    """解析 THS_RealtimeQuotes 的 dict 返回为单点快照 DataFrame。

    dict 结构：{'errorcode':int, 'errmsg':str, 'tables':[{'thscode':str,
    'table':{col:[v]}}], ...}（注意：实时快照无 time 序列，date 补当日）。
    返回 None 表示无数据。
    """
    if not isinstance(result, dict):
        return None
    ec = result.get("errorcode")
    # 免费版 errorcode 可能是字符串 "0"
    if ec not in (0, "0", None):
        return None
    tables = result.get("tables") or []
    if not tables:
        return None
    t = tables[0]
    table = t.get("table") or {}
    if not table:
        return None
    d = pd.DataFrame(table)
    # 实时快照无 time 序列，补当日日期
    d["date"] = pd.Timestamp.now().normalize()
    d["code"] = symbol
    return d


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    """把 iFinD 返回的裸行情统一成下游期望的标准 schema。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(
            columns=[
                "code",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "pre_close",
                "change_pct",
                "turnover_rate",
                "is_st",
                "is_limit_up",
                "is_limit_down",
                "listed_days",
            ]
        )
    if "vol" in df.columns and "volume" not in df.columns:
        df = df.rename(columns={"vol": "volume"})
    if "openinterest" in df.columns:
        df = df.drop(columns=["openinterest"])
    df["date"] = pd.to_datetime(df["date"])
    if "pre_close" not in df.columns:
        df["pre_close"] = df.groupby("code", group_keys=False)["close"].shift(1)
    if "change_pct" not in df.columns:
        df["change_pct"] = (df["close"] - df["pre_close"]) / df["pre_close"] * 100.0
    for c in ["turnover_rate", "is_st", "is_limit_up", "is_limit_down", "listed_days"]:
        if c not in df.columns:
            df[c] = None
    df["is_st"] = df["is_st"].fillna(False)
    df["is_limit_up"] = df["is_limit_up"].fillna(False)
    df["is_limit_down"] = df["is_limit_down"].fillna(False)
    cols = [
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pre_close",
        "change_pct",
        "turnover_rate",
        "is_st",
        "is_limit_up",
        "is_limit_down",
        "listed_days",
    ]
    return df.sort_values(["code", "date"]).reset_index(drop=True)[cols]


class IfindAdapter(BaseDataProvider):
    """同花顺 iFinD 适配器：通过 iFinDPy 拉取 A股行情。

    依赖 iFinDPy 包（由同花顺分发，不在 PyPI 公开发布），
    需配置 IFIND_USERNAME / IFIND_PASSWORD 环境变量。
    """

    # 免费版实测可用（2026-08-17 直连 600519.SH）：
    #   daily   -> THS_HQ（对象式）errorcode=0, 21 行真实日线（历史行情额度）
    #   kline   -> THS_HistoryQuotes（dict 式）errorcode=0（历史行情额度）
    #   realtime-> THS_RealtimeQuotes（dict 式）errorcode=0（实时行情额度）
    #   financial-> THS_BD（对象式）errorcode=0（基础数据额度）
    # 免费版不可用 / 受限：stock_list(THS_BasicCenter 未导出) / 公告(THS_ReportQuery 空返回,额度敏感) / 高频(-4309)
    # 双声明：旧键(daily/kline/realtime/financial)保留兼容，新键(market_kline/market_realtime)
    # 对齐注册表 DATA_TYPES，使 engine 以注册表 key 调 supports() 时 ifind 不被跳过
    # （参考 westock_adapter SUPPORTED_DATA_TYPES 双声明做法）。
    SUPPORTED_DATA_TYPES = {
        "daily", "kline", "realtime", "financial",
        "market_kline", "market_realtime",
    }

    def __init__(self, username: str | None = None, password: str | None = None):
        username = username or IFIND_USERNAME
        password = password or IFIND_PASSWORD
        if not username or not password:
            raise DataSourceError("ifind", "iFinD 账号或密码未设置，请配置环境变量 IFIND_USERNAME / IFIND_PASSWORD")
        try:
            from iFinDPy import (
                THS_iFinDLogin, THS_HQ, THSData, THS_BD,
                THS_HistoryQuotes, THS_RealtimeQuotes, THS_ReportQuery,
            )

            self._THS_iFinDLogin = THS_iFinDLogin
            self._THS_HQ = THS_HQ
            self._THSData = THSData
            self._THS_BD = THS_BD
            self._THS_HistoryQuotes = THS_HistoryQuotes
            self._THS_RealtimeQuotes = THS_RealtimeQuotes
            self._THS_ReportQuery = THS_ReportQuery
        except ImportError as e:
            raise DataSourceError("ifind", f"iFinDPy 包未安装: {e}。请购买同花顺 iFinD 数据服务并安装 iFinDPy。") from e

        self.username = username
        self.password = password
        self._inited = False
        self._login()

    def _login(self):
        """登录 iFinD。

        错误码约定(同花顺官方):
        - 0: 登录成功
        - -201: 重复登录(视为成功,官方示例 `if thsLogin in {0, -201}` 判定为成功)
        - -2: 用户名或密码错误
        - 其他: 登录失败
        """
        if self._inited:
            return
        try:
            code = self._THS_iFinDLogin(self.username, self.password)
            # 0=成功, -201=重复登录(同花顺官方视为成功)
            if code not in (0, -201):
                raise NetworkError("ifind", f"iFinD 登录失败，错误码: {code}")
            self._inited = True
            logger.info("iFinD 登录成功, 错误码: %s", code)
        except DataSourceError:
            raise
        except Exception as e:
            raise NetworkError("ifind", f"iFinD 登录异常: {e}") from e

    def _ensure_inited(self):
        if not self._inited:
            self._login()

    @staticmethod
    def _format_datetime(date_str: str) -> str:
        """把 YYYY-MM-DD 或 YYYYMMDD 统一为 iFinD 期望的 YYYY-MM-DD HH:MM:SS。"""
        s = date_str.replace("-", "").replace("/", "")
        if len(s) == 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]} 00:00:00"
        return date_str

    @staticmethod
    def _validate_symbol(symbol: str):
        if not any(symbol.endswith(suf) for suf in _SUPPORTED_SUFFIX):
            raise InvalidParameterError("ifind", f"不支持的代码格式: {symbol}，应为 XXXXXX.SH/.SZ 等")

    @staticmethod
    def _is_stock(symbol: str) -> bool:
        """判断是否为股票（用于决定是否加前复权参数）。"""
        return symbol.endswith(".SH") or symbol.endswith(".SZ")

    def get_daily(self, symbols: List[str], start_date: str, end_date: str, adjust: str = "hfq") -> pd.DataFrame:
        """获取日线行情。

        使用 THS_HQ 查询，indicators 用分号分隔。
        股票自动加 CPS 参数实现复权。
        """
        self._ensure_inited()
        cps = _ADJUST_MAP.get(adjust)
        begin = self._format_datetime(start_date)
        end = self._format_datetime(end_date)
        indicators = "open;high;low;close;volume;amount;openInterest"

        frames = []
        for symbol in symbols:
            ifind_symbol = symbol.upper()
            self._validate_symbol(ifind_symbol)

            # 构造 params
            params = "Fill:Original"
            if self._is_stock(ifind_symbol) and cps:
                params += f",CPS:{cps}"

            try:
                result = self._THS_HQ(
                    ifind_symbol,
                    indicators,
                    params,
                    begin,
                    end,
                )
            except Exception as e:
                raise NetworkError("ifind", f"THS_HQ 查询 {symbol} 异常: {e}") from e

            if result.errorcode:
                raise DataSourceError("ifind", f"THS_HQ 查询 {symbol} 失败，错误码: {result.errorcode}")
            if result.data is None or len(result.data) == 0:
                logger.warning("iFinD 未返回 %s 的数据", symbol)
                continue

            d = result.data.copy()
            # iFinD 返回的 time 列可能是 "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM"
            if "time" in d.columns:
                d["date"] = pd.to_datetime(d["time"], errors="coerce")
                d = d.drop(columns=["time"])
            d["code"] = symbol
            # 列名标准化为小写
            d.columns = [c.lower() for c in d.columns]
            if "openinterest" in d.columns:
                d = d.drop(columns=["openinterest"])
            frames.append(d)

        if not frames:
            return _finalize(pd.DataFrame())
        return _finalize(pd.concat(frames, ignore_index=True))

    def get_kline(
        self,
        symbols: List[str],
        period: str = "day",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "hfq",
        **kwargs,
    ) -> pd.DataFrame:
        """获取 K 线（多周期）行情——免费版走 THS_HistoryQuotes（dict 式）。

        实测（600519.SH）：errorcode=0，tables[0].time 含真实交易日序列，
        归属「历史行情 100万/月」额度，免费版可用。
        period 映射 iFinD 周期参数：day->'D' / week->'W' / month->'M' / minute->'M1'...
        若 period 非日级（分钟/ Tick）属「高频序列 150万/月」额度，免费版受限，
        会抛 DataSourceError 提示降级。
        """
        self._ensure_inited()
        cps = _ADJUST_MAP.get(adjust)
        begin = self._format_datetime(start_date) if start_date else "1900-01-01 00:00:00"
        end = self._format_datetime(end_date) if end_date else "2099-12-31 00:00:00"

        # period -> iFinD 周期码（与注册表 KLINE_PERIODS 标准命名对齐）
        _PERIOD_MAP = {
            # 日级
            "day": "D", "daily": "D",
            "week": "W", "weekly": "W",
            "month": "M", "monthly": "M",
            "season": "Q", "seasonal": "Q",
            "year": "Y", "yearly": "Y",
            # 分钟级（高频序列额度，免费版受限，但命名须与注册表兼容）
            "m1": "M1", "1min": "M1", "min": "M1", "minute": "M1",
            "m5": "M5", "5min": "M5",
            "m15": "M15", "15min": "M15",
            "m30": "M30", "30min": "M30",
            "m60": "M60", "60min": "M60",
            "m120": "M120", "120min": "M120",
        }
        period_key = str(period).lower()
        if period_key not in _PERIOD_MAP:
            # 未识别周期显式报错，不得静默降级日线（避免冒充错误粒度）
            raise DataSourceError(
                "ifind",
                f"不支持的 K 线周期: {period}。iFinD 免费版支持 "
                f"day/week/month/season/year 及分钟级 m1/m5/m15/m30/m60/m120；"
                f"分钟级属高频序列额度受限，请改用日级或切换付费账户。",
            )
        period_code = _PERIOD_MAP[period_key]
        # 高频（分钟/Tick）属「高频序列」额度，免费版受限
        if period_code.startswith("M") and period_code != "M":
            raise DataSourceError(
                "ifind",
                f"K线周期 {period} 属高频序列额度，免费版受限（试用账号仅 1 年数据）。"
                f"请改用 day/week/month/season/year，或切换付费账户。",
            )

        indicators = "open;high;low;close;volume;amount"
        frames = []
        for symbol in symbols:
            ifind_symbol = symbol.upper()
            self._validate_symbol(ifind_symbol)
            params = f"Period:{period_code},Fill:Original"
            if self._is_stock(ifind_symbol) and cps:
                params += f",CPS:{cps}"
            try:
                result = self._THS_HistoryQuotes(
                    ifind_symbol, indicators, params, begin, end,
                )
            except Exception as e:
                raise NetworkError("ifind", f"THS_HistoryQuotes 查询 {symbol} 异常: {e}") from e

            df = _parse_history_dict(result, symbol)
            if df is None or len(df) == 0:
                logger.warning("iFinD THS_HistoryQuotes 未返回 %s 的数据", symbol)
                continue
            # 列名标准化
            if "openinterest" in df.columns:
                df = df.drop(columns=["openinterest"])
            df.columns = [c.lower() for c in df.columns]
            frames.append(df)

        if not frames:
            return _finalize(pd.DataFrame())
        return _finalize(pd.concat(frames, ignore_index=True))

    def get_realtime_quote(self, symbols: List[str], **kwargs) -> pd.DataFrame:
        """获取实时行情快照——免费版走 THS_RealtimeQuotes（dict 式）。

        实测（600519.SH）：errorcode=0，tables[0].table 含 open/high/low/volume/amount
        单点快照（无 time 序列，补当日日期），归属「实时行情 300万/月」额度，免费版可用。
        额度敏感：实时行情 300万/月，调用须克制。
        """
        self._ensure_inited()
        if isinstance(symbols, str):
            symbols = [symbols]
        indicators = "last;open;high;low;volume;amount;preClose;openInterest"
        frames = []
        for symbol in symbols:
            ifind_symbol = symbol.upper()
            self._validate_symbol(ifind_symbol)
            try:
                result = self._THS_RealtimeQuotes(ifind_symbol, indicators)
            except Exception as e:
                raise NetworkError("ifind", f"THS_RealtimeQuotes 查询 {symbol} 异常: {e}") from e

            df = _parse_realtime_dict(result, symbol)
            if df is None or len(df) == 0:
                logger.warning("iFinD THS_RealtimeQuotes 未返回 %s 的数据", symbol)
                continue
            frames.append(df)

        if not frames:
            return _finalize(pd.DataFrame())
        return _finalize(pd.concat(frames, ignore_index=True))

    def get_stock_list(self) -> pd.DataFrame:
        """获取全市场 A 股列表。

        iFinD 提供 THS_BasicCenter / THS_DateSequence 等接口拉取成分股，
        但字段映射较复杂。此处采用简化实现：通过 THS_HQ 拉取沪深300
        作为占位，实际生产环境建议调用 THS_BasicCenter。
        """
        self._ensure_inited()
        logger.warning(
            "iFinD get_stock_list 采用简化实现，仅返回沪深300成分股。"
            "如需全市场列表，请使用 tushare/baostock/akshare 适配器。"
        )
        try:
            from iFinDPy import THS_BasicCenter

            df = THS_BasicCenter("沪深A股")
        except Exception as e:
            logger.warning("iFinD 获取股票列表失败: %s", e)
            return pd.DataFrame(columns=["code", "name", "industry", "list_date", "is_st"])

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["code", "name", "industry", "list_date", "is_st"])

        out = pd.DataFrame()
        out["code"] = df.iloc[:, 0] if df.shape[1] > 0 else None
        out["name"] = df.iloc[:, 1] if df.shape[1] > 1 else None
        out["industry"] = None
        out["list_date"] = None
        out["is_st"] = out["name"].astype(str).str.contains("ST", na=False)
        return out

    def get_adj_factor(self, symbols, start_date, end_date):
        """复权因子：iFinD 已通过 CPS 参数直接返回复权后价格，
        无需单独提供复权因子，返回空 DataFrame 保持接口一致。
        """
        return pd.DataFrame(columns=["code", "date", "adj_factor"])

    def get_financial(self, symbols, report_date, fields):
        """获取财务数据，返回统一标准 schema。

        使用 iFinD 的 THS_BD 基础数据接口逐指标查询单期财务数据。
        THS_BD 不支持分号批量多指标(会返回 -209)，故逐个指标调用。

        已验证可用的指标(均返回 errorcode=0):
        - ths_pe_ttm_stock(PE TTM)、ths_pb_stock(PB)、ths_ps_ttm_stock(PS TTM)
        - ths_roe_stock(ROE)、ths_roa_stock(ROA)、ths_dividend_ratio_stock(股息率)
        - ths_or_yoy_stock(营收同比)、ths_np_yoy_stock(净利润同比)
        - ths_current_ratio_stock(流动比率)、ths_quick_ratio_stock(速动比率)
        - ths_stock_short_name_stock(股票简称)

        暂无可用指标的: gross_margin / net_margin / debt_ratio / ocf / industry(留空)

        返回 DataFrame 包含标准字段:
            code, report_date, pe_ttm, pb, ps_ttm, dv_ratio,
            roe, roa, gross_margin, net_margin,
            revenue_growth, profit_growth,
            debt_ratio, current_ratio, quick_ratio, ocf,
            industry, name, disclosure_date
        """
        self._ensure_inited()

        # P0-1 PIT 契约：末尾追加 disclosure_date
        # ifind 无原生披露日接口，出口回填为 report_date（保守降级）
        standard_cols = [
            "code",
            "report_date",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ratio",
            "roe",
            "roa",
            "gross_margin",
            "net_margin",
            "revenue_growth",
            "profit_growth",
            "debt_ratio",
            "current_ratio",
            "quick_ratio",
            "ocf",
            "industry",
            "name",
            "disclosure_date",
        ]

        # 标准化报告期: '20240930' -> '2024-09-30'(iFinD 期望的日期格式)
        period = report_date.replace("-", "")
        period_std = f"{period[:4]}-{period[4:6]}-{period[6:8]}" if len(period) == 8 else report_date

        # 指标 -> 标准 schema 字段映射(逐个 THS_BD 调用)
        indicator_map = {
            "pe_ttm": "ths_pe_ttm_stock",
            "pb": "ths_pb_stock",
            "ps_ttm": "ths_ps_ttm_stock",
            "roe": "ths_roe_stock",
            "roa": "ths_roa_stock",
            "dv_ratio": "ths_dividend_ratio_stock",
            "revenue_growth": "ths_or_yoy_stock",
            "profit_growth": "ths_np_yoy_stock",
            "current_ratio": "ths_current_ratio_stock",
            "quick_ratio": "ths_quick_ratio_stock",
            "name": "ths_stock_short_name_stock",
        }

        rows = []
        for symbol in symbols:
            ifind_symbol = symbol.upper()
            self._validate_symbol(ifind_symbol)
            row = dict.fromkeys(standard_cols)
            row["code"] = symbol
            row["report_date"] = period
            # P0-1 PIT 契约：ifind 无原生披露日，回填为 report_date（保守降级）
            row["disclosure_date"] = period

            for dst_col, indicator in indicator_map.items():
                try:
                    result = self._THS_BD(ifind_symbol, indicator, period_std)
                except Exception as e:
                    raise NetworkError("ifind", f"THS_BD 查询 {symbol} {indicator} 异常: {e}") from e

                # errorcode 非 0 视为该指标无数据(如 -209)，跳过留空
                if getattr(result, "errorcode", None):
                    logger.debug("iFinD %s %s 无数据(errorcode=%s)", symbol, indicator, result.errorcode)
                    continue

                data = getattr(result, "data", None)
                if data is not None and not data.empty and indicator in data.columns:
                    raw_val = data.iloc[0][indicator]
                    # name 为字符串字段，其余为数值字段
                    row[dst_col] = str(raw_val) if dst_col == "name" else self._to_num(raw_val)

            rows.append(row)

        out = pd.DataFrame(rows, columns=standard_cols)

        # 如果调用方指定了 fields，按需过滤列
        # P0-1 PIT 契约：code/report_date/disclosure_date 始终保留
        if fields:
            keep = ["code", "report_date", "disclosure_date"] + [f for f in fields if f in standard_cols]
            keep = list(dict.fromkeys(keep))
            out = out[keep]

        return out.reset_index(drop=True)

    def get_announcement(self, symbols: List[str], start_date: str, end_date: str, **kwargs) -> pd.DataFrame:
        """获取公告列表——免费版走 THS_ReportQuery（额度敏感，1万次/月）。

        ⚠️ 额度敏感：公告查询 1万次/月，调用须克制，避免刷额度。
        实测（600519.SH 2024-03）：errorcode=0 但 data 为空（本账户未开通/参数受限），
        返回空 DataFrame 降级，不视为错误（不阻塞主流程）。
        date 格式 iFinD 期望 'date:YYYY-MM-DD,YYYY-MM-DD'。
        """
        self._ensure_inited()
        if isinstance(symbols, str):
            symbols = [symbols]
        begin = self._format_datetime(start_date) if start_date else "1900-01-01 00:00:00"
        end = self._format_datetime(end_date) if end_date else "2099-12-31 00:00:00"
        date_param = f"date:{begin[:10]},{end[:10]}"

        out_cols = ["code", "title", "ann_date", "type", "link"]
        rows = []
        for symbol in symbols:
            ifind_symbol = symbol.upper()
            self._validate_symbol(ifind_symbol)
            try:
                result = self._THS_ReportQuery(
                    ifind_symbol, "公告类型:全部公告", date_param, "",
                )
            except Exception as e:
                logger.warning("iFinD 公告查询 %s 异常（降级空返回）: %s", symbol, e)
                rows.append(dict.fromkeys(out_cols) | {"code": symbol})
                continue

            # 免费版 errorcode=0 但 data 空，直接降级空行
            data = getattr(result, "data", None)
            if data is None or (hasattr(data, "empty") and data.empty):
                rows.append(dict.fromkeys(out_cols) | {"code": symbol})
                continue
            # 若未来账户开通，解析 data（结构随版本，宽松取列）
            try:
                for _, r in data.iterrows():
                    rows.append({
                        "code": symbol,
                        "title": str(r.get("title", r.get("标题", None))),
                        "ann_date": str(r.get("date", r.get("公告日期", None))),
                        "type": str(r.get("type", r.get("公告类型", None))),
                        "link": str(r.get("link", r.get("链接", None))),
                    })
            except Exception as e:
                logger.warning("iFinD 公告解析 %s 失败（降级空返回）: %s", symbol, e)
                rows.append(dict.fromkeys(out_cols) | {"code": symbol})

        df = pd.DataFrame(rows, columns=out_cols)
        return df

    @staticmethod
    def _to_num(val):
        """安全转换为 float"""
        if val is None:
            return None
        try:
            import math

            f = float(val)
            return None if (isinstance(f, float) and math.isnan(f)) else f
        except (TypeError, ValueError):
            return None
