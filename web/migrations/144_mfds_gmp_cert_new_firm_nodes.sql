-- 144: 식약처 GMP 적합판정서 — 우리 업체 목록에 없는 업체를 지식 그래프 업체 노드로(공개 RPC).
--
-- 11-11 대표 결정 "지적 생기면 페이지": 우리 DB 에 지적·처분이 없는 판정서 업체(link_note
-- 'no_firm_node')는 지식 그래프(`graph/nodes-firm.jsonl`)에 업체 노드로만 싣고 업체 페이지는
-- 만들지 않는다. 그 업체의 지적이 들어오면 기존 규칙대로 업체 페이지가 생기고, 다음 연결
-- (grm_link_mfds_gmp_cert)에서 판정서가 그 업체에 이어지면 이 목록에서 저절로 빠진다.
--
-- 노드 키는 사업자번호를 직접 내지 않고 해시 앞 12자리로 만든다(142 공개 RPC 와 같은 원칙:
-- 사업자번호·raw 비공개). 번호가 빈 행은 판정서 업체명으로 해시한다.
-- 후보(candidate_firm_key)가 붙은 행과 번호 불일치·모호 행은 "새 업체"가 아니므로 뺀다.
--
-- 롤백: drop function if exists public.mfds_gmp_cert_new_firms_public();

create or replace function public.mfds_gmp_cert_new_firms_public()
returns table (node_key text, firm_name text, cert_count integer, product_kinds text,
               listed_now boolean, measured_on date)
language sql
stable
security definer
set search_path = public
as $$
  with latest as (select max(snapshot_on) as d from public.mfds_gmp_cert_snapshot),
  u as (
    select 'mfds-gmp-' || left(encode(sha256(convert_to(
             coalesce(nullif(c.bizrno, ''), 'name:' || c.firm_name), 'UTF8')), 'hex'), 12) as node_key,
           c.firm_name, c.product_kind, c.last_seen_on
    from public.mfds_gmp_cert c
    where c.firm_key is null and c.link_note = 'no_firm_node' and c.candidate_firm_key is null
  )
  select u.node_key,
         (array_agg(u.firm_name order by u.last_seen_on desc, u.firm_name))[1],
         count(*)::integer,
         string_agg(distinct u.product_kind, ',' order by u.product_kind),
         bool_or(u.last_seen_on = latest.d),
         latest.d
  from u, latest
  group by u.node_key, latest.d
  order by u.node_key
$$;
revoke all on function public.mfds_gmp_cert_new_firms_public() from public;
grant execute on function public.mfds_gmp_cert_new_firms_public() to anon, authenticated, service_role;
