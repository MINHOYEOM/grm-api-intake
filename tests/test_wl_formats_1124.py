"""WL 위반항목 지원 형식 확장(F5·F6)과 본문 앞 결손 회귀 — 실서한 표본 (2026-11-24).

11-23 수리 뒤에도 남은 '지원 형식 밖' 행(unsupported_format 58)과 앞부분 복원한 88행을 fda.gov 원문으로 분류해
두 형식을 더했다. 표본은 형식별 **실제 서한**(`tests/fixtures/wl_violations_1124/samples.json`)이다:
  F5 — 연속 번호 표제: API 704(a)(4)·CGMP `N. Failure to …` · HCT/P `N) Failure to … [21 CFR 1271…]` · DSCSA `N. Your firm …
       (FD&C Act Section …)`
  F6 — 번호 없는 BIMO(임상시험자·GLP) `You failed to … [21 CFR 312.60].`
고정하는 것: ① 형식별 실서한이 그 형식·개수·번호로 뽑힌다 ② 뽑힌 문장은 본문에 글자 그대로 있다
③ 기존 형식(F1~F4)이 뽑던 서한은 F5·F6 이 있어도 결과가 한 글자도 안 바뀐다(회귀 0)
④ 본문 앞 결손 두 원인(늦게 걸리는 'Specifically,' 앵커 · 'Dear' 없는 `RE:` 인사말)이 실서한 페이지에서 재발하지 않는다.
"""

from __future__ import annotations

import html
import json
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import collect_intake as ci  # noqa: E402

_FIX = os.path.join(_ROOT, "tests", "fixtures", "wl_violations_1124", "samples.json")
_FIX_1123 = os.path.join(_ROOT, "tests", "fixtures", "wl_violations_1123", "samples.json")


