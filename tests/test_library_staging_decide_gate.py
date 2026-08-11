#!/usr/bin/env python3
"""grm-library-staging.yml 의 `decide` 게이트 판정 로직 테스트.

★왜 YAML 안의 셸을 굳이 꺼내 실행하는가: 이 저장소는 "인라인 스텝은 커버리지 0이 되기
쉽다"를 이미 겪었다(경고 사다리 수리 때 YAML 에서 스크립트를 뽑아 실행하는 관례가 생겼다).
게이트가 조용히 뒤집히면 자료실이 통째로 한 주를 거르거나 반대로 매일 돌아 PR 소음이 된다
— 둘 다 겉으로는 초록이라 안 보인다.

검사 대상 = 2026-08-11 신설 "주간 결손 복구" 분기. 종전 복구 모드는 **수집기가 실패해
격리 이슈가 열렸을 때**만 돌았는데, 자료실이 한 주를 거르는 더 흔한 경로는 실패가 아니라
**월요일 정기 실행이 아예 안 도는 것**이다(크론 지각으로 KST 화요일이 되어 요일 검사에
안 걸림·러너 장애 등). 그 경우 격리 이슈가 없어 종전 로직은 7일을 그대로 굳혔다.

스텁은 `gh` 와 `date` 둘뿐이다(워크플로가 외부 jq 를 쓰지 않고 gh 내장 --jq 를 쓰므로).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "grm-library-staging.yml"
_BASH = shutil.which("bash")


def _probe_gnu_date() -> str | None:
    """GNU date 의 **bash 안에서의** 경로를 찾는다.

    ★Path("/usr/bin/date").exists() 로 판정하면 Windows 에서 파이썬이 C:\\usr\\bin\\date 를
    보고 없다고 해 테스트 전체가 **조용히 skip** 된다 — 이 저장소가 두 번 당한 "초록 CI ≠
    실행됨" 함정 그대로다. 그래서 bash 에게 직접 물어보고 실제 계산까지 시켜 확인한다.
    """
    if not _BASH:
        return None
    try:
        found = subprocess.run(
            [_BASH, "-c", 'command -v date'],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace")
        path = (found.stdout or "").strip()
        if not path:
            return None
        check = subprocess.run(
            [_BASH, "-c", f'"{path}" -d "2026-08-11 -1 days" +%F'],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace")
        return path if (check.stdout or "").strip() == "2026-08-10" else None
    except (OSError, subprocess.SubprocessError):
        return None


_REAL_DATE = _probe_gnu_date()


def _decide_script() -> str:
    """워크플로에서 decide 스텝의 run 본문을 그대로 꺼낸다(복사본을 두지 않는다).

    ★PyYAML 을 쓰지 않는다 — **CI 테스트 환경에 PyYAML 이 없다**(requirements.txt 미포함).
    로컬에는 있어서 통과하고 CI 에서만 ImportError 로 터진다(실제로 그렇게 터졌다).
    `tests/test_workflow_flag_resolution.py` 가 같은 이유로 자체 파서를 쓰는 선례이고,
    아래 PyYamlParityTest 가 PyYAML 이 있는 환경에서 결과를 대조한다(파서가 틀리면 이
    테스트 전체가 조용히 무력해지므로).
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    try:
        anchor = next(i for i, ln in enumerate(lines) if ln.strip() == "id: check")
        start = next(i for i in range(anchor, len(lines))
                     if lines[i].strip() in ("run: |", "run: |-"))
    except StopIteration:  # pragma: no cover - 구조가 바뀌면 즉시 실패시킨다
        raise AssertionError("decide 잡에서 id=check 의 run 블록을 찾지 못했다") from None
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


