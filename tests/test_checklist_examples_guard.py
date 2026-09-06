#!/usr/bin/env python3
"""체크리스트 사례 가드(`verify_checklist_examples.py`)의 **판정기** 오프라인 검증.

네트워크·DB 없이 판정기만 돌린다. 라이브 전수 검사는 그 스크립트가 CI/dispatch 에서 하고,
여기서는 **판정기가 실제로 무엇을 잡는지**를 고정한다.

## 회귀 코퍼스 = 사용자가 신고한 그 문장들

2026-09-06 사용자 신고분을 **수리 전 화면에 실제로 실렸던 형태 그대로**(옛 043 이 내려준
전문의 앞 240자) 박아 둔다. 가드가 이 넷을 못 잡으면 그 가드는 존재 이유가 없다:

  · 1번 21 CFR 211.22 의 첫 사례 = PReye, LLC 경고서한 **서두**(지적이 아님)
  · 10번 21 CFR 211.42 에도 같은 서두가 붙음
  · 3번 211.100 · 11번 211.188 의 사례로 Jabil 의 **211.22(d)** 문장이 반복 첨부됨

그리고 **수리 후 실측 발췌**(080 findings_clause_excerpt 가 라이브에서 내려준 값)를 함께
둔다 — 가드가 정상 사례를 위반으로 잡으면(과잉) 그것도 결함이기 때문이다. 한쪽만 있으면
"아무것도 안 잡는 가드"나 "전부 잡는 가드"가 초록으로 통과한다.
"""

from __future__ import annotations

import unittest

import verify_checklist_examples as guard


# ── 수리 전 실측(옛 043 + 화면 240자 절단) ────────────────────────────────────
PREYE_PREAMBLE_KO = (
    "귀사의 의약품 제조시설인 PReye, LLC(FDA Establishment Identifier(FEI) 3031057987, "
    "4855 Ward Road, Wheat Ridge 소재)에 대하여 2026년 3월 17일부터 19일까지 실사를 "
    "실시하였다. 이 실사는 안전하지 않거나 유효하지 않거나 품질이 낮은 의약품으로부터 "
    "공중을 보호하기 위한 FDA의 법적 권한과 공중보건상의 책임에 따라 수행되었다. 본 "
    "경고서한은 완제의약품에 대"
)
PREYE_PREAMBLE_EN = (
    "during an inspection of your drug manufacturing facility, PReye, LLC, FDA "
    "Establishment Identifier (FEI) 3031057987, at 4855 Ward Road, Wheat Ridge, from "
    "March 17 to 19, 2026. This inspection was conducted under FDA's statutory authority an"
)
JABIL_2122D_KO = (
    "귀사는 품질관리부서에 적용되는 적절한 문서화된 책임과 절차를 수립하지 못하였다"
    "(21 CFR 211.22(d)). 귀사의 품질부서(QU)는 의약품 제조에 대한 적절한 감독을 제공하지 "
    "않았다. 예를 들어 QU는 다음을 보증하지 못하였다. 무균 의약품 충전 중 모든 "
    "개입(intervention)의 문서화."
)
JABIL_2122D_EN = (
    "Your firm failed to establish adequate written responsibilities and procedures "
    "applicable to the quality control unit (21 CFR 211.22(d)). Your QU did not provide "
    "adequate oversight for the manufacture of your drug products. For example, you"
)

# ── 수리 후 실측(080 라이브 발췌) ─────────────────────────────────────────────
PREYE_2122_FIXED_KO = (
    "…귀사의 품질관리부서는 제조되는 의약품이 CGMP에 적합하고 확인, 함량, 품질 및 순도에 "
    "관한 설정된 규격을 충족하도록 보증할 책임을 이행하지 못하였다(21 CFR 211.22). 귀사의 "
    "품질시스템은 미흡하다."
)
PREYE_2122_FIXED_EN = (
    "…ility to ensure drug products manufactured are in compliance with CGMP, and meet "
    "established specifications for identity, strength, quality, and purity (21 CFR 211.22). "
    "Your firm's quality systems are inadequate."
)
JABIL_211100_FIXED_KO = (
    "…귀사는 새로운 생산 단계를 도입한 후 공정 밸리데이션 시험을 수행하지 않았고, 답변서에 "
    "안정성 데이터도 제시하지 않았다(21 CFR 211.100(a))."
)
JABIL_211100_FIXED_EN = (
    "…Your firm did not perform process validation studies after introducing a new "
    "production step, nor did your response provide stability data (21 CFR 211.100(a))."
)
JABIL_211188_FIXED_KO = (
    "…특히 충전 구역에서 바이알을 제거하는 행위와 (b)(4)에서 떨어진 바이알을 제거하는 행위 "
    "등 중요 개입이 문서화되지 않았다(21 CFR 211.188)."
)
JABIL_211188_FIXED_EN = (
    "…critical interventions such as removing vials from the filling line were not "
    "documented (21 CFR 211.188)."
)


