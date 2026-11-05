-- 지식그래프 — 업체명 별칭 확대 (105 위에 가산). 기존 26그룹은 건드리지 않는다.
-- firm_key 는 바꾸지 않는다(생성 컬럼·URL·firm_watchlist 불변). 행 23개를 firm_alias 에 넣을 뿐이고,
-- 함수·RPC 는 105 의 grm_firm_group_keys 를 그대로 쓴다(반환 shape 무변경, 멱등).
--
-- 자동 승인 규칙(2026-11-05 읽기 전용 실측, 보수적):
--   (1) 현재 firm_key 에서 소문자·구두점·공백·아포스트로피(’ ' )·'&'/and·후미 법인접미사(inc/ltd/
--       gmbh/spa/ag/oy 류)·선행 the 만 지운 문자열이 정확히 같다. 괄호 안 글자·단위 번호·제품명은
--       지우지 않는다("(Unit VII)" 와 "Unit 4" 는 서로 다른 키로 남는다).
--   (2) 군집 내 국가(country_key)가 충돌하지 않는다(한쪽이 비어 있는 건 허용) + FEI 가 있는 쪽끼리는
--       겹친다(충돌 없음).
--   (3) 증거 1개 이상: 같은 국가이거나 같은 FEI 를 공유한다(국가·FEI 둘 다 없는 쌍은 제외).
--   (4) 이미 firm_alias 에 올라 있는 키는 군집에서 뺀다(체인 금지).
--   정본 = 군집에서 행 수가 가장 많은 firm_key(동수면 ASCII 표기). 별칭은 정본으로 직행(체인 없음).
--   결과: 21그룹 / 별칭 23개. 탈락한 후보는 보고서 GRM_업체별칭후보_2026-11-05.md.
-- 시드는 grm_findings.FIRM_KEY_ALIASES 와 같아야 한다(tests/test_firm_alias_groups.py 가 고정).
-- ★알려진 영향: 정적 업체 페이지는 정본 키로만 만든다 -> 문서 2건 이상이던 별칭 키의 옛 정적 주소가 사라진다
--   (dr reddy’s laboratories, people’s custom rx and clinical care). 동적 /findings/firm/?key= 는 그대로 동작.

insert into public.firm_alias (alias_key, canonical_key) values
  ('american red cross donor and client support center', 'american red cross - donor and client support center'),
  ('aurobindo pharma - unit 4', 'aurobindo pharma unit 4'),
  ('brookfield medical / surgical supply', 'brookfield medical/surgical supply'),
  ('brookfield medical /surgical supply', 'brookfield medical/surgical supply'),
  ('crosby’s drugs', 'crosbys drugs'),
  ('curexa - east curexa', 'curexa – east curexa'),
  ('denver solutions leiter''s health', 'denver solutions leiters health'),
  ('denver solutions leiter’s health', 'denver solutions leiters health'),
  ('dignity health- northridge hospital medical center', 'dignity health-northridge hospital medical center'),
  ('dougherty’s pharmacy', 'dougherty''s pharmacy'),
  ('dr reddy’s laboratories', 'dr reddy''s laboratories'),
  ('dr reddy’s laboratories biologics', 'dr reddy''s laboratories biologics'),
  ('dyna labs', 'dynalabs'),
  ('fin vector oy', 'finvector oy'),
  ('grandpa’s compounding pharmacy', 'grandpa''s compounding pharmacy'),
  ('intratheca l compounding specialists', 'intrathecal compounding specialists'),
  ('mandell’s clinical pharmacy', 'mandell''s clinical pharmacy'),
  ('maplerose enterprises pencol pharmacy', 'maple rose enterprises pencol pharmacy'),
  ('master pharm', 'masterpharm'),
  ('people’s custom rx and clinical care', 'people''s custom rx and clinical care'),
  ('right value drug stores carie boyd’s prescription shop', 'right value drug stores carie boyd''s prescription shop'),
  ('rx south rx3 pharmacy', 'rxsouth ( rx3 pharmacy)'),
  ('veganicskn', 'veganic skn')
on conflict (alias_key) do nothing;

-- 검증(적용 전후 읽기 전용): 업체 수는 -23, 지적 행 합은 불변이어야 한다.
--   select count(distinct coalesce(a.canonical_key, f.firm_key)) as firm_groups, count(*) as findings
--   from public.findings f left join public.firm_alias a on a.alias_key = f.firm_key
--   where f.scope_status = 'ok';
--   -- 적용 전 3,832 / 26,744  ->  적용 후 3,809 / 26,744
--   select count(*) from public.firm_alias;   -- 26 -> 49
