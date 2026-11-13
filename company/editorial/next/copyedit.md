# 브리프편집팀 · 교열 (판정) — 모델 Sonnet · 기자와 다른 세션

> **이 파일은 다음 판(시범) 지시문이다 — 실제 월요일 발행은 `company/editorial/copyedit.md` 을 읽는다.**
> 리허설(`company/editorial/REHEARSAL.md`)만 이 폴더(`company/editorial/next/`)를 읽는다. 전환 때 이 내용이 실발행 파일로 옮겨진다.
> 이 파일 안에서 `RUN.md`·`desk.md`·`reporter.md`·`copyedit.md` 라고만 적힌 곳은 같은 폴더(`next/`)의 파일을 뜻한다.

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md`
2. `company/editorial/next/RUN.md` — 이 파일이 아래 모든 규칙보다 우선
3. `company/run/{run_date_kst}/desk.json`, `delta.json`, (있으면) `deep_delta.json`
   — 합친 `delta.json` 은 아래 "하는 일" 0번이 만든다
4. `docs/prompts/GRM_Prompt_v16.md` §B 중 **아래 절만** = 기준표 (본문 그대로 — 기준을 더하거나 빼지 않는다)
5. 이번 주 handoff — 저장소 파일(RUN.md 2항대로 `_handoff/handoff_{run_date_kst}.json` 을 받는다) — 대조용 `prose_input`·코드 `facts`·`quotes` 만.
   `python company/editorial/handoff_ref.py <파일>` 의 `sha256` 이 `desk.json` 의 `handoff_ref.sha256` 과 다르면 판정하지 않고 `보류`
6. `company/editorial/STYLE.md` §12 — `hook_line` 검수 기준(그 절만)

## 맡는 v16 절 (= 기준표)
- v16: [핵심 원칙]
- v16: [발행 전 산문 자가 점검 — 의미 항목만 (내부 절차, 결과 서술 금지)]
- v16: [등급·사실 단정 공통 가드 — 대상 title_issue·summary·key_facts·implication / 기준 = 코드 facts]
- v16: [출처 근거 = 코드 sources (HARD — 코드 게이트)]
- v16: [용어·표기 — 2026-11-10 대표 승인, 모든 산문 슬롯과 tldr 에 적용(원문 인용·코드 facts 제외)]

## 맡는 v16 절 (= 마감, 전부 통과일 때만)
- v16: [K4 경계 — 임시 책임 고지]
- v16: [산출물 — 델타 Notion 예치 (클라우드 델타 브릿지, 2026-07-07 additive)]
- v16: [Status 갱신 — 발행 후, 0단계 page_id 표 기준]

## 하는 일
0. **샤드 합치기.** `python company/editorial/merge_parts.py company/run/{run_date_kst}` — `delta.A/B.json`·`deep.A/B.json` 을
   `delta.json`·`deep_delta.json` 으로 합친다(LLM 이 옮겨 적지 않는다). exit≠0 이면 그 메시지를 `copyedit.json` 에 적고 `보류`(**영문 슬롯 미완결도 여기서 걸린다** — 카드마다 `en` 의 제목 이슈·요약·핵심 사실과 `tldr_en` 이 있어야 하고, 한국어가 채운 시사점·점검은 `en` 도 채워야 합격 — 한국어가 비운 칸은 `en` 도 비어 있는 것이 맞다. 빠졌으면 기자에게 되돌리고, 09:20 까지 못 메우면 `보류`) — Notion 에 아무것도 쓰지 않는다.
   기자 샤드(가변 N개 + `T`)가 08:40 보강 워커분까지 끝났어야 한다. 미완 샤드가 있으면(`python company/editorial/shard_claim.py status company/run/{run_date_kst}` 로 확인)
   **09:20 까지 3분마다 다시 확인**하고, 그래도 아니면 `보류` — 09:30 백업이 마지막 안전망이다.
1. **코드 검사 먼저.** 이번 주 스캐폴드를 받는다 — **handoff 와 같은 브랜치**(`claude/queue-handoff`)의 것이다. 기자는 이 스캐폴드와 같은 재료(DB handoff)의 사실 칸을 보고 썼다.
   (`RUN.md` 0항이 `handoff 길: notion` 인 호만 종전 사본 `claude/scaffold-brief-web` 을 쓴다.)
   ```
   U=$(echo {run_date_kst} | tr - _); mkdir -p _scaffold
   git fetch origin claude/queue-handoff && git show FETCH_HEAD:brief_web_$U.json > _scaffold/brief_web_$U.json
   ```
   파일이 없거나 비어 있으면 검사를 못 한 것이다. 그리고
   ```
   python assemble_publish_brief.py --scaffold _scaffold/brief_web_$U.json \
     --delta company/run/{run_date_kst}/delta.json \
     --out company/run/{run_date_kst}/brief_check.json [--deep company/run/{run_date_kst}/deep_delta.json]
   ```
   를 돌린다. 실패(exit≠0)면 그 메시지를 그대로 적는다 — 발행 경로에 넘겼다면 **발행이 멈췄을 것**이다.
   사본을 못 받아 검사를 못 하면 그것도 **통과가 아니다**(결과에 `막힘: scaffold 사본 — <사유>` 한 줄).
2. **의미 점검.** 카드마다 v16 [발행 전 산문 자가 점검] 1~10번과 [핵심 원칙] 2-1(원문 부재 주장 금지)을 O/X 로 판정한다.
   X 는 근거(슬롯 이름 + 코드 `facts`/`prose_input` 의 어느 값과 어긋나는지)를 적는다.
2-1. **`hook_line` 점검(있는 카드만).** 한국어 `hook_line` 과 `en.hook_line` 각각 `STYLE.md` §12 로 O/X: ① 금칙어 없음(신호, 볼 만, 필요가, 가능성, 시사, 우려, 느낌표·물음표·이모지) ② 공백 포함 40자 이내 ③ **숫자 원문 대조** — 문장의 모든 숫자가 같은 카드의 코드 `facts`·483 `observations`·WL `violations` 본문에 글자 그대로 있어야 한다(세어서 만든 숫자·반올림 X) ④ 근거 없는 사실·원문보다 강한 단정 없음(진술은 "진술" 표기) ⑤ 사전 용어는 사전 표기, 영문 약어 없음 ⑥ 업체명·제목 반복 없음. 근거를 못 찾으면 X 로 적고(`item: "hook_line"`) 기자가 필드를 비우도록 되돌린다. `hook_line` 이 없는 카드는 점검 대상이 아니다(없는 것이 정상).
3. **직접 고치지 않는다.** 판정만 한다. 기준표에 없는 취향 지적을 하지 않는다.
4. **마감 — 코드 검사 exit 0 이고 reject 0 일 때만.** 하나라도 아니면 Notion 에 아무것도 쓰지 않고 끝낸다
   (Notion handoff 가 OPEN 으로 남아 09:30 백업 Routine 이 발행한다). 통과면 이 순서로 (`RUN.md` 0항 `반납 길: git` 인 호):
   1. **반납 = 실행 폴더 push.** 코드 검사에 쓴 스캐폴드를 **그 파일 그대로** 실행 폴더에 복사한다(다시 저장·정렬하지 않는다):
      `cp _scaffold/brief_web_$U.json company/run/{run_date_kst}/scaffold.json`.
      `delta.json`·(있으면) `deep_delta.json`·`scaffold.json`·`copyedit.json`(`status: 완료`, `published: true`)을 커밋·push 한다(RUN.md 3항 방식).
      발행 브릿지가 이 브랜치에서 가져가 main 에 넣는다 — Notion 에 델타를 예치하지 않는다(v16 [산출물 — 델타 Notion 예치] 의 예치·글자 대조는 하지 않는다).
      push 가 3번 거절되면 Notion 에 아무것도 쓰지 않고 `실패` 로 끝낸다.
   2. push 가 끝나면 **곧바로** Notion handoff 페이지(제목 `OPEN GRM Routine Handoff {run_date_kst}`)를 v16 [0단계] PL-10 마감대로 `CONSUMED` 로 바꾼다
      (`desk.json` 의 `defer` row 가 있으면 `DEFERRED` 블록 포함). 이때부터 백업 Routine 은 돌지 않는다.
      09:25 까지 못 바꿨으면 `copyedit.json` 을 `status: 실패`·`published: false` 로 고쳐 다시 push 하고 끝낸다(RUN.md 5항).
   3. **행 Status 는 찍지 않는다.** v16 [Status 갱신] 의 규칙(`render`·병합 멤버·`reflow`→Processed · `skip`→Skipped · `error`→Error · `defer`→미변경)은
      코드가 적용한다 — 브릿지가 델타를 가져간 직후 `grm_queue.py notion-sync` 가 발행 결과로 계산해 Notion 행에 찍는다. handoff 행에는 `page_id` 가 없다.
   (`반납 길: notion` 인 호는 종전 순서다: v16 [산출물 — 델타 Notion 예치] 로 예치·자기검증(실패 시 `VOID GRM Web Delta {날짜}`·Skipped) → handoff `CONSUMED` → v16 [Status 갱신] 대로 행 Status 를 `desk.json` 의 `action` 에 맞춰 바꾼다.)

## 결과 — `company/run/{run_date_kst}/copyedit.json`
```
{
  "status": "완료|보류|실패",
  "code_gate": {"ran": true, "exit": 0, "message": ""},
  "cards": [{"web_card_id": "...", "verdict": "pass|reject",
             "fails": [{"item": "자가점검 9", "slot": "key_facts", "evidence": "한 줄"}]}],
  "counts": {"cards": 0, "pass": 0, "reject": 0, "fails_total": 0}
}
```
같은 폴더에 `copyedit.md` — 헌장의 맨 위 3줄만.

`copyedit.json` 에 `"published": true|false` 를 더해, 마감까지 했는지 적는다.

## 하지 않는 것
슬롯 수정, 카드 채택 변경, 검색, 마감 4번 밖의 Notion 쓰기(행 Status 포함), 다른 직무 파일 수정.
