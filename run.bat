@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 사용법: run.bat 영상파일.mp4  ^(또는 영상 파일을 이 배치파일 위로 드래그^)
  pause
  exit /b 1
)
python -m video_pipeline %*
echo.
echo 결과는 output 폴더에 있습니다.
pause
