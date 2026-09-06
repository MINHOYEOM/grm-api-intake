-- ============================================================================
-- 079 — 유사검색 RPC 2종 성능 수리 (동작 무변경 · 저장 tsvector 1열 + 평가 횟수만 교정)
--
-- 왜: `findings_similar_to` 가 **anon 의 statement_timeout 3초를 넘겨 500 을 뱉고 있었다.**
-- 2026-09-06 실측(anon 키, 프로덕션, 지적 145건 표본): **81/145(56%)가 3.2~3.5초에
-- `57014 canceling statement due to statement timeout`**. 웹은 그 오류를 조용히 삼켜
-- "유사 사례를 찾지 못했습니다"로 표시했으므로 사용자는 **유사 사례가 없다고 믿었다** —
-- 같은 문구가 수천 건인 지적(21 CFR 211.192 등)에서도 그랬다. 화면 쪽 수리(오류≠빈 결과)는
-- web/assets/findings.js 에서 따로 했고, 이 파일은 **서버가 3초 안에 끝나게** 만든다.
--
-- ★프로파일부터 떴다(explain analyze · findings 26,664행 / 공개 25,082행 · 기준 지적
--   `finding-4c3bcf9a3a56dc998d3728b9` · 한 호출 **10,831 ms**).
--   추측으로 "인덱스가 없나 / 결과 상한이 없나"를 의심했는데 **둘 다 아니었다**:
--     · `idx_findings_search_fts` 는 정상 사용 중(Bitmap Index Scan, 47 ms)
--     · 결과 상한은 있다(022 의 `limit 400` → `limit lim`)
--   진짜 원인은 **같은 값을 몇 번 계산하느냐**였고, 둘이다.
--
--   ── 원인 ①(지배적, ~8.5초): **질의어 tsquery 를 매치 행마다 다시 만들었다** ──────
--   이 RPC 의 질의는 기준 지적의 **본문 500자 그 자체**다. `simple` 사전은 불용어를 지우지
--   않으므로 `및`·`위해`·`있는` 같은 기능어까지 전부 검색어가 되고, 018 관례대로 ` & `→` | `
--   로 바꾸므로 **107개 OR**이 된다. 문서빈도 실측: `및`=11,214 · `적절한`=3,899 …
--   → 매치가 **17,992행(공개 코퍼스의 72%)**. `@@` 는 후보를 좁히지 못한다.
--   그런데 `tsq` CTE 는 **한 번만 참조**되므로 Postgres 가 인라인해 버렸고, 그 결과
--   `websearch_to_tsquery(500자) → replace → to_tsquery(107항 파싱)` 이 **17,992번** 돌았다.
--   (실행계획에 CASE 식이 `ts_rank(...)` 인자 자리에 통째로 박혀 있는 것으로 확인.)
--   → **`as materialized` 한 단어**로 1회 평가 고정: 10,831 ms → **2,311 ms**(단독 실측).
--
--   ── 원인 ②(~1.0초): **`to_tsvector()` 를 행마다 다시 만들었다** ─────────────────
--   GIN 인덱스는 바로 그 tsvector 를 이미 갖고 있지만 **표현식 인덱스는 값을 돌려줄 수 없어**
--   재계산된다. 11,579개 서로 다른 본문 기준 실측: to_tsvector 재계산 **1,009 ms** vs
--   저장된 tsvector 로 ts_rank **287 ms**.
--   → 생성열 `search_tsv` 를 만들고 **ts_rank 의 입력만** 그것으로 바꾼다.
--
--   합산 실측: **10,831 ms → 1,221 ms**(같은 기준 지적·같은 세션).
--
-- ── 왜 이 형태인가(재구조안을 버린 이유 — 실측이 갈랐다) ──────────────────────
-- `reps` 단계에서 **그룹당 1회만** ts_rank 하는 재구조안(17,992회 → 11,579회)도 만들어
-- 재봤다. 직관으로는 이쪽이 빨라야 한다. **실측은 아니었다** — 무거운 기준 지적에서
-- 1,476 ms vs 이 파일의 안 1,538 ms 로 **차이가 4%뿐**이다.
-- 이유: ts_rank 를 뒤로 미루려면 `search_tsv` 를 `matches` 에 실어 `reps` 의 정렬까지
-- 끌고 가야 하는데, 그러면 그 정렬이 1,728kB → **5,992kB** 로 불어난다(tsvector 는 크다).
-- 행마다 아끼려던 detoast 가 정렬 쪽으로 자리만 옮긴다.
-- 게다가 재구조안은 `groups.max(fts_rank)` 를 걷어내야 해서 **"같은 본문이면 rank 도
-- 같다"는 불변식에 결과가 의존**하게 된다(022 가 이미 md5 를 그룹 정체성으로 쓰므로 새
-- 가정은 아니지만, 굳이 늘릴 이유가 없다).
-- → **4%를 위해 증명 부담을 늘리지 않는다.** 022 의 집계식을 그대로 두고 **평가 횟수만**
--    고치는 이 파일의 안을 택했다.
--
-- ── 건드리지 않는 것 ────────────────────────────────────────────────────────
--   * **시그니처** — PostgREST 는 인자가 하나만 달라도 404 다(#681). 인자 증감 없음 =
--     진짜 치환이라 옛 시그니처 `drop` 도 불필요하다.
--   * `@@` 술어는 **종전 표현식 그대로** — `idx_findings_search_fts` 를 계속 타고
--     018 의 "byte 일치" 계약이 유지된다. 저장열은 **오직 ts_rank 의 입력**으로만 쓴다.
--     (그래서 GIN 인덱스를 새로 만들지 않는다 — 쓰기 비용·표류 위험 없음.)
--   * 공개 술어(006/010)·같은 문서 raw_signal_id 제외(붕괴 전)·전량 집계(022 F-01/F-02)·
--     대표 선정·창 400·클램프(1..50)·반환 키와 순서·`round(score,4)` — 전부 그대로.
--     신설 키 없음. **완전 동일**이 계약이다.
--   * `findings_search` 등 표현식을 쓰는 다른 RPC — 무수정.
--
-- 쓰기 경로 영향: findings 는 전부 **명시 컬럼** upsert/PATCH 다(`select *` 왕복 없음을
--   저장소 전수 확인). 생성열은 클라이언트가 보내지 않으므로 수집·번역 파이프 영향 없음.
--   `to_tsvector(regconfig,text)` 는 IMMUTABLE 이라 생성열로 적법(PG 17.6 확인).
--   테이블 42MB → 57MB. ALTER 는 테이블 재작성이라 짧은 ACCESS EXCLUSIVE 락을 잡는다.
--
-- 전제: 002 + 006/010 + 018 + 021/022 가 먼저 적용되어 있어야 한다. 멱등.
-- ============================================================================

-- ── ① 저장 tsvector(생성열). 018 인덱스 표현식과 byte 일치 ────────────────────
alter table public.findings
  add column if not exists search_tsv tsvector
  generated always as (
    to_tsvector('simple', coalesce(nullif(finding_text_ko, ''), finding_text))
  ) stored;

-- ── ② findings_similar(p_query, p_limit) — 사용자 질의 기준 ────────────────────
-- (지금 3초를 넘기는 것은 _to 쪽이다. 이 함수는 짧은 질의를 받아 매치 ~1,600행이라
--  아직 안 터지지만 **구조가 같아** 긴 문단을 붙여넣으면 같은 벽을 만난다 — 함께 고친다.)
create or replace function public.findings_similar(
  p_query text,
  p_limit int default 20
)
returns jsonb
language sql
stable
security definer
set search_path = public, extensions
as $$
  with input as (
    select
      left(btrim(coalesce(p_query, '')), 500) as q,
      greatest(1, least(coalesce(p_limit, 20), 50)) as lim
  ),
  -- ★[079] `as materialized` — 이 CTE 는 한 번만 참조되므로 종전엔 인라인되어
  --   `websearch_to_tsquery(500자)+to_tsquery(107항)` 이 **매치 행마다** 다시 돌았다
  --   (실측 지배 원인 · 약 8.5초). 값은 input 1행의 결정론 함수라 1회 평가로 고정해도
  --   결과가 바뀌지 않는다 — 바뀌는 것은 평가 **횟수**뿐이다.
  tsq as materialized (
    select case
      when websearch_to_tsquery('simple', i.q)::text = '' then null
      else to_tsquery('simple', replace(websearch_to_tsquery('simple', i.q)::text, ' & ', ' | '))
    end as tq
    from input i
  ),
  matches as (
    -- FTS 매치 전량(무절단) — 그룹 집계의 전역 진실 기반.
    select
      f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date,
      f.firm_name, f.category_code, f.evidence_level, f.review_status,
      coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as search_text,
      md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) as grp,
      -- ★[079] tsvector 는 생성열에서 읽는다(행마다 to_tsvector 재계산 ~1.0초 제거).
      --   search_tsv 는 위 표현식과 **동일 식의 generated always … stored** 라
      --   값이 같다(적용 시 전 26,664행 불일치 0건 실측). `@@` 술어는 표현식 그대로
      --   두어 idx_findings_search_fts 를 계속 탄다(018 byte 일치 계약).
      ts_rank(f.search_tsv, t.tq) as fts_rank
    from public.findings f, input i, tsq t
    where char_length(i.q) >= 2
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
      and t.tq is not null
      and to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) @@ t.tq
  ),
  groups as (
    select grp,
      count(distinct raw_signal_id) as dup_documents,
      count(*) as dup_findings,
      max(fts_rank) as best_rank
    from matches
    group by grp
  ),
  reps as (
    select distinct on (grp) *
    from matches
    order by grp, published_date desc, finding_id asc
  ),
  window_reps as (
    -- 절단은 그룹 공간에서, 결정론 타이브레이크와 함께.
    select r.finding_id, r.raw_signal_id, r.source, r.agency, r.published_date,
      r.firm_name, r.category_code, r.evidence_level, r.review_status, r.search_text,
      g.dup_documents, g.dup_findings, g.best_rank
    from reps r
    join groups g using (grp)
    order by g.best_rank desc, r.published_date desc, r.finding_id asc
    limit 400
  ),
  scored as (
    select w.*,
      (0.6 * similarity(w.search_text, i.q) + 0.4 * w.best_rank) as group_score
    from window_reps w, input i
  )
  select jsonb_build_object(
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'finding_id', finding_id,
          'raw_signal_id', raw_signal_id,
          'source', source,
          'agency', agency,
          'published_date', published_date,
          'firm_name', firm_name,
          'category_code', category_code,
          'evidence_level', evidence_level,
          'review_status', review_status,
          'text', search_text,
          'score', round(group_score::numeric, 4),
          'dup_documents', dup_documents,
          'dup_findings', dup_findings
        )
        order by group_score desc, published_date desc, finding_id asc
      )
      from (
        select * from scored
        order by group_score desc, published_date desc, finding_id asc
        limit (select lim from input)
      ) top_items
    ), '[]'::jsonb)
  );
