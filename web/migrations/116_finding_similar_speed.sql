-- 114 유사 지적 엣지 계산 속도 수리 — 함수 본문 교체 + 좁은 문장 표 1개. 결과(엣지 집합)는 114 와 같다.
--
-- ★실측(2026-11-05, 114 적용 후): grm_link_finding_similar 40건 = 32.4 s(건당 0.8 s) → 백필 5.6 h.
--   explain(analyze) 로 뜬 병목 둘:
--   ① `%` 임계 0.25 가 낮아 GIN 이 기준당 후보 ~2,400 행을 돌려주고 그중 ~310 행이 재검사를 통과했다(재검사 = 넓은 findings 힙 읽기).
--      최종 점수 = 0.6×sim + 0.4×fts >= 0.40 이면 sim >= (0.40 - 0.4×fts)/0.6. ts_rank(fts)의 상한은 작다:
--      483·HC·경고서한·MFDS 120기준 후보 15,275쌍 실측 최댓값 0.076(앞선 60기준 36,490쌍도 0.073).
--      점수 0.40 이상으로 채택된 4,341쌍의 최소 sim = 0.623. 그래서 임계를 (p_min_score - 0.4×0.16)/0.6 = 0.56 으로 올린다
--      (fts 상한을 실측의 2배인 0.16 으로 잡은 안전 마진. p_min_score 를 올리면 임계도 같이 오른다, 내리면 내려간다).
--   ② 후보 행이 같은 문장의 중복(HC 상투구는 한 문장이 130문서)이고, 힙 행이 넓어 재검사가 느렸다(기준당 ~0.1 s).
--      → 고유 문장 표 finding_similar_text(grp, ftype, txt) 를 따로 두고 거기에 trigram GIN 을 건다(행 ~1.6만, 좁음).
--      후보를 문장 단위로 뽑은 뒤 그 문장을 가진 지적(문서별 최신 2건)만 findings 에서 찾는다.
--      ts_rank 는 마지막에 대표 1행에만 계산한다(후보 수백 행 → 기준당 수십 행).
-- 점수식·하한·상위 5·같은 문서/자기 제외·같은 유형·같은 문장 접기(docs=N)·basis 형식은 114 와 같다. 표 finding_similar 는 불변.
--
-- 문장 표 유지: grm_finding_similar_sync_texts() 가 아직 없는 문장만 보탠다(grm_link_finding_similar 가 매 호출 맨 앞에서 부른다).
-- 번역이 바뀌어 사라진 옛 문장 행은 남지만 그 문장을 가진 지적이 없어 후보에서 자연히 탈락한다.

create table if not exists public.finding_similar_text (
  grp   text not null,
  ftype text not null,
  txt   text not null,
  primary key (grp, ftype)
);
create index if not exists idx_finding_similar_text_trgm
  on public.finding_similar_text using gin (txt extensions.gin_trgm_ops);
alter table public.finding_similar_text enable row level security;
revoke all on table public.finding_similar_text from public, anon, authenticated;

create or replace function public.grm_finding_similar_sync_texts()
returns integer
language plpgsql
volatile
security invoker
set search_path = public, extensions
as $$
declare
  v_rows integer;
begin
  insert into public.finding_similar_text (grp, ftype, txt)
  select distinct on (md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)), public.grm_similar_ftype(f.source, f.document_type))
         md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)),
         public.grm_similar_ftype(f.source, f.document_type),
         coalesce(nullif(f.finding_text_ko, ''), f.finding_text)
    from public.findings f
   where f.scope_status = 'ok' and (f.finding_text_ko <> '' or f.finding_language = 'KO')
     and public.grm_similar_ftype(f.source, f.document_type) is not null
     and not exists (select 1 from public.finding_similar_text t
                      where t.grp = md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text))
                        and t.ftype = public.grm_similar_ftype(f.source, f.document_type))
  on conflict do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_finding_similar_sync_texts() from public, anon, authenticated;

-- 최초 채움(고유 문장 ~1.6만 행).
select public.grm_finding_similar_sync_texts();

