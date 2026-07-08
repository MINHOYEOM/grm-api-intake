# FIND-1 M6 — findings 품질 고도화(국문 병기 + 카테고리 라벨)

> 날짜: 2026-07-09
> 상태: M6a 코드(`grm_findings.py` 국문 필드+`findings_translation_migrate_sqlite.py`) 완료·테스트 green · M6b 코드(`findings_translate.py` export/apply 도구) 완료·테스트 green · M6c 운영 실행(24건 번역) 로컬 sidecar 적용 완료·**라이브 통합 SQL 실행은 사용자 SQL Editor 대기** · M6d 코드(`/findings/` 국문 우선 UI+카테고리 라벨+LEGACY_FIELDS 폴백) 완료·골든 재동결 완료
> 코드 정본: `grm_findings.py`(`TRANSLATION_METHODS`/`_validate_translation_fields`/`finding_from_raw_signal`), `findings_translation_migrate_sqlite.py`, `findings_translate.py`, `web/migrations/005_findings_translation_columns.sql`, `web/assets/findings.js`, `web/templates/findings.html`, `findings_search_page.py`(M2 오프라인 뷰어 정합)
> 테스트 정본: `tests/test_grm_findings.py`(M6a 확장)·`tests/test_findings_store.py`/`tests/test_findings_supabase.py`/`tests/test_findings_supabase_append.py`/`tests/test_findings_search_export.py`(M6a 계층 전파 회귀)·`tests/test_findings_translation_migrate.py`(M6a 신규) — M6a 신규/확장 합계 40개, 전체 1333 passed+842 subtests. `tests/test_findings_translate.py`(M6b 신규 30개) — 전체 1363 passed+842 subtests. `tests/test_findings_search_page.py`/`web/tests/test_render.py`(M6d 확장) — 웹 87·루트 1370 passed+842 subtests.
> 커밋: `9baaf28`(M6a), `9329c18`(M6b), `3a3cdc3`(M6c/M6d), `8177bae`(gitignore 보강)

---

## 0. M6 게이트 — 하는 것 / 안 하는 것

M3(Supabase 서빙)~M5(taxonomy v2)를 거치며 라이브 `findings` 24건이 공개 `/findings/` 페이지에서 조회 가능해졌지만, 실제 사용자 피드백은 두 가지 가독성 결함을 지적했다 — ① 지적사항 본문(`finding_text`)이 영문 원문 그대로 벽 텍스트로 노출되어 있고, ② 카테고리 필터 드롭다운이 `documentation_records` 같은 snake_case 코드를 그대로 노출한다. M6은 이 두 결함을 스키마·도구·운영·UI 네 층으로 나눠 해소한다.

