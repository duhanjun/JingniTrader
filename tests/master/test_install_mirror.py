"""install.py 镜像默认值与参数解析单元测试（不联网、不真实装包）。

覆盖：
1. 默认镜像：无参数、无环境变量时使用清华 TUNA。
2. `--index-url` 参数覆盖默认镜像。
3. `PIP_INDEX_URL` 环境变量覆盖默认镜像。
4. 优先级：`--index-url` > `PIP_INDEX_URL` > 默认。
5. requirements 解析：默认取 skill 根 requirements.txt；目录/文件/缺失分支。
6. build_command 结构正确（当前解释器 + -r + -i），额外 pip 参数透传。
7. main() 全流程：--dry-run 不触发 subprocess；失败退出码原样返回。
8. 双树（产品层 / 共享层）install.py 默认镜像常量一致。
"""

from __future__ import annotations

import importlib.util as ilu
import os
import sys
from pathlib import Path

import pytest

# tests/master/ → tests/ → <skill 根>
SKILL_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_INSTALL = SKILL_ROOT / "install.py"


def _resolve_shared_install() -> Path:
    """定位 _shared 树的 install.py。

    原实现硬编码 SKILL_ROOT.parents[2]/ir/_shared（假定仓库副本布局
    core/skills/jingni-tech/jingni-trader）。主目录布局（d:/codebuddy/jingni-trader）
    层级更浅，parents[2] 会 IndexError。改为：
      ① 遍历 SKILL_ROOT 各祖先（越界安全）找 core/ir/_shared
      ② 回退 SKILL_ROOT 的父目录兄弟（d:/codebuddy/jingni-agents）
      ③ 均未命中则返回原路径（由调用侧的 skipif 守卫处理）
    """
    rel = Path("ir") / "_shared" / "skills" / "jingni-trader" / "install.py"
    rel_core = Path("core") / "ir" / "_shared" / "skills" / "jingni-trader" / "install.py"
    for base in list(SKILL_ROOT.parents)[:4]:
        for cand_rel in (rel, rel_core):
            cand = base / cand_rel
            if cand.exists():
                return cand
    sibling = SKILL_ROOT.parent / "jingni-agents" / rel_core
    if sibling.exists():
        return sibling
    return SKILL_ROOT.parent / rel if len(SKILL_ROOT.parents) > 0 else Path("ir") / "_shared"


SHARED_INSTALL = _resolve_shared_install()

DEFAULT_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
ALIYUN_MIRROR = "https://mirrors.aliyun.com/pypi/simple"


def _load_install(path: Path, mod_name: str):
    """独立加载指定树的 install.py（纯标准库脚本，无副作用导入）。"""
    spec = ilu.spec_from_file_location(mod_name, str(path))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def install_mod():
    return _load_install(PRODUCT_INSTALL, "jt_install_product")


@pytest.mark.skill_master
@pytest.mark.unit
class TestIndexUrlResolution:
    def test_default_is_tuna(self, install_mod):
        """无参数、无环境变量 → 默认清华 TUNA。"""
        assert install_mod.DEFAULT_INDEX_URL == DEFAULT_MIRROR
        assert install_mod.resolve_index_url(None, env={}) == DEFAULT_MIRROR

    def test_cli_arg_overrides_default(self, install_mod):
        """--index-url 覆盖默认镜像。"""
        assert install_mod.resolve_index_url(ALIYUN_MIRROR, env={}) == ALIYUN_MIRROR

    def test_env_var_overrides_default(self, install_mod):
        """PIP_INDEX_URL 环境变量覆盖默认镜像。"""
        env = {"PIP_INDEX_URL": ALIYUN_MIRROR}
        assert install_mod.resolve_index_url(None, env=env) == ALIYUN_MIRROR

    def test_cli_arg_beats_env_var(self, install_mod):
        """优先级：--index-url > PIP_INDEX_URL。"""
        env = {"PIP_INDEX_URL": ALIYUN_MIRROR}
        cli = "https://mirrors.cloud.tencent.com/pypi/simple"
        assert install_mod.resolve_index_url(cli, env=env) == cli

    def test_empty_env_var_falls_back_to_default(self, install_mod):
        """PIP_INDEX_URL 为空串时视为未设置，回落默认镜像。"""
        assert install_mod.resolve_index_url(None, env={"PIP_INDEX_URL": ""}) == DEFAULT_MIRROR

    def test_reads_real_os_environ_by_default(self, install_mod, monkeypatch):
        """env 参数省略时读取真实 os.environ。"""
        monkeypatch.setenv("PIP_INDEX_URL", ALIYUN_MIRROR)
        assert install_mod.resolve_index_url(None) == ALIYUN_MIRROR
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        assert install_mod.resolve_index_url(None) == DEFAULT_MIRROR


