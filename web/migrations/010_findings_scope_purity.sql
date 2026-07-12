-- FIND-1 데이터 순도 마이그레이션 — findings 에 섞여든 제약 GMP 범위 밖 데이터·추출 실패
-- 단편에 되돌릴 수 있는 플래그(scope_status)를 붙인다. 삭제가 아니다 — row 는 전부 그대로
-- 남고, 공개 게이트(006)·집계 RPC(007/008/009)·향후 신규 유입(트리거)에서만 걸러진다.
--
-- 근거(컨트롤 타워 라이브 실측, 2026-07-11, 9,013건/문서 1,476개 기준):
--   ① 식품·농업 시설 483 문서에서 나온 findings 477건 (Shell Egg Producer 등 — 제약 GMP
--      범위 밖 establishment_type)
--   ② 임상시험(GCP) 시설 483 문서에서 나온 findings 49건 (IRB/Clinical Investigator/
--      Sponsor/Bioanalytical — 역시 제약 제조 GMP 범위 밖)
--   ③ 30자 미만 추출 단편 229건(예: "Promised to correct", ".") — 전부 FDA 483 소스.
--      WL 은 블록 최소 470자·MFDS 는 480자로 추출하므로 이 오염은 483 전용이며 WL/MFDS
--      에는 동일 결함이 없다(오탐 없음 확인됨).
-- 합산 최대 755건이 영향받을 수 있으나, 정확한 수치는 이 파일의 (C) 백필 UPDATE 를
-- 실제 프로덕션에서 실행한 결과(영향받은 row count)로 확정된다 — 위 숫자는 사전 추정치다.
--
-- 되돌리는 법:
--   - 개별 오분류 row 복구: `update public.findings set scope_status = 'ok' where finding_id = '<id>';`
--   - 전체 되돌리기(트리거/백필 자체를 무효화하고 싶을 때): 이 파일의 (E) 정책과 (F) RPC 는
--     최신 그대로 두고, `update public.findings set scope_status = 'ok';` 한 번으로 모든
--     플래그를 해제할 수 있다(컬럼/제약/트리거/함수를 drop 할 필요 없음 — 값만 되돌리면
--     게이트·RPC 필터가 자동으로 다시 전량을 노출한다).
--   - 완전 원복(이 마이그레이션 자체를 무효화): (E) 정책에서 `and scope_status = 'ok'` 를
--     제거하고 006 원본 조건으로 되돌리며, (F) 의 5개 함수를 007/008/009 원본 바디로
--     `create or replace` 재적용하면 된다(007/008/009 파일은 이 마이그레이션이 바디를
--     건드리지 않았으므로 여전히 원본 소스가 그 파일들에 남아 있다).
--
-- ★중요: 007_findings_stats_rpc.sql 의 findings_stats/findings_firm_stats, 008_findings_
-- category_matrix.sql 의 findings_category_matrix, 009_findings_translation_bridge.sql 의
-- findings_translation_queue/findings_translation_rows — 이 5개 함수는 이 파일 (F) 에서
-- create or replace 로 재정의되어 **이 파일이 프로덕션 현재 정의를 supersede** 한다. 007/
-- 008/009 파일 자체의 함수 바디는 건드리지 않는다(git 히스토리·서식 보존용 원본으로 남긴다)
-- — 각 파일 상단에 이 사실을 알리는 한 줄 주석만 추가했다. grant(revoke-then-grant) 는
-- 007/008/009 가 이미 anon/authenticated 에 부여했으므로 이 파일에서 다시 선언하지 않는다
-- (create or replace 는 기존 함수의 grant 를 보존한다 — signature 불변이면 grant 도 그대로
-- 유지된다).
--
-- 전제: 002_findings.sql + 006_findings_publish_gate.sql + 007_findings_stats_rpc.sql +
-- 008_findings_category_matrix.sql + 009_findings_translation_bridge.sql 이 먼저 적용되어
-- 있어야 한다.

-- ============================================================================
-- (A) 컬럼 + 제약 추가 — scope_status 기본값 'ok'(전량 무영향 시작점), 허용값 3종.
-- ============================================================================

