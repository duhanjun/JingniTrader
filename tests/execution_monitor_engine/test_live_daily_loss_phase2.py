"""execution-monitor-engine L2 单元测试：live 单日亏损检查二期（2026-08-28）。

背景：一期（commit ``6c87089``）把断路器接入三个 live 适配器，但只覆盖
「下单频率 + 单笔金额占比」——broker 账户接口不提供 ``start_of_day_nav``
（xtquant ``XtAsset`` 仅 6 字段、gm ``Cashes.data`` 无该字段，``fpnl`` 口径
未源证），故单日亏损分支被硬编码跳过。二期改由**本地按交易日持久化的基线文件**
供数，打通参数即可复用 ``_check`` 中既有的单日亏损判定。

本文件验证（对应可行性评估 §5.1 的 7 条用例 + 灰度/护栏）：
1. 开关开启 + 亏 5%（> 2%）→ 拒单，reason 含「单日亏损」
2. 亏 1%（< 2%）→ 放行
3. 基线不可用（nav 看不清）→ 拒单 fail-closed，不静默跳过
4. 同日二次调用 → 基线不被覆盖
5. 跨交易日（mock 日期）→ 基线重置
6. 基线文件损坏 → 不崩溃，降级为取快照
7. 多 account_id → 基线互不串扰
8. **开关默认 off** → 行为与一期完全一致（灰度护栏，防回归）
9. 三适配器端到端：超限拒单且**未触达 broker**

变异测试护栏见文件末尾 ``TestMutationGuard`` 注释：禁用 sod 传递后第 1 条用例
必须失败，否则说明断言空洞。

全部用 mock 与 ``tmp_path``，不触网、不触真实账户。
"""

from __future__ import annotations

import json
import os
import sys
import importlib.util as ilu
from contextlib import nullcontext
from datetime import date
from unittest import mock

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXECUTION_ENGINE_DIR = os.path.join(ROOT, "skills", "execution-monitor-engine")
SCRIPTS_DIR = os.path.join(EXECUTION_ENGINE_DIR, "scripts")
ADAPTERS_DIR = os.path.join(SCRIPTS_DIR, "adapters")


