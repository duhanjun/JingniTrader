"""混合架构 LIVE 报告 + 报告门户单元测试（L2）。

覆盖混合架构核心模块：
- scripts/templates/live_polling_jsonp.py: JSONP 轮询脚本生成
- scripts/report_portal.py: manifest.json + index.html 门户页

验收标准：
- AC-H1: JSONP 轮询脚本生成合法 <script> 标签，正确引用 snapshot.js
- AC-H2: 门户页 HTML 纯静态可双击打开，正确渲染卡片
- AC-H3: manifest.json 增量更新（同类型覆盖，原子写入）
- AC-H4: LIVE 卡片通过 data-snapshot 属性标识新鲜度
- AC-H5: 跨目录相对路径正确转换
"""
from __future__ import annotations

import json
import os
import re
import sys
import importlib.util as ilu
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")


# ============================================================
# 模块加载工具
# ============================================================
def _load_jsonp_module():
    """加载 live_polling_jsonp 模块。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    # 先注册 scripts 包
    init_py = os.path.join(REPORTS_SCRIPTS, "__init__.py")
    spec = ilu.spec_from_file_location(
        "scripts", init_py,
        submodule_search_locations=[REPORTS_SCRIPTS],
    )
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    # 加载 live_polling_jsonp
    spec = ilu.spec_from_file_location(
        "scripts.templates.live_polling_jsonp",
        os.path.join(REPORTS_SCRIPTS, "templates", "live_polling_jsonp.py"),
    )
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.templates.live_polling_jsonp"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_portal_module():
    """加载 report_portal 模块（独立加载，无 scripts 依赖）。"""
    spec = ilu.spec_from_file_location(
        "report_portal",
        os.path.join(REPORTS_SCRIPTS, "report_portal.py"),
    )
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# JSONP 轮询脚本生成测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestJsonpPollingScript:
    """AC-H1: JSONP 轮询脚本生成正确性。"""

    def test_polling_script_contains_script_tag(self):
        """生成的脚本必须是 <script> 标签包裹。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_polling_script("snapshot.js", 3)
        assert "<script>" in html
        assert "</script>" in html

    def test_polling_script_references_snapshot_file(self):
        """脚本内必须引用指定的 snapshot 文件名。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_polling_script("snapshot.js", 5)
        assert "snapshot.js" in html
        # 必须有时间戳防缓存
        assert "Date.now()" in html or "ts=" in html

    def test_polling_script_uses_window_global(self):
        """脚本必须从 window.__LIVE_SNAPSHOT 读取数据。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_polling_script("snapshot.js", 3)
        assert "__LIVE_SNAPSHOT" in html

    def test_polling_interval_embedded(self):
        """轮询间隔（秒）应嵌入到脚本中。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_polling_script("snapshot.js", 7)
        # POLL_INTERVAL = 7（秒），setInterval(poll, POLL_INTERVAL * 1000)
        assert "POLL_INTERVAL = 7" in html
        assert "POLL_INTERVAL * 1000" in html

    def test_custom_snapshot_filename(self):
        """自定义 snapshot 文件名应正确传递。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_polling_script("live/snapshot.js", 3)
        assert "live/snapshot.js" in html

    def test_no_external_http_dependency(self):
        """JSONP 脚本不应依赖 fetch/XMLHttpRequest（绕过 CORS）。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_polling_script("snapshot.js", 3)
        # 不应使用 fetch API（file:// 协议下会触发 CORS）
        assert "fetch(" not in html
        assert "XMLHttpRequest" not in html


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestJsonpLiveStatusBar:
    """AC-H1: LIVE 状态条 HTML 生成。"""

    def test_status_bar_contains_lamp(self):
        """状态条必须包含状态指示灯元素。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_live_status_html(
            snapshot_file="snapshot.js",
            poll_interval=3,
            mode="live",
            backend="xtquant",
        )
        assert "status-lamp" in html
        assert "status-bar" in html

    def test_status_bar_shows_backend(self):
        """状态条应显示后端名称。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_live_status_html(
            snapshot_file="snapshot.js",
            poll_interval=3,
            mode="live",
            backend="xtquant",
        )
        assert "xtquant" in html

    def test_status_bar_contains_sync_button(self):
        """状态条应包含手动同步按钮。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_live_status_html(
            snapshot_file="snapshot.js",
            poll_interval=3,
            mode="live",
            backend="xtquant",
        )
        assert "sync-btn" in html

    def test_status_bar_includes_polling_script(self):
        """状态条 HTML 应内嵌 JSONP 轮询脚本。"""
        mod = _load_jsonp_module()
        html = mod.build_jsonp_live_status_html(
            snapshot_file="snapshot.js",
            poll_interval=3,
            mode="live",
            backend="xtquant",
        )
        # 必须包含轮询逻辑
        assert "__LIVE_SNAPSHOT" in html
        assert "setInterval" in html


