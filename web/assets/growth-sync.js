/* [구름이 서버 동기화 11차] growth.js localStorage v1 데이터의 로그인 사용자 보관 레이어.
 * 하이브리드 계약(불가침): 게스트(localStorage)는 이 파일 없이도 완전 동작한다 — 이 파일은
 * base.html reactions_enabled 게이트 안에서만 로드되고, 비로그인·supabase-js 부재·
 * 032(gurumi_growth) 미적용·네트워크 실패는 전부 조용한 로컬 폴백(reactions.js 관례 동형).
 * 로그인 강제 0 — 게스트 경험을 깎지 않는 순수 부가 레이어다.
 *
 * 세션 인프라: reactions.js 와 같은 storageKey "grm-public-auth-v1" 로 클라이언트를 재생성해
 * localStorage 세션을 공유한다(firm.js 워치리스트 관례 — 신규 인증 코드·secret 0,
 * Multiple GoTrueClient 콘솔 경고는 무해). DB 호출은 sb.from("gurumi_growth") 뿐 —
 * supabase-js 가 Authorization: Bearer <사용자 토큰>을 자동 첨부하고 RLS 가 본인 행만
 * 허용한다(032 계약). 서버엔 사실만 보낸다(version·weeks) — 점수·단계·이름은 항상
 * 클라이언트 재계산(growth.js 원칙, 서버 병합도 v1 스키마 그대로).
 *
 * 병합 규칙(결정론 — 단일 정본): week×문항 union — 두 저장본의 주(week key)와 문항(qid)을
 * 모두 보존한다(데이터 유실 0). 동일 (week,qid) 충돌은 Math.max(local, server) = 정답(1)
 * 우선. 근거: ① max 는 교환·결합·멱등이라 병합 순서·기기 수와 무관하게 같은 결과로
 * 수렴한다(로컬 우선/서버 우선은 비대칭이라 기기 간 영원히 불일치할 수 있다) ② "한 번
 * 맞힌 사실"은 어느 기기에서 맞혔든 성취로 보존한다(적립 dedup 의 주차 스냅샷 철학과
 * 정합 — 성취를 지우는 병합은 없다). week.idx 충돌은 Math.min(같은 산식 산출이라
 * 정상적으론 항상 동일 — 손상 데이터 대비 결정론 규칙일 뿐).
 *
 * push 는 항상 pull→병합→upsert(수렴 업서트): 동시 로그인 기기·growth.js 메모리 사본과의
 * 경합에서도 마지막 쓰기가 사실을 지우지 않는다(병합이 멱등 union 이라 반복 적용 안전). */
