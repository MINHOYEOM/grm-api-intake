/* 운영 센터 렌더러(web/assets/admin-ops.js) 시험 하네스 — 네트워크·DOM 없음.
 * argv: [2]=admin-ops.js 경로, [3]=ops_home 픽스처 JSON 경로. 시나리오별 HTML 과 자리 판정을 JSON 으로 낸다. */
const path = require("path"), fs = require("fs");
const ops = require(path.resolve(process.argv[2]));
const base = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const clone = () => JSON.parse(JSON.stringify(base));
const out = {};

out.sample = ops.render(clone(), { publish: { title: "#9 brief 발행", meta: "CI 통과" } });

// 전부 정상 · 이상 없음 · 결정 0건
const calm = clone();
calm.anomalies = []; calm.decisions = []; calm.decisions_more = 0; calm.upcoming = [];
calm.summary = { bad: 0, warn: 0, unknown_teams: 0, ok_teams: 11, decisions: 0, idle: 0, no_record: 0, deadlines: 0, overdue: 0 };
calm.tree.forEach(n => { n.state = "ok"; n.reason = ""; (n.children || []).forEach(c => { c.state = "ok"; c.reason = ""; }); });
Object.keys(calm.teams).forEach(k => { calm.teams[k].state = "ok"; calm.teams[k].reason = ""; });
out.calm = ops.render(calm, { publish: null, foldOk: true });

// 문제 1 · 위험한 문자열 이스케이프
const bad = clone();
bad.tree[2].children[0].state = "bad";
bad.tree[2].children[0].reason = "<script>alert(1)</script> 카드 오류 3건";
bad.teams.brief.state = "bad";
bad.decisions[0].title = "<img src=x onerror=alert(1)>";
bad.summary.bad = 1;
out.bad = ops.render(bad, {});

const empty = clone(); empty.tree = [];
out.emptyTree = ops.render(empty, {});
out.nullData = ops.render(null, {});

// 자리 판정은 DB 필드만 본다 — 일수로 다시 판정하지 않는다
const m = (over) => Object.assign({ name: "담당", state: "active", open_tasks: 0, idle: false, no_record: false, last_active_days: 2 }, over);
out.seats = {
  oldButNotIdle: ops.seatStatus(m({ last_active_days: 30, idle: false }), []).word,
  idleRecent: ops.seatStatus(m({ last_active_days: 1, idle: true }), []).word,
  noRecordFlag: ops.seatStatus(m({ last_active_days: null, no_record: true }), []).word,
  nullDays: ops.seatStatus(m({ last_active_days: null }), []).word,
  working: ops.seatStatus(m({ open_tasks: 1 }), [{ title: "일", lane: "doing", overdue: false }]).word,
  blocked: ops.seatStatus(m({ open_tasks: 1 }), [{ title: "일", lane: "inbox", overdue: true }]).word,
  review: ops.seatStatus(m({}), [{ title: "일", lane: "review", overdue: false }]).word,
  paused: ops.seatStatus(m({ state: "paused", idle: true }), []).word,
  free: ops.seatStatus(m({}), []).word
};
out.dayLabel = [ops.dayLabel("2026-11-09"), ops.dayLabel("2026-11-12T16:00:00+00:00")];
out.clock = ops.clock("2026-11-06T09:10:00+09:00");
process.stdout.write(JSON.stringify(out));
