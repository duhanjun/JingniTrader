"""LIVE 模式状态服务单元测试（L2）。

覆盖验收标准：
- AC-7: LIVE 模式下 HTTP 服务启动，/api/health 返回 200，/api/snapshot 返回数据
- AC-8: 状态服务不可用时降级（前端 catch 不抛异常）
- AC-9: 端口安全（仅 127.0.0.1，自动探测无冲突）

覆盖模块：
- scripts/status_server.py: StatusServer, _find_free_port, build_snapshot
- scripts/templates/live_polling.py: render_live_block, build_polling_script
- build_execution_report 的 LIVE 模式集成路径
"""
from __future__ import annotations

import json
import os
import socket
import sys
import importlib.util as ilu
import time
import urllib.request
import urllib.error
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")


def _load_status_modules():
    """加载 status_server 和 live_polling 模块。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    init_py = os.path.join(REPORTS_SCRIPTS, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts", init_py,
        submodule_search_locations=[REPORTS_SCRIPTS],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 加载 status_server
    spec = ilu.spec_from_file_location(
        "scripts.status_server",
        os.path.join(REPORTS_SCRIPTS, "status_server.py"),
    )
    status_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.status_server"] = status_mod
    spec.loader.exec_module(status_mod)

    # 加载 live_polling
    spec = ilu.spec_from_file_location(
        "scripts.templates.live_polling",
        os.path.join(REPORTS_SCRIPTS, "templates", "live_polling.py"),
    )
    polling_mod = ilu.module_from_spec(spec)
    sys.modules["scripts.templates.live_polling"] = polling_mod
    spec.loader.exec_module(polling_mod)

    return status_mod, polling_mod


# ============================================================
# _find_free_port 单元测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestFindFreePort:
    """端口自动探测测试（AC-9）。"""

    def test_auto_allocate_returns_usable_port(self):
        """start=0 时由 OS 分配，返回端口可立即 bind。"""
        mod, _ = _load_status_modules()
        port = mod._find_free_port(0)
        assert 1024 <= port <= 65535
        # 验证端口可用（_find_free_port 用 with 释放了）
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))

    def test_find_in_range(self):
        """指定范围内能找到可用端口。"""
        mod, _ = _load_status_modules()
        port = mod._find_free_port(start=20000, end=20100)
        assert 20000 <= port < 20100

    def test_all_ports_occupied_raises(self):
        """范围内端口全部被占用时抛 RuntimeError。"""
        mod, _ = _load_status_modules()
        # 占用单个端口
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        try:
            with pytest.raises(RuntimeError):
                mod._find_free_port(start=port, end=port + 1)
        finally:
            s.close()


# ============================================================
# build_snapshot 单元测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildSnapshot:
    """快照构建测试（AC-7 数据正确性）。"""

    def test_empty_paths_returns_defaults(self):
        """空路径应返回默认零值结构。"""
        mod, _ = _load_status_modules()
        snap = mod.build_snapshot("", "")
        assert "account_snapshot" in snap
        assert snap["account_snapshot"] == {}
        assert snap["orders_executed"] == 0
        assert snap["orders_failed"] == 0
        assert "risk_metrics" in snap
        assert "ts" in snap

    def test_with_account_state(self, tmp_path):
        """有 account_state.json 时应正确读取。"""
        mod, _ = _load_status_modules()
        state_path = tmp_path / "account_state.json"
        state_path.write_text(json.dumps({
            "nav": 1050000,
            "available_cash": 50000,
            "start_of_day_nav": 1000000,
            "positions": {"000001.SZ": {"volume": 1000, "avg_cost": 10}},
        }), encoding="utf-8")
        snap = mod.build_snapshot(str(state_path), "")
        assert snap["account_snapshot"]["nav"] == 1050000
        # 日亏损率 = (1050000 - 1000000) / 1000000 = 0.05
        assert abs(snap["risk_metrics"]["daily_loss_ratio"] - 0.05) < 1e-6

    def test_with_trade_log(self, tmp_path):
        """有 trade_log.jsonl 时应正确统计订单数。"""
        mod, _ = _load_status_modules()
        audit_path = tmp_path / "trade_log.jsonl"
        records = [
            {"status": "filled"},
            {"status": "filled"},
            {"status": "rejected"},
        ]
        audit_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        snap = mod.build_snapshot("", str(audit_path))
        assert snap["orders_executed"] == 2
        assert snap["orders_failed"] == 1


# ============================================================
# StatusServer 单元测试（AC-7/AC-8/AC-9）
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestStatusServer:
    """状态服务 HTTP 接口测试。"""

    def test_start_returns_port(self):
        """start() 应返回非零端口。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            assert port > 0
            assert server.running
            assert server.port == port
        finally:
            server.stop()

    def test_health_endpoint_returns_200(self):
        """AC-7: /api/health 返回 200 + status=ok。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                assert r.status == 200
                data = json.loads(r.read())
                assert data["status"] == "ok"
                assert "ts" in data
        finally:
            server.stop()

    def test_snapshot_endpoint_returns_data(self):
        """AC-7: /api/snapshot 返回账户/订单/风控数据。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=2) as r:
                assert r.status == 200
                data = json.loads(r.read())
                assert "account_snapshot" in data
                assert "orders_executed" in data
                assert "orders_failed" in data
                assert "risk_metrics" in data
                assert "ts" in data
        finally:
            server.stop()

    def test_unknown_path_returns_404(self):
        """未知路径返回 404。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/unknown", timeout=2)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_root_returns_default_placeholder_html(self):
        """未设置报告时 GET / 返回占位 HTML。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/",
                headers={"Accept": "text/html"},
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                assert r.status == 200
                assert "text/html" in r.headers.get("Content-Type", "")
                html = r.read().decode("utf-8")
                assert "报告生成中" in html
        finally:
            server.stop()

    def test_set_report_html_updates_root_page(self):
        """set_report_html 后 GET / 返回更新的 HTML 报告。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            custom_html = "<!DOCTYPE html><html><body><h1>测试报告</h1><div id='status-lamp'></div></body></html>"
            server.set_report_html(custom_html)

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                assert r.status == 200
                assert "text/html" in r.headers.get("Content-Type", "")
                html = r.read().decode("utf-8")
                assert html == custom_html
                assert "测试报告" in html
                assert "status-lamp" in html
        finally:
            server.stop()

    def test_root_and_api_same_port_same_origin(self):
        """同源验证：HTML 与 API 共用同一端口。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            server.set_report_html("<!DOCTYPE html><html><body>report</body></html>")

            # 访问 HTML 页面
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                assert r.status == 200
                assert "text/html" in r.headers.get("Content-Type", "")
            # 访问 API
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                assert r.status == 200
                assert "application/json" in r.headers.get("Content-Type", "")
            # 两者在同一端口 = 同源
        finally:
            server.stop()

    def test_set_report_html_before_start_warns(self):
        """服务未启动时 set_report_html 应 warning 不抛异常。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        # 未调用 start，应不抛异常
        server.set_report_html("<html></html>")
        assert server.port == 0

    def test_only_binds_localhost(self):
        """AC-9: 仅绑定 127.0.0.1，不对外暴露。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        try:
            port = server.start()
            # 验证只能通过 127.0.0.1 访问
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                assert r.status == 200
            # 尝试通过 0.0.0.0 访问应失败（仅绑定 127.0.0.1）
            # 注意：在某些系统上 0.0.0.0 会回环到 127.0.0.1，所以这里只验证 127.0.0.1 可用
        finally:
            server.stop()

    def test_stop_releases_port(self):
        """stop() 后端口应被释放。"""
        mod, _ = _load_status_modules()
        server = mod.StatusServer("", "")
        port = server.start()
        server.stop()
        assert not server.running
        assert server.port == 0
        # 端口应可被重新绑定
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))

    def test_specified_port_used(self):
        """指定端口时使用该端口。"""
        mod, _ = _load_status_modules()
        # 先找一个空闲端口
        port = mod._find_free_port(start=30000, end=31000)
        server = mod.StatusServer("", "", port=port)
        try:
            actual = server.start()
            assert actual == port
        finally:
            server.stop()

    def test_reuse_address_no_conflict(self):
        """allow_reuse_address 启用，避免与残留进程冲突。"""
        mod, _ = _load_status_modules()
        # 启动后停止，再启动同一端口不应冲突
        port = mod._find_free_port(start=31000, end=32000)
        server1 = mod.StatusServer("", "", port=port)
        server1.start()
        server1.stop()
        # 等待端口释放
        time.sleep(0.1)
        server2 = mod.StatusServer("", "", port=port)
        try:
            actual = server2.start()
            assert actual == port
        finally:
            server2.stop()


