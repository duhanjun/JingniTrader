"""
BaoStock 数据源适配器
免费、无需注册，适合快速验证
"""

import logging
import pandas as pd
import baostock as bs
from typing import List
from scripts.base.base_data_provider import BaseDataProvider


class BaostockAdapter(BaseDataProvider):
    """BaoStock 适配器"""

    # 实测确认（REQ-2026-0816-source-capability-verify，锚点 600519）：
    # get_stock_list✅(8901行) / get_adj_factor 存在(返回空DF，baostock 内置复权) /
    # daily✅ / kline✅(d/w/m/5/15/30/60) / financial 方法存在(季频) /
    # get_dividend✅(query_dividend_data) / get_trade_calendar✅(query_trade_dates) /
    # get_forecast✅(query_forecast_report) 已补实装（Damon 指示，任务 #32）。
    SUPPORTED_DATA_TYPES = {
        "daily",
        "kline",
        "financial",
        "basic_stock_list",
        "basic_adjust_factor",
        "ref_dividend",
        "basic_trade_calendar",
        "ref_forecast",
    }

    def __init__(self):
        self._logged_in = False
        self._logger = logging.getLogger(self.__class__.__name__)

    def _ensure_login(self):
        if not self._logged_in:
            bs.login()
            self._logged_in = True

    def _format_code(self, code: str) -> str:
        """将 000001.SZ 转为 sh.600000 或 sz.000001"""
        if "." in code:
            ticker, exchange = code.split(".")
            if exchange.upper() == "SH":
                return f"sh.{ticker}"
            else:
                return f"sz.{ticker}"
        return code

    def get_stock_list(self) -> pd.DataFrame:
        """
        获取全市场股票列表，并尝试附加行业分类。

        使用 bs.query_stock_basic() 获取基础信息，
        使用 bs.query_stock_industry() 获取行业（申万）。
        """
        self._ensure_login()
        # 获取全量股票数据
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            return pd.DataFrame()
        data = []
        while rs.next():
            data.append(rs.get_row_data())
        df = pd.DataFrame(data, columns=rs.fields)
        # 映射为统一格式
        df["code"] = df["code"].apply(lambda x: x.replace("sh.", "SH.").replace("sz.", "SZ."))
        df["name"] = df["code_name"]
        df["is_st"] = df["code_name"].str.contains("ST", na=False)

        # 映射 list_date：baostock 返回的上市日期字段为 ipoDate
        if "list_date" not in df.columns:
            if "ipoDate" in df.columns:
                df["list_date"] = df["ipoDate"]
            else:
                df["list_date"] = ""

        # 尝试获取行业（申万）
        try:
            ind_rs = bs.query_stock_industry()
            if ind_rs is not None and ind_rs.error_code == "0":
                ind_data = []
                while ind_rs.next():
                    ind_data.append(ind_rs.get_row_data())
                if ind_data:
                    ind_df = pd.DataFrame(ind_data, columns=ind_rs.fields)
                    # 统一 code 格式
                    if "code" in ind_df.columns:
                        ind_df["code"] = ind_df["code"].apply(lambda x: x.replace("sh.", "SH.").replace("sz.", "SZ."))
                        # 字段重命名: industry / industryClassification
                        rename_map = {}
                        if "industry" in ind_df.columns:
                            rename_map["industry"] = "industry"
                        elif "industryClassification" in ind_df.columns:
                            rename_map["industryClassification"] = "industry"
                        # 仅保留 code 与 industry 列
                        cols_to_keep = ["code"] + (
                            ["industry"]
                            if "industry" in ind_df.columns
                            else (["industryClassification"] if "industryClassification" in ind_df.columns else [])
                        )
                        if len(cols_to_keep) > 1:
                            ind_df = ind_df[cols_to_keep].rename(columns=rename_map)
                            df = df.merge(ind_df, on="code", how="left")
        except Exception as e:
            self._logger.warning(f"获取行业数据失败: {e}")

        if "industry" not in df.columns:
            df["industry"] = ""

        return df

    def get_daily(self, symbols: List[str], start_date: str, end_date: str, adjust: str = "hfq") -> pd.DataFrame:
        self._ensure_login()
        frames = []
        for code in symbols:
            bs_code = self._format_code(code)
            # 根据复权方式选择字段
            if adjust == "hfq":
                fields = "date,open,high,low,close,preclose,volume,amount,turn,adjustflag,tradestatus,isST"
            else:
                fields = "date,open,high,low,close,preclose,volume,amount,turn,adjustflag,tradestatus,isST"
            # 修改日期格式为 YYYY-MM-DD
            start = start_date
            end = end_date
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="2" if adjust == "qfq" else "1",  # 1:后复权, 2:前复权
            )
            if rs is None or rs.error_code != "0":
                continue
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            if not data:
                continue
            df = pd.DataFrame(data, columns=rs.fields)
            # 类型转换
            numeric_cols = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "isST"]
            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["code"] = code
            df["date"] = pd.to_datetime(df["date"])
            df["is_st"] = df["isST"].astype(bool) if "isST" in df.columns else False
            # 涨跌停标记：BaoStock未直接提供，需自行计算，此处留空
            df["is_limit_up"] = False
            df["is_limit_down"] = False
            df["listed_days"] = None
            # 映射列名
            df = df.rename(columns={"preclose": "pre_close", "turn": "turnover_rate"})
            frames.append(df)

        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames, ignore_index=True)
        return result.sort_values(["code", "date"]).reset_index(drop=True)

    def get_adj_factor(self, symbols, start_date, end_date):
        # BaoStock 已内置复权，无需额外因子
        return pd.DataFrame()

    # ── 全粒度 K 线（REQ-2026-08-15 行情全粒度接入）────────────
    # baostock query_history_k_data_plus 原生支持的 frequency：
    #   d=日线, w=周线, m=月线, 5/15/30/60=分钟线（无 1 分钟、无季线/年线）。
    # 实测确认（2026-08-15，锚点 600519）：week 返回 31 周线行、m5 返回 7152 条 5 分钟行。
    _KLINE_FREQ: dict = {
        "day": "d",
        "week": "w",
        "month": "m",
        "m5": "5",
        "m15": "15",
        "m30": "30",
        "m60": "60",
    }

    def get_kline(
        self,
        symbols: List[str],
        period: str = "day",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取任意周期 K 线（baostock 通道，真实支持 day/week/month/m5/m15/m30/m60）。

        m1/season/year 底层库不支持 → 抛 InvalidParameterError 触发降级。
        归一化到标准化 K 线契约（code/date/open/high/low/close/volume/amount）。
        day 周期经基类映射到 get_daily（复用其完整字段），其余周期走本实现。
        """
        if period in ("day", ""):
            return self.get_daily(symbols, start_date, end_date, adjust=adjust)

        freq = self._KLINE_FREQ.get(period)
        if freq is None:
            from scripts.errors import InvalidParameterError

            raise InvalidParameterError(
                "baostock",
                f"baostock 不支持周期 {period}（仅 d/w/m/5/15/30/60）；m1/season/year 请降级至其他源。",
            )

        self._ensure_login()
        adj_flag = "2" if adjust == "qfq" else "1"  # 1:后复权 2:前复权
        frames = []
        for code in symbols:
            bs_code = self._format_code(code)
            fields = "date,open,high,low,close,volume,amount"
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start_date or "1990-01-01",
                end_date=end_date or "2030-12-31",
                frequency=freq,
                adjustflag=adj_flag,
            )
            if rs is None or rs.error_code != "0":
                continue
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=rs.fields)
            for col in ("open", "high", "low", "close", "volume", "amount"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["code"] = code
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            # 标准化 K 线契约列（amount 缺失时留 NaN）
            df = df.rename(columns={})  # 字段名已对齐
            frames.append(df[["code", "date", "open", "high", "low", "close", "volume", "amount"]])

        if not frames:
            from scripts.errors import DataNotFoundError

            raise DataNotFoundError("baostock", f"baostock 未返回 {symbols} 周期 {period} 的 K 线数据")
        result = pd.concat(frames, ignore_index=True)
        return result.sort_values(["code", "date"]).reset_index(drop=True)

    def get_financial(self, symbols, report_date, fields):
        """
        获取财务数据，返回统一标准 schema。

        使用:
        - bs.query_growth_data(): 成长能力（营收增速/利润增速）
        - bs.query_profit_data(): 盈利能力（ROE/ROA/毛利率/净利率）
        - bs.query_balance_data(): 偿债能力（资产负债率/流动比率/速动比率）
        - bs.query_cash_flow_data(): 现金流（经营现金流）
        - bs.query_stock_industry(): 行业
        - bs.query_stock_basic(): 股票名称

        返回 DataFrame 包含标准字段:
            code, report_date, pe_ttm, pb, ps_ttm, dv_ratio,
            roe, roa, gross_margin, net_margin,
            revenue_growth, profit_growth,
            debt_ratio, current_ratio, quick_ratio, ocf,
            industry, name, disclosure_date
        """
        self._ensure_login()

        # P0-1 PIT 契约：末尾追加 disclosure_date
        # baostock 无原生披露日接口，出口回填为 report_date（保守降级）
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

        # BaoStock 报告期格式 YYYY-mm-dd
        period = report_date.replace("-", "")
        period_std = f"{period[:4]}-{period[4:6]}-{period[6:8]}" if len(period) == 8 else report_date

        # 1) 行业映射（一次性获取）
        industry_map = {}
        try:
            ind_rs = bs.query_stock_industry()
            if ind_rs is not None and ind_rs.error_code == "0":
                while ind_rs.next():
                    row = ind_rs.get_row_data()
                    # code, industry (字段顺序依据 BaoStock)
                    code_raw = row[0] if len(row) > 0 else ""
                    ind_val = row[3] if len(row) > 3 else (row[1] if len(row) > 1 else "")
                    code_norm = code_raw.replace("sh.", "SH.").replace("sz.", "SZ.")
                    industry_map[code_norm] = ind_val
        except Exception as e:
            self._logger.warning(f"获取行业失败: {e}")

        # 2) 股票名称映射（一次性获取）
        name_map = {}
        try:
            basic_rs = bs.query_stock_basic()
            if basic_rs is not None and basic_rs.error_code == "0":
                while basic_rs.next():
                    row = basic_rs.get_row_data()
                    # fields: code, code_name, ipoDate, outDate, type, status
                    code_raw = row[0] if len(row) > 0 else ""
                    code_name = row[1] if len(row) > 1 else ""
                    code_norm = code_raw.replace("sh.", "SH.").replace("sz.", "SZ.")
                    name_map[code_norm] = code_name
        except Exception as e:
            self._logger.warning(f"获取股票名称失败: {e}")

        rows = []
        for code in symbols:
            bs_code = self._format_code(code)
            row = dict.fromkeys(standard_cols)
            row["code"] = code
            row["report_date"] = period.replace("-", "")
            # P0-1 PIT 契约：baostock 无原生披露日，回填为 report_date（保守降级）
            row["disclosure_date"] = period.replace("-", "")
            row["industry"] = industry_map.get(code, "")
            row["name"] = name_map.get(code, "")

            # 成长能力
            try:
                rs = bs.query_growth_data(
                    code=bs_code, year=period_std[:4], quarter=self._quarter_from_period(period_std)
                )
                if rs is not None and rs.error_code == "0":
                    data = []
                    while rs.next():
                        data.append(rs.get_row_data())
                    if data:
                        g = dict(zip(rs.fields, data[-1], strict=False))
                        # YOYEquity YOYAsset YOYNI YOYEPSBasic
                        row["revenue_growth"] = self._to_num(g.get("YOYAsset"))
                        row["profit_growth"] = self._to_num(g.get("YOYNI"))
            except Exception as e:
                self._logger.debug(f"获取 {code} 成长数据失败: {e}")

            # 盈利能力
            try:
                rs = bs.query_profit_data(
                    code=bs_code, year=period_std[:4], quarter=self._quarter_from_period(period_std)
                )
                if rs is not None and rs.error_code == "0":
                    data = []
                    while rs.next():
                        data.append(rs.get_row_data())
                    if data:
                        p = dict(zip(rs.fields, data[-1], strict=False))
                        # roeAvg npMargin gpMargin netProfit isCutNetProfit
                        row["roe"] = self._to_num(p.get("roeAvg"))
                        # BaoStock 不直接返回 ROA，留空
                        row["roa"] = None
                        row["net_margin"] = self._to_num(p.get("npMargin"))
                        row["gross_margin"] = self._to_num(p.get("gpMargin"))
            except Exception as e:
                self._logger.debug(f"获取 {code} 盈利数据失败: {e}")

            # 偿债能力
            try:
                rs = bs.query_balance_data(
                    code=bs_code, year=period_std[:4], quarter=self._quarter_from_period(period_std)
                )
                if rs is not None and rs.error_code == "0":
                    data = []
                    while rs.next():
                        data.append(rs.get_row_data())
                    if data:
                        b = dict(zip(rs.fields, data[-1], strict=False))
                        # currentRatio quickRatio cashRatioYOYAsset liabilityToAsset
                        row["current_ratio"] = self._to_num(b.get("currentRatio"))
                        row["quick_ratio"] = self._to_num(b.get("quickRatio"))
                        row["debt_ratio"] = self._to_num(b.get("liabilityToAsset"))
            except Exception as e:
                self._logger.debug(f"获取 {code} 偿债数据失败: {e}")

            # 现金流
            try:
                rs = bs.query_cash_flow_data(
                    code=bs_code, year=period_std[:4], quarter=self._quarter_from_period(period_std)
                )
                if rs is not None and rs.error_code == "0":
                    data = []
                    while rs.next():
                        data.append(rs.get_row_data())
                    if data:
                        dict(zip(rs.fields, data[-1], strict=False))
                        # CAToAsset CFOToOR CFOToOR CFOToGr CFOToGr CAToAsset
                        # BaoStock 不直接返回绝对额 OCF，仅返回比率；ocf 留空
                        row["ocf"] = None
            except Exception as e:
                self._logger.debug(f"获取 {code} 现金流数据失败: {e}")

            # 估值指标：通过 query_history_k_data_plus 取最近交易日行情的 peTTM/pbMRQ/psTTM
            # BaoStock 日线接口支持 peTTM/pbMRQ/psTTM/pcfNcfTTM 字段
            # 取 period 对应报告期当日（或最近交易日）的估值
            try:
                # 取 period 当天前后 10 天的日线数据（避免非交易日）
                # 构造查询区间（前 10 天到 period 当天）
                from datetime import datetime, timedelta

                pdate = datetime.strptime(period_std, "%Y-%m-%d")
                start_d = (pdate - timedelta(days=15)).strftime("%Y-%m-%d")
                end_d = period_std
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM",
                    start_date=start_d,
                    end_date=end_d,
                    frequency="d",
                )
                if rs is not None and rs.error_code == "0":
                    data = []
                    while rs.next():
                        data.append(rs.get_row_data())
                    if data:
                        v = dict(zip(rs.fields, data[-1], strict=False))  # 取最近一条
                        row["pe_ttm"] = self._to_num(v.get("peTTM"))
                        row["pb"] = self._to_num(v.get("pbMRQ"))
                        row["ps_ttm"] = self._to_num(v.get("psTTM"))
                        # pcfNcfTTM 为现金流市盈率，非股息率，dv_ratio 仍留空
                        row["dv_ratio"] = None
            except Exception as e:
                self._logger.debug(f"获取 {code} 估值数据失败: {e}")
                row["pe_ttm"] = None
                row["pb"] = None
                row["ps_ttm"] = None
                row["dv_ratio"] = None
            else:
                # 若上面未赋值，确保为 None
                for k in ("pe_ttm", "pb", "ps_ttm", "dv_ratio"):
                    if row.get(k) is None:
                        row[k] = None

            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=standard_cols)

        out = pd.DataFrame(rows, columns=standard_cols)

        # 如果调用方指定了 fields，按需过滤列
        # P0-1 PIT 契约：code/report_date/disclosure_date 始终保留
        if fields:
            keep = ["code", "report_date", "disclosure_date"] + [f for f in fields if f in standard_cols]
            keep = list(dict.fromkeys(keep))
            out = out[keep]

        return out.reset_index(drop=True)

    # ── 参考数据 reference（任务 #32：补 baostock 官方 API 三项）────────
    # 引擎 _build_kwargs_for_type 对 ref_dividend/basic_trade_calendar/ref_forecast
    # 返回 {}，故以下方法以 **kwargs 兜底，互不依赖引擎传参。
    def get_dividend(self, symbols: List[str] = None, **kwargs) -> pd.DataFrame:
        """分红派息（baostock query_dividend_data）。

        官方契约：query_dividend_data(code, year, yearType)
          - yearType: 公告年份(1)/预案公布年份(2)/分红年份(3)
          - 返回列：code,dividendPeriod,year,dividendType,cashDividendRatio,
                    surplusReserveToCapitalRatio,capitalSurplusReserveRatio
        归一化到标准分红契约：code/date(报告期)/dividend_type/cash_dividend_ratio。
        失败/无数据抛 DataNotFoundError 触发降级，不静默返回空。
        """
        from scripts.errors import DataNotFoundError

        symbols = symbols or []
        if not symbols:
            raise DataNotFoundError("baostock", "get_dividend 需要 symbols 参数")

        frames = []
        for code in symbols:
            bs_code = self._format_code(code)
            # 取最近 5 个自然年（yearType=1 公告年份）
            from datetime import datetime

            cur_year = datetime.now().year
            for year in range(cur_year, cur_year - 5, -1):
                try:
                    rs = bs.query_dividend_data(code=bs_code, year=year, yearType="1")
                except Exception as e:
                    self._logger.debug(f"baostock query_dividend_data {code} {year} 失败: {e}")
                    continue
                if rs is None or rs.error_code != "0":
                    continue
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    continue
                df = pd.DataFrame(rows, columns=rs.fields)
                df["code"] = code
                # dividendPeriod 形如 '2023年中报' → 取年份前缀作为报告期
                period_raw = df.get("dividendPeriod", pd.Series(["" for _ in range(len(df))]))
                df["date"] = period_raw.astype(str).str.extract(r"(\d{4})")[0].fillna("")
                df = df.rename(
                    columns={
                        "dividendType": "dividend_type",
                        "cashDividendRatio": "cash_dividend_ratio",
                    }
                )
                keep = ["code", "date", "dividend_type", "cash_dividend_ratio"]
                keep = [c for c in keep if c in df.columns]
                frames.append(df[keep])

        if not frames:
            raise DataNotFoundError("baostock", f"baostock 未返回 {symbols} 的分红派息数据")
        return pd.concat(frames, ignore_index=True)

    def get_trade_calendar(self, start_date: str = "", end_date: str = "", **kwargs) -> pd.DataFrame:
        """交易日历（baostock query_trade_dates）。

        官方契约：query_trade_dates(start_date, end_date)
          - 返回列：date, is_trading_day（'1'=交易日）
        归一化到标准日历契约：date/is_trading/month_end/quarter_end/year_end。
        失败/无数据抛 DataNotFoundError 触发降级。
        """
        from scripts.errors import DataNotFoundError

        if not start_date:
            start_date = "1990-01-01"
        if not end_date:
            from datetime import datetime

            end_date = datetime.now().strftime("%Y-%m-%d")

        self._ensure_login()
        try:
            rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
        except Exception as e:
            raise DataNotFoundError("baostock", f"baostock 交易日历查询失败: {e}", original=e)

        if rs is None or rs.error_code != "0":
            raise DataNotFoundError("baostock", "baostock 交易日历返回错误")

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            raise DataNotFoundError("baostock", "baostock 交易日历返回空")

        df = pd.DataFrame(rows, columns=rs.fields)
        df["date"] = df["date"].astype(str)
        df["is_trading"] = df.get("is_trading_day", pd.Series(["0"] * len(df))).astype(str) == "1"
        # baostock 仅给逐日交易标记，月末/季末/年末标记由本地推算
        dates = pd.to_datetime(df["date"], errors="coerce")
        df["month_end"] = dates.dt.is_month_end.fillna(False) & df["is_trading"]
        df["quarter_end"] = dates.dt.is_quarter_end.fillna(False) & df["is_trading"]
        df["year_end"] = dates.dt.is_year_end.fillna(False) & df["is_trading"]
        return df[["date", "is_trading", "month_end", "quarter_end", "year_end"]]

    def get_forecast(self, symbols: List[str] = None, **kwargs) -> pd.DataFrame:
        """业绩预告（baostock query_forecast_report）。

        官方契约：query_forecast_report(code, start_date, end_date)
          - 返回列：code,profitHalfYear,profitYear,profitQuarter,
                    profitMean,profitMin,profitMax, ...（业绩预告区间）
        归一化到标准预告契约：code/report_date/profit_mean/profit_min/profit_max。
        失败/无数据抛 DataNotFoundError 触发降级。
        """
        from scripts.errors import DataNotFoundError

        symbols = symbols or []
        if not symbols:
            raise DataNotFoundError("baostock", "get_forecast 需要 symbols 参数")

        # 默认查询最近两年区间
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

        frames = []
        for code in symbols:
            bs_code = self._format_code(code)
            try:
                rs = bs.query_forecast_report(code=bs_code, start_date=start_date, end_date=end_date)
            except Exception as e:
                self._logger.debug(f"baostock query_forecast_report {code} 失败: {e}")
                continue
            if rs is None or rs.error_code != "0":
                continue
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=rs.fields)
            df["code"] = code
            df = df.rename(
                columns={
                    "profitMean": "profit_mean",
                    "profitMin": "profit_min",
                    "profitMax": "profit_max",
                }
            )
            # 报告期：取 statDate 或 date 列，缺失则留空
            if "statDate" in df.columns:
                df["report_date"] = df["statDate"].astype(str)
            elif "date" in df.columns:
                df["report_date"] = df["date"].astype(str)
            else:
                df["report_date"] = ""
            keep = ["code", "report_date", "profit_mean", "profit_min", "profit_max"]
            keep = [c for c in keep if c in df.columns]
            frames.append(df[keep])

        if not frames:
            raise DataNotFoundError("baostock", f"baostock 未返回 {symbols} 的业绩预告数据")
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _quarter_from_period(period_std: str) -> int:
        """从 'YYYY-MM-DD' 报告期推断季度: 1/2/3/4"""
        try:
            month = int(period_std[5:7])
            return (month - 1) // 3 + 1
        except Exception:
            return 1

    @staticmethod
    def _to_num(val):
        """安全转换为 float"""
        if val is None or val == "":
            return None
        try:
            return float(val)
        except Exception:
            return None
