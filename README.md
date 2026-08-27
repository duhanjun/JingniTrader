# jingni-trader

> ## ⚠️ 免责声明（请先阅读）
>
> **仅模拟、绝不下单、不构成投资建议。**
>
> 1. **默认仅模拟**：本工具默认 `TRADE_MODE=paper`（模拟交易），**不触碰真实资金、不下真实订单**。
>    ⚠️ **前提检查（必做）**：该默认值可被**操作系统级环境变量覆盖**。部署前务必执行 `echo $TRADE_MODE`（Linux/macOS）或 `echo %TRADE_MODE%`（Windows）确认实际生效值。若输出为 `live`，说明本工具**已处于实盘状态**，此时任何走到 EXECUTION 阶段的管线都会向券商真实报单。已知触发词包含「组合」「风控」「回测」「策略」「实盘」「下单」等日常高频词，误触发风险真实存在。
>    - 检查并清除：`TRADE_MODE` / `TRADE_BACKEND` / `GM_TOKEN` / `GM_ACCOUNT_ID` / `XTQUANT_ACCOUNT` 是否被写入用户级环境变量（Windows 注册表 `HKCU\Environment`，或 shell 配置文件 `~/.bashrc` / `~/.zshrc`）。
> 2. **绝不下单**：本工具不提供、不代客、不接受委托从事任何证券投资咨询或代客理财服务；所有输出均为技术与数据层面的**研究参考**，不构成任何买卖要约或投资建议。
> 3. **实盘风险自担**：`live`（实盘）模式为使用者**显式配置后**以其自身券商账户执行，且当前 live 链路**不受本引擎硬风控断路器约束**——两个实盘适配器（`XtQuantExecutor` / `GMExecutor`）的 `send_order` 均不含任何断路器调用，风控完全依赖券商终端与账户自身设置。启用实盘即视为自愿承担全部风险。
>
> 证券投资有风险，入市需谨慎。据本工具输出进行的任何投资决策及其后果，由使用者自行承担。

**像聊天一样做研究、决策、执行、复盘的全栈AI投资工作台。**

基于大语言模型的A股投研决策与交易执行全栈工作流引擎。融合**量化投研**与**主观投研**双路径，通过自然语言交互即可驱动数据采集、因子分析、模型训练、回测验证、组合优化、模拟/实盘交易，直至生成策略回测报告、股票分析/技术面报告、基本面报告、组合优化报告、执行监控报告、绩效归因报告等多类报告（报告引擎插件化，详见 `SKILL.md`）。以"研究→决策→执行→复盘"为闭环，将专业级投研与风控能力转化为普通投资者也能轻松驾驭的智能投资决策工作台。

## 系统特性

- **自然语言驱动**：用中文描述需求，系统自动解析意图并执行对应投研流程，无需编写代码或切换多个工具。
- **全栈投资闭环**：覆盖从数据研究、策略/个股决策、模拟/实盘执行到绩效复盘归因的完整投资链路（**默认模拟交易，实盘需显式开启**，详见「交易模式与合规口径」）。
- **投研模式融合**：量化策略回测（因子→模型→回测→策略回测报告）与主观个股深度分析（数据→指标→分析报告）共享基础设施，分叉后统一汇入交易执行管道。
- **多类报告插件化**：报告引擎以插件框架统一产出多类报告，含策略回测、股票分析（技术面/基本面）、绩效归因、组合优化、执行监控、因子分析等类型，明细与触发规则以 `#7 报告生成引擎` 章节的"插件报告类型（8 个）"表格或 `SKILL.md` 用户意图对照表为权威口径。
- **流程断点续跑**：每个阶段的产物独立存储，已完成的阶段自动跳过，研究过程随时中断和恢复。
- **硬风控断路器**：内置单日亏损限制、单笔金额上限、订单频率限制等多层风险控制（明细以 `#6 执行监控引擎` 章节表格为准）。⚠️ **仅对模拟交易（paper）生效**——`CircuitBreaker` 仅在 `PaperExecutor` 中实例化与调用，两个实盘适配器的 `send_order` 不含任何断路器调用，**实盘模式无本引擎侧风控**。另注意：风控阈值存在入口依赖（两套 config 默认值不同），详见 `SKILL.md`「交易模式与合规口径」。
- **功能模块架构**：7个独立子引擎与功能模块一一对应，支持按需组合和扩展。
- **多数据源降级**：支持11种数据源（免费链 5 源 local/westock/baostock/akshare/websearch + 按需调用组 6 源 tushare/gm/xtquant/tdxquant/wind/ifind），用户可在对话中直接切换优先级，含精准降级与模拟数据兜底。
- **系统运行归档**：每次运行自动创建时间戳归档目录，保存全部过程和产物，便于回顾与审计。

