"""
LIVE 模式实时监控器

统一 PaperExecutor / XtQuantExecutor / GMExecutor 的数据格式，
为 StatusServer 提供标准化的实时快照（账户/持仓/委托/成交）。

设计要点：
- 纯数据层：不依赖 reports-engine，不启动 HTTP 服务，仅提供 build_snapshot() 供调用方使用
- 解耦：通过 BaseExecutor 抽象接口访问执行器，对实盘执行器内部 SDK 对象做鸭子类型适配
- 统一快照：build_snapshot() 返回 StatusServer 所需格式
- 起始净值：首次调用 query_account 时记录 start_of_day_nav（用于日亏损率计算）

字段差异处理：
- PaperExecutor.query_account()  → {nav, available_cash, ...}
- XtQuantExecutor.query_account() → {total_assets, available_cash, market_value, frozen_cash}
- GMExecutor.query_account()       → {total_assets(=nav), available_cash, market_value, frozen_cash}
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from .base.base_executor import BaseExecutor
from .config import (
    MAX_DAILY_LOSS_RATIO, MAX_SINGLE_ORDER_RATIO, MAX_ORDER_FREQUENCY,
)

logger = logging.getLogger("live-monitor")


class LiveMonitor:
    """LIVE 模式实时监控器

    统一不同执行器的数据格式，提供标准化快照。

    参数:
        executor: 已连接（available=True）的 BaseExecutor 实例
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor
        self._start_nav: float = 0.0
        self._start_nav_initialized = False

    # ------------------------------------------------------------------
    # 账户快照
    # ------------------------------------------------------------------

    def unified_account_snapshot(self) -> Dict[str, Any]:
        """统一账户快照格式: nav/available_cash/market_value/frozen_cash/positions

        处理不同执行器的字段名差异:
        - PaperExecutor: nav / available_cash
        - XtQuantExecutor: total_assets / cash → 映射为 nav / available_cash
        - GMExecutor: total_assets(=nav) / available → 映射为 nav / available_cash
        """
        raw = self.executor.query_account()
        if not raw:
            return {
                "nav": 0.0,
                "available_cash": 0.0,
                "market_value": 0.0,
                "frozen_cash": 0.0,
                "start_of_day_nav": self._start_nav,
                "positions": self._normalize_positions(),
            }

        # 兼容字段名差异: total_assets → nav, available → available_cash, frozen → frozen_cash
        nav = float(raw.get("nav", raw.get("total_assets", 0)))
        available_cash = float(raw.get("available_cash", raw.get("available", 0)))
        market_value = float(raw.get("market_value", 0))
        frozen_cash = float(raw.get("frozen_cash", raw.get("frozen", 0)))

        # 首次调用记录 start_of_day_nav（用于日亏损率）
        if not self._start_nav_initialized and nav > 0:
            self._start_nav = nav
            self._start_nav_initialized = True
            logger.info(f"LIVE 起始净值记录: {self._start_nav:.2f}")

        return {
            "nav": nav,
            "available_cash": available_cash,
            "market_value": market_value,
            "frozen_cash": frozen_cash,
            "start_of_day_nav": self._start_nav,
            "positions": self._normalize_positions(),
        }

    def _normalize_positions(self) -> Dict[str, Any]:
        """统一持仓格式为 {code: {volume, available_volume, avg_cost, market_value}}"""
        try:
            df = self.executor.query_positions()
        except Exception as e:
            logger.warning(f"查询持仓失败: {e}")
            return {}

        if df is None or (hasattr(df, "empty") and df.empty):
            return {}

        result: Dict[str, Any] = {}
        if isinstance(df, pd.DataFrame):
            for _, row in df.iterrows():
                code = str(row.get("code", ""))
                volume = int(row.get("volume", 0))
                if volume <= 0 or not code:
                    continue
                result[code] = {
                    "volume": volume,
                    "available_volume": int(row.get("available_volume", 0)),
                    "avg_cost": float(row.get("avg_cost", 0)),
                    "market_value": float(row.get("market_value", 0)),
                }
        return result

    # ------------------------------------------------------------------
    # 委托查询
    # ------------------------------------------------------------------

    def query_orders(self) -> List[Dict[str, Any]]:
        """查询今日委托，统一格式（按执行器类型分发）"""
        # PaperExecutor: 从 self.orders 字典读取
        if hasattr(self.executor, "orders") and isinstance(self.executor.orders, dict):
            return self._query_paper_orders()
        # XtQuantExecutor: 通过 _xt_trader 查询
        if hasattr(self.executor, "_xt_trader") and self.executor._xt_trader is not None:
            return self._query_xtquant_orders()
        # GMExecutor: 通过 _gm 查询
        if hasattr(self.executor, "_gm") and self.executor._gm is not None:
            return self._query_gm_orders()
        return []

    def _query_paper_orders(self) -> List[Dict[str, Any]]:
        result = []
        for order_id, o in self.executor.orders.items():
            result.append({
                "timestamp": o.get("timestamp", datetime.now().isoformat()),
                "order_id": order_id,
                "code": o.get("code", ""),
                "side": o.get("side", ""),
                "volume": int(o.get("volume", 0)),
                "price": float(o.get("price", 0)),
                "status": o.get("status", "unknown"),
                "metadata": {"order_price": float(o.get("order_price", 0))},
            })
        return result

    def _query_xtquant_orders(self) -> List[Dict[str, Any]]:
        try:
            orders = self.executor._xt_trader.query_stock_orders(
                self.executor._acc, cancelable_only=False
            )
            if not orders:
                return []
            result = []
            for o in orders:
                status_str = _map_xtquant_status(getattr(o, "order_status", 0))
                result.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "order_id": str(getattr(o, "order_id", "")),
                    "code": getattr(o, "stock_code", ""),
                    "side": "buy" if getattr(o, "order_type", 0) == 23 else "sell",
                    "volume": int(getattr(o, "order_volume", 0)),
                    "price": float(getattr(o, "price", 0)),
                    "status": status_str,
                    "metadata": {
                        "order_price": float(getattr(o, "price", 0)),
                        "traded_volume": int(getattr(o, "traded_volume", 0)),
                    },
                })
            return result
        except Exception as e:
            logger.warning(f"xtquant 查询委托失败: {e}")
            return []

    def _query_gm_orders(self) -> List[Dict[str, Any]]:
        try:
            orders = self._safe_gm_call(self.executor._gm.get_orders)
            if not orders:
                return []
            result = []
            for o in orders:
                code = _get_attr(o, "symbol", "")
                display_code = _gm_to_display(code)
                side_num = _get_attr(o, "side", 0)
                side = "buy" if side_num == 1 else "sell"
                status_str = _map_gm_status(_get_attr(o, "status", 0))
                result.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "order_id": str(_get_attr(o, "cl_ord_id", "")),
                    "code": display_code,
                    "side": side,
                    "volume": int(_get_attr(o, "volume", 0)),
                    "price": float(_get_attr(o, "price", 0)),
                    "status": status_str,
                    "metadata": {
                        "order_price": float(_get_attr(o, "price", 0)),
                        "traded_volume": int(_get_attr(o, "filled_volume", 0)),
                    },
                })
            return result
        except Exception as e:
            logger.warning(f"gm 查询委托失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 成交查询
    # ------------------------------------------------------------------

    def query_trades(self) -> List[Dict[str, Any]]:
        """查询今日成交，统一格式（按执行器类型分发）"""
        if hasattr(self.executor, "orders") and isinstance(self.executor.orders, dict):
            return self._query_paper_trades()
        if hasattr(self.executor, "_xt_trader") and self.executor._xt_trader is not None:
            return self._query_xtquant_trades()
        if hasattr(self.executor, "_gm") and self.executor._gm is not None:
            return self._query_gm_trades()
        return []

    def _query_paper_trades(self) -> List[Dict[str, Any]]:
        # PaperExecutor 的 orders 中 status=filled 即为成交
        result = []
        for order_id, o in self.executor.orders.items():
            if o.get("status") != "filled":
                continue
            price = float(o.get("price", 0))
            volume = int(o.get("volume", 0))
            side = o.get("side", "")
            result.append({
                "execution_id": order_id,
                "trade_date": datetime.now().strftime("%Y-%m-%d"),
                "code": o.get("code", ""),
                "side": side,
                "shares": volume,
                "price": price,
                "commission": round(price * volume * 0.0003, 2),
                "stamp_tax": round(price * volume * 0.001, 2) if side == "sell" else 0.0,
                "slippage_cost": 0.0,
                "confirmed": True,
                "created_at": o.get("timestamp", datetime.now().isoformat()),
            })
        return result

    def _query_xtquant_trades(self) -> List[Dict[str, Any]]:
        try:
            trades = self.executor._xt_trader.query_stock_trades(self.executor._acc)
            if not trades:
                return []
            result = []
            for t in trades:
                price = float(getattr(t, "traded_price", 0))
                volume = int(getattr(t, "traded_volume", 0))
                side = "buy" if getattr(t, "order_type", 0) == 23 else "sell"
                result.append({
                    "execution_id": str(getattr(t, "order_id", "")),
                    "trade_date": datetime.now().strftime("%Y-%m-%d"),
                    "code": getattr(t, "stock_code", ""),
                    "side": side,
                    "shares": volume,
                    "price": price,
                    "commission": round(price * volume * 0.0003, 2),
                    "stamp_tax": round(price * volume * 0.001, 2) if side == "sell" else 0.0,
                    "slippage_cost": 0.0,
                    "confirmed": True,
                    "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                })
            return result
        except Exception as e:
            logger.warning(f"xtquant 查询成交失败: {e}")
            return []

    def _query_gm_trades(self) -> List[Dict[str, Any]]:
        try:
            trades = self._safe_gm_call(self.executor._gm.get_execution_reports)
            if not trades:
                return []
            result = []
            for t in trades:
                code = _get_attr(t, "symbol", "")
                display_code = _gm_to_display(code)
                price = float(_get_attr(t, "price", 0))
                volume = int(_get_attr(t, "volume", 0))
                side_num = _get_attr(t, "side", 0)
                side = "buy" if side_num == 1 else "sell"
                result.append({
                    "execution_id": str(_get_attr(t, "exec_id", "")),
                    "trade_date": datetime.now().strftime("%Y-%m-%d"),
                    "code": display_code,
                    "side": side,
                    "shares": volume,
                    "price": price,
                    "commission": round(price * volume * 0.0003, 2),
                    "stamp_tax": round(price * volume * 0.001, 2) if side == "sell" else 0.0,
                    "slippage_cost": 0.0,
                    "confirmed": True,
                    "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                })
            return result
        except Exception as e:
            logger.warning(f"gm 查询成交失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 快照构建（供 StatusServer 轮询调用）
    # ------------------------------------------------------------------

    def build_snapshot(self) -> Dict[str, Any]:
        """构建一次状态快照（供 StatusServer 的 /api/snapshot 返回）

        返回格式与 reports-engine.status_server.build_snapshot() 一致，
        但数据来自执行器实时接口，而非 account_state.json 文件。
        """
        account = self.unified_account_snapshot()
        orders = self.query_orders()
        trades = self.query_trades()

        orders_executed = sum(1 for o in orders if o.get("status") == "filled")
        orders_failed = sum(1 for o in orders if o.get("status") == "rejected")
        orders_pending = sum(1 for o in orders if o.get("status") == "pending")

        nav = account.get("nav", 0)
        start_nav = account.get("start_of_day_nav", nav)
        daily_loss_ratio = (nav - start_nav) / start_nav if start_nav > 0 else 0.0

        risk_metrics = {
            "daily_loss_ratio": daily_loss_ratio,
            "daily_loss_triggered": daily_loss_ratio <= -MAX_DAILY_LOSS_RATIO,
            "daily_loss_threshold": MAX_DAILY_LOSS_RATIO,
            "daily_loss_usage": (
                abs(daily_loss_ratio) / MAX_DAILY_LOSS_RATIO
                if MAX_DAILY_LOSS_RATIO > 0 else 0
            ),
            "order_count_today": len(orders),
            "order_frequency_exceeded": len(orders) > MAX_ORDER_FREQUENCY * 50,
            "order_frequency_threshold": MAX_ORDER_FREQUENCY,
            "max_single_order_ratio": MAX_SINGLE_ORDER_RATIO,
        }

        return {
            "account_snapshot": account,
            "orders_executed": orders_executed,
            "orders_failed": orders_failed,
            "orders_pending": orders_pending,
            "risk_metrics": risk_metrics,
            "stop_signals": {"any_triggered": False},
            "ts": time.time(),
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _safe_gm_call(self, func, *args, **kwargs):
        """安全调用 gm 接口，失败返回 None"""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"gm 接口调用失败 {getattr(func, '__name__', '?')}: {e}")
            return None

    def write_artifacts(self, execution_dir: str) -> Dict[str, str]:
        """将当前快照数据写入产物文件，供 reports-engine 生成报告使用

        参数:
            execution_dir: 执行目录路径

        返回:
            {state_path, audit_path, ledger_path}
        """
        import json
        import os

        os.makedirs(execution_dir, exist_ok=True)

        account = self.unified_account_snapshot()
        orders = self.query_orders()
        trades = self.query_trades()

        state_path = os.path.join(execution_dir, "account_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(account, f, ensure_ascii=False, indent=2)

        audit_path = os.path.join(execution_dir, "trade_log.jsonl")
        with open(audit_path, "w", encoding="utf-8") as f:
            for r in orders:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        # ledger 追加式写入（GAP 修复）：以 execution_id/order_id 去重，只追加新成交，
        # 避免定时刷新时覆盖历史成交导致绩效归因丢失历史数据。
        ledger_path = os.path.join(execution_dir, "ledger.jsonl")
        seen = set()
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            _id = rec.get("execution_id") or rec.get("order_id") or rec.get("trade_id")
                            if _id:
                                seen.add(str(_id))
                        except Exception:
                            continue
            except Exception:
                pass
        _mode = "a" if os.path.exists(ledger_path) else "w"
        with open(ledger_path, _mode, encoding="utf-8") as f:
            for r in trades:
                _id = r.get("execution_id") or r.get("order_id") or r.get("trade_id")
                if _id and str(_id) in seen:
                    continue  # 已存在，跳过避免重复
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        logger.info(
            f"产物已写入: account_state.json / trade_log.jsonl / ledger.jsonl "
            f"(委托 {len(orders)} 条, 成交 {len(trades)} 条)"
        )
        return {
            "state_path": state_path,
            "audit_path": audit_path,
            "ledger_path": ledger_path,
        }

    # ------------------------------------------------------------------
    # JSONP 快照写入（混合架构：file:// 协议双击打开 HTML 实时刷新）
    # ------------------------------------------------------------------

    def write_jsonp_snapshot(self, snapshot_path: str) -> str:
        """将当前快照以 JSONP 格式写入文件（原子写入）。

        生成的文件内容形如：
            window.__LIVE_SNAPSHOT = {...};

        供 HTML 文件通过 <script src="snapshot.js"> 标签加载，
        绕过 file:// 协议的 CORS 限制（<script> 标签不受同源策略约束）。

        原子写入流程：
        1. 写入 snapshot.tmp.js
        2. os.replace 原子替换 snapshot.js（避免浏览器读到半截文件）

        参数:
            snapshot_path: snapshot.js 的目标路径

        返回:
            snapshot.js 的绝对路径
        """
        import json
        import os

        snapshot = self.build_snapshot()
        # 额外携带委托/成交明细（build_snapshot 仅含统计数）
        snapshot["orders"] = self.query_orders()
        snapshot["trades"] = self.query_trades()

        content = "window.__LIVE_SNAPSHOT = " + json.dumps(
            snapshot, ensure_ascii=False, default=str
        ) + ";\n"

        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        tmp_path = snapshot_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, snapshot_path)

        return snapshot_path


# ============================================================================
# 模块级工具函数
# ============================================================================

def _get_attr(obj, key, default=0):
    """兼容对象属性与字典访问"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _gm_to_display(code: str) -> str:
    """gm 代码 → 显示代码。SZSE.000001 → 000001.SZ"""
    if "." in code:
        ticker, exchange = code.split(".", 1)
        if exchange.upper() == "SHSE":
            return f"{ticker}.SH"
        elif exchange.upper() == "SZSE":
            return f"{ticker}.SZ"
    return code


def _map_xtquant_status(status_code: int) -> str:
    """xtquant 委托状态码映射"""
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        return "unknown"
    mapping = {
        48: "pending", 49: "pending", 50: "filled",
        51: "rejected", 52: "rejected", 53: "rejected", 55: "pending",
    }
    return mapping.get(status_code, "unknown")


def _map_gm_status(status_code) -> str:
    """gm 委托状态码映射"""
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        return "unknown"
    # gm 状态码：0=无效,1=待报,2=已报,3=部成,4=已成,5=部撤,6=已撤,7=拒单
    mapping = {
        0: "pending", 1: "pending", 2: "pending",
        3: "pending", 4: "filled",
        5: "rejected", 6: "rejected", 7: "rejected",
    }
    return mapping.get(status_code, "unknown")
