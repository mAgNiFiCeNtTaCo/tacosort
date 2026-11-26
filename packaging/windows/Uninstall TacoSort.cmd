@echo off
rem Removes TacoSort from this computer. The work is done by uninstall.ps1 next to this file.
rem One line on purpose: this file deletes itself, so nothing may be read from it afterwards.
title Uninstall TacoSort
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" & echo. & pause & exit /b
