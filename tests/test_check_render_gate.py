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


if __name__ == "__main__":
    unittest.main()
