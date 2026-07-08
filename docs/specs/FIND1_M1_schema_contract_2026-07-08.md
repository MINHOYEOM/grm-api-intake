# FIND-1 M1 스키마 계약 — raw_signals + grm-finding/v1

> 날짜: 2026-07-08  
> 상태: M1a 스키마 동결 + M1b raw_signals dry-run exporter + M1c SQLite append 배선 + M1d findings 변환 v0 + M1e findings dry-run/SQLite 경계 검증 + M1f source coverage dry-run 검증 + M1g feature-flagged collect_intake findings append + M1h internal backfill dry-run planner + M1i SQLite transaction dry-run + M1j guarded SQLite file write boundary + M1k read-only Notion export/live 7월 백필 완료  
> 코드 정본: `grm_findings.py`, `findings_exporter.py`, `findings_store.py`, `findings_extractors.py`, `findings_backfill.py`, `findings_backfill_sqlite.py`, `findings_backfill_apply.py`, `findings_notion_export.py`  
> 테스트 정본: `tests/test_grm_findings.py`, `tests/test_findings_exporter.py`, `tests/test_findings_store.py`, `tests/test_findings_extractors.py`, `tests/test_findings_backfill.py`, `tests/test_findings_backfill_sqlite.py`, `tests/test_findings_backfill_apply.py`, `tests/test_findings_notion_export.py`

---

## 0. M1 게이트

FIND-1의 모든 구현·이중 적재·내외부 백필은 `raw_signals`와 `findings(grm-finding/v1)` 스키마 동결 이후에만 진행한다.

이번 M1a의 범위는 **스키마 계약과 검증 장치**까지다. Notion 대량 백필, SQLite 파일 생성, Supabase 적재, 대시보드 구현은 이 문서와 테스트가 통과한 뒤 다음 세션에서 진행한다.

M1b는 이 계약 위에 **오프라인 dry-run exporter**만 추가한다. 기존 Notion snapshot + raw payload 입력을 `raw_signals` 레코드로 변환하고 report를 만들지만, Notion API 조회·SQLite 파일 생성·Supabase 적재·대량 백필은 수행하지 않는다.

M1c는 `ENABLE_FINDINGS_SQLITE_APPEND=false` 기본 off 상태에서 **신규 수집분의 raw_signals SQLite append 배선**만 추가한다. 활성화 시에도 dry-run에서는 쓰지 않고, Notion insert 성공 후에만 `IntakeItem.raw_payload`를 `raw_signals`로 append한다. SQLite append 실패는 log로만 남기며 기존 Notion write, handoff, Status 흐름과 삽입 통계를 바꾸지 않는다.

M1d는 이 계약 위에 **raw_signal → findings 변환 v0**만 추가한다. 이미 캡처된 `raw_json`/`row_json`을 순수 함수로 읽어 `grm-finding/v1` 레코드를 만들며, DB/API/백필/대시보드는 실행하지 않는다.

M1e는 변환 산출물을 **dry-run exporter와 SQLite 저장 경계에서만 검증**한다. 이 단계에서는 `collect_intake` 자동 findings append를 수행하지 않는다.

M1f는 신규 수집분 findings append 연결 전에 **source별 dry-run coverage**를 추가한다. 실제 Notion API 조회나 SQLite 파일 생성 없이, 483/MFDS/WL/WHO 샘플에서 findings 분포와 미생성 raw_signal을 report로 확인한다.

M1g는 `collect_intake` SQLite sidecar에 **feature-flagged raw_signals+findings append**를 연결한다. `ENABLE_FINDINGS_SQLITE_APPEND=true` 단독은 기존 raw_signals-only로 유지하고, `ENABLE_FINDINGS_SQLITE_FINDINGS_APPEND=true`까지 함께 켠 non-dry-run에서만 Notion insert 성공분을 한 SQLite transaction으로 append한다. dry-run 쓰기 없음, append 실패 WARN-only, 기존 Notion write/handoff/Status/insert stats 불변. 내부/외부 백필, Supabase 적재, 대시보드는 여전히 수행하지 않는다.

