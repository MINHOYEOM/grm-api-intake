"""[2026-08-03] MFDS 한국어 tier 어휘 공백 회귀 테스트.

배경 — Tier 1 로 떨어진 MFDS 30건 중 기존 한국어 목록(`MFDS_GMP_TERMS`·`MFDS_KO_BOOST`)에
걸리는 것이 **0건**이었다. 영어 목록은 같은 개념을 이미 알고 있는데(recall·stability·
container closure) 한국어 목록에만 없던 **어휘 비대칭**이다.

이 파일이 지키는 것은 두 가지다:
  ① 그 4건이 다시 Tier 1 로 떨어지지 않는다(회수).
  ② 새 어휘가 **식품·화장품·의료기기 제외 게이트를 뚫지 않는다**(가장 큰 위험).
     `MFDS_GMP_TERMS` 에 순진하게 넣었을 때 "식품 회수"가 Pending → Likely 로 뒤집혀
     수집·승격되는 것을 실측으로 확인했고, 그래서 tier 전용 층으로 분리했다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds as M


class TestMfdsQaTopicFloor(unittest.TestCase):
    TYPE_GUIDE = "guidance-industry"

    def _tier(self, title, type_or_class=None, body=""):
        toc = type_or_class or self.TYPE_GUIDE
        return M._mfds_tier(toc, M._mfds_relevance(title, body), title, body)

    # ── ① 회수돼야 하는 실제 항목(실측 기반) ─────────────────────────────────
    def test_recovered_real_headlines(self):
        cases = [
            "의약품등 회수·폐기 처리 운영 지침",
            "의약품 수입자 약사감시 수행 절차",
            "의약품등 안정성시험기준 질의응답집",
            "의약품 유사 용기·포장 및 표시 개선 사례집",
        ]
        for title in cases:
            self.assertEqual(self._tier(title), "Tier 2", msg=title)

    # ── ② 제외 도메인은 뚫리면 안 된다(핵심 회귀 가드) ───────────────────────
    def test_excluded_domains_stay_pending_and_tier1(self):
        """식품·화장품·의료기기에 같은 주제어가 있어도 수집 제외·Tier 1 이어야 한다."""
        cases = [
            "식품 회수 명령 안내",
            "건강기능식품 회수 조치",
            "수입식품 회수 절차",
            "화장품 용기·포장 표시 개선",
            "의료기기 안정성시험 가이드",
            "축산물 회수 지침",
        ]
        for title in cases:
            self.assertEqual(M._mfds_relevance(title, ""), "Pending", msg=title)
            self.assertEqual(self._tier(title), "Tier 1", msg=title)

    def test_topic_floor_never_applies_to_pending(self):
        """Pending(수집 제외)인 항목에는 주제어 바닥을 적용하지 않는다."""
        self.assertFalse(M._qa_topic_floor_applies("Pending", 0, "회수 지침", ""))

    def test_topic_floor_never_applies_when_excluded(self):
        """제외어가 하나라도 있으면 적용하지 않는다(관련성이 통과했더라도)."""
        self.assertFalse(M._qa_topic_floor_applies("Likely", 1, "의약품 회수", ""))

    # ── ③ 관련성 판정은 **바뀌지 않았다** ────────────────────────────────────
    def test_relevance_is_untouched_by_topic_terms(self):
        """주제어는 tier 전용이다 — 이게 깨지면 제외 게이트가 약해진다.

        `MFDS_GMP_TERMS` 에 넣었다면 gmp_hits 가 올라 제외 구제 조건이 무너졌을 것이다.
        """
        for term in M.MFDS_QA_TOPIC_TERMS:
            self.assertNotIn(term, M.MFDS_GMP_TERMS,
                              msg=f"{term!r} 가 MFDS_GMP_TERMS 에 들어가면 관련성 판정을 바꾼다")
            self.assertNotIn(term, M.MFDS_KO_BOOST,
                              msg=f"{term!r} 를 KO_BOOST 에 넣으면 Tier 3 로 과승격된다")

    def test_pharma_only_topic_still_pending_without_pharma_signal(self):
        """의약품 신호가 전혀 없는 일반 문서는 주제어만으로 수집되지 않는다."""
        self.assertEqual(M._mfds_relevance("재고 회수 절차 안내", ""), "Pending")

    # ── ④ 기존 동작 불변 ────────────────────────────────────────────────────
    def test_existing_tier3_boost_unchanged(self):
        for title in ["무균 제조소 실태조사 결과", "데이터 완전성 위반 적발"]:
            self.assertEqual(self._tier(title), "Tier 3", msg=title)

    def test_existing_gmp_floor_unchanged(self):
        self.assertEqual(self._tier("의약품 제조소 품질관리 안내"), "Tier 2")

    def test_internal_review_procedures_stay_tier1(self):
        """식약처 **내부** 심사 업무절차는 QA 독자 대상이 아니다 — 승격 금지."""
        cases = [
            "신약 품목허가·심사 업무절차",
            "의약품 심사자문단 구성 및 운영",
            "의약품심사부 민원상담 처리절차",
            "2025년 의약품 허가보고서",
        ]
        for title in cases:
            self.assertEqual(self._tier(title, "guidance-internal"), "Tier 1", msg=title)


if __name__ == "__main__":
    unittest.main()
