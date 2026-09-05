(function () {
  "use strict";

  // 운영자 브라우저 표식 — base.html 의 RUM 비콘 게이트가 'grm-op' 를 읽어 이 브라우저를
  // Cloudflare Web Analytics 집계에서 영구 제외한다(운영자는 admin 을 반드시 지난다).
  // 게이트 자신도 /admin 방문 시 같은 플래그를 세운다 — 여기는 이중 안전벨트.
  try { localStorage.setItem("grm-op", "1"); } catch (e) { /* storage 불가 = 측정 fail-open */ }

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
    if (action === "web_publish" || workflow.indexOf("web-publish") >= 0) {
      meta = Object.assign({}, meta, {
        order: 2,
        index: "02",
        stage: "발행 준비",
        stageKey: "web_publish",
        icon: "ti-git-pull-request",
        pipeline: "웹 발행 PR",
        pipelineDesc: "브리프 초안 생성",
        impact: "실패하면 최신 주간 브리프 초안 PR과 미리보기가 만들어지지 않습니다.",
        focus: "발행일, 델타 파일, 스캐폴드 다운로드, 조립, PR 생성 단계를 봅니다."
      });
    } else if (action === "web_deploy" || workflow.indexOf("web-deploy") >= 0) {
      meta = Object.assign({}, meta, {
        order: 3,
        index: "03",
        stage: "라이브 배포",
        stageKey: "web_deploy",
        icon: "ti-cloud-upload",
        pipeline: "웹 배포",
        pipelineDesc: "사이트 반영",
        impact: "실패하면 PR 미리보기나 운영 사이트 배포가 되지 않습니다.",
        focus: "렌더, 링크체크, Cloudflare Pages 배포 단계를 봅니다."
      });
    } else if (action === "brief_audit" || workflow.indexOf("brief-audit") >= 0) {
      meta = Object.assign({}, meta, { order: 4, index: "04", stage: "근거 감사", stageKey: "audit", impact: "실패하면 발행된 브리프의 원문 링크와 근거 신뢰도 확인이 지연됩니다.", focus: "provenance JSON, 링크 검증, 경고 Issue 갱신 여부를 봅니다." });
    } else if (action === "ci" || workflow.indexOf("ci") >= 0) {
      meta = Object.assign({}, meta, { order: 4.5, index: "04B", stage: "회귀 검증", stageKey: "ci", impact: "실패하면 코드나 렌더 결과에 회귀 가능성이 있어 배포 전 확인이 필요합니다.", focus: "컴파일, 단위 테스트, 렌더 골든 테스트 실패 지점을 봅니다." });
    } else if (action === "newsletter_send") {
      meta = Object.assign({}, meta, { order: 5, index: "05", stage: "뉴스레터", stageKey: "newsletter" });
    } else if (action === "admin_backend") {
      meta = Object.assign({}, meta, { order: 6, index: "06", stage: "운영 API", stageKey: "admin" });
    } else if (action === "keepalive") {
      meta = Object.assign({}, meta, { order: 7, index: "07", stage: "인프라", stageKey: "infra" });
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
    invalid_intake_run_id: "수집 run id는 숫자만 입력할 수 있습니다.",
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
    function_not_deployed: "Admin 백엔드 함수가 아직 배포되지 않았습니다.",
    checks_not_green: "발행 PR의 CI가 아직 통과하지 않았습니다.",
    not_a_publish_branch: "발행 브랜치가 아니어서 머지할 수 없습니다.",
    publish_pr_not_found: "이번 주 발행 PR을 찾지 못했습니다."
  };
  var FRIENDLY_PURPOSE = {
    intake_run: "규제기관 발표를 모아 정리합니다",
    web_publish: "이번 주 브리프 초안과 미리보기를 만듭니다",
    web_deploy: "사이트를 다시 빌드해 grm-solutions.com 에 반영합니다",
    brief_audit: "발행된 카드의 원문 링크와 근거를 다시 검사합니다",
    ci: "코드와 화면이 깨지지 않았는지 자동 테스트합니다",
    newsletter_send: "최신호를 구독자 메일로 보냅니다",
    admin_backend: "관리자 콘솔 백엔드를 배포합니다",
    keepalive: "데이터베이스가 잠들지 않게 유지합니다"
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
    feedback: [],
    reactions: { totals: {}, topCards: [] },
    runs: [],
    ops: null,
    checks: [],
    health: { supabase: null, github: null, brevo: null },
    backendProbe: null,
    publishPr: null,
    growth: null,
    rum: null
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
        var publishDateInput = byId("grm-web-publish-date");
        if (publishDateInput && !publishDateInput.value) publishDateInput.value = state.latest.date || "";
        txt("grm-web-publish-latest", state.latest.date ? "최신 발행본 기준 " + state.latest.date : "최신 발행본 확인 중");
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
      // Edge Function 이 아직 구버전이면 feedback 키가 없다 — 빈 목록으로 우아하게(배포 순서 무결합).
      state.feedback = data.feedback || [];
      state.reactions = data.reactions || { totals: {}, topCards: [] };
      txt("grm-kpi-users", number((data.counts || {}).users || state.users.length));
      txt("grm-kpi-hearts", number((state.reactions.totals || {}).heart || 0));
      txt("grm-kpi-scraps", number((state.reactions.totals || {}).scrap || 0));
      txt("grm-kpi-dispatch", state.dispatches[0] ? fmtDay(state.dispatches[0].created_at) : "-");
      renderUsers();
      renderDispatches();
      renderAudit();
      renderFeedback();
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
  function loadFeedbackOnly() {
    setStatus(byId("grm-feedback-status"), "피드백 목록을 새로 불러오는 중", "");
    return api("admin-supabase?action=feedback").then(function (data) {
      state.feedback = data.feedback || [];
      renderFeedback();
      setStatus(byId("grm-feedback-status"), "피드백 목록을 갱신했습니다.", "ok");
    }).catch(function (error) { setStatus(byId("grm-feedback-status"), errText(error), "err"); });
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
  // RUM(방문·유입) — 072 rum_daily / rum_referrer_daily. Cloudflare 대시보드를 열지
  // 않고도 "어제 몇 명 왔고 어디서 왔나"를 한국어로 보게 하는 층.
  // ★분류는 화면이 한다 — 수집기는 리퍼러 호스트를 원문 그대로 넣고(사실), 어느 묶음에
  // 넣을지는 여기서 정한다. 서버가 미리 뭉치면 나중에 새 채널이 생겼을 때 과거를 다시
  // 못 가른다.
  // ★순서가 판정이다 — 먼저 걸리는 규칙이 이긴다. AI 를 구글보다 앞에 두는 이유는
  // gemini.google.com 이 구글 규칙(`.google.`)에도 걸리기 때문이다. 검색 유입과 AI 유입은
  // 성격이 다른 채널이라 한쪽으로 뭉치면 둘 다 잘못 읽힌다.
  var RUM_REFERRER_GROUPS = [
    { key: "ai", label: "AI 검색", match: function (h) {
        return /(^|\.)(chatgpt\.com|openai\.com|perplexity\.ai|gemini\.google\.com|claude\.ai|copilot\.microsoft\.com)$/.test(h);
      } },
    { key: "google", label: "구글", match: function (h) { return /(^|\.)google\./.test(h); } },
    { key: "naver", label: "네이버", match: function (h) { return /(^|\.)naver\.com$/.test(h); } },
    { key: "direct", label: "직접 방문", match: function (h) { return !h || h === "(direct)"; } }
  ];
  // ★값이 정확한지 추측하지 않는다 — 075 sample_interval 이 행마다 "이 값이 몇 배
  // 추정인지"를 담는다. Cloudflare 의 Adaptive 데이터셋은 질의가 비싸지면 표본으로
  // 내려가고 그때 값이 10 의 배수로 뭉개진다. 2026-09-02~04 에 실제로 방문 표까지
  // 10단위로 내려앉았는데 화면은 "위 방문 표의 합계가 정확한 값입니다"라고 적고
  // 있었다 — 화면이 스스로 정밀도를 말하지 않으면 그 거짓말이 며칠을 간다.
  // sample_interval 이 null 인 행 = 이 열을 만들기 전(075 이전)에 적재된 값.
  function rumPrecision(rows) {
    var worst = 1, unknown = false, known = false;
    (rows || []).forEach(function (r) {
      var v = r ? r.sample_interval : null;
      if (v === null || v === undefined || v === "") { unknown = true; return; }
      v = Number(v);
      if (!isFinite(v) || v < 1) v = 1;
      known = true;
      if (v > worst) worst = v;
    });
    return { worst: worst, unknown: unknown, known: known };
  }
  function precisionNote(list) {
    var worst = 1, unknown = false, known = false;
    (list || []).forEach(function (p) {
      if (!p) return;
      if (p.unknown) unknown = true;
      if (p.known) { known = true; if (p.worst > worst) worst = p.worst; }
    });
    if (!known) {
      return { warn: true, text: "정밀도 미상 — 표본 간격을 기록하기 전에 적재된 값입니다. 다음 동기화가 덮어씁니다." };
    }
    var tail = unknown ? " (일부 오래된 날은 정밀도 미상)" : "";
    if (worst < 1.05) return { warn: false, text: "전수 집계 — 표본추출 없이 받은 값입니다." + tail };
    if (worst < 2) return { warn: false, text: "거의 전수 — 표본 간격 최대 " + worst.toFixed(2) + "배로, 일부 이벤트만 추정입니다." + tail };
    return { warn: true, text: "표본 추정 — 표본 간격 최대 " + worst.toFixed(1) + "배입니다. 이보다 작은 값은 0으로 사라질 수 있습니다." + tail };
  }
  function setPrecisionNote(id, list, errorMessage) {
    var el = byId(id);
    if (!el) return;
    if (errorMessage) { el.textContent = ""; return; }
    var n = precisionNote(list);
    el.innerHTML = (n.warn ? "<b>주의</b> — " : "") + esc(n.text);
  }
  function rumGroupOf(host) {
    var h = String(host || "").toLowerCase();
    for (var i = 0; i < RUM_REFERRER_GROUPS.length; i++) {
      if (RUM_REFERRER_GROUPS[i].match(h)) return RUM_REFERRER_GROUPS[i].key;
    }
    return "other";
  }
  // 착지 경로를 사람이 아는 구역 이름으로 — 판정은 화면이 한다(수집기는 경로 원문만 넣는다).
  // 위에서부터 먼저 걸리는 규칙이 이긴다(구체적인 것이 앞).
  var RUM_ZONES = [
    { re: /^\/library\//, label: "자료실" },
    { re: /^\/glossary\//, label: "용어사전" },
    { re: /^\/findings\/firm\//, label: "업체 프로파일" },
    { re: /^\/findings\/inspector\//, label: "실사관 프로파일" },
    { re: /^\/findings\/doc(s)?\//, label: "지적사항 문서" },
    { re: /^\/findings\/trends\//, label: "트렌드" },
    { re: /^\/findings\//, label: "지적사항 검색" },
    { re: /^\/briefs\//, label: "주간 브리프" },
    { re: /^\/archive\//, label: "아카이브" },
    { re: /^\/quiz\//, label: "퀴즈" },
    { re: /^\/guide\//, label: "이용안내" },
    { re: /^\/admin\//, label: "운영 콘솔" },
    { re: /^\/$/, label: "홈" }
  ];
  function rumZoneOf(path) {
    var p = String(path || "");
    for (var i = 0; i < RUM_ZONES.length; i++) {
      if (RUM_ZONES[i].re.test(p)) return RUM_ZONES[i].label;
    }
    return "기타";
  }
  function renderRumPaths(errorMessage) {
    var host = byId("grm-rum-paths");
    setPrecisionNote("grm-rum-paths-precision",
      [(state.rum || {}).precision && state.rum.precision.paths], errorMessage);
    if (!host) return;
    if (errorMessage || !state.rum || !state.rum.paths) {
      host.innerHTML = emptyRow(3, errorMessage || "데이터 없음");
      return;
    }
    var rows = state.rum.paths;
    if (!rows.length) {
      host.innerHTML = emptyRow(3, "아직 수집된 경로 데이터가 없습니다(첫 동기화 대기).");
      return;
    }
    host.innerHTML = rows.slice(0, 15).map(function (r) {
      return "<tr><td>" + esc(r.path) + "</td><td>" + esc(rumZoneOf(r.path)) +
        "</td><td><b>" + number(r.visits) + "</b></td></tr>";
    }).join("");
  }

  function loadRum() {
    var days = GROWTH_SNAPSHOT_DAYS;
    return Promise.all([
      state.client.from("rum_daily").select("snap_date,metric,value,sample_interval")
        .order("snap_date", { ascending: false }).limit(days * 2),
      state.client.from("rum_referrer_daily").select("snap_date,referer_host,visits,sample_interval")
        .order("snap_date", { ascending: false }).limit(days * 25),
      state.client.from("rum_path_daily").select("snap_date,request_path,visits,sample_interval")
        .order("snap_date", { ascending: false }).limit(days * 40)
    ]).then(function (res) {
      if (res[0].error) throw res[0].error;
      if (res[1].error) throw res[1].error;
      if (res[2].error) throw res[2].error;
      var byDate = {};
      (res[0].data || []).forEach(function (r) {
        (byDate[r.snap_date] = byDate[r.snap_date] || {})[r.metric] = r.value || 0;
      });
      var refs = {};
      (res[1].data || []).forEach(function (r) {
        var d = (refs[r.snap_date] = refs[r.snap_date] || {});
        var g = rumGroupOf(r.referer_host);
        d[g] = (d[g] || 0) + (r.visits || 0);
      });
      // 경로는 최근 7일만 합산해 상위를 낸다(섹션별 비교가 목적이라 일자별 분해는 불필요).
      var recent = Object.keys(byDate).sort().slice(-7);
      var window7 = {};
      recent.forEach(function (d) { window7[d] = 1; });
      var pathTotals = {};
      (res[2].data || []).forEach(function (r) {
        if (!window7[r.snap_date]) return;
        pathTotals[r.request_path] = (pathTotals[r.request_path] || 0) + (r.visits || 0);
      });
      var paths = Object.keys(pathTotals)
        .map(function (p) { return { path: p, visits: pathTotals[p] }; })
        .sort(function (a, b) { return b.visits - a.visits || a.path.localeCompare(b.path); });
      // 정밀도는 "화면에 실제로 보이는 행"에서만 잰다 — 창 밖 오래된 행의 미상이
      // 지금 보고 있는 표를 미상으로 물들이면 안 된다.
      var precision = {
        daily: rumPrecision(res[0].data || []),
        refs: rumPrecision(res[1].data || []),
        paths: rumPrecision((res[2].data || []).filter(function (r) { return window7[r.snap_date]; }))
      };
      state.rum = { byDate: byDate, refs: refs, paths: paths, precision: precision };
      renderRum();
      renderRumPaths();
    }).catch(function (error) {
      state.rum = null;
      renderRum(errText(error) || "방문 데이터를 불러오지 못했습니다.");
      renderRumPaths(errText(error) || "방문 데이터를 불러오지 못했습니다.");
    });
  }
  function renderRum(errorMessage) {
    var host = byId("grm-rum-daily");
    // 방문 표는 방문·페이지뷰(rum_daily)와 유입 열(rum_referrer_daily)을 함께 그린다 —
    // 정밀도는 둘 중 나쁜 쪽을 말해야 표 전체에 대한 진술이 된다.
    setPrecisionNote("grm-rum-precision",
      [(state.rum || {}).precision && state.rum.precision.daily,
       (state.rum || {}).precision && state.rum.precision.refs], errorMessage);
    if (!host) return;
    if (errorMessage || !state.rum) {
      host.innerHTML = emptyRow(7, errorMessage || "데이터 없음");
      return;
    }
    var byDate = state.rum.byDate || {};
    var dates = Object.keys(byDate).sort().reverse().slice(0, GROWTH_SNAPSHOT_DAYS);
    if (!dates.length) {
      host.innerHTML = emptyRow(7, "아직 수집된 방문 데이터가 없습니다(첫 동기화 대기).");
      return;
    }
    host.innerHTML = dates.map(function (d) {
      var m = byDate[d] || {};
      var r = (state.rum.refs || {})[d] || {};
      return "<tr><td>" + esc(d) + "</td><td><b>" + number(m.visits || 0) + "</b></td><td>" +
        number(m.page_views || 0) + "</td><td>" + number(r.google || 0) + "</td><td>" +
        number(r.naver || 0) + "</td><td>" + number(r.ai || 0) + "</td><td>" +
        number(r.direct || 0) + "</td></tr>";
    }).join("");
  }

  // 성장·유입 패널 — 깔때기 어휘는 060/071 CHECK 와 동일해야 한다(테스트가 대조).
  var FUNNEL_KEYS = ["band_view", "band_submit", "cta_view", "cta_submit", "cta_dismiss"];
  var GROWTH_SNAPSHOT_DAYS = 15;
  function kstToday() {
    // KST 는 DST 가 없어 고정 +9h 산술이 안전하다.
    return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  }
  // 구역 라벨 — 화면 표기만이다. 없는 슬러그는 그대로 보여준다(목록이 낡아도 값은
  // 사라지지 않는다 — 076 이 구역을 경로 첫 조각으로 정한 것과 같은 이유).
  var ZONE_LABELS = {
    home: "홈", briefs: "주간 브리프", findings: "지적사항", glossary: "용어사전",
    library: "자료실", quiz: "주간 퀴즈", guide: "이용안내", archive: "지난 호",
    search: "검색", other: "기타"
  };
  function loadGrowth() {
    // 읽기 전용 select 셋 뿐 — 스냅샷 쓰기는 DB cron(funnel_snapshot)만 한다.
    return Promise.all([
      state.client.from("funnel_counts").select("key,total"),
      state.client.from("funnel_counts_daily").select("snap_date,key,total")
        .order("snap_date", { ascending: false }).limit(FUNNEL_KEYS.length * (GROWTH_SNAPSHOT_DAYS + 1)),
      state.client.from("funnel_zone_counts").select("key,zone,total")
    ]).then(function (res) {
      if (res[0].error) throw res[0].error;
      if (res[1].error) throw res[1].error;
      if (res[2].error) throw res[2].error;
      var totals = {};
      (res[0].data || []).forEach(function (r) { totals[r.key] = r.total || 0; });
      var byDate = {};
      (res[1].data || []).forEach(function (r) {
        (byDate[r.snap_date] = byDate[r.snap_date] || {})[r.key] = r.total || 0;
      });
      var zones = {};
      (res[2].data || []).forEach(function (r) {
        var z = (zones[r.zone] = zones[r.zone] || { zone: r.zone, band_submit: 0, cta_submit: 0 });
        if (r.key === "band_submit" || r.key === "cta_submit") z[r.key] += (r.total || 0);
      });
      state.growth = { totals: totals, byDate: byDate, zones: zones };
      renderGrowth();
      renderFunnelZones();
    }).catch(function (error) {
      state.growth = null;
      renderGrowth(errText(error) || "깔때기 데이터를 불러오지 못했습니다.");
      renderFunnelZones(errText(error) || "깔때기 데이터를 불러오지 못했습니다.");
    });
  }
  function renderFunnelZones(errorMessage) {
    var host = byId("grm-funnel-zones");
    if (!host) return;
    if (errorMessage || !state.growth || !state.growth.zones) {
      host.innerHTML = emptyRow(4, errorMessage || "데이터 없음");
      return;
    }
    var rows = Object.keys(state.growth.zones).map(function (z) {
      var r = state.growth.zones[z];
      return { zone: z, band: r.band_submit || 0, cta: r.cta_submit || 0,
               sum: (r.band_submit || 0) + (r.cta_submit || 0) };
    }).filter(function (r) { return r.sum > 0; })
      .sort(function (a, b) { return b.sum - a.sum || a.zone.localeCompare(b.zone); });
    if (!rows.length) {
      host.innerHTML = emptyRow(4, "아직 구역이 기록된 제출이 없습니다(2026-09-05 배선 이후 제출부터).");
      return;
    }
    host.innerHTML = rows.map(function (r) {
      var label = ZONE_LABELS[r.zone] || r.zone;
      return "<tr><td>" + esc(label) + " <span class=\"mono\">/" + esc(r.zone) + "/</span></td><td>" +
        number(r.band) + "</td><td>" + number(r.cta) + "</td><td><b>" + number(r.sum) + "</b></td></tr>";
    }).join("");
  }
  function growthDelta(cur, prev) {
    var row = {};
    FUNNEL_KEYS.forEach(function (k) {
      row[k] = Math.max(0, ((cur && cur[k]) || 0) - ((prev && prev[k]) || 0));
    });
    row.submits = row.band_submit + row.cta_submit;
    row.views = row.band_view + row.cta_view;
    return row;
  }
  function growthRate(subs, views) {
    if (!views) return "-";
    return (subs * 100 / views).toFixed(2) + "%";
  }
  function renderGrowth(errorMessage) {
    var kpiHost = byId("grm-growth-kpis");
    var tableHost = byId("grm-growth-daily");
    if (!kpiHost || !tableHost) return;
    if (errorMessage || !state.growth) {
      kpiHost.innerHTML = '<div class="admin-metric"><span><i class="ti ti-alert-triangle"></i>성장 지표</span><b class="bad">오류</b><p>' +
        esc(errorMessage || "데이터 없음") + "</p></div>";
      tableHost.innerHTML = emptyRow(7, errorMessage || "데이터 없음");
      return;
    }
    var totals = state.growth.totals || {};
    var byDate = state.growth.byDate || {};
    var dates = Object.keys(byDate).sort();
    var rows = [];
    for (var i = 1; i < dates.length; i++) {
      var gapDays = Math.round((Date.parse(dates[i]) - Date.parse(dates[i - 1])) / 86400000);
      rows.push({
        label: dates[i] + (gapDays > 1 ? " (" + gapDays + "일치)" : ""),
        delta: growthDelta(byDate[dates[i]], byDate[dates[i - 1]])
      });
    }
    var last = dates.length ? byDate[dates[dates.length - 1]] : null;
    var todayLabel = !dates.length ? "누적 (첫 스냅샷 대기)"
      : dates[dates.length - 1] === kstToday() ? "오늘 (스냅샷 이후)" : "오늘 (진행 중)";
    var todayDelta = growthDelta(totals, last);
    rows.push({ label: todayLabel, delta: todayDelta, today: true });
    rows.reverse();
    tableHost.innerHTML = rows.slice(0, GROWTH_SNAPSHOT_DAYS).map(function (r) {
      var d = r.delta;
      return "<tr" + (r.today ? ' style="background:var(--soft)"' : "") + "><td>" + esc(r.label) + "</td><td>" +
        number(d.band_view) + "</td><td>" + number(d.band_submit) + "</td><td>" + number(d.cta_view) + "</td><td>" +
        number(d.cta_submit) + "</td><td>" + number(d.cta_dismiss) + "</td><td><b>" + number(d.submits) + "</b></td></tr>";
    }).join("");
    var week = rows.slice(0, Math.min(rows.length, 8));
    var weekSubs = 0, weekViews = 0;
    week.forEach(function (r) { weekSubs += r.delta.submits; weekViews += r.delta.views; });
    var totalSubs = (totals.band_submit || 0) + (totals.cta_submit || 0);
    kpiHost.innerHTML =
      '<div class="admin-metric"><span><i class="ti ti-user-plus"></i>오늘 구독 제출</span><b class="' + (todayDelta.submits > 0 ? "ok" : "") + '">' +
        number(todayDelta.submits) + "</b><p>밴드 " + number(todayDelta.band_submit) + " · CTA " + number(todayDelta.cta_submit) + "</p></div>" +
      '<div class="admin-metric"><span><i class="ti ti-calendar-week"></i>최근 7일 제출</span><b>' + number(weekSubs) + "</b><p>오늘 포함 스냅샷 기준</p></div>" +
      '<div class="admin-metric"><span><i class="ti ti-eye"></i>최근 7일 노출</span><b>' + number(weekViews) + "</b><p>JS 실행 크롤러 포함 가능 — 참고용</p></div>" +
      '<div class="admin-metric"><span><i class="ti ti-percentage"></i>7일 노출→제출</span><b>' + growthRate(weekSubs, weekViews) + "</b><p>누적 제출 " + number(totalSubs) + "건</p></div>";
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
  function loadPublishPr() {
    return api("admin-github?action=publish_pr").then(function (data) {
      state.publishPr = data && data.pr ? data : null;
      renderPublishPr();
    }).catch(function (error) {
      state.publishPr = null;
      renderPublishPr(errText(error));
    });
  }
  function renderPublishPr(errorMessage) {
    var prLine = byId("grm-web-approve-pr");
    var previewLink = byId("grm-web-preview-link");
    var checksBadge = byId("grm-web-checks");
    var submitBtn = byId("grm-web-approve-submit");
    if (!prLine || !previewLink || !checksBadge || !submitBtn) return;
    if (errorMessage) {
      txt("grm-web-approve-pr", errorMessage);
      hide(previewLink, true);
      checksBadge.className = "admin-pill bad";
      txt("grm-web-checks", "확인 필요");
      submitBtn.disabled = true;
      setStatus(byId("grm-web-approve-status"), errorMessage, "err");
      return;
    }
    var data = state.publishPr;
    if (!data || !data.pr) {
      txt("grm-web-approve-pr", "이번 주 발행 PR이 아직 없습니다 — 월요일 오전 9시 30분에 자동 생성됩니다.");
      hide(previewLink, true);
      checksBadge.className = "admin-pill";
      txt("grm-web-checks", "대기");
      submitBtn.disabled = true;
      setStatus(byId("grm-web-approve-status"), "지금은 할 일이 없습니다. PR이 생기면 이 카드에 미리보기 버튼이 나타납니다.", "");
      return;
    }
    var pr = data.pr;
    if (pr.html_url) {
      prLine.innerHTML = "#" + esc(pr.number || "-") + " " + esc(pr.title || "발행 PR") + " · " +
        '<a href="' + esc(pr.html_url) + '" target="_blank" rel="noopener">PR 열기</a>';
    } else {
      prLine.textContent = "#" + (pr.number || "-") + " " + (pr.title || "발행 PR");
    }
    if (data.preview_url) {
      previewLink.href = data.preview_url;
      hide(previewLink, false);
    } else {
      hide(previewLink, true);
    }
    var checksState = (data.checks && data.checks.state) || "none";
    var checksKindMap = { green: "ok", red: "bad", pending: "warn", none: "warn" };
    var checksLabelMap = { green: "CI 통과", red: "CI 실패", pending: "CI 대기 중", none: "CI 확인 불가" };
    checksBadge.className = "admin-pill " + (checksKindMap[checksState] || "warn");
    txt("grm-web-checks", checksLabelMap[checksState] || "확인 중");
    submitBtn.disabled = !data.gate_ok;
    if (!data.gate_ok) {
      setStatus(byId("grm-web-approve-status"), errText({ data: { error: data.gate_reason } }) || "승인 게이트를 통과하지 못했습니다.", "err");
    } else {
      setStatus(byId("grm-web-approve-status"), "미리보기에서 이번 주 카드를 확인한 뒤 승인을 누르세요. 승인하면 몇 분 안에 라이브 사이트가 바뀝니다.", "");
    }
  }
  // ── Search Console ────────────────────────────────────────────────────────
  // RUM 은 "google.com 에서 왔다"까지만 안다 — **무엇을 검색했는지**는 여기서만 온다.
  //
  // ★비율은 저장하지 않고 여기서 만든다. 클릭률을 미리 계산해 두면 합칠 때 "평균의
  // 평균"이 되어(노출 10 짜리 날과 1,000 짜리 날이 같은 무게) 틀린 수가 된다. 순위도
  // 같은 이유로 **노출 가중 평균**이라야 뜻이 맞는다.
  function gscRate(clicks, impressions) {
    if (!impressions) return null;
    return (clicks / impressions) * 100;
  }
  function gscPct(clicks, impressions) {
    var v = gscRate(clicks, impressions);
    return v === null ? "-" : v.toFixed(v < 10 ? 2 : 1) + "%";
  }
  function gscPos(weighted, impressions) {
    return impressions ? (weighted / impressions).toFixed(1) : "-";
  }
  // 키(검색어·구역)별로 클릭·노출과 **순위×노출**을 함께 누적한다.
  function gscRollup(rows, keyOf) {
    var acc = {};
    (rows || []).forEach(function (r) {
      var k = keyOf(r);
      if (k === null || k === undefined || k === "") return;
      var a = acc[k] || (acc[k] = { key: k, clicks: 0, impressions: 0, posWeighted: 0 });
      var impr = r.impressions || 0;
      a.clicks += r.clicks || 0;
      a.impressions += impr;
      a.posWeighted += (r.avg_position || 0) * impr;
    });
    return Object.keys(acc).map(function (k) { return acc[k]; })
      .sort(function (a, b) {
        return b.impressions - a.impressions || b.clicks - a.clicks ||
          String(a.key).localeCompare(String(b.key));
      });
  }

  function loadSearchConsole() {
    var days = GROWTH_SNAPSHOT_DAYS;
    return Promise.all([
      state.client.from("gsc_daily").select("snap_date,clicks,impressions,avg_position")
        .order("snap_date", { ascending: false }).limit(days),
      state.client.from("gsc_query_daily").select("snap_date,query,clicks,impressions,avg_position")
        .order("snap_date", { ascending: false }).limit(days * 60),
      state.client.from("gsc_page_daily").select("snap_date,page_path,clicks,impressions,avg_position")
        .order("snap_date", { ascending: false }).limit(days * 60)
    ]).then(function (res) {
      if (res[0].error) throw res[0].error;
      if (res[1].error) throw res[1].error;
      if (res[2].error) throw res[2].error;
      var totals = gscRollup(res[0].data || [], function () { return "site"; })[0] || null;
      state.gsc = {
        totals: totals,
        dates: (res[0].data || []).map(function (r) { return r.snap_date; }).sort(),
        queries: gscRollup(res[1].data, function (r) { return r.query; }),
        zones: gscRollup(res[2].data, function (r) { return rumZoneOf(r.page_path); })
      };
      renderGscQueries();
      renderGscZones();
    }).catch(function (error) {
      state.gsc = null;
      renderGscQueries(errText(error) || "검색 데이터를 불러오지 못했습니다.");
      renderGscZones(errText(error) || "검색 데이터를 불러오지 못했습니다.");
    });
  }

  // ★"연결 안 됨"과 "검색 유입 0"은 다른 말이다. 행이 아예 없으면 아직 배선이 안 된
  // 것이고, 0 이라고 쓰면 성적이 나쁘다는 뜻이 되어 거짓 보고가 된다.
  function renderGscQueries(errorMessage) {
    var note = byId("grm-gsc-summary");
    var host = byId("grm-gsc-queries");
    var g = state.gsc;
    if (note) {
      if (errorMessage) note.textContent = "";
      else if (!g || !g.totals || !g.totals.impressions) {
        note.textContent = "아직 검색 데이터가 없습니다 — 첫 동기화를 기다리는 중입니다.";
      } else {
        note.textContent = "기간 " + g.dates[0] + " ~ " + g.dates[g.dates.length - 1] +
          " · 노출 " + number(g.totals.impressions) + "회 · 클릭 " + number(g.totals.clicks) +
          "회 · 클릭률 " + gscPct(g.totals.clicks, g.totals.impressions) +
          " · 평균 순위 " + gscPos(g.totals.posWeighted, g.totals.impressions) + "위";
      }
    }
    if (!host) return;
    if (errorMessage || !g) {
      host.innerHTML = emptyRow(5, errorMessage || "데이터 없음");
      return;
    }
    if (!g.queries.length) {
      host.innerHTML = emptyRow(5, "아직 수집된 검색어가 없습니다(첫 동기화 대기).");
      return;
    }
    host.innerHTML = g.queries.slice(0, 20).map(function (r) {
      return "<tr><td>" + esc(r.key) + "</td><td>" + number(r.impressions) +
        "</td><td><b>" + number(r.clicks) + "</b></td><td>" +
        esc(gscPct(r.clicks, r.impressions)) + "</td><td>" +
        esc(gscPos(r.posWeighted, r.impressions)) + "</td></tr>";
    }).join("");
  }

  function renderGscZones(errorMessage) {
    var host = byId("grm-gsc-zones");
    if (!host) return;
    var g = state.gsc;
    if (errorMessage || !g) {
      host.innerHTML = emptyRow(5, errorMessage || "데이터 없음");
      return;
    }
    if (!g.zones.length) {
      host.innerHTML = emptyRow(5, "아직 수집된 페이지가 없습니다(첫 동기화 대기).");
      return;
    }
    host.innerHTML = g.zones.map(function (r) {
      return "<tr><td>" + esc(r.key) + "</td><td>" + number(r.impressions) +
        "</td><td><b>" + number(r.clicks) + "</b></td><td>" +
        esc(gscPct(r.clicks, r.impressions)) + "</td><td>" +
        esc(gscPos(r.posWeighted, r.impressions)) + "</td></tr>";
    }).join("");
  }

  function refreshAll() {
    return Promise.allSettled([loadIndex(), loadOverview(), loadSubscribers(), loadGrowth(), loadRum(), loadSearchConsole(), loadRuns(), loadHealth(), loadPublishPr()]).then(function () {
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
      message = "다음 항목이 감지되었습니다: " + statusBits.join(", ") + ". 아래 '운영 이슈'에서 어떤 작업이 실패했는지 확인하고 필요한 경우 복구 실행을 사용하세요.";
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
          '<div class="admin-workflow-quick"><i class="ti ti-history"></i><span>' + esc((FRIENDLY_PURPOSE[wf.action] || wf.purpose || meta.pipelineDesc)) +
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
  var FLOW_STAGES = [
    { icon: "ti-database-import", title: "소식 수집", desc: "전 세계 규제기관 발표를 매일 새벽 자동으로 모읍니다.", when: "매일 새벽 · 자동", action: "intake_run" },
    { icon: "ti-sparkles", title: "카드 선별", desc: "AI가 모인 소식 중 이번 주에 알릴 카드를 골라 정리합니다.", when: "월요일 아침 · 자동", action: null },
    { icon: "ti-git-pull-request", title: "검토용 초안", desc: "이번 주 브리프 초안과 미리보기 화면을 자동으로 만듭니다.", when: "월요일 09:35 · 자동", action: "web_publish" },
    { icon: "ti-shield-check", title: "승인 → 사이트 반영", desc: "운영자가 미리보기를 확인하고 승인하면 사이트가 새 브리프로 바뀝니다.", when: "월요일 · 운영자(맨 위 카드)", action: "web_deploy", human: true },
    { icon: "ti-mail-forward", title: "뉴스레터 발송", desc: "새 브리프를 구독자 메일로 보냅니다.", when: "사이트 확인 후 · 운영자(뉴스레터 메뉴)", action: "newsletter_send", human: true }
  ];
  var FLOW_SUPPORT_CHIPS = [
    { action: "ci", label: "코드·화면 자동 테스트" },
    { action: "brief_audit", label: "카드 근거 재검사" },
    { action: "admin_backend", label: "관리자 콘솔 백엔드" },
    { action: "keepalive", label: "데이터베이스 유지" }
  ];
  function findWorkflowsByAction(action) {
    var workflows = (state.ops && state.ops.workflows) || [];
    return workflows.filter(function (wf) { return wf.action === action; });
  }
  function isExpectedNoDeltaGateRejection(wf) {
    var run = wf && wf.latest;
    if (!run || runKind(run) !== "bad") return false;
    var jobs = run.failed_jobs || [];
    return jobs.some(function (job) {
      return (job.failed_steps || []).some(function (step) {
        return /Resolve publish_date|델타 경로/.test(String(step.name || ""));
      });
    });
  }
  function flowNodeStatus(stage) {
    if (!stage.action) return { kind: "", dot: "", label: "자동 진행" };
    var matches = findWorkflowsByAction(stage.action);
    if (!matches.length) return { kind: "", dot: "", label: "기록 없음" };
    var kind = "ok";
    matches.forEach(function (wf) { kind = worseKind(kind, workflowDisplayKind(wf)); });
    if (stage.action === "web_publish" && kind === "bad") {
      var gateRejected = matches.some(isExpectedNoDeltaGateRejection);
      if (gateRejected) {
        return { kind: "pending", dot: "pending", label: "대기 중", descOverride: "이번 주 카드가 아직 준비되지 않아 정상적으로 대기 중입니다. 자동 실행이 새 카드를 만들면 여기가 바뀝니다." };
      }
    }
    var label = kind === "bad" ? "실패" : (kind === "warn" ? "확인 필요" : "정상");
    var anyRunning = matches.some(function (wf) { return wf.latest && wf.latest.status && wf.latest.status !== "completed"; });
    if (anyRunning) { kind = "warn"; label = "실행 중"; }
    return { kind: kind, dot: kind, label: label };
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
    var nodesHtml = FLOW_STAGES.map(function (stage, i) {
      var status = flowNodeStatus(stage);
      var nodeClass = "admin-flow-node" + (status.kind ? " " + status.kind : "") + (stage.human ? " human" : "");
      var dotHtml = status.dot ? '<span class="admin-dot ' + esc(status.dot) + '"></span>' : "";
      var node = '<div class="' + nodeClass + '">' +
        '<i class="ti ' + esc(stage.icon) + '"></i>' +
        "<b>" + esc(stage.title) + "</b>" +
        "<p>" + esc(status.descOverride || stage.desc) + "</p>" +
        '<span class="admin-flow-when">' + esc(stage.when) + "</span>" +
        '<span class="admin-flow-status">' + dotHtml + esc(status.label) + "</span>" +
        "</div>";
      var arrow = i < FLOW_STAGES.length - 1 ? '<div class="admin-flow-arrow">→</div>' : "";
      return node + arrow;
    }).join("");
    var chipsHtml = FLOW_SUPPORT_CHIPS.map(function (chip) {
      var matches = findWorkflowsByAction(chip.action);
      var dotKind = "";
      if (matches.length) {
        var kind = "ok";
        matches.forEach(function (wf) { kind = worseKind(kind, workflowDisplayKind(wf)); });
        dotKind = kind;
      }
      return '<span class="admin-flow-chip"><span class="admin-dot' + (dotKind ? " " + esc(dotKind) : "") + '"></span>' + esc(chip.label) + "</span>";
    }).join("");
    host.innerHTML = '<div class="admin-flow">' + nodesHtml + "</div>" +
      '<div class="admin-flow-support"><span class="admin-flow-support-label">뒤에서 돌아가는 자동 검사</span>' + chipsHtml + "</div>";
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

  // ── 문의 및 제안(061 user_feedback) — 목록·상태 트리아지 ──────────────────────
  // 상태 어휘는 061 CHECK 와 같은 4종(new·in_progress·done·dismissed) — 여기만 늘리면
  // update 가 DB 제약에 걸려 조용히 실패한다(마이그레이션과 같이 바꿀 것).
  var FEEDBACK_CATEGORY = { usability: "이용 불편", correction: "오류·수정", feature: "기능 제안", other: "기타" };
  var FEEDBACK_STATUS = {
    "new": ["신규", "warn"], in_progress: ["처리 중", ""],
    done: ["완료", "ok"], dismissed: ["보류", ""]
  };
  var FEEDBACK_OPEN = { "new": 1, in_progress: 1 };
  function renderFeedback() {
    var body = byId("grm-feedback-body");
    if (!body) return;
    var all = state.feedback || [];
    var openCount = all.filter(function (f) { return FEEDBACK_OPEN[f.status]; }).length;
    var pill = byId("grm-feedback-new");
    if (pill) {
      pill.className = "admin-pill " + (openCount ? "warn" : "ok");
      pill.textContent = openCount ? "미처리 " + openCount + "건" : "미처리 없음";
    }
    var q = ((byId("grm-feedback-filter") && byId("grm-feedback-filter").value) || "").toLowerCase();
    var openOnly = !!(byId("grm-feedback-open-only") && byId("grm-feedback-open-only").checked);
    var rows = all.filter(function (f) {
      if (openOnly && !FEEDBACK_OPEN[f.status]) return false;
      if (!q) return true;
      return (String(f.message || "") + " " + String(f.email || "")).toLowerCase().indexOf(q) >= 0;
    });
    if (!rows.length) {
      body.innerHTML = emptyRow(7, all.length ? "조건에 맞는 문의가 없습니다." : "접수된 문의가 없습니다.");
      return;
    }
    body.innerHTML = rows.map(function (f) {
      var st = FEEDBACK_STATUS[f.status] || [f.status || "-", ""];
      // message 는 자유 텍스트 — esc 후에만 줄바꿈을 <br> 로 되살린다(마크업 주입 봉쇄).
      var msg = '<span class="admin-fb-msg">' + esc(f.message || "").replace(/\n/g, "<br>") + "</span>";
      var cat = esc(FEEDBACK_CATEGORY[f.category] || f.category || "-");
      if (f.is_operator) cat += " " + badge("운영자", "warn");
      // 회신 이메일은 mailto 로 — 답장이 한 번에 열린다(동의받은 주소만 저장돼 있다).
      var mail = f.email
        ? '<a href="mailto:' + esc(f.email) + "?subject=" + encodeURIComponent("[GRM] 문의 " + f.id + "번 회신") + '">' + esc(f.email) + "</a>"
        : '<span class="admin-fb-mail">회신 미요청</span>';
      var page = f.page_path && String(f.page_path).indexOf("/") === 0
        ? '<a href="' + esc(f.page_path) + '" target="_blank" rel="noopener">' + esc(f.page_path) + "</a>"
        : esc(f.page_path || "-");
      var actions = "";
      if (f.status === "new") {
        actions += '<button class="admin-mini" type="button" data-feedback-status="in_progress" data-feedback-id="' + esc(f.id) + '">처리 시작</button>';
      }
      if (FEEDBACK_OPEN[f.status]) {
        actions += '<button class="admin-mini" type="button" data-feedback-status="done" data-feedback-id="' + esc(f.id) + '">완료</button>' +
          '<button class="admin-mini" type="button" data-feedback-status="dismissed" data-feedback-id="' + esc(f.id) + '">보류</button>';
      } else {
        actions += '<button class="admin-mini" type="button" data-feedback-status="new" data-feedback-id="' + esc(f.id) + '">다시 열기</button>';
      }
      return "<tr><td>#" + esc(f.id) + "<br>" + esc(fmtDate(f.created_at)) + "</td><td>" + cat + "</td><td>" + msg +
        "</td><td>" + mail + "</td><td>" + page + "</td><td>" + badge(st[0], st[1]) +
        '</td><td><div class="admin-row-actions">' + actions + "</div></td></tr>";
    }).join("");
  }
  function feedbackAction(id, status) {
    if (!id || !status) return Promise.resolve();
    setStatus(byId("grm-feedback-status"), "문의 상태를 갱신하는 중", "");
    return api("admin-supabase", { method: "POST", json: { action: "feedback_status", id: id, status: status } }).then(function () {
      toast("문의 상태를 갱신했습니다.");
      return loadFeedbackOnly();
    }).catch(function (error) { setStatus(byId("grm-feedback-status"), errText(error), "err"); });
  }

  function confirmDispatch(action, publishDate) {
    if (action === "newsletter_send") {
      return window.confirm("구독자 전체에게 최신 뉴스레터" + (publishDate ? " (" + publishDate + ")" : "") + "를 실제 발송합니다. 계속할까요?");
    }
    if (action === "web_publish") return window.confirm("발행일 " + (publishDate || "-") + " 기준으로 웹 브리프 초안 PR을 만들까요? PR 미리보기 확인 전에는 라이브 사이트가 바뀌지 않습니다.");
    if (action === "web_deploy") return window.confirm("현재 main 기준으로 웹 재배포 워크플로우를 실행할까요?");
    if (action === "intake_run") return window.confirm("규제 소스 수집 워크플로우를 수동 실행할까요?");
    if (action === "brief_audit") return window.confirm("발행본 provenance 감사 워크플로우를 실행할까요?");
    return true;
  }

  function webPublishPayload() {
    var dateInput = byId("grm-web-publish-date");
    var runInput = byId("grm-web-publish-run");
    var publishDate = ((dateInput && dateInput.value) || (state.latest && state.latest.date) || "").trim();
    var intakeRunId = ((runInput && runInput.value) || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(publishDate)) {
      setStatus(byId("grm-web-publish-status"), "발행일을 YYYY-MM-DD 형식으로 입력해 주세요.", "err");
      setStatus(byId("grm-ops-status"), "발행일 형식이 올바르지 않습니다.", "err");
      return null;
    }
    if (intakeRunId && !/^\d+$/.test(intakeRunId)) {
      setStatus(byId("grm-web-publish-status"), "수집 run id는 숫자만 입력할 수 있습니다.", "err");
      setStatus(byId("grm-ops-status"), "수집 run id는 숫자만 입력할 수 있습니다.", "err");
      return null;
    }
    var payload = { action: "web_publish", publish_date: publishDate };
    if (intakeRunId) payload.intake_run_id = intakeRunId;
    return payload;
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
      payload = webPublishPayload();
      if (!payload) return;
    }
    if (!confirmDispatch(action, payload.publish_date)) return;
    if (button) button.disabled = true;
    setStatus(byId("grm-ops-status"), "워크플로우 실행 요청 중", "");
    return api("admin-github", { method: "POST", json: payload }).then(function (data) {
      toast((data.label || "워크플로우") + " 실행을 요청했습니다.");
      setStatus(byId("grm-ops-status"), "실행 요청 완료: " + (data.label || action), "ok");
      if (action === "newsletter_send") setStatus(byId("grm-newsletter-status"), "뉴스레터 실발송 워크플로우를 요청했습니다.", "ok");
      if (action === "web_publish") setStatus(byId("grm-web-publish-status"), "웹 발행 PR 생성 워크플로우를 요청했습니다. 아래 운영 흐름에서 결과를 확인하세요.", "ok");
      return Promise.allSettled([loadRuns(), loadOverview()]);
    }).catch(function (error) {
      var message = errText(error);
      if (error && error.status === 409 && error.data && error.data.existing) {
        message += " (" + fmtDate(error.data.existing.created_at) + ")";
      }
      setStatus(byId("grm-ops-status"), message, "err");
      if (action === "newsletter_send") setStatus(byId("grm-newsletter-status"), message, "err");
      if (action === "web_publish") setStatus(byId("grm-web-publish-status"), message, "err");
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
  function publishDateFromHeadRef(headRef) {
    var m = /^publish\/brief-(\d{4}-\d{2}-\d{2})/.exec(String(headRef || ""));
    return m ? m[1] : "";
  }
  function approveMerge(button) {
    var data = state.publishPr;
    var pr = data && data.pr;
    if (!pr || !pr.number) {
      setStatus(byId("grm-web-approve-status"), "이번 주 발행 PR을 찾지 못했습니다.", "err");
      return;
    }
    if (!window.confirm("미리보기를 확인하셨나요? 승인하면 grm-solutions.com 라이브에 반영됩니다.")) return;
    var publishDate = publishDateFromHeadRef(pr.head_ref);
    var payload = publishDate ? { action: "merge", publish_date: publishDate } : { action: "merge", pr_number: pr.number };
    if (button) button.disabled = true;
    setStatus(byId("grm-web-approve-status"), "승인 처리 중", "");
    return api("admin-github", { method: "POST", json: payload }).then(function () {
      toast("승인됨 — 곧 라이브 반영");
      setStatus(byId("grm-web-approve-status"), "승인됨 — 곧 라이브 반영", "ok");
      return Promise.allSettled([loadPublishPr(), loadRuns()]);
    }).catch(function (error) {
      setStatus(byId("grm-web-approve-status"), errText(error), "err");
    }).finally(function () {
      if (button) button.disabled = !(state.publishPr && state.publishPr.gate_ok);
    });
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
  byId("grm-growth-refresh").addEventListener("click", function () {
    loadGrowth(); loadRum();
  });
  byId("grm-users-refresh").addEventListener("click", loadUsersOnly);
  byId("grm-feedback-refresh").addEventListener("click", loadFeedbackOnly);
  byId("grm-feedback-filter").addEventListener("input", renderFeedback);
  byId("grm-feedback-open-only").addEventListener("change", renderFeedback);
  byId("grm-feedback-body").addEventListener("click", function (e) {
    var b = e.target.closest("[data-feedback-status]");
    if (b) feedbackAction(b.getAttribute("data-feedback-id"), b.getAttribute("data-feedback-status"));
  });
  byId("grm-newsletter-send").addEventListener("click", function (e) { dispatch("newsletter_send", e.currentTarget); });
  byId("grm-web-publish-form").addEventListener("submit", function (e) {
    e.preventDefault();
    dispatch("web_publish", byId("grm-web-publish-submit"));
  });
  if (byId("grm-web-approve-submit")) {
    byId("grm-web-approve-submit").addEventListener("click", function (e) { approveMerge(e.currentTarget); });
  }
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
