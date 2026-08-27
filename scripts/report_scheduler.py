"""定时刷新调度器：组合优化报告 & 绩效归因报告

基于标准库 threading，零依赖，常驻进程。

触发时间（可配置）：
- 组合优化报告：每日开盘前（默认 09:30）—— 强制刷新行情/因子，重算权重并展示最新
- 绩效归因报告：每日收盘后（默认 15:00）—— 拉取最新成交，追加 ledger，重建归因

用法：
    python scripts/report_scheduler.py                       # 常驻调度
    python scripts/report_scheduler.py --dry-run portfolio   # 立即执行组合优化刷新
    python scripts/report_scheduler.py --dry-run attribution # 立即执行绩效归因刷新

说明：
- 组合优化刷新默认重算并覆盖 weights.json、展示最新权重（不自动调仓）
- 自动调仓需显式在 scheduler_config.json 开启 execute_after（默认 false）
"""
import os
import sys
import json
import time
import logging
import argparse
import threading
import importlib
from datetime import datetime, date, timedelta

# ---------------------------------------------------------------------------
# 路径与日志
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_work_dir = os.environ.get("QUANT_WORK_DIR", os.path.join(PROJECT_ROOT, "workspace"))
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("report_scheduler")


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "portfolio_report": {
        "enabled": True,
        "hour": 9,
        "minute": 30,
        "execute_after": False,   # 刷新后是否自动调仓（默认关闭）
    },
    "attribution_report": {
        "enabled": True,
        "hour": 15,
        "minute": 0,
    },
    "ctx": {
        "stock_pool": [],
        "benchmark": "000300.SH",
        "start_date": "",
        "end_date": "",
        "strategy_name": "ma20",
        "strategy_params": {},
        "data_sources": None,
        "backend": "paper",       # paper / gm / xtquant
    },
}


