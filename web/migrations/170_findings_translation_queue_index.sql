-- ============================================================================
-- 170_findings_translation_queue_index.sql — [번역 대기열 RPC 시간 초과] 큐 조건을 이미 있는 부분 인덱스
--   (128 ops_findings_untranslated_idx)와 **같은 글자**로 맞춰 findings 전 행을 두 번 훑지 않게 한다.
--
-- ★무엇이 깨졌나(2026-11-26): 번역팀 루틴이 쓰는 findings_translation_queue 가 statement timeout 을
--   두 번 냈다. 루틴은 **anon 키**로 부른다 — anon 의 statement_timeout 은 **3초**다(authenticator 8s 아님).
--   pg_stat_statements: 이 경로 평균 665ms·최대 2,986ms(= 3초 벽에 잘린 값). raw_signals 는 안 읽는다
--   (168/169 의 extraction_gap 과 뿌리가 다르다). 원인은 `coalesce(finding_text_ko, '') = ''` 조건이
--   부분 인덱스 조건(`finding_text_ko = ''`)과 글자가 달라 플래너가 인덱스를 못 쓰고 findings 30,337행을
--   **count 한 번·목록 한 번** 순차 탐색하는 것(5,890 buffers × 2). 평소엔 ~100ms 지만 공유 인스턴스
--   CPU 가 흔들리면(11-26 실측 같은 계산 1~19s) 3초를 넘는다.
--
-- ★고친 것: finding_text_ko 는 NOT NULL default ''(라이브 확인 — null 0행)이라 coalesce 를 빼도 결과가
--   같다. 빼면 두 탐색 모두 ops_findings_untranslated_idx 를 탄다(미번역 181행 기준 997 buffers·33ms).
--   나머지(필터·정렬·상한·반환 키·security definer)는 035 그대로 — 바뀐 글자는 두 where 절뿐이다.
--   권한은 create or replace 가 그대로 둔다(009 계약 — anon 실행 허용).
-- ★재발 방지: tests/test_findings_translation_queue_align.py 가 최신 정의의 큐 조건이 부분 인덱스
--   조건과 같은 글자인지 본다. findings_translate.py 는 RPC 마다 걸린 ms 를 남기고 anon 3초의 절반을
--   넘으면 경고한다.
--
-- 적용 후 확인(SQL 에디터):
--   explain select count(*) from public.findings
--     where finding_text_ko = '' and scope_status = 'ok' and review_status <> 'rejected';  -- Index Only Scan
--   select public.findings_translation_queue(1)->>'untranslated_total';  -- 적용 전과 같은 값
-- ============================================================================

create or replace function public.findings_translation_queue(p_limit integer default 200)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'untranslated_total', (
      select count(*) from public.findings
      where finding_text_ko = '' and scope_status = 'ok'
        and review_status <> 'rejected'
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
        where finding_text_ko = '' and scope_status = 'ok'
          and review_status <> 'rejected'
        order by published_date desc, finding_id asc
        limit greatest(1, least(coalesce(p_limit, 200), 500))
      ) t
    ), '[]'::jsonb)
  );
$$;
