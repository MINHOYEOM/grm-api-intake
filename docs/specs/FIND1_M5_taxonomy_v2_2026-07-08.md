# FIND-1 M5 — taxonomy v2(단어경계 분류기) + v1/v2 이중 수용 마이그레이션

> 날짜: 2026-07-08
> 상태: M5a 코드(`grm_findings.py` 매칭 엔진+키워드 정제) 완료·테스트 green · M5b 코드(`postgres_schema_ddl()` IN-list+`002_findings.sql` 재생성+`004_findings_taxonomy_v2.sql`+`findings_taxonomy_migrate_sqlite.py`) 완료·로컬 sidecar 마이그레이션 적용 완료 · **라이브 Supabase 004 적용 완료(2026-07-09 Codex 재검증)**
> 2026-07-09 Codex 재검증: 사용자가 004를 적용한 뒤 live DB에서 `findings_taxonomy_version_v1v2_check` 존재와 full 문자열 `grm-finding-taxonomy/v1`/`grm-finding-taxonomy/v2` 허용을 확인했다. 저장된 24개 finding은 모두 기존 provenance대로 `grm-finding-taxonomy/v1`이며 v2 row는 아직 없다.
> 코드 정본: `grm_findings.py`(`TAXONOMY_VERSION`/`TAXONOMY_VERSIONS`/`FINDING_TAXONOMY`/`classify_finding_category`/`_keyword_matches`/`_ascii_keyword_pattern`), `findings_supabase.py`(`postgres_schema_ddl()`), `web/migrations/002_findings.sql`, `web/migrations/004_findings_taxonomy_v2.sql`, `findings_taxonomy_migrate_sqlite.py`
> 테스트 정본: `tests/test_grm_findings.py`(M5a 신규 13개), `tests/test_findings_supabase.py`(M5b DDL 회귀 확장), `tests/test_findings_taxonomy_migrate.py`(M5b 신규 17개) — 신규 합계 30개, 전체 스위트 1293 passed+842 subtests
> 커밋: `fb4509a`(M5a+M5b, 단일 커밋)

---

## 0. M5 게이트 — 하는 것 / 안 하는 것

M4a/M4b가 `collect_intake` → Supabase 직행 적재 경로를 코드/워크플로 수준까지 열면서, 잔여 목록에 남아 있던 "분류기(v1 substring 매칭) 키워드 정제"를 **데이터가 축적되기 시작하기 전에** 처리한다. 이 시점(2026-07-08) 기준 라이브 `findings` 테이블은 M1 7월 백필의 24건뿐이라 taxonomy 개정에 따른 이월 비용이 최소다 — M4 raw-only 적재가 이미 활성화돼 있어 findings까지 켜지면(`ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true`) 매일 새 행이 v1 분류기로 쌓이기 시작하므로, 그 활성화 이전에 분류기를 정제하는 것이 이번 작업의 동기다.

- **하는 것(M5a):** `grm_findings.py`의 매칭 엔진을 substring에서 ASCII 단어경계 정규식으로 바꾸고, 오분류를 유발하던 3개 카테고리(`documentation_records`/`deviation_capa`/`qc_lab_controls`)의 일반어 키워드를 제거·구체화한다. `TAXONOMY_VERSION`을 `grm-finding-taxonomy/v2`로 올리되 `TAXONOMY_VERSIONS=(v1, v2)`로 validator/SQLite DDL이 두 버전을 모두 수용해 기존 v1 레코드를 무효화하지 않는다.
- **하는 것(M5b):** v2 분류기가 만드는 `taxonomy_version="grm-finding-taxonomy/v2"` 행을 저장소가 거부하지 않도록 SQLite(`findings_store`가 재사용하는 `grm_findings.sqlite_schema_ddl()`)와 Postgres(`findings_supabase.postgres_schema_ddl()`) 양쪽의 CHECK 제약을 IN-list로 확장한다. 로컬 sidecar는 파일 재구축(`findings_taxonomy_migrate_sqlite.py`)으로, 라이브 Supabase는 `ALTER TABLE ... DROP/ADD CONSTRAINT`(`004_findings_taxonomy_v2.sql`)로 각각 적용한다. 어느 경로도 기존 v1 행을 재분류하지 않는다(provenance 보존 — "그 시점에 어떤 분류 계약으로 생성됐는가"를 유지).
- **하지 않는 것:** 카테고리 신설·삭제·이름 변경·순서 변경(`FINDING_TAXONOMY`의 20개 code/label/순서는 완전 불변 — `category_code` IN-list도 무변경), 기존 저장된 v1 레코드의 재분류(선택적 재분류는 §7로 이월, 의도적 비수행), 라이브 Supabase에 대한 `004_findings_taxonomy_v2.sql` 실행(CC 세션 권한 게이트로 사용자에게 SQL 위임), `findings_supabase_append.py`/`collect_intake` 배선 변경(M4 범위, 무접촉), M1~M4 코드/스펙 문서 수정(불가침).
- **왜 지금인가:** M1 스키마 계약 문서(`FIND1_M1_schema_contract_2026-07-08.md` §4)가 이미 "분류기 v1은 결정론 substring 매칭이다 ... 의미 있는 수정은 taxonomy version 이벤트로 다룬다"고 명시했고, M4 스펙(`FIND1_M4_supabase_append_2026-07-08.md` §6)도 "분류기 키워드 정제 — taxonomy 버전 이벤트가 필요하며 SQLite/Postgres CHECK 제약 동반 마이그레이션이 뒤따른다"를 잔여로 남겼다. M4 raw-only 적재 활성화(`ENABLE_FINDINGS_SUPABASE_APPEND=true`, 2026-07-08)로 축적이 실제로 시작된 지금이, 24건에 그친 이월 비용으로 그 부채를 갚을 수 있는 마지막 저비용 시점이다.

