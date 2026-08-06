"""
LIVE 模式前端轮询脚本生成器

生成嵌入到 execution_report.html 的 <script> 片段，实现：
- setInterval 定时调用 /api/snapshot
- 更新状态条连接灯（绿/红/灰）+ 自动刷新倒计时
- 更新账户概览四宫格、风控仪表盘环形图
- 状态服务不可用时显示断连提示并降级为静态视图
- 不抛 JS 错误（catch 所有异常）

脚本零外部依赖（原生 fetch + DOM API）。
"""
from __future__ import annotations

import json
from typing import Any, Dict


def build_polling_script(
    port: int,
    poll_interval: int = 5,
) -> str:
    """生成 LIVE 模式前端轮询脚本。

    采用相对路径 /api/snapshot（同源访问），避免 file:// 协议的 CORS 拦截。
    要求 HTML 由 StatusServer 通过 http://127.0.0.1:{port}/ 托管。

    参数:
        port: 状态服务端口（仅用于日志显示，fetch 使用相对路径）
        poll_interval: 轮询间隔（秒）

    返回:
        嵌入 HTML 的 <script> 字符串
    """
    return f"""
<script>
(function() {{
    'use strict';
    // 同源访问：HTML 与 API 共用 http://127.0.0.1:{port}/
    var STATUS_BASE = "";  // 相对路径，避免 CORS
    var POLL_INTERVAL = {poll_interval};  // 秒
    var countdown = POLL_INTERVAL;
    var connected = false;

    function updateStatusLamp(state) {{
        var lamp = document.getElementById('status-lamp');
        if (!lamp) return;
        lamp.className = 'status-lamp ' + state;
        // state: 'connected' (绿脉冲) / 'disconnected' (红) / 'idle' (灰)
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
            bar.textContent = msg || '状态服务不可用，已降级为静态视图';
            bar.style.color = '#ef4444';
        }}
        var syncBtn = document.getElementById('sync-btn');
        if (syncBtn) syncBtn.style.display = 'none';
    }}

    function showConnected() {{
        connected = true;
        updateStatusLamp('connected');
        var bar = document.getElementById('status-message');
        if (bar) {{
            bar.textContent = '实时同步中';
            bar.style.color = '#10b981';
        }}
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
            if (navEl) navEl.textContent = '¥' + nav.toFixed(2);
            var changeEl = document.getElementById('metric-nav-change');
            if (changeEl) {{
                changeEl.textContent = (change >= 0 ? '+' : '') + change.toFixed(2) + ' (' + changePct + ')';
                changeEl.style.color = change >= 0 ? '#e02b2b' : '#12a05c';
            }}

            var cashEl = document.getElementById('metric-cash');
            if (cashEl) cashEl.textContent = '¥' + cash.toFixed(2);

            var execEl = document.getElementById('metric-orders');
            if (execEl) {{
                var total = snap.orders_executed + snap.orders_failed;
                var rate = total > 0 ? (snap.orders_executed / total * 100).toFixed(1) + '%' : '--';
                execEl.textContent = snap.orders_executed + '/' + total + ' (' + rate + ')';
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
                // 更新环形图 stroke-dashoffset
                var circ = 2 * Math.PI * 15;  // r=15
                var offset = circ * (1 - Math.min(usage, 1));
                ringEl.style.strokeDashoffset = offset;
                ringEl.style.stroke = usage >= 1 ? '#ef4444' : (usage >= 0.8 ? '#f59e0b' : '#10b981');
            }}
        }} catch (e) {{
            console.warn('更新风控仪表盘失败:', e);
        }}
    }}

    function poll() {{
        fetch(STATUS_BASE + '/api/snapshot', {{ cache: 'no-store' }})
            .then(function(r) {{
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            }})
            .then(function(snap) {{
                showConnected();
                updateAccountDashboard(snap);
                updateRiskDashboard(snap);
            }})
            .catch(function(err) {{
                showDisconnect('状态服务断连: ' + err.message);
            }});
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


def build_live_status_html(
    port: int,
    poll_interval: int = 5,
    mode: str = "live",
    backend: str = "xtquant",
) -> str:
    """生成 LIVE 模式状态条 HTML（含动态指示灯、倒计时、同步按钮）+ 轮询脚本。

    参数:
        port: 状态服务端口
        poll_interval: 轮询间隔（秒）
        mode: 交易模式
        backend: 执行后端
    """
    polling_script = build_polling_script(port, poll_interval)
    return f"""
<div class="status-bar live" id="live-status-bar">
    <span class="status-lamp idle" id="status-lamp"></span>
    <span class="status-text">
        <strong>{mode.upper()}</strong> · {backend}
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
    background: #9ca3af;  /* idle 灰 */
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
</style>
{polling_script}
"""


def should_enable_live_polling(mode: str, env_trade_mode: str = "") -> bool:
    """判断是否应启用 LIVE 模式轮询。

    启用条件：execution_metadata.mode == 'live' 或环境变量 TRADE_MODE=live
    """
    if mode == "live":
        return True
    if env_trade_mode.lower() == "live":
        return True
    return False


def render_live_block(
    port: int,
    poll_interval: int,
    mode: str,
    backend: str,
) -> str:
    """渲染 LIVE 模式状态条 + 轮询脚本（供 build_execution_report 调用）。"""
    return build_live_status_html(
        port=port,
        poll_interval=poll_interval,
        mode=mode,
        backend=backend,
    )
