/* GRM 규제 지적사항 트렌드 대시보드 (FIND-1 F3b) — 정적·클라이언트사이드, 순수 fetch
 * (PostgREST RPC 직접 호출, POST). findings.js 와 자매 페이지지만 데이터원이 다르다 —
 * findings.js 는 행 단위 SELECT(공개 게이트 006 통과분만), 이 페이지는 사전계산 집계
 * RPC 2종(007_findings_stats_rpc.sql)을 쓴다 — 공개 게이트를 우회해 전량(미번역분 포함)
 * 카운트를 반환하지만, 원문/URL 텍스트 필드는 어떤 경로로도 내려주지 않는다(안전 계약,
 * 마이그레이션 파일 원문 참조). 그래서 이 페이지는 카운트·서지 메타(카테고리/월/소스/
 * 증거등급/업체명)만 다루고 지적 내용 원문은 절대 렌더하지 않는다.
 *
 * cfg(url/key/root) 는 템플릿의 #grm-findings-cfg data-속성(env-param)에서 읽는다.
 * url/key 중 하나라도 없으면 "트렌드 서비스 준비 중입니다." 안내로 조용히 종료한다(오류
 * 아님 — 정적 페이지 골든 결정론, env 값과 무관하게 trends.html 자체 출력 byte 는 항상
 * 동일). data-root 는 findings 검색 페이지(카테고리 바 클릭 시 이동)로의 상대경로 계산에만
 * 쓴다(reactions.js 의 data-root 관례와 동형).
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 대입은 컨테이너 비우기 ""
 * 뿐 — findings.js 와 동일 XSS 계약). 업체명(firm_name)·소스(source)·카테고리 라벨은
 * 전부 textContent 로만 삽입한다.
 *
 * [동기화 규칙] CATEGORY_LABELS 는 findings.js 의 동명 상수·grm_findings.FINDING_TAXONOMY
 * 20개 code/label_ko/label_en 과 완전히 일치해야 한다(web/tests/test_render.py 가
 * WebTrendsRenderTest.test_category_labels_sync_with_taxonomy 로 대조). findings.js 를
 * import 할 수 없는 독립 정적 자산이라 값을 그대로 복제해 두되, 드리프트는 테스트가 잡는다.
 */
