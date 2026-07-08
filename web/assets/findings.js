/* GRM 지적사항 검색 (FIND-1 M3c) — 정적·클라이언트사이드, 순수 fetch(PostgREST 직접 호출).
 *
 * supabase-js CDN 미사용 — 인증 불필요한 anon SELECT 뿐이라 REST 엔드포인트를 직접 fetch 한다.
 * cfg(url/key) 는 템플릿의 #grm-findings-cfg data-속성(env-param, 미설정이면 빈 문자열)에서
 * 읽는다. 둘 중 하나라도 없으면 오류가 아니라 "준비 중" 안내로 조용히 종료한다(정적 페이지
 * 골든 결정론 — env 값과 무관하게 findings.html 자체 출력은 항상 동일 byte).
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 에 데이터 삽입 금지) — findings
 * 는 원문에서 자동 추출한 자유 텍스트라 이스케이프 누락 시 XSS 위험이 크다(archive.js 의
 * search-index.json 은 렌더러가 이미 생성한 신뢰 데이터라 다른 계약).
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

  var FIELDS = [
    "finding_id", "source", "agency", "document_id", "published_date",
    "firm_name", "category_code", "category_label_ko", "finding_text",
    "finding_language", "evidence_level", "evidence_url", "cfr_refs",
    "mfds_refs", "review_status", "confidence",
  ];

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
    // 카테고리 facet 표시용 code→한국어 라벨 맵(로드된 rows 파생). option.value 는 계속
    // code(필터 로직 불변) — textContent 만 "라벨 (code)" 로 표시(라벨 없으면 code 그대로).
    var catLabel = {};
    ROWS.forEach(function (r) {
      if (r.category_code && r.category_label_ko && !catLabel[r.category_code]) {
        catLabel[r.category_code] = r.category_label_ko;
      }
    });
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
        opt.textContent =
          key2 === "category_code" && catLabel[v] ? catLabel[v] + " (" + v + ")" : v;
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
      var hay = [row.finding_text, row.firm_name, row.document_id]
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
    if (row.category_label_ko) card.appendChild(el("p", "fnd-cat", row.category_label_ko));
    if (row.finding_text) card.appendChild(el("p", "fnd-text", row.finding_text));

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

  showState("loading");
  var cols = FIELDS.join(",");
  var endpoint =
    url.replace(/\/$/, "") +
    "/rest/v1/findings?select=" +
    encodeURIComponent(cols).replace(/%2C/g, ",") +
    "&order=published_date.desc,finding_id.asc&limit=1000";

  fetch(endpoint, {
    headers: { apikey: key, Authorization: "Bearer " + key },
  })
    .then(function (r) {
      if (!r.ok) throw new Error("findings fetch " + r.status);
      return r.json();
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
