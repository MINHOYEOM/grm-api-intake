-- 브리프 카드 '이 업체 앞선 지적 기록' 한 줄 시범(설계서 GRM_카드_업체이력_시범설계_2026-11-06.md §9 PR 1).
-- 카드 목록(문서번호·카드 발행일·업체키·FEI)을 한 번에 받아, 카드마다
--   (a) 같은 FEI 의 앞선 문서 수·최근 게시월        -> "이 제조소" (확실, 483 용)
--   (b) 이름 묶음(별칭 포함) 앞선 문서 수·최근 게시월  -> "같은 이름 업체" (참고용)
--   (c) 이름 묶음 속 서로 다른 제조소(FEI) 수          -> 이름이 같아도 제조소가 여럿인지 판단용
-- 집계만 돌려주는 읽기 전용 함수 1개다. 옛 "규제 이력 N건"(014 findings_firm_counts)은
-- 이 카드 자신의 지적까지 세고 지적 개수를 세서 부풀려진다 -> 여기서는 "문서 수"를 세고
-- 이 카드 문서와 같은 호의 형제 카드는 센다 하지 않는다.
--
-- ============================================================================
-- 안전 계약(불가침, 007/013/014 와 동종): 집계(count)·월 문자열만 반환한다 — finding_text/
-- finding_text_ko/evidence_url 등 원문·URL 필드는 반환 표면(jsonb_build_object 키)에 없다.
-- scope_status='ok' 만 센다(010 관례). 기존 함수의 모양은 바꾸지 않는다(가산만, 새 함수 1개).
-- ============================================================================
--
-- 입력
--   p_cards   jsonb 배열. 원소 {"id": 카드 문서번호(=findings.document_id), "date": "YYYY-MM-DD" 카드 발행일,
--             "firm_key": 카드에서 뽑은 업체키(비면 이름 집계 생략), "fei": 7~10자리 숫자(아니면 FEI 집계 생략)}.
--             앞 200개만 처리(014 와 같은 한도). 형식이 틀린 원소(id 없음·날짜 형식 아님)는 결과에서 생략.
--   p_exclude text[]. 이번 호의 모든 카드 문서번호 — 같은 호 형제 카드끼리 서로를 세지 않게 뺀다.
--             앞 500개만 처리. ★009 함정: 배열 슬라이스는 괄호로 감싸야 42601 이 안 난다.
-- 세는 기준(설계서 부록 A)
--   published_date < 카드 발행일(같은 날은 안 센다) · document_id <> 카드 문서번호 · document_id 가 p_exclude 에 없음
--   · scope_status='ok' · 서로 다른 document_id 개수. 이름 묶음은 grm_firm_group_keys(105) 정확 일치 + 등록 별칭뿐
--   (유사도·부분 일치 금지).
-- 출력: jsonb 배열(입력 순서). 원소 {"id","fei_n","fei_last","name_n","name_last","name_sites"}
--   *_n 은 정수(0 가능, 집계 생략 시 0), *_last 는 'YYYY-MM' 또는 null.
--
-- 전제: 002·010·013(firm_key)·101(fei)·105(grm_firm_group_keys) 적용. grm_firm_group_keys 는 anon 에
-- 닫혀 있지만 이 함수가 security definer 라 안에서 호출된다.

create or replace function public.findings_card_history(
  p_cards jsonb,
  p_exclude text[] default '{}'::text[]
)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with c as (
    select
      t.e ->> 'id' as id,
      t.e ->> 'date' as card_date,
      nullif(t.e ->> 'firm_key', '') as firm_key,
      case when (t.e ->> 'fei') ~ '^[0-9]{7,10}$' then t.e ->> 'fei' end as fei,
      t.ord
    from jsonb_array_elements(
           case when jsonb_typeof(p_cards) = 'array' then p_cards else '[]'::jsonb end
         ) with ordinality as t(e, ord)
    where t.ord <= 200
      and (t.e ->> 'id') is not null
      and (t.e ->> 'date') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  ),
  ex as (
    -- NULL 원소가 있으면 `not in` 이 전부 거짓이 되므로 여기서 걸러 낸다.
    select x.document_id
    from unnest((coalesce(p_exclude, '{}'::text[]))[1:500]) as x(document_id)
    where x.document_id is not null
  )
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'id', c.id,
      'fei_n', coalesce(fe.n, 0),
      'fei_last', fe.last_ym,
      'name_n', coalesce(nm.n, 0),
      'name_last', nm.last_ym,
      'name_sites', coalesce(nm.sites, 0)
    )
    order by c.ord
  ), '[]'::jsonb)
  from c
  left join lateral (
    select count(distinct f.document_id) as n, substr(max(f.published_date), 1, 7) as last_ym
    from public.findings f
    where c.fei is not null
      and f.fei = c.fei
      and f.scope_status = 'ok'
      and f.published_date < c.card_date
      and f.document_id <> c.id
      and f.document_id not in (select document_id from ex)
  ) fe on true
  left join lateral (
    select count(distinct f.document_id) as n, substr(max(f.published_date), 1, 7) as last_ym,
           count(distinct f.fei) as sites
    from public.findings f
    where c.firm_key is not null
      and f.firm_key = any(public.grm_firm_group_keys(c.firm_key))
      and f.scope_status = 'ok'
      and f.published_date < c.card_date
      and f.document_id <> c.id
      and f.document_id not in (select document_id from ex)
  ) nm on true;
$$;

-- 014 관례: 전면 회수 후 필요한 role 에만 부여.
revoke all on function public.findings_card_history(jsonb, text[]) from public;

grant execute on function public.findings_card_history(jsonb, text[]) to anon, authenticated;

-- 검증(사람 실행용):
-- 1) select public.findings_card_history('[{"id":"fda483-195525","date":"2026-10-30","firm_key":"agc biologics","fei":"3020270660"}]'::jsonb);
--    -> AGC S.p.A.(이탈리아): fei_n=0 이어야 한다(미국 AGC 기록 2건은 name_n 쪽에만 잡힘).
-- 2) select public.findings_card_history(null);  -> []
-- 3) 반환 원소에 finding_text/evidence_url 키가 없는지 육안 확인.
