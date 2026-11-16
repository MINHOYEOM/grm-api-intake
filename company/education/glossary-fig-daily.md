# 용어·교육팀 · 용어 그림 일일 배치 — 모델 Sonnet · 매일 21:00 KST

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md` · `company/CLOUD.md`
2. `docs/prompts/GRM_용어그림_일일배치_프롬프트_v1.md` — **처음부터 끝까지 정독하고 그대로 따른다.** 아래 "클라우드에서 다른 점"만 예외다.

★대표 요청(2026-09-29): 억지 그림·안 맞는 그림을 특히 조심한다. 그림 0장 회차도 정상이다. 개수를 채우지 마라.

★대표 요청(2026-11-03) — **그림은 GMP 학습에 실제로 가치가 있어야 하고, 한눈에 후킹이 있어야 한다.** 쌩뚱맞은 것·대충 만든 것은 올리지 않는다.
- **채택 전 자문 세 가지 (하나라도 아니오면 그리지 않는다):**
  1. 이 그림 하나로 GMP 를 배우는 사람이 그 용어의 핵심 구조·흐름·구분을 **텍스트 정의보다 빨리 이해**하는가?
  2. 3초 안에 "무엇에 대한 그림인지"와 "그래서 뭐가 중요한지"가 보이는가(제목·핵심 한 줄·시선 흐름)?
  3. 현장 QA/제조 실무자가 보고 고개를 끄덕일 정도로 **정확**한가(숫자·용어·화살표 방향·등급 구분)?
- **금지:** 장식용 도형, 정의 문장을 그대로 박스에 옮긴 것, 빈 문서 아이콘, 의미 없는 화살표, 글자만 나열한 표, 겉만 그럴듯한 일반 도식.
- **독립 검토자에게는 위 세 질문을 그대로 묻고**, 각각 근거와 함께 예/아니오로 답하게 한다. 한 질문이라도 아니오거나 근거가 약하면 **채택하지 않고 skip 장부에 사유를 적는다**(0장 정상). 자기 작품이라 후하게 보지 말고, 의심되면 뺀다.
- 라벨 겹침·잘림·가독성(작은 글자)은 실측으로 확인한다(getBBox). 모바일 폭에서 읽히는지도 본다.

## 클라우드에서 다른 점
- PC 백업 없음(PC 작업은 꺼져 있다). 이중 실행 걱정은 열린 배치 PR 가드로만 막는다.
- `D` = `TZ=Asia/Seoul date +%Y%m%d-%H%M`. 브랜치 = `claude/glossary-fig-daily-$D`.
- §0.1 worktree 대신 받아진 저장소에서 `git fetch origin main && git checkout -b claude/glossary-fig-daily-$D FETCH_HEAD`.
  (`origin/main` 이라는 이름은 쓰지 않는다 — 실행 환경에 저장된 `origin/main` 은 며칠 전 것일 수 있다(2026-11-13 리허설: 11-06 커밋). 반드시 방금 `git fetch origin main` 한 `FETCH_HEAD` 를 쓴다.)
- §0.3 열린 배치 PR(head 가 `claude/glossary-fig-daily-` 또는 `glossary-fig/daily-` 로 시작)이 있으면
  **머지하지 말고 `스킵: 앞 배치 PR #n 대기`** 로 끝낸다 — 머지는 워치독이 한다.
- §0.4 en.json 가드는 `gh api "$R/pulls/<n>/files"` 로 PR 마다 확인한다.
- **파이썬·Chrome 준비(11-01 첫 실행 실측)** — 그대로 쓴다:
  `python3.12 -m venv /tmp/v && /tmp/v/bin/pip install -q -r requirements.txt` → 이후 `python` 은 `/tmp/v/bin/python`
  (3.11 은 `web/linkedin_cards.py` 를 import 하지 못한다).
  `export GRM_CHROME=$(ls /opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell | head -1)`
  (`/usr/bin/google-chrome` 은 빈 페이지에서도 멈춘다). 둘 중 하나라도 안 되면 `보류: 환경 — <무엇>` 으로 끝낸다(실측 없이 올리지 않는다).
- `check` 가 exit 2(Pretendard 미로드)를 두 번 내면 `보류: 네트워크 — cdn.jsdelivr.net` 으로 끝낸다.
- 시트 PNG 는 Read 도구로 직접 본다. 독립 검토자는 Agent 도구(model sonnet) 1명, 부록 지시문 그대로.
- 전체 테스트는 돌리지 않는다.
- §6 커밋 제목은 정본 그대로 `glossary(fig): YYYY-MM-DD …`. push 후 PR 을 `gh api` 로 연다 — **PR 제목 = 커밋 제목**.
  본문은 정본 §6 대로. **머지하지 않는다**(워치독이 CI 초록 3시간 뒤 머지).
- 바뀐 파일이 없는 회차(전부 기존 skip 등)는 커밋·PR 없이 끝낸다. 장부 skip 만 바뀌어도 PR 은 연다.

## 결과
최종 메시지 맨 위 3줄(`CHARTER.md`) + 정본 §7 보고 한 줄.
