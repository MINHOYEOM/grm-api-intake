#!/usr/bin/env python3
"""MHRA 의약품 알림의 심각도 등급(Class 1~4) 배선.

★계기 — MHRA 알림은 제목에 등급을 명시하는데 tier 엔진이 그걸 읽지 않고 **키워드 매칭에만
의존**했다. 그게 왜 위험한지는 이 코퍼스 자신이 증명한다: 2026-07 수집분 Class 4 세 건은
T2 어휘에 안 걸려 Tier 1 로 저장됐는데, 2026-08-03 `#629` 가 "defect notification" 을 어휘에
추가하자 **같은 제목이 지금은 Tier 2** 다. 문서도 등급도 안 변했는데 목록이 변해 tier 가
움직인 것이다. 반대로도 성립한다 — 문구가 바뀌면 Class 2 회수가 조용히 Tier 1 로 떨어진다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_scaffold as cs  # noqa: E402
import collect_intake as ci  # noqa: E402

# tier 어휘가 전혀 없는 중립 문장. 등급만으로 판정이 나는지 보려면 blob 이 깨끗해야 한다.
NEUTRAL = "The company contacted the agency about a packaging line matter."


class TypeOrClassExtractionTest(unittest.TestCase):
    """gov.uk Atom 이 `<category>` 를 안 주므로 제목에서 등급을 뽑는다."""

    def test_extracts_class_from_title(self):
        self.assertEqual(
            ci._mhra_alert_type_or_class(
                "Class 2 Medicines Recall: Zentiva Pharma UK Limited, Fingolimod"),
            "Class 2 Medicines Recall")

    def test_handles_update_prefix(self):
        """실측 제목에 'UPDATE: ' 접두가 붙는 건이 있다 — 제목 처음에 고정하면 놓친다."""
        self.assertEqual(
            ci._mhra_alert_type_or_class(
                "UPDATE: Class 4 Medicines Defect Notification: Relonchem Limited"),
            "Class 4 Medicines Defect Notification")

    def test_category_wins_when_present(self):
        self.assertEqual(
            ci._mhra_alert_type_or_class("Class 2 Medicines Recall: X", "Given Category"),
            "Given Category")

    def test_falls_back_to_literal(self):
        """등급을 못 뽑으면 종전 리터럴 — behavior 불변(블로그 등)."""
        self.assertEqual(
            ci._mhra_alert_type_or_class("Inspectorate blog: data integrity"),
            "Medicines Recall")

    def test_class_number_is_arabic(self):
        for toc, want in (("Class 1 Medicines Recall", 1), ("Class 2 Medicines Recall", 2),
                          ("Class 4 Medicines Defect Notification", 4),
                          ("Medicines Recall", 0), ("Blog", 0), ("", 0)):
            with self.subTest(toc=toc):
                self.assertEqual(ci._mhra_alert_class_number(toc), want)


class ClassDrivesTierTest(unittest.TestCase):
    """★핵심 — 등급만으로 tier 가 정해지는가(어휘가 비어 있어도)."""

    def _decide(self, toc, qa="Unknown", text=NEUTRAL):
        return ci.compute_signal_tier_detail(ci.SOURCE_MHRA, toc, qa, "N/A", text)

    def test_fixture_itself_has_no_tier_vocabulary(self):
        """★픽스처 자신을 assert — 중립 문장에 어휘가 섞이면 이 클래스는 아무것도 재지 않는다."""
        blob = NEUTRAL.lower()
        self.assertEqual(ci._tier_kw_match(blob, ci.SIGNAL_TIER2_KEYWORDS), 0)
        self.assertEqual(ci._tier_kw_match(blob, ci.SIGNAL_TIER3_KEYWORDS), 0)

    def test_class_1_reaches_tier_3(self):
        d = self._decide("Class 1 Medicines Recall")
        self.assertEqual((d.tier, d.reason), ("Tier 3", "recall_class_i"))

    def test_class_2_reaches_tier_2_without_any_keyword(self):
        """이 배선이 막는 사고 그 자체 — 어휘가 없으면 종전엔 Tier 1 로 조용히 떨어졌다."""
        d = self._decide("Class 2 Medicines Recall")
        self.assertEqual((d.tier, d.reason), ("Tier 2", "recall_class_ii"))

    def test_class_1_overrides_unrelated_clamp(self):
        """openFDA Class I 과 동형 — 강제 예외라 qa_unrelated 클램프보다 앞이다."""
        self.assertEqual(self._decide("Class 1 Medicines Recall", qa="Unrelated").tier,
                         "Tier 3")

    def test_class_2_respects_unrelated_clamp(self):
        """openFDA Class II 와 동형 — 클램프 뒤라 Unrelated 는 Tier 1 로 고정된다."""
        self.assertEqual(self._decide("Class 2 Medicines Recall", qa="Unrelated").tier,
                         "Tier 1")

    def test_class_3_and_4_reach_tier_2(self):
        """★`#821` 이 미룬 규제 판단의 결론(MHRA-CLASS34-POLICY) — 내리지 않고 올린다.

        MHRA 등급은 **환자 위해의 긴급도**지 GMP 관련성이 아니다. 전수 실측에서 Class 3 은
        잘못된 PIL·GMP 일탈·불순물 기준초과, Class 4 는 병 속 이물·정제 수량 오류·PIL 안전
        정보 누락이었다 — 전부 제조·품질 일탈이라 이 사이트 독자에게는 목적물 그 자체다.
        환자위해 등급으로 낮추면 제조 결함 신호를 체계적으로 가리게 된다."""
        for toc in ("Class 3 Medicines Recall", "Class 4 Medicines Defect Notification"):
            with self.subTest(toc=toc):
                d = self._decide(toc)
                self.assertEqual((d.tier, d.reason), ("Tier 2", "mhra_medicines_alert"))

    def test_unclassed_company_led_recall_reaches_tier_2(self):
        """등급표기가 아예 없는 회수도 있다 — 전수 427건 중 22건(5.2%)이 "Company led
        medicines recall" 이고 수집기 keep 필터가 이미 받아들이는 진짜 회수다."""
        d = self._decide(ci._mhra_alert_type_or_class(
            "Company led medicines recall: Sun Pharma UK Ltd, Gemcitabine 10mg/ml"))
        self.assertEqual((d.tier, d.reason), ("Tier 2", "mhra_medicines_alert"))

    def test_reason_label_is_separate_from_class_ii(self):
        """★계기 분리 — 원인이 다른 사건을 한 카운터에 합치면 나중 진단이 반드시 틀린다."""
        self.assertEqual(self._decide("Class 2 Medicines Recall").reason, "recall_class_ii")
        self.assertEqual(self._decide("Class 3 Medicines Recall").reason,
                         "mhra_medicines_alert")

    def test_class_3_4_respect_unrelated_clamp(self):
        """Class 2 와 동형 — 클램프 뒤라 Unrelated 는 Tier 1 로 고정된다."""
        self.assertEqual(
            self._decide("Class 4 Medicines Defect Notification", qa="Unrelated").tier,
            "Tier 1")

    def test_mhra_non_medicines_gets_no_floor(self):
        """음성 검사 — 의약품 알림이 아닌 MHRA 항목(블로그)까지 올리면 안 된다."""
        self.assertEqual(self._decide("Blog").tier, "Tier 1")


