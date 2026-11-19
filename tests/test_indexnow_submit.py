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


class IncrementalCapTest(unittest.TestCase):
    """[구글 품질 1단계 2026-11-19] 구글용(축소)·전체 sitemap 을 섞어 비교하면 수천 건이 '새 URL'이 된다 —
    증분 상한을 넘으면 아무것도 보내지 않는다(빙 대량 제출 자제 권고)."""

    def _run(self, n_prev, n_cur, *extra):
        d = tempfile.mkdtemp()
        cur, prev = os.path.join(d, "cur.xml"), os.path.join(d, "prev.xml")
        with open(cur, "w", encoding="utf-8") as f:
            f.write(sm(*((f"https://grm-solutions.com/{i}", "") for i in range(n_cur))))
        with open(prev, "w", encoding="utf-8") as f:
            f.write(sm(*((f"https://grm-solutions.com/{i}", "") for i in range(n_prev))))
        sess = FakeSession(200)
        real = ix.post_batch
        ix.post_batch = lambda ep, payload, session=None: real(ep, payload, session=sess)
        try:
            return ix.main(["--sitemap", cur, "--previous", prev, *extra]), sess
        finally:
            ix.post_batch = real

    def test_over_cap_sends_nothing(self):
        rc, sess = self._run(10, 30, "--max-urls", "15")
        self.assertEqual(rc, 0)
        self.assertEqual(sess.calls, [])
        rc, _ = self._run(10, 30, "--max-urls", "15", "--strict")
        self.assertEqual(rc, 1)

    def test_under_cap_sends_diff(self):
        rc, sess = self._run(10, 20, "--max-urls", "15")
        self.assertEqual(rc, 0)
        self.assertEqual(len(sess.calls), len(ix.ENDPOINTS))
        self.assertEqual(len(sess.calls[0][1]["urlList"]), 10)

    def test_default_cap_blocks_google_vs_full_mixup(self):
        # 구글용(약 6천 장 축소)을 이전 값으로 잘못 쓰면 빠진 몫 전부가 새 URL 이 된다.
        self.assertLess(ix.MAX_INCREMENTAL, 6000)
        self.assertGreater(ix.MAX_INCREMENTAL, 2900)  # 11-16 조항 신설 같은 정상 대량 증분은 통과


if __name__ == "__main__":
    unittest.main()
