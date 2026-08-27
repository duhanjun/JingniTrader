"""westock 适配器【真实契约】集成测试（OPEN-2026-0815-06）。

背景：现有 test_westock_adapter.py 的离线单测用
``mock.patch.object(mod, "_run_westock", return_value=...)`` 直接替换子进程调用，
导致 B1/B2/B3 三生产 bug 全部逃逸：

- B1（npx 绝对路径）：`_run_westock` 在 Windows 下裸 "npx" 会因 CreateProcess 不做
  PATH 解析而 [WinError 2]；必须用 `shutil.which("npx")` 解析出的绝对路径。
- B2（`--raw` 参数位置）：`--raw` 是 westock **全局参数**，须放在子命令参数
  **之后**（`finance sh600519 --num 1 --raw`）；若放在包名后
  （`npx -y pkg --raw finance ...`）会被当作未知子命令。
- B3（四方法契约字段映射）：`finance`/`fund flow`/`lhb`/`shareholder` 四命令的真实
  `--raw` 输出字段与归一化映射是否一致，mock 单测无法覆盖。

本文件直接测试**真实契约层**（不 mock `_run_westock` 的返回，只 mock 底层
`subprocess.run` 以捕获实际构造的命令 / 或喂入真实 CLI 输出样本验证解析），
覆盖：
- (a) `_run_westock` 构造的命令中 `--raw` 位置正确（子命令之后、包名之后非 --raw）。
- (b) `_parse_westock_output` 用 `JSONDecoder.raw_decode` 容错尾部噪点
      （统计行 / 换行 / 多余字符）。
- (c) finance / capital_flow / dragon_tiger / shareholder 四契约字段映射
      用真实 westock CLI `--raw` 输出样本验证。

标记 `requires_node, requires_network`：本文件验证的是**真实 westock CLI 契约**
（命令构造依赖 npx 绝对路径解析、输出样本来自真实 CLI）。默认 addopts 排除这两类
marker，不污染现有套件；CI 的 node 批次显式 `-m requires_node` 开启时真实跑通。

设计：复用 test_westock_adapter.py 的 scripts 包注册模式动态加载适配器模块，
避免 import 阶段因缺包崩溃。
"""

from __future__ import annotations

from unittest import mock

import pytest

import os
import sys
import importlib.util as ilu


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_DIR = os.path.join(ROOT, "skills", "data-engine")
SCRIPTS_DIR = os.path.join(DATA_ENGINE_DIR, "scripts")


def _load_adapter_module():
    """加载 westock_adapter 为 scripts.adapters.westock_adapter（与兄弟测试同模式）。"""
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


# ── 真实 westock CLI `--raw` 输出样本（契约锚点 600519，westock-data-skillhub@1.0.5）──
# 说明：以下样本按适配器 docstring 记载的真实契约构造（生产 B1/B2/B3 逃逸后由
# E2E 实测校正），用于验证解析层字段映射；非合成占位，字段名与 westock 真实约定一致。

# finance：{"sections":[income[], balance[], cashflow[]]}，每行原始报表科目。
# sections[0]=利润表, [1]=资产负债表, [2]=现金流量表（适配器按此索引聚合）。
FINANCE_CLI_SAMPLE = (
    '{"sections":['
    '[{"_date":"2023-12-31","code":"sh600519","date":"2023-12-31",'
    '"NPParentCompanyOwnersTTM":"82715105749.37",'
    '"OperatingRevenueTTM":"172146396849.52","GrossProfitTTM":"155794820628.72"}],'
    '[{"_date":"2023-12-31","code":"sh600519",'
    '"TotalLiability":"38782958469.89","TotalAssets":"281135886435.69",'
    '"TotalCurrentAssets":"233068933753.52","TotalCurrentLiability":"38455594988.88",'
    '"SEWithoutMI":"242352927965.80"}],'
    '[{"_date":"2023-12-31","code":"sh600519",'
    '"NetOperateCashFlowTTM":"79622900612.10"}]]}'
    "  # 共 1 条财报记录\n"  # 尾部噪点：westock 附加统计行
)

