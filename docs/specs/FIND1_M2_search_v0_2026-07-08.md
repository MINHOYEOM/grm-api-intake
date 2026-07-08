# FIND-1 M2 검색 v0 — read-only 조회 계층 + 정적 export + 오프라인 뷰어

> 날짜: 2026-07-08  
> 상태: M2a read-only SQLite 조회 계층 + M2b grm-findings-search/v1 정적 export + M2c grm-findings-search-page/v1 오프라인 HTML 뷰어 빌더 + M2d 운영 실행(7월 데이터) 완료  
> 코드 정본: `findings_views.py`, `findings_search_export.py`, `findings_search_page.py`  
> 테스트 정본: `tests/test_findings_views.py`, `tests/test_findings_search_export.py`, `tests/test_findings_search_page.py`

---

## 0. M2 게이트

M2는 M1이 동결·commit한 `grm-findings.sqlite3`(`raw_signals`/`findings` 스키마 계약, `docs/specs/FIND1_M1_schema_contract_2026-07-08.md`) 위에서만 진행한다. M2의 범위는 **읽기 전용 조회 계층 + 정적 검색 export + 오프라인 단일 파일 뷰어**까지다. Notion/Supabase write, 웹 발행 파이프라인 접촉, 대시보드 구현은 M2 범위 밖이며 M3 이후로 분리한다.

M2a는 이 SQLite 파일 위에 **read-only 조회 계층**만 추가한다. `findings_views.py`는 SQLite `mode=ro` URI로만 연결하고, 파일이 없으면 생성하지 않고 예외를 낸다. 파라미터 바인딩 필터, 결정론 정렬, facet 집계, blob-free raw_signal 요약, db 요약을 제공하지만 SQLite/Notion/Supabase에 아무것도 쓰지 않는다.

M2b는 M2a 위에 **정적 검색 export**만 추가한다. `findings_search_export.py`는 read-only 연결로 SQLite 전체를 읽어 `grm-findings-search/v1` JSON envelope 하나를 만든다. records는 findings 전건에 raw_signal 요약을 조인한 것이고, coverage는 M1h coverage 요약 형태를 재사용하며, report는 스키마 검증 결과와 `ready_for_viewer` 게이트를 담는다. Notion 조회·SQLite write·Supabase 적재는 하지 않는다.

M2c는 M2b envelope 위에 **오프라인 단일 파일 HTML 검색 뷰어 빌더**만 추가한다. `findings_search_page.py`는 이미 메모리에 있는(또는 CLI로 디스크에서 읽은) export dict 하나만 입력으로 받아 결정론 HTML 문자열을 만든다. 네트워크 호출, SQLite 접근, Notion/Supabase 접근이 전혀 없다.

M2d는 M2a/b/c를 실제 운영 `grm-findings.sqlite3`(M1k 7월 백필 산출물)에 적용해 **로컬 전용 산출물**을 만든 실행 기록이다. 산출물은 두 파일 모두 `.gitignore` 대상이며, 이 실행으로 Notion/Supabase write나 웹 발행 파이프라인 변경은 없었다.

---

## 1. `grm-findings-search/v1` envelope 필드 계약

`findings_search_export.build_search_export(db_path)`가 만드는 최상위 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `schema_version` | string | 고정값 `grm-findings-search/v1` |
| `raw_signal_schema_version` | string | `grm_findings.RAW_SIGNAL_SCHEMA_VERSION` (`grm-raw-signal/v1`) |
| `finding_schema_version` | string | `grm_findings.FINDING_SCHEMA_VERSION` (`grm-finding/v1`) |
| `taxonomy_version` | string | `grm_findings.TAXONOMY_VERSION` (`grm-finding-taxonomy/v1`) |
| `source_db` | object | `{file_name, raw_signals, findings}` — `file_name`은 파일명만(절대경로 미노출), `raw_signals`/`findings`는 `db_summary()` row count |
| `records` | array | `findings` 전건(결정론 정렬: `published_date` DESC, `finding_id` ASC) + `raw_signal` 서브필드 |
| `facets` | object | `agency`/`category_code`/`source`/`evidence_level`/`review_status`/`published_month` 6개 키, 각 값→건수 dict(정렬됨) |
| `coverage` | object | M1h/M1f coverage 요약 형태 재사용(`_coverage_summary`) |
| `report` | object | `{mode, records, validation_errors, blocking_errors, ready_for_viewer}` |

`records[]` 원소는 `findings` 테이블의 모든 필수/보조 필드(`finding_id`·`raw_signal_id`·`source`·`agency`·`document_type`·`document_id`·`published_date`·`firm_name`·`category_code`·`finding_text`·`evidence_level`·`evidence_url`·`extraction_method`·`review_status`·`entity_id`·`site_name`·`site_country`·`product_family`·`modality`·`category_label_ko`·`finding_language`·`inspector_names`·`cfr_refs`·`mfds_refs`·`confidence`)에 더해 `raw_signal` 키를 갖는다. `raw_signal`은 `findings_views.raw_signal_summary()`가 반환하는 blob-free 서브셋(`title`·`source`·`source_kind`·`published_date`·`collected_at`·`source_url`·`official_url`·`firm_name`·`site_country`·`extraction_status`)이며, 해당 `raw_signal_id`가 없으면 `null`이다.

