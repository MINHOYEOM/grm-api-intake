# 브리프편집팀 · 교열 (판정) — 모델 Sonnet · 기자와 다른 세션

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md`
2. `company/editorial/RUN.md` — 이 파일이 아래 모든 규칙보다 우선
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
- 심층 지시문 3개의 '3-1. 문체' 절 — deep_delta.json 의 산문(observation·description·risk·
  inspectional_significance·fda_evaluation·disposition_basis·administrative_risks·items)을
  카드 슬롯과 같은 기준으로 O/X 판정한다. fails 의 slot 은 "deep.<키>"로 적는다.
  심층 [용어·표기] 중 코드가 세는 것(표 왼쪽 표기·OOS·목록 밖 약어)은 기자가 `precheck.py deep` 0건으로 넘긴다(같은 함수 `inject_slots.term_hits`, 리허설 1120b).
  원문의 조사·문서·시료 번호(`OOS-24006`·`OOS/22/002`·`QU-SOP-0056623`)는 약어가 아니다 — 원문 글자 그대로이므로 반려하지 않는다.

## 맡는 v16 절 (= 마감, 전부 통과일 때만)
- v16: [K4 경계 — 임시 책임 고지]
- v16: [산출물 — 델타 Notion 예치 (클라우드 델타 브릿지, 2026-07-07 additive)]
- v16: [Status 갱신 — 발행 후, 0단계 page_id 표 기준]

## 하는 일
0. **샤드 합치기.** 합치기 **전에** 읽을 것 5번의 handoff 를 받아 둔다(`_handoff/handoff_<desk.json 의 handoff_ref.date>.json`) — 없으면 합치기가 카드 유형을 몰라 소식·지침·고시 카드의 빈 `key_facts` 를 영문 미완결로 잘못 센다(리허설 1118: 첫 합치기 exit 1, handoff 를 받은 뒤 exit 0).
   그다음 `python company/editorial/merge_parts.py company/run/{run_date_kst}` — `delta.A/B.json`·`deep.A/B.json` 을
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
   이어서 **영문 게이트** `python check_en_slots.py company/run/{run_date_kst}/brief_check.json` 를 돌린다 — 발행 워크플로(`grm-web-publish.yml` 영문 슬롯 게이트)와 같은 판정이다.
   exit≠0 이면 코드 검사 실패와 같다(여기서 안 보면 `CONSUMED` 뒤에 발행이 멈춘다): 메시지의 빠진 카드가 1~5장이면(예시에 전부 나온다) 그 카드들을 3-1 대로 `재작업`
   (`fails` `{"item": "영문 게이트", "slot": "en", "evidence": "<메시지>"}`), 그보다 많거나 `brief.en.tldr 없음` 이 있으면 `보류`.
   이어서 **렌더 게이트** `python check_render_gate.py company/run/{run_date_kst}/brief_check.json` 를 돌린다 — 사이트 빌드(`web/render.py` 발행 게이트: 483 관찰 국문·WL 위반항목 국문·WHOPIR 국문·영문 사실 칸)와 같은 판정이다.
   exit≠0 이면 코드 검사 실패와 같다(이대로 마감하면 `CONSUMED` 뒤에 사이트 빌드가 멈춰 백업도 돌지 않는다 — 리허설 1122a 모의 발행, 2026-11-22). 위반 종류로 나눈다:
   - `483 관찰 국문`·`WL 위반항목 국문`·`WHOPIR 국문` 이 하나라도 있으면 **`보류`**(사유에 첫 줄 그대로). 국문 병기는 심층 대상 카드에만 붙어 기자가 고칠 수 없다. `보류` 면 Notion 이 `OPEN` 으로 남아 09:30 백업이 발행한다.
   - `영문 사실 칸`(`EN_INVENTED_NUMBER` — 영문이 한국어판·표에 없는 수치를 말함, 예: 한국어 '전수' 를 영문 '100%' 로)만 있고 `카드:` 줄의 카드가 1~25장이면 그 카드들을 3-1 대로 `재작업`
     (`fails` `{"item": "렌더 게이트", "slot": "en", "evidence": "<위반 줄>"}` — 기자는 영문에서 그 수치를 빼거나 한국어판에 있는 표현으로 바꾼다). 25장을 넘으면 `보류`.
   **`--deep` 에는 원문을 붙인 검사용 사본을 넘긴다**(`deep_delta.json` 이 있을 때):
   `python company/editorial/precheck.py deep-source _handoff/handoff_$D.json company/run/{run_date_kst}/deep_delta.json --out _scaffold/deep_check.json`
   → `--deep _scaffold/deep_check.json`. 심층분석의 근거 대조는 그 주 handoff 의 원문(`deep_analysis_input.body_full`)이 기준선인데,
   기자는 원문을 델타에 옮겨 적지 않는다(v16). 발행 브릿지는 조립 직전에 같은 원문을 붙인다 — 붙이지 않고 검사하면 조항 인용이 전부 '원문에 없음'으로 나와
   교열의 검사 결과가 발행 결과와 달라진다(리허설 1113c: 26장 중 11장). 사본은 커밋하지 않는다 — 반납하는 `deep_delta.json` 은 기자가 낸 그대로다.
   사본을 못 받아 검사를 못 하면 그것도 **통과가 아니다**(결과에 `막힘: scaffold 사본 — <사유>` 한 줄).
   **원문 대조 4가지 — 교열 판정이 회차마다 갈린 유형을 코드가 먼저 센다(2026-11-23).** `python company/editorial/precheck.py source company/run/{run_date_kst} _handoff/handoff_$D.json --scaffold _scaffold/brief_web_$U.json` — 기자 사전 점검(`terms`·`deep`)과 **같은 함수**(`guess_check.delta_source_lines`·`deep_source_lines`)다. 차단 줄(`개인 실명`·`가림 값 추측`·`원문에 없는 날짜`·`날짜 불일치`)이 있으면 그 카드는 `reject`, `fails` 에 `{"item": "코드 검사", "slot": "<슬롯>", "evidence": "<그 줄>"}`. 경고 줄(`진술 단정 의심`·`날짜 연도 확인`·`가림 값 추측 의심`·`개인 실명 의심`·`원문에 없는 날짜 의심`·`날짜 불일치 의심`)은 그 칸을 2-2 표(②·③·①·⑦)로 **반드시** 판정한다 — `reject` 면 `fails` 에, `pass` 면 `notes` 에 `{"item": "코드 경고 판정", "slot": "<슬롯>", "evidence": "<원문 조각 — 왜 괜찮은지>"}` 를 남긴다(같은 재료에서 판정이 갈리지 않게 근거를 적는다). 리허설 1122b 2차가 반려한 개인 실명 2·가림 값 추측 1·진술 단정 1 이 같은 재료로 다음 판정에선 통과했고, WHO 서류 평가 카드의 '실사 기간: 미국 FDA(2026-03-02~07)'(코드 실사일 2026-09-22)는 5회 내리 통과했다.
1-1. **전수 대조 — 카드를 한 장도 빼지 않고 원문과 맞춰 읽는다(리허설 1121b — 2026-11-21).** 1121b 교열은 153장을 4.5분에 `reject 0` 으로 냈지만
   스스로 "카드별 전수 정독은 아님(표본)" 이라 적었다(원문 대조 17장). 그래서 카드마다 맞춰 본 근거를 `checked` 로 적고, 코드가 그 근거를 원문과 대조한다.
   카드와 원문을 나란히 보는 묶음: `python company/editorial/copyedit_evidence.py show company/run/{run_date_kst} _handoff/handoff_$D.json`(묶음 목록 · `$D` = `desk.json` 의 `handoff_ref.date`) →
   `… show company/run/{run_date_kst} _handoff/handoff_$D.json --batch K`(K번째 20장 · 카드 국·영 슬롯·심층 칸 + handoff 원문·스캐폴드 `facts`·483 관찰).
   심층 원문 전문이 필요하면 `--full`. 카드마다 `checked` 에 `{"slot": "<칸>", "claim": "<카드 문장에서 글자 그대로>", "source": "<그 카드 원문에서 글자 그대로 — 공백 빼고 8자 이상>"}` 를 적는다:
   서로 다른 원문 조각 2개 이상 · 앞면 칸(`title_issue`·`summary`·`key_facts`·`implication`·`checks`) 1개 이상 · 한국어 `hook_line` 이 있으면 `hook_line` 1개 · 심층이 있는 카드는 `deep.<키>` 1개.
   원문에서 못 찾은 사실은 `{"slot", "claim", "absent": true}` — 필수 칸이면 그 카드는 `reject`(2-2 ②), 선택 칸이면 2-3 칸 삭제 주의를 같은 칸으로 적는다(없으면 검사가 걸린다).
   원문이 정말 없는 카드만 `checked` 대신 `"source_absent": "<사유>"` — 코드가 원문이 있다고 보면 실패다.
   묶음을 서브 에이전트로 나눠도 된다(예: 40장씩 4개 — 아래 2-2 표를 조각마다 붙인다). 조각마다 `_scaffold/copyedit.part<K>.json`(`{"cards": [...]}`)에 쓰고
   `python company/editorial/copyedit_evidence.py merge company/run/{run_date_kst}/copyedit.json _scaffold/copyedit.part*.json` 로 합친다(델타 순서 · `counts` 다시 셈).
   **`완료` 를 쓰기 전에** `python company/editorial/copyedit_evidence.py check company/run/{run_date_kst} _handoff/handoff_$D.json` 이 exit 0 이어야 한다.
   exit 1 이면 줄마다 나온 카드를 마저 읽고 근거를 채워 다시 돌린다 — 표본만 읽고 `완료` 를 쓰지 않는다. 09:20 까지 0 으로 못 만들면 — 1차는 `보류`, 2차는 3-3 `finish` 가 근거 빈 카드 목록을 내고 그 카드만 다시 대조한다(카드를 미루지 않는다).
   이 검사는 근거를 "적었는가" 를 본다 — 판정의 기준은 그대로 2-2 표다(근거가 있어도 정확성 오류가 있으면 `reject`).
2. **의미 점검.** 카드마다 v16 [발행 전 산문 자가 점검] 1~10번과 [핵심 원칙] 2-1(원문 부재 주장 금지)을 O/X 로 판정한다. 심층 칸이 있는 카드는 위 '3-1. 문체'도 판정한다.
   X 는 근거(슬롯 이름 + 코드 `facts`/`prose_input` 의 어느 값과 어긋나는지)를 적는다.
2-1. **`hook_line` 점검(있는 카드만).** 한국어 `hook_line` 과 `en.hook_line` 각각 `STYLE.md` §12 로 O/X: ① 금칙어 없음(신호, 볼 만, 필요가, 가능성, 시사, 우려, 느낌표·물음표·이모지) ② 공백 포함 **한국어 40자 이내 · 영문 90자 이내**(영문에 40자를 적용하지 않는다) ③ **숫자 원문 대조** — 문장의 모든 숫자가 같은 카드의 코드 `facts`·483 `observations`·WL `violations` 본문에 글자 그대로 있어야 한다(세어서 만든 숫자·반올림 X) ④ 근거 없는 사실·원문보다 강한 단정 없음(진술은 "진술" 표기) ⑤ 사전 용어는 사전 표기, 영문 약어 없음 ⑥ 업체명·제목 반복 없음. 근거를 못 찾으면(①③④) 아래 2-3 칸 삭제(`slot: "hook_line"`)로 적는다 — 기자에게 되돌리지 않는다. `hook_line` 이 없는 카드는 점검 대상이 아니다(없는 것이 정상).
2-2. **반려와 주의 — 반려는 정확성 문제만(2026-11-21 대표 결정).** 2·2-1번의 X 를 아래 표로 가른다. 반려(`reject`)는 표 왼쪽 줄에 해당할 때만이다.
   오른쪽 줄(문체)은 카드를 `verdict: "pass"` 로 두고 `notes` 에 적기만 한다 — 재작업(`rework.cards`)에 넣지 않고, reject 수에 세지 않는다.
   한 카드에 둘 다 있으면 `reject` 이고, 문체 지적은 그 카드의 `notes` 에 따로 적는다(`fails` 에는 반려 근거만).
   단 왼쪽 줄의 오류가 **선택 칸에만** 있으면 반려하지 않고 아래 2-3 칸 삭제로 처리한다.

   | 판정 | 해당하는 것 |
   |---|---|
   | **반려 `reject`** (`fails`) — 정확성 | ① 사실 오류: 원문·코드 `facts` 와 다른 숫자·날짜·이름·조항(세어서 만든 숫자 포함) ② 원문에 없는 말: 원문에 없는 사실·결합·분류를 보탬, 가려진(`[redacted]`·비공개)·OCR 깨진 값을 추정해 채움, 원문 부재 단정([핵심 원칙] 2-1) ③ 원문보다 강한 단정: 가능성·추정을 단정으로, 업체 직원의 말을 사실로(STYLE R3 — "진술" 표기 없음) ④ 금칙어(`hook_line` ① 포함 — 화면이 그 줄을 숨긴다) ⑤ 마크업·깨진 형식 ⑥ 국·영 짝 불일치(한쪽에만 있는 사실·숫자·뜻) ⑦ 개인 실명 노출 등 공개 위험(법적 위험): 공직자·규제기관 공식 발표 속 직함상 공식 인물 외의 개인 이름 — 업체명이 사람 이름(개인 농장·개인 사업자 — `Kenneth F Tesch`·`Matthew A. Leo` 꼴)이면 **업체명으로서도 반려**, '개인 사업자'(영문 'an individual business')로 쓴다(코드 `개인 실명` 줄과 같은 기준 — 11-23 실발행에서 '개인 사업체 업체명' 이라며 통과시킨 2장을 발행 PR 에서 손으로 고쳤다). 법인명은 그대로 ⑧ 코드 게이트 실패(1번 — `hook_line` 길이 ② 포함) |
   | **주의 `pass`** (`notes`) — 문체 | 같은 대상 다른 이름(앞면↔심층 포함) · 한 문장에 사실 여러 개 · 용어표 표기 차이·목록 밖 약어(코드가 세는 것은 기자의 `precheck.py` 사전 점검이 막는다) · 군말 · 문장 길이·리듬 · `hook_line` 문체 규칙(⑤ 사전 표기·영문 약어, ⑥ 업체명·제목 반복 — 단 금칙어는 반려) · 근거 번호 `(관찰 N)` 표기 형식 · 날짜·조항 표기 꼴 |

   판정을 여러 조각(서브 에이전트)으로 나누면 **조각마다 지시 문장에 이 표를 글자 그대로 붙인다**(리허설 1121a — 조각 4개가 따로 엄격도를 정해 59장 반려, 전부 문체·용어였다).
   조각 결과를 합칠 때 표 오른쪽 줄에 해당하는 반려는 `notes` 로 옮기고 `pass` 로 고친다.
   `notes` 는 이 호의 발행을 막지 않고, 다음 주 지시문·용어표를 고치는 재료로 남는다.
2-3. **칸 삭제 — 정확성 오류가 선택 칸에만 있으면 칸을 비우고 통과(2026-11-21 대표 결정).** 2-2 왼쪽(반려) 오류가 아래 칸에만 있으면
   그 카드는 `verdict: "pass"` 로 두고 칸마다 `notes` 에 `{"item": "칸 삭제", "slot": "<칸>", "evidence": "<사유 — 원문과 어디가 어긋나는지>"}` 를 적는다.
   칸은 교열이 손으로 지우지 않는다 — `blank_slots.py` 가 국·영 짝으로 비운다(비우기만 하므로 새 문장이 생기지 않는다).
   비울 수 있는 칸: `hook_line` · `implication` · `checks` · `checks[i]`(남는 점검 2~3개) · `key_facts[i]`(하나 이상 남을 때) ·
   FDA 483 심층의 `deep.key_violations[i]`(하나 이상 남을 때)·`deep.inspectional_significance`·`deep.administrative_risks`. `i` 는 한국어 목록에서 0부터 센 위치다(영문은 같은 위치가 빠진다).
   `title_issue`·`summary`·인용 번역·경고서한·행정처분 심층 절은 필수 칸이다 — 거기 오류가 있으면 반려(`fails`). 칸 안의 문장 일부만 지우는 것은 칸 삭제가 아니다.
   **반영은 마감하는 판정에서만.** 칸 삭제 말고 reject 가 0 이면(1차든 2차든) 4번 마감 **전에** 반영한다:
   ```
   mkdir -p _scaffold && cp -n company/run/{run_date_kst}/delta.json _scaffold/delta.pre_blank.json && cp -n company/run/{run_date_kst}/deep_delta.json _scaffold/deep.pre_blank.json
   python company/editorial/blank_slots.py apply company/run/{run_date_kst}/copyedit.json _scaffold/delta.pre_blank.json company/run/{run_date_kst}/delta.json \
     --deep-in _scaffold/deep.pre_blank.json --deep-out company/run/{run_date_kst}/deep_delta.json \
     --log company/run/{run_date_kst}/slot_removals.json
   ```
   (`cp -n` 은 이미 있는 칸 삭제 전 사본을 덮지 않는다 — 다시 돌려도 같은 사본에서 같은 결과가 나온다.) exit≠0 이면 메시지의 카드는 비울 수 없는 칸이었다 — `reject` 로 바꾼다.
   (`--log company/run/{run_date_kst}/slot_removals.json` 을 붙인다 — 뺀 칸·처리·사유가 남고, 마감 1번에 같이 커밋한다. 아래 2-4 심층 삭제도 같은 명령이 반영한다.)
   그다음 **1번 코드 검사를 다시 돌린다**(`deep-source` 검사용 사본도 새 `deep_delta.json` 으로 다시 만든다). exit 0 이고, 이어서
   `python company/editorial/blank_slots.py verify company/run/{run_date_kst}/copyedit.json company/run/{run_date_kst}/brief_check.json` 도 exit 0 이어야 통과다
   (칸 삭제로 심층을 비운 카드가 심층 게이트에 걸려 심층을 통째로 잃지 않았는지 본다 — 심층 게이트 실패는 조립을 막지 않는다. 2-4 심층 삭제 카드는 심층이 빠져도 통과 — `심층 없이 발행` 줄로 알린다).
   하나라도 실패하면 `cp _scaffold/delta.pre_blank.json company/run/{run_date_kst}/delta.json && cp _scaffold/deep.pre_blank.json company/run/{run_date_kst}/deep_delta.json` 로 되돌리고
   그 카드를 `reject` 로 바꿔 3-1 대로 처리한다. `code_gate` 에는 다시 돌린 결과를 적는다. 마감 1번이 push 하는 `delta.json`·`deep_delta.json` 은 칸을 비운 이 파일이다.
   **재작업(3-1)으로 되돌릴 때는 반영하지 않는다** — 칸 삭제는 `notes` 에만 두고 `delta.json` 은 판정한 그대로 커밋한다(2차의 `merge_parts.py` 가 샤드에서 델타를 다시 만들고
   `rework-diff` 가 1차 델타와 대조하므로, 1차에 비우면 반려 안 된 카드가 바뀐 것으로 걸린다). 2차는 `rework-diff` 와 코드 검사 뒤, 마감 전에 위 순서로 반영한다(1차에서 옮긴 `pass` 카드의 칸 삭제 포함).
2-4. **심층 삭제 — 반려가 심층 칸에만 있으면 코드가 심층을 빼고 통과(2026-11-21 대표 결정 · 리허설 1121b: 반려 21장 중 12장이 심층에서만 걸렸다).**
   판정은 2-2 표 그대로 한다 — 심층(`deep.…`)에 정확성 오류가 있으면 종전처럼 `reject` 와 `fails`(`slot: "deep.<키>"`)로 적는다. 손으로 통과로 바꾸지 않는다.
   판정을 다 적고(조각을 합친 뒤) **반려 수를 세기 전에** 코드가 가른다:
   ```
   python company/editorial/blank_slots.py deep-auto company/run/{run_date_kst}/copyedit.json
   ```
   `fails` 가 **전부** `deep.` 칸인 카드는 `pass` 가 되고 `fails` 는 `notes` `{"item": "심층 삭제", …}` 로 옮겨진다(`counts` 다시 셈 · `rework.cards` 에서 빠짐).
   통과 카드의 `checked` 에 `absent: true` 인 심층 칸도 심층 삭제 주의가 붙는다.
   **지울 수 있는 칸만 걸린 반려도 코드가 통과시킨다(11-23 실전 — fda483-196441 `hook_line` 하나로 호 전체가 `보류`).** `fails` 가 전부 2-3 의 비울 수 있는 칸(`hook_line`·`implication`·`checks`·`key_facts[i]`·483 심층 선택 절)이나 심층 칸이고 `apply` 가 전부 받아 주면(2-5 `check` 와 같은 함수로 판정 — 규칙을 따로 두지 않는다) `pass` 가 되고 `fails` 는 `칸 삭제`·`심층 삭제` 주의로 옮겨진다. 필수 칸(`title_issue`·`summary`·인용·`en`)·하나뿐인 `key_facts[0]`·코드 게이트 반려(`코드 검사`·`영문 게이트`·`렌더 게이트`·`칸 삭제 불가`)는 그대로 `reject` 다. 델타는 같은 실행 폴더의 `delta.json`·`deep_delta.json`(칸 삭제 전)을 읽는다.
   2차에는 1차 기록(`copyedit.r1.json` — 실행 폴더, 없으면 `_scaffold/`)의 칸 삭제·심층 삭제 주의를 **먼저 잇는다** — 2차가 다시 적지 않아도 1차 결정은 남는다. 반려 안 된 카드는 그대로 잇고, 재작업 카드는 그 칸 글자가 1차 델타(`_scaffold/delta.r1.json`·`deep_delta.r1.json`)와 같을 때만 잇는다(기자가 고쳤으면 2차가 다시 판정). 같은 칸을 가리키는 주의·반려가 이미 있으면 잇지 않는다.
   `deep-auto` 가 통과시킨 카드도 `pass` 카드다 — `checked` 근거(1-1)가 있어야 한다(2차에 다시 판정한 카드는 반려로 적었더라도 근거를 적는다).
   마지막 줄의 `남은 반려 N장` 이 3-1 의 reject 수다. 같은 파일에 다시 돌려도 결과가 같다.
   반영은 2-3 과 같은 `blank_slots.py apply`(같은 때 · 같은 사본 규칙 · `--log`)가 한다 — 뺄 수 있으면 그 항목만, 아니면 그 카드의 심층분석을 통째로 뺀다:
   `deep.key_violations[i]` → 그 지적만(하나도 안 남으면 통째) · `deep.required_remediation.items[i]` → 그 항목만(경고서한·행정처분은 하나는 남아야) ·
   483 선택 절 → 비움 · 그 밖의 심층분석 절(`fda_evaluation`·`disposition_basis` 등 필수 절) → 심층분석 통째(카드는 앞면·원문 블록으로 발행) ·
   원문 블록의 국문 해석(`deep.observations_ko[i].…`·`deep.violations_ko[i].…`·`deep.ncr_ko.…`) → 빼면 원문·국문 병기 게이트가 호 전체를 막으므로 고정 안내문
   (`국문 해석을 싣지 않았다 — 원문을 본다.`)으로 바꾼다. 뺀 내용은 `slot_removals.json` 에 남는다. 이 칸들을 가리키던 `checked` 근거는 반영 뒤 카드에 없어도 1-1 검사가 걸지 않는다.
   `apply` 가 exit 1 이면(심층 델타에 없는 카드·없는 번역 칸 — 칸 이름을 잘못 적었다) 그 카드를 `reject` 로 되돌린다.
2-5. **칸 삭제 사전 점검 — 1차·2차 모두, 재작업·완료를 정하기 전에(리허설 1121c).** 1차가 재작업으로 가면 `apply` 를 돌리지 않아
   비울 수 없는 칸 삭제(예: `key_facts` 가 하나뿐인 카드의 `key_facts[0]`)가 2차 마감에서야 드러나고, 재작업은 1회뿐이라 `보류` 가 된다.
   그래서 2-4 `deep-auto` 바로 뒤, **반려 수를 세기 전에** 같은 함수로 미리 돌려 본다(파일은 바꾸지 않는다 · 델타는 칸 삭제 전 것):
   ```
   python company/editorial/blank_slots.py check company/run/{run_date_kst}/copyedit.json company/run/{run_date_kst}/delta.json company/run/{run_date_kst}/deep_delta.json
   ```
   (`deep_delta.json` 이 없으면 셋째 인자를 뺀다.) exit 1 이면 `거부:` 줄의 카드마다 — 칸 이름을 2-3 목록의 꼴로 잘못 적었고 고친 이름으로 같은 오류를 비울 수 있으면 이름을 고쳐 다시 돌린다.
   그래도 거부되는 카드는 `verdict: "reject"` 로 바꾸고 그 칸 삭제 주의를 `notes` 에서 `fails` 로 옮긴다
   (`{"item": "칸 삭제 불가", "slot": "<칸>", "evidence": "비울 수 없는 칸 — 기자가 고친다: <거부 줄>"}`).
   그다음 `deep-auto` 를 한 번 더 돌린다(같은 파일에 다시 돌려도 결과가 같다 — 심층 칸만 거부된 카드는 심층 삭제로 통과하고, `counts`·`rework.cards` 를 다시 센다).
   이 카드들은 3-1 의 reject 수에 들어가 1차 재작업 목록(`rework.cards`)에 실린다. 1-1 `copyedit_evidence.py check` 도 같은 판정을 한다
   (실행 폴더에 `slot_removals.json` 이 없을 때 — 반영 전) — 거부될 칸 삭제가 남은 `pass` 카드가 있으면 exit 1 이다.
3. **직접 고치지 않는다.** 판정만 한다. 기준표에 없는 취향 지적을 하지 않는다.
3-1. **반려가 있으면 기자에게 한 번 되돌린다(재작업 1회 — 2026-11-14 대표 결정 · 상한 25장 2026-11-21).** 조건: 코드 검사 exit 0 · reject 1~25장(2-4 `deep-auto`·2-5 사전 점검 **뒤**의 수 — 심층에서만 걸린 카드는 세지 않고, 칸 삭제가 거부될 카드는 센다) · 이 호의 첫 판정(`copyedit.json` 이 아직 없었다).
   **코드 검사가 카드 몇 장 때문에 실패한 때도 같다(리허설 1118 — 2026-11-17).** 실패 메시지(`LLM 슬롯 주입 검증 실패`·`발행본 조립 검증 실패`)의 줄마다 카드 id 가 하나씩 들어 있고(`<카드 id>.<슬롯>` 또는 `카드 '<카드 id>': … <슬롯> …` — 리허설 1120 의 원문 부재 단정이 뒤의 꼴) 그 카드가 1~25장이면
   **그래도 의미 점검은 건너뛰지 않는다 — 전 카드에 그대로 한다(리허설 1122b — 2026-11-22: 코드 검사에 5장이 걸려 151장 의미 점검을 건너뛰었더니 반려 13장이 2차에서야 드러나 `보류` 가 됐다).** 코드 검사에 걸린 카드는 `cards` 에 `verdict: "reject"` 와 줄마다 `fails` `{"item": "코드 검사", "slot": "<슬롯>", "evidence": "<그 줄>"}` 로 적고, 나머지 카드는 평소대로 2·2-1번을 판정한다.
   재작업 카드(`rework.cards`) = 코드 검사 실패 카드 ∪ 의미 반려 카드(겹치면 한 번만 센다). 합쳐서 1~25장이면 `재작업` 으로 되돌린다.
   2차(3-2)는 코드 검사를 다시 돌린 뒤 **반려됐던 카드만** 다시 판정한다(1차에 이미 전 카드를 점검했다). 카드 id 가 없는 줄(`delta.tldr` 등)이 하나라도 있거나, 국문 병기 위반(483·WL·WHOPIR)이 있거나, 합쳐서 25장을 넘으면 종전대로 `보류`.
   그러면 마감하지 않고 `copyedit.json` 을 `"status": "재작업"`, `"round": 1`, `"published": false`, `"rework": {"cards": ["<반려 카드 id>", …]}` 로 써서
   `delta.json`(판정한 그대로)과 함께 커밋·push 하고 끝낸다. Notion 에는 아무것도 쓰지 않는다. 재작업 카드(코드 검사 실패 ∪ 의미 반려)가 25장을 넘거나 카드 id 없는 코드 검사 실패가 있으면 되돌리지 않고 종전대로 `보류`.
   **1차 교열은 늦어도 09:00 까지 `재작업` 을 push 한다** — 재작업 기자(09:02)는 09:12 까지만 기다린다. 반려가 많을수록 재작업·2차가 길어진다(`RUN.md` 5-1 시간표).
3-2. **다시 판정(2차).** 시작할 때 `copyedit.json` 이 이미 `재작업` 이면 이 실행은 2차다(`완료`·`보류`·`실패` 면 곧바로 종료).
   **예약작업 프롬프트에 `역할: 교열(2차)` 가 있는 실행(09:12 슬롯)은 2차만 한다** — `copyedit.json` 이 아직 없으면 1차 교열(08:45)이 아직 샤드를 기다리는 중이다:
   아무것도 쓰지 않고 종료한다(1차 판정을 대신하지 않는다 — 두 교열이 따로 판정하거나 1차가 `CONSUMED` 를 찍은 뒤 `copyedit.json` 을 덮어쓰면 아무도 발행하지 않는다).
   `재작업` 이 아니면(`완료`·`보류`·`실패`) 역시 아무것도 쓰지 않고 종료한다.
   `rework.json` 이 `완료` 가 될 때까지 09:20 KST 까지 2분마다 다시 확인하고(리허설은 시각 무시), 그때도 아니면 보류하지 않는다 — 1차 반려 카드를 대표 결정 요청으로 올린다(아래 3-3 `finish --rework-unfinished`). `완료` 면:
   ```
   mkdir -p _scaffold && cp company/run/{run_date_kst}/copyedit.json _scaffold/copyedit.r1.json && cp company/run/{run_date_kst}/delta.json _scaffold/delta.r1.json
   cp company/run/{run_date_kst}/deep_delta.json _scaffold/deep_delta.r1.json
   python company/editorial/merge_parts.py company/run/{run_date_kst}
   python company/editorial/precheck.py rework-diff _scaffold/delta.r1.json company/run/{run_date_kst}/delta.json _scaffold/copyedit.r1.json \
     --deep-old _scaffold/deep_delta.r1.json --deep-new company/run/{run_date_kst}/deep_delta.json
   ```
   `rework-diff` 가 exit≠0 이면(반려되지 않은 카드가 바뀌었거나 카드가 늘고 줄었다) 판정하지 않고 `보류`. exit 0 이면 1번(코드 검사)을 다시 돌리고,
   **반려됐던 카드만** 2·2-1번 전 항목으로 다시 판정한다(나머지 카드는 글자가 그대로이므로 1차 판정 `pass` 를 그대로 옮긴다).
   반려 카드가 10장을 넘으면 1-1 처럼 서브 에이전트 여러 개(조각마다 2-2 표를 글자 그대로)로 나눠 동시에 판정한다 — 09:20 전에 마감까지 가야 한다.
   다시 판정한 뒤에도 2-4 `deep-auto` 와 2-5 사전 점검을 돌린다(2차에 심층에서만 걸린 카드도 심층 삭제로 통과 · 지울 수 있는 칸만 걸린 카드는 칸 삭제로 통과 · 1차 칸 삭제는 `deep-auto` 가 잇는다 · 거부될 칸 삭제는 `reject` — 2차라 `보류`).
   `deep-auto` 의 마지막 줄 `남은 반려 N장` 을 본다 — 1차가 칸 삭제로 정한 칸을 2차가 `fails` 로 올려도 코드가 칸 삭제로 통과시킨다.
   2차에 코드 검사(1번)에 걸린 카드는 그 카드에 `verdict: "reject"` · `fails` `{"item": "코드 검사", "slot": "<슬롯>", "evidence": "<그 줄>"}` 로 적는다(3-3 `finish` 가 재작업 2회차·대표 결정으로 보낸다).
   결과는 `copyedit.json` 을 `"round": 2` 로 새로 쓰고, 1차 기록은 `copyedit.r1.json` 으로 실행 폴더에 남긴다. **2차에도 reject 가 남아도 `보류` 하지 않는다 — 3-3 `finish` 로 재작업 2회차, 그래도 남으면 대표 결정을 받는다(2026-11-23 대표 방침: 백업 v16 은 꺼졌다 · 카드를 이월하지 않는다).**
   2차에 다시 판정한 카드는 `checked` 도 새 문장으로 다시 적는다(옛 근거의 `claim` 이 고친 문장에 없으면 검사가 걸린다). 옮긴 `pass` 카드는 1차 줄(근거 포함)을 그대로 옮긴다.
   2차도 `완료` 전에 1-1 의 `copyedit_evidence.py check` exit 0 이어야 한다.
   **2차 재작업 카드는 `checked` 를 새로 적는다** — 고친 문장을 원문과 다시 맞추고, 1차 반려 칸마다 근거 하나씩(1차 근거를 옮겨 적지 않는다).
   비우면 `copyedit_evidence.py check` 가 그 카드를 실패로 낸다(11-23 실전: 재작업 8장의 `checked` 가 비어 수동 마감까지 막혔다). 반려 안 된 카드의 근거는 `deep-auto` 가 1차 줄에서 잇는다.
3-3. **2차 마감 전 다음 할 일 — 근거 다시 → 재작업 2회차 → 대표 결정(2026-11-23 대표 방침: 백업 v16 은 꺼졌다 · 불완전한 카드는 싣지 않고 이월하지도 않는다).**
   2차가 판정·`deep-auto`·2-5 사전 점검을 다 한 뒤, 4번 마감 **전에** 돌린다(델타는 칸 삭제 전 것 · `apply` 전 · handoff 는 1-1 과 같은 파일):
   ```
   python company/editorial/blank_slots.py finish company/run/{run_date_kst} _handoff/handoff_$D.json
   ```
   코드가 `deep-auto` 를 같은 함수로 한 번 더 돌린 뒤(칸 삭제·심층 삭제로 풀리는 카드는 통과 · 1차 결정·근거는 잇는다) 다음 할 일을 exit 로 낸다. 카드를 미루지 않는다.
   - **exit 3 근거 다시** — `근거 다시:` 줄의 카드는 근거가 빠졌거나 틀렸다(1-1 과 같은 함수). 카드 결함이 아니라 교열 누락이다(11-23: 근거만 빠진 14장은 원문 대조로 모두 통과).
     같은 실행 안에서 그 카드만 `python company/editorial/copyedit_evidence.py show company/run/{run_date_kst} _handoff/handoff_$D.json --ids <id,…>` 로 원문과 나란히 다시 읽고
     `checked` 를 적는다(재작업 카드는 1차 반려 칸마다 고친 문장의 근거). 읽다가 원문과 어긋나면 그 카드를 `reject` 로 바꾼다. 그리고 `finish` 를 다시 돌린다.
   - **exit 4 재작업 2회차** — 내용 반려가 1~5장이고 2회차를 아직 안 했다(`copyedit.json` 에 `rework2.cards` 가 적힌다). 같은 날 기자가 한 번 더 고친다:
     ```
     cp company/run/{run_date_kst}/copyedit.json _scaffold/copyedit.r2.json && cp company/run/{run_date_kst}/delta.json _scaffold/delta.r2.json
     cp company/run/{run_date_kst}/deep_delta.json _scaffold/deep_delta.r2.json
     ```
     서브 에이전트 **하나**를 기자로 띄워 `reporter.md` "재작업 2회차" 절만 하게 한다(교열은 슬롯을 직접 고치지 않는다 — 쓰는 이와 보는 이가 다르다). `rework2.json` 이 `완료` 면
     `merge_parts.py` → `precheck.py rework-diff _scaffold/delta.r2.json company/run/{run_date_kst}/delta.json _scaffold/copyedit.r2.json --deep-old _scaffold/deep_delta.r2.json --deep-new company/run/{run_date_kst}/deep_delta.json`
     (exit≠0 이면 2회차 결과를 버리고 `git checkout` 으로 샤드를 되돌린 뒤 exit 5 로 간다) → 코드 검사(1번) → 그 카드만 2·2-1번으로 다시 판정(근거 새로) → `finish` 를 다시 돌린다.
     시간 근거(`RUN.md` 5-1 실측): 재작업은 장당 2~3분이 대부분 고정 비용이고 2차 재판정·코드 검사가 약 4분이다 — 2차가 09:16 전후에 끝나면 5장 2회차와 재판정이 09:25 안에 들어간다. 그래서 상한 5장.
   - **exit 5 대표 결정** — 2회차 뒤에도 남았거나 5장을 넘는다. 코드가 `copyedit.json` 을 `결정 대기` 로 바꾸고 `decision_request.md`(헌장 3줄 · 카드·사유·결정 예시)를 쓴다.
     `copyedit.json`·`decision_request.md`·`delta.json`·`deep_delta.json` 을 커밋·push 하고 끝낸다 — 결정 전에는 발행하지 않는다(브릿지는 `완료` 가 아닌 반납을 가져가지 않는다).
     비서실이 같은 날 대표 결정을 받아 이어 간다: 고칠 문장 지시면 기자 재작업 → 2차 다시, 빼고 발행이면
     `python company/editorial/blank_slots.py finish company/run/{run_date_kst} _handoff/handoff_$D.json --drop <id,…> --decision "<대표 결정 원문>"` → `finish` → 4번 마감.
     카드를 빼는 것은 대표 결정이 있을 때만이다(`--drop` 은 `decision_request` 에 올린 카드만 받는다).
   - **exit 0 마감** — 근거·반려 모두 0. 곧바로 4번 마감으로 간다. exit 1 은 카드로 가를 수 없는 문제(판정 줄 중복 등)다 — 판정 파일을 고쳐 다시 돌린다.
   재작업이 09:20 까지 `완료` 가 아니면(`rework.json`) `merge_parts.py` 를 돌리지 않고
   `python company/editorial/blank_slots.py finish company/run/{run_date_kst} _handoff/handoff_$D.json --rework-unfinished` 로 1차 반려 카드를 대표 결정 요청으로 올린다(exit 5).
   **2차가 코드 검사 exit 0 · reject 0 이면 곧바로 아래 4번 마감을 1번(`scaffold.json` 복사 → `delta.json`·`deep_delta.json`·`scaffold.json`·`copyedit.json` push)부터 한다.**
   `copyedit.json` 에 `완료` 만 적고 끝내면 브릿지가 가져갈 반납이 없어 백업이 발행한다(리허설 1120d — 2차 통과 뒤 `scaffold.json` 이 없었다).
4. **마감 — 코드 검사 exit 0 이고 reject 0 일 때만.** 전수 대조(1-1 `copyedit_evidence.py check`) exit 0 도 조건이다 — 근거가 빠진 카드가 있으면 reject 0 은 0 이 아니다. 하나라도 아니면 Notion 에 아무것도 쓰지 않고 끝낸다
   (Notion handoff 가 OPEN 으로 남아 09:30 백업 Routine 이 발행한다). 통과면 이 순서로 (`RUN.md` 0항 `반납 길: git` 인 호):
   1. **반납 = 실행 폴더 push.** 코드 검사에 쓴 스캐폴드를 **그 파일 그대로** 실행 폴더에 복사한다(다시 저장·정렬하지 않는다):
      `cp _scaffold/brief_web_$U.json company/run/{run_date_kst}/scaffold.json`.
      `delta.json`·(있으면) `deep_delta.json`·`scaffold.json`·`copyedit.json`(`status: 완료`, `published: true`)·(칸 삭제·심층 삭제가 있었으면) `slot_removals.json` 을 커밋·push 한다(RUN.md 3항 방식).
      발행 브릿지가 이 브랜치에서 가져가 main 에 넣는다 — Notion 에 델타를 예치하지 않는다(v16 [산출물 — 델타 Notion 예치] 의 예치·글자 대조는 하지 않는다).
      push 가 3번 거절되면 Notion 에 아무것도 쓰지 않고 `실패` 로 끝낸다.
   2. push 가 끝나면 **곧바로** Notion handoff 페이지(제목 `OPEN GRM Routine Handoff {run_date_kst}`)를 v16 [0단계] PL-10 마감대로 `CONSUMED` 로 바꾼다
      (`desk.json` 의 `defer` row 가 있으면 `DEFERRED` 블록 포함). 이때부터 백업 Routine 은 돌지 않는다.
      09:25 까지 못 바꿨으면 `copyedit.json` 을 `status: 실패`·`published: false` 로 고쳐 다시 push 하고 끝낸다(RUN.md 5항).
   3. **브릿지 요청 — 2번까지 끝나면 곧바로 한 번.** 브릿지 크론만 기다리면 월요일 반영이 몇 시간 늦는다(실측 첫 발화 09:10~10:54 KST).
      `company/CLOUD.md` "Actions 다리" 형식 그대로 요청 마커 한 파일을 올린다 — `id` 는 지금 KST 시각으로 `YYYYMMDD-HHMM-bridge`:
      `git fetch origin main && git checkout -b claude/request-{run_date_kst}-delta-bridge FETCH_HEAD` →
      `company/requests/<id>.json` 에 `{"id": "<id>", "action": "delta-bridge", "args": {"publish_date": "{run_date_kst}"}}` →
      그 파일만 `git add` · 커밋 · `git push origin claude/request-{run_date_kst}-delta-bridge` → `git checkout claude/company-{run_date_kst}` 로 돌아온다.
      결과는 기다리지 않는다(크론이 예비다). push 가 거절되면 `copyedit.md` 에 `막힘: Actions — 브릿지 요청` 한 줄만 남긴다. 리허설은 이 단계를 하지 않는다(`REHEARSAL.md` 금지).
   4. **행 Status 는 찍지 않는다.** v16 [Status 갱신] 의 규칙(`render`·병합 멤버·`reflow`→Processed · `skip`→Skipped · `error`→Error · `defer`→미변경)은
      코드가 적용한다 — 브릿지가 델타를 가져간 직후 `grm_queue.py notion-sync` 가 발행 결과로 계산해 Notion 행에 찍는다. handoff 행에는 `page_id` 가 없다.
   (`반납 길: notion` 인 호는 종전 순서다: v16 [산출물 — 델타 Notion 예치] 로 예치·자기검증(실패 시 `VOID GRM Web Delta {날짜}`·Skipped) → handoff `CONSUMED` → v16 [Status 갱신] 대로 행 Status 를 `desk.json` 의 `action` 에 맞춰 바꾼다.)

## 결과 — `company/run/{run_date_kst}/copyedit.json`
```
{
  "status": "완료|보류|실패|재작업",
  "round": 1,
  "code_gate": {"ran": true, "exit": 0, "message": ""},
  "cards": [{"web_card_id": "...", "verdict": "pass|reject",
             "fails": [{"item": "자가점검 9", "slot": "key_facts", "evidence": "한 줄"}],
             "notes": [{"item": "같은 이름", "slot": "deep.key_violations[0].observation", "evidence": "한 줄"}],
             "checked": [{"slot": "summary", "claim": "카드 문장 그대로", "source": "원문 조각 그대로"},
                         {"slot": "hook_line", "claim": "…", "source": "…"}]}],
  "counts": {"cards": 0, "pass": 0, "reject": 0, "fails_total": 0, "notes_total": 0}
}
```
`checked` 는 카드마다 필수다(1-1 — 원문이 없는 카드만 `source_absent`). `fails` 는 반려 근거(2-2 표 왼쪽)만, `notes` 는 주의(표 오른쪽)만 적는다 — 없으면 빈 배열이거나 키를 빼도 된다. `verdict` 는 `pass`·`reject` 둘뿐이다(주의 카드도 `pass`).
`counts.reject` 는 `verdict: "reject"` 카드 수다 — `notes`(칸 삭제·심층 삭제 포함)는 세지 않는다.
같은 폴더에 `copyedit.md` — 헌장의 맨 위 3줄만.

`copyedit.json` 에 `"published": true|false` 를 더해, 마감까지 했는지 적는다.

## 하지 않는 것
슬롯 수정(반려 카드는 기자가 고친다 — 3-1. 2-3 칸 삭제·2-4 심층 삭제는 `blank_slots.py` 로만), 카드 채택 변경, 검색, 마감 4번 밖의 Notion 쓰기(행 Status 포함), 다른 직무 파일 수정.
