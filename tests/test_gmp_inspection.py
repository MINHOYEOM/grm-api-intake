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

    def test_c4_encrypted_pdf_labeled_pdf_encrypted(self):
        """암호화 PDF → 'pdf-encrypted' 진단 (scan-no-text/parse-fail 오라벨 정정, C4).

        fitz(PyMuPDF) 를 sys.modules 스텁으로 대체 — 무의존·무파일.
        """
        import sys as _sys

        class _FakeDoc:
            def __init__(self, needs_pass, is_encrypted):
                self.needs_pass = needs_pass
                self.is_encrypted = is_encrypted

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):                      # 잠긴 문서는 page 순회 안 됨
                raise AssertionError("encrypted doc must not be iterated")

        class _FakeFitz:
            def __init__(self, doc):
                self._doc = doc

            def open(self, **kwargs):
                return self._doc

        saved = _sys.modules.get("fitz")
        try:
            for needs_pass, is_enc in ((True, True), (True, False), (False, True)):
                with self.subTest(needs_pass=needs_pass, is_encrypted=is_enc):
                    _sys.modules["fitz"] = _FakeFitz(_FakeDoc(needs_pass, is_enc))
                    text, status = g._extract_pdf_text(b"%PDF-1.7 fake")
                    self.assertEqual(text, "")
                    self.assertEqual(status, "pdf-encrypted")
        finally:
            if saved is not None:
                _sys.modules["fitz"] = saved
            else:
                _sys.modules.pop("fitz", None)

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


# ── [상세보기 결정론 승격 2026-07-02 · spec §16] 지적 표 결정론 추출 회귀 ──────────
def _has_fitz() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except ImportError:
        return False


