---
name: jingni-trader
description: A股量化交易全流程主调度器。用于解析用户投研意图、管理投研阶段状态机、维护跨 Skill 上下文，按流程依次调度七个子 Skill 完成从数据采集、因子计算、策略建模、回测、组合优化到绩效报告的全链路分析工作；本身不执行任何量化计算，只做编排。触发关键词：量化、回测、选股、因子、实盘、组合优化、A股、策略开发、分析、技术面、基本面、诊股。首次使用请先运行 skill 根目录 install.py 安装依赖（默认国内加速镜像）。
license: MIT
compatibility: 需 Python 3.9+；入口 engine.py。数据源按需配置 TUSHARE_TOKEN / IFIND_USERNAME / IFIND_PASSWORD / JINGNI_URL / JINGNI_TOKEN / GM_TOKEN 等环境变量，默认降级链 local,westock,baostock,akshare,websearch 仅用免费源；需网络访问行情数据源。
metadata:
  jingni:
    version: "1.1.0"
    category: 技术
    author: quant-team
    created: "2026-08-14"
    updated: "2026-08-27"
    label: 惊泥量化交易主调度器
    maturity: beta
    description_for_human: A股量化投研全流程编排器，串联七个子引擎完成数据到报告的闭环。
    when_not_to_use: 不适用于单纯的行情查询或个股基础信息检索（应直接用数据源 skill）；不适用于非 A 股市场；不建议用于真实下单交易——本 skill 具备 live 实盘下单代码能力，但 live 链路无本引擎侧硬风控断路器，详见「交易模式与合规口径」。
    tags:
      - quant-trading
      - A股
      - master-skill
      - workflow
      - 量化
      - 调度器
    permissions:
      external: true
    compliance:
      basis: 内部量化研究规范；仅模拟回测与研究分析，不构成投资建议。默认 paper 模式不触碰真实资金，不执行真实委托下单。
      prohibited_self_check: 未触及禁止类；不提供代客理财、不承诺收益。实盘（live）为特别监管类：需使用者本人显式配置并自担风险，当前 live 链路无本引擎侧硬风控断路器，见「交易模式与合规口径」。
      data_source_trust: 第三方行情数据源（tushare/baostock/akshare/ifind 等），来源可信度依供应商授权口径，回测结论仅供研究参考。
    privacy: 本 skill 不采集个人身份信息；密钥一律经环境变量注入，不落盘明文。
    runtime:
      language: python
      python_version: "3.9+"
      entry_point: engine.py
      environment_variables:
        - name: TUSHARE_TOKEN
          description: Tushare Pro API Token（启用 tushare 数据源时需要；tushare 是 opt-in 源，默认不参与降级链）
          required: false
        - name: GM_TOKEN
          description: 掘金量化API Token，用于实盘交易
          required: false
        - name: IFIND_USERNAME
          description: 同花顺 iFinD 登录账号（启用 ifind 数据源时需要）
          required: false
        - name: IFIND_PASSWORD
          description: 同花顺 iFinD 登录密码（启用 ifind 数据源时需要）
          required: false
        - name: JINGNI_URL
          description: 惊泥因子库服务地址（启用 jingni-datafeed 因子库时需要）
          required: false
        - name: JINGNI_TOKEN
          description: 惊泥因子库 API Token（启用 jingni-datafeed 因子库时需要）
          required: false
        - name: DATA_BACKENDS
          description: 数据源优先级链，逗号分隔（如 "tushare,baostock,akshare,websearch"）。默认 "local,westock,baostock,akshare,websearch"（5 源免费链，local 为本地 Parquet 缓存且优先级最高）。用户对话指定时优先级高于此变量
          required: false
          default: "local,westock,baostock,akshare,websearch"
        - name: QUANT_WORK_DIR
          description: 数据和工作目录
          required: false
          default: "./workspace"
        - name: QUANT_FORCE_REFRESH
          description: 强制刷新所有阶段，忽略缓存产物（设为 "1" 启用）
          required: false
          default: "0"
        - name: FACTOR_BACKEND
          description: 因子计算后端（pandas_ta / talib），默认 pandas_ta（纯 Python，无需安装 C 依赖）
          required: false
          default: "pandas_ta"
        - name: BACKTEST_BACKEND
          description: 回测引擎后端（native / rqalpha / backtrader / gm），默认 native
          required: false
          default: "native"
        - name: ALLOW_SYNTHETIC_FALLBACK
          description: 全部数据源失败时是否生成模拟数据兜底（默认 false：显式报错、绝不静默合成行情；置 true 才生成模拟数据并告知用户）
          required: false
          default: "false"
        - name: AUTO_INSTALL_BACKENDS
          description: 数据源依赖缺失时自动 pip install 后重试（默认 false，遵循私有化纪律；置 true 时启用，且自动安装默认走清华 TUNA 加速镜像，可用 PIP_INDEX_URL 覆盖）
          required: false
          default: "false"
        - name: LOG_LEVEL
          description: 日志级别
          required: false
          default: "INFO"
      python_dependencies:
        - importlib (Python 标准库)
        - logging (Python 标准库)
        - json (Python 标准库)
      allowed_sub_skills:
        - data-engine
        - factor-engine
        - strategy-model-engine
        - backtest-engine
        - portfolio-risk-engine
        - execution-monitor-engine
        - reports-engine
      included_skills:
        - skills/data-engine
        - skills/factor-engine
        - skills/strategy-model-engine
        - skills/backtest-engine
        - skills/portfolio-risk-engine
        - skills/execution-monitor-engine
        - skills/reports-engine
        - skills/jingni-datafeed
      trigger_keywords:
        - 量化
        - 回测
        - 选股
        - 因子
        - 实盘
        - 组合优化
        - A股
        - 策略开发
        - 分析
        - 技术面
        - 基本面
        - 诊股
