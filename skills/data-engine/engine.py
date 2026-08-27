"""
A股数据引擎主逻辑
负责调度适配器、数据清洗、本地存储

数据源架构（REQ-2026-08-14 实施最终版，共 11 个 backend）：
- 免费链 5 源（默认降级）：local → tencent → baostock → akshare → websearch
  （local 本地缓存优先级高于 tencent；见 config.DEFAULT_DATA_SOURCES）
- 按需调用组 6 源（注册可用、不进默认链）：tushare / gm / xtquant / tdxquant / wind / ifind
- neodata 不接入（离开 workbuddy 无法使用，取数路径不得出现）
- tencent 为零鉴权直连腾讯公网行情（qt.gtimg.cn / web.ifzq.gtimg.cn）
- 降级条件见 config.DATA_FALLBACK_RULES；触发降级的异常类型集中在 errors.FALLBACK_TRIGGERING_ERRORS
"""
from __future__ import annotations

import os
import sys
import subprocess
import logging
import importlib
from typing import List, Dict, Any, Callable

# 注意：不要在这里 sys.path.insert，会破坏 from scripts.xxx 的包导入
# 由调用方负责正确设置 sys.path

import pandas as pd
import numpy as np

from scripts.config import (
    DATA_FORMAT,
    ADJUST_MODE,
    DEFAULT_DATA_SOURCES,
    SUPPORTED_BACKENDS,
    DATA_FALLBACK_RULES,
    ALLOW_SYNTHETIC_FALLBACK,
    AUTO_INSTALL_BACKENDS,
    BACKEND_PIP_PACKAGES,
    notify_supported_backends,
)
from scripts.base.base_data_provider import BaseDataProvider
from scripts.data_types import DATA_TYPES
from scripts.errors import (
    DataSourceError,
    QuotaExceededError,
    RateLimitError,
    NetworkError,
    InvalidParameterError,
    BlacklistedError,
    DataNotFoundError,
    FALLBACK_TRIGGERING_ERRORS,
)


# 适配器注册表
# 注意：websearch 适配器需要 web_search_fn 注入，特殊处理
# REQ-2026-08-14 实施（Damon 最终拍板）：恢复 9 旧 adapter + 接入 tencent，共 11 个 backend。
# 免费链 5 源：local/tencent/baostock/akshare/websearch（默认链启用）；
# 按需调用组 6 源：tushare/gm/xtquant/tdxquant/wind/ifind（注册可用、不进默认链）。
# ⚠️ neodata 不接入（离开 workbuddy 无法使用）。
_ADAPTER_REGISTRY = {
    "westock": ("scripts.adapters.westock_adapter", "WestockAdapter", {}),
    "local": ("scripts.adapters.local_adapter", "LocalAdapter", {}),
    "tushare": ("scripts.adapters.tushare_adapter", "TushareAdapter", {}),
    "baostock": ("scripts.adapters.baostock_adapter", "BaostockAdapter", {}),
    "akshare": ("scripts.adapters.akshare_adapter", "AkshareAdapter", {}),
    "websearch": ("scripts.adapters.websearch_adapter", "WebSearchAdapter", {"web_search_fn": None}),
    "xtquant": ("scripts.adapters.xtquant_adapter", "XtQuantAdapter", {}),
    "gm": ("scripts.adapters.gm_adapter", "GmAdapter", {}),
    "tdxquant": ("scripts.adapters.tdxquant_adapter", "TdxQuantAdapter", {}),
    "wind": ("scripts.adapters.wind_adapter", "WindAdapter", {}),
    "ifind": ("scripts.adapters.ifind_adapter", "IfindAdapter", {}),
}


# 触发降级的异常类型集合（从 errors 导入并复述，方便在引擎内引用）
_TRIGGERING_ERRORS = FALLBACK_TRIGGERING_ERRORS


def _load_adapter(backend: str, **extra_kwargs) -> BaseDataProvider:
    """动态加载指定数据源的适配器

    若适配器所需的第三方库未安装，且 AUTO_INSTALL_BACKENDS 开启，
    会先尝试 pip install 再加载；安装失败才判定该数据源不可用。
    """
    if backend not in _ADAPTER_REGISTRY:
        raise ValueError(f"不支持的数据源: {backend}。系统支持的数据源: {', '.join(SUPPORTED_BACKENDS)}")
    module_path, class_name, default_kwargs = _ADAPTER_REGISTRY[backend]
    # 合并默认参数和传入参数
    merged_kwargs = {**default_kwargs, **extra_kwargs}

    # 缺失依赖：先尝试自动安装，再决定是否可用
    if AUTO_INSTALL_BACKENDS and not _ensure_backend_installed(backend):
        raise DataSourceError(backend, f"数据源 {backend} 所需的依赖无法自动安装，已跳过该数据源")

    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        raise DataSourceError(backend, f"导入适配器模块失败: {e}") from e
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise DataSourceError(backend, f"适配器 {class_name} 不存在")
    return cls(**merged_kwargs)


# 已尝试过自动安装的数据源缓存，避免重复安装
_INSTALL_CACHE: Dict[str, bool] = {}


# 默认国内加速镜像（清华 TUNA）。使用 setdefault 注入 PIP_INDEX_URL，
# 保证用户在环境变量/命令行已指定的镜像不被覆盖。
_DEFAULT_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _pip_install(pkg: str) -> bool:
    """通过当前解释器执行 pip install，返回是否成功。

    默认走国内加速镜像（清华 TUNA）；用户已设置 PIP_INDEX_URL 时尊重其配置。
    """
    # setdefault：仅当用户未显式指定镜像时才注入默认加速源
    os.environ.setdefault("PIP_INDEX_URL", _DEFAULT_PIP_INDEX_URL)
    try:
        logger.info(f"检测到依赖缺失，尝试自动安装: pip install {pkg}")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            logger.warning(f"pip install {pkg} 失败 (returncode={proc.returncode}):\n{proc.stderr[-800:]}")
            return False
        logger.info(f"pip install {pkg} 成功")
        return True
    except Exception as e:
        logger.warning(f"pip install {pkg} 执行异常: {e}")
        return False


def _ensure_backend_installed(backend: str) -> bool:
    """确保某后端所需第三方库已安装；未安装则尝试自动 pip install。

    返回 True 表示依赖已就绪（可继续加载适配器），
    False 表示自动安装失败（该数据源应被跳过）。
    """
    if backend in _INSTALL_CACHE:
        return _INSTALL_CACHE[backend]

    pkgs = BACKEND_PIP_PACKAGES.get(backend, [])
    if not pkgs:
        # 无第三方依赖（如 websearch）
        _INSTALL_CACHE[backend] = True
        return True

    ready = True
    for pkg in pkgs:
        try:
            importlib.import_module(pkg)
            continue  # 已安装
        except Exception:
            pass
        # 未安装 -> 尝试安装
        if not _pip_install(pkg):
            ready = False
            break
        # 安装后再次确认可导入
        try:
            importlib.import_module(pkg)
        except Exception:
            ready = False
            break

    _INSTALL_CACHE[backend] = ready
    if not ready:
        logger.warning(f"数据源 {backend} 依赖自动安装失败，将跳过该数据源")
    return ready


