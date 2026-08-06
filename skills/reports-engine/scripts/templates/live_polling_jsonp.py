"""LIVE 模式 JSONP 轮询脚本生成器

混合架构核心模块：
- 替代 live_polling.py 的 fetch 方案
- 通过 <script src="snapshot.js?ts=..."> 标签加载 JSONP 文件
- 绕过 file:// 协议的 CORS 限制，HTML 双击即看，无需 HTTP 服务器

工作原理：
1. Python 后台进程每 3 秒调用 LiveMonitor.write_jsonp_snapshot() 覆盖 snapshot.js
2. HTML 内嵌的轮询脚本动态创建 <script> 标签加载 snapshot.js
3. 加载完成后 window.__LIVE_SNAPSHOT 被赋值，触发 DOM 更新
4. 失败时（文件被占用或不存在）降级为静态视图，等待下次重试

优势：
- 零 HTTP 服务器依赖，file:// 协议双击打开 HTML 即可实时刷新
- <script> 标签不受同源策略约束，兼容所有主流浏览器
- 原子写入 + 时间戳防缓存，确保数据一致性

限制：
- snapshot.js 必须与 HTML 在同目录或子目录（Chrome 在 file:// 下拒绝跨目录）
- 首次加载多 1-2 秒延迟（script 标签加载比 fetch 稍慢）
"""
from __future__ import annotations

from typing import Any, Dict


