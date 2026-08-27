"""
数据引擎专属配置
大部分全局配置从 master 继承，此处仅保留数据引擎特有设置
"""
from __future__ import annotations

import os
import logging
from typing import List, Dict


logger = logging.getLogger("data-engine.config")


# ── 系统支持的全部数据源（REQ-2026-08-14 实施：移除 neodata，恢复 9 旧源 + 接入 westock）──
# ⚠️ Damon 2026-08-14 最新拍板（最终版）：
#   neodata 离开 workbuddy 无法使用 → 数据层不接入 neodata（取数路径中不得出现）；
#   恢复 9 个旧 adapter（akshare/baostock/gm/ifind/tdxquant/tushare/websearch/wind/xtquant），
#   并新增 westock（腾讯公网直连免费源）；最终 11 个 backend = westock + local + 9 旧源。
# 默认免费链（5 源，按优先级降级）：local → westock → baostock → akshare → websearch。
#   ⚠️ local 是本地数据缓存，优先级高于 westock（Damon 确认）；
#   tushare 从免费链移除，移入按需调用组（见 PAID_OR_SPECIAL_BACKENDS）；
#   gm/xtquant/tdxquant/wind/ifind 亦属按需调用组，注册可用但不在默认链（避免默认链
#   引发现金/终端依赖），由用户对话或 DATA_BACKENDS 环境变量显式启用。
SUPPORTED_BACKENDS: List[str] = [
    "westock",  # 腾讯公网行情直连（免费、零鉴权）
    "local",  # 本地 Parquet 缓存（不联网，优先级高于 westock）
    "tushare",  # Tushare Pro 商业 API（需 TUSHARE_TOKEN）— 按需调用
    "baostock",  # 老虎量化开源项目（无需 Token）
    "akshare",  # 聚合库（爬虫，无需 Token）
    "websearch",  # 终极回退：经 WebSearch 工具查询
    "xtquant",  # 迅投 QMT/xtp（需本地券商客户端）— 按需调用
    "gm",  # 掘金量化（需 GM_TOKEN + 付费 SDK）— 按需调用
    "tdxquant",  # 通达信量化（需本地通达信金融终端）— 按需调用
    "wind",  # 万得 WindPy（需 Wind 金融终端 + WindPy）— 按需调用
    "ifind",  # 同花顺 iFinD（需 iFinDPy + 账号密码）— 按需调用
]


# ── 默认数据源（按优先级排序，免费链 5 源）────────────
# local（本地缓存，最高优先）→ westock（公网免费）→ baostock → akshare → websearch。
# 按需调用组（tushare/gm/xtquant/tdxquant/wind/ifind）需用户显式指定，不进默认链。
DEFAULT_DATA_SOURCES: List[str] = [
    s.strip()
    for s in os.environ.get("DATA_BACKENDS", "local,westock,baostock,akshare,websearch").split(",")
    if s.strip()
]


# ── 数据源降级条件表 ──────────────────────────────
DATA_FALLBACK_RULES: Dict[str, Dict[str, str]] = {
    "local": {
        "trigger_errors": "FileNotFoundError",
        "trigger_messages": "本地缓存无对应标的",
        "downgrade_to": "westock",
        "downgrade_reason": "本地缓存也无数据，转公网 westock",
    },
    "westock": {
        "trigger_errors": "NetworkError, DataNotFoundError, InvalidParameterError",
        "trigger_messages": "腾讯公网不可达 / 标的无覆盖",
        "downgrade_to": "baostock",
        "downgrade_reason": "腾讯公网通道不可用/无数据，走免费链下一源 baostock",
    },
    "baostock": {
        "trigger_errors": "BlacklistedError, DataNotFoundError, NetworkError",
        "trigger_messages": "服务器黑名单 / 标的未覆盖 / 网络错误",
        "downgrade_to": "akshare",
        "downgrade_reason": "Baostock 被服务器限制或未覆盖该标的",
    },
    "akshare": {
        "trigger_errors": "NetworkError, BlacklistedError, DataNotFoundError",
        "trigger_messages": "爬虫被服务器限制 / HTTPSConnectionPool 失败 / 标的未覆盖",
        "downgrade_to": "websearch",
        "downgrade_reason": "AkShare 爬虫被限制或未覆盖该标的",
    },
    "websearch": {
        "trigger_errors": "DataNotFoundError",
        "trigger_messages": "搜索引擎无相关数据 / 搜索结果无法解析为 OHLCV",
        "downgrade_to": "error（所有外部源都不可用，显式报错）",
        "downgrade_reason": "所有免费外部源都不可用",
    },
    # ── 以下是按需调用组（仅当用户显式启用对应源时生效）──
    "tushare": {
        "trigger_errors": "QuotaExceededError, RateLimitError",
        "trigger_messages": "积分不足 / 权限不足 / 访问频率超限",
        "downgrade_to": "baostock",
        "downgrade_reason": "Tushare 积分/权限/限频受限（按需调用时的内部降级）",
    },
}


