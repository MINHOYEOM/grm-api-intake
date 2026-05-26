# GRS_Prompt_v15.0.md — Self-review pass 1 + E2E validation

이 문서는 Codex 가 다루지 않은 영역 (Routine 프롬프트 자체 일관성 + Notion MCP 한계) 에 대한 self-review 결과와, 다음 월요일 첫 자동 실행 전에 반영한 변경을 기록한다. Codex 재검토에 첨부할 수 있도록 자체 완결로 작성.

## 1. Findings (총 6건)

| # | 영역 | severity | 발견 | 처리 |
|---|---|---|---|---|
| F1 | 운영 모델 vs 0순위 모순 | **critical** | 핵심 원칙 6 "best-effort 1회 시도 후 진행" vs [0순위] "직접 호출하지 않는다" 가 직접 충돌. Routine LLM 이 어느 정책을 따를지 모호. | ✅ 핵심 원칙 6 갱신: "공식 API 직접 호출·공식 사이트 직접 WebFetch 를 수행하지 않는다" 로 명확화. 보조 출처 WebFetch ([3순위]) 는 별도 정책 유지. |
| F2 | cron 시각 표기 drift | **critical** | Codex should-fix 로 cron 을 `0 22` → `7 22 * * 0` 로 변경했지만, GRS_Prompt_v15.0.md L42 · L507 · README.md L10 · setup_guide.md L113 에 stale "22:00 UTC / 07:00 KST" 잔존. | ✅ 4 곳 모두 "22:07 UTC / 07:07 KST" 로 통일. |
| F3 | "WebFetch" 의미 모호 | moderate | 같은 도구 이름이 두 정책에 등장: "공식 사이트 fetch (금지)" vs "보조 출처 fetch (수행)". 모호함. | ✅ F1 갱신 시 "보조 출처 WebFetch" 로 명시 구분. |
| F4 | QA Relevance `Unrelated` row 처리 미명시 | moderate | L127 가 "Pending/Possible/Likely 우선" 이라고만 적어 `Unrelated` 처리 정책이 공백. 수집기 휴리스틱이 너무 보수적이라 (첫 실행 15/15 = Pending) 실제 QA-critical 항목이 Unrelated 로 들어올 수 있음. | ✅ "Unrelated row 는 raw payload 의 reason_for_recall / abstract 를 직접 확인. 카테고리 관련성 있으면 본문 카드, 없으면 M2 에 미해당 N건 집계" 로 명시. |
| F5 | `Status` 필드 갱신 의무 미명시 | moderate | DB 스키마에 `Status: New / Processed / Skipped / Error` 가 있고 "Routine 이 갱신" 이라고 적혀있는데, 프롬프트에는 갱신 지시 없음. | ✅ "본문 카드 작성 완료 row 의 Status 는 Processed 로 갱신 (Notion API update)" 명시. |
| F6 | **Notion MCP property filter 부재** | **critical** | `notion-search` 는 의미 기반·`notion-fetch` 는 단일 entity. 사용자 property (`Run Date (KST)`) server-side filter 불가. 검증 실험: 미래 날짜 query 에도 현재 row 가 ranking 으로 섞여 반환됨. Routine 이 검색 결과를 단순 신뢰하면 다른 주차 row 가 다이제스트에 흘러들 수 있음. | ✅ [0단계] 에 "실행 흐름 (Notion MCP 한계 보완)" 섹션 추가. notion-search → notion-fetch per row → client-side property 검증 3단계 명시. |

## 2. E2E validation (이번 세션에서 수행)

수행 환경: 현재 세션의 Notion MCP 가 Routine 의 실제 환경과 동일.

### Test 1 — 기본 query 성공

```
notion-search(
  data_source_url="collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288",
  query="Run Date 2026-05-26",
  page_size=25
)
```
**결과**: 15 row 모두 반환 (FR 12 · Recall 3). 일치.

### Test 2 — 단일 row property 검증