def build_jsonp_polling_script(
    snapshot_file: str = "snapshot.js",
    poll_interval: int = 3,
) -> str:
    """生成 JSONP 轮询脚本。

    通过动态创建 <script> 标签加载 snapshot.js，绕过 file:// 协议的 CORS 限制。
    加载成功后从 window.__LIVE_SNAPSHOT 读取数据并更新 DOM。

    参数:
        snapshot_file: snapshot.js 的相对路径（相对于 HTML 文件所在目录）
        poll_interval: 轮询间隔（秒）

    返回:
        嵌入 HTML 的 <script> 字符串
    """
    return f"""
<script>
(function() {{
    'use strict';
    var SNAPSHOT_FILE = "{snapshot_file}";
    var POLL_INTERVAL = {poll_interval};  // 秒
    var countdown = POLL_INTERVAL;
    var connected = false;
    var lastSnapshot = null;

    function updateStatusLamp(state) {{
        var lamp = document.getElementById('status-lamp');
        if (!lamp) return;
        lamp.className = 'status-lamp ' + state;
    }}

    function updateCountdown() {{
        var el = document.getElementById('refresh-countdown');
        if (el) el.textContent = countdown + 's';
        if (countdown > 0) countdown--;
    }}

    function showDisconnect(msg) {{
        connected = false;
        updateStatusLamp('disconnected');
        var bar = document.getElementById('status-message');
        if (bar) {{
            bar.textContent = msg || '数据文件不可用，等待重试...';
            bar.style.color = '#ef4444';
        }}
    }}

    function showConnected() {{
        if (!connected) {{
            connected = true;
            updateStatusLamp('connected');
            var bar = document.getElementById('status-message');
            if (bar) {{
                bar.textContent = '实时同步中';
                bar.style.color = '#10b981';
            }}
        }}
    }}

    function formatMoney(v) {{
        var n = parseFloat(v || 0);
        return '¥' + n.toFixed(2);
    }}

    function updateAccountDashboard(snap) {{
        try {{
            var acc = snap.account_snapshot || {{}};
            var nav = acc.nav || 0;
            var cash = acc.available_cash || 0;
            var startNav = acc.start_of_day_nav || nav;
            var change = nav - startNav;
            var changePct = startNav > 0 ? (change / startNav * 100).toFixed(2) + '%' : '--';

            var navEl = document.getElementById('metric-nav');
            if (navEl) navEl.textContent = formatMoney(nav);
            var changeEl = document.getElementById('metric-nav-change');
            if (changeEl) {{
                changeEl.textContent = (change >= 0 ? '+' : '') + change.toFixed(2) + ' (' + changePct + ')';
                changeEl.style.color = change >= 0 ? '#e02b2b' : '#12a05c';
            }}

            var cashEl = document.getElementById('metric-cash');
            if (cashEl) cashEl.textContent = formatMoney(cash);

            var execEl = document.getElementById('metric-orders');
            if (execEl) {{
                var total = snap.orders_executed + snap.orders_failed;
                var rate = total > 0 ? (snap.orders_executed / total * 100).toFixed(1) + '%' : '--';
                execEl.textContent = snap.orders_executed + '/' + total + ' (' + rate + ')';
            }}

            // 持仓明细表（F-P1-1，10列：标的/目标权重/实际权重/偏差/持仓量/市值/浮盈/浮盈%/当日成交/状态）
            var positions = acc.positions || {{}};
            var posTable = document.getElementById('positions-table-body');
            if (posTable) {{
                var html = '';
                var codes = Object.keys(positions);
                if (codes.length === 0) {{
                    posTable.innerHTML = '<tr><td colspan="10" class="empty-row">暂无持仓</td></tr>';
                }} else {{
                    codes.forEach(function(code) {{
                        var p = positions[code];
                        var mv = parseFloat(p.market_value || 0);
                        var vol = parseInt(p.volume || 0);
                        var cost = parseFloat(p.avg_cost || 0);
                        // 实盘数据无目标权重/浮盈信息，显示占位
                        html += '<tr>' +
                            '<td>' + code + '</td>' +
                            '<td>--</td>' +  // 目标权重
                            '<td>' + (nav > 0 ? (mv / nav * 100).toFixed(2) + '%' : '--') + '</td>' +  // 实际权重
                            '<td>--</td>' +  // 偏差
                            '<td>' + vol + '</td>' +
                            '<td>' + formatMoney(mv) + '</td>' +
                            '<td>--</td>' +  // 浮盈
                            '<td>--</td>' +  // 浮盈%
                            '<td>--</td>' +  // 当日成交
                            '<td>持仓中</td>' +  // 状态
                            '</tr>';
                    }});
                    posTable.innerHTML = html;
                }}
            }}
        }} catch (e) {{
            console.warn('更新账户仪表盘失败:', e);
        }}
    }}

    function updateRiskDashboard(snap) {{
        try {{
            var rm = snap.risk_metrics || {{}};
            var usage = rm.daily_loss_usage || 0;
            var ringEl = document.getElementById('risk-ring-daily-loss');
            if (ringEl) {{
                var circ = 2 * Math.PI * 15;
                var offset = circ * (1 - Math.min(usage, 1));
                ringEl.style.strokeDashoffset = offset;
                ringEl.style.stroke = usage >= 1 ? '#ef4444' : (usage >= 0.8 ? '#f59e0b' : '#10b981');
            }}
        }} catch (e) {{
            console.warn('更新风控仪表盘失败:', e);
        }}
    }}

    function updateOrdersTrades(snap) {{
        try {{
            // 当日委托（F-P2-5，10列：时间/订单ID/标的/方向/委托价/委托量/成交价/成交量/状态/滑点）
            var orders = snap.orders || [];
            var ordTable = document.getElementById('orders-table-body');
            if (ordTable) {{
                if (orders.length === 0) {{
                    ordTable.innerHTML = '<tr><td colspan="10" class="empty-row">暂无委托</td></tr>';
                }} else {{
                    ordTable.innerHTML = orders.map(function(o) {{
                        var ts = (o.timestamp || '').slice(11, 19);
                        var meta = o.metadata || {{}};
                        return '<tr>' +
                            '<td>' + ts + '</td>' +
                            '<td>' + (o.order_id || '--') + '</td>' +
                            '<td>' + (o.code || '') + '</td>' +
                            '<td class="' + (o.side === 'buy' ? 'side-buy' : 'side-sell') + '">' +
                                (o.side === 'buy' ? '买入' : '卖出') + '</td>' +
                            '<td>' + parseFloat(meta.order_price || o.price || 0).toFixed(2) + '</td>' +
                            '<td>' + (o.volume || 0) + '</td>' +
                            '<td>' + parseFloat(o.price || 0).toFixed(2) + '</td>' +
                            '<td>' + (meta.traded_volume || 0) + '</td>' +
                            '<td class="status-' + (o.status || 'unknown') + '">' + (o.status || '--') + '</td>' +
                            '<td>--</td>' +  // 滑点（实盘数据无此字段）
                            '</tr>';
                    }}).join('');
                }}
            }}

            // 当日成交（F-P2-4，11列：交易日期/时间/标的/方向/数量/价格/佣金/印花税/滑点成本/滑点bps/总成本）
            var trades = snap.trades || [];
            var trdTable = document.getElementById('trades-table-body');
            if (trdTable) {{
                if (trades.length === 0) {{
                    trdTable.innerHTML = '<tr><td colspan="11" class="empty-row">暂无成交</td></tr>';
                }} else {{
                    trdTable.innerHTML = trades.map(function(t) {{
                        var dateStr = (t.trade_date || '').slice(0, 10);
                        var timeStr = (t.created_at || '').slice(11, 19);
                        var commission = parseFloat(t.commission || 0);
                        var stampTax = parseFloat(t.stamp_tax || 0);
                        var slippageCost = parseFloat(t.slippage_cost || 0);
                        var totalCost = commission + stampTax + slippageCost;
                        return '<tr>' +
                            '<td>' + dateStr + '</td>' +
                            '<td>' + timeStr + '</td>' +
                            '<td>' + (t.code || '') + '</td>' +
                            '<td class="' + (t.side === 'buy' ? 'side-buy' : 'side-sell') + '">' +
                                (t.side === 'buy' ? '买入' : '卖出') + '</td>' +
                            '<td>' + (t.shares || 0) + '</td>' +
                            '<td>' + parseFloat(t.price || 0).toFixed(2) + '</td>' +
                            '<td>' + commission.toFixed(2) + '</td>' +
                            '<td>' + stampTax.toFixed(2) + '</td>' +
                            '<td>' + slippageCost.toFixed(2) + '</td>' +
                            '<td>--</td>' +  // 滑点bps（实盘数据无此字段）
                            '<td>' + totalCost.toFixed(2) + '</td>' +
                            '</tr>';
                    }}).join('');
                }}
            }}
        }} catch (e) {{
            console.warn('更新委托/成交表失败:', e);
        }}
    }}

    function applySnapshot(snap) {{
        lastSnapshot = snap;
        showConnected();
        updateAccountDashboard(snap);
        updateRiskDashboard(snap);
        updateOrdersTrades(snap);
    }}

    function poll() {{
        var script = document.createElement('script');
        script.src = SNAPSHOT_FILE + '?ts=' + Date.now();
        script.onload = function() {{
            if (window.__LIVE_SNAPSHOT) {{
                applySnapshot(window.__LIVE_SNAPSHOT);
            }} else {{
                showDisconnect('snapshot.js 已加载但数据为空');
            }}
            // 清理 script 标签
            if (script.parentNode) script.parentNode.removeChild(script);
        }};
        script.onerror = function() {{
            showDisconnect('数据文件不可用，等待重试...');
            if (script.parentNode) script.parentNode.removeChild(script);
        }};
        document.head.appendChild(script);
    }}

    // 立即轮询一次
    poll();
    // 定时轮询
    setInterval(poll, POLL_INTERVAL * 1000);
    // 倒计时每秒更新
    setInterval(function() {{
        if (countdown <= 0) countdown = POLL_INTERVAL;
        updateCountdown();
    }}, 1000);

    // 手动同步按钮
    var syncBtn = document.getElementById('sync-btn');
    if (syncBtn) {{
        syncBtn.addEventListener('click', function() {{
            countdown = POLL_INTERVAL;
            poll();
        }});
    }}
}})();
</script>
"""


