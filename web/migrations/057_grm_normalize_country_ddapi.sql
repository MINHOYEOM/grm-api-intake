-- ============================================================================
-- 057_grm_normalize_country_ddapi.sql — grm_normalize_country 국가 매핑 확장
--   (FDA Data Dashboard API `inspections_classifications` CountryName 27종)
--
-- 배경: 신규 FDA 실사 등급(fda_inspections, 다음 마이그레이션 058)을 country_key
--   축으로 findings 와 나란히 세우기 위해, 그 원천(DDAPI)의 CountryName 표기를
--   055_findings_country_key.sql 의 public.grm_normalize_country 매핑 정본에
--   먼저 편입한다. 새 테이블(058)의 country_key generated 컬럼이 이 함수를 그대로
--   재사용하므로, 함수 확장이 테이블 생성보다 먼저 적용돼야 한다.
--
-- 실측(오프라인 픽스처 6,417건 전수 — scratchpad ddapi_gmp.json, ProjectArea=
--   'Drug Quality Assurance' 한정): CountryName distinct 62종. 이 중 35종은 기존
--   47코드 매핑에 변형 없이 이미 매치된다(예: "United States"/"India"/"China" 등
--   DDAPI 표기가 기존 when 절과 완전히 같은 문자열이라 그대로 통과). 나머지 27종이
--   매핑 밖이었다 — 아래 (A)가 그 27종을 추가한다. 추가 후 62종 전수 매핑 성공(잔여
--   미매핑 0)을 tests/test_findings_country_key.py 의
--   Ddapi057ExtensionTest.test_all_62_ddapi_country_names_map 로 고정했다(오프라인
--   픽스처 62개 이름을 그대로 fixture 로 박아 검증 — 라이브 DDAPI 재호출 없음).
--
--   27종 중 6종(Korea (the Republic of)/Czech Republic/Finland/Israel/
--   Slovenia/Norway)은 기존 코드(KR/CZ/FI/IL/SI/NO)를 재사용한다 — 같은 나라를
--   미국 정부 DDAPI 가 findings 기존 소스들과 다른 영문 정식 국호로 표기했을
--   뿐이다(예: 기존 "korea"/"republic of korea" 계열에 "korea (the republic of)"가
--   추가). 나머지 21종은 이 확장 전까지 어떤 findings 소스에도 없던 신규 코드다
--   (SG/BR/TH/MT/AR/HR/HK/CO/NZ/BG/DO/LV/OM/CR/EG/MO/PH/UY/AW/EE/AE).
--   → 47 + 21 = 68 개 고유 코드, 80 + 27 = 107 개 고유 원문 변종.
--
-- ─ ★"create or replace 로 파라미터를 추가하면 오버로드" 함정(MEMORY 상시 경계) 해당
--   없음 확인 — 이 파일은 시그니처(p_raw text 1개, text 반환)를 전혀 바꾸지 않는다.
--   본문(when/then 목록)만 늘어난다. 확인 방법: `create or replace function` 은
--   Postgres 카탈로그에서 함수를 (스키마, 이름, 인자 타입 목록) 으로 찾는다 — 인자
--   타입이 그대로면 같은 pg_proc OID 를 그대로 갱신하고 새 오버로드를 만들지
--   않는다(반대로 인자를 추가/삭제/타입변경하면 오버로드가 생겨 055 관례처럼 옛
--   시그니처를 drop 해야 한다 — 이 파일은 그 경로가 아니다).
--
-- ─ ★findings.country_key(STORED GENERATED, 055) 안전성 — "직접 확인" 결과
--   (프로덕션 rfwixqqdljpmtjdlblct 라이브 조회, 2026-08-12):
--   1) pg_depend 로 country_key 의 생성식이 함수를 **OID 로** 참조함을 확인했다
--      (`pg_proc` 의 `grm_normalize_country` → **oid 30300**, refclassid=pg_proc,
--      deptype='n'). CREATE OR REPLACE FUNCTION 은 시그니처가 같으면 이 OID 를 그대로
--      유지하고 본문(pg_proc.prosrc)만 갱신한다 — 즉 컬럼을 drop/재생성할 필요가 전혀
--      없다. 이것이 013/029 가 "generated 컬럼은 함수를 바꿔도 자동으로 살아있다"고
--      전제해 온 근거를 이번에 실제로 카탈로그에서 확인한 것이다.
--      ★2026-08-12 정정: 이 자리에 처음 적힌 값 17792 는 **함수가 아니라
--      `public.findings` 테이블(pg_class)의 OID** 였다 — 같은 pg_depend 결과 안에
--      테이블 자기참조(refclassid=pg_class, refobjsubid=13/33 = site_country/country_key)
--      가 함께 들어 있는 것을 함수 의존으로 오독한 것이다. 결론(시그니처 불변이면
--      OID 보존 → 컬럼 재생성 불요)은 바뀌지 않지만, **근거로 댄 숫자가 틀렸으므로
--      정정한다**(이 저장소 규율: 모르는 값을 채우지 마라·모든 수치는 직접 실행해
--      얻은 값이어야 한다). 재확인 질의는 아래 검증 4) 참고.
--   2) 단, STORED GENERATED 컬럼은 "표현식이 바뀌면 기존 행이 자동 재계산된다"는
--      뜻이 **아니다** — 함수 본문 교체는 이후의 INSERT/UPDATE에서만 새 매핑을
--      적용한다. 기존 저장값은 그대로 남는다(013 헤더의 "컬럼 추가 시점에 기존
--      행 전체가 자동 재계산"은 ALTER TABLE ADD COLUMN 이 테이블을 재작성하는
--      1회성 사건이지, 이후 함수 교체에는 적용되지 않는다). 그래서 이 파일 (B)에서
--      명시적 백필(터치 UPDATE)을 둔다.
--   3) 백필 영향 실측(적용 전 라이브 조회): `findings_country_unmapped()` 는 이미
--      `[]`(0건)이고, `count(*) filter (where site_country <> '' and country_key = '')`
--      도 0/26,499행이다 — 즉 findings.site_country 에는 이번에 추가한 27개 DDAPI
--      표기(예: "Korea (the Republic of)")가 현재 단 한 건도 없다(483/WL/MFDS/EU
--      GMP NCR/MHRA 다섯 수집기 중 어느 것도 이 정식 영문 국호 표기를 쓰지 않는다).
--      → (B)의 백필은 오늘 시점 0행 no-op 이지만, 향후 findings 에 이 표기가 유입될
--      경우를 대비해 멱등적으로 남겨둔다(반복 실행해도 안전 — 매치되는 행이 생기면
--      그때 자동으로 채워진다).
--
-- ─ findings_country_unmapped() 영향: 이 파일은 그 함수 본문을 건드리지 않는다
--   (grm_normalize_country 호출만 하므로 자동으로 새 매핑을 즉시 반영). 위 3)에서
--   보였듯 findings 축에서는 관측치 변화가 없다(0→0) — DDAPI 표기는 findings 가 아닌
--   신규 테이블 fda_inspections(058)에만 나타나므로, findings_country_unmapped() 는
--   fda_inspections 미매핑을 관측하지 않는다(별도 축 — 058 이 그 문제를 다룬다).
--
-- supersede 체인: grm_normalize_country = 055 → **이 파일(057)**.
-- 전제: 055_findings_country_key.sql 이 먼저 적용되어 있어야 한다.
-- ============================================================================
-- ★004/009 함정 해당 없음(055 관례 계승): plpgsql DO 블록·선언 변수·배열 슬라이스
-- 전혀 없음 — (A)는 language sql 순수 함수, (B)는 단순 UPDATE 한 문장이다.
-- ============================================================================

