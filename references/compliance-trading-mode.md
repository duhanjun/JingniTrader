# 交易模式与合规口径（jingni-trader）

> **责任主体：风控官魏预警**（jingni-trader 执行与风控口径责任人）
> 本文件的对外表述由风控官负责，任何改动须经风控官复核。

> 本文件由 SKILL.md「交易模式与合规口径」节拆分而来（FR-06 渐进披露：SKILL.md 正文 ≤500 行）。
> 权威结论与代码依据逐条列此，SKILL.md 保留一句话结论与链接。

## 一句话结论

**默认 paper 模拟交易，不触碰真实资金；live 实盘需显式配置 `TRADE_MODE=live` + `TRADE_BACKEND` 才会真实报单。live 链路自 2026-08-27 起已接入硬风控断路器（下单频率 + 单笔金额占比，账户看不清时拒单）；单日亏损检查于 2026-08-28 二期补齐，但受「日内冷启动漏损」限制且默认关闭——启用实盘仍等同自行承担剩余风险，仍不构成完整的风控保障。**

## 风险卡片（逐条对照代码）

| 项 | 口径 | 代码依据 |
|---|---|---|
| 默认模式 | `TRADE_MODE=paper`，不配置即模拟；`TRADE_BACKEND` 默认 `xtquant`（指「若切 live 默认走 miniQMT 通道」） | `scripts/config.py` TRADE_MODE / TRADE_BACKEND |
| 实盘触发条件 | `TRADE_MODE=live` 且管线走到 EXECUTION 阶段，经适配器 `send_order` 向券商终端真实报单 | `XtQuantExecutor.send_order` 调 `order_stock` |
| **是否有实盘能力** | **有**。本 Skill frontmatter 的 `when_not_to_use` 称「不用于真实下单交易」，与代码能力**不一致**——代码具备实盘下单能力，该字段仅表达使用建议 | adapters/xtquant_adapter.py、adapters/gm_adapter.py |
| 断路器覆盖范围 | **paper 与 live 均已覆盖**（2026-08-27 修复）。`CircuitBreaker` 下沉至 `scripts/base/circuit_breaker.py`，由 `BaseExecutor.__init__` 统一实例化，paper 与两个实盘适配器共用同一份实现 | `scripts/base/circuit_breaker.py`、`scripts/base/base_executor.py` |
| **live 模式风控** | **已接入（一期）**：xtquant / gm 两个实盘适配器的 `send_order` 在下单前调用 `BaseExecutor._live_risk_guard`，执行**下单频率 + 单笔金额占比**两项检查；超限拒单且不触达券商下单接口。 | `scripts/base/base_executor.py::_live_risk_guard`；adapters/{xtquant,gm}_adapter.py |
| **live 单日亏损（二期 2026-08-28）** | **已实现但默认关闭**。判定逻辑复用 `_check` 既有分支；`start_of_day_nav` 由**本地基线文件**按交易日持久化供数（broker 均不提供该字段：xtquant `XtAsset` 仅 6 字段、gm `Cashes.data` 无该字段）。开关 `LIVE_DAILY_LOSS_CHECK`，**默认 `off`**，置 `1/on/true/yes` 开启。 | `scripts/base/daily_baseline.py`、`scripts/base/base_executor.py::_resolve_live_sod`、`scripts/config.py::live_daily_loss_enabled` |
| **基线来源与冷启动漏损（残余缺陷）** | ⚠️ 基线 = 进程自交易日首次启动时刻的总资产快照。若引擎于**交易时段内冷启动**，基线取启动净值，**开盘至启动之间已实现亏损被"洗白"**，剩余阈值自启动净值起算。缓解：引擎日内常驻、冷启动只在开盘前完成（首选）；或由运维在开盘前将真值写入基线文件。 | `scripts/base/daily_baseline.py` 模块说明 |
| **基线不可用 → 拒单** | 开启状态下若拿不到基线（总资产非正 / 基线文件写入失败）→ **拒单**（fail-closed），不静默跳过检查。 | `scripts/base/circuit_breaker.py::UNAVAILABLE_SOD` |
| **日切口径** | 用**本地机器日期**判定交易日（`date.today()`），非交易所交易日历；跨零点夜盘等场景不精确。 | `scripts/base/daily_baseline.py::today_stamp` |
| **出入金污染（未处理）** | 日内出入金会改变总资产并被误算为盈亏。xtquant 侧无 `cum_inout` 字段，两 broker 无法一致处理，二期**明确不处理**。 | — |
| **live 风控失效模式** | **fail-closed**：`query_account()` 取不到总资产（空/0/抛异常）时一律拒单，绝不 fail-open。 | `scripts/base/base_executor.py::_live_risk_guard` |
| 风控阈值 | ⚠️ **存在入口依赖（2026-08-28 实测复核，卡片仍有效）**：`skills/execution-monitor-engine/scripts/config.py`（`MAX_SINGLE_ORDER_RATIO=0.10`、`MAX_DAILY_LOSS_RATIO=0.02`、`MAX_ORDER_FREQUENCY=2`）与根 `scripts/config.py`（`0.02` / `0.03` / `5`）两套默认值不同，**单笔比例实测相差 5.0 倍**（0.10 ÷ 0.02）。引用阈值时必须声明入口。<br>**但需明确**：根 `scripts/config.py` 的这三项 `MAX_*` 当前**无消费方**（全仓检索仅 `skills/execution-monitor-engine/scripts/base/circuit_breaker.py` 导入，而该 import 解析到 execution-monitor-engine 自己的 config），属**死配置**——读错不会改变实际风控行为，但会误导风险评估与对外口径。实际生效阈值为 execution-monitor-engine 那一套 | `skills/execution-monitor-engine/scripts/config.py`、`scripts/config.py` |
| 频率单位 | **笔/秒**（`_check_frequency` 窗口 `now - t < 1.0` 秒），非「笔/分钟」 | `skills/execution-monitor-engine/engine.py` |
| 连接失败行为 | xtquant 连接失败直接报错退出；**gm 连接失败自动降级为 paper**，此时不会真实下单 | `engine.py` `run()` / `run_live()` |
| 监控入口是否下单 | `scripts/run_live_xtquant.py` 只做账户/持仓/成交的实时监控与刷新，**不下单** | 该脚本调用 `run_live()` |

