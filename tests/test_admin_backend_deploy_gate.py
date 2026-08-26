#!/usr/bin/env python3
"""grm-admin-backend-deploy.yml 의 `db push` 관용 분기 테스트.

★왜 YAML 안의 셸을 꺼내 실행하는가: 이 저장소는 "인라인 스텝은 커버리지 0이 되기 쉽다"를
이미 겪었다(`tests/test_library_staging_decide_gate.py` 선례). 그리고 이 스텝은 **6주에 한 번
돌까 말까 한다** — `supabase/**` 가 바뀔 때만 도는데, 2026-07-13 이후 첫 실행이 2026-08-26
이었다. 그 사이에 조용히 깨져 있었고, 깨진 채로 **Edge Function 배포를 통째로 취소**했다.
자주 안 도는 경로일수록 테스트가 유일한 감시자다.

검사 대상 = 마이그레이션 채널이 둘이라 생기는 드리프트의 관용 처리:
  ⓐ supabase/migrations/  — CLI 관리(이 스텝이 미는 대상)
  ⓑ web/migrations/       — 사람이 SQL 에디터로 직접 적용 → 원격 이력에 CLI 가 모르는 버전
ⓑ 때문에 db push 가 "Remote migration versions not found..." 로 거부하는 것은 배포 실패가
아니다. 그 경우만 경고로 통과하고, **그 밖의 실패는 종전대로 막아야 한다** — 관용이 넓어지면
진짜 마이그레이션 오류가 조용히 배포되므로 아래 음성 검사가 그 경계를 고정한다.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "grm-admin-backend-deploy.yml"
_BASH = shutil.which("bash")

DRIFT_MSG = "Remote migration versions not found in local migrations directory."


def _push_script() -> str:
    """워크플로에서 db push 스텝의 run 본문을 그대로 꺼낸다(복사본을 두지 않는다).

    PyYAML 미사용 — CI 테스트 환경에 없다(선례와 같은 이유·requirements.txt 미포함).
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    try:
        anchor = next(i for i, ln in enumerate(lines) if ln.strip() == "id: dbpush")
        start = next(i for i in range(anchor, len(lines))
                     if lines[i].strip() in ("run: |", "run: |-"))
    except StopIteration:  # pragma: no cover - 구조가 바뀌면 즉시 실패시킨다
        raise AssertionError("id=dbpush 스텝의 run 블록을 찾지 못했다") from None
    key_indent = len(lines[start]) - len(lines[start].lstrip())
    body: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip():
            body.append("")
            continue
        if len(line) - len(line.lstrip()) <= key_indent:
            break
        body.append(line)
    widths = [len(ln) - len(ln.lstrip()) for ln in body if ln.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(ln[cut:] if ln.strip() else "" for ln in body)


@unittest.skipUnless(_BASH, "bash 필요")
class AdminBackendDbPushGateTest(unittest.TestCase):
    """실제 YAML 스크립트를 `supabase` 스텁 환경에서 돌려 종료코드를 검사한다."""

    def _run(self, *, stub_out: str, stub_rc: int) -> subprocess.CompletedProcess:
        script = _push_script()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binp = root / "bin"
            binp.mkdir()
            # supabase 스텁 — db push 의 출력과 종료코드를 흉내낸다.
            # 출력은 stderr 로 낸다(CLI 가 오류를 stderr 로 내고, 스텝은 2>&1 로 합친다).
            (binp / "supabase").write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$FAKE_OUT\" >&2\n"
                f"exit {stub_rc}\n",
                encoding="utf-8")
            (binp / "supabase").chmod(0o755)
            env = {
                "PATH": f"{binp}:/usr/bin:/bin",
                "SUPABASE_DB_PASSWORD": "pw",
                "FAKE_OUT": stub_out,
                "GITHUB_OUTPUT": str(root / "gh_out"),
            }
            return subprocess.run(
                [_BASH, "-c", script], capture_output=True, text=True,
                env=env, timeout=60, encoding="utf-8", errors="replace")

    def test_success_passes(self):
        r = self._run(stub_out="Remote database is up to date.", stub_rc=0)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_history_drift_is_tolerated_with_warning(self):
        """web/migrations 수동 적용분 때문에 생긴 드리프트는 배포를 막지 않는다."""
        r = self._run(stub_out=DRIFT_MSG, stub_rc=1)
        self.assertEqual(r.returncode, 0, "드리프트에서 배포가 막혔다: " + r.stdout + r.stderr)
        self.assertIn("::warning::", r.stdout + r.stderr, "드리프트를 조용히 삼켰다(경고 없음)")

    def test_real_failure_still_blocks(self):
        """음성 검사 — 관용이 넓어지면 진짜 오류가 조용히 배포된다.

        이 검사가 없으면 위 관용 분기를 `exit 0` 하나로 바꿔도 테스트가 통과한다."""
        for out, rc in (
            ("ERROR: syntax error at or near \"creat\" (SQLSTATE 42601)", 1),
            ("failed to connect to postgres: password authentication failed", 1),
            ("Error: Access token not provided.", 2),
        ):
            with self.subTest(out=out):
                r = self._run(stub_out=out, stub_rc=rc)
                self.assertNotEqual(r.returncode, 0, "진짜 실패가 통과했다: " + out)

    def test_edge_function_deploy_is_not_gated_on_db_push_outcome(self):
        """배포 스텝은 db push 의 성공 여부가 아니라 시크릿 준비 상태에만 걸린다 —
        두 관심사를 묶으면 이번 사고(함수와 무관한 이유로 배포 취소)가 되풀이된다."""
        text = WORKFLOW.read_text(encoding="utf-8")
        block = text.split("Deploy Admin Edge Functions", 1)[1].split("run:", 1)[0]
        self.assertIn("steps.ref.outputs.ready == 'true'", block)
        self.assertNotIn("steps.dbpush", block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
