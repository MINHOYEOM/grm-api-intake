-- ============================================================================
-- 128_ops_measure.sql — 운영 센터 자동 측정(SQL 갈래) (PR6/7, 설계서 2026-11-06 §2-6·부록 C-2·R5)
--
-- 무엇: 값이 비거나 낡은 목표를 **매일 새벽 DB 안에서** 다시 잰다. AI 호출 0 · 유료 API 0 · 코드(SQL)만.
--   private.ops_m_*(p_now)        목표 하나를 재는 함수. {value, note, aux} 를 돌려준다.
--                                 value 가 null 이면 '측정 보류'(분모 0 · 기준 기록 없음 등) — 값을 지어내지 않는다.
--   private.ops_measure_all_at(t) 본체. measure_kind='sql' 인 활성 목표를 돌며 measure_ref 의 함수를 부르고
--                                 public.ops_goal_set(125)과 같은 경로로 저장한다. 목표마다 따로 예외를 잡아
--                                 하나가 실패해도 나머지는 계속 잰다. 실패·보류는 값·측정 시각을 건드리지 않고
--                                 note 만 '측정 실패 MM-DD: …' / '측정 보류 MM-DD: …' 로 바꾼다 → 값은 낡아
--                                 주기×1.5 뒤 '측정 필요'로 자연 강등(123 의 규칙 그대로 · 감시기가 죽어도 초록 금지).
--   public.ops_measure_all()      pg_cron 이 부르는 진입점. service_role 전용 · security definer · search_path 고정.
--   private.ops_measure_log       측정 한 번 = 한 줄(상태·값·메모·걸린 ms). 180일 보관. 실패 이력·시간 확인용.
--   인덱스 3개(가산)              측정 집계를 5초 안에 끝내려고 — 아래 '5초' 참고.
--   목표 시드: 새 목표 3개 + 기존 목표의 measure_ref 채우기(아래 '목표').
--
-- 측정 정의(분자·분모·기준을 note 에 같이 적는다 — 숫자 출처가 보이게):
--   dq-clause-wl    경고서한 조항 연결률 = finding_clause 가 1개 이상인 지적 / source='FDA Warning Letter' 전체.
--                   (경고서한만 세는 함수가 없어 findings.source 로 정의. distinct finding_id 와 같은 exists 반세미조인)
--   dq-clause-mfds  식약처 조항 연결률 = 같은 방식 / agency='MFDS' 전체(11-06 조회 '에이전시 MFDS 전체 2,360건' 기준)
--   dq-term-min     용어 연결률 최저 유형 = finding_term 이 1개 이상인 지적 / 유형 전체, 유형 = source
--                   (식약처는 회수(recall-quality)와 나머지로 둘). 분모 20건 미만 유형은 제외(표본 부족).
--   infra-ingest-age 마지막 수집 적재 후 경과(시간) = now − max(raw_signals.ingested_at)
--   tr-backlog      3일 넘은 미번역 = 경고서한+483 중 한글 본문 없음 · 번역 큐(035)와 같은 필터
--                   (scope_status='ok' · rejected 제외) · 적재 3일 초과. 전체 미번역 수도 note 에.
--   infra-db-size   무료 DB 사용량(MB) = pg_database_size. note 에 무료 한도 500MB 대비 %.
--   growth-sub-top  구독 신청 1위 경로 비율 = funnel_touch_report().by_channel 1위 / total_submits(087 누적).
--   pr-subs-day     하루 구독자 증감 = newsletter_subscribers_daily 같은 목록의 최신 두 snap_date 값 차.
--                   스냅샷이 2일 넘게 낡았으면 보류(수집이 멈춘 걸 '증감 0'으로 덮지 않는다).
--                   ★출처(누가 언제 구독했나) 확인은 Brevo 가 정답 — 이 값은 매일 읽는 방향 지표다.
--   GitHub 갈래(dev-ci·dev-pr-old·dev-link-streak·infra-deploy-fail·sr-ontime·edu-terms-week)는
--   .github/workflows/grm-ops-measure.yml + web/ops_measure_github.py 가 잰다(measure_ref 로 표시).
--   ★edu-terms-week 는 measure_kind 를 'github' 로 바꾼다 — 용어 정본은 DB 표가 아니라 web/data/glossary.json
--     이라(107 머리말) SQL 로는 '연결된 용어'만 셀 수 있고, 새 용어는 연결 백필 전엔 거기에 안 잡힌다.
--   손으로 재는 목표(measure_kind='manual')는 이 파일이 건드리지 않는다.
--
-- 5초: 측정은 전부 count/max 한 번씩이고 인덱스 경로를 탄다 —
--   ops_findings_measure_idx (source, document_type, finding_id)  유형별 분모 + exists 탐침(PK 인덱스)
--   ops_findings_untranslated_idx (source, ingested_at) where 미번역 큐 조건  부분 인덱스(행 수 = 미번역 수)
--   ops_raw_signals_ingested_idx (ingested_at)                     max() 를 인덱스 끝 한 칸으로
--   ★PostgreSQL 은 문장이 시작될 때 statement_timeout 타이머를 건다 — 함수 안에서 set 해도 이미 돌고 있는
--     이 호출에는 안 걸린다. 그래서 5초 상한은 **cron 명령의 `set statement_timeout = '5s'`** 가 건다(아래).
--     5초가 넘으면 그때 돌던 측정이 57014(query_canceled)로 끊기고 → 그 목표만 '측정 실패: 시간 초과',
--     나머지는 계속 잰다(타이머는 한 번만 울린다). 목표마다 걸린 ms 는 결과 JSON·ops_measure_log 에 남는다.
--
-- 권한: 새 함수 전부 public·anon·authenticated 셋 명시 회수(10-28: public 만 회수하면 anon 이 남는다).
--   ops_measure_all 만 service_role 에 EXECUTE(security definer — 표 쓰기는 service_role 뿐이라).
--   측정 함수·본체는 private · invoker(definer 를 통해서만 돈다). measure_ref 는 동적 호출 전에
--   '^private\.ops_m_[a-z0-9_]+$' 형식과 실제 함수 존재(to_regprocedure)를 확인한다 — 표의 글자를 SQL 로 실행하지 않는다.
--
-- 적용: 122~127 다음. 비서실이 SQL 검토 후 SQL 에디터에서(PR 에는 파일만). pg_cron 등록은 이 파일에 없다.
-- 적용 후 확인(SQL 에디터):
--   select jsonb_pretty(public.ops_measure_all());       -- 한 번 손으로 돌려 본다. results[].ms 가 전부 5000 미만이어야
--   select slug, current_value, measured_at, note from public.ops_goal where measure_kind = 'sql' order by slug;
--   select slug, status, value, ms, note from private.ops_measure_log order by id desc limit 20;
--   손으로 센 값과 대조(설계서 PR6 시험 ①):
--     select count(*), count(*) filter (where exists (select 1 from public.finding_clause c where c.finding_id = f.finding_id))
--       from public.findings f where f.agency = 'MFDS';                    -- 11-06: 2,360 중 1,185
--     explain analyze select count(*) from public.findings f where f.source = 'FDA Warning Letter';  -- Index Only Scan 이어야
--   select routine_schema, routine_name, grantee, privilege_type from information_schema.routine_privileges
--    where routine_name like 'ops\_m\_%' or routine_name in ('ops_measure_all','ops_measure_all_at') order by 2,3;
--   → ops_measure_all: service_role(+postgres) · private.* : postgres 만. anon·authenticated·PUBLIC 행 없음.
--
-- 매일 03:30 KST 등록(비서실이 위 확인 뒤 적용 — 이 파일은 cron 을 만들지 않는다):
--   select cron.schedule('grm-ops-measure', '30 18 * * *',
--          $$set statement_timeout = '5s'; select public.ops_measure_all()$$);
--   확인: select jobid, schedule, command from cron.job where jobname = 'grm-ops-measure';
--        select status, return_message, start_time from cron.job_run_details
--         where jobid = (select jobid from cron.job where jobname = 'grm-ops-measure') order by start_time desc limit 5;
--
-- ★순서: 124 를 다시 돌리면 edu-terms-week 의 measure_kind 가 'sql' 로 돌아간다 → 그 뒤엔 128 도 다시 돌린다
--   (돌리지 않아도 measure_ref 가 private.ops_m_ 형식이 아니라 SQL 갈래는 그 목표를 건너뛴다 — 오작동은 없음).
--
-- 되돌리기(측정만 멈추고 값은 수동 갱신으로 계속 동작):
--   select cron.unschedule('grm-ops-measure');
--   drop function if exists public.ops_measure_all();
--   drop function if exists private.ops_measure_all_at(timestamptz);
--   drop function if exists private.ops_m_clause_pct_wl(timestamptz), private.ops_m_clause_pct_mfds(timestamptz),
--     private.ops_m_term_min(timestamptz), private.ops_m_ingest_age(timestamptz), private.ops_m_tr_backlog(timestamptz),
--     private.ops_m_db_size_mb(timestamptz), private.ops_m_funnel_top_share(timestamptz),
--     private.ops_m_subs_daily_delta(timestamptz), private.ops_m_ratio(bigint, bigint, text),
--     private.ops_m_num(numeric), private.ops_m_channel_ko(text);
--   drop table if exists private.ops_measure_log;
--   drop index if exists public.ops_findings_measure_idx, public.ops_findings_untranslated_idx, public.ops_raw_signals_ingested_idx;
--   delete from public.ops_goal where slug in ('infra-db-size','growth-sub-top','pr-subs-day');
--   update public.ops_goal set measure_ref = null where measure_ref like 'private.ops_m_%' or measure_ref = 'web/ops_measure_github.py';
--   update public.ops_goal set measure_kind = 'sql' where slug = 'edu-terms-week';
-- ============================================================================