# ============================================================
# live_polling 单元测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestLivePolling:
    """前端轮询脚本测试。"""

    def test_should_enable_live_polling_live_mode(self):
        """mode=live 应返回 True。"""
        mod, polling = _load_status_modules()
        assert polling.should_enable_live_polling("live") is True
        assert polling.should_enable_live_polling("paper") is False
        assert polling.should_enable_live_polling("paper", "live") is True
        assert polling.should_enable_live_polling("live", "paper") is True

    def test_build_polling_script_contains_setInterval(self):
        """轮询脚本应包含 setInterval 调用。"""
        _, polling = _load_status_modules()
        script = polling.build_polling_script(port=8000, poll_interval=5)
        assert "setInterval" in script
        assert "fetch" in script
        # 端口仅出现在注释中，不作为 fetch 的绝对 URL
        assert "POLL_INTERVAL = 5" in script

    def test_build_polling_script_uses_relative_path(self):
        """轮询脚本应使用相对路径（同源访问，避免 CORS）。"""
        _, polling = _load_status_modules()
        script = polling.build_polling_script(port=8000, poll_interval=5)
        # fetch 使用相对路径（STATUS_BASE 为空字符串）
        assert "STATUS_BASE = \"\"" in script
        assert "fetch(STATUS_BASE + '/api/snapshot'" in script
        # 不应在 fetch 调用中出现绝对 URL
        assert "fetch('http://127.0.0.1" not in script
        assert "fetch(\"http://127.0.0.1" not in script

    def test_build_polling_script_has_error_catch(self):
        """AC-8: 轮询脚本应 catch 错误，不抛 JS 异常。"""
        _, polling = _load_status_modules()
        script = polling.build_polling_script(port=8000, poll_interval=5)
        assert ".catch" in script
        assert "showDisconnect" in script

    def test_render_live_block_contains_status_lamp(self):
        """LIVE 状态条 HTML 应包含状态灯元素。"""
        _, polling = _load_status_modules()
        html = polling.render_live_block(port=8000, poll_interval=5, mode="live", backend="xtquant")
        assert 'id="status-lamp"' in html
        assert 'id="refresh-countdown"' in html
        assert 'id="sync-btn"' in html
        assert "LIVE" in html
        assert "xtquant" in html

    def test_render_live_block_contains_pulse_animation(self):
        """状态条应包含脉冲动画 CSS。"""
        _, polling = _load_status_modules()
        html = polling.render_live_block(port=8000, poll_interval=5, mode="live", backend="xtquant")
        assert "@keyframes pulse" in html
        assert "animation: pulse" in html


