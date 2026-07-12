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
 *
 * [업체 프로파일 진입] 017_findings_stats_firm_key.sql 적용 라이브에서 top_firms 행에
 * firm_key 가 실려오면, 업체 상세 패널 상단에 findings/firm/index.html?key= 로 가는
 * "업체 프로파일 전체 보기" 링크를 추가한다(findings_firm_stats(p_firm=firm_name) 기반
 * 기존 상세 패널 자체는 그대로 유지 — firm_key 는 오직 이 링크에만 쓴다). 017 미적용
 * 라이브(top_firms 에 firm_key 없음)에서는 링크 렌더를 조용히 생략한다(방어 폴백 —
 * 구버전 top_firms 형태와 신규 형태 둘 다 깨짐 없이 렌더되어야 한다).
 */
(function () {
  "use strict";

  var cfg = document.getElementById("grm-findings-cfg");
  var loadingEl = document.getElementById("tr-loading");
  var errorEl = document.getElementById("tr-error");
  var contentEl = document.getElementById("tr-content");
  var statsEl = document.getElementById("tr-stats");
  // [공개 범위 투명성] 스탯 스트립 수치(전량 집계)와 카테고리 클릭 → 검색 페이지 이동 결과
  // (공개 게이트 통과분만) 사이 간극을 명시하는 노트. 이미 보유한 fetchStats() 응답(totals)을
  // 재사용해 채운다(추가 fetch 0) — 엘리먼트가 없는 구버전 셸이어도(하위호환) renderCoverageNote()
  // 가 조용히 no-op 하도록 방어적으로 조회한다(findings.js 의 hasDash 관례와 동형).
  var coverageNoteEl = document.getElementById("tr-coverage-note");
  var coverageTextEl = document.getElementById("tr-coverage-text");
  var headlineEl = document.getElementById("tr-headline");
  var catEl = document.getElementById("tr-cat");
  var heatmapBlockEl = document.getElementById("tr-heatmap-block");
  var heatmapEl = document.getElementById("tr-heatmap");
  var yearEl = document.getElementById("tr-year");
  var firmsEl = document.getElementById("tr-firms");
  var firmDetailEl = document.getElementById("tr-firm-detail");
  var evidenceEl = document.getElementById("tr-evidence");
  var sourceEl = document.getElementById("tr-source");
  if (!cfg || !loadingEl || !errorEl || !contentEl || !statsEl || !headlineEl ||
      !catEl || !heatmapBlockEl || !heatmapEl || !yearEl || !firmsEl || !firmDetailEl ||
      !evidenceEl || !sourceEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();
  var root = (cfg.getAttribute("data-root") || "").trim();

  // grm_findings.FINDING_TAXONOMY verbatim(code -> {ko, en}) — findings.js CATEGORY_LABELS
  // 와 동일 복제본(동기화 테스트로 드리프트 차단, 파일 상단 계약 참조).
  // v3(2026-07-12): grm_findings.FINDING_TAXONOMY 순서 변경(complaint_recall,
  // computer_system_validation 이동)에 맞춰 선언 순서도 동기화 -- code/label 값 자체는
  // 불변(20개), 대조 테스트는 순서 무관 dict 비교이지만 이 파일의 관례상 선언 순서는
  // taxonomy 계약 순서를 따른다.
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

  var EVIDENCE_ORDER = ["A", "B", "C"];

  // 업체 상세 패널이 열려 있는지(?firm=)·직전 렌더의 top_firms(업체 랭킹 재렌더용)를
  // 여기 담는다 — findings.js 의 단일 state 객체 관례와 동형(별도 저장소 난립 금지).
  // openFirmKey — [업체 프로파일 진입] 017_findings_stats_firm_key.sql 적용 라이브에서만
  // top_firms 행에 firm_key 가 실려온다. 013 미적용/017 미적용 라이브(구버전 top_firms,
  // firm_name 만 있는 형태)에서는 빈 문자열로 남아 프로필 링크를 방어적으로 생략한다.
  var state = { openFirm: "", openFirmKey: "", lastFirms: [] };

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

  // [firm_name 엔티티 디코드 M5] findings.js 의 동명 헬퍼와 동일 계약(별도 파일이라
  // 재사용 불가, 계약만 복제) — DB firm_name 에 &amp;/&#039; 가 이미 이스케이프된 채로
  // 저장된 행을 표시 직전(textContent 대입 전)에만 되돌린다(순수 문자열 치환, XSS 무관).
  function decodeFirmDisplay(s) {
    return String(s || "").replace(/&amp;/g, "&").replace(/&#039;/g, "'");
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
      lines.push("지적 건수가 가장 많은 업체는 " + decodeFirmDisplay(f.firm_name) + "(" + fmtNum(f.cnt) + "건)입니다.");
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

  // [문서 수 병기] totals.documents(010_findings_scope_purity.sql findings_stats 신규
  // 키 — count distinct raw_signal_id, scope_status='ok' 기준)가 있을 때만 "분석 문서"
  // 스탯을 끼워 넣는다. 010 을 프로덕션 SQL Editor 에서 아직 적용하지 않은 라이브에서는
  // 이 키가 undefined 이므로 무조건 조용히 생략한다(레이아웃 깨짐 없음 — 기존 커버리지
  // 노트의 독립 폴백과 동일 정신). "지적 N건" 만 보면 문서(실사) 수로 오해하는 문제를
  // 완화하기 위해 총 지적사항 바로 옆에 둔다.
  function hasDocumentsCount(totals) {
    return typeof totals.documents === "number" && !isNaN(totals.documents);
  }

  function renderStats(totals) {
    statsEl.innerHTML = "";
    statsEl.appendChild(buildStat(fmtNum(totals.findings), "총 지적사항"));
    if (hasDocumentsCount(totals)) {
      statsEl.appendChild(buildStat(fmtNum(totals.documents), "분석 문서"));
    }
    statsEl.appendChild(buildStat(fmtNum(totals.firms), "업체"));
    statsEl.appendChild(buildStat(fmtNum(totals.raw_signals), "원문서"));
    var pub = buildStat(fmtNum(totals.public_findings), "국문 열람 가능");
    pub.appendChild(el("span", "tr-stat-note", "나머지는 집계에만 반영(원문 영문)"));
    statsEl.appendChild(pub);
  }

  function renderHeadline(lines) {
    headlineEl.textContent = lines.join(" ");
  }

  // [공개 범위 투명성] totals 는 fetchStats() 가 이미 fetch 한 findings_stats RPC 응답 —
  // 추가 네트워크 호출 없이 재사용한다. 요소가 없으면(구버전 셸) 조용히 no-op.
  // [문서 수 병기] totals.documents 가 있으면 첫 문장을 "규제 문서 N건에서 추출한 개별
  // 지적사항 M건"으로 바꿔 문서-지적 1:N 관계를 명시한다(010 미적용 시 undefined → 기존
  // "전체 M건" 문안 그대로 유지, 방어적 생략).
  function renderCoverageNote(totals) {
    if (!coverageNoteEl || !coverageTextEl) return;
    var total = Number(totals.findings || 0).toLocaleString("ko-KR");
    var pub = Number(totals.public_findings || 0).toLocaleString("ko-KR");
    var intro = hasDocumentsCount(totals)
      ? "이 대시보드의 수치는 규제 문서 " + Number(totals.documents).toLocaleString("ko-KR") +
        "건에서 추출한 개별 지적사항 " + total + "건 기준 집계입니다(문서당 평균 여러 건)."
      : "이 대시보드의 수치는 전체 " + total + "건 기준 집계입니다.";
    // [완역 자동 전환] 미번역 잔량이 5건 이하면(번역 3레인 소진 — 잔여는 OCR 완파손 등
    // 번역 불능 원문뿐) 미완료 경고를 완료형으로 스스로 전환한다(완역 시점엔 카테고리
    // 클릭 결과와 집계 수치가 일치하므로 경고 자체가 무의미).
    var isComplete =
      Number(totals.findings || 0) > 0 &&
      Number(totals.findings || 0) - Number(totals.public_findings || 0) <= 5;
    // [진행형 문구 중립화] "순차 공개되며"(계속 진행 중이라는 인상)를 "국문 번역이 완료된
    // 지적사항만 열람 가능"이라는 현재 상태 서술로 바꾼다 — 집계 수치와 클릭 결과가 다를
    // 수 있다는 핵심 정보(사용자가 오해하지 않도록 하는 실질 안내)는 그대로 유지한다.
    coverageTextEl.textContent = isComplete
      ? intro + " 전체 지적사항을 국문으로 열람할 수 있습니다."
      : intro + " 개별 원문 열람은 국문 번역이 완료된 지적사항(" + pub + "건)만 가능하며, " +
        "카테고리를 클릭해 이동한 검색 결과는 집계 수치보다 적을 수 있습니다.";
    coverageNoteEl.hidden = false;
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

  // ── 카테고리 × 연도 히트맵(H1) ───────────────────────────────────────────
  // findings_stats()(007)엔 카테고리×시간 매트릭스가 없어 findings_category_matrix()
  // (008)를 별도 RPC 로 병렬 fetch 한다 — 실패해도(008 미적용 라이브 포함) 이 섹션만
  // 조용히 숨겨진 채로 남고 다른 섹션엔 전혀 영향이 없다(§ 오케스트레이션 하단 참조).
  // 셀 농도는 opacity 5단계 정적 버킷(0.08/0.25/0.45/0.7/1.0, 행렬 전체 최댓값 대비 비율
  // 기준) — 0건 셀은 --line 톤의 빈 셀로 렌더한다.
  var HEATMAP_OPACITY_STEPS = [0.08, 0.25, 0.45, 0.7, 1.0];

  function heatmapOpacity(cnt, maxCnt) {
    if (!cnt || maxCnt <= 0) return 0;
    var ratio = cnt / maxCnt;
    if (ratio > 0.8) return HEATMAP_OPACITY_STEPS[4];
    if (ratio > 0.6) return HEATMAP_OPACITY_STEPS[3];
    if (ratio > 0.35) return HEATMAP_OPACITY_STEPS[2];
    if (ratio > 0.15) return HEATMAP_OPACITY_STEPS[1];
    return HEATMAP_OPACITY_STEPS[0];
  }

  function renderHeatmap(data) {
    heatmapEl.innerHTML = "";
    var years = data.years || [];
    var cats = (data.category_totals || []).slice(0, 12);
    if (!cats.length || !years.length) {
      heatmapEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      heatmapBlockEl.hidden = false;
      return;
    }
    var cellMap = {};
    (data.cells || []).forEach(function (c) {
      cellMap[c.category_code + "|" + c.year] = c.cnt || 0;
    });
    var maxCnt = 0;
    cats.forEach(function (c) {
      years.forEach(function (y) {
        var v = cellMap[c.category_code + "|" + y] || 0;
        if (v > maxCnt) maxCnt = v;
      });
    });

    var scroll = document.createElement("div");
    scroll.className = "tr-heatmap-scroll";
    var table = document.createElement("table");
    table.className = "tr-heatmap-table";

    var caption = document.createElement("caption");
    caption.className = "tr-heatmap-caption";
    caption.textContent = "카테고리별 연도별 지적 건수 히트맵(코럴 농도가 건수를 나타냅니다)";
    table.appendChild(caption);

    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    var cornerTh = document.createElement("th");
    cornerTh.setAttribute("scope", "col");
    cornerTh.className = "tr-heatmap-corner";
    cornerTh.textContent = "카테고리";
    headRow.appendChild(cornerTh);
    years.forEach(function (y) {
      var th = document.createElement("th");
      th.setAttribute("scope", "col");
      th.className = "tr-heatmap-yearhead";
      th.textContent = y;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    cats.forEach(function (c) {
      var label = CATEGORY_LABELS[c.category_code];
      var ko = label ? label.ko : c.category_code;
      var row = document.createElement("tr");
      var rowTh = document.createElement("th");
      rowTh.setAttribute("scope", "row");
      rowTh.className = "tr-heatmap-rowhead";
      rowTh.textContent = ko;
      row.appendChild(rowTh);
      years.forEach(function (y) {
        var cnt = cellMap[c.category_code + "|" + y] || 0;
        var td = document.createElement("td");
        td.className = "tr-heatmap-cell";
        td.title = ko + " · " + y + " · " + fmtNum(cnt) + "건";
        if (cnt > 0) {
          var opacity = heatmapOpacity(cnt, maxCnt);
          td.style.backgroundColor = "rgba(194,96,63," + opacity + ")";
          td.style.color = opacity > 0.45 ? "var(--on-coral)" : "var(--ink)";
          td.textContent = fmtNum(cnt);
        } else {
          td.classList.add("tr-heatmap-cell-empty");
        }
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    scroll.appendChild(table);
    heatmapEl.appendChild(scroll);
    heatmapBlockEl.hidden = false;
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
    // [firm_name 엔티티 디코드 M5] 클릭/state 비교는 raw f.firm_name(DB 원본값) 그대로 —
    // openFirm()/syncFirmUrl() 이 그 값을 findings_firm_stats RPC exact-match 파라미터로
    // 쓰므로 디코드하면 어긋난다. 디코드는 표시(라벨·aria-label)에만 적용한다.
    var firmDisplay = decodeFirmDisplay(f.firm_name);
    makeClickableRow(row, firmDisplay + " 상세 보기: " + f.cnt + "건", function () {
      if (state.openFirm === f.firm_name) closeFirm();
      else openFirm(f.firm_name, f.firm_key);
    });
    row.appendChild(el("span", "tr-firm-rank", String(idx + 1)));
    row.appendChild(el("span", "tr-firm-name", firmDisplay));
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

  // [업체 프로파일 진입] 017_findings_stats_firm_key.sql 적용 라이브에서 top_firms
  // 행에 firm_key 가 실려올 때만 렌더한다(state.openFirmKey, openFirm 호출 시점에
  // 세팅) — 017 미적용 라이브(top_firms 에 firm_key 없음)에서는 빈 문자열이라 링크
  // 자체를 생략한다(방어, 레이아웃 깨짐 없음). findings/firm/index.html 은 findings/
  // trends/index.html 과 같은 findings/ 하위 형제 디렉터리라 rel_root 계산 없이
  // "../firm/index.html" 상대경로 하나로 충분하다(findings.js buildDocHead 의
  // "firm/index.html" 관례와 동형 — 깊이만 한 단계 다르다).
  function buildFirmProfileLink(firmKey) {
    var a = document.createElement("a");
    a.className = "tr-fd-profile-link";
    a.href = "../firm/index.html?key=" + encodeURIComponent(firmKey);
    a.textContent = "업체 프로파일 전체 보기 →";
    return a;
  }

  function renderFirmDetail(data) {
    firmDetailEl.innerHTML = "";
    if (state.openFirmKey) {
      firmDetailEl.appendChild(buildFirmProfileLink(state.openFirmKey));
    }
    var head = document.createElement("div");
    head.className = "tr-firm-detail-head";
    var idbox = document.createElement("div");
    idbox.appendChild(el("h3", "tr-firm-detail-name", decodeFirmDisplay(data.firm_name || "")));
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

  function openFirm(name, firmKey) {
    state.openFirm = name;
    state.openFirmKey = firmKey || "";
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
    state.openFirmKey = "";
    renderFirmRanking(state.lastFirms);
    syncFirmUrl("");
    firmDetailEl.hidden = true;
    firmDetailEl.innerHTML = "";
  }

  // ?firm= 으로 직접 진입한 경우(북마크·공유 링크 등)에도 프로필 링크가 뜨도록,
  // 이미 fetch 된 state.lastFirms(top_firms) 에서 이름이 일치하는 행의 firm_key 를
  // 찾아 함께 넘긴다 — 017 미적용 라이브에서는 어차피 firm_key 가 없어 "" 로 방어된다.
  function findFirmKeyByName(name) {
    for (var i = 0; i < state.lastFirms.length; i++) {
      if (state.lastFirms[i].firm_name === name) return state.lastFirms[i].firm_key || "";
    }
    return "";
  }

  function maybeOpenFirmFromUrl() {
    if (typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    var f = params.get("firm");
    if (f) openFirm(f, findFirmKeyByName(f));
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
    renderCoverageNote(totals);
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

  // 008_findings_category_matrix.sql — findings_stats() 와 별개 RPC(H1). 008 미적용
  // 라이브에서 404 를 반환하므로 이 fetch 만 독립적으로 실패 처리한다(아래 오케스트레이션).
  function fetchCategoryMatrix() {
    return fetch(rpcEndpoint("findings_category_matrix"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_category_matrix " + r.status);
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

  // H1 히트맵 — findings_stats 와 독립적으로 병렬 fetch(위 fetchStats() 와 별개 promise
  // 체인). 실패해도(008 미적용 라이브 포함) tr-heatmap-block 은 정적 셸의 기본값인
  // hidden 상태 그대로 남는다 — 다른 섹션엔 전혀 영향이 없다.
  fetchCategoryMatrix()
    .then(function (data) { renderHeatmap(data); })
    .catch(function () { /* 조용히 숨김 유지 */ });
})();
