@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_webrtc_dev_server.ps1" -Profile hetzner-relay-only %*
pause
