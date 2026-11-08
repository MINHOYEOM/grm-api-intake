#!/usr/bin/env python3
"""findings_review_queue — 검토 대기 큐 나이 측정·사람 판정 적용 테스트(HTTP 전부 목킹)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import findings_review_queue as q


_BASE = "https://example.supabase.co"
_KEY = "service-role-secret-token"
_NOW = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
_REPO = Path(__file__).resolve().parents[1]


def _row(fid: str, days_ago: float | None, scope: str = "ok", source: str = "FDA Warning Letter") -> dict:
    ts = None if days_ago is None else (_NOW - timedelta(days=days_ago)).isoformat()
    return {"finding_id": fid, "source": source, "scope_status": scope,
            "document_id": "doc", "firm_name": "Firm | Inc", "ingested_at": ts}


class _Resp:
    def __init__(self, status: int, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class SummarizeTest(unittest.TestCase):
    def test_empty_queue_is_ok(self):
        r = q.summarize([], now=_NOW, max_age_days=3)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["needs_review"], 0)
        self.assertIsNone(r["oldest_age_days"])

    def test_young_rows_are_not_overdue(self):
        r = q.summarize([_row("finding-a", 0.5), _row("finding-b", 3.0)], now=_NOW, max_age_days=3)
        self.assertEqual(r["status"], "ok")      # 3.0 은 초과가 아니다(경계 포함 허용)
        self.assertEqual(r["needs_review"], 2)
        self.assertEqual(r["oldest_age_days"], 3.0)

    def test_overdue_row_breaches_and_is_listed_oldest_first(self):
        rows = [_row("finding-a", 4), _row("finding-b", 111, scope="non_pharma"), _row("finding-c", 1)]
        r = q.summarize(rows, now=_NOW, max_age_days=3)
        self.assertEqual(r["status"], "overdue")
        self.assertEqual(r["overdue"], 2)
        self.assertEqual([x["finding_id"] for x in r["overdue_rows"]], ["finding-b", "finding-a"])
        self.assertEqual(r["needs_review_visible"], 2)
        self.assertEqual(r["by_scope"], {"non_pharma": 1, "ok": 2})

    def test_missing_timestamp_counts_as_overdue_not_zero_days(self):
        r = q.summarize([_row("finding-x", None)], now=_NOW, max_age_days=3)
        self.assertEqual(r["status"], "overdue")
        self.assertEqual(r["unknown_age"], 1)

    def test_listing_is_capped_and_truncation_is_reported(self):
        rows = [_row(f"finding-{i:03d}", 10 + i) for i in range(q._MAX_LISTED + 5)]
        r = q.summarize(rows, now=_NOW, max_age_days=3)
        self.assertEqual(len(r["overdue_rows"]), q._MAX_LISTED)
        self.assertEqual(r["overdue_rows_truncated"], 5)


class MeasureFetchTest(unittest.TestCase):
    def test_fetch_filters_needs_review_only_and_never_reads_text(self):
        resp = _Resp(200, [], headers={"Content-Range": "*/0"})
        with mock.patch("findings_supabase_backfill.requests.get", return_value=resp) as g:
            report = q.run_measure(_BASE, _KEY, max_age_days=3, now=_NOW)
        params = g.call_args.kwargs["params"]
        self.assertEqual(params["review_status"], "eq.needs_review")
        self.assertNotIn("scope_status", params)            # 모든 scope 를 본다
        self.assertNotIn("finding_text", params["select"])
        self.assertEqual(report["status"], "ok")

    def test_http_error_is_error_status_without_key(self):
        resp = _Resp(500, None)
        with mock.patch("findings_supabase_backfill.requests.get", return_value=resp):
            report = q.run_measure(_BASE, _KEY, max_age_days=3, now=_NOW)
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["errors"])
        self.assertNotIn(_KEY, repr(report))

    def test_main_exit_codes(self):
        rows = [_row("finding-a", 10)]
        resp = _Resp(200, rows, headers={"Content-Range": "0-0/1"})
        env = {"SUPABASE_URL": _BASE, "SUPABASE_SERVICE_ROLE_KEY": _KEY}
        with mock.patch.dict(os.environ, env), \
             mock.patch("findings_supabase_backfill.requests.get", return_value=resp), \
             mock.patch("builtins.print"):
            self.assertEqual(q.main(["measure"]), 1)
            self.assertEqual(q.main(["measure", "--max-age-days", "30"]), 0)


class DecisionsTest(unittest.TestCase):
    def _write(self, data) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.remove, path)
        return path

    def test_committed_decision_file_is_valid(self):
        items = q.load_decisions(str(_REPO / "docs" / "findings_review_decisions_2026-11-08.json"))
        self.assertEqual(len(items), 7)

    def test_rejects_bad_decision_missing_reason_and_duplicates(self):
        for bad in (
            [{"finding_id": "finding-a", "decision": "maybe", "reason": "x"}],
            [{"finding_id": "finding-a", "decision": "accepted", "reason": ""}],
            [{"finding_id": "finding-a", "decision": "accepted", "reason": "x"},
             {"finding_id": "finding-a", "decision": "rejected", "reason": "y"}],
            [{"finding_id": "abc", "decision": "accepted", "reason": "x"}],
        ):
            with self.assertRaises(ValueError):
                q.load_decisions(self._write(bad))

    def test_dry_run_is_default_and_sends_nothing(self):
        items = [{"finding_id": "finding-a", "decision": "rejected", "reason": "x"}]
        with mock.patch.object(q.requests, "patch") as p:
            r = q.run_decide(_BASE, _KEY, items, apply=False)
        p.assert_not_called()
        self.assertEqual(r["mode"], "dry_run")
        self.assertEqual(r["planned_rejected"], 1)

    def test_apply_patches_with_needs_review_guard(self):
        items = [{"finding_id": "finding-a", "decision": "accepted", "reason": "x"},
                 {"finding_id": "finding-b", "decision": "rejected", "reason": "y"}]
        responses = [_Resp(200, [{"finding_id": "finding-a"}]), _Resp(200, [])]
        with mock.patch.object(q.requests, "patch", side_effect=responses) as p:
            r = q.run_decide(_BASE, _KEY, items, apply=True)
        self.assertEqual(r["patched"], 1)
        self.assertEqual(r["matched_zero"], 1)      # 이미 판정됨 — 멱등
        for call in p.call_args_list:
            self.assertEqual(call.kwargs["params"]["review_status"], "eq.needs_review")
        self.assertEqual(p.call_args_list[0].kwargs["json"], {"review_status": "accepted"})

    def test_apply_error_surfaces_status_not_key(self):
        items = [{"finding_id": "finding-a", "decision": "accepted", "reason": "x"}]
        with mock.patch.object(q.requests, "patch", return_value=_Resp(403, None)):
            r = q.run_decide(_BASE, _KEY, items, apply=True)
        self.assertIn("http_403", repr(r))
        self.assertNotIn(_KEY, repr(r))


class WorkflowContractTest(unittest.TestCase):
    def test_workflow_uses_label_reuses_issue_and_closes_on_recovery(self):
        text = (_REPO / ".github" / "workflows" / "grm-findings-review-queue.yml").read_text(encoding="utf-8")
        self.assertIn("const label = 'findings-review-queue';", text)
        self.assertIn("open.find(i => i.title === title)", text)       # 같은 이슈 재사용
        self.assertIn("steps.measure.outcome == 'success'", text)      # 정상 복귀 시 닫기
        self.assertIn("python findings_review_queue.py", text)
        # 입력은 env 로만 넘긴다(run 스크립트에 표현식 직접 삽입 금지)
        self.assertIn("MAX_AGE_DAYS: ${{ inputs.max_age_days }}", text)
        self.assertEqual(text.count("${{ inputs.max_age_days }}"), 1)


if __name__ == "__main__":
    unittest.main()
