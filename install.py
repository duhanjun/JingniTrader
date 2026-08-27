#!/usr/bin/env python3
"""jingni-trader 三方库安装脚本（跨平台，纯标准库，零依赖）。

默认使用国内加速镜像（清华 TUNA）安装 `requirements.txt`，避免从官方 PyPI
（美国）慢速拉取；用户可通过 `--index-url` 参数或 `PIP_INDEX_URL` 环境变量覆盖。

镜像优先级（高 → 低）：
  1. `--index-url` 命令行参数
  2. `PIP_INDEX_URL` 环境变量
  3. 默认清华 TUNA 镜像

用法：
  python install.py                                        # 默认清华镜像
  python install.py --index-url https://mirrors.aliyun.com/pypi/simple
  PIP_INDEX_URL=https://my.mirror/simple python install.py # 环境变量覆盖
  python install.py --requirements skills/jingni-datafeed/requirements.txt
  python install.py --dry-run                              # 只打印命令，不执行

说明：`requirements.txt` 内不写镜像源（pip 不支持在 requirements 内指定 index），
镜像统一经本脚本 `-i` 参数或 `PIP_INDEX_URL` 环境变量注入。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 默认国内加速镜像（清华 TUNA）。与 skills/data-engine/engine.py 的
# _DEFAULT_PIP_INDEX_URL 保持一致，双处同为清华镜像。
DEFAULT_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"

# 脚本所在目录 = jingni-trader skill 根目录
SKILL_ROOT = Path(__file__).resolve().parent

# 默认依赖清单（skill 根 requirements.txt）
DEFAULT_REQUIREMENTS = "requirements.txt"


def resolve_requirements(target: str | None) -> Path:
    """解析要安装的 requirements.txt 路径。

    - `target` 为 None：取 skill 根目录的 `requirements.txt`
    - `target` 为目录：取该目录下的 `requirements.txt`
    - `target` 为文件：直接使用
    相对路径一律相对 skill 根目录解析（保证从任意 cwd 调用行为一致）。
    """
    rel = target or DEFAULT_REQUIREMENTS
    path = Path(rel)
    if not path.is_absolute():
        path = SKILL_ROOT / path
    if path.is_dir():
        path = path / DEFAULT_REQUIREMENTS
    if not path.is_file():
        raise FileNotFoundError(f"依赖清单不存在: {path}")
    return path


def resolve_index_url(cli_index_url: str | None, env: dict[str, str] | None = None) -> str:
    """按优先级解析最终生效的 pip 镜像源。

    `--index-url` 参数 > `PIP_INDEX_URL` 环境变量 > 默认清华镜像。
    """
    environ = os.environ if env is None else env
    if cli_index_url:
        return cli_index_url
    env_url = environ.get("PIP_INDEX_URL")
    if env_url:
        return env_url
    return DEFAULT_INDEX_URL


def build_command(
    requirements: Path,
    index_url: str,
    extra_pip_args: list[str] | None = None,
) -> list[str]:
    """构造 `pip install` 命令（使用当前解释器，避免 pip 与 python 版本错配）。"""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(requirements),
        "-i",
        index_url,
    ]
    if extra_pip_args:
        cmd += list(extra_pip_args)
    return cmd


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="jingni-trader 三方库安装（默认国内加速镜像：清华 TUNA）",
    )
    parser.add_argument(
        "--index-url",
        dest="index_url",
        default=None,
        help=f"pip 镜像源，覆盖默认值（默认 {DEFAULT_INDEX_URL}；亦可用 PIP_INDEX_URL 环境变量）",
    )
    parser.add_argument(
        "--requirements",
        dest="requirements",
        default=None,
        help=f"依赖清单路径或所在目录（默认 skill 根目录 {DEFAULT_REQUIREMENTS}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的命令，不实际安装",
    )
    parser.add_argument(
        "pip_args",
        nargs=argparse.REMAINDER,
        help="`--` 之后的参数原样透传给 pip（如 --upgrade --no-cache-dir）",
    )
    return parser.parse_args(argv)


def _clean_pip_args(raw: list[str]) -> list[str]:
    """去掉 REMAINDER 首个分隔符 `--`，其余原样透传。"""
    if raw and raw[0] == "--":
        return raw[1:]
    return list(raw)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        requirements = resolve_requirements(args.requirements)
    except FileNotFoundError as exc:
        print(f"[install] 错误：{exc}", file=sys.stderr)
        print(
            "[install] 请确认在 jingni-trader skill 目录下执行，或用 --requirements 指定清单路径。",
            file=sys.stderr,
        )
        return 2

    index_url = resolve_index_url(args.index_url)
    source = (
        "--index-url 参数"
        if args.index_url
        else ("PIP_INDEX_URL 环境变量" if os.environ.get("PIP_INDEX_URL") else "默认清华 TUNA 镜像")
    )
    cmd = build_command(requirements, index_url, _clean_pip_args(args.pip_args))

    print("[install] jingni-trader 依赖安装")
    print(f"[install]   依赖清单：{requirements}")
    print(f"[install]   镜像源  ：{index_url}（来源：{source}）")
    print(f"[install]   执行命令：{' '.join(cmd)}")

    if args.dry_run:
        print("[install] --dry-run 已启用，未实际安装。")
        return 0

    try:
        proc = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print(f"[install] 失败：无法调用 pip（解释器 {sys.executable}）。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[install] 已被用户中断。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 兜底提示，避免裸栈直吐用户
        print(f"[install] 执行异常：{exc}", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        print(
            f"[install] 安装失败（pip 退出码 {proc.returncode}）。\n"
            f"[install] 排查建议：\n"
            f"[install]   1. 网络不通/镜像不可用 → 换源：python install.py --index-url <其他镜像>\n"
            f"[install]   2. 备选镜像：https://mirrors.aliyun.com/pypi/simple、"
            f"https://mirrors.cloud.tencent.com/pypi/simple\n"
            f"[install]   3. 某些包需系统级 C 库（如 TA-Lib，本 skill 默认后端 pandas_ta 不需要）",
            file=sys.stderr,
        )
        return proc.returncode

    print("[install] 安装完成 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
