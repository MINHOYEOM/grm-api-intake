-- [FIND-1] 유사검색 RPC 2종 사실값 수리 — Codex 감사 F-01/F-02 (2026-07-15).
--
-- ★근본 원인 = **절단 후 붕괴(collapse-after-truncation)**. 018/021 은 후보를
--   `order by ts_rank limit 200` 으로 자른 **뒤** md5 그룹을 셌다. 그 결과:
--   F-01: "동일 문구 N개 문서" 배지가 200 창 안에 든 부분집합만 세어 **사실보다 작다**
--         (재현: findings_similar('무균 배지모사시험 최악조건',20) → 20건 중 6건 불일치,
--          최대 8→19. 컨트롤타워 독립 재현으로 CONFIRMED).
--   F-02: 200 행이 중복 붕괴로 47 그룹이 되면 limit 50 을 못 채운다(underfill —
--         findings_similar('무균',50) → 47건. 적격 그룹은 429). 021 은 base 에 따라 발현.
--   또한 절단 지점 동률에 secondary key 가 없어 창 경계가 비결정적이었다(F-02/F-12).
--
-- ★수리 = **붕괴 후 절단(truncation-after-collapse)** + 전 절단 결정론 타이브레이크:
--   ①matches: FTS 매치 전량(무절단)에서 md5 그룹 집계 — **동일 텍스트면 tsvector 도
--     동일해 매치 여부가 같으므로, 매치 집합 내 그룹 카운트 = corpus 전역 진실**이다.
--     (021 은 기준 문서 제외 후 집계 = "기준 외 N개 문서" 의미 유지 — 종전과 동일.)
--   ②reps: 그룹당 대표 1행(published_date desc, finding_id asc — 종전 관례).
--   ③window: **그룹(대표) 공간에서** best_rank 기준 상위 400 절단(+타이브레이크).
--     lim 상한이 50 이므로 창 400 이면 underfill 은 "적격 그룹 자체가 부족한 경우"에만
--     발생한다(그건 결함이 아니라 사실).
--   ④similarity() 재랭킹은 대표 ≤400 행에만 계산(비용 종전 동급 — 동일 텍스트 그룹은
--     구성원 전원의 sim/rank 가 같으므로 대표만 계산해도 정보 손실이 없다).
--
-- 안전 계약 불변(018/021 과 동일 — 반환 키 목록·공개 술어(010)·같은 문서 raw_signal_id
--   기준 제외(021, 붕괴 전)·evidence_url/raw 미반환·클램프). 검색 표현식은
--   idx_findings_search_fts 와 byte 일치 유지. 이 파일은 함수 2개 교체뿐(멱등).

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
  tsq as (
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
      ts_rank(
        to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)),
        t.tq
      ) as fts_rank
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
  tsq as (
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
      ts_rank(
        to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)),
        t.tq
      ) as fts_rank
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

-- 검증(라이브 적용 시):
-- ①F-01 회귀: findings_similar('무균 배지모사시험 최악조건',20) 의 각 item 에 대해
--   md5(text) 전역 count(distinct raw_signal_id) 와 dup_documents 가 전부 일치해야 한다.
-- ②F-02 회귀: findings_similar('무균',50) → 50건(적격 429그룹). limit 클램프 999→50·-5→1.
-- ③021 의미 유지: findings_similar_to(<base>,5) 결과에 기준 raw_signal_id 부재,
--   dup_documents 는 기준 문서 제외 전역 진실과 일치.
-- ④빈/짧은 질의 {"items": []} · 미존재/비공개 finding_id 무구분 빈 결과(018/021 종전 계약).