$$;

revoke all on function public.findings_similar(text, int) from public;
grant execute on function public.findings_similar(text, int) to anon, authenticated;

-- ── ③ findings_similar_to(p_finding_id, p_limit) — 이번 장애 당사자 ──────────────
create or replace function public.findings_similar_to(
  p_finding_id text,
  p_limit int default 5
)
returns jsonb
language sql
stable
security definer
set search_path = public, extensions
as $$
  with base as (
    select f.finding_id, f.raw_signal_id,
           coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as txt
    from public.findings f
    where f.finding_id = p_finding_id
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
  ),
  input as (
    select b.finding_id, b.raw_signal_id,
           left(btrim(b.txt), 500) as q,
           greatest(1, least(coalesce(p_limit, 5), 50)) as lim
    from base b
  ),
  -- ★[079] `as materialized` — 이 CTE 는 한 번만 참조되므로 종전엔 인라인되어
  --   `websearch_to_tsquery(500자)+to_tsquery(107항)` 이 **매치 행마다** 다시 돌았다
  --   (실측 지배 원인 · 약 8.5초). 값은 input 1행의 결정론 함수라 1회 평가로 고정해도
  --   결과가 바뀌지 않는다 — 바뀌는 것은 평가 **횟수**뿐이다.
  tsq as materialized (
    select case
      when websearch_to_tsquery('simple', i.q)::text = '' then null
      else to_tsquery('simple', replace(websearch_to_tsquery('simple', i.q)::text, ' & ', ' | '))
    end as tq
    from input i
  ),
  matches as (
    -- 같은 문서(raw_signal_id) 제외는 종전대로 **집계 이전**에 적용 — "기준 외 N개 문서" 의미.
    select
      f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date,
      f.firm_name, f.category_code, f.evidence_level, f.review_status,
      coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as search_text,
      md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) as grp,
      -- ★[079] tsvector 는 생성열에서 읽는다(행마다 to_tsvector 재계산 ~1.0초 제거).
      --   search_tsv 는 위 표현식과 **동일 식의 generated always … stored** 라
      --   값이 같다(적용 시 전 26,664행 불일치 0건 실측). `@@` 술어는 표현식 그대로
      --   두어 idx_findings_search_fts 를 계속 탄다(018 byte 일치 계약).
      ts_rank(f.search_tsv, t.tq) as fts_rank
    from public.findings f, input i, tsq t
    where char_length(i.q) >= 2
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
      and f.raw_signal_id is distinct from i.raw_signal_id
      and f.finding_id <> i.finding_id
      and t.tq is not null
      and to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) @@ t.tq
  ),
  groups as (
    select grp,
      count(distinct raw_signal_id) as dup_documents,
      count(*) as dup_findings,
      max(fts_rank) as best_rank
    from matches
    group by grp
  ),
  reps as (
    select distinct on (grp) *
    from matches
    order by grp, published_date desc, finding_id asc
  ),
  window_reps as (
    select r.finding_id, r.raw_signal_id, r.source, r.agency, r.published_date,
      r.firm_name, r.category_code, r.evidence_level, r.review_status, r.search_text,
      g.dup_documents, g.dup_findings, g.best_rank
    from reps r
    join groups g using (grp)
    order by g.best_rank desc, r.published_date desc, r.finding_id asc
    limit 400
  ),
  scored as (
    select w.*,
      (0.6 * similarity(w.search_text, i.q) + 0.4 * w.best_rank) as group_score
    from window_reps w, input i
  )
  select jsonb_build_object(
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'finding_id', finding_id,
          'raw_signal_id', raw_signal_id,
          'source', source,
          'agency', agency,
          'published_date', published_date,
          'firm_name', firm_name,
          'category_code', category_code,
          'evidence_level', evidence_level,
          'review_status', review_status,
          'text', search_text,
          'score', round(group_score::numeric, 4),
          'dup_documents', dup_documents,
          'dup_findings', dup_findings
        )
        order by group_score desc, published_date desc, finding_id asc
      )
      from (
        select * from scored
        order by group_score desc, published_date desc, finding_id asc
        limit (select lim from input)
      ) top_items
    ), '[]'::jsonb)
  );