---

## 1. v2 매칭 엔진 규칙

| 항목 | v1 | v2 |
|---|---|---|
| ASCII 키워드 매칭 | `keyword.lower() in haystack`(단순 substring) | `_ascii_keyword_pattern(keyword)` — 대소문자 무시 단어경계 정규식(`re.IGNORECASE`) |
| 단일 단어 패턴 | 해당 없음 | `\b{word}s?\b`(단순 복수 허용, 예: `record`→`records`도 매치) |
| 다단어 구 패턴 | 해당 없음 | 단어 사이 `\s+`로 유연화(`re.escape(word)` 조인) + 말미 `s?`(예: `batch record`→`batch  records`도 매치) |
| 한글 키워드 매칭 | `keyword.lower() in haystack`(substring) | **동일(substring 유지)** — Hangul에는 정규식 단어경계(`\b`)가 적용되지 않아 개념 자체가 없음 |
| 패턴 캐시 | 없음 | `_ASCII_KEYWORD_PATTERN_CACHE: dict[str, re.Pattern]` 모듈 레벨 캐시(키워드당 1회 컴파일) |
| 라우팅 함수 | `classify_finding_category`가 직접 `in` 비교 | `_keyword_matches(haystack, keyword)`가 `keyword.isascii()`로 분기 후 `classify_finding_category`가 호출 |
| 카테고리 순회 순서 | `FINDING_TAXONOMY` 튜플 순서(20개, `other_quality_system` 제외 후 첫 매치) | **불변** — 매칭 함수만 교체, 순회·조기반환 로직 무변경 |

이 변경이 substring 오분류를 해소하는 이유: `\bcapa\b`는 `capacity` 내부의 `capa`(뒤에 단어 경계가 아닌 `city`가 이어짐)에 매치하지 않지만 기존 `"capa" in haystack`은 매치했다. 한글은 형태소 경계가 명확하지 않아(예: "실태조사"에서 "조사"만 분리 매칭하면 다른 의미) 단어경계 개념을 적용할 수 없으므로, §2의 한글 키워드 정제는 구체화(복합어 채택)로 대응하고 매칭 로직 자체는 substring을 유지한다.

---

## 2. 키워드 diff 표 — 3개 카테고리 v1 → v2

다른 17개 카테고리의 키워드는 v1에서 무변경이다.

### `documentation_records` (문서화/기록관리)

| 구분 | v1 | v2 |
|---|---|---|
| 제거 | `record`, `records`, `documentation`, `written procedure`, `문서` | — |
| 유지 | `제조기록`, `기록서` | `제조기록`, `기록서` |
| 신규 | — | `batch record`, `written procedure`(구 형태 유지·단어경계로 재수용), `documentation practice`, `recordkeeping`, `record retention`, `문서관리`, `기록관리` |
| 근거 | 일반어 `record`/`documentation`/`문서`가 뒤 순서 특화 카테고리(예: `aseptic_sterility_assurance`의 "batch record" 유사 문맥)보다 먼저 매치해 가려버림 | 구체적 구(phrase)만 남겨 일반어 단독 등장은 `other_quality_system`으로 정직하게 떨어지게 함 |

