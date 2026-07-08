# FIND-1 M3 Supabase 적재 준비 + `/findings/` 웹 검색 페이지 + 하드닝

> 날짜: 2026-07-08  
> 상태: M3a Postgres 스키마 계약 + 오프라인 로드 플랜 생성기 완료 · **Supabase 라이브 적용·재검증 완료(2026-07-08, raw_signals 112/findings 24)** · M3c `/findings/` 웹 검색 페이지 완료·라이브 배선 · M3d 하드닝 2건 완료
> 코드 정본: `findings_supabase.py`, `web/migrations/002_findings.sql`, `web/migrations/003_findings_public_read.sql`, `web/templates/findings.html`, `web/assets/findings.js`, `findings_extractors.py`(`findings_from_raw_signal_with_report`), `findings_backfill.py`/`findings_backfill_sqlite.py`(CLI exit code)  
> 테스트 정본: `tests/test_findings_supabase.py`(42), 웹 스위트(`web/tests/`, 80 OK — `findings.expected.html` 골든 포함), 기존 회귀 전량 무수정 통과

---

## 0. M3 게이트 — 단계별 범위와 미수행 명시

M3는 M2가 완료한 read-only 조회/정적 export/오프라인 뷰어 위에서 두 방향으로 갈라진다: **(A) Supabase(Postgres) 적재 준비**와 **(B) 그 데이터를 서빙하는 라이브 웹 검색 페이지**. 이 문서는 세 세션(M3a/M3c/M3d)의 정확한 범위와, 각 단계에서 **하지 않은 것**을 우선 명시한다.

- **M3a(완료)**: `findings_supabase.py`로 SQLite sidecar를 읽어 Postgres DDL + 데이터 INSERT + 검증 SQL을 담은 **오프라인 로드 플랜**을 만든다. 이 단계는 네트워크 호출도, Supabase 연결도, 실제 적재도 하지 않는다. `web/migrations/002_findings.sql`은 `postgres_schema_ddl()` 출력의 byte 단일 소스로 커밋됐을 뿐, 이 SQL이 실제 Supabase 프로젝트에 실행됐다는 뜻은 아니다.
- **M3a 실행(완료, 라이브 적재·검증까지 완료)**: 2026-07-08에 실제 운영 `grm-findings.sqlite3`(raw_signals 112/findings 24)로 로드 플랜을 산출해 원샷 SQL 파일(`grm-findings-supabase-apply-2026-07-08.sql`, DDL+데이터 15문+`003_findings_public_read.sql`까지 포함·454KB·`report.ready_for_apply=true`·`blocking_errors=0`)을 만들었다. **CC 세션의 auto-mode 권한 분류기가 공유 프로덕션 Supabase 프로젝트에 대한 CC 직접 write를 거부**했기 때문에(§3 참고), CC가 아니라 사용자가 Supabase Dashboard(`grm-reactions`) SQL Editor에서 이 파일을 1회 실행해 적용했다. CC는 이후 read-only로 재검증(`raw_signals=112`·`findings=24`·orphan `0`·`raw_sha256` 무결성 `112/112`·`findings` 공개 read 정책 1개·`raw_signals` 정책 0개)하고 security/performance advisors를 점검(M3 신규 테이블 관련 WARN/ERROR 없음, 의도된 INFO 2건만)해 M3a를 라이브까지 완결했다. 산출물 SQL 파일은 `.gitignore` 대상(`grm-findings-supabase-apply*.sql`)이라 git에는 없다.
- **M3c(완료)**: 위 적재 여부와 무관하게 독립적으로 진행 가능한 웹 레이어 — `/findings/` 정적 페이지 셸 + `findings.js` 런타임 PostgREST fetch + `web/migrations/003_findings_public_read.sql`(findings 테이블만 anon/authenticated SELECT 재허용). 렌더 골든 전체 재동결. **이 페이지는 002/003이 실제 Supabase에 적용되고 데이터가 적재돼 있어야 콘텐츠가 채워진다** — 그 전까지는 "검색 서비스 준비 중입니다" 셸만 보인다(오류 아님, §4 참고).
- **M3d(완료)**: M1/M2 코드에 대한 관측성/운영 하드닝 2건. 신규 기능이 아니라 기존 계약의 보강이다.
- M3 전체에 걸쳐 Notion write, 대시보드 구현, collect_intake → Supabase 직행 적재 자동화는 범위 밖이며 M4 이후로 이월한다(§6).

