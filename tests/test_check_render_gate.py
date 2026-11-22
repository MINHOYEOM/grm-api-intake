"""check_render_gate — 교열 코드 검사에서 사이트 렌더 게이트를 미리 돌린다(2026-11-22 리허설 1122a 모의 발행)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check_render_gate as g  # noqa: E402


def _brief(obs):
    return {
        "brief": {"publish_date": "2026-11-23", "run_date_kst": "2026-11-23"},
        "cards": [{
            "id": "fda483-1",
            "deterministic_detail": {"type": "fda_483_observations", "observations": obs},
        }],
    }


GOOD = [{"number": "1", "deficiency": "Failure", "deficiency_ko": "실패", "detail": "Specifically", "detail_ko": "구체적으로"}]
BAD = [{"number": "1", "deficiency": "Failure", "detail": "Specifically"}]


class RenderGateTest(unittest.TestCase):
    def test_pass_when_korean_present(self):
        self.assertEqual(g.find_violations(_brief(GOOD)), [])
        self.assertEqual(g.summarize([]), "렌더 게이트 통과")

    def test_483_missing_korean_is_violation_with_card_id(self):
        v = g.find_violations(_brief(BAD))
        self.assertEqual(len(v), 2)  # deficiency_ko · detail_ko
        msg = g.summarize(v)
        self.assertTrue(msg.startswith("렌더 게이트 위반 2건(483 관찰 국문 2건) · 카드 1장"))
        self.assertIn("카드: fda483-1", msg)

    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            ok, bad, junk = (os.path.join(d, n) for n in ("ok.json", "bad.json", "junk.json"))
            Path(ok).write_text(json.dumps(_brief(GOOD)), encoding="utf-8")
            Path(bad).write_text(json.dumps(_brief(BAD)), encoding="utf-8")
            Path(junk).write_text("[]", encoding="utf-8")
            self.assertEqual(g.main([ok]), 0)
            self.assertEqual(g.main([bad]), 1)
            self.assertEqual(g.main([junk]), 2)

    def test_uses_render_validators_not_a_copy(self):
        # 두 벌 금지 — render.py 의 발행 게이트 함수를 그대로 부른다.
        import render
        for _, fn in g.CHECKS:
            self.assertTrue(callable(getattr(render, fn)))
        src = (ROOT / "web" / "render.py").read_text(encoding="utf-8")
        for _, fn in g.CHECKS:
            self.assertIn(f"{fn}(briefs)", src, f"render 발행 게이트가 {fn} 를 더 이상 부르지 않는다 — 목록을 맞춰라")

    def test_copyedit_instructions_run_the_gate(self):
        for rel in ("company/editorial/copyedit.md", "company/editorial/next/copyedit.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("python check_render_gate.py company/run/{run_date_kst}/brief_check.json", text, rel)



class PrecheckEnFactTest(unittest.TestCase):
    """기자 사전 점검이 같은 영문 사실 판정을 조립 뒤 카드에 돌린다(리허설 1122b — '전수' → '100%')."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("company_precheck_rg", ROOT / "company" / "editorial" / "precheck.py")
        self.pc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pc)

    def _out(self, en_hook):
        card = {"id": "fda483-1", "summary": "검은 입자 발견 뒤 전수 재검사 없이 표본만 검사",
                "en": {"summary": en_hook}}
        return {"brief": {"publish_date": "2026-11-23"}, "cards": [card]}

    def test_invented_number_is_flagged(self):
        lines = self.pc.assembled_en_fact_lines({"fda483-1": {}}, self._out("No 100% reinspection after particles found"))
        self.assertEqual(len(lines), 1)
        self.assertIn("조립 검사 영문 사실 fda483-1", lines[0])
        self.assertIn("100", lines[0])

    def test_same_meaning_without_number_passes(self):
        self.assertEqual(self.pc.assembled_en_fact_lines({"fda483-1": {}}, self._out("No full reinspection after particles found")), [])

    def test_only_this_shards_cards(self):
        self.assertEqual(self.pc.assembled_en_fact_lines({}, self._out("No 100% reinspection")), [])

    def test_copyedit_splits_render_gate_outcomes(self):
        for rel in ("company/editorial/copyedit.md", "company/editorial/next/copyedit.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn('{"item": "렌더 게이트", "slot": "en"', text, rel)
            self.assertIn("`483 관찰 국문`·`WL 위반항목 국문`·`WHOPIR 국문` 이 하나라도 있으면 **`보류`**", text, rel)


if __name__ == "__main__":
    unittest.main()
