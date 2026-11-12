"""11-12 대표 결정 — 의약품 임상 스폰서(GCP)·생물분석 CRO 483 은 브리프 범위 안.

지적사항 범위(migration 146)는 같은 날 `sponsor`·`bioanalytical` 을 부정목록에서 뺐는데, 브리프·수집
범위(`grm_taxonomy.is_out_of_scope_483_establishment`)는 10-26 규칙 그대로 `sponsor`·`contract
research organization` 을 범위 밖으로 두었다. 그래서 의약품 스폰서인 "ANDA Sponsor" 까지 PDF 를
받기 전에 건너뛰었다. 두 층을 같은 결정으로 맞춘다.

유지: 임상시험자(Clinical Investigator)·IRB 는 146 과 같이 범위 밖. 의약품 단서 없는 스폰서·CRO 는
본문 축(기기·식품 신호)에서 그대로 걸러진다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grm_taxonomy as t  # noqa: E402


class SponsorCroInScopeTest(unittest.TestCase):
    def test_sponsor_and_cro_types_are_not_out_of_scope_by_type(self) -> None:
        for est in ("Sponsor", "ANDA Sponsor", "Contract Research Organization",
                    "Contract Research Organisation", "Bioanalytical Lab"):
            with self.subTest(est=est):
                self.assertFalse(t.is_out_of_scope_483_establishment(est))

    def test_drug_sponsor_with_drug_observation_stays_in_scope(self) -> None:
        body = ("OBSERVATION 1 The sponsor failed to ensure proper monitoring of the clinical "
                "investigation of the investigational drug product.")
        self.assertFalse(t.is_out_of_scope_483("Sponsor", body))
        self.assertFalse(t.is_out_of_scope_483("ANDA Sponsor", body))

    def test_device_only_sponsor_is_still_filtered_by_body(self) -> None:
        body = ("OBSERVATION 1 Design validation did not ensure the device conforms to defined "
                "user needs and intended uses, including testing of production units.")
        self.assertTrue(t.is_out_of_scope_483("Sponsor", body))

    def test_investigator_and_irb_remain_out_of_scope(self) -> None:
        for est in ("Clinical Investigator", "Institutional Review Board", "IRB"):
            with self.subTest(est=est):
                self.assertTrue(t.is_out_of_scope_483_establishment(est))


class MutationTest(unittest.TestCase):
    """수정 전 정규식(sponsor·CRO 포함)이면 위 범위-안 고정값이 범위 밖으로 떨어졌음을 확인한다."""

    def test_old_rule_would_have_dropped_sponsor_and_cro(self) -> None:
        import re
        old = re.compile(r"clinical\s+investigator|\bsponsor\b|institutional\s+review\s+board"
                         r"|\birb\b|contract\s+research\s+organi[sz]ation", re.I)
        for est in ("Sponsor", "ANDA Sponsor", "Contract Research Organization"):
            with self.subTest(est=est):
                self.assertTrue(old.search(est))
                self.assertFalse(t._FDA483_OUT_OF_SCOPE_EST_RE.search(est))


if __name__ == "__main__":
    unittest.main()