logger = logging.getLogger("data-engine")


# 已通知标记（避免重复打印）
_NOTIFIED = False


class DataEngine:
    """A股数据引擎（v3：精准降级 + 模拟数据兜底）"""

    def __init__(
        self,
        provider: BaseDataProvider | None = None,
        data_sources: List[str] | None = None,
        web_search_fn: Callable[[str], str] | None = None,
    ):
        """
        参数:
            provider: 自定义适配器（覆盖整个降级链）
            data_sources: 数据源优先级链（默认取 config.DEFAULT_DATA_SOURCES）
                         默认: ["local", "westock"]（见 config.DEFAULT_DATA_SOURCES）
            web_search_fn: 注入给 websearch 适配器的搜索函数（仅历史遗留接口，
                          私有化裁剪版不再启用外网搜索）
        """
        global _NOTIFIED
        self.web_search_fn = web_search_fn
        self.data_sources = data_sources if data_sources is not None else list(DEFAULT_DATA_SOURCES)

        # 校验 data_sources 中所有项都是支持的源
        unknown = [b for b in self.data_sources if b not in SUPPORTED_BACKENDS]
        if unknown:
            raise ValueError(f"data_sources 包含未知源 {unknown}。系统支持: {', '.join(SUPPORTED_BACKENDS)}")

        # websearch 注入函数保存在实例上，加载适配器时按实例注入（不修改全局注册表）
        self.web_search_fn = web_search_fn

        self.provider = provider or self._init_provider_with_fallback()
        # 用于在 fetch_and_clean 中告知调用方本次是否走了模拟数据
        self.is_synthetic = False

        # 首次初始化时打印支持的数据源全景
        # GAP-1 修复：日志打印（含非 ASCII 字形/中文）在 GBK 控制台可能抛
        # UnicodeEncodeError；此处兜底，避免日志编码失败中断 DataEngine 初始化。
        if not _NOTIFIED:
            try:
                notify_supported_backends()
            except UnicodeEncodeError as e:
                logger.warning(f"数据源全景提示打印被跳过（日志编码异常）: {e}")
            _NOTIFIED = True

    # ------------------------------------------------------------------
    # 多源降级（核心 v3）
    # ------------------------------------------------------------------

    def _websearch_extra_kwargs(self, backend: str) -> Dict[str, Any]:
        """按实例为 websearch 适配器注入搜索函数，避免修改全局注册表。"""
        if backend == "websearch" and getattr(self, "web_search_fn", None):
            return {"web_search_fn": self.web_search_fn}
        return {}

    def _init_provider_with_fallback(self) -> BaseDataProvider:
        """
        按 data_sources 顺序尝试初始化 provider；
        第一个初始化成功的源被选中（不实际拉数据）
        """
        last_err: Exception | None = None
        for backend in self.data_sources:
            try:
                logger.info(f"尝试加载数据源适配器: {backend}")
                provider = _load_adapter(backend, **self._websearch_extra_kwargs(backend))
                logger.info(f"数据源 {backend} 加载成功")
                self.backend = backend
                return provider
            except Exception as e:
                last_err = e
                logger.warning(f"数据源 {backend} 初始化失败: {e}，尝试下一个")
                continue
        raise RuntimeError(f"所有数据源都初始化失败。降级链: {self.data_sources}，最后错误: {last_err}")

    def _should_fallback(self, backend: str, exc: Exception) -> bool:
        """
        判断某个异常是否应触发【降级到下一源】

        决策依据：
        1. 异常类型在 FALLBACK_TRIGGERING_ERRORS 中
        2. 进一步核对 config.DATA_FALLBACK_RULES 中该源对应的 trigger_errors
           （防止 tushare 的限频被误判为 baostock 的限频）
        3. 对 InvalidParameterError 做特殊判断：仅 token/认证类错误才降级
        """
        # 1) 类型白名单
        if not isinstance(exc, _TRIGGERING_ERRORS):
            return False

        # 2) 对应源的降级规则
        rule = DATA_FALLBACK_RULES.get(backend, {})
        trigger_errs_str = rule.get("trigger_errors", "")
        trigger_err_names = {name.strip() for name in trigger_errs_str.split(",") if name.strip()}
        exc_type_name = type(exc).__name__
        if trigger_err_names and exc_type_name not in trigger_err_names:
            # 该异常类型不在此源允许的降级错误列表里
            # 例外：InvalidParameterError 永远允许（因为引擎已判定为 token/auth）
            if exc_type_name != "InvalidParameterError":
                return False

        # 3) InvalidParameterError 仅在 token/认证场景下才降级
        if isinstance(exc, InvalidParameterError):
            msg = exc.message.lower() if hasattr(exc, "message") else str(exc).lower()
            is_token_error = any(
                kw in msg
                for kw in (
                    "token",
                    "认证",
                    "authorization",
                    "登录",
                    "登录失败",
                    "access key",
                    "api key",
                    "您的token",
                )
            )
            return is_token_error

        return True

    def _try_fetch_with_fallback(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        adjust: str,
        exclude_st: bool,
        exclude_new: bool,
        min_listed_days: int,
        fill_suspend: bool,
    ) -> pd.DataFrame:
        """
        尝试拉取数据，按 data_sources 链切换（v3 精准降级）：
        每个源失败时，依据 DATA_FALLBACK_RULES 中该源对应的 trigger_errors
        来决定是否降级。
        走完整个降级链后仍未拿到数据 → 走模拟数据兜底。
        """
        tried_backends: List[str] = []
        last_errors: Dict[str, str] = {}

        for idx, backend in enumerate(self.data_sources):
            tried_backends.append(backend)
            # 切换 provider
            if idx > 0 or self.backend != backend:
                logger.info(f"切换到数据源: {backend}（链路: {' → '.join(tried_backends)}）")
                try:
                    self.provider = _load_adapter(backend, **self._websearch_extra_kwargs(backend))
                    self.backend = backend
                except Exception as e:
                    last_errors[backend] = f"初始化失败: {e}"
                    logger.warning(f"切换到 {backend} 失败: {e}，尝试下一个")
                    continue

            try:
                logger.info(f"使用数据源 {backend} 获取 {len(symbols)} 只股票的日线数据")
                df = self.provider.get_daily(symbols, start_date, end_date, adjust=adjust)
                if not df.empty:
                    if idx > 0:
                        logger.info(f"✓ 数据源 {backend} 拉取成功 {len(df)} 行（链路: {' → '.join(tried_backends)}）")
                    self.is_synthetic = False
                    return df
                else:
                    last_errors[backend] = "返回空数据"
                    logger.warning(f"数据源 {backend} 返回空数据，尝试下一个源")
                    continue
            except Exception as e:
                # 统一转字符串、记录
                msg = getattr(e, "message", str(e))
                last_errors[backend] = f"{type(e).__name__}: {msg}"

                if self._should_fallback(backend, e):
                    reason = DATA_FALLBACK_RULES.get(backend, {}).get("downgrade_reason", "")
                    logger.warning(f"数据源 {backend} 触发【{reason or type(e).__name__}】（{msg}），自动降级")
                    continue

                # 不可降级：直接抛
                if isinstance(e, DataSourceError):
                    logger.error(f"数据源 {backend} 错误（{msg}），不切换")
                else:
                    logger.error(f"数据源 {backend} 未知异常（{msg}），不切换")
                raise

        # 走完整个降级链仍失败
        logger.error(
            f"data_sources {self.data_sources} 中所有数据源都失败或返回空。已尝试: {' → '.join(tried_backends)}"
        )
        for k, v in last_errors.items():
            logger.error(f"  • {k}: {v}")

        # ---- 模拟数据兜底 ----
        if not ALLOW_SYNTHETIC_FALLBACK:
            raise RuntimeError(
                f"所有数据源都失败，且 ALLOW_SYNTHETIC_FALLBACK=False。"
                f"已尝试: {' → '.join(tried_backends)}，错误: {last_errors}"
            )

        logger.warning("=" * 60)
        logger.warning("⚠️  所有外部数据源均不可用，启用【模拟数据 fallback】")
        logger.warning("  这意味着回测结果仅供流程验证，不能用于真实交易决策。")
        logger.warning(f"  降级链: {' → '.join(tried_backends)}")
        for k, v in last_errors.items():
            logger.warning(f"    • {k}: {v}")
        logger.warning("=" * 60)

        self.is_synthetic = True
        self.backend = "synthetic"
        return self._generate_synthetic_data(symbols, start_date, end_date)

    # ------------------------------------------------------------------
    # 模拟数据生成（v3 兜底）
    # ------------------------------------------------------------------

    def _generate_synthetic_data(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        生成与真实 A 股日线统计特征近似的模拟数据。

        设计原则：
        - 仅供【流程跑通】和【集成测试】使用，不可用于真实交易
        - 满足 schema：date, code, open, high, low, close, vol, is_st, is_limit_up/down
        - 几何布朗运动 + 微小趋势，让后续回测引擎能正常推进
        - 每个标的独立一个价格序列
        """
        np.random.seed(20240101)
        s_date = pd.to_datetime(start_date)
        e_date = pd.to_datetime(end_date)
        all_dates = pd.bdate_range(start=s_date, end=e_date)
        n = len(all_dates)

        if n < 5:
            return pd.DataFrame(
                columns=[
                    "date",
                    "code",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                    "is_st",
                    "is_limit_up",
                    "is_limit_down",
                    "change_pct",
                ]
            )

        rows = []
        for sym in symbols:
            # 起始价：股票 10~50 元随机，债券 ETF 100~120 元
            is_etf = sym.startswith(("5", "1", "15")) and (sym.endswith(".SH") or sym.endswith(".SZ"))
            start_price = np.random.uniform(95.0, 115.0) if is_etf else np.random.uniform(8.0, 50.0)

            daily_drift = np.random.uniform(-0.0005, 0.0015)
            daily_vol = np.random.uniform(0.008, 0.020)

            returns = np.random.normal(daily_drift, daily_vol, n)
            for i in range(1, n):
                returns[i] += 0.15 * returns[i - 1]

            prices = [start_price]
            for r in returns[1:]:
                prices.append(prices[-1] * (1 + r))
            prices = np.array(prices)

            df_one = pd.DataFrame(
                {
                    "date": all_dates,
                    "code": sym,
                    "close": prices,
                }
            )
            df_one["open"] = df_one["close"].shift(1).fillna(df_one["close"].iloc[0]) * (
                1 + np.random.normal(0, 0.003, len(df_one))
            )
            intraday_range = np.abs(np.random.normal(0, 0.005, len(df_one)))
            df_one["high"] = np.maximum(df_one["open"], df_one["close"]) * (1 + intraday_range)
            df_one["low"] = np.minimum(df_one["open"], df_one["close"]) * (1 - intraday_range)
            df_one["vol"] = np.random.lognormal(10, 0.5, len(df_one)).astype(int)

            df_one["pre_close"] = df_one["close"].shift(1).fillna(df_one["close"].iloc[0])
            df_one["change_pct"] = (df_one["close"] - df_one["pre_close"]) / df_one["pre_close"] * 100
            df_one["is_st"] = False
            df_one["is_limit_up"] = df_one["change_pct"] >= 9.9
            df_one["is_limit_down"] = df_one["change_pct"] <= -9.9

            for c in ["open", "high", "low", "close"]:
                df_one[c] = df_one[c].round(4)

            rows.append(df_one)

        if not rows:
            return pd.DataFrame(
                columns=[
                    "date",
                    "code",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                    "is_st",
                    "is_limit_up",
                    "is_limit_down",
                    "change_pct",
                ]
            )

        df = pd.concat(rows, ignore_index=True)
        return df[
            [
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "vol",
                "pre_close",
                "change_pct",
                "is_st",
                "is_limit_up",
                "is_limit_down",
            ]
        ]

    def try_external_data(self, external_data: Dict[str, Any]) -> pd.DataFrame | None:
        """
        尝试从外部数据源获取数据（系统内置工具提供）

        返回清洗后的 DataFrame 或 None
        """
        daily = external_data.get("daily")
        if daily is None:
            return None

        if not isinstance(daily, pd.DataFrame):
            logger.info("external_data.daily 不是 DataFrame，跳过外部数据")
            return None

        if daily.empty:
            logger.info("external_data.daily 为空，跳过外部数据")
            return None

        source = external_data.get("source", "external")
        logger.info(f"使用系统内置工具提供的数据 (来源: {source})，共 {len(daily)} 行")

        required_cols = {"code", "date", "open", "high", "low", "close", "volume"}
        missing_cols = required_cols - set(daily.columns)
        if missing_cols:
            logger.error(f"外部数据缺少必需列: {missing_cols}")

        df = daily.copy()
        existing_cols = set(df.columns)

        if "code" not in existing_cols and "ts_code" in existing_cols:
            df["code"] = df["ts_code"]
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        if "change_pct" not in existing_cols and "close" in existing_cols and "pre_close" in existing_cols:
            df["change_pct"] = (df["close"] - df["pre_close"]) / df["pre_close"] * 100
        if "is_st" not in existing_cols:
            df["is_st"] = False
        if "is_limit_up" not in existing_cols:
            if "change_pct" in existing_cols:
                df["is_limit_up"] = df["change_pct"] >= 9.9
                df["is_limit_down"] = df["change_pct"] <= -9.9
            else:
                df["is_limit_up"] = False
                df["is_limit_down"] = False

        return df

    # ------------------------------------------------------------------
    # 按数据类型粒度独立降级（v4 增强）
    # ------------------------------------------------------------------

    def _build_kwargs_for_type(
        self,
        data_type: str,
        symbols: List[str],
        start_date: str,
        end_date: str,
        report_date: str | None,
    ) -> Dict[str, Any]:
        """根据数据类型构造对应适配器方法的参数"""
        if data_type == "daily":
            return {
                "symbols": symbols,
                "start_date": start_date,
                "end_date": end_date,
                "adjust": ADJUST_MODE,
            }
        elif data_type == "financial":
            return {
                "symbols": symbols,
                "report_date": report_date or start_date,
                "fields": [],
            }
        elif data_type in ("capital_flow", "dragon_tiger"):
            return {
                "symbols": symbols,
                "start_date": start_date,
                "end_date": end_date,
            }
        elif data_type == "shareholder":
            return {
                "symbols": symbols,
                "report_date": report_date or "",
            }
        return {}

    def fetch_by_type(
        self,
        data_type: str,
        symbols: List[str],
        start_date: str,
        end_date: str,
        report_date: str | None = None,
        override_start: str | None = None,
    ) -> tuple:
        """
        按数据类型独立降级获取数据

        遍历 self.data_sources 优先级链，逐个尝试支持该类型的适配器。
        某个源不支持该方法或返回空数据时，自动切到下一个源。

        参数:
            override_start: 增量更新时覆盖查询起始日（仅取 [override_start, end_date] 区间），
                            用于「只拉缓存截止日之后」的缺失区间；None 表示整段查询。

        返回: (DataFrame, active_backend)
        """
        meta = DATA_TYPES.get(data_type)
        if meta is None:
            raise ValueError(f"未知数据类型: {data_type}")

        # 增量场景：用 override_start 替换查询起始日（仅取缺失区间）
        eff_start = override_start or start_date
        kwargs = self._build_kwargs_for_type(data_type, symbols, eff_start, end_date, report_date)
        tried = []
        errors: Dict[str, str] = {}

        for backend in self.data_sources:
            # 加载适配器
            try:
                adapter = _load_adapter(backend, **self._websearch_extra_kwargs(backend))
            except Exception as e:
                logger.debug(f"[{meta.display_name}] 加载 {backend} 失败: {e}")
                tried.append(backend)
                continue

            # 检查该适配器是否支持此数据类型
            if not adapter.supports(data_type):
                logger.info(f"[{meta.display_name}] {backend} 不支持此数据类型，跳过")
                tried.append(backend)
                continue

            # 调用适配器获取数据
            try:
                df = adapter.fetch(data_type, **kwargs)
                if df is not None and not df.empty:
                    logger.info(f"[{meta.display_name}] 从 {backend} 获取成功 ({len(df)} 行)")
                    return df, backend
                else:
                    logger.info(f"[{meta.display_name}] {backend} 返回空数据，尝试下一个源")
            except Exception as e:
                msg = str(e)
                errors[backend] = msg
                logger.warning(f"[{meta.display_name}] {backend} 获取失败: {msg}")

            tried.append(backend)

        # 全部源都失败
        if meta.allow_synthetic:
            logger.warning(f"[{meta.display_name}] 所有源均失败，使用模拟数据兜底")
            return self._generate_synthetic_data(symbols, start_date, end_date), "synthetic"
        else:
            logger.warning(f"[{meta.display_name}] 所有源均失败或返回空 (已尝试: {' → '.join(tried)})")
            return pd.DataFrame(), ""

    def fetch_all_types(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        report_date: str | None = None,
    ) -> Dict[str, tuple]:
        """
        批量获取所有数据类型，每种类型独立降级

        返回: {data_type: (DataFrame, active_backend)}
        每种类型独立降级，互不影响
        """
        results = {}
        for data_type, meta in DATA_TYPES.items():
            df, backend = self.fetch_by_type(data_type, symbols, start_date, end_date, report_date)
            if df is not None and not df.empty:
                results[data_type] = (df, backend)
            elif meta.required:
                raise RuntimeError(f"必需数据类型 {meta.display_name} 获取失败")
            else:
                logger.info(f"[{meta.display_name}] 数据缺失（非必需，跳过）")
        return results

    def fetch_and_clean(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        adjust: str = ADJUST_MODE,
        exclude_st: bool = True,
        exclude_new: bool = True,
        min_listed_days: int = 60,
        fill_suspend: bool = False,
        external_data: Dict[str, Any] | None = None,
        override_start: str | None = None,
    ) -> pd.DataFrame:
        """
        获取并清洗日线数据

        数据源优先级（默认，见 config.DEFAULT_DATA_SOURCES）:
        1. external_data (系统内置工具提供，若传入则优先使用)
        2. tencent (免费主源，零鉴权直连腾讯公网行情)
        3. local (tencent 取数失败时回退本地缓存)
        4. synthetic (仅在数据源显式 allow_synthetic 时，作为最后兜底生成模拟数据)

        参数:
            symbols: 股票代码列表，为空则获取全部A股
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期
            adjust: 复权方式
            exclude_st: 剔除ST
            exclude_new: 剔除新股
            min_listed_days: 最少上市天数
            fill_suspend: 停牌是否前向填充
            external_data: 外部数据源提供的数据

        返回:
            清洗后的 DataFrame，包含统一列
        """
        if external_data:
            df = self.try_external_data(external_data)
            if df is not None and not df.empty:
                logger.info(f"外部数据清洗后 {len(df)} 行")
                return df.sort_values(["date", "code"]).reset_index(drop=True)
            logger.info("外部数据不可用，回退到内置适配器")

        if not symbols:
            try:
                stock_df = self.provider.get_stock_list()
                if stock_df.empty:
                    logger.error("无法获取股票列表")
                    return pd.DataFrame()
                symbols = stock_df["code"].tolist()
            except Exception as e:
                logger.error(f"获取股票列表失败: {e}，无法获取全市场数据")
                return pd.DataFrame()

        logger.info(f"开始获取 {len(symbols)} 只股票的日线数据，时间 {start_date} 至 {end_date}")
        # 使用降级链拉取数据（override_start 用于增量：仅取缓存截止日之后区间）
        df = self._try_fetch_with_fallback(
            symbols=symbols,
            start_date=override_start or start_date,
            end_date=end_date,
            adjust=adjust,
            exclude_st=exclude_st,
            exclude_new=exclude_new,
            min_listed_days=min_listed_days,
            fill_suspend=fill_suspend,
        )
        if df.empty:
            logger.error("未获取到任何数据")
            return df

        logger.info("开始数据清洗...")
        len(df)

        # 模拟数据时跳过【新股剔除】和【停牌剔除】等需要真实 list_date 的清洗
        # 否则会因为没有 list_date 字段把全部数据剔光
        if not self.is_synthetic and exclude_new:
            try:
                stock_info = self.provider.get_stock_list()
                if not stock_info.empty and "list_date" in stock_info.columns:
                    # 统一 code 格式：不同适配器返回的 code 格式可能不同
                    # (如日线数据为 002594.SZ，股票列表为 SZ.002594)
                    def _normalize_code(c):
                        """将各种 code 格式统一为 6位数字.交易所 后缀格式"""
                        s = str(c).strip().upper()
                        if "." in s:
                            parts = s.split(".")
                            # SZ.002594 → 002594.SZ, 002594.SZ → 002594.SZ
                            if len(parts) == 2 and len(parts[0]) <= 3 and parts[0] in ("SH", "SZ", "BJ"):
                                return f"{parts[1]}.{parts[0]}"
                            return s
                        # sh600000 → 600000.SH
                        if s.startswith(("SH", "SZ", "BJ")) and len(s) > 4:
                            return f"{s[2:]}.{s[:2]}"
                        return s

                    df["_code_norm"] = df["code"].apply(_normalize_code)
                    stock_info["_code_norm"] = stock_info["code"].apply(_normalize_code)
                    # list_date 支持多种格式（%Y%m%d / %Y-%m-%d / ISO）
                    stock_info["list_date"] = pd.to_datetime(stock_info["list_date"], format="mixed", errors="coerce")
                    df = df.merge(
                        stock_info[["_code_norm", "list_date"]], on="_code_norm", how="left", suffixes=("", "_stock")
                    )
                    df["listed_days"] = (df["date"] - df["list_date"]).dt.days
                    before = len(df)
                    df = df[df["listed_days"] >= min_listed_days]
                    logger.info(f"剔除新股后剩余 {len(df)} 行 (剔除 {before - len(df)} 行)")
                    df = df.drop(columns=["_code_norm"], errors="ignore")
            except Exception as e:
                logger.warning(f"获取股票列表失败，跳过新股剔除: {e}")

        if not self.is_synthetic and not fill_suspend:
            df = df[df["volume"] > 0] if "volume" in df.columns else df[df["vol"] > 0]
        else:
            df = df.sort_values(["code", "date"])
            df = df.set_index(["code", "date"])
            df = df.groupby("code").apply(lambda x: x.ffill()).reset_index(level=0, drop=True)
            df = df.reset_index()

        if "is_limit_up" not in df.columns or df["is_limit_up"].isna().all():
            df = self._mark_price_limits(df)
        if "is_st" not in df.columns or df["is_st"].isna().all():
            df = self._mark_st(df)

        if exclude_st and not self.is_synthetic:
            st_mask = df["is_st"].fillna(False).astype(bool)
            df = df[~st_mask]

        df = df.dropna(subset=["close"])

        logger.info(f"清洗完成，最终 {len(df)} 行数据")
        return df.sort_values(["date", "code"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # 财务数据 / 估值数据（v3 扩展：统一标准 schema + 降级链）
    # ------------------------------------------------------------------

    # 标准 schema 字段（与 base_data_provider.BaseDataProvider.get_financial 一致）
    # P0-1 PIT 契约：末尾追加 disclosure_date（披露日）
    _FINANCIAL_STANDARD_COLS = [
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

    def _try_fetch_financial_with_fallback(
        self,
        symbols: List[str],
        report_date: str,
        fields: List[str] | None = None,
    ) -> pd.DataFrame:
        """
        财务数据版本的降级链拉取。复用 _should_fallback 决策逻辑，
        但调用 provider.get_financial 而非 get_daily。

        与 _try_fetch_with_fallback 的区别：
        - 不走模拟数据兜底（财务数据合成无意义）
        - 全部源都失败/为空时返回空 DataFrame
        """
        tried_backends: List[str] = []
        last_errors: Dict[str, str] = {}

        for idx, backend in enumerate(self.data_sources):
            tried_backends.append(backend)
            # 切换 provider
            if idx > 0 or self.backend != backend:
                logger.info(f"切换到数据源: {backend}（财务数据链路: {' → '.join(tried_backends)}）")
                try:
                    self.provider = _load_adapter(backend, **self._websearch_extra_kwargs(backend))
                    self.backend = backend
                except Exception as e:
                    last_errors[backend] = f"初始化失败: {e}"
                    logger.warning(f"切换到 {backend} 失败: {e}，尝试下一个")
                    continue

            try:
                logger.info(f"使用数据源 {backend} 获取 {len(symbols)} 只股票的财务数据 (report_date={report_date})")
                df = self.provider.get_financial(symbols, report_date, fields or [])
                if df is not None and not df.empty:
                    if idx > 0:
                        logger.info(
                            f"✓ 数据源 {backend} 财务数据拉取成功 {len(df)} 行（链路: {' → '.join(tried_backends)}）"
                        )
                    self.is_synthetic = False
                    return df
                else:
                    last_errors[backend] = "返回空数据"
                    logger.warning(f"数据源 {backend} 财务数据返回空，尝试下一个源")
                    continue
            except Exception as e:
                msg = getattr(e, "message", str(e))
                last_errors[backend] = f"{type(e).__name__}: {msg}"

                if self._should_fallback(backend, e):
                    reason = DATA_FALLBACK_RULES.get(backend, {}).get("downgrade_reason", "")
                    logger.warning(f"数据源 {backend} 触发【{reason or type(e).__name__}】（{msg}），自动降级")
                    continue

                if isinstance(e, DataSourceError):
                    logger.error(f"数据源 {backend} 错误（{msg}），不切换")
                else:
                    logger.error(f"数据源 {backend} 未知异常（{msg}），不切换")
                raise

        # 走完整个降级链仍失败：财务数据不走模拟兜底，直接返回空
        logger.error(
            f"财务数据 data_sources {self.data_sources} 中所有数据源都失败或返回空。"
            f"已尝试: {' → '.join(tried_backends)}"
        )
        for k, v in last_errors.items():
            logger.error(f"  • {k}: {v}")
        return pd.DataFrame(columns=self._FINANCIAL_STANDARD_COLS)

    def fetch_financial(
        self,
        symbols: List[str],
        report_date: str,
        fields: List[str] | None = None,
    ) -> pd.DataFrame:
        """
        获取财务数据，返回统一标准 schema 的 DataFrame。

        走与 fetch_and_clean 相同的降级链（tencent → local），
        每个源的 get_financial 实现负责把原始字段映射到标准 schema。

        参数:
            symbols: 股票代码列表，如 ['000001.SZ', '600000.SH']
            report_date: 报告期，如 '20240930' 或 '2024-09-30'
            fields: 可选，需要返回的字段列表（code/report_date 始终保留）；
                    为 None 时返回完整标准 schema。

        返回:
            DataFrame，每行一只股票一个报告期，标准字段:
            code, report_date, pe_ttm, pb, ps_ttm, dv_ratio,
            roe, roa, gross_margin, net_margin,
            revenue_growth, profit_growth,
            debt_ratio, current_ratio, quick_ratio, ocf,
            industry, name
            全部源都失败时返回空 DataFrame（不抛异常）。
        """
        if not symbols:
            logger.warning("fetch_financial: symbols 为空")
            return pd.DataFrame(columns=self._FINANCIAL_STANDARD_COLS)

        try:
            df = self._try_fetch_financial_with_fallback(symbols, report_date, fields)
        except Exception as e:
            logger.error(f"fetch_financial 拉取失败: {e}")
            return pd.DataFrame(columns=self._FINANCIAL_STANDARD_COLS)

        if df.empty:
            return df

        # 保证列顺序与标准 schema 一致（缺失列补 NaN）
        for col in self._FINANCIAL_STANDARD_COLS:
            if col not in df.columns:
                df[col] = None
        df = df[self._FINANCIAL_STANDARD_COLS]

        # 如果调用方指定了 fields，按需过滤列
        # P0-1 PIT 契约：code/report_date/disclosure_date 始终保留
        if fields:
            keep = ["code", "report_date", "disclosure_date"] + [
                f for f in fields if f in self._FINANCIAL_STANDARD_COLS
            ]
            keep = list(dict.fromkeys(keep))
            df = df[keep]

        return df.reset_index(drop=True)

    def fetch_valuation(
        self,
        symbols: List[str],
        trade_date: str,
    ) -> pd.DataFrame:
        """
        获取指定交易日的估值数据（PE/PB/PS/股息率）。

        复用各适配器的 get_financial 实现：
        - Tushare: pro.daily_basic(trade_date=...)
        - AkShare: ak.stock_a_indicator_lg() 取最近一条
        - BaoStock: 不直接提供估值，留空

        参数:
            symbols: 股票代码列表
            trade_date: 交易日期，如 '20240930' 或 '2024-09-30'

        返回:
            DataFrame，列: code, trade_date, pe_ttm, pb, ps_ttm, dv_ratio
            全部源失败时返回空 DataFrame。
        """
        val_cols = ["code", "trade_date", "pe_ttm", "pb", "ps_ttm", "dv_ratio"]

        if not symbols:
            logger.warning("fetch_valuation: symbols 为空")
            return pd.DataFrame(columns=val_cols)

        # 复用财务数据降级链，传入估值相关字段
        try:
            df = self._try_fetch_financial_with_fallback(
                symbols=symbols,
                report_date=trade_date,
                fields=["pe_ttm", "pb", "ps_ttm", "dv_ratio"],
            )
        except Exception as e:
            logger.error(f"fetch_valuation 拉取失败: {e}")
            return pd.DataFrame(columns=val_cols)

        if df.empty:
            return pd.DataFrame(columns=val_cols)

        # 把 report_date 重命名为 trade_date，仅保留估值字段
        out = pd.DataFrame()
        out["code"] = df["code"] if "code" in df.columns else None
        out["trade_date"] = df["report_date"] if "report_date" in df.columns else trade_date.replace("-", "")
        for col in ["pe_ttm", "pb", "ps_ttm", "dv_ratio"]:
            out[col] = df[col] if col in df.columns else None

        return out[val_cols].reset_index(drop=True)

    def _mark_price_limits(self, df: pd.DataFrame) -> pd.DataFrame:
        """根据涨跌幅标记涨跌停"""
        if "change_pct" not in df.columns:
            return df
        limit_up = df["change_pct"] >= 9.9
        limit_down = df["change_pct"] <= -9.9
        df["is_limit_up"] = limit_up
        df["is_limit_down"] = limit_down
        return df

    def _mark_st(self, df: pd.DataFrame) -> pd.DataFrame:
        """标记ST股票"""
        if "is_st" in df.columns and not df["is_st"].isna().all():
            return df
        try:
            stock_list = self.provider.get_stock_list()
            if not stock_list.empty and "is_st" in stock_list.columns:
                st_codes = stock_list[stock_list["is_st"].fillna(False).astype(bool)]["code"].tolist()
                df["is_st"] = df["code"].isin(st_codes)
            else:
                df["is_st"] = False
        except Exception:
            df["is_st"] = False
        return df

    def save_data(self, df: pd.DataFrame, path: str):
        """保存数据到文件"""
        if DATA_FORMAT == "parquet":
            df.to_parquet(path, index=False)
        elif DATA_FORMAT == "csv":
            df.to_csv(path, index=False)
        elif DATA_FORMAT == "sql":
            from sqlalchemy import create_engine

            engine = create_engine(os.environ.get("QUANT_DB_URL", "sqlite:///quant.db"))
            df.to_sql("daily", engine, if_exists="append", index=False)
        else:
            raise ValueError(f"不支持的存储格式: {DATA_FORMAT}")
        logger.info(f"数据已保存至 {path}")

    # ── local 缓存增量更新（REQ-2026-08-16 优化：增量优先 + 修正感知）────
    # 序列类数据（daily/market_kline/financial/financial_report）保留旧缓存，
    # 仅联网拉取缓存截止日之后的缺失区间并合并；快照类维持整取。
    # 修正感知：meta 记录复权口径与数据指纹，重叠窗口数值跳变则回退全量重取。
    # 逻辑集中复用 scripts.cache_incremental 的纯函数；本方法只负责编排与 IO。
    def _read_cached_series(self, data_type: str, symbol: str) -> tuple[pd.DataFrame | None, dict]:
        """读取已落盘的序列类缓存（不触发 TTL 删除，供增量合并用）。"""
        from scripts.config import CACHE_DIR, cache_meta_path

        try:
            import os

            p = os.path.join(CACHE_DIR, data_type, f"{symbol}.parquet")
            if not os.path.exists(p):
                return None, {}
            import json

            meta = {}
            mp = cache_meta_path(p)
            if os.path.exists(mp):
                try:
                    meta = json.loads(open(mp, encoding="utf-8").read())
                except Exception:  # noqa
                    meta = {}
            df = pd.read_parquet(p)
            return df, meta
        except Exception as e:  # noqa
            logger.debug("读取旧缓存失败（忽略，走全量）: %s", e)
            return None, {}

    def _maybe_incremental(
        self,
        data_type: str,
        symbols: List[str],
        start_date: str,
        end_date: str,
        adjust: str = ADJUST_MODE,
        report_date: str | None = None,
    ) -> tuple[pd.DataFrame | None, bool]:
        """尝试增量取数；返回 (合并后完整 DataFrame 或 None, 是否走了增量)。

        返回 None 第一元素表示「不应走增量」（开关关/非序列/无旧缓存/口径变化/已覆盖/
        检测到修正）→ 调用方按原整取路径处理。
        """
        from scripts.config import CACHE_INCREMENTAL, CACHE_DIR
        from scripts.cache_incremental import (
            is_series_type,
            plan_incremental,
            correction_detected,
            merge_incremental,
        )

        if not CACHE_INCREMENTAL or not is_series_type(data_type):
            return None, False

        # 聚合各标的旧缓存（序列类按标的分文件）
        old_frames = []
        for sym in symbols:
            old_df, meta = self._read_cached_series(data_type, sym)
            if old_df is None or old_df.empty:
                # 任一标的有缺口 → 该 data_type 整体走全量（避免跨标的合并歧义）
                return None, False
            old_frames.append(old_df)
        if not old_frames:
            return None, False
        old_df = pd.concat(old_frames, ignore_index=True)

        meta0 = self._read_cached_series(data_type, symbols[0])[1]
        plan = plan_incremental(
            data_type, meta0, start_date, end_date, adjust, incremental_enabled=True
        )
        if plan is None:
            return None, False

        # 仅拉取 [plan, end_date] 缺失区间
        logger.info("[增量更新] %s 仅拉取 %s ~ %s 缺失区间", data_type, plan, end_date)
        if data_type == "daily":
            new_df = self.fetch_and_clean(
                symbols=symbols,
                start_date=plan,
                end_date=end_date,
                adjust=adjust,
                external_data=None,
                override_start=plan,
            )
        else:
            new_df, _ = self.fetch_by_type(
                data_type=data_type,
                symbols=symbols,
                start_date=plan,
                end_date=end_date,
                report_date=report_date,
                override_start=plan,
            )

        # 修正感知：重叠窗口数值跳变 → 回退全量
        if correction_detected(old_df, new_df, data_type):
            logger.warning("[增量更新] %s 检测到上游修正，回退全量重取", data_type)
            return None, False

        merged = merge_incremental(old_df, new_df, data_type)
        logger.info("[增量更新] %s 合并完成：旧 %d + 新 %d → %d 行",
                    data_type, len(old_df), len(new_df) if new_df is not None else 0, len(merged))
        return merged, True

    # ── local 缓存写回（REQ-2026-08-16：「买菜放冰箱」）────────────
    # 联网取数成功后，按数据项粒度同步写一份到 CACHE_DIR/{data_type}/{symbol}.parquet
    # （全局类型用 all.parquet），供后续查询本地命中、不再联网。
    # 受 CACHE_WRITE_BACK 开关控制；仅落数据，绝不写 token/敏感信息。
    def write_cache(self, data_type: str, symbols: List[str], df: pd.DataFrame, adjust: str | None = None) -> None:
        """把取数结果按数据项粒度写回 local 缓存目录。

        adjust: 复权口径（qfq/hfq/''），用于增量更新的修正感知比对。
        """
        from scripts.config import (
            CACHE_WRITE_BACK,
            CACHE_DIR,
            GLOBAL_CACHE_TYPES,
            write_cache_meta,
        )
        from scripts.cache_incremental import compute_data_signature, cached_end_date

        if not CACHE_WRITE_BACK or df is None or df.empty:
            return
        if data_type in GLOBAL_CACHE_TYPES:
            sym_keys = ["all"]
        else:
            # 优先用 DataFrame 内的 code 列（与落盘契约一致），无则回退入参 symbols
            if "code" in getattr(df, "columns", []):
                sym_keys = [str(c) for c in df["code"].dropna().unique().tolist()]
            else:
                sym_keys = [str(s) for s in symbols]
            if not sym_keys:
                sym_keys = ["all"]

        for sym in sym_keys:
            type_dir = os.path.join(CACHE_DIR, data_type)
            try:
                os.makedirs(type_dir, exist_ok=True)
                cpath = os.path.join(type_dir, f"{sym}.parquet")
                self.save_data(df if len(sym_keys) == 1 else df[df["code"].astype(str) == sym] if "code" in df.columns else df, cpath)
                # 增量更新 meta：记录复权口径、缓存截止日、数据指纹（无 token/敏感信息）
                end_ts = cached_end_date(df, data_type)
                write_cache_meta(
                    cpath,
                    adjust=adjust,
                    cached_end=end_ts.strftime("%Y-%m-%d") if end_ts is not None else None,
                    data_signature=compute_data_signature(df, data_type) if data_type in ("daily", "market_kline", "financial", "financial_report") else None,
                )
                logger.info(f"[缓存写回] {data_type}/{sym}.parquet 已写入 ({len(df)} 行)")
            except Exception as e:  # noqa: F841  写回失败不阻断主线（异常对象仅用于日志）
                logger.warning(f"[缓存写回] {data_type}/{sym}.parquet 写入失败（忽略）: {e}")


def run(ctx) -> Dict[str, Any]:
    """
    data-engine 的 run 函数
    由 jingnitrader 调度

    参数:
        ctx: Context 对象，需包含:
            - stock_pool: list
            - start_date: str
            - end_date: str
            - external_data: dict (可选，系统内置工具提供的数据)
            - 可选: artifacts 已有产物路径可跳过
            - 可选: data_sources: List[str] (用户自定义降级链)
            - 可选: web_search_fn: Callable (agent 注入的搜索函数)

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "metadata": dict,
            "error": str
        }
    """
    try:
        # 强制刷新感知（GAP 修复，与 master 引擎第 440 行一致）：
        # 组合优化/绩效归因定时刷新需要重拉最新行情，若这里只看
        # ctx.get_artifact("DATA") 是否存在就短路，会拿到旧数据。
        _force = os.environ.get("QUANT_FORCE_REFRESH", "").lower() in ("1", "true", "yes")
        existing = ctx.get_artifact("DATA")
        if existing and os.path.exists(existing) and not _force:
            return {"success": True, "artifact_path": existing, "metadata": {"source": "cache"}, "error": ""}

        external = ctx.external_data if ctx.external_data else None
        if external and external.get("daily") is not None:
            logger.info(f"检测到外部数据源: {external.get('source', 'unknown')}")

        # 从 context 获取可选参数
        data_sources = getattr(ctx, "data_sources", None)
        web_search_fn = None
        if hasattr(ctx, "external_data") and isinstance(ctx.external_data, dict):
            web_search_fn = ctx.external_data.get("web_search_fn")

        engine = DataEngine(
            data_sources=data_sources,
            web_search_fn=web_search_fn,
        )

        output_dir = os.environ.get("QUANT_DATA_DIR", "./workspace/data")
        os.makedirs(output_dir, exist_ok=True)

        # ============================================================
        # v4: 按数据类型粒度独立降级，批量获取所有数据类型
        # ============================================================
        # 日线数据：优先外部数据，其次降级链
        if external and external.get("daily") is not None:
            df = engine.fetch_and_clean(
                symbols=ctx.stock_pool,
                start_date=ctx.start_date,
                end_date=ctx.end_date,
                adjust=ADJUST_MODE,
                external_data=external,
            )
        else:
            # 增量更新：有旧缓存则只拉缺失区间并合并；否则走原整取路径
            inc_df, used_inc = engine._maybe_incremental(
                data_type="daily",
                symbols=ctx.stock_pool,
                start_date=ctx.start_date,
                end_date=ctx.end_date,
                adjust=ADJUST_MODE,
            )
            if used_inc and inc_df is not None and not inc_df.empty:
                df = inc_df
            else:
                df = engine.fetch_and_clean(
                    symbols=ctx.stock_pool,
                    start_date=ctx.start_date,
                    end_date=ctx.end_date,
                    adjust=ADJUST_MODE,
                    external_data=None,
                )

        if df.empty:
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "未获取到任何有效数据"}

        # 保存日线数据（主产物）
        path = os.path.join(output_dir, "cleaned_data.parquet")
        engine.save_data(df, path)
        # local 缓存写回：日线按标的交由 write_cache（含全局/分标的拆分）
        engine.write_cache("daily", ctx.stock_pool, df)

        data_source = "external" if external and external.get("daily") is not None else "native"
        metadata = {
            "rows": len(df),
            "symbols_count": df["code"].nunique(),
            "date_range": f"{df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')}",
            "data_source": data_source,
            "active_backend": engine.backend,
            "is_synthetic": engine.is_synthetic,
            "fallback_chain": engine.data_sources,
            "data_source_usage": {},
        }

        # 获取补充数据类型（financial/capital_flow/dragon_tiger/shareholder）
        # 每种数据类型独立降级，互不影响
        report_date = getattr(ctx, "metadata", {}).get("report_date", "")
        # P0-1 PIT 契约：asof 取回测结束日期（防止未来披露数据进入下游）
        pit_asof = ctx.end_date.replace("-", "") if ctx.end_date else ""
        pit_warnings: list = []
        # P0-2 三态数据质量门：收集实际拉到的 supplementary DataFrame，供出口 gate.check 使用
        supplementary_dfs: Dict[str, pd.DataFrame] = {}
        supplementary_types = ["financial", "capital_flow", "dragon_tiger", "shareholder"]
        for data_type in supplementary_types:
            meta = DATA_TYPES.get(data_type)
            if meta is None:
                continue
            try:
                # 增量更新：序列类（financial）有旧缓存则只拉缺失区间并合并
                inc_df, used_inc = engine._maybe_incremental(
                    data_type=data_type,
                    symbols=ctx.stock_pool,
                    start_date=ctx.start_date,
                    end_date=ctx.end_date,
                    adjust=ADJUST_MODE,
                    report_date=report_date,
                )
                if used_inc and inc_df is not None and not inc_df.empty:
                    sdf, sbackend = inc_df, engine.backend
                else:
                    sdf, sbackend = engine.fetch_by_type(
                        data_type=data_type,
                        symbols=ctx.stock_pool,
                        start_date=ctx.start_date,
                        end_date=ctx.end_date,
                        report_date=report_date,
                    )
                if sdf is not None and not sdf.empty:
                    # P0-1.3 PIT 出口扫描：财务数据保存前做 PIT 校验
                    if data_type == "financial" and pit_asof:
                        try:
                            from scripts.pit import scan_pit_warnings, pit_filter

                            warnings = scan_pit_warnings(sdf, pit_asof, table_name="financial")
                            if warnings:
                                pit_warnings.extend(warnings)
                                sdf = pit_filter(sdf, pit_asof)
                                logger.info(
                                    f"PIT 出口扫描：financial 表过滤后剩余 {len(sdf)} 行"
                                    f"（asof={pit_asof}，剔除 {len(warnings)} 行未来披露数据）"
                                )
                        except ValueError as ve:
                            logger.warning(f"PIT 出口扫描跳过（缺 disclosure_date 列）: {ve}")
                        except Exception as pit_e:
                            logger.warning(f"PIT 出口扫描异常（不阻断流程）: {pit_e}")
                    spath = os.path.join(output_dir, meta.artifact_filename)
                    engine.save_data(sdf, spath)
                    # local 缓存写回：按数据项粒度同步到 CACHE_DIR
                    engine.write_cache(data_type, ctx.stock_pool, sdf)
                    ctx.update_artifact(meta.artifact_key, spath)
                    metadata["data_source_usage"][data_type] = sbackend
                    # P0-2 收集实际拉到的 supplementary DataFrame，供出口 gate.check 使用
                    supplementary_dfs[data_type] = sdf
                    logger.info(f"{meta.display_name} 已落盘: {spath} (源: {sbackend})")
                else:
                    logger.info(f"{meta.display_name} 数据缺失（非必需，跳过）")
            except Exception as e:
                logger.warning(f"获取 {meta.display_name} 失败（非必需，跳过）: {e}")

        # P0-1.3 PIT 扫描结果写入 ctx.metadata 供下游参考
        if pit_warnings:
            if hasattr(ctx, "metadata") and isinstance(ctx.metadata, dict):
                ctx.metadata["pit_warnings"] = pit_warnings
            metadata["pit_warnings"] = pit_warnings
            logger.warning(f"PIT 扫描汇总：共 {len(pit_warnings)} 条 PIT 违规记录，已写入 ctx.metadata['pit_warnings']")

        metadata["data_source_usage"]["daily"] = engine.backend

        # ============================================================
        # P0-2 三态数据质量门：出口校验（PRD P0-2.5）
        # ============================================================
        # 构造 tables 字典：daily（主产物）+ 实际拉到的 supplementary
        # 别名（cleaned_data/financial/capital_flow/...）会在 gate 内部归一为 PRD 标准名
        tables_for_gate = {"daily": df}
        tables_for_gate.update(supplementary_dfs)
        try:
            from scripts.quality_gate import DataQualityGate

            gate = DataQualityGate(core_required=["daily"])
            verdict = gate.check(
                tables=tables_for_gate,
                asof=pit_asof or "20240101",
                pit_warnings=pit_warnings,
            )
            verdict_dict = verdict.to_dict()
            if hasattr(ctx, "metadata") and isinstance(ctx.metadata, dict):
                ctx.metadata["data_quality"] = verdict_dict
            metadata["data_quality"] = verdict_dict

            if verdict.mode == "abort":
                logger.error(f"数据质量 abort: {verdict.reason}")
                return {
                    "success": False,
                    "artifact_path": "",
                    "metadata": metadata,
                    "error": f"data_quality_abort: {verdict.reason}",
                }
            if verdict.mode == "degraded":
                logger.warning(f"数据质量降级: {verdict.reason}")
            else:
                logger.info(f"数据质量 normal: {verdict.reason}")
        except Exception as qe:
            logger.warning(f"P0-2 质量门校验异常（不阻断流程）: {qe}")

        return {"success": True, "artifact_path": path, "metadata": metadata, "error": ""}
    except Exception as e:
        logger.exception("数据引擎执行失败")
        return {"success": False, "artifact_path": "", "metadata": {}, "error": str(e)}


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            ctx_dict = json.load(f)
        from scripts.context import Context

        ctx = Context.from_dict(ctx_dict)
    else:
        from scripts.context import Context

        ctx = Context(
            task_id="test", stock_pool=["000001.SZ", "600000.SH"], start_date="2024-01-01", end_date="2024-12-31"
        )
    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 优化模块入口（已整合到 scripts/optimizations/）
# 使用方式: from engine import optimizations
# ---------------------------------------------------------------------------
from scripts.optimizations.pit_adapter import (
    PITDataAdapter as _PITDataAdapter,
    PITField as _PITField,
)


class optimizations:
    """数据引擎优化模块集合"""

    PITDataAdapter = _PITDataAdapter
    PITField = _PITField

    @staticmethod
    def get_pit_modules():
        """返回所有可用的 PIT 模块"""
        return {
            "pit_data_adapter": _PITDataAdapter,
            "pit_field": _PITField,
        }
