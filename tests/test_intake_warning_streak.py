"""grm-intake.yml 경고 알림 — 코드별 경과일 사다리 회귀 가드.

★2026-08-06 실장애: 종전 게이트는 "경고 코드 집합(서명)이 바뀔 때만" 코멘트를 달았다.
장애가 지속되면 그 집합은 불변이므로 **지속이야말로 침묵이 보장되는 상태**였다 —
감쇠 논리가 거꾸로 박혀 있었다(하루짜리 blip 은 울리고 3일·30일 장애는 조용하다).

실증:
  · apis.data.go.kr 간헐 timeout 으로 MFDS 가 2026-08-02~08-05 **4연속** 0건이었는데,
    08-02 에 한 번 울린 뒤 08-03·08-04·08-05 는 전부 `signature unchanged; no comment`.
  · fda483 계열 경고 3종은 07-30 이후 서명 불변이라 6일 넘게 침묵했고, 그 침묵 안에
    실제 결함(일일 dedup 사전조회가 한 번도 안 돌던 것, #655)이 숨어 있었다.

★이 스텝은 인라인 JS 라 unittest 가 직접 못 돌린다. 그래서 **YAML 에서 함수를 뽑아
node 로 실제 실행**한다 — 문구만 검사하면 로직 회귀를 못 잡는다(이 저장소의 반복 함정:
"초록 CI ≠ 실행됨"). node 가 없으면 그 테스트만 skip 하고, 텍스트 계약은 계속 검사한다.
"""
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(REPO, ".github", "workflows", "grm-intake.yml")
STEP = "- name: Open or update warning issue"


def _read_wf() -> str:
    with open(WF, encoding="utf-8") as fh:
        return fh.read()


def _step_script() -> str:
    src = _read_wf()
    assert STEP in src, "경고 이슈 스텝이 사라졌다"
    body = src.split(STEP, 1)[1].split("script: |", 1)[1]
    # 다음 스텝(같은 들여쓰기의 `- name:`)까지가 이 스텝의 스크립트다.
    out = []
    for line in body.split("\n"):
        if line.strip().startswith("- name:") and not line.startswith(" " * 13):
            break
        out.append(line[12:] if line.startswith(" " * 12) else line)
    return "\n".join(out)


