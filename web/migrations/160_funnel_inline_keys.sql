-- 160: 구독 깔때기 — 본문 속 구독 칸(줄형) 키 가산.
-- [2026-11-17 구독 유입 대책] 브리프 요약 아래·용어 정의 아래·지적 문서 끝·영문 홈/지적사항 첫 화면에
-- 줄형 구독 칸(.grm-inl)을 두었다. 기존 밴드(band_*)·배너(cta_*)와 따로 세려고 키 여섯을 더한다.
--   inline_top_view / inline_top_submit — 본문 앞쪽(요약·정의 아래, 첫 화면)
--   inline_mid_view / inline_mid_submit — 본문 중간(홈 '이번 주 핵심' 아래)
--   inline_end_view / inline_end_submit — 본문 끝(지적 문서 끝)
-- 어느 화면의 칸인지는 기존 구역(076 첫 경로 조각)·경로(084) 축이 함께 나눈다 — 자리 키 × 구역 = 칸 하나.
--
-- ★가산만 한다. 기존 키·함수 인자·반환 형태는 그대로다(라이브 페이지의 호출이 깨지지 않는다).
--   허용 키 목록을 넓히는 것뿐이라 060·071·076·084·087 원본 파일은 고치지 않는다(이력 보존 —
--   test_funnel_vocabulary_synced_three_ways 는 원본 5키를 그대로 본다).
-- ★071 funnel_counts_daily 도 넓힌다 — funnel_snapshot() 이 funnel_counts 의 모든 키를 복사하므로
--   여기를 빠뜨리면 새 키가 처음 쌓인 다음 날 스냅샷이 CHECK 위반으로 실패한다.
-- 무PII 계약은 060 과 같다: 키 문자열과 정수 합계만.

-- ── 1. 테이블 CHECK (자동 이름 제약을 찾아 바꾼다) ────────────────────────────
do $$
declare
  t text;
  c record;
begin
  foreach t in array array['funnel_counts','funnel_counts_daily','funnel_zone_counts',
                           'funnel_path_counts','funnel_touch_counts'] loop
    for c in
      select con.conname
        from pg_constraint con
        join pg_class rel on rel.oid = con.conrelid
        join pg_namespace ns on ns.oid = rel.relnamespace
       where ns.nspname = 'public' and rel.relname = t and con.contype = 'c'
         and pg_get_constraintdef(con.oid) like '%band_submit%'
    loop
      execute format('alter table public.%I drop constraint %I', t, c.conname);
    end loop;
  end loop;
end;
$$;

alter table public.funnel_counts add constraint funnel_counts_key_check
  check (key in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss',
                 'inline_top_view','inline_top_submit','inline_mid_view','inline_mid_submit','inline_end_view','inline_end_submit'));
alter table public.funnel_counts_daily add constraint funnel_counts_daily_key_check
  check (key in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss',
                 'inline_top_view','inline_top_submit','inline_mid_view','inline_mid_submit','inline_end_view','inline_end_submit'));
alter table public.funnel_zone_counts add constraint funnel_zone_counts_key_check
  check (key in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss',
                 'inline_top_view','inline_top_submit','inline_mid_view','inline_mid_submit','inline_end_view','inline_end_submit'));
-- 경로·유입 축은 제출 키만(084·087 과 같은 규칙 — 노출은 보내지 않는다).
alter table public.funnel_path_counts add constraint funnel_path_counts_key_check
  check (key in ('band_submit','cta_submit','inline_top_submit','inline_mid_submit','inline_end_submit'));
alter table public.funnel_touch_counts add constraint funnel_touch_counts_key_check
  check (key in ('band_submit','cta_submit','inline_top_submit','inline_mid_submit','inline_end_submit'));

-- ── 2. RPC 허용 키 (본문은 원본과 같고 키 목록만 넓힌다) ─────────────────────
create or replace function public.funnel_bump(p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_key not in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss',
                   'inline_top_view','inline_top_submit','inline_mid_view','inline_mid_submit','inline_end_view','inline_end_submit') then
    raise exception 'funnel_bump: unknown key %', p_key;
  end if;
  insert into public.funnel_counts as fc (key, total, updated_at)
  values (p_key, 1, now())
  on conflict (key) do update
  set total = fc.total + 1, updated_at = now();
