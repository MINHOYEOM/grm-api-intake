-- ============================================================================
-- 028_findings_rpc_projection.sql — [FIND-1] RPC 반환에서 누락된 클라이언트 소비 필드 3종 복원
--
-- ※ 2026-07-16 후속 2건:
--   ① findings_search 는 이후 **030**(hardening: work_mem·p_page 상한·blob semantics)이
--     create or replace 로 supersede 했다 — findings_search 의 프로덕션 현행 정의는 030,
--     findings_document 의 현행 정의는 이 파일(028)이다.
--   ② 프로덕션 마이그레이션 이력에는 028 이 **두 번** 기록돼 있다:
--     `findings_search_row_projection_028`(레인B 의 부분 적용본 — 문서 레벨 firm_key 누락)
--     → `028_findings_rpc_projection`(이 파일 정본 재적용, 레인A 가 pg_get_functiondef
--     마커 대조로 표류를 발견해 수렴). 라이브 함수는 이 파일과 동등하며 이중 기록은
--     이력 사실일 뿐 런타임 영향이 없다 — 병렬 두 세션이 같은 결함을 각자 고치다 생긴
--     흔적이므로 지우지 않고 기록한다.
--
-- supersede 체인: 026(원본 정의) → 027(findings_search 에 대시보드 축 추가) → 이 파일(028)이
--   findings_search 와 findings_document 를 **둘 다** create or replace 로 supersede 한다.
--   027 과 달리 findings_document 도 재선언하는 이유는 아래 결함이 두 함수에 **동일하게**
--   있기 때문이다 — 딥링크 단독 렌더도 같은 카드 조립 코드를 타므로 같은 소실이 난다.
--   026/027 은 이미 프로덕션에 적용됐으므로 그 파일들을 수정하지 않는다(체인 유지).
--
-- ★왜: PR-B(findings.js 를 RPC 소비로 전환)에서 발견한 **서버측 투영 누락**이다. 클라이언트
--   코드 결함이 아니라 026/027 의 반환 계약 결함이다. findings.js 의 카드 조립부가 읽는
--   필드 3개가 반환 객체에 없다:
--
--     1. firm_key           — buildDocHead() 가 있으면 업체명을 /findings/firm/?key= 링크로
--                             만든다(013). 없으면 평문 h2 로 폴백 = **업체 프로파일 진입점이
--                             전 문서 카드에서 사라진다**(013/014/015 워치리스트 도달 경로).
--     2. translation_method — appendOrigAndNote() 가 'llm_assisted' 일 때 "AI 번역 — 원문
--                             대조 권장" 고지를 단다. 없으면 **AI 번역 고지가 전 카드에서
--                             사라진다**(v1.68~v1.70 AI 고지 정책과 충돌).
--     3. confidence         — appendMetaLine() 의 "· 신뢰도 N%" 표기가 사라진다.
--
-- ★왜 조용히 소실되나: 셋 다 클라이언트가 방어적 `if (row.xxx)` / `=== "llm_assisted"` 분기로
--   읽는다 — 013·005 미적용 라이브 DB 하위호환을 위해 의도적으로 넣은 폴백이다(findings.js:
--   1195 주석). 그래서 필드가 없어도 크래시가 없고, 링크·고지·신뢰도만 **말없이** 빠진다.
--   교훈: 하위호환 폴백은 "필드가 없는 정상 상태"를 만들어 내므로, 그 필드의 서버측 투영은
--   테스트로 고정해야 한다(클라이언트가 못 잡는다). tests/test_findings_search_rpc.py 의
--   ProjectionCoversClientFieldsTest 가 정본 가드다 — 3필드를 하드코딩하는 대신 findings.js
--   의 FIELDS 선언을 파싱해 교차검증하므로 **다음에 추가될 필드의 투영 누락**까지 잡는다.
--
-- ★좁은 작업집합 계약(026 §"★★")은 그대로 지킨다: 세 필드는 searched CTE 가 아니라
--   **page_rows** — 페이지의 24문서분만 PK 로 되읽는 지점 — 에만 추가한다. 짧은 컬럼이라
--   스필 위험은 없지만 searched 에 넣을 이유도 없다. (027 이 firm_key 를 searched 에 넣은
--   것은 top_firms 집계라는 **다른 목적** 때문이고, 이 파일의 firm_key 는 카드 렌더용이라
--   목적이 다르다 — 같은 컬럼이지만 두 소비처가 독립이다.)
--
-- 아래는 027 의 헤더를 그대로 승계한다(설계 근거·실측 수치 전부 유효).
-- ----------------------------------------------------------------------------
-- 027_findings_search_dash_axes.sql — [FIND-1] canonical search 에 대시보드 축 추가
--
-- ★왜: PR-B(findings.js 를 RPC 소비로 전환) 착수 직전에 발견했다. 새 모델에는 클라이언트에
--   `matched`(필터링된 전체 행 배열)가 **존재하지 않는다** — 서버가 페이지의 24문서만 주기
--   때문이다. 그런데 renderDash() 는 그 `matched` 로부터 **기관 분포(agency)** 와 **업체
--   상위(top firms)** 를 계산한다(findings.js:732-800). 026 의 facets 5축(source/category/
--   month/evidence/review_status)에는 이 둘이 없어, 그대로 전환하면 대시보드 두 블록이
--   깨진다. 즉 **026 만으로는 PR-B 가 성립하지 않는다.**
--
-- ★facets 와 dash 는 의미가 다른 두 집계다(둘 다 필요):
--   · facets = 셀렉트 드롭다운용. **자기 축을 뺀** 나머지 필터로 센다(표준 파세팅).
--     "소스를 MFDS 로 바꾸면 몇 건이 되나"를 보여줘야 하므로 자기 축을 빼야 한다.
--     클라이언트 computeFacetCounts(row, exclude) 와 동일 의미.
--   · dash   = 대시보드용. **현재 결과 집합의 분포**라 필터를 전량 적용해서 센다.
--     클라이언트 renderDash(matched) → computeStats(matched) 와 동일 의미.
--   필터가 하나도 없으면 둘은 같은 값이 된다 — 그래서 종전 코드가 "무필터일 때만
--   findings_stats 전역 truth 로 바꿔치기"해도 앞뒤가 맞았다.
--
-- ★업체는 파셋 축이 아니다: toggleFirmFilter(name) 는 `state.q = name` 으로 **검색어를
--   설정**한다. 그래서 top_firms 에는 자기 축 제외 규칙이 없고 filtered 전량 기준으로 센다
--   — 종전 computeFirmTop(matched) 와 동일.
--
-- ★firm_key 로 묶는다(firm_name 아님): 017 이 top_firms 를 firm_key 정규화 기준으로 바꿨고
--   025 가 계승했다. 종전 클라이언트는 무필터일 때 RPC(firm_key 기준)를, 필터 시
--   computeFirmTop(firm_name 기준)을 써서 **기준이 갈리는 잠복 불일치**가 있었다(같은 회사
--   표기 변형이 필터 여부에 따라 합쳐졌다 흩어졌다 했다). 서버 일원화로 firm_key 로 통일한다.
--
-- 아래는 026 의 헤더를 그대로 승계한다(설계 근거·실측 수치 전부 유효).
-- ----------------------------------------------------------------------------
-- 026_findings_search.sql — [FIND-1] 서버 canonical search
--
-- 025 §⑤ 이월 항목의 이행: 검색·필터·정렬·페이지네이션·파셋 집계의 정본을 서버 RPC 로
-- 옮긴다. 클라이언트(web/assets/findings.js)는 결과를 **소비만** 한다.
--
-- ★왜: Codex 재감사가 찾은 "화면 FDA 483 (910) vs DB 진실 8,078" 은 숫자 표시 버그가
--   아니라 구조 문제의 증상이었다 — 클라이언트가 최신 1,000행만 로드한 뒤 그 위에서
--   검색·필터·정렬·집계를 전부 수행했기 때문이다. 025 는 "틀린 숫자를 숨기는" 응급
--   처치였고(§①②), 아래가 그대로 남아 있었다:
--     · 발행월 옵션이 로드분에 있는 월만 노출
--     · `오래된순`·`업체명순`이 전역 정렬이 아니라 비활성화로 회피
--     · 검색·필터가 로드분 안에서 24문서를 채우면 나머지 코퍼스를 조회조차 안 함
--   전부 같은 뿌리(ROWS 의존)이고, 회피책은 기능을 깎아서 정직해진 것이지 고쳐진 게
--   아니다. 이 파일이 그 뿌리를 제거한다.
--
-- 적용 순서(중요): 이 마이그레이션을 **먼저** 프로덕션에 적용하고, 그 다음에 findings.js
--   전환(PR-B)을 머지한다. web/migrations/*.sql 는 자동 적용되지 않는데(사람이 SQL
--   Editor) Cloudflare Pages 는 머지 즉시 배포되므로, JS 를 먼저 내보내면 RPC 없는
--   사이트가 라이브가 된다. 적용 직후 이 파일은 **호출자가 없는 상태**로 대기한다
--   (019 가 확립한 "적재→검증→전환" 패턴의 1단계 = 되돌리기 비용 0).
--
-- ★security invoker (관례 이탈, 의도적):
--   기존 findings RPC 는 전부 security definer 라서 공개 게이트 술어
--   `(finding_text_ko <> '' or finding_language = 'KO') and scope_status = 'ok'` 를
--   각자 WHERE 절에 손으로 복제한다 — 현재 정책 1곳(010) + RPC 5곳 = 6중복. 게이트가
--   바뀌면 6곳을 동기화해야 하고 하나 빠뜨리면 비공개 데이터 노출이다.
--   이 파일의 두 함수는 **findings 테이블만** 읽고, anon 은 이미 `grant select on
--   findings`(003) + RLS 정책(010)을 갖고 있다. 따라서 invoker 로 만들면 RLS 가 자동
--   적용되어 게이트 복제가 아예 불필요해진다 — 게이트의 단일 진실이 정책 하나로 돌아온다.
--   findings_stats 가 definer 인 건 raw_signals(anon 전면 차단) 카운트 때문이고,
--   여기엔 그 필요가 없다. search_path 고정은 invoker 에서도 그대로 유지한다.
--   ※ 그래서 이 함수를 service_role/postgres 로 호출하면 RLS 가 적용되지 않아 비공개
--     행까지 보인다(정상). 게이트 검증은 **반드시 anon 키로 PostgREST 를 통해** 한다.
--
-- ★검색 semantics = ILIKE 부분일치 유지 (FTS 로 바꾸지 않는다):
--   018 이 이미 FTS GIN 인덱스를 만들어 뒀지만 재사용하면 **안 된다**. 현재 클라이언트
--   검색은 String.indexOf 부분일치다. FTS 'simple' 사전은 한국어 형태소를 모르므로
--   (018:87-92 가 실측: websearch 기본 AND 는 표본 질의 3종 전부 0건) `무균` 질의가
--   `무균실`·`무균의`·`무균 공정` 을 놓친다 = 조용한 검색 축소. 정확한 숫자를 얻으려고
--   검색 품질을 떨어뜨리면 순손실이다. 018 의 FTS 인덱스는 findings_similar(유사 문구
--   검색) 전용으로 그대로 둔다 — 두 기능은 목적이 다르다.
--   LIKE 와일드카드(%·_·\)는 이스케이프한다 — indexOf 는 이들을 리터럴로 취급하므로
--   이스케이프하지 않으면 semantics 가 갈린다.
--
-- ★trgm 인덱스는 이 파일에 넣지 않는다(2단계로 미룸):
--   blob ILIKE seq scan 이 지배 비용(라이브 실측 69ms/9,292행)이고 코퍼스에 선형이다.
--   gin_trgm_ops 는 ILIKE semantics 를 바꾸지 않으면서 가속하므로 언제든 추가할 수 있다.
--   018:36 이 trgm 인덱스를 거부한 근거("쓰이지 않는데 매일 백필 insert 마다 쓰기 증폭만
--   유발")는 이제 쓰이므로 뒤집혔지만, 023 전량 백필이 커넥터 타임아웃→롤백된 교훈을
--   존중해 **라이브 p95 를 측정한 뒤** 별도 마이그레이션으로 추가한다. 현재 전체 형태
--   실측 128ms 는 감당 가능하다.
--
-- ★문서 단위 정렬의 전제(라이브 실측 2026-07-16):
--   공개 문서 1,356건 · 지적 8,168건 · avg 6.02/doc · max 46
--   docs_multi_date 0 · docs_multi_firm 0 · docs_multi_source 0
--   즉 raw_signal_id 하나가 **단일** published_date/firm_name/source 를 갖는다(위반 0).
--   클라이언트 buildDocHead() 가 rows[0] 을 문서 대표값으로 쓰는 가정이 실측으로 확인됐고,
--   따라서 서버가 문서 단위로 정렬·페이지네이션할 수 있다. 이 전제가 깨지면 문서 대표값이
--   임의값이 되므로 collector 측 불변식 테스트로 고정한다.
--
-- 정렬 결정론: 전 정렬에 min(finding_id) 최종 타이브레이크를 둔다. 022 가 fp16 동률로
--   데인 결함(타이브레이크 없는 order by → 평가 29슬롯 미판정)의 재발 방지다. 현
--   클라이언트 firm_asc 는 최종 타이브레이크가 없어 JS sort 안정성에 우연히 기대고 있다.
-- 한글 정렬: DB 기본 collate 는 en_US.UTF-8 이라 클라이언트 localeCompare 와 한글 업체명
--   순서가 갈린다. ICU ko-KR-x-icu 가 사용 가능(실측)하므로 firm_asc 에 명시한다.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- (A) findings_search — 검색·필터·정렬·페이지네이션·파셋의 단일 정본
-- ---------------------------------------------------------------------------
create or replace function public.findings_search(
  p_q             text default '',
  p_source        text default '',
  p_category      text default '',
  p_month         text default '',
  p_evidence      text default '',
  p_review_status text default '',
  p_agency        text default '',
  p_sort          text default 'date_desc',
  p_page          int  default 1,
  p_docs_per_page int  default 24
) returns jsonb
language sql
stable
security invoker
set search_path = public, extensions
as $$
with p as (
  -- 입력은 전부 서버에서 정규화·클램프한다 — 클라이언트를 신뢰하지 않는다.
  select
    coalesce(btrim(p_q), '')                                       as q,
    -- LIKE 와일드카드 이스케이프(기본 escape = 백슬래시). indexOf 는 %·_ 를 리터럴로
    -- 취급하므로 이스케이프해야 semantics 가 일치한다. 백슬래시를 **먼저** 치환해야
    -- 뒤에 삽입한 이스케이프 문자를 다시 이스케이프하지 않는다.
    replace(replace(replace(coalesce(btrim(p_q), ''), '\', '\\'), '%', '\%'), '_', '\_')
                                                                   as q_esc,
    coalesce(p_source, '')                                         as f_source,
    coalesce(p_category, '')                                       as f_cat,
    coalesce(p_month, '')                                          as f_month,
    coalesce(p_evidence, '')                                       as f_ev,
    coalesce(p_review_status, '')                                  as f_rs,
    coalesce(p_agency, '')                                         as f_agency,
    case when p_sort in ('date_desc', 'date_asc', 'firm_asc')
         then p_sort else 'date_desc' end                          as sort,
    greatest(coalesce(p_page, 1), 1)                               as page,
    least(greatest(coalesce(p_docs_per_page, 24), 1), 100)         as per
),
-- 검색만 적용한 공통 베이스. 필터는 아직 걸지 않는다 — 파셋이 "자기 축만 제외하고 나머지
-- 필터 적용"(표준 파세팅)을 하려면 검색이 공통 베이스여야 하기 때문이다.
--
-- ★★좁은 작업집합이 핵심이다. 이 CTE 는 **본문 텍스트(finding_text/finding_text_ko)를
--   싣지 않는다** — 필터·파셋·문서묶음에 필요한 식별/분류 컬럼만 투영한다. 본문은 페이지의
--   24문서분만 아래 page_rows 에서 PK 로 되읽는다.
--   근거(라이브 실측): 본문을 이 CTE 에 실었더니 **무검색 랜딩**(= q='' 이라 8,168행 전량이
--   통과하는 최악·최빈 경로)에서 CTE materialize 가 temp 파일로 스필해 **659ms**가 나왔다
--   (temp read=3,714 written=1,238). 검색 시에는 2,454행만 남아 메모리에 들어가서 이 결함이
--   숨어 있었다 — 즉 "검색만 재보면 통과하는" 함정이었다. 좁힌 뒤 **127.7ms·스필 0**.
--   교훈: 넓은 텍스트를 CTE 로 물고 다니지 말고, 페이지분만 늦게 가져와라.
-- ★blob 에 쓰이는 컬럼(category_label_ko·document_id·translation_method·cfr_refs·
--   mfds_refs)은 WHERE 절에서 f 를 직접 참조하므로 select 목록에 없어도 된다.
-- ★공개 게이트를 여기 쓰지 않는다 — security invoker 라 RLS(010)가 자동 적용된다.
-- 027: firm_key 추가(top_firms 집계용). 좁은 CTE 계약은 유지된다 — firm_key 는 짧은
--      generated 컬럼(013)이라 본문 텍스트와 달리 스필을 유발하지 않는다.
searched as (
  select
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.published_date, f.firm_name,
    f.firm_key, f.category_code, f.evidence_level, f.review_status,
    left(f.published_date, 7) as month
  from public.findings f, p
  where p.q = ''
     or (
          -- 검색 대상 blob. ★이 컬럼 목록이 "무엇이 검색되는가"의 정본이며 테스트가
          --   고정한다(조용한 축소 방지). 클라이언트 상수 라벨(EVIDENCE_LABEL "증거 A",
          --   STATUS_LABEL "검토 필요", 영문 카테고리명)은 **의도적으로 제외**한다 —
          --   셋 다 드롭다운 필터로 대체 가능하고, 클라이언트 상수를 SQL 에 복제하는 것은
          --   "서버가 정본" 원칙에 정면 배치되기 때문이다.
          -- month 는 별도로 넣지 않는다 — published_date 가 blob 에 있어 '2026-07' 이
          --   부분일치로 잡힌다.
          coalesce(f.finding_text_ko, '')    || ' ' ||
          coalesce(f.finding_text, '')       || ' ' ||
          coalesce(f.firm_name, '')          || ' ' ||
          coalesce(f.category_code, '')      || ' ' ||
          coalesce(f.category_label_ko, '')  || ' ' ||
          coalesce(f.document_id, '')        || ' ' ||
          coalesce(f.agency, '')             || ' ' ||
          coalesce(f.source, '')             || ' ' ||
          coalesce(f.published_date, '')     || ' ' ||
          coalesce(f.evidence_level, '')     || ' ' ||
          coalesce(f.review_status, '')      || ' ' ||
          coalesce(f.translation_method, '') || ' ' ||
          coalesce(f.cfr_refs::text, '')     || ' ' ||
          coalesce(f.mfds_refs::text, '')
        ) ilike '%' || p.q_esc || '%'
),
-- 필터 전량 적용분 = 결과 목록·totals 의 모집단.
filtered as (
  select s.* from searched s, p
  where (p.f_source = '' or s.source          = p.f_source)
    and (p.f_cat    = '' or s.category_code   = p.f_cat)
    and (p.f_month  = '' or s.month           = p.f_month)
    and (p.f_ev     = '' or s.evidence_level  = p.f_ev)
    and (p.f_rs     = '' or s.review_status   = p.f_rs)
    and (p.f_agency = '' or s.agency          = p.f_agency)
),
-- 문서 단위 집약. min() 은 §2.1 실측(문서당 단일값)에 근거 — max() 와 동일하다.
docs as (
  select
    f.raw_signal_id,
    min(f.published_date) as pub,
    min(f.firm_name)      as firm,
    min(f.finding_id)     as tie,
    count(*)::int         as doc_findings
  from filtered f
  group by f.raw_signal_id
),
ordered as (
  select
    d.raw_signal_id,
    row_number() over (
      order by
        -- 선택되지 않은 정렬의 CASE 는 전 행 NULL(상수)이라 순서에 영향이 없다.
        (case when p.sort = 'firm_asc' then d.firm end) collate "ko-KR-x-icu" asc nulls last,
        (case when p.sort = 'date_asc' then d.pub  end) asc  nulls last,
        (case when p.sort = 'date_desc' then d.pub end) desc nulls last,
        (case when p.sort = 'firm_asc' then d.pub  end) desc nulls last,
        d.tie asc   -- ★전 정렬 공통 최종 타이브레이크(022 교훈)
    )::int as rn
  from docs d, p
),
tot as (
  select
    (select count(*) from docs)::int                        as doc_total,
    (select coalesce(sum(doc_findings), 0) from docs)::int  as finding_total
),
page_docs as (
  select o.raw_signal_id, o.rn
  from ordered o, p
  where o.rn > (p.page - 1) * p.per
    and o.rn <= p.page * p.per
),
-- 페이지에 실제로 나갈 행 = (매치된 지적) ∩ (이 페이지의 24문서). 여기서 **처음으로**
-- 본문 텍스트를 findings 에서 PK 로 되읽는다 — 65행 안팎이라 Index Scan 이고, 넓은 텍스트가
-- 앞선 CTE 들을 통과하지 않으므로 스필이 없다(실측 Index Scan using findings_pkey).
-- ★findings 를 다시 읽으므로 RLS 가 한 번 더 적용된다 = 게이트 일관성이 공짜로 보장된다.
-- 028: firm_key/translation_method/confidence 를 **여기에** 추가한다 — 클라이언트 카드
--      조립부(buildDocHead/appendOrigAndNote/appendMetaLine)가 읽는 필드이고, 카드는 이
--      페이지의 24문서분만 렌더하므로 필요한 범위가 정확히 page_rows 다. searched 에 넣지
--      않는 이유는 파일 헤더 "★좁은 작업집합 계약" 참조.
page_rows as (
  select
    fl.rn,
    f.finding_id, f.raw_signal_id, f.source, f.agency, f.document_id, f.published_date,
    f.firm_name, f.category_code, f.category_label_ko, f.finding_text, f.finding_text_ko,
    f.evidence_level, f.review_status, f.evidence_url, f.cfr_refs, f.mfds_refs,
    f.firm_key, f.translation_method, f.confidence
  from (
    select fi.finding_id, fi.raw_signal_id, pd.rn
    from filtered fi
    join page_docs pd on pd.raw_signal_id = fi.raw_signal_id
  ) fl
  join public.findings f on f.finding_id = fl.finding_id
),
page_docs_full as (
  select
    pr.rn,
    pr.raw_signal_id,
    min(pr.firm_name)      as firm_name,
    min(pr.source)         as source,
    min(pr.agency)         as agency,
    min(pr.published_date) as published_date,
    min(pr.document_id)    as document_id,
    min(pr.evidence_url)   as evidence_url,
    -- 028: 문서 레벨 firm_key. buildDocHead 는 rows[0].firm_key(=findings[] 쪽)를 읽으므로
    -- 렌더에는 findings[] 만 있어도 동작하지만, 문서 대표값 묶음(firm_name/source/…)에
    -- firm_key 만 빠져 있으면 계약이 불명확하다 — 다른 대표값과 같은 min() 규칙으로 싣는다
    -- (026 §"문서 단위 정렬의 전제" 실측 docs_multi_firm 0 에 근거 = max() 와 동일).
    min(pr.firm_key)       as firm_key,
    count(*)::int          as matched_findings,
    -- 문서 내 지적 순서 = finding_id asc. 현재 클라이언트가 보는 순서(서버가
    -- published_date desc, finding_id asc 로 보내고 문서 내 날짜는 동일)와 같다.
    jsonb_agg(
      jsonb_build_object(
        'finding_id',        pr.finding_id,
        'raw_signal_id',     pr.raw_signal_id,
        'source',            pr.source,
        'agency',            pr.agency,
        'document_id',       pr.document_id,
        'published_date',    pr.published_date,
        'firm_name',         pr.firm_name,
        'category_code',     pr.category_code,
        'category_label_ko', pr.category_label_ko,
        'finding_text',      pr.finding_text,
        'finding_text_ko',   pr.finding_text_ko,
        'evidence_level',    pr.evidence_level,
        'review_status',     pr.review_status,
        'evidence_url',      pr.evidence_url,
        'cfr_refs',          pr.cfr_refs,
        'mfds_refs',         pr.mfds_refs,
        -- 028: 클라이언트 카드 조립부가 읽는 3종(파일 헤더 결함 설명 참조).
        'firm_key',           pr.firm_key,
        'translation_method', pr.translation_method,
        'confidence',         pr.confidence
      ) order by pr.finding_id
    ) as findings
  from page_rows pr
  group by pr.rn, pr.raw_signal_id
),
-- 파셋 = 표준 파세팅: 각 축은 **자기 자신을 뺀** 나머지 필터를 적용해 센다.
-- 클라이언트 computeFacetCounts(row, exclude) 와 동일 의미다.
-- 단위는 **지적(행) 수** — 현 화면·findings_stats.by_source 와 같은 단위다(FDA 483 = 8,078).
-- 라이브 실측상 축당 ~2ms 라 5축 전부 켜도 사실상 공짜다.
fac_source as (
  select s.source as v, count(*)::int as c from searched s, p
  where (p.f_cat = '' or s.category_code = p.f_cat) and (p.f_month = '' or s.month = p.f_month)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.source
),
fac_cat as (
  select s.category_code as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_month = '' or s.month = p.f_month)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.category_code
),
fac_month as (
  select s.month as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_ev = '' or s.evidence_level = p.f_ev) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.month
),
fac_ev as (
  select s.evidence_level as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_rs = '' or s.review_status = p.f_rs)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.evidence_level
),
fac_rs as (
  select s.review_status as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_agency = '' or s.agency = p.f_agency)
  group by s.review_status
),
-- 027: agency 파셋 축(자기 축 제외). 화면 컨트롤은 M14 에서 빠졌지만 state.agency·URL
-- 파라미터·필터 술어는 하위호환으로 살아 있으므로 축 자체는 표준 파세팅을 따른다.
fac_agency as (
  select s.agency as v, count(*)::int as c from searched s, p
  where (p.f_source = '' or s.source = p.f_source) and (p.f_cat = '' or s.category_code = p.f_cat)
    and (p.f_month = '' or s.month = p.f_month) and (p.f_ev = '' or s.evidence_level = p.f_ev)
    and (p.f_rs = '' or s.review_status = p.f_rs)
  group by s.agency
),
-- ---------------------------------------------------------------------------
-- 027: 대시보드 집계 — **filtered 전량 기준**(자기 축 제외 없음).
-- 위 fac_* 와 달리 "현재 결과 집합의 분포"라서 필터를 전부 적용한 모집단을 센다
-- (= 종전 renderDash(matched) 의 computeStats(matched) 와 동일 의미).
-- ---------------------------------------------------------------------------
dash_agency as (
  select f.agency as v, count(*)::int as c from filtered f group by f.agency
),
dash_cat as (
  select f.category_code as v, count(*)::int as c from filtered f group by f.category_code
),
dash_month as (
  select f.month as v, count(*)::int as c from filtered f group by f.month
),
-- 종전 클라이언트는 상위 5곳만 그렸지만(renderDashFirms 가 slice(0,5)) 서버는 10곳을 주고
-- 자르기는 클라이언트에 맡긴다 — 표시 개수는 UI 결정이지 데이터 계약이 아니다.
--
-- ★대표 표시명은 025 의 lateral 규칙을 그대로 미러링한다: **최빈 → 최장 → 알파벳**.
--   min(firm_name) 으로 골랐더니 건수·순서는 025 와 완전히 같은데 표기만 갈렸다
--   (실측: `Hospira Inc` vs 025 의 `Hospira Inc.`, `PharMEDium Services LLC` vs
--   `PharMEDium Services, LLC`). 같은 화면의 같은 블록이 필터 유무로 표기가 바뀌면
--   사용자에겐 다른 회사처럼 보인다 — 규칙을 맞춰 그 회귀를 없앤다.
-- ★대표명을 filtered(카운트와 같은 모집단)에서 고른다 — 이름과 숫자가 항상 같은 집합을
--   가리킨다. 무필터면 025 와 동일한 값이 된다(범위 내 미번역 0건이라 모집단도 같다).
dash_firms as (
  select g.firm_key as k, dn.firm_name as name, g.c
  from (
    select f.firm_key, count(*)::int as c
    from filtered f
    where coalesce(f.firm_key, '') <> ''
    group by f.firm_key
    order by count(*) desc, f.firm_key asc   -- 025 와 동일한 결정론 순서
    limit 10
  ) g
  join lateral (
    select f2.firm_name
    from filtered f2
    where f2.firm_key = g.firm_key
    group by f2.firm_name
    order by count(*) desc, length(f2.firm_name) desc, f2.firm_name asc
    limit 1
  ) dn on true
)
select jsonb_build_object(
  'documents', coalesce(
      (select jsonb_agg(
         jsonb_build_object(
           'raw_signal_id',    d.raw_signal_id,
           'firm_name',        d.firm_name,
           'firm_key',         d.firm_key,   -- 028: 문서 대표값 묶음의 계약 명확화
           'source',           d.source,
           'agency',           d.agency,
           'published_date',   d.published_date,
           'document_id',      d.document_id,
           'evidence_url',     d.evidence_url,
           'matched_findings', d.matched_findings,
           'findings',         d.findings
         ) order by d.rn
       ) from page_docs_full d),
      '[]'::jsonb),
  'totals', jsonb_build_object(
      'documents', (select doc_total from tot),
      'findings',  (select finding_total from tot)),
  'facets', jsonb_build_object(
      'by_source',        coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_source), '[]'::jsonb),
      'by_category',      coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_cat),    '[]'::jsonb),
      'by_month',         coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v desc)          from fac_month),  '[]'::jsonb),
      'by_evidence',      coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v asc)           from fac_ev),     '[]'::jsonb),
      'by_review_status', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_rs),     '[]'::jsonb),
      'by_agency',        coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from fac_agency), '[]'::jsonb)),
  -- 027: 대시보드 전용 블록. facets 와 키 이름이 겹치지만 **모집단이 다르다**(위 주석 참조).
  'dash', jsonb_build_object(
      'by_agency',   coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from dash_agency), '[]'::jsonb),
      'by_category', coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by c desc, v asc) from dash_cat),    '[]'::jsonb),
      -- 월은 오름차순(클라이언트 computeMonthTrend 관례 = 최근 12개를 뒤에서 자른다).
      'by_month',    coalesce((select jsonb_agg(jsonb_build_object('v', v, 'c', c) order by v asc)          from dash_month),  '[]'::jsonb),
      'top_firms',   coalesce((select jsonb_agg(jsonb_build_object('firm_key', k, 'firm_name', name, 'c', c) order by c desc, k asc) from dash_firms), '[]'::jsonb)),
  'page',          (select page from p),
  'docs_per_page', (select per from p),
  'pages',         (select case when (select per from p) > 0
                                then ((select doc_total from tot) + (select per from p) - 1) / (select per from p)
                                else 0 end),
  'sort',          (select sort from p)
);
$$;

comment on function public.findings_search(text, text, text, text, text, text, text, text, int, int) is
  '[FIND-1] /findings/ 의 canonical search — 검색(ILIKE 부분일치)·필터·정렬·문서 단위 '
  '페이지네이션·파셋 집계의 단일 정본. security invoker 라 공개 게이트는 RLS(010)가 강제한다. '
  '025 §⑤ 이행.';
-- ---------------------------------------------------------------------------
-- (B) findings_document — 딥링크 1회 해석. 026 정의 + 투영 3종(028)
-- ---------------------------------------------------------------------------
-- 027 은 이 함수를 재선언하지 않았다(대시보드 축과 무관했으므로 — 옳은 판단이었다). 028 은
-- 재선언한다: 투영 누락이 findings_search 와 findings_document 에 **동일하게** 있고, 딥링크
-- 단독 렌더도 같은 카드 조립 코드(buildDocHead/appendOrigAndNote/appendMetaLine)를 타므로
-- 한쪽만 고치면 "목록에선 보이는데 딥링크로 열면 사라지는" 더 나쁜 불일치가 된다.
--
-- 026 정의를 그대로 승계하며 rows_out jsonb 에 3키만 추가한다. rows_out 은 `select f.*` 라
-- 세 컬럼이 이미 실려 있다 — 결함은 **투영(jsonb_build_object)에만** 있었다.
--
-- 비공개/부재는 구분 없이 빈 결과다 — "존재 여부 정보 누설 금지" 계약(findings.js:1444)
-- 을 서버에서 유지한다. invoker+RLS 라 자동으로 성립한다: 비공개 행은 RLS 가 거르므로
-- 함수는 그 행이 존재하는지조차 알 수 없다.
create or replace function public.findings_document(p_finding_id text)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
with anchor as (
  select f.raw_signal_id
  from public.findings f
  where f.finding_id = coalesce(p_finding_id, '')
  limit 1
),
rows_out as (
  select f.*
  from public.findings f, anchor a
  where f.raw_signal_id = a.raw_signal_id
)
select case when not exists (select 1 from anchor) then 'null'::jsonb
else jsonb_build_object(
  'raw_signal_id',  (select raw_signal_id from anchor),
  'firm_name',      (select min(firm_name) from rows_out),
  -- 028: 문서 레벨 firm_key — findings_search 의 documents[] 와 같은 계약(같은 min() 규칙).
  'firm_key',       (select min(firm_key) from rows_out),
  'source',         (select min(source) from rows_out),
  'agency',         (select min(agency) from rows_out),
  'published_date', (select min(published_date) from rows_out),
  'document_id',    (select min(document_id) from rows_out),
  'evidence_url',   (select min(evidence_url) from rows_out),
  'findings', coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'finding_id',        r.finding_id,
        'raw_signal_id',     r.raw_signal_id,
        'source',            r.source,
        'agency',            r.agency,
        'document_id',       r.document_id,
        'published_date',    r.published_date,
        'firm_name',         r.firm_name,
        'category_code',     r.category_code,
        'category_label_ko', r.category_label_ko,
        'finding_text',      r.finding_text,
        'finding_text_ko',   r.finding_text_ko,
        'evidence_level',    r.evidence_level,
        'review_status',     r.review_status,
        'evidence_url',      r.evidence_url,
        'cfr_refs',          r.cfr_refs,
        'mfds_refs',         r.mfds_refs,
        -- 028: 클라이언트 카드 조립부가 읽는 3종 — findings_search 의 findings[] 와 동일 계약.
        'firm_key',           r.firm_key,
        'translation_method', r.translation_method,
        'confidence',         r.confidence
      ) order by r.finding_id
    ) from rows_out r), '[]'::jsonb)
) end;
$$;

comment on function public.findings_document(text) is
  '[FIND-1] 딥링크 1회 해석 — finding_id 로 그 지적이 속한 문서 전체를 반환. 비공개/부재는 '
  '구분 없이 null(존재 여부 누설 금지). security invoker + RLS(010)가 게이트를 강제한다.';


-- ---------------------------------------------------------------------------
-- (C) 권한
-- ---------------------------------------------------------------------------
-- create or replace 는 기존 grant 를 보존하지만, 이 파일만 단독으로 fresh DB 에 적용해도
-- 성립하도록 두 함수의 grant 를 재선언한다(멱등). 028 은 findings_document 도 만들므로
-- 027 과 달리 양쪽을 grant 한다.
grant execute on function public.findings_search(text, text, text, text, text, text, text, text, int, int) to anon, authenticated;
grant execute on function public.findings_document(text) to anon, authenticated;

-- ============================================================================
-- 검증 (사람 실행용)
-- ============================================================================
-- 적용 순서: 이 마이그레이션을 **먼저** 프로덕션 SQL Editor 에 적용한 뒤 아래로 확인한다
--   (web/migrations/*.sql 는 자동 적용되지 않는다). 반환 **추가**만 하므로 기존 클라이언트와
--   하위호환이다 = 적용 시점에 깨지는 것이 없고, 되돌리기는 027 재적용이면 된다.
--
-- ★반드시 **anon 키로 PostgREST** 를 통해 확인한다 — SQL Editor 는 service_role/postgres 라
--   RLS 가 적용되지 않아 게이트를 검증할 수 없다(026 헤더 "★security invoker" 참조).
--
-- 1) ★이 마이그레이션의 본 목적 — findings[] 에 3키가 실리는가
--    curl -s "$URL/rest/v1/rpc/findings_search" -H "apikey: $ANON" \
--         -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--         -d '{"p_q":"","p_page":1}' \
--      | jq '.documents[0].findings[0] | {firm_key, translation_method, confidence}'
--    기대: 3키 모두 존재. firm_key 는 비어 있지 않은 slug, confidence 는 0~1 실수.
--          (translation_method 는 'llm_assisted' 또는 다른 값 — 값 자체보다 **키 존재**가 계약)
--
-- 2) 문서 레벨 firm_key 도 실리는가
--    … | jq '.documents[0] | {firm_name, firm_key}'
--
-- 3) 딥링크(findings_document)도 같은 3키를 싣는가 — 목록/딥링크 불일치 방지
--    FID=$(curl -s "$URL/rest/v1/rpc/findings_search" -H "apikey: $ANON" \
--          -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--          -d '{"p_q":"","p_page":1}' | jq -r '.documents[0].findings[0].finding_id')
--    curl -s "$URL/rest/v1/rpc/findings_document" -H "apikey: $ANON" \
--         -H "Authorization: Bearer $ANON" -H 'Content-Type: application/json' \
--         -d "{\"p_finding_id\":\"$FID\"}" \
--      | jq '{firm_key, f: (.findings[0] | {firm_key, translation_method, confidence})}'
--
-- 4) 회귀 없음 — totals/facets 가 027 과 동일한가 (게이트 누출 0 재확인)
--    … -d '{"p_q":"","p_page":1}' | jq '.totals'
--    기대: totals.findings = 8,168 (공개분과 정확히 일치 = 비공개 0건 누출)
--
-- 5) 성능 회귀 없음 — 무검색 랜딩(최악·최빈 경로)이 026 실측 대역(~128ms)을 유지하는가.
--    세 컬럼 전부 page_rows(24문서분)에만 추가했으므로 스필 유발 요인이 아니다.
--    explain (analyze, buffers) select findings_search('','','','','','','','date_desc',1,24);
--    기대: temp read/written 없음(스필 0).
