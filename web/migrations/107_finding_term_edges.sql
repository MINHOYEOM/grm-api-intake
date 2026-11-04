-- 지식그래프 문제 6 — 지적↔용어 엣지 사전계산 테이블 finding_term. 가산만(테이블 1, 행 쓰기 없음).
--
-- 지금은 "이 지적에 걸린 용어" 를 검색 시점에 전문검색으로 계산한다(건수만 저장). 이 테이블은 같은 판정을
-- 미리 저장해 RAG·실사 대비가 용어를 근거로 쓸 수 있게 한다. 용어 정본은 web/data/glossary.json 이라
-- term_id 는 그 id(예: 'gmp', 'sop')이고 FK 는 걸지 않는다.
--
-- 매칭 규칙(kg_edges.py build_term_edges — 카드/문서 용어 링크와 같은 엔진, 부분일치 금지):
--   · 표면형 정확 일치 + 한글 낱말 시작 규칙(앞 글자가 한글이면 건너뜀) + 동사형 배제(…하/되/한/된)
--   · confidence: 1.0 라틴 표제어 3자 이상 또는 한글 4음절 이상 / 0.85 한글 3음절 / 그 아래(품질·제조 같은
--     일반어, 라틴 2자 이하)는 **연결하지 않는다**(저장 안 함). 소비자는 >= 0.8 만 저장돼 있다고 보면 된다.
-- 채우기: 이 파일이 아니라 `python kg_edges.py terms --emit-sql out.sql` 이 만든 INSERT 배치(멱등)로 한다.
-- 갱신: 용어/지적이 늘면 같은 스크립트를 다시 돌려 on conflict do nothing 으로 증분 반영, 용어 표면형이
-- 바뀌면 해당 term_id 행을 지우고 다시 채운다.

create table if not exists public.finding_term (
  finding_id text not null references public.findings (finding_id) on delete cascade,
  term_id    text not null,
  surface    text not null,
  confidence numeric(3,2) not null check (confidence >= 0.8 and confidence <= 1),
  primary key (finding_id, term_id)
);
create index if not exists idx_finding_term_term on public.finding_term (term_id);
alter table public.finding_term enable row level security;
revoke all on table public.finding_term from public, anon, authenticated;
