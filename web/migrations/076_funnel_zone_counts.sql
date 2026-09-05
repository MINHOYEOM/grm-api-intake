-- 076: 구독 제출이 **어느 구역에서** 나왔나 — 060 깔때기의 유일한 빈칸.
--
-- 계기(2026-09-04 실측): 그날 구독 2건이 17:07(CTA)·17:09(밴드) KST 에 들어왔다. 시각은
-- 초 단위로 남는데 **어느 페이지에서 눌렀는지는 어디에도 없었다** — 밴드와 CTA 는 전
-- 한국어 페이지에 있고, 060 은 키 문자열 하나만 보내기 때문이다. "유입 경로를 알 수
-- 있나"에 답할 자료가 구조적으로 없었다.
--
-- ★구역은 **경로의 첫 조각**뿐이다(`/glossary/purified-water/` → `glossary`). 손으로
-- 적은 구역 목록을 두지 않는다 — 목록은 라우트가 늘면 조용히 낡는다(2026-09-05 에
-- 가드 5개가 동시에 낡은 전례). 첫 조각은 라우트가 늘어도 저절로 따라간다.
-- 무PII: 쿼리스트링·경로 뒷부분을 통째로 버리므로 `/findings/inspector/?key=실명`
-- 같은 경로가 실려도 남는 값은 `findings` 뿐이다(073 의 clean_path 와 같은 이유).
--
-- ★열린 문자열을 anon RPC 로 받으면 남이 쓰레기 구역을 무한히 만들 수 있다. 형식
-- 제약(소문자·숫자·하이픈 24자)에 더해 **구역 수 상한**을 둔다 — 상한에 닿으면 새 구역을
-- 만들지 않고 'other' 로 접는다. 목록을 관리하지 않으면서 남용은 유한하게 막는 방법이다.
--
-- 읽기는 authenticated 뿐(072/073 과 같은 규칙 — 운영 지표다). funnel_counts(060)가
-- anon 공개인 것과 다른 이유: 그건 사이트가 쓸 수도 있는 숫자였고, 이건 운영 판독용이다.
-- 쓰기는 anon 이 RPC 로만(테이블 직접 쓰기 불가 — 060 관례 동형).

create table if not exists public.funnel_zone_counts (
  key text not null
    check (key in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss')),
  zone text not null check (zone ~ '^[a-z0-9-]{1,24}$'),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (key, zone)
);

alter table public.funnel_zone_counts enable row level security;

revoke all on public.funnel_zone_counts from public, anon, authenticated;
grant select on public.funnel_zone_counts to authenticated;

drop policy if exists "signed-in can read funnel zones" on public.funnel_zone_counts;
create policy "signed-in can read funnel zones"
on public.funnel_zone_counts for select to authenticated using (true);

create or replace function public.funnel_zone_bump(p_key text, p_zone text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  z text;
  -- 구역 수 상한 — 사이트 최상위 라우트가 20 개 남짓이고 키가 5 종이라 넉넉하다.
  cap constant integer := 80;
begin
  -- 모르는 키는 실패(폴백 금지) — 060 funnel_bump 와 같은 규칙.
  if p_key not in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss') then
    raise exception 'funnel_zone_bump: unknown key %', p_key;
  end if;
  z := lower(coalesce(p_zone, ''));
  -- 형식에 안 맞으면 버리지 않고 'other' 로 접는다 — 제출은 일어난 사실이라 세야 한다.
  if z !~ '^[a-z0-9-]{1,24}$' then
    z := 'other';
  end if;
  -- 상한을 넘겨 **새** 구역을 만들려는 호출은 'other' 로 접는다(기존 구역은 계속 증가).
  if not exists (select 1 from public.funnel_zone_counts f where f.key = p_key and f.zone = z)
     and (select count(*) from public.funnel_zone_counts) >= cap then
    z := 'other';
  end if;
  insert into public.funnel_zone_counts as fz (key, zone, total, updated_at)
  values (p_key, z, 1, now())
  on conflict (key, zone) do update
  set total = fz.total + 1, updated_at = now();
end;
$$;

revoke all on function public.funnel_zone_bump(text, text) from public, anon, authenticated;
grant execute on function public.funnel_zone_bump(text, text) to anon, authenticated;
