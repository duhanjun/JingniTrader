# 配置指南

本文档说明 data-engine 的配置选项（REQ-2026-08-14 实施最终版，共 11 个 backend）。

## 环境变量

### 数据源配置

| 变量名 | 描述 | 必需 | 默认值 |
|--------|------|------|--------|
| DATA_BACKENDS | 数据源优先级链，逗号分隔 | 否 | "local,westock,baostock,akshare,websearch"（免费链 5 源） |
| DATA_BACKEND | 单源模式（不降级，与 DATA_BACKENDS 互斥） | 否 | 空 |
| ALLOW_SYNTHETIC_FALLBACK | 全部数据源失败时生成模拟数据兜底（默认关闭，需 true 才启用；关闭时显式报错不合成） | 否 | "false" |
| AUTO_INSTALL_BACKENDS | 数据源依赖缺失时自动 pip install（默认禁用） | 否 | "false" |

### westock 限频护栏（可选）

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| WESTOCK_MIN_INTERVAL | 单标的最小请求间隔（秒，旧名 TENCENT_MIN_INTERVAL 仍兼容） | "0.5" |
| WESTOCK_MAX_BATCH | 单次批量标的上限（旧名 TENCENT_MAX_BATCH 仍兼容） | "20" |
| WESTOCK_KLINE_LIMIT | 单次 K 线拉取最大条数（旧名 TENCENT_KLINE_LIMIT 仍兼容） | "320" |

### 按需调用源凭证（仅启用对应源时需要）

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| TUSHARE_TOKEN | Tushare Pro API token（**非免费源**） | 空 |
| GM_TOKEN | 掘金量化 token | 空 |
| IFIND_USERNAME / IFIND_PASSWORD | 同花顺 iFinD 账号密码 | 空 |

> ⚠️ 安全纪律：上述 token 仅从环境变量读取，任何位置不得 print / 落盘明文。

### 数据存储与清洗

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| QUANT_WORK_DIR | 工作目录根路径 | "./workspace" |
| DATA_FORMAT | 数据落盘格式（parquet/csv/sql） | "parquet" |
| ADJUST_MODE | 复权方式（前复权 qfq / 后复权 hfq） | "qfq" |
| DATA_MAX_WORKERS | 并行下载线程数 | "4" |

## 数据源优先级策略

**获取数据时按以下优先级选择数据源：**

1. **系统内置工具提供的外部数据（最高优先级）** — ctx.external_data
2. **用户对话指定的数据源** — ctx.data_sources（如"用腾讯行情取数据"或"用 wind 取数据"）
3. **环境变量配置的数据源** — DATA_BACKENDS（默认免费链 local,westock,baostock,akshare,websearch）
4. **模拟数据兜底** — synthetic（默认关闭，仅 `ALLOW_SYNTHETIC_FALLBACK=true` 时启用；默认失败即显式报错）

> 按需调用组（tushare/gm/xtquant/tdxquant/wind/ifind）不进默认链，需用户显式指定或经
> DATA_BACKENDS 启用；neodata 不接入（离开 workbuddy 无法使用）。

## 精准降级规则

| 数据源 | 降级条件 | 降级到 |
|--------|---------|--------|
| local | FileNotFoundError（缓存无对应标的） | westock |
| westock | NetworkError / DataNotFoundError / InvalidParameterError | baostock |
| baostock | BlacklistedError / DataNotFoundError / NetworkError | akshare |
| akshare | NetworkError / BlacklistedError / DataNotFoundError | websearch |
| websearch | DataNotFoundError | 显式失败（全部免费外部源不可用） |

## 数据源适配器

### 默认免费源（自动参与降级链）
| 适配器 | 数据源 | 接口 | 需要Token/配置 |
|--------|--------|------|---------------|
| LocalAdapter | 本地 Parquet 缓存（最高优先） | get_daily, get_financial | 需预置缓存目录 |
| WestockAdapter | 腾讯公网行情直连（免费零鉴权） | get_daily | 无（标准库 urllib 直连） |
| BaostockAdapter | 老虎量化开源项目 | get_daily, get_financial | 无需 Token |
| AkshareAdapter | 聚合库（爬虫） | get_daily, get_financial | 无需 Token |
| WebSearchAdapter | 经 WebSearch 工具查询终极回退 | get_daily | 需注入 web_search_fn |

### 按需调用源（注册可用，不进默认链）
| 适配器 | 数据源 | 需要Token/配置 |
|--------|--------|---------------|
| TushareAdapter | Tushare Pro（**非免费源**） | TUSHARE_TOKEN |
| XtQuantAdapter | 迅投 QMT/xtp | 本地券商客户端 |
| GmAdapter | 掘金量化 | GM_TOKEN + 付费 SDK |
| TdxQuantAdapter | 通达信量化 | 本地通达信终端 |
| WindAdapter | 万得 WindPy | Wind 金融终端 + WindPy |
| IfindAdapter | 同花顺 iFinD | iFinDPy + 账号密码 |

> ⚠️ neodata 不接入：离开 workbuddy 无法使用，取数路径中不得出现。

## 使用示例

```python
import os
os.environ['DATA_BACKENDS'] = 'local,westock,baostock,akshare,websearch'

from engine import run
from scripts.context import Context

ctx = Context(task_id="task_001", stock_pool=["000001.SZ", "600000.SH"])
ctx.start_date = "2021-01-01"
ctx.end_date = "2024-01-01"

result = run(ctx)
```
