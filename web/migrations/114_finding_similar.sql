-- 지식그래프 — 지적↔유사 지적 엣지 finding_similar. 가산만(표 2·함수 4·색인 1, 기존 RPC·표 불변).
--
-- 배경: "이 지적과 유사한 사례"는 방문 시점에 계산한다(findings_similar_to = 어휘 OR 검색 + 글자 유사도, 092 캐시).
-- 미래의 AI/RAG 가 지적을 읽을 때 그 계산을 매번 돌릴 수 없으므로(한 건 0.2~2.3초) **미리 저장한 엣지**로 만든다.
-- 점수식은 findings_similar_to 와 같다: score = 0.6 × trigram similarity(본문, 기준 앞 500자) + 0.4 × ts_rank.
-- 방문자 RPC·092 캐시·findings.js 는 건드리지 않는다.
--
-- ★원칙: 틀리게 잇느니 안 잇는다. 모든 엣지에 score(= 신뢰도)와 basis(근거 문자열)가 붙는다.
--   · 저장 하한 score >= 0.40 (함수 인자 p_min_score 기본값, 표 CHECK 는 0.35). 2026-11-05 수동 대조 표본에서
--     0.35~0.40 구간은 적합 약 84%, 0.40 이상은 약 98% 였다 → 하한을 0.35 가 아니라 0.40 으로 둔다.
--   · 지적당 상위 5. 기준과 같은 문서(raw_signal_id)의 지적·자기 자신은 제외(번역 변형 중복 포함).
--   · 같은 유형 안에서만 잇는다(grm_similar_ftype): FDA 483 · Health Canada Inspection · FDA Warning Letter ·
--     MFDS recall-quality. 제외 — MFDS gmp-inspection(별표 묶음형, 표본 정밀도 낮음)·admin-action·EU/MHRA NCR(93건).
--   · 같은 문장이 여러 문서에 있으면 한 엣지(최신 문서의 대표)로 접고 basis 에 docs=N 을 단다.
--   · basis 예: lexical_v1;sim=0.62;fts=0.07;docs=3;same_firm  (same_text = 본문이 글자 그대로 같음)
--
-- ★왜 색인을 만드나: 후보를 "질의어 OR 17,992행"이 아니라 trigram GIN(`%`, 임계 0.25)으로 뽑으면 후보가 수십~수백 행이다.
--   색인이 없어도 결과는 같지만 느리다(전수 대조 시 483 한 기준 약 0.2초·경고서한 약 1초). 크기는 약 20~30MB 로 추정.
--   적용 시 findings 쓰기가 몇 초 막힌다(CREATE INDEX, 수집 시간대를 피한다).
--
-- 채우기·갱신(한 함수 한 벌): grm_link_finding_similar(p_since, p_limit, p_stale_days)
--   · 대기열 = 아직 계산 안 된 지적 + 본문 md5 가 바뀐 지적(번역이 나중에 채워지면 다시 계산) [+ p_stale_days 보다 오래된 것]
--   · 한 번에 p_limit 건만 계산하고 남은 수를 돌려준다. 백필 = p_since '-infinity' 로 pending 이 0 이 될 때까지 반복,
--     일일 = p_since now()-7일(kg_link_new.py). 계산한 지적은 엣지가 0개여도 finding_similar_done 에 남는다.
--   · 엣지는 방향이 있다(새 지적 → 옛 지적). 옛 지적에 새 이웃이 생기는 것은 p_stale_days 갱신이 따라잡는다.

-- ── 1. 후보 색인 + 유형 판정 ─────────────────────────────────────────────────────────────
create index if not exists idx_findings_text_trgm
  on public.findings using gin ((coalesce(nullif(finding_text_ko, ''), finding_text)) extensions.gin_trgm_ops)
  where scope_status = 'ok' and (finding_text_ko <> '' or finding_language = 'KO');

create or replace function public.grm_similar_ftype(p_source text, p_document_type text)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    when p_source in ('FDA 483', 'Health Canada Inspection', 'FDA Warning Letter') then p_source
    when p_source = 'MFDS' and p_document_type = 'recall-quality' then 'MFDS/recall-quality'
  end
$$;
revoke all on function public.grm_similar_ftype(text, text) from public, anon, authenticated;

-- ── 2. 표 ────────────────────────────────────────────────────────────────────────────────
create table if not exists public.finding_similar (
  finding_id text not null references public.findings (finding_id) on delete cascade,
  similar_id text not null references public.findings (finding_id) on delete cascade,
  score      numeric(5,4) not null check (score >= 0.35 and score <= 1),
  rank       smallint not null check (rank between 1 and 5),
  basis      text not null,
  primary key (finding_id, similar_id),
  unique (finding_id, rank),
  check (finding_id <> similar_id)
);
create index if not exists idx_finding_similar_similar_id on public.finding_similar (similar_id);
alter table public.finding_similar enable row level security;
revoke all on table public.finding_similar from public, anon, authenticated;

