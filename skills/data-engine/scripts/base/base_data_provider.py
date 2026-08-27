"""
数据提供者抽象基类
所有数据源适配器必须实现此接口
"""

from abc import ABC, abstractmethod
from typing import List, Set
import pandas as pd


class BaseDataProvider(ABC):
    """A股数据提供者基类

    子类通过类属性 SUPPORTED_DATA_TYPES 声明支持的数据类型。
    未声明的类型，降级框架会自动跳过该适配器。
    """

    # 子类覆盖此集合，声明该适配器支持的数据类型
    # 可选值：daily, financial, capital_flow, dragon_tiger, shareholder
    SUPPORTED_DATA_TYPES: Set[str] = {"daily", "financial"}

    def supports(self, data_type: str) -> bool:
        """查询该适配器是否支持某数据类型"""
        return data_type in self.SUPPORTED_DATA_TYPES

    def fetch(self, data_type: str, **kwargs) -> pd.DataFrame:
        """统一数据获取入口，按 data_type 分派到对应方法

        参数:
            data_type: 数据类型标识（daily/financial/capital_flow/...）
            **kwargs: 传递给对应方法的参数
        返回:
            DataFrame
        """
        # 延迟导入，避免循环引用
        from scripts.data_types import DATA_TYPES

        meta = DATA_TYPES.get(data_type)
        if not meta:
            raise NotImplementedError(f"未知数据类型: {data_type}")
        if not self.supports(data_type):
            raise NotImplementedError(f"{self.__class__.__name__} 不支持数据类型 {data_type}")
        method = getattr(self, meta.method_name, None)
        if method is None:
            raise NotImplementedError(f"{self.__class__.__name__} 未实现 {meta.method_name}")
        return method(**kwargs)

    @abstractmethod
    def get_daily(self, symbols: List[str], start_date: str, end_date: str, adjust: str = "hfq") -> pd.DataFrame:
        """
        获取日线行情数据

        参数:
            symbols: 股票代码列表，如 ['000001.SZ', '600000.SH']
            start_date: 开始日期 YYYYMMDD 或 YYYY-MM-DD
            end_date: 结束日期
            adjust: 复权方式 'hfq'(后复权), 'qfq'(前复权), ''(不复权)

        返回:
            DataFrame，必须包含列:
            code, date, open, high, low, close, volume, amount,
            pre_close, change_pct, turnover_rate,
            is_st, is_limit_up, is_limit_down, listed_days
        """
        ...

    def get_kline(
        self,
        symbols: List[str],
        period: str = "day",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """
        获取任意周期 K 线行情（REQ-2026-08-15 行情全粒度接入）。

        默认 period="day" 时映射到 get_daily（向后兼容，不破坏现有调用）。
        子类可覆盖以支持更多周期（分钟/周/月/季/年），并将底层数据归一化到
        标准化 K 线契约（code/date/open/high/low/close/volume/amount）。

        参数:
            symbols: 股票代码列表，如 ['000001.SZ', '600000.SH']
            period: K 线周期，取值见 data_types.KLINE_PERIODS
                    （m1/m5/m15/m30/m60/m120/day/week/month/season/year）
            start_date: 开始日期 YYYYMMDD 或 YYYY-MM-DD（'' = 不裁剪）
            end_date: 结束日期（'' = 不裁剪）
            adjust: 复权方式 'qfq'(前复权), 'hfq'(后复权), ''(不复权)

        返回:
            DataFrame，标准化 K 线契约列：
            code, date, open, high, low, close, volume, amount

        未实现的周期由子类抛 InvalidParameterError（由降级链路由到其他支持该周期的源）。
        """
        # 向后兼容：day 直接复用 get_daily 路径
        if period in ("day", ""):
            return self.get_daily(symbols, start_date, end_date, adjust=adjust)
        # 非 day 周期：基类默认不支持，交由子类覆盖实现
        from scripts.errors import InvalidParameterError

        raise InvalidParameterError(
            self.__class__.__name__,
            f"{self.__class__.__name__} 未实现周期 {period} 的 get_kline（day 已映射到 get_daily；其他周期需子类覆盖）",
        )

    @abstractmethod
    def get_stock_list(self) -> pd.DataFrame:
        """
        获取全市场股票列表及其状态

        返回:
            DataFrame，包含列: code, name, industry, list_date, is_st
        """
        ...

    @abstractmethod
    def get_adj_factor(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取复权因子

        返回:
            DataFrame，包含列: code, date, adj_factor
        """
        ...

    @abstractmethod
    def get_financial(self, symbols: List[str], report_date: str, fields: List[str]) -> pd.DataFrame:
        """
        获取财务数据

        参数:
            symbols: 股票代码列表，如 ['000001.SZ', '600000.SH']
            report_date: 报告期，如 '20240930'（YYYYMMDD）或 '2024-09-30'
            fields: 需要获取的字段列表（可选，适配器可忽略并返回完整标准字段）

        返回:
            DataFrame，每行一只股票一个报告期，标准字段:
            code, report_date, pe_ttm, pb, ps_ttm, dv_ratio (股息率),
            roe, roa, gross_margin (毛利率), net_margin (净利率),
            revenue_growth (营收增速), profit_growth (利润增速),
            debt_ratio (资产负债率), current_ratio (流动比率),
            quick_ratio (速动比率), ocf (经营现金流),
            industry (申万行业), name (股票名称)
        """
        ...
