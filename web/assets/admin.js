(function () {
  "use strict";

  var root = document.getElementById("grm-admin");
  var cfg = document.getElementById("grm-admin-cfg");
  if (!root || !cfg) return;

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () {
      document.documentElement.classList.add("grm-icons-ready");
    }).catch(function () {});
  } else {
    document.documentElement.classList.add("grm-icons-ready");
  }

  function byId(id) { return document.getElementById(id); }
  function qs(sel, host) { return (host || document).querySelector(sel); }
  function qsa(sel, host) { return Array.prototype.slice.call((host || document).querySelectorAll(sel)); }
  function txt(id, value) { var n = byId(id); if (n) n.textContent = value == null ? "" : String(value); }
  function hide(n, yes) { if (n) n.classList.toggle("admin-hidden", !!yes); }
  function setStatus(n, msg, type) {
    if (!n) return;
    n.textContent = msg || "";
    n.classList.toggle("err", type === "err");
    n.classList.toggle("ok", type === "ok");
  }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c];
    });
  }
  function fmtDate(value) {
    if (!value) return "-";
    var d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 19);
    return d.toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDay(value) {
    if (!value) return "-";
    var s = String(value);
    return s.length >= 10 ? s.slice(0, 10) : s;
  }
  function number(value) {
    var n = Number(value || 0);
    return Number.isFinite(n) ? n.toLocaleString("ko-KR") : "-";
  }
  function emptyRow(cols, label) {
    return '<tr><td colspan="' + cols + '"><div class="admin-empty">' + esc(label) + "</div></td></tr>";
  }
  function badge(label, kind) {
    return '<span class="admin-pill ' + esc(kind || "") + '">' + esc(label || "-") + "</span>";
  }
  function link(url, label) {
    return /^https?:\/\//i.test(String(url || "")) ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(label || "열기") + "</a>" : "-";
  }
  function runKind(run) {
    if (!run) return "warn";
    if (run.status && run.status !== "completed") return "warn";
    if (run.conclusion === "success") return "ok";
    if (["cancelled", "skipped", "neutral"].indexOf(String(run.conclusion || "")) >= 0) return "warn";
    return "bad";
  }
  function runLabel(run) {
    if (!run) return "실행 없음";
    return run.conclusion || run.status || "-";
  }
  function runDuration(run) {
    if (!run) return "-";
    var start = Date.parse(run.run_started_at || run.created_at || "");
    var end = Date.parse(run.updated_at || "");
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "-";
    var sec = Math.max(1, Math.round((end - start) / 1000));
    if (sec < 60) return sec + "초";
    return Math.floor(sec / 60) + "분 " + (sec % 60) + "초";
  }

  var ERROR_COPY = {
    missing_auth: "로그인이 필요합니다.",
    invalid_session: "세션이 만료되었습니다. 다시 로그인해 주세요.",
    forbidden: "Admin 권한이 없습니다.",
    server_not_configured: "Admin Edge Function service role 설정을 확인해야 합니다.",
    github_not_configured: "GitHub Actions 토큰 설정이 필요합니다.",
    brevo_not_configured: "Brevo API 키 설정이 필요합니다.",
    brevo_list_not_configured: "Brevo 리스트 ID 설정이 필요합니다.",
    invalid_email: "이메일 형식이 올바르지 않습니다.",
    invalid_publish_date: "발행일 형식이 올바르지 않습니다.",
    newsletter_already_dispatched: "이 발행일은 이미 실발송 요청이 기록되어 있습니다.",
    github_dispatch_failed: "GitHub Actions 실행 요청에 실패했습니다.",
    github_rerun_failed: "실패 job 재실행 요청에 실패했습니다.",
    missing_run_id: "재실행할 GitHub run ID가 없습니다.",
    workflow_not_dispatchable: "이 워크플로우는 Admin에서 직접 실행하지 않습니다.",
    brevo_request_failed: "Brevo 요청에 실패했습니다.",
    user_action_failed: "회원 조치에 실패했습니다.",
    missing_user_id: "회원 ID가 없습니다.",
    cannot_ban_self: "현재 로그인한 Admin 계정은 차단할 수 없습니다.",
    function_not_deployed: "Admin 백엔드 함수가 아직 배포되지 않았습니다."
  };
  function errText(error) {
    if (!error) return "요청에 실패했습니다.";
    var data = error.data || {};
    var key = data.error || error.error;
    if (key && ERROR_COPY[key]) return ERROR_COPY[key];
    if (data.message) return data.message;
    if (error.message) return error.message;
    if (key) return key;
    return "요청에 실패했습니다.";
  }
  function toast(msg) {
    var host = byId("grm-admin-toast");
    if (!host || !msg) return;
    var p = document.createElement("p");
    p.textContent = msg;
    host.appendChild(p);
    setTimeout(function () { p.remove(); }, 3800);
  }

  var supabaseUrl = (cfg.getAttribute("data-supabase-url") || "").replace(/\/+$/, "");
  var anonKey = cfg.getAttribute("data-supabase-anon-key") || "";
  var indexUrl = cfg.getAttribute("data-index") || "/assets/search-index.json";
  var adminEmail = cfg.getAttribute("data-admin-email") || "yeomminho1472@gmail.com";
  var state = {
    client: null,
    session: null,
    latest: null,
    index: null,
    users: [],
    subscribers: [],
    dispatches: [],
    audit: [],
    reactions: { totals: {}, topCards: [] },
    runs: [],
    ops: null,
    checks: [],
    health: { supabase: null, github: null, brevo: null },
    backendProbe: null
  };

  if (!window.supabase || !window.supabase.createClient || !supabaseUrl || !anonKey) {
    setStatus(byId("grm-admin-login-status"), "Admin 환경변수가 설정되지 않았습니다.", "err");
    txt("grm-admin-live", "설정 필요");
    return;
  }
  state.client = window.supabase.createClient(supabaseUrl, anonKey);
  var functionsBase = supabaseUrl + "/functions/v1/";

  function api(path, options) {
    options = options || {};
    return state.client.auth.getSession().then(function (res) {
      var session = res.data && res.data.session;
      if (!session || !session.access_token) throw new Error("로그인이 필요합니다.");
      var headers = options.headers || {};
      headers.Authorization = "Bearer " + session.access_token;
      if (options.json) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(options.json);
      }
      return fetch(functionsBase + path, { method: options.method || "GET", headers: headers, body: options.body || null });
    }).then(function (res) {
      return res.text().then(function (raw) {
        var body = {};
        if (raw) {
          try { body = JSON.parse(raw); } catch (_) { body = { raw: raw }; }
        }
        if (!res.ok) throw { status: res.status, data: body };
        return body;
      });
    });
  }

  function setLive(label, kind) {
    var n = byId("grm-admin-live");
    if (!n) return;
    n.className = "admin-pill " + (kind || "");
    n.innerHTML = '<i class="ti ti-activity"></i>' + esc(label);
  }
  function renderActivationPanel() {
    var host = byId("grm-admin-activation");
    if (!host) return;
    var probe = state.backendProbe || {};
    host.hidden = !!probe.ok;
    if (probe.ok) return;
    var deployUrl = "https://github.com/MINHOYEOM/grm-api-intake/actions/workflows/grm-admin-backend-deploy.yml";
    var backendLabel = probe.error === "function_not_deployed" ? "미배포" : "확인 필요";
    var backendKind = probe.error === "function_not_deployed" ? "bad" : "warn";
    host.innerHTML = '<h3>운영 API 활성화 요건</h3><div class="admin-activation-grid">' +
      '<div class="admin-check"><span>Edge Function Secrets<br><code>SUPABASE_SERVICE_ROLE_KEY · GITHUB_ACTIONS_TOKEN · NEWSLETTER_API_KEY</code></span>' +
      badge("점검", "warn") + "</div>" +
      '<div class="admin-check"><span>Newsletter List<br><code>GRM_NEWSLETTER_LIST_ID</code></span>' +
      badge("점검", "warn") + "</div>" +
      '<div class="admin-check"><span>Backend Deploy<br><code><a href="' + deployUrl + '" target="_blank" rel="noopener">GRM Admin Backend Deploy</a></code></span>' +
      badge(backendLabel, backendKind) + "</div>" +
      '<div class="admin-check"><span>Admin Email<br><code>' + esc(adminEmail) + '</code></span>' +
      badge("고정", "ok") + "</div>" +
      "</div>";
  }
  function renderLoginReadiness() {
    var host = byId("grm-admin-readiness");
    if (!host) return;
    var probe = state.backendProbe || {};
    var backendKind = "warn";
    var backendLabel = "확인 중";
    var backendDetail = "Supabase Edge Functions";
    if (probe.ok) {
      backendKind = "ok";
      backendLabel = "배포됨";
      backendDetail = probe.detail || "인증 대기";
    } else if (probe.status === 404 || probe.error === "function_not_deployed") {
      backendKind = "bad";
      backendLabel = "미배포";
      backendDetail = "GRM Admin Backend Deploy 필요";
    } else if (probe.status >= 500) {
      backendKind = "bad";
      backendLabel = "설정 필요";
      backendDetail = probe.detail || "Edge Function secret 확인";
    } else if (probe.error) {
      backendKind = "bad";
      backendLabel = "확인 필요";
      backendDetail = probe.detail || probe.error;
    }
    host.innerHTML = [
      '<div class="admin-check"><span>Admin Web Config<br><code>' + esc(supabaseUrl.replace(/^https:\/\//, "")) + '</code></span>' +
        badge(supabaseUrl && anonKey ? "정상" : "설정 필요", supabaseUrl && anonKey ? "ok" : "bad") + "</div>",
      '<div class="admin-check"><span>Admin Backend<br><code>' + esc(backendDetail) + '</code></span>' +
        badge(backendLabel, backendKind) + "</div>"
    ].join("");
    renderActivationPanel();
  }
  function probeBackend() {
    state.backendProbe = { ok: false, detail: "확인 중" };
    renderLoginReadiness();
    return fetch(functionsBase + "admin-supabase?action=me", {
      headers: { Authorization: "Bearer grm-admin-probe" }
    }).then(function (res) {
      return res.text().then(function (raw) {
        var body = {};
        if (raw) {
          try { body = JSON.parse(raw); } catch (_) { body = { raw: raw }; }
        }
        if (res.status === 401 || res.status === 403) {
          state.backendProbe = { ok: true, status: res.status, detail: "인증 응답 정상" };
        } else if (res.status === 404) {
          state.backendProbe = { ok: false, status: res.status, error: "function_not_deployed", detail: "Edge Function 404" };
        } else {
          state.backendProbe = { ok: res.ok, status: res.status, error: body.error || "", detail: errText({ data: body }) };
        }
        renderLoginReadiness();
      });
    }).catch(function (error) {
      state.backendProbe = { ok: false, error: "function_not_deployed", detail: "Edge Function 응답 없음" };
      renderLoginReadiness();
    });
  }
  function requireBackendReady() {
    var probe = state.backendProbe || {};
    if (probe.ok) return true;
    var message = probe.detail === "확인 중"
      ? "운영 API 상태 확인 중입니다. 잠시 후 다시 시도하세요."
      : "운영 API가 아직 활성화되지 않았습니다. Admin Backend Deploy와 Edge Function secrets 상태를 확인한 뒤 다시 시도하세요.";
    setStatus(byId("grm-admin-login-status"), message, "err");
    renderActivationPanel();
    return false;
  }
  function showLogin(message, type) {
    hide(byId("grm-admin-login"), false);
    hide(byId("grm-admin-dashboard"), true);
    hide(byId("grm-admin-signout"), true);
    byId("grm-admin-email").className = "admin-pill";
    byId("grm-admin-email").innerHTML = '<i class="ti ti-lock"></i>로그인 필요';
    setLive("로그인 필요", "warn");
    setStatus(byId("grm-admin-login-status"), message || "", type || "");
  }
  function showDashboard() {
    hide(byId("grm-admin-login"), true);
    hide(byId("grm-admin-dashboard"), false);
    hide(byId("grm-admin-signout"), false);
    var email = state.session && state.session.user ? state.session.user.email : "Admin";
    byId("grm-admin-email").className = "admin-pill ok";
    byId("grm-admin-email").innerHTML = '<i class="ti ti-shield-check"></i>' + esc(email || "Admin");
    setLive("Admin 연결됨", "ok");
    setStatus(byId("grm-admin-login-status"), "", "");
  }

  function setTab(name) {
    qsa("#grm-admin-tabs button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === name); });
    qsa("[data-panel]").forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== name; });
  }

  function loadIndex() {
    return fetch(indexUrl).then(function (res) {
      if (!res.ok) throw new Error("search-index.json 로드 실패");
      return res.json();
    }).then(function (idx) {
      state.index = idx;
      var issues = idx.issues || [];
      state.latest = issues.filter(function (x) { return x.latest; })[0] || issues[0] || null;
      if (state.latest) {
        txt("grm-latest-label", "최신호 " + state.latest.date);
        txt("grm-newsletter-title", state.latest.title || "최신 규제뉴스");
        txt("grm-newsletter-date", "Vol. " + state.latest.issue_no + " · " + state.latest.date + " · 카드 " + state.latest.count + "장");
      }
      renderInsights();
      renderContentChecks();
      renderSystemChecks();
      byId("grm-index-state").className = "admin-pill ok";
      txt("grm-index-state", "인덱스 정상");
    }).catch(function (error) {
      byId("grm-index-state").className = "admin-pill bad";
      txt("grm-index-state", "인덱스 오류");
      setStatus(byId("grm-newsletter-status"), errText(error), "err");
    });
  }

  function loadOverview() {
    return api("admin-supabase?action=overview").then(function (data) {
      state.users = data.users || [];
      state.dispatches = data.dispatches || [];
      state.audit = data.audit || [];
      state.reactions = data.reactions || { totals: {}, topCards: [] };
      txt("grm-kpi-users", number((data.counts || {}).users || state.users.length));
      txt("grm-kpi-hearts", number((state.reactions.totals || {}).heart || 0));
      txt("grm-kpi-scraps", number((state.reactions.totals || {}).scrap || 0));
      txt("grm-kpi-dispatch", state.dispatches[0] ? fmtDay(state.dispatches[0].created_at) : "-");
      renderUsers();
      renderDispatches();
      renderAudit();
      renderInsights();
      renderSystemChecks();
    }).catch(function (error) {
      setStatus(byId("grm-users-status"), errText(error), "err");
      renderSystemChecks([{ name: "Supabase Admin API", ok: false, detail: errText(error) }]);
    });
  }
  function loadUsersOnly() {
    setStatus(byId("grm-users-status"), "회원 목록을 새로 불러오는 중", "");
    return api("admin-supabase?action=users&limit=100").then(function (data) {
      state.users = data.users || [];
      txt("grm-kpi-users", number(data.count || state.users.length));
      renderUsers();
      setStatus(byId("grm-users-status"), "회원 목록을 갱신했습니다.", "ok");
    }).catch(function (error) { setStatus(byId("grm-users-status"), errText(error), "err"); });
  }
  function loadSubscribers() {
    return api("admin-brevo?action=subscribers&limit=100").then(function (data) {
      state.subscribers = data.contacts || [];
      txt("grm-kpi-subscribers", number(data.count == null ? state.subscribers.length : data.count));
      renderSubscribers();
      renderSystemChecks();
    }).catch(function (error) {
      txt("grm-kpi-subscribers", "-");
      var body = byId("grm-subscribers-body");
      if (body) body.innerHTML = emptyRow(5, errText(error));
      setStatus(byId("grm-subscribers-status"), errText(error), "err");
      renderSystemChecks([{ name: "Brevo API", ok: false, detail: errText(error) }]);
    });
  }
  function loadRuns() {
    return api("admin-github?action=ops").then(function (data) {
      state.ops = data;
      state.runs = data.runs || [];
      byId("grm-github-state").className = "admin-pill ok";
      txt("grm-github-state", "GitHub 연결됨");
      renderOpsMonitor();
      renderRuns();
      renderSystemChecks();
    }).catch(function (error) {
      state.ops = null;
      state.runs = [];
      byId("grm-github-state").className = "admin-pill bad";
      txt("grm-github-state", "GitHub 설정 필요");
      renderOpsMonitor(errText(error));
      var body = byId("grm-runs-body");
      if (body) body.innerHTML = emptyRow(5, errText(error));
      renderSystemChecks([{ name: "GitHub Actions API", ok: false, detail: errText(error) }]);
    });
  }
  function loadHealth() {
    return Promise.allSettled([
      api("admin-supabase?action=health"),
      api("admin-github?action=health"),
      api("admin-brevo?action=health")
    ]).then(function (results) {
      state.health.supabase = results[0].status === "fulfilled" ? results[0].value : { ok: false, error: errText(results[0].reason) };
      state.health.github = results[1].status === "fulfilled" ? results[1].value : { ok: false, error: errText(results[1].reason) };
      state.health.brevo = results[2].status === "fulfilled" ? results[2].value : { ok: false, error: errText(results[2].reason) };
      renderSystemChecks();
    });
  }
  function refreshAll() {
    return Promise.allSettled([loadIndex(), loadOverview(), loadSubscribers(), loadRuns(), loadHealth()]).then(function () {
      renderSystemChecks();
    });
  }

  function cardTitleMap() {
    var map = {};
    var cards = state.index && state.index.cards ? state.index.cards : [];
    cards.forEach(function (card) {
      var href = card.href || "";
      var id = href.indexOf("#") >= 0 ? href.split("#").pop() : "";
      if (id) map[id] = card;
    });
    return map;
  }
  function renderInsights() {
    var host = byId("grm-top-cards");
    if (!host) return;
    var top = (state.reactions && state.reactions.topCards) || [];
    if (!top.length) {
      host.innerHTML = '<div class="admin-empty">아직 반응 데이터가 없습니다.</div>';
      return;
    }
    var map = cardTitleMap();
    var max = Math.max.apply(null, top.map(function (x) { return x.total || 0; }).concat([1]));
    host.innerHTML = top.slice(0, 8).map(function (row) {
      var card = map[row.card_id] || {};
      var title = card.target || row.card_id;
      var meta = (row.heart || 0) + " 하트 · " + (row.scrap || 0) + " 스크랩";
      var w = Math.max(3, Math.round(((row.total || 0) / max) * 100));
      return '<div class="admin-bar"><div><div class="admin-bar-title" title="' + esc(title) + '">' +
        esc(title) + '</div><div class="admin-bar-track"><div class="admin-bar-fill" style="width:' + w + '%"></div></div></div>' +
        '<span class="admin-pill">' + esc(meta) + '</span></div>';
    }).join("");
  }
  function renderContentChecks() {
    var host = byId("grm-content-checks");
    if (!host) return;
    var idx = state.index || {};
    var latest = state.latest || {};
    var items = [
      ["최신호", latest.date || "-", !!latest.date],
      ["카드 수", latest.count == null ? "-" : latest.count + "장", latest.count > 0],
      ["검색 인덱스", ((idx.cards || []).length || 0) + "개 카드", (idx.cards || []).length > 0],
      ["기관 facet", ((idx.facets || {}).agencies || []).length + "개", true]
    ];
    host.innerHTML = items.map(function (x) {
      return '<div class="admin-check"><span>' + esc(x[0]) + '</span>' + badge(x[1], x[2] ? "ok" : "bad") + "</div>";
    }).join("");
  }
  function renderSystemChecks(extra) {
    var host = byId("grm-system-checks");
    if (!host) return;
    var supaHealth = state.health.supabase || {};
    var githubHealth = state.health.github || {};
    var brevoHealth = state.health.brevo || {};
    var opsWarnings = ((state.ops && state.ops.configuration_warnings) || []).length;
    var workflowChecks = (githubHealth.workflows || []).map(function (w) {
      return {
        name: "Workflow · " + (w.label || w.action || w.workflow),
        ok: !!w.ok,
        detail: (w.workflow || "-") + (w.state ? " · " + w.state : "")
      };
    });
    var dbChecks = (supaHealth.checks || []).map(function (c) {
      return {
        name: "DB · " + c.name,
        ok: !!c.ok,
        detail: c.error || ((c.count == null ? "-" : c.count) + " rows")
      };
    });
    var checks = [
      { name: "Supabase URL", ok: /^https:\/\/.+\.supabase\.co$/i.test(supabaseUrl), detail: supabaseUrl.replace(/^https:\/\//, "") },
      { name: "Admin Edge Function", ok: !!(state.backendProbe && state.backendProbe.ok), detail: state.backendProbe ? (state.backendProbe.detail || state.backendProbe.status || "-") : "확인 전" },
      { name: "Supabase Admin API", ok: supaHealth.ok === true || (supaHealth.ok == null && !!state.session), detail: supaHealth.error || (state.session ? "Admin 세션 확인" : "로그인 필요") },
      { name: "GitHub Actions", ok: (githubHealth.ok === true || state.runs.length > 0) && !opsWarnings, detail: githubHealth.error || (opsWarnings ? opsWarnings + "개 운영 경고" : (state.runs.length ? state.runs.length + "개 실행 확인" : "워크플로우 상태 확인")) },
      { name: "Brevo 구독자", ok: brevoHealth.ok === true || state.subscribers.length > 0, detail: brevoHealth.error || (state.subscribers.length ? state.subscribers.length + "명 로드" : "리스트 연결 확인") },
      { name: "Search Index", ok: !!(state.index && state.index.cards), detail: state.index ? (state.index.cards || []).length + "개 카드" : "로드 전" }
    ].concat(dbChecks, workflowChecks, extra || []);
    host.innerHTML = checks.map(function (c) {
      return '<div class="admin-check"><span>' + esc(c.name) + '<br><code>' + esc(c.detail || "") + '</code></span>' +
        badge(c.ok ? "정상" : "확인 필요", c.ok ? "ok" : "bad") + "</div>";
    }).join("");
  }

  function renderOpsMonitor(errorMessage) {
    renderOpsSummary(errorMessage);
    renderWorkflowCards(errorMessage);
    renderOpsIncidents(errorMessage);
  }
  function renderOpsSummary(errorMessage) {
    var host = byId("grm-ops-summary");
    if (!host) return;
    if (errorMessage) {
      host.innerHTML = '<div class="admin-metric"><span><i class="ti ti-alert-triangle"></i>GitHub 연결</span><b>확인 필요</b></div>';
      return;
    }
    var summary = (state.ops && state.ops.summary) || {};
    var sourceOk = summary.source_ok === true;
    var warningTotal = summary.warning_total == null
      ? ((summary.warning_issues || 0) + (summary.configuration_warnings || 0))
      : summary.warning_total;
    var items = [
      ["규제소스", summary.source_status || "-", sourceOk ? "ok" : "bad", "ti-database-import"],
      ["진행 중", number(summary.in_progress || 0), summary.in_progress ? "warn" : "ok", "ti-loader-2"],
      ["현재 실패", number(summary.incidents || 0), summary.incidents ? "bad" : "ok", "ti-alert-triangle"],
      ["운영 경고", number(warningTotal || 0), warningTotal ? "warn" : "ok", "ti-message-report"]
    ];
    host.innerHTML = items.map(function (item) {
      return '<div class="admin-metric"><span><i class="ti ' + esc(item[3]) + '"></i>' + esc(item[0]) +
        '</span><b class="' + esc(item[2]) + '">' + esc(item[1]) + "</b></div>";
    }).join("");
  }
  function renderWorkflowCards(errorMessage) {
    var host = byId("grm-workflow-cards");
    if (!host) return;
    if (errorMessage) {
      host.innerHTML = '<div class="admin-empty">' + esc(errorMessage) + "</div>";
      return;
    }
    var workflows = (state.ops && state.ops.workflows) || [];
    if (!workflows.length) {
      host.innerHTML = '<div class="admin-empty">워크플로우 상태를 불러오는 중입니다.</div>';
      return;
    }
    host.innerHTML = workflows.map(function (wf) {
      var run = wf.latest || null;
      var kind = wf.kind || runKind(run);
      var wfWarnings = wf.warnings || [];
      var title = run && run.display_title ? run.display_title : (run && run.event ? run.event : "최근 실행 없음");
      var status = runLabel(run);
      var runNo = run && run.run_number ? "#" + run.run_number : "-";
      var actions = [];
      var warningHtml = "";
      if (run && run.html_url) actions.push(link(run.html_url, "최근 run"));
      actions.push(link(wf.workflow_url || ("https://github.com/MINHOYEOM/grm-api-intake/actions/workflows/" + encodeURIComponent(wf.workflow || "")), "워크플로우"));
      if (run && kind === "bad") {
        actions.push('<button class="admin-mini danger" type="button" data-rerun-failed="' + esc(run.id || "") + '">실패 job 재실행</button>');
      }
      if (wfWarnings.length) {
        warningHtml = '<div class="admin-step-list">' + wfWarnings.slice(0, 2).map(function (warning) {
          var skipped = (warning.steps || []).slice(0, 3).map(function (step) {
            return step.name || "-";
          }).join(" / ");
          return "<code>" + esc((warning.title || "운영 경고") + (skipped ? " · skip: " + skipped : "")) + "</code>";
        }).join("") + "</div>";
      }
      return '<article class="admin-workflow-card ' + esc(kind) + '">' +
        '<div class="admin-workflow-top"><div><b>' + esc(wf.label || wf.workflow || "-") +
        '</b><p>' + esc(wf.purpose || "") + '</p></div>' + badge(status, kind) + "</div>" +
        '<div class="admin-meta">' +
          badge(wf.schedule || "-", "warn") +
          badge(wf.workflow_state || "active", wf.workflow_state === "active" ? "ok" : "warn") +
          badge(runNo, "") +
          badge(run ? fmtDate(run.created_at) : "실행 없음", "") +
          badge(run ? runDuration(run) : "-", "") +
        "</div>" +
        '<p title="' + esc(title) + '">' + esc(title) + "</p>" +
        warningHtml +
        '<div class="admin-card-actions">' + actions.join("") + "</div>" +
      "</article>";
    }).join("");
  }
  function renderOpsIncidents(errorMessage) {
    var host = byId("grm-ops-incidents");
    if (!host) return;
    if (errorMessage) {
      host.innerHTML = '<div class="admin-empty">' + esc(errorMessage) + "</div>";
      return;
    }
    var incidents = (state.ops && state.ops.incidents) || [];
    var warnings = (state.ops && state.ops.warning_issues) || [];
    var configWarnings = (state.ops && state.ops.configuration_warnings) || [];
    var parts = [];
    if (!incidents.length && !warnings.length && !configWarnings.length) {
      parts.push('<div class="admin-incident"><div class="admin-incident-head"><b>최근 실패 없음</b>' +
        badge("정상", "ok") + '</div><p>최근 GitHub Actions 실행에서 즉시 조치할 실패가 없습니다.</p></div>');
    }
    configWarnings.slice(0, 6).forEach(function (warning) {
      var steps = (warning.steps || []).slice(0, 5).map(function (step) {
        return "<code>" + esc((step.job_name || "job") + " · " + (step.name || "-") + " · " + (step.conclusion || step.status || "-")) + "</code>";
      }).join("");
      var actions = [];
      if (warning.run_url) actions.push(link(warning.run_url, "Run 열기"));
      actions.push(link("https://github.com/MINHOYEOM/grm-api-intake/settings/secrets/actions", "Secrets 확인"));
      parts.push('<div class="admin-incident warn"><div class="admin-incident-head"><b>' +
        esc(warning.title || "운영 설정 경고") + "</b>" + badge("설정 필요", "warn") +
        '</div><p>' + esc(warning.detail || "") + "</p>" +
        (steps ? '<div class="admin-step-list">' + steps + "</div>" : "") +
        '<div class="admin-card-actions">' + actions.join("") + "</div></div>");
    });
    incidents.slice(0, 6).forEach(function (run) {
      var jobs = run.failed_jobs || [];
      var jobHtml = "";
      if (jobs.length) {
        jobHtml = '<div class="admin-step-list">' + jobs.slice(0, 4).map(function (job) {
          var steps = (job.failed_steps || []).slice(0, 3).map(function (step) {
            return step.name + " · " + (step.conclusion || step.status || "-");
          }).join(" / ");
          return "<code>" + esc((job.name || "job") + " · " + (job.conclusion || "-") + (steps ? " · " + steps : "")) + "</code>";
        }).join("") + "</div>";
      }
      parts.push('<div class="admin-incident bad"><div class="admin-incident-head"><b>' +
        esc(run.workflow_name || run.workflow_id || "Workflow") + "</b>" + badge(run.conclusion || run.status || "-", "bad") +
        '</div><p>' + esc(fmtDate(run.created_at)) + " · " + esc(run.display_title || run.event || "") + "</p>" +
        jobHtml + '<div class="admin-card-actions">' + link(run.html_url, "GitHub에서 보기") +
        '<button class="admin-mini danger" type="button" data-rerun-failed="' + esc(run.id || "") + '">실패 job 재실행</button></div></div>');
    });
    warnings.slice(0, 4).forEach(function (issue) {
      var detail = issue.detail || "";
      var meta = [issue.title || "", fmtDate(issue.updated_at)].filter(Boolean).join(" · ");
      var actions = [link(issue.html_url, "Issue 열기")];
      if (issue.latest_run_url) actions.push(link(issue.latest_run_url, "최신 Run"));
      parts.push('<div class="admin-incident"><div class="admin-incident-head"><b>운영 경고 Issue #' +
        esc(issue.number || "-") + "</b>" + badge("open", "warn") + '</div><p>' + esc(meta) +
        '</p>' + (detail ? '<p>' + esc(detail) + "</p>" : "") +
        '<div class="admin-card-actions">' + actions.join("") + "</div></div>");
    });
    host.innerHTML = parts.join("");
  }

  function renderDispatches() {
    var body = byId("grm-dispatch-body");
    if (!body) return;
    var rows = state.dispatches || [];
    if (!rows.length) { body.innerHTML = emptyRow(5, "발송 요청 내역 없음"); return; }
    body.innerHTML = rows.slice(0, 12).map(function (row) {
      var status = row.github_status || "-";
      var ok = Number(status) >= 200 && Number(status) < 300;
      var runStatus = row.github_run_conclusion || row.github_run_status || "-";
      var runKind = row.github_run_conclusion === "success" ? "ok" : (row.github_run_conclusion ? "bad" : "warn");
      return "<tr><td>" + esc(fmtDay(row.publish_date)) + "</td><td>" + badge(status, ok ? "ok" : "warn") +
        "</td><td>" + badge(runStatus, runKind) + "</td><td>" + esc(fmtDate(row.created_at)) +
        "</td><td>" + link(row.github_run_url, row.github_run_id ? "#" + row.github_run_id : "열기") + "</td></tr>";
    }).join("");
  }
  function renderAudit() {
    var body = byId("grm-audit-body");
    if (!body) return;
    var rows = state.audit || [];
    if (!rows.length) { body.innerHTML = emptyRow(4, "감사 로그 없음"); return; }
    body.innerHTML = rows.slice(0, 30).map(function (row) {
      var details = row.details ? JSON.stringify(row.details).slice(0, 160) : "";
      return "<tr><td>" + esc(fmtDate(row.created_at)) + "</td><td>" + esc(row.action) +
        "</td><td>" + esc(row.target_type || "-") + "</td><td><code>" + esc(details) + "</code></td></tr>";
    }).join("");
  }
  function renderRuns() {
    var body = byId("grm-runs-body");
    if (!body) return;
    var runs = state.runs || [];
    if (!runs.length) { body.innerHTML = emptyRow(5, "워크플로우 실행 내역 없음"); return; }
    body.innerHTML = runs.slice(0, 24).map(function (run) {
      var kind = runKind(run);
      return "<tr><td>" + esc(run.workflow_name || run.workflow_id || "-") + "</td><td>" +
        badge(run.conclusion || run.status || "-", kind) + "</td><td>" + esc(run.head_branch || "-") +
        "</td><td>" + esc(fmtDate(run.created_at)) + "</td><td>" + link(run.html_url, "열기") + "</td></tr>";
    }).join("");
  }
  function renderSubscribers() {
    var body = byId("grm-subscribers-body");
    if (!body) return;
    var q = (byId("grm-subscriber-filter") && byId("grm-subscriber-filter").value || "").toLowerCase();
    var rows = (state.subscribers || []).filter(function (c) { return !q || String(c.email || "").toLowerCase().indexOf(q) >= 0; });
    if (!rows.length) { body.innerHTML = emptyRow(5, "구독자 내역 없음"); return; }
    body.innerHTML = rows.map(function (c) {
      var black = !!c.emailBlacklisted;
      return "<tr><td>" + esc(c.email || "-") + "</td><td>" + badge(black ? "수신거부" : "구독", black ? "bad" : "ok") +
        "</td><td>" + esc(fmtDate(c.createdAt)) + "</td><td>" + esc(fmtDate(c.modifiedAt)) +
        '</td><td><div class="admin-row-actions"><button class="admin-mini danger" type="button" data-remove-subscriber="' +
        esc(c.email || "") + '">목록 제거</button></div></td></tr>';
    }).join("");
  }
  function renderUsers() {
    var body = byId("grm-users-body");
    if (!body) return;
    var q = (byId("grm-user-filter") && byId("grm-user-filter").value || "").toLowerCase();
    var rows = (state.users || []).filter(function (u) { return !q || String(u.email || "").toLowerCase().indexOf(q) >= 0; });
    if (!rows.length) { body.innerHTML = emptyRow(5, "회원 내역 없음"); return; }
    body.innerHTML = rows.map(function (u) {
      var confirmed = !!u.email_confirmed_at;
      var banned = !!u.banned_until;
      var status = banned ? badge("차단", "bad") : badge(confirmed ? "활성" : "미인증", confirmed ? "ok" : "warn");
      var actions = '<button class="admin-mini" type="button" data-user-action="confirm_user" data-user-id="' + esc(u.id) + '">인증</button>';
      actions += banned
        ? '<button class="admin-mini" type="button" data-user-action="unban_user" data-user-id="' + esc(u.id) + '">차단 해제</button>'
        : '<button class="admin-mini danger" type="button" data-user-action="ban_user" data-user-id="' + esc(u.id) + '">차단</button>';
      return "<tr><td>" + esc(u.email || "-") + "</td><td>" + status + "</td><td>" + esc(fmtDate(u.created_at)) +
        "</td><td>" + esc(fmtDate(u.last_sign_in_at)) + '</td><td><div class="admin-row-actions">' + actions + "</div></td></tr>";
    }).join("");
  }

  function confirmDispatch(action, publishDate) {
    if (action === "newsletter_send") {
      return window.confirm("구독자 전체에게 최신 뉴스레터" + (publishDate ? " (" + publishDate + ")" : "") + "를 실제 발송합니다. 계속할까요?");
    }
    if (action === "web_deploy") return window.confirm("현재 main 기준으로 웹 재배포 워크플로우를 실행할까요?");
    if (action === "intake_run") return window.confirm("규제 소스 수집 워크플로우를 수동 실행할까요?");
    if (action === "brief_audit") return window.confirm("발행본 provenance 감사 워크플로우를 실행할까요?");
    return true;
  }

  function dispatch(action, button) {
    var payload = { action: action };
    if (action === "newsletter_send") {
      if (!state.latest || !state.latest.date) {
        setStatus(byId("grm-ops-status"), "발송할 최신호가 없습니다.", "err");
        return;
      }
      payload.publish_date = state.latest.date;
    }
    if (!confirmDispatch(action, payload.publish_date)) return;
    if (button) button.disabled = true;
    setStatus(byId("grm-ops-status"), "워크플로우 실행 요청 중", "");
    return api("admin-github", { method: "POST", json: payload }).then(function (data) {
      toast((data.label || "워크플로우") + " 실행을 요청했습니다.");
      setStatus(byId("grm-ops-status"), "실행 요청 완료: " + (data.label || action), "ok");
      if (action === "newsletter_send") setStatus(byId("grm-newsletter-status"), "뉴스레터 실발송 워크플로우를 요청했습니다.", "ok");
      return Promise.allSettled([loadRuns(), loadOverview()]);
    }).catch(function (error) {
      var message = errText(error);
      if (error && error.status === 409 && error.data && error.data.existing) {
        message += " (" + fmtDate(error.data.existing.created_at) + ")";
      }
      setStatus(byId("grm-ops-status"), message, "err");
      if (action === "newsletter_send") setStatus(byId("grm-newsletter-status"), message, "err");
    }).finally(function () { if (button) button.disabled = false; });
  }
  function rerunFailed(runId, button) {
    if (!runId) return;
    if (!window.confirm("이 GitHub Actions run의 실패한 job만 다시 실행할까요?")) return;
    if (button) button.disabled = true;
    setStatus(byId("grm-ops-status"), "실패 job 재실행 요청 중", "");
    return api("admin-github", { method: "POST", json: { action: "rerun_failed", run_id: runId } }).then(function (data) {
      toast("실패 job 재실행을 요청했습니다.");
      setStatus(byId("grm-ops-status"), "재실행 요청 완료: run #" + (data.run_id || runId), "ok");
      return loadRuns();
    }).catch(function (error) {
      setStatus(byId("grm-ops-status"), errText(error), "err");
    }).finally(function () { if (button) button.disabled = false; });
  }

  function subscriberAction(action, email) {
    if (action === "remove_from_list" && !window.confirm("이 구독자를 Brevo 리스트에서 제거할까요? " + (email || ""))) return Promise.resolve();
    setStatus(byId("grm-subscribers-status"), "구독자 정보를 갱신하는 중", "");
    return api("admin-brevo", { method: "POST", json: { action: action, email: email } }).then(function () {
      toast("구독자 정보를 갱신했습니다.");
      setStatus(byId("grm-subscribers-status"), "구독자 정보를 갱신했습니다.", "ok");
      return loadSubscribers();
    }).catch(function (error) { setStatus(byId("grm-subscribers-status"), errText(error), "err"); });
  }
  function userAction(action, userId) {
    if (action === "confirm_user" && !window.confirm("이 회원의 이메일 인증 상태를 관리자가 인증 완료로 변경할까요?")) return Promise.resolve();
    if (action === "ban_user" && !window.confirm("이 회원을 즉시 차단할까요? 복구 전까지 로그인할 수 없습니다.")) return Promise.resolve();
    if (action === "unban_user" && !window.confirm("이 회원의 차단을 해제할까요?")) return Promise.resolve();
    setStatus(byId("grm-users-status"), "회원 조치를 실행하는 중", "");
    return api("admin-supabase", { method: "POST", json: { action: action, user_id: userId } }).then(function () {
      toast("회원 조치를 완료했습니다.");
      setStatus(byId("grm-users-status"), "회원 조치를 완료했습니다.", "ok");
      return loadUsersOnly();
    }).catch(function (error) { setStatus(byId("grm-users-status"), errText(error), "err"); });
  }

  qsa("#grm-admin-tabs button").forEach(function (b) { b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); }); });
  byId("grm-refresh-all").addEventListener("click", refreshAll);
  byId("grm-system-refresh").addEventListener("click", refreshAll);
  byId("grm-subscribers-refresh").addEventListener("click", loadSubscribers);
  byId("grm-users-refresh").addEventListener("click", loadUsersOnly);
  byId("grm-newsletter-send").addEventListener("click", function (e) { dispatch("newsletter_send", e.currentTarget); });
  qsa("[data-dispatch]").forEach(function (b) {
    b.addEventListener("click", function () { dispatch(b.getAttribute("data-dispatch"), b); });
  });
  ["grm-workflow-cards", "grm-ops-incidents"].forEach(function (id) {
    var host = byId(id);
    if (!host) return;
    host.addEventListener("click", function (e) {
      var b = e.target.closest("[data-rerun-failed]");
      if (b) rerunFailed(b.getAttribute("data-rerun-failed"), b);
    });
  });
  byId("grm-subscriber-filter").addEventListener("input", renderSubscribers);
  byId("grm-user-filter").addEventListener("input", renderUsers);
  byId("grm-subscriber-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var email = (e.currentTarget.elements.email.value || "").trim();
    if (!email) return;
    subscriberAction("subscribe", email).then(function () { e.currentTarget.reset(); });
  });
  byId("grm-subscribers-body").addEventListener("click", function (e) {
    var b = e.target.closest("[data-remove-subscriber]");
    if (b) subscriberAction("remove_from_list", b.getAttribute("data-remove-subscriber"));
  });
  byId("grm-users-body").addEventListener("click", function (e) {
    var b = e.target.closest("[data-user-action]");
    if (b) userAction(b.getAttribute("data-user-action"), b.getAttribute("data-user-id"));
  });

  byId("grm-admin-login-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var form = e.currentTarget;
    var email = (form.elements.email.value || "").trim();
    var password = form.elements.password.value || "";
    if (!requireBackendReady()) return;
    setStatus(byId("grm-admin-login-status"), "로그인 중", "");
    state.client.auth.signInWithPassword({ email: email, password: password }).then(function (res) {
      if (res.error) throw res.error;
      state.session = res.data.session;
      return api("admin-supabase?action=me");
    }).then(function () {
      showDashboard();
      return refreshAll();
    }).catch(function (error) {
      showLogin(errText(error), "err");
    });
  });
  byId("grm-admin-signup").addEventListener("click", function () {
    var form = byId("grm-admin-login-form");
    var email = (form.elements.email.value || adminEmail).trim();
    var password = form.elements.password.value || "";
    if (email.toLowerCase() !== adminEmail.toLowerCase()) {
      setStatus(byId("grm-admin-login-status"), "최초 Admin 계정은 " + adminEmail + " 만 만들 수 있습니다.", "err");
      return;
    }
    if (password.length < 6) {
      setStatus(byId("grm-admin-login-status"), "비밀번호를 6자 이상 입력한 뒤 계정을 만드세요.", "err");
      return;
    }
    if (!requireBackendReady()) return;
    setStatus(byId("grm-admin-login-status"), "Admin 계정 생성 중", "");
    state.client.auth.signUp({ email: email, password: password }).then(function (res) {
      if (res.error) throw res.error;
      if (res.data && res.data.session) {
        state.session = res.data.session;
        return api("admin-supabase?action=me").then(function () { showDashboard(); return refreshAll(); });
      }
      setStatus(byId("grm-admin-login-status"), "확인 메일을 보냈습니다. 메일 인증 후 로그인하세요.", "ok");
    }).catch(function (error) { setStatus(byId("grm-admin-login-status"), errText(error), "err"); });
  });
  byId("grm-admin-reset").addEventListener("click", function () {
    if (!requireBackendReady()) return;
    var email = (byId("grm-admin-login-form").elements.email.value || adminEmail).trim();
    state.client.auth.resetPasswordForEmail(email).then(function (res) {
      if (res.error) throw res.error;
      setStatus(byId("grm-admin-login-status"), "비밀번호 재설정 메일을 보냈습니다.", "ok");
    }).catch(function (error) { setStatus(byId("grm-admin-login-status"), errText(error), "err"); });
  });
  byId("grm-admin-signout").addEventListener("click", function () {
    state.client.auth.signOut().finally(function () {
      state.session = null;
      showLogin("", "");
    });
  });

  probeBackend();

  state.client.auth.getSession().then(function (res) {
    state.session = res.data && res.data.session;
    if (!state.session) {
      showLogin("", "");
      loadIndex();
      return;
    }
    return api("admin-supabase?action=me").then(function () {
      showDashboard();
      return refreshAll();
    }).catch(function (error) {
      showLogin(errText(error), "err");
    });
  });
})();