---

## 1. Postgres 스키마 계약 — SQLite 대비 차이표

`findings_supabase.postgres_schema_ddl()`이 `web/migrations/002_findings.sql`의 byte 단일 소스다(테스트로 고정). 두 테이블(`raw_signals`/`findings`)의 필수 필드·CHECK 제약은 SQLite DDL(`grm_findings.py`)과 **전부 동일하게** 이식했고, 아래 항목만 Postgres 표현 방식이 달라졌다.

| 항목 | SQLite (`grm_findings.py`) | Postgres (`findings_supabase.py`) | 이유 |
|---|---|---|---|
| `raw_json` / `row_json` | `TEXT` | `text`(그대로 유지, jsonb 아님) | canonical JSON의 **byte 그대로**를 보존해야 `raw_sha256` 재검증(원문 해시 대조)이 항상 가능하다. jsonb로 저장하면 Postgres가 키 순서·공백을 정규화해 원본 byte를 잃는다. |
| `inspector_names` / `cfr_refs` / `mfds_refs` | `TEXT`(JSON 배열 직렬화 문자열) | `jsonb not null default '[]'::jsonb` | 조회·질의 편의를 위해 구조화 강화. SQLite는 배열 타입이 없어 문자열 직렬화만 가능했던 제약을 Postgres에서 해소. |
| `UNIQUE(raw_signal_id, finding_text)` | 테이블 제약(UNIQUE) | `create unique index ... on public.findings (raw_signal_id, md5(finding_text))` | `finding_text`가 긴 원문 발췌를 담을 수 있어 btree 인덱스 행 크기 한계(~2704B)를 넘을 수 있다. `finding_text`를 직접 인덱싱하는 대신 `md5(finding_text)` 해시를 인덱싱해 동일 의미(문자열 동일 시 유일)를 유지하면서 크기 제약을 회피한다. |
| `ingested_at` | 없음(SQLite 스키마엔 미노출) | `timestamptz not null default now()` | Postgres 적재 시점을 추적하는 인프라 컬럼. `grm-finding/v1`/`grm-raw-signal/v1` 논리 스키마 계약에는 포함되지 않는 순수 운영 메타데이터 — validator(`grm_findings.validate_*`)가 검사하는 필드 목록에 없다. |
| RLS/권한 | 해당 없음(로컬 파일, OS 권한만) | `enable row level security` + `revoke all ... from anon, authenticated`(002) → `grant select ... to anon, authenticated` + `create policy findings_public_read`(003, findings만) | Postgres는 네트워크로 노출되는 공유 DB이므로 3단 구조로 방어한다: ① 002가 두 테이블 모두 RLS 활성화 + anon/authenticated 권한 전면 회수(service_role 전용) ② 003이 **findings 테이블만** SELECT grant + read policy를 되돌려 공개 검색을 가능하게 함 ③ `raw_signals`는 원본 보존층(비공개 계약)이라 003 이후에도 계속 전면 차단 상태를 유지한다. |
| CHECK 제약(`schema_version`/`taxonomy_version`/`category_code`/`evidence_level`/`extraction_method`/`review_status`/`confidence`/`raw_sha256` 길이) | 전부 존재 | **전부 동일하게 존재**(SQLite CHECK 표현을 Postgres CHECK 구문으로 1:1 이식) | 의미 동치 원칙 — 스키마 계약(`grm_findings.py`)의 게이트를 어느 저장소에서 읽어도 동일하게 강제한다. |
| INSERT 멱등성 | `INSERT OR IGNORE`(SQLite) | `insert ... on conflict (<pk>) do nothing`(배치당 10행) | 재실행 안전(re-run safe) 원칙을 Postgres 문법으로 동일하게 구현. |

`002_findings.sql`은 스키마만 만든다 — 이 단계에서 데이터 적재는 하지 않는다(§0). `003_findings_public_read.sql`은 002가 먼저 적용돼 있음을 전제하며, `raw_signals`에는 어떤 grant/policy도 추가하지 않는다(파일 내 주석으로 "실행 효과 없음"을 명시).

---

## 2. `grm-findings-supabase-load/v1` envelope 계약

