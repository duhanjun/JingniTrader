#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xtquant（迅投 miniQMT）实盘执行监控 LIVE 启动器。

设置 TRADE_MODE=live + TRADE_BACKEND=xtquant，调用 execution-monitor-engine
的 run_live(ctx, live_data_mode="jsonp") 启动实时执行监控：
- 用 JSONP 模式（snapshot.js 文件轮询），HTML 双击即看，无需 HTTP 服务器
- 每 3 秒刷新一次 snapshot.js（账户/持仓/成交实时数据）
- 后台运行，按 Ctrl+C 停止

用法：
    python scripts/run_live_xtquant.py [--work-dir DIR]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="xtquant 实盘执行监控 LIVE 启动器")
    ap.add_argument("--work-dir", default=os.path.join(ROOT, "_live_xtquant_ws"),
                    help="工作目录（报告与实时数据存放处）")
    args = ap.parse_args()

    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)

    # ── 关键：切换到 xtquant 实盘后端 ──
    os.environ["TRADE_MODE"] = "live"
    os.environ["TRADE_BACKEND"] = "xtquant"
    os.environ["QUANT_WORK_DIR"] = work_dir
    os.environ["QUANT_FORCE_REFRESH"] = "1"
    os.environ["LOG_LEVEL"] = "INFO"

    import engine
    master = engine.MasterEngine()
    ctx = master.parse_intent("生成当前执行监控报告，查看账户持仓和成交")
    ctx.metadata["report_intent"] = "execution"

    # 切换到 execution-monitor-engine 的 scripts 包（live_monitor / adapters 等）
    engine._register_subskill_scripts("EXECUTION")

    # 用文件路径加载 execution-monitor-engine/engine.py 中的 run_live
    # （目录名含连字符，无法用常规 import 语法，需 spec_from_file_location）
    import importlib.util
    exec_engine_path = os.path.join(ROOT, "skills", "execution-monitor-engine", "engine.py")
    spec = importlib.util.spec_from_file_location("exec_mon_engine", exec_engine_path)
    exec_module = importlib.util.module_from_spec(spec)
    sys.modules["exec_mon_engine"] = exec_module
    spec.loader.exec_module(exec_module)

    result = exec_module.run_live(ctx=ctx, live_data_mode="jsonp")
    print(f"\nrun_live 返回: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
