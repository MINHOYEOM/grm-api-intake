#!/usr/bin/env python3
"""grm-finding-taxonomy/v10 tests -- 한글 조사가 영문 키워드의 단어경계를 깨는 결함.

★이 버전이 고치는 것은 **어휘 공백이 아니라 매칭 엔진 결함**이다. 그래서 어휘를
아무리 보강해도 영영 안 고쳐지는 종류였다.

  `audit trail` 은 v2 부터 data_integrity 의 키워드였다. 그런데 실측 문장
    "기타 [별표 1] 제2.2호 나목 Audit Trail과 사용 대장의 일치 확인을 위한 세부 실행 절차 미흡"
  은 캐치올(기타 품질시스템)로 떨어져 있었다. 이유는 파이썬 `re` 의 `\\b` 가 한글도
  `\\w` 로 보기 때문이다 — "trail" 다음이 "과"라 **뒤쪽 단어경계가 존재하지 않는다.**
  등록된 키워드가 매칭되지 않는데 아무 소리도 나지 않는다.

  이 결함은 한국어 본문 + 영문 GMP 용어라는 조합에서만 발화하므로 FDA 원문 위주의
  코퍼스에서는 보이지 않았다(실측: 영문 레인 22,868행에서 v9→v10 변동 0행).

두 번째 변경은 평범한 표기 누락이다 — v4 가 넣은 "annual product review" 의 국내
표기 "제품품질평가" 가 없어 식약처 지적서의 PQR 문장이 전부 캐치올로 떨어졌다.

★이 파일이 특히 고정하는 것: **기각한 후보 3종**(아래 마지막 클래스). 숫자만 보면
'기타가 줄었다'라 매력적이지만 전건 측정에서 이미 맞게 분류된 행을 더 많이 훔쳤다.
근거 없이 다시 넣지 못하도록 이유와 함께 박아 둔다.
"""

from __future__ import annotations

import unittest

import grm_findings as gf


# 라이브 실측 finding_text 그대로(2026-08-26, source=MFDS).
_LIVE_AUDIT_TRAIL = (
    "기타 [별표 1] 제2.2호 나목 Audit Trail과 사용 대장의 일치 확인을 위한 "
    "세부 실행 절차 미흡"
)
_LIVE_PQR = "기타 [별표 1] 제7.3호 제품품질평가에 대한 추가 자료를 제출할 것."


class TaxonomyV10VersionTest(unittest.TestCase):
    def test_version_is_v10_and_history_still_accepted(self) -> None:
        self.assertEqual(gf.TAXONOMY_VERSION, "grm-finding-taxonomy/v10")
        self.assertIn("grm-finding-taxonomy/v9", gf.TAXONOMY_VERSIONS)
        self.assertIn("grm-finding-taxonomy/v1", gf.TAXONOMY_VERSIONS)
        # 카테고리 개수·코드는 이 버전에서 손대지 않는다.
        self.assertEqual(len(gf.FINDING_TAXONOMY), 20)


class HangulParticleBreaksAsciiBoundaryTest(unittest.TestCase):
    """조사가 붙은 영문 키워드가 매칭되어야 한다."""

    def test_live_audit_trail_sentence_is_data_integrity(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(_LIVE_AUDIT_TRAIL), "data_integrity")

    def test_every_common_particle_attaches_without_breaking_the_match(self) -> None:
        """조사는 한 종류가 아니다 — 하나만 통과시키면 나머지가 조용히 남는다."""
        for particle in ("과", "을", "의", "에", "이", "은", "도", "만", "로"):
            with self.subTest(particle=particle):
                self.assertEqual(
                    gf.classify_finding_category("audit trail%s 관련 기록 미흡" % particle),
                    "data_integrity",
                )

    def test_the_old_b_boundary_really_did_fail_here(self) -> None:
        """[근거 고정] 이 수리가 실재하는 결함을 고쳤음을 회귀 없이 증명한다.

        수리본만 검사하면 "원래도 됐던 것 아니냐"를 구분할 수 없다. 종전 정규식을
        그 자리에서 다시 만들어 **실패하는 것을 확인**한다."""
        import re
        old = re.compile(r"\baudit\s+trails?\b", re.IGNORECASE)
        self.assertIsNone(old.search(_LIVE_AUDIT_TRAIL.lower()),
                          "종전 경계가 실패하지 않는다면 이 수리의 근거가 사라진다")
        new = gf._ascii_keyword_pattern("audit trail")
        self.assertIsNotNone(new.search(_LIVE_AUDIT_TRAIL.lower()))

    def test_ascii_word_boundary_is_still_enforced(self) -> None:
        """[음성 검사] 경계를 푼 게 아니라 **ASCII 로만** 다시 정의한 것이다.

        경계를 통째로 없앴다면 부분 문자열이 걸린다 — 그건 v2 가 없앤 결함의 부활이다."""
        pat = gf._ascii_keyword_pattern("qa")
        self.assertIsNone(pat.search("aqua"), "부분 문자열이 걸렸다 — v1 substring 회귀")
        self.assertIsNone(pat.search("qatar"))
        self.assertIsNotNone(pat.search("qa 검토"))
        self.assertIsNotNone(pat.search("qa검토"))       # 한글 인접은 허용

    def test_english_source_text_is_untouched(self) -> None:
        """영문 원문은 앞뒤가 공백·구두점이라 종전과 동일하게 동작해야 한다.

        (전건 실측: 영문 레인 22,868행에서 v9→v10 변동 0행.)"""
        for text, want in (
            ("Your firm failed to maintain an audit trail for the HPLC system.",
             "data_integrity"),
            ("Media fill runs were not performed.", "aseptic_sterility_assurance"),
            ("Equipment used in manufacturing is not maintained.", "equipment_facility"),
        ):
            with self.subTest(text=text[:32]):
                self.assertEqual(gf.classify_finding_category(text), want)


