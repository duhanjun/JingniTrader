"""westock 适配器 L2 单元测试 + L3 轻量连通性测试。

覆盖（REQ-2026-08-14 实施：接入腾讯公网免费数据源）：
- 多市场代码映射（A股 sh/sz、指数、港股 hk、美股 us 部分生效）
- 日K线解析（web.ifzq.gtimg.cn fqkline，列表的列表结构）
- 实时报价解析（qt.gtimg.cn，~ 分隔平铺串）
- 失败处理：网络异常/空返回 → 抛 NetworkError/DataNotFoundError（触发引擎降级）
- 轻限频护栏：批量标的上限 / 单标的最小间隔（mock 时间验证不阻塞）
- 架构契约：get_financial / get_stock_list 不提供 → 抛 DataNotFoundError / NotImplementedError
- L3 轻量实测：600519 为锚点，真实拉取近若干交易日收盘，校验 schema

设计：复用 test_quality_gate.py 的 scripts 包注册模式动态加载适配器模块，
避免 import 阶段因缺包崩溃。
"""

from __future__ import annotations

import os
import sys
import importlib.util as ilu
from unittest import mock

import pytest
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")


def _load_adapter_module():
    """加载 westock_adapter 为 scripts.adapters.westock_adapter。"""
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)

    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)

    adapter_path = os.path.join(SCRIPTS_DIR, "adapters", "westock_adapter.py")
    full_mod_name = "scripts.adapters.westock_adapter"
    spec = ilu.spec_from_file_location(full_mod_name, adapter_path)
    mod = ilu.module_from_spec(spec)
    sys.modules[full_mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_errors_module():
    # 复用适配器已加载的 scripts.errors 实例，保证异常类身份一致
    # （否则 pytest.raises 因类对象不同而无法捕获适配器抛出的异常）。
    existing = sys.modules.get("scripts.errors")
    if existing is not None:
        return existing
    for key in list(sys.modules.keys()):
        if key == "scripts" or key.startswith("scripts."):
            sys.modules.pop(key, None)
    init_py = os.path.join(SCRIPTS_DIR, "__init__.py")
    spec = ilu.spec_from_file_location("scripts", init_py, submodule_search_locations=[SCRIPTS_DIR])
    pkg = ilu.module_from_spec(spec)
    sys.modules["scripts"] = pkg
    spec.loader.exec_module(pkg)
    errors_path = os.path.join(SCRIPTS_DIR, "errors.py")
    spec = ilu.spec_from_file_location("scripts.errors", errors_path)
    mod = ilu.module_from_spec(spec)
    sys.modules["scripts.errors"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 模拟网络响应 ──────────────────────────────────────
KLINE_BODY = (
    '{"code":0,"msg":"","data":{"sh600519":{'
    '"qfqday":[["2026-08-11","1346.500","1346.500","1348.860","1343.000","24976.000"],'
    '["2026-08-12","1343.000","1343.000","1346.500","1341.000","27000.000"],'
    '["2026-08-13","1355.290","1355.290","1359.000","1355.000","29853.000"]]}}}'
)
QUOTE_BODY = 'v_sh600519="1~贵州茅台~600519~1341.99~1355.29~1355.00~29853~12760~17093~1341.98~1~1341.90~2~1341.69~1~1341.68~1~1341.62~3~1341.99~283~1342.00~23~1342.01~1~1342.02~2~1342.06~3~~20260814161443~-13.30~-0.98~1359.00~1338.14~1341.99/29853/4024065608~29853~402407~0.24~20.28~"'
EMPTY_BODY = '{"code":0,"msg":"","data":{}}'


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockCodeMapping:
    """多市场代码映射。"""

    def test_ashare_sz_suffix(self):
        mod = _load_adapter_module()
        code, market = mod._normalize_to_tencent_code("000001.SZ")
        assert code == "sz000001"
        assert market == "ashare"

    def test_ashare_sh_suffix(self):
        mod = _load_adapter_module()
        code, market = mod._normalize_to_tencent_code("600519.SH")
        assert code == "sh600519"
        assert market == "ashare"

    def test_bare_six_digit_sh_prefix(self):
        mod = _load_adapter_module()
        # 6 开头 → sh
        code, _ = mod._normalize_to_tencent_code("600519")
        assert code == "sh600519"
        # 0 开头 → sz
        code2, _ = mod._normalize_to_tencent_code("000001")
        assert code2 == "sz000001"

    def test_hk_mapping(self):
        mod = _load_adapter_module()
        code, market = mod._normalize_to_tencent_code("00700.HK")
        assert code == "hk00700"
        assert market == "hk"

    def test_us_mapping(self):
        mod = _load_adapter_module()
        code, market = mod._normalize_to_tencent_code("AAPL.US")
        assert code == "usAAPL"
        assert market == "us"

    def test_unsupported_exchange_raises(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        with pytest.raises(errs.InvalidParameterError):
            mod._normalize_to_tencent_code("1234.XY")


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockDailyParse:
    """日K线解析。"""

    def test_parse_kline_returns_valid_schema(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with mock.patch.object(adapter, "_http_get", return_value=KLINE_BODY):
            df = adapter.get_daily(["600519.SH"], "2026-08-01", "2026-08-31", adjust="qfq")
        assert not df.empty
        assert set(["date", "code", "open", "high", "low", "close", "volume"]).issubset(df.columns)
        assert (df["code"] == "600519.SH").all()
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        # 验证真实锚点：最新收盘 1355.29
        assert abs(df["close"].max() - 1355.29) < 1e-6
        # high >= low 逻辑
        assert (df["high"] >= df["low"]).all()

    def test_empty_data_raises_data_not_found(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        adapter = mod.WestockAdapter()
        with mock.patch.object(adapter, "_http_get", return_value=EMPTY_BODY):
            with pytest.raises(errs.DataNotFoundError):
                adapter.get_daily(["600519.SH"], "2026-08-01", "2026-08-31")

    def test_network_error_raises_network_error(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        adapter = mod.WestockAdapter()
        with mock.patch.object(
            adapter,
            "_http_get",
            side_effect=errs.NetworkError("tencent", "boom"),
        ):
            with pytest.raises(errs.NetworkError):
                adapter.get_daily(["600519.SH"], "2026-08-01", "2026-08-31")


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockRealtime:
    """实时报价解析。"""

    def test_realtime_quote_parse(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with mock.patch.object(adapter, "_http_get", return_value=QUOTE_BODY):
            q = adapter.get_realtime_quote("600519.SH")
        assert q["code"] == "600519.SH"
        assert abs(q["close"] - 1341.99) < 1e-6
        assert q["name"] == "贵州茅台"


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockUnsupportedTypes:
    """westock 适配器支持的数据类型契约（Phase 2/3 已补齐取数）。

    说明：原测试断言 westock 不提供 stock_list 等数据类型（当时为桩）。
    Phase 2/3 已通过 westock CLI 实测落地，故此处改为断言：
    - get_stock_list 已实现（无数据时抛 DataNotFoundError，不再 NotImplementedError）
    - SUPPORTED_DATA_TYPES 已扩展含 11 个 P0 叶子项键
    """

    def test_get_stock_list_implemented(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        # 已实现：调用不抛 NotImplementedError（无网络数据时抛 DataNotFoundError 由引擎降级）
        import scripts.errors as err

        try:
            adapter.get_stock_list()
        except Exception as e:  # noqa: BLE001
            assert not isinstance(e, NotImplementedError), "get_stock_list 已落地，不应再抛 NotImplementedError"
            assert isinstance(e, err.DataNotFoundError), f"无数据时应为 DataNotFoundError，实际 {type(e).__name__}"

    def test_supported_data_types_extended(self):
        mod = _load_adapter_module()
        expected = {
            "daily", "kline", "market_kline", "market_realtime",
            "financial", "financial_report",
            "capital_flow", "dragon_tiger", "shareholder",
            "basic_stock_list", "basic_stock_info", "basic_trade_calendar",
            "basic_adjust_factor",
            "ref_dividend", "ref_suspend_resume", "ref_locked_shares", "ref_forecast",
        }
        assert mod.WestockAdapter.SUPPORTED_DATA_TYPES == expected


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockWestockNodeMissing:
    """NodeMissingError 信号（REQ-2026-08-15 §5.3）：npx 缺失不触发数据源降级。"""

    def test_run_westock_no_npx_raises_node_missing(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        with mock.patch.object(mod.shutil, "which", return_value=None):
            with pytest.raises(errs.NodeMissingError):
                mod._run_westock(["finance", "sh600519"])

    def test_node_missing_not_in_fallback_set(self):
        errs = _load_errors_module()
        assert errs.NodeMissingError not in errs.FALLBACK_TRIGGERING_ERRORS

    def test_get_financial_no_npx_raises_node_missing(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        adapter = mod.WestockAdapter()
        with mock.patch.object(mod.shutil, "which", return_value=None):
            with pytest.raises(errs.NodeMissingError):
                adapter.get_financial(["600519.SH"], "20240930", [])


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockWestockOffline:
    """westock 四类方法离线单测（monkeypatch subprocess 返回固定 JSON 样本，验证字段归一化）。"""

    # 模拟 westock finance 输出（真实契约：{"sections":[income[], balance[], cashflow[]]}）
    # 仅含报表原始科目，无 pe/pb/roe 等衍生指标（与 westock-data-skillhub@1.0.5 一致）
    FINANCE_OUT = {
        "sections": [
            [
                {
                    "_date": "2023-12-31",
                    "code": "sh600519",
                    "date": "2023-12-31",
                    "NPParentCompanyOwnersTTM": "82715105749.37",
                    "OperatingRevenueTTM": "172146396849.52",
                    "GrossProfitTTM": "155794820628.72",
                }
            ],
            [
                {
                    "_date": "2023-12-31",
                    "code": "sh600519",
                    "TotalLiability": "38782958469.89",
                    "TotalAssets": "281135886435.69",
                    "TotalCurrentAssets": "233068933753.52",
                    "TotalCurrentLiability": "38455594988.88",
                    "SEWithoutMI": "242352927965.80",
                }
            ],
            [{"_date": "2023-12-31", "code": "sh600519", "NetOperateCashFlowTTM": "79622900612.10"}],
        ]
    }
    # 模拟 westock fund flow 输出（真实契约：flat list，字段 MainNetFlow/Jumbo/Block/Mid/Retail/Small）
    FUND_FLOW_OUT = [
        {
            "code": "sh600519",
            "name": "贵州茅台",
            "EndDate": "2024-03-01",
            "MainNetFlow": "123456789.00",
            "JumboNetFlow": "50000000.00",
            "BlockNetFlow": "30000000.00",
            "MidNetFlow": "-10000000.00",
            "RetailNetFlow": "-20000000.00",
            "SmallNetFlow": "-5000000.00",
        },
        {
            "code": "sh600519",
            "name": "贵州茅台",
            "EndDate": "2024-03-04",
            "MainNetFlow": "-50000000.00",
            "JumboNetFlow": "-30000000.00",
            "BlockNetFlow": "-10000000.00",
            "MidNetFlow": "5000000.00",
            "RetailNetFlow": "8000000.00",
            "SmallNetFlow": "2000000.00",
        },
    ]
    # 模拟 westock lhb 输出（真实契约：全市场当日 list，含中文字段 代码/名称/净买入额/机构买入席位）
    LHB_OUT = [
        {
            "排名": 1,
            "代码": "sh600519",
            "名称": "贵州茅台",
            "上榜天数": 1,
            "机构买入席位": 3,
            "机构买入额": "3.09亿",
            "买入占比": "17.39%",
            "总买入额": "3.09亿",
            "净买入额": "98765432.00",
            "净占比": "70.06%",
        },
        {
            "排名": 2,
            "代码": "sz000001",
            "名称": "平安银行",
            "上榜天数": 1,
            "机构买入席位": 1,
            "净买入额": "12345678.00",
        },
        # 前缀相似但不应误匹配：6005199 / 1600519 / 600510 / 含噪声
        {"排名": 3, "代码": "sh6005199", "名称": "噪声股", "净买入额": "111111.00"},
        {"排名": 4, "代码": "sh1600519", "名称": "噪声股2", "净买入额": "222222.00"},
        {"排名": 5, "代码": "sh600510", "名称": "噪声股3", "净买入额": "333333.00"},
    ]
    # 模拟 westock shareholder 输出（真实契约：{"sections":[[持有人...]]}）
    SHAREHOLDER_OUT = {
        "sections": [
            [
                {
                    "no": 1,
                    "name": "中国贵州茅台酒厂(集团)有限责任公司",
                    "holdShares": 681282935,
                    "holdPct": 54.4,
                    "holdChange": 0,
                },
                {
                    "no": 2,
                    "name": "香港中央结算有限公司",
                    "holdShares": 58733069,
                    "holdPct": 4.69,
                    "holdChange": 3684225,
                },
            ],
            [
                {
                    "no": 1,
                    "name": "中国贵州茅台酒厂(集团)有限责任公司",
                    "holdShares": 681282935,
                    "holdPct": 54.4,
                    "holdChange": 0,
                },
            ],
        ]
    }

    def _mock_westock(self, mod, out):
        # _run_westock 是模块级函数（适配器内直接调用，非 self.），须 mock 模块属性
        return mock.patch.object(mod, "_run_westock", return_value=out)

    def test_get_financial_normalizes_19_cols(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with self._mock_westock(mod, self.FINANCE_OUT):
            df = adapter.get_financial(["600519.SH"], "20240930", [])
        assert not df.empty
        std = [
            "code",
            "report_date",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ratio",
            "roe",
            "roa",
            "gross_margin",
            "net_margin",
            "revenue_growth",
            "profit_growth",
            "debt_ratio",
            "current_ratio",
            "quick_ratio",
            "ocf",
            "industry",
            "name",
            "disclosure_date",
        ]
        assert list(df.columns) == std
        assert df.iloc[0]["code"] == "600519.SH"
        # westock 仅提供报表原始科目，估值/衍生指标（pe_ttm/pb/roe 等）由原始科目派生或直接 NaN
        # pe_ttm/pb 无原生字段 → NaN；gross_margin/debt_ratio 由 TTM 科目派生应非空
        import numpy as np

        assert np.isnan(df.iloc[0]["pe_ttm"])
        assert not np.isnan(df.iloc[0]["gross_margin"])  # GrossProfitTTM/OperatingRevenueTTM
        assert not np.isnan(df.iloc[0]["debt_ratio"])  # TotalLiability/TotalAssets
        assert df.iloc[0]["report_date"] == "20240930"

    def test_get_capital_flow_normalizes_9_cols(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with self._mock_westock(mod, self.FUND_FLOW_OUT):
            df = adapter.get_capital_flow(["600519.SH"], "2024-03-01", "2024-03-31")
        assert not df.empty
        cols = [
            "code",
            "date",
            "main_net_inflow",
            "main_net_inflow_5d",
            "super_large_net",
            "large_net",
            "medium_net",
            "small_net",
            "north_net_inflow",
        ]
        assert list(df.columns) == cols
        assert (df["code"] == "600519.SH").all()

    def test_get_capital_flow_us_raises_invalid_param(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        adapter = mod.WestockAdapter()
        with pytest.raises(errs.InvalidParameterError):
            adapter.get_capital_flow(["AAPL.US"], "2024-03-01", "2024-03-31")

    def test_get_dragon_tiger_precise_match(self):
        """精确匹配：仅 600519 命中，000001 及前缀相似噪声（6005199/1600519/600510）均排除。"""
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with self._mock_westock(mod, self.LHB_OUT):
            df = adapter.get_dragon_tiger(["600519.SH"], "2024-03-01", "2024-03-31")
        assert not df.empty
        assert "code" in df.columns and "trade_date" in df.columns and "net_buy" in df.columns
        # 仅查询标的 600519.SH 应出现在命中行
        hit_rows = df[df["has_data"] == True]  # noqa: E712
        assert len(hit_rows) == 1, f"应仅 1 条精确命中，实际 {len(hit_rows)} 条"
        assert hit_rows.iloc[0]["code"] == "600519.SH"
        assert hit_rows.iloc[0]["net_buy"] == 98765432  # 600519 的真实净买额，非噪声行
        # 噪声行（6005199/1600519/600510/000001）不应误命中
        assert set(hit_rows["code"].tolist()) == {"600519.SH"}

    def test_get_dragon_tiger_no_false_prefix_match(self):
        """回归：查 600519 不得因子串包含误命中 6005199 / 1600519 / 600510。"""
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with self._mock_westock(mod, self.LHB_OUT):
            df = adapter.get_dragon_tiger(["600519.SH"], "2024-03-01", "2024-03-31")
        # 仅 1 条精确命中行（600519.SH）；绝不出现 6005199/1600519/600510/000001 等噪声
        assert len(df) == 1
        assert df.iloc[0]["code"] == "600519.SH"
        assert "6005199" not in df["code"].tolist()
        assert "1600519" not in df["code"].tolist()
        assert "600510" not in df["code"].tolist()
        assert "000001" not in df["code"].tolist()

    def test_get_dragon_tiger_hk_raises_invalid_param(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        adapter = mod.WestockAdapter()
        with pytest.raises(errs.InvalidParameterError):
            adapter.get_dragon_tiger(["00700.HK"], "2024-03-01", "2024-03-31")

    def test_get_shareholder_normalizes_6_cols(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with self._mock_westock(mod, self.SHAREHOLDER_OUT):
            df = adapter.get_shareholder(["600519.SH"], "20240930")
        assert not df.empty
        cols = ["code", "holder_name", "hold_amount", "hold_ratio", "change_type", "holder_type"]
        assert list(df.columns) == cols
        assert (df["code"] == "600519.SH").all()
        assert len(df) == 2  # 十大股东两条

    def test_sanitize_code_list_blocks_injection(self):
        mod = _load_adapter_module()
        # 合法代码保留；含 shell 元字符（; & 空格）的注入串被 _normalize_to_tencent_code
        # 拒绝（抛 InvalidParameterError 被捕获跳过），不会进入 westock 命令
        clean = mod._sanitize_code_list(["600519.SH", "000001.SZ; rm -rf /", "hk00700 & cat"])
        assert clean == ["sh600519"]

    def test_align_financial_report_date_quarter_end_unchanged(self):
        """财报日原样返回。"""
        mod = _load_adapter_module()
        assert mod._align_financial_report_date("2023-03-31") == "2023-03-31"
        assert mod._align_financial_report_date("2022-12-31") == "2022-12-31"
        assert mod._align_financial_report_date("20230930") == "2023-09-30"

    def test_align_financial_report_date_snaps_to_prev_quarter(self):
        """非财报日回退到最近前一个季度末（避免 westock 区间 null 误降级）。"""
        mod = _load_adapter_module()
        assert mod._align_financial_report_date("2023-01-01") == "2022-12-31"
        assert mod._align_financial_report_date("2023-05-20") == "2023-03-31"
        assert mod._align_financial_report_date("2023-07-10") == "2023-06-30"
        assert mod._align_financial_report_date("2023-11-15") == "2023-09-30"

    def test_align_financial_report_date_invalid_returns_empty(self):
        mod = _load_adapter_module()
        assert mod._align_financial_report_date("") == ""
        assert mod._align_financial_report_date("not-a-date") == ""
        assert mod._align_financial_report_date("2023-13-45") == ""

    def test_get_financial_non_quarter_date_aligns_and_succeeds(self):
        """回归：report_date=2023-01-01（非财报日）经对齐后走 westock 成功返回（不降级 baostock）。"""
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        # 对齐后实际查询 2022-12-31；mock _run_westock 返回该期 sections 结构
        SECTIONS = {
            "sections": [
                [
                    {
                        "_date": "2022-12-31",
                        "code": "sh600519",
                        "NPParentCompanyOwnersTTM": "62715105749.37",
                        "OperatingRevenueTTM": "124146396849.52",
                        "GrossProfitTTM": "115794820628.72",
                    }
                ],
                [
                    {
                        "_date": "2022-12-31",
                        "code": "sh600519",
                        "TotalLiability": "38782958469.89",
                        "TotalAssets": "281135886435.69",
                        "TotalCurrentAssets": "233068933753.52",
                        "TotalCurrentLiability": "38455594988.88",
                        "SEWithoutMI": "242352927965.80",
                    }
                ],
                [{"_date": "2022-12-31", "code": "sh600519", "NetOperateCashFlowTTM": "79622900612.10"}],
            ]
        }
        with mock.patch.object(mod, "_run_westock", return_value=SECTIONS) as m:
            df = adapter.get_financial(["600519.SH"], "2023-01-01", [])
        # 对齐后区间应包含 2022-12-31：--start 2022-10-01 --end 2022-12-31
        called_args = m.call_args[0][0]
        assert "--start" in called_args and "2022-10-01" in called_args
        assert "--end" in called_args and "2022-12-31" in called_args
        assert not df.empty
        assert df.iloc[0]["report_date"] == "2023-01-01"  # 保留引擎语义标签
        import numpy as np

        assert not np.isnan(df.iloc[0]["ocf"])  # 真实数据已解析


@pytest.mark.skill_data_engine
@pytest.mark.integration
@pytest.mark.requires_network
@pytest.mark.requires_node
class TestWestockWestockLive:
    """L3 轻量实测：600519 为锚点，真实经 npx westock 验证四类取数（默认 addopts 排除）。"""

    def test_live_financial_600519(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        try:
            df = adapter.get_financial(["600519.SH"], "20240930", [])
        except Exception as e:
            if "NodeMissing" in type(e).__name__ or "不可达" in str(e):
                pytest.skip(f"westock 不可用（node 缺失/网络受限）: {e}")
            raise
        assert not df.empty
        assert "pe_ttm" in df.columns

    def test_live_stock_list_600519(self):
        """L3 实测：westock connect 聚合陆股通标的列表（Phase 2 已落地）。"""
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        try:
            df = adapter.get_stock_list()
        except Exception as e:
            if "NodeMissing" in type(e).__name__ or "不可达" in str(e) or "DataNotFoundError" in type(e).__name__:
                pytest.skip(f"westock 不可用或返回空: {e}")
            raise
        assert not df.empty
        assert "code" in df.columns


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestWestockRateGuard:
    """轻限频护栏。"""

    def test_batch_symbol_cap(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        adapter = mod.WestockAdapter()
        big = [f"60000{i}.SH" for i in range(50)]
        with pytest.raises(errs.InvalidParameterError):
            adapter.get_daily(big, "2026-08-01", "2026-08-31")

    def test_min_interval_throttle_called(self):
        """验证单标的最小间隔护栏确实触发 sleep（mock time 不真实阻塞）。"""
        mod = _load_adapter_module()
        mod.WestockAdapter()
        sleeps = []
        fake_time = {"t": 1000.0}

        def fake_sleep(d):
            sleeps.append(d)
            fake_time["t"] += d

        with (
            mock.patch.object(mod.time, "time", side_effect=lambda: fake_time["t"]),
            mock.patch.object(mod.time, "sleep", side_effect=fake_sleep),
        ):
            mod._RateGuard.throttle("k")
            mod._RateGuard.throttle("k")  # 第二次应触发 sleep
        assert len(sleeps) >= 1


@pytest.mark.skill_data_engine
@pytest.mark.integration
@pytest.mark.requires_network
class TestWestockLive:
    """L3 轻量连通性实测：600519 为锚点。"""

    def test_live_daily_600519(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        try:
            df = adapter.get_daily(["600519.SH"], "2026-08-01", "2026-08-31", adjust="qfq")
        except Exception as e:
            if "不可达" in str(e) or "网络" in str(e):
                pytest.skip(f"腾讯公网不可达（网络受限）: {e}")
            raise
        assert not df.empty, "腾讯公网返回空日线"
        assert (df["code"] == "600519.SH").all()
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert (df["high"] >= df["low"]).all()
        # 锚点：最新收盘应接近本轮实测值（1308~1360 区间）
        assert df["close"].max() > 1000, "收盘价异常偏低，疑似解析错误"

    def test_live_realtime_600519(self):
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        try:
            q = adapter.get_realtime_quote("600519.SH")
        except Exception as e:
            if "不可达" in str(e) or "网络" in str(e):
                pytest.skip(f"腾讯公网不可达（网络受限）: {e}")
            raise
        assert "close" in q
        assert q["close"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-rs"])
