#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""三方一致性校验：主目录 ↔ 仓库副本 ↔ GitHub。

用途
----
版本漂移能被自动检出，而非靠人肉发现（OPEN-2026-0827-12）。

三方定义
--------
- A 主目录：本脚本所在仓库（d:/codebuddy/jingni-trader）
- B 仓库副本：core/skills/jingni-tech/jingni-trader（单一权威源，见 OPEN-2026-0827-05）
- C GitHub：origin/main 远端

检查项
------
1. git-dirty  ：主目录是否存在未提交改动（排除运行时产物）
2. unpushed   ：主目录是否领先 origin/main
3. unprotected：副本是否已被 GitHub 反向覆盖（副本独有的受保护文件丢失）
4. drift      ：主目录与副本共有源码文件的内容漂移（可选，--deep 开启）
5. tracked-artifact：版本库中是否混入了运行时产物（.log/.pkl/.db/.parquet/.pyc 等）

退出码
------
0 = 全部通过；1 = 存在 ERROR 级问题。

零新依赖：仅 Python 标准库。

用法
----
    python scripts/check_sync_consistency.py
    python scripts/check_sync_consistency.py --deep          # 追加内容漂移比对
    python scripts/check_sync_consistency.py --json          # 机器可读输出
    python scripts/check_sync_consistency.py --list-protected
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

# ── 配置 ────────────────────────────────────────────────────────────────────

REPO_A = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 仓库副本路径：默认按 jingni-agents 目录结构推断，可用环境变量覆盖
DEFAULT_REPO_B = os.path.join(
    os.path.dirname(REPO_A), "jingni-agents", "core", "skills", "jingni-tech", "jingni-trader"
)
REPO_B = os.environ.get("JINGNI_TRADER_COPY", DEFAULT_REPO_B)

REMOTE = "origin"
BRANCH = "main"

# 副本独有、且一旦被 GitHub 反向覆盖就会永久丢失的资产（OPEN-2026-0827-05）
PROTECTED_PATHS: Tuple[str, ...] = (
    # westock / local 后端适配器（11-backend 架构新增，主目录缺失）
    "skills/data-engine/scripts/adapters/westock_adapter.py",
    "skills/data-engine/scripts/adapters/local_adapter.py",
    "skills/data-engine/scripts/cache_incremental.py",
    # 数据源参考文档（实测副本 references/ 下 7 份 *_data.md + data_cleaning_rules.md）
    "skills/data-engine/references/westock_data.md",
    "skills/data-engine/references/akshare_data.md",
    "skills/data-engine/references/baostock_data.md",
    "skills/data-engine/references/gm_data.md",
    "skills/data-engine/references/tdxquant_data.md",
    "skills/data-engine/references/xtquant_data.md",
    "skills/data-engine/references/data_cleaning_rules.md",
    # 副本演进产物
    "skills/backtest-engine/scripts/optimizations/",
    "install.py",
    "install.sh",
    "install.bat",
    "MANIFEST.yaml",
    "sync-baseline.yml",
)

# 运行时产物：不得出现在版本库中
ARTIFACT_PATTERNS: Tuple[str, ...] = (
    ".log", ".pkl", ".db", ".sqlite", ".sqlite3", ".parquet", ".pyc", ".pyo",
)
ARTIFACT_DIRS: Tuple[str, ...] = (
    "workspace/", "mlruns/", ".pytest_cache/", "__pycache__/", "_e2e_workdir",
    "_reports_e2e/", "audit/", ".codebuddy/",
)

# 内容漂移比对时跳过的路径
DRIFT_EXCLUDES: Tuple[str, ...] = (
    ".git", "__pycache__", ".pytest_cache", "mlruns", "workspace",
    "_e2e_workdir", "_reports_e2e", "audit", ".codebuddy", "docs",
)


# ── 基础工具 ────────────────────────────────────────────────────────────────

