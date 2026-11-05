-- 지식그래프 — 업체명 별칭 확대 2차 (후보 B 검수분). 105·115 위에 가산, 기존 행 불변.
-- firm_key 는 바꾸지 않는다(생성 컬럼·URL·firm_watchlist 불변). firm_alias 에 35행을 넣을 뿐이고
-- 함수·RPC 는 105 의 grm_firm_group_keys 를 그대로 쓴다(반환 shape 무변경, 멱등).
--
-- 근거: 2026-11-05 후보 B(같은 FEI + 철자·단복수·접두 차이) 36쌍을 대표 승인 하에 검수. 쌍마다 읽기 전용 SQL 로
-- 재검증했다: 별칭·정본 키가 findings 에 모두 존재, FEI 가 있는 쌍은 FEI 겹침(excelvision 만 예외 — 정본에
-- FEI 없음, 하이픈 차이뿐), 별칭이 기존 별칭/정본과 체인·충돌 없음. 정본이 이미 다른 별칭의 정본인 경우
-- (intrathecal, denver, brookfield, aurobindo unit 4)는 체인이 아니라 같은 정본으로 모이는 것이다.
-- 제외 1건: 'sun pharmaceutical medicare' -> 'sun pharmaceuticals medicare'. 별칭 쪽이 105 에서 이미
-- 'sun pharmaceutical medicare (spml)' 의 정본이라 체인이 된다(후속에서 105 행 재지정 여부 판단).
-- 주의: alvotech hf(HU+IS 행)는 alvotech(IS) 로 모인다 — 같은 FEI 3013702557.
-- 시드는 grm_findings.FIRM_KEY_ALIASES 와 같아야 한다(tests/test_firm_alias_groups.py 가 고정).

insert into public.firm_alias (alias_key, canonical_key) values
  ('accurate rx pharmacy consulting accurate rx pharmacy', 'accurate rx pharmacy consulting'),
  ('acrx specialty pharmacy lnc', 'acrx specialty pharmacy'),
  ('alvotech hf', 'alvotech'),
  ('american red cross greater alleghenies regio', 'american red cross greater alleghenies region'),
  ('aurobindo pharma unit iv', 'aurobindo pharma unit 4'),
  ('avella of deer valley avella', 'avella of deer valley'),
  ('barclay luke & pillai specialty pharmacy pllc', 'barclay luke pillai specialty pharmacy pllc'),
  ('barclay luke & pillai specialty pharmacy pllc meta pharmacy services', 'barclay luke pillai specialty pharmacy pllc'),
  ('brookfield medical surgical supplies', 'brookfield medical/surgical supply'),
  ('denver solutions oba leiters health', 'denver solutions leiters health'),
  ('dr reddy''s laboratories cto -- unit 6', 'dr reddy''s laboratories unit 6'),
  ('dr reddy''s laboratories cto unit vi', 'dr reddy''s laboratories unit 6'),
  ('excelvision - fareva', 'excelvision fareva'),
  ('fagron compounding service fagron sterile services', 'fagron compounding services'),
  ('fagron compounding services fagron sterile services', 'fagron compounding services'),
  ('first royal care red mountain compounding', 'first royal care red mountain compounding pharmacy'),
  ('fusion iv pharmaceuticals axia pharmaceutical', 'fusion iv pharmaceuticals axia pharmaceuticals'),
  ('imclone systems ll c', 'imclone systems'),
  ('intas pharmaceutical', 'intas pharmaceuticals'),
  ('intrathecal compounding specialist', 'intrathecal compounding specialists'),
  ('isomeric pharmacy solution', 'isomeric pharmacy solutions'),
  ('jiangsu hengrui pharmceuticals', 'jiangsu hengrui pharmaceuticals'),
  ('joe wise pharmacy wise pharmacy', 'joe wise pharmacy'),
  ('kalman health & wellness essential wellness pharma 2', 'kalman health & wellness essential wellness pharma'),
  ('kelley-ross & associates kelley-ross compounding pharmacy', 'kelley-ross compounding pharmacy'),
  ('leiter''s enterprises leiter''s', 'leiter’s enterprises'),
  ('new vitalis pharmacy new vitalis pharmacy', 'new vitalis pharmacy'),
  ('nubratori nubratori rx', 'nubratori'),
  ('pharmedium service', 'pharmedium services'),
  ('samsung biologic', 'samsung biologics'),
  ('suzhou suncadia biopharmaceutical', 'suzhou suncadia biopharmaceuticals'),
  ('triangle compounding cary nc', 'triangle compounding'),
  ('unique pharmaceuticals', 'unique pharmaceutical'),
  ('w & c apothecary the apothecary', 'w & c the apothecary'),
  ('zions rx formulations services rx formulation serv', 'zions rx formulations services rx formulations serv')
on conflict (alias_key) do nothing;

-- 검증(적용 전후 읽기 전용): 업체 수는 -35, 지적 행 합은 불변이어야 한다.
--   select count(distinct coalesce(a.canonical_key, f.firm_key)) as firm_groups, count(*) as findings
--   from public.findings f left join public.firm_alias a on a.alias_key = f.firm_key
--   where f.scope_status = 'ok';
--   select count(*) from public.firm_alias;   -- 49 -> 84
