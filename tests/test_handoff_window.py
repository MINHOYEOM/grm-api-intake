"""정밀검토-B1 임시 방어 회귀 — handoff 조회 윈도우 확대 + 노후 미소비 New 경고.

B1: handoff 는 Status=New 큐를 Run Date 날짜 하한(종전 기본 7일=발행 cadence 와
무여유)으로 필터한다. 주간 Routine 이 1회 지연되면 미소비 New row 가 윈도우 밖으로
빠져 어떤 handoff 에도 안 잡힌다(조용한 영구 누락 — PL-10b 의 거울상). 임시 방어:
(1) 윈도우 기본 30일(GRM_HANDOFF_WINDOW_DAYS, CLI 우선) — cadence 초과로 손실 확률
제거, (2) 그래도 윈도우 밖에 남은 미소비 New 는 aged-unconsumed-new health 경고로
표면화(침묵 제거). 근본 해결(날짜 하한 제거)은 PL-10b 와 별도 트랙 — 이 테스트는
임시 방어 계약만 고정한다.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci


class ResolveHandoffWindowDaysTest(unittest.TestCase):
    """윈도우 결정 우선순위: CLI > GRM_HANDOFF_WINDOW_DAYS > 기본 30."""

    def setUp(self) -> None:
        self._saved = os.environ.pop("GRM_HANDOFF_WINDOW_DAYS", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["GRM_HANDOFF_WINDOW_DAYS"] = self._saved
        else:
            os.environ.pop("GRM_HANDOFF_WINDOW_DAYS", None)

    def test_default_is_30_not_window_days_7(self) -> None:
        # B1 핵심: CLI 미지정·env 미설정이면 30. 종전처럼 --window-days(7)에 묶이면
        # cadence(7일)와 무여유 — 명시적으로 7이 아님을 단언.
        days = ci.resolve_handoff_window_days(None)
        self.assertEqual(days, 30)
        self.assertNotEqual(days, 7)

    def test_cli_value_takes_precedence(self) -> None:
        os.environ["GRM_HANDOFF_WINDOW_DAYS"] = "21"
        self.assertEqual(ci.resolve_handoff_window_days(14), 14)   # CLI > env

    def test_env_overrides_default(self) -> None:
        os.environ["GRM_HANDOFF_WINDOW_DAYS"] = "21"
        self.assertEqual(ci.resolve_handoff_window_days(None), 21)

    def test_invalid_env_falls_back_to_default(self) -> None:
        os.environ["GRM_HANDOFF_WINDOW_DAYS"] = "abc"
        self.assertEqual(ci.resolve_handoff_window_days(None), 30)  # _env_int graceful


class NewIntakeRowsQueryWindowTest(unittest.TestCase):
    """notion_query_new_intake_rows 가 받은 window_days 가 쿼리 body 의 Run Date
    하한(on_or_after = run_date - window_days)으로 정확히 들어가는지 검증."""

    def test_window_start_is_run_date_minus_window_days(self) -> None:
        captured: list[dict] = []

        def fake_api(method, url, token, *, body=None, retries=2):
            captured.append(body)
            return {"results": [], "has_more": False}

        orig = ci.notion_api_request
        ci.notion_api_request = fake_api
        try:
            ci.notion_query_new_intake_rows(
                "tok", "db", date(2026, 6, 10), window_days=30)
        finally:
            ci.notion_api_request = orig

        and_filters = captured[0]["filter"]["and"]
        on_or_after = next(f["date"]["on_or_after"] for f in and_filters
                           if f.get("property") == ci.PROP_RUN_DATE and
                           "on_or_after" in f.get("date", {}))
        self.assertEqual(on_or_after, "2026-05-11")   # 2026-06-10 − 30일
        status_eq = next(f["select"]["equals"] for f in and_filters
                         if f.get("property") == ci.PROP_STATUS)
        self.assertEqual(status_eq, "New")


if __name__ == "__main__":
    unittest.main()
