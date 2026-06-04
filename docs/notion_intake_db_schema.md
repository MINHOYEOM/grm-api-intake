# GRM API Intake — Notion Database 스키마

이 문서는 GitHub Actions 수집기가 데이터를 기록하고, Claude Code Routine(현행 v15.6.3)이 읽어가는 **Intake staging DB** 의 속성 구조를 정의한다.

## ✓ DB 생성 완료 (2026-05-26)

| 항목 | 값 |
|---|---|
| Database 이름 | `GRM API Intake` |
| Database URL | <https://www.notion.so/7784c71fb7b343749b2bee5d04db7926> |
| **Database ID** | `7784c71fb7b343749b2bee5d04db7926` |
| 부모 페이지 | `Global Regulatory Monitor` (기존 다이제스트 DB 와 같은 부모) |
| Data Source ID | `d5b9634a-2bd7-4036-ba06-e4ad17ede288` |

**남은 셋업 단계 (사용자 작업)**

1. Notion → Settings → Integrations → `New integration` → 이름 자유 (예: `GRM Intake Collector`), Internal 타입
2. 발급받은 토큰을 GitHub Secrets `NOTION_TOKEN` 에 등록 (이 채팅에 노출된 `ntn_…` 토큰은 반드시 `Rotate token` 으로 폐기 후 새 토큰 발급)
3. Integration → `Access` 탭 → 부모 페이지 `Global Regulatory Monitor` 추가 (자식 `GRM API Intake` 자동 상속)
4. 위 Database ID `7784c71fb7b343749b2bee5d04db7926` 를 GitHub Secrets `NOTION_DATABASE_ID` 에 등록

## 속성 정의

| 이름 | 타입 | 필수 | 값/옵션 | 비고 |
|---|---|---|---|---|
| `Name` | Title | ✓ | `{source-prefix} {document_id} — {headline}` | 자동 생성 |
| `Source` | Select | ✓ | `Federal Register` / `OpenFDA Recall` / `EMA` / `MHRA Inspectorate` / `PIC/S` / `ECA Academy` / `FDA Warning Letter` / `Brave Search` / `RAPS` / `European Pharma Review` / `MFDS` / `GRM Handoff` | `GRM Handoff`는 Routine 입력 큐용 |
| `Document ID` | Rich text | ✓ | 소스별 고유 ID 또는 stable hash | 검색·중복 제거 키 |
| `Date` | Date | ✓ | 문서 발행일/게시일 | 수집 윈도우 필터용 |
| `Headline` | Rich text | ✓ | 제목/제품명/게시글 제목 | 표시용 |
| `Official URL` | URL |  | 공식 원문 URL | Search Evidence C는 비울 수 있음 |
| `Source URL` | URL |  | Search/Scrape가 발견한 실제 URL | Phase 2a 신규 |
| `Type or Class` | Select |  | `Rule` / `Proposed Rule` / `Notice` / `Class I` / `Class II` / `Class III` / `legislative-notice` / `gmp-guideline` / `gmp-inspection` / `regulation-final` / `notice-final` / `guidance-industry` / `guidance-internal` / `safety-letter` / `recall-quality` / `admin-action` / `gmp-certificate` / `routine-handoff` 등 | MFDS는 사람용 번역 없이 기계용 영문 키 사용. `routine-handoff`는 Routine 입력 큐 |
| `Firm` | Rich text |  | 업체명 | Recall/WL 중심 |
| `Body` | Rich text |  | 요약/본문 일부 | 한국어 원문은 그대로 저장 |
| `Distribution` | Rich text |  | `distribution_pattern` | Recall 전용 |
| `Comments Close` | Date |  | 의견수렴 종료일 | Watch 항목 분류용 |
| `Run Date (KST)` | Date | ✓ | 수집 실행일 KST 자정 | Routine filter key |
| `Collected At` | Date | ✓ | 수집 실행 timestamp (with time) | 감사 추적용 |
| `API Query` | URL |  | 호출 API/RSS URL | Evidence A 정보 출처 |
| `Search Query` | Rich text |  | Brave Search 쿼리 | Phase 2a 신규 |
| `Raw Excerpt` | Rich text |  | Search snippet/excerpt | Phase 2a 신규 |
| `Evidence Candidate` | Select |  | `A` / `B` / `C` / `D` | 수집기 후보, Routine 최종 판정 |
| `QA Relevance` | Select |  | `Likely` / `Possible` / `Unrelated` / `Pending` | 수집기 휴리스틱 |
| `OSD Relevance` | Select |  | `Direct` / `Indirect` / `N/A` | OpenFDA Recall 중심 |
| `Modality` | Select |  | `Chemical` / `Biologic` / `Other` | **(제품군 확장)** 원료 성격 '큰 틀' 3분류. `compute_modality()` 부여. `ENABLE_MODALITY_TAG=true` 일 때만 기록되므로 **이 속성을 Notion DB 에 먼저 생성**해야 함(미생성 시 insert 실패) |
| `Source Type` | Select |  | `Official API` / `Official Regulatory Page` / `Official Regulator Blog` / `Expert Secondary` / `Search Result` / `Official Page Scrape` | Phase 2 분류 |
| `Signal Tier` | Select |  | `Tier 1` / `Tier 2` / `Tier 3` | 수집기 1차 우선순위 |
| `Language` | Select |  | `KO` / `EN` | Phase 2b MFDS 권고 필드 |
| `Region/Jurisdiction` | Select |  | `Korea (MFDS)` 등 | 규제 관할. MFDS 해외 실사 제조소 소재국으로 사용하지 않음 |
| `Site Country` | Rich text |  | 예: `대한민국`, `프랑스`, `일본` | GMP 실태조사 제조소 소재국. `Region/Jurisdiction`과 분리 |
| `Self-Check Required` | Select |  | `Yes` / `Review` / `No` | 휴면 필드. 신규 수집기는 자동 산정·기입하지 않음 |
| `Status` | Select |  | `New` / `Processed` / `Skipped` / `Error` | Routine 이 갱신 |

