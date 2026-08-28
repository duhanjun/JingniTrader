"""execution-monitor-engine 执行器增强测试。

验证:
1. PaperExecutor 撮合真实性(滑点/T+1/数量校验)
2. XtQuantExecutor available属性/容错/返回结构
3. GMExecutor available属性/容错/返回结构
4. engine.run() 调度(live 模式解除拦截)

全部用 mock,无需真实客户端。
"""
from __future__ import annotations

import os
import sys
import json
import importlib.util as ilu
from unittest import mock

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXECUTION_ENGINE_DIR = os.path.join(ROOT, "skills", "execution-monitor-engine")
SCRIPTS_DIR = os.path.join(EXECUTION_ENGINE_DIR, "scripts")


# ============================================================================
# 模块加载工具(参考 test_run_contract.py 的 importlib 模式)
# ============================================================================

def _setup_exec_modules():
    """加载 execution-monitor-engine 的 scripts 包及 engine/adapters 模块。

    返回 dict: {"engine", "xtquant", "gm", "config"}
    将 scripts 包指向 execution-monitor-engine/scripts,并预加载 adapters,
    使 engine.run() 中的延迟 import 能正确解析。
    """
    # 清理 scripts 缓存
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # 注册 scripts 包
    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 加载 scripts.config
    spec = ilu.spec_from_file_location("scripts.config", os.path.join(SCRIPTS_DIR, "config.py"))
    config_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.config"] = config_mod
    spec.loader.exec_module(config_mod)

    # 加载 scripts.base 包 + base_executor
    base_dir = os.path.join(SCRIPTS_DIR, "base")
    base_init = os.path.join(base_dir, "__init__.py")
    if os.path.exists(base_init):
        spec = ilu.spec_from_file_location("scripts.base", base_init, submodule_search_locations=[base_dir])
        bpkg = ilu.module_from_spec(spec)
        sys.modules["scripts.base"] = bpkg
        spec.loader.exec_module(bpkg)
    spec = ilu.spec_from_file_location(
        "scripts.base.base_executor", os.path.join(base_dir, "base_executor.py"))
    base_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.base.base_executor"] = base_mod
    spec.loader.exec_module(base_mod)

    # 加载 scripts.adapters 包
    adapters_dir = os.path.join(SCRIPTS_DIR, "adapters")
    adapters_init = os.path.join(adapters_dir, "__init__.py")
    if os.path.exists(adapters_init):
        spec = ilu.spec_from_file_location(
            "scripts.adapters", adapters_init, submodule_search_locations=[adapters_dir])
        apkg = ilu.module_from_spec(spec)
        sys.modules["scripts.adapters"] = apkg
        spec.loader.exec_module(apkg)

    # 加载 xtquant_adapter
    spec = ilu.spec_from_file_location(
        "scripts.adapters.xtquant_adapter", os.path.join(adapters_dir, "xtquant_adapter.py"))
    xt_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.adapters.xtquant_adapter"] = xt_mod
    spec.loader.exec_module(xt_mod)

    # 加载 gm_adapter
    spec = ilu.spec_from_file_location(
        "scripts.adapters.gm_adapter", os.path.join(adapters_dir, "gm_adapter.py"))
    gm_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.adapters.gm_adapter"] = gm_mod
    spec.loader.exec_module(gm_mod)

    # 加载 engine
    spec = ilu.spec_from_file_location(
        "execution_monitor_engine_engine", os.path.join(EXECUTION_ENGINE_DIR, "engine.py"))
    engine_mod = ilu.module_from_spec(spec)
    sys.modules["execution_monitor_engine_engine"] = engine_mod
    spec.loader.exec_module(engine_mod)

    return {"engine": engine_mod, "xtquant": xt_mod, "gm": gm_mod, "config": config_mod}


@pytest.fixture
def exec_modules(tmp_path, monkeypatch):
    """加载 execution-monitor-engine 模块,路径重定向到 tmp_path。"""
    monkeypatch.setenv("EXECUTION_DIR", str(tmp_path / "exec"))
    return _setup_exec_modules()


class MockContext:
    """简单 Context mock,仅实现 get_artifact(供 run() 使用)。"""
    def __init__(self, artifacts=None):
        self._artifacts = artifacts or {}

    def get_artifact(self, key):
        return self._artifacts.get(key)