class ScriptShapeTest(unittest.TestCase):
    """텍스트 계약 — 값이 아니라 **그 값을 두는 이유**가 사라지지 않게 고정한다."""

    @classmethod
    def setUpClass(cls):
        cls.script = _step_script()
        cls.src = _read_wf()

    def test_signature_gate_is_gone(self):
        """서명(코드 집합) 비교로 되돌리면 지속 장애가 다시 영구 침묵한다."""
        self.assertNotIn("grm-warn-sig", self.src)
        self.assertNotIn("prevSig !== sig", self.src)
        self.assertIn("grm-warn-streak", self.script)

    def test_state_is_per_code_not_per_signature(self):
        """★코드 단위로 세야 한다 — 옆 코드가 깜빡일 때 서명이 바뀌면 지속 코드의 시계가
        매번 초기화돼 7·14·30일 단계에 영영 도달하지 못한다."""
        self.assertIn("for (const code of codes)", self.script)
        self.assertIn("prevState[code]", self.script)
        self.assertIn("state[code] = { f: first,", self.script)

    def test_parse_failure_fails_open_not_silent(self):
        """상태 주석이 지워지거나 깨지면 '상태 없음'으로 떨어져 1회 알린다 —
        침묵으로 빠지는 폴백은 이 수리가 없애려는 결함 그 자체다."""
        self.assertIn("streak state parse failed (treated as empty)", self.script)
        self.assertIn("let prevState = {};", self.script)

    def test_corrupt_alerted_is_clamped(self):
        """사람이 본문을 손볼 수 있는 자리다. `a` 가 경과일보다 크면 그 코드가 영영
        침묵하므로 day 로 클램프한다."""
        self.assertIn("Math.min(Math.max(Number(prev && prev.a) || 0, 0), day)", self.script)

    def test_does_not_alert_every_run(self):
        """'항상 울리는 경보는 경보가 아니다' — 사다리 도달분이 없으면 코멘트하지 않는다."""
        self.assertIn("if (due.length) {", self.script)
        self.assertIn("no rung due", self.script)

    def test_does_not_escalate_transient_to_failure(self):
        """transient 를 failure 로 올리면 exit 1 → grm-web-publish 가 `--status success`
        로 스캐폴드를 고르므로 **월요일 발행이 막힌다**(grm_health.py 가 문서화한 교환)."""
        self.assertIn("if: success() && github.event_name == 'schedule'", self.src)
        self.assertNotIn("core.setFailed", self.script)

    def test_zero_fetch_is_not_an_alert_condition(self):
        """정상 런에도 MHRA·MFDS-Law·WL 이 상시 0건이라 fetched==0 을 경보로 만들면
        매일 4~5건 잡음이 된다."""
        self.assertNotIn("fetched", self.script)

    def test_resolution_still_closes_the_issue(self):
        """경고가 0이 되면 닫아야 상태(=주석)도 함께 사라진다."""
        self.assertIn("if (!warnings.length) {", self.script)
        self.assertIn("state: 'closed'", self.script)

    def test_rationale_comment_survives(self):
        """왜 이 설계인지가 사라지면 다음 사람이 서명 방식으로 되돌린다."""
        head = self.src.split(STEP, 1)[1][:2000]
        self.assertIn("지속이야말로 침묵이 보장되는 상태", head)
        self.assertIn("08-02~08-05", head)