M1h는 **내부 백필 실행 전 dry-run plan/report**만 추가한다. 이미 확보한 export fixture/manifest를 오프라인으로 묶어 전역 dedupe, coverage, blocking error, review warning을 산출한다. Notion API 조회, SQLite write, Supabase write, Status/handoff 변경은 수행하지 않는다.

M1i는 M1h plan을 **SQLite `:memory:` transaction dry-run**으로 검증한다. 실제 SQLite DDL과 append helper를 사용해 first pass insert, replay idempotency, rollback을 확인하지만, SQLite 파일 write, Notion API 조회, Supabase write, Status/handoff 변경은 수행하지 않는다.

M1j는 M1h/M1i를 통과한 plan을 **명시 guard 뒤 로컬 SQLite 파일에 적용하는 write 경계**만 추가한다. `--write-file`과 `--db-path`가 모두 있어야 하며, M1i transaction dry-run이 통과하지 않으면 파일을 만들지 않는다. Notion API 조회, Supabase write, Status/handoff 변경, 운영 자동화는 수행하지 않는다.

M1k는 실제 7월 M1 운영 백필을 위해 **Notion Intake read-only export 경계**를 추가한다. `findings_notion_export.py`는 Intake DB를 읽고 page children의 raw JSON을 복원해 M1h 입력 JSON을 만든다. Notion/SQLite/Supabase/Status write는 하지 않는다. 2026-07-08 로컬 세션에서 공식 Notion API read-only export를 실행했고, 7월 운영 Intake 112건의 raw payload를 전부 복원한 뒤 M1h/M1i/M1j를 거쳐 `grm-findings.sqlite3`에 raw_signals 112/findings 24를 commit했다. Notion write, Supabase write, Status/handoff 변경, web publish 자동 반영은 없었다.

---

## 1. 전체 원칙

1. 기존 수집·발행 파이프라인은 불변이다. Findings는 additive 계층이다.
2. `raw_signals`는 재추출 가능한 원본 보존층이다. 원본 `raw`와 얇은 `row`를 안정 JSON으로 저장한다.
3. `findings`는 지적사항 분석층이다. FDA 483 Observation, MFDS GMP 지적사항, FDA WL 위반 본문, WHO inspection excerpt 등에서 나온 지적사항만 정규화한다.
4. record id는 현재시각이나 실행환경에 의존하지 않는다. `source + document_id` 또는 `raw_signal_id + ordinal + finding_text` 기반 SHA-256 prefix를 쓴다.
5. 분류는 v1에서 결정론 키워드 1차 분류만 한다. 불확실 항목은 버리지 않고 `other_quality_system` 또는 `needs_review`로 보낸다.
6. `raw_signal_id`는 내용 해시가 아니라 `(source, document_id)` 정체성 기반이다. 같은 문서가 나중에 보정되거나 원문 payload가 달라져도 기존 row를 자동 갱신하지 않는 **first-capture-wins** 의미론을 따른다. 라이브 신규 수집 경로(`IntakeItem` 기반 `row_json`)와 Notion 백필 경로(`_intake_page_snapshot` 기반 `row_json`)는 key set이 다를 수 있고, 먼저 저장된 형태가 유지된다. 같은 문서의 개정판 보존이 필요해지면 별도 revision/version 필드를 M2 이후에서 설계한다.

---

## 2. raw_signals

필수 필드:

