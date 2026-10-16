@echo off
title Plant Data Collector - measuring session (read only)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Session
pause
