#!/usr/bin/env python3
"""Health Canada 조항(C.02.xxx) 연결 · kg_hc_clause.py tests. 오프라인."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import kg_hc_clause as k

ROOT = Path(__file__).resolve().parent.parent


def raw(*obs):
    return json.dumps({"hc_inspection_observations": [
        {"no": str(i + 1), "regulation": r, "summary": s} for i, (r, s) in enumerate(obs)]})


def f(fid, text, doc="d1"):
    return {"finding_id": fid, "raw_signal_id": doc, "finding_text": text}


class Parse(unittest.TestCase):
    def test_regulation_forms(self):
        self.assertEqual(k.parse_regulation("C.02.015 - Quality control department"), "C.02.015")
        self.assertEqual(k.parse_regulation("C.02.017  - Packaging material testing"), "C.02.017")
        self.assertEqual(k.parse_regulation("  C.02.000 - GMP Sections"), "C.02.000")

    def test_rejects_non_clause(self):
        for bad in (None, "", "GUI-0080", "Quality control", "C.2.15", "xC.02.015"):
            self.assertIsNone(k.parse_regulation(bad))

    def test_observations_skip_bad_json_and_shapes(self):
        self.assertEqual(k.parse_observations(None), [])
        self.assertEqual(k.parse_observations("not json"), [])
        self.assertEqual(k.parse_observations("{bad"), [])
        self.assertEqual(k.parse_observations(json.dumps({"hc_inspection_observations": "x"})), [])
        self.assertEqual(k.parse_observations(raw(("Records", "text"))), [])


class Build(unittest.TestCase):
    def test_exact_match_whitespace_and_case_insensitive(self):
        r = {"d1": raw(("C.02.020 - Records", "The  recorded information was inadequate."))}
        e, st = k.build_edges([f("a", "The recorded information was inadequate. ")], r)
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["citation_key"], e[0]["citation_paragraph"], e[0]["basis"], e[0]["confidence"]),
                         ("Canada C.02.020", "", "hc_regulation_raw", 0.95))
        self.assertEqual(e[0]["raw_ref"], "C.02.020 - Records")
        self.assertEqual(st["matched"], 1)

    def test_same_sentence_under_two_clauses_links_both_lower_confidence(self):
        r = {"d1": raw(("C.02.015 - QC", "Same."), ("C.02.014 - QC", "Same."))}
        e, st = k.build_edges([f("a", "Same.")], r)
        self.assertEqual(sorted(x["citation_key"] for x in e), ["Canada C.02.014", "Canada C.02.015"])
        self.assertTrue(all(x["confidence"] == 0.9 for x in e))
        self.assertEqual(st["multi_key"], 1)

    def test_no_cross_document_match_and_unmatched_counted(self):
        r = {"d1": raw(("C.02.020 - Records", "X.")), "d2": raw(("C.02.011 - MC", "Y."))}
        e, st = k.build_edges([f("a", "Y.", "d1"), f("b", "Z.", "d2"), f("c", "X.", "d9")], r)
        self.assertEqual(e, [])
        self.assertEqual((st["unmatched"], st["no_raw"]), (3, 1))

    def test_idempotent_keys_unique(self):
        r = {"d1": raw(("C.02.015 - QC", "S."), ("C.02.015 - QC", "S."))}
        e, _ = k.build_edges([f("a", "S.")], r)
        self.assertEqual(len(e), 1)


class Wiring(unittest.TestCase):
    def test_flag_default_off_in_daily_link(self):
        src = (ROOT / "kg_link_new.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("GRM_KG_HC_CLAUSE") == "1"', src)
        self.assertIn("kg_hc_clause.link_recent", src)
        wf = (ROOT / ".github" / "workflows" / "grm-kg-link.yml").read_text(encoding="utf-8")
        self.assertNotIn("GRM_KG_HC_CLAUSE", wf)  # 켜는 것은 대표 승인 뒤 별도 변경

    def test_write_is_idempotent_insert_only(self):
        src = (ROOT / "kg_hc_clause.py").read_text(encoding="utf-8")
        self.assertIn("resolution=ignore-duplicates", src)
        self.assertNotIn("requests.patch", src)
        self.assertNotIn("requests.delete", src)

    def test_key_not_logged(self):
        src = (ROOT / "kg_hc_clause.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"print\(.*key", src))


if __name__ == "__main__":
    unittest.main()
