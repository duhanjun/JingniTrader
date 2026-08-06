"""报告公共组件：顶部导航栏 + 底部版权 footer。

供 technical_report / fundamental_report / 回测报告(engine.py) 共用，
确保三份报告的顶部导航栏、底部免责声明和版权信息与门户页样式一致。
"""
from __future__ import annotations

import os
import logging

logger = logging.getLogger("common_components")

# Logo SVG 路径（与 report_portal.py 共用同一份）
_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "assets", "jingni_logo.svg",
)


def _load_logo_svg() -> str:
    """读取惊泥科技 Logo SVG 内容并清理 XML 声明，便于内嵌到 HTML。"""
    try:
        with open(_LOGO_PATH, "r", encoding="utf-8") as f:
            svg = f.read()
        # 去除 XML 声明和 DOCTYPE（内嵌到 HTML 时会导致渲染失败）
        import re
        svg = re.sub(r'<\?xml[^>]*\?>\s*', '', svg)
        svg = re.sub(r'<!DOCTYPE[^>]*>\s*', '', svg)
        # 去除 width/height 属性，添加 viewBox 确保缩放正确
        svg = re.sub(r'\s+width="[^"]*"', '', svg)
        svg = re.sub(r'\s+height="[^"]*"', '', svg)
        if 'viewBox' not in svg:
            svg = svg.replace('<svg', '<svg viewBox="0 0 1101 1101"', 1)
        return svg
    except Exception as e:
        logger.warning(f"读取 Logo SVG 失败: {e}")
        return '<span style="font-size:20px;">📊</span>'


# ── 统一页面布局 CSS（body + 正文 header + 卡片），确保各报告宽度/高度一致 ──
_PAGE_CSS = """
/* ═══ 惊泥科技配色卡 ═══ */
:root {
    --jm-primary: #17223b;
    --jm-secondary: #263859;
    --jm-text: #6b778d;
    --jm-accent: #ff6768;
    --bg: #f5f6f8; --card-bg: #ffffff;
    --text: #17223b; --text-muted: #6b778d; --border: #e5e7eb;
    --up: #ff6768; --down: #12a05c;
    --danger: #ff6768; --warning: #f59e0b; --success: #10b981;
    --shadow: 0 1px 3px rgba(23,34,59,0.08);
}
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 1200px; margin: 0 auto; padding: 20px;
    background: var(--bg); color: var(--text);
}
/* ── 正文标题 header（统一高度） ── */
.header {
    background: linear-gradient(135deg, #17223b, #263859); color: #fff;
    padding: 30px 40px; border-radius: 12px; margin-bottom: 24px;
    border-bottom: 3px solid var(--jm-accent);
    box-shadow: 0 4px 12px rgba(23,34,59,0.15);
}
.header h1 { margin: 0 0 8px 0; font-size: 26px; }
.header p { margin: 0; opacity: 0.9; font-size: 14px; }
/* ── 正文卡片 ── */
.section {
    background: var(--card-bg); border-radius: 10px; padding: 24px;
    margin-bottom: 20px; box-shadow: var(--shadow); border: 1px solid #f3f4f6;
}
.section h2 {
    margin: 0 0 16px 0; font-size: 18px; color: var(--jm-primary);
    border-bottom: 2px solid var(--jm-accent); padding-bottom: 8px;
}
.metrics-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
}
.metric-card {
    background: #f9f9f9; padding: 16px; border-radius: 8px; text-align: center;
    border: 1px solid #eee;
}
.metric-value { font-size: 24px; font-weight: 700; color: var(--text); }
.metric-label { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.chart-container { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #f3f4f6; }
th { background: var(--bg); font-weight: 600; color: var(--text-muted); }
@media (max-width: 1024px) {
    body { padding: 16px; }
    .header { padding: 24px; }
    .header h1 { font-size: 22px; }
    .metrics-grid { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
}
@media (max-width: 640px) {
    body { padding: 12px; }
    .header { padding: 20px; }
    .header h1 { font-size: 20px; }
    .header p { font-size: 12px; }
    .section { padding: 16px; }
    .section h2 { font-size: 16px; }
    .metrics-grid { grid-template-columns: 1fr 1fr; gap: 12px; }
    table { font-size: 12px; }
    th, td { padding: 6px 8px; }
}
"""

