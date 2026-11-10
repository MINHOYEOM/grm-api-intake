"""브리프 산문 표현 검사(inject_slots.lint_prose_style) — 2026-11-10 대표 결정 4.

경고만 낸다(발행 차단 아님). 확인하는 것:
  · 맺음 반복률 — 금지어 목록에 없는 **새 상투어**도 같은 끝 6글자가 30%를 넘으면 잡힌다.
  · 금지 표현·원자료 표기·용어 표기를 카드 id 와 함께 센다.
  · 원문 인용(quotes)·코드 facts 는 보지 않는다(원문 표기는 고치지 않는다).
  · 조립기는 이 결과를 경고로만 싣고 발행을 막지 않는다(STYLE_LINT_BLOCKING=False).
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import assemble_publish_brief as apb  # noqa: E402
import inject_slots as inj  # noqa: E402
from test_assemble_publish_brief import DELTA_PATH, TRUTH_PATH, _blank_scaffold_from  # noqa: E402


def _delta(impls, **extra):
    cards = {f"c{i}": {"implication": t, **extra} for i, t in enumerate(impls)}
    return {"cards": cards}


class EndingRepeatTest(unittest.TestCase):
    def test_new_cliche_caught_without_ban_list(self):
        # "정리해 둘 일이다" 는 금지어 목록에 없다 — 비율로만 잡힌다.
        impls = [f"사실 {i}. 정리해 둘 일이다." for i in range(4)] + ["다른 사실이다.", "또 다른 끝."]
        rep = inj.lint_prose_style(_delta(impls))
        self.assertTrue(rep.ending_over)
        self.assertEqual(rep.ending_top, "정리해둘일이다"[-6:])
        self.assertTrue(any("맺음 반복" in l for l in rep.lines()))

    def test_varied_endings_pass(self):
        impls = ["FDA는 회신을 받아들이지 않았다.", "처분이 13개 품목으로 확대됐다.",
                 "정지 기간이 3개월로 늘었다.", "회사는 재교육으로 대응했다.", "수입경보에 올랐다."]
        self.assertFalse(inj.lint_prose_style(_delta(impls)).ending_over)

    def test_small_issue_not_judged(self):
        impls = ["같은 끝이다 확인할 차례다."] * 4          # 시사점 4장 < 최소 5장
        self.assertFalse(inj.lint_prose_style(_delta(impls)).ending_over)

    def test_empty_implications_ignored(self):
        impls = [""] * 10 + ["끝이 같다."] * 2
        self.assertFalse(inj.lint_prose_style(_delta(impls)).ending_over)


class PhraseAndTermTest(unittest.TestCase):
    def _lines(self, **slots):
        return inj.lint_prose_style({"cards": {"x": slots}}).lines()

    def test_banned_phrases(self):
        for text, label in [("품질 관리가 문제라는 신호다.", "신호다"),
                            ("일반적으로 설비 손상과 연관될 수 있다.", "일반적으로"),
                            ("원문 확인이 필요하다.", "원문 확인"),
                            ("우리 현장을 확인할 차례다.", "차례다")]:
            self.assertTrue(any(label in l for l in self._lines(implication=text)), text)

    def test_raw_values(self):
        lines = self._lines(key_facts=["회수명령일자: 20260903", "강제여부 Y"])
        self.assertTrue(any("8자리 날짜" in l for l in lines))
        self.assertTrue(any("강제여부" in l for l in lines))
        self.assertTrue(any("미국식 날짜" in l
                            for l in self._lines(summary="FDA가 06/15/2026 종료된 실사에서")))

    def test_term_variants(self):
        lines = self._lines(summary="FDA 는 데이터 무결성과 배지충진 실패를 지적했다.")
        for want in ("FDA 는", "데이터 무결성", "배지충진"):
            self.assertTrue(any(want in l for l in lines), want)

    def test_abbreviation_needs_korean_once(self):
        self.assertTrue(any("'OOS' 단독" in l for l in self._lines(summary="OOS 결과를 무효화했다.")))
        self.assertEqual([l for l in self._lines(summary="기준일탈(OOS) 결과를 무효화했다.")
                          if "OOS" in l], [])

    def test_clean_text_has_no_findings(self):
        self.assertEqual(self._lines(
            title_issue="품질검사 승인 전 출하", summary="식약처가 수입업무정지 3개월을 처분했다.",
            key_facts=["위반: 품질검사 승인 전 출하승인", "처분: 수입업무정지 3개월"],
            implication="", checks=["승인 전 제조번호는 출하승인 단계에서 막히는가?"]), [])

    def test_quotes_translation_not_checked(self):
        # 원문 인용 번역은 원문 표기를 따른다 — 검사 대상이 아니다.
        self.assertEqual(self._lines(quotes_translation=["Warning Letter 20260903 신호다"]), [])


class AssemblerWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))

    def test_warns_but_does_not_block(self):
        self.assertFalse(inj.STYLE_LINT_BLOCKING)
        d = copy.deepcopy(self.delta)
        for c in d["cards"].values():
            c["implication"] = "근거가 분명하다. 우리 현장을 확인할 차례다."
        out, report = apb.assemble_publish_brief(_blank_scaffold_from(self.truth), d, strict=True)
        self.assertTrue(out["cards"])
        self.assertTrue(any(w.startswith("[표현]") and "차례다" in w for w in report.warnings))
        self.assertGreater(report.style.counts["banned"], 0)

    def test_style_report_file(self):
        rep = inj.lint_prose_style(_delta(["확인할 차례다."] * 6))
        path = os.path.join(tempfile.mkdtemp(), "style.md")
        apb._write_style_report(path, rep)
        text = pathlib.Path(path).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("[style-lint] 맺음 반복 100%"))
        self.assertIn("\n- ", text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
