---
name: reports-engine
version: 1.0.0
description: A股绩效归因与可视化报告引擎。支持三种报告类型：量化投资者生成包含净值曲线、绩效指标 TearSheet、月度收益热力图、申万行业归因、Brinson 分解、风格暴露分析的综合 HTML 报告；非量化投资者生成个股技术面深度分析（9 章节，含A股特色：资金面、龙虎榜）和基本面深度分析（10 章节，含A股特色：股东结构）独立报告；绩效复盘报告从实盘/模拟交易账本中提取成交记录，通过 FIFO round-trip 归组实现盈亏归因、执行质量分析和 A 股压力期表现分析。集成 TradingView 轻量级 K 线图（8 种可切换技术指标），支持 LLM 占位符注入。基于 QuantStats / Plotly / Matplotlib 输出交互式图表和结构化数据 JSON。
author: quant-team
license: MIT
tags:
  - quant-trading
  - A股
  - reports-engine
  - 绩效
  - 可视化
  - quantstats
  - plotly
  - 个股分析
  - 技术面
  - 基本面
  - TradingView
dependencies:
  - quantstats
  - plotly>=5.15.0
  - matplotlib>=3.7.0
  - pandas>=2.0.0
  - numpy>=1.24.0
  - jinja2>=3.1.0
environment_variables:
  - name: QUANT_WORK_DIR
    description: 工作目录根路径
    required: false
    default: "./workspace"
  - name: REPORT_TITLE
    description: 报告标题
    required: false
    default: "A股量化策略绩效报告"
  - name: BENCHMARK
    description: 基准指数
    required: false
    default: "000300.SH"
  - name: INDUSTRY_STANDARD
    description: 行业分类标准（sw=申万 / zz=中信）
    required: false
    default: "sw"
  - name: CHART_THEME
    description: 图表主题（plotly_white / plotly_dark）
    required: false
    default: "plotly_white"
language: python
python_version: "3.9+"
entry_point: engine.py
trigger_keywords:
  - 报告
  - 绩效
  - 归因
  - 可视化
  - 热力图
  - 净值曲线
  - TearSheet
  - 行业归因
  - Brinson
  - 风格暴露
  - 技术面分析
  - 基本面分析
  - 个股分析
  - 诊股报告
  - K线图
  - 绩效归因
  - 归因分析
  - 复盘
  - 实盘报告
  - 盈亏分析
  - 执行报告
  - 交易复盘
  - 绩效复盘
  - attribution
---

# reports-engine

## 概述

reports-engine 是 A 股量化投研的**绩效归因与可视化报告引擎**，支持多种报告类型。所有报告均统一走**自包含插件**路径，插件 render.py 不依赖 engine.py 内部函数（包括 fallback_report 也已自包含，委托 technical_report / fundamental_report 两个插件渲染，不再回调 engine 内部函数）。

### 报告路由优先级（已收敛为纯插件匹配 + 兜底插件）

路由在 `run()` 内收敛为**两分支**，二者均经自包含插件路径生成报告：
- **分支 A（ENABLE_PLUGIN）**：统一由 `_run_plugin_auto` 执行插件匹配（含多命中聚合、
  自定义输出文件名、fallback 兜底）。
- **分支 B（无插件机制 / 异常兜底）**：`_run_builtin_fallback`，直接运行兜底插件
  `fallback_report` 生成默认个股技术/基本面报告（不再存在独立的「内置模板内核」HTML
  组装路径；旧 `_run_template_report` 已删除，其 HTML 组装能力由插件渲染替代）。

attribution/portfolio/execution 三类报告统一由 `report_intent` 直接映射到对应插件，
经 `_run_plugin_enhanced` 完成产物校验 + 插件委托 + metadata 组装（不进入通用匹配）：

