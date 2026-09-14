"""probe_source_reachability — 무음 그룹(2026-09-14)의 판정 규칙을 잠근다(네트워크 0)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe_source_reachability as probe


class TargetGroupsTest(unittest.TestCase):
    def test_every_group_carries_a_control(self) -> None:
        for name, group in probe.TARGET_GROUPS.items():
            with self.subTest(group=name):
                self.assertTrue(any(t["control"] for t in group),
                                "대조군 없는 그룹은 '막혔다'와 '프로브 고장'을 못 가른다")

    def test_silent_group_covers_the_2026_09_14_endpoints(self) -> None:
        """§0 표의 엔드포인트가 전부 들어 있는지 — 호스트 단위로만 본다(URL 손목록 미러 금지)."""
        urls = " ".join(t["url"] for t in probe.TARGET_GROUPS["silent"])
        for host in ("www.mfds.go.kr", "picscheme.org", "cms.mhra.gov.uk", "www.gov.uk",
                     "eudragmdp.ema.europa.eu", "extranet.who.int", "recalls-rappels.canada.ca",
                     "apis.data.go.kr", "nedrug.mfds.go.kr", "admin.ich.org"):
            self.assertIn(host, urls, f"{host} 가 무음 그룹에 없다")

    def test_every_target_has_a_body_check(self) -> None:
        for t in probe.TARGET_GROUPS["all"]:
            self.assertTrue(callable(t["expect"]), t["name"])


class FeedExpectTest(unittest.TestCase):
    def test_items_and_newest_date(self) -> None:
        body = (b"<rss><channel><item><title>a</title><pubDate>Thu, 30 Jul 2026 15:26:00 +0200"
                b"</pubDate></item><item><title>b</title></item></channel></rss>")
        ok, detail = probe._expect_feed_items(body)
        self.assertTrue(ok)
        self.assertIn("항목 2건", detail)
        self.assertIn("30 Jul 2026", detail)

    def test_empty_channel_is_not_ok_even_with_200(self) -> None:
        ok, detail = probe._expect_feed_items(b"<rss><channel><title>x</title></channel></rss>")
        self.assertFalse(ok)
        self.assertIn("0건", detail)

    def test_atom_entries_count(self) -> None:
        body = b"<feed><entry><updated>2026-09-09T14:41:45+01:00</updated></entry></feed>"
        ok, detail = probe._expect_feed_items(body)
        self.assertTrue(ok)
        self.assertIn("2026-09-09", detail)


class MarkerExpectTest(unittest.TestCase):
    def test_all_markers_required(self) -> None:
        check = probe._expect_markers(r"Non Compliant", r"/mhra/gmp/")
        self.assertTrue(check(b"<a href='/mhra/gmp/x'>Non Compliant</a>")[0])
        self.assertFalse(check(b"<a href='/mhra/gmp/x'>Compliant</a>")[0])

    def test_min_hits(self) -> None:
        check = probe._expect_markers(r"\b[QM]\d{1,2}[A-Z]?\b", min_hits=3)
        self.assertFalse(check(b"Q1 only")[0])
        self.assertTrue(check(b"Q1A Q2 M4 Q9")[0])


class OkStatusesTest(unittest.TestCase):
    """serviceKey 없이 쏘는 게이트웨이는 401 이 '도달' 이다 — 상태코드 집합을 타깃이 정한다."""

    def test_gateway_target_accepts_401(self) -> None:
        tgt = next(t for t in probe.SILENT_TARGETS if t["name"].startswith("mfds-admin"))
        self.assertIn(401, tgt["ok_statuses"])

    def test_default_targets_only_accept_200(self) -> None:
        for t in probe.TARGETS:
            self.assertEqual(tuple(t.get("ok_statuses", (200,))), (200,), t["name"])


if __name__ == "__main__":
    unittest.main()
