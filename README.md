# 유튜브 영상 1차 자동 편집

영상을 넣으면 아래 순서로 자동 처리해 `output/` 폴더에 결과를 만듭니다. 웹앱에서 단계별 버튼으로 실행하거나, 폴더 감시로 자동 실행할 수 있습니다.

1. **전사** — faster-whisper(GPU)로 단어 단위 타임스탬프 대본 생성
2. **1차 컷 편집** — 긴 무음 축소(0.3초는 남김), 추임새("어", "음") 제거, 말더듬("그 그") 제거. 인식된 단어는 잘라내지 않음
3. **시각 요소 기획** — Claude가 대본을 읽고 두 종류를 기획
   - 설명 일러스트: 그림이 있어야 이해되는 곳에 **투명 스티커로 화면 중앙에 크게**(기본 화면 높이의 62%) 얹음. 카드 방식은 옵션
   - 오버레이: 언급된 서비스 로고(`로고/` 폴더) 또는 단일 아이콘을 **투명 스티커**(화면 높이의 32%)로 지정 위치에 얹음
   - 모든 스티커는 등장 시 팝(살짝 커졌다 제자리) + 페이드, 퇴장 시 축소 + 페이드, 그 순간에 **효과음**('뽁'/'슉', 합성음이라 파일 불필요)
4. **이미지 생성** — gpt-image-1, 투명 배경. 그림체: 굵은 검정 외곽선 + 플랫 컬러(머스터드 옐로·네이비·코랄·오렌지·러스트), 그림 안 글자 없음
5. **개인정보 모자이크** — 0.5초 간격 OCR로 전화번호·이메일·주민번호·카드/계좌번호·주소·차량번호·API 키·비밀번호 패턴을 찾아 픽셀 모자이크
6. **최종 렌더** + 자막(SRT) + 편집 리포트(MD)

## 실행 방법

**웹앱 (권장)** — `web.bat` 실행 → 브라우저가 http://127.0.0.1:8766 를 엽니다.

- 영상을 끌어다 놓아 업로드 → 옵션 체크 → `전체 실행` 또는 단계 버튼(전사 / 컷 편집 / 기획 / 이미지 생성 / 개인정보 탐지 / 최종 렌더)
- 단계 버튼은 앞 단계 결과가 있으면 재사용, 없으면 자동으로 먼저 실행
- `기획 수정` 탭에서 장면·오버레이를 JSON으로 고치고 `저장 후 이미지 생성 + 렌더`
- `설정`에서 API 키 입력 (`.env`에 저장)

**폴더 감시** — `watch.bat` 실행 후 영상을 `inbox/`에 넣으면 자동 편집. 원본은 `inbox/done/`으로 이동.

**명령줄**

```bash
python -m video_pipeline 영상.mp4
python -m video_pipeline 영상.mp4 --steps plan,assets,render   # 특정 단계만
python -m video_pipeline 영상.mp4 --mode pip --no-overlays --no-mosaic
python -m video_pipeline 영상.mp4 --blur-faces --mute-spoken-pii --fresh
python -m video_pipeline 영상.mp4 --subtitles variety --subtitle-emphasis "곰돌이,말차"   # 예능 자막 번인
python -m video_pipeline 영상.mp4 --fix "뱃살=곰돌이,마오차=말차" --steps render          # 전사 오류 교정 후 재렌더
python -m video_pipeline 영상.mp4 --transcribe openai --subtitles variety --subtitle-tone mz   # API 전사 + MZ 말투 자막
```

**자막 번인** — `--subtitles variety`는 흑백요리사 풍 예능 자막(NanumSquare ExtraBold, 흰 글자 + 두꺼운 검정 외곽선 + 그림자, 하단 중앙, 등장 시 팝)을 영상에 직접 입힌다. `--subtitle-emphasis`에 쉼표로 적은 단어는 노란색으로 강조된다. `clean`은 담백한 흰 자막. 환경변수 `SUBTITLE_STYLE`, `SUBTITLE_EMPHASIS`, `SUBTITLE_FONT`로도 설정할 수 있다. 폰트가 없으면 `fonts-nanum`(Linux) 또는 나눔스퀘어를 설치한다.

