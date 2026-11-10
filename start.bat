@echo off
rem Double-click to run Riptide. Opens the web UI in your browser.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
