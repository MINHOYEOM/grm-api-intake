import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/grm-findings-docs.yml"


class FindingsDocsWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def step(self, name):
        return self.workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - name: ", 1)[0]

    def test_deploy_token_checked_before_ci_for_merge_candidates(self):
        guard_name = "Verify token for merge-triggered deployment"
        guard = self.step(guard_name)
        merge = self.step("Merge only after green CI")
        guard_condition = next(line for line in guard.splitlines() if line.strip().startswith("if: "))
        self.assertIn(guard_condition, merge.splitlines())
        self.assertIn("secrets.GRM_PUBLISH_TOKEN || secrets.ADMIN_GITHUB_ACTIONS_TOKEN || github.token",
                      self.workflow)
        self.assertIn("secrets.GRM_PUBLISH_TOKEN != '' || secrets.ADMIN_GITHUB_ACTIONS_TOKEN != ''",
                      guard)
        self.assertLess(self.workflow.index("Commit and open candidate PR"), self.workflow.index(guard_name))
        self.assertLess(self.workflow.index(guard_name), self.workflow.index("Dispatch CI gate"))

    @unittest.skipUnless(shutil.which("bash"), "bash required for workflow shell check")
    def test_token_guard_fails_without_deploy_token(self):
        guard = self.step("Verify token for merge-triggered deployment")
        script = textwrap.dedent(guard.split("        run: |\n", 1)[1].strip("\n"))
        for present, expected in (("false", 1), ("", 1), ("true", 0)):
            with self.subTest(present=present):
                result = subprocess.run(["bash", "--noprofile", "--norc", "-s", "--", present],
                                        input='HAS_DEPLOY_TRIGGER_TOKEN="$1"\n' + script,
                                        text=True, encoding="utf-8", capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual("::error::" in result.stdout, expected != 0)

    def test_alert_covers_pipeline_failure_and_requires_actual_recovery(self):
        alert = self.step("Open or update docs-refresh issue")
        recovery = self.step("Close docs-refresh issue on recovery")
        self.assertIn("        if: failure()", alert.splitlines())
        self.assertIn("        id: merge", self.step("Merge only after green CI").splitlines())
        self.assertIn("        if: success() && (steps.changes.outputs.changed == 'false' || "
                      "steps.merge.outcome == 'success')", recovery.splitlines())
