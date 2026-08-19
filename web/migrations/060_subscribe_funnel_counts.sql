-- 060: 구독 깔때기 계측(무PII) — 표면 노출/제출/닫힘의 익명 누적 카운터.
-- 목적: "전환 0" 의 원인 분해(노출이 없나 · 제출이 없나)를 주 단위로 판독할 진단 계기.
-- 무PII 계약: key 문자열과 정수 합계만 저장 — 개인 식별자·이메일·세션 없음.
-- 증가는 anon 이 RPC(funnel_bump)로만 가능(테이블 직접 쓰기 불가), 허용 키는 테이블
-- CHECK 가 최종 게이트(배제는 코드가 아닌 DB 제약으로).

create table if not exists public.funnel_counts (
  key text primary key
    check (key in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss')),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now()
);

alter table public.funnel_counts enable row level security;

revoke all on public.funnel_counts from public;
revoke all on public.funnel_counts from anon;
revoke all on public.funnel_counts from authenticated;
grant select on public.funnel_counts to anon, authenticated;

drop policy if exists "public can read funnel counts" on public.funnel_counts;
create policy "public can read funnel counts"
on public.funnel_counts
for select
to anon, authenticated
using (true);

create or replace function public.funnel_bump(p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  -- 모르는 키는 실패(폴백 금지) — 호출부 오타가 조용히 새 슬롯을 만들지 않게 한다.
  if p_key not in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss') then
    raise exception 'funnel_bump: unknown key %', p_key;
  end if;
  insert into public.funnel_counts as fc (key, total, updated_at)
  values (p_key, 1, now())
  on conflict (key) do update
  set total = fc.total + 1, updated_at = now();
end;
$$;

revoke all on function public.funnel_bump(text) from public;
revoke all on function public.funnel_bump(text) from anon;
revoke all on function public.funnel_bump(text) from authenticated;
grant execute on function public.funnel_bump(text) to anon, authenticated;
