"""交易硬风控断路器。

从 ``engine.py`` 下沉到本模块的原因：
``CircuitBreaker`` 原先定义在 ``engine.py``，导致 live 适配器（xtquant/gm/tdxquant）
无法复用它——适配器若 ``from engine import CircuitBreaker`` 会形成循环导入
（engine → adapters → engine），且测试用 importlib 以合成模块名加载 engine.py，
适配器侧的绝对导入根本解析不到。下沉到 ``scripts/base/`` 后，engine.py 与三个
live 适配器可共享同一份实现，paper 与 live 走同一把尺子。

对外两个入口（共用同一核心 ``_check``）：
- ``check_send_order``：paper 路径，完整三项检查（单日亏损 + 单笔比例 + 频率）
- ``check_live_order`` ：live 路径，仅频率 + 单笔比例；单日亏损待 live 侧能稳定
  提供 start_of_day_nav 后二期补上（live 账户 dict 无该字段）
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from ..config import MAX_DAILY_LOSS_RATIO, MAX_ORDER_FREQUENCY, MAX_SINGLE_ORDER_RATIO


class CircuitBreaker:
    """硬风控断路器。

    阈值在**构造时**从 config 解析（未在构造时传入则取 config 默认值）。
    这让 ``PaperExecutor`` 可以显式传入 engine 模块的全局阈值，
    从而使既有的 ``monkeypatch.setattr(engine, "MAX_SINGLE_ORDER_RATIO", ...)``
    继续生效；live 适配器不传参，直接使用 config 默认值。
    """

    def __init__(
        self,
        max_daily_loss_ratio: float | None = None,
        max_single_order_ratio: float | None = None,
        max_order_frequency: int | None = None,
    ):
        self._max_daily_loss_ratio = (
            float(MAX_DAILY_LOSS_RATIO) if max_daily_loss_ratio is None else float(max_daily_loss_ratio)
        )
        self._max_single_order_ratio = (
            float(MAX_SINGLE_ORDER_RATIO) if max_single_order_ratio is None else float(max_single_order_ratio)
        )
        self._max_order_frequency = (
            int(MAX_ORDER_FREQUENCY) if max_order_frequency is None else int(max_order_frequency)
        )
        self.last_order_times: List[float] = []

    def _thresholds(self) -> tuple[float, float, int]:
        """返回本次检查使用的 (单日亏损阈值, 单笔比例阈值, 频率阈值)。

        抽成方法是为了让 engine 侧子类可以**动态**读取 engine 模块全局阈值
        （既有测试用 ``monkeypatch.setattr(engine, "MAX_SINGLE_ORDER_RATIO", ...)``
        覆盖，构造期快照会让该 patch 失效）。默认实现返回构造期解析的值。
        """
        return self._max_daily_loss_ratio, self._max_single_order_ratio, self._max_order_frequency

    def check_send_order(
        self,
        account: Any,
        code: str,
        order_value: float,
        prices: Dict[str, float] | None = None,
    ) -> Dict[str, Any]:
        """paper 路径检查：单日亏损 + 单笔比例 + 频率。

        参数:
            account: ``Account`` 实例（提供 get_current_nav 与 start_of_day_nav）
            code: 标的代码（当前不参与判定，保留以兼容既有调用签名）
            order_value: 本笔委托金额
            prices: 最新价，用于计算当前净值

        返回:
            {"allowed": bool, "reason": str}
        """
        current_nav = account.get_current_nav(prices)
        return self._check(
            current_nav=current_nav,
            order_value=order_value,
            start_of_day_nav=account.start_of_day_nav,
        )

    def check_live_order(self, current_nav: float, order_value: float, code: str = "") -> Dict[str, Any]:
        """live 路径检查：频率 + 单笔比例（不含单日亏损）。

        与 paper 路径的差异：live 账户快照是 broker 返回的 dict，只有
        total_assets/available_cash 等字段，**没有 start_of_day_nav**，
        无法计算当日盈亏，故该项留待二期（需由引擎侧持久化每日起始净值）。

        fail-closed 语义：拿不到账户总资产（nav<=0）时直接拒单。实盘环境下
        "看不清账户还下单" 比 "保守拒单" 危险得多，故此处不做 fail-open。

        参数:
            current_nav: 账户总资产（来自 query_account()["total_assets"]）
            order_value: 本笔委托金额（price × volume）
            code: 标的代码（当前不参与判定，保持与 paper 路径签名对称）

        返回:
            {"allowed": bool, "reason": str}
        """
        return self._check(current_nav=current_nav, order_value=order_value, start_of_day_nav=None)

    def _check(
        self,
        current_nav: float,
        order_value: float,
        start_of_day_nav: float | None = None,
    ) -> Dict[str, Any]:
        """三项检查的唯一实现，paper/live 共用。

        参数:
            current_nav: 当前账户总资产
            order_value: 本笔委托金额
            start_of_day_nav: 当日起始净值；None 表示跳过单日亏损检查（live 路径）
        """
        max_daily_loss_ratio, max_single_order_ratio, max_order_frequency = self._thresholds()

        # 1) 单日亏损：仅在能提供当日起始净值时检查（paper 有，live 二期补）
        if start_of_day_nav is not None and start_of_day_nav > 0:
            daily_return = (current_nav - start_of_day_nav) / start_of_day_nav
            if not daily_return > -max_daily_loss_ratio:
                return {
                    "allowed": False,
                    "reason": f"单日亏损 {daily_return:.2%} 超过阈值 {max_daily_loss_ratio:.2%}",
                }

        # 2) 单笔委托金额占比
        single_order_limit = current_nav * max_single_order_ratio
        if order_value > single_order_limit:
            return {
                "allowed": False,
                "reason": f"单笔金额 {order_value:.0f} 超过上限 {single_order_limit:.0f}",
            }

        # 3) 下单频率
        if not self._check_frequency(max_order_frequency):
            return {"allowed": False, "reason": f"订单频率超过每秒 {max_order_frequency} 次限制"}

        self.last_order_times.append(time.time())
        return {"allowed": True, "reason": ""}

    def _check_frequency(self, max_order_frequency: int | None = None) -> bool:
        if max_order_frequency is None:
            max_order_frequency = self._thresholds()[2]
        now = time.time()
        self.last_order_times = [t for t in self.last_order_times if now - t < 1.0]
        return len(self.last_order_times) < max_order_frequency