alter table public.findings
  add column if not exists scope_status text not null default 'ok';

-- 004_findings_taxonomy_v2.sql 과 동일 관례(존재검사 후 추가 — 재실행해도 에러 없음).
-- 여기서는 제약 이름이 고정이므로 pg_constraint 존재검사만으로 충분하다(004 처럼 컬럼
-- 전체를 스캔해 무명 제약을 찾아 drop 할 필요가 없다 — 이 제약은 처음부터 명명 제약이다).
do $$
begin
  if not exists (
    select 1
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace nsp on nsp.oid = rel.relnamespace
    where nsp.nspname = 'public'
      and rel.relname = 'findings'
      and con.conname = 'findings_scope_status_chk'
  ) then
    alter table public.findings
      add constraint findings_scope_status_chk
      check (scope_status in ('ok', 'non_pharma', 'fragment')) not valid;
  end if;
end;
$$;

-- not valid 로 추가했으므로 기존 행은 아직 검증되지 않은 상태다 -- 명시적으로 검증한다.
-- 이 시점에는 모든 행이 컬럼 기본값 'ok' 이므로(위 add column) 항상 통과한다. 재실행해도
-- 에러 없이 다시 스캔만 한다(이미 valid 인 제약을 재검증해도 실패하지 않는다).
alter table public.findings validate constraint findings_scope_status_chk;

-- ============================================================================
-- (B) 분류 함수 — FDA 483 전용 판정 로직. 호출측이 source='FDA 483' 일 때만 사용해야
-- 한다(다른 소스는 항상 'ok' 로 둔다 -- 이 함수 자체는 그 게이트를 강제하지 않으므로
-- 호출측 책임이다. 아래 (D) 트리거와 (C) 백필이 그 게이트를 지킨다).
-- ============================================================================

-- 규칙 순서(먼저 매치하는 조건이 우선): ① 비제약 시설 유형 → 'non_pharma'
-- ② 30자 미만 추출 단편 → 'fragment' ③ 그 외 → 'ok'.
-- non_pharma 정규식(대소문자 무시, ~*): 식품·농업(shell egg/egg manufacturer/cheese/
-- peanut/sprout/pistachio/fruit processor/pet food/animal feed/infant formula/produce
-- manufacturer) + 항공(aircraft) + 농장(\y...\y 단어경계 -- "farm" 이 다른 단어의 일부로
-- 오탐되지 않도록) + 임상시험/GCP(institutional review board/clinical investigator/
-- bioanalytical/^sponsor$ -- sponsor 는 필드 전체가 정확히 "Sponsor" 인 경우만 매치해
-- "Corporate Sponsor Program" 같은 오탐을 막는다).
create or replace function public.grm_classify_483_scope(p_est_type text, p_len integer)
returns text
language sql
immutable
set search_path = public
as $$
  select case
    when coalesce(p_est_type, '') ~* '(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor|pet food|animal feed|infant formula|produce manufacturer|aircraft|\yfarm\y|institutional review board|clinical investigator|bioanalytical|^sponsor$)'
      then 'non_pharma'
    when coalesce(p_len, 0) < 30
      then 'fragment'
    else 'ok'
  end;
$$;

-- ============================================================================
-- (C) 기존 행 백필 -- FDA 483 소스만. raw_signals 에서 establishment_type 을 읽어
-- 분류하고, finding_text 길이로 단편 여부를 함께 판정한다.
-- ============================================================================

update public.findings f
set scope_status = public.grm_classify_483_scope(
  coalesce(nullif(trim((rs.raw_json::jsonb) ->> 'establishment_type'), ''), ''),
  length(f.finding_text)
)
from public.raw_signals rs
where rs.raw_signal_id = f.raw_signal_id
  and f.source = 'FDA 483';

