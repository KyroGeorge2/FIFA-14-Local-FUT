@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0server;%PYTHONPATH%"
title FIFA 14 Local FUT v2.41.1 BETA 2.25.9 - Friend Fresh Profile + Auto Prereqs
powershell -NoProfile -ExecutionPolicy Bypass -File ".\tools\launch_fifa14_hub_store.ps1"
if errorlevel 1 goto :failed

echo.
echo BETA session finished. Runtime diagnostics remain under artifacts\fifa14-local-fut-beta-v2411-beta2259-results.zip
pause
exit /b 0

:failed
echo.
echo BETA FAILED. Read the error above; any available diagnostics are under artifacts\.
pause
exit /b 1
