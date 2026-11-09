"""gsc_index_sample.py — 순수 함수(표본 결정론·URL·요약) 확인. 네트워크 없음."""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gsc_index_sample as g  # noqa: E402


class GscIndexSampleTest(unittest.TestCase):
    def test_sample_is_deterministic_per_week_and_bounded(self):
        ids = [str(i) for i in range(100)]
        a = g.pick_sample(ids, 15, "2026-W46:ko:thin:")
        self.assertEqual(a, g.pick_sample(list(reversed(ids)), 15, "2026-W46:ko:thin:"))
        self.assertEqual(len(a), 15)
        self.assertNotEqual(a, g.pick_sample(ids, 15, "2026-W47:ko:thin:"))
        self.assertEqual(g.pick_sample(ids[:3], 15, "x"), sorted(ids[:3], key=lambda d: g.hashlib.sha256(("x" + d).encode()).hexdigest()))

    def test_urls(self):
        self.assertEqual(g.doc_url("159255", "ko"), "https://grm-solutions.com/findings/doc/159255/")
        self.assertEqual(g.doc_url("159255", "en"), "https://grm-solutions.com/en/findings/doc/159255/")

    def test_week_key(self):
        self.assertEqual(g.week_key(dt.date(2026, 11, 10)), "2026-W46")

    def test_summary_counts_by_bucket(self):
        rows = [{"lang": "ko", "bucket": "thin", "coverage": "Excluded by 'noindex' tag"},
                {"lang": "ko", "bucket": "thin", "coverage": "Excluded by 'noindex' tag"},
                {"lang": "en", "bucket": "thick", "error": "HTTP 429"}]
        s = g.summarize(rows)
        self.assertEqual(s["ko·thin"], {"Excluded by 'noindex' tag": 2})
        self.assertEqual(s["en·thick"], {"HTTP 429": 1})

    def test_buckets_use_render_predicate(self):
        thin = {"document_id": "a", "findings": [{"text_ko": "짧다"}]}
        thick = {"document_id": "b", "findings": [{"text_ko": "가" * 900} for _ in range(6)]}
        b = g.buckets([thin, thick], "ko")
        self.assertEqual(b, {"thin": ["a"], "thick": ["b"]})

    def test_skips_cleanly_without_secret(self):
        import os
        old = os.environ.pop("GSC_SERVICE_ACCOUNT_JSON", None)
        try:
            self.assertEqual(g.main(["--docs", "nonexistent.json"]), 0)
        finally:
            if old is not None:
                os.environ["GSC_SERVICE_ACCOUNT_JSON"] = old


if __name__ == "__main__":
    unittest.main()
