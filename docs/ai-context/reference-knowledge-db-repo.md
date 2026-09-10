---
name: reference-knowledge-db-repo
description: 사용자의 개인 지식창고 GitHub 저장소(ssap-pa/DB) 구조와 창고별 사용 허가 규칙
metadata: 
  node_type: memory
  type: reference
  originSessionId: 99e34b3e-e435-40fa-875e-0bbfbdb5a41e
  modified: 2026-09-09T19:09:30.924Z
---

사용자의 지식창고는 비공개 저장소 **https://github.com/ssap-pa/DB** (브랜치 `backup`)에 있다. VPS의 pgvector DB 스냅샷이며, 원문은 `read/<창고>/<문서ID>.md`로 정리돼 있고 `knowledge.sql.gz`(42MB)에 7,095개 검색 조각의 1,536차원 벡터가 들어 있다.

창고 4개와 사용 규칙 (2026-09-10 사용자 확인):
- `read/adsense` 381건: 애드센스·유튜브·블로그 수익화 강의. **사용 가능.** README에 "구매한 외부 강의"라고 적혀 있으나 사용자가 잘못 메모한 것이라고 정정함.
- `read/writing-craft` 126건, `read/threads-algo` 2건: 사용 가능.
- `read/ssapable` 225건: 사용자 본인 강의(ssapable.com). **사용자가 다시 승인할 때까지 참고 금지.** README의 "에이전트가 참고하지 않는 정책"과 같은 뜻.

**How to apply:** 지식 기반 답변을 요청받으면 sparse clone으로 허용된 세 창고만 받아 grep으로 후보를 찾고 관련 문서를 읽는다. 저장소가 2026-09-09 이후 바뀌었을 수 있으니 구조를 다시 확인한다. 관련: [[reference-github-repo]], [[user-profile-ssapable]]