-- ============================================================================
-- (D) 트리거 -- 이후 어떤 경로로 insert 되든(일일 append, 백필 auto-cron 등) FDA 483
-- 행은 자동으로 분류된다. 다른 소스는 전혀 손대지 않는다(NEW.scope_status 를 그대로 둔다
-- -- 컬럼 기본값 'ok' 가 이미 적용된 상태이므로 별도 대입이 필요 없다).
-- SECURITY DEFINER 불필요: findings 에 insert 하는 주체(service_role)는 이미 raw_signals
-- 읽기 권한을 갖고 있다(RLS 를 bypass 하거나, 002 의 전면 차단이 anon/authenticated 만
-- 겨냥하므로 service_role 은 애초에 걸리지 않는다).
-- ============================================================================

create or replace function public.grm_findings_scope_status_trigger()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  v_est_type text;
begin
  if new.source = 'FDA 483' then
    select coalesce(nullif(trim((rs.raw_json::jsonb) ->> 'establishment_type'), ''), '')
      into v_est_type
    from public.raw_signals rs
    where rs.raw_signal_id = new.raw_signal_id;

    if v_est_type is null then
      -- raw_signal 이 같은 트랜잭션 내에서 아직 보이지 않는 등 방어적 예외 상황 --
      -- 명시된 기본값 'ok' 로 둔다(오탐으로 신규 데이터를 숨기지 않는 안전 측 기본값).
      new.scope_status := 'ok';
    else
      new.scope_status := public.grm_classify_483_scope(v_est_type, length(new.finding_text));
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists findings_scope_status_biu on public.findings;

create trigger findings_scope_status_biu
before insert on public.findings
for each row execute function public.grm_findings_scope_status_trigger();

-- ============================================================================
-- (E) 공개 게이트(006) 정책 갱신 -- 기존 조건에 scope_status='ok' 를 AND 로 추가한다.
-- 플래그된 행은 검색/트렌드 등 공개 웹에서 자동으로 사라진다(anon/authenticated SELECT
-- 차단 -- service_role 은 RLS 를 우회하므로 무관).
-- ============================================================================

drop policy if exists findings_public_read on public.findings;
create policy findings_public_read
on public.findings
for select
to anon, authenticated
using (
  (finding_text_ko <> '' or finding_language = 'KO')
  and scope_status = 'ok'
);

-- ============================================================================
-- (F) RPC 갱신 -- 007/008/009 의 5개 함수를 create or replace 로 재정의한다. 각 함수의
-- findings 집계/조회 전부에 scope_status='ok' 필터를 추가했다(findings_translation_rows
-- 는 예외 -- 아래 참조). 그 외 로직·컬럼 목록·순서·시그니처는 007/008/009 원본과 완전히
-- 동일하다(diff 는 오직 scope_status 필터 추가 + findings_stats.totals.documents 신규
-- 키뿐이다). grant 는 재선언하지 않는다(위 헤더 설명 참조 -- signature 불변이므로 007/
-- 008/009 가 이미 부여한 anon/authenticated EXECUTE 가 그대로 유지된다).
-- ============================================================================

