"""_pip_install 默认镜像注入（清华 TUNA）单元测试。

验证：
1. 未设置 PIP_INDEX_URL 时，调用 _pip_install 后 os.environ["PIP_INDEX_URL"]
   被 setdefault 注入为清华 TUNA 镜像。
2. 用户已设置 PIP_INDEX_URL 时，_pip_install 不得覆盖用户配置（setdefault 语义）。
3. 双树（产品层 / 共享层）engine.py 行为一致。

注意：本测试仅验证环境变量注入逻辑，不真实执行 pip（避免联网/装包）。
通过 monkeypatch subprocess.run 阻止真实子进程调用。
"""

from __future__ import annotations

import importlib.util as ilu
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ENGINE_PRODUCT = os.path.join(ROOT, "skills", "data-engine", "engine.py")

# 共享层（core/ir/_shared/skills/jingni-trader/）相对本 skill 的位置，取决于本树被
# 部署在仓库的哪一层：
#   - 副本树 core/skills/jingni-tech/jingni-trader/  → 需上溯 3 级才到仓库根
#   - 主目录 d:/codebuddy/jingni-trader/             → 是独立仓库，根本不存在该路径
# 因此不能用固定 ".." 级数硬编码（主目录上溯 3 级会越界到 D:\ir\... 报
# FileNotFoundError），改为向上查找仓库根（以 core/ 目录存在为判据）动态定位。
_SHARED_REL = os.path.join("core", "ir", "_shared", "skills", "jingni-trader",
                           "skills", "data-engine", "engine.py")


def _find_shared_engine() -> str:
    """自本文件向上查找仓库根，定位共享层 engine.py；找不到返回空串。"""
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, "core")):
            candidate = os.path.join(cur, _SHARED_REL)
            if os.path.isfile(candidate):
                return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


DATA_ENGINE_SHARED = _find_shared_engine()
_SHARED_ENGINE_EXISTS = bool(DATA_ENGINE_SHARED)

DEFAULT_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _load_engine(tree_path: str, mod_name: str):
    """独立加载指定树的 data-engine engine.py，避免污染 sys.modules['engine']。

    关键：engine.py 内部 `from scripts.config import ...`，而 scripts 包按
    sys.path 解析。为避免产品树/共享树的 scripts 互相串扰，加载前把目标树根
    （其下即 scripts/ 包）前置到 sys.path，并清理已缓存的同名 scripts 模块，
    保证 importlib 重新解析到正确树的 scripts。
    """
    engine_py = os.path.abspath(tree_path)
    # engine.py 内 `from scripts.config import ...`，scripts 包位于
    # <skill>/skills/data-engine/scripts/；故将其父目录（data-engine/）前置到
    # sys.path，使 `scripts` 解析到正确树的 data-engine/scripts 包。
    tree_root = os.path.dirname(engine_py)  # .../<skill>/skills/data-engine
    # 清理可能缓存的 scripts 包，强制重新解析到目标树
    for k in list(sys.modules.keys()):
        if k == "scripts" or k.startswith("scripts."):
            sys.modules.pop(k, None)
    sys.path.insert(0, tree_root)
    try:
        spec = ilu.spec_from_file_location(mod_name, engine_py)
        mod = ilu.module_from_spec(spec)
        sys.modules.setdefault("pandas", __import__("pandas"))
        sys.modules.setdefault("numpy", __import__("numpy"))
        spec.loader.exec_module(mod)
    finally:
        # 还原 sys.path，避免影响后续用例的脚本解析
        if tree_root in sys.path:
            sys.path.remove(tree_root)
    return mod


@pytest.mark.skill_data_engine
@pytest.mark.unit
class TestPipInstallMirror:
    @pytest.mark.parametrize(
        "tree,mod_name",
        [
            (DATA_ENGINE_PRODUCT, "de_product_engine_mirror"),
            pytest.param(
                DATA_ENGINE_SHARED, "de_shared_engine_mirror",
                marks=pytest.mark.skipif(
                    not _SHARED_ENGINE_EXISTS,
                    reason="本树未部署在含 core/ir/_shared 的仓库内（如独立主目录），"
                           "共享层镜像校验不适用；产品层用例仍完整执行",
                ),
            ),
        ],
    )
    def test_setdefault_injects_tuna_when_unset(self, monkeypatch, tree, mod_name):
        """未设置 PIP_INDEX_URL 时，_pip_install 注入清华镜像。"""
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        # 阻止真实子进程：让 subprocess.run 立即返回成功，且不触碰网络
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["env"] = dict(os.environ)
            class _P:
                returncode = 0
                stderr = ""
            return _P()

        monkeypatch.setattr("subprocess.run", fake_run)
        mod = _load_engine(tree, mod_name)
        # 调用前确保无残留
        assert "PIP_INDEX_URL" not in os.environ
        mod._pip_install("some-pkg")
        # setdefault 注入生效
        assert os.environ.get("PIP_INDEX_URL") == DEFAULT_MIRROR
        # 子进程继承了注入后的环境变量
        assert calls["env"].get("PIP_INDEX_URL") == DEFAULT_MIRROR

    @pytest.mark.parametrize(
        "tree,mod_name",
        [
            (DATA_ENGINE_PRODUCT, "de_product_engine_mirror2"),
            pytest.param(
                DATA_ENGINE_SHARED, "de_shared_engine_mirror2",
                marks=pytest.mark.skipif(
                    not _SHARED_ENGINE_EXISTS,
                    reason="本树未部署在含 core/ir/_shared 的仓库内（如独立主目录），"
                           "共享层镜像校验不适用；产品层用例仍完整执行",
                ),
            ),
        ],
    )
    def test_user_index_url_not_overridden(self, monkeypatch, tree, mod_name):
        """用户已设置 PIP_INDEX_URL 时，_pip_install 不覆盖。"""
        user_mirror = "https://mirrors.aliyun.com/pypi/simple"
        monkeypatch.setenv("PIP_INDEX_URL", user_mirror)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: type("P", (), {"returncode": 0, "stderr": ""})())
        mod = _load_engine(tree, mod_name)
        mod._pip_install("some-pkg")
        # 用户配置保留，不被清华镜像覆盖
        assert os.environ.get("PIP_INDEX_URL") == user_mirror

    @pytest.mark.parametrize(
        "tree,mod_name",
        [
            (DATA_ENGINE_PRODUCT, "de_product_engine_default_const"),
            pytest.param(
                DATA_ENGINE_SHARED, "de_shared_engine_default_const",
                marks=pytest.mark.skipif(
                    not _SHARED_ENGINE_EXISTS,
                    reason="本树未部署在含 core/ir/_shared 的仓库内（如独立主目录），"
                           "共享层镜像校验不适用；产品层用例仍完整执行",
                ),
            ),
        ],
    )
    def test_default_mirror_constant_present(self, tree, mod_name):
        """双树 engine.py 均定义 _DEFAULT_PIP_INDEX_URL 且为清华镜像。"""
        mod = _load_engine(tree, mod_name)
        assert hasattr(mod, "_DEFAULT_PIP_INDEX_URL")
        assert mod._DEFAULT_PIP_INDEX_URL == DEFAULT_MIRROR