class ProductQualityReviewKoreanSpellingTest(unittest.TestCase):
    def test_live_pqr_sentence_is_quality_unit_oversight(self) -> None:
        self.assertEqual(
            gf.classify_finding_category(_LIVE_PQR), "quality_unit_oversight")

    def test_korean_spelling_sits_beside_its_english_twin(self) -> None:
        """새 개념이 아니라 **이미 있는 개념의 표기 누락**이라는 것이 채택 근거였다."""
        qu = next(c for c in gf.FINDING_TAXONOMY if c.code == "quality_unit_oversight")
        self.assertIn("annual product review", qu.keywords)
        self.assertIn("제품품질평가", qu.keywords)


class RejectedV10CandidatesTest(unittest.TestCase):
    """전건 측정에서 **기각한** 후보들. 근거와 함께 박아 둔다.

    판정 기준은 "기타가 얼마나 줄었나"가 아니라 **이미 맞게 분류된 행을 훔치는가**다.
    분류기는 선언 순서대로 첫 매치를 쓰므로 앞선 카테고리에 어휘를 더하면 뒤 카테고리의
    행을 가져간다 — 숫자로는 개선처럼 보이고 내용은 나빠진다.

        후보                      기타→실질   훔침   기각 사유
        "백업"→컴퓨터화시스템          6       10    시설장비 지적을 대량 탈취
        "제품표준서"→문서화            3        5    회수·행정처분 보일러플레이트가 끌려옴
        "청정구역"→환경모니터링         6        7    "청정구역의 저울 관리"를 설비에서 탈취

    다시 넣고 싶다면 전건 재측정으로 훔침이 이득보다 작음을 보여야 한다.
    """

    def test_backup_is_not_a_computer_system_keyword(self) -> None:
        csv_ = next(c for c in gf.FINDING_TAXONOMY
                    if c.code == "computer_system_validation")
        self.assertNotIn("백업", csv_.keywords)
        # 실측 탈취 사례 — 이 문장의 주어는 백업이 아니라 시설장비다.
        self.assertEqual(
            gf.classify_finding_category("시설장비 기타 [별표 1] 2.2 백업 데이터를 정기적으로 점검할 것"),
            "equipment_facility")

    def test_cleanroom_korean_is_not_an_environmental_monitoring_keyword(self) -> None:
        em = next(c for c in gf.FINDING_TAXONOMY if c.code == "environmental_monitoring")
        self.assertNotIn("청정구역", em.keywords)
        self.assertNotIn("청정등급", em.keywords)
        # 실측 탈취 사례 — 주어는 청정구역이 아니라 저울이다.
        self.assertEqual(
            gf.classify_finding_category(
                "시설장비 [별표 1] 2.1 청정구역에 설치된 저울 점검에 사용하는 분동 관리를 적절히 할 것"),
            "equipment_facility")

    def test_master_batch_record_korean_is_not_a_documentation_keyword(self) -> None:
        doc = next(c for c in gf.FINDING_TAXONOMY if c.code == "documentation_records")
        self.assertNotIn("제품표준서", doc.keywords)
        self.assertNotIn("제조소총람", doc.keywords)


if __name__ == "__main__":
    unittest.main()
