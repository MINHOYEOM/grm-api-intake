-- 116 의 호출당 고정비 제거 — 지적↔문장 매핑표 finding_similar_member + 증분 동기화. 엣지 결과는 116 과 같다.
--
-- ★실측(2026-11-05, 116 적용 후): p_limit=100 → 40.9 s, 300 → 76.5 s = 건당 ~0.18 s + 호출당 고정 ~23 s.
--   PostgREST(service_role)는 약 8 s 에서 끊으므로 일일 경로가 REST 로 부르면 배치가 작아도 죽는다.
--   explain(analyze) 로 고정비를 쪼갠 결과(한 호출에서 findings 28k 행을 여러 번 훑고 md5 를 매번 다시 계산):
--     sync_texts 의 전체 NOT EXISTS 0.65 s · 큐 선택 0.27 s · 남은 수 계산 0.2 s · gm(후보 문장→지적) 해시조인 0.6 s
--     + 상관 부질의 `select count(*) from gm x …` 가 쌍마다 gm 전체를 다시 훑음(560회 × 0.26 ms).
--   (기준 문장당 후보 추출 ~0.09~0.14 s 는 고정비가 아니라 배치 크기에 비례한다 — 좁은 표 GIN 의 힙 재검사.)
--
-- ★바꾸는 것
--   · finding_similar_member(finding_id pk, grp, ftype, raw_signal_id, published_date, firm_key, ingested_at):
--     "이 지적은 어느 문장(md5)인가"를 저장. (grp, ftype) 색인. 최초 채움은 이 파일 안에서 1회.
--   · grm_finding_similar_sync_members(): 아직 member 에 없는 지적 + 최근 14일에 들어온 지적(번역이 나중에 채워져
--     본문이 바뀌는 경우)만 증분으로 채운다. md5 는 그 행들만 계산한다. 14일보다 오래된 지적의 본문이 나중에 바뀌면
--     member 가 낡는다(그 지적은 옛 문장으로 비교됨) — 필요하면 sync_members('infinity'가 아닌 큰 창)을 수동으로.
--   · grm_finding_similar_sync_texts(): member 에서 문장 표에 없는 (grp, ftype) 만 대상(전체 NOT EXISTS 제거).
--   · compute: gm 을 findings 전체 스캔이 아니라 member 색인 조인으로. 상관 부질의는 조인으로.
--   · grm_link_finding_similar: 큐·남은 수를 member 로(좁은 표, md5 재계산 없음).
-- 점수식·임계·하한·상위 5·접기·basis 는 116 과 같다. 표 finding_similar·방문자 RPC 불변.

create table if not exists public.finding_similar_member (
  finding_id     text primary key references public.findings (finding_id) on delete cascade,
  grp            text not null,
  ftype          text not null,
  raw_signal_id  text,
  published_date text,
  firm_key       text,
  ingested_at    timestamptz
);
create index if not exists idx_finding_similar_member_grp on public.finding_similar_member (grp, ftype);
alter table public.finding_similar_member enable row level security;
revoke all on table public.finding_similar_member from public, anon, authenticated;

create or replace function public.grm_finding_similar_sync_members(p_window interval default interval '14 days')
returns integer
language plpgsql
volatile
security invoker
set search_path = public, extensions
as $$
declare
  v_rows integer;
begin
  insert into public.finding_similar_member (finding_id, grp, ftype, raw_signal_id, published_date, firm_key, ingested_at)
  select f.finding_id, md5(coalesce(nullif(f.finding_text_ko, ''), f.finding_text)),
         public.grm_similar_ftype(f.source, f.document_type),
         f.raw_signal_id, f.published_date, f.firm_key, f.ingested_at
    from public.findings f
   where f.scope_status = 'ok' and (f.finding_text_ko <> '' or f.finding_language = 'KO')
     and public.grm_similar_ftype(f.source, f.document_type) is not null
     and ( f.ingested_at >= now() - p_window
        or not exists (select 1 from public.finding_similar_member m where m.finding_id = f.finding_id) )
  on conflict (finding_id) do update
    set grp = excluded.grp, ftype = excluded.ftype, raw_signal_id = excluded.raw_signal_id,
        published_date = excluded.published_date, firm_key = excluded.firm_key, ingested_at = excluded.ingested_at
  where (finding_similar_member.grp, finding_similar_member.ftype, finding_similar_member.published_date, finding_similar_member.firm_key)
        is distinct from (excluded.grp, excluded.ftype, excluded.published_date, excluded.firm_key);
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_finding_similar_sync_members(interval) from public, anon, authenticated;

-- 최초 채움(전 지적, 약 2.5만 행). 이후는 증분.
select public.grm_finding_similar_sync_members('infinity'::interval);

