"""MFDS GMP 실태조사 수집기 회귀 — P6(표지 너머 지적/결론 추출).

GMP 실사 결과 PDF 는 [표지 → 제조소 현황 → 실태조사 개요 → 실태조사 결과 →
평가 결과 지적(보완)사항] 순서다. 카드 인용/요약이 표지 보일러플레이트가 아니라
실제 지적/결론을 가리키도록 _extract_deficiency_excerpt 가 결론 섹션부터 잘라낸다.
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

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

    def test_premarket_pass_verdict_is_none_not_unknown(self):
        """★2026-08-02 실측. 수입 **사전 GMP 평가** 보고서는 "지적(보완)사항" 섹션 자체가
        없고 결론이 `❍ 실사 결과: 적합` 한 줄뿐이다. 그 어법을 몰라 7건이 `unknown`
        (판정 불능)으로 적재됐다 — 원문이 "적합"이라고 명시했는데 우리가 "모르겠다"고
        기록한 것이다. 그 결과 카드 본문에서 "지적사항 판정" 줄이 통째로 빠져, 적합
        판정을 받은 실사인데 그 사실을 말하지 않는 카드가 나갔다."""
        text = (
            "- 1 - 의약품 사전 GMP 평가 실태조사 결과 1 제조소 현황 "
            "❍ 제조소명: Baxter Oncology GmbH ❍ 소 재 지: Halle/Westfalen, Germany (독일) "
            "2 실태조사 개요 ❍ 실사 방식: 현지실사 ❍ 실사 기간: 2024. 09. 23. ∼2024. 09. 27. "
            "3 실태조사 결과 ❍ 실사 대상 품목 수입업체 제품명 비고 (주)박스터 케릭스주사 "
            "무균/완제 ❍ 실사 결과: 적합")
        self.assertEqual(g._assess_deficiency(text), "none")
        self.assertEqual(g._extract_deficiency_excerpt(text), "실사 결과: 적합")

    def test_pass_verdict_never_overrides_a_real_deficiency(self):
        """★안전의 핵심. 지적사항이 실재하면 `적합` 문구가 있어도 present 를 유지한다 —
        새 분기를 `_DEFICIENCY_PRESENT_RE` **뒤**에 두어 오늘 unknown 인 문서만 바뀐다."""
        text = ("평가 결과 지적(보완)사항(Deficiencies) 3건 "
                "품질경영 부적합 사항이 확인됨. 실사 결과: 적합 판정 보류")
        self.assertEqual(g._assess_deficiency(text), "present")

    def test_bare_pass_word_is_not_enough(self):
        """★단순 문자열 `적합` 은 쓰지 않는다 — 실측상 '적합'을 포함한 문서 24건 중
        17건이 실제 지적사항을 갖고 있다(부적합·적합성·적합하지 등). `실사 결과` 앵커에
        붙은 형태만 본다."""
        for text in ("제조소는 기준에 적합하게 관리되고 있는지 확인하였다.",
                     "적합성 평가 절차를 검토하였다.",
                     "실사 결과: 부적합",
                     "실사 결과: 불적합"):
            with self.subTest(text=text[:24]):
                self.assertNotEqual(g._assess_deficiency(text), "none")

    def test_pass_anchor_tolerates_colon_and_spacing(self):
        for text in ("❍ 실사 결과: 적합", "실사결과 적합", "실사 결과 ： 적합"):
            with self.subTest(text=text):
                self.assertEqual(g._assess_deficiency(text), "none")

    def test_evaluation_result_pass_is_also_a_verdict(self):
        """★[앵커 확장 2026-08-12] `실사 결과` 는 실제로 **소수 어법**이었다.

        2026-08-02 에 이 앵커를 만들 때 모집단이 7건뿐이라 그게 전부인 줄 알았는데,
        전수 실측(08-12)에서 findings 0건 + assessment≠none 인 사전평가 87건 중
        `실사 결과: 적합` 은 7건뿐이고 **`평가 결과: 적합` 이 42건**이었다. 원문이
        "적합"이라고 명시했는데 우리가 "모르겠다"로 적어 둔 상태가 3개월 남아 있었다.
        """
        for text in ("평가 결과: 적합", "평가결과 : 적합", "❍ 평가 결과 ： 적합"):
            with self.subTest(text=text):
                self.assertEqual(g._assess_deficiency(text), "none")

    def test_evaluation_result_anchor_does_not_touch_periodic_conclusions(self):
        """★안전의 핵심 — `평가 결과` 는 periodic 결론 어법과 **접두어가 같다**.

        `적합` 이 바로 뒤에 붙는 형태만 보므로 사이에 '지적(보완)사항' 이 끼면 매칭되지
        않는다. 전수 실측 검증: 이 앵커는 findings 를 가진 251건(present 146 +
        unknown 105) 중 **0건**을 잡고 `평가 결과: 부적합` 형태도 0건이다.
        """
        periodic = ("평가 결과: 지적(보완)사항 있음 - 지적(보완)사항 분류 : 중요 3건, 기타 20건 "
                    "분야 구분 근거 법령 지적(보완)사항 요약 품질경영 중요 [별표 1] 제3.2호")
        self.assertEqual(g._assess_deficiency(periodic), "present")
        for text in ("평가 결과: 부적합", "평가 결과: 불적합", "적합성 평가 결과를 검토하였다"):
            with self.subTest(text=text):
                self.assertNotEqual(g._assess_deficiency(text), "none")

    def test_widened_present_is_a_superset_never_a_downgrade(self):
        """★회귀 불가능성을 **성질**로 잠근다 — 넓힌 present 는 옛 패턴의 상위집합이다.

        옛 `지적\\s*\\(?보완\\)?\\s*사항` 에 걸리던 문자열은 새 패턴에도 반드시 걸린다
        (`보완` 그룹 전체가 선택이 됐을 뿐). present 는 pass 앵커보다 **먼저** 판정되고
        none 은 그보다 더 먼저이므로, `present → unknown` 강등은 구조적으로 불가능하다.
        """
        import re as _re
        old = _re.compile(r"지적\s*\(?보완\)?\s*사항\s*(?:\(Deficiencies\))?")
        new = _re.compile(r"지적\s*(?:\(?\s*보완\s*\)?)?\s*사항\s*(?:\(Deficiencies\))?")
        for text in ("지적(보완)사항", "지적 (보완) 사항", "지적보완사항",
                     "지적(보완)사항(Deficiencies)", "지적 (보완)사항 있음"):
            with self.subTest(text=text):
                self.assertTrue(old.search(text))
                self.assertTrue(new.search(text), "넓힌 패턴이 옛 매칭을 잃었다(강등 위험)")
        # 판정 순서도 함께 고정 — present 가 pass 앵커보다 먼저다.
        both = "평가 결과: 적합 그리고 지적(보완)사항 2건"
        self.assertEqual(g._assess_deficiency(both), "present")

    def test_pass_anchor_tolerates_broken_bullet_glyph(self):
        """★실측 43건이 `실사 涫 결과적합` — 글머리표(❍)가 깨진 글리프로 남아 앵커를 갈랐다.

        비한글·비영숫자 3자까지만 건너뛴다. `결과 적합` 만으로 느슨하게 잡으면 2026-08-02
        에 금지한 '단순 적합' 함정으로 되돌아간다. 전수 실측: 관대형도 findings 보유
        251건 중 0건이고 회수는 58→96(/128).
        """
        for text in ("비무균/DMF 실사 涫 결과적합",
                     "무균완제 / 실사 涫 결과적합",
                     "❍ 실사 ○ 결과: 적합"):
            with self.subTest(text=text):
                self.assertEqual(g._assess_deficiency(text), "none")

    def test_conditional_pass_with_deficiencies_is_present_not_none(self):
        """★`보완적합` = 조건부 적합(지적 있음) — 통과로 읽으면 지적사항을 잃는다.

        실측: `평가결과 : 보완적합 - 지적사항 분류 : 기타 1건`. 종전엔
        ①`_INSPECTION_PASS_RE` 가 못 잡고(정상) ②`_DEFICIENCY_PRESENT_RE` 도 `보완` 을
        필수로 요구해 "지적사항"(보완 없는 표기)을 못 잡아 **unknown** 이었다.
        """
        text = ("○의약품 제조 및 품질관리 기준(GMP) 실시상황 평가결과 : 보완적합 "
                "- 지적사항 분류 : 기타 1건 분야 구분 근거법령")
        self.assertEqual(g._assess_deficiency(text), "present")
        # 앵커가 `보완적합` 을 통과로 읽지 않는다(`(?<![부불완])`).
        self.assertIsNone(g._INSPECTION_PASS_RE.search("평가결과 : 보완적합"))

    def test_evaluation_result_excerpt_does_not_shift_periodic_documents(self):
        # excerpt 앵커도 **맨 뒤**라 periodic 문서는 1번 앵커가 먼저 잡는다(기존 값 불변).
        periodic = "평가 결과: 지적(보완)사항 있음 - 분류 : 중요 1건"
        self.assertTrue(g._extract_deficiency_excerpt(periodic).startswith("평가 결과: 지적"))
        self.assertEqual(g._extract_deficiency_excerpt("2 실태조사 개요 평가 결과: 적합"),
                         "평가 결과: 적합")

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


def _build_pdf(title: str, table_rows=None, extra_text: str = "",
               redact_cells=()) -> bytes:
    """지적 표 회귀용 합성 PDF. 내장 CJK 폰트 'korea' 사용 — 외부 폰트 불요·CI 이식성.

    table_rows=None → 표 없는 문서(사전평가/적합). 리스트면 5컬럼 ruled 표를 그린다
    (find_tables 는 벡터 선 격자를 결정론으로 인식 — 실측 PDF 와 동형 구조).

    redact_cells = [(데이터행 인덱스, 컬럼 인덱스), …] — 그 칸에 **검은 가림막**을 덧그린다.
    ★핵심: 글자를 지우지 않는다. 실측 문서와 같은 구조로 **살아 있는 텍스트 위에** 채워진
    사각형을 얹을 뿐이라, 가드가 없으면 `get_text()` 가 막대 아래 글자를 그대로 돌려준다
    (그 사실 자체를 `test_defect_reintroduced_leaks_hidden_text` 가 못박는다).
    ★막대는 칸 경계에 **정확히** 맞춰 그린다 — 칸 안쪽에 그리면 그 네 변이 새 격자선으로
    보여 find_tables 가 열/행을 더 쪼갤 수 있고, 그러면 픽스처가 가드가 아니라 표 검출을
    시험하게 된다.
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
        for data_row, col in redact_cells:
            r = data_row + 1                       # 0 = 헤더행
            page.draw_rect(
                fitz.Rect(cols_x[col], rows_y[r], cols_x[col + 1], rows_y[r + 1]),
                color=(0, 0, 0), fill=(0, 0, 0),
            )
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

    def test_overseas_onsite_inspection_is_periodic(self):
        """★해외 제조소 현지실사 결과서도 국내 정기실사와 **같은 지적 표**를 싣는다.

        2026-08-05 전량 실측(626문서)에서 발견: 이 표제가 두 정규식 어디에도 안 걸려
        unknown 으로 떨어졌고, 표 추출이 **시도조차 안 된 채** skipped-type 으로 넘어갔다.
        게시판 626문서 중 398건(64%)이 skipped-type 이었고, 그중 지적이 있어야 할
        102문서가 findings 66건뿐(36문서는 0건). 표본을 열어 보니 지적 표는 전부 있었다 —
        파서가 아니라 유형 게이트가 원인이었다.
        """
        for title in (
            "의약품 해외 제조소 현지실사 결과",
            "[붙임] 의약품 해외제조소 현지실사 결과",
            "의약품 해외 제조소 현지실사(비대면 실사) 결과",
            "의약품 해외제조소 실태조사(실사) 결과",
        ):
            with self.subTest(title=title):
                self.assertEqual(g._detect_inspection_type(title), "periodic")

    def test_overseas_pre_market_still_wins(self):
        # 해외 제조소라도 사전평가 표지가 있으면 pre_market(표 없음) — 안전 쪽 유지.
        self.assertEqual(
            g._detect_inspection_type("의약품 해외 제조소 사전 GMP 평가 결과"),
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

    def test_pdf_page_count_is_recorded(self):
        """★[얇은 텍스트층 관측 2026-08-12] `pdf-ok` 는 "본문을 다 읽었다"가 아니다.

        `scan-no-text` 는 **완전히 빈** 경우만 잡아서, 본문이 스캔 이미지이고 텍스트층엔
        표지 몇 줄만 있는 PDF 도 `pdf-ok` 로 통과한다(실측 08-12: findings 0건인 128건이
        전부 `pdf-ok`·평균 517자, 정상군은 1,051~1,248자). 문서당 밀도를 사후에 재려면
        페이지 수가 필요한데 여태 기록하지 않았다. **판정은 아직 바꾸지 않는다** —
        임계를 실측 없이 세우면 멀쩡한 글자를 덮어쓰는 쪽으로 틀린다.
        """
        data = _build_pdf(self._PERIODIC_TITLE, self._ROWS)
        text, status = g._extract_pdf_text(data)
        self.assertEqual(status, "pdf-ok")               # 동작 무변경
        self.assertTrue(text)
        self.assertGreaterEqual(g._pdf_page_count(data), 1)   # 재료는 남는다

    def test_pdf_page_count_never_raises(self):
        # 관측 실패가 수집 실패가 되면 안 된다 — 손상 입력은 0 을 돌려준다.
        self.assertEqual(g._pdf_page_count(b"not a pdf"), 0)
        self.assertEqual(g._pdf_page_count(b""), 0)

    def test_shared_pdf_engine_still_returns_two_tuple(self):
        """★회귀 가드: `_extract_pdf_text` 는 483·WHO 가 공유하는 엔진이다.

        반환값을 3-튜플로 바꾸면 `collect_fda_483`(2곳)·`collect_who`(3곳)가 런타임에
        깨지는데, 그 테스트들은 이 함수를 스텁으로 갈아끼워 **CI 는 초록인 채 프로덕션만
        죽는다**. 페이지 수는 별도 `_pdf_page_count` 로 뽑는다.
        """
        import inspect as _inspect
        sig = str(_inspect.signature(g._extract_pdf_text))
        self.assertIn("tuple[str, str]", sig)
        self.assertNotIn("tuple[str, str, int]", sig)

    def test_unknown_title_is_attempted_not_skipped(self):
        """★[기본값 반전 2026-08-12] 표제를 몰라도 표 추출을 **시도**한다.

        실측(08-12): 국내 "의약품 제조소 실태조사 결과"(제목에 '정기'가 없는 형태) 8건이
        `unknown` 으로 떨어져 `skipped-type` 으로 넘어갔다 — 08-05 에 해외 현지실사 3종을
        손으로 덧붙여 고친 그 손목록이 **열흘 만에 또 낡은** 것이다. 목록을 늘리는 대신
        pre_market 만 차단하도록 기본을 뒤집었다.
        """
        title = "- 1 - 의약품 제조소 실태조사 결과 1 제조소 현황 ❍ 제조소명: 주식회사큐러블"
        self.assertEqual(g._detect_inspection_type(title), "unknown")   # 표제는 여전히 미상
        data = _build_pdf(title, self._ROWS)
        with _FlagCtx("true"):
            rows, status = g._parse_deficiency_table(data, "pdf", title, "present", "doc-unk")
        self.assertEqual(status, "extracted")       # 종전엔 ([], "skipped-type") 이었다
        self.assertEqual(len(rows), 1)

    def test_unknown_title_without_table_degrades_quietly(self):
        # 반전의 안전성: 표가 없으면 종전과 똑같이 조용히 요약카드로 강등된다(오탐 없음).
        title = "무관한 공지문"
        data = _build_pdf(title, None)
        with _FlagCtx("true"):
            rows, status = g._parse_deficiency_table(data, "pdf", title, "none", "doc-unk2")
        self.assertEqual((rows, status), ([], "empty"))

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

    def test_unsupported_format_or_empty_text_no_extraction(self):
        """비지원 포맷·본문 없음 → 추출 시도 자체를 안 한다.

        ★2026-08-27 정정 — 종전엔 이 예시가 `hwpx` 였다. hwpx 는 표가 명시 마크업이라
        추출 경로가 신설되면서 **지원 포맷이 됐다**(`_DEFICIENCY_TABLE_FORMATS`).
        읽을 경로가 없는 구형 바이너리 `hwp-ole` 로 예시를 옮긴다."""
        with _FlagCtx("true"):
            self.assertEqual(
                g._parse_deficiency_table(b"", "hwp-ole", "정기실태조사", "present", "d"),
                ([], ""))
            self.assertEqual(
                g._parse_deficiency_table(b"%PDF", "pdf", "", "present", "d"),
                ([], ""))

    def test_warn_fires_when_unknown_and_no_table(self):
        """★[침묵 사각지대 가드 2026-08-25] empty + assess=unknown 은 WARN 이 울린다.

        종전엔 WARN 이 present 조합만이라, 원문에 표가 실재하는데 파서가 0행을 낸 문서
        (실측: 서울대병원)가 판정 불능(unknown)이면 소리 없이 empty 로 흘렀다. 가드는
        로그만 더한다 — status 는 "empty" 그대로(raw_payload 등 산출물 byte 불변)."""
        data = _build_pdf(self._PERIODIC_TITLE, None)  # 표 없음
        captured: list[tuple[str, str]] = []
        with _FlagCtx("true"), mock.patch.object(
                g, "log", side_effect=lambda lvl, msg: captured.append((lvl, msg))):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._PERIODIC_TITLE, "unknown", "doc-unk-warn")
        self.assertEqual((rows, status), ([], "empty"))   # 산출 status 불변
        self.assertTrue(
            any(lvl == "WARN" and "doc-unk-warn" in msg for lvl, msg in captured),
            f"empty+unknown 조합인데 WARN 미발화: {captured}")

    def test_no_warn_when_none_and_no_table(self):
        # 비발화 대조군: '지적사항 없음'(none) + 표 없음은 정상 — 경고가 없어야 한다.
        data = _build_pdf(self._PERIODIC_TITLE, None)
        captured: list[tuple[str, str]] = []
        with _FlagCtx("true"), mock.patch.object(
                g, "log", side_effect=lambda lvl, msg: captured.append((lvl, msg))):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._PERIODIC_TITLE, "none", "doc-none-quiet")
        self.assertEqual((rows, status), ([], "empty"))
        self.assertEqual([c for c in captured if c[0] == "WARN"], [])

    def test_extracted_with_unknown_assess_stays_quiet(self):
        # 비발화 대조군 2: unknown 이어도 표가 잡히면 정상 추출 — 경고가 없어야 한다.
        data = _build_pdf(self._PERIODIC_TITLE, self._ROWS)
        captured: list[tuple[str, str]] = []
        with _FlagCtx("true"), mock.patch.object(
                g, "log", side_effect=lambda lvl, msg: captured.append((lvl, msg))):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._PERIODIC_TITLE, "unknown", "doc-unk-ok")
        self.assertEqual(status, "extracted")
        self.assertEqual(len(rows), 1)
        self.assertEqual([c for c in captured if c[0] == "WARN"], [])


