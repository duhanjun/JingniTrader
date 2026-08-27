#!/usr/bin/env bash
# jingni-trader 三方库安装包装脚本（Linux / macOS）。
# 默认使用国内加速镜像（清华 TUNA）；可用 PIP_INDEX_URL 环境变量或
# --index-url 参数覆盖，参数原样透传给 install.py。
set -euo pipefail
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" install.py "$@"
