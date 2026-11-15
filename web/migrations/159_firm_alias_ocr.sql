-- 업체명 OCR 갈라짐 별칭 (11-15 대표 신고 후속 — "Medlmmune UK Limited"). 105·115·118 위에 가산, 기존 행 불변.
-- 원문(findings.firm_name·firm_key)은 바꾸지 않는다. firm_alias 에 3행을 넣어 업체 묶음(grm_firm_group_keys)만 합친다.
--
-- 찾은 방법(11-15, 읽기 전용 SQL): firm_key 를 OCR 혼동 정규화(rn→m · l→i · 0→o · 1→l · 괄호 안 제거 · 구두점 제거)한 뒤
-- 같은 값이 되는 서로 다른 키 묶음 중 firm_alias 에 아직 없는 것. 한글 업체명은 정규화 대상이 아니라 뺐다.
-- 결과 5묶음 — 채택 3(모두 FEI 일치), 제외 2:
--   · brookfield medical / surgical supply·/surgical supply → 이미 118 에서 'brookfield medical/surgical supply' 로 묶임.
--   · joinn laboratories vs joinn laboratories (suzhou) → 다른 사업장일 수 있어 제외(FEI 확인 전 보류).
-- 신뢰도 순:
--   1) eurorni → euromi                FEI 3007697975 (Belgium) — OCR rn→m. 별칭 1행·정본 6행.
--   2) medlmmune uk (astrazeneca/supply biologics) → medimmune uk   FEI 3004066112 (UK) — OCR I→l. 별칭 4행·정본 1행.
--   3) bioclinica /clario (eresearch technology ) → bioclinica / clario (eresearch technology )   FEI 3032745961 (China) — 띄어쓰기.
-- 되돌림: delete from public.firm_alias where alias_key in (아래 3개);  (함수·RPC 무변경이라 그것으로 끝)
-- 시드는 grm_findings.FIRM_KEY_ALIASES 와 같아야 한다(tests/test_firm_alias_groups.py 가 고정).

insert into public.firm_alias (alias_key, canonical_key) values
  ('bioclinica /clario (eresearch technology )', 'bioclinica / clario (eresearch technology )'),
  ('eurorni', 'euromi'),
  ('medlmmune uk (astrazeneca/supply biologics)', 'medimmune uk')
on conflict (alias_key) do nothing;

-- 검증(적용 전후 읽기 전용): 업체 묶음 수는 -3, 지적 행 합은 불변이어야 한다.
--   select count(distinct coalesce(a.canonical_key, f.firm_key)) as firm_groups, count(*) as findings
--   from public.findings f left join public.firm_alias a on a.alias_key = f.firm_key
--   where f.scope_status = 'ok';
--   select count(*) from public.firm_alias;   -- +3
