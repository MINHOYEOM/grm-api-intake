-- #1759 초안 — 대표 승인 전에는 실행하지 않는다 (DB 쓰기).
-- 대상: scope_status='non_pharma' AND review_status='needs_review' 인 FDA WL 26건.
-- 2026-11-20 SELECT 확인: 식품(제과·분식·씨푸드 HACCP·종자 등) 업체 24건 + 담배 1건(gethyppe.com)
-- + Zap Expo Center 1건(미확인 — 아래 사전 확인 쿼리로 본문 확인). 전부 의약품 GMP 범위 밖.
-- 적용 효과: 검색 화면은 이미 scope<>'ok' 로 가려 변화 없음. 장부(큐)만 정리 → #1759 자동 종료.

-- 0) 사전 확인 (SELECT) — 26건이어야 한다. Zap Expo 본문(21 CFR part ?)이 의약품이 아닌지 눈으로 확인.
select firm_name, count(*) from public.findings
 where scope_status = 'non_pharma' and review_status = 'needs_review'
 group by 1 order by 1;
select finding_id, left(finding_text, 300) from public.findings
 where firm_name = 'Zap Expo Center Inc.' and review_status = 'needs_review';

-- 1) 적용 (트랜잭션, 가드 포함 — 이미 바뀐 행은 매칭 0)
begin;
update public.findings
   set review_status = 'rejected'
 where source = 'FDA Warning Letter'
   and scope_status = 'non_pharma'
   and review_status = 'needs_review'
   and ingested_at >= '2026-11-12' and ingested_at < '2026-11-13';   -- 이번 배치 한정
-- 기대: UPDATE 26. 아니면 rollback.
commit;

-- 2) 사후 확인
select review_status, count(*) from public.findings
 where scope_status = 'non_pharma' and source = 'FDA Warning Letter' group by 1;

-- 되돌리기(필요 시): 같은 조건으로 review_status='needs_review' 복원.
-- 대안: 'rejected' 대신 'accepted' (11-08 선례 — 213건 WL non_pharma 가 accepted). 단 accepted 는
-- 범위가 ok 로 재분류되면 그대로 공개되므로, 식품·담배 서한에는 rejected 가 안전하다.