# ── 顶部导航栏 CSS（与门户页一致） ──
_NAV_CSS = """
/* ── 顶部导航栏 ── */
.jm-nav-bar {
    background: var(--jm-primary); border-bottom: 3px solid var(--jm-accent);
    display: flex; align-items: center;
    padding: 0 16px; height: 56px;
    box-shadow: 0 2px 8px rgba(23,34,59,0.15);
    position: sticky; top: 0; z-index: 100;
    margin: -20px -20px 24px -20px;
}
.jm-nav-brand {
    font-size: 17px; font-weight: 700; color: #ffffff;
    white-space: nowrap; margin-right: 20px;
    display: flex; align-items: center; gap: 8px;
    letter-spacing: 0.5px;
    text-decoration: none; cursor: pointer;
    transition: opacity 0.15s;
}
.jm-nav-brand:hover { opacity: 0.85; }
.jm-nav-brand .brand-logo {
    display: inline-flex; align-items: center; flex-shrink: 0;
}
.jm-nav-brand .brand-logo svg { width: 28px; height: 28px; display: block; }
.jm-nav-meta {
    margin-left: auto; font-size: 12px; color: rgba(255,255,255,0.6);
    white-space: nowrap;
}
@media (max-width: 1024px) {
    .jm-nav-bar { padding: 0 12px; height: 52px; }
    .jm-nav-brand { font-size: 15px; margin-right: 14px; }
    .jm-nav-brand .brand-logo svg { width: 24px; height: 24px; }
    .jm-nav-meta { display: none; }
}
@media (max-width: 640px) {
    .jm-nav-bar { padding: 0 8px; height: 48px; }
    .jm-nav-brand { font-size: 14px; margin-right: 8px; }
    .jm-nav-brand .brand-logo svg { width: 22px; height: 22px; }
}
"""

# ── 底部 footer CSS ──
_FOOTER_CSS = """
/* ── 底部版权 footer ── */
.jm-footer {
    background: var(--jm-primary); color: rgba(255,255,255,0.7);
    padding: 16px 20px; text-align: center; font-size: 12px;
    margin: 40px -20px -20px -20px;
    border-top: 1px solid var(--jm-secondary);
}
.jm-footer .footer-copyright { font-weight: 600; margin-bottom: 4px; color: #fff; }
.jm-footer .footer-license { opacity: 0.75; margin-bottom: 8px; }
.jm-footer a { color: var(--jm-accent); text-decoration: none; }
.jm-footer a:hover { text-decoration: underline; }
.jm-disclaimer {
    background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px;
    padding: 12px 14px; margin: 24px 0; font-size: 12px; color: #856404;
    line-height: 1.6;
}
.jm-disclaimer-title { font-weight: 700; margin-bottom: 4px; }
@media (max-width: 640px) {
    .jm-footer { padding: 12px 16px; }
}
"""


def build_page_css() -> str:
    """返回完整统一页面样式：页面布局 + 顶部导航栏 + 底部 footer。

    供所有报告复用，确保各报告的宽度、正文 header 高度、卡片样式完全一致。
    """
    return _PAGE_CSS + _NAV_CSS + _FOOTER_CSS


def build_nav_bar_css() -> str:
    """返回顶部导航栏 + 底部 footer 的 CSS 样式（供报告 <style> 内嵌）。

    兼容旧调用：等价于 build_page_css()。
    """
    return build_page_css()


def build_page_header(title: str, subtitle: str = "") -> str:
    """构建正文标题 header HTML（统一高度与样式）。"""
    return f"""
<div class="header">
    <h1>{title}</h1>
    <p>{subtitle}</p>
</div>
"""


def build_nav_bar_html(report_title: str = "") -> str:
    """构建顶部导航栏 HTML（与门户页样式一致）。

    参数:
        report_title: 当前报告标题（显示在导航栏右侧，可选）
    """
    logo_svg = _load_logo_svg()
    meta = f'<div class="jm-nav-meta">{report_title}</div>' if report_title else ''
    return f"""
<nav class="jm-nav-bar">
    <a class="jm-nav-brand" href="https://github.com/duhanjun/jingni-trader" target="_blank" rel="noopener" title="GitHub 仓库">
        <span class="brand-logo">{logo_svg}</span>
        <span>JingniTrader</span>
    </a>
    {meta}
</nav>
"""