-- ============================================================================
-- (A) public.grm_normalize_country(p_raw text) — 055 원본 80개 변종 + 신규 27개
--   변종(합계 107, 68 고유 코드). 055 이후 이 함수를 소비하는 findings_zone_category()
--   /findings_search()/findings_country_unmapped() 는 전혀 재정의하지 않는다 —
--   이 함수 하나만 교체하면 세 소비처 모두 새 매핑을 자동으로 물려받는다(코드가
--   함수를 호출만 하지, 매핑을 복제하지 않으므로).
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
    -- [057] FDA Data Dashboard API(inspections_classifications) CountryName
    -- 확장분 27종 -- 6종은 기존 코드 재사용(KR/CZ/FI/IL/SI/NO), 21종은 신규 코드.
    when 'korea (the republic of)'         then 'KR'
    when 'singapore'                       then 'SG'
    when 'czech republic'                  then 'CZ'
    when 'finland'                         then 'FI'
    when 'israel'                          then 'IL'
    when 'brazil'                          then 'BR'
    when 'slovenia'                        then 'SI'
    when 'thailand'                        then 'TH'
    when 'malta'                           then 'MT'
    when 'argentina'                       then 'AR'
    when 'croatia'                         then 'HR'
    when 'hong kong sar'                   then 'HK'
    when 'norway'                          then 'NO'
    when 'colombia'                        then 'CO'
    when 'new zealand'                     then 'NZ'
    when 'bulgaria'                        then 'BG'
    when 'dominican republic (the)'        then 'DO'
    when 'latvia'                          then 'LV'
    when 'oman'                            then 'OM'
    when 'costa rica'                      then 'CR'
    when 'egypt'                           then 'EG'
    when 'macao'                           then 'MO'
    when 'philippines'                     then 'PH'
    when 'uruguay'                         then 'UY'
    when 'aruba'                           then 'AW'
    when 'estonia'                         then 'EE'
    when 'united arab emirates'            then 'AE'
    else ''
  end;
