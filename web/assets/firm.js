/* GRM 업체 프로파일 (FIND-1 FIND-FIRM-ALIAS 웹 절반) — 정적·클라이언트사이드, 순수 fetch
 * (PostgREST RPC 직접 호출, POST). trends.js/findings.js 와 자매 페이지지만 진입 방식이
 * 다르다 — URL 파라미터(?key=firm_key)로만 조회하는 단일 업체 전용 페이지다.
 *
 * ★방어 설계(불가침) — 013_findings_firm_key.sql 이 라이브 DB 에 아직 적용되지 않았을
 * 수 있다는 전제. findings_firm_profile RPC 가 404(함수 미존재)를 반환하거나 network
 * 자체가 실패해도 "업체 프로파일 준비 중입니다"로만 보여준다(오류처럼 보이지 않게) —
 * findings.js/trends.js 의 "OO 서비스 준비 중입니다" 폴백과 동일 정신. 반대로 key
 * 파라미터가 아예 없거나(URL 오입력) RPC 가 빈 프로파일(display_name "")을 반환하면
 * (013 은 적용됐지만 그 firm_key 자체가 존재하지 않는 경우) "해당 업체를 찾을 수
 * 없습니다"로 구분해 보여준다 — 두 실패 모드를 섞지 않는다.
 *
 * ★안전 계약 — findings_firm_profile RPC 는 집계(count)와 서지 메타만 반환하고
 * finding_text/finding_text_ko 를 어떤 경로로도 내려주지 않는다(013 마이그레이션 원문
 * 참조). 문서 이력에서 "인라인 확장"으로 보여주는 개별 지적사항 원문은 이 RPC 가 아니라
 * 기존 anon REST(`/rest/v1/findings?...&raw_signal_id=eq.X`)로 별도 fetch 한다 — RLS
 * (003/006)가 공개 게이트 통과분만 돌려주므로 이 페이지가 원문 접근 게이트를 우회하지
 * 않는다.
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 에 데이터 삽입 금지 — 원문/
 * 업체명은 자유 텍스트라 이스케이프 누락 시 XSS 위험, findings.js 와 동일 계약).
 *
 * [동기화 규칙] CATEGORY_LABELS 는 findings.js/trends.js 의 동명 상수·grm_findings.
 * FINDING_TAXONOMY 20개 code/label_ko/label_en 과 완전히 일치해야 한다(web/tests/
 * test_render.py 가 대조 테스트로 강제).
 */
