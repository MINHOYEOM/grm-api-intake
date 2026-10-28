"""fda483_notion_raw_sync — 미소비 Notion 483 행의 빈 raw 를 Supabase 본문으로 채운다(#1209)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fda483_notion_raw_sync as sync  # noqa: E402
from grm_handoff import fetch_intake_raw_payload  # noqa: E402
from grm_notion import NotionHandoffError, build_raw_payload_children  # noqa: E402

_EMPTY = {"media_id": "195591", "firm": "Axogen Corporation",
          "fda483_text_status": "not-attempted"}
_FULL = {"media_id": "195591", "firm": "Axogen Corporation",
         "fda483_excerpt": "OBSERVATION 1 Procedures were not followed.",
         "fda_483_observations": [{"number": "1", "deficiency": "Procedures were not followed."},
                                  {"number": "2", "deficiency": "Records were incomplete."}]}


def _row(doc_id: str = "fda483-195591", page_id: str = "page-1") -> dict:
    return {"page_id": page_id, "document_id": doc_id, "source": "FDA 483"}


def _run(*, rows=None, notion_raw=None, supa=None, dry_run=False, replace=None,
         source_key="fda483"):
    rows = [_row()] if rows is None else rows
    notion_raw = notion_raw if notion_raw is not None else {"page-1": _EMPTY}
    supa = supa if supa is not None else {"fda483-195591": _FULL}
    replace = replace or mock.MagicMock()
    seen_query = {}
    seen_supa_source = []

    def query(token, db, run_date, **kw):
        seen_query.update(kw)
        return list(rows)

    report, code = sync.run(
        notion_token="t", notion_db="db", base_url="https://x.supabase.co", service_key="k",
        dry_run=dry_run, run_date=date(2026, 10, 27),
        query_rows=query,
        fetch_notion_raw=lambda token, page_id: notion_raw.get(page_id),
        fetch_supabase_raw=lambda base, key, ids, source: (
            seen_supa_source.append(source) or {i: supa[i] for i in ids if i in supa}),
        replace_page_raw=replace,
        source_key=source_key,
    )
    seen_query["_supa_source"] = seen_supa_source
    return report, code, replace, seen_query


class NotionRawSyncTest(unittest.TestCase):

    def test_empty_notion_row_gets_supabase_body(self):
        report, code, replace, query = _run()
        self.assertEqual(code, 0)
        self.assertEqual(query.get("source_names"), {"FDA 483"})
        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.synced, 1)
        self.assertEqual(report.observations_synced, 2)
        replace.assert_called_once_with("t", "page-1", _FULL)

    def test_dry_run_does_not_write(self):
        report, _code, replace, _q = _run(dry_run=True)
        self.assertEqual(report.synced, 1)
        replace.assert_not_called()

    def test_row_that_already_has_body_is_left_alone(self):
        report, _code, replace, _q = _run(notion_raw={"page-1": _FULL})
        self.assertEqual(report.already_has_body, 1)
        self.assertEqual(report.candidates, 0)
        replace.assert_not_called()

    def test_supabase_also_empty_is_not_written(self):
        """빈손을 빈손으로 덮지 않는다 — 회수 예약이 Supabase 를 채운 뒤 다음 실행이 처리한다."""
        report, _code, replace, _q = _run(supa={"fda483-195591": _EMPTY})
        self.assertEqual(report.supabase_also_empty, 1)
        self.assertEqual(report.synced, 0)
        replace.assert_not_called()

    def test_unreadable_notion_raw_is_not_touched(self):
        report, _code, replace, _q = _run(notion_raw={})
        self.assertEqual(report.notion_raw_unreadable, 1)
        replace.assert_not_called()

    def test_missing_supabase_row_is_counted(self):
        report, _code, replace, _q = _run(supa={})
        self.assertEqual(report.supabase_missing, 1)
        replace.assert_not_called()

    def test_write_failure_is_counted_and_surfaced(self):
        boom = mock.MagicMock(side_effect=NotionHandoffError("Notion API 500"))
        report, code, _replace, _q = _run(replace=boom)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.synced, 0)
        self.assertEqual(code, 1)

    def test_bad_supabase_url_exits_2(self):
        report, code = sync.run(
            notion_token="t", notion_db="db", base_url="http://x", service_key="k",
            dry_run=True, run_date=date(2026, 10, 27), query_rows=lambda *a, **k: [])
        self.assertEqual(code, 2)
        self.assertTrue(report.errors)


class ReplacePageRawTest(unittest.TestCase):
    """children 교체 순서: 새 블록을 먼저 붙이고 → 기존 블록을 보관한다(빈 페이지 창 없음)."""

    def test_append_before_archive_and_round_trip(self):
        calls = []

        def fake_request(method, url, token, body=None, retries=2):
            calls.append((method, url, body))
            if method == "GET":
                return {"results": [{"id": "old-1"}, {"id": "old-2"}], "has_more": False}
            return {}

        with mock.patch.object(sync, "notion_api_request", fake_request), \
                mock.patch.object(sync.time, "sleep", lambda s: None):
            sync._replace_page_raw("t", "page-1", _FULL)
        kinds = [(m, "archived" in (b or {})) for m, _u, b in calls]
        first_archive = kinds.index(("PATCH", True))
        last_append = max(i for i, k in enumerate(kinds) if k == ("PATCH", False))
        self.assertLess(last_append, first_archive)
        self.assertEqual(sum(1 for k in kinds if k == ("PATCH", True)), 2)

        # 붙인 블록을 수집기의 읽기 함수로 되읽으면 같은 raw 가 나와야 한다.
        appended = [blk for m, _u, b in calls if m == "PATCH" and b and "children" in b
                    for blk in b["children"]]
        self.assertEqual(appended, build_raw_payload_children(_FULL))
        with mock.patch("grm_handoff.notion_api_request",
                        lambda *a, **k: {"results": appended, "has_more": False}):
            self.assertEqual(fetch_intake_raw_payload("t", "page-1"), _FULL)



_WL_EXCERPT_ONLY = {"firm": "Acme", "url": "https://www.fda.gov/x", "wl_body_excerpt": "During ...",
                    "wl_body_status": "fetch-503"}
_WL_FULL = {"firm": "Acme", "url": "https://www.fda.gov/x", "wl_body_excerpt": "During ...",
            "wl_body_full": "During our inspection ... 1. Your firm failed ...",
            "wl_violations": [{"number": "1", "statement": "Your firm failed ..."}]}


class NotionRawSyncWarningLetterTest(unittest.TestCase):
    """[2026-10-28] `--source wl` — WL 의 본문 판정은 `wl_body_full`(fan-out 입력)이다."""

    def _wl(self, **kw):
        kw.setdefault("rows", [{"page_id": "page-1", "document_id": "abc123", "source": "FDA Warning Letter"}])
        kw.setdefault("notion_raw", {"page-1": _WL_EXCERPT_ONLY})
        kw.setdefault("supa", {"abc123": _WL_FULL})
        return _run(source_key="wl", **kw)

    def test_excerpt_only_row_is_synced_with_full_body(self):
        """발췌만 있는 행은 카드 신호로는 '본문 있음'이지만 fan-out 입력이 없다 — 동기화 대상."""
        report, code, replace, query = self._wl()
        self.assertEqual(code, 0)
        self.assertEqual(query.get("source_names"), {"FDA Warning Letter"})
        self.assertEqual(query["_supa_source"], ["FDA Warning Letter"])
        self.assertEqual(report.source, "FDA Warning Letter")
        self.assertEqual((report.candidates, report.synced, report.observations_synced), (1, 1, 1))
        replace.assert_called_once_with("t", "page-1", _WL_FULL)

    def test_row_with_full_body_is_left_alone(self):
        report, _code, replace, _q = self._wl(notion_raw={"page-1": _WL_FULL})
        self.assertEqual(report.already_has_body, 1)
        replace.assert_not_called()

    def test_supabase_without_full_body_is_not_written(self):
        report, _code, replace, _q = self._wl(supa={"abc123": _WL_EXCERPT_ONLY})
        self.assertEqual(report.supabase_also_empty, 1)
        replace.assert_not_called()

    def test_cli_accepts_source_wl(self):
        self.assertEqual(sync.build_arg_parser().parse_args(["--source", "wl"]).source, "wl")
        self.assertEqual(sync.build_arg_parser().parse_args([]).source, "fda483")


if __name__ == "__main__":
    unittest.main()