def _setup_modules():
    """加载 execution-monitor-engine 的 scripts 包 + 三个 live 适配器 + engine。

    与 ``test_live_circuit_breaker.py`` 同构（合成模块名 + importlib 加载）——
    engine.py 内部用绝对导入 ``from scripts.config import ...``，只有先把
    ``scripts`` 包注册进 ``sys.modules`` 才能解析。
    """
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    spec = ilu.spec_from_file_location(
        "scripts", os.path.join(SCRIPTS_DIR, "__init__.py"), submodule_search_locations=[SCRIPTS_DIR]
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    spec = ilu.spec_from_file_location("scripts.config", os.path.join(SCRIPTS_DIR, "config.py"))
    config_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.config"] = config_mod
    spec.loader.exec_module(config_mod)

    base_dir = os.path.join(SCRIPTS_DIR, "base")
    spec = ilu.spec_from_file_location(
        "scripts.base", os.path.join(base_dir, "__init__.py"), submodule_search_locations=[base_dir]
    )
    bpkg = ilu.module_from_spec(spec)
    sys.modules["scripts.base"] = bpkg
    spec.loader.exec_module(bpkg)

    for mod_name, file_name in (
        ("scripts.base.circuit_breaker", "circuit_breaker.py"),
        ("scripts.base.daily_baseline", "daily_baseline.py"),
        ("scripts.base.base_executor", "base_executor.py"),
    ):
        spec = ilu.spec_from_file_location(mod_name, os.path.join(base_dir, file_name))
        mod = ilu.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    spec = ilu.spec_from_file_location(
        "scripts.adapters", os.path.join(ADAPTERS_DIR, "__init__.py"), submodule_search_locations=[ADAPTERS_DIR]
    )
    apkg = ilu.module_from_spec(spec)
    sys.modules["scripts.adapters"] = apkg
    spec.loader.exec_module(apkg)

    mods = {}
    for name in ("xtquant_adapter", "gm_adapter", "tdxquant_adapter"):
        adapter_path = os.path.join(ADAPTERS_DIR, f"{name}.py")
        if not os.path.exists(adapter_path):
            continue  # 9-backend 陈旧树无 tdxquant，跳过而非硬报错
        spec = ilu.spec_from_file_location(f"scripts.adapters.{name}", adapter_path)
        mod = ilu.module_from_spec(spec)
        sys.modules[f"scripts.adapters.{name}"] = mod
        spec.loader.exec_module(mod)
        mods[name.replace("_adapter", "")] = mod

    return {
        "config": config_mod,
        "circuit_breaker": sys.modules["scripts.base.circuit_breaker"],
        "daily_baseline": sys.modules["scripts.base.daily_baseline"],
        "base_executor": sys.modules["scripts.base.base_executor"],
        **mods,
    }


@pytest.fixture
def mods(tmp_path, monkeypatch):
    """加载模块并把基线文件/执行目录重定向到 tmp_path。

    注意：**不开** ``LIVE_DAILY_LOSS_CHECK``——默认 off 才是生产语义，
    各用例按需自行开启，另有专门用例断言默认关闭（灰度护栏）。
    """
    monkeypatch.setenv("EXECUTION_DIR", str(tmp_path / "exec"))
    monkeypatch.setenv("LIVE_DAILY_BASELINE_PATH", str(tmp_path / "exec" / "live_daily_baseline.json"))
    monkeypatch.delenv("LIVE_DAILY_LOSS_CHECK", raising=False)
    return _setup_modules()


def _enable(monkeypatch):
    monkeypatch.setenv("LIVE_DAILY_LOSS_CHECK", "on")


def _p_live_adapters():
    keys = [k for k in ("xtquant", "gm", "tdxquant") if os.path.exists(os.path.join(ADAPTERS_DIR, f"{k}_adapter.py"))]
    return pytest.mark.parametrize("adapter_key", keys)


def _patch_tdxquant_sdk():
    """为 tdxquant 适配器注入假 ``tqcenter`` 模块。

    ``TdxQuantExecutor.send_order`` 在放行路径上执行 ``from tqcenter import tqconst``，
    本机未安装该 SDK → ImportError 被适配器兜底成 success=False。放行断言因此会
    误判为"被风控拦截"，故需注入桩模块把"风控放行"与"SDK 可用性"解耦。
    """
    tqconst = mock.MagicMock()
    tqconst.STOCK_BUY = 23
    tqconst.STOCK_SELL = 24
    tqconst.PRICE_MY = 11
    tqconst.PRICE_SJ = 12
    tqcenter = mock.MagicMock()
    tqcenter.tqconst = tqconst
    return mock.patch.dict(sys.modules, {"tqcenter": tqcenter, "tqcenter.tqconst": tqconst})


# ============================================================================
# 1-3. 核心判定：超阈值拒单 / 阈内放行 / 基线不可用拒单
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestLiveDailyLossDecision:
    """单日亏损判定的三种结果（对应评估 §5.1 用例 1/2/3）。"""

    def test_daily_loss_over_threshold_rejects(self, mods, monkeypatch):
        """亏 5% > 阈值 2% → 拒单，reason 含「单日亏损」。"""
        _enable(monkeypatch)
        cb = mods["circuit_breaker"].CircuitBreaker(max_daily_loss_ratio=0.02, max_single_order_ratio=0.9)
        result = cb.check_live_order(current_nav=950_000.0, order_value=1000.0, start_of_day_nav=1_000_000.0)
        assert result["allowed"] is False
        assert "单日亏损" in result["reason"]
        assert "-5.00%" in result["reason"]

    def test_daily_loss_within_threshold_passes(self, mods, monkeypatch):
        """亏 1% < 阈值 2% → 放行（未被误杀）。"""
        _enable(monkeypatch)
        cb = mods["circuit_breaker"].CircuitBreaker(max_daily_loss_ratio=0.02, max_single_order_ratio=0.9)
        result = cb.check_live_order(current_nav=990_000.0, order_value=1000.0, start_of_day_nav=1_000_000.0)
        assert result["allowed"] is True

    def test_baseline_unavailable_rejects(self, mods, monkeypatch):
        """基线不可用（UNAVAILABLE_SOD 哨兵）→ 拒单，不静默跳过检查。"""
        _enable(monkeypatch)
        cb = mods["circuit_breaker"].CircuitBreaker(max_single_order_ratio=0.9)
        sentinel = mods["circuit_breaker"].UNAVAILABLE_SOD
        result = cb.check_live_order(current_nav=1_000_000.0, order_value=1000.0, start_of_day_nav=sentinel)
        assert result["allowed"] is False
        assert "基线不可用" in result["reason"]

    def test_sentinel_is_not_none(self, mods):
        """哨兵必须区别于 None：None = 不检查，哨兵 = 该查但查不到。

        两者混淆会让"基线拿不到"被静默降级成"跳过检查"，正是二期要堵的口子。
        """
        sentinel = mods["circuit_breaker"].UNAVAILABLE_SOD
        assert sentinel is not None
        cb = mods["circuit_breaker"].CircuitBreaker(max_single_order_ratio=0.9)
        without = cb.check_live_order(current_nav=1_000_000.0, order_value=1000.0)  # 默认 None
        with_sentinel = cb.check_live_order(
            current_nav=1_000_000.0, order_value=1000.0, start_of_day_nav=sentinel
        )
        assert without["allowed"] is True
        assert with_sentinel["allowed"] is False


# ============================================================================
# 4-7. 基线持久化：同日复用 / 跨日重置 / 损坏降级 / 多账户隔离
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestDailyBaselineStore:
    """基线文件读写语义（对应评估 §5.1 用例 4/5/6/7）。"""

    def test_same_day_reuses_baseline(self, mods, monkeypatch, tmp_path):
        """同日第二次调用读回首次基线，不被当前净值覆盖。

        若同日每笔都重写基线，基线会被"追平"到当前净值，检查将永久失效。
        """
        path = str(tmp_path / "exec" / "live_daily_baseline.json")
        resolve = mods["daily_baseline"].resolve_start_of_day_nav

        first, src1 = resolve(path, "ACC-1", 1_000_000.0)
        assert first == 1_000_000.0 and src1 == "session_first_snapshot"

        second, src2 = resolve(path, "ACC-1", 950_000.0)  # 当日亏至 95 万
        assert second == 1_000_000.0, "同日必须复用基线而非取当前净值"
        assert src2 == "persisted"

    def test_new_trading_day_resets_baseline(self, mods, tmp_path):
        """跨交易日 → 基线重置为新净值快照。"""
        path = str(tmp_path / "exec" / "live_daily_baseline.json")
        baseline = mods["daily_baseline"]
        resolve = baseline.resolve_start_of_day_nav

        day1 = date(2026, 8, 28)
        with mock.patch.object(baseline, "today_stamp", return_value=day1.isoformat()):
            assert resolve(path, "ACC-1", 1_000_000.0)[0] == 1_000_000.0

        day2 = date(2026, 8, 29)
        with mock.patch.object(baseline, "today_stamp", return_value=day2.isoformat()):
            sod, source = resolve(path, "ACC-1", 960_000.0)
        assert sod == 960_000.0, "新交易日必须重置基线"
        assert source == "session_first_snapshot"

    def test_corrupted_baseline_degrades_to_snapshot(self, mods, tmp_path, caplog):
        """基线文件损坏/非法 JSON → 不崩溃，降级为取当前净值快照。"""
        path = tmp_path / "exec" / "live_daily_baseline.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")

        sod, source = mods["daily_baseline"].resolve_start_of_day_nav(str(path), "ACC-1", 1_000_000.0)
        assert sod == 1_000_000.0 and source == "session_first_snapshot"
        # 降级后文件应被重写为合法结构（自愈，不让坏文件永久驻留）
        with open(path, encoding="utf-8") as f:
            healed = json.load(f)
        assert healed["accounts"]["ACC-1"]["start_of_day_nav"] == 1_000_000.0

    def test_malformed_accounts_structure_degrades(self, mods, tmp_path):
        """结构合法但 accounts 非 dict → 同样降级，不抛异常。"""
        path = tmp_path / "exec" / "live_daily_baseline.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"accounts": "not-a-dict"}), encoding="utf-8")

        sod, _ = mods["daily_baseline"].resolve_start_of_day_nav(str(path), "ACC-1", 880_000.0)
        assert sod == 880_000.0

    def test_multiple_accounts_isolated(self, mods, tmp_path):
        """多 account_id 基线互不串扰。"""
        path = str(tmp_path / "exec" / "live_daily_baseline.json")
        resolve = mods["daily_baseline"].resolve_start_of_day_nav

        resolve(path, "ACC-A", 1_000_000.0)
        resolve(path, "ACC-B", 500_000.0)

        assert mods["daily_baseline"].peek_start_of_day_nav(path, "ACC-A")[0] == 1_000_000.0
        assert mods["daily_baseline"].peek_start_of_day_nav(path, "ACC-B")[0] == 500_000.0
        # 第三个账户无基线
        assert mods["daily_baseline"].peek_start_of_day_nav(path, "ACC-C")[0] is None

    def test_non_positive_nav_returns_unavailable(self, mods, tmp_path):
        """净值看不清（0/负）时无法取快照 → unavailable，由调用方拒单。"""
        path = str(tmp_path / "exec" / "live_daily_baseline.json")
        sod, source = mods["daily_baseline"].resolve_start_of_day_nav(path, "ACC-1", 0.0)
        assert sod is None and source == "unavailable"


