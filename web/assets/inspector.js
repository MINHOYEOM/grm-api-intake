/* GRM 실사관 프로파일(FDA 483 서명 실사관 집계) — 정적·클라이언트사이드, 순수 fetch
 * (PostgREST RPC 직접 호출, POST). firm.js 의 미러링이다 — URL 파라미터(?key=inspector_key)
 * 로만 조회하는 단일 실사관 전용 페이지.
 *
 * ★코호트 게이트(서버 계약) — findings_inspector_profile(p_inspector_key) 는 문서 5건
 * 이상 확인된 실사관만 데이터를 반환하고, 코호트 미달·미존재·키 형식 오류는 전부 null 을
 * 반환한다. firm.js 는 "013 미적용(RPC 404·network 실패)"과 "key 없음/빈 프로파일"을 서로
 * 다른 상태(준비 중 vs 찾을 수 없음)로 구분해 보여주지만, 이 페이지는 **구분하지 않는다** —
 * 코호트 미달 여부 자체가 특정 실사관에 대한 신호가 되는 정보 누출을 막기 위해 key 파라미터
 * 없음/RPC null/fetch 실패를 전부 하나의 "표시할 수 없습니다" 안내로 수렴시킨다(showState
 * 상태가 loading/unavailable/content 세 가지뿐인 이유).
 *
 * ★안전 계약 — findings_inspector_profile RPC 는 집계(count)와 서지 메타만 반환하고
 * finding_text/finding_text_ko 를 어떤 경로로도 내려주지 않는다. 문서 이력에서 "인라인
 * 확장"으로 보여주는 개별 지적사항 원문은 이 RPC 가 아니라 기존 anon REST(`/rest/v1/
 * findings?...&raw_signal_id=eq.X`)로 별도 fetch 한다(firm.js 와 동일 계약) — RLS(003/006)
 * 가 공개 게이트 통과분만 돌려주므로 이 페이지가 원문 접근 게이트를 우회하지 않는다.
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 에 데이터 삽입 금지 — 원문/
 * 업체명/실사관명은 자유 텍스트라 이스케이프 누락 시 XSS 위험, findings.js/firm.js 와
 * 동일 계약).
 *
 * ★범위(의도적 제한, 회귀 금지) — 이 파일은 **단일 실사관 프로파일 렌더만** 한다. 실사관
 * 목록/디렉터리, 실사관 간 순위·비교, "엄격하다/까다롭다" 류 성향 해석 함수는 만들지
 * 않는다(그런 심볼이 생기면 회귀 — web/tests/test_render.py 의 범위 가드 테스트 참조).
 *
 * [동기화 규칙] CATEGORY_LABELS 는 findings.js/trends.js/firm.js 의 동명 상수·grm_findings.
 * FINDING_TAXONOMY 20개 code/label_ko/label_en 과 완전히 일치해야 한다(web/tests/
 * test_render.py 가 web/assets/*.js 전수 글롭으로 대조 테스트를 강제한다).
 */
