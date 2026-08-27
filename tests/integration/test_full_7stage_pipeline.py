"""完整 7 阶段策略管线端到端集成测试。

验证 skill 的核心承诺：用户输入一个"构建可回测策略"的意图后，
系统按意图路由到完整 7 个子 skill（DATA → FACTOR → MODEL → BACKTEST →
PORTFOLIO → EXECUTION → REPORT），逐阶段执行并生成对应报告。

覆盖（对应 README/SKILL.md 意图表「策略构建/回测/选股」场景）：
- 意图解析正确路由到 7 阶段
- 每个子 skill 的 run() 被调用并产出约定产物文件
- REPORT 阶段生成 report.html 报告
- 归档目录包含 pipeline_summary.md

设计要点：
- 注入合成 OHLCV 数据（tests/fixtures/synthetic_data.py），避免外部数据源/网络
- 使用临时 QUANT_WORK_DIR，避免污染真实 workspace
- PORTFOLIO 阶段使用 hrp 优化方法（对合成数据稳健，有等权降级），
  避免 pypfopt max_sharpe 在随机数据下 infeasible
- 标记 slow + integration + skill_master，CI 中归入夜间全量回归
"""
from __future__ import annotations

import os
import sys
import json

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 归入 heavy 批次以进程隔离运行，默认安全子集跳过（OPEN-2026-0814-13 原生 SDK 共存问题）。
# 本目录用例真实拉起多阶段管线，会触发 mlflow / 原生扩展共存导致的挂起或段错误，
# 故统一标记 heavy，由 CI 的 heavy-regression job 按目录分批单进程执行。
pytestmark = [
    pytest.mark.heavy,
]


def _restore_real_sklearn():
    """恢复被 tests/conftest.py mock 掉的真实 sklearn。

    完整 7 阶段管线的 MODEL 阶段需要真实 sklearn 训练并 joblib 序列化模型，
    而 conftest 为了单元测试无需真实安装，用 mock.MagicMock 注入 sys.modules，
    导致 RandomForest 无法被 pickle。本测试在 import engine 前恢复真实模块。
    若真实 sklearn 未安装，则跳过测试（依赖 requires_sklearn）。
    """
    import importlib

    # 先清除 conftest 注入的 mock 条目（MagicMock 无 __spec__，需先移除才能探测）
    mock_keys = [
        k for k in list(sys.modules) if k == "sklearn" or k.startswith("sklearn.")
    ]
    for k in mock_keys:
        sys.modules.pop(k, None)

    try:
        importlib.import_module("sklearn")
    except ImportError:
        pytest.skip("需要真实 sklearn（requires_sklearn）")
    # 预加载常用子模块，避免 strategy-model-engine 内部懒加载时找不到
    for sub in ("sklearn.ensemble", "sklearn.linear_model", "sklearn.model_selection"):
        try:
            importlib.import_module(sub)
        except ImportError:
            pass


def _build_synthetic_external_data(n_codes: int = 10):
    """构造多股票合成 OHLCV DataFrame，供完整 7 阶段管线使用。

    用足够数量的股票 + 较长周期，让 MODEL 训练有足够样本、PORTFOLIO 优化可解。
    复用以确定性种子保证可复现。
    """
    import numpy as np
    import pandas as pd

    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    frames = []
    rng = np.random.RandomState(20240101)
    for code in codes:
        dates = pd.bdate_range("2023-01-01", "2024-06-30")
        n = len(dates)
        base = rng.uniform(8, 50)
        closes = base * (1 + np.cumsum(rng.normal(0, 0.02, n)))
        opens = closes * (1 + rng.normal(0, 0.002, n))
        highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.005, n)))
        lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.005, n)))
        vol = rng.randint(1_000_000, 10_000_000, n)
        frames.append(pd.DataFrame({
            "code": code, "date": dates,
            "open": opens.round(2), "high": highs.round(2),
            "low": lows.round(2), "close": closes.round(2),
            "volume": vol,
        }))
    return pd.concat(frames, ignore_index=True)


def _setup_env(monkeypatch, tmp_path):
    """统一设置隔离环境：临时工作目录 + 无外部依赖 + 强制刷新。

    必须在 import engine 之前调用 _restore_real_sklearn，否则 strategy-model-engine
    加载时已绑定 mock 的 sklearn，无法训练真实模型。
    """
    _restore_real_sklearn()
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    monkeypatch.setenv("QUANT_WORK_DIR", str(work_dir))
    monkeypatch.setenv("ALLOW_SYNTHETIC_FALLBACK", "true")
    monkeypatch.setenv("DATA_BACKENDS", "websearch")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    # 强制刷新：避免主调度器因前序测试遗留产物而跳过阶段调用
    monkeypatch.setenv("QUANT_FORCE_REFRESH", "1")
    return str(work_dir)