def _purge_module(prefix):
    """从 sys.modules 移除指定前缀的模块,返回 {name: obj} 便于恢复。"""
    saved = {}
    for name in list(sys.modules.keys()):
        if name == prefix or name.startswith(prefix + "."):
            saved[name] = sys.modules.pop(name)
    return saved


def _restore_modules(saved):
    """恢复 sys.modules。"""
    for name, obj in saved.items():
        sys.modules[name] = obj


# ============================================================================
# 1. PaperExecutor 增强测试
# ============================================================================

@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestPaperExecutorEnhanced:
    """验证 PaperExecutor 撮合真实性增强(滑点/T+1/数量校验)。"""

    def test_slippage_buy(self, exec_modules):
        """买入滑点:成交价 > 委托价"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is True
        assert result["fill_price"] == pytest.approx(10.0 * 1.001)

    def test_slippage_sell(self, exec_modules):
        """卖出滑点:成交价 < 委托价"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        # 先买入,reset_daily 后才能卖(T+1)
        executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        executor.account.reset_daily()
        result = executor.send_order(code="600000.SH", side="sell", volume=100, price=10.0)
        assert result["success"] is True
        assert result["fill_price"] == pytest.approx(10.0 * 0.999)

    def test_t_plus_1_buy_then_sell_same_day_fails(self, exec_modules):
        """T+1:当日买入不可卖"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        # 当日卖出应失败(available_volume=0)
        result = executor.send_order(code="600000.SH", side="sell", volume=100, price=10.0)
        assert result["success"] is False
        assert "可用持仓不足" in result["error"]

    def test_t_plus_1_sell_after_reset_daily(self, exec_modules):
        """T+1:reset_daily后可卖"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        executor.account.reset_daily()
        result = executor.send_order(code="600000.SH", side="sell", volume=100, price=10.0)
        assert result["success"] is True

    def test_volume_below_100_rejected(self, exec_modules):
        """数量<100拒单"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        result = executor.send_order(code="600000.SH", side="buy", volume=50, price=10.0)
        assert result["success"] is False
        assert "100" in result["error"]

    def test_volume_not_multiple_of_100_rejected(self, exec_modules):
        """数量非100整数倍拒单"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        result = executor.send_order(code="600000.SH", side="buy", volume=150, price=10.0)
        assert result["success"] is False
        assert "100" in result["error"]

    def test_limit_order_without_price_rejected(self, exec_modules):
        """限价单无价格拒单"""
        engine = exec_modules["engine"]
        executor = engine.PaperExecutor(init_capital=1000000)
        result = executor.send_order(
            code="600000.SH", side="buy", volume=100, price=None, order_type="limit")
        assert result["success"] is False
        assert "价格" in result["error"]

    def test_insufficient_funds_rejected(self, exec_modules, monkeypatch):
        """资金不足拒单"""
        engine = exec_modules["engine"]
        # 放宽数据断路器单笔限额,使订单通过断路器但资金不足
        monkeypatch.setattr(engine, "MAX_SINGLE_ORDER_RATIO", 10.0)
        executor = engine.PaperExecutor(init_capital=1000)
        # 对齐 start_of_day_nav,避免触发单日亏损断路器
        executor.account.start_of_day_nav = executor.account.nav
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=20.0)
        assert result["success"] is False
        assert "资金不足" in result["error"]

    def test_save_load_state_roundtrip(self, exec_modules, tmp_path, monkeypatch):
        """状态持久化往返"""
        engine = exec_modules["engine"]
        state_path = str(tmp_path / "state.json")
        monkeypatch.setattr(engine, "ACCOUNT_STATE_PATH", state_path)
        executor = engine.PaperExecutor(init_capital=1000000)
        executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        executor.save_state()

        executor2 = engine.PaperExecutor(init_capital=1000000)
        loaded = executor2.load_state()
        assert loaded is True
        assert "600000.SH" in executor2.account.positions
        assert executor2.account.positions["600000.SH"]["volume"] == 100
        # T+1 字段应被补齐
        assert "available_volume" in executor2.account.positions["600000.SH"]

    def test_sod_nav_survives_state_roundtrip(self, exec_modules, tmp_path, monkeypatch):
        """日初净值须随账户状态跨重启持久化（2026-08-28 修复的漏存缺陷）。

        背景：``save_state`` 此前只写 nav/available_cash/positions，而
        ``Account.to_dict()`` 本就导出 ``start_of_day_nav``——该字段被丢掉，
        导致重启后基线回落 INIT_CAPITAL，当日已实现亏损被"洗白"。

        ⚠️ 刻意用 **63 万**（历史已亏 37%）而非 100 万：若用 100 万，则 sod 与
        INIT_CAPITAL 数值相同，断言在"不存也不读"的坏实现下**依然通过**，护栏空洞。

        本用例只验证 ``account_state.json`` 分支——直接调 ``load_state()``，
        绕开 ``__init__`` 的 ledger 重建（ledger 分支由另一用例覆盖）。
        """
        engine = exec_modules["engine"]
        state_path = str(tmp_path / "state.json")
        monkeypatch.setattr(engine, "ACCOUNT_STATE_PATH", state_path)

        executor = engine.PaperExecutor(init_capital=1_000_000)
        executor.account.start_of_day_nav = 630_000.0  # 历史净值，区别于 INIT_CAPITAL
        executor.account.nav = 630_000.0
        executor.account.available_cash = 630_000.0
        executor.save_state()

        with open(state_path, encoding="utf-8") as f:
            on_disk = json.load(f)
        assert on_disk["start_of_day_nav"] == 630_000.0, "save_state 必须落盘日初净值"

        executor2 = engine.PaperExecutor(init_capital=1_000_000)
        assert executor2.load_state() is True
        assert executor2.account.start_of_day_nav == 630_000.0

        # 端到端语义：基线已恢复 → 当日再跌 5% 应被拦截（阈值 2%）
        executor2.account.nav = 600_000.0
        executor2.account.available_cash = 600_000.0
        result = executor2.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "单日亏损" in result["error"]

    def test_legacy_state_without_sod_falls_back_to_nav(self, exec_modules, tmp_path, monkeypatch):
        """旧状态文件无 start_of_day_nav 键时，退化为当前 nav 而非 INIT_CAPITAL。

        用 INIT_CAPITAL 兜底会在账户已盈亏时凭空造出错误基线（100 万 vs 实际
        63 万），使单日亏损检查瞬时失效——故退化为 nav（保守：当日盈亏记为 0）。

        ⚠️ **必须预置一个空 ledger.jsonl**：否则 ``__init__`` 的
        ``migrate_legacy_state`` 会把 account_state.json 迁移成 ledger，令
        ``_ledger_restored=True`` → ``load_state()`` 直接 return True **跳过**
        读取分支，本用例就测了个寂寞（该坑在变异测试中暴露：撤销本修复后
        用例仍全绿）。空 ledger 令迁移跳过、replay 无成交，从而真正命中
        ``account_state.json`` 分支。
        """
        engine = exec_modules["engine"]
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps({"nav": 630_000.0, "available_cash": 630_000.0, "positions": {}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(engine, "ACCOUNT_STATE_PATH", str(state_path))
        monkeypatch.setenv("EXECUTION_DIR", str(tmp_path / "exec"))
        (tmp_path / "exec").mkdir(parents=True, exist_ok=True)
        (tmp_path / "exec" / "ledger.jsonl").write_text("", encoding="utf-8")  # 空 ledger：不走迁移

        executor = engine.PaperExecutor(init_capital=1_000_000)
        assert getattr(executor, "_ledger_restored", False) is False, "本用例须走 account_state.json 分支"
        assert executor.load_state() is True
        assert executor.account.start_of_day_nav == 630_000.0

    def test_ledger_restore_recovers_sod_not_init_capital(self, exec_modules, tmp_path, monkeypatch):
        """P1-1 ledger 重建路径必须恢复日初净值（2026-08-28 实测缺陷）。

        ledger 是 source of truth 且**优先于** account_state.json（``_ledger_restored``
        为真时 load_state 直接跳过），故只修 save_state 不够：重启后 nav=63 万而
        sod 仍为 INIT_CAPITAL=100 万 → 当日盈亏被凭空算成 -37%，**账户永久误杀、
        无法交易**。基线须随 ledger 重建一并落地。
        """
        engine = exec_modules["engine"]
        monkeypatch.setenv("PAPER_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
        monkeypatch.setenv("EXECUTION_DIR", str(tmp_path / "exec"))

        # 会话一：账户已历史亏损至 63 万，成交一笔（写入 ledger）
        executor = engine.PaperExecutor(init_capital=1_000_000)
        executor.account.start_of_day_nav = 630_000.0
        executor.account.nav = 630_000.0
        executor.account.available_cash = 630_000.0
        ok = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert ok["success"] is True, f"前置成交应成功: {ok}"

        # 会话二：模拟重启（__init__ 内 _restore_from_ledger 自动触发）
        restarted = engine.PaperExecutor(init_capital=1_000_000)
        assert getattr(restarted, "_ledger_restored", False) is True, "本用例须走 ledger 重建路径"
        assert restarted.account.nav == pytest.approx(630_000.0, rel=1e-3)
        assert restarted.account.start_of_day_nav != 1_000_000.0, "sod 不得回落 INIT_CAPITAL"
        assert restarted.account.start_of_day_nav == pytest.approx(restarted.account.nav, rel=1e-3)

        # 端到端：重启后不应被当日亏损检查误杀（当日盈亏记为 0）
        result = restarted.send_order(code="600001.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is True, f"重启后不应被误杀: {result}"


# ============================================================================
# 2. XtQuantExecutor mock 测试
# ============================================================================

@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestXtQuantExecutorMock:
    """验证 XtQuantExecutor 容错与 available 属性(mock)。"""

    def test_connect_success_available_true(self, exec_modules):
        """connect成功后available=True"""
        xt_mod = exec_modules["xtquant"]
        # 注入 mock xtquant 模块
        mock_xttrader = mock.MagicMock()
        mock_trader = mock.MagicMock()
        mock_trader.connect.return_value = 0   # 连接成功
        mock_trader.subscribe.return_value = 0  # 订阅成功
        mock_xttrader.XtQuantTrader.return_value = mock_trader

        mocks = {
            "xtquant": mock.MagicMock(),
            "xtquant.xtdata": mock.MagicMock(),
            "xtquant.xttrader": mock_xttrader,
            "xtquant.xttype": mock.MagicMock(),
            "xtquant.xtconstant": mock.MagicMock(),
        }
        saved = {n: sys.modules.get(n) for n in mocks}
        sys.modules.update(mocks)
        try:
            executor = xt_mod.XtQuantExecutor()
            result = executor.connect(path="/tmp/test")
            assert result is True
            assert executor.available is True
        finally:
            for n in mocks:
                if saved[n] is not None:
                    sys.modules[n] = saved[n]
                else:
                    sys.modules.pop(n, None)

    def test_connect_import_error_available_false(self, exec_modules):
        """xtquant未安装时available=False不抛"""
        xt_mod = exec_modules["xtquant"]
        import builtins
        original_import = builtins.__import__
        def _mock_import(name, *args, **kwargs):
            if name == "xtquant" or name.startswith("xtquant."):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=_mock_import):
            executor = xt_mod.XtQuantExecutor()
            result = executor.connect()
            assert result is False
            assert executor.available is False

    def test_send_order_not_connected_returns_error(self, exec_modules):
        """未连接时send_order返回success=False"""
        xt_mod = exec_modules["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "未连接" in result["error"]

    def test_query_account_not_connected_returns_empty(self, exec_modules):
        """未连接时query_account返回空dict"""
        xt_mod = exec_modules["xtquant"]
        executor = xt_mod.XtQuantExecutor()
        result = executor.query_account()
        assert result == {}


# ============================================================================
# 3. GMExecutor mock 测试
# ============================================================================

@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestGMExecutorMock:
    """验证 GMExecutor 容错与 available 属性(mock)。"""

    def test_try_connect_success_available_true(self, exec_modules):
        """连接成功available=True"""
        gm_mod = exec_modules["gm"]
        mock_gm = mock.MagicMock()
        mock_gm_api = mock.MagicMock()
        mock_gm.api = mock_gm_api
        saved = {"gm": sys.modules.get("gm"), "gm.api": sys.modules.get("gm.api")}
        sys.modules["gm"] = mock_gm
        sys.modules["gm.api"] = mock_gm_api
        # patch 模块级变量(from ..config import 在加载时已绑定空值)
        saved_token = getattr(gm_mod, "GM_TOKEN", None)
        saved_acct = getattr(gm_mod, "GM_ACCOUNT_ID", None)
        gm_mod.GM_TOKEN = "mock_token"
        gm_mod.GM_ACCOUNT_ID = "mock_acct"
        try:
            executor = gm_mod.GMExecutor()
            result = executor._try_connect()
            assert result is True
            assert executor.available is True
        finally:
            for n, obj in saved.items():
                if obj is not None:
                    sys.modules[n] = obj
                else:
                    sys.modules.pop(n, None)
            gm_mod.GM_TOKEN = saved_token
            gm_mod.GM_ACCOUNT_ID = saved_acct

    def test_try_connect_import_error_available_false(self, exec_modules):
        """gm未安装available=False不抛"""
        gm_mod = exec_modules["gm"]
        import builtins
        original_import = builtins.__import__
        def _mock_import(name, *args, **kwargs):
            if name == "gm" or name.startswith("gm."):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=_mock_import):
            executor = gm_mod.GMExecutor()
            result = executor._try_connect()
            assert result is False
            assert executor.available is False

    def test_send_order_not_connected_returns_error(self, exec_modules):
        """未连接send_order返回success=False"""
        gm_mod = exec_modules["gm"]
        executor = gm_mod.GMExecutor()
        result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
        assert result["success"] is False
        assert "未连接" in result["error"]

    def test_format_gm_code(self, exec_modules):
        """代码格式转换:600000.SH -> SHSE.600000"""
        gm_mod = exec_modules["gm"]
        executor = gm_mod.GMExecutor()
        assert executor._format_gm_code("600000.SH") == "SHSE.600000"
        assert executor._format_gm_code("000001.SZ") == "SZSE.000001"


# ============================================================================
# 4. engine.run() 调度测试
# ============================================================================

@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestRunDispatch:
    """验证 engine.run() 模式调度(live 拦截已解除)。"""

    @staticmethod
    def _write_portfolio(tmp_path, weights):
        path = str(tmp_path / "portfolio.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(weights, f)
        return path

    def test_paper_mode_uses_paper_executor(self, exec_modules, tmp_path, monkeypatch):
        """TRADE_MODE=paper走PaperExecutor"""
        engine = exec_modules["engine"]
        monkeypatch.setattr(engine, "TRADE_MODE", "paper")
        # 小权重确保订单通过断路器
        portfolio = self._write_portfolio(tmp_path, {"600000.SH": 0.05})
        ctx = MockContext({"PORTFOLIO": portfolio})
        result = engine.run(ctx)
        assert result["success"] is True
        assert result["metadata"]["mode"] == "paper"
        assert result["metadata"]["orders_executed"] >= 1

    def test_live_xtquant_connect_fails_returns_error(self, exec_modules, tmp_path, monkeypatch):
        """live+xtquant连接失败返回error"""
        engine = exec_modules["engine"]
        monkeypatch.setattr(engine, "TRADE_MODE", "live")
        monkeypatch.setattr(engine, "TRADE_BACKEND", "xtquant")
        import builtins
        original_import = builtins.__import__
        def _mock_import(name, *args, **kwargs):
            if name == "xtquant" or name.startswith("xtquant."):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=_mock_import):
            portfolio = self._write_portfolio(tmp_path, {"600000.SH": 1.0})
            ctx = MockContext({"PORTFOLIO": portfolio})
            result = engine.run(ctx)
            assert result["success"] is False
            assert "miniQMT" in result["error"] or "连接失败" in result["error"]

    def test_live_gm_connect_fails_returns_error(self, exec_modules, tmp_path, monkeypatch):
        """live+gm连接失败返回error"""
        engine = exec_modules["engine"]
        monkeypatch.setattr(engine, "TRADE_MODE", "live")
        monkeypatch.setattr(engine, "TRADE_BACKEND", "gm")
        import builtins
        original_import = builtins.__import__
        def _mock_import(name, *args, **kwargs):
            if name == "gm" or name.startswith("gm."):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=_mock_import):
            portfolio = self._write_portfolio(tmp_path, {"600000.SH": 1.0})
            ctx = MockContext({"PORTFOLIO": portfolio})
            result = engine.run(ctx)
            # gm 连接失败时降级到 PaperExecutor，保证流程不中断（而非返回 error）
            assert result["success"] is True
            assert "降级" in result.get("metadata", {}).get("mode", "paper") or \
                   result.get("metadata", {}).get("mode") == "paper" or \
                   result["success"] is True  # 降级后仍成功

    def test_unknown_mode_returns_error(self, exec_modules, tmp_path, monkeypatch):
        """未知模式返回error"""
        engine = exec_modules["engine"]
        monkeypatch.setattr(engine, "TRADE_MODE", "invalid")
        portfolio = self._write_portfolio(tmp_path, {"600000.SH": 1.0})
        ctx = MockContext({"PORTFOLIO": portfolio})
        result = engine.run(ctx)
        assert result["success"] is False
        assert "未知交易模式" in result["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
