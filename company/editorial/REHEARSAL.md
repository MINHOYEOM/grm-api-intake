# 리허설 — 실제 발행 없이 데스크 → 기자 → 교열을 돌려 본다 (2026-11-18·11-20, 11-23 전환 전)

이 파일은 리허설 실행에서 `RUN.md` 와 직무 지시문보다 **우선한다.** 리허설이 아닌 월요일 실행은 이 파일을 읽지 않는다.
목적은 하나다: 편집팀이 **저장소의 handoff 파일만으로** 끝까지 갈 수 있는지 본다. 결과물은 발행하지 않는다.

## 리허설이 바꾸는 것 (나머지는 RUN.md·직무 지시문 그대로)
| 항목 | 월요일 실행 | 리허설 |
|---|---|---|
| handoff 날짜 `D` | 이번 주 월요일 | **`2026-11-16`** (지난 월요일분. 브랜치 `claude/queue-handoff` 에 8일간 남아 있다) |
| 브랜치 | `claude/company-{D}` | **`claude/company-rehearsal-{회차}`** (회차 = `1118`, `1120`) |
| 폴더 | `company/run/{D}/` | **`company/run/rehearsal-{회차}/`** |
| Notion handoff 가 OPEN 인지 확인(RUN.md 2항) | 한다 | **하지 않는다**(11-16 호는 이미 닫혀 있다) |
| 시각·대기(07:20, 09:20 까지 재확인 등) | 지킨다 | 무시한다. 앞 직무가 끝나면 바로 다음 직무 |
| 검색 보강(v16 [1단계]) | WebSearch 9·WebFetch 5 | 그대로(한도 같음) |
| 교열 마감 4번 | 반납 push → Notion CONSUMED | **1번(실행 폴더 push)까지만.** `copyedit.json` 은 `status: 완료`, `published: false`, `"rehearsal": true` |

## 금지 (하나라도 어기면 그 회차는 실패)
- Notion 에 쓰기 — 어떤 페이지·속성도. (읽기는 리허설에서 필요 없다.)
- 브랜치 `claude/company-2026-11-16` 등 **날짜 브랜치**에 쓰기, `main` 에 쓰기, PR 열기.
- 워크플로 실행 요청(`claude/request-*` 마커), Supabase 쓰기, 외부 발송.

발행 브릿지와 발행 워크플로는 `claude/company-{날짜}` 브랜치와 main 의 델타만 본다. 리허설 브랜치·폴더는 닿지 않는다.

## 실행표 — 회차마다 같은 순서 (비서실이 직무 Routine 을 수동 실행한다)
각 Routine 을 수동 실행할 때 프롬프트 맨 앞에 이 한 줄을 더한다: `company/editorial/REHEARSAL.md 를 먼저 읽고 그 규칙을 우선한다. 회차 {회차}.`
(워커 Routine 은 원래 있던 `워커: 델타-1` 같은 줄을 그대로 둔다.)

| 순서 | 회차 1118 (11-18 수) | 회차 1120 (11-20 금) | 돌릴 Routine | 다음으로 넘어가는 조건 |
|---|---|---|---|---|
| 1 | 13:00 | 13:00 | 데스크 (1개) | 리허설 브랜치의 `desk.json` 이 `완료`이고 `handoff_ref` 가 있다. `보류`·`실패`면 여기서 멈추고 사유를 적는다 |
| 2 | 데스크 완료 직후 (13:30 쯤) | 같음 | 델타 워커 2개(`델타-1`·`델타-2`) + 심층 워커 2개(`심층-1`·`심층-2`) | — (동시에 시작) |
| 3 | 순서 2 시작 40분 뒤 (14:10 쯤) | 같음 | 보강 워커: 델타 1개(`델타-1+`) + 심층 1개(`심층-1+`) | `python company/editorial/shard_claim.py status <실행 폴더>` 가 전 샤드 완료 + `delta.T.json` 완료. 미완이 남으면 보강 워커를 한 번 더 돌린다 |
| 4 | 전 샤드 완료 직후 (15:00 쯤) | 같음 | 교열 (1개) | `copyedit.json` 이 push 됐다 |
| 5 | 교열 뒤 | 같음 | (Routine 아님) 아래 "점검" — 비서실 세션이 PC 에서 | `rehearsal_check.py` 결과 + 6번 확인을 적는다 |

