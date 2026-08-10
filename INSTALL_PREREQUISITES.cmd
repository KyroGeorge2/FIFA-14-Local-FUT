@echo off
setlocal
cd /d "%~dp0"
title FIFA 14 Local FUT - Install Prerequisites
powershell -NoProfile -ExecutionPolicy Bypass -File ".\tools\install_prerequisites.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo PREREQUISITES FAILED. Read the error above.
) else (
  echo PREREQUISITES COMPLETE. You can now run RUN_FIFA14_LOCAL_BETA.cmd.
)
pause
exit /b %RC%
