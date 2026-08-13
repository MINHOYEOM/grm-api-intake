-- ============================================================================
-- 059_fda_inspection_stats_freshness.sql — fda_inspection_stats() 에 신선도 2키 추가
--
-- 배경: 058 이 만든 public.fda_inspections 는 2026-08-11 에 **1회성 수동 실행**으로
--   6,417건이 적재됐다. 그런데 화면 고지(trends.js #tr-fda-scope)는 범위
--   (FY2020~FY2026)와 출처만 적고 **날짜를 한 글자도 말하지 않는다**. FDA 는 새 실사
--   등급을 계속 등록하므로, 이대로 두면 반년 뒤에도 화면은 똑같이 "6,417건"이라 말하고
--   **낡았다는 사실이 어디에도 보이지 않는다**.
--   (2026-08-12 프로덕션 실측: count 6,417 · min(ingested_at) 2026-08-11 23:59:03.351328+00
--    · max(ingested_at) 2026-08-11 23:59:11.887371+00 — 폭 8.5초, 즉 단일 배치 1회 적재.
--    max(inspection_end_date) 2026-07-16 · inspection_end_date null 0건.)
--
-- supersede 체인: fda_inspection_stats = 058 → **이 파일(059)**
--
-- ─ 생성 방식(수작업 복사 금지) ──────────────────────────────────────────────
--   이 파일의 함수 본문은 손으로 옮겨 적은 것이 아니라 058 의 정의를 **기계적으로 읽어**
--   앵커 2곳에 삽입해 생성했다(054/056 관례). 생성기는 앵커가 정확히 1회가 아니면
--   중단한다(부분 생성 방지). 생성기는 스크래치패드에 두고 저장소에는 결과 SQL 만 남긴다.
--   그래서 아래 (A) 의 base/totals/by_year/by_country 블록은 058 과 **바이트 동일**이며,
--   tests/test_fda_inspections.py 가 두 파일을 실제로 대조해 그 사실을 고정한다.
--
-- ─ 변경 지점 2곳(둘 다 추가) ───────────────────────────────────────────────
--   ⑴ base CTE 투영에 `inspection_end_date, ingested_at` 추가 — 056 이 `searched` CTE
--      투영에 `f.country_key` 를 더한 것과 같은 형태다. 투영에 없으면 scope 에서 참조할
--      수 없고, 여기 실으면 같은 1회 스캔을 그대로 재사용한다(테이블 재스캔 없음).
--   ⑵ scope 블록 **끝에** 신선도 2키 추가.
--
-- ─ 기존 키 불변(하위호환) ──────────────────────────────────────────────────
--   scope 의 기존 7키(source · project_area · product_type · excluded_project_areas ·
--   fiscal_year_min · fiscal_year_max · unmapped_country_count)와 totals/by_year/
--   by_country 전부 **이름·타입·값·정렬이 058 과 동일**하다. 옛 키를 지우거나 이름을
--   바꾸면 캐시된 구버전 trends.js 가 그 자리를 통째로 잃는다(055 226~231행이 기록한
--   전례 — "이 저장소는 옛 키를 지워 스탯을 잃은 전례가 있다").
--   ★노출 관점: 새로 공개되는 **행은 0건**이다. 이 파일이 더하는 것은 집계 스칼라 2개뿐이고
--   원문·업체 식별 필드는 058 과 동일하게 어떤 경로로도 반환하지 않는다(007/038 안전 계약).
--
-- ─ 시그니처 불변 = PostgREST 404 위험 없음 ─────────────────────────────────
--   `create or replace function` 은 (스키마, 이름, 인자 타입 목록) 으로 함수를 찾는다 —
--   인자를 하나도 늘리지 않았으므로 같은 pg_proc OID(2026-08-12 실측 33546)를 갱신할 뿐
--   새 오버로드를 만들지 않는다. 파라미터를 하나라도 붙였다면 (i) 무인자 호출이
--   PGRST202/"function is not unique" 로 깨지고, (ii) 새 함수는 058 의
--   `revoke all ... from public` 을 물려받지 못해 **PUBLIC EXECUTE 로 열린 채 태어나며**,
--   (iii) 웹은 자동 배포·마이그는 수동이라 그 사이가 장애 창이 된다. 무인자 유지가
--   유일하게 안전한 경로다.
--
-- ─ ★grant/revoke 를 재발행하는 이유(054~057 의 "재부여 불필요" 주석과 다른 선택) ──
--   054~057 은 "create or replace 는 grant 를 보존하므로 재부여 불필요"라는 주석 한 줄로
--   대신했다. 시그니처가 같은 경우 그 말은 맞다(058 이 걸어둔 ACL 이 실제로 살아 있다 —
--   2026-08-12 실측 proacl `{postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,
--   service_role=X/postgres}` 에 PUBLIC 항목이 없다). 그럼에도 여기서는 **재발행한다**:
--   재발행은 멱등이고, 이 함수가 어떤 이유로든 존재하지 않는 DB(058 미적용)에 이 파일이
--   먼저 적용되면 함수는 Postgres 기본값인 **PUBLIC EXECUTE 로 태어나기** 때문이다.
--   "보존된다"에 기대는 대신 결과 상태를 파일이 직접 못 박는 쪽이 fail-closed 다.
--
-- ─ 적용 등가성 실측(2026-08-12, 프로덕션 **읽기 전용**) ────────────────────
--   이 파일의 본문을 함수로 만들지 않고 인라인 질의로 실행해 라이브 응답과 대조했다
--   (036 의 md5 등가성 관례 — 프로덕션에 아무것도 쓰지 않았다):
--     md5(현행 fda_inspection_stats()::text)                    = f2e3238a2bec08bc1c30a4619f72da9a
--     md5((이 파일 본문 결과 #- 신설 2키)::text)                = f2e3238a2bec08bc1c30a4619f72da9a
--   두 값이 같다(같은 질의에서 `old::text = stripped::text` 도 true) = 이 변경은
--   **순수 가산(purely additive)** 이다. 신설 2키 실측값은
--   last_ingested_date_kst='2026-08-12' · latest_inspection_end_date='2026-07-16' 이었다.
--   빈 집합 안전도 같은 세션에서 확인했다(`... where false` 로 두 식을 실행 → 둘 다 null,
--   에러 없음).
--   ★이 md5 는 표 내용이 바뀌면 당연히 달라진다(월간 재수집 후에는 다른 값이다) — 아래
--   검증 절차는 **적용 직전에 스스로 떠 둔 값**과 대조하는 방식이라 낡지 않는다.
--
-- ─ ★이 파일이 답하지 못하는 질문(한계를 숨기지 않는다) ─────────────────────
--   두 키 중 어느 것도 **"수집기가 죽었다"를 말하지 못한다**. 신규 실사가 0건인 달과
--   3개월째 수집기가 안 돈 달이 화면에서 똑같이 보인다. 그 신호는 이 RPC 가 아니라
--   `.github/workflows/grm-fda-inspections-backfill.yml` 의 월간 스케줄이 실패할 때
--   여는 운영 이슈(+red run)가 담당한다. 그 층을 RPC 로 옮기려면 표에 refreshed_at
--   컬럼을 더하고 수집기 payload 에 실어야 하는데(그래야 merge-duplicates 가 매 실행
--   갱신한다), 그건 **웹 자동배포와 수동 마이그레이션 사이의 장애 창**을 새로 여는
--   변경이라(수집기가 먼저 배포되면 존재하지 않는 컬럼을 POST 해 월간 적재가 전량
--   실패한다) 이 파일의 범위에서 의도적으로 뺐다.
--
-- 전제: 058_fda_inspections.sql 이 먼저 적용되어 있어야 한다(표와 이 함수의 원본).
-- ============================================================================
-- ★004/009 함정 해당 없음(055/057/058 관례 계승): plpgsql DO 블록·선언 변수·배열
-- 슬라이스 전혀 없음 — language sql 순수 정의 하나뿐이다(트리거·백필 없음. 표 DDL 도
-- 없다 — 이 파일은 함수 하나만 갱신한다).
-- ============================================================================

-- ============================================================================
-- (A) public.fda_inspection_stats() — 058 정의 + 신선도 2키(위 "변경 지점 2곳").
-- ============================================================================

create or replace function public.fda_inspection_stats()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with base as (
    select inspection_id, classification_code, fiscal_year, country_key, country_name,
           inspection_end_date, ingested_at
    from public.fda_inspections
  )
  select jsonb_build_object(
    'scope', jsonb_build_object(
      'source', 'FDA Data Dashboard API — inspections_classifications',
      -- project_area/product_type 은 (A)의 CHECK 제약이 표 전체에 이미 강제하므로
      -- 상수로 echo 한다(제약이 깨지면 애초에 이 값의 행이 표에 존재할 수 없다).
      'project_area', 'Drug Quality Assurance',
      'product_type', 'Drugs',
      -- 적재 대상에서 뺀 모집단 -- 정성적 고지일 뿐 이 표에서 라이브로 집계한 수치가
      -- 아니다(그 행들은 애초에 이 표에 없다 -- 헤더의 2026-08-11 실측: BIMO 4,651 /
      -- Unapproved and Misbranded Drugs 31 / OTC Drug Evaluation 5, ProductType=
      -- Drugs 전체 11,104건 중 GMP 6,417건을 뺀 나머지).
      'excluded_project_areas', jsonb_build_array(
        'Bioresearch Monitoring',
        'Unapproved and Misbranded Drugs',
        'Over-the-Counter Drug Evaluation'
      ),
      'fiscal_year_min', (select min(fiscal_year) from base),
      'fiscal_year_max', (select max(fiscal_year) from base),
      -- 055 의 excluded_unknown_country/findings_country_unmapped() 와 같은 취지 --
      -- 매핑 밖으로 새는 country_name 이 있으면 조용히 묻지 않고 센다.
      'unmapped_country_count', (select count(*) from base where country_key = ''),
      -- ── 059 신설: 신선도 2키 ────────────────────────────────────────────────
      -- 두 키는 **서로 다른 질문에 답한다**. 한 키로 뭉치면 이 저장소가 반복해 겪은
      -- "계기판 합산 오진"(원인이 다른 사건을 한 카운터에 합침)이 그대로 재현되므로
      -- 이름으로 갈라 둔다.
      --
      -- ① last_ingested_date_kst — "우리가 언제 새로고침했나".
      --    ★엄밀히는 "이 표에 **새 행이 마지막으로 들어온** 날"이다(키 이름과 화면
      --    문구를 둘 다 그렇게 적었다). 수집기의 upsert payload(collect_fda_inspections
      --    .normalize_row)에 ingested_at 이 실리지 않아 PostgREST
      --    resolution=merge-duplicates 가 **기존 행의 ingested_at 은 갱신하지 않기**
      --    때문이다 — 신규 실사가 0건인 달에는 재수집이 성공해도 이 값이 안 움직인다.
      --    "마지막 갱신 시각"이라고 적으면 그 문장이 시간이 지나며 거짓이 된다.
      --    ★KST 로 못 박는다 — timestamptz 를 jsonb 에 그냥 넣으면 문자열이 세션
      --    TimeZone GUC 에 의존한다(PostgREST anon 요청의 GUC 는 우리가 통제하지
      --    않는다). `at time zone 'Asia/Seoul'` 명시 변환은 GUC 와 무관하게 결정론이고,
      --    읽는 사람이 한국 실무자이므로 프레임도 KST 가 맞다. 프레임을 키 이름에
      --    적어 두면 다음 사람이 UTC 로 오독할 수 없다.
      --
      -- ② latest_inspection_end_date — "내용이 얼마나 최신인가"(FDA 원문 날짜 그대로).
      --    ★오른쪽 절단(right-censored) 꼬리의 최댓값이라 **완결성을 과대표시한다** —
      --    2026-08-12 실측 월별 건수: 2025-08 107 / 09 160 / 10 83 / 11 91 / 12 60 /
      --    2026-01 54 / 02 85 / 03 75 / 04 74 / 05 57 / 06 28 / 07 6. FDA 등급 확정·
      --    공개 지연 때문이며, 그래서 화면 문구도 "여기까지 완결"이 아니라 "담긴 실사
      --    중 가장 최근 종료일"로 좁게 적는다(trends.js). 서버가 "어느 달까지 완전한가"를
      --    임계로 판정하지 않는다 — 임계는 반드시 낡는다(007/038/058 계약: 서버는 센다).
      --
      -- ★0건 안전: 빈 집합의 max() 는 null 이고 null 은 jsonb null 로 나간다. 없는 날짜를
      --   지어내지 않는다(coalesce 로 now()/오늘을 채우지 않는다) — 화면은 값이 null 이면
      --   그 항목만 빼고 나머지를 정상 표시한다.
      'last_ingested_date_kst',
        (select (max(ingested_at) at time zone 'Asia/Seoul')::date from base),
      'latest_inspection_end_date', (select max(inspection_end_date) from base)
    ),
    'totals', jsonb_build_object(
      'inspections', (select count(*) from base),
      'nai', (select count(*) from base where classification_code = 'NAI'),
      'vai', (select count(*) from base where classification_code = 'VAI'),
      'oai', (select count(*) from base where classification_code = 'OAI')
    ),
    'by_year', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'fiscal_year', t.fiscal_year,
          'nai', t.nai, 'vai', t.vai, 'oai', t.oai
        )
        order by t.fiscal_year
      )
      from (
        select
          fiscal_year,
          count(*) filter (where classification_code = 'NAI')::int as nai,
          count(*) filter (where classification_code = 'VAI')::int as vai,
          count(*) filter (where classification_code = 'OAI')::int as oai
        from base
        group by fiscal_year
      ) t
    ), '[]'::jsonb),
    'by_country', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'code',    g.country_key,
          'country', case when g.country_key = '' then null else rep.country_name end,
          'nai', g.nai, 'vai', g.vai, 'oai', g.oai, 'total', g.total
        )
        order by g.total desc, g.country_key
      )
      from (
        select
          country_key,
          count(*) filter (where classification_code = 'NAI')::int as nai,
          count(*) filter (where classification_code = 'VAI')::int as vai,
          count(*) filter (where classification_code = 'OAI')::int as oai,
          count(*)::int as total
        from base
        group by country_key
      ) g
      left join lateral (
        -- 대표 표기: country_key 그룹 내 건수가 가장 많은 country_name 원문(동률이면
        -- 사전순 -- 013/055 결정론 관례와 동일). country_key='' 그룹에도 계산은 되지만
        -- 위 jsonb_build_object 의 case 가 그 값을 null 로 덮어써 출력하지 않는다.
        select b2.country_name
        from base b2
        where b2.country_key = g.country_key
        group by b2.country_name
        order by count(*) desc, b2.country_name asc
        limit 1
      ) rep on true
    ), '[]'::jsonb)
  );
