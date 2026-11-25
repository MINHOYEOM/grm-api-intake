import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import orphan_report


class OrphanReportTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, relative, content=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def workflow(self, filename, content):
        self.write(f".github/workflows/{filename}", content)

    def test_root_scripts_referenced_by_workflows_python_and_company(self):
        used = ("cli", "via_import", "via_from", "dynamic", "via_path", "via_playbook", "via_module")
        for name in (*used, "unused", "self_only", "similar"):
            self.write(f"{name}.py", "# self_only.py\n" if name == "self_only" else "")
        self.workflow("caller.yml", 'on: push\njobs: {job: {steps: [{run: python ./cli.py}]}}\n')
        self.write(
            "nested/caller.py",
            'import via_import as helper\nfrom via_from import run\n'
            'import importlib\nimportlib.import_module("dynamic")\n'
            'path = "./via_path.py"\nother = "not_similar.py"\n'
            'backup = "unused.py.bak"\n',
        )
        self.write(
            "company/editorial/RUN.md",
            "Run via_playbook.py.\n`python -B -m via_module`\n`git commit -m unused`\n",
        )
        self.write("nested/standalone.py")
        self.write("docs/notes.md", "unused.py")
        self.assertEqual(orphan_report.find_orphans(self.root), ([
            "self_only.py", "similar.py", "unused.py",
        ], []))

    def test_workflow_events_and_calls(self):
        self.workflow("cron.yml", "on:\n  schedule:\n    - cron: '0 2 * * *'\n")
        self.workflow("manual.yml", '"on": workflow_dispatch\n')
        self.workflow("repository.yaml", "on: [repository_dispatch]\n")
        self.workflow("push.yml", "on: {push: {branches: [main]}}\n")
        self.workflow("listener.yml", "on: {workflow_run: {workflows: [Build]}}\n")
        self.workflow("called.yml", "on: {workflow_call: {}}\n")
        self.workflow("cli.yml", "name: CLI target\non: []\n")
        self.workflow("rest.yml", "on: []\n")
        self.workflow("api.yml", "on: []\n")
        self.workflow("unused.yaml", "on: workflow_call\n# schedule: comment\n")
        self.workflow("self.yml", """
on: workflow_call
jobs:
  loop:
    uses: ./.github/workflows/self.yml
""")
        self.workflow("caller.yml", """
on: [push, pull_request]
jobs:
  reuse:
    uses: ./.github/workflows/called.yml
  dispatch:
    steps:
      - run: |
          gh workflow run "CLI target"
          gh api repos/owner/repo/actions/workflows/rest.yml/dispatches
      - uses: actions/github-script@v7
        with:
          script: |
            github.rest.actions.createWorkflowDispatch({workflow_id: "api.yml"})
""")
        self.assertEqual(orphan_report.find_orphans(self.root), ([], [
            ".github/workflows/self.yml", ".github/workflows/unused.yaml",
        ]))

    def test_inline_jobs_recognize_only_job_level_workflow_calls(self):
        for name in ("inline", "multiline", "mixed", "next-line", "nested", "commented"):
            self.workflow(f"{name}.yml", "on: workflow_call\n")
        self.workflow("inline-caller.yml", """
on: push
jobs: {first: {uses: ./.github/workflows/inline.yml},
       second: { # uses: ./.github/workflows/commented.yml
         'uses': "./.github/workflows/multiline.yml"},
       normal: {steps: [{uses: ./.github/workflows/nested.yml}]}}
""")
        self.workflow("mixed-caller.yml", """
on: push
jobs:
  reuse: {
    uses: ./.github/workflows/mixed.yml}
""")
        self.workflow("next-line-caller.yml", """
on: push
jobs:
  {reuse: {uses: ./.github/workflows/next-line.yml}}
""")
        self.assertEqual(orphan_report.find_orphans(self.root), ([], [
            ".github/workflows/commented.yml", ".github/workflows/nested.yml",
        ]))

    def test_cli_prints_markdown_and_leaves_tree_unchanged(self):
        self.write("idle.py")
        self.workflow("idle.yaml", "on: workflow_call\n")
        original = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        output = io.StringIO()
        with patch.object(sys, "argv", ["orphan_report.py", str(self.root)]):
            with contextlib.redirect_stdout(output):
                orphan_report.main()
        self.assertIn("| 스크립트 | `idle.py` |", output.getvalue())
        self.assertIn("| 워크플로 | `.github/workflows/idle.yaml` |", output.getvalue())
        self.assertEqual(
            {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()},
            original,
        )
