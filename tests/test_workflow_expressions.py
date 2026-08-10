"""GitHub Actions 표현식 문법 가드 — 파싱 불가 워크플로를 CI 에서 잡는다.

2026-08-10: 자료실 재시도 스텝에 재시도 회차를 1 올리려고 표현식 안에서 산술을 썼다.
YAML 로는 멀쩡히 파싱되고 로컬 테스트도 전부 통과했지만, **Actions 표현식에는 산술
연산자가 없다** — 워크플로 전체가 파싱 불가가 됐고 그 사실은 CI 가 아니라 dispatch 의
HTTP 422 로 드러났다. 그 사이 예약 실행도 같이 죽는다.

교훈은 "YAML 이 파싱된다 ≠ Actions 가 받아들인다" 다. 이 가드는 그 틈을 메운다.

지원되는 연산자는 `( ) [ ] . ! < <= > >= == != && ||` 뿐이다(산술 없음).
문자열 리터럴 안의 문자는 연산자가 아니므로 먼저 걷어낸다 — `format('{0}/{1}', …)` 의
슬래시나 `needs.validate-build` 의 하이픈을 오탐하면 가드가 못 쓰게 된다.
"""
from __future__ import annotations

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")

_EXPR_RE = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_QUOTED_RE = re.compile(r"'[^']*'")
# `+ * / %` 는 그대로 산술. `-` 는 식별자에 쓰이므로(`needs.validate-build`)
# **공백으로 둘러싸인 경우만** 산술로 본다.
_ARITH_RE = re.compile(r"[+*/%]|\s-\s")


def _workflow_files() -> list[str]:
    return sorted(
        os.path.join(WORKFLOW_DIR, n)
        for n in os.listdir(WORKFLOW_DIR)
        if n.endswith((".yml", ".yaml"))
    )


def _expressions() -> list[tuple[str, str]]:
    """(워크플로 파일명, 표현식 본문) 목록."""
    found: list[tuple[str, str]] = []
    for path in _workflow_files():
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        for match in _EXPR_RE.finditer(body):
            found.append((os.path.basename(path), match.group(1)))
    return found


class WorkflowExpressionTest(unittest.TestCase):

    def test_scan_finds_expressions(self) -> None:
        """스캔이 비면 정규식이 낡은 것이다 — 빈 결과를 통과로 읽지 않는다."""
        self.assertGreater(len(_expressions()), 50,
                           "워크플로 표현식 스캔이 비었거나 너무 적다")

    def test_no_arithmetic_in_actions_expressions(self) -> None:
        offenders = [
            f"{name}: ${{{{{expr.strip()[:80]}}}}}"
            for name, expr in _expressions()
            if _ARITH_RE.search(_QUOTED_RE.sub("", expr))
        ]
        self.assertEqual(
            offenders, [],
            "Actions 표현식에 산술 연산자가 있다 — 워크플로가 파싱 불가가 된다"
            f"(셸에서 계산하라): {offenders}")

    def test_guard_would_catch_the_2026_08_10_regression(self) -> None:
        """가드가 실제 그 표현식을 잡는지 — 잡지 못하는 가드는 초록만 칠한다."""
        broken = "fromJSON(inputs.retry_attempt || '0') + 1"
        self.assertTrue(_ARITH_RE.search(_QUOTED_RE.sub("", broken)))

    def test_guard_does_not_flag_legitimate_expressions(self) -> None:
        """오탐이 나면 가드가 꺼진다 — 실제 이 저장소에 있는 형태들로 확인한다."""
        for ok in (
            "needs.validate-build.outputs.should_send == 'true'",
            "secrets.GRM_PUBLISH_TOKEN || secrets.ADMIN_GITHUB_ACTIONS_TOKEN || github.token",
            "inputs.max_change_count || '20'",
            "always() && steps.skipped.outputs.sources != '' && fromJSON(env.RETRY_ATTEMPT) < 2",
            "format('{0}/{1}', github.server_url, github.repository)",
        ):
            with self.subTest(expr=ok):
                self.assertIsNone(_ARITH_RE.search(_QUOTED_RE.sub("", ok)))


if __name__ == "__main__":
    unittest.main()
