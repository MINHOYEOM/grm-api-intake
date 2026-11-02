/* GRM 웹 카드 반응 계층(하트·스크랩·회원) — S1 / B안(Supabase 직접 호출·RLS).
   비골든: 런타임에 카운트/상태만 주입한다(콘텐츠 골든·결정론 불침범). progressive enhancement —
   설정/라이브러리/로그인/네트워크 실패는 전부 삼켜 정적 카드 열람·공유(S0)에 무영향. env-gate
   (base.html {% if reactions_enabled %})로 SUPABASE_URL/ANON_KEY 설정 시에만 로드된다.
   provenance: 카드 사실·원문 URL 미참조 — 불투명 card_id(=card.anchor)만 취급. */
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
  // 뉴스레터 구독 폼 주소(Brevo 호스팅 폼 — 이 언어 판의 리스트). 비어 있으면 가입 칸·안내 카드 모두 꺼진다.
  var SUB_ACTION = cfg.getAttribute("data-sub") || "";
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
  var myTermsEl = document.getElementById("grm-my-terms");     // /me '저장한 용어'(용어사전 저장)
  var myFirmsEl = document.getElementById("grm-my-firms");     // /me 관심 업체 컨테이너(015 워치리스트)
  // [P2 관심 범위 · 067] /me 관심 범위 컨테이너 + 어휘(render 가 facets 정본에서 심음).
  var myIntEl = document.getElementById("grm-my-interests");
  var intVocabEl = document.getElementById("grm-interest-vocab");
  // [P2 개인화] 둘러보기 첫 화면의 '내 범위의 최근 공개'(기본 hidden).
  var mineEl = document.getElementById("fnd-mine");
  var mineSubEl = document.getElementById("fnd-mine-sub");
  var mineListEl = document.getElementById("fnd-mine-list");
  // 로그인/계정 UI(renderAuth)는 카드 유무와 무관하게 모든 페이지 헤더에 필요하므로 조기 종료하지 않는다.
  // (카드 반응·집계는 rows, 마이페이지는 myScrapsEl 로 각각 개별 가드된다.)
  var ids = rows.map(function (r) { return r.getAttribute("data-anchor"); }).filter(Boolean);
  var cfgRoot = cfg.getAttribute("data-root") || "";
  var session = null;

  function reactBtns(row) {
    return Array.prototype.slice.call(row.querySelectorAll(".grm-ca-react[data-react]"));
  }

  // ── [용어 저장] 용어사전 저장은 카드 스크랩과 **같은 reaction 테이블**을 쓴다 ───────
  // card_id 는 저장 계층에 불투명한 문자열일 뿐이라(형식 검사 없음) `glossary:<id>` 를
  // 그대로 넣는다 — 새 테이블·마이그레이션 0. 대신 두 목록을 **여기서** 갈라야 한다:
  // /me '내 스크랩' 은 이 접두를 빼고, '저장한 용어' 는 이 접두만 센다.
  var TERM_PREFIX = "glossary:";
  function isTermId(id) { return String(id || "").indexOf(TERM_PREFIX) === 0; }
  function termIdOf(id) { return String(id || "").slice(TERM_PREFIX.length); }

  // 비로그인 클릭의 보류 저장 — 로그인/가입을 마치면 그 클릭을 대신 수행한다.
  // 저장 버튼이 곧 가입 이유인데, 로그인하고 돌아왔을 때 아무 일도 안 일어나 있으면
  // 그 이유가 사라진다. localStorage(탭·리다이렉트를 건너야 한다 — 가입 확인 메일을
  // 거치면 다른 페이지로 돌아온다)에 **무엇을 누르려 했는지만** 둔다: 세션·토큰 미저장.
  // 24시간이 지난 의도는 버린다(그때의 클릭을 지금 대신 눌러 줄 이유가 없다).
  // 저장·읽기 실패(프라이빗 모드·차단)는 조용히 삼켜 기존 동작으로 폴백한다.
  var PENDING_KEY = "grm-pending-react";
  var PENDING_TTL_MS = 24 * 60 * 60 * 1000;
  function savePendingReact(id, kind) {
    try { localStorage.setItem(PENDING_KEY, JSON.stringify({ id: id, kind: kind, ts: Date.now() })); }
    catch (e) {}
  }
  // 읽으면서 지운다 — 성공·충돌·오류 어느 쪽으로 끝나도 같은 클릭이 다시 되살아나지 않는다.
  function takePendingReact() {
    var raw = null;
    try { raw = localStorage.getItem(PENDING_KEY); } catch (e) { return null; }
    if (!raw) return null;
    try { localStorage.removeItem(PENDING_KEY); } catch (e) {}
    var d = null;
    try { d = JSON.parse(raw); } catch (e) { return null; }   // 깨진 JSON — 조용히 버린다
    if (!d || !d.id || !d.kind) return null;
    if (typeof d.ts !== "number" || (Date.now() - d.ts) > PENDING_TTL_MS) return null;
    return d;
  }
  // 세션이 생긴 뒤 1회 — 이미 저장돼 있으면 PK 충돌이 나는데 그건 원하던 상태라 무시한다.
  // 그 anchor 가 이 페이지에 없어도(확인 메일을 거쳐 다른 면으로 돌아온 경우) 저장은 한다.
  function applyPendingReact() {
    if (!session || !session.user) return;
    var d = takePendingReact(); if (!d) return;
    sb.from("reaction").insert({ user_id: session.user.id, card_id: d.id, kind: d.kind })
      .then(function () { loadMine(); renderMyScraps(); renderMyTerms(); })
      .catch(function () {});
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
      var email = session.user.email || _t("회원"), ini = acctInitial(email);
      var wrap = document.createElement("div"); wrap.className = "grm-acct";
      wrap.innerHTML =
        '<button type="button" class="grm-acct-btn" aria-haspopup="menu" aria-expanded="false" aria-label="' + _t("계정 메뉴") + '">' +
        '<span class="grm-acct-av">' + esc(ini) + '</span>' +
        '<i class="ti ti-chevron-down grm-acct-cv" aria-hidden="true"></i></button>' +
        '<div class="grm-acct-menu" role="menu" hidden>' +
        '<div class="grm-acct-head"><span class="grm-acct-av grm-acct-av-lg">' + esc(ini) + '</span>' +
        '<span class="grm-acct-id"><span class="grm-acct-label">' + _t("로그인 계정") + '</span>' +
        '<span class="grm-acct-email">' + esc(email) + '</span></span></div>' +
        '<div class="grm-acct-div"></div>' +
        // 13차: /me 가 스크랩 전용에서 개인 홈(스크랩·구름이·관심 업체)으로 넓어져
        // 메뉴 이름도 실제와 맞췄다 — 링크·아이콘·위치는 그대로(새 표면 0).
        '<a class="grm-acct-item" role="menuitem" href="' + esc(cfgRoot) + 'me/index.html">' +
        '<i class="ti ti-bookmark" aria-hidden="true"></i>' + _t("마이페이지") + '</a>' +
        '<button type="button" class="grm-acct-item grm-acct-out" role="menuitem">' +
        '<i class="ti ti-logout" aria-hidden="true"></i>' + _t("로그아웃") + '</button></div>';
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
      login.type = "button"; login.className = "grm-acct-login"; login.textContent = _t("로그인");
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
  var pop, resetEmail = "", pendingSignupEmail = "", pendingOptin = false, resendTimer = null, holdPop = false;
  var MODE_COPY = {
    login:   { title: _t("로그인"),          sub: _t("이메일과 비밀번호만 있으면 됩니다.") },
    signup:  { title: _t("회원가입"),        sub: _t("이메일과 비밀번호만 정하면 끝이에요. 확인 코드를 메일로 보내드립니다.") },
    confirm: { title: _t("이메일 확인"),      sub: _t("메일로 받은 코드를 넣으면 가입이 끝나요.") },
    done:    { title: _t("가입이 완료되었습니다"), sub: _t("스크랩·관심 업체·구름이가 이 계정에 보관됩니다.") },
    reqcode: { title: _t("비밀번호 재설정"),   sub: _t("가입한 이메일로 재설정 코드를 보내드립니다.") },
    newpw:   { title: _t("새 비밀번호 설정"),  sub: _t("메일로 받은 코드와 새 비밀번호를 입력하세요.") }
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
      pendingOptin = readOptinFlag();
      return d.email;
    } catch (e) { return ""; }
  }
  // 뉴스레터 동의 체크 여부(코드 입력 단계 안내 문구용) — 진행 상태(이메일+시각)와 **별도 키**다.
  // 이 값은 문구만 정한다: 실제 구독 여부는 가입 때 서버에 남긴 user_metadata.newsletter_optin 이
  // 정하므로 새로고침·재로그인으로 이 키가 사라져도 동의는 유지된다(서버는 이 키를 보지 않는다).
  var OPTIN_KEY = "grm-signup-optin-v1";
  function saveOptinFlag(on) {
    try { if (on) sessionStorage.setItem(OPTIN_KEY, "1"); else sessionStorage.removeItem(OPTIN_KEY); } catch (e) {}
  }
  function readOptinFlag() {
    try { return sessionStorage.getItem(OPTIN_KEY) === "1"; } catch (e) { return false; }
  }
  function clearSignupProgress() {
    try { sessionStorage.removeItem(SIGNUP_KEY); sessionStorage.removeItem(OPTIN_KEY); } catch (e) {}
  }

  // 비밀번호 요건(가입·재설정). 로그인은 옛 6자 비밀번호 회원을 위해 요건을 검사하지 않는다.
  var PW_MIN = 8;
  function pwCheck(pw) { return { len: pw.length >= PW_MIN, letter: /[A-Za-z]/.test(pw), digit: /[0-9]/.test(pw) }; }
  function pwOk(pw) { var c = pwCheck(pw); return c.len && c.letter && c.digit; }
  var TERMS_VER = "terms-2026-11-03";   // 가입 시 동의한 약관·처리방침 시행일 — 문서를 바꾸면 올린다
  function legalHref(p) { return esc(cfgRoot) + p + "index.html"; }   // 언어 트리마다 같은 상대 경로(한국어 /privacy/, 영문 /en/privacy/)
  function pwField(ph, ac) {
    return '<span class="grm-pw-wrap"><input type="password" required minlength="6" autocomplete="' + ac +
      '" placeholder="' + ph + '" aria-label="' + ph + '" />' +
      '<button type="button" class="grm-pw-toggle" aria-label="' + _t("비밀번호 표시") + '">' + EYE + '</button></span>';
  }

  function buildPop() {
    pop = document.createElement("div");
    pop.className = "grm-login-pop";
    pop.innerHTML =
      '<div class="grm-login-card" role="dialog" aria-modal="true" aria-label="' + _t("로그인") + '">' +
      '<button type="button" class="grm-login-x" aria-label="' + _t("닫기") + '">×</button>' +
      '<div class="grm-login-brand">' + OWL_SVG + '<span>Global Regulatory Monitor</span></div>' +
      '<h3 class="grm-login-title">' + _t("로그인") + '</h3>' +
      '<p class="grm-login-sub"></p>' +
      // 로그인·회원가입 공용 폼(이메일 + 비밀번호)
      '<ol class="grm-steps" aria-label="' + _t("가입 단계") + '" hidden>' +
      '<li data-s="1">' + _t("계정") + '</li><li data-s="2">' + _t("이메일 확인") + '</li><li data-s="3">' + _t("완료") + '</li></ol>' +
      '<form class="grm-login-form grm-login-auth" novalidate>' +
      '<label class="grm-f"><span class="grm-f-l">' + _t("이메일") + '</span>' +
      '<input type="email" required autocomplete="email" placeholder="you@company.com" aria-describedby="grm-e-email" /></label>' +
      '<p class="grm-f-err" id="grm-e-email"></p>' +
      '<label class="grm-f"><span class="grm-f-l">' + _t("비밀번호") + '</span>' +
      pwField(_t("비밀번호"), "current-password") + '</label>' +
      '<p class="grm-f-err" id="grm-e-pw"></p>' +
      '<ul class="grm-pw-req" style="display:none" aria-live="polite">' +
      '<li data-r="len">' + _t("8자 이상") + '</li><li data-r="letter">' + _t("영문 포함") + '</li><li data-r="digit">' + _t("숫자 포함") + '</li></ul>' +
      '<div class="grm-consents" style="display:none">' +
      '<label class="grm-login-optin grm-consent"><input type="checkbox" class="grm-c-age" />' +
      '<span>' + _t("(필수) 만 14세 이상입니다") + '</span></label>' +
      '<label class="grm-login-optin grm-consent"><input type="checkbox" class="grm-c-terms" />' +
      '<span>' + _t("(필수) {terms}과 {privacy}에 동의합니다", { terms: '<a href="' + legalHref("terms/") + '" target="_blank" rel="noopener">' + _t("이용약관") + '</a>', privacy: '<a href="' + legalHref("privacy/") + '" target="_blank" rel="noopener">' + _t("개인정보처리방침") + '</a>' }) + '</span></label>' +
      '<p class="grm-f-err" id="grm-e-consent"></p>' +
      (SUB_ACTION && !lsGet(SUB_OK_KEY)
        ? '<label class="grm-login-optin"><input type="checkbox" class="grm-login-optin-cb" />' +
          '<span>' + _t("(선택) 주간 브리프·새 기능 안내 메일 받기") + '<small>' + _t("매주 월요일 규제 소식(브리프)과 사이트 새 기능 안내를 이메일로 받습니다. 가입 확인 코드를 입력하면 구독도 함께 완료되며, 모든 메일 하단의 수신거부 링크로 언제든 해지할 수 있어요.") + '</small></span></label>'
        : '') +
      '</div>' +
      '<button type="submit" class="grm-login-primary">' + _t("로그인") + '</button></form>' +
      // 회원가입 이메일 확인: 코드
      '<form class="grm-login-form grm-login-confirm" style="display:none" novalidate>' +
      '<label class="grm-f"><span class="grm-f-l">' + _t("확인 코드") + '</span>' +
      '<input type="text" inputmode="numeric" pattern="[0-9]*" maxlength="10" autocomplete="one-time-code" placeholder="' + _t("메일로 받은 확인 코드") + '" class="grm-code" /></label>' +
      '<button type="submit" class="grm-login-primary">' + _t("가입 완료") + '</button></form>' +
      '<div class="grm-login-done" style="display:none"><div class="grm-done-ic" aria-hidden="true">✓</div>' +
      '<p class="grm-done-news" style="display:none"></p>' +
      '<button type="button" class="grm-login-primary grm-done-go">' + _t("계속 둘러보기") + '</button></div>' +
      // 재설정 1단계: 이메일
      '<form class="grm-login-form grm-login-reqcode" style="display:none" novalidate>' +
      '<input type="email" required autocomplete="email" placeholder="' + _t("가입한 이메일") + '" aria-label="' + _t("이메일") + '" />' +
      '<button type="submit" class="grm-login-primary">' + _t("재설정 코드 받기") + '</button></form>' +
      // 재설정 2단계: 코드 + 새 비밀번호
      '<form class="grm-login-form grm-login-newpw" style="display:none" novalidate>' +
      '<input type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="' + _t("메일로 받은 코드") + '" aria-label="' + _t("재설정 코드") + '" />' +
      pwField(_t("새 비밀번호(8자 이상, 영문+숫자)"), "new-password") +
      '<button type="submit" class="grm-login-primary">' + _t("비밀번호 변경") + '</button></form>' +
      '<p class="grm-login-hint" role="alert"></p>' +
      '<p class="grm-login-msg" role="status" aria-live="polite"></p>' +
      '<button type="button" class="grm-login-resend" style="display:none"></button>' +
      '<p class="grm-login-note" style="display:none">' + _t("가입하면 스크랩·관심 업체·구름이가 계정에 보관되어 어느 기기에서든 이어집니다. 이메일 외에는 아무것도 받지 않으며, 비밀번호는 안전하게 암호화되어 저장됩니다.") + '</p>' +
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

    // 접근성: ESC 로 닫고, Tab 은 카드 안에 가둔다(모달 밖 요소로 포커스가 새지 않게 —
    // feedback.js 의 동일 트랩과 같은 계약). 팝업이 실제로 열려 있을 때만 반응한다.
    pop.addEventListener("keydown", function (e) {
      if (!pop.classList.contains("show")) return;
      if (e.key === "Escape") { closeLogin(); return; }
      if (e.key !== "Tab") return;
      var f = pop.querySelectorAll("button, select, textarea, input, a[href]");
      var vis = [];
      for (var i = 0; i < f.length; i++) {
        if (f[i].offsetParent !== null && !f[i].disabled) vis.push(f[i]);
      }
      if (!vis.length) return;
      var first = vis[0], last = vis[vis.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    // 비밀번호 표시/숨김 토글
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-toggle"), function (tg) {
      tg.addEventListener("click", function () {
        var inp = tg.parentNode.querySelector("input");
        var show = inp.type === "password";
        inp.type = show ? "text" : "password";
        tg.innerHTML = show ? EYE_OFF : EYE;
        tg.setAttribute("aria-label", show ? _t("비밀번호 숨김") : _t("비밀번호 표시"));
      });
    });
    // 입력 시 힌트·필드 오류 해제 + 비밀번호 요건 실시간 표시
    Array.prototype.forEach.call(pop.querySelectorAll("input"), function (inp) {
      inp.addEventListener("input", function () { hint.textContent = ""; clearFieldErr(inp); });
    });
    var pwInput = authForm.querySelector(".grm-pw-wrap input");
    var reqList = authForm.querySelector(".grm-pw-req");
    pwInput.addEventListener("input", function () {
      var c = pwCheck(pwInput.value);
      Array.prototype.forEach.call(reqList.querySelectorAll("li"), function (li) { li.classList.toggle("ok", !!c[li.getAttribute("data-r")]); });
    });
    Array.prototype.forEach.call(authForm.querySelectorAll(".grm-consents input"), function (cb) {
      cb.addEventListener("change", function () { setFieldErr(null, authForm.querySelector("#grm-e-consent"), ""); });
    });
    pop.querySelector(".grm-done-go").addEventListener("click", closeLogin);

    // 로그인/회원가입(이메일+비밀번호)
    authForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var emailIn = authForm.querySelector('input[type="email"]');
      var email = (emailIn.value || "").trim();
      var pw = (pwInput.value || "");
      var signup = pop.getAttribute("data-mode") === "signup";
      var firstBad = null;
      function bad(input, errEl, msg) { setFieldErr(input, errEl, msg); if (!firstBad) firstBad = input; }
      if (!email) bad(emailIn, authForm.querySelector("#grm-e-email"), _t("이메일을 입력해 주세요."));
      else if (!EMAIL_RE.test(email)) bad(emailIn, authForm.querySelector("#grm-e-email"), _t("올바른 이메일 형식을 입력해 주세요."));
      else if (isAdminEmail(email)) bad(emailIn, authForm.querySelector("#grm-e-email"), _t("운영자 계정은 Admin 페이지에서 로그인하세요."));
      if (!pw) bad(pwInput, authForm.querySelector("#grm-e-pw"), _t("비밀번호를 입력해 주세요."));
      else if (signup && !pwOk(pw)) bad(pwInput, authForm.querySelector("#grm-e-pw"), _t("비밀번호는 8자 이상, 영문과 숫자를 함께 넣어 주세요."));
      var ageCb = authForm.querySelector(".grm-c-age"), termsCb = authForm.querySelector(".grm-c-terms");
      if (signup && !(ageCb.checked && termsCb.checked)) {
        bad(!ageCb.checked ? ageCb : termsCb, authForm.querySelector("#grm-e-consent"),
          !ageCb.checked ? _t("만 14세 이상만 가입할 수 있어요.") : _t("이용약관과 개인정보처리방침에 동의해 주세요."));
      }
      if (firstBad) { firstBad.focus(); return; }
      var optinCb = authForm.querySelector(".grm-login-optin-cb");
      var wantsNews = !!(signup && optinCb && optinCb.checked);
      var meta = { newsletter_prompted: true, newsletter_optin: false };
      if (wantsNews) meta = { newsletter_prompted: true, newsletter_optin: true, newsletter_consent_ver: SIGNUP_CONSENT_VER };
      if (signup) { meta.age14_confirmed = true; meta.terms_ver = TERMS_VER; meta.terms_agreed_at = new Date().toISOString(); }
      setBusy(authForm, true); m.textContent = signup ? _t("가입 중…") : _t("로그인 중…");
      if (signup) holdPop = true;
      var call = signup
        ? sb.auth.signUp({ email: email, password: pw, options: { data: meta } })
        : sb.auth.signInWithPassword({ email: email, password: pw });
      call.then(function (res) {
        setBusy(authForm, false);
        if (res && res.error) {
          holdPop = false;
          m.textContent = "";
          // 미확인 계정 로그인 시도 → 막다른 오류 대신 코드 입력 단계로 이어준다.
          if (!signup && classifyAuthError(res.error) === "unconfirmed") {
            pendingSignupEmail = email; saveSignupProgress(email);
            setMode("confirm");
            m.textContent = _t("가입 확인이 아직이에요 — {email} 로 보낸 확인 코드를 입력해 주세요. 코드가 없으면 아래에서 다시 받을 수 있어요.", { email: email });
            return;
          }
          hint.textContent = authErr(res.error, signup);
          return;
        }
        if (signup) {
          pendingOptin = wantsNews;
          if (res.data && res.data.session) { clearSignupProgress(); setMode("done"); if (wantsNews) subscribeServer(res.data.session.access_token, "signup-optin", SIGNUP_CONSENT_VER); }
          else { holdPop = false; pendingSignupEmail = email; saveSignupProgress(email); saveOptinFlag(wantsNews); setMode("confirm"); m.textContent = _t("확인 코드를 {email} 로 보냈습니다.", { email: email }); startResendCooldown(); }
        } else { m.textContent = _t("로그인되었습니다."); }
      }).catch(function () { holdPop = false; setBusy(authForm, false); m.textContent = ""; hint.textContent = _t("요청에 실패했습니다. 잠시 후 다시 시도해 주세요."); });
    });

    // 회원가입 이메일 확인: 코드 검증(type:"signup")
    confirmForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var token = (confirmForm.querySelector("input").value || "").trim();
      if (!token) { hint.textContent = _t("메일로 받은 코드를 입력해 주세요."); return; }
      if (!pendingSignupEmail) { setMode("signup"); return; }
      setBusy(confirmForm, true); m.textContent = _t("확인 중…");
      holdPop = true;
      sb.auth.verifyOtp({ email: pendingSignupEmail, token: token, type: "signup" }).then(function (res) {
        setBusy(confirmForm, false);
        if (res && res.error) { holdPop = false; m.textContent = ""; hint.textContent = _t("코드가 맞지 않거나 시간이 지났어요. 다시 입력하거나 코드를 새로 받아 주세요."); return; }
        clearSignupProgress();
        setMode("done");
        // 이메일 확인이 끝난 지금에서야 구독을 서버에 요청한다(가입 화면에서 체크한 기록이 user_metadata 에 있을 때만 서버가 받는다).
        var vs = res && res.data, vmeta = vs && vs.user && vs.user.user_metadata;
        if (vs && vs.session && vmeta && vmeta.newsletter_optin === true) subscribeServer(vs.session.access_token, "signup-optin", vmeta.newsletter_consent_ver || SIGNUP_CONSENT_VER);
      }).catch(function () { holdPop = false; setBusy(confirmForm, false); m.textContent = ""; hint.textContent = _t("확인에 실패했습니다. 다시 시도해 주세요."); });
    });

    // 재설정 1단계: 코드 발송
    reqcodeForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = (reqcodeForm.querySelector("input").value || "").trim();
      if (!EMAIL_RE.test(email)) { hint.textContent = _t("올바른 이메일 형식을 입력해 주세요."); return; }
      if (isAdminEmail(email)) { hint.textContent = _t("운영자 비밀번호 재설정은 Admin 페이지에서 진행하세요."); return; }
      setBusy(reqcodeForm, true); m.textContent = _t("전송 중…");
      sb.auth.resetPasswordForEmail(email).then(function (res) {
        setBusy(reqcodeForm, false);
        if (res && res.error) { m.textContent = ""; hint.textContent = _t("전송에 실패했습니다. 잠시 후 다시 시도해 주세요."); return; }
        resetEmail = email; setMode("newpw"); m.textContent = _t("재설정 코드를 {email} 로 보냈습니다.", { email: email });
      }).catch(function () { setBusy(reqcodeForm, false); m.textContent = ""; hint.textContent = _t("전송에 실패했습니다. 잠시 후 다시 시도해 주세요."); });
    });

    // 재설정 2단계: 코드 검증(recovery) → 새 비밀번호 저장
    newpwForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var token = (newpwForm.querySelector('input[type="text"]').value || "").trim();
      var pw = (newpwForm.querySelector(".grm-pw-wrap input").value || "");
      if (!token) { hint.textContent = _t("메일로 받은 코드를 입력해 주세요."); return; }
      if (!pwOk(pw)) { hint.textContent = _t("비밀번호는 8자 이상, 영문과 숫자를 함께 넣어 주세요."); return; }
      if (!resetEmail) { setMode("reqcode"); return; }
      setBusy(newpwForm, true); m.textContent = _t("확인 중…");
      sb.auth.verifyOtp({ email: resetEmail, token: token, type: "recovery" }).then(function (res) {
        if (res && res.error) { setBusy(newpwForm, false); m.textContent = ""; hint.textContent = _t("코드가 맞지 않거나 시간이 지났어요. 다시 입력하거나 코드를 새로 받아 주세요."); return; }
        return sb.auth.updateUser({ password: pw }).then(function (up) {
          setBusy(newpwForm, false);
          if (up && up.error) { m.textContent = ""; hint.textContent = _t("비밀번호 변경에 실패했습니다. 다시 시도해 주세요."); return; }
          m.textContent = _t("비밀번호가 변경되어 로그인되었습니다.");
        });
      }).catch(function () { setBusy(newpwForm, false); m.textContent = ""; hint.textContent = _t("확인에 실패했습니다. 다시 시도해 주세요."); });
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
      m.textContent = _t("코드를 다시 보냈습니다.");
      call.then(function (res) {
        if (res && res.error) {
          m.textContent = classifyAuthError(res.error) === "rate_limit"
            ? _t("메일을 너무 자주 보냈어요. 잠시 후 다시 시도해 주세요.")
            : _t("재전송에 실패했습니다. 잠시 후 다시 시도해 주세요.");
        }
      }).catch(function () {});
    });
  }

  // 분류 결과 → 사용자 문구(대중성 톤·다음 행동을 알려주는 한 문장). unknown 이면
  // 맥락별 기본 문구로 떨어진다(가입/로그인 각각 기존 문구 그대로 — 무회귀).
  function authErr(err, signup) {
    switch (classifyAuthError(err)) {
      case "exists":         return _t("이미 가입된 이메일이에요. 로그인하거나 비밀번호를 재설정해 주세요.");
      case "weak_password":  return _t("비밀번호는 8자 이상, 영문과 숫자를 함께 넣어 주세요.");
      case "rate_limit":     return _t("메일을 너무 자주 보냈어요. 잠시 후 다시 시도해 주세요.");
      case "unconfirmed":    return _t("가입 확인이 아직이에요. 메일로 받은 확인 코드를 입력해 주세요.");
      default:               return signup ? _t("가입에 실패했습니다. 잠시 후 다시 시도해 주세요.")
                                           : _t("이메일 또는 비밀번호가 올바르지 않습니다.");
    }
  }

  // ── 뉴스레터 옵트인(회원 ↔ 구독자는 별개 명단) ────────────────────────────────
  // 구독 등록은 서버(Edge Function newsletter-optin)가 한다: 이메일 확인이 끝난 로그인 회원의
  // **JWT 본인 이메일만** Brevo 리스트에 올리고, 동의 근거(출처·문구 버전·시각)를 남긴다.
  // 브라우저는 이메일을 보내지 않는다(남의 주소 구독 불가). 동의 없는 자동 구독 0 — 가입 화면에서
  // 체크한 사람, 안내 카드의 '구독하기'를 누른 사람만. 사이트 구독 밴드 폼은 별개(Brevo 더블 옵트인).
  var SUB_OK_KEY = "grm-sub-ok";          // 구독 밴드와 같은 키(구독 요청한 브라우저 — 밴드·배너 숨김)
  var NUDGE_KEY = "grm-sub-nudge-v1";     // 로그인 후 안내를 이미 보인 브라우저
  var SIGNUP_CONSENT_VER = "optin-v2";    // 가입 화면 동의 문구 버전 — 문구를 바꾸면 올린다
  var NUDGE_CONSENT_VER = "nudge-v1";     // 안내 카드 동의 문구 버전
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k) { try { localStorage.setItem(k, "1"); } catch (e) {} }
  function subscribeServer(token, source, ver) {
    if (!SUB_ACTION || !window.fetch || !token) return Promise.resolve({});
    var lang = ((document.documentElement && document.documentElement.lang) || "ko").slice(0, 2) === "en" ? "en" : "ko";
    return fetch(SUPA_URL + "/functions/v1/newsletter-optin", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token, "apikey": SUPA_KEY },
      body: JSON.stringify({ source: source, consent_ver: ver, lang: lang })
    }).then(function (r) { return r.json().catch(function () { return {}; }); }).then(function (b) {
      if (b && b.ok) lsSet(SUB_OK_KEY);
      return b || {};
    }).catch(function () { return {}; });
  }

  // 기존 회원 안내 — 로그인 후 한 번만, 일괄 등록 없이 묻는다. 이미 구독 요청한 브라우저·이미 보인
  // 브라우저·가입 때 선택을 마친 회원(user_metadata.newsletter_prompted)에게는 뜨지 않는다.
  var nudgeEl = null;
  function maybeNudge(user) {
    if (nudgeEl || !SUB_ACTION || !user || !user.email) return;
    if (isAdminEmail(user.email)) return;
    var md = user.user_metadata || {};
    if (md.newsletter_prompted || lsGet(SUB_OK_KEY) || lsGet(NUDGE_KEY)) return;
    nudgeEl = document.createElement("div");
    nudgeEl.className = "grm-nudge";
    nudgeEl.setAttribute("role", "dialog");
    nudgeEl.setAttribute("aria-label", _t("주간 뉴스레터, 받아보시겠어요?"));
    nudgeEl.innerHTML =
      '<b>' + _t("주간 뉴스레터, 받아보시겠어요?") + '</b>' +
      '<p>' + _t("매주 월요일 규제 소식과 새 기능 안내를 보내드려요. 누르면 바로 구독되며, 메일 하단 수신거부 링크로 언제든 해지할 수 있어요.") + '</p>' +
      '<div class="grm-nudge-row"><button type="button" class="grm-nudge-yes">' + _t("구독하기") + '</button>' +
      '<button type="button" class="grm-nudge-no">' + _t("괜찮아요") + '</button></div>';
    document.body.appendChild(nudgeEl);
    lsSet(NUDGE_KEY);   // 보인 것으로 기록 — 닫거나 새로고침해도 다시 묻지 않는다
    function close() {
      if (nudgeEl && nudgeEl.parentNode) nudgeEl.parentNode.removeChild(nudgeEl);
    }
    nudgeEl.querySelector(".grm-nudge-no").addEventListener("click", function () {
      close();
      // 다른 기기에서 또 묻지 않도록 회원 정보에 '물어봤다'만 남긴다(실패해도 무시 — localStorage 가 1차).
      try { sb.auth.updateUser({ data: { newsletter_prompted: true } }).catch(function () {}); } catch (e) {}
    });
    nudgeEl.querySelector(".grm-nudge-yes").addEventListener("click", function () {
      var yes = nudgeEl.querySelector(".grm-nudge-yes");
      yes.disabled = true;
      var tok = session && session.access_token;
      subscribeServer(tok, "login-nudge", NUDGE_CONSENT_VER).then(function (b) {
        var msg = (b && b.ok)
          ? (b.status === "blocked_unsubscribed"
              ? _t("이전에 수신거부한 주소예요. 페이지 아래 구독 폼에서 다시 신청할 수 있어요.")
              : _t("구독이 완료되었습니다. 매주 월요일에 보내드려요."))
          : _t("구독을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.");
        if (!(b && b.ok)) { yes.disabled = false; }
        nudgeEl.innerHTML = '<p style="margin:0" role="status">' + msg + '</p>';
        if (b && b.ok) setTimeout(close, 5000);
        else setTimeout(close, 6000);
      });
    });
  }

  // 필드 오류: 입력칸에 aria-invalid, 메시지는 aria-describedby 로 연결된 <p> 에 둔다.
  function setFieldErr(input, errEl, msg) {
    if (errEl) errEl.textContent = msg || "";
    if (input) { if (msg) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid"); }
  }
  function clearFieldErr(input) {
    if (!input || !input.removeAttribute) return;
    input.removeAttribute("aria-invalid");
    var d = input.getAttribute("aria-describedby"), el = d && document.getElementById(d);
    if (el && el.classList.contains("grm-f-err")) el.textContent = "";
    if (input.closest && input.closest(".grm-pw-wrap")) { var pe = document.getElementById("grm-e-pw"); if (pe) pe.textContent = ""; }
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
    link.textContent = _t("재전송 ({left}초)", { left: left });
    resendTimer = setInterval(function () {
      left -= 1;
      if (left <= 0) { clearInterval(resendTimer); resendTimer = null; link.disabled = false; link.textContent = _t("코드 재전송"); }
      else { link.textContent = _t("재전송 ({left}초)", { left: left }); }
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
    pop.querySelector(".grm-login-sub").textContent = copy.sub + ((mode === "confirm" && pendingOptin && SUB_ACTION) ? " " + _t("뉴스레터 구독도 이 코드 입력으로 함께 완료됩니다.") : "");
    pop.querySelector(".grm-login-hint").textContent = "";

    var authForm    = pop.querySelector(".grm-login-auth");
    var confirmForm = pop.querySelector(".grm-login-confirm");
    var reqcodeForm = pop.querySelector(".grm-login-reqcode");
    var newpwForm   = pop.querySelector(".grm-login-newpw");
    var isAuth = (mode === "login" || mode === "signup");
    var doneBox = pop.querySelector(".grm-login-done");
    doneBox.style.display = (mode === "done") ? "flex" : "none";
    var steps = pop.querySelector(".grm-steps"), stepNo = { signup: 1, confirm: 2, done: 3 }[mode];
    steps.hidden = !stepNo;
    Array.prototype.forEach.call(steps.querySelectorAll("li"), function (li) {
      var n = +li.getAttribute("data-s");
      li.classList.toggle("on", n === stepNo); li.classList.toggle("past", n < stepNo);
      if (n === stepNo) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    if (mode === "done") {
      pop.querySelector(".grm-login-msg").textContent = "";
      var dn = pop.querySelector(".grm-done-news");
      dn.style.display = (pendingOptin && SUB_ACTION) ? "block" : "none";
      dn.textContent = _t("뉴스레터 구독도 함께 신청되었어요. 매주 월요일에 보내드려요.");
    }
    authForm.style.display    = isAuth ? "flex" : "none";
    confirmForm.style.display = (mode === "confirm") ? "flex" : "none";
    reqcodeForm.style.display = (mode === "reqcode") ? "flex" : "none";
    newpwForm.style.display   = (mode === "newpw") ? "flex" : "none";

    if (isAuth) {
      var pwIn = authForm.querySelector(".grm-pw-wrap input");
      pwIn.setAttribute("autocomplete", mode === "signup" ? "new-password" : "current-password");
      authForm.querySelector(".grm-login-primary").textContent = (mode === "signup") ? _t("가입하기") : _t("로그인");
      authForm.querySelector(".grm-pw-wrap input").placeholder = (mode === "signup") ? _t("8자 이상, 영문+숫자") : "";
      authForm.querySelector(".grm-consents").style.display = (mode === "signup") ? "block" : "none";
      authForm.querySelector(".grm-pw-req").style.display = (mode === "signup") ? "flex" : "none";
      var optinCb0 = authForm.querySelector(".grm-login-optin-cb");
      if (optinCb0 && mode !== "signup") optinCb0.checked = false;   // 기본 해제 — 모드를 오가도 미리 체크되지 않는다
      Array.prototype.forEach.call(authForm.querySelectorAll("[aria-invalid]"), function (x) { x.removeAttribute("aria-invalid"); });
      Array.prototype.forEach.call(authForm.querySelectorAll(".grm-f-err"), function (x) { x.textContent = ""; });
    }

    // 코드 재전송 링크(confirm/newpw) + 개인정보 안내(signup)
    var resend = pop.querySelector(".grm-login-resend");
    var showResend = (mode === "confirm" || mode === "newpw");
    resend.style.display = showResend ? "block" : "none";
    if (showResend && !resend.disabled) resend.textContent = _t("코드 재전송");
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
    if (mode === "login") { addLink(_t("회원가입"), "signup"); addLink(_t("비밀번호를 잊으셨나요?"), "reqcode"); }
    else if (mode === "signup") { addLink(_t("이미 계정이 있어요 · 로그인"), "login"); }
    else if (mode === "confirm") {
      // 복원된 가입 흐름에서 빠져나갈 두 출구(막다른 길 0) — "다른 이메일"은 진행 상태를 버린다.
      addLink(_t("로그인으로 돌아가기"), "login");
      addLink(_t("다른 이메일로 가입"), "signup", function () { clearSignupProgress(); pendingSignupEmail = ""; });
    }
    else if (mode === "done") { /* 마지막 단계 — 되돌아갈 곳 없음 */ }
    else { addLink(_t("로그인으로 돌아가기"), "login"); }

    var focusForm = isAuth ? authForm
      : (mode === "confirm" ? confirmForm : (mode === "reqcode" ? reqcodeForm : (mode === "done" ? doneBox : newpwForm)));
    var first = focusForm.querySelector("input, button");
    if (first) setTimeout(function () { first.focus(); }, 30);
  }

  // openLogin(opts) — opts 는 문자열(기존 호출부: 안내 문구) 또는 {mode, msg}.
  //   · mode:"signup" → 가입 의도가 분명한 진입점(펫 "구름이 안전하게 보관하기" 등)은
  //     로그인 화면을 한 번 거치지 않고 가입 폼으로 직행한다(하단에 "이미 계정이 있어요" 상시).
  //   · 진행 중인 가입(sessionStorage)이 있으면 요청 모드보다 **복원이 우선**한다 —
  //     팝업을 닫았다 다시 열어도 코드 입력 단계로 돌아오고, 재전송 버튼(쿨다운 30초)이 그대로 있다.
  function openLogin(opts) {
    if (!pop) buildPop();
    holdPop = false;
    var o = (typeof opts === "string") ? { msg: opts } : (opts || {});
    Array.prototype.forEach.call(pop.querySelectorAll("input"), function (i) { i.value = ""; if (i.type === "checkbox") i.checked = false; });
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-wrap input"), function (i) { i.type = "password"; });
    Array.prototype.forEach.call(pop.querySelectorAll(".grm-pw-toggle"), function (t) { t.innerHTML = EYE; t.setAttribute("aria-label", _t("비밀번호 표시")); });
    resetEmail = ""; pendingSignupEmail = "";
    if (resendTimer) { clearInterval(resendTimer); resendTimer = null; }
    var rs = pop.querySelector(".grm-login-resend"); rs.disabled = false;
    var resume = loadSignupProgress();
    var msg = o.msg || "";
    if (resume) {
      pendingSignupEmail = resume;
      setMode("confirm");
      msg = _t("가입을 이어서 마무리할 수 있어요 — {resume} 로 보낸 확인 코드를 입력해 주세요.", { resume: resume });
    } else {
      setMode(o.mode === "signup" ? "signup" : "login");
    }
    pop.querySelector(".grm-login-msg").textContent = msg;
    pop.classList.add("show");
  }
  function closeLogin() { holdPop = false; if (pop) pop.classList.remove("show"); }
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
    if (!session || !session.user) {
      // 누르려던 것을 기억해 두고 연다 — 로그인/가입이 끝나면 applyPendingReact 가 대신 누른다.
      savePendingReact(row.getAttribute("data-anchor"), btn.getAttribute("data-react"));
      openLogin({ msg: _t("로그인하면 저장됩니다.") });
      return;
    }
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

  // ── 계정 삭제 ─────────────────────────────────────────────────────────────
  // 본인 JWT 로 Edge Function account-delete 를 부른다(서버가 관리자 거부·최근 인증 5분·
  // Brevo/문의 정리·auth 삭제를 순서대로 한다). 재인증은 비밀번호 재입력(signInWithPassword)
  // — 이메일 코드 방식은 Supabase Magic Link 템플릿이 코드({{ .Token }})를 담아야 해서 피했다.
  // 되돌릴 수 없으므로 이메일 재입력 + 비밀번호 + 확인 체크 셋이 모두 있어야 버튼이 눌린다.
  var delPop = null;
  function closeDeleteAccount() { if (delPop) { delPop.parentNode.removeChild(delPop); delPop = null; } }
  function openDeleteAccount() {
    if (!session || !session.user || delPop) return;
    var email = session.user.email || "";
    delPop = document.createElement("div");
    delPop.className = "grm-login-pop show";
    delPop.innerHTML =
      '<div class="grm-login-card" role="dialog" aria-modal="true" aria-label="' + _t("계정 삭제") + '">' +
      '<button type="button" class="grm-login-x" aria-label="' + _t("닫기") + '">×</button>' +
      '<h3 class="grm-login-title">' + _t("계정 삭제") + '</h3>' +
      '<p class="grm-login-sub">' + _t("계정과 저장한 스크랩·용어·관심 업체·학습 기록이 모두 삭제되고 뉴스레터도 해지됩니다. 되돌릴 수 없습니다.") + '</p>' +
      '<form class="grm-login-form" novalidate>' +
      '<input type="email" class="grm-del-email" autocomplete="off" placeholder="' + _t("이메일을 다시 입력해 주세요") + '" aria-label="' + _t("이메일") + '" />' +
      '<input type="password" class="grm-del-pw" autocomplete="current-password" placeholder="' + _t("비밀번호") + '" aria-label="' + _t("비밀번호") + '" />' +
      '<label class="grm-login-optin"><input type="checkbox" class="grm-del-ok" />' +
      '<span>' + _t("삭제하면 되돌릴 수 없다는 것을 확인했습니다.") + '</span></label>' +
      '<button type="submit" class="grm-login-primary" disabled>' + _t("계정 삭제") + '</button></form>' +
      '<p class="grm-login-hint" role="alert"></p>' +
      '<p class="grm-login-msg" role="status" aria-live="polite"></p></div>';
    document.body.appendChild(delPop);
    var f = delPop.querySelector("form"), em = f.querySelector(".grm-del-email"),
        pw = f.querySelector(".grm-del-pw"), ok = f.querySelector(".grm-del-ok"),
        go = f.querySelector("button[type=submit]"),
        hint = delPop.querySelector(".grm-login-hint"), msg = delPop.querySelector(".grm-login-msg");
    function ready() {
      go.disabled = !(em.value.trim().toLowerCase() === email.toLowerCase() && pw.value && ok.checked);
    }
    [em, pw, ok].forEach(function (el) { el.addEventListener("input", ready); el.addEventListener("change", ready); });
    delPop.querySelector(".grm-login-x").addEventListener("click", closeDeleteAccount);
    delPop.addEventListener("click", function (e) { if (e.target === delPop) closeDeleteAccount(); });
    delPop.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDeleteAccount(); });
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      if (go.disabled) return;
      hint.textContent = ""; msg.textContent = _t("삭제 중…"); go.disabled = true;
      var fail = function (t) { msg.textContent = ""; hint.textContent = t; ready(); };
      sb.auth.signInWithPassword({ email: email, password: pw.value }).then(function (res) {
        if (res && res.error) { fail(_t("비밀번호가 맞지 않습니다.")); return null; }
        var tok = res && res.data && res.data.session && res.data.session.access_token;
        if (!tok) { fail(_t("삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.")); return null; }
        return fetch(SUPA_URL + "/functions/v1/account-delete", {
          method: "POST",
          headers: { "Authorization": "Bearer " + tok, "apikey": SUPA_KEY, "Content-Type": "application/json" },
          body: "{}"
        }).then(function (r) {
          if (r.ok) {
            try { sb.auth.signOut({ scope: "local" }); } catch (x) {}
            delPop.querySelector(".grm-login-card").innerHTML =
              '<h3 class="grm-login-title">' + _t("계정이 삭제되었습니다") + '</h3>' +
              '<p class="grm-login-sub">' + _t("이용해 주셔서 감사합니다.") + '</p>' +
              '<a class="grm-login-primary" style="display:block;text-align:center;text-decoration:none" href="' + esc(cfgRoot) + 'index.html">' + _t("홈으로") + '</a>';
            return null;
          }
          return r.json().catch(function () { return {}; }).then(function (j) {
            fail(j && j.error === "admin_protected"
              ? _t("운영자 계정은 여기서 삭제할 수 없습니다.")
              : _t("삭제하지 못했습니다. 잠시 후 다시 시도해 주세요."));
          });
        });
      }).catch(function () { fail(_t("삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.")); });
    });
    em.focus();
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
      '<div class="grm-me-idbox"><div class="grm-me-label">' + _t("게스트") + '</div>' +
      '<div class="grm-me-email">' + _t("아직 로그인하지 않았어요") + '</div>' +
      '<p class="grm-me-guest-s">' + _t("가입하면 저장한 용어·스크랩·관심 업체·구름이가 계정에 보관되어 어느 기기에서든 이어집니다. 아래 구름이 기록은 지금도 이 브라우저에 쌓이고 있어요.") + '</p>' +
      '<div class="grm-me-metaline">' +
      '<button type="button" class="grm-me-signup">' + _t("가입하고 시작하기") + '</button>' +
      '<button type="button" class="grm-me-out grm-me-login">' + _t("이미 계정이 있어요 · 로그인") + '</button>' +
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
    var email = session.user.email || _t("회원"), ini = acctInitial(email);
    var stat = (count == null) ? "" : ('<span class="grm-me-stat">' + _t("<b>{count}</b> 스크랩", { count: count }) + '</span>');
    head.innerHTML =
      '<div class="grm-me-card">' +
      '<span class="grm-acct-av grm-acct-av-xl">' + esc(ini) + '</span>' +
      '<div class="grm-me-idbox"><div class="grm-me-label">' + _t("로그인 계정") + '</div>' +
      '<div class="grm-me-email">' + esc(email) + '</div>' +
      '<div class="grm-me-metaline">' + stat +
      '<button type="button" class="grm-me-out">' + _t("로그아웃") + '</button>' +
      '<button type="button" class="grm-me-out grm-me-del">' + _t("계정 삭제") + '</button></div></div></div>';
    var out = head.querySelector(".grm-me-out");
    if (out) out.addEventListener("click", function () { sb.auth.signOut(); });
    var del = head.querySelector(".grm-me-del");
    if (del) del.addEventListener("click", openDeleteAccount);
  }
  // Supabase 스크랩 card_id + search-index.json(card_id→제목·기관·링크)로 목록을 런타임 렌더.
  // provenance: 제목·링크는 우리 인덱스에서만, Supabase 엔 불투명 card_id 만.
  function renderMyScraps() {
    if (!myScrapsEl) return;
    renderMeHead(null);
    if (!session || !session.user) {
      // 로그인 버튼은 상단 게스트 카드 하나로 모았다(같은 화면에 CTA 난립 금지) —
      // 여기선 무엇이 모이는지만 알려 주고 조용히 폴백한다. 관심 업체 섹션과 동형.
      myScrapsEl.innerHTML = '<p class="grm-my-note">' + _t("로그인하면 스크랩한 카드를 이곳에 모아볼 수 있어요.") + '</p>';
      return;
    }
    myScrapsEl.innerHTML = '<p class="grm-my-note">' + _t("불러오는 중…") + '</p>';
    var idxUrl = myScrapsEl.getAttribute("data-index") || "/assets/search-index.json";
    Promise.all([
      sb.from("reaction").select("card_id,created_at").eq("kind", "scrap"),
      fetch(idxUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (out) {
      var res = out[0], idx = out[1];
      if (res && res.error) { myScrapsEl.innerHTML = '<p class="grm-my-note">' + _t("불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") + '</p>'; return; }
      // 용어 저장(`glossary:`)은 같은 테이블에 있지만 이 목록의 것이 아니다 — 걸러 내지
      // 않으면 인덱스에 없는 id 라 "원문을 찾지 못함" 행이 된다(수도 같이 틀린다).
      var scraps = ((res && res.data) || []).filter(function (r) { return !isTermId(r.card_id); });
      if (!scraps.length) {
        myScrapsEl.innerHTML =
          '<div class="grm-my-empty"><span class="grm-my-empty-ic"><i class="ti ti-bookmark" aria-hidden="true"></i></span>' +
          '<p class="grm-my-empty-t">' + _t("아직 스크랩한 카드가 없어요") + '</p>' +
          '<p class="grm-my-empty-s">' + _t("카드의 스크랩 버튼을 누르면 이곳에 모여요.") + '</p>' +
          '<a class="grm-my-cta" href="' + esc(cfgRoot) + 'archive/index.html">' + _t("규제뉴스 둘러보기") + '</a></div>';
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
          a.textContent = (e.target || "") + (e.issue ? (" — " + e.issue) : "") || _t("카드 보기");
          var meta = document.createElement("span"); meta.className = "grm-my-meta";
          meta.textContent = [e.agency, e.date].filter(Boolean).join(" · ");
          li.appendChild(a); li.appendChild(meta);
        } else {
          var sp = document.createElement("span"); sp.className = "grm-my-meta";
          sp.textContent = _t("저장된 카드 (원문을 찾지 못함)");
          li.appendChild(sp);
        }
        var rm = document.createElement("button"); rm.type = "button"; rm.className = "grm-my-rm"; rm.textContent = _t("스크랩 해제");
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

  // ── /me 페이지: 저장한 용어(용어사전 [저장]) ────────────────────────────
  // 스크랩 목록과 같은 관례다 — 본인 행만(RLS), created_at 최신순 클라이언트 정렬, 실패는
  // 오류처럼 보이지 않는 노트로 조용히 폴백. 다른 것은 둘뿐이다: (1) 같은 reaction 테이블에서
  // `glossary:` 접두 행만 골라내고, (2) 제목·링크를 search-index 가 아니라 용어 인덱스
  // (glossary-index[.en].json)에서 푼다. Supabase 엔 여기서도 불투명 id 만 있다.
  function renderMyTerms() {
    if (!myTermsEl) return;
    if (!session || !session.user) {
      myTermsEl.innerHTML = '<p class="grm-my-note">' + _t("로그인하면 저장한 용어를 이곳에 모아볼 수 있어요.") + '</p>';
      return;
    }
    myTermsEl.innerHTML = '<p class="grm-my-note">' + _t("불러오는 중…") + '</p>';
    var idxUrl = myTermsEl.getAttribute("data-index") || "/assets/glossary-index.json";
    Promise.all([
      sb.from("reaction").select("card_id,created_at").eq("kind", "scrap"),
      fetch(idxUrl).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (out) {
      var res = out[0], idx = out[1];
      if (res && res.error) { myTermsEl.innerHTML = '<p class="grm-my-note">' + _t("불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") + '</p>'; return; }
      var saved = ((res && res.data) || []).filter(function (r) { return isTermId(r.card_id); });
      if (!saved.length) {
        myTermsEl.innerHTML =
          '<div class="grm-my-empty"><span class="grm-my-empty-ic"><i class="ti ti-bookmark" aria-hidden="true"></i></span>' +
          '<p class="grm-my-empty-t">' + _t("아직 저장한 용어가 없어요") + '</p>' +
          '<p class="grm-my-empty-s">' + _t("용어사전에서 저장 버튼을 누르면 이곳에 모여요.") + '</p>' +
          '<a class="grm-my-cta" href="' + esc(cfgRoot) + 'glossary/index.html">' + _t("용어사전 둘러보기") + '</a></div>';
        return;
      }
      saved.sort(function (a, c) { return (c.created_at || "").localeCompare(a.created_at || ""); });
      var byId = {};
      if (idx && idx.terms) { idx.terms.forEach(function (e) { byId[e.id] = e; }); }
      var ul = document.createElement("ul"); ul.className = "grm-my-list";
      saved.forEach(function (sv) {
        var e = byId[termIdOf(sv.card_id)], li = document.createElement("li");
        li.className = "grm-my-item";
        if (e) {
          var a = document.createElement("a"); a.className = "grm-my-a"; a.href = e.href;
          a.textContent = e.term || _t("용어 보기");
          li.appendChild(a);
        } else {
          // 정본에서 사라진 용어 — 지어내지 않고 그 사실만 적는다(스크랩 목록과 동형).
          var sp = document.createElement("span"); sp.className = "grm-my-meta";
          sp.textContent = _t("저장된 용어 (찾지 못함)");
          li.appendChild(sp);
        }
        var rm = document.createElement("button"); rm.type = "button"; rm.className = "grm-my-rm"; rm.textContent = _t("저장 해제");
        rm.addEventListener("click", function () {
          rm.disabled = true;
          sb.from("reaction").delete().match({ user_id: session.user.id, card_id: sv.card_id, kind: "scrap" })
            .then(function (d) {
              if (d && d.error) { rm.disabled = false; return; }
              if (li.parentNode) li.parentNode.removeChild(li);
              if (!ul.children.length) renderMyTerms();
            }).catch(function () { rm.disabled = false; });
        });
        li.appendChild(rm); ul.appendChild(li);
      });
      myTermsEl.innerHTML = ""; myTermsEl.appendChild(ul);
    }).catch(function () {});
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
      myFirmsEl.innerHTML = '<p class="grm-my-note">' + _t("로그인하면 관심 업체를 모아볼 수 있어요.") + '</p>';
      return;
    }
    myFirmsEl.innerHTML = '<p class="grm-my-note">' + _t("불러오는 중…") + '</p>';
    sb.from("firm_watchlist").select("firm_key,firm_display,created_at")
      .then(function (res) {
        // 015 미적용(테이블 부재 → PostgREST 오류) 포함 — "준비 중" 노트로 조용히 폴백.
        if (res && res.error) {
          myFirmsEl.innerHTML = '<p class="grm-my-note">' + _t("관심 업체 목록 준비 중입니다.") + '</p>';
          return;
        }
        var firms = (res && res.data) ? res.data.slice() : [];
        if (!firms.length) {
          myFirmsEl.innerHTML =
            '<div class="grm-my-empty"><span class="grm-my-empty-ic"><i class="ti ti-building-factory-2" aria-hidden="true"></i></span>' +
            '<p class="grm-my-empty-t">' + _t("아직 등록한 업체가 없습니다") + '</p>' +
            '<p class="grm-my-empty-s">' + _t("업체 프로파일에서 관심 업체로 등록하세요.") + '</p>' +
            '<a class="grm-my-cta" href="' + esc(cfgRoot) + 'findings/index.html">' + _t("규제 지적사항 검색하기") + '</a></div>';
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
          meta.textContent = _t("등록 {date}", { date: String(fw.created_at || "").slice(0, 10) });
          li.appendChild(a); li.appendChild(meta);
          var rm = document.createElement("button"); rm.type = "button"; rm.className = "grm-my-rm"; rm.textContent = _t("해제");
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
        myFirmsEl.innerHTML = '<p class="grm-my-note">' + _t("불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") + '</p>';
      });
  }

  // ── [P2 관심 범위 · 067] /me 에서 축을 고르고 해제한다 ────────────────────
  // 015 워치리스트와 같은 계약: 로그인 상태에서만 동작하고, 067 미적용(테이블 부재 →
  // PostgREST 오류)·네트워크 실패는 오류처럼 보이지 않는 노트로 조용히 폴백한다.
  var INT_KIND_LABEL = { agency: _t("규제기관"), category: _t("지적 분류"), country: _t("제조소 국가") };

  function intVocab() {
    if (!intVocabEl) return [];
    return Array.prototype.map.call(intVocabEl.querySelectorAll("span"), function (el) {
      return { kind: el.getAttribute("data-kind"), value: el.getAttribute("data-value"),
               label: el.textContent || "" };
    });
  }

  function renderMyInterests() {
    if (!myIntEl) return;
    if (!session || !session.user) {
      myIntEl.innerHTML = '<p class="grm-my-note">' + _t("로그인하면 관심 범위를 저장할 수 있어요.") + '</p>';
      return;
    }
    var vocab = intVocab();
    if (!vocab.length) {
      myIntEl.innerHTML = '<p class="grm-my-note">' + _t("관심 범위 준비 중입니다.") + '</p>';
      return;
    }
    myIntEl.innerHTML = '<p class="grm-my-note">' + _t("불러오는 중…") + '</p>';
    sb.from("user_interest").select("kind,value")
      .then(function (res) {
        if (res && res.error) {
          myIntEl.innerHTML = '<p class="grm-my-note">' + _t("관심 범위 준비 중입니다.") + '</p>';
          return;
        }
        var on = {};
        (res && res.data ? res.data : []).forEach(function (r) { on[r.kind + "|" + r.value] = 1; });
        myIntEl.innerHTML = "";
        ["agency", "category", "country"].forEach(function (kind) {
          var items = vocab.filter(function (v) { return v.kind === kind; });
          if (!items.length) return;
          var grp = document.createElement("div"); grp.className = "grm-int-grp";
          var h = document.createElement("p"); h.className = "grm-int-h";
          h.textContent = INT_KIND_LABEL[kind] || kind;
          grp.appendChild(h);
          var wrap = document.createElement("div"); wrap.className = "grm-int-chips";
          items.forEach(function (v) {
            var b = document.createElement("button");
            b.type = "button"; b.className = "grm-int-chip";
            b.textContent = v.label;
            var key = v.kind + "|" + v.value;
            b.setAttribute("aria-pressed", on[key] ? "true" : "false");
            b.addEventListener("click", function () { toggleInterest(b, v, !!on[key], on); });
            wrap.appendChild(b);
          });
          grp.appendChild(wrap); myIntEl.appendChild(grp);
        });
      })
      .catch(function () {
        myIntEl.innerHTML = '<p class="grm-my-note">' + _t("불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") + '</p>';
      });
  }

  function toggleInterest(btn, v, isOn, onMap) {
    if (!session || !session.user) return;
    btn.disabled = true;
    var key = v.kind + "|" + v.value;
    var q = isOn
      ? sb.from("user_interest").delete().match({ user_id: session.user.id, kind: v.kind, value: v.value })
      : sb.from("user_interest").insert({ user_id: session.user.id, kind: v.kind, value: v.value });
    q.then(function (res) {
      btn.disabled = false;
      // 상한 초과(067 트리거)·중복 등 서버가 거절하면 상태를 바꾸지 않는다.
      if (res && res.error) return;
      if (isOn) { delete onMap[key]; } else { onMap[key] = 1; }
      btn.setAttribute("aria-pressed", isOn ? "false" : "true");
    }).catch(function () { btn.disabled = false; });
  }

  // ── [P2 개인화] 둘러보기 첫 화면 — 내 범위의 최근 공개 ────────────────────
  // 기본은 **없는 것처럼** 동작한다. 로그인 + 등록된 범위가 있고 실제로 걸린 문서가
  // 있을 때만 펼친다 — 게스트·미등록·067 미적용·RPC 실패는 전부 hidden 유지다.
  function renderMineRecent() {
    if (!mineEl || !mineListEl) return;
    if (!session || !session.user) { mineEl.hidden = true; return; }
    sb.rpc("findings_my_recent", { p_limit: 6 })
      .then(function (res) {
        if (!res || res.error || !res.data || !res.data.length) { mineEl.hidden = true; return; }
        mineListEl.innerHTML = "";
        res.data.forEach(function (d) {
          var a = document.createElement("a");
          a.className = "fnd-rc-row";
          a.href = cfgRoot + "findings/index.html?q=" + encodeURIComponent(d.firm_name || "");
          var head = document.createElement("span"); head.className = "fnd-rc-head";
          var dt = document.createElement("span"); dt.className = "fnd-rc-date";
          dt.textContent = String(d.published_date || "").slice(0, 10);
          var src = document.createElement("span"); src.className = "fnd-rc-src";
          src.textContent = d.source || d.agency || "";
          var fm = document.createElement("span"); fm.className = "fnd-rc-firm";
          fm.textContent = decodeFirmDisplay(d.firm_name || "");
          head.appendChild(dt); head.appendChild(src); head.appendChild(fm);
          var cats = (d.categories || []).filter(Boolean);
          if (cats.length) {
            var c = document.createElement("span"); c.className = "fnd-rc-cats";
            c.textContent = _t("지적 {n}건", { n: (d.obs_cnt || 0) });
            head.appendChild(c);
          }
          a.appendChild(head);
          mineListEl.appendChild(a);
        });
        if (mineSubEl) {
          mineSubEl.textContent =
            _t("마이페이지에서 고른 관심 범위와 관심 업체에 걸린 최근 공개입니다. ") +
            // 날짜의 정체는 소스마다 다르다 — 캐나다 실사는 원천이 공개일을 주지 않아
            // 실사일이 이 자리에 들어간다(문서를 열면 둘 다 나온다).
            _t("날짜는 문서가 공개된 날이고, 캐나다 실사만 실사일입니다.");
        }
        mineEl.hidden = false;
      })
      .catch(function () { mineEl.hidden = true; });
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
    renderAuth(); if (rows.length) loadMine(); renderMyTerms(); renderMyScraps(); renderMyFirms(); renderMyInterests(); renderMineRecent();
  }).catch(function () { renderAuth(); renderMyTerms(); renderMyScraps(); renderMyFirms(); renderMyInterests(); renderMineRecent(); });
  sb.auth.onAuthStateChange(function (_evt, s) {
    session = s; renderAuth(); if (rows.length) loadMine(); renderMyTerms(); renderMyScraps(); renderMyFirms(); renderMyInterests(); renderMineRecent();
    // 세션 성립 = 가입 흐름 종료. 로그인하려던 이유(누르려던 저장)를 여기서 갚는다.
    if (s && s.user) { clearSignupProgress(); if (!holdPop) closeLogin(); applyPendingReact(); if (_evt === "SIGNED_IN") setTimeout(function () { maybeNudge(s.user); }, 1200); }
  });
  if (rows.length) loadCounts();
})();