@pytest.mark.integration
@pytest.mark.skill_master
@pytest.mark.slow
class TestFull7StagePipeline:
    """完整策略意图 → 7 个子 skill → 报告生成的端到端测试。"""

    # 每个阶段期望的产物文件名（EXPECTED_ARTIFACTS 约定）
    STAGE_ARTIFACTS = {
        "DATA": "cleaned_data.parquet",
        "FACTOR": "factor_data.parquet",
        "MODEL": "model.pkl",           # 实际是带时间戳的 model_*.pkl，单独校验
        "BACKTEST": "backtest_result.json",
        "PORTFOLIO": "portfolio_weights.json",
        "EXECUTION": "trade_log.jsonl",
        "REPORT": "report.html",
    }

    def test_full_strategy_intent_triggers_7_stages(self, tmp_path, monkeypatch):
        """策略构建意图应路由到完整 7 阶段管线。"""
        _setup_env(monkeypatch, tmp_path)
        import engine
        master = engine.MasterEngine()
        ctx = master.parse_intent("帮我用近3年A股数据做一个20日反转因子选股回测")
        assert ctx.metadata["strategy_required"] is True
        assert ctx.target_stages == [
            "DATA", "FACTOR", "MODEL", "BACKTEST", "PORTFOLIO", "EXECUTION", "REPORT"
        ]

    def test_full_7stage_pipeline_executes_and_generates_report(
        self, tmp_path, monkeypatch
    ):
        """完整 7 阶段管线应全部执行成功并生成各阶段产物与报告。"""
        work_dir = _setup_env(monkeypatch, tmp_path)

        import engine
        master = engine.MasterEngine()
        ctx = master.parse_intent(
            "帮我用近3年A股数据做一个20日反转因子选股回测，并优化组合实盘执行"
        )
        ctx.stock_pool = [f"{i:06d}.SZ" for i in range(1, 11)]
        ctx.start_date = "2023-01-01"
        ctx.end_date = "2024-06-30"
        # PORTFOLIO 用 hrp：对合成随机数据稳健（有等权降级），避免 max_sharpe infeasible
        ctx.strategy_params = {"optimization_method": "hrp"}
        ctx.external_data = {
            "daily": _build_synthetic_external_data(),
            "source": "integration-test",
        }

        result = master.run_pipeline(ctx=ctx)

        # 1) 全部 7 个阶段成功
        assert result.get("success") is True, f"管道未成功: {result.get('summary')}"
        assert result.get("failed_stages") == [], f"存在失败阶段: {result.get('failed_stages')}"
        assert result.get("completed_stages") == [
            "DATA", "FACTOR", "MODEL", "BACKTEST", "PORTFOLIO", "EXECUTION", "REPORT"
        ]

        # 2) 每个阶段产物文件真实落盘
        for stage in ["DATA", "FACTOR", "MODEL", "BACKTEST", "PORTFOLIO", "REPORT"]:
            artifact_path = ctx.artifacts.get(stage, "")
            assert artifact_path, f"阶段 {stage} 未记录产物路径"
            assert os.path.exists(artifact_path), f"阶段 {stage} 产物不存在: {artifact_path}"

        # 3) REPORT 阶段产物是报告文件
        report_path = ctx.artifacts.get("REPORT", "")
        assert report_path.endswith(".html"), f"REPORT 产物应为 .html: {report_path}"

        # 4) BACKTEST 产物含绩效指标
        bt_path = ctx.artifacts.get("BACKTEST")
        with open(bt_path, encoding="utf-8") as f:
            bt_data = json.load(f)
        assert "metrics" in bt_data, "backtest_result.json 应含 metrics"

        # 5) 归档目录含 pipeline_summary.md（复盘/审计契约）
        archive_dir = result.get("archive_dir", "")
        assert archive_dir and os.path.isdir(archive_dir), "应生成归档目录"
        assert os.path.isfile(os.path.join(archive_dir, "pipeline_summary.md")), (
            "归档目录应含 pipeline_summary.md"
        )

    def test_report_html_has_real_content(self, tmp_path, monkeypatch):
        """生成的报告 HTML 不应是空壳（应包含回测绩效内容）。"""
        work_dir = _setup_env(monkeypatch, tmp_path)

        import engine
        master = engine.MasterEngine()
        ctx = master.parse_intent("用近3年A股数据做20日反转因子选股回测，优化组合并实盘执行")
        ctx.stock_pool = [f"{i:06d}.SZ" for i in range(1, 11)]
        ctx.start_date = "2023-01-01"
        ctx.end_date = "2024-06-30"
        ctx.strategy_params = {"optimization_method": "hrp"}
        ctx.external_data = {
            "daily": _build_synthetic_external_data(),
            "source": "integration-test",
        }

        result = master.run_pipeline(ctx=ctx)
        assert result.get("success") is True, f"管道未成功: {result.get('summary')}"

        report_path = ctx.artifacts.get("REPORT", "")
        with open(report_path, encoding="utf-8") as f:
            html = f.read()
        assert len(html) > 1000, "报告 HTML 内容过短，疑似空壳"
        assert "JingniTrader" in html or "报告" in html, "报告缺少标识性内容"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
