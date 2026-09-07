/* [용어사전] 클라이언트 필터 — 정적·무의존(vanilla). 서버가 렌더한 초성 색인 1페이지의
 * 용어 카드를 검색어로 걸러 보여준다. 결정론 렌더(골든) 불침범: 이 스크립트는 런타임에
 * hidden 속성 토글과 **이미 있는 카드 노드의 이동**만 하고 콘텐츠(값/링크)는 만들지 않는다.
 * JS 미로드 시 전 용어가 그대로 보임(progressive enhancement) — 정적 열람·해시 딥링크 무영향.
 *
 * [2026-09-07] 검색 3종 보강. 종전엔 순위가 없어서 `CAPA` 를 치면 설명문에 CAPA 가 든
 * 카드까지 초성 순으로 나열되고 정작 「시정 및 예방조치(CAPA)」는 12건 중 여섯 번째였다
 * (`OOS` 는 3건 중 두 번째 — 「감사추적 검토」가 먼저 나왔다).
 *  1) 정확 일치 우선 — 이름(표제어·영문 표제어·괄호 안 약어·동의어)이 검색어와 같은 카드를
 *     맨 위 구역으로, 이름에 든 카드를 그 다음, 설명에만 든 카드를 마지막으로 옮긴다.
 *     판정 재료는 **화면에 실제로 나가는 문자열**이다(data 속성을 새로 만들지 않는다).
 *  2) 단어별 검색 — 토큰이 둘 이상이면 하나라도 맞으면 히트(OR), 맞은 토큰 수로 정렬한다.
 *     토큰이 하나면 판정 의미론은 종전과 완전히 같다(부분일치·단어경계 아님).
 *  3) 해시 구제 — 필터로 감춰진 카드를 해시가 가리키면 검색을 비우고 그 카드로 스크롤한다. */
