"""FDA WL tier 본문 cGMP floor 회귀 — 2026-08-31 R3 Medical Companies 미채택 사고.

무엇이 있었나. FDA WL 의 tier 는 인덱스 행(subject·발행부서)만 보고 정해졌다. FDA WL 인덱스의
Subject 열은 사실상 통제어휘라 cGMP 서한은 "CGMP/…" 접두를 달고, 기존 `wl_cgmp` 규칙이 그
접두를 집어 Tier 3 로 올린다. 그런데 표제가 다른 축인데 **본문에만** cGMP 위반이 있는 편지가
있다 — R3 Medical Companies(표제 "Unapproved New Drugs/Unlicensed Biological Product
Violations", 본문에 CGMP 위반 6건)가 Tier 1 로 떨어져 2026-08-31 브리프에서 빠졌다.
본문은 이미 확보돼 있었는데 판정에 쓰지 않았다.

왜 '전문 재판정'이 아니라 좁은 floor 인가. 본문을 일반 어휘층에 통째로 먹이면 노이즈가
폭발한다(실측: 텔레헬스·미승인의약품 서한이 `t2_keywords` 로 무더기 승격). 그래서
`fda_wl_body_cgmp_floor` 는 ①부서(CDER·CBER) ②cGMP 표현 ③21 CFR 210/211 인용을 **모두**
요구한다. ③이 없으면 조제(compounding) 면제 조항을 설명하는 **각주**에 걸린다(실측:
케타민 판매 사이트 3건이 §503A 각주만으로 오승격).

전수 실측(비-Tier3 WL 49건): 승격 2건(R3·Fagron BV) 모두 본문에 "CGMP Violations" 절 실재,
오승격 0.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci

CDER = "Center for Drug Evaluation and Research (CDER)"
CBER = "Center for Biologics Evaluation and Research (CBER)"
FOODS = "Human Foods Program"

# R3 서한의 실제 문장 형태(cGMP 절 + 규정 인용 + 번호 위반).
_BODY_REAL_CGMP = (
    "This warning letter also summarizes significant violations of current good "
    "manufacturing practice (CGMP) requirements, including violations of section "
    "501(a)(2)(B) of the FD&C Act, 21 U.S.C. 351(a)(2)(B), and 21 CFR parts 210 and 211, "
    "in the manufacture of your products. CGMP Violations 1. Failure to establish and "
    "follow appropriate written procedures designed to prevent microbiological "
    "contamination of drug products purporting to be sterile, including procedures for "
    "validation of all aseptic and sterilization processes, as required by 21 CFR 211.113(b)."
)

# 케타민 판매 사이트 서한의 실제 각주 형태 — cGMP 문구는 있으나 그 업체의 위반이 아니다.
_BODY_COMPOUNDING_FOOTNOTE = (
    "Section 503A of the FD&C Act (21 U.S.C. 353a) describes the conditions under which "
    "human drug products compounded by a licensed pharmacist in a State-licensed pharmacy "
    "or a Federal facility, or a licensed physician, qualify for exemptions from three "
    "sections of the FD&C Act: compliance with current good manufacturing practice of "
    "section 501(a)(2)(B) (21 U.S.C. 351(a)(2)(B)); labeling with adequate directions for "
    "use of section 502(f)(1); and FDA approval prior to marketing of section 505."
)

# 같은 표제·같은 부서(CBER)인데 cGMP 를 전혀 다루지 않는 서한(Blue Horizon 형태).
_BODY_NO_CGMP = (
    "Your products are unapproved new drugs in violation of section 505(a) of the FD&C Act "
    "and unlicensed biological products in violation of section 351(a)(1) of the PHS Act. "
    "Your products fail to meet the criteria in 21 CFR 1271.10(a) for homologous use only."
)


class WLBodyCgmpFloorTest(unittest.TestCase):
    """`fda_wl_body_cgmp_floor` — 세 조건 AND. 하나라도 빠지면 False."""

    def test_real_cgmp_letter_floors(self):
        """본문에 cGMP 절 + 21 CFR 211 인용 → floor 성립(부서 CDER·CBER 양쪽)."""
        for office in (CDER, CBER):
            with self.subTest(office=office):
                self.assertTrue(ci.fda_wl_body_cgmp_floor(office, _BODY_REAL_CGMP))

    def test_compounding_footnote_does_not_floor(self):
        """§503A 면제 각주의 'current good manufacturing practice' 만으로는 승격 금지.

        이 검사가 없으면 케타민 판매 사이트 서한 3건이 Tier 3 로 오승격한다(실측).
        """
        self.assertIn("current good manufacturing practice", _BODY_COMPOUNDING_FOOTNOTE.lower())
        self.assertFalse(ci.fda_wl_body_cgmp_floor(CDER, _BODY_COMPOUNDING_FOOTNOTE))

    def test_non_cgmp_letter_does_not_floor(self):
        """같은 표제·같은 부서라도 본문에 cGMP 가 없으면 그대로 — 표제가 아니라 내용으로 가른다."""
        self.assertFalse(ci.fda_wl_body_cgmp_floor(CBER, _BODY_NO_CGMP))

    def test_non_drug_office_does_not_floor(self):
        """식품 부서 서한은 본문에 cGMP 가 있어도 제외(21 CFR 111 은 다른 축)."""
        self.assertFalse(ci.fda_wl_body_cgmp_floor(FOODS, _BODY_REAL_CGMP))

    def test_empty_body_does_not_floor(self):
        """본문 확보 실패(403·no-anchor)는 승격 근거가 될 수 없다."""
        for body in ("", None):
            with self.subTest(body=body):
                self.assertFalse(ci.fda_wl_body_cgmp_floor(CDER, body or ""))


class WLTierTwoStageTest(unittest.TestCase):
    """인덱스 1차 판정 → 본문 floor 2차. **올리기만** 한다."""

    def _index_tier(self, subject, office):
        rel = ci.compute_relevance(subject, subject, office)
        return ci.compute_signal_tier_detail(
            ci.SOURCE_FDA_WL, office, rel, "N/A", subject, subject, office)

    def test_r3_regression_index_tier1_body_tier3(self):
        """사고 재현 — 인덱스만으로는 Tier 1, 본문을 보면 Tier 3."""
        subject = "Unapproved New Drugs/Unlicensed Biological Product Violations"
        first = self._index_tier(subject, CBER)
        self.assertEqual(first.tier, "Tier 1")
        self.assertEqual(first.reason, ci.REASON_DEFAULT_FALLTHROUGH)
        self.assertTrue(ci.fda_wl_body_cgmp_floor(CBER, _BODY_REAL_CGMP))

    def test_cgmp_subject_already_tier3_without_body(self):
        """기존 경로 불변 — 'CGMP/' 접두 표제는 본문 없이도 Tier 3(`wl_cgmp`)."""
        first = self._index_tier("CGMP/Finished Pharmaceuticals/Adulterated", CDER)
        self.assertEqual(first.tier, "Tier 3")
        self.assertEqual(first.reason, "wl_cgmp")

    def test_floor_never_lowers(self):
        """floor 는 Tier 3 를 낮추지 않는다 — 호출부는 rank<3 일 때만 적용한다."""
        first = self._index_tier("CGMP/Finished Pharmaceuticals/Adulterated", CDER)
        self.assertEqual(ci._TIER_RANK[first.tier], 3)
        self.assertFalse(ci._TIER_RANK.get(first.tier, 0) < 3)

    def test_tier_rank_ordering(self):
        self.assertLess(ci._TIER_RANK["Tier 1"], ci._TIER_RANK["Tier 2"])
        self.assertLess(ci._TIER_RANK["Tier 2"], ci._TIER_RANK["Tier 3"])


class TierObserverSingleRecordTest(unittest.TestCase):
    """2단 판정이 관측기를 두 번 세지 않는다(계기 부풀림 금지)."""

    def test_record_tier_decision_records_once(self):
        ci.TIER_OBSERVER.reset()
        decision = ci.TierDecision("Tier 3", "wl_cgmp_body")
        ci.record_tier_decision(decision, ci.SOURCE_FDA_WL, "blob")
        self.assertEqual(ci.TIER_OBSERVER.reason_counts.get("wl_cgmp_body"), 1)
        ci.TIER_OBSERVER.reset()

    def test_record_tier_decision_swallows_observer_failure(self):
        """관측 실패가 수집을 죽이면 안 된다."""
        ci.TIER_OBSERVER.reset()
        broken = object()  # decision.reason 접근에서 AttributeError
        ci.record_tier_decision(broken, ci.SOURCE_FDA_WL, "blob")  # 예외 전파 없으면 통과
        ci.TIER_OBSERVER.reset()


if __name__ == "__main__":
    unittest.main()
