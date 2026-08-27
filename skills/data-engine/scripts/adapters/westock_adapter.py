"""
腾讯公网行情适配器（免费数据源，零鉴权直连）

直连腾讯公开行情接口，无需 token / 无需 MCP / 不依赖任何付费数据源：
- 实时报价：  https://qt.gtimg.cn/q=<code>
- 日K线(前复权)：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<code>,day,,,N,qfq

设计意图（REQ-2026-08-14-data-source-verification 实施阶段，Damon 拍板）：
- neodata 离开 workbuddy 无法使用 → jingni-trader 移除 neodata；
- 腾讯公网直连作为免费数据源接入（westock 后端），与 local 并列。
- 取数失败（网络/空返回）抛出 NetworkError / DataNotFoundError（均属 FALLBACK_TRIGGERING_ERRORS），
  由 data-engine 降级链自动回退 local 缓存（复用现有机制，不在此处手写回退）。
- 轻限频护栏：单标的请求最小间隔 + 批量标的上限，避免触发公网无 SLA 接口的限流。

安全纪律：本适配器不读取/不打印任何 token（腾讯公网无需凭证）。
"""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Dict, Any

import pandas as pd

from scripts.base.base_data_provider import BaseDataProvider
from scripts.errors import (
    NetworkError,
    DataNotFoundError,
    InvalidParameterError,
    NodeMissingError,
)

logger = logging.getLogger("data-engine.westock_adapter")


# ── 端点常量（腾讯公网，零鉴权）─────────────────────────
QUOTE_HOST = "https://qt.gtimg.cn/q="
KLINE_HOST = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# ── 轻限频护栏（保守，非激进并发）──────────────────────
# 单标的两次请求最小间隔（秒）：实测 avg ~97ms，留 5x 余量。
# ⚠️ 环境变量已统一为 WESTOCK_*；保留 TENCENT_* 旧名作为兼容回退（不破坏既有部署/CI）。
_MIN_INTERVAL_PER_SYMBOL = float(os.environ.get("WESTOCK_MIN_INTERVAL", os.environ.get("TENCENT_MIN_INTERVAL", "0.5")))
# 单次批量取数标的上限（避免单批过大触发公网限流）。
_MAX_BATCH_SYMBOLS = int(os.environ.get("WESTOCK_MAX_BATCH", os.environ.get("TENCENT_MAX_BATCH", "20")))
# 单次 K 线拉取最大条数（接口 param 第 4 段）。
_KLINE_LIMIT = int(os.environ.get("WESTOCK_KLINE_LIMIT", os.environ.get("TENCENT_KLINE_LIMIT", "320")))

# ── westockdata 补充层（全品类，npx 子进程，零鉴权）──────────
# 调用形态：npx -y westock-data-skillhub@1.0.5 <command> [args]
# 命令映射：financial→finance / capital_flow→fund flow / dragon_tiger→lhb / shareholder→shareholder
# 代码格式：sh600519 / sz000651 / hk00700 / usAAPL（与 _normalize_to_tencent_code 对齐可复用）
_WESTOCK_PKG = "westock-data-skillhub@1.0.5"
# westock 单批标的上限（无官方 SLA，保守 ≤10，避免触发限频）
_WESTOCK_MAX_BATCH = int(os.environ.get("WESTOCK_MAX_BATCH", "10"))
# westock 最小请求间隔（秒，与腾讯公网护栏同参）
_WESTOCK_MIN_INTERVAL = float(os.environ.get("WESTOCK_MIN_INTERVAL", "0.5"))


def _normalize_to_tencent_code(symbol: str) -> str:
    """把 6位.交易所 或裸代码映射为腾讯接口代码（sh/sz/hk/us 前缀）。

    返回 (tencent_code, market)；market 用于日记与错误提示。
    不支持的市场 → 抛 InvalidParameterError（不切换数据源，避免无意义降级）。
    """
    s = str(symbol).strip().upper()
    # 形如 600519.SH / 000001.SZ
    if "." in s:
        code, exch = s.split(".", 1)
        exch = exch.upper()
        if exch in ("SH", "SS"):
            return f"sh{code}", "ashare"
        if exch == "SZ":
            return f"sz{code}", "ashare"
        if exch in ("BJ",):
            return f"bj{code}", "ashare"
        if exch in ("HK",):
            return f"hk{code}", "hk"
        if exch in ("US", "NYSE", "NASDAQ"):
            return f"us{code}", "us"
        raise InvalidParameterError(
            "westock",
            f"不支持的交易所后缀: {exch}（仅 SH/SZ/BJ/HK/US）",
        )
    # 裸代码：A股 sh/sz 前缀已在代码本身
    if s.startswith(("SH", "SZ", "BJ")):
        return s, "ashare"
    if s.startswith("HK"):
        return s, "hk"
    if s.startswith("US"):
        return s, "us"
    if len(s) == 6 and s.isdigit():
        # 纯 6 位数字按 A 股处理：上交所 6/9 开头 → sh，其余 → sz
        prefix = "sh" if s[0] in ("6", "9") else "sz"
        return f"{prefix}{s}", "ashare"
    raise InvalidParameterError(
        "westock",
        f"无法解析的股票代码: {symbol}",
    )


class _RateGuard:
    """进程内单标的最小请求间隔护栏（轻量，非分布式）。"""

    _last_ts: dict = {}

    @classmethod
    def throttle(cls, key: str) -> None:
        if _MIN_INTERVAL_PER_SYMBOL <= 0:
            return
        now = time.time()
        last = cls._last_ts.get(key)
        if last is not None:
            wait = _MIN_INTERVAL_PER_SYMBOL - (now - last)
            if wait > 0:
                time.sleep(min(wait, 2.0))  # 封顶 2s，避免异常长 sleep
        cls._last_ts[key] = time.time()


# ── westockdata 子进程原语（全品类补充取数层）────────────────
def _sanitize_code_list(symbols: List[str]) -> List[str]:
    """净化股票代码列表，仅保留 westock 接受的 [a-z]字母前缀+数字格式，防命令注入。

    输入 symbols 为 jingni-trader 内部格式（600519.SH / 000001.SZ / hk00700 / usAAPL 等）。
    先经 _normalize_to_tencent_code 对齐为 sh/sz/hk/us 前缀，再白名单校验（字母+数字），
    拒绝任何含空格/引号/分号/&/| 等 shell 元字符的异常输入（安全纪律：westock 命令不注入
    用户可控参数）。返回净化后的 tcode 列表（已逗号分隔由调用方组装）。
    """
    clean = []
    for s in symbols:
        try:
            tcode, _ = _normalize_to_tencent_code(s)
        except InvalidParameterError:
            continue
        # 白名单：仅 sh/sz/hk/us 前缀 + 纯数字后缀；拒绝任何非预期字符。
        # westock CLI 要求小写前缀（sh/sz/hk/us），统一转小写再校验。
        tcode = tcode.lower()
        if any(c.isspace() for c in tcode):
            continue
        if not all(c.isalnum() for c in tcode):
            continue
        clean.append(tcode)
    return clean


def _is_index_symbol(symbol: str) -> bool:
    """粗略判定 symbol 是否为指数（不走腾讯公网日线路径）。

    规则：sh/sz 前缀 + 6 位数字，且首段为 000（上证/深证指数）/ 399（深证成指系列）/
    950/951/952（中证系列）等；或显式含 'IDX'/'INDEX'。港股/美股指数前缀（hk/us）亦判为 True。
    """
    s = str(symbol).strip().upper()
    if s.startswith(("HK", "US")):
        return True
    if "INDEX" in s or "IDX" in s:
        return True
    if s.startswith(("SH", "SZ", "BJ")):
        digits = s[2:]
        if len(digits) == 6 and digits.isdigit():
            # 上证/深证/中证/国证指数代码段首
            return digits[:3] in ("000", "399", "950", "951", "952", "930", "000") or digits.startswith(("399", "930", "950", "951", "952"))
    return False