create schema if not exists private;

-- ── 0. 인덱스(가산 · 측정 집계를 인덱스 경로로) ─────────────────────────────────
create index if not exists ops_findings_measure_idx on public.findings (source, document_type, finding_id);
create index if not exists ops_findings_untranslated_idx on public.findings (source, ingested_at)
  where finding_text_ko = '' and scope_status = 'ok' and review_status <> 'rejected';
create index if not exists ops_raw_signals_ingested_idx on public.raw_signals (ingested_at);

-- ── 1. 측정 기록 표(private · 화면·클라이언트 노출 없음) ─────────────────────────
create table if not exists private.ops_measure_log (
  id bigint generated always as identity primary key,
  slug text not null,
  measured_at timestamptz not null,
  status text not null check (status in ('ok','hold','timeout','error')),
  value numeric,
  note text check (note is null or char_length(note) <= 200),
  aux jsonb not null default '{}'::jsonb,
  ms integer
);
create index if not exists ops_measure_log_slug_idx on private.ops_measure_log (slug, measured_at desc);
alter table private.ops_measure_log enable row level security;
revoke all on private.ops_measure_log from public, anon, authenticated;
do $$
declare s text := pg_get_serial_sequence('private.ops_measure_log', 'id');
begin
  if s is null then
    raise exception 'ops_measure_log 의 id 시퀀스를 찾지 못함';
  end if;
  execute format('revoke all on sequence %s from public, anon, authenticated', s);