| 필드 | 설명 |
|---|---|
| `schema_version` | 고정값 `grm-raw-signal/v1` |
| `raw_signal_id` | `rawsig-{sha256}` 안정 ID |
| `source` | 기존 row source (`FDA 483`, `MFDS` 등) |
| `source_kind` | 기존 `type_or_class` (`483`, `gmp-inspection`, `admin-action` 등) |
| `document_id` | 기존 dedup 기준 문서 ID |
| `published_date` | 원문/기관 발행일, `YYYY-MM-DD` 권장 |
| `title` | 기존 headline |
| `raw_sha256` | `raw_json`의 SHA-256 |
| `raw_json` | 원본 raw payload의 canonical JSON |
| `row_json` | 기존 얇은 row의 canonical JSON |
| `extraction_status` | 기본 `captured` |

보조 필드:

`collected_at`, `firm_name`, `site_name`, `site_country`, `modality`, `source_url`, `official_url`

SQLite 제약:

- `raw_signal_id` primary key
- `(source, document_id)` unique
- `schema_version = 'grm-raw-signal/v1'`

---

## 3. findings (`grm-finding/v1`)

필수 필드:

| 필드 | 설명 |
|---|---|
| `schema_version` | 고정값 `grm-finding/v1` |
| `taxonomy_version` | 고정값 `grm-finding-taxonomy/v1`; 분류 키워드/순서 개정 추적용 |
| `finding_id` | `finding-{sha256}` 안정 ID |
| `raw_signal_id` | `raw_signals.raw_signal_id` FK |
| `source` | 원천 source |
| `agency` | `FDA`, `MFDS`, `WHO`, `HC`, `ICH` 등 |
| `document_type` | `483`, `gmp-inspection`, `warning-letter` 등 |
| `document_id` | 원천 문서 ID |
| `published_date` | 원천 발행일 |
| `firm_name` | 업체명 원문 |
| `category_code` | taxonomy v1 코드 |
| `finding_text` | 지적사항 본문 또는 대표 deficiency |
| `evidence_level` | `A`, `B`, `C` |
| `evidence_url` | 공식 원문 우선, 없으면 source/index URL |
| `extraction_method` | `deterministic`, `llm_assisted`, `manual` |
| `review_status` | `accepted`, `needs_review`, `rejected` |

보조 필드:

`entity_id`, `site_name`, `site_country`, `product_family`, `modality`, `category_label_ko`, `finding_language`, `inspector_names`, `cfr_refs`, `mfds_refs`, `confidence`

SQLite 제약:

- `finding_id` primary key
- `raw_signal_id` foreign key
- `taxonomy_version = 'grm-finding-taxonomy/v1'`
- `category_code`는 taxonomy v1 코드 안에 있어야 함
- `evidence_level`은 `A/B/C`
- `extraction_method`는 `deterministic/llm_assisted/manual`
- `review_status`는 `accepted/needs_review/rejected`
- `(raw_signal_id, finding_text)` unique

---

## 4. 택소노미 v1

코드 정본은 `grm_findings.FINDING_TAXONOMY`다. v1은 20개 이하로 고정한다. 모든 `findings` 레코드는 `taxonomy_version=grm-finding-taxonomy/v1`을 기록해, 향후 키워드·순서·카테고리 개정 시 어떤 분류 계약으로 생성됐는지 추적한다.

| 코드 | 라벨 |
|---|---|
| `data_integrity` | 데이터 완전성 |
| `documentation_records` | 문서화/기록관리 |
| `aseptic_sterility_assurance` | 무균보증/무균공정 |
| `environmental_monitoring` | 환경모니터링 |
| `cleaning_validation` | 세척밸리데이션 |
| `deviation_capa` | 일탈/CAPA/조사 |
| `quality_unit_oversight` | 품질부서 관리감독 |
| `qc_lab_controls` | 시험실/품질관리 |
| `process_validation` | 공정밸리데이션 |
| `equipment_facility` | 설비/시설 |
| `material_supplier_control` | 원자재/공급업체 관리 |
| `contamination_control` | 오염/교차오염 관리 |
| `validation_qualification` | 밸리데이션/적격성평가 |
| `complaint_recall` | 불만/회수 |
| `stability_storage` | 안정성/보관 |
| `computer_system_validation` | 컴퓨터화시스템 |
| `labeling_packaging` | 표시/포장 |
| `regulatory_reporting` | 규제보고/변경관리 |
| `training_personnel` | 교육/작업자 |
| `other_quality_system` | 기타 품질시스템 |

