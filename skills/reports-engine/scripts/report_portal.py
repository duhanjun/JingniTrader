"""报告门户与清单管理

系统化管理多份 HTML 报告：
- manifest.json：记录所有报告的元数据（类型/标题/生成时间/LIVE标志等）
- index.html：门户页，扫描 manifest.json 渲染导航卡片，双击即看

设计要点：
- 门户页纯静态 HTML+JS，零外部依赖，file:// 协议直接打开
- manifest.json 增量更新：每次生成报告时调用 upsert_report 追加/覆盖
- LIVE 报告卡片显示绿点（数据新鲜）/灰点（无实时数据），通过检测 snapshot.js
  的修改时间判断（门户页 JS 轮询）
- 跨目录兼容：manifest.json 中的 file 字段使用相对路径（相对于 reports/ 目录）

manifest.json 结构示例：
{
  "task_id": "20260805_001",
  "generated_at": "2026-08-05T10:00:00",
  "reports": [
    {
      "type": "technical_report",
      "file": "technical_report.html",
      "title": "比亚迪技术分析",
      "generated_at": "2026-08-05T10:00:00",
      "live": false,
      "metadata": {"stock": "002594.SZ", "score": 85}
    },
    {
      "type": "execution_live",
      "file": "live/execution_live.html",
      "title": "执行监控-LIVE",
      "generated_at": "2026-08-05T10:00:00",
      "live": true,
      "backend": "xtquant",
      "snapshot_file": "live/snapshot.js"
    }
  ]
}
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("report-portal")


# 报告类型显示配置（类型 → 中文名 + 图标 emoji + 默认标题）
REPORT_TYPE_CONFIG: Dict[str, Dict[str, str]] = {
    "technical_report": {"label": "技术分析", "icon": "📈", "default_title": "技术分析报告"},
    "fundamental_report": {"label": "基本面分析", "icon": "🏢", "default_title": "基本面分析报告"},
    "portfolio": {"label": "组合优化", "icon": "⚖️", "default_title": "组合优化报告"},
    "execution": {"label": "交易监控", "icon": "🎯", "default_title": "交易监控报告"},
    "execution_live": {"label": "交易监控报告", "icon": "🔴", "default_title": "交易监控报告"},
    "attribution": {"label": "绩效归因", "icon": "📊", "default_title": "绩效归因报告"},
    "backtest": {"label": "回测绩效", "icon": "📉", "default_title": "回测绩效报告"},
}

# 门户页导航栏显示顺序（仅这几类进入门户页）
# 交易环节三份核心报告：交易监控(LIVE) → 组合优化 → 绩效归因
# 静态 execution 报告是流程副产物（report_type="execution"），不进门户页；
# 技术面/基本面/回测报告跟随系统流程按需生成，同样不进入门户页。
PORTAL_DISPLAY_ORDER: List[str] = ["execution_live", "portfolio", "attribution"]


def _load_manifest(report_dir: str) -> Dict[str, Any]:
    """加载现有 manifest.json，不存在时返回空骨架。"""
    manifest_path = os.path.join(report_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return {"task_id": "", "generated_at": "", "reports": []}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取 manifest.json 失败，重建: {e}")
        return {"task_id": "", "generated_at": "", "reports": []}


def _save_manifest(report_dir: str, manifest: Dict[str, Any]) -> str:
    """保存 manifest.json（原子写入）。"""
    manifest_path = os.path.join(report_dir, "manifest.json")
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp_path, manifest_path)
    return manifest_path


def upsert_report(
    report_dir: str,
    report_type: str,
    file_path: str,
    title: Optional[str] = None,
    task_id: str = "",
    live: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> str:
    """新增/更新一份报告记录到 manifest.json。

    参数:
        report_dir: 报告目录（manifest.json 所在目录）
        report_type: 报告类型（technical_report/fundamental_report/portfolio/execution/execution_live/attribution/backtest）
        file_path: 报告 HTML 文件路径（绝对路径或相对 report_dir 的路径，统一转相对路径存储）
        title: 报告标题；None 时按 report_type 取默认
        task_id: 任务 ID（首次写入时设置，后续忽略）
        live: 是否为 LIVE 实时报告
        metadata: 附加元数据
        **extra: 额外字段（如 backend/snapshot_file 等）

    返回:
        manifest.json 的绝对路径
    """
    os.makedirs(report_dir, exist_ok=True)

    manifest = _load_manifest(report_dir)
    if task_id and not manifest.get("task_id"):
        manifest["task_id"] = task_id
    manifest["generated_at"] = datetime.now().isoformat()

    # 转相对路径
    rel_file = file_path
    if os.path.isabs(file_path):
        try:
            rel_file = os.path.relpath(file_path, report_dir).replace("\\", "/")
        except ValueError:
            rel_file = file_path

    config = REPORT_TYPE_CONFIG.get(report_type, {})
    if title is None:
        title = config.get("default_title", report_type)

    record: Dict[str, Any] = {
        "type": report_type,
        "file": rel_file,
        "title": title,
        "generated_at": datetime.now().isoformat(),
        "live": live,
        "metadata": metadata or {},
    }
    record.update(extra)

    # 按 type + file 去重（同类型不同文件可共存，如 technical + fundamental）
    reports = [r for r in manifest.get("reports", [])
               if not (r.get("type") == report_type and r.get("file") == rel_file)]
    reports.append(record)
    manifest["reports"] = reports

    return _save_manifest(report_dir, manifest)


def build_portal_html(manifest: Dict[str, Any], report_dir: str) -> str:
    """生成门户页 index.html（单页应用：顶部导航栏 + iframe 切换）。

    纯静态 HTML+JS，零外部依赖，file:// 协议双击打开。
    - 顶部导航栏：报告类型按钮，点击在当前页面切换 iframe 内容（不跳转新标签）
    - LIVE 报告按钮显示绿点/灰点（通过 <script> 标签检测 snapshot.js 新鲜度）
    - iframe 加载本地 HTML 不受 file:// CORS 限制
    - 默认加载第一份报告；无报告时显示空状态

    参数:
        manifest: manifest.json 解析后的 dict
        report_dir: 报告目录（用于判断 snapshot.js 是否存在）
    """
    task_id = manifest.get("task_id", "")
    generated_at = manifest.get("generated_at", "")
    reports = manifest.get("reports", [])

    # 读取惊泥科技 Logo SVG 内容，内嵌到导航栏（避免 file:// 跨目录引用问题）
    logo_svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "templates", "assets", "jingni_logo.svg")
    logo_svg = ""
    try:
        with open(logo_svg_path, "r", encoding="utf-8") as f:
            logo_svg = f.read()
        # 内嵌到 HTML 时必须去掉 XML 声明和 DOCTYPE，否则浏览器无法渲染 SVG
        import re as _re
        logo_svg = _re.sub(r'<\?xml.*?\?>', '', logo_svg, flags=_re.DOTALL)
        logo_svg = _re.sub(r'<!DOCTYPE[^>]*>', '', logo_svg, flags=_re.DOTALL)
        logo_svg = logo_svg.strip()
        # 必须添加 viewBox 属性，否则 CSS 缩放时 SVG 内部坐标系无法映射，内容不可见
        # 同时移除原始 width/height 属性，交由 CSS 控制
        if 'viewBox' not in logo_svg:
            logo_svg = _re.sub(
                r'<svg([^>]*)>',
                lambda m: '<svg' + m.group(1).replace(
                    'width="1101px"', '').replace(
                    'height="1101px"', '') + ' viewBox="0 0 1101 1101">',
                logo_svg, count=1
            )
    except Exception as e:
        logger.warning(f"读取 Logo SVG 失败: {e}")
        logo_svg = '<span style="font-size:20px;">📊</span>'  # 降级为 emoji

    # 过滤：仅显示门户页指定的报告类型（交易监控/组合优化/绩效归因）
    # 技术面/基本面/回测报告跟随系统流程按需生成，不进入门户页
    portal_types = set(PORTAL_DISPLAY_ORDER)
    filtered_reports = [r for r in reports if r.get("type") in portal_types]

    # 按 PORTAL_DISPLAY_ORDER 顺序排序，同类型按生成时间倒序
    type_order = {t: i for i, t in enumerate(PORTAL_DISPLAY_ORDER)}
    sorted_reports = sorted(
        filtered_reports,
        key=lambda r: (type_order.get(r.get("type"), 999), r.get("generated_at", "")),
    )
    sorted_reports.reverse()  # 先按时间倒序，保证同类型最新的在前
    # 稳定排序：按 type_order 升序（同类型内保持时间倒序）
    sorted_reports = sorted(
        sorted_reports,
        key=lambda r: type_order.get(r.get("type"), 999),
    )

    # 生成导航按钮（不带图标）
    nav_buttons = []
    for idx, r in enumerate(sorted_reports):
        rtype = r.get("type", "")
        config = REPORT_TYPE_CONFIG.get(rtype, {"label": rtype, "icon": "📄", "default_title": rtype})
        is_live = bool(r.get("live", False))
        file_rel = r.get("file", "")
        title = r.get("title", config.get("default_title", rtype))
        snapshot_file = r.get("snapshot_file", "")

        live_dot = ""
        live_attrs = ""
        if is_live:
            live_dot = '<span class="live-dot" data-snapshot="' + snapshot_file + '"></span>'
            live_attrs = ' data-live="true"'

        active_cls = " active" if idx == 0 else ""
        nav_buttons.append(
            f'<button class="nav-btn{active_cls}" data-src="{file_rel}"{live_attrs}'
            f' onclick="switchReport(this)" title="{title}">'
            f'<span class="nav-label">{title}</span>'
            f'{live_dot}'
            f'</button>'
        )

    nav_buttons_str = "\n".join(nav_buttons) if nav_buttons else ""
    has_reports = bool(sorted_reports)
    first_src = sorted_reports[0].get("file", "") if has_reports else ""

    # 空状态 HTML
    empty_html = "" if has_reports else (
        '<div class="empty-state">'
        '<div class="empty-icon">📋</div>'
        '<div class="empty-text">暂无报告</div>'
        '<div class="empty-hint">运行投研流程后将自动生成报告</div>'
        '</div>'
    )

    # iframe 仅在有报告时显示
    iframe_html = (
        f'<iframe id="report-frame" src="{first_src}" frameborder="0"></iframe>'
        if has_reports else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JingniTrader</title>
<style>
/* ═══ 惊泥科技配色卡 ═══
   主色 #17223b | 辅助 #263859 | 文字 #6b778d | 强调 #ff6768 */
:root {{
    --jm-primary: #17223b;
    --jm-secondary: #263859;
    --jm-text: #6b778d;
    --jm-accent: #ff6768;
    --jm-bg: #f5f6f8;
    --jm-card-bg: #ffffff;
    --jm-border: #e5e7eb;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ height: 100%; overflow: hidden; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--jm-bg); color: var(--jm-primary);
    display: flex; flex-direction: column; height: 100vh;
}}

/* ── 顶部导航栏 ── */
.nav-bar {{
    flex-shrink: 0;
    background: var(--jm-primary); border-bottom: 3px solid var(--jm-accent);
    display: flex; align-items: center;
    padding: 0 16px; height: 56px;
    box-shadow: 0 2px 8px rgba(23,34,59,0.15);
    z-index: 10; position: relative;
}}
.nav-brand {{
    font-size: 17px; font-weight: 700; color: #ffffff;
    white-space: nowrap; margin-right: 20px;
    display: flex; align-items: center; gap: 8px;
    letter-spacing: 0.5px;
    text-decoration: none; cursor: pointer;
    transition: opacity 0.15s;
}}
.nav-brand:hover {{ opacity: 0.85; }}
.nav-brand .brand-icon {{ font-size: 20px; }}
.nav-brand .brand-logo {{
    display: inline-flex; align-items: center; flex-shrink: 0;
}}
.nav-brand .brand-logo svg {{
    width: 28px; height: 28px; display: block;
}}
.nav-buttons {{
    display: flex; align-items: center; gap: 4px;
    flex: 1; overflow-x: auto; overflow-y: hidden;
}}
.nav-buttons::-webkit-scrollbar {{ height: 0; }}

/* ── 汉堡菜单按钮（窄屏显示，默认隐藏） ── */
.nav-hamburger {{
    display: none; flex-direction: column; justify-content: center;
    gap: 4px; width: 32px; height: 32px; padding: 6px;
    background: transparent; border: none; cursor: pointer;
    margin-left: auto; flex-shrink: 0; border-radius: 6px;
}}
.nav-hamburger:hover {{ background: rgba(255,255,255,0.1); }}
.nav-hamburger span {{
    display: block; width: 20px; height: 2px;
    background: #ffffff; border-radius: 1px; transition: all 0.25s;
    transform-origin: center;
}}
/* 汉堡按钮激活态（变 X） */
.nav-hamburger.open span:nth-child(1) {{
    transform: translateY(6px) rotate(45deg);
}}
.nav-hamburger.open span:nth-child(2) {{ opacity: 0; }}
.nav-hamburger.open span:nth-child(3) {{
    transform: translateY(-6px) rotate(-45deg);
}}

/* ── 导航按钮 ── */
.nav-btn {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 8px 14px; border: none; background: transparent;
    border-radius: 8px; cursor: pointer; white-space: nowrap;
    font-size: 13px; color: rgba(255,255,255,0.7); transition: all 0.15s;
    font-family: inherit; position: relative;
}}
.nav-btn:hover {{ background: rgba(255,255,255,0.1); color: #ffffff; }}
.nav-btn.active {{
    background: var(--jm-accent); color: #ffffff; font-weight: 600;
}}
.nav-label {{ max-width: 180px; overflow: hidden; text-overflow: ellipsis; }}

/* ── LIVE 指示灯 ── */
.live-dot {{
    width: 7px; height: 7px; border-radius: 50%;
    background: #6b778d; flex-shrink: 0; margin-left: 2px;
}}
.live-dot.fresh {{
    background: #10b981; animation: pulse 1.5s ease-in-out infinite;
}}
.live-dot.stale {{ background: var(--jm-accent); }}
@keyframes pulse {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.6; transform: scale(1.3); }}
}}

/* ── 生成时间（右上角） ── */
.nav-time {{
    font-size: 11px; color: rgba(255,255,255,0.5); white-space: nowrap;
    margin-left: 16px; flex-shrink: 0;
}}

/* ── iframe 内容区 ── */
.content {{
    flex: 1; position: relative; overflow: hidden;
}}
#report-frame {{
    width: 100%; height: 100%; border: none; background: white;
    display: block;
}}

/* ── 底部版权 footer ── */
.portal-footer {{
    flex-shrink: 0; background: var(--jm-primary); color: rgba(255,255,255,0.7);
    padding: 10px 16px; text-align: center; font-size: 12px;
    border-top: 1px solid var(--jm-secondary);
}}
.portal-footer .footer-copyright {{ font-weight: 600; margin-bottom: 2px; }}
.portal-footer .footer-license {{ opacity: 0.75; }}
.portal-footer a {{ color: var(--jm-accent); text-decoration: none; }}
.portal-footer a:hover {{ text-decoration: underline; }}

/* ── 空状态 ── */
.empty-state {{
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%); text-align: center;
}}
.empty-icon {{ font-size: 48px; margin-bottom: 16px; opacity: 0.5; }}
.empty-text {{ font-size: 18px; color: var(--jm-text); margin-bottom: 8px; }}
.empty-hint {{ font-size: 13px; color: #cbd5e1; }}

/* ── 多端自适应 ── */
/* 平板：启用汉堡菜单折叠 */
@media (max-width: 1024px) {{
    .nav-bar {{ padding: 0 12px; height: 52px; }}
    .nav-brand {{ font-size: 15px; margin-right: 14px; }}
    .nav-brand .brand-logo svg {{ width: 24px; height: 24px; }}
    .nav-btn {{ padding: 6px 10px; font-size: 12px; }}
    .nav-label {{ max-width: 120px; }}
    .nav-time {{ display: none; }}
    /* 汉堡菜单：横向无法完整展示时折叠 */
    .nav-hamburger {{ display: flex; }}
    .nav-buttons {{
        display: none; flex-direction: column; align-items: stretch;
        position: absolute; top: 100%; right: 0; left: 0;
        background: var(--jm-primary); padding: 8px 12px; gap: 4px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.25); z-index: 20;
        max-height: 70vh; overflow-y: auto;
    }}
    .nav-buttons.open {{ display: flex; }}
    .nav-btn {{
        width: 100%; justify-content: flex-start; padding: 10px 12px;
    }}
    .nav-label {{ max-width: none; }}
}}
/* 手机 */
@media (max-width: 640px) {{
    .nav-bar {{ padding: 0 8px; height: 48px; }}
    .nav-brand {{ font-size: 14px; margin-right: 8px; }}
    .nav-brand .brand-logo svg {{ width: 22px; height: 22px; }}
    .nav-btn {{ padding: 8px 10px; font-size: 13px; gap: 4px; }}
    .nav-label {{ max-width: none; }}
}}
</style>
</head>
<body>
<div class="nav-bar">
    <a class="nav-brand" href="https://github.com/duhanjun/jingni-trader" target="_blank" rel="noopener" title="GitHub 仓库">
        <span class="brand-logo">{logo_svg}</span>
        <span>JingniTrader</span>
    </a>
    <div class="nav-buttons" id="navButtons">
{nav_buttons_str}
    </div>
    <button class="nav-hamburger" id="navHamburger" onclick="toggleMenu()" aria-label="菜单">
        <span></span><span></span><span></span>
    </button>
    <div class="nav-time">生成于 {generated_at[:19] if generated_at else '-'}</div>
</div>
<div class="content">
{empty_html}{iframe_html}
</div>
<footer class="portal-footer">
    <div class="footer-copyright">© 2026 JingniTrader · 惊泥科技</div>
    <div class="footer-license">基于 <a href="https://opensource.org/licenses/MIT" target="_blank" rel="noopener">MIT 开源协议</a> 发布 · <a href="https://github.com/duhanjun/jingni-trader" target="_blank" rel="noopener">GitHub 仓库</a></div>
</footer>

<script>
// ── 切换报告：更新 iframe src + 高亮当前按钮 ──
function switchReport(btn) {{
    var src = btn.getAttribute('data-src');
    if (!src) return;
    document.getElementById('report-frame').src = src;
    // 高亮当前按钮
    var allBtns = document.querySelectorAll('.nav-btn');
    allBtns.forEach(function(b) {{ b.classList.remove('active'); }});
    btn.classList.add('active');
    // 窄屏下点击后自动收起菜单
    closeMenu();
}}

// ── 汉堡菜单：展开/收起 ──
function toggleMenu() {{
    var nav = document.getElementById('navButtons');
    var btn = document.getElementById('navHamburger');
    var isOpen = nav.classList.toggle('open');
    btn.classList.toggle('open', isOpen);
}}
function closeMenu() {{
    var nav = document.getElementById('navButtons');
    var btn = document.getElementById('navHamburger');
    nav.classList.remove('open');
    btn.classList.remove('open');
}}

// ── 点击导航栏外区域自动收起菜单 ──
document.addEventListener('click', function(e) {{
    var nav = document.getElementById('navButtons');
    var btn = document.getElementById('navHamburger');
    if (!nav || !btn) return;
    if (!nav.contains(e.target) && !btn.contains(e.target)) {{
        closeMenu();
    }}
}});

// ── LIVE 报告新鲜度检测：通过 <script> 标签加载 snapshot.js ──
// （file:// 协议下 <script> 标签不受 CORS 限制）
(function() {{
    'use strict';
    var liveDots = document.querySelectorAll('.live-dot[data-snapshot]');
    if (!liveDots.length) return;

    function checkFreshness() {{
        liveDots.forEach(function(dot) {{
            var snapshotFile = dot.getAttribute('data-snapshot');
            if (!snapshotFile) return;
            var script = document.createElement('script');
            script.src = snapshotFile + '?ts=' + Date.now();
            script.onload = function() {{
                dot.className = 'live-dot fresh';
                setTimeout(function() {{
                    if (dot.className.indexOf('fresh') > -1) {{
                        dot.className = 'live-dot stale';
                    }}
                }}, 4000);
                if (script.parentNode) script.parentNode.removeChild(script);
            }};
            script.onerror = function() {{
                dot.className = 'live-dot';
                if (script.parentNode) script.parentNode.removeChild(script);
            }};
            document.head.appendChild(script);
        }});
    }}

    checkFreshness();
    setInterval(checkFreshness, 5000);
}})();
</script>
</body>
</html>"""


def generate_portal(report_dir: str) -> str:
    """根据 manifest.json 生成/刷新门户页 index.html。

    参数:
        report_dir: 报告目录

    返回:
        index.html 的绝对路径
    """
    manifest = _load_manifest(report_dir)
    html = build_portal_html(manifest, report_dir)

    index_path = os.path.join(report_dir, "index.html")
    tmp_path = index_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp_path, index_path)

    logger.info(f"报告门户已生成: {index_path} ({len(manifest.get('reports', []))} 份报告)")
    return index_path
