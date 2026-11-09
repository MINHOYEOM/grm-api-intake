"""11-09 호 기기 483 4장 누락 — 강한 기기 표지가 약한 의약품 어휘(stability·sterility·OOS)에 지던 결함.

11-09 주간 브리프(라이브)의 FDA 483 17장 중 기기 시설 4장이 범위 밖 판정을 통과했다. 시설 유형이
그냥 "Manufacturer"/"Contract Sterilizer" 라 본문으로 갈라야 하는데:
- Cardionxt(196231)·Immuno-Mycologics(196241)·American Contract Systems(196243): 기기 어휘는
  잡혔지만 의약품 거부(veto) 어휘 `stability`·`sterility`·`out-of-specification` 하나에 뒤집혔다
  ("software stability study", 진단시약 "reduced stability", EO 멸균 "sterility test").
- Radiation Products Design(196242): 2026-02 QMSR 시행 뒤 483 문구(ISO 13485 7.1 "risk
  management in product realization")가 기기 어휘에 없었다.

고정값은 실제 본문(`web/data/deltas/deep_2026_11_09.json`)의 핵심 구절 축약이다. 대조군은 같은 호의
의약품 483 과 복합제품 가정 문장 — 의약품 오제외 0 이 절대 조건이다.

뮤테이션: `MutationTest` 가 수정 전 판정(강한 표지 무시·옛 기기 어휘)을 재현해 고정값이 그때는
통과(=누락)했음을 확인한다. 수정을 되돌리면 `DeviceExcludedTest` 가 실패한다.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grm_taxonomy as t  # noqa: E402

# ── 11-09 기기 483 본문 핵심 구절(축약) ─────────────────────────────────────────
CARDIONXT_196231 = (
    "Your firm did not follow the procedures when evaluating complaint numbers 0016, 0017, and 0018, "
    "where a formal investigation was not conducted to determine the root cause of a software reset. "
    "Furthermore, your firm conducted a software stability study, QA-0008, iMap Software Stability "
    "Analysis, Rev A. The information required by the Global Unique Device Identification Database for "
    "each medical device version or model required to bear a unique device identifier (UDI) was not "
    "provided.")
IMMUNO_MYCOLOGICS_196241 = (
    "Records of risk management activities in product realization were not adequately maintained. "
    "The Risk Management File for the Class II Coccidioides Ab Enzyme Immunoassay ... reduced "
    "sensitivity, reduced specificity, and reduced stability. Design and development changes were not "
    "adequately reviewed, verified, validated.")
RADIATION_PRODUCTS_196242 = (
    "One or more processes for risk management in product realization have not been adequately "
    "documented. For example, I reviewed records pertaining to your tungsten eye shields. You do not "
    "have a risk assessment or other record to formally document risks.")
AMERICAN_CONTRACT_SYSTEMS_196243 = (
    "This includes any defect or deficiency involving the tray wrap, sterilization bag, EO gas delivery, "
    "bag sealing process, or aeration or sterility test conditions or results. The latter includes "
    "out-of-specification temperature conditions as shown on the recorder chart. A consulting firm was "
    "contracted to submit electronic Medical Device Reports to the FDA. Kits contained Powdered Surgical "
    "Gloves, a banned medical device, and a report was not submitted in writing to the FDA.")

# ── 대표 판단 대기(이 PR 은 제외하지 않는다) ───────────────────────────────────
ROGERS_FOAM_196239 = (
    "Processes for production and service provision where the resulting output cannot be, or is not, "
    "verified by subsequent monitoring or measurement were not adequately validated. Additionally, an "
    "initial validation of the foam cutting process has not been performed. Procedures for validation "
    "of processes did not adequately include statistical techniques with rationale for sample sizes.")

# ── 의약품 대조군(남아야 한다) ──────────────────────────────────────────────────
NOVO_196250 = (
    "Not all method validation activities were documented in source records. For instance, stability "
    "of the analyte in whole blood was evaluated after 1-hour incubation. SOP Q053698 states that only "
    "failed stability QCs should be included in subsequent confirmatory runs.")
ASCENDIS_PADE_196246 = (
    "Not all adverse drug experiences that are both serious and unexpected have been reported to FDA "
    "within 15 calendar days of initial receipt of the information.")
API_196220 = (
    "Stability studies do not support the labeled retest date. Your firm does not perform microbial "
    "testing during stability studies ... 4 batches of API were released for the US market.")
# 복합제품(프리필드 시린지) 가정 — 강한 기기 표지가 있어도 강한 의약품 어휘가 있으면 남긴다.
COMBINATION_PRODUCT = (
    "The labels of the medical device(s) do not bear a unique device identifier (UDI). The prefilled "
    "syringe drug product lots were released without complete testing.")
ASEPTIC_WITH_MDR = (
    "Medical Device Reports were not submitted. Aseptic processing interventions were not "
    "documented during media fill.")

DEVICE_CASES = [
    ("fda483-196231 Cardionxt", "Manufacturer", CARDIONXT_196231),
    ("fda483-196241 Immuno-Mycologics", "Manufacturer", IMMUNO_MYCOLOGICS_196241),
    ("fda483-196242 Radiation Products Design", "Manufacturer", RADIATION_PRODUCTS_196242),
    ("fda483-196243 American Contract Systems", "Contract Sterilizer", AMERICAN_CONTRACT_SYSTEMS_196243),
]
DRUG_CONTROLS = [
    ("fda483-196250 Novo (유형)", "Biopharmaceutical Manufacturer", NOVO_196250),
    ("fda483-196250 Novo (본문만)", "Manufacturer", NOVO_196250),
    ("fda483-196246 Ascendis PADE", "PADE", ASCENDIS_PADE_196246),
    ("fda483-196220 API", "Manufacturer", API_196220),
    ("복합제품 UDI+drug product", "Manufacturer", COMBINATION_PRODUCT),
    ("MDR+aseptic/media fill", "Manufacturer", ASEPTIC_WITH_MDR),
]


class DeviceExcludedTest(unittest.TestCase):
    def test_device_483s_are_out_of_scope(self):
        for name, est, body in DEVICE_CASES:
            with self.subTest(name):
                self.assertTrue(t.is_out_of_scope_483(est, body))

    def test_strong_markers_each_suffice(self):
        for phrase in ("unique device identifier", "UDI", "Medical Device Reports",
                       "a banned medical device", "risk management in product realization",
                       "21 CFR 803.50", "21 CFR 806.10", "21 CFR 807.85", "21 CFR 830.300"):
            with self.subTest(phrase):
                # 약한 의약품 어휘가 같이 있어도 기기로 본다.
                self.assertTrue(t.is_out_of_scope_483(
                    "Manufacturer", f"{phrase}. The stability and sterility test results were OOS."))

    def test_udi_is_word_bounded(self):
        # "audits" 안의 'udi' 에 걸리면 안 된다(483 서식 문구 "internal self-audits").
        self.assertFalse(t.is_out_of_scope_483(
            "Manufacturer", "your firm is responsible for conducting internal self-audits"))


class DrugKeptTest(unittest.TestCase):
    def test_drug_controls_stay_in_scope(self):
        for name, est, body in DRUG_CONTROLS:
            with self.subTest(name):
                self.assertFalse(t.is_out_of_scope_483(est, body))

    def test_pharma_establishment_wins_over_device_text(self):
        self.assertFalse(t.is_out_of_scope_483("Drug Manufacturer", CARDIONXT_196231))

    def test_rogers_foam_left_for_owner_decision(self):
        # ISO 13485 7.5.6 문구라 기기 쪽 근거가 있지만, 대표 판단 전에는 제외하지 않는다(PR 본문).
        self.assertFalse(t.is_out_of_scope_483("Manufacturer", ROGERS_FOAM_196239))


class MutationTest(unittest.TestCase):
    """수정을 되돌린 판정에서는 고정값이 누락(False)된다 — 고정값이 이 수정을 실제로 지킨다."""

    _OLD_DEVICE_RE = re.compile(
        r"\b820\.\d+|design\s+(?:controls?|validation|verification|history)"
        r"|device\s+(?:master|history)\s+record|\bmedical\s+devices?\b|\bmdrs?\b"
        r"|quality\s+system\s+regulation|unique\s+device\s+identif"
        r"|design\s+(?:and\s+development|changes?)\b", re.I)
    _NEVER = re.compile(r"(?!x)x")

    def test_reverted_rule_misses_all_four(self):
        with mock.patch.object(t, "_FDA483_DEVICE_STRONG_RE", self._NEVER), \
                mock.patch.object(t, "_FDA483_DEVICE_TEXT_RE", self._OLD_DEVICE_RE):
            for name, est, body in DEVICE_CASES:
                with self.subTest(name):
                    self.assertFalse(t.is_out_of_scope_483(est, body))

    def test_strong_override_alone_is_load_bearing(self):
        # 기기 어휘 보강만 남기고 '강한 표지 우선'을 빼면 veto 에 지던 3장이 다시 남는다.
        with mock.patch.object(t, "_FDA483_DEVICE_STRONG_RE", self._NEVER):
            for name, est, body in DEVICE_CASES:
                if "196242" in name:
                    continue
                with self.subTest(name):
                    self.assertFalse(t.is_out_of_scope_483(est, body))

    def test_strong_drug_veto_is_load_bearing(self):
        # 강한 의약품 veto 를 빼면 복합제품 대조군이 오제외된다.
        with mock.patch.object(t, "_FDA483_DRUG_STRONG_TEXT_RE", self._NEVER):
            self.assertTrue(t.is_out_of_scope_483("Manufacturer", COMBINATION_PRODUCT))


def _card483(cid, est, body):
    return {"id": cid, "type_tag": "483", "render_order": 0,
            "facts": [{"label": "시설 · 유형", "value": f"{est} · 483"}],
            "deterministic_detail": {"observations": [{"deficiency": body, "detail": ""}]}}


class RegressionGuardOutOfScopeTest(unittest.TestCase):
    """이미 발행된 기기 483 을 재조립에서 빼도 가드가 '소실'로 막지 않는다. 의약품은 여전히 막는다."""

    def setUp(self):
        import assemble_regression_guard as g
        self.g = g

    def test_device_card_removal_passes(self):
        pub = {"cards": [_card483("fda483-196231", "Manufacturer", CARDIONXT_196231),
                         _card483("fda483-196220", "Manufacturer", API_196220)]}
        asm = {"cards": [_card483("fda483-196220", "Manufacturer", API_196220)]}
        r = self.g.compare(pub, asm)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["out_of_scope_removed"], ["fda483-196231"])
        self.assertIn("범위 밖 483 제외 1장", self.g.render_markdown(r, "x.json"))

    def test_drug_card_removal_still_blocked(self):
        pub = {"cards": [_card483("fda483-196220", "Manufacturer", API_196220)]}
        r = self.g.compare(pub, {"cards": []})
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_cards"], ["fda483-196220"])
        self.assertEqual(r["out_of_scope_removed"], [])

    def test_guard_exception_is_load_bearing(self):
        # 가드 예외를 빼면(판정이 항상 False) 같은 제거가 소실로 막힌다.
        pub = {"cards": [_card483("fda483-196231", "Manufacturer", CARDIONXT_196231)]}
        with mock.patch.object(self.g, "_is_out_of_scope_483_card", lambda c: False):
            r = self.g.compare(pub, {"cards": []})
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