## 系统架构

系统采用"Y型分叉 + 尾部合并"架构：投研按量化与主观双线展开，各自独立输出决策报告；之后共同汇入统一的仓位管理与交易执行管道，最终完成绩效复盘。

```
                        【用户输入需求】

                               │
                       ┌───────┴───────┐
                       │    意图解析    │
                       └───────┬───────┘
                               │
              ┌────────────────┴───────────────┐
              │                                │
       【量化投研模式】                   【主观投研模式】

       研究：DATA → FACTOR              研究：DATA   →   FACTOR
                     ↓                                    ↓
            MODEL ←──┘                     [人机协同]   ←──┘
              ↓                                ↓
          BACKTEST                             ↓
              ↓                                ↓
        决策：策略回测报告              决策：股票分析报告
              │                                │
              └──────────────┬─────────────────┘
                             │
                     ┌───────┴───────┐
                     │   PORTFOLIO   │  组合优化 / 风险管理
                     └───────┬───────┘
                             │
                     ┌───────┴───────┐
                     │   EXECUTION   │  模拟交易(默认) / 实盘交易(需显式开启)
                     └───────┬───────┘
                             │

                    复盘：绩效归因报告
```

## 功能模块

7 个子引擎与全栈流水线环节一一对应，支持按需组合和扩展：

### 1. 数据采集引擎 (data-engine) — 数据采集
多源A股数据采集，自动复权、标记涨跌停和ST，支持11种数据源，对话中可直接切换优先级。

#### 数据源能力矩阵（11 数据源 × 16 数据细项）

| 数据细项 | local | westock | baostock | akshare | websearch | tushare | wind | ifind | tdxquant | xtquant | gm |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 股票列表 | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 交易日历 | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 复权因子 | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 基本信息 | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 实时行情 | — | ✅ | — | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 日线行情 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| K线全粒度 | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| 财务指标 | ◐ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ◐ | ✅ | ⚠️ |
| 三大报表 | ◐ | — | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | ✅ | ⚠️ |
| 资金面 | — | ✅ | — | ✅ | — | ✅ | ✅ | ✅ | — | — | — |
| 龙虎榜 | — | ✅ | — | ✅ | — | ✅ | ✅ | ✅ | — | — | — |
| 股东结构 | — | ✅ | — | ✅ | — | ◐积分 | ✅ | ✅ | ◐文件 | — | — |
| 分红派息 | — | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | — | — |
| 停复牌 | — | ✅ | — | ✅ | — | ✅ | ✅ | ✅ | — | — | ⚠️ |
| 限售解禁 | — | ✅ | — | ✅ | — | ⚠️ | ✅ | ✅ | — | — | — |
| 业绩预告 | — | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | — | — |

图例：✅ = 真实支持 · ◐ = 受限/间接 · ⚠️ = 名义支持 · — = 不支持

数据源开放策略：
- **免费且默认开启（5 源免费链，自动参与降级）**：local（本地 Parquet 缓存，最高优先）、westock、baostock、akshare、websearch —— 开箱即用，无需配置；降级顺序 local→westock→baostock→akshare→websearch（与 `DATA_BACKENDS` 默认链一致）。local 为本地已落盘缓存（get_daily/若有预置 get_financial），websearch 仅经 WebSearch 工具作终极回退（仅 get_daily）。
- **按需调用组（需手动开启，共 6 源）**：tushare（TUSHARE_TOKEN）、gm（GM_TOKEN）、xtquant（本地券商客户端）、tdxquant（本地通达信终端）、wind（Wind 金融终端）、ifind（iFinD 账号） —— 需凭证/环境并对话或 `DATA_BACKENDS` 环境变量显式启用，不进默认链（避免默认链引发现金/终端依赖）。

