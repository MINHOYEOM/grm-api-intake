-- ============================================================================
-- 142_mfds_gmp_cert.sql — 식약처 의약품 GMP 적합판정서 발급 현황(전체 목록) 원장 + 업체 연결(가산만)
--
-- ★운영 적용 2026-11-11(dry-run: 시험 13행 rollback — 번호·원문·법인격 접기 각 3 연결, 번호 불일치 3·없는 업체 1 미연결).
-- 계기(2026-11-11 대표 지시): 적합판정서를 DB 로 쌓아 지식 그래프에 쓴다. **브리프에는 올리지 않는다**
--   (적합판정 취소·부적합은 이미 행정처분으로 수집 중). 원천은 공공데이터포털 15097207
--   `DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq`.
--
-- 원천 실측(2026-11-11 Actions 조회): totalCount 569 · 칸 6개 전부 —
--   BSSH_NM 업체명 · FCTR_ADDR 공장소재지 · KGMP_BGMP_NAME 완제/원료 구분 · GMP_INGR_MM_GROUP_NAME
--   제형군/제조방법(273/569 만 채움) · VLD_PRD_YMD 유효기한 · BIZRNO 사업자등록번호(568/569).
--   ★판정일 칸은 원천에 없다 → judged_on 은 비우고, 유효기간 3년 역산값을 `judged_on_est` 로만 둔다
--   (추정임을 이름에 박는다 — 화면에는 내지 않는다).
--
-- 수집: mfds_gmp_cert_snapshot.py + grm-mfds-gmp-cert-monthly.yml(월 1회 전체 목록, Notion 무접촉).
--   행 = 원천 행 1건. cert_id = 원천 행 **전체 칸** 정렬 JSON 의 sha1 — 갱신으로 유효기한이 바뀌면 새
--   행이 생기고 옛 행은 last_seen_on 이 멈춘다(지우지 않는다 = 이력). 원천 칸은 raw 에 전부, 자르지 않음.
--
-- 업체 연결(grm_link_mfds_gmp_cert, 매 스냅샷 뒤 호출 · 멱등):
--   ① bizrno    1.00  식약처 지적(findings, source=MFDS) ⋈ raw_signals(같은 document_id)의 BIZRNO 가
--                     정확히 한 firm_key 로만 이어질 때.
--   ② exact_name 0.95 원천 업체명 = 식약처 지적 업체명(원문 그대로), 후보 firm_key 하나.
--   ③ kr_name_normalized 0.85  법인격 표기(주식회사·(주)·㈜ 등)·공백만 접은 키가 같고 후보 하나.
--   ②③ 은 그 firm_key 에 이미 알려진 사업자번호가 있는데 원천 번호와 다르면 **연결하지 않는다**
--   (동명이사 차단, link_note='bizrno_conflict'). 후보가 둘 이상이면 'ambiguous_name',
--   우리 DB 에 그 업체 지적이 없으면 'no_firm_node'. firm_alias(105·115·118)는 정본으로 접는다.
--
-- 공개: mfds_gmp_cert_public() — 연결된 행만, 사업자번호 제외(업체 페이지 한 줄용, anon 실행).
-- 지식 그래프: kg_firm_mfds_gmp_cert 뷰(엣지 '식약처 GMP 적합판정' — 업체·제조소·제형·판정일·유효기간).
--
-- ★004/009 함정: plpgsql 은 grm_link_mfds_gmp_cert 하나뿐이고 선언 변수·루프 없음(set-based UPDATE).
--
-- 되돌리기:
--   drop function if exists public.mfds_gmp_cert_public();
--   drop function if exists public.grm_link_mfds_gmp_cert();
--   drop view if exists public.kg_firm_mfds_gmp_cert;
--   drop function if exists public.grm_kr_firm_core(text);
--   drop table if exists public.mfds_gmp_cert_snapshot;
--   drop table if exists public.mfds_gmp_cert;
-- ============================================================================

