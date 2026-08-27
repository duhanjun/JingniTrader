# gm_api_reference - 掘金量化（GM）Python SDK API 参考合集

> **说明（来源与用途）**
> 本文档由 `C:/Users/Lenovo/Desktop/gm技术文档/` 下的 12 份掘金量化（GM）技术文档合并而成，作为 `jingni-trader` 的 `data-engine` 子 skill 的参考手册（references）。
> 合并仅做结构组织，**技术细节完整保留、未删减**。
> 各章节保留原始官方文档锚点链接，便于溯源。原始文档来源：掘金量化支持中心 `https://www.myquant.cn/docs2/sdk/python/` 系列页面。
>
> **职责拆分**：本文档仅保留**数据获取类**内容（数据结构-数据类 / 枚举常量 / 变量 / 动态参数 / 标的池 / 基本函数 / 其他函数 / 其他事件 / 错误码）。
> **实盘交易类**内容（交易类数据结构 Account/Order/ExecRpt/Cash/Position/Indicator、债券交易函数、基金交易函数、交易事件）已拆分至 `execution-monitor-engine/references/gm_trading.md`，由 `execution-monitor-engine`（实盘交易监控）技能维护。

**合并来源清单（本文件保留 9 份；交易类 3 份已拆分）：**

1. 数据结构-掘金量化.md（仅数据类，交易类已拆分）
2. 枚举常量-掘金量化.md
3. 变量约定-掘金量化.md
4. 动态参数-掘金量化.md
5. 标的池.md
6. 基本函数-掘金量化.md
7. ⮕ 债券交易函数-掘金量化.md → `execution-monitor-engine/references/gm_trading.md`
8. ⮕ 基金交易函数-掘金量化.md → `execution-monitor-engine/references/gm_trading.md`
9. 其他函数-掘金量化.md
10. ⮕ 交易事件-掘金量化.md → `execution-monitor-engine/references/gm_trading.md`
11. 其他事件-掘金量化.md
12. 错误码.md

**目录**