class TestDeficiencyTableHealthTally(unittest.TestCase):
    """★[침묵 사각지대 가드 2026-08-25] empty+unknown 조합의 health 관측(순수 dict 로직)."""

    @staticmethod
    def _item(status: str, assess: str, firm: str = "테스트제약") -> SimpleNamespace:
        return SimpleNamespace(
            raw_payload={"gmp_deficiency_table_status": status,
                         "attachment_deficiency_assessment": assess},
            firm=firm)

    @staticmethod
    def _fresh() -> dict:
        return {"enabled": True, "attempted": 0, "extracted": 0, "failed": 0, "warnings": []}

    def test_empty_unknown_records_observation(self):
        h = self._fresh()
        g._tally_deficiency_table_health(h, self._item("empty", "unknown"))
        self.assertEqual(h["attempted"], 1)     # 카운터는 종전 그대로
        self.assertEqual(h["failed"], 0)
        self.assertEqual(h["warnings"], ["empty-unknown: 테스트제약"])

    def test_empty_none_stays_silent(self):
        # 비발화: '지적사항 없음' + 표 없음은 정상이라 관측 항목도 없다.
        h = self._fresh()
        g._tally_deficiency_table_health(h, self._item("empty", "none"))
        self.assertEqual(h["attempted"], 1)
        self.assertEqual(h["warnings"], [])

    def test_gate_degraded_path_unchanged(self):
        # 기존 present 경로 회귀 가드: failed 카운트·경고 형식 종전 그대로.
        h = self._fresh()
        g._tally_deficiency_table_health(h, self._item("gate-degraded", "present"))
        self.assertEqual(h["attempted"], 1)
        self.assertEqual(h["failed"], 1)
        self.assertEqual(h["warnings"], ["gate-degraded: 테스트제약"])