# ============================================================
# 报告门户 manifest.json 测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestManifestUpsert:
    """AC-H3: manifest.json 增量更新。"""

    def test_upsert_creates_manifest_if_missing(self, tmp_path):
        """manifest.json 不存在时应创建。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="technical_report",
            file_path="technical_report.html",
            title="测试报告",
            task_id="20260805_001",
        )
        manifest_path = os.path.join(report_dir, "manifest.json")
        assert os.path.exists(manifest_path)
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["task_id"] == "20260805_001"
        assert len(manifest["reports"]) == 1
        assert manifest["reports"][0]["type"] == "technical_report"
        assert manifest["reports"][0]["title"] == "测试报告"

    def test_upsert_same_type_overwrites(self, tmp_path):
        """同类型报告应覆盖，不重复。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path="live/execution_live.html",
            title="旧报告",
        )
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path="live/execution_live.html",
            title="新报告",
        )
        with open(os.path.join(report_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        # 仅 1 条记录
        assert len(manifest["reports"]) == 1
        assert manifest["reports"][0]["title"] == "新报告"

    def test_upsert_different_types_coexist(self, tmp_path):
        """不同类型报告应共存。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="technical_report",
            file_path="technical_report.html",
        )
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path="live/execution_live.html",
            live=True,
        )
        with open(os.path.join(report_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        assert len(manifest["reports"]) == 2

    def test_upsert_converts_absolute_path_to_relative(self, tmp_path):
        """绝对路径应转换为相对路径存储。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        abs_path = os.path.join(report_dir, "live", "execution_live.html")
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path=abs_path,
        )
        with open(os.path.join(report_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        # 应存为相对路径
        assert manifest["reports"][0]["file"] == "live/execution_live.html"

    def test_upsert_preserves_live_metadata(self, tmp_path):
        """LIVE 标志和附加字段应保留。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path="live/execution_live.html",
            live=True,
            backend="xtquant",
            snapshot_file="live/snapshot.js",
        )
        with open(os.path.join(report_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        rec = manifest["reports"][0]
        assert rec["live"] is True
        assert rec["backend"] == "xtquant"
        assert rec["snapshot_file"] == "live/snapshot.js"

    def test_upsert_atomic_write(self, tmp_path):
        """写入应通过 .tmp 中转（不应残留 .tmp 文件）。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="technical_report",
            file_path="technical_report.html",
        )
        # 不应残留 .tmp 文件
        assert not os.path.exists(os.path.join(report_dir, "manifest.json.tmp"))


# ============================================================
# 报告门户 index.html 生成测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestPortalHtmlGeneration:
    """AC-H2/AC-H4: 门户页 HTML 生成。"""

    def test_generate_portal_creates_index_html(self, tmp_path):
        """generate_portal 应生成 index.html。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="technical_report",
            file_path="technical_report.html",
            title="测试报告",
        )
        index_path = mod.generate_portal(report_dir)
        assert os.path.exists(index_path)
        assert index_path.endswith("index.html")

    def test_portal_html_is_static_no_external_deps(self, tmp_path):
        """门户页应是纯静态 HTML，无外部 CDN/JS 依赖。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="technical_report",
            file_path="technical_report.html",
        )
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 不应引用外部 CDN
        assert "cdn.jsdelivr.net" not in html
        assert "unpkg.com" not in html
        assert "cdnjs.cloudflare.com" not in html

    def test_portal_renders_nav_btn_for_each_report(self, tmp_path):
        """每个报告应渲染为一个导航按钮。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "portfolio", "portfolio_report.html", title="A")
        mod.upsert_report(report_dir, "execution_live", "live/execution_live.html",
                          title="B", live=True, backend="xtquant")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 2 个导航按钮
        assert html.count('class="nav-btn') == 2
        assert "A" in html
        assert "B" in html

    def test_portal_has_iframe_for_inline_display(self, tmp_path):
        """门户页应包含 iframe，用于当前页面切换报告内容。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "portfolio", "portfolio_report.html", title="测试")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 必须有 iframe，且默认 src 指向第一份报告
        assert '<iframe id="report-frame"' in html
        assert 'src="portfolio_report.html"' in html

    def test_portal_nav_btn_has_data_src(self, tmp_path):
        """导航按钮应有 data-src 属性指向报告文件。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "portfolio", "portfolio_report.html", title="测试")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 按钮通过 data-src 携带报告路径
        assert re.search(r'data-src="portfolio_report\.html"', html)

    def test_portal_has_switch_report_function(self, tmp_path):
        """门户页应内嵌 switchReport 切换函数。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "portfolio", "portfolio_report.html")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        assert "function switchReport" in html

    def test_portal_first_btn_active_by_default(self, tmp_path):
        """第一个导航按钮应默认 active 高亮。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "portfolio", "a.html", title="A")
        mod.upsert_report(report_dir, "attribution", "b.html", title="B")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 仅 1 个 active 按钮
        assert html.count('nav-btn active') == 1

    def test_live_nav_btn_has_data_snapshot_attribute(self, tmp_path):
        """LIVE 导航按钮应包含 data-snapshot 属性供新鲜度检测。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path="live/execution_live.html",
            live=True,
            snapshot_file="live/snapshot.js",
        )
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 必须有 data-snapshot 属性
        assert "data-snapshot" in html
        assert "live/snapshot.js" in html

    def test_portal_includes_freshness_detection_script(self, tmp_path):
        """门户页应内嵌 LIVE 新鲜度检测脚本。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="execution_live",
            file_path="live/execution_live.html",
            live=True,
            snapshot_file="live/snapshot.js",
        )
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 应有 setInterval 周期检测
        assert "setInterval" in html
        assert "checkFreshness" in html

    def test_portal_atomic_write(self, tmp_path):
        """门户页写入应原子（无 .tmp 残留）。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "technical_report", "technical_report.html")
        mod.generate_portal(report_dir)
        assert not os.path.exists(os.path.join(report_dir, "index.html.tmp"))

    def test_portal_empty_manifest_renders_placeholder(self, tmp_path):
        """无报告时应渲染空状态提示。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        # 直接生成门户页（manifest 为空）
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        assert "暂无报告" in html

    def test_portal_no_anchor_links_to_report_files(self, tmp_path):
        """门户页不应使用 <a> 标签跳转，改用 iframe 内嵌切换。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(
            report_dir=report_dir,
            report_type="portfolio",
            file_path="portfolio_report.html",
        )
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 不应有 <a href="..."> 跳转链接（改为 button + iframe）
        assert not re.search(r'<a[^>]*href="portfolio_report\.html"', html)


