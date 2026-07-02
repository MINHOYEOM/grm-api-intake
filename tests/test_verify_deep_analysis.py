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


# ── [소스확장 2026-07-02] MFDS 행정처분(admin-action) 스키마 + 한국법령 D2 ──────────────
_ADMIN_SOURCE = (
    "제조기록서를 사실과 다르게 작성하고 일부 시험성적서를 보관하지 않아 약사법 제38조제1항을 "
    "위반함. 제조소 현장점검 결과 청정도 관리기준 이탈이 확인됨. "
    "처분명: 제조업무정지 1개월. "
    "적용법령: 약사법 제38조제1항, 의약품 등의 안전에 관한 규칙 제48조제9호, [별표8] 행정처분 기준. "
    "과징금 166,800,000원, 납부기한 2026-07-15."
)

_GOOD_ADMIN_DA = {
    "key_violations": [
        {"citation": "약사법 제38조제1항",
         "description": "제조기록서를 사실과 다르게 작성하고 일부 시험성적서를 보관하지 않음",
         "risk": "데이터 무결성 훼손 및 품질 보증 실패"},
        {"citation": "의약품 등의 안전에 관한 규칙 제48조제9호",
         "description": "제조소 현장점검 결과 청정도 관리기준 이탈이 확인됨",
         "risk": "무균·청정 환경 오염 위험"},
    ],
    "disposition_basis": "[별표8] 행정처분 기준에 따라 제조업무정지 1개월 처분이 부과되었다.",
    "required_remediation": {
        "deadline": "2026-07-15 납부기한",
        "items": ["과징금 166,800,000원 납부", "제조기록 관리 절차를 재수립하고 CAPA 를 문서화"],
    },
    "administrative_risks": "재위반 시 가중처분 및 품목허가 취소로 이어질 수 있다.",
}


class ResolveSectionsTest(unittest.TestCase):
    def test_card_type_admin(self) -> None:
        self.assertEqual(vda.resolve_required_sections(card_type="admin-action"),
                         vda.REQUIRED_SECTIONS_ADMIN)

    def test_card_type_wl(self) -> None:
        self.assertEqual(vda.resolve_required_sections(card_type="warning-letter"),
                         vda.REQUIRED_SECTIONS)

    def test_autodetect_admin_by_disposition_key(self) -> None:
        self.assertEqual(vda.resolve_required_sections(_GOOD_ADMIN_DA),
                         vda.REQUIRED_SECTIONS_ADMIN)

    def test_autodetect_wl_default(self) -> None:
        # fda_evaluation 보유(또는 disposition_basis 부재) → WL 기본(후방호환).
        self.assertEqual(vda.resolve_required_sections(_GOOD_DEEP_ANALYSIS),
                         vda.REQUIRED_SECTIONS)


class KoreanCitationExtractionTest(unittest.TestCase):
    def test_law_article_and_byeolpyo_extracted(self) -> None:
        found = {vda._normalize_citation(t) for t in vda.extract_citations(
            "약사법 제38조제1항 및 [별표8], 제48조제9호 위반")}
        self.assertIn(vda._normalize_citation("약사법 제38조제1항"), found)
        self.assertIn(vda._normalize_citation("[별표8]"), found)
        self.assertTrue(any("제48조제9호" in f for f in found))

    def test_hangul_particle_boundary_extracted(self) -> None:
        # 조사가 공백 없이 붙은 조항("제38조를")도 추출돼야 D2 근거대조가 걸린다(WL D3 교훈과 동형).
        found = {vda._normalize_citation(t) for t in vda.extract_citations("약사법 제38조를 위반")}
        self.assertIn(vda._normalize_citation("약사법 제38조"), found)

    def test_standalone_ho_and_hang_extracted(self) -> None:
        # Codex 차단1: `조` 없이 단독으로 온 제N호/제N항도 추출돼야 근거대조가 걸린다.
        found = {vda._normalize_citation(t) for t in vda.extract_citations("근거 제999호 및 제12항 위반")}
        self.assertIn("제999호", found)
        self.assertIn("제12항", found)

    def test_corner_bracket_law_normalized_equal(self) -> None:
        # Codex 차단2: 「」 브래킷 유무만 다른 표기는 같은 토큰으로 정규화(과탐 방지).
        self.assertEqual(vda._normalize_citation("「약사법」 제38조제1항"),
                         vda._normalize_citation("약사법 제38조제1항"))

    def test_bracketed_law_extracted_as_full_token(self) -> None:
        # Codex 2차: 「화장품법」 제38조제1항 은 법령명 뒤 `」`가 `제`를 막아 bare `제38조제1항`
        # 만 추출되던 우회 — 이제 법령명까지 한 토큰으로 뽑혀야 교차오인용 대조가 성립한다.
        toks = {vda._normalize_citation(t) for t in vda.extract_citations("「화장품법」 제38조제1항")}
        self.assertIn(vda._normalize_citation("화장품법 제38조제1항"), toks)
        self.assertNotIn("제38조제1항", toks)   # bare 만 남지 않는다(긴 매칭 dedup)


