"""grm-ema-relevance-apply.yml 가드 — 수동 전용·apply 기본 false·고정 입력·실패를 삼키지 않음."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = (ROOT / ".github/workflows/grm-ema-relevance-apply.yml").read_text(encoding="utf-8")
DECISIONS = ROOT / "docs/specs/ema_relevance_decisions_2026-11-25.json"


class EmaRelevanceApplyWorkflowTest(unittest.TestCase):
    def test_dispatch_only(self):
        self.assertIn("workflow_dispatch:", WF)
        for trigger in ("schedule:", "push:", "pull_request:", "workflow_run:"):
            self.assertNotIn("\n  " + trigger, WF)

    def test_apply_defaults_false_and_flag_only_when_true(self):
        self.assertIn("default: false", WF)
        self.assertIn('if [[ "$APPLY" == "true" ]]; then', WF)
        self.assertIn("args+=(--apply)", WF)
        self.assertEqual(WF.count("--apply"), 1)

    def test_fixed_input_and_markdown_summary(self):
        self.assertIn("python ema_relevance_apply.py docs/specs/ema_relevance_decisions_2026-11-25.json --markdown", WF)
        self.assertIn("$GITHUB_STEP_SUMMARY", WF)

    def test_failures_are_not_swallowed(self):
        self.assertNotIn("continue-on-error", WF)
        self.assertNotIn("|| true", WF)

    def test_service_key_comes_from_secrets_only(self):
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}", WF)

    def test_decisions_file_has_69_unique_rows(self):
        rows = json.loads(DECISIONS.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 69)
        docs = [r["doc"] for r in rows]
        self.assertEqual(len(set(docs)), 69)
        self.assertTrue(all(r.get("verdict") and r.get("evidence") for r in rows))


if __name__ == "__main__":
    unittest.main()