-- ── 계산 본문 교체(시그니처·반환 표 동일) ─────────────────────────────────────────────────
create or replace function public.grm_finding_similar_compute(
  p_ids       text[],
  p_min_score numeric default 0.40,
  p_per       integer default 5
)
returns table (finding_id text, similar_id text, score numeric, rank integer, basis text)
language plpgsql
volatile
security invoker
set search_path = public, extensions
as $$
#variable_conflict use_column
begin
  -- 점수 = 0.6×sim + 0.4×fts, fts <= 0.16(실측 최댓값 0.076 의 2배) 이므로 채택 쌍은 sim >= (p_min_score-0.064)/0.6.
  perform set_config('pg_trgm.similarity_threshold',
                     greatest(0.25, (coalesce(p_min_score, 0.40) - 0.064) / 0.6)::numeric(5,4)::text, true);
  return query
  with base as materialized (
    select f.finding_id as fid, f.raw_signal_id as rid, f.firm_key as firm,
           public.grm_similar_ftype(f.source, f.document_type) as ftype,
           coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as txt
      from public.findings f
     where f.finding_id = any (p_ids)
       and f.scope_status = 'ok' and (f.finding_text_ko <> '' or f.finding_language = 'KO')
       and public.grm_similar_ftype(f.source, f.document_type) is not null
  ),
  bg as materialized (
    select distinct on (md5(b.txt), b.ftype)
           md5(b.txt) as grp, b.ftype, left(btrim(b.txt), 500) as q,
           case when websearch_to_tsquery('simple', left(btrim(b.txt), 500))::text = '' then null
                else to_tsquery('simple', replace(websearch_to_tsquery('simple', left(btrim(b.txt), 500))::text, ' & ', ' | ')) end as tq
      from base b
     where char_length(left(btrim(b.txt), 500)) >= 2
  ),
  -- 후보 문장(고유) — 좁은 표의 trigram GIN
  cg as materialized (
    select bg.grp as bgrp, bg.ftype as bftype, t.grp as cgrp, similarity(t.txt, bg.q) as sim
      from bg
      cross join lateral (
        select s.grp, s.txt from public.finding_similar_text s
         where s.ftype = bg.ftype and s.txt % bg.q
      ) t
  ),
  -- 후보 문장을 가진 지적(문서별 최신 1건) — findings 전체를 한 번 훑어 md5 로 맞춘다
  gm as materialized (
    select distinct on (c.bgrp, c.cgrp, m.raw_signal_id)
           c.bgrp, c.cgrp, m.finding_id as cid, m.raw_signal_id as crid, m.published_date as cpub, m.firm_key as cfirm
      from cg c
      join (select f.finding_id, f.raw_signal_id, f.published_date, f.firm_key,
                   md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) as mg,
                   public.grm_similar_ftype(f.source, f.document_type) as mt
              from public.findings f
             where f.scope_status = 'ok' and (f.finding_text_ko <> '' or f.finding_language = 'KO')) m
        on m.mg = c.cgrp and m.mt = c.bftype
     order by c.bgrp, c.cgrp, m.raw_signal_id, m.published_date desc nulls last, m.finding_id
  ),
  gmr as materialized (
    select g.*, row_number() over (partition by g.bgrp, g.cgrp order by g.cpub desc nulls last, g.cid) as rk,
           count(*) over (partition by g.bgrp, g.cgrp) as n_docs
      from gm g
  ),
  pairs as materialized (
    select distinct on (b.fid, g.cgrp)
           b.fid, g.cid, g.cgrp, g.cpub, c.sim, bg.tq,
           (g.cgrp = md5(b.txt)) as same_text,
           (coalesce(b.firm, '') <> '' and b.firm = g.cfirm) as same_firm,
           g.n_docs - (select count(*) from gm x where x.bgrp = g.bgrp and x.cgrp = g.cgrp and x.crid = b.rid) as docs
      from base b
      join bg on bg.grp = md5(b.txt) and bg.ftype = b.ftype
      join gmr g on g.bgrp = bg.grp
      join cg c on c.bgrp = g.bgrp and c.cgrp = g.cgrp
     where g.crid is distinct from b.rid and g.cid <> b.fid and g.rk <= 2
     order by b.fid, g.cgrp, g.rk
  ),
  scored as (
    select p.fid, p.cid, p.cgrp, p.cpub, p.sim, p.same_text, p.same_firm, p.docs,
           coalesce(ts_rank(f.search_tsv, p.tq), 0) as fr
      from pairs p join public.findings f on f.finding_id = p.cid
  ),
  ok as (
    select s.*, least(1, 0.6 * s.sim + 0.4 * s.fr) as sc from scored s
  ),
  ranked as (
    select r.*, row_number() over (partition by r.fid
                                   order by r.sc desc, r.same_firm desc, r.docs desc, r.cpub desc nulls last, r.cid) as rn
      from ok r
     where r.sc >= p_min_score
  )
  select r.fid, r.cid, round(r.sc::numeric, 4), r.rn::integer,
         'lexical_v1' || case when r.same_text then ';same_text' else '' end
         || ';sim=' || to_char(r.sim::numeric, 'FM0.00') || ';fts=' || to_char(r.fr::numeric, 'FM0.00')
         || ';docs=' || r.docs || case when r.same_firm then ';same_firm' else '' end
    from ranked r
   where r.rn <= greatest(1, least(coalesce(p_per, 5), 5));
