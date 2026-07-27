"""스캔 483 OCR 소급 복구 스크립트 회귀 — 대상 선별·복구 판정·산출물 계약.

무네트워크: `collect_fda_483._fetch_fda483_pdf_text` 스텁.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f483
import fda483_ocr_backfill as bf

_OBS_TEXT = (
    "OBSERVATION 1\nAseptic processing operations were deficient.\n"
    "Specifically, the operator blocked first air during filling.\n"
    "OBSERVATION 2\nEnvironmental monitoring was inadequate.\n"
    "Specifically, excursions were not investigated."
)


class FoldedIdsTest(unittest.TestCase):
    SCAFFOLD = {"cards": [
        {"id": "fda483-1"}, {"id": "fda483-2"}, {"id": "fda483-3"},
        {"id": "admin-9"}, {"id": "fda483-2"},          # 비483 · 중복
    ]}
    PUBLISHED = {"cards": [{"id": "fda483-1"}, {"id": "admin-9"}]}

    def test_picks_only_folded_483(self):
        self.assertEqual(bf.folded_483_ids(self.SCAFFOLD, self.PUBLISHED),
                         ["fda483-2", "fda483-3"])

    def test_order_is_scaffold_order(self):
        sc = {"cards": [{"id": "fda483-9"}, {"id": "fda483-1"}]}
        self.assertEqual(bf.folded_483_ids(sc, {"cards": []}),
                         ["fda483-9", "fda483-1"])

    def test_nothing_folded(self):
        self.assertEqual(bf.folded_483_ids(self.PUBLISHED, self.PUBLISHED), [])


class RecoverTest(unittest.TestCase):
    def test_recovered_document_enters_patch(self):
        with patch.object(f483, "_fetch_fda483_pdf_text",
                          lambda url: (_OBS_TEXT, "pdf-ok-ocr")):
            patch_out, report = bf.run(["fda483-100"], delay=0)
        self.assertIn("fda483-100", patch_out)
        self.assertEqual(sorted(patch_out["fda483-100"]),
                         ["source_text", "source_text_status"],
                         "패치는 원문과 그 출처 표기만 실어야 한다(관찰은 조립이 재추출)")
        self.assertEqual(patch_out["fda483-100"]["source_text"], _OBS_TEXT)
        self.assertEqual(patch_out["fda483-100"]["source_text_status"], "pdf-ok-ocr")
        self.assertEqual(report[0]["observation_count"], 2)
        self.assertEqual(report[0]["status"], "pdf-ok-ocr")

    def test_unrecovered_document_stays_out_of_patch(self):
        """관찰 0건이면 패치에 싣지 않는다 — 빈 블록은 '상세 있음'으로 오인된다."""
        with patch.object(f483, "_fetch_fda483_pdf_text",
                          lambda url: ("", "scan-ocr-empty")):
            patch_out, report = bf.run(["fda483-101"], delay=0)
        self.assertEqual(patch_out, {})
        self.assertEqual(report[0]["observation_count"], 0)
        self.assertEqual(report[0]["status"], "scan-ocr-empty")

    def test_report_excludes_source_text(self):
        """보고서는 사람이 읽는 감사 기록 — 전문을 중복 보관하지 않는다."""
        with patch.object(f483, "_fetch_fda483_pdf_text",
                          lambda url: (_OBS_TEXT, "pdf-ok-ocr")):
            _, report = bf.run(["fda483-100"], delay=0)
        self.assertNotIn("source_text", report[0])
        self.assertIn("observations", report[0])

    def test_one_failure_does_not_stop_the_batch(self):
        def _flaky(url):
            if "102" in url:
                return "", "fetch-fail:boom"
            return _OBS_TEXT, "pdf-ok-ocr"
        with patch.object(f483, "_fetch_fda483_pdf_text", _flaky):
            patch_out, report = bf.run(["fda483-101", "fda483-102", "fda483-103"],
                                       delay=0)
        self.assertEqual(len(report), 3)
        self.assertEqual(set(patch_out), {"fda483-101", "fda483-103"})

    def test_delay_is_honoured_between_documents(self):
        calls: list[float] = []
        with patch.object(f483, "_fetch_fda483_pdf_text",
                          lambda url: (_OBS_TEXT, "pdf-ok")):
            bf.run(["fda483-1", "fda483-2", "fda483-3"], delay=0.25,
                   sleeper=calls.append)
        self.assertEqual(calls, [0.25, 0.25], "첫 건 앞에는 대기하지 않는다")

    def test_native_text_is_not_labelled_ocr(self):
        """원문 텍스트층 산출은 OCR 표기를 달지 않는다(라벨 오염 방지)."""
        with patch.object(f483, "_fetch_fda483_pdf_text",
                          lambda url: (_OBS_TEXT, "pdf-ok")):
            patch_out, _ = bf.run(["fda483-104"], delay=0)
        self.assertEqual(patch_out["fda483-104"]["source_text_status"], "pdf-ok")

    def test_summary_counts(self):
        report = [{"status": "pdf-ok-ocr", "observation_count": 3},
                  {"status": "scan-ocr-unavailable:x", "observation_count": 0}]
        summary = bf._summarize(report)
        self.assertIn("대상 2건", summary)
        self.assertIn("복구 1건", summary)
        self.assertIn("관찰 총 3건", summary)


if __name__ == "__main__":
    unittest.main()


class DigestRepresentativeTest(unittest.TestCase):
    """[2026-07-27] 디제스트 **대표** 카드가 복구 대상에서 빠지던 구멍.

    디제스트는 대표 1장만 발행본에 남기고 나머지를 드롭한다. 그런데 그 대표 역시
    **관찰 원문이 없어서 접힌 카드**다. "발행본에 없는 것"만 고르면 대표가 빠지고,
    나머지 멤버가 전부 복구돼 디제스트가 해체되는 순간 대표만 낡은 슬롯
    ("구체적 관찰 사유: 원문 미기재")을 단 채 단독 카드로 발행된다 — 07-12 실측.
    """

    SCAFFOLD = {"cards": [{"id": "fda483-1"}, {"id": "fda483-2"}, {"id": "fda483-3"}]}
    PUBLISHED = {"cards": [
        {"id": "fda483-1", "merged_count": 3},      # 디제스트 대표(= 원문 없음)
        {"id": "fda483-2", "merged_count": 1},      # 상세 보유 — 대상 아님
    ]}

    def test_digest_representative_is_a_target(self):
        self.assertEqual(bf.folded_483_ids(self.SCAFFOLD, self.PUBLISHED),
                         ["fda483-1", "fda483-3"])

    def test_normal_card_still_excluded(self):
        self.assertNotIn("fda483-2", bf.folded_483_ids(self.SCAFFOLD, self.PUBLISHED))

    def test_missing_merged_count_treated_as_normal(self):
        published = {"cards": [{"id": "fda483-1"}, {"id": "fda483-2"}]}
        self.assertEqual(bf.folded_483_ids(self.SCAFFOLD, published), ["fda483-3"])
