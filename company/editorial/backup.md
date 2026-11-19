# 브리프편집팀 · 백업 v17 — 편집팀이 마감하지 못한 호를 혼자 이어 마감한다 — 모델 Opus · 월 09:30 KST

v16 백업 Routine(Notion handoff → Notion 예치)을 대신한다. **Notion 은 읽지도 쓰지도 않는다.** 입력은 편집팀과 같은 저장소 handoff,
반납은 편집팀과 **같은 파일·같은 형식**을 같은 브랜치의 하위 폴더 `backup/` 에 push 하는 것이다. 발행 브릿지(`delta_bridge.py`)는 편집팀 반납을 먼저 보고,
그것을 쓸 수 없을 때만 이 폴더를 같은 채택 조건으로 가져간다. 이 파일은 `RUN.md`·직무 지시문보다 우선한다(아래에 적은 것만 — 나머지는 그 파일들 그대로).

## 값
- `D` = 이번 주 월요일(KST, `YYYY-MM-DD`) — 오늘 날짜. `U` = `D` 의 `-` 를 `_` 로. `B` = `claude/company-$D`.
- 편집팀 폴더 `E` = `company/run/$D/` · **백업 폴더 `F` = `company/run/$D/backup/`** — 백업이 쓰는 곳은 `F` 하나뿐이다. `E` 의 파일은 읽기만 한다.
- 리허설이면 맨 앞 줄이 `리허설 <회차> 재료 <D>` 다 — 아래 "리허설" 절을 먼저 본다.

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md` — 단, 이 실행은 데스크·기자·교열을 **한 세션이 차례로** 한다(직무 1개 = "백업"). 서브에이전트·병렬 작업 금지는 그대로.
2. 이 파일.
3. `company/editorial/RUN.md` 2·3·6항, `company/editorial/desk.md`·`company/editorial/reporter.md`·`company/editorial/copyedit.md` — 그 파일의 `company/run/{run_date_kst}/` 를 **`F` 로 바꿔 읽는다**.
   시각표(RUN.md 5항)·대기(5-2)·워커 루프와 잠금(7항)·재작업(5-1)은 **따르지 않는다** — 백업의 시각과 순서는 이 파일이 정한다.
4. 그 파일들이 가리키는 `docs/prompts/GRM_Prompt_v16.md` §B 의 절 — 직무 지시문의 "맡는 v16 절" 그대로(요약·재해석 금지). `company/editorial/STYLE.md` §12.

## 0. 시작 판정 — 코드가 정한다
```
pip install -q -r requirements.txt
python company/editorial/backup_gate.py $D
```
첫 줄이 `RUN` 이면 1번으로. `WAIT` 면 5분 뒤 다시(`sleep` 은 한 번에 5분 이하, 기다리는 동안 다른 일 금지). `SKIP` 이면 정상 — 아무것도 쓰지 않고 끝낸다.
`STOP` 이면 2번 기록만 남기고 끝낸다(대표가 정한다). 판정 근거는 둘째 줄 JSON 이다 — 다르게 해석하지 않는다.
- `RUN` 의 뜻: main 에 이번 호 델타가 없고, 브릿지가 편집팀 반납을 쓸 수 없고(`delta_bridge.load_git_run`·`load_run_scaffold` — 브릿지와 같은 함수),
  편집팀이 스스로 끝냈거나(교열 `보류`·`실패`, 교열 없이 데스크 `보류`·`실패`) 09:40 KST 가 지났다(교열 마감 09:25).
- 11:00 KST 가 지나도록 `RUN` 이 안 나오면 `STOP` 이 나온다. 그 뒤에 시작하지 않는다.

## 1. 맡기 (잠금)
3항 방식으로 `B` 에 올라탄다(`git fetch origin "$B"` 가 되면 그 위에, 안 되면 방금 받은 main `FETCH_HEAD` 에서 — `origin/main` 이라는 이름은 쓰지 않는다).
`test -f company/editorial/backup_gate.py` 가 실패하면 낡은 커밋이다 — 아무것도 쓰지 않고 `실패`.
```
mkdir -p company/run/$D/backup
python company/editorial/shard_claim.py claim company/run/$D/backup backup run 백업
```
exit 1 = 다른 백업이 하는 중 — 아무것도 쓰지 않고 끝낸다. 점유는 25분이 지나면 남이 가로챌 수 있으므로 **아래 단계가 하나 끝날 때마다 같은 명령으로 다시 맡는다**(같은 이름이면 시각만 새로 쓴다).

## 2. 기록 — 끝날 때 반드시 (어떤 결과든)
`F/backup.md` — 헌장의 맨 위 3줄(`상태: 완료|보류|실패` · `한 일:` · `대표가 볼 것:`) + 4번째 줄 `판정: <backup_gate 첫 줄> — <사유>`.
`SKIP` 이면 이 파일도 쓰지 않는다. 커밋은 `git add <그 파일들>` 로만(`git add -A` 금지), push 전 `git pull --rebase origin "$B"`, 거절되면 3번까지.

## 3. 재료 받기
RUN.md 2항대로 `_handoff/handoff_$D.json`, 교열 1번대로 `_scaffold/brief_web_$U.json` 을 받는다(브랜치 `claude/queue-handoff`).
`python company/editorial/handoff_ref.py _handoff/handoff_$D.json` 이 exit≠0 이면 `실패`.

## 4. 데스크 — 편집팀 것을 쓸 수 있으면 그대로 쓴다
- `E/desk.json` 이 `완료` 이고 그 `handoff_ref.sha256` 이 3번 출력의 `sha256` 과 같으면 **그 파일을 그대로 `F/desk.json` 으로 복사**한다(검색을 다시 하지 않는다).
- 아니면 `desk.md` "하는 일" 0~3번을 `F` 에 한다(조치 배정 · `plan_shards.py` 샤드 계획 · v16 [1단계] 검색 WebSearch 9·WebFetch 5).
  **카드를 덜어내지 않는다** — `defer` 는 v16 [0단계] 의 원래 뜻(용량 초과 보류)대로만, 데스크 규칙 그대로.

## 5. 기자 — 샤드를 차례로 (한 세션)
`F/desk.json` 의 `shards` 순서대로 델타 샤드 A, B, … → `T` → 심층 샤드 A, B, … 를 **혼자 차례로** 쓴다(`reporter.md` "하는 일" 1~6번, 결과 형태 그대로).
- **편집팀 샤드를 다시 쓰지 않는다**: 4번에서 편집팀 `desk.json` 을 복사했고 `E/<같은 이름>.json` 이 `완료` 면 그 파일을 `F` 로 복사한다. 단
  `E/copyedit.json` 의 `cards` 에서 `verdict: reject` 인 카드는 그 카드가 든 샤드를 복사한 뒤 `fails` 의 슬롯만 근거대로 고친다(`reporter.md` "재작업" 2번과 같은 방식).
- 샤드를 하나 끝낼 때마다 `reporter.md` 6번 코드 점검(델타 `precheck.py terms F/delta.{S}.json` · 심층 `precheck.py deep _handoff/handoff_$D.json F/deep.{S}.json`)이 exit 0 이어야 `완료` 로 쓴다.
- 순서는 **델타 먼저**다. 심층은 v16 에서 선택(additive)이다: 심층 샤드는 12:00 KST 전에 시작할 수 있는 것만 쓰고, 못 쓴 심층 샤드는 계획에서 빼지 말고
  `{"status":"완료","run_date_kst":"$D","shard":"<S>","items":{}}` 로 낸다(그 카드는 심층 없이 나간다 — `F/backup.md` 에 몇 장인지 적는다).
- 12:45 KST 까지 델타 샤드와 `T` 가 끝나지 않으면 반납하지 않는다 — `실패`(부분 델타는 발행을 막는다).

## 6. 교열 — 코드 검사 + 자가 점검
1. `python company/editorial/merge_parts.py company/run/$D/backup` — exit≠0 이면 메시지대로 그 샤드를 고쳐 다시(같은 오류 2번이면 `실패`).
2. `copyedit.md` 1번의 코드 검사를 `F` 로: `precheck.py deep-source` 로 검사용 사본(`_scaffold/deep_check.json`) → `assemble_publish_brief.py --scaffold _scaffold/brief_web_$U.json --delta F/delta.json --out F/brief_check.json [--deep _scaffold/deep_check.json]`.
   실패면 걸린 카드의 슬롯만 고쳐(샤드 파일에서) 1번부터 다시 — 세 번 해도 exit 0 이 아니면 `실패`.
3. 카드마다 v16 [발행 전 산문 자가 점검] 1~10번 · [핵심 원칙] 2-1 · (있으면) `hook_line` `STYLE.md` §12 · 심층 '3-1. 문체' 를 O/X 로 본다(`copyedit.md` 2·2-1번 기준 그대로).
   X 는 **스스로 고친다**(백업에는 되돌릴 기자가 없다) — 고친 뒤 1·2번을 다시 돌린다. 고칠 수 없는 X 가 남으면 반납하지 않는다(`보류`).
4. 통과면 `cp _scaffold/brief_web_$U.json F/scaffold.json`(다시 저장·정렬하지 않는다) 하고 `F/copyedit.json` 을 `copyedit.md` 결과 형태로 쓴다:
   `"status":"완료"` · `"round":1` · `"published":true` · `"by":"backup"` · `"code_gate":{"ran":true,"exit":0,"message":""}` · 카드마다 `verdict: "pass"` · `counts.reject: 0`
   · `"reused":{"desk":true|false,"shards":["delta.A", …]}`(편집팀 것을 복사한 파일).
5. **반납 전 점검** — 둘 다 통과해야 push 한다:
   ```
   python company/editorial/rehearsal_check.py company/run/$D/backup _handoff/handoff_$D.json     # 1~5 전부 통과
   python company/editorial/backup_gate.py $D --final                                            # 첫 줄 RUN
   ```
   `--final` 이 `SKIP` 이면(그 사이 편집팀 반납이 쓸 수 있게 됐거나 main 에 델타가 생겼다) `copyedit.json` 의 `status` 를 `보류`, `published` 를 `false` 로 고쳐 쓴다 — 발행 길에 두 번째 반납을 올리지 않는다.
6. `F` 의 `desk.json`·샤드 파일·`delta.json`·(있으면) `deep_delta.json`·`scaffold.json`·`copyedit.json`·`brief_check.json`·`backup.md` 를 `git add` 로 하나씩 올려 커밋·push(2번 방식).
   13:15 KST 까지 push 하지 못하면 `copyedit.json` 을 `실패` 로 고쳐 push 하고 끝낸다(브릿지 마지막 회차 13:37).
   브릿지가 가져가 main 에 넣으면 발행 PR·승인은 편집팀 호와 같다 — 백업은 그 뒤를 기다리지 않는다.

## 리허설 (실발행 없이 백업만 돌려 보기)
맨 앞 줄 `리허설 <회차> 재료 <D>` 이면 바뀌는 것만:
- `B` = `claude/company-rehearsal-<회차>` · `E` = `company/run/rehearsal-<회차>/` · `F` = `company/run/rehearsal-<회차>/backup/`. 날짜 브랜치(`claude/company-YYYY-MM-DD`)·main 에 쓰지 않는다.
- 판정은 `python company/editorial/backup_gate.py <D> --rehearsal <회차>` (시각·main 델타를 보지 않는다). `--final` 도 같이 `--rehearsal <회차>` 를 붙인다.
- 시각 시한(12:00·12:45·13:15)은 무시한다. `copyedit.json` 은 `"published": false, "rehearsal": true`.
- 편집팀 리허설 폴더가 있으면 4·5번 재사용 규칙이 그대로 시험된다. 없는 회차(`E` 가 빈 회차)는 처음부터 다 쓰는 경우를 시험한다.

## 하지 않는 것
Notion 읽기·쓰기 · `E`(편집팀 파일) 수정 · main push·PR·머지 · 워크플로 요청(`claude/request-*`) · Supabase 쓰기 · 행 Status 찍기(코드가 발행 결과로 계산한다).
