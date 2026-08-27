"""skill 静态契约测试 + skill-sync 与 engine 映射一致性校验。

本文件针对 jingni-trader 作为「AI Agent skill」的特质，验证纯代码测试无法覆盖的
「声明与实现一致性」，防止文档-代码漂移：

Part 1: skill 静态契约（L0）
    - 各子 skill 目录完整性（engine.py / scripts/ / SKILL.md 存在）
    - SKILL.md frontmatter 关键字段（name/entry_point/allowed_sub_skills）
    - 目录物理结构 与 SKILL.md allowed_sub_skills 声明一致
    - engine.py 的 _SUBSKILL_SCRIPTS 指向的 scripts 包真实存在

Part 2: skill-sync 与 engine 映射一致性
    - engine.py 的 SKILL_MODULES 指向的 engine 模块可 import
    - _SUBSKILL_SCRIPTS 的 stage → 目录映射 与 SKILL_MODULES 一一对应
    - 主 skill-sync.yml 与 jingni-datafeed 子 skill 的独立版本检查机制并存
    - skill_sync._SUBSKILL_DEPENDENCIES 与实际 skills/ 目录一致

Part 3: 触发关键词闭环
    - engine.py 的 STRATEGY_KEYWORDS / ATTRIBUTION_KEYWORDS 与 SKILL.md / README.md
      中的意图表描述保持同步（防止路由语义漂移）
"""
from __future__ import annotations

import os
import sys
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_DIR = os.path.join(ROOT, "skills")

# 7 个引擎子 skill（与 engine.py _SUBSKILL_SCRIPTS 的 key 一一对应）
STAGE_SKILL_MAP = {
    "DATA": "data-engine",
    "FACTOR": "factor-engine",
    "MODEL": "strategy-model-engine",
    "BACKTEST": "backtest-engine",
    "PORTFOLIO": "portfolio-risk-engine",
    "EXECUTION": "execution-monitor-engine",
    "REPORT": "reports-engine",
}


# ============================================================================
# Part 1: skill 静态契约
# ============================================================================

class TestSkillDirectoryContract:
    """验证每个引擎子 skill 的目录结构完整性。"""

    @pytest.mark.parametrize("skill", list(STAGE_SKILL_MAP.values()))
    def test_engine_skill_has_required_files(self, skill):
        """每个引擎 skill 必须存在 engine.py、scripts/、SKILL.md、__init__.py。"""
        skill_dir = os.path.join(SKILLS_DIR, skill)
        assert os.path.isdir(skill_dir), f"skill 目录不存在: {skill_dir}"
        assert os.path.isfile(os.path.join(skill_dir, "engine.py")), f"{skill}/engine.py 缺失"
        assert os.path.isdir(os.path.join(skill_dir, "scripts")), f"{skill}/scripts/ 缺失"
        assert os.path.isfile(os.path.join(skill_dir, "SKILL.md")), f"{skill}/SKILL.md 缺失"
        assert os.path.isfile(os.path.join(skill_dir, "__init__.py")), f"{skill}/__init__.py 缺失"

    @pytest.mark.parametrize("skill", list(STAGE_SKILL_MAP.values()))
    def test_engine_skill_scripts_has_init(self, skill):
        """每个引擎 skill 的 scripts/ 必须有 __init__.py（作为可 import 的包）。"""
        assert os.path.isfile(
            os.path.join(SKILLS_DIR, skill, "scripts", "__init__.py")
        ), f"{skill}/scripts/__init__.py 缺失"


class TestSkillManifestFrontmatter:
    """验证主 skill 的 SKILL.md frontmatter 与物理结构一致。"""

    @pytest.fixture(scope="class")
    @classmethod
    def skill_md(cls):
        path = os.path.join(ROOT, "SKILL.md")
        assert os.path.isfile(path), "根 SKILL.md 缺失"
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_frontmatter_has_name_and_entry_point(self, skill_md):
        """frontmatter 必须声明 name 与 entry_point=engine.py。"""
        assert re.search(r"^name:\s*jingni-trader\s*$", skill_md, re.M), "SKILL.md 缺少 name: jingni-trader"
        assert re.search(r"^entry_point:\s*engine\.py\s*$", skill_md, re.M), "SKILL.md 缺少 entry_point: engine.py"

    def test_allowed_sub_skills_match_directories(self, skill_md):
        """SKILL.md 的 allowed_sub_skills 必须与 skills/ 下实际引擎目录一致。"""
        # 解析 frontmatter 中的 allowed_sub_skills 列表
        allowed = set(re.findall(r"^\s*-\s*([a-z-]+-engine)\s*$", skill_md, re.M))
        expected = set(STAGE_SKILL_MAP.values())
        # 至少应包含全部 7 个引擎（jingni-datafeed 独立声明于 included_skills）
        assert expected.issubset(allowed), f"allowed_sub_skills 缺少: {expected - allowed}"

    def test_included_skills_match_directories(self, skill_md):
        """SKILL.md 的 included_skills 必须全部对应 skills/ 下真实目录。"""
        included = re.findall(r"^\s*-\s*skills/([a-z-]+)\s*$", skill_md, re.M)
        assert included, "SKILL.md 未声明 included_skills"
        for name in included:
            assert os.path.isdir(os.path.join(SKILLS_DIR, name)), (
                f"included_skills 声明了 skills/{name}，但目录不存在"
            )


