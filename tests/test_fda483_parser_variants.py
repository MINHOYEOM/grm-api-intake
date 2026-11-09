"""483 관찰 파서가 놓치던 세 가지 양식 (2026-11-07 실측, 11-02 호 묶음 카드 잔여분).

세 문서 모두 원문에 관찰이 분명히 있는데 파서가 0건을 냈다. 고친 곳은 전부 **정상 경로가
0건일 때만 도는 회수 경로**이고, 정상 경로의 앵커 선택(`_select_observation_anchors`)은
그대로다.

- fda483-195577 Baxter — `OBSERVATION I.` 다음이 **줄바꿈**. 줄머리 마침표 제거가 같은 줄
  공백만 허용해 표제가 "." 로 잘려 버려졌다.
- fda483-195404 MSD — 번호 없는 단일 관찰인데 표제의 제품 코드 `V116` 을 OCR 깨짐 토큰
  (영문+숫자)으로 세어 회수 게이트가 기각했다.
- fda483-195467 Sanofi Pasteur Limited — 번호 없는 단일 관찰 앞에 시스템 절 제목
  `DRUG SUBSTANCE (COMPONENT)` 이 붙고, 표제("allows for leaking/spilling")가 결함 어휘에 없었다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402

_PREAMBLE = (
    "FORM FDA 483 (09/08) PREVIOUS EDITION OBSOLETE INSPECTIONAL OBSERVATIONS\n"
    "This document lists observations made by the FDA representative(s) during the "
    "inspection of your facility. They are inspectional observations, and do not represent "
    "a final Agency determination regarding your compliance.\n"
)

BAXTER = _PREAMBLE + (
    "DURING AN INSPECTION OF YOUR FIRM (|) (WE) OBSERVED:\n"
    "OBSERVATION I.\n"
    "Established procedures for manufacturing were not followed. Specifically,\n"
    "The (b) (4)\nassembled with the (b) (4)\nwas overdue for calibration, and\n"
    "instead of restricting the use of the instrument as prescribed in SOP 01-01-043-7, "
    "the instrument was released and used on July 22, 2024.\n"
)

MSD = _PREAMBLE + (
    "DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
    "You did not adequately validate your to support your V116 (Capvaxive) monovalent bulk "
    "conjugate (MBC) drug substance manufacturing operations. Since validating and "
    "implementing your system on 07DEC2023, multiple deviations have been initiated "
    "pertaining to design limitations resulting in potential impact to product quality.\n"
)

SANOFI = _PREAMBLE + (
    "DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
    "DRUG SUBSTANCE (COMPONENT)\n"
    "The design of your firm's bioburden sample collection process allows for "
    "leaking/spilling of (b) (4) during collection. On 11 MAR 2025, two operators were "
    "observed taking bioburden samples of (b) (4) as it flows through (b) (4).\n"
)


class ParserVariantRecoveryTest(unittest.TestCase):

    def test_glyph_heading_followed_by_newline(self):
        rows = f._extract_483_observations_from_text(BAXTER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], "1")
        self.assertEqual(rows[0]["deficiency"],
                         "Established procedures for manufacturing were not followed.")
        self.assertIn("overdue for calibration", rows[0]["detail"])

    def test_product_code_in_unnumbered_heading_is_not_garble(self):
        rows = f._extract_483_observations_from_text(MSD)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["deficiency"].startswith("You did not adequately validate"))
        self.assertIn("V116", rows[0]["deficiency"])

    def test_leading_section_heading_is_stripped(self):
        rows = f._extract_483_observations_from_text(SANOFI)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["deficiency"].startswith("The design of your firm"))
        self.assertNotIn("DRUG SUBSTANCE", rows[0]["deficiency"])


class GuardsStillHoldTest(unittest.TestCase):
    """완화한 세 곳이 원래 막던 것은 여전히 막는다."""

    def test_lowercase_digit_ocr_tokens_still_count_as_garble(self):
        symbols, _ratio = f._deficiency_garble(
            "The firm fai1ed to va1idate th1s process for the batch.")
        self.assertGreater(symbols, 0)

    def test_product_code_pattern_is_narrow(self):
        for code in ("V116", "PR1257198", "QN201223715", "SOP-1161"):
            self.assertIsNotNone(f._PRODUCT_CODE_RE.match(code), code)
        for garble in ("th1s", "fai1ed", "l0t", "0f", "V1a6"):
            self.assertIsNone(f._PRODUCT_CODE_RE.match(garble), garble)

    def test_unnumbered_product_description_is_still_rejected(self):
        text = _PREAMBLE + (
            "DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
            "STERILE INJECTABLES\n"
            "Sterile, injectable liquid dosage form preparation for intravenous administration "
            "in single dose vials.\n"
        )
        self.assertEqual(f._extract_483_observations_from_text(text), [])

    def test_section_heading_strip_needs_a_following_sentence(self):
        self.assertEqual(
            f._LEADING_SECTION_HEADING_RE.sub("", "QUALITY SYSTEM The firm did not", count=1),
            "The firm did not")
        # 문장 없이 대문자만 이어지면 떼지 않는다.
        self.assertEqual(
            f._LEADING_SECTION_HEADING_RE.sub("", "QUALITY SYSTEM REVIEW", count=1),
            "QUALITY SYSTEM REVIEW")

    def test_normal_path_anchor_selection_unchanged(self):
        """정상 경로 앵커 선택은 줄바꿈 마침표 꼴을 여전히 상호참조로 본다(구조 불변)."""
        self.assertIsNone(f._LINE_HEAD_DOT_RE.match(".\nThere is a failure"))
        self.assertIsNotNone(f._LINE_HEAD_DOT_SEG_RE.match(".\nThere is a failure"))



class EggFarmEstablishmentScopeTest(unittest.TestCase):
    """계란 안전 규정(21 CFR 118) 483 은 식품 시설이다 — findings 는 이미 non_pharma 로 비공개
    (실측 80문서·291건)인데, 수집기·브리프의 시설 유형 판정만 범위 안으로 두고 있었다."""

    def test_egg_and_farm_types_are_out_of_scope(self):
        from grm_taxonomy import is_out_of_scope_483_establishment as oos
        for est in ("Shell Egg Producer", "Farm", "Egg Farm", "Poultry Processor",
                    "Hatchery", "Feed Mill"):
            self.assertTrue(oos(est), est)

    def test_pharma_types_unaffected(self):
        from grm_taxonomy import is_out_of_scope_483_establishment as oos
        for est in ("Manufacturer", "Pharmaceutical Manufacturer", "Compounding Pharmacy",
                    "Outsourcing Facility", "Producer of Sterile Drug Products",
                    "Drug Manufacturer", "Nonclinical Laboratory"):
            self.assertFalse(oos(est), est)


if __name__ == "__main__":
    unittest.main()
