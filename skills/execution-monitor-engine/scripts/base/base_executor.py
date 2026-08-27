"""
交易执行器抽象基类
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import pandas as pd

from .circuit_breaker import CircuitBreaker


class BaseExecutor(ABC):
    """交易执行器基类

    断路器在**基类**构造：这样 paper（PaperExecutor）与 live（xtquant/gm）
    适配器都持有 ``self.circuit_breaker``，风控不再只覆盖模拟盘。

    子类职责：各自在 ``send_order`` 内显式调用
    ``self.circuit_breaker.check_send_order``（paper，三项全查）或
    ``self._live_risk_guard``（live，频率 + 单笔比例）。
    基类不强制插入检查点——paper 的检查点夹在"价格/滑点计算之后、撮合成交之前"，
    位置由子类按自身撮合语义决定，基类无法代劳。
    """

    def __init__(self):
        self.circuit_breaker = CircuitBreaker()
        self._logger = logging.getLogger(self.__class__.__name__)

    def _live_risk_guard(
        self, code: str, volume: int, price: Optional[float] = None, order_value: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """live 下单前的硬风控检查（供实盘适配器共用）。

        约定：命中风控返回 ``{"success": False, "error": ...}`` 由适配器直接透传；
        未命中返回 None 表示放行。

        检查项（live 一期）：下单频率 + 单笔金额占总资产比例。
        单日亏损检查待 live 侧能稳定提供 start_of_day_nav 后二期接入——
        broker 返回的账户 dict 无该字段，强行用当日快照代替会算出错误盈亏。

        **fail-closed**：``query_account()`` 拿不到总资产（返回空/0）时判为拒单。
        实盘场景下"账户看不清还下单"远比"保守拒单"危险，故不采用 fail-open。

        返回:
            None（放行）或 {"success": False, "error": str}（拒单）
        """
        if order_value is None:
            order_value = float(price or 0.0) * int(volume or 0)

        current_nav = 0.0
        try:
            account = self.query_account()
            if isinstance(account, dict):
                current_nav = float(account.get("total_assets") or 0.0)
        except Exception as exc:  # noqa: BLE001 查询失败不得放行，按 nav=0 处理
            self._logger.warning(f"live 风控检查前查询账户失败，按拒单处理: {exc}")

        check = self.circuit_breaker.check_live_order(current_nav=current_nav, order_value=order_value, code=code)
        if not check["allowed"]:
            return {
                "success": False,
                "error": f"硬风控拒单: {check['reason']}",
                "code": code,
                "rejected_by": "circuit_breaker",
            }
        return None

    @abstractmethod
    def query_account(self) -> Dict[str, Any]:
        """查询账户资产、可用资金、持仓"""
        ...

    @abstractmethod
    def send_order(
        self,
        code: str,
        side: str,
        volume: int,
        price: Optional[float] = None,
        order_type: str = "limit"
    ) -> Dict[str, Any]:
        """
        发送订单

        参数:
            code: 股票代码
            side: buy / sell
            volume: 数量（股）
            price: 价格（限价单时必需）
            order_type: limit / market

        返回:
            订单信息字典
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """撤单"""
        ...

    @abstractmethod
    def query_positions(self) -> pd.DataFrame:
        """查询当前持仓"""
        ...

    @abstractmethod
    def sync_positions(self, target_weights: Dict[str, float], prices: Dict[str, float]) -> List[Dict]:
        """
        同步目标仓位

        参数:
            target_weights: {code: weight}
            prices: {code: latest_price}

        返回:
            需要执行的订单列表
        """
        ...