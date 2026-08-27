"""data-engine 数据细项分类元数据测试（jingni-trader 数据分类对齐 阶段 1）。

阶段 1：分类元数据先行（低风险，先行落地）。验证：
- DataTypeMeta 新增 group（basic/market/financial/reference）+ instrument_types（list，默认 ["stock"]）字段
- 现有 5 类按产品口径映射填充（daily→market、financial→financial、
  capital_flow/dragon_tiger/shareholder→reference）
- 新增 11 个 P0 叶子项元数据定义（basic* 4 / market* 2 / financial* 1 / ref* 4）
- 旧键 daily/financial/capital_flow/dragon_tiger/shareholder 全部保留，method_name 不变
- group ∈ {basic,market,financial,reference}，instrument_types ⊆ {stock,index,future,option}
- INSTRUMENT_TYPES / DATA_GROUPS 集合与枚举一致
- _shared 模板副本与产品副本注册表完全一致（OPEN-2026-027 同步纪律）

设计：
- 动态导入 data_types.py，不要求联网/外部 SDK。
- 仅断言元数据静态结构；具体取数实现为 Phase 2（P0-Phase2 标注）不在本阶段覆盖。
"""

from __future__ import annotations

import os
import importlib.util as ilu

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ROOT = core/skills/jingni-tech/jingni-trader
# 仓库根 = ROOT 的上溯 4 级：tests/data_engine -> jingni-trader -> jingni-tech -> skills -> core
#
# 主目录布局（d:/codebuddy/jingni-trader）层级更浅，上溯 4 级会越界到盘符根，
# 使 _shared 路径解析成 `D:\core\ir\...\data_types.py`（不存在）。
_DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(ROOT))))