`report.validation_errors`는 각 record를 `raw_signal` 키를 뺀 상태로 `grm_findings.validate_finding()`에 통과시켜 얻은 결과다. `blocking_errors`는 `validation_errors` 길이이며, `ready_for_viewer`는 `blocking_errors == 0`일 때만 `true`다. M2c 뷰어 빌더는 이 두 필드를 게이트로 사용한다.

`coverage`는 M2b가 새로 설계하지 않고 `findings_exporter._coverage_summary()`를 재사용한다 — `raw_signals_total`·`raw_signals_with_findings`·`raw_signals_without_findings`·`findings_total`·`raw_signals_by_source`·`findings_by_source`·`findings_by_agency`·`findings_by_review_status`·`findings_by_evidence_level`·`findings_by_category_code`.

CLI: `python findings_search_export.py --db-path <sqlite경로> [--output <json경로>] [--pretty]`. 읽기 전용이며 `--output` 생략 시 stdout에 출력한다.

---

## 2. `grm-findings-search-page/v1` 뷰어 계약

`findings_search_page.build_search_page(export)`는 M2b envelope 하나를 받아 단일 HTML 문자열을 반환한다.

**입력 게이트**: `export.schema_version`이 정확히 `grm-findings-search/v1`이 아니면 `ValueError`. `export.report.ready_for_viewer`가 참이 아니면 `ValueError`(blocking validation error가 있는 envelope는 렌더링 자체를 거부한다).

**결정론**: 같은 입력 dict는 항상 byte 동일한 HTML을 만든다. `build_search_page` 내부에 `datetime.now()`, `random`, 환경변수, 파일시스템/타임존 의존이 없다. `records`는 `json.dumps(..., sort_keys=True)`로 직렬화하고, facet 옵션은 `sorted(facet_counts.keys())`로 정렬한다.

**임베드 이스케이프**: findings 레코드 JSON을 `<script type="application/json">` 안에 넣기 전 `</`를 `<\/`로 치환해 조기 `</script>` 종료를 막는다. select `<option>`의 `value`/텍스트, 헤더의 파일명·taxonomy_version은 `html.escape()`(value는 `quote=True`)를 거친다.

**XSS-safe 렌더**: 클라이언트 JS(`_APP_JS`)는 `innerHTML`을 쓰지 않는다. 카드 렌더는 `document.createElement`/`element.textContent`만 사용해 사용자/원문 유래 텍스트(`finding_text`·`firm_name`·`category_label_ko`·`cfr_refs`·`mfds_refs` 등)가 항상 텍스트 노드로만 삽입된다. `evidence_url`은 `http://` 또는 `https://`로 시작할 때만 `<a href>` 앵커를 만들고, 그 외(빈 값·`javascript:` 등 비-http(s) 스킴)는 링크 대신 평문 라벨만 렌더한다.

**검색/필터**: 키워드 검색 input은 150ms 디바운스(`setTimeout`/`clearTimeout`) 후 `finding_text`·`firm_name`·`document_id`를 소문자 부분일치로 대조한다. facet select는 6종(`agency`·`category_code`·`source`·`evidence_level`·`review_status`·`published_month`) — 옵션과 건수는 빌드 시점에 Python이 `export.facets`로부터 생성하고(`_build_filter_field`), `published_month`는 `published_date`의 앞 7자로 클라이언트에서 파생한다. 초기화 버튼은 검색어와 모든 select를 비우고 재렌더한다.

**오프라인/현지화 제약**: `lang="ko"`, 시스템 폰트 스택(`-apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif`)만 사용, 외부 CSS/폰트/스크립트/이미지 요청 0건. 헤더에 AI 자동 추출 고지 문구를 고정 포함한다.

CLI: `python findings_search_page.py --input <search export json> --output <html경로>`.

---

## 3. read-only 경계 (M2a)

`findings_views.open_findings_db_readonly(db_path)`:

- `Path(db_path).is_file()`이 아니면 `ValueError`(파일 생성 없음).
- 경로를 `urllib.parse.quote(..., safe="/:")`로 percent-encoding한 뒤 `file:{quoted}?mode=ro` URI로 `sqlite3.connect(uri, uri=True)` — 공백이 들어간 디렉터리(`Global Regulatory Sweep` 등)도 안전하게 연다.
- 연결 직후 `raw_signals`/`findings` 두 테이블 존재를 `sqlite_master`로 검증하고, 누락 시 연결을 닫고 `ValueError`.