class TestAnchorColonForm(unittest.TestCase):
    def test_colon_form_now_matched(self):
        # 실문 콜론형("평가 결과: 지적(보완)사항") — 종전 1번 앵커 MISS → 콜론 허용 수정 검증.
        text = ("- 1 - GMP 정기실태조사 결과 제조소명: 콜론제약 실사 목적: 정기 "
                "평가 결과: 지적(보완)사항 품질경영 기타 오염관리 미흡")
        ex = g._extract_deficiency_excerpt(text)
        self.assertTrue(ex.startswith("평가 결과: 지적(보완)사항"))
        self.assertNotIn("제조소명", ex)


def _hwpx_bytes(tables):
    """hp:tbl/hp:tr/hp:tc 마크업을 가진 최소 HWPX(zip) 생성 — 바이너리 픽스처 불요."""
    import io as _io
    import zipfile as _zip
    ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    body = []
    for rows in tables:
        trs = []
        for row in rows:
            tcs = "".join(
                f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{c}</hp:t>"
                f"</hp:run></hp:p></hp:subList></hp:tc>" for c in row)
            trs.append(f"<hp:tr>{tcs}</hp:tr>")
        body.append(f"<hp:tbl>{''.join(trs)}</hp:tbl>")
    xml = f"<?xml version='1.0' encoding='UTF-8'?><hp:sec {ns}>{''.join(body)}</hp:sec>"
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as zf:
        zf.writestr("Contents/section0.xml", xml.encode("utf-8"))
    return buf.getvalue()


