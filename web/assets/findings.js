/* GRM 지적사항 검색 (FIND-1 M3c, 국문 우선표시+카테고리 라벨 M6d, 대시보드 밴드 M7,
 * 카드 UX 오버홀 M10b, 탐색 툴바 오버홀 M10c, 문서 중심 열람 재편) — 정적·클라이언트사이드,
 * 순수 fetch(PostgREST 직접 호출).
 *
 * [서버 canonical search] 검색·필터·정렬·문서묶음·페이지네이션·파셋·대시보드 집계의 정본은
 * findings_search RPC(026/027)다 — 이 파일은 **결과를 소비만** 한다. 클라이언트가 코퍼스
 * 일부를 로드해 그 위에서 집계하면 화면 숫자가 DB 진실과 갈린다(실측: 화면 FDA 483 (910)
 * vs DB 8,078). 따라서 전역 행 배열을 두지 않는다 — 화면은 LAST.documents(서버가 이미
 * 잘라 보낸 현재 페이지)만 그린다. 검색어/필터/정렬/페이지가 바뀌면 새로 요청한다.
 *
 * [문서 중심 열람] 열람 단위는 observation 조각이 아니라 문서·업체다(Redica 등 상용
 * 규제 인텔리전스 검증 패턴) — 서버가 raw_signal_id 로 묶어 보낸 문서 1건을 buildDocCard()
 * 문서 카드 1장으로 렌더한다. observation 단위 카드(buildCard()) 자체는 무변경으로 문서
 * 카드 내부에 재사용된다. documents[].findings 는 **매치된 지적만** 담는다(문서 전체가
 * 아니다) — matched_findings 가 그 개수다.
 *
 * supabase-js CDN 미사용 — 인증 불필요한 anon 호출 뿐이라 REST 엔드포인트를 직접 fetch 한다.
 * findings_search/findings_document 는 security invoker 라 공개 게이트를 RLS(010)가 강제한다
 * — 클라이언트가 게이트 술어를 복제하지 않는다.
 * cfg(url/key) 는 템플릿의 #grm-findings-cfg data-속성(env-param, 미설정이면 빈 문자열)에서
 * 읽는다. 둘 중 하나라도 없으면 오류가 아니라 "준비 중" 안내로 조용히 종료한다(정적 페이지
 * 골든 결정론 — env 값과 무관하게 findings.html 자체 출력은 항상 동일 byte).
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 에 데이터 삽입 금지) — findings
 * 는 원문에서 자동 추출한 자유 텍스트라 이스케이프 누락 시 XSS 위험이 크다(archive.js 의
 * search-index.json 은 렌더러가 이미 생성한 신뢰 데이터라 다른 계약). 매칭어 하이라이트
 * (M10b P1)도 이 계약을 지킨다 — appendHighlighted() 는 text node 분할 + createElement
 * ("mark") 조립로만 구현하고, innerHTML/정규식 치환 문자열 삽입은 쓰지 않는다.
 *
 * [동기화 규칙] CATEGORY_LABELS 는 grm_findings.FINDING_TAXONOMY 의 20개 code/label_ko/
 * label_en 과 완전히 일치해야 한다 — web/tests/test_render.py 가 대조 테스트로 강제한다.
 */
