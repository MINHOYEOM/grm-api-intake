-- [FIND-1 S2] 임베딩 저장층 + "이 지적과 유사한 사례" RPC — 설계 v1.1.1 §4.3.
--
-- S1(018 유사 문구 검색)이 어휘 매칭이라면, S2 는 의미 매칭이다. 라이브 실측(2026-07-15)이
-- 그 필요를 보여줬다: S1 품질 스팟체크 10종 중 "데이터 무결성 감사추적" 질의가 "용기 마개
-- 무결성"을 잡는 오매치가 나왔다(어휘는 같고 뜻은 다르다). S2 는 이 축을 바꾼다.
--
-- ★비용·운영 계약(불가침): 임베딩은 GitHub Actions CPU 에서 로컬 모델(multilingual-e5-small)
--   로 사전계산한다 — 신규 시크릿 0·과금 0. 쿼리 시점에 모델이 필요 없는 아이템-투-아이템
--   (기준 finding → 유사 finding)만 서빙하므로 이 계약이 성립한다. 자유문장 시맨틱(S3)은
--   보류이며, 켜려면 쿼리 임베딩 공급자가 필요하고 ★공급자를 바꾸면 벡터 공간이 호환되지
--   않아 코퍼스 전량 재임베딩이 동반된다(Codex 정정 — "쿼리 공급자만 붙이면 된다"는 오류).
--
-- ★E5 prefix 계약: 아이템-투-아이템은 대칭 유사도이므로 E5 모델 카드 권고대로 양쪽 모두
--   `query: ` prefix 를 쓴다(비대칭 검색용 `passage: ` 아님). 이 규칙이 깨지면 같은 벡터
--   공간이 아니게 되므로 서비스(findings_embed_service.py)와 이 파일이 함께 고정한다.
--
-- ★인덱스 없음(의도): 8.5k 규모에서는 exact cosine(순차 스캔)으로 먼저 품질을 검증한다.
--   HNSW 는 근사 검색이라 공개 게이트 술어가 후적용되면 결과가 부족해질 수 있다(Codex
--   지적). 지연을 실측한 뒤 필요할 때만 추가한다 — 추가 시 이 주석과 함께 갱신할 것.
--
-- 전제: 002(스키마)+006/010(공개 게이트)이 적용되어 있어야 한다. 이 파일은 확장 1개·
--   테이블 2개·함수 1개만 추가하며 기존 테이블·RLS·정책·RPC 는 건드리지 않는다.
--   전부 멱등(if not exists / or replace).

create extension if not exists vector with schema extensions;

-- ────────────────────────────────────────────────────────────────────────────
-- embedding_config: 활성 임베딩 버전 1행. 원자 전환의 단일 스위치.
-- ────────────────────────────────────────────────────────────────────────────
-- ★복합 PK 가 필요한 이유(Codex 정정): finding_id 단일 PK 면 구·신 벡터를 동시에 담을 수
--   없어 "신버전 전량 적재 → 검증 → 전환" 이 불가능하다(적재하는 순간 구버전이 덮여
--   혼합 공간이 서빙된다). (embedding_version, finding_id) 복합 PK 로 두 버전을 병렬
--   보관하고, 이 설정 테이블의 active_version 1행 UPDATE 로 원자 전환한다.
--   전환 순서: ①신버전 적재(구버전 서빙 유지) ②완결 검증(건수=공개분 전량·차원·NaN)
--   ③active_version UPDATE(원자) ④구버전 지연 삭제.
create table if not exists public.embedding_config (
  id int primary key default 1 check (id = 1),   -- 1행 강제
  active_version int not null default 0,          -- 0 = 활성 버전 없음(서빙 중단 상태)
  updated_at timestamptz not null default now()
);

insert into public.embedding_config (id, active_version)
values (1, 0)
on conflict (id) do nothing;

alter table public.embedding_config enable row level security;
revoke all on public.embedding_config from anon, authenticated;
-- 정책 0개 = 전면 차단. anon 은 아래 RPC(security definer)를 통해서만 간접 소비한다.

-- ────────────────────────────────────────────────────────────────────────────
-- finding_embeddings: 버전별 벡터 저장층.
-- ────────────────────────────────────────────────────────────────────────────
-- embed_input: 'A' = finding_text 단독(현행) / 'B' = deficiency + detail 재조합.
--   ★B 안의 근거(라이브 실측 2026-07-15): FDA 483 Observation 의 90.4% 가 raw_json 에
--   detail 을 갖고 있고 평균 768자다(deficiency 는 147자). findings.finding_text 는
--   deficiency 만 담아(findings_extractors.py) 이 5배 풍부한 사실관계를 통째로 버린다 —
--   공개 483 의 59.4% 가 동일 문구인 근본 원인이다. 어느 쪽이 유사도에 유리한지는
--   평가셋(30~50개)으로 판정한다(설계 §4.3 공개 게이트).
-- text_sha256: 임베딩 입력 텍스트(prefix 제외)의 해시 — 원문 변경·입력안 변경 감지용
--   재임베딩 트리거. finding_id 는 내용 해시라 원문이 바뀌면 id 도 바뀌지만, B 안은
--   raw_json 의 detail 이 재수집으로 바뀔 수 있어 finding_id 만으로는 부족하다.
create table if not exists public.finding_embeddings (
  embedding_version int not null,
  finding_id text not null references public.findings (finding_id) on delete cascade,
  embedding extensions.halfvec(384) not null,
  model text not null,                 -- 'intfloat/multilingual-e5-small@<revision>'
  embed_input text not null check (embed_input in ('A', 'B')),
  text_sha256 text not null check (char_length(text_sha256) = 64),
  embedded_at timestamptz not null default now(),
  primary key (embedding_version, finding_id)
);

