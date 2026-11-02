"""MFDS RSS 게시일은 KST 달력 날짜 — GMT 타임스탬프의 UTC 날짜를 쓰면 하루 어긋난다(2026-11-03).

실측: 사전 GMP 평가 운영지침(data0010_15310) `pubDate = Thu, 29 Oct 2026 22:53:25 GMT`
= KST 10-30 07:53. 공용 파서는 UTC 날짜 10-29 를 돌려줘 카드 발행일이 10-29 로 나왔다.
"""

from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds as cm  # noqa: E402


def _rss_item(pub: str) -> ET.Element:
    return ET.fromstring(f"<item><title>t</title><link>l</link><pubDate>{pub}</pubDate></item>")


class MfdsKstDateTest(unittest.TestCase):
    def test_gmt_late_evening_is_next_day_kst(self):
        _, _, date_iso, _ = cm._rss2_fields(_rss_item("Thu, 29 Oct 2026 22:53:25 GMT"))
        self.assertEqual(date_iso, "2026-10-30")

    def test_gmt_morning_stays_same_day(self):
        _, _, date_iso, _ = cm._rss2_fields(_rss_item("Fri, 30 Oct 2026 07:36:33 GMT"))
        self.assertEqual(date_iso, "2026-10-30")

    def test_kst_offset_unchanged(self):
        self.assertEqual(cm._kst_date_iso("Fri, 30 Oct 2026 00:10:00 +0900", False), "2026-10-30")

    def test_atom_z_converted_to_kst(self):
        self.assertEqual(cm._kst_date_iso("2026-10-29T22:53:25Z", True), "2026-10-30")

    def test_naive_and_unparseable_fall_back(self):
        self.assertEqual(cm._kst_date_iso("2026-10-30", True), "2026-10-30")
        self.assertEqual(cm._kst_date_iso("garbage", False), "")


if __name__ == "__main__":
    unittest.main()
