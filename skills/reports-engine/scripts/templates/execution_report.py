"""执行监控报告模板（F-P0-1 状态条 + F-P0-3 仪表盘 + F-P0-4 风控）。

生成执行监控引擎的可视化 HTML 报告，包含：
1. 实盘连接状态条（F-P0-1）
2. 账户概览四宫格仪表盘（F-P0-3）
3. 风控仪表盘 + 止损信号列表（F-P0-4）

数据来源：
- execution-monitor-engine run() 返回的 metadata
  - mode: paper/live
  - account_snapshot: {nav, available_cash, start_of_day_nav, positions: {code: {volume, available_volume, avg_cost}}}
  - orders_executed / orders_failed
- trade_log.jsonl（审计日志，AUDIT_LOG_PATH）
- ledger.jsonl（成交账本，PAPER_LEDGER_PATH，paper 模式）
- portfolio-risk-engine 的 stop_signals（跨引擎引用）

派生计算：
- 较昨日变化 = nav - start_of_day_nav
- 持仓市值 = Σ(volume × price)  # 需行情价格
- 当日盈亏（未实现）= 持仓市值 - 持仓成本
- 日亏损率 = (nav - start_of_day_nav) / start_of_day_nav
- 成交率 = orders_executed / (orders_executed + orders_failed)

数据缺失容错：任一字段缺失时显示「数据不可用」占位，不阻塞生成。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("reports-engine.execution_template")

# 执行监控风控阈值默认值（与 execution-monitor-engine config.py 一致）
DEFAULT_MAX_DAILY_LOSS_RATIO = 0.02
DEFAULT_MAX_SINGLE_ORDER_RATIO = 0.10
DEFAULT_MAX_ORDER_FREQUENCY = 2


def load_trade_log(audit_log_path: Optional[str]) -> List[Dict[str, Any]]:
    """加载审计日志 trade_log.jsonl。

    每行一条记录：{timestamp, order_id, code, side, volume, price, status, metadata}
    """
    if not audit_log_path or not os.path.exists(audit_log_path):
        return []
    records = []
    try:
        with open(audit_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception as e:
        logger.warning(f"加载审计日志失败: {e}")
    return records


def load_ledger(ledger_path: Optional[str]) -> List[Dict[str, Any]]:
    """加载成交账本 ledger.jsonl。"""
    if not ledger_path or not os.path.exists(ledger_path):
        return []
    records = []
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception as e:
        logger.warning(f"加载成交账本失败: {e}")
    return records


def derive_risk_metrics(
    account_snapshot: Dict[str, Any],
    trade_log: List[Dict[str, Any]],
    max_daily_loss_ratio: float = DEFAULT_MAX_DAILY_LOSS_RATIO,
    max_single_order_ratio: float = DEFAULT_MAX_SINGLE_ORDER_RATIO,
    max_order_frequency: int = DEFAULT_MAX_ORDER_FREQUENCY,
) -> Dict[str, Any]:
    """派生风控指标。

    返回 dict 含：
    - daily_loss_ratio: 当日亏损率
    - max_single_order_value: 当日最大单笔成交额
    - order_frequency: 当日下单频率（记录数）
    - thresholds: 阈值配置
    """
    nav = float(account_snapshot.get("nav", 0) or 0)
    start_of_day_nav = float(account_snapshot.get("start_of_day_nav", nav) or nav)

    # 日亏损率
    daily_loss_ratio = 0.0
    if start_of_day_nav > 0:
        daily_loss_ratio = (nav - start_of_day_nav) / start_of_day_nav

    # 当日最大单笔成交额
    max_single_order_value = 0.0
    for rec in trade_log:
        price = float(rec.get("price", 0) or 0)
        volume = float(rec.get("volume", 0) or 0)
        order_value = abs(price * volume)
        if order_value > max_single_order_value:
            max_single_order_value = order_value

    # 下单频率（当日记录数）
    order_frequency = len(trade_log)

    # 阈值限额（用于环形图占比）
    max_single_order_limit = nav * max_single_order_ratio if nav > 0 else 0

    return {
        "daily_loss_ratio": daily_loss_ratio,
        "max_single_order_value": max_single_order_value,
        "order_frequency": order_frequency,
        "max_single_order_limit": max_single_order_limit,
        "thresholds": {
            "MAX_DAILY_LOSS_RATIO": max_daily_loss_ratio,
            "MAX_SINGLE_ORDER_RATIO": max_single_order_ratio,
            "MAX_ORDER_FREQUENCY": max_order_frequency,
        },
    }


def build_status_bar_html(mode: str, backend: str, available: bool) -> str:
    """构建实盘连接状态条（F-P0-1）。

    借鉴 astock-workbench xtCard + renderXTStatus 的 UI。
    """
    from scripts.renderers.svg_components import render_status_indicator

    if mode == "live":
        status = "connected" if available else "disconnected"
        label = f"实盘交易 · {backend} · {'已连接' if available else '已断开'}"
        status_html = render_status_indicator(status=status, label=label)
    else:
        status_html = render_status_indicator(
            status="disabled",
            label=f"模拟交易 · 未启用实盘",
        )

    return f"""
    <div style="position:sticky;top:0;z-index:100;background:#ffffff;
                border-bottom:1px solid #e5e7eb;padding:12px 20px;
                display:flex;align-items:center;justify-content:space-between;
                box-shadow:0 1px 3px rgba(0,0,0,0.05);">
        <div>{status_html}</div>
        <div style="font-size:12px;color:#6b7280;">
            <span id="last-sync">最后同步: 刚刚</span>
        </div>
    </div>
    """


def build_account_dashboard_html(
    account_snapshot: Dict[str, Any],
    orders_executed: int,
    orders_failed: int,
    trade_log: List[Dict[str, Any]],
) -> str:
    """构建账户概览四宫格仪表盘（F-P0-3）。

    借鉴 astock-workbench renderStats() 四宫格布局。
    """
    from scripts.renderers.svg_components import COLORS, render_metric_card, render_progress_bar

    if not account_snapshot:
        return '<div class="section"><h2>账户概览</h2><div class="placeholder">数据不可用</div></div>'

    nav = float(account_snapshot.get("nav", 0) or 0)
    available_cash = float(account_snapshot.get("available_cash", 0) or 0)
    start_of_day_nav = float(account_snapshot.get("start_of_day_nav", nav) or nav)
    positions = account_snapshot.get("positions", {}) or {}

    # 较昨日变化
    change = nav - start_of_day_nav
    change_pct = (change / start_of_day_nav * 100) if start_of_day_nav > 0 else 0
    change_color = COLORS["up"] if change >= 0 else COLORS["down"]
    change_sign = "+" if change >= 0 else ""

    # 持仓成本（从 avg_cost × volume 计算）
    holding_cost = sum(
        float(pos.get("avg_cost", 0) or 0) * float(pos.get("volume", 0) or 0)
        for pos in positions.values()
    )
    # 持仓市值需要行情价格，当前无价格数据源时显示持仓成本作为近似
    position_value = holding_cost  # 占位：实际需从 DATA 产物获取最新价

    # 仓位水平
    position_ratio = (position_value / nav) if nav > 0 else 0

    # 执行进度
    total_orders = orders_executed + orders_failed
    fill_rate = (orders_executed / total_orders) if total_orders > 0 else 0
    fill_rate_color = COLORS["warning"] if fill_rate < 0.8 else COLORS["success"]

    # 当日盈亏（未实现 = 持仓浮盈，简化为 0 因为无行情价格）
    unrealized_pnl = 0  # 数据缺口：需行情价格

    cards = [
        render_metric_card(
            "账户净值",
            f"{nav:,.2f}",
            f"较昨日 {change_sign}{change:,.2f} ({change_sign}{change_pct:.2f}%)",
            color=change_color,
        ),
        render_metric_card(
            "当日盈亏",
            f"{unrealized_pnl:,.2f}",
            "未实现浮盈（需行情数据）",
            color=COLORS["text_muted"],
        ),
        render_metric_card(
            "仓位水平",
            f"{position_ratio * 100:.1f}%",
            f"可用资金 {available_cash:,.2f}",
            color=COLORS["primary"],
        ),
        render_metric_card(
            "执行进度",
            f"{orders_executed}/{total_orders}",
            f"成交率 {fill_rate * 100:.1f}%",
            color=fill_rate_color,
        ),
    ]

    cards_html = "".join(cards)
    progress_html = render_progress_bar(
        value=fill_rate, total=1.0, label="成交率",
        color_override=fill_rate_color,
    )

    return f"""
    <div class="section">
        <h2>账户概览</h2>
        <div class="metrics-grid">{cards_html}</div>
        <div style="margin-top:16px;">{progress_html}</div>
    </div>
    """


def build_risk_dashboard_html(
    risk_metrics: Dict[str, Any],
    stop_signals: Dict[str, Any],
) -> str:
    """构建风控仪表盘（F-P0-4）。

    包含：
    - 日亏损监控环形图
    - 单笔订单监控环形图
    - 下单频率环形图
    - 断路器状态卡
    - 止损信号列表（来自 portfolio-risk-engine 的 stop_signals）
    """
    from scripts.renderers.svg_components import (
        COLORS, render_ring_chart, render_alert_item,
    )

    thresholds = risk_metrics.get("thresholds", {})
    daily_loss = abs(risk_metrics.get("daily_loss_ratio", 0))
    max_single = risk_metrics.get("max_single_order_value", 0)
    max_single_limit = risk_metrics.get("max_single_order_limit", 0)
    order_freq = risk_metrics.get("order_frequency", 0)
    max_freq = thresholds.get("MAX_ORDER_FREQUENCY", DEFAULT_MAX_ORDER_FREQUENCY)
    max_daily_loss = thresholds.get("MAX_DAILY_LOSS_RATIO", DEFAULT_MAX_DAILY_LOSS_RATIO)

    # 三个环形图
    daily_loss_ring = render_ring_chart(
        value=daily_loss, threshold=max_daily_loss,
        label=f"{daily_loss * 100:.2f}%", sublabel="日亏损",
    )
    single_order_ring = render_ring_chart(
        value=max_single, threshold=max_single_limit if max_single_limit > 0 else 1.0,
        label=f"{max_single:,.0f}", sublabel="单笔最大",
    )
    freq_ring = render_ring_chart(
        value=order_freq, threshold=max_freq,
        label=f"{order_freq}", sublabel="下单频率",
    )

    # 断路器状态（从 stop_signals 推断）
    any_triggered = stop_signals.get("any_triggered", False)
    circuit_status = "触发" if any_triggered else "正常"
    circuit_color = COLORS["danger"] if any_triggered else COLORS["success"]
    circuit_bg = "#fee2e2" if any_triggered else "#d1fae5"

    circuit_card = f"""
    <div style="background:{circuit_bg};padding:16px;border-radius:8px;
                border-left:3px solid {circuit_color};text-align:center;">
        <div style="font-size:12px;color:#6b7280;margin-bottom:4px;">断路器状态</div>
        <div style="font-size:20px;font-weight:700;color:{circuit_color};">{circuit_status}</div>
    </div>
    """

    # 止损信号列表
    alerts_html = ""
    if stop_signals:
        portfolio_stop = stop_signals.get("portfolio_stop", {}) or {}
        individual_stops = stop_signals.get("individual_stops", {}) or {}

        if portfolio_stop and portfolio_stop.get("triggered"):
            alerts_html += render_alert_item(
                severity="od",
                title="组合日亏损止损触发",
                detail=f"当前日亏损: {portfolio_stop.get('daily_return', 0) * 100:.2f}% · "
                       f"阈值: {portfolio_stop.get('threshold', 0) * 100:.2f}%",
                suggestion=portfolio_stop.get("reason") or "暂停下单",
            )

        triggered_codes = [c for c, t in individual_stops.items() if t]
        if triggered_codes:
            alerts_html += render_alert_item(
                severity="od",
                title=f"个股止损触发（{len(triggered_codes)} 只）",
                detail=", ".join(triggered_codes[:10]) + ("..." if len(triggered_codes) > 10 else ""),
                suggestion="检查持仓并执行卖出",
            )

        if not any_triggered:
            alerts_html += render_alert_item(
                severity="ok",
                title="无止损信号触发",
                detail="所有风控指标正常",
            )
    else:
        alerts_html = '<div class="placeholder">无止损信号数据</div>'

    return f"""
    <div class="section">
        <h2>风控指标</h2>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
                    gap:16px;margin-bottom:20px;">
            <div style="text-align:center;">{daily_loss_ring}</div>
            <div style="text-align:center;">{single_order_ring}</div>
            <div style="text-align:center;">{freq_ring}</div>
            {circuit_card}
        </div>
        <h3 style="margin-top:24px;font-size:15px;color:#374151;">止损信号</h3>
        {alerts_html}
    </div>
    """


def build_execution_html(
    status_bar_html: str,
    account_dashboard_html: str,
    risk_dashboard_html: str,
    mode: str,
    backend: str,
    p1p2_html: str = "",
    nav_curve_html: str = "",
) -> str:
    """构建执行监控报告完整 HTML。

    参数:
        p1p2_html: P1/P2 扩展区块 HTML 片段（持仓明细、当日成交、订单面板），
                  为空字符串时不渲染该区块。
        nav_curve_html: 净值曲线模块 HTML 片段（F-P1-4），置于 header 与账户概览之间。
    """
    from datetime import datetime
    from scripts.templates.common_components import (
        build_page_css, build_nav_bar_html, build_footer_html,
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    page_css = build_page_css()
    nav_html = build_nav_bar_html()
    footer_html = build_footer_html(disclaimer=(
        "本报告由 JingniTrader 实时/模拟交易监控系统自动生成，仅供学习研究用途，不构成任何投资建议或操作指令。"
        "报告中的账户资金、持仓、风险指标等数据可能存在传输延迟、数据源偏差或统计口径差异，"
        "不应作为实时交易决策的唯一依据。已触发的风控信号（如止损、风险预警）仅提示风险，"
        "不代表必须执行相应操作。实盘交易有风险，请结合自身情况谨慎决策。"
    ))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交易监控报告</title>
<style>
    /* 统一页面布局 + 导航栏 + 底部版权栏（与其余报告一致） */
    {page_css}
    /* ── 交易监控报告特有样式 ── */
    .placeholder {{ padding: 24px; text-align: center; color: var(--text-muted);
                   background: #f9fafb; border-radius: 8px; font-size: 13px; }}
</style>
</head>
<body>
{nav_html}

<div class="header">
    <h1>交易监控报告</h1>
    <p>生成时间: {now_str}</p>
</div>

{status_bar_html}

{account_dashboard_html}

{nav_curve_html}

{risk_dashboard_html}

{p1p2_html}

{footer_html}
</body>
</html>"""