def _build_pdf(title: str, table_rows=None, extra_text: str = "") -> bytes:
    """지적 표 회귀용 합성 PDF. 내장 CJK 폰트 'korea' 사용 — 외부 폰트 불요·CI 이식성.

    table_rows=None → 표 없는 문서(사전평가/적합). 리스트면 5컬럼 ruled 표를 그린다
    (find_tables 는 벡터 선 격자를 결정론으로 인식 — 실측 PDF 와 동형 구조).
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    kw = dict(fontname="korea")
    page.insert_text((50, 50), title, fontsize=11, **kw)
    if extra_text:
        page.insert_text((50, 78), extra_text, fontsize=10, **kw)
    if table_rows:
        header = ["분야", "구분", "근거 법령", "지적(보완)사항 요약", "비고"]
        cols_x = [40, 100, 150, 275, 470, 555]
        rows_y = [110 + 34 * i for i in range(len(table_rows) + 2)]
        for x in cols_x:
            page.draw_line((x, rows_y[0]), (x, rows_y[-1]))
        for y in rows_y:
            page.draw_line((cols_x[0], y), (cols_x[-1], y))
        for r, row in enumerate([header] + table_rows):
            for c, cell in enumerate(row):
                page.insert_text((cols_x[c] + 2, rows_y[r] + 18), cell, fontsize=7, **kw)
    return doc.tobytes()


class _FlagCtx:
    """ENABLE_GMP_DEFICIENCY_TABLE 를 임시로 설정/복원(테스트 격리)."""
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        import os
        self._saved = os.environ.get("ENABLE_GMP_DEFICIENCY_TABLE")
        if self.value is None:
            os.environ.pop("ENABLE_GMP_DEFICIENCY_TABLE", None)
        else:
            os.environ["ENABLE_GMP_DEFICIENCY_TABLE"] = self.value
        return self

    def __exit__(self, *exc):
        import os
        if self._saved is None:
            os.environ.pop("ENABLE_GMP_DEFICIENCY_TABLE", None)
        else:
            os.environ["ENABLE_GMP_DEFICIENCY_TABLE"] = self._saved
        return False


class TestInspectionTypeDetection(unittest.TestCase):
    def test_periodic(self):
        self.assertEqual(
            g._detect_inspection_type("의약품 제조소 GMP 정기실태조사(정기실사) 결과"),
            "periodic")

    def test_pre_market(self):
        self.assertEqual(
            g._detect_inspection_type("의약품 사전 GMP 평가 실태조사 결과 실사 결과: 적합"),
            "pre_market")

    def test_unknown(self):
        self.assertEqual(g._detect_inspection_type("무관한 공지문"), "unknown")
        self.assertEqual(g._detect_inspection_type(""), "unknown")

    def test_pre_market_wins_when_both_present(self):
        # 사전평가 문서에 '정기실태조사' 참조가 섞여도 pre_market(표 미추출=안전 쪽).
        self.assertEqual(
            g._detect_inspection_type("사전 GMP 평가 결과 — 정기실태조사 규정 준용"),
            "pre_market")


class TestNormalizeDeficiencyTable(unittest.TestCase):
    _HEADER = ["분야", "구분", "근거 법령", "지적(보완)사항 요약", "비고"]

    def test_maps_columns_by_header_token(self):
        rows = [self._HEADER,
                ["시설장비", "기타", "[별표1] 2.1호", "교차오염 방지", "이행 인정"]]
        self.assertEqual(g._normalize_deficiency_table(rows), [{
            "area": "시설장비", "severity": "기타", "legal_basis": "[별표1] 2.1호",
            "summary": "교차오염 방지", "followup": "이행 인정"}])

    def test_skips_rows_without_legal_or_summary(self):
        rows = [self._HEADER,
                ["", "", "", "", ""],                     # 빈행
                ["구분줄", "", "", "", "비고만"],           # 근거·지적 없음 → 주석/구분줄 제외
                ["제조", "중요", "[별표1] 6호", "밸리데이션", "행정처분"]]
        out = g._normalize_deficiency_table(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["area"], "제조")

    def test_returns_empty_without_deficiency_header(self):
        # 제조소 현황 표(분야/근거/지적 헤더 부재) → 지적 표 아님 → [].
        rows = [["구분", "내용"], ["제조소명", "테스트제약"]]
        self.assertEqual(g._normalize_deficiency_table(rows), [])

    def test_cleans_newlines_and_whitespace(self):
        rows = [self._HEADER,
                ["제조", "기타", "[별표1]\n6.1호", "밸리데이션\n 실시  할 것", "이행"]]
        out = g._normalize_deficiency_table(rows)
        self.assertEqual(out[0]["legal_basis"], "[별표1] 6.1호")
        self.assertEqual(out[0]["summary"], "밸리데이션 실시 할 것")

    def test_handles_none_cells(self):
        rows = [self._HEADER, ["제조", None, "[별표1] 6호", None, None]]
        self.assertEqual(g._normalize_deficiency_table(rows), [{
            "area": "제조", "severity": "", "legal_basis": "[별표1] 6호",
            "summary": "", "followup": ""}])

    def test_repeated_header_row_skipped(self):
        rows = [self._HEADER, self._HEADER,
                ["제조", "기타", "[별표1] 6호", "밸리데이션", "이행"]]
        self.assertEqual(len(g._normalize_deficiency_table(rows)), 1)

    def test_empty_input(self):
        self.assertEqual(g._normalize_deficiency_table([]), [])


@unittest.skipUnless(_has_fitz(), "PyMuPDF(fitz) 필요")
class TestExtractDeficiencyTablePDF(unittest.TestCase):
    def test_extracts_rows_from_ruled_table(self):
        data = _build_pdf(
            "의약품 제조소 GMP 정기실태조사(정기실사) 결과",
            [["시설장비", "기타", "[별표1] 2.1호", "교차오염 방지 시설", "이행 인정"],
             ["제조", "중요", "[별표1] 6.1호", "밸리데이션 실시", "행정처분 예정"]])
        rows = g._extract_deficiency_table(data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["area"], "시설장비")
        self.assertEqual(rows[0]["legal_basis"], "[별표1] 2.1호")
        self.assertEqual(rows[1]["severity"], "중요")
        self.assertEqual(rows[1]["followup"], "행정처분 예정")

    def test_deterministic_same_bytes_same_rows(self):
        data = _build_pdf("정기실태조사",
                          [["제조", "기타", "[별표1] 6호", "밸리데이션", "이행"]])
        self.assertEqual(g._extract_deficiency_table(data),
                         g._extract_deficiency_table(data))

    def test_no_table_returns_empty(self):
        data = _build_pdf("의약품 사전 GMP 평가 실태조사 결과", None,
                          extra_text="실사 결과: 적합")
        self.assertEqual(g._extract_deficiency_table(data), [])


@unittest.skipUnless(_has_fitz(), "PyMuPDF(fitz) 필요")
class TestParseDeficiencyTableGate(unittest.TestCase):
    _PERIODIC_TITLE = "의약품 제조소 GMP 정기실태조사(정기실사) 결과"
    _ROWS = [["제조", "중요", "[별표1] 6호", "밸리데이션 실시", "행정처분 예정"]]

    def test_flag_off_no_extraction(self):
        data = _build_pdf(self._PERIODIC_TITLE, self._ROWS)
        with _FlagCtx(None):
            self.assertEqual(
                g._parse_deficiency_table(data, "pdf", self._PERIODIC_TITLE,
                                          "present", "doc1"),
                ([], ""))

    def test_enabled_periodic_extracts(self):
        data = _build_pdf(self._PERIODIC_TITLE, self._ROWS)
        with _FlagCtx("true"):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._PERIODIC_TITLE, "present", "doc1")
        self.assertEqual(status, "extracted")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["area"], "제조")

    def test_enabled_pre_market_skipped(self):
        data = _build_pdf(self._PERIODIC_TITLE, self._ROWS)  # 데이터 무관 — 유형이 우선
        with _FlagCtx("true"):
            rows, status = g._parse_deficiency_table(
                data, "pdf", "의약품 사전 GMP 평가 실태조사 결과 적합", "none", "doc2")
        self.assertEqual((rows, status), ([], "skipped-type"))

    def test_gate_degraded_when_present_but_no_table(self):
        # periodic·지적사항 present 인데 표가 안 잡히면 조용히 강등(요약카드 유지) + gate-degraded.
        data = _build_pdf(self._PERIODIC_TITLE, None)  # 표 없음
        with _FlagCtx("true"):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._PERIODIC_TITLE, "present", "doc3")
        self.assertEqual((rows, status), ([], "gate-degraded"))

    def test_empty_when_none_and_no_table(self):
        # '지적사항 없음'(none)은 표 없음이 정상 → empty(경고 없음).
        data = _build_pdf(self._PERIODIC_TITLE, None)
        with _FlagCtx("true"):
            self.assertEqual(
                g._parse_deficiency_table(data, "pdf", self._PERIODIC_TITLE,
                                          "none", "doc4"),
                ([], "empty"))

    def test_non_pdf_or_empty_text_no_extraction(self):
        with _FlagCtx("true"):
            self.assertEqual(
                g._parse_deficiency_table(b"", "hwpx", "정기실태조사", "present", "d"),
                ([], ""))
            self.assertEqual(
                g._parse_deficiency_table(b"%PDF", "pdf", "", "present", "d"),
                ([], ""))


class TestAnchorColonForm(unittest.TestCase):
    def test_colon_form_now_matched(self):
        # 실문 콜론형("평가 결과: 지적(보완)사항") — 종전 1번 앵커 MISS → 콜론 허용 수정 검증.
        text = ("- 1 - GMP 정기실태조사 결과 제조소명: 콜론제약 실사 목적: 정기 "
                "평가 결과: 지적(보완)사항 품질경영 기타 오염관리 미흡")
        ex = g._extract_deficiency_excerpt(text)
        self.assertTrue(ex.startswith("평가 결과: 지적(보완)사항"))
        self.assertNotIn("제조소명", ex)


if __name__ == "__main__":
    unittest.main()
