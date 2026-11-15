/* [전역 검색 2026-11-12 · 대표 확정안 3] 검색 결과 화면(/search/?q=) — 정적·무의존(vanilla).
 *  1) 가장 가까운 답 한 칸: assets/lookup*.json(빌드 파생 — 용어·업체·조항)에서 이름 일치로
 *     한 건을 고른다. 한국어 품질: 공백·기호 무시 비교, 한글 초성 검색(ㅊㅈㅅ → 청정실),
 *     영문·약어·동의어 일치. 비용 0(정적 파일).
 *  2) 지적 문장: findings_search RPC 본문 전용 모드(p_text_only). 용어가 답이면 그 용어의
 *     사례 검색어(용어 페이지의 사례 수와 같은 기준)로, 초성 검색이면 답의 이름으로 찾는다.
 *     첫 호출이 서버 시간 제한(57014)에 걸리는 일이 있어 간격을 두고 두 번 더 시도하고,
 *     그래도 실패하면 '다시 시도' 버튼을 낸다(11-12 라이브: 첫 검색 두 번 연속 실패 실측).
 *  3) 필터 두 묶음(기관·문서 종류)은 응답 집계로 그리고, 나머지 조건은 '상세 필터'(기존
 *     지적 문장 검색 화면)로 넘긴다. 값은 전부 textContent 로 넣는다(innerHTML 미사용). */
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
  var _isEn = (typeof document !== "undefined"
    && (document.documentElement.lang || "ko") !== "ko");
  var _HANGUL = /[가-힣]/;
  var _bodyText = function (row) {
    var ko = String((row && row.finding_text_ko) || "").trim();
    var orig = String((row && row.finding_text) || "").trim();
    return _isEn ? (orig || ko) : (ko || orig);
  };
  var _altText = function (row) {
    var ko = String((row && row.finding_text_ko) || "").trim();
    var orig = String((row && row.finding_text) || "").trim();
    if (!ko || !orig) return "";
    if (_isEn && _HANGUL.test(orig)) return "";
    return _isEn ? ko : orig;
  };
  var _DOC_PAGES = null;
  var _docPagesPromise = null;
  function _fetchDocPages(assetRoot) {
    if (_docPagesPromise) return _docPagesPromise;
    _docPagesPromise = fetch(assetRoot + "assets/doc-pages.json")
      .then(function (r) {
        if (!r.ok) throw new Error("doc-pages " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || data.schema !== "grm-doc-pages/v1" ||
            !Array.isArray(data.document_ids)) return null;
        var map = {};
        data.document_ids.forEach(function (id) { if (id) map[id] = id; });
        var odd = data.slug_by_document_id;
        if (odd && typeof odd === "object") {
          Object.keys(odd).forEach(function (id) { if (odd[id]) map[id] = odd[id]; });
        }
        if (_isEn && Array.isArray(data.ko_only_document_ids)) {
          data.ko_only_document_ids.forEach(function (id) { delete map[id]; });
        }
        _DOC_PAGES = map;
        return map;
      })
      .catch(function () { return null; });
    return _docPagesPromise;
  }
  function _docPageHref(root, documentId) {
    var id = String(documentId || "");
    var slug = (id && _DOC_PAGES) ? _DOC_PAGES[id] : "";
    if (!slug) return "";
    return root + "findings/doc/" + encodeURIComponent(slug) + "/";
  }
  var cfg = document.getElementById("grm-search-cfg");
  if (!cfg) return;
  var URL_ = (cfg.getAttribute("data-url") || "").replace(/\/$/, "");
  var KEY = cfg.getAttribute("data-key") || "";
  var ROOT = cfg.getAttribute("data-root") || "";
  var ASSET_ROOT = cfg.getAttribute("data-asset-root") || "";
  var LOOKUP = cfg.getAttribute("data-lookup") || "";

  var $ = function (id) { return document.getElementById(id); };
  var input = $("sr-q"), bestEl = $("sr-best"), hitsEl = $("sr-hits"), noteEl = $("sr-note");
  var hitsH = $("sr-hits-h"), allA = $("sr-all"), advA = $("sr-adv");
  var moreEl = $("sr-more"), pillsEl = $("sr-pills");
  var fAgency = $("sr-f-agency"), fSource = $("sr-f-source"), fPeriod = $("sr-f-period");
  var goBtn = $("sr-go"), progEl = $("sr-prog");

  // ── [검색 로딩 2026-11-15] 진행 표시 ───────────────────────────────────────
  // 진행 중: 단추 글자 '검색 중' + 입력창 아래 진행선. 0.4초가 넘으면 결과 자리에 자리 표시
  // 세 줄(그보다 빨리 오면 자리 표시 없이 바로 결과 — 깜빡임 방지). 단추는 잠그지 않는다
  // (느릴 때 검색어를 고쳐 다시 보낼 수 있어야 한다). runSeq = 늦게 온 옛 응답을 버리는 표.
  var SLOW_MS = 400, slowTimer = null, runSeq = 0;
  function setBusy(on) {
    if (goBtn) {
      goBtn.textContent = on ? _t("검색 중") : _t("검색");
      goBtn.classList.toggle("is-busy", on);
    }
    if (progEl) progEl.classList.toggle("on", on);
    hitsEl.setAttribute("aria-busy", on ? "true" : "false");
    clearTimeout(slowTimer);
    if (on) {
      slowTimer = setTimeout(function () {
        if (hitsEl.childNodes.length) return;
        for (var i = 0; i < 3; i++) {
          var li = el("li", "sr-sk");
          li.setAttribute("aria-hidden", "true");
          li.appendChild(el("span")); li.appendChild(el("span")); li.appendChild(el("span"));
          hitsEl.appendChild(li);
        }
      }, SLOW_MS);
    }
  }
  function noteBox(kind) {
    noteEl.classList.toggle("is-box", !!kind);
    noteEl.classList.toggle("is-err", kind === "err");
  }

  var params = new URLSearchParams(location.search);
  var Q = (params.get("q") || "").trim().slice(0, 120);
  var PERIODS = { "1y": 12, "3y": 36 };
  var state = { agency: params.get("agency") || "", source: params.get("src") || "",
    period: PERIODS[params.get("period")] ? params.get("period") : "" };
  var periodUnsupported = false;
  if (input) input.value = Q;

  var AGENCY_LABEL = {
    FDA: "FDA", MFDS: _t("식약처"), EMA: "EU", MHRA: _t("영국 MHRA"),
    HC: _t("캐나다"), PMDA: _t("일본 PMDA"), WHO: "WHO"
  };
  var SOURCE_LABEL = {
    "FDA 483": "FDA 483", "FDA Warning Letter": _t("FDA 경고서한"),
    "MFDS": _t("식약처 실사"), "EU GMP NCR (EudraGMDP)": _t("EU GMP 부적합 보고서"),
    "MHRA GMP NCR": _t("영국 MHRA"), "Health Canada Inspection": _t("캐나다 실사")
  };
  var PERIOD_LABEL = { "1y": _t("최근 1년"), "3y": _t("최근 3년") };
  var label = function (map, v) { return Object.prototype.hasOwnProperty.call(map, v) ? map[v] : v; };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function fmt(n) { return Number(n || 0).toLocaleString(_isEn ? "en-US" : "ko-KR"); }

  // ── 정규화·초성 ────────────────────────────────────────────────────────────
  var CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"; /* i18n-ignore */
  function compact(s) {
    return String(s || "").toLowerCase().normalize("NFC").replace(/[\s\-_·.,()\[\]{}'"\/:;§]+/g, "");
  }
  function choseong(s) {
    var out = "";
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0xac00 && c <= 0xd7a3) out += CHO.charAt(Math.floor((c - 0xac00) / 588));
      else if (/[ㄱ-ㅎ]/.test(s.charAt(i))) out += s.charAt(i);
    }
    return out;
  }
  var IS_CHO = /^[ㄱ-ㅎ\s]+$/;

  function scoreNames(names, q, qc, isCho) {
    var best = 0;
    for (var i = 0; i < names.length; i++) {
      var n = names[i];
      if (!n) continue;
      var sc = 0;
      if (isCho) {
        var nc = choseong(n);
        if (!nc) continue;
        if (nc === qc) sc = 90; else if (nc.indexOf(qc) === 0) sc = 70;
        else if (qc.length >= 2 && nc.indexOf(qc) > 0) sc = 50;
      } else {
        var cn = compact(n);
        if (!cn) continue;
        if (cn === qc) sc = 100;
        else if (cn.indexOf(qc) === 0) sc = 80 - Math.min(10, cn.length - qc.length) / 2;
        else if (qc.length >= 2 && cn.indexOf(qc) > 0) sc = 60 - Math.min(10, cn.length - qc.length) / 2;
        else if (cn.length >= 2 && qc.indexOf(cn) >= 0 && cn.length >= qc.length * 0.6) sc = 55;
      }
      if (i === 0) sc += 2; // 표제어 우선(동의어보다)
      if (sc > best) best = sc;
    }
    return best;
  }

  function rankEntities(lk, q) {
    var isCho = IS_CHO.test(q);
    var qc = isCho ? q.replace(/\s+/g, "") : compact(q);
    if (!qc) return [];
    var out = [];
    function push(kind, item, names, weight) {
      var s = scoreNames(names, q, qc, isCho);
      if (s >= (isCho ? 50 : 55)) out.push({ kind: kind, item: item, score: s + weight });
    }
    (lk.terms || []).forEach(function (t) {
      push("term", t, [t.n].concat(String(t.m || "").split(" | ")), Math.min(3, (t.c || 0) / 200));
    });
    (lk.firms || []).forEach(function (f) {
      push("firm", f, [f.n].concat(String(f.m || "").split(" | ")), Math.min(3, (f.f || 0) / 30));
    });
    var digits = /\d/.test(q);
    (lk.clauses || []).forEach(function (c) {
      push("clause", c, [c.n].concat(String(c.m || "").split(" | ")), digits ? 4 : 0);
    });
    out.sort(function (a, b) { return b.score - a.score; });
    return out;
  }

  function entityHref(e) {
    if (e.kind === "term") return ROOT + "glossary/" + encodeURIComponent(e.item.id) + "/";
    if (e.kind === "firm") return ROOT + "findings/firm/" + encodeURIComponent(e.item.slug) + "/";
    return ROOT + "findings/clause/" + encodeURIComponent(e.item.slug) + "/";
  }
  var KIND_LABEL = { term: _t("용어"), firm: _t("업체"), clause: _t("조항") };

  function renderBest(e) {
    bestEl.textContent = "";
    if (!e) { bestEl.hidden = true; return; }
    var it = e.item;
    bestEl.appendChild(el("span", "sr-kind", KIND_LABEL[e.kind]));
    var a = el("a", "sr-best-t", it.n);
    a.href = entityHref(e);
    if (e.kind === "term" && it.s) a.appendChild(el("span", "", it.s));
    if (e.kind === "clause" && it.t) a.appendChild(el("span", "", it.t));
    bestEl.appendChild(a);
    if (e.kind === "term") {
      if (it.e) bestEl.appendChild(el("p", "", it.e));
      var bits = [];
      if (it.g && it.g.length) bits.push(_t("근거 조항") + " " + it.g.join(", "));
      if (bits.length) bestEl.appendChild(el("span", "sr-meta", bits.join(" · ")));
    } else if (e.kind === "firm") {
      var ag = (it.a || []).map(function (x) { return label(AGENCY_LABEL, x); }).join(", ");
      bestEl.appendChild(el("span", "sr-meta",
        _t("공개 문서 {n}건", { n: fmt(it.d) }) + " · " + _t("지적 {n}건", { n: fmt(it.f) }) +
        (ag ? " · " + ag : "") + (it.l ? " · " + _t("최근 공개 {date}", { date: it.l }) : "")));
    } else {
      bestEl.appendChild(el("span", "sr-meta",
        _t("공개 문서 {n}건", { n: fmt(it.d) }) + " · " + _t("지적 {n}건", { n: fmt(it.f) })));
    }
    bestEl.hidden = false;
  }

  function renderPills(list) {
    pillsEl.textContent = "";
    var seen = {};
    list.forEach(function (e) {
      var href = entityHref(e);
      if (seen[href]) return;
      seen[href] = 1;
      if (Object.keys(seen).length > 10) return;
      var a = el("a", "", e.item.n);
      a.href = href;
      a.appendChild(el("small", "", KIND_LABEL[e.kind]));
      pillsEl.appendChild(a);
    });
    moreEl.hidden = !pillsEl.childNodes.length;
  }

  // 기간: 최근 n개월 = 이번 달 포함 n개월 → 서버에는 월 범위 "YYYY-MM.."(153)로 보낸다.
  function periodRange(code) {
    var n = PERIODS[code] || 0;
    if (!n) return "";
    var d = new Date(), y = d.getFullYear(), m = d.getMonth() + 1 - (n - 1);
    while (m <= 0) { m += 12; y -= 1; }
    return y + "-" + (m < 10 ? "0" : "") + m + "..";
  }

  // ── 지적 문장 ──────────────────────────────────────────────────────────────
  function rpc(q, textOnly, attempt) {
    var body = {
      // 쪽당 24건 = 서버 캐시(findings_search_cache, 086)의 키와 같은 값 — 용어 사례 검색어는
      // 미리 계산된 답을 받는다(8건이면 키가 달라 매번 새로 계산, 3초 제한에 걸렸다). 화면은 10줄만 쓴다.
      p_q: q, p_text_only: !!textOnly, p_page: 1, p_docs_per_page: 24,
      p_agency: state.agency, p_source: state.source,
      p_orig_lang: _isEn ? "en" : ""
    };
    // 기간을 고르지 않으면 p_month 를 아예 안 보낸다 — 캐시 키(086)가 종전과 같다.
    var period = periodUnsupported ? "" : periodRange(state.period);
    if (period) body.p_month = period;
    return fetch(URL_ + "/rest/v1/rpc/findings_search", {
      method: "POST",
      headers: { apikey: KEY, Authorization: "Bearer " + KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_search " + r.status);
      return r.json();
    }).then(function (d) {
      if (!d || !Array.isArray(d.documents) || !d.totals) throw new Error("shape");
      return d;
    }).catch(function (err) {
      var a = attempt || 0;
      if (a < 2) {
        return new Promise(function (res) { setTimeout(res, 700 * (a + 1)); })
          .then(function () { return rpc(q, textOnly, a + 1); });
      }
      throw err;
    });
  }

  function highlight(p, text, tokens) {
    var lower = text.toLowerCase(), i = 0;
    var marks = [];
    tokens.forEach(function (t) {
      var from = 0, k;
      while (t && (k = lower.indexOf(t, from)) >= 0) { marks.push([k, k + t.length]); from = k + t.length; }
    });
    marks.sort(function (a, b) { return a[0] - b[0]; });
    marks.forEach(function (m) {
      if (m[0] < i) return;
      p.appendChild(document.createTextNode(text.slice(i, m[0])));
      p.appendChild(el("mark", "", text.slice(m[0], m[1])));
      i = m[1];
    });
    p.appendChild(document.createTextNode(text.slice(i)));
  }

  function explorerHref(q, textOnly) {
    var u = new URLSearchParams();
    u.set("q", q);
    if (textOnly) u.set("text", "1");
    if (state.agency) u.set("agency", state.agency);
    if (state.source) u.set("src", state.source);
    return ROOT + "findings/index.html?" + u.toString();
  }

  function renderFilters(d, rerun) {
    var f = (d && d.facets) || {};
    function group(fs, key, rows, map) {
      Array.prototype.slice.call(fs.querySelectorAll("label")).forEach(function (n) { n.remove(); });
      var opts = [{ v: "", c: null }].concat((rows || []).map(function (r) { return { v: r.v, c: r.c }; }));
      if (state[key] && !opts.some(function (o) { return o.v === state[key]; })) opts.push({ v: state[key], c: 0 });
      opts.forEach(function (o) {
        var lab = el("label");
        var r = document.createElement("input");
        r.type = "radio"; r.name = "sr-" + key; r.value = o.v; r.checked = state[key] === o.v;
        r.addEventListener("change", function () { state[key] = o.v; syncUrl(); rerun(); });
        lab.appendChild(r);
        lab.appendChild(document.createTextNode(o.v ? label(map, o.v) : _t("전체")));
        if (o.c != null) lab.appendChild(el("small", "", fmt(o.c)));
        fs.appendChild(lab);
      });
    }
    group(fAgency, "agency", f.by_agency, AGENCY_LABEL);
    group(fSource, "source", f.by_source, SOURCE_LABEL);
    // 기간 칸: 월별 분포(by_month — 월 조건을 안 거는 패싯)에서 각 기간의 건수를 더한다.
    if (fPeriod) {
      fPeriod.hidden = periodUnsupported;
      var months = f.by_month || [];
      var sumFrom = function (from) {
        return months.reduce(function (n, r) { return n + (!from || r.v >= from ? (r.c || 0) : 0); }, 0);
      };
      group(fPeriod, "period", ["1y", "3y"].map(function (k) {
        return { v: k, c: sumFrom(periodRange(k).slice(0, 7)) };
      }), PERIOD_LABEL);
    }
  }

  function syncUrl() {
    var u = new URLSearchParams();
    if (Q) u.set("q", Q);
    if (state.agency) u.set("agency", state.agency);
    if (state.source) u.set("src", state.source);
    if (state.period) u.set("period", state.period);
    try { history.replaceState(null, "", location.pathname + (u.toString() ? "?" + u.toString() : "")); } catch (e) {}
  }

  // textOnly: 본문만(기본) / 업체명·문서 정보까지(업체가 답이거나 본문 일치가 0건일 때).
  function runHits(sq, basisNote, textOnly) {
    var seq = ++runSeq;
    hitsEl.textContent = "";
    noteEl.hidden = false;
    noteBox("");
    noteEl.textContent = _t("지적 문장 검색 중");
    setBusy(true);
    allA.href = explorerHref(sq, textOnly);
    advA.href = explorerHref(sq, textOnly);
    var rerun = function () { runHits(sq, basisNote, textOnly); };
    if (!URL_ || !KEY) { setBusy(false); noteEl.textContent = _t("지적 문장 검색을 사용할 수 없는 환경입니다."); return; }
    var tokens = sq.toLowerCase().split(/\s+/).filter(function (t) { return t.length >= 1; });
    Promise.all([rpc(sq, textOnly), _fetchDocPages(ASSET_ROOT)]).then(function (res) {
      if (seq !== runSeq) return;
      var d = res[0];
      // 옛 함수(153 적용 전)는 월 범위를 한 달 값으로 읽어 0건을 낸다. 월별 분포에는 그 기간
      // 행이 있는데 결과가 0 이면 기간 축을 끄고 다시 부른다(화면이 먼저 배포돼도 안전).
      var pr = periodRange(state.period).slice(0, 7);
      if (pr && !d.totals.findings && ((d.facets && d.facets.by_month) || []).some(function (r) { return r.v >= pr && r.c; })) {
        periodUnsupported = true;
        state.period = "";
        syncUrl();
        rerun();
        return;
      }
      if (textOnly && !d.totals.findings && !state.agency && !state.source && !state.period) {
        runHits(sq, basisNote, false);
        return;
      }
      setBusy(false);
      hitsEl.textContent = "";
      hitsH.textContent = _t("지적 문장 {n}건", { n: fmt(d.totals.findings) });
      renderFilters(d, rerun);
      var rows = [];
      d.documents.forEach(function (doc) {
        (doc.findings || []).forEach(function (f) { rows.push({ doc: doc, f: f }); });
      });
      // 먼저 보이는 문장은 짧고 온전한 것(대표 확정안 3) — 가림 표기((b)(4))·판독 누락·긴 문단은
      // 뒤로 보낸다. 같은 묶음 안에서는 서버 순서(최신순)를 지킨다.
      var rough = function (row) {
        var t = _bodyText(row.f);
        // 판독이 깨진 OCR 문장(`~r> (.iJ)` 같은 기호 잔해)도 가림 표기와 같이 뒤로.
        var junk = (t.match(/[~<>{}|^]/g) || []).length;
        return (/\(b\)\s*\(\d\)|판독 누락|�|\(\.\w+\)/.test(t) || junk >= 2 ? 2 : 0) +
          (t.length > 260 ? 1 : 0);
      };
      rows = rows.map(function (r, i) { return { r: r, i: i, k: rough(r) }; })
        .sort(function (a, b) { return a.k - b.k || a.i - b.i; })
        .map(function (x) { return x.r; }).slice(0, 10);
      if (!rows.length) {
        // 0건: 무엇이 없는지 적고, 바로 가 볼 곳 두 군데만 둔다.
        noteBox("zero");
        noteEl.textContent = _t("일치하는 지적 문장이 없습니다. 띄어쓰기나 영문 표기를 바꾸면 결과가 달라질 수 있습니다.");
        var acts = el("span", "sr-acts");
        [[_t("업체"), "findings/firm/"], [_t("용어사전"), "glossary/"]].forEach(function (x) {
          var a = el("a", "", x[0]);
          a.href = ROOT + x[1];
          acts.appendChild(a);
        });
        noteEl.appendChild(acts);
        return;
      }
      noteEl.hidden = !basisNote;
      if (basisNote) noteEl.textContent = basisNote;
      rows.forEach(function (row) {
        var li = el("li", "sr-hit");
        var p = el("p");
        highlight(p, _bodyText(row.f), tokens);
        li.appendChild(p);
        var meta = el("span", "sr-meta");
        var bits = [label(SOURCE_LABEL, row.doc.source || row.f.source || ""), row.doc.firm_name || "", row.doc.published_date || ""];
        meta.appendChild(document.createTextNode(bits.filter(Boolean).join(" · ")));
        var href = _docPageHref(ROOT, row.doc.document_id);
        var a = el("a", "", href ? _t("문서 보기") : _t("원문"));
        a.href = href || row.doc.evidence_url || "#";
        if (!href) { a.rel = "noopener"; a.target = "_blank"; }
        if (a.href && a.getAttribute("href") !== "#") { meta.appendChild(document.createTextNode(" · ")); meta.appendChild(a); }
        li.appendChild(meta);
        hitsEl.appendChild(li);
      });
    }).catch(function () {
      if (seq !== runSeq) return;
      setBusy(false);
      hitsEl.textContent = "";
      noteEl.hidden = false;
      noteBox("err");
      noteEl.textContent = _t("지적 문장을 불러오지 못했습니다. 잠시 뒤 다시 시도해 주십시오.") + " ";
      var b = el("button", "sr-retry", _t("다시 시도"));
      b.type = "button";
      b.addEventListener("click", rerun);
      noteEl.appendChild(b);
    });
  }

  if (!Q) { if (input) input.focus(); return; }
  document.title = Q + " · " + document.title;

  fetch(LOOKUP).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    .then(function (lk) {
      var ranked = (lk && lk.schema === "grm-lookup/v1") ? rankEntities(lk, Q) : [];
      var best = ranked.length ? ranked[0] : null;
      renderBest(best);
      var more = ranked.slice(1, 12);
      if (best && best.kind !== "firm") {
        (best.item.r || []).forEach(function (r) {
          var t = (lk.terms || []).filter(function (x) { return x.id === r.id; })[0];
          if (t) more.push({ kind: "term", item: t, score: 0 });
        });
      }
      renderPills(more);
      // 지적 문장 검색어: 초성이면 답의 이름, 용어가 정확히 맞으면 용어 페이지 사례 기준.
      var sq = Q, note = "";
      if (IS_CHO.test(Q)) {
        if (best) {
          sq = (best.kind === "term" && best.item.q) ? best.item.q : best.item.n;
          note = _t("검색 기준: {q}", { q: sq });
        }
      } else if (best && best.kind === "term" && best.score >= 100 && best.item.q) {
        sq = best.item.q;
        if (compact(sq) !== compact(Q)) note = _t("검색 기준: {q} (용어 페이지 사례와 같은 기준)", { q: sq });
      }
      runHits(sq, note, !(best && best.kind === "firm"));
    });
})();
