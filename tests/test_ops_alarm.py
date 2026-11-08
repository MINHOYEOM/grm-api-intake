#!/usr/bin/env python3
"""경보 이슈 -> 운영 센터 업무 (131 + web/ops_alarm_sync.py + grm-ops-alarm.yml).

DB 없이 본다: 라벨->팀 표 고정, 경보 판정, 동기화 계획(멱등 입력), 이벤트 해석, 마이그레이션 텍스트 계약,
워크플로 안전 장치, 화면 렌더(Node). 행동(멱등·닫힘·재오픈·3일 경계·ops_home 가산)은 격리 PostgreSQL 에서
tests/sql/ops_center_alarm.sql 이 보고, 뮤테이션은 tests/run_ops_center_sql_mutations.py 가 CI 에서 돌린다.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
import ops_alarm_sync as al  # noqa: E402

MIG = ROOT / "web" / "migrations" / "131_ops_alarm_tasks.sql"
WF = ROOT / ".github" / "workflows" / "grm-ops-alarm.yml"
OPS_JS = ROOT / "web" / "assets" / "admin-ops.js"
FIXTURE = ROOT / "tests" / "fixtures" / "ops_home_sample.json"


def _code(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _issue(n, labels, state="open", title="t", pr=False):
    d = {"number": n, "title": title, "html_url": f"https://github.com/MINHOYEOM/grm-api-intake/issues/{n}",
         "state": state, "labels": [{"name": x} for x in labels]}
    if pr:
        d["pull_request"] = {}
    return al.issue_view(d)


class MappingTest(unittest.TestCase):
    """라벨 -> 팀 표. 바꾸면 이 표가 같이 바뀌어야 한다(고정)."""

    CASES = [
        (["reconciliation", "reconcile-mfds"], "infra"),        # 원천 대조 경보
        (["reconciliation"], "infra"),
        (["reconcile-fda"], "infra"),                           # reconcile-<소스> 접두
        (["reconciliation", "reconcile-monitor"], "dev"),       # 감시 자체 실패는 개발팀(접두 규칙보다 위)
        (["reconcile-monitor"], "dev"),
        (["automation-failure"], "infra"),
        (["automation-warning"], "infra"),
        (["grm-intake", "automation-failure"], "infra"),
        (["source-verify"], "infra"),
        (["automation-warning", "grm-fda-coverage"], "brief"),  # 발행 전 점검은 수집 계열 라벨보다 우선
        (["grm-intake", "automation-warning", "grm-publish-watchdog"], "brief"),
        (["checklist-verify"], "brief"),
        (["newsletter-freshness"], "brief"),
        (["quiz-freshness"], "education"),
        (["growth-weekly"], "growth"),
        (["findings-backlog"], "translation"),
        (["grm-findings", "automation-failure"], "quality"),    # 지적사항 파이프라인 실패는 데이터품질팀
        (["taxonomy-drift"], "quality"),
        (["classification-audit"], "quality"),
        (["grm-brand-new-monitor"], "situation-room"),          # 표에 없는 경보 -> 종합상황실
        (["automation-new-thing"], "situation-room"),
    ]

    def test_table(self):
        for labels, team in self.CASES:
            self.assertTrue(al.is_alarm(labels), labels)
            self.assertEqual(al.team_for(labels), team, labels)

    def test_label_order_does_not_matter(self):
        self.assertEqual(al.team_for(["grm-fda-coverage", "automation-warning"]), "brief")
        self.assertEqual(al.team_for(["reconcile-monitor", "reconciliation"]), "dev")

    def test_non_alarm_labels(self):
        for labels in ([], ["bug"], ["enhancement", "question"], ["dependencies", "python"]):
            self.assertFalse(al.is_alarm(labels), labels)

    def test_every_target_team_exists_in_seed(self):
        seed = (ROOT / "web" / "migrations" / "124_ops_center_seed.sql").read_text(encoding="utf-8")
        teams = {t for _, t in al.LABEL_TEAM} | {t for _, t in al.PREFIX_TEAM} | {al.FALLBACK_TEAM}
        for t in teams:
            self.assertRegex(seed, r"\('" + re.escape(t) + r"',", t)

    def test_labels_in_table_are_real(self):
        """표의 라벨이 워크플로가 실제로 쓰는 라벨인지(오타 방지) — 저장소 라벨 + 워크플로 문자열 전수."""
        blob = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.yml"))
        blob += (ROOT / "source_reconcile.py").read_text(encoding="utf-8")
        for label, _ in al.LABEL_TEAM:
            self.assertIn(label, blob, label)

    def test_reconcile_monitor_rule_above_prefix(self):
        names = [k for k, _ in al.LABEL_TEAM]
        self.assertIn("reconcile-monitor", names)
        self.assertEqual(al.team_for(["reconcile-monitor", "reconcile-mfds"]), "dev")


class PlanTest(unittest.TestCase):
    def test_sync_plan(self):
        issues = [_issue(1274, ["reconciliation"]), _issue(1267, ["automation-warning", "grm-fda-coverage"]),
                  _issue(1255, ["automation-failure"]), _issue(1214, ["source-verify"]),
                  _issue(5, ["bug"]), _issue(6, ["automation-failure"], pr=True)]
        plan = al.plan_sync(issues, {1214, 999})
        self.assertEqual(plan["to_upsert"], [1214, 1255, 1267, 1274])       # 일괄 등록 대상 4건 · 버그/PR 제외
        self.assertEqual(plan["to_close_check"], [999])                      # DB 엔 열림인데 목록에 없다 -> 닫혔는지 확인
        self.assertEqual(al.plan_sync(issues, {1214, 999}), plan, "같은 입력은 같은 계획(멱등)")

    def test_rpc_args_same_for_same_issue(self):
        a = al.rpc_args(_issue(1274, ["reconciliation", "reconcile-mfds"], title="낙차"))
        b = al.rpc_args(_issue(1274, ["reconcile-mfds", "reconciliation"], title="낙차"))
        self.assertEqual(a, b, "라벨 순서가 달라도 같은 호출 -> 같은 업무")
        self.assertEqual(a["p_issue"], 1274)
        self.assertEqual(a["p_team"], "infra")
        self.assertEqual(a["p_state"], "open")
        self.assertTrue(a["p_url"].endswith("/issues/1274"))

    def test_closed_and_reopened_state(self):
        self.assertEqual(al.rpc_args(_issue(7, ["automation-failure"], state="closed"))["p_state"], "closed")
        self.assertEqual(al.rpc_args(_issue(7, ["automation-failure"], state="open"))["p_state"], "open")

    def test_neglect_comment_has_marker(self):
        c = al.neglect_comment(4, "infra")
        self.assertIn(al.NEGLECT_MARK, c)
        self.assertIn("4일째", c)


class MainTest(unittest.TestCase):
    def test_event_dry_run_and_skip(self):
        import os
        import tempfile
        env = {k: os.environ.get(k) for k in ("GITHUB_EVENT_PATH", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        try:
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            with tempfile.TemporaryDirectory() as d:
                for name, issue, want in (
                        ("alarm", {"number": 9, "title": "경보", "html_url": "https://github.com/a/b/issues/9", "state": "open",
                                   "labels": [{"name": "reconciliation"}]}, "[dry-run] #9 open"),
                        ("plain", {"number": 10, "title": "버그", "html_url": "https://github.com/a/b/issues/10", "state": "open",
                                   "labels": [{"name": "bug"}]}, "경보 라벨 없음")):
                    p = Path(d) / f"{name}.json"
                    p.write_text(json.dumps({"issue": issue}), encoding="utf-8")
                    os.environ["GITHUB_EVENT_PATH"] = str(p)
                    import io
                    import contextlib
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        self.assertEqual(al.main(["--mode", "event", "--dry-run"]), 0)
                    self.assertIn(want, buf.getvalue(), name)
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_stdout_guard_in_main(self):
        src = (ROOT / "web" / "ops_alarm_sync.py").read_text(encoding="utf-8")
        self.assertIn('stream.reconfigure(encoding="utf-8", errors="replace")', src)


class MigrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = MIG.read_text(encoding="utf-8")
        cls.sql = _code(cls.raw)
        cls.norm = " ".join(cls.sql.split())

    def test_file(self):
        self.assertNotIn(b"\r\n", MIG.read_bytes())
        self.assertEqual([p.name for p in MIG.parent.glob("131_*.sql")], [MIG.name])
        self.assertGreaterEqual(self.raw.count("--"), 30)
        self.assertIn("information_schema.routine_privileges", self.raw)
        self.assertIn("drop table if exists public.ops_alarm;", self.raw)

    def test_additive_only(self):
        for bad in ("drop table", "alter table public.ops_task", "alter table public.ops_team", "truncate", "delete from",
                    "insert into public.ops_team", "insert into public.ops_member", "cron.", "create or replace function private.ops_home_at"):
            self.assertNotIn(bad, self.norm, bad)
        made = re.findall(r"create or replace function ([a-z_]+\.[a-z_]+)\(", self.sql)
        self.assertEqual(sorted(made), ["private.ops_alarm_summary", "public.ops_alarm_neglect_scan", "public.ops_alarm_neglected",
                                        "public.ops_alarm_upsert", "public.ops_home"])
        self.assertEqual(re.findall(r"create table if not exists public\.(\w+)", self.sql), ["ops_alarm"])

    def test_table_locked_to_service_role(self):
        n = self.norm
        self.assertIn("alter table public.ops_alarm enable row level security;", n)
        self.assertIn("revoke all on public.ops_alarm from public, anon, authenticated;", n)
        self.assertIn("grant all on public.ops_alarm to service_role;", n)
        self.assertNotIn("create policy", n, "정책 없음 = 회원·관리자 토큰으로도 직접 못 읽음")
        self.assertNotIn("to authenticated", n.split("grant all on public.ops_alarm")[1].split("create or replace")[0])

    def test_function_grants(self):
        n = self.norm
        up = "public.ops_alarm_upsert(integer, text, text, text, text[], text, timestamptz)"
        for fn in (up, "public.ops_alarm_neglected(timestamptz)", "public.ops_alarm_neglect_scan(timestamptz)"):
            self.assertIn(f"revoke all on function {fn} from public, anon, authenticated;", n, fn)
            self.assertIn(f"grant execute on function {fn} to service_role;", n, fn)
        self.assertIn("revoke all on function private.ops_alarm_summary(timestamptz) from public, anon, authenticated;", n)
        self.assertNotIn("grant execute on function private.ops_alarm_summary", n)
        self.assertIn("grant execute on function public.ops_home() to authenticated;", n)
        for m in re.finditer(r"grant execute on function ([^;]*?) to ([^;]*);", n):
            if "ops_home" not in m.group(1):
                self.assertEqual(m.group(2).strip(), "service_role", m.group(0))
        self.assertEqual(self.sql.count("set search_path = public"), 5)

    def test_ops_home_additive_and_guarded(self):
        n = self.norm
        self.assertIn("if not coalesce(private.is_admin(), false) then raise exception 'ops_home: admin only' using errcode = '42501';", n)
        self.assertIn("return private.ops_home_at(now()) || jsonb_build_object('alarms', private.ops_alarm_summary(now()));", n)
        head = n.split("create or replace function public.ops_home()")[1].split("$$")[0]
        for tok in ("returns jsonb", "language plpgsql", "stable", "security definer", "set search_path = public"):
            self.assertIn(tok, head)
        # 반환 shape 가산만: 123 본체는 그대로, alarms 요약 키 고정
        self.assertIn("'open'", n)
        self.assertIn("'neglected'", n)
        self.assertIn("'neglect_days', 3", n)
        self.assertIn("'items'", n)

    def test_neglect_defined_in_one_place(self):
        self.assertEqual(self.sql.count("interval '3 days'"), 1)
        self.assertIn("p_now - t.updated_at > interval '3 days'", self.norm)

    def test_idempotency_key_and_url_guard(self):
        n = self.norm
        self.assertIn("issue_number integer primary key", n)
        self.assertIn("task_id bigint not null unique references public.ops_task(id)", n)
        self.assertIn("pg_advisory_xact_lock(hashtext('ops_alarm'), p_issue)", n)
        self.assertIn("'^https://github\\.com/[^ ]+$'", self.sql.replace("\n", " "))

    def test_no_mail_or_net(self):
        for bad in ("http_post", "pg_net", "net.http", "brevo", "mail"):
            self.assertNotIn(bad, self.norm.lower(), bad)

    def test_not_defined_in_other_migrations(self):
        for p in sorted(MIG.parent.glob("*.sql")):
            if p.name == MIG.name:
                continue
            code = _code(p.read_text(encoding="utf-8"))
            for token in ("ops_alarm", "private.ops_alarm_summary"):
                self.assertNotIn(token, code, p.name)


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = WF.read_text(encoding="utf-8")
        cls.code = "\n".join(l for l in cls.wf.splitlines() if not l.lstrip().startswith("#"))

    def test_triggers(self):
        self.assertIn("issues:\n    types: [opened, reopened, closed, labeled, unlabeled]", self.code)
        self.assertRegex(self.code, r"schedule:\n\s+- cron: '41 22 \* \* \*'")
        self.assertIn("workflow_dispatch:", self.code)
        self.assertNotIn("pull_request", self.code)

    def test_default_off_and_dry_run(self):
        self.assertIn("ENABLED: ${{ vars.GRM_OPS_ALARM_ENABLED }}", self.code)
        self.assertIn('DRY="--dry-run"', self.code)
        self.assertRegex(self.code, r"dry_run:\n(?:.*\n)*?\s+default: true\n\s+type: boolean")
        self.assertIn('if [ "$ENABLED" = "true" ]; then', self.code)
        self.assertIn('[ "$DISPATCH_DRY" = "false" ] && DRY=""', self.code)

    def test_no_untrusted_input_in_shell(self):
        run = self.code.split("run: |")[-1]
        self.assertNotIn("github.event.issue", run)
        self.assertNotIn("${{", run, "run 블록에 표현식을 끼우지 않는다")
        self.assertNotIn("github.event.issue", self.code)

    def test_permissions_and_secrets(self):
        self.assertIn("permissions:\n  contents: read\n  issues: write", self.code)
        self.assertIn("persist-credentials: false", self.code)
        self.assertIn("::add-mask::$SUPABASE_SERVICE_ROLE_KEY", self.code)
        self.assertIn("secrets.SUPABASE_SERVICE_ROLE_KEY", self.code)
        self.assertNotIn("DECISION_MAIL", self.code, "결재 메일과 연결하지 않는다")
        for bad in ("sendmail", "brevo", "smtp", "api.brevo"):
            self.assertNotIn(bad, self.code.lower())

    def test_uses_pinned_actions_and_script(self):
        for m in re.finditer(r"uses: (\S+)", self.code):
            self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", m.group(1))
        self.assertIn("web/ops_alarm_sync.py", self.code)


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class RenderTest(unittest.TestCase):
    def _run(self, alarms):
        js = (
            "const ops=require(process.argv[1]);const d=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));"
            "const before=ops.render(d,{});"
            "if(process.argv[3]!=='none')d.alarms=JSON.parse(process.argv[3]);"
            "console.log(JSON.stringify({a:ops.renderAlarms(d),all:ops.render(d,{}),before}));"
        )
        res = subprocess.run(["node", "-e", js, str(OPS_JS), str(FIXTURE), alarms],
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_absent_or_zero_renders_nothing_and_page_unchanged(self):
        out = self._run("none")
        self.assertEqual(out["a"], "")
        self.assertEqual(out["all"], out["before"])
        out = self._run(json.dumps({"open": 3, "neglected": 0, "neglect_days": 3, "items": []}))
        self.assertEqual(out["a"], "")
        self.assertEqual(out["all"], out["before"])

    def test_red_banner_with_links_escaped(self):
        al_ = {"open": 4, "neglected": 7, "neglect_days": 3, "items": [
            {"issue": 1274, "url": "https://github.com/MINHOYEOM/grm-api-intake/issues/1274", "title": "<img src=x onerror=1> 낙차", "team": "infra", "days": 5},
            {"issue": 9, "url": "javascript:alert(1)", "title": "나쁜 링크", "team": "dev", "days": 4}]}
        out = self._run(json.dumps(al_))["a"]
        self.assertIn('role="alert"', out)
        self.assertIn("방치된 경보 7건", out)
        self.assertIn('href="https://github.com/MINHOYEOM/grm-api-intake/issues/1274"', out)
        self.assertNotIn("<img", out)
        self.assertNotIn("javascript:", out)
        self.assertIn("그 밖에 5건", out)

    def test_wiring(self):
        admin = (ROOT / "web" / "assets" / "admin.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "templates" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('id="grm-oc-alarms"', html)
        self.assertIn("window.GRMOps.renderAlarms(state.opsHome)", admin)
        self.assertIn(".oc-alarm{", html)


if __name__ == "__main__":
    unittest.main()
