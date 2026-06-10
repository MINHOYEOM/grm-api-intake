"""MFDS GMP 실태조사 수집기 회귀 — P6(표지 너머 지적/결론 추출).

GMP 실사 결과 PDF 는 [표지 → 제조소 현황 → 실태조사 개요 → 실태조사 결과 →
평가 결과 지적(보완)사항] 순서다. 카드 인용/요약이 표지 보일러플레이트가 아니라
실제 지적/결론을 가리키도록 _extract_deficiency_excerpt 가 결론 섹션부터 잘라낸다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds_gmp_inspection as g

# 실제 PDF 본문(평탄화) 형태 — 표지/개요 보일러플레이트 + 평가 결과 지적사항.
_FULL_PRESENT = (
    "- 1 - 의약품 제조소 GMP 정기실태조사(정기실사) 결과 "
    "제조소 현황(Name & full address of the Inspected site) "
    "제조소명: 에이치디엑스(주)(제6공장) 소재지: 전라남도 순천시 순광로 221 "
    "실태조사 개요(Overview of the inspection) 실사 목적: 「약사법」 제38조의3 및 "
    "제69조에 따라 의약품 GMP 준수 여부를 확인·조사 실사 방식: 현장실사 "
    "실사 기간: 2026. 1. 13. ∼ 2026. 1. 15. (3일) "
    "실태조사 결과(Inspection Results) 실사 대상 제형 및 제조방법: "
    "무균-일반제제(방사성의약품)-주사제 완제 "
    "평가 결과 지적(보완)사항(Deficiencies) 품질경영 기타 [별표 1] 제1.2호 "
    "오염관리전략 수립 미흡 보완 완료 시설장비 기타 제2.3호 공기조화장치 정기 점검 미흡"
)
_FULL_NONE = (
    "- 1 - 의약품 제조소 GMP 정기실태조사(정기실사) 결과 제조소 현황 "
    "제조소명: 대상(주) 소재지: 전북특별자치도 군산시 외항1길 208 "
    "실태조사 개요 실사 목적: 「약사법」 제38조의3 실사 기간: 2026. 4. 24. (1일) "
    "실태조사 결과 실사 대상 제형: 비무균-일반제제-정제 원료 "
    "의약품 제조 및 품질관리기준(GMP) 평가 결과 지적(보완)사항(Deficiencies) 없음"
)


class TestDeficiencyExcerpt(unittest.TestCase):
    def test_excerpt_skips_cover_and_starts_at_findings(self):
        ex = g._extract_deficiency_excerpt(_FULL_PRESENT)
        # 표지(제조소명·실사목적·실사기간)는 제외된다.
        self.assertNotIn("제조소명", ex)
        self.assertNotIn("실사 목적", ex)
        self.assertNotIn("실사 기간", ex)
        # 결론 섹션과 실제 지적사항은 포함된다.
        self.assertTrue(ex.startswith("평가 결과 지적(보완)사항(Deficiencies)"))
        self.assertIn("오염관리전략 수립 미흡", ex)

    def test_excerpt_none_case_is_short_conclusion(self):
        ex = g._extract_deficiency_excerpt(_FULL_NONE)
        self.assertNotIn("제조소명", ex)
        self.assertIn("없음", ex)

    def test_excerpt_empty_when_no_marker(self):
        self.assertEqual(g._extract_deficiency_excerpt("표지만 있는 문서"), "")

    def test_excerpt_empty_on_empty_text(self):
        self.assertEqual(g._extract_deficiency_excerpt(""), "")

    def test_excerpt_capped_at_body_limit(self):
        big = "평가 결과 지적(보완)사항 " + ("가" * (g.MAX_ATTACHMENT_BODY_CHARS + 500))
        self.assertLessEqual(len(g._extract_deficiency_excerpt(big)),
                             g.MAX_ATTACHMENT_BODY_CHARS)

    def test_assess_deficiency_still_present(self):
        # 추출이 판정을 바꾸지 않는다(회귀).
        self.assertEqual(g._assess_deficiency(_FULL_PRESENT), "present")
        self.assertEqual(g._assess_deficiency(_FULL_NONE), "none")

    def test_present_wins_over_incidental_no_deficiency(self):
        """실제 지적 + 부수적 '이상 없음' 공존 → present (A1 수정 검증)."""
        text = (
            "- 1 - 의약품 제조소 GMP 정기실태조사(정기실사) 결과 "
            "제조소 현황(Name & full address of the Inspected site) "
            "제조소명: 테스트제약(주) 소재지: 서울특별시 "
            "실태조사 개요(Overview of the inspection) 실사 목적: 정기 "
            "평가 결과 지적(보완)사항(Deficiencies) "
            "제조 공정 일탈 발견 보완 필요 설비 외관 이상 없음"
        )
        self.assertEqual(g._assess_deficiency(text), "present")

    def test_incidental_no_deficiency_only_stays_none(self):
        """지적 단서 없이 '이상 없음'만 있는 정상 보고서 → none (과교정 방지)."""
        text = (
            "평가 결과 지적(보완)사항(Deficiencies) 없음 "
            "설비 외관 이상 없음"
        )
        self.assertEqual(g._assess_deficiency(text), "none")

    def test_b3_none_then_header_stays_none(self):
        """결론 '없음' 뒤 '제조소 현황' 헤더의 '제조' 오승격 차단 (B3 잠금)."""
        text = (
            "평가 결과 지적(보완)사항(Deficiencies) 없음 "
            "제조소 현황 제조소명: 정상제약(주)"
        )
        self.assertEqual(g._assess_deficiency(text), "none")

    def test_b3_none_then_general_header_stays_none(self):
        """결론 '없음' 뒤 '제조소 일반현황' 헤더 변형도 none 유지 (B3 잠금)."""
        text = (
            "평가 결과 지적(보완)사항(Deficiencies) 없음 "
            "제조소 일반현황 제조소명: 정상제약(주)"
        )
        self.assertEqual(g._assess_deficiency(text), "none")

    def test_b3_header_then_boilerplate_without_verdict_not_present(self):
        """'없음' 앵커조차 없는 정상 보고서: 헤더+보일러플레이트만 → present 금지 (B3).

        '제조소 (일반)현황' 의 '제조' 가 80자 창에 걸리던 오탐과,
        "Deficiencies 존재+'없음' 부재 → present" fallback 오탐을 함께 잠근다.
        판정 근거가 없으므로 unknown(→ manual_review_required)으로 떨어져야 한다.
        """
        cases = [
            "평가 결과 지적(보완)사항(Deficiencies) 제조소 일반현황 표.",
            "목차 1. 제조소 현황 2. 실태조사 개요 "
            "3. 지적(보완)사항(Deficiencies) 4. 제조소 일반현황",
            "지적(보완)사항 다음 페이지: 제조소 현황",
        ]
        for text in cases:
            with self.subTest(text=text[:40]):
                self.assertNotEqual(g._assess_deficiency(text), "present")
                self.assertEqual(g._assess_deficiency(text), "unknown")

    def test_b3_real_findings_with_verdict_stay_present(self):
        """실제 지적(판정어 동반)은 형태별로 present 유지 (B3 과교정 방지)."""
        cases = [
            # 분류 명사 + 판정어(미흡)
            "평가 결과 지적(보완)사항(Deficiencies) 품질경영 기타 [별표 1] "
            "제1.2호 오염관리전략 수립 미흡",
            # '제조' 비-제조소 형태 + 판정어(일탈)
            "평가 결과 지적(보완)사항(Deficiencies) 제조 공정 일탈 발견 보완 필요",
            # 명시적 '있음'
            "지적(보완)사항 있음",
            # 건수 직접 표기
            "지적(보완)사항(Deficiencies) 총 3건",
            # 분류 명사 + N건
            "지적(보완)사항 허가관리 변경허가 미신청 1건",
        ]
        for text in cases:
            with self.subTest(text=text[:40]):
                self.assertEqual(g._assess_deficiency(text), "present")


if __name__ == "__main__":
    unittest.main()
