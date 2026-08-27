---
name: data-engine
description: A股数据采集与治理引擎。支持 11 个数据源（免费链 5 源 local/westock/baostock/akshare/websearch + 按需调用组 6 源 tushare/gm/xtquant/tdxquant/wind/ifind），完成复权、涨跌停标记、ST过滤、新股剔除、停牌处理等本土化清洗。优先使用 Agent 系统内置的金融数据获取工具（external_data）。支持精准降级；外部源全部失败时默认显式报错，仅 ALLOW_SYNTHETIC_FALLBACK=true 时模拟数据兜底。neodata 不接入。触发关键词：数据提取、行情计算、采集治理。
license: MIT
metadata:
  standard: anthropic-claude-skill
  jingni:
    version: "1.1.0"
    category: 技术
    author: quant-team
    created: "2026-08-17"
    tags:
      - quant-trading
      - A股
      - data-engine
      - westock
      - local
    maturity: stable
    pip_dependencies:
      - pandas>=2.0.0
      - numpy>=1.24.0
    runtime:
      language: python
      python_version: "3.9+"
      entry_point: engine.py
      environment_variables:
        - name: QUANT_WORK_DIR
          description: 工作目录根路径
          required: false
          default: "./workspace"
        - name: DATA_BACKENDS
          description: 数据源优先级链，逗号分隔（默认免费链 local,westock,baostock,akshare,websearch）
          required: false
          default: "local,westock,baostock,akshare,websearch"
        - name: DATA_BACKEND
          description: 单源模式（不降级），与 DATA_BACKENDS 互斥
          required: false
        - name: ALLOW_SYNTHETIC_FALLBACK
          description: 全部数据源失败时生成模拟数据兜底（默认关闭，需 true 才启用；关闭时显式报错不合成）
          required: false
          default: "false"
        - name: AUTO_INSTALL_BACKENDS
          description: 数据源依赖缺失时自动 pip install
          required: false
          default: "false"
        - name: DATA_FORMAT
          description: 数据落盘格式（parquet/csv/sql）
          required: false
          default: "parquet"
        - name: DATA_MAX_WORKERS
          description: 并行下载线程数
          required: false
          default: "4"
        - name: ADJUST_MODE
          description: 复权方式（前复权 qfq / 后复权 hfq）
          required: false
          default: "qfq"
        - name: WESTOCK_MIN_INTERVAL
          description: westock 单标的最小请求间隔（秒，轻限频护栏；腾讯行情与 westock npx 层共用同参，旧名 TENCENT_MIN_INTERVAL 仍兼容）
          required: false
          default: "0.5"
        - name: WESTOCK_MAX_BATCH
          description: westock 单次批量标的上限（旧名 TENCENT_MAX_BATCH 仍兼容；腾讯行情层默认 20，westock npx 层保守 ≤10 避免限频）
          required: false
          default: "20"
        - name: WESTOCK_KLINE_LIMIT
          description: westock 单次 K 线拉取最大条数（旧名 TENCENT_KLINE_LIMIT 仍兼容）
          required: false
          default: "320"
        - name: WESTOCK_TIMEOUT
          description: westock 子进程超时（秒，westock 后端 financial/capital_flow/dragon_tiger/shareholder 经 npx 调用）
          required: false
          default: "30"
        - name: AUTO_INSTALL_NODE
          description: westock 通道 node 前置缺失时自动安装（无人值守客户场景）；默认 off 守住私有化纪律
          required: false
          default: "false"
      trigger_keywords:
        - 数据获取
        - 行情
        - 日线
        - 分钟线
        - 财务
        - 数据清洗
        - 复权
        - 估值
        - 行业对比
---

# data-engine

## 概述

data-engine 是 A 股量化投研的**数据源统一引擎**，提供：

