-- ============================================================================
-- 148_raw_signal_versions.sql — 원문 갱신 규칙 R1: 옛 판 보관표 + 한 길뿐인 갱신 함수
--
-- 무엇: 정본 원문(raw_signals)이 바뀔 때 **바뀌기 전 판**을 보관하는 append-only 표 1개와,
--   원문을 바꾸는 유일한 길인 함수 1개를 만든다. 기존 표·함수·RPC 는 하나도 건드리지 않는다
--   (raw_signals 에 칸을 더하지도 않는다 — 현재 판 번호는 보관표에서 계산한다).
--
-- 왜(2026-11-12, 대표 결정 "원문 갱신 규칙을 노션 전환 전에 만든다"):
--   · raw_signals 는 같은 키가 오면 무시해 첫 판만 갖는다. 고칠 때마다 도구를 따로 만들어 raw_json 을
--     PATCH 했고 옛 판은 남지 않았다.
--   · 그 PATCH 가 지문(raw_sha256)을 다시 계산하지 않아, 11-12 실측으로 FDA 483 2,217행·MFDS 168행의
--     지문이 내용과 어긋나 있다(검증 장치 무력화).
--   설계안: GRM_원문갱신규칙_설계안_2026-11-12.md (3절).
--
-- 함수 raw_signal_apply_version 의 계약:
--   · 현재 행의 raw_sha256 이 p_expected_raw_sha256 과 다르면 'conflict' — 그 사이 다른 도구가 바꿨다.
--     호출자는 다시 읽고 재시도한다(낙관적 동시성).
--   · 넘겨받은 p_raw_sha256 이 sha256(p_raw_json) 과 다르면 예외 — 정규화 안 된 JSON·지문 누락을 막는다.
--     지문은 Python(grm_findings.canonical_json) 한 곳에서 계산하고, DB 는 검증만 한다.
--   · 새 raw_json 이 현재와 같고 지문도 같으면 'unchanged'(보관표에 아무것도 안 쌓는다).
--   · 그 외에는 한 트랜잭션에서: 현재 판을 보관표에 넣고 → raw_signals 를 새 판으로 바꾼다 → 'applied'.
--   · change_kind='baseline'(지문 재매김, R2)은 내용이 같고 직렬화·지문만 바뀌는 경우다. 옛 raw_json 을
--     다시 쌓지 않고(용량 — DB 351MB/500MB) 옛 지문만 남긴다. 그래서 그 경우만 raw_json 이 null 이다.
--
-- 권한: 표는 RLS 켜고 정책 없음. 표·함수 모두 public·anon·authenticated 를 **명시해서** 회수한다
--   (10-28 교훈: revoke from public 만으로는 Supabase 가 직접 준 anon 권한이 남는다). service_role 만.
--   함수는 security invoker(service_role 이 표 권한을 직접 가진다), search_path 고정.
--
-- 이 migration 은 아무 행도 바꾸지 않는다. 트리거(raw_json 을 함수 밖에서 바꾸면 거부)는 수리 도구를 이
-- 함수로 옮긴 뒤(R3) 별도 migration 으로 건다 — 지금 걸면 사용 중인 483 수리 도구가 바로 막힌다.
--
-- 적용 후 확인:
--   select grantee, privilege_type from information_schema.role_table_grants
--    where table_schema='public' and table_name='raw_signal_versions' order by 1,2;   → service_role(+postgres)
--   select routine_name, grantee from information_schema.routine_privileges
--    where routine_name='raw_signal_apply_version' order by 2;                       → service_role(+postgres)
--   select relrowsecurity from pg_class where relname='raw_signal_versions';         → true
-- 되돌리기: drop function public.raw_signal_apply_version(text,text,jsonb,text,text,text,jsonb);
--           drop table public.raw_signal_versions;   (보관된 판이 없을 때만)
-- ============================================================================

create table if not exists public.raw_signal_versions (
  raw_signal_id   text        not null references public.raw_signals(raw_signal_id) on delete restrict,
  version_no      integer     not null check (version_no >= 1),
  raw_sha256      text        not null check (char_length(raw_sha256) = 64),
  raw_json        text,                      -- 바뀌기 전 판. baseline 일 때만 null(내용 동일)
  row_json        text,
  record_json     jsonb       not null default '{}'::jsonb,   -- 바뀌기 전 제목·업체·URL·날짜 등 열 값
  valid_from      timestamptz,               -- 그 판이 현재가 된 시각(첫 판은 ingested_at)
  valid_to        timestamptz not null default now(),
  change_kind     text        not null check (change_kind in ('source_update', 'reextract', 'repair', 'baseline')),
  changed_by      text        not null default '',
  diff_summary    jsonb       not null default '{}'::jsonb,   -- 바뀐 칸 이름·길이 전후 — 값은 싣지 않는다
  primary key (raw_signal_id, version_no),
  constraint raw_signal_versions_raw_json_presence
    check (raw_json is not null or change_kind = 'baseline')
);

comment on table public.raw_signal_versions is
  '정본 원문(raw_signals)의 바뀌기 전 판. append-only. 쓰는 길은 raw_signal_apply_version 하나. (148, 2026-11-12)';