-- 문장 표 동기화 — member 에 있는데 문장 표에 없는 (grp, ftype) 만.
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
  select distinct on (m.grp, m.ftype) m.grp, m.ftype, coalesce(nullif(f.finding_text_ko, ''), f.finding_text)
    from public.finding_similar_member m
    join public.findings f on f.finding_id = m.finding_id
   where not exists (select 1 from public.finding_similar_text t where t.grp = m.grp and t.ftype = m.ftype)
   order by m.grp, m.ftype, m.finding_id
  on conflict do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_finding_similar_sync_texts() from public, anon, authenticated;

-- ── 계산 본문(116 대비: gm = member 조인, docs = 조인) ──────────────────────────────────
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
  cg as materialized (
    select bg.grp as bgrp, bg.ftype as bftype, t.grp as cgrp, similarity(t.txt, bg.q) as sim
      from bg
      cross join lateral (
        select s.grp, s.txt from public.finding_similar_text s
         where s.ftype = bg.ftype and s.txt % bg.q
      ) t
  ),
  gm as materialized (
    select distinct on (c.bgrp, c.cgrp, m.raw_signal_id)
           c.bgrp, c.cgrp, m.finding_id as cid, m.raw_signal_id as crid, m.published_date as cpub, m.firm_key as cfirm
      from cg c
      join public.finding_similar_member m on m.grp = c.cgrp and m.ftype = c.bftype
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
           g.n_docs - (x.crid is not null)::integer as docs
      from base b
      join bg on bg.grp = md5(b.txt) and bg.ftype = b.ftype
      join gmr g on g.bgrp = bg.grp
      join cg c on c.bgrp = g.bgrp and c.cgrp = g.cgrp
      left join gm x on x.bgrp = g.bgrp and x.cgrp = g.cgrp and x.crid = b.rid
     where g.crid is distinct from b.rid and g.cid <> b.fid and g.rk <= 2
     order by b.fid, g.cgrp, g.rk
  ),
  scored as (
    select p.fid, p.cid, p.cgrp, p.cpub, p.sim, p.same_text, p.same_firm, p.docs,
           coalesce(ts_rank(f.search_tsv, p.tq), 0) as fr
      from pairs p join public.findings f on f.finding_id = p.cid and f.scope_status = 'ok'
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

-- ── 채우기 함수: 큐·남은 수를 member 로 ─────────────────────────────────────────────────
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
  v_members integer;
  v_texts   integer;
  v_t0      timestamptz := clock_timestamp();
begin
  v_members := public.grm_finding_similar_sync_members();
  v_texts := public.grm_finding_similar_sync_texts();

  select array_agg(q.finding_id) into v_ids
    from (
      select m.finding_id
        from public.finding_similar_member m
        left join public.finding_similar_done d on d.finding_id = m.finding_id
       where ( (d.finding_id is null and m.ingested_at >= p_since)
            or (d.finding_id is not null
                and (d.text_md5 <> m.grp
                     or (p_stale_days is not null and d.computed_at < now() - make_interval(days => p_stale_days)))) )
       order by d.computed_at nulls first, m.grp, m.finding_id
       limit greatest(1, least(coalesce(p_limit, 40), 500))
    ) q;

  if v_ids is not null then
    delete from public.finding_similar s where s.finding_id = any (v_ids);
    insert into public.finding_similar (finding_id, similar_id, score, rank, basis)
    select c.finding_id, c.similar_id, c.score, c.rank, c.basis
      from public.grm_finding_similar_compute(v_ids) c;
    get diagnostics v_edges = row_count;
    insert into public.finding_similar_done (finding_id, text_md5, edges, computed_at)
    select m.finding_id, m.grp,
           (select count(*) from public.finding_similar s where s.finding_id = m.finding_id), now()
      from public.finding_similar_member m
     where m.finding_id = any (v_ids)
    on conflict (finding_id) do update
      set text_md5 = excluded.text_md5, edges = excluded.edges, computed_at = excluded.computed_at;
  end if;

  select count(*) into v_pending
    from public.finding_similar_member m
    left join public.finding_similar_done d on d.finding_id = m.finding_id
   where ( (d.finding_id is null and m.ingested_at >= p_since)
        or (d.finding_id is not null
            and (d.text_md5 <> m.grp
                 or (p_stale_days is not null and d.computed_at < now() - make_interval(days => p_stale_days)))) );

  return jsonb_build_object('processed', coalesce(cardinality(v_ids), 0), 'edges', v_edges, 'pending', v_pending,
                            'new_members', v_members, 'new_texts', v_texts,
                            'elapsed_ms', (extract(epoch from (clock_timestamp() - v_t0)) * 1000)::integer);
end;
$$;
revoke all on function public.grm_link_finding_similar(timestamptz, integer, integer) from public, anon, authenticated;