(function () {
  "use strict";

  var cfg = document.getElementById("grm-findings-cfg");
  var loadingEl = document.getElementById("tr-loading");
  var errorEl = document.getElementById("tr-error");
  var contentEl = document.getElementById("tr-content");
  var statsEl = document.getElementById("tr-stats");
  var headlineEl = document.getElementById("tr-headline");
  var catEl = document.getElementById("tr-cat");
  var yearEl = document.getElementById("tr-year");
  var firmsEl = document.getElementById("tr-firms");
  var firmDetailEl = document.getElementById("tr-firm-detail");
  var evidenceEl = document.getElementById("tr-evidence");
  var sourceEl = document.getElementById("tr-source");
  if (!cfg || !loadingEl || !errorEl || !contentEl || !statsEl || !headlineEl ||
      !catEl || !yearEl || !firmsEl || !firmDetailEl || !evidenceEl || !sourceEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();
  var root = (cfg.getAttribute("data-root") || "").trim();

  // grm_findings.FINDING_TAXONOMY verbatim(code -> {ko, en}) — findings.js CATEGORY_LABELS
  // 와 동일 복제본(동기화 테스트로 드리프트 차단, 파일 상단 계약 참조).
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

  var EVIDENCE_ORDER = ["A", "B", "C"];

  // 업체 상세 패널이 열려 있는지(?firm=)·직전 렌더의 top_firms(업체 랭킹 재렌더용)를
  // 여기 담는다 — findings.js 의 단일 state 객체 관례와 동형(별도 저장소 난립 금지).
  var state = { openFirm: "", lastFirms: [] };

  // ── 공용 헬퍼 ────────────────────────────────────────────────────────────
  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") e.textContent = text;
    return e;
  }

  function fmtNum(n) {
    var s = String(Math.round(Number(n) || 0));
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // 클릭 가능한 div 행(role=button+tabindex+Enter/Space) — findings.js 의 동명 헬퍼와
  // 동일 계약(별도 파일이라 재사용 불가, 계약만 복제).
  function makeClickableRow(node, ariaLabel, onActivate) {
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

  // 카테고리 바 클릭 → 검색 페이지 필터 링크. findings.js 의 URL_KEYS.category_code="cat"
  // 계약을 그대로 따른다(파라미터명이 다르면 findings 페이지에서 필터가 걸리지 않는다).
  function findingsHref(paramKey, value) {
    return root + "findings/index.html?" + paramKey + "=" + encodeURIComponent(value);
  }

  function aggregateYears(byMonth) {
    var sums = {};
    (byMonth || []).forEach(function (r) {
      var m = r.month || "";
      if (m.length < 4) return;
      var y = m.slice(0, 4);
      sums[y] = (sums[y] || 0) + (r.cnt || 0);
    });
    return Object.keys(sums).sort().map(function (y) { return { year: y, cnt: sums[y] }; });
  }

  function aggregateCategories(byAgencyCategory) {
    var totals = {}, byAgency = {};
    (byAgencyCategory || []).forEach(function (r) {
      if (!r.category_code) return;
      totals[r.category_code] = (totals[r.category_code] || 0) + (r.cnt || 0);
      byAgency[r.category_code] = byAgency[r.category_code] || {};
      byAgency[r.category_code][r.agency || ""] =
        (byAgency[r.category_code][r.agency || ""] || 0) + (r.cnt || 0);
    });
    return Object.keys(totals).map(function (code) {
      var cat = CATEGORY_LABELS[code];
      var agencies = byAgency[code] || {};
      var agencyTitle = Object.keys(agencies)
        .filter(Boolean)
        .sort(function (a, b) { return agencies[b] - agencies[a]; })
        .map(function (a) { return a + " " + fmtNum(agencies[a]); })
        .join(" · ");
      return { code: code, ko: cat ? cat.ko : code, cnt: totals[code], agencyTitle: agencyTitle };
    }).sort(function (a, b) { return b.cnt - a.cnt || a.code.localeCompare(b.code); });
  }

  // ── 한눈 요약(에디토리얼 헤드라인) — 결정론 생성, 억지 통계 금지 ────────────────
  // 규칙: 문장1=최다 카테고리(항상). 문장2=최근12개월 vs 그 이전12개월 증감(24개월
  // 이상 커버리지 + 두 구간 모두 0건이 아닐 때만) — 조건 미충족이면 최다 업체 문장으로
  // 대체한다(억지로 %를 만들지 않는다).
  function shiftMonth(ym, delta) {
    var y = parseInt(ym.slice(0, 4), 10), m = parseInt(ym.slice(5, 7), 10);
    var total = y * 12 + (m - 1) + delta;
    var ny = Math.floor(total / 12), nm = (total % 12) + 1;
    return ny + "-" + (nm < 10 ? "0" + nm : "" + nm);
  }

  function computeYoy(byMonth) {
    var sums = {};
    (byMonth || []).forEach(function (r) { sums[r.month] = (sums[r.month] || 0) + (r.cnt || 0); });
    var months = Object.keys(sums).sort();
    if (!months.length) return null;
    var last = months[months.length - 1];
    var recentStart = shiftMonth(last, -11);
    var prevEnd = shiftMonth(recentStart, -1);
    var prevStart = shiftMonth(recentStart, -12);
    if (months[0] > prevStart) return null; // 24개월 미만 커버리지 → 계산 보류
    var recentSum = 0, prevSum = 0;
    months.forEach(function (m) {
      if (m >= recentStart && m <= last) recentSum += sums[m];
      else if (m >= prevStart && m <= prevEnd) prevSum += sums[m];
    });
    if (prevSum <= 0 || recentSum <= 0) return null;
    return { pct: Math.round(((recentSum - prevSum) / prevSum) * 100) };
  }

  function buildHeadline(data) {
    var lines = [];
    var cats = aggregateCategories(data.by_agency_category || []);
    if (cats.length) {
      lines.push("가장 많이 지적된 영역은 " + cats[0].ko + "(" + fmtNum(cats[0].cnt) + "건)입니다.");
    }
    var yoy = computeYoy(data.by_month || []);
    if (yoy) {
      var dir = yoy.pct >= 0 ? "증가" : "감소";
      lines.push("최근 12개월 지적은 전년 동기 대비 " + Math.abs(yoy.pct) + "% " + dir + "했습니다.");
    } else if ((data.top_firms || []).length) {
      var f = data.top_firms[0];
      lines.push("지적 건수가 가장 많은 업체는 " + f.firm_name + "(" + fmtNum(f.cnt) + "건)입니다.");
    }
    return lines;
  }

  // ── 스탯 스트립 ──────────────────────────────────────────────────────────
  function buildStat(num, label) {
    var block = el("div", "tr-stat");
    block.appendChild(el("span", "tr-stat-num", num));
    block.appendChild(el("span", "tr-stat-lbl", label));
    return block;
  }

  function renderStats(totals) {
    statsEl.innerHTML = "";
    statsEl.appendChild(buildStat(fmtNum(totals.findings), "총 지적사항"));
    statsEl.appendChild(buildStat(fmtNum(totals.firms), "업체"));
    statsEl.appendChild(buildStat(fmtNum(totals.raw_signals), "원문서"));
    var pub = buildStat(fmtNum(totals.public_findings), "국문 열람 가능");
    pub.appendChild(el("span", "tr-stat-note", "나머지는 집계에만 반영(원문 영문)"));
    statsEl.appendChild(pub);
  }

  function renderHeadline(lines) {
    headlineEl.textContent = lines.join(" ");
  }

  // ── 카테고리 순위(메인 시각) — 상위 10, 순위별 opacity 100→40% 농도 단계 ─────────
  function buildCatRow(entry, idx, maxCnt) {
    var a = document.createElement("a");
    a.className = "tr-cat-row";
    a.href = findingsHref("cat", entry.code);
    if (entry.agencyTitle) a.title = entry.agencyTitle;
    a.appendChild(el("span", "tr-cat-rank", String(idx + 1)));
    a.appendChild(el("span", "tr-cat-label", entry.ko));
    var track = document.createElement("div");
    track.className = "tr-cat-track";
    var bar = document.createElement("div");
    bar.className = "tr-cat-bar";
    var ratio = maxCnt > 0 ? entry.cnt / maxCnt : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    bar.style.opacity = String(Math.max(0.4, 1 - idx * (0.6 / 9)));
    track.appendChild(bar);
    a.appendChild(track);
    a.appendChild(el("span", "tr-cat-count", fmtNum(entry.cnt) + "건"));
    return a;
  }

  function renderCategoryRanking(byAgencyCategory) {
    catEl.innerHTML = "";
    var cats = aggregateCategories(byAgencyCategory).slice(0, 10);
    if (!cats.length) {
      catEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = cats[0].cnt || 1;
    cats.forEach(function (c, i) { catEl.appendChild(buildCatRow(c, i, maxCnt)); });
  }

  // ── 연도별 추이 ──────────────────────────────────────────────────────────
  function renderYearTrend(byMonth) {
    yearEl.innerHTML = "";
    var years = aggregateYears(byMonth);
    if (!years.length) {
      yearEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = years.reduce(function (m, y) { return Math.max(m, y.cnt); }, 0) || 1;
    var wrap = document.createElement("div");
    wrap.className = "tr-year-bars";
    years.forEach(function (y) {
      var col = document.createElement("div");
      col.className = "tr-year-col";
      var barwrap = document.createElement("div");
      barwrap.className = "tr-year-barwrap";
      var bar = document.createElement("div");
      bar.className = "tr-year-bar";
      bar.style.height = Math.max(4, Math.round((y.cnt / maxCnt) * 100)) + "%";
      barwrap.appendChild(bar);
      col.appendChild(barwrap);
      col.appendChild(el("span", "tr-year-lbl", y.year));
      col.appendChild(el("span", "tr-year-count", fmtNum(y.cnt)));
      wrap.appendChild(col);
    });
    yearEl.appendChild(wrap);
  }

  // ── 업체 랭킹 Top 30 + 상세 패널 ─────────────────────────────────────────
  function buildFirmRow(f, idx, maxCnt) {
    var row = document.createElement("div");
    row.className = "tr-firm-row";
    if (state.openFirm === f.firm_name) row.classList.add("on");
    makeClickableRow(row, f.firm_name + " 상세 보기: " + f.cnt + "건", function () {
      if (state.openFirm === f.firm_name) closeFirm();
      else openFirm(f.firm_name);
    });
    row.appendChild(el("span", "tr-firm-rank", String(idx + 1)));
    row.appendChild(el("span", "tr-firm-name", f.firm_name));
    var track = document.createElement("div");
    track.className = "tr-firm-bar";
    var fill = document.createElement("div");
    fill.className = "tr-firm-bar-fill";
    var ratio = maxCnt > 0 ? f.cnt / maxCnt : 0;
    fill.style.width = Math.max(2, Math.round(ratio * 100)) + "%";
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("span", "tr-firm-count", fmtNum(f.cnt)));
    return row;
  }

  function renderFirmRanking(topFirms) {
    firmsEl.innerHTML = "";
    if (!topFirms.length) {
      firmsEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = topFirms[0].cnt || 1;
    topFirms.forEach(function (f, i) { firmsEl.appendChild(buildFirmRow(f, i, maxCnt)); });
  }

  function buildFirmDetailCatCol(byCategory) {
    var col = document.createElement("div");
    col.appendChild(el("h4", "tr-fd-h", "카테고리 분포"));
    var rows = byCategory.map(function (r) {
      var cat = CATEGORY_LABELS[r.category_code];
      return { ko: cat ? cat.ko : r.category_code, cnt: r.cnt || 0 };
    }).sort(function (a, b) { return b.cnt - a.cnt; }).slice(0, 6);
    if (!rows.length) {
      col.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return col;
    }
    var maxCnt = rows[0].cnt || 1;
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "tr-fd-row";
      row.appendChild(el("span", "tr-fd-label", r.ko));
      var track = document.createElement("div");
      track.className = "tr-fd-track";
      var bar = document.createElement("div");
      bar.className = "tr-fd-bar";
      bar.style.transform = "scaleX(" + Math.max(0.02, r.cnt / maxCnt) + ")";
      track.appendChild(bar);
      row.appendChild(track);
      row.appendChild(el("span", "tr-fd-count", fmtNum(r.cnt)));
      col.appendChild(row);
    });
    return col;
  }

  function buildFirmDetailYearCol(byMonth) {
    var col = document.createElement("div");
    col.appendChild(el("h4", "tr-fd-h", "연도별 추이"));
    var years = aggregateYears(byMonth);
    if (!years.length) {
      col.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return col;
    }
    var maxCnt = years.reduce(function (m, y) { return Math.max(m, y.cnt); }, 0) || 1;
    var wrap = document.createElement("div");
    wrap.className = "tr-fd-year-bars";
    years.forEach(function (y) {
      var c = document.createElement("div");
      c.className = "tr-fd-year-col";
      var barwrap = document.createElement("div");
      barwrap.className = "tr-fd-year-barwrap";
      var bar = document.createElement("div");
      bar.className = "tr-fd-year-bar";
      bar.style.height = Math.max(4, Math.round((y.cnt / maxCnt) * 100)) + "%";
      barwrap.appendChild(bar);
      c.appendChild(barwrap);
      c.appendChild(el("span", "tr-fd-year-lbl", y.year.slice(2)));
      wrap.appendChild(c);
    });
    col.appendChild(wrap);
    return col;
  }

  function buildFirmDetailSourceRow(bySource) {
    var wrap = document.createElement("div");
    wrap.className = "tr-fd-src";
    wrap.appendChild(el("h4", "tr-fd-h", "소스 구성"));
    var sorted = bySource.slice().sort(function (a, b) { return (b.cnt || 0) - (a.cnt || 0); });
    if (!sorted.length) {
      wrap.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return wrap;
    }
    var list = document.createElement("p");
    list.className = "tr-fd-src-list";
    sorted.forEach(function (s, i) {
      if (i > 0) list.appendChild(document.createTextNode(" · "));
      list.appendChild(document.createTextNode(s.source + " " + fmtNum(s.cnt) + "건"));
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderFirmDetail(data) {
    firmDetailEl.innerHTML = "";
    var head = document.createElement("div");
    head.className = "tr-firm-detail-head";
    var idbox = document.createElement("div");
    idbox.appendChild(el("h3", "tr-firm-detail-name", data.firm_name || ""));
    var period = (data.first_seen || "?") + " ~ " + (data.last_seen || "?");
    idbox.appendChild(el("p", "tr-firm-detail-meta",
      period + " · 총 " + fmtNum((data.totals || {}).findings || 0) + "건"));
    head.appendChild(idbox);
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "tr-firm-detail-close";
    closeBtn.setAttribute("aria-label", "업체 상세 닫기");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", closeFirm);
    head.appendChild(closeBtn);
    firmDetailEl.appendChild(head);

    var grid = document.createElement("div");
    grid.className = "tr-firm-detail-grid";
    grid.appendChild(buildFirmDetailCatCol(data.by_category || []));
    grid.appendChild(buildFirmDetailYearCol(data.by_month || []));
    firmDetailEl.appendChild(grid);
    firmDetailEl.appendChild(buildFirmDetailSourceRow(data.by_source || []));
  }

  function renderFirmDetailLoading() {
    firmDetailEl.innerHTML = "";
    firmDetailEl.appendChild(el("p", "tr-empty", "불러오는 중…"));
  }

  function renderFirmDetailError() {
    firmDetailEl.innerHTML = "";
    firmDetailEl.appendChild(el("p", "tr-empty", "업체 통계를 불러오지 못했습니다."));
  }

  // ?firm= 은 findings_firm_stats(p_firm) 의 exact-match 계약을 따른다(top_firms.firm_name
  // 값 그대로만 넘긴다) — URLSearchParams 가 인코딩/디코딩을 전담(pushState 는 쓰지 않는다,
  // 뒤로가기 히스토리 오염 방지, findings.js 와 동일 원칙).
  function syncFirmUrl(name) {
    if (typeof history === "undefined" || !history.replaceState || typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    if (name) params.set("firm", name); else params.delete("firm");
    var qs = params.toString();
    var newUrl = location.pathname + (qs ? "?" + qs : "") + location.hash;
    history.replaceState(null, "", newUrl);
  }

  function openFirm(name) {
    state.openFirm = name;
    renderFirmRanking(state.lastFirms);
    syncFirmUrl(name);
    firmDetailEl.hidden = false;
    renderFirmDetailLoading();
    fetchFirmStats(name).then(function (data) {
      renderFirmDetail(data);
      if (typeof firmDetailEl.scrollIntoView === "function") {
        firmDetailEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }).catch(function () {
      renderFirmDetailError();
    });
  }

  function closeFirm() {
    state.openFirm = "";
    renderFirmRanking(state.lastFirms);
    syncFirmUrl("");
    firmDetailEl.hidden = true;
    firmDetailEl.innerHTML = "";
  }

  function maybeOpenFirmFromUrl() {
    if (typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    var f = params.get("firm");
    if (f) openFirm(f);
  }

  // ── 하단: 증거등급 구성(A/B/C 스택 바) · 소스 구성 ───────────────────────────
  function renderEvidence(byEvidence) {
    evidenceEl.innerHTML = "";
    var map = {};
    (byEvidence || []).forEach(function (r) { map[r.evidence_level] = r.cnt || 0; });
    var total = EVIDENCE_ORDER.reduce(function (s, k) { return s + (map[k] || 0); }, 0);
    if (!total) {
      evidenceEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var bar = document.createElement("div");
    bar.className = "tr-evidence-bar";
    EVIDENCE_ORDER.forEach(function (k, i) {
      var cnt = map[k] || 0;
      if (!cnt) return;
      var seg = document.createElement("div");
      seg.className = "tr-evidence-seg";
      seg.style.opacity = String(Math.max(0.35, 1 - i * 0.32));
      seg.style.width = ((cnt / total) * 100) + "%";
      seg.title = "Evidence " + k + " " + fmtNum(cnt) + "건";
      bar.appendChild(seg);
    });
    evidenceEl.appendChild(bar);
    var legend = document.createElement("div");
    legend.className = "tr-evidence-legend";
    EVIDENCE_ORDER.forEach(function (k, i) {
      var cnt = map[k] || 0;
      var item = document.createElement("span");
      item.className = "tr-evidence-item";
      var sw = document.createElement("i");
      sw.className = "tr-evidence-swatch";
      sw.setAttribute("aria-hidden", "true");
      sw.style.opacity = String(Math.max(0.35, 1 - i * 0.32));
      item.appendChild(sw);
      item.appendChild(document.createTextNode("Evidence " + k + " " + fmtNum(cnt) + "건"));
      legend.appendChild(item);
    });
    evidenceEl.appendChild(legend);
  }

  function renderSource(bySource) {
    sourceEl.innerHTML = "";
    var sorted = (bySource || []).slice().sort(function (a, b) { return (b.cnt || 0) - (a.cnt || 0); });
    if (!sorted.length) {
      sourceEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = sorted[0].cnt || 1;
    sorted.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "tr-src-row";
      row.appendChild(el("span", "tr-src-label", s.source));
      var track = document.createElement("div");
      track.className = "tr-src-track";
      var bar = document.createElement("div");
      bar.className = "tr-src-bar";
      bar.style.transform = "scaleX(" + Math.max(0.02, s.cnt / maxCnt) + ")";
      track.appendChild(bar);
      row.appendChild(track);
      row.appendChild(el("span", "tr-src-count", fmtNum(s.cnt)));
      sourceEl.appendChild(row);
    });
  }

  // ── 오케스트레이션 ───────────────────────────────────────────────────────
  function renderAll(data) {
    var totals = data.totals || {};
    renderStats(totals);
    renderHeadline(buildHeadline(data));
    renderCategoryRanking(data.by_agency_category || []);
    renderYearTrend(data.by_month || []);
    state.lastFirms = data.top_firms || [];
    renderFirmRanking(state.lastFirms);
    renderEvidence(data.by_evidence || []);
    renderSource(data.by_source || []);
  }

  function rpcEndpoint(name) {
    return url.replace(/\/$/, "") + "/rest/v1/rpc/" + name;
  }

  function fetchStats() {
    return fetch(rpcEndpoint("findings_stats"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_stats " + r.status);
      return r.json();
    });
  }

  function fetchFirmStats(firmName) {
    return fetch(rpcEndpoint("findings_firm_stats"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_firm: firmName }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_firm_stats " + r.status);
      return r.json();
    });
  }

  if (!url || !key) {
    loadingEl.textContent = "트렌드 서비스 준비 중입니다.";
    return;
  }

  fetchStats()
    .then(function (data) {
      loadingEl.hidden = true;
      renderAll(data);
      contentEl.hidden = false;
      maybeOpenFirmFromUrl();
    })
    .catch(function () {
      loadingEl.hidden = true;
      errorEl.hidden = false;
    });
})();