@unittest.skipUnless(shutil.which("node"), "node 없음 — 사다리 실행 검증 skip")
class LadderBehaviourTest(unittest.TestCase):
    """★핵심 — 문구가 아니라 **동작**을 검사한다. YAML 에서 뽑은 함수를 node 로 실제 실행."""

    @classmethod
    def setUpClass(cls):
        script = _step_script()
        # dueRung / daysSince 를 원본에서 그대로 떼어 온다(복제하면 드리프트한다).
        rungs = re.search(r"const RUNGS = \[[^\]]*\];", script).group(0)
        due = re.search(r"function dueRung\(day, alerted\) \{.*?\n            \}",
                        script, re.S)
        if due is None:
            due = re.search(r"function dueRung\(day, alerted\) \{(?:.|\n)*?\n\}", script)
        days = re.search(r"function daysSince\(from, to\) \{(?:.|\n)*?\n\}", script)
        cls.prelude = "\n".join([rungs, due.group(0), days.group(0)])

    def _run(self, expr):
        out = subprocess.run(
            ["node", "-e", self.prelude + "\nconsole.log(JSON.stringify(" + expr + "));"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_new_code_alerts_on_day_one(self):
        self.assertEqual(self._run("dueRung(1, 0)"), "1")

    def test_persisting_code_is_silent_between_rungs(self):
        """★이게 종전 결함의 반대편이다 — 매일 울리면 안 된다."""
        self.assertEqual(self._run("[2,4,5,6].map(d => dueRung(d, 3))"), "[0,0,0,0]")
        self.assertEqual(self._run("[8,9,13].map(d => dueRung(d, 7))"), "[0,0,0]")

    def test_ladder_fires_at_each_rung(self):
        self.assertEqual(self._run("dueRung(3, 1)"), "3")
        self.assertEqual(self._run("dueRung(7, 3)"), "7")
        self.assertEqual(self._run("dueRung(14, 7)"), "14")
        self.assertEqual(self._run("dueRung(30, 14)"), "30")

    def test_beyond_thirty_days_repeats_monthly(self):
        """30일을 넘겨도 영원히 침묵하면 안 된다 — 자가복구 안 되는 사건(키 만료·엔드포인트
        폐지)이 30일 재수집 창을 넘기는 순간 손실이 영구화되는데, 그때가 가장 조용하다."""
        self.assertEqual(self._run("dueRung(60, 30)"), "60")
        self.assertEqual(self._run("dueRung(45, 30)"), "0")
        self.assertEqual(self._run("dueRung(90, 60)"), "90")

    def test_skipped_rungs_collapse_to_the_highest(self):
        """실행이 며칠 걸러 돌아 단계를 건너뛰어도 한 번만 울린다(중복 알림 금지)."""
        self.assertEqual(self._run("dueRung(20, 0)"), "14")

    # 스텝이 실제로 도는 방식 — 매 실행마다 클램프하고 그 값을 다시 저장한다.
    # ★격리된 dueRung 호출로는 이 성질을 검사할 수 없다. 상태가 실행 사이에 이월되기
    #   때문이고, 클램프의 효과도 "그 값이 persist 된다"에 있다.
    _SIM = """
    function sim(startA, days, clamp) {
      let a = startA, fired = [];
      for (const d of days) {
        const eff = clamp ? Math.min(Math.max(a, 0), d) : a;
        const r = dueRung(d, eff);
        if (r > 0) fired.push(d);
        a = r > 0 ? r : eff;
      }
      return fired;
    }"""

    def _sim(self, start_a, days, clamp=True):
        expr = ("(() => {" + self._SIM + "\n return sim(" + str(start_a) + ", "
                + repr(list(days)).replace("'", "") + ", " + ("true" if clamp else "false")
                + "); })()")
        return self._run(expr)

    def test_corrupt_state_cannot_silence_forever(self):
        """★`a` 가 경과일보다 큰 불가능한 상태(손편집·오타)의 계약:
        ① 이미 지나간 단계는 재발화하지 않는다 ② 그렇다고 **영구 침묵하지도 않는다**.

        클램프가 매 실행 `a` 를 되돌려 저장하므로, 7일차에 `a=99` 가 주입돼도 그 값은
        7 로 눌려 persist 되고 **14일차에 다시 울린다**. 클램프가 없으면 99일까지 전
        단계를 삼켜 그 코드가 화면에서 조용히 사라진다."""
        days = list(range(7, 31))
        self.assertEqual(self._sim(99, days, clamp=True), "[14,30]",
                         "클램프가 있으면 다음 단계에서 살아나야 한다")
        self.assertEqual(self._sim(99, days, clamp=False), "[]",
                         "대조군 — 클램프가 없으면 영구 침묵한다(이 수리의 결함 그 자체)")

    def test_normal_sequence_alerts_only_at_rungs(self):
        """정상 진행 — 30일 연속 장애에서 1·3·7·14·30 다섯 번만 울린다(매일 아님)."""
        self.assertEqual(self._sim(0, range(1, 31)), "[1,3,7,14,30]")

    def test_first_day_is_day_one_not_zero(self):
        self.assertEqual(self._run("daysSince('2026-08-06', '2026-08-06')"), "1")
        self.assertEqual(self._run("daysSince('2026-08-02', '2026-08-05')"), "4")

    def test_bad_dates_do_not_crash(self):
        self.assertEqual(self._run("daysSince('nope', '2026-08-06')"), "1")

    def test_the_real_incident_would_have_alerted(self):
        """★실장애 재현 — MFDS 가 08-02~08-05 4연속. 종전에는 08-02 한 번만 울렸다.
        사다리에서는 1일차(신규)와 3일차에 울린다."""
        days = ["2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]
        alerted, fired = 0, []
        for d in days:
            day = int(self._run(f"daysSince('2026-08-02', '{d}')"))
            rung = int(self._run(f"dueRung({day}, {alerted})"))
            if rung:
                fired.append(day)
                alerted = rung
        self.assertEqual(fired, [1, 3], "1일차·3일차에 울려야 한다")


if __name__ == "__main__":
    unittest.main()
