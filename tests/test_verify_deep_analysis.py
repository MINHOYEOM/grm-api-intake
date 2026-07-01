"""verify_deep_analysis 테스트 — [WL 심층분석 fan-out 2026-07-01] 사실 근거 게이트.

카드별 fan-out(카드 1건 = 호출 1건, 독립 컨텍스트) 산출물이 원문(wl_body_full)에 근거하는지
결정론으로 대조한다. brief_lint.py 의 provenance 게이트와 동형 원칙(과알림 0·식별자성 사실은
하드 검증) — 이 모듈은 조항 인용(D2)이 그 대상이다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verify_deep_analysis as vda

_SOURCE = (
    "During our inspection of your facility, we observed significant violations of "
    "Current Good Manufacturing Practice regulations, including 21 CFR 211.192 "
    "(failure to thoroughly investigate unexplained discrepancies) and 21 CFR 211.113(b) "
    "(failure to validate aseptic processing). This letter also cites FD&C Act 502(a). "
    "Within 15 working days of receipt of this letter, you should respond with the "
    "specific steps you have taken. Failure to promptly correct these violations may "
    "result in legal action including seizure and injunction."
)

_GOOD_DEEP_ANALYSIS = {
    "key_violations": [
        {"citation": "21 CFR 211.192", "description": "예기치 못한 불일치에 대한 조사 부실",
         "risk": "재발 방지 실패로 불량 제품 유통 위험"},
        {"citation": "21 CFR 211.113(b)", "description": "무균 공정 밸리데이션 미흡",
         "risk": "미생물 오염 위험"},
    ],
    "fda_evaluation": "FDA는 이전 대응이 근본 원인 분석 없이 이뤄졌다고 평가했다.",
    "required_remediation": {
        "deadline": "15영업일 이내 서면 회신",
        "items": ["불일치 조사 절차를 재수립하고 근본 원인 분석을 문서화",
                  "무균 공정 밸리데이션을 재수행하고 결과를 제출"],
    },
    "administrative_risks": "미이행 시 압류·금지명령 등 법적 조치가 뒤따를 수 있다.",
}


class StructureTest(unittest.TestCase):
    def test_complete_sections_pass(self) -> None:
        findings = vda.check_structure(_GOOD_DEEP_ANALYSIS)
        self.assertEqual(findings, [])

    def test_missing_section_fails(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        del da["administrative_risks"]
        findings = vda.check_structure(da)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, vda.SEV_FAIL)
        self.assertEqual(findings[0].code, "D1-SECTION-INCOMPLETE")

    def test_too_short_section_fails(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["fda_evaluation"] = "짧음"
        findings = vda.check_structure(da)
        self.assertTrue(any(f.code == "D1-SECTION-INCOMPLETE" for f in findings))

    def test_key_violations_list_of_dicts_counts_as_text(self) -> None:
        # key_violations 는 리스트(dict 항목)라도 _section_text 로 합쳐져 길이 판정된다.
        findings = vda.check_structure({**_GOOD_DEEP_ANALYSIS, "key_violations": []})
        self.assertTrue(any(f.code == "D1-SECTION-INCOMPLETE" and "key_violations" in f.detail
                            for f in findings))

    def test_remediation_legacy_string_fails(self) -> None:
        # §2.5: required_remediation 문단(str) → {deadline, items[]} 객체. 구식 문자열 = FAIL.
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["required_remediation"] = "15영업일 이내 서면으로 시정 조치를 제출해야 한다(구식 문자열)."
        findings = vda.check_structure(da)
        self.assertTrue(any(f.code == "D1-SECTION-INCOMPLETE"
                            and "required_remediation" in f.detail for f in findings))

    def test_remediation_empty_items_fails(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["required_remediation"] = {"deadline": "15영업일 이내 서면 회신", "items": []}
        findings = vda.check_structure(da)
        self.assertTrue(any("items" in f.detail for f in findings))

    def test_remediation_missing_deadline_fails(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["required_remediation"] = {"items": ["시정 조치 A 를 수행하고 결과를 문서화한다"]}
        findings = vda.check_structure(da)
        self.assertTrue(any("deadline" in f.detail for f in findings))


class CitationExtractionTest(unittest.TestCase):
    def test_extracts_cfr_and_fdca_forms(self) -> None:
        text = "위반: 21 CFR 211.192, FD&C Act 502(a), section 505(a)."
        found = {vda._normalize_citation(t) for t in vda.extract_citations(text)}
        self.assertIn(vda._normalize_citation("21 CFR 211.192"), found)
        self.assertTrue(any("502(a)" in f for f in found))

    def test_bare_citation_with_hangul_particle_extracted(self) -> None:
        # Codex P1 회귀: 조사가 공백 없이 붙은 bare 조항("610.13는"/"502(a)는")도 추출돼야
        # D2 근거대조가 걸린다(예전 \b 는 숫자-한글 경계를 못 만들어 추출조차 못 했다).
        found = {vda._normalize_citation(t) for t in vda.extract_citations("610.13는 원문에 없는 조항")}
        self.assertIn("610.13", found)
        found2 = {vda._normalize_citation(t) for t in vda.extract_citations("502(a)는 조사 대상")}
        self.assertTrue(any("502(a)" in f for f in found2))


class CitationGroundingTest(unittest.TestCase):
    def test_grounded_citations_pass(self) -> None:
        findings = vda.check_citation_grounding(_GOOD_DEEP_ANALYSIS, _SOURCE)
        self.assertEqual([f for f in findings if f.severity == vda.SEV_FAIL], [])

    def test_ungrounded_citation_fails(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = list(da["key_violations"]) + [
            {"citation": "21 CFR 610.13", "description": "원문에 없는 조항", "risk": "날조 의심"}
        ]
        findings = vda.check_citation_grounding(da, _SOURCE)
        fails = [f for f in findings if f.severity == vda.SEV_FAIL]
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0].code, "D2-CITATION-UNGROUNDED")
        self.assertIn("610.13", fails[0].detail)


class NovelNumberTest(unittest.TestCase):
    def test_number_present_in_source_no_warn(self) -> None:
        findings = vda.check_novel_numbers(_GOOD_DEEP_ANALYSIS, _SOURCE)
        self.assertEqual(findings, [])  # 15(일) 은 3자리 미만이라 대상 아님, 나머지 숫자 없음

    def test_novel_long_number_warns_not_fails(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["administrative_risks"] += " FEI 30441955는 원문에 없다."
        findings = vda.check_novel_numbers(da, _SOURCE)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, vda.SEV_WARN)  # 비차단


class GateTest(unittest.TestCase):
    def test_clean_input_passes_gate(self) -> None:
        result = vda.run_deep_analysis_gate(_GOOD_DEEP_ANALYSIS, _SOURCE)
        self.assertTrue(result.ok)
        self.assertEqual(result.fail_count, 0)

    def test_fabricated_citation_blocks_merge(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = list(da["key_violations"]) + [
            {"citation": "21 CFR 999.99", "description": "지어낸 조항", "risk": "-"}
        ]
        result = vda.run_deep_analysis_gate(da, _SOURCE)
        self.assertFalse(result.ok)
        self.assertGreaterEqual(result.fail_count, 1)

    def test_fabricated_bare_citation_with_particle_blocks_gate(self) -> None:
        # Codex P1 회귀(핵심 위협 벡터): 한국어 산문에 조사 직결로 날조 조항("610.13는")을 심어
        # D2 를 통째로 우회하던 것을 차단. citation 필드는 grounded, 산문에 날조 조항만 삽입.
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = list(da["key_violations"]) + [
            {"citation": "21 CFR 211.192",
             "description": "원문에 없는 610.13는 조항을 근거로 든 날조 서술", "risk": "-"}
        ]
        result = vda.run_deep_analysis_gate(da, _SOURCE)
        self.assertFalse(result.ok)   # 예전엔 True(우회) — 이제 FAIL
        self.assertTrue(any("610.13" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_incomplete_structure_blocks_merge_and_skips_citation_pass(self) -> None:
        da = dict(_GOOD_DEEP_ANALYSIS)
        del da["fda_evaluation"]
        result = vda.run_deep_analysis_gate(da, _SOURCE)
        self.assertFalse(result.ok)
        # 구조 불완전 시 인용 대조는 생략(findings 는 D1 만).
        self.assertTrue(all(f.code == "D1-SECTION-INCOMPLETE" for f in result.findings))


if __name__ == "__main__":
    unittest.main()
