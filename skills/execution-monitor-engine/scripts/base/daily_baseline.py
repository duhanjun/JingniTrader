"""live 单日亏损检查的「日初净值基线」本地持久化（二期，2026-08-28）。

为什么需要本模块
----------------
``CircuitBreaker._check`` 的单日亏损分支早已实现，live 路径不走它的唯一原因是
**拿不到 start_of_day_nav**：broker 账户接口不提供该字段——

- xtquant ``XtAsset`` 仅 6 个字段（account_id/cash/frozen_cash/market_value/
  total_asset/fetch_balance），无日初净值与当日盈亏；
- gm ``Cashes.data`` 29 字段有 ``fpnl``，但 protobuf 无字段注释、当前环境无实盘
  会话可验证其是否为「当日」口径，按数据纪律标 ``[UNSOURCED]``，**不作实现依赖**。

故改由本地文件按交易日持久化基线，两个/三个 broker 行为完全一致。

文件格式（按 account_id 分键，避免多账户互相串扰）
------------------------------------------------
::

    {
      "accounts": {
        "<account_id>": {
          "asof": "2026-08-28",
          "start_of_day_nav": 1000000.0,
          "source": "session_first_snapshot",
          "updated_at": "2026-08-28T09:31:00+08:00"
        }
      }
    }

日切规则
--------
``asof != 今天`` 或基线缺失/非正 → 取当前净值快照并落盘；同日则复用既有基线、
**不覆盖**（否则每笔下单都会把基线"追平"到当前净值，检查永久失效）。

⚠️ **已知残余缺陷（二期不消除，仅在此显式声明）**：进程于交易时段内冷启动时，
基线会取启动时刻净值，开盘至启动之间已实现的亏损被"洗白"，剩余阈值从启动净值
起算。缓解手段首选「引擎日内常驻、冷启动只在开盘前完成」。

零新依赖：仅 Python 标准库。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import date, datetime
from typing import Any, Dict, Tuple

logger = logging.getLogger("daily-baseline")

# 读改写期间的进程内互斥：live 下单可能并发，避免两笔同时判定"无基线"后互相覆盖
_LOCK = threading.Lock()

# 基线来源标记
SOURCE_SNAPSHOT = "session_first_snapshot"  # 进程首次快照（含日切重置与冷启动）
SOURCE_PERSISTED = "persisted"  # 同日复用落盘基线
UNAVAILABLE = "unavailable"  # 取不到基线（调用方须 fail-closed）


def today_stamp() -> str:
    """返回本地日期戳（``YYYY-MM-DD``）。

    局限（已知，不消除）：用**本地机器日期**而非交易所交易日历，跨零点夜盘等
    场景需引入交易日历才能精确判定日切。
    """
    return date.today().isoformat()


def _empty_store() -> Dict[str, Any]:
    return {"accounts": {}}


def _load_store(path: str) -> Tuple[Dict[str, Any], str | None]:
    """读取基线文件，返回 (store, error)。

    文件缺失 / 损坏 / 结构非法 / 不可解析 → 返回空 store + 错误原因，
    **绝不抛异常**（调用方据此降级为"取快照"，不得因一个状态文件拖垮下单链路）。
    """
    if not path or not os.path.exists(path):
        return _empty_store(), "baseline_missing"
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning(f"live 日初净值基线文件不可读（降级为取快照）: {path}: {exc}")
        return _empty_store(), f"unreadable:{type(exc).__name__}"
    if not isinstance(raw, dict):
        logger.warning(f"live 日初净值基线结构非法（降级为取快照）: {path}")
        return _empty_store(), "malformed:not_a_dict"
    accounts = raw.get("accounts")
    if not isinstance(accounts, dict):
        logger.warning(f"live 日初净值基线 accounts 结构非法（降级为取快照）: {path}")
        return _empty_store(), "malformed:accounts"
    return {"accounts": accounts}, None


def _atomic_write(path: str, store: Dict[str, Any]) -> bool:
    """原子写入基线文件（同目录临时文件 + os.replace）。

    避免写一半崩溃留下截断的 JSON——虽然读侧已能容错，但"读侧容错"不等于
    "可以随便写坏"：实盘状态文件必须保证自身可解析。写入失败返回 False 由
    调用方 fail-closed，不静默吞掉。
    """
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".live_baseline_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            # 临时文件残留不影响正确性（读侧只认正式文件名），但尽量清理
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.error(f"live 日初净值基线写入失败（调用方须按拒单处理）: {path}: {exc}")
        return False


def resolve_start_of_day_nav(
    path: str,
    account_id: str,
    current_nav: float,
) -> Tuple[float | None, str]:
    """解析本次下单应使用的日初净值，必要时落盘新基线。

    参数:
        path: 基线文件路径（``config.LIVE_DAILY_BASELINE_PATH``）
        account_id: 资金账号；空串按 ``"default"`` 归档（单账户场景）
        current_nav: 本次查到的账户总资产

    返回:
        ``(start_of_day_nav, source)``。``start_of_day_nav`` 为 ``None`` 表示
        **取不到基线**（净值非正或写入失败），调用方必须 fail-closed 拒单，
        不得退化为"跳过单日亏损检查继续下单"。
    """
    key = str(account_id or "default")
    today = today_stamp()

    with _LOCK:
        store, _err = _load_store(path)
        entry = store["accounts"].get(key)
        if isinstance(entry, dict):
            try:
                cached = float(entry.get("start_of_day_nav") or 0.0)
            except (TypeError, ValueError):
                cached = 0.0
            if cached > 0 and entry.get("asof") == today:
                return cached, SOURCE_PERSISTED

        # 需要新基线：净值看不清则无法取快照 → 调用方拒单
        try:
            nav = float(current_nav)
        except (TypeError, ValueError):
            return None, UNAVAILABLE
        if not nav > 0:
            return None, UNAVAILABLE

        store["accounts"][key] = {
            "asof": today,
            "start_of_day_nav": nav,
            "source": SOURCE_SNAPSHOT,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if not _atomic_write(path, store):
            return None, UNAVAILABLE
        return nav, SOURCE_SNAPSHOT


def peek_start_of_day_nav(path: str, account_id: str) -> Tuple[float | None, str]:
    """只读当前基线（供运维/测试核对落盘结果，不写盘、不改变状态）。"""
    key = str(account_id or "default")
    store, err = _load_store(path)
    entry = store["accounts"].get(key)
    if not isinstance(entry, dict):
        return None, err or "baseline_missing"
    try:
        nav = float(entry.get("start_of_day_nav") or 0.0)
    except (TypeError, ValueError):
        return None, "malformed:nav"
    if nav <= 0:
        return None, "malformed:nav"
    return nav, str(entry.get("source") or SOURCE_PERSISTED)
