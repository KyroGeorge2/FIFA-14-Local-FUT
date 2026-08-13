@echo off
setlocal
cd /d "%~dp0"
title FIFA 14 Local FUT - Diagnostics
powershell -NoProfile -ExecutionPolicy Bypass -File ".\tools\run_diagnostics.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo PROBLEMS FOUND. Read "What to do" above.
  echo A copy was saved to diagnostics-report.txt - attach it to your issue.
) else (
  echo NO BLOCKING PROBLEMS FOUND.
  echo A copy was saved to diagnostics-report.txt.
)
pause
exit /b %RC%