(function () {
  "use strict";
  var _t = function (s, v) {
    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;
    return v ? r.replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : r;
  };
  var input = document.getElementById("grm-gl-q");
  if (!input) return;
  var terms = Array.prototype.slice.call(document.querySelectorAll(".gl-term"));
  var groups = Array.prototype.slice.call(document.querySelectorAll(".gl-group"));
  var indexLinks = Array.prototype.slice.call(
    document.querySelectorAll("#grm-gl-index a[data-bucket]"));
  var countEl = document.getElementById("grm-gl-count");
  var emptyEl = document.getElementById("grm-gl-empty");
  var body = document.querySelector(".gl-body");
  var total = countEl ? (countEl.getAttribute("data-total") || String(terms.length)) : String(terms.length);

  // 그룹 앵커 → 색인 링크 매핑(빈 그룹의 색인 버튼도 함께 숨긴다).
  var linkByBucket = {};
  indexLinks.forEach(function (a) { linkByBucket[a.getAttribute("data-bucket")] = a; });

  // ── 이름 후보 ──────────────────────────────────────────────────────────────
  // 카드가 화면에 띄운 "이름"들. 표제어 링크 텍스트·영문 부제(한국어판만 값이 있다)·
  // 동의어 목록에서 읽는다. 괄호가 있으면 안(약어)과 밖(괄호를 뺀 나머지)도 각각
  // 후보다 — 「Out-of-Specification (OOS) Result」 카드는 `OOS` 로도 이름이 맞아야 한다.
  var PAREN = /\(([^)]+)\)/g;
  var MIDDOT = /\s+·\s+/;   // 템플릿의 aliases|join(' · ') 구분자. 동의어 안의 `시정·예방조치` 는 안 쪼갠다.
  function norm(s) { return String(s).toLowerCase().replace(/[\s.-]+/g, ""); }
  function pushName(out, s) {
    var v = String(s || "").trim();
    if (!v) return;
    out.push(v);
    var m, inner = [];
    PAREN.lastIndex = 0;
    while ((m = PAREN.exec(v)) !== null) inner.push(m[1]);
    if (!inner.length) return;
    for (var i = 0; i < inner.length; i++) out.push(inner[i]);
    var outer = v.replace(/\([^)]*\)/g, " ").replace(/\s+/g, " ").trim();
    if (outer) out.push(outer);
  }
  function textOf(card, sel) {
    var el = card.querySelector(sel);
    return el ? (el.textContent || "") : "";
  }
  function namesOf(card) {
    var out = [];
    pushName(out, textOf(card, ".gl-term-link"));
    pushName(out, textOf(card, ".gl-term-en"));
    var alias = textOf(card, ".gl-alias-v").split(MIDDOT);
    for (var i = 0; i < alias.length; i++) pushName(out, alias[i]);
    var lower = [], normed = [];
    for (var j = 0; j < out.length; j++) { lower.push(out[j].toLowerCase()); normed.push(norm(out[j])); }
    return { lower: lower, normed: normed };
  }

  // 카드별 고정 재료를 로드 시 한 번만 읽는다(입력마다 DOM 을 다시 읽지 않는다).
  var cards = terms.map(function (el, i) {
    var n = namesOf(el);
    return {
      el: el, order: i,
      search: el.getAttribute("data-search") || "",
      lower: n.lower, normed: n.normed,
      parent: el.parentNode, next: el.nextSibling
    };
  });

  // ── 등급 구역 ──────────────────────────────────────────────────────────────
  // 검색 중에만 쓰는 빈 껍데기 3개. 카드는 **복제하지 않고 옮긴다**(id 가 둘이 되면
  // 해시 딥링크·퀴즈 링크가 어느 쪽으로 갈지 알 수 없게 된다).
  var RANK_LABELS = ["", _t("일치하는 용어"), _t("이름에 포함"), _t("설명에 언급된 용어")];
  var hitSections = [];
  if (body) {
    for (var r = 3; r >= 1; r--) {
      var sec = document.createElement("section");
      sec.className = "gl-group gl-hit";
      sec.setAttribute("data-rank", String(r));
      sec.hidden = true;
      var h = document.createElement("h2");
      h.className = "gl-group-h";
      h.textContent = RANK_LABELS[r];
      sec.appendChild(h);
      body.insertBefore(sec, body.firstChild);
      hitSections[r] = sec;
    }
  }
  var scattered = false;

  function restore() {
    if (!scattered) return;
    // 역순 복귀 — 뒤 카드부터 되돌리면 앞 카드의 `next` 형제가 이미 제자리에 있다.
    for (var i = cards.length - 1; i >= 0; i--) {
      var c = cards[i];
      if (c.el.parentNode !== c.parent) c.parent.insertBefore(c.el, c.next);
    }
    scattered = false;
  }

  function apply() {
    var q = input.value.trim().toLowerCase();
    var tokens = q ? q.split(/\s+/) : [];
    // 등급을 매길 대조 문자열 — 검색어 전체, 그리고 낱말이 둘 이상이면 낱말 각각.
    // 낱말이 하나면 목록은 [q] 하나뿐이라 판정이 종전 설계와 글자 그대로 같다.
    // 낱말이 둘 이상일 때 이 확장이 없으면 「밸리데이션 적격성평가 차이」 같은 검색에서
    // 정작 「밸리데이션」·「적격성평가」 카드가 56건 중 14·17번째로 밀린다(실측).
    var probes = [], probeNorms = [];
    if (q) {
      probes.push(q);
      if (tokens.length > 1) probes = probes.concat(tokens);
      for (var pi = 0; pi < probes.length; pi++) probeNorms.push(norm(probes[pi]));
    }
    var shown = 0;
    var buckets = [null, [], [], []];
    restore();
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i], hit = false, hits = 0, rank = 3;
      if (q === "") {
        hit = true;
      } else if (tokens.length < 2) {
        // 단일 토큰: 종전과 같은 부분일치(단어경계 아님) — 도달 탐침 표의 의미론.
        hit = c.search.indexOf(q) !== -1;
        hits = hit ? 1 : 0;
      } else {
        for (var k = 0; k < tokens.length; k++) {
          if (c.search.indexOf(tokens[k]) !== -1) hits++;
        }
        hit = hits > 0;
      }
      if (hit && q !== "") {
        rank = 3;
        for (var p = 0; p < probes.length && rank > 1; p++) {
          var pq = probes[p], pn = probeNorms[p];
          for (var j = 0; j < c.normed.length; j++) {
            if (pn && c.normed[j] === pn) { rank = 1; break; }
            if (rank === 3 && (c.lower[j].indexOf(pq) !== -1
                || (pn && c.normed[j].indexOf(pn) !== -1))) rank = 2;
          }
        }
        buckets[rank].push({ card: c, hits: hits });
      }
      c.el.hidden = !hit;
      if (hit) shown++;
    }
    // 등급 안 순서: 맞은 토큰 수 내림차순, 같으면 원래 순서(안정 정렬).
    if (q !== "" && body) {
      for (var b = 1; b <= 3; b++) {
        var list = buckets[b];
        list.sort(function (x, y) { return (y.hits - x.hits) || (x.card.order - y.card.order); });
        for (var m2 = 0; m2 < list.length; m2++) hitSections[b].appendChild(list[m2].card.el);
        hitSections[b].hidden = list.length === 0;
        if (list.length) scattered = true;
      }
    } else {
      for (var b2 = 1; b2 <= 3; b2++) if (hitSections[b2]) hitSections[b2].hidden = true;
    }
    // 빈 그룹(+색인 버튼) 숨김.
    for (var g = 0; g < groups.length; g++) {
      var visible = groups[g].querySelectorAll(".gl-term:not([hidden])").length;
      groups[g].hidden = visible === 0;
      var link = linkByBucket[groups[g].getAttribute("data-bucket")];
      if (link) link.hidden = visible === 0;
    }
    if (emptyEl) emptyEl.hidden = shown !== 0;
    if (countEl) {
      if (q === "") countEl.innerHTML = _t("전체") + " <b>" + total + "</b>" + _t("개 용어");
      else countEl.innerHTML = "<b>" + shown + "</b>" + _t("개 표시");
    }
  }

  // ── 해시 구제 ──────────────────────────────────────────────────────────────
  // 해시가 가리키는 카드가 필터로 감춰져 있으면 주소만 바뀌고 화면은 그대로다(퀴즈의
  // `glossary/#id` 딥링크로 들어왔다가 검색한 뒤 되돌아오는 경로). 그때는 검색을 비운다.
  // ★"감춰졌다"가 아니라 **이 필터가 감췄다**로 가른다. 이 페이지에는 제 사정으로
  //   hidden 인 요소가 따로 있고(맨 위로 버튼·언어 안내 띠·마스코트 패널), 그것들을
  //   가리키는 해시에 반응하면 사용자가 친 검색어를 아무 이유 없이 지우게 된다.
  function ours(el) {
    return !!el && !!el.classList
      && (el.classList.contains("gl-term") || el.classList.contains("gl-group"));
  }
  function revealHash() {
    var raw = (location.hash || "").slice(1);
    if (!raw) return;
    var el = document.getElementById(raw);
    if (!el) {
      try { el = document.getElementById(decodeURIComponent(raw)); } catch (e) { el = null; }
    }
    if (!ours(el) || !el.hidden) return;
    input.value = "";
    apply();
    if (el.scrollIntoView) el.scrollIntoView();
  }

  input.addEventListener("input", apply);
  window.addEventListener("hashchange", revealHash);
  // 뒤로가기로 돌아오면 브라우저가 검색창 값만 되살리고 필터는 안 건 상태일 수 있다(폼 복원).
  // 그러면 `OOS` 라고 적힌 창 밑에 242개가 다 보인다 — 말과 화면이 어긋난다.
  if (input.value.trim()) apply();
  revealHash();
})();
