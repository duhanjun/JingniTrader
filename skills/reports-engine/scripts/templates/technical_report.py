"""
技术面深度分析报告生成器（独立报告，借鉴 TradingAgents market_report 设计）

10 个章节：
1. 报告概览（决策仪表盘风格，借鉴 daily_stock_analysis）
2. 价格走势与K线形态（含涨跌停分析，A股特色）
3. 技术指标解读（MACD/RSI/KDJ/BOLL 逐项分析）
4. 均线系统与趋势（多周期共振）
5. 支撑阻力位
6. 量价关系
7. 资金面分析（A股特色：主力资金+北向资金）
8. 龙虎榜与大单（A股特色）
9. 技术面深度解读（LLM 分析师章节）
10. 风险提示

与 stock_analysis_report.py 的关系：
- 复用其评分/指标提取/格式化辅助方法
- 独立渲染技术面相关章节，不包含基本面章节
"""
import os
import html as _html
import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Any
from datetime import datetime
from jinja2 import Template

# 复用合并报告的基类方法（评分/格式化/指标提取）
from .stock_analysis_report import StockAnalysisReportGenerator
from .common_components import build_nav_bar_css, build_nav_bar_html, build_footer_html, render_page

logger = logging.getLogger("technical_report")

class TechnicalReportGenerator(StockAnalysisReportGenerator):
    """技术面深度分析报告生成器（独立报告）"""

    def generate(self,
                 stock_code: str,
                 stock_name: str,
                 ohlcv_data: pd.DataFrame,
                 technical_indicators: Dict,
                 pattern_results: Dict,
                 support_resistance: Dict,
                 multi_timeframe: Dict,
                 capital_flow: Optional[Dict] = None,
                 dragon_tiger: Optional[Dict] = None,
                 output_path: str = None,
                 llm_prompts: Optional[Dict] = None) -> str:
        """
        生成技术面深度分析报告

        参数:
            stock_code: 股票代码
            stock_name: 股票名称
            ohlcv_data: OHLCV数据
            technical_indicators: 技术指标数据 (MACD, RSI, KDJ, MA等)
            pattern_results: K线形态识别结果
            support_resistance: 支撑阻力位
            multi_timeframe: 多周期分析结果
            capital_flow: 资金面数据（主力资金流向、北向资金）A 股特色
            dragon_tiger: 龙虎榜数据（机构席位、营业部动向）A 股特色
            output_path: 输出文件路径
            llm_prompts: LLM 分析师 prompt

        返回:
            HTML 报告文件路径
        """
        logger.info(f"开始生成 {stock_name}({stock_code}) 技术面深度分析报告")

        # 防御性初始化
        technical_indicators = technical_indicators or {}
        pattern_results = pattern_results or {}
        support_resistance = support_resistance or {}
        multi_timeframe = multi_timeframe or {}
        capital_flow = capital_flow or {}
        dragon_tiger = dragon_tiger or {}

        # 注入量价指标
        enriched_indicators: Dict[str, Any] = dict(technical_indicators)
        volume_metrics = self._compute_volume_metrics(ohlcv_data)
        enriched_indicators.update(volume_metrics)

        # 技术面评分（满分100）
        trend_score = self._score_trend(multi_timeframe, enriched_indicators)
        indicator_score = self._score_indicator_signals(enriched_indicators, multi_timeframe)
        pattern_score = self._score_patterns(pattern_results)
        volume_score = self._score_volume_price(enriched_indicators)

        technical_total = float(min(100.0, max(0.0,
            trend_score + indicator_score + pattern_score + volume_score
        )))
        rating = self._rating(technical_total)

        scores = {
            "technical": round(technical_total, 1),
            "rating": rating,
            "breakdown": {
                "trend": round(trend_score, 1),
                "indicator": round(indicator_score, 1),
                "pattern": round(pattern_score, 1),
                "volume": round(volume_score, 1),
            },
        }

        # 风险提示（仅技术面维度）
        risk_warnings = self._generate_risk_warnings(
            enriched_indicators, pattern_results, multi_timeframe, support_resistance
        )

        # K线联动图（TradingView lightweight-charts）
        kline_chart_html = self._safe_render_chart(
            self.kline_gen, "generate_tradingview_chart",
            ohlcv_data, stock_code=stock_code, stock_name=stock_name,
            show_support_resistance=False,
            fallback="K线图暂不可用"
        )

        data_date = self._get_data_date(ohlcv_data)
        current_price = float(ohlcv_data.iloc[-1]['close']) if len(ohlcv_data) > 0 else 0.0

        # 涨跌停分析（A股特色）
        limit_analysis = self._analyze_price_limit(ohlcv_data)

        context = {
            "stock_code": _html.escape(str(stock_code)),
            "stock_name": _html.escape(str(stock_name)),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": data_date,
            "current_price": current_price,
            "scores": scores,
            "multi_timeframe": multi_timeframe,
            "technical_indicators": enriched_indicators,
            "pattern_results": pattern_results,
            "support_resistance": support_resistance,
            "risk_warnings": risk_warnings,
            "kline_chart_html": kline_chart_html,
            "trend_color": self._TREND_COLOR,
            "strength_color": self._STRENGTH_COLOR,
            "divergences": multi_timeframe.get("divergences", []),
            "resonance": multi_timeframe.get("resonance", {}),
            "tf_summary": multi_timeframe.get("summary", ""),
            "capital_flow": capital_flow,
            "dragon_tiger": dragon_tiger,
            "limit_analysis": limit_analysis,
            "has_llm_prompts": bool(llm_prompts),
        }

        html_content = self._render_html(context)

        if output_path:
            output_dir = os.path.dirname(os.path.abspath(output_path))
            os.makedirs(output_dir, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"技术面报告已保存: {output_path}")
            return output_path
        return html_content

    # ================================================================
    # A股特色：涨跌停分析
    # ================================================================

    def _analyze_price_limit(self, ohlcv_data: pd.DataFrame) -> Dict:
        """分析近期涨跌停情况（A股特色，ST股5%、主板10%、创业板/科创板20%）"""
        result = {
            "has_limit_data": False,
            "recent_limit_ups": 0,
            "recent_limit_downs": 0,
            "limit_type": "10%",  # 默认主板
            "near_limit": False,
        }
        if ohlcv_data is None or len(ohlcv_data) < 2:
            return result

        try:
            df = ohlcv_data.copy()
            if "date" in df.columns:
                df = df.sort_values("date").reset_index(drop=True)
            recent = df.tail(20).copy()

            # 判断涨跌停类型
            code = str(ohlcv_data["code"].iloc[0]) if "code" in ohlcv_data.columns else ""
            if code.startswith("300") or code.startswith("301") or code.startswith("688"):
                result["limit_type"] = "20%"
            elif "ST" in str(ohlcv_data.get("name", [""]).iloc[0] if "name" in ohlcv_data.columns else ""):
                result["limit_type"] = "5%"

            limit_pct = float(result["limit_type"].rstrip("%")) / 100.0

            # 统计近期涨跌停
            for _, row in recent.iterrows():
                if "pre_close" in row and row["pre_close"] and float(row["pre_close"]) > 0:
                    change = (float(row["close"]) - float(row["pre_close"])) / float(row["pre_close"])
                    if change >= limit_pct - 0.001:
                        result["recent_limit_ups"] += 1
                    elif change <= -limit_pct + 0.001:
                        result["recent_limit_downs"] += 1

            # 是否接近涨停（最近一日涨幅 > 8%）
            if len(recent) > 0:
                last = recent.iloc[-1]
                if "pre_close" in last and last["pre_close"] and float(last["pre_close"]) > 0:
                    last_change = (float(last["close"]) - float(last["pre_close"])) / float(last["pre_close"])
                    result["near_limit"] = last_change >= limit_pct - 0.02

            result["has_limit_data"] = True
        except Exception as e:
            logger.debug(f"涨跌停分析失败: {e}")

        return result

    # ================================================================
    # HTML 渲染（9章节技术面报告）
    # ================================================================

    def _render_html(self, context: Dict) -> str:
        """渲染报告（统一骨架 base.html.j2，阶段一）

        说明：
        - base.html.j2 提供统一页面骨架（<!DOCTYPE html>/<head>/<body>/nav/footer）；
        - head_css 由 .j2 模板自身携带完整报告样式，base_css 置空以避免与报告
          专属 CSS 冲突（各报告样式系统独立且更丰富）；
        - 导航栏样式由 nav_css（build_nav_bar_css）在 head_css 中注入。
        """
        render_context = dict(context)
        render_context["_fmt_num"] = self._fmt_num
        render_context["_fmt_price"] = self._fmt_price
        render_context["_fmt_pct"] = self._fmt_pct
        render_context["_fmt_date"] = self._fmt_date
        render_context["_fmt_market_cap"] = self._fmt_market_cap
        render_context["_ind_val"] = self._tmpl_ind_val
        return render_page(
            'technical.html.j2',
            base_css="",
            nav_css=build_nav_bar_css(),
            **render_context,
        )