class TestHwpxDeficiencyTable(unittest.TestCase):
    """HWPX 지적 표 추출 — PDF 와 달리 표가 **명시 마크업**이라 추정이 필요 없다.

    실측 계기: hwpx 16건은 게이트가 `file_format == "pdf"` 라 표 추출이 시도조차 되지 않아
    `gmp_deficiency_table_status` 가 통째로 비어 있었다(본문 텍스트는 이미 뽑고 있었다).
    전건 실측(2026-08-27): 지적 present 7건 → 23행 · none 6건 → 0행(오탐 0)."""

    HEADER = ["분야", "구분", "근거 법령", "지적(보완)사항 요약", "비고"]

    def test_extracts_rows_from_markup(self):
        data = _hwpx_bytes([[
            self.HEADER,
            ["제조", "기타", "[별표 1의2] 제6.4호", "제조기록서를 작업과 동시에 작성하지 않았음",
             "보완완료"],
            ["시험실", "기타", "[별표 1의2] 제11.1호", "품질관리기록서에 근거자료 미첨부",
             "보완완료"],
        ]])
        rows = g._extract_hwpx_deficiency_table(data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["area"], "제조")
        self.assertEqual(rows[0]["legal_basis"], "[별표 1의2] 제6.4호")
        self.assertEqual(rows[1]["summary"], "품질관리기록서에 근거자료 미첨부")

    def test_non_deficiency_table_is_ignored(self):
        """제조소 현황 표 등은 헤더 토큰이 없어 채택되지 않는다(오탐 0 실측과 동형)."""
        data = _hwpx_bytes([[["제조소명", "소재지"], ["A사", "서울"]]])
        self.assertEqual(g._extract_hwpx_deficiency_table(data), [])

    def test_broken_zip_degrades_quietly(self):
        """첨부 붕괴는 요약카드 유지로 degrade — 수집을 죽이지 않는다."""
        self.assertEqual(g._extract_hwpx_deficiency_table(b"not a zip"), [])

    def test_shares_the_pdf_normalizer(self):
        """별도 정규화기를 두면 산출 모양이 갈린다 — 같은 함수를 쓰는지 못박는다."""
        import inspect
        src = inspect.getsource(g._extract_hwpx_deficiency_table)
        self.assertIn("_normalize_deficiency_table", src)


