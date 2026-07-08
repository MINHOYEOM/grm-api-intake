# FIND-1 M4 — collect_intake → Supabase 직행 적재 자동화(기본 off)

> 날짜: 2026-07-08
> 상태: M4a 코드(`findings_supabase_append.py`+`collect_intake` 배선) 완료·테스트 green · M4b 워크플로 배선(`grm-intake.yml` env 4줄)+본 문서 완료 · **raw-only 1단계 설정 완료(2026-07-09 재검증), post-M4 daily run 실증 대기**
> 2026-07-09 Codex 재검증: repo Variable `ENABLE_FINDINGS_SUPABASE_APPEND=true`, `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND` 미설정(기본 `false`), Secret `SUPABASE_SERVICE_ROLE_KEY` 존재를 확인했다. 따라서 raw-only 1단계 설정은 완료됐지만, 최신 scheduled `GRM API Intake (Daily)` 실행은 2026-07-07T20:16Z로 M4 머지 전이라 post-M4 daily run의 실제 raw append 로그/DB count 증가는 아직 관측 전이다(`raw_signals=112`, `findings=24` 유지).
> 코드 정본: `findings_supabase_append.py`, `collect_intake.py`(`RunConfig.findings_supabase_*`/`insert_items(..., findings_supabase=...)`), `.env.example`(M4a 블록)
> 테스트 정본: `tests/test_findings_supabase_append.py`(15개, HTTP 전량 mock), `tests/test_findings_store.py`(`CollectIntakeFindingsSupabaseAppendTest` 5개, collect_intake↔append 배선 경계) — 합계 20개, 기존 회귀 전량 무수정 통과(1263 passed+842 subtests)
> 커밋: `deed40e`(M4a 코드) · 이 M4b 세션(워크플로 배선+문서화)은 코드 무변경

---

## 0. M4 게이트 — 하는 것 / 안 하는 것

M4a/M4b는 M3(Supabase 라이브 적재 완료 — `raw_signals` 112건/`findings` 24건)가 남긴 잔여 항목 중 하나("`collect_intake` → Supabase 직행 적재 자동화")를 코드/워크플로 수준까지 연다. 다만 **자동 활성화는 하지 않는다** — 이 점이 M4의 핵심 게이트다.

- **M4a(완료, 코드):** `findings_supabase_append.py`를 신설해 `collect_intake`가 Notion insert 성공분을 PostgREST로 직접 append하는 경로를 제공한다. 레코드 생성·검증은 M1 계층(`findings_store.raw_signal_from_intake_item`/`findings_extractors.findings_from_raw_signal`/`grm_findings.validate_raw_signal`·`validate_finding`)을 그대로 재사용하며, 이 모듈이 새로 추가하는 것은 **HTTP 전송과 status 어휘뿐**이다. 기본은 두 플래그 모두 off — 코드가 존재해도 워크플로/로컬 환경에서 플래그를 켜지 않으면 아무 동작도 하지 않는다.
- **M4b(완료, 워크플로+문서):** `grm-intake.yml`의 collect 스텝 env에 `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`/두 `ENABLE_FINDINGS_SUPABASE_*` 플래그를 배선해 M4a 코드가 GitHub Actions에서 실행될 수 있는 상태로 만든다. **이 배선 자체는 아무것도 켜지 않는다** — `vars.ENABLE_FINDINGS_SUPABASE_APPEND`가 repo Variable로 등록돼 있지 않으면 `|| 'false'` fallback으로 계속 off이고, `SUPABASE_SERVICE_ROLE_KEY` secret이 미등록이면 설령 플래그를 켜도 `collect_intake`가 WARN 후 비활성화한다.
- **하지 않은 것:** GitHub Secrets/Variables 등록(사람의 몫, §4 runbook), Supabase 프로젝트 측 스키마 변경(M3에서 이미 완료 — `raw_signals`/`findings` 테이블과 RLS는 그대로), Notion/웹 발행 파이프라인 변경(무접촉), `findings_supabase.py`(M3a 오프라인 로드 플랜 생성기) 변경(별개 모듈, 이번 범위 밖), M1~M3 코드/스펙 수정(불가침).
- **왜 필요한가:** M1 운영 백필(2026-07-08)로 커밋한 `raw_signals=112`/`findings=24`가 Supabase 라이브 DB의 현재 전량이다 — 그날 이후 매일 수집되는 신규 신호는 SQLite sidecar(`ENABLE_FINDINGS_SQLITE_APPEND`, 로컬 파일이라 GitHub Actions 실행마다 휘발)에만 쌓이고 Supabase에는 반영되지 않는다. M4a/M4b는 그 축적 경로를 "일일 수집분을 지속적으로 Supabase에 추가"할 수 있는 상태로 준비하되, 실제로 그 경로를 여는 결정(Secrets/Variables 등록)은 사람에게 남긴다.

---

## 1. HTTP 계약

`findings_supabase_append.py`의 `_post_rows()`가 모든 PostgREST 호출의 단일 경로다.

