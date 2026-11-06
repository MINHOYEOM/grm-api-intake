/* 운영 센터 홈 렌더러 (PR3/7, 설계서 2026-11-06 §3) — 사무실 층 배치도.
 *
 * 입력은 public.ops_home() 의 JSON 한 덩어리뿐이다. 상태(정상·주의·문제·측정 필요), 놀고 있음,
 * 활동 기록 없음, 낡음, 마감 지남은 전부 DB 가 계산해서 준다 — 여기서는 그 필드를 글자·색으로
 * 옮겨 그리기만 한다(경계값·일수 계산을 다시 하지 않는다. 같은 규칙이 두 벌이 되면 갈라진다).
 *
 * 순수 함수: render(data, ctx) → HTML 문자열. DOM·네트워크를 만지지 않아 Node 로 시험한다
 * (tests/js/ops_home_render.js). 화면 동작(자리 펼치기)은 bind(host) 한 곳.
 * 밀도(11-06 대표 피드백 "빽빽하다"): 정상 팀은 한 줄로 접고, 목표 막대는 팀 머리의 가장 나쁜 목표 하나만,
 * 자리는 작은 격자에 한 마디 상태만 — 나머지는 누르면 펼친다.
 * 읽기 전용: 승인·보류 버튼은 PR4 에서 붙인다(지금은 자리도 만들지 않는다).
 * 운영자 전용 한국어 화면 — grm_i18n.EXCLUDED_ASSETS 대상(영문판에 실리지 않는다).
 */
