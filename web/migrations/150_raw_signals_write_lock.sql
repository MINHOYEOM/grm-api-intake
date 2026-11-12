-- ============================================================================
-- 150_raw_signals_write_lock.sql — 원문 갱신 규칙 R3: 함수 밖 원문 변경 거부 + 보관표 append-only 권한
--
-- 무엇:
--   1) raw_signal_apply_version(148)을 다시 만든다 — 본문은 148 과 같고, raw_signals 를 바꾸기 직전에
--      트랜잭션 한정 표식 grm.raw_apply_version='on' 을 켜고 끝나면 끈다. 그 밖은 한 글자도 같다.
--   2) raw_signals 에 BEFORE UPDATE 트리거: 원문 칸(raw_json·row_json·raw_sha256) 중 하나라도 바뀌는데
--      표식이 없으면 거부한다. 제목·업체·제품군(modality) 같은 파생 칸만 바꾸는 갱신은 그대로 통과한다.
--      PATCH·merge-duplicates upsert(INSERT … ON CONFLICT DO UPDATE)·SQL 직접 UPDATE 모두 여기서 막힌다.
--      일상 수집의 ignore-duplicates(ON CONFLICT DO NOTHING)와 새 행 INSERT 는 UPDATE 가 아니라 영향 없다.
--   3) raw_signal_versions: service_role 의 UPDATE·DELETE·TRUNCATE·REFERENCES·TRIGGER 회수 → SELECT·INSERT 만.
--      148 의 grant 는 select·insert 였지만 Supabase 기본 권한으로 service_role 이 전부 갖고 있었다(11-12 실측).
--      그리고 행 단위 UPDATE·DELETE 거부 트리거(소유자 postgres 도 실수로 못 지우게).
--
-- 왜 지금: R3 에서 원문을 PATCH 하던 도구 4개(483 상세·483 OCR·식약처 평가·WL 본문)를 함수 경유로 옮겨
--   CI 가드 DEBT 가 0 이 됐다(tests/test_raw_signals_write_guard.py). 도구 이관 PR 이 main 에 들어간 뒤에만
--   적용한다 — 먼저 걸면 옛 코드로 도는 예약 수리 워크플로가 바로 막힌다.
--
-- 비상구(사람만): 정말 SQL 로 원문을 고쳐야 하면 같은 트랜잭션에서
--     select set_config('grm.raw_apply_version', 'on', true);  update …;
--   를 쓴다. 다만 그러면 옛 판이 남지 않으니 함수를 쓰는 것이 원칙이다. PostgREST 로는 표식을 켤 길이 없다
--   (set_config 는 노출 스키마 밖).
--
-- 적용 후 확인:
--   select tgname from pg_trigger where tgrelid='public.raw_signals'::regclass and not tgisinternal;
--     → raw_signals_raw_write_lock
--   select grantee, string_agg(privilege_type, ',' order by privilege_type) from information_schema.role_table_grants
--    where table_schema='public' and table_name='raw_signal_versions' group by 1;
--     → service_role: INSERT,SELECT (postgres 는 소유자라 그대로)
--   -- 거부 확인(롤백되는 시험):
--   begin; update public.raw_signals set raw_json = raw_json || ' ' where raw_signal_id = (select raw_signal_id from public.raw_signals limit 1); rollback;
--     → ERROR raw_signals: 원문 칸은 raw_signal_apply_version 으로만 바꾼다
-- 되돌리기:
--   drop trigger raw_signals_raw_write_lock on public.raw_signals;
--   drop trigger raw_signal_versions_append_only on public.raw_signal_versions;
--   drop function public.raw_signals_raw_write_lock(); drop function public.raw_signal_versions_append_only();
--   (함수 본문의 set_config 두 줄은 남아도 무해하다)
-- ============================================================================

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

  -- [150] 이 함수 안에서만 원문 칸 변경을 허락한다(트랜잭션 한정 표식 — 트리거가 본다).
  perform set_config('grm.raw_apply_version', 'on', true);
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

  perform set_config('grm.raw_apply_version', '', true);
  return jsonb_build_object('status', 'applied', 'version_no', next_no + 1);
end;
$$;

comment on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) is
  '정본 원문을 바꾸는 유일한 길: 지문 검증 → 낙관적 동시성 → 옛 판 보관 → 교체. 트리거(150)가 다른 길을 거부. (148·150, 2026-11-12)';

revoke all on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) from public;
revoke all on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) from anon;
revoke all on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) from authenticated;
grant execute on function public.raw_signal_apply_version(text, text, jsonb, text, text, text, jsonb) to service_role;

create or replace function public.raw_signals_raw_write_lock()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if (new.raw_json, new.row_json, new.raw_sha256) is distinct from (old.raw_json, old.row_json, old.raw_sha256)
     and coalesce(current_setting('grm.raw_apply_version', true), '') <> 'on' then
    raise exception 'raw_signals: 원문 칸은 raw_signal_apply_version 으로만 바꾼다 (raw_signal_id=%)', old.raw_signal_id
      using errcode = 'insufficient_privilege',
            hint = 'raw_versioning.replace_raw / apply_record 를 쓴다(150_raw_signals_write_lock.sql)';
  end if;
  return new;
end;
$$;

comment on function public.raw_signals_raw_write_lock() is
  '원문 칸(raw_json·row_json·raw_sha256)은 raw_signal_apply_version 안에서만 바뀐다. (150, 2026-11-12)';

drop trigger if exists raw_signals_raw_write_lock on public.raw_signals;
create trigger raw_signals_raw_write_lock
  before update on public.raw_signals
  for each row execute function public.raw_signals_raw_write_lock();

create or replace function public.raw_signal_versions_append_only()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  raise exception 'raw_signal_versions 는 append-only 다 (% 거부)', tg_op
    using errcode = 'insufficient_privilege';
end;
$$;

drop trigger if exists raw_signal_versions_append_only on public.raw_signal_versions;
create trigger raw_signal_versions_append_only
  before update or delete on public.raw_signal_versions
  for each row execute function public.raw_signal_versions_append_only();

-- 트리거 함수는 직접 부를 일이 없다 — 실행 권한을 명시해서 회수(10-28 교훈).
revoke all on function public.raw_signals_raw_write_lock() from public, anon, authenticated;
revoke all on function public.raw_signal_versions_append_only() from public, anon, authenticated;

revoke update, delete, truncate, references, trigger on table public.raw_signal_versions from service_role;
revoke all on table public.raw_signal_versions from public, anon, authenticated;
grant select, insert on table public.raw_signal_versions to service_role;