분류기 v1은 결정론 substring 매칭이다. 빠르고 재현 가능하지만 `"capa"`가 더 긴 단어 일부와 겹치거나, 일반 키워드(`record`, `문서`)가 뒤쪽 특화 카테고리보다 먼저 잡히는 한계가 있다. 따라서 `FINDING_TAXONOMY`의 순서와 키워드 목록은 계약의 일부이며, 의미 있는 수정은 taxonomy version 이벤트로 다룬다.

---

## 5. 세션 분할

### M1a — 이번 세션

- `grm_findings.py` 추가
- `tests/test_grm_findings.py` 추가
- SQLite DDL 실행 검증
- FDA 483/MFDS GMP golden fixture 기반 raw_signal/finding 검증
- `GRM_SYSTEM.md` §5.3 및 변경 이력 갱신

### M1b — 이번 추가

- `findings_exporter.py` 추가
- 기존 Notion Intake snapshot 형태 + raw payload map을 입력으로 받는 오프라인 exporter 설계
- 샘플 fixture `tests/fixtures/findings_m1b_sample_export.json` dry-run으로 `raw_signals` 변환 검증
- invalid row/raw payload, missing raw, duplicate raw_signal_id는 report에 skip으로 남기고 전체 실행은 중단하지 않음
- 대량 백필 실행 금지, dry-run 산출물만 확인

### M1c — 이번 추가

- `findings_store.py` 추가
- feature flag 기본 off 상태의 SQLite append 배선
- `collect_intake.insert_items(..., findings_sqlite_path=...)`에서 Notion insert 성공 후에만 수집기 `IntakeItem.raw_payload` → `raw_signals` append
- `ENABLE_FINDINGS_SQLITE_APPEND=false` 기본, `GRM_FINDINGS_SQLITE_PATH=grm-findings.sqlite3` 경로
- dry-run은 SQLite 쓰기 없음
- duplicate raw_signal은 idempotent skip
- SQLite append 실패는 기존 Notion insert stats/handoff/Status 흐름을 바꾸지 않음
- `grm-findings.sqlite3`, `*.sqlite3-shm`, `*.sqlite3-wal`은 git 추적 제외

### M1d — 이번 추가

- `findings_extractors.py` 추가
- `findings_from_raw_signal(raw_signal)` 순수 함수로 483 Observation, MFDS GMP 지적 표, MFDS 지적 excerpt fallback, WL 위반 본문, WHO inspection excerpt에서 findings 변환 v0 추가
- FDA 483 `fda_483_observations`: Evidence A, `accepted`, `confidence=0.95`
- MFDS GMP `gmp_deficiencies`: Evidence A, `accepted`, `confidence=0.90`
- MFDS `attachment_deficiency_excerpt` fallback, FDA WL `wl_body_excerpt|wl_body_full`, WHO `whopir_excerpt`: Evidence B, `needs_review`, `confidence=0.72`
- finding 중복 텍스트는 raw_signal 단위에서 dedupe하고, `grm_findings.validate_finding`을 통과한 레코드만 반환
- `tests/test_findings_extractors.py` 추가
- DB/API/백필/대시보드 구현 금지 유지

### M1e — 이번 추가