`Recall D-0547-2026 Ascend Laboratories` page 의 `notion-fetch`:
```
"properties": {
  "Source":"OpenFDA Recall",
  "Document ID":"D-0547-2026",
  "Status":"New",
  "Type or Class":"Class II",
  "QA Relevance":"Pending",
  "date:Run Date (KST):start":"2026-05-26",
  "date:Date:start":"2026-05-20",
  "Headline":"Ascend Laboratories, LLC, Metoprolol Succinate Extended-Release Tablets, USP, 25 mg...",
  "Firm":"Ascend Laboratories, LLC",
  "Body":"Failed Dissolution Specifications",
  "Distribution":"U.S. Nationwide",
  "Official URL":"https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts",
  "API Query":"https://api.fda.gov/drug/enforcement.json?search=report_date:[20260519+TO+20260526]&limit=100&skip=0"
}
"content": "### Raw API payload\n```json\n{ ... full OpenFDA payload ... }\n```"
```
**결과**: 16개 property 모두 채워짐, raw payload code block 페이지 본문에 보존. Routine 의 0단계 가 흡수해야 할 데이터가 정확히 정의된 위치에 있음.

### Test 3 — Notion MCP 의 property filter 한계 검증

```
notion-search(
  data_source_url="collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288",
  query="Run Date 2026-06-01",   # 미래 날짜
  page_size=5
)
```
**결과**: 2026-05-26 row 들이 반환됨. **`notion-search` 는 의미 기반 ranking** 이라 query date 에 정확히 매치되지 않은 row 도 ranking 으로 섞임.

**시사점**: Routine 의 0단계 가 client-side property 검증 단계를 반드시 수행해야 함. 단순히 `notion-search` 결과를 신뢰하면 다른 주차 row 가 다이제스트에 섞일 위험.

→ **F6 의 fix 가 바로 이 위험을 차단**.

## 3. 추가 검증 (Phase 2 가능성)

`notion-search` 가 `filters.created_date_range` 를 지원하므로 (per row 의 createdTime), Routine 이 그것까지 활용하면 server-side 필터링이 일부 가능:
```python
notion-search(
  data_source_url=...,
  filters={"created_date_range": {"start_date": today, "end_date": today}},
  query="GRS API Intake"
)
```
단점:
- `createdTime` 은 UTC 기준 → KST 자정 보정 필요 (Routine 이 직접 계산)
- 정확히 같은 KST 일에 created 됐다는 보장: 수집기가 일요일 22:07 UTC ≈ 월요일 07:07 KST 에 row 생성 → KST 당일과 일치. 단, 수집기 재실행 시 created date 가 달라지면 깨짐.
- robust 하지 못함. 현재의 "notion-fetch + client-side filter" 가 더 신뢰성 높음.

Phase 2 검토용으로만 기록.

## 4. 변경된 파일 (이번 review pass 1)

| 파일 | 변경 |
|---|---|
| `GRS_Prompt_v15.0.md` | 핵심 원칙 6 (F1+F3) · [0단계] 흐름 명확화 + 검증 단계 추가 (F4+F5+F6) · M3 cron 표기 (F2) |
| `README.md` | cron 표기 22:00 → 22:07 (F2) |
| `setup_guide.md` | cron 표기 22:00 → 22:07 (F2) · 보안 메모 갱신 (사용자가 ProcessStartInfo 로 PowerShell 업그레이드한 것 반영) |

## 5. 다음 자동 실행 (2026-06-01 월 07:07 KST) 전 점검 체크리스트

| 체크 | 확인 방법 |
|---|---|
| 이 PR 이 main 에 merge 됐는가 | GitHub PR 페이지 |
| GRS_Prompt_v15.0.md 본문이 Claude Code Routine 에 붙여넣어졌는가 | Routine 설정 화면 |
| Notion Integration 이 부모 페이지에 연결됐는가 | Notion → 부모 페이지 → Connections |
| Notion Intake DB 에 다음 주차 새 row 가 생기는가 | 일요일 22:07 UTC 후 GRS API Intake DB 확인 |
| Routine 이 그 row 들을 client-side 필터링으로 정확히 선별하는가 | 월요일 07:30 KST 후 다이제스트 페이지의 M2 메타 확인 ("Intake 결과: FR N · Recall N") |
