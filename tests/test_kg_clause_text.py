#!/usr/bin/env python3
"""본문 직접 인용 조항(113 · kg_clause_text.py) tests. 오프라인."""
from __future__ import annotations

import unittest
from pathlib import Path

import kg_clause_text as k

_SQL = (Path(__file__).resolve().parent.parent / "web" / "migrations" / "113_finding_clause_from_text.sql").read_text(encoding="utf-8")


class TextCitations(unittest.TestCase):
    def test_eu_annex(self):
        eu = "EU GMP NCR (EudraGMDP)"
        self.assertEqual([c[0] for c in k.parse_text_citations(eu, "compliance with Annex I of the GMP")], ["EU GMP Annex 1"])
        self.assertEqual([c[0] for c in k.parse_text_citations(eu, "e.g. Annex1 gap assessment")], ["EU GMP Annex 1"])
        self.assertEqual([c[0] for c in k.parse_text_citations(eu, "GMP annex 16 and Annex 11")], ["EU GMP Annex 16", "EU GMP Annex 11"])

    def test_eu_annex_rejects_authorisation_appendix_and_other_annex(self):
        eu = "EU GMP NCR (EudraGMDP)"
        self.assertEqual(k.parse_text_citations(eu, "Products (ANNEX 2) - ANNEX 2 of the Manufacturing authorization"), [])
        self.assertEqual(k.parse_text_citations(eu, "Annex 15 and Annex 111"), [])
        self.assertEqual(k.parse_text_citations("FDA 483", "Annex 1"), [])

    def test_hc_guidance_only_literal(self):
        hc = "Health Canada Inspection"
        self.assertEqual(k.parse_text_citations(hc, "Drug GMP Compliance of Foreign Sites (GUI-0080).")[0][0], "Canada GUI-0080")
        self.assertEqual(k.parse_text_citations(hc, "records inadequate (C.02.020 not stated)"), [])

    def test_cfr_key_matches_106_format(self):
        c = k.parse_text_citations("FDA Warning Letter", "violates 21 CFR 211.22(a)(1) and 21CFR 820.30")
        self.assertEqual([x[:2] for x in c], [("21 CFR 211.22", "(a)(1)"), ("21 CFR 820.30", "")])

    def test_sql_mirrors_python_regex(self):
        for frag in (r"(?<!\()\yannex\s*(1|i|11|16)\y(?!\))", r"\yGUI-?\s?(\d{4})\y", "'FDA 483', 'FDA Warning Letter'",
                     "on conflict do nothing", "revoke all on function public.grm_text_citations"):
            self.assertIn(frag, _SQL)


if __name__ == "__main__":
    unittest.main()
