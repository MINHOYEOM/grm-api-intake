/* GRM 지적사항 검색 (FIND-1 M3c, 국문 우선표시+카테고리 라벨 M6d, 대시보드 밴드 M7,
 * 카드 UX 오버홀 M10b, 탐색 툴바 오버홀 M10c) — 정적·클라이언트사이드, 순수 fetch(PostgREST
 * 직접 호출).
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
  var CATEGORY_LABELS = {
    data_integrity: { ko: "데이터 완전성", en: "Data integrity" },
    documentation_records: { ko: "문서화/기록관리", en: "Documentation and records" },
    aseptic_sterility_assurance: { ko: "무균보증/무균공정", en: "Aseptic processing and sterility assurance" },
    environmental_monitoring: { ko: "환경모니터링", en: "Environmental monitoring" },
    cleaning_validation: { ko: "세척밸리데이션", en: "Cleaning validation" },
    deviation_capa: { ko: "일탈/CAPA/조사", en: "Deviation, CAPA, and investigation" },
    quality_unit_oversight: { ko: "품질부서 관리감독", en: "Quality unit oversight" },
    qc_lab_controls: { ko: "시험실/품질관리", en: "Laboratory and QC controls" },
    process_validation: { ko: "공정밸리데이션", en: "Process validation" },
    equipment_facility: { ko: "설비/시설", en: "Equipment and facility" },
    material_supplier_control: { ko: "원자재/공급업체 관리", en: "Material and supplier control" },
    contamination_control: { ko: "오염/교차오염 관리", en: "Contamination control" },
    validation_qualification: { ko: "밸리데이션/적격성평가", en: "Validation and qualification" },
    complaint_recall: { ko: "불만/회수", en: "Complaint and recall handling" },
    stability_storage: { ko: "안정성/보관", en: "Stability and storage" },
    computer_system_validation: { ko: "컴퓨터화시스템", en: "Computer system validation" },
    labeling_packaging: { ko: "표시/포장", en: "Labeling and packaging" },
    regulatory_reporting: { ko: "규제보고/변경관리", en: "Regulatory reporting and change control" },
    training_personnel: { ko: "교육/작업자", en: "Training and personnel" },
    other_quality_system: { ko: "기타 품질시스템", en: "Other quality system" },
  };

  // 신규(M6d) 필드 포함 전체 목록. 라이브 DB 에 005(finding_text_ko/translation_method)가
  // 아직 적용되지 않은 경우 PostgREST 가 알 수 없는 컬럼에 400 을 반환하므로, 그 경우에만
  // LEGACY_FIELDS(신규 2컬럼 제외)로 1회 재시도한다 — 배포 순서와 무관하게 페이지가 절대
  // 깨지지 않도록 하는 폴백이다.
  var FIELDS = [
    "finding_id", "source", "agency", "document_id", "published_date",
    "firm_name", "category_code", "category_label_ko", "finding_text",
    "finding_text_ko", "translation_method",
    "finding_language", "evidence_level", "evidence_url", "cfr_refs",
    "mfds_refs", "review_status", "confidence",
  ];
  var LEGACY_FIELDS = FIELDS.filter(function (f) {
    return f !== "finding_text_ko" && f !== "translation_method";
  });

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

  var qInput = document.getElementById("fnd-q");
  var sortSel = document.getElementById("fnd-sort");
  var filtersEl = document.getElementById("fnd-filters");
  var filtersToggleBtn = document.getElementById("fnd-filters-toggle");
  var filtersBadgeEl = document.getElementById("fnd-filters-badge");
  var activeEl = document.getElementById("fnd-active"); // [M15] 적용 필터 칩 행
  var dashToggleBtn = document.getElementById("fnd-dash-toggle");
  var dashGridEl = document.getElementById("fnd-dash-grid");

  // [FIND-1 M7] 대시보드 밴드 — 필터 컨트롤 위 콤팩트 조망(스탯/카테고리/월별/업체).
  // 셸(findings.html)은 빈 컨테이너+hidden 만 가진다 — 다섯 엘리먼트가 모두 있을 때만
  // 활성화하고(hasDash), 없으면 검색 자체는 기존과 동일하게 계속 동작한다(하위호환).
  var dashEl = document.getElementById("fnd-dash");
  var dashStatsEl = document.getElementById("fnd-dash-stats");
  var dashCatEl = document.getElementById("fnd-dash-cat");
  var dashMonthEl = document.getElementById("fnd-dash-month");
  var dashFirmEl = document.getElementById("fnd-dash-firm");
  var hasDash = !!(dashEl && dashStatsEl && dashCatEl && dashMonthEl && dashFirmEl);

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
  function buildFacetSkeleton() {
    SELECT_FACETS.forEach(function (def) {
      var selId = def[0], key2 = def[1];
      var sel = document.getElementById(selId);
      if (!sel) return;
      var values = collectFacetValues(key2);
      if (key2 === "category_code") values = categoryCodesInTaxonomyOrder(values);
      values.forEach(function (v) {
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
    var sel = document.getElementById("fnd-f-category");
    state.category_code = state.category_code === code ? "" : code;
    if (sel) sel.value = state.category_code;
    render();
  }

  function toggleMonthFilter(month) {
    var sel = document.getElementById("fnd-f-month");
    state.month = state.month === month ? "" : month;
    if (sel) sel.value = state.month;
    render();
  }

  function toggleFirmFilter(name) {
    state.q = state.q === name ? "" : name;
    if (qInput) qInput.value = state.q;
    render();
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
    stats.agencies.forEach(function (a) {
      dashStatsEl.appendChild(buildStatBlock(String(a.count), a.agency, false));
    });
    if (stats.needsReview > 0) {
      dashStatsEl.appendChild(buildStatBlock(String(stats.needsReview), "검토 필요", true));
    }
  }

  function renderDashCategories(stats) {
    dashCatEl.innerHTML = "";
    var top = stats.categories.slice(0, 6);
    var restCount = stats.categories.slice(6).reduce(function (s, c) { return s + c.count; }, 0);
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
    row.appendChild(el("span", "fnd-dash-cat-label", label));
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
      makeClickableRow(row, f.name + " 검색: " + f.count + "건", function () {
        toggleFirmFilter(f.name);
      });
      row.appendChild(el("span", "fnd-dash-firm-name", f.name));
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

    if (row.firm_name) card.appendChild(elHL("h3", "fnd-firm", row.firm_name, query));
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
    state[key] = "";
    syncControlsFromState();
    render();
  }

  function clearAllFilters() {
    state = {
      q: "", agency: "", category_code: "", source: "", evidence_level: "",
      review_status: "", month: "", sort: "date_desc",
    };
    syncControlsFromState();
    render();
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

  function render() {
    var matched = sortRows(ROWS.filter(matches));
    renderDash(matched); // [FIND-1 M7] 필터 결과 기준 대시보드 재계산(데이터 없으면 hidden)
    refreshFacetUI(); // [M15] 셀렉트 건수 갱신(표준 파세팅)
    renderActiveChips(); // [M15] 적용 필터 칩 행 재계산
    updateFiltersToggleBadge();
    syncStateToUrl();
    countEl.innerHTML = "";
    var b = document.createElement("b");
    b.textContent = String(matched.length);
    countEl.appendChild(document.createTextNode("총 "));
    countEl.appendChild(b);
    countEl.appendChild(document.createTextNode("건"));

    resultsEl.innerHTML = "";
    if (!matched.length) {
      showState("empty");
      return;
    }
    showState("none");
    var query = state.q.trim().toLowerCase(); // [M10b P1] 하이라이트 검색어(trim+대소문자무시)
    var frag = document.createDocumentFragment();
    var built = [];
    matched.forEach(function (row) {
      var b2 = buildCard(row, query);
      frag.appendChild(b2.card);
      built.push(b2);
    });
    resultsEl.appendChild(frag);
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
    // [M15] 6개 셀렉트(소스·증거등급·검토상태·카테고리·발행월·정렬 중 정렬 제외 5개)가
    // 전부 SELECT_FACETS 단일 경로로 change 배선된다(칩 그룹 배선 없음).
    SELECT_FACETS.forEach(function (def) {
      var sel = document.getElementById(def[0]);
      if (!sel) return;
      sel.addEventListener("change", function () {
        state[def[1]] = sel.value;
        render();
      });
    });
    if (sortSel) {
      sortSel.addEventListener("change", function () {
        state.sort = sortSel.value;
        render();
      });
    }
    if (qInput) {
      qInput.addEventListener("input", function () {
        state.q = qInput.value;
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(render, 150);
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

  function buildEndpoint(fields) {
    var cols = fields.join(",");
    return (
      url.replace(/\/$/, "") +
      "/rest/v1/findings?select=" +
      encodeURIComponent(cols).replace(/%2C/g, ",") +
      "&order=published_date.desc,finding_id.asc&limit=1000"
    );
  }

  function fetchFindings(fields) {
    return fetch(buildEndpoint(fields), {
      headers: { apikey: key, Authorization: "Bearer " + key },
    });
  }

  showState("loading");
  fetchFindings(FIELDS)
    .then(function (r) {
      if (r.ok) return r.json();
      // 005(finding_text_ko/translation_method) 미적용 라이브 DB 대비 legacy FIELDS 재시도.
      return fetchFindings(LEGACY_FIELDS).then(function (r2) {
        if (!r2.ok) throw new Error("findings fetch " + r2.status);
        return r2.json();
      });
    })
    .then(function (data) {
      if (!Array.isArray(data)) throw new Error("findings shape");
      ROWS = data;
      buildFacetSkeleton();
      // [FIND-1 M10c] URL→state 복원은 facet 값 목록(collectFacetValues)이 필요해
      // buildFacetSkeleton() 다음, 첫 render() 이전에 수행한다.
      readStateFromUrl();
      syncControlsFromState();
      wire();
      render();
    })
    .catch(function () {
      showState("error");
    });
})();