| 항목 | 값 |
|---|---|
| 엔드포인트 | `POST {SUPABASE_URL}/rest/v1/{table}` (`table` = `raw_signals` 또는 `findings`), `on_conflict` 쿼리 파라미터로 충돌 대상 컬럼 지정(`raw_signals`→`raw_signal_id`, `findings`→`finding_id`) |
| 헤더 | `apikey`/`Authorization: Bearer {service_key}`, `Content-Type: application/json`, `Prefer: resolution=ignore-duplicates,return=representation` |
| 멱등 판정 | `return=representation`이 반환하는 JSON 배열의 **길이**로 판정한다 — 길이>0이면 그 행(들)이 실제 insert된 것(=`inserted`), 길이 0(빈 배열)이면 `on conflict ... ignore-duplicates`가 스킵한 것(=`duplicate`). Postgres 쪽 카운트 쿼리를 별도로 치지 않는다. |
| 타임아웃 | 15초(`DEFAULT_TIMEOUT_SECONDS`) |
| 재시도 | 5xx 응답 또는 `requests.exceptions.Timeout` 발생 시 **1회만** 재시도(총 시도 2회). 4xx 또는 timeout 이외의 `RequestException`은 즉시 실패(재시도 없음). |
| findings 배치 409 폴백 | `findings`는 PK(`finding_id`) 충돌만 batch `on_conflict`로 흡수 가능하고 `(raw_signal_id, md5(finding_text))` unique index 충돌은 batch 단위로 표현할 수 없다 — 배치 POST가 409를 반환하면 그 배치를 **행 단위로 재전송**하고, 행별 409는 콘텐츠 레벨 duplicate로 집계한다(SQLite 쪽 dedupe 의미론과 동치). |
| 에러 요약 | `""`(성공) \| `"timeout"` \| 예외 타입명(예: `"ConnectionError"`) \| `"http_{status}"`. **원문 예외 메시지·응답 바디는 어디에도 포함하지 않는다** — service-role key가 하위 전송 계층 에러 문자열에 실려도 이 경로로는 노출될 수 없다. |

---

## 2. status 어휘 매트릭스 — SQLite 대비

| 결과 클래스 | SQLite(`findings_store.py`) | Supabase(`findings_supabase_append.py`) |
|---|---|---|
| raw_signal 단독 append | `inserted` \| `duplicate` \| `invalid` | `inserted` \| `duplicate` \| `invalid` \| **`error`**(HTTP/네트워크 실패, 재시도 소진 후) |
| raw_signal+findings append | `inserted` \| `duplicate` \| `invalid` \| `partial` \| `raw_signal_inserted` | 좌와 동일 5종 + **`error`** |
| `error`의 의미 | 해당 없음(로컬 파일 I/O 실패는 예외로 전파, 별도 status 없음) | raw_signal POST가 재시도 소진 후에도 실패했거나(→ 그 시점에서 findings 시도 자체를 하지 않음), findings 배치+행단위 폴백이 모두 실패해 insert/duplicate가 0건인 경우 |
| `partial`의 의미(공통) | raw_signal은 성공했으나 findings 중 일부가 invalid이거나 POST 에러를 만난 경우 | 동일(양쪽 저장소 의미론 일치) |

Dataclass: `SupabaseRawSignalAppendResult{status, raw_signal_id, errors}` / `SupabaseRawSignalWithFindingsAppendResult{status, raw_signal_id, raw_signal_status, findings_inserted, findings_duplicate, findings_invalid, errors}`. `errors`는 사람이 읽을 문자열 튜플이며 service-role key를 담지 않는다(§1 참고).

---

## 3. `collect_intake` 배선 계약