class TestCollapsedColmapGuard(unittest.TestCase):
    """★병합 셀 하나에 페이지 전체 텍스트가 담기면 헤더 토큰 3개가 우연히 다 들어간다.

    그대로 채택하면 모든 필드가 같은 열을 가리켜 데이터행 한 칸이 다섯 필드에 복제된
    **가짜 행**(`분야=근거법령=지적내용='한약정책과'`)이 나온다 — HWPX 실측 6건에서 발생했다.
    PDF 경로도 같은 정규화기를 쓰므로, 채택 전 회귀 코퍼스 194문서/934행이 **바이트 불변**
    임을 실측했다."""

    def test_merged_single_cell_header_is_rejected(self):
        merged = [["실사 결과 분야 구분 근거 법령 지적(보완)사항 요약 비고 기타 안내문"],
                  ["한약정책과"]]
        idx, colmap = g._match_deficiency_header(merged)
        self.assertIsNone(idx)
        self.assertEqual(colmap, {})
        self.assertEqual(g._normalize_deficiency_table(merged), [])

    def test_real_multi_column_header_still_matches(self):
        """음성 검사 — 가드가 정상 표를 훔치면 안 된다."""
        real = [["분야", "구분", "근거 법령", "지적(보완)사항 요약"],
                ["제조", "기타", "[별표 1] 제6호", "기록 미비"]]
        idx, colmap = g._match_deficiency_header(real)
        self.assertEqual(idx, 0)
        self.assertGreaterEqual(
            len({v for v in colmap.values() if v is not None}),
            g._DEFICIENCY_MIN_COLUMNS)
        self.assertEqual(len(g._normalize_deficiency_table(real)), 1)


class TestDeficiencyTableFormats(unittest.TestCase):
    def test_pdf_and_hwpx_only(self):
        self.assertEqual(set(g._DEFICIENCY_TABLE_FORMATS), {"pdf", "hwpx"})

    def test_hwp_ole_still_excluded(self):
        """구형 바이너리 hwp 는 읽을 경로가 없다 — 늘리지 않는다."""
        self.assertNotIn("hwp-ole", g._DEFICIENCY_TABLE_FORMATS)

    def test_gate_routes_hwpx_to_hwpx_extractor(self):
        data = _hwpx_bytes([[
            ["분야", "구분", "근거 법령", "지적(보완)사항 요약"],
            ["제조", "기타", "[별표 1] 제6호", "기록 미비"],
        ]])
        with mock.patch.object(g, "_deficiency_table_enabled", return_value=True), \
             mock.patch.object(g, "_detect_inspection_type", return_value="periodic"):
            rows, status = g._parse_deficiency_table(
                data, "hwpx", "정기 실태조사 결과", "present", "doc-1")
        self.assertEqual(status, "extracted")
        self.assertEqual(len(rows), 1)

    def test_gate_still_skips_unsupported_format(self):
        with mock.patch.object(g, "_deficiency_table_enabled", return_value=True):
            rows, status = g._parse_deficiency_table(
                b"x", "hwp-ole", "text", "present", "doc-2")
        self.assertEqual((rows, status), ([], ""))


# ── [가림막 가드 2026-08-27 · docs/specs/GMP_지적표_추출불가_실측_2026-08-27.md] ──────
# 식약처가 지적사항 일부를 검은 막대로 가려 배포하는데, 그 막대는 글자를 지우지 않고
# **살아 있는 텍스트 위에 덧그려져** 있어 `get_text()` 가 아래를 읽는다. 우리 파서는
# 원천이 의도적으로 감춘 문장을 추출하고 있었다(CONTROL 194문서 중 13문서/35행).
# 행 단위로 버리는 근거는 collect_mfds_gmp_inspection.py 상단 상수 블록 주석 참조.

