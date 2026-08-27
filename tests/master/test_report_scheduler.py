"""report_scheduler 定时调度器测试。

覆盖纯逻辑（不触发联网/交易）：
- 配置加载与默认值
- 下次触发时间计算（当天未到 / 已过顺延）
- Context 构造字段
- 强制刷新环境变量开关
- 绩效归因刷新的 ledger 定位（缺失时返回错误提示）
"""

import os
import sys
from datetime import datetime

import pytest

# 加载调度器模块（scripts/report_scheduler.py）。
# 注意：不用 sys.path.insert(0, SCRIPTS_DIR) 把主 scripts 目录塞到 sys.path 最前，
# 否则后续 jingni_datafeed 等子 skill 的 `from config import JingniConfig` 会错误解析
# 到主 scripts/config.py（无 JingniConfig）而 ImportError。改用 importlib 显式加载，
# 完全避免污染全局 sys.path。
import importlib.util as _ilu  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RS_PATH = os.path.join(_PROJECT_ROOT, "scripts", "report_scheduler.py")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_rs_spec = _ilu.spec_from_file_location("_report_scheduler", _RS_PATH)
rs = _ilu.module_from_spec(_rs_spec)
sys.modules["_report_scheduler"] = rs
_rs_spec.loader.exec_module(rs)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """隔离 QUANT_FORCE_REFRESH 环境变量。"""
    monkeypatch.delenv("QUANT_FORCE_REFRESH", raising=False)


class TestConfigLoading:
    def test_default_config(self):
        cfg = rs.load_config()
        assert cfg["portfolio_report"]["enabled"] is True
        assert cfg["portfolio_report"]["hour"] == 9
        assert cfg["portfolio_report"]["minute"] == 30
        assert cfg["portfolio_report"]["execute_after"] is False
        assert cfg["attribution_report"]["hour"] == 15
        assert cfg["attribution_report"]["minute"] == 0
        assert cfg["ctx"]["backend"] == "paper"

    def test_default_config_independent(self):
        """两次 load 返回独立副本，避免相互污染。"""
        c1 = rs.load_config()
        c1["portfolio_report"]["hour"] = 99
        c2 = rs.load_config()
        assert c2["portfolio_report"]["hour"] == 9


class TestNextRunTime:
    def test_today_not_reached(self):
        now = datetime(2026, 8, 5, 9, 0)
        target = rs._next_run_time(9, 30, now)
        assert target == datetime(2026, 8, 5, 9, 30)

    def test_today_passed_rollover(self):
        now = datetime(2026, 8, 5, 9, 31)
        target = rs._next_run_time(9, 30, now)
        assert target == datetime(2026, 8, 6, 9, 30)

    def test_equal_rollover(self):
        """恰好等于触发时刻则顺延到次日。"""
        now = datetime(2026, 8, 5, 15, 0)
        target = rs._next_run_time(15, 0, now)
        assert target == datetime(2026, 8, 6, 15, 0)


class TestBuildCtx:
    def test_ctx_fields(self):
        cfg = rs.load_config()
        cfg["ctx"]["stock_pool"] = ["510300.SH"]
        cfg["ctx"]["backend"] = "gm"
        ctx = rs.build_ctx(cfg)
        assert ctx.stock_pool == ["510300.SH"]
        assert ctx.metadata["backend"] == "gm"
        assert ctx.metadata["scheduler"] is True
        assert ctx.benchmark == "000300.SH"
        assert ctx.end_date  # 非空

    def test_default_start_date(self):
        ctx = rs.build_ctx(rs.load_config())
        assert ctx.start_date  # 默认回溯约 1 年


class TestForceRefresh:
    def test_set_and_clear(self):
        rs._set_force_refresh(True)
        assert os.environ.get("QUANT_FORCE_REFRESH") == "1"
        rs._set_force_refresh(False)
        assert os.environ.get("QUANT_FORCE_REFRESH") == "0"


class TestRefreshAttribution:
    def test_missing_ledger_returns_error(self, monkeypatch, tmp_path):
        """ledger 不存在时应返回明确错误，而非崩溃。"""
        cfg = rs.load_config()
        # 指向一个不存在 execution ledger 的工作目录
        monkeypatch.setattr(rs, "_work_dir", str(tmp_path / "nope"))
        result = rs.refresh_attribution(cfg)
        assert result["success"] is False
        assert "ledger" in result["error"]