def build_execution_report(
    execution_metadata: Dict[str, Any],
    audit_log_path: Optional[str] = None,
    ledger_path: Optional[str] = None,
    stop_signals: Optional[Dict[str, Any]] = None,
    max_daily_loss_ratio: float = DEFAULT_MAX_DAILY_LOSS_RATIO,
    max_single_order_ratio: float = DEFAULT_MAX_SINGLE_ORDER_RATIO,
    max_order_frequency: int = DEFAULT_MAX_ORDER_FREQUENCY,
    data_path: Optional[str] = None,
    target_weights: Optional[Dict[str, float]] = None,
    include_p1: bool = True,
    include_p2: bool = True,
    chart_theme: str = "plotly_white",
    account_state_path: Optional[str] = None,
    enable_live_polling: Optional[bool] = None,
    live_poll_interval: Optional[int] = None,
    live_status_port: Optional[int] = None,
    serve_blocking: bool = False,
    live_mode: str = "http",
    live_snapshot_file: str = "snapshot.js",
) -> str:
    """构建执行监控报告主入口。

    参数:
        execution_metadata: execution-monitor-engine run() 返回的 metadata
        audit_log_path: trade_log.jsonl 路径
        ledger_path: ledger.jsonl 路径（paper 模式）
        stop_signals: 来自 portfolio-risk-engine 的 stop_signals（跨引擎引用）
        max_daily_loss_ratio: 日亏损率阈值
        max_single_order_ratio: 单笔订单占比阈值
        max_order_frequency: 下单频率阈值
        data_path: DATA 产物路径（可选，P1 持仓明细需要最新价）
        target_weights: portfolio_weights.json 的目标权重（P1 持仓明细需要）
        include_p1: 是否包含 P1 功能（持仓明细、净值曲线）
        include_p2: 是否包含 P2 功能（当日成交、订单面板）
        chart_theme: Plotly 主题
        account_state_path: account_state.json 路径（LIVE 模式轮询需要，启用时启动 StatusServer）
        enable_live_polling: 显式启用/禁用 LIVE 轮询；None 时自动判断（mode==live 或 TRADE_MODE=live）
        live_poll_interval: 轮询间隔秒数；None 时读 QUANT_EXEC_REPORT_POLL_INTERVAL（默认 5）
        live_status_port: 状态服务端口；None 时读 QUANT_EXEC_REPORT_STATUS_PORT（0=自动探测）
        serve_blocking: LIVE 模式下是否阻塞主线程保持服务运行（用户浏览器访问 http://127.0.0.1:{port}/）
                       默认 False（仅启动后台守护线程）；True 时阻塞直到 Ctrl+C
        live_mode: LIVE 数据传输方式："http"（启动 StatusServer，fetch 轮询）或
                  "jsonp"（写 snapshot.js 文件，<script> 标签轮询，file:// 协议双击打开）
        live_snapshot_file: JSONP 模式下 snapshot.js 的相对路径（相对于 HTML 所在目录）

    返回:
        完整 HTML 字符串
    """
    import os

    # 从 metadata 提取数据（数据缺失容错）
    mode = execution_metadata.get("mode", "paper")
    backend = os.environ.get("TRADE_BACKEND", "paper" if mode == "paper" else "xtquant")
    available = execution_metadata.get("backend_available", mode == "live")
    account_snapshot = execution_metadata.get("account_snapshot", {}) or {}
    orders_executed = int(execution_metadata.get("orders_executed", 0) or 0)
    orders_failed = int(execution_metadata.get("orders_failed", 0) or 0)

    # 加载审计日志
    trade_log = load_trade_log(audit_log_path)

    # 派生风控指标
    risk_metrics = derive_risk_metrics(
        account_snapshot=account_snapshot,
        trade_log=trade_log,
        max_daily_loss_ratio=max_daily_loss_ratio,
        max_single_order_ratio=max_single_order_ratio,
        max_order_frequency=max_order_frequency,
    )

    # 止损信号（跨引擎引用，缺失时为空 dict）
    stop_signals_data = stop_signals or {}

    # 判断是否启用 LIVE 轮询
    if enable_live_polling is None:
        env_trade_mode = os.environ.get("TRADE_MODE", "")
        enable_live = (mode == "live") or (env_trade_mode.lower() == "live")
    else:
        enable_live = bool(enable_live_polling)

    # LIVE 模式：启动状态服务 + 生成动态状态条
    status_bar_html = ""
    live_server = None
    live_port = 0
    if enable_live:
        if live_mode == "jsonp":
            # JSONP 模式：写 snapshot.js 文件，<script> 标签轮询，无需 HTTP 服务器
            try:
                from scripts.templates.live_polling_jsonp import build_jsonp_live_status_html

                if live_poll_interval is None:
                    live_poll_interval = 3

                status_bar_html = build_jsonp_live_status_html(
                    snapshot_file=live_snapshot_file,
                    poll_interval=live_poll_interval,
                    mode=mode,
                    backend=backend,
                )
                logger.info(f"LIVE JSONP 模式：snapshot_file={live_snapshot_file}")
            except Exception as e:
                logger.warning(f"LIVE JSONP 模式初始化失败，降级为静态状态条: {e}")
                status_bar_html = build_status_bar_html(mode=mode, backend=backend, available=available)
        else:
            # HTTP 模式：启动 StatusServer，fetch 轮询（原方案）
            try:
                from scripts.templates.live_polling import render_live_block, should_enable_live_polling
                from scripts.status_server import StatusServer

                # 读取轮询配置
                if live_poll_interval is None:
                    try:
                        from scripts.config import EXEC_REPORT_POLL_INTERVAL
                        live_poll_interval = EXEC_REPORT_POLL_INTERVAL
                    except Exception:
                        live_poll_interval = 5
                if live_status_port is None:
                    try:
                        from scripts.config import EXEC_REPORT_STATUS_PORT
                        live_status_port = EXEC_REPORT_STATUS_PORT
                    except Exception:
                        live_status_port = 0

                # 推断 account_state_path（未显式传入时从 audit_log_path 同目录推断）
                state_path = account_state_path
                if not state_path and audit_log_path:
                    state_path = os.path.join(os.path.dirname(audit_log_path), "account_state.json")

                # 启动状态服务（后台守护线程）
                live_server = StatusServer(
                    account_state_path=state_path or "",
                    audit_log_path=audit_log_path or "",
                    port=live_status_port,
                    max_daily_loss_ratio=max_daily_loss_ratio,
                    max_single_order_ratio=max_single_order_ratio,
                    max_order_frequency=max_order_frequency,
                )
                live_port = live_server.start()
                logger.info(f"LIVE 状态服务监听 127.0.0.1:{live_port}")

                # 生成动态状态条 + 轮询脚本（替换静态状态条）
                status_bar_html = render_live_block(
                    port=live_port,
                    poll_interval=live_poll_interval,
                    mode=mode,
                    backend=backend,
                )
            except Exception as e:
                logger.warning(f"LIVE 状态服务启动失败，降级为静态状态条: {e}")
                status_bar_html = build_status_bar_html(mode=mode, backend=backend, available=available)
                live_server = None
    else:
        # PAPER 模式：静态状态条
        status_bar_html = build_status_bar_html(mode=mode, backend=backend, available=available)

    account_dashboard_html = build_account_dashboard_html(
        account_snapshot=account_snapshot,
        orders_executed=orders_executed,
        orders_failed=orders_failed,
        trade_log=trade_log,
    )
    risk_dashboard_html = build_risk_dashboard_html(
        risk_metrics=risk_metrics,
        stop_signals=stop_signals_data,
    )

    # P1/P2 扩展图表（数据缺失时降级为占位符）
    p1p2_html = ""
    nav_curve_html = ""  # F-P1-4 净值曲线（独立模块，置于 header 与账户概览之间）
    if include_p1 or include_p2:
        target_w = target_weights or {}
        ledger_records = load_ledger(ledger_path)

        if include_p1:
            # F-P1-1: 持仓明细表
            try:
                from scripts.templates.execution_charts_p1p2 import build_holdings_table_html
                p1p2_html += build_holdings_table_html(
                    account_snapshot=account_snapshot,
                    target_weights=target_w,
                    data_path=data_path,
                    trade_log=trade_log,
                )
            except Exception as e:
                logger.warning(f"F-P1-1 持仓明细生成失败: {e}")
                p1p2_html += '<div class="section"><h2>持仓明细</h2><div class="placeholder">生成失败</div></div>'

            # F-P1-4: 净值曲线（独立模块，置于 header 与账户概览之间）
            try:
                from scripts.templates.execution_charts_p1p2 import build_nav_curve_chart
                nav_html = build_nav_curve_chart(ledger_path, chart_theme=chart_theme)
                nav_curve_html = f'<div class="section"><h2>净值曲线</h2><div class="chart-container">{nav_html}</div></div>'
            except Exception as e:
                logger.warning(f"F-P1-4 净值曲线生成失败: {e}")
                nav_curve_html = '<div class="section"><h2>净值曲线</h2><div class="placeholder">生成失败</div></div>'

        if include_p2:
            # F-P2-4: 当日成交分析
            try:
                from scripts.templates.execution_charts_p1p2 import build_trade_review_html
                p1p2_html += build_trade_review_html(ledger_path, trade_log)
            except Exception as e:
                logger.warning(f"F-P2-4 当日成交生成失败: {e}")
                p1p2_html += '<div class="section"><h2>当日成交</h2><div class="placeholder">生成失败</div></div>'

            # F-P2-5: 当日委托
            try:
                from scripts.templates.execution_charts_p1p2 import build_order_panel_html
                p1p2_html += build_order_panel_html(
                    trade_log=trade_log,
                    ledger_records=ledger_records,
                    orders_executed=orders_executed,
                    orders_failed=orders_failed,
                )
            except Exception as e:
                logger.warning(f"F-P2-5 当日委托生成失败: {e}")
                p1p2_html += '<div class="section"><h2>当日委托</h2><div class="placeholder">生成失败</div></div>'

    html = build_execution_html(
        status_bar_html=status_bar_html,
        account_dashboard_html=account_dashboard_html,
        risk_dashboard_html=risk_dashboard_html,
        mode=mode,
        backend=backend,
        p1p2_html=p1p2_html,
        nav_curve_html=nav_curve_html,
    )

    # LIVE 模式：将生成的 HTML 托管到状态服务，按需阻塞主线程
    if live_server is not None:
        live_server.set_report_html(html)
        if serve_blocking:
            print(f"\n{'='*60}")
            print(f"LIVE 实时报告已就绪，请用浏览器访问:")
            print(f"  http://127.0.0.1:{live_port}/")
            print(f"按 Ctrl+C 退出服务")
            print(f"{'='*60}\n")
            live_server.serve_forever_blocking()

    return html
