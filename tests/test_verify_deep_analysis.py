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

    def test_halfwidth_corner_bracket_source_grounds_citation(self) -> None:
        # [2026-08-03 실측] 식약처 원문은 전각 「」(U+300C/300D)와 반각 ｢｣(U+FF62/FF63)를
        # 문서마다 섞어 쓴다. 반각 원문에서 조항이 실재하는데도 D2 가 날조로 FAIL 하던 오탐
        # (admin-2026005914, FAIL 3건)의 회귀 방지. 전각 짝과 판정이 같아야 한다.
        src = "｢약사법｣ 제38조제1항 위반. [별표8] 행정처분 기준."
        self.assertTrue(vda.run_deep_analysis_gate(self._admin_da("약사법 제38조제1항"), src).ok)
        self.assertTrue(vda.run_deep_analysis_gate(self._admin_da("｢약사법｣ 제38조제1항"), src).ok)
        self.assertTrue(vda.run_deep_analysis_gate(self._admin_da("「약사법」 제38조제1항"), src).ok)

    def test_halfwidth_bracket_cross_law_fabrication_still_blocks(self) -> None:
        # 반각을 흡수해도 **교차 오인용 차단력은 유지**돼야 한다(정규화가 탐지를 죽이지 않음).
        src = "｢약사법｣ 제38조제1항 위반. [별표8] 행정처분 기준."
        result = vda.run_deep_analysis_gate(self._admin_da("｢화장품법｣ 제38조제1항"), src)
        self.assertFalse(result.ok)
        self.assertTrue(any("화장품법" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))


# ── [FDA 483 분석층 2026-07-02] FDA 483 스키마 + D2 해석성 인용 WARN(비차단) ──────────────
# 483 원문 = 실사 관찰사항 목록(영문). CFR 조항을 명시하지 않는 게 보통 — 분석가가 붙인 CFR 해석
# 인용은 원문에 없어도 WARN(비차단). 날조 식별번호(FEI 등)는 D3 가 여전히 WARN 으로 잡는다.
_FDA483_SOURCE = (
    "During an inspection of your firm, we documented the following observations. "
    "OBSERVATION 1: There is a failure to thoroughly review unexplained discrepancies. "
    "Your firm invalidated out-of-specification (OOS) results without scientific justification. "
    "OBSERVATION 2: Aseptic processing areas were not adequately monitored for microbial "
    "contamination during production, and environmental monitoring records were incomplete."
)

_GOOD_FDA483_DA = {
    "key_violations": [
        {"citation": "21 CFR 211.192",
         "description": "규격초과(OOS) 결과를 과학적 근거 없이 무효화하고 불일치 조사를 문서화하지 않음",
         "risk": "불량 배치가 시장에 유통될 위험"},
        {"citation": "21 CFR 211.42(c)",
         "description": "무균 공정 구역의 미생물 오염 모니터링·기록이 미흡함",
         "risk": "무균 제품 오염으로 인한 환자 안전 위험"},
    ],
    "inspectional_significance": (
        "본 483 은 데이터 무결성과 무균 관리의 systemic 결함을 지적하며, 회사의 응답이 미흡할 경우 "
        "Warning Letter 또는 해외 제조소의 경우 Import Alert 로 승격될 가능성이 있다."),
    "required_remediation": {
        "deadline": "483 수령 후 15영업일 이내 FDA 에 서면 회신",
        "items": ["OOS 조사 절차를 재수립하고 소급 검토를 수행",
                  "환경 모니터링 프로그램을 강화하고 CAPA 를 문서화"],
    },
    "administrative_risks": "미시정 시 Warning Letter·Import Alert·OAI 분류로 이어질 수 있다.",
}


class Fda483ResolveTest(unittest.TestCase):
    def test_card_type_fda483(self) -> None:
        self.assertEqual(vda.resolve_required_sections(card_type="fda-483"),
                         vda.REQUIRED_SECTIONS_FDA483)
        self.assertEqual(vda.resolve_required_sections(card_type="FDA 483"),
                         vda.REQUIRED_SECTIONS_FDA483)

    def test_autodetect_by_inspectional_significance(self) -> None:
        self.assertEqual(vda.resolve_required_sections(_GOOD_FDA483_DA),
                         vda.REQUIRED_SECTIONS_FDA483)

    def test_admin_and_wl_autodetect_unaffected(self) -> None:
        # 483 판별 추가가 admin/WL 자동판별을 흔들지 않는다(fda_evaluation·disposition_basis 우선).
        self.assertEqual(vda.resolve_required_sections(_GOOD_ADMIN_DA),
                         vda.REQUIRED_SECTIONS_ADMIN)
        self.assertEqual(vda.resolve_required_sections(_GOOD_DEEP_ANALYSIS),
                         vda.REQUIRED_SECTIONS)


