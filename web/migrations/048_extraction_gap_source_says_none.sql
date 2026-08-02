-- ============================================================================
-- 048_extraction_gap_source_says_none.sql — [운영 감시 정밀화] "원문이 없다고 말한 문서"
--   를 추출 격차 모집단에서 제외한다.
--
-- ★왜(2026-08-02, 046 첫 실행 후속): 046 이 식약처 gmp-inspection 12건을 "지적사항 0건"
--   으로 지목했는데, **전수 확인 결과 12건 모두 0건이 정상이었다**. 누락된 지적사항은
--   0건이다. 갈래는 둘이다:
--     · 5건 — 원문에 `평가 결과 지적(보완)사항(Deficiencies) 없음` 이라고 적혀 있고
--             수집기가 `attachment_deficiency_assessment='none'` 으로 정확히 읽었다.
--     · 7건 — 수입 **사전 GMP 평가** 보고서라 "지적(보완)사항" 섹션 자체가 없고 결론이
--             `❍ 실사 결과: 적합` 한 줄뿐이다. 수집기가 그 어법을 몰라 `unknown` 으로
--             적재했다(같은 PR 에서 파서 수리 — 이후 재수집분은 `none` 으로 적재된다).
--
--   즉 이 문서들은 **애초에 findings 를 낼 대상이 아니다.** 그런데 046 은 이들을 격차로
--   세어 식약처를 12.5% 적색으로 만들었다. **무시되는 알림은 진짜 신호를 가린다** —
--   FDA 483 의 가이던스 문서를 (source, kind) 로 걸러낸 것과 정확히 같은 문제이고,
--   여기서는 kind 가 같아서(gmp-inspection) 종류로 가를 수 없다. 문서 단위로 가려야 한다.
--
-- ★판별은 **소스별 키를 열거하지 않고** 유도한다: payload 안에 이름이 `…assessment` 로
--   끝나면서 값이 정확히 `'none'` 인 필드가 있으면 "원문이 없다고 말했다"로 본다.
--   새 수집기가 같은 관례로 판정을 남기기만 하면 자동으로 감시 모집단에서 빠진다
--   (하드코딩하면 소스가 늘 때마다 목록이 낡는다 — 046 의 producing 집합과 같은 원칙).
--
-- ★분자·분모 **양쪽에서** 뺀다. 지표의 질문은 "findings 를 낼 것으로 기대한 문서 중
--   몇 건이 못 냈나"이므로, 애초에 기대 대상이 아닌 문서는 모집단이 아니다. 분자에서만
--   빼면 비율이 인위적으로 낮아져 진짜 격차가 희석된다.
--
-- 실측(적용 전 dry-run): FDA 483 2000건/118 불변 · 식약처 **91건/7**(원문 무지적 5건 제외)
--   · WL 1299/0 · EU·MHRA NCR 0. 046 의 안전 계약(definer·카운트 전용·원문 무반환) 불변.
-- ============================================================================

create or replace function public.extraction_gap_by_source()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with per_doc as (
    select r.source,
           coalesce(r.source_kind, '') as kind,
           r.raw_signal_id,
           exists (select 1 from public.findings f
                    where f.raw_signal_id = r.raw_signal_id) as has_finding,
           exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                    where length(kv.value) >= 200) as has_stored_text,
           -- ★"원문이 지적사항 없다고 말했다" — 우리의 추출 실패와는 전혀 다른 사건이다.
           exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                    where kv.key like '%assessment' and kv.value = 'none') as source_says_none
      from public.raw_signals r
  ),
  producing as (
    select source, kind from per_doc group by source, kind having bool_or(has_finding)
  ),
  scoped as (        -- 감시 모집단: 산출 대상 종류 ∧ 원문이 없다고 말하지 않은 문서
    select d.*
      from per_doc d
      join producing p on p.source = d.source and p.kind = d.kind
     where not d.source_says_none
  ),
  agg as (
    select s.source,
           count(*)                                      as docs,
           count(*) filter (where s.has_finding)         as with_findings,
           count(*) filter (where not s.has_finding)     as zero_findings,
           count(*) filter (where not s.has_finding
                              and s.has_stored_text)     as zero_with_stored_text,
           count(distinct s.kind)                        as kinds
      from scoped s
     group by s.source
  ),
  skipped as (       -- 정직성: 몇 건을 모집단에서 뺐는지 함께 보고한다(조용한 축소 금지)
    select d.source, count(*) as n
      from per_doc d
      join producing p on p.source = d.source and p.kind = d.kind
     where d.source_says_none
     group by d.source
  )
  select jsonb_build_object(
    'generated_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'totals', jsonb_build_object(
      'sources',        (select count(*) from agg),
      'docs',           (select coalesce(sum(docs), 0) from agg),
      'zero_findings',  (select coalesce(sum(zero_findings), 0) from agg),
      'source_says_none', (select coalesce(sum(n), 0) from skipped)
    ),
    'by_source', coalesce((
      select jsonb_agg(jsonb_build_object(
               'source',          a.source,
               'docs',            a.docs,
               'with_findings',   a.with_findings,
               'zero_findings',   a.zero_findings,
               'zero_with_stored_text', a.zero_with_stored_text,
               -- 모집단에서 제외한 건수. 0 이 아니면 그 소스에 "원문이 무지적이라고
               -- 명시한" 문서가 있다는 뜻이고, 그건 결손이 아니라 정상이다.
               'source_says_none', coalesce(k.n, 0),
               'kinds',           a.kinds,
               'zero_pct',        round(100.0 * a.zero_findings / nullif(a.docs, 0), 1)
             ) order by a.zero_findings desc, a.source)
        from agg a left join skipped k on k.source = a.source), '[]'::jsonb)
  );
$$;

comment on function public.extraction_gap_by_source() is
  '소스별 "적재됐으나 findings 0건" 문서 감시(카운트 전용·원문 무반환). 거르는 단위는 '
  '(source, source_kind), 보고 단위는 source. **원문이 무지적이라고 명시한 문서**'
  '(…assessment=''none'')는 분자·분모 양쪽에서 제외한다 — 추출 실패가 아니라 정상이다.';

revoke all on function public.extraction_gap_by_source() from public;
grant execute on function public.extraction_gap_by_source() to service_role;
