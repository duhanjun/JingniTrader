"""
基本面深度分析报告生成器（独立报告，借鉴 TradingAgents fundamentals_report 设计）

10 个章节：
1. 报告概览（基本面评分仪表盘）
2. 公司概况与业务分析
3. 行业分析与景气度
4. 财务报表分析（利润/资产负债/现金流）
5. 盈利能力分析（ROE/毛利率/净利率）
6. 成长性分析（营收/利润增速）
7. 估值分析（PE/PB/PS 分位）
8. 股东结构与资本运作（A股特色：十大股东/解禁/回购）
9. 基本面深度解读（LLM 分析师章节）
10. 风险提示

与 stock_analysis_report.py 的关系：
- 复用其估值/盈利/成长评分方法
- 独立渲染基本面相关章节，不包含技术面章节
"""
import os
import html as _html
import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Any
from datetime import datetime
from jinja2 import Template

# 复用合并报告的基类方法（评分/格式化/基本面提取）
from .stock_analysis_report import StockAnalysisReportGenerator
from .common_components import build_nav_bar_css, build_nav_bar_html, build_footer_html, render_page

logger = logging.getLogger("fundamental_report")

class FundamentalReportGenerator(StockAnalysisReportGenerator):
    """基本面深度分析报告生成器（独立报告）"""

    def generate(self,
                 stock_code: str,
                 stock_name: str,
                 fundamental_data: Dict,
                 ohlcv_data: Optional[pd.DataFrame] = None,
                 industry_data: Optional[Dict] = None,
                 shareholder_data: Optional[Dict] = None,
                 output_path: str = None,
                 llm_prompts: Optional[Dict] = None) -> str:
        """
        生成基本面深度分析报告

        参数:
            stock_code: 股票代码
            stock_name: 股票名称
            fundamental_data: 基本面数据（财务指标、估值指标等）
            ohlcv_data: OHLCV 数据（可选，用于提取当前价/市值）
            industry_data: 行业数据（行业景气度、竞争格局等）
            shareholder_data: 股东结构数据（A股特色：十大股东、解禁、回购）
            output_path: 输出文件路径
            llm_prompts: LLM 分析师 prompt

        返回:
            HTML 报告文件路径
        """
        logger.info(f"开始生成 {stock_name}({stock_code}) 基本面深度分析报告")

        # 防御性初始化
        fundamental_data = fundamental_data or {}
        industry_data = industry_data or {}
        shareholder_data = shareholder_data or {}

        # 基本面评分（满分100）
        valuation_score = self._score_valuation(fundamental_data)
        profitability_score = self._score_profitability(fundamental_data)
        growth_score = self._score_growth(fundamental_data)
        fundamental_total = float(min(100.0, max(0.0,
            valuation_score + profitability_score + growth_score
        )))
        rating = self._rating(fundamental_total)

        scores = {
            "fundamental": round(fundamental_total, 1),
            "rating": rating,
            "breakdown": {
                "valuation": round(valuation_score, 1),
                "profitability": round(profitability_score, 1),
                "growth": round(growth_score, 1),
            },
        }

        # 估值水平判定
        valuation_level = self._judge_valuation_level(fundamental_data)

        # 风险提示（基本面维度）
        risk_warnings = self._generate_fundamental_risk_warnings(
            fundamental_data, shareholder_data, industry_data
        )

        # K线行情图（TradingView lightweight-charts，仅保留基本K线和均线）
        kline_chart_html = ""
        if ohlcv_data is not None and len(ohlcv_data) > 0:
            kline_chart_html = self._safe_render_chart(
                self.kline_gen, "generate_tradingview_chart",
                ohlcv_data, stock_code=stock_code, stock_name=stock_name,
                show_support_resistance=False,
                fallback="K线图暂不可用"
            )

        # 当前价/市值
        current_price = 0.0
        market_cap = self._get_fundamental(fundamental_data, "market_cap", "total_market_cap", "总市值")
        if ohlcv_data is not None and len(ohlcv_data) > 0:
            try:
                current_price = float(ohlcv_data.iloc[-1]['close'])
            except Exception:
                pass

        data_date = self._get_data_date(ohlcv_data) if ohlcv_data is not None else "—"

        # 派生常用财务指标
        fin = self._extract_financial_metrics(fundamental_data)

        context = {
            "stock_code": _html.escape(str(stock_code)),
            "stock_name": _html.escape(str(stock_name)),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": data_date,
            "current_price": current_price,
            "market_cap": market_cap,
            "scores": scores,
            "valuation_level": valuation_level,
            "fundamental_data": fundamental_data,
            "industry_data": industry_data,
            "shareholder_data": shareholder_data,
            "fin": fin,
            "risk_warnings": risk_warnings,
            "kline_chart_html": kline_chart_html,
            "has_llm_prompts": bool(llm_prompts),
        }

        html_content = self._render_html(context)

        if output_path:
            output_dir = os.path.dirname(os.path.abspath(output_path))
            os.makedirs(output_dir, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"基本面报告已保存: {output_path}")
            return output_path
        return html_content

    # ================================================================
    # 估值水平判定
    # ================================================================

    def _judge_valuation_level(self, fundamental_data: Dict) -> str:
        """根据 PE/PB 分位判定估值水平：低估/合理/偏高/高估"""
        if not fundamental_data:
            return "—"

        pe_pct = self._get_fundamental(fundamental_data, "pe_percentile", "pe_pct", "pe_quantile")
        pb_pct = self._get_fundamental(fundamental_data, "pb_percentile", "pb_pct", "pb_quantile")

        pcts = []
        for pct in [pe_pct, pb_pct]:
            if pct is not None and pd.notna(pct):
                try:
                    pcts.append(float(pct))
                except Exception:
                    pass

        if not pcts:
            return "—"

        avg_pct = float(np.mean(pcts))
        if avg_pct < 0.2:
            return "低估"
        elif avg_pct < 0.4:
            return "合理偏低"
        elif avg_pct < 0.6:
            return "合理"
        elif avg_pct < 0.8:
            return "偏高"
        else:
            return "高估"

    # ================================================================
    # 财务指标提取（用于模板渲染）
    # ================================================================

    def _extract_financial_metrics(self, fundamental_data: Dict) -> Dict[str, Any]:
        """从 fundamental_data 中提取并归类财务指标，便于模板渲染"""
        fd = fundamental_data or {}
        return {
            # 估值类
            "pe_ttm": self._get_fundamental(fd, "pe_ttm", "pe", "pe_ratio"),
            "pb": self._get_fundamental(fd, "pb", "pb_ratio"),
            "ps_ttm": self._get_fundamental(fd, "ps_ttm", "ps", "ps_ratio"),
            "pe_percentile": self._get_fundamental(fd, "pe_percentile", "pe_pct", "pe_quantile"),
            "pb_percentile": self._get_fundamental(fd, "pb_percentile", "pb_pct", "pb_quantile"),
            "dv_ratio": self._get_fundamental(fd, "dv_ratio", "dividend_yield", "股息率"),
            # 盈利能力类
            "roe": self._get_fundamental(fd, "roe", "roe_ttm", "return_on_equity"),
            "roa": self._get_fundamental(fd, "roa", "return_on_assets"),
            "gross_margin": self._get_fundamental(fd, "gross_margin", "毛利率"),
            "net_margin": self._get_fundamental(fd, "net_margin", "净利率", "profit_margin"),
            # 成长性类
            "revenue_growth": self._get_fundamental(fd, "revenue_growth", "rev_growth", "营收增速"),
            "profit_growth": self._get_fundamental(fd, "profit_growth", "net_profit_growth", "利润增速"),
            # 规模类
            "revenue": self._get_fundamental(fd, "revenue", "total_revenue", "营业收入"),
            "net_profit": self._get_fundamental(fd, "net_profit", "净利润", "net_income"),
            "total_assets": self._get_fundamental(fd, "total_assets", "总资产"),
            "net_assets": self._get_fundamental(fd, "net_assets", "净资产", "shareholders_equity"),
            # 偿债能力
            "debt_ratio": self._get_fundamental(fd, "debt_ratio", "资产负债率"),
            "current_ratio": self._get_fundamental(fd, "current_ratio", "流动比率"),
            # 现金流
            "operating_cashflow": self._get_fundamental(fd, "operating_cashflow", "经营现金流", "cfo"),
            "free_cashflow": self._get_fundamental(fd, "free_cashflow", "fcf", "自由现金流"),
        }

    # ================================================================
    # 基本面风险提示
    # ================================================================

    def _generate_fundamental_risk_warnings(self,
                                            fundamental_data: Dict,
                                            shareholder_data: Dict,
                                            industry_data: Dict) -> List[str]:
        """生成基本面维度的风险提示"""
        warnings: List[str] = []
        fd = fundamental_data or {}
        sd = shareholder_data or {}
        idt = industry_data or {}

        # 1. 高估值风险
        pe_pct = self._get_fundamental(fd, "pe_percentile", "pe_pct", "pe_quantile")
        if pe_pct is not None and pd.notna(pe_pct):
            try:
                if float(pe_pct) > 0.8:
                    warnings.append(f"PE 处于历史 {float(pe_pct)*100:.0f}% 分位，估值偏高，回调风险较大")
                elif float(pe_pct) > 0.6:
                    warnings.append(f"PE 处于历史 {float(pe_pct)*100:.0f}% 分位，估值偏高")
            except Exception:
                pass

        # 2. 业绩下滑风险
        profit_growth = self._get_fundamental(fd, "profit_growth", "net_profit_growth", "利润增速")
        if profit_growth is not None and pd.notna(profit_growth):
            try:
                g = float(profit_growth)
                if abs(g) < 1:
                    g = g * 100
                if g < -10:
                    warnings.append(f"净利润同比下滑 {abs(g):.1f}%，业绩明显恶化")
                elif g < 0:
                    warnings.append(f"净利润同比下滑 {abs(g):.1f}%，需关注业绩可持续性")
            except Exception:
                pass

        # 3. 盈利能力恶化
        roe = self._get_fundamental(fd, "roe", "roe_ttm", "return_on_equity")
        if roe is not None and pd.notna(roe):
            try:
                r = float(roe)
                if abs(r) < 1:
                    r = r * 100
                if r < 0:
                    warnings.append(f"ROE 为 {r:.1f}%，处于亏损状态")
                elif r < 5:
                    warnings.append(f"ROE 仅 {r:.1f}%，盈利能力较弱")
            except Exception:
                pass

        # 4. 高负债风险
        debt_ratio = self._get_fundamental(fd, "debt_ratio", "资产负债率")
        if debt_ratio is not None and pd.notna(debt_ratio):
            try:
                d = float(debt_ratio)
                if abs(d) < 1:
                    d = d * 100
                if d > 70:
                    warnings.append(f"资产负债率 {d:.1f}%，负债水平较高，财务风险上升")
                elif d > 60:
                    warnings.append(f"资产负债率 {d:.1f}%，需关注偿债能力")
            except Exception:
                pass

        # 5. 解禁风险（A股特色）
        upcoming_unlock = sd.get("upcoming_unlock") if isinstance(sd, dict) else None
        if upcoming_unlock and isinstance(upcoming_unlock, dict):
            try:
                unlock_ratio = upcoming_unlock.get("unlock_ratio")
                if unlock_ratio and float(unlock_ratio) > 0.1:
                    warnings.append(
                        f"近期存在解禁，解禁比例约 {float(unlock_ratio)*100:.1f}%，"
                        f"解禁日期 {upcoming_unlock.get('unlock_date', '—')}"
                    )
            except Exception:
                pass

        # 6. 股东减持风险（A股特色）
        reduction = sd.get("shareholder_reduction") if isinstance(sd, dict) else None
        if reduction and isinstance(reduction, dict):
            try:
                red_pct = reduction.get("reduction_ratio")
                if red_pct and float(red_pct) > 0.02:
                    warnings.append(
                        f"大股东近期减持比例 {float(red_pct)*100:.2f}%，"
                        f"需关注管理层信心"
                    )
            except Exception:
                pass

        # 7. 行业景气度下行
        prosperity = idt.get("prosperity_trend") if isinstance(idt, dict) else None
        if prosperity == "down":
            warnings.append("行业景气度处于下行周期，整体需求走弱")

        if not warnings:
            warnings.append("未检测到明显基本面风险信号（基于现有数据）")

        return warnings

    # ================================================================
    # HTML 渲染（10章节基本面报告）
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
            'fundamental.html.j2',
            base_css="",
            nav_css=build_nav_bar_css(),
            **render_context,
        )