def _load() -> dict:
    with open(_FIX, encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return " ".join(str(s).split())


class RealLetterFormatTest(unittest.TestCase):
    def test_every_sample_extracts_its_format(self):
        samples = _load()["samples"]
        self.assertGreaterEqual({s["expect_format"] for s in samples}, {"F4", "F5", "F6"})
        for s in samples:
            with self.subTest(doc=s["document_id"], label=s["label"]):
                rows, fmt = ci.extract_wl_violations_detail(s["wl_body_full"])
                self.assertEqual(fmt, s["expect_format"])
                self.assertEqual(len(rows), s["expect_count"])
                self.assertEqual([r["number"] for r in rows], s["expect_numbers"])
                self.assertEqual(rows[0]["statement"], s["expect_first"])
                body = _norm(s["wl_body_full"])
                for r in rows:
                    self.assertIn(_norm(r["statement"]), body, "본문에 없는 문장")      # 원문 그대로 — 지어내기 금지
                    self.assertTrue(r["statement"].strip())

    def test_build_wl_item_path_gives_same_rows(self):
        """일일 수집·백필·재추출이 지나는 build_wl_item 도 같은 결과(경로가 갈라지지 않는다)."""
        for s in _load()["samples"]:
            with self.subTest(doc=s["document_id"]):
                item = ci.build_wl_item(
                    firm="Fixture", posted_raw="2026-03-01", letter_date_raw="", issuing_office="CDER",
                    subject="Clinical Investigator", wl_href=f"https://www.fda.gov/warning-letters/{s['url_slug']}",
                    office_verdict="", excerpt=None, full=(s["wl_body_full"], "ok"))
                got = item.raw_payload.get("wl_violations") or []
                self.assertEqual(len(got), s["expect_count"])

    def test_numbered_list_inside_a_response_is_not_a_violation(self):
        """'(1) you revised … (2) you updated …' 같은 회신 요약의 번호 목록은 표제가 아니다(7a13: F6 한 건뿐)."""
        s = next(s for s in _load()["samples"] if s["document_id"] == "7a1396863214")
        rows, fmt = ci.extract_wl_violations_detail(s["wl_body_full"])
        self.assertEqual((fmt, len(rows)), ("F6", 1))
        self.assertTrue(rows[0]["statement"].startswith("You failed to obtain informed consent"))

    def test_f5_needs_consecutive_numbers_from_one(self):
        body = ("Dear Sir: We found problems. 2) Failure to test each lot [21 CFR 211.165(a)]. Details here. "
                "3) Failure to label [21 CFR 211.122]. Details. Conclusion You must respond.")
        rows, fmt = ci._extract_wl_f5(" ".join(body.split())), "F5"
        self.assertEqual(rows, [])                         # 1 이 없으면 표제 목록이 아니다


class NoRegressionTest(unittest.TestCase):
    def test_earlier_formats_are_untouched_by_f5_f6(self):
        """11-23 표본 전부: F1~F4 가 뽑던 서한은 F5·F6 을 꺼도 같다(= F5·F6 이 끼어들지 않는다)."""
        with open(_FIX_1123, encoding="utf-8") as f:
            samples = json.load(f)["samples"]
        checked = 0
        for s in samples:
            body = s["wl_body_full"]
            rows, fmt = ci.extract_wl_violations_detail(body)
            if fmt in ("F5", "F6"):
                continue                                   # 새로 뽑히게 된 서한(이전엔 빈 사유)
            with mock.patch.object(ci, "_extract_wl_f5", side_effect=AssertionError("F5 끼어듦")), \
                    mock.patch.object(ci, "_extract_wl_f6", side_effect=AssertionError("F6 끼어듦")):
                try:
                    again = ci.extract_wl_violations_detail(body)
                except AssertionError:
                    # F1~F4 가 빈손이라 F5 에 도달한 서한 — 그때 결과는 사유(빈 목록)여야 한다.
                    self.assertEqual(rows, [], f"{s['document_id']}: 사유가 있던 서한이 F5/F6 로 바뀜")
                    continue
            self.assertEqual((rows, fmt), again, s["document_id"])
            checked += 1
        self.assertGreater(checked, 10)

    def test_formats_tuple_lists_new_formats(self):
        self.assertEqual(ci.WL_VIOLATION_FORMATS[-2:], ("F5", "F6"))

    def test_out_of_scope_letters_never_reach_f5_f6(self):
        s = next(s for s in _load()["samples"] if s["expect_format"] == "F6")
        rows, status = ci.extract_wl_violations_detail(s["wl_body_full"], extended=False)
        self.assertEqual((rows, status), ([], ci.WL_VIOLATIONS_STATUS_OUT_OF_SCOPE))


class HeadLossRegressionTest(unittest.TestCase):
    """실서한 페이지 전체 텍스트(탐색 메뉴 포함)로 본문 시작점을 고정한다."""

    def test_real_pages_start_at_the_letter_not_mid_letter(self):
        for p in _load()["pages"]:
            with self.subTest(doc=p["document_id"], why=p["why"]):
                page = "<html><body><p>" + html.escape(p["page_text"]) + "</p></body></html>"
                body = ci._extract_wl_body_full(page)
                self.assertTrue(body.startswith(p["expect_start"]), body[:80])
                self.assertNotRegex(body, r"^\s*Conclusion\b")
                self.assertNotRegex(body, r"^\s*Specifically,")

    def test_re_salutation_is_a_letter_opening(self):
        self.assertIsNotNone(ci._wl_letter_opening("WARNING LETTER March 31, 2026 RE: 721806 Bernard Gramlich: This letter"))
        self.assertIsNone(ci._wl_letter_opening("Warning letter about RE: nothing here"))

    def test_conclusion_start_is_flagged_as_truncated_head(self):
        self.assertTrue(ci._WL_BODY_HEAD_TRUNCATED_RE.match("Conclusion The violations cited in this letter"))
        rows, status = ci.extract_wl_violations_detail("Conclusion The violations cited in this letter are not intended.")
        self.assertEqual((rows, status), ([], ci.WL_VIOLATIONS_STATUS_BODY_HEAD_TRUNCATED))


if __name__ == "__main__":
    unittest.main()