1. **11 个数据源**：免费链 5 源（local 本地缓存 / westock 腾讯公网直连 / baostock / akshare / websearch）+ 按需调用组 6 源（tushare / gm / xtquant / tdxquant / wind / ifind）
2. **统一接口**：BaseDataProvider 抽象基类，`get_daily()` 统一返回标准化 DataFrame
3. **数据清洗**：复权处理、停牌标记、涨跌停标记、ST/退市过滤、新股过滤
4. **数据存储**：支持 Parquet/CSV/SQL 多种格式
5. **Agent 系统工具优先**：自动检测并使用系统内置金融数据获取工具（external_data）
6. **精准降级**：免费链按 local→westock→baostock→akshare→websearch 顺序降级，仅在特定异常触发时切换
7. **失败显式报错（默认不合成）**：外部源全部不可用时按私有化纪律显式报错、不静默合成数据；仅当 `ALLOW_SYNTHETIC_FALLBACK=true` 时生成模拟数据兜底并告知用户

> ⚠️ REQ-2026-08-14 实施（Damon 最终拍板）：恢复 9 个旧 adapter + 接入 westock，共 11 个 backend。
> neodata 离开 workbuddy 无法使用 → **数据层不接入 neodata**（取数路径中不得出现）。
> 免费链 5 源：local 本地缓存（最高优先，Damon 确认 local 优先级高于 westock）→ westock → baostock → akshare → websearch。
> tushare 从免费链移除、归入按需调用组（需 TUSHARE_TOKEN，非免费源）；gm/xtquant/tdxquant/wind/ifind 亦属按需调用组，注册可用、不进默认链（避免默认链引发现金/终端依赖），由用户对话或 DATA_BACKENDS 显式启用。

## 数据源优先级策略

**获取数据时按以下优先级选择数据源：**

1. **系统内置工具提供的外部数据（最高优先级）**
   - 检查 Context 对象的 `external_data` 字段
   - 外部数据格式：`{"daily": DataFrame, "stock_list": DataFrame, "source": "..."}`

2. **用户对话指定的数据源（ctx.data_sources）**
   - 用户通过对话明确指定（如"用腾讯行情取数据"或"用 wind 取数据"），覆盖环境变量

3. **环境变量配置的数据源（DATA_BACKENDS）**
   - 默认免费链 `"local,westock,baostock,akshare,websearch"`

4. **模拟数据兜底（synthetic，默认关闭）**
   - 仅当 `ALLOW_SYNTHETIC_FALLBACK=true` 时，全部外部源都失败才生成几何布朗运动模拟数据跑通流程并告知用户；默认（false）下外部源全部失败即显式报错退出，绝不静默合成行情

**回退流程：**
```
external_data → ctx.data_sources → DATA_BACKENDS → local,westock,baostock,akshare,websearch → (synthetic 仅 ALLOW_SYNTHETIC_FALLBACK=true)
    ↓ 失败          ↓ 失败            ↓ 失败                          ↓ 失败       ↓ 失败(默认)/模拟(开启)
  下一级         下一级           下一级                         下一级       报错退出/模拟兜底
```

## 精准降级规则

每个数据源有明确的降级条件（`DATA_FALLBACK_RULES`），只在特定异常类型触发时才切换：

| 数据源 | 降级条件 | 降级到 |
|--------|---------|--------|
| local | FileNotFoundError（缓存无对应标的） | westock |
| westock | NetworkError / DataNotFoundError / InvalidParameterError | baostock |
| baostock | BlacklistedError / DataNotFoundError / NetworkError | akshare |
| akshare | NetworkError / BlacklistedError / DataNotFoundError | websearch |
| websearch | DataNotFoundError | 显式失败（全部免费外部源不可用） |

## node 前置处理（westock 通道，REQ-2026-08-15 方案 2）

> westock 后端自 REQ-2026-08-15 起，financial / capital_flow / dragon_tiger / shareholder 四类
> 经 **westockdata**（`npx -y westock-data-skillhub@1.0.5 <command>`）子进程取数，硬前置为 **Node.js ≥18**。
> 无 node 环境时通道不可用，须按下述行为层指令处理，**不得静默崩溃或误降级**。