---

# jingni-trader

## 概述

jingni-trader 是量化交易 Skill 套件的**主协调中枢**，负责：

1. 解析用户自然语言意图，判断当前投研阶段
2. 管理任务状态机，按流程调度子 Skill
3. 维护会话状态和任务上下文
4. 输出结构化的量化研究报告

## 意图解析与路由

系统采用**单一工作流模型**：根据用户是否明确需要构建可回测/可交易策略，选择执行深度。因子计算/IC 分析本身是两条路径共用的前置步骤，不构成"策略构建"意图。

### 项目用户意图 — 阶段/场景/报告/引擎流程/触发规则对照表

agent 运行本 skill 时，根据下表匹配用户意图 → 调度对应引擎 → 生成对应报告：

| 投研场景 | 用户意图表述 | 意图标识 | 阶段路径 | 触发的引擎流程（按序） | 触发规则（关键词/条件） | 最终报告产物 |
|---------|------------|---------|---------|----------------------|----------------------|------------|
| **量化投研开发** | 因子分析 / IC分析 | `factor`（隐式附加） | DATA → FACTOR → REPORT | data-engine → factor-engine → reports-engine（`factor_analysis_report`） | FACTOR 产物存在时伴随触发；不进门户、不阻断主报告 | 因子分析报告（附加产物） |
| **量化投研开发** | 策略构建 / 回测验证 / 选股 | `strategy_required=True`（隐式） | DATA → FACTOR → MODEL → BACKTEST → PORTFOLIO → EXECUTION → REPORT | data-engine → factor-engine → strategy-model-engine → backtest-engine → portfolio-risk-engine → execution-monitor-engine → reports-engine（`backtest_report`） | 含"回测/策略/模型/选股/实盘/下单"等动作关键词 | 策略回测报告（净值曲线/夏普/回撤/行业归因） |
| **量化投研开发** | 组合优化 | `portfolio` | 同上（策略管线完整跑，REPORT 阶段命中 `portfolio_report`） | … → portfolio-risk-engine → reports-engine（`portfolio_report`） | `report_intent=portfolio` | 组合优化报告 |
| **量化投研开发** | 执行监控 | `execution` | 同上（策略管线完整跑，REPORT 阶段命中 `execution_report`） | … → execution-monitor-engine → reports-engine（`execution_report`） | `report_intent=execution` | 执行监控报告（账户概览/持仓/成交/委托） |
| **主观投研分析** | 个股技术面分析 | `technical` | DATA → FACTOR → REPORT | data-engine → factor-engine → reports-engine（`technical_report`） | `report_intent=technical` 或 `report_template=technical` 或含"技术面/K线/形态/指标"等关键词 | 技术分析报告（K线图/指标/资金面/龙虎榜） |
| **主观投研分析** | 个股基本面分析 | `fundamental` | DATA → FACTOR → REPORT | data-engine → factor-engine → reports-engine（`fundamental_report`） | `report_intent=fundamental` 或 `report_template=fundamental` 或含"基本面/财报/估值/财务"等关键词 | 基本面分析报告（财务数据/估值/股东结构） |
| **主观投研分析** | 个股综合分析（默认） | `both`（隐式） | DATA → FACTOR → REPORT | data-engine → factor-engine → reports-engine（`technical_report` + `fundamental_report`，`_run_plugin_many` 多命中聚合） | `report_template=both` / 同时命中技术面+基本面关键词 / 无明确意图的默认路径 | 技术分析报告 + 基本面分析报告（双报告联合生成） |
| **执行复盘** | 绩效归因 / 复盘 | `attribution` | DATA → FACTOR → EXECUTION → REPORT | data-engine → factor-engine → execution-monitor-engine → reports-engine（`attribution_report`） | `report_intent=attribution` 或含"绩效归因/归因分析/复盘/实盘报告/盈亏分析/交易复盘/绩效复盘"等关键词（**最高优先级**） | 绩效归因报告（Round-Trip 归因/成本分析/压力期表现） |
| **兜底** | 无明确意图 / 全部未命中 | — | DATA → FACTOR → REPORT | data-engine → factor-engine → reports-engine（`fallback_report`） | 无任何插件命中时，兜底插件 `fallback_report` 自动接管 | 默认个股报告（技术面+基本面） |

