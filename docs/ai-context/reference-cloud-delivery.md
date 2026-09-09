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
- **결과 영상 전체(4K)를 받으려면:** 사용자 PC에서 파이프라인을 돌리거나, 저장소 정책상 output/을 커밋하지 않으므로 별도 전달 경로가 필요하다.
- 클라우드 VM 메모리 한도(cgroup)는 약 12GB 지점에서 OOM kill. 관련: [[feedback-cut-render-oom]]