# ── opt-in / 按需调用源（需前置条件：token/账号/本地终端 SDK）────────
# 默认不进入降级链；用户可通过对话（如"用 tushare 取数据"）或 DATA_BACKENDS 显式启用。
# ⚠️ 2026-08-14 最新拍板：tushare 从免费链移除，归入本组（按需调用，非免费源）。
PAID_OR_SPECIAL_BACKENDS: List[str] = ["tushare", "xtquant", "gm", "tdxquant", "wind", "ifind"]
PAID_OR_SPECIAL_DESCRIPTIONS = {
    "tushare": "Tushare Pro 商业 API（需 TUSHARE_TOKEN，https://tushare.pro/）— 按需调用，非免费源",
    "xtquant": "迅投 QMT/xtp（需本地券商客户端）— 按需调用",
    "gm": "掘金量化（需 GM_TOKEN + 付费 SDK，https://www.myquant.cn）— 按需调用",
    "tdxquant": "通达信量化（需本地通达信金融终端 TQ 策略，https://help.tdx.com.cn/quant/）— 按需调用",
    "wind": "万得 WindPy（需 Wind 金融终端 + WindPy，https://www.wind.com.cn/）— 按需调用",
    "ifind": "同花顺 iFinD（需 iFinDPy + 账号密码，http://ft.10jqka.com.cn/）— 按需调用",
}


# ── 兼容性：保留 DATA_BACKEND 单源选择 ───────────────
DATA_BACKEND: str | None = os.environ.get("DATA_BACKEND")
if DATA_BACKEND and not os.environ.get("DATA_BACKENDS"):
    DEFAULT_DATA_SOURCES = [DATA_BACKEND]
    logger.info(f"使用 DATA_BACKEND={DATA_BACKEND}（单源模式，不降级）")


# ── 数据存储格式 ──────────────────────────
DATA_FORMAT = os.environ.get("DATA_FORMAT", "parquet")

# ── 并行下载线程数 ─────────────────────────
MAX_WORKERS = int(os.environ.get("DATA_MAX_WORKERS", 4))

# ── 行情复权方式 ──────────────────────────
# 默认使用前复权(qfq)：保证最新交易日价格 = 实际成交价，技术指标计算最准确。
# 注意：baostock 的 adjustflag 语义为 1=后复权、2=前复权，akshare 的 stock_zh_a_hist
#       与 stock_zh_a_daily 均支持 "qfq"。此默认值保证跨数据源复权口径一致。
ADJUST_MODE = os.environ.get("ADJUST_MODE", "qfq")

# ── 缓存目录 ──────────────────────────────
_WORK_DIR = os.environ.get("QUANT_WORK_DIR", "./workspace")
CACHE_DIR = os.environ.get("DATA_CACHE_DIR", os.path.join(_WORK_DIR, "data_cache"))
os.makedirs(CACHE_DIR, exist_ok=True)

# ── local 缓存写回开关（REQ-2026-08-16 cache write-back）──
# 「买菜放冰箱」：引擎联网取数成功后，同步写一份到 local 缓存目录，
# 下次查询直接命中缓存（不联网）。默认开启；设为 false 可关闭写回。
CACHE_WRITE_BACK: bool = os.environ.get("DATA_CACHE_WRITE_BACK", "true").lower() != "false"

# ── local 缓存增量更新开关（REQ-2026-08-16 优化：增量优先 + 修正感知）──
# 开启：序列类数据仅拉缓存截止日之后增量并合并；快照类仍整取。
# 关闭：维持原整删整取行为（兼容回滚）。
CACHE_INCREMENTAL: bool = os.environ.get("DATA_CACHE_INCREMENTAL", "true").lower() != "false"