**默认走分析路径**：意图模糊或仅提及"因子/分析"时，`strategy_required=False`，因子仅用于分析，不构建策略。用户明确要求"回测/策略/实盘"等动作时才升级到完整 7 阶段管线。

### 路由实现

- `ctx.metadata["strategy_required"]`: 布尔标志，驱动 `target_stages` 选择
- `ctx.metadata["report_template"]`: 报告模板检测（technical/fundamental/both），**正交维度**，两条路径都设置
- `reports-engine` 已统一路由：根据是否存在 `BACKTEST` 产物自动选择生成绩效报告还是模板分析报告，无需上层区分

个股分析报告支持三种模板：**technical**（技术面深度分析，含A股特色：资金面、龙虎榜）/ **fundamental**（基本面深度分析，含A股特色：股东结构）/ **both**（技术面+基本面双报告，默认）。

### 报告插件机制（可扩展报告类型）

reports-engine 支持**报告插件**，新增报告 = 新增一个插件文件夹，无需改主引擎代码。

**插件位置**：`skills/reports-engine/plugins/<插件id>/`，含 3 个文件：
- `plugin.yaml`：报告声明（`trigger` 触发条件 + `requires` 所需产物 + `label`/`icon` 门户元信息）
- `render.py`：渲染器，实现 `render(data, ctx, output_path)` 生成 HTML
- `<报告id>.html.j2`：HTML 模板（继承 `base.html.j2` 复用统一骨架）

**触发机制**：`reports-engine.run()` 在 REPORT 阶段执行，路由已**收敛为两分支**：
- **分支 A（插件匹配）**：由 `_run_plugin_auto` 统一执行，优先级为：① `report_intent` ∈ {attribution/portfolio/execution} → 对应插件（`_run_plugin_enhanced`，含产物校验）；② BACKTEST 产物存在且回测插件命中 → 回测报告；③ 显式 `report_template`（无 BACKTEST）→ 模板类插件（`both` 多命中 → `_run_plugin_many` 生成双报告）；④ 其余 `find_by_trigger` 匹配；⑤ 全部未命中 → 兜底插件 `fallback_report`
- **分支 B（内置兜底）**：`_run_template_report`，仅当插件机制关闭 / 无插件命中且无兜底插件 / 匹配异常时作为终极兜底

