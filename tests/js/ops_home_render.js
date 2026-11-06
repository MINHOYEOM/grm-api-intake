/* 운영 센터 렌더러(web/assets/admin-ops.js) 시험 하네스 — 네트워크·DOM 없음.
 * argv: [2]=admin-ops.js 경로, [3]=ops_home 픽스처 JSON 경로. 시나리오별 HTML 과 직원 판정을 JSON 으로 낸다. */
const path = require("path"), fs = require("fs");
const ops = require(path.resolve(process.argv[2]));
const base = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const clone = () => JSON.parse(JSON.stringify(base));
const parts = (d, ctx) => ({ all: ops.render(d, ctx), summary: ops.renderSummary(d, ctx), docs: ops.renderDocs(d, ctx), teams: ops.renderTeams(d, ctx) });
const out = {};

out.sample = parts(clone(), { publish: { title: "#9 brief 발행", meta: "CI 통과" } });

// 전부 정상 · 결정 0건 · 배정 없는 직원 0
const calm = clone();
calm.anomalies = []; calm.decisions = []; calm.decisions_more = 0; calm.upcoming = [];
calm.summary = { bad: 0, warn: 0, unknown_teams: 0, ok_teams: 11, decisions: 0, idle: 0, no_record: 0, deadlines: 0, overdue: 0 };
calm.tree.forEach(n => { n.state = "ok"; n.reason = ""; (n.children || []).forEach(c => { c.state = "ok"; c.reason = ""; }); });
Object.keys(calm.teams).forEach(k => { calm.teams[k].state = "ok"; calm.teams[k].reason = ""; });
out.calm = parts(calm, { publish: null });

// 문제 1 · 위험한 문자열 이스케이프
const bad = clone();
bad.tree[2].children[0].state = "bad";
bad.tree[2].children[0].reason = "<script>alert(1)</script> 카드 오류 3건";
bad.teams.brief.state = "bad";
bad.decisions[0].title = "<img src=x onerror=alert(1)>";
bad.summary.bad = 1;
out.bad = parts(bad, {});

const empty = clone(); empty.tree = [];
out.emptyTree = parts(empty, {});
out.nullData = ops.render(null, {});

// 결재 버튼 상태: 처리 중(잠금) · 처리됨(버튼 사라짐) · 오류(문구)
out.decide = {
  busy: ops.renderDocs(clone(), { decide: { busy: { 2: true }, done: {}, errors: {} } }),
  done: ops.renderDocs(clone(), { decide: { busy: {}, done: { 2: "승인됨 · 비서실이 이어서 진행합니다" }, errors: {} } }),
  error: ops.renderDocs(clone(), { decide: { busy: {}, done: {}, errors: { 1: "처리하지 못했습니다. 잠시 뒤 다시 눌러 주세요." } } }),
  labels: [ops.approveLabel({ reason: "approve", recommendation: "진행" }), ops.approveLabel({ reason: "ask", recommendation: "유지" }),
           ops.approveLabel({ reason: "ask", recommendation: "11-09~13 클라우드 정상 확인 후 끔" }), ops.approveLabel({ reason: "ask" })]
};
// bind: 버튼을 누르면 onDecide(id, action, snooze) 가 불린다(가짜 DOM)
const calls = [];
const btn = (attrs) => ({ disabled: !!attrs.disabled, getAttribute: k => attrs[k] == null ? null : String(attrs[k]),
                          closest: sel => (sel === "[data-oc-decide]" ? btn(attrs) : null) });
const host = { addEventListener: (_, fn) => { host.fn = fn; } };
ops.bind(host, () => calls.push(["goto"]), (id, action, snooze) => calls.push([id, action, snooze]));
host.fn({ target: btn({ "data-oc-decide": "approve", "data-oc-id": 2 }) });
host.fn({ target: btn({ "data-oc-decide": "snooze", "data-oc-id": 1, "data-oc-snooze": "3d" }) });
host.fn({ target: btn({ "data-oc-decide": "approve", "data-oc-id": 3, disabled: true }) });
out.bindCalls = calls;

// 직원 판정은 DB 필드만 본다 — 일수로 다시 판정하지 않는다
const m = (over) => Object.assign({ name: "담당", state: "active", open_tasks: 0, idle: false, no_record: false, last_active_days: 2, last_active_note: null }, over);
const st = (mm, tasks) => { const s = ops.memberStatus(mm, tasks || []); return [s.tag, s.text, !!s.idle]; };
out.members = {
  oldButNotIdle: st(m({ last_active_days: 30, idle: false })),
  idleRecent: st(m({ last_active_days: 1, idle: true })),
  noRecordFlag: st(m({ last_active_days: null, no_record: true })),
  nullDays: st(m({ last_active_days: null })),
  working: st(m({ open_tasks: 1 }), [{ title: "카드 고르기", lane: "doing", overdue: false }]),
  late: st(m({ open_tasks: 1 }), [{ title: "원문 확인", lane: "inbox", overdue: true }]),
  review: st(m({}), [{ title: "유입 보고", lane: "review", overdue: false }]),
  paused: st(m({ state: "paused", idle: true })),
  note: st(m({ last_active_note: "11-05 PR" }))
};
out.dayLabel = [ops.dayLabel("2026-11-09"), ops.dayLabel("2026-11-12T16:00:00+00:00")];
out.daysLeft = [ops.daysLeft("2026-11-09T00:00:00+00:00", "2026-11-06T09:10:00+09:00"), ops.daysLeft("2026-11-06", "2026-11-06T09:10:00+09:00")];
out.clock = ops.clock("2026-11-06T09:10:00+09:00");
process.stdout.write(JSON.stringify(out));
