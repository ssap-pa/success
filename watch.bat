@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo inbox 폴더를 감시합니다. 영상을 inbox 폴더에 넣으면 자동 편집됩니다.
python watch_inbox.py
pause
