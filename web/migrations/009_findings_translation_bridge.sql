-- FIND-1 번역 파이프라인 RLS 브릿지 — findings_translate.py 가 anon 키(PostgREST)로 미번역
-- 행을 조회/검증하지 못하는 구조 결함을 RPC 2개로 우회한다.
--
-- 결함: 006_findings_publish_gate.sql 의 공개 게이트(anon/authenticated SELECT 는
-- finding_text_ko <> '' or finding_language = 'KO' 인 행만 허용)가 "번역할 대상"을 anon
-- 에게서 전부 숨긴다. findings_translate.py 의 --source supabase 모드는:
--   ① --export(_fetch_untranslated_supabase): 미번역 행을 anon GET 으로 조회 -> 006 이
--      전부 숨겨 0건. 번역할 게 없다고 오판한다.
--   ② --apply 의 라이브 검증(_fetch_live_rows_for_ids_supabase): 원문 byte 대조용 라이브
--      스냅샷을 anon GET 으로 조회 -> 미번역 행(대조 대상 그 자체)이 안 보여 검증이 전부
--      실패한다.
-- 실측(컨트롤 타워, 2026-07-11): anon 으로 미번역 행 조회 = 0건(실제 미번역 7,500건+).
-- 같은 날 일일 번역 배치가 "1건 번역, 잔여 0건"으로 끝난 실사고가 이 결함의 직접 증거다
-- (그 1건은 finding_language='KO' 라 게이트를 우연히 통과해 보였던 행).
--
-- ============================================================================
-- ★보안 예외(의도적, 반드시 숙지): 아래 두 RPC 는 006 게이트에 숨겨진 행의 finding_text
-- (영문 원문)를 anon 에게 그대로 반환한다 — 007/008_findings_*.sql 의 "원문 텍스트/URL
-- 필드는 어떤 경로로도 반환하지 않는다"는 안전 계약과 정반대다. 이 파일은 그 계약의
-- 예외이며, 007/008 에는 여전히 적용된다(이 파일이 그 계약을 깨지 않는다).
--
-- 근거: (1) finding_text 의 원문은 애초에 공개 규제 문서(FDA 483/Warning Letter, MFDS
-- 공개 행정처분 등)에서 그대로 발췌한 것이라 기밀이 아니다 — 이미 각 기관 웹사이트에
-- 공개돼 있다. (2) 006 게이트는 기밀 보호 게이트가 아니라 "웹 열람 품질" 게이트다:
-- 국문 번역이 끝나지 않은 행을 최종 사용자 화면(검색/트렌드)에 노출하지 않으려는
-- 목적일 뿐, 원문 자체를 감추려는 목적이 아니다. (3) 이 RPC 들의 유일한 소비자는
-- findings_translate.py(번역 파이프라인 도구)이며, 번역을 하려면 원문을 읽어야 하는
-- 것이 그 도구의 존재 이유다.
-- ============================================================================
--
-- security definer/search_path 고정/revoke-then-grant 관례는 007/008 과 동일하다.
-- 전제: 002_findings.sql + 006_findings_publish_gate.sql 이 먼저 적용되어 있어야 한다.
-- 이 파일은 함수 2개만 추가하며 기존 테이블·RLS·정책·007/008 의 함수는 전혀 건드리지
-- 않는다.

-- public.findings_translation_queue(p_limit): 미번역 행 큐. findings_translate.py 의
-- _EXPORT_COLUMNS_SUPABASE 와 정확히 동일한 컬럼 집합을 items 에 담는다(finding_id/
-- source/agency/category_code/category_label_ko/published_date/firm_name/finding_text/
-- finding_text_ko/translation_method). finding_text_ko/translation_method 는 필터
-- 조건상 이 함수가 반환하는 모든 행에서 항상 빈 문자열이다(레거시 REST GET 경로와
-- 동일 계약 -- 클라이언트가 별도로 빈 문자열 스탬프를 할 필요가 없다).
-- p_limit 은 [1, 500] 범위로 클램프한다(기본 200) -- 호출측이 큰 값을 넘겨도 한 번의
-- 큐 조회가 과도한 페이로드를 반환하지 않도록 하는 방어.
create or replace function public.findings_translation_queue(p_limit integer default 200)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'untranslated_total', (
      select count(*) from public.findings where coalesce(finding_text_ko, '') = ''
    ),
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'finding_id', finding_id,
          'source', source,
          'agency', agency,
          'category_code', category_code,
          'category_label_ko', category_label_ko,
          'published_date', published_date,
          'firm_name', firm_name,
          'finding_text', finding_text,
          'finding_text_ko', finding_text_ko,
          'translation_method', translation_method
        )
        order by published_date desc, finding_id asc
      )
      from (
        select *
        from public.findings
        where coalesce(finding_text_ko, '') = ''
        order by published_date desc, finding_id asc
        limit greatest(1, least(coalesce(p_limit, 200), 500))
      ) t
    ), '[]'::jsonb)
  );
$$;

-- public.findings_translation_rows(p_finding_ids): --apply 의 라이브 검증(원문 byte 대조)
-- 이 쓰는 3컬럼(finding_id/finding_text/finding_text_ko)만 반환한다. 006 게이트로 숨겨진
-- 미번역 행도 포함해서 반환하는 것이 이 함수의 존재 이유다(검증 대상이 바로 그 행들
-- 이므로). 입력 배열은 앞 500개만 처리한다(단순 클램프 -- 초과분은 그냥 무시되며,
-- findings_translate.py 쪽 클라이언트 배치 크기(_SUPABASE_VALIDATE_BATCH_SIZE=20)가
-- 이미 훨씬 작으므로 실사용 경로에서 이 클램프에 걸릴 일은 없다 -- 방어선일 뿐이다).
create or replace function public.findings_translation_rows(p_finding_ids text[])
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'finding_id', finding_id,
      'finding_text', finding_text,
      'finding_text_ko', finding_text_ko
    )
  ), '[]'::jsonb)
  from public.findings
  where finding_id = any(coalesce(p_finding_ids, '{}'::text[])[1:500]);
$$;

-- Supabase 는 함수 생성 시 기본적으로 PUBLIC 에 execute 를 부여할 수 있으므로, 먼저
-- 전면 회수한 뒤 anon/authenticated 로만 명시적으로 재부여한다(007/008 과 동일 관례).
revoke all on function public.findings_translation_queue(integer) from public;
revoke all on function public.findings_translation_rows(text[]) from public;

grant execute on function public.findings_translation_queue(integer) to anon, authenticated;
grant execute on function public.findings_translation_rows(text[]) to anon, authenticated;

-- 검증: 미번역 큐가 실제로 행을 반환하는지(006 게이트로 가려졌던 행 포함).
-- set role anon; select jsonb_array_length(public.findings_translation_queue(5)->'items');
-- reset role;
-- 검증: untranslated_total 이 실제 미번역 전체 수와 일치하는지.
-- select (public.findings_translation_queue(1)->>'untranslated_total')::int
--   = (select count(*) from public.findings where finding_text_ko = '');
-- 검증: rows RPC 가 미번역 행도 포함해 원문을 반환하는지(anon 세션에서).
-- set role anon; select public.findings_translation_rows(array['<some-untranslated-finding-id>']);
-- reset role;
-- 검증: 입력 500개 초과 시 앞 500개만 처리되는지(과도한 배열을 넘겨도 에러 없이 클램프).
-- select jsonb_array_length(public.findings_translation_rows(
--   (select array_agg(finding_id) from public.findings limit 600)
-- ));
