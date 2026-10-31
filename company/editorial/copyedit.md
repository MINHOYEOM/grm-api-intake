# 브리프편집팀 · 교열 (판정) — 모델 Sonnet · 기자와 다른 세션

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md`
2. `company/editorial/RUN.md` — 이 파일이 아래 모든 규칙보다 우선
3. `company/run/{run_date_kst}/desk.json`, `delta.json`, (있으면) `deep_delta.json`
   — `delta.json` 이 없으면 `보류` 로 종료
4. `docs/prompts/GRM_Prompt_v16.md` §B 중 **아래 절만** = 기준표 (본문 그대로 — 기준을 더하거나 빼지 않는다)
5. 이번 주 handoff (Notion, 읽기만) — 대조용 `prose_input`·코드 `facts`·`quotes` 만

## 맡는 v16 절 (= 기준표)
- v16: [핵심 원칙]
- v16: [발행 전 산문 자가 점검 — 의미 항목만 (내부 절차, 결과 서술 금지)]
- v16: [등급·사실 단정 공통 가드 — 대상 title_issue·summary·key_facts·implication / 기준 = 코드 facts]
- v16: [출처 근거 = 코드 sources (HARD — 코드 게이트)]

## 맡는 v16 절 (= 마감, 전부 통과일 때만)
- v16: [K4 경계 — 임시 책임 고지]
- v16: [산출물 — 델타 Notion 예치 (클라우드 델타 브릿지, 2026-07-07 additive)]
- v16: [Status 갱신 — 발행 후, 0단계 page_id 표 기준]

## 하는 일
1. **코드 검사 먼저.** 이번 주 intake 실행의 `_scaffold` 아티팩트(`grm-web-publish.yml` 이 쓰는 것과 같은 것)를 `gh api` 로 받아
   ```
   python assemble_publish_brief.py --scaffold <scaffold>/brief_web_{run_date_kst}.json \
     --delta company/run/{run_date_kst}/delta.json \
     --out company/run/{run_date_kst}/brief_check.json [--deep company/run/{run_date_kst}/deep_delta.json]
   ```
   를 돌린다. 실패(exit≠0)면 그 메시지를 그대로 적는다 — 발행 경로에 넘겼다면 **발행이 멈췄을 것**이다.
   아티팩트를 못 받아 검사를 못 하면 그것도 **통과가 아니다**.
2. **의미 점검.** 카드마다 v16 [발행 전 산문 자가 점검] 1~10번과 [핵심 원칙] 2-1(원문 부재 주장 금지)을 O/X 로 판정한다.
   X 는 근거(슬롯 이름 + 코드 `facts`/`prose_input` 의 어느 값과 어긋나는지)를 적는다.
3. **직접 고치지 않는다.** 판정만 한다. 기준표에 없는 취향 지적을 하지 않는다.
4. **마감 — 코드 검사 exit 0 이고 reject 0 일 때만.** 하나라도 아니면 Notion 에 아무것도 쓰지 않고 끝낸다
   (handoff 가 OPEN 으로 남아 09:30 백업 Routine 이 발행한다). 통과면 이 순서로:
   1. v16 [산출물 — 델타 Notion 예치] 그대로 `delta.json`(·`deep_delta.json`)을 예치하고, 그 절의 **자기검증
      (DB 쿼리에 잡히는지 · 다시 읽어 글자 단위 대조)까지** 끝낸다. 검증 실패를 고치지 못하면 예치한 페이지를
      그대로 두지 말고 **삭제하지 않고** Status 를 `Skipped`, 제목을 `VOID GRM Web Delta {날짜}` 로 바꿔
      브릿지와 백업 Routine(같은 제목의 OPEN 페이지를 덮어쓰려 한다)이 못 집게 한 뒤 `실패` 로 끝낸다.
   2. 검증이 끝나면 **곧바로** handoff 를 v16 [0단계] PL-10 마감대로 `CONSUMED` 로 바꾼다
      (`desk.json` 의 `defer` row 가 있으면 `DEFERRED` 블록 포함). 이때부터 백업 Routine 은 돌지 않는다.
   3. v16 [Status 갱신] 대로 모든 row 의 Status 를 `desk.json` 의 `action` 에 맞춰 바꾼다
      (`render`·병합 멤버·`reflow`→Processed · `skip`→Skipped · `error`→Error · `defer`→미변경).

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
슬롯 수정, 카드 채택 변경, 검색, 마감 4번 밖의 Notion 쓰기, 다른 직무 파일 수정.
