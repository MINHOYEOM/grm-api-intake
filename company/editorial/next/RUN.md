# 실행 규칙 — 브리프편집팀이 월요일 실제 발행을 맡는다 (2026-W45 부터, 대표 결정 2026-10-31)

> **이 파일은 다음 판(시범) 지시문이다 — 실제 월요일 발행은 `company/editorial/RUN.md` 을 읽는다.**
> 리허설(`company/editorial/REHEARSAL.md`)만 이 폴더(`company/editorial/next/`)를 읽는다. 전환 때 이 내용이 실발행 파일로 옮겨진다.
> 이 파일 안에서 `RUN.md`·`desk.md`·`reporter.md`·`copyedit.md` 라고만 적힌 곳은 같은 폴더(`next/`)의 파일을 뜻한다.

데스크(07:20, 재시도 07:45) → 기자 워커 풀(08:00, 보강 08:40 · 샤드 수 가변) → 교열(09:05) 순서로 돌고, **교열이 통과시킨 것만** 발행 경로에 넘긴다.
현행 v16 Routine 은 09:30 **백업**으로 남는다. (2026-11-02 시범에서 handoff 가 07:05 에야 생기고 기자 한 세션이 분량을 못 감당해 시각표·분량을 바꿨다 — 아래 5·6항.) 이 파일의 규칙은 직무 지시문과 v16 본문보다 **우선한다.**

## 0. 이번 호의 길 (사람이 바꾸는 두 줄 — 전환한 호부터)
- `handoff 길: git` — handoff 를 저장소 브랜치 `claude/queue-handoff` 의 파일에서 읽는다(2항). 수집기가 DB 로 만든 것이다.
- `반납 길: git` — 교열이 통과시킨 델타를 Notion 에 예치하지 않는다. 실행 폴더에 push 한 파일을 발행 브릿지가 가져간다(`copyedit.md` 마감).
  저장소 변수 `GRM_DELTA_SOURCE=git` 일 때만 성립한다. 이 줄이 `notion` 이면 교열은 종전처럼 v16 [산출물 — 델타 Notion 예치] 를 한다.
- 백업 Routine(09:30)은 바뀌지 않는다 — Notion handoff 를 읽고 Notion 에 예치한다.
- 리허설(실제 발행 없이 같은 순서를 돌려 보는 것)은 `REHEARSAL.md` 가 이 파일보다 우선한다.

## 1. Notion 쓰기는 교열의 마감 단계만 — handoff 페이지 하나
- 데스크·기자는 Notion 을 **읽기만** 한다. 어떤 페이지·속성도 만들거나 바꾸지 않는다.
- 교열도 판정이 **전부 통과**일 때만, `copyedit.md` 의 "마감" 순서대로만 쓴다. 쓰는 것은 **Notion handoff 페이지를 CONSUMED 로 닫는 것 하나**다
  (백업 Routine 이 같은 호를 한 번 더 발행하지 않게). 행마다의 Status 는 교열이 찍지 않는다 — 코드가 찍는다
  (`grm_queue.py notion-sync`, 발행 브릿지가 델타를 가져간 직후).
- 하나라도 통과가 아니면 교열은 Notion 에 **아무것도 쓰지 않고** 끝낸다. 그러면 Notion handoff 가 `OPEN` 으로 남아
  09:30 백업 Routine 이 평소대로 발행한다. 이것이 품질이 떨어지지 않게 하는 안전장치다.

