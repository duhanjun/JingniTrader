# akshare 数据源参考（akshare_data）

> **来源**：AKShare 官方 GitHub 仓库 `https://github.com/akfamily/akshare`（README、源码目录）与官方文档站点 `https://akshare.akfamily.xyz/`（文档版本 **1.18.91**）。
> **用途**：本文件归集 AKShare（开源财经数据接口库）的**数据获取类**能力，作为 `jingni-trader` 的 `data-engine` 子技能参考手册（references），与 `xtquant_data.md` / `tdxquant_data.md` / `gm_data.md` / `westock_data.md` / `baostock_data.md` 同属数据源文档。
> **数据获取 vs 实盘交易**：AKShare 仅提供**数据获取**（行情/财务/宏观等），不含实盘报单能力；实盘交易类内容归 `execution-monitor-engine`（本文件不收录交易章节）。
> **说明**：AKShare 接口数千个，本文档聚焦「模块总览 + 高频示例 + 使用注意」，不穷举全部接口；读者无需回看 GitHub 即可使用核心功能。接口名均以代码格式标注。

---

## 一、简介与特性

**AKShare** 是一个基于 Python 的开源财经数据接口库，目标为「一行代码获取金融数据」（Write less, get more!），底层数据源覆盖东方财富、新浪财经、金十数据、各交易所官网等。

**核心特性**：
- **易用性**：一行代码即可拉取数据，统一返回 `pandas.DataFrame`。
- **可扩展**：易于在其它应用中自定义与组合。
- **生态强**：无缝接入 Python 量化生态（pandas / numpy / 回测框架）。
- **离线接口检索**：内置离线接口注册表，可在不联网的情况下按关键词查找接口（`ak.search`）并查看接口元数据（`ak.interface_info`）。

**适用场景**：A股/美股/港股行情、债券、基金（公募/私募/ETF/LOF）、期货、期权、外汇、宏观、新闻、加密货币、能源、另类数据等的研究与策略数据准备。

**许可与声明**：
- 开源协议：MIT。
- 数据仅供学术研究参考，不构成投资建议；部分接口可能因上游不可用被移除，使用时需关注数据风险。

---

## 二、安装与版本

**环境要求**：Python（64 位）**3.11 及以上**。

**安装（通用）**：
```shell
pip install akshare --upgrade
```

**安装（国内镜像）**：
```shell
pip install akshare -i https://mirrors.aliyun.com/pypi/simple/ --upgrade
```

**从 GitHub 安装开发版**：
```shell
pip install git+https://github.com/akfamily/akshare.git
```

**Docker（可选）**：
```shell
docker pull registry.cn-shanghai.aliyuncs.com/akfamily/aktools:jupyter
docker run -it registry.cn-shanghai.aliyuncs.com/akfamily/aktools:jupyter python
```

**版本校验**：
```python
import akshare as ak
print(ak.__version__)   # 文档基准版本：1.18.91
```

**HTTP API（跨语言）**：AKShare 提供 `AKTools` 将接口以 HTTP 服务暴露，供非 Python 语言调用。

---

## 三、数据接口模块总览

AKShare 按资产/主题划分为以下大类（类目名与官方文档一致）。每个类目下包含数十至数百个具体接口，下表列出**各类目关键接口**及用途，标注来源版本 **1.18.91**。

### 3.1 股票（stock）
| 接口名 | 用途 |
|---|---|
| `stock_zh_a_hist` | A股个股历史行情（日/周/月线，支持复权） |
| `stock_zh_a_spot_em` | 东方财富-A股实时行情（全市场快照） |
| `stock_zh_a_daily` | A股历史日线（新浪/腾讯源） |
| `stock_intraday_em` | A股分时行情 |
| `stock_zh_a_minute` | A股分钟级行情 |
| `stock_bj_a_spot_em` | 北交所实时行情 |
| `stock_hk_hist` | 港股历史行情 |
| `stock_us_hist` / `stock_us_daily` | 美股历史/日线行情 |

### 3.2 债券（bond）
| 接口名 | 用途 |
|---|---|
| `bond_zh_hs_cov_daily` | 沪深可转债日线行情 |
| `bond_cb_jsl` | 集思录可转债实时数据（行情+溢价率+到期收益率） |
| `bond_zh_hs_cov_min` | 可转债分时行情 |
| `bond_repay_sina` | 债券回购等利率数据 |

### 3.3 基金（fund）
| 接口名 | 用途 |
|---|---|
| `fund_etf_spot_em` | 东方财富-ETF 实时行情（含 IOPV 实时估值） |
| `fund_lof_spot_em` | 东方财富-LOF 实时行情 |
| `fund_etf_category_sina` | 新浪-基金列表及行情（封闭/ETF/LOF） |
| `fund_etf_category_ths` | 同花顺-基金每日净值-实时行情（按类别） |
| `fund_etf_hist_em` / `fund_lof_hist_em` | ETF/LOF 历史净值（symbol/period/start_date/end_date/adjust） |
| `fund_open_fund_info_em` | 开放式基金单位净值与累计净值历史 |
| `fund_name_em` | 基金代码-名称映射 |

### 3.4 期货（futures）
| 接口名 | 用途 |
|---|---|
| `futures_zh_daily_sina` | 新浪-期货日线行情 |
| `futures_main_sina` | 期货主连合约行情 |
| `futures_inventory_em` | 期货仓单库存数据 |
| `futures_rule_em` | 期货合约规则 |

### 3.5 期权（option）
| 接口名 | 用途 |
|---|---|
| `option_sse_50etf_daily` | 上交所-50ETF 期权日线 |
| `option_cffex_stock_daily` | 中金所-股指期权日线 |
| `option_sse_list` | 上交所期权合约列表 |