数据源备注：
- **westock** 实时行情：✅（支持；get_realtime_quote 经 qt.gtimg.cn 实时报价，数据细项含 market_realtime；kline 历史线命令标注「数据有延迟，不代表盘中实时价」，与实时报价命令是两条独立路径）
- **tdxquant**（2026-08-18 逐接口实测锚点 600519.SH，客户端 tqcenter 已初始化连接）：
  - 股票列表 `✅`（`get_stock_list(market='5')` 实测返回 5554 只 A 股）；交易日历 `✅`（`get_trading_dates` / `get_trading_calendar` 实测返回交易日序列）。
  - 复权因子 `✅`（`get_market_data` 传 `dividend_type='none'` 可返回 `ForwardFactor` 前复权因子字段，月线实测因子随除权由 0.978866→1.0 变化；另有 `get_divid_factors` 返回历史分红送配明细，实测 11 条）。
  - K线全粒度 `⚠️名义支持`：日/周/月/季/年等 bar 级周期实测可用（日线、周线、月线均返回真实OHLCV）；但分钟级（`1m/5m/15m/30m/1h/60m`）实测 `get_market_data` 均返回空 `{}`，需客户端单独下载分钟数据/L2，分钟级不可用。
  - 财务指标 `◐`：仅能经 `get_stock_info` 返回的部分每股指标（每股收益 J_mgsy、每股净资产 J_mgjzc、ROE J_jyl、净利润 J_jly、总资产 J_zzc 等）间接获得基础指标；专业财务报表字段（`get_financial_data` FN1~FN584、`get_financial_data_by_date`）实测返回异常/`--`，需客户端下载专业财务数据包后方可用，故**三大报表** `—` 不支持。
  - 资金面 `—`：`get_gpjy_value`（含融资融券/陆股通/主力资金）实测返回 `null`，`get_scjy_value`（市场交易数据）实测返回 `None`，均需专业数据包；`get_bkjy_value`（板块级）仅部分字段可用（BK9 涨跌数等），不构成个股资金面。
  - 龙虎榜 `—`：字段存在于 `get_gpjy_value`（GP02/GP08/GP09/GP17/GP18/GP42），但实测返回 `null`，需专业数据包。
  - 股东结构 `◐文件`（受限）：`get_gpjy_value(GP01 股东人数)` 实测返回 `null`；但 `download_file` 下载十大股东文件实测成功（返回 `下载十大股东数据[2024]成功`），故依赖本地文件为受限可用。
  - 分红派息 `✅`：`get_divid_factors` 实测返回贵州茅台 11 条分红送配记录（含 Bonus/ShareBonus/Allotment 等）。
  - 停复牌 `—`：口径依据 SKILL 文档 `get_more_info` 的 `TPFlag` 字段，但当前 tqcenter.py 类中无 `get_more_info` 方法（实测 AttributeError），无法提供。
  - 限售解禁 / 业绩预告 `—`：无对应接口。
  - 可转债 `get_cb_info`、新股 `get_ipo_info` 实测为空（需对应数据/license），不计入上述 16 项。
- **tushare** 股东结构：`◐积分`（受限，需积分解锁）
- **baostock** 停复牌：`—`（不支持；无停复牌专项接口）
- **baostock** 三大报表：`—`（不支持；仅支持季频财务指标：query_profit_data 盈利能力 / query_operation_data 营运能力 / query_growth_data 成长能力 / query_balance_data 偿债能力 / query_cash_flow_data 现金流量 / query_dupont_data 杜邦指数，无科目级三大报表；2026-08-18 逐接口实测核验）
- **westock** 业绩预告：✅（支持；get_forecast 经 calendar --event financial_report 映射返回业绩预告 EPS；calendar 另返回财报发布日、disclosure 为财报预约披露日，注意区分）
- **gm** 交易日历 / 财务指标 / 三大报表 / 停复牌：`⚠️名义支持`（名义支持，实际可用性受限）

### 2. 因子计算引擎 (factor-engine) — 因子计算
表达式引擎、Alpha158因子库、IC分析、提前期偏差检测，同时支撑量化阿尔法挖掘和主观技术/财务指标计算。

**内置因子清单（按类型）**

| 因子类型 | 内置因子 | 说明 |
|---------|---------|------|
| 动量/反转 | `momentum_20d` / `momentum_60d` / `reversal_5d` / `reversal_20d` | 20/60 日动量，5/20 日反转 |
| 规模 | `lncap` | 对数市值 |
| 交易 | `turnover_20d` / `turnover_5d` / `turnover_change` / `volume_ratio` | 换手率（20/5 日）、换手变化、量比 |
| 波动率 | `volatility_20d` | 20 日波动率 |
| 资金流 | `money_flow_20d` | 20 日累计资金流 |
| 表达式引擎 | KDJ / RSI / MACD / 布林带等 | 声明式因子 DSL（如 `RANK(DELTA($close, 5))`） |
| Alpha158 扩展库 | 47 个因子 × 6 大类 | 动量/反转 12 · 波动率 8 · 成交量/换手率 8 · 技术指标 10 · 资金流向 5 · 其他 4 |

**因子分析能力**

