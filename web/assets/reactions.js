/* GRM 웹 카드 반응 계층(하트·스크랩·회원) — S1 / B안(Supabase 직접 호출·RLS).
   비골든: 런타임에 카운트/상태만 주입한다(콘텐츠 골든·결정론 불침범). progressive enhancement —
   설정/라이브러리/로그인/네트워크 실패는 전부 삼켜 정적 카드 열람·공유(S0)에 무영향. env-gate
   (base.html {% if reactions_enabled %})로 SUPABASE_URL/ANON_KEY 설정 시에만 로드된다.
   provenance: 카드 사실·원문 URL 미참조 — 불투명 card_id(=card.anchor)만 취급. */
(function () {
  "use strict";
  var cfg = document.getElementById("grm-reactions-cfg");
  var lib = window.supabase;
  if (!cfg || !lib || !lib.createClient) return;
  var SUPA_URL = cfg.getAttribute("data-url") || "";
  var SUPA_KEY = cfg.getAttribute("data-key") || "";
  if (!SUPA_URL || !SUPA_KEY) return;

  var sb;
  try { sb = lib.createClient(SUPA_URL, SUPA_KEY); } catch (e) { return; }

  var rows = Array.prototype.slice.call(
    document.querySelectorAll(".grm-card-actions[data-anchor]"));
  var myScrapsEl = document.getElementById("grm-my-scraps");   // /me 페이지 컨테이너(있으면 마이페이지)
  if (!rows.length && !myScrapsEl) return;
  var ids = rows.map(function (r) { return r.getAttribute("data-anchor"); }).filter(Boolean);
  var cfgRoot = cfg.getAttribute("data-root") || "";
  var session = null;

  function reactBtns(row) {
    return Array.prototype.slice.call(row.querySelectorAll(".grm-ca-react[data-react]"));
  }

  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
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
  function renderAuth() {
    var el = ensureAuthEl(); if (!el) return;
    el.innerHTML = "";
    if (session && session.user) {
      var meLink = document.createElement("a");
      meLink.href = cfgRoot + "me/index.html";
      meLink.className = "grm-my-link";
      meLink.textContent = "내 스크랩";
      el.appendChild(meLink);
      var who = document.createElement("span");
      who.className = "grm-auth-who";
      who.textContent = session.user.email || "회원";
      var out = document.createElement("button");
      out.type = "button"; out.textContent = "로그아웃";
      out.addEventListener("click", function () { sb.auth.signOut(); });
      el.appendChild(who); el.appendChild(out);
    } else {
      var login = document.createElement("button");
      login.type = "button"; login.textContent = "로그인";
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
  var pop, resetEmail = "", pendingSignupEmail = "", resendTimer = null;
  var MODE_COPY = {
    login:   { title: "로그인",          sub: "이메일과 비밀번호로 로그인하세요." },
    signup:  { title: "회원가입",        sub: "이메일과 비밀번호로 가입하세요. 확인 코드를 메일로 보내드립니다." },
    confirm: { title: "이메일 인증",      sub: "가입을 마치려면 메일로 받은 코드를 입력하세요." },
    reqcode: { title: "비밀번호 재설정",   sub: "가입한 이메일로 재설정 코드를 보내드립니다." },
    newpw:   { title: "새 비밀번호 설정",  sub: "메일로 받은 코드와 새 비밀번호를 입력하세요." }
  };

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
      '<p class="grm-login-note" style="display:none">가입 시 최소한의 정보(이메일)만 수집하며, 비밀번호는 안전하게 암호화되어 저장됩니다.</p>' +
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
          hint.textContent = signup ? signupErr(res.error) : "이메일 또는 비밀번호가 올바르지 않습니다.";
          return;
        }
        if (signup) {
          if (res.data && res.data.session) { m.textContent = "가입되었습니다."; }
          else { pendingSignupEmail = email; setMode("confirm"); m.textContent = "확인 코드를 " + email + " 로 보냈습니다."; }
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
        if (res && res.error) { m.textContent = ""; hint.textContent = "코드가 올바르지 않거나 만료됐습니다. 다시 시도하거나 코드를 재전송해 주세요."; return; }
        m.textContent = "가입이 완료되었습니다.";
      }).catch(function () { setBusy(confirmForm, false); m.textContent = ""; hint.textContent = "확인에 실패했습니다. 다시 시도해 주세요."; });
    });

    // 재설정 1단계: 코드 발송
    reqcodeForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = (reqcodeForm.querySelector("input").value || "").trim();
      if (!EMAIL_RE.test(email)) { hint.textContent = "올바른 이메일 형식을 입력해 주세요."; return; }
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
        if (res && res.error) { setBusy(newpwForm, false); m.textContent = ""; hint.textContent = "코드가 올바르지 않거나 만료됐습니다. 다시 시도하거나 코드를 재전송해 주세요."; return; }
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
      m.textContent = "코드를 다시 보냈습니다.";
      call.then(function (res) { if (res && res.error) { m.textContent = "재전송에 실패했습니다. 잠시 후 다시 시도해 주세요."; } }).catch(function () {});
    });
  }

  function signupErr(err) {
    var msg = (err && err.message) || "";
    if (/registered|exists|already/i.test(msg)) return "이미 가입된 이메일입니다. 로그인하거나 비밀번호를 재설정해 주세요.";
    if (/password/i.test(msg)) return "비밀번호가 정책에 맞지 않습니다. (6자 이상)";
    return "가입에 실패했습니다. 잠시 후 다시 시도해 주세요.";
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
    function addLink(label, target) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "grm-login-link"; b.textContent = label;
      b.addEventListener("click", function () { setMode(target); pop.querySelector(".grm-login-msg").textContent = ""; });
      alt.appendChild(b);
    }
    if (mode === "login") { addLink("회원가입", "signup"); addLink("비밀번호를 잊으셨나요?", "reqcode"); }
    else if (mode === "signup") { addLink("이미 계정이 있어요 · 로그인", "login"); }
    else { addLink("로그인으로 돌아가기", "login"); }

    var focusForm = isAuth ? authForm
      : (mode === "confirm" ? confirmForm : (mode === "reqcode" ? reqcodeForm : newpwForm));
    var first = focusForm.querySelector("input");
    if (first) setTimeout(function () { first.focus(); }, 30);
  }

  function openLogin(msg) {
    if (!pop) buildPop();
    Array.prototype.forEach.call(pop.querySelectorAll("input"), function (i) { i.value = ""; });
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-wrap input"), function (i) { i.type = "password"; });
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-toggle"), function (t) { t.innerHTML = EYE; t.setAttribute("aria-label", "비밀번호 표시"); });
    resetEmail = ""; pendingSignupEmail = "";
    if (resendTimer) { clearInterval(resendTimer); resendTimer = null; }
    var rs = pop.querySelector(".grm-login-resend"); rs.disabled = false;
    setMode("login");
    pop.querySelector(".grm-login-msg").textContent = msg || "";
    pop.classList.add("show");
  }
  function closeLogin() { if (pop) pop.classList.remove("show"); }

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

  // ── /me 페이지: 내 스크랩 목록(호를 넘나들며) ─────────────────────────────
  // Supabase 스크랩 card_id + search-index.json(card_id→제목·기관·링크)로 목록을 런타임 렌더.
  // provenance: 제목·링크는 우리 인덱스에서만, Supabase 엔 불투명 card_id 만.
  function renderMyScraps() {
    if (!myScrapsEl) return;
    if (!session || !session.user) {
      myScrapsEl.innerHTML = '<p class="grm-my-note">로그인하면 스크랩한 카드를 모아볼 수 있어요.</p>';
      var lb = document.createElement("button");
      lb.type = "button"; lb.className = "grm-my-login"; lb.textContent = "로그인";
      lb.addEventListener("click", function () { openLogin(); });
      myScrapsEl.appendChild(lb);
      return;
    }
    myScrapsEl.innerHTML = '<p class="grm-my-note">불러오는 중…</p>';
    var idxUrl = myScrapsEl.getAttribute("data-index") || "assets/search-index.json";
    Promise.all([
      sb.from("reaction").select("card_id,created_at").eq("kind", "scrap"),
      fetch(idxUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (out) {
      var res = out[0], idx = out[1];
      if (res && res.error) { myScrapsEl.innerHTML = '<p class="grm-my-note">불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>'; return; }
      var scraps = (res && res.data) ? res.data.slice() : [];
      if (!scraps.length) { myScrapsEl.innerHTML = '<p class="grm-my-note">아직 스크랩한 카드가 없어요. 카드의 스크랩 버튼을 눌러 저장해 보세요.</p>'; return; }
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
    renderAuth(); loadMine(); renderMyScraps();
  }).catch(function () { renderAuth(); renderMyScraps(); });
  sb.auth.onAuthStateChange(function (_evt, s) {
    session = s; renderAuth(); loadMine(); renderMyScraps();
    if (s && s.user) closeLogin();
  });
  if (rows.length) loadCounts();
})();