-- 서비스가 "이 버전에서 아직 임베딩 안 된 finding" 을 찾을 때 쓰는 조회 인덱스.
create index if not exists idx_finding_embeddings_version
  on public.finding_embeddings (embedding_version);

alter table public.finding_embeddings enable row level security;
revoke all on public.finding_embeddings from anon, authenticated;
-- 정책 0개 = 전면 차단. 노출 표면은 아래 RPC 하나뿐이며 벡터 자체는 반환하지 않는다.

-- ────────────────────────────────────────────────────────────────────────────
-- public.findings_similar_by_id(p_finding_id, p_limit): "이 지적과 유사한 사례".
-- ────────────────────────────────────────────────────────────────────────────
-- 안전 계약(018 과 동일 축):
--   ①공개 술어 = 010 현행(번역 AND scope_status='ok')을 기준 finding·후보 양쪽에 적용.
--     기준 finding 이 비공개면 빈 결과(존재 여부 누설 금지 — 018 uniform not-found 관례).
--   ②같은 문서(raw_signal_id) 제외 — 문서당 평균 6건이라 제외하지 않으면 이웃
--     Observation 이 결과를 도배한다(★document_id 가 아니라 raw_signal_id 가 웹의 문서
--     정체성 키다 — Codex 정정, findings.js groupByDocument 와 동일).
--   ③동일 문구 붕괴(md5) + dup_documents/dup_findings 병기 — 018 과 동형.
--   ④반환 필드는 row 조회로 이미 공개되는 서지 + 본문 + score/중복카운트뿐. 벡터·
--     evidence_url·raw 는 어떤 경로로도 반환하지 않는다.
--   ⑤활성 버전(embedding_config.active_version)과 일치하는 행만 조회 — 혼합 벡터 공간
--     서빙을 구조적으로 차단한다. active_version=0(미설정)이면 빈 결과.
-- 거리: cosine(<=>). score = 1 - distance(= cosine similarity), 0..1 로 정규화해 반환.
create or replace function public.findings_similar_by_id(
  p_finding_id text,
  p_limit int default 10
)
returns jsonb
language sql
stable
security definer
set search_path = public, extensions
as $$
  with cfg as (
    select active_version from public.embedding_config where id = 1
  ),
  base as (
    -- 기준 finding — 공개 술어 통과 + 활성 버전 벡터 보유분만.
    select f.finding_id, f.raw_signal_id, e.embedding
    from public.findings f
    join public.finding_embeddings e
      on e.finding_id = f.finding_id
     and e.embedding_version = (select active_version from cfg)
    where f.finding_id = p_finding_id
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
      and (select active_version from cfg) <> 0
  ),
  neighbors as (
    select
      f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date,
      f.firm_name, f.category_code, f.evidence_level, f.review_status,
      coalesce(nullif(f.finding_text_ko, ''), f.finding_text) as search_text,
      (1 - (e.embedding <=> b.embedding)) as score
    from base b
    join public.finding_embeddings e
      on e.embedding_version = (select active_version from cfg)
    join public.findings f
      on f.finding_id = e.finding_id
    where f.raw_signal_id is distinct from b.raw_signal_id   -- ②같은 문서 제외
      and f.finding_id <> b.finding_id
      and (f.finding_text_ko <> '' or f.finding_language = 'KO')
      and f.scope_status = 'ok'
    order by e.embedding <=> b.embedding
    limit 400   -- 붕괴 전 후보 상한(중복 문구가 상위를 채워도 충분한 대표가 남게)
  ),
  groups as (
    select md5(search_text) as grp,
      count(distinct raw_signal_id) as dup_documents,
      count(*) as dup_findings,
      max(score) as group_score
    from neighbors
    group by md5(search_text)
  ),
  collapsed as (
    select r.finding_id, r.raw_signal_id, r.source, r.agency, r.published_date,
      r.firm_name, r.category_code, r.evidence_level, r.review_status, r.search_text,
      g.dup_documents, g.dup_findings, g.group_score
    from (
      select distinct on (md5(search_text)) *, md5(search_text) as grp
      from neighbors
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
        limit greatest(1, least(coalesce(p_limit, 10), 50))
      ) top_items
    ), '[]'::jsonb)
  );
$$;

-- 007/018 관례: PUBLIC 전면 회수 후 anon/authenticated 로만 재부여.
revoke all on function public.findings_similar_by_id(text, int) from public;
grant execute on function public.findings_similar_by_id(text, int) to anon, authenticated;

-- 검증(라이브 적용 시 실행):
-- ①미설정(active_version=0) 상태에서 빈 결과 유효 jsonb:
--   select public.findings_similar_by_id('finding-000000000000000000000000', 10);
-- ②미존재/비공개 finding_id → 동일한 빈 결과(존재 여부 누설 없음):
--   select public.findings_similar_by_id('__nope__', 10);
-- ③적재·전환 후 같은 문서 제외 확인(반환 raw_signal_id 에 기준 문서가 없어야 한다):
--   select public.findings_similar_by_id('<실제 finding_id>', 10);
