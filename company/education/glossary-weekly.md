# 용어·교육팀 · 주간 용어 추가 — 모델 Sonnet · 화 08:00 KST

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md` · `company/CLOUD.md`
2. `docs/prompts/GRM_주간용어추가_프롬프트_v1.md` — **처음부터 끝까지 정독하고 그대로 따른다.** 아래 "클라우드에서 다른 점"만 예외다.

## 클라우드에서 다른 점
- `YYYYWW` = `TZ=Asia/Seoul date +%G%V`. 표식 브랜치 = `claude/glossary-weekly-YYYYWW`.
- **§0 가드에 하나 더:** 원격에 표식 브랜치나 `glossary/weekly-YYYYWW`(PC) 브랜치가 있거나, 제목이 `glossary(weekly): YYYYWW` 로 시작하는 열린 PR 이 있으면
  `스킵: 이번 주 이미 함` 으로 끝낸다(클라우드는 이어받지 않는다 — 머지는 코드가 한다).
- §0.1 worktree 대신 받아진 저장소에서 `git checkout -b claude/glossary-weekly-YYYYWW origin/main`.
- §1 후보 정의 확인은 WebSearch·WebFetch 로 공식 문서를 직접 본다. 공식 문서를 못 열면 그 후보는 싣지 않는다(추측 금지).
- §3 anon 키·URL 은 `gh variable get` 대신 `company/CLOUD.md` "네트워크" 방법으로 얻는다. 못 얻으면 `보류: 네트워크`.
- §4 명령의 `python` 은 `python3`.
- §5 worktree 없음. 커밋 제목은 정본 그대로 `glossary(weekly): YYYYWW 용어 N개 — 표제어1·표제어2…`.
  push 후 PR 을 `gh api` 로 연다 — **PR 제목 = 커밋 제목**. 본문은 정본 §5 대로. **머지하지 않는다.**
- **추가 0개인 주:** 커밋·PR 없이 표식 브랜치만 올린다 — `git push origin origin/main:refs/heads/claude/glossary-weekly-YYYYWW`
  (PC 백업이 같은 일을 다시 하지 않게 하는 표식).
- 실패·보류로 끝나면 표식 브랜치를 올리지 않는다 → PC 백업이 이어서 한다.

## 결과
최종 메시지 맨 위 3줄(`CHARTER.md`) + 정본 §6 보고 한 줄.
