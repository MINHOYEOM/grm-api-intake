-- 지식그래프 — 식약처 규칙명 없는 별표 참조(`[별표 1] 제7.1호`, `[별표 1] 7.1`)에 규칙명을 **출처 문서 근거로만** 보강한다.
--
-- 가산만 한다: 함수 3(grm_mfds_doc_rule · grm_backfill_finding_clause_mfds_rule_since · grm_backfill_finding_clause_mfds_rule)
-- + grm_link_new_findings(108)에 결과 키 1개 추가(기존 키·반환형 jsonb 불변). 106/108 의 함수 본문·테이블은 건드리지 않는다.
-- 이 파일은 행을 쓰지 않는다. 적용 후 `select grm_backfill_finding_clause_mfds_rule();` 를 1회 호출해야 채워진다.
--
-- 근거(2026-11-05 실측): 규칙명 없는 별표 참조 1,259건(1,087 지적)은 지적 표 한 줄을 그대로 옮긴 것이라 규칙명이 없다.
-- 그러나 같은 출처 문서(raw_signals.raw_json.attachment_text)의 "실사 목적" 문장이 규칙과 별표를 함께 적는다:
--   "｢의약품 등의 안전에 관한 규칙｣(총리령) 제48조의4 및 [별표 1]의 제조 및 품질관리기준에 따라 …"
-- 이 문장에서 (규칙명, 별표 번호) 쌍을 읽고, **한 문서에서 그 별표 번호에 규칙이 정확히 하나만** 묶일 때만 채운다.
-- 같은 refs 배열·같은 문서의 다른 지적에는 규칙명이 명시된 항목이 한 건도 없어(실측 0) 그 경로는 쓰지 않는다.
-- 지적 본문 속 규칙명 언급("…안전에 관한 규칙")은 어느 별표인지 묶이지 않아 근거로 쓰지 않는다. 별표 8(행정처분)·2 등
-- 문서에 규칙-별표 문장이 없는 번호는 추측하지 않고 지금처럼 `[규칙 미상]` 으로 둔다.
--
-- 보수 장치: ref 가 `[별표 N] 제X.Y호 [목] [, 제…호]` 형태(strict)일 때만. `[별표 1] … [별표 1의2] …` 처럼 한 문자열에
-- 별표가 둘이면 106 파서가 뒤쪽 호를 앞 별표에 붙이므로(오결합) 제외한다. 조·본문이 섞인 잘린 표기도 제외.
-- confidence 0.9 (명시 규칙명 0.95 보다 낮게 — 문서에서 추론), basis 'mfds_byeolpyo_doc'. citation_key 는 명시 표기와 같은
-- 형식('의약품 등의 안전에 관한 규칙 별표1 제7.1호')이라 같은 조항으로 합쳐진다. 기존 '[규칙 미상]' 행(0.7)은 지우지 않는다.
--
-- ★004/009 함정: 변수명은 v_ 접두, 컬럼과 겹치지 않는다.

create or replace function public.grm_mfds_doc_rule(p_doc text, p_tbl text)
returns text
language sql
immutable
set search_path = public
as $$
  select case when count(distinct t.law) = 1 then min(t.law) end
    from (
      select regexp_replace(btrim(m[1]), '\s+', ' ', 'g') as law,
             regexp_replace(m[2], '\s+', '', 'g') as tbl
        from regexp_matches(coalesce(p_doc, ''),
               '[｢「]([^｣」]{2,40})[｣」][^｢｣「」\[]{0,60}?\[별표\s*(\d+(?:의\d+)?)\]', 'g') as m
    ) t
   where t.tbl = p_tbl
     and t.law ~ '(규칙|규정)$'
$$;
revoke all on function public.grm_mfds_doc_rule(text, text) from public, anon, authenticated;

create or replace function public.grm_backfill_finding_clause_mfds_rule_since(p_since timestamptz)
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
         s.finding_id, s.citation_key, s.citation_paragraph, 0.90::numeric, 'mfds_byeolpyo_doc', s.raw
    from (
      select d.finding_id, x.raw, p.citation_key, p.citation_paragraph
        from (
          select f.finding_id, f.mfds_refs,
                 case when r.raw_json like '{%' then (r.raw_json::jsonb) ->> 'attachment_text' end as doc_text
            from public.findings f
            join public.raw_signals r on r.raw_signal_id = f.raw_signal_id
           where f.source = 'MFDS'
             and jsonb_typeof(f.mfds_refs) = 'array'
             and f.ingested_at >= p_since
        ) d
        cross join lateral jsonb_array_elements_text(d.mfds_refs) x(raw)
        cross join lateral (
          select public.grm_mfds_doc_rule(d.doc_text, (regexp_match(x.raw, '^\s*\[별표\s*(\d+(?:의\d+)?)\]'))[1]) as law
        ) w
        cross join lateral public.grm_parse_citation(w.law || ' ' || x.raw) p
       where w.law is not null
         and p.basis = 'mfds_byeolpyo'
         and x.raw ~ '^\s*\[별표\s*\d+(?:의\d+)?\]\s*(?:제\s*)?\d+(?:[.의]\d+)*\s*호?(?:\s*[가-하]목)?(?:\s*(?:,|및)\s*제\s*\d+(?:[.의]\d+)*\s*호(?:\s*[가-하]목)?)*\s*$'
    ) s
   order by s.finding_id, s.citation_key, s.citation_paragraph, s.raw
  on conflict do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_finding_clause_mfds_rule_since(timestamptz) from public, anon, authenticated;

create or replace function public.grm_backfill_finding_clause_mfds_rule()
returns integer
language sql
security invoker
set search_path = public
as $$ select public.grm_backfill_finding_clause_mfds_rule_since('-infinity'::timestamptz); $$;
revoke all on function public.grm_backfill_finding_clause_mfds_rule() from public, anon, authenticated;

-- 일일 연결(108)에 같은 보강을 얹는다 — 기존 결과 키는 그대로, 'clause_mfds_rule' 만 추가.
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
  v_rule   integer;
begin
  v_fei := public.grm_backfill_findings_fei_since(v_since);
  v_clause := public.grm_backfill_finding_clause_since(v_since);
  v_rule := public.grm_backfill_finding_clause_mfds_rule_since(v_since);
  return jsonb_build_object('since', v_since, 'fei_filled', v_fei, 'clause_edges', v_clause,
                            'clause_mfds_rule', v_rule);
end;
$$;
revoke all on function public.grm_link_new_findings(integer) from public, anon, authenticated;