end $$;

-- ── 2. 보조(글자 만들기) ─────────────────────────────────────────────────────
-- 천 단위 쉼표 정수/소수 한 자리.
create or replace function private.ops_m_num(p numeric)
returns text
language sql
stable
set search_path = public
as $$
  select case when p is null then '?'
              when p = trunc(p) then to_char(p, 'FM999,999,999,990')
              else to_char(round(p, 1), 'FM999,999,999,990.0') end
$$;

-- 비율 결과 한 벌: 분모 0 이면 value null(= 측정 보류). 값은 소수 한 자리 %.
create or replace function private.ops_m_ratio(p_den bigint, p_num bigint, p_basis text)
returns jsonb
language sql
stable
set search_path = public
as $$
  select case
    when coalesce(p_den, 0) <= 0 then
      jsonb_build_object('value', null, 'note', '분모 0 · ' || p_basis)
    else
      jsonb_build_object(
        'value', round(100.0 * p_num / p_den, 1),
        'note', private.ops_m_num(round(100.0 * p_num / p_den, 1)) || '% (' || private.ops_m_num(p_den)
                || '건 중 ' || private.ops_m_num(p_num) || '건) · ' || p_basis,
        'aux', jsonb_build_object('num', p_num, 'den', p_den))
  end
$$;

