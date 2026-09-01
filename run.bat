@echo off
title cherry2workbuddy migration
pushd "%~dp0"

echo ============================================================
echo   Cherry Studio to WorkBuddy model migration
echo ============================================================
echo.

REM pass "dry" to preview only, no write: run.bat dry
set "ARGS="
if /i "%~1"=="dry" set "ARGS=--dry-run"

REM detect python: prefer WorkBuddy managed python, fallback to py / python
set "PY="
if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
) else (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo [ERROR] Python runtime not found. Please install Python or WorkBuddy.
    echo.
    pause
    popd
    exit /b 1
)

echo [info] Python : %PY%
echo [info] Workdir: %CD%
echo [info] Args   : %ARGS%
echo.

%PY% cherry_to_workbuddy.py %ARGS%
set "RC=%errorlevel%"

echo.
if %RC%==0 (
    echo [OK] Done. models.json overwritten.
) else (
    echo [FAIL] Script exited with code: %RC%
)
echo.
echo ============================================================
echo   Sync WorkBuddy custom models -^> CodeBuddy
echo ============================================================
echo.

%PY% sync_wb_models_to_cb.py %ARGS%
set "RC2=%errorlevel%"

echo.
if %RC2%==0 (
    echo [OK] CodeBuddy models.json synced.
) else (
    echo [FAIL] CodeBuddy sync exited with code: %RC2%
)

echo.
echo Press any key to close...
pause >nul
popd
