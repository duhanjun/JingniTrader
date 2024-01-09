# -*- coding: utf-8 -*- 
 
"""
项目名称：JingniTrader

项目版本：v1.0.0

项目描述：JingniTrader是一个基于Python的量化交易开发框架，致力于提供兼容中国券商交易软件的量化解决方案

项目版权：Copyright © 2024 Du Hanjun. All rights reserved.

项目许可：项目采用MIT许可证

项目作者：二哥聊指数 (hanjun.du@outlook.com)

项目贡献：

    项目长期招募贡献者，有意向请邮件联系项目作者。

使用方法： 

    ①Python下载安装参考：https://www.python.org/downloads/
    ②Python环境变量配置：
        Windows 7 可参考：https://jingyan.baidu.com/article/48206aeafdcf2a216ad6b316.html
        Windows 10可参考：https://jingyan.baidu.com/article/60ccbcebb2b81264cab197b4.html
        Linux发行版可参考：https://jingyan.baidu.com/article/4f34706e3de476e387b56dd6.html
    ③在命令行（windows可使用win+r输入cmd）中输入python，出现类似“Python 3.10.11”提示信息，说明python环境安装成功
    ④在命令行（windows可使用win+r输入cmd）中输入pip XXXX（XXXX为本项目程序开头import代码后面的工具包）安装本项目程序运行所需的依赖项

代码结构：

    def main() 该函数作为运行本项目程序的入口
    def jingni_before_trading_start() 该函数在每天9:30开始交易前被调用一次，用于处理每天开盘前的信息，如无盘前处理需求，该函数可不调用
    def jingni_handle_data(trade_start_time, trade_end_time, trade_seconds) 该函数在交易时间内（9:30-15:00）按指定的周期频率（秒）运行，是主要的交易模块。
    def jingni_after_trading_end()    该函数会在每天15:00结束交易后被调用一次，用于处理每天收盘后的信息，如无盘后处理需求，该函数可不调用

    def jingni_select_security() 该函数用于筛选所需交易的证券品种（股票、债券、基金）
    def jingni_trade_signal()    该函数用于判断适合市场的交易信号（BUY买入、SELL卖出）
    def jingni_trade_strategy()  该函数用于构建基于模型和算法的交易决策方法（交易策略）

    def XXXX() 更多自定义函数由交易者根据实际需要自行添加

免责声明：本项目仅用于教育目的，不保证任何交易的成功。请自行承担风险！！！

关于本项目的信息和反馈，请访问项目GitHub存储库：https://github.com/duhanjun/JingniTrader

关于本项目的教程和案例，请访问项目社区[刺桐说]：https://wx.zsxq.com/dweb2/index/group/28855458552411

"""

import sys
import datetime
import time

# 定义选股模型函数
def jingni_select_security():

    # 获取市场数据
    security = ['贵州茅台', '工商银行', '中信证券']

    # 处理数据以选股
    # ...
    security = security[:2]

    # 返回选定交易品种
    return security

# 定义择时模型函数
def jingni_trade_signal():

    # 获取市场数据
    signal = ['BUY', 'SELL']

    # 处理数据以择时
    signal = signal[0]

    # 返回市场择时信号
    return signal

# 定义交易策略函数
def jingni_trade_strategy():
 
    # 运行选股模型
    security = jingni_select_security()

    # 运行择时模型
    signal = jingni_trade_signal()

    # 根据交易信号买卖证券
    if signal == 'BUY':
        for stock in security:
            # 买入证券
            print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '买入证券：%s' % stock)
            pass
    elif signal == 'SELL':
        for stock in security:
            # 卖出证券
            print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '卖出证券：%s' % stock)
            pass

# 盘前交易事件函数
def jingni_before_trading_start():

    print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '开始运行盘前函数')

    # 运行盘前函数1
    # 运行盘前函数2
    # 运行盘前函数3

    print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '结束运行盘前函数')

# 盘中交易事件函数（开始交易时间，结束交易时间，交易时间间隔）
def jingni_handle_data(trade_start_time, trade_end_time, trade_seconds):

    print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '开始运行盘中函数')

    while True:

        # 获取系统最新时间
        trade_now_time = datetime.datetime.now().time()

        # 如果系统最新时间大于等于程序开始交易时间，小于等于程序结束交易时间，则运行盘中所需相关函数
        if trade_start_time <= trade_now_time <= trade_end_time:

            # 运行盘中函数1
            # 运行盘中函数2
            # 运行盘中函数3
            jingni_trade_strategy()

        # 如果系统最新时间大于程序结束交易时间
        elif trade_now_time > trade_end_time:
            break

        # 设置交易时间间隔
        time.sleep(trade_seconds)

    print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '结束运行盘中函数')

# 盘后交易事件函数
def jingni_after_trading_end():

    print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '开始运行盘后函数')

    # 运行盘后函数1
    # 运行盘后函数2
    # 运行盘后函数3

    print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '结束运行盘后函数')

# 主函数
def main():

    # 运行盘前交易事件函数
    jingni_before_trading_start()

    # 定义程序开始交易时间为09:30:00
    trade_start_time = datetime.time(9,30,0)
    # 定义程序结束交易时间为15:00:00
    trade_end_time = datetime.time(15,00,0)
    # 定义程序交易时间间隔为10秒
    trade_seconds = 10

    while True:

        # 获取系统最新时间
        trade_now_time = datetime.datetime.now().time()

        # 如果系统最新时间大于等于程序开始交易时间，小于等于程序结束交易时间，则运行盘中交易事件函数
        if trade_start_time <= trade_now_time <= trade_end_time:
            jingni_handle_data(trade_start_time, trade_end_time, trade_seconds)

        # 如果系统最新时间大于程序结束交易时间
        elif trade_now_time > trade_end_time:
            break

    # 运行盘后交易事件函数
    jingni_after_trading_end()

# 初始化
if __name__ == '__main__':

    # 定义程序开始运行时间为09:00:00
    app_start_time = datetime.time(9,0,0)
    # 定义程序结束运行时间为15:00:00
    app_end_time = datetime.time(15,00,0)

    while True:
        # 获取系统最新时间
        app_now_time = datetime.datetime.now().time()
        print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '等待程序运行')

        # 如果系统最新时间大于等于程序开始运行时间，小于程序结束运行时间，则开始运行程序
        if app_start_time <= app_now_time <= app_end_time:
            print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '开始运行程序')
            main()

        # 如果系统最新时间大于程序结束运行时间，则自动关闭程序
        elif app_end_time < app_now_time:
            print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), '10秒自动关闭') 
            time.sleep(10)
            sys.exit()