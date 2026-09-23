#!/usr/bin/env python3
"""web/utm.py 테스트 — 순수 함수 층(네트워크 0·now() 0).

CI(`unittest discover -s tests`)는 `tests/test_web_utm.py` shim 으로 순회. 직접:
  python web/tests/test_utm.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import utm  # noqa: E402


class WithUtmTest(unittest.TestCase):
    def test_appends_to_bare_url(self):
        out = utm.with_utm("https://grm-solutions.com/briefs/2026-09-07/",
                            "linkedin", "social", "2026-09-07_weekly")
        self.assertEqual(
            out,
            "https://grm-solutions.com/briefs/2026-09-07/"
            "?utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly")

    def test_preserves_existing_query(self):
        out = utm.with_utm("https://example.com/x?a=1&b=2", "newsletter", "email", "brief_2026-09-07")
        self.assertTrue(out.startswith("https://example.com/x?a=1&b=2&utm_source=newsletter"), out)
        self.assertIn("utm_medium=email", out)
        self.assertIn("utm_campaign=brief_2026-09-07", out)

    def test_preserves_fragment(self):
        out = utm.with_utm("https://example.com/x#sec-1", "newsletter", "forward", "brief_2026-09-07")
        self.assertTrue(out.endswith("#sec-1"), out)
        self.assertIn("?utm_source=newsletter", out)

    def test_rejects_at_sign(self):
        with self.assertRaises(ValueError):
            utm.with_utm("https://example.com/", "link@in", "social", "2026-09-07_weekly")

    def test_rejects_spaces(self):
        with self.assertRaises(ValueError):
            utm.with_utm("https://example.com/", "linkedin", "so cial", "2026-09-07_weekly")

    def test_rejects_empty_value(self):
        with self.assertRaises(ValueError):
            utm.with_utm("https://example.com/", "", "social", "2026-09-07_weekly")

    def test_uppercase_is_lowered_not_rejected(self):
        out = utm.with_utm("https://example.com/", "LinkedIn", "Social", "2026-09-07_WEEKLY")
        self.assertIn("utm_source=linkedin", out)
        self.assertIn("utm_medium=social", out)
        self.assertIn("utm_campaign=2026-09-07_weekly", out)

    def test_campaign_helper(self):
        self.assertEqual(utm.linkedin_weekly_campaign("2026-09-07"), "2026-09-07_weekly")

    def test_linkedin_weekly_constant(self):
        self.assertEqual(utm.LINKEDIN_WEEKLY, ("linkedin", "social"))
        out = utm.with_utm("https://example.com/", *utm.LINKEDIN_WEEKLY,
                            utm.linkedin_weekly_campaign("2026-09-07"))
        self.assertIn("utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly", out)


if __name__ == "__main__":
    unittest.main()
