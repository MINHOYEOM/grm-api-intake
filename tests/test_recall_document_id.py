"""OpenFDA 회수 번호가 비거나 "N/A" 일 때의 대체 document_id (2026-11-08 수집 완전성 ③-2).

종전에는 빈 번호는 insert_items 에서 통째로 건너뛰고, "N/A" 번호끼리는
`OpenFDA Recall::N/A` 한 키로 합쳐졌다.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collect_intake as ci  # noqa: E402


def _rec(**kw):
    base = {"recall_number": "D-0123-2026", "event_id": "98765",
            "product_description": "Metformin HCl ER Tablets 500 mg",
            "code_info": "Lot A123", "recalling_firm": "Acme Pharma",
            "report_date": "20261105", "recall_initiation_date": "20261020",
            "classification": "Class II", "reason_for_recall": "Failed dissolution"}
    base.update(kw)
    return base


class RecallDocumentIdTest(unittest.TestCase):
    def test_real_recall_number_is_unchanged(self):
        """정상 번호는 그대로 — 이미 Notion 에 있는 행과 같은 키여야 재적재가 없다."""
        self.assertEqual(ci.recall_document_id(_rec()), "D-0123-2026")
        self.assertEqual(ci.recall_document_id(_rec(recall_number="  D-0123-2026 ")), "D-0123-2026")

    def test_blank_and_placeholder_get_fallback_key(self):
        for num in ("", None, "N/A", "n/a", "NA", " none ", "-"):
            doc_id = ci.recall_document_id(_rec(recall_number=num))
            self.assertRegex(doc_id, r"^recall-fb-[0-9a-f]{16}$", repr(num))

    def test_fallback_is_stable_across_placeholder_spelling_and_whitespace(self):
        a = ci.recall_document_id(_rec(recall_number="N/A"))
        b = ci.recall_document_id(_rec(recall_number="",
                                       product_description="Metformin  HCl ER Tablets 500 mg "))
        self.assertEqual(a, b)

    def test_different_recalls_with_na_no_longer_merge(self):
        a = ci.recall_document_id(_rec(recall_number="N/A"))
        other_lot = ci.recall_document_id(_rec(recall_number="N/A", code_info="Lot B456"))
        other_firm = ci.recall_document_id(_rec(recall_number="N/A", recalling_firm="Beta Labs",
                                                event_id="11111"))
        self.assertEqual(len({a, other_lot, other_firm}), 3)

    def test_fallback_cannot_collide_with_fda_recall_number_format(self):
        doc_id = ci.recall_document_id(_rec(recall_number="N/A"))
        self.assertNotRegex(doc_id, r"^[A-Z]-\d{4}-\d{4}$")

    def test_all_fields_empty_stays_empty(self):
        """원천에 아무 단서도 없으면 지어내지 않고 빈 값 — 장부의 missing_document_id 경로."""
        empty = {k: "" for k in ci._RECALL_FALLBACK_KEY_FIELDS}
        self.assertEqual(ci.recall_document_id({"recall_number": "N/A", **empty}), "")

    def test_item_and_dedup_key(self):
        item = ci._recall_to_item(_rec(recall_number=""), "https://api.fda.gov/drug/enforcement.json")
        self.assertTrue(item.document_id.startswith("recall-fb-"))
        self.assertEqual(item.headline, "Metformin HCl ER Tablets 500 mg")

    def test_insert_items_no_longer_skips_blank_number(self):
        items = [ci._recall_to_item(_rec(recall_number=""), "q"),
                 ci._recall_to_item(_rec(recall_number="N/A", code_info="Lot B456"), "q")]
        existing: set[str] = set()
        ins, sk, fail = ci.insert_items("t", "db", items, date(2026, 11, 8), ci.now_kst(),
                                        existing, True)
        self.assertEqual((ins, sk, fail), (2, 0, 0))
        self.assertEqual(len(existing), 2)
        self.assertTrue(all(k.startswith(f"{ci.SOURCE_RECALL}::recall-fb-") for k in existing))


if __name__ == "__main__":
    unittest.main()