-- ※ 2026-07: findings_stats() 의 top_firms 키는 017_findings_stats_firm_key.sql 에서
-- 다시 create or replace 로 재정의됨(firm_name group by -> firm_key group by, 프로덕션
-- 현행 top_firms 정의는 017 참조). 이 파일의 totals/by_agency_category/by_month/
-- by_source/by_evidence 4개 키는 017 이후에도 무변경으로 계속 유효하다.
-- --- 007: findings_stats() -----------------------------------------------
-- totals.documents 신규 -- count(distinct raw_signal_id)(scope_status='ok' 기준, 문서
-- 단위 커버리지). totals.findings/public_findings 도 scope_status='ok' 반영해 재계산.
-- totals.raw_signals 는 findings 테이블과 무관한 별도 원본 카운트라 필터 대상이 아니다
-- (raw_signals 에는 scope_status 컬럼이 없다 -- 그 테이블 자체가 이 마이그레이션의
-- 대상이 아니다).
create or replace function public.findings_stats()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'totals', jsonb_build_object(
      'findings', (
        select count(*) from public.findings where scope_status = 'ok'
      ),
      'public_findings', (
        select count(*) from public.findings
        where scope_status = 'ok'
          and (finding_text_ko <> '' or finding_language = 'KO')
      ),
      'raw_signals', (select count(*) from public.raw_signals),
      'firms', (
        select count(distinct firm_name) from public.findings where scope_status = 'ok'
      ),
      'documents', (
        select count(distinct raw_signal_id) from public.findings where scope_status = 'ok'
      )
    ),
    'by_agency_category', coalesce((
      select jsonb_agg(
        jsonb_build_object('agency', agency, 'category_code', category_code, 'cnt', cnt)
        order by agency, category_code
      )
      from (
        select agency, category_code, count(*) as cnt
        from public.findings
        where scope_status = 'ok'
        group by agency, category_code
      ) t
    ), '[]'::jsonb),
    'by_month', coalesce((
      select jsonb_agg(
        jsonb_build_object('month', month, 'agency', agency, 'cnt', cnt)
        order by month, agency
      )
      from (
        select left(published_date, 7) as month, agency, count(*) as cnt
        from public.findings
        where scope_status = 'ok'
        group by left(published_date, 7), agency
      ) t
    ), '[]'::jsonb),
    'by_source', coalesce((
      select jsonb_agg(
        jsonb_build_object('source', source, 'cnt', cnt)
        order by source
      )
      from (
        select source, count(*) as cnt
        from public.findings
        where scope_status = 'ok'
        group by source
      ) t
    ), '[]'::jsonb),
    'by_evidence', coalesce((
      select jsonb_agg(
        jsonb_build_object('evidence_level', evidence_level, 'cnt', cnt)
        order by evidence_level
      )
      from (
        select evidence_level, count(*) as cnt
        from public.findings
        where scope_status = 'ok'
        group by evidence_level
      ) t
    ), '[]'::jsonb),
    'top_firms', coalesce((
      select jsonb_agg(
        jsonb_build_object('firm_name', firm_name, 'cnt', cnt, 'public_cnt', public_cnt)
        order by cnt desc, firm_name asc
      )
      from (
        select
          firm_name,
          count(*) as cnt,
          count(*) filter (
            where finding_text_ko <> '' or finding_language = 'KO'
          ) as public_cnt
        from public.findings
        where scope_status = 'ok'
        group by firm_name
        order by cnt desc, firm_name asc
        limit 30
      ) t
    ), '[]'::jsonb)
  );
$$;

-- --- 007: findings_firm_stats(p_firm) -------------------------------------
create or replace function public.findings_firm_stats(p_firm text)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'firm_name', p_firm,
    'totals', jsonb_build_object(
      'findings', (
        select count(*) from public.findings
        where firm_name = p_firm and scope_status = 'ok'
      ),
      'public_findings', (
        select count(*) from public.findings
        where firm_name = p_firm
          and scope_status = 'ok'
          and (finding_text_ko <> '' or finding_language = 'KO')
      )
    ),
    'by_category', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'cnt', cnt)
        order by category_code
      )
      from (
        select category_code, count(*) as cnt
        from public.findings
        where firm_name = p_firm and scope_status = 'ok'
        group by category_code
      ) t
    ), '[]'::jsonb),
    'by_month', coalesce((
      select jsonb_agg(
        jsonb_build_object('month', month, 'cnt', cnt)
        order by month
      )
      from (
        select left(published_date, 7) as month, count(*) as cnt
        from public.findings
        where firm_name = p_firm and scope_status = 'ok'
        group by left(published_date, 7)
      ) t
    ), '[]'::jsonb),
    'by_source', coalesce((
      select jsonb_agg(
        jsonb_build_object('source', source, 'cnt', cnt)
        order by source
      )
      from (
        select source, count(*) as cnt
        from public.findings
        where firm_name = p_firm and scope_status = 'ok'
        group by source
      ) t
    ), '[]'::jsonb),
    'first_seen', (
      select min(published_date) from public.findings
      where firm_name = p_firm and scope_status = 'ok'
    ),
    'last_seen', (
      select max(published_date) from public.findings
      where firm_name = p_firm and scope_status = 'ok'
    )
  );