class Fda483GateTest(unittest.TestCase):
    def test_good_483_passes_gate_by_card_type(self) -> None:
        # CFR 인용이 원문(관찰사항)에 없어도 483 은 D2 WARN(비차단) → FAIL 0, gate PASS.
        result = vda.run_deep_analysis_gate(_GOOD_FDA483_DA, _FDA483_SOURCE, card_type="fda-483")
        self.assertTrue(result.ok, result.report)
        self.assertEqual(result.fail_count, 0)

    def test_good_483_passes_gate_by_autodetect(self) -> None:
        # card_type 미전달이어도 inspectional_significance 키로 483 판별 → 동일하게 PASS.
        result = vda.run_deep_analysis_gate(_GOOD_FDA483_DA, _FDA483_SOURCE)
        self.assertTrue(result.ok, result.report)

    def test_ungrounded_cfr_is_warn_not_fail(self) -> None:
        result = vda.run_deep_analysis_gate(_GOOD_FDA483_DA, _FDA483_SOURCE, card_type="fda-483")
        self.assertEqual(result.fail_count, 0)
        self.assertGreaterEqual(result.warn_count, 1)   # CFR 인용은 WARN 으로 남음
        warns = [f for f in result.findings if f.code == "D2-CITATION-UNGROUNDED"]
        self.assertTrue(warns and all(f.severity == vda.SEV_WARN for f in warns))

    def test_missing_inspectional_significance_fails_d1(self) -> None:
        da = dict(_GOOD_FDA483_DA)
        del da["inspectional_significance"]
        result = vda.run_deep_analysis_gate(da, _FDA483_SOURCE, card_type="fda-483")
        self.assertFalse(result.ok)
        self.assertTrue(any(f.code == "D1-SECTION-INCOMPLETE"
                            and "inspectional_significance" in f.detail for f in result.findings))

    def test_fabricated_fei_number_still_warns_d3(self) -> None:
        da = {**_GOOD_FDA483_DA,
              "administrative_risks": _GOOD_FDA483_DA["administrative_risks"] + " FEI 30441955는 원문에 없다."}
        result = vda.run_deep_analysis_gate(da, _FDA483_SOURCE, card_type="fda-483")
        self.assertTrue(result.ok)   # D3 는 비차단
        self.assertTrue(any(f.code == "D3-NUMBER-UNVERIFIED" and "30441955" in f.detail
                            for f in result.findings))

    def test_wl_citation_still_hard_fails(self) -> None:
        # 회귀(격리): 483 의 D2 WARN 강등이 WL 경로로 새면 안 된다 — WL 은 여전히 하드 FAIL.
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = list(da["key_violations"]) + [
            {"citation": "21 CFR 610.13", "description": "원문에 없는 조항", "risk": "-"}]
        result = vda.run_deep_analysis_gate(da, _SOURCE, card_type="warning-letter")
        self.assertFalse(result.ok)
        self.assertTrue(any(f.code == "D2-CITATION-UNGROUNDED" and f.severity == vda.SEV_FAIL
                            for f in result.findings))


# ── [FDA 483 원문절단 결함 2026-07-13] D5a/D5b — 원문·국문 병기 정합성 ──────────────────
_483_TRUNC_SOURCE = (
    "OBSERVATION 1: Your firm failed to adequately investigate an unexplained "
    "discrepancy in a batch that failed to meet its specifications before releasing "
    "the batch for follow-up. Specifically, A. Extrinsic biological particulates "
    "(mammalian hair) were identified in Lot 12345 during microscopic examination."
)

_483_TRUNC_ORIGINAL_DEFICIENCY_ONLY = (
    "Your firm failed to adequately investigate an unexplained discrepancy in a "
    "batch that failed to meet its specifications before releasing the batch for "
    "follow-up."
)

_483_TRUNC_ORIGINAL_FULL = (
    _483_TRUNC_ORIGINAL_DEFICIENCY_ONLY + " Specifically, A. Extrinsic biological "
    "particulates (mammalian hair) were identified in Lot 12345 during microscopic "
    "examination."
)


