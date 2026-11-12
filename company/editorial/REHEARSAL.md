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

## 순서
1. 데스크 — `desk.md` 그대로. `desk.json` 에 `handoff_ref` 가 들어가야 한다.
2. 기자 워커 — `reporter.md` 그대로. 샤드 수는 계획대로(줄이지 않는다). 워커 수는 줄여도 된다(한 워커가 여러 샤드를 이어서 한다).
3. 교열 — `copyedit.md` 0~3번 그대로, 4번은 위 표대로 1번까지만.
4. 점검(사람 또는 비서실 세션, PC 에서):
   ```
   git fetch origin claude/company-rehearsal-{회차} claude/queue-handoff
   git worktree add ../_rehearsal FETCH_HEAD            # 또는 git show 로 폴더만 꺼낸다
   git show origin/claude/queue-handoff:handoff_2026-11-16.json > _handoff_2026-11-16.json
   python company/editorial/rehearsal_check.py ../_rehearsal/company/run/rehearsal-{회차} _handoff_2026-11-16.json
   ```

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
