"""Run the workflow issue steps against an in-memory GitHub API."""

import json
from pathlib import Path
import re
import shutil
import subprocess
from textwrap import dedent
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
INTAKE_TITLE = "GRM Intake 운영 경고"
BACKLOG_TITLE = "[findings-backlog] 번역/검수 백로그가 임계를 초과했습니다"
MONITOR_TITLE = "[findings-backlog] 모니터 오류 및 보조 검사 경보"

HARNESS = """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const issues = input.issues;
const operations = [];
const api = {
  listForRepo: () => { throw new Error('issue listing must be paginated'); },
  getLabel: async () => {},
  update: async ({issue_number, body, state}) => {
    const issue = issues.find(i => i.number === issue_number);
    if (body !== undefined) issue.body = body;
    if (state !== undefined) issue.state = state;
    operations.push([state || 'update', issue_number]);
  },
  create: async ({title, body}) => {
    const number = 3000 + issues.length;
    issues.push({title, body, number});
    operations.push(['create', title]);
  },
  createComment: async ({issue_number}) => operations.push(['comment', issue_number]),
};
const github = {
  rest: {issues: api},
  paginate: async (method, params) => {
    if (method !== api.listForRepo || params.state !== 'open' || !params.labels) {
      throw new Error('invalid issue lookup');
    }
    return issues.filter(i => i.state !== 'closed');
  },
};
const read = name => name === 'fs' ? {
  existsSync: () => true,
  readFileSync: () => JSON.stringify(input.report),
} : require(name);
const context = {repo: {owner: 'example', repo: 'example'}, runId: 7};
const proc = {env: {GITHUB_WORKSPACE: '/work'}};
const logger = {log: () => {}};
const run = new (Object.getPrototypeOf(async function () {}).constructor)(
  'require', 'github', 'context', 'process', 'console', 'core', input.script);
run(read, github, context, proc, logger, {notice: () => {}}).then(() => {
  process.stdout.write(JSON.stringify({issues, operations}));
}).catch(error => { console.error(error); process.exitCode = 1; });
"""


def _script(filename, step_name):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    step = workflow.split(f"      - name: {step_name}\n", 1)[1]
    step = step.split("\n      - name: ", 1)[0]
    script = re.search(r"^          script: \|\n((?: {12}[^\n]*\n|\n)+)", step, re.M)
    return dedent(script.group(1))


def _issue(number, title):
    return {"number": number, "title": title}


@unittest.skipUnless(shutil.which("node"), "Node.js required for workflow script tests")
class OpsIssueLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.intake = _script("grm-intake.yml", "Open or update warning issue")
        cls.backlog = _script("grm-findings-backlog-monitor.yml",
                              "Reconcile backlog and monitor issues")

    def _run(self, script, report, issues):
        result = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps({"script": script, "report": report, "issues": issues}),
            capture_output=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_intake_persistent_warning_updates_same_issue_then_closes(self):
        warning = {"code": "gmp-attachment-manual-review", "source": "MFDS",
                   "message": "수동 확인 1건"}
        report = {"run_date_kst": "2026-11-25", "health": {"warnings": [warning]}}
        first = self._run(self.intake, report, [])
        self.assertEqual(first["operations"], [["create", INTAKE_TITLE]])
        number = first["issues"][0]["number"]
        report["run_date_kst"] = "2026-11-26"
        second = self._run(self.intake, report, first["issues"])
        self.assertEqual(second["operations"], [["update", number]])
        report["health"]["warnings"] = []
        third = self._run(self.intake, report, second["issues"] + [_issue(3010, INTAKE_TITLE)])
        self.assertEqual(third["operations"],
                         [["closed", number], ["comment", number], ["closed", 3010], ["comment", 3010]])
        self.assertEqual(self._run(self.intake, report, third["issues"])["operations"], [])

    def test_intake_incomplete_health_does_not_close_warning(self):
        result = self._run(self.intake, {"health": {}}, [_issue(1943, INTAKE_TITLE)])
        self.assertEqual(result["operations"], [])

    def test_backlog_stays_on_existing_issue_while_over_threshold(self):
        report = {"status": "failure", "backlog_checked": True, "errors": [], "breaches": [
            {"code": "untranslated-gap-high", "message": "319 > 300"},
        ]}
        result = self._run(self.backlog, report, [_issue(1945, BACKLOG_TITLE)])
        self.assertEqual(result["operations"], [["update", 1945]])
        self.assertIn("319 > 300", result["issues"][0]["body"])

    def test_backlog_can_close_while_auxiliary_monitors_still_warn(self):
        report = {"status": "failure", "backlog_checked": True, "errors": [], "breaches": [
            {"code": "extraction-gap-high", "message": "missing findings"},
        ]}
        result = self._run(self.backlog, report, [_issue(1945, BACKLOG_TITLE)])
        self.assertEqual(result["operations"],
                         [["closed", 1945], ["comment", 1945], ["create", MONITOR_TITLE]])
        diagnostic = result["issues"][-1]
        self.assertIn("extraction-gap-high", diagnostic["body"])
        report.update(status="ok", breaches=[])
        recovered = self._run(self.backlog, report, result["issues"])
        self.assertEqual(recovered["operations"],
                         [["closed", diagnostic["number"]], ["comment", diagnostic["number"]]])

    def test_missing_stats_preserve_backlog_issue_and_update_monitor_issue(self):
        report = {"status": "error", "backlog_checked": False,
                  "errors": ["findings_stats RPC failed"], "breaches": []}
        result = self._run(self.backlog, report, [
            _issue(1945, BACKLOG_TITLE), _issue(2045, MONITOR_TITLE),
        ])
        self.assertEqual(result["operations"], [["update", 2045]])

    def test_auxiliary_rpc_error_does_not_prevent_confirmed_backlog_recovery(self):
        report = {"status": "error", "backlog_checked": True,
                  "errors": ["extraction_gap_by_source RPC failed"], "breaches": []}
        result = self._run(self.backlog, report, [_issue(1945, BACKLOG_TITLE)])
        self.assertEqual(result["operations"],
                         [["closed", 1945], ["comment", 1945], ["create", MONITOR_TITLE]])