class NoSpilloverTest(unittest.TestCase):
    """★음성 검사 — 이 배선이 MHRA 밖으로 새지 않는가."""

    def test_other_sources_ignore_medicines_recall_label(self):
        """MHRA 바닥은 SOURCE_MHRA 안에서만 선다 — 같은 문자열이 타 소스에 있어도 무관."""
        for src in (ci.SOURCE_EMA, ci.SOURCE_PICS, ci.SOURCE_ECA, ci.SOURCE_ISPE):
            with self.subTest(src=src):
                d = ci.compute_signal_tier_detail(src, "Class 3 Medicines Recall",
                                                  "Unknown", "N/A", NEUTRAL)
                self.assertEqual(d.tier, "Tier 1")

    def test_other_sources_ignore_arabic_class(self):
        """같은 문자열이라도 MHRA 가 아니면 등급 분기를 타면 안 된다."""
        for src in (ci.SOURCE_EMA, ci.SOURCE_PICS, ci.SOURCE_ECA, ci.SOURCE_FDA_WL):
            with self.subTest(src=src):
                d = ci.compute_signal_tier_detail(src, "Class 1 Medicines Recall",
                                                  "Unknown", "N/A", NEUTRAL)
                self.assertEqual(d.tier, "Tier 1")

    def test_openfda_roman_numerals_unchanged(self):
        """openFDA 는 로마자를 쓴다 — 종전 판정이 그대로여야 한다."""
        for toc, want in (("Class I", "Tier 3"), ("Class II", "Tier 2"),
                          ("Class III", "Tier 1")):
            with self.subTest(toc=toc):
                d = ci.compute_signal_tier_detail(ci.SOURCE_RECALL, toc, "Unknown",
                                                  "Unknown", NEUTRAL)
                self.assertEqual(d.tier, want)

    def test_mhra_blog_unchanged(self):
        d = ci.compute_signal_tier_detail(ci.SOURCE_MHRA, "Blog", "Unknown", "N/A", NEUTRAL)
        self.assertEqual(d.tier, "Tier 1")


class CardClassRowTest(unittest.TestCase):
    """등급이 비어 있던 탓에 카드 W2 의 "Class" 행이 한 번도 렌더되지 않았다."""

    def test_class_row_renders_now(self):
        row = {"type_or_class": "Class 2 Medicines Recall",
               "headline": "Class 2 Medicines Recall: Zentiva Pharma UK Limited, Fingolimod"}
        rows = cs._w2_extra_mhra_recall(row, {"title": row["headline"]})
        self.assertIn(("Class", "Class 2"), rows)

    def test_class_row_absent_on_old_literal(self):
        """음성 검사 — 등급 없는 옛 값에서는 행이 안 나야 한다(빈 행 생성 금지)."""
        row = {"type_or_class": "Medicines Recall", "headline": "Medicines Recall: X, Y"}
        rows = cs._w2_extra_mhra_recall(row, {"title": row["headline"]})
        self.assertFalse([r for r in rows if r[0] == "Class"])


if __name__ == "__main__":
    unittest.main()
