@echo off
REM jingni-trader 三方库安装包装脚本（Windows）。
REM 默认使用国内加速镜像（清华 TUNA）；可用 PIP_INDEX_URL 环境变量或
REM --index-url 参数覆盖，参数原样透传给 install.py。
setlocal
cd /d "%~dp0"
python install.py %*
exit /b %ERRORLEVEL%