| 能力 | 说明 |
|------|------|
| IC 分析 | Pearson IC / Spearman Rank IC / 向量化 IC（`IC_TYPE` 切换 normal/spearman/rank） |
| 行业中性化 | 市值 + 行业中性回归取残差 |
| 相关性去冗余 | 因子相关性分析，相关性阈值可配（默认 0.7） |
| 多因子融合 | 等权 / IC 加权融合（含 NaN 隔离） |
| 提前期偏差检测 | 自动检测因子计算中的未来数据泄露 |
| 因子预处理管线 | 7 个内置 Processor：中性化 / 去极值 / 缺失填充 / 标准化 / IC 分析 / 相关性过滤 / 融合（`pipeline.yaml` 声明式配置） |
| 技术指标后端 | pandas_ta（默认，纯 Python）与 TA-Lib 双后端，`FACTOR_BACKEND` 切换 |
| 高性能 DataFrame 后端 | IC/中性化/IC Decay/相关性热路径支持 pandas/polars 双后端，可通过 `auto` 模式自动探测 polars 可用性（polars 提速 5-15×） |
| 惊泥因子库集成 | 可选从 jingni-datafeed 加载已沉淀因子（`JINGNI_URL`/`JINGNI_TOKEN`） |
| Alphalens 因子报告 | `QUANT_ALPHALENS_REPORT=1` 时输出分层收益/IC/换手/摘要等 4 PNG + 1 HTML + 1 JSON |

### 3. 模型训练引擎 (strategy-model-engine) — 模型训练
LightGBM/CatBoost/随机森林等模型，Optuna自动调参，Purged时序交叉验证防过拟合。

**内置训练模型**

| 算法/模型类型 | 适用场景 | 说明 |
|--------------|---------|------|
| LightGBM | 截面多因子选股 | 默认模型（`MODEL_TYPE=lightgbm`），速度快、效果好 |
| CatBoost | 含类别特征的截面选股 | 自动处理类别特征 |
| 逻辑回归 | 涨跌二分类预测 | 简单可解释 |
| 随机森林 | 非线性特征交互 | 防过拟合能力较强 |

**训练方法与机制**

| 方法/机制 | 说明 |
|----------|------|
| Optuna 自动调参 | 超参搜索（默认 100 次 / 3600s 超时，`OPTUNA_TRIALS`/`OPTUNA_TIMEOUT` 可配） |
| Purged Group Time Series Split | 分组时序交叉验证防过拟合：训练 36 月 / 验证 12 月 / 测试 12 月，清洗期 2 天（`PURGE_GAP_DAYS`） |
| 标签体系 | regression / classification 双类型（`LABEL_TYPE`），默认 T+20 日前视收益标签（`FORWARD_PERIOD=20`） |
| MLflow 实验管理 | 实验追踪与模型版本管理（`MLFLOW_TRACKING_URI`/`MLFLOW_EXPERIMENT_NAME`，默认 jingnitrader），joblib 保存模型 |

### 4. 回测引擎 (backtest-engine) — 回测验证
严格A股规则（T+1、涨跌停、真实费率），输出绩效指标，支持Walk-Forward稳健性检验。

**回测 A 股规则**

| 规则 | 实现说明 |
|------|---------|
| T+1 交割 | 当日买入次日才能卖出（`Position.last_buy_date` 严格校验） |
| 涨跌停板 | 涨停无法买入，跌停无法卖出 |
| 停牌处理 | 资产冻结，复牌后以开盘价恢复 |
| 佣金 | 万 2.5（最低 5 元） |
| 印花税 | 1‰，单边卖出 |
| 过户费 | 0.02‰，双侧 |
| 滑点 | 买入价 ×(1+slippage)、卖出价 ×(1−slippage)，双侧应用 |
| 最小交易单位 | 100 股 |

**回测绩效指标**

| 指标/输出 | 说明 |
|----------|------|
| 年化收益 / 夏普 / 最大回撤 / 胜率 / Calmar / Sortino | 完整绩效指标体系 |
| alpha / beta / excess_return / benchmark_max_drawdown | 基准对比（默认基准 000300.SH，`BENCHMARK` 可配） |
| gross_return vs net_return / total_cost_drag | 成本分离：不含费用与含费用收益对比 |
| HTML / JSON 双格式 | 可视化报告与结构化数据输出 |
| 原生实现 | 纯 pandas/numpy，无外部回测框架依赖（初始资金默认 100 万，`INIT_CAPITAL` 可配） |

### 5. 组合优化引擎 (portfolio-risk-engine) — 组合优化
接收量化或主观决策信号，进行权重优化、风险预算分配，遵守行业与个股权重约束。

