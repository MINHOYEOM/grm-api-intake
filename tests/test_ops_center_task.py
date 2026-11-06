#!/usr/bin/env python3
"""운영 센터 PR5 — 업무 지시(126_ops_task_create.sql) + 시드 추가(127_ops_center_seed_planning.sql) + 지시 시트 화면.

행동(42501·팀 필수·길이·소속·10초 중복·감사 / 124→127 멱등·이름)은 격리 PostgreSQL 에서
tests/sql/ops_center_task.sql·tests/sql/ops_center_seed_planning.sql 이 보고, 뮤테이션은
tests/run_ops_center_sql_mutations.py 가 CI 에서 돌린다. 여기서는 DB 없이 글자·Node 렌더러로 본다.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MIG = _ROOT / "web" / "migrations"
_P126 = _MIG / "126_ops_task_create.sql"
_P127 = _MIG / "127_ops_center_seed_planning.sql"
_P124 = _MIG / "124_ops_center_seed.sql"
_WF = _ROOT / ".github" / "workflows" / "grm-ops-center-sql-check.yml"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ops_center_seed as seed124  # noqa: E402  (VALUES 파서·상태 규칙 재사용)
import test_ops_center_home_ui as home_ui  # noqa: E402  (Node 하네스 재사용)

_PUBLIC = "public.ops_task_create(text, text, text, date, text)"
_BODY = "private.ops_task_create_at(text, text, text, date, text, uuid, timestamptz)"


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _fn(sql: str, name: str) -> str:
    m = re.search(r"create or replace function " + re.escape(name) + r"\(.*?\$\$(.*?)\$\$;", sql, re.S)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(0)


def _mutations():
    spec = importlib.util.spec_from_file_location("ops_mut", _ROOT / "tests" / "run_ops_center_sql_mutations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Migration126Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _P126.read_text(encoding="utf-8")
        cls.sql = _code(cls.raw)
        cls.pub = _code(_fn(cls.raw, "public.ops_task_create"))
        cls.body = _code(_fn(cls.raw, "private.ops_task_create_at"))

    def test_file_lf_unique_number_and_header(self):
        self.assertNotIn(b"\r\n", _P126.read_bytes())
        self.assertEqual([p.name for p in _MIG.glob("126_*.sql")], [_P126.name])
        self.assertIn("information_schema.routine_privileges", self.raw)
        self.assertIn(f"drop function if exists {_PUBLIC};", self.raw)
        self.assertIn(f"drop function if exists {_BODY};", self.raw)

    def test_additive_only(self):
        low = " ".join(self.sql.split()).lower()
        for bad in ("create table", "alter table", "drop table", "drop function", "truncate", "delete from",
                    "update public.", "function public.ops_home(", "function private.ops_home_at(",
                    "function public.ops_decide(", "function public.ops_task_move("):
            self.assertNotIn(bad, low, bad)
        made = re.findall(r"create or replace function ([a-z_]+\.[a-z_]+)\(", self.sql)
        self.assertEqual(sorted(made), ["private.ops_task_create_at", "public.ops_task_create"])

    def test_not_defined_in_other_migrations(self):
        for p in sorted(_MIG.glob("*.sql")):
            if p.name == _P126.name:
                continue
            # 주석 언급(122 머리말의 'PR5 의 ops_task_create')은 괜찮다 — 정의·호출만 없으면 된다
            self.assertNotIn("ops_task_create", _code(p.read_text(encoding="utf-8")), p.name)

    def test_admin_guard_first_and_definer(self):
        f = self.pub
        self.assertIn("security definer", f)
        self.assertIn("set search_path = public", f)
        guard = "if not coalesce(private.is_admin(), false) then"
        self.assertIn(guard, f)
        self.assertIn("errcode = '42501'", f[f.index(guard):])
        self.assertLess(f.index(guard), f.index("private.ops_task_create_at("), "검사가 본체 호출보다 먼저")
        self.assertIn("auth.uid(), now()", f)
        self.assertIn("p_member text default null", _fn(self.raw, "public.ops_task_create"))
        self.assertNotIn("security definer", self.body, "본체는 invoker(화면 함수를 거쳐서만)")
        self.assertIn("set search_path = public", self.body)

    def test_grants(self):
        g = " ".join(self.sql.split())
        for fn in (_PUBLIC, _BODY):
            self.assertIn(f"revoke all on function {fn} from public, anon, authenticated;", g, fn)
        self.assertIn(f"grant execute on function {_PUBLIC} to authenticated;", g)
        self.assertEqual(re.findall(r"grant execute on function ([a-z_.]+)\(", g), ["public.ops_task_create"])
        self.assertNotRegex(g, r"grant execute on function public\.ops_task_create\([^)]*\) to [^;]*anon")

    def test_rules_in_order(self):
        b = self.body
        steps = ["'no_team'", "'unknown_team'", "'not_a_team'", "'title_length'", "'detail_length'", "'due_past'",
                 "'member_team'", "pg_advisory_xact_lock(", "'duplicate'", "insert into public.ops_task",
                 "insert into public.admin_audit_log"]
        idx = [b.index(s) for s in steps]
        self.assertEqual(idx, sorted(idx), "검사 → 잠금 → 중복 → 삽입 → 감사 순서")
        self.assertIn("char_length(v_title) < 1 or char_length(v_title) > 120", b)
        self.assertIn("char_length(v_detail) > 2000", b)
        self.assertIn("p_due < (p_now at time zone 'Asia/Seoul')::date", b)
        self.assertIn("team_id = v_team.id and state <> 'retired'", b)
        self.assertIn("created_at > p_now - interval '10 seconds'", b)
        self.assertIn("v_detail, 'inbox', 'directive', p_actor, 'ceo',", b)
        self.assertIn("'지시했습니다 · 비서실이 읽고 담당을 정합니다'", b)
        self.assertNotIn("now()", b, "본체는 시각을 인자로만 받는다(시험 가능)")

    def test_refusals_return_messages_not_exceptions(self):
        self.assertEqual(self.body.count("raise exception"), 0)
        self.assertEqual(self.body.count("'ok', false"), 8)


class Seed127Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _P127.read_bytes()
        cls.sql = cls.raw.decode("utf-8")
        cls.top = seed124._rows(cls.sql, "top")
        cls.teams = seed124._rows(cls.sql, "teams")
        cls.members = seed124._rows(cls.sql, "members")
        cls.goals = seed124._rows(cls.sql, "goals")
        s124 = _P124.read_text(encoding="utf-8")
        cls.t124 = {r[0]: r for r in seed124._rows(s124, "teams")}
        cls.g124 = {r[1]: r for r in seed124._rows(s124, "goals")}
        cls.top124 = {r[0] for r in seed124._rows(s124, "top")}

    def test_file_number_unique_and_lf(self):
        self.assertEqual([p.name for p in _MIG.glob("127_*.sql")], [_P127.name])
        self.assertNotIn(b"\r\n", self.raw)

    def test_rows_as_ordered(self):
        self.assertEqual(self.top, [("planning", "경영기획실", "office", 0, "회사가 계속 굴러가도록 점검하고 우선순위를 제안합니다")])
        self.assertEqual(self.teams, [
            ("strategy", "제품전략팀", "platform", 1, False, "새 기능을 시험해 보고 쓸지 정합니다"),
            ("support", "독자지원팀", "customer", 3, False, "독자 문의와 제안을 받고 답합니다"),
        ])
        self.assertEqual([r[:6] for r in self.members], [
            ("planning", "plan-review", "경영기획 담당", "session", "주간 경영 점검(예정)", "opus"),
            ("planning", "plan-policy", "법무·정책 담당", "session", "처리방침·저작권 점검(예정)", "opus"),
            ("support", "support-desk", "문의 응대 담당", "session", "문의·제안 채널(예정)", "sonnet"),
        ])
        g = {r[1]: r for r in self.goals}
        # 팀, slug, 이름, 단위, 방향, 목표, 주의, 문제, 값, 측정, 주기, 방식, 순서
        self.assertEqual(g["plan-review-gap"][:13], ("planning", "plan-review-gap", "마지막 경영 점검 후 경과", "일", "lte", 7, 10, 14, None, None, 168, "manual", 1))
        self.assertEqual(g["plan-risks-open"][:13], ("planning", "plan-risks-open", "대응 안 된 높은 위험", "건", "lte", 0, None, 1, None, None, 168, "manual", 2))
        self.assertEqual(g["support-reply"][:13], ("support", "support-reply", "문의 답변까지 걸린 시간", "시간", "lte", 48, 72, 168, None, None, 168, "manual", 1))
        self.assertEqual(g["strat-adopted"][2], "채택한 아이디어 수")

    def test_strategy_changes_only_name_and_pilot(self):
        new, old = self.teams[0], self.t124["strategy"]
        self.assertEqual(old[1], "제품전략팀(시범)", "124 는 고치지 않는다")
        self.assertTrue(old[4])
        self.assertEqual((new[0], new[2], new[3], new[5]), (old[0], old[2], old[3], old[5]))
        g_new, g_old = {r[1]: r for r in self.goals}["strat-adopted"], self.g124["strat-adopted"]
        self.assertEqual(g_new[:2] + g_new[3:], g_old[:2] + g_old[3:], "strat-adopted 는 이름만 바뀐다")

    def test_constraints_and_no_invented_values(self):
        for slug, name, kind, _o, blurb in self.top:
            self.assertNotIn(slug, self.top124)
            self.assertTrue(len(name) <= 30 and blurb and len(blurb) <= 60)
        for slug, name, parent, _o, _p, blurb in self.teams:
            self.assertIn(parent, ("research", "platform", "customer"))
            self.assertTrue(len(name) <= 30 and blurb and len(blurb) <= 60, slug)
        for team, slug, name, kind, ref, model, exempt, last, note in self.members:
            self.assertRegex(name, r"담당$")
            self.assertTrue(ref and len(ref) <= 120 and len(model) <= 20)
            self.assertFalse(exempt, f"{slug}: 운영 DB 값을 모르면 기본값(false) 그대로")
            self.assertIsNone(last, f"{slug}: 활동 일수를 지어내지 않는다")
            self.assertIsNone(note)
        for (team, slug, label, unit, d, t, w, b, val, measured, period, mkind, ord_, note) in self.goals:
            self.assertTrue(1 <= len(label) <= 60 and len(unit) <= 8 and note and len(note) <= 120, slug)
            self.assertIn(period, (24, 168, 744))
            pairs = [(b, w), (w, t), (b, t)] if d == "gte" else [(t, w), (w, b), (t, b)]
            for lo, hi in pairs:
                if lo is not None and hi is not None:
                    self.assertLessEqual(lo, hi, f"{slug}: 선 순서 위반")
            self.assertEqual(val is None, measured is None, slug)
            if slug != "strat-adopted":
                self.assertIsNone(val, f"{slug}: 아직 안 잰 목표는 값 없음")
        self.assertNotIn("now()", _code(self.sql))

    def test_idempotent_and_newer_values_win(self):
        code = _code(self.sql).lower()
        inserts = re.findall(r"insert into public\.(ops_\w+)", code)
        self.assertEqual(inserts, ["ops_team", "ops_team", "ops_member", "ops_goal"])
        self.assertEqual(code.count("on conflict (slug) do update set"), len(inserts))
        flat = " ".join(_code(self.sql).split())
        self.assertIn("ops_goal.measured_at <= excluded.measured_at", flat)
        self.assertIn("ops_member.last_active_at <= excluded.last_active_at", flat)
        tail = flat.split("insert into public.ops_goal", 1)[1].split("on conflict", 1)[1]
        self.assertNotIn("current_value = excluded.current_value", tail)
        for bad in ("create ", "drop ", "alter ", "truncate", "delete from", "grant ", "revoke ",
                    "ops_task", "ops_decision", "ops_reports", "update public."):
            self.assertNotIn(bad, code, bad)

    def test_header_order_and_rollback(self):
        head = self.sql.split("-- seed:top begin", 1)[0]
        self.assertIn("124 → 127", head)
        self.assertIn("124 를 다시 돌린 뒤에는 127 도 다시 돌린다", head)
        rb = head.split("되돌리기", 1)[1]
        listed = set(re.findall(r"'([a-z0-9-]+)'", rb))
        added = {r[0] for r in self.top} | {"support"} | {r[1] for r in self.members} | {
            "plan-review-gap", "plan-risks-open", "support-reply"}
        self.assertTrue(added <= listed, added - listed)
        self.assertIn("set name = '제품전략팀(시범)', is_pilot = true where slug = 'strategy'", rb)
        self.assertIn("set label = '시범 채택 건수' where slug = 'strat-adopted'", rb)


class SqlChecksRegisteredTest(unittest.TestCase):
    def test_mutations_target_existing_text(self):
        mod = _mutations()
        self.assertGreaterEqual(len(mod.MUTATIONS_TASK), 20)
        self.assertGreaterEqual(len(mod.MUTATIONS_PLAN), 6)
        names = [m[0] for m in mod.MUTATIONS + mod.MUTATIONS_DECIDE + mod.MUTATIONS_TASK + mod.MUTATIONS_PLAN]
        self.assertEqual(len(names), len(set(names)))
        for name, path, old, new in mod.MUTATIONS_TASK + mod.MUTATIONS_PLAN:
            self.assertEqual(path.read_text(encoding="utf-8").count(old), 1, name)
            self.assertNotEqual(old, new, name)
        src = (_ROOT / "tests" / "run_ops_center_sql_mutations.py").read_text(encoding="utf-8")
        self.assertIn("(SQL_TASK, MUTATIONS_TASK), (SQL_PLAN, MUTATIONS_PLAN)", src)
        self.assertIn("M126, M127)}", src)

    def test_workflow_runs_both_sql_files(self):
        wf = _WF.read_text(encoding="utf-8")
        head = wf.split("jobs:", 1)[0]
        for f in ("tests/sql/ops_center_task.sql", "tests/sql/ops_center_seed_planning.sql"):
            self.assertIn(f"-f {f}", wf)
            self.assertIn(f, head)
        self.assertIn("web/migrations/*ops_task*", head)
        self.assertIn("web/migrations/*ops_center*", head, "127 은 ops_center 이름으로 걸린다")
        t = (_ROOT / "tests" / "sql" / "ops_center_task.sql").read_text(encoding="utf-8")
        self.assertIn("\\ir ../../web/migrations/126_ops_task_create.sql", t)
        self.assertIn("rollback;", t)
        p = (_ROOT / "tests" / "sql" / "ops_center_seed_planning.sql").read_text(encoding="utf-8")
        self.assertEqual(p.count("\\ir ../../web/migrations/127_ops_center_seed_planning.sql"), 3)
        self.assertEqual(p.count("\\ir ../../web/migrations/124_ops_center_seed.sql"), 2)
        self.assertLess(p.index("124_ops_center_seed.sql"), p.index("127_ops_center_seed_planning.sql"), "124 → 127 순서")
        self.assertIn("rollback;", p)


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class TaskSheetRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = home_ui._run()
        cls.t, cls.task_calls = out["task"], out["taskCalls"]

    def test_week_fold_shows_inbox_count_and_items(self):
        w = self.t["weekClosed"]
        self.assertTrue(w.startswith('<details class="oc-card oc-week" data-oc-week="1">'), "기본은 접힘")
        self.assertIn("<summary>이번 주 일 · 지시함 3 · 진행 3 · 완료 1</summary>", w)
        self.assertIn("<h5>지시함 3</h5>", w)
        self.assertIn("구독 독려 메일 명단 정리", w, "담당이 없는 대표 지시도 보인다")
        self.assertIn("대외홍보팀 · 담당 미정 · 대표 지시", w)
        self.assertIn('<span class="oc-wk-s">접수됨</span>', w, "비서실이 읽기 전 = 접수됨")
        self.assertIn("비서실 확인함", w)
        self.assertIn('<span class="oc-flag-bad">마감 지남</span>', w)
        self.assertIn("확인 대기", w)
        self.assertTrue(self.t["weekOpen"].startswith('<details class="oc-card oc-week" data-oc-week="1" open>'), "지시 직후엔 펼쳐서")
        self.assertIn("새 지시 없음", self.t["weekEmpty"])
        self.assertIn("지시함 0 · 진행 0 · 완료 0", self.t["weekEmpty"])

    def test_button_in_team_head_only_when_org_exists(self):
        h = self.t["teamsWithButton"]
        head = h[:h.index("</div>", h.index('class="oc-legend"') + 1) + 200]
        self.assertIn('<button type="button" class="oc-pri oc-task-open" data-oc-task-open="1">업무 지시</button>', head)
        self.assertEqual(h.count("data-oc-task-open"), 1)
        self.assertLess(h.index("data-oc-task-open"), h.index('class="oc-card oc-week"'))
        self.assertLess(h.index('class="oc-card oc-week"'), h.index('class="oc-rooms"'))
        self.assertNotIn("data-oc-task-open", self.t["teamsEmptyTree"], "조직 자료가 없으면 보낼 곳도 없다")
        self.assertEqual(self.task_calls, ["task"], "bind 가 업무 지시 열기를 넘긴다")

    def test_sheet_requires_team(self):
        s = self.t["sheetEmpty"]
        self.assertIn('role="dialog" aria-modal="true"', s)
        self.assertIn('<option value="">팀을 고르세요</option>', s)
        self.assertIn('<select id="grm-oc-task-member" name="member" disabled>', s)
        self.assertIn('id="grm-oc-task-send" disabled>보내기</button>', s, "팀이 비면 보내기 잠금")
        self.assertIn('<p class="oc-why" id="grm-oc-task-why">팀을 골라야 보낼 수 있습니다.</p>', s, "막힌 이유 한 줄")
        self.assertIn('maxlength="120"', s)
        self.assertIn('maxlength="2000"', s)
        self.assertIn('type="date" min="2026-11-06"', s, "마감 최소 = 서버 기준 KST 오늘")
        for label in ("팀 <b>필수</b>", "직원 <small>선택</small>", "제목", "내용", "마감일"):
            self.assertIn(label, s)
        t = self.t["sheetTeam"]
        self.assertIn('id="grm-oc-task-send">보내기</button>', t)
        self.assertIn('id="grm-oc-task-why" hidden>', t)
        self.assertIn('<option value="pr-subscribe-nudge" selected>', t)
        self.assertIn('value="&lt;b&gt;명단&lt;/b&gt;"', t, "입력값 이스케이프")

    def test_team_options_offices_and_teams_not_divisions(self):
        self.assertEqual(self.t["teamSlugs"], ["situation-room", "verification", "brief", "translation", "education",
                                               "strategy", "dev", "quality", "infra", "outreach", "growth"])
        self.assertIn('<optgroup label="실">', self.t["sheetEmpty"])
        self.assertNotIn('value="customer"', self.t["sheetEmpty"])
        self.assertIn("팀을 먼저 고르세요", self.t["memberNone"])
        self.assertEqual(re.findall(r'value="([^"]*)"', self.t["memberOutreach"]), ["", "pr-linkedin-cards", "pr-subscribe-nudge"])
        self.assertIn(">비서실이 정함<", self.t["memberOutreach"])

    def test_validation_matches_126(self):
        v = self.t["validate"]
        for k in ("noTeam", "blankTeam", "division", "unknownTeam"):
            self.assertEqual((v[k]["ok"], v[k]["code"], v[k]["field"]), (False, "no_team", "team"), k)
        self.assertEqual(v["noTeam"]["message"], "팀을 골라 주세요. 맡을 팀이 없으면 보낼 수 없습니다")
        self.assertEqual(v["noTitle"]["code"], "title_length")
        self.assertTrue(v["title120"]["ok"])
        self.assertEqual(v["title121"]["code"], "title_length")
        self.assertEqual(v["detail2001"]["code"], "detail_length")
        self.assertEqual(v["duePast"]["code"], "due_past")
        self.assertTrue(v["dueToday"]["ok"])
        self.assertEqual(v["dueBad"]["code"], "due_format")
        self.assertEqual(v["otherMember"]["code"], "member_team")
        self.assertEqual(v["full"]["payload"], {"p_team": "outreach", "p_title": "카드 7장", "p_detail": None,
                                                "p_due": "2026-11-09", "p_member": "pr-linkedin-cards"})
        self.assertTrue(v["office"]["ok"], "실에도 지시할 수 있다")
        sql = _P126.read_text(encoding="utf-8")
        for code in ("no_team", "title_length", "detail_length", "due_past", "member_team"):
            self.assertIn(f"'{code}'", sql, f"화면 코드 {code} 가 126 에도 있다")

    def test_design_bans(self):
        for k in ("weekClosed", "sheetEmpty", "sheetTeam", "teamsWithButton"):
            h = self.t[k]
            self.assertNotRegex(h, r"gradient|box-shadow|text-shadow", k)
            self.assertIsNone(home_ui.EMOJI_RE.search(h), k)
            for w in home_ui.BANNED_WORDS:
                self.assertNotIn(w, h, k)


class TaskWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (_ROOT / "web" / "assets" / "admin.js").read_text(encoding="utf-8")
        cls.html = (_ROOT / "web" / "templates" / "admin.html").read_text(encoding="utf-8")
        body = cls.js[cls.js.index("function opsTaskSend("):]
        cls.send = body[:body.index("\n  }\n") + 4]

    def test_rpc_once_with_locks(self):
        self.assertEqual(self.js.count('rpc("ops_task_create"'), 1)
        self.assertIn('state.client.rpc("ops_task_create", p)', self.send)
        self.assertIn("if (!form || st.busy) return Promise.resolve();", self.send, "보내는 중 재전송 무시")
        self.assertLess(self.send.index("st.busy = true;"), self.send.index('rpc("ops_task_create"'), "호출 전에 잠근다")
        self.assertLess(self.send.index("send.disabled = true"), self.send.index('rpc("ops_task_create"'))
        self.assertLess(self.send.index("window.GRMOps.validateTask("), self.send.index("st.busy = true;"), "화면 검사가 먼저")
        self.assertIn("OPS_TASK_DUP_MS = 10000", self.js)
        self.assertIn("Date.now() - st.last.at < OPS_TASK_DUP_MS", self.send)
        self.assertIn('"42501"', self.send)
        self.assertIn("지시했습니다 · 비서실이 읽고 담당을 정합니다", self.send)
        self.assertIn("state.opsWeekOpen = true;", self.send)
        self.assertIn("opsTaskClose(); loadOpsHome();", self.send, "성공 후 홈 새로고침")
        self.assertIn("weekOpen: !!state.opsWeekOpen", self.js)
        self.assertIn('form.elements.namedItem(name)', self.send, "form.title 은 폼 속성과 겹친다")

    def test_team_change_unlocks_send(self):
        sync = self.js[self.js.index("function opsTaskSync("):]
        sync = sync[:sync.index("\n  }\n")]
        self.assertIn("send.disabled = !ready || state.opsTask.busy;", sync)
        self.assertIn("why.hidden = ready;", sync)
        self.assertIn("window.GRMOps.memberOptions(state.opsHome, team.value, keep)", sync)

    def test_sheet_host_inside_wrap_and_hidden(self):
        home = home_ui._home_panel(self.html)
        wrap = home[home.index('id="grm-oc-wrap"'):home.index('id="grm-home-legacy"')]
        self.assertIn('<div id="grm-oc-sheet" hidden></div>', wrap, "토큰을 받도록 oc-wrap 안")
        self.assertIn("#grm-oc-sheet[hidden]{display:none}", self.html, "hidden 은 author display 에 진다")
        self.assertIn(".oc-why[hidden]", self.html)

    def test_css_no_gradient_or_shadow_and_phone_sheet(self):
        css = self.html[self.html.index(".oc-sec .oc-task-open"):self.html.index(".oc-sheet-btns button")]
        self.assertNotRegex(css, r"gradient|shadow")
        self.assertIn(".oc-sheet{top:auto;left:0;width:100%;max-height:92vh", self.html, "폰은 아래에서 올라오는 한 열 시트")
        self.assertIn(".oc-wk{grid-template-columns:minmax(0,1fr)}", self.html, "폰은 이번 주 일도 한 열")


if __name__ == "__main__":
    unittest.main()