- **하는 것(M6a):** `grm_findings.py`의 `findings` 레코드에 `finding_text_ko`/`translation_method` **optional** 필드 쌍을 additive로 추가한다. `schema_version=grm-finding/v1`은 그대로 유지하고(신규 스키마 버전 이벤트 아님), `FINDING_REQUIRED_FIELDS`는 무변경이며, 두 필드 모두 `finding_id` 안정 해시 계산에 포함되지 않는다(§1). SQLite/Postgres DDL에 컬럼+CHECK를 추가하고, 라이브용 멱등 `ALTER`(`005_findings_translation_columns.sql`)와 로컬 sidecar용 가드 마이그레이터(`findings_translation_migrate_sqlite.py`)를 만든다.
- **하는 것(M6b):** 번역 자체는 이 도구가 생성하지 않는다 — `findings_translate.py`는 미번역 항목을 결정론적으로 뽑아내는 `--export`와, LLM/사람이 채운 번역안을 전건 검증 후 all-or-nothing으로 반영하는 `--apply`만 제공하는 순수 파이프라인 도구다(§2).
- **하는 것(M6c, 운영 실행):** M6b 도구로 라이브 24건 전량을 국문 번역하고 로컬 sidecar에 적용했다(§3). 라이브 Supabase 반영은 통합 원샷 SQL을 사람이 SQL Editor에서 1회 실행하는 것으로 위임한다.
- **하는 것(M6d):** `/findings/` 웹 페이지(및 M2 오프라인 뷰어 `findings_search_page.py`)가 `finding_text_ko`가 있으면 국문을 본문으로, 영문 원문은 `<details>` 접기로 낮춰 보여주고, 카테고리 드롭다운을 `grm_findings.FINDING_TAXONOMY` 20종 code/label_ko/label_en과 동기화된 하드코딩 맵으로 "{국문} · {영문}" 표기한다(§4). 라이브 DB에 005가 아직 적용되지 않은 상태에서도 페이지가 깨지지 않도록 1회 폴백 재시도를 배선한다.
- **하지 않는 것:** taxonomy 카테고리 신설·삭제·이름 변경(M5의 20개 code/label/순서 완전 불변, M6은 그 라벨을 UI에 노출하는 방식만 바꾼다), `finding_id`/`finding_text`(영문 원문) 자체의 변경(원문은 번역 근거로서 영구 verbatim 보존, §1·§2 양쪽에서 이중으로 가드), 신규 findings에 대한 번역 자동화(수집 파이프라인에 번역 호출을 배선하지 않음 — 여전히 사람/LLM이 주기적으로 `--export`→번역→`--apply`를 실행하는 반자동 절차, §6), 라이브 Supabase에 대한 005/번역 UPDATE SQL 실행(CC 세션 권한 게이트로 사용자에게 위임, §5), M1~M5 코드/스펙 문서 수정(불가침).

---

## 1. 스키마 확장 계약

### 1.1 필드 정의

| 필드 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `finding_text_ko` | text | `''` | `finding_text`(영문 원문)의 국문 해석. 빈 문자열=미번역 |
| `translation_method` | text | `''` | 번역 생성 방식. `TRANSLATION_METHODS = ('', 'llm_assisted', 'manual')` |

두 필드 모두 `grm_findings.finding_from_raw_signal()` 키워드 인자로 추가됐고(기본값 `""`), `schema_version`은 여전히 `grm-finding/v1`이다 — taxonomy v2(M5)처럼 별도 버전 이벤트를 발급하지 않는다. `finding_id`는 여전히 `(raw_signal_id, finding_text)` 기반 안정 해시만 사용하므로(§ M1a 계약 불변), 번역 필드를 나중에 채우거나 갱신해도 `finding_id`는 바뀌지 않는다 — 이는 M6b `--apply`가 `finding_id`로 행을 특정해 UPDATE할 수 있는 전제이기도 하다.

### 1.2 쌍 규칙 validator 매트릭스

`grm_findings._validate_translation_fields()`는 레코드에 두 키가 전혀 없으면(=M6a 이전 레코드) 통과시키고, 하나라도 있으면 아래를 강제한다.

| 케이스 | 판정 |
|---|---|
| 두 키 모두 없음(pre-M6a 레코드) | 통과(no-op) |
| `translation_method`가 `TRANSLATION_METHODS` 밖의 값 | 오류 |
| `finding_text_ko` 비어있지 않은데 `translation_method`가 빈 문자열 | 오류(짝 없는 번역 금지) |
| `translation_method`가 비어있지 않은데 `finding_text_ko`가 빈 문자열 | 오류(짝 없는 방식 태그 금지) |
| 둘 다 빈 문자열, 또는 둘 다 유효한 비어있지 않은 값 | 통과 |

### 1.3 DDL 전파 — 3계층

