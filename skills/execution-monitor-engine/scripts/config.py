"""
执行引擎专属配置
"""
import os

_WORK_DIR = os.environ.get("QUANT_WORK_DIR", "./workspace")
EXECUTION_DIR = os.environ.get("EXECUTION_DIR", os.path.join(_WORK_DIR, "execution"))
TRADE_MODE = os.environ.get("TRADE_MODE", "paper")
TRADE_BACKEND = os.environ.get("TRADE_BACKEND", "paper")
INIT_CAPITAL = float(os.environ.get("INIT_CAPITAL", 1000000))
MAX_DAILY_LOSS_RATIO = float(os.environ.get("MAX_DAILY_LOSS_RATIO", 0.02))
MAX_SINGLE_ORDER_RATIO = float(os.environ.get("MAX_SINGLE_ORDER_RATIO", 0.10))
MAX_SINGLE_STOCK_WEIGHT = float(os.environ.get("MAX_SINGLE_STOCK_WEIGHT", 0.10))
MAX_ORDER_FREQUENCY = int(os.environ.get("MAX_ORDER_FREQUENCY", 2))
MIN_COMMISSION = float(os.environ.get("MIN_COMMISSION", 5.0))
COMMISSION_RATE = float(os.environ.get("COMMISSION_RATE", 0.00025))
STAMP_TAX_RATE = float(os.environ.get("STAMP_TAX_RATE", 0.001))
SLIPPAGE = float(os.environ.get("SLIPPAGE", 0.001))
AUDIT_LOG_PATH = os.path.join(EXECUTION_DIR, "trade_log.jsonl")
ACCOUNT_STATE_PATH = os.path.join(EXECUTION_DIR, "account_state.json")

# 实盘交易后端配置
XTQUANT_PATH = os.environ.get("XTQUANT_PATH", "")  # miniQMT userdata_mini 路径
XTQUANT_ACCOUNT = os.environ.get("XTQUANT_ACCOUNT", "")  # miniQMT 资金账号
GM_TOKEN = os.environ.get("GM_TOKEN", "")  # 掘金量化 token
GM_ACCOUNT_ID = os.environ.get("GM_ACCOUNT_ID", "")  # 掘金账户ID(终端获取)

# ── live 单日亏损检查（二期，2026-08-28 Damon 批准，自 A 树同步）────────
# 背景：broker 账户接口均不提供 start_of_day_nav——xtquant 的 XtAsset 仅 6 字段
# （已全量枚举）、gm 的 Cashes.data 29 字段中 fpnl 口径未源证（[UNSOURCED]），
# 故改由本地文件按交易日持久化基线。默认 **off** 灰度开启：启用前须确认基线
# 来源（见 references/compliance-trading-mode.md「冷启动漏损」说明）。
DEFAULT_LIVE_DAILY_LOSS_CHECK = "off"
_TRUTHY = ("1", "on", "true", "yes")

LIVE_DAILY_BASELINE_PATH = os.environ.get(
    "LIVE_DAILY_BASELINE_PATH", os.path.join(EXECUTION_DIR, "live_daily_baseline.json")
)


def live_daily_loss_enabled() -> bool:
    """实时判定 live 单日亏损检查是否开启（**不做构造期快照**）。

    刻意每次调用都现读环境变量：① 运行期可能由运维/风控官切换开关，构造期
    快照会让切换失效；② 缓存会让 ``monkeypatch.setenv`` 失效，测试将无从
    覆盖开启态。
    """
    return os.environ.get("LIVE_DAILY_LOSS_CHECK", DEFAULT_LIVE_DAILY_LOSS_CHECK).strip().lower() in _TRUTHY


os.makedirs(EXECUTION_DIR, exist_ok=True)