class AdminGateTest(unittest.TestCase):
    def test_admin_good_passes_gate_autodetect(self) -> None:
        # card_type 미전달 → disposition_basis 키로 admin 스키마 자동판별.
        result = vda.run_deep_analysis_gate(_GOOD_ADMIN_DA, _ADMIN_SOURCE)
        self.assertTrue(result.ok, result.report)
        self.assertEqual(result.fail_count, 0)

    def test_admin_missing_disposition_basis_fails_d1(self) -> None:
        da = dict(_GOOD_ADMIN_DA)
        del da["disposition_basis"]
        result = vda.run_deep_analysis_gate(da, _ADMIN_SOURCE, card_type="admin-action")
        self.assertFalse(result.ok)
        self.assertTrue(any(f.code == "D1-SECTION-INCOMPLETE"
                            and "disposition_basis" in f.detail for f in result.findings))

    def test_admin_fabricated_law_blocks_merge(self) -> None:
        # 원문은 약사법인데 화장품법 제99조(원문 부재)로 오인용/날조 → 교차오인용 D2 FAIL.
        da = dict(_GOOD_ADMIN_DA)
        da["key_violations"] = list(da["key_violations"]) + [
            {"citation": "화장품법 제99조", "description": "원문에 없는 법령을 근거로 든 날조 서술",
             "risk": "-"}]
        result = vda.run_deep_analysis_gate(da, _ADMIN_SOURCE)
        self.assertFalse(result.ok)
        self.assertTrue(any("화장품법 제99조" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_admin_fabricated_bare_article_with_particle_blocks(self) -> None:
        # 조사 직결 날조 조항("제77조를")이 산문에 섞여도 추출·차단(우회 방지).
        da = dict(_GOOD_ADMIN_DA)
        da["administrative_risks"] += " 원문에 없는 제77조를 근거로 든 날조."
        result = vda.run_deep_analysis_gate(da, _ADMIN_SOURCE)
        self.assertFalse(result.ok)
        self.assertTrue(any("제77조" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_wl_unaffected_by_admin_extension(self) -> None:
        # 회귀(Codex 차단3c): WL 산출물은 card_type 없이도 여전히 WL 스키마로 PASS·불변
        # (fda_evaluation 자리 유지, 한국법령/브래킷 패치가 영문 WL 경로에 영향 없음).
        result = vda.run_deep_analysis_gate(_GOOD_DEEP_ANALYSIS, _SOURCE)
        self.assertTrue(result.ok)
        self.assertEqual(result.fail_count, 0)
        self.assertEqual(vda.check_citation_grounding(_GOOD_DEEP_ANALYSIS, _SOURCE), [])

    def test_fabricated_standalone_ho_blocks_gate(self) -> None:
        # Codex 차단3a: 산문에 `조` 없이 단독으로 심은 날조 `제999호`(원문 부재) → D2 FAIL.
        da = dict(_GOOD_ADMIN_DA)
        da["administrative_risks"] += " 원문에 없는 제999호를 근거로 든 날조 서술이다."
        result = vda.run_deep_analysis_gate(da, _ADMIN_SOURCE)
        self.assertFalse(result.ok)
        self.assertTrue(any("제999호" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_corner_bracket_source_grounds_plain_citation(self) -> None:
        # Codex 차단3b: 원문이 「」 브래킷 법령명(「약사법」 제38조제1항)이어도 정상 인용
        # (약사법 제38조제1항)이 과탐 없이 PASS.
        source = ("제조기록서 거짓작성으로 「약사법」 제38조제1항을 위반함. "
                  "처분: 제조업무정지 1개월. 근거 「의약품 등의 안전에 관한 규칙」 제48조제9호, [별표8].")
        da = dict(_GOOD_ADMIN_DA)
        da["key_violations"] = [
            {"citation": "약사법 제38조제1항", "description": "제조기록서를 사실과 다르게 작성",
             "risk": "데이터 무결성 훼손 위험"},
            {"citation": "의약품 등의 안전에 관한 규칙 제48조제9호",
             "description": "행정처분 기준 위반", "risk": "품질 시스템 결함"}]
        da["disposition_basis"] = "[별표8] 행정처분 기준에 따라 제조업무정지 1개월이 부과되었다."
        result = vda.run_deep_analysis_gate(da, source)
        self.assertTrue(result.ok, result.report)   # 브래킷 차이만으로 FAIL 나지 않아야 함

    def _admin_da(self, citation: str) -> dict:
        return {
            "key_violations": [{"citation": citation,
                                "description": "제조기록서를 사실과 다르게 작성 위반",
                                "risk": "데이터 무결성 훼손 위험"}],
            "disposition_basis": "[별표8] 행정처분 기준에 따라 제조업무정지 1개월이 부과되었다.",
            "required_remediation": {"deadline": "처분 통지 후 90일 이내 이의신청",
                                     "items": ["과징금 납부 및 CAPA 재수행"]},
            "administrative_risks": "재위반 시 가중처분 및 품목허가 취소로 이어질 수 있다."}

    def test_bracketed_cross_law_fabrication_blocks(self) -> None:
        # Codex 2차(핵심): 원문이 「약사법」 인데 산출물이 「화장품법」(브래킷 법령명)으로 오인용 →
        # 교차오인용 D2 FAIL 이어야 한다(예전엔 bare `제38조제1항`만 추출돼 통째로 우회·PASS).
        src = "「약사법」 제38조제1항 위반. [별표8] 행정처분 기준."
        result = vda.run_deep_analysis_gate(self._admin_da("「화장품법」 제38조제1항"), src)
        self.assertFalse(result.ok)
        self.assertTrue(any("화장품법" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_bracketed_law_grounds_bracketed_source(self) -> None:
        # 짝: 같은 법(「약사법」)이면 브래킷 유무 무관하게 PASS(과탐 없음).
        src = "「약사법」 제38조제1항 위반. [별표8] 행정처분 기준."
        self.assertTrue(vda.run_deep_analysis_gate(self._admin_da("「약사법」 제38조제1항"), src).ok)
        self.assertTrue(vda.run_deep_analysis_gate(self._admin_da("약사법 제38조제1항"), src).ok)


if __name__ == "__main__":
    unittest.main()
