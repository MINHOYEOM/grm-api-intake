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


class NewIntakeRowsQueryWindowTest(unittest.TestCase):
    """notion_query_new_intake_rows 가 받은 window_days 가 쿼리 body 의 Run Date
    하한(on_or_after = run_date - window_days)으로 정확히 들어가는지 검증."""

    def test_window_start_is_run_date_minus_window_days(self) -> None:
        captured: list[dict] = []

        def fake_api(method, url, token, *, body=None, retries=2):
            captured.append(body)
            return {"results": [], "has_more": False}

        orig = grm_handoff.notion_api_request
        grm_handoff.notion_api_request = fake_api
        try:
            ci.notion_query_new_intake_rows(
                "tok", "db", date(2026, 6, 10), window_days=30)
        finally:
            grm_handoff.notion_api_request = orig

        and_filters = captured[0]["filter"]["and"]
        on_or_after = next(f["date"]["on_or_after"] for f in and_filters
                           if f.get("property") == ci.PROP_RUN_DATE and
                           "on_or_after" in f.get("date", {}))
        self.assertEqual(on_or_after, "2026-05-11")   # 2026-06-10 − 30일
        status_eq = next(f["select"]["equals"] for f in and_filters
                         if f.get("property") == ci.PROP_STATUS)
        self.assertEqual(status_eq, "New")


def _fake_page(source: str = "MFDS", type_class: str = "recall-quality") -> dict:
    return {"id": "pg-x", "properties": {
        ci.PROP_SOURCE: {"select": {"name": source}},
        ci.PROP_TYPE_CLASS: {"select": {"name": type_class}},
    }}


class AgedUnconsumedNewCountTest(unittest.TestCase):
    """notion_count_aged_unconsumed_new — 필터 body·handoff 제외·페이지 누적 검증."""

    def _run(self, responses):
        captured: list[dict] = []
        it = iter(responses)

        def fake_api(method, url, token, *, body=None, retries=2):
            captured.append({k: v for k, v in body.items()})
            return next(it)

        orig = grm_handoff.notion_api_request
        grm_handoff.notion_api_request = fake_api
        try:
            n = ci.notion_count_aged_unconsumed_new(
                "tok", "db", date(2026, 6, 10), handoff_window_days=30)
        finally:
            grm_handoff.notion_api_request = orig
        return n, captured

    def test_filter_targets_just_outside_window(self) -> None:
        # cutoff = run_date − window − 1 = 2026-05-10 (윈도우 하한 2026-05-11 바로 바깥).
        n, captured = self._run([{"results": [], "has_more": False}])
        self.assertEqual(n, 0)
        and_filters = captured[0]["filter"]["and"]
        on_or_before = next(f["date"]["on_or_before"] for f in and_filters
                            if f.get("property") == ci.PROP_RUN_DATE)
        self.assertEqual(on_or_before, "2026-05-10")
        status_eq = next(f["select"]["equals"] for f in and_filters
                         if f.get("property") == ci.PROP_STATUS)
        self.assertEqual(status_eq, "New")

    def test_handoff_pages_are_excluded_from_count(self) -> None:
        # handoff 페이지(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아님 — 미집계.
        pages = [
            _fake_page(),                                          # 진짜 노후 New
            _fake_page(source=ci.SOURCE_HANDOFF),                  # handoff → 제외
            _fake_page(type_class=ci.TYPE_ROUTINE_HANDOFF),       # handoff → 제외
        ]
        n, _ = self._run([{"results": pages, "has_more": False}])
        self.assertEqual(n, 1)

    def test_pagination_accumulates_across_pages(self) -> None:
        n, captured = self._run([
            {"results": [_fake_page(), _fake_page()], "has_more": True,
             "next_cursor": "c2"},
            {"results": [_fake_page()], "has_more": False},
        ])
        self.assertEqual(n, 3)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[1].get("start_cursor"), "c2")


class DisplayWindowSeparationTest(unittest.TestCase):
    """B1 조회/표시 분리 — payload window_start(브리프 '검색 기간')는 표시 윈도우.

    payload 의 window_start~window_end 는 v16 프롬프트가 발행 브리프의 "검색 기간"
    속성으로 그대로 렌더한다(프롬프트 본문도 "지난 7일"로 서술). 조회 윈도우만
    30일로 넓히고 표시는 수집 윈도우(주간)를 유지해야 한다 — 분리 없이는 첫 월요일
    브리프가 "검색 기간: [30일 전]~[오늘]"로 나와 프롬프트 문구·K3 관찰과 충돌.
    """

    RUN = date(2026, 6, 10)

    def _emit(self, *, display, v2: bool):
        from datetime import datetime
        captured: dict = {}

        def fake_query(token, db_id, run_date, window_days,
                       source_names=None, doc_ids=None, current_handoff_id=None,
                       current_handoff_open=True):
            captured["query_window"] = window_days
            return []

        def fake_upsert(token, db_id, payload, generated_at, compact=False):
            captured["payload"] = payload
            return "pid", "url"

        # notion_query_new_intake_rows·notion_upsert_routine_handoff 는 grm_handoff(배치5
        # Phase2) 정의 — emit_routine_handoff 가 내부 호출하므로 정의 모듈에서 대체한다.
        orig_q = grm_handoff.notion_query_new_intake_rows
        orig_u = grm_handoff.notion_upsert_routine_handoff
        saved_v2 = os.environ.get("ENABLE_HANDOFF_V2")
        grm_handoff.notion_query_new_intake_rows = fake_query
        grm_handoff.notion_upsert_routine_handoff = fake_upsert
        os.environ["ENABLE_HANDOFF_V2"] = "true" if v2 else "false"
        try:
            ci.emit_routine_handoff(
                "tok", "db", self.RUN, 30, datetime(2026, 6, 10, 3, 17),
                display_window_days=display)
        finally:
            grm_handoff.notion_query_new_intake_rows = orig_q
            grm_handoff.notion_upsert_routine_handoff = orig_u
            if saved_v2 is None:
                os.environ.pop("ENABLE_HANDOFF_V2", None)
            else:
                os.environ["ENABLE_HANDOFF_V2"] = saved_v2
        return captured

    def test_v1_payload_shows_display_window_while_querying_30(self) -> None:
        captured = self._emit(display=7, v2=False)
        self.assertEqual(captured["query_window"], 30)              # 조회 = 안전망 30일
        self.assertEqual(captured["payload"]["window_start"], "2026-06-03")  # 표시 = 주간
        self.assertEqual(captured["payload"]["window_end"], "2026-06-10")

    def test_v2_payload_shows_display_window_while_querying_30(self) -> None:
        captured = self._emit(display=7, v2=True)
        self.assertEqual(captured["query_window"], 30)
        self.assertEqual(captured["payload"]["window_start"], "2026-06-03")
        self.assertEqual(captured["payload"]["window_end"], "2026-06-10")

    def test_display_omitted_falls_back_to_query_window(self) -> None:
        # 기존 호출 호환: display 미지정 → payload 윈도우 = 조회 윈도우(종전 의미).
        captured = self._emit(display=None, v2=False)
        self.assertEqual(captured["payload"]["window_start"], "2026-05-11")  # run−30


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
