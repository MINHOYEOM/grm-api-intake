-- ============================================================================
-- 055_findings_country_key.sql — findings 국가 정규화(country_key) + zone RPC 재작성
--
-- 배경(실측, 2026-08-11): public.findings.site_country 는 자유 텍스트다. distinct
--   85종(빈값 제외) 이고, 같은 나라가 영/한/전체대문자/정식국호로 최대 5중 분열돼
--   있다(한국 5종: 대한민국/Republic of Korea/South Korea/The Republic of Korea/Korea,
--   미국 4종: United States/USA/미국/United States of America (USA), 중국 3종 등).
--   전체 16,986행 중 빈값이 4,651행(27.4%) — 수집기 5갈래가 제각각이기 때문이다
--   (FDA 483=영문 verbatim, FDA 경고서한=아예 안 채움, MFDS GMP실사=한국어 verbatim,
--   MFDS 회수/행정처분/도메스틱=아예 안 채움, EU GMP NCR=영문 정식, MHRA=주소 마지막
--   세그먼트 휴리스틱 — 전체대문자 변종의 원인).
--
--   이 결함의 유일한 소비처는 038_findings_zone_category.sql 의 findings_zone_category()
--   RPC 다. 038 은 `site_country in ('United States', 'USA')` 문자열 비교로 us/foreign/
--   unknown 을 가르는데, 038 자신의 헤더 주석이 "한국도 3종으로 갈려 있으나 이 함수의
--   US/FOREIGN 이분에는 영향이 없다"고 이미 적어뒀다 — 즉 US/FOREIGN 이분은 우연히
--   두 변종만 있어서 살아남았을 뿐, top_countries(해외 국가 구성)는 `group by
--   site_country` 라 변종이 서로 다른 나라로 흩어진다(예: 해외 인도 표본이 "India" 로만
--   집계되면 다행이지만, 향후 다른 소스가 "인도" 를 채우면 같은 나라가 두 행으로 갈린다).
--
-- 처방(013_findings_firm_key.sql 의 4단 골조를 그대로 계승 — SQL IMMUTABLE 정규화
--   함수 + Python 파리티 함수 + STORED GENERATED 컬럼(소급 자동) + 파리티 테스트. 다른
--   점은 정규화 대상이 텍스트가 아니라 코드(ISO 3166-1 alpha-2)라는 것뿐이다):
--   (A) public.grm_normalize_country(p_raw text) — 매핑 정본. 아래 (A) 참고.
--   (B) findings.country_key — STORED GENERATED 컬럼. 기존 16,986행 자동 소급.
--   (C) public.findings_zone_category() 재작성(038 supersede) — us/foreign/unknown
--       판정과 top_countries 집계를 country_key 축으로 바꾼다.
--   (D) public.findings_country_unmapped() 신설 — 매핑 밖으로 새는 변종을 관측한다.
--
-- supersede 체인: findings_zone_category = 038 → **이 파일(055)**.
--
-- ============================================================================
-- ★004/009 함정 해당 없음(013 관례 계승): 이 파일은 plpgsql DO 블록/선언 변수를 전혀
-- 쓰지 않는다((A)(C)(D) 모두 language sql 순수 함수다). 배열 슬라이스도 없다.
-- ============================================================================
--
-- 전제: 002_findings.sql(findings.site_country 컬럼) + 010_findings_scope_purity.sql
-- (scope_status) + 038_findings_zone_category.sql(supersede 대상 원본) 이 먼저 적용되어
-- 있어야 한다. 013(firm_key)·054(문서 축)의 기존 함수는 이 파일이 건드리지 않는다.
-- ============================================================================

