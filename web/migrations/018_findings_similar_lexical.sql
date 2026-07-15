-- [FIND-1 S1] 유사 문구 검색(렉시컬) RPC — 설계: GRM_규제인텔리전스_업그레이드_설계 v1.1.1 §4.2.
-- "의미검색"이 아니라 pg_trgm(trigram)+FTS 하이브리드의 "유사 문구 검색"이다(웹 UI 명칭 동일).
--
-- 검색 대상 텍스트(불가침 계약): coalesce(nullif(finding_text_ko, ''), finding_text)
--   — finding_language='KO' 행(MFDS 원문 등)은 finding_text_ko 가 비어도 공개 게이트를
--   통과하므로 ko 단독이면 검색에서 누락된다. 이하 "search_text". 아래 인덱스 2개의
--   표현식과 RPC 본문 표현식은 반드시 byte 일치해야 한다(불일치 = 인덱스 미사용).
--
-- 공개 술어(불가침, 010 현행 정책과 동일): (finding_text_ko <> '' or finding_language='KO')
--   and scope_status='ok'. 이 RPC 는 007/008 과 달리 본문 텍스트를 반환하므로 게이트
--   술어를 함수 안에 명시 복제한다 — 후보·반환 전부에 적용(비공개 행 노출 차단선).
--
-- 반환 계약: row 조회로 이미 공개되는 서지 필드 + search_text + score/중복 카운트뿐.
--   evidence_url/raw_json/벡터 등은 어떤 경로로도 반환하지 않는다(007 안전 계약 확장).
--
-- 중복 붕괴(설계 §4.2): 공개 FDA 483 의 59.4%가 동일 지적문 그룹(최다 337회, 2026-07-15
--   실측) — 동일 search_text 는 대표 1행으로 접고 dup_documents(문서 수)·dup_findings
--   (행 수)를 병기해 상투문구가 결과를 도배하는 것을 서버에서 차단한다.
--
-- ★★후보 팔 = FTS 단독(라이브 실측으로 설계 정정, 2026-07-15). 설계 v1.1.1 은 "trgm(%)
--   OR FTS(@@)" 2중 후보였으나 프로덕션 적용 중 실측한 결과 trgm 후보 팔을 폐기했다:
--   ⓐ이 코퍼스의 기하학 — 질의(문장)는 길고 지적문은 짧아(EN 평균 145자) 길이 불균형
--     탓에 전 코퍼스 최대 similarity 가 0.1111 에 불과. 기본 임계값 0.3 에서 후보 0건.
--   ⓑ임계값을 0.1 로 낮춰도 후보 2건 — 있으나 마나(word_similarity 도 최대 0.3333 대
--     기본 0.6 이라 동일 결론).
--   ⓒSupabase 에서 `alter function ... set pg_trgm.similarity_threshold` 은 권한 거부.
--     역할·DB 전역 설정(alter role/database)은 모든 세션에 영향을 주므로 RPC 하나를
--     위해 쓰지 않는다(승인 범위 밖·나쁜 설계).
--   → trgm GIN 인덱스도 만들지 않는다(쓰이지 않는데 매일 백필 insert 마다 쓰기 증폭만
--     유발). pg_trgm 확장 자체는 유지 — similarity() 를 재랭킹 점수로 쓴다(GUC 불요).
--   실측 근거: FTS 단독 후보 = Bitmap Index Scan on idx_findings_search_fts, 42ms
--   (전량 seq scan 244ms 대비 6배). EXPLAIN 첨부는 PR 본문.
--
-- ★알려진 한계(정직 고지): FTS 'simple' 은 공백 토큰 완전일치라, 조사가 붙어 어떤 토큰도
--   정확히 일치하지 않는 질의("무균실에서" vs 본문 "무균")는 0건 → 웹은 기존 키워드
--   검색으로 조용히 폴백한다. 형태 변형·의미 매칭은 S2(임베딩)의 몫이며 S1 은 그 전
--   단계의 무료 MVP 다("유사 문구 검색"이라는 정직한 명칭이 이 한계를 그대로 반영).
--
-- search_path 주의: pg_trgm 은 Supabase 관례대로 extensions 스키마에 설치한다. 007 의
--   `set search_path = public` 관례를 이 함수만 `public, extensions` 로 확장한다 —
--   similarity() 해석에 필요하며, 고정 목록이라 mutable search_path 취약점과 무관하다
--   (001/007 과 동일한 고정 원칙).
--
-- 전제: 002(스키마)+006(공개 게이트)+010(scope_status 게이트)이 적용되어 있어야 한다.
--   이 파일은 확장 1개·인덱스 1개·함수 1개만 추가하며 기존 테이블·RLS·정책은 건드리지
--   않는다. 전부 멱등(if not exists / or replace).

create extension if not exists pg_trgm with schema extensions;

-- 후보 검색용 expression GIN 인덱스(공개행 ~8.5k). FTS(simple 사전 — 언어 불문 공백
-- 토큰화)를 websearch_to_tsquery 후보 검색이 사용한다. 표현식은 RPC 본문과 byte 일치
-- 해야 한다(불일치 = 인덱스 미사용 — Bitmap Index Scan 확인은 PR 본문 EXPLAIN 첨부).
create index if not exists idx_findings_search_fts
  on public.findings using gin
  (to_tsvector('simple', coalesce(nullif(finding_text_ko, ''), finding_text)));