class TestZeroMaskGuard(unittest.TestCase):
    """텍스트층 가림(`0000…`) — 좌표가 없어 **PDF·HWPX 두 경로 공통**으로 걸린다."""

    _HEADER = ["분야", "구분", "근거 법령", "지적(보완)사항 요약", "비고"]

    def test_is_zero_masked_predicate(self):
        for masked in ("0000", "00000000", "0000 0000 0000", " 0000\n0000 "):
            with self.subTest(value=masked):
                self.assertTrue(g._is_zero_masked(masked))
        for clean in ("", "0", "000", "제0000호", "[별표 1] 제6.4호",
                      "2026-08-27", "밸리데이션 실시할 것"):
            with self.subTest(value=clean):
                self.assertFalse(g._is_zero_masked(clean))

    def test_fully_masked_row_dropped(self):
        rows = [self._HEADER,
                ["0000", "0000", "0000 0000", "0000 0000 0000", "0000"],
                ["제조", "기타", "[별표 1] 제6호", "기록 미비", "보완완료"]]
        out = g._normalize_deficiency_table(rows)
        self.assertEqual([r["summary"] for r in out], ["기록 미비"])

    def test_single_masked_field_drops_the_whole_row(self):
        """근거법령이 살아 있어도 지적내용이 가려졌으면 그 행은 나가면 안 된다 —
        남은 칸만 실으면 '근거법령만 있고 지적은 없다'는 거짓 진술이 된다."""
        rows = [self._HEADER,
                ["제조", "기타", "[별표 1] 제6호", "000000000000", "보완완료"]]
        self.assertEqual(g._normalize_deficiency_table(rows), [])

    def test_masked_legal_basis_drops_the_row(self):
        rows = [self._HEADER,
                ["제조", "기타", "0000 0000", "제조기록서 미작성", "보완완료"]]
        self.assertEqual(g._normalize_deficiency_table(rows), [])

    def test_legitimate_zero_bearing_citation_survives(self):
        """`제0000호` 처럼 0 이 넷 박힌 정상 표기는 살아야 한다(과잉 차단 방지)."""
        rows = [self._HEADER,
                ["제조", "기타", "고시 제0000호", "기록 미비", "0"]]
        out = g._normalize_deficiency_table(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["legal_basis"], "고시 제0000호")

    def test_hwpx_path_shares_the_guard(self):
        """HWPX 는 좌표가 없지만 같은 정규화기를 쓰므로 이 가드가 자동으로 걸린다."""
        data = _hwpx_bytes([[
            self._HEADER,
            ["0000", "0000", "0000 0000", "0000 0000 0000", "0000"],
            ["시험실", "기타", "[별표 1의2] 제11.1호", "근거자료 미첨부", "보완완료"],
        ]])
        rows = g._extract_hwpx_deficiency_table(data)
        self.assertEqual([r["summary"] for r in rows], ["근거자료 미첨부"])


class TestRedactedRowIndexPlumbing(unittest.TestCase):
    """`redacted_rows` 인덱스는 **원본 표 행렬 기준**이다(헤더 뒤 슬라이스 기준이 아니다)."""

    _HEADER = ["분야", "구분", "근거 법령", "지적(보완)사항 요약", "비고"]
    _ROW_A = ["제조", "기타", "[별표 1] 제6호", "제조기록서 미작성", "보완완료"]
    _ROW_B = ["시험실", "중요", "[별표 1] 제11호", "근거자료 미첨부", "행정처분"]

    def test_default_is_a_no_op(self):
        rows = [self._HEADER, self._ROW_A, self._ROW_B]
        self.assertEqual(g._normalize_deficiency_table(rows),
                         g._normalize_deficiency_table(rows, redacted_rows=frozenset()))

    def test_marked_row_is_dropped(self):
        rows = [self._HEADER, self._ROW_A, self._ROW_B]
        out = g._normalize_deficiency_table(rows, redacted_rows=frozenset({1}))
        self.assertEqual([r["summary"] for r in out], ["근거자료 미첨부"])

    def test_index_is_matrix_relative_not_slice_relative(self):
        """★헤더 앞에 표지행이 있으면 슬라이스 기준 인덱스는 통째로 밀린다.

        행렬: 0=표지 · 1=단위 · 2=헤더 · **3=`_ROW_A`** · 4=`_ROW_B`.
        인덱스 3 을 가리면 `_ROW_A` 가 빠지고 `_ROW_B` 만 남아야 한다. 슬라이스(헤더+1)
        기준으로 셌다면 3 은 그 슬라이스의 범위 밖이라 **아무것도 안 버려지고** 두 행이
        다 나간다 — 가드가 조용히 통과되는 형태라 이 테스트가 그걸 못박는다.
        """
        rows = [["의약품 제조소 실태조사 결과"], ["(단위: 건)"],
                self._HEADER, self._ROW_A, self._ROW_B]
        out = g._normalize_deficiency_table(rows, redacted_rows=frozenset({3}))
        self.assertEqual([r["summary"] for r in out], ["근거자료 미첨부"])

    def test_all_rows_marked_yields_nothing(self):
        rows = [self._HEADER, self._ROW_A, self._ROW_B]
        self.assertEqual(
            g._normalize_deficiency_table(rows, redacted_rows=frozenset({0, 1, 2})), [])


class _StubRow:
    def __init__(self, cells):
        self.cells = cells


class _StubTable:
    def __init__(self, rows, bbox):
        self.rows = rows
        self.bbox = bbox


