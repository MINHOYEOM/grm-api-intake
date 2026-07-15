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
  if (!cfg || !loadingEl || !errorEl || !notfoundEl || !contentEl || !nameEl ||
      !statsEl || !catEl || !yearEl || !docsEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();
  var root = (cfg.getAttribute("data-root") || "").trim();

  // grm_findings.FINDING_TAXONOMY verbatim(code -> {ko, en}) — findings.js/trends.js 의
  // 동명 상수와 동일 복제본(동기화 테스트로 드리프트 차단).
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

  function showState(which) {
    loadingEl.hidden = which !== "loading";
    errorEl.hidden = which !== "error";
    notfoundEl.hidden = which !== "notfound";
    contentEl.hidden = which !== "content";
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
  function findingsHref(paramKey, value) {
    return root + "findings/index.html?" + paramKey + "=" + encodeURIComponent(value);
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
    statsEl.appendChild(buildStat(fmtNum(totals.findings), "총 지적"));
    statsEl.appendChild(buildStat(fmtNum(totals.documents), "문서"));
    var period = (totals.first_seen || "?") + " ~ " + (totals.last_seen || "?");
    statsEl.appendChild(buildStat(period, "기간"));
    statsEl.appendChild(buildStat(fmtNum(totals.public_findings), "국문 열람 가능"));
  }

  // ── 카테고리 구성(상위 카테고리 코럴 농도 바) ────────────────────────────────
  function buildCatRow(entry, maxCnt) {
    var a = document.createElement("a");
    a.className = "fp-cat-row";
    a.href = findingsHref("cat", entry.category_code);
    var label = CATEGORY_LABELS[entry.category_code];
    a.appendChild(el("span", "fp-cat-label", label ? label.ko : entry.category_code));
    var track = document.createElement("div");
    track.className = "fp-cat-track";
    var bar = document.createElement("div");
    bar.className = "fp-cat-bar";
    var ratio = maxCnt > 0 ? entry.cnt / maxCnt : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    track.appendChild(bar);
    a.appendChild(track);
    a.appendChild(el("span", "fp-cat-count", fmtNum(entry.cnt) + "건"));
    return a;
  }

  function renderCategories(byCategory) {
    catEl.innerHTML = "";
    if (!byCategory.length) {
      catEl.appendChild(el("p", "fp-empty", "표시할 데이터가 없습니다."));
      return;
    }
    // RPC 가 이미 cnt desc 로 정렬해 반환한다(013 by_category 계약) — 재정렬 없음.
    var maxCnt = byCategory[0].cnt || 1;
    byCategory.forEach(function (c) { catEl.appendChild(buildCatRow(c, maxCnt)); });
  }

  // ── 연도 추이(간단 막대) ─────────────────────────────────────────────────
  function renderYears(byYear) {
    yearEl.innerHTML = "";
    if (!byYear.length) {
      yearEl.appendChild(el("p", "fp-empty", "표시할 데이터가 없습니다."));
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
    var catText = label ? label.ko : (row.category_label_ko || "");
    if (catText) card.appendChild(el("p", "fp-obs-cat", catText));

    var ko = (row.finding_text_ko || "").trim();
    var mainText = ko || row.finding_text || "";
    if (mainText) card.appendChild(el("p", "fp-obs-text", mainText));

    if (ko && row.finding_text) {
      var details = document.createElement("details");
      details.className = "fp-obs-orig";
      var summary = document.createElement("summary");
      summary.textContent = "원문 보기 (영문)";
      details.appendChild(summary);
      details.appendChild(el("p", null, row.finding_text));
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
    container.appendChild(el("p", "fp-doc-detail-loading", "불러오는 중…"));
  }

  function renderDocDetailError(container) {
    container.innerHTML = "";
    container.appendChild(el("p", "fp-doc-detail-empty", "지적사항을 불러오지 못했습니다."));
  }

  function renderDocDetail(container, rows) {
    container.innerHTML = "";
    if (!Array.isArray(rows) || !rows.length) {
      container.appendChild(el("p", "fp-doc-detail-empty", "공개된 지적사항이 없습니다."));
      return;
    }
    rows.forEach(function (row) { container.appendChild(buildObsCard(row)); });
  }

  function buildDocRow(doc) {
    var row = document.createElement("div");
    row.className = "fp-doc-row";

    var main = document.createElement("div");
    main.className = "fp-doc-row-main";
    main.appendChild(el("span", "fp-doc-date", doc.published_date || ""));
    if (doc.source) main.appendChild(el("span", "fp-b", doc.source));

    var canExpand = (doc.public_obs_cnt || 0) > 0;
    var obsCnt = doc.obs_cnt || 0;
    // [완역 자동 전환] 문서의 지적이 전부 국문 열람 가능하면 병기 괄호가 동어반복이자
    // 미번역이 남은 듯한 인상만 주므로 생략 — 일부만 공개된 문서(신규 수집 직후 등)에만
    // "(국문 열람 가능 M건)"을 남긴다.
    var partiallyPublic = canExpand && (doc.public_obs_cnt || 0) < obsCnt;
    var countText = "지적 " + fmtNum(obsCnt) + "건" +
      (partiallyPublic ? "(국문 열람 가능 " + fmtNum(doc.public_obs_cnt) + "건)" : "");
    main.appendChild(el("span", "fp-doc-count", countText));

    var detail = document.createElement("div");
    detail.className = "fp-doc-detail";
    detail.hidden = true;

    if (canExpand) {
      main.appendChild(el("span", "fp-doc-chev", "▸"));
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
      main.appendChild(el("span", "fp-doc-pending", "국문 번역 대기 중"));
    }

    row.appendChild(main);
    row.appendChild(detail);
    return row;
  }

  function renderDocuments(documents) {
    docsEl.innerHTML = "";
    if (!documents.length) {
      docsEl.appendChild(el("p", "fp-empty", "표시할 문서가 없습니다."));
      return;
    }
    // RPC 가 이미 published_date desc 로 정렬해 반환한다(013 documents 계약) — 재정렬 없음.
    documents.forEach(function (doc) { docsEl.appendChild(buildDocRow(doc)); });
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
      watchEl.appendChild(el("p", "fp-watch-note", "로그인하면 관심 업체로 등록할 수 있습니다"));
      var lb = document.createElement("button");
      lb.type = "button";
      lb.className = "fp-watch-login";
      lb.textContent = "로그인";
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
      btn.textContent = registered ? "관심 등록됨 · 해제" : "관심 업체 등록";
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
              ? "해제에 실패했습니다. 잠시 후 다시 시도해 주세요."
              : "등록에 실패했습니다. 관심 업체는 사용자당 최대 50개까지 등록할 수 있습니다.";
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
    renderStats(data.totals || {});
    renderCategories(data.by_category || []);
    renderYears(data.by_year || []);
    renderDocuments(data.documents || []);
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

  var firmKeyParam = getFirmKeyParam();

  if (!url || !key) {
    // env(SUPABASE_URL/ANON_KEY) 미설정 — findings.js/trends.js 와 동일한 "준비 중" 폴백.
    showState("loading");
    loadingEl.textContent = "업체 프로파일 준비 중입니다.";
  } else if (!firmKeyParam) {
    // key 파라미터 자체가 없으면 fetch 를 시도할 이유가 없다 — 바로 "찾을 수 없음".
    showState("notfound");
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
      })
      .catch(function () {
        // RPC 404(013 미적용 라이브)·network 실패 — "찾을 수 없음"이 아니라 "준비 중".
        showState("error");
      });
  }
})();
