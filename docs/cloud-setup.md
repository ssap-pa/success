# 클로드 코드 클라우드(claude.ai/code)에서 파이프라인 실행하기

클라우드 VM은 Ubuntu 24.04, 4 vCPU, 16GB RAM, 30GB 디스크, **GPU 없음**, ffmpeg 미설치다.
아래 설정을 한 번 해두면 이후 세션은 캐시된 스냅샷으로 바로 시작한다.

## 1. 환경(Environment) 설정 — claude.ai/code 에서 직접 입력

### Network access: **Custom**

"Also include default list of common package managers" 체크 후, 허용 도메인에 추가:

```
huggingface.co
*.huggingface.co
api.openai.com
download.pytorch.org
```

- huggingface.co: faster-whisper 모델 다운로드
- api.openai.com: gpt-image-1 이미지 생성 (API credentials로 등록하면 이 줄은 생략 가능)
- download.pytorch.org: CPU 전용 torch (pypi 기본 torch는 CUDA 포함이라 2GB 넘음)
- EasyOCR 모델은 github 릴리스에서 받으므로 기본 목록으로 충분

### Environment variables

```
ANTHROPIC_API_KEY=<값>
OPENAI_API_KEY=<값>
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
OCR_INTERVAL=1.0
VIDEO_CODEC=libx264
```

- `WHISPER_MODEL=small`: CPU에서 large-v3는 50초 영상에 수 분 이상 걸린다. 테스트는 small, 품질이 필요하면 medium
- `OCR_INTERVAL=1.0`: CPU OCR 부담을 줄이기 위해 검사 간격을 기본 0.5초에서 1초로
- `VIDEO_CODEC=libx264`: NVENC 없음

Pro/Max 플랜이면 OPENAI 키는 **API credentials** 칸에 호스트 `api.openai.com`으로 등록하는 편이 안전하다.
단, openai 파이썬 라이브러리가 환경변수가 비어 있으면 요청 전에 오류를 낼 수 있으니 그 경우 `OPENAI_API_KEY=placeholder`를 환경변수에 함께 둔다.

### Setup script

```bash
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
(sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg libgl1 libglib2.0-0) \
  || (apt-get update -qq && apt-get install -y -qq ffmpeg libgl1 libglib2.0-0)
python -m pip install --upgrade pip -q
python -m pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -q -r requirements.txt
ffmpeg -version | head -1
python -c "import torch, easyocr, faster_whisper; print('deps ok, cuda =', torch.cuda.is_available())"
```

## 2. 테스트 영상을 저장소에 넣기

`uploads/`, `inbox/`는 .gitignore로 제외되어 클라우드에 안 올라간다. 테스트 영상은 `samples/`에 넣고 커밋·푸시한다.

```bash
cp "영상경로.mp4" samples/test_50s.mp4
git add samples/test_50s.mp4
git commit -m "test: add 50s sample"
git push origin main
```

100MB 미만이면 그대로 올려도 된다. 그 이상이면 1080p로 줄여서 올린다.

## 3. 클라우드 세션에서 실행

웹앱(브라우저)은 클라우드에서 못 쓰므로 명령줄로 실행한다.

```bash
python -m video_pipeline samples/test_50s.mp4
```

단계별로 나눠 확인하려면:

```bash
python -m video_pipeline samples/test_50s.mp4 --steps transcribe
python -m video_pipeline samples/test_50s.mp4 --steps cut,plan
python -m video_pipeline samples/test_50s.mp4 --steps assets,pii,render
```

결과는 `output/`에 생기며 저장소에는 올라가지 않는다. 결과 영상을 받으려면 세션에서 PR 브랜치에 `output/`을 예외적으로 포함시키거나, `_report.md`와 `_edited.srt`만 확인한다.

## 예상 소요 (50초 영상, CPU 기준, 대략)

| 단계 | 예상 |
|---|---|
| 첫 세션 setup script | 5~10분 (이후 캐시) |
| whisper small 전사 | 1~2분 |
| 컷 편집 | 수십 초 |
| Claude 기획 + gpt-image-1 생성 | 1~3분 |
| EasyOCR (1초 간격, 1280폭) | 2~5분 |
| libx264 렌더 | 1~2분 |

## 흔한 실패

- `ffmpeg: not found`: setup script가 안 돌았거나 실패. 세션에서 `sudo apt-get install -y ffmpeg`
- huggingface 연결 오류: Network access가 Custom이 아니거나 도메인 누락
- `libGL.so.1` 오류: `libgl1` 미설치
- openai 401/연결 오류: API credentials 호스트 오타 또는 api.openai.com 미허용
- 메모리 초과로 종료: `WHISPER_MODEL=small` 확인, `OCR_MAX_WIDTH=960`으로 낮춤
