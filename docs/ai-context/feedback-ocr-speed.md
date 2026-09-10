---
name: feedback-ocr-speed
description: 사용자는 OCR(개인정보 탐지)이 너무 느리다고 지적 — 1440p 화면녹화가 주 입력이므로 속도 최적화가 중요
metadata:
  type: feedback
---

사용자의 실제 입력은 2560x1440 화면 녹화(스레드 댓글 UI 등, 3분 내외)이며, OCR이 30초당 224초 걸려 불만을 제기함.

**Why:** 1440p 전체 프레임을 0.5초마다 EasyOCR로 읽었고, SNS 화면의 "threads.com @handle"이 이메일로 오인돼 89개 가짜 영역까지 생김.

**How to apply:** 2026-09-08에 OCR 축소(1280폭)+정적 화면 건너뛰기+배치 인식으로 약 6배 개선하고 이메일 정규식을 TLD 필수로 조임. 이후 화면녹화 입력에 대한 처리 시간·오탐 여부를 먼저 점검할 것. 관련: [[project-video-pipeline]]