`findings_supabase.build_supabase_load_plan(db_path)`가 만드는 최상위 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `schema_version` | string | 고정값 `grm-findings-supabase-load/v1` |
| `raw_signal_schema_version` / `finding_schema_version` / `taxonomy_version` | string | `grm_findings`의 스키마 버전 상수 재노출(검증용) |
| `migration_name` | string | 고정값 `findings_v1_raw_signals_findings` |
| `ddl_sql` | string | `postgres_schema_ddl()` 전체 텍스트(=`002_findings.sql` byte 동일) |
| `data_sql` | array[string] | `raw_signals` → `findings` 순서로 10행씩 배치한 `insert ... on conflict do nothing;` 문 목록 |
| `verification_sql` | array[string] | 6종 고정 검증 쿼리(아래 §3) |
| `counts` | object | `{raw_signals, findings, raw_signal_batches, finding_batches}` |
| `report` | object | `{mode: "supabase_load_plan", validation_errors, blocking_errors, ready_for_apply}` |

**읽기 경계**: `build_supabase_load_plan`은 `findings_views.open_findings_db_readonly`로만 SQLite에 연결한다(mode=ro, 파일 미존재 시 예외) — M2a read-only 계약을 그대로 재사용하며 SQLite에 쓰지 않는다.

**검증**: `raw_signals` 전건에 `grm_findings.validate_raw_signal()`, `findings` 전건(jsonb 3필드는 미리 `json.loads`로 역직렬화 후)에 `grm_findings.validate_finding()`을 통과시키고, 추가로 모든 `findings.raw_signal_id`가 로드된 `raw_signals` 집합에 존재하는지(orphan 가드)를 확인한다. 하나라도 실패하면 `blocking_errors > 0`이 되어 `ready_for_apply=false`가 된다 — M2c 뷰어의 `ready_for_viewer` 게이트와 동일한 설계.

**값 인코딩**: 텍스트는 `'`→`''` 이스케이프 + NUL 바이트 제거(`pg_quote_text`) 후 단일따옴표로 감싼다. `inspector_names`/`cfr_refs`/`mfds_refs`는 `json.dumps(..., sort_keys=True)` 후 `::jsonb` 캐스트(`pg_quote_jsonb`). `confidence`는 `repr(float(...))`로 숫자 리터럴화. 컬럼 순서는 `findings_store.RAW_SIGNAL_SQLITE_COLUMNS`/`FINDING_SQLITE_COLUMNS`를 그대로 재사용해 SQLite 저장소 계약과 컬럼 순서·존재를 동기화한다.

CLI: `python findings_supabase.py --db-path <sqlite경로> [--output <json경로>] [--pretty]`. 읽기 전용이며 네트워크 호출이 없다.

---

## 3. 적재 절차 runbook(원샷 SQL → 검증 6쿼리 → advisors)

M3a는 계약과 플랜 생성기만 만들고, 실제 Supabase 프로젝트에 대한 적용은 사람/컨트롤 타워의 별도 실행 단계로 분리했다. 2026-07-08 실행 기록:

1. **플랜 산출**: `python findings_supabase.py --db-path grm-findings.sqlite3 --output grm-findings-supabase-apply-2026-07-08.sql`(실제로는 `ddl_sql`+`data_sql`+`003_findings_public_read.sql`을 이어붙인 원샷 실행 가능 SQL 파일로 조립) — raw_signals 112건·findings 24건 기준 15개 데이터 문장·454KB, `report.blocking_errors=0`.
2. **적용 시도**: 대상 프로젝트 `grm-reactions`(기존 반응 계층과 동일 프로젝트, 신규 프로젝트 아님)에 Supabase MCP `apply_migration`/`execute_sql`로 직접 적용을 시도했으나, **CC 세션의 auto-mode 권한 분류기가 공유 프로덕션 Supabase 프로젝트에 대한 write 실행을 거부**했다(로컬 개발 스택이 아닌 공유 운영 프로젝트에 스키마 변경 SQL을 자동 실행하는 것은 게이트 대상). 이는 코드/스키마 결함이 아니라 세션 권한 경계다.
3. **인계 및 실행**: 원샷 SQL 파일을 사용자에게 전달했다(파일은 `.gitignore` 대상이라 git에는 없다). **사용자가 Supabase Dashboard(`grm-reactions`) SQL Editor에서 이 파일을 1회 실행해 적용을 완료했다** (2026-07-08).
4. **적용 후 검증(완료)**: CC가 `_verification_sql()`이 고정하는 6종 쿼리를 실제로 재실행해 확인 —
   - `raw_signals`/`findings` count → **112 / 24** (기대치와 일치)
   - `findings.schema_version` DISTINCT → **`grm-finding/v1`** 단일값
   - `findings.taxonomy_version` DISTINCT → **`grm-finding-taxonomy/v1`** 단일값
   - `raw_sha256` 무결성: 일치 행 수 **112 / 전체 112** — 원문 byte 손상 없음
   - orphan `findings` 카운트 → **0**
   - `findings` RLS 정책 `findings_public_read`(anon/authenticated SELECT) **1개** 존재, `raw_signals` 정책 **0개**(anon SELECT 불가 재확인)
