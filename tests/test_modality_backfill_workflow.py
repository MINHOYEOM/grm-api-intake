"""grm-raw-signals-modality-backfill.yml 의 입력 검사 — 빈 source 는 '전부'로 통과해야 한다.

run 43146592319(2026-11-12): source 를 비우면 `printf '' | grep -qE` 가 1 을 돌려줘 "허용되지 않는
문자"로 거부됐다. 워크플로의 run 스크립트를 그대로 꺼내 bash 로 돌려 본다(python 호출만 인자 출력으로 바꿈).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "grm-raw-signals-modality-backfill.yml"
PY_CALL = 'python backfill_raw_signals_modality.py "${args[@]}"'


def run_script() -> str:
    lines = WF.read_text(encoding="utf-8").splitlines()
    start = next(i for i, x in enumerate(lines) if x.strip() == "- name: Backfill (dry-run unless apply=true)")
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run_at + 1]) - len(lines[run_at + 1].lstrip())
    body = []
    for x in lines[run_at + 1:]:
        if x.strip() and len(x) - len(x.lstrip()) < indent:
            break
        body.append(x[indent:])
    script = "\n".join(body)
    assert PY_CALL in script, "python 호출 줄이 바뀌었다 — 시험의 치환도 맞춰라"
    return script.replace(PY_CALL, 'printf "ARG:%s\\n" "${args[@]}"')


@unittest.skipUnless(shutil.which("bash"), "bash 없음")
class ModalityWorkflowInputTest(unittest.TestCase):
    def _run(self, source: str, limit: str = "", apply: str = "false") -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, IN_SOURCE=source, IN_LIMIT=limit, IN_APPLY=apply,
                       GITHUB_STEP_SUMMARY=str(Path(tmp) / "summary.md"))
            # 스크립트는 stdin 으로(Windows 명령줄 인자는 한글을 코드 페이지로 바꿔 깨뜨린다).
            # 찾은 경로 그대로(Windows 에서 이름만 주면 System32 의 WSL bash 가 잡힐 수 있다).
            res = subprocess.run([shutil.which("bash") or "bash", "-s"], input=run_script().encode("utf-8"), env=env,
                                 cwd=tmp, capture_output=True, timeout=60)
            return subprocess.CompletedProcess(
                res.args, res.returncode, res.stdout.decode("utf-8", "replace"),
                res.stderr.decode("utf-8", "replace"))

    def test_empty_source_means_all(self) -> None:
        res = self._run("")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("ARG:--source", res.stdout)

    def test_named_source_is_passed(self) -> None:
        res = self._run("FDA 483", apply="true", limit="50")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("ARG:--source\nARG:FDA 483", res.stdout)
        self.assertIn("ARG:--apply", res.stdout)
        self.assertIn("ARG:--limit\nARG:50", res.stdout)

    def test_bad_characters_and_limit_rejected(self) -> None:
        self.assertEqual(self._run("FDA; rm -rf /").returncode, 2)
        self.assertEqual(self._run("FDA 483", limit="5x").returncode, 2)


if __name__ == "__main__":
    unittest.main()
