-- 103: 브리프 본문 독자 행동 카운터 — 링크 종류별 클릭 · 스크롤 깊이 · 카드 유형별 펼침 · 기기 구분.
--
-- 계기(2026-11-03 성장분석팀 「브리프 독자는 무엇을 보고 누르나」): Cloudflare 비콘은 페이지뷰만
-- 세므로 브리프 안에서 무엇을 눌렀는지·어디까지 읽었는지·어느 유형 카드를 펼쳤는지가 측정 불가였다.
-- 087(funnel_touch_bump)과 같은 방식 — anon 이 RPC 로만 쓰는 익명 정수 카운터. 새 서비스·비용 0.
--
-- ★무PII — 저장하는 것은 (KST 일자, 이벤트 키, 닫힌 어휘 하위값, 카드 유형, 기기 2값) 조합의 정수
--   합계뿐이다. IP·세션/브라우저 식별자·이메일·URL·경로·쿼리·링크 목적지는 보내지도 저장하지도
--   않는다(클라이언트가 링크를 '종류'로만 분류해 보낸다). 방문자 단위 행이 없어 "한 사람이 몇 번"은
--   원리상 알 수 없다 — 의도한 한계다.
-- ★가산만 — 기존 표·RPC(060/076/084/087 funnel_*, rum_*)는 건드리지 않는다. 반환 shape 변경 없음.
-- ★열린 문자열은 card_type 하나뿐이고(카드 유형은 데이터가 정한다) 3중 제약을 건다 — 형식 정규식 ·
--   길이 · 행 수 상한 3000. 상한에 닿으면 card_type 만 'other' 로 접는다(카운트는 잃지 않는다).
-- ★키·하위값은 닫힌 어휘 — 모르는 키는 예외(폴백 금지), 모르는 하위값은 'other' 로 접는다.
--     link_click : src(원문) firm(업체 이력) term(용어) doc(지적사항 문서) toc(목차) other
--     depth      : 25 50 75 100  (페이지 방문당 클라이언트가 1회씩만 보낸다)
--     card_open  : deep(상세 분석) merged(묶음 전체) detail(지적/회수 상세) other
--   '유사 사례' 링크는 현재 브리프 본문에 없다(지적사항 검색 화면 안 기능). 생기면 어휘에 가산한다.
-- 읽기는 authenticated 뿐, 쓰기는 anon 이 RPC 로만(087 과 같은 규칙).
-- ★적용은 사람이 한다 — 이 파일은 PR 에만 있다.

create table if not exists public.brief_event_counts (
  day date not null,
  key text not null check (key in ('link_click','depth','card_open')),
  sub text not null check (sub ~ '^[a-z0-9]{1,12}$'),
  card_type text not null check (card_type ~ '^[A-Za-z0-9가-힣 ._/&()+-]{1,30}$'),
  device text not null check (device in ('mobile','pc')),
  total integer not null default 0 check (total >= 0),
  updated_at timestamptz not null default now(),
  primary key (day, key, sub, card_type, device),
  check (
    (key = 'link_click' and sub in ('src','firm','term','doc','toc','other')) or
    (key = 'depth'      and sub in ('25','50','75','100')) or
    (key = 'card_open'  and sub in ('deep','merged','detail','other'))
  )
);

alter table public.brief_event_counts enable row level security;

revoke all on public.brief_event_counts from public, anon, authenticated;
grant select on public.brief_event_counts to authenticated;

drop policy if exists "signed-in can read brief events" on public.brief_event_counts;
create policy "signed-in can read brief events"
on public.brief_event_counts for select to authenticated using (true);

create or replace function public.brief_event_bump(
  p_key text, p_sub text, p_card_type text, p_device text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  s text; c text; d text;
  cap constant integer := 3000;
  today constant date := (now() at time zone 'Asia/Seoul')::date;
begin
  if p_key not in ('link_click','depth','card_open') then
    raise exception 'brief_event_bump: unknown key %', p_key;
  end if;
  s := lower(coalesce(p_sub, ''));
  if p_key = 'depth' then
    if s not in ('25','50','75','100') then
      raise exception 'brief_event_bump: bad depth %', s;   -- 깊이는 닫힌 4값 — 'other' 칸이 없다
    end if;
  elsif p_key = 'link_click' then
    if s not in ('src','firm','term','doc','toc','other') then s := 'other'; end if;
  else
    if s not in ('deep','merged','detail','other') then s := 'other'; end if;
  end if;
  d := case when p_device = 'mobile' then 'mobile' else 'pc' end;
  c := coalesce(p_card_type, '');
  if c !~ '^[A-Za-z0-9가-힣 ._/&()+-]{1,30}$' then c := 'other'; end if;
  if not exists (select 1 from public.brief_event_counts b
                  where b.day = today and b.key = p_key and b.sub = s
                    and b.card_type = c and b.device = d)
     and (select count(*) from public.brief_event_counts) >= cap then
    c := 'other';
  end if;
  insert into public.brief_event_counts as be (day, key, sub, card_type, device, total, updated_at)
  values (today, p_key, s, c, d, 1, now())
  on conflict (day, key, sub, card_type, device) do update
  set total = be.total + 1, updated_at = now();
end;
$$;

revoke all on function public.brief_event_bump(text, text, text, text) from public, anon, authenticated;
grant execute on function public.brief_event_bump(text, text, text, text) to anon, authenticated;

-- 판독 — 키·하위값·기기별 합계 + 카드 유형별 펼침. 성장 일보/admin 이 부른다(authenticated 만).
create or replace function public.brief_event_report()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'by_key', coalesce((select jsonb_agg(jsonb_build_object(
                'key', key, 'sub', sub, 'device', device, 'total', s) order by key, sub, device)
              from (select key, sub, device, sum(total) as s
                      from public.brief_event_counts group by key, sub, device) g), '[]'::jsonb),
    'card_open_by_type', coalesce((select jsonb_agg(jsonb_build_object(
                'card_type', card_type, 'total', s) order by s desc, card_type)
              from (select card_type, sum(total) as s from public.brief_event_counts
                     where key = 'card_open' group by card_type) g), '[]'::jsonb),
    'first_day', (select min(day) from public.brief_event_counts),
    'last_day', (select max(day) from public.brief_event_counts)
  );
$$;

revoke all on function public.brief_event_report() from public, anon, authenticated;
grant execute on function public.brief_event_report() to authenticated;
