# 交易模式与合规口径（jingni-trader）

> **责任主体：风控官魏预警**（jingni-trader 执行与风控口径责任人）
> 本文件的对外表述由风控官负责，任何改动须经风控官复核。

> 本文件由 SKILL.md「交易模式与合规口径」节拆分而来（FR-06 渐进披露：SKILL.md 正文 ≤500 行）。
> 权威结论与代码依据逐条列此，SKILL.md 保留一句话结论与链接。

## 一句话结论

**默认 paper 模拟交易，不触碰真实资金；live 实盘需显式配置 `TRADE_MODE=live` + `TRADE_BACKEND` 才会真实报单，且当前 live 链路不受本引擎硬风控断路器约束——启用实盘等同自行承担全部风险。**

## 风险卡片（逐条对照代码）

| 项 | 口径 | 代码依据 |
|---|---|---|
| 默认模式 | `TRADE_MODE=paper`，不配置即模拟；`TRADE_BACKEND` 默认 `xtquant`（指「若切 live 默认走 miniQMT 通道」） | `scripts/config.py` TRADE_MODE / TRADE_BACKEND |
| 实盘触发条件 | `TRADE_MODE=live` 且管线走到 EXECUTION 阶段，经适配器 `send_order` 向券商终端真实报单 | `XtQuantExecutor.send_order` 调 `order_stock` |
| **是否有实盘能力** | **有**。本 Skill frontmatter 的 `when_not_to_use` 称「不用于真实下单交易」，与代码能力**不一致**——代码具备实盘下单能力，该字段仅表达使用建议 | adapters/xtquant_adapter.py、adapters/gm_adapter.py |
| 断路器覆盖范围 | **仅 paper**。`CircuitBreaker` 仅在 `PaperExecutor.__init__` 实例化、仅被 `PaperExecutor.send_order` 调用 | `skills/execution-monitor-engine/engine.py` |
| **live 模式风控** | **当前无本引擎侧风控**。两个实盘适配器的 `send_order` 中 `circuit\|breaker\|check_send_order` 命中数为 **0**，风控依赖券商终端与账户自身设置 | adapters/xtquant_adapter.py、adapters/gm_adapter.py |
| 风控阈值 | ⚠️ **存在入口依赖**：`skills/execution-monitor-engine/scripts/config.py` 与根 `scripts/config.py` 两套默认值不同（单笔比例相差 5 倍）。引用阈值时必须声明入口 | 两份 config.py |
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
| **特别监管类 · 实盘交易** | `live` 实盘模式为**特别监管类**功能：需使用者本人显式配置并自行承担全部风险；当前版本 live 链路**无本引擎侧硬风控断路器**，不构成任何形式的风控保障。 |
| **特别监管类 · 数据合规** | 行情与财务数据来自第三方数据源，使用者须自行确保符合各数据源的**授权范围与使用条款**，不得用于商业分发或转售。 |
| **非适用对象** | 本工具不面向**无风险承受能力的投资者**；不具备相应知识与风险识别能力者不应使用。 |

## 启用实盘前的强制确认（建议转人工复核）

1. 确认券商终端（miniQMT / 掘金）已登录，且账户具备实盘交易权限；
2. 确认已理解 live 链路无本引擎侧断路器，需在券商侧或外部另行设置风控；
3. **确认 `TRADE_MODE` 未被操作系统级环境变量覆盖为 `live`**（Windows 查 `HKCU\Environment`，Linux/macOS 查 shell 配置文件）——这是最易被忽视的真实风险；
4. 建议先用 `paper` 完整跑通一轮，再评估是否切换 live。

## 免责声明

**仅模拟、绝不下单、不构成投资建议。** 默认 paper 模式不触碰真实资金；live 实盘为使用者显式开启后以其自身券商账户执行，非本工具主动代客下单，风险由使用者自行承担。
