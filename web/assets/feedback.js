/* GRM 문의 및 제안 — 전 페이지 공통 런타임 위젯.
   비골든: 푸터 '서비스' 열(`[data-feedback-slot]`)의 진입 링크와 모달, 소개 페이지 연락
   블록(`[data-feedback-mount]`)의 진입 버튼을 전부 런타임에 주입한다 — JS 미실행이면
   흔적 0(죽은 링크를 남기지 않는 share 버튼 선례). 스타일은 base.html 게이트 안 스코프
   <style>(.grm-fb-*) — grm.css 프리즈 보존(reactions 관례). 소개 버튼 스타일은 about.html.

   쓰기 경로(불가침): 061 RPC(feedback_submit)만 — anon 은 user_feedback 테이블에 직접
   insert 할 수 없다(060 funnel_bump 관례 동형). 호출은 popular.js 와 같은 raw PostgREST
   fetch(supabase-js 비의존 — 로드 순서·로그인 여부와 무관하게 동작해야 하는 익명 폼).
   RPC 는 접수번호(bigint)를 돌려준다 — 사용자가 자기 접수를 지칭할 수 있게 화면에 보인다.

   개인정보: 이메일은 **회신 동의 체크가 있을 때만** 전송한다(개인정보보호법). 동의 없이
   들어온 이메일은 RPC 가 폐기한다(폼이 막아도 DB 가 최종 방어선). 함께 보내는 진단 정보
   (현재 페이지 주소·브라우저·화면 크기)는 무엇이 가는지 화면에 명시한다 — 몰래 걷지 않는다.

   운영자 표식: 운영자 브라우저(localStorage 'grm-op')와 비프로덕션 호스트(프리뷰·localhost)
   제출은 p_operator=true 로 표식만 한다 — 제출 자체는 막지 않는다(프리뷰에서도 폼이 실제로
   동작해야 검증이 된다). #763 운영자 제외와 같은 이유(판독 잡음 분리)·storage 예외는
   비운영자로 fail-open(확실한 운영자만 표식).

   허용 category 는 061 CHECK 와 같은 4종 — 여기 목록을 바꾸면 마이그레이션도 같이 바꿔야
   한다(모르는 값은 RPC 가 거부한다·폴백 금지). */