- `findings_exporter.py`에 `include_findings` 옵션과 CLI `--include-findings` 추가
- `--include-findings` 사용 시 `schema_version=grm-findings-dry-run/v1`, `finding_schema_version=grm-finding/v1`, `taxonomy_version=grm-finding-taxonomy/v1`, `findings[]`, `report.findings_exported`, `report.raw_signals_without_findings` 산출
- 기존 M1b raw_signals-only dry-run 경로는 기본값으로 유지
- `findings_store.py`에 `append_raw_signal_with_findings()`와 `append_intake_item_with_findings_to_sqlite()` 추가
- raw_signal과 generated findings는 명시 호출 시 한 SQLite transaction으로 검증
- duplicate raw_signal/finding은 idempotent 처리, raw_signal/finding FK 불일치 등은 `partial`/`invalid`로 report
- `collect_intake` 자동 findings append 연결은 M1g feature flag 단계에서만 수행
- 내부/외부 백필, Supabase 적재, 대시보드 구현 금지 유지

### M1f — 이번 추가

- `findings_exporter.py` `--include-findings` report에 `coverage` 요약 추가
- coverage 필드: `raw_signals_total`, `raw_signals_with_findings`, `raw_signals_without_findings`, `findings_total`, `raw_signals_by_source`, `findings_by_source`, `findings_by_agency`, `findings_by_review_status`, `findings_by_evidence_level`, `findings_by_category_code`
- source coverage fixture `tests/fixtures/findings_m1f_source_coverage_export.json` 추가
- fixture 범위: FDA 483 Observation, MFDS GMP 지적 표, FDA WL 본문, WHO WHOPIR excerpt, WHO link-only gap
- `tests/test_findings_exporter.py` coverage 분포 회귀 추가
- Notion API 조회, SQLite 파일 생성, 내부/외부 백필, Supabase 적재, 대시보드 구현 금지 유지

### M1g — 이번 추가

- `collect_intake.RunConfig`에 `ENABLE_FINDINGS_SQLITE_FINDINGS_APPEND=false` 기본 off 플래그 추가
- `ENABLE_FINDINGS_SQLITE_APPEND=true` 단독은 기존 raw_signals-only append 유지
- 두 플래그가 모두 켜진 non-dry-run에서만 `insert_items(..., findings_sqlite_include_findings=True)`가 `append_intake_item_with_findings_to_sqlite()` 호출
- append는 Notion insert 성공 후에만 수행
- dry-run은 SQLite 쓰기 없음
- raw_signals/findings append 실패는 WARN-only이며 기존 Notion insert stats/handoff/Status 흐름을 바꾸지 않음
- `tests/test_findings_store.py`에 raw-only 호환, raw+findings append, findings append 실패 비차단 회귀 추가

### M1h — 이번 추가

- `findings_backfill.py` 추가
- 입력: `tests/fixtures/findings_m1h_backfill_manifest.json` 같은 manifest 또는 반복 `--input`
- 각 batch를 `findings_exporter.export_from_input(..., include_findings=True)`로 변환한 뒤 전역 dedupe
- 산출: `schema_version=grm-findings-internal-backfill-dry-run/v1`, `records[]`, `findings[]`, `duplicates`, `skipped_rows`, `report.coverage`, `report.preflight`
- `report.preflight`는 Notion/SQLite/Supabase write가 모두 `not_used`임을 명시
- blocking error: skipped row, raw/finding validation error, orphan finding
- review warning: duplicate raw_signal/finding, findings 미생성 raw_signal
- 샘플 manifest: M1b seed + M1f source coverage fixture를 묶어 input rows 7, unique raw_signals 6, unique findings 7, duplicate 1쌍, WHOPIR link-only gap 1건을 고정
- 절차 문서: `docs/specs/FIND1_M1_internal_backfill_dry_run_2026-07-08.md`
- `findings_internal_backfill_dry_run*.json` 로컬 산출물은 git 추적 제외

### M1i — 이번 추가

