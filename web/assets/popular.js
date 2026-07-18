/* GRM 인기 카드(Weekly Reactions) — 랜딩 전용 런타임 위젯.
   비골든: 런타임에 상위 3장 목록만 주입한다(랜딩 정적 콘텐츠·결정론 불침범). reactions.js
   와 동형 progressive enhancement — 설정/네트워크/파싱 실패는 전부 삼켜 정적 유도 문구
   (landing.html #grm-popular 초기 마크업)를 그대로 유지한다(오류 문구·콘솔 스팸 0).

   RPC 계약(031_reactions_weekly_top.sql, 불가침): reactions_weekly_top(p_limit) 는
   card_id(text)·distinct_user_count(bigint) 두 필드만 반환한다. stable 함수라 PostgREST
   GET 으로 호출 가능. 이 파일은 이 두 필드 외 어떤 RPC 응답 필드도 읽지 않고, card_id 를
   포함해 RPC 로 받은 어떤 텍스트도 화면에 직접 출력하지 않는다 — 화면 텍스트(제목·기관)는
   전부 커밋된 search-index.json 파생값이다(reactions.js 469~520행 교차 패턴 재사용).
   provenance: card_id 는 인덱스 앵커 조회 키로만 사용. */
(function () {
  "use strict";
  var cfg = document.getElementById("grm-reactions-cfg");
  if (!cfg) return;
  var SUPA_URL = cfg.getAttribute("data-url") || "";
  var SUPA_KEY = cfg.getAttribute("data-key") || "";
  if (!SUPA_URL || !SUPA_KEY) return;

  var root = document.getElementById("grm-popular");
  if (!root) return;
  var idxUrl = root.getAttribute("data-index") || "/assets/search-index.json";

  function rpcUrl() {
    return SUPA_URL.replace(/\/+$/, "") + "/rest/v1/rpc/reactions_weekly_top?p_limit=3";
  }

  Promise.all([
    fetch(rpcUrl(), {
      headers: { apikey: SUPA_KEY, Authorization: "Bearer " + SUPA_KEY }
    }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
    fetch(idxUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
  ]).then(function (out) {
    var rows = out[0], idx = out[1];
    if (!Array.isArray(rows) || !idx || !Array.isArray(idx.cards)) return;

    var byAnchor = {};
    idx.cards.forEach(function (e) {
      var h = e.href || "", i = h.indexOf("#");
      if (i >= 0) byAnchor[h.slice(i + 1)] = e;
    });

    var picked = [];
    for (var i = 0; i < rows.length && picked.length < 3; i++) {
      var row = rows[i];
      if (!row || typeof row.card_id !== "string") continue;
      var count = Number(row.distinct_user_count);
      if (!isFinite(count) || count <= 0) continue;
      var entry = byAnchor[row.card_id];
      if (!entry) continue;
      picked.push({ entry: entry, count: count });
    }
    if (!picked.length) return;

    var ul = document.createElement("ul");
    ul.className = "popular-list";
    picked.forEach(function (p, i) {
      var e = p.entry;
      var li = document.createElement("li");
      li.className = "popular-item";

      var rank = document.createElement("span");
      rank.className = "popular-rank";
      rank.textContent = String(i + 1);
      li.appendChild(rank);

      if (e.agency) {
        var agency = document.createElement("span");
        agency.className = "popular-agency";
        agency.textContent = e.agency;
        li.appendChild(agency);
      }

      var a = document.createElement("a");
      a.className = "popular-title";
      a.href = String(e.href || "").replace(/^\.\.\//, "");
      var title = (e.target || "") + (e.issue ? (" — " + e.issue) : "");
      a.textContent = title || "카드 보기";
      li.appendChild(a);

      var count = document.createElement("span");
      count.className = "popular-count";
      count.textContent = p.count + "명 반응";
      li.appendChild(count);

      ul.appendChild(li);
    });

    root.innerHTML = "";
    root.appendChild(ul);
  }).catch(function () {});
})();