**전사 방식** — 기본은 로컬 faster-whisper(GPU 권장). `--transcribe openai`(또는 `TRANSCRIBE_PROVIDER=openai`)를 주면 OpenAI `whisper-1` API로 전사한다. 단어 타임스탬프를 그대로 주므로 컷 편집에 차이가 없고, GPU 없는 환경에서 44초 → 10초로 빨라진다(41초 영상 기준). 오디오가 외부로 나가므로 민감한 영상은 로컬을 쓴다.

**자막 말투** — `--subtitle-tone mz`는 OpenAI 텍스트 모델(`TEXT_MODEL`, 기본 gpt-4.1-mini)로 자막을 한국 MZ 말투로 바꾼다. 결과는 `work/<영상>/tone_mz.json`에 캐시되며, 이 파일의 `lines`를 손으로 고치면 다음 렌더에 그대로 반영된다. 배포 SRT도 변환된 말투로 나간다.

**예능 효과** — `work/<영상>/fx.json`에 이벤트를 적으면 렌더가 읽는다(`--no-fx`로 끔). `zoom`(급 줌인 + '휙' 효과음), `flash`(흑백+비네트 플래시 + '두둥'), `caption`(상단 임팩트 자막, 노랑/흰/빨강). 형식은 `video_pipeline/fx.py` 상단 주석 참고.

**전사 교정** — 음성 인식이 틀린 단어는 `--fix "잘못=바름,잘못2=바름2"`(또는 `TRANSCRIPT_FIXES`)로 바로잡는다. 캐시된 대본에 매번 적용되므로 `--steps render`만 다시 돌리면 자막(SRT·번인)과 리포트에 반영된다. 일러스트·아이콘 내용도 바꾸려면 `work/<영상>/plan.json`의 프롬프트를 고치고 `image_path`를 비운 뒤 렌더한다.

## API 키

`.env` 파일(또는 웹앱 설정)에 넣습니다. `.env.example` 참고.

| 키 | 용도 | 없으면 |
|---|---|---|
| `ANTHROPIC_API_KEY` (클라우드 환경은 `PIPELINE_ANTHROPIC_API_KEY`) | 시각 요소 기획 (Claude) | 기획 단계를 건너뜀. 컷 편집·모자이크는 정상 |
| `OPENAI_API_KEY` | 일러스트·아이콘 생성 (gpt-image-1) | 같은 팔레트의 도형 플레이스홀더로 대체 |

## 결과물

```
output/
  영상_edited.mp4     # 편집 완료 영상
  영상_edited.srt     # 편집본 기준 자막
  영상_report.md      # 컷 목록, 일러스트/오버레이 위치, 개인정보 탐지 내역
work/영상/            # 중간 산출물: transcript.json, cuts.json, cut.mp4, plan.json, images/, pii.json
uploads/              # 웹앱으로 올린 원본
```

## 설치 (다른 PC)

```bash
pip install -r requirements.txt
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126   # NVIDIA GPU
```

ffmpeg가 PATH에 있어야 합니다. 이 PC는 이미 설치되어 있습니다.

## OCR(개인정보 탐지) 속도

기본 설정은 두 가지로 속도를 확보합니다.
- **OCR 해상도 1280**: 1440p/4K 원본을 1280 폭으로 줄여 검사. 화면 글씨는 이 크기에서도 충분히 읽힘
- **정적 화면 건너뛰기**: 직전 검사와 화면이 거의 같으면 이전 결과를 재사용. 단, 최소 3초마다 한 번은 강제로 검사

1440p 30초 클립 기준 224초 → 39초(약 6배). 화면 전환이 잦은 영상은 건너뛰기 효과가 줄어듭니다.
더 빠르게 하려면 `OCR 해상도 960` 또는 `OCR 간격 1.0`, 더 꼼꼼히 하려면 `OCR 해상도 1920` 과 `OCR 간격 0.25`.

## 한계와 확인 포인트

- 자동 편집이므로 업로드 전 리포트를 보고 확인하세요. 특히 **모자이크 누락**(작은 글씨)과 **일러스트·오버레이 위치**.
- 말로 읊은 개인정보는 기록만 하고 기본으로 음소거하지 않습니다(옵션).
- 얼굴은 기본으로 가리지 않습니다. 타인 얼굴이 나오면 `얼굴 모자이크` 옵션을 쓰되, 본인 얼굴도 가려집니다.
- 로고는 `로고/` 폴더의 파일명(한글_영문_로고.png)으로 찾습니다. 없는 서비스는 아이콘으로 대체됩니다.