@unittest.skipUnless(_has_fitz(), "PyMuPDF(fitz) 필요")
class TestRedactionBarDetection(unittest.TestCase):
    """막대 판별 하한 — 표 괘선·밑줄을 막대로 오인하면 멀쩡한 표가 통째로 사라진다."""

    def _bars(self, drawings):
        class _Page:
            def get_drawings(self_inner):
                return drawings
        return g._pdf_redaction_bars(_Page())

    def _rect(self, x0, y0, x1, y1):
        import fitz
        return fitz.Rect(x0, y0, x1, y1)

    def test_black_bar_detected(self):
        bars = self._bars([{"fill": (0.0, 0.0, 0.0),
                            "items": [("re", self._rect(100, 100, 300, 118))]}])
        self.assertEqual(len(bars), 1)

    def test_scalar_grayscale_black_fill_detected(self):
        """★회색조 단일 채널 `0.0` 은 falsy 다 — `if not fill` 로 걸러 내면 진짜 검은
        막대를 통째로 놓친다(가드가 조용히 무력화되는 형태)."""
        bars = self._bars([{"fill": 0.0,
                            "items": [("re", self._rect(100, 100, 300, 118))]}])
        self.assertEqual(len(bars), 1)

    def test_table_rule_is_not_a_bar(self):
        """괘선은 세로 두께가 사실상 0 — 이걸 막대로 보면 모든 표가 가려진 게 된다."""
        bars = self._bars([{"fill": (0.0, 0.0, 0.0),
                            "items": [("re", self._rect(40, 110, 555, 110.4))]}])
        self.assertEqual(bars, [])

    def test_short_mark_is_not_a_bar(self):
        bars = self._bars([{"fill": (0.0, 0.0, 0.0),
                            "items": [("re", self._rect(100, 100, 112, 118))]}])
        self.assertEqual(bars, [])

    def test_light_fill_is_not_a_bar(self):
        """셀 음영(연회색 배경)은 가림막이 아니다."""
        bars = self._bars([{"fill": (0.9, 0.9, 0.9),
                            "items": [("re", self._rect(100, 100, 300, 118))]}])
        self.assertEqual(bars, [])

    def test_unfilled_rect_is_not_a_bar(self):
        bars = self._bars([{"fill": None,
                            "items": [("re", self._rect(100, 100, 300, 118))]}])
        self.assertEqual(bars, [])

    def test_non_rect_item_ignored(self):
        bars = self._bars([{"fill": (0.0, 0.0, 0.0),
                            "items": [("l", self._rect(100, 100, 300, 118))]}])
        self.assertEqual(bars, [])

    def test_unreadable_drawings_degrade_to_no_bars(self):
        """`get_drawings()` 붕괴는 [] — 여기서 fail-closed 로 가면 가림막 없는 194문서
        전부가 영향을 받아 '안 가려진 문서는 바이트 불변'이라는 대전제가 깨진다."""
        class _Page:
            def get_drawings(self_inner):
                raise RuntimeError("broken")
        self.assertEqual(g._pdf_redaction_bars(_Page()), [])

    def test_no_bars_never_touches_the_text_layer(self):
        """막대가 없으면 단어 추출 자체를 하지 않는다 — 기존 문서 산출 불변의 근거이자
        비용 근거. page 로 None 을 줘도 통과한다는 것이 곧 '건드리지 않았다'는 증거다."""
        self.assertEqual(g._pdf_covered_word_rects(None, []), [])


@unittest.skipUnless(_has_fitz(), "PyMuPDF(fitz) 필요")
class TestRedactedRowIndicesFailClosed(unittest.TestCase):
    """좌표를 행에 대응 못 시키면 '안 가려졌다'가 아니라 '모른다' → 표 전체를 버린다."""

    def _rect(self, x0, y0, x1, y1):
        import fitz
        return fitz.Rect(x0, y0, x1, y1)

    def test_no_covered_words_is_a_no_op(self):
        table = _StubTable([_StubRow([(0, 0, 10, 10)])], (0, 0, 100, 100))
        self.assertEqual(g._pdf_redacted_row_indices(table, 1, []), frozenset())

    def test_row_count_mismatch_drops_whole_table(self):
        table = _StubTable([_StubRow([(0, 0, 10, 10)])], (0, 0, 100, 100))
        self.assertEqual(g._pdf_redacted_row_indices(table, 3, [self._rect(1, 1, 5, 5)]),
                         frozenset({0, 1, 2}))

    def test_missing_geometry_drops_whole_table(self):
        class _Broken:
            @property
            def rows(self):
                raise RuntimeError("no geometry")
            bbox = (0, 0, 100, 100)
        self.assertEqual(g._pdf_redacted_row_indices(_Broken(), 2, [self._rect(1, 1, 5, 5)]),
                         frozenset({0, 1}))

    def test_word_inside_table_but_outside_every_cell_drops_whole_table(self):
        """병합 셀·좌표 누락으로 어느 행인지 말할 수 없으면 통째로 버린다."""
        table = _StubTable([_StubRow([(0, 0, 10, 10)]), _StubRow([(0, 20, 10, 30)])],
                           (0, 0, 100, 100))
        # (50,50)-(60,60) 은 표 안이지만 어느 칸에도 안 들어간다.
        self.assertEqual(
            g._pdf_redacted_row_indices(table, 2, [self._rect(50, 50, 60, 60)]),
            frozenset({0, 1}))

    def test_word_outside_the_table_is_ignored(self):
        """다른 곳(표지·머리말)의 가림막은 이 표와 무관하다."""
        table = _StubTable([_StubRow([(0, 0, 10, 10)]), _StubRow([(0, 20, 10, 30)])],
                           (0, 0, 100, 100))
        self.assertEqual(
            g._pdf_redacted_row_indices(table, 2, [self._rect(500, 500, 520, 520)]),
            frozenset())

    def test_only_the_covered_row_is_marked(self):
        table = _StubTable([_StubRow([(0, 0, 10, 10)]), _StubRow([(0, 20, 10, 30)])],
                           (0, 0, 100, 100))
        self.assertEqual(
            g._pdf_redacted_row_indices(table, 2, [self._rect(2, 22, 8, 28)]),
            frozenset({1}))


