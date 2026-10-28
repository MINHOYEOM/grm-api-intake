-- ============================================================================
-- 095_extraction_gap_out_of_scope.sql — [운영 감시 정밀화] 회수가 "범위 밖"으로 확정한 문서를
--   추출 격차 모집단에서 제외한다.
--
-- ★왜(2026-10-28 FDA 본문 결손 감사 후속): 483 회수(backfill_483_ocr_recovery)는 다시 읽어 보니
--   도메인 게이트(혈액·IRB·조직·식품·기기 등) 밖인 문서의 표식을 `fda483_text_status =
--   'out-of-scope'` 로 확정한다(#1228 · 시설 유형만으로 범위 밖이면 PDF 없이 표식만, 같은 날 PR).
--   이 문서들은 **findings 를 낼 대상이 아니다** — 048 의 "원문이 무지적이라고 말한 문서"와
--   같은 처지인데 046/048 은 이들을 결손으로 셌다(실측: FDA 483 0건 82건 중 30건이 이미
--   out-of-scope 표식, ~18건이 범위 밖 시설인데 낡은 상한 표식). 진짜 결손이 묻힌다.
--
-- ★판별은 048 과 같은 원칙으로 **유도**한다: 이름이 `…_text_status` 로 끝나고 값이 정확히
--   `'out-of-scope'` 인 필드. 다른 수집기·회수가 같은 관례로 남기면 자동으로 빠진다.
--   분자·분모 양쪽에서 빼고(048 과 같은 이유), 뺀 건수는 `out_of_scope` 로 따로 보고한다.
--
-- 046/048 의 안전 계약(definer·카운트 전용·원문 무반환)·기존 응답 키 불변 — 키 추가뿐이다.
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
           exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                    where kv.key like '%assessment' and kv.value = 'none') as source_says_none,
           exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                    where kv.key like '%\_text\_status' and kv.value = 'out-of-scope') as out_of_scope
      from public.raw_signals r
  ),
  producing as (
    select source, kind from per_doc group by source, kind having bool_or(has_finding)
  ),
  scoped as (
    select d.*
      from per_doc d
      join producing p on p.source = d.source and p.kind = d.kind
     where not d.source_says_none
       and not d.out_of_scope
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
  skipped as (
    select d.source,
           count(*) filter (where d.source_says_none)                       as n,
           count(*) filter (where d.out_of_scope and not d.source_says_none) as oos
      from per_doc d
      join producing p on p.source = d.source and p.kind = d.kind
     where d.source_says_none or d.out_of_scope
     group by d.source
  )
  select jsonb_build_object(
    'generated_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'totals', jsonb_build_object(
      'sources',        (select count(*) from agg),
      'docs',           (select coalesce(sum(docs), 0) from agg),
      'zero_findings',  (select coalesce(sum(zero_findings), 0) from agg),
      'source_says_none', (select coalesce(sum(n), 0) from skipped),
      'out_of_scope',   (select coalesce(sum(oos), 0) from skipped)
    ),
    'by_source', coalesce((
      select jsonb_agg(jsonb_build_object(
               'source',          a.source,
               'docs',            a.docs,
               'with_findings',   a.with_findings,
               'zero_findings',   a.zero_findings,
               'zero_with_stored_text', a.zero_with_stored_text,
               'source_says_none', coalesce(k.n, 0),
               -- 회수가 범위 밖으로 확정해 모집단에서 뺀 건수(결손 아님).
               'out_of_scope',    coalesce(k.oos, 0),
               'kinds',           a.kinds,
               'zero_pct',        round(100.0 * a.zero_findings / nullif(a.docs, 0), 1)
             ) order by a.zero_findings desc, a.source)
        from agg a left join skipped k on k.source = a.source), '[]'::jsonb)
  );
$$;

comment on function public.extraction_gap_by_source() is
  '소스별 "적재됐으나 findings 0건" 문서 감시(카운트 전용·원문 무반환). 거르는 단위는 '
  '(source, source_kind), 보고 단위는 source. **원문이 무지적이라고 명시한 문서**'
  '(…assessment=''none'')와 **회수가 범위 밖으로 확정한 문서**(…_text_status=''out-of-scope'')는 '
  '분자·분모 양쪽에서 제외한다 — 추출 실패가 아니라 정상이다.';

revoke all on function public.extraction_gap_by_source() from public;
grant execute on function public.extraction_gap_by_source() to service_role;
