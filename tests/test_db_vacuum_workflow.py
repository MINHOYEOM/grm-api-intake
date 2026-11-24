"""grm-db-vacuum.yml 의 허용 목록 검사 — 목록 밖 표는 거부, 순서는 findings 마지막, 스케줄 없음."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "grm-db-vacuum.yml"


def block(name: str) -> str:
    lines = WF.read_text(encoding="utf-8").splitlines()
    start = next(i for i, x in enumerate(lines) if x.strip() == f"- name: {name}")
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run_at + 1]) - len(lines[run_at + 1].lstrip())
    body = []
    for x in lines[run_at + 1:]:
        if x.strip() and len(x) - len(x.lstrip()) < indent:
            break
        body.append(x[indent:])
    return "\n".join(body)


@unittest.skipUnless(shutil.which("bash"), "bash 없음")
class DbVacuumWorkflowTest(unittest.TestCase):
    def _run(self, tables: str):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.write_text("")
            env = dict(os.environ, IN_TABLES=tables, GITHUB_OUTPUT=str(out))
            r = subprocess.run(["bash"], input=block("Validate tables (allow-list)"), env=env,
                               capture_output=True, text=True, encoding="utf-8")
            return r, out.read_text()

    def test_findings_sorted_last(self):
        r, out = self._run("findings, rpc_snapshot,raw_signals")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("list=rpc_snapshot raw_signals findings", out)

    def test_rejects_unknown_and_injection(self):
        for bad in ("users", "findings;drop table findings", "", "public.findings"):
            r, _ = self._run(bad)
            self.assertNotEqual(r.returncode, 0, bad)

    def test_no_schedule_and_dry_run_default(self):
        text = WF.read_text(encoding="utf-8")
        self.assertNotIn("schedule:", text)
        self.assertIn("default: true", text)


if __name__ == "__main__":
    unittest.main()
