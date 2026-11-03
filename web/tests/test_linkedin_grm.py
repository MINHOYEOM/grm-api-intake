#!/usr/bin/env python3
"""링크드인 사건 해부 GRM 판(v2 시범) 테스트 — 순수 층만(Chrome·네트워크 0).

CI 는 `tests/test_web_linkedin_grm.py` shim 으로 순회. 직접: python web/tests/test_linkedin_grm.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_anatomy as la  # noqa: E402
import linkedin_cards as lc  # noqa: E402
import linkedin_grm as lg  # noqa: E402

BRIEF = WEB_DIR / "data" / "briefs" / "brief_web_2026_11_02.json"
SLOTS = WEB_DIR / "data" / "linkedin_anatomy" / "2026-11-02.v2.json"


def _doc() -> dict:
    return json.loads(BRIEF.read_text(encoding="utf-8"))


def _slots() -> dict:
    return json.loads(SLOTS.read_text(encoding="utf-8"))


FINDINGS = json.loads(lg.FINDINGS.read_text(encoding="utf-8"))
GLOSSARY = json.loads(lg.GLOSSARY.read_text(encoding="utf-8"))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.card = la.pick_anatomy_card(_doc())

    def _p(self, mutate):
        s = _slots()
        mutate(s)
        return lg.validate_v2(self.card, s, FINDINGS, GLOSSARY)

    def test_pilot_passes(self):
        self.assertEqual(lg.validate_v2(self.card, _slots(), FINDINGS, GLOSSARY), [])

    def test_quote_must_be_verbatim_both_languages(self):
        p = self._p(lambda s: s["s02"]["quotes"][0].update(en="More than 90 ISO 5 isolates were not identified"))
        self.assertTrue(any("quotes[0].en" in x for x in p), p)
        p = self._p(lambda s: s["s02"]["quotes"][1].update(ko="교육은 한 장이었다."))
        self.assertTrue(any("quotes[1].ko" in x for x in p), p)

    def test_db_numbers_come_from_the_db_not_the_writer(self):
        p = self._p(lambda s: s["s03"]["lines"].__setitem__(0, "경고서한 431건 중 환경모니터링 분류는 99건이다."))
        self.assertTrue(any("숫자" in x and "99" in x for x in p), p)

    def test_peer_quote_must_exist_in_db_document(self):
        p = self._p(lambda s: s["s03"]["peer"].update(en="All isolates are never identified"))
        self.assertTrue(any("peer.en" in x for x in p), p)

    def test_dots_must_match_evidence(self):
        p = self._p(lambda s: s["cover"]["dots"].update(marked=3))
        self.assertTrue(any("cover.dots" in x for x in p), p)

    def test_ai_tell_and_firm_leak_blocked(self):
        p = self._p(lambda s: s["caption"].update(lead="Northland 는 조사가 부족했다는 신호다."))
        self.assertTrue(any("AI 티" in x for x in p) and any("실명" in x for x in p), p)

    def test_not_stated_needs_basis_and_question_form(self):
        p = self._p(lambda s: s["s06"]["not_stated"][0].pop("basis"))
        self.assertTrue(any("not_stated" in x for x in p), p)
        p = self._p(lambda s: s["s06"].update(question="동정 건수를 확인한다"))
        self.assertTrue(any("질문형" in x for x in p), p)

    def test_unknown_glossary_id_blocked(self):
        p = self._p(lambda s: s["s06"].update(terms=["no-such-term"]))
        self.assertTrue(any("용어사전에 없는" in x for x in p), p)


class DbPositionTest(unittest.TestCase):
    def test_counts_are_derived_from_documents(self):
        db = lg.db_position(FINDINGS, la.pick_anatomy_card(_doc()))
        self.assertEqual(db["wl_total"], sum(1 for d in FINDINGS["documents"] if d["source"] == "FDA Warning Letter"))
        row = next(r for r in db["em_rows"] if r["label"] == "FDA 경고서한")
        self.assertEqual(row["total"], db["wl_total"])
        self.assertLessEqual(row["n"], row["total"])


class DeckTest(unittest.TestCase):
    def setUp(self):
        self.deck = lg.build_grm_deck(_doc(), _slots(), FINDINGS, GLOSSARY)

    def test_six_slides_with_signature_corners(self):
        self.assertEqual([s["kind"] for s in self.deck["slides"]],
                         ["g_cover", "g_orig", "g_db", "g_vs", "g_req", "g_end"])
        self.assertEqual([s.get("corner") for s in self.deck["slides"][1:]],
                         ["원문 대조", "DB로 본 자리", "회신과 판단", "FDA가 요구한 것", "서한에 없는 말"])

    def test_no_hashtags_and_no_closing_question_in_caption(self):
        self.assertNotIn("#", self.deck["caption"].split("전체 해설")[0])

    def test_no_firm_name_and_ai_notice_kept(self):
        out = lg.render_html(self.deck, font_links=False) + self.deck["caption"]
        self.assertNotIn("Northland", out)
        for s in self.deck["slides"]:
            self.assertEqual(s["ai"], lc.AI_NOTE)

    def test_invalid_slots_raise(self):
        s = _slots()
        s["cover"]["dots"]["marked"] = 9
        with self.assertRaises(la.AnatomyError):
            lg.build_grm_deck(_doc(), s, FINDINGS, GLOSSARY)

    def test_no_paid_api_and_live_module_untouched(self):
        self.assertNotIn("import anthropic", (WEB_DIR / "linkedin_grm.py").read_text(encoding="utf-8"))
        self.assertNotIn("linkedin_grm", (WEB_DIR / "linkedin_cards.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