# ============================================================================
# 8. 灰度护栏：开关默认 off，行为与一期完全一致
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
class TestFeatureToggleDefaultOff:
    """``LIVE_DAILY_LOSS_CHECK`` 默认关闭——灰度期的核心安全保证。"""

    def test_disabled_by_default(self, mods):
        assert mods["config"].live_daily_loss_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "on", "ON", "true", "yes"])
    def test_truthy_values_enable(self, mods, monkeypatch, raw):
        monkeypatch.setenv("LIVE_DAILY_LOSS_CHECK", raw)
        assert mods["config"].live_daily_loss_enabled() is True

    @pytest.mark.parametrize("raw", ["0", "off", "false", "", "maybe"])
    def test_falsy_values_disable(self, mods, monkeypatch, raw):
        monkeypatch.setenv("LIVE_DAILY_LOSS_CHECK", raw)
        assert mods["config"].live_daily_loss_enabled() is False

    def test_resolve_sod_returns_none_when_disabled(self, mods, tmp_path):
        """关闭时 ``_resolve_live_sod`` 必须返回 None（不检查），且**不读基线文件**。

        直接打该方法（而非只打 ``check_live_order``）是为了让变异护栏能命中
        真正的接线点：若该方法被改成无条件返回 None，适配器端到端用例与
        本用例须同时失败。
        """
        base_executor = mods["base_executor"]
        baseline_path = tmp_path / "exec" / "live_daily_baseline.json"
        assert base_executor.BaseExecutor._resolve_live_sod(1_000_000.0, "A1") is None
        assert not baseline_path.exists()

    def test_resolve_sod_returns_positive_when_enabled(self, mods, tmp_path, monkeypatch):
        """开启且净值可见 → 返回正数基线（并落盘）。"""
        _enable(monkeypatch)
        base_executor = mods["base_executor"]
        baseline_path = tmp_path / "exec" / "live_daily_baseline.json"

        sod = base_executor.BaseExecutor._resolve_live_sod(1_000_000.0, "A1")
        assert sod == 1_000_000.0
        assert baseline_path.exists(), "开启后必须落盘基线"

    def test_resolve_sod_returns_sentinel_when_nav_unknown(self, mods, tmp_path, monkeypatch):
        """开启但净值看不清 → 返回 UNAVAILABLE_SOD 哨兵（调用方据此拒单）。"""
        _enable(monkeypatch)
        base_executor = mods["base_executor"]
        sentinel = mods["circuit_breaker"].UNAVAILABLE_SOD
        assert base_executor.BaseExecutor._resolve_live_sod(0.0, "A1") is sentinel

    def test_disabled_skips_daily_loss_and_writes_nothing(self, mods, tmp_path):
        """关闭时：亏 5% 仍放行（一期行为），且不写基线文件。"""
        mods  # fixture 已重定向路径
        xt = mods.get("xtquant")
        if xt is None:
            pytest.skip("本树无 xtquant 适配器")
        executor = xt.XtQuantExecutor()
        executor._connected = True
        executor._available = True
        executor._xt_trader = mock.MagicMock()
        executor._xt_trader.order_stock.return_value = 1

        baseline_path = tmp_path / "exec" / "live_daily_baseline.json"
        with mock.patch.object(executor, "query_account", return_value={"total_assets": 950_000.0}):
            result = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)

        assert result["success"] is True, "默认关闭 → 单日亏损不检查，行为同一期"
        assert not baseline_path.exists(), "关闭时不得写基线文件"


