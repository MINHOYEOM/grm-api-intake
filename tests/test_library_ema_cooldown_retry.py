"""#1456 — EMA 상세 페이지 일시 429 로 자료실 소스 전체가 격리되던 결함.

11-10~12 사흘 연속 'ich-guideline-q13-…' 상세 한 장이 429 로 실패해 EMA 전체가 격리됐다.
수집기가 실패한 상세 페이지를 목록 끝에 한 번 쉬고(RETRY_COOLDOWN_SECONDS) 다시 받는지,
그래도 실패하면 종전처럼 오류를 돌려주는지 고정한다(뮤테이션: 재시도 블록을 빼면 첫 시험이 실패).
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import library_collect_ema as ema  # noqa: E402

GUIDE_A = ema.SITE_BASE + "/en/ich-guideline-q13-continuous-manufacturing"
GUIDE_B = ema.SITE_BASE + "/en/guideline-on-process-validation"


def _fake_pages(fail_times: dict[str, int]):
    calls: dict[str, int] = {}

    def fake_get(url: str) -> str:
        calls[url] = calls.get(url, 0) + 1
        if url == ema.COMPILATION_URL:
            return "<compilation>"
        if url == ema.MANUFACTURING_GUIDELINES_URL:
            return "<listing>"
        if calls[url] <= fail_times.get(url, 0):
            raise RuntimeError("429 Client Error: Too Many Requests")
        return f"<page {url}>"

    return fake_get, calls


class EmaCooldownRetryTest(unittest.TestCase):
    def _run(self, fail_times: dict[str, int]):
        fake_get, calls = _fake_pages(fail_times)
        procedures = [{"id": f"p{i}"} for i in range(ema.MIN_EXPECTED_PROCEDURES)]
        links = [(GUIDE_A, "Q13"), (GUIDE_B, "PV")] + [
            (ema.SITE_BASE + f"/en/g{i}", f"g{i}") for i in range(ema.MIN_EXPECTED_GUIDELINES)]
        with mock.patch.object(ema, "_get", side_effect=fake_get), \
             mock.patch.object(ema, "parse_procedures", return_value=procedures), \
             mock.patch.object(ema, "parse_guideline_links", return_value=links), \
             mock.patch.object(ema, "build_guideline_item",
                               side_effect=lambda u, t, h: {"id": u, "html": h}), \
             mock.patch.object(ema.time, "sleep") as sleep:
            items, error = ema.collect_library_items(date(2026, 11, 12))
        return items, error, calls, sleep

    def test_transient_429_recovered_after_cooldown(self):
        items, error, calls, sleep = self._run({GUIDE_A: 1})
        self.assertIsNone(error)
        self.assertEqual(calls[GUIDE_A], 2)
        page = next(i for i in items if i.get("id") == GUIDE_A)
        self.assertTrue(page["html"])
        sleep.assert_any_call(ema.RETRY_COOLDOWN_SECONDS)

    def test_persistent_failure_still_reported(self):
        items, error, calls, _ = self._run({GUIDE_A: 5})
        self.assertIsNotNone(error)
        self.assertIn("ich-guideline-q13", error)
        self.assertEqual(calls[GUIDE_A], 2)          # 재시도는 한 번뿐
        page = next(i for i in items if i.get("id") == GUIDE_A)
        self.assertEqual(page["html"], "")           # 빈 본문으로라도 항목은 남긴다(종전과 같음)

    def test_no_cooldown_when_nothing_failed(self):
        _, error, _, sleep = self._run({})
        self.assertIsNone(error)
        self.assertNotIn(mock.call(ema.RETRY_COOLDOWN_SECONDS), sleep.call_args_list)

    def test_request_delay_raised(self):
        self.assertGreaterEqual(ema.REQUEST_DELAY_SECONDS, 3.0)


if __name__ == "__main__":
    unittest.main()
