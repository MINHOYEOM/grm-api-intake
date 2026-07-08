# FIND-1 M8 — 번역 도구 라이브 소스 모드(`--source supabase`) + M4 완전 가동(운영 지위 전환)

> 날짜: 2026-07-09
> 상태: 코드 완료(M8a)·테스트 green(신규 19개, 전량 mock·전체 1397 passed+842 subtests)·M4 완전 가동 라이브 실증(raw_signals 18건 자동 적재 확인)·주간 번역 루프는 여전히 사람 게이트(SQL Editor 1회 실행) 포함 반자동
> 코드 정본: `findings_translate.py`(`--source {sqlite,supabase}`, `build_translation_plan_supabase`/`apply_translations_supabase`/`_supabase_get`/`_fetch_untranslated_supabase`/`_fetch_findings_total_supabase`/`_fetch_live_rows_for_ids_supabase`/`_resolve_supabase_credentials`)
> 테스트 정본: `tests/test_findings_translate.py`(`SupabaseExportTest`·`SupabaseApplyTest`·`SupabaseCliTest`·`SqliteSourceRegressionTest`, 신규 19개)
> 커밋: `34b04ff`(M8a, 코드). M4 완전 가동(raw_signals 직행 적재 라이브 확인 + `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true` 활성화)은 코드 변경이 아니라 기존 M4a/M4b 플래그의 운영 사건 기록이다.

---

## 0. M8 게이트 — 하는 것 / 안 하는 것 (운영 지위 전환 명시)

M6·M7까지 `findings_translate.py`는 로컬 `grm-findings.sqlite3` sidecar만 읽고 쓰는 도구였고, 이 sidecar는 M1 7월 백필(112/24)에 사실상 동결돼 있었다. 그런데 2026-07-08~09 사이 두 가지 실증이 겹쳤다 — ① M4 raw-only 적재(`ENABLE_FINDINGS_SUPABASE_APPEND=true`)가 머지 후 첫 daily run에서 실제로 라이브 append를 수행했고(§4), ② 사용자 지시로 `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true`까지 앞당겨 활성화했다. 그 결과 **신규로 유입되는 findings의 정본이 로컬 sidecar에서 라이브 Supabase로 넘어갔다** — 이 전환을 코드와 문서 양쪽에 반영하는 것이 M8의 동기다.

- **하는 것(M8a, 코드):** `findings_translate.py`에 `--source {sqlite,supabase}`(기본 `sqlite`)를 추가해, 같은 도구가 라이브 Supabase를 PostgREST 경유 anon key로 읽을 수 있게 한다. `--export --source supabase`는 미번역 findings를 라이브에서 직접 뽑고, `--apply --source supabase`는 채워진 plan을 라이브 스냅샷과 대조 검증한 뒤 사람이 SQL Editor에서 실행할 UPDATE SQL만 만든다(§2). 기존 `--source sqlite`(기본값) 경로는 **byte 단위로 무변경**이다.
- **하는 것(운영 지위 문서화):** 로컬 sidecar(`grm-findings.sqlite3`, 112/24)는 이제 "7월 백필 스냅샷 + 로컬 개발용 사본"이고, **신규 유입분(M4 완전 가동 이후 매일 쌓이는 raw_signals/findings)의 시스템 정본은 라이브 Supabase**다. 로컬 sidecar에 대한 백필/마이그레이션(M1~M6a)은 여전히 유효한 과거 기록이지만, 앞으로의 번역 작업은 `--source supabase`가 기본 대상이 된다(sidecar 경로는 로컬 개발·회귀 테스트용으로 계속 존재).
- **하지 않는 것:** 라이브 Supabase에 대한 직접 write 경로 추가(anon key는 RLS로 `findings` SELECT만 허용되고 UPDATE 권한이 없다 — §2), `--write-file`을 `--source supabase`에서 허용(명시적으로 exit 2 거부), export 1000행 초과 시 자동 페이지네이션(§5 이월), 번역 자체의 자동 생성(도구는 여전히 추출/검증/SQL 산출만 하고 번역 텍스트는 오프라인 LLM/사람이 채운다 — M6b 원칙 그대로), M1~M7 스키마·코드·골든 수정(전부 불가침 — 이번 스프린트는 findings_translate.py 확장 하나뿐).

---

## 1. CLI 모드 매트릭스