$$;

-- ============================================================================
-- (B) 권한 — 재발행(멱등). 근거는 헤더의 "grant/revoke 를 재발행하는 이유" 절.
--   revoke 가 grant 보다 **먼저** 와야 한다(058 과 같은 순서 — 순서가 뒤집히면 PUBLIC
--   회수가 방금 준 권한까지 되돌린다).
-- ============================================================================

revoke all on function public.fda_inspection_stats() from public;
grant execute on function public.fda_inspection_stats() to anon, authenticated;

-- 검증(사람 실행용, 프로덕션 SQL Editor -- 컨트롤 타워 라이브 dry-run):
-- ★0) **적용 직전에 먼저** 떠 둔다(적용 후에는 다시 뜰 수 없다 -- 036 의 md5 등가성 관례):
--    select md5(public.fda_inspection_stats()::text);   -- 이 값을 적어 둔다
--
-- (여기서 이 파일을 적용한다)
--
-- 1) 순수 가산 증명 -- 신설 2키만 지운 결과가 적용 전과 **바이트 동일**해야 한다:
--    select md5((public.fda_inspection_stats()
--                  #- '{scope,last_ingested_date_kst}'
--                  #- '{scope,latest_inspection_end_date}')::text);
--    -- 0) 에서 적어 둔 값과 같아야 한다(같으면 기존 키 이름·타입·값·정렬 전부 불변).
--    -- ★2026-08-12 읽기 전용 사전 실측에서는 양쪽 다 f2e3238a2bec08bc1c30a4619f72da9a 였다.
-- 2) 신설 2키가 실제로 붙었는지 + 값의 모양:
--    select public.fda_inspection_stats() -> 'scope' ->> 'last_ingested_date_kst',
--           public.fda_inspection_stats() -> 'scope' ->> 'latest_inspection_end_date';
--    -- 2026-08-12 기준 '2026-08-12' / '2026-07-16'. 월간 재수집 이후에는 당연히 달라진다.
-- 3) 두 키가 원천과 일치하는지(RPC 를 안 거치고 표에서 직접 재현):
--    select (max(ingested_at) at time zone 'Asia/Seoul')::date, max(inspection_end_date)
--    from public.fda_inspections;   -- 2) 와 두 값이 같아야 한다
-- 4) 오버로드가 생기지 않았는지(무인자 하나만 남아야 한다 -- 생겼다면 PostgREST 404 위험):
--    select oid, pg_get_function_identity_arguments(oid) as args, prosecdef
--    from pg_proc where proname = 'fda_inspection_stats';
--    -- 1행·args 빈 문자열·prosecdef=t. oid 는 058 적용본과 같아야 한다(2026-08-12 실측 33546).
-- 5) 권한이 의도대로인지(PUBLIC 없음 + anon/authenticated 실행 가능):
--    select proacl from pg_proc where proname = 'fda_inspection_stats';
--    -- '=X/postgres'(PUBLIC) 항목이 없어야 하고 anon=X / authenticated=X 가 있어야 한다.
-- 6) 0건 안전(빈 표에서 null 이 나오고 터지지 않는지) -- 이 표를 비울 수는 없으므로
--    같은 식을 빈 집합에 대해 직접 확인한다:
--    select (max(ingested_at) at time zone 'Asia/Seoul')::date, max(inspection_end_date)
--    from public.fda_inspections where false;   -- 두 값 모두 null(에러 아님)
