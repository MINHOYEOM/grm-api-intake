-- 087 findings_search_cache_sync — 086 검색 캐시의 daily 목록(용어사전 사례 링크)을 목록 한 벌로 맞추는 RPC.
-- Max local web/migrations prefix was 086 on main aa1cda8. Additive only — 086 의 표·함수·cron 무변경.
--
-- ★왜: 086 은 daily 계층(용어사전 "사례 N건 보기" 링크 = `p_q`+`p_text_only=true`) 192종을 **시드로**
--   굳혔다. 용어사전은 매주 목요일 `grm-glossary-cases.yml` 이 `web/data/glossary_cases.json` 을 다시
--   재고, 용어가 늘거나(사례가 새로 생긴 용어) 줄면(사례 0 이 된 용어) 그 q 목록이 달라진다. 목록이
--   시드에 묶여 있으면 **새 용어의 링크는 영영 캐시를 못 타고**(종전 속도 0.8s), 사라진 용어는 매일
--   헛계산된다. 이 파일은 그 목록을 저장소의 glossary_cases.json 과 같게 맞추는 **쓰기 표면 하나**를
--   만든다 — 워크플로가 표에 직접 쓰지 않고, q 목록만 넘긴다.
--
-- ★무엇을: `findings_search_cache_sync(p_qs text[])` — security definer, **service_role 만** 실행.
--   ① 각 q 를 086 의 정규화 함수(`findings_search_cache_args(p_q, p_text_only := true)`)에 통과시켜
--      daily 행의 키를 만든다(렌더러 링크 → findings.js 호출과 같은 키로 접히도록 같은 함수).
--   ② 없는 키는 `tier='daily'` 로 넣는다(payload 는 비워 둔다 — 다음 daily 갱신 12:10 KST 가 채우고,
--      그때까지 그 링크는 종전 경로로 계산된다 = 폴백).
--   ③ 목록에 없는 daily 행은 지운다. **hot 행은 절대 건드리지 않는다**(tier 로 가른다).
--   ④ 한 번에 지울 수 있는 양에 상한을 둔다 — 지우려는 행이 max(20, 현재 daily 의 25%) 를 넘으면
--      전체를 거부한다(빈 파일·잘린 파일·잘못된 경로가 목록을 통째로 비우는 사고 방지. 용어사전이
--      진짜로 그만큼 줄어드는 주는 사람이 두 번에 나눠 부르면 된다). 빈 목록도 거부한다.
--   ⑤ 형식 제약: 원소 400개 이하 · btrim 후 1~64자 · 제어문자 없음. 어긋나는 원소는 **버리고 개수로
--      보고**한다(함수를 죽이지 않는다 — 나머지 목록은 맞춰야 한다).
--
-- ★계약 불변: 086 의 공개 RPC·읽기·갱신·cron 은 그대로다. 이 함수는 표의 **행 집합**만 바꾼다.
--   anon/authenticated 는 실행 불가(권한 회수) — 목록 오염 표면 0.
--
-- ★검증(적용 직후): 현재 glossary_cases.json 의 q 192종으로 dry-run 없이 한 번 부르면
--   `added=0 · removed=0 · total=192` 여야 한다(시드와 동일 목록이므로 무변경).

create or replace function public.findings_search_cache_sync(p_qs text[]) returns jsonb
language plpgsql security definer set search_path to 'public' as $$
declare
  n_in        integer;
  n_daily     integer;
  n_valid     integer;
  n_added     integer;
  n_removed   integer;
  n_to_remove integer;
  cap_remove  integer;
begin
  if p_qs is null then
    raise exception 'findings_search_cache_sync: p_qs is null';
  end if;
  n_in := coalesce(array_length(p_qs, 1), 0);
  if n_in = 0 then
    raise exception 'findings_search_cache_sync: empty list refused (would delete every daily row)';
  end if;
  if n_in > 400 then
    raise exception 'findings_search_cache_sync: too many terms (% > 400)', n_in;
  end if;

  -- 목표 키 집합(정규화·중복 제거·형식 게이트). 세션 임시 표 — 함수가 끝나면 사라진다.
  create temp table _sync_want on commit drop as
    select distinct public.findings_search_cache_args(p_q := btrim(q), p_text_only := true) as args
    from unnest(p_qs) as t(q)
    where btrim(coalesce(q, '')) <> ''
      and length(btrim(q)) <= 64
      and q !~ '[[:cntrl:]]';
  select count(*) into n_valid from _sync_want;
  if n_valid = 0 then
    raise exception 'findings_search_cache_sync: no valid term after format gate';
  end if;

  select count(*) into n_daily from public.findings_search_cache where tier = 'daily';
  select count(*) into n_to_remove
    from public.findings_search_cache c
   where c.tier = 'daily'
     and not exists (select 1 from _sync_want w where w.args = c.args);
  cap_remove := greatest(20, (n_daily * 25) / 100);
  if n_to_remove > cap_remove then
    raise exception 'findings_search_cache_sync: would remove % daily rows (> cap %) — refused, split the change or check the input file',
      n_to_remove, cap_remove;
  end if;

  with ins as (
    insert into public.findings_search_cache (args, tier)
    select w.args, 'daily' from _sync_want w
    on conflict (args) do nothing
    returning 1
  )
  select count(*) into n_added from ins;

  with del as (
    delete from public.findings_search_cache c
     where c.tier = 'daily'
       and not exists (select 1 from _sync_want w where w.args = c.args)
    returning 1
  )
  select count(*) into n_removed from del;

  return jsonb_build_object(
    'received', n_in,
    'valid',    n_valid,
    'dropped',  n_in - n_valid,
    'added',    n_added,
    'removed',  n_removed,
    'total_daily', (select count(*) from public.findings_search_cache where tier = 'daily'),
    'hot_rows',    (select count(*) from public.findings_search_cache where tier = 'hot'));
end;
$$;
revoke execute on function public.findings_search_cache_sync(text[]) from public, anon, authenticated;
grant execute on function public.findings_search_cache_sync(text[]) to service_role;

notify pgrst, 'reload schema';
