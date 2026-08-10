@echo off
setlocal
cd /d "%~dp0"
title FIFA 14 Local FUT - Push to GitHub

echo ============================================================
echo  FIFA 14 LOCAL FUT - PUSH TO GITHUB
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git was not found. Run INSTALL_PREREQUISITES.cmd first.
  pause
  exit /b 1
)

if not exist ".git\" (
  echo [ERROR] This folder is not initialised yet.
  echo Run SETUP_GITHUB_REPO.cmd first.
  pause
  exit /b 1
)

set /p "REPO_URL=Paste the EMPTY GitHub repository URL: "
if "%REPO_URL%"=="" (
  echo [ERROR] No repository URL entered.
  pause
  exit /b 1
)

echo.
echo Setting origin to:
echo   %REPO_URL%

git remote get-url origin >nul 2>nul
if errorlevel 1 (
  git remote add origin "%REPO_URL%"
) else (
  git remote set-url origin "%REPO_URL%"
)
if errorlevel 1 goto :failed

git branch -M main
if errorlevel 1 goto :failed

echo.
echo Pushing main branch...
echo Git for Windows may open a browser window for GitHub sign-in.
git push -u origin main
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo  PUSH COMPLETE
echo ============================================================
echo Your repository source is now on GitHub.
echo Use PACKAGE_RELEASE.cmd when you want a clean downloadable ZIP.
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Push failed. Check the repository URL and GitHub authentication.
pause
exit /b 1