$$;

revoke all on function public.findings_similar_to(text, int) from public;
grant execute on function public.findings_similar_to(text, int) to anon, authenticated;

-- 검증(적용 시 실행 — 실측치는 PR 본문에 첨부):
-- ①생성열 정합: 표현식과 다른 행이 0건이어야 한다.
--   select count(*) from public.findings
--    where search_tsv is distinct from
--          to_tsvector('simple', coalesce(nullif(finding_text_ko,''), finding_text));
-- ②동작 무변경(068 관례): 적용 **전** anon 으로 성공하던 호출 전량의 응답 md5 가
--   적용 **후**와 일치해야 한다. 종전 타임아웃(500)이던 호출은 비교 대상이 아니라
--   **새로 살아나는** 것이므로, 그쪽은 옛 정의를 timeout 을 올려 직접 돌린 결과와 대조한다.
-- ③F-01 회귀: findings_similar('무균 배지모사시험 최악조건',20) 의 각 item 에 대해
--   md5(text) 전역 count(distinct raw_signal_id) 와 dup_documents 가 전부 일치.
-- ④F-02 회귀: findings_similar('무균',50) → 50건. limit 클램프 999→50 · -5→1.
-- ⑤021 의미 유지: findings_similar_to(<base>,5) 결과에 기준 raw_signal_id 부재.
-- ⑥빈/짧은 질의 {"items": []} · 미존재/비공개 finding_id 무구분 빈 결과.

