"""Notion 중복 조회 재시도(2026-09-27) — 30초 무응답 1회가 하루 적재를 통째로 멈췄다.

run 36272185568(09-27 KST): 수집은 전부 끝났는데(FR 16·Recall 58·WHO 167 …) 적재 직전
중복 조회의 첫 페이지가 `Read timed out (read timeout=30)` 1회 → insert 전면 중단 →
그날 적재 0건·handoff 미생성. 같은 조회는 직전 7일 내내 2~4초였다.

결함은 재시도 루프가 **응답이 온 경우**(429·5xx)만 다시 시도했다는 것 — 네트워크 예외는
첫 시도에서 곧장 fail-closed 로 빠졌다. 여기서는 ①네트워크 예외를 재시도하는지
②다 써도 여전히 fail-closed 인지(불완전한 dedup 으로 insert 하면 대량 중복) ③4xx 는
재시도하지 않는지 ④중단 경로가 원인을 health JSON 으로 남기는지를 고정한다.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci  # noqa: E402
import grm_notion  # noqa: E402

RUN_DATE = date(2026, 9, 27)


def _page(rows, *, has_more=False, next_cursor=None):
    """Notion DB query 응답 1페이지. rows = [(source, doc_id), ...]."""
    results = [{
        "properties": {
            grm_notion.PROP_SOURCE: {"select": {"name": src}},
            grm_notion.PROP_DOC_ID: {"rich_text": [{"plain_text": doc_id}]},
        },
    } for src, doc_id in rows]
    return {"results": results, "has_more": has_more, "next_cursor": next_cursor}


def _resp(status, payload=None):
    r = mock.Mock()
    r.status_code = status
    r.text = json.dumps(payload or {})
    r.headers = {}
    r.json.return_value = payload or {}
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    else:
        r.raise_for_status.return_value = None
    return r


class _Post:
    """requests.post 대역 — 스크립트 순서대로 예외를 던지거나 응답을 돌려주고,
    호출 시점의 start_cursor 를 기록한다(body 는 호출부가 제자리에서 바꾸므로 복사해 둔다)."""

    def __init__(self, script):
        self.script = list(script)
        self.cursors: list[str | None] = []

    def __call__(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.cursors.append((json or {}).get("start_cursor"))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _query(script):
    post = _Post(script)
    with mock.patch.object(grm_notion.requests, "post", side_effect=post), \
            mock.patch.object(grm_notion.time, "sleep") as sleep:
        keys = grm_notion.notion_query_existing_keys("tok", "db", RUN_DATE, window_days=30)
    return keys, post, sleep


class NetworkErrorRetryTest(unittest.TestCase):

    def test_read_timeout_is_retried_not_fatal(self):
        """★09-27 그대로 — 첫 시도 ReadTimeout, 두 번째 정상 → 조회 성공."""
        keys, post, _ = _query([
            requests.ReadTimeout("Read timed out. (read timeout=30)"),
            _resp(200, _page([("Federal Register", "2026-19654")])),
        ])
        self.assertEqual(keys.doc_ids, {"Federal Register::2026-19654"})
        self.assertEqual(len(post.cursors), 2)

    def test_connection_error_mid_pagination_retries_the_same_page(self):
        """2페이지째 끊겨도 같은 커서로 다시 받아 1·2페이지를 모두 모은다."""
        keys, post, _ = _query([
            _resp(200, _page([("EMA", "a")], has_more=True, next_cursor="c2")),
            requests.ConnectionError("Connection reset by peer"),
            _resp(200, _page([("EMA", "b")])),
        ])
        self.assertEqual(keys.doc_ids, {"EMA::a", "EMA::b"})
        self.assertEqual(post.cursors, [None, "c2", "c2"])

    def test_backoff_waits_between_network_retries(self):
        _keys, _post, sleep = _query([
            requests.ReadTimeout("t1"),
            requests.ReadTimeout("t2"),
            _resp(200, _page([])),
        ])
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(len(waits), 2)
        self.assertTrue(all(w > 0 for w in waits))

    def test_persistent_timeout_still_fails_closed(self):
        """다 써도 안 되면 종전대로 예외 — 빈/불완전 dedup 으로 insert 하지 않는다."""
        attempts = grm_notion._DEDUP_ATTEMPTS
        with self.assertRaises(grm_notion.NotionDedupeQueryError) as ctx:
            _query([requests.ReadTimeout("still down")] * attempts)
        self.assertIn("still down", str(ctx.exception))

    def test_attempts_are_bounded(self):
        post = _Post([requests.ReadTimeout("x")] * 50)
        with mock.patch.object(grm_notion.requests, "post", side_effect=post), \
                mock.patch.object(grm_notion.time, "sleep"), \
                self.assertRaises(grm_notion.NotionDedupeQueryError):
            grm_notion.notion_query_existing_keys("tok", "db", RUN_DATE)
        self.assertEqual(len(post.cursors), grm_notion._DEDUP_ATTEMPTS)
        self.assertGreater(grm_notion._DEDUP_ATTEMPTS, 1)

    def test_client_error_is_not_retried(self):
        """400·401 은 다시 보내도 같은 답 — 첫 응답에서 바로 중단."""
        post = _Post([_resp(400, {"message": "bad filter"}), _resp(200, _page([]))])
        with mock.patch.object(grm_notion.requests, "post", side_effect=post), \
                mock.patch.object(grm_notion.time, "sleep"), \
                self.assertRaises(grm_notion.NotionDedupeQueryError):
            grm_notion.notion_query_existing_keys("tok", "db", RUN_DATE)
        self.assertEqual(len(post.cursors), 1)

    def test_server_error_retry_unchanged(self):
        keys, post, _ = _query([
            _resp(503, {"message": "unavailable"}),
            _resp(200, _page([("PIC/S", "p1")])),
        ])
        self.assertEqual(keys.doc_ids, {"PIC/S::p1"})
        self.assertEqual(len(post.cursors), 2)


class DedupeAbortHealthJsonTest(unittest.TestCase):
    """중단 경로가 health JSON 없이 `return 1` 하던 것 — 이슈 #1084 가 원인 없이
    "Health status: unavailable" 만 보였고 실행일도 UTC 폴백(09-26)으로 적혔다."""

    def _run_main(self, health_path):
        stats = ci.CollectionStats()
        collected = (stats,) + tuple([] for _ in range(22))
        env = {"NOTION_TOKEN": "tok", "NOTION_DATABASE_ID": "db",
               "GRM_HEALTH_JSON": health_path}
        fixed_now = datetime(2026, 9, 27, 6, 17, tzinfo=ci.KST)
        err = grm_notion.NotionDedupeQueryError(
            "Notion 중복 조회 실패 (RunDate=2026-09-27): Read timed out. (read timeout=30)")
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["collect_intake.py", "--emit-routine-handoff"]), \
                mock.patch.object(ci, "now_kst", return_value=fixed_now), \
                mock.patch.object(ci, "notion_verify_modality_property", return_value=True), \
                mock.patch.object(ci, "notion_verify_handoff_ref_property", return_value=True), \
                mock.patch.object(ci, "probe_kr_egress_proxy", return_value=("unconfigured", "")), \
                mock.patch.object(ci, "_fda483_known_document_ids", return_value=None), \
                mock.patch.object(ci, "_run_collection", return_value=collected), \
                mock.patch.object(ci, "notion_query_existing_keys", side_effect=err), \
                mock.patch.object(ci, "notion_create_page") as create:
            code = ci.main()
        return code, create

    def test_abort_writes_failure_health_with_cause_and_kst_date(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "grm-health.json")
            code, create = self._run_main(path)
            self.assertEqual(code, 1)
            create.assert_not_called()                 # fail-closed 는 그대로
            self.assertTrue(os.path.exists(path), "중단 경로가 health JSON 을 남겨야 한다")
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        self.assertEqual(payload["run_date_kst"], "2026-09-27")
        self.assertEqual(payload["health"]["status"], "failure")
        codes = [x["code"] for x in payload["health"]["failures"]]
        self.assertEqual(codes, ["notion-dedupe-query-failed"])
        self.assertIn("Read timed out", payload["health"]["failures"][0]["detail"])
        self.assertFalse(payload["handoff"]["emitted"])


if __name__ == "__main__":
    unittest.main()