def _git(args: List[str], cwd: str = REPO_A) -> Tuple[int, str]:
    """执行 git 命令，返回 (returncode, stdout)。失败不抛异常。"""
    try:
        r = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, timeout=60
        )
        return r.returncode, r.stdout.decode("utf-8", "replace").strip()
    except FileNotFoundError:
        return 127, "git 命令不可用"
    except subprocess.TimeoutExpired:
        return 124, "git 命令超时（60s）"


def _is_artifact(path: str) -> bool:
    """判断路径是否为运行时产物。"""
    normalized = path.replace("\\", "/")
    for d in ARTIFACT_DIRS:
        if normalized.startswith(d) or ("/" + d) in normalized:
            return True
    return normalized.endswith(ARTIFACT_PATTERNS)


# ── 检查项 ──────────────────────────────────────────────────────────────────

def check_git_dirty() -> Dict[str, Any]:
    """1. 主目录未提交改动（排除运行时产物）。"""
    rc, out = _git(["status", "--porcelain"])
    if rc != 0:
        return {"id": "git-dirty", "ok": False, "level": "ERROR",
                "detail": "git status 执行失败: %s" % out}
    dirty = [l[3:] for l in out.splitlines() if l.strip()]
    real = [p for p in dirty if not _is_artifact(p)]
    return {
        "id": "git-dirty",
        "ok": not real,
        "level": "WARN",
        "detail": "主目录未提交改动 %d 项（另有 %d 项运行时产物已忽略）"
                  % (len(real), len(dirty) - len(real)),
        "items": real[:20],
        "total": len(real),
    }


def check_unpushed() -> Dict[str, Any]:
    """2. 主目录是否领先远端。"""
    rc, out = _git(["rev-list", "--left-right", "--count",
                    "%s/%s...HEAD" % (REMOTE, BRANCH)])
    if rc != 0:
        return {"id": "unpushed", "ok": False, "level": "WARN",
                "detail": "无法读取远端（离线?）: %s" % out}
    parts = out.split()
    if len(parts) != 2:
        return {"id": "unpushed", "ok": False, "level": "WARN",
                "detail": "rev-list 输出异常: %r" % out}
    behind, ahead = int(parts[0]), int(parts[1])
    ok = (behind == 0 and ahead == 0)
    return {
        "id": "unpushed",
        "ok": ok,
        "level": "WARN",
        "detail": "behind=%d ahead=%d（期望 0 0）" % (behind, ahead),
        "behind": behind,
        "ahead": ahead,
    }


def check_unprotected() -> Dict[str, Any]:
    """3. 副本受保护资产是否丢失（防 GitHub 反向覆盖）。"""
    if not os.path.isdir(REPO_B):
        return {"id": "unprotected", "ok": True, "level": "SKIP",
                "detail": "仓库副本不存在，跳过: %s" % REPO_B}
    missing = [p for p in PROTECTED_PATHS
               if not os.path.exists(os.path.join(REPO_B, p))]
    return {
        "id": "unprotected",
        "ok": not missing,
        "level": "ERROR",
        "detail": "受保护资产缺失 %d/%d 项" % (len(missing), len(PROTECTED_PATHS)),
        "items": missing,
        "total": len(PROTECTED_PATHS),
    }


def check_tracked_artifact() -> Dict[str, Any]:
    """5. 版本库中是否混入运行时产物。"""
    rc, out = _git(["ls-files"])
    if rc != 0:
        return {"id": "tracked-artifact", "ok": False, "level": "ERROR",
                "detail": "git ls-files 失败: %s" % out}
    files = [l for l in out.splitlines() if l.strip()]
    bad = [f for f in files if _is_artifact(f)]
    return {
        "id": "tracked-artifact",
        "ok": not bad,
        "level": "ERROR",
        "detail": "版本库混入运行时产物 %d 项（应 git rm --cached）" % len(bad),
        "items": bad[:20],
        "total": len(bad),
    }


