/* 483 문서 페이지 '구체 내용' (UI 2단 2026-11-12, 지식 그래프 1단계 화면).
   지적 표제 아래에 상세 서술("Specifically, …")을 접어서 붙인다. 값은 findings 의
   detail_text(원문)·detail_text_ko(국문) — 공개 키로 읽는 표 단위 권한 그대로(RLS 동일).
   화면 규칙(11-12 대표 결정): 상세는 끝맺음과 무관하게 싣고, 끝에 붙은 483 양식 바닥글 잡음만
   화면에서 걷는다(trimFormFooter, 저장값 불변). "원문 일부" 대상(data-partial,
   content_completeness.partial_detail_notice 판정)은 고지와 원문 PDF 링크를 붙인다.
   실패는 전부 삼킨다(표제·원문 링크는 그대로). */
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
  // <form-footer-trim>
  // [11-12 대표 결정] 상세는 끝맺음과 무관하게 싣는다. 대신 **화면에서만** 끝에 붙은 483 양식
  // 바닥글 잡음을 걷는다(저장값 불변). 규칙은 끝에 붙은 것만, 남는 본문이 40자 미만이면 걷지 않는다.
  // 고정값: tests/test_partial_detail_map.py (걷는 예·걷으면 안 되는 예).
  var FOOTER_RULES = [
    ["fda_rep", /(?:\bTHIS\s+DOCUMENT\s+LISTS\s+)?(?:\bOBSERVATIONS?\s+MAD\S*\s+BY\s+\S+\s+)?\bFDA\s+R\S{0,3}PRESENTATIVE[\s\S]{0,120}$/i],
    ["fda_rep", /\uc774\s*\ubb38\uc11c\ub294[^.]{0,40}FDA\s*(?:\ub2f4\ub2f9\uc790|\ub300\ud45c)[\s\S]{0,90}$/],
    ["doc_lists", /(?:\b\w{2,4}\s+)?\bDOCUMENT\s+LISTS?\b[\s\S]{0,60}$/i],
    ["signature", /\bEMPL\S{2,5}\(?S\)?\s*SIGNAT[\s\S]{0,150}$/i],
    ["see_reverse", /\bSEE\s+REVERSE\s*OF\s*THIS\s+PAGE[\s\S]{0,40}$/i],
    ["address", /\b(?:NAME\s+AN\S\s+TITLE\s+OF\s+IND\S*|TITLE\s+OF\s+IND\S*|TO\s+WHOM\s+REPORT\s+ISSUED|F\s?IRM\s?NAME|STREET\s+AD\S{3,5}|C\S{1,3}Y[\s.,~]*(?:AND\s+)?STA\S{0,3}[\s.,~]+(?:\(?Z\S{1,2}P|\S{0,4}\s*<?\.?\.?ODE)|COUNTRY\s+TYPE|TYPE\s+EST\S*\s+INSPECTED)[\s\S]{0,320}$/i],
    ["district", /\b(?:\S{0,3}ST\S{0,2}RICT\s?(?:OFFICE\s+)?ADD?RESS(?:\s+AND\s+PHONE\s+NUMBER)?|FE\S?\s?I\s?NUMBER|Industry\s+Information:|\(\d{3}\)\s?\d{3}-\s?\d{4}\s+Fax)[\s\S]{0,170}$/i],
    ["district", /(?:\bDEP\s?ART[\s\S]{0,14}?)?(?:\S{0,8}\s+)?\bOF\s+[HI]{1,2}EAL\s?TH\s+A\S{1,3}\s+H\s?UMA\S{0,4}\s+SER\s?VICES[\s\S]{0,140}$/i],
    ["page_mark", /\bPAGE\s+\S{1,3}\s+OF\s*\S{0,3}\s+PAGES?\b[\s\S]{0,60}$/i],
    ["system_heading", /([.!?"'”)\]])\s+(?:FACILIT\S*|EQUIPMENT|PRODUCTION|LABORATOR\S*|MATERIALS?|PACKAGING|LABELING|QUALITY|AND|&)(?:[\s.]*(?:FACILIT\S*|EQUIPMENT|PRODUCTION|LABORATOR\S*|MATERIALS?|PACKAGING|LABELING|QUALITY|AND|&))*[\s.]*SYSTEMS?(?:[\s.]+(?:FACILIT\S*|EQUIPMENT|PRODUCTION|LABORATOR\S*|MATERIALS?|PACKAGING|LABELING|QUALITY|AND|&)(?:[\s.]*(?:FACILIT\S*|EQUIPMENT|PRODUCTION|LABORATOR\S*|MATERIALS?|PACKAGING|LABELING|QUALITY|AND|&))*[\s.]*SYSTEMS?)*\s*(?:\([^)]{0,24}\))?\s*$/i],
    ["system_heading", /([.!?"'\u201d)\]\ub2e4])\s+(?:(?:FACILIT\S*|EQUIPMENT|PRODUCTION|LABORATOR\S*|MATERIALS?|PACKAGING|LABELING|QUALITY|AND|&)(?:[\s.]*(?:FACILIT\S*|EQUIPMENT|PRODUCTION|LABORATOR\S*|MATERIALS?|PACKAGING|LABELING|QUALITY|AND|&))*[\s.]*SYSTEMS?\s*)?(?:\ud488\uc9c8|\uc2dc\uc124|\uc124\ube44|\uc7a5\ube44|\ud3ec\uc7a5|\ub77c\ubca8\ub9c1|\ud45c\uc2dc|\uc2dc\ud5d8\uc2e4|\uc2e4\ud5d8\uc2e4|\ubb3c\uc9c8|\uc790\uc7ac|\uc6d0\ub8cc|\uc0dd\uc0b0|\uc81c\uc870|\ubc0f|&|,|\u00b7)(?:\([A-Za-z &.]{2,40}\))?(?:\s*(?:\ud488\uc9c8|\uc2dc\uc124|\uc124\ube44|\uc7a5\ube44|\ud3ec\uc7a5|\ub77c\ubca8\ub9c1|\ud45c\uc2dc|\uc2dc\ud5d8\uc2e4|\uc2e4\ud5d8\uc2e4|\ubb3c\uc9c8|\uc790\uc7ac|\uc6d0\ub8cc|\uc0dd\uc0b0|\uc81c\uc870|\ubc0f|&|,|\u00b7)(?:\([A-Za-z &.]{2,40}\))?)*\s*\uc2dc\uc2a4\ud15c(?:\s*\([A-Za-z &.]{3,60}\))?\s*$/i],
    ["investigator", /([.!?])\s+[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]*){0,3},\s*Invest\s?igator,?\s*$/],
    ["stray_see", /([.!?\]])[\s\-–—·•.,~_|=*wm]*\s?[$S]EE\.?\s*$/],
    ["trailing_junk", /([.!?\]])[\s\-–—·•‘’'"~,_|=]+$/]
  ];
  var FOOTER_MIN_BODY = 40;
  var TAIL_SENT_END = /[a-z0-9)\]\uac00-\ud7a3]\s*[.!?]+["'\u201d\u2019)]?\s*$/;
  var ADDR_LAST_LABEL = /INSPECT\S*|CO\S{0,3}NT\S{0,3}Y\b|ISS\S{0,3}E\S?D?\b/gi;
  var PROSE_RUN = /(?:\b[a-z]{3,}\b[\s,]+){3}\b[a-z]{3,}\b/;
  function addressTailOk(removed) {
    var last = -1, m;
    ADDR_LAST_LABEL.lastIndex = 0;
    while ((m = ADDR_LAST_LABEL.exec(removed))) last = m.index + m[0].length;
    var tail = last >= 0 ? removed.slice(last) : removed;
    return tail.length <= 90 && !PROSE_RUN.test(removed);
  }
  function trimFormFooter(text) {
    var t = String(text || "").replace(/\s+$/, ""), cuts = [];
    for (var round = 0; round < 4; round++) {
      var changed = false;
      for (var i = 0; i < FOOTER_RULES.length; i++) {
        var m = FOOTER_RULES[i][1].exec(t);
        if (!m) continue;
        // 앞 문장의 끝 부호(캡처 1)는 남긴다.
        var keep = m.index + (m[1] && m[0].indexOf(m[1]) === 0 ? m[1].length : 0);
        var rest = t.slice(0, keep).replace(/\s+$/, "");
        if (rest.length < FOOTER_MIN_BODY || rest === t) continue;
        // 걷을 꼬리가 문장으로 끝나면 바닥글이 아니라 본문이 뒤에 이어진 것이다 — 걷지 않는다
        // (실측: 주소 칸 뒤에 "…dated October 31, 2022." 같은 본문이 이어진 상세가 있다).
        if (FOOTER_RULES[i][0] !== "trailing_junk" && TAIL_SENT_END.test(t.slice(keep))) continue;
        // 주소 칸은 양식 머리 칸이 쪽 넘김으로 본문 **사이**에 끼는 일이 있다(실측: 칸 뒤에 제품 목록·
        // "This failure also impacts…" 가 이어짐). 마지막 칸 표지 뒤 꼬리가 짧고 산문이 아닐 때만 걷는다.
        if (FOOTER_RULES[i][0] === "address" && !addressTailOk(t.slice(keep))) continue;
        if (FOOTER_RULES[i][0] === "district" && PROSE_RUN.test(t.slice(keep))) continue;
        t = rest; cuts.push(FOOTER_RULES[i][0]); changed = true;
      }
      if (!changed) break;
    }
    return { text: t, cuts: cuts };
  }
  // </form-footer-trim>

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
        // [11-12 대표 결정] 끝맺음이 없어도 싣는다. 바닥글 잡음은 화면에서만 걷는다.
        text = trimFormFooter(text).text;
        if (!text) return;
        var box = document.createElement("details");
        box.className = "fd-detail";
        var sum = document.createElement("summary");
        sum.textContent = _t("구체 내용");
        box.appendChild(sum);
        var p = document.createElement("p");
        p.textContent = text;
        box.appendChild(p);
        if (!whole && partial[fid]) {
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