### `deviation_capa` (일탈/CAPA/조사)

| 구분 | v1 | v2 |
|---|---| ---|
| 제거 | `조사`(단독), `시정`(단독) | — |
| 유지 | `deviation`, `capa`, `investigation`, `unexplained discrepancy`, `일탈` | 동일(단어경계 매칭으로 전환) |
| 신규 | — | `원인조사`, `일탈조사`, `시정조치` |
| 근거 | bare `"조사"`가 `실태조사`(GMP 정기실태조사, 전혀 다른 카테고리)·`임상시험 조사`류 문맥에 포함돼 오분류. bare `"시정"`도 유사하게 광범위 | 일탈 문맥에 특화된 복합어만 채택 |

### `qc_lab_controls` (시험실/품질관리)

| 구분 | v1 | v2 |
|---|---|---|
| 제거 | `시험`(단독) | — |
| 유지 | `laboratory`, `quality control`, `test method`, `시험성적`, `품질관리` | 동일(단어경계 매칭으로 전환) |
| 신규 | — | `시험실`, `시험방법` |
| 근거 | bare `"시험"`이 `임상시험`·`실태조사 중 시험` 등 QC 랩과 무관한 문맥에도 매치 | `시험실`/`시험방법`처럼 QC 랩 문맥에 특화된 복합어만 채택 |

---

## 3. 버전 수용 계약 — 3계층

taxonomy 버전은 아래 세 지점에서 각각 "v1 또는 v2"를 수용하도록 IN-list로 전환됐다. 세 지점 모두 값의 **집합**만 넓혔고, 다른 컬럼·제약·인덱스는 무변경이다.

| 계층 | 위치 | v1(이전) | v2(이후) |
|---|---|---|---|
| Python validator | `grm_findings.validate_finding()` | `record.get("taxonomy_version") != TAXONOMY_VERSION`(등호 1건) | `record.get("taxonomy_version") not in TAXONOMY_VERSIONS`(멤버십, `TAXONOMY_VERSIONS=("grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2")`) |
| SQLite DDL | `grm_findings.sqlite_schema_ddl()` → `findings.taxonomy_version` 컬럼 CHECK | `CHECK (taxonomy_version = 'grm-finding-taxonomy/v1')` | `CHECK (taxonomy_version IN ('grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2'))` |
| Postgres DDL | `findings_supabase.postgres_schema_ddl()` → `002_findings.sql`(byte 단일 소스) | `check (taxonomy_version = 'grm-finding-taxonomy/v1')` | `check (taxonomy_version in ('grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2'))` |

라이브 Supabase는 이미 002의 v1 등호 CHECK가 적용된 상태이므로, 위 표의 "Postgres DDL" 변경은 **신규 fresh-install에만 자동 반영**된다. 이미 살아있는 테이블에 IN-list를 반영하려면 별도 `ALTER`(§4의 004)가 필요하다 — 이것이 M5b가 002 재생성과 004 신설을 모두 포함하는 이유다.

---

## 4. 마이그레이션 runbook

### 4.1 로컬 sidecar (`grm-findings.sqlite3`) — 완료

SQLite는 컬럼 CHECK 제약을 `ALTER`로 바꿀 수 없으므로, `findings_taxonomy_migrate_sqlite.py`는 새 DDL로 파일 전체를 재구축한다.

1. `migrate_taxonomy_sqlite(db_path, write_file=False)`(기본 dry-run) — 원본을 `mode=ro`로만 열어 `before_counts`/`before_hashes`(=`(raw_signal_id, finding_text)` SHA-256 identity 집합)/`before_taxonomy_versions`를 스냅샷.
2. 임시 디렉터리에 `findings_store.ensure_findings_schema()`(v2 IN-list DDL)로 새 DB를 만들고 `raw_signals`/`findings` 전 컬럼을 원본에서 그대로(재인코딩 없이) 복사.
3. 새 DB를 다시 read-only로 열어 `_verify_new_db()` — counts 일치, identity 해시 집합 일치, `taxonomy_version` 값이 `{v1, v2}`의 부분집합, `findings` 테이블 DDL 문자열에 IN-list와 두 버전 리터럴이 모두 존재함을 확인. 넷 다 참이어야 `verified=true`.
4. `write_file=False`(기본)면 여기서 끝 — 임시 디렉터리가 삭제되고 원본은 전혀 건드리지 않는다.
5. `write_file=True`이고 `verified=true`일 때만: `{db_path}.bak-v1`이 이미 있으면 즉시 에러(재실행 시 기존 백업을 덮어쓰지 않음) → 없으면 원본을 그 이름으로 복사 → 새 DB를 `{db_path}.tmp-v2`로 복사 → `os.replace(tmp, db_path)`로 원자 교체.
6. CLI(`python findings_taxonomy_migrate_sqlite.py --db-path ... [--write-file] [--output report.json] [--pretty]`) exit code: `0`=검증 통과(dry-run 또는 write 성공), `2`=`OSError`/`ValueError`/`sqlite3.Error`(예: 백업 기존재), `3`=검증 실패(write 여부와 무관).

