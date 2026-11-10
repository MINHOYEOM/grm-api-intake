"""indexnow_submit — 증분 비교·배치·페이로드·실패 비차단 회귀."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indexnow_submit as ix  # noqa: E402


def sm(*pairs):
    body = "".join(
        f"<url><loc>{u}</loc>" + (f"<lastmod>{m}</lastmod>" if m else "") + "</url>"
        for u, m in pairs)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")


class FakeResp:
    def __init__(self, code):
        self.status_code = code


class FakeSession:
    def __init__(self, code=200):
        self.code = code
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, json.loads(data), timeout))
        return FakeResp(self.code)


class DiffTest(unittest.TestCase):
    def test_new_changed_unchanged(self):
        cur = ix.parse_sitemap(sm(("https://a/", "2026-01-02"), ("https://b/", "2026-01-01"),
                                  ("https://c/", "2026-01-01")))
        prev = ix.parse_sitemap(sm(("https://a/", "2026-01-01"), ("https://b/", "2026-01-01")))
        self.assertEqual(ix.diff_urls(cur, prev), ["https://a/", "https://c/"])

    def test_no_previous_means_all(self):
        cur = ix.parse_sitemap(sm(("https://a/", ""), ("https://b/", "")))
        self.assertEqual(ix.diff_urls(cur, None), ["https://a/", "https://b/"])


class BatchPayloadTest(unittest.TestCase):
    def test_batching_limit(self):
        urls = [f"https://x/{i}" for i in range(25001)]
        sizes = [len(b) for b in ix.batches(urls)]
        self.assertEqual(sizes, [10000, 10000, 5001])

    def test_payload_shape(self):
        p = ix.build_payload(["https://grm-solutions.com/"])
        self.assertEqual(p["host"], "grm-solutions.com")
        self.assertEqual(p["key"], ix.INDEXNOW_KEY)
        self.assertEqual(p["keyLocation"], f"https://grm-solutions.com/{ix.INDEXNOW_KEY}.txt")
        self.assertEqual(p["urlList"], ["https://grm-solutions.com/"])

    def test_submit_posts_to_each_endpoint(self):
        s = FakeSession(202)
        res = ix.submit(["https://grm-solutions.com/a"], session=s)
        self.assertEqual([c[0] for c in s.calls], list(ix.ENDPOINTS))
        self.assertTrue(all(c[2] == 30 for c in s.calls))
        self.assertTrue(all(r == {"ok": 1, "fail": 0} for r in res.values()))


class MainTest(unittest.TestCase):
    def _files(self):
        d = tempfile.mkdtemp()
        cur = os.path.join(d, "cur.xml")
        with open(cur, "w", encoding="utf-8") as f:
            f.write(sm(("https://grm-solutions.com/a", "1")))
        return cur

    def _run(self, code, *extra):
        real_sleep = ix.time.sleep
        ix.time.sleep = lambda s: None
        real = ix.post_batch
        sess = FakeSession(code)
        ix.post_batch = lambda ep, payload, session=None: real(ep, payload, session=sess)
        try:
            return ix.main(["--sitemap", self._files(), "--all", *extra]), sess
        finally:
            ix.post_batch = real
            ix.time.sleep = real_sleep

    def test_5xx_does_not_fail_without_strict(self):
        rc, sess = self._run(503)
        self.assertEqual(rc, 0)
        self.assertEqual(len(sess.calls), len(ix.ENDPOINTS) * (ix.RETRIES + 1))

    def test_5xx_fails_with_strict(self):
        rc, _ = self._run(400, "--strict")
        self.assertEqual(rc, 1)

    def test_success_exit_zero(self):
        rc, _ = self._run(200, "--strict")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
