/* 사이트 공지 팝업 — 새 기능을 처음 들어온 방문자에게 한 번 알린다.
   내용은 같은 폴더의 site-popup.json 한 곳에서만 온다. 공지를 바꾸거나 끄는 일은 그 파일만
   고치면 되고, 페이지 HTML(골든)은 흔들리지 않는다 — 이 스크립트 태그는 늘 그대로다.
   ★닫기 세 갈래: × ·바깥·Esc = 이번 방문(sessionStorage) / 오늘 하루 = 오늘 날짜(KST) /
     다시 보지 않기 = 영구. 기억은 공지 id 별이라 새 공지는 다시 뜬다.
   ★storage 는 사생활 모드에서 접근 자체가 던진다 — 실패하면 기억을 못 할 뿐 화면은 정상.
   ★`until` 은 이 날(KST)까지 띄운다. 빌드 시각이 아니라 방문 시각으로 판정해 결정론을 지킨다. */
(function () {
  "use strict";
  var KEY_PREFIX = "grm-popup:";
  var ID_RE = /^[a-z0-9][a-z0-9-]{2,63}$/;
  var DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

  function todayKST() {
    return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  }
  function get(store, key) { try { return store.getItem(key); } catch (e) { return null; } }
  function set(store, key, val) { try { store.setItem(key, val); } catch (e) { /* 기억만 못 한다 */ } }
  // 우리 사이트 안 경로만(`//host` 같은 프로토콜-상대 주소는 외부로 나간다).
  function sitePath(p) { return typeof p === "string" && p.charAt(0) === "/" && p.charAt(1) !== "/"; }

  function valid(c) {
    return c && ID_RE.test(c.id || "") && DATE_RE.test(c.until || "")
      && typeof c.title === "string" && c.title && typeof c.body === "string"
      && typeof c.cta_text === "string" && c.cta_text && sitePath(c.cta_path);
  }

  function dismissed(c) {
    var key = KEY_PREFIX + c.id;
    var v = get(window.localStorage, key);
    if (v === "off" || v === "day:" + todayKST()) return true;
    return get(window.sessionStorage, key) === "x";
  }

  // 포스터형 — 밝은 두 단(위: 옅은 주황 그라데이션에 큰 제목 / 아래: 설명·버튼). 장식은 글자로만
  // 한다(옅은 워터마크 글자·형광펜 밑줄). 도형·그림·이모지는 쓰지 않는다. 색은 사이트 토큰.
  var CSS =
    ".grm-popup{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(20,18,15,.5);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}" +
    ".grm-popup[hidden]{display:none}" +
    ".grm-popup-box{position:relative;width:100%;max-width:440px;border-radius:20px;overflow:hidden;background:var(--canvas,#FAF9F5);box-shadow:0 30px 70px -10px rgba(0,0,0,.35),0 0 0 1px rgba(194,96,63,.12);font-family:var(--sans);animation:grmPopIn .32s var(--ease,ease-out)}" +
    "@keyframes grmPopIn{from{opacity:0;transform:translateY(14px) scale(.97)}to{opacity:1;transform:none}}" +
    "@media (prefers-reduced-motion:reduce){.grm-popup-box{animation:none}}" +
    ".grm-popup-hero{position:relative;overflow:hidden;padding:40px 32px 30px;background:linear-gradient(155deg,var(--coral-tint,#F4E7DF) 0%,#F8EEE7 45%,var(--canvas,#FAF9F5) 100%);border-bottom:1px solid rgba(194,96,63,.14)}" +
    ".grm-popup-mark{position:absolute;right:-10px;bottom:-34px;font-family:var(--serif);font-weight:800;font-size:132px;line-height:1;color:rgba(194,96,63,.09);pointer-events:none;user-select:none;white-space:nowrap}" +
    ".grm-popup-kicker{position:relative;display:inline-block;margin:0 0 18px;padding:6px 13px;border-radius:999px;background:var(--canvas,#FAF9F5);border:1px solid rgba(194,96,63,.28);font-size:13.5px;font-weight:700;color:var(--coral,#C2603F);word-break:keep-all}" +
    ".grm-popup h2{position:relative;margin:0;font-family:var(--serif);font-weight:700;font-size:44px;line-height:1.18;color:var(--ink,#1A1815);white-space:pre-line;word-break:keep-all}" +
    ".grm-popup h2 em{font-style:normal;color:var(--coral-2,#A14B30);background:linear-gradient(transparent 64%,rgba(194,96,63,.22) 64%,rgba(194,96,63,.22) 92%,transparent 92%)}" +
    ".grm-popup-body{padding:24px 32px 26px}" +
    ".grm-popup p{margin:0 0 22px;font-size:16px;line-height:1.7;color:var(--body,#3D3A35);word-break:keep-all}" +
    ".grm-popup-cta{display:flex;align-items:center;justify-content:center;gap:10px;background:var(--coral,#C2603F);color:var(--on-coral,#FAF9F5);text-decoration:none;font-weight:700;font-size:17px;border-radius:12px;padding:16px;box-shadow:0 12px 24px -12px rgba(161,75,48,.75);transition:transform .15s ease,box-shadow .15s ease}" +
    ".grm-popup-cta:hover{transform:translateY(-1px);box-shadow:0 16px 28px -12px rgba(161,75,48,.85)}" +
    ".grm-popup-note{margin:14px 0 0;text-align:center;font-size:12.5px;color:var(--muted,#8A867E)}" +
    ".grm-popup-x{position:absolute;top:10px;right:10px;z-index:2;width:36px;height:36px;border-radius:50%;background:rgba(250,249,245,.7);border:0;font-size:22px;line-height:1;color:var(--muted,#8A867E);cursor:pointer}" +
    ".grm-popup-x:hover{color:var(--ink,#1A1815);background:var(--canvas,#FAF9F5)}" +
    ".grm-popup-foot{display:flex;border-top:1px solid var(--line-2,rgba(26,24,21,.09));background:var(--soft,#F5F0E8)}" +
    ".grm-popup-foot button{flex:1;background:none;border:0;padding:14px 8px;font-family:var(--sans);font-size:13.5px;color:var(--muted,#8A867E);cursor:pointer}" +
    ".grm-popup-foot button+button{border-left:1px solid var(--line-2,rgba(26,24,21,.09))}" +
    ".grm-popup-foot button:hover{color:var(--ink,#1A1815)}" +
    "@media (max-width:420px){.grm-popup-hero{padding:36px 24px 26px}.grm-popup-body{padding:22px 24px 24px}.grm-popup h2{font-size:36px}.grm-popup-mark{font-size:108px;bottom:-28px}}";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function show(c) {
    var key = KEY_PREFIX + c.id;
    var style = el("style"); style.textContent = CSS; document.head.appendChild(style);

    var wrap = el("div", "grm-popup");
    wrap.hidden = true;
    var box = el("div", "grm-popup-box");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "grm-popup-title");
    var hero = el("div", "grm-popup-hero");
    if (c.watermark) { var mk = el("span", "grm-popup-mark", c.watermark); mk.setAttribute("aria-hidden", "true"); hero.appendChild(mk); }
    if (c.kicker) hero.appendChild(el("div", "grm-popup-kicker", c.kicker));
    // 제목 안의 accent 부분만 형광펜. 줄바꿈은 제목 문자열의 \n 그대로(pre-line).
    var h = el("h2"); h.id = "grm-popup-title";
    var at = c.accent ? c.title.indexOf(c.accent) : -1;
    if (at >= 0) {
      h.appendChild(document.createTextNode(c.title.slice(0, at)));
      h.appendChild(el("em", "", c.accent));
      h.appendChild(document.createTextNode(c.title.slice(at + c.accent.length)));
    } else { h.textContent = c.title; }
    hero.appendChild(h);
    var inner = el("div", "grm-popup-body");
    if (c.body) inner.appendChild(el("p", "", c.body));
    var cta = el("a", "grm-popup-cta", c.cta_text + " →");
    cta.href = c.cta_path;
    inner.appendChild(cta);
    if (c.note) inner.appendChild(el("div", "grm-popup-note", c.note));
    var x = el("button", "grm-popup-x", "×");
    x.type = "button"; x.setAttribute("aria-label", "닫기");
    var foot = el("div", "grm-popup-foot");
    var day = el("button", "", "오늘 하루 보지 않기"); day.type = "button";
    var off = el("button", "", "다시 보지 않기"); off.type = "button";
    foot.appendChild(day); foot.appendChild(off);
    box.appendChild(x); box.appendChild(hero); box.appendChild(inner); box.appendChild(foot);
    wrap.appendChild(box);
    document.body.appendChild(wrap);

    var prevFocus = document.activeElement;
    function close(mark) {
      if (mark === "x") set(window.sessionStorage, key, "x");
      else set(window.localStorage, key, mark);
      wrap.hidden = true;
      document.removeEventListener("keydown", onKey);
      if (prevFocus && prevFocus.focus) try { prevFocus.focus(); } catch (e) { /* 무시 */ }
    }
    function onKey(e) { if (e.key === "Escape") close("x"); }
    x.addEventListener("click", function () { close("x"); });
    day.addEventListener("click", function () { close("day:" + todayKST()); });
    off.addEventListener("click", function () { close("off"); });
    // 눌러서 들어간 사람에게 다음 페이지에서 또 띄우지 않는다.
    cta.addEventListener("click", function () { set(window.sessionStorage, key, "x"); });
    wrap.addEventListener("click", function (e) { if (e.target === wrap) close("x"); });
    document.addEventListener("keydown", onKey);

    wrap.hidden = false;
    try { cta.focus({ preventScroll: true }); } catch (e) { /* 무시 */ }
  }

  function run(c) {
    if (!valid(c)) return;
    if ((c.lang || "ko") !== (document.documentElement.lang || "")) return;
    if (todayKST() > c.until) return;
    var path = location.pathname;
    var skip = Array.isArray(c.skip_paths) ? c.skip_paths : [];
    for (var i = 0; i < skip.length; i++) {
      if (typeof skip[i] === "string" && skip[i] && path.indexOf(skip[i]) === 0) return;
    }
    if (dismissed(c)) return;
    // 첫 화면을 먼저 읽게 한 박자 늦게 띄운다.
    setTimeout(function () { show(c); }, 800);
  }

  if (typeof fetch !== "function") return;
  // no-cache = 매번 재검증(ETag) — 공지를 바꾸거나 끈 배포가 곧바로 반영된다.
  fetch("/assets/site-popup.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (c) { if (c && c.id) run(c); })
    .catch(function () { /* 공지가 없거나 깨졌으면 아무것도 띄우지 않는다 */ });
})();
