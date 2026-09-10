---
name: reference-cloud-delivery
description: 클라우드 세션에서 결과 영상을 사용자에게 전달하는 경로와 한계 (Google Drive MCP, 파일 전송 한도)
metadata:
  type: reference
---

2026-09-09 클라우드 세션에서 Google Drive 링크의 영상(4K 60fps, 257MB)을 편집해 Drive 폴더로 돌려주는 작업을 하며 확인한 사실.

- **입력 다운로드:** `drive.google.com/uc?export=download&id=…` 로 확인 페이지를 받고, 페이지의 form 값(id, export, confirm, uuid)으로 `drive.usercontent.google.com/download` 를 curl 하면 받아진다. Drive MCP의 `download_file_content`는 base64로 돌려주므로 영상에는 못 쓴다.
- **Drive 업로드:** Drive MCP `create_file`은 내용을 tool 인자(base64/text)로 넘기므로 자막·리포트 같은 텍스트만 가능. 영상 파일은 크기 때문에 불가. Drive API 직접 호출은 OAuth 토큰이 없어 401.
- **사용자에게 파일 전송(SendUserFile):** 한도 30MiB. 4K 결과(85MB)는 실패 → 1080p `crf 20 -maxrate 6M` 로 줄이면 34초에 약 21MB.
- **결과 영상 전체(4K)를 받는 경로 (2026-09-09 추가):** `video_pipeline/drive_upload.py` — 서비스 계정(`GDRIVE_SERVICE_ACCOUNT_JSON`)으로 Drive API resumable 업로드. `--drive-folder <ID|URL>` 또는 `GDRIVE_FOLDER_ID`. 폴더를 서비스 계정 이메일에 편집자로 공유해야 한다. 사용자는 이 변수 이름으로 환경에 넣었다고 함. 새 환경변수는 실행 중인 세션에 안 들어오고 새 세션에만 적용된다.
- 클라우드 VM의 시스템 `cryptography`는 `_cffi_backend`가 없어 import 시 패닉 → `pip install cffi`로 해결(requirements에 포함).
- 클라우드 VM 메모리 한도(cgroup)는 약 12GB 지점에서 OOM kill. 관련: [[feedback-cut-render-oom]]
- **환경변수 입력 형식 (2026-09-09 추가):** claude.ai/code 환경변수 칸은 줄마다 `KEY=value` 하나만 받는다. 서비스 계정 JSON을 여러 줄로 붙여 넣으면 `Couldn't parse ""type": "service_account",". Use KEY=value format.` 오류. 한 줄 JSON(`json.dumps`)으로 넣거나 `base64 -w0 key.json` 결과를 넣는다. `drive_upload._load_service_account`가 base64도 받도록 고쳤다. 실제로 한 번은 `private_key_id`(40자 16진수)만 들어가 있어 로더가 실패했다. 값을 출력하지 말고 길이·문자 종류만 확인해 진단한다.
- **서비스 계정 업로드 한계 (2026-09-10 확인):** 서비스 계정 `video-test@home-calendar-489506.iam.gserviceaccount.com` 은 `storageQuota.limit=0` 이라 사용자 My Drive 폴더에 새 파일을 만들면 `403 Service Accounts do not have storage quota` 가 난다. 공유 폴더 안의 파일 **읽기·다운로드**(`files/<ID>?alt=media`)는 된다. 우회로 `drive_upload.replace_file_content`(사용자 소유 빈 파일의 내용을 서비스 계정이 교체, 용량은 소유자 부담)를 추가했고, 사용자가 `.claude/settings.json`에 `Bash(python -m video_pipeline.drive_upload *)` 허용 규칙을 커밋한 뒤 `python -m video_pipeline.drive_upload <mp4> --replace <파일ID>` 로 4K 85MB 업로드 성공(2026-09-10). 빈 파일은 Drive MCP `create_file`(base64 수십 바이트)로 만든다. AI가 스스로 허용 규칙을 쓰는 것은 분류기가 막으므로 사용자가 직접 커밋해야 한다.
- **사용자 폴더 이름:** Drive 폴더는 `claude_ success`(공백 포함), 하위 `out put`. Drive MCP `search_files`(title =)로는 안 잡히고 서비스 계정 `files?q=`로 찾았다. 원본은 `0908-복사.mp4`(4K 60fps 41.6s, 257MB).
- **ANTHROPIC_API_KEY 환경변수 (2026-09-10 확인):** claude.ai/code 환경변수에 `ANTHROPIC_API_KEY`를 넣어도 세션에 들어오지 않는다(Claude Code 인증용으로 예약). 자식 세션에서 확인. 해결: 사용자가 `PIPELINE_ANTHROPIC_API_KEY`로 넣고, `planner.make_plan`이 그 이름을 먼저 읽어 `anthropic.Anthropic(api_key=...)`에 넘긴다. 환경변수는 새 세션에만 적용되므로 넣은 뒤 세션을 새로 열어야 한다. 공식 문서(cloud-environments)의 API credentials 기능은 `api.anthropic.com` 요청에는 붙지 않는다고 명시돼 있어 대안이 못 되고, GitHub Actions secret 은 워크플로 실행 중에만 풀려 세션에서 읽을 수 없다. 호스트는 `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=1`을 직접 설정한다.
- **키 없이 기획하는 대안:** 파이프라인은 `work/<영상>/plan.json`이 있으면 API 호출 없이 캐시로 쓴다. 세션의 Claude가 `transcript_cut.json`을 읽고 같은 스키마로 plan.json을 쓰면 이미지 생성·렌더까지 이어진다(2026-09-10 이미지 생성까지 확인). 단 세션 컨텍스트(10만 토큰대)를 턴마다 다시 읽어 API 호출(2~3천 토큰)보다 훨씬 비효율적이고 구독 한도를 소모한다.
- **파이프라인 소요 (2026-09-10, 4 vCPU CPU 전용, 41.6s 4K60 원본):** whisper medium 전사 108s, 컷 편집본 4K libx264 렌더 283s, gpt-image-1 medium 일러스트 1장 19s·아이콘 3장 46s, OCR(1.0s 간격) 172s.
