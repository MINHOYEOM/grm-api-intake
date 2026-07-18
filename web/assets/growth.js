/* [구름이 성장 시스템 v1] /quiz/ 전용 클라이언트 레이어 — 듀오링고식 성장·스트릭.
 * 저장: localStorage 단독(스키마 version:1 — 10차 서버 동기화 대비 버전 필드 예약).
 * 서버 전송 0(네트워크 API 일절 미사용 — 테스트가 금지 문자열로 가드), 로그인·랭킹 없음.
 * quiz.js 무수정 통합: 채점·주차 회전 계약 불변 — 이 파일은 .qz-choice 클릭을 문서 위임으로
 * "관찰"만 하고(data-answer 대조), 자체 주×문항 dedup 으로 새로고침 재풀이 중복 적립을 막는다.
 * progressive enhancement: JS 미로드 시 자리표시자(hidden)가 그대로 숨어 퀴즈 기본 동작 무영향.
 * 접근성: 단계 SVG 는 장식(aria-hidden), 수치·단계는 전부 텍스트 병행, 갱신은 aria-live,
 * prefers-reduced-motion 존중(펄스 연출 생략). 결정론 렌더(골든) 불침범 — 마크업은 전부
 * 런타임 주입(자리표시자 1줄만 서버 렌더). */
