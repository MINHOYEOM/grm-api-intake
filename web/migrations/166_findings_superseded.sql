-- 166 findings '대체됨' 표식 — 본문이 다시 수집돼 새 지적 글로 대체된 옛 덩어리를 되돌릴 수 있게 숨긴다.
-- 번호는 비서실 배정(2026-11-24, main 마지막 165). Additive only — 새 열·새 정책 변경 없음:
--   1) findings.scope_status 허용값에 'superseded' 가산(제약 교체). 공개 게이트·통계·RPC 는 모두 scope_status = 'ok'
--      만 보므로 이 값은 그대로 숨김이 된다(010·034 이후 공개면 전체). 삭제가 아니다.
--   2) findings_supersessions — 누가·언제·무엇으로 대체했는지와 **숨기기 전 scope_status** 기록(되돌리기 근거).
--   3) grm_findings_supersede(p_items, p_changed_by) — 서비스 롤 전용. 건마다 DB 안에서 다시 확인한 뒤에만 숨긴다:
--        · 옛 행이 지금 scope_status = 'ok' (이미 숨은 행·범위 밖 판정은 건드리지 않는다)
--        · 대체 글(superseded_by)이 모두 같은 raw_signal 의 findings 로 존재하고, 옛 행은 그 안에 없다
--        · 대체 글 중 하나 이상이 **공개 상태**(scope ok · 반려 아님 · 국문 있음 또는 원문 국문) — 034 공개 게이트와 같은 조건
--      review_status·finding_text_ko·translation_method(사람·LLM 결정)는 읽기만 하고 쓰지 않는다.
--   4) grm_findings_unsupersede(p_finding_ids) — 기록된 이전 값으로 되돌리고 기록을 지운다.
--
-- ★왜(2026-11-24): WL 본문 재수집 247행(#1898)에서 옛 결손 본문('Specifically,' 시작)으로 뽑힌 findings 239건이
--   새 본문 기준으로는 더 나오지 않는다. 그중 208건이 공개 중이라, 새 위반 글 273건이 번역되면 같은 문서에
--   옛 덩어리와 새 글이 함께 보인다. 판정은 raw_body_followup.py 가 매일(recover-wl 잡) 같은 추출기로 내고,
--   새 글이 번역·공개된 문서에서만 이 함수를 부른다.
--
-- ★적용 순서: #1898 머지 → 새 글 삽입 → 번역 공개 → 이 migration. 적용 전에는 매일 잡이 'supersede_unavailable'
--   경고만 내고 숨기지 않는다(실패 아님).
--
-- ★앞으로 scope_status 를 일괄 재계산하는 migration 은 'superseded' 행을 제외해야 한다
--   (tests/test_findings_superseded.py 가 167 이후 migration 의 `set scope_status` 를 검사한다).
--
-- ★검증법(적용 뒤):
--   select pg_get_constraintdef(oid) from pg_constraint where conname = 'findings_scope_status_chk';
--   select count(*) from public.findings where scope_status = 'superseded';  -- 적용 직후 0
-- 되돌리기(전부):
--   select public.grm_findings_unsupersede(array(select finding_id from public.findings_supersessions));
--   drop function public.grm_findings_supersede(jsonb, text); drop function public.grm_findings_unsupersede(text[]);
--   drop table public.findings_supersessions;
--   alter table public.findings drop constraint findings_scope_status_chk;
--   alter table public.findings add constraint findings_scope_status_chk check (scope_status in ('ok','non_pharma','fragment'));
-- ============================================================================

alter table public.findings drop constraint if exists findings_scope_status_chk;
alter table public.findings
  add constraint findings_scope_status_chk
  check (scope_status in ('ok', 'non_pharma', 'fragment', 'superseded')) not valid;
alter table public.findings validate constraint findings_scope_status_chk;

create table if not exists public.findings_supersessions (
  finding_id         text primary key references public.findings (finding_id) on delete cascade,
  raw_signal_id      text not null,
  prev_scope_status  text not null,
  superseded_by      jsonb not null default '[]'::jsonb,   -- 대체 글 finding_id 목록
  reason             text not null default '',
  changed_by         text not null default '',
  superseded_at      timestamptz not null default now()
);
create index if not exists findings_supersessions_raw_idx on public.findings_supersessions (raw_signal_id);
alter table public.findings_supersessions enable row level security;
revoke all on table public.findings_supersessions from public;
revoke all on table public.findings_supersessions from anon;
revoke all on table public.findings_supersessions from authenticated;
grant select, insert, delete on table public.findings_supersessions to service_role;

create or replace function public.grm_findings_supersede(p_items jsonb, p_changed_by text default '')
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  it          jsonb;
  v_fid       text;
  v_rid       text;
  v_by        text[];
  v_cur       public.findings%rowtype;
  v_found     integer;
  v_visible   integer;
  n_done      integer := 0;
  skipped     jsonb := '[]'::jsonb;
begin
  if jsonb_typeof(p_items) is distinct from 'array' then
    raise exception 'grm_findings_supersede: p_items 는 배열이어야 한다';
  end if;
  for it in select value from jsonb_array_elements(p_items) loop
    v_fid := it->>'finding_id';
    v_rid := it->>'raw_signal_id';
    v_by := array(select jsonb_array_elements_text(coalesce(it->'superseded_by', '[]'::jsonb)));
    select * into v_cur from public.findings where finding_id = v_fid for update;
    if not found then
      skipped := skipped || jsonb_build_object('finding_id', v_fid, 'why', 'not_found');
      continue;
    end if;
    if v_cur.raw_signal_id is distinct from v_rid then
      skipped := skipped || jsonb_build_object('finding_id', v_fid, 'why', 'raw_signal_mismatch');
      continue;
    end if;
    if v_cur.scope_status <> 'ok' then
      skipped := skipped || jsonb_build_object('finding_id', v_fid, 'why', 'not_ok:' || v_cur.scope_status);
      continue;
    end if;
    if coalesce(array_length(v_by, 1), 0) = 0 or v_fid = any(v_by) then
      skipped := skipped || jsonb_build_object('finding_id', v_fid, 'why', 'bad_superseded_by');
      continue;
    end if;
    select count(*),
           count(*) filter (where f.scope_status = 'ok' and f.review_status <> 'rejected'
                              and (f.finding_text_ko <> '' or f.finding_language = 'KO'))
      into v_found, v_visible
      from public.findings f
     where f.finding_id = any(v_by) and f.raw_signal_id = v_rid;
    if v_found <> cardinality(v_by) then
      skipped := skipped || jsonb_build_object('finding_id', v_fid, 'why', 'superseded_by_missing');
      continue;
    end if;
    if v_visible = 0 then
      skipped := skipped || jsonb_build_object('finding_id', v_fid, 'why', 'replacement_not_public');
      continue;
    end if;
    insert into public.findings_supersessions
      (finding_id, raw_signal_id, prev_scope_status, superseded_by, reason, changed_by)
    values (v_fid, v_rid, v_cur.scope_status, to_jsonb(v_by), coalesce(it->>'reason', ''), coalesce(p_changed_by, ''))
    on conflict (finding_id) do nothing;
    update public.findings set scope_status = 'superseded' where finding_id = v_fid;
    n_done := n_done + 1;
  end loop;
  return jsonb_build_object('superseded', n_done, 'skipped', skipped);
end;
$$;

create or replace function public.grm_findings_unsupersede(p_finding_ids text[])
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  n integer;
begin
  with restored as (
    update public.findings f
       set scope_status = s.prev_scope_status
      from public.findings_supersessions s
     where s.finding_id = f.finding_id
       and f.finding_id = any(p_finding_ids)
       and f.scope_status = 'superseded'
    returning f.finding_id
  ), dropped as (
    delete from public.findings_supersessions s
     where s.finding_id = any(p_finding_ids)
    returning s.finding_id
  )
  select count(*) into n from restored;
  return jsonb_build_object('restored', n);
end;
$$;

comment on function public.grm_findings_supersede(jsonb, text) is
  '본문 재수집으로 대체된 옛 findings 를 숨김(scope_status=superseded) — 대체 글이 공개된 경우만, 기록 남김. (166, 2026-11-24)';
comment on function public.grm_findings_unsupersede(text[]) is
  'grm_findings_supersede 되돌리기 — 기록된 이전 scope_status 로. (166, 2026-11-24)';

revoke all on function public.grm_findings_supersede(jsonb, text) from public;
revoke all on function public.grm_findings_supersede(jsonb, text) from anon;
revoke all on function public.grm_findings_supersede(jsonb, text) from authenticated;
grant execute on function public.grm_findings_supersede(jsonb, text) to service_role;
revoke all on function public.grm_findings_unsupersede(text[]) from public;
revoke all on function public.grm_findings_unsupersede(text[]) from anon;
revoke all on function public.grm_findings_unsupersede(text[]) from authenticated;
grant execute on function public.grm_findings_unsupersede(text[]) to service_role;
