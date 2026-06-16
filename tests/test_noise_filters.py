import os
import unittest
from unittest import mock

from collect_intake import (
    SOURCE_FR,
    _fda_wl_office_gate,
    _is_low_value_fda_warning_letter,
    compute_relevance,
    compute_signal_tier,
)
import collect_mfds_admin_action as adm
from collect_mfds_admin_action import _is_collectable as _admin_is_collectable
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


class FederalRegisterDeviceNoiseFilterTest(unittest.TestCase):
    """C-2 G4: FR 의료기기 분류 Rule("Medical Devices; Orthopedic Devices;…")이
    Intake 에 카드로 유입되던 갭. 복수형 단서로 Unrelated → Tier 1 고정해 차단.
    단, 약물전달기기·combination product 정당 항목은 오배제하지 않는다."""

    # 실제 누수 3건 raw (2026-11308 · 11306 · 11302) — 순수 기기 분류 Rule.
    REAL_DEVICE_RULES = [
        (
            "Medical Devices; Orthopedic Devices; Classification of the Resorbable "
            "Calcium Salt Bone Void Filler Containing a Single Approved "
            "Aminoglycoside Antibacterial",
            "The Food and Drug Administration (FDA) is classifying the resorbable "
            "calcium salt bone void filler containing a single approved aminoglycoside "
            "antibacterial into class II (special controls). The special controls that "
            "apply to the product type are identified in this order and will be part of "
            "the codified language for classification of the resorbable calcium salt "
            "bone void filler containing a single approved aminoglycoside antibacterial.",
        ),
        (
            "Medical Devices; Orthopedic Devices; Classification of the Shoulder Joint "
            "Humeral (Hemi-Shoulder) Ceramic Head/Metallic Stem Cemented or "
            "Uncemented Prosthesis",
            "The Food and Drug Administration (FDA) is classifying the shoulder joint "
            "humeral (hemi-shoulder) ceramic head/metallic stem cemented or uncemented "
            "prosthesis into class II (special controls). The special controls that "
            "apply to the device type are identified in this order.",
        ),
        (
            "Medical Devices; Orthopedic Devices; Classification of the Absorbable "
            "Metallic Bone Fixation Fastener",
            "The Food and Drug Administration (FDA) is classifying the absorbable "
            "metallic bone fixation fastener into class II (special controls). The "
            "special controls that apply to the device type are identified in this order.",
        ),
    ]

    def test_real_device_classification_rules_are_unrelated_tier1(self) -> None:
        # compute_relevance=Unrelated → compute_signal_tier 가 Tier 1 로 고정(카드 미렌더).
        for title, abstract in self.REAL_DEVICE_RULES:
            with self.subTest(title=title[:48]):
                rel = compute_relevance(title, abstract, "Rule")
                self.assertEqual(rel, "Unrelated")
                tier = compute_signal_tier(
                    SOURCE_FR, "Rule", rel, "N/A", title, abstract, "Rule"
                )
                self.assertEqual(tier, "Tier 1")

    def test_plural_device_terms_match_on_word_boundary(self) -> None:
        # 단수 단서는 복수형 FR 제목에 안 걸렸던 누수 경로 — 복수형도 Unrelated.
        self.assertEqual(
            compute_relevance("Medical Devices; Orthopedic Devices; Classification"),
            "Unrelated",
        )

    def test_drug_delivery_combination_device_is_not_over_excluded(self) -> None:
        # 오배제 0: 기기 단서가 있어도 약물/복합제 단서가 함께면 Unrelated 가 아니어야 한다
        # (약물전달기기·combination product 정당 포함).
        combo_cases = [
            (
                "Medical Devices; Cardiovascular Devices; Classification of the "
                "Drug-Eluting Coronary Stent",
                "The drug-eluting coronary stent is a combination product containing "
                "a drug constituent intended to reduce restenosis.",
            ),
            (
                "Medical Devices; General Hospital Devices; Drug-Device Combination "
                "Infusion Pump",
                "This combination product delivers a biologic drug product via an "
                "implanted device constituent.",
            ),
        ]
        for title, abstract in combo_cases:
            with self.subTest(title=title[:48]):
                self.assertNotEqual(
                    compute_relevance(title, abstract, "Rule"), "Unrelated"
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


class MfdsGmpGasOverExclusionTest(unittest.TestCase):
    """A6: 한글 '가스' 부분문자열 과배제 차단(토큰경계 매칭).

    바 '가스'가 '메가스터디제약'(메[가스]터디)·'한국가스공사 자회사 제약'(가스[공사])을
    의료가스 노이즈로 오배제하던 결함. '가스' 뒤에 한글이 없을 때만 가스 제조사로 본다.
    """

    def test_megastudy_pharma_is_not_gas_noise(self) -> None:
        # '메가스터디제약' — '가스' 뒤에 한글('터')이 이어짐 → 가스 제조사 아님(수집 유지).
        self.assertFalse(
            _is_medical_gas_gmp_noise(
                {"manufacturer": "메가스터디제약", "address": "서울특별시",
                 "product_type": "완제"}
            )
        )

    def test_gas_corp_subsidiary_pharma_is_not_gas_noise(self) -> None:
        # '한국가스공사 자회사 제약' — '가스' 뒤 '공' → 어중 토큰 → 과배제 금지(수집 유지).
        self.assertFalse(
            _is_medical_gas_gmp_noise(
                {"manufacturer": "한국가스공사 자회사 제약", "address": "경기도",
                 "product_type": "완제"}
            )
        )

    def test_industrial_gas_suffix_company_still_noise(self) -> None:
        # 회귀: '○○산업가스'(가스 접미사 토큰)는 여전히 의료가스 노이즈로 제외.
        for manufacturer in ("대성산업가스", "밀성산업가스", "○○가스(주)"):
            with self.subTest(manufacturer=manufacturer):
                self.assertTrue(
                    _is_medical_gas_gmp_noise(
                        {"manufacturer": manufacturer, "address": "충청북도",
                         "product_type": "완제"}
                    )
                )

    def test_real_gas_via_context_term_still_noise(self) -> None:
        # 회귀: 가스 접미사 없는 사명이라도 product_type 맥락어(의료용 고압가스)면 제외 유지.
        self.assertTrue(
            _is_medical_gas_gmp_noise(
                {"manufacturer": "동방메디칼", "address": "경기도",
                 "product_type": "의료용 고압가스"}
            )
        )


class MfdsAdminActionPurifiedWaterTest(unittest.TestCase):
    """A5: 행정처분 collectability 게이트의 '정제수'→'정제' 오탐 차단.

    바 '정제'(tablet) 가 '정제수'(purified water·비의약품)를 부분매칭해 비의약품
    행정처분이 false positive 로 Intake 에 유입되던 누수. compute_modality 의
    haystack.replace("정제수","") 가드를 collectability 게이트에도 전파했다.
    """

    def test_purified_water_nonpharma_is_not_collectable(self) -> None:
        # 비의약품(손소독제) + 화장품 등 명시 저가치어 없음 + Tier3 처분어 없음(판매업무정지는
        # ADMIN_TIER3_TERMS 미포함) → collectability 가 오직 pharma rescue 에 달림. '정제수'가
        # 유일한 '정제' 부분문자열 출처가 되도록 구성('세정제'는 자체에 '정제' 포함 → 회피)해
        # 정제수 제거 경로만 격리 검증한다. 가드 없으면 '정제' 오매칭으로 수집(누수).
        raw = {
            "ITEM_NAME": "정제수 기반 손소독제",
            "EXPOSE_CONT": "정제수를 원료로 한 위생용품 표시 위반",
            "ADM_DISPS_NAME": "판매업무정지",
        }
        self.assertFalse(_admin_is_collectable(raw))

    def test_real_tablet_drug_admin_action_is_still_collectable(self) -> None:
        # 회귀: 실제 '정제'(tablet) 의약품은 여전히 수집 유지. 처분어가 Tier3 가 아니어도
        # pharma rescue('정제')만으로 collectable 이어야 한다(정제수 제거가 진짜 '정제'
        # 단서를 깨면 안 됨 — '정제수' 부분문자열만 제거).
        raw = {
            "ITEM_NAME": "세파클러정제",
            "EXPOSE_CONT": "표시기재 위반",
            "ADM_DISPS_NAME": "판매업무정지",
        }
        self.assertTrue(_admin_is_collectable(raw))


class MfdsAdminUrlVerifyTest(unittest.TestCase):
    """E2 — ENABLE_MFDS_URL_VERIFY(기본 off) 행정처분 L1 resolve&verify.

    off=수집기 동작 불변(admin_l1_verify 키 미기록). on=verify_url_live 로 후보 L1 을
    검증해 pass/fail 을 raw 에 남긴다(scaffold 가 L1 승격/강등). collect 행위 불변 보호.
    """

    _RAW = {
        "ENTP_NAME": "대한약품", "ITEM_NAME": "정제X",
        "LAST_SETTLE_DATE": "20260601", "ADM_DISPS_NAME": "제조업무정지",
        "ADM_DISPS_SEQ": "2026004188", "EXPOSE_CONT": "품질부적합",
    }

    def test_flag_off_records_nothing(self):
        # 기본(env 미설정) — 후보 검증 안 함, admin_l1_verify 키 미기록(현행 동작).
        env = {k: v for k, v in os.environ.items() if k != "ENABLE_MFDS_URL_VERIFY"}
        with mock.patch.dict(os.environ, env, clear=True):
            item = adm._to_item(dict(self._RAW), "q")
        self.assertIsNotNone(item)
        self.assertNotIn("admin_l1_verify", item.raw_payload)

    def test_flag_on_pass_promotes(self):
        with mock.patch.dict(os.environ, {"ENABLE_MFDS_URL_VERIFY": "true"}), \
             mock.patch("brief_lint.verify_url_live", return_value={"ok": True}) as m:
            item = adm._to_item(dict(self._RAW), "q")
        self.assertEqual(item.raw_payload["admin_l1_verify"], "pass")
        self.assertEqual(
            item.raw_payload["admin_l1_candidate_url"],
            "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026004188")
        # 후보 L1 URL 로 검증을 호출했는지 확인.
        self.assertIn("dispsApplySeq=2026004188", m.call_args[0][0])

    def test_flag_on_fail_demotes(self):
        with mock.patch.dict(os.environ, {"ENABLE_MFDS_URL_VERIFY": "1"}), \
             mock.patch("brief_lint.verify_url_live", return_value={"ok": False}):
            item = adm._to_item(dict(self._RAW), "q")
        self.assertEqual(item.raw_payload["admin_l1_verify"], "fail")

    def test_flag_on_verify_exception_demotes(self):
        # verify 자체가 터지면(예외) 미검증=강등(fail) — 차단 측 안전.
        with mock.patch.dict(os.environ, {"ENABLE_MFDS_URL_VERIFY": "on"}), \
             mock.patch("brief_lint.verify_url_live", side_effect=RuntimeError("boom")):
            item = adm._to_item(dict(self._RAW), "q")
        self.assertEqual(item.raw_payload["admin_l1_verify"], "fail")


if __name__ == "__main__":
    unittest.main()
