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
import grm_handoff  # 배치5 Phase2: handoff/emit 정의 모듈(patch 대상)


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




def _fake_page(source: str = "MFDS", type_class: str = "recall-quality") -> dict:
    return {"id": "pg-x", "properties": {
        "Source": {"select": {"name": source}},
        "Type or Class": {"select": {"name": type_class}},
    }}






def _health_kwargs(**over):
    """_evaluate_health 최소 호출 kwargs(전 소스 비활성·에러 없음 → 기본 ok)."""
    base = dict(
        stats=ci.CollectionStats(),
        active=set(),
        enable_search=False, enable_mfds=False, enable_mfds_law=False,
        enable_mfds_recall=False, enable_mfds_admin=False,
        enable_mfds_gmp_cert=False, enable_mfds_safety_letter=False,
        enable_mfds_gmp_inspection=False,
        enable_ich=False, enable_who=False, enable_hc=False,
        enable_fda483=False,
        enable_moleg_api=False, enable_scrape=False,
        event_name="schedule",
        emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
        handoff_error_msg="",
    )
    base.update(over)
    return base


class AgedUnconsumedNewHealthWarningTest(unittest.TestCase):
    """_evaluate_health 의 aged-unconsumed-new 경고 — warning(exit 0)이지 failure 아님."""

    def test_aged_rows_surface_as_warning_not_failure(self) -> None:
        health = ci._evaluate_health(**_health_kwargs(
            aged_unconsumed_new=5, handoff_window_days=30))
        codes = [w.code for w in health.warnings]
        self.assertEqual(codes.count("aged-unconsumed-new"), 1)
        self.assertEqual(health.status, "warning")
        self.assertEqual(health.exit_code, 0)        # §3.5 warning 분류 — exit 0 유지
        self.assertEqual(health.failures, [])
        aged = next(w for w in health.warnings if w.code == "aged-unconsumed-new")
        self.assertIn("30일", aged.message)
        self.assertIn("5건", aged.message)

    def test_zero_aged_rows_no_warning(self) -> None:
        health = ci._evaluate_health(**_health_kwargs(
            aged_unconsumed_new=0, handoff_window_days=30))
        self.assertNotIn("aged-unconsumed-new", [w.code for w in health.warnings])
        self.assertEqual(health.status, "ok")

    def test_query_failure_surfaces_as_warning(self) -> None:
        # 카운트 조회 실패는 조용한 0 이 아니라 별도 경고로 표면화(감시 공백 가시화).
        health = ci._evaluate_health(**_health_kwargs(
            aged_new_query_error="Notion API 500"))
        codes = [w.code for w in health.warnings]
        self.assertIn("aged-unconsumed-new-query-failed", codes)
        self.assertEqual(health.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