-- 계산 장부: 엣지가 0개인 지적도 "계산했음"을 남긴다(대기열이 같은 지적을 되풀이해 잡지 않도록).
create table if not exists public.finding_similar_done (
  finding_id  text primary key references public.findings (finding_id) on delete cascade,
  text_md5    text not null,
  edges       smallint not null default 0,
  computed_at timestamptz not null default now()
);
alter table public.finding_similar_done enable row level security;
revoke all on table public.finding_similar_done from public, anon, authenticated;

-- ── 3. 계산(읽기 전용) — 기준 지적 id 배열 → 엣지 행 ─────────────────────────────────────
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
  -- `%` 연산자의 임계. 최종 점수가 p_min_score(>=0.35) 이상이려면 유사도가 이보다 낮을 수 없다(ts_rank 는 작은 값).
  perform set_config('pg_trgm.similarity_threshold', '0.25', true);
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
  cand as materialized (
    select bg.grp as bgrp, bg.ftype as bftype, c.finding_id as cid, c.raw_signal_id as crid,
           md5(c.ctxt) as cgrp, c.published_date as cpub, c.firm_key as cfirm,
           similarity(c.ctxt, bg.q) as sim, coalesce(ts_rank(c.search_tsv, bg.tq), 0) as fr
      from bg
      cross join lateral (
        select f.finding_id, f.raw_signal_id, f.published_date, f.firm_key, f.search_tsv, f.source, f.document_type,
               coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as ctxt
          from public.findings f
         where f.scope_status = 'ok' and (f.finding_text_ko <> '' or f.finding_language = 'KO')
           and coalesce(nullif(f.finding_text_ko, ''), f.finding_text) % bg.q
      ) c
     where public.grm_similar_ftype(c.source, c.document_type) = bg.ftype
  ),
  pairs as materialized (
    select b.fid, c.cid, c.cgrp, c.cpub, c.sim, c.fr,
           least(1, 0.6 * c.sim + 0.4 * c.fr) as sc,
           (c.cgrp = md5(b.txt)) as same_text,
           (coalesce(b.firm, '') <> '' and b.firm = c.cfirm) as same_firm,
           c.crid
      from base b
      join cand c on c.bgrp = md5(b.txt) and c.bftype = b.ftype
     where c.crid is distinct from b.rid and c.cid <> b.fid
       and least(1, 0.6 * c.sim + 0.4 * c.fr) >= p_min_score
  ),
  docs as (
    select p.fid, p.cgrp, count(distinct p.crid) as n from pairs p group by p.fid, p.cgrp
  ),
  reps as (
    select distinct on (p.fid, p.cgrp) p.*, d.n as docs
      from pairs p join docs d on d.fid = p.fid and d.cgrp = p.cgrp
     order by p.fid, p.cgrp, p.cpub desc nulls last, p.cid
  ),
  ranked as (
    select r.*, row_number() over (partition by r.fid
                                   order by r.sc desc, r.same_firm desc, r.docs desc, r.cpub desc nulls last, r.cid) as rn
      from reps r
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

-- ── 4. 채우기·갱신 — 대기열에서 p_limit 건을 계산해 저장하고 남은 수를 돌려준다 ──────────────
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
  v_t0      timestamptz := clock_timestamp();
begin
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
       order by d.computed_at nulls first, e.ingested_at desc, e.finding_id
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
                            'elapsed_ms', (extract(epoch from (clock_timestamp() - v_t0)) * 1000)::integer);
end;
$$;
revoke all on function public.grm_link_finding_similar(timestamptz, integer, integer) from public, anon, authenticated;

-- ── 5. 관측(운영자) — 유형별 계산 수·엣지 수·점수 구간·대기 수 ───────────────────────────────
create or replace function public.grm_finding_similar_status()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  select jsonb_build_object(
    'done',    (select count(*) from public.finding_similar_done),
    'with_edges', (select count(*) from public.finding_similar_done where edges > 0),
    'edges',   (select count(*) from public.finding_similar),
    'by_type', coalesce((select jsonb_object_agg(t, n) from (
                  select public.grm_similar_ftype(f.source, f.document_type) as t, count(*) as n
                    from public.finding_similar s join public.findings f on f.finding_id = s.finding_id
                   group by 1) x), '{}'::jsonb),
    'score_bands', jsonb_build_object(
        'lt_0_50', (select count(*) from public.finding_similar where score < 0.50),
        'ge_0_50', (select count(*) from public.finding_similar where score >= 0.50)),
    'oldest_computed', (select min(computed_at) from public.finding_similar_done));
$$;
revoke all on function public.grm_finding_similar_status() from public, anon, authenticated;