end;
$$;

create or replace function public.funnel_zone_bump(p_key text, p_zone text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  z text;
  cap constant integer := 80;
begin
  if p_key not in ('band_view','band_submit','cta_view','cta_submit','cta_dismiss',
                   'inline_top_view','inline_top_submit','inline_mid_view','inline_mid_submit','inline_end_view','inline_end_submit') then
    raise exception 'funnel_zone_bump: unknown key %', p_key;
  end if;
  z := lower(coalesce(p_zone, ''));
  if z !~ '^[a-z0-9-]{1,24}$' then
    z := 'other';
  end if;
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

create or replace function public.funnel_path_bump(p_key text, p_path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v text;
  cap constant integer := 500;
begin
  if p_key not in ('band_submit','cta_submit','inline_top_submit','inline_mid_submit','inline_end_submit') then
    raise exception 'funnel_path_bump: unknown key %', p_key;
  end if;
  v := lower(coalesce(p_path, ''));
  if v !~ '^[a-z0-9-]{1,40}(/[a-z0-9-]{1,40}){0,3}$' then
    v := 'other';
  end if;
  if not exists (select 1 from public.funnel_path_counts f where f.key = p_key and f.path = v)
     and (select count(*) from public.funnel_path_counts) >= cap then
    v := 'other';
  end if;
  insert into public.funnel_path_counts as fp (key, path, total, updated_at)
  values (p_key, v, 1, now())
  on conflict (key, path) do update
  set total = fp.total + 1, updated_at = now();
end;
$$;

create or replace function public.funnel_touch_bump(
  p_key text, p_source text, p_medium text, p_campaign text, p_ref_host text, p_landing_zone text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  s text; m text; c text; r text; z text;
  cap constant integer := 500;
begin
  if p_key not in ('band_submit','cta_submit','inline_top_submit','inline_mid_submit','inline_end_submit') then
    raise exception 'funnel_touch_bump: unknown key %', p_key;
  end if;
  s := lower(coalesce(p_source, ''));       if s !~ '^[a-z0-9._-]{1,40}$' then s := 'other'; end if;
  m := lower(coalesce(p_medium, ''));       if m !~ '^[a-z0-9._-]{1,40}$' then m := 'other'; end if;
  c := lower(coalesce(p_campaign, ''));     if c !~ '^[a-z0-9._-]{1,60}$' then c := 'other'; end if;
  r := lower(coalesce(p_ref_host, ''));     if r !~ '^[a-z0-9.-]{1,80}$'  then r := 'other'; end if;
  z := lower(coalesce(p_landing_zone, '')); if z !~ '^[a-z0-9-]{1,24}$'   then z := 'other'; end if;
  if not exists (select 1 from public.funnel_touch_counts f
                  where f.key = p_key and f.source = s and f.medium = m and f.campaign = c
                    and f.ref_host = r and f.landing_zone = z)
     and (select count(*) from public.funnel_touch_counts) >= cap then
    s := 'other'; m := 'other'; c := 'other'; r := 'other'; z := 'other';
  end if;
  insert into public.funnel_touch_counts as ft
    (key, source, medium, campaign, ref_host, landing_zone, total, updated_at)
  values (p_key, s, m, c, r, z, 1, now())
  on conflict (key, source, medium, campaign, ref_host, landing_zone) do update
  set total = ft.total + 1, updated_at = now();
end;
$$;

-- 권한은 create or replace 가 유지하지만, 원본과 같은 상태를 명시해 둔다.
revoke all on function public.funnel_bump(text) from public, anon, authenticated;
grant execute on function public.funnel_bump(text) to anon, authenticated;
revoke all on function public.funnel_zone_bump(text, text) from public, anon, authenticated;
grant execute on function public.funnel_zone_bump(text, text) to anon, authenticated;
revoke all on function public.funnel_path_bump(text, text) from public, anon, authenticated;
grant execute on function public.funnel_path_bump(text, text) to anon, authenticated;
revoke all on function public.funnel_touch_bump(text, text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.funnel_touch_bump(text, text, text, text, text, text) to anon, authenticated;
