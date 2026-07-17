/* [주간 퀴즈] 클라이언트 로직 — 정적·무의존(vanilla). 서버가 embed 한 전 문항 중
 * "이번 주" 세트를 ISO 주차 키로 결정론 선택하고(같은 주 = 전 직원 동일 세트), 선택 즉시
 * 채점해 정답·해설·근거 링크를 보여준다. 결정론 렌더(골든) 불침범: 이 스크립트는 런타임에
 * 클래스/hidden/텍스트만 토글하며 문항·정답·해설·링크 콘텐츠를 만들지 않는다(전부 서버가
 * 렌더한 DOM 값). JS 미로드 시 전 문항이 그대로 보이고 근거 링크·해시 딥링크 무영향
 * (progressive enhancement). v1 비목표: 랭킹·참여기록·서버 저장 없음 — 점수는 화면 표시만
 * 이며 어떤 저장소에도 남기지 않는다(now()/난수는 주차 회전에만 쓰고 서버로 보내지 않음). */
(function () {
  "use strict";
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

  function mod(n, m) { return ((n % m) + m) % m; }

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

  // 난이도별 분할(DOM 순 = 정본 뱅크 순 유지). 주차 회전이 easy 과반·normal 1~2 를 맞춘다.
  var easy = cards.filter(function (c) { return c.getAttribute("data-difficulty") === "easy"; });
  var normal = cards.filter(function (c) { return c.getAttribute("data-difficulty") !== "easy"; });

  function pickWeekly(seed) {
    var count = Math.min(weeklyCount, cards.length);
    // normal 은 1~2문항으로 제한(운영설계 §2.3), 나머지는 easy(과반 자동 충족).
    var normalCount = Math.min(count >= 5 ? 2 : 1, normal.length, count);
    var easyCount = Math.min(count - normalCount, easy.length);
    // easy 가 부족하면 normal 로 보충(뱅크가 작아지는 방어적 경로 — v1 데이터엔 미발생).
    normalCount = Math.min(count - easyCount, normal.length);
    var chosen = [];
    var i, baseE = mod(seed * 3, Math.max(easy.length, 1));
    for (i = 0; i < easyCount; i++) chosen.push(easy[mod(baseE + i, easy.length)]);
    var baseN = mod(seed, Math.max(normal.length, 1));
    for (i = 0; i < normalCount; i++) chosen.push(normal[mod(baseN + i, normal.length)]);
    // 읽기 순서 안정화 — 정본 뱅크 순(data-index)으로 정렬.
    chosen.sort(function (a, b) {
      return parseInt(a.getAttribute("data-index"), 10) - parseInt(b.getAttribute("data-index"), 10);
    });
    return chosen;
  }

  var weekly = pickWeekly(isoWeekSeed(new Date()));
  var weeklySet = {};
  weekly.forEach(function (c) { weeklySet[c.getAttribute("data-index")] = true; });
  var mode = "weekly";

  function applyMode() {
    for (var i = 0; i < cards.length; i++) {
      var inWeek = !!weeklySet[cards[i].getAttribute("data-index")];
      cards[i].hidden = (mode === "weekly") && !inWeek;
    }
    if (mode === "weekly") {
      if (titleEl) titleEl.textContent = "이번 주 퀴즈";
      if (subEl) subEl.textContent = "이번 주에 뽑은 " + weekly.length + "문항이에요. 같은 주에는 모두 같은 문제를 풀어요.";
      if (toggle) { toggle.textContent = "전체 " + cards.length + "문항 풀기"; toggle.setAttribute("aria-pressed", "false"); }
    } else {
      if (titleEl) titleEl.textContent = "전체 문항";
      if (subEl) subEl.textContent = "정본 문항 " + cards.length + "개를 모두 볼 수 있어요.";
      if (toggle) { toggle.textContent = "이번 주 문항만 보기"; toggle.setAttribute("aria-pressed", "true"); }
    }
  }

  var answered = 0, correct = 0;
  function updateScore() {
    if (correctEl) correctEl.textContent = String(correct);
    if (answeredEl) answeredEl.textContent = String(answered);
  }

  function gradeCard(card, pickedI) {
    if (card.getAttribute("data-done")) return;      // 재시도·감점 없음(§2.2) — 1회 확정.
    card.setAttribute("data-done", "1");
    var answerI = parseInt(card.getAttribute("data-answer"), 10);
    var isRight = pickedI === answerI;
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
    answered++;
    if (isRight) correct++;
    updateScore();
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
      applyMode();
    });
  }

  // 해시 딥링크(#q-0xx)로 들어온 문항이 이번 주 세트 밖이면 전체 보기로 전환해 노출.
  if (window.location.hash) {
    var target = document.getElementById(window.location.hash.slice(1));
    if (target && target.classList.contains("qz-card") && !weeklySet[target.getAttribute("data-index")]) {
      mode = "all";
    }
  }

  applyMode();
  updateScore();
})();