-- 087 채널 이름 → 한국어(그 밖은 원문 그대로, utm:… 포함)
create or replace function private.ops_m_channel_ko(p text)
returns text
language sql
stable
set search_path = public
as $$
  select case p
    when 'naver' then '네이버' when 'google' then '구글' when 'direct' then '직접'
    when 'internal' then '사이트 안' when 'ai' then 'AI 검색' when 'other_search' then '기타 검색'
    when 'newsletter' then '뉴스레터' when 'linkedin' then '링크드인' when 'teams' then '팀즈'
    when 'other' then '기타' else coalesce(p, '?') end
$$;

-- ── 3. 측정 함수(목표 하나 = 함수 하나 · 인자는 '지금') ───────────────────────────
-- 경고서한 조항 연결률
create or replace function private.ops_m_clause_pct_wl(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  select private.ops_m_ratio(c.den, c.num, '경고서한 전체 기준')
    from (select count(*) as den,
                 count(*) filter (where exists (select 1 from public.finding_clause fc
                                                 where fc.finding_id = f.finding_id)) as num
            from public.findings f
           where f.source = 'FDA Warning Letter') c
$$;

-- 식약처 조항 연결률
create or replace function private.ops_m_clause_pct_mfds(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  select private.ops_m_ratio(c.den, c.num, '식약처(agency MFDS) 전체 기준')
    from (select count(*) as den,
                 count(*) filter (where exists (select 1 from public.finding_clause fc
                                                 where fc.finding_id = f.finding_id)) as num
            from public.findings f
           where f.agency = 'MFDS') c
$$;

-- 용어 연결률 — 유형별로 재서 가장 낮은 유형(분모 20건 미만 유형 제외)
create or replace function private.ops_m_term_min(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  with t as (
    select case when f.source = 'MFDS' and f.document_type = 'recall-quality' then '식약처 회수'
                when f.source = 'MFDS' then '식약처'
                when f.source = 'FDA 483' then '483'
                when f.source = 'FDA Warning Letter' then '경고서한'
                when f.source = 'Health Canada Inspection' then '캐나다'
                else f.source end as kind,
           count(*) as den,
           count(*) filter (where exists (select 1 from public.finding_term ft
                                           where ft.finding_id = f.finding_id)) as num
      from public.findings f
     group by 1
  ),
  r as (
    select t.kind, t.den, t.num, round(100.0 * t.num / t.den, 1) as pct,
           row_number() over (order by round(100.0 * t.num / t.den, 1), t.kind) as rn,
           count(*) over () as kinds
      from t
     where t.den >= 20
  )
  select coalesce(
    (select jsonb_build_object(
              'value', a.pct,
              'note', a.kind || ' ' || private.ops_m_num(a.pct) || '% (' || private.ops_m_num(a.den) || '건 중 '
                      || private.ops_m_num(a.num) || '건) · 다음 '
                      || coalesce(b.kind || ' ' || private.ops_m_num(b.pct) || '%', '없음')
                      || ' · 유형 ' || a.kinds || '개(20건 미만 제외)',
              'aux', jsonb_build_object('kind', a.kind, 'num', a.num, 'den', a.den, 'kinds', a.kinds))
       from r a left join r b on b.rn = 2
      where a.rn = 1),
    jsonb_build_object('value', null, 'note', '분모 20건 이상인 유형 없음'))
$$;

-- 마지막 수집 적재 후 경과(시간)
create or replace function private.ops_m_ingest_age(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  select case
    when x.m is null then jsonb_build_object('value', null, 'note', 'raw_signals 적재 기록 없음')
    else jsonb_build_object(
      'value', round(greatest(extract(epoch from (p_now - x.m)), 0) / 3600.0, 1),
      'note', '마지막 적재 ' || to_char(x.m at time zone 'Asia/Seoul', 'MM-DD HH24:MI') || ' KST (raw_signals 최신)',
      'aux', jsonb_build_object('last', x.m))
  end
    from (select max(r.ingested_at) as m from public.raw_signals r) x
$$;

-- 3일 넘은 미번역(경고서한+483) — 부분 인덱스 조건과 글자까지 같게 쓴다(finding_text_ko 는 not null)
create or replace function private.ops_m_tr_backlog(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  select jsonb_build_object(
    'value', c.old,
    'note', '3일 넘은 미번역 ' || private.ops_m_num(c.old) || '건 · 전체 미번역 ' || private.ops_m_num(c.total)
            || '건 · 정의: 경고서한+483, 한글 없음, 번역 큐 필터, 적재 3일 초과',
    'aux', jsonb_build_object('old', c.old, 'total', c.total))
    from (select count(*) filter (where f.ingested_at < p_now - interval '3 days') as old,
                 count(*) as total
            from public.findings f
           where f.source in ('FDA Warning Letter', 'FDA 483')
             and f.finding_text_ko = '' and f.scope_status = 'ok' and f.review_status <> 'rejected') c
$$;

-- 무료 DB 사용량(MB)
create or replace function private.ops_m_db_size_mb(p_now timestamptz)
returns jsonb
language sql
stable
set search_path = public
as $$
  select jsonb_build_object(
    'value', round(x.b / 1048576.0, 1),
    'note', 'DB ' || private.ops_m_num(round(x.b / 1048576.0, 1)) || 'MB · 무료 한도 500MB 의 '
            || round(100.0 * x.b / (500 * 1048576.0))::int || '%',
    'aux', jsonb_build_object('bytes', x.b))
    from (select pg_database_size(current_database())::numeric as b) x
$$;

-- 구독 신청 1위 경로 비율(087 funnel_touch_report 재사용 · 누적)
create or replace function private.ops_m_funnel_top_share(p_now timestamptz)
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  r jsonb := public.funnel_touch_report();
  v_total numeric := coalesce((r ->> 'total_submits')::numeric, 0);
  v_top jsonb := r -> 'by_channel' -> 0;
  v_2nd jsonb := r -> 'by_channel' -> 1;
  v_pct numeric;
begin
  if v_total <= 0 or v_top is null then
    return jsonb_build_object('value', null, 'note', '구독 신청 0건(분모 0)');
  end if;
  v_pct := round(100.0 * (v_top ->> 'submits')::numeric / v_total, 1);
  return jsonb_build_object(
    'value', v_pct,
    'note', private.ops_m_channel_ko(v_top ->> 'channel') || ' ' || private.ops_m_num(v_pct) || '% ('
            || private.ops_m_num(v_total) || '건 중)'
            || coalesce(' · 2위 ' || private.ops_m_channel_ko(v_2nd ->> 'channel') || ' '
                        || private.ops_m_num(round(100.0 * (v_2nd ->> 'submits')::numeric / v_total, 1)) || '%', '')
            || ' · ' || coalesce(r ->> 'first_kst', '?') || ' 이후 누적',
    'aux', jsonb_build_object('total', v_total, 'top', v_top ->> 'channel'));
end
$$;

-- 하루 구독자 증감(같은 목록의 최신 두 snap_date · snap_date 로만 — captured_at 으로 추론하지 않는다)
create or replace function private.ops_m_subs_daily_delta(p_now timestamptz)
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  v_list integer;
  a record;
  b record;
begin
  select n.list_id into v_list
    from public.newsletter_subscribers_daily n
   order by n.snap_date desc, n.total_subscribers desc, n.list_id
   limit 1;
  if v_list is null then
    return jsonb_build_object('value', null, 'note', '구독자 스냅샷 없음');
  end if;
  select n.snap_date, n.total_subscribers into a
    from public.newsletter_subscribers_daily n where n.list_id = v_list order by n.snap_date desc limit 1;
  select n.snap_date, n.total_subscribers into b
    from public.newsletter_subscribers_daily n
   where n.list_id = v_list and n.snap_date < a.snap_date order by n.snap_date desc limit 1;
  if not found then
    return jsonb_build_object('value', null, 'note', '비교할 전날 스냅샷 없음');
  end if;
  if a.snap_date < (p_now at time zone 'Asia/Seoul')::date - 2 then
    return jsonb_build_object('value', null, 'note', '스냅샷 낡음(마지막 ' || to_char(a.snap_date, 'MM-DD') || ')');
  end if;
  return jsonb_build_object(
    'value', a.total_subscribers - b.total_subscribers,
    'note', to_char(a.snap_date, 'MM-DD') || ' ' || a.total_subscribers || '명 · ' || to_char(b.snap_date, 'MM-DD') || ' '
            || b.total_subscribers || '명 대비'
            || case when a.snap_date - b.snap_date > 1 then ' (' || (a.snap_date - b.snap_date) || '일 차)' else '' end
            || ' · Brevo 목록 스냅샷 차',
    'aux', jsonb_build_object('list_id', v_list, 'latest', a.total_subscribers, 'prev', b.total_subscribers));
end
$$;

-- ── 4. 본체 — 목표마다 따로 잡아 하나가 실패해도 계속 ─────────────────────────────
create or replace function private.ops_measure_all_at(p_now timestamptz)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  g record;
  v_res jsonb;
  v_val numeric;
  v_note text;
  v_err text;
  v_status text;
  v_t0 timestamptz;
  v_ms integer;
  v_day text := to_char(p_now at time zone 'Asia/Seoul', 'MM-DD');
  v_ok text[] := '{}';
  v_hold text[] := '{}';
  v_fail jsonb := '[]'::jsonb;
  v_rows jsonb := '[]'::jsonb;
begin
  for g in
    select gl.slug, gl.measure_ref
      from public.ops_goal gl
     where gl.active and gl.measure_kind = 'sql' and gl.measure_ref is not null
     order by gl.slug
  loop
    v_t0 := clock_timestamp();
    v_res := null; v_val := null; v_note := null; v_err := null; v_status := null;
    begin
      -- 표의 글자를 그대로 실행하지 않는다: 형식 + 실제 함수 존재를 먼저 확인
      if g.measure_ref !~ '^private\.ops_m_[a-z0-9_]{1,40}$'
         or to_regprocedure(g.measure_ref || '(timestamptz)') is null then
        raise exception 'measure_ref 허용 안 됨' using errcode = '22023';
      end if;
      execute format('select %s($1)', g.measure_ref) into v_res using p_now;
      v_val := (v_res ->> 'value')::numeric;
      v_note := left(coalesce(v_res ->> 'note', ''), 100);
      if v_val is null then
        -- 분모 0·기준 기록 없음 = 측정 보류. 값·측정 시각은 그대로(낡으면 자연 강등)
        update public.ops_goal set note = left('측정 보류 ' || v_day || ': ' || v_note, 120) where slug = g.slug;
        v_status := 'hold';
      else
        perform public.ops_goal_set(g.slug, v_val, left(v_note, 120), p_now);
        v_status := 'ok';
      end if;
    exception
      when query_canceled then
        v_status := 'timeout';
        v_err := '시간 초과';
      when others then
        v_status := 'error';
        v_err := sqlstate || ' ' || left(sqlerrm, 60);
    end;
    if v_status in ('timeout', 'error') then
      -- 실패: 이전 값·측정 시각 유지, 메모만(값은 낡아 주기×1.5 뒤 '측정 필요')
      update public.ops_goal set note = left('측정 실패 ' || v_day || ': ' || v_err, 120) where slug = g.slug;
      v_fail := v_fail || jsonb_build_object('slug', g.slug, 'status', v_status, 'error', v_err);
      v_val := null;
    elsif v_status = 'hold' then
      v_hold := v_hold || g.slug;
    else
      v_ok := v_ok || g.slug;
    end if;
    v_ms := round(extract(epoch from (clock_timestamp() - v_t0)) * 1000)::int;
    insert into private.ops_measure_log (slug, measured_at, status, value, note, aux, ms)
    values (g.slug, p_now, v_status, v_val, left(coalesce(v_err, v_note), 200), coalesce(v_res -> 'aux', '{}'::jsonb), v_ms);
    v_rows := v_rows || jsonb_build_object('slug', g.slug, 'status', v_status, 'value', v_val, 'ms', v_ms);
  end loop;
  delete from private.ops_measure_log where measured_at < p_now - interval '180 days';
  return jsonb_build_object('at', p_now, 'updated', to_jsonb(v_ok), 'held', to_jsonb(v_hold),
                            'failed', v_fail, 'results', v_rows);
end
$$;

-- pg_cron 진입점(5초 상한은 cron 명령의 set statement_timeout — 머리말 참고)
create or replace function public.ops_measure_all()
returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
begin
  return private.ops_measure_all_at(now());
end
$$;

-- ── 5. 권한: 셋 다 명시 회수 후 진입점만 service_role ──────────────────────────
revoke all on function private.ops_m_num(numeric) from public, anon, authenticated;
revoke all on function private.ops_m_ratio(bigint, bigint, text) from public, anon, authenticated;
revoke all on function private.ops_m_channel_ko(text) from public, anon, authenticated;
revoke all on function private.ops_m_clause_pct_wl(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_clause_pct_mfds(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_term_min(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_ingest_age(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_tr_backlog(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_db_size_mb(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_funnel_top_share(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_m_subs_daily_delta(timestamptz) from public, anon, authenticated;
revoke all on function private.ops_measure_all_at(timestamptz) from public, anon, authenticated;
revoke all on function public.ops_measure_all() from public, anon, authenticated;
grant execute on function public.ops_measure_all() to service_role;

-- ── 6. 목표: 새 3개(멱등) + 기존 목표의 measure_ref ─────────────────────────────
-- 새 목표는 값을 시드하지 않는다(첫 측정이 채운다). 정의는 이 파일 값으로 덮고, 메모는 아직 한 번도
-- 안 쟀을 때만 덮는다(적용 뒤 측정이 쓴 메모를 재실행이 지우지 않게 — 124 와 같은 규칙).
-- seed:goals begin
insert into public.ops_goal (team_id, slug, label, unit, direction, target, warn_at, bad_at,
                             current_value, measured_at, period_hours, measure_kind, measure_ref, note, sort_order, active)
select t.id, v.slug, v.label, v.unit, v.dir, v.target, v.warn, v.bad,
       null, null, v.period, 'sql', v.ref, v.note, v.ord, true
from (values
  ('infra',    'infra-db-size',  '무료 DB 사용량',          'MB', 'lte', 400::numeric, 400::numeric, 450::numeric, 24, 'private.ops_m_db_size_mb',       3, '첫 자동 측정 전 · 무료 한도 500MB'),
  ('growth',   'growth-sub-top', '구독 신청 1위 경로 비율', '%',  'gte', null,         null,         null,         24, 'private.ops_m_funnel_top_share', 3, '목표 미정 · 구독 신청이 어느 경로에 쏠리는지 매일 읽는 값'),
  ('outreach', 'pr-subs-day',    '하루 구독자 증감',        '명', 'gte', null,         null,         null,         24, 'private.ops_m_subs_daily_delta', 3, '목표 미정 · Brevo 목록 스냅샷 하루 차(출처 확인은 Brevo)')
) v(team, slug, label, unit, dir, target, warn, bad, period, ref, ord, note)
join public.ops_team t on t.slug = v.team
on conflict (slug) do update set
  team_id = excluded.team_id, label = excluded.label, unit = excluded.unit, direction = excluded.direction,
  target = excluded.target, warn_at = excluded.warn_at, bad_at = excluded.bad_at,
  period_hours = excluded.period_hours, measure_kind = excluded.measure_kind, measure_ref = excluded.measure_ref,
  sort_order = excluded.sort_order, active = excluded.active,
  note = case when ops_goal.measured_at is null then excluded.note else ops_goal.note end;
-- seed:goals end

-- seed:refs begin
update public.ops_goal g
   set measure_ref = v.ref, measure_kind = v.kind
  from (values
    ('dq-clause-wl',      'sql',    'private.ops_m_clause_pct_wl'),
    ('dq-clause-mfds',    'sql',    'private.ops_m_clause_pct_mfds'),
    ('dq-term-min',       'sql',    'private.ops_m_term_min'),
    ('infra-ingest-age',  'sql',    'private.ops_m_ingest_age'),
    ('tr-backlog',        'sql',    'private.ops_m_tr_backlog'),
    ('dev-ci',            'github', 'web/ops_measure_github.py'),
    ('dev-pr-old',        'github', 'web/ops_measure_github.py'),
    ('dev-link-streak',   'github', 'web/ops_measure_github.py'),
    ('infra-deploy-fail', 'github', 'web/ops_measure_github.py'),
    ('sr-ontime',         'github', 'web/ops_measure_github.py'),
    ('edu-terms-week',    'github', 'web/ops_measure_github.py')
  ) v(slug, kind, ref)
 where g.slug = v.slug
   and (g.measure_ref is distinct from v.ref or g.measure_kind is distinct from v.kind);
-- seed:refs end