조회 함수는 전부 파라미터 바인딩이다: `agency`/`category_code`/`source`/`review_status`/`evidence_level`는 `IN (?, ...)`, `firm_contains`/`text_contains`는 `LOWER(...) LIKE LOWER(?) ESCAPE '\\'`(`\`·`%`·`_` 이스케이프 후 부분일치), `date_from`/`date_to`는 `published_date` 범위 비교. 정렬은 항상 `published_date DESC, finding_id ASC`로 결정론이다.

`facet_counts()`는 같은 필터를 적용한 뒤 5개 컬럼(`agency`/`category_code`/`source`/`evidence_level`/`review_status`) + `published_month`(`substr(published_date, 1, 7)`)별 건수를 집계한다. `raw_signal_summary()`는 blob 컬럼(`raw_json`/`row_json`) 없이 10개 컬럼만 반환한다. `db_summary()`는 row count와 findings의 `schema_version`/`taxonomy_version` DISTINCT 값을 검증용으로 노출한다.

M2 전체(M2a/M2b/M2c)에 걸쳐 SQLite/Notion/Supabase에 대한 write 코드 경로는 존재하지 않는다.

---

## 4. 운영 실행 결과 (M2d, 2026-07-08)

M1k 운영 백필로 commit된 `grm-findings.sqlite3`(raw_signals 112 / findings 24)를 입력으로 M2b/M2c를 실행했다.

- `findings_search_export.py --db-path grm-findings.sqlite3 --output findings_search_2026_07.json`:
  - `records`: 24건
  - `report.blocking_errors`: 0, `report.ready_for_viewer`: `true`
  - `coverage`: `raw_signals_total` 112, `raw_signals_with_findings` 8, `findings_total` 24
  - `facets.agency`: `FDA` 23, `MFDS` 1
- `findings_search_page.py --input findings_search_2026_07.json --output grm-findings-search.html`:
  - 24건 finding 레코드가 `<script type="application/json" id="findings-data">`에 임베드됨을 확인

두 산출물(`findings_search_2026_07.json`, `grm-findings-search.html`) 모두 `.gitignore`(`findings_search_*.json`, `grm-findings-search*.html`) 대상 로컬 전용이며, 이 실행으로 Notion write, Supabase write, 웹 발행 파이프라인 변경은 없었다.

---

## 5. 세션 분할

### M2a — read-only SQLite 조회 계층

- `findings_views.py` 추가: `open_findings_db_readonly`·`query_findings`·`facet_counts`·`raw_signal_summary`·`db_summary`
- `tests/test_findings_views.py` 추가(21개 회귀)
- SQLite/Notion/Supabase write 없음

### M2b — grm-findings-search/v1 정적 export

- `findings_search_export.py` 추가: `build_search_export()` + CLI
- `findings_exporter._coverage_summary()` 재사용, `grm_findings.validate_finding()`으로 record별 blocking validation 확인
- `tests/test_findings_search_export.py` 추가(10개 회귀)
- Notion 조회·SQLite write·Supabase 적재 없음

### M2c — grm-findings-search-page/v1 오프라인 뷰어

- `findings_search_page.py` 추가: `build_search_page()` + CLI
- 결정론 HTML 빌드(`</`→`<\/` 이스케이프, `html.escape`, textContent/createElement 렌더, evidence_url http(s) 화이트리스트), 키워드 검색(150ms 디바운스) + facet select 6종 + 초기화 버튼
- `tests/test_findings_search_page.py` 추가(12개 회귀)
- 네트워크·SQLite·Notion·Supabase 접근 없음

### M2d — 운영 실행(7월 데이터)

- 실제 `grm-findings.sqlite3`(raw_signals 112/findings 24)에 M2b/M2c 적용
- `findings_search_2026_07.json`(records 24·blocking 0·ready_for_viewer true) → `grm-findings-search.html`(24건 임베드 확인) 생성
- 두 산출물 모두 `.gitignore` 대상, git 미추적
- Notion/Supabase write 없음, web/ 발행 파이프라인 미접촉

### M2 이후 (M3 이월)

- 웹 통합(정적 검색 뷰어를 `grm-solutions.com` 웹 레이어와 연결할지, sidecar 데이터의 클라우드 거처를 먼저 확정)
- Supabase 적재(PostgREST read-only API 서빙)
- 대시보드(파셋 검색·히트맵·업체 이력)
- 분류기(v1 substring 매칭) 키워드 정제 — `other_quality_system`/`needs_review` 비중이 높은 카테고리 우선
- invalid-drop 카운터(현재 M2 report는 blocking validation error만 세고, 조회 단계에서 조용히 걸러지는 항목에 대한 별도 카운터는 없음)
- CI에서 findings sidecar(`grm-findings.sqlite3`)의 거처(아티팩트 보존/재생성 방식) 결정
