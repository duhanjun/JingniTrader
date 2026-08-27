"""
报告模板公共计算引擎

根据模板配置（YAML）从 factor_data.parquet 和 price_data.parquet 计算技术/
基本面报告所需数据（渲染各章节 HTML 片段 + LLM prompt），返回渲染 context。
不组装完整 HTML、不写文件——完整 HTML 组装由 technical_report /
fundamental_report 插件（自带 .j2 模板）与 fallback_report 兜底插件承担。

对外入口：``compute_report_data(template_id, ctx)``（供上述插件复用）。

报告结构 = 固定章节(行情数据 + 深度解读 + 风险提示) + 因子组合章节(N个)

数据流向：
- 行情数据章节：直接读 price_data.parquet（K线图）
- 因子组合章节：读 factor_data.parquet，调用渲染器生成 HTML
- 深度解读章节：基于模板因子生成 LLM prompt，留占位符待注入
- 风险提示章节：从因子异常值自动提取风险信号
"""
import os
import sys
import logging
import importlib.util
from typing import Dict, Any, List, Optional
from datetime import datetime

import pandas as pd

logger = logging.getLogger("template_engine")


# 模板配置目录
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "config")


def _load_yaml_config(template_id: str) -> Optional[Dict[str, Any]]:
    """加载模板 YAML 配置"""
    yaml_path = os.path.join(_TEMPLATE_DIR, f"{template_id}.yaml")
    if not os.path.exists(yaml_path):
        logger.error(f"模板配置不存在: {yaml_path}")
        return None
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        logger.warning("PyYAML 未安装，尝试简单解析")
        return _parse_simple_yaml(yaml_path)
    except Exception as e:
        logger.error(f"加载模板配置失败: {e}")
        return None


