-- 155 방문 기록 넓히기 — 페이지 간 이동(이전 종류 → 다음 종류)과 탭·칩·펼침 클릭을 하루 단위 익명 카운터로.
-- 153 = 기간 필터(#1635), 154 = DB 크기 기록(#1631) — 그 다음 번호. Additive only — 기존 표·보기·함수 무변경.
--
-- ★왜(2026-11-13, 대표 요청 ⑤⑥): UI 개선(#1599 메뉴 개편·찾아보기·검색 화면, 브리프 카드)의 효과를
--   재야 하는데, 지금 RUM(072~083, Cloudflare)은 **페이지별 방문 수**만 있다. "용어 페이지에서 어디로
--   가는가", "검색 화면을 거쳐 문서로 가는가", "483 카드의 Observation 상세를 몇 번 펼치는가"는 어디에도
--   없다. Cloudflare 비콘은 사용자 이벤트를 못 싣고, 퍼널 카운터(060·076·084·087)는 구독 제출 전용이다.
--
-- ★무엇을(설계: docs/specs/GRM_방문기록_이동·클릭_설계_2026-11-13.md):
--   1) `rum_nav_daily(snap_date, lang, from_kind, to_kind, total)` — 페이지 한 번 열릴 때 1.
--      from_kind 는 같은 사이트 리퍼러의 **페이지 종류**, 바깥·직접 유입은 'entry'. 새로고침은 안 센다.
--   2) `rum_ui_daily(snap_date, lang, page_kind, ui_kind, ui_id, ctx, total)` — 탭·칩·토글·필터
--      클릭과 펼침(details 열기) 1회마다 1. ui_id 는 그 요소의 **CSS 클래스 이름**(우리가 지은 고정
--      어휘), ctx 는 카드 종류 같은 묶음 이름(⑥ 483 = page brief · expand · block-detail · fda-483).
--   3) `rum_nav_bump` · `rum_ui_bump` — anon 이 부르는 정의자 함수(퍼널 카운터와 같은 결).
--   4) `rum_nav_daily_clean` · `rum_ui_daily_clean` — 139 봇 거르기와 **같은 날 제외**
--      (`rum_anomaly_days`)를 건 보기. 분석·보고는 이 보기를 읽는다(139 규율 승계).
--   5) `rum_ui_report(date, date)` — 판독용(authenticated·service_role). _clean 보기만 읽는다.
--
-- ★개인정보 0 — 무엇을 **싣지 않는가**가 설계의 핵심이다(084 의 근거 승계):
--   · 경로·쿼리·검색어·업체명·사람 이름은 싣지 않는다. 종류는 경로 **앞 두 조각을 화이트리스트로
--     접은 이름**(home·brief·term·doc·firm·search …)이라 업체 slug·문서 id·실사관 이름(쿼리)이
--     들어갈 자리가 없다. 클라이언트가 틀려도 DB 가 형식(`^[a-z][a-z0-9-]{0,23}$`)과 하루 행 상한으로
--     막는다 — 넘치면 'other' 로 접는다(버리지 않는다: 일어난 사실이라 센다).
--   · ui_id 는 요소의 클래스 이름이지 화면 글자·값(data-g·필터 값)이 아니다. 필터는 "어느 묶음을
--     눌렀나"(sr-f-agency)만 세고 **무슨 값을 골랐나**(업체·기관 값)는 안 센다.
--   · 개인 식별자·세션 id·시각(분 단위) 없음 — 키는 날짜(KST)와 위 어휘뿐. 한 사람을 이어 볼 수 없다.
--   · 운영자(localStorage 'grm-op')·자동화 브라우저·프리뷰 호스트는 클라이언트에서 보내지 않는다
--     (base.html RUM 게이트와 같은 판정).
--
-- ★남용 방어: 형식 위반 → 'other'. 하루 새 행 상한(nav 600 · ui 800) → 넘치면 튜플 전체 'other'.
--   ui_kind 는 다섯 값만(모르면 예외 — 060/076/087 규칙). total 은 정수 증가만.
--   anon 은 bump 두 함수만 실행할 수 있고 표·보기는 못 읽는다.
--
-- 적용: 비서실(대표 승인 뒤). 배포 순서는 **이 migration 먼저 → 화면(base.html) 배포** — 없는 RPC 를
--   부르는 창을 만들지 않는다(084 와 같음. 반대 순서여도 fetch 실패는 조용히 버려져 화면은 무사).
-- 적용 후 확인:
--   select public.rum_nav_bump('ko', 'entry', 'home');               -- 1행 생김
--   select public.rum_ui_bump('ko', 'brief', 'expand', 'block-detail', 'fda-483');
--   select jsonb_pretty(public.rum_ui_report(current_date - 1, current_date));
--   delete from public.rum_nav_daily where snap_date = (now() at time zone 'Asia/Seoul')::date
--     and from_kind = 'entry' and to_kind = 'home' and total = 1;     -- 시험 행 지우기(필요하면)
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name like 'rum_%_bump' or routine_name = 'rum_ui_report' order by 1, 2;
-- 되돌리기:
--   drop function if exists public.rum_ui_report(date, date);
--   drop function if exists public.rum_ui_bump(text, text, text, text, text);
--   drop function if exists public.rum_nav_bump(text, text, text);
--   drop view if exists public.rum_ui_daily_clean, public.rum_nav_daily_clean;
--   drop table if exists public.rum_ui_daily, public.rum_nav_daily;

