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
 * ★범위(의도적 제한, 회귀 금지 — 2026-08-31 개정) — 037 이 세웠던 "실사관 디렉터리(목록
 * 열람) 페이지를 만들지 않는다"는 이제 "목록의 존재"가 아니라 "사람을 서열화하는 것"을
 * 금지하는 것으로 좁혔다(근거: 037 SQL 헤더 2026-08-31 개정 블록, 사용자 결정 "순위를
 * 매기지 말라는 거고, 가나다 순으로 색인하면 됨"). 지금도 그대로 금지인 것: 실사관 간
 * **순위·비교**, "엄격하다/까다롭다" 류 성향 해석, **건수 표기·건수 기준 정렬을 통한
 * 사실상의 랭킹**(그런 심볼·패턴이 생기면 회귀 — web/tests/test_render.py 의 범위 가드
 * 테스트 참조). 새로 허용된 것: 조회 랜딩(`ip-lookup`, ?key= 없는 상태) 안에서만 사는
 * **이름순(display_name 오름차순) 색인 하나** — 정렬 키는 이름뿐이고 건수는 어디에도
 * 찍지 않는다(buildIndexGroups/filterIndexRows 는 반환 객체에 documents 필드 자체를
 * 담지 않는다 — 렌더가 실수로도 건수를 그릴 수 없게 데이터 모양으로 막는다). 별도
 * 라우트는 새로 만들지 않는다.
 *
 * [동기화 규칙] CATEGORY_LABELS 는 findings.js/trends.js/firm.js 의 동명 상수·grm_findings.
 * FINDING_TAXONOMY 20개 code/label_ko/label_en 과 완전히 일치해야 한다(web/tests/
 * test_render.py 가 web/assets/*.js 전수 글롭으로 대조 테스트를 강제한다).
 *
 * [확인한 제조소 2026-08-31] "반복 확인된 영역" 다음·"연도별 추이" 앞에 사는 블록
 * (buildFirmGroups/buildFirmRow/renderFirms) — RPC 를 새로 만들지 않고 documents[]
 * 를 firm_name 으로 클라이언트 집계한다. 정렬은 이름순(localeCompare) 고정, 문서 N건
 * 표기는 037 이 색인에만 거는 건수 미표기 규칙과 별개로 허용된다(위 §범위 주석 참조).
 */
(function () {
  "use strict";
  var _t = function (s, v) {
    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;
    return v ? r.replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : r;
  };

  var cfg = document.getElementById("grm-inspector-cfg");
  var loadingEl = document.getElementById("ip-loading");
  var unavailableEl = document.getElementById("ip-unavailable");
  var contentEl = document.getElementById("ip-content");
  var nameEl = document.getElementById("ip-name");
  var statsEl = document.getElementById("ip-stats");
  var catEl = document.getElementById("ip-cat");
  var yearEl = document.getElementById("ip-year");
  var docsEl = document.getElementById("ip-docs");
  // [P1 해석층] 신설 셸 — 구버전 셸에서도 무해하도록 전부 null 가드로 쓴다.
  var catNoteEl = document.getElementById("ip-cat-note");
  var repBlockEl = document.getElementById("ip-rep-block");
  var repNoteEl = document.getElementById("ip-rep-note");
  var repEl = document.getElementById("ip-rep");
  var filterEl = document.getElementById("ip-filter");
  // [확인한 제조소 2026-08-31] 신설 셸 — 마찬가지로 null 가드(renderFirms 참조).
  var firmBlockEl = document.getElementById("ip-firm-block");
  var firmEl = document.getElementById("ip-firm");
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
    data_integrity: { ko: _t("데이터 완전성"), en: "Data integrity" },
    computer_system_validation: { ko: _t("컴퓨터화시스템"), en: "Computer system validation" },
    documentation_records: { ko: _t("문서화/기록관리"), en: "Documentation and records" },
    aseptic_sterility_assurance: { ko: _t("무균보증/무균공정"), en: "Aseptic processing and sterility assurance" },
    environmental_monitoring: { ko: _t("환경모니터링"), en: "Environmental monitoring" },
    cleaning_validation: { ko: _t("세척밸리데이션"), en: "Cleaning validation" },
    complaint_recall: { ko: _t("불만/회수"), en: "Complaint and recall handling" },
    deviation_capa: { ko: _t("일탈/CAPA/조사"), en: "Deviation, CAPA, and investigation" },
    quality_unit_oversight: { ko: _t("품질부서 관리감독"), en: "Quality unit oversight" },
    qc_lab_controls: { ko: _t("시험실/품질관리"), en: "Laboratory and QC controls" },
    process_validation: { ko: _t("공정밸리데이션"), en: "Process validation" },
    equipment_facility: { ko: _t("설비/시설"), en: "Equipment and facility" },
    material_supplier_control: { ko: _t("원자재/공급업체 관리"), en: "Material and supplier control" },
    contamination_control: { ko: _t("오염/교차오염 관리"), en: "Contamination control" },
    validation_qualification: { ko: _t("밸리데이션/적격성평가"), en: "Validation and qualification" },
    stability_storage: { ko: _t("안정성/보관"), en: "Stability and storage" },
    labeling_packaging: { ko: _t("표시/포장"), en: "Labeling and packaging" },
    regulatory_reporting: { ko: _t("규제보고/변경관리"), en: "Regulatory reporting and change control" },
    training_personnel: { ko: _t("교육/작업자"), en: "Training and personnel" },
    other_quality_system: { ko: _t("기타 품질시스템"), en: "Other quality system" },
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

  // ★[P1 해석층 — 의도적 비대칭] 업체 프로파일에는 "문서당 지적"(밀도)을 넣었지만 여기엔
  // 넣지 않는다. 업체에서는 실사를 많이 받은 곳이 커 보이는 착시를 걷어내는 정규화지만,
  // 실사관에게 같은 값을 붙이면 **"한 번에 몇 건을 적는 사람인가" = 까다로움 지표**가 되어
  // 037 정책(순위·비교·위험도 표기 금지)이 막으려는 바로 그 읽기를 만든다. 두 화면을
  // '일관성' 때문에 맞추지 말 것 — 다른 것이 맞다.
  function renderStats(totals) {
    statsEl.innerHTML = "";
    statsEl.appendChild(buildStat(fmtNum(totals.documents), _t("실사 문서")));
    statsEl.appendChild(buildStat(fmtNum(totals.findings), _t("지적")));
    statsEl.appendChild(buildStat(fmtNum(totals.firms), _t("업체")));
    var period = (totals.first_seen || "?") + " ~ " + (totals.last_seen || "?");
    statsEl.appendChild(buildStat(period, _t("기간")));
  }

  // ── [P1 해석층] 프로파일 안 좁히기 상태 (firm.js 와 동일 계약) ────────────────
  // 065 가 documents[].categories 를 주면 이 화면 안에서 좁히고, 없으면(미적용·구버전
  // 캐시) 종전처럼 검색으로 내보낸다. 037 정책(순위·비교 금지)은 그대로 — 다른 실사관과
  // 견주는 값은 어디에도 만들지 않고, 이 사람의 공개 이력 안에서만 상대화한다.
  var activeCat = null;
  var LAST_DOCS = [];
  var LAST_NAME = "";
  var LAST_CATS = [];
  var filterable = false;

  function catLabel(code) {
    var label = CATEGORY_LABELS[code];
    return label ? label.ko : code;
  }

  function docHasCat(doc, code) {
    var cats = doc && doc.categories;
    return !!(cats && cats.length && cats.indexOf(code) !== -1);
  }

  function setActiveCat(code) {
    activeCat = activeCat === code ? null : code;
    renderCategories(LAST_CATS, LAST_NAME);
    renderFilter();
    renderDocuments(LAST_DOCS);
    if (docsEl && activeCat && docsEl.scrollIntoView) docsEl.scrollIntoView({ block: "nearest" });
  }

  function renderFilter() {
    if (!filterEl) return;
    filterEl.innerHTML = "";
    if (!activeCat) { filterEl.hidden = true; return; }
    filterEl.hidden = false;
    var shown = LAST_DOCS.filter(function (d) { return docHasCat(d, activeCat); }).length;
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ip-filter-chip";
    chip.appendChild(el("span", null, _t("{cat} · 문서 {n}건", { cat: catLabel(activeCat), n: fmtNum(shown) })));
    chip.appendChild(el("span", "x", "×"));
    chip.setAttribute("aria-label", _t("{cat} 필터 해제", { cat: catLabel(activeCat) }));
    chip.addEventListener("click", function () { setActiveCat(activeCat); });
    filterEl.appendChild(chip);
    var out = document.createElement("a");
    out.className = "ip-filter-out";
    out.href = findingsHref("cat", activeCat, LAST_NAME);
    out.textContent = _t("전체 지적사항에서 보기 →");
    filterEl.appendChild(out);
  }

  // ── 분류 구성(상위 분류 코럴 농도 바) ────────────────────────────────────────
  function buildCatRow(entry, maxCnt, personName, total) {
    var row = document.createElement(filterable ? "button" : "a");
    row.className = "ip-cat-row";
    if (filterable) {
      row.type = "button";
      row.setAttribute("aria-pressed", activeCat === entry.category_code ? "true" : "false");
      row.addEventListener("click", function () { setActiveCat(entry.category_code); });
    } else {
      row.href = findingsHref("cat", entry.category_code, personName);
    }
    row.appendChild(el("span", "ip-cat-label", catLabel(entry.category_code)));
    var track = document.createElement("div");
    track.className = "ip-cat-track";
    var bar = document.createElement("div");
    bar.className = "ip-cat-bar";
    var ratio = maxCnt > 0 ? entry.cnt / maxCnt : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    track.appendChild(bar);
    row.appendChild(track);
    row.appendChild(el("span", "ip-cat-count", _t("{n}건", { n: fmtNum(entry.cnt) })));
    if (total > 0) {
      row.appendChild(el("span", "ip-cat-share", Math.round((entry.cnt / total) * 100) + "%"));
    }
    return row;
  }

  // ★[P1 해석층] 캐치올 분류는 순위 문장·반복 목록에서 뺀다(막대와 분모에는 남긴다).
  // "1위 = 기타 품질시스템"은 분류기 상태를 말하는 문장이라 조치로 이어지지 않는다 —
  // 트렌드 면(#810)이 세운 규율을 그대로 따르고, 뺐다는 사실은 화면에 적는다.
  var CATCH_ALL = "other_quality_system";

  // [P1 해석층] 숫자 위의 한 문장. 어휘 주의 — "가장 많이 지적한"이 아니라 "공개 문서에서
  // 가장 많이 확인된"이다(귀속 금지: 483 은 여러 실사관이 함께 서명할 수 있다).
  function renderCatNote(cats, total) {
    if (!catNoteEl) return;
    catNoteEl.innerHTML = "";
    if (!cats.length || !total) return;
    var ranked = cats.filter(function (c) { return c.category_code !== CATCH_ALL; });
    var other = cats.filter(function (c) { return c.category_code === CATCH_ALL; })[0];
    if (ranked.length) {
      var top = ranked[0];
      catNoteEl.appendChild(document.createTextNode(_t("이 실사관이 서명한 공개 문서에서 가장 많이 확인된 영역은 ")));
      catNoteEl.appendChild(el("b", null, catLabel(top.category_code)));
      catNoteEl.appendChild(document.createTextNode(
        _t("입니다({n}건 · 이 이력 안에서 {pct}%).", { n: fmtNum(top.cnt), pct: Math.round((top.cnt / total) * 100) })
      ));
    }
    if (other) {
      catNoteEl.appendChild(document.createTextNode(
        _t(" {cat} {n}건은 세부 분류 전이라 이 문장에서 뺐습니다 — 위 비율의 분모와 아래 막대에는 그대로 들어 있습니다.",
          { cat: catLabel(CATCH_ALL), n: fmtNum(other.cnt) })
      ));
    }
    if (filterable) {
      catNoteEl.appendChild(document.createTextNode(
        _t(" 줄을 누르면 아래 문서 목록이 그 분류로 좁혀집니다.")
      ));
    }
  }

  // ── [P1 해석층] 반복 확인된 영역(065 repeats) ────────────────────────────────
  function renderRepeats(repeats) {
    if (!repBlockEl || !repEl) return;
    var all = repeats || [];
    var rows = all.filter(function (r) { return r.category_code !== CATCH_ALL; });
    var dropped = all.filter(function (r) { return r.category_code === CATCH_ALL; })[0];
    if (!rows.length) { repBlockEl.hidden = true; return; }
    repBlockEl.hidden = false;
    if (repNoteEl) {
      repNoteEl.textContent =
        _t("이 실사관이 서명한 서로 다른 문서 2건 이상에서 다시 확인된 영역입니다. 같은 문서 안에서 여러 건이 잡힌 것은 반복으로 세지 않으며, 다른 실사관과 비교한 값이 아닙니다.") +
        (dropped ? _t(" {cat}(문서 {n}건)은 세부 분류 전이라 뺐습니다.",
          { cat: catLabel(CATCH_ALL), n: fmtNum(dropped.documents) }) : "");
    }
    repEl.innerHTML = "";
    rows.forEach(function (r) {
      var row = el("div", "ip-rep-row");
      row.appendChild(el("span", "ip-rep-name", catLabel(r.category_code)));
      row.appendChild(el("span", "ip-rep-docs", _t("문서 {n}건", { n: fmtNum(r.documents) })));
      var years = document.createElement("span");
      years.className = "ip-rep-years";
      (r.years || []).forEach(function (y) { years.appendChild(el("span", "ip-rep-year", y)); });
      row.appendChild(years);
      repEl.appendChild(row);
    });
  }

  // ── [확인한 제조소 2026-08-31] documents[] 를 firm_name 으로 클라이언트 집계 ──────
  // 마이그레이션 없음 — findings_inspector_profile RPC 응답의 documents[]
  // (firm_name·firm_key·published_date) 을 그대로 재사용한다. 037 정책은 실사관 간
  // 순위·비교를 금지하지 제조소 목록을 금지하지 않지만("행 안의 문서 N건 은 사실 표기라
  // 허용" — 색인의 건수 미표기 규칙과는 다른 자리다), "찾기 쉬운 순서 = 중요도"로
  // 읽히지 않도록 정렬 키는 firm_name(localeCompare) 하나로 고정한다(사용자 확정) —
  // 건수 기준 비교자는 절대 만들지 않는다.
  //
  // firm_name 이 비면(방어) card_scaffold.VALUE_UNKNOWN 과 동일한 부재 어휘("미확인")로
  // 표기한다 — 이 화면과 같은 FDA 483 파이프라인(_w2_extra_fda_483)이 이미 "제조소/업체"
  // 행에 쓰는 값이라 새 어휘를 짓지 않는다. 그 버킷은 firm_key 를 신뢰할 수 없으므로
  // (서로 다른 실제 제조소가 섞여 있을 수 있다) 절대 링크를 달지 않는다.
  var FIRM_BLANK_LABEL = _t("미확인");

  function buildFirmGroups(documents) {
    var byName = {};
    var order = [];
    (documents || []).forEach(function (doc) {
      if (!doc) return;
      var decoded = decodeFirmDisplay(doc.firm_name || "").trim();
      var name = decoded || FIRM_BLANK_LABEL;
      var g = byName[name];
      if (!g) {
        g = { name: name, firm_key: "", count: 0, years: {} };
        byName[name] = g;
        order.push(g);
      }
      if (decoded && !g.firm_key && doc.firm_key) g.firm_key = doc.firm_key;
      g.count++;
      var year = String(doc.published_date || "").slice(0, 4);
      if (/^\d{4}$/.test(year)) g.years[year] = true;
    });
    order.forEach(function (g) { g.years = Object.keys(g.years).sort(); });
    // 이름순(오름차순) 고정 — 건수(count)는 정렬 키로 절대 쓰지 않는다.
    order.sort(function (a, b) { return a.name.localeCompare(b.name); });
    return order;
  }

  function buildFirmRow(g) {
    var row = el("div", "ip-firm-row");
    if (g.firm_key) {
      var link = document.createElement("a");
      link.className = "ip-firm-name";
      link.href = root + "findings/firm/index.html?key=" + encodeURIComponent(g.firm_key);
      link.textContent = g.name;
      row.appendChild(link);
    } else {
      row.appendChild(el("span", "ip-firm-name", g.name));
    }
    var meta = _t("문서 {n}건", { n: fmtNum(g.count) });
    if (g.years.length) meta += " · " + g.years.join(", ");
    row.appendChild(el("span", "ip-firm-meta", meta));
    return row;
  }

  function renderFirms(documents) {
    if (!firmBlockEl || !firmEl) return;
    var groups = buildFirmGroups(documents);
    if (!groups.length) { firmBlockEl.hidden = true; return; }
    firmBlockEl.hidden = false;
    firmEl.innerHTML = "";
    groups.forEach(function (g) { firmEl.appendChild(buildFirmRow(g)); });
  }

  function renderCategories(byCategory, personName) {
    LAST_CATS = byCategory || [];
    catEl.innerHTML = "";
    if (!LAST_CATS.length) {
      catEl.appendChild(el("p", "ip-empty", _t("표시할 데이터가 없습니다.")));
      return;
    }
    // RPC 가 이미 cnt desc 로 정렬해 반환한다(findings_firm_profile by_category 와 동일
    // 계약) — 재정렬 없음.
    var maxCnt = LAST_CATS[0].cnt || 1;
    var total = LAST_CATS.reduce(function (s, c) { return s + (Number(c.cnt) || 0); }, 0);
    LAST_CATS.forEach(function (c) {
      catEl.appendChild(buildCatRow(c, maxCnt, personName, total));
    });
    renderCatNote(LAST_CATS, total);
  }

  // ── 연도 추이(간단 막대) ─────────────────────────────────────────────────
  function renderYears(byYear) {
    yearEl.innerHTML = "";
    if (!byYear.length) {
      yearEl.appendChild(el("p", "ip-empty", _t("표시할 데이터가 없습니다.")));
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
      summary.textContent = _t("원문 보기 (영문)");
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
    container.appendChild(el("p", "ip-doc-detail-loading", _t("불러오는 중…")));
  }

  function renderDocDetailError(container) {
    container.innerHTML = "";
    container.appendChild(el("p", "ip-doc-detail-empty", _t("지적사항을 불러오지 못했습니다.")));
  }

  function renderDocDetail(container, rows) {
    container.innerHTML = "";
    if (!Array.isArray(rows) || !rows.length) {
      container.appendChild(el("p", "ip-doc-detail-empty", _t("공개된 지적사항이 없습니다.")));
      return;
    }
    rows.forEach(function (row) { container.appendChild(buildObsCard(row)); });
  }

  // ── [A2] 문서 상세 링크 멤버십 — 다른 에이전트가 dist 에 발행하는
  // assets/inspector-doc-pages.json({schema:"grm-inspector-doc-pages/v1",
  // document_ids:[...]}, 사전순)을 소비한다. 이 집합은 **정적 문서 페이지가 실제
  // 존재하는 문서 id** 뿐이다 — 임계 미달 문서까지 확인 없이 링크하면 16% 404(실측).
  // 프로파일이 렌더될 때 한 번만 lazy fetch 하고 세션 캐시, 실패는 조용히 삼킨다(문서
  // 목록 자체는 링크 없이 그대로 보여야 한다 — 렌더를 막지 않는다).
  var DOC_PAGE_IDS = null; // null = 아직 모름/실패 · 객체 = {document_id: true, ...}
  var docPagesPromise = null;

  function fetchDocPageIds() {
    if (docPagesPromise) return docPagesPromise;
    docPagesPromise = fetch(root + "assets/inspector-doc-pages.json")
      .then(function (r) {
        if (!r.ok) throw new Error("inspector-doc-pages " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || data.schema !== "grm-inspector-doc-pages/v1" ||
            !Array.isArray(data.document_ids)) {
          return null;
        }
        var set = {};
        data.document_ids.forEach(function (id) { if (id) set[id] = true; });
        DOC_PAGE_IDS = set;
        return set;
      })
      .catch(function () { return null; });
    return docPagesPromise;
  }

  // document_id 가 위 집합에 있으면 날짜+소스 배지 영역을 findings/doc/{document_id}/
  // 링크로 감싼다(제목이 따로 없는 이 행에서 가장 "제목"에 가까운 자리). 없으면 현행처럼
  // 링크 없는 평문. 업체 링크(firm_key, 아래)는 이미 정상 동작 중이라 건드리지 않는다 —
  // 이 링크는 문서 상세로 가는 **처음 생기는** 간선이다.
  function appendDocTitleArea(main, doc) {
    var docId = doc.document_id || "";
    var hasPage = !!(DOC_PAGE_IDS && docId && DOC_PAGE_IDS[docId]);
    if (!hasPage) {
      main.appendChild(el("span", "ip-doc-date", doc.published_date || ""));
      if (doc.source) main.appendChild(el("span", "ip-b", doc.source));
      return;
    }
    var link = document.createElement("a");
    link.className = "ip-doc-title";
    link.href = root + "findings/doc/" + encodeURIComponent(docId) + "/";
    link.appendChild(el("span", "ip-doc-date", doc.published_date || ""));
    if (doc.source) link.appendChild(el("span", "ip-b", doc.source));
    // 이 링크는 main 안에 중첩돼 있고 main 자체도 클릭 핸들러(지적사항 펼치기,
    // makeClickableRow)를 갖는다 — stopPropagation 없이 두면 한 클릭이 "문서 페이지로
    // 이동"과 "펼치기 토글"을 동시에 시도해 둘이 서로 삼킨다.
    link.addEventListener("click", function (ev) { ev.stopPropagation(); });
    main.appendChild(link);
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
    appendDocTitleArea(main, doc);

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
    var countText = _t("지적 {n}건", { n: fmtNum(obsCnt) }) +
      (partiallyPublic ? _t("(국문 열람 가능 {n}건)", { n: fmtNum(doc.public_obs_cnt) }) : "");
    main.appendChild(el("span", "ip-doc-count", countText));

    var detail = document.createElement("div");
    detail.className = "ip-doc-detail";
    detail.hidden = true;

    if (canExpand) {
      main.appendChild(el("span", "ip-doc-chev", "▸"));
      var loaded = false;
      makeClickableRow(main, _t("{source} {date} 지적사항 펼치기", { source: doc.source || "", date: doc.published_date || "" }),
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
      main.appendChild(el("span", "ip-doc-pending", _t("국문 번역 대기 중")));
    }

    row.appendChild(main);
    row.appendChild(detail);
    return row;
  }

  function renderDocuments(documents) {
    docsEl.innerHTML = "";
    if (!documents.length) {
      docsEl.appendChild(el("p", "ip-empty", _t("표시할 문서가 없습니다.")));
      return;
    }
    // [P1 해석층] 활성 분류가 있으면 그 분류가 붙은 문서만 — 프로파일을 벗어나지 않는다.
    var rows = activeCat
      ? documents.filter(function (d) { return docHasCat(d, activeCat); })
      : documents;
    if (!rows.length) {
      docsEl.appendChild(el("p", "ip-empty",
        _t("{cat} 분류가 붙은 문서가 목록에 없습니다.", { cat: catLabel(activeCat) })));
      return;
    }
    // RPC 가 이미 published_date desc 로 정렬해 반환한다(findings_firm_profile documents
    // 와 동일 계약) — 재정렬 없음.
    rows.forEach(function (doc) { docsEl.appendChild(buildDocRow(doc)); });
  }

  // ── 오케스트레이션 ───────────────────────────────────────────────────────
  function renderAll(data) {
    nameEl.textContent = data.display_name || "";
    LAST_NAME = data.display_name || "";
    LAST_DOCS = data.documents || [];
    activeCat = null;
    // [P1 해석층] 065 적용 여부를 응답에서 판정(배포 순서가 어긋나도 화면은 안 깨진다).
    filterable = LAST_DOCS.some(function (d) {
      return d && d.categories && d.categories.length;
    });
    renderStats(data.totals || {});
    renderCategories(data.by_category || [], LAST_NAME);
    renderRepeats(data.repeats || []);
    renderFirms(LAST_DOCS);
    renderYears(data.by_year || []);
    renderFilter();
    renderDocuments(LAST_DOCS);
  }

  function rpcEndpoint(name) {
    return url.replace(/\/$/, "") + "/rest/v1/rpc/" + name;
  }

  function fetchInspectorProfileOnce(inspectorKey) {
    return fetch(rpcEndpoint("findings_inspector_profile"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_inspector_key: inspectorKey }),
    }).then(function (r) {
      if (!r.ok) {
        var err = new Error("findings_inspector_profile " + r.status);
        err.status = r.status;
        throw err;
      }
      return r.json();
    });
  }

  // ── [A3] RPC 재시도 — 실측: 프로파일 RPC 첫 호출이 콜드스타트로 HTTP 500(직후 8/8
  // 200·중앙값 645ms). 5xx 또는 네트워크 오류(응답이 없어 status 를 모르는 경우)만
  // 재시도 대상이다. ★null 응답(코호트 미달·미존재)은 정상 200 이라 이 함수에 예외로
  // 들어오지 않는다 — .then() 을 그대로 통과하므로 재시도 판정 자체가 발동하지 않는다
  // (재시도 제외가 아니라 애초에 재시도 분기에 도달할 방법이 없다).
  var RETRY_DELAY_MS = 400;

  function isRetryableFetchError(err) {
    if (!err) return false;
    var status = err.status;
    if (typeof status !== "number") return true; // fetch 자체가 실패(네트워크 오류)
    return status >= 500 && status < 600;
  }

  function delay(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // 037 의 정보 누출 방지 계약(실패 모드를 하나의 화면 상태로 수렴)은 그대로 — 재시도는
  // fetch 계층 안에서만 일어나고 화면 상태를 늘리지 않는다. 1회 재시도도 실패하면
  // 기존과 동일하게 바깥 .catch() 에서 "unavailable" 로 수렴한다.
  function fetchInspectorProfileWithRetry(inspectorKey) {
    return fetchInspectorProfileOnce(inspectorKey).catch(function (err) {
      if (!isRetryableFetchError(err)) throw err;
      return delay(RETRY_DELAY_MS).then(function () {
        return fetchInspectorProfileOnce(inspectorKey);
      });
    });
  }

  // ── [B] 이름순 색인(037 2026-08-31 개정, findings_inspector_index RPC 소비) ────────
  // 데이터는 037/039 findings_inspector_index() 그대로(코호트, {inspector_key,
  // display_name, documents}). ★건수(documents)는 여기서부터 화면까지 어디에도 찍지
  // 않는다 — buildIndexGroups 의 반환 객체가 애초에 그 필드를 담지 않으므로 렌더 쪽의
  // 실수로도 건수가 그려질 수 없다(이중 방어, 소스 텍스트 가드는 web/tests/
  // test_render.py 가 별도로 건다). index RPC 는 세션당 1회만 받아 캐시한다.
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

  // 부분일치(이름 또는 key) — 대소문자 무시. 빈 질의는 전체를 그대로 통과시킨다 — 037
  // 개정으로 "빈 입력 = 이름순 전체 훑어보기"가 이제 허용된 상태다(과거엔 여기서
  // 목록화 방지 게이트가 막았다).
  function filterIndexRows(rows, query) {
    var q = String(query || "").trim().toLowerCase();
    var list = Array.isArray(rows) ? rows : [];
    if (!q) return list.slice();
    return list.filter(function (r) {
      return String((r && r.display_name) || "").toLowerCase().indexOf(q) >= 0 ||
             String((r && r.inspector_key) || "").toLowerCase().indexOf(q) >= 0;
    });
  }

  // display_name 오름차순(localeCompare) 정렬 + 첫 글자 그룹핑. 그룹 헤더는 **보이는
  // 이름의 첫 글자**를 쓴다(성이 아니라 — 보이는 대로 정렬해야 예측 가능하다). ★반환
  // 객체 {letter, items:[{inspector_key, display_name}]} 에는 documents(건수)가 없다.
  function buildIndexGroups(rows) {
    var list = (Array.isArray(rows) ? rows : [])
      .filter(function (r) { return r && r.display_name; })
      .slice()
      .sort(function (a, b) {
        return String(a.display_name).localeCompare(String(b.display_name));
      });
    var groups = [];
    var byLetter = {};
    list.forEach(function (r) {
      var letter = String(r.display_name).charAt(0).toUpperCase() || "#";
      if (!byLetter[letter]) {
        byLetter[letter] = { letter: letter, items: [] };
        groups.push(byLetter[letter]);
      }
      byLetter[letter].items.push({
        inspector_key: r.inspector_key || "",
        display_name: r.display_name,
      });
    });
    return groups;
  }

  function buildIndexItemLink(item) {
    var a = document.createElement("a");
    a.className = "ip-idx-link";
    a.href = "?key=" + encodeURIComponent(item.inspector_key || "");
    a.textContent = item.display_name || item.inspector_key || "";
    return a;
  }

  // 접근성: 목록은 <ul>/<li>, 그룹 헤더(<h3>)와 목록을 aria-labelledby 로 연결.
  function renderIndexGroups(groups) {
    if (!lookResEl) return;
    lookResEl.innerHTML = "";
    if (!groups.length) {
      lookResEl.appendChild(el("p", "tr-look-empty",
        _t("찾은 실사관이 없습니다. 공개 문서 5건 이상 확인된 실사관만 이력을 제공합니다.")));
      return;
    }
    groups.forEach(function (g, gi) {
      var section = document.createElement("section");
      section.className = "ip-idx-group";
      var headingId = "ip-idx-g-" + gi;
      var heading = el("h3", "ip-idx-letter", g.letter);
      heading.id = headingId;
      section.appendChild(heading);
      var ul = document.createElement("ul");
      ul.className = "ip-idx-list";
      ul.setAttribute("aria-labelledby", headingId);
      g.items.forEach(function (item) {
        var li = document.createElement("li");
        li.appendChild(buildIndexItemLink(item));
        ul.appendChild(li);
      });
      section.appendChild(ul);
      lookResEl.appendChild(section);
    });
  }

  var idxRowsCache = null; // fetchInspectorIndex 성공 후 원시 rows(필터링용, 세션 캐시)

  // 검색창 실시간 필터링 + 최초 진입 시 전체 A–Z 렌더(둘 다 이 함수 하나) — 비우면
  // 전체 복귀, 입력하면 부분일치로 좁힌다.
  function renderNameIndex(query) {
    if (!lookResEl) return;
    if (idxRowsCache) {
      renderIndexGroups(buildIndexGroups(filterIndexRows(idxRowsCache, query)));
      return;
    }
    lookResEl.innerHTML = "";
    lookResEl.appendChild(el("p", "tr-look-empty", _t("불러오는 중…")));
    fetchInspectorIndex().then(function (rows) {
      idxRowsCache = Array.isArray(rows) ? rows : [];
      if (!lookResEl) return;
      // 로딩 중 사용자가 계속 입력했을 수 있으니 지금 입력값 기준으로 그린다.
      var live = lookInputEl ? lookInputEl.value : query;
      renderIndexGroups(buildIndexGroups(filterIndexRows(idxRowsCache, live)));
    }).catch(function () {
      // ★색인 로드 실패 — 색인 영역만 조용히 생략한다(검색창 자체는 그대로 동작).
      if (!lookResEl) return;
      lookResEl.innerHTML = "";
    });
  }

  function runLookup() {
    renderNameIndex(lookInputEl ? lookInputEl.value : "");
  }

  if (lookFormEl) {
    lookFormEl.addEventListener("submit", function (ev) {
      ev.preventDefault();
      runLookup();
    });
  }

  if (lookInputEl) {
    lookInputEl.addEventListener("input", function () { runLookup(); });
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
    // [037 2026-08-31 개정] 빈 검색창 = 전체 이름순 색인을 바로 보여준다.
    renderNameIndex("");
  } else {
    showState("loading");
    // A2: 문서 링크 멤버십은 프로파일 fetch 와 병렬로(서로 독립, 실패해도 서로 막지 않음).
    Promise.all([fetchInspectorProfileWithRetry(inspectorKeyParam), fetchDocPageIds()])
      .then(function (out) {
        var data = out[0];
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
        // RPC 미배포·network 실패(1회 재시도까지 소진)도 동일하게 수렴(구분 없음).
        showState("unavailable");
      });
  }
})();
