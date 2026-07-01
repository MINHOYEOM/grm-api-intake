# GRM WL 심층분석 fan-out — 실행 절차(월요일 Routine 2단계)

> **무엇.** Warning Letter 카드의 `deep_analysis`(4섹션 심층분석)를 **카드당 독립 서브에이전트
> 1개**로 생성·검증·병합하는 절차. 월요일 Routine(MINO 의 Claude Code 세션) **1단계(기존 6슬롯
> 치환)**를 마친 뒤 이어서 도는 **2단계**다.
>
> **실행 모델(2026-07-01 확정 · 지시문 §3.1).** 신규 GitHub Actions + Anthropic API 키(호출당
> 과금) 조합은 **배제**됐다. deep_analysis 는 6슬롯 Routine 과 똑같이 **이 세션의 Claude Code
> 서브에이전트(Task)** 로, 즉 구독 사용량 안에서(무과금) 처리한다. 카드당 서브에이전트 1개가
> 그 카드의 `body_full` 만 보므로 카드 간 내용이 섞일 여지가 구조적으로 없다.

## 전제
- `ENABLE_WL_BODY_FULL=true` 로 수집돼 handoff 에 `deep_analysis_ready=true` + `deep_analysis_input.body_full` 이 실린 WL 카드가 있을 것(없으면 이 절차는 통째로 건너뛴다 — 브리프는 6슬롯만으로 정상 발행).
- 1단계(6슬롯) 완료 — 그때 쓴 **handoff JSON**(카드 목록)과 빈슬롯 **`brief_web_*.json`** 이 있을 것.
- 순수 헬퍼: `deep_analysis_fanout.py` / 게이트: `verify_deep_analysis.py` / 프롬프트: `docs/prompts/GRM_Prompt_DeepWL_v1.md` / 병합: `inject_slots.py`.

## 절차

**1) 작업목록 만들기 (결정론)**
```
python -m deep_analysis_fanout build-jobs --handoff <handoff.json> --out jobs.json
```
→ `jobs.json` = `[{document_id, body_full}, ...]` (deep_analysis_ready 카드만). 0건이면 여기서 종료.

**2) 카드당 서브에이전트 1개 호출 (LLM 단계 — 이 세션에서)**
`jobs.json` 의 **각 항목마다** Claude Code 서브에이전트(Task)를 **하나씩** 띄운다. 각 서브에이전트에 주는 것은 **딱 두 가지**뿐:
  - `docs/prompts/GRM_Prompt_DeepWL_v1.md` 전문(지시).
  - 그 job 의 `body_full` (해당 편지 원문. **다른 카드·다른 맥락은 절대 주지 말 것** — 격리가 fan-out 의 핵심).
서브에이전트는 4섹션 JSON(`key_violations`·`fda_evaluation`·`required_remediation`·`administrative_risks`) **하나만** 반환한다. 여러 카드를 한 서브에이전트에 몰아넣지 말 것(부하·혼동 방지).

**3) 응답 모으기**
반환된 JSON 들을 `{document_id: <4섹션 JSON>}` 형태의 `responses.json` 으로 저장(키 = 그 job 의 `document_id`).

**4) 게이트 통과분만 델타로 (결정론)**
```
python -m deep_analysis_fanout assemble --jobs jobs.json --responses responses.json --out deep_deltas.json
```
→ 각 응답을 그 카드 `body_full` 로 `verify_deep_analysis` 게이트(D1 구조·D2 인용 근거대조·D3 숫자)에 통과시켜 **PASS 만** `deep_deltas.json` 에 싣는다. stderr 에 `병합 N · 보류 M` 리포트가 카드별 사유(FAIL 게이트 report)와 함께 찍힌다 — **이 리포트를 실행 로그에 반드시 남길 것**(어떤 카드가 왜 빠졌는지 추적 = 지시문 §2 요구).

**5) 브리프에 병합**
```
python inject_slots.py --brief <brief_web.json> --delta <sixslot_delta.json> \
    --deep-analysis-deltas deep_deltas.json --out <final_brief.json>
```
→ 6슬롯 + deep_analysis 를 함께 병합. deep 델타가 없는(또는 게이트 보류된) 카드는 `deep_analysis=null` 그대로 두어 **6슬롯만으로 발행**된다(카드 단위 graceful degrade — 전체 브리프는 안 막힌다).

## 실패·주의
- **게이트 FAIL = 정상 동작.** 조항 인용이 원문에 없거나 어순이 다르면(§2 교훈: `section 502(a) of the FD&C Act` 처럼 **원문 표현 그대로** 써야 통과) D2 가 막는다. 그 카드는 6슬롯만으로 나가고 브리프는 계속된다. 프롬프트가 원문-verbatim 인용을 이미 지시하지만, 반복 FAIL 카드는 원문 대조로 확인.
- **발행 전 표본 육안 검증(지시문 §3.2).** 게이트 PASS ≠ 정확. 최소 몇 건은 사람이 `body_full` 과 4섹션을 대조해 (a) 날조가 실제로 막히는지 (b) PASS 산출물이 정확한지 확인한 뒤 운영 투입.
- **비-WL·전문 미확보 카드**엔 이 절차가 아무 영향도 주지 않는다(`deep_analysis` 키 자체가 없어 렌더에도 안 나타남).

## 📝 변경 이력
| 날짜 | 변경 |
|---|---|
| 2026-07-01 | 최초(CC). 실행모델 확정(카드당 서브에이전트·무과금) 반영. `deep_analysis_fanout.py`(build-jobs/assemble) + `GRM_Prompt_DeepWL_v1.md` + `inject_slots.py --deep-analysis-deltas` 배선. |