-- ============================================================================
-- (A) public.grm_normalize_country(p_raw text) — ISO 3166-1 alpha-2 코드 정규화 함수.
--   generated column((B)) 표현식에 쓰이므로 반드시 IMMUTABLE. NULL 허용(STRICT 아님) —
--   coalesce(p_raw, '') 로 내부 처리하므로 NULL 입력도 에러 없이 '' 를 반환한다.
--
--   입력 정리: trim → 내부 연속 공백 1칸 축약 → 소문자 비교(대소문자 무시).
--   매핑에 없거나 빈 입력이면 빈 문자열 `''` 을 반환한다(NULL 이 아니다) — 추측 금지
--   규율: 모르는 나라를 임의 코드로 찍지 않는다. 이 함수가 반환하는 ''은 "미확인"이지
--   "국가 없음"이 아니다(소비 측 findings_zone_category() 가 이를 'unknown' zone 으로
--   따로 취급한다).
--
--   매핑 정본(왼쪽=반환 코드, 오른쪽 각 when 절=인식할 원문 변종 하나 — 소문자로 이미
--   접혀 있는 값과 비교하므로 원문의 대소문자 변종(예: China/CHINA)은 소문자 접힘
--   단계에서 자동으로 같은 branch 로 합쳐진다. 즉 아래 when 목록에 "CHINA" 를 별도로
--   적지 않아도 된다 — 이미 'china' 하나로 커버된다):
--
--   US: United States | USA | 미국 | United States of America (USA)
--   PR: 푸에르토리코  — ★미국령. 아래 (C) zone 판정에서만 US 로 합산한다(FDA 관할 미국령
--       이고 실제 제약 제조 거점이라 "해외 실사" 로 분류하면 오독을 낳는다). country_key
--       자체는 US 와 분리된 별도 코드로 유지한다 — 합산 판단의 책임은 이 함수가 아니라
--       소비 측(C)에 있다.
--   KR: 대한민국 | Republic of Korea | South Korea | The Republic of Korea | Korea
--   IN: India | 인도            CN: China | 중국           JP: Japan | 일본
--   DE: Germany | 독일          CA: Canada | 캐나다        FR: France | 프랑스
--   GB: United Kingdom | 영국   IS: Iceland                IT: Italy | 이탈리아
--   MY: Malaysia                ES: Spain | 스페인         BE: Belgium | 벨기에
--   HU: Hungary                 TW: Taiwan | 대만          CH: Switzerland
--   CY: Cyprus | 사이프러스     AU: Australia | 호주       IE: Ireland | 아일랜드
--   SE: Sweden | 스웨덴         JO: Jordan                 GR: Greece | 그리스
--   DK: Denmark | 덴마크        NL: Netherlands | 네덜란드 MX: Mexico | 멕시코
--   CZ: Czechia                 LT: Lithuania              PL: Poland
--   CL: Chile                   AT: Austria | 오스트리아   RO: Romania | 루마니아
--   ZA: South Africa | 남아프리카 공화국   BD: Bangladesh   ID: Indonesia | 인도네시아
--   LB: Lebanon                 PT: Portugal | 포르투갈    SK: Slovakia
--   LK: Sri Lanka               TR: Turkey | Türkiye       NO: 노르웨이
--   FI: 핀란드                  VN: Vietnam | 베트남       BY: 벨라루스
--   SI: 슬로베니아              IL: 이스라엘
--
--   이 SQL 함수 본문이 정본이며, grm_findings.py 의 normalize_country() 가 파이썬
--   복제본이다 — 두 구현은 반드시 동일 결과를 내야 하고, 그 파리티가 유일한 진짜
--   계약이다(013 관례와 동형). 파리티는 tests/test_findings_country_key.py 가 SQL 본문의
--   when/then 쌍을 파싱해 파이썬 사전과 집합 대조로 고정한다.
--
--   ★사전은 반드시 낡는다 — web/assets/trends.js 의 COUNTRY_LABELS_KO(23종 고정)가
--   이미 47종 매핑 정본보다 좁아 47-23=24종이 화면 표시 매핑 밖으로 새고 있었다(표시는
--   영문 원문 폴백이라 화면이 비지는 않는다 — countryLabelKo() 참고). 이 함수의 매핑도
--   같은 방식으로 낡을 것이므로 (D) findings_country_unmapped() 로 새는 변종을 관측한다.
-- ============================================================================

