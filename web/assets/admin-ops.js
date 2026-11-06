/* 운영 센터 홈 렌더러 — 대표 확정 시안(2026-11-06 grm-company-concept) 구성.
 *
 * 위에서 아래로: 한 줄 요약 → [대표실 · 결재 서류 | 우리 회사 숫자(정적 마크업, admin.js 가 채움)] → 팀과 직원.
 * 입력은 public.ops_home() 의 JSON 한 덩어리뿐이다. 상태(정상·주의·문제·측정 필요), idle, no_record,
 * 낡음, 마감 지남은 전부 DB 가 계산해서 준다 — 여기서는 그 필드를 글자 태그로 옮겨 그리기만 한다
 * (경계값·일수 계산을 다시 하지 않는다. 같은 규칙이 두 벌이 되면 갈라진다).
 *
 * 순수 함수: renderSummary / renderDocs / renderTeams(data, ctx) → HTML 문자열, render = 셋을 이은 것.
 * DOM·네트워크를 만지지 않아 Node 로 시험한다(tests/js/ops_home_render.js).
 * 직원 한 줄 = 직무 이름 + 지금 맡은 일 한 줄 + 글자 태그(진행 중 / 확인 대기 / 지연 / 배정 없음).
 * 평면도·원형 아바타·점 아이콘은 쓰지 않는다.
 * 결재(PR4): 서류마다 [승인(질문형은 권장 라벨)] [보류(내일 아침)] [3일 뒤] — 누르면 admin.js 가 rpc('ops_decide')
 * 를 부르고, 결과는 ctx.decide 로 다시 넘어와 그려진다(처리 중·처리됨·오류). 반려 버튼은 없다(D5).
 * 운영자 전용 한국어 화면 — grm_i18n.EXCLUDED_ASSETS 대상(영문판에 실리지 않는다).
 */
