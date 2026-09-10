# success — 유튜브 영상 1차 자동 편집 파이프라인

저장소: https://github.com/ssap-pa/success (공개, 브랜치 `main`). 다른 AI 도구용 안내는 `agent.md`.

## 먼저 읽을 것

작업을 시작하기 전에 `docs/ai-context/` 폴더를 읽는다. 이 폴더는 로컬 클로드 메모리를 저장소 안으로 옮긴 것이다.

- `docs/ai-context/MEMORY.md` — 색인
- `docs/ai-context/project-video-pipeline.md` — 파이프라인 구조, 확정된 사용자 요구, PC 환경 제약
- `docs/ai-context/feedback-ocr-speed.md` — OCR 속도·오탐에 대한 사용자 피드백과 적용 내역

새로 알게 된 사실이나 사용자 피드백은 로컬 메모리에만 두지 말고 `docs/ai-context/`에도 같은 형식(frontmatter + 본문)으로 추가한다. 그래야 다른 컴퓨터와 클라우드 세션에서도 보인다.

## 프로젝트 개요

영상을 넣으면 전사 → 컷 편집 → Claude 시각 기획 → gpt-image-1 이미지 생성 → EasyOCR 개인정보 모자이크 → 최종 렌더 순으로 처리해 `output/`에 결과를 만든다. 상세 사용법은 `README.md`.

- `video_pipeline/` — 파이프라인 패키지. `Job` 클래스로 단계별 실행 (`transcribe → cut → plan → assets → pii → render`)
- `web/server.py` — Flask 로컬 웹앱, 포트 8766, `web.bat`으로 실행 (이전 인스턴스 자동 종료)
- `watch_inbox.py` — `inbox/` 폴더 감시 자동 실행
- `로고/` — 서비스 로고 PNG (`한글_영문_로고.png` 파일명 규칙)
- `samples/test_video.mp4` — 30초 안에 끝나는 테스트 영상

## 사용자 확정 요구 (바꾸지 말 것)

- 일러스트는 화면 중앙 투명 스티커 기본(`illustration_mode=overlay`). 카드 방식은 옵션
- 로고·아이콘은 투명 PNG 스티커 오버레이. 등장·퇴장 시 팝 애니메이션과 합성 효과음
- 그림체: 굵은 검정 외곽선 + 플랫 컬러(머스터드 옐로·네이비·코랄·오렌지·러스트), 그림 안에 글자 없음
- 인식된 단어는 컷 편집에서 잘라내지 않음
- 얼굴 모자이크와 말로 읊은 개인정보 음소거는 기본 OFF

## 환경 제약

- Python 3.12 시스템 설치 → `pip install --user` 필요
- ffmpeg는 `/c/FFmpeg/bin`, NVENC 사용 가능. GPU는 RTX 4070 Ti 12GB
- 포트 8765는 다른 앱이 점유 → 웹앱은 8766 고정
- Git Bash에서 heredoc 여러 개를 한 명령에 넣으면 깨진다. 긴 파일은 Write 도구로 쓴다
- PowerShell 5.1은 BOM 없는 `.ps1`의 한글을 깨뜨린다
- 웹앱은 `web.bat`으로 실행하면 소스(`video_pipeline/*.py`, `web/*.py`) 변경을 감지해 작업이 없을 때 스스로 재시작하고(종료 코드 3 → bat 루프), 브라우저도 자동 새로고침된다. `python -m web.server`로 직접 실행하면 변경 시 그냥 종료된다
- `.bat` 파일은 **CP949(ANSI)로 저장하고 `chcp 65001`을 쓰지 않는다.** UTF-8 + chcp 조합은 cmd가 배치 파일을 잘못 읽어 실행이 깨진다. 수정할 때는 Python으로 `encode("cp949")` 해서 쓴다

## 테스트

기능을 바꾸면 `samples/test_video.mp4`로 확인한다.

```bash
python -m video_pipeline samples/test_video.mp4
python -m video_pipeline samples/test_video.mp4 --steps render   # 특정 단계만
```

클라우드 세션(claude.ai/code)에서 실행할 때는 `docs/cloud-setup.md`를 먼저 읽는다. GPU·ffmpeg가 없고 네트워크 허용 도메인과 환경변수 설정이 필요하다.

## 사용자 지식창고 (클라우드 세션에서도 적용)

사용자의 전문 지식은 비공개 저장소 **https://github.com/ssap-pa/DB** (브랜치 `backup`)에 있다. 원문은 `read/<창고>/<문서ID>.md`, 창고별 목차는 `read/<창고>/README.md`.

| 창고 | 내용 | 사용 |
|---|---|---|
| `read/adsense` | 애드센스·유튜브·블로그 수익화 강의 | 사용 가능 |
| `read/writing-craft` | 글쓰기 자료 | 사용 가능 |
| `read/threads-algo` | Threads 알고리즘 | 사용 가능 |
| `read/ssapable` | 사용자 본인 강의 | **금지. 사용자가 다시 승인하기 전까지 내려받지도, 읽지도 않는다** |

- 수익화·블로그·애드센스·유튜브·글쓰기·SNS 알고리즘 관련 답변이나 콘텐츠 제작을 요청받으면 일반 지식보다 이 창고를 먼저 근거로 삼는다. 코드 작업에는 가져오지 않는다.
- 전체를 읽기엔 크다(마크다운 740개, 12MB). 허용된 창고만 sparse clone으로 임시 폴더에 받아 grep으로 후보를 찾고 관련 문서만 읽는다.

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/ssap-pa/DB.git /tmp/DB
cd /tmp/DB && git sparse-checkout set read/adsense read/writing-craft read/threads-algo
```

- 답변에 근거 문서(창고/문서ID)를 밝힌다. 창고에 근거가 없으면 없다고 말하고, 일반 지식으로 보충할 때는 구분해서 표시한다.
- 창고 내용은 비공개 자료다. 외부 공개 산출물에 쓸 때는 사용자에게 먼저 확인한다.

## 절대 하지 말 것

- `.env`를 커밋하거나 내용을 출력하지 않는다. API 키는 `.env.example`의 키 이름만 참고한다
- `inbox/`, `uploads/`, `output/`, `work/`는 사용자 개인 영상이다. 저장소에 올리지 않고 내용을 임의로 삭제하지 않는다
- 이 저장소는 **공개**다. 개인정보·키·사용자 영상·지식창고 원문을 커밋하지 않는다
- OCR 처리 시간을 늘리는 변경은 먼저 사용자에게 알린다. 1440p 화면녹화가 주 입력이라 속도가 중요하다