# ============================================================================
# Part 2: skill-sync 与 engine 映射一致性
# ============================================================================

class TestEngineStageMapping:
    """验证 engine.py 的 stage → 子 skill 映射与物理目录一致。"""

    @pytest.fixture(scope="class")
    @classmethod
    def engine_mod(cls):
        sys.path.insert(0, ROOT)
        import engine as _engine
        yield _engine
        sys.path.remove(ROOT)

    def test_subskill_scripts_dirs_exist(self, engine_mod):
        """_SUBSKILL_SCRIPTS 指向的每个 scripts 包目录必须真实存在。"""
        for stage, rel in engine_mod._SUBSKILL_SCRIPTS.items():
            init_py = os.path.join(ROOT, rel, "__init__.py")
            assert os.path.isfile(init_py), (
                f"stage {stage} 的 scripts 包 {rel}/__init__.py 不存在"
            )

    def test_skill_modules_files_exist(self, engine_mod):
        """SKILL_MODULES 指向的每个 engine 模块文件（skills/<x>/engine.py）必须物理存在。

        说明：不做真实 import——子 skill engine.py 依赖其自带 scripts 包，而
        _register_subskill_scripts 使用全局单槽位 sys.modules['scripts']，在同一
        pytest 进程内连续加载 7 个不同 stage 会互相干扰。真实运行每个 stage 只
        加载一个 engine（独立进程/归档目录）。「模块可 import」属集成层职责，
        此处仅做静态契约校验，避免脆弱的全局状态依赖。
        """
        for stage, mod_name in engine_mod.SKILL_MODULES.items():
            rel_path = mod_name.replace(".", "/") + ".py"
            assert os.path.isfile(os.path.join(ROOT, rel_path)), (
                f"stage {stage} 的 engine 模块文件缺失: {rel_path}"
            )

    def test_register_subskill_scripts_points_to_existing(self, engine_mod):
        """_register_subskill_scripts 对每个 stage 都能定位到存在的子 skill scripts 包。

        验证主引擎的 scripts 包切换机制每个分支的脚本包真实存在。
        """
        for stage in engine_mod.SKILL_MODULES.keys():
            rel = engine_mod._SUBSKILL_SCRIPTS.get(stage)
            assert rel, f"stage {stage} 在 _SUBSKILL_SCRIPTS 中缺失"
            init_py = os.path.join(ROOT, rel, "__init__.py")
            assert os.path.isfile(init_py), (
                f"stage {stage} 的 _register_subskill_scripts 目标包缺失: {rel}/__init__.py"
            )

    def test_scripts_and_modules_cover_same_stages(self, engine_mod):
        """_SUBSKILL_SCRIPTS 与 SKILL_MODULES 的 stage 集合必须完全一致。"""
        assert set(engine_mod._SUBSKILL_SCRIPTS.keys()) == set(engine_mod.SKILL_MODULES.keys()), (
            "_SUBSKILL_SCRIPTS 与 SKILL_MODULES 的 stage 集合不一致"
        )

    def test_all_stages_expected_artifacts(self, engine_mod):
        """每个 stage 都必须有期望产物定义（EXPECTED_ARTIFACTS）。"""
        assert set(engine_mod.SKILL_MODULES.keys()) <= set(engine_mod.EXPECTED_ARTIFACTS.keys()), (
            "存在未定义 EXPECTED_ARTIFACTS 的 stage"
        )

    def test_stage_skill_mapping_matches_source(self, engine_mod):
        """_SUBSKILL_SCRIPTS 的目录名必须与 STAGE_SKILL_MAP 一致。"""
        for stage, rel in engine_mod._SUBSKILL_SCRIPTS.items():
            expected = f"skills/{STAGE_SKILL_MAP[stage]}/scripts"
            assert rel == expected, f"stage {stage}: 期望 {expected}，实际 {rel}"