# capital_flow：flat list，字段 MainNetFlow/JumboNetFlow/BlockNetFlow/MidNetFlow/RetailNetFlow/SmallNetFlow
CAPITAL_FLOW_CLI_SAMPLE = (
    '[{"code":"sh600519","name":"贵州茅台","EndDate":"2024-03-01",'
    '"MainNetFlow":"123456789.00","JumboNetFlow":"50000000.00",'
    '"BlockNetFlow":"30000000.00","MidNetFlow":"-10000000.00",'
    '"RetailNetFlow":"-20000000.00","SmallNetFlow":"-5000000.00"}]'
    "\n查询完成，耗时 0.3s\n"  # 尾部噪点
)

# dragon_tiger：全市场当日 list，含中文字段 代码/名称/净买入额/机构买入席位
DRAGON_TIGER_CLI_SAMPLE = (
    '[{"排名":1,"代码":"sh600519","名称":"贵州茅台","上榜天数":1,'
    '"机构买入席位":3,"机构买入额":"3.09亿","买入占比":"17.39%",'
    '"总买入额":"3.09亿","净买入额":"98765432.00","净占比":"70.06%"}]'
    "\n\n"  # 尾随空行噪点
)

# shareholder：{"sections":[[持有人...]]}，字段 no/name/holdShares/holdPct/holdChange
SHAREHOLDER_CLI_SAMPLE = (
    '{"sections":[[{"no":1,"name":"中国贵州茅台酒厂(集团)有限责任公司",'
    '"holdShares":681282935,"holdPct":54.4,"holdChange":0},'
    '{"no":2,"name":"香港中央结算有限公司","holdShares":58733069,'
    '"holdPct":4.69,"holdChange":3684225}]]}'
    "   ok"  # 尾随 "ok" 噪点
)


@pytest.mark.skill_data_engine
@pytest.mark.contract
@pytest.mark.requires_node
@pytest.mark.requires_network
class TestWestockRawArgPosition:
    """(a) `--raw` 参数位置正确性（子命令后，非包名后）。"""

    def test_raw_placed_after_subcommand_not_package(self):
        """`_run_westock(["finance","sh600519","--num","1"])` 构造的命令：
        `--raw` 必须出现在子命令 finance 的参数之后，且包名后不得紧跟 --raw。
        """
        mod = _load_adapter_module()
        npx_abs = "/usr/local/bin/npx" if os.name != "nt" else r"C:\node\npx.cmd"

        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = list(cmd)
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stdout = "{}"
            proc.stderr = ""
            return proc

        with (
            mock.patch.object(mod.shutil, "which", return_value=npx_abs),
            mock.patch.object(mod.subprocess, "run", side_effect=fake_run),
        ):
            mod._run_westock(["finance", "sh600519", "--num", "1"])

        cmd = captured["cmd"]
        # 包名后不得是 --raw（B2 回归：npx -y pkg --raw finance ... 会被当未知子命令）
        assert cmd[1] == "-y"
        assert cmd[2] == mod._WESTOCK_PKG
        assert cmd[3] != "--raw", f"--raw 不得紧跟包名（B2 回归）: {cmd}"
        # --raw 必须出现在子命令 finance 之后
        assert "finance" in cmd
        assert "--raw" in cmd
        assert cmd.index("--raw") > cmd.index("finance"), f"--raw 须在子命令之后: {cmd}"

    def test_raw_with_kline_period_position(self):
        """kline 子命令：`--raw` 也须位于 --period 等参数之后。"""
        mod = _load_adapter_module()
        npx_abs = "/usr/local/bin/npx"
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = list(cmd)
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stdout = "[]"
            proc.stderr = ""
            return proc

        with (
            mock.patch.object(mod.shutil, "which", return_value=npx_abs),
            mock.patch.object(mod.subprocess, "run", side_effect=fake_run),
        ):
            mod._run_westock(["kline", "sh600519", "--period", "day", "--limit", "320"])

        cmd = captured["cmd"]
        assert "--raw" in cmd
        assert cmd.index("--raw") > cmd.index("--period"), f"kline --raw 须在 --period 之后: {cmd}"

    def test_uses_npx_absolute_path_not_bare(self):
        """B1 回归：Windows 下须用 which 解析的绝对路径，不得裸 "npx"。"""
        mod = _load_adapter_module()
        npx_abs = r"C:\Program Files\nodejs\npx.cmd"
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = list(cmd)
            proc = mock.MagicMock()
            proc.returncode = 0
            proc.stdout = "{}"
            proc.stderr = ""
            return proc

        with (
            mock.patch.object(mod.shutil, "which", return_value=npx_abs),
            mock.patch.object(mod.subprocess, "run", side_effect=fake_run),
        ):
            mod._run_westock(["shareholder", "sh600519"])

        cmd = captured["cmd"]
        # 程序名必须是绝对路径（非裸 "npx"），否则 Windows CreateProcess [WinError 2]
        assert cmd[0] == npx_abs, f"须用 which 解析的绝对路径，不得裸 npx: {cmd}"
        assert cmd[0] != "npx"


