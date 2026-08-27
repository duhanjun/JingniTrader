#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""7 份报告真实数据端到端验证脚本（企业级回归工具）。

用真实数据源（默认 baostock，免费、无需 token）依次运行多组意图管线，
验证 skill 能按用户意图执行子 skill 并生成对应报告，最后汇集到统一目录。

生成的 7 份报告：
  1. technical_report.html       个股技术面分析（report_template=both）
  2. fundamental_report.html     个股基本面分析（report_template=both）
  3. factor_analysis_report.html 因子分析（FACTOR 产物附带，依赖 alphalens 指标）
  4. report.html                 策略回测（BACKTEST 产物 → backtest_report 插件）
  5. portfolio_report.html       组合优化（report_intent=portfolio）
  6. execution_report.html       执行监控（report_intent=execution）
  7. attribution_report.html     绩效归因（report_intent=attribution）

用法：
    python scripts/validate_7_reports_e2e.py                     # 跑全部 5 组
    python scripts/validate_7_reports_e2e.py --run-only 3         # 只跑第 3 组（供 subprocess 调用）

说明：脚本内部用子进程逐组运行，保证每组独立的 QUANT_WORK_DIR（config 模块
import 时缓存路径常量，同一进程内改环境变量不生效）。
"""
from __future__ import annotations

import argparse
import glob
import importlib
import logging
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# 统一报告汇集目录
OUT_ROOT = os.path.join(ROOT, "_reports_e2e")

RUNS = [
    {
        "name": "个股综合分析(技术+基本面+因子)",
        "intent": "分析 600000.SH 浦发银行的技术面和基本面",
        "report_template": "both",
        "report_intent": None,
    },
    {
        "name": "策略回测",
        "intent": "用近3年A股数据做一个20日反转因子选股回测",
        "report_intent": None,
    },
    {
        "name": "组合优化",
        "intent": "用近3年A股数据优化组合，最大回撤控制在15%以内",
        "report_intent": "portfolio",
    },
    {
        "name": "执行监控",
        "intent": "生成当前执行监控报告，查看账户持仓和成交",
        "report_intent": "execution",
    },
    {
        "name": "绩效归因",
        "intent": "生成上个月实盘绩效归因报告",
        "report_intent": "attribution",
    },
]

STOCK_POOL = ["600000.SH", "000001.SZ", "600036.SH", "601318.SH", "000858.SZ"]
START_DATE = "2023-01-01"
END_DATE = "2024-12-31"

REPORT_FILES = [
    "technical_report.html", "fundamental_report.html",
    "factor_analysis_report.html", "report.html",
    "portfolio_report.html", "execution_report.html",
    "attribution_report.html",
]


def _run_single(index: int, data_backend: str, work_dir: str) -> None:
    """运行第 index 个 run（在独立子进程中执行，环境隔离）。"""
    # 先恢复真实 sklearn
    for k in [k for k in list(sys.modules) if k == "sklearn" or k.startswith("sklearn.")]:
        sys.modules.pop(k, None)
    importlib.import_module("sklearn")

    os.environ["QUANT_WORK_DIR"] = work_dir
    os.environ["DATA_BACKENDS"] = data_backend
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["QUANT_FORCE_REFRESH"] = "1"
    # 启用 alphalens 因子分析指标（FACTOR 阶段产出 metrics.json，REPORT 聚合为 factor_analysis_report.html）
    os.environ["QUANT_ALPHALENS_REPORT"] = "1"
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("DEEPSEEK_API_KEY", None)

    run = RUNS[index - 1]
    import engine
    master = engine.MasterEngine()
    ctx = master.parse_intent(run["intent"])
    ctx.stock_pool = list(STOCK_POOL)
    ctx.start_date = START_DATE
    ctx.end_date = END_DATE
    ctx.strategy_params = {"optimization_method": "hrp"}
    if run.get("report_template"):
        ctx.metadata["report_template"] = run["report_template"]
    if run.get("report_intent"):
        ctx.metadata["report_intent"] = run["report_intent"]

    print(f"[{index}/{len(RUNS)}] {run['name']}")
    print(f"  target_stages: {ctx.target_stages}")
    result = master.run_pipeline(ctx=ctx)
    print(f"  success: {result.get('success')} | completed: {result.get('completed_stages')} "
          f"| failed: {result.get('failed_stages')}")

    # 把本组生成的报告 HTML 复制到汇集目录
    os.makedirs(OUT_ROOT, exist_ok=True)
    for html in glob.glob(os.path.join(work_dir, "archives", "**", "artifacts", "*.html"),
                          recursive=True):
        name = os.path.basename(html)
        if name == "index.html":
            continue
        dst = os.path.join(OUT_ROOT, name)
        shutil.copy2(html, dst)
        print(f"  + 报告: {os.path.relpath(dst, ROOT)} ({os.path.getsize(dst)} bytes)")


def _render_portal() -> str:
    """生成统一门户 index.html，列出全部报告链接。"""
    cards = []
    title_map = {
        "technical_report.html": "技术面分析", "fundamental_report.html": "基本面分析",
        "factor_analysis_report.html": "因子分析", "report.html": "策略回测",
        "portfolio_report.html": "组合优化", "execution_report.html": "执行监控",
        "attribution_report.html": "绩效归因",
    }
    icon_map = {
        "technical_report.html": "📈", "fundamental_report.html": "🏢",
        "factor_analysis_report.html": "🧬", "report.html": "📉",
        "portfolio_report.html": "⚖️", "execution_report.html": "🎯",
        "attribution_report.html": "📊",
    }
    for name in REPORT_FILES:
        path = os.path.join(OUT_ROOT, name)
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        color = "#222" if exists else "#999"
        status = "✅ 已生成" if exists else "❌ 未生成"
        cards.append(
            f'<div style="border:1px solid #ddd;border-radius:8px;padding:16px;'
            f'margin:12px;width:260px;display:inline-block;vertical-align:top">'
            f'<div style="font-size:28px">{icon_map.get(name, "📄")}</div>'
            f'<h3 style="color:{color}">{title_map.get(name, name)}</h3>'
            f'<p style="color:#888;font-size:12px">{name}<br>{size:,} bytes<br>{status}</p>'
            + (f'<a href="{name}" target="_blank" style="color:#1677ff">打开报告 →</a>'
               if exists else '<span style="color:#999">（未生成）</span>')
            + '</div>'
        )
    portal_path = os.path.join(OUT_ROOT, "index.html")
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>jingni-trader 7 份报告端到端验证</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:40px;background:#f7f8fa}}
h1{{color:#222}}.grid{{max-width:1200px}}</style></head>
<body><h1>jingni-trader 7 份报告端到端验证</h1>
<div class="grid">{''.join(cards)}</div>
<p style="color:#999;margin-top:24px">数据源：真实行情(baostock) · 生成于真实数据端到端回归</p>
</body></html>"""
    with open(portal_path, "w", encoding="utf-8") as f:
        f.write(html)
    return portal_path


