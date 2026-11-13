# -*- coding: utf-8 -*-
"""편집팀 사전 점검 도구(`company/editorial/precheck.py`) — 조립 검사·발행 브릿지와 같은 규칙인지."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import delta_bridge
import inject_slots

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("company_precheck", ROOT / "company" / "editorial" / "precheck.py")
precheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precheck)


def _card(**kw):
    base = {"title_issue": "무균공정 관리 미흡", "summary": "FDA가 관찰사항 3건을 적었다.",
            "key_facts": ["관찰사항: 3건"], "implication": "", "checks": []}
    base.update(kw)
    return base


class TermsTest(unittest.TestCase):
    def test_clean_delta_passes(self):
        self.assertEqual(precheck.term_lines({"cards": {"a": _card()}}), [])

    def test_bare_abbreviation_and_left_column_term_are_caught(self):
        # 리허설 1113c 의 반려: 'CAPA' 가 풀이 없이 처음 나옴.
        delta = {"cards": {"a": _card(implication="CAPA 절차가 수립되지 않았다."),
                           "b": _card(summary="규격 이탈 결과를 조사하지 않았다."),
                           "c": _card(implication="시정 및 예방조치(CAPA) 절차가 없었다. CAPA 기록도 없었다.")}}
        lines = precheck.term_lines(delta)
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("'CAPA' 단독" in x and "a" in x for x in lines))
        self.assertTrue(any("기준일탈(OOS)" in x and "b" in x for x in lines))

    def test_same_rules_as_the_assembly_lint(self):
        delta = {"cards": {"a": _card(summary="데이터 무결성 문제가 있었다.")}}
        self.assertEqual(precheck.term_lines(delta), inject_slots.lint_prose_style(delta).lines())

    def test_length_limits_are_the_code_limits(self):
        ko, en = inject_slots._HOOK_LINE_MAX["ko"], inject_slots._HOOK_LINE_MAX["en"]
        t = inject_slots._MAX_TITLE_ISSUE
        ok = _card(title_issue="가" * t, hook_line="가" * ko,
                   en={"title_issue": "a" * (t * 2), "hook_line": "a" * en})
        self.assertEqual(precheck.term_lines({"cards": {"a": ok}}), [])
        over = _card(title_issue="가" * (t + 1), hook_line="가" * (ko + 1),
                     en={"title_issue": "a" * (t * 2 + 1), "hook_line": "a" * (en + 1)})
        lines = precheck.term_lines({"cards": {"a": over}})
        self.assertEqual(len(lines), 4)
        self.assertTrue(any(f"a.en.title_issue: {t * 2 + 1}자" in x for x in lines))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = os.path.join(tmp, "g.json"), os.path.join(tmp, "b.json")
            Path(good).write_text(json.dumps({"cards": {"a": _card()}}, ensure_ascii=False), encoding="utf-8")
            Path(bad).write_text(json.dumps({"cards": {"a": _card(implication="CAPA 가 없었다.")}},
                                            ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(precheck.main(["terms", good]), 0)
                self.assertEqual(precheck.main(["terms", bad]), 1)
                self.assertEqual(precheck.main(["terms", os.path.join(tmp, "none.json")]), 2)


class DeepSourceTest(unittest.TestCase):
    HANDOFF = {"rows": [
        {"web_card_id": "wl-1", "deep_analysis_input": {"body_full": "원문 하나"}},
        {"web_card_id": "wl-1", "deep_analysis_input": {"body_full": "다른 원문"}},      # 먼저 만난 것이 정본
        {"web_card_id": "fda483-2", "deep_analysis_input": {"body_full": "  "}},
        {"web_card_id": "hc-3"},
        "쓰레기",
    ]}

    def test_bodies_match_the_bridge_extractor(self):
        with mock.patch.object(delta_bridge, "log"):
            want = delta_bridge.extract_handoff_body_full(self.HANDOFF)
        self.assertEqual(precheck.handoff_bodies(self.HANDOFF), want)
        self.assertEqual(want, {"wl-1": "원문 하나"})

    def test_copy_gets_the_same_source_text_the_bridge_applies(self):
        deep = {"wl-1": {"deep_analysis": {"x": 1}}, "fda483-2": {"deep_analysis": {}}, "odd": "문자열"}
        before = json.dumps(deep, ensure_ascii=False, sort_keys=True)
        out, filled = precheck.with_source_text(deep, precheck.handoff_bodies(self.HANDOFF))
        self.assertEqual(json.dumps(deep, ensure_ascii=False, sort_keys=True), before)   # 원본은 그대로
        bridged = json.loads(before)
        with mock.patch.object(delta_bridge, "log"):
            delta_bridge.apply_handoff_source_text(bridged, {"wl-1": "원문 하나"})
        self.assertEqual(out, bridged)
        self.assertEqual(filled, 1)
        self.assertNotIn("source_text", out["fda483-2"])

    def test_cli_writes_a_separate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            h, d, o = (os.path.join(tmp, n) for n in ("h.json", "d.json", "o.json"))
            Path(h).write_text(json.dumps(self.HANDOFF, ensure_ascii=False), encoding="utf-8")
            Path(d).write_text(json.dumps({"wl-1": {"deep_analysis": {}}}, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(["deep-source", h, d, "--out", o]), 0)
            self.assertEqual(json.loads(Path(o).read_text(encoding="utf-8"))["wl-1"]["source_text"], "원문 하나")
            self.assertNotIn("source_text", json.loads(Path(d).read_text(encoding="utf-8"))["wl-1"])


class InstructionsPointAtTheToolTest(unittest.TestCase):
    def _read(self, *parts):
        return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")

    def test_reporters_run_terms_and_next_copyedit_runs_deep_source(self):
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            self.assertIn("python company/editorial/precheck.py terms company/run/{run_date_kst}/delta.{S}.json",
                          self._read(*folder, "reporter.md"), folder)
        copyedit = self._read("company", "editorial", "next", "copyedit.md")
        self.assertIn("python company/editorial/precheck.py deep-source _handoff/handoff_$D.json", copyedit)
        self.assertIn("--deep _scaffold/deep_check.json", copyedit)

    def test_rehearsal_success_still_requires_zero_rejects(self):
        self.assertIn("4. 교열 reject 가 0 이다.", self._read("company", "editorial", "REHEARSAL.md"))
        self.assertIn("n_reject == 0", self._read("company", "editorial", "rehearsal_check.py"))


if __name__ == "__main__":
    unittest.main()
