#!/usr/bin/env python3
"""링크드인 사건 해부형 카드(시범) 테스트 — 순수 층만(Chrome·네트워크 0).

CI 는 `tests/test_web_linkedin_anatomy.py` shim 으로 순회. 직접:
  python web/tests/test_linkedin_anatomy.py

픽스처는 발행본 `brief_web_2026_11_02.json` + 시범 슬롯 `web/data/linkedin_anatomy/2026-11-02.json`.
핵심은 '검증 게이트' — 슬롯이 근거 없는 숫자·상투어·실명·누락을 담으면 덱이 **만들어지지 않는다**.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_anatomy as la  # noqa: E402
import linkedin_cards as lc  # noqa: E402

BRIEF = WEB_DIR / "data" / "briefs" / "brief_web_2026_11_02.json"
SLOTS = WEB_DIR / "data" / "linkedin_anatomy" / "2026-11-02.json"
GLOSSARY = WEB_DIR / "data" / "glossary.json"


def _doc() -> dict:
    return json.loads(BRIEF.read_text(encoding="utf-8"))


def _slots() -> dict:
    return json.loads(SLOTS.read_text(encoding="utf-8"))


class SelectionTest(unittest.TestCase):
    def test_picks_the_letter_with_most_concrete_figures(self):
        card = la.pick_anatomy_card(_doc())
        self.assertIsNotNone(card)
        self.assertEqual(card["id"], _slots()["card_id"])

    def test_only_letters_with_body_evaluation_and_requests_are_eligible(self):
        card = copy.deepcopy(la.pick_anatomy_card(_doc()))
        self.assertTrue(la.anatomy_eligible(card))
        card["source_body_captured"] = False
        self.assertFalse(la.anatomy_eligible(card))
        card["source_body_captured"] = True
        card["deep_analysis"]["fda_evaluation"] = ""
        self.assertFalse(la.anatomy_eligible(card))

    def test_no_eligible_card_is_none_not_an_error(self):
        self.assertIsNone(la.pick_anatomy_card({"cards": []}))


class ValidationGateTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc()
        self.card = la.pick_anatomy_card(self.doc)

    def _problems(self, mutate):
        s = _slots()
        mutate(s)
        return la.validate_slots(self.card, s)

    def test_pilot_slots_pass(self):
        self.assertEqual(la.validate_slots(self.card, _slots()), [])

    def test_evidence_must_be_verbatim_from_source(self):
        p = self._problems(lambda s: s["cover"]["bars"][0].update(evidence="2024년 3월부터 2026년 4월까지 100건이 넘는 검출"))
        self.assertTrue(any("근거가 원천에 없다" in x for x in p), p)

    def test_invented_number_is_blocked(self):
        p = self._problems(lambda s: s["s02"]["rows"][0].update(text="ISO 5 구역에서 미생물이 120건 넘게 검출됐다."))
        self.assertTrue(any("근거에 없는 숫자" in x and "120" in x for x in p), p)

    def test_number_in_text_without_evidence_must_exist_in_source(self):
        p = self._problems(lambda s: s["s03"].update(proposal="월 987회 동정률을 확인한다."))
        self.assertTrue(any("원천에 없는 숫자" in x for x in p), p)

    def test_ai_tell_phrases_are_blocked(self):
        for phrase in ("조사가 부족했다는 신호다.", "점검할 필요가 있다.", "다양한 조치를 요구했다."):
            p = self._problems(lambda s, ph=phrase: s["s03"].update(proposal=ph))
            self.assertTrue(any("AI 티" in x for x in p), (phrase, p))

    def test_firm_name_must_not_leak(self):
        p = self._problems(lambda s: s["caption"].update(lead="Northland 는 21 CFR 212.20(d) 위반을 지적받았습니다."))
        self.assertTrue(any("업체 실명" in x for x in p), p)

    def test_checklist_must_be_questions(self):
        p = self._problems(lambda s: s["s06"]["checks"].__setitem__(0, "동정 기록 방식 점검"))
        self.assertTrue(any("질문형" in x for x in p), p)

    def test_every_fda_request_must_be_covered(self):
        p = self._problems(lambda s: s["s04"]["items"].pop())
        self.assertTrue(any("FDA 요구" in x for x in p), p)

    def test_fda_quote_must_be_verbatim(self):
        p = self._problems(lambda s: s["s03"].update(fda_quote="FDA는 조사를 수행할 수 없었다고 했다."))
        self.assertTrue(any("fda_quote" in x for x in p), p)

    def test_overlong_text_is_blocked(self):
        p = self._problems(lambda s: s["cover"]["headline"].__setitem__(0, "ISO 5 구역에서 균이 백 건 넘게 나왔는데 조사하지 못했다는 이야기"))
        self.assertTrue(any("너무 길다" in x for x in p), p)

    def test_wrong_card_id_is_blocked(self):
        p = self._problems(lambda s: s.update(card_id="000000000000"))
        self.assertTrue(any("card_id" in x for x in p), p)


class DeckTest(unittest.TestCase):
    def setUp(self):
        self.deck = la.build_anatomy_deck(_doc(), _slots(),
                                          glossary=json.loads(GLOSSARY.read_text(encoding="utf-8")))

    def test_six_slides_in_benchmark_order(self):
        self.assertEqual([s["kind"] for s in self.deck["slides"]],
                         ["a_cover", "a_table", "a_flow", "a_boxes", "a_pairs", "a_check"])
        self.assertEqual([s["idx"] for s in self.deck["slides"]], [1, 2, 3, 4, 5, 6])

    def test_fda_request_count_and_deadline_come_from_the_letter(self):
        boxes = self.deck["slides"][3]
        self.assertEqual(len(boxes["items"]), 4)
        self.assertIn("15영업일", boxes["deadline"])

    def test_no_firm_name_anywhere_in_output(self):
        html = la.render_html(self.deck, font_links=False) + self.deck["caption"]
        self.assertNotIn("Northland", html)
        self.assertNotIn("Nuclear Medicine", html)

    def test_ai_notice_kept_on_every_slide(self):
        for s in self.deck["slides"]:
            self.assertEqual(s["ai"], lc.AI_NOTE)

    def test_html_escapes_text(self):
        s = _slots()
        s["s06"]["question"] = "우리 ISO 5 <b>검출</b> 건수와 동정 건수는 몇 대 몇인가?"
        deck = la.build_anatomy_deck(_doc(), s)
        self.assertNotIn("<b>검출</b>", la.render_html(deck, font_links=False))

    def test_deterministic(self):
        again = la.build_anatomy_deck(_doc(), _slots())
        self.assertEqual(la.render_html(self.deck), la.render_html(again))

    def test_invalid_slots_raise_instead_of_building(self):
        s = _slots()
        s["s02"]["rows"][0]["text"] = "ISO 5 구역에서 미생물이 999건 검출됐다."
        with self.assertRaises(la.AnatomyError):
            la.build_anatomy_deck(_doc(), s)

    def test_live_deck_module_does_not_depend_on_the_pilot(self):
        src = (WEB_DIR / "linkedin_cards.py").read_text(encoding="utf-8")
        self.assertNotIn("linkedin_anatomy", src)
        self.assertNotIn("import anthropic", (WEB_DIR / "linkedin_anatomy.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
