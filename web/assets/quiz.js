/* [주간 퀴즈] 클라이언트 로직 — 정적·무의존(vanilla). 서버가 embed 한 전 문항 중
 * "이번 주" 세트를 ISO 주차 키로 결정론 선택하고(같은 주 = 전 직원 동일 세트), 선택 즉시
 * 채점해 정답·해설·근거 링크를 보여준다. 결정론 렌더(골든) 불침범: 이 스크립트는 런타임에
 * 클래스/hidden/텍스트만 토글하며 문항·정답·해설·링크 콘텐츠를 만들지 않는다(전부 서버가
 * 렌더한 DOM 값). JS 미로드 시 전 문항이 그대로 보이고 근거 링크·해시 딥링크 무영향
 * (progressive enhancement). 랭킹·서버 저장 없음(성장 적립은 별도 growth.js 레이어 소관).
 * [13차] 학습 루프: ① 새로고침해도 그 주 선택을 복원(localStorage grm-quiz-picks-v1 —
 * 화면 상태 전용·서버 미전송, 성장 스키마 불침범) ② 이번 주 세트를 다 풀면 완주 요약과
 * 오답노트 노출 ③ 틀린 문제만 재도전(구름이 적립은 growth.js 의 주×문항 dedup 이 그대로
 * 막아 반복 적립 없음) ④ 전체 보기 필터(개념/사례/안 푼 문항만). 점수는 저장하지 않고
 * 카드 상태에서 매번 다시 세어 복원·재도전과 어긋나지 않는다. 오답노트는 서버가 렌더한
 * 질문문을 옮겨 담을 뿐이며 문항·정답·해설을 새로 만들지 않는다(결정론 계약 유지).
 * [9차 G3] week(YYYYWW) 필드: 뱅크 항목에 week 가 있으면 해당 주차 문항을 "이번 주"로
 * 우선 선정하고 부족분만 기존 회전으로 보충한다(월 13:00 자동 생성 파이프라인 계약).
 * week 가 하나도 없으면(현 데이터) 기존 회전과 완전 동일 경로 — node 테스트가 두 경로 고정. */