| 优先级 | 触发条件 | 报告类型 | 生成插件 | 回答的问题 |
|--------|---------|---------|---------|-----------|
| 1 | `report_intent` ∈ {attribution/portfolio/execution} | 绩效归因/组合优化/执行监控 | `attribution_report` / `portfolio_report` / `execution_report` | 盈亏/组合/执行质量 |
| 2 | 有 BACKTEST 产物 | 回测绩效报告 | `backtest_report`（`output_file: report.html`） | 策略理论上能赚钱吗？ |
| 3 | 显式 `report_template` ∈ {technical/fundamental/both}（无 BACKTEST） | 模板化个股分析 | `technical_report` / `fundamental_report`，both 多命中 → `_run_plugin_many` 联合生成双份 | 个股技术/基本面 |
| 4 | 其余插件 trigger 匹配（产物存在等） | 因子分析等 | `factor_analysis_report` 等 | 这只股票值得买吗？ |
| 5 | 无任何插件命中 | 默认个股分析兜底 | `fallback_report`（fallback=true，委托 technical_report / fundamental_report 插件渲染） | 个股技术/基本面 |
| 6 | 无插件机制 / 无兜底插件 / 匹配异常 | 默认个股分析兜底 | `_run_builtin_fallback` 直接运行 `fallback_report` 插件（若兜底插件也缺失则返回失败，不再有独立内置模板内核兜底） | 个股技术/基本面 |

> **字段统一**：`report_intent` 与 `report_template` 均用于个股分析路由，二者在
> `_detect_report_template` 内相互兼容（report_intent 作为旧字段回退）。`both`
> 通过两个模板插件各自声明 `report_template=both` 触发条件，经 `find_by_trigger`
> 同时命中后由 `_run_plugin_many` 联合生成技术+基本面两份报告，并统一注册门户、
> 注入 LLM 解读、汇总 `report_data.json`。

### 自包含插件架构

7 份内置报告统一走**自包含插件**路径（`plugins/<id>/` 内含 render.py + 专属模板/辅助模块），render.py 仅依赖 `scripts.*` 公共库（含 `compute_report_data`），不依赖 `engine.py` 内部函数（含 fallback_report，已自包含，委托 technical_report / fundamental_report 插件渲染，无内置模板内核回调）。HTML 组装只在插件层发生，全项目仅一套「插件厨房」。

| 插件 id | report_type | 内容 | 专属模块 |
|--------|------------|------|---------|
| `technical_report` | technical | 技术分析 | `technical_report.html.j2` |
| `fundamental_report` | fundamental | 基本面分析 | `fundamental_report.html.j2` |
| `attribution_report` | attribution | 绩效归因 | `attribution_llm.py`（LLM 解读） |
| `portfolio_report` | portfolio | 组合优化 | `scripts.templates.portfolio_report` |
| `execution_report` | execution | 执行监控 | `scripts.templates.execution_report` |
| `backtest_report` | backtest | 回测绩效 | `backtest_helpers.py`（暴露计算） |
| `factor_analysis_report` | factor_analysis | 因子分析汇总 | `factor_analysis_report.html.j2` |

**公共库**（插件复用，不属任何单个插件）：`scripts/report_generator.py`（`ReportGenerator`，回测/归因共用）、`scripts/renderers`、`scripts/charts`、`scripts/templates`、`scripts/config.py`、`scripts/template_engine.py`（`compute_report_data` 供技术/基本面插件复用）。

**report_type 兼容**：各插件 plugin.yaml 声明 `report_type` 字段，`_run_plugin` 返回兼容元数据（保持 attribution/portfolio/execution 等原值），不破坏门户与测试契约。

### 绩效复盘报告（新增）

通过意图识别触发（关键词：复盘/绩效归因/归因分析/盈亏分析等），从 EXECUTION 阶段的 `ledger.jsonl` 产物中提取成交记录，通过 FIFO round-trip 归组实现完整盈亏归因分析。

**报告 7 个章节：**
1. 净值曲线 + 回撤图（从 ledger 重建净值序列）
2. 报告概览（核心指标卡片：总收益/胜率/盈亏比/最大回撤/夏普比）
3. 交易统计概览（总成交笔数、佣金/印花税/滑点汇总）
4. Round-Trip 盈亏归因（闭环交易列表、胜率、盈亏比、平均持仓天数）
5. 按标的盈亏明细（每只股票的总盈亏、交易次数、胜率）
6. 执行质量分析（费用占比 bps、滑点占比 bps、成交规模）
7. A 股压力期表现（2015 股灾/2016 熔断/2018 熊市/2020 疫情/2024 年初下跌）

