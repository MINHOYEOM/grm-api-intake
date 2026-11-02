# 실행 규칙 — 브리프편집팀이 월요일 실제 발행을 맡는다 (2026-W45 부터, 대표 결정 2026-10-31)

데스크(07:20, 재시도 07:45) → 기자 샤드 4개(08:00·08:35) → 교열(09:05) 순서로 돌고, **교열이 통과시킨 것만** 발행 경로에 넘긴다.
현행 v16 Routine 은 09:30 **백업**으로 남는다. (2026-11-02 시범에서 handoff 가 07:05 에야 생기고 기자 한 세션이 분량을 못 감당해 시각표·분량을 바꿨다 — 아래 5·6항.) 이 파일의 규칙은 직무 지시문과 v16 본문보다 **우선한다.**

## 1. Notion 쓰기는 교열의 마감 단계만
- 데스크·기자는 Notion 을 **읽기만** 한다. 어떤 페이지·속성도 만들거나 바꾸지 않는다.
- 교열도 판정이 **전부 통과**일 때만, `copyedit.md` 의 "마감" 순서대로만 쓴다.
- 하나라도 통과가 아니면 교열은 Notion 에 **아무것도 쓰지 않고** 끝낸다. 그러면 handoff 가 `OPEN` 으로 남아
  09:30 백업 Routine 이 평소대로 발행한다. 이것이 품질이 떨어지지 않게 하는 안전장치다.

## 2. handoff 선택 = v16 [0단계] 그대로
`Status=New`(Title `OPEN ...`) 중 `run_date_kst` 가 가장 큰 1건. 그 날짜가 이번 주 월요일(KST)이 아니거나
OPEN 이 없으면 `상태: 보류` 로 종료한다(이미 누가 처리했다는 뜻).

## 3. 중간 결과는 git 브랜치에
- 브랜치: `claude/company-{run_date_kst}` (예: `claude/company-2026-11-02`). 없으면 `origin/main` 에서 만든다.
- 폴더: `company/run/{run_date_kst}/` 에 직무별 파일만 쓴다. 다른 경로는 건드리지 않는다.
- 기자 샤드는 **같은 브랜치에 동시에** 쓴다. 각자 자기 파일만 `git add <파일>` 로 커밋하고, push 전에 `git pull --rebase origin claude/company-{run_date_kst}`,
  push 가 거절되면 같은 방식으로 3번까지 다시 시도한다. 그래도 안 되면 `보류`.
- PR 을 열지 않는다. `main` 에 push·머지하지 않는다. (발행 PR 은 기존 자동화가 만든다.)

## 4. 한도는 현행과 같게
- WebSearch 총 9회 · WebFetch 5회 (v16 [1단계]).
- 다른 직무의 결과를 고치지 않는다. 이의가 있으면 자기 파일에 `이의:` 한 줄로 남긴다.
- 앞 직무 파일이 없거나 `status` 가 `완료` 가 아니면 `보류` 로 종료한다 — 백업이 이어받는다.

## 5. 시각표 (월요일 KST) — handoff 는 07:05 까지 늦어진 적이 있다
| 시각 | 직무 | 모델 | 하는 일 |
|---|---|---|---|
| 07:20 | 데스크 | Sonnet | handoff 읽기 · 조치 배정 · **샤드 계획**(`desk.json` 의 `shards`) |
| 07:45 | 데스크 재시도 | Sonnet | 07:20 에 `desk.json` 이 `완료`면 곧바로 종료. 아니면(handoff 없음·실패) 다시 한다 |
| 08:00 | 기자 델타-A | Opus | 계획된 앞 절반 카드의 산문 슬롯 → `delta.A.json` |
| 08:00 | 기자 심층-A | Sonnet | 계획된 심층분석 절반 → `deep.A.json` |
| 08:35 | 기자 델타-B | Opus | 뒤 절반 카드 슬롯 + 브리프 `tldr` → `delta.B.json` |
| 08:35 | 기자 심층-B | Sonnet | 나머지 심층분석 → `deep.B.json` |
| 09:05 | 교열 | Sonnet | 샤드 합치기 → 코드 검사 → 의미 점검 → 전부 통과면 예치·마감 |
| 09:30 | 백업 (v16 Routine) | Opus | handoff 가 아직 OPEN 이면 예전 방식 그대로 |

교열은 09:30 전에 마감까지 끝내야 한다 — 마감(CONSUMED)이 늦으면 백업과 **이중 발행**된다.
09:20 이 되도록 마감 4번의 예치 검증이 끝나지 않았으면 예치 페이지를 RUN.md 규칙대로 VOID 처리하고 `실패` 로 끝낸다(백업이 이어받는다).

## 6. 기자 일의 분할 — 한 세션이 끝낼 수 있는 단위
- 데스크가 `desk.json` 에 `shards` 를 쓴다: `{"delta":{"A":[web_card_id…],"B":[…]}, "deep":{"A":[…],"B":[…]}}`.
  delta 는 `action=render`·`merged_into` 없음인 카드를 handoff 순서대로 앞 절반(A)·뒤 절반(B)으로, deep 은 `deep_analysis_ready`·`ncr_translation_ready` 카드를
  관찰 건수(483 `observations`)가 비슷하도록 둘로 나눈다. 비어 있어도 키는 둔다.
- 샤드 하나는 자기 목록의 카드**만** 쓴다. 결과는 `delta.A.json` · `delta.B.json` · `deep.A.json` · `deep.B.json` — 형태는 `reporter.md`.
- 샤드는 서로 기다리지 않는다. 예외 하나: 델타-B 는 `tldr` 때문에 `delta.A.json` 의 `title_issue` 를 읽는다 — A 가 `완료`가 아니면 B 도 `보류`.
- 이미 자기 결과 파일이 `완료`로 있으면 일을 다시 하지 않고 종료한다(재실행 안전).
- 합치기는 LLM 이 하지 않는다: 교열이 `python company/editorial/merge_parts.py company/run/{run_date_kst}` 를 돌려
  `delta.json`·`deep_delta.json` 을 만든다. 계획과 한 장이라도 다르면 exit 1 → 교열 `보류` → 백업.
