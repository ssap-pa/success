---
name: feedback-cut-render-oom
description: 컷 편집 렌더(trim+concat)가 4K 영상에서 메모리 폭주로 죽음 → select/aselect 단일 패스로 교체
metadata:
  type: feedback
---

2026-09-09 클라우드 세션에서 4K 60fps 41초 커플 인터뷰 영상(257MB)을 처리하다 `render_cut`의 ffmpeg가 SIGKILL(OOM, RSS 12GB)로 죽었다.

**Why:** `[0:v]trim=...` 6갈래 + `concat`은 concat이 첫 구간을 내보내는 동안 뒤 구간의 디코딩된 4K 프레임(장당 12MB)을 전부 큐에 쌓는다. 1000프레임이면 12GB.

**How to apply:** `cutter.render_cut`을 `select='between(t,s,e)+…',setpts=N/FRAME_RATE/TB` + `aselect`/`asetpts=N/SR/TB`로 바꿨다. 한 스트림을 한 번만 훑어 메모리가 2GB 수준으로 일정하다. 오디오 경계는 오디오 프레임(약 23ms) 단위로 맞춰진다. samples/test_video.mp4로 컷 길이(29.0s 계획 → 29.1s 결과) 확인. 관련: [[project-video-pipeline]]