## 合规边界（禁止类 / 特别监管类）

本工具为**技术工具，不是持牌金融服务**。以下行为**明确禁止**，不因任何配置、提示词或输出而豁免：

| 类别 | 边界 |
|------|------|
| **禁止类 · 投资咨询** | 不得将本工具输出作为**证券投资咨询/投资建议**对外提供。未经中国证监会许可的证券投资咨询业务属**非法经营**，本工具不提供、不得被用于提供荐股、买卖点指导、收益承诺等服务。 |
| **禁止类 · 代客理财** | 不得用于**代客理财、代客下单、账户托管**或接受他人委托进行证券交易。本工具不持有、不代管任何用户账户与资金。 |
| **禁止类 · 收益承诺** | 不得凭本工具回测/分析结果作出**保本、保收益、承诺收益率**的表述。历史回测业绩**不代表**未来表现。 |
| **特别监管类 · 实盘交易** | `live` 实盘模式为**特别监管类**功能：需使用者本人显式配置并自行承担全部风险。当前版本 live 链路已覆盖「下单频率 + 单笔金额占比 + 单日亏损」三项硬风控（账户信息不可用时拒单）。**其中单日亏损检查默认关闭，且基于本地持久化的日初净值基线——若引擎进程于交易时段内冷启动，基线将取启动时刻净值，开盘至启动之间的已实现亏损不纳入计算。** 故**仍不构成完整的风控保障**，使用者须在券商侧或外部另行设置止损风控。 |
| **特别监管类 · 数据合规** | 行情与财务数据来自第三方数据源，使用者须自行确保符合各数据源的**授权范围与使用条款**，不得用于商业分发或转售。 |
| **非适用对象** | 本工具不面向**无风险承受能力的投资者**；不具备相应知识与风险识别能力者不应使用。 |

## 启用实盘前的强制确认（建议转人工复核）

1. 确认券商终端（miniQMT / 掘金）已登录，且账户具备实盘交易权限；
2. 确认已理解 live 链路覆盖「下单频率 + 单笔金额占比 + 单日亏损」三项硬风控，其中**单日亏损默认关闭**（`LIVE_DAILY_LOSS_CHECK`，默认 `off`），且即便开启也受**日内冷启动漏损**限制，需在券商侧或外部另行设置止损风控；
3. **确认 `TRADE_MODE` 未被操作系统级环境变量覆盖为 `live`**（Windows 查 `HKCU\Environment`，Linux/macOS 查 shell 配置文件）——这是最易被忽视的真实风险；
4. 建议先用 `paper` 完整跑通一轮，再评估是否切换 live。

## 免责声明

**仅模拟、绝不下单、不构成投资建议。** 默认 paper 模式不触碰真实资金；live 实盘为使用者显式开启后以其自身券商账户执行，非本工具主动代客下单，风险由使用者自行承担。