def _make_483_da(original: str) -> dict:
    return {
        "key_violations": [
            {"citation": "21 CFR 211.192", "original": original,
             "observation": "예기치 못한 불일치 조사가 미흡했으며, 구체적으로 mammalian hair 등 "
                           "이물이 로트 12345 에서 검출됨",
             "risk": "불량 배치가 시장에 유통될 위험"},
        ],
        "inspectional_significance": (
            "본 483 은 데이터 무결성 결함을 지적하며, 회사의 응답이 미흡할 경우 Warning Letter 로 "
            "승격될 가능성이 있다."),
        "required_remediation": {
            "deadline": "483 수령 후 15영업일 이내 FDA 에 서면 회신",
            "items": ["불일치 조사 절차를 재수립하고 소급 검토를 수행",
                      "이물 관리 프로그램을 강화하고 CAPA 를 문서화"],
        },
        "administrative_risks": "미시정 시 Warning Letter·Import Alert 로 이어질 수 있다.",
    }


class Fda483TruncationTest(unittest.TestCase):
    def test_deficiency_only_original_before_specifically_fails(self) -> None:
        # 1) original 이 결함 문장만 발췌하고 뒤이은 "Specifically, ..." 상세를 잘라냄 → D5b FAIL.
        da = _make_483_da(_483_TRUNC_ORIGINAL_DEFICIENCY_ONLY)
        result = vda.run_deep_analysis_gate(da, _483_TRUNC_SOURCE, card_type="fda-483")
        self.assertFalse(result.ok)
        fails = [f for f in result.findings if f.severity == vda.SEV_FAIL]
        self.assertTrue(any(f.code == "D5-483-ORIGINAL-TRUNCATED" for f in fails))

    def test_full_original_including_specifically_passes(self) -> None:
        # 2) original 이 결함 + "Specifically…" 상세 전체를 포함 → D5b 미발생, gate PASS.
        da = _make_483_da(_483_TRUNC_ORIGINAL_FULL)
        result = vda.run_deep_analysis_gate(da, _483_TRUNC_SOURCE, card_type="fda-483")
        self.assertFalse(any(f.code == "D5-483-ORIGINAL-TRUNCATED" for f in result.findings))
        self.assertTrue(result.ok, result.report)

    def test_original_not_in_source_no_d5b(self) -> None:
        # 5) original 이 source_text 안에서 아예 발견 안 되면 D5b 는 관여하지 않음(D4 영역).
        da = _make_483_da("This exact phrase does not appear anywhere in the source text.")
        result = vda.run_deep_analysis_gate(da, _483_TRUNC_SOURCE, card_type="fda-483")
        self.assertFalse(any(f.code == "D5-483-ORIGINAL-TRUNCATED" for f in result.findings))
        # D4 (원문 병기 미근거) 는 WARN 으로 남아야 함 — D5b 만 관여하지 않는다는 뜻.
        self.assertTrue(any(f.code == "D4-ORIGINAL-UNGROUNDED" for f in result.findings))


class KoreanSpecificGroundingTest(unittest.TestCase):
    def test_ungrounded_latin_specific_warns_not_blocks(self) -> None:
        # 3) WL 카드: 국문 해석에 원문(original) 밖 라틴 단어("Alternaria")가 등장 → D5a WARN,
        # 그러나 WARN 은 비차단이라 gate 는 여전히 ok True.
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = [
            {"citation": "21 CFR 211.192",
             "original": "failure to thoroughly investigate unexplained discrepancies",
             "description": "예기치 못한 불일치 조사 부실(Alternaria 균 오염 포함)",
             "risk": "재발 방지 실패로 불량 제품 유통 위험"},
        ] + list(da["key_violations"][1:])
        result = vda.run_deep_analysis_gate(da, _SOURCE)
        warns = [f for f in result.findings if f.code == "D5-KO-SPECIFIC-UNGROUNDED"]
        self.assertTrue(any("Alternaria" in f.detail for f in warns))
        self.assertTrue(result.ok, result.report)

    def test_allowlisted_acronyms_no_warning(self) -> None:
        # 4) 흔한 규제 약어(HEPA·ISO)는 원문 밖에 있어도 D5a 미발생(allowlist).
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = [
            {"citation": "21 CFR 211.192",
             "original": "failure to thoroughly investigate unexplained discrepancies",
             "description": "예기치 못한 불일치 조사 부실(HEPA 필터 및 ISO 등급 관련 기록 포함)",
             "risk": "재발 방지 실패로 불량 제품 유통 위험"},
        ] + list(da["key_violations"][1:])
        result = vda.run_deep_analysis_gate(da, _SOURCE)
        self.assertFalse(any(f.code == "D5-KO-SPECIFIC-UNGROUNDED" for f in result.findings))
        self.assertTrue(result.ok, result.report)


