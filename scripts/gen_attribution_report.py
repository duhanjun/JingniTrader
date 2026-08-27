#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绩效归因报告生成器（真实数据验证用）。

用真实数据源（默认 baostock）拉取标的日线，构造一组"低价买入 → 高价卖出"
的模拟成交账本（ledger.jsonl），驱动 reports-engine 的 attribution_report
插件生成包含真实价格数据的绩效归因报告 HTML。

背景：完整策略管线中归因意图 DATA→FACTOR→EXECUTION→REPORT 的 EXECUTION 阶段
硬性依赖 PORTFOLIO 目标权重且需真实券商账户产生非空 ledger。本脚本绕过
EXECUTION 下单，直接用真实行情构造成交记录，验证 attribution_report 插件
的渲染与真实数据整合能力。

用法：
    python scripts/gen_attribution_report.py [--out DIR] [--work-dir DIR]
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

STOCK_POOL = ["600000.SH", "000001.SZ", "600036.SH", "601318.SH", "000858.SZ"]
START = "2023-01-01"
END = "2024-12-31"
INIT_CAPITAL = 1_000_000.0


def _fetch_real_prices(work_dir: str):
    """用 baostock 拉真实日线价格，返回 {code: DataFrame(date, close)}。"""
    import baostock as bs
    import pandas as pd

    os.environ["QUANT_WORK_DIR"] = work_dir
    os.makedirs(work_dir, exist_ok=True)

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")

    prices: dict = {}
    try:
        for code in STOCK_POOL:
            sym = ("sh." if code.endswith(".SH") else "sz.") + code.split(".")[0]
            rs = bs.query_history_k_data_plus(
                sym, "date,code,open,high,low,close,volume",
                start_date=START, end_date=END, frequency="d", adjustflag="2",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["date", "code", "open", "high", "low", "close", "volume"])
            df["close"] = df["close"].astype(float)
            df["date"] = pd.to_datetime(df["date"])
            prices[code] = df.sort_values("date").reset_index(drop=True)
    finally:
        bs.logout()

    if not prices:
        raise RuntimeError("baostock 未返回任何价格数据，无法构造账本")
    return prices


def _build_ledger(prices: dict, exec_dir: str):
    """基于真实价格构造模拟买卖账本（年初买入 → 年末卖出，形成 round-trip）。"""
    os.makedirs(exec_dir, exist_ok=True)
    ledger_path = os.path.join(exec_dir, "ledger.jsonl")
    trade_log_path = os.path.join(exec_dir, "trade_log.json")

    cash = INIT_CAPITAL
    records = []

    buy_date = "2023-06-30"
    sell_date = "2024-11-29"

    for code, df in prices.items():
        buy_row = df[df["date"] <= buy_date]
        sell_row = df[df["date"] <= sell_date]
        if buy_row.empty or sell_row.empty:
            continue
        buy_price = float(buy_row.iloc[-1]["close"])
        sell_price = float(sell_row.iloc[-1]["close"])

        invest = INIT_CAPITAL * 0.15
        shares = int(invest // (buy_price * 100)) * 100
        if shares <= 0:
            continue
        buy_cost = shares * buy_price
        commission_buy = buy_cost * 0.0003
        cash -= buy_cost + commission_buy

        records.append({
            "execution_id": f"buy_{code}_{buy_date}",
            "trade_date": buy_date, "code": code, "side": "buy",
            "shares": shares, "price": round(buy_price, 3),
            "commission": round(commission_buy, 2), "stamp_tax": 0.0,
            "slippage_cost": round(buy_cost * 0.001, 2),
            "position_after_shares": shares, "cash_after": round(cash, 2),
            "nav_after": round(cash + buy_cost, 2), "confirmed": True,
        })

        sell_value = shares * sell_price
        commission_sell = sell_value * 0.0003
        stamp_sell = sell_value * 0.0005
        cash += sell_value - commission_sell - stamp_sell
        records.append({
            "execution_id": f"sell_{code}_{sell_date}",
            "trade_date": sell_date, "code": code, "side": "sell",
            "shares": shares, "price": round(sell_price, 3),
            "commission": round(commission_sell, 2),
            "stamp_tax": round(stamp_sell, 2),
            "slippage_cost": round(sell_value * 0.001, 2),
            "position_after_shares": 0, "cash_after": round(cash, 2),
            "nav_after": round(cash, 2), "confirmed": True,
        })

    if len(records) < 2:
        raise RuntimeError("构造的交易记录不足，无法生成有意义的归因报告")

    with open(ledger_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(trade_log_path, "w", encoding="utf-8") as f:
        json.dump({"records": records, "init_capital": INIT_CAPITAL},
                  f, ensure_ascii=False, indent=2)
    print(f"  账本已构造: {ledger_path} ({len(records)} 条成交)")
    return ledger_path, trade_log_path


def main() -> int:
    ap = argparse.ArgumentParser(description="生成绩效归因报告（真实价格）")
    ap.add_argument("--out", default=os.path.join(ROOT, "_reports_e2e", "attribution_report.html"))
    ap.add_argument("--work-dir", default=os.path.join(ROOT, "_e2e_workdir", "attribution"))
    args = ap.parse_args()

    work_dir = os.path.abspath(args.work_dir)
    os.environ["QUANT_WORK_DIR"] = work_dir

    print("拉取真实价格...")
    prices = _fetch_real_prices(work_dir)
    print(f"  获取 {len(prices)} 只标的真实行情")

    exec_dir = os.path.join(work_dir, "execution")
    ledger_path, trade_log_path = _build_ledger(prices, exec_dir)

    # 构造 ctx 并指向 EXECUTION 产物
    import engine
    master = engine.MasterEngine()
    ctx = master.parse_intent("生成上个月实盘绩效归因报告")
    ctx.stock_pool = list(STOCK_POOL)
    ctx.start_date = START
    ctx.end_date = END
    ctx.metadata["report_intent"] = "attribution"
    ctx.update_artifact("EXECUTION", os.path.join(exec_dir, "trade_log.json"))

    # 切换到 REPORTS skill 的 scripts 包，确保 attribution_analyzer 等可导入
    engine._register_subskill_scripts("REPORT")
    # 将 REPORTS skill 根目录加入 sys.path，使 plugins 包可导入
    reports_root = os.path.join(ROOT, "skills", "reports-engine")
    if reports_root not in sys.path:
        sys.path.insert(0, reports_root)

    print("生成归因报告...")
    from plugins.registry import get as plugin_get
    from plugins.loader import load as plugin_load

    plugin_load(os.path.join(reports_root, "plugins", "attribution_report"))
    plugin = plugin_get("attribution_report")
    if plugin is None or not plugin.render:
        raise RuntimeError("attribution_report 插件未注册或无 render")

    html_path = plugin.render(None, ctx, os.path.abspath(args.out))
    size = os.path.getsize(os.path.abspath(args.out))
    print(f"报告已生成: {os.path.abspath(args.out)} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
