/* GRM 웹 카드 반응 계층(하트·스크랩·회원) — S1 / B안(Supabase 직접 호출·RLS).
   비골든: 런타임에 카운트/상태만 주입한다(콘텐츠 골든·결정론 불침범). progressive enhancement —
   설정/라이브러리/로그인/네트워크 실패는 전부 삼켜 정적 카드 열람·공유(S0)에 무영향. env-gate
   (base.html {% if reactions_enabled %})로 SUPABASE_URL/ANON_KEY 설정 시에만 로드된다.
   provenance: 카드 사실·원문 URL 미참조 — 불투명 card_id(=card.anchor)만 취급. */
(function () {
  "use strict";

  // ── 인증 오류 분류(순수 함수 — DOM·Supabase 무의존) ───────────────────────────
  // Supabase Auth 는 공식 오류 코드를 준다(email_not_confirmed·user_already_exists·
  // otp_expired·over_email_send_rate_limit·invalid_credentials). code 를 1순위로 쓰고,
  // code 필드가 없는 옛 supabase-js 를 위해 message 정규식을 보조로만 둔다(추측 분기 0 —
  // 매칭 실패는 전부 "unknown" 으로 떨어져 기존 뭉뚱그린 문구와 동일하게 동작한다).
  // env-gate 로 조기 종료하기 전에 window.GRM_AUTH 에 붙여 node 로 경로를 고정한다.
  function classifyAuthError(err) {
    var code = (err && err.code) || "";
    var msg = (err && err.message) || "";
    if (code === "email_not_confirmed" || /email not confirmed/i.test(msg)) return "unconfirmed";
    if (code === "user_already_exists" || /already registered|already exists|user already/i.test(msg)) return "exists";
    if (code === "weak_password" || /password should be|weak password/i.test(msg)) return "weak_password";
    if (code === "over_email_send_rate_limit" || code === "over_request_rate_limit" ||
        /rate limit|too many requests/i.test(msg)) return "rate_limit";
    if (code === "otp_expired" || /token has expired|otp.*expired/i.test(msg)) return "expired_code";
    if (code === "invalid_credentials" || /invalid login credentials/i.test(msg)) return "invalid_credentials";
    return "unknown";
  }
  window.GRM_AUTH = window.GRM_AUTH || {};
  window.GRM_AUTH.classifyAuthError = classifyAuthError;

  var cfg = document.getElementById("grm-reactions-cfg");
  var lib = window.supabase;
  if (!cfg || !lib || !lib.createClient) return;
  var SUPA_URL = cfg.getAttribute("data-url") || "";
  var SUPA_KEY = cfg.getAttribute("data-key") || "";
  if (!SUPA_URL || !SUPA_KEY) return;

  var sb;
  try {
    sb = lib.createClient(SUPA_URL, SUPA_KEY, {
      auth: {
        storageKey: "grm-public-auth-v1",
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: false
      }
    });
  } catch (e) { return; }

  var rows = Array.prototype.slice.call(
    document.querySelectorAll(".grm-card-actions[data-anchor]"));
  var myScrapsEl = document.getElementById("grm-my-scraps");   // /me 페이지 컨테이너(있으면 마이페이지)
  var myFirmsEl = document.getElementById("grm-my-firms");     // /me 관심 업체 컨테이너(015 워치리스트)
  // 로그인/계정 UI(renderAuth)는 카드 유무와 무관하게 모든 페이지 헤더에 필요하므로 조기 종료하지 않는다.
  // (카드 반응·집계는 rows, 마이페이지는 myScrapsEl 로 각각 개별 가드된다.)
  var ids = rows.map(function (r) { return r.getAttribute("data-anchor"); }).filter(Boolean);
  var cfgRoot = cfg.getAttribute("data-root") || "";
  var session = null;

  function reactBtns(row) {
    return Array.prototype.slice.call(row.querySelectorAll(".grm-ca-react[data-react]"));
  }

  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var ADMIN_EMAIL = "yeomminho1472@gmail.com";
  function esc(x){return String(x==null?"":x).replace(/[&<>\"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c];});}
  function acctInitial(email){var t=String(email||'G').trim();return (t.charAt(0)||'G').toUpperCase();}
  function isAdminEmail(email){return String(email||"").trim().toLowerCase()===ADMIN_EMAIL;}
  var acctMenuOpen=false, acctDocBound=false;
  var OWL_SVG =
    '<svg viewBox="0 0 64 64" aria-hidden="true" focusable="false">' +
    '<rect x="0" y="0" width="64" height="64" rx="16" fill="#C2603F"/>' +
    '<path d="M14 19 L29 19 L19 6 Z" fill="#FAF6EE"/><path d="M50 19 L35 19 L45 6 Z" fill="#FAF6EE"/>' +
    '<circle cx="23" cy="33" r="10" fill="#FAF6EE"/><circle cx="41" cy="33" r="10" fill="#FAF6EE"/>' +
    '<circle cx="23" cy="34" r="3.6" fill="#22303F"/><circle cx="41" cy="34" r="3.6" fill="#22303F"/>' +
    '<path d="M28 43 L36 43 L32 51 Z" fill="#E8B04A"/></svg>';
  var EYE =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path d="M17.9 17.9A10.4 10.4 0 0 1 12 19C5 19 1 12 1 12a19 19 0 0 1 5.1-5.9M9.9 4.2A10.4 10.4 0 0 1 12 5c7 0 11 7 11 7a19 19 0 0 1-2.2 3.2M1 1l22 22"/></svg>';

  // ── 헤더 로그인 상태(런타임 주입 — 정적 HTML 무변형) ─────────────────────────
  var authEl;
  function ensureAuthEl() {
    if (authEl) return authEl;
    var host = document.querySelector(".nav-in") || document.querySelector(".nav");
    if (!host) return null;
    authEl = document.createElement("span");
    authEl.className = "grm-auth";
    host.appendChild(authEl);
    return authEl;
  }
  function closeAcctMenu() {
    if (!authEl) return;
    var m = authEl.querySelector(".grm-acct-menu"), b = authEl.querySelector(".grm-acct-btn");
    if (m) m.hidden = true;
    if (b) b.setAttribute("aria-expanded", "false");
    acctMenuOpen = false;
  }
  // 계정 칩(아바타) + 드롭다운 메뉴(이메일·내 스크랩·로그아웃) — 런타임 주입(정적 HTML 무변형).
  function renderAuth() {
    var el = ensureAuthEl(); if (!el) return;
    el.innerHTML = "";
    if (session && session.user) {
      var email = session.user.email || "회원", ini = acctInitial(email);
      var wrap = document.createElement("div"); wrap.className = "grm-acct";
      wrap.innerHTML =
        '<button type="button" class="grm-acct-btn" aria-haspopup="menu" aria-expanded="false" aria-label="계정 메뉴">' +
        '<span class="grm-acct-av">' + esc(ini) + '</span>' +
        '<i class="ti ti-chevron-down grm-acct-cv" aria-hidden="true"></i></button>' +
        '<div class="grm-acct-menu" role="menu" hidden>' +
        '<div class="grm-acct-head"><span class="grm-acct-av grm-acct-av-lg">' + esc(ini) + '</span>' +
        '<span class="grm-acct-id"><span class="grm-acct-label">로그인 계정</span>' +
        '<span class="grm-acct-email">' + esc(email) + '</span></span></div>' +
        '<div class="grm-acct-div"></div>' +
        // 13차: /me 가 스크랩 전용에서 개인 홈(스크랩·구름이·관심 업체)으로 넓어져
        // 메뉴 이름도 실제와 맞췄다 — 링크·아이콘·위치는 그대로(새 표면 0).
        '<a class="grm-acct-item" role="menuitem" href="' + esc(cfgRoot) + 'me/index.html">' +
        '<i class="ti ti-bookmark" aria-hidden="true"></i>마이페이지</a>' +
        '<button type="button" class="grm-acct-item grm-acct-out" role="menuitem">' +
        '<i class="ti ti-logout" aria-hidden="true"></i>로그아웃</button></div>';
      el.appendChild(wrap);
      var btn = wrap.querySelector(".grm-acct-btn"), menu = wrap.querySelector(".grm-acct-menu");
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var willOpen = menu.hidden;
        menu.hidden = !willOpen;
        btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
        acctMenuOpen = willOpen;
      });
      wrap.querySelector(".grm-acct-out").addEventListener("click", function () { closeAcctMenu(); sb.auth.signOut({ scope: "local" }); });
      if (!acctDocBound) {
        acctDocBound = true;
        document.addEventListener("click", function (e) {
          if (acctMenuOpen && authEl && !authEl.contains(e.target)) closeAcctMenu();
        });
        document.addEventListener("keydown", function (e) {
          if (acctMenuOpen && (e.key === "Escape" || e.key === "Esc")) closeAcctMenu();
        });
      }
    } else {
      var login = document.createElement("button");
      login.type = "button"; login.className = "grm-acct-login"; login.textContent = "로그인";
      login.addEventListener("click", function () { openLogin(); });
      el.appendChild(login);
    }
  }

  // ── 로그인/회원가입/이메일확인/비밀번호 재설정(이메일 + 비밀번호) ───────────
  // 일반 회원가입 방식: signUp / signInWithPassword. 최초 가입 이메일 확인과 비밀번호 재설정은
  // 모두 **링크가 아닌 코드**(verifyOtp type:"signup" / "recovery")로 처리해, 메일 스캐너·클릭추적
  // (Brevo sendibt3)이 확인 링크를 선방문해 1회용 토큰을 소모하는 문제를 피한다. Supabase
  // "Confirm signup"·"Reset Password" 이메일 템플릿은 {{ .Token }}(코드)만 담고
  // {{ .ConfirmationURL }} 링크는 제거해야 한다.
  // 마찰 개선(12차): ① openLogin({mode:"signup"}) 가입 직행 ② 가입 진행 상태 sessionStorage
  // 복원 ③ 미확인 계정 로그인 실패 → 확인 코드 단계로 이어주기. 세 가지 모두 기존 로그인
  // 성공 경로·세션 저장소(grm-public-auth-v1)·하트/스크랩 로직은 건드리지 않는다.
  var pop, resetEmail = "", pendingSignupEmail = "", resendTimer = null;
  var MODE_COPY = {
    login:   { title: "로그인",          sub: "이메일과 비밀번호만 있으면 됩니다." },
    signup:  { title: "회원가입",        sub: "이메일과 비밀번호만 정하면 끝이에요. 확인 코드를 메일로 보내드립니다." },
    confirm: { title: "확인 코드 입력",   sub: "메일로 받은 코드를 넣으면 가입이 끝나요." },
    reqcode: { title: "비밀번호 재설정",   sub: "가입한 이메일로 재설정 코드를 보내드립니다." },
    newpw:   { title: "새 비밀번호 설정",  sub: "메일로 받은 코드와 새 비밀번호를 입력하세요." }
  };

  // 가입 진행 상태 보존(팝업을 닫아도 코드 입력 단계로 복원) ─────────────────────
  // sessionStorage 에 "어느 이메일이 코드 입력을 기다리는가"만 둔다 — 비밀번호·토큰·세션은
  // 절대 저장하지 않는다(세션 정본은 supabase-js 의 grm-public-auth-v1 그대로·불침범).
  // 탭을 닫으면 사라지고(sessionStorage), 30분이 지나도 만료로 버린다. 저장 실패
  // (사파리 프라이빗 등)는 조용히 삼켜 기존 동작으로 폴백한다.
  var SIGNUP_KEY = "grm-signup-progress-v1";
  var SIGNUP_TTL_MS = 30 * 60 * 1000;
  function saveSignupProgress(email) {
    try { sessionStorage.setItem(SIGNUP_KEY, JSON.stringify({ email: email, ts: Date.now() })); } catch (e) {}
  }
  function loadSignupProgress() {
    try {
      var d = JSON.parse(sessionStorage.getItem(SIGNUP_KEY));
      if (!d || !EMAIL_RE.test(d.email || "")) return "";
      if (typeof d.ts !== "number" || (Date.now() - d.ts) > SIGNUP_TTL_MS) { clearSignupProgress(); return ""; }
      return d.email;
    } catch (e) { return ""; }
  }
  function clearSignupProgress() {
    try { sessionStorage.removeItem(SIGNUP_KEY); } catch (e) {}
  }

  function pwField(ph, ac) {
    return '<span class="grm-pw-wrap"><input type="password" required minlength="6" autocomplete="' + ac +
      '" placeholder="' + ph + '" aria-label="' + ph + '" />' +
      '<button type="button" class="grm-pw-toggle" aria-label="비밀번호 표시">' + EYE + '</button></span>';
  }

  function buildPop() {
    pop = document.createElement("div");
    pop.className = "grm-login-pop";
    pop.innerHTML =
      '<div class="grm-login-card" role="dialog" aria-modal="true" aria-label="로그인">' +
      '<button type="button" class="grm-login-x" aria-label="닫기">×</button>' +
      '<div class="grm-login-brand">' + OWL_SVG + '<span>Global Regulatory Monitor</span></div>' +
      '<h3 class="grm-login-title">로그인</h3>' +
      '<p class="grm-login-sub"></p>' +
      // 로그인·회원가입 공용 폼(이메일 + 비밀번호)
      '<form class="grm-login-form grm-login-auth" novalidate>' +
      '<input type="email" required autocomplete="email" placeholder="you@company.com" aria-label="이메일" />' +
      pwField("비밀번호(6자 이상)", "current-password") +
      '<button type="submit" class="grm-login-primary">로그인</button></form>' +
      // 회원가입 이메일 확인: 코드
      '<form class="grm-login-form grm-login-confirm" style="display:none" novalidate>' +
      '<input type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="메일로 받은 확인 코드" aria-label="확인 코드" />' +
      '<button type="submit" class="grm-login-primary">가입 완료</button></form>' +
      // 재설정 1단계: 이메일
      '<form class="grm-login-form grm-login-reqcode" style="display:none" novalidate>' +
      '<input type="email" required autocomplete="email" placeholder="가입한 이메일" aria-label="이메일" />' +
      '<button type="submit" class="grm-login-primary">재설정 코드 받기</button></form>' +
      // 재설정 2단계: 코드 + 새 비밀번호
      '<form class="grm-login-form grm-login-newpw" style="display:none" novalidate>' +
      '<input type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="메일로 받은 코드" aria-label="재설정 코드" />' +
      pwField("새 비밀번호(6자 이상)", "new-password") +
      '<button type="submit" class="grm-login-primary">비밀번호 변경</button></form>' +
      '<p class="grm-login-hint" role="alert"></p>' +
      '<p class="grm-login-msg" role="status" aria-live="polite"></p>' +
      '<button type="button" class="grm-login-resend" style="display:none"></button>' +
      '<p class="grm-login-note" style="display:none">가입하면 스크랩·관심 업체·구름이가 계정에 보관되어 어느 기기에서든 이어집니다. 이메일 외에는 아무것도 받지 않으며, 비밀번호는 안전하게 암호화되어 저장됩니다.</p>' +
      '<div class="grm-login-alt"></div>' +
      '</div>';
    document.body.appendChild(pop);

    var authForm    = pop.querySelector(".grm-login-auth");
    var confirmForm = pop.querySelector(".grm-login-confirm");
    var reqcodeForm = pop.querySelector(".grm-login-reqcode");
    var newpwForm   = pop.querySelector(".grm-login-newpw");
    var hint        = pop.querySelector(".grm-login-hint");
    var m           = pop.querySelector(".grm-login-msg");

    pop.querySelector(".grm-login-x").addEventListener("click", closeLogin);
    pop.addEventListener("click", function (e) { if (e.target === pop) closeLogin(); });

    // 비밀번호 표시/숨김 토글
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-toggle"), function (tg) {
      tg.addEventListener("click", function () {
        var inp = tg.parentNode.querySelector("input");
        var show = inp.type === "password";
        inp.type = show ? "text" : "password";
        tg.innerHTML = show ? EYE_OFF : EYE;
        tg.setAttribute("aria-label", show ? "비밀번호 숨김" : "비밀번호 표시");
      });
    });
    // 입력 시 힌트 해제
    Array.prototype.forEach.call(pop.querySelectorAll("input"), function (inp) {
      inp.addEventListener("input", function () { hint.textContent = ""; });
    });

    // 로그인/회원가입(이메일+비밀번호)
    authForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = (authForm.querySelector('input[type="email"]').value || "").trim();
      var pw = (authForm.querySelector(".grm-pw-wrap input").value || "");
      if (!EMAIL_RE.test(email)) { hint.textContent = "올바른 이메일 형식을 입력해 주세요."; return; }
      if (isAdminEmail(email)) { hint.textContent = "운영자 계정은 Admin 페이지에서 로그인하세요."; return; }
      if (pw.length < 6) { hint.textContent = "비밀번호는 6자 이상이어야 합니다."; return; }
      var signup = pop.getAttribute("data-mode") === "signup";
      setBusy(authForm, true); m.textContent = signup ? "가입 중…" : "로그인 중…";
      var call = signup
        ? sb.auth.signUp({ email: email, password: pw })
        : sb.auth.signInWithPassword({ email: email, password: pw });
      call.then(function (res) {
        setBusy(authForm, false);
        if (res && res.error) {
          m.textContent = "";
          // 미확인 계정 로그인 시도 → 막다른 오류 대신 코드 입력 단계로 이어준다.
          if (!signup && classifyAuthError(res.error) === "unconfirmed") {
            pendingSignupEmail = email; saveSignupProgress(email);
            setMode("confirm");
            m.textContent = "가입 확인이 아직이에요 — " + email + " 로 보낸 확인 코드를 입력해 주세요. 코드가 없으면 아래에서 다시 받을 수 있어요.";
            return;
          }
          hint.textContent = authErr(res.error, signup);
          return;
        }
        if (signup) {
          if (res.data && res.data.session) { clearSignupProgress(); m.textContent = "가입되었습니다."; }
          else { pendingSignupEmail = email; saveSignupProgress(email); setMode("confirm"); m.textContent = "확인 코드를 " + email + " 로 보냈습니다."; }
        } else { m.textContent = "로그인되었습니다."; }
      }).catch(function () { setBusy(authForm, false); m.textContent = ""; hint.textContent = "요청에 실패했습니다. 잠시 후 다시 시도해 주세요."; });
    });

    // 회원가입 이메일 확인: 코드 검증(type:"signup")
    confirmForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var token = (confirmForm.querySelector("input").value || "").trim();
      if (!token) { hint.textContent = "메일로 받은 코드를 입력해 주세요."; return; }
      if (!pendingSignupEmail) { setMode("signup"); return; }
      setBusy(confirmForm, true); m.textContent = "확인 중…";
      sb.auth.verifyOtp({ email: pendingSignupEmail, token: token, type: "signup" }).then(function (res) {
        setBusy(confirmForm, false);
        if (res && res.error) { m.textContent = ""; hint.textContent = "코드가 맞지 않거나 시간이 지났어요. 다시 입력하거나 코드를 새로 받아 주세요."; return; }
        clearSignupProgress();
        m.textContent = "가입이 완료되었습니다.";
      }).catch(function () { setBusy(confirmForm, false); m.textContent = ""; hint.textContent = "확인에 실패했습니다. 다시 시도해 주세요."; });
    });

    // 재설정 1단계: 코드 발송
    reqcodeForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = (reqcodeForm.querySelector("input").value || "").trim();
      if (!EMAIL_RE.test(email)) { hint.textContent = "올바른 이메일 형식을 입력해 주세요."; return; }
      if (isAdminEmail(email)) { hint.textContent = "운영자 비밀번호 재설정은 Admin 페이지에서 진행하세요."; return; }
      setBusy(reqcodeForm, true); m.textContent = "전송 중…";
      sb.auth.resetPasswordForEmail(email).then(function (res) {
        setBusy(reqcodeForm, false);
        if (res && res.error) { m.textContent = ""; hint.textContent = "전송에 실패했습니다. 잠시 후 다시 시도해 주세요."; return; }
        resetEmail = email; setMode("newpw"); m.textContent = "재설정 코드를 " + email + " 로 보냈습니다.";
      }).catch(function () { setBusy(reqcodeForm, false); m.textContent = ""; hint.textContent = "전송에 실패했습니다. 잠시 후 다시 시도해 주세요."; });
    });

    // 재설정 2단계: 코드 검증(recovery) → 새 비밀번호 저장
    newpwForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var token = (newpwForm.querySelector('input[type="text"]').value || "").trim();
      var pw = (newpwForm.querySelector(".grm-pw-wrap input").value || "");
      if (!token) { hint.textContent = "메일로 받은 코드를 입력해 주세요."; return; }
      if (pw.length < 6) { hint.textContent = "새 비밀번호는 6자 이상이어야 합니다."; return; }
      if (!resetEmail) { setMode("reqcode"); return; }
      setBusy(newpwForm, true); m.textContent = "확인 중…";
      sb.auth.verifyOtp({ email: resetEmail, token: token, type: "recovery" }).then(function (res) {
        if (res && res.error) { setBusy(newpwForm, false); m.textContent = ""; hint.textContent = "코드가 맞지 않거나 시간이 지났어요. 다시 입력하거나 코드를 새로 받아 주세요."; return; }
        return sb.auth.updateUser({ password: pw }).then(function (up) {
          setBusy(newpwForm, false);
          if (up && up.error) { m.textContent = ""; hint.textContent = "비밀번호 변경에 실패했습니다. 다시 시도해 주세요."; return; }
          m.textContent = "비밀번호가 변경되어 로그인되었습니다.";
        });
      }).catch(function () { setBusy(newpwForm, false); m.textContent = ""; hint.textContent = "확인에 실패했습니다. 다시 시도해 주세요."; });
    });

    // 코드 재전송(쿨다운 30초)
    pop.querySelector(".grm-login-resend").addEventListener("click", function () {
      var mode = pop.getAttribute("data-mode");
      var email = mode === "confirm" ? pendingSignupEmail : resetEmail;
      if (!email) return;
      var call = mode === "confirm"
        ? sb.auth.resend({ type: "signup", email: email })
        : sb.auth.resetPasswordForEmail(email);
      startResendCooldown();
      if (mode === "confirm") saveSignupProgress(email);   // 재전송 = 진행 중 → 만료 시계 갱신
      m.textContent = "코드를 다시 보냈습니다.";
      call.then(function (res) {
        if (res && res.error) {
          m.textContent = classifyAuthError(res.error) === "rate_limit"
            ? "메일을 너무 자주 보냈어요. 잠시 후 다시 시도해 주세요."
            : "재전송에 실패했습니다. 잠시 후 다시 시도해 주세요.";
        }
      }).catch(function () {});
    });
  }

  // 분류 결과 → 사용자 문구(대중성 톤·다음 행동을 알려주는 한 문장). unknown 이면
  // 맥락별 기본 문구로 떨어진다(가입/로그인 각각 기존 문구 그대로 — 무회귀).
  function authErr(err, signup) {
    switch (classifyAuthError(err)) {
      case "exists":         return "이미 가입된 이메일이에요. 로그인하거나 비밀번호를 재설정해 주세요.";
      case "weak_password":  return "비밀번호는 6자 이상으로 정해 주세요.";
      case "rate_limit":     return "메일을 너무 자주 보냈어요. 잠시 후 다시 시도해 주세요.";
      case "unconfirmed":    return "가입 확인이 아직이에요. 메일로 받은 확인 코드를 입력해 주세요.";
      default:               return signup ? "가입에 실패했습니다. 잠시 후 다시 시도해 주세요."
                                           : "이메일 또는 비밀번호가 올바르지 않습니다.";
    }
  }

  function setBusy(form, busy) {
    var b = form.querySelector(".grm-login-primary");
    if (b) { b.disabled = !!busy; b.classList.toggle("is-busy", !!busy); }
  }

  function startResendCooldown() {
    var link = pop.querySelector(".grm-login-resend");
    var left = 30;
    link.disabled = true;
    if (resendTimer) clearInterval(resendTimer);
    link.textContent = "재전송 (" + left + "초)";
    resendTimer = setInterval(function () {
      left -= 1;
      if (left <= 0) { clearInterval(resendTimer); resendTimer = null; link.disabled = false; link.textContent = "코드 재전송"; }
      else { link.textContent = "재전송 (" + left + "초)"; }
    }, 1000);
  }

  // 모드 전환: 폼 표시/숨김 + 제목/안내 + 하단 링크 구성.
  function setMode(mode) {
    if (!pop) return;
    pop.setAttribute("data-mode", mode);
    var copy = MODE_COPY[mode] || MODE_COPY.login;
    var dlg = pop.querySelector(".grm-login-card");
    if (dlg) dlg.setAttribute("aria-label", copy.title);
    pop.querySelector(".grm-login-title").textContent = copy.title;
    pop.querySelector(".grm-login-sub").textContent = copy.sub;
    pop.querySelector(".grm-login-hint").textContent = "";

    var authForm    = pop.querySelector(".grm-login-auth");
    var confirmForm = pop.querySelector(".grm-login-confirm");
    var reqcodeForm = pop.querySelector(".grm-login-reqcode");
    var newpwForm   = pop.querySelector(".grm-login-newpw");
    var isAuth = (mode === "login" || mode === "signup");
    authForm.style.display    = isAuth ? "flex" : "none";
    confirmForm.style.display = (mode === "confirm") ? "flex" : "none";
    reqcodeForm.style.display = (mode === "reqcode") ? "flex" : "none";
    newpwForm.style.display   = (mode === "newpw") ? "flex" : "none";

    if (isAuth) {
      var pwIn = authForm.querySelector(".grm-pw-wrap input");
      pwIn.setAttribute("autocomplete", mode === "signup" ? "new-password" : "current-password");
      authForm.querySelector(".grm-login-primary").textContent = (mode === "signup") ? "가입하기" : "로그인";
    }

    // 코드 재전송 링크(confirm/newpw) + 개인정보 안내(signup)
    var resend = pop.querySelector(".grm-login-resend");
    var showResend = (mode === "confirm" || mode === "newpw");
    resend.style.display = showResend ? "block" : "none";
    if (showResend && !resend.disabled) resend.textContent = "코드 재전송";
    pop.querySelector(".grm-login-note").style.display = (mode === "signup") ? "block" : "none";

    // 하단 전환 링크
    var alt = pop.querySelector(".grm-login-alt");
    alt.innerHTML = "";
    function addLink(label, target, before) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "grm-login-link"; b.textContent = label;
      b.addEventListener("click", function () {
        if (before) before();
        setMode(target); pop.querySelector(".grm-login-msg").textContent = "";
      });
      alt.appendChild(b);
    }
    if (mode === "login") { addLink("회원가입", "signup"); addLink("비밀번호를 잊으셨나요?", "reqcode"); }
    else if (mode === "signup") { addLink("이미 계정이 있어요 · 로그인", "login"); }
    else if (mode === "confirm") {
      // 복원된 가입 흐름에서 빠져나갈 두 출구(막다른 길 0) — "다른 이메일"은 진행 상태를 버린다.
      addLink("로그인으로 돌아가기", "login");
      addLink("다른 이메일로 가입", "signup", function () { clearSignupProgress(); pendingSignupEmail = ""; });
    }
    else { addLink("로그인으로 돌아가기", "login"); }

    var focusForm = isAuth ? authForm
      : (mode === "confirm" ? confirmForm : (mode === "reqcode" ? reqcodeForm : newpwForm));
    var first = focusForm.querySelector("input");
    if (first) setTimeout(function () { first.focus(); }, 30);
  }

  // openLogin(opts) — opts 는 문자열(기존 호출부: 안내 문구) 또는 {mode, msg}.
  //   · mode:"signup" → 가입 의도가 분명한 진입점(펫 "구름이 안전하게 보관하기" 등)은
  //     로그인 화면을 한 번 거치지 않고 가입 폼으로 직행한다(하단에 "이미 계정이 있어요" 상시).
  //   · 진행 중인 가입(sessionStorage)이 있으면 요청 모드보다 **복원이 우선**한다 —
  //     팝업을 닫았다 다시 열어도 코드 입력 단계로 돌아오고, 재전송 버튼(쿨다운 30초)이 그대로 있다.
  function openLogin(opts) {
    if (!pop) buildPop();
    var o = (typeof opts === "string") ? { msg: opts } : (opts || {});
    Array.prototype.forEach.call(pop.querySelectorAll("input"), function (i) { i.value = ""; });
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-wrap input"), function (i) { i.type = "password"; });
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-toggle"), function (t) { t.innerHTML = EYE; t.setAttribute("aria-label", "비밀번호 표시"); });
    resetEmail = ""; pendingSignupEmail = "";
    if (resendTimer) { clearInterval(resendTimer); resendTimer = null; }
    var rs = pop.querySelector(".grm-login-resend"); rs.disabled = false;
    var resume = loadSignupProgress();
    var msg = o.msg || "";
    if (resume) {
      pendingSignupEmail = resume;
      setMode("confirm");
      msg = "가입을 이어서 마무리할 수 있어요 — " + resume + " 로 보낸 확인 코드를 입력해 주세요.";
    } else {
      setMode(o.mode === "signup" ? "signup" : "login");
    }
    pop.querySelector(".grm-login-msg").textContent = msg;
    pop.classList.add("show");
  }
  function closeLogin() { if (pop) pop.classList.remove("show"); }
  // 다른 자산(growth-sync.js 펫 CTA 등)이 별도 로그인 UI 발명 없이 모드를 지정해 여는 공개 진입점.
  window.GRM_AUTH.open = openLogin;

  // ── 하트 공개 집계(인기) ─────────────────────────────────────────────────
  function loadCounts() {
    sb.from("heart_counts").select("card_id,hearts").in("card_id", ids)
      .then(function (res) {
        var map = {};
        ((res && res.data) || []).forEach(function (r) { map[r.card_id] = r.hearts; });
        rows.forEach(function (row) {
          var c = row.querySelector('[data-react="heart"] [data-count]');
          if (c) c.textContent = String(map[row.getAttribute("data-anchor")] || 0);
        });
      })
      .catch(function () {});
  }

  // ── 내 반응 상태 ─────────────────────────────────────────────────────────
  function loadMine() {
    if (!session || !session.user) { clearMine(); return; }
    sb.from("reaction").select("card_id,kind")
      .then(function (res) {
        var set = {};
        ((res && res.data) || []).forEach(function (r) { set[r.kind + ":" + r.card_id] = 1; });
        rows.forEach(function (row) {
          var id = row.getAttribute("data-anchor");
          reactBtns(row).forEach(function (b) {
            var on = !!set[b.getAttribute("data-react") + ":" + id];
            b.classList.toggle("is-on", on);
            b.setAttribute("aria-pressed", on ? "true" : "false");
          });
        });
      })
      .catch(function () {});
  }
  function clearMine() {
    rows.forEach(function (row) {
      reactBtns(row).forEach(function (b) {
        b.classList.remove("is-on"); b.setAttribute("aria-pressed", "false");
      });
    });
  }

  // ── 토글(낙관적 UI + 실패 롤백) ──────────────────────────────────────────
  function bumpCount(row, delta) {
    var c = row.querySelector('[data-react="heart"] [data-count]'); if (!c) return;
    var n = parseInt(c.textContent, 10); if (isNaN(n)) n = 0;
    c.textContent = String(Math.max(0, n + delta));
  }
  function toggle(btn, row) {
    if (!session || !session.user) { openLogin(); return; }
    var kind = btn.getAttribute("data-react");
    var id = row.getAttribute("data-anchor");
    var uid = session.user.id;
    var on = btn.classList.contains("is-on");
    btn.classList.toggle("is-on", !on);
    btn.setAttribute("aria-pressed", !on ? "true" : "false");
    if (kind === "heart") bumpCount(row, on ? -1 : 1);
    function rollback() {
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      if (kind === "heart") bumpCount(row, on ? 1 : -1);
    }
    var op = on
      ? sb.from("reaction").delete().match({ user_id: uid, card_id: id, kind: kind })
      : sb.from("reaction").insert({ user_id: uid, card_id: id, kind: kind });
    op.then(function (res) { if (res && res.error) rollback(); }).catch(rollback);
  }

  // ── /me 마이페이지 ───────────────────────────────────────────────────────
  // [비로그인 불침번] 13차부터 /me 는 로그인 게이트가 아니라 개인 홈이다 — 게스트로 와도
  // 구름이 성장 현황(localStorage)은 그대로 보이고, 계정 카드는 빈칸이 아니라 "무엇이
  // 보관되는지 + 가입" 안내로 채운다. 가입 CTA 는 이 카드 한 곳에만 두고(페이지에 로그인
  // 버튼이 세 개씩 생기지 않게), 진입점은 기존 openLogin 을 재사용한다 — 새 인증 UI 0.
  function renderMeGuestHead(head) {
    head.innerHTML =
      '<div class="grm-me-card grm-me-guest">' +
      '<span class="grm-acct-av grm-acct-av-xl" aria-hidden="true"><i class="ti ti-user"></i></span>' +
      '<div class="grm-me-idbox"><div class="grm-me-label">게스트</div>' +
      '<div class="grm-me-email">아직 로그인하지 않았어요</div>' +
      '<p class="grm-me-guest-s">가입하면 스크랩·관심 업체·구름이가 계정에 보관되어 어느 기기에서든 이어집니다. 아래 구름이 기록은 지금도 이 브라우저에 쌓이고 있어요.</p>' +
      '<div class="grm-me-metaline">' +
      '<button type="button" class="grm-me-signup">가입하고 시작하기</button>' +
      '<button type="button" class="grm-me-out grm-me-login">이미 계정이 있어요 · 로그인</button>' +
      '</div></div></div>';
    // 가입 의도가 분명한 진입점이라 로그인 화면을 한 번 거치지 않고 가입 폼으로 직행한다
    // (growth-sync.js 펫 CTA 와 동일 계약 — window.GRM_AUTH.open({mode:"signup"})).
    head.querySelector(".grm-me-signup").addEventListener("click", function () { openLogin({ mode: "signup" }); });
    head.querySelector(".grm-me-login").addEventListener("click", function () { openLogin(); });
  }

  // /me 계정 헤더(아바타·이메일·스크랩 수) — 런타임 주입.
  function renderMeHead(count) {
    var head = document.getElementById("grm-me-head");
    if (!head) return;
    if (!session || !session.user) { renderMeGuestHead(head); return; }
    var email = session.user.email || "회원", ini = acctInitial(email);
    var stat = (count == null) ? "" : ('<span class="grm-me-stat"><b>' + count + '</b> 스크랩</span>');
    head.innerHTML =
      '<div class="grm-me-card">' +
      '<span class="grm-acct-av grm-acct-av-xl">' + esc(ini) + '</span>' +
      '<div class="grm-me-idbox"><div class="grm-me-label">로그인 계정</div>' +
      '<div class="grm-me-email">' + esc(email) + '</div>' +
      '<div class="grm-me-metaline">' + stat +
      '<button type="button" class="grm-me-out">로그아웃</button></div></div></div>';
    var out = head.querySelector(".grm-me-out");
    if (out) out.addEventListener("click", function () { sb.auth.signOut(); });
  }
  // Supabase 스크랩 card_id + search-index.json(card_id→제목·기관·링크)로 목록을 런타임 렌더.
  // provenance: 제목·링크는 우리 인덱스에서만, Supabase 엔 불투명 card_id 만.
  function renderMyScraps() {
    if (!myScrapsEl) return;
    renderMeHead(null);
    if (!session || !session.user) {
      // 로그인 버튼은 상단 게스트 카드 하나로 모았다(같은 화면에 CTA 난립 금지) —
      // 여기선 무엇이 모이는지만 알려 주고 조용히 폴백한다. 관심 업체 섹션과 동형.
      myScrapsEl.innerHTML = '<p class="grm-my-note">로그인하면 스크랩한 카드를 이곳에 모아볼 수 있어요.</p>';
      return;
    }
    myScrapsEl.innerHTML = '<p class="grm-my-note">불러오는 중…</p>';
    var idxUrl = myScrapsEl.getAttribute("data-index") || "/assets/search-index.json";
    Promise.all([
      sb.from("reaction").select("card_id,created_at").eq("kind", "scrap"),
      fetch(idxUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (out) {
      var res = out[0], idx = out[1];
      if (res && res.error) { myScrapsEl.innerHTML = '<p class="grm-my-note">불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>'; return; }
      var scraps = (res && res.data) ? res.data.slice() : [];
      if (!scraps.length) {
        myScrapsEl.innerHTML =
          '<div class="grm-my-empty"><span class="grm-my-empty-ic"><i class="ti ti-bookmark" aria-hidden="true"></i></span>' +
          '<p class="grm-my-empty-t">아직 스크랩한 카드가 없어요</p>' +
          '<p class="grm-my-empty-s">카드의 스크랩 버튼을 누르면 이곳에 모여요.</p>' +
          '<a class="grm-my-cta" href="' + esc(cfgRoot) + 'archive/index.html">규제뉴스 둘러보기</a></div>';
        renderMeHead(0);
        return;
      }
      scraps.sort(function (a, c) { return (c.created_at || "").localeCompare(a.created_at || ""); });
      var byAnchor = {};
      if (idx && idx.cards) {
        idx.cards.forEach(function (e) {
          var h = e.href || "", i = h.indexOf("#");
          if (i >= 0) byAnchor[h.slice(i + 1)] = e;
        });
      }
      var ul = document.createElement("ul"); ul.className = "grm-my-list";
      scraps.forEach(function (sc) {
        var e = byAnchor[sc.card_id], li = document.createElement("li");
        li.className = "grm-my-item";
        if (e) {
          var a = document.createElement("a"); a.className = "grm-my-a"; a.href = e.href;
          a.textContent = (e.target || "") + (e.issue ? (" — " + e.issue) : "") || "카드 보기";
          var meta = document.createElement("span"); meta.className = "grm-my-meta";
          meta.textContent = [e.agency, e.date].filter(Boolean).join(" · ");
          li.appendChild(a); li.appendChild(meta);
        } else {
          var sp = document.createElement("span"); sp.className = "grm-my-meta";
          sp.textContent = "저장된 카드 (원문을 찾지 못함)";
          li.appendChild(sp);
        }
        var rm = document.createElement("button"); rm.type = "button"; rm.className = "grm-my-rm"; rm.textContent = "스크랩 해제";
        rm.addEventListener("click", function () {
          rm.disabled = true;
          sb.from("reaction").delete().match({ user_id: session.user.id, card_id: sc.card_id, kind: "scrap" })
            .then(function (d) {
              if (d && d.error) { rm.disabled = false; return; }
              if (li.parentNode) li.parentNode.removeChild(li);
              if (!ul.children.length) renderMyScraps();
            }).catch(function () { rm.disabled = false; });
        });
        li.appendChild(rm); ul.appendChild(li);
      });
      myScrapsEl.innerHTML = ""; myScrapsEl.appendChild(ul);
      renderMeHead(scraps.length);
    });
  }

  // [firm_name 엔티티 디코드 M5] findings.js/trends.js/firm.js 의 동명 헬퍼와 동일 계약
  // (별도 파일이라 재사용 불가, 계약만 복제) — DB firm_display 에 &amp;/&#039; 가 이미
  // 이스케이프된 채로 저장된 행을 표시 직전(textContent 대입 전)에만 되돌린다.
  function decodeFirmDisplay(s) {
    return String(s || "").replace(/&amp;/g, "&").replace(/&#039;/g, "'");
  }

  // ── /me 페이지: 관심 업체 목록(015_firm_watchlist.sql — 등록은 업체 프로파일에서) ──
  // 스크랩 목록(renderMyScraps)과 동일 관례: 본인 행만(RLS), created_at 최신순 클라이언트
  // 정렬, 오류(015 미적용 포함)는 오류처럼 보이지 않는 노트로 조용히 폴백. Supabase 엔
  // 불투명 firm_key + 등록 시점 표시명 스냅샷(firm_display)만 있다 — 업체 사실 미저장.
  function renderMyFirms() {
    if (!myFirmsEl) return;
    if (!session || !session.user) {
      myFirmsEl.innerHTML = '<p class="grm-my-note">로그인하면 관심 업체를 모아볼 수 있어요.</p>';
      return;
    }
    myFirmsEl.innerHTML = '<p class="grm-my-note">불러오는 중…</p>';
    sb.from("firm_watchlist").select("firm_key,firm_display,created_at")
      .then(function (res) {
        // 015 미적용(테이블 부재 → PostgREST 오류) 포함 — "준비 중" 노트로 조용히 폴백.
        if (res && res.error) {
          myFirmsEl.innerHTML = '<p class="grm-my-note">관심 업체 목록 준비 중입니다.</p>';
          return;
        }
        var firms = (res && res.data) ? res.data.slice() : [];
        if (!firms.length) {
          myFirmsEl.innerHTML =
            '<div class="grm-my-empty"><span class="grm-my-empty-ic"><i class="ti ti-building-factory-2" aria-hidden="true"></i></span>' +
            '<p class="grm-my-empty-t">아직 등록한 업체가 없습니다</p>' +
            '<p class="grm-my-empty-s">업체 프로파일에서 관심 업체로 등록하세요.</p>' +
            '<a class="grm-my-cta" href="' + esc(cfgRoot) + 'findings/index.html">규제 지적사항 검색하기</a></div>';
          return;
        }
        firms.sort(function (a, c) { return (c.created_at || "").localeCompare(a.created_at || ""); });
        var ul = document.createElement("ul"); ul.className = "grm-my-list";
        firms.forEach(function (fw) {
          var li = document.createElement("li");
          li.className = "grm-my-item";
          var a = document.createElement("a"); a.className = "grm-my-a";
          a.href = cfgRoot + "findings/firm/index.html?key=" + encodeURIComponent(fw.firm_key);
          a.textContent = decodeFirmDisplay(fw.firm_display) || fw.firm_key;
          var meta = document.createElement("span"); meta.className = "grm-my-meta";
          meta.textContent = "등록 " + String(fw.created_at || "").slice(0, 10);
          li.appendChild(a); li.appendChild(meta);
          var rm = document.createElement("button"); rm.type = "button"; rm.className = "grm-my-rm"; rm.textContent = "해제";
          rm.addEventListener("click", function () {
            rm.disabled = true;
            sb.from("firm_watchlist").delete().match({ user_id: session.user.id, firm_key: fw.firm_key })
              .then(function (d) {
                if (d && d.error) { rm.disabled = false; return; }
                if (li.parentNode) li.parentNode.removeChild(li);
                if (!ul.children.length) renderMyFirms();
              }).catch(function () { rm.disabled = false; });
          });
          li.appendChild(rm); ul.appendChild(li);
        });
        myFirmsEl.innerHTML = ""; myFirmsEl.appendChild(ul);
      })
      .catch(function () {
        myFirmsEl.innerHTML = '<p class="grm-my-note">불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>';
      });
  }

  // ── 배선 ─────────────────────────────────────────────────────────────────
  document.body.classList.add("grm-reactions-on");
  rows.forEach(function (row) {
    reactBtns(row).forEach(function (b) {
      b.addEventListener("click", function () { toggle(b, row); });
    });
  });
  sb.auth.getSession().then(function (res) {
    session = (res && res.data) ? res.data.session : null;
    renderAuth(); if (rows.length) loadMine(); renderMyScraps(); renderMyFirms();
  }).catch(function () { renderAuth(); renderMyScraps(); renderMyFirms(); });
  sb.auth.onAuthStateChange(function (_evt, s) {
    session = s; renderAuth(); if (rows.length) loadMine(); renderMyScraps(); renderMyFirms();
    if (s && s.user) { clearSignupProgress(); closeLogin(); }   // 세션 성립 = 가입 흐름 종료
  });
  if (rows.length) loadCounts();
})();
