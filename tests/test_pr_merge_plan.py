import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pr_merge_plan as plan


SAMPLE_JSON = """
[
  {
    "number": 104, "title": "Pending checks", "baseRefName": "main",
    "headRefName": "pending", "isCrossRepository": false, "isDraft": false,
    "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}],
    "changedFiles": 1, "files": [{"path": "web/render.py"}]
  },
  {
    "number": 101, "title": "Stack child", "baseRefName": "parent",
    "headRefName": "child", "isCrossRepository": false, "isDraft": false,
    "statusCheckRollup": [{"state": "SUCCESS"}],
    "changedFiles": 1, "files": [{"path": "web/render.py"}]
  },
  {
    "number": 105, "title": "Draft", "baseRefName": "main",
    "headRefName": "draft", "isCrossRepository": false, "isDraft": true,
    "statusCheckRollup": [{"state": "SUCCESS"}],
    "changedFiles": 1, "files": [{"path": "draft.py"}]
  },
  {
    "number": 102, "title": "Stack parent", "baseRefName": "main",
    "headRefName": "parent", "isCrossRepository": false, "isDraft": false,
    "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
    "changedFiles": 2, "files": [{"path": "web/render.py"}, {"path": "web/templates/brief.html"}]
  },
  {
    "number": 103, "title": "Independent", "baseRefName": "main",
    "headRefName": "independent", "isCrossRepository": false, "isDraft": false,
    "statusCheckRollup": [{"state": "SUCCESS"}],
    "changedFiles": 1, "files": [{"path": "collect_intake.py"}]
  }
]
"""


class MergePlanTest(unittest.TestCase):
    def setUp(self):
        self.prs = json.loads(SAMPLE_JSON)

    def test_order_respects_stack_ci_draft_and_overlaps(self):
        rows, _ = plan.merge_plan(self.prs)
        self.assertEqual(
            [(number, ci, parent, rank) for number, ci, parent, rank, _ in rows],
            [
                (103, "PASS", None, 1),
                (102, "PASS", None, 2),
                (101, "PASS", 102, 3),
                (105, "PASS", None, None),
                (104, "PENDING", None, None),
            ],
        )
        self.assertEqual(plan.merge_plan(self.prs[::-1]), plan.merge_plan(self.prs))

    def test_overlaps_list_each_pair_and_every_shared_path_once(self):
        self.prs[1]["files"].append({"path": "web/templates/brief.html"})
        self.prs[1]["changedFiles"] = 2
        _, overlaps = plan.merge_plan(self.prs)
        self.assertEqual(
            dict(overlaps),
            {
                (101, 102): ["web/render.py", "web/templates/brief.html"],
                (101, 104): ["web/render.py"],
                (102, 104): ["web/render.py"],
            },
        )

    def test_failed_parent_holds_child_and_ready_independent_pr_still_ranks(self):
        self.prs[3]["statusCheckRollup"] = [{"conclusion": "FAILURE"}, {"state": "PENDING"}]
        rows, _ = plan.merge_plan(self.prs)
        self.assertEqual(rows[0][0:4], (103, "PASS", None, 1))
        by_number = {row[0]: row[1:] for row in rows}
        self.assertEqual(by_number[102][:3], ("FAIL", None, None))
        self.assertEqual(by_number[101], ("PASS", 102, None, "상위 PR #102 보류"))

    def test_fork_head_does_not_match_same_named_base(self):
        self.prs[3]["isCrossRepository"] = True
        rows, _ = plan.merge_plan(self.prs)
        child = next(row for row in rows if row[0] == 101)
        self.assertEqual(child[2:4], (None, 2))

    def test_ambiguous_parent_and_circular_stack_are_held(self):
        self.prs[4]["headRefName"] = "parent"
        rows, _ = plan.merge_plan(self.prs)
        child = next(row for row in rows if row[0] == 101)
        self.assertEqual(child[2:], (None, None, "상위 PR 확인 (#102, #103)"))

        self.prs[4]["headRefName"] = "independent"
        self.prs[3]["baseRefName"] = "child"
        rows, _ = plan.merge_plan(self.prs)
        self.assertEqual({row[0] for row in rows if row[4] == "스택 순환 확인"}, {101, 102})
        self.assertEqual(rows[0][0:4], (103, "PASS", None, 1))

    def test_no_checks_and_unrecognized_conclusions_stay_out_of_ranking(self):
        self.prs[4]["statusCheckRollup"] = []
        self.prs[3]["statusCheckRollup"] = [{"status": "COMPLETED", "conclusion": ""}]
        rows, _ = plan.merge_plan(self.prs)
        self.assertTrue(all(row[3] is None for row in rows))
        self.assertEqual({row[0]: row[1] for row in rows if row[0] in (102, 103)}, {102: "UNKNOWN", 103: "NONE"})

    def test_live_collection_reads_extra_file_pages(self):
        pr = self.prs[3]
        pr["files"] = pr["files"][:1]
        with patch.object(plan, "gh_json", side_effect=[
            [pr], [[{"filename": "web/render.py"}], [{"filename": "web/templates/brief.html"}]],
        ]) as gh:
            loaded = plan.load_open_prs()
        self.assertEqual(loaded[0]["files"], [
            {"path": "web/render.py"}, {"path": "web/templates/brief.html"},
        ])
        self.assertEqual(gh.call_args_list[0].args[:4], ("pr", "list", "--state", "open"))
        self.assertEqual(gh.call_args_list[1].args, (
            "api", "--paginate", "--slurp", "repos/{owner}/{repo}/pulls/102/files?per_page=100",
        ))

    def test_incomplete_file_list_is_rejected(self):
        self.prs[3]["files"] = self.prs[3]["files"][:1]
        with self.assertRaisesRegex(ValueError, "#102"):
            plan.render(self.prs)

    def test_offline_cli_renders_summary_without_gh(self):
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(SAMPLE_JSON)),
            patch.object(plan, "gh_json") as gh,
            redirect_stdout(output),
        ):
            self.assertEqual(plan.main(["--input", "-"]), 0)
        gh.assert_not_called()
        self.assertIn("| 1 | #103 | Independent | main | - | 아니요 | PASS | 머지 검토 |", output.getvalue())
        self.assertIn("| #101 / #102 | web/render.py |", output.getvalue())
