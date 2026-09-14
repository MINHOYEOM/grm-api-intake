"""DB 백업 검증 — 정확 count 가 타임아웃(504)이어도 백업이 죽지 않는지(#997).

2026-09-13 스케줄 런: `admin_audit_log` 정확 count 가 PostgREST 504 → counts 단계가 예외로
죽어 덤프가 성공했는데도 백업 전체가 실패로 기록됐다. 정확 count 는 검증의 정밀도를 위한
것이지 백업의 전제가 아니다 — 재시도 → 추정치(count=planned) 폴백 → 그 표만 완화 비율.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_backup_verify as dbv  # noqa: E402


class _Resp:
    def __init__(self, status: int, total: str | None = None) -> None:
        self.status_code = status
        self.headers = {"Content-Range": f"0-0/{total}"} if total is not None else {}


def _fake_get(script: dict[str, list]):
    """URL 끝 표 이름별로 응답 대본을 순서대로 돌려주는 가짜 requests.get. 호출을 기록한다."""
    calls: list[tuple[str, str]] = []

    def get(url, headers, params, timeout):
        name = url.rsplit("/", 1)[-1]
        calls.append((name, headers["Prefer"]))
        seq = script[name]
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return item

    get.calls = calls  # type: ignore[attr-defined]
    return get


class LiveCountsFallbackTest(unittest.TestCase):
    URL = "https://x.supabase.co"

    def test_transient_5xx_is_retried_then_exact(self) -> None:
        get = _fake_get({"findings": [_Resp(504), _Resp(504), _Resp(206, "42")]})
        slept: list[float] = []
        out = dbv.live_counts(self.URL, "k", ["findings"], get=get, sleep=slept.append)
        self.assertEqual(out, {"findings": 42})
        self.assertEqual([p for _, p in get.calls], ["count=exact"] * 3)
        self.assertEqual(slept, [1, 2])

    def test_persistent_504_falls_back_to_planned_estimate(self) -> None:
        get = _fake_get({"admin_audit_log": [_Resp(504), _Resp(504), _Resp(504), _Resp(206, "18000")],
                         "findings": [_Resp(206, "25087")]})
        out = dbv.live_counts(self.URL, "k", ["admin_audit_log", "findings"], get=get,
                              sleep=lambda s: None)
        self.assertEqual(out["admin_audit_log"], 18000)
        self.assertEqual(out["findings"], 25087)
        self.assertEqual(out[dbv.APPROX_KEY], ["admin_audit_log"])
        prefers = [p for n, p in get.calls if n == "admin_audit_log"]
        self.assertEqual(prefers, ["count=exact"] * 3 + ["count=planned"])

    def test_timeout_exception_is_retryable_and_falls_back(self) -> None:
        get = _fake_get({"t": [TimeoutError("read timed out"), TimeoutError("x"),
                               TimeoutError("y"), _Resp(206, "7")]})
        out = dbv.live_counts(self.URL, "k", ["t"], get=get, sleep=lambda s: None)
        self.assertEqual(out, {"t": 7, dbv.APPROX_KEY: ["t"]})

    def test_estimate_also_failing_raises_without_leaking_key(self) -> None:
        get = _fake_get({"t": [_Resp(504)]})
        with self.assertRaises(RuntimeError) as cm:
            dbv.live_counts(self.URL, "SECRET-KEY", ["t"], get=get, sleep=lambda s: None)
        self.assertIn("count-failed:t:http_504;planned:http_504", str(cm.exception))
        self.assertNotIn("SECRET-KEY", str(cm.exception))

    def test_4xx_is_not_retried(self) -> None:
        get = _fake_get({"t": [_Resp(404)]})
        with self.assertRaises(RuntimeError):
            dbv.live_counts(self.URL, "k", ["t"], get=get, sleep=lambda s: None)
        self.assertEqual(len(get.calls), 1)


class CompareApproxTest(unittest.TestCase):
    def _cmp(self, live, dumped, approx=()):
        return dbv.compare(dict(live), dict(dumped), min_ratio=0.99,
                           require=["findings"], min_tables=1, approx=set(approx))

    def test_approx_table_uses_loose_ratio_only(self) -> None:
        live = {"findings": 100, "admin_audit_log": 18000}
        # 추정치 표는 0.99 비율에 못 미쳐도 통과(플래너 추정 흔들림), 정확 표는 그대로 엄격.
        ok, rows, problems = self._cmp(live, {"findings": 100, "admin_audit_log": 12000},
                                       approx={"admin_audit_log"})
        self.assertTrue(ok, problems)
        self.assertTrue(next(r for r in rows if r["table"] == "admin_audit_log")["approx"])
        ok, _, problems = self._cmp(live, {"findings": 100, "admin_audit_log": 12000})
        self.assertFalse(ok)      # 같은 수치가 정확 count 였다면 위반

    def test_approx_table_still_catches_gross_truncation_and_zero(self) -> None:
        live = {"findings": 100, "admin_audit_log": 18000}
        ok, _, problems = self._cmp(live, {"findings": 100, "admin_audit_log": 5000},
                                    approx={"admin_audit_log"})
        self.assertFalse(ok)
        self.assertTrue(any("완화 비율" in p for p in problems))
        ok, _, problems = self._cmp(live, {"findings": 100, "admin_audit_log": 0},
                                    approx={"admin_audit_log"})
        self.assertFalse(ok)
        self.assertTrue(any("~18000행인데 덤프 0행" in p for p in problems))

    def test_report_marks_estimates(self) -> None:
        ok, rows, problems = self._cmp({"findings": 3, "a": 10}, {"findings": 3, "a": 9},
                                       approx={"a"})
        text = dbv.render_report(rows, problems, ok=ok)
        self.assertIn("| a | ~10 | 9 | ok |", text)
        self.assertIn("count=planned", text)


class VerifyCliApproxTest(unittest.TestCase):
    def test_counts_json_with_approx_key_verifies(self) -> None:
        data = ("COPY public.findings (id) FROM stdin;\na\nb\nc\n\\.\n"
                "COPY public.admin_audit_log (id) FROM stdin;\n" + "x\n" * 9 + "\\.\n")
        with tempfile.TemporaryDirectory() as d:
            dump, counts, report = (os.path.join(d, n) for n in ("data.sql", "counts.json", "r.md"))
            with open(dump, "w", encoding="utf-8") as fh:
                fh.write(data)
            with open(counts, "w", encoding="utf-8") as fh:
                json.dump({"findings": 3, "admin_audit_log": 12, dbv.APPROX_KEY: ["admin_audit_log"]}, fh)
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            rc = subprocess.run([sys.executable, dbv.__file__, "verify", "--dump", dump,
                                 "--counts", counts, "--report", report, "--min-tables", "1",
                                 "--require", "findings"], env=env, capture_output=True).returncode
            self.assertEqual(rc, 0)
            with open(report, encoding="utf-8") as fh:
                self.assertIn("| admin_audit_log | ~12 | 9 | ok |", fh.read())


if __name__ == "__main__":
    unittest.main()
