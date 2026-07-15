-- ※ 2026-07-15: public.findings_similar_to(text, int) 의 함수 바디가
-- 022_findings_similar_truth.sql 에서 create or replace 되어 이 파일의 정의를
-- **supersede** 한다(F-01/F-02 수리 — 절단 후 붕괴를 붕괴 후 절단으로 교체 + 전 절단
-- 결정론 타이브레이크; 같은 문서 제외를 집계 이전에 적용하는 이 파일의 계약은 022 에도
-- 그대로 유지된다). 프로덕션 현행 정의는 022 를 참조하라(007/008/009→010 관례와 동형).
-- 아래 함수 바디는 git 히스토리·원복용 원본으로 남긴다.
--
-- [FIND-1 S1-B] "이 지적과 유사한 사례" — finding_id 로 여는 렉시컬 유사검색 RPC.
--
-- ★왜 임베딩(019)이 아니라 렉시컬인가 — A/B 평가 실측(2026-07-15, 평가셋 40건·arm 블라인드
--   pooled 관련성 판정 1,051쌍·부트스트랩 95% CI):
--     S1(렉시컬) P@5 1.000 / nDCG@10 0.969
--     S2-A(임베딩·finding_text) 0.995 / 0.973  → 두 지표 CI 가 0 포함 = 동률(개선 미입증)
--     S2-B(임베딩·deficiency+detail) 0.880 / 0.773 → 유의하게 열세(nDCG 40건 중 35패)
--   근본 원인: 공개 483 의 59.4% 가 동일 문구다(CFR 조항을 옮긴 정형 문장이라 "같은 위반
--   유형" = "거의 같은 문장"). 기준 지적의 본문을 그대로 질의하면 렉시컬이 이미 정답을
--   찾는다 — S1 상위5 의 97.5% 가 최고 등급. 임베딩은 얻을 것이 없다.
--   → 설계 §4.3 게이트("S1 대비 개선 입증 시에만 S2 공개") 불통과 → S2 웹 공개 중단,
--     이 기능은 임베딩·cron·9.2MB 테이블 없이 S1 만으로 서빙한다.
--   (019 의 findings_similar_by_id 는 embedding_config.active_version=0 으로 inert 유지.
--    자유문장 시맨틱(S3)의 근거는 별개로 살아 있다 — 이번 평가는 아이템-투-아이템만 다뤘다.)
--
-- ★018 을 그대로 클라이언트에서 쓰지 않는 이유(실측 결함):
--   018 findings_similar(text) 는 같은 문서를 제외하지 않고 **중복 붕괴를 먼저** 한다.
--   기준 본문으로 호출하면 기준 자신이 속한 md5 그룹의 대표로 뽑히기 쉽고(대표 선정 =
--   published_date desc), 그 대표를 클라이언트가 같은 문서라고 버리면 **그룹 전체가 사라진다**.
--   평가셋 40건 실측: top-1 이 같은 문서라 버려지는 경우 21건(52.5%), 그중 5건은
--   dup_documents>1 이라 **동일 위반이 있는 다른 문서 최대 12곳이 통째로 소실**됐다 —
--   사용자에게 가장 값진 결과가 12.5% 확률로 증발한다.
--   → 이 RPC 는 **같은 raw_signal_id 를 붕괴 전에 제외**한다(순서가 계약의 핵심).
--
-- 안전 계약(018/019 와 동일 축, 불가침):
--   ①공개 술어 = 010 현행(번역 OR KO) AND scope_status='ok' — 기준 finding·후보 양쪽에
--     적용. 기준이 비공개/미존재/형식오류면 **구분 없이 빈 결과**(존재 여부 누설 금지).
--   ②같은 문서 제외 = raw_signal_id(document_id 아님 — 웹의 문서 정체성 키).
--   ③반환 = 서지 + 본문 + score/중복카운트뿐. evidence_url·raw_json 등 미반환.
--   ④검색 대상 = coalesce(nullif(finding_text_ko,''), finding_text) — ko 단독이면
--     finding_language='KO' 행이 검색에서 누락된다. 018 의 idx_findings_search_fts 와
--     **byte 일치**해야 인덱스를 탄다.
--
-- 전제: 002 + 006/010(공개 게이트) + **018**(pg_trgm 확장·idx_findings_search_fts·
--   FTS OR 변환 관례)이 먼저 적용되어 있어야 한다. 이 파일은 함수 1개만 추가하며 기존
--   테이블·인덱스·RLS·정책·RPC 를 전혀 건드리지 않는다. 멱등(create or replace).

create or replace function public.findings_similar_to(
  p_finding_id text,
  p_limit int default 5
)
returns jsonb
language sql
stable
security definer
set search_path = public, extensions
as $$
  with base as (
    -- 기준 finding. 공개 술어를 여기서 적용 — 비공개면 아래가 전부 빈 결과가 된다.
    select f.finding_id, f.raw_signal_id,
           coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as txt
    from public.findings f
    where f.finding_id = p_finding_id
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
  ),
  input as (
    select b.finding_id, b.raw_signal_id,
           left(btrim(b.txt), 500) as q,
           greatest(1, least(coalesce(p_limit, 5), 50)) as lim
    from base b
  ),
  tsq as (
    -- 018 과 동일한 OR 변환(한국어는 조사 탓에 websearch 기본 AND 로는 0건 — 018 주석의
    -- 라이브 실측 근거 참조). 빈 tsquery 는 null 로 우회한다.
    select case
      when websearch_to_tsquery('simple', i.q)::text = '' then null
      else to_tsquery('simple', replace(websearch_to_tsquery('simple', i.q)::text, ' & ', ' | '))
    end as tq
    from input i
  ),
  candidates as (
    -- ★같은 문서 제외를 **붕괴 전**에 적용한다(위 ★018 주석의 그룹 소실 결함 방어).
    select
      f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date,
      f.firm_name, f.category_code, f.evidence_level, f.review_status,
      coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as search_text,
      similarity(coalesce(nullif(f.finding_text_ko, ''), f.finding_text), i.q) as sim,
      ts_rank(
        to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)),
        t.tq
      ) as fts_rank
    from public.findings f, input i, tsq t
    where char_length(i.q) >= 2
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
      and f.raw_signal_id is distinct from i.raw_signal_id   -- ②같은 문서 제외(붕괴 전)
      and f.finding_id <> i.finding_id
      and t.tq is not null
      and to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) @@ t.tq
    -- 후보 절단은 인덱스가 받치는 ts_rank 로(018 과 동일 — similarity 로 자르면 인덱스
    -- 스캔 결과 전체에 similarity 를 계산해야 해 절단의 이점이 사라진다).
    order by ts_rank(
      to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)), t.tq
    ) desc
    limit 200
  ),
  scored as (
    select *, (0.6 * sim + 0.4 * fts_rank) as score from candidates
  ),
  groups as (
    select md5(search_text) as grp,
      count(distinct raw_signal_id) as dup_documents,
      count(*) as dup_findings,
      max(score) as group_score
    from scored
    group by md5(search_text)
  ),
  collapsed as (
    select r.finding_id, r.raw_signal_id, r.source, r.agency, r.published_date,
      r.firm_name, r.category_code, r.evidence_level, r.review_status, r.search_text,
      g.dup_documents, g.dup_findings, g.group_score
    from (
      -- 대표 선정·최종 정렬 모두 결정론 타이브레이크(published_date desc, finding_id asc).
      select distinct on (md5(search_text)) *, md5(search_text) as grp
      from scored
      order by md5(search_text), published_date desc, finding_id asc
    ) r
    join groups g using (grp)
  )
  select jsonb_build_object(
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'finding_id', finding_id,
          'raw_signal_id', raw_signal_id,
          'source', source,
          'agency', agency,
          'published_date', published_date,
          'firm_name', firm_name,
          'category_code', category_code,
          -- 신뢰도 배지 2종(M13) 유지 — 018 과 동일 계약(둘 다 row 조회로 이미 anon 공개).
          'evidence_level', evidence_level,
          'review_status', review_status,
          'text', search_text,
          'score', round(group_score::numeric, 4),
          'dup_documents', dup_documents,
          'dup_findings', dup_findings
        )
        order by group_score desc, published_date desc, finding_id asc
      )
      from (
        select * from collapsed
        order by group_score desc, published_date desc, finding_id asc
        limit (select lim from input)
      ) top_items
    ), '[]'::jsonb)
  );
$$;

-- 007/018 관례: PUBLIC 전면 회수 후 anon/authenticated 로만 재부여.
revoke all on function public.findings_similar_to(text, int) from public;
grant execute on function public.findings_similar_to(text, int) to anon, authenticated;

-- 검증(라이브 적용 시 실행):
-- ①미존재/형식오류/비공개 → 구분 없이 {"items": []}:
--   select public.findings_similar_to('__nope__', 5);
--   select public.findings_similar_to('finding-000000000000000000000000', 5);
-- ②실제 finding → 같은 문서가 결과에 없어야 한다(raw_signal_id 대조):
--   select public.findings_similar_to('finding-025b03434c3ebafea437d1af', 5);
-- ③그룹 소실 방어 확인 — 018 을 같은 본문으로 호출했을 때 top-1 이 같은 문서라 버려지던
--   기준(평가셋 40건 중 21건)에서, 이 RPC 는 다른 문서의 동일 위반을 정상 반환해야 한다.
-- ④limit 클램프: p_limit=999 → 50 이하, p_limit=-5 → 1.
