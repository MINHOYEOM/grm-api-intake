import unittest

from collect_intake import (
    _fda_wl_office_gate,
    _is_low_value_fda_warning_letter,
    compute_relevance,
)
from collect_mfds_gmp_inspection import _is_medical_gas_gmp_noise


class FdaWarningLetterNoiseFilterTest(unittest.TestCase):
    def test_food_haccp_fsvp_warning_letters_are_low_value(self) -> None:
        low_value_examples = [
            (
                "Foreign Supplier Verification Program (FSVP)",
                "Seafood HACCP and FSVP violations",
                "Center for Food Safety and Applied Nutrition",
                "Example Seafood Importer",
            ),
            (
                "CGMP/Hazard Analysis/Risk-Based Preventive Controls for Food",
                "CGMP/Hazard Analysis/Risk-Based Preventive Controls for Food/Adulterated",
                "Human Foods Program",
                "Example Bakery",
            ),
            (
                "Dietary Supplement/New Drug/Misbranded",
                "Dietary Supplement/New Drug/Misbranded",
                "Human Foods Program",
                "Meta Labs Pharmaceuticals, LLC",
            ),
        ]
        for parts in low_value_examples:
            with self.subTest(parts=parts):
                self.assertTrue(_is_low_value_fda_warning_letter(*parts))

        self.assertEqual(
            compute_relevance("Seafood HACCP and FSVP violations"),
            "Unrelated",
        )
        self.assertEqual(
            compute_relevance("CGMP/Hazard Analysis/Risk-Based Preventive Controls for Food"),
            "Unrelated",
        )

    def test_pharma_cgmp_warning_letter_is_not_filtered(self) -> None:
        self.assertFalse(
            _is_low_value_fda_warning_letter(
                "CGMP violations",
                "Current Good Manufacturing Practice for finished pharmaceuticals",
                "Center for Drug Evaluation and Research",
                "Example Pharma",
            )
        )
        self.assertEqual(
            compute_relevance("cGMP Current Good Manufacturing Practice finished pharmaceuticals"),
            "Likely",
        )