**插件触发条件**支持三种方式（plugin.yaml 的 `trigger`）：
- 关键词识别：`{ keyword: ["资金流", "主力资金"] }` —— 用户输入含这些词即触发
- 字段精确匹配：`{ field: report_intent, equals: technical }` 或 `{ field: report_template, equals: both }`
- 产物存在性：`{ artifact: BACKTEST }`

**多命中聚合**：当多个插件同时命中（如 `report_template=both` 命中 technical+fundamental）时，
`_run_plugin_many` 联合生成多份报告，并统一注册门户、注入 LLM 深度解读、汇总 `report_data.json`。

**自定义输出文件名**：插件可在 `plugin.yaml` 声明 `output_file`（如回测插件声明 `report.html`），
生成时直接输出该文件名，无需复制改名。

**兜底插件**：声明 `fallback: true` 且无 trigger 的插件（`fallback_report`），在无任何插件命中时
承担默认个股报告（技术面+基本面）。

**示例**：内置 `technical_report` 插件通过 `report_intent=technical`（或关键词命中技术面）触发。用户说"分析 002594.SZ 的技术面"，REPORT 阶段命中该插件，生成 `technical_report.html` 技术分析报告。

**现有插件清单**（`plugins/` 目录）：
| 插件 id | 触发方式 | 说明 |
|---|---|---|
| `technical_report` | `report_intent=technical` / `report_template`∈{technical,both} | 技术分析报告 |
| `fundamental_report` | `report_intent=fundamental` / `report_template`∈{fundamental,both} | 基本面分析报告 |
| `attribution_report` | `report_intent=attribution` | 绩效归因（经 `_run_plugin_enhanced`） |
| `portfolio_report` | `report_intent=portfolio` | 组合优化（经 `_run_plugin_enhanced`） |
| `execution_report` | `report_intent=execution` | 执行监控（经 `_run_plugin_enhanced`） |
| `backtest_report` | `{ artifact: BACKTEST }`，`output_file: report.html` | 策略回测报告（直接输出 report.html） |
| `factor_analysis_report` | `{ artifact: FACTOR }` | 因子分析（附加产物，不进门户、不阻断主报告） |
| `fallback_report` | 无 trigger（`fallback: true`） | 兜底插件：无任何插件命中时生成默认个股报告 |

> 其中 `attribution_report` / `portfolio_report` / `execution_report` / `backtest_report`
> 均由插件机制承载（不再有独立内置路由分支）；`backtest_report` 声明 `output_file: report.html`
> 直接输出固定文件名，兼容下游缓存/门户/归档旧约定。

**开发新报告**：复制任一插件文件夹 → 改 `plugin.yaml`（触发词/所需产物）+ `render.py` + 模板 → 放入 `plugins/` 下即可被自动扫描注册。设计背景见 `docs/01-archive/prd/report-plugin-mechanism.md`（历史 PRD，口径以本节为准）。

### LLM 动态 Prompt 生成

reports-engine 的 llm_analyst 模块根据模板配置文件（`technical.yaml` / `fundamental.yaml`）中的 `factor_groups` 动态生成 LLM 系统提示词：

- 每个因子分组（factor_group）包含因子列表、渲染方式、分析要点提示（analysis_hint）
- TechnicalAnalyst 和 FundamentalsAnalyst 根据 factor_groups 动态构建指标参考说明和分析要点
- 当 factor_groups 为空时，自动回退到硬编码的默认指标说明
- 因子名称到中文说明的映射表维护在 `_TECHNICAL_FACTOR_DESCRIPTIONS` / `_FUNDAMENTAL_FACTOR_DESCRIPTIONS` 中

## 数据源优先级策略

**数据源优先级采用"对话优先 + 配置兜底"的设计：用户通过自然语言对话即可切换数据源，无需修改环境变量。**

### 完整优先级链（从高到低）