**실행 기록(2026-07-08):** 운영 `grm-findings.sqlite3`(raw_signals 112/findings 24)에 대해 dry-run으로 `verified=true`를 먼저 확인한 뒤 `--write-file`을 적용했다. `counts.before == counts.after == {raw_signals: 112, findings: 24}`, `finding_identity_match=true`, `taxonomy_versions_after`가 `{grm-finding-taxonomy/v1}`의 부분집합(기존 24건은 모두 v1로 생성됐으므로 v2 값은 아직 등장하지 않음 — 저장소가 v2도 받아들일 준비만 됐다는 뜻), `findings_ddl_has_in_list=true`를 확인했다. `grm-findings.sqlite3.bak-v1` 백업이 로컬에 남아 있다(둘 다 `.gitignore` 대상, 커밋되지 않음).

### 4.2 라이브 Supabase — 004 적용 완료(2026-07-09 재검증), 순서 제약

라이브 `public.findings`는 이미 002의 v1 등호 CHECK로 생성돼 있다(M3 라이브 적용, `raw_signals=112`/`findings=24`). `004_findings_taxonomy_v2.sql`은 그 제약만 넓히는 `DO $$ ... $$` 블록이다 — `pg_constraint`를 `public.findings`·`taxonomy_version` 관련 CHECK로 필터링해 이름에 의존하지 않고 전부 drop한 뒤, 명명된 `findings_taxonomy_version_v1v2_check`를 추가한다. 재실행해도 동일 결과(멱등)이며 행 데이터는 전혀 건드리지 않는다.

**절차:**
1. 사용자가 Supabase Dashboard(`grm-reactions`) → SQL Editor에서 `web/migrations/004_findings_taxonomy_v2.sql` 전체를 1회 실행한다.
2. 검증 쿼리(파일 하단 주석에 포함) 2건 실행: `select distinct taxonomy_version from public.findings order by taxonomy_version;`(v1/v2 외 값이 없어야 함) / `select conname, pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_taxonomy_version_v1v2_check';`(신규 제약 존재+IN-list 확인).

**순서 제약(중요):** `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true`를 켜기 **전에** 반드시 004를 먼저 적용해야 한다. v2 분류기가 만든 `taxonomy_version="grm-finding-taxonomy/v2"` 행을 004 미적용 상태로 append 시도하면, 라이브의 v1 등호 CHECK가 그 INSERT를 거부한다(`findings_supabase_append.py`의 `error` 상태로 관측됨 — §2 `FIND1_M4_supabase_append_2026-07-08.md` status 어휘 매트릭스 참고). 현재 활성 상태인 raw-only 단계(`ENABLE_FINDINGS_SUPABASE_APPEND=true`, `_FINDINGS_APPEND`는 아직 off)는 `raw_signals`만 append하고 `findings` 테이블에 쓰지 않으므로 taxonomy CHECK와 무관하며 이 순서 제약의 영향을 받지 않는다.

---

## 5. 실코퍼스 검증 기록 (2026-07-08)

라이브 findings 24건에 v2 분류기(`classify_finding_category`, 정제된 키워드+단어경계 매칭)를 재실행해 v1 저장값과 비교했다.

| 결과 | 건수 | 내용 |
|---|---|---|
| 불변 | 21 | v1과 v2가 같은 `category_code`를 산출 — 대다수 레코드는 애초에 오분류 유발 키워드(bare record/조사/시험)와 무관 |
| 변경 | 3 | 전부 **개선 방향**으로 이동: WL(경고서한) 일반 전문 발췌 2건이 `documentation_records`(일반어 오매칭)에서 `aseptic_sterility_assurance`/`equipment_facility` 등 더 구체적인 특화 카테고리로 재분류됨, 웹사이트/라벨 리뷰 관련 1건이 `labeling_packaging`에서 `other_quality_system`으로 이동(원문이 실제로는 표시·포장 특화 키워드와 무관했던 경우) |

