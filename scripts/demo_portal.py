"""演示报告门户多卡片效果

将 workspace/reports/ 下已有的静态报告注册到门户页 manifest，
并生成一份示例组合优化报告和回测报告，展示混合架构下多报告统一管理的效果。

运行方式:
    cd D:\\codebuddy\\jingni-trader
    python scripts/demo_portal.py
"""
from __future__ import annotations

import os
import sys
import importlib.util as ilu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_ENGINE_DIR = os.path.join(ROOT, "skills", "reports-engine")
REPORTS_SCRIPTS = os.path.join(REPORTS_ENGINE_DIR, "scripts")

# 目标报告目录（与 LIVE 模式一致）
WORK_DIR = os.environ.get("QUANT_WORK_DIR", os.path.join(ROOT, "workspace", "e2e_verify"))
REPORT_DIR = os.path.join(WORK_DIR, "reports")


def _load_portal_module():
    """加载 report_portal 模块"""
    spec = ilu.spec_from_file_location(
        "report_portal",
        os.path.join(REPORTS_SCRIPTS, "report_portal.py"),
    )
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    portal = _load_portal_module()
    os.makedirs(REPORT_DIR, exist_ok=True)

    # 1. 注册已有的个股分析报告（技术面 + 基本面）
    src_reports = os.path.join(ROOT, "workspace", "reports")
    registered = []

    tech_src = os.path.join(src_reports, "technical_report.html")
    if os.path.exists(tech_src):
        tech_dst = os.path.join(REPORT_DIR, "technical_report.html")
        _copy_file(tech_src, tech_dst)
        portal.upsert_report(
            report_dir=REPORT_DIR,
            report_type="technical_report",
            file_path=tech_dst,
            title="贵州茅台-技术面分析",
            task_id="demo_20260805",
        )
        registered.append("个股分析(技术面)")

    fund_src = os.path.join(src_reports, "fundamental_report.html")
    if os.path.exists(fund_src):
        fund_dst = os.path.join(REPORT_DIR, "fundamental_report.html")
        _copy_file(fund_src, fund_dst)
        # 同类型会覆盖，用不同 title 但同 type 只保留最后一个
        # 改为单独注册（type 相同会覆盖，这里仅保留技术面作为个股分析代表）

    # 2. 生成一份示例组合优化报告（基于已有 portfolio_weights.json）
    portfolio_path = os.path.join(ROOT, "workspace", "portfolio", "portfolio_weights.json")
    if os.path.exists(portfolio_path):
        _generate_demo_portfolio_report(portal, REPORT_DIR, portfolio_path)
        registered.append("组合优化")

    # 3. 生成一份示例回测绩效报告（基于已有 backtest_result.json）
    backtest_path = os.path.join(ROOT, "workspace", "backtest_results", "backtest_result.json")
    equity_path = os.path.join(ROOT, "workspace", "backtest_results", "equity_curve.parquet")
    if os.path.exists(backtest_path):
        _generate_demo_backtest_report(portal, REPORT_DIR, backtest_path, equity_path)
        registered.append("回测绩效")

    # 4. 生成一份示例执行监控报告（静态版，区别于 LIVE）
    execution_dir = os.path.join(WORK_DIR, "execution")
    ledger_path = os.path.join(execution_dir, "ledger.jsonl")
    if os.path.exists(ledger_path):
        _generate_demo_execution_report(portal, REPORT_DIR, ledger_path)
        registered.append("执行监控(静态)")

    # 5. 刷新门户页
    index_path = portal.generate_portal(REPORT_DIR)

    print(f"\n{'='*60}")
    print(f"报告门户演示完成")
    print(f"已注册 {len(registered)} 份报告: {', '.join(registered)}")
    print(f"门户页: {index_path}")
    print(f"{'='*60}\n")


def _copy_file(src, dst):
    """简单文件复制"""
    import shutil
    shutil.copy2(src, dst)


def _generate_demo_portfolio_report(portal, report_dir, portfolio_path):
    """生成示例组合优化报告"""
    import json
    with open(portfolio_path, "r", encoding="utf-8") as f:
        weights = json.load(f)

    # 简单 HTML 报告
    weights_rows = ""
    if isinstance(weights, dict):
        items = list(weights.items())[:20]
    elif isinstance(weights, list):
        items = [(item.get("code", ""), item.get("weight", 0)) for item in weights[:20]]
    else:
        items = []

    for code, weight in items:
        w = float(weight) * 100 if float(weight) <= 1 else float(weight)
        weights_rows += f"<tr><td>{code}</td><td>{w:.2f}%</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>组合优化报告</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f8fafc; color: #1e293b; }}
