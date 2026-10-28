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
_OUT_OF_SCOPE = {"media_id": "195591", "firm": "Krispy Kreme Doughnut Corporation",
                 "establishment_type": "Manufacturer", "fda483_text_status": "out-of-scope"}
_FULL = {"media_id": "195591", "firm": "Axogen Corporation",
         "fda483_excerpt": "OBSERVATION 1 Procedures were not followed.",
         "fda_483_observations": [{"number": "1", "deficiency": "Procedures were not followed."},
                                  {"number": "2", "deficiency": "Records were incomplete."}]}


def _row(doc_id: str = "fda483-195591", page_id: str = "page-1") -> dict:
    return {"page_id": page_id, "document_id": doc_id, "source": "FDA 483"}


def _run(*, rows=None, notion_raw=None, supa=None, dry_run=False, replace=None, skip=None):
    rows = [_row()] if rows is None else rows
    notion_raw = notion_raw if notion_raw is not None else {"page-1": _EMPTY}
    supa = supa if supa is not None else {"fda483-195591": _FULL}
    replace = replace or mock.MagicMock()
    skip = skip or mock.MagicMock()
    seen_query = {}

    def query(token, db, run_date, **kw):
        seen_query.update(kw)
        return list(rows)

    report, code = sync.run(
        notion_token="t", notion_db="db", base_url="https://x.supabase.co", service_key="k",
        dry_run=dry_run, run_date=date(2026, 10, 27),
        query_rows=query,
        fetch_notion_raw=lambda token, page_id: notion_raw.get(page_id),
        fetch_supabase_raw=lambda base, key, ids: {i: supa[i] for i in ids if i in supa},
        replace_page_raw=replace,
        mark_row_skipped=skip,
    )
    _run.skip = skip
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

    def test_out_of_scope_row_is_closed_as_skipped(self):
        """회수가 범위 밖으로 확정한 문서는 Notion 행을 Skipped 로 마감한다(#1228 후속).

        본문 없는 New 행으로 남으면 다음 주 스캐폴드가 '관찰 본문 미확보' 묶음 카드에 넣는다
        (10-26 호 범위 밖 7건). Skipped 면 Status=New 조회에서 빠져 handoff·스캐폴드·조립
        어디에도 들어가지 않는다 — 수집기가 처음부터 범위 밖으로 버린 문서와 같은 결과다."""
        report, code, replace, _q = _run(supa={"fda483-195591": _OUT_OF_SCOPE})
        self.assertEqual(code, 0)
        self.assertEqual(report.skipped_out_of_scope, 1)
        self.assertEqual(report.supabase_also_empty, 0)
        self.assertEqual(report.synced, 0)
        _run.skip.assert_called_once_with("t", "page-1")
        replace.assert_not_called()   # raw 는 그대로 — Status 만 바꾼다
        self.assertEqual(report.samples[0]["action"], "skipped_out_of_scope")

    def test_out_of_scope_marker_on_drug_establishment_is_not_closed(self):
        """[2026-10-28 실측] 시설 유형이 의약품인데 범위 밖으로 찍힌 문서(Victory Medical Center
        Pharmacy 등)는 Skipped 로 마감하지 않는다 — 범위 규칙은 그런 문서를 범위 밖으로 보지
        않으므로 표식이 오판일 수 있다. 종전처럼 New 로 남아 묶음 카드에 들어간다."""
        oos_drug = dict(_OUT_OF_SCOPE, firm="Victory Medical Center Pharmacy",
                        establishment_type="Producer of Sterile and Non Sterile Drug Products")
        report, _code, replace, _q = _run(supa={"fda483-195591": oos_drug})
        self.assertEqual(report.skipped_out_of_scope, 0)
        self.assertEqual(report.out_of_scope_kept_pharma, 1)
        _run.skip.assert_not_called()
        replace.assert_not_called()

    def test_out_of_scope_marker_on_device_establishment_is_closed(self):
        oos_dev = dict(_OUT_OF_SCOPE, establishment_type="Software Manufacturer")
        report, _code, _replace, _q = _run(supa={"fda483-195591": oos_dev})
        self.assertEqual(report.skipped_out_of_scope, 1)
        _run.skip.assert_called_once_with("t", "page-1")

    def test_out_of_scope_dry_run_does_not_write(self):
        report, _code, replace, _q = _run(supa={"fda483-195591": _OUT_OF_SCOPE}, dry_run=True)
        self.assertEqual(report.skipped_out_of_scope, 1)
        _run.skip.assert_not_called()
        replace.assert_not_called()

    def test_out_of_scope_skip_failure_is_counted(self):
        boom = mock.MagicMock(side_effect=NotionHandoffError("Notion API 500"))
        report, code, _replace, _q = _run(supa={"fda483-195591": _OUT_OF_SCOPE}, skip=boom)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.skipped_out_of_scope, 0)
        self.assertEqual(code, 1)

    def test_out_of_scope_with_body_in_notion_is_left_alone(self):
        """Notion 에 이미 본문이 있으면(사람이 고친 행 등) 판정 대상이 아니다 — 종전 규칙 그대로."""
        report, _code, _replace, _q = _run(notion_raw={"page-1": _FULL},
                                           supa={"fda483-195591": _OUT_OF_SCOPE})
        self.assertEqual(report.skipped_out_of_scope, 0)
        _run.skip.assert_not_called()

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


class PharmaEstablishmentTest(unittest.TestCase):
    """`is_pharma_establishment_483` 가 참이면 범위 규칙은 본문과 무관하게 범위 안이다."""

    def test_cases(self):
        from grm_taxonomy import is_out_of_scope_483, is_pharma_establishment_483
        for est in ("Drug Manufacturer", "Producer of Sterile and Non Sterile Drug Products",
                    "Biologics & Medical Device Manufacturer"):
            self.assertTrue(is_pharma_establishment_483(est), est)
            self.assertFalse(is_out_of_scope_483(est, "21 CFR 820.30 design controls"), est)
        for est in ("Manufacturer", "Software Manufacturer", "Contract Sterilizer", "",
                    "Blood Bank Drug Manufacturer"):
            self.assertFalse(is_pharma_establishment_483(est), est)


class MarkRowSkippedTest(unittest.TestCase):
    """Skipped 마감은 그 행의 Status 한 속성만 PATCH 한다(Handoff Ref·raw 무접촉)."""

    def test_patches_only_status(self):
        calls = []

        def fake_request(method, url, token, body=None, retries=2):
            calls.append((method, url, body))
            return {}

        with mock.patch.object(sync, "notion_api_request", fake_request), \
                mock.patch.object(sync.time, "sleep", lambda s: None):
            sync._mark_row_skipped("t", "page-1")
        self.assertEqual(calls, [("PATCH", "https://api.notion.com/v1/pages/page-1",
                                  {"properties": {"Status": {"select": {"name": "Skipped"}}}})])


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


if __name__ == "__main__":
    unittest.main()
