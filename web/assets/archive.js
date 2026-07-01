/* GRM 아카이브 교차검색 (P4) — 정적·클라이언트사이드. 빌드시 생성된
 * assets/search-index.json 으로 전 호 카드 검색 + facet 필터.
 *
 * progressive enhancement: 서버가 렌더한 정적 호 목록(#static-issues)이 baseline.
 * 이 스크립트는 인덱스 fetch 성공 시에만 body.js-search 를 켜고 #results 를 동적
 * 치환한다. fetch/파싱 실패·JS 미지원 → 정적 목록 그대로(열람 가능). 런타임 서버 0.
 *
 * 무변형: 인덱스는 카드 기존 값만 담는다(렌더러 파생). 검색은 그 값을 소문자 비교·
 * 하이라이트할 뿐 재생성하지 않는다. 검색 동작은 비골든(spec §1.5).
 */
(function () {
  "use strict";

  // ── 인덱스 위치: 자기 <script src> 기준(페이지 깊이·서브패스 무관) ──────────
  var scriptEl =
    document.currentScript ||
    document.querySelector('script[src$="archive.js"]');
  if (!scriptEl) return;
  var indexUrl = new URL("search-index.json", scriptEl.src).href;

  var state = {
    q: "",
    view: "issues",
    agency: new Set(),
    cat: new Set(),
    mod: new Set(),
    month: new Set(),
  };
  var DATA = null; // {facets, issues, cards}

  // ── escape (텍스트·속성 공용; 과다 이스케이프는 텍스트에서 무해) ───────────
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (m) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[m];
    });
  }
  function tokens(s) {
    return (s || "").toLowerCase().split(/\s+/).filter(Boolean);
  }
  function hi(s) {
    var out = esc(s);
    var q = state.q.trim();
    if (!q) return out;
    tokens(q).forEach(function (t) {
      var re = new RegExp(
        "(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")",
        "ig"
      );
      out = out.replace(re, "<mark>$1</mark>");
    });
    return out;
  }

  // ── 매칭 ────────────────────────────────────────────────────────────────
  function cardMatches(c) {
    if (state.agency.size && !state.agency.has(c.agency)) return false;
    if (state.cat.size && !state.cat.has(c.category)) return false;
    if (state.mod.size && !(c.modality && state.mod.has(c.modality)))
      return false;
    if (state.month.size && !state.month.has(c.month)) return false;
    var q = state.q.trim().toLowerCase();
    if (q) {
      var hay = (c.text || "").toLowerCase();
      if (
        !tokens(q).every(function (t) {
          return hay.indexOf(t) !== -1;
        })
      )
        return false;
    }
    return true;
  }
  function anyFilter() {
    return (
      !!state.q.trim() ||
      state.agency.size ||
      state.cat.size ||
      state.mod.size ||
      state.month.size
    );
  }

  // ── facet 칩(데이터 기반 — 실제 존재값만) ────────────────────────────────
  var FACET_DEFS = [
    ["기관", "agencies", "agency"],
    ["카테고리", "categories", "cat"],
    ["제품군", "modalities", "mod"],
    ["기간", "months", "month"],
  ];
  function buildFacets() {
    var html = FACET_DEFS.map(function (def) {
      var label = def[0],
        vals = (DATA.facets && DATA.facets[def[1]]) || [],
        key = def[2];
      if (!vals.length) return "";
      var chips = vals
        .map(function (v) {
          return (
            '<button type="button" class="chip" data-key="' +
            esc(key) +
            '" data-val="' +
            esc(v) +
            '">' +
            esc(v) +
            "</button>"
          );
        })
        .join("");
      return (
        '<div class="frow"><span class="flab">' +
        esc(label) +
        '</span><div class="chips">' +
        chips +
        "</div></div>"
      );
    }).join("");
    document.getElementById("facets").innerHTML = html;
  }

  // ── 렌더 ────────────────────────────────────────────────────────────────
  function emptyHTML() {
    return (
      '<div class="empty"><i class="ti ti-search-off"></i><b>결과 없음</b><br>검색어나 필터를 바꿔보세요.</div>'
    );
  }
  function delay(i) {
    return Math.min(i * 32, 360);
  }

  function render() {
    var results = document.getElementById("results");
    var matched = DATA.cards.filter(cardMatches);
    var countEl = document.getElementById("count");
    var filtered = anyFilter();

    if (state.view === "cards") {
      countEl.innerHTML =
        "카드 <b>" +
        matched.length +
        '</b><span class="mono"> / ' +
        DATA.cards.length +
        "</span>건";
    } else {
      var shown = filtered
        ? new Set(
            matched.map(function (c) {
              return c.date;
            })
          ).size
        : DATA.issues.length;
      // P1-3: 필터 적용 시 일치 카드 총수를 함께 노출(호 수만으론 'EMA 2건' 같은 결과량이 안 보임).
      countEl.innerHTML = filtered
        ? "카드 <b>" + matched.length + "</b>건 · 호 " + shown + "개"
        : "호 <b>" + shown + "</b>개";
    }
    document.getElementById("clearall").classList.toggle("on", !!filtered);

    if (state.view === "cards") {
      if (!matched.length) {
        results.innerHTML = emptyHTML();
        return;
      }
      var cards = matched.slice().sort(function (a, b) {
        return (
          (b.date > a.date ? 1 : b.date < a.date ? -1 : 0) ||
          Number(b.signal_tier) - Number(a.signal_tier)
        );
      });
      results.innerHTML =
        '<div class="cards">' +
        cards
          .map(function (c, i) {
            var mod = c.modality
              ? '<span class="b mod">' + esc(c.modality) + "</span>"
              : "";
            var issue = c.issue ? " — <b>" + hi(c.issue) + "</b>" : "";
            return (
              '<a class="cresult" href="' +
              esc(c.href) +
              '" style="animation-delay:' +
              delay(i) +
              'ms">' +
              '<div><div class="badges"><span class="b ag">' +
              esc(c.agency) +
              '</span><span class="b cat">' +
              esc(c.category) +
              "</span>" +
              mod +
              "</div>" +
              "<h4>" +
              hi(c.target) +
              issue +
              "</h4>" +
              '<div class="meta"><span class="mono">Vol.' +
              esc(c.issue_no) +
              " · " +
              esc(c.date) +
              "</span> · " +
              esc(c.card_type) +
              " · Signal T" +
              esc(c.signal_tier) +
              " · Evidence " +
              esc(c.evidence_level) +
              "</div></div>" +
              '<i class="ti ti-arrow-right go"></i></a>'
            );
          })
          .join("") +
        "</div>";
      return;
    }

    // 주간 호 뷰 — 인덱스 issues(서버 baseline 과 동일 파생) + 필터시 일치건수.
    var volHit = {};
    matched.forEach(function (c) {
      volHit[c.date] = (volHit[c.date] || 0) + 1;
    });
    var issues = DATA.issues;
    if (filtered)
      issues = issues.filter(function (v) {
        return volHit[v.date];
      });
    if (!issues.length) {
      results.innerHTML = emptyHTML();
      return;
    }
    results.innerHTML =
      '<div class="issuelist">' +
      issues
        .map(function (v, i) {
          var badge = v.latest
            ? '<span class="badge"><span class="live"></span>이번 주 · LIVE</span>'
            : "";
          var tags = (v.agencies || [])
            .map(function (a) {
              return '<span class="tag">' + esc(a) + "</span>";
            })
            .join("");
          var hit = filtered
            ? '<span class="tag hit">일치 ' + volHit[v.date] + "건</span>"
            : "";
          return (
            '<a class="issue' +
            (v.latest ? " latest" : "") +
            '" href="' +
            esc(v.href) +
            '" style="animation-delay:' +
            i * 45 +
            'ms">' +
            '<div class="vol">Vol.<b>' +
            esc(v.issue_no) +
            "</b></div>" +
            "<div>" +
            badge +
            "<h3>" +
            esc(v.title) +
            "</h3>" +
            '<div class="row"><span class="tag date">' +
            esc(v.date) +
            "</span>" +
            tags +
            hit +
            "</div></div>" +
            '<div class="stat"><b>' +
            esc(v.count) +
            '</b><span class="u">건</span><span class="ev mono">' +
            esc(v.ev) +
            "</span></div></a>"
          );
        })
        .join("") +
      "</div>";
  }

  // ── 컨트롤 배선 ───────────────────────────────────────────────────────────
  function setView(v) {
    state.view = v;
    var toggle = document.getElementById("toggle");
    toggle.classList.toggle("cards", v === "cards");
    toggle.querySelectorAll("button").forEach(function (x) {
      x.classList.toggle("on", x.dataset.view === v);
    });
    render();
  }

  function wire() {
    var facetsEl = document.getElementById("facets");
    facetsEl.addEventListener("click", function (e) {
      var b = e.target.closest(".chip");
      if (!b) return;
      var set = state[b.dataset.key],
        v = b.dataset.val;
      if (set.has(v)) {
        set.delete(v);
        b.classList.remove("on");
      } else {
        set.add(v);
        b.classList.add("on");
      }
      render();
    });

    var q = document.getElementById("q"),
      qclear = document.getElementById("qclear"),
      sbar = document.getElementById("searchbar");
    q.addEventListener("focus", function () {
      sbar.classList.add("focused");
    });
    q.addEventListener("blur", function () {
      sbar.classList.remove("focused");
    });
    q.addEventListener("input", function () {
      state.q = q.value;
      qclear.classList.toggle("on", !!q.value);
      if (q.value && state.view === "issues") setView("cards");
      else render();
    });
    qclear.addEventListener("click", function () {
      q.value = "";
      state.q = "";
      qclear.classList.remove("on");
      q.focus();
      render();
    });

    document
      .getElementById("toggle")
      .addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (b) setView(b.dataset.view);
      });

    document
      .getElementById("clearall")
      .addEventListener("click", function () {
        state.q = "";
        q.value = "";
        qclear.classList.remove("on");
        ["agency", "cat", "mod", "month"].forEach(function (k) {
          state[k].clear();
        });
        facetsEl.querySelectorAll(".chip.on").forEach(function (c) {
          c.classList.remove("on");
        });
        render();
      });
  }

  // ── 부트: 인덱스 fetch → 성공 시에만 검색 UI 노출(graceful) ────────────────
  fetch(indexUrl, { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("search-index fetch " + r.status);
      return r.json();
    })
    .then(function (data) {
      if (!data || !Array.isArray(data.cards) || !Array.isArray(data.issues))
        throw new Error("search-index shape");
      DATA = data;
      buildFacets();
      wire();
      document.body.classList.add("js-search");
      render(); // #static-issues 를 동적 결과로 치환(무필터 = 동일 모습)
    })
    .catch(function () {
      /* graceful: 정적 호 목록(#static-issues) 그대로 둔다. */
    });
})();
