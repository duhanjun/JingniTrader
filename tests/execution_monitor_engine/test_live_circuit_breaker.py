"""execution-monitor-engine L2 单元测试：live 链路硬风控断路器接入。

背景（OPEN-2026-0827 live 断路器缺口）：
``CircuitBreaker`` 原先只被 ``PaperExecutor.send_order`` 调用，三个实盘适配器
（xtquant / gm / tdxquant）的 ``send_order`` 完全没有风控检查——paper 三层风控、
live 裸奔。本次将断路器下沉到 ``scripts/base/circuit_breaker.py`` 并由
``BaseExecutor`` 统一持有，三个 live 适配器在下单前调用 ``_live_risk_guard``。

本文件验证：
1. 三个 live 适配器均已接入断路器（持有实例且下单前实际调用）
2. 单笔金额超占比 → 拒单，且**未触达** broker 下单接口
3. 下单频率超限 → 拒单
4. fail-closed：账户总资产取不到时拒单（不 fail-open）
5. 未连接时仍优先返回"未连接"（风控不抢在连接检查之前）
6. paper 路径阈值仍可被 monkeypatch 覆盖（回归护栏）

全部用 mock，无需真实客户端/网络。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
from unittest import mock

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXECUTION_ENGINE_DIR = os.path.join(ROOT, "skills", "execution-monitor-engine")
SCRIPTS_DIR = os.path.join(EXECUTION_ENGINE_DIR, "scripts")
ADAPTERS_DIR = os.path.join(SCRIPTS_DIR, "adapters")


def _setup_modules():
    """加载 execution-monitor-engine 的 scripts 包 + 三个 live 适配器 + engine。

    返回 {"engine", "xtquant", "gm", "tdxquant", "config"}。
    """
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    spec = ilu.spec_from_file_location("scripts.config", os.path.join(SCRIPTS_DIR, "config.py"))
    config_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.config"] = config_mod
    spec.loader.exec_module(config_mod)

    base_dir = os.path.join(SCRIPTS_DIR, "base")
    spec = ilu.spec_from_file_location("scripts.base", os.path.join(base_dir, "__init__.py"),
                                       submodule_search_locations=[base_dir])
    bpkg = ilu.module_from_spec(spec)
    sys.modules["scripts.base"] = bpkg
    spec.loader.exec_module(bpkg)

    spec = ilu.spec_from_file_location("scripts.base.circuit_breaker",
                                       os.path.join(base_dir, "circuit_breaker.py"))
    cb_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.base.circuit_breaker"] = cb_mod
    spec.loader.exec_module(cb_mod)

    spec = ilu.spec_from_file_location("scripts.base.base_executor",
                                       os.path.join(base_dir, "base_executor.py"))
    be_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.base.base_executor"] = be_mod
    spec.loader.exec_module(be_mod)

    adapters_init = os.path.join(ADAPTERS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts.adapters", adapters_init,
                                       submodule_search_locations=[ADAPTERS_DIR])
    apkg = ilu.module_from_spec(spec)
    sys.modules["scripts.adapters"] = apkg
    spec.loader.exec_module(apkg)

    mods = {}
    for name in ("xtquant_adapter", "gm_adapter", "tdxquant_adapter"):
        adapter_path = os.path.join(ADAPTERS_DIR, f"{name}.py")
        if not os.path.exists(adapter_path):
            # 9-backend 陈旧树（主目录）无 tdxquant 适配器，跳过而非硬报错
            continue
        spec = ilu.spec_from_file_location(f"scripts.adapters.{name}", adapter_path)
        mod = ilu.module_from_spec(spec)
        sys.modules[f"scripts.adapters.{name}"] = mod
        spec.loader.exec_module(mod)
        mods[name.replace("_adapter", "")] = mod

    spec = ilu.spec_from_file_location("execution_monitor_engine_engine",
                                       os.path.join(EXECUTION_ENGINE_DIR, "engine.py"))
    engine_mod = ilu.module_from_spec(spec)
    sys.modules["execution_monitor_engine_engine"] = engine_mod
    spec.loader.exec_module(engine_mod)

    return {"engine": engine_mod, "config": config_mod, "base_executor": be_mod,
            "circuit_breaker": cb_mod, **mods}


@pytest.fixture
def mods(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECUTION_DIR", str(tmp_path / "exec"))
    return _setup_modules()


# ============================================================================
# 适配器接入状态：三处 live 通道都必须持有断路器
# ============================================================================


def _loaded_live_adapters(mods):
    """返回本树实际加载的 live 适配器 key 列表（陈旧树可能缺 tdxquant）。"""
    return [k for k in ("xtquant", "gm", "tdxquant") if k in mods]


def _p_live_adapters():
    """按当前树实际存在的适配器参数化，避免陈旧树（无 tdxquant）报错。"""
    keys = [k for k in ("xtquant", "gm", "tdxquant")
            if os.path.exists(os.path.join(ADAPTERS_DIR, f"{k}_adapter.py"))]
    return pytest.mark.parametrize("adapter_key", keys)


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestLiveBreakerWiring:
    """验证各 live 适配器均已持有断路器实例。"""

    @_p_live_adapters()
    def test_adapter_has_circuit_breaker(self, mods, adapter_key):
        """每个 live 适配器实例化后都应持有 circuit_breaker。"""
        executor = mods[adapter_key].__dict__[
            {"xtquant": "XtQuantExecutor", "gm": "GMExecutor", "tdxquant": "TdxQuantExecutor"}[adapter_key]
        ]()
        assert hasattr(executor, "circuit_breaker"), f"{adapter_key} 未持有断路器"
        assert executor.circuit_breaker is not None

    @_p_live_adapters()
    def test_adapter_has_live_risk_guard(self, mods, adapter_key):
        """适配器应继承基类的 _live_risk_guard 风控入口。"""
        cls_name = {"xtquant": "XtQuantExecutor", "gm": "GMExecutor", "tdxquant": "TdxQuantExecutor"}[adapter_key]
        executor = mods[adapter_key].__dict__[cls_name]()
        assert callable(getattr(executor, "_live_risk_guard", None))


# ============================================================================
# 单笔金额占比：超限时必须拒单且不触达 broker
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestLiveSingleOrderSize:
    """验证 live 下单的单笔金额占比检查。"""

    def test_xtquant_oversized_order_rejected(self, mods):
        """xtquant：单笔超过总资产 MAX_SINGLE_ORDER_RATIO 时拒单，且不调用 broker。"""
        xt_mod = mods["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._xt_trader = mock.MagicMock()

        # 总资产 100 万，单笔上限 10%（默认）→ 委托 1000 万应被拒
        with mock.patch.object(executor, "query_account", return_value={"total_assets": 1_000_000.0}):
            result = executor.send_order(code="600000.SH", side="buy", volume=100_000, price=100.0)

        assert result["success"] is False
        assert "硬风控拒单" in result["error"]
        assert result.get("rejected_by") == "circuit_breaker"
        # 关键：拒单不得触达 broker 下单接口
        executor._xt_trader.order_stock.assert_not_called()

    def test_gm_oversized_order_rejected(self, mods):
        """gm：单笔超过总资产占比时拒单，且不调用 broker。"""
        gm_mod = mods["gm"]
        executor = gm_mod.GMExecutor()
        executor._connected = True
        executor._available = True
        executor._gm = mock.MagicMock()

        with mock.patch.object(executor, "query_account", return_value={"total_assets": 1_000_000.0}):
            result = executor.send_order(code="600000.SH", side="buy", volume=100_000, price=100.0)

        assert result["success"] is False
        assert "硬风控拒单" in result["error"]
        executor._gm.order_volume.assert_not_called()

    @pytest.mark.skipif(
        not os.path.exists(os.path.join(ADAPTERS_DIR, "tdxquant_adapter.py")),
        reason="本树无 tdxquant 适配器（9-backend 陈旧树）",
    )
    def test_tdxquant_oversized_order_rejected(self, mods):
        """tdxquant：单笔超过总资产占比时拒单，且不调用 broker。"""
        td_mod = mods["tdxquant"]
        executor = td_mod.TdxQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._tq = mock.MagicMock()

        with mock.patch.object(executor, "query_account", return_value={"total_assets": 1_000_000.0}):
            result = executor.send_order(code="600000.SH", side="buy", volume=100_000, price=100.0)

        assert result["success"] is False
        assert "硬风控拒单" in result["error"]
        executor._tq.order_stock.assert_not_called()

    def test_within_limit_order_passes(self, mods):
        """限额内的订单应通过风控（未被误杀）。"""
        xt_mod = mods["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._xt_trader = mock.MagicMock()
        executor._xt_trader.order_stock.return_value = 12345

        # 总资产 100 万，委托 5 万（5% < 10% 上限）→ 放行
        with mock.patch.object(executor, "query_account", return_value={"total_assets": 1_000_000.0}):
            result = executor.send_order(code="600000.SH", side="buy", volume=5000, price=10.0)

        assert result["success"] is True
        executor._xt_trader.order_stock.assert_called_once()


# ============================================================================
# 下单频率：超限拒单
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestLiveOrderFrequency:
    """验证 live 下单频率检查（MAX_ORDER_FREQUENCY 默认 2 次/秒）。"""

    def test_frequency_exceeded_rejects_third_order(self, mods):
        """1 秒内第 3 笔订单被频率风控拒绝。"""
        xt_mod = mods["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._xt_trader = mock.MagicMock()
        executor._xt_trader.order_stock.return_value = 1

        with mock.patch.object(executor, "query_account", return_value={"total_assets": 1_000_000.0}):
            ok1 = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
            ok2 = executor.send_order(code="600001.SH", side="buy", volume=100, price=10.0)
            blocked = executor.send_order(code="600002.SH", side="buy", volume=100, price=10.0)

        assert ok1["success"] is True
        assert ok2["success"] is True
        assert blocked["success"] is False
        assert "频率" in blocked["error"]


# ============================================================================
# fail-closed：账户看不清时必须拒单
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestLiveFailClosed:
    """验证账户信息不可用时按拒单处理（不 fail-open）。"""

    @pytest.mark.parametrize("account_snapshot", [{}, {"total_assets": 0.0}])
    def test_unknown_nav_rejects_order(self, mods, account_snapshot):
        """查询账户返回空/0 时拒单——实盘不能"看不清还下单"。"""
        xt_mod = mods["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._xt_trader = mock.MagicMock()

        with mock.patch.object(executor, "query_account", return_value=account_snapshot):
            result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)

        assert result["success"] is False
        assert "硬风控拒单" in result["error"]
        executor._xt_trader.order_stock.assert_not_called()

    def test_query_account_raises_rejects_order(self, mods):
        """查询账户抛异常时同样拒单（异常不得被吞成放行）。"""
        xt_mod = mods["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._xt_trader = mock.MagicMock()

        with mock.patch.object(executor, "query_account", side_effect=RuntimeError("broker 超时")):
            result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)

        assert result["success"] is False
        assert "硬风控拒单" in result["error"]
        executor._xt_trader.order_stock.assert_not_called()


# ============================================================================
# 检查顺序：连接检查必须优先于风控
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestConnectionCheckPrecedence:
    """未连接时应返回"未连接"，而非被风控抢先拦截。"""

    def test_xtquant_not_connected_error_precedes_risk(self, mods):
        """xtquant 未连接 → 错误为"未连接"（既有测试断言依赖此语义）。"""
        xt_mod = mods["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "未连接" in result["error"]

    def test_gm_not_connected_error_precedes_risk(self, mods):
        """gm 未连接 → 错误为"未连接"。"""
        gm_mod = mods["gm"]
        executor = gm_mod.GMExecutor()
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "未连接" in result["error"]

    @pytest.mark.skipif(
        not os.path.exists(os.path.join(ADAPTERS_DIR, "tdxquant_adapter.py")),
        reason="本树无 tdxquant 适配器（9-backend 陈旧树）",
    )
    def test_tdxquant_not_connected_error_precedes_risk(self, mods):
        """tdxquant 未连接 → 错误为"未连接"。"""
        td_mod = mods["tdxquant"]
        executor = td_mod.TdxQuantExecutor()
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "未连接" in result["error"]


# ============================================================================
# paper 回归护栏：阈值 monkeypatch 必须继续生效
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestPaperThresholdMonkeypatch:
    """验证 paper 路径阈值仍可被 engine 模块全局覆盖（下沉重构的回归风险点）。"""

    def test_paper_single_order_ratio_monkeypatch_honored(self, mods, monkeypatch):
        """monkeypatch.setattr(engine, "MAX_SINGLE_ORDER_RATIO", ...) 必须生效。

        断路器下沉到 scripts/base 后，若实现改为读取 config 的构造期快照，
        该 patch 会失效——本用例即该回归的护栏。
        """
        engine = mods["engine"]
        monkeypatch.setattr(engine, "MAX_SINGLE_ORDER_RATIO", 10.0)
        executor = engine.PaperExecutor(init_capital=1000)
        executor.account.start_of_day_nav = executor.account.nav

        # 1000 本金、放宽到 10 倍 → 2000 元委托应通过风控（进而因资金不足被拒）
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=20.0)
        assert result["success"] is False
        assert "资金不足" in result["error"], "应通过风控并走到资金校验，说明阈值 patch 生效"

    def test_paper_breaker_still_blocks_without_monkeypatch(self, mods):
        """未 patch 时 paper 单笔比例检查仍按默认 10% 生效。"""
        engine = mods["engine"]
        executor = engine.PaperExecutor(init_capital=1_000_000)
        executor.account.start_of_day_nav = executor.account.nav

        # 100 万本金，委托 50 万（50% > 10%）→ 被风控拒
        result = executor.send_order(code="600000.SH", side="buy", volume=50_000, price=10.0)
        assert result["success"] is False
        assert "超过上限" in result["error"]

    def test_paper_daily_loss_check_preserved(self, mods):
        """paper 单日亏损检查仍然生效（live 无此项，属 paper 专属）。"""
        engine = mods["engine"]
        executor = engine.PaperExecutor(init_capital=1_000_000)
        # 模拟当日已亏损 5%（超过默认 2% 阈值）
        executor.account.start_of_day_nav = 1_000_000
        executor.account.available_cash = 950_000
        executor.account.nav = 950_000

        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "单日亏损" in result["error"]


# ============================================================================
# 断路器单元行为（下沉后的核心实现）
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestCircuitBreakerCore:
    """验证下沉后的 CircuitBreaker 核心判定逻辑。"""

    def test_live_check_skips_daily_loss(self, mods):
        """live 路径不传 start_of_day_nav → 不检查单日亏损，仅查比例与频率。"""
        cb = mods["circuit_breaker"].CircuitBreaker(max_single_order_ratio=0.5)
        result = cb.check_live_order(current_nav=100.0, order_value=10.0)
        assert result["allowed"] is True

    def test_live_check_blocks_oversized(self, mods):
        """live 路径单笔超限 → 拒绝。"""
        cb = mods["circuit_breaker"].CircuitBreaker(max_single_order_ratio=0.1)
        result = cb.check_live_order(current_nav=1000.0, order_value=500.0)
        assert result["allowed"] is False
        assert "超过上限" in result["reason"]

    def test_live_check_zero_nav_rejected(self, mods):
        """nav=0（看不清账户）→ 拒绝，fail-closed。"""
        cb = mods["circuit_breaker"].CircuitBreaker()
        result = cb.check_live_order(current_nav=0.0, order_value=1.0)
        assert result["allowed"] is False

    def test_paper_check_includes_daily_loss(self, mods):
        """paper 路径传入 start_of_day_nav → 单日亏损检查生效。"""
        cb = mods["circuit_breaker"].CircuitBreaker(max_daily_loss_ratio=0.02)
        result = cb._check(current_nav=95.0, order_value=1.0, start_of_day_nav=100.0)
        assert result["allowed"] is False
        assert "单日亏损" in result["reason"]

    def test_frequency_window_expiry(self, mods):
        """频率窗口 1 秒外的历史订单不计入（不会永久锁死）。"""
        cb = mods["circuit_breaker"].CircuitBreaker(max_order_frequency=1)
        assert cb._check_frequency() is True
        cb.last_order_times.append(__import__("time").time())
        assert cb._check_frequency() is False
        # 伪造一条 2 秒前的记录 → 应被窗口剔除
        cb.last_order_times = [__import__("time").time() - 2.0]
        assert cb._check_frequency() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