class FdaWarningLetterOfficeGateTest(unittest.TestCase):
    """M0: 발행 부서(issuing_office) 1차 게이트 (redesign §7)."""

    def test_food_centers_are_excluded_unconditionally(self) -> None:
        # HFP/CFSAN — 식품 부서는 본문 맥락과 무관하게 제외.
        cases = [
            ("Human Foods Program", "CGMP/Hazard Analysis/Risk-Based Preventive "
                                    "Controls for Food", "Example Bakery"),
            ("Center for Food Safety and Applied Nutrition (CFSAN)",
             "Seafood HACCP and FSVP violations", "Example Seafood Importer"),
        ]
        for office, subject, firm in cases:
            with self.subTest(office=office):
                self.assertEqual(
                    _fda_wl_office_gate(office, subject, subject, firm), "exclude"
                )

    def test_veterinary_tobacco_device_centers_are_excluded(self) -> None:
        # CVM(수의)/CTP(담배)/CDRH(기기) — 무조건 제외 (약어·풀네임 모두).
        for office in (
            "CVM", "Center for Veterinary Medicine (CVM)",
            "CTP", "Center for Tobacco Products",
            "CDRH", "Center for Devices and Radiological Health (CDRH)",
        ):
            with self.subTest(office=office):
                self.assertEqual(_fda_wl_office_gate(office, "", "", ""), "exclude")

    def test_cder_finished_pharma_is_kept(self) -> None:
        # CDER + finished pharmaceuticals/cGMP → 유지.
        for office in ("CDER", "Center for Drug Evaluation and Research (CDER)"):
            with self.subTest(office=office):
                self.assertEqual(
                    _fda_wl_office_gate(
                        office,
                        "CGMP violations",
                        "Current Good Manufacturing Practice for finished "
                        "pharmaceuticals",
                        "Example Pharma",
                    ),
                    "keep",
                )

    def test_cber_biologics_aseptic_is_kept(self) -> None:
        # CBER + biologics/aseptic → 유지.
        for office in ("CBER", "Center for Biologics Evaluation and Research (CBER)"):
            with self.subTest(office=office):
                self.assertEqual(
                    _fda_wl_office_gate(
                        office,
                        "Sterility assurance / aseptic processing deficiencies",
                        "biologics aseptic processing",
                        "Example Biologics Inc.",
                    ),
                    "keep",
                )

    def test_oii_food_context_is_excluded(self) -> None:
        # OII(구 ORA) + 수산/HACCP 맥락 → 제외.
        self.assertEqual(
            _fda_wl_office_gate(
                "Office of Inspections and Investigations (OII)",
                "Seafood HACCP violations",
                "Seafood HACCP and FSVP violations",
                "Example Seafood Co.",
            ),
            "exclude",
        )

    def test_oii_food_cgmp_for_foods_is_excluded(self) -> None:
        # P1 회귀: 식품 WL 제목의 단독 "CGMP for Foods" 가 약품으로 오인돼 관통하던 갭.
        # 식품 단서가 있고 약품 '전용' 단서가 없으므로 제외돼야 한다(Codex: Stavis Seafoods).
        self.assertEqual(
            _fda_wl_office_gate(
                "Office of Inspections and Investigations (OII)",
                "Seafood HACCP/CGMP for Foods",
                "Seafood HACCP/CGMP for Foods/Adulterated",
                "Stavis Seafoods, Inc.",
            ),
            "exclude",
        )

    def test_oii_drug_context_is_kept(self) -> None:
        # OII + 약품 '전용' 단서(finished pharmaceuticals) → 유지(약품 WL 오삭제 방지).
        # 단독 cgmp 가 아니라 약품 전용 단서가 keep 을 결정해야 한다.
        self.assertEqual(
            _fda_wl_office_gate(
                "Office of Inspections and Investigations (OII)",
                "CGMP violations",
                "Current Good Manufacturing Practice for finished pharmaceuticals",
                "Example Pharma",
            ),
            "keep",
        )

    def test_oii_bare_cgmp_without_drug_only_clue_is_review(self) -> None:
        # P1: 단독 cgmp 는 더 이상 keep 단서가 아니다. 식품·약품전용 단서 모두 없으면
        # review(보수적 유지) — keep 으로 단정하지 않는다.
        self.assertEqual(
            _fda_wl_office_gate(
                "Office of Inspections and Investigations (OII)",
                "CGMP violations", "current good manufacturing practice", "Example Co."
            ),
            "review",
        )

    def test_oii_ambiguous_context_is_review(self) -> None:
        # OII 인데 식품·약품 단서 모두 없음 → review(비-드롭).
        self.assertEqual(
            _fda_wl_office_gate(
                "Office of Inspections and Investigations (OII)",
                "Unapproved misbranded product", "", "Example Co."
            ),
            "review",
        )

    def test_missing_office_falls_back_to_keyword_filter(self) -> None:
        # 부서 결측 → "unknown" (호출부 본문 키워드 폴백).
        self.assertEqual(_fda_wl_office_gate("", "FSVP violations", "", ""), "unknown")
        self.assertEqual(
            _fda_wl_office_gate("", "finished pharmaceuticals cGMP", "", ""), "unknown"
        )
        # 결측+FSVP → 본문 폴백으로 제외, 결측+finished/cGMP → 유지.
        self.assertTrue(_is_low_value_fda_warning_letter("FSVP violations"))
        self.assertFalse(
            _is_low_value_fda_warning_letter(
                "Current Good Manufacturing Practice for finished pharmaceuticals"
            )
        )


class MfdsGmpNoiseFilterTest(unittest.TestCase):
    def test_medical_gas_companies_are_low_value_for_osd_digest(self) -> None:
        for manufacturer in ("밀성산업가스", "에어퍼스트", "한국수소"):
            with self.subTest(manufacturer=manufacturer):
                self.assertTrue(
                    _is_medical_gas_gmp_noise(
                        {
                            "manufacturer": manufacturer,
                            "address": "충청북도",
                            "product_type": "완제",
                        }
                    )
                )

    def test_non_gas_pharma_manufacturer_is_not_filtered(self) -> None:
        for manufacturer in (
            "Bora Pharmaceutical Services Inc.",
            # 회귀: "linde" substring 오탐 방지 (단어 경계 매칭)
            "Lindenberg Pharma GmbH",
            # 회귀: 단독 "수소" substring 오탐 방지
            "수소문제약(주)",
        ):
            with self.subTest(manufacturer=manufacturer):
                self.assertFalse(
                    _is_medical_gas_gmp_noise(
                        {
                            "manufacturer": manufacturer,
                            "address": "Canada",
                            "product_type": "완제",
                        }
                    )
                )

    def test_english_gas_brands_match_on_word_boundary(self) -> None:
        for manufacturer in ("Linde Korea Co., Ltd.", "Praxair Inc.", "Air Products Korea"):
            with self.subTest(manufacturer=manufacturer):
                self.assertTrue(
                    _is_medical_gas_gmp_noise(
                        {
                            "manufacturer": manufacturer,
                            "address": "Korea",
                            "product_type": "완제",
                        }
                    )
                )


if __name__ == "__main__":
    unittest.main()