- `findings_backfill_sqlite.py` 추가
- 입력: M1h `--plan` JSON, 또는 manifest/반복 `--input`에서 plan을 메모리 생성
- SQLite `:memory:`에 schema 생성 후 data transaction 시작
- first pass: plan의 unique raw_signals/findings append
- replay pass: 같은 plan 재실행 시 duplicate skip 검증
- rollback 후 raw_signals/findings row count가 0으로 돌아오는지 검증
- 저장소 helper는 exporter dry-run 메타 필드(`export_source`) 등 schema 외 key를 SQLite insert에서 무시하도록 보강
- 샘플 manifest 기준 first pass raw_signals 6/findings 7, replay duplicate raw_signals 6/findings 7, rollback count 0/0 고정
- 절차 문서: `docs/specs/FIND1_M1_sqlite_backfill_dry_run_2026-07-08.md`
- `findings_sqlite_backfill_dry_run*.json` 로컬 산출물은 git 추적 제외

### M1j — 이번 추가

- `findings_backfill_apply.py` 추가
- 입력: M1h `--plan` JSON, 또는 manifest/반복 `--input`에서 plan을 메모리 생성
- 파일 write는 `--write-file`과 `--db-path`를 모두 명시한 경우에만 허용
- 적용 전 M1i `sqlite_transaction_dry_run()`을 다시 실행해 `ready_for_commit_review=true`를 확인
- first apply: 샘플 manifest 기준 raw_signals 6/findings 7 commit
- second apply: 같은 DB에 재적용 시 raw_signals 6/findings 7 duplicate skip으로 idempotent
- Notion API 조회, Supabase write, Status/handoff 변경, 운영 자동화 없음
- 절차 문서: `docs/specs/FIND1_M1_sqlite_file_write_boundary_2026-07-08.md`
- `findings_sqlite_backfill_apply*.json`, `grm-findings.sqlite3`, `*.sqlite3-shm`, `*.sqlite3-wal` 로컬 산출물은 git 추적 제외

### M1k — 이번 추가

- `findings_notion_export.py` 추가
- 입력: `NOTION_TOKEN`/`NOTION_DATABASE_ID` 또는 CLI `--notion-token`/`--database-id`
- 기본 동작: Notion Intake DB read-only query → `_intake_page_snapshot()` row 복원 → `fetch_intake_raw_payload()`로 page children raw JSON 복원
- 출력: `schema_version=grm-findings-notion-export/v1`, `rows[]`, `raw_by_page_id`, `raw_by_key`, `report.preflight`
- routine handoff page(`Source=GRM Handoff` 또는 `Type or Class=routine-handoff`)와 web-delta 운반 page(`web-delta`, `web-deep-delta`)는 기본 제외
- `--run-date-from`/`--run-date-to`로 7월 운영 백필 범위를 제한할 수 있음
- missing raw payload는 blocking error로 보고하고, M1h/M1i/M1j로 넘기기 전 보정 대상이 됨
- Notion API write, SQLite write, Supabase write, Status/handoff 변경 없음
- 절차 문서: `docs/specs/FIND1_M1_notion_export_backfill_runbook_2026-07-08.md`
- `findings_notion_export*.json` 로컬 산출물은 git 추적 제외
- 2026-07-08 운영 실행 결과: 공식 API export는 page 117건 조회, routine/web-delta 운반 page 5건 제외, signal row 112건 export, raw payload 112/112 복원(`raw_fetch_missing=0`).
- 같은 export 기반 M1h plan은 raw_signals 112/findings 24/blocking error 0, M1i transaction dry-run은 first pass 112/24 insert·replay 112/24 duplicate·rollback 0/0으로 통과했다.
- M1j file apply는 `grm-findings.sqlite3`에 raw_signals 112/findings 24를 commit했고 `report.ready_for_search_export=true`를 반환했다.
- 운영 인증값은 로컬 임시 환경변수로만 사용했고 문서/파일에 기록하지 않는다.

### M1 이후

- 정적 `findings.json` export, SQLite view 기반 검색 페이지 v0, Supabase 적재/서빙, 대시보드는 M1 완료 이후 M2/M3 작업으로 분리한다.