**核心组件：**
- `AttributionAnalyzer`：成交记录解析 + FIFO 归组 + 多维度归因分析
- `attribution_report.py`：HTML 模板 + 图表生成
- LLM 深度解读：复用现有 LLM 注入机制，含规则兜底

### 量化投资者报告

1. **基础绩效指标**：年化收益、夏普、最大回撤、Calmar、胜率、Sortino、VaR
2. **QuantStats TearSheet**：交互式 HTML 报告
3. **Plotly 图表**：净值曲线、月度热力图
4. **风格暴露分析**：大盘/小盘/成长/价值
5. **申万行业归因**：利润贡献分解
6. **Brinson 归因**：配置效应 vs 选择效应

### 非量化投资者报告

根据 `ctx.metadata["investor_mode"]` 自动路由到个人投资者分析路径，按意图生成：

| 意图 | 生成报告 | 章节数 |
|------|---------|--------|
| technical | 技术面深度分析报告 | 9 章节 |
| fundamental | 基本面深度分析报告 | 10 章节 |
| both（默认） | 技术面 + 基本面两份报告 | 9 + 10 章节 |

**技术面报告 9 章节：**
1. 行情数据（交互式 K 线图 + 8 种可切换技术指标）
2. 报告概览（技术评分 + 综合评估）
3. 多周期趋势分析（日/周/月线）
4. 技术指标信号解读（MACD/RSI/KDJ/BOLL/WR/CCI/OBV）
5. K 线形态识别（锤子线/吞没/启明星等）
6. 关键价位分析（支撑位/阻力位/斐波那契）
7. 量价关系分析
8. 资金面分析（A股特色：北向资金、主力资金）
9. 龙虎榜解读（A股特色）

**基本面报告 10 章节：**
1. 行情数据（交互式 K 线图 + 8 种可切换技术指标）
2. 报告概览（基本面评分 + 综合评估）
3. 公司概况
4. 行业分析
5. 财务报表分析
6. 盈利能力分析
7. 成长性分析
8. 估值分析（PE/PB/PS/股息率）
9. 股东结构分析（A股特色：大股东/机构持仓/解禁/回购）
10. 风险因素

## LLM 内容注入

报告模板中包含 LLM 占位符（`<!--LLM_TECHNICAL_ANALYSIS_PLACEHOLDER-->` / `<!--LLM_FUNDAMENTAL_ANALYSIS_PLACEHOLDER-->`），agent 可在主调度器 `run_pipeline()` 时传入 `llm_responses` 参数自动替换。报告引擎内部通过 `llm_analyst.py` 的 `TechnicalAnalyst` 和 `FundamentalsAnalyst` 准备 prompt，供 agent 调用 LLM 后回填。

## K 线图功能

- 基于 TradingView lightweight-charts（CDN 加载）
- 支持 8 种可切换技术指标：成交量、MACD、RSI、KDJ、BOLL、WR、CCI、OBV
- 显示 MA5/MA10/MA20/MA60 均线
- 不显示支撑/阻力位标签（仅显示基本 K 线 + 均线）

## 报告结构（量化投资者）

1. 概览摘要（关键指标卡片）
2. 净值曲线与回撤图
3. 月度收益热力图
4. 绩效统计表
5. 风格暴露图
6. 行业归因图
7. Brinson 分解表
8. 完整交易记录

## 使用示例

### Python API

```python
from engine import run
from scripts.context import Context

# 量化投资者
ctx = Context(
    task_id="task_001",
    user_intent="生成报告",
    current_stage="IDLE"
)
result = run(ctx)

# 非量化投资者
ctx.metadata["investor_mode"] = "non_quant"
ctx.metadata["report_intent"] = "both"  # 或 "technical" / "fundamental"
result = run(ctx)
```

### CLI 运行

```bash
python engine.py -i "生成我的策略报告"
```

## 配置说明

详见 [references/config_guide.md](references/config_guide.md)

## API 文档

详见 [references/api_reference.md](references/api_reference.md)