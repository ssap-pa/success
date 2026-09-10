# agent.md — AI 코딩 에이전트용 작업 안내

Claude Code 외의 도구(Codex, Cursor, Copilot 등)도 이 문서를 먼저 읽는다. Claude Code 전용 세부 지침은 `CLAUDE.md`, 축적된 맥락은 `docs/ai-context/`.

## 이 프로젝트가 하는 일

유튜브 영상 1차 자동 편집. 입력 영상 하나를 다음 순서로 처리한다.

| 단계 | 모듈 | 내용 |
|---|---|---|
| transcribe | `video_pipeline/transcribe.py` | faster-whisper(GPU)로 단어 단위 타임스탬프 전사 |
| cut | `video_pipeline/cutter.py` | 무음 축소, 추임새("어","음")·말더듬 제거, ffmpeg trim+concat |
| plan | `video_pipeline/planner.py` | Claude(`claude-opus-5`, structured output)가 일러스트·오버레이·발화 개인정보 기획 |
| assets | `video_pipeline/illustrator.py` | gpt-image-1 투명 배경 생성, 로고 투명화, 스티커 합성 |
| pii | `video_pipeline/pii.py` | EasyOCR + 정규식으로 화면 개인정보 탐지 |
| render | `video_pipeline/render.py`, `sfx.py` | 모자이크 + 스티커 애니메이션 + 효과음 합성, ffmpeg 인코딩 |

오케스트레이션은 `video_pipeline/pipeline.py`의 `Job` 클래스. 각 단계는 `work/<영상>/`에 산출물을 남기고 다음 단계가 읽는다. 단계별 캐시가 있어 `Job.run(["render"])`처럼 일부만 다시 돌릴 수 있다.

## 실행

```bash
pip install --user -r requirements.txt          # 이 PC는 시스템 Python이라 --user 필요
python -m video_pipeline samples/test_video.mp4  # 전체 실행, 30초 안에 끝남
python -m video_pipeline 영상.mp4 --steps plan,assets,render
python -m web.server                             # 로컬 웹앱 http://127.0.0.1:8766 (web.bat 권장)
python watch_inbox.py                            # inbox/ 폴더 감시
```

API 키는 `.env`(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). 없으면 해당 단계만 건너뛰거나 플레이스홀더로 대체되며 나머지는 동작한다.

## 코드 규칙

- 설정은 전부 `video_pipeline/config.py`의 `PipelineConfig`. 새 옵션은 여기에 필드 + `.env` 이름을 추가하고, 웹앱에 노출하려면 `web/server.py`의 `OPTION_FIELDS`와 `web/static/index.html`에도 추가한다.
- 기획 JSON 스키마를 바꾸면 `planner.py`의 `PLAN_SCHEMA`, 데이터클래스, `Plan.from_dict`, 웹앱 기획 편집 탭을 함께 맞춘다.
- 개인정보 탐지 규칙(정규식·OCR 방식)을 바꾸면 `pipeline.py`의 `PII_VERSION`을 올려 옛 캐시를 무효화한다.
- 로그는 `video_pipeline.utils.log` 로거만 쓴다. 웹앱이 이 로거를 잡아 화면에 보여준다.
- 한국어 UI·로그·주석. 코드 식별자는 영어.
- ffmpeg 필터가 길어지면 `-filter_complex_script` 파일로 넘긴다(Windows 명령줄 길이 제한).

## 확정된 사용자 요구 (임의로 바꾸지 않는다)

- 일러스트는 카드 없이 화면 중앙 투명 스티커(`illustration_mode=overlay`, 높이 62%). 로고·아이콘 스티커는 높이 32%
- 등장 시 팝 + 페이드, 퇴장 시 축소 + 페이드, 각 순간 합성 효과음
- 그림체: 굵은 검정 외곽선 + 플랫 컬러(옐로 #F7C948, 네이비 #2D3858, 코랄 #F68C83, 오렌지 #F08C28, 러스트 #963719), 그림 안 글자 없음
- 컷 편집은 인식된 단어를 절대 자르지 않는다
- 얼굴 모자이크, 음성 개인정보 음소거는 기본 OFF
- OCR은 1280 폭 축소 + 정적 화면 건너뛰기가 기본. 처리 시간을 늘리는 변경은 사용자에게 먼저 알린다 (주 입력이 1440p 화면녹화)

## 하지 말 것

- `.env` 커밋·출력 금지. `inbox/ uploads/ output/ work/`는 개인 영상이라 커밋·삭제 금지
- 이 저장소는 공개다. 사용자 개인정보나 비공개 지식창고(`ssap-pa/DB`) 원문을 넣지 않는다
- 웹앱 포트 8766을 바꾸지 않는다 (8765는 다른 앱이 사용)

## 검증

변경 후 최소한 다음을 통과시킨다.

```bash
python -m compileall -q video_pipeline web
python -m video_pipeline samples/test_video.mp4 --steps render
```

결과는 `output/test_video_edited.mp4`, 프레임을 뽑아 스티커·모자이크 위치를 눈으로 확인한다.