def _walk(root: str) -> List[str]:
    """遍历目录下所有文件，返回相对路径（跳过 DRIFT_EXCLUDES）。"""
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DRIFT_EXCLUDES]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append(rel)
    return out


def check_drift() -> Dict[str, Any]:
    """4. 主目录与副本共有源码文件的内容漂移（--deep 才跑，较慢）。"""
    if not os.path.isdir(REPO_B):
        return {"id": "drift", "ok": True, "level": "SKIP",
                "detail": "仓库副本不存在，跳过"}
    fa, fb = set(_walk(REPO_A)), set(_walk(REPO_B))
    common = sorted(fa & fb)
    diff: List[str] = []
    for rel in common:
        if rel.endswith((".png", ".jpg", ".ico", ".pkl")):
            continue
        pa, pb = os.path.join(REPO_A, rel), os.path.join(REPO_B, rel)
        try:
            with open(pa, "rb") as x, open(pb, "rb") as y:
                if x.read() != y.read():
                    diff.append(rel)
        except OSError:
            continue
    return {
        "id": "drift",
        "ok": True,  # 漂移本身是信息，不是错误——方向由人判定
        "level": "INFO",
        "detail": "共有文件 %d 个，其中内容不同 %d 个" % (len(common), len(diff)),
        "common": len(common),
        "differing": len(diff),
        "items": diff[:30],
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────

def run_all(deep: bool = False) -> Dict[str, Any]:
    checks = [
        check_tracked_artifact(),   # ERROR 级优先
        check_unprotected(),        # ERROR 级
        check_unpushed(),           # WARN
        check_git_dirty(),          # WARN
    ]
    if deep:
        checks.append(check_drift())
    errors = [c for c in checks if c["level"] == "ERROR" and not c["ok"]]
    warnings = [c for c in checks if c["level"] == "WARN" and not c["ok"]]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "checks": checks,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="三方一致性校验：主目录 ↔ 仓库副本 ↔ GitHub")
    parser.add_argument("--deep", action="store_true",
                        help="追加内容漂移比对（较慢）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="机器可读 JSON 输出")
    parser.add_argument("--list-protected", action="store_true",
                        help="列出受保护资产清单后退出")
    parser.add_argument("--repo-b", default=None,
                        help="仓库副本路径（默认环境变量 JINGNI_TRADER_COPY 或推断）")
    args = parser.parse_args(argv)

    global REPO_B
    if args.repo_b:
        REPO_B = args.repo_b

    if args.list_protected:
        for p in PROTECTED_PATHS:
            mark = "OK " if os.path.exists(os.path.join(REPO_B, p)) else "MISS"
            print("[%s] %s" % (mark, p))
        return 0

    result = run_all(deep=args.deep)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    print("=" * 66)
    print("三方一致性校验  主目录 ↔ 仓库副本 ↔ GitHub")
    print("=" * 66)
    print("主目录 : %s" % REPO_A)
    print("副本   : %s%s" % (REPO_B, "" if os.path.isdir(REPO_B) else "  (不存在)"))
    print("远端   : %s/%s" % (REMOTE, BRANCH))
    print("-" * 66)
    for c in result["checks"]:
        if c["level"] == "SKIP":
            mark = "SKIP"
        else:
            mark = "PASS" if c["ok"] else ("FAIL" if c["level"] == "ERROR" else "WARN")
        print("[%s] %-16s %s" % (mark, c["id"], c["detail"]))
        for it in c.get("items", [])[:8]:
            print("         - %s" % it)
        if c.get("total", 0) > 8:
            print("         ... 其余 %d 项省略" % (c["total"] - 8))
    print("-" * 66)
    print("结论: %s  (ERROR=%d WARN=%d)"
          % ("PASS" if result["ok"] else "FAIL", result["errors"], result["warnings"]))
    print("=" * 66)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