**중요 — 이 재검증은 관측(코드 정확성 확인)일 뿐이며, 라이브 데이터의 실제 재분류(UPDATE)는 수행하지 않았다.** §0 게이트대로 기존 24건은 `taxonomy_version="grm-finding-taxonomy/v1"`로 저장된 그대로 유지된다. provenance 원칙 — "이 레코드가 어떤 분류 계약으로 생성됐는가"를 그 시점 값으로 보존하고, 새 계약은 새로 생성되는 레코드부터만 적용한다. 선택적 소급 재분류는 §7로 명시적으로 이월한다.

---

## 6. 롤백

### 6.1 로컬 sidecar

`grm-findings.sqlite3.bak-v1`이 v2 DDL 적용 전 원본이다. 되돌리려면:

```
mv grm-findings.sqlite3 grm-findings.sqlite3.v2-rejected   # 또는 삭제
mv grm-findings.sqlite3.bak-v1 grm-findings.sqlite3
```

백업은 마이그레이션 스크립트가 재실행 시 기존 백업을 거부하므로(§4.1 5단계), 롤백 후 다시 마이그레이션하려면 위처럼 `.bak-v1`을 먼저 치워야 한다.

### 6.2 라이브 Supabase

004는 이미 존재하던 CHECK를 이름과 무관하게 전부 drop 후 재생성하므로, v1 등호로 되돌리는 SQL은 같은 패턴을 역으로 적용한다(예시, 실행 전 `pg_constraint`로 현재 이름 재확인 권장):

```sql
alter table public.findings drop constraint if exists findings_taxonomy_version_v1v2_check;
alter table public.findings
  add constraint findings_taxonomy_version_check
  check (taxonomy_version = 'grm-finding-taxonomy/v1');
```

**주의:** 이 롤백은 그 시점까지 이미 `taxonomy_version='grm-finding-taxonomy/v2'`로 append된 행이 있으면 제약 추가 자체가 실패한다(기존 행이 새 CHECK를 위반) — 그런 행이 있다면 먼저 삭제하거나 v1로 갱신해야 하며, 이는 데이터 손실/재분류를 수반하므로 신중히 결정한다. `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND`를 다시 `false`로 되돌리면 신규 v2 행 유입은 즉시 멈춘다(코드 되돌림 불필요, `FIND1_M4_supabase_append_2026-07-08.md` §5와 동일 원리).

---

## 7. 이월

- **라이브 004 적용 실행 자체** — 2026-07-09 재검증 기준 사용자가 SQL Editor에서 1회 실행을 완료했고, `findings_taxonomy_version_v1v2_check`가 live DB에 존재한다. 다음 잔여는 `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true`를 켜기 전 raw-only 관찰 결과를 확인하는 것이다.
- **M4 잔여 후보(대시보드 고도화 등)** — 파셋 검색·히트맵·업체 이력 등은 M5와 무관하게 여전히 잔여다(`FIND1_M4_supabase_append_2026-07-08.md` §6 참고).
- **v1 기존 행의 선택적 재분류는 의도적으로 수행하지 않는다.** §5의 실코퍼스 검증은 v2 분류기가 개선 방향으로만 움직인다는 것을 확인하는 관측이며, 기존 24건(이후 라이브 004 적용·findings append 활성화 전까지 계속 v1로만 쌓일 신규 행 포함)을 일괄 재분류하는 별도 배치는 이 문서의 범위 밖이다. 필요해지면 "언제·무엇을 재분류했는지"를 별도 taxonomy 마이그레이션 이벤트로 문서화하고, `category_code` 변경 이력을 추적할 수 있는 감사 필드(예: `taxonomy_reclassified_at`)를 먼저 설계해야 한다 — 이번 M5는 그 설계를 포함하지 않는다.
- **Copilot Studio 에이전트 연동(M4~M5, 전략 로드맵)** — `GRM_Findings인텔리전스_전략로드맵_2026-07-07.md`의 후속 단계이며, 이번 taxonomy v2(내부 명명 M5)와는 별개 트랙이다.
