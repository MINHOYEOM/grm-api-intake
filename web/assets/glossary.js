/* [용어사전] 클라이언트 필터 — 정적·무의존(vanilla). 서버가 렌더한 초성 색인 1페이지의
 * 용어 카드를 검색어로 걸러 보여준다. 결정론 렌더(골든) 불침범: 이 스크립트는 런타임에
 * hidden 속성만 토글하고 콘텐츠(값/링크)는 만들지 않는다. JS 미로드 시 전 용어가 그대로
 * 보임(progressive enhancement) — 정적 열람·해시 딥링크 무영향. */
(function () {
  "use strict";
  var input = document.getElementById("grm-gl-q");
  if (!input) return;
  var terms = Array.prototype.slice.call(document.querySelectorAll(".gl-term"));
  var groups = Array.prototype.slice.call(document.querySelectorAll(".gl-group"));
  var indexLinks = Array.prototype.slice.call(
    document.querySelectorAll("#grm-gl-index a[data-bucket]"));
  var countEl = document.getElementById("grm-gl-count");
  var emptyEl = document.getElementById("grm-gl-empty");
  var total = countEl ? (countEl.getAttribute("data-total") || String(terms.length)) : String(terms.length);

  // 그룹 앵커 → 색인 링크 매핑(빈 그룹의 색인 버튼도 함께 숨긴다).
  var linkByBucket = {};
  indexLinks.forEach(function (a) { linkByBucket[a.getAttribute("data-bucket")] = a; });

  function apply() {
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    for (var i = 0; i < terms.length; i++) {
      var hit = q === "" || (terms[i].getAttribute("data-search") || "").indexOf(q) !== -1;
      terms[i].hidden = !hit;
      if (hit) shown++;
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
      if (q === "") countEl.innerHTML = "전체 <b>" + total + "</b>개 용어";
      else countEl.innerHTML = "<b>" + shown + "</b>개 표시";
    }
  }

  input.addEventListener("input", apply);
})();