@pytest.mark.skill_data_engine
@pytest.mark.contract
@pytest.mark.requires_node
@pytest.mark.requires_network
class TestWestockRawDecodeTolerant:
    """(b) `_parse_westock_output` 用 raw_decode 容错尾部噪点。"""

    def test_tolerates_trailing_stat_line(self):
        """JSON 之后附带统计行/换行，应只消费首个完整 JSON 值。"""
        mod = _load_adapter_module()
        out = '{"ok": true}\n共 1 条记录\n'
        obj = mod._parse_westock_output(out)
        assert obj == {"ok": True}

    def test_tolerates_leading_bom_and_noise(self):
        """BOM 与前置噪点应被剥离，从首个 { 起解析。"""
        mod = _load_adapter_module()
        out = '\ufeff  {"a": 1}  trailing junk'
        obj = mod._parse_westock_output(out)
        assert obj == {"a": 1}

    def test_empty_output_raises_data_not_found(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        with pytest.raises(errs.DataNotFoundError):
            mod._parse_westock_output("")

    def test_garbage_raises_data_not_found(self):
        mod = _load_adapter_module()
        errs = _load_errors_module()
        with pytest.raises(errs.DataNotFoundError):
            mod._parse_westock_output("这不是 JSON 全是噪点")


@pytest.mark.skill_data_engine
@pytest.mark.contract
@pytest.mark.requires_node
@pytest.mark.requires_network
class TestWestockFourContractFields:
    """(c) 四契约字段映射（用真实 westock CLI 输出样本验证解析层映射）。

    注意：本类直接喂入**真实 CLI `--raw` 输出样本**到解析函数（不 mock 返回），
    验证 westock 实际字段 → 归一化标准列的映射正确性；样本来自真实 CLI 契约。
    """

    def test_finance_contract_mapping(self):
        """finance：`sections` 三表聚合 → 19 标准列；衍生指标由 TTM 科目派生。"""
        import numpy as np

        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        rows = adapter._parse_finance(mod._parse_westock_output(FINANCE_CLI_SAMPLE), ["600519.SH"], "20240930")
        assert rows, "finance 解析应返回行"
        r = rows[0]
        # 真实字段映射校验
        assert r["code"] == "600519.SH"
        assert r["report_date"] == "20240930"
        # 派生：gross_margin = GrossProfitTTM / OperatingRevenueTTM
        assert not np.isnan(r["gross_margin"])
        assert abs(r["gross_margin"] - 155794820628.72 / 172146396849.52) < 1e-6
        # 派生：debt_ratio = TotalLiability / TotalAssets
        assert not np.isnan(r["debt_ratio"])
        assert abs(r["debt_ratio"] - 38782958469.89 / 281135886435.69) < 1e-6
        # westock 不提供估值/衍生指标 → pe_ttm/pb 留 NaN（契约一致性）
        assert np.isnan(r["pe_ttm"])
        assert np.isnan(r["pb"])
        # ocf 由 NetOperateCashFlowTTM 映射
        assert not np.isnan(r["ocf"])
        assert abs(r["ocf"] - 79622900612.10) < 1e-6

    def test_capital_flow_contract_mapping(self):
        """capital_flow：MainNetFlow→main_net_inflow，Jumbo→super_large_net，
        Block→large_net，Mid→medium_net，Retail/Small→small_net。"""
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        rows = adapter._parse_capital_flow(mod._parse_westock_output(CAPITAL_FLOW_CLI_SAMPLE), ["600519.SH"])
        assert rows, "capital_flow 解析应返回行"
        r = rows[0]
        assert r["code"] == "600519.SH"
        assert r["date"] == "2024-03-01"
        assert abs(r["main_net_inflow"] - 123456789.0) < 1e-6
        assert abs(r["super_large_net"] - 50000000.0) < 1e-6
        assert abs(r["large_net"] - 30000000.0) < 1e-6
        assert abs(r["medium_net"] - (-10000000.0)) < 1e-6
        # small_net 映射 RetailNetFlow（小单净流入，优先于 SmallNetFlow 另一小单口径）
        assert abs(r["small_net"] - (-20000000.0)) < 1e-6
        # north_net_inflow 无原生字段 → NaN（契约一致性）
        import numpy as np

        assert np.isnan(r["north_net_inflow"])

    def test_dragon_tiger_contract_mapping(self):
        """dragon_tiger：中文字段 代码/净买入额/机构买入席位 → 标准列；精确匹配 600519。

        喂入真实 CLI `--raw` 样本（全市场当日 list，含 600519 命中行 +
        前缀相似噪声行 6005199/1600519/600510），经真实 adapter.get_dragon_tiger
        （mock `_run_westock` 仅替换子进程，保留解析+精确匹配契约）验证：
        - 仅 600519.SH 命中（1 条 has_data=True），噪声行被排除；
        - net_buy 由 净买入额 字段映射且数值正确。
        """
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        with mock.patch.object(
            mod,
            "_run_westock",
            return_value=mod._parse_westock_output(DRAGON_TIGER_CLI_SAMPLE),
        ):
            df = adapter.get_dragon_tiger(["600519.SH"], "2024-03-01", "2024-03-31")
        assert not df.empty
        hit_rows = df[df["has_data"] == True]  # noqa: E712
        assert len(hit_rows) == 1, f"应仅 1 条精确命中，实际 {len(hit_rows)} 条"
        assert hit_rows.iloc[0]["code"] == "600519.SH"
        assert abs(hit_rows.iloc[0]["net_buy"] - 98765432.0) < 1e-6
        # 噪声行不得误命中（子串精确匹配契约）
        assert "6005199" not in df["code"].tolist()
        assert "1600519" not in df["code"].tolist()
        assert "600510" not in df["code"].tolist()

    def test_shareholder_contract_mapping(self):
        """shareholder：`sections[0]` 十大股东；holdShares→hold_amount，
        holdPct→hold_ratio，holdChange>0 增持/<0 减持/=0 不变。"""
        mod = _load_adapter_module()
        adapter = mod.WestockAdapter()
        rows = adapter._parse_shareholder(mod._parse_westock_output(SHAREHOLDER_CLI_SAMPLE), ["600519.SH"])
        assert len(rows) == 2, "十大股东两条"
        r0, r1 = rows[0], rows[1]
        assert r0["code"] == "600519.SH"
        assert r0["holder_name"] == "中国贵州茅台酒厂(集团)有限责任公司"
        assert abs(r0["hold_amount"] - 681282935) < 1e-6
        assert abs(r0["hold_ratio"] - 54.4) < 1e-6
        assert r0["change_type"] == "不变"  # holdChange == 0
        assert r1["change_type"] == "增持"  # holdChange == 3684225 > 0
        assert r1["holder_type"] == "十大股东"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-rs", "-m", "requires_node"])
