(function () {
  "use strict";
  var root = document.getElementById("grm-pet");
  if (!root || !window.localStorage) return;
  var KEY = "grm-gurumi-growth", VERSION = 1;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var stages = [
    { min: 0, slug: "egg", name: "알", mood: "톡톡, 곧 만날 수 있을 것 같아요" },
    { min: 50, slug: "baby", name: "아기 구름이", mood: "세상의 규제 소식이 궁금해요" },
    { min: 150, slug: "youth", name: "소년 구름이", mood: "오늘도 문서 한 장을 배웠어요" },
    { min: 350, slug: "adult", name: "어른 구름이", mood: "핵심만 쏙쏙 정리할 준비 완료!" },
    { min: 700, slug: "legend", name: "전설 구름이", mood: "규제의 하늘을 든든하게 지켜요" }
  ];
  var $ = function (id) { return document.getElementById(id); };
  var toggle = $("grm-pet-toggle"), panel = $("grm-pet-panel"), close = $("grm-pet-close");
  var sprite = $("grm-pet-sprite"), hero = $("grm-pet-hero-img"), talk = $("grm-pet-talk");
  var currentStage = -1, talkTimer, previewStage = null;

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
  function openPanel() { panel.hidden = false; toggle.setAttribute("aria-expanded", "true"); toggle.setAttribute("aria-label", "구름이 펫 닫기"); }
  function closePanel() { panel.hidden = true; toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-label", "구름이 펫 열기"); if (previewStage !== null) refresh(); }
  function say(text) { talk.textContent = text; talk.classList.add("show"); clearTimeout(talkTimer); talkTimer = setTimeout(function () { talk.classList.remove("show"); }, 3000); }
  function particles(count) {
    if (reduce) return; var glyphs = ["♥", "✦", "★", "●"];
    for (var i = 0; i < count; i++) { (function (n) { setTimeout(function () { var p = document.createElement("i"); p.className = "grm-pet-particle"; p.textContent = glyphs[Math.floor(Math.random() * glyphs.length)]; p.style.color = n % 2 ? "#e8b04a" : "#ce6e4c"; p.style.setProperty("--x", (Math.random() * 90 - 45) + "px"); p.style.setProperty("--y", (-55 - Math.random() * 45) + "px"); p.style.setProperty("--r", (Math.random() * 100 - 50) + "deg"); root.appendChild(p); setTimeout(function () { p.remove(); }, 1000); }, n * 55); })(i); }
  }
  function pat() { root.classList.remove("is-patted"); void root.offsetWidth; root.classList.add("is-patted"); particles(5); say(["기분이 몽글몽글해요 ☁️", "조금 더 가까워진 것 같아요", "오늘도 같이 소식을 살펴봐요"][Math.floor(Math.random() * 3)]); setTimeout(function () { root.classList.remove("is-patted"); }, 700); }
  function party() { panel.classList.remove("is-party"); void panel.offsetWidth; panel.classList.add("is-party"); particles(7); setTimeout(function () { panel.classList.remove("is-party"); }, 850); }
  atlas(); refresh();
  (function ambient() { setTimeout(function () { if (panel.hidden && !reduce) { root.classList.add("is-curious"); setTimeout(function () { root.classList.remove("is-curious"); }, 1000); } ambient(); }, 6500 + Math.random() * 5500); })();
  toggle.addEventListener("click", function () { if (panel.hidden) { openPanel(); refresh(); } else closePanel(); }); close.addEventListener("click", closePanel); $("grm-pet-pat").addEventListener("click", pat);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) { closePanel(); toggle.focus(); } });
  window.addEventListener("storage", function (e) { if (e.key === KEY) refresh(); });
  window.addEventListener("grm:gurumi-change", function (e) { refresh(e.detail ? (e.detail.correct ? "correct" : "try") : null); });
  setTimeout(function () { if (panel.hidden) say("저를 눌러 성장 상태를 확인해 보세요"); }, 1600);
  window.GurumiPet = { refresh: refresh, derive: derive, stages: stages, celebrate: party };
})();
