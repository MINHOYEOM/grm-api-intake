-- ============================================================================
-- 045_extraction_gap_monitor.sql — [운영 감시] 적재됐으나 지적사항 0건인 문서 감시
--
-- ★왜(2026-08-01 RCA): FDA 483 문서 124건이 지적사항 0건이었고, 그 원인을 **세 번 연속
--   OCR 로 오진**했다(엔진 배선 수리 → 렌더 DPI 300 → DPI 400). 실측해 보니 124건 중
--   본문 확보 실패는 2건(fda.gov 404)뿐이고 나머지 122건은 본문이 멀쩡한데 추출층이
--   못 뽑은 것이었다. 즉 원인은 수집이 아니라 파서였다.
--
--   왜 그렇게 오래 못 봤는가가 이 파일의 존재 이유다. **"문서는 들어왔는데 지적사항이
--   하나도 안 나온다"를 상시로 말해 주는 장치가 없었다.** 이 값은 사람이 손으로 물어볼
--   때만 드러났고, 그래서 444건까지 조용히 쌓인 뒤에야 발견됐다. 실제로 이 감시를 처음
--   돌려 보자마자 아무도 보지 않던 두 번째 사각지대가 같이 나왔다 — 식약처 문서 113건 중
--   29건(25.7%)이 지적사항 0건이다.
--
--   교훈: 추출 실패는 **에러를 내지 않는다.** 정상 종료하고 빈손을 남긴다. 그래서 실패
--   카운터로는 영원히 안 잡히고, 오직 "산출물이 0인 입력"을 세야만 보인다.
--
-- ★안전 계약(007/010/041/042 와 동종·불가침): 이 함수는 어떤 경로로도 원문 텍스트를
--   반환하지 않는다. 반환 표면은 소스명과 **카운트**뿐이다(raw_json 을 읽지만 길이만
--   센다). jsonb_build_object 키 목록이 그 계약의 유일한 표면이다.
--
-- security definer + `set search_path = public`(007 관례) — raw_signals 는 anon 에게
--   열려 있지 않으므로 definer 여야 운영 모니터(service-role 아닌 경로 포함)가 읽는다.
--   원문 무반환 계약을 지키므로 definer 로 안전하다.
--
-- ★"지적사항을 만드는 대상"은 하드코딩하지 않고 **데이터에서 유도**한다 — findings 를
--   한 건이라도 낳은 적이 있는 (source, source_kind) 쌍만 대상이다. 뉴스형 소스
--   (Federal Register·ECA·ISPE·EMA·Health Canada·PIC/S·OpenFDA Recall)는 애초에
--   findings 를 만들지 않으므로 0건이 정상이고, 하드코딩하면 새 소스가 늘 때 목록이 낡는다.
--
-- ★★거르는 단위는 (source, kind)인데 **보고 단위는 source** 다. 이 비대칭이 핵심이다:
--   · 소스 단위로만 거르면 — 식약처가 영구 적색이 된다. 한 소스 안에 성격이 다른 문서가
--     섞여 있기 때문이다(gmp-inspection 34건은 지적사항을 내지만, "신약 품목허가·심사
--     업무절차" 같은 guidance-industry/internal 17건은 0건이 당연하다). 무시되는 알림은
--     진짜 신호를 가리므로 이건 감시의 실패다.
--   · kind 단위로 보고하면 — Warning Letter 가 감시에서 통째로 사라진다. WL 의
--     source_kind 는 문서 종류가 아니라 **발행 부서명**이라 자유 문자열 49종으로 쪼개지고,
--     각 그룹이 소량이라 잡음 억제 임계에 전부 걸려 버린다.
--   즉 source_kind 의 의미가 소스마다 다르다는 사실 자체를 설계에 반영해야 한다.
--   실측 검증: 483 2000건/124(6.2%) 불변 · 식약처 96건/12(12.5%, 가이던스 17 제외) ·
--   WL 1299건/0(49 kind 전부 유지) · EU·MHRA NCR 0.
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
           -- ★이름을 정확히 쓸 것: 이건 "원문에 본문이 있었는가"가 **아니라** "우리가
           --   저장한 payload 안에 산문으로 보이는 문자열이 있는가"다. DB 는 전자를 알 수
           --   없다 — 수집기가 전문을 저장하지 않고 발췌만 남기기 때문이다. 실제로 이번
           --   124건 중 저장된 산문이 있는 건 56건이지만, PDF 를 다시 받아 보면 95건에
           --   본문이 있었다. 과잉 주장하는 지표를 만드는 것이 이 사고의 원인이었으므로
           --   여기서는 알 수 있는 것만 이름에 담는다.
           exists (select 1 from jsonb_each_text(r.raw_json::jsonb) kv
                    where length(kv.value) >= 200) as has_stored_text
      from public.raw_signals r
  ),
  producing as (        -- findings 를 낳은 적이 있는 (소스, 종류) 쌍만 감시 대상
    select source, kind from per_doc group by source, kind having bool_or(has_finding)
  ),
  agg as (
    select d.source,
           count(*)                                      as docs,
           count(*) filter (where d.has_finding)         as with_findings,
           count(*) filter (where not d.has_finding)     as zero_findings,
           count(*) filter (where not d.has_finding
                              and d.has_stored_text)     as zero_with_stored_text,
           count(distinct d.kind)                        as kinds
      from per_doc d
      join producing p on p.source = d.source and p.kind = d.kind
     group by d.source
  )
  select jsonb_build_object(
    'generated_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'totals', jsonb_build_object(
      'sources',        (select count(*) from agg),
      'docs',           (select coalesce(sum(docs), 0) from agg),
      'zero_findings',  (select coalesce(sum(zero_findings), 0) from agg)
    ),
    'by_source', coalesce((
      select jsonb_agg(jsonb_build_object(
               'source',          a.source,
               'docs',            a.docs,
               'with_findings',   a.with_findings,
               'zero_findings',   a.zero_findings,
               -- 저장된 산문을 갖고도 0건 = **파서 신호가 확실한 하한**. 0 이라고 해서
               -- 수집 문제라는 뜻은 아니다(저장 자체를 안 했을 수 있다) — 이 값은
               -- "최소한 이만큼은 파서 문제"만 말한다. 위쪽 주석 참조.
               'zero_with_stored_text', a.zero_with_stored_text,
               -- 이 소스에서 감시 대상으로 남은 문서 종류 수(가이던스 등 비산출 종류 제외 후).
               'kinds',           a.kinds,
               'zero_pct',        round(100.0 * a.zero_findings / nullif(a.docs, 0), 1)
             ) order by a.zero_findings desc, a.source)
        from agg a), '[]'::jsonb)
  );
$$;

comment on function public.extraction_gap_by_source() is
  '소스별 "적재됐으나 findings 0건" 문서 감시(카운트 전용·원문 무반환). '
  '추출 실패는 에러를 내지 않으므로 실패 카운터로는 안 잡힌다 — 산출물이 0인 입력을 센다.';

revoke all on function public.extraction_gap_by_source() from public;
grant execute on function public.extraction_gap_by_source() to service_role;
