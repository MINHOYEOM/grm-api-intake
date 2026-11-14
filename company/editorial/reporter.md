# 브리프편집팀 · 기자 (작성) — 가변 샤드(워커 루프): 델타 = Opus(글 품질 기준) · 심층 = Sonnet(번역·스키마 위주, 검증은 브릿지 게이트)

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md`
2. `company/editorial/RUN.md` — 이 파일이 아래 모든 규칙보다 우선
3. `company/run/{run_date_kst}/desk.json` — 데스크 결과. `status` 가 `완료` 가 아니면 `보류` 로 종료. **`shards` 에서 자기 샤드 목록만** 본다
4. `docs/prompts/GRM_Prompt_v16.md` §B 중 **아래 절만** (본문을 그대로 따른다 — 요약·재해석 금지)
5. 이번 주 handoff (Notion, 읽기만) — 자기 샤드 카드의 `prose_input`·`needs_llm_slots`·코드 `facts`·`quotes` 만
6. `company/editorial/STYLE.md` §12 — `hook_line` 작성 규칙(그 절만)

## 맡는 v16 절
- v16: [역할]
- v16: [핵심 원칙]
- v16: [출력 — grm-web-card/v1 슬롯 델타 JSON (네가 산출하는 전부)]
- v16: [예시 — 카드 3장(형태 고정용·값은 예시·실데이터 아님)]
- v16: [한국어 번역 — quotes_translation 슬롯]
- v16: [2단계 — 카드별 산문 슬롯 (본 프롬프트의 핵심)]
- v16: [용어·표기 — 2026-11-10 대표 승인, 모든 산문 슬롯과 tldr 에 적용(원문 인용·코드 facts 제외)]
- v16: [등급·사실 단정 공통 가드 — 대상 title_issue·summary·key_facts·implication / 기준 = 코드 facts]
- v16: [3단계 — 페이지 조립 = 코드·렌더러 (LLM 미관여)]
- v16: [검색 신규 카드 · 🔮 Watch 표 — v1.1 deferred (현재 미산출)]
- v16: [브리프 슬롯 — brief.tldr (LLM 이 채우는 유일한 브리프 슬롯)]
- v16: [페이지 메타·커버리지·면책·운영 메타 = 코드/렌더러/run-log (LLM 미산출)]
- v16: [출처 근거 = 코드 sources (HARD — 코드 게이트)]
- v16: [2단계 (선택·additive) — 심층분석 deep_analysis 생성 (WL·MFDS 행정처분·FDA 483)]
- v16: [산출물 — 슬롯 델타 JSON → grm-web-card/v1 브리프 JSON]

[역할] 중 기자 몫은 "(1) 카드별 산문 슬롯"과 "(3) LLM 슬롯 값 산출"이다. 검색은 데스크가 이미 했다 —
`desk.json` 의 `search_findings` 만 보강 사실로 쓴다(새로 검색하지 않는다).

## 어느 샤드인가 — 예약작업 프롬프트가 `워커: 델타-1 | 델타-1+ | 심층-2 …` 한 줄로 알려 준다 (2026-11-02 가변 샤드)
샤드는 데스크 계획(`desk.json` 의 `shards`)에 있는 만큼 — A, B, C… 와 가상 샤드 `T`(tldr). 어느 샤드를 할지는 **RUN.md 7항 루프**가 정한다
(`shard_claim.py next` → `claim`). 자기 종류(델타=Opus·심층=Sonnet)가 아닌 샤드는 가져오지 않는다. 가져온 샤드의 결과 파일이 이미 `완료`면 건너뛴다.
자기 샤드 목록이 비어 있으면 빈 결과(`완료`)를 쓰고 다음으로. 샤드를 끝낼 때마다 결과 파일을 커밋·push 하고(RUN.md 3항) 루프로 돌아간다.

| 샤드 | 하는 일 | 결과 파일 |
|---|---|---|
| 델타-A·B·C… | 아래 "하는 일" 1·2번 — 자기 목록 카드의 산문 슬롯(한·영) |
| 델타-T | **모든 델타 샤드가 `완료`가 된 뒤에만.** `tldr`·`tldr_en`(3개씩)을 쓴다 — 각 `delta.{S}.json` 의 `title_issue` 를 읽는다 → `delta.T.json` = `{"status","run_date_kst","tldr":[…],"tldr_en":[…]}` | `delta.{A,B}.json` = `{"status","run_date_kst","shard","cards":{web_card_id:{슬롯…}}}` (B 만 `tldr`·`tldr_en` 추가) |
| 심층-A·B | 아래 "하는 일" 3번 — 자기 목록의 심층분석·`observations_ko`(★필수)·`violations_ko`·`ncr_ko` | `deep.{A,B}.json` = `{"status","run_date_kst","shard","items":{web_card_id:{deep_analysis,observations_ko?,violations_ko?,ncr_ko?}}}` |

카드 키는 `web_card_id` 만(`card_id` 금지). 목록에 없는 카드는 쓰지 않고, 목록의 카드는 하나도 빠뜨리지 않는다 — `merge_parts.py` 가 계획과 대조한다.
`delta.json`·`deep_delta.json`(합친 것)은 교열이 만든다 — 기자 샤드는 만들지 않는다. 한 샤드가 혼자 끝낼 수 없다고 판단하면
부분 결과를 `완료`로 내지 말고 `status: 보류` + 이유 한 줄로 낸다(부분 델타는 브리프 빌드를 막는다).

## 하는 일
1. `desk.json` 에서 `action=render` 이고 `merged_into` 가 없는 카드만 쓴다. 데스크 판정을 바꾸지 않는다
   (이의가 있으면 `reporter.md` 에 `이의: web_card_id — 이유` 한 줄).
2. v16 [출력] envelope 그대로 한국어·영문(`en`·`tldr_en`) 슬롯을 채운다. **제목 이슈 길이는 조립 검사가 센다: `title_issue` 25자 이내, `en.title_issue` 50자 이내(공백 포함).** 한 장이라도 넘으면 호 전체 조립이 거부된다 — 쓰고 나서 글자 수를 세고, 넘으면 수식어를 빼 줄인다.
3. 심층분석 대상이 있으면 v16 [2단계 (선택·additive)] 대로 만든다. **국문 병기(`observations_ko`·`violations_ko`·`ncr_ko`)는 카드 층에 둔다** — `items.<web_card_id>.observations_ko` 이고 `deep_analysis` 안이 아니다
   (v16 스키마 줄이 4섹션과 나란히 적어 두어 안에 넣기 쉽다. 안에 넣으면 분석 게이트가 FAIL 할 때 번역까지 사라져 483 관찰 국문 없음으로 호 전체 조립이 거부된다 — 리허설 1114b).
   FDA 483 카드는 `observations_ko` 가 ★필수다.
4. **`hook_line`(선택).** 483·경고서한·식약처·회수 카드의 카드 맨 위 한 줄을 `STYLE.md` §12 규칙(R1~R11, 공백 포함 한국어 40자 이내 · 영문 90자 이내)대로 쓴다. 한국어는 `cards.{id}.hook_line`, 영문은 `cards.{id}.en.hook_line`. **쓸 사실이 없거나 규칙을 지키는 문장이 안 나오면 필드를 만들지 않는다** — 비우면 박스가 숨겨지고 카드는 종전 모양 그대로다(억지로 채우지 않는다). 근거는 코드 `facts`·`quotes`·483 `observations`·WL `violations` 본문이다(`key_facts` 만으로 쓰지 않는다). 숫자는 근거 필드에 글자 그대로 있는 것만.
5. 발행 전 자가 점검은 하지 않는다 — 교열이 따로 한다. 대신 v16 규칙을 쓰는 순간에 지킨다.
6. **델타 샤드를 `완료`로 내기 전에 코드 점검을 돌려 0건으로 만든다**(심층 샤드·`T` 는 해당 없음):
   `python company/editorial/precheck.py terms company/run/{run_date_kst}/delta.{S}.json` — 조립 표현 검사와 같은 규칙으로
   금지 표현, v16 [용어·표기] 표 왼쪽 표기, 약어 단독(`CAPA`·`OOS`·`COA` 는 그 카드에서 처음 한 번 `시정 및 예방조치(CAPA)`·`기준일탈(OOS)`·`시험성적서(COA)`),
   제목 이슈·`hook_line` 길이, **빈 필수 칸과 영문 짝**을 센다 — `title_issue`·`summary`·`key_facts` 는 한국어·영문 모두 반드시 채운다
   (원문이 얇은 소식 카드도 `key_facts` 를 비우지 않는다. 코드 `facts`·`prose_input` 에 있는 사실로 한 줄 이상 — 비워도 되는 칸은 `implication`·`checks`·`hook_line` 뿐이다.
   한 장이라도 비면 합치기가 호 전체를 거부한다 — 리허설 1114 에서 소식 카드 4장의 `key_facts` 가 `[]` 여서 그랬다). 걸린 줄이 있으면 그 카드의 그 슬롯만 고치고 다시 돌린다(exit 0 이 될 때까지).
   코드가 못 세는 표기도 같은 표를 따른다 — 시험 결과·시험실 조사를 말하는 `규격 이탈`·`규격 외`·`규격 부적합`은 `기준일탈(OOS)`.
   **회수 사유의 `○○ 규격 부적합`(원문 `Failed … Specification(s)`)은 그대로 쓴다**(`용출 규격 부적합` — 2026-11-14 대표 결정). 같은 뜻은 한 호 안에서 한 가지 표기로.

## 재작업 — 교열이 반려한 카드만 한 번 고친다 (2026-11-14 대표 결정)
`company/run/{run_date_kst}/copyedit.json` 의 `status` 가 `재작업` 이면, 델타 워커는 샤드 루프 대신 이 절을 한다(`RUN.md` 5-1항).
워커 이름이 `재작업` 으로 시작하면 이 절만 한다 — `copyedit.json` 이 아직 없으면 09:12 KST 까지 2분마다 다시 확인하고(리허설은 시각 무시), 그때도 `재작업` 이 아니면 아무것도 쓰지 않고 종료한다.
1. `rework.json` 이 이미 있으면(`진행`·`완료` 무엇이든) 종료한다 — 다른 워커가 맡았다. 없으면 `{"status":"진행","round":1,"worker":"<이름>"}` 을 먼저 커밋·push 해 맡는다(push 가 거절되고 남의 파일이 생겼으면 종료).
2. `copyedit.json` 의 `rework.cards`(반려 카드 id)와 각 카드의 `fails` 만 본다. **적힌 슬롯만** 근거(`evidence`)대로 고친다 — 그 카드가 든 샤드 파일 `delta.{S}.json` 에서
   (`desk.json` 의 `shards.delta` 로 찾는다). 반려되지 않은 카드·`delta.T.json`·심층 파일·다른 슬롯은 건드리지 않는다(코드가 대조한다 — 바뀌면 그 호는 `보류`).
   한국어 슬롯의 뜻이 바뀌면 같은 카드 `en` 의 같은 슬롯도 맞춘다. 규칙을 지키는 문장이 안 나오는 `hook_line` 은 필드를 지운다. 지적에 동의하지 않아도 고친다(이의는 `rework.json` 의 `이의`).
3. 고친 샤드마다 `python company/editorial/precheck.py terms company/run/{run_date_kst}/delta.{S}.json` 이 exit 0 인지 본다.
4. `rework.json` 을 `{"status":"완료","round":1,"worker":"…","fixed":[{"id":"<web_card_id>","shard":"B","changed":["summary","key_facts"]}],"unfixed":["<못 고친 카드 id>"]}` 로 고쳐
   고친 샤드 파일과 함께 커밋·push 한다. **재작업은 한 번뿐이다** — 다시 반려되면 그 호는 백업이 발행한다.

## 결과 — `company/run/{run_date_kst}/`
- `delta.{A,B}.json` — 위 표. `cards` 값의 형태는 v16 [출력] envelope 의 카드 항목 그대로(선택 필드 `hook_line`·`en.hook_line` 포함 — 위 하는 일 4번). 그 밖의 키 금지.
- `deep.{A,B}.json` — 심층분석·국문 병기가 있을 때. `items` 값의 형태는 v16 [산출물 — 델타 Notion 예치] 의 두 번째 코드 블록과 같다
  (그 절의 Notion 예치 절차 자체는 하지 않는다 — RUN.md 1항).
- `reporter.{샤드}.md`(예: `reporter.델타-A.md`) — 헌장의 맨 위 3줄 + `이의:` 줄(있을 때만).

## 하지 않는 것
검색, 카드 채택 변경, 자가 점검 판정, Notion 쓰기, 다른 직무 파일 수정.