def build_jsonp_live_status_html(
    snapshot_file: str = "snapshot.js",
    poll_interval: int = 3,
    mode: str = "live",
    backend: str = "xtquant",
) -> str:
    """生成 LIVE 模式状态条 HTML（含动态指示灯、倒计时、同步按钮）+ JSONP 轮询脚本。

    参数:
        snapshot_file: snapshot.js 的相对路径（相对于 HTML 文件所在目录）
        poll_interval: 轮询间隔（秒）
        mode: 交易模式
        backend: 执行后端
    """
    polling_script = build_jsonp_polling_script(snapshot_file, poll_interval)
    # PAPER 模式显示"模拟交易"，不显示 backend
    # LIVE 模式显示"实盘交易 · gm" 或 "实盘交易 · xtquant"
    mode_label = "实盘交易" if mode.lower() == "live" else "模拟交易"
    backend_part = f" · {backend}" if mode.lower() == "live" else ""
    return f"""
<div class="status-bar live" id="live-status-bar">
    <span class="status-lamp idle" id="status-lamp"></span>
    <span class="status-text">
        <strong>{mode_label}</strong>{backend_part}
        · <span id="status-message">连接中...</span>
        · 下次刷新 <span id="refresh-countdown">{poll_interval}s</span>
    </span>
    <button id="sync-btn" class="sync-btn">立即同步</button>
</div>
<style>
.status-bar.live {{
    display: flex; align-items: center; gap: 12px;
    padding: 10px 16px; background: #f8fafc;
    border: 1px solid #e2e8f0; border-radius: 8px;
    margin-bottom: 16px;
}}
.status-lamp {{
    width: 12px; height: 12px; border-radius: 50%;
    background: #9ca3af;
    flex-shrink: 0;
}}
.status-lamp.connected {{
    background: #10b981;
    animation: pulse 1.5s ease-in-out infinite;
}}
.status-lamp.disconnected {{
    background: #ef4444;
}}
@keyframes pulse {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.6; transform: scale(1.2); }}
}}
.status-text {{ font-size: 14px; color: #475569; }}
.sync-btn {{
    margin-left: auto; padding: 4px 12px;
    background: #3b82f6; color: white; border: none;
    border-radius: 4px; cursor: pointer; font-size: 13px;
}}
.sync-btn:hover {{ background: #2563eb; }}
.empty-row {{ text-align: center; color: #94a3b8; padding: 16px; }}
.side-buy {{ color: #e02b2b; font-weight: 500; }}
.side-sell {{ color: #12a05c; font-weight: 500; }}
.status-filled {{ color: #10b981; }}
.status-rejected {{ color: #ef4444; }}
.status-pending {{ color: #f59e0b; }}
</style>
{polling_script}
"""
