import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

from automation_pr_alarm import count_stale_automation_prs, main


class AutomationPrAlarmTest(unittest.TestCase):
    now = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    glossary_pr = {
        "number": 1835,
        "headRefName": "automation/glossary-cases-auto-test",
        "createdAt": "2026-11-20T03:00:00Z",
        "state": "OPEN",
    }

    def test_glossary_pr_1835_and_inclusive_72_hour_boundary(self):
        on_cutoff = dict(self.glossary_pr, number=1836, createdAt="2026-11-22T09:00:00+09:00")
        under_cutoff = dict(on_cutoff, number=1837, createdAt="2026-11-22T00:00:01Z")
        self.assertEqual(count_stale_automation_prs([self.glossary_pr, on_cutoff, under_cutoff], self.now), 2)

    def test_ignores_closed_merged_and_other_branches(self):
        prs = [
            dict(self.glossary_pr, state="CLOSED"),
            dict(self.glossary_pr, state="MERGED"),
            dict(self.glossary_pr, headRefName="feature/automation/glossary"),
            dict(self.glossary_pr, headRefName="automation-other/glossary"),
        ]
        self.assertEqual(count_stale_automation_prs(prs, self.now), 0)
        self.assertEqual(count_stale_automation_prs([], self.now), 0)

    def test_cli_prints_warning_for_injected_pr_list(self):
        with patch("automation_pr_alarm.datetime", wraps=datetime) as clock:
            clock.now.return_value = self.now
            with patch("sys.stdin", io.StringIO(json.dumps([self.glossary_pr]))):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(), 0)
        self.assertEqual(output.getvalue(), "::warning::3일 이상 열린 automation/* PR 1건\n")
