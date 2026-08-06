"""轻量 SVG 组件模块（零 JS 依赖）。

提供组合优化与执行监控报告所需的纯 SVG 图表组件，
借鉴 astock-workbench 的轻量手绘 SVG 风格，融合统一设计变量。

组件清单：
- render_weight_deviation_bar: 双向条形图（权重偏差/超配低配）
- render_ring_chart: 环形图（比例/阈值监控）
- render_status_indicator: 状态指示灯（连接状态/执行模式）
- render_progress_bar: 进度条（仓位水平/成交率）

设计约束：
- 所有组件返回纯 SVG 字符串，可直接嵌入 HTML
- 颜色使用 A 股习惯：红涨绿跌
- 告警色三级：od(红/超限) / wr(橙/警告) / ok(绿/正常)
- 不引用外部 CSS class，样式全部内联
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# 统一设计变量（与 PRD 6.5 节一致）
COLORS = {
    "up": "#e02b2b",          # 红涨
    "down": "#12a05c",        # 绿跌
    "danger": "#ef4444",      # od 超限
    "warning": "#f59e0b",     # wr 警告
    "success": "#10b981",     # ok 正常
    "muted": "#9ca3af",       # 灰色（未启用）
    "primary": "#3b82f6",
    "text": "#1f2937",
    "text_muted": "#6b7280",
    "border": "#e5e7eb",
    "bg": "#ffffff",
}


def _escape(text: str) -> str:
    """转义 SVG 文本中的特殊字符。"""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_weight_deviation_bar(
    weights: Dict[str, float],
    benchmark: Optional[Dict[str, float]] = None,
    max_items: int = 20,
    width: int = 720,
) -> str:
    """渲染权重偏差双向条形图。

    中线为基准权重（默认等权），左侧低配（绿），右侧超配（红）。
    借鉴 astock-workbench renderPosChart 的 SVG 双向条形图代码。

    参数:
        weights: 实际权重字典 {code: weight}
        benchmark: 基准权重字典；None 时使用等权基准
        max_items: 最多展示条目数（按绝对偏差排序）
        width: SVG 宽度

    返回:
        SVG 字符串
    """
    if not weights:
        return '<div class="svg-placeholder">无权重数据</div>'

    items = list(weights.items())
    n = len(items)

    # 等权基准或指定基准
    if benchmark is None:
        bench_weight = 1.0 / n if n > 0 else 0.0
        deviations = [(code, w, w - bench_weight) for code, w in items]
    else:
        deviations = [
            (code, w, w - benchmark.get(code, 0.0))
            for code, w in items
        ]

    # 按绝对偏差降序，取前 max_items
    deviations.sort(key=lambda x: abs(x[2]), reverse=True)
    deviations = deviations[:max_items]

    # 布局参数
    row_height = 28
    label_width = 110
    value_width = 90
    center_x = label_width + (width - label_width - value_width) // 2
    max_abs_dev = max((abs(d[2]) for d in deviations), default=0.01)
    max_abs_dev = max(max_abs_dev, 0.01)  # 防止除零
    bar_max_width = (width - label_width - value_width) // 2 - 20

    height = len(deviations) * row_height + 40
    rows_svg = []

    for i, (code, weight, dev) in enumerate(deviations):
        y = 20 + i * row_height
        is_over = dev >= 0
        color = COLORS["up"] if is_over else COLORS["down"]
        bar_len = int((abs(dev) / max_abs_dev) * bar_max_width)

        # 条形起点：超配从中心向右，低配从中心向左
        if is_over:
            bar_x = center_x
        else:
            bar_x = center_x - bar_len

        weight_pct = f"{weight * 100:.2f}%"
        dev_pct = f"{dev * 100:+.2f}%"
        dev_sign = "+" if is_over else ""

        rows_svg.append(
            f'<g>'
            f'<text x="{label_width - 8}" y="{y + 14}" text-anchor="end" '
            f'font-size="11" fill="{COLORS["text"]}">{_escape(code)}</text>'
            f'<rect x="{bar_x}" y="{y}" width="{bar_len}" height="18" '
            f'fill="{color}" rx="2"/>'
            f'<text x="{center_x}" y="{y + 14}" text-anchor="middle" '
            f'font-size="10" fill="{COLORS["text_muted"]}">{weight_pct}</text>'
            f'<text x="{width - value_width + 8}" y="{y + 14}" '
            f'font-size="11" fill="{color}">{dev_sign}{dev_pct}</text>'
            f'</g>'
        )

    # 中线
    center_line = (
        f'<line x1="{center_x}" y1="10" x2="{center_x}" '
        f'y2="{height - 20}" stroke="{COLORS["border"]}" '
        f'stroke-dasharray="3,3"/>'
    )

    # 标题与图例
    legend = (
        f'<text x="10" y="14" font-size="11" fill="{COLORS["text_muted"]}">'
        f'标的</text>'
        f'<text x="{width - value_width + 8}" y="14" font-size="11" '
        f'fill="{COLORS["text_muted"]}">偏差</text>'
        f'<rect x="{center_x + 5}" y="{height - 16}" width="10" height="8" '
        f'fill="{COLORS["up"]}"/>'
        f'<text x="{center_x + 20}" y="{height - 9}" font-size="10" '
        f'fill="{COLORS["text_muted"]}">超配</text>'
        f'<rect x="{center_x - 60}" y="{height - 16}" width="10" height="8" '
        f'fill="{COLORS["down"]}"/>'
        f'<text x="{center_x - 45}" y="{height - 9}" font-size="10" '
        f'fill="{COLORS["text_muted"]}">低配</text>'
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px;font-family:sans-serif">'
        f'{legend}{center_line}{"".join(rows_svg)}</svg>'
    )


def render_ring_chart(
    value: float,
    threshold: float = 1.0,
    label: str = "",
    sublabel: str = "",
    size: int = 120,
    color_override: Optional[str] = None,
) -> str:
    """渲染环形图（比例/阈值监控）。

    借鉴 astock-workbench ring() 的 SVG 环形图代码。
    当 value/threshold 超过 1.0 时自动变为危险色。

    参数:
        value: 当前值
        threshold: 阈值（用于计算占比和颜色）
        label: 中心主标签（如 "75%"）
        sublabel: 中心副标签（如 "日亏损"）
        size: SVG 尺寸（正方形）
        color_override: 强制颜色（覆盖自动色阶）

    返回:
        SVG 字符串
    """
    ratio = value / threshold if threshold > 0 else 0.0
    ratio = max(0.0, min(ratio, 1.0))  # 环形进度限制在 0-1

    # 自动色阶：超限红 / 接近阈值橙 / 正常绿
    if color_override is not None:
        color = color_override
    elif ratio >= 1.0:
        color = COLORS["danger"]
    elif ratio >= 0.8:
        color = COLORS["warning"]
    else:
        color = COLORS["success"]

    stroke_width = 10
    radius = (size - stroke_width) // 2
    cx = size // 2
    cy = size // 2
    circumference = 2 * math.pi * radius
    dash_len = ratio * circumference

    # 背景环 + 进度环
    bg_ring = (
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
        f'stroke="{COLORS["border"]}" stroke-width="{stroke_width}"/>'
    )
    progress_ring = (
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-dasharray="{dash_len} {circumference}" '
        f'stroke-dashoffset="{circumference / 4}" '
        f'transform="rotate(-90 {cx} {cy})" stroke-linecap="round"/>'
    )

    # 中心文本
    center_text = ""
    if label:
        center_text += (
            f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" '
            f'font-size="18" font-weight="700" fill="{COLORS["text"]}">'
            f'{_escape(label)}</text>'
        )
    if sublabel:
        center_text += (
            f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" '
            f'font-size="10" fill="{COLORS["text_muted"]}">'
            f'{_escape(sublabel)}</text>'
        )

    return (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'style="font-family:sans-serif">'
        f'{bg_ring}{progress_ring}{center_text}</svg>'
    )


def render_status_indicator(
    status: str,
    label: str = "",
    show_pulse: bool = True,
) -> str:
    """渲染状态指示灯（连接状态/执行模式）。

    借鉴 astock-workbench xtdot + pulse CSS 动画。

    参数:
        status: 状态类型
            "connected" - 绿色脉冲（已连接）
            "disconnected" - 红色（断开）
            "disabled" - 灰色（未启用）
        label: 状态文字
        show_pulse: 已连接时是否显示脉冲动画

    返回:
        SVG + 内联 style 字符串
    """
    color_map = {
        "connected": COLORS["success"],
        "disconnected": COLORS["danger"],
        "disabled": COLORS["muted"],
    }
    color = color_map.get(status, COLORS["muted"])
    pulse = status == "connected" and show_pulse

    # 内联 CSS 动画（避免依赖外部样式表）
    style = ""
    if pulse:
        style = (
            '<style>'
            '.status-dot-pulse{animation:statusPulse 1.5s ease-in-out infinite;}'
            '@keyframes statusPulse{'
            '0%{opacity:1;transform:scale(1)}'
            '50%{opacity:0.5;transform:scale(1.3)}'
            '100%{opacity:1;transform:scale(1)}}'
            '</style>'
        )
        dot_class = "status-dot-pulse"
    else:
        dot_class = ""

    dot_svg = (
        f'<svg width="12" height="12" viewBox="0 0 12 12" '
        f'style="display:inline-block;vertical-align:middle">'
        f'<circle class="{dot_class}" cx="6" cy="6" r="5" fill="{color}" '
        f'transform-origin="6 6"/></svg>'
    )

    if label:
        return (
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'font-family:sans-serif;font-size:13px;color:{COLORS["text"]}">'
            f'{dot_svg}<span>{_escape(label)}</span></span>{style}'
        )
    return dot_svg + style


def render_progress_bar(
    value: float,
    total: float = 1.0,
    label: str = "",
    width: int = 240,
    color_override: Optional[str] = None,
    show_text: bool = True,
) -> str:
    """渲染水平进度条（仓位水平/成交率）。

    参数:
        value: 当前值
        total: 总值（用于计算占比）
        label: 左侧标签
        width: SVG 宽度
        color_override: 强制颜色
        show_text: 是否显示百分比文字

    返回:
        SVG 字符串
    """
    ratio = value / total if total > 0 else 0.0
    ratio = max(0.0, min(ratio, 1.0))

    if color_override is not None:
        color = color_override
    elif ratio >= 1.0:
        color = COLORS["danger"]
    elif ratio >= 0.8:
        color = COLORS["warning"]
    else:
        color = COLORS["primary"]

    bar_height = 16
    bar_width = width - (120 if label else 0) - (50 if show_text else 0)
    bar_x = 120 if label else 0
    fill_width = int(bar_width * ratio)

    label_svg = ""
    if label:
        label_svg = (
            f'<text x="0" y="12" font-size="12" fill="{COLORS["text"]}">'
            f'{_escape(label)}</text>'
        )

    text_svg = ""
    if show_text:
        pct = f"{ratio * 100:.1f}%"
        text_svg = (
            f'<text x="{width - 5}" y="12" text-anchor="end" font-size="12" '
            f'fill="{COLORS["text"]}" font-weight="600">{pct}</text>'
        )

    return (
        f'<svg viewBox="0 0 {width} {bar_height + 4}" width="{width}" '
        f'height="{bar_height + 4}" style="font-family:sans-serif">'
        f'{label_svg}'
        f'<rect x="{bar_x}" y="0" width="{bar_width}" height="{bar_height}" '
        f'fill="{COLORS["border"]}" rx="3"/>'
        f'<rect x="{bar_x}" y="0" width="{fill_width}" height="{bar_height}" '
        f'fill="{color}" rx="3"/>'
        f'{text_svg}'
        f'</svg>'
    )


def render_metric_card(
    title: str,
    value: str,
    subvalue: str = "",
    color: Optional[str] = None,
    icon: str = "",
) -> str:
    """渲染单个指标卡片（用于四宫格仪表盘）。

    借鉴 astock-workbench renderStats() 的卡片样式。

    参数:
        title: 卡片标题
        value: 主数值
        subvalue: 副数值（如变化量）
        color: 主数值颜色
        icon: 可选图标字符

    返回:
        HTML 字符串
    """
    value_color = color if color else COLORS["primary"]
    sub_html = ""
    if subvalue:
        sub_html = (
            f'<div style="font-size:12px;color:{COLORS["text_muted"]};'
            f'margin-top:4px">{_escape(subvalue)}</div>'
        )

    return (
        f'<div style="background:{COLORS["bg"]};padding:20px;border-radius:10px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.08);">'
        f'<div style="font-size:13px;color:{COLORS["text_muted"]};'
        f'margin-bottom:8px">{_escape(title)}</div>'
        f'<div style="font-size:24px;font-weight:700;color:{value_color};'
        f'font-family:sans-serif">{_escape(value)}</div>'
        f'{sub_html}'
        f'</div>'
    )


def render_alert_item(
    severity: str,
    title: str,
    detail: str = "",
    suggestion: str = "",
) -> str:
    """渲染单个告警/待办项（借鉴 astock-workbench buildTodos）。

    三级严重度色编码：
    - od (红/超限)
    - wr (橙/警告)
    - ok (绿/正常)

    参数:
        severity: 严重度 "od" / "wr" / "ok"
        title: 标题
        detail: 详情
        suggestion: 建议操作

    返回:
        HTML 字符串
    """
    severity_map = {
        "od": (COLORS["danger"], COLORS["danger"] + "20"),  # 红 + 浅红背景
        "wr": (COLORS["warning"], COLORS["warning"] + "20"),
        "ok": (COLORS["success"], COLORS["success"] + "20"),
    }
    color, bg_color = severity_map.get(severity, severity_map["ok"])

    detail_html = ""
    if detail:
        detail_html = (
            f'<div style="font-size:12px;color:{COLORS["text_muted"]};'
            f'margin-top:4px">{_escape(detail)}</div>'
        )

    suggestion_html = ""
    if suggestion:
        suggestion_html = (
            f'<div style="font-size:12px;color:{color};margin-top:4px">'
            f'建议: {_escape(suggestion)}</div>'
        )

    return (
        f'<div style="background:{bg_color};border-left:3px solid {color};'
        f'padding:12px 16px;border-radius:6px;margin-bottom:8px;'
        f'font-family:sans-serif">'
        f'<div style="font-size:14px;font-weight:600;color:{COLORS["text"]}">'
        f'{_escape(title)}</div>'
        f'{detail_html}{suggestion_html}'
        f'</div>'
    )
