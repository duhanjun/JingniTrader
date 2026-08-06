"""
报告引擎专属配置
"""
import os

_WORK_DIR = os.environ.get("QUANT_WORK_DIR", "./workspace")
REPORT_DIR = os.environ.get("REPORT_DIR", os.path.join(_WORK_DIR, "reports"))
REPORT_TITLE = os.environ.get("REPORT_TITLE", "策略回测报告")
REPORT_FORMAT = os.environ.get("REPORT_FORMAT", "html")
INDUSTRY_STANDARD = os.environ.get("INDUSTRY_STANDARD", "sw")
BENCHMARK = os.environ.get("BENCHMARK", "000300.SH")
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", 0.03))
INCLUDE_HEATMAP = os.environ.get("INCLUDE_HEATMAP", "true").lower() == "true"
INCLUDE_ATTRIBUTION = os.environ.get("INCLUDE_ATTRIBUTION", "true").lower() == "true"
CHART_THEME = os.environ.get("CHART_THEME", "plotly_white")
# 报告插件机制开关（true 时启用 plugins/ 目录下的报告插件）
ENABLE_PLUGIN = os.environ.get("ENABLE_PLUGIN", "true").lower() == "true"

# LIVE 模式实时状态服务（仅 TRADE_MODE=live 时激活）
# 轮询间隔（秒），前端 setInterval 据此刷新
EXEC_REPORT_POLL_INTERVAL = int(os.environ.get("QUANT_EXEC_REPORT_POLL_INTERVAL", "5"))
# 状态服务端口；未设置（或设为 0）时自动探测空闲端口
EXEC_REPORT_STATUS_PORT = int(os.environ.get("QUANT_EXEC_REPORT_STATUS_PORT", "0"))

os.makedirs(REPORT_DIR, exist_ok=True)