-- 지식그래프 — 본문이 글자 그대로 인용한 조항을 finding_clause 에 보탠다(가산만, 기존 함수·RPC 불변).
-- 파이썬 기준: kg_clause_text.py. 이 파일은 행을 쓰지 않는다 — 적용 후 select grm_backfill_finding_clause_text(); 1회.
-- 대상: 483·경고서한 `21 CFR 211.22`(키는 106 과 동일) / EU·MHRA NCR 의 EU GMP Annex 1·11·16 /
--       Health Canada 의 GUI-NNNN 지침 문서. 본문에 없는 조항(HC C.02.xxx 등)은 연결하지 않는다.
-- 기존 행(106 의 cfr_refs 경로)이 있으면 건드리지 않는다(on conflict do nothing).

create or replace function public.grm_text_citations(p_source text, p_text text)
returns table (citation_key text, citation_paragraph text, confidence numeric, basis text)
language sql
immutable
set search_path = public
as $$
  select distinct on (k, para) k, para, c, b from (
    select '21 CFR ' || m[1] || '.' || m[2] as k, lower(m[3]) as para, 0.95::numeric as c, 'cfr_text'::text as b
      from regexp_matches(coalesce(p_text, ''), '\y21\s*CFR\s*(?:Part\s*)?(\d{1,4})\.(\d{1,4})((?:\([A-Za-z0-9]+\))*)', 'gi') as m
     where p_source in ('FDA 483', 'FDA Warning Letter')
    union all
    select 'EU GMP Annex ' || case when lower(m[1]) = 'i' then '1' else m[1] end, '', 0.9::numeric, 'eu_gmp_annex_text'
      from regexp_matches(coalesce(p_text, ''), '(?<!\()\yannex\s*(1|i|11|16)\y(?!\))', 'gi') as m
     where p_source in ('EU GMP NCR (EudraGMDP)', 'MHRA GMP NCR')
    union all
    select 'Canada GUI-' || m[1], '', 0.9::numeric, 'hc_guidance_text'
      from regexp_matches(coalesce(p_text, ''), '\yGUI-?\s?(\d{4})\y', 'gi') as m
     where p_source = 'Health Canada Inspection'
  ) s(k, para, c, b)
  order by k, para
$$;
revoke all on function public.grm_text_citations(text, text) from public, anon, authenticated;

create or replace function public.grm_backfill_finding_clause_text_since(p_since timestamptz)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_rows integer;
begin
  insert into public.finding_clause (finding_id, citation_key, citation_paragraph, confidence, basis, raw_ref)
  select f.finding_id, p.citation_key, p.citation_paragraph, p.confidence, p.basis, p.citation_key
    from public.findings f
    cross join lateral public.grm_text_citations(f.source, f.finding_text) p
   where f.ingested_at >= p_since
  on conflict do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_finding_clause_text_since(timestamptz) from public, anon, authenticated;

create or replace function public.grm_backfill_finding_clause_text()
returns integer
language sql
security invoker
set search_path = public
as $$ select public.grm_backfill_finding_clause_text_since('-infinity'::timestamptz); $$;
revoke all on function public.grm_backfill_finding_clause_text() from public, anon, authenticated;