$$;

-- 055 가 이미 anon/authenticated 에 grant 했고 시그니처(인자 1개 text, 반환 text)가
-- 그대로다 -- create or replace 는 기존 grant 를 보존하므로 재부여 불필요(055 의
-- findings_zone_category 재정의와 동일 관례).

-- ============================================================================
-- (B) 백필 -- findings.country_key(STORED GENERATED) 는 함수 본문 교체만으로는
--   기존 행이 재계산되지 않는다(위 헤더 "직접 확인" ②). 새 매핑에 걸릴 수 있는
--   행(site_country 가 채워져 있는데 country_key 가 여전히 빈 문자열인 행)만
--   좁혀서 site_country 를 자기 자신에 대입하는 no-op UPDATE 로 재계산을 강제한다
--   -- generated 컬럼은 UPDATE 시점에 새 함수 정의로 다시 계산되므로, 값이 바뀌지
--   않는 컬럼(site_country)을 건드리는 것만으로 country_key 파생값이 새로고침된다.
--   ★실측(위 헤더 ③): 이 시점 영향 행 0건(no-op) -- 미래 유입 대비 멱등 안전망.
-- ============================================================================

update public.findings
   set site_country = site_country
 where site_country <> ''
   and country_key = '';

-- 검증(사람 실행용, 프로덕션 SQL Editor -- 컨트롤 타워 라이브 dry-run):
-- 1) 함수가 새 27종을 실제로 매핑하는지:
--    select public.grm_normalize_country('Korea (the Republic of)'),  -- 'KR'
--           public.grm_normalize_country('Singapore'),                -- 'SG'
--           public.grm_normalize_country('United Arab Emirates');     -- 'AE'
-- 2) 백필 영향 건수(적용 직후 재실행하면 0건이어야 한다 -- 이미 반영됐으므로):
--    update public.findings set site_country = site_country
--     where site_country <> '' and country_key = '';
-- 3) findings 축은 무회귀인지(위 헤더 ③ 예상대로 0→0):
--    select public.findings_country_unmapped();  -- 여전히 '[]' 이어야 정상
-- 4) 함수 OID 가 055 적용 이후로 안 바뀌었는지(컬럼 재생성 없이 교체됐는지 확인):
--    select oid, proname from pg_proc where proname = 'grm_normalize_country';
--    -- 이 oid 가 findings.country_key 의 pg_depend refobjid 와 여전히 일치해야 한다:
--    select d.refobjid::regprocedure
--    from pg_attribute a
--    join pg_attrdef ad on ad.adrelid = a.attrelid and ad.adnum = a.attnum
--    join pg_depend d on d.objid = ad.oid and d.classid = 'pg_attrdef'::regclass
--    join pg_class c on c.oid = a.attrelid
--    where c.relname = 'findings' and a.attname = 'country_key' and d.deptype = 'n';
