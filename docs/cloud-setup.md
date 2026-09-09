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

결과는 `output/`에 생기며 저장소에는 올라가지 않는다. 세션의 파일 전송은 30MiB 한도라 4K 결과는 못 돌려준다. 결과 영상을 받는 방법은 아래 4번(Drive 업로드).

## 4. 결과를 Google Drive로 받기 (서비스 계정)

1. Google Cloud 콘솔 → 프로젝트 → **Drive API 사용 설정** → 서비스 계정 생성 → JSON 키 발급
2. Drive에서 결과 받을 폴더를 서비스 계정 이메일(`client_email`, `…@….iam.gserviceaccount.com`)에 **편집자**로 공유
3. 환경변수 `GDRIVE_SERVICE_ACCOUNT_JSON` 에 JSON 본문 전체를 **한 줄로** 넣는다 (파일 경로도 가능)
   - 환경변수 입력칸은 줄마다 `KEY=value` 하나로 읽으므로 JSON 파일을 여러 줄 그대로 붙여 넣으면
     `Couldn't parse ""type": "service_account",". Use KEY=value format.` 오류가 난다
   - 한 줄로 만들기: `python -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8'))))" key.json`
   - 또는 base64 한 줄도 된다: `base64 -w0 key.json` (Git Bash). 로더가 JSON → 파일 경로 → base64 순으로 시도한다
   - `private_key_id`(40자 16진수) 한 필드만 넣으면 안 된다. 파일 내용 전체여야 한다
4. 실행:

```bash
python -m video_pipeline uploads/영상.mp4 --drive-folder <폴더ID 또는 폴더URL>
# 또는 GDRIVE_FOLDER_ID=<폴더ID> 환경변수
```

렌더가 끝나면 `_edited.mp4`, `_edited.srt`, `_report.md` 세 파일을 resumable 업로드로 올리고 링크를 로그에 남긴다.

- 폴더 404: 폴더를 서비스 계정에 공유하지 않았거나 ID가 틀림
- `storageQuotaExceeded`: 서비스 계정에 저장 용량이 없어 개인 My Drive 폴더에 파일을 만들 수 없는 경우. 공유 드라이브(Workspace)를 쓰거나 OAuth 사용자 인증으로 바꿔야 한다
- 입력 영상이 Drive 공유 링크면 `drive.google.com/uc?export=download&id=<ID>` 확인 페이지의 form 값(id, export, confirm, uuid)으로 `drive.usercontent.google.com/download` 를 curl 하면 받아진다. 서비스 계정이 파일을 볼 수 있게 공유돼 있으면 `https://www.googleapis.com/drive/v3/files/<ID>?alt=media` 로도 받을 수 있다

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
