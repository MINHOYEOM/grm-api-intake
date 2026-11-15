/* Document facts remain plain text until the runtime cohort index confirms a profile exists. */
(function () {
  "use strict";
  // [11-15 DB 무응답] DB 가 응답하지 않으면 fetch 가 20초 넘게(때로 끝없이) 걸려 화면이 '불러오는 중'에
  // 멈췄다. 공개 RPC 호출은 15초에 끊어 각 화면의 기존 실패 처리(오류 안내·다시 시도)로 넘긴다.
  function _fetchT(u, o, ms) {
    o = o || {};
    if (typeof AbortController === "function" && !o.signal) {   // 자기 상한을 가진 호출(o.signal)은 그대로
      var c = new AbortController();
      setTimeout(function () { c.abort(); }, ms || o.timeoutMs || 15000);
      o.signal = c.signal;
    }
    return fetch(u, o);
  }
  var _t = function (s, v) {
    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;
    return v ? r.replace(/\{(\w+)\}/g, function (m, k) {
      if (k === "s" && !Object.prototype.hasOwnProperty.call(v, "s")) {
        var pn = v.n;
        return (pn === 1 || pn === "1") ? "" : "s";
      }
      return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : r;
  };
  var cfg = document.getElementById("mfds-doc-attendees");
  if (!cfg) return;
  var url = (cfg.getAttribute("data-url") || "").replace(/\/+$/, "");
  var key = cfg.getAttribute("data-key") || "";
  var root = cfg.getAttribute("data-root") || "";
  if (!url || !key) return;
  _fetchT(url + "/rest/v1/rpc/mfds_inspector_index", {
    method: "POST", body: "{}",
    headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
  }).then(function (response) {
    if (!response.ok) throw new Error("MFDS index unavailable");
    return response.json();
  }).then(function (rows) {
    if (!Array.isArray(rows)) return;
    var cohort = Object.create(null);
    rows.forEach(function (row) { if (row.inspector_key) cohort[row.inspector_key] = true; });
    cfg.querySelectorAll("[data-mfds-name]").forEach(function (node) {
      var name = node.getAttribute("data-mfds-name") || "";
      if (!cohort[name]) return;
      var link = document.createElement("a");
      link.href = root + "findings/inspector/index.html?source=mfds&key=" + encodeURIComponent(name);
      link.textContent = name;
      link.rel = "nofollow";
      node.textContent = "";
      node.appendChild(link);
    });
  }).catch(function () { /* The published document facts remain available. */ });
})();