(function () {
  "use strict";
  var _t = function (s, v) {
    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;
    return v ? r.replace(/\{(\w+)\}/g, function (m, k) {
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
  //: 이 화면에 한국어 원문 지적이 섞였는가 — 섞였으면 목록 머리에서 한 번 밝힌다.
  var _sawKoreanBody = false;

  var cfg = document.getElementById("grm-firm-cfg");
  var loadingEl = document.getElementById("fp-loading");
  var errorEl = document.getElementById("fp-error");
  var notfoundEl = document.getElementById("fp-notfound");
  var contentEl = document.getElementById("fp-content");
  var nameEl = document.getElementById("fp-firm-name");
  var statsEl = document.getElementById("fp-stats");
  var catEl = document.getElementById("fp-cat");
  var yearEl = document.getElementById("fp-year");
  var docsEl = document.getElementById("fp-docs");
  // [P1 해석층] 신설 셸 — 구버전 셸(이 id 들이 없는 캐시 HTML)에서도 무해하도록 전부
  // null 가드를 두고 쓴다(findings.js hasDash 관례 동형).
  var catNoteEl = document.getElementById("fp-cat-note");
  var repBlockEl = document.getElementById("fp-rep-block");
  var repNoteEl = document.getElementById("fp-rep-note");
  var repEl = document.getElementById("fp-rep");
  var filterEl = document.getElementById("fp-filter");
  // [존 재편 2026-08-26] ?key= 없이 들어왔을 때의 조회 랜딩. 하드 게이트에는 넣지
  // 않는다 — 이 블록이 없는 구버전 셸(캐시 스큐)에서도 프로파일 본기능은 살아야 한다.
  var lookupEl = document.getElementById("fp-lookup");
  // [063] FDA 실사 이력 — 워치리스트와 같은 격리: 미적용 라이브·구버전 셸·fetch 실패
  // 전부 이 블록만 hidden 으로 남고 프로파일 본기능은 무장애.
  var inspSubEl = document.getElementById("fp-insp-sub");
  var inspNoteEl = document.getElementById("fp-insp-note");
  var lookFormEl = document.getElementById("fp-look-form");
  var lookInputEl = document.getElementById("fp-look-input");
  var lookResEl = document.getElementById("fp-look-res");
  if (!cfg || !loadingEl || !errorEl || !notfoundEl || !contentEl || !nameEl ||
      !statsEl || !catEl || !yearEl || !docsEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();
  var root = (cfg.getAttribute("data-root") || "").trim();

  // grm_findings.FINDING_TAXONOMY verbatim(code -> {ko, en}) — findings.js/trends.js 의
  // 동명 상수와 동일 복제본(동기화 테스트로 드리프트 차단).
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

  function showState(which) {
    loadingEl.hidden = which !== "loading";
    errorEl.hidden = which !== "error";
    notfoundEl.hidden = which !== "notfound";
    contentEl.hidden = which !== "content";
    if (lookupEl) lookupEl.hidden = which !== "lookup";
  }

  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") e.textContent = text;
    return e;
  }

  // [firm_name 엔티티 디코드 M5] findings.js/trends.js 의 동명 헬퍼와 동일 계약(별도
  // 파일이라 재사용 불가, 계약만 복제) — DB firm_name(=display_name)에 &amp;/&#039; 가
  // 이미 이스케이프된 채로 저장된 행을 표시 직전(textContent 대입 전)에만 되돌린다.
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

  // 카테고리 바 클릭 → 검색 페이지 필터 링크(trends.js findingsHref 와 동일 계약 —
  // findings.js 의 URL_KEYS.category_code="cat" 을 그대로 따른다).
  // [맥락 유지 2026-08-26] 카테고리만 넘기면 업체 조건이 사라져 전체 코퍼스로 떨어진다
  // (검색 RPC 엔 구조적 업체 필터가 없다 — firm_name 은 자유 검색 blob 에만 있다).
  // q=업체명을 함께 실어 "이 업체 + 이 카테고리"로 착지시킨다 — findings.js 대시보드
  // 업체 행(state.q=업체명)이 쓰는 것과 같은 관용구다. q 값은 **원문 display_name**
  // (엔티티 미해제)이어야 DB blob 과 부분일치가 성립한다(decodeFirmDisplay 금지).
  function findingsHref(paramKey, value, qValue) {
    var href = root + "findings/index.html?" + paramKey + "=" + encodeURIComponent(value);
    if (qValue) href += "&q=" + encodeURIComponent(qValue);
    return href;
  }

  function getFirmKeyParam() {
    if (typeof URLSearchParams === "undefined") return "";
    return (new URLSearchParams(location.search).get("key") || "").trim();
  }

  // ── 스탯 스트립 ──────────────────────────────────────────────────────────
  function buildStat(num, label) {
    var block = el("div", "fp-stat");
    block.appendChild(el("span", "fp-stat-num", num));
    block.appendChild(el("span", "fp-stat-lbl", label));
    return block;
  }

  function renderStats(totals) {
    statsEl.innerHTML = "";
    statsEl.appendChild(buildStat(fmtNum(totals.findings), _t("총 지적")));
    statsEl.appendChild(buildStat(fmtNum(totals.documents), _t("문서")));
    // [P1 해석층] 문서당 지적 — 원시 건수만 보면 **실사를 많이 받은 곳이 무조건 커 보인다**.
    // 문서 수로 나눈 값을 나란히 둬 규모와 밀도를 갈라 읽게 한다(반올림 소수 1자리).
    var docs = Number(totals.documents) || 0;
    var finds = Number(totals.findings) || 0;
    if (docs > 0) {
      statsEl.appendChild(buildStat((Math.round((finds / docs) * 10) / 10).toFixed(1), _t("문서당 지적")));
    }
    var period = (totals.first_seen || "?") + " ~ " + (totals.last_seen || "?");
    statsEl.appendChild(buildStat(period, _t("기간")));
    statsEl.appendChild(buildStat(fmtNum(totals.public_findings), _t("국문 열람 가능")));
  }

  // ── [P1 해석층] 프로파일 안 좁히기 상태 ──────────────────────────────────────
  // 065 가 documents[].categories 를 주기 전에는 이 프로파일을 벗어나 검색으로 나가야
  // 했다(?cat=&q=이름 — 표기 변형 문서를 놓치는 우회). 이제 같은 화면에서 좁힌다.
  // 065 미적용 라이브·구버전 응답이면 categories 가 없으므로 filterable=false 로 떨어져
  // 종전 링크 동작을 그대로 유지한다(조용한 하위호환 — 프로파일 본기능 무장애).
  var activeCat = null;
  var LAST_DOCS = [];
  // [P1.5-3] 실사 응답은 프로파일 렌더 **뒤에** 별도 RPC 로 도착한다(063).
  // 도착 전에는 문서만 그리고, 도착하면 같은 목록을 다시 그린다. null = 아직
  // 모름(또는 실패) — 0건과 구분해야 "확인된 기록 없음" 문장을 언제 쓸지 갈린다.
  var LAST_INSP = null;
  var LAST_NAME = "";
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
    renderTimeline();
    if (docsEl && activeCat && docsEl.scrollIntoView) docsEl.scrollIntoView({ block: "nearest" });
  }

  // 활성 필터 칩 — 해제(×)와 "검색에서 보기"(종전 우회 경로)를 함께 둔다. 프로파일 안
  // 좁히기가 기본이고, 전체 코퍼스에서 같은 분류를 보고 싶을 때만 밖으로 나간다.
  function renderFilter() {
    if (!filterEl) return;
    filterEl.innerHTML = "";
    if (!activeCat) { filterEl.hidden = true; return; }
    filterEl.hidden = false;
    var shown = LAST_DOCS.filter(function (d) { return docHasCat(d, activeCat); }).length;
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "fp-filter-chip";
    chip.appendChild(el("span", null, _t("{cat} · 문서 {n}건", { cat: catLabel(activeCat), n: fmtNum(shown) })));
    chip.appendChild(el("span", "x", "×"));
    chip.setAttribute("aria-label", _t("{cat} 필터 해제", { cat: catLabel(activeCat) }));
    chip.addEventListener("click", function () { setActiveCat(activeCat); });
    filterEl.appendChild(chip);
    var out = document.createElement("a");
    out.className = "fp-filter-out";
    out.href = findingsHref("cat", activeCat, LAST_NAME);
    out.textContent = _t("전체 지적사항에서 보기 →");
    filterEl.appendChild(out);
    // [P1.5-3] 분류 필터는 문서에만 적용된다 — 실사에는 분류가 없어
    // 시간축에서 빠진다. 그 사실을 말하지 않으면 "이 업체는 실사 기록이 없다"로
    // 오독된다(부재 어휘 규율). 실사가 없는 업체에서는 아무 말도 하지 않는다.
    var inspN = ((LAST_INSP && LAST_INSP.inspections) || []).length;
    if (inspN > 0) {
      filterEl.appendChild(el("span", "fp-filter-side",
        _t("실사 {n}건은 분류가 없어 이 필터에서 제외됩니다", { n: fmtNum(inspN) })));
    }
  }

  // ── 분류 구성(상위 분류 코럴 농도 바) ────────────────────────────────────────
  // filterable 이면 <button>(프로파일 안 좁히기), 아니면 종전 <a>(검색으로).
  function buildCatRow(entry, maxCnt, firmName, total) {
    var row = document.createElement(filterable ? "button" : "a");
    row.className = "fp-cat-row";
    if (filterable) {
      row.type = "button";
      row.setAttribute("aria-pressed", activeCat === entry.category_code ? "true" : "false");
      row.addEventListener("click", function () { setActiveCat(entry.category_code); });
    } else {
      row.href = findingsHref("cat", entry.category_code, firmName);
    }
    row.appendChild(el("span", "fp-cat-label", catLabel(entry.category_code)));
    var track = document.createElement("div");
    track.className = "fp-cat-track";
    var bar = document.createElement("div");
    bar.className = "fp-cat-bar";
    var ratio = maxCnt > 0 ? entry.cnt / maxCnt : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    track.appendChild(bar);
    row.appendChild(track);
    row.appendChild(el("span", "fp-cat-count", _t("{n}건", { n: fmtNum(entry.cnt) })));
    // [P1 해석층] 구성비 — "51건"이 이 업체 안에서 어느 정도인지를 같이 적는다.
    if (total > 0) {
      row.appendChild(el("span", "fp-cat-share", Math.round((entry.cnt / total) * 100) + "%"));
    }
    return row;
  }

  var LAST_CATS = [];

  function renderCategories(byCategory, firmName) {
    LAST_CATS = byCategory || [];
    catEl.innerHTML = "";
    if (!LAST_CATS.length) {
      catEl.appendChild(el("p", "fp-empty", _t("표시할 데이터가 없습니다.")));
      return;
    }
    // RPC 가 이미 cnt desc 로 정렬해 반환한다(013 by_category 계약) — 재정렬 없음.
    var maxCnt = LAST_CATS[0].cnt || 1;
    var total = LAST_CATS.reduce(function (s, c) { return s + (Number(c.cnt) || 0); }, 0);
    LAST_CATS.forEach(function (c) {
      catEl.appendChild(buildCatRow(c, maxCnt, firmName, total));
    });
    renderCatNote(LAST_CATS, total);
  }

  // ★[P1 해석층] 캐치올 분류는 **순위 문장과 반복 목록에서 뺀다**(막대와 분모에는 남긴다).
  // "이 업체에서 가장 많이 확인된 영역 = 기타 품질시스템"은 그 업체의 성질이 아니라
  // **분류기 상태**를 말하는 문장이라 조치로 이어지지 않는다. 트렌드 면(#810)이 이미 세운
  // 규율을 프로파일에도 그대로 적용한다 — 빼되 **뺐다는 사실을 적는다**(축을 조용히
  // 바꾸지 않는다). 라이브 프리뷰에서 두 프로파일 모두 1위가 기타로 나와 잡았다.
  var CATCH_ALL = "other_quality_system";

  // [P1 해석층] 숫자 위의 한 문장 — 가장 많이 확인된 영역과 그 비중을 말로 적는다.
  function renderCatNote(cats, total) {
    if (!catNoteEl) return;
    catNoteEl.innerHTML = "";
    if (!cats.length || !total) return;
    var ranked = cats.filter(function (c) { return c.category_code !== CATCH_ALL; });
    var other = cats.filter(function (c) { return c.category_code === CATCH_ALL; })[0];
    if (ranked.length) {
      var top = ranked[0];
      catNoteEl.appendChild(document.createTextNode(_t("공개된 문서에서 가장 많이 확인된 영역은 ")));
      catNoteEl.appendChild(el("b", null, catLabel(top.category_code)));
      catNoteEl.appendChild(document.createTextNode(
        _t("입니다({n}건 · 전체의 {pct}%).", { n: fmtNum(top.cnt), pct: Math.round((top.cnt / total) * 100) })
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
        _t(" 줄을 누르면 아래 문서 이력이 그 분류로 좁혀집니다.")
      ));
    }
  }

  // ── [P1 해석층] 반복 확인된 영역(065 repeats) ────────────────────────────────
  // 누적 합계로는 "한 번에 몰린 것"과 "여러 번 되풀이된 것"이 구분되지 않는다. 여기서는
  // 서로 다른 문서 수로 세므로, 같은 문서 안의 다건은 반복이 아니다.
  function renderRepeats(repeats) {
    if (!repBlockEl || !repEl) return;
    var all = repeats || [];
    // 캐치올은 목록에서 뺀다(위 renderCatNote 와 같은 이유) — 대신 뺐다는 사실을 적는다.
    var rows = all.filter(function (r) { return r.category_code !== CATCH_ALL; });
    var dropped = all.filter(function (r) { return r.category_code === CATCH_ALL; })[0];
    if (!rows.length) { repBlockEl.hidden = true; return; }
    repBlockEl.hidden = false;
    if (repNoteEl) {
      repNoteEl.textContent =
        _t("서로 다른 문서 2건 이상에서 다시 확인된 영역입니다. 같은 문서 안에서 여러 건이 잡힌 것은 반복으로 세지 않습니다.") +
        (dropped ? _t(" {cat}(문서 {n}건)은 세부 분류 전이라 뺐습니다.",
          { cat: catLabel(CATCH_ALL), n: fmtNum(dropped.documents) }) : "");
    }
    repEl.innerHTML = "";
    rows.forEach(function (r) {
      var row = el("div", "fp-rep-row");
      row.appendChild(el("span", "fp-rep-name", catLabel(r.category_code)));
      row.appendChild(el("span", "fp-rep-docs", _t("문서 {n}건", { n: fmtNum(r.documents) })));
      var years = document.createElement("span");
      years.className = "fp-rep-years";
      (r.years || []).forEach(function (y) { years.appendChild(el("span", "fp-rep-year", y)); });
      row.appendChild(years);
      repEl.appendChild(row);
    });
  }

  // ── 연도 추이(간단 막대) ─────────────────────────────────────────────────
  function renderYears(byYear) {
    yearEl.innerHTML = "";
    if (!byYear.length) {
      yearEl.appendChild(el("p", "fp-empty", _t("표시할 데이터가 없습니다.")));
      return;
    }
    var maxCnt = byYear.reduce(function (m, y) { return Math.max(m, y.cnt); }, 0) || 1;
    var wrap = document.createElement("div");
    wrap.className = "fp-year-bars";
    byYear.forEach(function (y) {
      var col = document.createElement("div");
      col.className = "fp-year-col";
      var barwrap = document.createElement("div");
      barwrap.className = "fp-year-barwrap";
      var bar = document.createElement("div");
      bar.className = "fp-year-bar";
      bar.style.height = Math.max(4, Math.round((y.cnt / maxCnt) * 100)) + "%";
      barwrap.appendChild(bar);
      col.appendChild(barwrap);
      col.appendChild(el("span", "fp-year-lbl", y.year));
      col.appendChild(el("span", "fp-year-count", fmtNum(y.cnt)));
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

  // 단순화한 국문+원문 details 카드 — findings.js buildCard() 의 본문/원문 접기/refs
  // 규칙을 이 페이지 전용으로 축약한 것(별도 정적 자산이라 함수 재사용 불가, 계약만 복제).
  function buildObsCard(row) {
    var card = el("article", "fp-obs");
    var label = CATEGORY_LABELS[row.category_code];
    // 분류 라벨은 사전을 타는 표(`label.ko` 가 _t() 라 영어에서 영문으로 나온다).
    // 표에 없는 코드일 때의 폴백은 DB 의 한국어 라벨이라, 영어 화면에는 싣지 않는다
    // — 라벨 하나를 잃는 것보다 영어 화면에 한국어를 남기는 쪽이 나쁘다(손목록이 낡으면
    // 여기로 샌다).
    var fallbackCat = row.category_label_ko || "";
    var catText = label ? label.ko
      : (_isEn && _HANGUL.test(fallbackCat) ? "" : fallbackCat);
    if (catText) card.appendChild(el("p", "fp-obs-cat", catText));

    // [다국어 3단계] 본문은 읽는 언어 먼저(영어판=규제기관 원문), 접기는 반대편.
    var mainText = _bodyText(row);
    if (mainText) {
      card.appendChild(el("p", "fp-obs-text", mainText));
      if (_isEn && _HANGUL.test(mainText)) _sawKoreanBody = true;
    }

    var altText = _altText(row);
    if (altText) {
      var details = document.createElement("details");
      details.className = "fp-obs-orig";
      var summary = document.createElement("summary");
      summary.textContent = _isEn ? _t("국문 번역 보기") : _t("원문 보기 (영문)");
      details.appendChild(summary);
      details.appendChild(el("p", null, altText));
      card.appendChild(details);
    }

    var refs = ([]).concat(row.cfr_refs || [], row.mfds_refs || []);
    if (refs.length) {
      var refsWrap = el("div", "fp-obs-refs");
      refs.forEach(function (r) { if (r) refsWrap.appendChild(el("span", "fp-obs-ref", r)); });
      card.appendChild(refsWrap);
    }
    return card;
  }

  function renderDocDetailLoading(container) {
    container.innerHTML = "";
    container.appendChild(el("p", "fp-doc-detail-loading", _t("불러오는 중…")));
  }

  function renderDocDetailError(container) {
    container.innerHTML = "";
    container.appendChild(el("p", "fp-doc-detail-empty", _t("지적사항을 불러오지 못했습니다.")));
  }

  function renderDocDetail(container, rows) {
    container.innerHTML = "";
    if (!Array.isArray(rows) || !rows.length) {
      container.appendChild(el("p", "fp-doc-detail-empty", _t("공개된 지적사항이 없습니다.")));
      return;
    }
    _sawKoreanBody = false;
    rows.forEach(function (row) { container.appendChild(buildObsCard(row)); });
    // ★[2026-09-06] 영어 화면에 한국어 원문 지적이 섞이면 **감추지 말고 밝힌다** —
    //   식약처 실사기록은 원문이 한국어라 번역본이 따로 없다. 사이트의 다른 다섯 곳
    //   (용어사전 출처·자료실 제목·갱신 스트립·브리프 이름/인용·검색 결과)과 같은 규율.
    if (_isEn && _sawKoreanBody) {
      container.insertBefore(
        el("p", "fp-obs-ko-note",
           _t("이 문서는 한국 규제기관 기록이라 지적 원문이 한국어로 표시됩니다.")),
        container.firstChild);
    }
  }

  function buildDocRow(doc) {
    var row = document.createElement("div");
    row.className = "fp-doc-row";

    var main = document.createElement("div");
    main.className = "fp-doc-row-main";
    main.appendChild(el("span", "fp-doc-date", doc.published_date || ""));
    main.appendChild(el("span", "fp-tl-when", _t("공개")));      // [P1.5-3] 날짜 종류
    main.appendChild(el("span", "fp-tl-kind", _t("문서")));
    if (doc.source) main.appendChild(el("span", "fp-b", doc.source));

    var canExpand = (doc.public_obs_cnt || 0) > 0;
    var obsCnt = doc.obs_cnt || 0;
    // [완역 자동 전환] 문서의 지적이 전부 국문 열람 가능하면 병기 괄호가 동어반복이자
    // 미번역이 남은 듯한 인상만 주므로 생략 — 일부만 공개된 문서(신규 수집 직후 등)에만
    // "(국문 열람 가능 M건)"을 남긴다.
    var partiallyPublic = canExpand && (doc.public_obs_cnt || 0) < obsCnt;
    var countText = _t("지적 {n}건", { n: fmtNum(obsCnt) }) +
      (partiallyPublic ? _t("(국문 열람 가능 {n}건)", { n: fmtNum(doc.public_obs_cnt) }) : "");
    main.appendChild(el("span", "fp-doc-count", countText));

    var detail = document.createElement("div");
    detail.className = "fp-doc-detail";
    detail.hidden = true;

    if (canExpand) {
      main.appendChild(el("span", "fp-doc-chev", "▸"));
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
      main.appendChild(el("span", "fp-doc-pending", _t("국문 번역 대기 중")));
    }

    row.appendChild(main);
    row.appendChild(detail);
    return row;
  }

  // [P1.5-3] 규제 이력 — 문서(공개일)와 실사(실사 종료일)를 한 시간축에 내림차순으로.
  // ★분류 필터는 **문서에만** 적용된다. 실사에는 분류가 없으므로, 필터가 걸리면 실사
  //   행은 빠지고 그 사실을 칩 옆 문장이 말한다 — 조용히 빠지면 "이 업체는 실사 기록이
  //   없다"로 오독된다(부재 어휘 규율).
  // ★정렬은 날짜 문자열(YYYY-MM-DD) 사전순 = 시간순. 날짜가 빈 행은 맨 뒤로 보낸다
  //   (없는 날짜를 0000 으로 채워 맨 앞에 두면 가장 오래된 사건처럼 보인다).
  function timelineEntries() {
    var out = [];
    (LAST_DOCS || []).forEach(function (d) {
      if (activeCat && !docHasCat(d, activeCat)) return;
      out.push({ at: d.published_date || "", build: function () { return buildDocRow(d); } });
    });
    if (!activeCat) {
      ((LAST_INSP && LAST_INSP.inspections) || []).forEach(function (r) {
        out.push({
          at: r.inspection_end_date || "",
          build: function () { return buildInspRow(r); },
        });
      });
    }
    out.sort(function (a, b) {
      if (!a.at) return 1;
      if (!b.at) return -1;
      return a.at < b.at ? 1 : (a.at > b.at ? -1 : 0);
    });
    return out;
  }

  function renderTimeline() {
    if (!docsEl) return;
    docsEl.innerHTML = "";
    var rows = timelineEntries();
    if (!rows.length) {
      docsEl.appendChild(el("p", "fp-empty", activeCat
        ? _t("{cat} 분류가 붙은 문서가 목록에 없습니다.", { cat: catLabel(activeCat) })
        : _t("표시할 기록이 없습니다.")));
      return;
    }
    rows.forEach(function (e) { docsEl.appendChild(e.build()); });
  }

  // ── 관심 업체 워치리스트(015_firm_watchlist.sql — 등록/해제 토글) ────────────
  // reactions.js 의 세션 취득/로그인 상태 판단/Authorization 헤더 사용 패턴을 그대로
  // 재사용한다(새 인증 코드 발명 금지):
  //   · window.supabase(lib.createClient) + auth 설정 4종(storageKey "grm-public-auth-v1"/
  //     persistSession/autoRefreshToken/detectSessionInUrl:false)을 reactions.js 와 동일
  //     하게 생성 — 같은 storageKey 라 localStorage 세션이 그대로 공유된다(별도 로그인 불요).
  //   · 로그인 상태 판단 = session && session.user (reactions.js toggle()/renderMyScraps() 동형).
  //   · DB 호출은 wsb.from("firm_watchlist") — supabase-js 가 Authorization: Bearer
  //     <사용자 access_token> 을 자동 첨부한다(reactions.js 의 sb.from("reaction") 동형.
  //     RLS 본인 행만 — 015 계약).
  //   · 로그인 진입 경로 = reactions.js 가 헤더에 주입하는 로그인 버튼(.grm-acct-login →
  //     openLogin() 팝업)을 클릭 위임으로 재사용(별도 로그인 UI 발명 0).
  // 실패는 전부 삼켜 hidden 유지(조용한 비활성) — env 미설정·supabase-js 부재·015 미적용
  // (테이블 부재 → PostgREST 오류)·network 실패 어느 경우에도 프로파일 본기능 무장애.
  // 주: reactions.js 와 GoTrueClient 2개가 같은 storageKey 를 공유하면 콘솔 경고(Multiple
  // GoTrueClient instances)가 뜰 수 있으나 동작엔 무해하다(둘 다 같은 저장소를 읽는다).
  var watchEl = document.getElementById("fp-watch");

  function initWatchlist(firmKey, displayName) {
    if (!watchEl || !url || !key) return;
    var lib = window.supabase;
    if (!lib || !lib.createClient) return;
    var wsb;
    try {
      wsb = lib.createClient(url, key, {
        auth: {
          storageKey: "grm-public-auth-v1",
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: false
        }
      });
    } catch (e) { return; }

    var wSession = null;
    var registered = false;

    function hideWatch() { watchEl.innerHTML = ""; watchEl.hidden = true; }

    // 비로그인 — 버튼 대신 안내 + 기존 로그인 진입(헤더 버튼) 재사용.
    function renderLoggedOut() {
      watchEl.innerHTML = "";
      watchEl.appendChild(el("p", "fp-watch-note", _t("로그인하면 관심 업체로 등록할 수 있습니다")));
      var lb = document.createElement("button");
      lb.type = "button";
      lb.className = "fp-watch-login";
      lb.textContent = _t("로그인");
      lb.addEventListener("click", function () {
        var headerLogin = document.querySelector(".grm-auth .grm-acct-login");
        if (headerLogin) headerLogin.click();
      });
      watchEl.appendChild(lb);
      watchEl.hidden = false;
    }

    // 로그인 — 등록/해제 토글 버튼("관심 업체 등록" ↔ "관심 등록됨 · 해제").
    function renderWatchButton() {
      watchEl.innerHTML = "";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "fp-watch-btn" + (registered ? " is-on" : "");
      btn.textContent = registered ? _t("관심 등록됨 · 해제") : _t("관심 업체 등록");
      btn.setAttribute("aria-pressed", registered ? "true" : "false");
      var hint = el("p", "fp-watch-hint", "");
      btn.addEventListener("click", function () {
        if (!wSession || !wSession.user) { renderLoggedOut(); return; }
        btn.disabled = true;
        hint.textContent = "";
        var op = registered
          ? wsb.from("firm_watchlist").delete()
              .match({ user_id: wSession.user.id, firm_key: firmKey })
          : wsb.from("firm_watchlist").insert({
              user_id: wSession.user.id,
              firm_key: firmKey,
              firm_display: displayName || ""
            });
        op.then(function (res) {
          btn.disabled = false;
          if (res && res.error) {
            // insert 거부는 015 상한 트리거(사용자당 50개) 초과가 대표 경로 — 상한 안내.
            hint.textContent = registered
              ? _t("해제에 실패했습니다. 잠시 후 다시 시도해 주세요.")
              : _t("등록에 실패했습니다. 관심 업체는 사용자당 최대 50개까지 등록할 수 있습니다.");
            return;
          }
          registered = !registered;
          renderWatchButton();
        }).catch(function () { btn.disabled = false; });
      });
      watchEl.appendChild(btn);
      watchEl.appendChild(hint);
      watchEl.hidden = false;
    }

    function refreshWatch() {
      if (!wSession || !wSession.user) { renderLoggedOut(); return; }
      wsb.from("firm_watchlist").select("firm_key").eq("firm_key", firmKey)
        .then(function (res) {
          // 015 미적용(테이블 부재)·권한 오류 — 조용한 비활성(hidden 유지, 오류 미노출).
          if (res && res.error) { hideWatch(); return; }
          registered = !!((res && res.data) || []).length;
          renderWatchButton();
        })
        .catch(function () { hideWatch(); });
    }

    wsb.auth.getSession().then(function (res) {
      wSession = (res && res.data) ? res.data.session : null;
      refreshWatch();
    }).catch(function () { hideWatch(); });
    wsb.auth.onAuthStateChange(function (_evt, s) {
      wSession = s;
      refreshWatch();
    });
  }

  // ── 오케스트레이션 ───────────────────────────────────────────────────────
  function renderAll(data) {
    nameEl.textContent = decodeFirmDisplay(data.display_name || "");
    // [맥락 유지] q 값은 원문 display_name — DB blob 과 부분일치해야 하므로 decode 금지.
    LAST_NAME = data.display_name || "";
    LAST_DOCS = data.documents || [];
    activeCat = null;
    // [P1 해석층] 065 적용 여부를 **응답에서** 판정한다 — 배포 순서(마이그 먼저)가
    // 어긋나거나 구버전 캐시가 남아도 화면이 깨지지 않고 종전 링크 동작으로 내려간다.
    filterable = LAST_DOCS.some(function (d) {
      return d && d.categories && d.categories.length;
    });
    renderStats(data.totals || {});
    renderCategories(data.by_category || [], LAST_NAME);
    renderRepeats(data.repeats || []);
    renderYears(data.by_year || []);
    renderFilter();
    renderTimeline();
  }

  function rpcEndpoint(name) {
    return url.replace(/\/$/, "") + "/rest/v1/rpc/" + name;
  }

  function fetchFirmProfile(firmKey) {
    return fetch(rpcEndpoint("findings_firm_profile"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_firm_key: firmKey }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_firm_profile " + r.status);
      return r.json();
    });
  }

  // ── [이름으로 조회] 041 findings_firm_search ─────────────────────────────────
  // trends.js 일곱 번째 블록에 있던 폼을 여기로 옮겼다. 통계 페이지 한가운데 있던
  // 조회 도구를 **조회 대상 페이지 자체**의 랜딩으로 올린 것 — 그래야 이 URL 을 그냥
  // 링크하는 것만으로 진입로가 생긴다(재편 전 정적 인바운드 링크 0개).
  function buildLookupRow(item) {
    var a = document.createElement("a");
    a.className = "tr-look-row";
    a.href = "?key=" + encodeURIComponent(item.firm_key || "");
    var name = el("span", "tr-look-name", decodeFirmDisplay(item.firm_name));
    a.appendChild(name);
    var meta = el("span", "tr-look-meta",
      _t("문서 {docs} · 지적 {finds}", { docs: fmtNum(item.documents), finds: fmtNum(item.findings) }));
    a.appendChild(meta);
    return a;
  }

  function renderLookupResult(payload, q) {
    if (!lookResEl) return;
    lookResEl.innerHTML = "";
    var items = (payload && payload.items) || [];
    if (!items.length) {
      lookResEl.appendChild(el("p", "tr-look-empty",
        _t("‘{q}’ (으)로 찾은 업체가 없습니다. 영문 상호의 일부만 넣어 보세요.", { q: q })));
      return;
    }
    lookResEl.appendChild(el("p", "tr-look-empty",
      _t("‘{q}’ 검색 결과 {n}곳 — 이름을 누르면 그 업체의 이력으로 갑니다.", { q: q, n: fmtNum(items.length) })));
    items.forEach(function (it) { lookResEl.appendChild(buildLookupRow(it)); });
  }

  function runLookup() {
    if (!lookInputEl || !lookResEl) return;
    var q = (lookInputEl.value || "").trim();
    if (q.length < 2) {
      lookResEl.innerHTML = "";
      lookResEl.appendChild(el("p", "tr-look-empty", _t("두 글자 이상 입력해 주세요.")));
      return;
    }
    lookResEl.innerHTML = "";
    lookResEl.appendChild(el("p", "tr-look-empty", _t("찾는 중…")));
    fetch(rpcEndpoint("findings_firm_search"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_q: q, p_limit: 20 }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_firm_search " + r.status);
      return r.json();
    }).then(function (payload) {
      renderLookupResult(payload, q);
    }).catch(function () {
      lookResEl.innerHTML = "";
      lookResEl.appendChild(el("p", "tr-look-empty", _t("업체 검색을 불러오지 못했습니다.")));
    });
  }

  if (lookFormEl) {
    lookFormEl.addEventListener("submit", function (ev) {
      ev.preventDefault();
      runLookup();
    });
  }

  // ── [063] FDA GMP 실사 이력 ─────────────────────────────────────────────────
  // fda_inspection_firm(p_firm_key) — 지적 이력과 **단위가 다른**(실사 건의 등급)
  // 별도 소스라 별도 섹션에 그린다. 서로 나누지 않는다.
  var INSP_GRADE_KO = { NAI: _t("적합"), VAI: _t("경미"), OAI: _t("중대") };
  var INSP_ROWS = 20;

  function buildInspRow(r) {
    var row = el("div", "fp-insp-row");
    row.appendChild(el("span", "fp-insp-date", r.inspection_end_date || ""));
    // [P1.5-3] 한 축에 문서와 실사가 섞이므로 **무엇이고 그 날짜가 무슨 날인지**를
    // 행마다 말한다 — 문서는 공개일, 실사는 실사 종료일이라 의미가 다르다.
    row.appendChild(el("span", "fp-tl-when", _t("실사 종료")));
    row.appendChild(el("span", "fp-tl-kind insp", _t("실사")));
    var code = String(r.classification_code || "");
    var grade = el("span", "fp-insp-grade " + code.toLowerCase(),
      code + (INSP_GRADE_KO[code] ? " " + INSP_GRADE_KO[code] : ""));
    row.appendChild(grade);
    var site = el("span", "fp-insp-site", r.legal_name || "");
    var loc = [r.city, r.state, r.country_name].filter(Boolean).join(", ");
    if (loc) {
      site.appendChild(document.createTextNode(" "));
      site.appendChild(el("span", "loc", loc));
    }
    row.appendChild(site);
    if (r.citations_posted) row.appendChild(el("span", "fp-insp-cit", _t("지적서 공개")));
    return row;
  }

  // [P1.5-3] 실사 응답이 도착했다 — 목록 그리기는 타임라인이 맡고, 여기서는
  // **타임라인이 표현할 수 없는 것**만 문장으로 남긴다: 등급 구성 요약과 수집 범위.
  // (누적 합계·OAI 건수는 시간축 위에서는 읽히지 않는다.)
  function renderInspections(data) {
    var d = data || {};
    var t = d.totals || {};
    var scope = d.scope || {};
    var total = Number(t.inspections || 0);
    // 범위 문자열 — "없음"을 말할 때도 쓰므로 scope 에서만 만든다(하드코딩 금지).
    var range = "";
    if (scope.fiscal_year_min && scope.fiscal_year_max) {
      range = _t("FY{min}~FY{max} FDA 의약품 GMP 실사",
        { min: scope.fiscal_year_min, max: scope.fiscal_year_max });
    }
    // ★상한을 넘는 분은 타임라인에서도 자른다 — 요약이 말하는 건수와 목록의 줄 수가
    //   어긋나면 어느 쪽이 맞는지 알 수 없게 된다. 자른 사실은 아래 각주가 밝힌다.
    LAST_INSP = { inspections: (d.inspections || []).slice(0, INSP_ROWS) };
    renderTimeline();

    if (total === 0) {
      // ★"실사 기록 없음"이 아니라 "**확인된** 기록 없음 + 범위"다. 이 표는 FY2020
      //   이후 GMP 실사만 담으므로 범위 없는 부재 단정은 거짓이 된다(부재 어휘 규율).
      //   범위 문자열조차 없으면(구버전 응답) 아무 말도 하지 않는다 — 좁힐 수 없는
      //   부재 문장을 싣지 않는다.
      if (inspSubEl) {
        inspSubEl.textContent = range
          ? _t("확인된 {range} 기록이 없습니다. 그 이전 실사나 다른 유형의 실사는 이 범위 밖입니다.", { range: range })
          : "";
      }
      if (inspNoteEl) { inspNoteEl.textContent = ""; inspNoteEl.hidden = true; }
      return;
    }
    if (inspSubEl) {
      var sub_ = _t("실사 {n}건", { n: total });
      if (Number(t.sites || 0) > 1) sub_ += _t(" · 사업장 {n}곳", { n: t.sites });
      sub_ += _t(" · 중대 지적(OAI) {n}건", { n: Number(t.oai || 0) });
      if (t.first_inspection_end_date && t.last_inspection_end_date) {
        sub_ += " · " + t.first_inspection_end_date + " ~ " + t.last_inspection_end_date;
      }
      inspSubEl.textContent = sub_;
    }
    if (inspNoteEl) {
      var note = "";
      if (total > INSP_ROWS) {
        note += _t("실사는 최근 {rows}건만 시간축에 올렸습니다(전체 {total}건). ", { rows: INSP_ROWS, total: total });
      }
      if (range) note += _t("실사 범위: {range} — 그 이전 실사는 담겨 있지 않습니다.", { range: range });
      inspNoteEl.textContent = note;
      inspNoteEl.hidden = !note;
    }
  }

  function fetchInspectionHistory(firmKey) {
    return fetch(rpcEndpoint("fda_inspection_firm"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_firm_key: firmKey }),
    }).then(function (r) {
      if (!r.ok) throw new Error("fda_inspection_firm " + r.status);
      return r.json();
    });
  }

  var firmKeyParam = getFirmKeyParam();

  if (!url || !key) {
    // env(SUPABASE_URL/ANON_KEY) 미설정 — findings.js/trends.js 와 동일한 "준비 중" 폴백.
    showState("loading");
    loadingEl.textContent = _t("업체 프로파일 준비 중입니다.");
  } else if (!firmKeyParam) {
    // [존 재편] 키가 없으면 "찾을 수 없음"이 아니라 **조회 랜딩**이다. 재편 전에는
    // 여기서 막다른 안내로 끝나 페이지가 스스로 진입로가 되지 못했다.
    showState("lookup");
    if (lookInputEl) lookInputEl.focus();
  } else {
    showState("loading");
    fetchFirmProfile(firmKeyParam)
      .then(function (data) {
        // 013 은 미존재 firm_key 에도 에러 없이 빈 구조(display_name "")를 반환한다
        // (계약, 013_findings_firm_key.sql §(C) 참조) — 그 경우만 "찾을 수 없음".
        if (!data || typeof data !== "object" || !(data.display_name || "")) {
          showState("notfound");
          return;
        }
        renderAll(data);
        showState("content");
        // 워치리스트는 프로파일 로드 성공 후에만 배선(실패해도 본기능 무장애 — 내부에서
        // env/lib/세션/015 미적용을 각각 방어하고 조용히 hidden 유지).
        initWatchlist(firmKeyParam, data.display_name || "");
        // [063] FDA 실사 이력 — 본기능과 독립 promise 체인. 실패(미적용 라이브의
        // 404 포함)해도 이 블록만 hidden 으로 남는다.
        fetchInspectionHistory(firmKeyParam)
          .then(function (insp) { renderInspections(insp); })
          .catch(function () { /* 조용히 숨김 유지 */ });
      })
      .catch(function () {
        // RPC 404(013 미적용 라이브)·network 실패 — "찾을 수 없음"이 아니라 "준비 중".
        showState("error");
      });
  }
})();
