"""web/render.py `_TIDY_DATE_RE` 가 실제로 날짜를 잡는지(0x08 백스페이스 사고 재발 방지).

2026-11-04 #1326 에서 `\b` 가 실제 백스페이스(0x08) 문자로 박혀 정규식이 영영 맞지 않았다.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

import render  # noqa: E402


class TidyDateRegexTest(unittest.TestCase):
    def test_pattern_has_no_control_characters(self):
        self.assertNotRegex(render._TIDY_DATE_RE.pattern, r"[\x00-\x08\x0b-\x1f]")

    def test_us_date_is_normalized_to_iso(self):
        self.assertEqual(render._norm_tidy_value("02/27/2026"), "2026-02-27")

    def test_date_inside_text_is_normalized(self):
        self.assertEqual(render._norm_tidy_value("실사일: 11/10/2023 · 483").split(": ")[1], "2023-11-10")

    def test_longer_digit_runs_are_not_dates(self):
        self.assertEqual(render._norm_tidy_value("1102/27/20266"), "1102/27/20266")


if __name__ == "__main__":
    unittest.main()
