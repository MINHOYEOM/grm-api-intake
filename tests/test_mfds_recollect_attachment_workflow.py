"""Verify the manual MFDS GMP attachment workflow contract."""

from pathlib import Path
import re
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "grm-mfds-recollect-attachment.yml"
)


class MfdsRecollectAttachmentWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_dispatch_requires_ids_and_defaults_to_dry_run(self):
        trigger = self.workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  (\w+):", trigger), ["workflow_dispatch"])
        self.assertRegex(
            trigger, r"(?m)^      ids:\n(?:^        [^\n]+\n)*"
            r"^        type: string\n^        required: true$"
        )
        self.assertRegex(
            trigger, r"(?m)^      apply:\n(?:^        [^\n]+\n)*"
            r"^        type: boolean\n^        default: false$"
        )

    def test_empty_proxy_fails_before_setup_and_probe(self):
        guard = re.search(
            r'(?m)^          if \[ -z "\$MFDS_HTTP_PROXY" \]; then\n'
            r'^            echo "::error::[^"\n]+"\n'
            r'^            exit 1\n^          fi$',
            self.workflow,
        )
        self.assertIsNotNone(guard)
        self.assertLess(guard.start(), self.workflow.index("uses: actions/checkout@"))
        self.assertLess(guard.start(), self.workflow.index("python probe_mfds_egress.py"))

    def test_shares_backfill_concurrency_group(self):
        self.assertRegex(
            self.workflow, r"(?m)^concurrency:\n  group: mfds-backfill\n  cancel-in-progress: false$"
        )

    def test_passes_apply_flag_only_when_input_is_true(self):
        self.assertIn("APPLY: ${{ inputs.apply }}", self.workflow)
        self.assertRegex(
            self.workflow,
            r'(?m)^        run: \|\n'
            r'^          args=\(\)\n'
            r'^          if \[\[ "\$APPLY" == "true" \]\]; then\n'
            r'^            args\+=\(--apply\)\n'
            r'^          fi\n'
            r'^          python recollect_mfds_gmp_attachment\.py --ids "\$IDS"'
            r' --json recollect\.json "\$\{args\[@\]\}"$',
        )
        self.assertEqual(self.workflow.count("--apply"), 1)

    def test_probe_precedes_recollection_and_failure_is_reported(self):
        self.assertLess(
            self.workflow.index("python probe_mfds_egress.py"),
            self.workflow.index("python recollect_mfds_gmp_attachment.py"),
        )
        self.assertNotIn("continue-on-error:", self.workflow)
        self.assertRegex(
            self.workflow, r'(?m)^          if \[ "\$OUTCOME" = "failure" \]; then\n'
            r'^            echo "::error::',
        )
        self.assertIn("path: recollect.json", self.workflow)
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', self.workflow)
