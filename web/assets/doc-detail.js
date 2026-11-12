/* 483 문서 페이지 '구체 내용' (UI 2단 2026-11-12, 지식 그래프 1단계 화면).
   지적 표제 아래에 상세 서술("Specifically, …")을 접어서 붙인다. 값은 findings 의
   detail_text(원문)·detail_text_ko(국문) — 공개 키로 읽는 표 단위 권한 그대로(RLS 동일).
   화면 규칙: 상세가 문장으로 끝나면 싣는다. 문장 중간에서 끝나면 싣지 않는다 — 단,
   "원문 일부" 대상(data-partial, content_completeness.partial_detail_notice 판정)은
   고지와 원문 PDF 링크를 붙여 싣는다. 실패는 전부 삼킨다(표제·원문 링크는 그대로). */
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
  var cfg = document.getElementById("fd-detail-cfg");
  if (!cfg || typeof fetch !== "function") return;
  var url = (cfg.getAttribute("data-url") || "").replace(/\/+$/, "");
  var key = cfg.getAttribute("data-key") || "";
  var docId = cfg.getAttribute("data-doc") || "";
  var pdf = cfg.getAttribute("data-pdf") || "";
  if (!url || !key || !docId) return;
  var partial = Object.create(null);
  (cfg.getAttribute("data-partial") || "").split(/\s+/).forEach(function (id) { if (id) partial[id] = true; });
  var isEn = (document.documentElement.lang || "ko") !== "ko";
  // content_completeness._SENTENCE_END_RE 와 같은 규칙(tests/test_partial_detail_map.py 가 대조).
  var SENT_END = /[.!?][)"'”’]?\s*$/;

  // 지적 id(기본키)로 묻는다 — findings 에는 document_id 색인이 없어 문서 id 로 거르면 공개 키의
  // 3초 제한에 걸린다(실측 57014). 화면에 그려진 지적만 묻는 것이기도 하다.
  var ids = [];
  document.querySelectorAll(".fd-item[id]").forEach(function (n) {
    if (/^finding-[0-9a-f]+$/.test(n.id)) ids.push(n.id);
  });
  if (!ids.length) return;
  var q = url + "/rest/v1/findings?select=finding_id,detail_text,detail_text_ko" +
    "&finding_id=in.(" + ids.join(",") + ")&detail_text=not.is.null";
  fetch(q, { headers: { apikey: key, Authorization: "Bearer " + key } })
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (rows) {
      if (!Array.isArray(rows)) return;
      rows.forEach(function (row) {
        var fid = row && row.finding_id;
        var orig = String((row && row.detail_text) || "").trim();
        var ko = String((row && row.detail_text_ko) || "").trim();
        var text = isEn ? orig : (ko || "");
        if (!fid || !orig || !text) return;
        var art = document.getElementById(fid);
        if (!art || art.querySelector(".fd-detail")) return;
        var whole = SENT_END.test(orig);
        if (!whole && !partial[fid]) return;
        var box = document.createElement("details");
        box.className = "fd-detail";
        var sum = document.createElement("summary");
        sum.textContent = _t("구체 내용");
        box.appendChild(sum);
        var p = document.createElement("p");
        p.textContent = text;
        box.appendChild(p);
        if (!whole) {
          var note = document.createElement("p");
          note.className = "fd-detail-note";
          note.appendChild(document.createTextNode(_t("원문 일부 — 전체는 원문 PDF 에서 확인") + " "));
          if (/^https?:\/\//.test(pdf)) {
            var a = document.createElement("a");
            a.href = pdf; a.target = "_blank"; a.rel = "noopener";
            a.textContent = _t("원문 PDF");
            note.appendChild(a);
          }
          box.appendChild(note);
        }
        art.appendChild(box);
      });
    })
    .catch(function () {});
})();
