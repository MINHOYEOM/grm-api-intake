#!/usr/bin/env python3
"""FIND-1 M1k read-only Notion Intake export tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import findings_notion_export as notion_export


def _title(value: str) -> dict:
    return {"title": [{"plain_text": value}]}


def _rich_text(value: str) -> dict:
    return {"rich_text": [{"plain_text": value}]}


def _select(value: str) -> dict:
    return {"select": {"name": value} if value else None}


def _date(value: str) -> dict:
    return {"date": {"start": value} if value else None}


def _url(value: str) -> dict:
    return {"url": value}


def _page(
    page_id: str,
    *,
    source: str = "FDA Warning Letter",
    document_id: str = "doc-1",
    type_or_class: str = "warning-letter",
    status: str = "New",
    run_date: str = "2026-07-07",
) -> dict:
    headline = f"{source} {document_id}"
    return {
        "id": page_id,
        "url": f"https://notion.test/{page_id}",
        "properties": {
            "Name": _title(headline),
            "Source": _select(source),
            "Document ID": _rich_text(document_id),
            "Date": _date("2026-07-07"),
            "Headline": _rich_text(headline),
            "Official URL": _url(f"https://example.test/{document_id}"),
            "Source URL": _url(f"https://source.test/{document_id}"),
            "Type or Class": _select(type_or_class),
            "Firm": _rich_text("Example Firm"),
            "Body": _rich_text("Example body"),
            "Distribution": _rich_text(""),
            "Comments Close": _date(""),
            "Run Date (KST)": _date(run_date),
            "Collected At": _date(run_date),
            "API Query": _url(""),
            "Search Query": _rich_text(""),
            "Raw Excerpt": _rich_text(""),
            "QA Relevance": _select("High"),
            "OSD Relevance": _select("Direct"),
            "Modality": _select("Chemical"),
            "Source Type": _select("official"),
            "Signal Tier": _select("Tier 1"),
            "Evidence Candidate": _select("A"),
            "Language": _select("en"),
            "Region/Jurisdiction": _select("US"),
            "Site Country": _rich_text("US"),
            "Status": _select(status),
        },
    }


class FindingsNotionExportTest(unittest.TestCase):
    def test_export_reads_signal_pages_and_reports_missing_raw_without_writes(self) -> None:
        pages = [
            _page("page-ok", document_id="fda-1"),
            _page("page-missing", source="MFDS", document_id="mfds-1", type_or_class="gmp-notice"),
            _page(
                "page-handoff",
                source="GRM Handoff",
                document_id="routine-handoff::2026-07-07",
                type_or_class="routine-handoff",
            ),
            _page(
                "page-web-delta",
                source="GRM Web Delta",
                document_id="web-delta::2026-07-07",
                type_or_class="web-delta",
            ),
        ]
        query_result = {"results": pages, "has_more": False}
        raw = {"id": "fda-1", "raw": True}

        with mock.patch.object(notion_export, "notion_api_request", return_value=query_result) as query:
            with mock.patch.object(notion_export, "fetch_intake_raw_payload", side_effect=[raw, None]) as fetch:
                result = notion_export.export_notion_intake(
                    token="token",
                    database_id="db",
                    sleep_s=0,
                )

        query.assert_called_once()
        fetch.assert_has_calls([
            mock.call("token", "page-ok"),
            mock.call("token", "page-missing"),
        ])
        self.assertEqual(result["schema_version"], notion_export.NOTION_EXPORT_SCHEMA_VERSION)
        self.assertEqual([row["page_id"] for row in result["rows"]], ["page-ok", "page-missing"])
        self.assertEqual(result["raw_by_page_id"], {"page-ok": raw})
        self.assertEqual(result["raw_by_key"], {"FDA Warning Letter::fda-1": raw})

        report = result["report"]
        self.assertEqual(report["mode"], "notion_read_only_export")
        self.assertEqual(report["pages_seen"], 4)
        self.assertEqual(report["signal_rows_exported"], 2)
        self.assertEqual(report["raw_fetch_attempted"], 2)
        self.assertEqual(report["raw_fetch_ok"], 1)
        self.assertEqual(report["raw_fetch_missing"], 1)
        self.assertEqual(report["missing_raw"][0]["row_key"], "MFDS::mfds-1")
        self.assertEqual([p["reason"] for p in report["skipped_pages"]], ["routine_handoff", "web_delta"])
        self.assertEqual(report["preflight"]["notion_api"], "read_only")
        self.assertEqual(report["preflight"]["sqlite_write"], "not_used")
        self.assertEqual(report["preflight"]["supabase_write"], "not_used")
        self.assertEqual(report["preflight"]["status_handoff"], "not_used")
        self.assertEqual(report["preflight"]["blocking_errors"], 1)
        self.assertFalse(report["preflight"]["ready_for_backfill_plan"])

    def test_query_filter_limit_and_page_size_are_applied(self) -> None:
        query_result = {
            "results": [_page("page-limited", document_id="limited-1")],
            "has_more": True,
            "next_cursor": "cursor-1",
        }
        with mock.patch.object(notion_export, "notion_api_request", return_value=query_result) as query:
            with mock.patch.object(notion_export, "fetch_intake_raw_payload", return_value={"id": "limited-1"}):
                result = notion_export.export_notion_intake(
                    token="token",
                    database_id="db",
                    limit=1,
                    page_size=500,
                    sleep_s=0,
                    status_names=["New", "Consumed"],
                    run_date_from="2026-07-01",
                    run_date_to="2026-07-08",
                )

        self.assertEqual(len(result["rows"]), 1)
        query.assert_called_once()
        body = query.call_args.kwargs["body"]
        self.assertEqual(body["page_size"], 100)
        self.assertEqual(body["sorts"], [{"property": "Run Date (KST)", "direction": "ascending"}])
        self.assertEqual(
            body["filter"],
            {
                "and": [
                    {
                        "or": [
                            {"property": "Status", "select": {"equals": "New"}},
                            {"property": "Status", "select": {"equals": "Consumed"}},
                        ]
                    },
                    {"property": "Run Date (KST)", "date": {"on_or_after": "2026-07-01"}},
                    {"property": "Run Date (KST)", "date": {"on_or_before": "2026-07-08"}},
                ]
            },
        )
        self.assertEqual(result["report"]["query"]["page_size"], 100)

    def test_cli_rejects_missing_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "findings_notion_export.json")
            with mock.patch.dict(os.environ, {}, clear=True):
                rc = notion_export.main(["--output", out])

            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(out))

    def test_cli_reports_notion_api_error_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "findings_notion_export.json")
            with mock.patch.object(notion_export, "export_notion_intake", side_effect=RuntimeError("HTTP 401")):
                rc = notion_export.main([
                    "--notion-token",
                    "token",
                    "--database-id",
                    "db",
                    "--output",
                    out,
                ])

            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(out))

    def test_cli_writes_export_json(self) -> None:
        payload = {
            "schema_version": notion_export.NOTION_EXPORT_SCHEMA_VERSION,
            "rows": [],
            "raw_by_page_id": {},
            "raw_by_key": {},
            "report": {"preflight": {"notion_api": "read_only"}},
        }
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "findings_notion_export.json")
            with mock.patch.object(notion_export, "export_notion_intake", return_value=payload) as export:
                rc = notion_export.main([
                    "--notion-token",
                    "token",
                    "--database-id",
                    "db",
                    "--output",
                    out,
                    "--pretty",
                ])

            self.assertEqual(rc, 0)
            export.assert_called_once()
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["schema_version"], notion_export.NOTION_EXPORT_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