(function () {
  "use strict";

  var cfg = document.getElementById("grm-findings-cfg");
  var loadingEl = document.getElementById("fnd-loading");
  var errorEl = document.getElementById("fnd-error");
  var emptyEl = document.getElementById("fnd-empty");
  var resultsEl = document.getElementById("fnd-results");
  var countEl = document.getElementById("fnd-count");
  if (!cfg || !loadingEl || !errorEl || !emptyEl || !resultsEl || !countEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();

  function showState(which) {
    loadingEl.hidden = which !== "loading";
    errorEl.hidden = which !== "error";
    emptyEl.hidden = which !== "empty";
  }

  if (!url || !key) {
    showState("loading");
    loadingEl.textContent = "검색 서비스 준비 중입니다.";
    return;
  }

  // grm_findings.FINDING_TAXONOMY verbatim (code -> {ko, en}). 카드 카테고리 표시=ko,
  // 드롭다운 옵션 표시="{ko} · {en}"(snake_case 코드는 어디에도 노출하지 않는다).
  // v3(2026-07-12): FINDING_TAXONOMY 순서 변경(complaint_recall, computer_system_validation
  // 이동)에 맞춰 선언 순서도 동기화 -- code/label 값 자체는 불변(20개).
  var CATEGORY_LABELS = {
    data_integrity: { ko: "데이터 완전성", en: "Data integrity" },
    computer_system_validation: { ko: "컴퓨터화시스템", en: "Computer system validation" },
    documentation_records: { ko: "문서화/기록관리", en: "Documentation and records" },
    aseptic_sterility_assurance: { ko: "무균보증/무균공정", en: "Aseptic processing and sterility assurance" },
    environmental_monitoring: { ko: "환경모니터링", en: "Environmental monitoring" },
    cleaning_validation: { ko: "세척밸리데이션", en: "Cleaning validation" },
    complaint_recall: { ko: "불만/회수", en: "Complaint and recall handling" },
    deviation_capa: { ko: "일탈/CAPA/조사", en: "Deviation, CAPA, and investigation" },
    quality_unit_oversight: { ko: "품질부서 관리감독", en: "Quality unit oversight" },
    qc_lab_controls: { ko: "시험실/품질관리", en: "Laboratory and QC controls" },
    process_validation: { ko: "공정밸리데이션", en: "Process validation" },
    equipment_facility: { ko: "설비/시설", en: "Equipment and facility" },
    material_supplier_control: { ko: "원자재/공급업체 관리", en: "Material and supplier control" },
    contamination_control: { ko: "오염/교차오염 관리", en: "Contamination control" },
    validation_qualification: { ko: "밸리데이션/적격성평가", en: "Validation and qualification" },
    stability_storage: { ko: "안정성/보관", en: "Stability and storage" },
    labeling_packaging: { ko: "표시/포장", en: "Labeling and packaging" },
    regulatory_reporting: { ko: "규제보고/변경관리", en: "Regulatory reporting and change control" },
    training_personnel: { ko: "교육/작업자", en: "Training and personnel" },
    other_quality_system: { ko: "기타 품질시스템", en: "Other quality system" },
  };

  // [PR-0 딥링크] /findings/?finding_id=finding-<24hex> 공유 URL. 형식은 grm_findings.py:706
  // "finding-" + stable_hash(...)[:24] (sha256 hexdigest 앞 24자, 항상 소문자 hex)와 일치해야
  // 한다 — 형식 불일치는 fetch 없이 곧장 "찾을 수 없음"으로 처리한다(§1).
  var FINDING_ID_RE = /^finding-[0-9a-f]{24}$/;
  var DEEP_LINK_PARAM = "finding_id";

  // [M15] 전면 재설계 — 소스·증거등급·검토상태 칩 그룹을 카테고리·발행월과 동일한
  // <select> 로 통일했다(균일 셀렉트 행). state 키 구조·URL 파라미터·매칭 로직은 M3c
  // 이후 불변 — DOM 배선만 단일 경로(SELECT_FACETS)로 단순화됐다. 옛 칩 파셋 정의는 제거.
  // [M14] 기관(agency) 은 이미 DOM 에서 제거했다 — 소스(FDA 483/FDA Warning Letter/MFDS)가
  // 이미 기관을 포함하는 상위 구분이라 "MFDS" 가 기관·소스 양쪽에 중복 노출되던 혼란을
  // 없앤다. state.agency·URL param(agency)·서버 필터 인자(p_agency)는 그대로 유지한다 —
  // URL 로 agency 파라미터가 들어오면 여전히 필터가 적용된다(하위호환).
  var SELECT_FACETS = [
    ["fnd-f-source", "source"],
    ["fnd-f-evidence", "evidence_level"],
    ["fnd-f-status", "review_status"],
    ["fnd-f-category", "category_code"],
    ["fnd-f-month", "month"],
  ];
  // [M15] 적용 필터 칩 행(#fnd-active) 라벨 — 정렬(sort)은 필터가 아니므로 대상에서
  // 제외한다. 값 포맷터는 selectOptionLabel 과 별개(칩엔 건수 병기가 필요 없다).
  // agency 는 M14 이후 화면 컨트롤이 없지만 URL(?agency=)로는 여전히 걸린다 — 보이지 않는
  // 필터가 결과를 좁히는 상태를 드러내는 것이 이 칩 행의 존재 이유이므로 칩에는 포함한다.
  var ACTIVE_FILTER_DEFS = [
    ["q", "검색", function (v) { return v; }],
    ["agency", "기관", function (v) { return v; }],
    ["source", "소스", function (v) { return v; }],
    ["evidence_level", "증거 등급", function (v) { return EVIDENCE_LABEL[v] || v; }],
    ["review_status", "검토 상태", function (v) { return STATUS_LABEL[v] || v; }],
    ["category_code", "카테고리", function (v) { var c = CATEGORY_LABELS[v]; return c ? c.ko : v; }],
    ["month", "발행월", function (v) { return v; }],
  ];

  var SORT_VALUES = ["date_desc", "date_asc", "firm_asc"];
  var DEFAULT_STATE = {
    q: "", agency: "", category_code: "", source: "", evidence_level: "",
    review_status: "", month: "", sort: "date_desc",
  };
  var state = {
    q: "", agency: "", category_code: "", source: "", evidence_level: "",
    review_status: "", month: "", sort: "date_desc",
  };
  var debounceTimer = null;

  // [서버 canonical search] LAST=가장 최근 findings_search 응답 전체(documents/totals/
  // facets/dash/page/pages/sort). 화면의 모든 숫자·옵션·카드가 여기서만 나온다 — 별도
  // 파생 캐시를 두면 그 순간 두 진실이 생기고, 그게 "화면 910 vs DB 8,078"의 구조적
  // 원인이었다. null=첫 응답 도착 전(가드 필요).
  // DOCS_PER_PAGE=문서 카드 수(서버 p_docs_per_page 로 그대로 넘긴다 — 서버가 1~100 으로
  // 클램프한다). currentPage=1-based 현재 페이지. navToken=연타 방어용 세대 카운터 —
  // ★LAST 대입은 이 토큰 검사를 통과한 응답만 한다(먼저 나간 요청이 늦게 도착해 최신
  // 결과를 덮어쓰는 것을 막는 유일한 장치).
  var LAST = null;
  var DOCS_PER_PAGE = 24;
  var currentPage = 1;
  var navToken = 0;
  // state 키 → findings_search facets/dash 축 이름. 서버 응답의 축 배열은 [{v,c}] 형태다.
  var FACET_AXIS = {
    source: "by_source",
    category_code: "by_category",
    month: "by_month",
    evidence_level: "by_evidence",
    review_status: "by_review_status",
    agency: "by_agency",
  };
  // [선로딩 c] 페이지네이션 버튼(처음/이전/번호/다음/끝) 클릭으로 촉발된 이동에서만 완료
  // 후 결과 목록 상단으로 스크롤한다 — goToPageFromPager() 가 세팅, goToPage() 의 완료
  // 콜백이 소비 후 즉시 리셋한다. 필터/검색/정렬 변경발 goToPage(1) 리셋은 스크롤하지
  // 않는다(검색창에 타이핑할 때마다 화면이 튀는 것을 방지).
  var pendingScrollAfterNav = false;

  // [PR-0 딥링크] deepLinkParam=URL 에서 읽은 원본 finding_id 값 — exitDeepLinkMode() 가
  // 지울 때까지 유지되며(found/notfound 상태와 무관하게 "이 세션에 딥링크 관심사가 아직
  // 살아있다"는 단일 플래그), 파라미터 자체가 없으면 처음부터 null 이라 이하 전 로직이
  // no-op 로 남아 일반 모드 회귀가 0이다. deepLinkStatus="found"|"notfound"|""(미확정/비활성).
  // rowsReady=첫 findings_search 응답 도착 여부 — maybeFinishInit() 이 딥링크 해석 완료와
  // 함께 둘 다 기다렸다가 깜빡임 없이 한 번만 최종 렌더를 확정한다.
  var deepLinkParam = null;
  var deepLinkStatus = "";
  var deepLinkDocRows = null;
  var deepLinkPending = false;
  var rowsReady = false;
  var bannerEl = null;

  // [FIND-1 S1] 유사 문구 검색(렉시컬, 018_findings_similar_lexical.sql RPC 소비) — 정직
  // 표기: trigram+FTS 하이브리드 매칭일 뿐, 뜻을 이해해 찾아주는 방식이 아니다(마이그레이션
  // 주석과 동일 원칙 — UI 명칭은 반드시 "유사 문구 검색" 고정). RPC 는 아직 라이브 DB 에 미적용일 수 있어
  // (컨트롤타워가 별도 적용) 실패/빈 결과는 조용히 기존 키워드 검색으로 폴백한다 — 토글이
  // 켜져 있어도 페이지는 항상 정상 동작해야 한다(§ RPC 미적용 방어). similarFetchToken 은
  // 연타/모드전환 시 오래된 응답을 무시하는 세대 카운터(goToPage() 의 navToken 관례와 동형).
  var SIMILAR_MIN_QUERY_LEN = 2;
  var SIMILAR_LIMIT = 20;
  var similarMode = false;
  var similarToggleBtn = null;
  var similarFetchToken = 0;

  var qInput = document.getElementById("fnd-q");
  var sortSel = document.getElementById("fnd-sort");
  var filtersEl = document.getElementById("fnd-filters");
  var filtersToggleBtn = document.getElementById("fnd-filters-toggle");
  var filtersBadgeEl = document.getElementById("fnd-filters-badge");
  var activeEl = document.getElementById("fnd-active"); // [M15] 적용 필터 칩 행
  var dashToggleBtn = document.getElementById("fnd-dash-toggle");
  var dashGridEl = document.getElementById("fnd-dash-grid");

  // [공개 범위 투명성] 커버리지 노트 — 메인 검색(findings_search)과 완전히 독립된 별도
  // fetch(findings_stats RPC, trends.js 와 동일 엔드포인트). 성공 시에만 노트를 채우고
  // 노출한다 — 실패(RPC 미존재 등)해도 이 노트만 hidden 유지, 검색 본기능엔 영향 없다.
  var coverageNoteEl = document.getElementById("fnd-coverage-note");
  var coverageTextEl = document.getElementById("fnd-coverage-text");

  // [FIND-1 M7] 대시보드 밴드 — 필터 컨트롤 위 콤팩트 조망(스탯/카테고리/월별/업체).
  // 셸(findings.html)은 빈 컨테이너+hidden 만 가진다 — 다섯 엘리먼트가 모두 있을 때만
  // 활성화하고(hasDash), 없으면 검색 자체는 기존과 동일하게 계속 동작한다(하위호환).
  var dashEl = document.getElementById("fnd-dash");
  var dashStatsEl = document.getElementById("fnd-dash-stats");
  var dashCatEl = document.getElementById("fnd-dash-cat");
  var dashMonthEl = document.getElementById("fnd-dash-month");
  var dashFirmEl = document.getElementById("fnd-dash-firm");
  var hasDash = !!(dashEl && dashStatsEl && dashCatEl && dashMonthEl && dashFirmEl);

  // [문서 단위 페이지네이션] 상단(#fnd-pager-top)·하단(#fnd-pager-bottom) 페이지네이션
  // 바 — 완전히 동일한 goToPage()/renderPager() 로직을 공유한다. 구버전 셸(엘리먼트
  // 없음)에서도 조용히 no-op(hasDash 관례와 동형 방어적 조회 — renderPager()/
  // setPagerLoading() 내부에서 개별 null 체크).
  var pagerTopEl = document.getElementById("fnd-pager-top");
  var pagerBottomEl = document.getElementById("fnd-pager-bottom");

  // [sticky 미니 내비] .fnd-tools(sticky) 안의 이전/다음 버튼 — 실사용자 신고("다음 누르면
  // 화면이 밀려나 매번 위로 되돌아가 다시 눌러야 함") 해법의 본체. sticky 영역에 있으므로
  // 스크롤 위치와 무관하게 항상 같은 화면 자리에 떠 있고, instant 스크롤(goToPage 내부)과
  // 결합하면 커서를 움직이지 않고 연타로 페이지를 넘길 수 있다. 리스너는 여기서 1회만
  // 바인딩(렌더마다 재바인딩 금지) — 클릭 시점의 currentPage 를 읽어 이동한다. 구버전
  // 셸(엘리먼트 없음)에서는 조용히 no-op(pager 관례와 동형).
  var pnavEl = document.getElementById("fnd-pnav");
  var pnavPrevBtn = document.getElementById("fnd-pnav-prev");
  var pnavNextBtn = document.getElementById("fnd-pnav-next");
  if (pnavPrevBtn) {
    pnavPrevBtn.addEventListener("click", function () { goToPageFromPager(currentPage - 1); });
  }
  if (pnavNextBtn) {
    pnavNextBtn.addEventListener("click", function () { goToPageFromPager(currentPage + 1); });
  }

  // renderPager() 가 호출하는 미니 내비 상태 갱신 — 페이지 1개뿐이면 통째로 숨기고,
  // 처음/끝에서 해당 방향 버튼을 disabled 처리한다(moreMayExist 면 다음은 항상 열어둠).
  function updatePnav(current, total, moreMayExist) {
    if (!pnavEl || !pnavPrevBtn || !pnavNextBtn) return;
    if (total <= 1 && !moreMayExist) {
      pnavEl.hidden = true;
      return;
    }
    pnavEl.hidden = false;
    pnavPrevBtn.disabled = current === 1;
    pnavNextBtn.disabled = current >= total && !moreMayExist;
    pnavPrevBtn.setAttribute("aria-label", "이전 페이지 (현재 " + current + " / " + total + ")");
    pnavNextBtn.setAttribute("aria-label", "다음 페이지 (현재 " + current + " / " + total + ")");
  }

  // LAST.facets 의 축 배열([{v,c}]) → {v: c} 평탄화. 축 자체가 없으면(구버전 RPC 등
  // 방어) 빈 객체 — 소비 측이 건수 0 으로 자연히 떨어진다.
  function facetCounts(key2) {
    var counts = {};
    if (!LAST || !LAST.facets) return counts;
    var axis = LAST.facets[FACET_AXIS[key2]];
    if (!Array.isArray(axis)) return counts;
    axis.forEach(function (e) {
      if (e && e.v) counts[e.v] = e.c || 0;
    });
    return counts;
  }

  // 옵션 값 목록 — 서버 파셋 축이 정본이다. 파셋은 **자기 축을 뺀** 나머지 필터 기준이라
  // (표준 파세팅) 자기 축 필터가 걸려 있어도 그 축의 선택지 전체가 그대로 보인다 = 다른
  // 값으로 갈아타는 길이 항상 열려 있다.
  // 정렬은 서버 순서(건수 desc 등)를 따르지 않고 값 자체로 재정렬한다 — 드롭다운 항목이
  // 건수 변동에 따라 자리를 옮기면 같은 항목을 두 번 고르기 어렵다(월만 최신 우선).
  function collectFacetValues(key2) {
    var counts = facetCounts(key2);
    var sorted = Object.keys(counts).sort();
    if (key2 === "month") sorted.reverse(); // 최신월 우선
    return sorted;
  }

  function selectOptionLabel(key2, v) {
    if (key2 === "category_code") {
      var cat = CATEGORY_LABELS[v];
      return cat ? cat.ko + " · " + cat.en : v;
    }
    if (key2 === "evidence_level") return EVIDENCE_LABEL[v] || v;
    if (key2 === "review_status") return STATUS_LABEL[v] || v;
    return v;
  }

  // [M14 §6] 카테고리 옵션 정렬 — collectFacetValues() 의 알파벳순(snake_case code 기준)은
  // 한국어 사용자에게 무작위 순서로 보인다. 대신 CATEGORY_LABELS 선언 순서(=grm_findings.
  // FINDING_TAXONOMY 계약 순서, 대략 "데이터 무결성→ 시설/설비→..." 식의 의미 있는 그룹핑)를
  // 따른다 — 실제 데이터에 존재하는(available) 코드만, 그 선언 순서대로 필터링한다.
  function categoryCodesInTaxonomyOrder(available) {
    var avail = {};
    available.forEach(function (v) { avail[v] = true; });
    return Object.keys(CATEGORY_LABELS).filter(function (code) { return avail[code]; });
  }

  // [M15] 소스/증거등급/검토상태/카테고리/발행월 <select> 5개의 DOM 골격(옵션)을 만든다.
  // change 배선은 wire()에서(전 필드 동일 경로 — SELECT_FACETS 단일).
  // ★멱등(재호출 가능)이어야 한다: 파셋 축은 자기 축을 제외한 나머지 필터 기준이라, 다른
  // 필터를 풀면 이전 응답에 없던 값이 새로 드러날 수 있다 — render() 마다 다시 불러 옵션을
  // 누적시킨다. 이미 존재하는 값은 건너뛰므로 중복 <option> 이 생기지 않고, 사라진 값의
  // 옵션은 지우지 않는다(refreshFacetUI 가 건수 0 + disabled 로 표시 — 선택지가 눈앞에서
  // 사라져 사용자가 방금 본 항목을 다시 찾지 못하는 것보다 낫다).
  function buildFacetSkeleton() {
    SELECT_FACETS.forEach(function (def) {
      var selId = def[0], key2 = def[1];
      var sel = document.getElementById(selId);
      if (!sel) return;
      var existing = {};
      Array.prototype.forEach.call(sel.options, function (opt) { existing[opt.value] = true; });
      var values = collectFacetValues(key2);
      if (key2 === "category_code") values = categoryCodesInTaxonomyOrder(values);
      values.forEach(function (v) {
        if (existing[v]) return; // 재호출 시 이미 만든 옵션은 다시 만들지 않는다
        var opt = document.createElement("option");
        opt.value = v;
        opt.dataset.label = selectOptionLabel(key2, v);
        opt.textContent = opt.dataset.label; // 초기 라벨(건수는 첫 render()가 바로 병기)
        sel.appendChild(opt);
      });
    });
  }

  // [M15] 셀렉트 옵션 라벨(건수 병기)을 매 render() 마다 갱신한다 — DOM 엘리먼트는
  // buildFacetSkeleton() 이 만든 것을 재사용(재생성 없음).
  // ★건수는 **항상** 표시한다. 종전엔 필터가 걸리면 건수를 숨겼는데, 그건 숫자가 로드분
  // 기준 부분집합이라 **틀렸기 때문**이지 숨기는 게 옳아서가 아니었다. 서버 파셋은 검색어
  // + 자기 축을 뺀 나머지 필터를 적용한 exact 값이라 숨길 이유가 사라졌다.
  function refreshFacetUI() {
    SELECT_FACETS.forEach(function (def) {
      var selId = def[0], key2 = def[1];
      var sel = document.getElementById(selId);
      if (!sel) return;
      var counts = facetCounts(key2);
      Array.prototype.forEach.call(sel.options, function (opt) {
        if (!opt.value) return; // "전체" 옵션은 건수 병기 대상 아님
        var count = counts[opt.value] || 0;
        opt.textContent = opt.dataset.label + " (" + count + ")";
        opt.disabled = count === 0 && sel.value !== opt.value;
      });
    });
  }

  // ── [FIND-1 M7] 대시보드 클릭 연동 — 기존 state/select 재사용, 별도 상태 저장소 없음.
  // 클릭 시 대응하는 select.value 도 동기화해 드롭다운·행 클릭 상태가 항상 일치하게 한다.
  function toggleCategoryFilter(code) {
    exitDeepLinkMode(); // [PR-0 딥링크] 필터 조작 → 딥링크 모드 종료(§4)
    exitSimilarMode(); // [FIND-1 S1] 필터 조작 → 유사검색 모드 종료(§6)
    var sel = document.getElementById("fnd-f-category");
    state.category_code = state.category_code === code ? "" : code;
    if (sel) sel.value = state.category_code;
    currentPage = 1; // [페이지네이션] 필터 변경 → 1페이지로 리셋
    goToPage(1);
  }

  function toggleMonthFilter(month) {
    exitDeepLinkMode(); // [PR-0 딥링크] 필터 조작 → 딥링크 모드 종료(§4)
    exitSimilarMode(); // [FIND-1 S1] 필터 조작 → 유사검색 모드 종료(§6)
    var sel = document.getElementById("fnd-f-month");
    state.month = state.month === month ? "" : month;
    if (sel) sel.value = state.month;
    currentPage = 1; // [페이지네이션] 필터 변경 → 1페이지로 리셋
    goToPage(1);
  }

  function toggleFirmFilter(name) {
    exitDeepLinkMode(); // [PR-0 딥링크] 검색 조작 → 딥링크 모드 종료(§4)
    exitSimilarMode(); // [FIND-1 S1] 검색 조작 → 유사검색 모드 종료(§6)
    state.q = state.q === name ? "" : name;
    if (qInput) qInput.value = state.q;
    currentPage = 1; // [페이지네이션] 검색어 변경 → 1페이지로 리셋
    goToPage(1);
  }

  function makeClickableRow(node, ariaLabel, onActivate) {
    // role=button+tabindex+Enter/Space — 클릭 가능한 div 행의 공용 접근성 배선(M7).
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.setAttribute("aria-label", ariaLabel);
    node.addEventListener("click", onActivate);
    node.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
        ev.preventDefault();
        onActivate();
      }
    });
  }

  // [M14 §4] 스탯 줄 → 스탯 블록(큰 숫자+라벨 가로 나열): [N 전체][N FDA][N MFDS]…[N 검토 필요].
  // 숫자는 textContent 로만 채운다(XSS 계약). 검토 필요 블록은 needsReview 0 이면 생략.
  function buildStatBlock(num, label, warn) {
    var block = el("div", "fnd-dash-stat");
    block.appendChild(el("span", "fnd-dash-stat-num" + (warn ? " warn" : ""), num));
    block.appendChild(el("span", "fnd-dash-stat-lbl", label));
    return block;
  }

  function renderDashStats(stats) {
    dashStatsEl.innerHTML = "";
    dashStatsEl.appendChild(buildStatBlock(String(stats.total), "전체", false));
    // 옵셔널 스탯 관례 유지 — 값이 없으면 블록 자체를 생략한다(레이아웃 깨짐 없음).
    if (stats.documents !== undefined && stats.documents !== null) {
      dashStatsEl.appendChild(buildStatBlock(String(stats.documents), "문서", false));
    }
    stats.agencies.forEach(function (a) {
      var block = buildStatBlock(String(a.count), a.agency, false);
      // stats.agenciesExact 가 아니면(RPC 미확보·필터 적용 중) 로드된 데이터 기준
      // 추정치라는 것을 툴팁으로 시각적으로 구분한다(EVIDENCE_TITLE/STATUS_TITLE 와
      // 동일한 title 속성 관례 — XSS 무관).
      if (!stats.agenciesExact) {
        block.title = "현재 로드된 데이터 기준(참고용)";
      }
      dashStatsEl.appendChild(block);
    });
    if (stats.needsReview > 0) {
      dashStatsEl.appendChild(buildStatBlock(String(stats.needsReview), "검토 필요", true));
    }
  }

  function renderDashCategories(stats) {
    dashCatEl.innerHTML = "";
    // [그리드 균형 M2a] 상위 8개만 개별 바로, 나머지는 "그 외 N건" 한 줄로 합산한다(기존
    // 6개 → 8개 — 겹침의 실제 원인은 항목 수가 아니라 라벨 CSS 였지만(buildCatRow 의
    // .fnd-dash-cat-label 참조), 그래도 목록이 과도하게 길어지지 않도록 8개로 상향).
    var top = stats.categories.slice(0, 8);
    var restCount = stats.categories.slice(8).reduce(function (s, c) { return s + c.count; }, 0);
    if (!top.length) {
      dashCatEl.appendChild(el("p", "fnd-dash-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCount = top.reduce(function (m, c) { return Math.max(m, c.count); }, 0) || 1;
    top.forEach(function (c) {
      dashCatEl.appendChild(buildCatRow(c.ko, c.count, maxCount, c.code));
    });
    if (restCount > 0) {
      dashCatEl.appendChild(buildCatRow("그 외", restCount, maxCount, null));
    }
  }

  // [M14 §4] 회색 트랙 제거 — 바 자체가 flex 셀(flex:1 1 auto)을 채우고, 비율은
  // transform:scaleX() 로 표현한다(레이아웃 폭은 항상 셀 전체 — width 축소가 아니다).
  function buildCatRow(label, count, maxCount, code) {
    var row = document.createElement("div");
    row.className = "fnd-dash-cat-row";
    if (code) {
      if (state.category_code === code) row.classList.add("on");
      makeClickableRow(
        row,
        label + " 카테고리로 필터: " + count + "건",
        function () { toggleCategoryFilter(code); }
      );
    }
    // [라벨·바 트랙 분리 M2a] 라벨은 CSS 로 110px+ellipsis 잘림 — title 로 전체 텍스트를
    // 계속 확인할 수 있게 한다(잘리지 않는 라벨도 무해하게 동일 텍스트를 반복할 뿐이다).
    var labelEl = el("span", "fnd-dash-cat-label", label);
    labelEl.title = label;
    row.appendChild(labelEl);
    var bar = el("div", "fnd-dash-cat-bar");
    var ratio = maxCount > 0 ? count / maxCount : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    row.appendChild(bar);
    row.appendChild(el("span", "fnd-dash-cat-count", String(count)));
    return row;
  }

  function renderDashMonths(stats) {
    dashMonthEl.innerHTML = "";
    var months = stats.months;
    if (!months.length) {
      dashMonthEl.appendChild(el("p", "fnd-dash-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCount = months.reduce(function (m, x) { return Math.max(m, x.count); }, 0) || 1;
    var years = {};
    months.forEach(function (x) { years[x.month.slice(0, 4)] = true; });
    var multiYear = Object.keys(years).length > 1;
    var wrap = el("div", "fnd-dash-month-bars");
    months.forEach(function (x) {
      var col = document.createElement("div");
      col.className = "fnd-dash-month-col";
      if (state.month === x.month) col.classList.add("on");
      var monthLabelText = x.month + " " + x.count + "건";
      makeClickableRow(col, monthLabelText, function () {
        toggleMonthFilter(x.month);
      });
      col.title = monthLabelText;
      var barwrap = el("div", "fnd-dash-month-barwrap");
      var bar = el("div", "fnd-dash-month-bar");
      bar.style.height = Math.max(6, Math.round((x.count / maxCount) * 100)) + "%";
      barwrap.appendChild(bar);
      col.appendChild(barwrap);
      var mm = x.month.slice(5, 7);
      var lblText = multiYear ? x.month.slice(2, 4) + "." + mm : mm;
      col.appendChild(el("span", "fnd-dash-month-lbl", lblText));
      wrap.appendChild(col);
    });
    dashMonthEl.appendChild(wrap);
  }

  function renderDashFirms(stats) {
    dashFirmEl.innerHTML = "";
    if (!stats.firms.length) {
      dashFirmEl.appendChild(el("p", "fnd-dash-empty", "표시할 데이터가 없습니다."));
      return;
    }
    stats.firms.forEach(function (f) {
      var row = document.createElement("div");
      row.className = "fnd-dash-firm-row";
      if (state.q === f.name) row.classList.add("on");
      // [firm_name 엔티티 디코드 M5] 클릭/필터는 raw f.name(DB 원본값) 그대로 써야 서버
      // 검색어(state.q → p_q) 매칭이 어긋나지 않는다 — 디코드는 표시(라벨·툴팁)에만.
      var firmDisplay = decodeFirmDisplay(f.name);
      makeClickableRow(row, firmDisplay + " 검색: " + f.count + "건", function () {
        toggleFirmFilter(f.name);
      });
      row.appendChild(el("span", "fnd-dash-firm-name", firmDisplay));
      row.appendChild(el("span", "fnd-dash-firm-count", String(f.count)));
      dashFirmEl.appendChild(row);
    });
  }

  // LAST.dash 의 축 배열([{v,c}]) → 서버 순서 그대로 반환(건수 desc, 값 asc — 027 계약).
  // 재정렬하지 않는다: 서버가 이미 결정론 순서로 보낸다.
  function dashAxis(name) {
    if (!LAST || !LAST.dash) return [];
    var axis = LAST.dash[name];
    return Array.isArray(axis) ? axis : [];
  }

  // 검토 필요 건수 — LAST.dash 에는 review_status 축이 없다(027). facets.by_review_status
  // 로 유도하되, 파셋은 **자기 축을 뺀** 모집단이라 검토상태 필터가 걸리면 현재 결과보다
  // 넓은 집합을 센다. 그 경우엔 필터 자체가 답을 확정한다:
  //   · review_status=needs_review 로 좁혔다 → 결과 전량이 needs_review = totals.findings
  //   · 다른 상태로 좁혔다                   → 결과에 needs_review 는 0건
  // 필터가 없을 때만 파셋 값을 쓴다(자기 축 제외가 no-op 이라 모집단이 결과와 정확히 일치).
  // 세 경로 모두 exact 다 — 추정치가 섞이지 않는다.
  function dashNeedsReview() {
    if (state.review_status) {
      return state.review_status === "needs_review" ? LAST.totals.findings : 0;
    }
    return facetCounts("review_status").needs_review || 0;
  }

  // 대시보드 = **현재 결과 집합의 분포**(필터 전량 적용, LAST.dash). 드롭다운 파셋
  // (LAST.facets, 자기 축 제외)과 모집단이 달라 서로 바꿔 쓸 수 없다 — 027 이 두 블록을
  // 따로 내려주는 이유다.
  function renderDash() {
    if (!hasDash) return;
    if (!LAST.totals.findings) {
      dashEl.hidden = true;
      return;
    }
    // [FIND-1 M9a] 미번역 건수는 집계하지 않는다 — 공개 게이트(006/010)가 국문 해석이 없는
    // 행을 RLS 단계에서 이미 차단하므로 클라이언트가 세면 항상 0에 수렴해 오해만 만든다.
    var stats = {
      total: LAST.totals.findings,
      documents: LAST.totals.documents,
      agencies: dashAxis("by_agency").map(function (e) {
        return { agency: e.v, count: e.c };
      }),
      agenciesExact: true, // 서버 집계라 항상 exact — "로드된 데이터 기준" 툴팁이 붙지 않는다
      needsReview: dashNeedsReview(),
      categories: dashAxis("by_category").map(function (e) {
        var cat = CATEGORY_LABELS[e.v];
        return { code: e.v, ko: cat ? cat.ko : e.v, count: e.c };
      }),
      months: [],
      firms: dashAxis("top_firms").slice(0, 5).map(function (f) {
        return { name: f.firm_name, count: f.c };
      }),
    };
    // 월 추이는 최근 12개월만 — 서버는 오름차순 전량을 주므로 뒤에서 자른다(027 계약).
    var months = dashAxis("by_month");
    if (months.length > 12) months = months.slice(months.length - 12);
    stats.months = months.map(function (e) { return { month: e.v, count: e.c }; });
    renderDashStats(stats);
    renderDashCategories(stats);
    renderDashMonths(stats);
    renderDashFirms(stats);
    dashEl.hidden = false;
  }

  function safeUrl(u) {
    var s = (u || "").trim().toLowerCase();
    return s.indexOf("http://") === 0 || s.indexOf("https://") === 0;
  }

  // [firm_name 엔티티 디코드 M5] DB firm_name 에 &amp;/&#039; 가 이미 이스케이프된 채로
  // 저장된 행이 있어("H &amp; P Industries") 표시 직전 이 2종 엔티티만 순수 문자열
  // replace 로 되돌린다 — textContent/setAttribute 대입 전용이라 innerHTML 이 아니므로
  // XSS 계약과 무관하다(트렌드/업체 프로파일/워치리스트 등 다른 정적 자산에도 동일
  // 헬퍼가 각각 복제돼 있다 — 별도 파일이라 import 불가, 계약만 복제하는 기존 관례와 동형).
  function decodeFirmDisplay(s) {
    return String(s || "").replace(/&amp;/g, "&").replace(/&#039;/g, "'");
  }

  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") e.textContent = text;
    return e;
  }

  // [M10b P1] 매칭어 하이라이트 — text node 분할 + createElement("mark") 조립만 사용
  // (innerHTML/정규식 치환 문자열 삽입 금지, 파일 상단 XSS 계약 참조). query 는 이미
  // trim+lowercase 된 상태로 전달받는다. indexOf 루프로 전 구간을 순회한다.
  function appendHighlighted(parent, text, query) {
    if (!query) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    var hay = text.toLowerCase();
    var i = 0;
    var idx = hay.indexOf(query, i);
    if (idx === -1) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    while (idx !== -1) {
      if (idx > i) parent.appendChild(document.createTextNode(text.slice(i, idx)));
      var mark = document.createElement("mark");
      mark.className = "fnd-hl";
      mark.textContent = text.slice(idx, idx + query.length);
      parent.appendChild(mark);
      i = idx + query.length;
      idx = hay.indexOf(query, i);
    }
    if (i < text.length) parent.appendChild(document.createTextNode(text.slice(i)));
  }

  // el() 의 하이라이트 버전 — query 가 없으면 el() 과 동일한 순수 textContent 경로.
  function elHL(tag, className, text, query) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") {
      if (query) appendHighlighted(e, text, query);
      else e.textContent = text;
    }
    return e;
  }

  var EVIDENCE_LABEL = { A: "Evidence A", B: "Evidence B", C: "Evidence C" };
  var STATUS_LABEL = { needs_review: "검토 필요", accepted: "검토 완료", rejected: "반려" };
  // [M13a] 배지 의미 툴팁 — 증거등급/검토상태가 "무엇을 뜻하는지" title 로 즉답한다
  // (setAttribute("title", ...) 뿐이라 XSS 무관). accepted 는 사람이 검토를 마쳤다는
  // 뜻이 아니라 결정론 규칙 기반 자동 승인이므로, 그렇게 오해될 문구는 쓰지 않는다.
  var EVIDENCE_TITLE = {
    A: "Evidence A — 1차 공식문서에서 직접 추출(신뢰도 높음)",
    B: "Evidence B — 공식 인덱스+보조 자료 기반(원문 대조 권장)",
    C: "Evidence C — 보조 출처 단독(참고용)",
  };
  var STATUS_TITLE = {
    needs_review: "AI 추출 후 사람 검수 전 — 원문 대조 필수",
    accepted: "결정론 추출 규칙 통과(자동 승인)",
  };

  // [M10b P1] 본문(국문 우선, 없으면 원문). 접힘 상태에서도 항상 보이므로 card 에 직접
  // 붙인다(부가 섹션과 분리) — 반환한 엘리먼트로 render() 가 오버플로 판정을 한다.
  function appendMainText(card, row, query) {
    var ko = (row.finding_text_ko || "").trim();
    var text = ko || row.finding_text || "";
    if (!text) return null;
    var p = elHL("p", "fnd-text", text, query);
    card.appendChild(p);
    return p;
  }

  // [원문·국문 병기 M6d, M10b P1, M14 P0] finding_text_ko 가 있을 때만 원문(영문) 접기가
  // 존재한다 — 접힘 상태에서 숨기는 부가 섹션(extra)에 들어간다. [M14] 번역고지("AI 번역 —
  // 원문 대조 권장")는 details 내부(summary 아래·원문 <p> 위)로 이동했다 — 원문을 펼쳐
  // 대조하는 맥락에서만 보이게 해, 기본(접힌) 화면의 AI 경고 문구 노출을 0회로 줄인다.
  function appendOrigAndNote(extra, row, query) {
    var ko = (row.finding_text_ko || "").trim();
    if (!ko || !row.finding_text) return;
    var details = document.createElement("details");
    details.className = "fnd-orig";
    var summary = document.createElement("summary");
    summary.textContent = "원문 보기 (영문)";
    details.appendChild(summary);
    if (row.translation_method === "llm_assisted") {
      details.appendChild(el("span", "fnd-tr-note", "AI 번역 — 원문 대조 권장"));
    }
    var p = elHL("p", null, row.finding_text, query);
    details.appendChild(p);
    extra.appendChild(details);
  }

  // [M10b P0] 조항(refs) 상태 명시 — 있으면 기존 칩, 없으면 "조항 미추출" 회색 칩
  // (한글이므로 .fnd-ref 를 재사용하지 않고 별도 클래스 .fnd-ref-missing, mono 미적용).
  function appendRefs(extra, row) {
    var refs = ([]).concat(row.cfr_refs || [], row.mfds_refs || []);
    var refsWrap = el("div", "fnd-refs");
    if (refs.length) {
      refs.forEach(function (r) {
        if (r) refsWrap.appendChild(el("span", "fnd-ref", r));
      });
    } else {
      refsWrap.appendChild(el("span", "fnd-ref-missing", "조항 미추출"));
    }
    extra.appendChild(refsWrap);
  }

  // [M10b P0] 메타 줄 — 문서번호(ASCII, mono 허용) · 신뢰도. 문서번호 표시 위치이므로
  // 매칭어 하이라이트 대상(P1)이기도 하다.
  function appendMetaLine(extra, row, query) {
    var docId = row.document_id || "";
    var meta = document.createElement("p");
    meta.className = "fnd-meta";
    meta.appendChild(document.createTextNode("문서번호 "));
    var docSpan = elHL("span", "fnd-meta-doc", docId, query);
    meta.appendChild(docSpan);
    if (row.confidence !== undefined && row.confidence !== null && row.confidence !== "") {
      var pct = Math.round(Number(row.confidence) * 100);
      if (!isNaN(pct)) meta.appendChild(document.createTextNode(" · 신뢰도 " + pct + "%"));
    }
    extra.appendChild(meta);
  }

  // [M10b P1] "자세히 보기"/"접기" 토글. 상태는 카드(article) 단위 로컬(전역 오염 없음) —
  // 클로저가 card/btn 을 캡처하고, 별도 state 저장소를 쓰지 않는다. 기본은 hidden(판정
  // 전) — render() 가 rAF 1회로 오버플로/부가섹션 존재 여부를 확인한 뒤 필요할 때만
  // 노출하고, 불필요하면 DOM 에서 제거한다("버튼을 만들지 않는다").
  function buildMoreToggle(card) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fnd-more-btn";
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = "자세히 보기";
    btn.hidden = true;
    btn.addEventListener("click", function () {
      var stillCollapsed = card.classList.toggle("fnd-collapsed");
      var expanded = !stillCollapsed;
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      btn.textContent = expanded ? "접기" : "자세히 보기";
    });
    return btn;
  }

  // ── ["이 지적과 유사한 사례"] findings_similar_to RPC(021_findings_similar_to.sql) 소비 ──
  // A/B 평가 실측(2026-07-15, 021 마이그레이션 주석 참조)으로 임베딩(S2)이 렉시컬(S1)을 못
  // 이겨 S2 웹 공개가 중단됐다 — 이 버튼은 021(finding_id 기준 렉시컬)을 소비하고,
  // 019(임베딩) 의 단건 유사도 RPC 는 호출하지 않는다(inert 유지 — 그 함수명은 이 파일에
  // 등장하지 않아야 한다, 평가 결과 반영 계약). 서버가 공개 게이트·
  // 같은 문서 제외·중복 붕괴·정렬을 전부 처리하므로 클라이언트는 재정렬·재필터를 하지
  // 않는다(§ S1 관례와 동일 — renderSimilarToState() 는 반환 순서 그대로 렌더). RPC 는
  // 라이브 DB 에 아직 미적용일 수 있어(컨트롤타워가 별도 적용) 실패는 조용히 0건과
  // 동일하게 처리한다 — 콘솔 에러도 화면 에러 상태도 없다. 카드 89개 전체에 자동 조회하지
  // 않도록 on-demand(클릭 전 fetch 없음) + 1회 fetch 후 캐시(재클릭은 토글만)로 구현한다.
  var SIMILAR_TO_LIMIT = 5;

  function fetchSimilarTo(findingId, limit) {
    return fetch(url.replace(/\/$/, "") + "/rest/v1/rpc/findings_similar_to", {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_finding_id: findingId, p_limit: limit }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_similar_to " + r.status);
      return r.json();
    });
  }

  // 항목 1개 — 업체명·발행일·본문(2줄 클램프)·중복 배지("동일 문구 N개 문서", N=
  // dup_documents, dup_findings>1 인 경우만)·해당 문서 보기(PR-0 딥링크, S1 이 이미 만든
  // similarItemDeepLinkUrl() 재사용 — 신규 URL 스킴 없음). 신뢰도 배지(Evidence 등급)는
  // 공간 제약상 표시하지 않는다(§2 명세). review_status==='needs_review' 는 .fnd-card--review
  // 관례(왼쪽 3px coral 보더)를 grm.css 를 건드리지 않고 인라인 스타일로 재현한다(§7 XSS
  // 계약 — textContent/createElement 만, HTML 문자열 데이터 주입 금지).
  function buildSimilarToItem(item) {
    var row = document.createElement("div");
    row.className = "fnd-simto-item";
    var needsReview = item.review_status === "needs_review";
    row.style.cssText =
      "margin-top:9px;padding:9px 0 0 " + (needsReview ? "9px" : "0") + ";" +
      "border-top:1px solid var(--line)" +
      (needsReview ? ";border-left:3px solid var(--coral)" : "");
    var head = document.createElement("div");
    head.style.cssText = "display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:4px";
    if (item.firm_name) {
      var firm = el("span", null, decodeFirmDisplay(item.firm_name));
      firm.style.cssText = "font-weight:600;font-size:12.5px;color:var(--ink)";
      head.appendChild(firm);
    }
    if (item.published_date) {
      var date = el("span", null, item.published_date);
      date.style.cssText = "font-family:var(--mono);font-size:11.5px;color:var(--muted)";
      head.appendChild(date);
    }
    if (Number(item.dup_findings) > 1) {
      var badge = el("span", null, "동일 문구 " + (item.dup_documents || 0) + "개 문서");
      badge.style.cssText =
        "display:inline-flex;align-items:center;height:20px;font-size:11px;font-weight:600;" +
        "line-height:1;border-radius:var(--rad-s);padding:0 7px;border:1px solid rgba(194,96,63,.22);" +
        "background:var(--coral-tint);color:var(--coral-2)";
      head.appendChild(badge);
    }
    row.appendChild(head);
    var text = el("p", null, item.text || "");
    text.style.cssText =
      "font-size:12.5px;line-height:1.5;color:var(--body);margin:0 0 6px;" +
      "display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden";
    row.appendChild(text);
    if (item.finding_id) {
      var link = document.createElement("a");
      link.href = similarItemDeepLinkUrl(item.finding_id);
      link.textContent = "해당 문서 보기";
      link.style.cssText = "font-size:12px;font-weight:600;color:var(--coral-2);text-decoration:none";
      row.appendChild(link);
    }
    return row;
  }

  // 상태 렌더 — items===null 이면 로딩 문구, 빈 배열이면 0건 문구(실패도 이 경로로
  // 수렴, §3). items 를 재정렬·재필터하지 않고 서버가 반환한 순서 그대로 forEach 렌더한다.
  function renderSimilarToState(block, items) {
    block.textContent = "";
    if (items === null) {
      var loading = el("p", null, "불러오는 중…");
      loading.style.cssText = "font-size:12.5px;color:var(--muted);margin:0";
      block.appendChild(loading);
      return;
    }
    if (!items.length) {
      var empty = el("p", null, "유사 사례를 찾지 못했습니다");
      empty.style.cssText = "font-size:12.5px;color:var(--muted);margin:0";
      block.appendChild(empty);
      return;
    }
    items.forEach(function (item) { block.appendChild(buildSimilarToItem(item)); });
  }

  // 카드 액션 행에 붙는 "유사 사례" 토글 — on-demand(클릭 전 fetch 없음), 1회 fetch 후
  // 캐시(재클릭은 접기/펼치기만, 재요청 금지). finding_id 가 없는 방어적 행(레거시 폴백
  // 등)은 버튼 자체를 만들지 않는다(evidence_url 조건부와 동형 관례 — 카드 깨짐 없음).
  function buildSimilarCasesControl(row) {
    var findingId = row.finding_id;
    if (!findingId) return null;
    var block = document.createElement("div");
    block.className = "fnd-simto";
    block.hidden = true;
    block.style.cssText = "margin-top:12px;padding-top:12px;border-top:1px solid var(--line)";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fnd-simto-btn";
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = "유사 사례";
    btn.style.cssText =
      "font:inherit;font-size:12.5px;font-weight:600;color:var(--coral-2);" +
      "background:transparent;border:0;padding:0;cursor:pointer";
    var fetched = false; // [on-demand+캐시] 1회 fetch 이후 재클릭은 토글만(재요청 금지)
    btn.addEventListener("click", function () {
      var opening = block.hidden;
      block.hidden = !opening;
      btn.setAttribute("aria-expanded", opening ? "true" : "false");
      if (!opening || fetched) return; // 접는 조작이거나 이미 fetch 완료 — 재요청 금지
      renderSimilarToState(block, null); // 로딩 표시
      // [F-08] fetched=true 는 성공(then)에서만 세운다 — 클릭 시점에 미리 세우면 일시 오류 후 재클릭이 새로고침 전까지 영구 봉쇄된다.
      fetchSimilarTo(findingId, SIMILAR_TO_LIMIT)
        .then(function (data) {
          fetched = true;
          var items = (data && Array.isArray(data.items)) ? data.items : [];
          renderSimilarToState(block, items);
        })
        .catch(function () {
          fetched = false; // [F-08] 재시도 허용(404 RPC 미존재도 멱등 GET성 POST라 무해) — 표시는 종전과 동일한 조용한 폴백
          renderSimilarToState(block, []); // §3 조용한 폴백 — RPC 미적용(404)/네트워크
          // 오류 전부 0건과 동일 문구로 수렴한다(재발생·콘솔 에러 로그 없음).
        });
    });
    return { btn: btn, block: block };
  }

  function buildCard(row, query) {
    var card = el("article", "fnd-card");
    // [PR-0 딥링크] 안정 DOM id — 일반 모드 포함 항상 부여한다(무해). 딥링크 자동 도달
    // (revealAndFocusTarget)이 document.getElementById("f-"+finding_id) 로 대상을 찾는다.
    if (row.finding_id) card.id = "f-" + row.finding_id;
    if (row.review_status === "needs_review") card.classList.add("fnd-card--review");
    card.classList.add("fnd-collapsed"); // 기본 접힘(카드 단위 로컬 상태, 재렌더시 초기화 허용)

    // [M14 §5] head 재구성 — 좌측 [소스][증거][검토 필요(해당 시)], 우측 date(margin-left:
    // auto, 배지 줄의 마지막 자식으로 붙여야 flex 행에서 실제로 우측 끝에 고정된다). 기관
    // (agency) 배지는 제거했다 — 소스(FDA 483/FDA Warning Letter/MFDS)가 기관을 포함하는
    // 상위 구분이라 "FDA"+"FDA 483" 같은 중복 표기를 없앤다.
    var head = el("div", "fnd-card-head");
    if (row.source) head.appendChild(el("span", "fnd-b", row.source));
    var evLabel = EVIDENCE_LABEL[row.evidence_level] || row.evidence_level || "";
    if (evLabel) {
      var evBadge = el("span", "fnd-b" + (row.evidence_level === "A" ? " ev-a" : ""), evLabel);
      var evTitle = EVIDENCE_TITLE[row.evidence_level];
      if (evTitle) evBadge.setAttribute("title", evTitle);
      head.appendChild(evBadge);
    }
    if (row.review_status === "needs_review") {
      var reviewBadge = el("span", "fnd-b needs-review", STATUS_LABEL.needs_review);
      reviewBadge.setAttribute("title", STATUS_TITLE.needs_review);
      head.appendChild(reviewBadge);
    } else if (row.review_status && STATUS_LABEL[row.review_status]) {
      var statusBadge = el("span", "fnd-b", STATUS_LABEL[row.review_status]);
      var statusTitle = STATUS_TITLE[row.review_status];
      if (statusTitle) statusBadge.setAttribute("title", statusTitle);
      head.appendChild(statusBadge);
    }
    head.appendChild(el("span", "fnd-b date", row.published_date || ""));
    card.appendChild(head);

    if (row.firm_name) card.appendChild(elHL("h3", "fnd-firm", decodeFirmDisplay(row.firm_name), query));
    var cat = CATEGORY_LABELS[row.category_code];
    var catText = cat ? cat.ko : row.category_label_ko;
    if (catText) card.appendChild(el("p", "fnd-cat", catText));

    var textEl = appendMainText(card, row, query);

    var extra = el("div", "fnd-extra"); // 접힘 상태에서 숨기는 부가 섹션(원문/refs/번역고지/메타)
    appendOrigAndNote(extra, row, query);
    appendRefs(extra, row);
    appendMetaLine(extra, row, query);
    card.appendChild(extra);

    var actions = el("div", "fnd-actions");
    if (safeUrl(row.evidence_url)) {
      var a = document.createElement("a");
      a.className = "fnd-link";
      a.href = row.evidence_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      var icon = document.createElement("i");
      icon.className = "ti ti-external-link";
      icon.setAttribute("aria-hidden", "true");
      a.appendChild(icon);
      a.appendChild(document.createTextNode("원문 보기"));
      actions.appendChild(a);
    }
    // ["이 지적과 유사한 사례"] 기존 "자세히 보기"(moreBtn) 관례와 나란히, 그 앞에 배치
    // (원문 보기 링크 뒤·자세히 보기 앞) — finding_id 없는 방어적 행은 null 이라 버튼이
    // 생기지 않는다(evidence_url 조건부와 동형, 카드 깨짐 없음).
    var simCases = buildSimilarCasesControl(row);
    if (simCases) actions.appendChild(simCases.btn);
    var moreBtn = buildMoreToggle(card);
    actions.appendChild(moreBtn);
    card.appendChild(actions);
    if (simCases) card.appendChild(simCases.block); // actions 바로 아래 인라인 펼침 블록

    return { card: card, textEl: textEl, extraEl: extra, moreBtn: moreBtn };
  }

  // 문서 카드 헤더 — 업체명이 주인공(기존 .fnd-firm 과 동일한 세리프 규칙, 문서 단위라
  // 조금 더 크게), 소스·발행일·지적 건수는 보조 메타. 문서 내 모든 행이 firm_name/source/
  // published_date 를 공유하므로 대표값(rows[0])만 쓴다.
  // [업체 프로파일 진입] head.firm_key(013_findings_firm_key.sql generated 컬럼)가 있으면
  // 업체명을 /findings/firm/?key= 링크로 만든다 — findings/index.html 과 findings/firm/
  // index.html 은 같은 findings/ 디렉터리의 부모·자식 경로라 rel_root 계산 없이
  // "firm/index.html" 상대경로 하나로 충분하다. 013 미적용 라이브에서는 row 에 firm_key
  // 가 아예 없으므로(방어) 링크 없이 기존 텍스트 그대로 렌더한다(하위호환).
  function buildDocHead(rows) {
    var head = rows[0];
    var docHead = el("div", "fnd-doc-head");
    if (head.firm_name) {
      var firmDisplay = decodeFirmDisplay(head.firm_name);
      if (head.firm_key) {
        var h2 = document.createElement("h2");
        h2.className = "fnd-doc-firm";
        var firmLink = document.createElement("a");
        firmLink.href = "firm/index.html?key=" + encodeURIComponent(head.firm_key);
        firmLink.textContent = firmDisplay;
        h2.appendChild(firmLink);
        docHead.appendChild(h2);
      } else {
        docHead.appendChild(el("h2", "fnd-doc-firm", firmDisplay));
      }
    }
    var meta = el("div", "fnd-doc-meta");
    if (head.source) meta.appendChild(el("span", "fnd-b", head.source));
    if (head.published_date) meta.appendChild(el("span", "fnd-doc-date", head.published_date));
    meta.appendChild(el("span", "fnd-doc-count", "지적 " + rows.length + "건"));
    docHead.appendChild(meta);
    return docHead;
  }

  // [긴 문서 접기] 한 문서에 지적사항이 DOC_OBS_VISIBLE_LIMIT(6)개 이상이면 처음 5개만
  // 펼치고 "지적 N건 모두 보기" 버튼으로 나머지를 토글한다(textContent/createElement만,
  // innerHTML 금지 — 기존 XSS 계약과 동일).
  var DOC_OBS_VISIBLE_LIMIT = 6;
  var DOC_OBS_INITIAL_SHOW = 5;

  function buildDocObsToggle(hiddenWrap, totalCount) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fnd-doc-toggle";
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = "지적 " + totalCount + "건 모두 보기";
    btn.addEventListener("click", function () {
      var expanded = hiddenWrap.hidden; // 현재 숨김이면 이번 클릭으로 펼쳐짐
      hiddenWrap.hidden = !expanded;
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      btn.textContent = expanded ? "접기" : "지적 " + totalCount + "건 모두 보기";
    });
    return btn;
  }

  // 문서 카드 1장 조립 — 헤더 + 소속 observation 카드들(기존 buildCard() 렌더를 그대로
  // 재사용: 카테고리 칩·국문 우선·원문 details 접기 전부 무변경).
  // 반환한 built 배열은 render() 의 rAF 오버플로 판정(자세히 보기 버튼 표시 여부)에 쓰인다.
  function buildDocCard(rows, query) {
    var doc = el("article", "fnd-doc");
    doc.appendChild(buildDocHead(rows));

    var obsWrap = el("div", "fnd-doc-obs");
    var built = [];
    var overflows = rows.length >= DOC_OBS_VISIBLE_LIMIT;
    var visibleCount = overflows ? DOC_OBS_INITIAL_SHOW : rows.length;
    rows.slice(0, visibleCount).forEach(function (row) {
      var b = buildCard(row, query);
      obsWrap.appendChild(b.card);
      built.push(b);
    });
    doc.appendChild(obsWrap);

    var rest = rows.slice(visibleCount);
    if (rest.length) {
      var hiddenWrap = el("div", "fnd-doc-obs fnd-doc-obs-more");
      hiddenWrap.hidden = true;
      rest.forEach(function (row) {
        var b = buildCard(row, query);
        hiddenWrap.appendChild(b.card);
        built.push(b);
      });
      doc.appendChild(hiddenWrap);
      var toggleWrap = el("div", "fnd-doc-more");
      toggleWrap.appendChild(buildDocObsToggle(hiddenWrap, rows.length));
      doc.appendChild(toggleWrap);
    }

    return { card: doc, built: built };
  }

  // [FIND-1 M10c] 활성 필터 개수(검색어·정렬 제외) — 모바일 "필터·정렬 (N)" 배지에 쓴다.
  function countActiveFilters() {
    var keys = ["agency", "category_code", "source", "evidence_level", "review_status", "month"];
    return keys.reduce(function (n, k) { return n + (state[k] ? 1 : 0); }, 0);
  }

  function updateFiltersToggleBadge() {
    if (!filtersBadgeEl) return;
    var n = countActiveFilters();
    filtersBadgeEl.textContent = n > 0 ? " (" + n + ")" : "";
  }

  // [FIND-1 M10c] state→URL(query string) — 기본값이 아닌 키만 반영, replaceState 로만
  // 갱신한다(pushState 금지 — 뒤로가기 히스토리 오염 방지). render() 마다 호출.
  var URL_KEYS = {
    q: "q", agency: "agency", category_code: "cat", source: "src",
    evidence_level: "ev", review_status: "status", month: "m", sort: "sort",
  };

  function syncStateToUrl() {
    if (typeof history === "undefined" || !history.replaceState || typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams();
    Object.keys(URL_KEYS).forEach(function (k) {
      var v = state[k];
      if (v && v !== DEFAULT_STATE[k]) params.set(URL_KEYS[k], v);
    });
    // [문서 단위 페이지네이션] 1페이지(기본값)는 URL 을 더럽히지 않는다 — 딥링크/뒤로가기는
    // 2페이지 이상일 때만 의미가 있다.
    if (currentPage > 1) params.set("page", String(currentPage));
    // [PR-0 딥링크] finding_id 는 URL_KEYS 필터 파라미터가 아니라 exitDeepLinkMode() 가
    // 지울 때까지 보존해야 하는 별도 계약이다 — 여기서 챙기지 않으면 이 함수가 새로
    // 만드는 URLSearchParams 가 기존 finding_id 를 조용히 지워버린다(§6).
    if (deepLinkParam) params.set(DEEP_LINK_PARAM, deepLinkParam);
    var qs = params.toString();
    var newUrl = location.pathname + (qs ? "?" + qs : "") + location.hash;
    history.replaceState(null, "", newUrl);
  }

  // URL→state(초기 로드 1회). ★첫 fetch **이전**에 호출한다 — 이 state 가 곧 첫 요청의
  // 파라미터이기 때문이다(URL 상태 없이 한 번 요청하고 다시 요청하면 왕복 2회 + 깜빡임).
  // 그래서 파셋 값 목록(collectFacetValues)에 의존할 수 없다: 그 목록은 서버 응답에만 있고,
  // 응답은 이 함수가 만든 state 로 요청해야 온다(순환).
  // 필터 값은 검증 없이 그대로 싣는다 — 서버가 모르는 값이면 결과가 0건이 되고, 적용 필터
  // 칩 행(#fnd-active)에 그 값이 그대로 드러나 한 번의 클릭으로 해제된다. 종전처럼 조용히
  // 무시하면 URL 이 말하는 필터와 화면이 어긋난다(URL 은 걸렸다는데 결과는 전량). 파셋
  // 목록으로 사후 검증하는 방법도 있으나 축은 자기 자신을 제외하므로 "다른 필터 때문에
  // 0건이라 축에서 빠진 정상 값"과 "존재하지 않는 값"을 구분하지 못한다 = 사용자가 건
  // 필터를 임의로 푸는 오검출이 생긴다.
  // sort 만 예외로 클라이언트에서 검증한다 — 서버도 클램프하지만 sortSel.value 대입이
  // 성립하려면 <option> 에 있는 값이어야 하고, 그 목록은 정적 셸에 있어 이미 안다.
  function readStateFromUrl() {
    if (typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    var qv = params.get(URL_KEYS.q);
    if (qv !== null) state.q = qv;
    ["agency", "category_code", "source", "evidence_level", "review_status", "month"].forEach(function (k) {
      var raw = params.get(URL_KEYS[k]);
      if (raw !== null) state[k] = raw;
    });
    var sortRaw = params.get(URL_KEYS.sort);
    if (sortRaw !== null && SORT_VALUES.indexOf(sortRaw) !== -1) state.sort = sortRaw;
  }

  // [문서 단위 페이지네이션] URL ?page= → 초기 페이지(양의 정수만, 그 외/누락은 1 —
  // syncStateToUrl() 과 짝을 이루는 딥링크·뒤로가기 지원). 최종 유효 범위(로드된 문서
  // 수 대비 클램프)는 render() 가 보정한다.
  function readPageFromUrl() {
    if (typeof URLSearchParams === "undefined") return 1;
    var raw = new URLSearchParams(location.search).get("page");
    if (raw === null) return 1;
    var n = parseInt(raw, 10);
    return !isNaN(n) && n >= 1 ? n : 1;
  }

  // URL 복원값을 검색창/셀렉트 UI 에도 동기화(칩 행은 renderActiveChips()가 매번 재계산).
  function syncControlsFromState() {
    if (qInput) qInput.value = state.q;
    SELECT_FACETS.forEach(function (def) {
      var sel = document.getElementById(def[0]);
      if (sel) sel.value = state[def[1]];
    });
    if (sortSel) sortSel.value = state.sort;
  }

  // [M15] 적용 필터 칩 행(#fnd-active) — 활성 필터(검색어 포함, 정렬 제외) 각각을 제거
  // 가능한 칩으로 보여주고, 끝에 "모두 지우기" 텍스트 버튼을 붙인다. 활성 필터가 0개면
  // 컨테이너 자체를 hidden 처리한다. 전부 textContent/createElement(XSS 계약).
  function clearActiveFilter(key) {
    exitDeepLinkMode(); // [PR-0 딥링크] 필터 해제 조작 → 딥링크 모드 종료(§4)
    exitSimilarMode(); // [FIND-1 S1] 필터 해제 조작 → 유사검색 모드 종료(§6)
    state[key] = "";
    syncControlsFromState();
    currentPage = 1; // [페이지네이션] 필터 해제 → 1페이지로 리셋
    goToPage(1);
  }

  function clearAllFilters() {
    exitDeepLinkMode(); // [PR-0 딥링크] 필터 전체 초기화 조작 → 딥링크 모드 종료(§4)
    exitSimilarMode(); // [FIND-1 S1] 필터 전체 초기화 조작 → 유사검색 모드 종료(§6)
    state = {
      q: "", agency: "", category_code: "", source: "", evidence_level: "",
      review_status: "", month: "", sort: "date_desc",
    };
    syncControlsFromState();
    currentPage = 1; // [페이지네이션] 전체 초기화 → 1페이지로 리셋
    goToPage(1);
  }

  function buildActiveChip(label, value, onClear) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fnd-active-chip";
    btn.setAttribute("aria-label", label + " 필터 해제");
    btn.appendChild(document.createTextNode(label + ": " + value + " "));
    var x = document.createElement("span");
    x.className = "fnd-active-x";
    x.setAttribute("aria-hidden", "true");
    x.textContent = "×";
    btn.appendChild(x);
    btn.addEventListener("click", onClear);
    return btn;
  }

  function renderActiveChips() {
    if (!activeEl) return;
    activeEl.innerHTML = "";
    var entries = ACTIVE_FILTER_DEFS.filter(function (def) { return !!state[def[0]]; });
    if (!entries.length) {
      activeEl.hidden = true;
      return;
    }
    entries.forEach(function (def) {
      var key = def[0], label = def[1], formatFn = def[2];
      var chip = buildActiveChip(label, formatFn(state[key]), function () { clearActiveFilter(key); });
      activeEl.appendChild(chip);
    });
    var clearAllBtn = document.createElement("button");
    clearAllBtn.type = "button";
    clearAllBtn.className = "fnd-active-clearall";
    clearAllBtn.textContent = "모두 지우기";
    clearAllBtn.addEventListener("click", clearAllFilters);
    activeEl.appendChild(clearAllBtn);
    activeEl.hidden = false;
  }

  // ── [PR-0 딥링크] /findings/?finding_id=finding-<24hex> 공유 링크 ──────────────────────
  // 우선순위: ①형식 불일치 → fetch 없이 즉시 notfound ②findings_document RPC 1회 →
  // null(RLS 비공개 포함) = notfound ③문서 전체를 buildDocCard() 로 카드 1장 렌더.
  // found/notfound 는 사용자가 필터·검색·정렬·페이지를 조작하는 순간 exitDeepLinkMode()
  // 로 종료되고 일반 모드로 전환된다(§4). 비공개·미존재·형식오류는 전부 동일한 notfound
  // 배너로 수렴한다(§7, 존재 여부 정보 누설 금지).
  function isValidFindingId(id) {
    return typeof id === "string" && FINDING_ID_RE.test(id);
  }

  function getDeepLinkParam() {
    if (typeof URLSearchParams === "undefined") return null;
    var v = new URLSearchParams(location.search).get(DEEP_LINK_PARAM);
    v = v ? v.trim() : "";
    return v || null;
  }

  function urlWithoutDeepLink() {
    if (typeof URLSearchParams === "undefined") return location.pathname;
    var params = new URLSearchParams(location.search);
    params.delete(DEEP_LINK_PARAM);
    var qs = params.toString();
    return location.pathname + (qs ? "?" + qs : "") + location.hash;
  }

  // 결과 영역(#fnd-loading 위) 상단에 삽입하는 안내 바 — grm.css 는 불가침이라 인라인
  // style.cssText 로만 꾸민다(§4 한글안전: letter-spacing/text-transform/mono 미사용,
  // 순수 배경·보더·패딩뿐). findings.html 템플릿은 건드리지 않는다(§8).
  function ensureDeepLinkBanner() {
    if (bannerEl) return bannerEl;
    bannerEl = document.createElement("div");
    bannerEl.id = "fnd-deeplink-banner";
    bannerEl.hidden = true;
    bannerEl.style.cssText =
      "display:flex;flex-wrap:wrap;align-items:center;gap:10px;" +
      "background:var(--strong);border:1px solid var(--line-2);border-radius:var(--rad-s);" +
      "padding:12px 16px;margin:0 0 16px;font-size:13px;color:var(--body)";
    var text = document.createElement("span");
    text.id = "fnd-deeplink-banner-text";
    bannerEl.appendChild(text);
    var link = document.createElement("a");
    link.id = "fnd-deeplink-banner-link";
    link.style.cssText = "font-weight:600;color:var(--coral-2)";
    link.textContent = "전체 목록 보기";
    link.hidden = true;
    bannerEl.appendChild(link);
    if (loadingEl && loadingEl.parentNode) loadingEl.parentNode.insertBefore(bannerEl, loadingEl);
    return bannerEl;
  }

  function hideDeepLinkBanner() {
    if (bannerEl) bannerEl.hidden = true;
  }

  function showDeepLinkFoundBanner() {
    var b = ensureDeepLinkBanner();
    document.getElementById("fnd-deeplink-banner-text").textContent = "공유된 지적사항의 문서를 표시 중입니다.";
    var link = document.getElementById("fnd-deeplink-banner-link");
    link.href = urlWithoutDeepLink(); // [4] finding_id 파라미터 제거한 URL
    link.hidden = false;
    b.hidden = false;
  }

  function showDeepLinkNotFoundBanner() {
    var b = ensureDeepLinkBanner();
    document.getElementById("fnd-deeplink-banner-text").textContent =
      "공유된 지적사항을 찾을 수 없습니다(비공개이거나 존재하지 않는 항목일 수 있습니다). 전체 목록을 표시합니다.";
    document.getElementById("fnd-deeplink-banner-link").hidden = true;
    b.hidden = false;
  }

  // 필터·검색·정렬·페이지 조작 진입점(wire()/clearAllFilters()/clearActiveFilter()/
  // toggleXFilter()/goToPageFromPager())이 공통으로 호출한다 — deepLinkParam 이 없으면
  // (일반 모드) 즉시 no-op 이라 기존 동작에 회귀가 없다(§7).
  function exitDeepLinkMode() {
    if (!deepLinkParam) return;
    deepLinkParam = null;
    deepLinkStatus = "";
    deepLinkDocRows = null;
    hideDeepLinkBanner();
  }

  // findings_document RPC(026) — finding_id 로 그 지적이 속한 문서 전체를 **1회 왕복**으로
  // 받는다. 비공개/부재는 구분 없이 null 이다(존재 여부 누설 금지 계약을 서버가 RLS 로
  // 강제한다 — 클라이언트가 두 경우를 구분할 수단 자체가 없다).
  function fetchDocument(findingId) {
    return fetch(url.replace(/\/$/, "") + "/rest/v1/rpc/findings_document", {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_finding_id: findingId }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_document " + r.status);
      return r.json();
    });
  }

  function resolveDeepLink(id) {
    if (!isValidFindingId(id)) {
      deepLinkStatus = "notfound";
      deepLinkPending = false;
      maybeFinishInit();
      return;
    }
    fetchDocument(id)
      .then(function (doc) {
        // null(비공개/부재) · 빈 findings · 네트워크 실패 — 전부 같은 notfound 로 수렴한다(§7).
        if (!doc || !Array.isArray(doc.findings) || !doc.findings.length) {
          deepLinkStatus = "notfound";
          deepLinkPending = false;
          maybeFinishInit();
          return;
        }
        deepLinkDocRows = doc.findings;
        deepLinkStatus = "found";
        deepLinkPending = false;
        maybeFinishInit();
      })
      .catch(function () {
        deepLinkStatus = "notfound";
        deepLinkPending = false;
        maybeFinishInit();
      });
  }

  // 대상 observation 요소(#f-<finding_id>)까지 자동 도달 — ①"N건 모두 보기" 뒤에 숨어
  // 있으면 펼침 ②카드 기본 접힘(3줄 요약)이면 펼침 ③goToPage() 의 기존 sticky 오프셋
  // 보정 스크롤 공식(§5)을 재사용 ④tabindex=-1 focus + 2초 일시 강조(인라인 style —
  // grm.css 불가침, 순수 outline 이라 §4 한글안전과 무관).
  function revealAndFocusTarget(built, targetId) {
    var item = null;
    for (var i = 0; i < built.length; i++) {
      if (built[i].card && built[i].card.id === "f-" + targetId) { item = built[i]; break; }
    }
    var targetEl = item ? item.card : document.getElementById("f-" + targetId);
    if (!targetEl) return;
    var moreWrap = targetEl.closest ? targetEl.closest(".fnd-doc-obs-more") : null;
    if (moreWrap && moreWrap.hidden) {
      moreWrap.hidden = false;
      var toggleBtn = moreWrap.parentNode ? moreWrap.parentNode.querySelector(".fnd-doc-toggle") : null;
      if (toggleBtn) {
        toggleBtn.setAttribute("aria-expanded", "true");
        toggleBtn.textContent = "접기";
      }
    }
    if (targetEl.classList.contains("fnd-collapsed")) {
      targetEl.classList.remove("fnd-collapsed");
      if (item && item.moreBtn) {
        item.moreBtn.hidden = false;
        item.moreBtn.setAttribute("aria-expanded", "true");
        item.moreBtn.textContent = "접기";
      }
    }
    var toolsBar = document.getElementById("fnd-tools");
    var stickyBottom = toolsBar ? toolsBar.getBoundingClientRect().bottom : 0;
    var scrollTarget = window.scrollY + targetEl.getBoundingClientRect().top - stickyBottom - 10;
    window.scrollTo({ top: Math.max(0, scrollTarget), behavior: "auto" });
    targetEl.setAttribute("tabindex", "-1");
    targetEl.focus({ preventScroll: true });
    targetEl.style.outline = "2px solid var(--coral)";
    targetEl.style.outlineOffset = "2px";
    setTimeout(function () {
      targetEl.style.outline = "";
      targetEl.style.outlineOffset = "";
    }, 2000);
  }

  // 문서 카드 1장 단독 렌더 — 페이지네이션·대시보드와 무관(§2: 딥링크 모드가 페이지네이션과
  // 무관하게 단독 렌더). findings_document 가 이미 문서 1건의 지적만 묶어 보내므로 클라이언트
  // 그룹핑 없이 buildDocCard() 를 그대로 재사용한다(§3 — 신규 렌더러를 만들지 않는다).
  function renderDeepLinkDoc() {
    showState("none");
    hidePager();
    if (hasDash) dashEl.hidden = true; // 문서 1건뿐이라 대시보드는 의미가 없다(파괴 아님 — 숨김만)
    resultsEl.textContent = "";
    countEl.textContent = "";
    var doc = buildDocCard(deepLinkDocRows, "");
    resultsEl.appendChild(doc.card);
    showDeepLinkFoundBanner();
    var targetId = deepLinkParam;
    // 마무리(펼침·측정·도달)는 rAF + setTimeout 이중 스케줄(finalized 가드로 1회만 실행)
    // — rAF 단독이면 백그라운드 탭(공유 링크를 새 탭으로 여는 흔한 경로)·헤드리스 환경에서
    // 유예/미발화되어 자동 도달이 죽는다. setTimeout 은 그 두 경우 모두에서 발화한다.
    // 실행 순서 계약: ①대상이 "N건 모두 보기" 래퍼 뒤면 래퍼부터 펼친다 — hidden 요소는
    // scrollHeight/clientHeight 가 0이라, 펼치기 전에 ②의 clamp 측정을 하면 래퍼 안 전
    // 카드의 "자세히 보기" 버튼이 오판(remove)된다 ②clamp 측정으로 moreBtn 표시/제거
    // ③대상 카드 펼침+스크롤+포커스+강조(revealAndFocusTarget — ①과 겹치는 래퍼 처리는
    // 멱등이라 무해).
    var finalized = false;
    function finalizeDeepLinkDoc() {
      if (finalized) return;
      finalized = true;
      var targetEl = document.getElementById("f-" + targetId);
      var moreWrap = targetEl && targetEl.closest ? targetEl.closest(".fnd-doc-obs-more") : null;
      if (moreWrap && moreWrap.hidden) {
        moreWrap.hidden = false;
        var toggleBtn = moreWrap.parentNode ? moreWrap.parentNode.querySelector(".fnd-doc-toggle") : null;
        if (toggleBtn) {
          toggleBtn.setAttribute("aria-expanded", "true");
          toggleBtn.textContent = "접기";
        }
      }
      doc.built.forEach(function (item) {
        var overflow = !!item.textEl && item.textEl.scrollHeight - item.textEl.clientHeight > 1;
        var hasExtra = !!item.extraEl && item.extraEl.childNodes.length > 0;
        if (overflow || hasExtra) { item.moreBtn.hidden = false; } else { item.moreBtn.remove(); }
      });
      revealAndFocusTarget(doc.built, targetId);
    }
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(finalizeDeepLinkDoc);
    setTimeout(finalizeDeepLinkDoc, 120);
  }

  // 첫 검색 응답(rowsReady)과 딥링크 해석(!deepLinkPending) 이 둘 다 끝나야 최종 렌더를
  // 확정한다 — 어느 쪽이 먼저 끝나든(네트워크 순서 무관) 깜빡임 없이 한 번만 렌더한다.
  // finding_id 파라미터가 애초에 없었으면 deepLinkPending 은 시작부터 false 라 이 함수는
  // rowsReady 만 기다리는 기존 흐름과 완전히 동일하게 동작한다(§7 회귀 0).
  // ★render() 를 직접 부른다(goToPage 가 아니다) — 첫 응답이 이미 LAST 에 있으므로
  // goToPage 를 부르면 같은 페이지를 한 번 더 요청하게 된다(왕복 2회).
  function maybeFinishInit() {
    if (!rowsReady || deepLinkPending) return;
    if (deepLinkStatus === "found") {
      renderDeepLinkDoc();
      return;
    }
    if (deepLinkStatus === "notfound") showDeepLinkNotFoundBanner();
    // URL ?page= 가 범위를 넘었으면(옛 북마크 등 — 첫 요청은 pages 를 모른 채 나간다)
    // 마지막 페이지를 다시 요청한다. 그대로 그리면 빈 목록 + 페이저 숨김이라 되돌아갈
    // 길이 없다(goToPage 의 동일 보정과 같은 이유). 아직 "불러오는 중" 상태이므로
    // 중간 깜빡임도 없다.
    if (!LAST.documents.length && LAST.totals.documents > 0 && currentPage > LAST.pages) {
      goToPage(LAST.pages);
      return;
    }
    render();
  }

  // ── [FIND-1 S1] 유사 문구 검색(렉시컬) — findings_similar RPC 소비 ──────────────────────
  // 서버(018)가 공개 게이트·중복 붕괴·정렬을 전부 처리한다 — 클라이언트는 재정렬·재필터를
  // 하지 않는다(계약). 결과는 문서 그룹핑 없이 finding 단위 카드 목록으로, 기존 buildCard()
  // 를 그대로 재사용해 렌더한다(신규 렌더러 금지 — §2).

  // RPC item(finding_id/raw_signal_id/source/agency/published_date/firm_name/category_code/
  // text/score/dup_documents/dup_findings) → buildCard() 가 기대하는 row 모양 최소 어댑터.
  // RPC 의 text 는 원문/국문 구분이 없는 단일 텍스트라 finding_text_ko 에만 채우고
  // finding_text 는 비운다 — appendOrigAndNote() 는 row.finding_text 가 falsy 면 즉시
  // return 이라(파일 상단 §XSS 계약 인접 로직 재확인) "원문 보기" 접기가 조용히 나타나지
  // 않는다(깨진 접기 없음). evidence_level/review_status/cfr_refs/mfds_refs/document_id/
  // confidence/evidence_url 은 RPC 가 반환하지 않으므로 undefined 로 남고, buildCard() 의
  // 기존 방어적 조건부(if (row.xxx))가 각각 조용히 생략한다(카드 깨짐 없음).
  function mapSimilarItemToRow(item) {
    return {
      finding_id: item.finding_id,
      raw_signal_id: item.raw_signal_id,
      source: item.source,
      agency: item.agency,
      published_date: item.published_date,
      firm_name: item.firm_name,
      category_code: item.category_code,
      // 신뢰도 배지 2종(M13 — Evidence 등급/검토 필요 경계)은 목록 모드와 동일하게
      // 유지해야 한다. RPC 가 이 두 서지 필드를 반환하지 않으면 유사검색 결과에서만
      // "검토 필요" 경고가 조용히 사라진다(018 주석의 반환 계약 참조).
      evidence_level: item.evidence_level || "",
      review_status: item.review_status || "",
      finding_text_ko: item.text || "",
      finding_text: "",
    };
  }

  // [S1 중복 배지] dup_findings>1 인 경우에만 "동일 문구 N개 문서"(N=dup_documents) 배지를
  // 카드 헤드에 추가한다 — grm.css 불가침이라 인라인 style 뿐(§4 한글안전: letter-spacing/
  // text-transform/mono 미사용). date 배지(margin-left:auto, 항상 head 끝)보다 앞에 꽂아
  // 우측 끝 고정을 깨지 않는다.
  function appendSimilarDupBadge(card, item) {
    if (!item || !(Number(item.dup_findings) > 1)) return;
    var head = card.querySelector(".fnd-card-head");
    if (!head) return;
    var badge = document.createElement("span");
    badge.style.cssText =
      "display:inline-flex;align-items:center;height:22px;font-size:11.5px;font-weight:600;" +
      "line-height:1;border-radius:var(--rad-s);padding:0 8px;border:1px solid rgba(194,96,63,.22);" +
      "background:var(--coral-tint);color:var(--coral-2)";
    badge.textContent = "동일 문구 " + (item.dup_documents || 0) + "개 문서";
    var dateBadge = head.querySelector(".fnd-b.date");
    if (dateBadge) head.insertBefore(badge, dateBadge);
    else head.appendChild(badge);
  }

  // [딥링크 연계 §4] PR-0 이 만든 /findings/?finding_id=<id> 공유 링크를 그대로 재사용 —
  // 각 결과 카드에서 해당 finding 이 속한 문서로 이동할 수 있는 착지점. buildCard() 자체는
  // 건드리지 않고(§2 재사용 원칙), actions 푸터에 링크 하나만 추가로 꽂는다.
  function similarItemDeepLinkUrl(id) {
    return location.pathname + "?" + DEEP_LINK_PARAM + "=" + encodeURIComponent(id);
  }

  function appendSimilarDeepLink(card, findingId) {
    if (!findingId) return;
    var actions = card.querySelector(".fnd-actions");
    if (!actions) return;
    var a = document.createElement("a");
    a.className = "fnd-link";
    a.href = similarItemDeepLinkUrl(findingId);
    var icon = document.createElement("i");
    icon.className = "ti ti-file-text";
    icon.setAttribute("aria-hidden", "true");
    a.appendChild(icon);
    a.appendChild(document.createTextNode("해당 문서 보기"));
    actions.insertBefore(a, actions.firstChild);
  }

  function fetchSimilarItems(q, limit) {
    return fetch(url.replace(/\/$/, "") + "/rest/v1/rpc/findings_similar", {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_query: q, p_limit: limit }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_similar " + r.status);
      return r.json();
    });
  }

  // 결과 렌더 — 문서 그룹핑 없이 finding 단위 카드 목록(서버 정렬 순서 그대로, 재정렬 없음).
  // 대시보드·페이저는 이 모드와 무관하므로 숨긴다(딥링크 단독 렌더(renderDeepLinkDoc)와
  // 동일한 관례).
  function renderSimilarResults(items) {
    showState("none");
    hidePager();
    if (hasDash) dashEl.hidden = true;
    resultsEl.textContent = "";
    countEl.textContent = "";
    countEl.appendChild(document.createTextNode("유사 문구 " + items.length.toLocaleString("ko-KR") + "건"));
    var frag = document.createDocumentFragment();
    items.forEach(function (item) {
      var row = mapSimilarItemToRow(item);
      var built = buildCard(row, "");
      appendSimilarDupBadge(built.card, item);
      appendSimilarDeepLink(built.card, item.finding_id);
      frag.appendChild(built.card);
    });
    resultsEl.appendChild(frag);
  }

  // 토글 아이콘/on-상태만 갱신(DOM 재생성 없음) — buildFacetSkeleton() 옵션 갱신 관례와 동형.
  function updateSimilarToggleUI() {
    if (!similarToggleBtn) return;
    similarToggleBtn.setAttribute("aria-pressed", similarMode ? "true" : "false");
    if (similarMode) {
      similarToggleBtn.style.background = "var(--coral-tint)";
      similarToggleBtn.style.borderColor = "rgba(194,96,63,.35)";
      similarToggleBtn.style.color = "var(--coral-2)";
    } else {
      similarToggleBtn.style.background = "var(--canvas)";
      similarToggleBtn.style.borderColor = "var(--line-2)";
      similarToggleBtn.style.color = "var(--body)";
    }
  }

  // 질의 2자 미만이면 목록 모드로(§ 모드 이탈 "질의 삭제"). 그 외엔 RPC 를 호출하고,
  // 실패든 빈 결과든 전부 조용히 goToPage(1)(기존 키워드 검색, 토글 OFF 상태와 동일 동작)
  // 로 폴백한다 — 콘솔 에러 노출도, 사용자에게 보이는 에러 상태도 없다(§5 폴백 계약).
  function runSimilarSearch() {
    var q = state.q.trim();
    currentPage = 1;
    if (q.length < SIMILAR_MIN_QUERY_LEN) {
      goToPage(1);
      return;
    }
    similarFetchToken += 1;
    var myToken = similarFetchToken;
    fetchSimilarItems(q, SIMILAR_LIMIT)
      .then(function (data) {
        if (myToken !== similarFetchToken) return; // 더 최근 토글/입력으로 취소됨
        var items = (data && Array.isArray(data.items)) ? data.items : [];
        if (!items.length) {
          goToPage(1); // 조용한 폴백(§5) — 빈 결과
          return;
        }
        renderSimilarResults(items);
      })
      .catch(function () {
        if (myToken !== similarFetchToken) return;
        goToPage(1); // 조용한 폴백(§5) — RPC 미적용(404)/네트워크 오류 전부 여기로 수렴
      });
  }

  function setSimilarMode(on) {
    if (similarMode === on) return;
    similarMode = on;
    similarFetchToken += 1; // 진행 중이던 이전 모드의 fetch 응답을 무시
    updateSimilarToggleUI();
    if (similarMode) runSimilarSearch();
    else { currentPage = 1; goToPage(1); } // §6 모드 이탈 — 기존 목록 모드로 복귀
  }

  // §6 모드 이탈 — 필터/정렬/페이지 조작 시 유사검색 모드를 끄고 목록 모드로 복귀한다.
  // exitDeepLinkMode() 와 동형 관례(비활성 상태면 즉시 no-op, 회귀 0).
  function exitSimilarMode() {
    if (!similarMode) return;
    similarMode = false;
    similarFetchToken += 1;
    updateSimilarToggleUI();
  }

  // 검색창 옆(sticky 툴바 .fnd-tools 내부) 토글 버튼 — findings.html 템플릿엔 자리가 없어
  // (§ 템플릿 최소 변경 원칙, PR-0 딥링크 배너와 동일 관례) findings.js 가 런타임에 DOM
  // 삽입한다. 명칭은 반드시 "유사 문구 검색"으로 고정한다 — trigram+FTS 렉시컬 매칭일 뿐
  // 뜻을 이해해 찾아주는 방식이 아니므로, 그렇게 오인시키는 다른 표현은 쓰지 않는다.
  function buildSimilarToggle() {
    if (!qInput || !qInput.parentNode || document.getElementById("fnd-similar-toggle")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "fnd-similar-toggle";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "유사 문구 검색 켜기/끄기");
    btn.textContent = "유사 문구 검색";
    btn.style.cssText =
      "flex:none;height:34px;padding:0 14px;font:inherit;font-size:12.5px;font-weight:600;" +
      "color:var(--body);background:var(--canvas);border:1.5px solid var(--line-2);" +
      "border-radius:999px;cursor:pointer;white-space:nowrap";
    btn.addEventListener("click", function () {
      exitDeepLinkMode(); // [S1] 유사검색 진입 → 딥링크 모드 종료(자연스러운 모드 전환)
      setSimilarMode(!similarMode);
    });
    qInput.parentNode.insertBefore(btn, countEl || null);
    similarToggleBtn = btn;
  }

  // LAST(가장 최근 서버 응답)만으로 화면 전체를 그린다 — 재필터·재정렬·재집계·슬라이스가
  // 전부 없다(서버가 이미 이 페이지의 문서만 잘라 보냈다).
  function render() {
    buildFacetSkeleton(); // 이번 응답에서 새로 드러난 파셋 값 반영(멱등)
    renderDash(); // [FIND-1 M7] 현재 결과 집합 기준 대시보드(LAST.dash, 결과 0건이면 hidden)
    refreshFacetUI(); // [M15] 셀렉트 건수 갱신(표준 파세팅)
    renderActiveChips(); // [M15] 적용 필터 칩 행 재계산
    updateFiltersToggleBadge();

    // totals 는 검색·필터 적용 후 exact 다(추정치가 아니다) — "이상" 접미사 같은 불확실성
    // 표기가 존재할 이유가 없다. pages 도 서버가 같은 모집단에서 계산해 보낸다.
    var totalDocs = LAST.totals.documents;
    var totalFindings = LAST.totals.findings;
    var totalPages = Math.max(1, LAST.pages || 1);
    if (currentPage > totalPages) currentPage = totalPages; // 방어적 클램프(표시용 — goToPage 가 범위 밖 요청을 이미 되돌린다)
    if (currentPage < 1) currentPage = 1;

    // [문서 중심 열람] "전체 N문서 · M지적 · 페이지 X / Y"
    countEl.textContent = "";
    countEl.appendChild(document.createTextNode("전체 "));
    var bDocs = document.createElement("b");
    bDocs.textContent = totalDocs.toLocaleString("ko-KR");
    countEl.appendChild(bDocs);
    countEl.appendChild(document.createTextNode("문서 · "));
    var bObs = document.createElement("b");
    bObs.textContent = totalFindings.toLocaleString("ko-KR");
    countEl.appendChild(bObs);
    countEl.appendChild(document.createTextNode("지적 · 페이지 "));
    var bCur = document.createElement("b");
    bCur.textContent = String(currentPage);
    countEl.appendChild(bCur);
    countEl.appendChild(document.createTextNode(" / "));
    var bTotal = document.createElement("b");
    bTotal.textContent = String(totalPages);
    countEl.appendChild(bTotal);

    syncStateToUrl(); // [페이지네이션] ?page= 도 여기서 함께 반영(currentPage 확정 이후)

    resultsEl.textContent = "";
    if (!LAST.documents.length) {
      showState("empty");
      hidePager();
      return;
    }
    showState("none");
    var query = state.q.trim().toLowerCase(); // [M10b P1] 하이라이트 검색어(trim+대소문자무시)
    var frag = document.createDocumentFragment();
    var built = [];
    // doc.findings = 이 문서에서 **매치된 지적만**(문서 전체가 아니다). 페이지 경계에서
    // 문서가 쪼개지지 않는 것은 서버가 문서 단위로 페이지를 나눈 결과다.
    LAST.documents.forEach(function (doc) {
      var d = buildDocCard(doc.findings, query);
      frag.appendChild(d.card);
      built = built.concat(d.built);
    });
    resultsEl.appendChild(frag);
    renderPager(currentPage, totalPages, false); // moreMayExist=false — 총 페이지가 exact 라 "+" 표기가 성립하지 않는다
    // [M10b P1] "자세히 보기" 버튼 표시 여부 — DOM 삽입 후(레이아웃 확정) 1회 rAF 로 판정.
    // 본문이 3줄을 넘겨 잘렸거나(scrollHeight>clientHeight) 부가 섹션이 있으면 노출,
    // 둘 다 아니면 DOM 에서 제거한다(버튼 없음).
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(function () {
        built.forEach(function (item) {
          var overflow = !!item.textEl && item.textEl.scrollHeight - item.textEl.clientHeight > 1;
          var hasExtra = !!item.extraEl && item.extraEl.childNodes.length > 0;
          if (overflow || hasExtra) {
            item.moreBtn.hidden = false;
          } else {
            item.moreBtn.remove();
          }
        });
      });
    }
  }

  function wire() {
    buildSimilarToggle(); // [FIND-1 S1] 검색창 옆 "유사 문구 검색" 토글(런타임 DOM 삽입)
    // [M15] 6개 셀렉트(소스·증거등급·검토상태·카테고리·발행월·정렬 중 정렬 제외 5개)가
    // 전부 SELECT_FACETS 단일 경로로 change 배선된다(칩 그룹 배선 없음).
    SELECT_FACETS.forEach(function (def) {
      var sel = document.getElementById(def[0]);
      if (!sel) return;
      sel.addEventListener("change", function () {
        exitDeepLinkMode(); // [PR-0 딥링크] 필터 조작 → 딥링크 모드 종료(§4)
        exitSimilarMode(); // [FIND-1 S1] 필터 조작 → 유사검색 모드 종료(§6)
        state[def[1]] = sel.value;
        currentPage = 1; // [페이지네이션] 필터 변경 → 1페이지로 리셋
        goToPage(1);
      });
    });
    if (sortSel) {
      sortSel.addEventListener("change", function () {
        exitDeepLinkMode(); // [PR-0 딥링크] 정렬 조작 → 딥링크 모드 종료(§4)
        exitSimilarMode(); // [FIND-1 S1] 정렬 조작 → 유사검색 모드 종료(§6)
        state.sort = sortSel.value;
        currentPage = 1; // [페이지네이션] 정렬 변경 → 1페이지로 리셋
        goToPage(1);
      });
    }
    if (qInput) {
      qInput.addEventListener("input", function () {
        exitDeepLinkMode(); // [PR-0 딥링크] 검색 조작 → 딥링크 모드 종료(§4, 디바운스 전 즉시)
        state.q = qInput.value;
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function () {
          // [FIND-1 S1] 유사검색 모드면 RPC 로, 아니면 기존 키워드 검색 목록 모드로.
          if (similarMode) { runSimilarSearch(); return; }
          currentPage = 1; // [페이지네이션] 검색어 변경 → 1페이지로 리셋
          goToPage(1);
          // 250ms — 매 입력이 서버 왕복이라 종전 150ms 보다 길게 잡는다(로컬 배열 필터링
          // 이던 시절의 값이라 그대로 두면 타이핑 중 요청이 과하게 나간다).
        }, 250);
      });
    }
    // [M15] 전체 초기화는 #fnd-reset 버튼이 아니라 적용 필터 칩 행의 "모두 지우기"
    // (renderActiveChips() 가 매 render() 마다 동적으로 만든다) — clearAllFilters() 참조.
    // [FIND-1 M10c] 모바일(≤700px) 필터·정렬 접기 — JS 는 클래스/aria 토글만, 표시/숨김은
    // CSS 미디어쿼리(.fnd-filters.open)가 담당한다.
    if (filtersToggleBtn && filtersEl) {
      filtersToggleBtn.addEventListener("click", function () {
        var open = filtersEl.classList.toggle("open");
        filtersToggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    // [FIND-1 M10c] 대시보드 세부지표(3블록 그리드) 접기 — 기본값은 matchMedia 1회 판정
    // (데스크톱 펼침·≤700px 접힘), 이후는 버튼 클릭으로만 토글.
    if (dashToggleBtn && dashGridEl) {
      var collapsedByDefault = typeof matchMedia === "function" && matchMedia("(max-width:700px)").matches;
      if (collapsedByDefault) {
        dashGridEl.classList.add("fnd-dash-grid--collapsed");
        dashToggleBtn.setAttribute("aria-expanded", "false");
      }
      dashToggleBtn.addEventListener("click", function () {
        var collapsed = dashGridEl.classList.toggle("fnd-dash-grid--collapsed");
        dashToggleBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      });
    }
  }

  // findings_search RPC(026/027) — 검색·필터·정렬·페이지네이션·파셋·대시보드의 단일 정본.
  // 인자는 전부 기본값이 있어 생략 가능하지만 명시적으로 전부 싣는다(서버가 클램프·정규화
  // 하므로 클라이언트 검증은 불필요 — 신뢰 경계는 서버다).
  // 응답을 LAST 에 대입하지 않고 그대로 반환한다 — 대입 시점은 호출부가 navToken 세대
  // 검사를 통과시킨 뒤여야 한다(늦게 도착한 옛 응답이 최신 결과를 덮어쓰는 것 방지).
  function fetchSearch(page) {
    return fetch(url.replace(/\/$/, "") + "/rest/v1/rpc/findings_search", {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({
        p_q: state.q.trim(),
        p_source: state.source,
        p_category: state.category_code,
        p_month: state.month,
        p_evidence: state.evidence_level,
        p_review_status: state.review_status,
        p_agency: state.agency,
        p_sort: state.sort,
        p_page: page,
        p_docs_per_page: DOCS_PER_PAGE,
      }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("findings_search " + r.status);
        return r.json();
      })
      .then(function (data) {
        // 봉투 계약 검증 — 형태가 어긋나면 부분 렌더로 화면을 망가뜨리지 말고 실패로 다룬다.
        if (!data || !Array.isArray(data.documents) || !data.totals) {
          throw new Error("findings_search shape");
        }
        return data;
      });
  }

  // [페이지 이동] navToken 세대 카운터로 연타(빠른 재클릭)를 방어한다 — 오래된 요청의
  // 응답은 LAST/currentPage/render() 를 건드리지 않고 조용히 버려지므로, 항상 "가장
  // 최근 조작"만 화면에 반영된다.
  // ★진행 중에 이전 결과를 지우지 않는다 — setPagerLoading(true) 로 진행만 알리고, 교체는
  // 응답이 도착한 뒤 render() 가 한 번에 한다(입력할 때마다 목록이 사라졌다 나타나는 깜빡임 방지).
  function goToPage(n) {
    var target = Math.max(1, Math.floor(n) || 1);
    navToken += 1;
    var myToken = navToken;
    var doScroll = pendingScrollAfterNav;
    pendingScrollAfterNav = false;
    setPagerLoading(true);
    fetchSearch(target)
      .then(function (data) {
        if (myToken !== navToken) return; // 더 최근 조작에 의해 취소됨
        // 범위 밖 페이지(옛 북마크·손으로 고친 ?page= 등): 서버는 빈 documents 를 준다.
        // 그대로 그리면 "결과 없음"에 페이저까지 숨겨져 되돌아갈 길이 사라진다 — 결과가
        // 존재하는데 페이지만 넘친 경우에 한해 마지막 페이지로 1회 되돌린다(재귀 아님:
        // 목표가 pages 라 두 번째 응답은 범위 안이다).
        if (!data.documents.length && data.totals.documents > 0 && target > data.pages) {
          goToPage(data.pages);
          return;
        }
        LAST = data;
        currentPage = target;
        render(); // render() 가 pager 를 통째로 재생성하므로 로딩 상태는 자연히 정리된다.
        // [로딩 UX b′] fetch 완료 후 목표 페이지 렌더가 끝나면 결과 목록 상단으로 스크롤한다
        // — 페이지네이션 바 클릭(goToPageFromPager())에서만(doScroll), 필터/검색/정렬 변경발
        // goToPage(1) 리셋은 스크롤하지 않는다. ★실사용자 신고 반영 2건: ①.fnd-tools 가
        // sticky(top:66px)라 단순 scrollIntoView(start)는 결과 상단이 sticky 툴바 밑에
        // 가려지고 상단 페이저는 화면 위로 밀려나 "매번 위로 되돌아가 다음을 눌러야" 했다
        // → 결과 상단을 sticky 툴바 바닥 바로 아래에 정렬(오프셋 보정). ②smooth 애니메이션
        // 은 연타 시 버튼 위치가 흘러다녀 instant(auto)로 교체 — sticky 미니 내비(#fnd-pnav)
        // 와 결합하면 커서를 움직이지 않고 같은 자리에서 연속으로 페이지를 넘길 수 있다.
        if (doScroll && resultsEl) {
          var toolsBar = document.getElementById("fnd-tools");
          var stickyBottom = toolsBar ? toolsBar.getBoundingClientRect().bottom : 0;
          var scrollTarget = window.scrollY + resultsEl.getBoundingClientRect().top - stickyBottom - 10;
          window.scrollTo({ top: Math.max(0, scrollTarget), behavior: "auto" });
        }
      })
      .catch(function () {
        if (myToken !== navToken) return;
        // 이전 결과가 있으면 그대로 두고 조용히 실패한다 — 일시적 네트워크 오류로 사용자가
        // 보던 목록을 통째로 날리지 않는다. render() 재호출은 로딩 표시(setPagerLoading)를
        // 되돌리기 위한 것이다. 그릴 것이 아예 없을 때만 오류 상태로 간다.
        if (!LAST) { showState("error"); return; }
        render();
      });
  }

  // [로딩 UX b] 페이지네이션 바(처음/이전/번호/다음/끝) 전용 진입점 — goToPage() 를 그대로
  // 위임하되, 완료 후 스크롤 플래그만 세팅한다(goToPage() 자체 시그니처는 다른 호출부
  // 다수가 공유하므로 변경하지 않는다).
  function goToPageFromPager(n) {
    exitDeepLinkMode(); // [PR-0 딥링크] 페이지 조작 → 딥링크 모드 종료(§4)
    exitSimilarMode(); // [FIND-1 S1] 페이지 조작 → 유사검색 모드 종료(§6)
    pendingScrollAfterNav = true;
    goToPage(n);
  }

  // [단독 렌더 모드 공통] 페이저 3종을 함께 숨긴다 — 상/하단 페이저 + sticky 미니 내비
  // (#fnd-pnav, PR#231). ★pnav 를 빼먹으면 딥링크·유사검색처럼 "페이지가 없는" 단독
  // 렌더 모드에서 sticky 툴바에 ‹ › 화살표만 덩그러니 남는다(프리뷰 실측으로 발견).
  // 복귀는 render()→renderPager()→updatePnav() 가 상태 기준으로 되살린다.
  function hidePager() {
    if (pagerTopEl) pagerTopEl.hidden = true;
    if (pagerBottomEl) pagerBottomEl.hidden = true;
    if (pnavEl) pnavEl.hidden = true;
  }

  // 페이지 이동 중(네트워크 대기) 버튼을 비활성화한다 — render() 가 뒤이어 pager 를
  // 완전히 새로 그리므로 별도 "재활성화" 처리는 필요 없다. [로딩 UX b] 현재 페이지 pill
  // (.on)이 있으면 그 자리에서 바로 "불러오는 중…"으로 바꿔 보여주고(없으면 — 현재 페이지가
  // "..." 로 축약된 윈도우 밖일 때 — nav 끝에 별도 상태 텍스트를 붙인다), 무반응처럼
  // 보이던 문제를 없앤다.
  function setPagerLoading(loading) {
    // [sticky 미니 내비] 로딩 중 연타 방어 — pnav 도 페이저와 함께 잠근다(해제는
    // 로딩 종료 후 render()→renderPager()→updatePnav() 가 상태 기준으로 되살린다).
    if (loading && pnavPrevBtn) pnavPrevBtn.disabled = true;
    if (loading && pnavNextBtn) pnavNextBtn.disabled = true;
    [pagerTopEl, pagerBottomEl].forEach(function (nav) {
      if (!nav) return;
      nav.setAttribute("aria-busy", loading ? "true" : "false");
      if (loading) {
        Array.prototype.forEach.call(nav.querySelectorAll("button"), function (b) { b.disabled = true; });
        var current = nav.querySelector(".fnd-pager-btn.on");
        if (current) {
          current.textContent = "불러오는 중…";
        } else {
          var status = document.createElement("span");
          status.className = "fnd-pager-status";
          status.setAttribute("aria-live", "polite");
          status.textContent = "불러오는 중…";
          nav.appendChild(status);
        }
      }
    });
  }

  // "1 … 4 [5] 6 … 20" 식 페이지 윈도우 — 전체 7페이지 이하면 생략 없이 전부, 아니면
  // 처음·끝 고정 + 현재 페이지 좌우 1개씩 + 그 사이는 "..." 로 축약한다.
  function computePageWindow(current, total) {
    if (total <= 7) {
      var all = [];
      for (var i = 1; i <= total; i++) all.push(i);
      return all;
    }
    var items = [1];
    var start = Math.max(2, current - 1);
    var end = Math.min(total - 1, current + 1);
    if (start > 2) items.push("...");
    for (var j = start; j <= end; j++) items.push(j);
    if (end < total - 1) items.push("...");
    items.push(total);
    return items;
  }

  function buildPagerBtn(label, ariaLabel, disabled, onClick) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fnd-pager-btn";
    btn.textContent = label;
    btn.setAttribute("aria-label", ariaLabel);
    btn.disabled = !!disabled;
    btn.addEventListener("click", onClick);
    return btn;
  }

  // [문서 단위 페이지네이션] 이전/다음 + 페이지 번호(현재 주변 윈도우) + 처음/끝 점프.
  // moreMayExist 면 마지막(=지금까지 알려진) 페이지 번호에 "+" 를 붙이고(최소 추정 표기)
  // 다음/끝 버튼도 그 지점에서 비활성화하지 않는다 — 항상 열어둬서, 클릭하면 goToPage()
  // 가 필요한 만큼 서버 청크를 이어서 fetch 한다. 상단·하단 두 nav 모두 완전히 동일한
  // 내용을 렌더한다(둘 다 goToPage() 를 공유 — 중복 클릭 방어·로딩 표시가 자동 동기화).
  function renderPager(current, total, moreMayExist) {
    updatePnav(current, total, moreMayExist); // [sticky 미니 내비] 항상 페이저와 동기
    [pagerTopEl, pagerBottomEl].forEach(function (nav) {
      if (!nav) return;
      nav.textContent = "";
      nav.setAttribute("aria-busy", "false");
      if (total <= 1 && !moreMayExist) {
        nav.hidden = true;
        return;
      }
      nav.hidden = false;
      nav.appendChild(buildPagerBtn("«", "처음 페이지로 이동", current === 1, function () { goToPageFromPager(1); }));
      nav.appendChild(buildPagerBtn("‹ 이전", "이전 페이지", current === 1, function () { goToPageFromPager(current - 1); }));
      computePageWindow(current, total).forEach(function (item) {
        if (item === "...") {
          var gap = document.createElement("span");
          gap.className = "fnd-pager-gap";
          gap.setAttribute("aria-hidden", "true");
          gap.textContent = "…";
          nav.appendChild(gap);
          return;
        }
        var isLastKnown = item === total;
        var label = String(item) + (isLastKnown && moreMayExist ? "+" : "");
        var btn = buildPagerBtn(label, "페이지 " + item + "로 이동", false, function () { goToPageFromPager(item); });
        if (item === current) {
          btn.classList.add("on");
          btn.setAttribute("aria-current", "page");
          btn.disabled = true;
        }
        nav.appendChild(btn);
      });
      var atKnownEnd = current >= total && !moreMayExist;
      nav.appendChild(buildPagerBtn("다음 ›", "다음 페이지", atKnownEnd, function () { goToPageFromPager(current + 1); }));
      nav.appendChild(buildPagerBtn("»", "끝 페이지로 이동", atKnownEnd, function () { goToPageFromPager(total); }));
    });
  }

  // [공개 범위 투명성] findings_stats RPC(007) — 공개 게이트(006)를 우회해 전량 집계를
  // 반환한다(trends.js 와 동일 계약, 원문 텍스트는 내려주지 않는 안전 계약도 동일). 이
  // 페이지의 공개분(게이트 통과분만)과 이 RPC 의 전량 집계 사이 간극을 사용자에게 정직하게
  // 알리는 것이 목적이다 — 실패해도 독립적으로 조용히 숨김 유지(아래 .catch()).
  // ★이 RPC 는 **커버리지 노트 전용**이다. 파셋·대시보드·총수는 findings_search 가 정본이며
  // (검색·필터가 반영된 값이 필요하다) 여기서 파생시키면 두 진실이 생긴다. 이 노트가
  // findings_search 로 대체되지 않는 이유는 딱 하나 — 비공개분을 포함한 전량(raw_signals
  // 총계 등 findings 밖 정보)은 공개 게이트를 통과하는 findings_search 로는 볼 수 없다.
  function fetchCoverageNote() {
    if (!coverageNoteEl || !coverageTextEl) return;
    fetch(url.replace(/\/$/, "") + "/rest/v1/rpc/findings_stats", {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    })
      .then(function (r) {
        if (!r.ok) throw new Error("findings_stats " + r.status);
        return r.json();
      })
      .then(function (data) {
        var totals = (data && data.totals) || {};
        var pub = Number(totals.public_findings || 0).toLocaleString("ko-KR");
        var total = Number(totals.findings || 0).toLocaleString("ko-KR");
        // [문서 수 병기] totals.documents(010_findings_scope_purity.sql 신규 키)가 있을
        // 때만 "규제 문서 N건 · 지적사항 M건 중 P건 국문 열람 가능"으로 문서-지적 관계를
        // 명시한다. 010 미적용 라이브(undefined)에서는 문서 수 없는 문안을 유지한다(방어적 생략).
        var hasDocs = typeof totals.documents === "number" && !isNaN(totals.documents);
        // [완역 자동 전환] 미번역 잔량이 5건 이하면(번역 3레인 소진 시점 — 잔여는 OCR
        // 완파손 등 번역 불능 원문뿐) 미완료 문안을 완료형으로 스스로 전환한다 — 완역
        // 도달에 맞춘 별도 배포가 필요 없도록 조건을 미리 심어둔 것.
        var isComplete =
          Number(totals.findings || 0) > 0 &&
          Number(totals.findings || 0) - Number(totals.public_findings || 0) <= 5;
        // 미완료 분기는 2026-07-15 백로그 완역 이후엔 당일 수집분이 다음 날 아침 번역
        // 배치를 기다리는 짧은 구간에만 나타난다 — "번역이 밀려 있다"가 아니라 "신규분이
        // 번역 중"으로 읽히도록 지연 사유를 덧붙인다("N건 중 M건 국문 열람 가능" 골격과
        // 완역 자동 전환(isComplete) 분기 자체는 그대로 유지).
        coverageTextEl.textContent = isComplete
          ? (hasDocs
              ? "규제 문서 " + Number(totals.documents).toLocaleString("ko-KR") + "건 · 지적사항 " +
                total + "건 전체를 국문으로 열람할 수 있습니다."
              : "전체 " + total + "건을 국문으로 열람할 수 있습니다.")
          : (hasDocs
              ? "규제 문서 " + Number(totals.documents).toLocaleString("ko-KR") + "건 · 지적사항 " +
                total + "건 중 " + pub + "건 국문 열람 가능"
              : "지적사항 " + total + "건 중 " + pub + "건 국문 열람 가능") +
            " — 신규 수집분은 국문 번역을 거쳐 다음 날 공개됩니다.";
        coverageNoteEl.hidden = false;
      })
      .catch(function () {
        // 조용히 숨김 유지 — 검색 페이지 본기능(검색·필터)과 무관한 독립 폴백.
      });
  }

  showState("loading");
  fetchCoverageNote();
  // [PR-0 딥링크] finding_id 파라미터가 있으면 목록 fetch 와 병렬로 문서 조회를 시작한다 —
  // 어느 쪽이 먼저 끝나든 maybeFinishInit() 이 둘 다 끝난 뒤 한 번만 확정 렌더한다(깜빡임
  // 없음). 파라미터 자체가 없으면 deepLinkPending=false 로 시작해 아래 로직 전체가 기존
  // 동작과 완전히 동일하게(no-op) 흘러간다(§7 회귀 0).
  var requestedFindingId = getDeepLinkParam();
  if (requestedFindingId) {
    deepLinkParam = requestedFindingId;
    deepLinkPending = true;
    resolveDeepLink(requestedFindingId);
  }
  // ★초기화 순서: URL 읽기 → 첫 fetch → 파셋 골격 → 컨트롤 동기화 → 배선 → 확정 렌더.
  // 종전엔 fetch 가 맨 앞이었지만(로드분 위에서 필터링했으므로 URL 상태가 요청과 무관),
  // 이제 state 가 곧 요청 파라미터라 URL 을 **먼저** 읽어야 첫 요청이 옳게 나간다.
  // 파셋 골격은 반대로 서버 응답(LAST.facets)이 있어야 만들 수 있어 fetch 뒤로 간다 —
  // 그래서 readStateFromUrl() 은 파셋 값 검증 의존을 끊었다(해당 함수 주석 참조).
  // 컨트롤 동기화(select.value 대입)는 반드시 골격 **뒤**여야 한다 — <option> 이 없는
  // 값을 대입하면 조용히 무시된다(종전에도 같은 순서였다).
  readStateFromUrl();
  currentPage = readPageFromUrl();
  navToken += 1;
  var initToken = navToken;
  fetchSearch(currentPage)
    .then(function (data) {
      if (initToken !== navToken) return; // 첫 응답 도착 전에 이미 다른 조작이 앞질렀다
      LAST = data;
      buildFacetSkeleton();
      syncControlsFromState();
      wire();
      rowsReady = true;
      // [PR-0 딥링크] 최종 렌더는 maybeFinishInit() 이 상황에 맞게 부른다(found=문서 카드
      // 단독 렌더, notfound/일반=목록 렌더 — 첫 응답이 이미 LAST 에 있어 재요청 없음).
      maybeFinishInit();
    })
    .catch(function () {
      // [PR-0 딥링크] 목록 fetch 가 실패해도 이미 확정된 딥링크 단건 렌더는 덮어쓰지 않는다.
      if (deepLinkStatus !== "found") showState("error");
    });
})();