def _align_financial_report_date(report_date: str) -> str:
    """将任意 report_date 对齐到最近的【前一个】财报披露季度末。

    westock `finance` 按区间返回最近一期真实财报；若 report_date 恰为财报日
    （03-31/06-30/09-30/12-31）则原样返回；否则回退到该日期之前最近的季度末
    （如 2023-01-01 → 2022-12-31，2023-05-20 → 2023-03-31）。
    返回 YYYY-MM-DD 格式字符串。入参非法/空 → 返回 ""（由调用方走 --num 兜底）。
    """
    import datetime as _dt

    s = (report_date or "").strip().replace("/", "-")
    # 仅接受 YYYY-MM-DD / YYYYMMDD
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return ""
    try:
        y, m, d = int(s[:4]), int(s[5:7]), int(s[8:10])
        _dt.date(y, m, d)  # 校验合法性
    except (ValueError, TypeError):
        return ""

    # 标准财报季度末
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    if (m, d) in quarter_ends:
        return s  # 已是财报日，原样返回
    # 回退到当前日期之前最近的季度末
    if m <= 3:
        return f"{y - 1}-12-31"
    if m <= 6:
        return f"{y}-03-31"
    if m <= 9:
        return f"{y}-06-30"
    return f"{y}-09-30"


def _run_westock(args: List[str], timeout: float = 30.0) -> Dict[str, Any]:
    """调用 westock-data-skillhub 子进程，返回解析后的 JSON dict。

    前置检测：npx/node 缺失 → 抛 NodeMissingError（不触发数据源降级，由方案 1/2 处理）。
    运行失败（非零退出 / 超时 / JSON 解析失败）→ 转 NetworkError / DataNotFoundError。

    安全纪律：args 必须由调用方经 _sanitize_code_list 净化，本函数不再拼接用户输入。
    """
    # 前置检测 npx 可用性（node 由 npx 间接依赖；缺失即 node 缺失）
    npx_bin = shutil.which("npx")
    if npx_bin is None:
        raise NodeMissingError(
            "westock",
            "westock 通道前置缺失：未检测到 npx/node（需 Node.js ≥18）。"
            "请安装 Node.js 后重试，或经 AUTO_INSTALL_NODE 自动安装。",
        )
    # Windows 关键修正：subprocess.run 以 list 传参且 shell=False 时，CreateProcess
    # 不对程序名做 PATH 解析，裸 "npx" 会 [WinError 2]。必须用 which 解析出的绝对路径。
    # --raw 为 westock 全局参数，须放在子命令参数【之后】（finance sh600519 --num 1 --raw），
    # 放在包名后会被当作未知子命令。westock 默认输出 Markdown 表格，--raw 才输出 JSON。
    cmd = [npx_bin, "-y", _WESTOCK_PKG, *args, "--raw"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # 不 shell=True，杜绝命令注入
        )
    except subprocess.TimeoutExpired as e:
        raise NetworkError("westock", f"westock 子进程超时（>{timeout}s）: {' '.join(cmd)}", original=e)
    except FileNotFoundError as e:
        # npx 在运行时被移除（which 通过但 exec 失败，极少见）
        raise NodeMissingError("westock", f"westock 子进程启动失败（npx 不可用）: {e}", original=e)

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().replace("\n", " ")[:200]
        raise NetworkError("westock", f"westock 子进程失败（rc={proc.returncode}）: {stderr_tail}")

    return _parse_westock_output(proc.stdout)


def _parse_westock_output(stdout: str) -> Dict[str, Any]:
    """容错解析 westock stdout JSON（--raw 模式）。

    westock --raw 输出为 JSON，但可能在 JSON 之后附带统计行/换行噪点；
    用 JSONDecoder.raw_decode 只消费首个完整 JSON 值（dict 或 list），忽略尾部噪点。
    解析失败 → 抛 DataNotFoundError（视为无有效数据，触发降级）。
    """
    import json as _json

    s = (stdout or "").strip()
    if not s:
        raise DataNotFoundError("westock", "westock 返回空输出")
    # 去除 BOM
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff")
    # 找到第一个 { 或 [ 起解析（westock 可能是对象或数组）
    idx = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
    if idx > 0:
        s = s[idx:]
    decoder = _json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(s)
        return obj
    except _json.JSONDecodeError as e:
        raise DataNotFoundError("westock", f"westock 输出 JSON 解析失败: {e}")


