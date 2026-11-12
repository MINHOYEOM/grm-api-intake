"""raw_signals.modality — 수집 시점 기록 + 기존 행 소급(2026-11-12 재생 대조 A).

Notion 적재(`grm_notion.notion_create_page`)와 정본 행(`findings_store.raw_signal_from_intake_item`)이
같은 함수·같은 입력으로 제품군을 정해야 DB 로 만든 handoff 가 노션 것과 같아진다.
"""
from __future__ import annotations

import json
import unittest

import backfill_raw_signals_modality as bm
import collect_intake as ci
import findings_store
from grm_taxonomy import compute_modality


def _item(**kw) -> ci.IntakeItem:
    base = dict(source="FDA 483", document_id="fda483-1", date_iso="2026-11-01",
                headline="[FDA 483] Acme Biologics", official_url="https://www.fda.gov/media/1/download",
                type_or_class="483", firm="Acme Biologics", body="",
                raw_payload={"establishment_type": "Biological Drug Product Manufacturer",
                             "fda_483_observations": [{"number": "1",
                                                       "deficiency": "monoclonal antibody lot release"}]})
    base.update(kw)
    return ci.IntakeItem(**base)


def _notion_path_modality(item: ci.IntakeItem) -> str:
    # grm_notion.notion_create_page 와 같은 호출(입력 순서까지).
    return compute_modality(item.raw_payload, item.headline, item.body, item.type_or_class, item.firm)


class IntakeRecordCarriesModalityTest(unittest.TestCase):
    def test_record_modality_equals_notion_path(self) -> None:
        for item in (
            _item(),
            _item(source="OpenFDA Recall", document_id="D-1", headline="Amlodipine Tablets 10 mg",
                  type_or_class="Class II", firm="X", raw_payload={"product_type": "Drugs",
                                                                    "route": ["ORAL"]}),
            _item(source="EMA", document_id="e1", headline="Meeting highlights", type_or_class="news",
                  firm="", raw_payload={}),
        ):
            with self.subTest(source=item.source):
                rec = findings_store.raw_signal_from_intake_item(item)
                expected = _notion_path_modality(item)
                self.assertEqual(rec.get("modality") or "", expected)
                self.assertEqual(json.loads(rec["row_json"]).get("modality", ""), expected)

    def test_identity_fields_unchanged(self) -> None:
        # 제품군 칸 추가는 raw_signal_id·raw_sha256(dedup·지문)을 바꾸지 않는다.
        item = _item()
        rec = findings_store.raw_signal_from_intake_item(item)
        plain = dict(rec)
        self.assertTrue(plain["raw_signal_id"])
        self.assertEqual(rec["raw_sha256"], findings_store.raw_signal_from_intake_item(
            _item(headline="다른 헤드라인"))["raw_sha256"])


class BackfillPlanTest(unittest.TestCase):
    def _row(self, rid: str, item: ci.IntakeItem, modality: str | None = None) -> dict:
        rec = findings_store.raw_signal_from_intake_item(item)
        row_json = json.loads(rec["row_json"])
        row_json.pop("modality", None)          # 옛 행: row_json 에 modality 없음
        return {"raw_signal_id": rid, "source": item.source, "document_id": item.document_id,
                "modality": modality, "raw_json": rec["raw_json"], "row_json": json.dumps(row_json)}

    def test_backfill_matches_intake_computation(self) -> None:
        item = _item()
        row = self._row("r1", item)
        self.assertEqual(bm.modality_for_row(row), _notion_path_modality(item))

    def test_plan_skips_filled_and_unknown(self) -> None:
        bio = _item()
        unknown = _item(source="EMA", document_id="e1", headline="Meeting highlights",
                        type_or_class="news", firm="", raw_payload={})
        rows = [self._row("r1", bio), self._row("r2", bio, modality="Chemical"),
                self._row("r3", unknown), self._row("r4", bio, modality="")]
        result = bm.plan(rows)
        expected = _notion_path_modality(bio)
        self.assertTrue(expected)
        self.assertEqual(result["skipped_already_filled"], 1)
        self.assertEqual(result["_ids"], {expected: ["r1", "r4"]})
        self.assertEqual(result["unknown"], 1 if not _notion_path_modality(unknown) else 0)

    def test_apply_chunks_and_passes_value(self) -> None:
        calls: list[tuple[list[str], str]] = []
        result = {"_ids": {"Biologic": [f"r{i}" for i in range(250)], "Chemical": ["c1"]}}
        done = bm.apply_plan(result, base_url="https://x", service_key="k",
                             patcher=lambda _u, _k, ids, v: calls.append((ids, v)))
        self.assertEqual(done, {"Biologic": 250, "Chemical": 1})
        self.assertEqual([len(ids) for ids, v in calls if v == "Biologic"], [100, 100, 50])

    def test_broken_json_does_not_crash(self) -> None:
        self.assertIsInstance(bm.modality_for_row({"raw_json": "{", "row_json": None}), str)


if __name__ == "__main__":
    unittest.main()