- 시각은 어림이다. **앞 순서의 조건이 채워지면 바로 다음을 돌린다.** 월요일용 시한은 리허설에 적용하지 않는다:
  워커 루프의 "08:55 KST 를 넘었으면 종료", 교열의 "09:20 까지 재확인"·"09:25 까지 마감", 데스크의 07:45 재시도.
- 워커 수는 월요일(델타 4·심층 3·보강 4)보다 적다. 샤드 수는 계획대로이고, 한 워커가 빈 샤드를 이어서 가져간다(RUN.md 7항 루프 그대로 — 점유 25분 규칙도 그대로).
  걸린 시간을 재려면 월요일과 같은 수로 돌려도 된다. 그때는 순서 2 에서 델타 4·심층 3 을 함께 시작한다.
- 한 회차 안에서 같은 Routine 을 다시 돌려도 된다(이미 `완료`인 결과는 건너뛴다 — 재실행 안전).
- 회차 1120 은 1118 의 결과를 쓰지 않는다. 브랜치·폴더가 다르므로 처음부터 다시 한다. 1118 에서 지시문을 고쳤다면 그 수정이 main 에 머지된 뒤에 1120 을 돌린다.

## 직무별로 달라지는 점
1. 데스크 — `desk.md` 그대로. `desk.json` 에 `handoff_ref` 가 들어가야 한다.
2. 기자 워커 — `reporter.md` 그대로. 샤드 수는 계획대로(줄이지 않는다).
3. 교열 — `copyedit.md` 0~3번 그대로, 4번은 위 표대로 1번까지만.

## 점검
(사람 또는 비서실 세션, PC 에서)
```
git fetch origin claude/company-rehearsal-{회차} claude/queue-handoff
git worktree add --detach ../_rehearsal-{회차} origin/claude/company-rehearsal-{회차}
git show origin/claude/queue-handoff:handoff_2026-11-16.json > _handoff_2026-11-16.json
python company/editorial/rehearsal_check.py ../_rehearsal-{회차}/company/run/rehearsal-{회차} _handoff_2026-11-16.json
```
저장소 루트(main 체크아웃)에서 돌린다 — 점검 도구는 main 의 코드로 판정한다. 끝나면 `git worktree remove ../_rehearsal-{회차}`.

## 성공 판정 — `rehearsal_check.py` 가 전부 `통과` 를 내고, 아래 6번을 사람이 확인하면 1회 성공
1. `desk.json` 이 `완료`이고 `handoff_ref` 가 handoff 파일의 지문과 같다.
2. 계획한 샤드가 전부 `완료`이고 `delta.json` 이 있다(`merge_parts.py` exit 0 의 결과).
3. `copyedit.json` 의 코드 검사가 `ran: true`, `exit: 0` 이다.
4. 교열 reject 가 0 이다.
5. `scaffold.json` 이 그 날짜의 것이고 델타의 카드를 전부 갖고 있다. handoff 의 모든 행이 상태를 하나씩 받는다(발행·미채택·보류·오류 중 하나 — 빈 행 0).
6. (사람) Notion·main·날짜 브랜치에 리허설이 쓴 것이 없다 — Notion handoff·Intake 행의 마지막 수정 시각, `git log origin/main`, `git ls-remote origin 'claude/company-2026-11-16'` 로 본다.

두 회차(11-18, 11-20) 모두 성공이면 11-23 에 전환한다. 한 번이라도 실패면 원인을 고치고 11-21 에 한 번 더 한다. 그래도 안 되면 11-30 으로 미룬다.

## 리허설에서 함께 볼 것 (판정에는 넣지 않는다)
- 걸린 시간: 데스크, 샤드별, 교열. 09:05~09:25 안에 교열이 끝날 분량인지.
- 11-16 실제 발행본과 카드 수·채택 카드가 얼마나 다른지(같을 필요는 없다 — 다른 재료·다른 실행이다).
- 발행 조립 dry: 점검 세션이 `python assemble_publish_brief.py --scaffold <scaffold.json> --delta <delta.json> --out _rehearsal_brief.json [--deep <deep_delta.json>]` 를 한 번 돌려 exit 0 인지 본다(교열 코드 검사와 같은 명령 — 발행 워크플로를 부르지 않는다).
