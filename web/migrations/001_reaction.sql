-- GRM 웹 반응 계층(S1) — 하트·스크랩. Supabase(Postgres) SQL 편집기에서 1회 실행.
-- 사용자는 Supabase auth.users 재사용(별도 user 테이블 없음). 백엔드는 불투명 card_id 만 저장 —
-- 카드 사실·제목·원문 URL 은 저장/전송하지 않는다(provenance 보존).

create table if not exists public.reaction (
  user_id    uuid not null references auth.users(id) on delete cascade,
  card_id    text not null,                         -- 불투명 앵커(= card.anchor / document_id)
  kind       text not null check (kind in ('heart','scrap')),
  created_at timestamptz not null default now(),
  primary key (user_id, card_id, kind)
);

alter table public.reaction enable row level security;

-- 본인 행만 읽기/쓰기/삭제 (RLS 로 DB 레벨 강제)
drop policy if exists reaction_select_own on public.reaction;
drop policy if exists reaction_insert_own on public.reaction;
drop policy if exists reaction_delete_own on public.reaction;
create policy reaction_select_own on public.reaction for select using (auth.uid() = user_id);
create policy reaction_insert_own on public.reaction for insert with check (auth.uid() = user_id);
create policy reaction_delete_own on public.reaction for delete using (auth.uid() = user_id);

-- 공개 하트 집계('인기 카드') — 개별 반응 비노출, 카드별 수만. anon 읽기 허용.
create or replace view public.heart_counts
  with (security_invoker = off) as
  select card_id, count(*)::int as hearts
  from public.reaction where kind = 'heart' group by card_id;
grant select on public.heart_counts to anon, authenticated;

-- 조회 성능(카드별 집계·본인 조회)
create index if not exists reaction_card_kind_idx on public.reaction (card_id, kind);
