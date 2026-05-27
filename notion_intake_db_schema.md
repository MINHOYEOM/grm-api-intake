# GRS API Intake — Notion Database 스키마

이 문서는 GitHub Actions 수집기가 데이터를 기록하고, Claude Code Routine v15.0이 읽어가는 **Intake staging DB** 의 속성 구조를 정의한다.

## ✓ DB 생성 완료 (2026-05-26)

| 항목 | 값 |
|---|---|
| Database 이름 | `GRS API Intake` |
| Database URL | <https://www.notion.so/7784c71fb7b343749b2bee5d04db7926> |
| **Database ID** | `7784c71fb7b343749b2bee5d04db7926` |
| 부모 페이지 | `Global Regulatory Sweep` (기존 다이제스트 DB 와 같은 부모) |
| Data Source ID | `d5b9634a-2bd7-4036-ba06-e4ad17ede288` |

**남은 셋업 단계 (사용자 작업)**

1. Notion → Settings → Integrations → `New integration` → 이름 자유 (예: `GRS Intake Collector`), Internal 타입
2. 발급받은 토큰을 GitHub Secrets `NOTION_TOKEN` 에 등록 (이 채팅에 노출된 `ntn_…` 토큰은 반드시 `Rotate token` 으로 폐기 후 새 토큰 발급)
3. Integration → `Access` 탭 → 부모 페이지 `Global Regulatory Sweep` 추가 (자식 `GRS API Intake` 자동 상속)
4. 위 Database ID `7784c71fb7b343749b2bee5d04db7926` 를 GitHub Secrets `NOTION_DATABASE_ID` 에 등록

## 속성 정의

| 이름 | 타입 | 필수 | 값 (Federal Register) | 값 (OpenFDA Recall) | 비고 |
|---|---|---|---|---|---|
| `Name` | Title | ✓ | `FR {document_number} — {title 80자}` | `Recall {recall_number} — {recalling_firm}` | 자동 생성 |
| `Source` | Select | ✓ | `Federal Register` | `OpenFDA Recall` | 옵션 2개 사전 등록됨 |
| `Document ID` | Rich text | ✓ | `document_number` (예: `2026-10277`) | `recall_number` (예: `D-1234-2026`) | 검색·중복 제거 키 |
| `Date` | Date | ✓ | `publication_date` | `report_date` | 7일 윈도우 필터용 |
| `Headline` | Rich text | ✓ | `title` | `product_description` (2000자 절단) | 표시용 |
| `Official URL` | URL | ✓ | `html_url` | FDA Recalls L2 (고정) | Evidence A 게이트 |
| `Type or Class` | Select |  | `Rule` / `Proposed Rule` / `Notice` / `Presidential Document` | `Class I` / `Class II` / `Class III` | 7개 옵션 사전 등록됨, 새 값은 자동 생성 |
| `Firm` | Rich text |  | (비움) | `recalling_firm` | Recall 전용 |
| `Body` | Rich text |  | `abstract` (2000자 절단) | `reason_for_recall` (2000자 절단) | 본문 요약 필드 |
| `Distribution` | Rich text |  | (비움) | `distribution_pattern` (2000자 절단) | Recall 전용 |
| `Comments Close` | Date |  | `comments_close_on` | (비움) | Watch 항목 분류용 |
| `Run Date (KST)` | Date | ✓ | 수집 실행일 KST 자정 | 동일 | Routine filter key |
| `Collected At` | Date | ✓ | 수집 실행 timestamp (with time) | 동일 | 감사 추적용 |
| `API Query` | URL | ✓ | 호출한 FR API URL | 호출한 OpenFDA API URL | Evidence A 정보 출처 |
| `QA Relevance` | Select |  | `Likely / Possible / Unrelated / Pending` | 동일 | 4개 옵션 사전 등록됨 |
| `OSD Relevance` | Select |  | `N/A` (FR 항목) | `Direct / Indirect / N/A` | **v15.1 신규** — Notion에 수동 추가 필요. 옵션 3개: Direct, Indirect, N/A |
| `Status` | Select |  | `New / Processed / Skipped / Error` | 동일 | 4개 옵션 사전 등록됨, Routine 이 갱신 (v15.1: Processed/Skipped 실제 갱신 지시 추가됨) |

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

v15.0 Routine 은 Notion MCP `notion-fetch` 또는 데이터소스 query 로 다음 필터를 적용한다:

```
Database ID: 7784c71fb7b343749b2bee5d04db7926
Data Source: collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288

Source: any of [Federal Register, OpenFDA Recall]
Run Date (KST): equals 오늘(KST 자정)
Status: any of [New, Processed]
```

`Run Date (KST)` 가 오늘과 정확히 일치하는 row 가 0건이면 Routine 은 v14.5 WebSearch-only 모드로 graceful degradation.

## 운영 주의

- DB 자체 삭제 금지 (수집기·Routine 모두 의존)
- 속성 이름은 코드 상수와 동일. 변경 시 `collect_intake.py` 의 `PROP_*` 상수도 함께 수정
- Run Date(KST) 동일하고 Document ID 동일한 row 는 중복 — 수집기가 사전 조회 후 skip

## DB 재생성이 필요한 경우 (참고)

다른 워크스페이스로 옮길 때 등:

1. Notion에서 새 페이지 → `/Database - Full page` 선택
2. 이름: `GRS API Intake`
3. 위 속성표 그대로 추가 (이름 · 타입 정확히)
4. Integration 을 부모 페이지에 연결