(function (root) {
  "use strict";

  var STATE_WORD = { ok: "정상", warn: "주의", bad: "문제", unknown: "측정 필요" };
  var UNKNOWN_WHY = { no_target: "목표 미정", no_value: "값 없음", stale: "낡음", no_goals: "목표 없음", no_children: "팀 없음" };
  var KIND_WORD = { routine: "정기 작업", session: "세션", code: "코드" };
  var LANE_WORD = { inbox: "지시함", doing: "진행", review: "확인 대기", done: "완료" };
  var MEASURE_WORD = { sql: "자동(DB)", github: "자동(GitHub)", manual: "손으로 기록" };
  var TAG_WORD = { work: "진행 중", wait: "확인 대기", bad: "지연", none: "배정 없음" };
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
  // KST 날짜 부품. "2026-11-09" 또는 ISO 시각 → [y, m, d]
  function kstParts(value) {
    if (!value) return null;
    var s = String(value), plain = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (plain) return [+plain[1], +plain[2], +plain[3]];
    var t = Date.parse(s);
    if (isNaN(t)) return null;
    var k = new Date(t + 9 * 3600 * 1000);
    return [k.getUTCFullYear(), k.getUTCMonth() + 1, k.getUTCDate()];
  }
  function dayLabel(value) {
    var p = kstParts(value);
    if (!p) return value ? String(value).slice(0, 10) : "";
    var wd = WEEKDAY[new Date(Date.UTC(p[0], p[1] - 1, p[2])).getUTCDay()];
    return (p[1] < 10 ? "0" : "") + p[1] + "-" + (p[2] < 10 ? "0" : "") + p[2] + "(" + wd + ")";
  }
  // 기한까지 남은 날 — 표시용. 기준은 서버 시각(generated_at)이라 기기 시계와 무관.
  function daysLeft(due, generatedAt) {
    var a = kstParts(generatedAt), b = kstParts(due);
    if (!a || !b) return null;
    return Math.round((Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2])) / 86400000);
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
  function stateTag(state) {
    var k = stateKey(state);
    return k === "ok" ? "" : '<span class="oc-pill oc-st-' + k + '">' + STATE_WORD[k] + "</span>";
  }

  // ── 직원 한 줄: 태그 + 맡은 일 한 줄 — ops_home 필드를 옮길 뿐 ───────────────────
  // 순서: 지연(맡은 일 중 overdue) > 확인 대기(lane=review) > 진행 중(open_tasks>0) > 배정 없음.
  // 배정 없음의 설명: 일시 정지(state=paused) / 기록 없음(no_record·기록 null) / 마지막 활동(idle 이면 강조).
  function memberStatus(m, tasks) {
    var overdue = tasks.filter(function (t) { return t.overdue; });
    var review = tasks.filter(function (t) { return t.lane === "review"; });
    var open = num(m.open_tasks) || 0;
    var days = m.last_active_days == null ? null : num(m.last_active_days);
    if (m.state === "paused") return { k: "none", tag: TAG_WORD.none, text: "일시 정지" };
    if (overdue.length) return { k: "bad", tag: TAG_WORD.bad, text: "마감 지난 일 " + overdue.length + "건 · " + overdue[0].title };
    if (review.length) return { k: "wait", tag: TAG_WORD.wait, text: review[0].title };
    if (open > 0) {
      var first = tasks.filter(function (t) { return t.lane === "doing"; })[0] || tasks[0];
      return { k: "work", tag: TAG_WORD.work, text: first ? first.title : "맡은 일 " + open + "건" };
    }
    if (m.no_record || days == null) return { k: "none", tag: TAG_WORD.none, text: "기록 없음" };
    return { k: "none", tag: TAG_WORD.none, idle: !!m.idle,
             text: m.idle ? "마지막 활동 " + agoDays(days) : (m.last_active_note || "마지막 활동 " + agoDays(days)) };
  }

  function goalValueText(g) {
    var unit = g.unit || "";
    if (num(g.target) == null) return (num(g.current) == null ? "값 없음" : fmtNum(g.current) + unit) + " · 목표 미정";
    var cur = num(g.current) == null ? "—" : fmtNum(g.current) + unit;
    return cur + " / 목표 " + fmtNum(g.target) + unit + (g.direction === "lte" ? " 이하" : "");
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
      '<div class="oc-goal-top"><span>' + esc(g.label) + "</span>" +
      (st === "ok" ? "" : '<span class="oc-goal-st">' + STATE_WORD[st] + (why && why !== "낡음" ? " · " + why : "") + "</span>") + "</div>" +
      goalBar(g) +
      '<div class="oc-goal-val">' + esc(goalValueText(g)) + (when ? " · " + esc(when) : "") +
      (g.measure_kind ? " · " + esc(MEASURE_WORD[g.measure_kind] || g.measure_kind) : "") + "</div>" +
      (g.note ? '<div class="oc-goal-val">' + esc(g.note) + "</div>" : "") + "</li>";
  }

  function tasksByMember(flow) {
    var map = {}, add = function (t) { if (!t || !t.member) return; (map[t.member] = map[t.member] || []).push(t); };
    arr(flow && flow.todo).forEach(add);
    arr(flow && flow.doing).forEach(add);
    return map;
  }

  function memberLine(m, tasks) {
    var s = memberStatus(m, tasks), days = m.last_active_days == null ? null : num(m.last_active_days);
    var meta = [KIND_WORD[m.kind] || m.kind, m.model].filter(Boolean).join(" · ");
    var taskHtml = tasks.length ? "<ul>" + tasks.map(function (t) {
      return "<li>" + esc(t.title) + " <small>" + esc(LANE_WORD[t.lane] || t.lane || "") +
        (t.due_on ? " · " + esc(dayLabel(t.due_on)) + "까지" : "") + "</small>" +
        (t.overdue ? ' <span class="oc-flag-bad">마감 지남</span>' : "") + "</li>";
    }).join("") + "</ul>" : "<p>배정된 업무 없음</p>";
    var last = days == null ? "기록 없음" : agoDays(days) + (m.last_active_note ? " · " + esc(m.last_active_note) : "");
    return '<details class="oc-line oc-k-' + s.k + (s.idle ? " is-idle" : "") + '">' +
      '<summary><span class="oc-n">' + esc(m.name || m.slug) + '</span><span class="oc-s">' + esc(s.text) + "</span>" +
      '<span class="oc-pill oc-p-' + s.k + '">' + s.tag + "</span></summary>" +
      '<div class="oc-line-body">' +
      (meta ? '<p class="oc-muted">' + esc(meta) + (m.idle_exempt ? " · 부를 때만 일하는 자리" : "") + "</p>" : "") +
      "<h5>맡은 일</h5>" + taskHtml +
      "<h5>마지막 활동</h5><p>" + last + "</p></div></details>";
  }

  function teamBlock(node, team, ctx, isOffice) {
    team = team || {};
    var st = stateKey(team.state || node.state), goals = arr(team.goals), members = arr(team.members);
    var reason = st === "ok" ? "" : (node.reason || team.reason || "");
    var lines = members.map(function (m) { return memberLine(m, ctx.tasks[m.slug] || []); }).join("");
    var alerts = goals.filter(function (g) { return g.state !== "ok"; }).length;
    return '<div class="oc-team oc-t-' + st + '" data-team="' + esc(node.slug) + '">' +
      (isOffice ? "" : '<h4>' + esc(node.name || team.name) + stateTag(st) +
        (num(node.decisions) ? '<span class="oc-pill oc-p-dec">결재 ' + num(node.decisions) + "</span>" : "") + "</h4>") +
      (reason ? '<p class="oc-reason">' + esc(reason) + "</p>" : "") +
      (lines || '<p class="oc-muted">직원 없음</p>') +
      (goals.length ? '<details class="oc-goals-fold"><summary>목표 ' + goals.length + "개" + (alerts ? " · 살펴볼 것 " + alerts : "") +
        '</summary><ul class="oc-goals">' + goals.map(goalRow).join("") + "</ul></details>" : "") +
      "</div>";
  }

  function room(node, teams, ctx) {
    var st = stateKey(node.state), isOffice = node.kind === "office";
    var blurb = (teams[node.slug] || {}).blurb || "";
    var body = isOffice ? teamBlock(node, teams[node.slug], ctx, true)
      : arr(node.children).map(function (k) { return teamBlock(k, teams[k.slug], ctx, false); }).join("") || '<p class="oc-muted">팀 없음</p>';
    return '<section class="oc-card oc-room" data-room="' + esc(node.slug) + '">' +
      "<h3>" + esc(node.name) + stateTag(st) +
      (isOffice && num(node.decisions) ? '<span class="oc-pill oc-p-dec">결재 ' + num(node.decisions) + "</span>" : "") + "</h3>" +
      (blurb ? '<p class="oc-rn">' + esc(blurb) + "</p>" : "") + body + "</section>";
  }

  // ── 한 줄 요약 ───────────────────────────────────────────────────────────────
  function decisionsAll(data, ctx) { return (num((data.summary || {}).decisions) || 0) + (ctx && ctx.publish ? 1 : 0); }
  function renderSummary(data, ctx) {
    if (!data || typeof data !== "object") return "";
    var s = data.summary || {}, n = decisionsAll(data, ctx);
    var bad = num(s.bad) || 0, alert = bad + (num(s.warn) || 0), idle = num(s.idle) || 0;
    var a = n ? "결재할 서류가 <b>" + n + "건</b> 있고, " : "결재할 서류는 없고, ";
    var b = alert ? "주의가 필요한 팀이 <em>" + alert + "곳</em>입니다." + (bad ? " 그중 " + bad + "곳은 문제입니다." : "")
      : "주의가 필요한 팀은 없습니다.";
    var c = idle ? " 배정된 업무가 없는 직원은 " + idle + "명입니다." : " 배정된 업무가 없는 직원은 없습니다.";
    return a + b + c;
  }

  // ── 대표실 · 결재 서류 ───────────────────────────────────────────────────────
  // 승인 버튼 글자: 승인 요청은 '승인', 질문은 권장 답이 짧으면 그 답(예: '유지'), 길면 '권장대로'.
  function approveLabel(d) {
    if (d.reason !== "ask") return "승인";
    var r = String(d.recommendation || "").trim();
    return r && r.length <= 8 ? r : "권장대로";
  }
  function decideButtons(d, st) {
    var busy = !!(st.busy && st.busy[d.id]), id = esc(d.id), t = esc(d.title);
    var dis = busy ? " disabled" : "";
    return '<div class="oc-btns">' +
      '<button type="button" class="oc-pri" data-oc-decide="approve" data-oc-id="' + id + '" aria-label="' + t + " — " + esc(approveLabel(d)) + '"' + dis + ">" +
        (busy ? "처리 중" : esc(approveLabel(d))) + "</button>" +
      '<button type="button" data-oc-decide="snooze" data-oc-snooze="tomorrow" data-oc-id="' + id + '" aria-label="' + t + ' — 내일 아침까지 보류"' + dis + ">보류</button>" +
      '<button type="button" class="oc-link" data-oc-decide="snooze" data-oc-snooze="3d" data-oc-id="' + id + '" aria-label="' + t + ' — 3일 뒤까지 보류"' + dis + ">3일 뒤</button>" +
      "</div>";
  }
  function decisionDoc(d, teams, generatedAt, st) {
    st = st || {};
    var team = teams[d.team] || {}, u = safeUrl(d.ref_url), left = daysLeft(d.due_at, generatedAt);
    var done = st.done && st.done[d.id], err = st.errors && st.errors[d.id];
    var due = !d.due_at ? "기한 없음"
      : d.overdue ? '<span class="oc-flag-warn">기한 지남 ' + esc(dayLabel(d.due_at)) + "</span>"
      : "기한 " + esc(dayLabel(d.due_at)) + (left != null && left >= 0 ? " (" + (left === 0 ? "오늘" : left + "일 남음") + ")" : "");
    return '<div class="oc-doc' + (d.overdue ? " is-overdue" : "") + (done ? " is-done" : "") + '" data-oc-doc="' + esc(d.id) + '"><div class="oc-doc-body">' +
      '<span class="oc-kind">' + (d.reason === "ask" ? "질문" : "승인 요청") + "</span>" +
      '<span class="oc-t">' + esc(d.title) + "</span>" +
      '<div class="oc-m">' + esc(team.name || d.team || "") + " · " + due +
      (u ? ' · <a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">근거</a>' : "") + "</div>" +
      // 시안: 서류 = 종류·제목 / 팀·기한 / 권장 한 줄(권장 + 이유) / 승인하면 일어나는 일.
      (d.recommendation || d.why ? '<div class="oc-rec"><b>권장</b> ' + esc([d.recommendation, d.why].filter(Boolean).join(". ")) + "</div>" : "") +
      (d.if_approved && !done ? '<div class="oc-m">승인하면 ' + esc(d.if_approved) + "</div>" : "") +
      (done ? '<div class="oc-done" role="status">' + esc(done) + "</div>" : "") +
      (err && !done ? '<div class="oc-err" role="status">' + esc(err) + "</div>" : "") +
      "</div>" + (done ? "" : decideButtons(d, st)) + "</div>";
  }
  function renderDocs(data, ctx) {
    ctx = ctx || {};
    if (!data || typeof data !== "object") return "";
    var teams = data.teams || {}, decisions = arr(data.decisions).slice(0, 3), more = num(data.decisions_more) || 0;
    var html = "";
    if (ctx.publish) {
      html += '<div class="oc-doc"><div class="oc-doc-body"><span class="oc-kind">발행 승인</span>' +
        '<span class="oc-t">' + esc(ctx.publish.title || "이번 주 브리프 발행") + "</span>" +
        '<div class="oc-m">' + esc(ctx.publish.meta || "") + "</div></div>" +
        '<button type="button" class="oc-link" data-oc-goto="ops">발행·운영 열기</button></div>';
    }
    html += decisions.map(function (d) { return decisionDoc(d, teams, data.generated_at, ctx.decide); }).join("");
    if (!html) html = '<p class="oc-empty">결재할 서류가 없습니다.</p>';
    return html + (more ? '<div class="oc-more">그 밖에 ' + more + "건이 더 기다립니다</div>" : "");
  }

  // ── 팀과 직원 ────────────────────────────────────────────────────────────────
  function renderTeams(data) {
    if (!data || typeof data !== "object") return "";
    var teams = data.teams || {}, tree = arr(data.tree), c = { tasks: tasksByMember(data.flow || {}) };
    var head = '<div class="oc-sec"><h2>팀과 직원</h2><span>누가 지금 무슨 일을 맡고 있는지</span>' +
      '<div class="oc-legend"><span class="oc-pill oc-p-work">진행 중</span><span class="oc-pill oc-p-wait">확인 대기</span>' +
      '<span class="oc-pill oc-p-bad">지연</span><span class="oc-pill oc-p-none">배정 없음</span></div></div>';
    if (!tree.length) return head + '<p class="oc-empty">아직 조직 자료가 없습니다.</p>';
    return head + '<div class="oc-rooms">' + tree.map(function (n) { return room(n, teams, c); }).join("") + "</div>";
  }

  function render(data, ctx) {
    if (!data || typeof data !== "object") return "";
    return '<p class="oc-summary">' + renderSummary(data, ctx) + "</p>" +
      '<section class="oc-card oc-docs">' + renderDocs(data, ctx) + "</section>" + renderTeams(data);
  }

  // 화면 동작: '발행·운영 열기' 이동과 결재 버튼(펼침은 <details> 가 한다). 처리 자체는 admin.js 의 onDecide.
  function bind(host, onGoto, onDecide) {
    if (!host || host.__ocBound) return;
    host.__ocBound = true;
    host.addEventListener("click", function (e) {
      var go = e.target.closest ? e.target.closest("[data-oc-goto]") : null;
      if (go) { if (onGoto) onGoto(go.getAttribute("data-oc-goto")); return; }
      var b = e.target.closest ? e.target.closest("[data-oc-decide]") : null;
      if (!b || b.disabled || !onDecide) return;
      onDecide(Number(b.getAttribute("data-oc-id")), b.getAttribute("data-oc-decide"), b.getAttribute("data-oc-snooze") || null);
    });
  }

  var api = { render: render, renderSummary: renderSummary, renderDocs: renderDocs, renderTeams: renderTeams,
              bind: bind, approveLabel: approveLabel, memberStatus: memberStatus, dayLabel: dayLabel, daysLeft: daysLeft, clock: clock, STATE_WORD: STATE_WORD };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.GRMOps = api;
})(typeof window !== "undefined" ? window : this);
