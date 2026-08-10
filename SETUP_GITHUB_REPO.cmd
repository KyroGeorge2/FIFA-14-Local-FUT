@echo off
setlocal
cd /d "%~dp0"
title FIFA 14 Local FUT - Git Repository Setup

echo ============================================================
echo  FIFA 14 LOCAL FUT - GIT REPOSITORY SETUP
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git was not found.
  echo Run INSTALL_PREREQUISITES.cmd first, or install Git for Windows.
  pause
  exit /b 1
)

if exist ".git\" (
  echo [INFO] This folder is already a Git repository.
) else (
  echo [1/3] Initialising repository...
  git init -b main
  if errorlevel 1 goto :failed
)

echo [2/3] Staging repository files...
git add .
if errorlevel 1 goto :failed

git diff --cached --quiet
if not errorlevel 1 (
  echo [INFO] Nothing new to commit.
) else (
  echo [3/3] Creating initial commit...
  git commit -m "Initial FIFA 14 Local FUT release"
  if errorlevel 1 (
    echo.
    echo Git may need your name/email configured first. Example:
    echo   git config --global user.name "Your Name"
    echo   git config --global user.email "you@example.com"
    echo Then run this file again.
    goto :failed
  )
)

echo.
echo ============================================================
echo  LOCAL GIT REPOSITORY READY
echo ============================================================
echo Create an EMPTY repository on GitHub, then run:
echo   PUSH_TO_GITHUB.cmd
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Git setup failed. Read the message above.
pause
exit /b 1