(function () {
  "use strict";
  var host = document.getElementById("grm-growth");
  if (!host || !window.localStorage) return;

  var KEY = "grm-gurumi-growth";
  var SCHEMA_VERSION = 1;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── 저장(version:1) — {version, weeks:{"<YYYYWW>":{idx:<절대주번호>, q:{"<qid>":0|1}}}} ──
  function load() {
    try {
      var d = JSON.parse(localStorage.getItem(KEY));
      if (d && d.version === SCHEMA_VERSION && d.weeks && typeof d.weeks === "object") return d;
    } catch (e) { /* 손상 데이터 → 초기화 */ }
    return { version: SCHEMA_VERSION, weeks: {} };
  }
  function save(d) { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch (e) { /* 저장 불가 무시 */ } }
  var data = load();

  // ── ISO 8601 주차 — quiz.js isoWeekSeed 와 동일 산식(키 문자열) + 절대 주 번호(연속성 계산용) ──
  function weekInfo(now) {
    var d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    var dayNum = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - dayNum + 3);        // 해당 주의 목요일
    var firstThu = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
    var fdn = (firstThu.getUTCDay() + 6) % 7;
    firstThu.setUTCDate(firstThu.getUTCDate() - fdn + 3);
    var week = 1 + Math.round((d - firstThu) / (7 * 24 * 3600 * 1000));
    return { key: String(d.getUTCFullYear() * 100 + week),
             idx: Math.floor(d.getTime() / (7 * 24 * 3600 * 1000)) };
  }

  // ── 파생 통계(저장값은 사실만, 점수·단계는 항상 재계산 — 드리프트 0) ──
  // 포인트 = 정답×10 + 참여 주×20 + 스트릭 보너스(연속 2주부터 주당 +10, 상한 +50).
  function derive(curIdx) {
    var totalA = 0, totalC = 0, present = {}, weeks = 0;
    for (var k in data.weeks) {
      if (!Object.prototype.hasOwnProperty.call(data.weeks, k)) continue;
      var w = data.weeks[k], q = w.q || {};
      weeks++;
      present[w.idx] = 1;
      for (var id in q) {
        if (!Object.prototype.hasOwnProperty.call(q, id)) continue;
        totalA++;
        if (q[id]) totalC++;
      }
    }
    // 스트릭 = 현재 주(참여 전이면 직전 주)에서 끝나는 연속 참여 구간 길이.
    var anchor = present[curIdx] ? curIdx : (present[curIdx - 1] ? curIdx - 1 : null);
    var streak = 0;
    if (anchor !== null) { streak = 1; while (present[anchor - streak]) streak++; }
    var points = totalC * 10 + weeks * 20 + Math.min(Math.max(streak - 1, 0), 5) * 10;
    return { answered: totalA, correct: totalC, weeks: weeks, streak: streak, points: points };
  }

  // ── 성장 단계(v1 확정안 — 대안은 PR/보고 기록) ──
  var STAGES = [
    { min: 0,   name: "알",        sub: "퀴즈를 풀면 부화가 시작돼요" },
    { min: 50,  name: "아기 구름이", sub: "방금 알을 깨고 나왔어요" },
    { min: 150, name: "소년 구름이", sub: "매주 소식을 먹고 자라는 중이에요" },
    { min: 350, name: "어른 구름이", sub: "규제 소식이라면 척척 정리해요" },
    { min: 700, name: "전설 구름이", sub: "규제의 하늘을 지키는 전설이에요" }
  ];
  function stageIndex(points) {
    var i = 0;
    for (var s = 0; s < STAGES.length; s++) if (points >= STAGES[s].min) i = s;
    return i;
  }

  // ── 단계별 SVG(장식 — 부엉이 마스코트 팔레트 공유: 코럴 그라데이션·크림·골드) ──
  var OWL_DEFS = '<defs><linearGradient id="ggrad" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0" stop-color="#CE6E4C"/><stop offset="1" stop-color="#B9542F"/></linearGradient></defs>';
  function owlEyes(cx1, cx2, cy, r) {
    return '<circle cx="' + cx1 + '" cy="' + cy + '" r="' + r + '" fill="#FAF6EE"/>' +
      '<circle cx="' + cx2 + '" cy="' + cy + '" r="' + r + '" fill="#FAF6EE"/>' +
      '<circle cx="' + cx1 + '" cy="' + (cy + 1) + '" r="' + (r * 0.42) + '" fill="#22303F"/>' +
      '<circle cx="' + cx2 + '" cy="' + (cy + 1) + '" r="' + (r * 0.42) + '" fill="#22303F"/>';
  }
  var STAGE_SVGS = [
    // 0 알 — 크림 알 + 코럴 반점, 잔금.
    '<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg">' + OWL_DEFS +
      '<ellipse cx="48" cy="82" rx="26" ry="6" fill="#1A1815" opacity=".07"/>' +
      '<path d="M48 14 C66 14 76 36 76 54 C76 72 64 84 48 84 C32 84 20 72 20 54 C20 36 30 14 48 14 Z" fill="#FAF6EE" stroke="#DCD3C7" stroke-width="1.5"/>' +
      '<circle cx="38" cy="42" r="3.4" fill="#CE6E4C" opacity=".5"/><circle cx="58" cy="56" r="2.6" fill="#CE6E4C" opacity=".45"/><circle cx="46" cy="66" r="3" fill="#CE6E4C" opacity=".4"/>' +
      '<path d="M42 26 l5 6 -4 5" fill="none" stroke="#DCD3C7" stroke-width="1.6" stroke-linecap="round"/></svg>',
    // 1 아기 — 동글동글 병아리형 부엉이(귀깃 없음·아기 솜털).
    '<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg">' + OWL_DEFS +
      '<ellipse cx="48" cy="84" rx="24" ry="5" fill="#1A1815" opacity=".07"/>' +
      '<circle cx="48" cy="54" r="30" fill="url(#ggrad)"/>' +
      '<path d="M40 22 q8 -8 16 0" fill="none" stroke="#B9542F" stroke-width="2.4" stroke-linecap="round"/>' +
      '<ellipse cx="48" cy="64" rx="17" ry="14" fill="#F4E7DF" opacity=".9"/>' +
      owlEyes(38, 58, 48, 10) +
      '<path d="M44 58 L52 58 L48 65 Z" fill="#E8B04A"/></svg>',
    // 2 소년 — 짧은 귀깃 + 작은 날개.
    '<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg">' + OWL_DEFS +
      '<ellipse cx="48" cy="86" rx="26" ry="5" fill="#1A1815" opacity=".07"/>' +
      '<path d="M30 26 L42 24 L34 12 Z" fill="#CE6E4C"/><path d="M66 26 L54 24 L62 12 Z" fill="#CE6E4C"/>' +
      '<rect x="20" y="20" width="56" height="66" rx="26" fill="url(#ggrad)"/>' +
      '<path d="M74 44 q10 5 7 20 q-2 9 -9 11 q4 -15 2 -31 Z" fill="#B9542F"/>' +
      '<path d="M22 44 q-10 5 -7 20 q2 9 9 11 q-4 -15 -2 -31 Z" fill="#B9542F"/>' +
      '<ellipse cx="48" cy="66" rx="16" ry="13" fill="#F4E7DF" opacity=".88"/>' +
      owlEyes(38, 58, 44, 10.5) +
      '<path d="M44 55 L52 55 L48 62 Z" fill="#E8B04A"/></svg>',
    // 3 어른 — base.html 마스코트와 동형 실루엣(귀깃·날개·배).
    '<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg">' + OWL_DEFS +
      '<ellipse cx="48" cy="88" rx="28" ry="5" fill="#1A1815" opacity=".07"/>' +
      '<path d="M26 24 L44 24 L31 6 Z" fill="#CE6E4C"/><path d="M70 24 L52 24 L65 6 Z" fill="#CE6E4C"/>' +
      '<rect x="16" y="17" width="64" height="71" rx="27" fill="url(#ggrad)"/>' +
      '<path d="M78 40 q13 5 10 24 q-2 11 -11 13 q5 -18 1 -37 Z" fill="#B9542F"/>' +
      '<path d="M18 40 q-13 5 -10 24 q2 11 11 13 q-5 -18 -1 -37 Z" fill="#B9542F"/>' +
      '<ellipse cx="48" cy="68" rx="19" ry="16" fill="#F4E7DF" opacity=".88"/>' +
      owlEyes(37, 59, 44, 12) +
      '<path d="M43 56 L53 56 L48 64 Z" fill="#E8B04A"/></svg>',
    // 4 전설 — 어른 + 골드 왕관·반짝임.
    '<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg">' + OWL_DEFS +
      '<ellipse cx="48" cy="88" rx="28" ry="5" fill="#1A1815" opacity=".07"/>' +
      '<path d="M26 26 L44 26 L31 9 Z" fill="#CE6E4C"/><path d="M70 26 L52 26 L65 9 Z" fill="#CE6E4C"/>' +
      '<rect x="16" y="19" width="64" height="69" rx="27" fill="url(#ggrad)"/>' +
      '<path d="M78 42 q13 5 10 23 q-2 11 -11 13 q5 -17 1 -36 Z" fill="#B9542F"/>' +
      '<path d="M18 42 q-13 5 -10 23 q2 11 11 13 q-5 -17 -1 -36 Z" fill="#B9542F"/>' +
      '<ellipse cx="48" cy="68" rx="19" ry="15" fill="#F4E7DF" opacity=".88"/>' +
      owlEyes(37, 59, 46, 11.5) +
      '<path d="M43 58 L53 58 L48 66 Z" fill="#E8B04A"/>' +
      '<path d="M36 16 L40 8 L45 14 L48 5 L51 14 L56 8 L60 16 Z" fill="#E8B04A" stroke="#B07D17" stroke-width="1"/>' +
      '<path d="M14 30 l2.4 4.8 4.8 2.4 -4.8 2.4 -2.4 4.8 -2.4 -4.8 -4.8 -2.4 4.8 -2.4 Z" fill="#E8B04A" opacity=".85"/>' +
      '<path d="M82 22 l1.8 3.6 3.6 1.8 -3.6 1.8 -1.8 3.6 -1.8 -3.6 -3.6 -1.8 3.6 -1.8 Z" fill="#E8B04A" opacity=".8"/></svg>'
  ];

  // ── 적립(주×문항 dedup — 새로고침 재풀이·재클릭 중복 적립 방지) ──
  function record(qid, isRight) {
    var wk = weekInfo(new Date());
    var w = data.weeks[wk.key];
    if (!w) { w = data.weeks[wk.key] = { idx: wk.idx, q: {} }; }
    if (Object.prototype.hasOwnProperty.call(w.q, qid)) return false;
    w.q[qid] = isRight ? 1 : 0;
    save(data);
    return true;
  }

  // ── 패널 마크업(정적 문자열만 — 사용자·외부 텍스트 주입 0) ──
  host.innerHTML =
    '<div class="qzg">' +
    '  <span class="qzg-art" id="grm-qzg-art" aria-hidden="true"></span>' +
    '  <div class="qzg-main">' +
    '    <div class="qzg-head">' +
    '      <b class="qzg-name" id="grm-qzg-name"></b>' +
    '      <span class="qzg-lv" id="grm-qzg-lv"></span>' +
    '      <span class="qzg-streak" id="grm-qzg-streak"></span>' +
    '    </div>' +
    '    <p class="qzg-sub" id="grm-qzg-sub"></p>' +
    '    <div class="qzg-barwrap"><div class="qzg-bar" id="grm-qzg-bar"></div></div>' +
    '    <p class="qzg-meta"><span id="grm-qzg-pts"></span> · <span id="grm-qzg-stat"></span>' +
    '      <span class="qzg-note">기록은 이 브라우저에만 저장돼요</span>' +
    '      <button class="qzg-reset" id="grm-qzg-reset" type="button">기록 초기화</button></p>' +
    '  </div>' +
    '</div>';
  host.hidden = false;
  host.setAttribute("aria-live", "polite");

  var artEl = document.getElementById("grm-qzg-art");
  var nameEl = document.getElementById("grm-qzg-name");
  var lvEl = document.getElementById("grm-qzg-lv");
  var streakEl = document.getElementById("grm-qzg-streak");
  var subEl = document.getElementById("grm-qzg-sub");
  var barEl = document.getElementById("grm-qzg-bar");
  var ptsEl = document.getElementById("grm-qzg-pts");
  var statEl = document.getElementById("grm-qzg-stat");
  var curStage = -1;

  function refresh(animate) {
    var s = derive(weekInfo(new Date()).idx);
    var si = stageIndex(s.points);
    if (si !== curStage) {
      curStage = si;
      artEl.innerHTML = STAGE_SVGS[si];
    }
    nameEl.textContent = STAGES[si].name;
    lvEl.textContent = "Lv." + (si + 1) + "/" + STAGES.length;
    subEl.textContent = s.answered ? STAGES[si].sub : "이번 주 퀴즈를 풀면 구름이가 자라나요";
    streakEl.textContent = s.streak >= 2 ? "연속 참여 " + s.streak + "주" : "";
    streakEl.hidden = s.streak < 2;
    var next = STAGES[si + 1];
    var pct = next ? Math.max(0, Math.min(100, Math.round((s.points - STAGES[si].min) * 100 / (next.min - STAGES[si].min)))) : 100;
    barEl.style.width = pct + "%";
    ptsEl.textContent = next ? ("성장 포인트 " + s.points + " (다음 단계까지 " + (next.min - s.points) + ")") : ("성장 포인트 " + s.points + " · 최고 단계");
    statEl.textContent = "누적 정답 " + s.correct + " · 푼 문제 " + s.answered + " · 참여 " + s.weeks + "주";
    if (animate && !reduce) {
      artEl.classList.remove("is-pulse");
      void artEl.offsetWidth;
      artEl.classList.add("is-pulse");
    }
  }

  // 채점 관찰 — quiz.js 의 버튼 직접 리스너가 먼저 실행(타깃 단계)된 뒤 문서로 버블된
  // 같은 클릭을 여기서 수신한다(비활성 버튼은 새 클릭 자체가 발생하지 않아 중복 없음).
  document.addEventListener("click", function (e) {
    var t = e.target;
    var btn = t && t.closest ? t.closest(".qz-choice") : null;
    if (!btn) return;
    var card = btn.closest(".qz-card");
    if (!card || !card.id) return;
    var ans = parseInt(card.getAttribute("data-answer"), 10);
    var picked = parseInt(btn.getAttribute("data-i"), 10);
    if (isNaN(ans) || isNaN(picked)) return;
    if (record(card.id, picked === ans)) refresh(true);
  });

  var resetBtn = document.getElementById("grm-qzg-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      if (!window.confirm("구름이 성장 기록을 모두 지울까요? (이 브라우저에서만 지워져요)")) return;
      data = { version: SCHEMA_VERSION, weeks: {} };
      save(data);
      curStage = -1;
      refresh(false);
    });
  }

  refresh(false);
})();
