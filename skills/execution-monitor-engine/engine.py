"""
实盘执行与监控引擎主逻辑
支持 Paper 模拟 / Live 实盘双模式，含硬风控断路器和审计日志
"""
import os
import sys
import json
import logging
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from scripts.config import (
    EXECUTION_DIR, TRADE_MODE, TRADE_BACKEND, INIT_CAPITAL,
    MAX_DAILY_LOSS_RATIO, MAX_SINGLE_ORDER_RATIO,
    MAX_ORDER_FREQUENCY, MIN_COMMISSION, COMMISSION_RATE, STAMP_TAX_RATE,
    SLIPPAGE, AUDIT_LOG_PATH, ACCOUNT_STATE_PATH
)
from scripts.base.base_executor import BaseExecutor
from scripts.base.circuit_breaker import CircuitBreaker
from scripts.paper_ledger import (
    PaperTradeRecordV1, AccountSnapshot, PositionState,
    append_paper_trade, replay_ledger, migrate_legacy_state,
    get_default_ledger_path,
)

logger = logging.getLogger("execution-monitor-engine")


@dataclass
class Account:
    """虚拟账户（模拟模式）"""
    nav: float = INIT_CAPITAL
    available_cash: float = INIT_CAPITAL
    start_of_day_nav: float = INIT_CAPITAL
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def reset_daily(self):
        self.start_of_day_nav = self.nav
        # T+1: 次日所有持仓可卖
        for pos in self.positions.values():
            pos["available_volume"] = pos["volume"]

    def get_current_nav(self, prices: Optional[Dict[str, float]] = None) -> float:
        total = self.available_cash
        if prices:
            for code, pos in self.positions.items():
                price = prices.get(code, pos.get("avg_cost", 0))
                total += pos["volume"] * price
        return total

    def apply_buy(self, code: str, price: float, volume: int, commission: float):
        cost = price * volume + commission
        if cost > self.available_cash:
            raise ValueError(f"资金不足: 需要 {cost}, 可用 {self.available_cash}")
        self.available_cash -= cost
        if code not in self.positions:
            self.positions[code] = {"volume": 0, "avg_cost": 0.0, "available_volume": 0}
        old_cost = self.positions[code]["avg_cost"] * self.positions[code]["volume"]
        self.positions[code]["volume"] += volume
        self.positions[code]["avg_cost"] = (old_cost + price * volume) / self.positions[code]["volume"] if self.positions[code]["volume"] > 0 else 0.0
        # T+1: 买入当日 available_volume 不变,次日 reset_daily 后才可卖

    def apply_sell(self, code: str, price: float, volume: int, commission: float, stamp_tax: float):
        if code not in self.positions or self.positions[code].get("available_volume", 0) < volume:
            raise ValueError(f"可用持仓不足: 需要 {volume}, 可用 {self.positions[code].get('available_volume', 0)}")
        total_fee = commission + stamp_tax
        revenue = price * volume - total_fee
        self.available_cash += revenue
        self.positions[code]["volume"] -= volume
        self.positions[code]["available_volume"] -= volume
        if self.positions[code]["volume"] <= 0:
            del self.positions[code]

    def calc_commission(self, amount: float, is_sell: bool = False) -> float:
        commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
        stamp_tax = amount * STAMP_TAX_RATE if is_sell else 0
        return commission + stamp_tax

    def to_dict(self) -> Dict:
        return {
            "nav": self.nav,
            "available_cash": self.available_cash,
            "start_of_day_nav": self.start_of_day_nav,
            "positions": self.positions,
        }


class PaperCircuitBreaker(CircuitBreaker):
    """paper 路径断路器：阈值动态取自 engine 模块全局变量。

    ``CircuitBreaker`` 下沉到 ``scripts/base/`` 后，其默认实现读的是
    ``scripts.config`` 的构造期快照。但 engine 模块在导入时把 config 的值
    **复制**成了自己的模块级全局（MAX_SINGLE_ORDER_RATIO 等），既有测试与
    运行期调参都是通过改这些全局完成的。故此处覆写 ``_thresholds``，
    每次检查实时回读 engine 模块的当前全局值。
    """

    def _thresholds(self):
        return MAX_DAILY_LOSS_RATIO, MAX_SINGLE_ORDER_RATIO, MAX_ORDER_FREQUENCY


