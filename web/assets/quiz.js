/* [주간 퀴즈] 클라이언트 로직 — 정적·무의존(vanilla). 서버가 embed 한 전 문항 중
 * "이번 주" 세트를 ISO 주차 키로 결정론 선택하고(같은 주 = 전 직원 동일 세트), 선택 즉시
 * 채점해 정답·해설·근거 링크를 보여준다. 결정론 렌더(골든) 불침범: 이 스크립트는 런타임에
 * 클래스/hidden/텍스트만 토글하며 문항·정답·해설·링크 콘텐츠를 만들지 않는다(전부 서버가
 * 렌더한 DOM 값). JS 미로드 시 전 문항이 그대로 보이고 근거 링크·해시 딥링크 무영향
 * (progressive enhancement). 랭킹·서버 저장 없음(성장 적립은 별도 growth.js 레이어 소관).
 * [9차 G3] week(YYYYWW) 필드: 뱅크 항목에 week 가 있으면 해당 주차 문항을 "이번 주"로
 * 우선 선정하고 부족분만 기존 회전으로 보충한다(월 13:00 자동 생성 파이프라인 계약).
 * week 가 하나도 없으면(현 데이터) 기존 회전과 완전 동일 경로 — node 테스트가 두 경로 고정. */
(function () {
  "use strict";
  function mod(n, m) { return ((n % m) + m) % m; }

  // [9차 G3] 주간 선택 — 순수 함수(DOM 무접촉·테스트 대상). items = [{index(뱅크순),
  // difficulty("easy"|"normal"), week("YYYYWW"|"")}]. 반환 = 선정 문항 index 오름차순.
  // 규칙: ① week===String(seed) 문항을 뱅크 순으로 최대 count 개 우선(초과분은 잘림 —
  // 지정분 난이도 구성은 생성 파이프라인 책임) ② 부족분은 나머지 문항에서 기존 회전
  // (normal 1~2 제한·easy 과반·seed 기반 결정론)으로 보충 ③ week 미보유 뱅크(현 데이터)는
  // ①이 공집합이라 기존 회전과 산식·결과 동일(무회귀).
  function pickWeeklyIndexes(items, weeklyCount, seed) {
    var count = Math.min(weeklyCount, items.length);
    var wk = String(seed);
    var pinned = items.filter(function (it) { return it.week === wk; }).slice(0, count);
    var inPinned = {};
    pinned.forEach(function (it) { inPinned[it.index] = true; });
    var chosen = pinned.slice();
    var need = count - pinned.length;
    if (need > 0) {
      var poolE = items.filter(function (it) { return it.difficulty === "easy" && !inPinned[it.index]; });
      var poolN = items.filter(function (it) { return it.difficulty !== "easy" && !inPinned[it.index]; });
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

  var weeklySet = {};
  pickWeeklyIndexes(items, weeklyCount, isoWeekSeed(new Date())).forEach(function (i) { weeklySet[i] = true; });
  var weekly = cards.filter(function (c) { return !!weeklySet[c.getAttribute("data-index")]; });
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