alter table public.raw_signal_versions enable row level security;
revoke all on table public.raw_signal_versions from public;
revoke all on table public.raw_signal_versions from anon;
revoke all on table public.raw_signal_versions from authenticated;
grant select, insert on table public.raw_signal_versions to service_role;

create or replace function public.raw_signal_apply_version(
  p_raw_signal_id        text,
  p_expected_raw_sha256  text,
  p_record               jsonb,      -- 새 판의 raw_signals 열 값(raw_json·row_json·raw_sha256 필수)
  p_change_kind          text,
  p_changed_by           text,
  p_content_sha256       text,       -- 새 판의 본문 지문(설계안 2-1). 기록용 — 비어도 된다
  p_diff_summary         jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  cur        public.raw_signals%rowtype;
  new_raw    text := p_record->>'raw_json';
  new_row    text := p_record->>'row_json';
  new_sha    text := p_record->>'raw_sha256';
  next_no    integer;
  old_from   timestamptz;
begin
  if p_change_kind not in ('source_update', 'reextract', 'repair', 'baseline') then
    raise exception 'raw_signal_apply_version: change_kind % 허용 안 됨', p_change_kind;
  end if;
  if new_raw is null or new_row is null or new_sha is null then
    raise exception 'raw_signal_apply_version: raw_json·row_json·raw_sha256 필수';
  end if;
  if encode(sha256(convert_to(new_raw, 'UTF8')), 'hex') <> new_sha then
    raise exception 'raw_signal_apply_version: raw_sha256 이 raw_json 과 맞지 않음(정규화·지문 계산 확인)';
  end if;

  select * into cur from public.raw_signals where raw_signal_id = p_raw_signal_id for update;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;
  if cur.raw_sha256 <> p_expected_raw_sha256 then
    return jsonb_build_object('status', 'conflict', 'current_raw_sha256', cur.raw_sha256);
  end if;
  if cur.raw_json = new_raw and cur.raw_sha256 = new_sha then
    return jsonb_build_object('status', 'unchanged');
  end if;
  if p_change_kind = 'baseline'
     and cur.raw_json::jsonb is distinct from new_raw::jsonb then
    raise exception 'raw_signal_apply_version: baseline 은 내용이 같아야 함(직렬화·지문만 바뀜)';
  end if;

  select coalesce(max(version_no), 0) + 1, max(valid_to)
    into next_no, old_from
    from public.raw_signal_versions where raw_signal_id = p_raw_signal_id;

  insert into public.raw_signal_versions
    (raw_signal_id, version_no, raw_sha256, raw_json, row_json, record_json,
     valid_from, change_kind, changed_by, diff_summary)
  values
    (cur.raw_signal_id, next_no, cur.raw_sha256,
     case when p_change_kind = 'baseline' then null else cur.raw_json end,
     case when p_change_kind = 'baseline' then null else cur.row_json end,
     jsonb_build_object(
       'title', cur.title, 'firm_name', cur.firm_name, 'site_name', cur.site_name,
       'site_country', cur.site_country, 'modality', cur.modality,
       'source_url', cur.source_url, 'official_url', cur.official_url,
       'published_date', cur.published_date, 'source_kind', cur.source_kind,
       'collected_at', cur.collected_at, 'extraction_status', cur.extraction_status,
       'content_sha256', coalesce(p_diff_summary->>'old_content_sha256', '')),
     coalesce(old_from, cur.ingested_at),
     p_change_kind, coalesce(p_changed_by, ''),
     coalesce(p_diff_summary, '{}'::jsonb) || jsonb_build_object('new_content_sha256', coalesce(p_content_sha256, '')));

  update public.raw_signals set
    raw_json          = new_raw,
    row_json          = new_row,
    raw_sha256        = new_sha,
    title             = coalesce(p_record->>'title', title),
    firm_name         = case when p_record ? 'firm_name' then p_record->>'firm_name' else firm_name end,
    site_name         = case when p_record ? 'site_name' then p_record->>'site_name' else site_name end,
    site_country      = case when p_record ? 'site_country' then p_record->>'site_country' else site_country end,
    modality          = case when p_record ? 'modality' then p_record->>'modality' else modality end,
    source_url        = case when p_record ? 'source_url' then p_record->>'source_url' else source_url end,
    official_url      = case when p_record ? 'official_url' then p_record->>'official_url' else official_url end,
    published_date    = case when p_record ? 'published_date' then p_record->>'published_date' else published_date end,
    source_kind       = coalesce(nullif(p_record->>'source_kind', ''), source_kind),
    extraction_status = coalesce(nullif(p_record->>'extraction_status', ''), extraction_status)
  where raw_signal_id = p_raw_signal_id;

  return jsonb_build_object('status', 'applied', 'version_no', next_no + 1);
end;
$$;

comment on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) is
  '정본 원문을 바꾸는 유일한 길: 지문 검증 → 낙관적 동시성 → 옛 판 보관 → 교체. (148, 2026-11-12)';

revoke all on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) from public;
revoke all on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) from anon;
revoke all on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) from authenticated;
grant execute on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) to service_role;
