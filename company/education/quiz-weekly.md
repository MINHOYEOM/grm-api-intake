# 용어·교육팀 · 주간 퀴즈 — 모델 Sonnet · 월 12:30 · 16:30 KST

브리프 라이브가 대표 승인에 달려 있어 월요일에 두 번 돈다. 이미 이번 주 퀴즈가 있으면 바로 끝낸다(정상).

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md` · `company/CLOUD.md`
2. `docs/prompts/GRM_주간퀴즈생성_프롬프트_v1.md` — **처음부터 끝까지 정독하고 그대로 따른다.** 아래 "클라우드에서 다른 점"만 예외다.

## 클라우드에서 다른 점
- `YYYYWW` = `TZ=Asia/Seoul date +%G%V`. 표식 브랜치 = `claude/quiz-weekly-YYYYWW`.
- **먼저 확인(하나라도 맞으면 `스킵: 이번 주 이미 함`):** `web/data/quiz_bank.json`(origin/main)에 `week: YYYYWW` 문항이 있다 ·
  원격에 표식 브랜치가 있다 · 제목이 `quiz(weekly): YYYYWW` 로 시작하는 열린 PR 이 있다.
- 라이브 확인(`https://grm-solutions.com/briefs/{publish_date}/`)이 안 되면 `보류: 네트워크 — grm-solutions.com` 으로 끝낸다.
  라이브가 아니면 정본대로 `SKIP` — 표식 브랜치를 만들지 않는다(다음 회차·PC 백업이 다시 본다).
- §1 worktree 대신 `git checkout -b claude/quiz-weekly-YYYYWW origin/main`.
- §4-3 전체 테스트는 돌리지 않는다(CI 가 본다). 대신 `python3 quiz_lint.py` · `python3 -m unittest tests.test_quiz_lint` ·
  `python3 web/tests/test_render.py --freeze` 후 `git diff --numstat -- web/tests/golden` 에서 퀴즈가 실리는 골든만 바뀌었는지 확인.
  다른 골든이 바뀌면 멈추고 `보류` 로 끝낸다.
- §4-6 브랜치는 표식 브랜치. **커밋·PR 제목 = `quiz(weekly): YYYYWW 문항 N개 (브리프 N + 개념 N)`.** PR 은 `gh api` 로 연다.
- §4-7·8 CI 를 기다리거나 머지하지 않는다. PR 을 열면 끝 — 머지는 코드가 한다.
- `git add` 는 경로 명시(`web/data/quiz_bank.json` + 바뀐 골든만).

## 결과
최종 메시지 맨 위 3줄(`CHARTER.md`) + 정본 끝의 `DONE`/`SKIP` 보고.