```
1. ctx.external_data (Agent 系统内置工具/MCP) — 最高，直接跳过降级链
2. ctx.data_sources (用户对话指定) — 用户通过对话明确要求时由 agent 写入
3. 环境变量 DATA_BACKENDS — 高级用户/CI 配置
4. 代码默认值 — 兜底（仅真正免费源）

   ⚠️ **口径以代码为准**：`skills/data-engine/scripts/config.py` 的 `DEFAULT_DATA_SOURCES` 为
   `local → westock → baostock → akshare → websearch`（5 源，按优先级降级）。
   本文件 frontmatter 的 `compatibility` 字段已同步为 `local,westock,baostock,akshare,websearch`
   （其中 `tencent` 为历史旧名，**现名 `westock`**，同一腾讯公网直连免费源）。
   代码真值优先，文档与代码不一致时以 `config.py` 为准。
5. synthetic (模拟数据兜底) — 仅当 `ALLOW_SYNTHETIC_FALLBACK=true` 时启用；
   默认 `false`（`config.py:262`），外部源全部失败即显式报错、不静默合成行情
```

### 用户对话式切换数据源（推荐方式）

用户直接与 agent 对话即可指定数据源优先级，agent 会解析意图并写入 `ctx.data_sources`，覆盖环境变量：

| 用户说什么 | agent 解析结果(ctx.data_sources) |
|-----------|--------------------------------|
| "用 wind 取数据" | `["wind", "baostock", "akshare", "websearch"]` |
| "优先用 ifind" | `["ifind", "baostock", "akshare", "websearch"]` |
| "优先用 ifind，失败用 tushare" | `["ifind", "tushare", "baostock", "akshare", "websearch"]` |
| "用 tushare 取数据" | `["tushare", "baostock", "akshare", "websearch"]` |
| "用万得取数据" | `["wind", ...]`（中文别名） |
| "用同花顺取数据" | `["ifind", ...]`（中文别名） |
| "用 baostock 和 akshare" | `["baostock", "akshare", "websearch"]` |
| （未提及数据源） | `None`（走环境变量 → 默认值） |

**判定规则**（避免误触发）：
- 必须同时命中"动作动词"（用/使用/优先/首选/改用/切换/换/use/using/from）和"数据源名称"（tushare/baostock/akshare/wind/ifind/万得/同花顺/掘金/通达信/迅投）
- 用户只指定部分源时，自动追加默认免费降级链作为兜底
- 未识别到数据源意图时，`ctx.data_sources` 保持 None，由 data-engine 走环境变量/默认值

### 可选数据源（opt-in 源）

以下数据源默认不参与降级链，需要用户通过对话明确指定或通过 `DATA_BACKENDS` 环境变量启用：

| 数据源 | 中文名 | 前置条件 |
|--------|--------|---------|
| `tushare` | Tushare Pro | TUSHARE_TOKEN（商业 API，有免费额度） |
| `wind` | 万得 | Wind 金融终端 + WindPy |
| `ifind` | 同花顺 iFinD | iFinDPy + 账号密码（`IFIND_USERNAME`/`IFIND_PASSWORD`） |
| `xtquant` | 迅投 QMT/xtp | 本地券商客户端 |
| `gm` | 掘金量化 | GM_TOKEN + 付费 SDK |
| `tdxquant` | 通达信量化 | 本地通达信金融终端 TQ 策略 |

### 默认免费数据源（无需任何配置）

| 数据源 | 说明 |
|--------|------|
| `local` | 本地 Parquet 缓存（不联网，免费链中优先级最高） |
| `westock` | 腾讯公网行情直连（免费、零鉴权；旧名 `tencent`） |
| `baostock` | 老虎量化开源项目（无需 Token） |
| `akshare` | 聚合库爬虫（无需 Token） |
| `websearch` | 通过 WebSearch 工具查询（终极回退，仅 get_daily） |

> 免费链降级顺序：`local → westock → baostock → akshare → websearch`
> （与 `skills/data-engine/scripts/config.py` 的 `DEFAULT_DATA_SOURCES` 及 `DATA_FALLBACK_RULES` 一致）。

### 精准降级