create or replace function public.grm_normalize_country(p_raw text)
returns text
language sql
immutable
set search_path = public
as $$
  select case lower(regexp_replace(trim(coalesce(p_raw, '')), '\s+', ' ', 'g'))
    -- US -----------------------------------------------------------------
    when 'united states'                  then 'US'
    when 'usa'                             then 'US'
    when '미국'                            then 'US'
    when 'united states of america (usa)' then 'US'
    -- PR(미국령 -- 코드는 별도, 합산은 소비 측 책임) --------------------------
    when '푸에르토리코'                     then 'PR'
    -- KR -----------------------------------------------------------------
    when '대한민국'                        then 'KR'
    when 'republic of korea'               then 'KR'
    when 'south korea'                     then 'KR'
    when 'the republic of korea'           then 'KR'
    when 'korea'                           then 'KR'
    -- IN / CN / JP / DE / CA / FR --------------------------------------
    when 'india'                           then 'IN'
    when '인도'                            then 'IN'
    when 'china'                           then 'CN'
    when '중국'                            then 'CN'
    when 'japan'                           then 'JP'
    when '일본'                            then 'JP'
    when 'germany'                         then 'DE'
    when '독일'                            then 'DE'
    when 'canada'                          then 'CA'
    when '캐나다'                          then 'CA'
    when 'france'                          then 'FR'
    when '프랑스'                          then 'FR'
    -- GB / IS / IT / MY / ES / BE / HU -----------------------------------
    when 'united kingdom'                  then 'GB'
    when '영국'                            then 'GB'
    when 'iceland'                         then 'IS'
    when 'italy'                           then 'IT'
    when '이탈리아'                        then 'IT'
    when 'malaysia'                        then 'MY'
    when 'spain'                           then 'ES'
    when '스페인'                          then 'ES'
    when 'belgium'                         then 'BE'
    when '벨기에'                          then 'BE'
    when 'hungary'                         then 'HU'
    -- TW / CH / CY / AU / IE / SE / JO ------------------------------------
    when 'taiwan'                          then 'TW'
    when '대만'                            then 'TW'
    when 'switzerland'                     then 'CH'
    when 'cyprus'                          then 'CY'
    when '사이프러스'                      then 'CY'
    when 'australia'                       then 'AU'
    when '호주'                            then 'AU'
    when 'ireland'                         then 'IE'
    when '아일랜드'                        then 'IE'
    when 'sweden'                          then 'SE'
    when '스웨덴'                          then 'SE'
    when 'jordan'                          then 'JO'
    -- GR / DK / NL / MX / CZ / LT / PL / CL --------------------------------
    when 'greece'                          then 'GR'
    when '그리스'                          then 'GR'
    when 'denmark'                         then 'DK'
    when '덴마크'                          then 'DK'
    when 'netherlands'                     then 'NL'
    when '네덜란드'                        then 'NL'
    when 'mexico'                          then 'MX'
    when '멕시코'                          then 'MX'
    when 'czechia'                         then 'CZ'
    when 'lithuania'                       then 'LT'
    when 'poland'                          then 'PL'
    when 'chile'                           then 'CL'
    -- AT / RO / ZA / BD / ID / LB / PT / SK / LK / TR ----------------------
    when 'austria'                         then 'AT'
    when '오스트리아'                      then 'AT'
    when 'romania'                         then 'RO'
    when '루마니아'                        then 'RO'
    when 'south africa'                    then 'ZA'
    when '남아프리카 공화국'               then 'ZA'
    when 'bangladesh'                      then 'BD'
    when 'indonesia'                       then 'ID'
    when '인도네시아'                      then 'ID'
    when 'lebanon'                         then 'LB'
    when 'portugal'                        then 'PT'
    when '포르투갈'                        then 'PT'
    when 'slovakia'                        then 'SK'
    when 'sri lanka'                       then 'LK'
    when 'turkey'                          then 'TR'
    when 'türkiye'                         then 'TR'
    -- NO / FI / VN / BY / SI / IL -----------------------------------------
    when '노르웨이'                        then 'NO'
    when '핀란드'                          then 'FI'
    when 'vietnam'                         then 'VN'
    when '베트남'                          then 'VN'
    when '벨라루스'                        then 'BY'
    when '슬로베니아'                      then 'SI'
    when '이스라엘'                        then 'IL'
    else ''
  end;
$$;

-- ============================================================================
-- (B) findings.country_key — STORED GENERATED 컬럼(013 firm_key 와 동형). 트리거·
--   백필 스크립트가 필요 없다: 컬럼 추가 시점에 기존 16,986행이 자동 재계산되고,
--   이후 모든 INSERT/UPDATE(site_country 변경 포함)에서 자동으로 다시 계산된다.
-- ============================================================================

alter table public.findings
  add column if not exists country_key text generated always as (
    public.grm_normalize_country(site_country)
  ) stored;

create index if not exists findings_country_key_idx
  on public.findings (country_key);

