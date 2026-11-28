"""grm-mfds-recollect-attachment.yml — dispatch 전용·apply 기본 false·프록시 없으면 시작 전 실패·ids 검사."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "grm-mfds-recollect-attachment.yml"
PY_CALL = 'python recollect_mfds_gmp_attachment.py "${args[@]}"'


def _step_script(name: str) -> str:
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


class StaticShapeTest(unittest.TestCase):
    def setUp(self):
        self.text = WF.read_text(encoding="utf-8")

    def test_dispatch_only(self):
        self.assertIn("workflow_dispatch:", self.text)
        for trigger in ("\n  push:", "\n  pull_request", "\n  schedule:", "\n  workflow_run:"):
            self.assertNotIn(trigger, self.text)

    def test_apply_defaults_false(self):
        block = self.text.split("      apply:")[1].split("permissions:")[0]
        self.assertIn("type: boolean", block)
        self.assertIn("default: false", block)

    def test_shares_nedrug_concurrency_group(self):
        self.assertIn("group: mfds-backfill", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_required_env(self):
        for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "MFDS_HTTP_PROXY",
                    "ENABLE_GMP_DEFICIENCY_TABLE: 'true'"):
            self.assertIn(key, self.text)

    def test_inputs_not_interpolated_into_shell(self):
        self.assertNotIn("${{", _step_script("Recollect (dry unless apply=true)"))

    def test_probe_runs_before_recollect(self):
        self.assertLess(self.text.index("probe_mfds_egress.py"),
                        self.text.index("python recollect_mfds_gmp_attachment.py"))

    def test_artifact_uploaded_even_on_failure(self):
        tail = self.text.split("결과 업로드")[1]
        self.assertIn("if: always()", tail)


@unittest.skipUnless(shutil.which("bash"), "bash 없음")
class ScriptBehaviourTest(unittest.TestCase):
    def _run(self, name: str, **env):
        script = _step_script(name)
        script = script.replace(PY_CALL, 'printf "ARG:%s\\n" "${args[@]}"')
        return subprocess.run(["bash"], input=script, capture_output=True, text=True,
                              encoding="utf-8", env=dict(os.environ, **env))

    def test_empty_proxy_fails_before_any_network(self):
        r = self._run("프록시 도달성 선점검", MFDS_HTTP_PROXY="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("MFDS_HTTP_PROXY", r.stdout)

    def test_dry_by_default(self):
        r = self._run("Recollect (dry unless apply=true)", IN_IDS="1PvoSBbO1to", IN_APPLY="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ARG:--apply", r.stdout)
        self.assertIn("ARG:1PvoSBbO1to", r.stdout)

    def test_apply_true_adds_flag(self):
        r = self._run("Recollect (dry unless apply=true)", IN_IDS="a,b", IN_APPLY="true")
        self.assertIn("ARG:--apply", r.stdout)

    def test_empty_and_bad_ids_rejected(self):
        for bad in ("", "a;rm -rf /", "a$(x)", "a b`c`"):
            r = self._run("Recollect (dry unless apply=true)", IN_IDS=bad, IN_APPLY="false")
            self.assertEqual(r.returncode, 2, bad)
            self.assertNotIn("ARG:", r.stdout)


if __name__ == "__main__":
    unittest.main()
