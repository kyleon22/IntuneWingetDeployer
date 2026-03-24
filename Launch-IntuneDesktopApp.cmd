@echo off
cd /d %~dp0
where python >nul 2>nul
if %errorlevel% neq 0 (
  py -3 intune_desktop_app.py
) else (
  python intune_desktop_app.py
)
pause
