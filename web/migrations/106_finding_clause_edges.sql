-- 지식그래프 문제 4 — 조항 키 정규화(citation_key) + 지적↔조항 엣지 finding_clause.
--
-- 가산만 한다: 함수 1(grm_parse_citation) + 테이블 1(finding_clause) + 백필 함수 1. 기존 테이블·RPC
-- 불변(반환 shape 무변경). **적용 후 select grm_backfill_finding_clause(); 를 1회 호출**해야 채워진다
-- (이 파일은 행을 쓰지 않는다 — 비서실이 dry-run 수치를 보고 적용).
--
-- 문제: findings.cfr_refs 가 같은 조항을 두 가지로 적는다 — `section 301(a)` 와 `21 U.S.C. § 331(a)`,
-- `21 CFR 211.22` 와 `21CFR211.22`(공백 없음). 식약처 별표는 `[별표 1] 제7.1호` / `[별표 1] 7.1` /
-- `의약품 등의 안전에 관한 규칙 [별표 1] 제7호` 로 갈린다. 문자열이 달라 "이 조항으로 지적받은 업체"가
-- 한쪽 표기만 센다.
--
-- citation_key 규칙(조항 단위 키 + 항 단위 citation_paragraph 분리):
--   21 CFR   : '21 CFR 211.22'            paragraph '(a)(1)' (소문자) — 공백 변형 흡수. Part 만 있으면 '21 CFR 211'(0.9)
--   21 U.S.C.: '21 USC 331'               paragraph '(a)'
--   FD&C §N  : 'section N' 을 U.S.C. 번호로 옮겨 '21 USC 331' 과 **같은 키**로 (대응표 아래, 0.9).
--              대응표에 없는 section(PHS법 351·361, 1~12 같은 비-FD&C 번호 등)은 **연결하지 않는다**.
--   식약처   : '<규칙명> 별표N 제X호' + paragraph '가목'. 규칙명이 없으면 '[규칙 미상] 별표N 제X호'(0.7),
--              항목 없이 별표만 있으면 표 단위 키(0.5). 규칙명 유무로 키를 억지로 합치지 않는다.
-- confidence: 1.0 정형 CFR/USC, 0.95 규칙명 명시 별표, 0.9 FD&C→USC 대응·Part 단독, 0.7 규칙 미상 별표,
--             0.5 표 단위. 소비자는 임계(예: >= 0.8)로 거른다.
--
-- ★004/009 함정: plpgsql 은 백필 함수 하나뿐이고 변수명이 컬럼과 겹치지 않는다(v_ 접두).

