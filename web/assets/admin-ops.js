/* 운영 센터 홈 렌더러 (PR3/7, 설계서 2026-11-06 §3) — 사무실 층 배치도.
 *
 * 입력은 public.ops_home() 의 JSON 한 덩어리뿐이다. 상태(정상·주의·문제·측정 필요), 놀고 있음,
 * 활동 기록 없음, 낡음, 마감 지남은 전부 DB 가 계산해서 준다 — 여기서는 그 필드를 글자·색으로
 * 옮겨 그리기만 한다(경계값·일수 계산을 다시 하지 않는다. 같은 규칙이 두 벌이 되면 갈라진다).
 *
 * 순수 함수: render(data, ctx) → HTML 문자열. DOM·네트워크를 만지지 않아 Node 로 시험한다
 * (tests/js/ops_home_render.js). 화면 동작(자리 펼치기)은 bind(host) 한 곳.
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

  // ── 자리(직원) 한 줄 상태 — ops_home 필드를 글자로 옮길 뿐 ────────────────────
  // 순서: 쉬는 중(state=paused) > 막힘(맡은 일 중 overdue) > 놀고 있음(idle) > 검사 대기(review)
  //       > 일하는 중(open_tasks>0) > 활동 기록 없음(no_record·기록 null) > 일 없음
  function seatStatus(m, tasks) {
    var overdue = tasks.filter(function (t) { return t.overdue; }).length;
    var review = tasks.filter(function (t) { return t.lane === "review"; }).length;
    var open = num(m.open_tasks) || 0;
    var days = m.last_active_days == null ? null : num(m.last_active_days);
    if (m.state === "paused") return { k: "off", word: "쉬는 중", line: "일시 정지된 자리" };
    if (overdue) return { k: "bad", word: "막힘", line: "마감 지난 일 " + overdue + "건" };
    if (m.idle) return { k: "warn", word: "놀고 있음", line: (days == null ? "" : days + "일째 ") + "맡은 일 없음" };
    if (review) return { k: "warn", word: "검사 대기", line: "검사를 기다리는 일 " + review + "건" };
    if (open > 0) {
      var first = tasks.filter(function (t) { return t.lane === "doing"; })[0] || tasks[0];
      return { k: "work", word: "일하는 중", line: first ? first.title : "맡은 일 " + open + "건" };
    }
    if (m.no_record) return { k: "none", word: "활동 기록 없음", line: "팀 목표가 밀려 있는데 기록이 없음" };
    if (days == null) return { k: "none", word: "활동 기록 없음", line: "아직 기록이 없습니다" };
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
  function goalBar(g) {
    var st = stateKey(g.state), cur = num(g.current), tgt = num(g.target);
    var cls = "oc-bar oc-b-" + st + (g.stale ? " is-stale" : "");
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
      '<div class="oc-goal-val">' + esc(goalValueText(g)) + (when ? ' <small>' + esc(when) + "</small>" : "") + "</div></li>";
  }
  function goalDetail(g) {
    var st = stateKey(g.state), when = goalWhen(g);
    return "<li><b>" + esc(g.label) + "</b> " + dot(st) +
      "<span>" + esc(goalValueText(g)) + (when ? " · " + esc(when) : "") +
      (g.measure_kind ? " · " + esc(MEASURE_WORD[g.measure_kind] || g.measure_kind) : "") + "</span>" +
      (g.note ? "<small>" + esc(g.note) + "</small>" : "") + "</li>";
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
      '<button type="button" class="oc-seat-btn" data-oc-seat="' + id + '" aria-expanded="false" aria-controls="' + id + '">' +
      '<span class="oc-avatar" aria-hidden="true">' + esc(name.charAt(0)) + "</span>" +
      '<span class="oc-seat-text"><b>' + esc(name) + "</b>" +
      '<span class="oc-seat-line"><span class="oc-seat-word">' + esc(s.word) + "</span>" +
      (s.line ? " · " + esc(s.line) : "") + "</span></span></button></li>";
  }
  function seatDetail(m, teamSlug, tasks, done, goals, idx) {
    var id = "oc-seat-" + esc(teamSlug) + "-" + idx, days = m.last_active_days == null ? null : num(m.last_active_days);
    var meta = [KIND_WORD[m.kind] || m.kind, m.model].filter(Boolean).join(" · ");
    var taskHtml = tasks.length ? "<ul>" + tasks.map(function (t) {
      return "<li>" + esc(t.title) + " <small>" + esc(LANE_WORD[t.lane] || t.lane || "") +
        (t.due_on ? " · " + esc(dayLabel(t.due_on)) + "까지" : "") + "</small>" +
        (t.overdue ? ' <span class="oc-flag-bad">마감 지남</span>' : "") + "</li>";
    }).join("") + "</ul>" : "<p>맡은 일 없음</p>";
    var doneHtml = done.length ? '<h5>이번 주 끝낸 일</h5><ul>' + done.map(function (t) {
      var u = safeUrl(t.result_url);
      return "<li>" + (u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(t.title) + "</a>" : esc(t.title)) + "</li>";
    }).join("") + "</ul>" : "";
    var last = days == null ? "기록 없음" : agoDays(days) + (m.last_active_note ? " · " + esc(m.last_active_note) : "");
    return '<div class="oc-seat-detail" id="' + id + '" role="region" aria-label="' + esc(m.name) + ' 상세">' +
      '<div class="oc-sd-head"><b>' + esc(m.name) + "</b><small>" + esc(meta) + "</small>" +
      '<button type="button" class="oc-sd-close" data-oc-close="' + id + '">닫기</button></div>' +
      "<h5>지금 하는 일</h5>" + taskHtml + doneHtml +
      "<h5>마지막 활동</h5><p>" + last + "</p>" +
      (m.idle_exempt ? "<p class=\"oc-muted\">부를 때만 일하는 자리라 '놀고 있음'에서 뺍니다.</p>" : "") +
      (goals.length ? "<h5>팀 목표</h5><ul class=\"oc-goal-detail\">" + goals.map(goalDetail).join("") + "</ul>" : "") +
      "</div>";
  }

  function teamBox(node, team, ctx, opts) {
    team = team || {};
    var st = stateKey(team.state || node.state), slug = node.slug;
    var members = arr(team.members), goals = arr(team.goals);
    var decisions = num(node.decisions) || 0;
    var unassigned = ctx.unassigned[slug] || 0;
    var reason = st === "ok" ? "" : (node.reason || team.reason || "");
    var h = opts && opts.room ? "h3" : "h4";
    var seats = members.map(function (m, i) { return seat(m, slug, ctx.tasks[m.slug] || [], i); }).join("");
    var details = members.map(function (m, i) {
      return seatDetail(m, slug, ctx.tasks[m.slug] || [], ctx.done[m.slug] || [], goals, i);
    }).join("");
    return '<section class="oc-team oc-t-' + st + (opts && opts.room ? " oc-room oc-office" : "") + '" data-team="' + esc(slug) + '">' +
      '<header class="oc-team-head"><' + h + ">" + esc(node.name || team.name || slug) + "</" + h + ">" + dot(st) +
      (decisions ? '<span class="oc-chip">결재 ' + decisions + "</span>" : "") + "</header>" +
      (reason ? '<p class="oc-reason">' + esc(reason) + "</p>" : "") +
      (team.blurb && opts && opts.room ? '<p class="oc-blurb">' + esc(team.blurb) + "</p>" : "") +
      (members.length ? '<ul class="oc-seats">' + seats + "</ul>" + details : '<p class="oc-muted">자리 없음</p>') +
      (unassigned ? '<p class="oc-muted">맡은 사람 없는 일 ' + unassigned + "건</p>" : "") +
      (goals.length ? '<ul class="oc-goals" aria-label="팀 목표">' + goals.map(goalRow).join("") + "</ul>" : "") +
      "</section>";
  }

  function wing(node, teams, ctx) {
    var st = stateKey(node.state), kids = arr(node.children);
    var allOk = st === "ok";
    return '<details class="oc-room oc-wing oc-t-' + st + '"' + (allOk && ctx.foldOk ? "" : " open") + ' data-team="' + esc(node.slug) + '">' +
      '<summary class="oc-team-head"><h3>' + esc(node.name) + "</h3>" + dot(st) +
      (num(node.decisions) ? '<span class="oc-chip">결재 ' + num(node.decisions) + "</span>" : "") +
      '<span class="oc-count">팀 ' + kids.length + "</span></summary>" +
      (st === "ok" || !node.reason ? "" : '<p class="oc-reason">' + esc(node.reason) + "</p>") +
      '<div class="oc-wing-teams">' + (kids.length ? kids.map(function (k) { return teamBox(k, teams[k.slug], ctx); }).join("") : '<p class="oc-muted">팀 없음</p>') + "</div>" +
      "</details>";
  }

  function summaryLine(s) {
    s = s || {};
    var part = function (label, n, cls) {
      n = num(n) || 0;
      return '<span class="oc-sum' + (n && cls ? " " + cls : "") + '">' + label + " <b>" + n + "</b></span>";
    };
    return part("문제", s.bad, "is-bad") + part("주의", s.warn, "is-warn") + part("측정 필요", s.unknown_teams) +
      part("결재", s.decisions_all != null ? s.decisions_all : s.decisions, "is-warn") +
      part("놀고 있음", s.idle, "is-warn") + part("기록 없음", s.no_record) +
      part("7일 안 마감", s.deadlines) + (num(s.overdue) ? part("마감 지남", s.overdue, "is-bad") : "");
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
      (num(d.snooze_count) ? '<p class="oc-muted">보류했다가 다시 올라온 서류(' + num(d.snooze_count) + "회)</p>" : "") +
      '<p class="oc-muted">승인·보류는 다음 단계에서 붙습니다.</p>' +
      "</div></details></li>";
  }

  function anomalyText(a, teams) {
    var tn = (teams[a.team] || {}).name || a.team || "";
    if (a.kind === "idle") return { k: "warn", t: "놀고 있음", x: tn + " " + (a.name || a.member || "") + (a.days != null ? " · " + a.days + "일째" : "") };
    if (a.kind === "no_record") return { k: "none", t: "활동 기록 없음", x: tn + " " + (a.name || a.member || "") };
    if (a.kind === "stale") return { k: "none", t: "낡음", x: tn + " · " + (a.text || "") };
    return { k: stateKey(a.state), t: STATE_WORD[stateKey(a.state)], x: tn + " · " + (a.text || "") };
  }

  function ceoOffice(data, ctx) {
    var s = data.summary || {}, decisions = arr(data.decisions), more = num(data.decisions_more) || 0;
    var publish = ctx.publish, teams = data.teams || {};
    var anomalies = arr(data.anomalies), upcoming = arr(data.upcoming);
    var papers = "";
    if (publish) {
      papers += '<li class="oc-paper oc-paper-publish"><div class="oc-paper-row"><span class="oc-paper-kind">발행 승인</span>' +
        '<b class="oc-paper-title">' + esc(publish.title || "이번 주 브리프 발행") + "</b>" +
        '<span class="oc-paper-meta">' + esc(publish.meta || "") + '</span>' +
        '<button type="button" class="oc-link" data-oc-goto="ops">발행·운영 열기</button></div></li>';
    }
    papers += decisions.map(function (d) { return decisionPaper(d, teams); }).join("");
    var shown = 6, anomHtml = anomalies.slice(0, shown).map(function (a) {
      var v = anomalyText(a, teams);
      return '<li class="oc-k-' + v.k + '"><span class="oc-memo-tag">' + esc(v.t) + "</span>" + esc(v.x) + "</li>";
    }).join("");
    return '<section class="oc-ceo" aria-labelledby="oc-ceo-title">' +
      '<header class="oc-ceo-head"><h2 id="oc-ceo-title">대표실</h2>' +
      '<p class="oc-headline">' + esc(headline(s, publish)) + "</p></header>" +
      '<p class="oc-sumline">' + summaryLine(Object.assign({}, s, { decisions_all: (num(s.decisions) || 0) + (publish ? 1 : 0) })) + "</p>" +
      '<div class="oc-ceo-grid">' +
      '<div class="oc-desk"><h3>결재 서류</h3>' +
      (papers ? '<ul class="oc-papers">' + papers + "</ul>" : '<p class="oc-muted">결재할 서류가 없습니다.</p>') +
      (more ? '<p class="oc-more">그 밖에 ' + more + "건이 더 기다리고 있습니다.</p>" : "") + "</div>" +
      '<div class="oc-side">' +
      '<h3>눈여겨볼 것</h3>' + (anomHtml ? '<ul class="oc-memo">' + anomHtml + "</ul>" : '<p class="oc-muted">이상 없음</p>') +
      (anomalies.length > shown ? '<p class="oc-more">그 밖에 ' + (anomalies.length - shown) + "건</p>" : "") +
      "<h3>다가오는 날짜</h3>" + (upcoming.length ? '<ul class="oc-dates">' + upcoming.map(function (u) {
        var tn = (teams[u.team] || {}).name || u.team || "";
        return "<li><b>" + esc(dayLabel(u.on)) + "</b>" + (u.kind === "decision" ? "결재 기한 · " : "") + esc(u.text) +
          (tn ? " <small>" + esc(tn) + "</small>" : "") + "</li>";
      }).join("") + "</ul>" : '<p class="oc-muted">7일 안에 정해진 날짜 없음</p>') +
      "</div></div></section>";
  }

  function flowFold(flow, teams) {
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
    return '<details class="oc-flow"><summary>이번 주 일 목록 <small>할 일 ' + todo.length + " · 진행 " + doing.length + " · 완료 " + done.length + "</small></summary>" +
      '<div class="oc-flow-grid">' + col("할 일", todo) + col("진행", doing) + col("완료", done, true) + "</div></details>";
  }

  function render(data, ctx) {
    ctx = ctx || {};
    if (!data || typeof data !== "object") return "";
    var teams = data.teams || {}, tree = arr(data.tree), flow = data.flow || {};
    var c = { tasks: tasksByMember(flow), done: doneByMember(flow), unassigned: {}, foldOk: !!ctx.foldOk };
    arr(flow.todo).concat(arr(flow.doing)).forEach(function (t) { if (t && !t.member && t.team) c.unassigned[t.team] = (c.unassigned[t.team] || 0) + 1; });
    var offices = tree.filter(function (n) { return n.kind === "office"; });
    var wings = tree.filter(function (n) { return n.kind === "division"; });
    var floor = (offices.length ? '<div class="oc-offices">' + offices.map(function (n) { return teamBox(n, teams[n.slug], c, { room: true }); }).join("") + "</div>" : "") +
      (wings.length ? '<div class="oc-wings">' + wings.map(function (n) { return wing(n, teams, c); }).join("") + "</div>" : "");
    return '<div class="oc">' + ceoOffice(data, ctx) +
      '<section class="oc-floor" aria-label="사무실"><header class="oc-floor-head"><h2>사무실</h2>' +
      '<p class="oc-legend"><span class="oc-k-work"><i></i>일하는 중</span><span class="oc-k-warn"><i></i>대기·놀고 있음</span>' +
      '<span class="oc-k-bad"><i></i>막힘</span><span class="oc-k-free"><i></i>일 없음·기록 없음</span></p></header>' +
      (floor || '<p class="oc-muted">아직 조직 자료가 없습니다.</p>') + "</section>" +
      flowFold(flow, teams) + "</div>";
  }

  // 화면 동작: 자리를 누르면 그 팀 안에서 상세가 펼쳐지고, 다른 자리를 누르면 바뀐다.
  // ★`hidden` 속성은 author display 에 진다(09-?? 교훈) → 열림은 클래스로만 다룬다.
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
