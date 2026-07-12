-- FIND-1 브리프→업체 프로파일 브릿지(트랙 A → 트랙 B) — 주간 브리프 카드에 빌드시
-- 스탬프된 firm_key(render.py, 카드 facts 업체명 → 013_findings_firm_key.sql 의
-- grm_normalize_firm_name 파리티)들을 한 번에 조회해 "규제 이력 N건" 링크 판단에
-- 쓸 카운트만 돌려주는 RPC. 007/009/013 관례 그대로(security definer/stable/
-- language sql/search_path 고정/revoke-then-grant).
--
-- 근거: 주간 브리프(트랙 A)는 큐레이션 인텔리전스, findings DB(트랙 B)는 참조
-- 인프라다. 상용 규제 인텔리전스 서비스의 핵심 패턴은 "알림 → 업체 도시에" 연결 —
-- 브리프 카드에서 "이 업체가 과거에 뭘 지적받았나"로 1클릭 진입하게 하려면, 브리프
-- 페이지가 카드마다 개별 조회하지 않고 카드 수만큼의 firm_key 를 한 번에 넘겨
-- 카운트만 받아오는 저비용 RPC 가 필요하다(013_findings_firm_key.sql 의
-- findings_firm_profile 은 업체 1곳의 전체 프로파일용이라 이 용도엔 과하다).
--
-- ============================================================================
-- 안전 계약(불가침, 007/013 과 동종): 이 함수는 집계(count)만 반환한다 —
-- finding_text/finding_text_ko 등 원문 텍스트·URL 필드는 이 함수의 반환 표면
-- (jsonb_build_object 키 목록)에 전혀 등장하지 않는다. scope_status='ok' 필터만
-- (010_findings_scope_purity.sql 관례 계승 — non_pharma/fragment 로 플래그된
-- 행은 집계에서도 제외).
-- ============================================================================
--
-- ★009 함정 필수 준수: 함수 인자로 받은 배열을 바로 슬라이스하면 42601 구문
-- 오류가 난다(라이브 실측된 함정 — 009_findings_translation_bridge.sql
-- findings_translation_rows 참조). 반드시 괄호로 감싸야 한다:
--   `(coalesce(p_firm_keys, '{}'::text[]))[1:200]`
-- 회귀 테스트는 tests/test_findings_firm_key.py 와 같은 계열(013)에 이 파일
-- 전용으로 추가한다(tests/test_findings_firm_counts_rpc.py).
--
-- 입력: p_firm_keys(text[]) — 브리프 페이지(brief.html)가 런타임에 카드
-- data-firm-key 스탬프를 전부 모아 고유 배열로 1회 호출한다(카드 수만큼 개별
-- 호출 금지 — brief-firm-link.js 계약). 앞 200개만 처리(단순 클램프 — 브리프
-- 1호는 수십 장 규모라 실사용 경로에서 이 한도에 걸릴 일은 거의 없지만 방어선은
-- 둔다. 009 의 500 보다 낮게 잡은 이유: 이 RPC 의 소비자는 "카드 수" 상한이 있는
-- 브리프 페이지뿐이라 500 보다 보수적인 값으로도 충분하다).
--
-- 존재하지 않는 firm_key(013 미배선·오타·아직 findings 미축적)는 결과 배열에서
-- 아예 생략한다(0건 행을 만들지 않음) — 클라이언트가 "배열에 없으면 0건"으로
-- 처리하는 것이 계약이다. findings_firm_profile(p_firm_key 단수, 미존재 시 빈
-- 구조 반환) 과는 다른 계약이다 — 이 함수는 다건 조회라 0건 행을 채우면 페이로드
-- 만 커지고 클라이언트가 어차피 없음=0 으로 취급하므로 무의미하다.
--
-- 전제: 002_findings.sql + 010_findings_scope_purity.sql + 013_findings_firm_key.sql
-- (firm_key generated 컬럼)이 먼저 적용돼 있어야 한다. 이 파일은 함수 1개만
-- 추가하며 기존 테이블·RLS·007/009/010/013 의 함수는 전혀 건드리지 않는다.

create or replace function public.findings_firm_counts(p_firm_keys text[])
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'firm_key', firm_key,
      'findings', cnt,
      'documents', doc_cnt
    )
    order by cnt desc, firm_key asc
  ), '[]'::jsonb)
  from (
    select
      firm_key,
      count(*) as cnt,
      count(distinct raw_signal_id) as doc_cnt
    from public.findings
    where scope_status = 'ok'
      and firm_key = any((coalesce(p_firm_keys, '{}'::text[]))[1:200])
    group by firm_key
  ) t;
$$;

-- Supabase 는 함수 생성 시 기본적으로 PUBLIC 에 execute 를 부여할 수 있으므로, 먼저
-- 전면 회수한 뒤 anon/authenticated 로만 명시적으로 재부여한다(007/009/013 관례와
-- 동일).
revoke all on function public.findings_firm_counts(text[]) from public;

grant execute on function public.findings_firm_counts(text[]) to anon, authenticated;

-- 검증(사람 실행용, 프로덕션 SQL Editor — 컨트롤 타워 라이브 dry-run):
-- 1) 존재하는 firm_key 배열 → cnt>0 인 항목만 반환하는지:
--    select public.findings_firm_counts(array['sca pharmaceuticals']);
-- 2) 존재하지 않는 키를 섞어도 그 키만 결과에서 생략되는지(에러 아님):
--    select public.findings_firm_counts(array['sca pharmaceuticals', '__does_not_exist__']);
-- 3) 200개 초과 배열도 에러 없이 앞 200개만 처리되는지:
--    select public.findings_firm_counts(
--      (select array_agg(firm_key) from public.findings limit 250)
--    );
-- 4) 안전 계약(원문 텍스트 미반환) 수동 확인 — 반환 jsonb 배열 원소 어디에도
--    finding_text/finding_text_ko 키가 없어야 한다:
--    select public.findings_firm_counts(
--      (select array_agg(distinct firm_key) from public.findings limit 5)
--    );
-- 5) 빈 배열/NULL 입력도 에러 없이 빈 배열을 반환하는지:
--    select public.findings_firm_counts(array[]::text[]);
--    select public.findings_firm_counts(null);