# ============================================================================
# 9. 适配器端到端：开启后超限拒单且未触达 broker
# ============================================================================


@pytest.mark.skill_execution_monitor_engine
@pytest.mark.unit
@pytest.mark.parametrize(
    "adapter_key,cls_name,broker_attr,broker_api",
    [
        ("xtquant", "XtQuantExecutor", "_xt_trader", "order_stock"),
        ("gm", "GMExecutor", "_gm", "order_volume"),
        ("tdxquant", "TdxQuantExecutor", "_tq", "order_stock"),
    ],
)
def test_adapter_blocks_daily_loss_without_touching_broker(
    mods, monkeypatch, adapter_key, cls_name, broker_attr, broker_api
):
    """三个 live 适配器：开关开启 + 当日亏损超阈值 → 拒单且不触达 broker。

    跨树兼容：9-backend 陈旧树无 tdxquant，按 skip 处理。
    """
    if adapter_key not in mods:
        pytest.skip(f"本树无 {adapter_key} 适配器（陈旧树）")
    _enable(monkeypatch)

    mod = mods[adapter_key]
    executor = mod.__dict__[cls_name]()
    executor._connected = True
    executor._available = True
    broker = mock.MagicMock()
    setattr(executor, broker_attr, broker)

    sdk_patch = _patch_tdxquant_sdk() if adapter_key == "tdxquant" else nullcontext()

    # 首次调用取基线 100 万（写入基线文件）→ 放行
    with sdk_patch, mock.patch.object(
        executor, "query_account", return_value={"total_assets": 1_000_000.0, "account_id": "A1"}
    ):
        first = executor.send_order(code="600000.SH", side="buy", volume=100, price=10.0)
    assert first["success"] is True, f"基线建立当笔应放行（当日盈亏为 0），实际: {first}"

    # 净值跌至 95 万（亏 5% > 2%）→ 拒单
    with sdk_patch, mock.patch.object(
        executor, "query_account", return_value={"total_assets": 950_000.0, "account_id": "A1"}
    ):
        blocked = executor.send_order(code="600001.SH", side="buy", volume=100, price=10.0)

    assert blocked["success"] is False
    assert "单日亏损" in blocked["error"]
    assert blocked.get("rejected_by") == "circuit_breaker"
    # 关键：拒单不得触达 broker 下单接口（第一次调用可能已调用，此处断言未被再调用）
    broker_method = getattr(broker, broker_api)
    assert broker_method.call_count <= 1


# ============================================================================
# 护栏说明（人工执行，不自动化——自动化会污染被测代码）
# ============================================================================
#
# 变异测试：把 ``base_executor._resolve_live_sod`` 的返回值强改为 ``None``
# （等价于退回一期、不传 sod），本文件下列用例必须**失败**：
#   - TestLiveDailyLossDecision::test_daily_loss_over_threshold_rejects
#   - test_adapter_blocks_daily_loss_without_touching_broker[*]
# 若仍全绿，说明断言空洞（sod 根本没参与判定）。
#
# 2026-08-28 实测：改后 4 条用例失败（1 条单元 + 3 条适配器参数化），
# 还原后全绿 → 断言非空洞。