-- ── 1. 표 ─────────────────────────────────────────────────────────────────────────────
create table if not exists public.rum_nav_daily (
  snap_date  date    not null,
  lang       text    not null check (lang in ('ko', 'en')),
  from_kind  text    not null check (from_kind ~ '^[a-z][a-z0-9-]{0,23}$'),
  to_kind    text    not null check (to_kind ~ '^[a-z][a-z0-9-]{0,23}$'),
  total      integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (snap_date, lang, from_kind, to_kind)
);
comment on table public.rum_nav_daily is
  '155: 페이지 간 이동(이전 페이지 종류 → 다음 페이지 종류) 하루 익명 카운트. 경로·쿼리·사람 식별자 없음. 쓰기는 rum_nav_bump(anon) 하나.';

create table if not exists public.rum_ui_daily (
  snap_date  date    not null,
  lang       text    not null check (lang in ('ko', 'en')),
  page_kind  text    not null check (page_kind ~ '^[a-z][a-z0-9-]{0,23}$'),
  ui_kind    text    not null check (ui_kind in ('expand', 'tab', 'chip', 'toggle', 'filter')),
  ui_id      text    not null check (ui_id ~ '^[a-z][a-z0-9-]{0,39}$'),
  ctx        text    not null check (ctx = '' or ctx ~ '^[a-z][a-z0-9-]{0,23}$'),
  total      integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (snap_date, lang, page_kind, ui_kind, ui_id, ctx)
);
comment on table public.rum_ui_daily is
  '155: 탭·칩·토글·필터 클릭과 펼침(details 열기) 하루 익명 카운트. ui_id=요소 클래스 이름, ctx=카드 종류 등 묶음. 고른 값·화면 글자 없음. 쓰기는 rum_ui_bump(anon) 하나.';

alter table public.rum_nav_daily enable row level security;
alter table public.rum_ui_daily enable row level security;
revoke all on public.rum_nav_daily, public.rum_ui_daily from public, anon, authenticated;
grant select on public.rum_nav_daily, public.rum_ui_daily to authenticated;
grant all on public.rum_nav_daily, public.rum_ui_daily to service_role;

drop policy if exists "signed-in can read rum nav" on public.rum_nav_daily;
create policy "signed-in can read rum nav" on public.rum_nav_daily for select to authenticated using (true);
drop policy if exists "signed-in can read rum ui" on public.rum_ui_daily;
create policy "signed-in can read rum ui" on public.rum_ui_daily for select to authenticated using (true);

