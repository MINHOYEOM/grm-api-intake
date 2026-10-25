/* Document facts remain plain text until the runtime cohort index confirms a profile exists. */
(function () {
  "use strict";
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
  fetch(url + "/rest/v1/rpc/mfds_inspector_index", {
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