create or replace function public.grm_parse_citation(p_raw text)
returns table (citation_key text, citation_paragraph text, confidence numeric, basis text)
language sql
immutable
set search_path = public
as $$
  -- 21 CFR 절(Part.Section)
  select '21 CFR ' || m[1] || '.' || m[2], lower(coalesce(m[3], '')), 1.0::numeric, 'cfr'
    from (select regexp_match(p_raw, '^\s*21\s*CFR\s*(?:Part\s*)?(\d{1,4})\.(\d{1,4})((?:\([A-Za-z0-9]+\))*)\s*$', 'i') as m) t
   where m is not null
  union all
  -- 21 CFR Part 단독
  select '21 CFR ' || m[1], '', 0.9::numeric, 'cfr_part'
    from (select regexp_match(p_raw, '^\s*21\s*CFR\s*(?:Part\s*)?(\d{1,4})\s*$', 'i') as m) t
   where m is not null
  union all
  -- 21 U.S.C.
  select '21 USC ' || lower(m[1]), lower(coalesce(m[2], '')), 1.0::numeric, 'usc'
    from (select regexp_match(p_raw, '^\s*21\s*U\.?\s*S\.?\s*C\.?\s*(?:§+|section)?\s*(\d{3}[A-Za-z]?(?:-\d+)?)((?:\([A-Za-z0-9]+\))*)\s*$', 'i') as m) t
   where m is not null
  union all
  -- FD&C Act section N -> U.S.C. (대응표에 있는 것만)
  select '21 USC ' || map.usc, lower(coalesce(m[2], '')), 0.9::numeric, 'fdca_section'
    from (select regexp_match(p_raw, '^\s*section\s+(\d{3}[A-Za-z]?(?:-\d+)?)((?:\([A-Za-z0-9]+\))*)\s*$', 'i') as m) t
    join (values
      ('201','321'),('301','331'),('303','333'),('402','342'),('403','343'),('409','348'),
      ('501','351'),('502','352'),('503','353'),('503b','353b'),('505','355'),('505-1','355-1'),
      ('506','356'),('510','360'),('512','360b'),('515','360e'),('519','360i'),('520','360j'),
      ('704','374'),('801','381')
    ) as map(fdca, usc) on map.fdca = lower(t.m[1])
   where m is not null
  union all
  -- 식약처 별표: 규칙명 + 표 + 호(여러 개면 호마다 한 행) + 목
  select coalesce(nullif(btrim(l.law), ''), '[규칙 미상]') || ' 별표' || l.tbl || ' 제' || i.item || '호',
         coalesce((regexp_match(l.rest, '([가-하])목'))[1] || '목', ''),
         case when nullif(btrim(l.law), '') is null then 0.7 else 0.95 end::numeric,
         'mfds_byeolpyo'
    from (select (regexp_match(p_raw, '^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$'))[1] as law,
                 (regexp_match(p_raw, '^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$'))[2] as tbl,
                 (regexp_match(p_raw, '^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$'))[3] as rest) l
    cross join lateral (
      select (mm)[1] as item from regexp_matches(l.rest, '제\s*(\d+(?:[.의]\d+)*)\s*호', 'g') as mm
      union all
      select (mm)[1] from regexp_matches(l.rest, '^(\d+(?:\.\d+)*)\s*$') as mm
    ) i
   where l.tbl is not null and l.law !~ '[*0]{4,}'
  union all
  -- 식약처 별표: 항목 없이 표만 (행정처분의 / Ⅰ·Ⅱ 같은 잘린 표기)
  select coalesce(nullif(btrim(l.law), ''), '[규칙 미상]') || ' 별표' || l.tbl, '', 0.5::numeric, 'mfds_table'
    from (select (regexp_match(p_raw, '^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$'))[1] as law,
                 (regexp_match(p_raw, '^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$'))[2] as tbl,
                 (regexp_match(p_raw, '^\s*(.*?)\s*\[별표\s*(\d+(?:의\d+)?)\]\s*(.*)$'))[3] as rest) l
   where l.tbl is not null and l.law !~ '[*0]{4,}'
     and l.rest !~ '제\s*\d' and l.rest !~ '^\d+(?:\.\d+)*\s*$'
$$;

revoke all on function public.grm_parse_citation(text) from public, anon, authenticated;

create table if not exists public.finding_clause (
  finding_id         text not null references public.findings (finding_id) on delete cascade,
  citation_key       text not null,
  citation_paragraph text not null default '',
  confidence         numeric(3,2) not null check (confidence > 0 and confidence <= 1),
  basis              text not null,
  raw_ref            text not null,
  primary key (finding_id, citation_key, citation_paragraph)
);
create index if not exists idx_finding_clause_key on public.finding_clause (citation_key);
alter table public.finding_clause enable row level security;
revoke all on table public.finding_clause from public, anon, authenticated;

-- 백필 — 멱등(이미 있는 (finding, key, paragraph) 는 건드리지 않는다). 같은 키가 서로 다른 표기에서 나오면
-- (section 301(a) 와 21 U.S.C. § 331(a)) 높은 confidence 쪽 raw_ref 를 남긴다.
create or replace function public.grm_backfill_finding_clause()
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
    ) s
   order by s.finding_id, s.citation_key, s.citation_paragraph, s.confidence desc, s.raw
  on conflict do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
revoke all on function public.grm_backfill_finding_clause() from public, anon, authenticated;
