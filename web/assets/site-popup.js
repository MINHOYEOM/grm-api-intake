/* 사이트 공지 띠 — 새 기능을 관련 화면에서만, 본문을 가리지 않는 한 줄 띠로 알린다.
   내용은 같은 폴더의 site-popup.json 한 곳에서만 온다. 공지를 바꾸거나 끄는 일은 그 파일만
   고치면 되고, 페이지 HTML(골든)은 흔들리지 않는다 — 이 스크립트 태그는 늘 그대로다.
   ★[띠로 전환 2026-11-12] 종전 모달은 홈·지적사항 어디로 들어와도 첫 화면을 막았다(모바일은
     화면 전체). 이제 `only_paths` 에 든 화면에서만, <main> 맨 위에 띠로 붙는다. 포커스를
     뺏지 않고 배경을 막지 않는다.
   ★닫기 = × 하나, 영구(localStorage). 기억은 공지 id 별이라 새 공지는 다시 뜬다.
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
      && typeof c.title === "string" && c.title
      && typeof c.cta_text === "string" && c.cta_text && sitePath(c.cta_path)
      && Array.isArray(c.only_paths) && c.only_paths.length > 0;
  }

  function dismissed(c) {
    var key = KEY_PREFIX + c.id;
    var v = get(window.localStorage, key);
    return v === "off" || get(window.sessionStorage, key) === "x";
  }

  function startsWithAny(path, list) {
    for (var i = 0; i < list.length; i++) {
      if (typeof list[i] === "string" && list[i] && path.indexOf(list[i]) === 0) return true;
    }
    return false;
  }

  // 띠 — 옅은 바탕 한 줄. 장식 없이 글자와 링크만. 색은 사이트 토큰.
  var CSS =
    ".grm-band{background:var(--coral-tint,#F4E7DF);border-bottom:1px solid rgba(194,96,63,.18);font-family:var(--sans)}" +
    ".grm-band-in{max-width:var(--maxw,1180px);margin:0 auto;padding:10px 22px;display:flex;align-items:center;gap:6px 14px;flex-wrap:wrap;font-size:14px;line-height:1.5;color:var(--body,#3D3A35);word-break:keep-all}" +
    ".grm-band-k{font-weight:700;color:var(--coral-2,#A14B30)}" +
    ".grm-band-t{font-weight:600;color:var(--ink,#1A1815)}" +
    ".grm-band-cta{font-weight:600;color:var(--coral-2,#A14B30);text-decoration:underline;text-underline-offset:3px;white-space:nowrap}" +
    ".grm-band-x{margin-left:auto;flex:none;width:32px;height:32px;border:0;border-radius:50%;background:transparent;font-size:20px;line-height:1;color:var(--muted,#8A867E);cursor:pointer}" +
    ".grm-band-x:hover{color:var(--ink,#1A1815);background:rgba(250,249,245,.7)}" +
    "@media (max-width:640px){.grm-band-in{font-size:13.5px;padding:8px 16px}}";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function show(c) {
    var main = document.querySelector("main");
    if (!main) return;
    var key = KEY_PREFIX + c.id;
    var style = el("style"); style.textContent = CSS; document.head.appendChild(style);

    var band = el("aside", "grm-band");
    band.setAttribute("aria-label", "공지");
    var inner = el("div", "grm-band-in");
    if (c.kicker) inner.appendChild(el("span", "grm-band-k", c.kicker));
    inner.appendChild(el("span", "grm-band-t", c.title.replace(/\s*\n\s*/g, " ")));
    var cta = el("a", "grm-band-cta", c.cta_text + " →");
    cta.href = c.cta_path;
    inner.appendChild(cta);
    var x = el("button", "grm-band-x", "×");
    x.type = "button"; x.setAttribute("aria-label", "닫기");
    inner.appendChild(x);
    band.appendChild(inner);
    main.insertBefore(band, main.firstChild);

    x.addEventListener("click", function () {
      set(window.localStorage, key, "off");
      band.remove();
    });
    // 눌러서 들어간 사람에게 다음 페이지에서 또 띄우지 않는다.
    cta.addEventListener("click", function () { set(window.sessionStorage, key, "x"); });
  }

  function run(c) {
    if (!valid(c)) return;
    if ((c.lang || "ko") !== (document.documentElement.lang || "")) return;
    if (todayKST() > c.until) return;
    var path = location.pathname;
    if (!startsWithAny(path, c.only_paths)) return;
    if (startsWithAny(path, Array.isArray(c.skip_paths) ? c.skip_paths : [])) return;
    if (dismissed(c)) return;
    show(c);
  }

  if (typeof fetch !== "function") return;
  // no-cache = 매번 재검증(ETag) — 공지를 바꾸거나 끈 배포가 곧바로 반영된다.
  fetch("/assets/site-popup.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (c) { if (c && c.id) run(c); })
    .catch(function () { /* 공지가 없거나 깨졌으면 아무것도 띄우지 않는다 */ });
})();
