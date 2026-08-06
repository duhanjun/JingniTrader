"""
LIVE 模式轻量 HTTP 状态服务（仅 TRADE_MODE=live 时激活）

同一服务同时承担两个职责：
1. 托管执行监控 HTML 报告（GET /）→ 用户用浏览器访问 http://127.0.0.1:{port}/
2. 提供实时状态快照 API（GET /api/health, GET /api/snapshot）

同源设计：HTML 与 API 共用一个端口，前端 fetch 使用相对路径 "/api/snapshot"，
避免 file:// 协议打开 HTML 时的 CORS 跨协议拦截。

暴露接口：
- GET /            → 200 text/html（执行监控报告，内嵌轮询脚本）
- GET /api/health  → 200 {"status":"ok","ts":...}（健康检查）
- GET /api/snapshot → 200 {account_snapshot, orders_executed, orders_failed, risk_metrics, ts}

设计要点：
- 仅绑定 127.0.0.1（本地回环，不对外暴露）
- 端口自动探测（_find_free_port）；未占用时立即让出
- 默认后台守护线程；LIVE 模式可通过 serve_forever_blocking() 阻塞主线程
- allow_reuse_address=True，避免与残留进程冲突
- 单次响应 < 200ms（直接读 account_state.json，无重计算）
- CORS 头设为 *（防御性，同源时不需要）
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

logger = logging.getLogger("reports-engine.status_server")


def _find_free_port(start: int = 0, end: int = 0) -> int:
    """自动探测空闲端口。

    参数:
        start: 起始端口（含）；0 表示由 OS 自动分配
        end: 结束端口（不含）；0 表示无上限
    返回:
        可用端口号
    """
    if start <= 0:
        # 直接让 OS 分配
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    for port in range(start, end if end > 0 else 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无可用端口 (range={start}-{end})")


def load_account_state(account_state_path: str) -> Dict[str, Any]:
    """读取最新账户状态快照。"""
    if not account_state_path or not os.path.exists(account_state_path):
        return {}
    try:
        with open(account_state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取 account_state.json 失败: {e}")
        return {}


def load_trade_log_stats(audit_log_path: str) -> Dict[str, int]:
    """从审计日志统计订单成功/失败数。"""
    stats = {"orders_executed": 0, "orders_failed": 0}
    if not audit_log_path or not os.path.exists(audit_log_path):
        return stats
    try:
        with open(audit_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    status = rec.get("status", "")
                    if status == "filled":
                        stats["orders_executed"] += 1
                    elif status in ("rejected", "cancelled"):
                        stats["orders_failed"] += 1
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"读取 trade_log.jsonl 失败: {e}")
    return stats


def build_snapshot(
    account_state_path: str,
    audit_log_path: str,
    execution_dir: Optional[str] = None,
    max_daily_loss_ratio: float = 0.02,
    max_single_order_ratio: float = 0.10,
    max_order_frequency: int = 2,
) -> Dict[str, Any]:
    """构建一次状态快照（供 /api/snapshot 返回）。

    直接读盘，无重计算，保证 < 200ms 响应。
    """
    import time

    account = load_account_state(account_state_path)
    order_stats = load_trade_log_stats(audit_log_path)

    # 派生日亏损率（仅用 account_state 字段，不回放 ledger）
    nav = float(account.get("nav", 0))
    start_of_day_nav = float(account.get("start_of_day_nav", nav))
    daily_loss_ratio = 0.0
    if start_of_day_nav > 0:
        daily_loss_ratio = (nav - start_of_day_nav) / start_of_day_nav

    # 风控阈值占用比例（用于前端环形图）
    risk_metrics = {
        "daily_loss_ratio": daily_loss_ratio,
        "daily_loss_threshold": max_daily_loss_ratio,
        "daily_loss_usage": (
            abs(daily_loss_ratio) / max_daily_loss_ratio if max_daily_loss_ratio > 0 else 0
        ),
        "max_single_order_ratio": max_single_order_ratio,
        "max_order_frequency": max_order_frequency,
    }

    return {
        "account_snapshot": account,
        "orders_executed": order_stats["orders_executed"],
        "orders_failed": order_stats["orders_failed"],
        "risk_metrics": risk_metrics,
        "ts": time.time(),
    }


class _SnapshotHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器：托管 HTML 报告 + 状态 API。"""

    # 通过类变量传递依赖（避免全局状态）
    _snapshot_builder = None  # type: Optional[callable]
    _report_html = ""  # type: str

    def log_message(self, format, *args):  # noqa: A003
        # 静默默认日志（避免污染 stdout）
        pass

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            html = self._report_html or "<html><body><h1>报告生成中...</h1></body></html>"
            self._send_html(html)
        elif path == "/api/health":
            import time
            self._send_json(200, {"status": "ok", "ts": time.time()})
        elif path == "/api/snapshot":
            if self._snapshot_builder is None:
                self._send_json(503, {"error": "snapshot builder 未初始化"})
                return
            try:
                snapshot = self._snapshot_builder()
                self._send_json(200, snapshot)
            except Exception as e:
                logger.exception("构建 snapshot 失败")
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": f"未知路径: {path}"})


