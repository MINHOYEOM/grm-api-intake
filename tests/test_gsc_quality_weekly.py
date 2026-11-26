"""Exercise the weekly report with fake REST and URL Inspection data."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import gsc_quality_weekly as quality


TODAY = dt.date(2026, 11, 30)
ENV = {"SUPABASE_URL": "https://example.test/", "SUPABASE_SERVICE_ROLE_KEY": "fake-key"}


class WeeklyQualityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.sample = root / "sample.json"
        self.summary = root / "summary.md"
        self.reports = root / "_ops" / "reports"
        environment = patch.dict(os.environ, {**ENV, "GITHUB_STEP_SUMMARY": str(self.summary)})
        environment.start()
        self.addCleanup(environment.stop)
        rest = patch.object(quality.requests, "get")
        self.get = rest.start()
        self.addCleanup(rest.stop)
        self.get.return_value = Mock(status_code=200, **{"json.return_value": [{"snap_date": "2026-11-27"}]})
        self.rows = [
            {"lang": lang, "bucket": kind, "verdict": "PASS", "indexing": "INDEXING_ALLOWED"}
            for lang, kind in quality.BUCKETS
        ]

    def run_report(self, *, write_sample=True, day=TODAY):
        if write_sample:
            self.sample.write_text(json.dumps({"week": "2026-W49", "observed_on": TODAY.isoformat(),
                                               "rows": self.rows}), encoding="utf-8")
        result = quality.main(["--sample", str(self.sample), "--report-dir", str(self.reports)], today=day)
        report, = self.reports.glob("gsc_quality_*.md")
        contents = report.read_text(encoding="utf-8")
        self.assertEqual(self.summary.read_text(encoding="utf-8"), contents)
        return result, contents

    def test_report_totals_and_buckets_use_observed_denominators(self):
        self.rows += [
            {"lang": "ko", "bucket": "thin", "verdict": "NEUTRAL", "indexing": "BLOCKED_BY_META_TAG"},
            {"lang": "ko", "bucket": "thin", "verdict": "FAIL", "indexing": "BLOCKED_BY_HTTP_HEADER"},
            {"lang": "ko", "bucket": "thin", "error": "HTTP 429"},
        ]
        result, report = self.run_report()
        self.assertEqual(result, 0)
        self.assertIn("| ko·thin | 4 | 2건 / 확인 3건 | 1/3 (33.3%) |", report)
        self.assertIn("| **전체 표본** | 7 | 2건 / 확인 6건 | 4/6 (66.7%) |", report)
        self.assertIn("2026-W49", report)
        self.assertIn("2단계 후", report)
        self.assertIn("2026-11-27", report)
        self.assertIn("⚠️", report)
        self.get.assert_called_once_with(
            "https://example.test/rest/v1/gsc_daily",
            headers={"apikey": "fake-key", "Authorization": "Bearer fake-key"},
            params={"select": "snap_date", "and": "(snap_date.gte.2026-11-23,snap_date.lt.2026-11-30)",
                    "order": "snap_date.desc", "limit": 1},
            timeout=20, allow_redirects=False,
        )

    def test_empty_daily_data_fails_with_report(self):
        self.get.return_value.json.return_value = []
        result, report = self.run_report()
        self.assertEqual(result, 1)
        self.assertIn("❌ gsc_daily 최근 7일 자료 없음", report)
        self.assertIn("4/4 (100.0%)", report)

    def test_http_error_does_not_disclose_response(self):
        self.get.return_value.status_code = 403
        result, report = self.run_report()
        self.assertEqual(result, 1)
        self.assertIn("❌ gsc_daily REST 조회 실패", report)
        self.assertNotIn("fake-key", report)
        self.get.return_value.json.assert_not_called()

    def test_missing_sample_fails_and_keeps_daily_date(self):
        result, report = self.run_report(write_sample=False)
        self.assertEqual(result, 1)
        self.assertIn("❌ 이번 주 URL 검사 표본이 없거나 읽을 수 없음", report)
        self.assertIn("2026-11-27", report)
        self.assertIn("| **전체 표본** | 0 | 자료 없음 | 자료 없음 |", report)

    def test_empty_sample_fails(self):
        self.rows.clear()
        result, report = self.run_report()
        self.assertEqual(result, 1)
        self.assertIn("❌ 이번 주 URL 검사 표본 0건", report)

    def test_noindex_unknown_is_not_zero(self):
        self.rows[0]["indexing"] = "INDEXING_STATE_UNSPECIFIED"
        result, report = self.run_report()
        self.assertEqual(result, 1)
        self.assertIn("| ko·thin | 1 | 자료 없음 | 1/1 (100.0%) |", report)

    def test_stale_sample_from_same_week_fails(self):
        result, report = self.run_report(day=dt.date(2026, 12, 1))
        self.assertEqual(result, 1)
        self.assertIn("| **전체 표본** | 0 | 자료 없음 | 자료 없음 |", report)

    def test_release_dates_and_phases(self):
        expected = {
            18: "1단계 전", 19: "1단계 전환일", 20: "1단계 후 · 2단계 전",
            25: "1단계 후 · 2단계 전", 26: "2단계 전환일", 27: "2단계 후",
        }
        for day, phase in expected.items():
            with self.subTest(day=day):
                self.assertEqual(quality.phase(dt.date(2026, 11, day)), phase)
