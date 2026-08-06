"""
个股深度分析报告生成器
整合技术面、基本面、K线形态、支撑阻力位、多周期分析，
生成完整的HTML个股分析报告
"""
import os
import html as _html
import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Any
from datetime import datetime
from jinja2 import Template

logger = logging.getLogger("stock_analysis_report")


class StockAnalysisReportGenerator:
    """个股深度分析报告生成器"""

    # 评分等级阈值
    _RATING_THRESHOLDS = [
        (80, "强烈推荐"),
        (70, "推荐"),
        (60, "中性偏多"),
        (50, "中性"),
        (40, "中性偏空"),
        (30, "回避"),
        (0, "强烈回避"),
    ]

    # 趋势/强度对应的颜色类
    _TREND_COLOR = {
        "上涨": "trend-up",
        "下跌": "trend-down",
        "震荡": "trend-neutral",
        "未知": "trend-unknown",
    }
    _STRENGTH_COLOR = {
        "强": "strength-strong",
        "中": "strength-medium",
        "弱": "strength-weak",
        "无": "strength-none",
    }

    def __init__(self):
        # 防御性导入: charts 模块可能在后续任务中创建
        self.kline_gen = None
        self.indicator_gen = None
        self.fundamental_gen = None
        try:
            from ..charts.kline_chart import KlineChartGenerator
            self.kline_gen = KlineChartGenerator()
        except Exception as e:
            logger.warning(f"KlineChartGenerator 不可用: {e}")
        try:
            from ..charts.indicator_chart import IndicatorChartGenerator
            self.indicator_gen = IndicatorChartGenerator()
        except Exception as e:
            logger.warning(f"IndicatorChartGenerator 不可用: {e}")
        try:
            from ..charts.fundamental_dashboard import FundamentalDashboardGenerator
            self.fundamental_gen = FundamentalDashboardGenerator()
        except Exception as e:
            logger.warning(f"FundamentalDashboardGenerator 不可用: {e}")

    # ================================================================
    # 公共入口
    # ================================================================


    # ================================================================
    # 综合评分
    # ================================================================

    def _calc_comprehensive_score(self, technical_indicators: Dict,
                                  pattern_results: Dict,
                                  multi_timeframe: Dict,
                                  fundamental_data: Optional[Dict] = None) -> Dict:
        """
        计算综合评分

        技术面评分 (0-100):
        - 趋势方向 (30分): 多周期共振 + 均线排列
        - 指标信号 (30分): MACD/RSI/KDJ信号
        - K线形态 (20分): 近期看涨/看跌形态
        - 量价配合 (20分): 成交量变化

        基本面评分 (0-100):
        - 估值水平 (35分): PE/PB分位
        - 盈利能力 (35分): ROE/毛利率
        - 成长性 (30分): 营收/利润增速

        综合评分 = 技术面 * 0.5 + 基本面 * 0.5
        """
        # ── 技术面评分 ──────────────────────────────
        trend_score = self._score_trend(multi_timeframe, technical_indicators)
        indicator_score = self._score_indicator_signals(technical_indicators, multi_timeframe)
        pattern_score = self._score_patterns(pattern_results)
        volume_score = self._score_volume_price(technical_indicators)

        technical_total = trend_score + indicator_score + pattern_score + volume_score
        technical_total = float(min(100.0, max(0.0, technical_total)))

        # ── 基本面评分 ──────────────────────────────
        fundamental_total = None
        valuation_score = None
        profitability_score = None
        growth_score = None
        if fundamental_data:
            valuation_score = self._score_valuation(fundamental_data)
            profitability_score = self._score_profitability(fundamental_data)
            growth_score = self._score_growth(fundamental_data)
            fundamental_total = float(min(100.0, max(0.0,
                valuation_score + profitability_score + growth_score
            )))

        # ── 综合评分 ──────────────────────────────
        if fundamental_total is not None:
            comprehensive = technical_total * 0.5 + fundamental_total * 0.5
        else:
            comprehensive = technical_total
        comprehensive = float(min(100.0, max(0.0, comprehensive)))

        rating = self._rating(comprehensive)

        return {
            "technical": round(technical_total, 1),
            "fundamental": round(fundamental_total, 1) if fundamental_total is not None else None,
            "comprehensive": round(comprehensive, 1),
            "rating": rating,
            "has_fundamental": fundamental_total is not None,
            "breakdown": {
                "trend": round(trend_score, 1),
                "indicator": round(indicator_score, 1),
                "pattern": round(pattern_score, 1),
                "volume": round(volume_score, 1),
                "valuation": round(valuation_score, 1) if valuation_score is not None else None,
                "profitability": round(profitability_score, 1) if profitability_score is not None else None,
                "growth": round(growth_score, 1) if growth_score is not None else None,
            },
        }

    def _score_trend(self, multi_timeframe: Dict, technical_indicators: Dict) -> float:
        """趋势方向评分 (满分 30): 多周期共振 + 均线排列"""
        score = 0.0
        resonance = multi_timeframe.get("resonance", {}) if multi_timeframe else {}

        # 多周期共振 (0-22分)
        if resonance.get("all_bullish"):
            score += 22.0
        elif resonance.get("bullish"):
            score += 16.0
        elif resonance.get("all_bearish"):
            score += 0.0
        elif resonance.get("bearish"):
            score += 6.0
        else:
            score += 11.0  # 中性

        # 均线排列 (0-8分): MA5 > MA20 > MA60 为多头排列
        ma5 = self._get_indicator(technical_indicators, multi_timeframe, "ma5")
        ma20 = self._get_indicator(technical_indicators, multi_timeframe, "ma20")
        ma60 = self._get_indicator(technical_indicators, multi_timeframe, "ma60")
        if all(v is not None and pd.notna(v) for v in [ma5, ma20, ma60]):
            if ma5 > ma20 > ma60:
                score += 8.0
            elif ma5 < ma20 < ma60:
                score += 0.0
            elif ma5 > ma20:
                score += 4.0
            else:
                score += 2.0
        else:
            score += 4.0  # 数据缺失, 给中性分

        return min(30.0, score)

    def _score_indicator_signals(self, technical_indicators: Dict, multi_timeframe: Dict) -> float:
        """指标信号评分 (满分 30): MACD/RSI/KDJ 信号"""
        score = 0.0

        # MACD (0-12分)
        macd_dif = self._get_indicator(technical_indicators, multi_timeframe, "macd_dif")
        macd_dea = self._get_indicator(technical_indicators, multi_timeframe, "macd_dea")
        macd_hist = self._get_indicator(technical_indicators, multi_timeframe, "macd_hist")
        if macd_dif is not None and macd_dea is not None and pd.notna(macd_dif) and pd.notna(macd_dea):
            if macd_dif > macd_dea:
                score += 9.0  # 金叉状态
                if macd_dif > 0:
                    score += 3.0  # 零轴上方
            else:
                score += 2.0  # 死叉状态
                if macd_dif < 0:
                    score += 0.0
                else:
                    score += 1.0
        elif macd_hist is not None and pd.notna(macd_hist):
            score += 6.0 if macd_hist > 0 else 3.0
        else:
            score += 6.0  # 数据缺失, 中性

        # RSI (0-9分)
        rsi = self._get_indicator(technical_indicators, multi_timeframe, "rsi")
        if rsi is not None and pd.notna(rsi):
            rsi = float(rsi)
            if rsi < 30:
                score += 9.0  # 超卖, 反弹机会
            elif rsi < 45:
                score += 7.0
            elif rsi < 55:
                score += 5.0  # 中性
            elif rsi < 70:
                score += 3.0
            else:
                score += 1.0  # 超买, 回调风险
        else:
            score += 4.5

        # KDJ (0-9分)
        kdj_j = self._get_indicator(technical_indicators, multi_timeframe, "kdj_j")
        kdj_k = self._get_indicator(technical_indicators, multi_timeframe, "kdj_k")
        kdj_d = self._get_indicator(technical_indicators, multi_timeframe, "kdj_d")
        if kdj_k is not None and kdj_d is not None and pd.notna(kdj_k) and pd.notna(kdj_d):
            if kdj_k > kdj_d:
                score += 6.0  # 金叉
                if kdj_j is not None and pd.notna(kdj_j) and kdj_j < 20:
                    score += 3.0  # 低位金叉
                elif kdj_j is not None and pd.notna(kdj_j) and kdj_j > 100:
                    score += 0.0  # 高位, 虽金叉但风险高
                else:
                    score += 1.5
            else:
                score += 1.0  # 死叉
                if kdj_j is not None and pd.notna(kdj_j) and kdj_j > 100:
                    score += 0.0
                elif kdj_j is not None and pd.notna(kdj_j) and kdj_j < 0:
                    score += 3.0  # 超卖区死叉, 可能接近底部
                else:
                    score += 1.0
        else:
            score += 4.5

        return min(30.0, score)

    def _score_patterns(self, pattern_results: Dict) -> float:
        """K线形态评分 (满分 20): 近期看涨/看跌形态"""
        if not pattern_results:
            return 10.0  # 中性

        bullish_count = int(pattern_results.get("bullish_count", 0))
        bearish_count = int(pattern_results.get("bearish_count", 0))
        dominant = pattern_results.get("dominant_signal", "neutral")

        total = bullish_count + bearish_count
        if total == 0:
            return 10.0

        bull_ratio = bullish_count / total

        if dominant == "bullish":
            base = 16.0
        elif dominant == "bearish":
            base = 4.0
        else:
            base = 10.0

        # 根据比例微调
        adjustment = (bull_ratio - 0.5) * 8.0
        score = base + adjustment
        return float(min(20.0, max(0.0, score)))

    def _score_volume_price(self, technical_indicators: Dict) -> float:
        """量价配合评分 (满分 20): 成交量变化"""
        # 优先使用预计算的 up_down_volume_ratio
        ratio = technical_indicators.get("up_down_volume_ratio")
        if ratio is None or not pd.notna(ratio):
            return 10.0  # 中性

        ratio = float(ratio)
        # ratio > 1 表示上涨日成交量大于下跌日
        if ratio > 1.8:
            return 20.0
        elif ratio > 1.4:
            return 17.0
        elif ratio > 1.1:
            return 14.0
        elif ratio > 0.9:
            return 10.0
        elif ratio > 0.6:
            return 6.0
        else:
            return 3.0

    def _score_valuation(self, fundamental_data: Dict) -> float:
        """估值水平评分 (满分 35): PE/PB分位"""
        if not fundamental_data:
            return 17.5

        pe_pct = self._get_fundamental(fundamental_data, "pe_percentile", "pe_pct", "pe_quantile")
        pb_pct = self._get_fundamental(fundamental_data, "pb_percentile", "pb_pct", "pb_quantile")

        scores = []
        for pct in [pe_pct, pb_pct]:
            if pct is not None and pd.notna(pct):
                pct = float(pct)
                if pct < 0.2:
                    scores.append(17.5)
                elif pct < 0.4:
                    scores.append(14.0)
                elif pct < 0.6:
                    scores.append(10.5)
                elif pct < 0.8:
                    scores.append(7.0)
                else:
                    scores.append(3.5)

        if not scores:
            # 无分位数据时用 PE 绝对值粗略判断
            pe = self._get_fundamental(fundamental_data, "pe", "pe_ttm", "pe_ratio")
            if pe is not None and pd.notna(pe):
                pe = float(pe)
                if pe < 0:
                    return 5.0  # 亏损
                elif pe < 15:
                    return 28.0
                elif pe < 25:
                    return 21.0
                elif pe < 40:
                    return 14.0
                elif pe < 60:
                    return 7.0
                else:
                    return 3.5
            return 17.5

        return float(np.mean(scores))

    def _score_profitability(self, fundamental_data: Dict) -> float:
        """盈利能力评分 (满分 35): ROE/毛利率"""
        if not fundamental_data:
            return 17.5

        roe = self._get_fundamental(fundamental_data, "roe", "roe_ttm", "return_on_equity")
        gross_margin = self._get_fundamental(fundamental_data, "gross_margin", "毛利率")

        scores = []

        if roe is not None and pd.notna(roe):
            roe = float(roe)
            # ROE 以百分比或小数传入都能处理
            if abs(roe) < 1:
                roe = roe * 100
            if roe > 20:
                scores.append(22.0)
            elif roe > 15:
                scores.append(18.0)
            elif roe > 10:
                scores.append(14.0)
            elif roe > 5:
                scores.append(9.0)
            elif roe > 0:
                scores.append(5.0)
            else:
                scores.append(0.0)

        if gross_margin is not None and pd.notna(gross_margin):
            gm = float(gross_margin)
            if abs(gm) < 1:
                gm = gm * 100
            if gm > 60:
                scores.append(13.0)
            elif gm > 40:
                scores.append(10.0)
            elif gm > 25:
                scores.append(7.0)
            elif gm > 15:
                scores.append(4.0)
            else:
                scores.append(2.0)

        if not scores:
            return 17.5

        # ROE 权重高于毛利率
        if len(scores) == 2:
            return float(scores[0] * (22.0 / 35.0) + scores[1] * (13.0 / 35.0))
        # 单项时按比例还原到35分制
        if roe is not None and pd.notna(roe):
            return float(scores[0] * (35.0 / 22.0))
        return float(scores[0] * (35.0 / 13.0))

    def _score_growth(self, fundamental_data: Dict) -> float:
        """成长性评分 (满分 30): 营收/利润增速"""
        if not fundamental_data:
            return 15.0

        rev_growth = self._get_fundamental(fundamental_data, "revenue_growth", "rev_growth", "营收增速")
        profit_growth = self._get_fundamental(fundamental_data, "profit_growth", "net_profit_growth", "利润增速")

        scores = []

        for g in [rev_growth, profit_growth]:
            if g is not None and pd.notna(g):
                g = float(g)
                if abs(g) < 1:
                    g = g * 100
                if g > 30:
                    scores.append(15.0)
                elif g > 15:
                    scores.append(11.0)
                elif g > 5:
                    scores.append(8.0)
                elif g > 0:
                    scores.append(5.0)
                elif g > -10:
                    scores.append(3.0)
                else:
                    scores.append(0.0)

        if not scores:
            return 15.0

        return float(np.mean(scores))

    # ================================================================
    # 风险提示
    # ================================================================

    def _generate_risk_warnings(self, technical_indicators: Dict,
                                pattern_results: Dict,
                                multi_timeframe: Dict,
                                support_resistance: Dict) -> List[str]:
        """生成风险提示列表"""
        warnings: List[str] = []
        technical_indicators = technical_indicators or {}
        pattern_results = pattern_results or {}
        multi_timeframe = multi_timeframe or {}
        support_resistance = support_resistance or {}

        # 1. 多周期看空共振
        resonance = multi_timeframe.get("resonance", {})
        if resonance.get("all_bearish"):
            warnings.append("多周期共振看空，日/周/月线全部下跌，趋势明确转弱，建议谨慎")
        elif resonance.get("bearish"):
            warnings.append("日线与月线同步走弱，中期趋势偏空")

        # 2. 顶背离信号
        divergences = multi_timeframe.get("divergences", [])
        top_divs = [d for d in divergences if d.get("type") == "顶背离"]
        if top_divs:
            descs = "；".join(d.get("description", "") for d in top_divs[:3])
            warnings.append(f"检测到顶背离信号 ({descs})，价格创新高但指标未跟上，短期可能面临调整压力")

        # 3. RSI 超买
        rsi = self._get_indicator(technical_indicators, multi_timeframe, "rsi")
        if rsi is not None and pd.notna(rsi):
            if float(rsi) > 80:
                warnings.append(f"RSI={float(rsi):.1f}，处于严重超买区，回调风险较大")
            elif float(rsi) > 70:
                warnings.append(f"RSI={float(rsi):.1f}，处于超买区，注意短期回调")

        # 4. 布林带触及上轨
        boll_pos = self._get_indicator(technical_indicators, multi_timeframe, "boll_position")
        if boll_pos is not None and pd.notna(boll_pos):
            if float(boll_pos) > 0.95:
                warnings.append("价格触及布林带上轨，短期存在回归中轨的压力")

        # 5. KDJ 超买
        kdj_j = self._get_indicator(technical_indicators, multi_timeframe, "kdj_j")
        kdj_k = self._get_indicator(technical_indicators, multi_timeframe, "kdj_k")
        if kdj_j is not None and pd.notna(kdj_j) and float(kdj_j) > 100:
            warnings.append(f"KDJ的J值={float(kdj_j):.1f}，处于超买区，存在技术性回调风险")
        elif kdj_k is not None and pd.notna(kdj_k) and float(kdj_k) > 80:
            warnings.append(f"KDJ的K值={float(kdj_k):.1f}，处于高位，注意短期波动")

        # 6. MACD 死叉
        macd_dif = self._get_indicator(technical_indicators, multi_timeframe, "macd_dif")
        macd_dea = self._get_indicator(technical_indicators, multi_timeframe, "macd_dea")
        if macd_dif is not None and macd_dea is not None and pd.notna(macd_dif) and pd.notna(macd_dea):
            if macd_dif < macd_dea:
                if macd_dif < 0:
                    warnings.append("MACD死叉且处于零轴下方，趋势偏空")
                else:
                    warnings.append("MACD死叉，短期趋势转弱")

        # 7. K线形态偏空
        dominant = pattern_results.get("dominant_signal", "neutral")
        bearish_count = int(pattern_results.get("bearish_count", 0))
        if dominant == "bearish" and bearish_count > 0:
            warnings.append(f"近期K线形态偏空，检测到{bearish_count}个看跌形态，注意下跌风险")

        # 8. 接近阻力位
        current_price = support_resistance.get("current_price")
        nearest_resistance = support_resistance.get("nearest_resistance")
        if current_price and nearest_resistance and current_price > 0:
            distance = (nearest_resistance - current_price) / current_price
            if 0 < distance < 0.03:
                warnings.append(f"价格接近阻力位 {nearest_resistance:.2f} (距现价 {distance*100:.1f}%)，注意上方压力")

        # 9. 跌破支撑位
        nearest_support = support_resistance.get("nearest_support")
        if current_price and nearest_support and current_price > 0:
            if current_price < nearest_support:
                warnings.append(f"价格已跌破最近支撑位 {nearest_support:.2f}，下方支撑失效")

        # 10. 估值过高 (基本面)
        pe_pct = self._get_fundamental(technical_indicators, "pe_percentile", "pe_pct")
        if pe_pct is not None and pd.notna(pe_pct) and float(pe_pct) > 0.8:
            warnings.append(f"PE估值处于历史{float(pe_pct)*100:.0f}%分位，估值偏高，注意估值回归风险")

        # 11. 业绩下滑 (若有基本面数据传入 technical_indicators)
        profit_growth = self._get_fundamental(technical_indicators, "profit_growth", "net_profit_growth")
        if profit_growth is not None and pd.notna(profit_growth):
            pg = float(profit_growth)
            if abs(pg) < 1:
                pg = pg * 100
            if pg < -15:
                warnings.append(f"净利润增速={pg:.1f}%，业绩明显下滑，关注基本面恶化风险")

        # 12. 量价背离
        vol_ratio = technical_indicators.get("up_down_volume_ratio")
        if vol_ratio is not None and pd.notna(vol_ratio):
            # 价格上涨但下跌日成交量更大 → 量价背离
            daily_trend = multi_timeframe.get("timeframes", {}).get("daily", {}).get("trend", "")
            if daily_trend == "上涨" and float(vol_ratio) < 0.8:
                warnings.append("价格上涨但下跌日成交量显著放大，存在量价背离，上涨持续性存疑")

        if not warnings:
            warnings.append("暂无重大风险信号提示，但仍需关注市场整体环境变化")

        return warnings

    # ================================================================
    # HTML 渲染
    # ================================================================


    # ================================================================
    # 辅助方法
    # ================================================================

    def _rating(self, score: float) -> str:
        """根据综合评分返回评级"""
        for threshold, label in self._RATING_THRESHOLDS:
            if score >= threshold:
                return label
        return "强烈回避"

    def _get_indicator(self, technical_indicators: Dict,
                       multi_timeframe: Dict, key: str) -> Any:
        """
        从 technical_indicators 或 multi_timeframe.daily.indicators 中
        提取指标值, technical_indicators 优先
        """
        # 1. 在 technical_indicators 中查找 (支持嵌套 dict)
        if technical_indicators:
            # 直接键名匹配 (忽略大小写)
            for k, v in technical_indicators.items():
                if isinstance(k, str) and k.lower() == key.lower():
                    if not isinstance(v, dict):
                        return v
            # 嵌套分组查找: {"MACD": {"dif": ...}, "KDJ": {"k": ...}}
            group_map = {
                "macd_dif": ("MACD", ["dif", "diff", "DIF"]),
                "macd_dea": ("MACD", ["dea", "signal", "DEA"]),
                "macd_hist": ("MACD", ["hist", "bar", "HIST"]),
                "kdj_k": ("KDJ", ["k", "K"]),
                "kdj_d": ("KDJ", ["d", "D"]),
                "kdj_j": ("KDJ", ["j", "J"]),
                "ma5": ("MA", ["ma5", "MA5", "5"]),
                "ma10": ("MA", ["ma10", "MA10", "10"]),
                "ma20": ("MA", ["ma20", "MA20", "20"]),
                "ma60": ("MA", ["ma60", "MA60", "60"]),
            }
            if key in group_map:
                group_name, sub_keys = group_map[key]
                group = technical_indicators.get(group_name)
                if isinstance(group, dict):
                    for sk in sub_keys:
                        if sk in group:
                            return group[sk]
                # 也尝试小写的组名
                group = technical_indicators.get(group_name.lower())
                if isinstance(group, dict):
                    for sk in sub_keys:
                        if sk in group:
                            return group[sk]

        # 2. 回退到 multi_timeframe.daily.indicators
        daily_ind = (multi_timeframe or {}).get(
            "timeframes", {}
        ).get("daily", {}).get("indicators", {})
        if daily_ind:
            for k, v in daily_ind.items():
                if isinstance(k, str) and k.lower() == key.lower():
                    return v
        return None

    def _get_fundamental(self, fundamental_data: Dict, *keys) -> Any:
        """从基本面数据中查找值, 支持多个候选键名"""
        if not fundamental_data:
            return None
        lower_map = {}
        for k, v in fundamental_data.items():
            if isinstance(k, str):
                lower_map[k.lower()] = v
        for key in keys:
            k = key.lower() if isinstance(key, str) else key
            if k in lower_map:
                return lower_map[k]
        return None

    def _tmpl_ind_val(self, technical_indicators: Dict,
                      multi_timeframe: Dict, key: str) -> Any:
        """模板可调用的指标提取函数"""
        return self._get_indicator(technical_indicators, multi_timeframe, key)

    def _tmpl_fund_val(self, fundamental_data: Dict, *keys) -> Any:
        """模板可调用的基本面值提取函数"""
        return self._get_fundamental(fundamental_data, *keys)

    def _compute_volume_metrics(self, ohlcv_data: pd.DataFrame) -> Dict:
        """从 OHLCV 数据计算量价指标, 注入 technical_indicators 供评分使用"""
        metrics = {}
        if ohlcv_data is None or len(ohlcv_data) < 10:
            metrics["up_down_volume_ratio"] = np.nan
            metrics["volume_trend"] = "unknown"
            return metrics

        try:
            df = ohlcv_data.copy()
            if "date" in df.columns:
                df = df.sort_values("date").reset_index(drop=True)
            recent = df.tail(20).copy()
            close = recent["close"].astype(float)
            volume = recent["volume"].astype(float)
            returns = close.pct_change().dropna()
            vol = volume.iloc[1:].reset_index(drop=True)
            rets = returns.reset_index(drop=True)

            up_mask = rets > 0
            down_mask = rets < 0
            up_vol = vol[up_mask].mean() if up_mask.any() else 0.0
            down_vol = vol[down_mask].mean() if down_mask.any() else 0.0

            if down_vol > 0 and up_vol > 0:
                ratio = float(up_vol / down_vol)
            elif up_vol > 0:
                ratio = 2.0
            elif down_vol > 0:
                ratio = 0.3
            else:
                ratio = 1.0
            metrics["up_down_volume_ratio"] = ratio

            # 成交量趋势: 近5日均量 vs 前10日均量
            if len(volume) >= 15:
                recent_vol = volume.tail(5).mean()
                prev_vol = volume.iloc[-15:-5].mean()
                if prev_vol > 0:
                    vol_change = recent_vol / prev_vol
                    if vol_change > 1.2:
                        metrics["volume_trend"] = "increasing"
                    elif vol_change < 0.8:
                        metrics["volume_trend"] = "decreasing"
                    else:
                        metrics["volume_trend"] = "stable"
                else:
                    metrics["volume_trend"] = "stable"
            else:
                metrics["volume_trend"] = "unknown"
        except Exception as e:
            logger.debug(f"计算量价指标失败: {e}")
            metrics["up_down_volume_ratio"] = np.nan
            metrics["volume_trend"] = "unknown"

        return metrics

    def _get_data_date(self, ohlcv_data: pd.DataFrame) -> str:
        """从 OHLCV 数据提取最新日期"""
        if ohlcv_data is None or ohlcv_data.empty:
            return "—"
        try:
            if "date" in ohlcv_data.columns:
                last_date = ohlcv_data["date"].iloc[-1]
                return str(last_date)[:10]
            return "—"
        except Exception:
            return "—"

    def _safe_render_chart(self, chart_gen, method_name: str, *args,
                           fallback: str = "", **kwargs) -> str:
        """安全调用图表生成器, 失败时返回占位符"""
        if chart_gen is None:
            return ""
        method = getattr(chart_gen, method_name, None)
        if method is None:
            return ""
        try:
            result = method(*args, **kwargs)
            if isinstance(result, str):
                return result
            # Plotly Figure -> HTML 片段
            if hasattr(result, "to_html"):
                return result.to_html(full_html=False, include_plotlyjs="cdn")
            return str(result) if result else ""
        except Exception as e:
            logger.warning(f"图表生成失败 ({method_name}): {e}")
            return ""

    # ── 格式化辅助 ─────────────────────────────

    @staticmethod
    def _fmt_num(value: Any, decimals: int = 2) -> str:
        """格式化数值"""
        if value is None:
            return "—"
        try:
            v = float(value)
            if pd.isna(v) or not np.isfinite(v):
                return "—"
            return f"{v:.{decimals}f}"
        except Exception:
            return "—"

    @staticmethod
    def _fmt_price(value: Any) -> str:
        """格式化价格"""
        if value is None:
            return "—"
        try:
            v = float(value)
            if pd.isna(v) or not np.isfinite(v):
                return "—"
            return f"{v:.2f}"
        except Exception:
            return "—"

    @staticmethod
    def _fmt_pct(value: Any) -> str:
        """格式化百分比 (输入可为小数 0.15 或百分数 15.0)"""
        if value is None:
            return "—"
        try:
            v = float(value)
            if pd.isna(v) or not np.isfinite(v):
                return "—"
            if abs(v) < 1:
                v = v * 100
            return f"{v:.2f}%"
        except Exception:
            return "—"

    @staticmethod
    def _fmt_date(value: Any) -> str:
        """格式化日期"""
        if value is None:
            return "—"
        try:
            s = str(value)
            return s[:10] if len(s) >= 10 else s
        except Exception:
            return "—"

    @staticmethod
    def _fmt_market_cap(value: Any) -> str:
        """格式化市值 (输入为元)"""
        if value is None:
            return "—"
        try:
            v = float(value)
            if pd.isna(v) or not np.isfinite(v):
                return "—"
            if v >= 1e12:
                return f"{v / 1e12:.2f}万亿"
            elif v >= 1e8:
                return f"{v / 1e8:.2f}亿"
            elif v >= 1e4:
                return f"{v / 1e4:.2f}万"
            return f"{v:.2f}"
        except Exception:
            return "—"