**检测信号**：westock_adapter 在 npx 不可用时抛出 `NodeMissingError`（非 NetworkError，不触发数据源降级）。

**agent 行为流程（默认，AUTO_INSTALL_NODE=off）**：

1. **检测前置**：调用 westock 四类数据前，若捕获 `NodeMissingError`，识别为「运行前置缺失」而非「数据源不可用」。
2. **告知 + 征得同意**：向用户说明「westock 通道需要 Node.js ≥18，当前环境未安装」，并请求安装授权（**不得静默自动安装**，守住私有化纪律）。
3. **按系统分支安装**（仅用户同意后执行）：
   - Windows：`winget install OpenJS.NodeJS --accept-package-agreements --accept-source-agreements`（或引导官方安装器 https://nodejs.org）
   - macOS：`brew install node`
   - Linux：`sudo apt install -y nodejs`（Debian/Ubuntu）或 `sudo dnf install -y nodejs`（Fedora/RHEL）
4. **刷新 PATH + 重试**：安装完成后刷新进程 PATH（重读 `os.environ["PATH"]` 或重开子进程），重试 westock 通道。
5. **失败 / 拒绝回退**：安装失败或用户拒绝 → 如实告知用户，回退降级链（baostock / akshare / local）取数，**不阻塞主链**；westock 四类退化为既有免费链。

**代码层自动安装（可选，无人值守场景）**：部署方显式设 `AUTO_INSTALL_NODE=true` 时，引擎按平台分支直接执行安装命令，无需用户交互。默认 off，私有化纪律不变。

**禁忌**：`NodeMissingError` 绝不进入 `FALLBACK_TRIGGERING_ERRORS`，引擎不因 node 缺失静默切换到 baostock（避免「前置问题」被误判为「数据源不可用」）。

## 系统支持的全部数据源

> 当前共 11 个 backend。免费链 5 源按默认值自动参与降级；按需调用组 6 源需用户显式指定或经 DATA_BACKENDS 启用。
> westock 经标准库 urllib 直连腾讯公网，零鉴权；local 仅读本地已落盘 Parquet；neodata 不接入。

### 默认免费源（自动参与降级链）
| 数据源 | 说明 | get_daily | get_financial | 需要配置 |
|--------|------|-----------|---------------|---------|
| local | 本地 Parquet 缓存（最高优先） | 支持 | 支持（若有预置） | 需预置缓存目录 |
| westock | 腾讯公网行情直连（daily，urllib）+ westockdata 全品类（financial/capital_flow/dragon_tiger/shareholder，npx 子进程） | 支持 | 支持（westock，需 Node.js≥18） | 无（免费零鉴权；westock 经 npx 子进程） |
| baostock | 老虎量化开源项目 | 支持 | 支持 | 无需 Token |
| akshare | 聚合库（爬虫） | 支持 | 支持 | 无需 Token |
| websearch | 经 WebSearch 工具查询终极回退 | 支持 | 不支持 | 需注入 web_search_fn |

### 按需调用源（注册可用，不进默认链，用户显式指定启用）
| 数据源 | 说明 | 需要配置 |
|--------|------|---------|
| tushare | Tushare Pro 商业 API（**非免费源**） | TUSHARE_TOKEN |
| xtquant | 迅投 QMT/xtp（需本地券商客户端） | 本地券商客户端 |
| gm | 掘金量化（需 GM_TOKEN + 付费 SDK） | GM_TOKEN |
| tdxquant | 通达信量化（需本地通达信金融终端） | 本地通达信终端 |
| wind | 万得 WindPy | Wind 金融终端 + WindPy |
| ifind | 同花顺 iFinD | iFinDPy + 账号密码 |

## 数据结构