(function () {
  "use strict";

  var cfg = document.getElementById("grm-inspector-cfg");
  var loadingEl = document.getElementById("ip-loading");
  var unavailableEl = document.getElementById("ip-unavailable");
  var contentEl = document.getElementById("ip-content");
  var nameEl = document.getElementById("ip-name");
  var statsEl = document.getElementById("ip-stats");
  var catEl = document.getElementById("ip-cat");
  var yearEl = document.getElementById("ip-year");
  var docsEl = document.getElementById("ip-docs");
  // [존 재편 2026-08-26] ?key= 없이 들어왔을 때의 조회 랜딩(037 진입 경로 조항 개정 —
  // 템플릿 주석에 근거를 적어 뒀다). 하드 게이트에는 넣지 않는다.
  var lookupEl = document.getElementById("ip-lookup");
  var lookFormEl = document.getElementById("ip-look-form");
  var lookInputEl = document.getElementById("ip-look-input");
  var lookResEl = document.getElementById("ip-look-res");
  if (!cfg || !loadingEl || !unavailableEl || !contentEl || !nameEl ||
      !statsEl || !catEl || !yearEl || !docsEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();
  var root = (cfg.getAttribute("data-root") || "").trim();

  // grm_findings.FINDING_TAXONOMY verbatim(code -> {ko, en}) — findings.js/trends.js/firm.js
  // 의 동명 상수와 동일 복제본(동기화 테스트로 드리프트 차단, 별도 공유 파일로 빼지 않는다
  // — 이 저장소의 확립된 방식).
  var CATEGORY_LABELS = {
    data_integrity: { ko: "데이터 완전성", en: "Data integrity" },
    computer_system_validation: { ko: "컴퓨터화시스템", en: "Computer system validation" },
    documentation_records: { ko: "문서화/기록관리", en: "Documentation and records" },
    aseptic_sterility_assurance: { ko: "무균보증/무균공정", en: "Aseptic processing and sterility assurance" },
    environmental_monitoring: { ko: "환경모니터링", en: "Environmental monitoring" },
    cleaning_validation: { ko: "세척밸리데이션", en: "Cleaning validation" },
    complaint_recall: { ko: "불만/회수", en: "Complaint and recall handling" },
    deviation_capa: { ko: "일탈/CAPA/조사", en: "Deviation, CAPA, and investigation" },
    quality_unit_oversight: { ko: "품질부서 관리감독", en: "Quality unit oversight" },
    qc_lab_controls: { ko: "시험실/품질관리", en: "Laboratory and QC controls" },
    process_validation: { ko: "공정밸리데이션", en: "Process validation" },
    equipment_facility: { ko: "설비/시설", en: "Equipment and facility" },
    material_supplier_control: { ko: "원자재/공급업체 관리", en: "Material and supplier control" },
    contamination_control: { ko: "오염/교차오염 관리", en: "Contamination control" },
    validation_qualification: { ko: "밸리데이션/적격성평가", en: "Validation and qualification" },
    stability_storage: { ko: "안정성/보관", en: "Stability and storage" },
    labeling_packaging: { ko: "표시/포장", en: "Labeling and packaging" },
    regulatory_reporting: { ko: "규제보고/변경관리", en: "Regulatory reporting and change control" },
    training_personnel: { ko: "교육/작업자", en: "Training and personnel" },
    other_quality_system: { ko: "기타 품질시스템", en: "Other quality system" },
  };

  // ★정체성 키 정규화 — findings_inspector_index()/findings_inspector_profile() RPC(서버)와
  // 반드시 동일한 규칙: 소문자 → 마침표(.) 제거 → 공백 연속을 1칸으로 → 앞뒤 공백 제거.
  // 예) "Eileen A. Liu" → "eileen a liu". 이 함수 하나만 이 정규화를 담당한다(호출부가
  // 각자 규칙을 재구현하지 않는다) — findings.js 에도 동일 규칙의 독립 복제본이 있다
  // (별도 정적 자산이라 공유 불가, 계약만 복제).
  function normalizeInspectorKey(name) {
    return String(name || "")
      .toLowerCase()
      .replace(/\./g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function showState(which) {
    loadingEl.hidden = which !== "loading";
    unavailableEl.hidden = which !== "unavailable";
    contentEl.hidden = which !== "content";
    if (lookupEl) lookupEl.hidden = which !== "lookup";
  }

  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") e.textContent = text;
    return e;
  }

  // [firm_name 엔티티 디코드 M5] findings.js/firm.js/trends.js 의 동명 헬퍼와 동일 계약
  // (별도 파일이라 재사용 불가, 계약만 복제) — DB firm_name 에 &amp;/&#039; 가 이미
  // 이스케이프된 채로 저장된 행을 표시 직전(textContent 대입 전)에만 되돌린다.
  function decodeFirmDisplay(s) {
    return String(s || "").replace(/&amp;/g, "&").replace(/&#039;/g, "'");
  }

  // 전 페이지 공용 관례(§ 관례) — 숫자 표기는 toLocaleString("ko-KR").
  function fmtNum(n) {
    return Number(n || 0).toLocaleString("ko-KR");
  }

  function makeClickableRow(node, ariaLabel, onActivate) {
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.setAttribute("aria-label", ariaLabel);
    node.addEventListener("click", onActivate);
    node.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
        ev.preventDefault();
        onActivate();
      }
    });
  }

  // 카테고리 바 클릭 → 검색 페이지 필터 링크(firm.js findingsHref 와 동일 계약 —
  // findings.js 의 URL_KEYS.category_code="cat" 을 그대로 따른다).
  // [맥락 유지 2026-08-26] 카테고리만 넘기면 실사관 조건이 사라져 전체 코퍼스로 떨어진다
  // (검색 RPC 엔 실사관 필터가 없어 구조적으로 표현 불가). 대신 검색 blob 에 실사관
  // 이름이 들어 있으므로(마이그 040) q=이름을 함께 실어 "이 실사관 + 이 카테고리"로
  // 착지시킨다 — findings.js 대시보드 업체 행이 쓰는 것과 같은 관용구(자유 검색 경유)다.
  // 한계: 표기 변형(별칭)으로 서명한 문서는 q 부분일치에서 빠질 수 있다 — 전수는 이
  // 프로파일 화면이 정본이고, 검색 착지 화면엔 q·카테고리 칩이 떠 조건이 그대로 보인다.
  function findingsHref(paramKey, value, qValue) {
    var href = root + "findings/index.html?" + paramKey + "=" + encodeURIComponent(value);
    if (qValue) href += "&q=" + encodeURIComponent(qValue);
    return href;
  }

  function getInspectorKeyParam() {
    if (typeof URLSearchParams === "undefined") return "";
    return (new URLSearchParams(location.search).get("key") || "").trim();
  }

  // ── 스탯 스트립 ──────────────────────────────────────────────────────────
  function buildStat(num, label) {
    var block = el("div", "ip-stat");
    block.appendChild(el("span", "ip-stat-num", num));
    block.appendChild(el("span", "ip-stat-lbl", label));
    return block;
  }

  function renderStats(totals) {
    statsEl.innerHTML = "";
    statsEl.appendChild(buildStat(fmtNum(totals.documents), "실사 문서"));
    statsEl.appendChild(buildStat(fmtNum(totals.findings), "지적"));
    statsEl.appendChild(buildStat(fmtNum(totals.firms), "업체"));
    var period = (totals.first_seen || "?") + " ~ " + (totals.last_seen || "?");
    statsEl.appendChild(buildStat(period, "기간"));
  }

  // ── 카테고리 구성(상위 카테고리 코럴 농도 바) ────────────────────────────────
  function buildCatRow(entry, maxCnt, personName) {
    var a = document.createElement("a");
    a.className = "ip-cat-row";
    a.href = findingsHref("cat", entry.category_code, personName);
    var label = CATEGORY_LABELS[entry.category_code];
    a.appendChild(el("span", "ip-cat-label", label ? label.ko : entry.category_code));
    var track = document.createElement("div");
    track.className = "ip-cat-track";
    var bar = document.createElement("div");
    bar.className = "ip-cat-bar";
    var ratio = maxCnt > 0 ? entry.cnt / maxCnt : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    track.appendChild(bar);
    a.appendChild(track);
    a.appendChild(el("span", "ip-cat-count", fmtNum(entry.cnt) + "건"));
    return a;
  }

  function renderCategories(byCategory, personName) {
    catEl.innerHTML = "";
    if (!byCategory.length) {
      catEl.appendChild(el("p", "ip-empty", "표시할 데이터가 없습니다."));
      return;
    }
    // RPC 가 이미 cnt desc 로 정렬해 반환한다(findings_firm_profile by_category 와 동일
    // 계약) — 재정렬 없음.
    var maxCnt = byCategory[0].cnt || 1;
    byCategory.forEach(function (c) { catEl.appendChild(buildCatRow(c, maxCnt, personName)); });
  }

  // ── 연도 추이(간단 막대) ─────────────────────────────────────────────────
  function renderYears(byYear) {
    yearEl.innerHTML = "";
    if (!byYear.length) {
      yearEl.appendChild(el("p", "ip-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = byYear.reduce(function (m, y) { return Math.max(m, y.cnt); }, 0) || 1;
    var wrap = document.createElement("div");
    wrap.className = "ip-year-bars";
    byYear.forEach(function (y) {
      var col = document.createElement("div");
      col.className = "ip-year-col";
      var barwrap = document.createElement("div");
      barwrap.className = "ip-year-barwrap";
      var bar = document.createElement("div");
      bar.className = "ip-year-bar";
      bar.style.height = Math.max(4, Math.round((y.cnt / maxCnt) * 100)) + "%";
      barwrap.appendChild(bar);
      col.appendChild(barwrap);
      col.appendChild(el("span", "ip-year-lbl", y.year));
      col.appendChild(el("span", "ip-year-count", fmtNum(y.cnt)));
      wrap.appendChild(col);
    });
    yearEl.appendChild(wrap);
  }

  // ── 문서 이력 + 인라인 확장(anon REST, RLS 공개 게이트 통과분만) ────────────────
  var OBS_FIELDS = [
    "finding_id", "category_code", "category_label_ko",
    "finding_text", "finding_text_ko", "cfr_refs", "mfds_refs",
  ];

  function fetchDocObservations(rawSignalId) {
    var cols = OBS_FIELDS.join(",");
    var endpoint = url.replace(/\/$/, "") + "/rest/v1/findings?select=" +
      encodeURIComponent(cols).replace(/%2C/g, ",") +
      "&raw_signal_id=eq." + encodeURIComponent(rawSignalId) +
      "&order=finding_id.asc";
    return fetch(endpoint, {
      headers: { apikey: key, Authorization: "Bearer " + key },
    }).then(function (r) {
      if (!r.ok) throw new Error("findings fetch " + r.status);
      return r.json();
    });
  }

  // 단순화한 국문+원문 details 카드 — findings.js buildCard()/firm.js buildObsCard() 의
  // 본문/원문 접기/refs 규칙을 이 페이지 전용으로 축약한 것(별도 정적 자산이라 함수 재사용
  // 불가, 계약만 복제).
  function buildObsCard(row) {
    var card = el("article", "ip-obs");
    var label = CATEGORY_LABELS[row.category_code];
    var catText = label ? label.ko : (row.category_label_ko || "");
    if (catText) card.appendChild(el("p", "ip-obs-cat", catText));

    var ko = (row.finding_text_ko || "").trim();
    var mainText = ko || row.finding_text || "";
    if (mainText) card.appendChild(el("p", "ip-obs-text", mainText));

    if (ko && row.finding_text) {
      var details = document.createElement("details");
      details.className = "ip-obs-orig";
      var summary = document.createElement("summary");
      summary.textContent = "원문 보기 (영문)";
      details.appendChild(summary);
      details.appendChild(el("p", null, row.finding_text));
      card.appendChild(details);
    }

    var refs = ([]).concat(row.cfr_refs || [], row.mfds_refs || []);
    if (refs.length) {
      var refsWrap = el("div", "ip-obs-refs");
      refs.forEach(function (r) { if (r) refsWrap.appendChild(el("span", "ip-obs-ref", r)); });
      card.appendChild(refsWrap);
    }
    return card;
  }

  function renderDocDetailLoading(container) {
    container.innerHTML = "";
    container.appendChild(el("p", "ip-doc-detail-loading", "불러오는 중…"));
  }

  function renderDocDetailError(container) {
    container.innerHTML = "";
    container.appendChild(el("p", "ip-doc-detail-empty", "지적사항을 불러오지 못했습니다."));
  }

  function renderDocDetail(container, rows) {
    container.innerHTML = "";
    if (!Array.isArray(rows) || !rows.length) {
      container.appendChild(el("p", "ip-doc-detail-empty", "공개된 지적사항이 없습니다."));
      return;
    }
    rows.forEach(function (row) { container.appendChild(buildObsCard(row)); });
  }

  // [업체 프로파일 진입] doc.firm_key(findings_inspector_profile documents[].firm_key)로
  // 업체 프로파일 링크를 만든다 — findings/inspector/index.html 은 findings/firm/index.html
  // 과 같은 findings/ 하위 형제 디렉터리라 rel_root 계산 없이 "../firm/index.html" 상대경로
  // 하나로 충분하다(trends.js buildFirmProfileLink 와 동일 관례). firm_key 가 없으면(방어)
  // 링크 없이 업체명 텍스트만 렌더한다.
  function buildDocRow(doc) {
    var row = document.createElement("div");
    row.className = "ip-doc-row";

    var main = document.createElement("div");
    main.className = "ip-doc-row-main";
    main.appendChild(el("span", "ip-doc-date", doc.published_date || ""));
    if (doc.source) main.appendChild(el("span", "ip-b", doc.source));

    if (doc.firm_name) {
      var firmDisplay = decodeFirmDisplay(doc.firm_name);
      if (doc.firm_key) {
        var firmLink = document.createElement("a");
        firmLink.className = "ip-doc-firm";
        firmLink.href = "../firm/index.html?key=" + encodeURIComponent(doc.firm_key);
        firmLink.textContent = firmDisplay;
        main.appendChild(firmLink);
      } else {
        main.appendChild(el("span", "ip-doc-firm", firmDisplay));
      }
    }

    var canExpand = (doc.public_obs_cnt || 0) > 0;
    var obsCnt = doc.obs_cnt || 0;
    // [완역 자동 전환] 문서의 지적이 전부 국문 열람 가능하면 병기 괄호가 동어반복이자
    // 미번역이 남은 듯한 인상만 주므로 생략 — 일부만 공개된 문서에만 "(국문 열람 가능
    // M건)"을 남긴다(firm.js 와 동일 계약).
    var partiallyPublic = canExpand && (doc.public_obs_cnt || 0) < obsCnt;
    var countText = "지적 " + fmtNum(obsCnt) + "건" +
      (partiallyPublic ? "(국문 열람 가능 " + fmtNum(doc.public_obs_cnt) + "건)" : "");
    main.appendChild(el("span", "ip-doc-count", countText));

    var detail = document.createElement("div");
    detail.className = "ip-doc-detail";
    detail.hidden = true;

    if (canExpand) {
      main.appendChild(el("span", "ip-doc-chev", "▸"));
      var loaded = false;
      makeClickableRow(main, (doc.source || "") + " " + (doc.published_date || "") + " 지적사항 펼치기",
        function () {
          var open = row.classList.toggle("open");
          detail.hidden = !open;
          if (open && !loaded) {
            loaded = true;
            renderDocDetailLoading(detail);
            fetchDocObservations(doc.raw_signal_id)
              .then(function (rows) { renderDocDetail(detail, rows); })
              .catch(function () { renderDocDetailError(detail); loaded = false; });
          }
        });
    } else {
      row.classList.add("disabled");
      main.appendChild(el("span", "ip-doc-pending", "국문 번역 대기 중"));
    }

    row.appendChild(main);
    row.appendChild(detail);
    return row;
  }

  function renderDocuments(documents) {
    docsEl.innerHTML = "";
    if (!documents.length) {
      docsEl.appendChild(el("p", "ip-empty", "표시할 문서가 없습니다."));
      return;
    }
    // RPC 가 이미 published_date desc 로 정렬해 반환한다(findings_firm_profile documents
    // 와 동일 계약) — 재정렬 없음.
    documents.forEach(function (doc) { docsEl.appendChild(buildDocRow(doc)); });
  }

  // ── 오케스트레이션 ───────────────────────────────────────────────────────
  function renderAll(data) {
    nameEl.textContent = data.display_name || "";
    renderStats(data.totals || {});
    renderCategories(data.by_category || [], data.display_name || "");
    renderYears(data.by_year || []);
    renderDocuments(data.documents || []);
  }

  function rpcEndpoint(name) {
    return url.replace(/\/$/, "") + "/rest/v1/rpc/" + name;
  }

  function fetchInspectorProfile(inspectorKey) {
    return fetch(rpcEndpoint("findings_inspector_profile"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_inspector_key: inspectorKey }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_inspector_profile " + r.status);
      return r.json();
    });
  }

  // ── [이름으로 조회] 037/039 findings_inspector_index ─────────────────────────
  // ★이 코드가 지켜야 하는 037 계약(어기면 이 기능은 '디렉터리'가 된다):
  //   · **2글자 미만이면 아무것도 그리지 않는다.** 빈 입력에 전체 명단을 뿌리는 순간
  //     이 화면은 사람을 훑어보는 목록이 된다 — 037 이 금지한 바로 그것이다.
  //   · 결과는 **알파벳순**으로 최대 8명. 문서 수 내림차순으로 정렬하면 그 자체가
  //     순위표라 037 의 "순위·비교 금지"를 어긴다. 건수는 맥락으로 병기만 한다.
  //   · index RPC 는 세션당 1회만 받아 캐시한다(findings.js 의 동일 관례).
  var idxCache = null;

  function fetchInspectorIndex() {
    if (idxCache) return idxCache;
    idxCache = fetch(rpcEndpoint("findings_inspector_index"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_inspector_index " + r.status);
      return r.json();
    });
    return idxCache;
  }

  function buildLookupRow(item) {
    var a = document.createElement("a");
    a.className = "tr-look-row";
    a.href = "?key=" + encodeURIComponent(item.inspector_key || "");
    a.appendChild(el("span", "tr-look-name", item.display_name || item.inspector_key || ""));
    a.appendChild(el("span", "tr-look-meta", "문서 " + (item.documents || 0) + "건"));
    return a;
  }

  function runLookup() {
    if (!lookInputEl || !lookResEl) return;
    var q = (lookInputEl.value || "").trim().toLowerCase();
    lookResEl.innerHTML = "";
    if (q.length < 2) {
      // 목록화 방지 게이트 — 이 분기에서 절대 결과를 그리지 않는다.
      lookResEl.appendChild(el("p", "tr-look-empty", "두 글자 이상 입력해 주세요."));
      return;
    }
    lookResEl.appendChild(el("p", "tr-look-empty", "찾는 중\u2026"));
    fetchInspectorIndex().then(function (rows) {
      if (!lookResEl) return;
      lookResEl.innerHTML = "";
      var hits = (rows || []).filter(function (r) {
        return String(r.display_name || "").toLowerCase().indexOf(q) >= 0 ||
               String(r.inspector_key || "").toLowerCase().indexOf(q) >= 0;
      }).sort(function (a, b) {
        return String(a.display_name || "").localeCompare(String(b.display_name || ""));
      }).slice(0, 8);
      if (!hits.length) {
        lookResEl.appendChild(el("p", "tr-look-empty",
          "찾은 실사관이 없습니다. 공개 문서 5건 이상 확인된 실사관만 이력을 제공합니다."));
        return;
      }
      hits.forEach(function (it) { lookResEl.appendChild(buildLookupRow(it)); });
    }).catch(function () {
      if (!lookResEl) return;
      lookResEl.innerHTML = "";
      lookResEl.appendChild(el("p", "tr-look-empty", "실사관 조회를 불러오지 못했습니다."));
    });
  }

  if (lookFormEl) {
    lookFormEl.addEventListener("submit", function (ev) {
      ev.preventDefault();
      runLookup();
    });
  }

  var inspectorKeyParam = getInspectorKeyParam();

  if (!url || !key) {
    // env 미설정 — 조회도 프로파일도 불가. 단일 안내로 수렴(코호트 게이트 정보 누출
    // 방지 원칙은 그대로 — 파일 상단 주석 참조).
    showState("unavailable");
  } else if (!inspectorKeyParam) {
    // [존 재편] 키가 없으면 조회 랜딩. 막다른 안내로 끝내지 않는다.
    showState("lookup");
    if (lookInputEl) lookInputEl.focus();
  } else {
    showState("loading");
    fetchInspectorProfile(inspectorKeyParam)
      .then(function (data) {
        // 코호트 미달·미존재·키 형식 오류는 전부 null(계약, findings_inspector_profile
        // 참조) — display_name 이 없으면 무조건 "표시할 수 없습니다"로 수렴한다.
        if (!data || typeof data !== "object" || !(data.display_name || "")) {
          showState("unavailable");
          return;
        }
        renderAll(data);
        showState("content");
      })
      .catch(function () {
        // RPC 미배포·network 실패도 동일하게 수렴(구분 없음).
        showState("unavailable");
      });
  }
})();
