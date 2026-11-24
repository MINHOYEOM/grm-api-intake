import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import audit_483_inspector_blanks as tool
import findings_supabase_backfill as fsb


SIGNATURE = "EMPLOYEE(S) SIGNATURE\n"


class TestAudit483InspectorBlanks(unittest.TestCase):
    def assert_blank_reason(self, body, reason, requires_human):
        report = tool.audit([{"document_id": "case", "raw_signal_id": "raw", "body": body}])

        self.assertEqual(report, {
            "mode": "dry-run",
            "scanned": 1,
            "counts": {reason: 1},
            "rows": [{"raw_signal_id": "raw", "document_id": "case",
                      "reason": reason, "requires_human": requires_human}],
        })

    def test_no_signature_block(self):
        self.assert_blank_reason(
            "OBSERVATION 1\nIncomplete batch records. Operator (b)(6) was present.",
            "no_signature_block", False,
        )

    def test_redacted_b6_parentheses(self):
        self.assert_blank_reason(
            SIGNATURE + "(b) (6), Investigator\n3/5/2026", "redacted", False,
        )

    def test_redacted_b6_angle_brackets(self):
        self.assert_blank_reason(
            SIGNATURE + "<Redacted B6>, Investigator\n3/5/2026", "redacted", False,
        )

    def test_ocr_unreadable(self):
        self.assert_blank_reason(SIGNATURE + "~~ #@? ~~\n", "ocr_unreadable", True)

    def test_conflicting_signatures_need_human(self):
        self.assert_blank_reason(
            SIGNATURE + "Avery Sharpe, Investigator\n3/5/2026\n"
            + SIGNATURE + "Avery Sharp, Investigator\n3/5/2026", "needs_human", True,
        )

    def test_single_signature_needs_human(self):
        self.assert_blank_reason(
            SIGNATURE + "Avery Sharpe, Investigator\n3/5/2026", "needs_human", True,
        )

    def test_only_blank_fda_483_rows_with_stored_text_are_reported(self):
        body = "OBSERVATION 1\nIncomplete batch records."
        rows = [
            {"document_id": "target", "raw_json": json.dumps({"fda483_body_full": body})},
            {"document_id": "filled", "raw_json": {"fda483_body_full": body,
                                                  "fda483_inspectors": ["Avery Sharpe"]}},
            {"document_id": "out-of-scope", "body": body, "st": "out-of-scope"},
            {"document_id": "other-source", "body": body, "source": "other"},
            {"document_id": "no-body", "raw_json": {}},
            {"document_id": "whitespace", "body": " \n"},
        ]

        report = tool.audit(rows)

        self.assertEqual(report["scanned"], len(rows))
        self.assertEqual([row["document_id"] for row in report["rows"]], ["target"])

    def test_fixture_cli_prints_dry_report(self):
        stdout = StringIO()
        with TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.json"
            fixture.write_text(json.dumps([
                {"document_id": "fixture", "body": "OBSERVATION 1"},
            ]), encoding="utf-8")
            with redirect_stdout(stdout):
                self.assertEqual(tool.main(["--rows-json", str(fixture)]), 0)

        self.assertEqual(json.loads(stdout.getvalue())["counts"], {"no_signature_block": 1})

    def test_read_db_does_not_use_service_role_key(self):
        stderr = StringIO()
        with (
            patch.dict(os.environ, {
                "SUPABASE_URL": "https://fixture.invalid",
                "SUPABASE_SERVICE_ROLE_KEY": "fixture-service-key",
            }),
            patch.object(tool, "_fetch_rows", side_effect=AssertionError("unexpected DB read")),
            redirect_stderr(stderr),
        ):
            os.environ.pop("SUPABASE_READ_ONLY_KEY", None)
            with self.assertRaises(SystemExit) as exc:
                tool.main(["--read-db"])

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("use --rows-json locally", stderr.getvalue())

    def test_read_db_uses_filtered_get_with_read_only_key(self):
        class FixtureResponse:
            status_code = 200
            headers = {"Content-Range": "0-0/1"}

            def json(self):
                return [{"document_id": "stored", "raw_json": {"fda483_body_full": "OBSERVATION 1"}}]

        def get(url, *, headers, params, timeout):
            self.assertEqual(url, "https://fixture.invalid/rest/v1/raw_signals")
            self.assertEqual(headers["Authorization"], "Bearer fixture-read-only-key")
            self.assertEqual(params, {
                "source": "eq.FDA 483",
                "raw_json": "like.*fda483_body_full*",
                "select": "raw_signal_id,document_id,raw_json",
                "order": "raw_signal_id.asc",
            })
            return FixtureResponse()

        stdout = StringIO()
        with (
            patch.dict(os.environ, {
                "SUPABASE_URL": "https://fixture.invalid/",
                "SUPABASE_READ_ONLY_KEY": "fixture-read-only-key",
            }),
            patch.object(fsb.requests, "get", side_effect=get),
            redirect_stdout(stdout),
        ):
            self.assertEqual(tool.main(["--read-db"]), 0)

        self.assertEqual(json.loads(stdout.getvalue())["rows"][0]["document_id"], "stored")

    def test_apply_refuses_before_reading(self):
        stderr = StringIO()
        with (
            patch.object(Path, "read_text", side_effect=AssertionError("unexpected fixture read")),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as exc,
        ):
            tool.main(["--rows-json", "fixture.json", "--apply"])

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("--apply is unavailable", stderr.getvalue())