.header {{ background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; padding: 30px; border-radius: 12px; margin-bottom: 24px; }}
.section {{ background: white; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
th {{ background: #f9fafb; font-weight: 600; }}
</style></head><body>
<div class="header"><h1>⚖️ 组合优化报告</h1>
<p>生成时间: 演示数据 | 优化方法: 均值方差</p></div>
<div class="section"><h2>权重分布</h2>
<table><thead><tr><th>标的</th><th>权重</th></tr></thead>
<tbody>{weights_rows}</tbody></table>
</div>
<div class="section"><h2>风险指标</h2>
<p>预期年化收益: 15.2% | 年化波动率: 18.5% | 夏普比率: 0.82</p>
<p>最大回撤: -12.3% | Calmar 比率: 1.23</p>
</div>
</body></html>"""

    html_path = os.path.join(report_dir, "portfolio_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    portal.upsert_report(
        report_dir=report_dir,
        report_type="portfolio",
        file_path=html_path,
        title="组合优化报告-均值方差",
        task_id="demo_20260805",
    )


def _generate_demo_backtest_report(portal, report_dir, backtest_path, equity_path):
    """生成示例回测绩效报告"""
    import json
    with open(backtest_path, "r", encoding="utf-8") as f:
        result = json.load(f)

    metrics = result if isinstance(result, dict) else {}
    sharpe = metrics.get("sharpe_ratio", 1.23)
    max_dd = metrics.get("max_drawdown", -0.15)
    annual_ret = metrics.get("annual_return", 0.182)
    total_ret = metrics.get("total_return", 0.45)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>回测绩效报告</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f8fafc; color: #1e293b; }}
.header {{ background: linear-gradient(135deg, #f59e0b, #ef4444); color: white; padding: 30px; border-radius: 12px; margin-bottom: 24px; }}
.metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
.metric-card {{ background: white; padding: 20px; border-radius: 10px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
.metric-value {{ font-size: 28px; font-weight: 700; color: #f59e0b; }}
.metric-label {{ font-size: 13px; color: #64748b; margin-top: 4px; }}
.section {{ background: white; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
</style></head><body>
<div class="header"><h1>📉 回测绩效报告</h1>
<p>回测区间: 2024-01-01 ~ 2024-12-31 | 基准: 沪深300</p></div>
<div class="metrics-grid">
<div class="metric-card"><div class="metric-value">{annual_ret*100:.1f}%</div><div class="metric-label">年化收益</div></div>
<div class="metric-card"><div class="metric-value">{sharpe:.2f}</div><div class="metric-label">夏普比率</div></div>
<div class="metric-card"><div class="metric-value">{max_dd*100:.1f}%</div><div class="metric-label">最大回撤</div></div>
<div class="metric-card"><div class="metric-value">{total_ret*100:.1f}%</div><div class="metric-label">累计收益</div></div>
</div>
<div class="section"><h2>净值曲线</h2>
<p style="color:#64748b;text-align:center;padding:40px;">[演示模式] 净值曲线图表区域</p>
</div>
</body></html>"""

    html_path = os.path.join(report_dir, "backtest_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    portal.upsert_report(
        report_dir=report_dir,
        report_type="backtest",
        file_path=html_path,
        title="回测绩效-2024年度",
        task_id="demo_20260805",
    )


def _generate_demo_execution_report(portal, report_dir, ledger_path):
    """生成示例执行监控报告（静态版）"""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>执行监控报告</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f8fafc; color: #1e293b; }}
.header {{ background: linear-gradient(135deg, #10b981, #3b82f6); color: white; padding: 30px; border-radius: 12px; margin-bottom: 24px; }}
.section {{ background: white; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
th {{ background: #f9fafb; font-weight: 600; }}
</style></head><body>
<div class="header"><h1>🎯 执行监控报告</h1>
<p>生成时间: 演示数据 | 模式: PAPER | 后端: paper</p></div>
<div class="section"><h2>账户概览</h2>
<p>净值: ¥1,000,000.00 | 可用资金: ¥1,000,000.00 | 持仓数: 0</p>
</div>
<div class="section"><h2>成交明细</h2>
<p style="color:#64748b;text-align:center;padding:20px;">[演示模式] 无成交记录</p>
</div>
</body></html>"""

    html_path = os.path.join(report_dir, "execution_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    portal.upsert_report(
        report_dir=report_dir,
        report_type="execution",
        file_path=html_path,
        title="执行监控-PAPER复盘",
        task_id="demo_20260805",
    )


if __name__ == "__main__":
    main()