class WestockAdapter(BaseDataProvider):
    """westock 行情适配器（免费、零鉴权、直连腾讯公网 + westockdata 全品类补充层）。

    数据类型覆盖（REQ-2026-08-15 实施）：
    - daily：经 urllib 直连腾讯公网（qt.gtimg.cn / web.ifzq.gtimg.cn），零子进程开销。
    - financial / capital_flow / dragon_tiger / shareholder：经 westockdata 官方通道
      （`npx -y westock-data-skillhub@1.0.5 <command>` 子进程）取数，归一化到
      data_types.py 契约；取数失败抛 DataNotFoundError/NetworkError 触发降级链回退
      baostock/akshare。
    - npx 前置缺失 → 抛 NodeMissingError（不触发数据源降级，由方案 1/2 处理 node 安装）。
    """

    SUPPORTED_DATA_TYPES = {
        "daily",
        "kline",
        "market_kline",
        "market_realtime",
        "financial",
        "financial_report",
        "capital_flow",
        "dragon_tiger",
        "shareholder",
        "basic_stock_list",
        "basic_stock_info",
        "basic_trade_calendar",
        "basic_adjust_factor",
        "ref_dividend",
        "ref_suspend_resume",
        "ref_locked_shares",
        "ref_forecast",
    }

    def __init__(self, cache_dir: str | None = None, timeout: float = 15.0, **kwargs):
        # cache_dir 兼容签名（与 local_adapter 对齐），本适配器不写本地缓存。
        self._cache_dir = cache_dir
        self._timeout = timeout
        # westock 子进程超时（独立参数，默认 30s，长于 urllib 的 15s）
        self._westock_timeout = float(os.environ.get("WESTOCK_TIMEOUT", "30"))

    # ── 网络原语 ──────────────────────────────────────
    def _http_get(self, url: str) -> str:
        _RateGuard.throttle("http")
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise NetworkError(
                "westock",
                f"HTTP {e.code} {e.reason} 拉取失败: {url}",
                original=e,
            )
        except urllib.error.URLError as e:
            raise NetworkError(
                "westock",
                f"网络不可达: {e.reason}",
                original=e,
            )
        # 腾讯接口返回 GBK 编码
        return raw.decode("gbk", "ignore")

    # ── 实时报价 ──────────────────────────────────────
    def get_realtime_quote(self, symbol: str) -> dict:
        """实时报价（qt.gtimg.cn）。

        返回 dict：date/code/open/close/high/low/pre_close/volume/amount 等。
        仅用于最新价补充，不直接参与日K线主链路。
        """
        tcode, _ = _normalize_to_tencent_code(symbol)
        _RateGuard.throttle(tcode)
        body = self._http_get(f"{QUOTE_HOST}{tcode}")
        # 形如 v_sh600519="1~贵州茅台~600519~1341.99~...~"
        if "~" not in body:
            raise DataNotFoundError("westock", f"实时报价返回异常: {body[:80]}")
        parts = body.split("=", 1)[1].strip().strip('"').split("~")
        # 字段索引（腾讯公开约定）：0 未知/1 市场,2 名称,3 代码,4 现价,
        # 5 昨收,6 开盘,7 成交量(手),... 29 时间,30 涨跌,31 涨跌幅
        try:
            return {
                "code": symbol,
                "name": parts[1],
                "open": float(parts[5]) if parts[5] else float("nan"),
                "pre_close": float(parts[4]) if parts[4] else float("nan"),
                "close": float(parts[3]) if parts[3] else float("nan"),
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else float("nan"),
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else float("nan"),
                "volume": float(parts[6]) if parts[6] else 0.0,
                "amount": float(parts[37]) if len(parts) > 37 and parts[37] else float("nan"),
                "datetime": parts[30] if len(parts) > 30 else "",
            }
        except (IndexError, ValueError) as e:
            raise DataNotFoundError("westock", f"实时报价字段解析失败: {e}", original=e)

    # ── 日K线 ────────────────────────────────────────
    def get_daily(self, symbols: List[str], start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()
        if len(symbols) > _MAX_BATCH_SYMBOLS:
            raise InvalidParameterError(
                "westock",
                f"批量标的数 {len(symbols)} 超过上限 {_MAX_BATCH_SYMBOLS}",
            )

        frames = []
        for sym in symbols:
            df_one = self._get_daily_one(sym, adjust=adjust)
            if df_one is not None and not df_one.empty:
                frames.append(df_one)

        if not frames:
            # 全部标的交不到数据 → 触发降级到 local
            raise DataNotFoundError(
                "westock",
                f"腾讯公网未返回 {symbols} 的日线数据（网络/标的覆盖），触发降级",
            )
        df = pd.concat(frames, ignore_index=True)
        # 按日期区间裁剪（接口按条数返回，调用方可进一步约束）
        df = self._clip_date_range(df, start_date, end_date)
        return df

    def _get_daily_one(self, symbol: str, adjust: str = "qfq") -> pd.DataFrame | None:
        tcode, market = _normalize_to_tencent_code(symbol)
        # adjust: 接口 qfq=前复权, hfq=后复权, ""=不复权
        adj_param = adjust if adjust in ("qfq", "hfq") else ""
        url = f"{KLINE_HOST}?param={urllib.parse.quote(tcode)},day,,,{_KLINE_LIMIT},{adj_param}"
        try:
            body = self._http_get(url)
        except (NetworkError, DataNotFoundError):
            raise  # 交给上层降级

        try:
            data = _safe_json_loads(body)
        except Exception as e:
            raise DataNotFoundError("westock", f"K线 JSON 解析失败: {e}", original=e)

        node = (data.get("data") or {}).get(tcode)
        if not node:
            raise DataNotFoundError("westock", f"腾讯公网无 {symbol}({tcode}) 数据")

        # qfqday / hfqday / day 为「列表的列表」[date, open, close, high, low, volume]
        recs = node.get("qfqday") or node.get("hfqday") or node.get("day")
        if not recs:
            raise DataNotFoundError("westock", f"腾讯公网 {symbol} K线为空（可能退市/未上市）")

        rows = []
        for r in recs:
            # r = [date, open, close, high, low, volume]
            try:
                date_s, o, c, h, l, v = r[0], r[1], r[2], r[3], r[4], r[5]
            except (IndexError, TypeError):
                continue
            try:
                rows.append(
                    {
                        "date": pd.to_datetime(str(date_s)),
                        "code": symbol,
                        "open": float(o),
                        "close": float(c),
                        "high": float(h),
                        "low": float(l),
                        "volume": float(v) * 100 if float(v) else 0.0,  # 手→股
                    }
                )
            except (ValueError, TypeError):
                continue

        if not rows:
            raise DataNotFoundError("westock", f"腾讯公网 {symbol} K线无有效行")

        df = pd.DataFrame(rows)
        # 复权后涨跌幅/成交额等腾讯公网免费接口不直接给，留 NaN 由引擎清洗补齐
        df["pre_close"] = df["close"].shift(1)
        df["change_pct"] = (df["close"] - df["pre_close"]) / df["pre_close"] * 100
        df["amount"] = float("nan")
        df["turnover_rate"] = float("nan")
        df["is_st"] = False
        df["is_limit_up"] = df["change_pct"] >= 9.9
        df["is_limit_down"] = df["change_pct"] <= -9.9
        return df[
            [
                "date",
                "code",
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
            ]
        ]

    # ── 全粒度 K 线（REQ-2026-08-15 行情全粒度接入，2026-08-16 实测修正）────────────
    # westock `kline --period <p>` 实测真实支持粒度（锚点 600519，westock-data-skillhub@1.0.5）：
    #   m1/m5/m15/m30/m60/m120/day/week/month/season/year（共 10 种；分钟级仅 A 股个股）。
    # 注意：westock 无 m250（对应别名为 m120），m250 不应传入本方法。
    _KLINE_CLI_PERIODS = {
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

    def get_kline(
        self,
        symbols: List[str],
        period: str = "day",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取任意周期 K 线（westock 通道，全粒度实测可用）。

        周期支持矩阵（实测 2026-08-16，锚点 600519）：
        - day：✅ 经腾讯公网 urllib 直连（_get_daily_one），真实日线（零子进程开销）。
        - m1/m5/m15/m30/m60/m120：✅ 经 westock `kline --period <p>` 真实分钟级 K 线
          （分钟级仅 A 股个股支持）。
        - week/month/season/year：✅ 经 westock `kline --period <p>` 真实周期 K 线。
        参数/返回契约见 BaseDataProvider.get_kline（归一化到
        code/date/open/high/low/close/volume/amount）。

        不支持的 period（如 m250、未知字符串）→ 抛 InvalidParameterError，
        交由 data-engine 降级链路由到其他支持该周期的源。
        """
        if period in ("", "day"):
            # day：股票走已验证的腾讯公网直连路径（快、零子进程开销）；
            # 指数（sh000300/sh000001/sz399001 等）腾讯公网不支持，改走 westock CLI。
            if any(_is_index_symbol(s) for s in symbols):
                period = "day"  # 落入下方 westock CLI 分支
            else:
                return self.get_daily(symbols, start_date, end_date, adjust=adjust)

        if period in ("", "day"):
            # 指数 day：经 westock CLI 真实取数（_get_daily_one 仅支持股票）
            clean = _sanitize_code_list(symbols)
            if not clean:
                raise InvalidParameterError("westock", f"无有效代码可查 K 线: {symbols}")
            _RateGuard.throttle("westock")
            args = ["kline", ",".join(clean), "--period", "day", "--limit", str(_KLINE_LIMIT)]
            out = _run_westock(args)
            rows = self._parse_kline_cli(out, symbols, "day")
            if not rows:
                raise DataNotFoundError("westock", f"westock kline 无 {symbols} 日线数据")
            df = pd.DataFrame(rows)
            for col in ["date", "code", "open", "high", "low", "close", "volume", "amount"]:
                if col not in df.columns:
                    df[col] = float("nan")
            df = df[["date", "code", "open", "high", "low", "close", "volume", "amount"]]
            df = self._clip_date_range(df, start_date, end_date)
            return df.reset_index(drop=True)

        if period not in self._KLINE_CLI_PERIODS:
            raise InvalidParameterError(
                "westock",
                f"westock 通道不支持周期 {period!r}（支持集见 KLINE_PERIODS："
                "m1/m5/m15/m30/m60/m120/day/week/month/season/year）。"
                "m250 不存在，请用 m120；其余未知周期请降级至其他源。",
            )

        # 非 day 周期：经 westock CLI 真实取数
        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查 K 线: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError("westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}")

        _RateGuard.throttle("westock")
        # westock kline：kline <code[,code...]> --period <p> [--limit N] --raw
        # 注：--period 仅 westock 接受其允许集；--raw 输出 JSON 列表（见 _parse_westock_output）。
        # start/end 区间 westock kline 不直接支持，由调用方经 _clip_date_range 裁剪。
        args = ["kline", ",".join(clean), "--period", period, "--limit", str(_KLINE_LIMIT)]
        out = _run_westock(args)

        rows = self._parse_kline_cli(out, symbols, period)
        if not rows:
            raise DataNotFoundError("westock", f"westock kline 无 {symbols} 周期 {period} 数据")
        df = pd.DataFrame(rows)
        for col in ["date", "code", "open", "high", "low", "close", "volume", "amount"]:
            if col not in df.columns:
                df[col] = float("nan")
        df = df[["date", "code", "open", "high", "low", "close", "volume", "amount"]]
        df = self._clip_date_range(df, start_date, end_date)
        return df.reset_index(drop=True)

    @staticmethod
    def _parse_kline_cli(out: Any, symbols: List[str], period: str) -> List[Dict]:
        """从 westock kline --raw 的 JSON 列表解析为标准化 K 线行。

        westock kline 输出：[{date, open, last, high, low, volume, amount, exchange}]
        —— 注意字段名为 last（非 close）；date 对分钟级为 'YYYY-MM-DD HH:MM:SS'，
        对日/周/月/季/年为 'YYYY-MM-DD'。归一化映射 last→close。
        """
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            date_s = str(r.get("date") or "")
            try:
                parsed_date = pd.to_datetime(date_s)
            except (ValueError, TypeError):
                continue
            sym = symbols[0] if symbols else ""
            rows.append(
                {
                    "code": sym,
                    "date": parsed_date,
                    "open": float(r.get("open", "nan")),
                    "high": float(r.get("high", "nan")),
                    "low": float(r.get("low", "nan")),
                    "close": float(r.get("last", "nan")),
                    "volume": float(r.get("volume", 0) or 0),
                    "amount": float(r.get("amount", "nan") or "nan"),
                }
            )
        return rows

    @staticmethod
    def _clip_date_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        if df.empty or not start_date or not end_date:
            return df
        try:
            sd = pd.to_datetime(start_date)
            ed = pd.to_datetime(end_date)
        except Exception:
            return df
        mask = (df["date"] >= sd) & (df["date"] <= ed)
        return df[mask].reset_index(drop=True)

    # ── westock 全品类补充层（financial/capital_flow/dragon_tiger/shareholder）──
    def get_financial(self, symbols: List[str], report_date: str, fields: List[str]) -> pd.DataFrame:
        """财务数据（westock `finance`，三大表）。

        真实 westock 契约（westock-data-skillhub@1.0.5）：
          `finance <code> [--start D --end D | --num N] --raw`
          返回 {"sections":[ income行[], balance行[], cashflow行[] ]}，
          每行一只股票一个报告期（_date/code/BasicEPS/OperatingRevenue/... 原始报表科目）。
        westock 仅提供报表原始科目，不含 pe/pb/roe 等衍生指标 → 衍生列留 NaN（与降级口径一致）。
        disclosure_date 无原生披露日 → 回填 report_date。
        取数失败抛 DataNotFoundError/NetworkError → 引擎降级 baostock/akshare。
        """
        import numpy as np

        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查财务: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError("westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}")

        # 报告期对齐：非财报日（03-31/06-30/09-30/12-31）回退到最近前一个季度末，
        # 否则 westock 区间查询返回 null → 误降级 baostock。对齐后构造
        # --start <对齐日所在季度初> --end <对齐日>，westock 返回该期真实财报。
        aligned = _align_financial_report_date(report_date)
        if aligned:
            ay, am, ad = int(aligned[:4]), int(aligned[5:7]), int(aligned[8:10])
            # 对齐日所在季度初：03→01, 06→04, 09→07, 12→10
            q_start_month = {3: 1, 6: 4, 9: 7, 12: 10}[am]
            start = f"{ay}-{q_start_month:02d}-01"
            end = f"{ay}-{am:02d}-{ad:02d}"
            date_args = ["--start", start, "--end", end]
        else:
            # report_date 非法/空 → 取最近 1 期
            date_args = ["--num", "1"]

        _RateGuard.throttle("westock")
        out = _run_westock(["finance", ",".join(clean), *date_args])
        rows = self._parse_finance(out, symbols, report_date)
        if not rows:
            raise DataNotFoundError("westock", f"westock finance 无 {symbols} 财务数据")
        df = pd.DataFrame(rows)
        # 标准列齐全（缺失补 NaN）；westock 不提供估值/衍生指标，留 NaN 由引擎清洗补齐
        std_cols = [
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
        for col in std_cols:
            if col not in df.columns:
                df[col] = np.nan
        return df[std_cols]

    def _parse_finance(self, out: Dict[str, Any], symbols: List[str], report_date: str) -> List[Dict]:
        """从 westock finance 的 {sections:[income,balance,cashflow]} 解析为标准化财务行。

        sections[0]=利润表, [1]=资产负债表, [2]=现金流量表；每行一个报告期。
        按 code 聚合三表科目到一行；缺失字段/衍生指标留 NaN。
        """
        import numpy as np

        if not isinstance(out, dict):
            return []
        sections = out.get("sections") or []
        if not isinstance(sections, list) or not sections:
            return []
        # 取每个 section 的第一行（最新一期）
        income = (sections[0] or [{}])[0] if sections[0] else {}
        balance = (sections[1] or [{}])[0] if len(sections) > 1 and sections[1] else {}
        cashflow = (sections[2] or [{}])[0] if len(sections) > 2 and sections[2] else {}
        income = income if isinstance(income, dict) else {}
        balance = balance if isinstance(balance, dict) else {}
        cashflow = cashflow if isinstance(cashflow, dict) else {}

        rows = []
        for sym in symbols:
            tcode, _ = _normalize_to_tencent_code(sym)
            # 优先匹配 code 字段，否则回退首个 section 行（单标的场景）
            inc = (
                income
                if income.get("code") == tcode or len(symbols) == 1
                else next((r for r in (sections[0] or []) if isinstance(r, dict) and r.get("code") == tcode), income)
            )
            bal = (
                balance
                if balance.get("code") == tcode or len(symbols) == 1
                else next((r for r in (sections[1] or []) if isinstance(r, dict) and r.get("code") == tcode), balance)
            )
            cf = (
                cashflow
                if cashflow.get("code") == tcode or len(symbols) == 1
                else next((r for r in (sections[2] or []) if isinstance(r, dict) and r.get("code") == tcode), cashflow)
            )
            flat = {}
            flat.update(inc)
            flat.update(bal)  # 资产负债表科目补充
            flat.update(cf)  # 现金流量表科目补充
            if not flat:
                continue
            rpt = report_date or flat.get("date") or flat.get("EndDate") or ""
            rows.append(
                {
                    "code": sym,
                    "report_date": rpt,
                    "pe_ttm": np.nan,  # westock 不提供估值指标
                    "pb": np.nan,
                    "ps_ttm": np.nan,
                    "dv_ratio": np.nan,
                    "roe": self._num(flat.get("NetProfit")) / self._num(flat.get("SEWithoutMI"))
                    if (self._num(flat.get("NetProfit")) and self._num(flat.get("SEWithoutMI")))
                    else np.nan,
                    "roa": np.nan,
                    "gross_margin": self._num(flat.get("GrossProfitTTM")) / self._num(flat.get("OperatingRevenueTTM"))
                    if (self._num(flat.get("GrossProfitTTM")) and self._num(flat.get("OperatingRevenueTTM")))
                    else np.nan,
                    "net_margin": self._num(flat.get("NPParentCompanyOwnersTTM"))
                    / self._num(flat.get("OperatingRevenueTTM"))
                    if (self._num(flat.get("NPParentCompanyOwnersTTM")) and self._num(flat.get("OperatingRevenueTTM")))
                    else np.nan,
                    "revenue_growth": np.nan,
                    "profit_growth": np.nan,
                    "debt_ratio": self._num(flat.get("TotalLiability")) / self._num(flat.get("TotalAssets"))
                    if (self._num(flat.get("TotalLiability")) and self._num(flat.get("TotalAssets")))
                    else np.nan,
                    "current_ratio": self._num(flat.get("TotalCurrentAssets"))
                    / self._num(flat.get("TotalCurrentLiability"))
                    if (self._num(flat.get("TotalCurrentAssets")) and self._num(flat.get("TotalCurrentLiability")))
                    else np.nan,
                    "quick_ratio": np.nan,
                    "ocf": self._num(flat.get("NetOperateCashFlowTTM")),
                    "industry": flat.get("EnterpriseType", ""),
                    "name": flat.get("name", ""),
                    "disclosure_date": flat.get("InfoPublDate") or rpt or "",
                }
            )
        return rows

    def _num(self, v: Any) -> float:
        """安全转 float，失败返回 NaN（不抛，保证归一化不中断）。"""
        if v is None or v == "":
            return float("nan")
        try:
            return float(v)
        except (ValueError, TypeError):
            return float("nan")

    def get_capital_flow(self, symbols: List[str], start_date: str, end_date: str, **kwargs) -> pd.DataFrame:
        """资金面数据（westock `fund flow`，主力资金流向）。

        真实 westock 契约：'fund flow <code> [--date D | --start D --end D] --raw'
        返回 [{code,name,EndDate,MainNetFlow,MainInFlow,MainOutFlow,JumboNetFlow,
               MidNetFlow,RetailNetFlow,BlockNetFlow,SmallNetFlow,...}]（单日一行）。
        标准列：code/date/main_net_inflow/super_large_net/large_net/medium_net/small_net/
                north_net_inflow（9 列；north 在 --raw 无字段时留 NaN）。
        A股+港股支持；美股无 fund flow → 抛 InvalidParameterError（不降级）。
        取数失败抛 DataNotFoundError/NetworkError → 引擎降级 baostock/akshare。
        """
        import numpy as np

        # 美股无 fund flow（westock 限制）→ 直接 InvalidParameterError，避免无意义降级
        for s in symbols:
            if str(s).upper().endswith((".US", ".NYSE", ".NASDAQ")) or str(s).upper().startswith("US"):
                raise InvalidParameterError("westock", f"美股不支持 fund flow（westock 限制），请用 fund short: {s}")

        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查资金流: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError("westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}")

        _RateGuard.throttle("westock")
        args = ["fund", "flow", ",".join(clean)]
        # westock fund flow 用 --date 单日；若给区间则优先 --start/--end
        if start_date and end_date:
            args += ["--start", start_date, "--end", end_date]
        elif end_date:
            args += ["--date", end_date]
        elif start_date:
            args += ["--date", start_date]
        out = _run_westock(args)

        rows = self._parse_capital_flow(out, symbols)
        if not rows:
            raise DataNotFoundError("westock", f"westock fund flow 无 {symbols} 资金流数据")
        df = pd.DataFrame(rows)
        for col in [
            "code",
            "date",
            "main_net_inflow",
            "main_net_inflow_5d",
            "super_large_net",
            "large_net",
            "medium_net",
            "small_net",
            "north_net_inflow",
        ]:
            if col not in df.columns:
                df[col] = np.nan
        return df[
            [
                "code",
                "date",
                "main_net_inflow",
                "main_net_inflow_5d",
                "super_large_net",
                "large_net",
                "medium_net",
                "small_net",
                "north_net_inflow",
            ]
        ]

    def _parse_capital_flow(self, out: Dict[str, Any], symbols: List[str]) -> List[Dict]:
        """从 westock fund flow 的 flat list 解析为标准化资金流行。

        westock 字段约定（--raw）：MainNetFlow=主力净流入, JumboNetFlow=超大单净流入,
        MidNetFlow=中单净流入, RetailNetFlow=小单净流入, BlockNetFlow=大宗净流入,
        SmallNetFlow=小单(另), EndDate=日期。main_net_inflow_5d 无原生字段→留 NaN。
        """
        import numpy as np

        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            tcode = str(r.get("code") or r.get("SecuCode") or "")
            sym = self._code_from_digit(tcode, symbols) if tcode else (symbols[0] if symbols else "")
            rows.append(
                {
                    "code": sym,
                    "date": str(r.get("EndDate") or r.get("date") or ""),
                    "main_net_inflow": self._num(r.get("MainNetFlow")),
                    "main_net_inflow_5d": np.nan,
                    "super_large_net": self._num(r.get("JumboNetFlow")),
                    "large_net": self._num(r.get("BlockNetFlow")),
                    "medium_net": self._num(r.get("MidNetFlow")),
                    "small_net": self._num(r.get("RetailNetFlow") or r.get("SmallNetFlow")),
                    "north_net_inflow": np.nan,  # westock fund flow --raw 无北向单列
                }
            )
        return rows

    def get_dragon_tiger(self, symbols: List[str], start_date: str, end_date: str, **kwargs) -> pd.DataFrame:
        """龙虎榜数据（westock `lhb`，仅 A 股）。

        真实 westock 契约：'lhb --type <榜单类型> [--date D] --raw'
        返回[{排名,代码,名称,上榜天数,机构买入席位,机构买入额,买入占比,总买入额,净买入额,净占比}]
        —— 注意：westock lhb 返回的是【当日全市场榜单】，不按个股过滤，需在本地按 代码【精确匹配】。
        标准列：code/trade_date/has_data/reason/net_buy/seats（6 列）。
        HK/US 抛 InvalidParameterError（不降级）；取数失败抛 DataNotFoundError/NetworkError → 降级。
        """
        import numpy as np

        # lhb 仅 A 股（westock 限制）→ HK/US 直接 InvalidParameterError
        for s in symbols:
            if str(s).upper().endswith((".HK",)) or str(s).upper().startswith("HK"):
                raise InvalidParameterError("westock", f"龙虎榜仅支持 A 股，不含港股: {s}")
            if str(s).upper().endswith((".US", ".NYSE", ".NASDAQ")) or str(s).upper().startswith("US"):
                raise InvalidParameterError("westock", f"龙虎榜仅支持 A 股，不含美股: {s}")

        _RateGuard.throttle("westock")
        # 取查询日（优先 end_date，否则 start_date，否则今天）；westock lhb 仅支持单 --date
        lhb_date = end_date or start_date or ""
        lhb_args = ["lhb", "--type", "institution,hotmoney"]
        if lhb_date:
            lhb_args += ["--date", lhb_date]
        out = _run_westock(lhb_args)

        # 本地按代码【精确匹配】过滤（westock lhb 返回全市场当日数据）。
        # 归一化：去 sh/sz/hk/us 前缀后取完整数字串做 == 比对，杜绝子串误命中。
        query_digits = set()
        for s in symbols:
            tcode, _ = _normalize_to_tencent_code(s)
            query_digits.add(tcode[2:])  # 去 sh/sz 前缀，纯 6 位

        def _normalize_digit(code_val: str) -> str:
            s = str(code_val).strip()
            if len(s) > 2 and s[:2].isalpha() and s[2:].isdigit():
                s = s[2:]
            return "".join(c for c in s if c.isdigit())

        rows = []
        lhb_all = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(lhb_all, list):
            lhb_all = []
        for r in lhb_all:
            if not isinstance(r, dict):
                continue
            code_val = str(r.get("代码", "") or r.get("code", ""))
            if _normalize_digit(code_val) not in query_digits:
                continue
            net_buy = self._num(r.get("净买入额"))
            rows.append(
                {
                    "code": self._code_from_digit(code_val, symbols),
                    "trade_date": lhb_date.replace("-", "")[:8],
                    "has_data": True,
                    "reason": str(r.get("名称", "") or ""),
                    "net_buy": net_buy,
                    "seats": str(r.get("机构买入席位", "") or ""),
                }
            )
        # 区间内未上榜的标的补占位行（has_data=False）
        matched_codes = {row["code"] for row in rows}
        for s in symbols:
            if s not in matched_codes:
                rows.append(
                    {
                        "code": s,
                        "trade_date": lhb_date.replace("-", "")[:8],
                        "has_data": False,
                        "reason": "",
                        "net_buy": 0.0,
                        "seats": "",
                    }
                )
        if not rows:
            raise DataNotFoundError("westock", f"westock lhb 区间内无 {symbols} 上榜记录")
        df = pd.DataFrame(rows)
        for col in ["code", "trade_date", "has_data", "reason", "net_buy", "seats"]:
            if col not in df.columns:
                df[col] = np.nan if col in ("net_buy",) else ""
        return df[["code", "trade_date", "has_data", "reason", "net_buy", "seats"]]

    @staticmethod
    def _code_from_digit(code_val: str, symbols: List[str]) -> str:
        """从 westock 返回的纯代码反查 jingni-trader 内部格式（600519.SH 等）。"""
        digit = "".join(c for c in code_val if c.isdigit())[:6]
        for s in symbols:
            if digit in s:
                return s
        return code_val

    def get_shareholder(self, symbols: List[str], report_date: str = "", **kwargs) -> pd.DataFrame:
        """股东结构数据（westock `shareholder`）。

        真实 westock 契约：'shareholder <code[,code...]> --raw'
        返回 {"sections":[[持有人...],[持有人...]]}（sections[0] 为十大股东）。
        字段：no/name/holdShares/holdPct/holdChange。
        标准列：code/holder_name/hold_amount/hold_ratio/change_type/holder_type（6 列）。
        取数失败抛 DataNotFoundError/NetworkError → 引擎降级 baostock/akshare。
        """

        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查股东: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError("westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}")

        _RateGuard.throttle("westock")
        out = _run_westock(["shareholder", ",".join(clean)])

        rows = self._parse_shareholder(out, symbols)
        if not rows:
            raise DataNotFoundError("westock", f"westock shareholder 无 {symbols} 股东数据")
        df = pd.DataFrame(rows)
        for col in ["code", "holder_name", "hold_amount", "hold_ratio", "change_type", "holder_type"]:
            if col not in df.columns:
                df[col] = None
        return df[["code", "holder_name", "hold_amount", "hold_ratio", "change_type", "holder_type"]]

    def _parse_shareholder(self, out: Dict[str, Any], symbols: List[str]) -> List[Dict]:
        """从 westock shareholder 的 {sections:[[持有人...]]} 解析为标准化股东行。

        取 sections[0]（最新一期十大股东）；holdShares=持股数, holdPct=持股比例,
        holdChange=持股变动（>0 增持 / <0 减持 / =0 不变）。
        """
        if not isinstance(out, dict):
            return []
        sections = out.get("sections") or []
        holders = (sections[0] if sections and isinstance(sections[0], list) else []) or []
        if not isinstance(holders, list):
            return []
        rows = []
        for sym in symbols:
            tcode, _ = _normalize_to_tencent_code(sym)
            for h in holders:
                if not isinstance(h, dict):
                    continue
                change = self._num(h.get("holdChange"))
                change_type = "增持" if change > 0 else ("减持" if change < 0 else "不变")
                rows.append(
                    {
                        "code": sym,
                        "holder_name": str(h.get("name", "") or ""),
                        "hold_amount": self._num(h.get("holdShares")),
                        "hold_ratio": self._num(h.get("holdPct")),
                        "change_type": change_type,
                        "holder_type": "十大股东",
                    }
                )
        return rows

    # ── 腾讯公网不提供的数据类型：保持原语义 ──
    # 注：原「get_stock_list 显式 NotImplementedError」占位已被下方「基础数据 basic」
    #     的真实实现（connect --exchange sh|sz 聚合）取代，此处不再保留死代码。

    def get_adj_factor(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """复权因子（westock `kline --fq`，不复权/前复权/后复权三价还原）。

        westock kline 真实支持 --fq qfq/hfq；通过同一标的的 不复权价 与 前复权价
        反算复权因子（adj_factor = qfq_close / raw_close），归一化到标准契约
        code/date/adj_factor。仅 A 股个股（分钟级需 --start/--end，此处用日线 day）。
        A 股+港股支持；美股无 → 抛 InvalidParameterError（不降级）。
        取数失败抛 DataNotFoundError/NetworkError → 引擎降级 baostock/akshare/local。
        """
        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查复权因子: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError(
                "westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}"
            )

        _RateGuard.throttle("westock")
        args = ["kline", ",".join(clean), "--period", "day", "--fq", "qfq", "--limit", str(_KLINE_LIMIT)]
        qfq_out = _run_westock(args)
        _RateGuard.throttle("westock")
        # 不复权价：westock kline 不传 --fq 即返回原始价（raw）
        raw_out = _run_westock(
            ["kline", ",".join(clean), "--period", "day", "--limit", str(_KLINE_LIMIT)]
        )
        rows = self._parse_adj_factor(qfq_out, raw_out, symbols)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {symbols} 复权因子数据")
        df = pd.DataFrame(rows)
        for col in ["code", "date", "adj_factor"]:
            if col not in df.columns:
                df[col] = float("nan")
        df = df[["code", "date", "adj_factor"]]
        # westock --limit 返回最近 N 条（非区间命中）；若区间裁剪后为空，
        # 说明请求区间早于可得数据，回退到未裁剪全量（确保有数据可降级/使用）。
        clipped = self._clip_date_range(df, start_date, end_date)
        if not clipped.empty:
            df = clipped
        return df.reset_index(drop=True)

    @staticmethod
    def _parse_adj_factor(qfq_out: Any, raw_out: Any, symbols: List[str]) -> List[Dict]:
        """由前复权价 / 不复权价反算复权因子（adj_factor = qfq / raw）。"""
        qfq = WestockAdapter._parse_kline_cli(qfq_out, symbols, "day")
        raw = WestockAdapter._parse_kline_cli(raw_out, symbols, "day")
        if not qfq or not raw:
            return []
        raw_by_date = {r["date"]: r["close"] for r in raw if r.get("date") is not None}
        sym = symbols[0] if symbols else ""
        rows = []
        for r in qfq:
            d = r.get("date")
            raw_close = raw_by_date.get(d)
            qfq_close = r.get("close")
            if raw_close and qfq_close and raw_close != 0:
                rows.append(
                    {"code": sym, "date": d, "adj_factor": round(qfq_close / raw_close, 6)}
                )
        return rows

    # ── 基础数据 basic（Phase 2：全市场列表 / 个股简况 / 交易日历）────────────
    def get_stock_list(self) -> pd.DataFrame:
        """全市场股票列表（westock `connect --exchange sh|sz` 陆股通标的范围聚合）。

        说明：westock 无「全 A 股无条件列表」端点（search 强制要求关键词），
        故采用 `connect --exchange sh` + `connect --exchange sz` 聚合陆股通（沪深港通）
        标的范围——这是 westock 能稳定给出的、覆盖最全的 A 股标的清单（约 2 千余只）。
        标准列：code/name/industry/list_date/is_st（industry/list_date 不提供 → 留空；
        is_st 默认 False）。
        若两交易所均返回空 → 抛 DataNotFoundError → 引擎降级 local（local 含全量 A 股列表）。
        """
        import numpy as np

        rows: List[Dict] = []
        for exch in ("sh", "sz"):
            _RateGuard.throttle("westock")
            try:
                out = _run_westock(["connect", "--exchange", exch, "--limit", "500"])
            except (DataNotFoundError, NetworkError) as e:
                # 单交易所失败不致命，继续另一交易所
                continue
            rows.extend(self._parse_connect_list(out))
        if not rows:
            raise DataNotFoundError(
                "westock",
                "westock connect 两交易所均无陆股通标的返回，触发降级到 local",
            )
        df = pd.DataFrame(rows)
        for col in ["code", "name", "industry", "list_date", "is_st"]:
            if col not in df.columns:
                df[col] = "" if col in ("industry", "list_date") else (False if col == "is_st" else np.nan)
        return df[["code", "name", "industry", "list_date", "is_st"]].drop_duplicates(subset=["code"]).reset_index(drop=True)

    @staticmethod
    def _parse_connect_list(out: Any) -> List[Dict]:
        """从 westock connect 的 list 解析为标准化股票列表行。"""
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            code = str(r.get("code") or "")
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": str(r.get("name") or ""),
                    "industry": "",
                    "list_date": "",
                    "is_st": False,
                }
            )
        return rows

    def get_stock_info(self, symbols: List[str]) -> pd.DataFrame:
        """个股基本信息（westock `profile`）。

        真实 westock 契约：`profile <code[,code...]> --raw`
        返回 {success,data:{code,name,listedDate,business,website,industry,issuePrice,
        regCapital,establishDate,chairman,...}}。
        标准列：code/name/industry/list_date/total_shares（部分字段 westock 不提供 → 留空）。
        A 股+港股+美股支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        import numpy as np

        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查个股简况: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError(
                "westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}"
            )
        _RateGuard.throttle("westock")
        out = _run_westock(["profile", ",".join(clean)])
        rows = self._parse_profile(out, symbols)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {symbols} 个股简况")
        df = pd.DataFrame(rows)
        for col in ["code", "name", "industry", "list_date", "total_shares"]:
            if col not in df.columns:
                df[col] = np.nan if col == "total_shares" else ""
        return df[["code", "name", "industry", "list_date", "total_shares"]]

    @staticmethod
    def _parse_profile(out: Any, symbols: List[str]) -> List[Dict]:
        """从 westock profile 的 {data:{...}} 解析为标准化个股简况行。"""
        data = out.get("data") if isinstance(out, dict) else None
        if not isinstance(data, dict):
            return []
        # profile 支持批量逗号分隔，但返回为单 data 对象（取首个 symbol 信息）
        sym = symbols[0] if symbols else ""
        return [
            {
                "code": sym,
                "name": str(data.get("name") or ""),
                "industry": str(data.get("industry") or data.get("sector") or ""),
                "list_date": str(data.get("listedDate") or ""),
                "total_shares": float(data["regCapital"]) if str(data.get("regCapital", "")).replace(".", "", 1).isdigit() else float("nan"),
            }
        ]

    def get_trade_calendar(self, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """交易日历（westock `trade-calendar`）。

        真实 westock 契约：`trade-calendar [--year YYYY | --start D --end D] [--trading-only] --raw`
        返回 {listCode,listName,startDate,endDate,days:[{date,isTrading,
        isLastTradeDayInWeek/Month/Quarter/Year}],total}。
        标准列：date/is_trading/month_end/quarter_end/year_end（周末标记 westock 不提供 → 留 False）。
        A 股（沪深京）支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        import numpy as np

        _RateGuard.throttle("westock")
        args = ["trade-calendar"]
        if start_date and end_date:
            args += ["--start", start_date, "--end", end_date]
        else:
            # 默认查当年（westock 必须给 --year 或区间，否则报错）
            import datetime as _dt

            args += ["--year", str(_dt.date.today().year)]
        try:
            out = _run_westock(args)
        except (DataNotFoundError, NetworkError) as e:
            raise DataNotFoundError("westock", f"westock 交易日历失败: {e}", original=e)
        rows = self._parse_trade_calendar(out)
        if not rows:
            raise DataNotFoundError("westock", "westock 交易日历返回空")
        df = pd.DataFrame(rows)
        for col in ["date", "is_trading", "week_end", "month_end", "quarter_end", "year_end"]:
            if col not in df.columns:
                df[col] = False if col != "date" else ""
        return df[["date", "is_trading", "week_end", "month_end", "quarter_end", "year_end"]]

    @staticmethod
    def _parse_trade_calendar(out: Any) -> List[Dict]:
        """从 westock trade-calendar 的 {days:[...]} 解析为标准化日历行。"""
        days = (out or {}).get("days") if isinstance(out, dict) else None
        if not isinstance(days, list):
            return []
        rows = []
        for d in days:
            if not isinstance(d, dict):
                continue
            rows.append(
                {
                    "date": str(d.get("date") or ""),
                    "is_trading": bool(d.get("isTrading", False)),
                    "week_end": bool(d.get("isLastTradeDayInWeek", False)),
                    "month_end": bool(d.get("isLastTradeDayInMonth", False)),
                    "quarter_end": bool(d.get("isLastTradeDayInQuarter", False)),
                    "year_end": bool(d.get("isLastTradeDayInYear", False)),
                }
            )
        return rows

    # ── 参考数据 reference（Phase 2：分红 / 停复牌 / 限售解禁 / 业绩预告）────────
    def get_dividend(self, symbols: List[str], years: int = 3) -> pd.DataFrame:
        """分红派息（westock `dividend list`）。

        真实 westock 契约：`dividend list <code[,code...]> [--years N | --all] --raw`
        返回 [{reportEndDate,dividendFlag,dividendType,procedure,rightRegDate,
        exDiviDate,bonusShareRatio,tranAddShareRatio,cashDiviRMB,
        totalCashDiviComRMB,dividendPlan}]。
        标准列：code/report_end_date/ex_divi_date/cash_divi_per_10/dividend_plan/procedure。
        A 股+港股+美股支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        clean = _sanitize_code_list(symbols)
        if not clean:
            raise InvalidParameterError("westock", f"无有效代码可查分红: {symbols}")
        if len(clean) > _WESTOCK_MAX_BATCH:
            raise InvalidParameterError(
                "westock", f"westock 批量标的数 {len(clean)} 超过上限 {_WESTOCK_MAX_BATCH}"
            )
        _RateGuard.throttle("westock")
        out = _run_westock(["dividend", "list", ",".join(clean), "--years", str(years)])
        rows = self._parse_dividend(out, symbols)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {symbols} 分红数据")
        df = pd.DataFrame(rows)
        for col in ["code", "report_end_date", "ex_divi_date", "cash_divi_per_10", "dividend_plan", "procedure"]:
            if col not in df.columns:
                df[col] = ""
        return df[["code", "report_end_date", "ex_divi_date", "cash_divi_per_10", "dividend_plan", "procedure"]]

    @staticmethod
    def _parse_dividend(out: Any, symbols: List[str]) -> List[Dict]:
        """从 westock dividend list 的 list 解析为标准化分红行。"""
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        sym = symbols[0] if symbols else ""
        for r in data:
            if not isinstance(r, dict):
                continue
            # dividendPlan 形如 "10派280.242元" → 提取每 10 股现金（元）
            plan = str(r.get("dividendPlan") or "")
            cash = ""
            import re as _re

            m = _re.search(r"派([\d.]+)元", plan)
            if m:
                cash = m.group(1)
            rows.append(
                {
                    "code": sym,
                    "report_end_date": str(r.get("reportEndDate") or ""),
                    "ex_divi_date": str(r.get("exDiviDate") or ""),
                    "cash_divi_per_10": cash,
                    "dividend_plan": plan,
                    "procedure": str(r.get("procedure") or ""),
                }
            )
        return rows

    def get_suspend_resume(self, market: str = "hs", trade_date: str = "") -> pd.DataFrame:
        """停复牌（westock `suspension`）。

        真实 westock 契约：`suspension [--market hs|hk|us] --raw`
        返回 [{code,name,status,statusDesc,suspendDate,resumeDate,reason}]。
        标准列：code/name/status/suspend_date/resume_date/reason。
        A 股+港股+美股支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        import numpy as np

        _RateGuard.throttle("westock")
        args = ["suspension", "--market", market]
        try:
            out = _run_westock(args)
        except (DataNotFoundError, NetworkError) as e:
            raise DataNotFoundError("westock", f"westock 停复牌列表失败: {e}", original=e)
        rows = self._parse_suspend(out)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {market} 停复牌数据")
        df = pd.DataFrame(rows)
        for col in ["code", "name", "status", "suspend_date", "resume_date", "reason"]:
            if col not in df.columns:
                df[col] = "" if col != "status" else np.nan
        return df[["code", "name", "status", "suspend_date", "resume_date", "reason"]]

    @staticmethod
    def _parse_suspend(out: Any) -> List[Dict]:
        """从 westock suspension 的 list 解析为标准化停复牌行。"""
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            rows.append(
                {
                    "code": str(r.get("code") or ""),
                    "name": str(r.get("name") or ""),
                    "status": str(r.get("status") or ""),
                    "suspend_date": str(r.get("suspendDate") or ""),
                    "resume_date": str(r.get("resumeDate") or ""),
                    "reason": str(r.get("reason") or ""),
                }
            )
        return rows

    def get_locked_shares(self, trade_date: str) -> pd.DataFrame:
        """限售股解禁（westock `calendar --event lockup_release`）。

        真实 westock 契约：`calendar --event lockup_release --date YYYY-MM-DD --raw`
        返回 [{date,market,symbol,amount,percent,stockName}]。
        标准列：date/symbol/name/amount/percent。
        A 股+港股+美股支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        _RateGuard.throttle("westock")
        args = ["calendar", "--event", "lockup_release"]
        if trade_date:
            args += ["--date", trade_date]
        try:
            out = _run_westock(args)
        except (DataNotFoundError, NetworkError) as e:
            raise DataNotFoundError("westock", f"westock 限售解禁日历失败: {e}", original=e)
        rows = self._parse_locked_shares(out)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {trade_date} 限售解禁数据")
        df = pd.DataFrame(rows)
        for col in ["date", "symbol", "name", "amount", "percent"]:
            if col not in df.columns:
                df[col] = ""
        return df[["date", "symbol", "name", "amount", "percent"]]

    @staticmethod
    def _parse_locked_shares(out: Any) -> List[Dict]:
        """从 westock calendar lockup_release 的 list 解析为标准化解禁行。"""
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            rows.append(
                {
                    "date": str(r.get("date") or ""),
                    "symbol": str(r.get("symbol") or ""),
                    "name": str(r.get("stockName") or ""),
                    "amount": str(r.get("amount") or ""),
                    "percent": str(r.get("percent") or ""),
                }
            )
        return rows

    def get_forecast(self, trade_date: str) -> pd.DataFrame:
        """业绩预告（westock `calendar --event financial_report`）。

        真实 westock 契约：`calendar --event financial_report --date YYYY-MM-DD --raw`
        返回 [{date,market,symbol,mgsy,mgtbyl,stockName}]。
        标准列：date/symbol/name/eps/pre_year_eps（预告仅给 EPS 区间，净利润区间留空待引擎补全）。
        A 股+港股+美股支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        _RateGuard.throttle("westock")
        args = ["calendar", "--event", "financial_report"]
        if trade_date:
            args += ["--date", trade_date]
        try:
            out = _run_westock(args)
        except (DataNotFoundError, NetworkError) as e:
            raise DataNotFoundError("westock", f"westock 业绩预告日历失败: {e}", original=e)
        rows = self._parse_forecast(out)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {trade_date} 业绩预告数据")
        df = pd.DataFrame(rows)
        for col in ["date", "symbol", "name", "eps", "pre_year_eps"]:
            if col not in df.columns:
                df[col] = ""
        return df[["date", "symbol", "name", "eps", "pre_year_eps"]]

    @staticmethod
    def _parse_forecast(out: Any) -> List[Dict]:
        """从 westock calendar financial_report 的 list 解析为标准化预告行。"""
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            rows.append(
                {
                    "date": str(r.get("date") or ""),
                    "symbol": str(r.get("symbol") or ""),
                    "name": str(r.get("stockName") or ""),
                    "eps": str(r.get("mgsy") or ""),
                    "pre_year_eps": str(r.get("mgtbyl") or ""),
                }
            )
        return rows

    # ── 标的类型扩展（Phase 3：指数成份股）────────────────────
    def get_index_constituent(self, index_symbol: str) -> pd.DataFrame:
        """指数成份股（westock `index constituent`）。

        真实 westock 契约：`index constituent <指数代码> [--limit N] --raw`
        返回 [{code,name}]（成份股清单）。
        标准列：code/name。仅 A 股+港股指数支持。
        取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        import numpy as np

        tcode, _ = _normalize_to_tencent_code(index_symbol) if not str(index_symbol).startswith(("sh", "sz", "hk")) else (str(index_symbol), "index")
        _RateGuard.throttle("westock")
        try:
            out = _run_westock(["index", "constituent", tcode])
        except (DataNotFoundError, NetworkError) as e:
            raise DataNotFoundError("westock", f"westock 指数成份股失败: {e}", original=e)
        rows = self._parse_index_constituent(out)
        if not rows:
            raise DataNotFoundError("westock", f"westock 无 {index_symbol} 成份股")
        df = pd.DataFrame(rows)
        for col in ["code", "name"]:
            if col not in df.columns:
                df[col] = np.nan if col == "code" else ""
        return df[["code", "name"]]

    @staticmethod
    def _parse_index_constituent(out: Any) -> List[Dict]:
        """从 westock index constituent 的 list 解析为标准化成份股行。"""
        data = out if isinstance(out, list) else (out.get("data") if isinstance(out, dict) else None)
        if not isinstance(data, list):
            return []
        rows = []
        for r in data:
            if not isinstance(r, dict):
                continue
            code = str(r.get("code") or "")
            if not code:
                continue
            rows.append({"code": code, "name": str(r.get("name") or "")})
        return rows

    # ── 财务扩展（Phase 2：三大报表原始科目归集，复用 finance 通道）────────
    def get_financial_report(self, symbols: List[str], report_date: str) -> pd.DataFrame:
        """财报数据（westock `finance`，复用 get_financial 同通道）。

        与 get_financial 共享 westock `finance` 子命令，仅返回口径聚焦三大报表
        原始科目（income/balance/cashflow 三表聚合）。标准列对齐 get_financial。
        A 股+港股+美股支持；取数失败抛 DataNotFoundError/NetworkError → 引擎降级 local。
        """
        return self.get_financial(symbols, report_date, fields=[])


def _safe_json_loads(body: str):
    """腾讯接口偶发返回含 BOM / 前后噪点，做容错解析。"""
    s = body.strip()
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff")
    # 找到第一个 { 起解析
    idx = s.find("{")
    if idx > 0:
        s = s[idx:]
    return json.loads(s)
