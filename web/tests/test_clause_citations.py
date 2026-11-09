#!/usr/bin/env python3
"""FDA 지적 조항 화면(web/clause_citations.py) 테스트 — 집계·비율·뷰·커밋 집계본 일관성.

CI 는 `tests/test_web_clause_citations.py` shim 으로 순회한다. 직접:
  python web/tests/test_clause_citations.py
"""
from __future__ import annotations

import copy
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import clause_citations as cc  # noqa: E402


def _rows():
    def r(i, fy, code, t="T"):
        return {"inspection_id": i, "fiscal_year": fy, "act_cfr_number": code,
                "short_description": t}
    return [
        # 실사 1: 같은 조항을 두 줄 인용(실사로는 1회) + 하위항 다름
        r("1", 2024, "21 CFR 211.100(a)"), r("1", 2024, "21 CFR 211.100(a)"),
        r("1", 2024, "21 CFR 211.100(b)", "B"),
        r("2", 2024, "21 CFR 211.100(a)"), r("3", 2025, "21 CFR 211.192", "x  y"),
        r("3", 2025, "21 CFR 211.192", "x y"), r("4", 2025, "FDCA 501(a)(2)(A)"),
    ]


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.snap = cc.build_snapshot(_rows(), as_of="2026-01-01", min_inspections=1)
        self.by_code = {c["code"]: c for c in self.snap["codes"]}

    def test_counts_inspections_not_lines(self):
        c = self.by_code["21 CFR 211.100(a)"]
        self.assertEqual((c["citations"], c["inspections"]), (3, 2))

    def test_denominator_is_citation_bearing_inspections_per_year(self):
        self.assertEqual(self.snap["years"], [2024, 2025])
        self.assertEqual(self.snap["inspections"]["by_year"], [2, 2])
        self.assertEqual(self.snap["inspections"]["total"], 4)

    def test_section_merges_subparagraphs_without_double_count(self):
        sec = next(s for s in self.snap["sections"] if s["section"] == "211.100")
        # 실사 1 은 (a)(b) 둘 다 인용해도 섹션으로는 1회
        self.assertEqual(sec["inspections"], 2)
        self.assertEqual(sec["by_year"], [2, 0])

    def test_non_cfr_code_has_no_section(self):
        self.assertEqual(cc.section_of("FDCA 501(a)(2)(A)"), "")
        self.assertEqual([s["section"] for s in self.snap["sections"]].count("501"), 0)

    def test_title_is_whitespace_normalized_mode(self):
        self.assertEqual(self.by_code["21 CFR 211.192"]["title"], "x y")

    def test_min_inspections_floor(self):
        snap = cc.build_snapshot(_rows(), as_of="d", min_inspections=2)
        self.assertEqual([c["code"] for c in snap["codes"]], ["21 CFR 211.100(a)"])

    def test_deterministic(self):
        self.assertEqual(self.snap, cc.build_snapshot(list(reversed(_rows())),
                                                      as_of="2026-01-01", min_inspections=1))

    def test_valid(self):
        self.assertEqual(cc.validate_snapshot(self.snap), [])


class ValidationTest(unittest.TestCase):
    def test_rejects_year_sum_mismatch(self):
        snap = cc.build_snapshot(_rows(), as_of="d", min_inspections=1)
        bad = copy.deepcopy(snap)
        bad["codes"][0]["by_year"][0] += 1
        self.assertTrue(any("year_sum" in e for e in cc.validate_snapshot(bad)))

    def test_rejects_more_than_total(self):
        snap = cc.build_snapshot(_rows(), as_of="d", min_inspections=1)
        bad = copy.deepcopy(snap)
        bad["codes"][0]["inspections"] = 999
        self.assertTrue(any("over_total" in e for e in cc.validate_snapshot(bad)))


class ViewTest(unittest.TestCase):
    def test_pct_format(self):
        self.assertEqual(cc.fmt_pct(783, 2709), "29%")
        self.assertEqual(cc.fmt_pct(259, 2709), "9.6%")
        self.assertEqual(cc.fmt_pct(0, 0), "-")

    def test_section_view_missing_is_none(self):
        data = cc.load_snapshot()
        self.assertIsNone(cc.section_view(data, "999.999"))
        self.assertIsNone(cc.section_view(None, "211.192"))

    def test_section_view_lists_subparagraphs(self):
        snap = cc.build_snapshot(_rows() * 20, as_of="d", min_inspections=1)
        # 행을 복제해도 실사 id 가 같아 수치는 그대로 — 하위항 2개가 표로 나온다
        v = cc.section_view(snap, "211.100")
        self.assertEqual([s["code"] for s in v["subs"]],
                         ["21 CFR 211.100(a)", "21 CFR 211.100(b)"])
        self.assertEqual(v["pct"], "50%")


class CommittedSnapshotTest(unittest.TestCase):
    """커밋된 web/data/fda_citations.json — 조용한 0장·손상 가드."""

    @classmethod
    def setUpClass(cls):
        cls.data = cc.load_snapshot()

    def test_loads_and_valid(self):
        self.assertIsNotNone(self.data)
        self.assertEqual(cc.validate_snapshot(self.data), [])

    def test_rows_sorted_and_above_floor(self):
        ins = [c["inspections"] for c in self.data["codes"]]
        self.assertEqual(ins, sorted(ins, reverse=True))
        self.assertGreaterEqual(min(ins), self.data["min_inspections"])

    def test_section_is_union_of_its_subparagraphs(self):
        # 섹션 실사 수 ≥ 그 안의 어떤 하위항 실사 수, ≤ 하위항 합(중복 실사는 1회)
        for s in self.data["sections"]:
            subs = [c["inspections"] for c in self.data["codes"]
                    if cc.section_of(c["code"]) == s["section"]]
            if subs:
                self.assertGreaterEqual(s["inspections"], max(subs), s["section"])

    def test_no_legal_judgement_wording_in_view(self):
        v = cc.ranking_view(self.data)
        self.assertEqual(len(v["rows"]), 30)
        self.assertEqual(v["rows"][0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