降级链中某个源失败时，只在该源**特定异常类型**触发时才切换到下一源（如 tushare 的 `QuotaExceededError`/`RateLimitError`），避免普通错误误降级。详见 data-engine 的 `DATA_FALLBACK_RULES`。

### 数据源依赖自动安装

当某数据源适配器所需的第三方库尚未安装时，data-engine 不会直接跳过该数据源，而是先尝试用当前 Python 解释器自动安装依赖（`pip install`），安装成功后再加载并使用该数据源；仅当自动安装失败时才会按降级链跳到下一个数据源。

- 开关：`AUTO_INSTALL_BACKENDS`（默认 `false`，遵循私有化纪律；置 `true` 时启用运行期自动安装）
- 后端与 pip 包映射（见 `skills/data-engine/scripts/config.py` 的 `BACKEND_PIP_PACKAGES`）
- 自动安装结果会被缓存，避免在同一次运行的降级链里重复安装
- **安装镜像**：自动安装与手动安装均默认走国内加速镜像（清华 TUNA `https://pypi.tuna.tsinghua.edu.cn/simple`）。覆盖方式见下方「依赖安装镜像」。

### 依赖安装（国内加速镜像）

三方库安装默认走**清华 TUNA 国内加速镜像**。手动安装（推荐）用 skill 根目录的跨平台脚本（纯标准库、零依赖，自动锚定 skill 根，不受 cwd 影响）：

```bash
cd core/skills/jingni-tech/jingni-trader

install.bat        # Windows
bash install.sh    # Linux / macOS
python install.py  # 跨平台通用
```

常用参数与镜像覆盖：

```bash
python install.py --index-url https://mirrors.aliyun.com/pypi/simple  # 覆盖镜像
python install.py --dry-run                                          # 仅打印命令，不安装
python install.py --requirements <路径|目录>                           # 指定依赖清单
python install.py -- --upgrade --no-cache-dir                        # `--` 后原样透传 pip
```

> 镜像优先级：`--index-url` > `PIP_INDEX_URL` 环境变量 > 默认清华镜像。运行期自动安装（`AUTO_INSTALL_BACKENDS=true`）同样注入默认镜像，但用户已设 `PIP_INDEX_URL` 时不被覆盖；`requirements.txt` 内不写镜像源（pip 不支持）。安装失败时脚本打印备选镜像（阿里云/腾讯云）与排查建议，退出码与 pip 一致，便于 CI 判定。

### 高级：环境变量配置（可选）

如果用户希望通过环境变量持久化配置数据源优先级（适合 CI/服务器场景），可设置 `DATA_BACKENDS`：

```bash
# Linux/Mac
export DATA_BACKENDS=wind,tushare,baostock,akshare,websearch

# Windows PowerShell
$env:DATA_BACKENDS = "wind,tushare,baostock,akshare,websearch"
```

环境变量优先级低于 `ctx.data_sources`：用户在对话里说"用 wind"会立即覆盖环境变量配置。

## 运行归档机制

每次运行自动在 `workspace/archives/` 下创建 `YYYYMMDD_HHMMSS` 归档目录，每阶段存入 `step_N_<阶段名>/`（含 `summary.md` 与 `artifacts/`），根目录生成全景汇总 `pipeline_summary.md`。目录结构示例见 [references/workflow_architecture.md](references/workflow_architecture.md)。

## 阶段状态机

单一工作流，按 `strategy_required` 标志选择执行深度：默认分析路径（`False`）为 DATA → FACTOR → REPORT；策略构建路径（`True`）为完整 7 阶段管线（DATA → FACTOR → MODEL → BACKTEST → PORTFOLIO → EXECUTION → REPORT）。

完整分支图、各阶段失败回退逻辑见 [references/workflow_architecture.md](references/workflow_architecture.md) §阶段状态机。

## LLM 内容注入

个股报告含 LLM 占位符（`<!--LLM_TECHNICAL_ANALYSIS_PLACEHOLDER-->` / `<!--LLM_FUNDAMENTAL_ANALYSIS_PLACEHOLDER-->`），调用 `run_pipeline()` 时传入 `llm_responses` 参数即自动替换：

