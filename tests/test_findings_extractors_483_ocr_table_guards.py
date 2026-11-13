#!/usr/bin/env python3
"""[483 추출 가드 2026-11-13] 주석란 정규식 OCR 변형 흡수 · 재표제화 표 가드.

픽스처는 운영 raw_signals.raw_json.fda_483_observations 실측 조각을 줄인 것(문서 id 실측 그대로).
근거: _ops/reports/GRM_483번역보류7행_상류수리설계_2026-11-13.md §1 (77380 eb27f823 · 163754 40e4d18d).
"""

from __future__ import annotations

import unittest

import findings_extractors as extractors
import grm_findings as gf


def _signal(document_id: str, observations: list[dict]) -> dict:
    row = {
        "date": "2026-05-27",
        "document_id": document_id,
        "firm": "Example Firm",
        "headline": "[FDA 483] Example Firm",
        "language": "EN",
        "modality": "Chemical",
        "official_url": "https://www.fda.gov/media/000000/download",
        "source": "FDA 483",
        "type_or_class": "483",
    }
    raw = {"channel": "fda-483", "firm": "Example Firm", "fda_483_observations": observations}
    return gf.raw_signal_from_row(row, raw)


def _obs(number: str, deficiency: str, detail: str = "") -> dict:
    return {"number": number, "deficiency": deficiency, "detail": detail}


def _texts(findings: list[dict]) -> list[str]:
    return [f["finding_text"] for f in findings]


_REAL = "Procedures describing the warehousing of drug products are not established and followed."

# fda483-77380 — 483 뒷면 주석란 체크란. 정확일치는 "Promised to correct." 만 잡고 OCR 변형을 놓쳤다.
_77380 = [
    _obs("1", _REAL, "Specifically, the firm failed to follow the section."),
    _obs("4", "Promised to correct.", "Obsenration 5: Promised to correct."),
    _obs("6", "Repolted corrected not verified.", "Obsenration 7: Promised to correct."),
]

# fda483-163754 — 표제 "." 둘, 두 번째 상세 첫 문장이 안정성 표를 지나 `APL C.` 에서야 끝난다.
_163754 = [
    _obs("2", ".", "CAPA states that all batches were verified and found within specification however the statement is inaccurate."),
    _obs("2", ".",
         "Both batches were not manufactured under Month/Stability Condition Initial Release % % 1 M "
         "Accelerated % Not detected 2 M Accelerated % Not detected 3 M Accelerated % % 3M Long Term % % "
         "6 M Accelerated % (OOS U09-001587) % 6 M Long Term % (OOS U09-001588) % 9 M Long Term "
         "Discontinued Stability % 12 M Long Term % Approximately batches (Crude) Stage Intermediate "
         "batches were manufactured using reprocessed input material and shipped to Unit 1 for "
         "purification step of APL C. Next sentence."),
]


class AnnotationOcrVariantTests(unittest.TestCase):
    def test_ocr_variants_are_annotations(self) -> None:
        for title in (
            "Repolted corrected not verified.",
            "Reported corrected not verified.",
            "Reportedcorrected not verified.",
            "Promised to correct.",
            "Promlsed to correct within 24 weeks.",
            "Not annotated *DATES OF INSPECTION",
            "Notannotated",
            "Corrected not verified.",
        ):
            with self.subTest(title=title):
                self.assertTrue(extractors._fda483_is_annotation(title))

    def test_prose_titles_are_not_annotations(self) -> None:
        for title in (
            _REAL,
            "Reports of complaints were not corrected in a timely manner.",
            "Procedures were not promised to be corrected by management.",
            "Not all annotations were verified by the quality unit.",
            "Corrected data were not verified before release.",
        ):
            with self.subTest(title=title):
                self.assertFalse(extractors._fda483_is_annotation(title))

    def test_77380_rows_drop_both_annotation_rows(self) -> None:
        findings = extractors.findings_from_raw_signal(_signal("fda483-77380", _77380))
        self.assertEqual(_texts(findings), [_REAL])


class RetitleTableGuardTests(unittest.TestCase):
    def test_table_first_sentence_is_not_used_as_title(self) -> None:
        detail = _163754[1]["detail"]
        self.assertIsNone(extractors._fda483_title_from_detail(detail))

    def test_table_detector_signals(self) -> None:
        self.assertTrue(extractors._fda483_looks_like_table("A % % B"))
        self.assertTrue(extractors._fda483_looks_like_table("results at 3 M Accelerated were stable"))
        self.assertTrue(extractors._fda483_looks_like_table("1 2 3 4 5 6 7 8 9 10 abc def"))
        self.assertFalse(extractors._fda483_looks_like_table(_REAL))
        self.assertFalse(extractors._fda483_looks_like_table("Recovery was 98% at 25 C for 12 months of testing."))

    def test_163754_table_row_keeps_fragment_title_but_normal_retitle_works(self) -> None:
        findings = extractors.findings_from_raw_signal(_signal("fda483-163754", _163754))
        texts = _texts(findings)
        self.assertTrue(any(t.startswith("CAPA states") for t in texts), texts)
        self.assertFalse(any("Month/Stability" in t for t in texts), texts)
        self.assertFalse(any("% %" in t for t in texts), texts)

    def test_ordinary_retitle_still_works(self) -> None:
        got = extractors._fda483_title_from_detail(
            ". Records are not maintained so that data can be reviewed. Specifically, the firm has none."
        )
        self.assertIsNotNone(got)
        self.assertTrue(got[0].startswith("Records are not maintained"))


if __name__ == "__main__":
    unittest.main()