@pytest.mark.skill_master
@pytest.mark.unit
class TestArgParsing:
    def test_defaults_are_none(self, install_mod):
        args = install_mod.parse_args([])
        assert args.index_url is None
        assert args.requirements is None
        assert args.dry_run is False

    def test_parses_index_url_and_requirements(self, install_mod):
        args = install_mod.parse_args(
            ["--index-url", ALIYUN_MIRROR, "--requirements", "requirements.txt", "--dry-run"]
        )
        assert args.index_url == ALIYUN_MIRROR
        assert args.requirements == "requirements.txt"
        assert args.dry_run is True

    def test_extra_pip_args_passthrough(self, install_mod):
        """`--` 之后的参数透传给 pip，且分隔符本身被剥离。"""
        args = install_mod.parse_args(["--", "--upgrade", "--no-cache-dir"])
        cleaned = install_mod._clean_pip_args(args.pip_args)
        assert cleaned == ["--upgrade", "--no-cache-dir"]


@pytest.mark.skill_master
@pytest.mark.unit
class TestRequirementsResolution:
    def test_default_resolves_skill_root_requirements(self, install_mod):
        """默认解析到 skill 根 requirements.txt（真实存在）。"""
        req = install_mod.resolve_requirements(None)
        assert req.is_file()
        assert req.name == "requirements.txt"
        assert req.parent == SKILL_ROOT

    def test_relative_path_is_skill_root_anchored(self, install_mod, tmp_path, monkeypatch):
        """相对路径相对 skill 根解析，不受 cwd 影响。"""
        monkeypatch.chdir(tmp_path)  # cwd 换到别处
        req = install_mod.resolve_requirements("requirements.txt")
        assert req.parent == SKILL_ROOT

    def test_directory_target_appends_requirements(self, install_mod, tmp_path):
        """传目录 → 自动补 requirements.txt。"""
        (tmp_path / "requirements.txt").write_text("pandas>=2.0.0\n", encoding="utf-8")
        req = install_mod.resolve_requirements(str(tmp_path))
        assert req == tmp_path / "requirements.txt"

    def test_missing_raises_file_not_found(self, install_mod, tmp_path):
        with pytest.raises(FileNotFoundError):
            install_mod.resolve_requirements(str(tmp_path / "nope" / "requirements.txt"))