create table if not exists public.mfds_gmp_cert (
  cert_id          text primary key check (cert_id ~ '^gmpcert-[0-9a-f]{40}$'),
  firm_name        text not null default '',
  bizrno           text not null default '',
  factory_addr     text not null default '',
  product_kind     text not null default '',
  dosage_group     text not null default '',
  valid_until_raw  text not null default '',
  valid_until      date,
  raw              jsonb not null,
  first_seen_on    date not null,
  last_seen_on     date not null,
  firm_key         text,
  link_method      text check (link_method in ('bizrno', 'exact_name', 'kr_name_normalized')),
  link_confidence  numeric(3,2),
  link_note        text,
  linked_at        timestamptz,
  ingested_at      timestamptz not null default now()
);
create index if not exists mfds_gmp_cert_firm_key_idx on public.mfds_gmp_cert (firm_key);
create index if not exists mfds_gmp_cert_bizrno_idx on public.mfds_gmp_cert (bizrno);
alter table public.mfds_gmp_cert enable row level security;
revoke all on table public.mfds_gmp_cert from public, anon, authenticated;

comment on table public.mfds_gmp_cert is
  '식약처 의약품 GMP 적합판정서 발급 현황(data.go.kr 15097207) — 원천 행 1건 = 1행, 원천 칸 전부 raw. 142';

create table if not exists public.mfds_gmp_cert_snapshot (
  snapshot_on    date primary key,
  total_count    integer,
  fetched        integer not null,
  distinct_rows  integer not null,
  new_rows       integer not null default 0,
  complete       boolean not null,
  linked_rows    integer,
  unlinked_rows  integer,
  report         jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);
alter table public.mfds_gmp_cert_snapshot enable row level security;
revoke all on table public.mfds_gmp_cert_snapshot from public, anon, authenticated;

-- 한국어 업체명 비교 키 — 법인격 표기·공백·구두점만 접는다(내용 단어 보존).
create or replace function public.grm_kr_firm_core(p_name text)
returns text
language sql
immutable
set search_path = public
as $$
  select regexp_replace(
           regexp_replace(
             regexp_replace(
               lower(
                 regexp_replace(
                   normalize(coalesce(p_name, ''), NFKC),
                   '[(（]\s*(주|유|재|사|합)\s*[)）]|㈜|㈐|㈒|주식회사|유한책임회사|유한회사|합자회사|합명회사|재단법인|사단법인|의료법인|농업회사법인|영농조합법인',
                   ' ', 'g')),
               '\y(co|ltd|inc|corp|corporation|company|limited)\y', ' ', 'g'),
             '[.,·''"_-]', ' ', 'g'),
           '\s+', '', 'g')
$$;

