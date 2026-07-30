-- ============================================================================
-- 038_findings_zone_category.sql — [트렌드] 해외 실사 vs 미국 내 실사 지적 패턴
--
-- 배경: FDA 483 을 실사 **소재국** 축으로 갈라 보면 지적 카테고리 분포가 뚜렷이 다르다.
--   실측(2026-07-30, 4개 카테고리 내 점유율):
--     일탈/CAPA/조사      해외 23.4% : 미국 12.2%
--     품질부서 관리감독    해외 23.1% : 미국 10.4%
--     공정밸리데이션      해외 12.7% : 미국  7.4%
--     무균보증/무균공정    해외 40.8% : 미국 70.0%
--   ★이 패턴은 **2024 대량백필분과 그 외 연도에서 각각 따로 계산해도 동일**하다
--     (일탈/CAPA 25.6:12.5 vs 23.4:12.2 등) — 특정 시기 적재가 만든 착시가 아니다.
--   한국 제조소가 FDA 실사를 받을 때 무엇을 더 준비해야 하는지에 직접 쓰이는 사실이라
--   트렌드 대시보드에 상설 패널로 올린다.
--
-- ─ 범위를 FDA 483 으로 **한정하는 이유**(중요) ─────────────────────────────
--   `site_country` 가 실제로 채워지는 소스는 483 뿐이다. 실측:
--     FDA 483            : US 7,288 · 해외 905 · 미상 90(1.1%)  → 분해 가능
--     FDA Warning Letter : **전량 미상 3,270**                   → 분해 불가
--   WL 을 섞으면 "미상"이 최대 버킷이 되어 비교가 무의미해진다. 그래서 483 한정이며,
--   그 사실을 반환값(`scope`)에 실어 **화면이 반드시 밝히도록** 강제한다.
--
-- ─ 정직성 장치 2가지 ───────────────────────────────────────────────────────
--   ⑴ `scope.excluded_unknown_country` — 소재국을 모르는 건수를 **숨기지 않고 반환**한다.
--      분모에서 뺐다는 사실을 화면이 밝힐 수 있어야 한다.
--   ⑵ `top_countries` — "해외"는 균질한 덩어리가 아니다. 실측상 인도가 압도적이라
--      (533/905 findings) 이걸 안 보여주면 독자가 "해외 = 우리(한국)"로 오독한다.
--      해외 버킷의 국가 구성을 함께 반환해 일반화의 한계를 드러낸다.
--
-- ─ 소재국 정규화 ───────────────────────────────────────────────────────────
--   미국 표기가 두 가지다: `United States`(7,189) · `USA`(99). 둘 다 US 로 본다.
--   빈 문자열은 UNKNOWN(집계 제외·건수만 보고). 그 외 전부 FOREIGN.
--   ※한국도 `Republic of Korea`/`South Korea`/`The Republic of Korea` 3종으로 갈려 있으나
--     이 함수의 US/FOREIGN 이분에는 영향이 없다. 한국 전용 뷰를 만들 때 정규화가 필요하다.
--
-- ─ 안전 계약 ───────────────────────────────────────────────────────────────
--   · security definer + `set search_path = public` (007/008/013 관례와 동형).
--   · 공개 게이트는 명시 술어 `scope_status = 'ok'`.
--   · **카운트와 서지 메타만 반환한다** — 원문 텍스트·URL·업체명을 싣지 않는다
--     (007/008 안전 계약 유지). jsonb_build_object 의 키 목록이 그 계약의 유일한 표면이다.
--   · 파라미터 없음 — 입력으로 인한 실패 경로가 없다.
--
-- 전제: 002(findings) + 010 계열 공개 게이트.
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
      case
        when site_country in ('United States', 'USA') then 'us'
        when coalesce(site_country, '') = ''          then 'unknown'
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
      'excluded_unknown_country', (select count(*) from base where zone = 'unknown')
    ),
    'totals', jsonb_build_object(
      'foreign', jsonb_build_object(
        'findings',  (select count(*) from known where zone = 'foreign'),
        'documents', (select count(distinct raw_signal_id) from known where zone = 'foreign'),
        'countries', (select count(distinct site_country) from known where zone = 'foreign')
      ),
      'us', jsonb_build_object(
        'findings',  (select count(*) from known where zone = 'us'),
        'documents', (select count(distinct raw_signal_id) from known where zone = 'us')
      )
    ),
    -- 카테고리별 양쪽 카운트. **비율은 클라이언트가 계산한다** — 서버가 비율을 내보내면
    -- 반올림 표기 정책이 서버에 박혀 화면과 어긋날 여지가 생긴다(007 관례: 서버는 센다).
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
    -- 해외 버킷의 국가 구성. "해외"를 균질한 덩어리로 오독하지 않게 하는 장치.
    'top_countries', coalesce((
      select jsonb_agg(
        jsonb_build_object('country', site_country, 'findings', cnt, 'documents', docs)
        order by cnt desc, site_country
      )
      from (
        select site_country, count(*)::int as cnt, count(distinct raw_signal_id)::int as docs
        from known
        where zone = 'foreign'
        group by site_country
        order by count(*) desc, site_country
        limit 10
      ) t
    ), '[]'::jsonb)
  );
$$;

-- 007/008/013 관례: 집계 RPC 는 anon 이 직접 호출한다(정적 사이트라 서버가 없다).
grant execute on function public.findings_zone_category() to anon, authenticated;