@pytest.mark.skill_master
@pytest.mark.unit
class TestBuildCommand:
    def test_command_structure(self, install_mod, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("pandas>=2.0.0\n", encoding="utf-8")
        cmd = install_mod.build_command(req, DEFAULT_MIRROR)
        # 使用当前解释器，避免 pip / python 版本错配
        assert cmd[0] == sys.executable
        assert cmd[1:4] == ["-m", "pip", "install"]
        assert "-r" in cmd and cmd[cmd.index("-r") + 1] == str(req)
        assert "-i" in cmd and cmd[cmd.index("-i") + 1] == DEFAULT_MIRROR

    def test_extra_args_appended(self, install_mod, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("pandas>=2.0.0\n", encoding="utf-8")
        cmd = install_mod.build_command(req, DEFAULT_MIRROR, ["--upgrade"])
        assert cmd[-1] == "--upgrade"


@pytest.mark.skill_master
@pytest.mark.unit
class TestMainFlow:
    def test_dry_run_does_not_invoke_subprocess(self, install_mod, monkeypatch, capsys):
        """--dry-run 只打印命令，绝不调用 subprocess。"""
        called = {"n": 0}

        def _boom(*a, **k):
            called["n"] += 1
            raise AssertionError("dry-run 不应调用 subprocess.run")

        monkeypatch.setattr(install_mod.subprocess, "run", _boom)
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        rc = install_mod.main(["--dry-run"])
        assert rc == 0
        assert called["n"] == 0
        out = capsys.readouterr().out
        assert DEFAULT_MIRROR in out  # 默认镜像出现在输出

    def test_dry_run_reports_override_source(self, install_mod, monkeypatch, capsys):
        """覆盖时输出使用用户指定镜像。"""
        monkeypatch.setattr(install_mod.subprocess, "run", lambda *a, **k: None)
        rc = install_mod.main(["--dry-run", "--index-url", ALIYUN_MIRROR])
        assert rc == 0
        out = capsys.readouterr().out
        assert ALIYUN_MIRROR in out
        assert "--index-url 参数" in out

    def test_env_override_reflected_in_command(self, install_mod, monkeypatch, capsys):
        """PIP_INDEX_URL 生效时命令中带该镜像。"""
        monkeypatch.setenv("PIP_INDEX_URL", ALIYUN_MIRROR)
        monkeypatch.setattr(install_mod.subprocess, "run", lambda *a, **k: None)
        rc = install_mod.main(["--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert ALIYUN_MIRROR in out
        assert "PIP_INDEX_URL 环境变量" in out

    def test_missing_requirements_returns_2(self, install_mod, tmp_path, capsys):
        rc = install_mod.main(["--requirements", str(tmp_path / "absent.txt")])
        assert rc == 2
        assert "错误" in capsys.readouterr().err

    def test_pip_failure_propagates_returncode(self, install_mod, monkeypatch, capsys):
        """pip 非零退出码原样返回，并给出换源排查建议。"""

        class _P:
            returncode = 7

        monkeypatch.setattr(install_mod.subprocess, "run", lambda *a, **k: _P())
        rc = install_mod.main([])
        assert rc == 7
        err = capsys.readouterr().err
        assert "安装失败" in err

    def test_success_returns_zero(self, install_mod, monkeypatch, capsys):
        class _P:
            returncode = 0

        monkeypatch.setattr(install_mod.subprocess, "run", lambda *a, **k: _P())
        rc = install_mod.main([])
        assert rc == 0
        assert "安装完成" in capsys.readouterr().out

    def test_actual_command_uses_default_mirror(self, install_mod, monkeypatch):
        """真实执行路径（非 dry-run）也带默认清华镜像 -i 参数。"""
        seen = {}

        class _P:
            returncode = 0

        def _capture(cmd, **kwargs):
            seen["cmd"] = cmd
            return _P()

        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        monkeypatch.setattr(install_mod.subprocess, "run", _capture)
        assert install_mod.main([]) == 0
        cmd = seen["cmd"]
        assert cmd[cmd.index("-i") + 1] == DEFAULT_MIRROR


@pytest.mark.skill_master
@pytest.mark.unit
class TestDualTreeConsistency:
    def test_shared_tree_install_exists(self):
        assert SHARED_INSTALL.is_file(), f"共享层缺少 install.py: {SHARED_INSTALL}"

    def test_both_trees_same_default_mirror(self):
        """双树 install.py 默认镜像常量一致（均为清华 TUNA）。"""
        shared = _load_install(SHARED_INSTALL, "jt_install_shared")
        product = _load_install(PRODUCT_INSTALL, "jt_install_product_dual")
        assert shared.DEFAULT_INDEX_URL == DEFAULT_MIRROR
        assert product.DEFAULT_INDEX_URL == shared.DEFAULT_INDEX_URL

    def test_shared_tree_resolves_own_requirements(self):
        """共享树 install.py 解析到自身 requirements.txt（自包含，不串到产品树）。"""
        shared = _load_install(SHARED_INSTALL, "jt_install_shared_req")
        req = shared.resolve_requirements(None)
        assert req == SHARED_INSTALL.parent / "requirements.txt"
        assert req.is_file()

    def test_wrapper_scripts_present_in_both_trees(self):
        """两棵树均提供 install.sh / install.bat 包装脚本。"""
        for root in (SKILL_ROOT, SHARED_INSTALL.parent):
            assert (root / "install.sh").is_file(), f"缺 install.sh: {root}"
            assert (root / "install.bat").is_file(), f"缺 install.bat: {root}"