def _resolve_repo_root() -> str:
    """解析 jingni-agents 仓库根（需含 core/ir/_shared 标记目录）。

    主目录与 jingni-agents 仓库是**兄弟关系**（同在 d:/codebuddy 下），并非祖先关系，
    故单纯上溯无法命中。按序尝试：
      ① JINGNI_REPO_ROOT 环境变量
      ② 默认上溯路径（仓库副本布局 core/skills/jingni-tech/jingni-trader）
      ③ ROOT 的父目录兄弟（d:/codebuddy/jingni-agents，主目录布局）
      ④ 从 ROOT 逐级上溯查找标记目录
    """
    marker = os.path.join("core", "ir", "_shared")
    env_root = os.environ.get("JINGNI_REPO_ROOT")
    if env_root and os.path.exists(os.path.join(env_root, marker)):
        return env_root
    if os.path.exists(os.path.join(_DEFAULT_REPO_ROOT, marker)):
        return _DEFAULT_REPO_ROOT
    sibling = os.path.join(os.path.dirname(ROOT), "jingni-agents")
    if os.path.exists(os.path.join(sibling, marker)):
        return sibling
    cur = ROOT
    for _ in range(8):
        if os.path.exists(os.path.join(cur, marker)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return _DEFAULT_REPO_ROOT


REPO_ROOT = _resolve_repo_root()
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")
SHARED_DT = os.path.join(
    REPO_ROOT, "core", "ir", "_shared", "skills", "jingni-trader",
    "skills", "data-engine", "scripts", "data_types.py",
)


def _load_data_types():
    p = os.path.join(SCRIPTS_DIR, "data_types.py")
    assert os.path.exists(p), f"data_types.py not found at {p}"
    spec = ilu.spec_from_file_location("scripts.data_types", p)
    m = ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


INSTRUMENT_TYPES_EXPECTED = ["stock", "index", "future", "option"]
DATA_GROUPS_EXPECTED = ["basic", "market", "financial", "reference"]


@pytest.fixture(scope="module")
def dt_mod():
    return _load_data_types()


def test_total_entries_phase1(dt_mod):
    """阶段 1 应有 16 项：5 旧 + 11 P0 叶子项。"""
    assert len(dt_mod.DATA_TYPES) == 16


def test_instrument_types_enum():
    assert set(INSTRUMENT_TYPES_EXPECTED) <= {"stock", "index", "future", "option"}
    assert set(INSTRUMENT_TYPES_EXPECTED) == {"stock", "index", "future", "option"}


def test_data_groups_enum():
    assert set(DATA_GROUPS_EXPECTED) == {"basic", "market", "financial", "reference"}


def test_old_keys_preserved(dt_mod):
    """旧键全部保留，method_name 不变（管线零改动前提）。"""
    dt = dt_mod.DATA_TYPES
    for key, method in [
        ("daily", "get_daily"),
        ("financial", "get_financial"),
        ("capital_flow", "get_capital_flow"),
        ("dragon_tiger", "get_dragon_tiger"),
        ("shareholder", "get_shareholder"),
    ]:
        assert key in dt, f"旧键丢失: {key}"
        assert dt[key].method_name == method, f"method_name 变更: {key}"


def test_old_key_group_mapping(dt_mod):
    """现有 5 类按产品口径映射。"""
    dt = dt_mod.DATA_TYPES
    assert dt["daily"].group == "market"
    assert dt["financial"].group == "financial"
    assert dt["capital_flow"].group == "reference"
    assert dt["dragon_tiger"].group == "reference"
    assert dt["shareholder"].group == "reference"


def test_eleven_p0_leaf_items_present(dt_mod):
    """11 个 P0 叶子项全部注册且 group 正确。"""
    dt = dt_mod.DATA_TYPES
    expected = {
        "basic_stock_list": ("basic", "get_stock_list"),
        "basic_stock_info": ("basic", "get_stock_info"),
        "basic_trade_calendar": ("basic", "get_trade_calendar"),
        "basic_adjust_factor": ("basic", "get_adj_factor"),
        "market_kline": ("market", "get_kline"),
        "market_realtime": ("market", "get_realtime_quote"),
        "financial_report": ("financial", "get_financial_report"),
        "ref_dividend": ("reference", "get_dividend"),
        "ref_suspend_resume": ("reference", "get_suspend_resume"),
        "ref_locked_shares": ("reference", "get_locked_shares"),
        "ref_forecast": ("reference", "get_forecast"),
    }
    for key, (group, method) in expected.items():
        assert key in dt, f"P0 叶子项缺失: {key}"
        assert dt[key].group == group, f"group 不符: {key}"
        assert dt[key].method_name == method, f"method_name 不符: {key}"


def test_all_groups_valid(dt_mod):
    valid = {"basic", "market", "financial", "reference"}
    for key, v in dt_mod.DATA_TYPES.items():
        assert v.group in valid, f"非法 group: {key}={v.group}"


def test_all_instrument_types_valid(dt_mod):
    valid = {"stock", "index", "future", "option"}
    for key, v in dt_mod.DATA_TYPES.items():
        assert isinstance(v.instrument_types, list)
        assert v.instrument_types, f"空 instrument_types: {key}"
        for it in v.instrument_types:
            assert it in valid, f"非法 instrument_types: {key}={it}"


def test_default_instrument_types_is_stock(dt_mod):
    """未显式声明时默认含 stock。"""
    dt = dt_mod.DATA_TYPES
    for key in [
        "basic_stock_list", "basic_stock_info", "basic_adjust_factor",
        "ref_dividend", "ref_suspend_resume", "ref_locked_shares", "ref_forecast",
    ]:
        assert "stock" in dt[key].instrument_types


def test_legacy_key_map_empty(dt_mod):
    """阶段 1 无键重命名，LEGACY_KEY_MAP 应为空（旧键直接保留）。"""
    assert dt_mod.LEGACY_KEY_MAP == {}


def test_shared_copy_synced():
    """_shared 模板副本与产品副本注册表完全一致（OPEN-2026-027）。

    适用性：本校验针对「共享层镜像一致性」，要求本树部署在含 core/ir/_shared 的
    jingni-agents 仓库内。主目录（d:/codebuddy/jingni-trader）是独立仓库，结构上
    不含 core/ir/_shared——此时该校验不适用，跳过；产品层自身注册表校验（其余用例）
    仍完整执行。副本树（core/skills/jingni-tech/jingni-trader）下会真实执行。
    """
    prod = _load_data_types().DATA_TYPES
    shared_path = os.path.normpath(SHARED_DT)
    if not os.path.exists(shared_path):
        pytest.skip(
            f"本树未部署在含 core/ir/_shared 的仓库内（解析路径 {shared_path} 不存在），"
            "共享层镜像校验不适用"
        )
    spec = ilu.spec_from_file_location("shared.data_types", shared_path)
    sm = ilu.module_from_spec(spec)
    spec.loader.exec_module(sm)
    shared = sm.DATA_TYPES
    assert set(prod.keys()) == set(shared.keys()), "键集合不一致"
    for k in prod:
        pv, sv = prod[k], shared[k]
        assert (pv.group, pv.instrument_types, pv.method_name) == (
            sv.group, sv.instrument_types, sv.method_name,
        ), f"值不一致: {k}"