$$;

-- --- 008: findings_category_matrix() --------------------------------------
create or replace function public.findings_category_matrix()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'years', coalesce((
      select jsonb_agg(year order by year)
      from (
        select distinct left(published_date, 4) as year
        from public.findings
        where left(published_date, 4) <> '' and scope_status = 'ok'
      ) t
    ), '[]'::jsonb),
    'cells', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'year', year, 'cnt', cnt)
        order by category_code, year
      )
      from (
        select category_code, left(published_date, 4) as year, count(*) as cnt
        from public.findings
        where left(published_date, 4) <> '' and scope_status = 'ok'
        group by category_code, left(published_date, 4)
      ) t
      where cnt > 0
    ), '[]'::jsonb),
    'category_totals', coalesce((
      select jsonb_agg(
        jsonb_build_object('category_code', category_code, 'cnt', cnt)
        order by cnt desc, category_code
      )
      from (
        select category_code, count(*) as cnt
        from public.findings
        where scope_status = 'ok'
        group by category_code
      ) t
    ), '[]'::jsonb)
  );
$$;

-- --- 009: findings_translation_queue(p_limit) -----------------------------
-- 미번역 큐 + untranslated_total 양쪽에 scope_status='ok' 필터 -- 플래그된 junk 를
-- 번역 예산(사람·LLM 리소스)에 넣지 않기 위함.
create or replace function public.findings_translation_queue(p_limit integer default 200)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'untranslated_total', (
      select count(*) from public.findings
      where coalesce(finding_text_ko, '') = '' and scope_status = 'ok'
    ),
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'finding_id', finding_id,
          'source', source,
          'agency', agency,
          'category_code', category_code,
          'category_label_ko', category_label_ko,
          'published_date', published_date,
          'firm_name', firm_name,
          'finding_text', finding_text,
          'finding_text_ko', finding_text_ko,
          'translation_method', translation_method
        )
        order by published_date desc, finding_id asc
      )
      from (
        select *
        from public.findings
        where coalesce(finding_text_ko, '') = '' and scope_status = 'ok'
        order by published_date desc, finding_id asc
        limit greatest(1, least(coalesce(p_limit, 200), 500))
      ) t
    ), '[]'::jsonb)
  );
$$;

-- --- 009: findings_translation_rows(p_finding_ids) ------------------------
-- 의도적으로 scope_status 필터를 넣지 않는다 -- 이 함수는 --apply 라이브 검증이 특정
-- finding_id 목록을 넘겨 원문 byte 대조에만 쓰므로 scope 여부와 무관하다(시그니처/동작
-- 007/008/009 원본과 완전 동일 -- 009 의 배열 슬라이스 괄호 형태
-- `(coalesce(...))[1:500]` 도 그대로 보존한다).
create or replace function public.findings_translation_rows(p_finding_ids text[])
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'finding_id', finding_id,
      'finding_text', finding_text,
      'finding_text_ko', finding_text_ko
    )
  ), '[]'::jsonb)
  from public.findings
  where finding_id = any((coalesce(p_finding_ids, '{}'::text[]))[1:500]);
$$;

-- 검증(사람 실행용, 프로덕션 SQL Editor):
-- 1) 백필 영향 건수(이 UPDATE 를 다시 실행하면 0건이어야 한다 -- 이미 반영됐으므로):
--    select scope_status, count(*) from public.findings where source = 'FDA 483' group by scope_status order by scope_status;
-- 2) 공개 게이트가 실제로 걸러내는지:
--    set role anon; select count(*) from public.findings where scope_status <> 'ok'; -- 항상 0
--    reset role;
-- 3) totals.documents 가 신규로 채워졌는지:
--    select public.findings_stats() -> 'totals' -> 'documents';
-- 4) 트리거가 신규 483 insert 에 자동 적용되는지는 라이브 배치(다음 daily append)에서
--    scope_status 분포 변화로 관찰한다(이 파일 자체는 오프라인 텍스트 계약 테스트로만
--    검증됨 -- tests/test_findings_scope_purity.py 참조).