```python
result = engine.run_pipeline(
    user_input="分析 002594.SZ 比亚迪的技术面和基本面",
    llm_responses={
        "technical": {"overall_assessment": "...", "technical_score": 75, ...},
        "fundamental": {"overall_assessment": "...", "fundamental_score": 82, ...},
    }
)
```

## Context 对象

标准化上下文对象（task_id / user_intent / current_stage / target_stages / stock_pool / start_date / end_date / artifacts / metadata / errors 等）。

完整字段表、类型与默认值见 [references/context_protocol.md](references/context_protocol.md)。

## 使用流程

### 环境准备（首次使用必读）

运行前先检查依赖完整性；若依赖缺失（如 `import pandas` / `import plotly` 报 ModuleNotFoundError），运行 skill 根目录安装脚本预装：

```bash
cd core/skills/jingni-tech/jingni-trader
python install.py      # 默认走清华 TUNA 国内加速镜像
```

更多参数（`--dry-run` / `--index-url` 等）见上文「依赖安装（国内加速镜像）」。

## 使用示例

### CLI 运行（常用）

```bash
python engine.py -i "帮我用近3年A股数据做一个20日反转因子选股回测"
python engine.py -i "分析 002594.SZ 比亚迪的技术面和基本面"
python engine.py -c ./workspace/context.json        # 用已有 Context JSON 恢复运行
python engine.py -i "生成上个月实盘绩效报告" -o ./workspace/result.json
python engine.py -i "分析比亚迪基本面" --force       # 忽略缓存，重跑所有阶段
```

> **CLI 参数集（实测口径）**：仅支持 `-i/--input`（必填）、`-c/--context`、`-o/--output`、`--force` 四个参数。
> 股票池、日期范围等任务参数请在 `-i` 的自然语言描述中指定（如 `"分析 002594.SZ 比亚迪"`），由意图解析自动提取。

### Python API

```python
from engine import run, MasterEngine
from scripts.context import Context

ctx = Context(
    task_id="task_001",
    user_intent="帮我用近3年A股数据做一个20日反转因子选股回测",
    current_stage="IDLE"
)
result = run(ctx)

# 个股分析（含 LLM 内容注入）
engine = MasterEngine()
result = engine.run_pipeline(
    user_input="分析 002594.SZ 比亚迪的技术面和基本面",
    llm_responses={"technical": {...}, "fundamental": {...}}
)
```

完整 API 签名、Context 构造与更多示例见 [references/api_reference.md](references/api_reference.md)。

## 子 Skill 映射

| 阶段 | 对应子 Skill | 说明 |
|------|-------------|------|
| DATA | data-engine | 多源数据采集与清洗 |
| FACTOR | factor-engine | 因子计算、IC分析、多因子融合 |
| MODEL | strategy-model-engine | 模型训练与超参优化 |
| BACKTEST | backtest-engine | 策略回测与绩效评估 |
| PORTFOLIO | portfolio-risk-engine | 组合优化与风控 |
| EXECUTION | execution-monitor-engine | 实盘执行与监控 |
| REPORT | reports-engine | 量化绩效报告 / 个股分析报告 |

## 数据源 / 交易文档索引

各子引擎 `references/` 下的技术参考文档（按职责拆分：数据获取 `_data.md` 归 data-engine，实盘交易 `_trading.md` 归 execution-monitor-engine）：

> **仓库层级说明（重要）**：本 skill 的单一权威源是 `core/skills/jingni-tech/jingni-trader/`，
> 同步链为单向（权威源 → `d:/codebuddy/jingni-trader` → GitHub），禁止反向覆盖。
> `core/ir/_shared/skills/jingni-trader/` 是本文件单向镜像出的**裁剪引用版**，不独立演进——
> `execution-monitor-engine` 仅保留 `references/`，实盘运行代码按红线剔除不并入共享层。
> 因此本文档中出现的 `execution-monitor-engine` 均为**编排关系与能力声明**。
> 完整规范见 `knowledge/engineering/jingni-trader-source-of-truth.md`。

