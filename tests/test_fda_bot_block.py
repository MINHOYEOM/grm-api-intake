"""Offline regression tests for the FDA request stop and manual-run warning."""

import os
from datetime import date
from io import StringIO
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import requests

import collect_fda_483
import collect_intake
import grm_common
from grm_common import FDA_BOT_BLOCK, FdaBotBlockError

FDA_URL = "https://www.fda.gov/media/1/download"


def response(url=FDA_URL, body=b"", status=404):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body
    return result


class FdaBotBlockTests(TestCase):
    def setUp(self):
        self.enterContext(patch.object(grm_common, "_fda_blocked", False))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("GITHUB_OUTPUT", None)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_block_redirect_stops_fda_requests(self):
        self.check_first_block(response(
            url="https://www.fda.gov/apology_objects/abuse-detection-apology.html",
        ))

    def test_block_apology_body_stops_fda_requests(self):
        self.check_first_block(response(body=b"<html>ABUSE-DETECTION-APOLOGY</html>", status=200))

    def test_block_apology_objects_body_stops_fda_requests(self):
        self.check_first_block(response(body=b"<html>apology_objects</html>"))

    def check_first_block(self, blocked):
        tmp_path = Path(self.enterContext(TemporaryDirectory(dir=Path(__file__).resolve().parent)))
        summary = tmp_path / "summary"
        output = tmp_path / "output"
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        os.environ["GITHUB_OUTPUT"] = str(output)
        other_url = "https://example.org/"
        get = Mock(side_effect=[blocked, response(other_url, b"ok", 200)])
        self.enterContext(patch.object(requests, "get", get))
        self.enterContext(patch.object(grm_common.time, "sleep", side_effect=AssertionError("retried")))

        with self.assertRaisesRegex(FdaBotBlockError, "^fda-bot-block$"):
            grm_common.http_get_bytes(FDA_URL, retries=3)
        self.assertEqual(collect_intake._fetch_wl_letter_html(FDA_URL), ("", FDA_BOT_BLOCK))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(grm_common.http_get_html(other_url), "ok")
        self.assertEqual(summary.read_text(encoding="utf-8").count(FDA_BOT_BLOCK), 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "fda_bot_block=true\n")

    def test_plain_404_does_not_stop_fda_requests(self):
        get = Mock(side_effect=[response(), response(body=b"ok", status=200)])
        self.enterContext(patch.object(requests, "get", get))

        with self.assertRaisesRegex(RuntimeError, "404") as exc:
            grm_common.http_get_html(FDA_URL, retries=0)
        self.assertNotIn(FDA_BOT_BLOCK, str(exc.exception))
        self.assertEqual(grm_common.http_get_html(FDA_URL), "ok")
        self.assertEqual(get.call_count, 2)

    def test_blocked_483_listing_reports_failure_without_fallback(self):
        get = Mock(return_value=response(body=b"apology_objects"))
        self.enterContext(patch.object(requests, "get", get))
        self.enterContext(patch.object(
            collect_fda_483, "_fetch_legacy_json_rows",
            side_effect=AssertionError("continued to fallback"),
        ))
        day = date(2026, 11, 25)

        self.assertEqual(collect_fda_483.collect_fda_483(day, day), ([], FDA_BOT_BLOCK))
        self.assertEqual(collect_fda_483.LAST_HEALTH["status"], FDA_BOT_BLOCK)
        self.assertEqual(get.call_count, 1)

    def test_manual_warning_below_threshold(self):
        self.check_manual_warning(2)

    def test_manual_warning_at_threshold(self):
        self.check_manual_warning(3)

    def check_manual_warning(self, prior_count):
        script = Path(__file__).resolve().parents[1] / ".github/scripts/fda_manual_run_warning.py"
        warn = run_path(str(script))["warn_if_needed"]
        before_kst_midnight = {"databaseId": 1, "createdAt": "2026-11-24T14:59:59Z"}
        current = {"databaseId": 5, "createdAt": "2026-11-24T15:10:00Z"}
        later = {"databaseId": 6, "createdAt": "2026-11-24T15:10:01Z"}
        prior = [
            {"databaseId": 2, "createdAt": "2026-11-24T15:00:00Z"},
            {"databaseId": 3, "createdAt": "2026-11-24T15:04:00Z"},
            {"databaseId": 4, "createdAt": "2026-11-24T15:09:00Z"},
        ]
        runs_by_workflow = {
            "grm-fda-citations.yml": [before_kst_midnight, later, *prior[:1]],
            "grm-intake.yml": [current, *prior[1:prior_count]],
        }

        def gh(*args):
            if args[0] == "view":
                self.assertEqual(args[1], "5")
                return current
            self.assertEqual(args[:2], ("list", "--workflow"))
            self.assertEqual(args[3:8], (
                "--event", "workflow_dispatch", "--all", "--created",
                "2026-11-24T15:00:00Z..2026-11-24T15:10:00Z",
            ))
            return runs_by_workflow.get(args[2], [])

        summary = StringIO()
        with patch.dict(warn.__globals__, {"_gh_json": gh}):
            warn(5, summary)
        self.assertEqual(("3건 이상" in summary.getvalue()), (prior_count >= 3))
        self.assertLessEqual(len(summary.getvalue().splitlines()), 1)
