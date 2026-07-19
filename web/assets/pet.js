(function () {
  "use strict";
  var root = document.getElementById("grm-pet");
  if (!root || !window.localStorage) return;
  var KEY = "grm-gurumi-growth", POS_KEY = "grm-gurumi-position-v1", VERSION = 1;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var stages = [
    { min: 0, slug: "egg", name: "알", mood: "톡톡, 곧 만날 수 있을 것 같아요" },
    { min: 50, slug: "baby", name: "아기 구름이", mood: "세상의 규제 소식이 궁금해요" },
    { min: 150, slug: "youth", name: "소년 구름이", mood: "오늘도 문서 한 장을 배웠어요" },
    { min: 350, slug: "adult", name: "어른 구름이", mood: "핵심만 쏙쏙 정리할 준비 완료!" },
    { min: 700, slug: "legend", name: "전설 구름이", mood: "규제의 하늘을 든든하게 지켜요" }
  ];
  var $ = function (id) { return document.getElementById(id); };
  var toggle = $("grm-pet-toggle"), panel = $("grm-pet-panel"), close = $("grm-pet-close"), resetPos = $("grm-pet-reset-pos");
  var sprite = $("grm-pet-sprite"), hero = $("grm-pet-hero-img"), talk = $("grm-pet-talk");
  var stateChip = root.querySelector("#grm-pet-state-chip b"), stateName = $("grm-pet-state-name"), stateCopy = $("grm-pet-state-copy");
  var currentStage = -1, talkTimer, stateTimer, blinkTimer, scrollTimer, previewStage = null, drag = null, suppressClick = false, activeDock = null, nearDock = null;
  var states = {
    idle: { name: "쉬는 중", copy: "새로운 소식을 기다리고 있어요" },
    reading: { name: "읽는 중", copy: "지금 보고 있는 내용을 따라가고 있어요" },
    thinking: { name: "생각 중", copy: "답을 고르는 중이에요" },
    ready: { name: "완료", copy: "정답과 성장 포인트를 확인했어요" },
    retry: { name: "다시 도전", copy: "다음 문제를 함께 풀어봐요" },
    moving: { name: "이동 중", copy: "새 자리를 찾고 있어요" },
    sleeping: { name: "잠든 중", copy: "돌아오면 다시 깨어나요" },
    together: { name: "함께 보는 중", copy: "성장 기록을 살펴보고 있어요" }
  };

  function restingState() { return panel.hidden ? "idle" : "together"; }
  function setPetState(name, ttl) {
    if (!states[name]) name = "idle";
    clearTimeout(stateTimer);
    root.setAttribute("data-pet-state", name);
    stateChip.textContent = states[name].name; stateName.textContent = states[name].name; stateCopy.textContent = states[name].copy;
    if (ttl) stateTimer = setTimeout(function () { setPetState(restingState()); }, ttl);
  }

  function load() {
    try { var d = JSON.parse(localStorage.getItem(KEY)); if (d && d.version === VERSION && d.weeks) return d; } catch (e) {}
    return { version: VERSION, weeks: {} };
  }
  function weekInfo(now) {
    var d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    var day = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - day + 3);
    var first = new Date(Date.UTC(d.getUTCFullYear(), 0, 4)), fd = (first.getUTCDay() + 6) % 7;
    first.setUTCDate(first.getUTCDate() - fd + 3);
    return { key: String(d.getUTCFullYear() * 100 + 1 + Math.round((d - first) / 604800000)), idx: Math.floor(d.getTime() / 604800000) };
  }
  function derive() {
    var d = load(), answered = 0, correct = 0, weeks = 0, present = {}, cur = weekInfo(new Date()).idx;
    Object.keys(d.weeks).forEach(function (key) { var w = d.weeks[key], q = w.q || {}; weeks++; present[w.idx] = 1; Object.keys(q).forEach(function (id) { answered++; if (q[id]) correct++; }); });
    var anchor = present[cur] ? cur : (present[cur - 1] ? cur - 1 : null), streak = 0;
    if (anchor !== null) { streak = 1; while (present[anchor - streak]) streak++; }
    var points = correct * 10 + weeks * 20 + Math.min(Math.max(streak - 1, 0), 5) * 10, si = 0;
    stages.forEach(function (s, i) { if (points >= s.min) si = i; });
    return { data: d, answered: answered, correct: correct, weeks: weeks, streak: streak, points: points, stage: si };
  }
  function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }
  function updateSide() {
    var r = root.getBoundingClientRect();
    root.classList.toggle("is-left-side", r.left + r.width / 2 < window.innerWidth / 2);
  }
  function setPosition(x, y) {
    var r = root.getBoundingClientRect(), margin = 8;
    x = clamp(x, margin, Math.max(margin, window.innerWidth - r.width - margin));
    y = clamp(y, margin, Math.max(margin, window.innerHeight - r.height - margin));
    root.style.left = Math.round(x) + "px"; root.style.top = Math.round(y) + "px";
    root.style.right = "auto"; root.style.bottom = "auto"; updateSide();
    if (!panel.hidden) positionPanel();
  }
  function dockTargets() {
    var r = root.getBoundingClientRect(), w = r.width, h = r.height, gap = window.innerWidth <= 640 ? 9 : 14;
    var top = Math.min(Math.max(70, gap), Math.max(gap, window.innerHeight - h - gap));
    var bottom = Math.max(gap, window.innerHeight - h - gap), middle = clamp((window.innerHeight - h) / 2, gap, bottom);
    return {
      "top-left": { x: gap, y: top }, "top-right": { x: window.innerWidth - w - gap, y: top },
      "middle-left": { x: gap, y: middle }, "middle-right": { x: window.innerWidth - w - gap, y: middle },
      "bottom-left": { x: gap, y: bottom }, "bottom-right": { x: window.innerWidth - w - gap, y: bottom }
    };
  }
  var dockLayer = document.createElement("div");
  dockLayer.className = "grm-pet-docks"; dockLayer.setAttribute("aria-hidden", "true");
  ["top-left", "top-right", "middle-left", "middle-right", "bottom-left", "bottom-right"].forEach(function (id) {
    var marker = document.createElement("i"); marker.className = "grm-pet-dock"; marker.setAttribute("data-dock", id); dockLayer.appendChild(marker);
  });
  document.body.appendChild(dockLayer);
  function renderDocks() {
    var targets = dockTargets(), r = root.getBoundingClientRect();
    dockLayer.querySelectorAll("[data-dock]").forEach(function (marker) {
      var p = targets[marker.getAttribute("data-dock")]; marker.style.left = Math.round(p.x + r.width / 2) + "px"; marker.style.top = Math.round(p.y + r.height - 8) + "px";
    });
  }
  function selectNearestDock() {
    var r = root.getBoundingClientRect(), targets = dockTargets(), best = null, distance = Infinity;
    Object.keys(targets).forEach(function (id) { var p = targets[id], d = Math.hypot(r.left - p.x, r.top - p.y); if (d < distance) { best = id; distance = d; } });
    nearDock = distance <= (window.innerWidth <= 640 ? 76 : 96) ? best : null;
    dockLayer.querySelectorAll("[data-dock]").forEach(function (marker) { marker.classList.toggle("is-near", marker.getAttribute("data-dock") === nearDock); });
  }
  function setDock(id, persist) {
    var target = dockTargets()[id]; if (!target) return;
    activeDock = id; setPosition(target.x, target.y);
    if (persist) { try { localStorage.setItem(POS_KEY, JSON.stringify({ version: 1, dock: id })); } catch (e) {} }
  }
  function savePosition() {
    try {
      activeDock = null;
      var r = root.getBoundingClientRect(), ax = Math.max(1, window.innerWidth - r.width), ay = Math.max(1, window.innerHeight - r.height);
      localStorage.setItem(POS_KEY, JSON.stringify({ version: 1, x: clamp(r.left / ax, 0, 1), y: clamp(r.top / ay, 0, 1) }));
    } catch (e) {}
  }
  function restorePosition() {
    try {
      var p = JSON.parse(localStorage.getItem(POS_KEY));
      if (p && p.version === 1 && typeof p.dock === "string" && dockTargets()[p.dock]) { setDock(p.dock, false); return; }
      if (!p || p.version !== 1 || typeof p.x !== "number" || typeof p.y !== "number") { updateSide(); return; }
      activeDock = null;
      var r = root.getBoundingClientRect(); setPosition(p.x * Math.max(1, window.innerWidth - r.width), p.y * Math.max(1, window.innerHeight - r.height));
    } catch (e) { updateSide(); }
  }
  function resetPosition() {
    try { localStorage.removeItem(POS_KEY); } catch (e) {}
    activeDock = null; nearDock = null;
    root.style.removeProperty("left"); root.style.removeProperty("top"); root.style.removeProperty("right"); root.style.removeProperty("bottom");
    requestAnimationFrame(function () { updateSide(); positionPanel(); });
    say("기본 위치로 돌아왔어요");
  }
  function positionPanel() {
    if (panel.hidden) return;
    var r = root.getBoundingClientRect(), pw = panel.offsetWidth, ph = panel.offsetHeight, vw = window.innerWidth, vh = window.innerHeight, gap = 10, margin = 10, x, y;
    if (vw > 640 && r.left >= pw + gap + margin) x = r.left - pw - gap;
    else if (vw > 640 && vw - r.right >= pw + gap + margin) x = r.right + gap;
    else x = clamp(r.left + r.width / 2 - pw / 2, margin, vw - pw - margin);
    if (vw <= 640 && r.top >= ph + gap + margin) y = r.top - ph - gap;
    else if (vw <= 640 && vh - r.bottom >= ph + gap + margin) y = r.bottom + gap;
    else y = clamp(r.top + r.height / 2 - ph / 2, margin, vh - ph - margin);
    panel.style.left = Math.round(clamp(x, margin, vw - pw - margin)) + "px";
    panel.style.top = Math.round(clamp(y, margin, vh - ph - margin)) + "px";
  }
  function asset(i) { return "/assets/gurumi-" + stages[i].slug + ".png"; }
  function atlas() {
    var host = $("grm-pet-atlas"), html = "";
    stages.forEach(function (s, i) { html += '<button class="grm-pet-stage" type="button" data-stage="' + i + '" aria-label="' + s.name + ' 미리보기"><img src="' + asset(i) + '" width="512" height="512" loading="lazy" decoding="async" alt=""><span>' + s.name.replace(" 구름이", "") + "</span></button>"; });
    host.innerHTML = html;
    host.addEventListener("click", function (e) { var b = e.target.closest("[data-stage]"); if (!b) return; preview(parseInt(b.getAttribute("data-stage"), 10)); });
  }
  function preview(i) {
    previewStage = i; hero.src = asset(i); hero.alt = stages[i].name; $("grm-pet-name").textContent = stages[i].name; $("grm-pet-mood").textContent = stages[i].mood;
    $("grm-pet-preview").hidden = i === currentStage; party();
  }
  function refresh(celebrate) {
    var s = derive(), i = s.stage, next = stages[i + 1], pct = next ? Math.round((s.points - stages[i].min) * 100 / (next.min - stages[i].min)) : 100;
    var evolved = currentStage >= 0 && i > currentStage; currentStage = i; previewStage = null; root.setAttribute("data-stage", i);
    sprite.src = asset(i); hero.src = asset(i); hero.alt = stages[i].name; $("grm-pet-name").textContent = stages[i].name; $("grm-pet-mood").textContent = stages[i].mood; $("grm-pet-level").textContent = "Lv." + (i + 1);
    $("grm-pet-points").textContent = s.points + " 포인트"; $("grm-pet-next").textContent = next ? (next.min - s.points) + " 포인트 남음" : "최고 단계"; $("grm-pet-bar").style.width = pct + "%";
    $("grm-pet-correct").textContent = s.correct; $("grm-pet-weeks").textContent = s.weeks; $("grm-pet-streak").textContent = s.streak; $("grm-pet-preview").hidden = true;
    root.querySelectorAll(".grm-pet-stage").forEach(function (b, n) { b.classList.toggle("is-current", n === i); b.classList.toggle("is-future", n > i); if (n === i) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current"); });
    if (evolved) { panel.classList.add("is-evolve"); openPanel(); say("새로운 모습으로 성장했어요! ✨"); setTimeout(function () { panel.classList.remove("is-evolve"); }, 950); }
    if (celebrate) { party(); say(celebrate === "correct" ? "정답! 포인트가 쑥 올랐어요 ✨" : "좋은 도전이었어요. 다음 문제도 함께해요!"); }
  }
  function openPanel() { panel.hidden = false; positionPanel(); toggle.setAttribute("aria-expanded", "true"); toggle.setAttribute("aria-label", "구름이 펫 닫기"); setPetState("together"); }
  function closePanel() { panel.hidden = true; toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-label", "구름이 펫 열기"); if (previewStage !== null) refresh(); setPetState("idle"); }
  function say(text) { talk.textContent = text; talk.classList.add("show"); clearTimeout(talkTimer); talkTimer = setTimeout(function () { talk.classList.remove("show"); }, 3000); }
  function particles(count) {
    if (reduce) return; var glyphs = ["♥", "✦", "★", "●"];
    for (var i = 0; i < count; i++) { (function (n) { setTimeout(function () { var p = document.createElement("i"); p.className = "grm-pet-particle"; p.textContent = glyphs[Math.floor(Math.random() * glyphs.length)]; p.style.color = n % 2 ? "#e8b04a" : "#ce6e4c"; p.style.setProperty("--x", (Math.random() * 90 - 45) + "px"); p.style.setProperty("--y", (-55 - Math.random() * 45) + "px"); p.style.setProperty("--r", (Math.random() * 100 - 50) + "deg"); root.appendChild(p); setTimeout(function () { p.remove(); }, 1000); }, n * 55); })(i); }
  }
  function pat() { root.classList.remove("is-patted"); void root.offsetWidth; root.classList.add("is-patted"); particles(5); say(["기분이 몽글몽글해요 ☁️", "조금 더 가까워진 것 같아요", "오늘도 같이 소식을 살펴봐요"][Math.floor(Math.random() * 3)]); setTimeout(function () { root.classList.remove("is-patted"); }, 700); }
  function party() { panel.classList.remove("is-party"); void panel.offsetWidth; panel.classList.add("is-party"); particles(7); setTimeout(function () { panel.classList.remove("is-party"); }, 850); }
  function scheduleBlink() {
    clearTimeout(blinkTimer); if (reduce) return;
    blinkTimer = setTimeout(function blink() {
      if (currentStage > 0 && document.visibilityState !== "hidden" && root.getAttribute("data-pet-state") !== "sleeping") {
        root.classList.add("is-blink");
        setTimeout(function () {
          root.classList.remove("is-blink");
          if (Math.random() < .22) setTimeout(function () { root.classList.add("is-blink"); setTimeout(function () { root.classList.remove("is-blink"); }, 120); }, 130);
        }, 145);
      }
      blinkTimer = setTimeout(blink, 2800 + Math.random() * 2400);
    }, 1200 + Math.random() * 1800);
  }
  atlas(); refresh(); restorePosition(); setPetState("idle"); scheduleBlink();
  (function ambient() { setTimeout(function () { if (panel.hidden && !reduce) { root.classList.add("is-curious"); setTimeout(function () { root.classList.remove("is-curious"); }, 1000); } ambient(); }, 6500 + Math.random() * 5500); })();
  toggle.addEventListener("pointerdown", function (e) {
    if (e.button !== undefined && e.button !== 0) return;
    var r = root.getBoundingClientRect(); drag = { id: e.pointerId, sx: e.clientX, sy: e.clientY, x: r.left, y: r.top, moved: false };
    if (toggle.setPointerCapture) toggle.setPointerCapture(e.pointerId);
  });
  toggle.addEventListener("pointermove", function (e) {
    if (!drag || drag.id !== e.pointerId) return;
    var dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    if (!drag.moved) { drag.moved = true; activeDock = null; renderDocks(); dockLayer.classList.add("show"); setPetState("moving"); }
    root.classList.add("is-dragging"); e.preventDefault(); setPosition(drag.x + dx, drag.y + dy); selectNearestDock();
  });
  window.addEventListener("pointermove", function (e) {
    if (!drag || drag.id !== e.pointerId || e.target === toggle || toggle.contains(e.target)) return;
    var dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    if (!drag.moved) { drag.moved = true; activeDock = null; renderDocks(); dockLayer.classList.add("show"); setPetState("moving"); }
    root.classList.add("is-dragging"); e.preventDefault(); setPosition(drag.x + dx, drag.y + dy); selectNearestDock();
  }, { passive: false });
  function endDrag(e) {
    if (!drag || drag.id !== e.pointerId) return;
    var moved = drag.moved, dock = nearDock; drag = null; root.classList.remove("is-dragging"); dockLayer.classList.remove("show");
    if (toggle.releasePointerCapture && toggle.hasPointerCapture && toggle.hasPointerCapture(e.pointerId)) toggle.releasePointerCapture(e.pointerId);
    if (moved) {
      if (dock) { setDock(dock, true); root.classList.add("is-docking"); say("여기에 앉아 있을게요"); setTimeout(function () { root.classList.remove("is-docking"); }, 650); }
      else { savePosition(); say("여기에 자리 잡을게요"); }
      nearDock = null; dockLayer.querySelectorAll(".is-near").forEach(function (marker) { marker.classList.remove("is-near"); });
      setPetState(restingState(), 900); suppressClick = true; setTimeout(function () { suppressClick = false; }, 0);
    }
  }
  toggle.addEventListener("pointerup", endDrag); toggle.addEventListener("pointercancel", endDrag); toggle.addEventListener("lostpointercapture", endDrag);
  window.addEventListener("pointerup", endDrag); window.addEventListener("pointercancel", endDrag);
  toggle.addEventListener("keydown", function (e) {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].indexOf(e.key) < 0) return;
    e.preventDefault(); if (e.key === "Home") { resetPosition(); return; }
    var r = root.getBoundingClientRect(), step = e.shiftKey ? 24 : 10, x = r.left, y = r.top;
    if (e.key === "ArrowLeft") x -= step; if (e.key === "ArrowRight") x += step; if (e.key === "ArrowUp") y -= step; if (e.key === "ArrowDown") y += step;
    activeDock = null; setPosition(x, y); savePosition(); setPetState("moving", 700);
  });
  toggle.addEventListener("click", function () { if (suppressClick) return; if (panel.hidden) { openPanel(); refresh(); } else closePanel(); });
  close.addEventListener("click", closePanel); resetPos.addEventListener("click", resetPosition); $("grm-pet-pat").addEventListener("click", pat);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) { closePanel(); toggle.focus(); } });
  document.addEventListener("visibilitychange", function () { setPetState(document.visibilityState === "hidden" ? "sleeping" : restingState()); });
  document.addEventListener("scroll", function () { if (document.visibilityState === "hidden" || drag) return; clearTimeout(scrollTimer); setPetState("reading"); scrollTimer = setTimeout(function () { setPetState(restingState()); }, 1500); }, { passive: true });
  document.addEventListener("pointerover", function (e) { if (e.target.closest && e.target.closest(".qz-choice")) setPetState("thinking", 2400); });
  document.addEventListener("focusin", function (e) { if (e.target.closest && e.target.closest(".qz-choice")) setPetState("thinking", 2400); });
  window.addEventListener("storage", function (e) { if (e.key === KEY) refresh(); });
  window.addEventListener("resize", function () { if (activeDock) setDock(activeDock, false); else restorePosition(); renderDocks(); if (!panel.hidden) positionPanel(); });
  window.addEventListener("grm:gurumi-change", function (e) { var correct = e.detail && e.detail.correct; refresh(e.detail ? (correct ? "correct" : "try") : null); setPetState(correct ? "ready" : "retry", 3200); });
  setTimeout(function () { if (panel.hidden) say("저를 눌러 성장 상태를 확인해 보세요"); }, 1600);
  window.GurumiPet = { refresh: refresh, derive: derive, stages: stages, celebrate: party, setState: setPetState, setDock: setDock };
})();