def _ex(firm: str, ko: str, en: str, fid: str = "finding-x") -> dict:
    return {"finding_id": fid, "firm_name": firm, "excerpt_ko": ko, "excerpt": en}


def _rules(problems: list[dict]) -> set[str]:
    return {p["rule"] for p in problems}


class ReportedDefectsAreCaughtTest(unittest.TestCase):
    """수리 전 실측 = 반드시 위반으로 잡힌다(가드가 무력하면 여기서 죽는다)."""

    def test_preamble_is_flagged_as_non_finding(self):
        problems = guard.check_section(
            "211.22", [_ex("PReye, LLC", PREYE_PREAMBLE_KO, PREYE_PREAMBLE_EN)])
        self.assertIn("non_finding_ko", _rules(problems))
        self.assertIn("non_finding_en", _rules(problems))

    def test_preamble_also_fails_the_anchor_rule(self):
        """서두는 특정 조항을 인용하지 않는다 — 앵커 규칙 하나만으로도 걸려야 한다."""
        problems = guard.check_section(
            "211.42", [_ex("PReye, LLC", PREYE_PREAMBLE_KO, PREYE_PREAMBLE_EN)])
        self.assertIn("anchor_ko", _rules(problems))
        self.assertIn("anchor_en", _rules(problems))

    def test_other_clause_sentence_is_flagged(self):
        """Jabil 의 211.22(d) 문장이 211.100·211.188 사례로 붙던 그 결함."""
        for section in ("211.100", "211.188"):
            with self.subTest(section=section):
                problems = guard.check_section(
                    section, [_ex("Jabil Inc.", JABIL_2122D_KO, JABIL_2122D_EN)])
                self.assertIn("anchor_ko", _rules(problems))
                self.assertIn("anchor_en", _rules(problems))

    def test_same_sentence_is_fine_for_its_own_clause(self):
        """같은 문장이라도 **그 조항**의 사례로는 정당하다 — 규칙이 문장이 아니라 짝을 본다."""
        problems = guard.check_section(
            "211.22", [_ex("Jabil Inc.", JABIL_2122D_KO, JABIL_2122D_EN)])
        self.assertEqual(problems, [])


class FixedExamplesPassTest(unittest.TestCase):
    """수리 후 실측 = 위반 0건이어야 한다(과잉 가드도 결함이다)."""

    def test_clause_anchored_excerpts_pass(self):
        cases = [
            ("211.22", "PReye, LLC", PREYE_2122_FIXED_KO, PREYE_2122_FIXED_EN),
            ("211.100", "Jabil Inc.", JABIL_211100_FIXED_KO, JABIL_211100_FIXED_EN),
            ("211.188", "Jabil Inc.", JABIL_211188_FIXED_KO, JABIL_211188_FIXED_EN),
        ]
        for section, firm, ko, en in cases:
            with self.subTest(section=section):
                self.assertEqual(guard.check_section(section, [_ex(firm, ko, en)]), [])


class RuleShapeTest(unittest.TestCase):
    def test_clause_boundary_does_not_swallow_neighbours(self):
        """`211.22` 질의가 본문 `211.226` 을 앵커로 삼으면 안 된다(080 SQL 과 같은 규칙)."""
        self.assertFalse(guard.anchored_in("… 위반이다(21 CFR 211.226).", "211.22"))
        self.assertTrue(guard.anchored_in("… 위반이다(21 CFR 211.22).", "211.22"))
        self.assertTrue(guard.anchored_in("… 위반이다(21 CFR 211.22(d)).", "211.22"))

    def test_duplicate_firm_in_one_section_is_flagged(self):
        """같은 업체가 한 조항에 두 번 = "여러 곳에서 반복되는 지적" 전제가 깨진다."""
        one = _ex("Jabil Inc.", JABIL_211188_FIXED_KO, JABIL_211188_FIXED_EN, "finding-a")
        two = _ex("Jabil Inc.", JABIL_211188_FIXED_KO, JABIL_211188_FIXED_EN, "finding-b")
        self.assertIn("duplicate_firm", _rules(guard.check_section("211.188", [one, two])))

    def test_empty_excerpt_is_flagged(self):
        self.assertIn("empty", _rules(guard.check_section("211.22", [_ex("A", "", "")])))

    def test_ordinary_finding_is_not_mistaken_for_boilerplate(self):
        """정상 지적 문장이 서두로 잡히면 가드가 과잉이다."""
        self.assertEqual(guard.non_finding_hits(PREYE_2122_FIXED_KO), [])
        self.assertEqual(guard.non_finding_hits(JABIL_2122D_KO), [])
        self.assertEqual(guard.non_finding_hits(JABIL_2122D_EN), [])


if __name__ == "__main__":
    unittest.main()