class StatusServer:
    """LIVE 模式状态服务管理器（同时托管 HTML 报告 + 状态 API）。

    用法（LIVE 模式阻塞主线程）:
        server = StatusServer(account_state_path, audit_log_path)
        port = server.start()
        server.set_report_html(html)  # 更新托管的 HTML 报告
        print(f"访问 http://127.0.0.1:{port}/ 查看实时报告")
        server.serve_forever_blocking()  # 阻塞主线程，Ctrl+C 退出

    用法（后台运行，仅启动 API）:
        server = StatusServer(account_state_path, audit_log_path)
        port = server.start()
        # ... 主线程做其他事 ...
        server.stop()
    """

    def __init__(
        self,
        account_state_path: str,
        audit_log_path: str,
        port: int = 0,
        max_daily_loss_ratio: float = 0.02,
        max_single_order_ratio: float = 0.10,
        max_order_frequency: int = 2,
        snapshot_builder: Optional[Any] = None,
    ) -> None:
        self._account_state_path = account_state_path
        self._audit_log_path = audit_log_path
        self._requested_port = port
        self._max_daily_loss_ratio = max_daily_loss_ratio
        self._max_single_order_ratio = max_single_order_ratio
        self._max_order_frequency = max_order_frequency
        self._custom_snapshot_builder = snapshot_builder  # 自定义数据源（如 xtquant 实时拉取）

        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port: int = 0
        self._handler_cls: Optional[type] = None  # 保留引用以便 set_report_html

    @property
    def port(self) -> int:
        """实际监听端口（start() 调用后才有值）。"""
        return self._port

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _build_snapshot(self) -> Dict[str, Any]:
        if self._custom_snapshot_builder is not None:
            return self._custom_snapshot_builder()
        return build_snapshot(
            account_state_path=self._account_state_path,
            audit_log_path=self._audit_log_path,
            max_daily_loss_ratio=self._max_daily_loss_ratio,
            max_single_order_ratio=self._max_single_order_ratio,
            max_order_frequency=self._max_order_frequency,
        )

    def set_report_html(self, html: str) -> None:
        """更新托管的 HTML 报告内容（运行时动态更新，无需重启服务）。

        参数:
            html: 完整的 HTML 字符串
        """
        if self._handler_cls is not None:
            self._handler_cls._report_html = html
            logger.info(f"已更新托管报告 ({len(html)} 字符)")
        else:
            logger.warning("服务未启动，无法设置报告 HTML")

    def start(self) -> int:
        """启动 HTTP 服务（后台守护线程）。

        返回: 实际监听端口
        异常: 端口探测失败时抛 RuntimeError
        """
        if self.running:
            return self._port

        # 探测端口
        if self._requested_port > 0:
            port = self._requested_port
        else:
            port = _find_free_port()

        # 构建-handler 类，注入 snapshot builder
        class _BoundHandler(_SnapshotHandler):
            _snapshot_builder = self._build_snapshot  # type: ignore
            _report_html = ""

        self._handler_cls = _BoundHandler

        # 创建 HTTPServer，仅绑定 127.0.0.1
        HTTPServer.allow_reuse_address = True
        self._server = HTTPServer(("127.0.0.1", port), _BoundHandler)
        self._port = port

        # 启动守护线程
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="reports-status-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"LIVE 状态服务已启动: http://127.0.0.1:{port}")
        return port

    def serve_forever_blocking(self) -> None:
        """阻塞主线程运行服务，直到 Ctrl+C 或 stop() 调用。

        适用于 LIVE 模式：报告生成完毕后调用此方法保持服务运行，
        用户通过浏览器访问 http://127.0.0.1:{port}/ 查看实时报告。

        实现说明：daemon 线程已在 serve_forever() 处理请求，
        主线程只需等待 KeyboardInterrupt，避免重复调用 serve_forever()。
        """
        if self._server is None or self._thread is None:
            logger.error("服务未启动，无法阻塞运行")
            return
        try:
            logger.info(f"服务阻塞运行中，访问 http://127.0.0.1:{self._port}/ 查看报告 (Ctrl+C 退出)")
            # 主线程等待，daemon 线程持续处理 HTTP 请求
            while self._thread.is_alive():
                self._thread.join(timeout=1.0)
        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，准备关闭服务...")
        finally:
            self.stop()

    def stop(self) -> None:
        """停止服务（关闭 socket 并等待线程退出）。"""
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                logger.warning(f"关闭状态服务异常: {e}")
            finally:
                self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._port = 0