(function (root) {
  "use strict";

  var STATE_WORD = { ok: "정상", warn: "주의", bad: "문제", unknown: "측정 필요" };
  var UNKNOWN_WHY = { no_target: "목표 미정", no_value: "값 없음", stale: "낡음", no_goals: "목표 없음", no_children: "팀 없음" };
  var KIND_WORD = { routine: "정기 작업", session: "세션", code: "코드" };
  var LANE_WORD = { inbox: "지시함", doing: "진행", review: "검사 중", done: "완료" };
  var MEASURE_WORD = { sql: "자동(DB)", github: "자동(GitHub)", manual: "손으로 기록" };
  var WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c];
    });
  }
  function safeUrl(url) {
    return /^https?:\/\//i.test(String(url || "")) ? String(url) : "";
  }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function num(v) { return typeof v === "number" ? v : (v == null || v === "" ? null : Number(v)); }
  function fmtNum(v) {
    var n = num(v);
    if (n == null || !isFinite(n)) return "—";
    return (Math.round(n * 10) / 10).toLocaleString("ko-KR");
  }
  function stateKey(s) { return STATE_WORD[s] ? s : "unknown"; }
  // 날짜 표시만 KST 로 맞춘다(판정은 DB). "2026-11-09" 또는 ISO 시각 → "11-09(월)"
  function dayLabel(value) {
    if (!value) return "";
    var s = String(value), y, m, d;
    var plain = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (plain) { y = +plain[1]; m = +plain[2]; d = +plain[3]; }
    else {
      var t = Date.parse(s);
      if (isNaN(t)) return s.slice(0, 10);
      var k = new Date(t + 9 * 3600 * 1000);
      y = k.getUTCFullYear(); m = k.getUTCMonth() + 1; d = k.getUTCDate();
    }
    var wd = WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
    return (m < 10 ? "0" : "") + m + "-" + (d < 10 ? "0" : "") + d + "(" + wd + ")";
  }
  function clock(generatedAt) {
    var m = /T(\d{2}:\d{2})/.exec(String(generatedAt || ""));
    return m ? m[1] : "";
  }
  function agoDays(days) {
    if (days == null) return "";
    if (days <= 0) return "오늘";
    if (days === 1) return "어제";
    return days + "일 전";
  }
  function dot(state) {
    var k = stateKey(state);
    return '<span class="oc-state oc-s-' + k + '"><i class="oc-dot" aria-hidden="true"></i>' + STATE_WORD[k] + "</span>";
  }

  // ── 자리(직원) 상태 — ops_home 필드를 한 마디로 옮길 뿐 ──────────────────────
  // 순서: 쉬는 중(state=paused) > 막힘(맡은 일 중 overdue) > 놀고 있음(idle) > 검사 대기(review)
  //       > 일하는 중(open_tasks>0) > 기록 없음(no_record·기록 null) > 일 없음
  // word = 자리에 보이는 한 마디, line = 펼쳤을 때 보이는 설명.
  function seatStatus(m, tasks) {
    var overdue = tasks.filter(function (t) { return t.overdue; }).length;
    var review = tasks.filter(function (t) { return t.lane === "review"; }).length;
    var open = num(m.open_tasks) || 0;
    var days = m.last_active_days == null ? null : num(m.last_active_days);
    if (m.state === "paused") return { k: "off", word: "쉬는 중", line: "일시 정지된 자리" };
    if (overdue) return { k: "bad", word: "막힘", line: "마감 지난 일 " + overdue + "건" };
    if (m.idle) return { k: "warn", word: "놀고 있음", line: (days == null ? "" : days + "일째 ") + "맡은 일 없음" };
    if (review) return { k: "warn", word: "검사 대기", line: "검사를 기다리는 일 " + review + "건" };
    if (open > 0) return { k: "work", word: "일하는 중", line: "맡은 일 " + open + "건" };
    if (m.no_record) return { k: "none", word: "기록 없음", line: "팀 목표가 밀려 있는데 활동 기록이 없음" };
    if (days == null) return { k: "none", word: "기록 없음", line: "아직 활동 기록이 없음" };
    return { k: "free", word: "일 없음", line: "마지막 활동 " + agoDays(days) };
  }

  function goalValueText(g) {
    var unit = g.unit || "";
    if (num(g.target) == null) return (num(g.current) == null ? "값 없음" : fmtNum(g.current) + unit) + " · 목표 미정";
    var cur = num(g.current) == null ? "—" : fmtNum(g.current) + unit;
    var tgt = fmtNum(g.target) + unit + (g.direction === "lte" ? " 이하" : "");
    return cur + " / 목표 " + tgt;
  }
  function goalWhen(g) {
    if (g.stale) return (g.measured_days == null ? "" : g.measured_days + "일 전 값") + " · 낡음";
    if (g.measured_days == null) return "";
    return g.measured_days > 0 ? g.measured_days + "일 전 측정" : "";
  }
  // 막대: 길이·목표선 위치만 계산(상태는 g.state). 작을수록 좋은 목표는 한도 대비 사용량.
  function goalBar(g, thin) {
    var st = stateKey(g.state), cur = num(g.current), tgt = num(g.target);
    var cls = "oc-bar oc-b-" + st + (g.stale ? " is-stale" : "") + (thin ? " is-thin" : "");
    if (cur == null || tgt == null) return '<span class="' + cls + ' is-empty" aria-hidden="true"></span>';
    var top = g.direction === "lte"
      ? Math.max(num(g.bad_at) || 0, num(g.warn_at) || 0, tgt, cur, 1)
      : Math.max(tgt, cur, 1e-9);
    var w = Math.max(0, Math.min(100, (cur / top) * 100));
    var line = Math.max(0, Math.min(100, (tgt / top) * 100));
    return '<span class="' + cls + '" aria-hidden="true"><b style="width:' + w.toFixed(1) + '%"></b>' +
      '<i style="left:' + line.toFixed(1) + '%"></i></span>';
  }
  function goalRow(g) {
    var st = stateKey(g.state), when = goalWhen(g);
    var why = st === "unknown" && g.unknown_reason ? UNKNOWN_WHY[g.unknown_reason] || "" : "";
    return '<li class="oc-goal oc-g-' + st + '">' +
      '<div class="oc-goal-top"><span class="oc-goal-label">' + esc(g.label) + "</span>" +
      (st === "ok" ? "" : '<span class="oc-goal-st">' + STATE_WORD[st] + (why && why !== "낡음" ? " · " + why : "") + "</span>") + "</div>" +
      goalBar(g) +
      '<div class="oc-goal-val">' + esc(goalValueText(g)) + (when ? " · " + esc(when) : "") +
      (g.measure_kind ? " · " + esc(MEASURE_WORD[g.measure_kind] || g.measure_kind) : "") + "</div>" +
      (g.note ? '<div class="oc-goal-note">' + esc(g.note) + "</div>" : "") + "</li>";
  }
  function goalList(goals) {
    return goals.length ? '<ul class="oc-goals" aria-label="팀 목표">' + goals.map(goalRow).join("") + "</ul>" : "";
  }

  function tasksByMember(flow) {
    var map = {}, add = function (t) { if (!t || !t.member) return; (map[t.member] = map[t.member] || []).push(t); };
    arr(flow && flow.todo).forEach(add);
    arr(flow && flow.doing).forEach(add);
    return map;
  }
  function doneByMember(flow) {
    var map = {};
    arr(flow && flow.done).forEach(function (t) { if (t && t.member) (map[t.member] = map[t.member] || []).push(t); });
    return map;
  }

  function seat(m, teamSlug, tasks, idx) {
    var s = seatStatus(m, tasks), id = "oc-seat-" + esc(teamSlug) + "-" + idx;
    var name = String(m.name || m.slug || "?");
    return '<li class="oc-seat oc-k-' + s.k + '">' +
      '<button type="button" class="oc-seat-btn" data-oc-seat="' + id + '" aria-expanded="false" aria-controls="' + id + '" title="' + esc(name + " · " + s.word) + '">' +
      '<span class="oc-avatar" aria-hidden="true">' + esc(name.charAt(0)) + "</span>" +
      '<span class="oc-seat-text"><b>' + esc(name) + "</b>" +
      '<span class="oc-seat-word">' + esc(s.word) + "</span></span></button></li>";
  }
  function seatDetail(m, teamSlug, tasks, done, goals, idx) {
    var s = seatStatus(m, tasks);
    var id = "oc-seat-" + esc(teamSlug) + "-" + idx, days = m.last_active_days == null ? null : num(m.last_active_days);
    var meta = [KIND_WORD[m.kind] || m.kind, m.model].filter(Boolean).join(" · ");
    var taskHtml = tasks.length ? "<ul>" + tasks.map(function (t) {
      return "<li>" + esc(t.title) + " <small>" + esc(LANE_WORD[t.lane] || t.lane || "") +
        (t.due_on ? " · " + esc(dayLabel(t.due_on)) + "까지" : "") + "</small>" +
        (t.overdue ? ' <span class="oc-flag-bad">마감 지남</span>' : "") + "</li>";
    }).join("") + "</ul>" : "<p>맡은 일 없음</p>";
    var doneHtml = done.length ? "<h5>이번 주 끝낸 일</h5><ul>" + done.map(function (t) {
      var u = safeUrl(t.result_url);
      return "<li>" + (u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(t.title) + "</a>" : esc(t.title)) + "</li>";
    }).join("") + "</ul>" : "";
    var last = days == null ? "기록 없음" : agoDays(days) + (m.last_active_note ? " · " + esc(m.last_active_note) : "");
    return '<div class="oc-seat-detail oc-k-' + s.k + '" id="' + id + '" role="region" aria-label="' + esc(m.name) + ' 상세">' +
      '<div class="oc-sd-head"><b>' + esc(m.name) + '</b><span class="oc-seat-word">' + esc(s.word) + "</span>" +
      '<button type="button" class="oc-sd-close" data-oc-close="' + id + '">닫기</button></div>' +
      '<p class="oc-muted">' + esc(s.line) + (meta ? " · " + esc(meta) : "") + "</p>" +
      "<h5>지금 하는 일</h5>" + taskHtml + doneHtml +
      "<h5>마지막 활동</h5><p>" + last + "</p>" +
      (m.idle_exempt ? '<p class="oc-muted">부를 때만 일하는 자리(놀고 있음 판정 제외)</p>' : "") +
      (goals.length ? "<h5>팀 목표</h5>" + goalList(goals) : "") +
      "</div>";
  }

  // 팀 머리 아래 가는 선 하나 = 그 팀에서 가장 나쁜 목표(DB 의 worst_goal·reason 그대로).
  function worstLine(node, team, goals) {
    var slug = team.worst_goal || node.worst_goal, reason = node.reason || team.reason || "";
    var g = slug ? goals.filter(function (x) { return x.slug === slug; })[0] : null;
    if (!g && !reason) return "";
    return '<div class="oc-worst">' + (g ? goalBar(g, true) : "") +
      '<span class="oc-worst-text">' + esc(reason || g.label) + "</span></div>";
  }

  // 팀 = <details>. 정상은 한 줄로 접히고, 알림이 있는 팀(주의·문제·측정 필요)은 펼친 채로.
  function teamBox(node, team, ctx, opts) {
    team = team || {};
    var st = stateKey(team.state || node.state), slug = node.slug;
    var members = arr(team.members), goals = arr(team.goals);
    var decisions = num(node.decisions) || 0;
    var room = !!(opts && opts.room), h = room ? "h3" : "h4";
    var seats = members.map(function (m, i) { return seat(m, slug, ctx.tasks[m.slug] || [], i); }).join("");
    var details = members.map(function (m, i) {
      return seatDetail(m, slug, ctx.tasks[m.slug] || [], ctx.done[m.slug] || [], goals, i);
    }).join("");
    return '<details class="oc-team oc-t-' + st + (room ? " oc-room oc-office" : "") + '"' + (st === "ok" ? "" : " open") +
      ' data-team="' + esc(slug) + '">' +
      '<summary class="oc-team-head"><' + h + ">" + esc(node.name || team.name || slug) + "</" + h + ">" + dot(st) +
      (decisions ? '<span class="oc-chip">결재 ' + decisions + "</span>" : "") +
      (st === "ok" ? '<span class="oc-count">' + members.length + "명</span>" : "") + "</summary>" +
      '<div class="oc-team-body">' +
      (st === "ok" ? "" : worstLine(node, team, goals)) +
      (members.length ? '<ul class="oc-seats">' + seats + "</ul>" + details : '<p class="oc-muted">자리 없음</p>') +
      (goals.length ? '<details class="oc-goals-fold"><summary>목표 ' + goals.length + "개</summary>" + goalList(goals) + "</details>" : "") +
      "</div></details>";
  }

  function wing(node, teams, ctx) {
    var st = stateKey(node.state), kids = arr(node.children);
    return '<details class="oc-room oc-wing oc-t-' + st + '"' + (st === "ok" ? "" : " open") + ' data-team="' + esc(node.slug) + '">' +
      '<summary class="oc-team-head"><h3>' + esc(node.name) + "</h3>" + dot(st) +
      (num(node.decisions) ? '<span class="oc-chip">결재 ' + num(node.decisions) + "</span>" : "") + "</summary>" +
      '<div class="oc-wing-teams">' + (kids.length ? kids.map(function (k) { return teamBox(k, teams[k.slug], ctx); }).join("") : '<p class="oc-muted">팀 없음</p>') + "</div>" +
      "</details>";
  }

  // 숫자 줄: 문제·주의·결재 세 개만 늘 보이고, 나머지는 '숫자 더 보기' 안에.
  function summaryLine(s, decisionsAll) {
    s = s || {};
    var part = function (label, n, cls) {
      n = num(n) || 0;
      return '<span class="oc-sum' + (n && cls ? " " + cls : "") + '">' + label + " <b>" + n + "</b></span>";
    };
    var main = part("문제", s.bad, "is-bad") + part("주의", s.warn, "is-warn") + part("결재", decisionsAll, "is-warn");
    var rest = part("측정 필요", s.unknown_teams) + part("놀고 있음", s.idle, "is-warn") + part("기록 없음", s.no_record) +
      part("7일 안 마감", s.deadlines) + part("마감 지남", s.overdue, "is-bad");
    return '<div class="oc-sumline">' + main + '<details class="oc-sum-more"><summary>숫자 더 보기</summary><p>' + rest + "</p></details></div>";
  }
  function headline(s, publish) {
    s = s || {};
    var bad = num(s.bad) || 0, warn = num(s.warn) || 0, dec = (num(s.decisions) || 0) + (publish ? 1 : 0);
    var a = bad ? "문제가 있는 팀이 " + bad + "곳 있습니다." : warn ? "주의할 팀이 " + warn + "곳 있습니다." : "문제나 주의할 팀은 없습니다.";
    return a + (dec ? " 결재할 서류가 " + dec + "건 있습니다." : " 결재할 서류는 없습니다.");
  }

  function decisionPaper(d, teams) {
    var team = teams[d.team] || {}, u = safeUrl(d.ref_url);
    var label = d.reason === "ask" ? "질문" : "승인 요청";
    var due = d.due_at ? (d.overdue ? '<span class="oc-flag-warn">기한 지남 ' + esc(dayLabel(d.due_at)) + "</span>" : "기한 " + esc(dayLabel(d.due_at))) : "기한 없음";
    return '<li class="oc-paper' + (d.overdue ? " is-overdue" : "") + '"><details>' +
      '<summary><span class="oc-paper-kind">' + label + "</span>" +
      '<b class="oc-paper-title">' + esc(d.title) + "</b>" +
      '<span class="oc-paper-meta">' + esc(team.name || d.team || "") + " · " + due + "</span></summary>" +
      '<div class="oc-paper-body">' +
      (d.why ? "<p><span>이유</span>" + esc(d.why) + "</p>" : "") +
      (d.if_approved ? "<p><span>승인하면</span>" + esc(d.if_approved) + "</p>" : "") +
      (d.recommendation ? "<p><span>비서실 권장</span>" + esc(d.recommendation) + "</p>" : "") +
      (u ? '<p><a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">근거 열기</a></p>' : "") +
      (num(d.snooze_count) ? '<p class="oc-muted">보류 뒤 다시 올라옴(' + num(d.snooze_count) + "회)</p>" : "") +
      "</div></details></li>";
  }

  function anomalyText(a, teams) {
    var tn = (teams[a.team] || {}).name || a.team || "";
    if (a.kind === "idle") return { k: "warn", t: "놀고 있음", x: tn + " " + (a.name || a.member || "") + (a.days != null ? " · " + a.days + "일째" : "") };
    if (a.kind === "no_record") return { k: "none", t: "기록 없음", x: tn + " " + (a.name || a.member || "") };
    if (a.kind === "stale") return { k: "none", t: "낡음", x: tn + " · " + (a.text || "") };
    return { k: stateKey(a.state), t: STATE_WORD[stateKey(a.state)], x: tn + " · " + (a.text || "") };
  }
  function memoItem(a, teams) {
    var v = anomalyText(a, teams);
    return '<li class="oc-k-' + v.k + '"><span class="oc-memo-tag">' + esc(v.t) + "</span>" + esc(v.x) + "</li>";
  }

  var MEMO_MAX = 5;
  function ceoOffice(data, ctx) {
    var s = data.summary || {}, decisions = arr(data.decisions), more = num(data.decisions_more) || 0;
    var publish = ctx.publish, teams = data.teams || {}, anomalies = arr(data.anomalies);
    var papers = "";
    if (publish) {
      papers += '<li class="oc-paper oc-paper-publish"><div class="oc-paper-row"><span class="oc-paper-kind">발행 승인</span>' +
        '<b class="oc-paper-title">' + esc(publish.title || "이번 주 브리프 발행") + "</b>" +
        '<span class="oc-paper-meta">' + esc(publish.meta || "") + "</span>" +
        '<button type="button" class="oc-link" data-oc-goto="ops">발행·운영 열기</button></div></li>';
    }
    papers += decisions.map(function (d) { return decisionPaper(d, teams); }).join("");
    var head = anomalies.slice(0, MEMO_MAX), rest = anomalies.slice(MEMO_MAX);
    return '<section class="oc-ceo" aria-labelledby="oc-ceo-title">' +
      '<header class="oc-ceo-head"><h2 id="oc-ceo-title">대표실</h2>' +
      '<p class="oc-headline">' + esc(headline(s, publish)) + "</p></header>" +
      summaryLine(s, (num(s.decisions) || 0) + (publish ? 1 : 0)) +
      '<div class="oc-ceo-grid">' +
      '<div class="oc-desk"><h3>결재 서류</h3>' +
      (papers ? '<ul class="oc-papers">' + papers + "</ul>" : '<p class="oc-muted">결재할 서류가 없습니다.</p>') +
      (more ? '<p class="oc-more">그 밖에 ' + more + "건</p>" : "") + "</div>" +
      '<div class="oc-side"><h3>눈여겨볼 것</h3>' +
      (head.length ? '<ul class="oc-memo">' + head.map(function (a) { return memoItem(a, teams); }).join("") + "</ul>" : '<p class="oc-muted">이상 없음</p>') +
      (rest.length ? '<details class="oc-memo-more"><summary>더 보기 ' + rest.length + '</summary><ul class="oc-memo">' +
        rest.map(function (a) { return memoItem(a, teams); }).join("") + "</ul></details>" : "") +
      "</div></div></section>";
  }

  // 이번 주 일 + 다가오는 날짜는 한 접힘 안에(대표실은 결재·눈여겨볼 것만).
  function flowFold(flow, upcoming, teams) {
    var todo = arr(flow && flow.todo), doing = arr(flow && flow.doing), done = arr(flow && flow.done);
    var col = function (title, items, isDone) {
      return '<div class="oc-flow-col"><h4>' + title + " " + items.length + "</h4>" + (items.length ? "<ul>" + items.map(function (t) {
        var tn = (teams[t.team] || {}).name || t.team || "";
        return '<li class="' + (t.overdue ? "is-overdue" : "") + '">' + esc(t.title) + " <small>" + esc(tn) +
          (!isDone && t.lane === "review" ? " · 검사 중" : "") +
          (!isDone && t.due_on ? " · " + esc(dayLabel(t.due_on)) + "까지" : "") + "</small>" +
          (t.overdue ? ' <span class="oc-flag-bad">마감 지남</span>' : "") + "</li>";
      }).join("") + "</ul>" : '<p class="oc-muted">없음</p>') + "</div>";
    };
    var dates = '<div class="oc-flow-col"><h4>다가오는 날짜</h4>' + (upcoming.length ? '<ul class="oc-dates">' + upcoming.map(function (u) {
      var tn = (teams[u.team] || {}).name || u.team || "";
      return "<li><b>" + esc(dayLabel(u.on)) + "</b>" + (u.kind === "decision" ? "결재 기한 · " : "") + esc(u.text) +
        (tn ? " <small>" + esc(tn) + "</small>" : "") + "</li>";
    }).join("") + "</ul>" : '<p class="oc-muted">7일 안에 없음</p>') + "</div>";
    return '<details class="oc-flow"><summary>이번 주 일 <small>할 일 ' + todo.length + " · 진행 " + doing.length + " · 완료 " + done.length + "</small></summary>" +
      '<div class="oc-flow-grid">' + col("할 일", todo) + col("진행", doing) + col("완료", done, true) + dates + "</div></details>";
  }

  function render(data, ctx) {
    ctx = ctx || {};
    if (!data || typeof data !== "object") return "";
    var teams = data.teams || {}, tree = arr(data.tree), flow = data.flow || {};
    var c = { tasks: tasksByMember(flow), done: doneByMember(flow) };
    var offices = tree.filter(function (n) { return n.kind === "office"; });
    var wings = tree.filter(function (n) { return n.kind === "division"; });
    var floor = (offices.length ? '<div class="oc-offices">' + offices.map(function (n) { return teamBox(n, teams[n.slug], c, { room: true }); }).join("") + "</div>" : "") +
      (wings.length ? '<div class="oc-wings">' + wings.map(function (n) { return wing(n, teams, c); }).join("") + "</div>" : "");
    return '<div class="oc">' + ceoOffice(data, ctx) +
      '<section class="oc-floor" aria-label="사무실"><header class="oc-floor-head"><h2>사무실</h2>' +
      '<p class="oc-legend"><span class="oc-k-work"><i></i>일하는 중</span><span class="oc-k-warn"><i></i>대기</span>' +
      '<span class="oc-k-bad"><i></i>막힘</span><span class="oc-k-free"><i></i>일 없음</span></p></header>' +
      (floor || '<p class="oc-muted">아직 조직 자료가 없습니다.</p>') + "</section>" +
      flowFold(flow, arr(data.upcoming), teams) + "</div>";
  }

  // 화면 동작: 자리를 누르면 그 팀 안에서 상세가 펼쳐지고, 다른 자리를 누르면 바뀐다.
  // ★`hidden` 속성은 author display 에 진다 → 열림은 클래스로만 다룬다.
  function bind(host, onGoto) {
    if (!host || host.__ocBound) return;
    host.__ocBound = true;
    host.addEventListener("click", function (e) {
      var go = e.target.closest ? e.target.closest("[data-oc-goto]") : null;
      if (go) { if (onGoto) onGoto(go.getAttribute("data-oc-goto")); return; }
      var close = e.target.closest ? e.target.closest("[data-oc-close]") : null;
      var btn = e.target.closest ? e.target.closest("[data-oc-seat]") : null;
      var id = close ? close.getAttribute("data-oc-close") : btn ? btn.getAttribute("data-oc-seat") : "";
      if (!id) return;
      var panel = host.querySelector("#" + id);
      var opening = !!btn && !(panel && panel.classList.contains("is-open"));
      Array.prototype.forEach.call(host.querySelectorAll(".oc-seat-detail.is-open"), function (p) { p.classList.remove("is-open"); });
      Array.prototype.forEach.call(host.querySelectorAll('[data-oc-seat][aria-expanded="true"]'), function (b) { b.setAttribute("aria-expanded", "false"); });
      if (opening && panel) {
        panel.classList.add("is-open");
        btn.setAttribute("aria-expanded", "true");
      } else if (close) {
        var back = host.querySelector('[data-oc-seat="' + id + '"]');
        if (back && back.focus) back.focus();
      }
    });
  }

  var api = { render: render, bind: bind, seatStatus: seatStatus, dayLabel: dayLabel, clock: clock, STATE_WORD: STATE_WORD };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.GRMOps = api;
})(typeof window !== "undefined" ? window : this);