**组合优化方法**

| 优化方法 | 说明 | 后端 |
|---------|------|------|
| max_sharpe（默认） | 最大夏普比率组合 | PyPortfolioOpt |
| min_variance | 最小方差组合 | PyPortfolioOpt |
| risk_parity / hierarchical_risk_parity / hrp | 分层风险平价（HRP，三者为别名） | PyPortfolioOpt |
| black_litterman | Black-Litterman 模型 | PyPortfolioOpt |
| cvar | CVaR 最优化（需安装 cvxpy） | cvxpy |

**约束与风控机制**

| 机制 | 说明 |
|------|------|
| 个股权重上限 | 单一股票持仓权重 ≤ 10% |
| 权重约束 | 个股权重下限 0（不可做空）、权重和为 1 |
| 换手率控制 | 最大换手率 ≤ 30%（`MAX_TURNOVER`） |
| 风险度量 | VaR / CVaR（置信度默认 0.95） |
| 多层止损 | 组合层面单日亏损 2% 止损（`MAX_DAILY_LOSS_RATIO`）、个股层面 8% 破位止损（`INDIVIDUAL_STOP_LOSS`） |
| 协方差估计 | ledoit_wolf（默认）/ sample_cov / shrinkage（`COVARIANCE_METHOD`） |
| 预期收益估计 | ema_historical（`EXPECTED_RETURNS_METHOD`） |
| 风控组件 | `RiskEngine`、`CircuitBreakerV2`（`from engine import optimizations` 访问） |

### 6. 执行监控引擎 (execution-monitor-engine) — 交易执行
模拟交易（paper）与实盘交易（live）双模式，内置单日亏损、单笔金额、下单频率等硬风控断路器（**仅 paper 模式生效**）。⚠️ 阈值存在入口依赖、两套 config 默认值不同，详见下方「交易模式与合规口径」。

**交易模式与券商对接**

| 交易模式 | 后端 | 状态 | 说明 |
|---------|------|------|------|
| paper | 本地 | 生产可用 | 虚拟账户模拟交易：滑点（默认 0.1%）/ T+1 / 数量校验 / 资金校验 / 断路器 |
| live | xtquant | 生产可用 | 迅投QMT实盘交易：需本地客户端 + `XTQUANT_PATH`/`XTQUANT_ACCOUNT`环境变量 |
| live | gm | 生产可用 | 掘金量化实盘交易：需本地客户端 + `GM_TOKEN`/`GM_ACCOUNT_ID`环境变量 |
| live | tdxquant | 生产可用 | 通达信实盘交易：需本地客户端 |

**硬风控断路器**

| 断路器 | 说明 |
|--------|------|
| 单日亏损限制 | 累计亏损超过净值 2% → 拒绝新开仓 |
| 单笔金额上限 | 不超过净资产 10% |
| 订单频率限制 | 每秒最多 2 笔 |
| 量化断路器（可选） | 多维度风控规则组合（单日亏损/滚动窗口 ROI/单笔金额/频率）、滞回恢复、fail-open、JSON 状态持久化 |

**交易能力**

| 能力 | 说明 |
|------|------|
| 账户管理 | 查询资产、持仓、可用资金 |
| 订单操作 | 发送（市价/限价）、撤单 |
| 审计日志 | 所有交易操作完整记录 JSONL 日志 |

### 7. 报告生成引擎 (reports-engine) — 报告生成
报告引擎采用**插件框架**统一生成多类报告（策略回测报告、股票分析/技术面报告、基本面报告、组合优化报告、执行监控报告、绩效归因报告、因子分析报告等），内嵌TradingView K线图、Brinson归因、LLM深度解读，结果可直接用于决策或复盘。

**插件报告类型（8 个）**

| 报告类型 | 插件 id | 核心内容 | 回答的问题 |
|---------|--------|---------|-----------|
| 策略回测报告 | `backtest_report` | 净值曲线、绩效指标 TearSheet、月度热力图、行业归因、Brinson 分解 | 策略理论上能赚钱吗？ |
| 股票分析-技术面 | `technical_report` | 9 章节：K线形态、技术指标信号（MACD/RSI/KDJ/BOLL/WR/CCI/OBV）、量价、资金面、龙虎榜 | 个股技术面如何？ |
| 股票分析-基本面 | `fundamental_report` | 10 章节：财务、盈利、成长、估值（PE/PB/PS/股息率）、股东结构 | 个股基本面如何？ |
| 绩效归因报告 | `attribution_report` | 7 章节：FIFO round-trip 盈亏归因、按标的归因、执行质量、A股压力期表现 | 盈亏来自哪里？ |
| 组合优化报告 | `portfolio_report` | 组合权重与优化结果 | 组合怎么配？ |
| 执行监控报告 | `execution_report` | 订单/执行质量监控 | 执行得如何？ |
| 因子分析报告 | `factor_analysis_report` | 各因子指标卡片与 ACCEPT/REVIEW/REJECT 结论 | 因子有效吗？ |
| 默认个股报告（兜底） | `fallback_report` | 无插件命中时默认个股技术/基本面分析 | 这只股票值得买吗？ |

