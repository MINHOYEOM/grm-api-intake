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
  function actionLink(url, label, kind) {
    return /^https?:\/\//i.test(String(url || ""))
      ? '<a class="admin-mini ' + esc(kind || "") + '" href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(label || "열기") + "</a>"
      : "-";
  }
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  function runKind(run) {
    if (!run) return "warn";
    if (run.status && run.status !== "completed") return "warn";
    if (run.conclusion === "success") return "ok";
    if (["cancelled", "skipped", "neutral"].indexOf(String(run.conclusion || "")) >= 0) return "warn";
    return "bad";
  }
  function runLabel(run) {
    if (!run) return "실행 없음";
    var value = String(run.conclusion || run.status || "");
    var labels = {
      success: "정상",
      failure: "실패",
      startup_failure: "시작 실패",
      timed_out: "시간 초과",
      cancelled: "취소",
      skipped: "건너뜀",
      neutral: "중립",
      action_required: "조치 필요",
      queued: "대기 중",
      in_progress: "실행 중",
      requested: "요청됨",
      waiting: "대기 중",
      completed: "완료"
    };
    return labels[value] || value || "-";
  }
  function sourceStatusLabel(value, ok) {
    if (ok === true) return "정상";
    var raw = String(value || "");
    var labels = {
      success: "정상",
      failure: "실패",
      no_run: "실행 없음",
      "no-run": "실행 없음",
      in_progress: "실행 중",
      queued: "대기 중"
    };
    return labels[raw] || raw || "확인 중";
  }
  function eventLabel(value) {
    var raw = String(value || "");
    var labels = {
      workflow_dispatch: "수동 실행",
      schedule: "자동 일정",
      push: "코드 변경",
      pull_request: "PR 검증",
      repository_dispatch: "외부 요청",
      workflow_run: "연계 실행"
    };
    return labels[raw] || raw || "-";
  }
  function nextAction(kind, run, warnings) {
    if (!run) return { kind: "warn", text: "최근 실행 기록이 없습니다. GitHub Actions 연결과 워크플로우 활성 상태를 확인하세요." };
    if (kind === "bad") return { kind: "bad", text: "실패 job 로그를 확인하고, 원인이 일시적이면 실패 job 재실행을 사용하세요." };
    if (run.status && run.status !== "completed") return { kind: "warn", text: "현재 실행 중입니다. 완료 후 결과가 정상으로 바뀌는지 확인하세요." };
    if ((warnings || []).length) return { kind: "warn", text: "실행은 완료됐지만 스킵 또는 소스 경고가 있습니다. 경고 Issue와 최신 Run을 확인하세요." };
    return { kind: "ok", text: "조치 없음. 최신 실행이 정상 범위입니다." };
  }
  function workflowDisplayKind(wf) {
    var kind = wf.kind || runKind(wf.latest || null);
    return (wf.warnings || []).length && kind === "ok" ? "warn" : kind;
  }
  function workflowMeta(wf) {
    var action = String((wf && wf.action) || "");
    var group = String((wf && wf.group) || "");
    var workflow = String((wf && wf.workflow) || "").toLowerCase();
    var defaults = {
      source: { order: 1, index: "01", stage: "수집", stageKey: "source", icon: "ti-database-import", pipeline: "소스 수집", pipelineDesc: "규제 신호 확보", impact: "실패하면 새 규제 카드와 주간 소식 소재가 갱신되지 않습니다.", focus: "소스 누락, 인증 차단, 수집 결과 파일 생성 여부를 봅니다." },
      quality: { order: 2, index: "02", stage: "검증", stageKey: "quality", icon: "ti-shield-check", pipeline: "검증", pipelineDesc: "테스트와 근거 확인", impact: "실패하면 코드 회귀나 근거 검증 누락을 먼저 확인해야 합니다.", focus: "실패 테스트, provenance 경고, 링크 검증 결과를 봅니다." },
      publish: { order: 3, index: "03", stage: "배포", stageKey: "publish", icon: "ti-cloud-upload", pipeline: "웹 배포", pipelineDesc: "사이트 반영", impact: "실패하면 운영 도메인에 최신 웹사이트 변경사항이 반영되지 않습니다.", focus: "빌드, 링크체크, Cloudflare Pages 배포 단계를 봅니다." },
      newsletter: { order: 4, index: "04", stage: "발송", stageKey: "newsletter", icon: "ti-mail-forward", pipeline: "뉴스레터", pipelineDesc: "구독자 도달", impact: "실패하면 구독자에게 최신 Weekly Brief가 발송되지 않습니다.", focus: "발송 게이트, Brevo 캠페인 생성, 중복 발송 방지 결과를 봅니다." },
      admin: { order: 5, index: "05", stage: "운영 API", stageKey: "admin", icon: "ti-server-2", pipeline: "운영 API", pipelineDesc: "Admin 기능", impact: "실패하면 운영자 버튼, 회원 관리, 구독자 관리 API가 최신 상태가 아닐 수 있습니다.", focus: "Supabase migration, Edge Function secrets, 함수 배포 단계를 봅니다." },
      infra: { order: 6, index: "06", stage: "인프라", stageKey: "infra", icon: "ti-database-cog", pipeline: "인프라", pipelineDesc: "서비스 유지", impact: "실패가 반복되면 Supabase 프로젝트 휴면 방지나 기본 연결 상태를 확인해야 합니다.", focus: "정기 keepalive 실행과 Supabase 응답 상태를 봅니다." }
    };
    var meta = defaults[group] || defaults.quality;
    if (action === "brief_audit" || workflow.indexOf("brief-audit") >= 0) {
      meta = Object.assign({}, meta, { index: "02A", stage: "근거 감사", impact: "실패하면 발행된 브리프의 원문 링크와 근거 신뢰도 확인이 지연됩니다.", focus: "provenance JSON, 링크 검증, 경고 Issue 갱신 여부를 봅니다." });
    } else if (action === "ci" || workflow.indexOf("ci") >= 0) {
      meta = Object.assign({}, meta, { index: "02B", stage: "회귀 검증", impact: "실패하면 코드나 렌더 결과에 회귀 가능성이 있어 배포 전 확인이 필요합니다.", focus: "컴파일, 단위 테스트, 렌더 골든 테스트 실패 지점을 봅니다." });
    }
    return meta;
  }
  function workflowJudgment(kind, run, warnings) {
    if (!run) return "실행 기록이 없어 상태 판단이 제한됩니다. 워크플로우 활성화와 GitHub Actions 연결을 확인하세요.";
    if (kind === "bad") return "운영 흐름이 이 단계에서 멈췄을 수 있습니다. 실패 job 로그가 최우선 확인 대상입니다.";
    if (run.status && run.status !== "completed") return "현재 처리 중입니다. 완료 후 정상 또는 실패로 판정됩니다.";
    if ((warnings || []).length) return "실행은 완료됐지만 운영 경고가 남았습니다. 경고 내용을 확인하면 됩니다.";
    return "정상 완료 상태입니다. 다음 자동 실행 또는 필요한 수동 실행까지 대기하면 됩니다.";
  }
  function worseKind(a, b) {
    if (a === "bad" || b === "bad") return "bad";
    if (a === "warn" || b === "warn") return "warn";
    return "ok";
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
    cannot_manage_admin_user: "Admin 계정은 회원 관리 조치 대상이 아닙니다.",
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
  var pendingAdminSignupEmail = "";
  var pendingAdminResetEmail = "";
  var state = {
    client: null,
    session: null,
    latest: null,
    index: null,
    users: [],
    adminUsers: [],
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
  state.client = window.supabase.createClient(supabaseUrl, anonKey, {
    auth: {
      storageKey: "grm-admin-auth-v1",
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false
    }
  });
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
    setAdminAuthMode("login");
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
  function setAdminAuthMode(mode) {
    hide(byId("grm-admin-login-form"), mode !== "login");
    hide(byId("grm-admin-confirm-form"), mode !== "confirm");
    hide(byId("grm-admin-reset-form"), mode !== "reset");
    hide(byId("grm-admin-signup"), mode !== "login");
    hide(byId("grm-admin-reset"), mode !== "login");
    hide(byId("grm-admin-auth-back"), mode === "login");
  }
  function adminLoginEmail() {
    var form = byId("grm-admin-login-form");
    return ((form && form.elements.email.value) || adminEmail).trim();
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
      state.adminUsers = data.admin_users || [];
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
      state.adminUsers = data.admin_users || [];
      txt("grm-kpi-users", number(data.count || state.users.length));
      renderUsers();
      setStatus(byId("grm-users-status"), "일반 회원 목록을 갱신했습니다." + (state.adminUsers.length ? " Admin 계정 " + state.adminUsers.length + "개는 제외됩니다." : ""), "ok");
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
    renderOpsBrief(errorMessage);
    renderOpsSummary(errorMessage);
    renderWorkflowPipeline(errorMessage);
    renderWorkflowCards(errorMessage);
    renderOpsIncidents(errorMessage);
  }
  function renderOpsBrief(errorMessage) {
    var host = byId("grm-ops-brief");
    if (!host) return;
    if (errorMessage) {
      host.className = "admin-ops-brief bad";
      host.innerHTML = '<div><h3><i class="ti ti-alert-triangle"></i>GitHub 연결 확인 필요</h3>' +
        '<p>운영 데이터를 불러오지 못했습니다. Admin GitHub 설정과 Edge Function 응답을 먼저 확인해야 합니다.</p></div>' +
        badge("연결 실패", "bad");
      return;
    }
    var summary = (state.ops && state.ops.summary) || {};
    var warnings = (state.ops && state.ops.warning_issues) || [];
    var configWarnings = (state.ops && state.ops.configuration_warnings) || [];
    var sourceOk = summary.source_ok === true;
    var sourceStatus = sourceStatusLabel(summary.source_status, sourceOk);
    var incidentCount = Number(summary.incidents || 0);
    var inProgress = Number(summary.in_progress || 0);
    var warningTotal = summary.warning_total == null
      ? (warnings.length + configWarnings.length)
      : Number(summary.warning_total || 0);
    var sourceBad = summary.source_ok === false || sourceStatus === "실패";
    var kind = sourceBad || incidentCount || configWarnings.length ? "bad" : (warningTotal || inProgress ? "warn" : "ok");
    var title = kind === "bad" ? "즉시 조치 필요" : (kind === "warn" ? "주의해서 확인" : "운영 정상");
    var icon = kind === "bad" ? "ti-alert-triangle" : (kind === "warn" ? "ti-alert-circle" : "ti-shield-check");
    var statusBits = [];
    if (sourceBad) statusBits.push(sourceStatus === "확인 중" ? "규제소스 수집 상태 확인 필요" : "규제소스 수집 " + sourceStatus);
    if (incidentCount) statusBits.push(number(incidentCount) + "개 실패 작업");
    if (configWarnings.length) statusBits.push(number(configWarnings.length) + "개 설정 경고");
    if (!statusBits.length && inProgress) statusBits.push(number(inProgress) + "개 워크플로우 실행 중");
    if (!statusBits.length && warningTotal) statusBits.push(number(warningTotal) + "개 운영 경고");
    var message = "수집, 발행, 배포 워크플로우가 정상 범위입니다. 별도 조치 없이 정기 점검만 유지하면 됩니다.";
    if (kind === "bad") {
      message = "다음 항목이 감지되었습니다: " + statusBits.join(", ") + ". 오른쪽 조치 항목에서 GitHub 로그를 열고 필요한 경우 복구 실행을 사용하세요.";
    } else if (kind === "warn") {
      message = statusBits.join(", ") + " 상태입니다. 서비스가 멈춘 상태는 아니지만 최신 Run과 경고 Issue를 확인하는 것이 좋습니다.";
    }
    host.className = "admin-ops-brief " + kind;
    host.innerHTML = '<div><h3><i class="ti ' + esc(icon) + '"></i>' + esc(title) + '</h3><p>' +
      esc(message) + "</p><small>마지막 진단 " + esc(fmtDate((state.ops && state.ops.generated_at) || summary.generated_at)) +
      "</small></div>" + badge(kind === "ok" ? "정상" : (kind === "warn" ? "경고" : "조치 필요"), kind);
  }
  function renderOpsSummary(errorMessage) {
    var host = byId("grm-ops-summary");
    if (!host) return;
    if (errorMessage) {
      host.innerHTML = '<div class="admin-metric"><span><i class="ti ti-alert-triangle"></i>GitHub 연결</span><b class="bad">확인 필요</b><p>운영 API 응답 실패</p></div>';
      return;
    }
    var summary = (state.ops && state.ops.summary) || {};
    var sourceOk = summary.source_ok === true;
    var warningTotal = summary.warning_total == null
      ? ((summary.warning_issues || 0) + (summary.configuration_warnings || 0))
      : summary.warning_total;
    var items = [
      { label: "수집 상태", value: sourceStatusLabel(summary.source_status, sourceOk), kind: sourceOk ? "ok" : (summary.source_status ? "bad" : "warn"), icon: "ti-database-import", desc: "규제소스 수집 최신 실행" },
      { label: "실행 중", value: number(summary.in_progress || 0), kind: summary.in_progress ? "warn" : "ok", icon: "ti-loader-2", desc: "대기 또는 진행 중인 Actions" },
      { label: "실패 작업", value: number(summary.incidents || 0), kind: summary.incidents ? "bad" : "ok", icon: "ti-alert-triangle", desc: "최신 유효 실행 기준 실패" },
      { label: "운영 경고", value: number(warningTotal || 0), kind: warningTotal ? "warn" : "ok", icon: "ti-message-report", desc: "열린 경고 Issue와 설정 경고" }
    ];
    host.innerHTML = items.map(function (item) {
      return '<div class="admin-metric"><span><i class="ti ' + esc(item.icon) + '"></i>' + esc(item.label) +
        '</span><b class="' + esc(item.kind) + '">' + esc(item.value) + "</b><p>" + esc(item.desc) + "</p></div>";
    }).join("");
  }
  function renderWorkflowCards(errorMessage) {
    var host = byId("grm-workflow-cards");
    if (!host) return;
    if (errorMessage) {
      host.innerHTML = '<div class="admin-empty">' + esc(errorMessage) + "</div>";
      return;
    }
    var workflows = ((state.ops && state.ops.workflows) || []).slice().sort(function (a, b) {
      var ma = workflowMeta(a);
      var mb = workflowMeta(b);
      return ma.order === mb.order ? ma.index.localeCompare(mb.index) : ma.order - mb.order;
    });
    if (!workflows.length) {
      host.innerHTML = '<div class="admin-empty">워크플로우 상태를 불러오는 중입니다.</div>';
      return;
    }
    host.innerHTML = workflows.map(function (wf) {
      var run = wf.latest || null;
      var wfWarnings = wf.warnings || [];
      var displayKind = workflowDisplayKind(wf);
      var meta = workflowMeta(wf);
      var title = run && run.display_title ? run.display_title : (run && run.event ? run.event : "최근 실행 없음");
      var status = wfWarnings.length && displayKind === "warn" ? "경고 확인" : runLabel(run);
      var runNo = run && run.run_number ? "#" + run.run_number : "-";
      var action = nextAction(displayKind, run, wfWarnings);
      var actions = [];
      var warningHtml = "";
      var facts = [
        ["주기", wf.schedule || "일정 없음"],
        ["최근", run ? fmtDate(run.created_at) : "기록 없음"],
        ["소요", run ? runDuration(run) : "-"],
        ["방식", run ? eventLabel(run.event) : "-"]
      ];
      if (run && run.html_url) actions.push(actionLink(run.html_url, "GitHub 로그", "primary"));
      actions.push(actionLink(wf.workflow_url || ("https://github.com/MINHOYEOM/grm-api-intake/actions/workflows/" + encodeURIComponent(wf.workflow || "")), "워크플로우 설정", ""));
      if (run && displayKind === "bad") {
        actions.push('<button class="admin-mini danger" type="button" data-rerun-failed="' + esc(run.id || "") + '">실패 job 재실행</button>');
      }
      if (wfWarnings.length) {
        warningHtml = '<div class="admin-workflow-alerts">' + wfWarnings.slice(0, 2).map(function (warning) {
          var skipped = (warning.steps || []).slice(0, 3).map(function (step) {
            return step.name || "-";
          }).join(" / ");
          return '<div class="admin-workflow-alert"><i class="ti ti-alert-circle"></i> ' +
            esc((warning.title || "운영 경고") + (skipped ? " · 확인 단계: " + skipped : "")) + "</div>";
        }).join("") + "</div>";
      }
      return '<details class="admin-workflow-row ' + esc(displayKind) + '">' +
        '<summary><div class="admin-workflow-compact">' +
          '<div class="admin-workflow-identity"><span class="admin-workflow-index">' + esc(meta.index) +
          '</span><div class="admin-workflow-name"><span class="admin-workflow-stage"><i class="ti ' + esc(meta.icon) + '"></i> ' +
          esc(meta.stage) + ' · ' + esc(wf.workflow || "") + '</span><b>' + esc(wf.label || wf.workflow || "-") + "</b></div></div>" +
          '<div class="admin-workflow-quick"><i class="ti ti-history"></i><span>' + esc((wf.purpose || meta.pipelineDesc)) +
          " · 최근 " + esc(run ? fmtDate(run.created_at) : "기록 없음") + " · " + esc(run ? eventLabel(run.event) : "-") + "</span></div>" +
        '</div><div class="admin-workflow-action">' + badge(status, displayKind) +
          '<span class="admin-expand-label">상세 런북</span></div></summary>' +
        '<div class="admin-workflow-detail">' +
          '<div class="admin-next-action ' + esc(action.kind) + '"><strong>다음 조치</strong> · ' + esc(action.text) + "</div>" +
          '<div class="admin-workflow-detail-grid">' +
            '<div class="admin-workflow-note"><span><i class="ti ti-clipboard-check"></i>현재 판단</span>' + esc(workflowJudgment(displayKind, run, wfWarnings)) + "</div>" +
            '<div class="admin-workflow-note"><span><i class="ti ti-route"></i>운영 영향</span>' + esc(meta.impact) + "</div>" +
            '<div class="admin-workflow-note"><span><i class="ti ti-search"></i>볼 곳</span>' + esc(meta.focus) + "</div>" +
          "</div>" +
          '<dl class="admin-workflow-facts">' + facts.map(function (fact) {
            return "<div><dt>" + esc(fact[0]) + "</dt><dd>" + esc(fact[1]) + "</dd></div>";
          }).join("") + "</dl>" +
          '<div class="admin-workflow-note"><span><i class="ti ti-history"></i>최근 실행</span>' + esc(runNo) + " · " + esc(title) + "</div>" +
          warningHtml +
          '<div class="admin-card-actions">' + actions.join("") + "</div>" +
        "</div></details>";
    }).join("");
  }
  function renderWorkflowPipeline(errorMessage) {
    var host = byId("grm-workflow-pipeline");
    if (!host) return;
    if (errorMessage) {
      host.innerHTML = '<div class="admin-empty">' + esc(errorMessage) + "</div>";
      return;
    }
    var workflows = (state.ops && state.ops.workflows) || [];
    if (!workflows.length) {
      host.innerHTML = '<div class="admin-empty">운영 흐름을 불러오는 중입니다.</div>';
      return;
    }
    var grouped = {};
    workflows.forEach(function (wf) {
      var meta = workflowMeta(wf);
      var key = meta.stageKey || meta.stage;
      if (!grouped[key]) grouped[key] = { meta: meta, kind: "ok", total: 0, bad: 0, warn: 0 };
      var kind = workflowDisplayKind(wf);
      grouped[key].kind = worseKind(grouped[key].kind, kind);
      grouped[key].total += 1;
      if (kind === "bad") grouped[key].bad += 1;
      if (kind === "warn") grouped[key].warn += 1;
    });
    host.innerHTML = Object.keys(grouped).map(function (key) { return grouped[key]; })
      .sort(function (a, b) { return a.meta.order - b.meta.order; })
      .map(function (item) {
        var status = item.bad ? item.bad + "개 실패" : (item.warn ? item.warn + "개 확인" : "정상");
        return '<div class="admin-pipeline-step ' + esc(item.kind) + '"><span><i class="ti ' + esc(item.meta.icon) +
          '"></i>' + esc(item.meta.index.replace(/[A-Z]$/, "")) + "단계</span><b>" + esc(item.meta.pipeline) +
          '</b><em>' + esc(item.meta.pipelineDesc) + " · " + esc(status) + "</em></div>";
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
      parts.push('<div class="admin-incident-empty"><strong>현재 조치할 항목 없음</strong><br>실패 작업이나 설정 경고가 발견되지 않았습니다.</div>');
    }
    configWarnings.slice(0, 6).forEach(function (warning) {
      var steps = (warning.steps || []).slice(0, 5).map(function (step) {
        return "<code>" + esc((step.job_name || "job") + " · " + (step.name || "-") + " · " + (step.conclusion || step.status || "-")) + "</code>";
      }).join("");
      var actions = [];
      if (warning.run_url) actions.push(actionLink(warning.run_url, "GitHub 로그", "primary"));
      actions.push(actionLink("https://github.com/MINHOYEOM/grm-api-intake/settings/secrets/actions", "Secrets 확인", ""));
      parts.push('<details class="admin-incident-row warn"><summary><div class="admin-incident-title"><span>설정 경고</span><b>' +
        esc(warning.title || "운영 설정 경고") + "</b></div>" + badge("상세", "warn") + "</summary>" +
        '<div class="admin-incident-detail"><p><strong>의미</strong> · ' + esc(warning.detail || "워크플로우 일부 단계가 설정 문제로 건너뛰었을 수 있습니다.") + "</p>" +
        '<p><strong>권장 조치</strong> · 필요한 GitHub Secret과 배포 단계를 확인하세요.</p>' +
        (steps ? '<div class="admin-step-list">' + steps + "</div>" : "") +
        '<div class="admin-card-actions">' + actions.join("") + "</div></div></details>");
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
      parts.push('<details class="admin-incident-row bad"><summary><div class="admin-incident-title"><span>실패 Run</span><b>' +
        esc(run.workflow_name || run.workflow_id || "Workflow") + "</b></div>" + badge(runLabel(run), "bad") + "</summary>" +
        '<div class="admin-incident-detail"><p><strong>의미</strong> · 최신 실행이 실패했습니다. ' + esc(fmtDate(run.created_at)) + " 기준 " + esc(run.display_title || eventLabel(run.event) || "") + "</p>" +
        '<p><strong>권장 조치</strong> · 실패 job 로그를 확인하고 원인이 일시적이면 재실행하세요.</p>' +
        jobHtml + '<div class="admin-card-actions">' + actionLink(run.html_url, "GitHub 로그", "primary") +
        '<button class="admin-mini danger" type="button" data-rerun-failed="' + esc(run.id || "") + '">실패 job 재실행</button></div></div></details>');
    });
    warnings.slice(0, 4).forEach(function (issue) {
      var detail = issue.detail || "";
      var meta = [issue.title || "", fmtDate(issue.updated_at)].filter(Boolean).join(" · ");
      var actions = [actionLink(issue.html_url, "Issue 확인", "primary")];
      if (issue.latest_run_url) actions.push(actionLink(issue.latest_run_url, "최신 Run", ""));
      parts.push('<details class="admin-incident-row warn"><summary><div class="admin-incident-title"><span>운영 경고</span><b>Issue #' +
        esc(issue.number || "-") + "</b></div>" + badge("상세", "warn") + '</summary><div class="admin-incident-detail"><p><strong>의미</strong> · ' + esc(meta || "운영 경고가 열려 있습니다.") +
        '</p>' + (detail ? '<p><strong>권장 조치</strong> · ' + esc(detail) + "</p>" : '<p><strong>권장 조치</strong> · Issue 내용을 확인하고 최신 Run과 비교하세요.</p>') +
        '<div class="admin-card-actions">' + actions.join("") + "</div></div></details>");
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
    var adminMatch = q && (state.adminUsers || []).some(function (u) { return String(u.email || "").toLowerCase().indexOf(q) >= 0; });
    if (!rows.length) { body.innerHTML = emptyRow(5, adminMatch ? "Admin 계정은 운영자 권한으로 분리되어 회원 관리 대상에서 제외됩니다." : "일반 회원 내역 없음"); return; }
    body.innerHTML = rows.map(function (u) {
      var confirmed = !!u.email_confirmed_at;
      var banned = !!u.banned_until;
      var status = u.is_admin ? badge("Admin", "warn") : (banned ? badge("차단", "bad") : badge(confirmed ? "활성" : "미인증", confirmed ? "ok" : "warn"));
      var actions = u.is_admin ? badge("조치 제외", "warn") : '<button class="admin-mini" type="button" data-user-action="confirm_user" data-user-id="' + esc(u.id) + '">인증</button>';
      if (!u.is_admin) {
        actions += banned
          ? '<button class="admin-mini" type="button" data-user-action="unban_user" data-user-id="' + esc(u.id) + '">차단 해제</button>'
          : '<button class="admin-mini danger" type="button" data-user-action="ban_user" data-user-id="' + esc(u.id) + '">차단</button>';
      }
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
    if (action === "web_publish") {
      var wpDate = window.prompt(
        "웹 발행일 (YYYY-MM-DD)\nweb/data/deltas/delta_{date}.json 가 먼저 커밋돼 있어야 합니다.",
        (state.latest && state.latest.date) || "");
      if (!wpDate) return;
      payload.publish_date = String(wpDate).trim();
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
    if ((state.adminUsers || []).some(function (u) { return String(u.id || "") === String(userId || ""); })) {
      setStatus(byId("grm-users-status"), "Admin 계정은 회원 관리 조치 대상이 아닙니다.", "err");
      return Promise.resolve();
    }
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
      pendingAdminSignupEmail = email;
      setAdminAuthMode("confirm");
      setStatus(byId("grm-admin-login-status"), "인증 코드를 " + email + " 로 보냈습니다. 메일의 코드를 입력하세요.", "ok");
    }).catch(function (error) { setStatus(byId("grm-admin-login-status"), errText(error), "err"); });
  });
  byId("grm-admin-confirm-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var token = (e.currentTarget.elements.token.value || "").trim();
    if (!pendingAdminSignupEmail) {
      setAdminAuthMode("login");
      setStatus(byId("grm-admin-login-status"), "먼저 Admin 계정 만들기를 실행하세요.", "err");
      return;
    }
    if (!token) {
      setStatus(byId("grm-admin-login-status"), "메일로 받은 인증 코드를 입력하세요.", "err");
      return;
    }
    setStatus(byId("grm-admin-login-status"), "인증 중", "");
    state.client.auth.verifyOtp({ email: pendingAdminSignupEmail, token: token, type: "signup" }).then(function (res) {
      if (res.error) throw res.error;
      state.session = res.data && res.data.session;
      return api("admin-supabase?action=me");
    }).then(function () {
      pendingAdminSignupEmail = "";
      showDashboard();
      return refreshAll();
    }).catch(function (error) {
      setStatus(byId("grm-admin-login-status"), errText(error) || "코드가 올바르지 않거나 만료됐습니다.", "err");
    });
  });
  byId("grm-admin-reset").addEventListener("click", function () {
    if (!requireBackendReady()) return;
    var email = adminLoginEmail();
    if (!EMAIL_RE.test(email)) {
      setStatus(byId("grm-admin-login-status"), "올바른 이메일을 입력하세요.", "err");
      return;
    }
    if (email.toLowerCase() !== adminEmail.toLowerCase()) {
      setStatus(byId("grm-admin-login-status"), "Admin 비밀번호 재설정은 " + adminEmail + " 계정만 가능합니다.", "err");
      return;
    }
    state.client.auth.resetPasswordForEmail(email).then(function (res) {
      if (res.error) throw res.error;
      pendingAdminResetEmail = email;
      setAdminAuthMode("reset");
      setStatus(byId("grm-admin-login-status"), "재설정 코드를 " + email + " 로 보냈습니다. 코드와 새 비밀번호를 입력하세요.", "ok");
    }).catch(function (error) { setStatus(byId("grm-admin-login-status"), errText(error), "err"); });
  });
  byId("grm-admin-reset-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var token = (e.currentTarget.elements.token.value || "").trim();
    var password = e.currentTarget.elements.password.value || "";
    if (!pendingAdminResetEmail) {
      setAdminAuthMode("login");
      setStatus(byId("grm-admin-login-status"), "먼저 재설정 코드를 요청하세요.", "err");
      return;
    }
    if (!token) {
      setStatus(byId("grm-admin-login-status"), "메일로 받은 재설정 코드를 입력하세요.", "err");
      return;
    }
    if (password.length < 6) {
      setStatus(byId("grm-admin-login-status"), "새 비밀번호를 6자 이상 입력하세요.", "err");
      return;
    }
    setStatus(byId("grm-admin-login-status"), "코드 확인 중", "");
    state.client.auth.verifyOtp({ email: pendingAdminResetEmail, token: token, type: "recovery" }).then(function (res) {
      if (res.error) throw res.error;
      state.session = res.data && res.data.session;
      return state.client.auth.updateUser({ password: password });
    }).then(function (res) {
      if (res.error) throw res.error;
      return api("admin-supabase?action=me");
    }).then(function () {
      pendingAdminResetEmail = "";
      showDashboard();
      return refreshAll();
    }).catch(function (error) {
      setStatus(byId("grm-admin-login-status"), errText(error) || "코드가 올바르지 않거나 만료됐습니다.", "err");
    });
  });
  byId("grm-admin-auth-back").addEventListener("click", function () {
    pendingAdminSignupEmail = "";
    pendingAdminResetEmail = "";
    setAdminAuthMode("login");
    setStatus(byId("grm-admin-login-status"), "", "");
  });
  byId("grm-admin-signout").addEventListener("click", function () {
    state.client.auth.signOut({ scope: "local" }).finally(function () {
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