-- ── 2. 쓰기 — anon 이 부르는 정의자 함수 둘 ──────────────────────────────────────────
create or replace function public.rum_nav_bump(p_lang text, p_from text, p_to text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  d   date := (now() at time zone 'Asia/Seoul')::date;
  l   text := case when lower(coalesce(p_lang, '')) = 'en' then 'en' else 'ko' end;
  f   text := lower(coalesce(p_from, ''));
  t   text := lower(coalesce(p_to, ''));
  cap constant integer := 600;   -- 하루 행 상한(종류 ~30 × 30 이면 충분)
begin
  if f !~ '^[a-z][a-z0-9-]{0,23}$' then f := 'other'; end if;
  if t !~ '^[a-z][a-z0-9-]{0,23}$' then t := 'other'; end if;
  if not exists (select 1 from public.rum_nav_daily n
                  where n.snap_date = d and n.lang = l and n.from_kind = f and n.to_kind = t)
     and (select count(*) from public.rum_nav_daily n where n.snap_date = d) >= cap then
    f := 'other'; t := 'other';
  end if;
  insert into public.rum_nav_daily as n (snap_date, lang, from_kind, to_kind, total, updated_at)
  values (d, l, f, t, 1, now())
  on conflict (snap_date, lang, from_kind, to_kind) do update
  set total = n.total + 1, updated_at = now();
end;
$$;
revoke all on function public.rum_nav_bump(text, text, text) from public, anon, authenticated;
grant execute on function public.rum_nav_bump(text, text, text) to anon, authenticated;

create or replace function public.rum_ui_bump(p_lang text, p_page text, p_kind text, p_id text, p_ctx text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  d   date := (now() at time zone 'Asia/Seoul')::date;
  l   text := case when lower(coalesce(p_lang, '')) = 'en' then 'en' else 'ko' end;
  pg  text := lower(coalesce(p_page, ''));
  k   text := lower(coalesce(p_kind, ''));
  i   text := lower(coalesce(p_id, ''));
  c   text := lower(coalesce(p_ctx, ''));
  cap constant integer := 800;   -- 하루 행 상한
begin
  -- 모르는 종류는 실패(폴백 금지) — 어휘가 다섯 개뿐이고, 틀린 값은 클라이언트 결함이다.
  if k not in ('expand', 'tab', 'chip', 'toggle', 'filter') then
    raise exception 'rum_ui_bump: unknown ui kind %', p_kind;
  end if;
  if pg !~ '^[a-z][a-z0-9-]{0,23}$' then pg := 'other'; end if;
  if i  !~ '^[a-z][a-z0-9-]{0,39}$' then i  := 'other'; end if;
  if c <> '' and c !~ '^[a-z][a-z0-9-]{0,23}$' then c := 'other'; end if;
  if not exists (select 1 from public.rum_ui_daily u
                  where u.snap_date = d and u.lang = l and u.page_kind = pg and u.ui_kind = k
                    and u.ui_id = i and u.ctx = c)
     and (select count(*) from public.rum_ui_daily u where u.snap_date = d) >= cap then
    pg := 'other'; i := 'other'; c := 'other';
  end if;
  insert into public.rum_ui_daily as u (snap_date, lang, page_kind, ui_kind, ui_id, ctx, total, updated_at)
  values (d, l, pg, k, i, c, 1, now())
  on conflict (snap_date, lang, page_kind, ui_kind, ui_id, ctx) do update
  set total = u.total + 1, updated_at = now();
end;
$$;
revoke all on function public.rum_ui_bump(text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.rum_ui_bump(text, text, text, text, text) to anon, authenticated;

-- ── 3. 봇 거르기 보기 — 139 와 같은 날 제외 ─────────────────────────────────────────
create or replace view public.rum_nav_daily_clean with (security_invoker = true) as
select r.* from public.rum_nav_daily r
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

create or replace view public.rum_ui_daily_clean with (security_invoker = true) as
select r.* from public.rum_ui_daily r
 where not exists (select 1 from public.rum_anomaly_days a where a.snap_date = r.snap_date);

revoke all on public.rum_nav_daily_clean, public.rum_ui_daily_clean from public, anon, authenticated;
grant select on public.rum_nav_daily_clean, public.rum_ui_daily_clean to authenticated, service_role;

-- ── 4. 판독 — _clean 만 읽는다 ───────────────────────────────────────────────────────
create or replace function public.rum_ui_report(p_from date, p_to date)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'from', p_from, 'to', p_to,
    'nav_total', coalesce((select sum(total) from public.rum_nav_daily_clean
                            where snap_date between p_from and p_to), 0),
    'nav_top', coalesce((
      select jsonb_agg(jsonb_build_object('from', from_kind, 'to', to_kind, 'n', n) order by n desc, from_kind, to_kind)
        from (select from_kind, to_kind, sum(total)::int as n from public.rum_nav_daily_clean
               where snap_date between p_from and p_to group by 1, 2 order by 3 desc limit 60) x), '[]'::jsonb),
    'ui_top', coalesce((
      select jsonb_agg(jsonb_build_object('page', page_kind, 'kind', ui_kind, 'id', ui_id, 'ctx', ctx, 'n', n)
                       order by n desc, page_kind, ui_id)
        from (select page_kind, ui_kind, ui_id, ctx, sum(total)::int as n from public.rum_ui_daily_clean
               where snap_date between p_from and p_to group by 1, 2, 3, 4 order by 5 desc limit 80) x), '[]'::jsonb),
    -- ⑥ 483 카드 "Observation 상세" 펼침 — 날짜별(개선 전후 비교용).
    'expand_483_by_day', coalesce((
      select jsonb_agg(jsonb_build_object('day', snap_date, 'n', n) order by snap_date)
        from (select snap_date, sum(total)::int as n from public.rum_ui_daily_clean
               where snap_date between p_from and p_to and ui_kind = 'expand' and ctx = 'fda-483'
               group by 1) x), '[]'::jsonb),
    'excluded_days', coalesce((select jsonb_agg(snap_date order by snap_date) from public.rum_anomaly_days
                                where snap_date between p_from and p_to), '[]'::jsonb),
    -- 배선 이전은 이 표에 없다(소급 불가) — 0 과 "관측 전"을 가르려고 첫 기록일을 같이 낸다.
    'first_day', (select min(snap_date) from public.rum_nav_daily)
  );
$$;
revoke all on function public.rum_ui_report(date, date) from public, anon, authenticated;
grant execute on function public.rum_ui_report(date, date) to authenticated, service_role;
