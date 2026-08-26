-- ============================================================================
-- 061_fda_inspection_stats_inspection_date.sql
--   fda_inspection_stats() 에 **실사 종료일 축**(by_quarter) + **한국 슬라이스**(korea)
--   두 키를 추가한다. 059 를 같은 무인자 시그니처로 supersede 하며, **기존 4키
--   (scope/totals/by_year/by_country)는 한 글자도 바뀌지 않는다.**
--
-- ── 왜 ───────────────────────────────────────────────────────────────────────
-- 058 이 `inspection_end_date` 를 6,417건 **전부** 적재해 두고도(결측 0건 ·
-- 2019-10-01~2026-07-16, 2026-08-26 실측) 집계 RPC 는 `fiscal_year` 만 노출했다.
-- 이 저장소에서 **실사가 실제로 일어난 날**을 축으로 쓸 수 있는 데이터는 이 표가
-- 유일하다 — findings 에는 실사일 컬럼 자체가 없고(원문이 주지 않는다) 날짜는
-- `published_date`(문서 공개일) 하나뿐이다. 그래서 "최근 실사 경향"이라는 질문에
-- 답할 수 있는 유일한 축이 화면 밖에 있었다.
--
-- 그 축이 만드는 차이는 장식이 아니다. 같은 데이터를 회계연도로 보면 OAI 비율이
-- 9.4 → 22 → 16 → 12 → 14 → 16 → 15% 로 크게 출렁이는데, 실사 종료일 분기로 다시
-- 재면 최근 2년이 **16~17% 로 안정**이다(2026-08-26 실측). FY 경계와 백필 편중이
-- 만든 착시였고, 화면은 지금 그 착시를 그리고 있었다.
--
-- 함께 노출하는 `citations_posted` 는 058 이 적재해 두고 아무도 쓰지 않던 두 번째
-- 축이다(`posted_citations` — 전수 6,417건이 'Yes'/'No' 2종). "실사 뒤 지적서가 실제로
-- 공개됐나"이자, 우리가 483 본문을 확보할 수 있는 모집단의 상한이다.
--
-- ── 안전 계약(059 계승, 불가침) ───────────────────────────────────────────────
-- ★**무인자 시그니처 유지** — 파라미터를 하나라도 붙이면 새 오버로드가 생겨 기존
--   무인자 호출이 PostgREST 404 가 되고, 새 함수는 058 의 `revoke ... from public`
--   을 물려받지 못해 PUBLIC EXECUTE 로 태어난다(059 헤더의 근거 그대로).
-- ★**카운트·서지 메타만** 반환한다 — 원문 텍스트·URL 은 어떤 경로로도 나가지 않는다.
-- ★**완결성 판정을 서버가 하지 않는다** — 최근 분기는 FDA 등급 확정·공개 지연으로
--   오른쪽 절단이지만, "몇 분기 전까지 완전한가"를 서버 임계로 박으면 그 임계는
--   반드시 낡는다(007/038/058/059 공통: 서버는 센다). 화면이 059 가 이미 내보내는
--   `scope.latest_inspection_end_date` 라는 **데이터의 전선**에서 파생해 표시한다.
-- ★**순수 가산 증명** — 이 파일은 059 의 함수 정의를 손으로 옮기지 않고 기계적으로
--   읽어 앵커 2곳에만 삽입해 생성했다(054/056/059 와 같은 방법론). 적용 후 응답에서
--   신설 2키를 제거하면 적용 전 응답과 md5 가 같아야 한다 —
--   `tests/test_fda_inspection_stats_061.py` 가 그 대조를 고정한다.
--
-- 전제: 058(표+RPC) · 059(신선도 2키) 적용 완료.
-- ★이 파일이 적용된 뒤에는 **프로덕션 현행 정의의 정본이 061 이다**(059 는 원복용
--   원본으로 남긴다 — 055/058 이 같은 관례를 쓴다).
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
           inspection_end_date, ingested_at,
           -- [061] posted_citations — 058 이 적재해 두고 어디서도 쓰지 않던 축.
           -- 실측(2026-08-26 전수): 6,417건 전부 채워져 있고 값은 'Yes'/'No' 2종뿐이다.
           -- "실사 뒤 지적서가 실제로 공개됐나"를 뜻하며, 우리가 483 본문을 확보할 수
           -- 있는 모집단의 크기이기도 하다(= 이 사이트가 볼 수 있는 것의 상한).
           posted_citations
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
    ), '[]'::jsonb),
    -- ══ [061 신설 ①] by_quarter — **실사 종료일** 기준 분기 집계 ═══════════════
    -- ★왜 이 키가 필요한가: 이 표에는 `inspection_end_date` 가 6,417건 **전부** 채워져
    --   있는데(결측 0 · 2019-10-01~2026-07-16 실측) 화면에 나가는 시간 축은 회계연도
    --   하나뿐이었다. 그 결과 같은 데이터가 FY 축에서 OAI 9.4→22→16→12→14→16→15% 로
    --   크게 출렁이는 것처럼 보였는데, 실사일 축으로 다시 재면 최근 2년이 16~17% 로
    --   **안정**이다(2026-08-26 실측: 2024-Q3 17.6 / Q4 15.8 / 2025-Q1 16.2 / Q2 16.9 /
    --   Q3 16.5 / Q4 16.7 / 2026-Q1 17.3). FY 경계와 백필 편중이 만든 착시였다.
    --   ★이 사이트에서 **실사가 실제로 일어난 날**을 축으로 쓸 수 있는 데이터는 이
    --   표가 유일하다 — findings 에는 실사일 컬럼 자체가 없다(원문이 주지 않는다).
    --
    -- ★완결성 판정을 **서버가 하지 않는다**(007/038/058/059 공통 계약: 서버는 센다).
    --   최근 분기는 FDA 등급 확정·공개 지연으로 오른쪽 절단(right-censored)이지만,
    --   "몇 분기 전까지 완전한가"를 서버 임계로 박으면 그 임계는 반드시 낡는다.
    --   대신 scope.latest_inspection_end_date(059)를 이미 내보내고 있으므로 화면이
    --   **데이터의 전선(frontier)에서 파생해** 미완 분기를 표시한다(trends.js).
    'by_quarter', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'quarter', q.q,
          'quarter_end', q.q_end,
          'nai', q.nai, 'vai', q.vai, 'oai', q.oai, 'total', q.total,
          'citations_posted', q.citations_posted
        )
        order by q.q
      )
      from (
        select
          to_char(date_trunc('quarter', inspection_end_date), 'YYYY-"Q"Q') as q,
          -- 분기 마지막 날 — 화면이 '이 분기가 데이터 전선을 넘었는가'를 판정하는 근거.
          (date_trunc('quarter', inspection_end_date) + interval '3 months - 1 day')::date as q_end,
          count(*) filter (where classification_code = 'NAI')::int as nai,
          count(*) filter (where classification_code = 'VAI')::int as vai,
          count(*) filter (where classification_code = 'OAI')::int as oai,
          count(*)::int as total,
          count(*) filter (where posted_citations = 'Yes')::int as citations_posted
        from base
        where inspection_end_date is not null
        group by date_trunc('quarter', inspection_end_date)
      ) q
    ), '[]'::jsonb),
    -- ══ [061 신설 ②] korea — 한국 소재 제조소 연도별 슬라이스 ═══════════════════
    -- ★왜 국가 하나를 키로 박는가(일반화하지 않는가): 이 사이트의 독자는 국내 제약
    --   실무자이고, by_country 는 이미 "한국은 목록 밖이어도 따로 표시"를 화면 계약으로
    --   갖고 있다(058/trends.js). KR 은 85건뿐이라 연도별로 갈라도 응답이 거의 안 늘고,
    --   국가×연도 전체 교차표는 62개국 × 8년이라 쓰지도 않을 부피만 커진다.
    --   ★다른 나라에 같은 뷰가 필요해지면 **키를 하나 더 박지 말고** 파라미터 있는
    --   별도 RPC 를 만들어라 — 무인자 시그니처는 059 헤더의 근거대로 불가침이다
    --   (파라미터를 붙이면 새 오버로드가 생겨 기존 무인자 호출이 404 가 된다).
    -- ★연도는 회계연도가 아니라 **달력연도**다(실사 종료일 기준) — 국내 독자에게
    --   FY2024 는 즉시 읽히지 않고, 여기서 답하려는 질문이 "작년에 몇 곳이 받았나"다.
    -- ★firms 는 고유 사업장 수다. 실측(2026-08-26)에서 연도별 실사 수와 고유 사업장
    --   수가 **모든 해에 같았다** — 같은 해에 두 번 실사받은 국내 사업장이 없었다는
    --   뜻이고, 그 사실 자체가 정보라 세어서 내보낸다(화면이 판단하게 둔다).
    'korea', jsonb_build_object(
      'country_key', 'KR',
      'totals', jsonb_build_object(
        'inspections', (select count(*) from base where country_key = 'KR'),
        'nai', (select count(*) from base where country_key = 'KR' and classification_code = 'NAI'),
        'vai', (select count(*) from base where country_key = 'KR' and classification_code = 'VAI'),
        'oai', (select count(*) from base where country_key = 'KR' and classification_code = 'OAI'),
        'firms', (select count(distinct legal_name) from public.fda_inspections where country_key = 'KR')
      ),
      'by_year', coalesce((
        select jsonb_agg(
          jsonb_build_object(
            'year', k.y, 'total', k.total,
            'nai', k.nai, 'vai', k.vai, 'oai', k.oai, 'firms', k.firms
          )
          order by k.y
        )
        from (
          select
            extract(year from i.inspection_end_date)::int as y,
            count(*) filter (where i.classification_code = 'NAI')::int as nai,
            count(*) filter (where i.classification_code = 'VAI')::int as vai,
            count(*) filter (where i.classification_code = 'OAI')::int as oai,
            count(*)::int as total,
            count(distinct i.legal_name)::int as firms
          from public.fda_inspections i
          where i.country_key = 'KR' and i.inspection_end_date is not null
          group by extract(year from i.inspection_end_date)
        ) k
      ), '[]'::jsonb)
    )
  );
$$;

-- ============================================================================
-- (B) 권한 — 재발행(멱등). create or replace 는 grant 를 보존하지만, 이 함수가 어떤
--   경로로든 drop 후 재생성되면 058 의 revoke 가 사라진 채로 살아날 수 있다.
--   revoke 가 grant 보다 **먼저** 와야 한다(058/059 와 같은 순서 — 뒤집히면 PUBLIC
--   EXECUTE 가 남는다).
-- ============================================================================
revoke all on function public.fda_inspection_stats() from public;
grant execute on function public.fda_inspection_stats() to anon, authenticated;
