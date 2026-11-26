-- ============================================================================
-- 169_extraction_gap_rpc_fast.sql — [운영 감시 복구] extraction_gap_by_source() 가
--   statement_timeout 8s 를 넘겨 http_500 을 내던 것을 고친다(판정·응답 키 불변).
--
-- ★무엇이 깨졌나(2026-11-26): Findings Backlog Monitor run 45748412092 가
--   "extraction_gap_by_source RPC failed (http_500)" 로 적색. 11-25 까지는 통과했다.
--   실측(DB 유휴·웜 캐시): 095 정의 10.1s(콜드 15.6s). PostgREST 는 authenticator 의
--   statement_timeout=8s 안에서 돈다 → 57014 → 500. 동시 실행 탓이 아니라 **문서가 늘어
--   선을 넘은 것**이다(raw_signals 9,827행, raw_json text 79MB — 483 본문 복원으로 계속 는다).
--   원인: per_doc 가 행마다 raw_json::jsonb 를 **세 번** 파싱하고, findings 탐침을 행마다 했다.
--
-- ★고친 것: 결과가 참일 수 없는 행은 파싱하지 않는다(per_doc 주석) + findings 는 해시 조인.
--   같은 조건에서 1.6s. 판정식(exists …)은 095 와 글자까지 같다.
--
-- ★재발 방지(같은 PR): findings_backlog_monitor.py 가 RPC 마다 걸린 ms 를 보고서에 싣고,
--   타임아웃의 절반(4s)을 넘으면 `rpc-near-timeout` 로 미리 적색을 띄운다 — 다음에 선을 넘기
--   전에 안다. 500 이면 PostgREST 오류 코드(57014 등)를 함께 적어 시간 초과인지 고장인지 바로 갈린다.
--
-- 적용 전 대조(SQL 에디터, 읽기 전용) — 두 값이 같아야 한다(generated_at 제외):
--   select public.extraction_gap_by_source() - 'generated_at';   -- 적용 전에 떠 둔다
--   (적용 후 같은 쿼리를 다시 돌려 비교)
-- 적용 후 확인: explain analyze select public.extraction_gap_by_source();  -- 4s 미만
-- ============================================================================

create or replace function public.extraction_gap_by_source()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with has_f as (
    -- ★findings 에 지적사항이 하나라도 있는 문서 id 를 한 번만 모은다(행마다 탐침 대신 해시 조인).
    select distinct f.raw_signal_id from public.findings f
  ),
  per_doc as (
    -- ★raw_json 은 text 열이라 `::jsonb` 가 매번 전체 파싱이다(11-26 실측 9,827행·79MB).
    --   095 는 행마다 세 번 파싱해 웜 캐시에서도 10.1s → authenticator statement_timeout 8s 에
    --   잘려 http_500 이 났다. 판정은 그대로 두고, **결과가 참일 수 없는 행은 파싱하지 않는다**:
    --     · has_stored_text 는 지적 0건 문서에서만 쓰인다(agg 의 zero_with_stored_text).
    --       값 길이 200 이상이면 원문 길이도 200 이상(이스케이프는 길이를 늘리기만 한다).
    --     · 'none' / 'out-of-scope' 값은 원문 글자에 그대로 들어 있어야 한다.
    --   걸러진 행만 095 와 같은 exists 로 판정한다 → 결과 동일(적용 전 대조 쿼리는 아래 머리말).
    select r.source,
           coalesce(r.source_kind, '') as kind,
           r.raw_signal_id,
           (h.raw_signal_id is not null) as has_finding,
           case when h.raw_signal_id is null and length(r.raw_json) >= 200
                then exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                              where length(kv.value) >= 200)
                else false end as has_stored_text,
           case when strpos(r.raw_json, 'none') > 0
                then exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                              where kv.key like '%assessment' and kv.value = 'none')
                else false end as source_says_none,
           case when strpos(r.raw_json, 'out-of-scope') > 0
                then exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                              where kv.key like '%\_text\_status' and kv.value = 'out-of-scope')
                else false end as out_of_scope
      from public.raw_signals r
      left join has_f h on h.raw_signal_id = r.raw_signal_id
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

revoke execute on function public.extraction_gap_by_source() from anon, authenticated;
