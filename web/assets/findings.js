/* GRM 지적사항 검색 (FIND-1 M3c, 국문 우선표시+카테고리 라벨 M6d, 대시보드 밴드 M7,
 * 카드 UX 오버홀 M10b, 탐색 툴바 오버홀 M10c, 문서 중심 열람 재편) — 정적·클라이언트사이드,
 * 순수 fetch(PostgREST 직접 호출).
 *
 * [문서 중심 열람] 열람 단위는 observation 조각이 아니라 문서·업체다(Redica 등 상용
 * 규제 인텔리전스 검증 패턴) — 같은 문서(raw_signal_id 동일)의 지적사항을 groupByDocument()
 * 로 묶어 buildDocCard() 문서 카드 1장으로 렌더한다. observation 단위 카드(buildCard())
 * 자체는 무변경으로 문서 카드 내부에 재사용된다.
 *
 * supabase-js CDN 미사용 — 인증 불필요한 anon SELECT 뿐이라 REST 엔드포인트를 직접 fetch 한다.
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

  // 신규(M6d) 필드 포함 전체 목록. 라이브 DB 에 005(finding_text_ko/translation_method)가
  // 아직 적용되지 않은 경우 PostgREST 가 알 수 없는 컬럼에 400 을 반환하므로, 그 경우에만
  // LEGACY_FIELDS(신규 2컬럼 제외)로 1회 재시도한다 — 배포 순서와 무관하게 페이지가 절대
  // 깨지지 않도록 하는 폴백이다.
  // [문서 중심 열람] raw_signal_id = 문서 정체성 키(002_findings.sql: findings.raw_signal_id
  // → raw_signals.raw_signal_id FK). anon RLS(003)는 행 필터만 있고 컬럼 제한이 없어
  // select 목록에 추가해도 무해하다 — 같은 문서의 지적사항을 그룹핑하는 데만 쓴다.
  // [업체 프로파일 진입] firm_key = 013_findings_firm_key.sql 의 generated 컬럼(문서 카드
  // 헤더 업체명 → /findings/firm/?key= 링크용). 013 이 라이브 DB 에 아직 적용되지 않았을
  // 수 있으므로(방어 설계) FIELDS_NO_FIRM_KEY 로 한 단계 더 재시도한다 — raw_signal_id
  // 폴백(005 미적용)과 독립적인 별개 실패 축이라 3단계 폴백 체인이 된다: FIELDS(전체) →
  // FIELDS_NO_FIRM_KEY(013 미적용, 005 는 적용) → LEGACY_FIELDS(013·005 둘 다 미적용).
  var FIELDS = [
    "finding_id", "source", "agency", "document_id", "published_date",
    "firm_name", "firm_key", "category_code", "category_label_ko", "finding_text",
    "finding_text_ko", "translation_method",
    "finding_language", "evidence_level", "evidence_url", "cfr_refs",
    "mfds_refs", "review_status", "confidence", "raw_signal_id",
  ];
  var FIELDS_NO_FIRM_KEY = FIELDS.filter(function (f) {
    return f !== "firm_key";
  });
  var LEGACY_FIELDS = FIELDS_NO_FIRM_KEY.filter(function (f) {
    return f !== "finding_text_ko" && f !== "translation_method";
  });

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
  // 없앤다. state.agency·URL param(agency)·rowMatchesFilters/searchTermsFor 의 매칭 로직은
  // 그대로 유지한다 — URL 로 agency 파라미터가 들어오면 여전히 필터가 적용된다(하위호환).
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
  var ROWS = null; // fetch 성공 시 findings 배열
  var debounceTimer = null;

  // [문서 단위 페이지네이션] "더 보기" 무한로드 → 문서 24개=1페이지 이전/다음 페이지네이션
  // 으로 전환(§목표: 페이지 경계에서 문서가 절대 쪼개지지 않는다). PAGE_LIMIT=서버 청크
  // fetch 단위(obs 행 기준, 기존 limit=1000 그대로 유지 — 서버 왕복 최소화). DOCS_PER_PAGE=
  // 화면에 보여줄 문서 카드 수. SERVER_TOTAL=Content-Range 헤더(Prefer: count=exact 응답)
  // 에서 파싱한 서버측 obs exact count — 파싱 실패/헤더 미노출 환경이면 null(폴백).
  // LOADED_FIELDS=최초 3단계 폴백 체인 중 실제로 성공한 필드 리스트(로드 완료 후 고정 —
  // 페이지 이동으로 인한 추가 fetch 는 매번 재협상하지 않고 이 필드셋을 재사용한다).
  // LAST_BATCH_SIZE=가장 최근 fetch 가 반환한 행 수(SERVER_TOTAL 미확보 환경에서 "이번
  // 청크가 PAGE_LIMIT 로 꽉 찼으니 더 있을 수 있다"는 방어적 휴리스틱에 쓴다). fetchGaveUp=
  // 청크 fetch 실패 시 영구 플래그(무한 재시도 방지 — 이후 로드된 데이터만으로 계속
  // 동작). isFetchingPage/pendingPageCallbacks=중복 fetch 방어(여러 페이지 이동이 겹쳐도
  // 실제 네트워크 요청은 1개만 진행, 나머지는 그 결과에 편승). currentPage=1-based 현재
  // 페이지. navToken=페이지 이동 연타 방어용 세대 카운터(오래된 이동의 완료 콜백 무시).
  var PAGE_LIMIT = 1000;
  var DOCS_PER_PAGE = 24;
  var SERVER_TOTAL = null;
  var LOADED_FIELDS = null;
  var LAST_BATCH_SIZE = null;
  var fetchGaveUp = false;
  var isFetchingPage = false;
  var pendingPageCallbacks = [];
  var currentPage = 1;
  var navToken = 0;
  // [정확 총수 M1a] findings_stats RPC(fetchCoverageNote() 가 독립적으로 fetch)의
  // totals.documents/totals.findings — 무필터 기준 exact 값(로드 진행과 무관). 무필터 +
  // 서버 미소진 구간에서 render() 가 이 값으로 문서수·지적수·총 페이지를 정확히 표시하고,
  // 끝(») 점프의 목표 페이지로도 그대로 쓴다(더 이상 "로드된 만큼"이 아니라 진짜 마지막
  // 페이지 1클릭). RPC 실패/010 미적용 라이브에서는 null 유지 — 기존 로드 기준 폴백.
  var SERVER_DOC_TOTAL = null;
  var SERVER_FINDINGS_TOTAL = null;
  // [대시보드 실총수 M3] findings_stats RPC 의 by_agency_category 를 agency 기준으로 합산한
  // {agency: count} — 무필터 대시보드 스탯의 소스별(FDA/MFDS) 분해를 정확화한다. null 이면
  // (RPC 실패 등) 대시보드는 기존처럼 로드된 데이터 기준(computeAgencyDist)으로 폴백한다.
  var SERVER_AGENCY_TOTALS = null;
  // [선로딩 c] 페이지네이션 버튼(처음/이전/번호/다음/끝) 클릭으로 촉발된 이동에서만 완료
  // 후 결과 목록 상단으로 스크롤한다 — goToPageFromPager() 가 세팅, goToPage() 의 완료
  // 콜백이 소비 후 즉시 리셋한다. 필터/검색/정렬 변경발 goToPage(1) 리셋은 스크롤하지
  // 않는다(검색창에 타이핑할 때마다 화면이 튀는 것을 방지).
  var pendingScrollAfterNav = false;

  // [PR-0 딥링크] deepLinkParam=URL 에서 읽은 원본 finding_id 값 — exitDeepLinkMode() 가
  // 지울 때까지 유지되며(found/notfound 상태와 무관하게 "이 세션에 딥링크 관심사가 아직
  // 살아있다"는 단일 플래그), 파라미터 자체가 없으면 처음부터 null 이라 이하 전 로직이
  // no-op 로 남아 일반 모드 회귀가 0이다. deepLinkStatus="found"|"notfound"|""(미확정/비활성).
  // rowsReady=일반 목록 fetch 완료 여부 — maybeFinishInit() 이 딥링크 해석 완료와 함께
  // 둘 다 기다렸다가 깜빡임 없이 한 번만 최종 렌더를 확정한다.
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

  // [공개 범위 투명성] 커버리지 노트 — 메인 fetchFindings() 와 완전히 독립된 별도 fetch
  // (findings_stats RPC, trends.js 와 동일 엔드포인트). 성공 시에만 노트를 채우고 노출한다
  // — 실패(RPC 미존재 등)해도 이 노트만 hidden 유지, 검색 페이지 본기능엔 영향 없다.
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

  function monthOf(row) {
    var d = row.published_date || "";
    return d.length >= 7 ? d.slice(0, 7) : "";
  }

  // 값 전체 목록(현재 필터와 무관, ROWS 전체 기준) — 칩/옵션 자체는 한 번만 만들고,
  // 이후 render() 마다 건수·disabled·on 상태만 갱신한다(DOM 재생성 없음).
  function collectFacetValues(key2) {
    var vals = {};
    ROWS.forEach(function (r) {
      var v = key2 === "month" ? monthOf(r) : r[key2];
      if (v) vals[v] = true;
    });
    var sorted = Object.keys(vals).sort();
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

  function arrayTerms(v) {
    return Array.isArray(v) ? v : [];
  }

  // [FIND-1 M10c P0] 사용자가 화면에서 보는 배지·칩·드롭다운 라벨도 검색 대상이다.
  // "MFDS", "FDA 483", "Evidence A", "검토 필요", "문서화", "2026-07" 처럼
  // 가시 메타데이터로 찾는 흐름을 보장한다.
  function searchTermsFor(row) {
    var cat = CATEGORY_LABELS[row.category_code] || {};
    var evidence = EVIDENCE_LABEL[row.evidence_level] || "";
    var status = STATUS_LABEL[row.review_status] || "";
    var reviewStatusPlain = row.review_status ? String(row.review_status).replace(/_/g, " ") : "";
    return [
      row.finding_text, row.finding_text_ko, row.firm_name, row.document_id,
      row.agency, row.source, row.published_date, monthOf(row),
      row.evidence_level, evidence, row.evidence_level ? "증거 " + row.evidence_level : "",
      row.review_status, reviewStatusPlain, status,
      row.category_code, row.category_label_ko, cat.ko, cat.en,
      row.translation_method,
    ].concat(arrayTerms(row.cfr_refs), arrayTerms(row.mfds_refs));
  }

  // [M15] 소스/증거등급/검토상태/카테고리/발행월 <select> 5개의 DOM 골격(옵션)을 데이터
  // 로드 직후 1회만 만든다. change 배선은 wire()에서(전 필드 동일 경로 — SELECT_FACETS 단일).
  // [페이지네이션] 페이지 이동으로 청크가 추가 fetch 돼 ROWS 가 늘어난 뒤에도 새로 드러난
  // 값을 옵션으로 추가할 수 있도록 재호출 가능(idempotent)하게 만든다 — 이미 존재하는
  // 옵션 값은 건너뛰어 중복 <option> 이 생기지 않는다.
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

  // rowMatchesFilters(row, exclude) — exclude 로 지정한 파셋 키 하나만 제외하고 나머지
  // 활성 필터+검색어를 적용한다. matches(row)=exclude 없이 전체 적용(기존 계약과 동일).
  // 표준 파세팅 건수(옆 칩/옵션이 "이 값을 골랐다면 몇 건" 인지)는 이 함수로 계산한다.
  function rowMatchesFilters(row, exclude) {
    if (exclude !== "agency" && state.agency && row.agency !== state.agency) return false;
    if (exclude !== "category_code" && state.category_code && row.category_code !== state.category_code) return false;
    if (exclude !== "source" && state.source && row.source !== state.source) return false;
    if (exclude !== "evidence_level" && state.evidence_level && row.evidence_level !== state.evidence_level) return false;
    if (exclude !== "review_status" && state.review_status && row.review_status !== state.review_status) return false;
    if (exclude !== "month" && state.month && monthOf(row) !== state.month) return false;
    var q = state.q.trim().toLowerCase();
    if (q) {
      var hay = searchTermsFor(row)
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }
    return true;
  }

  function matches(row) {
    return rowMatchesFilters(row, null);
  }

  // key2 값별 건수 — "검색어 + 그 파셋을 제외한 나머지 활성 필터" 적용 결과 기준(표준
  // 파세팅). 셀렉트/칩 렌더 갱신(refreshFacetUI)이 render() 마다 호출한다.
  function computeFacetCounts(key2) {
    var counts = {};
    ROWS.forEach(function (r) {
      if (!rowMatchesFilters(r, key2)) return;
      var v = key2 === "month" ? monthOf(r) : r[key2];
      if (!v) return;
      counts[v] = (counts[v] || 0) + 1;
    });
    return counts;
  }

  // [M15] 셀렉트 옵션 라벨(건수 병기)을 매 render() 마다 갱신한다 — DOM 엘리먼트는
  // buildFacetSkeleton()이 1회 만든 것을 재사용(재생성 없음).
  function refreshFacetUI() {
    SELECT_FACETS.forEach(function (def) {
      var selId = def[0], key2 = def[1];
      var sel = document.getElementById(selId);
      if (!sel) return;
      var counts = computeFacetCounts(key2);
      Array.prototype.forEach.call(sel.options, function (opt) {
        if (!opt.value) return; // "전체" 옵션은 건수 병기 대상 아님
        var count = counts[opt.value] || 0;
        opt.textContent = opt.dataset.label + " (" + count + ")";
        opt.disabled = count === 0 && sel.value !== opt.value;
      });
    });
  }

  // ── [FIND-1 M7] 대시보드 집계 — 순수 함수(입력=현재 필터 결과 rows, 부작용 없음). ──────
  function computeAgencyDist(rows) {
    var counts = {};
    rows.forEach(function (r) {
      var a = r.agency || "";
      if (!a) return;
      counts[a] = (counts[a] || 0) + 1;
    });
    return Object.keys(counts)
      .map(function (a) { return { agency: a, count: counts[a] }; })
      .sort(function (x, y) { return y.count - x.count || x.agency.localeCompare(y.agency); });
  }

  function computeCategoryDist(rows) {
    var counts = {};
    rows.forEach(function (r) {
      var code = r.category_code || "";
      if (!code) return;
      counts[code] = (counts[code] || 0) + 1;
    });
    return Object.keys(counts)
      .map(function (code) {
        var cat = CATEGORY_LABELS[code];
        return { code: code, ko: cat ? cat.ko : code, count: counts[code] };
      })
      .sort(function (a, b) { return b.count - a.count || a.code.localeCompare(b.code); });
  }

  function computeMonthTrend(rows) {
    var counts = {};
    rows.forEach(function (r) {
      var m = monthOf(r);
      if (!m) return;
      counts[m] = (counts[m] || 0) + 1;
    });
    var months = Object.keys(counts).sort(); // published_month 오름차순
    if (months.length > 12) months = months.slice(months.length - 12); // 최근 12개월
    return months.map(function (m) { return { month: m, count: counts[m] }; });
  }

  function computeFirmTop(rows) {
    var counts = {};
    rows.forEach(function (r) {
      var f = (r.firm_name || "").trim();
      if (!f) return;
      counts[f] = (counts[f] || 0) + 1;
    });
    return Object.keys(counts)
      .map(function (name) { return { name: name, count: counts[name] }; })
      .sort(function (a, b) { return b.count - a.count || a.name.localeCompare(b.name); })
      .slice(0, 5);
  }

  function computeStats(rows) {
    // [FIND-1 M9a] 미번역 건수 집계는 여기서 더 이상 계산하지 않는다 — 공개 게이트
    // (006_findings_publish_gate.sql)가 DB 레벨에서 국문 해석이 없는(finding_text_ko=''
    // 이고 finding_language!='KO') 행을 anon fetch 결과 자체에서 차단하므로, 클라이언트가
    // 세는 값은 항상 0에 수렴해 오해를 일으킨다.
    return {
      total: rows.length,
      agencies: computeAgencyDist(rows),
      needsReview: rows.filter(function (r) { return r.review_status === "needs_review"; }).length,
      categories: computeCategoryDist(rows),
      months: computeMonthTrend(rows),
      firms: computeFirmTop(rows),
    };
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
    // [대시보드 실총수 M3] renderDash() 가 무필터+SERVER_DOC_TOTAL 확보 시에만 채우는
    // 값 — 필터 상태·RPC 미확보에서는 stats.documents 자체가 없어(undefined) 조용히
    // 생략된다(레이아웃 깨짐 없음, 기존 옵셔널 스탯들과 동일한 방어적 관례).
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
      makeClickableRow(row, label + " 카테고리로 필터: " + count + "건", function () {
        toggleCategoryFilter(code);
      });
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
      makeClickableRow(col, x.month + " " + x.count + "건", function () {
        toggleMonthFilter(x.month);
      });
      col.title = x.month + " " + x.count + "건";
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
      // [firm_name 엔티티 디코드 M5] 클릭/필터는 raw f.name(DB 원본값) 그대로 써야 검색
      // state.q·rowMatchesFilters 매칭이 어긋나지 않는다 — 디코드는 표시(라벨·툴팁)에만.
      var firmDisplay = decodeFirmDisplay(f.name);
      makeClickableRow(row, firmDisplay + " 검색: " + f.count + "건", function () {
        toggleFirmFilter(f.name);
      });
      row.appendChild(el("span", "fnd-dash-firm-name", firmDisplay));
      row.appendChild(el("span", "fnd-dash-firm-count", String(f.count)));
      dashFirmEl.appendChild(row);
    });
  }

  function renderDash(matched) {
    if (!hasDash) return;
    if (!matched.length) {
      dashEl.hidden = true;
      return;
    }
    var stats = computeStats(matched);
    // [대시보드 실총수 M3] 필터가 하나도 없을 때만(=matched 가 로드된 전체라는 모집단)
    // findings_stats RPC 의 exact 총수로 스탯을 바꿔치기한다 — "전체 1000" 처럼 로드된
    // 행 수가 서버 총수(예: 2,272+)로 오인되는 신고에 대응. 필터가 걸린 상태에서는
    // matched.length 자체가 "필터링된 결과 전체"라는 다른 모집단이라 서버 총수로 바꾸면
    // 오히려 더 오해를 만든다 — countActiveFilters()/state.q.trim() 로 자체 판정한다.
    var filtersActive = countActiveFilters() > 0 || !!state.q.trim();
    if (!filtersActive) {
      // 전체(지적) — RPC exact 우선, 실패 시 Content-Range exact(SERVER_TOTAL) 폴백,
      // 그마저 없으면 로드 수(stats.total 원래값) 유지.
      if (SERVER_FINDINGS_TOTAL !== null) {
        stats.total = SERVER_FINDINGS_TOTAL;
      } else if (SERVER_TOTAL !== null && SERVER_TOTAL > matched.length) {
        stats.total = SERVER_TOTAL;
      }
      // 문서 — RPC 에만 있는 값이라 폴백 없음(미확보면 스탯 자체를 생략, renderDashStats 참조).
      if (SERVER_DOC_TOTAL !== null) {
        stats.documents = SERVER_DOC_TOTAL;
      }
      // FDA/MFDS 소스별 분해 — RPC 확보 시 exact 로 교체하고 시각적으로 구분
      // (agenciesExact=false 이면 renderDashStats() 가 "로드된 데이터 기준" 툴팁을 단다).
      if (SERVER_AGENCY_TOTALS !== null) {
        stats.agencies = Object.keys(SERVER_AGENCY_TOTALS)
          .map(function (a) { return { agency: a, count: SERVER_AGENCY_TOTALS[a] }; })
          .sort(function (x, y) { return y.count - x.count || x.agency.localeCompare(y.agency); });
        stats.agenciesExact = true;
      }
    }
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
    var moreBtn = buildMoreToggle(card);
    actions.appendChild(moreBtn);
    card.appendChild(actions);

    return { card: card, textEl: textEl, extraEl: extra, moreBtn: moreBtn };
  }

  // ── [문서 중심 열람] observation 조각이 아니라 문서·업체 단위로 열람한다(Redica 등
  // 상용 규제 인텔리전스의 검증된 패턴 — observation 조각은 집계 엔진 내부용). 같은
  // 문서(raw_signal_id 동일)의 지적사항을 문서 카드 1장으로 묶는다. ────────────────────
  // matched(정렬 완료) 배열의 순서를 그대로 보존하며 병합만 한다 — 문서 순서는 그 문서의
  // 첫 지적사항이 정렬 결과에서 나타나는 위치로 결정되고(published_date 최신순 등 기존
  // 정렬 그대로), 문서 내부 지적사항 순서도 기존 정렬을 그대로 유지한다(재정렬 없음).
  // raw_signal_id 가 없는 행(legacy fetch 폴백 등 방어적 케이스)은 홀로 자기 그룹을
  // 이룬다 — 그룹핑 실패가 검색 결과 누락으로 이어지지 않게 한다.
  function groupByDocument(rows) {
    var order = [];
    var byKey = {};
    rows.forEach(function (row) {
      var key = row.raw_signal_id || ("__standalone__" + (row.finding_id || order.length));
      if (!byKey[key]) {
        byKey[key] = [];
        order.push(key);
      }
      byKey[key].push(row);
    });
    return order.map(function (key) { return byKey[key]; });
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
  // 재사용: 카테고리 칩·국문 우선·원문 details 접기·LEGACY_FIELDS 폴백 전부 무변경).
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

  // [FIND-1 M10c] 정렬 — 최신순(기본, published_date desc → finding_id asc)/오래된순/
  // 업체명순(localeCompare, 동순위는 published_date desc). 순수 함수(원본 배열 비파괴).
  function sortRows(rows) {
    var sorted = rows.slice();
    if (state.sort === "date_asc") {
      sorted.sort(function (a, b) {
        var d = (a.published_date || "").localeCompare(b.published_date || "");
        if (d !== 0) return d;
        return String(a.finding_id || "").localeCompare(String(b.finding_id || ""));
      });
    } else if (state.sort === "firm_asc") {
      sorted.sort(function (a, b) {
        var c = (a.firm_name || "").localeCompare(b.firm_name || "");
        if (c !== 0) return c;
        return (b.published_date || "").localeCompare(a.published_date || "");
      });
    } else {
      sorted.sort(function (a, b) {
        var d = (b.published_date || "").localeCompare(a.published_date || "");
        if (d !== 0) return d;
        return String(a.finding_id || "").localeCompare(String(b.finding_id || ""));
      });
    }
    return sorted;
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

  // URL→state(초기 로드 1회) — 알 수 없는/무효한 값은 조용히 무시한다(오류 없이 기본값 유지).
  // ROWS·buildFacetSkeleton() 이후에 호출해야 collectFacetValues 로 유효값 검증이 가능하다.
  function readStateFromUrl() {
    if (typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    var qv = params.get(URL_KEYS.q);
    if (qv !== null) state.q = qv;
    ["agency", "category_code", "source", "evidence_level", "review_status", "month"].forEach(function (k) {
      var raw = params.get(URL_KEYS[k]);
      if (raw === null) return;
      if (collectFacetValues(k).indexOf(raw) !== -1) state[k] = raw;
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
  // 우선순위: ①형식 불일치 → fetch 없이 즉시 notfound ②단건 조회(FIELDS 3단계 폴백
  // 재사용) → 빈 결과(RLS 비공개 포함) = notfound ③raw_signal_id 로 같은 문서 전체
  // 재조회 → groupByDocument()+buildDocCard() 로 문서 카드 1장 렌더. found/notfound 는
  // 사용자가 필터·검색·정렬·페이지를 조작하는 순간 exitDeepLinkMode() 로 종료되고 일반
  // 모드로 전환된다(§4). 비공개·미존재·형식오류는 전부 동일한 notfound 배너로 수렴한다
  // (§7, 존재 여부 정보 누설 금지).
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

  function fetchFindingsFiltered(fields, filterQS) {
    var cols = fields.join(",");
    var endpoint =
      url.replace(/\/$/, "") + "/rest/v1/findings?select=" +
      encodeURIComponent(cols).replace(/%2C/g, ",") + "&" + filterQS +
      "&order=published_date.desc,finding_id.asc";
    return fetch(endpoint, { headers: { apikey: key, Authorization: "Bearer " + key } });
  }

  // 기존 3단계 FIELDS 폴백 체인(findings.js 파일 상단 §FIELDS 주석 계약)과 동일한 구조를
  // finding_id/raw_signal_id 필터 조회에도 그대로 재사용한다(§2·§3).
  function fetchDeepLinkFiltered(filterQS) {
    return fetchFindingsFiltered(FIELDS, filterQS).then(function (r) {
      if (r.ok) return r.json();
      return fetchFindingsFiltered(FIELDS_NO_FIRM_KEY, filterQS).then(function (r2) {
        if (r2.ok) return r2.json();
        return fetchFindingsFiltered(LEGACY_FIELDS, filterQS).then(function (r3) {
          if (!r3.ok) throw new Error("findings deep link fetch " + r3.status);
          return r3.json();
        });
      });
    });
  }

  function resolveDeepLink(id) {
    if (!isValidFindingId(id)) {
      deepLinkStatus = "notfound";
      deepLinkPending = false;
      maybeFinishInit();
      return;
    }
    fetchDeepLinkFiltered("finding_id=eq." + encodeURIComponent(id))
      .then(function (rows) {
        if (!Array.isArray(rows) || !rows.length) {
          deepLinkStatus = "notfound";
          deepLinkPending = false;
          maybeFinishInit();
          return;
        }
        var target = rows[0];
        var rsid = target.raw_signal_id;
        if (!rsid) {
          // 방어적 폴백 — raw_signal_id 가 없는 행(legacy 등)은 단건 자체를 문서로 취급.
          deepLinkDocRows = [target];
          deepLinkStatus = "found";
          deepLinkPending = false;
          maybeFinishInit();
          return;
        }
        return fetchDeepLinkFiltered("raw_signal_id=eq." + encodeURIComponent(rsid)).then(function (docRows) {
          deepLinkDocRows = (Array.isArray(docRows) && docRows.length) ? docRows : [target];
          deepLinkStatus = "found";
          deepLinkPending = false;
          maybeFinishInit();
        });
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
  // 무관하게 단독 렌더). groupByDocument() 로 재확인 후(방어적) buildDocCard() 를 그대로
  // 재사용한다(§3 — 신규 렌더러를 만들지 않는다).
  function renderDeepLinkDoc() {
    showState("none");
    hidePager();
    if (hasDash) dashEl.hidden = true; // 문서 1건뿐이라 대시보드는 의미가 없다(파괴 아님 — 숨김만)
    resultsEl.textContent = "";
    countEl.textContent = "";
    var groups = groupByDocument(deepLinkDocRows);
    var rows = groups.length ? groups[0] : deepLinkDocRows;
    var doc = buildDocCard(rows, "");
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

  // ROWS 로드(rowsReady)와 딥링크 해석(!deepLinkPending) 이 둘 다 끝나야 최종 렌더를
  // 확정한다 — 어느 쪽이 먼저 끝나든(네트워크 순서 무관) 깜빡임 없이 한 번만 렌더한다.
  // finding_id 파라미터가 애초에 없었으면 deepLinkPending 은 시작부터 false 라 이 함수는
  // rowsReady 만 기다리는 기존 흐름과 완전히 동일하게 동작한다(§7 회귀 0).
  function maybeFinishInit() {
    if (!rowsReady || deepLinkPending) return;
    if (deepLinkStatus === "found") {
      renderDeepLinkDoc();
      return;
    }
    if (deepLinkStatus === "notfound") showDeepLinkNotFoundBanner();
    goToPage(readPageFromUrl());
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

  function render() {
    var matched = sortRows(ROWS.filter(matches));
    var docs = groupByDocument(matched); // [문서 중심 열람] raw_signal_id 로 문서 단위 그룹핑
    renderDash(matched); // [FIND-1 M7] 필터 결과 기준 대시보드 재계산(데이터 없으면 hidden, obs 기준 불변)
    refreshFacetUI(); // [M15] 셀렉트 건수 갱신(표준 파세팅)
    renderActiveChips(); // [M15] 적용 필터 칩 행 재계산
    updateFiltersToggleBadge();

    // [문서 단위 페이지네이션] moreMayExist=서버에 아직 더 받아올 obs 청크가 남아있을 수
    // 있다(isServerExhausted() 참조) — 필터 여부와 무관하게 동일 기준을 쓴다: 필터가
    // 걸려도 ensurePageReady() 가 필요할 때 계속 다음 청크를 당겨오므로, "아직 최소
    // 추정치일 뿐"이라는 신호는 필터 여부와 상관없이 항상 정직해야 한다.
    var moreMayExist = !isServerExhausted();
    // [정확 총수 M1a] 무필터(검색어·필터 전부 비어있음) + 서버 미소진 구간에서는 지금까지
    // 로드된 docs.length 대신 findings_stats RPC 의 exact 총수(SERVER_DOC_TOTAL/
    // SERVER_FINDINGS_TOTAL — fetchCoverageNote() 가 독립적으로 채운다)를 그대로 쓴다 —
    // 로드 진행과 무관하게 항상 정확하다. 서버가 소진되면(전량 로드 완료) 로드된
    // docs.length 자체가 이미 ground truth 이므로 그쪽으로 자연 전환된다(exactUnfiltered
    // 는 moreMayExist 가 꺼지면 함께 꺼진다 — RPC 추정치와의 미세한 오차가 있어도 소진
    // 시점에 스스로 교정됨). 필터가 걸리면 matched 자체가 "필터링된 결과 전체"라는 다른
    // 모집단이라 RPC 총수로 바꿔치기하지 않는다(로드 기준 유지, renderDash() 의 filtersActive
    // 판정과 동일 조건).
    var filtersActive = countActiveFilters() > 0 || !!state.q.trim();
    var exactUnfiltered = !filtersActive && moreMayExist && SERVER_DOC_TOTAL !== null;
    var totalDocsKnown = docs.length;
    var totalFindingsKnown = matched.length;
    var totalPagesKnown;
    if (exactUnfiltered) {
      totalDocsKnown = SERVER_DOC_TOTAL;
      if (SERVER_FINDINGS_TOTAL !== null) totalFindingsKnown = SERVER_FINDINGS_TOTAL;
      totalPagesKnown = Math.max(1, Math.ceil(SERVER_DOC_TOTAL / DOCS_PER_PAGE));
    } else {
      totalPagesKnown = Math.max(1, Math.ceil(totalDocsKnown / DOCS_PER_PAGE));
    }
    if (currentPage > totalPagesKnown) currentPage = totalPagesKnown; // 방어적 클램프(필터 변경 등)
    if (currentPage < 1) currentPage = 1;

    // [문서 중심 열람] "전체 N문서 · M지적 · 페이지 X / Y" — exactUnfiltered 면 N/M/Y 모두
    // findings_stats RPC 의 exact 값이라 접미사를 붙이지 않는다. 그 외(필터 적용 중이거나
    // RPC 미확보)이고 moreMayExist 면 아직 최소 추정치일 뿐이라는 정직한 신호로 숫자 뒤에
    // " 이상"을 붙인다 — 구버전의 "+" 기호보다 명확한 표기(필터 상태에서도 동일하게 적용).
    var uncertain = moreMayExist && !exactUnfiltered;
    countEl.textContent = "";
    countEl.appendChild(document.createTextNode("전체 "));
    var bDocs = document.createElement("b");
    bDocs.textContent = totalDocsKnown.toLocaleString("ko-KR");
    countEl.appendChild(bDocs);
    if (uncertain) countEl.appendChild(document.createTextNode(" 이상"));
    countEl.appendChild(document.createTextNode("문서 · "));
    var bObs = document.createElement("b");
    bObs.textContent = totalFindingsKnown.toLocaleString("ko-KR");
    countEl.appendChild(bObs);
    if (uncertain) countEl.appendChild(document.createTextNode(" 이상"));
    countEl.appendChild(document.createTextNode("지적 · 페이지 "));
    var bCur = document.createElement("b");
    bCur.textContent = String(currentPage);
    countEl.appendChild(bCur);
    countEl.appendChild(document.createTextNode(" / "));
    var bTotal = document.createElement("b");
    bTotal.textContent = String(totalPagesKnown);
    countEl.appendChild(bTotal);
    if (uncertain) countEl.appendChild(document.createTextNode(" 이상"));

    syncStateToUrl(); // [페이지네이션] ?page= 도 여기서 함께 반영(currentPage 확정 이후)

    resultsEl.textContent = "";
    if (!matched.length) {
      showState("empty");
      hidePager();
      return;
    }
    showState("none");
    // [문서 단위 페이지네이션] 문서 24개=1페이지 슬라이스 — obs 가 아니라 문서 경계로
    // 자른다(페이지 경계에서 같은 raw_signal_id 문서가 절대 쪼개지지 않는다. 경계 문서
    // 완결성 보장은 ensurePageReady()/incompleteDocKey() 가 페이지 이동 시점에 이미
    // 확인했으므로, 여기서는 순수 슬라이스만 한다).
    var pageDocs = docs.slice((currentPage - 1) * DOCS_PER_PAGE, currentPage * DOCS_PER_PAGE);
    var query = state.q.trim().toLowerCase(); // [M10b P1] 하이라이트 검색어(trim+대소문자무시)
    var frag = document.createDocumentFragment();
    var built = [];
    pageDocs.forEach(function (rows) {
      var d = buildDocCard(rows, query);
      frag.appendChild(d.card);
      built = built.concat(d.built);
    });
    resultsEl.appendChild(frag);
    renderPager(currentPage, totalPagesKnown, uncertain);
    schedulePrefetch(docs.length, moreMayExist); // [선로딩 c] 다음 청크 lookahead 1개
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
        }, 150);
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

  // [페이지네이션] offset(2번째 인자)이 있으면 &offset= 을 덧붙인다 — 생략(단일 인자
  // 호출, 기존 3단계 폴백 체인이 그대로 쓰는 형태)하면 offset=0 과 동일(기존 동작 불변).
  function buildEndpoint(fields, offset) {
    var cols = fields.join(",");
    var off = offset || 0;
    return (
      url.replace(/\/$/, "") +
      "/rest/v1/findings?select=" +
      encodeURIComponent(cols).replace(/%2C/g, ",") +
      "&order=published_date.desc,finding_id.asc&limit=" + PAGE_LIMIT +
      (off > 0 ? "&offset=" + off : "")
    );
  }

  // [페이지네이션] Prefer: count=exact — PostgREST 가 응답 Content-Range 헤더(예:
  // "0-999/1926")에 서버 exact total 을 실어 보낸다. parseServerTotal() 이 그 헤더를
  // 읽어 SERVER_TOTAL 을 채운다(헤더 미노출/파싱 실패 시 null 폴백 — 아래 §5 방어 참조).
  function fetchFindings(fields, offset) {
    return fetch(buildEndpoint(fields, offset), {
      headers: { apikey: key, Authorization: "Bearer " + key, Prefer: "count=exact" },
    });
  }

  // Content-Range: "0-999/1926" → 1926. 형식이 다르거나(예: "*/*") 헤더 자체가 없으면
  // (CORS 로 노출되지 않는 환경 포함) null 을 반환해 호출부가 조용히 폴백하게 한다.
  function parseServerTotal(resp) {
    var cr = resp && resp.headers ? resp.headers.get("content-range") : null;
    if (!cr) return null;
    var m = /\/(\d+)$/.exec(cr);
    if (!m) return null;
    var n = parseInt(m[1], 10);
    return isNaN(n) ? null : n;
  }

  // [페이지네이션] 페이지 이동으로 이어서 불러온 다음 청크를 ROWS 에 병합한다.
  // finding_id 기준 중복 제거 — 두 fetch 사이 새 번역이 공개(publish gate 통과)돼 정렬
  // 경계에서 행이 밀리더라도 같은 행이 두 번 들어오지 않게 방어한다.
  function mergeRows(newRows) {
    var seen = {};
    ROWS.forEach(function (r) {
      if (r.finding_id !== undefined && r.finding_id !== null) seen[r.finding_id] = true;
    });
    newRows.forEach(function (r) {
      var id = r.finding_id;
      if (id !== undefined && id !== null && seen[id]) return;
      ROWS.push(r);
      if (id !== undefined && id !== null) seen[id] = true;
    });
  }

  // ── [문서 단위 페이지네이션] 점진 로드 + 페이지 요구 기반 fetch ─────────────────────
  // 전량을 한 번에 받지 않는다 — 이미 로드된 페이지는 서버 왕복 0(ROWS 슬라이스만), 아직
  // 로드 안 된 페이지로 이동할 때만 필요한 만큼 청크(PAGE_LIMIT=1000)를 이어서 fetch 한다.

  // 서버 obs 청크가 소진됐는지 — SERVER_TOTAL(exact count) 이 있으면 정확히 비교하고,
  // 없으면(헤더 미노출 환경) "가장 최근 청크가 PAGE_LIMIT 미만이었다"는 휴리스틱으로
  // 판단한다(초기 로드 전=LAST_BATCH_SIZE null 이면 미확정 → false, 즉 "아직 더 있을
  // 수 있다"). fetchGaveUp(청크 fetch 실패)이면 무조건 소진 취급해 무한 재시도를 막는다.
  function isServerExhausted() {
    if (fetchGaveUp) return true;
    if (SERVER_TOTAL !== null) return ROWS.length >= SERVER_TOTAL;
    return LAST_BATCH_SIZE !== null && LAST_BATCH_SIZE < PAGE_LIMIT;
  }

  // [경계 문서 완결성] 서버가 아직 소진되지 않았다면, 가장 최근에 로드된 obs 행의
  // raw_signal_id 를 가진 문서는 다음 청크에 같은 문서의 obs 가 더 있을 수 있어
  // "미완결"로 간주한다 — 서버 fetch 가 published_date.desc, finding_id.asc 로 안정
  // 정렬되므로 같은 문서(raw_signal_id)의 obs 들은 서버 응답에서 서로 인접하게 도착한다는
  // 전제다. ensurePageReady() 가 이 키를 가진 문서가 목표 페이지 안에 있으면 한 청크
  // 더 당겨 재확인한다(클라이언트 정렬(state.sort)과 무관하게 항상 raw_signal_id 로만
  // 판정하므로 오래된순/업체명순 등 다른 정렬에서도 동일하게 안전하다).
  function incompleteDocKey() {
    if (isServerExhausted() || !ROWS.length) return null;
    var lastKey = ROWS[ROWS.length - 1].raw_signal_id;
    return lastKey === undefined || lastKey === null || lastKey === "" ? null : lastKey;
  }

  // pageNum 페이지를 안전하게 그릴 수 있을 만큼 ROWS 가 찼는지 확인하고, 부족하면 fetch
  // 를 이어서 한 뒤 done() 을 호출한다(충분하거나 서버가 소진됐으면 done() 을 동기
  // 호출 — 흔한 경우엔 네트워크 지연 없이 즉시 진행). 필터가 걸려 있어도 동일 로직을
  // 그대로 쓴다 — ROWS(원본)에 계속 청크를 채우고 그 위에서 매번 새로 필터링하므로,
  // 결과가 희소한 필터라면 여러 청크를 연달아 당길 수 있다(별도 최적화는 스코프 밖).
  function ensurePageReady(pageNum, done) {
    function attempt() {
      var matched = sortRows(ROWS.filter(matches));
      var docs = groupByDocument(matched);
      var neededDocs = pageNum * DOCS_PER_PAGE;
      if (docs.length >= neededDocs) {
        var pageSlice = docs.slice(neededDocs - DOCS_PER_PAGE, neededDocs);
        var badKey = incompleteDocKey();
        var unsafe = badKey !== null && pageSlice.some(function (rows) {
          return rows[0].raw_signal_id === badKey;
        });
        if (!unsafe) { done(); return; }
      }
      if (isServerExhausted()) { done(); return; }
      fetchNextChunkFor(attempt);
    }
    attempt();
  }

  // [중복 fetch 방어] 이미 진행 중인 청크 fetch 가 있으면 새 요청을 내지 않고 콜백만
  // 큐에 편승시킨다 — 여러 페이지 이동(연타)이 겹쳐도 실제 네트워크 요청은 항상 1개만
  // 진행되고, 그 결과가 도착하면 대기 중이던 콜백이 전부 한 번에 재개된다.
  function fetchNextChunkFor(cb) {
    if (!LOADED_FIELDS) { cb(); return; }
    pendingPageCallbacks.push(cb);
    if (isFetchingPage) return;
    isFetchingPage = true;
    fetchFindings(LOADED_FIELDS, ROWS.length)
      .then(function (r) {
        if (!r.ok) throw new Error("findings fetch more " + r.status);
        var total = parseServerTotal(r);
        if (total !== null) SERVER_TOTAL = total;
        return r.json();
      })
      .then(function (data) {
        if (!Array.isArray(data)) throw new Error("findings shape");
        LAST_BATCH_SIZE = data.length;
        mergeRows(data);
        buildFacetSkeleton(); // 새로 드러난 파셋 값 옵션 추가(중복 방지는 자체 보장)
      })
      .catch(function () {
        // 조용히 포기 — 이미 로드된 데이터만으로 계속 진행한다(무한 재시도 방지).
        fetchGaveUp = true;
      })
      .then(function () {
        isFetchingPage = false;
        var cbs = pendingPageCallbacks;
        pendingPageCallbacks = [];
        cbs.forEach(function (fn) { fn(); });
      });
  }

  // [선로딩 c] 현재 페이지 렌더 후 idle 시간에 다음 청크 1개만 미리 fetch한다(lookahead
  // 1 — 완역 시 수 MB 가 될 수 있는 전량 eager 로드는 절대 하지 않는다). 아직 여유가
  // 있으면(로드된 문서 기준 페이지 수가 현재 페이지보다 1 이상 남아있으면) 아무 것도
  // 하지 않는다 — PAGE_LIMIT=1000 obs 청크 하나가 보통 여러 페이지 분량을 이미 커버하므로
  // 실제로는 로드된 데이터의 마지막·마지막 직전 페이지에 있을 때만 net 요청이 나간다.
  // 렌더를 트리거하지 않는 순수 선로딩(fetchNextChunkFor 의 콜백은 no-op) — 다음 실제
  // 페이지 이동(goToPage)이 이 데이터를 즉시 재사용한다.
  function schedulePrefetch(loadedDocsCount, uncertainLoad) {
    if (!uncertainLoad || isFetchingPage) return;
    var loadedPages = Math.max(1, Math.ceil(loadedDocsCount / DOCS_PER_PAGE));
    if (currentPage < loadedPages - 1) return; // 아직 여유 있음 — lookahead 불필요
    var idle = (typeof requestIdleCallback === "function")
      ? requestIdleCallback
      : function (fn) { return setTimeout(fn, 200); };
    idle(function () {
      if (isFetchingPage || isServerExhausted()) return;
      fetchNextChunkFor(function () {}); // 선로딩만 — 렌더 트리거 없음(다음 이동 시 즉시 반영)
    });
  }

  // [페이지 이동] navToken 세대 카운터로 연타(빠른 재클릭)를 방어한다 — 오래된 이동의
  // 완료 콜백은 currentPage/render() 를 건드리지 않고 조용히 버려지므로, 항상 "가장
  // 최근 클릭"만 화면에 반영된다.
  function goToPage(n) {
    var target = Math.max(1, Math.floor(n) || 1);
    navToken += 1;
    var myToken = navToken;
    var doScroll = pendingScrollAfterNav;
    pendingScrollAfterNav = false;
    setPagerLoading(true);
    ensurePageReady(target, function () {
      if (myToken !== navToken) return; // 더 최근 이동에 의해 취소됨
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

  function hidePager() {
    if (pagerTopEl) pagerTopEl.hidden = true;
    if (pagerBottomEl) pagerBottomEl.hidden = true;
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
  // 페이지의 anon SELECT(공개 게이트 통과분만)와 이 RPC 의 전량 집계 사이 간극을 사용자에게
  // 정직하게 알리는 것이 목적이다 — 실패해도 독립적으로 조용히 숨김 유지(아래 .catch()).
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
        // [정확 총수 M1a] 페이지네이션(render())·대시보드(renderDash())가 공유하는 exact
        // 총수 — 이 fetch 는 메인 검색 fetch 와 독립적이라, 성공하면 페이지 이동/렌더
        // 시점과 무관하게 항상 최신값을 들고 있다(실패하면 null 유지 — 기존 로드 기준 폴백).
        if (hasDocs) SERVER_DOC_TOTAL = totals.documents;
        if (typeof totals.findings === "number" && !isNaN(totals.findings)) {
          SERVER_FINDINGS_TOTAL = totals.findings;
        }
        // [대시보드 실총수 M3] by_agency_category(agency×category_code 교차 집계)를
        // agency 기준으로만 합산해 무필터 대시보드 스탯의 FDA/MFDS 소스별 분해를
        // 정확화한다 — findings_stats 에 agency 단독 집계 키가 없어 이 교차표에서
        // 파생한다(RPC 실패/010 미적용이면 null 유지 — renderDash() 가 로드 기준으로 폴백).
        if (Array.isArray(data.by_agency_category)) {
          var agencySums = {};
          data.by_agency_category.forEach(function (row) {
            if (!row || !row.agency) return;
            agencySums[row.agency] = (agencySums[row.agency] || 0) + (row.cnt || 0);
          });
          SERVER_AGENCY_TOTALS = agencySums;
        }
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

  // [페이지네이션] 3단계 폴백 체인 중 실제로 성공한 Response·필드셋을 기억해뒀다가
  // (아래 .then 에서) SERVER_TOTAL/LOADED_FIELDS 를 채운다 — 어느 단계가 성공했든
  // 동일하게 처리한다(체인 흐름 자체는 §3 fallback 계약과 무변경).
  var loadedResp = null;
  var loadedFieldsUsed = null;
  showState("loading");
  fetchCoverageNote();
  // [PR-0 딥링크] finding_id 파라미터가 있으면 목록 fetch 와 병렬로 단건+문서 조회를
  // 시작한다 — 어느 쪽이 먼저 끝나든 maybeFinishInit() 이 둘 다 끝난 뒤 한 번만 확정
  // 렌더한다(깜빡임 없음). 파라미터 자체가 없으면 deepLinkPending=false 로 시작해 아래
  // 로직 전체가 기존 동작과 완전히 동일하게(no-op) 흘러간다(§7 회귀 0).
  var requestedFindingId = getDeepLinkParam();
  if (requestedFindingId) {
    deepLinkParam = requestedFindingId;
    deepLinkPending = true;
    resolveDeepLink(requestedFindingId);
  }
  fetchFindings(FIELDS)
    .then(function (r) {
      if (r.ok) {
        loadedResp = r;
        loadedFieldsUsed = FIELDS;
        return r.json();
      }
      // 013(firm_key) 미적용 라이브 DB 대비 1차 재시도(firm_key 만 제외).
      return fetchFindings(FIELDS_NO_FIRM_KEY).then(function (r2) {
        if (r2.ok) {
          loadedResp = r2;
          loadedFieldsUsed = FIELDS_NO_FIRM_KEY;
          return r2.json();
        }
        // 013·005(finding_text_ko/translation_method) 둘 다 미적용 라이브 DB 대비
        // 최종 legacy FIELDS 재시도.
        return fetchFindings(LEGACY_FIELDS).then(function (r3) {
          if (!r3.ok) throw new Error("findings fetch " + r3.status);
          loadedResp = r3;
          loadedFieldsUsed = LEGACY_FIELDS;
          return r3.json();
        });
      });
    })
    .then(function (data) {
      if (!Array.isArray(data)) throw new Error("findings shape");
      ROWS = data;
      LOADED_FIELDS = loadedFieldsUsed;
      LAST_BATCH_SIZE = data.length;
      var total = parseServerTotal(loadedResp);
      if (total !== null) SERVER_TOTAL = total;
      buildFacetSkeleton();
      // [FIND-1 M10c] URL→state 복원은 facet 값 목록(collectFacetValues)이 필요해
      // buildFacetSkeleton() 다음, 첫 render() 이전에 수행한다.
      readStateFromUrl();
      syncControlsFromState();
      wire();
      rowsReady = true;
      // [PR-0 딥링크] goToPage(초기 페이지) 는 maybeFinishInit() 이 상황에 맞게 호출한다
      // (found=문서 카드 단독 렌더, notfound/일반=기존과 동일한 페이지 목록 렌더 — ?page=
      // 초기 1회 복원은 그 안에서 readPageFromUrl() 로 그대로 수행된다).
      maybeFinishInit();
    })
    .catch(function () {
      // [PR-0 딥링크] 목록 fetch 가 실패해도 이미 확정된 딥링크 단건 렌더는 덮어쓰지 않는다.
      if (deepLinkStatus !== "found") showState("error");
    });
})();