-- public.findings_similar(p_query, p_limit): 후보 검색(인덱스 가용 술어) → 재랭킹 2단.
--   ts_rank/similarity 단일 ORDER BY 는 인덱스를 못 타므로(설계 §4.2 Codex 정정), 후보를
--   `@@`(FTS 인덱스)로 좁힌 뒤 상위 200개만 재랭킹한다. 재랭킹 점수 = 0.6*similarity +
--   0.4*ts_rank — similarity() 는 함수 호출이라 GUC(임계값)가 필요 없고, 200행 대상이라
--   비용도 무시할 수준이다(위 ★★ 주석: % 연산자 후보 팔은 실측으로 폐기).
--   입력 가드: 2자 미만은 빈 결과, 500자 초과는 앞 500자로 절단, p_limit 은 1..50
--   클램프(에러가 아니라 클램프 — 007 "미존재 업체도 유효 jsonb" 관례).
create or replace function public.findings_similar(
  p_query text,
  p_limit int default 20
)
returns jsonb
language sql
stable
security definer
set search_path = public, extensions
as $$
  with input as (
    select
      left(btrim(coalesce(p_query, '')), 500) as q,
      greatest(1, least(coalesce(p_limit, 20), 50)) as lim
  ),
  tsq as (
    -- FTS 질의는 OR 시맨틱으로 변환한다 — ★라이브 실측(2026-07-15, dry-run): 한국어는
    -- 조사가 붙어 공백 토큰 완전일치가 거의 안 되므로 websearch 기본 AND 는 표본 질의
    -- 3종 전부 0건, OR 변환 시 26~1,048건. 변환은 websearch_to_tsquery 출력 텍스트의
    -- 최상위 ' & ' 만 ' | ' 로 치환(따옴표 구문의 <-> 구절은 불변). 커버리지(더 많은
    -- 단어 일치)는 ts_rank 가 점수로 반영한다. 빈 tsquery 가드: to_tsquery('') 는
    -- 에러이므로 null 로 우회(@@/ts_rank 는 아래에서 null-안전 처리).
    select case
      when websearch_to_tsquery('simple', i.q)::text = '' then null
      else to_tsquery('simple', replace(websearch_to_tsquery('simple', i.q)::text, ' & ', ' | '))
    end as tq
    from input i
  ),
  candidates as (
    -- 후보: FTS(@@) 단독 — 위 expression GIN 인덱스가 받친다(Bitmap Index Scan 실측).
    -- 공개 술어(010)를 후보 단계에서 즉시 적용해 비공개 행이 랭킹에도 못 들어오게 한다.
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
      and t.tq is not null
      and to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)) @@ t.tq
    -- 후보 절단 기준은 인덱스가 받치는 ts_rank 로 한다(similarity 로 order by 하면
    -- 인덱스 스캔 결과 전체에 similarity 를 계산해야 해 후보 절단의 이점이 사라진다).
    order by ts_rank(
      to_tsvector('simple', coalesce(nullif(f.finding_text_ko, ''), f.finding_text)), t.tq
    ) desc
    limit 200
  ),
  scored as (
    select *, (0.6 * sim + 0.4 * fts_rank) as score
    from candidates
  ),
  groups as (
    -- 동일 문구 그룹 집계(count(distinct) 는 윈도우 함수 미지원이라 group by 로 계산).
    select md5(search_text) as grp,
      count(distinct raw_signal_id) as dup_documents,
      count(*) as dup_findings,
      max(score) as group_score
    from scored
    group by md5(search_text)
  ),
  collapsed as (
    -- 동일 문구 붕괴: 대표 = 최신 발행일 → finding_id 오름차순(결정론 타이브레이크).
    select r.finding_id, r.raw_signal_id, r.source, r.agency, r.published_date,
      r.firm_name, r.category_code, r.evidence_level, r.review_status, r.search_text,
      g.dup_documents, g.dup_findings, g.group_score
    from (
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
          -- 신뢰도 배지 2종(M13) 유지에 필요 — 둘 다 row 조회(FIELDS)로 이미 anon 에
          -- 공개되는 서지 메타이고, 007 안전 계약도 evidence_level 을 명시 허용한다.
          -- 이게 없으면 유사검색 결과에서 "검토 필요" 경계 표시가 사라진다.
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

-- 007 관례: PUBLIC 전면 회수 후 anon/authenticated 로만 재부여.
revoke all on function public.findings_similar(text, int) from public;
grant execute on function public.findings_similar(text, int) to anon, authenticated;

-- 검증(라이브 적용 시 실행):
-- ①빈/짧은 질의 → {"items": []} 유효 jsonb:
--   select public.findings_similar('', 20);  select public.findings_similar('a', 20);
-- ②표본 질의(품질 스팟체크 10개는 PR 본문 첨부):
--   select public.findings_similar('무균 작업 배지모사시험 최악조건 미반영', 10);
-- ③EXPLAIN ANALYZE(후보 단계 인덱스 사용 확인 — Bitmap OR 기대, PR 본문 첨부):
--   explain analyze select public.findings_similar('환경모니터링 경보 조치 미흡', 10);