@unittest.skipUnless(_has_fitz(), "PyMuPDF(fitz) 필요")
class TestRedactionGuardPDF(unittest.TestCase):
    """PDF 경로 통합 — 실측 문서와 같은 구조(살아 있는 글자 위 검은 막대)로 재현."""

    _ROWS = [["시설장비", "기타", "[별표 1] 제2.1호", "교차오염 방지시설 미비", "이행 인정"],
             ["제조", "중요", "[별표 1] 제6.1호", "밸리데이션 미실시", "행정처분 예정"]]
    _TITLE = "의약품 제조소 GMP 정기실태조사(정기실사) 결과"
    _HIDDEN = "밸리데이션 미실시"
    _VISIBLE = "교차오염 방지시설 미비"

    def _redacted_pdf(self):
        # 데이터행 1(두 번째 지적)의 '지적(보완)사항 요약' 칸(컬럼 3)을 가린다.
        return _build_pdf(self._TITLE, self._ROWS, redact_cells=[(1, 3)])

    def test_fixture_really_hides_live_text(self):
        """★픽스처 자신을 assert — 막대 아래 글자가 텍스트층에 **살아 있어야** 이 테스트가
        의미를 갖는다. 글자가 지워진 픽스처였다면 가드가 없어도 초록이라 무의미하다."""
        import fitz
        with fitz.open(stream=self._redacted_pdf(), filetype="pdf") as doc:
            text = doc[0].get_text()
        self.assertIn("밸리데이션", text)

    def test_defect_reintroduced_leaks_hidden_text(self):
        """★네거티브 — 막대 탐지를 꺼서(가드 도입 직전 상태) 결함을 되살리면 가려진
        문장이 그대로 추출된다. 이 테스트가 빨갛지 않으면 가드가 아니라 픽스처가
        일하고 있는 것이다."""
        data = self._redacted_pdf()
        with mock.patch.object(g, "_pdf_redaction_bars", return_value=[]):
            leaked = g._extract_deficiency_table(data)
        self.assertIn(self._HIDDEN, [r["summary"] for r in leaked])

    def test_covered_row_is_dropped(self):
        rows = g._extract_deficiency_table(self._redacted_pdf())
        summaries = [r["summary"] for r in rows]
        self.assertNotIn(self._HIDDEN, summaries)
        self.assertIn(self._VISIBLE, summaries)          # 안 가려진 행은 살아남는다

    def test_hidden_text_absent_from_every_field(self):
        """지적사항 칸만 보고 넘어가면 다른 칸으로 새는 것을 놓친다."""
        rows = g._extract_deficiency_table(self._redacted_pdf())
        for row in rows:
            for value in row.values():
                self.assertNotIn("밸리데이션", value)

    def test_unredacted_table_is_untouched(self):
        """가림막이 없는 문서는 가드가 있으나 없으나 **같은 산출**이어야 한다 —
        회귀 코퍼스 194문서 바이트 불변 주장의 최소 재현."""
        data = _build_pdf(self._TITLE, self._ROWS)
        guarded = g._extract_deficiency_table(data)
        with mock.patch.object(g, "_pdf_redaction_bars", return_value=[]):
            baseline = g._extract_deficiency_table(data)
        self.assertEqual(guarded, baseline)
        self.assertEqual(len(guarded), 2)

    def test_guard_logs_when_it_fires(self):
        """조용한 가드는 '표가 없는 문서'와 구분이 안 된다 — 발동은 반드시 남는다."""
        with mock.patch.object(g, "log") as logged:
            g._extract_deficiency_table(self._redacted_pdf(), "gmpinspect-TEST")
        messages = [" ".join(str(a) for a in call.args) for call in logged.call_args_list]
        self.assertTrue(any("가림막" in m and "gmpinspect-TEST" in m for m in messages),
                        f"가드 발동 로그 없음: {messages}")

    def test_no_log_when_nothing_is_redacted(self):
        with mock.patch.object(g, "log") as logged:
            g._extract_deficiency_table(_build_pdf(self._TITLE, self._ROWS))
        self.assertEqual(logged.call_args_list, [])

    def test_gate_still_reports_extracted_for_surviving_rows(self):
        """가드가 일부 행만 먹으면 나머지는 정상 발행 경로로 간다(전면 강등 금지)."""
        data = self._redacted_pdf()
        with mock.patch.object(g, "_deficiency_table_enabled", return_value=True):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._TITLE, "present", "gmpinspect-TEST")
        self.assertEqual(status, "extracted")
        self.assertEqual([r["summary"] for r in rows], [self._VISIBLE])

    def test_fully_redacted_table_degrades_to_summary_card(self):
        """모든 지적행이 가려지면 표 없음과 같은 강등 경로로 간다(요약카드 유지)."""
        data = _build_pdf(self._TITLE, self._ROWS, redact_cells=[(0, 3), (1, 3)])
        with mock.patch.object(g, "_deficiency_table_enabled", return_value=True):
            rows, status = g._parse_deficiency_table(
                data, "pdf", self._TITLE, "present", "gmpinspect-TEST")
        self.assertEqual((rows, status), ([], "gate-degraded"))


if __name__ == "__main__":
    unittest.main()
