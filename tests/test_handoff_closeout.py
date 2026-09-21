"""handoff 마감 누락 감시 — "발행은 됐는데 handoff 가 안 닫혔다"를 잡는지 못박는다.

2026-09-14 실측: 그 주 브리프는 정상 발행됐는데(델타 커밋 + grm-web-publish 성공)
handoff page 는 CONSUMED(Processed)가 아니라 `STALE GRM Routine Handoff 2026-09-14
(superseded by 2026-09-15)` / Skipped 로 끝나 있었다. 같은 기간의 다른 월요일
(08-17·08-24·08-31·09-07·09-21)은 전부 CONSUMED 였으니 **"월요일 handoff 가 STALE 됐다"
자체가 정확한 고장 신호**인데, 그때 아무것도 경보하지 않았다 — publish 워치독은
`delta_{date}.json` 존재만 보고 handoff 가 닫혔는지는 보지 않는다.

피해는 그 주에 0 이었다(잔존 New row 0건·09-14↔09-21 카드 중복 0건). 그래도 구조적으로는
위험한 자리다: reconcile 에서 CONSUMED 는 라우틴이 못 찍은 row 를 **마감**하는 경로이고
STALE 은 반대로 미발행 row 의 ref 를 비워 **재투입**한다 — 미발행 row 가 남아 있던 주라면
다음 주 중복 카드가 된다.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import collect_intake as ci
import grm_handoff
from grm_handoff import PUBLISH_WEEKDAY, unconsumed_publish_handoffs


def _handoff_page(handoff_id: str, run_date: str, status: str, page_id: str) -> dict:
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "properties": {
            "Name": {"title": [{"plain_text": f"OPEN GRM Routine Handoff {run_date}"}]},
            "Source": {"select": {"name": "GRM Handoff"}},
            "Document ID": {"rich_text": [{"plain_text": handoff_id}]},
            "Type or Class": {"select": {"name": "routine-handoff"}},
            "Status": {"select": {"name": status}},
            "Run Date (KST)": {"date": {"start": run_date}},
        },
    }


class UnconsumedPublishHandoffsTest(unittest.TestCase):
    def test_publish_weekday_is_monday(self) -> None:
        self.assertEqual(PUBLISH_WEEKDAY, 0)
        self.assertEqual(date(2026, 9, 14).weekday(), PUBLISH_WEEKDAY)

    def test_the_2026_09_14_miss_is_flagged(self) -> None:
        """실측 회귀 — 09-15 수집기가 09-14(월) handoff 를 봉인한 그 상황."""
        self.assertEqual(unconsumed_publish_handoffs(["2026-09-14"]), ["2026-09-14"])

    def test_ordinary_daily_supersede_is_silent(self) -> None:
        """월요일이 아닌 날의 handoff 가 STALE 되는 건 **설계상 정상**이다.

        수집기는 매일 03:17 KST 에 새 handoff 를 내고 직전 OPEN 을 봉인한다 — 화~일의
        봉인까지 경고하면 매일 6줄이 나고, 그러면 진짜 신호(월요일)를 아무도 못 본다.
        """
        every_other_day = ["2026-09-15", "2026-09-16", "2026-09-17",
                           "2026-09-18", "2026-09-19", "2026-09-20"]
        self.assertEqual(unconsumed_publish_handoffs(every_other_day), [])

    def test_unparsable_date_is_dropped_not_guessed(self) -> None:
        """판정 근거가 없는 것을 고장이라 부르지 않는다(`prior_date` 는 "?" 일 수 있다)."""
        self.assertEqual(unconsumed_publish_handoffs(["?", "", "not-a-date"]), [])

    def test_result_is_deduped_and_sorted(self) -> None:
        got = unconsumed_publish_handoffs(
            ["2026-09-21", "2026-09-14", "2026-09-14", "2026-09-15"])
        self.assertEqual(got, ["2026-09-14", "2026-09-21"])

    def test_datetime_prefix_is_tolerated(self) -> None:
        self.assertEqual(unconsumed_publish_handoffs(["2026-09-14T00:00:00+09:00"]),
                         ["2026-09-14"])


class SealedDatesWiringTest(unittest.TestCase):
    """봉인 함수가 날짜를 실제로 내보내는지 — 반환값(건수)은 그대로여야 한다."""

    def _seal(self, pages: list[dict], keep: str, out: list[str] | None):
        def fake_api(method, url, token, body=None, **kw):
            if method == "POST" and "/query" in url:
                return {"results": pages, "has_more": False}
            return {}

        with mock.patch.object(grm_handoff, "notion_api_request", side_effect=fake_api):
            return ci.notion_stale_prior_open_handoffs(
                "tok", "db", keep_handoff_id=keep, superseded_by="2026-09-15",
                sealed_dates=out)

    def test_sealed_dates_collected(self) -> None:
        out: list[str] = []
        staled = self._seal(
            [_handoff_page("routine-handoff::2026-09-14", "2026-09-14", "New", "p14")],
            keep="routine-handoff::2026-09-15", out=out)
        self.assertEqual(staled, 1)
        self.assertEqual(out, ["2026-09-14"])
        self.assertEqual(unconsumed_publish_handoffs(out), ["2026-09-14"])

    def test_kept_handoff_is_not_collected(self) -> None:
        out: list[str] = []
        staled = self._seal(
            [_handoff_page("routine-handoff::2026-09-15", "2026-09-15", "New", "p15")],
            keep="routine-handoff::2026-09-15", out=out)
        self.assertEqual(staled, 0)
        self.assertEqual(out, [])

    def test_out_param_is_optional(self) -> None:
        """기존 호출부(out-param 없음)는 그대로 동작해야 한다 — 반환 계약 불변."""
        staled = self._seal(
            [_handoff_page("routine-handoff::2026-09-14", "2026-09-14", "New", "p14")],
            keep="routine-handoff::2026-09-15", out=None)
        self.assertEqual(staled, 1)


class HealthWiringTest(unittest.TestCase):
    """`_evaluate_health` 가 경고를 싣는지 — 그리고 **발행을 막지 않는지**."""

    def _health(self, **kw):
        import grm_health
        from collect_intake import CollectionStats
        base = dict(
            stats=CollectionStats(), active={"fr"}, enable_search=False,
            enable_mfds=False, enable_mfds_law=False, enable_mfds_recall=False,
            enable_mfds_admin=False, enable_mfds_gmp_cert=False,
            enable_mfds_safety_letter=False, enable_mfds_gmp_inspection=False,
            enable_ich=False, enable_who=False, enable_hc=False, enable_fda483=False,
            enable_moleg_api=False, enable_scrape=False, event_name="schedule",
            emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
            handoff_error_msg="",
        )
        base.update(kw)
        return grm_health._evaluate_health(**base)

    def test_missing_closeout_is_a_warning_not_a_failure(self) -> None:
        health = self._health(
            unconsumed_publish_handoff_dates=("2026-09-14",)).finalize()
        codes = [w.code for w in health.warnings]
        self.assertIn("handoff-not-consumed:2026-09-14", codes)
        # 지나간 주의 기록 문제가 이번 수집·발행을 막으면 안 된다.
        self.assertEqual(health.exit_code, 0)
        self.assertEqual([f.code for f in health.failures], [])

    def test_clean_run_says_nothing(self) -> None:
        health = self._health().finalize()
        self.assertEqual([w.code for w in health.warnings
                          if w.code.startswith("handoff-not-consumed:")], [])


if __name__ == "__main__":
    unittest.main()
