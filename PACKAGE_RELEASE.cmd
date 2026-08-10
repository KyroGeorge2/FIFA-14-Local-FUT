@echo off
setlocal
cd /d "%~dp0"
title FIFA 14 Local FUT - Package Release
powershell -NoProfile -ExecutionPolicy Bypass -File ".\tools\package_release.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo RELEASE PACKAGING FAILED. Read the error above.
) else (
  echo RELEASE PACKAGE READY under dist\
)
pause
exit /b %RC%
