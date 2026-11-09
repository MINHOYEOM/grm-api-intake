-- [SEO 2단계 2026-11-09] 문서 페이지 "다른 업체의 비슷한 지적" — 공개 엣지 읽기 RPC. 가산만(함수 1, 표·기존 RPC 불변).
--
-- 왜: finding_similar(114) 는 anon 에 닫혀 있다(revoke all). 문서 정본 갱신(findings_docs_refresh.py)은
--     공개 집합을 정확히 맞추려고 **anon 키로만** 읽는 규율이라 이 표에 닿을 길이 없었다.
--     이 함수는 **양 끝 지적이 모두 공개 행**(findings_public_read RLS 와 같은 조건)인 엣지만 돌려준다 —
--     anon 이 findings_search 로 이미 볼 수 있는 두 지적 사이의 점수뿐이라 공개 범위가 넓어지지 않는다.
--     basis(근거 문자열)·rank·계산 장부는 내보내지 않는다.
--
-- 호출: POST /rest/v1/rpc/findings_similar_edges {"p_finding_ids": [...]} — 한 번에 최대 1,000개.
--   p_finding_ids 에 닿는 엣지를 **양방향**(finding_id 쪽·similar_id 쪽) 모두 돌려준다.
--   PK(finding_id, …)·idx_finding_similar_similar_id 두 색인을 탄다.
--
-- 적용: 비서실이 dry-run 후 적용. 적용 전에는 갱신 스크립트가 404 를 받고 지난 findings_similar.json 을
--   그대로 두며(실패 아님), 렌더러는 파일이 없거나 비어 있으면 블록을 내지 않는다.

create or replace function public.findings_similar_edges(p_finding_ids text[])
returns table (finding_id text, similar_id text, score numeric)
language sql
stable
security definer
set search_path = public
as $$
  select s.finding_id, s.similar_id, s.score
    from public.finding_similar s
    join public.findings a on a.finding_id = s.finding_id
    join public.findings b on b.finding_id = s.similar_id
   where coalesce(cardinality(p_finding_ids), 0) between 1 and 1000
     and (s.finding_id = any (p_finding_ids) or s.similar_id = any (p_finding_ids))
     and (a.finding_text_ko <> '' or a.finding_language = 'KO')
     and a.scope_status = 'ok' and a.review_status <> 'rejected'
     and (b.finding_text_ko <> '' or b.finding_language = 'KO')
     and b.scope_status = 'ok' and b.review_status <> 'rejected'
$$;

-- `revoke from public` 만으로는 anon 의 EXECUTE 가 남는다(10-28 교훈) — 역할을 명시하고, 의도한 역할에만 다시 준다.
revoke all on function public.findings_similar_edges(text[]) from public, anon, authenticated;
grant execute on function public.findings_similar_edges(text[]) to anon, authenticated, service_role;

-- 적용 후 확인:
--   select has_function_privilege('anon', 'public.findings_similar_edges(text[])', 'EXECUTE');  -- true
--   select count(*) from public.findings_similar_edges(array(select finding_id from public.findings
--     where scope_status = 'ok' limit 300));                                                   -- > 0, 3초 안
