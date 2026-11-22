# -*- coding: utf-8 -*-
"""교열 1차는 코드 검사에 카드가 걸려도 전 카드 의미 점검을 건너뛰지 않는다(리허설 1122b).

두 판(company/editorial/copyedit.md · next/copyedit.md)이 같은 문구를 가져야 한다.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = (
    os.path.join(ROOT, "company", "editorial", "copyedit.md"),
    os.path.join(ROOT, "company", "editorial", "next", "copyedit.md"),
)
MUST_HAVE = (
    "그래도 의미 점검은 건너뛰지 않는다 — 전 카드에 그대로 한다",
    "코드 검사 실패 카드 ∪ 의미 반려 카드",
    "국문 병기 위반(483·WL·WHOPIR)",
    "**반려됐던 카드만** 다시 판정한다(1차에 이미 전 카드를 점검했다)",
)
MUST_NOT_HAVE = (
    "의미 점검을 하지 않고 그 카드들을",
    "(1차에 못 했으므로)",
)


class CopyeditSemanticAllCards(unittest.TestCase):
    def test_both_editions_have_new_rule(self):
        for p in FILES:
            with open(p, encoding="utf-8") as f:
                s = f.read()
            for t in MUST_HAVE:
                self.assertIn(t, s, f"{os.path.relpath(p, ROOT)}: {t}")
            for t in MUST_NOT_HAVE:
                self.assertNotIn(t, s, f"{os.path.relpath(p, ROOT)}: {t}")


if __name__ == "__main__":
    unittest.main()
