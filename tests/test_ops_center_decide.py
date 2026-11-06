#!/usr/bin/env python3
"""운영 센터 PR4 — 125_ops_decision_write.sql 텍스트 계약 + 결정 알림 메일(web/ops_decision_mail.py)·워크플로.

행동(42501·이미 처리·KST 08:00·서비스 함수 권한)은 격리 PostgreSQL 에서 tests/sql/ops_center_decide.sql 이 보고,
뮤테이션은 tests/run_ops_center_sql_mutations.py 가 CI 에서 돌린다. 여기서는 DB 없이 글자·순수 함수로 본다.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_P125 = _ROOT / "web" / "migrations" / "125_ops_decision_write.sql"
_WF = _ROOT / ".github" / "workflows" / "grm-ops-decision-mail.yml"
sys.path.insert(0, str(_ROOT / "web"))
import ops_decision_mail as mail  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
ADMIN = "https://grm-solutions.com/admin/"
_SERVICE = {
    "public.ops_decision_open": "(text, text, text, text, text, text, timestamptz, text)",
    "public.ops_secretary_inbox": "()",
    "public.ops_member_touch": "(text, text, timestamptz)",
    "public.ops_goal_set": "(text, numeric, text, timestamptz)",
    "public.ops_task_move": "(bigint, text, text, text)",
}


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _fn(sql: str, name: str) -> str:
    m = re.search(r"create or replace function " + re.escape(name) + r"\(.*?\$\$(.*?)\$\$;", sql, re.S)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(0)


class Migration125Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _P125.read_text(encoding="utf-8")
        cls.sql = _code(cls.raw)

    def test_file_lf_unique_number_and_header(self):
        self.assertNotIn(b"\r\n", _P125.read_bytes())
        self.assertEqual([p.name for p in _P125.parent.glob("125_*.sql")], [_P125.name])
        self.assertIn("information_schema.routine_privileges", self.raw)
        self.assertIn("drop function if exists public.ops_decide(bigint, text, text);", self.raw)
        for fn, sig in _SERVICE.items():
            self.assertIn(f"drop function if exists {fn}{sig};", self.raw, fn)

    def test_additive_only(self):
        low = " ".join(self.sql.split()).lower()
        for bad in ("create table", "alter table", "drop table", "drop function", "truncate",
                    "function public.ops_home(", "function private.ops_home_at("):
            self.assertNotIn(bad, low, bad)

    def test_ops_decide_admin_guard_and_definer(self):
        f = _code(_fn(self.raw, "public.ops_decide"))
        self.assertIn("security definer", f)
        self.assertIn("set search_path = public", f)
        guard = "if not coalesce(private.is_admin(), false) then"
        self.assertIn(guard, f)
        self.assertIn("errcode = '42501'", f[f.index(guard):])
        self.assertLess(f.index(guard), f.index("private.ops_decide_at("), "검사가 본체 호출보다 먼저")
        self.assertIn("auth.uid()", f)

    def test_state_condition_and_messages(self):
        body = _code(_fn(self.raw, "private.ops_decide_at"))
        self.assertEqual(body.count("where id = p_id and state in ('pending','snoozed')"), 2)
        self.assertIn("decided_at = p_now, decided_by = p_actor", body)
        self.assertIn("'이미 처리된 결정입니다'", body)
        self.assertIn("'승인됨 · 비서실이 이어서 진행합니다'", body)
        self.assertIn("insert into public.admin_audit_log", body)
        self.assertNotIn("reject", body.replace("'approve','snooze'", ""), "반려 없음(D5)")
        self.assertNotIn("security definer", body)

    def test_snooze_is_kst_0800(self):
        f = _code(_fn(self.raw, "private.ops_snooze_until"))
        self.assertIn("immutable", f)
        self.assertEqual(f.count("(p_now at time zone 'Asia/Seoul')::date"), 2)
        self.assertEqual(f.count("time '08:00') at time zone 'Asia/Seoul'"), 2)
        self.assertIn("+ 1)", f)
        self.assertIn("+ 3)", f)

    def test_grants(self):
        g = " ".join(self.sql.split())
        for fn in ("private.ops_snooze_until(timestamptz, text)",
                   "private.ops_decide_at(bigint, text, text, uuid, timestamptz)",
                   "public.ops_decide(bigint, text, text)"):
            self.assertIn(f"revoke all on function {fn} from public, anon, authenticated;", g, fn)
        self.assertIn("grant execute on function public.ops_decide(bigint, text, text) to authenticated;", g)
        self.assertNotRegex(g, r"grant execute on function public\.ops_decide\([^)]*\) to [^;]*anon")
        for fn, sig in _SERVICE.items():
            self.assertIn(f"revoke all on function {fn}{sig} from public, anon, authenticated;", g, fn)
            self.assertIn(f"grant execute on function {fn}{sig} to service_role;", g, fn)
            body = _code(_fn(self.raw, fn))
            self.assertNotIn("security definer", body, f"{fn} 는 invoker(service_role 권한으로 충분)")
            self.assertIn("set search_path = public", body, fn)
        grants = re.findall(r"grant execute on function ([a-z_.]+)\(", g)
        self.assertEqual(sorted(grants), sorted(["public.ops_decide", *_SERVICE]))

    def test_service_guards(self):
        self.assertIn("if coalesce(p_ref_url, '') ~ 'publish/brief-' then", self.sql)
        self.assertIn("if p_lane = 'done' and coalesce(p_result_url, v_task.result_url) is null then", self.sql)
        self.assertIn("if v_unit = '%' and (p_value < 0 or p_value > 100) then", self.sql)

    def test_ci_mutations_target_existing_text(self):
        spec = importlib.util.spec_from_file_location("ops_mut", _ROOT / "tests" / "run_ops_center_sql_mutations.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertGreaterEqual(len(mod.MUTATIONS_DECIDE), 15)
        names = [m[0] for m in mod.MUTATIONS + mod.MUTATIONS_DECIDE]
        self.assertEqual(len(names), len(set(names)))
        for name, path, old, new in mod.MUTATIONS_DECIDE:
            self.assertEqual(path.read_text(encoding="utf-8").count(old), 1, name)
            self.assertNotEqual(old, new, name)
        wf = (_ROOT / ".github" / "workflows" / "grm-ops-center-sql-check.yml").read_text(encoding="utf-8")
        self.assertIn("-f tests/sql/ops_center_decide.sql", wf)
        self.assertIn("web/migrations/*ops_decision*", wf)
        sql = (_ROOT / "tests" / "sql" / "ops_center_decide.sql").read_text(encoding="utf-8")
        self.assertIn("\\ir ../../web/migrations/125_ops_decision_write.sql", sql)
        self.assertIn("rollback;", sql)


def _row(i, created, title="결정", due=None, team="종합상황실", rec="진행"):
    return {"id": i, "title": title, "recommendation": rec, "due_at": due, "created_at": created,
            "ops_team": {"name": team}}


class MailPlanTest(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 6, 9, 10, tzinfo=KST)

    def test_one_mail_per_decision_up_to_three_a_day(self):
        rows = [_row(i, f"2026-11-06T0{i}:00:00+09:00") for i in range(1, 6)]
        mails = mail.plan(rows, [], self.NOW, ADMIN)
        self.assertEqual([m.ids for m in mails], [[1], [2], [3]])
        self.assertFalse(any(m.digest for m in mails))
        sent = ["2026-11-06T08:30:00+09:00", "2026-11-06T08:30:00+09:00", "2026-11-06T09:00:00+09:00"]
        self.assertEqual(mail.batches_sent_today(sent, self.NOW), 2, "같은 시각 = 묶음 1통")
        self.assertEqual([m.ids for m in mail.plan(rows, sent, self.NOW, ADMIN)], [[1]])
        sent3 = sent + ["2026-11-06T09:05:00+09:00"]
        self.assertEqual(mail.plan(rows, sent3, self.NOW, ADMIN), [], "하루 3통 상한")
        self.assertEqual(mail.batches_sent_today(["2026-11-05T23:00:00+09:00"], self.NOW), 0, "어제 보낸 건 안 센다")

    def test_leftovers_go_out_as_one_morning_digest(self):
        rows = [_row(1, "2026-11-05T10:00:00+09:00"), _row(2, "2026-11-05T22:30:00+09:00"),
                _row(3, "2026-11-06T08:50:00+09:00")]
        mails = mail.plan(rows, [], self.NOW, ADMIN)
        self.assertEqual([(m.ids, m.digest) for m in mails], [([1, 2], True), ([3], False)])
        self.assertEqual(mails[0].subject, "[GRM 결재] 결재 서류 2건")
        one_left = mail.plan([_row(1, "2026-11-05T10:00:00+09:00")], [], self.NOW, ADMIN)
        self.assertFalse(one_left[0].digest, "남은 게 1건이면 그냥 1통")

    def test_quiet_hours_kst(self):
        rows = [_row(1, "2026-11-06T07:00:00+09:00")]
        for h, quiet in ((7, True), (8, False), (21, False), (22, True), (0, True)):
            now = dt.datetime(2026, 11, 6, h, 30, tzinfo=KST)
            self.assertEqual(mail.is_quiet(now), quiet, h)
            self.assertEqual(bool(mail.plan(rows, [], now, ADMIN)), not quiet, h)
        # UTC 로 들어와도 KST 로 판단한다(UTC 23:30 = KST 08:30 → 보낸다)
        self.assertFalse(mail.is_quiet(dt.datetime(2026, 11, 5, 23, 30, tzinfo=dt.timezone.utc)))

    def test_message_three_lines_one_button_admin_only_escaped(self):
        m = mail.build_single(_row(7, "2026-11-06T08:00:00+09:00", title="<b>위험</b> & 제목",
                                   due="2026-11-09T00:00:00+00:00"), ADMIN)
        self.assertEqual(m.subject, "[GRM 결재] <b>위험</b> & 제목")  # 제목 줄은 평문(메일 클라이언트가 그대로 보인다)
        self.assertNotIn("<b>위험</b>", m.html)
        self.assertIn("&lt;b&gt;위험&lt;/b&gt; &amp; 제목", m.html)
        self.assertEqual(m.html.count('<p style="margin:0 0 10px'), 3, "본문 3줄")
        self.assertEqual(m.html.count("<a "), 1, "버튼 1개")
        self.assertIn("관리자에서 열기", m.html)
        self.assertEqual(re.findall(r'href="([^"]+)"', m.html), [ADMIN])
        self.assertIn("11-09(월)", m.html)
        self.assertIn("메일로는 승인되지 않습니다", m.html)
        self.assertTrue(mail.build_single(_row(8, "x", title="가" * 60), ADMIN).subject.endswith("…"))

    def test_recipient_is_single_address(self):
        with self.assertRaises(ValueError):
            mail.resolve_recipient("a@x.com, b@x.com")
        self.assertEqual(mail.resolve_recipient("ceo@example.com"), "ceo@example.com")


class MailWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = _WF.read_text(encoding="utf-8")

    def test_dry_run_by_default(self):
        self.assertIn('DRY="--dry-run"', self.wf)
        self.assertIn("ENABLED: ${{ vars.GRM_OPS_DECISION_MAIL_ENABLED }}", self.wf)
        self.assertRegex(self.wf, r"dry_run:\n(?:.*\n)*?\s+default: true")
        # 실발송은 ENABLED 가 켜졌을 때만 — 수동 실행도 마찬가지
        run = self.wf[self.wf.index('DRY="--dry-run"'):]
        self.assertLess(run.index('if [ "$ENABLED" = "true" ]; then'), run.index('DRY=""'))

    def test_schedule_and_concurrency(self):
        self.assertIn("cron: '7,37 * * * *'", self.wf)
        self.assertIn("group: grm-ops-decision-mail", self.wf)
        self.assertIn("cancel-in-progress: false", self.wf)
        self.assertIn("contents: read", self.wf)
        self.assertNotIn("write", self.wf.split("jobs:")[0].split("permissions:")[1])

    def test_existing_secrets_only_and_masked(self):
        secrets = sorted(set(re.findall(r"secrets\.([A-Z_]+)", self.wf)))
        self.assertEqual(secrets, ["NEWSLETTER_API_KEY", "SUPABASE_SERVICE_ROLE_KEY"], "새 비밀값 없음")
        self.assertIn("GRM_APPROVAL_MAIL_TO: ${{ vars.GRM_APPROVAL_MAIL_TO }}", self.wf, "수신자 = 월요일 결재 메일과 같은 변수")
        for v in ("SUPABASE_SERVICE_ROLE_KEY", "NEWSLETTER_API_KEY", "GRM_APPROVAL_MAIL_TO"):
            self.assertIn(f'echo "::add-mask::${v}"', self.wf, v)
        self.assertIn("python web/ops_decision_mail.py $DRY", self.wf)

    def test_script_cap_and_no_title_in_logs(self):
        src = (_ROOT / "web" / "ops_decision_mail.py").read_text(encoding="utf-8")
        self.assertIn("DAILY_CAP = 3", src)
        prints = re.findall(r"print\((.*)\)", src)
        for p in prints:
            self.assertNotIn("subject", p)
            self.assertNotIn("title", p)
            self.assertNotIn("to}", p)
        self.assertIn('"mail_sent_at": "is.null"', src, "이미 보낸 행은 다시 표시하지 않는다")


if __name__ == "__main__":
    unittest.main()