# ── [인용 어순 관용 2026-08-10] D2 가 어순 종속이라 실재 인용을 날조로 차단하던 과차단 ────────
# 실사고: 2026-08-10 WL 카드 5f27c3276af4 가 D2 FAIL 4건으로 deep 델타에서 drop 됐다. 아래
# 원문은 그 카드의 실제 문장 구조(영어 자연 어순: `section N of the FD&C Act`)를 옮긴 것이고,
# 산출물은 `FD&C Act N` 어순으로 인용한다 — 같은 조항인데 옛 게이트는 FAIL 4건을 냈다.
_WL_ORDER_SOURCE = (
    "Your syNeo products are unapproved new drugs because they are new drugs within "
    "the meaning of section 201(p) of the FD&C Act. Introducing such products into "
    "interstate commerce violates sections 505(a) and 301(d) of the FD&C Act. "
    "Marketing this misbranded product violates section 301(a). Products offered for "
    "import into the United States under section 801(a)(3) may be refused admission. "
    "Within 15 working days of receipt of this letter, respond in writing."
)


def _wl_order_da(citations: list, extra_admin: str = "") -> dict:
    """어순 시험용 WL 산출물 — 인용만 갈아끼운다(나머지 4섹션 구조는 고정·D1 통과)."""
    return {
        "key_violations": [
            {"citation": c, "description": "모노그래프가 허용하지 않는 유효성분 조합 사용",
             "risk": "무허가 신약에 해당해 수입 거부·집행 위험"} for c in citations],
        "fda_evaluation": "FDA 는 회사의 회신이 근본 원인을 다루지 않았다고 평가했다.",
        "required_remediation": {
            "deadline": "15영업일 이내 서면 회신",
            "items": ["문제 제품의 유통을 중단하고 처방 조합의 모노그래프 적합성을 재검토"]},
        "administrative_risks": (
            "미이행 시 압류·금지명령 등 법적 조치가 뒤따를 수 있다. " + extra_admin),
    }


