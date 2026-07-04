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

  // ── 로그인 유도(이메일 → 인증 코드) ─────────────────────────────────────
  // 매직링크 대신 코드 입력: 메일 스캐너/클릭추적(Brevo sendibt3)이 링크를 선방문해 1회용
  // 토큰을 소모하는 문제를 우회한다(클릭할 링크 자체가 없음). Supabase 이메일 템플릿은
  // {{ .Token }}(인증 코드)만 담아야 하며 {{ .ConfirmationURL }} 링크는 제거해야 한다.
  // 코드 길이는 Supabase 설정을 따른다(6~8자리 등) — UI/verifyOtp 는 길이 비의존.
  var pop, pendingEmail = "";
  function openLogin(msg) {
    if (!pop) {
      pop = document.createElement("div");
      pop.className = "grm-login-pop";
      pop.innerHTML =
        '<div class="grm-login-card" role="dialog" aria-modal="true" aria-label="로그인">' +
        '<button type="button" class="grm-login-x" aria-label="닫기">×</button>' +
        '<h3>관심 카드를 모으려면 로그인하세요</h3>' +
        '<p>이메일을 입력하면 로그인 인증 코드를 보내드립니다. 비밀번호는 없습니다.</p>' +
        '<form class="grm-login-form grm-login-email"><input type="email" required autocomplete="email" ' +
        'placeholder="you@company.com" aria-label="이메일" />' +
        '<button type="submit">코드 받기</button></form>' +
        '<form class="grm-login-form grm-login-code" style="display:none"><input type="text" inputmode="numeric" ' +
        'pattern="[0-9]*" autocomplete="one-time-code" placeholder="메일로 받은 인증 코드" aria-label="로그인 코드" />' +
        '<button type="submit">확인</button></form>' +
        '<p class="grm-login-msg" role="status" aria-live="polite"></p></div>';
      document.body.appendChild(pop);
      var emailForm = pop.querySelector(".grm-login-email");
      var codeForm = pop.querySelector(".grm-login-code");
      var m = pop.querySelector(".grm-login-msg");
      pop.querySelector(".grm-login-x").addEventListener("click", closeLogin);
      pop.addEventListener("click", function (e) { if (e.target === pop) closeLogin(); });
      emailForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var email = (emailForm.querySelector("input").value || "").trim();
        if (!email) return;
        m.textContent = "전송 중…";
        sb.auth.signInWithOtp({ email: email }).then(function (res) {
          if (res && res.error) { m.textContent = "전송 실패: " + res.error.message; return; }
          pendingEmail = email;
          emailForm.style.display = "none"; codeForm.style.display = "flex";
          m.textContent = "메일로 받은 인증 코드를 입력하세요. (안 보이면 스팸함도 확인)";
          var ci = codeForm.querySelector("input"); if (ci) setTimeout(function () { ci.focus(); }, 30);
        }).catch(function () { m.textContent = "전송에 실패했습니다. 잠시 후 다시 시도해 주세요."; });
      });
      codeForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var token = (codeForm.querySelector("input").value || "").trim();
        if (!token || !pendingEmail) return;
        m.textContent = "확인 중…";
        sb.auth.verifyOtp({ email: pendingEmail, token: token, type: "email" }).then(function (res) {
          m.textContent = (res && res.error)
            ? "코드가 올바르지 않거나 만료됐습니다. 다시 시도해 주세요."
            : "로그인되었습니다.";
          // 성공 시 onAuthStateChange 가 UI 갱신·closeLogin 처리.
        }).catch(function () { m.textContent = "확인에 실패했습니다. 다시 시도해 주세요."; });
      });
    }
    // 매 호출마다 이메일 단계로 초기화 — 로그아웃 후 재로그인 시 옛 코드/단계 잔존 방지.
    var ef = pop.querySelector(".grm-login-email");
    var cf = pop.querySelector(".grm-login-code");
    if (ef) ef.style.display = "flex";
    if (cf) { cf.style.display = "none"; var cin = cf.querySelector("input"); if (cin) cin.value = ""; }
    pendingEmail = "";
    var msgEl = pop.querySelector(".grm-login-msg");
    if (msgEl) msgEl.textContent = msg || "";
    pop.classList.add("show");
    var i = pop.querySelector(".grm-login-email input"); if (i) setTimeout(function () { i.focus(); }, 30);
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
