-- 지식그래프 — 매일 새로 들어오는 지적을 자동 연결하고, 연결이 비는 것을 감시한다. 가산만(함수 4, 기존 RPC 불변).
--
-- 전제: 101(findings.fei) · 106(finding_clause) · 107(finding_term) 가 먼저 적용돼 있어야 한다.
-- 이 파일은 행을 쓰지 않는다. 적용 후 매일 `python kg_link_new.py`(grm-kg-link.yml)가 아래 (C) 를 부른다.
--
-- 설계: 백필과 일일 연결이 **같은 SQL 한 벌**을 쓴다 — 핵심 로직은 `_since(p_since)` 변형에 있고,
-- 101/106 의 인자 없는 백필 함수는 `_since('-infinity')` 를 부르는 얇은 껍데기로 바꾼다(시그니처·반환형 동일).
-- 그래서 백필 결과와 일일 증분 결과가 어긋날 수 없다(tests/test_kg_link_new.py 가 껍데기 관계를 고정).
-- 수집 경로(findings_supabase_append)는 **건드리지 않는다**: 적재 payload 에 fei 열을 넣으면 101 이 아직
-- 적용되지 않은 DB 에서 PostgREST 가 모든 INSERT 를 거부한다(적재 장애). DB 안에서 사후 채우는 쪽이 안전하다.

-- (A) fei — 483 행의 raw_signals.raw_json->>'fei_number' 를 채운다(숫자 7~10자리만, fei is null 인 행만).
create or replace function public.grm_backfill_findings_fei_since(p_since timestamptz)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_rows integer;
begin
  update public.findings f
     set fei = x.fei
    from (
      select r.raw_signal_id,
             (r.raw_json::jsonb ->> 'fei_number') as fei
        from public.raw_signals r
       where r.source = 'FDA 483'
         and r.raw_json like '{%'
         and (r.raw_json::jsonb ->> 'fei_number') ~ '^[0-9]{7,10}$'
    ) x
   where f.raw_signal_id = x.raw_signal_id
     and f.fei is null
     and f.ingested_at >= p_since;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_findings_fei_since(timestamptz) from public, anon, authenticated;

create or replace function public.grm_backfill_findings_fei()
returns integer
language sql
security invoker
set search_path = public
as $$ select public.grm_backfill_findings_fei_since('-infinity'::timestamptz); $$;
revoke all on function public.grm_backfill_findings_fei() from public, anon, authenticated;

-- (B) 조항 엣지 — 106 의 백필 본문과 동일, ingested_at 창만 추가.
create or replace function public.grm_backfill_finding_clause_since(p_since timestamptz)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_rows integer;
begin
  insert into public.finding_clause (finding_id, citation_key, citation_paragraph, confidence, basis, raw_ref)
  select distinct on (s.finding_id, s.citation_key, s.citation_paragraph)
         s.finding_id, s.citation_key, s.citation_paragraph, s.confidence, s.basis, s.raw
    from (
      select f.finding_id, x.raw, p.citation_key, p.citation_paragraph, p.confidence, p.basis
        from public.findings f
        cross join lateral (
          select jsonb_array_elements_text(f.cfr_refs) as raw where jsonb_typeof(f.cfr_refs) = 'array'
          union all
          select jsonb_array_elements_text(f.mfds_refs) where jsonb_typeof(f.mfds_refs) = 'array'
        ) x
        cross join lateral public.grm_parse_citation(x.raw) p
       where f.ingested_at >= p_since
    ) s
   order by s.finding_id, s.citation_key, s.citation_paragraph, s.confidence desc, s.raw
  on conflict do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_finding_clause_since(timestamptz) from public, anon, authenticated;

create or replace function public.grm_backfill_finding_clause()
returns integer
language sql
security invoker
set search_path = public
as $$ select public.grm_backfill_finding_clause_since('-infinity'::timestamptz); $$;
revoke all on function public.grm_backfill_finding_clause() from public, anon, authenticated;

