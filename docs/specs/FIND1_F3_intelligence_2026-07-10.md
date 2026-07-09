# FIND-1 F3 — 인텔리전스: 분석 서빙·대시보드 (초안, §0~§1만)

> 날짜: 2026-07-10
> 상태: F3a(집계 서빙 RPC) 코드 완료·테스트 green. F3b(웹 UI 계약)~F3d(실사관 프로파일)는 이 문서에
>   이어 쓴다 — 이번 초안은 §0(게이트)·§1(stats RPC 계약)만 확정한다.
> 코드 정본: `web/migrations/007_findings_stats_rpc.sql`(신규, `public.findings_stats()` +
>   `public.findings_firm_stats(p_firm text)`)
> 테스트 정본: `tests/test_findings_stats_rpc.py`(신규 18개, 오프라인 텍스트 계약 검증 — 실 Postgres
>   미접속)
> 전제 문서: `docs/GRM_Findings인텔리전스_전략로드맵_2026-07-07.md`(§ F2e·F3a 원안), F2b 커밋
>   (`fdc3a5b`, FDA 483/WL 외부 백필 수집기)

---

## 0. F3 게이트 — 하는 것 / 안 하는 것

F2 백필로 `findings` 행 수가 수백 → 수천 건으로 늘어나는 중이다(2026-07-10 기준 417건, 매일 증가).
그런데 F2 유입분은 대부분 영문 원문이고 번역(`finding_text_ko`)은 주간 수동 루프(M8/M9 runbook)로만
채워진다 — 즉 **볼륨이 커지는 속도가 번역이 따라잡는 속도보다 훨씬 빠르다.** 006 공개 게이트
(`finding_text_ko <> '' or finding_language = 'KO'`)는 이 미번역 다수를 anon/authenticated 의 row
단위 SELECT 에서 원천 차단한다 — 설계대로 동작하는 것이지 버그가 아니다(M9a §1).

문제는 F3(트렌드 대시보드·히트맵·업체 이력)가 바로 이 "게이트에 걸려 안 보이는" 다수를 포함한
**전량 집계**를 요구한다는 데 있다. 로드맵 F2e 가 이미 이 충돌의 해법을 정책으로 못 박아 두었다 —
"집계(트렌드·히트맵·카운트)는 번역 없이 전량 활용, row 노출은 최근 우선 번역·과거분은 점진/미번역
유지." F3a 는 이 정책을 실행 가능한 형태로 옮긴 것이다.

- **하는 것(F3a):** `public.findings_stats()` / `public.findings_firm_stats(p_firm text)` 두
  `security definer` RPC 함수를 신설해, 006 RLS 를 우회하되 **집계 숫자·서지 메타만** 반환한다.
  원문 지적 텍스트(`finding_text`/`finding_text_ko`)·URL(`evidence_url`)·raw 페이로드
  (`raw_json`/`row_json`)는 이 함수들의 반환 표면(jsonb 키 목록)에 존재하지 않는다 — "집계는
  공개 무해, 원문은 게이트 대상"이라는 F2e 판정을 코드로 강제한다.
- **하는 것(안전장치):** `set search_path = public` 고정(Supabase advisors 의 mutable
  search_path 경고 방지 — definer 함수가 호출자의 `search_path` 를 물려받아 스키마 스푸핑에
  노출되는 것을 막는다). `revoke all ... from public` 후 `grant execute ... to anon,
  authenticated` 로 명시적 화이트리스트 부여(001_reaction.sql 의 `private.sync_reaction_count()`
  회수 관례와 동형).
- **하지 않는 것(이 스프린트 범위 밖):** 웹 UI(대시보드·차트·필터) 구현 — F3b 에서 이어 씀.
  히트맵 페이지·업체 이력 페이지 자체 — F3b/F3c. 483 `inspector_names` 기반 실사관 프로파일 —
  F3d. 기존 마이그레이션(001~006)·`findings.js`·`findings_translate*.py` 등 기존 모듈 수정 — 전부
  불가침. 실 Supabase 적용(SQL Editor 실행) — 컨트롤 타워가 사람 경유로 별도 처리(§1 하단 검증
  SELECT 참고).

---

## 1. `007_findings_stats_rpc.sql` — 집계 서빙 RPC 계약

### 1.1 `public.findings_stats() returns jsonb`

인자 없음 · `language sql stable security definer set search_path = public`. 전체 스냅샷 하나를
반환한다. 반환 키(고정 — 웹이 계약으로 소비):

