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

**자막 번인 (2026-09-10 추가):** 사용자가 '흑백요리사 스타일 예능 자막'을 요청 → `video_pipeline/subtitles.py`가 컷 편집본 대본으로 ASS를 만들고 `render_final`이 ffmpeg `ass` 필터(libass)로 프레임에 입힌다. 스타일 `variety`: NanumSquare ExtraBold, 화면 높이 6.4%, 검정 외곽선 0.7%, 반투명 그림자, 하단 중앙, `\fad(60,60)`+88→100% 팝. 강조어(`--subtitle-emphasis`)는 노란색(&H0000E5FF). 짧은 자막은 0.8초로 늘리고 20자 넘으면 가운데 띄어쓰기에서 두 줄. 클라우드 VM에는 한글 굵은 폰트가 없어 `fonts-nanum fonts-nanum-extra`를 apt로 설치해야 한다(setup script에 추가). `-ss`를 입력 옵션으로 두고 프레임을 뽑으면 타임스탬프가 0부터 시작해 자막이 안 보이니 `-copyts`를 붙여 확인한다.

**전사 교정 (2026-09-10 추가):** 사용자가 '뱃살→곰돌이(bear), 마오차→말차' 교정을 요청 → `--fix`/`TRANSCRIPT_FIXES`로 `Transcript.apply_fixes`가 단어·문장을 치환. `Job._fix`가 캐시 로드 때마다 적용해 멱등이고, `ensure_cut` 캐시 경로에서 SRT도 다시 쓴다. 기획(plan.json)은 별도라 프롬프트를 손으로 고치고 `image_path`를 비워 재생성했다(말차 vs 아이스 아메리카노, 곰돌이 아이콘). 교훈: 영상 화면(여자는 말차, 남자는 아아)을 보면 전사 오류를 잡을 수 있으니 기획 전에 프레임 몇 장을 확인할 것.

**2026-09-10 추가 기능:** (1) `--transcribe openai`: whisper-1 API 전사(`transcribe_openai`). 로컬 medium과 결과가 꽤 달랐고(로컬 '괜찮은데?/뱃살' vs API '시원해/bear') API 쪽이 문맥상 자연스러웠다. (2) `--subtitle-tone mz`: `subtitles.rewrite_tone`이 OpenAI 텍스트 모델로 줄 단위 변환, `tone_<tone>.json` 캐시(키=말투+모델+원문 해시). 결과에 '~임' 남발·없는 말 추가('할게')가 섞이므로 캐시 `lines`를 손으로 다듬는 절차를 둔다. (3) `fx.json` 예능 효과: `fx.FrameFx`가 프레임 루프에서 줌(중앙 crop→resize)·흑백 플래시(비네트)를 적용하고, 임팩트 자막은 ASS `Impact` 스타일(상단 중앙 9%H), 효과음 `dudung`/`whip`은 `sfx.py`에 합성. 사용자 요청 배경: '흑백요리사 예능 같은 효과'.