| 계층 | 위치 | 내용 |
|---|---|---|
| SQLite DDL | `grm_findings.sqlite_schema_ddl()` | `findings.finding_text_ko TEXT NOT NULL DEFAULT ''`, `findings.translation_method TEXT NOT NULL DEFAULT '' CHECK (translation_method IN ('', 'llm_assisted', 'manual'))` |
| Postgres DDL | `findings_supabase.postgres_schema_ddl()` → `web/migrations/002_findings.sql`(byte 단일 소스, fresh-install 정본이라 재생성) | SQLite와 동일 컬럼/CHECK, `check` 소문자 표기(Postgres 관례) |
| 라이브 ALTER | `web/migrations/005_findings_translation_columns.sql` | `add column if not exists`(컬럼 2개) + `drop constraint if exists` 후 `add constraint findings_translation_method_check`. **DO 블록 불필요**(M5의 004와 달리 새 컬럼 추가라 기존 행과 충돌할 제약이 없음 — 컬럼 자체가 없으므로 `if not exists`만으로 멱등) |
| 저장소 컬럼 목록 | `findings_store.FINDING_SQLITE_COLUMNS` | 튜플 말미에 `finding_text_ko`, `translation_method` 추가(append 시 INSERT 컬럼 목록에 포함) |

### 1.4 로컬 sidecar 마이그레이터 — `findings_translation_migrate_sqlite.py`

M5b 분류기 마이그레이터(`findings_taxonomy_migrate_sqlite.py`)는 CHECK 제약을 바꿀 수 없어 파일 전체를 재구축했지만, M6a는 컬럼 추가뿐이라 SQLite `ALTER TABLE ... ADD COLUMN`(컬럼 레벨 `NOT NULL DEFAULT`+단일 컬럼 `CHECK`)로 충분하다. 그래서 이 마이그레이터는 재구축 대신 **제자리 ALTER**를 쓴다.

- **dry-run(기본):** 실제 `ALTER TABLE ADD COLUMN` 문 2개(+CHECK)를 명시 트랜잭션 안에서 실행한 뒤 **롤백**한다 — `PRAGMA table_info` 확인만으로 끝내지 않고 실제 리허설을 수행해 `verified` 플래그가 진짜 실행 가능성을 반영하게 한다. 파일은 리허설 후 byte-identical.
- **`--write-file`:** 먼저 `{db_path}.bak-v2` 백업(기존재 시 즉시 에러 — 재실행 시 기존 백업을 덮어쓰지 않음), 이어서 같은 ALTER 문을 커밋. 기존 행은 새 컬럼의 기본값(`''`)만 얻고 어떤 번역도 채우지 않는다(이 마이그레이터는 스키마 변경 전용, 번역 데이터는 M6b/M6c 몫).
- **exit code:** `0`=검증 통과(dry-run 또는 write 성공), `2`=`OSError`/`ValueError`/`sqlite3.Error`(백업 기존재 포함), `3`=검증 실패.
- 로컬 실행 기록: dry-run 통과 확인 후 `--write-file` 적용 완료(`grm-findings.sqlite3.bak-v2` 잔존, `.gitignore` 대상).

---

## 2. 번역 도구 계약 — `findings_translate.py`

이 도구는 번역을 생성하지 않는다. LLM/사람 세션이 오프라인에서 plan JSON을 채우고, 이 도구는 그 앞뒤(추출·검증·반영)만 결정론적으로 수행한다.

### 2.1 `--export` — 미번역 추출(read-only)

`finding_text_ko == ''`인 행만 `finding_id` 오름차순 등 결정론 정렬로 뽑아 `grm-findings-translation-plan/v1` JSON을 만든다. 컬럼은 번역자가 맥락 판단에 필요한 최소 집합(`finding_id`·`source`·`agency`·`category_code`·`category_label_ko`·`published_date`·`firm_name`·`finding_text`)이다. DB에 쓰기 없음.

### 2.2 `--apply` — 전건 검증 all-or-nothing

plan JSON(`schema_version=grm-findings-translation-plan/v1`)의 `items[]`를 라이브 DB 스냅샷과 대조해 검증한다. **한 항목이라도 실패하면 아무것도 쓰지 않는다**(sidecar도, `--sql-output`도) — 반환 report의 `ready=false`.