def load_config() -> dict:
    """加载调度配置，缺失字段用默认值。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            _deep_merge(cfg, user_cfg)
        except Exception as e:
            logger.error(f"加载配置失败（使用默认）: {e}")
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并配置字典。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# 子引擎驱动（复用 master 引擎的 scripts 包切换机制）
# ---------------------------------------------------------------------------
from engine import _register_subskill_scripts, SKILL_MODULES  # noqa: E402


def run_stage(stage: str, ctx) -> dict:
    """执行单个阶段子引擎，返回 run() 结果。

    复用 master 引擎的 scripts 包切换 + 模块加载机制，保证与主流程一致。
    成功后更新 ctx.artifacts[stage] 与 ctx.metadata[stage]。
    """
    module_name = SKILL_MODULES.get(stage)
    if not module_name:
        raise ValueError(f"未知阶段: {stage}")

    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            del sys.modules[key]
    _register_subskill_scripts(stage)
    skill_module = importlib.import_module(module_name)

    logger.info(f"[{stage}] 开始执行...")
    result = skill_module.run(ctx)
    if result.get("success"):
        artifact = result.get("artifact_path", "")
        if artifact:
            ctx.update_artifact(stage, artifact)
        ctx.metadata[stage] = result.get("metadata", {})
        logger.info(f"[{stage}] 成功，产物: {artifact}")
    else:
        error = result.get("error", "未知错误")
        logger.error(f"[{stage}] 失败: {error}")
        ctx.add_error(f"{stage}: {error}")
    return result


def _set_force_refresh(on: bool = True):
    """设置/清除强制刷新环境变量。"""
    os.environ["QUANT_FORCE_REFRESH"] = "1" if on else "0"


def build_ctx(cfg: dict):
    """根据配置构造 Context。"""
    from scripts.context import Context
    c = cfg.get("ctx", {})
    today = date.today().isoformat()
    start_date = c.get("start_date") or _default_start_date()
    ctx = Context(
        task_id=f"scheduler_{datetime.now():%Y%m%d_%H%M%S}",
        user_intent="组合优化与绩效归因定时刷新",
        current_stage="IDLE",
        target_stages=[],
        stock_pool=c.get("stock_pool") or [],
        benchmark=c.get("benchmark") or "000300.SH",
        start_date=start_date,
        end_date=c.get("end_date") or today,
        strategy_name=c.get("strategy_name") or "",
        strategy_params=c.get("strategy_params") or {},
        data_sources=c.get("data_sources"),
        metadata={
            "backend": c.get("backend") or "paper",
            "scheduler": True,
        },
    )
    return ctx


def _default_start_date() -> str:
    """默认回溯 1 年。"""
    from datetime import timedelta
    return (date.today() - timedelta(days=365)).isoformat()


# ---------------------------------------------------------------------------
# 组合优化刷新任务
# ---------------------------------------------------------------------------
def refresh_portfolio(cfg: dict) -> dict:
    """组合优化报告刷新：DATA→FACTOR→PORTFOLIO→REPORT(portfolio)。"""
    logger.info("=== 开始组合优化报告刷新 ===")
    _set_force_refresh(True)  # 强制刷新 DATA/FACTOR，拿最新行情
    ctx = build_ctx(cfg)
    stages = ["DATA", "FACTOR", "PORTFOLIO"]
    for stage in stages:
        r = run_stage(stage, ctx)
        if not r.get("success"):
            return {"success": False, "error": f"组合优化刷新在 {stage} 失败", "ctx": ctx}

    # 设置组合优化意图，REPORT 阶段生成组合优化报告
    ctx.metadata["report_intent"] = "portfolio"
    ctx.metadata["backend"] = cfg.get("ctx", {}).get("backend", "paper")
    r = run_stage("REPORT", ctx)
    if not r.get("success"):
        return {"success": False, "error": f"组合优化报告生成失败: {r.get('error')}", "ctx": ctx}

    # 可选：刷新后自动调仓（默认关闭）
    if cfg.get("portfolio_report", {}).get("execute_after"):
        logger.info("execute_after=True，刷新后执行实际调仓")
        r_exec = run_stage("EXECUTION", ctx)
        if not r_exec.get("success"):
            return {"success": False, "error": f"自动调仓失败: {r_exec.get('error')}", "ctx": ctx}

    logger.info("=== 组合优化报告刷新完成 ===")
    return {"success": True, "artifact": ctx.get_artifact("REPORT"), "ctx": ctx}


# ---------------------------------------------------------------------------
# 绩效归因刷新任务
# ---------------------------------------------------------------------------
def refresh_attribution(cfg: dict) -> dict:
    """绩效归因报告刷新：基于现有 ledger 重建归因报告。

    归因报告消费的是 EXECUTION 产生的 ledger.jsonl（由交易/LIVE 写入），
    因此刷新时不需要重跑 DATA/FACTOR/EXECUTION，只需定位已有 ledger 并重新生成报告。
    若 ledger 不存在，提示先执行模拟/实盘交易。
    """
    logger.info("=== 开始绩效归因报告刷新 ===")
    ctx = build_ctx(cfg)

    # 定位 EXECUTION 产物（ledger 所在目录）
    execution_dir = os.path.join(_work_dir, "execution")
    ledger_path = os.path.join(execution_dir, "ledger.jsonl")
    if not os.path.exists(ledger_path):
        return {
            "success": False,
            "error": f"未找到 ledger 文件（{ledger_path}），请先执行模拟/实盘交易生成交易账本",
            "ctx": ctx,
        }

    # 设置 EXECUTION 产物路径为 execution 目录（而非具体文件），
    # 供 reports-engine 归因插件的 os.path.isfile 判断正确落在目录上，
    # 从而定位到 execution_dir/ledger.jsonl。
    ctx.update_artifact("EXECUTION", execution_dir)

    # 设置归因意图，REPORT 阶段生成绩效归因报告
    ctx.metadata["report_intent"] = "attribution"
    ctx.metadata["backend"] = cfg.get("ctx", {}).get("backend", "paper")
    r = run_stage("REPORT", ctx)
    if not r.get("success"):
        return {"success": False, "error": f"绩效归因报告生成失败: {r.get('error')}", "ctx": ctx}

    logger.info("=== 绩效归因报告刷新完成 ===")
    return {"success": True, "artifact": ctx.get_artifact("REPORT"), "ctx": ctx}


# ---------------------------------------------------------------------------
# 定时调度（标准库 threading）
# ---------------------------------------------------------------------------
def _next_run_time(hour: int, minute: int, now: datetime) -> datetime:
    """计算下一个触发时刻（每天 hour:minute，若今天已过则顺延到明天）。"""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
    return target


def _schedule_daily(cfg: dict, name: str, hour: int, minute: int, run_fn, stop_event: threading.Event):
    """每日定时任务：到点触发 run_fn。"""
    logger.info(f"[{name}] 定时任务已注册，每天 {hour:02d}:{minute:02d} 触发")
    while not stop_event.is_set():
        now = datetime.now()
        target = _next_run_time(hour, minute, now)
        wait_sec = (target - now).total_seconds()
        if stop_event.wait(wait_sec):
            break
        try:
            result = run_fn(cfg)
            if result.get("success"):
                logger.info(f"[{name}] 刷新成功，产物: {result.get('artifact')}")
            else:
                logger.error(f"[{name}] 刷新失败: {result.get('error')}")
        except Exception as e:
            logger.exception(f"[{name}] 刷新异常: {e}")


def run_scheduler(cfg: dict):
    """常驻调度：注册两个定时任务并阻塞。"""
    stop_event = threading.Event()
    threads = []

    pr = cfg.get("portfolio_report", {})
    ar = cfg.get("attribution_report", {})

    if pr.get("enabled", True):
        t = threading.Thread(
            target=_schedule_daily,
            args=(cfg, "组合优化", pr.get("hour", 9), pr.get("minute", 30),
                  refresh_portfolio, stop_event),
            daemon=True,
        )
        t.start()
        threads.append(t)

    if ar.get("enabled", True):
        t = threading.Thread(
            target=_schedule_daily,
            args=(cfg, "绩效归因", ar.get("hour", 15), ar.get("minute", 0),
                  refresh_attribution, stop_event),
            daemon=True,
        )
        t.start()
        threads.append(t)

    if not threads:
        logger.warning("两个定时任务均被禁用，调度器空转")
    else:
        logger.info(f"调度器已启动，共 {len(threads)} 个定时任务。按 Ctrl+C 停止")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("调度器停止")
        stop_event.set()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="组合优化/绩效归因报告定时刷新调度器")
    parser.add_argument("--dry-run", choices=["portfolio", "attribution"],
                        help="立即执行一次指定刷新（不进入常驻调度），用于验证")
    args = parser.parse_args()

    cfg = load_config()

    if args.dry_run == "portfolio":
        result = refresh_portfolio(cfg)
        print("\n组合优化刷新结果:", json.dumps({
            "success": result.get("success"),
            "artifact": result.get("artifact"),
            "error": result.get("error"),
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("success") else 1)

    if args.dry_run == "attribution":
        result = refresh_attribution(cfg)
        print("\n绩效归因刷新结果:", json.dumps({
            "success": result.get("success"),
            "artifact": result.get("artifact"),
            "error": result.get("error"),
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("success") else 1)

    run_scheduler(cfg)


if __name__ == "__main__":
    main()
