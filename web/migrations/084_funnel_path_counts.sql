-- 084: 구독 제출이 **어느 페이지에서** 나왔나 — 076 구역 카운터의 다음 한 칸.
--
-- 계기(2026-09-22 실측): 그날 구독 2건이 13:44·17:01 KST 에 들어왔고, 076 덕분에
-- 구역이 `findings`·`glossary` 라는 것까지는 바로 나왔다. 그런데 "어느 지적사항이고
-- 어느 용어냐"에 답할 자료가 **어디에도 없었다** — 076 은 경로의 첫 조각만 남기고,
-- Brevo 연락처에 붙는 속성도 기본 항목(EMAIL/FIRSTNAME/LASTNAME/SMS/EXT_ID/
-- LANDLINE_NUMBER)뿐이라 전부 비어 있었다. 용어사전은 방문·검색노출 모두 1위 구역이라
-- "어떤 용어가 구독으로 이어지나"는 다음 글감을 고르는 데 직접 쓰이는 답이다.
--
-- ★무PII — 076 이 지키던 성질을 그대로 유지한다. 실사관·업체 프로파일의 실명은
-- **쿼리스트링에만** 있다: `findings/inspector/` 는 셸 페이지 하나만 렌더하고 실제
-- 조회는 `?key=<실명>` 으로 inspector.js 가 한다(render.py — sitemap 미등록·noindex,
-- "실명이 적시된 개인 집계라 베이스 경로조차 넣지 않는다"). 즉 **경로에는 사람 이름이
-- 들어오지 않는다.** 그래서 쿼리를 통째로 버리고 경로만 실으면 076 의 무PII 가 그대로
-- 성립한다. 클라이언트도 location.pathname 만 읽는다(location.search/href 미참조를
-- 렌더 테스트가 고정한다 — 076 과 같은 방식).
--
-- ★076 을 고치지 않고 새 표로 **가산**한다. funnel_zone_counts 의 열을 늘리거나
-- funnel_zone_bump 의 인자를 바꾸면 이미 라이브에 나가 있는 페이지의 호출이 깨진다
-- (배포 창이 생긴다). 구역 카운터는 그대로 두고 호출을 하나 더 얹는다.
--
-- ★키는 제출 둘뿐이다. 076 이 노출(view)을 빼둔 이유가 여기서는 더 강하다 — 경로는
-- 차원이 4천 쪽이라 노출까지 실으면 표가 쓰레기로 덮인다.
--
-- ★열린 문자열을 anon RPC 로 받으므로 076 과 같은 3중 제약을 건다: 형식(소문자·숫자·
-- 하이픈·슬래시), 길이(80자), **경로 수 상한**. 상한에 닿으면 새 경로를 만들지 않고
-- 'other' 로 접는다. 제출이 주 1~5건이라 상한 500 이면 몇 해를 써도 닿지 않고,
-- 남용은 500 행에서 멈춘다.
--
-- 읽기는 authenticated 뿐, 쓰기는 anon 이 RPC 로만 — 076/072/073 과 같은 규칙.

create table if not exists public.funnel_path_counts (
  key text not null
    check (key in ('band_submit','cta_submit')),
  -- 슬래시로 이은 경로 조각. 앞뒤 슬래시 없음(`glossary/bioburden`), 홈은 'home'.
  path text not null check (path ~ '^[a-z0-9-]{1,40}(/[a-z0-9-]{1,40}){0,3}$'),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (key, path)
);

alter table public.funnel_path_counts enable row level security;

revoke all on public.funnel_path_counts from public, anon, authenticated;
grant select on public.funnel_path_counts to authenticated;

drop policy if exists "signed-in can read funnel paths" on public.funnel_path_counts;
create policy "signed-in can read funnel paths"
on public.funnel_path_counts for select to authenticated using (true);

create or replace function public.funnel_path_bump(p_key text, p_path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v text;
  -- 경로 수 상한 — 제출이 주 1~5 건이라 넉넉하다(076 의 80 은 구역용이라 여기선 좁다).
  cap constant integer := 500;
begin
  -- 모르는 키는 실패(폴백 금지) — 060/076 과 같은 규칙. 노출 키는 여기 오면 안 된다.
  if p_key not in ('band_submit','cta_submit') then
    raise exception 'funnel_path_bump: unknown key %', p_key;
  end if;
  v := lower(coalesce(p_path, ''));
  -- 형식에 안 맞으면 버리지 않고 'other' 로 접는다 — 제출은 일어난 사실이라 세야 한다.
  if v !~ '^[a-z0-9-]{1,40}(/[a-z0-9-]{1,40}){0,3}$' then
    v := 'other';
  end if;
  -- 상한을 넘겨 **새** 경로를 만들려는 호출은 'other' 로 접는다(기존 경로는 계속 증가).
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

revoke all on function public.funnel_path_bump(text, text) from public, anon, authenticated;
grant execute on function public.funnel_path_bump(text, text) to anon, authenticated;

-- 판독용 — 077 growth_daily_report 를 통째로 다시 쓰지 않고 별도 함수로 둔다(078 이
-- gsc_report 를 가른 것과 같은 이유: 200줄 복제를 만들지 않는다). 성장 일보는 이 함수를
-- 한 번 더 호출해 "구독이 나온 페이지"를 붙인다.
--
-- ★표가 누적만 있고 일자 스냅샷이 없다 — updated_at 이 **그 경로의 마지막 제출 시각**
-- 이라 "어제 어디서 들어왔나"는 그 시각으로 읽는다. 제출이 주 1~5 건이라 이걸로 충분하고,
-- 일자 스냅샷을 또 두면 060/071 과 같은 배관을 하나 더 유지해야 한다.
create or replace function public.funnel_paths_report()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'paths', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'key', key,
                'path', path,
                'total', total,
                'last_kst', to_char(updated_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI'))
              order by updated_at desc)
         from public.funnel_path_counts), '[]'::jsonb),
    'distinct_paths', (select count(*) from public.funnel_path_counts),
    'total_submits', coalesce((select sum(total) from public.funnel_path_counts), 0),
    -- 배선 이전 제출은 이 표에 없다(소급 불가) — 첫 기록 시각을 같이 내서 "0 건"이
    -- 유입 없음이 아니라 관측 시작 전이라는 것을 읽는 쪽이 구분할 수 있게 한다.
    'first_kst', (select to_char(min(updated_at) at time zone 'Asia/Seoul', 'YYYY-MM-DD')
                    from public.funnel_path_counts)
  );
$$;

revoke all on function public.funnel_paths_report() from public, anon, authenticated;
grant execute on function public.funnel_paths_report() to authenticated;
