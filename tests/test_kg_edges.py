#!/usr/bin/env python3
"""지식그래프 엣지 tests — 조항 파서(kg_edges.parse_citation ↔ 106 SQL), 용어 엣지(kg_edges.build_term_edges, 107)."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import kg_edges

_ROOT = Path(__file__).resolve().parent.parent
_MIG = _ROOT / "web" / "migrations"
_S106 = (_MIG / "106_finding_clause_edges.sql").read_text(encoding="utf-8")
_S107 = (_MIG / "107_finding_term_edges.sql").read_text(encoding="utf-8")


def _code(s: str) -> str:
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("--"))


# 2026-11-05 라이브 grm_parse_citation(SQL) 실행 결과와 동일하게 고정한 픽스처.
_FIXTURES = {
    "21 CFR 211.22": [("21 CFR 211.22", "", 1.0, "cfr")],
    "21CFR211.22": [("21 CFR 211.22", "", 1.0, "cfr")],
    "21 CFR211.160(b)": [("21 CFR 211.160", "(b)", 1.0, "cfr")],
    "21CFR 211.84(d)(1)": [("21 CFR 211.84", "(d)(1)", 1.0, "cfr")],
    "21 CFR 211": [("21 CFR 211", "", 0.9, "cfr_part")],
    "21CFR113(b)": [],
    "21 U.S.C. § 331(a)": [("21 USC 331", "(a)", 1.0, "usc")],
    "21 U.S.C. § 355": [("21 USC 355", "", 1.0, "usc")],
    "section 301(a)": [("21 USC 331", "(a)", 0.9, "fdca_section")],
    "section 502(f)(1)": [("21 USC 352", "(f)(1)", 0.9, "fdca_section")],
    "section 503B(a)(2)(C)": [("21 USC 353b", "(a)(2)(c)", 0.9, "fdca_section")],
    "section 6": [],
    "section 351(a)": [],
    "[별표 1] 제7.1호": [("[규칙 미상] 별표1 제7.1호", "", 0.7, "mfds_byeolpyo")],
    "[별표 1] 7.1": [("[규칙 미상] 별표1 제7.1호", "", 0.7, "mfds_byeolpyo")],
    "의약품 등의 안전에 관한 규칙 [별표 1] 제7호": [("의약품 등의 안전에 관한 규칙 별표1 제7호", "", 0.95, "mfds_byeolpyo")],
    "[별표 1] 제3.3호 하목": [("[규칙 미상] 별표1 제3.3호", "하목", 0.7, "mfds_byeolpyo")],
    "[별표 1] 제2.1호, 제8.1호": [("[규칙 미상] 별표1 제2.1호", "", 0.7, "mfds_byeolpyo"),
                              ("[규칙 미상] 별표1 제8.1호", "", 0.7, "mfds_byeolpyo")],
    "[별표 8] 행정처분의": [("[규칙 미상] 별표8", "", 0.5, "mfds_table")],
    "[별표8] Ⅱ": [("[규칙 미상] 별표8", "", 0.5, "mfds_table")],
    "의약품 등의 안전에 관한 규칙 [별표 1] ***": [("의약품 등의 안전에 관한 규칙 별표1", "", 0.5, "mfds_table")],
    "*********** ************* ***": [],
}


class ClauseParser(unittest.TestCase):
    def test_fixtures(self):
        for raw, want in _FIXTURES.items():
            with self.subTest(raw=raw):
                self.assertEqual(kg_edges.parse_citation(raw), want)

    def test_both_notations_share_one_key(self):
        a = kg_edges.parse_citation("section 301(a)")[0][:2]
        b = kg_edges.parse_citation("21 U.S.C. § 331(a)")[0][:2]
        self.assertEqual(a, b)
        self.assertEqual(kg_edges.parse_citation("21 CFR 211.22")[0][0],
                         kg_edges.parse_citation("21CFR211.22")[0][0])

    def test_non_fdca_sections_not_linked(self):
        for raw in ("section 351(a)", "section 361", "section 6", "section 1401"):
            self.assertEqual(kg_edges.parse_citation(raw), [], raw)

    def test_unknown_law_keys_stay_apart_from_named_law(self):
        bare = kg_edges.parse_citation("[별표 1] 제7호")[0]
        named = kg_edges.parse_citation("의약품 등의 안전에 관한 규칙 [별표 1] 제7호")[0]
        self.assertNotEqual(bare[0], named[0])
        self.assertLess(bare[2], 0.8)


class ClauseSqlContract(unittest.TestCase):
    def test_section_map_matches_python(self):
        block = _code(_S106).split("join (values", 1)[1].split(") as map", 1)[0]
        sql_map = dict(re.findall(r"\('([^']+)','([^']+)'\)", block))
        self.assertEqual(sql_map, kg_edges.FDCA_TO_USC)

    def test_confidence_constants_match(self):
        code = _code(_S106)
        for conf, basis in ((1.0, "cfr"), (0.9, "cfr_part"), (1.0, "usc"), (0.9, "fdca_section"),
                            (0.5, "mfds_table")):
            self.assertRegex(code, rf"{conf}::numeric, '{basis}'", basis)
        self.assertIn("then 0.7 else 0.95", code)

    def test_additive_and_locked_down(self):
        code = _code(_S106).lower()
        self.assertNotRegex(code, r"drop\s+(table|column|function)|alter table public\.findings")
        for obj in ("function public.grm_parse_citation(text)", "function public.grm_backfill_finding_clause()",
                    "table public.finding_clause"):
            self.assertRegex(code, rf"revoke all on {re.escape(obj)} from public, anon, authenticated")
        self.assertIn("enable row level security", code)
        self.assertIn("on conflict do nothing", code)  # 멱등

    def test_term_table_contract(self):
        code = _code(_S107).lower()
        self.assertIn("references public.findings (finding_id) on delete cascade", code)
        self.assertRegex(code, rf"confidence >= {kg_edges.MIN_CONFIDENCE}")
        self.assertIn("revoke all on table public.finding_term from public, anon, authenticated", code)
        self.assertNotIn("insert into", code)  # 마이그레이션은 행을 쓰지 않는다


class TermEdges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terms = json.loads((_ROOT / "web/data/glossary.json").read_text(encoding="utf-8"))

    def _edges(self, text):
        docs = [{"findings": [{"finding_id": "f1", "text_ko": text}]}]
        edges, _ = kg_edges.build_term_edges(docs, self.terms)
        return {e["term_id"]: e for e in edges}

    def test_exact_acronym_links(self):
        e = self._edges("표준작업지침서(SOP)의 관리가 미흡했다.")
        self.assertIn("sop", e)
        self.assertEqual(e["sop"]["confidence"], 1.0)

    def test_hangul_word_start_rule_blocks_partial(self):
        # 회수율 속의 '수율' 은 용어 '수율' 이 아니다
        e = self._edges("회수율이 기준에 미달했다.")
        self.assertNotIn("yield", e)

    def test_generic_short_words_not_linked(self):
        e = self._edges("품질 기록 제조 관리가 미흡했다.")
        self.assertEqual(e, {})

    def test_no_edge_below_threshold(self):
        docs = [{"findings": [{"finding_id": "f", "text_ko": "완제품 출하판정 밸리데이션 SOP 품질관리"}]}]
        edges, stats = kg_edges.build_term_edges(docs, self.terms)
        self.assertTrue(edges)
        self.assertTrue(all(e["confidence"] >= kg_edges.MIN_CONFIDENCE for e in edges))

    def test_emit_sql_idempotent_and_guarded(self):
        sql = kg_edges.emit_sql([{"finding_id": "f1", "term_id": "sop", "surface": "SOP", "confidence": 1.0}])
        self.assertIn("on conflict do nothing", sql)
        self.assertIn("join public.findings f on f.finding_id = v.finding_id", sql)


if __name__ == "__main__":
    unittest.main()