- [数据结构](#数据结构)
  - [数据类](#数据类)（Tick / Bar / L2Order / L2Transaction）
- [枚举常量](#枚举常量)
- [变量约定](#变量约定)
- [动态参数](#动态参数)
- [标的池](#标的池)
- [函数](#函数)
  - [基本函数](#基本函数)
  - [其他函数](#其他函数)
- [事件](#事件)
  - [其他事件](#其他事件)
- [错误码](#错误码)

---

## 数据结构

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html>

### 数据结构

### 数据类

#### Tick - Tick 对象

行情快照数据(包含盘口数据和当天动态日线数据)

|参数名|类型|说明|
|---|---|---|
|symbol|str|[标的代码](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#symbol-%E4%BB%A3%E7%A0%81%E6%A0%87%E8%AF%86)|
|open|float|日线开盘价|
|high|float|日线最高价|
|low|float|日线最低价|
|price|float|最新价(集合竞价成交前price为0)|
|cum\_volume|long|最新总成交量,累计值（日线成交量）|
|cum\_amount|float|最新总成交额,累计值 （日线成交金额）|
|cum\_position|int|合约持仓量(只适用于期货),累计值（股票此值为 0）|
|trade\_type|int|交易类型（只适用于期货） 1: '双开', 2: '双平', 3: '多开', 4: '空开', 5: '空平', 6: '多平', 7: '多换', 8: '空换'|
|last\_volume|int|最新瞬时成交量|
|last\_amount|float|最新瞬时成交额（郑商所不支持）|
|created\_at|datetime.datetime|创建时间|
|quotes|\[\] (list of dict)|股票提供买卖 5 档数据, list\[0\]~list\[4\]分别对应买卖一档到五档, 期货提供买卖 1 档数据, list\[0\]表示买卖一档；, level2 行情对应的是 list\[0\]~list\[9\]买卖一档到十档，**注意：**：可能会有买档或卖档报价缺失，比如跌停时无买档报价（bid\_p 和 bid\_v 为 0），涨停时无卖档报价(ask\_p 和 ask\_v 为 0); 其中每档报价`quote`结构如下：|
|iopv|float|基金份额参考净值，(只适用于基金)|

##### 报价`quote` - (dict 类型)

|参数名|类型|说明|
|---|---|---|
|bid\_p|float|买价|
|bid\_v|int|买量|
|ask\_p|float|卖价|
|ask\_v|int|卖量|
|bid\_q|dict|委买队列 包含（total\_orders （int）委托总个数, queue\_volumes (list) 委托量队列），仅 level2 行情支持|
|ask\_q|dict|委卖队列 包含（total\_orders （int）委托总个数, queue\_volumes (list) 委托量队列），仅 level2 行情支持|

**注意：**

1、tick 是分笔成交数据，股票频率为 3s, 期货为 0.5s, 指数 5s, 包含集合竞价数据，股票早盘集合竞价数为 09:15:00-09:25:00 的 tick 数据

2、涨停时， 没有卖价和卖量， ask\_p 和 ask\_v 用 0 填充，跌停时，没有买价和买量，bid\_p 和 bid\_v 用 0 填充

3、queue\_volumes 委托量队列，只能获取到最优第一档的前 50 个委托量（不活跃标的可能会不足 50 个）

#### Bar - Bar 对象

bar 数据是指各种频率的行情数据

|参数名|类型|说明|
|---|---|---|
|symbol|str|[标的代码](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#symbol-%E4%BB%A3%E7%A0%81%E6%A0%87%E8%AF%86)|
|frequency|str|频率, 支持 'tick', '60s', '300s', '900s' 等, 默认'1d', 详情见[股票行情数据](https://www.myquant.cn/docs2/docs/index.html#%E8%A1%8C%E6%83%85%E6%95%B0%E6%8D%AE)和[期货行情数据](https://www.myquant.cn/docs2/docs/%E6%9C%9F%E8%B4%A7.html#%E8%A1%8C%E6%83%85%E6%95%B0%E6%8D%AE), [实时行情支持的频率](https://www.myquant.cn/docs2/faq/%E6%95%B0%E6%8D%AE%E9%97%AE%E9%A2%98.html#%E8%AE%A2%E9%98%85%E5%AE%9E%E6%97%B6%E6%95%B0%E6%8D%AE%E6%94%AF%E6%8C%81%E5%93%AA%E4%BA%9B%E9%A2%91%E7%8E%87)|
|open|float|开盘价|
|close|float|收盘价|
|high|float|最高价|
|low|float|最低价|
|amount|float|成交额|
|volume|long|成交量|
|position|long|持仓量（仅期货）|
|bob|datetime.datetime|bar 开始时间|
|eob|datetime.datetime|bar 结束时间|

**注意：** 不活跃标的，没有成交量是不生成 bar

#### L2Order - Level2 逐笔委托

|参数名|类型|说明|
|---|---|---|
|symbol|str|[标的代码](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#symbol-%E4%BB%A3%E7%A0%81%E6%A0%87%E8%AF%86)|
|side|str|委托方向 深市：'1'买， '2'卖， 'F'借入， 'G'出借， 沪市：'B'买，'S'卖|
|price|float|委托价|
|volume|int|委托量|
|order\_type|str|委托类型 深市：'1'市价， '2'限价， 'U'本方最优，沪市：'A'新增委托订单，'D'删除委托订单|
|order\_index|int|委托编号|
|created\_at|datetime.datetime|创建时间|

#### L2Transaction - Level2 逐笔成交

|参数名|类型|说明|
|---|---|---|
|symbol|str|[标的代码](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#symbol-%E4%BB%A3%E7%A0%81%E6%A0%87%E8%AF%86)|
|side|str|委托方向 沪市：B – 外盘,主动买, S – 内盘,主动卖, N – 集合竞价，深市无此字段|
|price|float|成交价|
|volume|int|成交量|
|exec\_type|str|成交类型 深市：'4'撤单，'F'成交 ，沪市不支持|
|exec\_index|int|成交编号|
|ask\_order\_index|int|叫卖委托编号|
|bid\_order\_index|int|叫买委托编号|
|created\_at|datetime.datetime|创建时间|

## 枚举常量

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/%E6%9E%9A%E4%B8%BE%E5%B8%B8%E9%87%8F.html>

### 枚举常量

#### OrderStatus委托状态

```

OrderStatus_New = 1                   # 已报
OrderStatus_PartiallyFilled = 2       # 部成
OrderStatus_Filled = 3                # 已成
OrderStatus_Canceled = 5              # 已撤
OrderStatus_Rejected = 8              # 已拒绝
OrderStatus_PendingNew = 10           # 待报
OrderStatus_Expired = 12              # 已过期
OrderStatus_PendingTrigger = 15       # 待触发, CTP条件单
OrderStatus_Triggered = 16            # 已触发, CTP条件单

```

#### OrderSide委托方向

```

OrderSide_Buy = 1             # 买入
OrderSide_Sell = 2            # 卖出
```

#### OrderType委托类型

```
用于映射OrderDuration和OrderQualifier的参数组合，推荐下单时直接指定OrderType，可无需额外指定OrderDuration和OrderQualifier

OrderType_Limit = 1            # 限价委托 (全部交易所支持)
OrderType_Market = 2           # 市价委托 （上期所和上能所不支持，中金所远期合约不支持，可转债不支持，上交所需要填上price保护限价）

# 终端3.18.0.0以上新增
# 上交所
OrderType_Limit = 1	       # 限价
OrderType_Market = 2	   # 市价(默认五档即成转限)
OrderType_Market_BOC = 20  # 市价对方最优价格(best of counterparty)
OrderType_Market_BOP = 21  # 市价己方最优价格(best of party)
OrderType_Market_B5TC = 24 # 市价最优五档剩余撤销(best 5 then cancel)
OrderType_Market_B5TL = 25 # 市价最优五档剩余转限价(best 5 then limit)

# 深交所
OrderType_Limit = 1	       # 限价
OrderType_Market = 2	   # 市价（默认对方最优价）
OrderType_Market_BOC = 20  # 市价对方最优价格(best of counterparty)
OrderType_Market_BOP = 21  # 市价己方最优价格(best of party)
OrderType_Market_FAK = 22  # 市价即时成交剩余撤销(fill and kill)
OrderType_Market_FOK = 23  # 市价即时全额成交或撤销(fill or kill)
OrderType_Market_B5TC = 24 # 市价最优五档剩余撤销(best 5 then cancel)

# 大商所
OrderType_Limit = 1	        # 限价
OrderType_Limit_FAK = 10	# 限价即时成交剩余撤销 (fill and kill)
OrderType_Limit_FOK = 11	# 限价即时全额成交或撤销 (fill or kill)
OrderType_Market = 2	    # 市价
OrderType_Market_FAK = 22	# 市价即时成交剩余撤销(fill and kill)
OrderType_Market_FOK = 23	# 市价即时全额成交或撤销(fill or kill)

# 郑商所
OrderType_Limit = 1	        # 限价
OrderType_Market = 2	    # 市价
OrderType_Market_FOK = 23	# 市价即时全额成交或撤销(fill or kill)

# 上期所和上能所
OrderType_Limit = 1	        # 限价
OrderType_Limit_FAK = 10	# 限价即时成交剩余撤销 (fill and kill)
OrderType_Limit_FOK = 11	# 限价即时全额成交或撤销 (fill or kill)

# 中金所
OrderType_Limit = 1	        # 限价
OrderType_Limit_FAK = 10	# 限价即时成交剩余撤销 (fill and kill)
OrderType_Limit_FOK = 11	# 限价即时全额成交或撤销 (fill or kill)
OrderType_Market_B5TC = 24	# 市价最优五档剩余撤销(best 5 then cancel)
OrderType_Market_B5TL = 25	# 市价最优五档剩余转限价(best 5 then limit)
OrderType_Market_BOPC = 27	# 市价最优价即时成交剩余撤销(best of price then cancel)
OrderType_Market_BOPL = 28  # 市价最优价即时成交剩余转限价(best of price then limit)

# 广期所
OrderType_Limit = 1	        # 限价
OrderType_Limit_FAK = 10	# 限价即时成交剩余撤销 (fill and kill)
OrderType_Limit_FOK = 11	# 限价即时全额成交或撤销 (fill or kill)
OrderType_Market = 2	    # 市价
OrderType_Market_FAK = 22	# 市价即时成交剩余撤销(fill and kill)
OrderType_Market_FOK = 23	# 市价即时全额成交或撤销(fill or kill)
```

#### OrderBusiness委托业务类型

用于映射OrderSide和PositionEffect的参数组合，新增业务类型

```
OrderBusiness_NORMAL = 0                        # 普通交易。默认值为空，以保持向前兼容

OrderBusiness_STOCK_BUY = 1                     # 股票,基金,可转债买入（映射OrderSide_Buy和PositionEffect_Open）
OrderBusiness_STOCK_SELL = 2	                # 股票,基金,可转债卖出（映射OrderSide_Buy和PositionEffect_Close）

OrderBusiness_FUTURE_BUY_OPEN = 10              # 期货买入开仓（映射OrderSide_Buy和PositionEffect_Open）
OrderBusiness_FUTURE_SELL_CLOSE = 11	        # 期货卖出平仓（映射OrderSide_Sell和PositionEffect_Close）
OrderBusiness_FUTURE_SELL_CLOSE_TODAY = 12      # 期货卖出平仓，优先平今（映射OrderSide_Sell和PositionEffect_CloseToday）
OrderBusiness_FUTURE_SELL_CLOSE_YESTERDAY = 13	# 期货卖出平仓，优先平昨（映射OrderSide_Sell和PositionEffect_CloseYesterday）
OrderBusiness_FUTURE_SELL_OPEN = 14             # 期货卖出开仓（映射OrderSide_Sell和PositionEffect_Open）
OrderBusiness_FUTURE_BUY_CLOSE = 15 	        # 期货买入平仓（映射OrderSide_Buy和PositionEffect_Close）
OrderBusiness_FUTURE_BUY_CLOSE_TODAY = 16       # 期货买入平仓，优先平今（映射OrderSide_Buy和PositionEffect_CloseToday）
OrderBusiness_FUTURE_BUY_CLOSE_YESTERDAY = 17	# 期货买入平仓，优先平昨（映射OrderSide_Buy和PositionEffect_CloseYesterday）

OrderBusiness_FUTURE_CTP_CONDITIONAL_ORDER = 18                # CTP条件单

OrderBusiness_IPO_BUY = 100                     # 新股申购	100

OrderBusiness_CREDIT_BOM = 200                  # 融资买入(buying on margin)
OrderBusiness_CREDIT_SS = 201                   # 融券卖出(short selling)
OrderBusiness_CREDIT_RSBBS = 202                # 买券还券(repay share by buying share)
OrderBusiness_CREDIT_RCBSS = 203                # 卖券还款(repay cash by selling share)
OrderBusiness_CREDIT_DRS = 204                  # 直接还券(directly repay share)

OrderBusiness_CREDIT_BOC = 207                  # 担保品买入(buying on collateral)
OrderBusiness_CREDIT_SOC = 208                  # 担保品卖出(selling on collateral)
OrderBusiness_CREDIT_CI = 209                   # 担保品转入(collateral in)
OrderBusiness_CREDIT_CO = 210                   # 担保品转出(collateral out)
OrderBusiness_CREDIT_DRC = 211	                # 直接还款(directly repay cash)

OrderBusiness_ETF_BUY = 301                     # ETF申购(purchase)
OrderBusiness_ETF_RED = 302                     # ETF赎回(redemption)
OrderBusiness_FUND_SUB = 303	                # 基金认购(subscribing)
OrderBusiness_FUND_BUY = 304	                # 基金申购(purchase)
OrderBusiness_FUND_RED = 305	                # 基金赎回(redemption)
OrderBusiness_FUND_CONVERT = 306                # 基金转换(convert)
OrderBusiness_FUND_SPLIT = 307 	                # 基金分拆(split)
OrderBusiness_FUND_MERGE = 308	                # 基金合并(merge)

OrderBusiness_BOND_RRP = 400 	                # 债券逆回购
OrderBusiness_BOND_CONVERTIBLE_BUY = 401        # 可转债申购(purchase)
OrderBusiness_BOND_CONVERTIBLE_CALL = 402       # 可转债转股
OrderBusiness_BOND_CONVERTIBLE_PUT = 403        # 可转债回售
OrderBusiness_BOND_CONVERTIBLE_PUT_CANCEL = 404	# 可转债回售撤销

OrderBusiness_OPTION_BUY_OPEN = 500             # 期权买入开仓（映射OrderSide_Buy和PositionEffect_Open）
OrderBusiness_OPTION_SELL_CLOSE = 501	        # 期权卖出平仓（映射OrderSide_Sell和PositionEffect_Open）
OrderBusiness_OPTION_SELL_OPEN = 502	        # 期权卖出开仓（映射OrderSide_Sell和PositionEffect_Open）
OrderBusiness_OPTION_BUY_CLOSE = 503	        # 期权买入平仓（映射OrderSide_Buy和PositionEffect_Close）
OrderBusiness_OPTION_COVERED_SELL_OPEN = 504	# 期权备兑开仓(备兑卖出开仓，只适用认购合约)
OrderBusiness_OPTION_COVERED_BUY_CLOSE = 505	# 期权备兑平仓(备兑买入平仓，只适用认购合约)
OrderBusiness_OPTION_EXERCISE = 506             # 期权行权


```

#### OrderTriggerType期货CTP条件单触发方式

```
# 期货条件单触发方式
OrderTriggerType_Unknown = 0        # 普通单
OrderTriggerType_LastPriceGreaterThanStopPrice = 1        # 条件单，触发条件：最新价大于条件价
OrderTriggerType_LastPriceGreaterEqualStopPrice = 2        # 条件单，触发条件：最新价大于等于条件价
OrderTriggerType_LastPriceLessThanStopPrice = 3        # 条件单，触发条件：最新价小于条件价
OrderTriggerType_LastPriceLessEqualStopPrice = 4        # 条件单，触发条件：最新价小于等于条件价
OrderTriggerType_AskPriceGreaterThanStopPrice = 5        # 条件单，触发条件：卖一价大于条件价
OrderTriggerType_AskPriceGreaterEqualStopPrice = 6        # 条件单，触发条件：卖一价大于等于条件价
OrderTriggerType_AskPriceLessThanStopPrice = 7        # 条件单，触发条件：卖一价小于条件价
OrderTriggerType_AskPriceLessEqualStopPrice = 8        # 条件单，触发条件：卖一价小于等于条件价
OrderTriggerType_BidPriceGreaterThanStopPrice = 9        # 条件单，触发条件：买一价大于条件价
OrderTriggerType_BidPriceGreaterEqualStopPrice = 10        # 条件单，触发条件：买一价大于等于条件价
OrderTriggerType_BidPriceLessThanStopPrice = 11        # 条件单，触发条件：买一价小于条件价
OrderTriggerType_BidPriceLessEqualStopPrice = 12        # 条件单，触发条件：买一价小于等于条件价
```

#### ExecType执行回报类型

```

ExecType_Trade = 15                            # 成交
ExecType_CancelRejected = 19                   # 撤单被拒绝 
```

#### PositionEffect开平仓类型

```

PositionEffect_Open = 1                        # 开仓
PositionEffect_Close = 2                       # 平仓, 具体语义取决于对应的交易所（实盘上期所和上海能源所不适用，上期所和上海能源所严格区分平今平昨，需要用3和4）
PositionEffect_CloseToday = 3                  # 平今仓
PositionEffect_CloseYesterday = 4              # 平昨仓(只适用于期货，不适用股票，股票用2平仓)

```

#### PositionSide持仓方向

```

PositionSide_Long = 1                        # 多方向
PositionSide_Short = 2                       # 空方向
```

#### OrderRejectReason订单拒绝原因

（仿真有效，实盘需要参考具体的拒绝原因）

```
OrderRejectReason_Unknown = 0                          # 未知原因
OrderRejectReason_RiskRuleCheckFailed = 1              # 不符合风控规则
OrderRejectReason_NoEnoughCash = 2                     # 资金不足
OrderRejectReason_NoEnoughPosition = 3                 # 仓位不足
OrderRejectReason_IllegalAccountId = 4                 # 非法账户ID
OrderRejectReason_IllegalStrategyId = 5                # 非法策略ID
OrderRejectReason_IllegalSymbol = 6                    # 非法交易标的
OrderRejectReason_IllegalVolume = 7                    # 非法委托量
OrderRejectReason_IllegalPrice = 8                     # 非法委托价
OrderRejectReason_AccountDisabled = 10                 # 交易账号被禁止交易
OrderRejectReason_AccountDisconnected = 11             # 交易账号未连接
OrderRejectReason_AccountLoggedout = 12                # 交易账号未登录
OrderRejectReason_NotInTradingSession = 13             # 非交易时段
OrderRejectReason_OrderTypeNotSupported = 14           # 委托类型不支持
OrderRejectReason_Throttle = 15                        # 流控限制
```

#### CancelOrderRejectReason取消订单拒绝原因

```
CancelOrderRejectReason_OrderFinalized = 101           # 委托已完成
CancelOrderRejectReason_UnknownOrder = 102             # 未知委托
CancelOrderRejectReason_BrokerOption = 103             # 柜台设置
CancelOrderRejectReason_AlreadyInPendingCancel = 104   # 委托撤销中
```

#### OrderStyle委托风格

```
OrderStyle_Unknown = 0
OrderStyle_Volume = 1                                  # 按指定量委托
OrderStyle_Value = 2                                   # 按指定价值委托
OrderStyle_Percent = 3                                 # 按指定比例委托
OrderStyle_TargetVolume = 4                            # 调仓到目标持仓量
OrderStyle_TargetValue = 5                             # 调仓到目标持仓额
OrderStyle_TargetPercent = 6                           # 调仓到目标持仓比例
```

#### CashPositionChangeReason仓位变更原因

```

CashPositionChangeReason_Trade = 1            # 交易
CashPositionChangeReason_Inout = 2            # 出入金 / 出入持仓
```

#### SecType标的类别

```
SEC_TYPE_STOCK = 1                          # 股票
SEC_TYPE_FUND = 2                           # 基金
SEC_TYPE_INDEX = 3                          # 指数
SEC_TYPE_FUTURE = 4                         # 期货
SEC_TYPE_OPTION = 5                         # 期权
SEC_TYPE_CREDIT = 6                         # 信用交易
SEC_TYPE_BOND = 7                           # 债券
SEC_TYPE_BOND_CONVERTIBLE = 8               # 可转债
SEC_TYPE_CONFUTURE = 10                     # 期货连续合约
```

#### AccountStatus交易账户状态

```

State_CONNECTING = 1                     # 连接中
State_CONNECTED = 2                      # 已连接
State_LOGGEDIN = 3                       # 已登录
State_DISCONNECTING = 4                  # 断开中
State_DISCONNECTED = 5                   # 已断开
State_ERROR = 6                          # 错误
```

#### PositionSrc头寸来源(仅适用融券)

```

PositionSrc_L1 = 1                      # 普通池
PositionSrc_L2 = 2                      # 专项池

```

#### AlgoOrderStatus算法单状态,暂停/恢复算法单时有效

```

AlgoOrderStatus_Resume = 1                   # 恢复母单
AlgoOrderStatus_Pause = 2                    # 暂停母单
AlgoOrderStatus_PauseAndCancelSubOrders = 3  # 暂停母单并撤子单
```

> 上次更新: 11/29/2024, 3:28:57 PM

---

## 变量约定

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#symbol-%E4%BB%A3%E7%A0%81%E6%A0%87%E8%AF%86>

### 变量约定

#### symbol - 代码标识

掘金代码(**symbol**)是掘金平台用于唯一标识交易标的代码,

格式为：**交易所代码.交易标代码**, 比如深圳平安的**symbol**，示例：`SZSE.000001`（注意区分大小写）。 板块为：**BK.板块代码**，比如鸿蒙概念的**symbol**，示例：`BK.007347`，板块symbol可通过get\_symbols(sec\_type1=1070)获取。 代码标识表示可以在掘金终端的仿真交易或交易工具中进行查询。

![](%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A/attach_163e07077adc2677.png)

##### 交易所代码

目前掘金支持国内的 8 个交易所, 各交易所的代码缩写如下：

|市场中文名|市场代码|
|---|---|
|上交所|SHSE|
|深交所|SZSE|
|中金所|CFFEX|
|上期所|SHFE|
|大商所|DCE|
|郑商所|CZCE|
|上海国际能源交易中心|INE|
|广期所|GFEX|

##### 交易标的代码

交易表代码是指交易所给出的交易标的代码, 包括股票（如 600000）, 期货（如 rb2011）, 期权(如 10002498）, 指数（如 000001）, 基金（如 510300）等代码。

具体的代码请参考交易所的给出的证券代码定义。

##### symbol 示例

|市场中文名|市场代码|示例代码|证券简称|
|---|---|---|---|
|上交所|SHSE|SHSE.600000|浦发银行|
|深交所|SZSE|SZSE.000001|平安银行|
|中金所|CFFEX|CFFEX.IC2011|中证 500 指数 2020 年 11 月期货合约|
|上期所|SHFE|SHFE.rb2011|螺纹钢 2020 年 11 月期货合约|
|大商所|DCE|DCE.m2011|豆粕 2020 年 11 月期货合约|
|郑商所|CZCE|CZCE.FG101|玻璃 2021 年 1 月期货合约|
|上海国际能源交易中心|INE|INE.sc2011|原油 2020 年 11 月期货合约|
|广期所|GFEX|GFEX.lc2405|碳酸锂 2024 年 05 月期货合约|

##### 虚拟合约

|市场中文名|市场代码|示例代码|证券简称|
|---|---|---|---|
|上期所|SHFE|SHFE.RB|螺纹钢主力连续合约|
|上期所|SHFE|SHFE.RB22|螺纹钢次主力连续合约|
|上期所|SHFE|SHFE.RB99|螺纹钢加权指数合约|
|上期所|SHFE|SHFE.RB00|螺纹钢当月连续合约|
|上期所|SHFE|SHFE.RB01|螺纹钢下月连续合约|
|上期所|SHFE|SHFE.RB02|螺纹钢下季连续合约|
|上期所|SHFE|SHFE.RB03|螺纹钢隔季连续合约|

##### 期货主力连续合约

仅回测模式下使用，`期货主力连续合约`为量价数据的简单拼接，未做平滑处理， 如 SHFE.RB 螺纹钢主力连续合约，其他主力合约请查看[期货主力连续合约](https://www.myquant.cn/docs2/docs/%E6%9C%9F%E8%B4%A7.html#%E8%BF%9E%E7%BB%AD%E5%90%88%E7%BA%A6%E6%95%B0%E6%8D%AE)

#### mode - 模式选择

策略支持两种运行模式,需要在`run()`里面指定，分别为实时模式和回测模式。

##### 实时模式

实时模式需指定 **mode = MODE\_LIVE**

订阅行情服务器推送的实时行情，也就是交易所的实时行情，只在交易时段提供，常用于仿真和实盘。

##### 回测模式

回测模式需指定 **mode = MODE\_BACKTEST**

订阅指定时段、指定交易代码、指定数据类型的历史行情，行情服务器将按指定条件全速回放对应的行情数据。适用的场景是策略回测阶段，快速验证策略的绩效是否符合预期。

#### context - 上下文对象

context 是策略运行上下文环境对象，该对象将会在你的算法策略的任何方法之间做传递。用户可以通过 context 定义多种自己需要的属性，也可以查看 context 固有属性，context 结构如下图：

![](%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A/attach_163e0724ee0db372.png)

##### context.symbols - 订阅代码集合

通过 subscribe 行情订阅函数， 订阅代码会生成一个代码集合

**函数原型：**

**返回值：**

|类型|说明|
|---|---|
|set(str)|订阅代码集合|

**示例：**

**返回值：**

##### context.now - 当前时间

实时模式返回当前本地时间, 回测模式返回当前回测时间

**函数原型：**

**返回值：**

|类型|说明|
|---|---|
|datetime.datetime|当前时间(回测模式下是策略回测的当前历史时间， 实时模式下是用户的系统本地时间)|

**示例：**

**返回:**

##### context.mode - 运行模式

实时模式为1，回测模式为2

**函数原型：**

**返回值：**

|类型|说明|
|---|---|
|int|实时模式为1，回测模式为2|

**示例：**

**返回:**

##### context.data - 数据滑窗

获取订阅的[tick 对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#tick-tick-%E5%AF%B9%E8%B1%A1) 或者 [bar 对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#bar-bar-%E5%AF%B9%E8%B1%A1)滑窗，数据为包含当前时刻推送 tick 或 bar 的前 count 条`tick`或者`bar`数据

**原型:**

**参数：**

|参数名|类型|说明|
|---|---|---|
|symbol|str|标的代码(只允许单个标的的代码字符串)，使用时参考[symbol](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#symbol-%E4%BB%A3%E7%A0%81%E6%A0%87%E8%AF%86)|
|frequency|str|频率, 支持 'tick', '1d', '60s' 等, 默认 '1d', 详情见[股票行情数据](https://www.myquant.cn/docs2/docs/index.html#%E8%A1%8C%E6%83%85%E6%95%B0%E6%8D%AE)和[期货行情数据](https://www.myquant.cn/docs2/docs/%E6%9C%9F%E8%B4%A7.html#%E8%A1%8C%E6%83%85%E6%95%B0%E6%8D%AE), [实时行情支持的频率](https://www.myquant.cn/docs2/faq/%E6%95%B0%E6%8D%AE%E9%97%AE%E9%A2%98.html#%E8%AE%A2%E9%98%85%E5%AE%9E%E6%97%B6%E6%95%B0%E6%8D%AE%E6%94%AF%E6%8C%81%E5%93%AA%E4%BA%9B%E9%A2%91%E7%8E%87)|
|count|int|滑窗大小(正整数)，需小于等于 subscribe 函数中 count 值|
|fields|str|指定返回对象字段, 如有多个字段, 中间用, 隔开, 默认所有, 具体字段见:[tick 对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#tick-tick-%E5%AF%B9%E8%B1%A1) 和 [bar 对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#bar-bar-%E5%AF%B9%E8%B1%A1) ，需在 subscribe 函数中指定的fields范围内，指定字段越少，查询速度越快|

**返回值：**

**当subscribe的format="df"（默认）时，返回dataframe**

|类型|说明|
|---|---|
|dataframe|tick 的 dataframe 或者 bar 的 dataframe|

**示例：**

**输出：**

**subscribe的format ="row"时，返回list\[dict\]**

|类型|说明|
|---|---|
|list\[dict\]|当frequency='tick'时，返回tick列表：\[{tick\_1}, {tick\_2}, ..., {tick\_n}\]，列表长度等于滑窗大小，即n=count， 当frequency='60s', '300s', '900s', '1800s', '3600s'时，返回bar列表：\[{bar\_1}, {bar\_2}, {bar\_n}, ..., \] ，列表长度等于滑窗大小，即n=count|

**示例：**

**输出：**

**subscribe的format ="col"时，返回dict**

|类型|说明|
|---|---|
|dict|当frequency='tick'时，返回tick数据（symbol为str格式，其余字段为列表，列表长度等于滑窗大小count），当frequency='60s', '300s', '900s', '1800s', '3600s'时，返回bar数据（symbol和frequency为str格式，其余字段为列表，列表长度等于滑窗大小count）|

**示例：**

**输出：**

**注意：**

**1.** 只有在**订阅**后，此接口才能取到数据，如未订阅数据，则返回报错。

**2.** symbol 参数只支持输入**一个**标的。

**3.** count 参数必须**小于或等于**订阅函数里面的 count 值。

**4.** fields 参数必须在订阅函数subscribe里面指定的 fields 范围内。指定字段越少，查询速度越快，目前效率是row > col > df。

**5.** 当subscribe的format指定col时，tick的quotes字段会被拆分，只返回买卖一档的量和价，即只有bid\_p，bid\_v, ask\_p和ask\_v。

##### context.account - 账户信息

可通过此函数获取账户资金信息及持仓信息。

**原型:**

**参数：**

|参数名|类型|说明|
|---|---|---|
|account\_id|str|账户信息，默认返回默认账户, 如多个账户需指定 account\_id|

**返回值：**

返回类型为[account - 账户对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#account-%E8%B4%A6%E6%88%B7%E5%AF%B9%E8%B1%A1)。

_示例-获取当前持仓：_

**返回值：**

|类型|说明|
|---|---|
|list\[position\]|[持仓对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#position-%E6%8C%81%E4%BB%93%E5%AF%B9%E8%B1%A1)列表|

**注意：** 没有持仓的情况下， 用 context.account().positions()查总持仓， 返回空列表， 用 context.account().position()查单个持仓，返回 None

**输出：**

_示例-获取当前账户资金：_

**返回值：**

|类型|说明|
|---|---|
|dict\[cash\]|[资金对象](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#cash-%E8%B5%84%E9%87%91%E5%AF%B9%E8%B1%A1)字典|

**输出：**

_示例-获取账户连接状态：_

**输出：**

##### context.parameters - 动态参数

获取所有动态参数

**函数原型：**

**返回值：**

|类型|说明|
|---|---|
|dict|key 为动态参数的 key, 值为动态参数对象， 参见[动态参数](https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%8A%A8%E6%80%81%E5%8F%82%E6%95%B0.html#%E5%8A%A8%E6%80%81%E5%8F%82%E6%95%B0)|

_示例-添加动态参数和查询所有设置的动态参数_

**输出：**

##### context.xxxxx - 自定义属性

通过自定义属性设置参数， 随 context 全局变量传入策略各个事件里

**返回值：**

|类型|说明|
|---|---|
|any type|自定义属性|

_示例-输出自定义属性_

**输出：**

> 上次更新: 5/29/2024, 5:29:07 PM

---

## 动态参数

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%8A%A8%E6%80%81%E5%8F%82%E6%95%B0.html>

### 动态参数

动态参数仅在**仿真交易和实盘交易**下生效， 可在终端设置和修改。

动态参数通过策略调用接口实现策略和掘金界面参数交互， 在不停止策略运行的情况下，界面修改参数（移开光标，修改就会生效）会对策略里的指定变量做修改

![动态参数](%E5%8A%A8%E6%80%81%E5%8F%82%E6%95%B0/attach_16408fc9e3c9fed6.png)

#### `add_parameter` - 增加动态参数

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|key|str|参数的键|
|value|double|参数的值|
|min|double|最小值|
|max|double|最大值|
|name|str|参数名称|
|intro|str|参数说明|
|group|str|参数的组|
|readonly|bool|是否为只读参数|

**返回值：**

`None`

**示例：**

#### `set_parameter` - 修改已经添加过的动态参数

\*\*注意：\*\*需要保持 key 键名和添加过的动态参数的 key 一致，否则不生效，无报错

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|key|str|参数的键|
|value|double|参数的值|
|min|double|最小值|
|max|double|最大值|
|name|str|参数名称|
|intro|str|参数说明|
|group|str|参数的组|
|readonly|bool|是否为只读参数|

**返回值：**

`None`

**示例：**

#### `on_parameter` - 动态参数修改事件推送

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|
|parameter|dict|当前被推送的动态参数对象|

**示例：**

**输出：**

#### context.parameters - 获取所有动态参数

返回数据类型为字典, key 为动态参数的 key, 值为动态参数对象

**示例：**

**输出：**

> 上次更新: 1/8/2024, 4:08:51 PM

---

## 标的池

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E6%A0%87%E7%9A%84%E6%B1%A0.html>

### 标的池

标的池功能，通过调用标的池API接口，实现策略标的池和掘金终端界面【交易工具】-【标的池】联动。在不停止策略运行的情况下，在界面导入标的池，策略可调用标的池API获取标的池成分代码；或者策略通过API创建/修改标的池，在终端界面可查看标的池成分的可视化行情。

![](%E6%A0%87%E7%9A%84%E6%B1%A0/97066201-c4a8-4a80-9f40-51e0702f0cdb.png)

-   实时模式（仿真交易和实盘交易）标的池应用：在终端【交易工具】-【标的池】进行增删查改操作，策略实时查询指定标的池成分代码进行交易。
    -   例1：在多个策略之间传递选股标的池（策略A选股 -> 标的池 -> 策略B择时交易） 第一步（策略A）：使用概念板块数据/选股条件产生选股symbol，通过标的池API创建标的池 第二步（终端界面）：人工盯盘，实时手动调整标的池成分股（可选） 第三步（策略B）：通过标的池API获取标的池最新成分股，根据择时逻辑自动交易
    -   例2：手动选股+策略择时（手动选股 -> 标的池 -> 策略择时交易） 第一步（终端界面）：创建标的池（手动自选/文件导入/持仓导入/板块导入） 第二步（终端界面）：人工盯盘，实时手动调整标的池成分股（可选） 第三步（策略）：通过标的池API获取标的池最新成分股，根据择时逻辑自动交易
    -   例3：策略选股+手动交易（策略选股 -> 标的池 -> 手动交易） 第一步（策略）：使用概念板块数据/选股条件产生选股symbol，通过标的池API创建标的池、定时任务更新标的池成分股 第二步（终端界面）：人工盯盘，实时手动调整标的池成分股（可选） 第三步（终端界面）：人工盯盘，手动择时交易
-   回测模式标的池应用：先手动选股，通过终端界面【交易工具】-【标的池】导入标的池，策略调用标的池API获取成分代码进行回测。
    -   例4：手动选股+策略回测（手动选股 -> 标的池 -> 策略历史回测） 第一步（终端界面）：创建标的池（手动自选/文件导入/持仓导入/板块导入） 第二步（终端界面）：手动调整修改标的池成分股（可选） 第三步（策略）：调用标的池API获取标的池成分股，根据策略实现逻辑进行历史回测

![](%E6%A0%87%E7%9A%84%E6%B1%A0/9ebb61f9-7852-4e24-b640-7886876fa1a0.png)

#### `universe_set` - 设置标的池

创建一个新标的池，或者重置已有标的池成分标的

**函数原型：**

**参数：**

|参数名|类型|中文名称|必填|默认值|参数用法说明|
|---|---|---|---|---|---|
|universe\_name|str|标的池名称|Y|无|指定标的池名称|
|universe\_symbols|list\[str\]|成分标的代码|N|None|单个标的示例：\['SZSE.000002'\],多个标的示例：\['SHSE.600008', 'SZSE.000002'\]|

**返回值：None**

**示例：**

**注意：**

**1.** 创建/重置标的池失败会报错。

**2.** 传入的标的池名称universe\_name已存在，会根据universe\_symbols重置当前标的池成分。

**3.** 传入的标的池名称universe\_name不存在，会创建一个新标的池。

**4.** 当universe\_symbols为空列表或None时，会创建/重置为成分数量为0的一个空标的池。

**5.** 若已存在重名标的池，会随机选取其中一个标的池进行重置。

#### `universe_get_symbols` - 获取标的池成分

获取单个标的池的成分标的代码

**函数原型：**

**参数：**

|参数名|类型|中文名称|必填|默认值|参数用法说明|
|---|---|---|---|---|---|
|universe\_name|str|标的池名称|Y|无|指定标的池名称|

**返回值：list\[str\]**

|类型|说明|
|---|---|
|list\[str\]|`成分标的代码` 列表|

**示例：**

**输出：**

**注意：**

**1.** 不存在的标的池，返回None。

**2.** 成分标的数量为0，返回空列表。

**3.** 若存在重名标的池，随机返回其中一个标的池的成分代码。

#### `universe_get_names` - 获取全部标的池名称

获取全部已创建标的池名称

**函数原型：**

**返回值：list\[str\]**

|类型|说明|
|---|---|
|list\[str\]|`标的池名称` 列表|

**示例：**

**输出：**

**注意：**

**1.** 只返回已创建的标的池名称列表。

**2.** 没有已创建标的池，返回空列表。

#### `universe_delete` - 删除标的池

删除一个已创建标的池

**函数原型：**

**参数：**

|参数名|类型|中文名称|必填|默认值|参数用法说明|
|---|---|---|---|---|---|
|universe\_name|str|标的池名称|Y|无|指定要删除的标的池|

**返回值：None**

**示例：**

**注意：**

**1.** 删除标的池失败会报错

> 上次更新: 2/14/2025, 3:43:32 PM

---

## 函数

### 基本函数

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%9F%BA%E6%9C%AC%E5%87%BD%E6%95%B0.html>

#### 基本函数

#### init - 初始化策略

初始化策略, 策略启动时自动执行。可以在这里初始化策略配置参数。

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文，全局变量可存储在这里|

**示例：**

**注意：**

**1.** 回测模式下init函数里不支持交易操作，仿真模式和实盘模式支持。

**2.** init只会在策略启动时运行一次，如果不是每天重启策略，每天需要查询更新数据，可以通过设置定时任务执行。

#### schedule - 定时任务配置

在指定时间自动执行策略算法, 通常用于选股类型策略

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|schedule\_func|function|策略定时执行算法|
|date\_rule|str|n + 时间单位， 可选'd/w/m' 表示 n 天/n 周/n 月|
|time\_rule|str|执行算法的具体时间 (%H:%M:%S 格式)|

**返回值：**

`None`

**示例：**

**注意：**

**1.** time\_rule 的时,分,秒均不可以只输入个位数，例:`'9:40:0'`或`'14:5:0'`

**2.** 目前暂时支持`1d`、`1w`、`1m`，其中`1w`、`1m`仅用于回测

#### run - 运行策略

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|strategy\_id|str|策略 id|
|filename|str|策略文件名称|
|mode|int|策略模式 MODE\_LIVE(实时)=1 MODE\_BACKTEST(回测) =2|
|token|str|用户标识|
|backtest\_start\_time|str|回测开始时间 (%Y-%m-%d %H:%M:%S 格式)|
|backtest\_end\_time|str|回测结束时间 (%Y-%m-%d %H:%M:%S 格式)|
|backtest\_initial\_cash|double|回测初始资金, 默认 1000000|
|backtest\_transaction\_ratio|double|回测成交比例, 默认 1.0, 即下单 100%成交|
|backtest\_commission\_ratio|double|回测佣金比例, 默认 0|
|backtest\_slippage\_ratio|double|回测滑点比例, 默认 0|
|backtest\_adjust|int|回测复权方式(默认不复权) ADJUST\_NONE(不复权)=0 ADJUST\_PREV(前复权)=1 ADJUST\_POST(后复权)=2|
|backtest\_check\_cache|int|回测是否使用缓存：1 - 使用， 0 - 不使用；默认使用|
|serv\_addr|str|终端服务地址, 默认本地地址, 可不填，若需指定应输入 ip+端口号，如"127.0.0.1:7001"|
|backtest\_match\_mode|int|回测市价撮合模式： 1-实时撮合：在当前 bar 的收盘价/当前 tick 的 price 撮合，0-延时撮合：在下个 bar 的开盘价/下个 tick 的 price 撮合，默认是模式 0|
|backtest\_intraday|int|回测模式不订阅行情，current 和 current\_price 行情数据查询函数返回的日线价格类型：
1 - 当日收盘价: 回测当前交易日的日线收盘价（T日盘中和盘后均为T日收盘价）;
0 - 历史收盘价：回测当前时刻的历史最新日线收盘价（T日盘中为T-1日收盘价，T日盘后为T日收盘价），默认是0|

**返回值：**

`None`

**示例：**

**注意：**

**1.** run 函数中，`mode=1`也可改为`mode=MODE_LIVE`，两者等价，`backtest_adjust`同理

**2.** 在前复权和后复权回测模式下，是不会处理分红、送股、拆分事件的，因为除权除息产生的变动已经通过复权因子调整反映在前复权/后复权股价中，无需重复处理。不复权会自动处理分红送转。

**3.** filename 指运行的 py 文件名字，如该策略文件名为 Strategy.py,则此处应填"Strategy.py"

#### stop - 停止策略

停止策略，退出策略进程

**函数原型：**

**返回值：**

`None`

**示例：**

#### timer - 设置定时器

设定定时器的间隔秒数，每过设定好的秒数调用一次计时器 timer\_func()，直到 timer\_stop()结束定时器为止。 （仿真、实盘场景适用，回测模式下不生效）

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|timer\_func|function|在 timer 设置的时间到达时触发的事件函数|
|period|int|定时事件间隔毫秒数，设定每隔多少毫秒触发一次定时器，范围在 \[1,43200000\]|
|start\_delay|int|等待秒数(毫秒)，设定多少毫秒后启动定时器，范围在\[0,43200000\]|

**返回值： dict**

|字段|类型|说明|
|---|---|---|
|timer\_status|int|定时器设置是否成功，成功=0，失败=非 0 错误码（timer\_id 无效）。|
|timer\_id|int|设定好的定时器 id|

#### `timer_stop` - 停止定时器

停止已设置的定时器

**函数原型：**

**参数：**

|字段|类型|说明|
|---|---|---|
|timer\_id|int|要停止的定时器 id|

**返回值：**

|字段|类型|说明|
|---|---|---|
|is\_stop|bool|是否成功停止，True or False|

**示例：**

**注意：**

**1.** 仿真、实盘场景适用，回测模式下不生效

**2.** period 从前一次事件函数开始执行时点起算，若下一次事件函数需要执行时，前一次事件函数没运行完毕，等待上一个事件执行完毕再执行下一个事件。

> 上次更新: 8/5/2025, 4:38:13 PM

### 其他函数

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%85%B6%E4%BB%96%E5%87%BD%E6%95%B0.html>

#### 其他函数

#### `set_token` - 设置 token

用户有时只需要提取数据, set\_token 后就可以直接调用数据函数, 无需编写策略结构。如果 token 不合法, 访问需要身份验证的函数会抛出异常。

**token 位置参见终端-系统设置界面-密钥管理（token）**

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|token|str|身份标识|

**返回值：**

`None`

**示例：**

**注意：** token 输入错误会报错“错误或无效的 token”。

#### `set_option` - 系统设置函数

设置策略单次运行时的系统选项。

**函数原型：**

**参数：**

|**参数名**|**类型**|**说明**|
|---|---|---|
|max\_wait\_time|int|api 调用触发流控（超出单位时间窗口的调用次数）时，允许系统冷却的最大等待时间（单位：毫秒）。 若系统当次冷却需要的时间>max\_wait\_time，api 调用失败会返回流控错误，需要策略自行处理（例如捕获错误提示中的需等待时间，自行等待相应时间）。 默认`max_wait_time=3600000`，即最大 3600000 毫秒，可设范围\[0,3600000\].|
|backtest\_thread\_num|int|回测运行的最大线程个数。默认`backtest_thread_num=1`，即回测运行最多使用 1 个线程，可设范围\[1,32\].|
|ctp\_md\_info|dict|ctp柜台tick行情设置|

**返回值：**

`None`

**示例：**

**注意：**

1.  设置`max_wait_time`，在回测模式/实时模式均可生效，与 run()中设定的策略模式 mode 一致。
    -   用户策略触发流控规则后，掘金系统默认会自动冷却，等待下一时间窗口再请求下一轮，不会中止策略；只有系统当次冷却需要的时间超出`max_wait_time`时，才会返回流控错误并终止策略。
    -   如果自定义最大等待时间`max_wait_time=0`，触发流控规则后会返回流控错误并中止策略。如果不想中止策略，可根据流控错误提示中的等待时间，自行冷却，再次发起调用请求。
2.  设置`backtest_thread_num`，只对回测模式生效。

**示例：**

-   连CTP柜台tick行情

#### log - 日志函数

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|level|str|日志级别 `debug`, `info`, `warning`, `error`|
|msg|str|信息|
|source|str|来源|

**返回值：**

`None`

**示例：**

**注意：**

**1.** log 函数仅支持**实时模式**，输出到终端策略日志处。

**2.** level 输入无效参数不会报错，终端日志无显示。

**3.** 参数类型报 NameError 错误,缺少参数报 TypeError 错误。

**4.** 重启终端日志记录会自动清除，需要记录日志到本地的，可以使用 Python 的 logging 库

#### `get_strerror` - 查询错误码的错误描述信息

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|error\_code|int|错误码|

全部 [错误码详细信息](https://www.myquant.cn/docs2/sdk/python/%E9%94%99%E8%AF%AF%E7%A0%81.html)

**返回值：**

错误原因描述信息字符串

**示例：**

**输出：**

**注意：**

error\_code 值输入错误无报错，返回值为空。

#### `get_version` - 查询 api 版本

**函数原型：**

**返回值：**

字符串 当前 API 版本号

**示例：**

**输出：**

> 上次更新: 5/29/2024, 5:29:07 PM

---

## 事件

### 其他事件

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%85%B6%E4%BB%96%E4%BA%8B%E4%BB%B6.html>

#### 其他事件

#### `on_backtest_finished` - 回测结束事件

描述： 在回测模式下，回测结束后会触发该事件，并返回回测得到的绩效指标对象

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|
|indicator|[indicator](https://www.myquant.cn/docs2/sdk/python/%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84.html#indicator-%E7%BB%A9%E6%95%88%E6%8C%87%E6%A0%87%E5%AF%B9%E8%B1%A1)|绩效指标|

**示例：**

**返回：**

#### `on_error` - 错误事件

描述： 当发生异常情况，比如断网时、终端服务崩溃是会触发

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|
|code|int|[错误码](https://www.myquant.cn/docs2/sdk/python/%E9%94%99%E8%AF%AF%E7%A0%81.html)|
|info|str|错误信息|

**示例：**

**返回：**

#### `on_market_data_connected` - 实时行情网络连接成功事件

描述： 实时行情网络连接时触发，比如策略实时运行启动后会触发、行情断连又重连后会触发

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|

**示例：**

#### `on_trade_data_connected` - 交易通道网络连接成功事件

描述： 目前监控 SDK 的交易和终端的链接情况，终端之后部分暂未做在内。账号连接情况可通过终端内账户连接指示灯查看

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|

**示例：**

#### `on_market_data_disconnected` - 实时行情网络连接断开事件

**函数原型：**

描述： 实时行情网络断开时触发，比如策略实时运行行情断连会触发

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|

**示例：**

#### `on_trade_data_disconnected` - 交易通道网络连接断开事件

描述： 目前监控 SDK 的交易和终端的链接情况，终端交易服务崩溃后会触发，终端之后部分暂未做在内。账号连接情况可通过终端内账户连接指示灯查看

**函数原型：**

**参数：**

|参数名|类型|说明|
|---|---|---|
|context|[context](https://www.myquant.cn/docs2/sdk/python/%E5%8F%98%E9%87%8F%E7%BA%A6%E5%AE%9A.html#context-%E4%B8%8A%E4%B8%8B%E6%96%87%E5%AF%B9%E8%B1%A1)|上下文|

**示例：**

> 上次更新: 11/29/2024, 2:23:31 PM

---

## 错误码

> 掘金量化支持中心，提供量化学习、答疑、指引等大量文档，包括掘金量化快速入门、操作指引、Python SDK内容、C++ SDK内容、C# SDK内容、Matlab SDK内容、基础数据、行情数据、量化数据、常见问题、量化工具等文档

来源：<https://www.myquant.cn/docs2/sdk/python/%E9%94%99%E8%AF%AF%E7%A0%81.html>

### 错误码

|错误码|描述|解决方法|
|---|---|---|
|0|成功|
|1000|错误或无效的 token|检查下[token](https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%85%B6%E4%BB%96%E5%87%BD%E6%95%B0.html#set-token-%E8%AE%BE%E7%BD%AE-token)是否有误|
|1001|无法连接到终端服务|检查是否开启了掘金终端|
|1010|无法获取掘金服务器地址列表|检查是否开启了掘金终端|
|1013|交易服务调用错误|检查终端是否正常或重启掘金终端|
|1014|历史行情服务调用错误|在微信群或者 QQ 群通知技术支持|
|1015|策略服务调用错误|检查终端是否正常或重启掘金终端|
|1016|动态参数调用错误|检查[动态参数](https://www.myquant.cn/docs2/sdk/python/API%E4%BB%8B%E7%BB%8D/%E5%8A%A8%E6%80%81%E5%8F%82%E6%95%B0.html#%E5%8A%A8%E6%80%81%E5%8F%82%E6%95%B0)设置|
|1017|基本面数据服务调用错误|在微信群或者 QQ 群通知技术支持|
|1018|回测服务调用错误|重启掘金终端、重新运行策略|
|1019|交易网关服务调用错误|检查终端是否正常或重启掘金终端|
|1020|无效的 ACCOUNT\_ID|检查账户 id 是否填写正确|
|1021|非法日期格式|对照帮助文档修改日期格式， 检查 run()回测日期是否正确|
|1025|无法连接到认证服务|在微信群或者 QQ 群通知技术支持|
|1026|更新令牌错误|在微信群或者 QQ 群通知技术支持|
|1027|接口调用错误，无效入参|例如检查定时任务的频率参数，实时模式只支持 1d|
|1028|不支持的服务|在微信群或者 QQ 群通知技术支持|
|1029|超出最大限制设置|检查入参的标的个数和时间范围|
|1100|交易消息服务连接失败|检查终端是否正常或重启掘金终端|
|1101|交易消息服务断开|一般不用处理，等待自动重连|
|1200|实时行情服务连接失败|一般不用处理，等待自动重连|
|1201|实时行情服务连接断开|一般不用处理，等待自动重连|
|1202|实时行情订阅失败|订阅代码标的数量超过账户权限，联系商务咨询权限|
|1300|初始化回测失败|检查终端是否启动或策略是否连接到终端|
|1301|回测时间区间错误|检查回测时间是否超出范围|
|1302|回测读取缓存数据错误|在微信群或者 QQ 群通知技术支持|
|1303|回测写入缓存数据错误|在微信群或者 QQ 群通知技术支持|
|2001|用户无此数据接口权限|联系商务咨询权限|
|2002|超出业务授权范围|调整数据日期范围，或者联系商务延长权限|
|2003|实时行情订阅代码数量超过用户权限|联系商务咨询权限|
|3001|超出数据接口调用频率限制（流控）|检查程序是否异常运行导致循环取数，增加等待时间再调用|

> 上次更新: 3/25/2024, 5:56:44 PM