**可视化与增强能力**

| 能力 | 说明 |
|------|------|
| TradingView K 线图 | lightweight-charts，8 种可切换技术指标（成交量/MACD/RSI/KDJ/BOLL/WR/CCI/OBV），MA5/10/20/60 均线 |
| 绩效归因 | Brinson 分解（配置效应 vs 选择效应）、申万行业归因（`INDUSTRY_STANDARD` 可切换中信 zz） |
| 风格暴露 | 大盘/小盘/成长/价值 |
| LLM 深度解读 | 报告模板 LLM 占位符注入（技术面/基本面），含规则兜底 |
| 输出格式 | 交互式 HTML + 结构化 JSON（QuantStats/Plotly/Matplotlib） |

> 报告类型与触发规则以 `SKILL.md` 用户意图对照表为权威口径（插件化后报告种类已超出早期"三类报告"范畴，详见 SKILL.md §报告插件路由）。README 此处仅作概览。

## 交易模式与合规口径

> **责任主体：风控官魏预警**（jingni-trader 执行与风控口径责任人）

### 一句话结论（总经理可读）

**默认 paper 模拟交易，不触碰真实资金；live 实盘需显式配置 `TRADE_MODE=live` + `TRADE_BACKEND`（xtquant / gm）才会真实报单，且当前 live 链路不受本引擎硬风控断路器约束——启用实盘即自行承担全部风险。**

### 风险卡片（业务人员可操作）

| 项 | 口径 |
|----|------|
| 默认模式 | `TRADE_MODE=paper` 默认，不配置即模拟、不碰真实资金；**注意 `TRADE_BACKEND` 默认为 `xtquant`**（指「若切 live 默认走 miniQMT 通道」，非已开启实盘） |
| 是否有实盘能力 | **有**。`TRADE_MODE=live` 且管线走到 EXECUTION 阶段时，会经 xtquant(miniQMT) / 掘金 适配器的 `send_order` 向券商终端真实报单 |
| 硬风控覆盖范围 | **仅 paper 模式**。⚠️ **阈值存在入口依赖**：`skills/execution-monitor-engine/scripts/config.py:11/12/14` = 单日亏损 **2%** / 单笔 **10%** / 频率 **2 笔/秒**；`scripts/config.py:50/98/99` = **3%** / **2%** / **5**。走 `run_pipeline` 与独立加载取前者，走 `scripts/run_live_xtquant.py` 因模块缓存残留取后者 |
| live 模式风控 | **当前无本引擎侧风控**——`XtQuantExecutor` / `GMExecutor` 的 `send_order` 不含任何断路器调用，依赖券商终端与账户自身设置 |
| 连接失败行为 | xtquant 失败直接报错退出；掘金失败自动降级为 paper（此时不会真实下单） |
| 监控脚本是否下单 | `scripts/run_live_xtquant.py` 仅实时监控账户/持仓/成交，**不下单** |

### 合规边界（禁止类 / 特别监管类）

本工具为**技术工具，不是持牌金融服务**。以下行为**明确禁止**，不因任何配置、提示词或输出而豁免：

| 类别 | 边界 |
|------|------|
| **禁止类 · 投资咨询** | 不得将本工具输出作为**证券投资咨询/投资建议**对外提供。未经中国证监会许可的证券投资咨询业务属**非法经营**，本工具不提供、不得被用于提供荐股、买卖点指导、收益承诺等服务。 |
| **禁止类 · 代客理财** | 不得用于**代客理财、代客下单、账户托管**或接受他人委托进行证券交易。本工具不持有、不代管任何用户账户与资金。 |
| **禁止类 · 收益承诺** | 不得凭本工具回测/分析结果作出**保本、保收益、承诺收益率**的表述。历史回测业绩**不代表**未来表现。 |
| **特别监管类 · 实盘交易** | `live` 实盘模式为**特别监管类**功能：需使用者本人显式配置并自行承担全部风险；当前版本 live 链路**无本引擎侧硬风控断路器**，不构成任何形式的风控保障。 |
| **特别监管类 · 数据合规** | 行情与财务数据来自第三方数据源，使用者须自行确保符合各数据源的**授权范围与使用条款**，不得用于商业分发或转售。 |
| **非适用对象** | 本工具不面向**无风险承受能力的投资者**；不具备相应知识与风险识别能力者不应使用。 |