def _parse_simple_yaml(yaml_path: str) -> Dict[str, Any]:
    """无 PyYAML 时的简单 YAML 解析（仅支持本模板格式）"""
    config: Dict[str, Any] = {"factor_groups": []}
    current_group: Optional[Dict[str, Any]] = None
    section = None

    with open(yaml_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if not line.startswith(" ") and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if val:
                    if key in ("template_id", "template_name", "analyst_type"):
                        config[key] = val
                    else:
                        config[key] = val
                else:
                    if key == "fixed_sections":
                        section = "fixed"
                        config["fixed_sections"] = {}
                    elif key == "factor_groups":
                        section = "groups"
                current_group = None
            elif line.startswith("  - "):
                # 新因子分组
                if section == "groups":
                    current_group = {}
                    config["factor_groups"].append(current_group)
                    item = stripped[2:].strip()
                    if ":" in item:
                        k, _, v = item.partition(":")
                        current_group[k.strip()] = v.strip().strip('"')
            elif line.startswith("    ") and current_group is not None:
                item = stripped
                if ":" in item:
                    k, _, v = item.partition(":")
                    k = k.strip()
                    v = v.strip().strip('"')
                    if k == "factors":
                        current_group[k] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
                    else:
                        current_group[k] = v
            elif line.startswith("    ") and section == "fixed":
                # fixed_sections 子项
                pass

    return config


def compute_report_data(template_id: str, ctx) -> Dict[str, Any]:
    """（公共）计算技术/基本面报告所需数据，返回渲染 context。

    供 technical_report / fundamental_report 插件自包含渲染复用：
    读取 DATA/FACTOR → 选股 → 渲染行情/因子/深度/风险章节。
    返回 dict 含组装 HTML 所需的全部变量 + llm_prompt，不组装 HTML、不写文件。

    返回 dict 结构：
        {template_id, template_name, analyst_type, stock_code, stock_name,
         current_price, data_date, market_html, factor_sections_html,
         deep_html, risk_html, llm_prompt, config, success, error}
    """
    engine = ComputeReportDataEngine()
    return engine._compute_report_data(template_id, ctx)


class ComputeReportDataEngine:
    """报告数据计算引擎（只做计算，不组装 HTML、不写文件）"""

    def __init__(self):
        self._kline_chart_module = None

    # ------------------------------------------------------------------
    # 公共计算（供技术/基本面插件自包含渲染复用，阶段 A）
    # ------------------------------------------------------------------

    def _compute_report_data(self, template_id: str, ctx) -> Dict[str, Any]:
        """计算技术/基本面报告所需数据，返回渲染 context（不组装 HTML、不写文件）。

        计算编排：加载配置 → 读 DATA/FACTOR → 选股 →
        渲染行情/因子/深度/风险章节 → 返回组装 HTML 所需全部变量。

        返回 dict 结构：
            {template_id, template_name, analyst_type, stock_code, stock_name,
             current_price, data_date, market_html, factor_sections_html,
             deep_html, risk_html, llm_prompt, config, success, error}
        """
        config = _load_yaml_config(template_id)
        if not config:
            return {"success": False, "error": f"模板 {template_id} 加载失败"}

        template_name = config.get("template_name", template_id)
        analyst_type = config.get("analyst_type", template_id)
        logger.info(f"开始计算报告数据: {template_name} (template_id={template_id})")

        # 读取数据
        data_path = ctx.get_artifact("DATA") if hasattr(ctx, 'get_artifact') else None
        factor_path = ctx.get_artifact("FACTOR") if hasattr(ctx, 'get_artifact') else None

        if not data_path or not os.path.exists(data_path):
            return {"success": False, "error": "缺少 DATA 产物"}

        price_data = pd.read_parquet(data_path)
        if price_data.empty:
            return {"success": False, "error": "行情数据为空"}

        factor_data = pd.DataFrame()
        if factor_path and os.path.exists(factor_path):
            try:
                factor_data = pd.read_parquet(factor_path)
            except Exception as e:
                logger.warning(f"读取因子数据失败: {e}")

        # 优先使用 ctx.stock_pool 指定的标的
        stock_code = ""
        if hasattr(ctx, 'stock_pool') and getattr(ctx, 'stock_pool', None):
            for code in ctx.stock_pool:
                if 'code' in price_data.columns and code in price_data['code'].astype(str).values:
                    stock_code = str(code)
                    break
        if not stock_code:
            stock_code = str(price_data['code'].iloc[0]) if 'code' in price_data.columns else ""
        stock_name = stock_code
        ohlcv = price_data[price_data['code'] == stock_code].sort_values('date') if stock_code else price_data
        current_price = float(ohlcv.iloc[-1]['close']) if len(ohlcv) > 0 else 0.0
        data_date = str(ohlcv.iloc[-1]['date'])[:10] if len(ohlcv) > 0 else ""

        # 渲染固定章节
        fixed_cfg = config.get("fixed_sections", {})
        market_html = self._render_market_data(fixed_cfg.get("market_data", {}), ohlcv, stock_code)

        factor_sections_html = ""
        all_factor_values_for_llm: Dict[str, Any] = {}
        for group_cfg in config.get("factor_groups", []):
            section_html, factor_values = self._render_factor_group(group_cfg, factor_data, stock_code)
            factor_sections_html += section_html
            all_factor_values_for_llm.update(factor_values)

        llm_prompt = self._prepare_llm_prompt(analyst_type, stock_code, stock_name,
                                               current_price, data_date, all_factor_values_for_llm,
                                               template_config=config)
        deep_html = f'''
<div class="section" id="deep_analysis">
    <h2>深度解读</h2>
    <!--LLM_{"TECHNICAL" if analyst_type == "technical" else "FUNDAMENTAL"}_ANALYSIS_PLACEHOLDER-->
</div>'''

        risk_html = self._render_risk_warning(fixed_cfg.get("risk_warning", {}), all_factor_values_for_llm)

        return {
            "success": True,
            "error": "",
            "template_id": template_id,
            "template_name": template_name,
            "analyst_type": analyst_type,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "current_price": current_price,
            "data_date": data_date,
            "market_html": market_html,
            "factor_sections_html": factor_sections_html,
            "deep_html": deep_html,
            "risk_html": risk_html,
            "llm_prompt": llm_prompt,
            "config": config,
        }

    # ------------------------------------------------------------------
    # 固定章节渲染
    # ------------------------------------------------------------------

    def _render_market_data(self, market_cfg: Dict, ohlcv: pd.DataFrame, stock_code: str) -> str:
        """渲染行情数据章节（K线图）"""
        kline_html = ""
        try:
            kline_html = self._render_kline_chart(ohlcv, market_cfg)
        except Exception as e:
            logger.warning(f"K线图渲染失败: {e}")
            kline_html = f'<p class="no-data">K线图渲染失败: {e}</p>'

        return f'''
<div class="section" id="market_data">
    <h2>行情数据</h2>
    <div class="chart-container">{kline_html}</div>
</div>'''

    def _render_kline_chart(self, ohlcv: pd.DataFrame, market_cfg: Dict) -> str:
        """调用现有 kline_chart 模块渲染 K线图"""
        if self._kline_chart_module is None:
            self._kline_chart_module = self._load_kline_module()
        if self._kline_chart_module is None:
            return '<p class="no-data">K线图模块不可用</p>'

        ma_periods = market_cfg.get("ma_periods", [5, 10, 20, 60])
        show_sr = market_cfg.get("show_support_resistance", False)

        try:
            gen = self._kline_chart_module.KlineChartGenerator()
            return gen.generate_tradingview_chart(
                ohlcv,
                ma_periods=ma_periods,
                show_support_resistance=show_sr,
            )
        except Exception as e:
            logger.warning(f"K线图渲染异常: {e}")
            return f'<p class="no-data">K线图异常: {e}</p>'

    def _load_kline_module(self):
        """加载 kline_chart 模块"""
        import importlib.util
        kline_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts", "kline_chart.py")
        if not os.path.exists(kline_path):
            logger.warning(f"kline_chart.py 不存在: {kline_path}")
            return None
        try:
            spec = importlib.util.spec_from_file_location("kline_chart", kline_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception as e:
            logger.warning(f"加载 kline_chart 模块失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 因子组合章节渲染
    # ------------------------------------------------------------------

    # 因子名别名映射：模板声明的因子名 → 数据 schema 实际列名
    # 解决模板（如 fundamental.yaml 用 roe_ttm）与数据 schema（用 roe）错位问题
    FACTOR_NAME_ALIASES = {
        # 盈利能力
        'roe_ttm': 'roe',
        'roe_avg': 'roe',
        'roa_ttm': 'roa',
        'gross_profit_margin': 'gross_margin',
        'net_profit_margin': 'net_margin',
        # 成长性
        'revenue_growth_yoy': 'revenue_growth',
        'profit_growth_yoy': 'profit_growth',
        'net_profit_growth_yoy': 'profit_growth',
        # 偿债能力
        'debt_to_asset': 'debt_ratio',
        'current': 'current_ratio',
        'quick': 'quick_ratio',
        # 估值
        'pe': 'pe_ttm',
        'pb_ratio': 'pb',
        'ps': 'ps_ttm',
        'dividend_yield': 'dv_ratio',
        # 现金流
        'ocf_per_share': 'ocf',
        'operating_cash_flow': 'ocf',
    }

    def _resolve_factor_name(self, factor_name: str, available_columns) -> str:
        """将模板因子名解析为数据中实际存在的列名。
        优先精确匹配，其次查别名表。返回 None 表示无匹配。"""
        # 1. 精确匹配
        if factor_name in available_columns:
            return factor_name
        # 2. 别名匹配
        alias = self.FACTOR_NAME_ALIASES.get(factor_name)
        if alias and alias in available_columns:
            return alias
        # 3. 反向匹配（数据列名是别名，模板用原名）
        for template_name, data_name in self.FACTOR_NAME_ALIASES.items():
            if data_name == factor_name and template_name in available_columns:
                return template_name
        return None

    def _render_factor_group(
        self,
        group_cfg: Dict,
        factor_data: pd.DataFrame,
        stock_code: str,
    ) -> tuple:
        """渲染一个因子分组，返回 (html, factor_values_dict)"""
        from scripts.renderers.registry import render_factor_group

        group_id = group_cfg.get("id", "")
        title = group_cfg.get("title", "")
        factors = group_cfg.get("factors", [])
        render_as = group_cfg.get("render_as", "metric_grid")
        hint = group_cfg.get("analysis_hint", "")

        if factor_data.empty:
            html = f'<div class="section" id="{group_id}"><h2>{title}</h2><p class="no-data">暂无因子数据</p></div>'
            return html, {}

        # 提取该股票的因子数据（取最新行）
        stock_factors = factor_data
        if 'code' in factor_data.columns and stock_code:
            stock_factors = factor_data[factor_data['code'] == stock_code]
        if 'date' in stock_factors.columns and len(stock_factors) > 0:
            stock_factors = stock_factors.sort_values('date').iloc[-1:]

        # 只保留配置中指定的因子列（含别名解析）
        available_factors = []
        factor_name_mapping = {}  # 模板名 -> 实际列名
        for f in factors:
            resolved = self._resolve_factor_name(f, stock_factors.columns)
            if resolved:
                available_factors.append(f)
                factor_name_mapping[f] = resolved

        if not available_factors:
            html = f'<div class="section" id="{group_id}"><h2>{title}</h2><p class="no-data">暂无数据</p></div>'
            return html, {}

        factor_values = {}
        for f in available_factors:
            actual_col = factor_name_mapping[f]
            if actual_col in stock_factors.columns:
                val = stock_factors[actual_col].iloc[0] if len(stock_factors) > 0 else None
                factor_values[f] = val

        html = render_factor_group(group_cfg, factor_data, stock_code)
        return html, factor_values

    # ------------------------------------------------------------------
    # 深度解读 LLM prompt 准备
    # ------------------------------------------------------------------

    def _prepare_llm_prompt(
        self,
        analyst_type: str,
        stock_code: str,
        stock_name: str,
        current_price: float,
        data_date: str,
        factor_values: Dict[str, Any],
        template_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """基于模板因子生成 LLM prompt（动态加载 factor_groups）

        返回:
            dict: {"system_prompt": str, "user_prompt": str, "response_schema": dict}
        """
        try:
            from scripts.llm_analyst import TechnicalAnalyst, FundamentalsAnalyst
        except ImportError:
            logger.warning("llm_analyst 模块不可用")
            return {}

        # 提取 factor_groups（优先使用传入的 template_config）
        factor_groups = None
        if template_config:
            factor_groups = template_config.get("factor_groups", [])
        else:
            # 回退：从模板文件加载
            config = _load_yaml_config(analyst_type)
            if config:
                factor_groups = config.get("factor_groups", [])

        # 从 factor_values 构造上下文
        ctx_data = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "current_price": current_price,
            "data_date": data_date,
            "technical_indicators": factor_values,
            "fundamental_data": factor_values,
        }

        try:
            if analyst_type == "technical":
                return TechnicalAnalyst().prepare(ctx_data, factor_groups=factor_groups)
            else:
                return FundamentalsAnalyst().prepare(ctx_data, factor_groups=factor_groups)
        except Exception as e:
            logger.warning(f"LLM prompt 准备失败: {e}")
            return {}

    # ------------------------------------------------------------------
    # 风险提示章节
    # ------------------------------------------------------------------

    def _render_risk_warning(self, risk_cfg: Dict, factor_values: Dict[str, Any]) -> str:
        """从因子异常值提取风险信号"""
        risk_factors = risk_cfg.get("source_factors", [])
        risks: List[str] = []

        for name in risk_factors:
            val = factor_values.get(name)
            if val is None or (isinstance(val, float) and val != val):
                continue
            try:
                v = float(val)
            except (ValueError, TypeError):
                continue

            risk = self._check_risk(name, v)
            if risk:
                risks.append(risk)

        if not risks:
            risks.append("当前未检测到明显异常风险信号。")

        risk_items = "".join(f'<li>{r}</li>' for r in risks)
        return f'''
<div class="section" id="risk_warning">
    <h2>风险提示</h2>
    <ul class="risk-list">{risk_items}</ul>
</div>'''

    def _check_risk(self, factor_name: str, value: float) -> Optional[str]:
        """检查单个因子的风险信号"""
        if factor_name == "volatility_20d" and value > 0.05:
            return f"20日波动率 {value*100:.2f}% 超过阈值 5%，价格波动较大"
        if factor_name == "volume_ratio" and value > 2.0:
            return f"量比 {value:.2f} 偏高，存在异常放量"
        if factor_name == "money_flow_20d" and value < 0:
            return f"20日累计资金流为负 ({value:.2f})，资金持续流出"
        if factor_name == "debt_ratio" and value > 0.7:
            return f"资产负债率 {value*100:.1f}% 偏高，偿债压力较大"
        if factor_name == "revenue_growth_yoy" and value < 0:
            return f"营收同比增速 {value:.2f}% 为负，业绩下滑"
        if factor_name == "pe_ttm" and value > 100:
            return f"PE(TTM) {value:.1f} 倍偏高，估值风险"
        return None
