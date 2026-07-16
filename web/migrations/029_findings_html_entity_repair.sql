-- [FIND-1] HTML 엔티티 오염 정정 — firm_name/site_name 언이스케이프 (2026-07-16).
--
-- ★결함: 수집기 `collect_fda_483.py:_strip()` 이 태그만 제거하고 엔티티를 복원하지 않아
--   FDA 원본의 escape 표기가 그대로 적재됐다. 원본이 escape 해서 내려주는 게 사실이다 —
--   라이브 `ora-foia-reading.json` 3079행 중 217셀에 엔티티 실재(`&amp;` 129 / `&#039;` 95
--   / `&quot;` 2). HTML 표 경로는 HTMLParser(convert_charrefs=True)가 이미 복원하므로
--   무사했고, **JSON/DataTables 경로(현행 라이브)만** 새고 있었다.
--   → 상류 수리는 같은 PR 의 `_strip` 엔티티 복원 + `tests/test_fda_483.py`
--     `HtmlEntityContractTest`(6건, 수리 전 전건 실패 확인)가 재발을 막는다.
--   이 파일은 **이미 적재된 행의 정정**만 담당한다(상류 수리 없이 이 파일만 돌리면 다음
--   수집에서 다시 오염된다 — 반드시 함께 배포).
--
-- ★영향 실측(2026-07-16 라이브, anon 조회 — 공개 findings 8168행 기준):
--     findings.firm_name  439행 / 45개 고유 표기 / 전량 source='FDA 483'
--     findings.site_name  439행 (동일 행 — site_name 은 raw.company 에서 파생돼 firm_name 과 동행)
--     엔티티 종류: `&amp;` 311 · `&#039;` 137 (그 외 0)
--     대표: 'H &amp; P Industries, Inc.'(128행 — 상위 2위 업체라 대시보드 노출) ·
--           'California Pharmacy &amp; Compounding Center'(30) ·
--           'Dr. Reddy&#039;s Laboratories Ltd.'(16)
--   다른 텍스트 컬럼(site_country/product_family/finding_text/finding_text_ko/
--   category_label_ko/document_id/evidence_url/entity_id/modality)은 전부 0행 — 오염은
--   업체명 축에만 있다. 다른 source(MFDS/HC/WHO/ICH…)도 0행.
--   ※ raw_signals 는 anon 차단(002 계약)이라 이 실측에 포함되지 않았다 — (D) 가 같은
--     술어로 함께 정정한다(건수는 (B) dry-run 으로 적용 직전에 확인하라).
--
-- ★안전성 — 적용 전 확인 끝난 3가지:
--   1) `findings_rawsig_text_md5_uq` unique 제약은 `(raw_signal_id, md5(finding_text))` 다.
--      **firm_name/site_name 을 포함하지 않는다** → 이 UPDATE 로는 위반이 불가능하다.
--      (finding_text 는 이 파일이 건드리지 않는다.)
--   2) `findings.firm_key` 는 `grm_normalize_firm_name(firm_name)` generated stored 라
--      자동 재계산되지만 **값은 안 바뀐다** — 013 의 정규화 규칙 1번이 이미 `&amp;`→`&`,
--      `&#039;`→`'` 를 복원하고 있어서다(그래서 firm_key='h & p industries' 는 처음부터
--      정상이었고, 이 오염이 firm_key 축에서는 안 보였다). 45개 고유 표기 전수를 파이썬
--      정본 `normalize_firm_name` 으로 재현 검증: firm_key 변동 0건. → firm_key 로 묶인
--      워치리스트/유사도/임베딩/RPC 는 전부 무영향. (E)-2 가 라이브에서 재확인한다.
--   3) 정정 후 이름이 기존 행과 충돌(병합)하지 않는다 — 45개 표기의 unescape 결과를
--      전수 조회한 결과 이미 그 이름으로 존재하는 행 0건. 즉 순수 표기 정정이고
--      업체 통합/분할이 아니다(대시보드 top_firms 순위 불변, 건수 이동 없음).
--
-- ★023 사고(대량 update 커넥터 타임아웃) 대응 — 범위를 두 겹으로 좁힌다:
--   `source = 'FDA 483'`(오염이 실재하는 유일 소스) **AND** 엔티티 정규식 매치 행만.
--   439행(+raw_signals 동급) 규모라 단발 실행으로 충분하다. 전량 스캔·무조건 update 금지.
--
-- ★멱등성: 술어가 "엔티티를 포함한 행" 이므로 정정 후 재실행은 0행 no-op 다. 단
--   이중 이스케이프(`&amp;amp;`)가 있다면 재실행이 한 겹 더 풀 수 있다 — 실측 결과 45개
--   표기 전부 단일 이스케이프이고 이중은 0건이다((B) dry-run 의 double_escaped 열로 매번
--   재확인하라. 0 이 아니면 멈추고 사람이 판단한다).
--
-- 전제: 002(테이블)·013(firm_key) 적용 완료. 이 파일은 기존 함수/정책/제약을 안 건드린다.
--   028(findings_rpc_projection)과는 완전 독립이다 — 그 쪽은 RPC 투영(읽기 경로),
--   이 쪽은 적재 데이터 정정(쓰기 1회). 순서 의존 없음(어느 쪽을 먼저 적용해도 무방).
--   ※ 028 은 동시 작업 트랙이 선점한 번호라 이 파일이 029 로 밀렸다.
-- 적용: 사람이 Supabase SQL Editor 에 붙여넣는다(자동 적용 아님). (B) → (C)(D) → (E) 순서.

