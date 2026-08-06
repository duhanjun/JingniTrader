"""执行监控报告 P1/P2 扩展图表模块。

实现 PRD 中执行监控的 P1/P2 功能：
- F-P1-1: 持仓明细表（目标权重 vs 实际权重、浮盈、当日成交）
- F-P1-4: 净值曲线与回撤（从 ledger.jsonl 回放重建）
- F-P2-4: 当日成交分析（从 ledger 展示已有字段）
- F-P2-5: 当日委托（从 trade_log + ledger 提取）

数据来源：
- execution-monitor-engine 的 account_snapshot / trade_log.jsonl / ledger.jsonl
- portfolio-risk-engine 的 portfolio_weights.json（目标权重）
- DATA 产物最新价（行情价格，缺失时用 avg_cost 占位）

数据缺失容错：上游不可用时返回占位符，不阻塞报告生成。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger("reports-engine.execution_charts_p1p2")


def _load_latest_prices(data_path: Optional[str]) -> Dict[str, float]:
    """从 DATA 产物加载最新收盘价。

    数据缺失容错：路径不存在时返回空 dict。
    """
    if not data_path or not os.path.exists(data_path):
        return {}
    try:
        df = pd.read_parquet(data_path)
        if "close" not in df.columns or "code" not in df.columns or "date" not in df.columns:
            return {}
        latest = df[df["date"] == df["date"].max()]
        return dict(zip(latest["code"], latest["close"]))
    except Exception as e:
        logger.warning(f"加载最新价失败: {e}")
        return {}


def build_holdings_table_html(
    account_snapshot: Dict[str, Any],
    target_weights: Dict[str, float],
    data_path: Optional[str] = None,
    trade_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """F-P1-1: 持仓明细表。

    列：标的、目标权重 vs 实际权重、持仓数量/市值、浮动盈亏、当日成交、执行状态
    """
    from scripts.renderers.svg_components import COLORS

    positions = account_snapshot.get("positions", {}) or {}
    nav = float(account_snapshot.get("nav", 0) or 0)

    if not positions:
        return '<div class="section"><h2>持仓明细</h2><div class="placeholder">无持仓数据</div></div>'

    # 加载最新价（缺失时用 avg_cost 占位）
    prices = _load_latest_prices(data_path)

    # 当日成交聚合
    daily_volume = {}
    if trade_log:
        for rec in trade_log:
            code = rec.get("code", "")
            side = rec.get("side", "")
            vol = float(rec.get("volume", 0) or 0)
            if code not in daily_volume:
                daily_volume[code] = {"buy": 0, "sell": 0}
            if side in daily_volume[code]:
                daily_volume[code][side] += vol

    rows = ""
    for code, pos in positions.items():
        volume = float(pos.get("volume", 0) or 0)
        avg_cost = float(pos.get("avg_cost", 0) or 0)
        price = prices.get(code, avg_cost)  # 缺失用 avg_cost 占位

        market_value = volume * price
        actual_weight = (market_value / nav) if nav > 0 else 0
        target_w = target_weights.get(code, 0)
        weight_diff = actual_weight - target_w

        floating_pnl = (price - avg_cost) * volume
        floating_pnl_pct = ((price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0

        daily = daily_volume.get(code, {"buy": 0, "sell": 0})
        status = "已成交" if volume > 0 else "无持仓"

        # 权重偏差高亮
        diff_cls = "positive" if weight_diff >= 0 else "negative"
        pnl_cls = "positive" if floating_pnl >= 0 else "negative"

        rows += (
            f'<tr>'
            f'<td>{code}</td>'
            f'<td>{target_w * 100:.2f}%</td>'
            f'<td>{actual_weight * 100:.2f}%</td>'
            f'<td class="{diff_cls}">{weight_diff * 100:+.2f}%</td>'
            f'<td>{int(volume)}</td>'
            f'<td>{market_value:,.2f}</td>'
            f'<td class="{pnl_cls}">{floating_pnl:,.2f}</td>'
            f'<td class="{pnl_cls}">{floating_pnl_pct:+.2f}%</td>'
            f'<td>买 {int(daily["buy"])} / 卖 {int(daily["sell"])}</td>'
            f'<td>{status}</td>'
            f'</tr>'
        )

    return f"""
    <div class="section">
        <h2>持仓明细</h2>
        <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr style="background:#f9f9f9;">
                <th style="padding:8px;text-align:left;">标的</th>
                <th style="padding:8px;">目标权重</th>
                <th style="padding:8px;">实际权重</th>
                <th style="padding:8px;">偏差</th>
                <th style="padding:8px;">持仓量</th>
                <th style="padding:8px;">市值</th>
                <th style="padding:8px;">浮盈</th>
                <th style="padding:8px;">浮盈%</th>
                <th style="padding:8px;">当日成交</th>
                <th style="padding:8px;">状态</th>
            </tr></thead>
            <tbody id="positions-table-body">{rows}</tbody>
        </table>
        </div>
    </div>
    """


def build_nav_curve_chart(
    ledger_path: Optional[str],
    benchmark_path: Optional[str] = None,
    chart_theme: str = "plotly_white",
) -> str:
    """F-P1-4: 净值曲线与回撤。

    从 ledger.jsonl 的 nav_after 字段回放重建净值序列。
    上轴净值曲线，下轴回撤区域图。
    """
    if not ledger_path or not os.path.exists(ledger_path):
        return '<div class="chart-placeholder">无 ledger 数据，无法重建净值曲线</div>'

    # 回放 ledger 重建净值序列
    records = []
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    if "nav_after" in rec and "created_at" in rec:
                        records.append({
                            "timestamp": rec["created_at"],
                            "nav": float(rec["nav_after"]),
                        })
    except Exception as e:
        logger.warning(f"回放 ledger 失败: {e}")
        return '<div class="chart-placeholder">ledger 回放失败</div>'

    if not records:
        return '<div class="chart-placeholder">ledger 无 nav_after 记录</div>'

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # 计算回撤
    df["peak"] = df["nav"].cummax()
    df["drawdown"] = (df["nav"] - df["peak"]) / df["peak"]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.12,
        row_heights=[0.65, 0.35],
        subplot_titles=("净值曲线", "回撤"),
    )

    fig.add_trace(
        go.Scatter(x=df["timestamp"], y=df["nav"], mode="lines",
                   line=dict(color="#3b82f6", width=2), name="净值"),
        row=1, col=1,
    )

    fig.add_trace(
        go.Bar(x=df["timestamp"], y=df["drawdown"] * 100,
               marker_color=["#ef4444" if d < 0 else "#10b981" for d in df["drawdown"]],
               name="回撤"),
        row=2, col=1,
    )

    fig.update_layout(
        height=500, template=chart_theme, showlegend=False,
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="回撤 (%)", row=2, col=1)

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def build_trade_review_html(
    ledger_path: Optional[str],
    trade_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """F-P2-4: 当日成交分析。

    从 ledger.jsonl 展示 PaperTradeRecordV1 已有字段。
    signal_id/reason 为数据缺口，省略相关列。
    """
    if not ledger_path or not os.path.exists(ledger_path):
        return '<div class="section"><h2>当日成交</h2><div class="placeholder">无 ledger 数据</div></div>'

    records = []
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception as e:
        logger.warning(f"加载 ledger 失败: {e}")

    if not records:
        return '<div class="section"><h2>当日成交</h2><div class="placeholder">无成交记录</div></div>'

    rows = ""
    for rec in records[:50]:  # 最多 50 条
        code = rec.get("code", "-")
        side = rec.get("side", "-")
        shares = int(rec.get("shares", 0) or 0)
        price = float(rec.get("price", 0) or 0)
        commission = float(rec.get("commission", 0) or 0)
        stamp_tax = float(rec.get("stamp_tax", 0) or 0)
        slippage = float(rec.get("slippage_cost", 0) or 0)
        trade_date = rec.get("trade_date", "-")
        created_at = rec.get("created_at", "-")

        side_cls = "positive" if side == "buy" else "negative"
        side_label = "买入" if side == "buy" else "卖出"
        total_cost = commission + stamp_tax + slippage

        # 滑点成本率
        order_value = price * shares
        slippage_rate = (slippage / order_value * 10000) if order_value > 0 else 0  # bps

        rows += (
            f'<tr>'
            f'<td>{trade_date}</td>'
            f'<td>{created_at[:19] if isinstance(created_at, str) else "-"}</td>'
            f'<td>{code}</td>'
            f'<td class="{side_cls}">{side_label}</td>'
            f'<td>{shares}</td>'
            f'<td>{price:.2f}</td>'
            f'<td>{commission:.2f}</td>'
            f'<td>{stamp_tax:.2f}</td>'
            f'<td>{slippage:.2f}</td>'
            f'<td>{slippage_rate:.1f}</td>'
            f'<td>{total_cost:.2f}</td>'
            f'</tr>'
        )

    return f"""
    <div class="section">
        <h2>当日成交</h2>
        <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr style="background:#f9f9f9;">
                <th style="padding:8px;text-align:left;">交易日期</th>
                <th style="padding:8px;text-align:left;">时间</th>
                <th style="padding:8px;text-align:left;">标的</th>
                <th style="padding:8px;">方向</th>
                <th style="padding:8px;">数量</th>
                <th style="padding:8px;">价格</th>
                <th style="padding:8px;">佣金</th>
                <th style="padding:8px;">印花税</th>
                <th style="padding:8px;">滑点成本</th>
                <th style="padding:8px;">滑点(bps)</th>
                <th style="padding:8px;">总成本</th>
            </tr></thead>
            <tbody id="trades-table-body">{rows}</tbody>
        </table>
        </div>
    </div>
    """


def build_order_panel_html(
    trade_log: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    orders_executed: int,
    orders_failed: int,
) -> str:
    """F-P2-5: 当日委托。

    订单列表表 + 成交统计卡。
    """
    from scripts.renderers.svg_components import COLORS, render_metric_card

    if not trade_log:
        return '<div class="section"><h2>当日委托</h2><div class="placeholder">无订单数据</div></div>'

    # 订单列表
    rows = ""
    slippage_map = {}
    for rec in ledger_records:
        code = rec.get("code", "")
        if code not in slippage_map:
            slippage_map[code] = []
        slippage_map[code].append(float(rec.get("slippage_cost", 0) or 0))

    for rec in trade_log[:50]:
        timestamp = rec.get("timestamp", "-")
        order_id = rec.get("order_id", "-")
        code = rec.get("code", "-")
        side = rec.get("side", "-")
        volume = int(rec.get("volume", 0) or 0)
        price = float(rec.get("price", 0) or 0)
        status = rec.get("status", "-")
        metadata = rec.get("metadata", {}) or {}
        order_price = float(metadata.get("order_price", price) or price)

        side_cls = "positive" if side == "buy" else "negative"
        side_label = "买入" if side == "buy" else "卖出"
        status_cls = "positive" if status in ("filled", "成交") else "negative"

        # 滑点（从 ledger 关联）
        slip_list = slippage_map.get(code, [])
        slip = slip_list.pop(0) if slip_list else 0

        rows += (
            f'<tr>'
            f'<td>{timestamp[:19] if isinstance(timestamp, str) else "-"}</td>'
            f'<td>{order_id}</td>'
            f'<td>{code}</td>'
            f'<td class="{side_cls}">{side_label}</td>'
            f'<td>{order_price:.2f}</td>'
            f'<td>{volume}</td>'
            f'<td>{price:.2f}</td>'
            f'<td>{volume}</td>'
            f'<td class="{status_cls}">{status}</td>'
            f'<td>{slip:.2f}</td>'
            f'</tr>'
        )

    # 成交统计
    total_turnover = sum(float(r.get("price", 0) or 0) * float(r.get("volume", 0) or 0) for r in trade_log)
    total_slippage = sum(float(r.get("slippage_cost", 0) or 0) for r in ledger_records)
    avg_slippage = (total_slippage / len(ledger_records)) if ledger_records else 0
    total_orders = orders_executed + orders_failed
    fill_rate = (orders_executed / total_orders * 100) if total_orders > 0 else 0

    stats_cards = (
        render_metric_card("总成交额", f"{total_turnover:,.2f}", color=COLORS["primary"]) +
        render_metric_card("平均滑点", f"{avg_slippage:.2f}", color=COLORS["warning"]) +
        render_metric_card("成交率", f"{fill_rate:.1f}%", color=COLORS["success"] if fill_rate >= 80 else COLORS["warning"]) +
        render_metric_card("订单总数", f"{total_orders}", color=COLORS["text_muted"])
    )

    return f"""
    <div class="section">
        <h2>当日委托</h2>
        <div class="metrics-grid">{stats_cards}</div>
        <div style="overflow-x:auto;margin-top:16px;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr style="background:#f9f9f9;">
                <th style="padding:8px;text-align:left;">时间</th>
                <th style="padding:8px;text-align:left;">订单ID</th>
                <th style="padding:8px;text-align:left;">标的</th>
                <th style="padding:8px;">方向</th>
                <th style="padding:8px;">委托价</th>
                <th style="padding:8px;">委托量</th>
                <th style="padding:8px;">成交价</th>
                <th style="padding:8px;">成交量</th>
                <th style="padding:8px;">状态</th>
                <th style="padding:8px;">滑点</th>
            </tr></thead>
            <tbody id="orders-table-body">{rows}</tbody>
        </table>
        </div>
    </div>
    """
