# -*- coding: utf-8 -*-
"""영문 슬롯 게이트 — 빠진 호를 발행 PR 전에 잡는지."""
import json
import os
import tempfile
import unittest

import check_en_slots as g

FULL = {"title_issue": "t", "summary": "s", "implication": "i", "key_facts": ["k"], "checks": ["c"]}


def _doc(cards, tldr=("a",)):
    return {"brief": {"en": {"tldr": list(tldr)} if tldr else {}}, "cards": cards}


class GateTest(unittest.TestCase):
    def test_complete_passes(self):
        self.assertEqual(g.find_problems(_doc([{"id": "a", "en": FULL}])), [])

    def test_no_en_listed_with_count(self):
        out = g.find_problems(_doc([{"id": "a"}, {"id": "b", "en": FULL}]))
        self.assertTrue(any("1/2" in p and "a" in p for p in out))

    def test_partial_names_missing_field(self):
        out = g.find_problems(_doc([{"id": "a", "en": dict(FULL, checks=[])}]))
        self.assertTrue(any("checks" in p for p in out))

    def test_missing_tldr(self):
        out = g.find_problems(_doc([{"id": "a", "en": FULL}], tldr=None))
        self.assertTrue(any("tldr" in p for p in out))

    def test_cli_exit_codes(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_doc([{"id": "a"}]), f)
        self.assertEqual(g.main([p]), 1)
        self.assertEqual(g.main([p, "--warn-only"]), 0)
        self.assertEqual(g.main([os.path.join(d, "none.json")]), 2)


if __name__ == "__main__":
    unittest.main()
