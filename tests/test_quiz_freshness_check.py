import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import quiz_freshness_check as qf


class CommittedBankFreshnessTest(unittest.TestCase):
    """커밋된 뱅크에 대해 감시가 동작하는지 — 데이터가 아니라 감시기를 검사한다.

    "이번 주가 신선한가"는 실행 시점에 달렸으므로 CI 에서 값을 단정하지 않는다.
    대신 최신 주차를 기준일로 삼으면 반드시 FRESH 여야 한다(감시기가 항상 STALE 을
    내는 고장 상태를 잡는다 — 늘 울리는 경보는 경보가 아니다).
    """

    def test_latest_generated_week_reads_as_fresh(self):
        bank = json.loads(qf.DEFAULT_QUIZ_BANK.read_text(encoding="utf-8"))
        weeks = sorted({str(q["week"]) for q in bank if "week" in q})
        self.assertTrue(weeks, "뱅크에 주차 문항이 없습니다")
        import datetime as dt
        latest = weeks[-1]
        monday = dt.date.fromisocalendar(int(latest[:4]), int(latest[4:]), 1)
        report = qf.check(as_of=monday)
        self.assertTrue(report["fresh"], report)
        self.assertEqual(report["weeks_behind"], 0)
        self.assertEqual(report["errors"], [])


class QuizFreshnessTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="quiz_fresh_")
        self.bank = Path(self._tmp.name) / "quiz_bank.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, weeks):
        items = [{"id": f"q-{w}-01", "week": w} for w in weeks]
        self.bank.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def test_iso_week_key_matches_the_pipeline_format(self):
        import datetime as dt
        # 한 자리 주차는 0 을 채운다 — "20265" 로 쓰면 quiz.js 의 String(seed) 와 어긋난다.
        self.assertEqual(qf.iso_week_key(dt.date(2026, 2, 2)), "202606")
        self.assertEqual(qf.iso_week_key(dt.date(2026, 8, 5)), "202632")
        # ISO 연도 경계 — 달력 연도가 아니라 ISO 연도를 쓴다.
        self.assertEqual(qf.iso_week_key(dt.date(2026, 12, 31)), "202653")

    def test_current_week_present_is_fresh(self):
        import datetime as dt
        self._write(["202630", "202632"])
        report = qf.check(self.bank, as_of=dt.date(2026, 8, 5))
        self.assertTrue(report["fresh"])
        self.assertEqual(report["current_week"], "202632")
        self.assertEqual(report["weeks_behind"], 0)

    def test_missing_current_week_is_stale_and_counts_weeks_behind(self):
        import datetime as dt
        self._write(["202630", "202631", "202632"])
        for day, behind in ((dt.date(2026, 8, 11), 1), (dt.date(2026, 8, 25), 3)):
            with self.subTest(day=day):
                report = qf.check(self.bank, as_of=day)
                self.assertFalse(report["fresh"])
                self.assertEqual(report["weeks_behind"], behind)
                self.assertEqual(report["latest_week"], "202632")

    def test_empty_and_broken_inputs_report_errors_not_false_freshness(self):
        import datetime as dt
        self.bank.write_text("[]", encoding="utf-8")
        report = qf.check(self.bank, as_of=dt.date(2026, 8, 5))
        self.assertFalse(report["fresh"])
        self.assertIsNone(report["latest_week"])

        self.bank.write_text("{}", encoding="utf-8")
        self.assertTrue(qf.check(self.bank, as_of=dt.date(2026, 8, 5))["errors"])

        self.bank.write_text("not json", encoding="utf-8")
        self.assertTrue(qf.check(self.bank, as_of=dt.date(2026, 8, 5))["errors"])

        missing = self.bank.parent / "nope.json"
        self.assertTrue(qf.check(missing, as_of=dt.date(2026, 8, 5))["errors"])

    def test_main_exit_codes_are_zero_one_two(self):
        self._write(["202632"])
        base = ["--quiz-bank", str(self.bank)]

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = qf.main(base + ["--as-of", "2026-08-05"])
        self.assertEqual(code, 0)
        self.assertIn("quiz_freshness: FRESH", stdout.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = qf.main(base + ["--as-of", "2026-08-12"])
        self.assertEqual(code, 1)
        self.assertIn("quiz_freshness: STALE", stdout.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = qf.main(base + ["--as-of", "not-a-date"])
        self.assertEqual(code, 2)

    def test_report_json_is_written_for_the_workflow_summary(self):
        self._write(["202632"])
        out = self.bank.parent / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            qf.main(["--quiz-bank", str(self.bank), "--as-of", "2026-08-05", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        # 워크플로 이슈 본문이 참조하는 키 — 이름이 바뀌면 알림이 조용히 "undefined" 가 된다.
        for key in ("current_week", "latest_week", "weeks_behind", "week_count", "checked_date_kst", "errors"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
