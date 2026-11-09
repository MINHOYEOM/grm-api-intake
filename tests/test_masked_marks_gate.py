"""게이트 8 — 카드 본문 슬롯의 FDA 가림 표기 `(b)(4)` 노출 차단."""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import assemble_publish_brief as apb  # noqa: E402


def _card(**kw):
    base = {"id": "c1", "title_issue": "t", "summary": "s", "implication": "i",
            "key_facts": ["지적: 가림 없음"], "checks": ["점검?"],
            "en": {"title_issue": "t", "summary": "s", "implication": "i",
                   "key_facts": ["Finding: none"], "checks": ["Check?"]}}
    base.update(kw)
    return base


class MaskedMarkGate(unittest.TestCase):
    def test_clean_card_passes(self):
        self.assertEqual(apb.lint_masked_marks_in_body([_card()]), [])

    def test_ko_key_facts_flagged(self):
        errs = apb.lint_masked_marks_in_body(
            [_card(key_facts=["소르비톨 용액 약 (b)(4) 회 입고분"])])
        self.assertEqual(len(errs), 1)
        self.assertIn("key_facts[0]", errs[0])

    def test_each_slot_and_en_flagged(self):
        for slot in ("title_issue", "summary", "implication"):
            with self.subTest(slot=slot):
                self.assertTrue(apb.lint_masked_marks_in_body([_card(**{slot: "x (b)(6) y"})]))
                en = dict(_card()["en"], **{slot: "x (b)(7)(C) y"})
                self.assertTrue(apb.lint_masked_marks_in_body([_card(en=en)]))
        en = dict(_card()["en"], checks=["Ask (b)(4)?"])
        self.assertTrue(apb.lint_masked_marks_in_body([_card(en=en)]))

    def test_cfr_citation_not_flagged(self):
        """704(a)(4)·211.84(d)(1) 같은 정상 조항 표기는 가림 표기가 아니다."""
        errs = apb.lint_masked_marks_in_body(
            [_card(key_facts=["근거: 704(a)(4) 요청", "21 CFR 211.84(d)(1)"])])
        self.assertEqual(errs, [])

    def test_source_fields_ignored(self):
        card = _card(quotes=[{"original": "contains (b)(4)"}],
                     deterministic_detail={"description": "(b)(4) 를 포함한"},
                     source_text="approximately (b)(4) shipments")
        self.assertEqual(apb.lint_masked_marks_in_body([card]), [])


class DeltaFixed(unittest.TestCase):
    def test_delta_1109_has_no_masked_marks(self):
        d = json.loads((ROOT / "web/data/deltas/delta_2026_11_09.json")
                       .read_text(encoding="utf-8"))
        cards = [dict(v, id=k) for k, v in d["cards"].items()]
        self.assertEqual(apb.lint_masked_marks_in_body(cards), [])


if __name__ == "__main__":
    unittest.main()