def build_footer_html(disclaimer: str = "") -> str:
    """构建底部免责声明 + 版权信息 HTML（与门户页样式一致）。

    参数:
        disclaimer: 报告定制的免责声明正文（空字符串则用通用免责声明）。
    """
    if not disclaimer:
        disclaimer = (
            "本报告由 JingniTrader 自动生成，仅供学习研究用途，不构成任何投资建议。"
            "量化交易存在风险，历史回测业绩不代表未来收益。实盘交易有风险，"
            "请根据自身风险承受能力谨慎决策。"
        )
    return f"""
<div class="jm-disclaimer">
    <div class="jm-disclaimer-title">⚠️ 免责声明</div>
    {disclaimer}
</div>
<footer class="jm-footer">
    <div class="footer-copyright">© 2026 JingniTrader · 惊泥科技</div>
    <div class="footer-license">基于 <a href="https://opensource.org/licenses/MIT" target="_blank" rel="noopener">MIT 开源协议</a> 发布 · <a href="https://github.com/duhanjun/jingni-trader" target="_blank" rel="noopener">GitHub 仓库</a></div>
</footer>
"""


# ════════════════════════════════════════════════════════════════
# base.html.j2 模板渲染能力（阶段一）
# ════════════════════════════════════════════════════════════════
# 统一页面骨架由 base.html.j2 提供（<!DOCTYPE html> / <head> / <body> /
# nav / footer 骨架），各报告通过 {% extends "base.html.j2" %} 继承，
# 只覆盖 title / head_css / content 三个 block，从而统一复用同一套骨架。

_TEMPLATES_DIR = os.path.dirname(os.path.abspath(__file__))

_jinja_env = None


def _get_jinja_env():
    """懒加载 Jinja2 Environment（FileSystemLoader 指向 templates 目录）。"""
    global _jinja_env
    if _jinja_env is None:
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
        except Exception as e:  # jinja2 未安装
            logger.warning(f"jinja2 未安装: {e}")
            return None
        _jinja_env = Environment(
            loader=FileSystemLoader(_TEMPLATES_DIR),
            autoescape=select_autoescape(default_for_string=False, default=False),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
    return _jinja_env


def render_page(template_name: str, **context) -> str:
    """用 base 体系渲染一个报告模板。

    参数:
        template_name: 模板文件名（如 backtest.html.j2 / capital_flow_report.html.j2）。
        **context: 模板上下文。自动注入 base_css / logo_svg / nav_title /
                   disclaimer 等 base 模板所需的公共变量（调用方可覆盖）。
                   可选参数 extra_template_dirs（list[str]）：额外模板搜索目录
                   （报告插件模板所在目录），用于加载插件自身的 .j2 模板。
    返回:
        渲染后的完整 HTML 页面字符串。

    注意:
        - 模板内容里的 Jinja2 表达式 {{ }} / 宏 / block 按模板语法渲染；
        - 图表 HTML（plotly）等已是渲染后的 HTML，需用 | safe 过滤器传入，
          或在调用方以已安全片段形式注入。
    """
    extra_dirs = context.pop("extra_template_dirs", None)
    if extra_dirs:
        # 报告插件：额外搜索插件模板目录（与 templates 目录合并），
        # 使插件模板可 {% extends "base.html.j2" %}
        env = _build_env(extra_dirs)
    else:
        env = _get_jinja_env()
    if env is None:
        raise RuntimeError("jinja2 未安装，无法使用 base.html.j2 模板渲染")

    # 注入 base 模板所需公共变量（调用方可通过 context 覆盖）
    base_ctx = {
        "base_css": build_page_css(),
        "logo_svg": _load_logo_svg(),
        "nav_title": "",
        "disclaimer": (
            "本报告由 JingniTrader 自动生成，仅供学习研究用途，不构成任何投资建议。"
            "量化交易存在风险，历史回测业绩不代表未来收益。实盘交易有风险，"
            "请根据自身风险承受能力谨慎决策。"
        ),
    }
    base_ctx.update(context)

    tmpl = env.get_template(template_name)
    return tmpl.render(**base_ctx)


def _build_env(extra_dirs) -> "Environment":
    """构建合并模板搜索路径的 Jinja2 Environment（templates + extra_dirs）。"""
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except Exception as e:
        logger.warning(f"jinja2 未安装: {e}")
        return None
    search_paths = [_TEMPLATES_DIR] + list(extra_dirs)
    return Environment(
        loader=FileSystemLoader(search_paths),
        autoescape=select_autoescape(default_for_string=False, default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
