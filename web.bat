@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 이전에 켜 둔 웹앱이 있으면 종료합니다...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*-m web.server*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo 웹앱을 시작합니다. 브라우저가 자동으로 열립니다. (종료: Ctrl+C)
python -m web.server
pause