## 2. handoff 선택 = 저장소 파일 (날짜·요일 규칙은 v16 [0단계] 그대로)
```
D=<이번 주 월요일 KST, YYYY-MM-DD>; mkdir -p _handoff
git fetch origin claude/queue-handoff && git show FETCH_HEAD:handoff_$D.json > _handoff/handoff_$D.json
```
- 파일이 없거나 비어 있으면 `상태: 보류` 로 종료한다(수집이 아직 안 끝났거나 DB handoff 생성 실패 — 백업이 한다). 다른 날짜의 파일로 대신하지 않는다.
- 그리고 Notion 에서 제목 `OPEN GRM Routine Handoff $D` 페이지가 **OPEN(Status=New)** 인지 읽기만 한다. 없거나 이미 `CONSUMED` 면 `상태: 보류`(이미 누가 처리했다는 뜻).
- 이 파일의 `rows[]` 가 유일한 입력이다. 행에 Notion `page_id` 는 없다(쓰지 않는다). 카드 키는 `web_card_id`.
- 월요일 파일은 **07:00 KST 부터 동결**된다 — 그 뒤에 수집을 다시 돌려도 바뀌지 않는다(그 전의 재실행은 파일을 갱신한다). 그래서 데스크·기자·교열이 같은 파일을 본다.

## 3. 중간 결과는 git 브랜치에
- 브랜치: `claude/company-{run_date_kst}` (예: `claude/company-2026-11-02`). 아래 순서로 올라탄다(`B` = 브랜치 이름).
  ```
  if git fetch origin "$B"; then git checkout -B "$B" FETCH_HEAD; else git fetch origin main && git checkout -b "$B" FETCH_HEAD; fi
  ```
  - **`origin/main` 이라는 이름을 쓰지 않는다.** 실행 환경에 저장된 `origin/main` 은 며칠 전 것일 수 있다(2026-11-13 리허설: 11-06 커밋이었고,
    거기서 브랜치를 만들자 이 폴더와 `handoff_ref.py` 가 사라졌다). 새 브랜치는 반드시 방금 `git fetch origin main` 한 `FETCH_HEAD` 에서 만든다.
  - 브랜치에 올라탄 직후 `test -f company/editorial/handoff_ref.py` 를 확인한다. 없으면 낡은 커밋 위에 있는 것이다 — 아무것도 쓰지 말고 `상태: 실패`, 사유 `낡은 main 에서 만든 브랜치` 로 종료한다.
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
| 07:20 | 데스크 | Sonnet | handoff 읽기 · 조치 배정 · **샤드 계획**(`plan_shards.py` → `desk.json` 의 `shards`, 6항) |
| 07:45 | 데스크 재시도 | Sonnet | 07:20 에 `desk.json` 이 `완료`면 곧바로 종료. 아니면(handoff 없음·실패) 다시 한다 |
| 08:00 | 델타 워커 ×4 (`델타-1`~`델타-4`) | Opus | 7항 루프 — 빈 샤드를 가져와 `delta.{S}.json`, 델타가 다 끝나면 `T`(tldr) |
| 08:00 | 심층 워커 ×3 (`심층-1`~`심층-3`) | Sonnet | 7항 루프 — 빈 샤드를 가져와 `deep.{S}.json` |
| 08:40 | 보강 워커 (델타 ×2·심층 ×2, 같은 이름 뒤에 `+`) | 위와 같음 | 같은 루프. 할 일이 없으면 바로 종료(비용 거의 0). 죽은 워커의 점유를 이어받는다 |
| 09:05 | 교열 | Sonnet | 샤드 합치기 → 코드 검사 → 의미 점검 → 전부 통과면 반납·마감. **미완 샤드가 있으면 09:20 까지 3분마다 다시 확인** |
| 09:30 | 백업 (v16 Routine) | Opus | Notion handoff 가 아직 OPEN 이면 예전 방식 그대로 — 마지막 안전망(변경 없음) |

교열은 09:30 전에 마감까지 끝내야 한다 — 마감(Notion handoff CONSUMED)이 늦으면 백업과 **이중 발행**된다.
09:25 가 되도록 마감 2번(Notion handoff CONSUMED)을 못 했으면 `copyedit.json` 을 `실패` 로 고쳐 push 하고 끝낸다 — 브릿지는 `완료` 가 아닌 반납을 가져가지 않고, 백업이 이어받는다.
(`반납 길: notion` 인 호는 종전 규칙: 09:20 이 되도록 예치 검증이 끝나지 않았으면 예치 페이지를 VOID 처리하고 `실패`.)