end;
$$;
revoke all on function public.grm_finding_similar_compute(text[], numeric, integer) from public, anon, authenticated;

-- ── 채우기 함수: 맨 앞에서 문장 표를 동기화한다(그 밖은 114 와 같다) ─────────────────────────
create or replace function public.grm_link_finding_similar(
  p_since      timestamptz default '-infinity'::timestamptz,
  p_limit      integer default 40,
  p_stale_days integer default null
)
returns jsonb
language plpgsql
volatile
security invoker
set search_path = public, extensions
as $$
declare
  v_ids     text[];
  v_edges   integer := 0;
  v_pending integer;
  v_texts   integer;
  v_t0      timestamptz := clock_timestamp();
begin
  v_texts := public.grm_finding_similar_sync_texts();

  select array_agg(q.finding_id) into v_ids
    from (
      select e.finding_id
        from public.findings e
        left join public.finding_similar_done d on d.finding_id = e.finding_id
       where e.scope_status = 'ok' and (e.finding_text_ko <> '' or e.finding_language = 'KO')
         and public.grm_similar_ftype(e.source, e.document_type) is not null
         and ( (d.finding_id is null and e.ingested_at >= p_since)
            or (d.finding_id is not null
                and (d.text_md5 <> md5(coalesce(nullif(e.finding_text_ko, ''), e.finding_text))
                     or (p_stale_days is not null and d.computed_at < now() - make_interval(days => p_stale_days)))) )
       order by d.computed_at nulls first, md5(coalesce(nullif(e.finding_text_ko, ''), e.finding_text)), e.finding_id
       limit greatest(1, least(coalesce(p_limit, 40), 500))
    ) q;

  if v_ids is not null then
    delete from public.finding_similar s where s.finding_id = any (v_ids);
    insert into public.finding_similar (finding_id, similar_id, score, rank, basis)
    select c.finding_id, c.similar_id, c.score, c.rank, c.basis
      from public.grm_finding_similar_compute(v_ids) c;
    get diagnostics v_edges = row_count;
    insert into public.finding_similar_done (finding_id, text_md5, edges, computed_at)
    select f.finding_id, md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)),
           (select count(*) from public.finding_similar s where s.finding_id = f.finding_id), now()
      from public.findings f
     where f.finding_id = any (v_ids)
    on conflict (finding_id) do update
      set text_md5 = excluded.text_md5, edges = excluded.edges, computed_at = excluded.computed_at;
  end if;

  select count(*) into v_pending
    from public.findings e
    left join public.finding_similar_done d on d.finding_id = e.finding_id
   where e.scope_status = 'ok' and (e.finding_text_ko <> '' or e.finding_language = 'KO')
     and public.grm_similar_ftype(e.source, e.document_type) is not null
     and ( (d.finding_id is null and e.ingested_at >= p_since)
        or (d.finding_id is not null
            and (d.text_md5 <> md5(coalesce(nullif(e.finding_text_ko, ''), e.finding_text))
                 or (p_stale_days is not null and d.computed_at < now() - make_interval(days => p_stale_days)))) );

  return jsonb_build_object('processed', coalesce(cardinality(v_ids), 0), 'edges', v_edges, 'pending', v_pending,
                            'new_texts', v_texts,
                            'elapsed_ms', (extract(epoch from (clock_timestamp() - v_t0)) * 1000)::integer);
end;
$$;
revoke all on function public.grm_link_finding_similar(timestamptz, integer, integer) from public, anon, authenticated;