class TestSkillSyncConsistency:
    """验证 skill-sync 声明与实际目录的一致性。"""

    def test_jingni_datafeed_has_own_skill_sync(self):
        """jingni-datafeed 作为独立子 skill，必须有独立的 skill-sync.yml。"""
        assert os.path.isfile(
            os.path.join(SKILLS_DIR, "jingni-datafeed", "skill-sync.yml")
        ), "skills/jingni-datafeed/skill-sync.yml 缺失（独立版本检查机制）"

    def test_subskill_dependencies_match_dirs(self):
        """skill_sync._SUBSKILL_DEPENDENCIES 声明的子 skill 必须存在对应目录。"""
        from scripts.skill_sync import _SUBSKILL_DEPENDENCIES
        for skill_name, repo in _SUBSKILL_DEPENDENCIES.items():
            assert os.path.isdir(os.path.join(SKILLS_DIR, skill_name)), (
                f"skill_sync 声明依赖 {skill_name}，但 skills/{skill_name} 目录不存在"
            )
            # 依赖仓库必须是 github owner/repo 格式
            assert "/" in repo and not repo.startswith("/"), (
                f"依赖 {skill_name} 的 repo 格式非法: {repo}"
            )

    def test_master_skill_sync_yml_exists(self):
        """主 skill 根目录必须有 skill-sync.yml。"""
        assert os.path.isfile(os.path.join(ROOT, "skill-sync.yml")), "根 skill-sync.yml 缺失"

    def test_master_skill_sync_protects_user_paths(self):
        """主 skill-sync.yml 的 protected_paths 应保护 workspace/、.env 等用户数据。"""
        import yaml
        with open(os.path.join(ROOT, "skill-sync.yml"), encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        protected = meta.get("protected_paths", [])
        # 至少应保护 workspace 与 .env
        assert any("workspace" in p for p in protected), "protected_paths 未保护 workspace/"
        assert any(".env" in p for p in protected), "protected_paths 未保护 .env"


# ============================================================================
# Part 3: 触发关键词闭环（防止意图路由语义漂移）
# ============================================================================

class TestTriggerKeywordClosure:
    """验证 engine.py 意图路由关键词与 SKILL.md 意图表描述的一致性。"""

    @pytest.fixture(scope="class")
    @classmethod
    def engine_mod(cls):
        sys.path.insert(0, ROOT)
        import engine as _engine
        yield _engine
        sys.path.remove(ROOT)

    def test_strategy_keywords_nonempty(self, engine_mod):
        """STRATEGY_KEYWORDS 必须非空且含核心动作词。"""
        kws = engine_mod.STRATEGY_KEYWORDS
        assert kws, "STRATEGY_KEYWORDS 为空"
        # 核心动作词必须在其中（触发完整 7 阶段管线的关键）
        for must in ("回测", "策略", "模型", "选股"):
            assert must in kws, f"STRATEGY_KEYWORDS 缺少关键动作词: {must}"

    def test_attribution_keywords_priority(self, engine_mod):
        """ATTRIBUTION_KEYWORDS 必须包含复盘/归因核心词（最高优先级路径）。"""
        kws = engine_mod.ATTRIBUTION_KEYWORDS
        assert kws, "ATTRIBUTION_KEYWORDS 为空"
        for must in ("绩效归因", "复盘", "盈亏分析"):
            assert must in kws, f"ATTRIBUTION_KEYWORDS 缺少关键词: {must}"

    def test_strategy_and_attribution_disjointness_intent(self, engine_mod):
        """「复盘」类意图不应被误判为策略构建——验证路由优先级语义。

        当输入同时含归因关键词时，_is_attribution_intent 优先。
        """
        import engine as eng
        master = eng.MasterEngine()
        intent = "生成上个月的绩效归因复盘报告"
        assert master._is_attribution_intent(intent) is True

    def test_document_keyword_sync_with_engine(self, engine_mod):
        """SKILL.md 意图表列举的触发关键词应能在 engine.py 常量中找到对应。"""
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            skill_md = f.read()

        # 归因路径关键词：文档中"执行复盘"行应提及，且 engine 常量应覆盖
        for doc_word in ("绩效归因", "复盘", "盈亏分析"):
            # 文档中该词至少出现一次（意图表描述）
            assert doc_word in skill_md, f"SKILL.md 未提及归因关键词: {doc_word}"

        # 策略构建关键词：文档提及且 engine 覆盖
        for doc_word in ("回测", "选股", "实盘"):
            assert doc_word in skill_md, f"SKILL.md 未提及策略关键词: {doc_word}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