| 규칙 | 내용 |
|---|---|
| 원문 byte 대조 | `item.finding_text`가 DB의 `finding_text`와 정확히 일치해야 함(불일치=원문이 도중에 변형됐다는 신호) |
| `finding_id` 실존 | DB에 없는 `finding_id`는 오류 |
| 한글 포함 | `finding_text_ko`가 공백 제거 후 비어있으면 오류; 비어있지 않으면 정규식 `[가-힣]`로 한글 문자 최소 1자 필요 |
| method 화이트리스트 | `translation_method`는 `TRANSLATION_METHODS`에서 `''`을 제외한 `('llm_assisted', 'manual')` 중 하나 |
| 번역≠원문 금지 | `finding_text_ko == finding_text`면 오류(번역 안 하고 원문을 그대로 복사하는 것 방지) |
| plan 내 중복 `finding_id` | 오류 |

검증을 통과한 항목 중, DB에 이미 비어있지 않은 `finding_text_ko`가 있는 행은 `--overwrite` 없이는 **skip**(재번역은 opt-in). `--write-file` 없이는 sidecar를 건드리지 않는 dry-run이지만, report는 실제 apply와 동일한 `validated`/`updated`/`skipped_already_translated` 카운트를 반환하고, `--sql-output`이 지정돼 있으면 dry-run에서도 SQL 파일은 써진다(사이드카를 건드리지 않으므로).

**TOCTOU 방지:** 실제 UPDATE는 `UPDATE findings SET finding_text_ko=?, translation_method=? WHERE finding_id=? AND finding_text=?` — `finding_text`를 WHERE 절에 다시 넣어, plan 검증 이후 실행 사이에 원문이 바뀌었다면 `cursor.rowcount != 1`로 즉시 감지해 트랜잭션 전체를 롤백한다.

### 2.3 `--sql-output` — 라이브 Postgres UPDATE 원샷

`--apply`와 함께 지정하면 `to_update` 항목들로 라이브 `public.findings`에 적용할 `UPDATE ... WHERE finding_id = ... AND finding_text = ...` 문 묶음을 SQL 파일로 만든다(`findings_supabase.pg_quote_text` 재사용). WHERE 절이 `finding_id`+원문을 함께 고정하므로 **재실행해도 멱등**(이미 적용된 뒤 다시 실행하면 각 문장이 0행 매치=안전한 no-op).

### 2.4 CLI·exit code

```
python findings_translate.py --db-path <sidecar> --export [--output plan.json] [--pretty]
python findings_translate.py --db-path <sidecar> --apply <filled.json> [--write-file] [--overwrite] [--sql-output out.sql] [--output report.json]
```

`--export`/`--apply`는 상호 배타·둘 중 하나 필수. exit `0`=성공(export 완료, 또는 apply `ready=true`), `2`=usage/IO/JSON 파싱 오류, `3`=`--apply` 검증 실패(`ready=false`).

---

## 3. 번역 운영 기록(2026-07-09)

- **범위:** 라이브 `findings` 24건 전량(`finding_text_ko`가 비어있던 모든 행).
- **생성 주체:** LLM(Sonnet) 초안 + 컨트롤 타워(사람) 샘플 검수.
- **용어 원칙:** QA 보고체를 기본으로 하고, 규제 전문용어는 국문 번역 뒤 괄호로 원어를 병기한다(예: `critical area` → "청정구역(critical area)", `aseptic` → "무균공정", `misbranded` → "허위표시(misbranded)"). 법조항 번호·CFR 인용 등은 원문 그대로 유지한다(번역하지 않음 — 법적 대조 가능성 보존).
- **적용 방식:** `--export`로 24건 plan 추출 → LLM 번역 초안 채움 → `--apply` dry-run(`--write-file` 없이)으로 24/24 `ready`(전건 검증 통과) 확인 → `--write-file`로 로컬 sidecar에 실제 적용(24/24 갱신, `translation_method=llm_assisted`).
- **산출물 격리:** plan/filled/apply-report JSON과 라이브용 통합 SQL(`grm-findings-translation-apply-2026-07-09.sql`)은 전부 `.gitignore` 대상이다 — 번역 데이터 자체가 저장소에 커밋되지 않는다(§2.3 SQL 파일 포함). 운영 중 filled plan JSON 1건이 실수로 커밋에 섞였다가 push 전 amend로 제거했고, 재발 방지로 `.gitignore` 패턴을 보강했다(`findings_translation_plan*.json`·`findings_translation_filled*.json`·`findings_translation_apply*.json`·`grm-findings-translations-*.sql`·`grm-findings-translation-apply*.sql`).
- **라이브 반영:** 스키마 확장(005)과 24건 번역 UPDATE를 하나로 묶은 통합 원샷 SQL을 만들었다(§5). **아직 사용자가 Supabase SQL Editor에서 실행하지 않았다** — 이 SQL 실행이 M6 운영 완료의 마지막 사람 게이트다.