**数据引擎（data-engine，6 份数据源文档）**
- `skills/data-engine/references/westock_data.md` — weStock 数据命令参考
- `skills/data-engine/references/baostock_data.md` — 宝盛开源行情数据接口
- `skills/data-engine/references/akshare_data.md` — AKShare 开源财经数据源
- `skills/data-engine/references/xtquant_data.md` — 迅投 XtData 行情模块
- `skills/data-engine/references/tdxquant_data.md` — 通达信量化数据接口
- `skills/data-engine/references/gm_data.md` — 掘金量化数据接口

**执行监控（execution-monitor-engine，3 份交易文档）**
- `skills/execution-monitor-engine/references/xtquant_trading.md` — miniQMT 交易模块
- `skills/execution-monitor-engine/references/tdxquant_trading.md` — 通达信交易接口
- `skills/execution-monitor-engine/references/gm_trading.md` — 掘金交易函数/事件

## jingni-datafeed 自动部署

jingni-trader 可选依赖 `jingni-datafeed`（惊泥因子库 datafeed 服务），该子 skill 独立维护在 [duhanjun/jingni-datafeed](https://github.com/duhanjun/jingni-datafeed)。

**启动时自动检测**：MasterEngine 实例化时自动检查 `skills/jingni-datafeed/` 目录：
- **目录不存在** → 自动从 GitHub 克隆（`git clone --depth 1`），用户无需手动操作
- **目录已存在** → 运行正常的版本检查（只检测落后、不自动修改文件）

自动克隆失败时**不会阻断主流程**，仅输出警告日志。如需手动安装：

```bash
cd jingni-trader
git clone https://github.com/duhanjun/jingni-datafeed.git skills/jingni-datafeed
cp skills/jingni-datafeed/.env.example skills/jingni-datafeed/.env   # 编辑填入 JINGNI_URL / JINGNI_TOKEN
```

## 里程碑检查点与错误处理

每个子 Skill 完成后自动检查**产物完整性**与**基本合理性**，失败时给出清晰错误码并支持从断点重试；所有子 Skill 调用均含异常捕获、明确错误信息与优雅降级策略。

## 交易模式与合规口径

> **责任主体：风控官魏预警**（jingni-trader 执行与风控口径责任人）
> 本节的对外表述由风控官负责，任何改动须经风控官复核。

**一句话结论**：默认 paper 模拟交易，不触碰真实资金；live 实盘需显式配置 `TRADE_MODE=live` + `TRADE_BACKEND` 才会真实报单，且**当前 live 链路不受本引擎硬风控断路器约束**——启用实盘等同自行承担全部风险。

完整风险卡片（逐条代码依据）、**合规边界（禁止类/特别监管类）**、启用实盘前强制确认清单与免责声明见
[references/compliance-trading-mode.md](references/compliance-trading-mode.md)。

> **免责声明（投资内容强制三段式）**：仅模拟、绝不下单、不构成投资建议。默认 paper 模式不触碰真实资金；live 实盘为使用者显式开启后以其自身券商账户执行，非本工具主动代客下单，风险由使用者自行承担。

## 配置说明

详见 [references/config_guide.md](references/config_guide.md)

## API 文档

详见 [references/api_reference.md](references/api_reference.md)

## Context 协议

详见 [references/context_protocol.md](references/context_protocol.md)

## 工作流架构

详见 [references/workflow_architecture.md](references/workflow_architecture.md)

## 版本历史

| 版本 | 日期 | 变更摘要 |
|------|------|---------|
| 1.1.0 | 2026-08-27 | 新增「交易模式与合规口径」章节与 `references/compliance-trading-mode.md`；README 补投资合规免责声明（责任主体：风控官魏预警）；修正 `when_not_to_use` 与 `compliance` 的实盘能力表述（原称"不用于真实下单交易"，与代码能力不符）；SKILL.md 正文瘦身至 FR-06 的 500 行约束内，明细外置 `references/`；免费降级链口径由 `tencent` 统一为 `westock` |
| 1.0.0 | 2026-08-14 | 首个版本：七子引擎全链路编排、报告插件化机制、11 数据源降级链 |