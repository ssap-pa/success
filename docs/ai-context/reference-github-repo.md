---
name: reference-github-repo
description: success 프로젝트의 GitHub 저장소 위치(공개 ssap-pa/success)와 AI 컨텍스트 동기화 규칙
metadata:
  type: reference
---

success 폴더는 2026-09-11부터 GitHub **공개** 저장소 **https://github.com/ssap-pa/success** (계정 ssap-pa, 브랜치 main)에 올라간다. 2026-09-10에 쓰던 비공개 `ssap-pa/success_test`는 이전 버전이며 더 이상 갱신하지 않는다.

- 로컬 클로드 메모리는 `docs/ai-context/`에 복사돼 저장소에 함께 실린다. 새 메모리를 쓰면 이 폴더에도 같은 파일을 추가하고 커밋한다.
- 저장소가 공개이므로 개인정보·키·사용자 영상·지식창고 원문은 절대 넣지 않는다. `.env`와 `inbox/ uploads/ output/ work/`는 `.gitignore`로 제외됨.
- 사용자가 "success 저장소에 올려줘"라고 하면 커밋 후 `git push origin main`.
- `CLAUDE.md`(클로드 코드용)와 `agent.md`(다른 AI 도구용)가 저장소 루트에 있다. 프로젝트 규칙이 바뀌면 둘 다 갱신한다.

관련: [[project-video-pipeline]]

**2026-09-10 저장소 이동:** `ssap-pa/success_test`(비공개)가 사라지고(404/403) 별도 이력의 **공개** 저장소 `ssap-pa/success`가 생겼다. 클라우드 세션 커밋 16개를 `success/main` 위에 cherry-pick 해 `claude/cloud-pipeline-fx` 브랜치로 푸시, PR #1 초안. 세션에서 새 저장소에 푸시하려면 사용자가 Claude GitHub 앱 설치 대상에 그 저장소를 추가해야 한다(add_repo 만으로는 읽기만 됨). 공개 저장소이므로 개인정보·키·영상 커밋 금지.
