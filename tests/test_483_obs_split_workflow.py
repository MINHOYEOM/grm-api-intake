"""grm-483-obs-split.yml 정적 검사 — dry 기본·apply 는 입력 조건부·dispatch 전용·서비스키 비노출."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "grm-483-obs-split.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text() -> str:
    return WF.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    """주석 줄을 뺀 본문."""
    return [x for x in _text().splitlines() if not x.lstrip().startswith("#")]


def _block(top_key: str) -> str:
    """최상위 키(들여쓰기 0) 블록 본문."""
    lines = _code_lines()
    out, on = [], False
    for x in lines:
        if re.match(r"^\S", x):
            on = x.startswith(top_key + ":")
            continue
        if on:
            out.append(x)
    return "\n".join(out)


class ObsSplitWorkflowTest(unittest.TestCase):
    def test_trigger_is_dispatch_only(self) -> None:
        on = _block("on")
        self.assertIn("workflow_dispatch:", on)
        for bad in ("schedule", "push", "pull_request", "workflow_run", "repository_dispatch"):
            self.assertNotRegex(on, rf"(?m)^\s+{bad}\s*:", f"트리거에 {bad} 가 있다")

    def test_apply_input_defaults_false_boolean(self) -> None:
        on = _block("on")
        m = re.search(r"(?m)^\s+apply:\s*\n((?:\s{8,}.*\n?)+)", on)
        self.assertIsNotNone(m, "apply 입력이 없다")
        body = m.group(1)
        self.assertRegex(body, r"type:\s*boolean")
        self.assertRegex(body, r"default:\s*false\s*$|default:\s*false\s*\n", "apply 기본이 false 가 아니다")

    def test_apply_flag_only_conditional_on_input(self) -> None:
        text = "\n".join(_code_lines())
        hits = [m.start() for m in re.finditer(r"--apply\b", text)]
        self.assertEqual(len(hits), 1, "--apply 는 정확히 한 곳(조건부)에만")
        before = text[:hits[0]].splitlines()[-3:]
        self.assertTrue(any('"${IN_APPLY}" = "true"' in x for x in before),
                        "--apply 가 IN_APPLY=true 조건 밖에 있다")
        self.assertRegex(text, r"IN_APPLY:\s*\$\{\{\s*inputs\.apply\s*\}\}")

    def test_apply_guard_main_only(self) -> None:
        text = "\n".join(_code_lines())
        self.assertIn("refs/heads/main", text)

    def test_artifact_upload_present(self) -> None:
        text = "\n".join(_code_lines())
        self.assertIn("actions/upload-artifact@", text)
        self.assertIn("obs_split_report.json", text)
        self.assertRegex(text, r"retention-days:\s*\d+")
        # 실패해도 보관(always)
        m = re.search(r"(?s)- name: Upload report artifact\s*\n\s*if:\s*(.*?)\n", text)
        self.assertIsNotNone(m)
        self.assertIn("always()", m.group(1))

    def test_secret_names_and_no_key_echo(self) -> None:
        text = "\n".join(_code_lines())
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}", text)
        self.assertIn("SUPABASE_URL: ${{ vars.SUPABASE_URL }}", text)
        # 키는 secrets 표현식 한 곳에서만 나오고, run 안에서 $ 참조·set -x 가 없다
        self.assertEqual(len(re.findall(r"secrets\.", text)), 1)
        self.assertNotRegex(text, r"\$\{?SUPABASE_SERVICE_ROLE_KEY")
        self.assertNotRegex(text, r"(?m)^\s*set\s+-\w*x")
        self.assertNotRegex(text, r"--service-role-key")

    def test_no_dispatch_input_interpolated_into_shell(self) -> None:
        """run 본문에는 ${{ }} 표현식이 없어야 한다(입력은 env 로만)."""
        lines = _code_lines()
        in_run, indent = False, 0
        for x in lines:
            s = x.strip()
            if s.startswith("run:"):
                in_run, indent = True, len(x) - len(x.lstrip())
                if "${{" in x:
                    self.fail(f"run 한 줄에 표현식: {x}")
                continue
            if in_run:
                if s and len(x) - len(x.lstrip()) <= indent:
                    in_run = False
                elif "${{" in x:
                    self.fail(f"run 본문에 표현식: {x}")

    def test_permissions_minimal(self) -> None:
        perms = _block("permissions").strip().splitlines()
        self.assertEqual([p.strip() for p in perms], ["contents: read"])

    def test_concurrency_and_timeout(self) -> None:
        text = "\n".join(_code_lines())
        self.assertRegex(text, r"(?m)^concurrency:")
        self.assertRegex(text, r"cancel-in-progress:\s*false")
        self.assertRegex(text, r"timeout-minutes:\s*\d+")

    def test_actions_pinned_to_sha(self) -> None:
        uses = re.findall(r"(?m)^\s*uses:\s*(\S+)", "\n".join(_code_lines()))
        self.assertGreaterEqual(len(uses), 3)
        for u in uses:
            self.assertIn("@", u)
            self.assertRegex(u.split("@", 1)[1], SHA_RE, f"SHA 핀 아님: {u}")

    def test_tool_invoked_with_report_path(self) -> None:
        text = "\n".join(_code_lines())
        self.assertIn("python backfill_483_obs_split.py", text)
        self.assertIn("--report-path", text)
        self.assertIn("--doc-ids=", text)


if __name__ == "__main__":
    unittest.main()
