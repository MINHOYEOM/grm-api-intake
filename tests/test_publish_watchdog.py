"""발행 워치독 판정 로직 테스트 — 멱등·유한·기동조건·경보조건 계약 고정.

이 로직이 워크플로 bash 에 남아 있으면 검증할 방법이 배포뿐이라, 판정만 순수 함수로
분리했다(publish_watchdog.py). 여기서 고정하는 계약이 곧 자가 복구의 안전 계약이다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import publish_watchdog as w  # noqa: E402


class DecideDispatchIdempotencyTest(unittest.TestCase):
    """멱등 — 이미 결과가 있으면 절대 기동하지 않는다."""

    def test_delta_exists_blocks_dispatch(self):
        ok, reason = w.decide_dispatch(delta_exists=True, publish_pr_exists=False,
                                       dispatches_today=0)
        self.assertFalse(ok)
        self.assertIn("이관 완료", reason)

    def test_publish_pr_exists_blocks_dispatch(self):
        # 델타가 없어 보여도 발행 PR 이 있으면 조립까지 끝난 것 — 기동 금지.
        ok, reason = w.decide_dispatch(delta_exists=False, publish_pr_exists=True,
                                       dispatches_today=0)
        self.assertFalse(ok)
        self.assertIn("발행 PR", reason)

    def test_both_present_blocks_dispatch(self):
        ok, _ = w.decide_dispatch(delta_exists=True, publish_pr_exists=True,
                                  dispatches_today=0)
        self.assertFalse(ok)

    def test_repeated_calls_after_recovery_stay_blocked(self):
        # 복구된 뒤 남은 회차들이 다시 때리지 않는지(워치독 3회 구조의 핵심).
        for n in range(3):
            ok, _ = w.decide_dispatch(delta_exists=True, publish_pr_exists=False,
                                      dispatches_today=n)
            self.assertFalse(ok)


class DecideDispatchCapTest(unittest.TestCase):
    """유한 — 상한을 넘겨 계속 때리지 않는다."""

    def test_dispatches_below_cap_allowed(self):
        ok, reason = w.decide_dispatch(False, False, dispatches_today=0, cap=2)
        self.assertTrue(ok)
        self.assertIn("1/2", reason)
        ok, reason = w.decide_dispatch(False, False, dispatches_today=1, cap=2)
        self.assertTrue(ok)
        self.assertIn("2/2", reason)

    def test_at_cap_blocks(self):
        ok, reason = w.decide_dispatch(False, False, dispatches_today=2, cap=2)
        self.assertFalse(ok)
        self.assertIn("상한", reason)

    def test_over_cap_blocks(self):
        ok, _ = w.decide_dispatch(False, False, dispatches_today=99, cap=2)
        self.assertFalse(ok)

    def test_watchdog_three_runs_never_exceed_cap(self):
        """워치독이 3회 도는 최악의 주(브릿지가 매번 실패)에도 기동은 상한까지만."""
        dispatched = 0
        for _ in range(3):
            ok, _ = w.decide_dispatch(False, False, dispatches_today=dispatched,
                                      cap=w.DEFAULT_DISPATCH_CAP)
            if ok:
                dispatched += 1
        self.assertEqual(dispatched, w.DEFAULT_DISPATCH_CAP)

    def test_human_dispatches_count_toward_cap(self):
        # 사람이 이미 2회 손으로 돌린 주에는 워치독이 더 얹지 않는다.
        ok, _ = w.decide_dispatch(False, False, dispatches_today=2)
        self.assertFalse(ok)


class DecideDispatchTriggerTest(unittest.TestCase):
    """기동 조건 — 부재일 때만 기동한다."""

    def test_missing_both_triggers(self):
        ok, reason = w.decide_dispatch(False, False, 0)
        self.assertTrue(ok)
        self.assertIn("부재", reason)


class DecideEscalateTest(unittest.TestCase):
    """경보 조건 — 과알림 0 과 침묵 방지 사이의 계약."""

    def test_normal_no_escalation(self):
        ok, _ = w.decide_escalate(delta_exists=True, recovered=False, kst_hour=13)
        self.assertFalse(ok)

    def test_recovered_no_escalation(self):
        ok, reason = w.decide_escalate(delta_exists=False, recovered=True, kst_hour=13)
        self.assertFalse(ok)
        self.assertIn("기록", reason)

    def test_early_run_stays_quiet(self):
        # 09:13 회차 — Routine 예치 지연과 구분 불가하므로 경보하지 않는다.
        ok, reason = w.decide_escalate(delta_exists=False, recovered=False, kst_hour=9)
        self.assertFalse(ok)
        self.assertIn("이른 회차", reason)

    def test_late_run_escalates(self):
        for hour in (11, 13):
            ok, _ = w.decide_escalate(delta_exists=False, recovered=False, kst_hour=hour)
            self.assertTrue(ok, f"{hour}시에는 경보해야 한다")

    def test_boundary_hour_escalates(self):
        # 경계값(quiet_before_hour 와 같은 시각)은 경보 쪽 — 미만일 때만 조용하다.
        ok, _ = w.decide_escalate(False, False, kst_hour=w.DEFAULT_QUIET_BEFORE_KST_HOUR)
        self.assertTrue(ok)

    def test_scheduled_slots_behaviour(self):
        """실제 크론 3회차(09/11/13 KST)에서 최악의 주 = 경보 2회(첫 회차는 침묵)."""
        alarms = [w.decide_escalate(False, False, h)[0] for h in (9, 11, 13)]
        self.assertEqual(alarms, [False, True, True])

    def test_publish_pr_suppresses_alarm(self):
        """발행 PR 이 있으면 델타가 안 보여도 경보하지 않는다(오탐 가드).

        2026-07-20 시뮬레이션에서 발견: 기동은 발행 PR 로 막으면서 경보는 안 막아,
        발행이 정상 완료된 주에 "델타 없음" 경보가 뜨는 조합이 있었다.
        """
        ok, reason = w.decide_escalate(delta_exists=False, recovered=False, kst_hour=13,
                                       publish_pr_exists=True)
        self.assertFalse(ok)
        self.assertIn("발행 PR", reason)

    def test_dispatch_and_escalate_agree_on_publish_pr(self):
        # 같은 사실(발행 PR 존재)에 대해 기동·경보가 엇갈리지 않아야 한다.
        d, _ = w.decide_dispatch(delta_exists=False, publish_pr_exists=True, dispatches_today=0)
        e, _ = w.decide_escalate(delta_exists=False, recovered=False, kst_hour=13,
                                 publish_pr_exists=True)
        self.assertFalse(d)
        self.assertFalse(e)


class NonAsciiOutputTest(unittest.TestCase):
    """사유 문자열의 비ASCII(—)가 cp949 stdout 에서 죽지 않는지.

    2026-07-20 로컬 시뮬레이션에서 발견한 잠복 결함. Actions(UTF-8 리눅스)에서는 재현되지
    않지만, 이 저장소 개발환경은 Windows 라 로컬 실행 시 UnicodeEncodeError 로 죽었고,
    워크플로의 `set -e` 가 그 실패를 스텝 실패로 올려 **복구가 시작조차 못 한다**.
    """

    def test_reasons_encode_under_cp949_stdout(self):
        import io
        import contextlib
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp949", errors="strict")
        with contextlib.redirect_stdout(stream):
            rc = w.main(["dispatch"])          # 사유에 em dash 포함
        self.assertEqual(rc, 0)

    def test_all_reasons_are_utf8_safe(self):
        # 모든 분기의 사유가 출력 가능한지(문자열 자체 검증).
        reasons = [
            w.decide_dispatch(True, False, 0)[1], w.decide_dispatch(False, True, 0)[1],
            w.decide_dispatch(False, False, 9, 2)[1], w.decide_dispatch(False, False, 0)[1],
            w.decide_escalate(True, False, 13)[1], w.decide_escalate(False, True, 13)[1],
            w.decide_escalate(False, False, 9)[1], w.decide_escalate(False, False, 13)[1],
            w.decide_escalate(False, False, 13, publish_pr_exists=True)[1],
        ]
        for r in reasons:
            self.assertTrue(r.strip())
            self.assertNotIn("\n", r)
            r.encode("utf-8")


class CliContractTest(unittest.TestCase):
    """워크플로가 쓰는 CLI 계약 — key=value 출력·항상 exit 0."""

    def _run(self, argv):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = w.main(argv)
        return rc, dict(line.split("=", 1) for line in buf.getvalue().strip().splitlines())

    def test_dispatch_cli_true(self):
        rc, out = self._run(["dispatch", "--dispatches-today", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(out["dispatch"], "true")

    def test_dispatch_cli_blocked_by_delta(self):
        rc, out = self._run(["dispatch", "--delta-exists"])
        self.assertEqual(rc, 0)
        self.assertEqual(out["dispatch"], "false")

    def test_dispatch_cli_blocked_by_publish_pr(self):
        _, out = self._run(["dispatch", "--publish-pr-exists"])
        self.assertEqual(out["dispatch"], "false")

    def test_escalate_cli(self):
        _, out = self._run(["escalate", "--kst-hour", "13"])
        self.assertEqual(out["escalate"], "true")
        _, out = self._run(["escalate", "--kst-hour", "9"])
        self.assertEqual(out["escalate"], "false")
        _, out = self._run(["escalate", "--kst-hour", "13", "--recovered"])
        self.assertEqual(out["escalate"], "false")

    def test_reason_is_single_line(self):
        # 사유에 줄바꿈이 섞이면 GITHUB_OUTPUT 파싱이 깨진다.
        for argv in (["dispatch"], ["escalate", "--kst-hour", "13"]):
            _, out = self._run(argv)
            for v in out.values():
                self.assertNotIn("\n", v)


if __name__ == "__main__":
    unittest.main(verbosity=2)