5. **security advisors 점검(완료)**: `get_advisors(type="security"/"performance")` 재실행. M3 신규 테이블 관련은 `raw_signals`의 "RLS enabled, no policy"(INFO, 의도된 설계)와 신규 인덱스 2종의 "unused index"(INFO, 트래픽 미발생)뿐이며 **WARN/ERROR 없음**. 기존 프로젝트의 leaked-password-protection·`reaction` auth_rls_initplan 등은 M3 이전부터 있던 무관 이슈로 이번 범위에서 손대지 않았다.

---

## 4. 웹 `/findings/` 계약

`web/render.py`가 `findings.html`을 `findings/index.html`로 빌드한다(`nav_active="findings"`, sitemap에 `/findings/` 포함). 이 페이지는 브리프 아카이브와 달리 **라이브 데이터**(계속 누적되는 findings)라 빌드 시점에 카드를 고정할 수 없으므로, 서버는 로딩 상태만 렌더하는 정적 셸을 만들고 클라이언트에서 PostgREST를 직접 fetch한다.

**env-param 게이트**: `<div id="grm-findings-cfg" data-url="{{ supabase_url }}" data-key="{{ supabase_anon_key }}" hidden>`. `supabase_url`/`supabase_anon_key`는 render.py가 기존 `SUPABASE_URL`/`SUPABASE_ANON_KEY` 환경변수에서 채우는 **기존 반응 계층(S1)과 동일한 전역 값**이다(§7 참고, 신규 secret 없음). 값이 비어 있어도 `findings.html` 자체 출력 byte는 항상 동일(결정론) — `findings.js`가 런타임에 `data-url`/`data-key`가 빈 문자열이면 "검색 서비스 준비 중입니다"로 조용히 종료한다(오류 아님).

**PostgREST 쿼리 형태**: `findings.js`는 supabase-js를 쓰지 않고 REST 엔드포인트를 직접 `fetch`한다 — 인증이 필요 없는 anon SELECT뿐이라 SDK가 불필요하기 때문이다.

```
GET {supabase_url}/rest/v1/findings
  ?select=finding_id,source,agency,document_id,published_date,firm_name,
          category_code,category_label_ko,finding_text,finding_language,
          evidence_level,evidence_url,cfr_refs,mfds_refs,review_status,confidence
  &order=published_date.desc,finding_id.asc
  &limit=1000
Headers: apikey: {anon_key}, Authorization: Bearer {anon_key}
```

이 쿼리가 성공하려면 003 마이그레이션의 `findings_public_read` 정책(anon/authenticated SELECT)이 적용돼 있어야 한다 — 미적용 상태에서는 PostgREST가 RLS로 빈 결과 또는 403을 반환하고, `findings.js`는 fetch 실패를 "데이터를 불러오지 못했습니다" 에러 상태로 표시한다.

**XSS 규칙**: 렌더는 전부 `document.createElement`/`element.textContent`로만 하고 `innerHTML`에 데이터를 넣지 않는다 — findings는 원문(483/WL/실태조사)에서 자동 추출한 자유 텍스트라 검색 아카이브(`archive.js`, 렌더러가 이미 생성한 신뢰 데이터)와 다른 신뢰 경계를 갖는다. `evidence_url`은 `http://`/`https://`로 시작할 때만 `<a>` 앵커를 만들고(`safeUrl()`), 그 외는 링크를 렌더하지 않는다. 검색은 키워드 입력 150ms 디바운스(`finding_text`/`firm_name`/`document_id` 소문자 부분일치) + facet select 6종(`agency`/`category_code`/`source`/`evidence_level`/`review_status`/`published_month`, 옵션은 로드된 rows에서 클라이언트가 파생) + 초기화 버튼.

