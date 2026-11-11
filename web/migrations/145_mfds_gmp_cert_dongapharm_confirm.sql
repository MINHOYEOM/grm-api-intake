-- 145: 식약처 GMP 적합판정서 — 동아제약(주) 후보 3행을 확정 연결로(11-11 대표 확인).
--
-- 143 에서 후보(candidate, 0.60)로 둔 규칙: 업체명·당진 제조소 주소는 같으나 사업자번호가
-- 우리 DB 의 동아제약(주) 번호와 달랐다. 대표 확인 "당진 공장 = 동아제약"으로 확정한다.
-- 번호 차이는 사업장(제조소)별 사업자등록번호 때문이다(같은 회사의 다른 사업장 번호).
-- 다음 연결(grm_link_mfds_gmp_cert)부터 이 규칙이 firm_key 를 채운다(월간·매일 kg_link_new).
--
-- 롤백: 아래 update 를 status='candidate', method='name_and_address_bizrno_conflict',
--       confidence=0.60 으로 되돌리고 select public.grm_link_mfds_gmp_cert();

update public.mfds_gmp_cert_link_rule
   set status = 'confirmed',
       method = 'name_and_address',
       confidence = 0.90,
       evidence = '대표 확인(11-11): 당진 공장 = 동아제약. 업체명 같음 · 당진 제조소 주소 같음(합덕읍 인더스파크로 22). '
               || '사업자번호가 다른 이유: 판정서 번호(2048100130)는 사업장(제조소)별 번호이고 우리 DB 번호(2048640118)는 '
               || '다른 사업장 기록에서 온 것 — 같은 회사의 다른 사업장 번호',
       decided_on = '2026-11-11'
 where bizrno = '2048100130' and cert_firm_name = '동아제약(주)' and firm_key = '동아제약(주)';

select public.grm_link_mfds_gmp_cert();