---

## 4. UI 계약(`/findings/` 및 M2 오프라인 뷰어)

### 4.1 국문 우선 + 원문 접기

`row.finding_text_ko`가 비어있지 않으면 카드 본문은 국문을 표시하고, 영문 원문(`finding_text`)은 `<details><summary>원문 보기 (영문)</summary>...</details>`로 접어 낮춘다. `translation_method === 'llm_assisted'`이면 `AI 번역 — 원문 대조 권장` 안내를 덧붙인다. `finding_text_ko`가 비어있으면(미번역 또는 폴백 fetch) 기존처럼 영문 원문만 그대로 노출한다 — 국문 우선은 additive 강등이지 원문 은닉이 아니다. 동일 로직이 `web/assets/findings.js`(`appendFindingText`)와 오프라인 뷰어 `findings_search_page.py`(`appendFindingText`, JS 문자열 내장)에 각각 구현돼 있다.

### 4.2 카테고리 라벨 — 하드코딩 동기화 계약

`findings.js`의 `CATEGORY_LABELS`(20개 code→{ko, en})는 `grm_findings.FINDING_TAXONOMY`의 `code`/`label_ko`/`label_en`과 **완전히 일치해야 한다**. 이 계약은 서술이 아니라 `web/tests/test_render.py::test_category_labels_sync_with_taxonomy`가 강제한다 — JS 소스에서 정규식으로 `CATEGORY_LABELS` 리터럴을 파싱해 `grm_findings.FINDING_TAXONOMY`(20개 고정)와 dict 비교로 대조하므로, 둘 중 하나만 고치면 CI가 즉시 감지한다. 드롭다운 옵션 텍스트는 항상 `"{ko} · {en}"` 형식이며, snake_case `category_code` 값은 어디에도 노출되지 않는다(`test_category_dropdown_never_exposes_raw_code`). 오프라인 뷰어(`findings_search_page.py`)는 하드코딩 사본을 두지 않고 `grm_findings.FINDING_TAXONOMY`를 직접 import해 같은 포맷을 만든다 — 두 UI가 서로 다른 동기화 리스크를 갖지 않는다.

### 4.3 LEGACY_FIELDS 폴백 — 배포 순서 무관 무장애

라이브 Supabase에 005(`finding_text_ko`/`translation_method` 컬럼)가 아직 적용되지 않은 상태에서 이 PR이 먼저 배포되면, PostgREST의 `select=` 목록에 알 수 없는 컬럼이 있어 400을 반환한다. `findings.js`/`findings_search_page.py` 둘 다 최초 fetch가 실패하면 신규 2컬럼을 제외한 `LEGACY_FIELDS`로 **1회만** 재시도한다 — 두 fetch 모두 실패해야 진짜 오류로 표면화한다. 이 폴백 덕에 **005 적용과 이 PR 머지의 순서가 뒤바뀌어도 페이지가 깨지지 않는다**(국문 표시만 컬럼 적용 전까지 늦게 나타날 뿐). 회귀는 `test_legacy_fetch_fallback_marker_present`(JS 소스 마커 확인)로 고정했다.

### 4.4 AI 고지 문구 보강

`findings.html`의 AI 자동 추출 고지 문단에 "카드에 표시되는 국문 해석 역시 AI 번역이므로, 법적 판단은 반드시 원문을 기준으로 하시기 바랍니다"를 추가했다(`test_ai_disclosure_mentions_translation`).

### 4.5 골든 영향

