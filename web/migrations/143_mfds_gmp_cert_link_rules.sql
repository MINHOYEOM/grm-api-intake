-- 143: 식약처 GMP 적합판정서 — 자동 연결이 못 한 행의 "근거 확인 규칙" + 후보 칸.
--
-- 142 의 자동 연결(사업자번호 > 업체명 원문 > 법인격 접은 이름)이 못 맞춘 행을,
-- 사람이(또는 세션이) 근거를 확인해 적은 규칙 표로 마저 잇는다.
--   * status='confirmed' → firm_key 에 연결(화면에 나온다). 근거·확실도는 link_method/
--     link_confidence 와 규칙 표 evidence 에 남는다.
--   * status='candidate' → candidate_* 칸에만 적는다(화면·공개 RPC·KG 뷰에 나오지 않음).
-- 규칙은 (사업자번호, 판정서 업체명) 둘 다 같을 때만 적용한다 — 번호가 바뀌면 다시 확인.
-- 월간 수집이 grm_link_mfds_gmp_cert() 를 부르면 자동 연결 → 규칙 적용 순서로 매번 다시 계산한다(멱등).
--
-- 롤백:
--   drop function if exists public.grm_link_mfds_gmp_cert();
--   alter function public.grm_link_mfds_gmp_cert_auto() rename to grm_link_mfds_gmp_cert;
--   drop table if exists public.mfds_gmp_cert_link_rule;
--   alter table public.mfds_gmp_cert drop column if exists candidate_firm_key,
--     drop column if exists candidate_method, drop column if exists candidate_confidence,
--     drop column if exists candidate_evidence;

alter table public.mfds_gmp_cert
  add column if not exists candidate_firm_key   text,
  add column if not exists candidate_method     text,
  add column if not exists candidate_confidence numeric(3,2),
  add column if not exists candidate_evidence   text;

-- 연결 방법 어휘에 규칙 방법을 더한다(기존 셋은 그대로).
alter table public.mfds_gmp_cert drop constraint if exists mfds_gmp_cert_link_method_check;
alter table public.mfds_gmp_cert add constraint mfds_gmp_cert_link_method_check
  check (link_method in ('bizrno', 'exact_name', 'kr_name_normalized',
                         'name_and_address', 'name_and_site', 'english_name_and_site'));

create table if not exists public.mfds_gmp_cert_link_rule (
  rule_id        bigint generated always as identity primary key,
  bizrno         text not null check (bizrno ~ '^[0-9]{10}$'),
  cert_firm_name text not null,
  firm_key       text not null,
  status         text not null check (status in ('confirmed', 'candidate')),
  -- 확정 규칙의 method 는 위 link_method 어휘 안에서만 고른다(후보는 자유 서술 키).
  method         text not null,
  confidence     numeric(3,2) not null check (confidence > 0 and confidence <= 1),
  evidence       text not null,
  decided_on     date not null default current_date,
  unique (bizrno, cert_firm_name, firm_key)
);
alter table public.mfds_gmp_cert_link_rule enable row level security;
revoke all on table public.mfds_gmp_cert_link_rule from public, anon, authenticated;

-- 142 의 자동 연결 함수는 이름만 바꿔 그대로 둔다.
alter function public.grm_link_mfds_gmp_cert() rename to grm_link_mfds_gmp_cert_auto;