-- ============================================================================
-- (C) public.findings_zone_category() 재작성 — 038 을 supersede한다. (B) 컬럼만 추가
--   하고 이 RPC 를 안 고치면 변종 분산이 그대로 남는다(038 이 site_country 문자열을
--   직접 비교하기 때문) — 그래서 컬럼 추가와 RPC 재작성을 반드시 같은 파일에서 함께
--   처리한다.
--
--   변경 3곳(038 대비):
--   ① zone 판정을 `site_country in ('United States', 'USA')` 문자열 비교에서
--      `country_key in ('US', 'PR')` 로 바꾼다(PR 합산 근거는 (A) 주석 참고).
--      country_key = '' 는 'unknown' — 038 의 "빈 문자열만 unknown" 에서 "매핑 밖 변종도
--      unknown" 으로 조건이 넓어진다(정확히 (A)의 계약과 일치 — 미확인은 미확인이다).
--   ② totals.foreign.countries 를 `count(distinct site_country)` 에서
--      `count(distinct country_key)` 로 바꾼다 — 문자열 변종이 서로 다른 나라로 잡히는
--      결함을 여기서도 없앤다(예: 향후 인도 표본에 "인도" 와 "India" 가 섞여도 1개국).
--   ③ top_countries 를 `group by site_country` 에서 `group by country_key` 로 바꾸고,
--      반환 항목에 `code`(ISO2) 키를 새로 추가한다. **기존 키(`country`)는 지우지 않고
--      유지한다** — 캐시된 구버전 trends.js 가 아직 `c.country`/`c.findings` 를 그대로
--      읽는다(이 저장소는 옛 키를 지워 스탯을 잃은 전례가 있다 — 가산만 한다). `country`
--      값은 각 country_key 그룹 내 findings 건수가 가장 많은 원문 site_country 표기를
--      대표로 고른다(결정론 — 동률이면 site_country 사전순, 013 firm_profile 의
--      display_name 결정론과 같은 방식).
--
--   `country_key = ''` 인 행은 (038 과 동일하게) top_countries 에서 제외한다(미확인은
--   나라가 아니다) — known CTE 가 zone <> 'unknown' 으로 이미 걸러낸다.
--
--   안전 계약(038 유지): security definer + `set search_path = public`. 카운트와
--   서지 메타(국가명/코드)만 반환 — 원문 텍스트·URL·업체명 미탑재. 파라미터 없음.
-- ============================================================================

create or replace function public.findings_zone_category()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with base as (
    select
      category_code,
      raw_signal_id,
      site_country,
      country_key,
      case
        -- [055] PR 은 미국령이라 US 로 합산한다((A) 주석 참고).
        when country_key in ('US', 'PR') then 'us'
        when country_key = ''            then 'unknown'
        else 'foreign'
      end as zone
    from public.findings
    where source = 'FDA 483'
      and scope_status = 'ok'
  ),
  known as (
    select * from base where zone <> 'unknown'
  )
  select jsonb_build_object(
    'scope', jsonb_build_object(
      'source', 'FDA 483',
      -- 소재국 미상 건수 — 분모에서 뺐다는 사실을 화면이 밝히도록 그대로 내보낸다.
      -- [055] country_key='' 는 site_country 가 비어 있던 행 + 매핑 정본 밖의 변종이던
      -- 행을 모두 포함한다(038 은 전자만 셌다).
      'excluded_unknown_country', (select count(*) from base where zone = 'unknown')
    ),
    'totals', jsonb_build_object(
      'foreign', jsonb_build_object(
        'findings',  (select count(*) from known where zone = 'foreign'),
        'documents', (select count(distinct raw_signal_id) from known where zone = 'foreign'),
        -- [055] site_country 문자열이 아니라 country_key 로 distinct — 같은 나라의
        -- 표기 변종이 서로 다른 국가로 중복 집계되지 않는다.
        'countries', (select count(distinct country_key) from known where zone = 'foreign')
      ),
      'us', jsonb_build_object(
        'findings',  (select count(*) from known where zone = 'us'),
        'documents', (select count(distinct raw_signal_id) from known where zone = 'us')
      )
    ),
    -- 카테고리별 양쪽 카운트. 비율은 클라이언트가 계산한다(007/038 관례: 서버는 센다).
    'by_category', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'category_code', category_code,
          'foreign_cnt',   foreign_cnt,
          'us_cnt',        us_cnt
        )
        order by foreign_cnt desc, category_code
      )
      from (
        select
          category_code,
          count(*) filter (where zone = 'foreign')::int as foreign_cnt,
          count(*) filter (where zone = 'us')::int      as us_cnt
        from known
        group by category_code
      ) t
    ), '[]'::jsonb),
    -- [055] 해외 버킷의 국가 구성 — country_key 축으로 재집계. `country`(대표 원문 표기)
    -- 키는 유지하고 `code`(ISO2) 키를 새로 추가한다(가산만, 구버전 JS 호환 유지).
    'top_countries', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'country',   rep.site_country,
          'code',      g.country_key,
          'findings',  g.cnt,
          'documents', g.docs
        )
        order by g.cnt desc, g.country_key
      )
      from (
        select country_key, count(*)::int as cnt, count(distinct raw_signal_id)::int as docs
        from known
        where zone = 'foreign'
        group by country_key
        order by count(*) desc, country_key
        limit 10
      ) g
      join lateral (
        -- 대표 표기: 이 country_key 그룹 안에서 findings 건수가 가장 많은 site_country
        -- 원문(동률이면 site_country 사전순 — 결정론).
        select k2.site_country
        from known k2
        where k2.country_key = g.country_key and k2.zone = 'foreign'
        group by k2.site_country
        order by count(*) desc, k2.site_country asc
        limit 1
      ) rep on true
    ), '[]'::jsonb)
  );