def _crons() -> set[str]:
    """schedule 의 cron 목록(자체 파서 — 위와 같은 이유로 PyYAML 미사용)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]", text, re.M))


@unittest.skipUnless(_BASH and _REAL_DATE, "bash/GNU date 필요")
class DecideGateTest(unittest.TestCase):
    """실제 YAML 스크립트를 스텁 환경에서 돌려 run/reason 을 검사한다."""

    def _run(self, *, today: str, last: str, event: str = "schedule",
             quarantine_open: int = 0) -> tuple[str, str]:
        script = _decide_script()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binp = root / "bin"
            binp.mkdir()

            # gh 스텁 — issue list 는 격리 이슈 수를, api 는 generated_on 을 돌려준다.
            (binp / "gh").write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                f'  issue) echo "{quarantine_open}" ;;\n'
                '  api)   [ -n "$FAKE_LAST" ] && echo "$FAKE_LAST" || exit 1 ;;\n'
                "esac\n", encoding="utf-8")
            # date 스텁 — FAKE_TODAY 를 '오늘'로 삼고 나머지는 진짜 date 에 위임한다.
            (binp / "date").write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                f'  +%u) exec {_REAL_DATE} -d "$FAKE_TODAY" +%u ;;\n'
                f'  -d)  exec {_REAL_DATE} -d "$FAKE_TODAY $2" "$3" ;;\n'
                "esac\n"
                f'exec {_REAL_DATE} "$@"\n', encoding="utf-8")
            for name in ("gh", "date"):
                (binp / name).chmod(0o755)

            out = root / "out"
            out.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "PATH": f"{binp}{os.pathsep}{os.environ.get('PATH', '')}",
                "GITHUB_OUTPUT": str(out),
                "GRM_EVENT_NAME": event,
                "GRM_REPO": "owner/repo",
                "QUARANTINE_ISSUE_TITLE": "GRM 자료실 수집기 실패 — 소스 격리",
                "FAKE_TODAY": today,
                "FAKE_LAST": last,
            }
            # ★encoding 을 명시한다 — 한국어 reason 문자열을 로케일(cp949)로 읽으면
            #   UnicodeDecodeError 가 난다(이 저장소가 이미 겪은 함정).
            subprocess.run([_BASH, "-c", script], env=env, check=False,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
            parsed = dict(
                line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines()
                if "=" in line)
        return parsed.get("run", ""), parsed.get("reason", "")

    # 2026-08-10 = 월요일, 08-11 = 화요일 (실측 기준일)

    def test_monday_always_runs_regardless_of_history(self):
        run, reason = self._run(today="2026-08-17", last="2026-08-03")
        self.assertEqual(run, "true")
        self.assertIn("주간 정기", reason)

    def test_weekday_skips_when_this_week_already_refreshed(self):
        """정상: 이번 주 월요일에 이미 돌았으면 평일엔 건너뛴다(PR 소음 방지)."""
        run, reason = self._run(today="2026-08-11", last="2026-08-10")
        self.assertEqual(run, "false")
        self.assertIn("이번 주 갱신 완료", reason)

    def test_weekday_runs_when_this_week_was_missed(self):
        """★고치려는 결함: 월요일 실행이 없어 마지막 갱신이 지난주면 복구한다."""
        run, reason = self._run(today="2026-08-11", last="2026-08-03")
        self.assertEqual(run, "true")
        self.assertIn("주간 결손 복구", reason)

    def test_recovery_is_one_shot_once_it_succeeds(self):
        """복구가 돌아 generated_on 이 갱신되면 다음 날은 다시 돌지 않는다."""
        run, _ = self._run(today="2026-08-12", last="2026-08-11")
        self.assertEqual(run, "false")

    def test_recovery_retries_while_still_missing(self):
        """복구도 실패해 이력이 그대로면 남은 평일에 계속 시도한다(주 6회 상한)."""
        for today in ("2026-08-12", "2026-08-13", "2026-08-15"):
            run, _ = self._run(today=today, last="2026-08-03")
            self.assertEqual(run, "true", today)

    def test_boundary_last_equal_to_this_monday_is_not_missed(self):
        """경계: 마지막 갱신 == 이번 주 월요일이면 결손이 아니다."""
        run, _ = self._run(today="2026-08-14", last="2026-08-10")
        self.assertEqual(run, "false")

    def test_quarantine_still_wins_before_the_week_check(self):
        """격리 복구는 종전대로 먼저 판정한다 — 새 분기가 이걸 가리면 안 된다."""
        run, reason = self._run(today="2026-08-11", last="2026-08-10", quarantine_open=1)
        self.assertEqual(run, "true")
        self.assertIn("격리", reason)

    def test_unreadable_history_skips_instead_of_running_daily(self):
        """★이력을 못 읽을 때 '결손'으로 단정하면 매일 돌아 PR 소음이 된다 — 건너뛴다.
        월요일 정기 실행은 이 판정과 무관하므로 최악이어도 종전 동작이다."""
        run, reason = self._run(today="2026-08-11", last="")
        self.assertEqual(run, "false")
        self.assertIn("확인 실패", reason)

    def test_manual_dispatch_always_runs(self):
        run, reason = self._run(today="2026-08-11", last="2026-08-10",
                                event="workflow_dispatch")
        self.assertEqual(run, "true")
        self.assertIn("수동", reason)


class DecideGateContractTest(unittest.TestCase):
    """실행 없이도 계약을 고정한다(bash 없는 환경 대비)."""

    def test_history_marker_is_the_staging_diff_file(self):
        """별도 상태 파일을 새로 만들지 않는다 — 갱신마다 커밋되는 기존 산출물을 쓴다."""
        script = _decide_script()
        self.assertIn("web/data/library_staging_diff.json", script)
        self.assertIn("generated_on", script)

    def test_gate_has_no_external_jq_dependency(self):
        """gh 내장 --jq 만 쓴다(러너에 jq 가 있어도 의존을 늘리지 않는다)."""
        script = _decide_script()
        self.assertIn("--jq", script)
        self.assertNotIn("| jq", script)

    def test_recovery_cron_covers_the_non_monday_days(self):
        crons = _crons()
        self.assertIn("23 18 * * 0", crons, "주간 정기(일 18:23 UTC = 월 03:23 KST)")
        self.assertIn("23 18 * * 1-6", crons, "복구 모드(나머지 요일)")

    def test_no_module_level_pyyaml_dependency(self):
        """★CI 에는 PyYAML 이 없다. 모듈 최상단에서 import 하면 **CI 에서만** ImportError 로
        터지고 로컬은 통과한다(실제로 그렇게 터졌다). 자체 파서를 쓰는 규율을 못 박는다."""
        src = Path(__file__).read_text(encoding="utf-8")
        top_level = [ln for ln in src.splitlines()
                     if re.match(r"^(import yaml|from yaml\b)", ln)]
        self.assertEqual(top_level, [], "모듈 최상단 PyYAML import 금지")

    def test_execution_suite_is_not_silently_skipped_on_linux(self):
        """★skip 은 초록으로 보인다. CI(리눅스)에서 실행 스위트가 통째로 건너뛰어지면
        게이트 로직에 커버리지가 0인데 아무도 모른다 — 이 저장소가 두 번 당한 함정이다.
        리눅스에서는 bash·GNU date 가 반드시 있으므로 없으면 그건 환경 결함이다.
        (Windows 로컬은 git-bash 가 없을 수 있어 skip 을 허용한다.)"""
        if os.name != "posix":
            self.skipTest("POSIX 아님 — CI(ubuntu)에서 강제된다")
        self.assertTrue(_BASH, "리눅스인데 bash 를 못 찾았다")
        self.assertTrue(_REAL_DATE, "리눅스인데 GNU date 로 날짜 계산을 못 했다")


class PyYamlParityTest(unittest.TestCase):
    """자체 파서가 PyYAML 과 같은 결과를 내는지 — PyYAML 이 있는 환경에서만 돈다.

    CI 는 PyYAML 이 없어 자체 파서 단독으로 검사한다. 그래서 **파서가 틀리면 이 파일의
    가드 전체가 조용히 무력해진다**. 개발 환경(로컬)에는 PyYAML 이 있으므로 여기서 붙잡는다.
    (`tests/test_workflow_flag_resolution.StdlibParserAgreesWithPyYamlTest` 와 같은 관례.)
    """

    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML 없음(CI) — 자체 파서 단독 동작")

    def test_run_block_matches_pyyaml(self):
        import yaml
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        step = next(s for s in data["jobs"]["decide"]["steps"] if s.get("id") == "check")
        self.assertEqual(_decide_script().strip(), str(step["run"]).strip())

    def test_cron_list_matches_pyyaml(self):
        import yaml
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML 은 YAML 1.1 이라 `on:` 키를 boolean True 로 읽는다(알려진 함정).
        schedule = (data.get("on") or data.get(True))["schedule"]
        self.assertEqual(_crons(), {entry["cron"] for entry in schedule})


if __name__ == "__main__":
    unittest.main()