```
{
  "totals": {
    "findings": N,          -- public.findings 전체 행 수(게이트 무관 — F2 백필 포함 전량)
    "public_findings": N,   -- 006 게이트 통과분(= 006 정책과 동일 predicate 로 계산)
    "raw_signals": N,       -- public.raw_signals 전체 행 수
    "firms": N              -- public.findings 의 distinct firm_name 수
  },
  "by_agency_category": [{"agency": "...", "category_code": "...", "cnt": N}, ...],
  "by_month": [{"month": "YYYY-MM", "agency": "...", "cnt": N}, ...],   -- published_date 앞 7자
  "by_source": [{"source": "...", "cnt": N}, ...],
  "by_evidence": [{"evidence_level": "...", "cnt": N}, ...],
  "top_firms": [{"firm_name": "...", "cnt": N, "public_cnt": N}, ...]   -- cnt 상위 30, 동률은
                                                                         -- firm_name 오름차순
}
```

- `public_findings`/`top_firms[].public_cnt` 는 006 정책의 `using` 절과 **byte 단위로 동일한
  predicate**(`finding_text_ko <> '' or finding_language = 'KO'`)를 사용한다 — 집계가 게이트의
  실제 공개 기준과 어긋나면(예: 다른 조건을 쓰면) 대시보드 숫자가 실제 공개 상태와 모순되는
  신뢰 붕괴가 생기므로, 이 동일성을 계약으로 고정한다(`GateConsistencyTest` 로 회귀 검증).
- 빈 테이블에서도 유효한 jsonb 를 반환한다 — 배열 필드는 전부 `coalesce(..., '[]'::jsonb)`.
- 정렬은 각 배열마다 결정론(예: `by_agency_category` 는 agency·category_code 오름차순,
  `top_firms` 는 cnt 내림차순·firm_name 오름차순) — 같은 데이터라면 항상 같은 순서로 직렬화된다.

### 1.2 `public.findings_firm_stats(p_firm text) returns jsonb`

동일 `security definer`/`search_path` 규칙. `p_firm` 은 **정확 일치**(`=`, `ilike` 아님) —
웹이 `findings_stats()` 의 `top_firms[].firm_name` 값을 가공 없이 그대로 넘기는 계약이라
`ilike`/패턴 매칭이 필요 없고, 정확 일치가 인젝션·성능 양쪽에 안전하다. 반환 키:

```
{
  "firm_name": "...",                              -- 입력값 echo
  "totals": {"findings": N, "public_findings": N},
  "by_category": [{"category_code": "...", "cnt": N}, ...],
  "by_month": [{"month": "YYYY-MM", "cnt": N}, ...],
  "by_source": [{"source": "...", "cnt": N}, ...],
  "first_seen": "YYYY-MM-DD",                       -- min(published_date), 행 없으면 null
  "last_seen": "YYYY-MM-DD"                         -- max(published_date), 행 없으면 null
}
```

- 미존재 업체명을 넘기면 에러가 아니라 `totals` 가 모두 0(배열은 `[]`, `first_seen`/`last_seen`
  은 `null`)인 유효 jsonb 를 반환한다 — 웹 클라이언트가 별도 404 분기 없이 그대로 렌더할 수 있다.
- 함수 파라미터명은 `p_firm`(컬럼명 `firm_name` 과 접두사로 구분) — 004 마이그레이션에서
  plpgsql 변수명이 쿼리 테이블 별칭과 겹쳐 `record "..." is not assigned yet` 로 라이브 실패했던
  전례(`TaxonomyV2AlterMigrationTest.test_loop_variable_does_not_shadow_query_table_alias`)와
  동류 함정을 피한다. 다만 007 의 두 함수는 순수 `language sql`(plpgsql DO 블록·record 변수
  자체가 없음)이라 그 함정 경로가 애초에 존재하지 않는다 — `p_firm` 명명은 그럼에도 관례로
  접두사를 고정해 향후 이 함수를 plpgsql 로 확장할 일이 생겨도 안전하게 남긴다.

### 1.3 검증 (사람이 Supabase SQL Editor 에서 실행 — 이 세션은 실행하지 않음)

```sql
-- 빈 테이블에서도 유효한 jsonb(모든 배열 필드가 [])를 반환하는지 확인.
select public.findings_stats();
-- 미존재 업체명을 넘겨도 에러 없이 totals 0 의 유효 jsonb 를 반환하는지 확인.
select public.findings_firm_stats('__does_not_exist__');
```

### 1.4 이월 (F3b 에서 이어 씀)

- 웹(`/findings/`) 이 이 두 RPC 를 어떻게 호출·캐시·렌더하는지(PostgREST RPC 엔드포인트
  `/rest/v1/rpc/findings_stats` 형태 호출 계약, 폴링/빌드타임 스냅샷 여부)는 F3b 범위.
- `by_agency_category`/`by_month` 를 기관×카테고리×기간 히트맵으로 시각화하는 것은 F3b.
- 업체 지적 이력 페이지(entity 정규화 전제, F2d)는 F3c.
- 483 `inspector_names` 기반 실사관 프로파일은 F3d — `findings_firm_stats` 와 같은 패턴(집계
  전용 RPC)을 재사용할 가능성이 높지만 이 문서에서는 설계하지 않는다.