| `--source` | `--export` | `--apply` | `--write-file` | `--sql-output` | 동작 |
|---|---|---|---|---|---|
| `sqlite`(기본) | O | — | 선택(커밋 시) | 선택 | M6b 그대로 — 로컬 `--db-path` sidecar 직접 읽기/쓰기. `--db-path`는 `--source sqlite`에서만 필수. |
| `sqlite`(기본) | — | O | 생략 시 dry-run | 선택 | M6b 그대로 — `--write-file` 있으면 트랜잭션 커밋, 없으면 dry-run(그래도 전건 검증+리포트). |
| `supabase` | O | — | (해당 없음 — export는 항상 read-only) | (해당 없음) | PostgREST GET으로 라이브 미번역 findings를 직접 추출(§2). |
| `supabase` | — | O | **항상 거부(exit 2)** | **항상 필수** | 라이브 GET 스냅샷 대조 검증 → 통과 시 `--sql-output`에 UPDATE SQL만 기록(`mode: "sql_only"`). 라이브 write 경로 자체가 없다. |

- 접속값은 CLI 인자(`--supabase-url`/`--supabase-anon-key`) 우선, 없으면 env(`SUPABASE_URL`/`SUPABASE_ANON_KEY`) 폴백 — 둘 다 없으면 exit 2.
- exit code 규약은 sqlite/supabase 공통: `0`=성공, `2`=usage·IO·자격증명 누락, `3`=`--apply` 검증 실패(`ready=false`).
- `--supabase` 모드에서 `--write-file`을 주면 "라이브 직접 쓰기 없음 — `--sql-output` 후 SQL Editor로 적용하라"는 안내와 함께 exit 2로 즉시 거부한다(anon read-only 경계를 CLI 레벨에서도 강제).

---

## 2. 라이브 경계 — anon read-only · SQL Editor 경유 · 검증 규칙

- **anon key는 읽기 전용이다.** `public.findings`는 M3c(`003_findings_public_read.sql`)로 anon/authenticated SELECT만 재허용돼 있고 UPDATE/INSERT 권한은 없다(`raw_signals`는 여전히 전면 비공개). 그래서 `apply_translations_supabase()`는 애초에 라이브에 쓸 방법이 없다 — 검증만 라이브로 하고, 결과는 항상 `--sql-output` 파일(사람이 SQL Editor에서 실행)로만 나온다. `mode: "sql_only"`가 이 사실을 리포트에 명시한다.
- **전송 계층(`_supabase_get`)**: 단일 GET 헬퍼로 export/검증 양쪽이 공유한다. timeout 15초, 5xx/timeout에 한해 1회 재시도(`findings_supabase_append.py`의 재시도 계약과 동형), 실패는 `timeout`/예외 타입명/`http_NNN`만 반환하고 **anon key 값은 로그·예외·결과 어디에도 노출하지 않는다**(공개용 키라도 이 모듈의 기존 관례를 그대로 따름).
- **export 필터**: `finding_text_ko=eq.`(빈 문자열, 즉 미번역)만 뽑고 `published_date.desc,finding_id.asc`로 결정론 정렬, `limit=1000`. 총 건수는 별도 `Prefer: count=exact` 프로브(동일 엔드포인트에 `limit=1`)로 `Content-Range` 헤더를 파싱해 얻는다 — 이 프로브가 실패해도(-1 반환) export 자체는 실패시키지 않고 `count_unavailable: true`를 plan에 추가로 얹는다. 반환 건수가 정확히 1000이면 `truncated_possible: true`(§5 이월).
- **apply 검증**: plan의 `finding_id`를 정렬·중복 제거한 뒤 `finding_id=in.(...)`로 **20개 배치**(URL 길이 방어) GET해 라이브 원문(`finding_text`)·현재 번역 상태(`finding_text_ko`)를 가져오고, 이후 로직은 sqlite `--apply`와 **완전히 동일한 `_validate_item`**을 재사용한다 — 원문 byte 대조, `finding_id` 실존, 한글 1자 이상, `translation_method` 화이트리스트, 번역=원문 동일 금지, plan 내 중복 id 거부. 하나라도 실패하면 all-or-nothing으로 `errors`만 채워 반환하고 SQL 파일은 쓰지 않는다(`ready: false`, exit 3). 전건 통과 시에만 `_write_sql_file()`로 UPDATE 문을 만든다(`--overwrite` 없이는 라이브에 이미 번역된 행은 skip).

---

## 3. 주간 번역 Runbook (핵심 절차)

M6 §5.2의 sqlite 기반 절차를 라이브 소스로 치환한 것이 이번 M8의 실질 운영 변화다. 신규 유입 findings가 이제 로컬이 아니라 Supabase에 직접 쌓이므로(§4), 번역도 라이브를 대상으로 반복한다.

