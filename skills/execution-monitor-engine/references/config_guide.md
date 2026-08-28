# 配置指南

本文档说明 execution-monitor-engine 的配置选项。

## 环境变量

### 交易模式

| 变量名 | 描述 | 必需 | 默认值 |
|--------|------|------|--------|
| TRADE_MODE | 交易模式（paper / live） | 否 | "paper" |
| TRADE_BACKEND | 交易接口后端（xtquant / gm） | 否 | "paper" |
| QUANT_WORK_DIR | 工作目录根路径 | 否 | "./workspace" |
| EXECUTION_DIR | 执行日志目录 | 否 | "{QUANT_WORK_DIR}/execution" |

### 实盘交易后端配置

#### xtquant (miniQMT)

| 变量名 | 描述 | 必需 | 默认值 |
|--------|------|------|--------|
| XTQUANT_PATH | miniQMT 安装目录下的 userdata_mini 路径 | live+xtquant 必需 | 空 |
| XTQUANT_ACCOUNT | miniQMT 资金账号 | live+xtquant 必需 | 空 |

#### gm (掘金量化)

| 变量名 | 描述 | 必需 | 默认值 |
|--------|------|------|--------|
| GM_TOKEN | 掘金量化 API Token | live+gm 必需 | 空 |
| GM_ACCOUNT_ID | 掘金账户 ID（终端获取） | live+gm 必需 | 空 |

### 风控配置

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| MAX_DAILY_LOSS_RATIO | 单日最大亏损比例 | 0.02 (2%) |
| MAX_SINGLE_ORDER_RATIO | 单笔订单最大金额比例 | 0.10 (10%) |
| MAX_SINGLE_STOCK_WEIGHT | 单票最大持仓比例 | 0.10 (10%) |
| MAX_ORDER_FREQUENCY | 每秒最大下单笔数 | 2 |

### live 单日亏损检查（二期，2026-08-28）

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| LIVE_DAILY_LOSS_CHECK | live 单日亏损检查开关（`1`/`on`/`true`/`yes` 开启） | off |
| LIVE_DAILY_BASELINE_PATH | 日初净值基线文件落盘路径 | `{EXECUTION_DIR}/live_daily_baseline.json` |

说明：broker 账户接口均不提供 `start_of_day_nav`，故 live 侧的日初净值由本地基线文件
按交易日持久化供数（同日复用、跨日重置、按 `account_id` 分键）。**默认关闭**——启用前
须确认基线来源可接受「日内冷启动漏损」（引擎于交易时段内冷启动时，基线取启动时刻净值，
开盘至启动之间已实现亏损不纳入计算）。开启后若基线不可用则拒单（fail-closed）。
详见 `references/compliance-trading-mode.md`。

### 费用配置（paper 模式）

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| INIT_CAPITAL | 初始资金 | 1000000 |
| COMMISSION_RATE | 佣金费率 | 0.00025 (万2.5) |
| MIN_COMMISSION | 最低佣金 | 5.0 元 |
| STAMP_TAX_RATE | 印花税率（卖出） | 0.001 (千1) |
| SLIPPAGE | 滑点模拟比例 | 0.001 (千1) |

### 路径配置

```python
EXECUTION_DIR = os.path.join(QUANT_WORK_DIR, "execution")
AUDIT_LOG_PATH = os.path.join(EXECUTION_DIR, "trade_log.jsonl")
ACCOUNT_STATE_PATH = os.path.join(EXECUTION_DIR, "account_state.json")
```

## 模式选择

### paper（模拟交易）

本地虚拟账户，支持滑点模拟、T+1约束、数量校验、资金校验、断路器风控、审计日志、状态持久化。

```python
import os
os.environ['TRADE_MODE'] = 'paper'
```

### live + xtquant（miniQMT 实盘）

需本地运行 miniQMT 客户端，连接 xtdata 数据服务和 XtQuantTrader 交易接口。

```bash
# 环境变量配置
TRADE_MODE=live
TRADE_BACKEND=xtquant
XTQUANT_PATH=D:\gszq\qmt\userdata_mini
XTQUANT_ACCOUNT=你的资金账号
```

### live + gm（掘金量化实盘）

需配置掘金 Token 和账户 ID，通过 set_token + set_account_id 连接。

```bash
# 环境变量配置
TRADE_MODE=live
TRADE_BACKEND=gm
GM_TOKEN=你的掘金Token
GM_ACCOUNT_ID=你的掘金账户ID
```

## 使用示例

```python
import os
os.environ['TRADE_MODE'] = 'paper'

from engine import run, PaperExecutor, CircuitBreaker

result = run(ctx)
```