(function () {
  "use strict";
  if (!window.localStorage) return;
  var cfg = document.getElementById("grm-reactions-cfg");
  var lib = window.supabase;
  if (!cfg || !lib || !lib.createClient) return;
  var SUPA_URL = cfg.getAttribute("data-url") || "";
  var SUPA_KEY = cfg.getAttribute("data-key") || "";
  if (!SUPA_URL || !SUPA_KEY) return;

  var KEY = "grm-gurumi-growth";
  var SCHEMA_VERSION = 1;
  var TABLE = "gurumi_growth";
  var PUSH_DEBOUNCE_MS = 1500;

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

  var session = null;
  var pushTimer = null;
  var syncing = false;
  var queued = false;
  var selfNotify = false;

  // ── 로컬 저장(growth.js v1 스키마 그대로 — {version, weeks:{key:{idx,q:{qid:0|1}}}}) ──
  function loadLocal() {
    try {
      var d = JSON.parse(localStorage.getItem(KEY));
      if (d && d.version === SCHEMA_VERSION && d.weeks && typeof d.weeks === "object") return d;
    } catch (e) { /* 손상 → 빈 저장본 */ }
    return { version: SCHEMA_VERSION, weeks: {} };
  }
  function saveLocal(weeks) {
    try { localStorage.setItem(KEY, JSON.stringify({ version: SCHEMA_VERSION, weeks: weeks })); } catch (e) {}
  }

  // weeks 모양 검증·정규화 — 서버 값도 결국 클라이언트 산출물이므로 신뢰하지 않고
  // 모양(주차 키 6자리·idx 유한수·q 값 0/1)만 통과시킨다(그 외 키는 조용히 버림).
  function sanitizeWeeks(weeks) {
    var out = {};
    if (!weeks || typeof weeks !== "object") return out;
    for (var k in weeks) {
      if (!Object.prototype.hasOwnProperty.call(weeks, k)) continue;
      if (!/^\d{6}$/.test(k)) continue;
      var w = weeks[k];
      if (!w || typeof w !== "object" || typeof w.idx !== "number" || !isFinite(w.idx)) continue;
      var q = {}, src = (w.q && typeof w.q === "object") ? w.q : {};
      for (var id in src) {
        if (!Object.prototype.hasOwnProperty.call(src, id)) continue;
        q[id] = src[id] ? 1 : 0;
      }
      out[k] = { idx: Math.floor(w.idx), q: q };
    }
    return out;
  }

  // ── 병합(순수 함수·결정론 — 규칙은 파일 머리 주석이 정본) ──
  function mergeWeeks(a, b) {
    var out = {};
    function fold(src) {
      for (var k in src) {
        if (!Object.prototype.hasOwnProperty.call(src, k)) continue;
        var w = src[k];
        if (!out[k]) out[k] = { idx: w.idx, q: {} };
        else out[k].idx = Math.min(out[k].idx, w.idx);
        for (var id in w.q) {
          if (!Object.prototype.hasOwnProperty.call(w.q, id)) continue;
          var v = w.q[id] ? 1 : 0;
          out[k].q[id] = Object.prototype.hasOwnProperty.call(out[k].q, id)
            ? Math.max(out[k].q[id], v) : v;
        }
      }
    }
    fold(a);
    fold(b);
    return out;
  }

  // 결정론 직렬화(키 정렬) — 병합 결과의 변경 유무 판정용(불필요한 쓰기·업서트 생략).
  function stableStr(weeks) {
    var keys = Object.keys(weeks).sort();
    return JSON.stringify(keys.map(function (k) {
      var q = weeks[k].q, qk = Object.keys(q).sort();
      return [k, weeks[k].idx, qk.map(function (id) { return [id, q[id]]; })];
    }));
  }

  // ── 동기화(pull → 병합 → 로컬/서버 양쪽 반영) — 실패는 전부 조용한 로컬 폴백 ──
  function sync() {
    if (!session || !session.user) return;
    if (syncing) { queued = true; return; }
    syncing = true;
    function done() {
      syncing = false;
      if (queued) { queued = false; sync(); }
    }
    sb.from(TABLE).select("version,weeks").maybeSingle().then(function (res) {
      // 032 미적용(테이블 부재 → PostgREST 오류)·권한 오류 → 조용한 로컬 폴백.
      if (!res || res.error) { syncing = false; queued = false; return; }
      var localWeeks = sanitizeWeeks(loadLocal().weeks);
      var serverWeeks = (res.data && res.data.version === SCHEMA_VERSION)
        ? sanitizeWeeks(res.data.weeks) : {};
      var merged = mergeWeeks(localWeeks, serverWeeks);
      var mergedStr = stableStr(merged);
      if (mergedStr !== stableStr(localWeeks)) {
        saveLocal(merged);
        // growth.js(메모리 사본 재적재)·pet.js(재파생 렌더)에 병합 반영 통지.
        // selfNotify: 자기 통지로 push 를 재스케줄하지 않는다(아래에서 즉시 업서트).
        selfNotify = true;
        try {
          window.dispatchEvent(new CustomEvent("grm:gurumi-sync"));
          window.dispatchEvent(new CustomEvent("grm:gurumi-change"));
        } catch (e) {}
        selfNotify = false;
      }
      if (res.data && mergedStr === stableStr(serverWeeks)) { done(); return; }
      // 서버 행도 없고 병합본도 비었으면 빈 행을 만들지 않는다(무의미한 쓰기 0).
      if (!res.data && !Object.keys(merged).length) { done(); return; }
      sb.from(TABLE).upsert(
        { user_id: session.user.id, version: SCHEMA_VERSION, weeks: merged },
        { onConflict: "user_id" }
      ).then(function () { done(); }).catch(function () { done(); });
    }).catch(function () { syncing = false; queued = false; });
  }

  // 적립 발생 시 서버 push(디바운스) — 비로그인이면 전송 0(하이브리드 계약).
  function schedulePush() {
    if (!session || !session.user) return;
    if (pushTimer) clearTimeout(pushTimer);
    pushTimer = setTimeout(function () { pushTimer = null; sync(); }, PUSH_DEBOUNCE_MS);
  }
  window.addEventListener("grm:gurumi-change", function () {
    if (!selfNotify) schedulePush();
  });

  // ── 펫 패널 보관 상태/CTA(런타임 주입 — 정적 HTML·골든 무변형) ──────────────
  // 강요 톤 금지: 게스트 저장 안내(.grm-pet-local)는 그대로 두고, 로그인 시에만
  // "계정 보관" 상태로 교대한다. CTA 는 기존 로그인 플로우(reactions.js)를 재사용한다 —
  // 별도 로그인 UI 발명 0. 12차부터는 window.GRM_AUTH.open({mode:"signup"}) 로 가입 폼에
  // 직행하고(계정이 없는 게스트가 이 CTA 를 누르므로), 폴백은 기존 헤더 클릭 위임(firm.js 관례).
  function renderSlot() {
    var panel = document.getElementById("grm-pet-panel");
    if (!panel) return;
    var localNote = panel.querySelector(".grm-pet-local");
    var slot = document.getElementById("grm-pet-sync");
    if (!slot) {
      slot = document.createElement("div");
      slot.id = "grm-pet-sync";
      slot.className = "grm-pet-sync";
      if (localNote && localNote.parentNode) localNote.parentNode.insertBefore(slot, localNote.nextSibling);
      else panel.appendChild(slot);
    }
    var loggedIn = !!(session && session.user);
    if (localNote) localNote.hidden = loggedIn;
    slot.innerHTML = "";
    if (loggedIn) {
      var on = document.createElement("p");
      on.className = "grm-pet-sync-on";
      on.innerHTML = '<i class="ti ti-cloud-check" aria-hidden="true"></i>구름이가 계정에 안전하게 보관되고 있어요';
      var sub = document.createElement("p");
      sub.className = "grm-pet-sync-s";
      sub.textContent = "어느 기기에서든 이어서 키울 수 있어요";
      slot.appendChild(on);
      slot.appendChild(sub);
    } else {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "grm-pet-sync-cta";
      btn.innerHTML = '<i class="ti ti-cloud-up" aria-hidden="true"></i>구름이 안전하게 보관하기';
      btn.addEventListener("click", function () {
        // 가입 의도가 분명한 진입점 → 가입 폼 직행(로그인 화면 경유 1클릭 제거).
        // GRM_AUTH 부재(reactions.js 미로드)면 기존 헤더 위임으로 폴백.
        if (window.GRM_AUTH && typeof window.GRM_AUTH.open === "function") {
          window.GRM_AUTH.open({ mode: "signup" });
          return;
        }
        var headerLogin = document.querySelector(".grm-auth .grm-acct-login");
        if (headerLogin) headerLogin.click();
      });
      var hint = document.createElement("p");
      hint.className = "grm-pet-sync-s";
      hint.textContent = "로그인하면 어느 기기에서든 이어서 키울 수 있어요";
      slot.appendChild(btn);
      slot.appendChild(hint);
    }
  }

  // ── 배선 — 세션 취득/전이는 reactions.js 와 동형(getSession + onAuthStateChange) ──
  sb.auth.getSession().then(function (res) {
    session = (res && res.data) ? res.data.session : null;
    renderSlot();
    if (session && session.user) sync();
  }).catch(function () { renderSlot(); });
  sb.auth.onAuthStateChange(function (_evt, s) {
    var wasIn = !!(session && session.user);
    session = s;
    renderSlot();
    if (s && s.user && !wasIn) sync();   // 로그인 전이 시 서버 pull → 병합 → 양쪽 반영
  });
})();