# ── 缓存 TTL（秒）────────────────────────
# 各数据项新鲜度窗口；local 读取时检查 TTL，过期视为未命中触发联网重取。
# 口径（Damon 沟通）：realtime=60s、daily=1d、financial/reference=7d、basic=30d。
_CACHE_TTL_SECONDS: Dict[str, int] = {
    "market_realtime": 60,            # 实时行情 60s
    "daily": 86400,                   # 日线 1d
    "market_kline": 86400,            # K线（日级缓存）1d
    "financial": 604800,              # 财务 7d
    "financial_report": 604800,       # 财报 7d
    "capital_flow": 604800,           # 资金面 7d
    "dragon_tiger": 604800,           # 龙虎榜 7d
    "shareholder": 604800,            # 股东 7d
    "ref_dividend": 604800,           # 分红派息 7d
    "ref_suspend_resume": 604800,     # 停复牌 7d
    "ref_locked_shares": 604800,      # 限售解禁 7d
    "ref_forecast": 604800,           # 业绩预告 7d
    "basic_stock_list": 2592000,      # 股票列表 30d
    "basic_stock_info": 2592000,      # 股票信息 30d
    "basic_trade_calendar": 2592000,  # 交易日历 30d
    "basic_adjust_factor": 2592000,   # 复权因子 30d
}
# 兜底 TTL（未显式登记的数据项按 1d 处理）
CACHE_TTL_DEFAULT: int = 86400
# 允许经环境变量整体覆盖（逗号分隔 data_type:ttl，可选；未提供项用上表）
_env_ttl = os.environ.get("DATA_CACHE_TTL")
if _env_ttl:
    for _pair in _env_ttl.split(","):
        if ":" in _pair:
            _k, _v = _pair.split(":", 1)
            try:
                _CACHE_TTL_SECONDS[_k.strip()] = int(_v.strip())
            except ValueError:
                logger.warning("忽略无效 DATA_CACHE_TTL 项: %s", _pair)

def cache_ttl_seconds(data_type: str) -> int:
    """返回某数据项的缓存 TTL（秒）。"""
    return _CACHE_TTL_SECONDS.get(data_type, CACHE_TTL_DEFAULT)

# ── 无 symbol 维度的全局缓存数据项 ─────────
# 这类数据不分标的（如交易日历、全市场股票列表），缓存文件统一用 all.parquet。
GLOBAL_CACHE_TYPES: set = {"basic_stock_list", "basic_trade_calendar"}

# ── 缓存新鲜度元数据（轻量策略）───────────
# 每次写回在 parquet 同目录写一份 <name>.parquet.meta.json，记录 fetch_ts；
# local 读取时比对 TTL 决定是否过期。meta 文件仅含时间戳，绝不写 token/敏感信息。
def cache_meta_path(parquet_path: str) -> str:
    """返回 parquet 对应的 meta.json 路径。"""
    return parquet_path + ".meta.json"