class CitationWordOrderTest(unittest.TestCase):
    def test_act_first_citation_grounded_by_section_first_source(self) -> None:
        # 회귀(본 사고): 산출물 `FD&C Act 201(p)` ↔ 원문 `section 201(p) of the FD&C Act`.
        da = _wl_order_da(["FD&C Act 201(p)", "FD&C Act 505(a)", "FD&C Act 301(a)"],
                          extra_admin="수입 시 FD&C Act 801(a)(3) 에 따라 거부될 수 있다.")
        result = vda.run_deep_analysis_gate(da, _WL_ORDER_SOURCE)
        self.assertTrue(result.ok, result.report)
        self.assertEqual([f for f in result.findings
                          if f.code == "D2-CITATION-UNGROUNDED"], [])

    def test_section_first_citation_grounded_by_act_first_source(self) -> None:
        # 반대 어순도 같은 판정이어야 한다(대칭) — 원문이 `FD&C Act 201(p)`, 산출물이
        # `section 201(p) of the FD&C Act`. 옛 게이트는 이쪽도 FAIL 이었다.
        source = ("This letter cites FD&C Act 201(p) and FD&C Act 505(a). "
                  "Respond within 15 working days of receipt of this letter.")
        da = _wl_order_da(["section 201(p) of the FD&C Act", "§ 505(a)"])
        result = vda.run_deep_analysis_gate(da, source)
        self.assertTrue(result.ok, result.report)

    def test_fabricated_section_still_fails(self) -> None:
        # ★불가침: 어순을 흡수해도 **원문에 없는 섹션 번호는 여전히 하드 FAIL**(날조 차단).
        da = _wl_order_da(["FD&C Act 201(p)", "FD&C Act 999(z)"])
        result = vda.run_deep_analysis_gate(da, _WL_ORDER_SOURCE)
        self.assertFalse(result.ok)
        fails = [f for f in result.findings if f.severity == vda.SEV_FAIL]
        self.assertTrue(any(f.code == "D2-CITATION-UNGROUNDED" and "999(z)" in f.detail
                            for f in fails))
        self.assertFalse(any("201(p)" in f.detail for f in fails))  # 실재 인용은 통과

    def test_fabricated_subsection_of_real_section_still_fails(self) -> None:
        # 더 얇은 날조: 섹션 번호는 원문에 있으나(505) 하위항이 다른 것(505(z))도 FAIL.
        da = _wl_order_da(["FD&C Act 505(z)"])
        result = vda.run_deep_analysis_gate(da, _WL_ORDER_SOURCE)
        self.assertFalse(result.ok)
        self.assertTrue(any("505(z)" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_act_name_absent_from_source_still_fails(self) -> None:
        # ★교차 오인용 차단: 섹션 번호는 원문에 있지만 원문이 FD&C Act 를 전혀 언급하지 않으면
        # `FD&C Act 351(a)` 주장은 근거가 없다 — 번호만 보고 통과시키지 않는다.
        source = ("Your firm violated section 351(a) of the Public Health Service Act "
                  "during the inspection of your licensed facility this year.")
        da = _wl_order_da(["FD&C Act 351(a)"])
        result = vda.run_deep_analysis_gate(da, source)
        self.assertFalse(result.ok)
        self.assertTrue(any("법률명" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_longer_number_does_not_ground_shorter_citation(self) -> None:
        # 숫자 경계: 원문의 `1201(p)` 가 날조 `201(p)` 의 근거가 되면 안 된다.
        source = ("This notice concerns section 1201(p) of an unrelated statute. "
                  "Respond within 15 working days of receipt of this letter. FD&C Act.")
        da = _wl_order_da(["FD&C Act 201(p)"])
        result = vda.run_deep_analysis_gate(da, source)
        self.assertFalse(result.ok)
        self.assertTrue(any("201(p)" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_cfr_citation_matching_unchanged(self) -> None:
        # ★범위 격리: CFR 은 분해하지 않는다 — 원문에 `211.192` 와 `21 CFR` 이 따로 있어도
        # `21 CFR 610.13` 은 근거를 얻지 못하고 FAIL(통짜 대조 유지).
        da = dict(_GOOD_DEEP_ANALYSIS)
        da["key_violations"] = list(da["key_violations"]) + [
            {"citation": "21 CFR 610.13", "description": "원문에 없는 조항", "risk": "-"}]
        result = vda.run_deep_analysis_gate(da, _SOURCE)
        self.assertFalse(result.ok)
        self.assertTrue(any("610.13" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_korean_cross_law_matching_unchanged(self) -> None:
        # ★범위 격리: 한국 법령 토큰도 분해하지 않는다 — 「약사법」 원문에 「화장품법」 인용은
        # 여전히 교차 오인용 FAIL(2026-07-02 설계 §5-1 불변).
        src = "「약사법」 제38조제1항 위반. [별표8] 행정처분 기준."
        da = {
            "key_violations": [{"citation": "「화장품법」 제38조제1항",
                                "description": "제조기록서를 사실과 다르게 작성 위반",
                                "risk": "데이터 무결성 훼손 위험"}],
            "disposition_basis": "[별표8] 행정처분 기준에 따라 제조업무정지 1개월이 부과되었다.",
            "required_remediation": {"deadline": "처분 통지 후 90일 이내 이의신청",
                                     "items": ["과징금 납부 및 CAPA 재수행"]},
            "administrative_risks": "재위반 시 가중처분 및 품목허가 취소로 이어질 수 있다."}
        result = vda.run_deep_analysis_gate(da, src)
        self.assertFalse(result.ok)
        self.assertTrue(any("화장품법" in f.detail for f in result.findings
                            if f.severity == vda.SEV_FAIL))

    def test_long_form_act_name_in_source_grounds_citation(self) -> None:
        # 원문이 법률명을 풀어 쓴 경우(Federal Food, Drug, and Cosmetic Act)도 같은 법이다.
        source = ("These are new drugs within the meaning of section 201(p) of the "
                  "Federal Food, Drug, and Cosmetic Act. Respond within 15 working days.")
        result = vda.run_deep_analysis_gate(_wl_order_da(["FD&C Act 201(p)"]), source)
        self.assertTrue(result.ok, result.report)

    def test_483_word_order_stays_warn_not_fail(self) -> None:
        # 483 경로의 D2 강등(WARN)은 그대로 — 어순 관용이 심각도를 바꾸지 않는다.
        da = dict(_GOOD_FDA483_DA)
        da["administrative_risks"] = (
            "미시정 시 FD&C Act 501(a)(2)(B) 위반으로 Warning Letter 로 이어질 수 있다.")
        result = vda.run_deep_analysis_gate(da, _FDA483_SOURCE, card_type="fda-483")
        self.assertTrue(result.ok)   # WARN 은 비차단
        self.assertTrue(any(f.code == "D2-CITATION-UNGROUNDED" and f.severity == vda.SEV_WARN
                            for f in result.findings))


class CitationSplitUnitTest(unittest.TestCase):
    def test_split_targets_only_fdc_and_section_forms(self) -> None:
        self.assertEqual(vda._split_act_section("FD&C Act 201(p)"), ("201(p)", True))
        self.assertEqual(vda._split_act_section("section 201(p)"), ("201(p)", False))
        self.assertEqual(vda._split_act_section("§502(a)"), ("502(a)", False))
        self.assertIsNone(vda._split_act_section("21 CFR 211.192"))
        self.assertIsNone(vda._split_act_section("약사법 제38조제1항"))
        self.assertIsNone(vda._split_act_section("[별표8]"))

    def test_contains_section_respects_digit_boundary(self) -> None:
        self.assertTrue(vda._contains_section("section201(p)ofthefd&cact", "201(p)"))
        self.assertFalse(vda._contains_section("section1201(p)oftheact", "201(p)"))
        self.assertFalse(vda._contains_section("21cfr211.192", "192"))
        self.assertFalse(vda._contains_section("21cfr211.192", "211"))
        self.assertTrue(vda._contains_section("fd&cact505(a)and301(d)", "505(a)"))
        # 문장을 끝내는 마침표는 조항 번호의 연장이 아니다(막으면 그게 또 다른 과차단).
        self.assertTrue(vda._contains_section("violatessection301(a).marketing", "301(a)"))


class HeadingOnlyOriginalTest(unittest.TestCase):
    """D5c — original 이 결정론 표제문 범위를 못 벗어나면 FAIL(2026-08-24 병기쌍 파손 사고).

    발행 실사고: WL 6장·19개 항목 전부가 original 로 번호 매긴 표제문 한 문장만 발췌하고
    국문 description 은 본문 단락을 요약 → 화면의 원문↔국문 해석이 서로 다른 내용이 됐는데
    D4/D5a 는 WARN(비차단)이라 그대로 발행됐다. 표제문은 위반항목 상세 블록이 이미 보여주므로
    표제문 안에 머무는 발췌는 정보 0 + 병기쌍 파손 뿐이다."""

    _STATEMENT = ("Your firm failed to conduct appropriate laboratory testing, as "
                  "necessary, for each batch of drug product required to be free of "
                  "objectionable microorganisms (21 CFR 211.165(b)).")
    _BODY = ("Specifically, you lacked appropriate incubation times, lacked appropriate "
             "method suitability, and lacked positive and negative controls.")

    def _da_with_original(self, original: str) -> dict:
        da = {k: v for k, v in _GOOD_DEEP_ANALYSIS.items()}
        kv = [dict(v) for v in da["key_violations"]]
        kv[0] = dict(kv[0], original=original)
        da["key_violations"] = kv
        return da

    def test_heading_only_original_fails(self) -> None:
        findings = vda.check_heading_only_original(
            self._da_with_original(self._STATEMENT), [self._STATEMENT])
        self.assertTrue(any(f.code == "D5C-ORIGINAL-HEADING-ONLY"
                            and f.severity == vda.SEV_FAIL for f in findings))

    def test_heading_prefix_original_fails(self) -> None:
        # 표제문의 앞부분만 잘라 발췌해도(부분문자열) 본문 근거는 여전히 0 — 같은 파손.
        prefix = self._STATEMENT[:90]
        findings = vda.check_heading_only_original(
            self._da_with_original(prefix), [self._STATEMENT])
        self.assertTrue(any(f.code == "D5C-ORIGINAL-HEADING-ONLY" for f in findings))

    def test_original_extending_into_body_passes(self) -> None:
        findings = vda.check_heading_only_original(
            self._da_with_original(self._STATEMENT + " " + self._BODY), [self._STATEMENT])
        self.assertEqual(findings, [])

    def test_no_statements_no_check(self) -> None:
        # 결정론 표제문이 없는 카드(예: "Failure to…" 서식)는 대조 기준이 없어 검사하지 않는다.
        findings = vda.check_heading_only_original(
            self._da_with_original(self._STATEMENT), None)
        self.assertEqual(findings, [])

    def test_missing_original_not_flagged(self) -> None:
        # original 생략은 프롬프트 계약상 정당한 경로(발췌할 본문이 없으면 생략) — D5c 무관.
        findings = vda.check_heading_only_original(_GOOD_DEEP_ANALYSIS, [self._STATEMENT])
        self.assertEqual(findings, [])

    def test_gate_blocks_merge_with_statements(self) -> None:
        source = _SOURCE + " " + self._STATEMENT
        da = self._da_with_original(self._STATEMENT)
        # D5d(완역 하한)와 분리해 D5c 게이팅만 검증 — description 을 원문 완역 수준으로 채운다.
        da["key_violations"][0]["description"] = (
            "귀사는 유해 미생물이 없어야 하는 의약품의 각 배치에 대해 필요한 "
            "적절한 시험실 시험을 수행하지 않았다(21 CFR 211.165(b)).")
        without = vda.run_deep_analysis_gate(da, source)
        self.assertTrue(without.ok)   # wl_statements 미전달 = D5c 무영향(additive)
        with_stmts = vda.run_deep_analysis_gate(da, source, wl_statements=[self._STATEMENT])
        self.assertFalse(with_stmts.ok)


class DescriptionCoverageTest(unittest.TestCase):
    """D5d — 국문 해석의 완역 하한(2026-08-24 2차 사고: 긴 영문 발췌 vs 두 줄 국문 요약).

    사이트의 다른 모든 원문↔국문 병기(statement_ko·NCR *_ko·WHOPIR text_ko)는 완역인데
    deep kv 쌍만 요약이라 독자에게 '번역 안 된' 화면이 됐다 — 완역 계약의 결정론 하한선."""

    _ORIGINAL = ("Your firm failed to conduct appropriate laboratory testing, as "
                 "necessary, for each batch of drug product required to be free of "
                 "objectionable microorganisms (21 CFR 211.165(b)). Specifically, you "
                 "lacked appropriate incubation times, lacked appropriate method "
                 "suitability, and lacked positive and negative controls.")

    def _da(self, description: str) -> dict:
        da = {k: v for k, v in _GOOD_DEEP_ANALYSIS.items()}
        kv = [dict(v) for v in da["key_violations"]]
        kv[0] = dict(kv[0], original=self._ORIGINAL, description=description)
        da["key_violations"] = kv
        return da

    def test_summary_description_fails(self) -> None:
        findings = vda.check_description_coverage(self._da("미생물 시험이 부적절했다."))
        self.assertTrue(any(f.code == "D5D-DESCRIPTION-UNDERTRANSLATED"
                            and f.severity == vda.SEV_FAIL for f in findings))

    def test_full_translation_passes(self) -> None:
        full = ("귀사는 유해 미생물이 없어야 하는 의약품의 각 배치에 대해 필요한 적절한 "
                "시험실 시험을 수행하지 않았다(21 CFR 211.165(b)). 구체적으로 적절한 "
                "배양시간이 없었고, 적절한 시험법 적합성이 없었으며, 양성·음성 대조도 없었다.")
        self.assertEqual(vda.check_description_coverage(self._da(full)), [])

    def test_no_original_skipped(self) -> None:
        # original 미보유 kv(국문 단독 발행)는 완역 대조 대상이 아니다.
        self.assertEqual(vda.check_description_coverage(_GOOD_DEEP_ANALYSIS), [])

    def test_admin_sections_not_checked_by_gate(self) -> None:
        # 처분문(한국어 원문) 카드는 원문을 그대로 읽을 수 있어 요약이 정당 — WL 섹션 집합이
        # 아닐 때는 게이트가 D5d 를 부르지 않는다.
        da = self._da("요약 한 줄.")
        da["disposition_basis"] = da.pop("fda_evaluation")
        source = _SOURCE + " " + self._ORIGINAL
        result = vda.run_deep_analysis_gate(da, source)
        self.assertTrue(all(f.code != "D5D-DESCRIPTION-UNDERTRANSLATED"
                            for f in result.findings))


if __name__ == "__main__":
    unittest.main()