-- ============================================================================
-- (A) public.grm_html_unescape(p_text text) — 단일 레벨 HTML 엔티티 복원.
--
-- ★치환 순서가 계약이다: `&amp;` 를 **맨 마지막**에 푼다. 먼저 풀면 `&amp;lt;`(리터럴
--   `&lt;` 를 표현한 것)가 `&lt;` → `<` 로 이중 복원돼 원문을 훼손한다. 마지막에 풀면
--   `&amp;lt;` → (다른 치환 무매치) → `&lt;` 로 한 겹만 풀려 파이썬 `html.unescape` 의
--   단일 레벨 의미와 일치한다.
-- `&nbsp;` 는 U+00A0 대신 일반 공백으로 보낸다 — 수집기가 복원 뒤 공백 축약까지 하므로
--   그 최종 산출물과 같은 값이 되게 맞춘다(파리티).
-- IMMUTABLE: replace 만 쓰는 순수 함수(카탈로그·테이블 미참조).
-- ============================================================================

create or replace function public.grm_html_unescape(p_text text)
returns text
language sql
immutable
set search_path = public
as $$
  select replace(
           replace(
             replace(
               replace(
                 replace(
                   replace(
                     replace(
                       replace(coalesce(p_text, ''), '&#039;', ''''),
                       '&#39;', ''''),
                     '&apos;', ''''),
                   '&quot;', '"'),
                 '&lt;', '<'),
               '&gt;', '>'),
             '&nbsp;', ' '),
           '&amp;', '&')                             -- ← 단일 레벨 보장(맨 마지막)
$$;

-- ============================================================================
-- (B) DRY-RUN — (C)(D) 실행 **전에** 이것부터 돌려 영향 범위를 눈으로 확인한다.
--     기대치(2026-07-16 실측): findings 439행 / 45 표기 / double_escaped 0.
--     double_escaped 가 0 이 아니면 멈춘다(위 멱등성 주석 참조).
-- ============================================================================

-- (B)-1 영향 행 수 — 테이블·컬럼별
select 'findings.firm_name' as target, count(*) as rows_affected,
       count(distinct firm_name) as distinct_spellings,
       count(*) filter (
         where public.grm_html_unescape(public.grm_html_unescape(firm_name))
               is distinct from public.grm_html_unescape(firm_name)
       ) as double_escaped
  from public.findings
 where source = 'FDA 483'
   and firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'findings.site_name', count(*), count(distinct site_name),
       count(*) filter (
         where public.grm_html_unescape(public.grm_html_unescape(site_name))
               is distinct from public.grm_html_unescape(site_name)
       )
  from public.findings
 where source = 'FDA 483'
   and site_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'raw_signals.firm_name', count(*), count(distinct firm_name), 0
  from public.raw_signals
 where source = 'FDA 483'
   and firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'raw_signals.site_name', count(*), count(distinct site_name), 0
  from public.raw_signals
 where source = 'FDA 483'
   and site_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'raw_signals.title', count(*), count(distinct title), 0
  from public.raw_signals
 where source = 'FDA 483'
   and title ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

-- (B)-2 before → after 표기 대조 + firm_key 이동 여부(핵심 안전성 확인).
--        기대치: 45행 전부 firm_key_after = firm_key_now (key_moves 없음).
select firm_name                                              as before_name,
       public.grm_html_unescape(firm_name)                     as after_name,
       firm_key                                                as firm_key_now,
       public.grm_normalize_firm_name(public.grm_html_unescape(firm_name))
                                                               as firm_key_after,
       (firm_key is distinct from
        public.grm_normalize_firm_name(public.grm_html_unescape(firm_name)))
                                                               as key_moves,
       count(*)                                                as rows
  from public.findings
 where source = 'FDA 483'
   and firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
 group by 1, 2, 3, 4, 5
 order by rows desc;

-- (B)-3 정정 후 기존 행과의 이름 충돌(업체 병합) 여부. 기대치: 0행.
select public.grm_html_unescape(d.firm_name) as after_name, count(*) as would_merge_into
  from (select distinct firm_name from public.findings
         where source = 'FDA 483'
           and firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});') d
  join public.findings f
    on f.firm_name = public.grm_html_unescape(d.firm_name)
 group by 1;

-- ============================================================================
-- (C) findings 정정 — firm_name/site_name. firm_key 는 generated 라 자동 재계산된다.
--     finding_text 는 건드리지 않는다 → findings_rawsig_text_md5_uq 무관.
-- ============================================================================

update public.findings
   set firm_name = public.grm_html_unescape(firm_name)
 where source = 'FDA 483'
   and firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

update public.findings
   set site_name = public.grm_html_unescape(site_name)
 where source = 'FDA 483'
   and site_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

-- ============================================================================
-- (D) raw_signals 정정 — **파생 컬럼만**(firm_name/site_name/title).
--
-- ★`raw_json`/`row_json` 은 의도적으로 건드리지 않는다: 002 의 원본 보존층 계약이고,
--   원문이 escape 였다는 사실 자체가 보존돼야 한다. 게다가 `raw_sha256` 은
--   sha256(raw_json) 이므로 raw_json 을 고치면 해시 정합성이 깨진다 — 안 건드리므로 무관.
-- ★왜 raw_signals 도 고치나: findings 는 raw_signals 에서 파생된다(build_finding 이
--   firm_name/site_name 을 그대로 복사). 여기를 남겨두면 findings 재백필 시 오염이 되살아난다.
-- ============================================================================

update public.raw_signals
   set firm_name = public.grm_html_unescape(firm_name)
 where source = 'FDA 483'
   and firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

update public.raw_signals
   set site_name = public.grm_html_unescape(site_name)
 where source = 'FDA 483'
   and site_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

update public.raw_signals
   set title = public.grm_html_unescape(title)
 where source = 'FDA 483'
   and title ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

-- ============================================================================
-- (E) 검증(적용 직후 실행)
-- ============================================================================

-- (E)-1 잔여 오염 0 — 소스/컬럼 무관 전수. 기대치: 0행.
select 'findings.firm_name' as target, count(*) as remaining from public.findings
 where firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'findings.site_name', count(*) from public.findings
 where site_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'raw_signals.firm_name', count(*) from public.raw_signals
 where firm_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'raw_signals.site_name', count(*) from public.raw_signals
 where site_name ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'
union all
select 'raw_signals.title', count(*) from public.raw_signals
 where title ~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});';

-- (E)-2 firm_key 불변 재확인 — 대표 업체가 정정 후에도 같은 key 를 유지하고 건수도 그대로.
--        기대치: firm_name='H & P Industries, Inc.' / firm_key='h & p industries' / 128행.
select firm_name, firm_key, count(*) as rows
  from public.findings
 where firm_key = 'h & p industries'
 group by 1, 2;

-- (E)-3 대시보드 축(025/027 과 교차검증한 top_firms) 정상 표기 확인.
select firm_key, min(firm_name) as display_name, count(*) as rows
  from public.findings
 where source = 'FDA 483'
 group by 1
 order by rows desc
 limit 5;
