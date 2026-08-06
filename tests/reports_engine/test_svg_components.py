"""SVG 组件模块单元测试（L2）。

覆盖 renderers/svg_components.py 的所有组件：
- render_weight_deviation_bar: 双向条形图
- render_ring_chart: 环形图
- render_status_indicator: 状态指示灯
- render_progress_bar: 进度条
- render_metric_card: 指标卡片
- render_alert_item: 告警项

关键路径：颜色色阶、数据缺失容错、SVG 结构正确性
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_SCRIPTS = os.path.join(ROOT, "skills", "reports-engine", "scripts")


def _load_svg_module():
    """加载 svg_components 模块（处理 scripts 包切换）。"""
    import importlib.util as ilu

    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    init_py = os.path.join(REPORTS_SCRIPTS, "__init__.py")
    if os.path.exists(init_py):
        spec = ilu.spec_from_file_location(
            "scripts", init_py,
            submodule_search_locations=[REPORTS_SCRIPTS],
        )
        pkg = ilu.module_from_spec(spec)
        sys.modules["scripts"] = pkg
        spec.loader.exec_module(pkg)

    svg_path = os.path.join(REPORTS_SCRIPTS, "renderers", "svg_components.py")
    spec = ilu.spec_from_file_location("scripts.renderers.svg_components", svg_path)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.renderers.svg_components"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestRenderWeightDeviationBar:
    """双向条形图测试。"""

    def test_empty_weights_returns_placeholder(self):
        """空权重字典应返回占位符，不抛异常。"""
        mod = _load_svg_module()
        result = mod.render_weight_deviation_bar({})
        assert "无权重数据" in result

    def test_normal_weights_returns_svg(self):
        """正常权重应返回 SVG 字符串。"""
        mod = _load_svg_module()
        weights = {"000001.SZ": 0.3, "600000.SH": 0.4, "000002.SZ": 0.3}
        result = mod.render_weight_deviation_bar(weights)
        assert "<svg" in result
        assert "000001.SZ" in result
        assert "600000.SH" in result

    def test_overweight_uses_red_color(self):
        """超配应使用红色（A 股习惯：红涨）。"""
        mod = _load_svg_module()
        weights = {"000001.SZ": 0.5, "600000.SH": 0.5}
        result = mod.render_weight_deviation_bar(weights)
        # 红色 hex
        assert mod.COLORS["up"] in result

    def test_underweight_uses_green_color(self):
        """低配应使用绿色（A 股习惯：绿跌）。"""
        mod = _load_svg_module()
        weights = {"000001.SZ": 0.1, "600000.SH": 0.9}
        result = mod.render_weight_deviation_bar(weights)
        assert mod.COLORS["down"] in result

    def test_max_items_limit(self):
        """超过 max_items 应截断。"""
        mod = _load_svg_module()
        weights = {f"00000{i}.SZ": 0.1 for i in range(10)}
        result = mod.render_weight_deviation_bar(weights, max_items=3)
        # 应只显示 3 个标的
        assert result.count("<g>") <= 3 + 1  # 图例不算


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestRenderRingChart:
    """环形图测试。"""

    def test_zero_ratio_uses_success_color(self):
        """比值为 0 时应使用绿色（正常）。"""
        mod = _load_svg_module()
        result = mod.render_ring_chart(value=0, threshold=1.0, label="0%")
        assert mod.COLORS["success"] in result
        assert "<svg" in result

    def test_over_threshold_uses_danger_color(self):
        """超过阈值应使用红色（超限）。"""
        mod = _load_svg_module()
        result = mod.render_ring_chart(value=1.5, threshold=1.0, label="150%")
        assert mod.COLORS["danger"] in result

    def test_near_threshold_uses_warning_color(self):
        """接近阈值（80%）应使用橙色（警告）。"""
        mod = _load_svg_module()
        result = mod.render_ring_chart(value=0.85, threshold=1.0, label="85%")
        assert mod.COLORS["warning"] in result

    def test_color_override(self):
        """color_override 应覆盖自动色阶。"""
        mod = _load_svg_module()
        result = mod.render_ring_chart(
            value=0.5, threshold=1.0, color_override="#abcdef"
        )
        assert "#abcdef" in result

    def test_zero_threshold_returns_zero_ratio(self):
        """threshold=0 时比例应为 0，不抛异常。"""
        mod = _load_svg_module()
        result = mod.render_ring_chart(value=0.5, threshold=0)
        assert "<svg" in result


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestRenderStatusIndicator:
    """状态指示灯测试。"""

    def test_connected_status_shows_pulse(self):
        """connected 状态应显示脉冲动画。"""
        mod = _load_svg_module()
        result = mod.render_status_indicator(status="connected", label="LIVE")
        assert mod.COLORS["success"] in result
        assert "status-dot-pulse" in result
        assert "LIVE" in result

    def test_disconnected_status_no_pulse(self):
        """disconnected 状态不应显示脉冲。"""
        mod = _load_svg_module()
        result = mod.render_status_indicator(status="disconnected", label="断开")
        assert mod.COLORS["danger"] in result
        assert "status-dot-pulse" not in result

    def test_disabled_status_uses_muted_color(self):
        """disabled 状态应使用灰色。"""
        mod = _load_svg_module()
        result = mod.render_status_indicator(status="disabled", label="PAPER")
        assert mod.COLORS["muted"] in result
        assert "PAPER" in result

    def test_unknown_status_falls_back_to_muted(self):
        """未知状态应回退到灰色。"""
        mod = _load_svg_module()
        result = mod.render_status_indicator(status="unknown")
        assert mod.COLORS["muted"] in result


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestRenderProgressBar:
    """进度条测试。"""

    def test_normal_progress(self):
        """正常进度应返回 SVG。"""
        mod = _load_svg_module()
        result = mod.render_progress_bar(value=0.5, total=1.0, label="成交率")
        assert "<svg" in result
        assert "成交率" in result
        assert "50.0%" in result

    def test_full_progress_uses_danger_color(self):
        """100% 进度应使用红色（满仓告警）。"""
        mod = _load_svg_module()
        result = mod.render_progress_bar(value=1.0, total=1.0)
        assert mod.COLORS["danger"] in result

    def test_zero_total_returns_zero_ratio(self):
        """total=0 时比例应为 0，不抛异常。"""
        mod = _load_svg_module()
        result = mod.render_progress_bar(value=0.5, total=0)
        assert "<svg" in result


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestRenderMetricCard:
    """指标卡片测试。"""

    def test_basic_card(self):
        """基本卡片应包含标题和数值。"""
        mod = _load_svg_module()
        result = mod.render_metric_card(title="账户净值", value="100,000.00")
        assert "账户净值" in result
        assert "100,000.00" in result

    def test_card_with_subvalue(self):
        """带副数值的卡片。"""
        mod = _load_svg_module()
        result = mod.render_metric_card(
            title="账户净值", value="100,000.00", subvalue="较昨日 +1.5%"
        )
        assert "较昨日" in result

    def test_card_with_custom_color(self):
        """自定义颜色应生效。"""
        mod = _load_svg_module()
        result = mod.render_metric_card(
            title="测试", value="123", color="#ff0000"
        )
        assert "#ff0000" in result


@pytest.mark.skill_reports_engine
@pytest.mark.unit
class TestRenderAlertItem:
    """告警项测试。"""

    def test_od_severity_uses_danger_color(self):
        """od 严重度应使用红色。"""
        mod = _load_svg_module()
        result = mod.render_alert_item(
            severity="od", title="止损触发", detail="日亏损 -3%"
        )
        assert mod.COLORS["danger"] in result
        assert "止损触发" in result

    def test_wr_severity_uses_warning_color(self):
        """wr 严重度应使用橙色。"""
        mod = _load_svg_module()
        result = mod.render_alert_item(severity="wr", title="接近阈值")
        assert mod.COLORS["warning"] in result

    def test_ok_severity_uses_success_color(self):
        """ok 严重度应使用绿色。"""
        mod = _load_svg_module()
        result = mod.render_alert_item(severity="ok", title="正常")
        assert mod.COLORS["success"] in result

    def test_with_suggestion(self):
        """带建议操作的告警项。"""
        mod = _load_svg_module()
        result = mod.render_alert_item(
            severity="od", title="止损", suggestion="暂停下单"
        )
        assert "建议" in result
        assert "暂停下单" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