class AuditLogger:
    """审计日志记录器"""

    def __init__(self, log_path: str = AUDIT_LOG_PATH):
        self.log_path = log_path

    def log_order(
        self,
        order_id: str,
        code: str,
        side: str,
        volume: int,
        price: float,
        status: str,
        metadata: Optional[Dict] = None,
    ):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "order_id": order_id,
            "code": code,
            "side": side,
            "volume": volume,
            "price": price,
            "status": status,
            "metadata": metadata or {},
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_logs(self, n: int = 100) -> List[Dict]:
        """读取最近 n 条日志"""
        if not os.path.exists(self.log_path):
            return []
        logs = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                logs.append(json.loads(line.strip()))
        return logs[-n:]


class PaperExecutor(BaseExecutor):
    """模拟交易执行器（P1-1: 集成追加式 JSONL 账本）"""

    def __init__(self, init_capital: float = INIT_CAPITAL):
        super().__init__()
        self.account = Account(nav=init_capital, available_cash=init_capital)
        # 用 paper 专用子类替换基类实例：阈值实时回读 engine 全局，
        # 保证 monkeypatch.setattr(engine, "MAX_*") 仍然生效（见 PaperCircuitBreaker 说明）
        self.circuit_breaker = PaperCircuitBreaker()
        self.audit = AuditLogger()
        self.orders: Dict[str, Dict] = {}

        # P1-1: 追加式 JSONL 账本配置
        self.ledger_path = get_default_ledger_path()
        self.init_capital = init_capital
        self._trade_seq = 0  # 用于生成 execution_id

        # P1-1.6a: 旧状态自动迁移（account_state.json 存在但 ledger.jsonl 不存在）
        try:
            migrated = migrate_legacy_state(
                Path(self.ledger_path), Path(ACCOUNT_STATE_PATH), init_capital
            )
            if migrated:
                logger.info("P1-1 旧 account_state.json 已迁移为 ledger.jsonl")
        except Exception as e:
            logger.warning(f"P1-1 旧状态迁移失败（不阻断）: {e}")

        # P1-1.6: 启动时调用 replay_ledger 重建状态
        self._restore_from_ledger()

    def query_account(self) -> Dict[str, Any]:
        return self.account.to_dict()

    # ------------------------------------------------------------------
    # P1-1: JSONL 账本集成
    # ------------------------------------------------------------------

    def _restore_from_ledger(self) -> None:
        """P1-1.6: 启动时调用 replay_ledger 重建账户状态"""
        try:
            snapshot = replay_ledger(Path(self.ledger_path), self.init_capital)
        except Exception as e:
            logger.warning(f"P1-1 replay_ledger 失败（使用初始资金）: {e}")
            return
        if snapshot.last_trade_date:
            # 有历史记录，用重建结果覆盖 account
            self.account.available_cash = snapshot.cash
            self.account.nav = snapshot.nav
            # P1-1 下 ledger 是 source of truth 且**优先于** account_state.json
            # （load_state 见 _ledger_restored 即跳过），故日初净值必须在此一并
            # 恢复：否则重启后 nav=70 万而 sod 仍为 INIT_CAPITAL=100 万，当日盈亏
            # 被凭空算成 -30%，单日亏损检查永久误杀、账户无法交易（2026-08-28 实测）。
            self.account.start_of_day_nav = self._resolve_sod_on_restore(snapshot)
            # PositionState → account.positions dict
            self.account.positions = {
                code: {
                    "volume": pos.shares,
                    "available_volume": pos.available,
                    "avg_cost": pos.cost,
                }
                for code, pos in snapshot.positions.items()
            }
            self._ledger_restored = True
            logger.info(
                f"P1-1 已从 ledger 重建状态: nav={snapshot.nav}, "
                f"sod={self.account.start_of_day_nav}, 持仓标的数={len(snapshot.positions)}"
            )
        else:
            self._ledger_restored = False

    def _resolve_sod_on_restore(self, snapshot):
        """从 ledger 快照推导重启后的日初净值基线。

        优先级：
        1. **上次成交在往日** → 直接取 ``snapshot.nav``（= 上一交易日收盘净值），
           这正是严格意义上的"日初净值"，无信息损失；
        2. **同日已有成交** → 同样取 ``snapshot.nav``，但属降级：ledger 未记录
           "成交前净值"，当日已实现亏损不纳入计算（与二期 live 侧"日内冷启动
           漏损"同源，已文档化）。

        绝不返回 ``INIT_CAPITAL``——历史盈亏会让它与真实净值脱节，直接导致
        单日亏损检查误杀或失效。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if snapshot.last_trade_date == today:
            logger.warning(
                "P1-1 ledger 含当日成交，日初净值降级取当前净值"
                "（ledger 未记录成交前净值，当日已实现亏损不纳入单日亏损计算）"
            )
        return float(snapshot.nav)

    def _append_trade_record(
        self,
        code: str,
        side: str,
        shares: int,
        price: float,
        base_price: float,
        commission: float,
        stamp_tax: float,
    ) -> None:
        """P1-1.5: 成交后追加一条 record 到 ledger.jsonl"""
        self._trade_seq += 1
        ts = datetime.now()
        pos = self.account.positions.get(code, {})
        position_after = pos.get("volume", 0)
        nav_after = self.account.nav
        # 滑点成本 = 成交价相对基准价的偏离 × 股数（仅 SLIPPAGE>0 时有意义）
        slippage_cost = abs(price - base_price) * shares if SLIPPAGE > 0 else 0.0
        try:
            record = PaperTradeRecordV1(
                execution_id=f"{ts.strftime('%Y%m%d%H%M%S')}_{self._trade_seq:04d}",
                trade_date=ts.strftime("%Y-%m-%d"),
                code=code,
                side=side,
                shares=shares,
                price=price,
                commission=commission,
                stamp_tax=stamp_tax,
                slippage_cost=slippage_cost,
                position_after_shares=position_after,
                cash_after=self.account.available_cash,
                nav_after=nav_after,
                confirmed=True,
                created_at=ts,
            )
            append_paper_trade(Path(self.ledger_path), record)
        except Exception as e:
            logger.warning(f"P1-1 追加 ledger 失败（不阻断交易）: {e}")

    def send_order(
        self,
        code: str,
        side: str,
        volume: int,
        price: Optional[float] = None,
        order_type: str = "limit"
    ) -> Dict[str, Any]:
        # 数量校验: A股最小100股
        if volume < 100 or volume % 100 != 0:
            return {"success": False, "error": f"下单数量需为100的整数倍且≥100, 当前 {volume}"}

        # 价格校验
        if order_type == "limit" and price is None:
            return {"success": False, "error": "限价单必须指定价格"}
        if price is not None and price <= 0:
            return {"success": False, "error": f"价格必须>0, 当前 {price}"}

        # 滑点模拟
        base_price = price if price is not None else 0
        if order_type == "market" and base_price == 0:
            return {"success": False, "error": "市价单需提供基准价格"}
        fill_price = base_price
        if SLIPPAGE > 0:
            if side == "buy":
                fill_price = base_price * (1 + SLIPPAGE)
            elif side == "sell":
                fill_price = base_price * (1 - SLIPPAGE)

        order_value = fill_price * volume
        check = self.circuit_breaker.check_send_order(self.account, code, order_value)

        if not check["allowed"]:
            return {"success": False, "error": check["reason"]}

        order_id = str(uuid.uuid4())[:12]
        try:
            if side == "buy":
                commission = self.account.calc_commission(order_value, is_sell=False)
                self.account.apply_buy(code, fill_price, volume, commission)
            elif side == "sell":
                total_fee = self.account.calc_commission(order_value, is_sell=True)
                stamp_tax = order_value * STAMP_TAX_RATE
                commission = total_fee - stamp_tax
                self.account.apply_sell(code, fill_price, volume, commission, stamp_tax)
            else:
                return {"success": False, "error": f"未知 side: {side}"}

            self.orders[order_id] = {
                "order_id": order_id, "code": code, "side": side,
                "volume": volume, "price": fill_price, "order_price": base_price,
                "status": "filled",
                "timestamp": datetime.now().isoformat(),
            }
            self.audit.log_order(order_id, code, side, volume, fill_price, "filled", {"order_price": base_price})
            # P1-1.5: 成交后追加 record 到 ledger.jsonl（事务日志）
            stamp_tax_amt = order_value * STAMP_TAX_RATE if side == "sell" else 0.0
            self._append_trade_record(code, side, volume, fill_price, base_price, commission, stamp_tax_amt)
            return {"success": True, "order_id": order_id, "status": "filled", "fill_price": fill_price}

        except Exception as e:
            self.audit.log_order(order_id, code, side, volume, fill_price, "rejected", {"error": str(e)})
            return {"success": False, "order_id": order_id, "status": "rejected", "error": str(e)}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if order_id in self.orders:
            self.orders[order_id]["status"] = "cancelled"
            return {"success": True, "order_id": order_id}
        return {"success": False, "error": "订单不存在"}

    def query_positions(self) -> pd.DataFrame:
        rows = []
        for code, pos in self.account.positions.items():
            rows.append({
                "code": code, "volume": pos["volume"],
                "available_volume": pos.get("available_volume", pos["volume"]),
                "avg_cost": pos["avg_cost"]
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["code", "volume", "available_volume", "avg_cost"])

    def sync_positions(
        self,
        target_weights: Dict[str, float],
        prices: Dict[str, float],
    ) -> List[Dict]:
        """同步目标仓位，生成买卖订单列表"""
        nav = self.account.get_current_nav(prices)
        orders_to_execute = []

        for code, target_weight in target_weights.items():
            if code not in prices:
                continue
            price = prices[code]
            target_value = nav * target_weight
            pos = self.account.positions.get(code, {})
            current_volume = pos.get("volume", 0)
            current_available = pos.get("available_volume", current_volume)
            target_volume = int((target_value / price) // 100 * 100)

            if target_volume > current_volume:
                # 需买入(看总持仓差额)
                diff = target_volume - current_volume
                if diff >= 100:
                    orders_to_execute.append({
                        "code": code, "side": "buy", "volume": diff,
                        "price": price, "order_type": "limit",
                    })
            elif target_volume < current_available:
                # 需卖出(只能卖可用部分,T+1约束)
                diff = current_available - target_volume
                if diff >= 100:
                    orders_to_execute.append({
                        "code": code, "side": "sell", "volume": diff,
                        "price": price, "order_type": "limit",
                    })

        return orders_to_execute

    def save_state(self):
        """持久化账户状态。

        ⚠️ ``start_of_day_nav`` 必须一并落盘（2026-08-28 修复，自 A 树同步）：
        此前只写 nav/available_cash/positions，导致 paper 侧日初净值不跨重启
        持久——重启后 ``Account`` 回落默认 ``INIT_CAPITAL``，单日亏损检查的基线
        被悄悄重置，当日已实现亏损被"洗白"。``Account.to_dict()`` 本就导出该
        字段，此处只是不再丢掉它。
        """
        state = {
            "nav": self.account.nav,
            "available_cash": self.account.available_cash,
            "start_of_day_nav": self.account.start_of_day_nav,
            "positions": self.account.positions,
            "updated_at": datetime.now().isoformat(),
        }
        os.makedirs(os.path.dirname(ACCOUNT_STATE_PATH), exist_ok=True)
        with open(ACCOUNT_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def load_state(self) -> bool:
        """加载持久化的账户状态。

        P1-1: 若 ledger 已重建状态（__init__ 中 _restore_from_ledger），
        则跳过 account_state.json 加载（ledger 是 source of truth）。
        """
        # P1-1: ledger 已恢复 → 不覆盖
        if getattr(self, "_ledger_restored", False):
            logger.debug("P1-1 ledger 已恢复状态，跳过 account_state.json 加载")
            return True
        if not os.path.exists(ACCOUNT_STATE_PATH):
            logger.info("无账户状态文件，使用初始资金")
            return False
        with open(ACCOUNT_STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        self.account.nav = state.get("nav", INIT_CAPITAL)
        self.account.available_cash = state.get("available_cash", INIT_CAPITAL)
        # 与 save_state 对称读回日初净值；无该键（旧状态文件）时退化为当前 nav，
        # 而非 INIT_CAPITAL——后者在账户已盈亏时会凭空造出一个错误基线。
        self.account.start_of_day_nav = state.get("start_of_day_nav", self.account.nav)
        positions = state.get("positions", {})
        # 兼容旧状态文件:补齐 available_volume 字段
        for code, pos in positions.items():
            if "available_volume" not in pos:
                pos["available_volume"] = pos.get("volume", 0)
        self.account.positions = positions
        logger.info(f"已加载账户状态: nav={self.account.nav}")
        return True


def run(ctx) -> Dict[str, Any]:
    """
    execution-monitor-engine 的 run 函数

    参数:
        ctx: Context 对象，需包含:
            - artifacts['PORTFOLIO']: 目标权重 JSON 路径
            - artifacts['DATA']: 行情数据

    返回:
        {
            "success": bool,
            "artifact_path": str,
            "metadata": {...},
            "error": str
        }
    """
    try:
        os.makedirs(EXECUTION_DIR, exist_ok=True)

        portfolio_path = ctx.get_artifact("PORTFOLIO")
        if not portfolio_path or not os.path.exists(portfolio_path):
            return {"success": False, "artifact_path": "", "metadata": {}, "error": "目标权重文件不存在"}

        with open(portfolio_path, "r", encoding="utf-8") as f:
            target_weights = json.load(f)

        data_path = ctx.get_artifact("DATA")
        prices = {}
        if data_path and os.path.exists(data_path):
            data = pd.read_parquet(data_path)
            latest = data[data['date'] == data['date'].max()]
            prices = dict(zip(latest['code'], latest['close']))
        else:
            for code in target_weights:
                prices[code] = 10.0
            logger.warning("无行情数据，使用默认价格10元")

        mode = TRADE_MODE
        if mode == "paper":
            executor = PaperExecutor()
            executor.load_state()
        elif mode == "live":
            backend = TRADE_BACKEND
            if backend == "xtquant":
                from scripts.adapters.xtquant_adapter import XtQuantExecutor
                executor = XtQuantExecutor()
                if not executor.connect():
                    return {"success": False, "artifact_path": "", "metadata": {},
                            "error": "miniQMT 连接失败,请检查 XTQUANT_PATH/XTQUANT_ACCOUNT 配置及客户端运行状态"}
            elif backend == "gm":
                from scripts.adapters.gm_adapter import GMExecutor
                executor = GMExecutor()
                if not executor._try_connect():
                    # gm 连接失败时降级到 PaperExecutor，保证执行监控流程不中断
                    # （gm.api 的 get_cash 对仿真账户会报 1013；实盘账户终端未就绪也会失败）
                    logger.warning(
                        "掘金终端连接失败，降级到模拟交易模式(PaperExecutor)。"
                        "如需实盘，请检查 GM_TOKEN/GM_ACCOUNT_ID、掘金终端登录状态及账户实盘交易权限。"
                    )
                    executor = PaperExecutor()
                    executor.load_state()
                    mode = "paper"  # 降级标记
            else:
                return {"success": False, "artifact_path": "", "metadata": {},
                        "error": f"不支持的交易后端: {backend}"}
        else:
            return {"success": False, "artifact_path": "", "metadata": {},
                    "error": f"未知交易模式: {mode}(应为 paper/live)"}

        # 仅 paper 执行器有 account 字段和 reset_daily 语义
        if hasattr(executor, "account") and executor.account is not None:
            executor.account.reset_daily()
        orders = executor.sync_positions(target_weights, prices)

        success_count = 0
        fail_count = 0
        for order in orders:
            result = executor.send_order(
                code=order["code"],
                side=order["side"],
                volume=order["volume"],
                price=order["price"],
                order_type=order.get("order_type", "limit"),
            )
            if result.get("success"):
                success_count += 1
            else:
                fail_count += 1
                logger.warning(f"订单失败: {result}")

        # 仅 paper 执行器支持本地状态持久化
        if hasattr(executor, "save_state"):
            executor.save_state()
        account_snapshot = executor.query_account()

        return {
            "success": True,
            "artifact_path": AUDIT_LOG_PATH,
            "metadata": {
                "orders_executed": success_count,
                "orders_failed": fail_count,
                "account_snapshot": account_snapshot,
                "mode": mode,
            },
            "error": ""
        }

    except Exception as e:
        logger.exception("执行引擎执行失败")
        return {"success": False, "artifact_path": "", "metadata": {}, "error": str(e)}


# ============================================================================
# LIVE 模式入口
# ============================================================================

def _load_reports_engine_modules():
    """动态加载 reports-engine scripts 模块（运行时加载，避免硬依赖）

    注意: 会覆盖 sys.modules["scripts"] 指向 reports-engine。
    但 engine.py 顶层 import 已完成，不影响已绑定的变量。
    """
    import importlib.util as ilu
    from unittest import mock

    # 定位 reports-engine/scripts 目录
    engine_dir = os.path.dirname(os.path.abspath(__file__))
    skills_dir = os.path.dirname(engine_dir)
    reports_scripts = os.path.join(skills_dir, "reports-engine", "scripts")

    if not os.path.isdir(reports_scripts):
        logger.warning(f"reports-engine 目录不存在: {reports_scripts}")
        return None

    # 清理旧 scripts 模块
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # mock 可选依赖
    for _m in ("talib", "pandas_ta", "sklearn", "sklearn.linear_model",
               "sklearn.ensemble", "sklearn.model_selection"):
        if _m not in sys.modules:
            sys.modules[_m] = mock.MagicMock()

    # 加载 reports-engine scripts 包
    init_py = os.path.join(reports_scripts, "__init__.py")
    if not os.path.exists(init_py):
        logger.warning("reports-engine scripts/__init__.py 不存在")
        return None

    spec = ilu.spec_from_file_location(
        "scripts", init_py,
        submodule_search_locations=[reports_scripts],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 加载所需子模块
    module_specs = [
        ("scripts.config", os.path.join(reports_scripts, "config.py")),
        ("scripts.cross_engine_adapter", os.path.join(reports_scripts, "cross_engine_adapter.py")),
        ("scripts.status_server", os.path.join(reports_scripts, "status_server.py")),
        ("scripts.renderers.svg_components", os.path.join(reports_scripts, "renderers", "svg_components.py")),
        ("scripts.templates.portfolio_charts_p1p2", os.path.join(reports_scripts, "templates", "portfolio_charts_p1p2.py")),
        ("scripts.templates.execution_charts_p1p2", os.path.join(reports_scripts, "templates", "execution_charts_p1p2.py")),
        ("scripts.templates.live_polling", os.path.join(reports_scripts, "templates", "live_polling.py")),
        ("scripts.templates.portfolio_report", os.path.join(reports_scripts, "templates", "portfolio_report.py")),
        ("scripts.templates.execution_report", os.path.join(reports_scripts, "templates", "execution_report.py")),
    ]
    for mod_name, mod_path in module_specs:
        if not os.path.exists(mod_path):
            continue
        spec = ilu.spec_from_file_location(mod_name, mod_path)
        mod = ilu.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    return True


def _generate_live_report_html(monitor, audit_path, ledger_path, mode, backend,
                               live_mode="http", snapshot_file="snapshot.js"):
    """生成 LIVE 模式执行监控报告 HTML

    设计（渲染收敛）：execution-monitor-engine 只负责"实时采集 + 触发"，
    报告的渲染组装统一复用 reports-engine 插件的统一入口
    ``scripts.templates.execution_report.build_execution_report``（插件 execution_report
    与 LIVE 模式共用同一套模板 + 轮询能力，避免渲染组装逻辑在两端重复维护）。

    参数:
        monitor: LiveMonitor 实例（实时账户快照来源）
        audit_path: 委托日志路径
        ledger_path: 成交账本路径
        mode: 交易模式 (paper/live)
        backend: 交易后端 (paper/xtquant/gm)
        live_mode: 数据传输方式 "http"（StatusServer）或 "jsonp"（snapshot.js 文件）
        snapshot_file: JSONP 模式下 snapshot.js 的相对路径

    返回:
        完整的 HTML 字符串
    """
    if not _load_reports_engine_modules():
        return (
            "<html><body><h1>LIVE 模式（报告引擎不可用）</h1>"
            "<p>reports-engine 未安装，仅提供 API 服务。</p></body></html>"
        )

    try:
        from scripts.templates.execution_report import (
            build_execution_report,
            load_trade_log,
        )

        # ── 实时采集（保留 execution-monitor-engine 职责）：实时账户快照 + 委托日志 ──
        account_snapshot = monitor.unified_account_snapshot()
        loaded_trades = load_trade_log(audit_path)
        orders_executed = sum(1 for t in loaded_trades if t.get("status") == "filled")
        orders_failed = sum(1 for t in loaded_trades if t.get("status") == "rejected")

        # 组装成插件统一入口 build_execution_report 所需的 execution_metadata
        execution_metadata = {
            "mode": mode,
            "backend": backend,
            "backend_available": True,
            "account_snapshot": account_snapshot,
            "orders_executed": orders_executed,
            "orders_failed": orders_failed,
        }

        # ── 渲染：复用插件统一入口 build_execution_report（含 LIVE 轮询）──
        # JSONP（推荐）用 snapshot.js 文件轮询（file:// 双击即看，无需 HTTP 服务器）。
        # HTTP（原方案）由 build_execution_report 内部启动 StatusServer 提供实时 API。
        return build_execution_report(
            execution_metadata=execution_metadata,
            audit_log_path=audit_path,
            ledger_path=ledger_path,
            include_p1=True,
            include_p2=True,
            enable_live_polling=True,
            live_mode=live_mode,
            live_snapshot_file=snapshot_file,
            live_poll_interval=3,
            serve_blocking=False,
        )
    except Exception as e:
        logger.exception("生成 LIVE 报告 HTML 失败")
        return f"<html><body><h1>LIVE 模式</h1><p>报告生成失败: {e}</p></body></html>"


def run_live(ctx, live_data_mode: str = "jsonp") -> Dict[str, Any]:
    """LIVE 模式入口: 启动实时监控服务

    将 LIVE 模式集成到 execution-monitor-engine 正式流程。
    通过 Context 调用，符合引擎调用规范。

    流程:
    1. 根据 TRADE_MODE/TRADE_BACKEND 初始化执行器
    2. 创建 LiveMonitor 统一数据格式
    3. 写入数据产物（account_state.json / trade_log.jsonl / ledger.jsonl）
    4. 动态加载 reports-engine 生成执行监控 HTML 报告
    5. 根据 live_data_mode 选择数据传输方式：
       - "jsonp": 写 snapshot.js 文件，HTML 双击即看，无需 HTTP 服务器（推荐）
       - "http": 启动 StatusServer 托管报告 + 提供 /api/snapshot 实时 API
    6. 阻塞运行直到 Ctrl+C

    参数:
        ctx: Context 对象（兼容接口，LIVE 模式不强制要求 artifacts）
        live_data_mode: 数据传输方式 "jsonp"（默认）或 "http"

    返回:
        {"success": bool, "port": int, "error": str, "report_path": str}
    """
    try:
        os.makedirs(EXECUTION_DIR, exist_ok=True)

        mode = TRADE_MODE
        backend = TRADE_BACKEND

        # 1. 初始化执行器
        if mode == "paper":
            executor = PaperExecutor()
            executor.load_state()
        elif mode == "live":
            if backend == "xtquant":
                from scripts.adapters.xtquant_adapter import XtQuantExecutor
                executor = XtQuantExecutor()
                if not executor.connect():
                    return {"success": False, "port": 0,
                            "error": "xtquant 连接失败,请检查 XTQUANT_PATH/XTQUANT_ACCOUNT 配置及客户端运行状态"}
            elif backend == "gm":
                from scripts.adapters.gm_adapter import GMExecutor
                executor = GMExecutor()
                if not executor._try_connect():
                    # gm 连接失败时降级到 PaperExecutor，保证 LIVE 监控流程不中断
                    logger.warning(
                        "掘金终端连接失败，降级到模拟交易模式(PaperExecutor)。"
                        "如需实盘，请检查 GM_TOKEN/GM_ACCOUNT_ID、掘金终端登录状态及账户实盘交易权限。"
                    )
                    executor = PaperExecutor()
                    executor.load_state()
                    mode = "paper"  # 降级标记
            else:
                return {"success": False, "port": 0, "error": f"不支持的后端: {backend}"}
        else:
            return {"success": False, "port": 0, "error": f"未知模式: {mode}"}

        # 2. 创建 LiveMonitor
        from scripts.live_monitor import LiveMonitor
        monitor = LiveMonitor(executor)

        # 3. 写入数据产物
        paths = monitor.write_artifacts(EXECUTION_DIR)

        # 4. 确定报告输出目录
        _work_dir = os.environ.get("QUANT_WORK_DIR", "./workspace")
        _report_dir = os.path.join(_work_dir, "reports")
        os.makedirs(_report_dir, exist_ok=True)

        if live_data_mode == "jsonp":
            # ── JSONP 模式：写 snapshot.js 文件，HTML 双击即看 ──
            # LIVE 报告独立子目录，避免污染静态报告
            live_dir = os.path.join(_report_dir, "live")
            os.makedirs(live_dir, exist_ok=True)
            snapshot_path = os.path.join(live_dir, "snapshot.js")

            # 首次写入 snapshot.js
            monitor.write_jsonp_snapshot(snapshot_path)

            # 生成 LIVE 报告 HTML（使用 JSONP 轮询脚本）
            report_html = _generate_live_report_html(
                monitor=monitor,
                audit_path=paths["audit_path"],
                ledger_path=paths["ledger_path"],
                mode=mode,
                backend=backend,
                live_mode="jsonp",
                snapshot_file="snapshot.js",
            )

            # 写入 execution_live.html
            html_path = os.path.join(live_dir, "execution_live.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(report_html)
            logger.info(f"LIVE 报告已生成（JSONP 模式）: {html_path}")

            # 更新报告门户 manifest
            try:
                import importlib.util as ilu
                _reports_scripts = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "reports-engine", "scripts"
                )
                portal_spec = ilu.spec_from_file_location(
                    "report_portal",
                    os.path.join(_reports_scripts, "report_portal.py")
                )
                portal_mod = ilu.module_from_spec(portal_spec)
                portal_spec.loader.exec_module(portal_mod)

                portal_mod.upsert_report(
                    report_dir=_report_dir,
                    report_type="execution_live",
                    file_path=html_path,
                    task_id=ctx.task_id if hasattr(ctx, 'task_id') else "",
                    live=True,
                    backend=backend,
                    snapshot_file="live/snapshot.js",
                )
                portal_mod.generate_portal(_report_dir)
            except Exception as e:
                logger.warning(f"更新报告门户失败（不阻断）: {e}")

            print(f"\n{'='*60}")
            print(f"LIVE 实盘报告已就绪 ({backend} · JSONP 模式)")
            print(f"双击打开 HTML 文件即可查看实时数据:")
            print(f"  {html_path}")
            print(f"门户页:")
            print(f"  {os.path.join(_report_dir, 'index.html')}")
            print(f"数据来源: {backend} 实时接口（每 3 秒自动刷新 snapshot.js）")
            print(f"按 Ctrl+C 退出服务")
            print(f"{'='*60}\n", flush=True)

            # 6. 阻塞循环：定时写 snapshot.js
            # GAP 修复：每 60 次循环（约 3 分钟）追加一次 ledger，
            # 让 LIVE 模式下 ledger.jsonl 随实时成交增长，绩效归因可拿到新交易。
            _ledger_tick = 0
            _ledger_interval = max(int(os.environ.get("QUANT_LEDGER_APPEND_TICKS", "60")), 1)
            try:
                while True:
                    time.sleep(3)
                    monitor.write_jsonp_snapshot(snapshot_path)
                    _ledger_tick += 1
                    if _ledger_tick % _ledger_interval == 0:
                        try:
                            monitor.write_artifacts(EXECUTION_DIR)
                            logger.info(f"LIVE ledger 已周期追加（tick={_ledger_tick}）")
                        except Exception as e:
                            logger.warning(f"LIVE ledger 周期追加失败（不阻断）: {e}")
            except KeyboardInterrupt:
                print("\n服务已停止")
                return {"success": True, "port": 0, "error": "",
                        "report_path": html_path}

        else:
            # ── HTTP 模式：由 build_execution_report 内部启动 StatusServer 托管报告 ──
            report_html = _generate_live_report_html(
                monitor=monitor,
                audit_path=paths["audit_path"],
                ledger_path=paths["ledger_path"],
                mode=mode,
                backend=backend,
            )

            # build_execution_report 的 HTTP 模式已在内部启动 StatusServer 并生成
            # 含真实端口轮询脚本的 HTML（不再需要二次启动 / 占位端口替换）。
            # 从 HTML 中提取实际端口（形如 http://127.0.0.1:{port} 或 :{port}/）。
            import re as _re
            port = 0
            m = _re.search(r"127\.0\.0\.1:(\d+)", report_html)
            if m:
                port = int(m.group(1))

            # 写端口文件
            port_file = os.path.join(os.path.dirname(EXECUTION_DIR), ".live_port")
            with open(port_file, "w") as f:
                f.write(str(port))

            print(f"\n{'='*60}")
            print(f"LIVE 实盘报告已就绪 ({backend} · HTTP 模式)，请用浏览器访问:")
            print(f"  http://127.0.0.1:{port}/")
            print(f"数据来源: {backend} 实时接口（每 3 秒自动刷新）")
            print(f"按 Ctrl+C 退出服务")
            print(f"{'='*60}\n", flush=True)

            # 6. 阻塞运行：StatusServer 由 build_execution_report 内部启动为守护线程，
            # 此处保持主线程存活直到 Ctrl+C。
            try:
                while True:
                    time.sleep(3)
                    # 实时刷新 account_state.json，供 StatusServer 的 /api/snapshot 返回最新快照
                    try:
                        monitor.write_artifacts(EXECUTION_DIR)
                    except Exception as e:
                        logger.warning(f"HTTP LIVE 刷新账本失败（不阻断）: {e}")
            except KeyboardInterrupt:
                print("\n服务已停止")

            return {"success": True, "port": port, "error": "",
                    "report_path": ""}

    except Exception as e:
        logger.exception("LIVE 模式启动失败")
        return {"success": False, "port": 0, "error": str(e), "report_path": ""}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            ctx_dict = json.load(f)
        from scripts.context import Context
        ctx = Context.from_dict(ctx_dict)
    else:
        from scripts.context import Context
        ctx = Context(
            task_id="test_execution",
            stock_pool=[],
            start_date="2024-01-01",
            end_date="2024-12-31"
        )
        ctx.update_artifact("PORTFOLIO", "./workspace/portfolio/portfolio_weights.json")
        ctx.update_artifact("DATA", "./workspace/data/cleaned_data.parquet")

    result = run(ctx)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