$$;

-- 038 이 이미 anon/authenticated 에 grant 했고 함수 시그니처(파라미터 없음, jsonb 반환)가
-- 그대로다 — create or replace 는 기존 grant 를 보존하므로 재부여 불필요(054 의
-- findings_search 재정의와 동일 관례).

-- ============================================================================
-- (D) public.findings_country_unmapped() — 미매핑 감시 RPC(신설).
--
--   이유: 사전은 반드시 낡는다((A) 하단 주석 참고 — trends.js COUNTRY_LABELS_KO 가
--   23종 고정이라 이미 매핑 정본보다 좁다). 이 함수의 47개 코드 매핑도 새 소스·새 변종이
--   생기면 조용히 country_key='' 로 떨어진다 — 그게 "침묵 실패" 다(이 저장소의 가장 흔한
--   결함 계열). site_country 는 채워져 있는데 country_key 가 빈 문자열인 행 =
--   "정규화가 실패한 채 조용히 unknown 이 된 행" 을 관측 가능하게 만든다.
--
--   권한/RLS: 010/054 관례(invoker + RLS 존중)를 따른다 — 038 처럼 security definer +
--   명시적 scope_status 필터를 쓰지 않고, 054 처럼 security invoker 로 두어 RLS 정책이
--   비공개 행을 자동으로 걸러내게 한다(명시 필터를 따로 두지 않는다).
-- ============================================================================

create or replace function public.findings_country_unmapped()
returns jsonb
language sql
stable
security invoker
set search_path = public, extensions
as $$
  select coalesce(
    jsonb_agg(
      jsonb_build_object('site_country', t.site_country, 'findings', t.cnt)
      order by t.cnt desc, t.site_country asc
    ),
    '[]'::jsonb
  )
  from (
    select site_country, count(*)::int as cnt
    from public.findings
    where site_country <> '' and country_key = ''
    group by site_country
  ) t;
$$;

revoke all on function public.grm_normalize_country(text) from public;
revoke all on function public.findings_country_unmapped() from public;

grant execute on function public.grm_normalize_country(text) to anon, authenticated;
grant execute on function public.findings_country_unmapped() to anon, authenticated;

-- 검증(사람 실행용, 프로덕션 SQL Editor — 컨트롤 타워 라이브 dry-run):
-- 1) 매핑 수렴이 재현되는지(85 원문 변종 -> 47 코드, 빈값 27.4% 제외):
--    select count(distinct site_country) as raw_countries,
--           count(distinct country_key) filter (where country_key <> '') as norm_countries
--    from public.findings;
-- 2) 매핑 밖으로 새는 변종이 있는지:
--    select public.findings_country_unmapped();
-- 3) zone 판정이 038 실측과 정합하는지(US 7,288 근방 · 해외 905 근방 · 미상은 같거나 커짐):
--    select public.findings_zone_category();
-- 4) generated 컬럼이 신규 insert 에도 자동 반영되는지(다음 daily append 관찰) —
--    이 파일 자체는 오프라인 텍스트 계약 테스트로만 검증됨(tests/test_findings_country_key.py).