create or replace function public.grm_link_mfds_gmp_cert()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.grm_link_mfds_gmp_cert_auto();

  -- 확정 규칙: 자동 연결이 못 한 행만.
  update public.mfds_gmp_cert t
     set firm_key = coalesce(a.canonical_key, r.firm_key),
         link_method = r.method,
         link_confidence = r.confidence,
         link_note = null,
         linked_at = now()
    from public.mfds_gmp_cert_link_rule r
    left join public.firm_alias a on a.alias_key = r.firm_key
   where r.status = 'confirmed' and t.firm_key is null
     and t.bizrno = r.bizrno and t.firm_name = r.cert_firm_name;

  -- 후보: 매번 지우고 다시 적는다(연결된 행엔 남기지 않는다).
  update public.mfds_gmp_cert
     set candidate_firm_key = null, candidate_method = null,
         candidate_confidence = null, candidate_evidence = null
   where candidate_firm_key is not null;

  update public.mfds_gmp_cert t
     set candidate_firm_key = c.firm_key,
         candidate_method = c.method,
         candidate_confidence = c.confidence,
         candidate_evidence = c.evidence
    from (
      select distinct on (r.bizrno, r.cert_firm_name)
             r.bizrno, r.cert_firm_name, coalesce(a.canonical_key, r.firm_key) as firm_key,
             r.method, r.confidence, r.evidence
        from public.mfds_gmp_cert_link_rule r
        left join public.firm_alias a on a.alias_key = r.firm_key
       where r.status = 'candidate'
       order by r.bizrno, r.cert_firm_name, r.confidence desc, r.rule_id
    ) c
   where t.firm_key is null and t.bizrno = c.bizrno and t.firm_name = c.cert_firm_name;

  return (
    select jsonb_build_object(
      'rows', count(*),
      'linked_rows', count(*) filter (where firm_key is not null),
      'linked_firms', count(distinct firm_key),
      'unlinked_rows', count(*) filter (where firm_key is null),
      'candidate_rows', count(*) filter (where firm_key is null and candidate_firm_key is not null),
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
revoke all on function public.grm_link_mfds_gmp_cert_auto() from public, anon, authenticated;
grant execute on function public.grm_link_mfds_gmp_cert_auto() to service_role;

-- 11-11 근거 확인분(미연결 197행 재조사). 근거 없이 이름만 비슷한 계열사
-- (휴온스바이오파마↔휴온스, 파마리서치바이오↔파마리서치 등)·같은 건물 다른 법인
-- (동아제약 천안↔동아에스티, 제일헬스사이언스↔제일약품)은 다른 회사라 규칙에 넣지 않는다.
insert into public.mfds_gmp_cert_link_rule
  (bizrno, cert_firm_name, firm_key, status, method, confidence, evidence, decided_on)
values
  ('6158162393', '(주)삼흥에너지', '주)삼흥에너지', 'confirmed', 'name_and_address', 0.95,
   '업체명 같음(우리 쪽 "주)" 표기 오류) · 제조소 주소 같음(김해시 장유로55번길 40, 식약처 행정처분 원문)', '2026-11-11'),
  ('4088213350', '화순전남대학병원', '화순전남대학병원 화순군', 'confirmed', 'name_and_address', 0.95,
   '업체명 같음(우리 쪽 지역명 덧붙음) · 제조소 주소 같음(화순군 화순읍 서양로 322)', '2026-11-11'),
  ('3118119107', '지에이치(주)', '지에이치(주) 응봉공장', 'confirmed', 'name_and_site', 0.85,
   '업체명 같음(우리 쪽 공장명 덧붙음) · 소재지 같음(예산군; 우리 원문 예산군 응봉면 응봉로 50-33)', '2026-11-11'),
  ('2118704515', '휴젤주식회사', 'hugel', 'confirmed', 'english_name_and_site', 0.85,
   '영문 업체명 Hugel Inc.(FDA 483, FEI 3012163998) · 실사 문서 소재지 Chuncheon = 판정서 춘천시', '2026-11-11'),
  ('2048100130', '동아제약(주)', '동아제약(주)', 'candidate', 'name_and_address_bizrno_conflict', 0.60,
   '업체명 같음 · 당진 제조소 주소 같음(합덕읍 인더스파크로 22) · 그러나 사업자번호 다름(우리 2048640118) — 법인 분할 전 번호일 수 있어 확인 필요', '2026-11-11'),
  ('6088139370', '(주)평화산소', '(주)평화산소공업', 'candidate', 'name_contains', 0.50,
   '업체명이 포함 관계 · 그러나 주소 다름(판정서 창원 진북면, 우리 원문 부산 사상구) — 같은 회사 다른 공장인지 확인 필요', '2026-11-11')
on conflict (bizrno, cert_firm_name, firm_key) do nothing;