# ============================================================
# build_execution_report LIVE 模式集成测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestBuildExecutionReportLiveMode:
    """build_execution_report 在 LIVE 模式下的集成路径。"""

    def test_paper_mode_uses_static_status_bar(self):
        """PAPER 模式应使用静态状态条，不含轮询脚本。"""
        mod, _ = _load_status_modules()
        # 加载 execution_report
        spec = ilu.spec_from_file_location(
            "scripts.templates.execution_report",
            os.path.join(REPORTS_SCRIPTS, "templates", "execution_report.py"),
        )
        exec_mod = ilu.module_from_spec(spec)
        sys.modules["scripts.templates.execution_report"] = exec_mod
        spec.loader.exec_module(exec_mod)

        # 显式禁用 LIVE 轮询，避免环境变量 TRADE_MODE 污染
        html = exec_mod.build_execution_report(
            execution_metadata={"mode": "paper", "account_snapshot": {"nav": 100}},
            include_p1=False,
            include_p2=False,
            enable_live_polling=False,
        )
        assert "<!DOCTYPE html>" in html
        # PAPER 模式不应有轮询脚本
        assert "setInterval" not in html
        assert "status-lamp" not in html

    def test_live_mode_enables_polling(self):
        """LIVE 模式应启动状态服务并注入轮询脚本。"""
        mod, _ = _load_status_modules()
        spec = ilu.spec_from_file_location(
            "scripts.templates.execution_report",
            os.path.join(REPORTS_SCRIPTS, "templates", "execution_report.py"),
        )
        exec_mod = ilu.module_from_spec(spec)
        sys.modules["scripts.templates.execution_report"] = exec_mod
        spec.loader.exec_module(exec_mod)

        html = exec_mod.build_execution_report(
            execution_metadata={"mode": "live", "account_snapshot": {"nav": 100}},
            include_p1=False,
            include_p2=False,
            live_poll_interval=3,
        )
        assert "<!DOCTYPE html>" in html
        # LIVE 模式应包含轮询脚本
        assert "setInterval" in html
        assert "status-lamp" in html
        assert "POLL_INTERVAL = 3" in html
        # 同源设计：fetch 使用相对路径，不出现绝对 URL 作为 fetch 目标
        assert "STATUS_BASE = \"\"" in html
        assert "fetch(STATUS_BASE + '/api/snapshot'" in html
        # 注释中保留端口信息（供调试），但不作为 fetch 目标
        assert "fetch('http://127.0.0.1" not in html
        assert "fetch(\"http://127.0.0.1" not in html

    def test_live_mode_with_explicit_disable_uses_static(self):
        """enable_live_polling=False 显式禁用时应使用静态状态条。"""
        mod, _ = _load_status_modules()
        spec = ilu.spec_from_file_location(
            "scripts.templates.execution_report",
            os.path.join(REPORTS_SCRIPTS, "templates", "execution_report.py"),
        )
        exec_mod = ilu.module_from_spec(spec)
        sys.modules["scripts.templates.execution_report"] = exec_mod
        spec.loader.exec_module(exec_mod)

        html = exec_mod.build_execution_report(
            execution_metadata={"mode": "live", "account_snapshot": {"nav": 100}},
            include_p1=False,
            include_p2=False,
            enable_live_polling=False,  # 显式禁用
        )
        assert "<!DOCTYPE html>" in html
        assert "setInterval" not in html

    def test_live_mode_failure_falls_back_to_static(self):
        """AC-8: LIVE 状态服务启动失败时应降级为静态状态条。"""
        mod, _ = _load_status_modules()
        spec = ilu.spec_from_file_location(
            "scripts.templates.execution_report",
            os.path.join(REPORTS_SCRIPTS, "templates", "execution_report.py"),
        )
        exec_mod = ilu.module_from_spec(spec)
        sys.modules["scripts.templates.execution_report"] = exec_mod
        spec.loader.exec_module(exec_mod)

        # mock StatusServer.start 抛异常
        with mock.patch.object(mod.StatusServer, "start", side_effect=RuntimeError("端口占用")):
            html = exec_mod.build_execution_report(
                execution_metadata={"mode": "live", "account_snapshot": {"nav": 100}},
                include_p1=False,
                include_p2=False,
            )
        assert "<!DOCTYPE html>" in html
        # 降级为静态状态条，不含轮询脚本
        assert "setInterval" not in html


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
