@echo off
cd /d "%~dp0"

REM 传入 dry 参数时仅预览、不写入：run.bat dry
set "ARGS="
if /i "%1"=="dry" set "ARGS=--dry-run"

REM 运行时探测：优先 WorkBuddy 托管 Python，回退系统 python / py
set "PY="
if exist "C:\Users\amw\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PY=C:\Users\amw\.workbuddy\binaries\python\versions\3.13.12\python.exe"
) else (
    where py >nul 2>nul && set "PY=py"
    if not defined PY ( where python >nul 2>nul && set "PY=python" )
)

if not defined PY (
    echo [error] 未找到 Python 运行时，请先安装 Python 或 WorkBuddy。
    pause
    exit /b 1
)

%PY% cherry_to_workbuddy.py %ARGS%
pause