def write_cache_meta(
    parquet_path: str,
    fetch_ts: float | None = None,
    adjust: str | None = None,
    cached_end: str | None = None,
    data_signature: str | None = None,
) -> None:
    """写入缓存新鲜度元数据（仅时间戳/口径/指纹，无敏感信息）。

    扩展字段（增量更新用）：
      - adjust: 复权口径（qfq/hfq/''），用于修正感知比对
      - cached_end: 缓存覆盖的截止日期（序列类），用于规划增量起始日
      - data_signature: 旧缓存数据指纹，用于重叠窗口修正检测
    """
    import json
    import time

    meta = {"fetch_ts": fetch_ts if fetch_ts is not None else time.time()}
    if adjust is not None:
        meta["adjust"] = adjust
    if cached_end is not None:
        meta["cached_end"] = cached_end
    if data_signature is not None:
        meta["data_signature"] = data_signature
    try:
        with open(cache_meta_path(parquet_path), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception as e:  # noqa: F841  元数据写入失败不阻断主流程（异常对象仅用于日志）
        logger.debug("缓存 meta 写入失败（忽略）: %s", e)

def read_cache_meta(parquet_path: str) -> dict:
    """读取缓存新鲜度元数据；不存在/损坏返回空 dict。"""
    import json

    p = cache_meta_path(parquet_path)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa
        return {}

def cache_is_fresh(parquet_path: str, data_type: str, now: float | None = None) -> bool:
    """判断缓存文件是否仍在 TTL 内（新鲜）。"""
    import time

    meta = read_cache_meta(parquet_path)
    ts = meta.get("fetch_ts")
    if ts is None:
        # 无 meta（历史缓存）→ 视为过期，触发重取，保证策略生效
        return False
    now = now if now is not None else time.time()
    return (now - float(ts)) <= cache_ttl_seconds(data_type)

# ── 股票池默认文件 ─────────────────────────
STOCK_LIST_FILE = os.environ.get("STOCK_LIST_FILE", "")

# ── 数据质量阈值 ──────────────────────────
MAX_MISSING_RATIO = 0.05

# ── API 令牌（仅从环境变量读取，绝不打印/落盘明文）────────
# 仅 tushare/gm/ifind 三个按需调用源需要；其余源（tencent/baostock/akshare/websearch/
# local/xtquant/tdxquant/wind）经免费公网或本地终端 SDK，不读 token。
# 安全纪律：本文件只 os.environ.get 读取引用，任何位置不得 print/落盘 token 明文。
TUSHARE_TOKEN: str | None = os.environ.get("TUSHARE_TOKEN")
GM_TOKEN: str | None = os.environ.get("GM_TOKEN")
IFIND_USERNAME: str | None = os.environ.get("IFIND_USERNAME")
IFIND_PASSWORD: str | None = os.environ.get("IFIND_PASSWORD")

# ── 是否允许模拟数据 fallback ──────────────────
# 私有化纪律：默认关闭合成数据兜底，所有外部源失败即显式报错，
# 由调用方（agent）决定如何告知用户。绝不静默合成行情。
ALLOW_SYNTHETIC_FALLBACK: bool = os.environ.get("ALLOW_SYNTHETIC_FALLBACK", "false").lower() == "true"


# ── 数据源依赖自动安装 ─────────────────────────
# 私有化纪律：禁止运行时自动 pip install（避免联网/越权装包）。
AUTO_INSTALL_BACKENDS: bool = os.environ.get("AUTO_INSTALL_BACKENDS", "false").lower() == "true"

# ── westock 通道 node 前置自动安装（REQ-2026-08-15 方案 1）──
# 设计 §5.2：默认 off，守住「私有化禁止自动安装」基线；
# 仅无人值守客户部署（agent daemon 常驻）显式开启 AUTO_INSTALL_NODE=true，
# 此时 westock_adapter 检测到 NODE_MISSING 后按平台分支直接执行安装命令。
# 行为层（方案 2，SKILL.md 指令）始终优先：agent 检测→告知→征得同意→安装，
# 不依赖此开关；开关仅用于无人值守场景的代码层自动安装。
AUTO_INSTALL_NODE: bool = os.environ.get("AUTO_INSTALL_NODE", "false").lower() == "true"

# 各平台 node 安装命令（供方案 1 AUTO_INSTALL_NODE=true 时直接执行）。
# 与 SKILL.md「node 前置处理」章节的安装分支同源；win32 用 winget 并显式接受协议。
_NODE_INSTALL_CMDS: Dict[str, List[str]] = {
    "win32": ["winget", "install", "OpenJS.NodeJS", "--accept-package-agreements", "--accept-source-agreements"],
    "darwin": ["brew", "install", "node"],
    "linux": ["sudo", "apt", "install", "-y", "nodejs"],
}

# 后端名 -> 需要的 pip 包名（仅用于提示/文档，运行时不再自动 pip install）
BACKEND_PIP_PACKAGES: Dict[str, List[str]] = {
    "westock": [],  # urllib 直连公网（daily）+ npx westock-data-skillhub 子进程（其余 4 类）；均非 PyPI 依赖
    "local": [],  # 纯本地 IO
    "tushare": ["tushare"],  # 需 TUSHARE_TOKEN
    "baostock": ["baostock"],  # 无需 Token
    "akshare": ["akshare"],  # 无需 Token
    "websearch": [],  # 依赖注入 web_search_fn，无第三方包
    "xtquant": ["xtquant"],  # 需本地券商客户端
    "gm": ["gm"],  # 需 GM_TOKEN + 付费 SDK
    "tdxquant": [],  # 需本地通达信金融终端
    "wind": [],  # 需 Wind 金融终端 + WindPy（非 PyPI）
    "ifind": [],  # 需 iFinDPy（非 PyPI）+ 账号密码
}


def notify_supported_backends() -> None:
    """
    首次使用时提示用户：系统支持的数据源全景 + 降级条件

    设计意图：让用户知道每个源的优先级、降级条件、opt-in 源。
    """
    logger.info("=" * 60)
    logger.info("数据引擎已就绪。默认数据源（按优先级降级）：")
    for i, s in enumerate(DEFAULT_DATA_SOURCES, 1):
        rule = DATA_FALLBACK_RULES.get(s, {})
        trigger = rule.get("trigger_messages", "")
        logger.info(f"  {i}. {s}")
        if trigger:
            logger.info(f"     降级条件: {trigger}")
            logger.info(f"     降级到:   {rule.get('downgrade_to', '?')}")
    logger.info("")
    logger.info(f"系统还支持以下 {len(PAID_OR_SPECIAL_BACKENDS)} 个 opt-in 源（需显式启用）：")
    for name in PAID_OR_SPECIAL_BACKENDS:
        logger.info(f"  • {name}: {PAID_OR_SPECIAL_DESCRIPTIONS[name]}")
    logger.info("")
    logger.info(f"合成数据 fallback: {'[OK] 启用' if ALLOW_SYNTHETIC_FALLBACK else '[X] 禁用（失败会抛异常）'}")
    logger.info("")
    logger.info("启用方式：export DATA_BACKENDS=tencent,local（私有化仅两通道，外网/付费源已移除）")
    logger.info("=" * 60)
