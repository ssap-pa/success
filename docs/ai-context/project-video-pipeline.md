---
name: project-video-pipeline
description: success 폴더의 유튜브 자동 편집 파이프라인(video_pipeline + web 앱) 현황과 환경 제약
metadata: 
  node_type: memory
  type: project
  originSessionId: 30037aea-406e-4b28-95aa-ace6434a0388
  modified: 2026-09-08T02:47:41.936Z
---

2026-09-08에 `success/` 에 만든 것: `video_pipeline/` 패키지(전사 → 컷 → Claude 기획 → gpt-image-1 생성 → EasyOCR 모자이크 → 렌더, `Job` 클래스로 단계별 실행)와 Flask 로컬 웹앱 `web/server.py` (포트 8766, `web.bat`). 폴더 감시는 `watch_inbox.py`.

사용자 요구 확정 사항: 일러스트는 **카드 없이 화면 중앙 투명 스티커**(overlay, 높이 62%) 기본, 로고·아이콘도 **투명 PNG 스티커**(높이 32%), 등장·퇴장 팝 애니메이션 + 합성 효과음, 그림체는 굵은 검정 외곽선 + 플랫 컬러(옐로·네이비·코랄·오렌지·러스트), 그림 안 글자 없음.

**환경 제약:**
- PC: RTX 4070 Ti 12GB, Python 3.12 (C:\Python312 시스템 설치 → `pip install --user` 필요), ffmpeg /c/FFmpeg/bin, NVENC 가능. faster-whisper large-v3 캐시됨.
- `.env`에 ANTHROPIC_API_KEY, OPENAI_API_KEY 있음 (2026-09-08 사용자가 직접 넣음). 실제 Claude 기획·gpt-image-1 생성 동작 확인.
- 포트 8765는 사용자의 다른 앱(autotube, uvicorn)이 점유 → 웹앱은 8766.
- Git Bash에서 여러 heredoc을 한 명령에 넣으면 파싱 실패 → 긴 파일은 Write 도구. PowerShell 5.1은 BOM 없는 .ps1의 한글을 깨뜨림.
- 사용자는 이미 실제 영상("쓰레드 댓글 승인…")을 이 파이프라인으로 처리해 봤음 (output/에 결과 존재).

- `.bat`는 CP949로 저장하고 `chcp 65001`을 쓰지 않는다 (UTF-8+chcp 조합은 cmd가 배치 파일을 잘못 읽어 서버가 안 뜸, 2026-09-11 확인). 웹앱은 소스 변경 시 자동 재시작(종료 코드 3 → web.bat 루프).

**How to apply:** 이어서 작업할 때 위 제약을 전제로 하고, 테스트는 `samples/test_video.mp4` (SAPI Heami TTS + drawtext 개인정보 오버레이)로 하면 30초 안에 끝난다.
