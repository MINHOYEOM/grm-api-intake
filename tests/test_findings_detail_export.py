"""findings_detail_export — 표제 짝 비교 키(2026-11-12 표제 앞 OCR 잡음)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import findings_detail_export as ex  # noqa: E402


class HeadingKeyTest(unittest.TestCase):
    def test_leading_ocr_noise_is_ignored(self):
        clean = "The responsibilities and procedures applicable to the quality control unit are not in writing."
        for noisy in ("r " + clean, "| " + clean, "| | | " + clean, "· " + clean, "\x01 " + clean,
                      "j " + clean, "ʖ " + clean, "  " + clean):
            self.assertEqual(ex._heading_key(noisy), ex._heading_key(clean), repr(noisy[:6]))

    def test_other_differences_still_fail(self):
        a = "The responsibilities applicable to the quality unit are not fully followed."
        self.assertNotEqual(ex._heading_key(a), ex._heading_key(a.replace("fully ", "")))     # 문구 차이
        self.assertNotEqual(ex._heading_key(a), ex._heading_key("STREET ADDRESS " + a))     # 머리글 접두
        self.assertNotEqual(ex._heading_key(a), ex._heading_key(a + " Specifically,"))      # 꼬리 차이
        self.assertNotEqual(ex._heading_key(a), ex._heading_key("ab " + a))                  # 두 글자 조각은 안 뗀다
        self.assertNotEqual(ex._heading_key(a), ex._heading_key("x the responsibilities applicable"))
        # 여는 괄호·따옴표로 시작하는 표제는 그대로 둔다
        self.assertTrue(ex._heading_key('"Quality unit" procedures').startswith('"Quality'))
        self.assertTrue(ex._heading_key("(b)(4) records were not reviewed").startswith("(b)(4)"))

    def test_queue_links_noisy_raw_heading(self):
        findings = [{"finding_id": "f1", "raw_signal_id": "r1", "published_date": "2024-01-17",
                     "finding_text": "Equipment used in the manufacture is not of appropriate design.",
                     "detail_text": None}]
        raws = [{"raw_signal_id": "r1", "raw_json": {"fda_483_observations": [
            {"number": "7", "deficiency": "| Equipment used in the manufacture is not of appropriate design.",
             "detail": "Specifically, the hopper was cracked."}]}}]
        queue, stats = ex.build_queue(findings, raws)
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(queue[0]["detail_text"], "Specifically, the hopper was cracked.")
        raws[0]["raw_json"]["fda_483_observations"][0]["deficiency"] = "STREET ADDRESS Equipment used in the manufacture"
        _, stats = ex.build_queue(findings, raws)
        self.assertEqual(stats["queued"], 0)


if __name__ == "__main__":
    unittest.main()