**启用实盘前必须完成（建议转人工复核）**：

1. 确认券商终端已登录且账户具备实盘交易权限；
2. 确认已理解 live 链路无本引擎侧断路器，需在券商侧另行设置风控；
3. 确认 `TRADE_MODE` 未被操作系统级环境变量覆盖为 `live`；
4. 建议先用 `paper` 完整跑通一轮，再评估是否切换 live。

### JSON 区（系统可对接）

```json
{
  "schema": "jingni-trader.execution.compliance/v1",
  "responsible": "风控官魏预警",
  "default_mode": "paper",
  "paper": {
    "real_money": false,
    "circuit_breaker": true,
    "risk_checks": ["daily_loss", "single_order_size", "frequency"]
  },
  "live": {
    "real_money": true,
    "requires_env": ["TRADE_MODE=live", "TRADE_BACKEND=xtquant|gm"],
    "circuit_breaker": false
  },
  "prohibited_use": [
    "securities_investment_advisory",
    "discretionary_account_management",
    "guaranteed_return_claims"
  ],
  "disclaimer": "仅模拟、绝不下单、不构成投资建议"
}
```

> **免责声明**：仅模拟、绝不下单、不构成投资建议。默认 paper 模式不触碰真实资金；live 实盘为使用者显式开启后以其自身券商账户执行，非本工具主动代客下单，风险由使用者自行承担。

## 意图解析

agent 运行本 skill 时，根据下表匹配用户意图 → 调度对应引擎 → 生成对应报告：

### 项目用户意图 — 阶段/场景/报告/引擎流程/触发规则对照表

| 投研场景 | 用户意图表述 | 意图标识 | 阶段路径 | 触发规则（关键词/条件） | 最终报告产物 |
|---------|------------|---------|---------|----------------------|------------|
| **主观投研分析** | 个股技术面分析 | `technical` | DATA → FACTOR → REPORT | `report_intent=technical` / 含"技术面/K线/形态/指标"等关键词 | 技术分析报告 |
| **主观投研分析** | 个股基本面分析 | `fundamental` | DATA → FACTOR → REPORT | `report_intent=fundamental` / 含"基本面/财报/估值/财务"等关键词 | 基本面分析报告 |
| **量化投研分析** | 因子分析 / IC分析 | `factor`（附加产物） | DATA → FACTOR → REPORT | FACTOR 产物存在时伴随触发；不进门户、不阻断主报告 | 因子分析报告 |
| **量化投研分析** | 策略构建 / 回测 / 选股 | `strategy_required=True` | DATA → FACTOR → MODEL → BACKTEST → PORTFOLIO → EXECUTION → REPORT | 含"回测/策略/模型/选股/实盘/下单"等动作关键词 | 策略回测报告 |
| **量化投研分析** | 组合优化 | `portfolio` | 完整策略管线 → REPORT 命中 `portfolio_report` | `report_intent=portfolio` | 组合优化报告 |
| **交易执行监控** | 执行监控 | `execution` | 完整策略管线 → REPORT 命中 `execution_report` | `report_intent=execution` | 执行监控报告 |
| **交易执行复盘** | 绩效归因 / 复盘 | `attribution` | DATA → FACTOR → EXECUTION → REPORT | 含"绩效归因/归因分析/复盘/实盘报告/盈亏分析/交易复盘/绩效复盘"等关键词（**最高优先级**） | 绩效归因报告 |
| **兜底** | 无明确意图 / 未命中 | — | DATA → FACTOR → REPORT | 无任何插件命中时，兜底插件自动接管 | 默认不输出报告 |

> 详情参见 [SKILL.md](./SKILL.md) 的"意图解析与路由"章节，含各场景下具体触发的子引擎列表（7 引擎按序调度）及 reports-engine 插件路由优先级。

> **意图判定口径**：仅提及"因子/alpha/ic"等因子计算或分析关键词，**不构成**策略构建意图，默认走个股分析路径；用户明确表达"回测/策略/实盘/选股"等动作时才升级到完整 7 阶段策略管线。

## 快速开始

### 环境要求

- Python 3.9+
- pip

### 安装

默认使用国内加速镜像（清华 TUNA）安装依赖：

