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
-- ★영향 실측(2026-07-16 라이브 dry-run, service_role 전수 — FDA 483 은 문서 1,895건·
--   findings 9,202건):
--     findings.firm_name    495행 / 53개 고유 표기  ← 공개 439 + 비공개 56(scope_status<>'ok')
--     findings.site_name    495행 (동일 행 — site_name 은 raw.company 파생이라 firm_name 과 동행)
--     raw_signals(firm_name/site_name/title)  각 95행 / 69개 고유 표기
--       (findings 보다 표기가 많다 — 483 문서 중 findings 를 만들지 못한 건이 있어서다.
--        raw_signals 가 상위집합이므로 (D) 를 빼면 재백필 시 오염이 되살아난다.)
--     엔티티 종류: findings.firm_name `&amp;` 339 · `&#039;` 165(한 행에 둘 다 있는 경우 존재)
--                  raw_signals.title  `&amp;` 45 · `&#039;` 52 · **`&quot;` 2**
--       → `&quot;` 가 실재하므로 (A) 는 특정 엔티티 2종만 푸는 게 아니라 **일반 복원**이어야 한다.
--     대표: 'H &amp; P Industries, Inc.'(133행 = 공개 128 — 상위 2위 업체라 대시보드 노출) ·
--           'California Pharmacy &amp; Compounding Center'(30) ·
--           'Pacific Healthcare, Inc. dba B &amp; B Pharmacy'(22) ·
--           'Dr. Reddy&#039;s Laboratories Ltd.'(16)
--   다른 텍스트 컬럼(site_country/product_family/finding_text/finding_text_ko/
--   category_label_ko/document_id/evidence_url/entity_id/modality)은 전부 0행 — 오염은
--   업체명 축에만 있다.
--   ★술어 커버리지 확인 완료: 오염 행 중 `source <> 'FDA 483'` 인 것은 두 테이블 모두
--     **0행** — 아래 update 의 source 술어는 아무것도 놓치지 않는다(다른 수집기의 같은 계열
--     결함은 잠복 상태이고 라이브 발현이 없다).
--   ★dry-run 실측: double_escaped 0 · key_moves 0 · 병합충돌 0 · 정정 후 잔여 엔티티 0.
--
-- ★안전성 — 적용 전 확인 끝난 3가지:
--   1) `findings_rawsig_text_md5_uq` unique 제약은 `(raw_signal_id, md5(finding_text))` 다.
--      **firm_name/site_name 을 포함하지 않는다** → 이 UPDATE 로는 위반이 불가능하다.
--      (finding_text 는 이 파일이 건드리지 않는다.)
--   2) `findings.firm_key` 는 `grm_normalize_firm_name(firm_name)` generated stored 라
--      자동 재계산되지만 **값은 안 바뀐다** — 013 의 정규화 규칙 1번이 이미 `&amp;`→`&`,
--      `&#039;`→`'` 를 복원하고 있어서다(그래서 firm_key='h & p industries' 는 처음부터
--      정상이었고, 이 오염이 firm_key 축에서는 안 보였다). **라이브 dry-run 전수 검증:
--      495행 중 key_moves 0행**(53개 고유 표기 전부 firm_key 불변). → firm_key 로 묶인
--      워치리스트/유사도/임베딩/RPC 는 전부 무영향. (E)-2 가 적용 후 재확인한다.
--   3) 정정 후 이름이 기존 행과 충돌(병합)하지 않는다 — 53개 표기의 unescape 결과로
--      이미 존재하는 행이 있는지 라이브 조회: **0행**. 즉 순수 표기 정정이고
--      업체 통합/분할이 아니다(대시보드 top_firms 순위 불변, 건수 이동 없음).
--
-- ★023 사고(대량 update 커넥터 타임아웃) 대응 — 범위를 두 겹으로 좁힌다:
--   `source = 'FDA 483'`(오염이 실재하는 유일 소스) **AND** 엔티티 정규식 매치 행만.
--   findings 495행 + raw_signals 95행 규모라 단발 실행으로 충분하다(dry-run 실측).
--   전량 스캔·무조건 update 금지.
--
-- ★멱등성: 술어가 "엔티티를 포함한 행" 이므로 정정 후 재실행은 0행 no-op 다. 단
--   이중 이스케이프(`&amp;amp;`)가 있다면 재실행이 한 겹 더 풀 수 있다 — 라이브 dry-run
--   결과 **double_escaped 0행**(495행 전수 단일 이스케이프)이다((B) 의 double_escaped 열로
--   매번 재확인하라. 0 이 아니면 멈추고 사람이 판단한다).
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
--     기대치(2026-07-16 라이브 dry-run 실측):
--       findings.firm_name  495행 / 53 표기 / double_escaped 0   (공개 439 + 비공개 56)
--       findings.site_name  495행 / 53 표기
--       raw_signals 3컬럼   각 95행 / 69 표기
--       (B)-2 key_moves 전부 false · (B)-3 병합충돌 0행.
--     double_escaped 가 0 이 아니거나 key_moves 가 하나라도 true 면 멈춘다(사람 판단).
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
--        기대치: 53개 표기 전부 firm_key_after = firm_key_now (key_moves 전부 false).
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
--
-- ★(C)+(D) 5개 update 는 **한 트랜잭션**으로 묶는다(begin ~ commit). 중간에 하나가
--   실패했을 때 findings 만 고쳐지고 raw_signals 는 옛 표기로 남는 반쪽 상태를 막는다
--   — 그 상태로 재백필이 돌면 오염이 되살아난다((D) 주석 참조). SQL Editor 에 (C)(D) 를
--   통째로 붙여넣어 한 번에 실행하라(begin 만 실행하고 멈추지 말 것).
-- ============================================================================

begin;

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

commit;

-- ============================================================================
-- (E) 검증(적용 직후 실행)
-- ============================================================================

-- (E)-1 잔여 오염 0 — 소스/컬럼 무관 전수. 기대치: 5행 전부 remaining = 0.
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
--   기대치: 1행만 나온다 — firm_name='H & P Industries, Inc.'(엔티티 사라짐) /
--           firm_key='h & p industries'(불변) / rows=133 · public_rows=128.
--   ★rows 는 133 이다(128 아님). SQL Editor 는 postgres 로 실행돼 RLS 를 우회하므로
--     비공개(scope_status<>'ok') 5행까지 함께 센다 — 128 은 사이트에 보이는 공개분이다.
--     이 구분을 안 적어두면 "128 이어야 하는데 133 이 나온다"고 오판하게 된다.
--   ★2행 이상 나오면 멈춰라 — 정정 전/후 표기가 공존한다는 뜻(= update 가 일부만 적용됨).
select firm_name, firm_key, count(*) as rows,
       count(*) filter (where scope_status = 'ok') as public_rows
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