-- 업체 연결(멱등). 반환: 방법별·사유별 건수.
create or replace function public.grm_link_mfds_gmp_cert()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  with
  canon as (select alias_key, canonical_key from public.firm_alias),
  mf as (
    select distinct f.firm_name, coalesce(c.canonical_key, f.firm_key) as firm_key, f.document_id
    from public.findings f left join canon c on c.alias_key = f.firm_key
    where f.source = 'MFDS' and f.firm_key is not null and f.firm_key <> ''
  ),
  rs as (
    select document_id, (raw_json::jsonb) ->> 'BIZRNO' as biz
    from public.raw_signals
    where source = 'MFDS' and raw_json like '%"BIZRNO"%'
  ),
  biz_firm as (            -- 사업자번호 → firm_key (하나로만 이어질 때)
    select rs.biz, min(mf.firm_key) as firm_key
    from mf join rs on rs.document_id = mf.document_id
    where coalesce(rs.biz, '') <> ''
    group by rs.biz having count(distinct mf.firm_key) = 1
  ),
  firm_biz as (            -- firm_key 에 알려진 사업자번호들
    select mf.firm_key, array_agg(distinct rs.biz) as bizs
    from mf join rs on rs.document_id = mf.document_id
    where coalesce(rs.biz, '') <> ''
    group by mf.firm_key
  ),
  by_exact as (
    select firm_name, min(firm_key) as firm_key, count(distinct firm_key) as n
    from mf group by firm_name
  ),
  by_core as (
    select public.grm_kr_firm_core(firm_name) as core, min(firm_key) as firm_key,
           count(distinct firm_key) as n
    from mf where public.grm_kr_firm_core(firm_name) <> ''
    group by 1
  ),
  decided as (
    select c.cert_id,
      case
        when b.firm_key is not null then b.firm_key
        when e.n = 1 and (fb1.bizs is null or c.bizrno = '' or c.bizrno = any(fb1.bizs)) then e.firm_key
        when e.n is null and k.n = 1 and (fb2.bizs is null or c.bizrno = '' or c.bizrno = any(fb2.bizs)) then k.firm_key
      end as firm_key,
      case
        when b.firm_key is not null then 'bizrno'
        when e.n = 1 and (fb1.bizs is null or c.bizrno = '' or c.bizrno = any(fb1.bizs)) then 'exact_name'
        when e.n is null and k.n = 1 and (fb2.bizs is null or c.bizrno = '' or c.bizrno = any(fb2.bizs)) then 'kr_name_normalized'
      end as method,
      case
        when b.firm_key is not null then null
        when e.n = 1 or (e.n is null and k.n = 1) then 'bizrno_conflict'
        when coalesce(e.n, 0) > 1 or coalesce(k.n, 0) > 1 then 'ambiguous_name'
        else 'no_firm_node'
      end as note
    from public.mfds_gmp_cert c
    left join biz_firm b on b.biz = c.bizrno and c.bizrno <> ''
    left join by_exact e on e.firm_name = c.firm_name
    left join firm_biz fb1 on fb1.firm_key = e.firm_key
    left join by_core k on k.core = public.grm_kr_firm_core(c.firm_name)
    left join firm_biz fb2 on fb2.firm_key = k.firm_key
  )
  update public.mfds_gmp_cert t
     set firm_key = d.firm_key,
         link_method = d.method,
         link_confidence = case d.method when 'bizrno' then 1.00 when 'exact_name' then 0.95
                                         when 'kr_name_normalized' then 0.85 end,
         link_note = case when d.firm_key is null then d.note end,
         linked_at = now()
    from decided d
   where d.cert_id = t.cert_id;

  return (
    select jsonb_build_object(
      'rows', count(*),
      'linked_rows', count(*) filter (where firm_key is not null),
      'linked_firms', count(distinct firm_key),
      'unlinked_rows', count(*) filter (where firm_key is null),
      'by_method', coalesce((select jsonb_object_agg(link_method, n) from (
          select link_method, count(*) n from public.mfds_gmp_cert
          where link_method is not null group by 1) m), '{}'::jsonb),
      'by_note', coalesce((select jsonb_object_agg(link_note, n) from (
          select link_note, count(*) n from public.mfds_gmp_cert
          where firm_key is null group by 1) u), '{}'::jsonb))
    from public.mfds_gmp_cert);
end;
$$;
revoke all on function public.grm_link_mfds_gmp_cert() from public, anon, authenticated;
grant execute on function public.grm_link_mfds_gmp_cert() to service_role;

-- 지식 그래프 엣지: 업체 —[식약처 GMP 적합판정]→ 판정서(제조소·제형·유효기간).
create or replace view public.kg_firm_mfds_gmp_cert
with (security_invoker = true) as
select c.firm_key,
       '식약처 GMP 적합판정'::text as relation,
       c.cert_id,
       c.firm_name,
       c.factory_addr        as site,
       c.product_kind,
       c.dosage_group,
       null::date            as judged_on,          -- 원천에 판정일 칸이 없다(2026-11-11 실측)
       (c.valid_until - interval '3 years' + interval '1 day')::date as judged_on_est,
       c.valid_until,
       c.last_seen_on = (select max(snapshot_on) from public.mfds_gmp_cert_snapshot) as listed_now,
       c.link_method,
       c.link_confidence
from public.mfds_gmp_cert c
where c.firm_key is not null;
revoke all on table public.kg_firm_mfds_gmp_cert from public, anon, authenticated;

-- 공개(업체 페이지 한 줄): 연결된 행만, 사업자번호·raw 제외.
create or replace function public.mfds_gmp_cert_public()
returns table (firm_key text, firm_name text, factory_addr text, product_kind text,
               dosage_group text, valid_until date, listed_now boolean, measured_on date)
language sql
stable
security definer
set search_path = public
as $$
  with latest as (select max(snapshot_on) as d from public.mfds_gmp_cert_snapshot)
  select c.firm_key, c.firm_name, c.factory_addr, c.product_kind, c.dosage_group,
         c.valid_until, c.last_seen_on = latest.d, latest.d
  from public.mfds_gmp_cert c, latest
  where c.firm_key is not null
  order by c.firm_key, c.valid_until desc nulls last, c.cert_id
$$;
revoke all on function public.mfds_gmp_cert_public() from public;
grant execute on function public.mfds_gmp_cert_public() to anon, authenticated, service_role;