| 항목 | 값 |
|---|---|
| 플래그 | `ENABLE_FINDINGS_SUPABASE_APPEND`(raw_signals만) · `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND`(APPEND도 true여야 findings까지 포함) — 둘 다 기본 `false` |
| 자격 변수 | `SUPABASE_URL`(https:// 로 시작해야 유효 — `_normalize_base_url()`이 거부 시 `invalid`), `SUPABASE_SERVICE_ROLE_KEY` |
| dry-run | `--dry-run`이면 두 플래그가 켜져 있어도 Supabase append를 전혀 시도하지 않는다(`args.dry_run` 분기가 SQLite sidecar와 동일 위치에서 스킵, INFO 로그만 남김) |
| 자격 미달 처리 | `ENABLE_FINDINGS_SUPABASE_APPEND=true`인데 `SUPABASE_URL` 또는 `SUPABASE_SERVICE_ROLE_KEY`가 비어 있으면 **WARN 로그 후 이번 실행은 append 완전 비활성**(hard fail 없음, Notion insert는 그대로 진행). `ENABLE_FINDINGS_SUPABASE_APPEND=false`인데 `_FINDINGS_APPEND=true`만 켜져 있으면 WARN 후 무시(SQLite sidecar와 동일 규칙). |
| 실패 격리 | `insert_items()`가 `append_intake_item_to_supabase()`/`append_intake_item_with_findings_to_supabase()` 호출을 try/except로 감싸 어떤 예외가 나도 **Notion insert 통계(inserted/skipped/failed)·handoff·Notion `Status` 속성 흐름을 바꾸지 않는다** — WARN 로그만 남는다(`tests/test_findings_store.py::CollectIntakeFindingsSupabaseAppendTest`가 raw-only/findings-포함 두 경로 모두 회귀 고정). |
| SQLite sidecar와의 관계 | 완전 독립 — `findings_sqlite_path`(SQLite)와 `findings_supabase`(Supabase) 파라미터는 `insert_items()`에 별도로 전달되며, 둘 다 켜면 같은 Notion insert 성공 아이템에 대해 SQLite append와 Supabase append가 **모두** 실행된다(`test_sqlite_and_supabase_both_on_calls_both`로 고정). 한쪽 플래그·자격 상태가 다른 쪽에 영향을 주지 않는다. |
| health flags 노출 | `grm-health.json`에 `ENABLE_FINDINGS_SUPABASE_APPEND`/`ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND`(요청값, `cfg.findings_supabase_*_requested`) 두 키가 항상 노출된다 — 실제 활성 여부(자격 충족까지 포함한 effective 값)는 별도 노출 없이 실행 로그(INFO/WARN)로만 확인 가능하다. |

---

## 4. 활성화 runbook (사람)

라이브 적재를 시작하려면 아래 순서를 그대로 따른다. 어느 단계에서 멈춰도 이전 상태로 안전하게 유지된다(§5 롤백).

1. **service_role key 확인** — Supabase Dashboard → 대상 프로젝트(`grm-reactions`, M3와 동일 프로젝트) → Settings → API → `service_role` 시크릿 키를 확인한다. 이미 `grm-admin-backend-deploy.yml`이 같은 키를 `secrets.SUPABASE_SERVICE_ROLE_KEY`로 쓰고 있으므로, 그 값을 재확인하는 것으로 충분하다(새 키를 발급할 필요 없음).
2. **GitHub Secrets 등록 확인** — 저장소 Settings → Secrets and variables → Actions → Secrets 탭에 `SUPABASE_SERVICE_ROLE_KEY`가 이미 있으면(관리 백엔드 배포용으로 등록돼 있을 가능성이 높다) 그대로 재사용한다. 없다면 1에서 확인한 값을 새로 등록한다.
3. **repo Variable 1단계** — Variables 탭에 `ENABLE_FINDINGS_SUPABASE_APPEND=true`를 추가한다(`ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND`는 아직 건드리지 않음). 다음 scheduled run(매일 18:17 UTC)부터 raw_signals만 Supabase에 append되기 시작한다. **최소 1주 관찰**한다 — `grm-health.json`의 두 플래그 값과 Actions 로그의 `FIND-1 raw_signals Supabase append 활성화` INFO 라인, WARN 유무를 확인한다.
4. **findings까지 확장** — 1주 관찰에서 이상이 없으면 `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true`도 추가한다. 이후 실행부터는 raw_signals+findings가 함께 append된다.
5. **확인 쿼리** — Supabase SQL Editor 또는 `mcp__…__execute_sql`로 `select count(*) from public.raw_signals;` / `select count(*) from public.findings;`를 실행해 M1 백필 시점 값(112/24)보다 카운트가 늘어나는지 확인한다. `grm-health.json`의 `ENABLE_FINDINGS_SUPABASE_APPEND`/`_FINDINGS_APPEND` 플래그 값도 함께 확인해 요청대로 반영됐는지 교차 확인한다.

## 5. 롤백

repo Variables에서 `ENABLE_FINDINGS_SUPABASE_APPEND`를 `false`로 되돌리면(또는 삭제하면) 다음 scheduled run부터 즉시 append가 중단된다 — 코드 되돌림·재배포 불필요. **이미 Supabase에 적재된 데이터는 롤백해도 그대로 남는다**(삭제되지 않음) — 데이터 자체를 되돌리려면 별도로 Supabase에서 수동 삭제해야 한다. SQLite sidecar 경로는 이 플래그와 무관하게 계속 동작한다(독립 배선, §3).

## 6. 잔여 / 이월 목록

- **대시보드 고도화**(파셋 검색·히트맵·업체 이력) — M4a/M4b 범위 밖, 향후 트랙.
- **분류기(v1 substring 매칭) 키워드 정제** — `other_quality_system`/`needs_review` 비중이 높은 카테고리 우선. taxonomy 버전 이벤트가 필요하며 SQLite/Postgres CHECK 제약 동반 마이그레이션이 뒤따른다.
- **라이브 활성화 자체** — 이 문서가 다루는 코드/워크플로 배선은 준비까지이며, §4 runbook 실행(Secrets 확인·Variables 등록·1주 관찰)은 사람의 몫으로 남는다.
- **Copilot Studio 에이전트 연동(M4~M5, 전략 로드맵)** — `GRM_Findings인텔리전스_전략로드맵_2026-07-07.md` 원래 계획의 후속 단계로, 이번 M4a/M4b(적재 자동화)와는 별개 트랙이다.
