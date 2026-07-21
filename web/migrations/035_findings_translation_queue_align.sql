-- [FIND-1 · 2026-07-21] 번역 큐를 공개 게이트와 정합시킨다 — rejected 제외.
-- 배경: 일일 번역 루틴(grm-findings-weekly-translate)은 findings_translation_queue(009,
-- SECURITY DEFINER·006 RLS 우회)로 미번역 findings 를 가져온다. 이 큐는 scope_status='ok'
-- 는 걸었지만(010) review_status='rejected'(검수에서 오추출로 판정 → 034 로 공개 게이트에서
-- 숨김)는 안 걸어, 루틴이 **영영 공개되지 않을 rejected 오추출을 번역**하느라 하루 100건
-- 상한 슬롯을 낭비했다. untranslated_total 도 그만큼 부풀어 P0 백로그 모니터의 격차와 어긋난다.
--
-- 034(공개 게이트·findings_stats)와 동일 필터 `review_status <> 'rejected'` 를 큐에 추가한다.
-- 짝 RPC findings_translation_rows 는 finding_id 로 특정 행만 재조회(apply 검증용)하므로
-- 변경 불필요 — 루틴은 큐가 고른 항목만 넘긴다. 라이브 정의(pg_get_functiondef) 기반, diff 는
-- count·items 두 where 절에 필터 추가뿐(009 의 나머지 계약·정렬·상한 불변).
--
-- 불변식: review_status 는 NOT NULL(002)이라 `<> 'rejected'` 가 accepted/needs_review 를
-- 포함한다. 되돌림 = 이 필터 제거. 신규 노출 표면 없음(집계·읽기 전용).

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
      where coalesce(finding_text_ko, '') = '' and scope_status = 'ok'
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
        where coalesce(finding_text_ko, '') = '' and scope_status = 'ok'
          and review_status <> 'rejected'
        order by published_date desc, finding_id asc
        limit greatest(1, least(coalesce(p_limit, 200), 500))
      ) t
    ), '[]'::jsonb)
  );
$$;

-- 검증(라이브 적용 시):
-- 1) 큐 total 이 rejected 미번역분만큼 줄었는지(034 후 P0 격차와 일치):
--    select public.findings_translation_queue(1)->>'untranslated_total';
-- 2) items 에 review_status='rejected' finding_id 가 없는지(큐는 finding_id 만 노출하므로
--    별도 대조): rejected 미번역 finding_id 가 items 에 안 들어오는지 확인.
