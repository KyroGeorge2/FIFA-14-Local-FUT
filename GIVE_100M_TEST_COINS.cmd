@echo off
setlocal
cd /d "%~dp0"
title FIFA 14 Local FUT - Optional 100M Test Coins
powershell -NoProfile -ExecutionPolicy Bypass -File ".\tools\give_100m_test_coins.ps1"
pause