(function () {
  "use strict";
  function mod(n, m) { return ((n % m) + m) % m; }

  // [9차 G3] 주간 선택 — 순수 함수(DOM 무접촉·테스트 대상). items = [{index(뱅크순),
  // difficulty("easy"|"normal"), week("YYYYWW"|"")}]. 반환 = 선정 문항 index 오름차순.
  // 규칙: ① week===String(seed) 문항을 뱅크 순으로 최대 count 개 우선(초과분은 잘림 —
  // 지정분 난이도 구성은 생성 파이프라인 책임) ② 부족분은 **week 없는 legacy pool 에서만**
  // 기존 회전(normal 1~2 제한·easy 과반·seed 기반 결정론)으로 보충 ③ week 미보유 뱅크는
  // ①이 공집합이라 기존 회전과 산식·결과 동일(무회귀).
  //
  // ②의 "legacy pool 에서만"이 핵심이다(운영설계 addendum §3.2). 종전에는 pinned 만
  // 빼고 나머지를 전부 후보로 삼아, 생성이 걸러진 주에 **지난주·지지난주 문항이 "이번 주
  // 퀴즈"로 다시 떴다** — 2026-08-03 오전(그 주 생성 머지 전) 세트에 2주 전 q-202630-01 이
  // 실제로 들어갔다. 과거 주차 문항은 "전체 문항"에서 언제든 볼 수 있으므로 폴백에서
  // 빼도 잃는 것이 없고, 대신 "이번 주"라는 말이 거짓이 되지 않는다.
  function pickWeeklyIndexes(items, weeklyCount, seed) {
    var count = Math.min(weeklyCount, items.length);
    var wk = String(seed);
    var pinned = items.filter(function (it) { return it.week === wk; }).slice(0, count);
    var inPinned = {};
    pinned.forEach(function (it) { inPinned[it.index] = true; });
    var chosen = pinned.slice();
    var need = count - pinned.length;
    if (need > 0) {
      var legacy = items.filter(function (it) { return !it.week && !inPinned[it.index]; });
      var poolE = legacy.filter(function (it) { return it.difficulty === "easy"; });
      var poolN = legacy.filter(function (it) { return it.difficulty !== "easy"; });
      var normalCount = Math.min(need >= 5 ? 2 : 1, poolN.length, need);
      var easyCount = Math.min(need - normalCount, poolE.length);
      normalCount = Math.min(need - easyCount, poolN.length);
      var i, baseE = mod(seed * 3, Math.max(poolE.length, 1));
      for (i = 0; i < easyCount; i++) chosen.push(poolE[mod(baseE + i, poolE.length)]);
      var baseN = mod(seed, Math.max(poolN.length, 1));
      for (i = 0; i < normalCount; i++) chosen.push(poolN[mod(baseN + i, poolN.length)]);
    }
    chosen.sort(function (a, b) { return a.index - b.index; });
    return chosen.map(function (it) { return it.index; });
  }
  // 테스트(node)·후속 파이프라인 검증용 노출 — DOM 부재 환경에서도 순수 함수만 쓸 수 있게
  // root 가드보다 먼저 부착한다.
  if (typeof window !== "undefined") window.GRM_QUIZ = { pickWeeklyIndexes: pickWeeklyIndexes };

  var root = document.getElementById("grm-qz");
  if (!root) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll(".qz-card"));
  if (!cards.length) return;

  var weeklyCount = parseInt(root.getAttribute("data-weekly-count"), 10) || 4;
  var correctEl = document.getElementById("grm-qz-correct");
  var answeredEl = document.getElementById("grm-qz-answered");
  var subEl = document.getElementById("grm-qz-sub");
  var titleEl = document.getElementById("grm-qz-title");
  var toggle = document.getElementById("grm-qz-toggle");
  var filterBar = document.getElementById("grm-qz-filters");
  var filterBtns = filterBar ? Array.prototype.slice.call(filterBar.querySelectorAll("[data-filter]")) : [];
  var emptyEl = document.getElementById("grm-qz-empty");
  var resultEl = document.getElementById("grm-qz-result");
  var resultScoreEl = document.getElementById("grm-qz-result-score");
  var resultNoteEl = document.getElementById("grm-qz-result-note");
  var wrongWrapEl = document.getElementById("grm-qz-wrong");
  var wrongListEl = document.getElementById("grm-qz-wrong-list");
  var retryBtn = document.getElementById("grm-qz-retry");
  var shareBtn = document.getElementById("grm-qz-share");

  // ISO 8601 주차 키(연*100 + 주차) — 클라이언트 now() 기준. 같은 달력 주에는 모든 사용자가
  // 동일 seed 를 얻어 같은 문항 세트를 본다(렌더러 결정론과 무관 — 서버는 회전하지 않는다).
  function isoWeekSeed(now) {
    var d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    var dayNum = (d.getUTCDay() + 6) % 7;           // 월=0 … 일=6
    d.setUTCDate(d.getUTCDate() - dayNum + 3);        // 해당 주의 목요일로 이동
    var firstThu = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
    var firstDayNum = (firstThu.getUTCDay() + 6) % 7;
    firstThu.setUTCDate(firstThu.getUTCDate() - firstDayNum + 3);
    var week = 1 + Math.round((d - firstThu) / (7 * 24 * 3600 * 1000));
    return d.getUTCFullYear() * 100 + week;
  }

  // DOM 카드 → 순수 항목 서술자(뱅크 순 index·난이도·주차) — 선택은 pickWeeklyIndexes 소관.
  var items = cards.map(function (c) {
    return {
      index: parseInt(c.getAttribute("data-index"), 10),
      difficulty: c.getAttribute("data-difficulty") === "easy" ? "easy" : "normal",
      week: c.getAttribute("data-week") || ""
    };
  });

  var weekSeed = isoWeekSeed(new Date());
  // 이번 주 지정 세트가 실제로 있는가. 없으면 아래는 legacy 복습 세트다 — ISO 주차는
  // 월요일 00:00 에 넘어가는데 생성 머지는 그날 13시대라(3주 실측 13:18·13:15·15:24),
  // 아침에 브리프를 보고 들어온 사람은 매주 이 구간에 들어온다. 그때 "이번 주에 뽑은
  // 4문항이에요"라고 단언하면 오후 접속자와 다른 세트를 보면서도 같다고 듣게 된다.
  var hasPinnedSet = items.some(function (it) { return it.week === String(weekSeed); });
  var weeklySet = {};
  pickWeeklyIndexes(items, weeklyCount, weekSeed).forEach(function (i) { weeklySet[i] = true; });
  var weekly = cards.filter(function (c) { return !!weeklySet[c.getAttribute("data-index")]; });
  var mode = "weekly";
  var filter = "all";                                  // all | glossary | brief | unsolved

  // 전체 보기 필터 — 45문항(주 4문항씩 계속 는다)을 한 줄로 늘어놓으면 다 푼 문항을
  // 다시 훑는 것 말고 할 게 없다. "안 푼 문항만"은 growth.js 의 읽기 전용 조회창을
  // 쓴다(적립 정본은 그쪽 하나 — 여기서 localStorage 를 직접 뒤지지 않는다).
  function solvedMap() {
    try {
      if (window.GRM_GROWTH && typeof window.GRM_GROWTH.solvedIds === "function") {
        return window.GRM_GROWTH.solvedIds() || {};
      }
    } catch (e) { /* 조회창 부재·손상 → 필터를 끄되 화면은 정상 동작 */ }
    return null;
  }

  function passesFilter(card, solved) {
    if (filter === "glossary" || filter === "brief") return card.getAttribute("data-source") === filter;
    if (filter === "unsolved") {
      if (!solved) return true;                        // 조회 불가 → 거르지 않는다(빈 화면 방지)
      return !(card.id && solved[card.id]) && !card.getAttribute("data-done");
    }
    return true;
  }

  function applyMode() {
    var solved = (mode === "all" && filter === "unsolved") ? solvedMap() : null;
    var shown = 0;
    for (var i = 0; i < cards.length; i++) {
      var inWeek = !!weeklySet[cards[i].getAttribute("data-index")];
      var visible = (mode === "weekly") ? inWeek : passesFilter(cards[i], solved);
      cards[i].hidden = !visible;
      if (visible) shown++;
    }
    if (filterBar) filterBar.hidden = (mode !== "all");
    for (var f = 0; f < filterBtns.length; f++) {
      filterBtns[f].setAttribute("aria-pressed", filterBtns[f].getAttribute("data-filter") === filter ? "true" : "false");
    }
    if (emptyEl) emptyEl.hidden = shown > 0;
    if (mode === "weekly") {
      if (titleEl) titleEl.textContent = hasPinnedSet ? "이번 주 퀴즈" : "지난 문항으로 복습";
      if (subEl) {
        subEl.textContent = hasPinnedSet
          ? "이번 주에 뽑은 " + weekly.length + "문항이에요. 같은 주에는 모두 같은 문제를 풀어요."
          : "이번 주 문항은 아직 준비 중이에요. 그동안 지난 " + weekly.length + "문항으로 복습해 보세요.";
      }
      if (toggle) { toggle.textContent = "전체 " + cards.length + "문항 풀기"; toggle.setAttribute("aria-pressed", "false"); }
    } else {
      if (titleEl) titleEl.textContent = "전체 문항";
      if (subEl) subEl.textContent = "정본 문항 " + cards.length + "개 중 " + shown + "개를 보고 있어요.";
      if (toggle) { toggle.textContent = "이번 주 문항만 보기"; toggle.setAttribute("aria-pressed", "true"); }
    }
    updateResult();
  }

  // ── 풀이 복원 저장(이 브라우저 전용·서버 미전송) ─────────────────────────────
  // 성장 적립 정본은 growth.js 의 grm-gurumi-growth 이고, 그 스키마는 서버 동기화
  // (growth-sync.js·032)가 q[id] 를 0|1 로 정규화한다 — "내가 고른 보기"를 거기 얹으면
  // 동기화가 객체를 1 로 납작하게 만들어 조용히 사라진다. 화면 복원은 순수 UI 상태이므로
  // 별도 키에 현재 주차분만 담고 주가 바뀌면 통째로 버린다(자기 정리·서버 무관).
  var PICKS_KEY = "grm-quiz-picks-v1";
  function loadPicks() {
    try {
      var d = JSON.parse(window.localStorage.getItem(PICKS_KEY));
      if (d && d.version === 1 && String(d.week) === String(weekSeed) &&
          d.picks && typeof d.picks === "object") return d.picks;
    } catch (e) { /* 손상 데이터 → 초기화 */ }
    return {};
  }
  function savePicks() {
    try {
      window.localStorage.setItem(PICKS_KEY,
        JSON.stringify({ version: 1, week: String(weekSeed), picks: picks }));
    } catch (e) { /* 저장 불가(시크릿 모드 등) → 복원만 포기, 동작은 그대로 */ }
  }
  var picks = window.localStorage ? loadPicks() : {};

  // ── 점수는 저장하지 않고 카드 상태에서 매번 다시 센다 ────────────────────────
  // 종전에는 증가 카운터라 복원·재도전에서 실제 화면과 어긋났다(구름이 누적치와 다른
  // 숫자가 한 화면에 동시에 뜨던 원인). 파생값은 드리프트가 없다.
  function weeklyStats() {
    var done = 0, right = 0, wrong = [];
    for (var i = 0; i < weekly.length; i++) {
      if (!weekly[i].getAttribute("data-done")) continue;
      done++;
      if (weekly[i].getAttribute("data-correct") === "1") right++; else wrong.push(weekly[i]);
    }
    return { total: weekly.length, done: done, right: right, wrong: wrong };
  }

  function updateScore() {
    var done = 0, right = 0;
    for (var i = 0; i < cards.length; i++) {
      if (!cards[i].getAttribute("data-done")) continue;
      done++;
      if (cards[i].getAttribute("data-correct") === "1") right++;
    }
    if (correctEl) correctEl.textContent = String(right);
    if (answeredEl) answeredEl.textContent = String(done);
    updateResult();
  }

  // ── 완주 요약·오답노트 ───────────────────────────────────────────────────────
  // 서버가 렌더한 값만 옮겨 담는다(질문문 textContent 재사용) — 문항·정답·해설을
  // 새로 만들지 않는다는 결정론 계약은 그대로다.
  var resultShown = false, initialised = false;
  function updateResult() {
    if (!resultEl) return;
    var s = weeklyStats();
    var complete = mode === "weekly" && s.total > 0 && s.done === s.total;
    resultEl.hidden = !complete;
    if (!complete) { resultShown = false; return; }

    var titleNode = resultEl.querySelector(".qz-result-title");
    if (titleNode) titleNode.textContent = hasPinnedSet ? "이번 주 퀴즈 완주!" : "복습 완주!";
    if (resultScoreEl) resultScoreEl.textContent = s.total + "문제 중 " + s.right + "개를 맞혔어요.";
    if (resultNoteEl) {
      resultNoteEl.textContent = s.wrong.length
        ? "다시 풀어도 구름이 점수는 처음 결과로 남아요 — 편하게 복습하세요."
        : "이번 주 문항을 모두 맞혔어요. 다음 주에 새 문항으로 만나요!";
    }
    if (wrongWrapEl) wrongWrapEl.hidden = !s.wrong.length;
    if (retryBtn) retryBtn.hidden = !s.wrong.length;
    if (wrongListEl) {
      while (wrongListEl.firstChild) wrongListEl.removeChild(wrongListEl.firstChild);
      s.wrong.forEach(function (card) {
        var q = card.querySelector(".qz-q");
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = "#" + card.id;
        a.textContent = q ? q.textContent : card.id;
        li.appendChild(a);
        wrongListEl.appendChild(li);
      });
    }
    // 첫 완주 순간에만 시야로 옮긴다. 복원으로 이미 완주 상태인 재방문에서는 페이지가
    // 제멋대로 튀면 안 되므로 초기화가 끝난 뒤부터만 동작시킨다.
    if (!resultShown && initialised) {
      var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      try { resultEl.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" }); } catch (e) {}
    }
    resultShown = true;
  }

  function gradeCard(card, pickedI, restoring) {
    if (card.getAttribute("data-done")) return;
    var answerI = parseInt(card.getAttribute("data-answer"), 10);
    if (isNaN(answerI) || isNaN(pickedI)) return;
    var isRight = pickedI === answerI;
    card.setAttribute("data-done", "1");
    card.setAttribute("data-correct", isRight ? "1" : "0");
    var choices = card.querySelectorAll(".qz-choice");
    for (var i = 0; i < choices.length; i++) {
      var ci = parseInt(choices[i].getAttribute("data-i"), 10);
      choices[i].disabled = true;
      var state = choices[i].querySelector(".qz-state");
      if (ci === answerI) {
        choices[i].classList.add("is-correct");
        if (state) state.textContent = "✓ 정답";
      }
      if (ci === pickedI) {
        choices[i].classList.add("is-picked");
        if (!isRight) {
          choices[i].classList.add("is-wrong");
          if (state) state.textContent = "✗ 내 선택";
        }
      }
    }
    var fb = card.querySelector(".qz-fb");
    var verdict = card.querySelector(".qz-verdict");
    if (verdict) {
      verdict.classList.add(isRight ? "is-correct" : "is-wrong");
      verdict.textContent = isRight ? "🦉 정답이에요!" : "🦉 아쉬워요 — 해설로 근거를 확인해 보세요.";
    }
    if (fb) fb.hidden = false;
    if (!restoring && card.id) { picks[card.id] = pickedI; savePicks(); }
    updateScore();
  }

  // 재도전 — 화면 상태만 되돌린다. 구름이 적립은 growth.js 가 주×문항으로 dedup 하므로
  // 다시 풀어도 점수가 늘지 않는다(반복 적립 방지 장치는 그대로 두는 것이 맞다).
  function resetCard(card) {
    card.removeAttribute("data-done");
    card.removeAttribute("data-correct");
    var choices = card.querySelectorAll(".qz-choice");
    for (var i = 0; i < choices.length; i++) {
      choices[i].disabled = false;
      choices[i].classList.remove("is-correct", "is-wrong", "is-picked");
      var state = choices[i].querySelector(".qz-state");
      if (state) state.textContent = "";
    }
    var fb = card.querySelector(".qz-fb");
    if (fb) fb.hidden = true;
    var verdict = card.querySelector(".qz-verdict");
    if (verdict) { verdict.classList.remove("is-correct", "is-wrong"); verdict.textContent = ""; }
    if (card.id && Object.prototype.hasOwnProperty.call(picks, card.id)) delete picks[card.id];
  }

  cards.forEach(function (card) {
    var choices = card.querySelectorAll(".qz-choice");
    for (var i = 0; i < choices.length; i++) {
      choices[i].addEventListener("click", function () {
        gradeCard(card, parseInt(this.getAttribute("data-i"), 10));
      });
    }
  });

  if (toggle) {
    toggle.addEventListener("click", function () {
      mode = (mode === "weekly") ? "all" : "weekly";
      if (mode === "weekly") filter = "all";
      applyMode();
    });
  }

  filterBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      filter = btn.getAttribute("data-filter") || "all";
      applyMode();
    });
  });

  if (retryBtn) {
    retryBtn.addEventListener("click", function () {
      var wrong = weeklyStats().wrong;
      if (!wrong.length) return;
      wrong.forEach(resetCard);
      savePicks();
      resultShown = false;
      updateScore();
      try { wrong[0].scrollIntoView({ block: "center" }); } catch (e) {}
      var first = wrong[0].querySelector(".qz-choice");
      if (first) first.focus();
    });
  }

  if (shareBtn) {
    shareBtn.addEventListener("click", function () {
      var s = weeklyStats();
      var text = (hasPinnedSet ? "이번 주 GRM 규제 퀴즈 " : "GRM 규제 퀴즈 복습 ") +
                 s.right + "/" + s.total + " 🦉\n" +
                 window.location.origin + window.location.pathname;
      function done() {
        shareBtn.textContent = "복사했어요";
        window.setTimeout(function () { shareBtn.textContent = "결과 복사"; }, 2000);
      }
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "readonly");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* 복사 불가 환경 */ }
        document.body.removeChild(ta);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else {
        fallback();
      }
    });
  }

  // 해시 딥링크(#q-0xx)로 들어온 문항이 이번 주 세트 밖이면 전체 보기로 전환해 노출.
  if (window.location.hash) {
    var target = document.getElementById(window.location.hash.slice(1));
    if (target && target.classList.contains("qz-card") && !weeklySet[target.getAttribute("data-index")]) {
      mode = "all";
    }
  }

  // 저장된 선택 복원 — 직접 호출이라 클릭 이벤트가 나지 않는다(growth.js 는 클릭을
  // 관찰하므로 중복 적립 없음. 원래 클릭 때 이미 적립됐다).
  cards.forEach(function (card) {
    if (card.id && Object.prototype.hasOwnProperty.call(picks, card.id)) {
      gradeCard(card, picks[card.id], true);
    }
  });

  applyMode();
  updateScore();
  initialised = true;
})();