`web/tests/golden/findings.expected.html`만 재동결했다 — `base.html`/`render.py` 등 공용 렌더 경로는 무접촉임을 회귀로 확인했다(다른 페이지 골든 byte-diff 0).

---

## 5. Runbook — 통합 SQL → 머지 → 신규 번역 주기

### 5.1 라이브 반영 순서(사람 게이트, 1회)

1. Supabase Dashboard(`grm-reactions`) → SQL Editor에서 통합 원샷 SQL(`005_findings_translation_columns.sql`의 컬럼/CHECK 추가 + 24건 번역 UPDATE 24문)을 1회 실행한다.
2. 검증: `select finding_id, finding_text_ko, translation_method from public.findings where translation_method != '' order by finding_id;`로 24건이 모두 `llm_assisted`이고 `finding_text_ko`가 비어있지 않은지 확인.
3. 이 브랜치(PR)를 머지·배포한다.
4. **순서는 무관하다**(§4.3) — 1→3이든 3→1이든 폴백 덕에 무장애다. 다만 003(findings 공개 read) 이후에는 005도 가능한 한 빨리 적용해 국문 표시 지연을 최소화하는 것을 권장한다.

### 5.2 신규 findings 번역 — 주기적 반자동 절차

M4 raw-only 적재(`ENABLE_FINDINGS_SUPABASE_APPEND=true`)가 활성 상태이므로 매일 새 `raw_signals`가 쌓이고 있고, `_FINDINGS_APPEND`가 켜지면(§6) 신규 `findings` 행도 `finding_text_ko=''`(미번역) 상태로 계속 생성된다. 번역은 수집 파이프라인에 배선돼 있지 않으므로, 주기적으로 아래를 반복해야 한다.

```
1. python findings_translate.py --db-path grm-findings.sqlite3 --export --output plan.json
2. (오프라인) LLM/사람이 plan.json 의 각 item 에 finding_text_ko/translation_method 를 채운다
3. python findings_translate.py --db-path grm-findings.sqlite3 --apply plan_filled.json
   (dry-run 으로 ready=true 확인)
4. python findings_translate.py --db-path grm-findings.sqlite3 --apply plan_filled.json \
     --write-file --sql-output grm-findings-translations-<date>.sql
5. grm-findings-translations-<date>.sql 을 Supabase SQL Editor 에서 1회 실행(사람 게이트)
```

이 절차의 빈도(예: 주간)와 트리거(예: 새 raw-only 관찰 결과에 따른 `_FINDINGS_APPEND` 활성화 시점)는 아직 확정하지 않았다 — §6 이월.

---

## 6. 이월

- **신규 findings 번역 자동화** — 위 5.2 절차를 사람이 주기적으로 실행해야 하는 반자동 상태다. 자동화(예: 수집 워크플로에 LLM 번역 호출 배선, 또는 별도 스케줄드 태스크)는 이번 M6 범위 밖이며, `_FINDINGS_APPEND` 2단계 활성화로 신규 행 유입 속도가 확인된 뒤 비용/빈도를 재평가해 착수한다.
- **M4 2단계(`ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true`) 활성화 대기** — 현재는 raw-only 관찰 단계이며, findings 직행 적재가 켜지면 미번역 신규 행이 매일 쌓이기 시작한다. 켜는 시점부터 5.2 절차의 실행 빈도를 정례화해야 한다.
- **대시보드/집계 고도화** — 번역 완료율(`finding_text_ko`가 비어있지 않은 비율), `translation_method` 분포 등은 M3~M5 잔여였던 파셋 검색·히트맵 고도화와 함께 별도 트랙으로 이월한다.
- **`manual` 번역 경로 실사용 0건** — `TRANSLATION_METHODS`에 `manual`이 존재하지만 2026-07-09 운영 실행은 전량 `llm_assisted`다. 사람이 직접 교정·재작성한 번역을 구분하고 싶을 때를 위한 값이며, 아직 그 워크플로(예: 검수 후 `manual`로 재태깅)는 설계하지 않았다.