# ============================================================
# 报告类型配置测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestReportTypeConfig:
    """报告类型显示配置正确性。"""

    def test_all_required_types_have_config(self):
        """所有报告类型都应有显示配置。"""
        mod = _load_portal_module()
        required = {
            "technical_report", "fundamental_report", "portfolio", "execution",
            "execution_live", "attribution", "backtest",
        }
        assert required.issubset(set(mod.REPORT_TYPE_CONFIG.keys()))
        # 旧合并版个股分析类型已被技术/基本面报告取代，不应再存在
        assert "stock_analysis" not in mod.REPORT_TYPE_CONFIG

    def test_config_has_label_and_icon(self):
        """配置应包含 label 和 icon 字段。"""
        mod = _load_portal_module()
        for rtype, cfg in mod.REPORT_TYPE_CONFIG.items():
            assert "label" in cfg, f"{rtype} 缺少 label"
            assert "icon" in cfg, f"{rtype} 缺少 icon"
            assert "default_title" in cfg, f"{rtype} 缺少 default_title"

    def test_execution_label_is_trading_monitor(self):
        """execution 类型的 label 应为「交易监控」。"""
        mod = _load_portal_module()
        assert mod.REPORT_TYPE_CONFIG["execution"]["label"] == "交易监控"


# ============================================================
# 门户页过滤与排序测试
# ============================================================
@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestPortalFilterAndOrder:
    """门户页仅显示交易监控/组合优化/绩效归因，按指定顺序排序。"""

    def test_portal_excludes_stock_analysis(self, tmp_path):
        """个股分析报告不应出现在门户页。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "technical_report", "technical_report.html", title="技术面")
        mod.upsert_report(report_dir, "execution_live", "live/execution_live.html", title="交易监控")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 个股分析不显示
        assert "技术面" not in html
        # 交易监控（LIVE）显示
        assert "交易监控" in html

    def test_portal_excludes_backtest(self, tmp_path):
        """回测报告不应出现在门户页。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        mod.upsert_report(report_dir, "backtest", "report.html", title="回测绩效")
        mod.upsert_report(report_dir, "portfolio", "portfolio_report.html", title="组合优化")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        assert "回测绩效" not in html
        assert "组合优化" in html

    def test_portal_order_execution_portfolio_attribution(self, tmp_path):
        """导航栏顺序应为：交易监控 → 组合优化 → 绩效归因。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        # 故意乱序注册
        mod.upsert_report(report_dir, "attribution", "a.html", title="绩效归因报告")
        mod.upsert_report(report_dir, "execution_live", "e.html", title="交易监控报告")
        mod.upsert_report(report_dir, "portfolio", "p.html", title="组合优化报告")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 验证顺序：交易监控 在 组合优化 之前，组合优化 在 绩效归因 之前
        pos_exec = html.find("交易监控报告")
        pos_port = html.find("组合优化报告")
        pos_attr = html.find("绩效归因报告")
        assert pos_exec < pos_port < pos_attr, "导航栏顺序应为 交易监控→组合优化→绩效归因"

    def test_portal_only_specified_types_displayed(self, tmp_path):
        """门户页应仅显示指定类型（交易监控/组合优化/绩效归因），排除个股分析和回测。"""
        mod = _load_portal_module()
        report_dir = str(tmp_path)
        # 注册 7 种类型
        mod.upsert_report(report_dir, "technical_report", "t.html", title="技术面")
        mod.upsert_report(report_dir, "fundamental_report", "f.html", title="基本面")
        mod.upsert_report(report_dir, "backtest", "b.html", title="回测")
        mod.upsert_report(report_dir, "execution", "e.html", title="交易监控")
        mod.upsert_report(report_dir, "execution_live", "el.html", title="交易监控报告")
        mod.upsert_report(report_dir, "portfolio", "p.html", title="组合优化")
        mod.upsert_report(report_dir, "attribution", "a.html", title="归因")
        mod.generate_portal(report_dir)
        with open(os.path.join(report_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # 仅 3 个导航按钮（execution_live + portfolio + attribution），静态 execution 已不进门户
        assert html.count('class="nav-btn') == 3
        # 个股分析、回测、静态 execution 均不显示
        assert "技术面" not in html
        assert "基本面" not in html
        assert "回测" not in html