def main() -> int:
    global OUT_ROOT
    ap = argparse.ArgumentParser(description="7 份报告真实数据端到端验证")
    ap.add_argument("--data-backend", default="baostock")
    ap.add_argument("--out", default=OUT_ROOT)
    ap.add_argument("--work-dir", default="")
    ap.add_argument("--run-only", type=int, default=0,
                    help="只跑第 N 组（供 subprocess 调用）；0=跑全部")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    OUT_ROOT = os.path.abspath(args.out)
    os.makedirs(OUT_ROOT, exist_ok=True)

    if args.run_only:
        work_dir = args.work_dir or os.path.join(ROOT, "_e2e_workdir", f"run{args.run_only}")
        os.makedirs(work_dir, exist_ok=True)
        _run_single(args.run_only, args.data_backend, work_dir)
        return 0

    # ── 主进程：循环用子进程跑每组，保证环境隔离 ──
    py = args.python
    script = os.path.abspath(__file__)
    for i in range(1, len(RUNS) + 1):
        print(f"\n{'='*70}\n[{i}/{len(RUNS)}] {RUNS[i-1]['name']}\n{'='*70}")
        cmd = [py, script, "--run-only", str(i),
               "--data-backend", args.data_backend,
               "--work-dir", os.path.join(ROOT, "_e2e_workdir", f"run{i}"),
               "--out", OUT_ROOT]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        print(proc.stdout)
        if proc.returncode != 0:
            print(proc.stderr[-2000:] if proc.stderr else "(无 stderr)")
            print(f"  ⚠️ 第 {i} 组执行返回非 0 码 {proc.returncode}，继续后续组")

    # ── 汇总门户 ──
    print(f"\n{'='*70}\n汇集结果\n{'='*70}")
    for name in REPORT_FILES:
        path = os.path.join(OUT_ROOT, name)
        status = "OK " if os.path.exists(path) else "MISS"
        print(f"  [{status}] {name} -> {os.path.relpath(path, ROOT)} "
              f"({os.path.getsize(path) if os.path.exists(path) else '未生成'})")
    portal = _render_portal()
    print(f"\n门户: {os.path.relpath(portal, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
