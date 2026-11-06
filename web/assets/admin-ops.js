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
 * 업무 지시(PR5): '팀과 직원' 머리의 [업무 지시] → renderTaskSheet(팀 필수·직원 선택·제목·내용·마감일) →
 * admin.js 가 validateTask 로 먼저 막고 rpc('ops_task_create') 를 부른다. 보낸 지시는 '이번 주 일' 접힘(flow)의 지시함에 보인다.
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

  // ── 이번 주 일(접힘) — ops_home.flow 그대로: 지시함 · 진행(확인 대기 포함) · 이번 주 완료 ─────────
  // 대표가 막 보낸 지시는 담당이 비어 있을 수 있어 직원 줄에는 안 보인다 → 여기서 '접수됨'으로 보인다(두 번 안 보내게).
  function memberName(teams, t) {
    var ms = arr((teams[t.team] || {}).members);
    for (var i = 0; i < ms.length; i++) if (ms[i].slug === t.member) return ms[i].name || t.member;
    return t.member || "";
  }
  function weekItem(t, teams) {
    var team = (teams[t.team] || {}).name || t.team || "";
    var who = t.member ? memberName(teams, t) : "담당 미정";
    var tag = t.lane === "done" ? "완료"
      : t.lane === "review" ? "확인 대기"
      : t.lane === "doing" ? "진행 중"
      : t.secretary_seen ? "비서실 확인함" : "접수됨";
    var u = safeUrl(t.result_url);
    return '<li class="oc-wk-i' + (t.overdue ? " is-overdue" : "") + '"><span class="oc-wk-t">' + esc(t.title) + "</span>" +
      '<span class="oc-wk-m">' + esc(team) + " · " + esc(who) +
      (t.due_on ? " · " + esc(dayLabel(t.due_on)) + "까지" : "") +
      (t.created_kind === "ceo" ? " · 대표 지시" : "") + "</span>" +
      '<span class="oc-wk-s">' + esc(tag) + (t.overdue ? ' <span class="oc-flag-bad">마감 지남</span>' : "") +
      (u ? ' · <a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">결과</a>' : "") + "</span></li>";
  }
  function renderWeek(data, ctx) {
    if (!data || typeof data !== "object") return "";
    var flow = data.flow || {}, teams = data.teams || {};
    var todo = arr(flow.todo), doing = arr(flow.doing), done = arr(flow.done);
    var col = function (title, list, empty) {
      return '<div class="oc-wk-col"><h5>' + title + " " + list.length + "</h5>" +
        (list.length ? "<ul>" + list.map(function (t) { return weekItem(t, teams); }).join("") + "</ul>"
          : '<p class="oc-muted">' + empty + "</p>") + "</div>";
    };
    return '<details class="oc-card oc-week" data-oc-week="1"' + (ctx && ctx.weekOpen ? " open" : "") + ">" +
      "<summary>이번 주 일 · 지시함 " + todo.length + " · 진행 " + doing.length + " · 완료 " + done.length + "</summary>" +
      '<div class="oc-wk">' + col("지시함", todo, "새 지시 없음") + col("진행", doing, "진행 중인 일이 없습니다") +
      col("이번 주 완료", done, "아직 없음") + "</div></details>";
  }

  // ── 팀과 직원 ────────────────────────────────────────────────────────────────
  function renderTeams(data, ctx) {
    if (!data || typeof data !== "object") return "";
    var teams = data.teams || {}, tree = arr(data.tree), c = { tasks: tasksByMember(data.flow || {}) };
    var head = '<div class="oc-sec"><h2>팀과 직원</h2><span>누가 지금 무슨 일을 맡고 있는지</span>' +
      '<div class="oc-legend"><span class="oc-pill oc-p-work">진행 중</span><span class="oc-pill oc-p-wait">확인 대기</span>' +
      '<span class="oc-pill oc-p-bad">지연</span><span class="oc-pill oc-p-none">배정 없음</span></div>' +
      (tree.length ? '<button type="button" class="oc-pri oc-task-open" data-oc-task-open="1">업무 지시</button>' : "") + "</div>";
    if (!tree.length) return head + '<p class="oc-empty">아직 조직 자료가 없습니다.</p>';
    return head + renderWeek(data, ctx) +
      '<div class="oc-rooms">' + tree.map(function (n) { return room(n, teams, c); }).join("") + "</div>";
  }

  // ── 업무 지시 시트 ─────────────────────────────────────────────────────────────
  // 팀 필수(대상 없는 지시 금지) · 직원은 고르지 않으면 비서실이 정한다 · 제목 1~120자 · 내용 2000자 · 마감 선택.
  // 검사 규칙은 126 ops_task_create 와 같은 선(서버가 최종 판정 — 화면은 먼저 막아 헛걸음을 줄인다).
  var TASK_LIMIT = { title: 120, detail: 2000 };
  var TASK_MSG = {
    no_team: "팀을 골라 주세요. 맡을 팀이 없으면 보낼 수 없습니다",
    title_length: "제목은 1~120자로 적어 주세요",
    detail_length: "내용은 2000자까지 적을 수 있습니다",
    due_past: "마감일이 이미 지났습니다. 오늘 이후로 골라 주세요",
    due_format: "마감일을 달력에서 골라 주세요",
    member_team: "고른 직원이 그 팀에 없습니다"
  };
  // 지시를 받을 수 있는 칸 = 실과 팀(부문은 묶음이라 고르지 않는다). 순서는 조직도 순서.
  function taskTeams(data) {
    var out = [];
    arr(data && data.tree).forEach(function (n) {
      if (n.kind === "office") out.push({ slug: n.slug, name: n.name, group: "실" });
      else arr(n.children).forEach(function (k) { out.push({ slug: k.slug, name: k.name, group: n.name }); });
    });
    return out;
  }
  function teamOptions(data, selected) {
    var html = '<option value="">팀을 고르세요</option>', group = null;
    taskTeams(data).forEach(function (t) {
      if (t.group !== group) { html += (group === null ? "" : "</optgroup>") + '<optgroup label="' + esc(t.group) + '">'; group = t.group; }
      html += '<option value="' + esc(t.slug) + '"' + (t.slug === selected ? " selected" : "") + ">" + esc(t.name) + "</option>";
    });
    return html + (group === null ? "" : "</optgroup>");
  }
  function memberOptions(data, team, selected) {
    var ms = arr(((data && data.teams) || {})[team] && data.teams[team].members).filter(function (m) { return m.state !== "retired"; });
    return '<option value="">' + (team ? "비서실이 정함" : "팀을 먼저 고르세요") + "</option>" + ms.map(function (m) {
      return '<option value="' + esc(m.slug) + '"' + (m.slug === selected ? " selected" : "") + ">" + esc(m.name || m.slug) + "</option>";
    }).join("");
  }
  function kstToday(data) {
    var p = kstParts(data && data.generated_at);
    if (!p) return "";
    return p[0] + "-" + (p[1] < 10 ? "0" : "") + p[1] + "-" + (p[2] < 10 ? "0" : "") + p[2];
  }
  // 입력 → { ok, code, field, message, payload }. payload 는 rpc('ops_task_create') 인자 그대로.
  function validateTask(v, data) {
    v = v || {};
    var team = String(v.team || "").trim(), member = String(v.member || "").trim();
    var title = String(v.title || "").trim(), detail = String(v.detail || "").trim(), due = String(v.due || "").trim();
    var fail = function (code, field) { return { ok: false, code: code, field: field, message: TASK_MSG[code] }; };
    if (!team) return fail("no_team", "team");
    if (data && !taskTeams(data).some(function (t) { return t.slug === team; })) return fail("no_team", "team");
    if (title.length < 1 || title.length > TASK_LIMIT.title) return fail("title_length", "title");
    if (detail.length > TASK_LIMIT.detail) return fail("detail_length", "detail");
    if (due && !/^\d{4}-\d{2}-\d{2}$/.test(due)) return fail("due_format", "due");
    var today = kstToday(data);
    if (due && today && due < today) return fail("due_past", "due");
    if (member && data && !arr(((data.teams || {})[team] || {}).members).some(function (m) { return m.slug === member; })) {
      return fail("member_team", "member");
    }
    return { ok: true, payload: { p_team: team, p_title: title, p_detail: detail || null, p_due: due || null, p_member: member || null } };
  }
  function renderTaskSheet(data, f) {
    f = f || {};
    var team = f.team || "", ready = !!team, today = kstToday(data);
    return '<div class="oc-sheet-back" data-oc-task-close="1"></div>' +
      '<div class="oc-sheet" role="dialog" aria-modal="true" aria-labelledby="grm-oc-task-title">' +
      '<form class="oc-form" id="grm-oc-task-form" novalidate>' +
      '<div class="oc-sheet-head"><h3 id="grm-oc-task-title">업무 지시</h3>' +
      '<button type="button" class="oc-link" data-oc-task-close="1">닫기</button></div>' +
      '<p class="oc-muted">팀을 고르고 할 일을 적어 보내면 지시함에 들어갑니다. 직원을 고르지 않으면 비서실이 정합니다.</p>' +
      '<label for="grm-oc-task-team">팀 <b>필수</b></label>' +
      '<select id="grm-oc-task-team" name="team" required>' + teamOptions(data, team) + "</select>" +
      '<label for="grm-oc-task-member">직원 <small>선택</small></label>' +
      '<select id="grm-oc-task-member" name="member"' + (ready ? "" : " disabled") + ">" + memberOptions(data, team, f.member) + "</select>" +
      '<label for="grm-oc-task-name">제목</label>' +
      '<input id="grm-oc-task-name" name="title" type="text" maxlength="' + TASK_LIMIT.title + '" required autocomplete="off" value="' + esc(f.title || "") + '">' +
      '<label for="grm-oc-task-detail">내용 <small>선택 · 2000자까지</small></label>' +
      '<textarea id="grm-oc-task-detail" name="detail" rows="4" maxlength="' + TASK_LIMIT.detail + '">' + esc(f.detail || "") + "</textarea>" +
      '<label for="grm-oc-task-due">마감일 <small>선택</small></label>' +
      '<input id="grm-oc-task-due" name="due" type="date"' + (today ? ' min="' + today + '"' : "") + ' value="' + esc(f.due || "") + '">' +
      '<p class="oc-why" id="grm-oc-task-why"' + (ready ? " hidden" : "") + ">팀을 골라야 보낼 수 있습니다.</p>" +
      '<p class="oc-err" id="grm-oc-task-err" role="status">' + esc(f.error || "") + "</p>" +
      '<div class="oc-btns oc-sheet-btns"><button type="submit" class="oc-pri" id="grm-oc-task-send"' + (ready ? "" : " disabled") + ">보내기</button>" +
      '<button type="button" data-oc-task-close="1">취소</button></div>' +
      "</form></div>";
  }

  function render(data, ctx) {
    if (!data || typeof data !== "object") return "";
    return '<p class="oc-summary">' + renderSummary(data, ctx) + "</p>" +
      '<section class="oc-card oc-docs">' + renderDocs(data, ctx) + "</section>" + renderTeams(data, ctx);
  }

  // 화면 동작: '발행·운영 열기' 이동, 결재 버튼, '업무 지시' 시트 열기(펼침은 <details> 가 한다).
  // 처리 자체는 admin.js 의 onDecide·onTask.
  function bind(host, onGoto, onDecide, onTask) {
    if (!host || host.__ocBound) return;
    host.__ocBound = true;
    host.addEventListener("click", function (e) {
      var go = e.target.closest ? e.target.closest("[data-oc-goto]") : null;
      if (go) { if (onGoto) onGoto(go.getAttribute("data-oc-goto")); return; }
      var open = e.target.closest ? e.target.closest("[data-oc-task-open]") : null;
      if (open) { if (!open.disabled && onTask) onTask(); return; }
      var b = e.target.closest ? e.target.closest("[data-oc-decide]") : null;
      if (!b || b.disabled || !onDecide) return;
      onDecide(Number(b.getAttribute("data-oc-id")), b.getAttribute("data-oc-decide"), b.getAttribute("data-oc-snooze") || null);
    });
  }

  var api = { render: render, renderSummary: renderSummary, renderDocs: renderDocs, renderTeams: renderTeams,
              renderWeek: renderWeek, renderTaskSheet: renderTaskSheet, teamOptions: teamOptions, memberOptions: memberOptions,
              validateTask: validateTask, taskTeams: taskTeams, TASK_MSG: TASK_MSG,
              bind: bind, approveLabel: approveLabel, memberStatus: memberStatus, dayLabel: dayLabel, daysLeft: daysLeft, clock: clock, STATE_WORD: STATE_WORD };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.GRMOps = api;
})(typeof window !== "undefined" ? window : this);