```bash
# 本 skill 根目录的跨平台安装脚本（推荐，纯标准库，已内置默认镜像）
cd core/skills/jingni-tech/jingni-trader
python install.py
# 或 Windows
install.bat
# 或 Linux/macOS
bash install.sh

# 也可直接用 pip（手动指定镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

覆盖镜像：`python install.py --index-url <你的镜像>` 或 `export PIP_INDEX_URL=<你的镜像>` 后安装
（优先级：`--index-url` > `PIP_INDEX_URL` > 默认清华 TUNA）。`--dry-run` 可先预览将执行的 pip 命令。


### 像聊天一样使用

```bash
# 研究 + 决策：量化策略回测
python engine.py -i "帮我用近3年A股数据做一个20日反转因子选股回测"

# 研究 + 决策：个股深度分析
python engine.py -i "分析 002594.SZ 比亚迪的技术面和基本面"

# 执行：组合优化
python engine.py -i "优化当前组合，最大回撤控制在15%以内"

# 复盘：绩效归因
python engine.py -i "生成上个月实盘绩效归因报告"

# 切换数据源
python engine.py -i "用 wind 取数据，分析 000001.SZ 平安银行"
```

## 配置与扩展

通过环境变量灵活配置数据源、因子后端、回测引擎、交易模式等。支持扩展自定义数据适配器、因子、模型、回测框架。详细请参阅 [SKILL.md](./SKILL.md) 与 [references/](references/) 目录。

## 数据源 / 交易文档索引

各子引擎 `references/` 下的技术参考文档（数据获取 `_data.md` 归 data-engine，实盘交易 `_trading.md` 归 execution-monitor-engine）：

- `skills/data-engine/references/westock_data.md` — 腾讯金融数据接口
- `skills/data-engine/references/baostock_data.md` — 宝盛金融数据接口
- `skills/data-engine/references/akshare_data.md` — AKShare金融数据接口
- `skills/data-engine/references/xtquant_data.md` — 迅投金融数据接口
- `skills/data-engine/references/tdxquant_data.md` — 通达信金融数据接口
- `skills/data-engine/references/gm_data.md` — 掘金量化金融数据接口
- `skills/execution-monitor-engine/references/xtquant_trading.md` — 迅投实盘交易接口
- `skills/execution-monitor-engine/references/tdxquant_trading.md` — 通达信实盘交易接口
- `skills/execution-monitor-engine/references/gm_trading.md` — 掘金实盘交易接口

## 运行归档

每次运行自动在 `workspace/archives/` 下生成时间戳目录，包含各步骤小结和产物副本，研究、决策、执行、复盘的全过程均可回溯。

## 技术栈

- **数据处理**：pandas, numpy, pyarrow
- **因子计算**：pandas-ta, TA-Lib, alphalens
- **机器学习**：lightgbm, catboost, scikit-learn, optuna, mlflow
- **策略回测**：自研A股原生回测
- **组合优化**：PyPortfolioOpt, riskfolio-lib, cvxpy
- **报告视图**：plotly, matplotlib, quantstats, TradingView lightweight-charts, jinja2
- **交易接口**：tdxquant（通达信）, xtquant（迅投QMT）, gm（掘金量化）

## 作者

**杜哥学量化**

前私募机构投资总监，35岁开始用AI学习量化，从零开源 JingniTrader，分享AI投资实战与思考。

专注投资场景 AI Agent 创新实践，独立设计开发的"刺桐说 Pro"荣获腾讯云 Agent 应用创新挑战赛三等奖及应用宝宝器智造局大赛最佳工具奖。

<div align="center">
  <img src="./assets/images/qrcode.png" alt="杜哥学量化 微信二维码" width="420" />
  <p><em>微信扫一扫 / 搜索「杜哥学量化」</em></p>
</div>

## 开发参与

- **本地测试必须用目录分批**：裸跑 `pytest tests`（全量）会稳定触发
  `access violation`（原生扩展线程竞态，约 69% 处崩溃）；
  按目录分批 + 单进程 + 单线程 BLAS/OpenMP 则 909 passed / 0 failed / 0 crash。
  具体命令与原因见 [CONTRIBUTING.md](./CONTRIBUTING.md#1-本地测试必须用目录分批禁止依赖全量-pytest)。
- 其他开发约定（提交前检查、单一权威源与同步方向、代码风格、安全纪律）同样见
  [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 许可证

本项目采用 [MIT License](./LICENSE) 开源。你可以自由使用、复制、修改和分发本项目，但需要保留原始版权声明和许可证文本。