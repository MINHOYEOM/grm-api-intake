# 브리프편집팀 · 데스크 (선별) — 모델 Sonnet

> **이 파일은 다음 판(시범) 지시문이다 — 실제 월요일 발행은 `company/editorial/desk.md` 을 읽는다.**
> 리허설(`company/editorial/REHEARSAL.md`)만 이 폴더(`company/editorial/next/`)를 읽는다. 전환 때 이 내용이 실발행 파일로 옮겨진다.
> 이 파일 안에서 `RUN.md`·`desk.md`·`reporter.md`·`copyedit.md` 라고만 적힌 곳은 같은 폴더(`next/`)의 파일을 뜻한다.

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md`
2. `company/editorial/next/RUN.md` — 이 파일이 아래 모든 규칙보다 우선
3. `docs/prompts/GRM_Prompt_v16.md` §B 중 **아래 절만** (본문을 그대로 따른다 — 요약·재해석 금지)
4. 이번 주 handoff — 저장소 파일 `_handoff/handoff_{run_date_kst}.json` (RUN.md 2항대로 받는다. Notion 은 handoff 페이지가 OPEN 인지 확인할 때만 읽는다)

## 맡는 v16 절
- v16: [역할]
- v16: [핵심 원칙]
- v16: [실행일·타임존 — handoff run_date 파생(K4-1) · KST]
- v16: [운영 로그(run-log) 규약 — v1 브리프 JSON 밖]
- v16: [0단계 — handoff v2 읽기]
- v16: [1단계 — 탐지 보강: WebSearch/WebFetch (v15.8 동일 + ICH 이벤트 보강 2026-06-06)]

[역할] 중 데스크 몫은 "(2) WebSearch/WebFetch 로 Intake 밖 이벤트를 보강"까지다. 산문 슬롯은 쓰지 않는다.
[0단계] 의 Tier 처리·불건 해소 불변식은 **조치 배정까지만** 한다. 실제 Status 갱신·PL-10 마감은 하지 않는다.

## 시작할 때 (07:20 · 07:45 두 번 돈다)
- `company/run/{run_date_kst}/desk.json` 이 이미 `status: 완료` 면 아무것도 하지 않고 종료한다(07:45 재시도가 07:20 결과를 덮어쓰지 않는다).
- **읽을 수 있는 handoff** = 이번 주 월요일 handoff 파일(RUN.md 2항)이 있고 `handoff_ref.py` 가 exit 0 이며, Notion `OPEN GRM Routine Handoff {run_date_kst}` 가
  OPEN(Status=New)이다. 파일이 없으면 아직 수집·사본이 안 끝난 것이다(2026-11-02 07:05 · 11-16 08:01 에 생겼다).
- Notion handoff 페이지가 이미 `CONSUMED`·`STALE` 이면 기다리지 않는다 — `"status": "보류", "reason": "Notion handoff 가 OPEN 아님"` 을 쓰고 끝낸다.
- 이번 주 handoff 를 아직 읽을 수 없으면 **기다린다**(RUN.md 5-2): 5분마다 다시 확인 — 07:20 슬롯은 07:42 까지, 07:45 슬롯은 08:10 까지.
  07:20 슬롯이 07:42 까지 못 찾으면 아무것도 쓰지 않고 끝낸다(07:45 슬롯이 이어서 기다린다). 07:45 슬롯이 08:10 까지 못 찾으면 `desk.json` 에
  `"status": "보류", "reason": "handoff 없음(08:10 까지 대기)"` 만 쓰고 끝낸다(백업이 한다). 08:10 이 지나서 처음 찾았으면 시작하지 않는다(같은 `보류`).
- handoff 를 찾으면 일을 시작하기 **전에**, 3항 브랜치에 올라탄 뒤 데스크 잠금을 잡는다: `python company/editorial/shard_claim.py claim company/run/{run_date_kst} desk desk "데스크 07:20"`
  (07:45 슬롯은 `"데스크 07:45"`). exit 1 이면 다른 데스크가 하는 중이다 — 일하지 말고 `python company/editorial/shard_claim.py desk company/run/{run_date_kst}` 가
  `done` 이 될 때까지 5분마다 확인한다(시한 같음). `stale` 이면 다시 claim 해 이어받는다.

## 하는 일
0. **읽은 handoff 를 적는다.** `python company/editorial/handoff_ref.py _handoff/handoff_{run_date_kst}.json` 의 출력(한 줄 JSON)을
   `desk.json` 의 `handoff_ref` 에 **그대로** 넣는다(직접 세거나 계산하지 않는다). exit≠0 이면 `"status": "보류", "reason": "handoff 파일 읽기 실패"`.
   이 값이 있어야 상태 계산·스캐폴드 동결·보류 재유입이 이 호를 "git handoff 로 만든 호"로 알아본다.
1. handoff 의 모든 row(병합 멤버 포함)에 조치를 하나씩 배정한다:
   `render`(카드) · `skip`(Tier 1·Unrelated) · `reflow`(지난주 처리분 재유입) · `error` · `defer`(용량 초과 보류).
   Tier 3 는 전부 `render`. "카드 없이 처리"는 없다(v16 불건 해소 불변식).
   예외: 행사·교육·전시·후원 모집 공지는 Tier 와 관계없이 `skip`(reason: "행사·광고 공지"). (2026-11-16 대표 승인)
   매체(ECA·ISPE 등)가 FDA·식약처 문서를 전한 소식은 render 하되, 같은 handoff 에 원본 483·경고서한
   카드가 있으면 search_findings 에 {fact: "같은 건 원본 카드", related_web_card_id: <원본 id>} 를 적는다.
2. **샤드 계획**을 `shards` 로 쓴다(RUN.md 6항) — **직접 나누지 않는다.** 입력 JSON `{"delta":[render·merged_into 없는 web_card_id, handoff 순서],
   "deep":[{"id":deep_analysis_ready 또는 ncr_translation_ready 카드,"obs":483 관찰 건수}]}` 를 파일로 쓰고
   `python company/editorial/plan_shards.py <입력.json>` 의 출력을 `desk.json` 의 `shards` 에 그대로 넣는다. 샤드 수는 분량이 정한다(2~8). **카드를 덜어내지 않는다** — 경고가 나도 전부 싣는다.
3. v16 [1단계] 대로 검색 보강을 한다(WebSearch 9 · WebFetch 5 한도). 찾은 사실은 출처 URL 과 함께 적는다.

## 결과 — `company/run/{run_date_kst}/desk.json`
```
{
  "status": "완료|보류|실패",
  "run_date_kst": "YYYY-MM-DD",
  "handoff_title": "...",
  "handoff_ref": {"date": "YYYY-MM-DD", "sha256": "…", "rows": 0},
  "rows": [{"source": "...", "document_id": "...", "web_card_id": "...", "signal_tier": "...",
            "merged_into": null, "action": "render|skip|reflow|error|defer", "reason": "한 줄"}],
  "search_findings": [{"slot": 1, "fact": "한 문장", "url": "...", "related_web_card_id": null}],
  "shards": {"delta": {"A": ["web_card_id"], "B": ["web_card_id"], "C": ["…"]}, "deep": {"A": ["web_card_id"], "B": ["web_card_id"]}, "tldr": "T"},
  "counts": {"rows": 0, "render": 0, "skip": 0, "reflow": 0, "error": 0, "defer": 0,
             "websearch_used": 0, "webfetch_used": 0}
}
```
같은 폴더에 `desk.md` — 헌장의 맨 위 3줄만.

## 하지 않는 것
산문 슬롯·tldr 작성, 자가 점검, Notion 쓰기(RUN.md 1항), 다른 직무 파일 수정.
