@echo off
rem Installs TacoSort for you (no administrator needed). The work is done by install.ps1 next to this file.
title Install TacoSort
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
