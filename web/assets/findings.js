/* GRM 지적사항 검색 (FIND-1 M3c, 국문 우선표시+카테고리 라벨 M6d) — 정적·클라이언트사이드,
 * 순수 fetch(PostgREST 직접 호출).
 *
 * supabase-js CDN 미사용 — 인증 불필요한 anon SELECT 뿐이라 REST 엔드포인트를 직접 fetch 한다.
 * cfg(url/key) 는 템플릿의 #grm-findings-cfg data-속성(env-param, 미설정이면 빈 문자열)에서
 * 읽는다. 둘 중 하나라도 없으면 오류가 아니라 "준비 중" 안내로 조용히 종료한다(정적 페이지
 * 골든 결정론 — env 값과 무관하게 findings.html 자체 출력은 항상 동일 byte).
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 에 데이터 삽입 금지) — findings
 * 는 원문에서 자동 추출한 자유 텍스트라 이스케이프 누락 시 XSS 위험이 크다(archive.js 의
 * search-index.json 은 렌더러가 이미 생성한 신뢰 데이터라 다른 계약).
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

  var FACET_DEFS = [
    ["fnd-f-agency", "agency"],
    ["fnd-f-category", "category_code"],
    ["fnd-f-source", "source"],
    ["fnd-f-evidence", "evidence_level"],
    ["fnd-f-status", "review_status"],
    ["fnd-f-month", "month"],
  ];

  var state = { q: "", agency: "", category_code: "", source: "", evidence_level: "", review_status: "", month: "" };
  var ROWS = null; // fetch 성공 시 findings 배열
  var debounceTimer = null;

  var qInput = document.getElementById("fnd-q");
  var resetBtn = document.getElementById("fnd-reset");

  function monthOf(row) {
    var d = row.published_date || "";
    return d.length >= 7 ? d.slice(0, 7) : "";
  }

  function buildFacetOptions() {
    FACET_DEFS.forEach(function (def) {
      var selId = def[0], key2 = def[1];
      var sel = document.getElementById(selId);
      if (!sel) return;
      var vals = {};
      ROWS.forEach(function (r) {
        var v = key2 === "month" ? monthOf(r) : r[key2];
        if (v) vals[v] = true;
      });
      var sorted = Object.keys(vals).sort();
      if (key2 === "month") sorted.reverse(); // 최신월 우선
      sorted.forEach(function (v) {
        var opt = document.createElement("option");
        opt.value = v;
        if (key2 === "category_code") {
          var cat = CATEGORY_LABELS[v];
          opt.textContent = cat ? cat.ko + " · " + cat.en : v;
        } else {
          opt.textContent = v;
        }
        sel.appendChild(opt);
      });
    });
  }

  function matches(row) {
    if (state.agency && row.agency !== state.agency) return false;
    if (state.category_code && row.category_code !== state.category_code) return false;
    if (state.source && row.source !== state.source) return false;
    if (state.evidence_level && row.evidence_level !== state.evidence_level) return false;
    if (state.review_status && row.review_status !== state.review_status) return false;
    if (state.month && monthOf(row) !== state.month) return false;
    var q = state.q.trim().toLowerCase();
    if (q) {
      var hay = [row.finding_text, row.finding_text_ko, row.firm_name, row.document_id]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }
    return true;
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

  var EVIDENCE_LABEL = { A: "Evidence A", B: "Evidence B", C: "Evidence C" };
  var STATUS_LABEL = { needs_review: "검토 필요", accepted: "검토 완료", rejected: "반려" };

  function appendFindingText(card, row) {
    // [원문·국문 병기 M6d] finding_text_ko 가 있으면 국문을 본문으로, 원문은 접기(details)로
    // 낮춰 보여준다. 없으면(폴백 fetch 포함) 기존처럼 원문만 그대로 표시.
    var ko = (row.finding_text_ko || "").trim();
    if (!ko) {
      if (row.finding_text) card.appendChild(el("p", "fnd-text", row.finding_text));
      return;
    }
    card.appendChild(el("p", "fnd-text", ko));
    if (row.finding_text) {
      var details = document.createElement("details");
      details.className = "fnd-orig";
      var summary = document.createElement("summary");
      summary.textContent = "원문 보기 (영문)";
      details.appendChild(summary);
      var p = document.createElement("p");
      p.textContent = row.finding_text;
      details.appendChild(p);
      card.appendChild(details);
    }
    if (row.translation_method === "llm_assisted") {
      card.appendChild(el("span", "fnd-tr-note", "AI 번역 — 원문 대조 권장"));
    }
  }

  function buildCard(row) {
    var card = el("article", "fnd-card");

    var head = el("div", "fnd-card-head");
    head.appendChild(el("span", "fnd-b date", row.published_date || ""));
    head.appendChild(el("span", "fnd-b", row.agency || ""));
    head.appendChild(el("span", "fnd-b", row.source || ""));
    var evLabel = EVIDENCE_LABEL[row.evidence_level] || row.evidence_level || "";
    if (evLabel) {
      head.appendChild(el("span", "fnd-b" + (row.evidence_level === "A" ? " ev-a" : ""), evLabel));
    }
    if (row.review_status === "needs_review") {
      head.appendChild(el("span", "fnd-b needs-review", STATUS_LABEL.needs_review));
    } else if (row.review_status && STATUS_LABEL[row.review_status]) {
      head.appendChild(el("span", "fnd-b", STATUS_LABEL[row.review_status]));
    }
    card.appendChild(head);

    if (row.firm_name) card.appendChild(el("h3", "fnd-firm", row.firm_name));
    var cat = CATEGORY_LABELS[row.category_code];
    var catText = cat ? cat.ko : row.category_label_ko;
    if (catText) card.appendChild(el("p", "fnd-cat", catText));
    appendFindingText(card, row);

    var refs = ([]).concat(row.cfr_refs || [], row.mfds_refs || []);
    if (refs.length) {
      var refsWrap = el("div", "fnd-refs");
      refs.forEach(function (r) {
        if (r) refsWrap.appendChild(el("span", "fnd-ref", r));
      });
      card.appendChild(refsWrap);
    }

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
      card.appendChild(a);
    }

    return card;
  }

  function render() {
    var matched = ROWS.filter(matches);
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
    var frag = document.createDocumentFragment();
    matched.forEach(function (row) {
      frag.appendChild(buildCard(row));
    });
    resultsEl.appendChild(frag);
  }

  function wire() {
    FACET_DEFS.forEach(function (def) {
      var sel = document.getElementById(def[0]);
      if (!sel) return;
      sel.addEventListener("change", function () {
        state[def[1]] = sel.value;
        render();
      });
    });
    if (qInput) {
      qInput.addEventListener("input", function () {
        state.q = qInput.value;
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(render, 150);
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        state = { q: "", agency: "", category_code: "", source: "", evidence_level: "", review_status: "", month: "" };
        if (qInput) qInput.value = "";
        FACET_DEFS.forEach(function (def) {
          var sel = document.getElementById(def[0]);
          if (sel) sel.value = "";
        });
        render();
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
      buildFacetOptions();
      wire();
      render();
    })
    .catch(function () {
      showState("error");
    });
})();