## 페이지 본문(children)

각 row의 페이지 본문에는 **원본 API 응답 JSON 전체** 를 코드 블록으로 저장한다.

- `language: "json"`
- 2000자 초과 시 여러 코드 블록으로 분할 (Notion rich_text 단일 limit 회피)
- Evidence A 운용 시 Routine 이 이 원문을 재확인할 수 있도록 보존

## QA Relevance 휴리스틱 (수집기 단계)

수집기는 가벼운 키워드 매칭만 수행하고, 최종 판정은 Routine 에 위임한다.

| 라벨 | 조건 |
|---|---|
| `Likely` | 13개 카테고리 키워드 매칭 + 경구 고형제·정제·CGMP·warning letter·data integrity·OOS·OOT·CAPA·dissolution·Annex 1 중 1개 이상 매칭 |
| `Possible` | 13개 카테고리 키워드 매칭 (경구 고형제 직접 키워드 없음) |
| `Unrelated` | 명시 제외 키워드 (medical device only, cosmetics, food safety, vaccine only 등) |
| `Pending` | 휴리스틱으로 판정 불가 (Routine 판정 대기) |

13개 카테고리 키워드 (단일 진실 공급원: `collect_intake.py`의 `QA_CATEGORY_KEYWORDS` 상수):

> **주의**: 이 목록은 참고용입니다. 코드 변경 시 이 섹션도 함께 갱신하세요.
> 실제 매칭은 단어 경계(`\b`) 기반으로 수행합니다 (v15.1 개선).

- GMP, CGMP, manufacturing practice
- Pharmaceutical Quality System, PQS, ICH Q10
- Quality Risk Management, QRM, ICH Q9
- data integrity, ALCOA, Part 11, Annex 11
- computer system validation, artificial intelligence
  *(주의: "CSV" 단독 약어는 파일 형식과 혼동 가능하여 제거됨. "computer system validation" 전체 구문 사용)*
- process validation, cleaning validation
- analytical procedure, ICH Q2, ICH Q14
- post-approval, CMC change, ICH Q12
- continuous manufacturing
- stability, ICH Q1, OOS, OOT
- deviation, CAPA, change control
- sterile, Annex 1
- supplier qualification
- **v15.1 추가 (OpenFDA Recall 특화)**: dissolution, assay failure, out of specification, particulate matter, particulate contamination, subpotent, superpotent, mislabeling, endotoxin
- **v15.1 추가 (Nitrosamine)**: nitrosamine, NDMA, NDEA, n-nitroso
- **v15.1 추가 (주요 제조사)**: Alkem, Aurobindo, Lupin, Zydus, Dr. Reddy

## Routine 측 읽기 쿼리

v15.6.3 이후 Routine 은 원 Intake DB를 직접 broad search 하지 않고, 수집기가 생성한 New-only handoff row를 먼저 읽는다:

```
Database ID: 7784c71fb7b343749b2bee5d04db7926
Data Source: collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288

Source: GRM Handoff
Type or Class: routine-handoff
Document ID: routine-handoff::{YYYY-MM-DD}
Title: OPEN GRM Routine Handoff {YYYY-MM-DD}
Body: JSON code block (schema_version = grm-routine-handoff/v1)
```

handoff JSON 의 `rows[]` 만 Routine 의 Intake 입력이다. 수집기는 이 handoff 를 만들 때 Notion API 속성 필터로 `Status=New` + `Run Date (KST)` window 를 적용하고, 동일 `Source::Document ID` 는 최신 row만 남긴다.

Routine 1회차 종료 시 handoff row 는 `Status=Processed`, title 은 `CONSUMED GRM Routine Handoff {YYYY-MM-DD}` 로 바꾼다. 같은 날 2회차 실행에서 `OPEN` handoff 가 없으면 빈 브리프/특이사항 없음으로 처리해 중복 발행을 막는다.

legacy(v15.0~v15.6.2) Routine 은 Notion MCP `notion-fetch` 또는 데이터소스 query 로 다음 필터를 적용했다:

```
Database ID: 7784c71fb7b343749b2bee5d04db7926
Data Source: collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288

Source: any of [Federal Register, OpenFDA Recall, EMA, MHRA Inspectorate, PIC/S, ECA Academy, FDA Warning Letter, Brave Search, MFDS]
Run Date (KST): equals 오늘(KST 자정)
Status: any of [New, Processed]
```

`Run Date (KST)` 가 오늘과 정확히 일치하는 row 가 0건이면 Routine 은 v14.5 WebSearch-only 모드로 graceful degradation.
이 legacy 경로는 PL-10 이후 기본 운영 경로에서 사용하지 않는다.

## 운영 주의

- DB 자체 삭제 금지 (수집기·Routine 모두 의존)
- 속성 이름은 코드 상수와 동일. 변경 시 `collect_intake.py` 의 `PROP_*` 상수도 함께 수정
- Run Date(KST) 동일하고 Document ID 동일한 row 는 중복 — 수집기가 사전 조회 후 skip

## DB 재생성이 필요한 경우 (참고)

다른 워크스페이스로 옮길 때 등:

1. Notion에서 새 페이지 → `/Database - Full page` 선택
2. 이름: `GRM API Intake`
3. 위 속성표 그대로 추가 (이름 · 타입 정확히)
4. Integration 을 부모 페이지에 연결
