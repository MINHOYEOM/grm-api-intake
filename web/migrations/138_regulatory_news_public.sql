-- [최근 규제 소식 2026-11-10] /news/ 정적 페이지용 공개 읽기 RPC. 가산만(함수 1, 표·기존 RPC 불변).
--
-- 왜: raw_signals 는 anon 에 전면 차단돼 있다(002 — raw_json/row_json 비공개 계약). 주간 브리프에 안 실린
--     비-findings 소스(ECA·ISPE·EMA·관보·OpenFDA/HC/MHRA 회수·PIC/S·WHO·MFDS 지침/공고)의 수집분은
--     브리프 밖에서는 어디에도 보이지 않았다. 이 함수는 그중 **공개 안전 5개 컬럼만** 돌려준다:
--     source · source_kind · published_date · title · official_url (원문 링크).
--     raw_json/row_json/업체명/수집 시각/내부 id 는 내보내지 않는다.
--
-- 무엇을 거르나(서버에서):
--   · 소스 허용목록 — findings 로 흘러가는 소스(FDA 483·WL·HC 실사·MFDS 실사/회수/행정처분·EU/MHRA NCR)는
--     이미 findings 면에 있으므로 제외. MFDS 는 지침·입법예고·고시 4종 source_kind 만.
--   · 브리프 파이프라인이 범위 밖으로 판정한 행(row_json.qa_relevance = 'Unrelated') 제외.
--   · findings 에 연결된 raw_signal 제외(이중 노출 방지).
--   · 제목 공백·원문 링크가 http(s) 가 아닌 행·날짜 형식이 깨진 행 제외.
--
-- 호출: POST /rest/v1/rpc/regulatory_news {"p_days": 60}   (p_days 1..400, 결과 상한 3,000건)
--
-- 적용: 비서실이 dry-run 후 적용. 적용 전에는 갱신 스크립트(news_refresh.py)가 404 를 받고 지난 news.json 을
--   그대로 두며(실패로 취급), 렌더러는 파일이 없으면 페이지·링크·sitemap 항목을 모두 내지 않는다.
-- 되돌리기: drop function if exists public.regulatory_news(integer);

create or replace function public.regulatory_news(p_days integer default 60)
returns table (source text, source_kind text, published_date text, title text, official_url text)
language sql
stable
security definer
set search_path = public
as $$
  -- [11-10 dry-run] 전 행에 정규식·jsonb 캐스트를 먼저 걸면 2.0s(anon 3초 근접). 소스·날짜로 먼저 좁힌 뒤
  -- 나머지 조건을 거는 materialized CTE 로 72ms, 결과 403행 동일.
  with c as materialized (
    select rs.raw_signal_id, rs.source, rs.source_kind, rs.published_date, btrim(rs.title) as title,
           rs.official_url, rs.row_json
      from public.raw_signals rs
     where p_days between 1 and 400
       and (rs.source in ('ECA Academy', 'ISPE', 'EMA', 'Federal Register', 'OpenFDA Recall',
                          'Health Canada', 'MHRA Inspectorate', 'PIC/S', 'WHO')
            or (rs.source = 'MFDS' and rs.source_kind in
                  ('guidance-industry', 'guidance-internal', 'legislative-notice', 'regulation-final')))
       and rs.published_date >= to_char(current_date - p_days, 'YYYY-MM-DD')
       and rs.published_date <= to_char(current_date + 1, 'YYYY-MM-DD')
  )
  select c.source, c.source_kind, c.published_date, c.title, c.official_url
    from c
   where c.published_date ~ '^\d{4}-\d{2}-\d{2}$'
     and c.title <> ''
     and c.official_url ~* '^https?://'
     and coalesce((c.row_json::jsonb) ->> 'qa_relevance', '') <> 'Unrelated'
     and not exists (select 1 from public.findings f where f.raw_signal_id = c.raw_signal_id)
   order by c.published_date desc, c.source, c.source_kind, c.title
   limit 3000
$$;

-- `revoke from public` 만으로는 anon 의 EXECUTE 가 남는다(10-28 교훈) — 역할을 명시하고, 의도한 역할에만 다시 준다.
revoke all on function public.regulatory_news(integer) from public, anon, authenticated;
grant execute on function public.regulatory_news(integer) to anon, authenticated, service_role;

-- 적용 후 확인:
--   select has_function_privilege('anon', 'public.regulatory_news(integer)', 'EXECUTE');   -- true
--   select source, count(*) from public.regulatory_news(60) group by 1 order by 2 desc;     -- 합 ≈ 330, 3초 안