### 5-1. 재작업 1회 — 반려 카드를 기자가 고쳐 다시 교열받는다 (2026-11-14 대표 결정)
| 시각 | 직무 | 모델 | 하는 일 |
|---|---|---|---|
| 09:05 | 교열 (1차) | Sonnet | 코드 검사 exit 0 인데 reject 가 1~10장이면 마감하지 않고 `copyedit.json` 을 `재작업` 으로 남긴다(`copyedit.md` 3-1) |
| 1차 직후 | 재작업 기자 (`재작업-1`) | Opus | `copyedit.json` 이 `재작업` 일 때만 — 반려 카드의 지적된 슬롯만 고치고 `rework.json` `완료`(`reporter.md` "재작업"). 아니면 바로 종료 |
| 재작업 직후 | 교열 (2차) | Sonnet | `rework.json` 이 `완료` 면 다시 합치고(`merge_parts.py`) 반려됐던 카드만 다시 판정. reject 0 이면 마감, 남으면 `보류`(`copyedit.md` 3-2) |

- 재작업은 **한 번뿐**이다. 2차에도 반려가 남거나, 반려되지 않은 카드가 바뀌었거나(`precheck.py rework-diff`), 시한을 넘기면 종전대로 백업이 발행한다.
- **09:25 시한은 그대로다** — 2차 교열도 09:25 까지 마감 2번을 못 하면 `실패` 로 끝낸다. 그래서 재작업이 월요일에 실제로 돌려면
  예약 슬롯 두 개(`재작업-1`, 교열 2차)가 있어야 하고 1차 교열이 그만큼 일찍 끝나야 한다(1차 교열 약 10분·재작업 약 5분·2차 약 5분 — 리허설 1113c 실측과 어림).
  권장 슬롯: 교열 1차 08:45 · `재작업-1` 09:02(09:12 까지 대기) · 교열 2차 09:12(09:20 까지 대기). 슬롯이 없으면 1차의 `재작업` 은 아무도 이어받지 않고 백업이 발행한다 — 지금과 같다.
- 브릿지는 `copyedit.json` 이 `완료` 일 때만 반납을 가져간다. `재작업`·`보류` 는 가져가지 않는다.

## 6. 기자 일의 분할 — 샤드 수는 분량이 정한다 (가변, 카드는 줄이지 않는다)
- 데스크는 입력 JSON `{"delta":[web_card_id…(handoff 순서)],"deep":[{"id":…,"obs":483 관찰 건수}…]}` 를 만들어
  `python company/editorial/plan_shards.py <입력.json>` 을 돌리고, 출력 `shards` 를 `desk.json` 에 **그대로** 넣는다(LLM 이 나누지 않는다).
  형태: `{"delta":{"A":[…],"B":[…],…},"deep":{"A":[…],…},"tldr":"T"}` — 샤드는 A, B, C… 로 늘어난다(종류별 상한 8, 최소 2).
  delta 는 `action=render`·`merged_into` 없음 카드를 handoff 순서의 **연속 구간**으로 균등 분할(샤드당 약 18장 이하),
  deep 은 `deep_analysis_ready`·`ncr_translation_ready` 카드를 샤드당 12장·483 관찰 30건 이하로 균형 분할한다.
  **FDA 게시 기준 카드는 전부 싣는다** — 모자라면 샤드가 늘 뿐 카드를 덜어내지 않는다(`plan_shards.py` 가 상한 초과 시 경고만 낸다).