**AI 자동 추출 고지**: `#findings-notice` 섹션이 페이지 상단에 고정 노출되어(구현 미완이 아니라 항상 렌더) 원문 자동 추출·오분류 가능성과 원문 링크 대조 필요성을 명시한다.

**골든**: `web/tests/golden/findings.expected.html`을 신규 동결하고, `css_ver`/`findingsjs_ver` 등 기존 콘텐츠-해시 캐시버스팅 규약을 그대로 따른다. `base.html` nav에 "지적사항" 링크, footer "둘러보기"에도 동일 링크 추가. 웹 테스트 80건 OK(재동결 포함).

---

## 5. 하드닝 2건 (M3d)

### ① extractor invalid-drop 관측 카운터

`findings_extractors.findings_from_raw_signal_with_report(raw_signal)`을 신설했다(기존 `findings_from_raw_signal()`은 내부적으로 이 함수를 호출하도록 바뀌었을 뿐 시그니처·동작 불변). 반환값은 `(findings, report)`이며 `report`는 `{extracted, kept, dropped_invalid, dropped_duplicate_text, invalid_errors}`를 담아 "추출할 게 없음"과 "추출은 됐지만 invalid/중복으로 버려짐"을 구분한다.

`findings_exporter.py`의 coverage 요약에 `extraction_dropped_invalid`/`extraction_dropped_duplicate_text` 두 카운터를, report에 `extraction_drop_details`(additive, 어떤 raw_signal에서 무엇이 왜 드롭됐는지)를 추가했다. 기존 exporter 출력 필드는 전부 그대로 유지된다(순수 additive) — M2b `grm-findings-search/v1`나 M1h/M1i/M1j 산출물 계약을 깨지 않는다.

### ② M1h/M1i CLI exit code

`findings_backfill.py`(M1h)와 `findings_backfill_sqlite.py`(M1i)의 `main()`이 명시적 exit code를 반환하도록 바뀌었다:

- `0` — 클린(입출력 성공, blocking error 없음)
- `2` — 입력/IO 오류(파일 없음, JSON 파싱 실패 등)
- `3` — blocking error 존재. M1i는 여기에 **rollback 미검증**(`rollback_verified=false`)도 포함한다 — `blocking_errors > 0 or not rollback_verified`일 때 3을 반환한다.

이 exit code는 CI/운영 스크립트가 dry-run 결과를 `$?`로 즉시 분기할 수 있게 하는 관측성 보강이며, 두 CLI의 JSON 출력 구조·필드는 변경하지 않았다.

두 하드닝 모두 기존 테스트를 무수정으로 통과시켰다(전체 스위트: 루트 1243 passed + 842 subtests, 웹 80 OK).

---

## 6. 잔여 / 이월 목록

- **ⓐ 원샷 SQL 실행 + 검증** — ✅ **완료(2026-07-08)**: 사용자가 Supabase SQL Editor에서 `grm-findings-supabase-apply-2026-07-08.sql`을 1회 실행했고, CC가 §3의 검증 6쿼리 + security advisors를 재확인했다(전부 기대치 일치, WARN/ERROR 없음).
- **ⓑ 배포 env 확인**: Cloudflare 배포 빌드의 `SUPABASE_URL`/`SUPABASE_ANON_KEY`가 이미 반응 계층(S1)용으로 등록돼 있어 `/findings/` 활성화에 추가 secret 등록이 필요 없을 가능성이 높지만, 실제로 확인된 사실은 아니다 — 라이브에서 "준비 중" 문구가 사라지는지 배포 후 재확인 필요.
- **ⓒ PR 머지**: M1+M2는 PR#135(OPEN), M3(M3a/M3c/M3d)는 PR#136(OPEN, `feat/findings-m1-schema-2026-07-08` 위에 스택 — #135 머지 후 base를 main으로 전환해 머지). 데이터가 이미 라이브에 적재됐으므로 PR 머지는 코드/문서 반영일 뿐 재적재를 의미하지 않는다.
- **ⓓ M4 후보**: 대시보드 고도화(파셋 검색·히트맵·업체 이력), 분류기(v1 substring 매칭) 키워드 정제(`other_quality_system`/`needs_review` 비중이 높은 카테고리 우선 — taxonomy 버전 이벤트 필요, SQLite/Postgres CHECK 제약 동반 마이그레이션), `collect_intake` → Supabase 직행 적재 자동화(현재는 SQLite sidecar 경유 + 수동 로드 플랜 생성 + 사람 적용).
