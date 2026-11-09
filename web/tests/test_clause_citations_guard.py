#!/usr/bin/env python3
"""월간 갱신 가드(web/clause_citations_guard.py) + 워크플로 정적 검증.

CI 는 `tests/test_web_clause_citations_guard.py` shim 으로 순회한다. 직접:
  python web/tests/test_clause_citations_guard.py
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
import clause_citations as cc  # noqa: E402
import clause_citations_guard as g  # noqa: E402

WF = REPO / ".github" / "workflows" / "grm-fda-citations-snapshot.yml"


def _snap(n_insp=2000, n_codes=60):
    rows = []
    i = 0
    for k in range(n_codes):
        for _ in range(35):
            i += 1
            rows.append({"inspection_id": str(i % n_insp), "fiscal_year": 2024 + (i % 2),
                         "act_cfr_number": f"21 CFR 211.{100 + k}", "short_description": "t"})
    return cc.build_snapshot(rows, as_of="2026-11-01")


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.old = _snap()

    def test_identical_except_as_of_is_unchanged(self):
        new = copy.deepcopy(self.old)
        new["as_of"] = "2026-12-01"
        self.assertEqual(g.judge(self.old, new)[0], g.UNCHANGED)

    def test_real_change_is_ok(self):
        new = copy.deepcopy(self.old)
        new["codes"][0]["citations"] += 1
        self.assertEqual(g.judge(self.old, new)[0], g.OK)

    def test_first_snapshot_ok(self):
        self.assertEqual(g.judge(None, self.old)[0], g.OK)

    def test_codes_drop_20pct_abnormal(self):
        new = copy.deepcopy(self.old)
        keep = len(new["codes"]) * 80 // 100  # 정확히 -20%
        new["codes"] = new["codes"][:keep]
        code, why = g.judge(self.old, new)
        self.assertEqual(code, g.ABNORMAL)
        self.assertTrue(any(w.startswith("codes") for w in why))

    def test_drop_just_under_threshold_ok(self):
        new = copy.deepcopy(self.old)
        new["codes"] = new["codes"][:len(new["codes"]) - 2]  # 60 -> 58 = -3.3%
        self.assertEqual(g.judge(self.old, new)[0], g.OK)

    def test_inspection_total_collapse_abnormal(self):
        new = copy.deepcopy(self.old)
        new["inspections"]["total"] = self.old["inspections"]["total"] // 2
        # 검증(over_total)이 먼저 잡거나 감소 가드가 잡는다 - 어느 쪽이든 비정상.
        self.assertEqual(g.judge(self.old, new)[0], g.ABNORMAL)

    def test_invalid_new_abnormal(self):
        self.assertEqual(g.judge(self.old, {"schema": 99})[0], g.ABNORMAL)
        self.assertEqual(g.judge(self.old, [])[0], g.ABNORMAL)
        empty = copy.deepcopy(self.old)
        empty["codes"] = []
        self.assertEqual(g.judge(self.old, empty)[0], g.ABNORMAL)

    def test_year_window_going_backwards_abnormal(self):
        new = copy.deepcopy(self.old)
        new["years"] = [y - 1 for y in new["years"]]
        self.assertEqual(g.judge(self.old, new)[0], g.ABNORMAL)

    def test_min_inspections_change_abnormal(self):
        new = copy.deepcopy(self.old)
        new["min_inspections"] = 5
        self.assertEqual(g.judge(self.old, new)[0], g.ABNORMAL)

    def test_cli_exit_codes_and_no_write(self):
        with tempfile.TemporaryDirectory() as t:
            o, n = pathlib.Path(t, "o.json"), pathlib.Path(t, "n.json")
            o.write_text(json.dumps(self.old), encoding="utf-8")
            same = copy.deepcopy(self.old)
            same["as_of"] = "2027-01-01"
            n.write_text(json.dumps(same), encoding="utf-8")
            self.assertEqual(g.main(["--old", str(o), "--new", str(n)]), 10)
            n.write_text("{broken", encoding="utf-8")
            self.assertEqual(g.main(["--old", str(o), "--new", str(n)]), 1)

    def test_committed_snapshot_is_deterministic_and_self_unchanged(self):
        """커밋본은 검증을 통과하고 자기 자신과 비교하면 '변경 없음'(= 정렬 고정)."""
        data = json.loads(cc.DATA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(cc.validate_snapshot(data), [])
        self.assertEqual(g.judge(data, copy.deepcopy(data))[0], g.UNCHANGED)

    def test_build_is_order_independent(self):
        rows = [{"inspection_id": str(i % 40), "fiscal_year": 2025,
                 "act_cfr_number": f"21 CFR 211.{i % 3}", "short_description": "a" if i % 2 else "b"}
                for i in range(200)]
        a = cc.build_snapshot(rows, as_of="x", min_inspections=1)
        b = cc.build_snapshot(list(reversed(rows)), as_of="x", min_inspections=1)
        self.assertEqual(json.dumps(a), json.dumps(b))


class WorkflowStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WF.read_text(encoding="utf-8")

    def test_cron_and_dispatch(self):
        self.assertRegex(self.text, r"cron:\s*'\d+ \d+ \d+ \* \*'")  # 월 1회(일자 고정)
        self.assertIn("workflow_dispatch:", self.text)

    def test_no_auto_merge(self):
        self.assertNotIn("gh pr merge", self.text)

    def test_reuses_existing_secrets_only(self):
        used = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.text))
        self.assertEqual(used, {"SUPABASE_SERVICE_ROLE_KEY", "GRM_PUBLISH_TOKEN",
                                "ADMIN_GITHUB_ACTIONS_TOKEN"})
        for name in used:
            self.assertTrue(
                any(name in p.read_text(encoding="utf-8")
                    for p in WF.parent.glob("*.yml") if p != WF),
                f"{name}: 다른 워크플로에서 안 쓰는 새 secret")

    def test_guard_runs_before_pr_and_writes_temp_first(self):
        t = self.text
        self.assertLess(t.index("clause_citations_guard.py"), t.index("gh pr create"))
        self.assertIn("--out \"$RUNNER_TEMP/", t)
        self.assertIn("steps.guard.outputs.changed == 'true'", t)
        self.assertIn("exit 1", t)  # 비정상 -> 실패

    def test_guard_maps_exit_codes(self):
        self.assertIn('"$RC" -eq 10', self.text)

    def test_no_dry_run_input_three_state_trap(self):
        """schedule 이벤트엔 inputs 가 빈 문자열 - 기본값은 `||` 로 채운다."""
        self.assertIn("${{ inputs.max_drop_pct || '20' }}", self.text)

    def test_body_file_and_failure_issue(self):
        self.assertIn("--body-file", self.text)
        self.assertIn("automation-failure", self.text)

    def test_actions_pinned_by_sha(self):
        for ln in self.text.splitlines():
            m = re.search(r"uses:\s*(\S+)@(\S+)", ln)
            if m:
                self.assertRegex(m.group(2), r"^[0-9a-f]{40}$", ln)

    def test_yaml_parses(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("PyYAML 없음")
        doc = yaml.safe_load(self.text)
        self.assertIn("refresh", doc["jobs"])


if __name__ == "__main__":
    unittest.main()