- 샤드 하나는 자기 목록의 카드**만** 쓴다. 결과는 `delta.{S}.json` · `deep.{S}.json` 과 가상 샤드 `delta.T.json`(브리프 `tldr`·`tldr_en`) — 형태는 `reporter.md`.
- `T` 는 **모든 델타 샤드가 `완료`가 된 뒤** 한 워커가 한 번 쓴다(각 샤드의 `title_issue` 를 읽는다). 델타 샤드 사이는 서로 기다리지 않는다.
- 이미 자기 결과 파일이 `완료`로 있으면 일을 다시 하지 않는다(재실행 안전).
- 합치기는 LLM 이 하지 않는다: 교열이 `python company/editorial/merge_parts.py company/run/{run_date_kst}` 를 돌려
  `delta.json`·`deep_delta.json` 을 만든다(샤드 이름은 `desk.json` 계획에서 읽는다). 계획과 한 장이라도 다르면 exit 1 → 교열 `보류` → 백업.

## 7. 끝날 때까지 이어서 한다 — 워커 루프와 잠금 (`shard_claim.py`)
워커 한 개가 한 샤드로 끝나지 않는다. 자기 일을 끝내고 시간이 남으면 다음 빈 샤드를 가져온다.
```
반복:
  S=$(python company/editorial/shard_claim.py next company/run/{D} <delta|deep>)      # 가장 번호 낮은 빈·죽은 샤드, 없으면 NONE
  NONE 이거나 08:55 KST 를 넘었으면 종료
  python company/editorial/shard_claim.py claim company/run/{D} <종류> $S <워커이름>   # 0=내가 잡음, 1=졌음 → 위로 돌아가 다음 샤드
  샤드 S 를 처리하고 결과 파일을 커밋·push (RUN.md 3항 방식) — 이것이 곧 '완료' 표시다
```
- **잠금** = `company/run/{D}/claims/{종류}.{S}.claim` 파일을 커밋해 push. push 가 거절되면 `pull --rebase` 후, 같은 파일이 남의 것이면 진 것이다(3번 재시도).
- 점유한 지 25분이 지나도록 결과 파일이 없으면 **죽은 워커**로 보고 다른 워커가 가로챈다. 한 샤드가 25분 안에 끝나지 않는 크기이면 안 되므로 샤드 한도(6항)를 그에 맞춰 둔다.
- 자기 결과를 쓰기 직전에 `desk.json` 의 목록과 자기 카드가 같은지 확인한다. 못 끝낸 샤드는 부분 결과를 `완료`로 쓰지 말고 `claim` 만 남겨 둔다 — 25분 뒤 보강 워커가 이어받는다.
- 델타 워커는 Opus, 심층 워커는 Sonnet — **종류가 다른 샤드는 가져오지 않는다**(모델 배정을 지킨다).
- 서브에이전트·병렬 세션은 쓰지 않는다(`CHARTER.md`). 분량이 늘어도 해법은 '샤드를 늘리고 워커 슬롯에 중복 배치'이지 세션 안에서 병렬화가 아니다.
- 예약작업이 다른 예약작업을 깨우거나 세션을 새로 여는 길은 **이 저장소에서 실측하지 못했다**. 확인되면 이 항을 고친다 — 그 전까지는 고정 슬롯(5항)만 쓴다.

## 8. 월요일 handoff 를 다시 만들어야 할 때 (사람만 — 편집팀 직무는 하지 않는다)
- **07:00 KST 전**: 수집을 다시 돌리기만 하면 된다(`GRM API Intake (Daily)` 재실행). 그 시각 전의 재실행은 월요일 파일을 스스로 갱신한다
  (FDA 483 본문 회수 뒤의 자동 재생성 `grm-fda-coverage-gate.yml` 도 이 길이다).
- **07:00~07:20**(파일은 동결됐고 데스크는 아직 읽기 전): 저장소 변수 `GRM_QUEUE_HANDOFF_REFRESH=true` → 수집 재실행 → 끝나면 변수를 `false` 로.
- **07:20 뒤에는 다시 만들지 않는다**(직무마다 다른 파일을 보게 된다). 이미 발행돼 닫힌 호에는 스위치도 듣지 않는다.
