"""
数据类型注册表

定义所有数据类型及其元数据，用于按数据类型粒度的独立降级。
每种数据类型独立遍历优先级链，互不影响。

分类体系（2026-08-16 对齐行业标准，三阶段实施 P1/P2/P3）：
  - group（数据分组）：basic（基础）/ market（行情）/ financial（财务）/ reference（参考）
  - instrument_types（适用标的类型）：stock（股票，默认）/ index（指数）/ future（期货）/ option（期权）
  旧键 daily/financial/capital_flow/dragon_tiger/shareholder 全部保留，method_name 不变，管线零改动。
  新增 11 个 P0 叶子项元数据（basic_*/market_*/ref_*）：P1 定义元数据，P2 补齐取数实现
  （除 market_kline/market_realtime/financial_report 复用既有通道外，其余 8 项均经 westock CLI 实测落地）。
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DataTypeMeta:
    """数据类型元数据"""

    name: str  # 数据类型标识：daily/financial/capital_flow/...
    display_name: str  # 中文名
    method_name: str  # 适配器方法名：get_daily/get_financial/get_capital_flow/...
    artifact_filename: str  # 落盘文件名
    artifact_key: str  # ctx.artifacts 中的键名
    allow_synthetic: bool  # 是否允许模拟数据兜底
    required: bool  # 是否为管线必需（缺失则报错）
    description: str  # 描述
    # —— 分类体系维度（2026-08-16 对齐行业标准，P1 先行落地）——
    group: str = "market"  # basic/market/financial/reference（数据分组）
    instrument_types: List[str] = field(
        default_factory=lambda: ["stock"]
    )  # 适用标的类型：stock/index/future/option


# ── 行情周期维度（REQ-2026-08-15 行情全粒度接入）──────────────
# 标准化 K 线周期类型。适配器 get_kline(period=...) 接受以下取值；
# 各数据源底层映射见 adapters/*_adapter.py 的 _PERIOD_FREQ 表。
# 实证（2026-08-16 经 npx westock-data-skillhub@1.0.5 kline sh600519 --period <p> 实测，锚点 600519）：
#   westock 真实支持 m1/m5/m15/m30/m60/m120/day/week/month/season/year 共 10 种粒度，
#   其中分钟级仅 A 股个股支持；m250 不存在（westock 对应别名为 m120），故本集以 m120 取代 m250。
#   分钟/周/月/季/年线均由 westock CLI 真实交付，不再依赖 baostock/akshare 兜底。
KLINE_PERIODS: set = {
    "m1",
    "m5",
    "m15",
    "m30",
    "m60",
    "m120",
    "day",
    "week",
    "month",
    "season",
    "year",
}

# 周期 → 人类可读中文（用于日志/报告）
PERIOD_DISPLAY: dict = {
    "m1": "1分钟",
    "m5": "5分钟",
    "m15": "15分钟",
    "m30": "30分钟",
    "m60": "60分钟",
    "m120": "120日线",
    "day": "日线",
    "week": "周线",
    "month": "月线",
    "season": "季线",
    "year": "年线",
}

# 复权方式 → 标准化取值（与 get_daily 对齐：qfq 前复权 / hfq 后复权 / '' 不复权）
ADJUST_MODES: set = {"qfq", "hfq", ""}

# 标准化 K 线返回契约列（get_kline / get_daily 均归一化到此）
KLINE_CONTRACT_COLS: list = [
    "code",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
]

# 标的类型标准化取值（Phase 3 显式 asset_type 维度；当前经 symbol 前缀隐式编码）
INSTRUMENT_TYPES: set = {"stock", "index", "future", "option"}

# 数据分组标准化取值（对齐行业标准四类）
DATA_GROUPS: set = {"basic", "market", "financial", "reference"}


# 所有数据类型注册
# 旧键（daily/financial/capital_flow/dragon_tiger/shareholder）保留，group 按 Jobs 口径归类。
DATA_TYPES: Dict[str, DataTypeMeta] = {
    # ===================== 行情 market =====================
    "daily": DataTypeMeta(
        name="daily",
        display_name="日线行情",
        method_name="get_daily",
        artifact_filename="cleaned_data.parquet",
        artifact_key="DATA",
        allow_synthetic=True,
        required=True,
        description="OHLCV 日线行情，管线必需数据",
        group="market",
        instrument_types=["stock", "index"],
    ),
    "capital_flow": DataTypeMeta(
        name="capital_flow",
        display_name="资金面数据",
        method_name="get_capital_flow",
        artifact_filename="capital_flow.parquet",
        artifact_key="CAPITAL_FLOW",
        allow_synthetic=False,
        required=False,
        description="主力资金流向、北向资金",
        group="reference",
        instrument_types=["stock"],
    ),
    "dragon_tiger": DataTypeMeta(
        name="dragon_tiger",
        display_name="龙虎榜数据",
        method_name="get_dragon_tiger",
        artifact_filename="dragon_tiger.parquet",
        artifact_key="DRAGON_TIGER",
        allow_synthetic=False,
        required=False,
        description="龙虎榜上榜明细",
        group="reference",
        instrument_types=["stock"],
    ),
    "shareholder": DataTypeMeta(
        name="shareholder",
        display_name="股东结构数据",
        method_name="get_shareholder",
        artifact_filename="shareholder_data.parquet",
        artifact_key="SHAREHOLDER",
        allow_synthetic=False,
        required=False,
        description="十大股东、持股变动",
        group="reference",
        instrument_types=["stock"],
    ),
    # ===================== 财务 financial =====================
    "financial": DataTypeMeta(
        name="financial",
        display_name="财务数据",
        method_name="get_financial",
        artifact_filename="financial_data.parquet",
        artifact_key="FINANCIAL",
        allow_synthetic=False,
        required=False,
        description="PE/PB/ROE/毛利率等财务指标",
        group="financial",
        instrument_types=["stock", "index"],
    ),
    # ===================== 基础 basic（P0 叶子项，Phase 2 补实现）=====================
    "basic_stock_list": DataTypeMeta(
        name="basic_stock_list",
        display_name="股票列表",
        method_name="get_stock_list",
        artifact_filename="stock_list.parquet",
        artifact_key="STOCK_LIST",
        allow_synthetic=False,
        required=False,
        description="股票列表（westock connect 陆股通标的聚合，约 2k+；全量回退 local）",
        group="basic",
        instrument_types=["stock"],
    ),
    "basic_stock_info": DataTypeMeta(
        name="basic_stock_info",
        display_name="股票基本信息",
        method_name="get_stock_info",
        artifact_filename="stock_info.parquet",
        artifact_key="STOCK_INFO",
        allow_synthetic=False,
        required=False,
        description="个股基本信息（行业/上市日期/总股本等，westock profile）",
        group="basic",
        instrument_types=["stock"],
    ),
    "basic_trade_calendar": DataTypeMeta(
        name="basic_trade_calendar",
        display_name="交易日历",
        method_name="get_trade_calendar",
        artifact_filename="trade_calendar.parquet",
        artifact_key="TRADE_CALENDAR",
        allow_synthetic=False,
        required=False,
        description="交易日历（开市/休市标记，westock trade-calendar）",
        group="basic",
        instrument_types=["stock", "index", "future", "option"],
    ),
    "basic_adjust_factor": DataTypeMeta(
        name="basic_adjust_factor",
        display_name="复权因子",
        method_name="get_adj_factor",
        artifact_filename="adjust_factor.parquet",
        artifact_key="ADJUST_FACTOR",
        allow_synthetic=False,
        required=False,
        description="复权因子（qfq/raw 价反算，westock kline --fq）",
        group="basic",
        instrument_types=["stock"],
    ),
    # ===================== 行情扩展 market（P0 叶子项）=====================
    "market_kline": DataTypeMeta(
        name="market_kline",
        display_name="K线行情",
        method_name="get_kline",
        artifact_filename="kline.parquet",
        artifact_key="KLINE",
        allow_synthetic=False,
        required=False,
        description="多周期 K 线（m1~m120/day/week/month/season/year，westock kline）",
        group="market",
        instrument_types=["stock", "index"],
    ),
    "market_realtime": DataTypeMeta(
        name="market_realtime",
        display_name="实时行情",
        method_name="get_realtime_quote",
        artifact_filename="realtime_quote.parquet",
        artifact_key="REALTIME_QUOTE",
        allow_synthetic=False,
        required=False,
        description="实时快照（最新价/买卖盘/涨跌幅，westock qt.gtimg.cn）",
        group="market",
        instrument_types=["stock", "index"],
    ),
    # ===================== 财务扩展 financial（P0 叶子项）=====================
    "financial_report": DataTypeMeta(
        name="financial_report",
        display_name="财报数据",
        method_name="get_financial_report",
        artifact_filename="financial_report.parquet",
        artifact_key="FINANCIAL_REPORT",
        allow_synthetic=False,
        required=False,
        description="三大报表（利润表/资产负债表/现金流，复用 westock finance）",
        group="financial",
        instrument_types=["stock", "index"],
    ),
    # ===================== 参考 reference（P0 叶子项，Phase 2 已实测落地）=====================
    "ref_dividend": DataTypeMeta(
        name="ref_dividend",
        display_name="分红派息",
        method_name="get_dividend",
        artifact_filename="dividend.parquet",
        artifact_key="DIVIDEND",
        allow_synthetic=False,
        required=False,
        description="分红派息记录（预案/实施/除权除息，westock dividend list）",
        group="reference",
        instrument_types=["stock"],
    ),
    "ref_suspend_resume": DataTypeMeta(
        name="ref_suspend_resume",
        display_name="停复牌",
        method_name="get_suspend_resume",
        artifact_filename="suspend_resume.parquet",
        artifact_key="SUSPEND_RESUME",
        allow_synthetic=False,
        required=False,
        description="停复牌事件（停牌原因/预计复牌日，westock suspension）",
        group="reference",
        instrument_types=["stock"],
    ),
    "ref_locked_shares": DataTypeMeta(
        name="ref_locked_shares",
        display_name="限售解禁",
        method_name="get_locked_shares",
        artifact_filename="locked_shares.parquet",
        artifact_key="LOCKED_SHARES",
        allow_synthetic=False,
        required=False,
        description="限售股解禁安排（解禁日/解禁数量，westock calendar lockup_release）",
        group="reference",
        instrument_types=["stock"],
    ),
    "ref_forecast": DataTypeMeta(
        name="ref_forecast",
        display_name="业绩预告",
        method_name="get_forecast",
        artifact_filename="forecast.parquet",
        artifact_key="FORECAST",
        allow_synthetic=False,
        required=False,
        description="业绩预告（EPS 区间，westock calendar financial_report）",
        group="reference",
        instrument_types=["stock"],
    ),
}


# 旧键 → 新键 兼容映射（Phase 2 前保留，供旧管线引用；新代码直接用新键）
# 说明：daily/financial/capital_flow/dragon_tiger/shareholder 在 P1 中已作为正式键保留，
# 此处映射仅作显式文档化，不引入额外别名键，避免双份维护。
LEGACY_KEY_MAP: Dict[str, str] = {}