### get_daily() 返回的 DataFrame 包含以下字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| code | str | 股票代码，格式如 000001.SZ 或 600000.SH |
| date | datetime | 交易日期 |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量（手） |
| amount | float | 成交额（元） |
| pre_close | float | 前收盘价 |
| change_pct | float | 涨跌幅（%） |
| turnover_rate | float | 换手率（%） |
| is_st | bool | 是否ST |
| is_limit_up | bool | 是否涨停 |
| is_limit_down | bool | 是否跌停 |

### get_financial() 返回的标准财务数据字段：

| 字段名 | 说明 |
|--------|------|
| code, report_date | 股票代码 + 报告期 |
| pe_ttm, pb, ps_ttm, dv_ratio | 估值指标 |
| roe, roa, gross_margin, net_margin | 盈利能力 |
| revenue_growth, profit_growth | 成长性 |
| debt_ratio, current_ratio, quick_ratio, ocf | 偿债能力与现金流 |
| industry, name | 行业与名称 |

## 使用示例

### Python API

```python
from engine import run
from scripts.context import Context

# 创建 Context
ctx = Context(
    task_id="task_001",
    user_intent="获取数据",
    current_stage="IDLE"
)
ctx.stock_pool = ["000001.SZ", "600000.SH"]
ctx.start_date = "2021-01-01"
ctx.end_date = "2024-01-01"

# 使用内置适配器
result = run(ctx)

# 通过对话指定数据源优先级
ctx.data_sources = ["wind", "tushare", "baostock", "akshare", "websearch"]
result = run(ctx)

# 使用系统内置工具提供的外部数据（优先）
import pandas as pd
ctx.external_data = {
    "daily": pd.DataFrame(...),  # 系统内置工具已获取的日线数据
    "stock_list": pd.DataFrame(...),
    "source": "mcp_tushare"
}
result = run(ctx)
```

### CLI 运行

```bash
python engine.py -i "获取2021年的000001.SZ行情数据"
```

## 数据源适配器

| 适配器 | 数据源 | 需要Token/配置 |
|--------|--------|---------------|
| TushareAdapter | Tushare Pro | TUSHARE_TOKEN |
| BaoStockAdapter | 宝盛 | 无 |
| AkShareAdapter | AkShare | 无 |
| WebSearchAdapter | WebSearch | web_search_fn 注入 |
| XtQuantAdapter | 迅投 QMT | 本地客户端 |
| GmAdapter | 掘金量化 | GM_TOKEN |
| TdxQuantAdapter | 通达信 | 本地终端 |
| WindAdapter | 万得 | Wind 终端 |
| IfindAdapter | 同花顺 iFinD | IFIND_USERNAME/IFIND_PASSWORD |

## 数据清洗规则

详见 [references/data_cleaning_rules.md](references/data_cleaning_rules.md)

## 配置说明

详见 [references/config_guide.md](references/config_guide.md)

## API 文档

详见 [references/api_reference.md](references/api_reference.md)

## 参考资料（references）

各数据源/交易软件技术文档（按 `data-engine` 数据获取职责归集，实盘交易类见 `execution-monitor-engine`）：

| 参考文档 | 对应数据源 | 说明 |
|----------|-----------|------|
| [references/westock_data.md](references/westock_data.md) | westock | weStock 数据命令参考（腾讯公网行情/财务/资金面/龙虎榜/股东等） |
| [references/baostock_data.md](references/baostock_data.md) | baostock | 宝盛开源行情数据接口参考 |
| [references/akshare_data.md](references/akshare_data.md) | akshare | AKShare 开源财经数据源参考（股票/债券/基金/期货/宏观等模块总览 + 高频示例） |
| [references/xtquant_data.md](references/xtquant_data.md) | xtquant | 迅投 XtQuant XtData 行情模块（行情/财务/基础行情） |
| [references/tdxquant_data.md](references/tdxquant_data.md) | tdxquant | 通达信 TdxQuant 量化数据接口（行情/专业数据/板块/订阅/日历/公式） |
| [references/gm_data.md](references/gm_data.md) | gm | 掘金量化（GM）数据接口参考（行情/财务/枚举/变量/动态参数/标的池/基本函数/其他事件/错误码） |