(function () {
  "use strict";
  var _t = function (s, v) {
    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;
    return v ? r.replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : r;
  };
  var cfg = document.getElementById("grm-reactions-cfg");
  if (!cfg || !window.fetch) return;
  var SUPA_URL = (cfg.getAttribute("data-url") || "").replace(/\/+$/, "");
  var SUPA_KEY = cfg.getAttribute("data-key") || "";
  if (!SUPA_URL || !SUPA_KEY) return;
  var SITE_HOST = cfg.getAttribute("data-host") || "";

  var MAX = 2000;
  var DRAFT_KEY = "grm-fb-draft-v1";
  // [id, 라벨, 그 유형을 골랐을 때 보여줄 작성 예시] — id 는 061 CHECK 와 같은 4종.
  var CATEGORIES = [
    ["usability", _t("이용이 불편한 점"),
     _t("예) 모바일에서 표가 화면 밖으로 잘려 가로로 스크롤해야 읽을 수 있습니다.")],
    ["correction", _t("내용 오류·수정 요청"),
     _t("예) 8월 24일자 식약처 카드의 처분 기간이 원문(2026.7.1~7.31)과 다릅니다.")],
    ["feature", _t("기능 제안"),
     _t("예) 관심 업체의 지적사항을 엑셀로 내려받을 수 있으면 좋겠습니다.")],
    ["other", _t("그 밖의 의견"),
     _t("예) 서비스 전반에 대한 의견을 자유롭게 남겨 주세요.")]
  ];

  function isOperator() {
    try { if (localStorage.getItem("grm-op") === "1") return true; } catch (e) { /* fail-open */ }
    if (SITE_HOST && location.hostname !== SITE_HOST && location.hostname !== "www." + SITE_HOST) return true;
    return false;
  }
  function viewportLabel() {
    try { return (window.innerWidth || 0) + "x" + (window.innerHeight || 0); } catch (e) { return ""; }
  }

  // ── 진입점: 푸터 '서비스' 열에 링크 주입(전 페이지 공통·nav 과밀 금지 원칙) ──────────
  //    열은 제목 문자열이 아니라 `[data-feedback-slot]` 속성으로 찾는다 — 제목은 언어·문구
  //    사전을 타서 바뀔 수 있고, 그러면 링크가 조용히 사라진다(속성은 언어와 무관).
  //    마이페이지(계정) 링크가 있으면 그 앞에 둔다 — 계정 링크는 열의 맨 끝.
  var slot = document.querySelector("footer.site .foot [data-feedback-slot]");
  if (!slot) return;
  var trigger = document.createElement("a");
  trigger.href = "#";
  trigger.id = "grm-feedback-open";
  trigger.textContent = _t("문의 및 제안");
  var meLink = slot.querySelector('a[href$="me/index.html"]');
  if (meLink) slot.insertBefore(trigger, meLink); else slot.appendChild(trigger);
  // ── 소개 페이지 연락 블록(`[data-feedback-mount]`)에도 같은 진입 버튼 — 푸터 링크와 같은
  //    관례(JS 미실행이면 흔적 0). 없는 페이지에서는 아무것도 하지 않는다.
  var mount = document.querySelector("[data-feedback-mount]");
  var mountBtn = null;
  if (mount) {
    mountBtn = document.createElement("a");
    mountBtn.href = "#";
    mountBtn.className = "about-fb";
    mountBtn.textContent = _t("문의 및 제안");
    mount.appendChild(mountBtn);
  }

  var pop = null, lastFocus = null, sending = false, saveTimer = null;

  function el(id) { return pop ? pop.querySelector("#" + id) : null; }

  function buildPop() {
    pop = document.createElement("div");
    pop.className = "grm-fb-pop";
    pop.id = "grm-fb-pop";
    var opts = CATEGORIES.map(function (c) {
      return '<option value="' + c[0] + '">' + c[1] + "</option>";
    }).join("");
    // 정적 문자열만 innerHTML — 사용자 입력은 어떤 경로로도 마크업에 섞지 않는다.
    pop.innerHTML =
      '<div class="grm-fb-card" role="dialog" aria-modal="true" aria-labelledby="grm-fb-title" aria-describedby="grm-fb-lede">' +
      '<button class="grm-fb-x" type="button" id="grm-fb-close" aria-label="' + _t("닫기") + '">&times;</button>' +
      '<h3 id="grm-fb-title">' + _t("문의 및 제안") + '</h3>' +
      '<p class="grm-fb-lede" id="grm-fb-lede">' + _t("이용하며 불편했던 점, 잘못된 내용, 있으면 좋을 기능을 알려주세요.") + '<br>' +
      _t("보내주신 의견은 운영자가 직접 확인합니다.") + '</p>' +
      '<form class="grm-fb-form" id="grm-fb-form" novalidate>' +
      '<div class="grm-fb-field">' +
      '<label for="grm-fb-cat">' + _t("문의 유형") + '</label>' +
      '<select id="grm-fb-cat" name="category" required>' + opts + "</select>" +
      "</div>" +
      '<div class="grm-fb-field">' +
      '<div class="grm-fb-labelrow"><label for="grm-fb-msg">' + _t("내용") + '</label>' +
      '<span class="grm-fb-count" id="grm-fb-count" aria-live="off">0 / ' + MAX + "</span></div>" +
      '<textarea id="grm-fb-msg" name="message" required minlength="5" maxlength="' + MAX + '" rows="5"></textarea>' +
      "</div>" +
      '<div class="grm-fb-field">' +
      '<label for="grm-fb-email">' + _t("회신 받을 이메일") + ' <span class="grm-fb-opt">' + _t("선택") + '</span></label>' +
      '<input type="email" id="grm-fb-email" name="email" maxlength="200" autocomplete="email" placeholder="you@company.com">' +
      '<label class="grm-fb-consent" id="grm-fb-consent-row" hidden>' +
      '<input type="checkbox" id="grm-fb-consent" name="consent">' +
      '<span>' + _t("회신을 위해 이메일 주소를 수집·이용하는 데 동의합니다. 답변 후 보관하지 않으며, 동의하지 않으면 이메일 없이 접수됩니다.") + '</span>' +
      "</label>" +
      "</div>" +
      '<p class="grm-fb-meta">' + _t("접수 시 현재 페이지 주소와 브라우저·화면 정보가 함께 전달됩니다 — 재현에만 사용합니다.") + '</p>' +
      '<button class="grm-fb-submit" type="submit" id="grm-fb-submit">' + _t("보내기") + '</button>' +
      "</form>" +
      '<p class="grm-fb-status" id="grm-fb-status" role="status" aria-live="polite"></p>' +
      '<div class="grm-fb-done" id="grm-fb-done" hidden>' +
      '<span class="grm-fb-done-ic" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>' +
      "</span>" +
      "<b>" + _t("접수되었습니다") + "</b>" +
      '<span class="grm-fb-ticket" id="grm-fb-ticket"></span>' +
      "<span>" + _t("보내주신 내용은 운영자가 직접 확인합니다. 감사합니다.") + "</span>" +
      '<button class="grm-fb-doneclose" type="button" id="grm-fb-doneclose">' + _t("닫기") + '</button>' +
      "</div>" +
      "</div>";
    document.body.appendChild(pop);

    var form = el("grm-fb-form");
    var cat = el("grm-fb-cat");
    var msg = el("grm-fb-msg");
    var email = el("grm-fb-email");
    var consentRow = el("grm-fb-consent-row");
    var consent = el("grm-fb-consent");

    function syncPlaceholder() {
      for (var i = 0; i < CATEGORIES.length; i++) {
        if (CATEGORIES[i][0] === cat.value) { msg.placeholder = CATEGORIES[i][2]; return; }
      }
    }
    function syncCount() {
      var n = msg.value.length;
      el("grm-fb-count").textContent = n + " / " + MAX;
    }
    // 이메일을 적기 시작해야 동의 줄이 나타난다 — 안 적는 사람에게 동의를 묻지 않는다.
    function syncConsent() {
      var want = email.value.trim() !== "";
      consentRow.hidden = !want;
      if (!want) consent.checked = false;
    }
    function saveDraft() {
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({
          c: cat.value, m: msg.value, e: email.value
        }));
      } catch (e) { /* storage 불가 = 초안 보관 포기(제출에는 영향 없음) */ }
    }
    function queueSave() {
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(saveDraft, 400);
    }

    cat.addEventListener("change", function () { syncPlaceholder(); queueSave(); });
    msg.addEventListener("input", function () { syncCount(); queueSave(); });
    email.addEventListener("input", function () { syncConsent(); queueSave(); });
    syncPlaceholder();
    syncCount();

    pop.addEventListener("click", function (e) { if (e.target === pop) close(); });
    el("grm-fb-close").addEventListener("click", close);
    el("grm-fb-doneclose").addEventListener("click", close);

    // 접근성: ESC 로 닫고, Tab 은 카드 안에 가둔다(모달 밖 요소로 포커스가 새지 않게).
    pop.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { close(); return; }
      if (e.key !== "Tab") return;
      var f = pop.querySelectorAll("button, select, textarea, input");
      var vis = [];
      for (var i = 0; i < f.length; i++) {
        if (f[i].offsetParent !== null && !f[i].disabled) vis.push(f[i]);
      }
      if (!vis.length) return;
      var first = vis[0], last = vis[vis.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (sending) return;
      var status = el("grm-fb-status");
      var body = msg.value.trim();
      var addr = email.value.trim();
      status.className = "grm-fb-status";
      if (body.length < 5) {
        status.textContent = _t("내용을 5자 이상 적어주세요.");
        msg.focus();
        return;
      }
      if (addr && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(addr)) {
        status.textContent = _t("이메일 주소 형식을 확인해 주세요.");
        email.focus();
        return;
      }
      if (addr && !consent.checked) {
        status.textContent = _t("이메일로 회신받으시려면 수집·이용 동의가 필요합니다.");
        consent.focus();
        return;
      }
      sending = true;
      var btn = el("grm-fb-submit");
      btn.disabled = true;
      btn.textContent = _t("보내는 중…");
      status.textContent = "";
      fetch(SUPA_URL + "/rest/v1/rpc/feedback_submit", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          apikey: SUPA_KEY,
          Authorization: "Bearer " + SUPA_KEY
        },
        body: JSON.stringify({
          p_category: cat.value,
          p_message: body,
          p_email: addr || null,
          p_consent: !!(addr && consent.checked),
          p_page: (location.pathname || "").slice(0, 300),
          p_ua: (navigator.userAgent || "").slice(0, 400),
          p_viewport: viewportLabel(),
          p_operator: isOperator()
        })
      }).then(function (r) {
        return r.text().then(function (raw) {
          if (!r.ok) throw { status: r.status, raw: raw };
          var ticket = parseInt(raw, 10);
          done(isFinite(ticket) && ticket > 0 ? ticket : 0);
        });
      }).catch(function (err) {
        sending = false;
        btn.disabled = false;
        btn.textContent = _t("보내기");
        var raw = (err && err.raw) || "";
        status.className = "grm-fb-status is-err";
        status.textContent = raw.indexOf("rate limited") >= 0
          ? _t("잠시 뒤에 다시 시도해 주세요. 접수가 한꺼번에 몰리고 있습니다.")
          : _t("전송에 실패했습니다. 네트워크를 확인하고 다시 시도해 주세요.");
      });
    });
  }

  function done(ticket) {
    sending = false;
    el("grm-fb-form").hidden = true;
    el("grm-fb-status").textContent = "";
    el("grm-fb-ticket").textContent = ticket ? _t("접수번호 {ticket}번", { ticket: ticket }) : "";
    el("grm-fb-done").hidden = false;
    try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* noop */ }
    el("grm-fb-doneclose").focus();
  }

  function open() {
    if (!pop) buildPop();
    var form = el("grm-fb-form");
    var btn = el("grm-fb-submit");
    sending = false;
    form.hidden = false;
    el("grm-fb-done").hidden = true;
    el("grm-fb-status").textContent = "";
    el("grm-fb-status").className = "grm-fb-status";
    btn.disabled = false;
    btn.textContent = _t("보내기");
    // 쓰다 만 초안 복원 — 실수로 닫았거나 페이지를 옮겨도 내용이 사라지지 않는다.
    var draft = null;
    try { draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch (e) { draft = null; }
    if (draft && typeof draft === "object") {
      if (draft.c) el("grm-fb-cat").value = draft.c;
      if (draft.m) el("grm-fb-msg").value = String(draft.m).slice(0, MAX);
      if (draft.e) el("grm-fb-email").value = String(draft.e).slice(0, 200);
    }
    el("grm-fb-cat").dispatchEvent(new Event("change"));
    el("grm-fb-msg").dispatchEvent(new Event("input"));
    el("grm-fb-email").dispatchEvent(new Event("input"));
    lastFocus = document.activeElement;
    pop.classList.add("show");
    el("grm-fb-cat").focus();
  }

  function close() {
    if (!pop) return;
    pop.classList.remove("show");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  trigger.addEventListener("click", function (e) { e.preventDefault(); open(); });
  if (mountBtn) mountBtn.addEventListener("click", function (e) { e.preventDefault(); open(); });
})();