-- (C) 일일 연결 진입점 — 최근 p_days 일에 들어온 지적의 fei·조항 엣지를 채운다(멱등).
-- 용어 엣지(finding_term)는 한국어 본문 매칭 엔진이 파이썬이라 kg_link_new.py 가 따로 쓴다.
create or replace function public.grm_link_new_findings(p_days integer default 3)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_since  timestamptz := now() - make_interval(days => greatest(coalesce(p_days, 3), 1));
  v_fei    integer;
  v_clause integer;
begin
  v_fei := public.grm_backfill_findings_fei_since(v_since);
  v_clause := public.grm_backfill_finding_clause_since(v_since);
  return jsonb_build_object('since', v_since, 'fei_filled', v_fei, 'clause_edges', v_clause);
end;
$$;
revoke all on function public.grm_link_new_findings(integer) from public, anon, authenticated;

-- (D) 연결 품질 감시 — [now()-p_days, now()-p_grace_hours] 에 들어온 지적 중 연결이 비어 있는 비율의 재료.
-- grace: 일일 연결·번역 배치가 한 바퀴 돌 시간을 준다(방금 들어온 행을 미연결로 세지 않는다).
-- 분모는 "연결할 수 있었던 것"만이다: fei = 원본에 유효 FEI 가 있는 483, 조항 = 신뢰도 0.8 이상으로
-- 파싱되는 ref 가 있는 지적, 용어 = 한국어 본문이 있는 지적. 임계 판정은 kg_link_health.py 가 한다.
create or replace function public.kg_link_health(p_days integer default 7, p_grace_hours integer default 36)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  with w as (
    select f.*
      from public.findings f
     where f.ingested_at >= now() - make_interval(days => greatest(coalesce(p_days, 7), 1))
       and f.ingested_at <  now() - make_interval(hours => greatest(coalesce(p_grace_hours, 36), 0))
  ),
  fei as (
    select count(*) as n, count(*) filter (where w.fei is not null) as filled
      from w
     where w.source = 'FDA 483'
       and exists (select 1 from public.raw_signals r
                    where r.raw_signal_id = w.raw_signal_id
                      and r.raw_json like '{%'
                      and (r.raw_json::jsonb ->> 'fei_number') ~ '^[0-9]{7,10}$')
  ),
  clause as (
    select w.source,
           count(*) as expected,
           count(*) filter (where exists (select 1 from public.finding_clause c
                                           where c.finding_id = w.finding_id and c.confidence >= 0.8)) as linked
      from w
     where exists (
             select 1
               from (select jsonb_array_elements_text(w.cfr_refs) as raw where jsonb_typeof(w.cfr_refs) = 'array'
                     union all
                     select jsonb_array_elements_text(w.mfds_refs) where jsonb_typeof(w.mfds_refs) = 'array') x
               cross join lateral public.grm_parse_citation(x.raw) p
              where p.confidence >= 0.8)
     group by w.source
  ),
  term as (
    select w.source,
           count(*) as texts,
           count(*) filter (where exists (select 1 from public.finding_term t where t.finding_id = w.finding_id)) as linked
      from w
     where coalesce(w.finding_text_ko, '') <> ''
     group by w.source
  )
  select jsonb_build_object(
    'window_days', greatest(coalesce(p_days, 7), 1),
    'grace_hours', greatest(coalesce(p_grace_hours, 36), 0),
    'fei', (select jsonb_build_object('fillable', n, 'filled', filled) from fei),
    'clause', coalesce((select jsonb_agg(jsonb_build_object('source', source, 'expected', expected, 'linked', linked)
                                         order by source) from clause), '[]'::jsonb),
    'term', coalesce((select jsonb_agg(jsonb_build_object('source', source, 'texts', texts, 'linked', linked)
                                       order by source) from term), '[]'::jsonb)
  );
$$;
revoke all on function public.kg_link_health(integer, integer) from public, anon, authenticated;
