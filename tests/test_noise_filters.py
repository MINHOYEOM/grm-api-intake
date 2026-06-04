import unittest

from collect_intake import (
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
        self.assertFalse(
            _is_medical_gas_gmp_noise(
                {
                    "manufacturer": "Bora Pharmaceutical Services Inc.",
                    "address": "Canada",
                    "product_type": "완제",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