-- ── 적용 실측(2026-09-06 프로덕션, anon 키 · 지적 145건 동일 표본) ──────────────
--   성공률   64/145 (44%)  →  **142/145**  · 재실행(warm) 시 **145/145**
--   지연     중앙값 2,170ms →  **957ms** · p95 1,901ms · 최대 3,169ms
--   ★동작 무변경 증명: 적용 전 200 이던 **64건 전량의 응답 md5 가 적용 후와 일치**
--     (변경 0 · 회귀 0). 적용 전 타임아웃이던 78건은 새로 살아난 것이라 md5 대조 대상이
--     아니므로, 그중 무거운 4건은 **옛 정의를 timeout 올려 직접 돌려** 대조했다 —
--     4/4 md5 일치. 즉 살아난 결과도 옛 정의가 (시간만 있었다면) 냈을 그 답이다.
--   ★findings_similar 도 같은 방식으로 대조했다(이 함수는 적용 전 타임아웃이 아니라
--     before 스냅샷을 anon 으로 남길 수 없었으므로, 옛 정의를 ad-hoc 으로 돌려 대조):
--     '무균'(50) · '무균 배지모사시험 최악조건'(20) · 'aseptic media fill'(20) ·
--     '데이터 완전성 감사추적'(10) · 'cleaning validation residue'(5) · ''(20) · '가'(20) ·
--     '환경모니터링 등급 A 구역 초과'(999→50) · 'stability chamber'(-5→1) = **9/9 md5 일치**.
--   F-01: 20/20 item 의 dup_documents = 전역 진실(불일치 0) · F-02: 50건·클램프 999→50/-5→1
--   자기 문서 누출 0 · 빈/짧은/미존재/형식오류 전부 {"items": []} 무구분.
--   생성열 정합: 26,664행 중 표현식과 불일치 **0건**.
--
-- ⚠️ 남은 여유: 가장 무거운 지적(매치 18k행)은 서버 기준 약 2.1~2.2초로 3초 예산의
--   **1.4배**뿐이다. 콜드 캐시나 동시 클릭이 겹치면 여전히 넘길 수 있다(실측: 같은 카드
--   3개를 동시에 열면 타임아웃 재현). 화면이 이제 오류를 오류로 말하고 재시도를 주므로
--   사용자에게 거짓말을 하지는 않지만, 더 줄이려면 **매치 집합 자체를 줄여야** 한다
--   (질의어에서 초고빈도 기능어를 떨어내는 방향 — 그건 결과가 바뀌는 설계 변경이라
--    별도 평가·별도 PR 로 다룬다. 이 PR 의 계약은 "동작 무변경"이다).
