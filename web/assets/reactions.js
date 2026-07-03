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
  if (!rows.length) return;
  var ids = rows.map(function (r) { return r.getAttribute("data-anchor"); }).filter(Boolean);
  var session = null;

  function reactBtns(row) {
    return Array.prototype.slice.call(row.querySelectorAll(".grm-ca-react[data-react]"));
  }

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
      var who = document.createElement("span");
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

  // ── 매직링크 로그인 유도 ──────────────────────────────────────────────────
  var pop;
  function openLogin(msg) {
    if (!pop) {
      pop = document.createElement("div");
      pop.className = "grm-login-pop";
      pop.innerHTML =
        '<div class="grm-login-card" role="dialog" aria-modal="true" aria-label="로그인">' +
        '<button type="button" class="grm-login-x" aria-label="닫기">×</button>' +
        '<h3>관심 카드를 모으려면 로그인하세요</h3>' +
        '<p>이메일을 입력하면 로그인 링크를 보내드립니다. 비밀번호는 없습니다.</p>' +
        '<form class="grm-login-form"><input type="email" required autocomplete="email" ' +
        'placeholder="you@company.com" aria-label="이메일" />' +
        '<button type="submit">로그인 링크 받기</button></form>' +
        '<p class="grm-login-msg" role="status" aria-live="polite"></p></div>';
      document.body.appendChild(pop);
      pop.querySelector(".grm-login-x").addEventListener("click", closeLogin);
      pop.addEventListener("click", function (e) { if (e.target === pop) closeLogin(); });
      pop.querySelector(".grm-login-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var input = pop.querySelector("input");
        var m = pop.querySelector(".grm-login-msg");
        var email = (input.value || "").trim();
        if (!email) return;
        m.textContent = "전송 중…";
        sb.auth.signInWithOtp({ email: email, options: { emailRedirectTo: location.href } })
          .then(function (res) {
            m.textContent = (res && res.error)
              ? ("전송 실패: " + res.error.message)
              : "확인 메일을 보냈습니다. 메일의 로그인 링크를 눌러 주세요.";
          })
          .catch(function () { m.textContent = "전송에 실패했습니다. 잠시 후 다시 시도해 주세요."; });
      });
    }
    var msgEl = pop.querySelector(".grm-login-msg");
    if (msgEl) msgEl.textContent = msg || "";
    pop.classList.add("show");
    var i = pop.querySelector("input"); if (i) setTimeout(function () { i.focus(); }, 30);
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

  // ── 배선 ─────────────────────────────────────────────────────────────────
  document.body.classList.add("grm-reactions-on");
  rows.forEach(function (row) {
    reactBtns(row).forEach(function (b) {
      b.addEventListener("click", function () { toggle(b, row); });
    });
  });
  sb.auth.getSession().then(function (res) {
    session = (res && res.data) ? res.data.session : null;
    renderAuth(); loadMine();
  }).catch(function () { renderAuth(); });
  sb.auth.onAuthStateChange(function (_evt, s) {
    session = s; renderAuth(); loadMine();
    if (s && s.user) closeLogin();
  });
  loadCounts();
})();