```
① CC 세션:
   python findings_translate.py --export --source supabase --output plan.json
   (미번역 findings 추출 — finding_text_ko='' 인 행만, read-only)

② Sonnet 에이전트(오프라인):
   plan.json 의 각 item 에 finding_text_ko/translation_method 를 채운다
   — M6c 번역 지침 그대로: QA 보고체, 전문용어 원어 병기
     (예: critical area → 청정구역(critical area)), 법조항 원문 유지,
     사실 왜곡 금지. 번역 후 dry-run(③)으로 즉시 검증한다.

③ CC 세션:
   python findings_translate.py --apply filled.json --source supabase \
       --sql-output out.sql
   (라이브 원문 byte 대조 등 §2 전체 규칙 통과 시에만 out.sql 산출,
    실패 시 errors 로 무엇이 틀렸는지 리포트 — 라이브에는 아무것도 쓰지 않음)

④ 사용자:
   out.sql 을 Supabase SQL Editor 에서 1회 실행(사람 게이트, 유일한 write)

⑤ CC 세션(read-only 재검증):
   python findings_translate.py --export --source supabase --output check.json
   translated 카운트/남은 미번역 건수를 재확인
```

이 다섯 단계 중 라이브에 실제로 쓰는 행위는 ④ 하나뿐이며, ①~③·⑤는 전부 read-only(anon key)다. 빈도(주간/필요 시)와 자동 트리거는 아직 미확정이다(§5 이월) — 사람이 필요할 때 세션을 열어 돌리는 반자동 상태가 이어진다. 웹(`/findings/`, M6d)은 `finding_text_ko`가 비어 있으면 원문+"AI 번역 — 원문 대조 권장" 대신 국문 우선 렌더가 아니라 원문을 그대로 보여주며 검토 큐를 자연스럽게 드러내므로, 번역이 며칠 밀리는 것 자체는 장애가 아니다 — 이 runbook은 지연을 줄이기 위한 운영 절차이지, 없으면 페이지가 깨지는 필수 게이트가 아니다.

---

## 4. M4 완전 가동 실증 기록 (2026-07-08~09)

- **raw_signals 직행 적재 라이브 확인**: M4a/M4b(`ENABLE_FINDINGS_SUPABASE_APPEND=true`)는 이전부터 repo Variable로는 켜져 있었지만 머지 이후 첫 daily run 관측이 없었다(v1.109-draft 재검증 당시 지적됨). 2026-07-08 19:56 UTC daily run이 그 첫 post-머지 실행이었고, 로그에 `FIND-1 raw_signals Supabase append 완료`가 실측됐다 — 전 소스에서 18건이 새로 append되어 `raw_signals` 카운트가 112 → 130으로 늘었고, 오류 0건이었다.
- **`ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true` 활성화**: 같은 날 2026-07-08 22:43 UTC, 사용자 지시로 (M4b가 권장했던 "raw-only 1주 관찰 후 전환"을 단축해) 두 번째 플래그까지 켰다. 이제부터의 daily run은 raw_signals뿐 아니라 `findings`(v2 taxonomy, `finding_text_ko`는 처음부터 미번역 상태)도 함께 Supabase에 직행 적재한다.
- **운영 지위 정리(§0의 근거)**: 위 두 사건으로 "신규 유입분의 정본 = Supabase, 로컬 sidecar = 7월 백필 스냅샷 + 로컬 개발용"이라는 구도가 실제로 성립했다 — 로컬 `grm-findings.sqlite3`는 더 이상 커지지 않고, 라이브 `findings` 테이블만 매일 늘어난다.
- **아직 관측하지 못한 것**: `_FINDINGS_APPEND` 활성화 이후 첫 daily run에서 실제로 findings 행이 append되는지는 이 문서 작성 시점 기준 다음 daily run을 기다려야 확인된다(§5 이월) — raw_signals 18건 append는 실측했지만, findings 쪽 첫 적재는 아직 관찰 전이다.

---

## 5. 이월

- **export 1000행 페이지네이션** — `_SUPABASE_EXPORT_LIMIT=1000`을 넘는 미번역 건이 쌓이면 `truncated_possible` 경고만 뜨고 나머지는 잘린다. 현재 물량(findings 24건 + 향후 매일 소량 유입)에서는 불필요하지만, 유입이 누적되면 `offset`/`Range` 기반 페이지네이션을 추가해야 한다.
- **번역 트리거 자동화** — §3 runbook의 실행 빈도·트리거는 여전히 사람이 판단해 세션을 여는 반자동이다. 스케줄드 태스크로 정례화할지는 M4 완전 가동 이후 유입 속도를 관찰한 뒤 재평가한다(M6 §6에서 이월된 항목 그대로 계승).
- **WL 장문 분할 추출 개선** — FDA Warning Letter 전문이 `finding_text` 1건으로 뭉쳐 집계 해상도를 낮추는 문제(M7 §4에서 이미 이월)는 이번 M8과 무관하게 `findings_extractors.py` 층의 별도 개선으로 계속 이월한다.
- **M4 findings 첫 적재 관찰** — `ENABLE_FINDINGS_SUPABASE_FINDINGS_APPEND=true` 활성화 후 첫 daily run에서 findings 행이 실제로 append되는지, v2 taxonomy로 분류되는지, `finding_text_ko` 미번역 상태로 정상 생성되는지를 다음 실행에서 확인해야 한다(§4).