### 3.6 外汇（fx）与货币（currency）
| 接口名 | 用途 |
|---|---|
| `fx_spot_quote` | 实时外汇即期报价 |
| `currency_boc_safe` | 外汇及跨境人民币数据 |
| `currency_rate_boc` | 中国银行外汇牌价 |

### 3.7 指数（index）
| 接口名 | 用途 |
|---|---|
| `index_zh_a_hist` | A股指数历史行情 |
| `index_value_hist_funddb` | 指数估值历史 |
| `index_stock_cons` | 指数成分股 |

### 3.8 宏观（macro）
| 接口名 | 用途 |
|---|---|
| `macro_china_gdp_yearly` | 中国 GDP 年率（今值/预测/前值） |
| `macro_china_cpi_yearly` | 中国 CPI 年率 |
| `macro_china_m2_yearly` | 中国 M2 货币供应年率 |
| `macro_usa_gdp` | 美国 GDP 数据 |
| `macro_china_money_supply` | 中国货币供应量 |

### 3.9 新闻（news）与工具（tool）
| 接口名 | 用途 |
|---|---|
| `news_economic_baidu` | 宏观经济新闻 |
| `tool_trade_date_hist_sina` | 新浪-股票交易日历 |
| `tool_Chineseholiday` | 中国法定节假日 |

### 3.10 其它大类（不穷举）
加密货币（dc，`crypto_hist`）、现货（spot）、利率（interest_rate）、私募基金（fund_private）、银行（bank）、论文（article）、能源（energy）、迁徙（event）、高频（hf）、自然语言处理（nlp）、QDII（qdii）、另类（others）、奇货可查（qhkc）。

> 完整接口清单与参数请以 `ak.search("<关键词>")` 离线检索或官网数据字典 `https://akshare.akfamily.xyz/data/index.html` 为准。

---

## 四、常用接口示例（高频场景）

### 示例 1：A股个股日线行情
```python
import akshare as ak

# symbol: 6位代码（不带交易所后缀）；period: daily/weekly/monthly
# adjust: "" 不复权 / "qfq" 前复权 / "hfq" 后复权
stock_zh_a_hist_df = ak.stock_zh_a_hist(
    symbol="000001",
    period="daily",
    start_date="20170301",
    end_date="20231022",
    adjust="",
)
print(stock_zh_a_hist_df)
# 输出列：日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
```

### 示例 2：ETF 实时行情（含 IOPV）
```python
import akshare as ak

fund_etf_spot_em_df = ak.fund_etf_spot_em()
print(fund_etf_spot_em_df.head())
# 输出列含：代码/名称/最新价/IOPV实时估值/基金折价率/涨跌额/涨跌幅/成交量/成交额
```

### 示例 3：开放式基金历史净值
```python
import akshare as ak

# 以易方达蓝筹精选(005827)为例；单位净值+累计净值历史
fund_open_fund_info_em_df = ak.fund_open_fund_info_em(
    symbol="005827",
    indicator="单位净值走势",
)
print(fund_open_fund_info_em_df)
```

### 示例 4：中国宏观经济指标
```python
import akshare as ak

gdp_df = ak.macro_china_gdp_yearly()       # GDP 年率
cpi_df = ak.macro_china_cpi_yearly()       # CPI 年率
m2_df = ak.macro_china_m2_yearly()         # M2 货币供应年率
print(gdp_df.head(), cpi_df.head(), m2_df.head())
```

### 示例 5：股票交易日历
```python
import akshare as ak

# 返回 1990-12-19 起的股票交易日列表（单列 trade_date）
tool_trade_date_hist_sina_df = ak.tool_trade_date_hist_sina()
print(tool_trade_date_hist_sina_df.tail())
```

### 离线接口检索（辅助）
```python
import akshare as ak

# 关键词检索接口（无需联网）
search_df = ak.search("可转债 实时行情", limit=5)
print(search_df)
# 查看某接口完整元数据（参数/输出列/示例）
info = ak.interface_info("bond_cb_jsl")
print(info)
```
> 注意：`ak.search` 为关键词匹配（非语义搜索），传入完整接口名可稳定确认该接口。

---

## 五、使用注意事项

1. **频率限制与重试**：AKShare 接口调用上游第三方站点（东方财富/新浪等），高频或并发请求可能触发对方限流（返回空数据或异常）。建议：
   - 请求间加入适当延时（如 `time.sleep`），批量拉取时控制节奏；
   - 调用失败时做指数退避重试（retry with backoff）；
   - 服务端/客户端保持相同 AKShare 版本，必要时 `pip install akshare --upgrade` 并清理缓存（`pip cache purge`）。

2. **数据质量与一致性**：
   - 不同数据源（新浪/腾讯/东方财富）对同一标的的字段命名、复权口径可能不同，跨源比对需做字段归一化；
   - 复权参数（`adjust`）务必显式指定，避免前/后复权混用导致回测偏差；
   - 部分接口字段为字符串（如涨跌幅带 `%`），下游消费前需类型转换；
   - 部分接口可能因上游改版被移除或更名，生产环境应捕获异常并告警。

3. **版本与兼容**：
   - Python 需 ≥ 3.11（64 位）；
   - 接口名会随版本演进，升级后建议用 `ak.search` 复核关键接口名；
   - 接口更名一览见官方 `changelog` 文档。

4. **合规与声明**：
   - 数据仅供学术研究，不构成投资建议；
   - 遵守上游数据源与 AKShare 的开源协议（MIT）；
   - 实盘交易不在 AKShare 范畴，需对接券商/极速柜台（见 `execution-monitor-engine` 相关 `*_trading.md`）。

---

> 文档基准：AKShare **1.18.91**；接口签名与示例以官方文档/README 为准，升级后请以 `ak.interface_info` 核实。
