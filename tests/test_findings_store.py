#!/usr/bin/env python3
"""FIND-1 M1c/M1g SQLite append boundary tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import collect_intake as ci
import findings_store as store
import grm_findings as gf


def _item(**overrides) -> ci.IntakeItem:
    base = dict(
        source="FDA 483",
        document_id="fda483-192439",
        date_iso="2026-05-27",
        headline="[FDA 483] BPI Labs, LLC",
        official_url="https://www.fda.gov/media/192439/download",
        type_or_class="483",
        firm="BPI Labs, LLC",
        body="There is a failure to review unexplained discrepancies.",
        qa_relevance="Likely",
        osd_relevance="Direct",
        signal_tier="Tier 3",
        raw_payload={
            "firm": "BPI Labs, LLC",
            "media_id": "192439",
            "fda_483_observations": [
                {"number": "1", "deficiency": "Failure to investigate discrepancies."}
            ],
        },
    )
    base.update(overrides)
    return ci.IntakeItem(**base)


class FindingsStoreTest(unittest.TestCase):
    def test_raw_signal_from_intake_item_matches_schema(self) -> None:
        collected_at = datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc)
        record = store.raw_signal_from_intake_item(_item(), collected_at=collected_at)

        self.assertEqual(record["schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(record["source"], "FDA 483")
        self.assertEqual(record["source_kind"], "483")
        self.assertEqual(record["document_id"], "fda483-192439")
        self.assertEqual(record["collected_at"], "2026-07-08T00:00:00+00:00")
        self.assertEqual(gf.validate_raw_signal(record), [])
        row = json.loads(record["row_json"])
        self.assertEqual(row["signal_tier"], "Tier 3")
        self.assertEqual(row["firm"], "BPI Labs, LLC")

    def test_append_intake_item_to_sqlite_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")

            first = store.append_intake_item_to_sqlite(db, _item())
            second = store.append_intake_item_to_sqlite(db, _item())

            self.assertEqual(first.status, "inserted")
            self.assertEqual(second.status, "duplicate")
            conn = sqlite3.connect(db)
            try:
                count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
                raw_json = conn.execute("SELECT raw_json FROM raw_signals").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 1)
            self.assertEqual(json.loads(raw_json)["media_id"], "192439")

    def test_append_raw_signal_ignores_non_schema_export_metadata(self) -> None:
        record = store.raw_signal_from_intake_item(_item())
        record["export_source"] = "page_id:page-fda-483-192439"

        conn = sqlite3.connect(":memory:")
        try:
            store.ensure_findings_schema(conn)
            result = store.append_raw_signal(conn, record)

            self.assertEqual(result.status, "inserted")
            count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_append_intake_item_with_findings_to_sqlite_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")

            first = store.append_intake_item_with_findings_to_sqlite(db, _item())
            second = store.append_intake_item_with_findings_to_sqlite(db, _item())

            self.assertEqual(first.status, "inserted")
            self.assertEqual(first.raw_signal_status, "inserted")
            self.assertEqual(first.findings_inserted, 1)
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(second.raw_signal_status, "duplicate")
            self.assertEqual(second.findings_duplicate, 1)

            conn = sqlite3.connect(db)
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
                finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
                review_status = conn.execute("SELECT review_status FROM findings").fetchone()[0]
                taxonomy_version = conn.execute("SELECT taxonomy_version FROM findings").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(raw_count, 1)
            self.assertEqual(finding_count, 1)
            self.assertEqual(review_status, "accepted")
            self.assertEqual(taxonomy_version, gf.TAXONOMY_VERSION)

    def test_append_raw_signal_with_findings_rejects_mismatched_fk(self) -> None:
        record = store.raw_signal_from_intake_item(_item())
        finding = gf.finding_from_raw_signal(record, finding_text="Failure to investigate discrepancies.")
        finding["raw_signal_id"] = "rawsig-other"

        conn = sqlite3.connect(":memory:")
        try:
            store.ensure_findings_schema(conn)
            result = store.append_raw_signal_with_findings(conn, record, [finding])

            self.assertEqual(result.status, "partial")
            self.assertEqual(result.raw_signal_status, "inserted")
            self.assertEqual(result.findings_inserted, 0)
            self.assertEqual(result.findings_invalid, 1)
            self.assertIn("findings.raw_signal_id must match raw_signals.raw_signal_id", result.errors)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)
        finally:
            conn.close()

    def test_invalid_raw_payload_is_reported_without_insert(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            result = store.append_intake_item_to_sqlite(
                db,
                _item(raw_payload=["not", "an", "object"]),
            )

            self.assertEqual(result.status, "invalid")
            self.assertIn("raw_signals.raw_signal_id required", result.errors)
            conn = sqlite3.connect(db)
            try:
                count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 0)


class CollectIntakeFindingsAppendTest(unittest.TestCase):
    def test_insert_items_appends_raw_signal_after_notion_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            existing: set[str] = set()
            with mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(ci, "notion_create_page", return_value=True):
                got = ci.insert_items(
                    "tok",
                    "db",
                    [_item()],
                    datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                    datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                    existing,
                    False,
                    modality_enabled=False,
                    findings_sqlite_path=db,
                )

            self.assertEqual(got, (1, 0, 0))
            conn = sqlite3.connect(db)
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
                finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(raw_count, 1)
            self.assertEqual(finding_count, 0)

    def test_insert_items_appends_generated_findings_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            existing: set[str] = set()
            with mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(ci, "notion_create_page", return_value=True):
                got = ci.insert_items(
                    "tok",
                    "db",
                    [_item()],
                    datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                    datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                    existing,
                    False,
                    modality_enabled=False,
                    findings_sqlite_path=db,
                    findings_sqlite_include_findings=True,
                )

            self.assertEqual(got, (1, 0, 0))
            conn = sqlite3.connect(db)
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
                finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
                review_status = conn.execute("SELECT review_status FROM findings").fetchone()[0]
                evidence_level = conn.execute("SELECT evidence_level FROM findings").fetchone()[0]
                taxonomy_version = conn.execute("SELECT taxonomy_version FROM findings").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(raw_count, 1)
            self.assertEqual(finding_count, 1)
            self.assertEqual(review_status, "accepted")
            self.assertEqual(evidence_level, "A")
            self.assertEqual(taxonomy_version, gf.TAXONOMY_VERSION)

    def test_insert_items_dry_run_does_not_write_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            with mock.patch.object(ci, "notion_create_page") as create_page:
                got = ci.insert_items(
                    "tok",
                    "db",
                    [_item()],
                    datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                    datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                    set(),
                    True,
                    modality_enabled=False,
                    findings_sqlite_path=db,
                )

            self.assertEqual(got, (1, 0, 0))
            create_page.assert_not_called()
            self.assertFalse(os.path.exists(db))

    def test_sqlite_append_failure_does_not_change_notion_insert_stats(self) -> None:
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page", return_value=True), \
                mock.patch.object(ci, "append_intake_item_to_sqlite", side_effect=OSError("disk full")):
            got = ci.insert_items(
                "tok",
                "db",
                [_item()],
                datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                set(),
                False,
                modality_enabled=False,
                findings_sqlite_path="findings.sqlite3",
            )

        self.assertEqual(got, (1, 0, 0))

    def test_findings_append_failure_does_not_change_notion_insert_stats(self) -> None:
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page", return_value=True), \
                mock.patch.object(
                    ci, "append_intake_item_with_findings_to_sqlite", side_effect=OSError("disk full")
                ):
            got = ci.insert_items(
                "tok",
                "db",
                [_item()],
                datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                set(),
                False,
                modality_enabled=False,
                findings_sqlite_path="findings.sqlite3",
                findings_sqlite_include_findings=True,
            )

        self.assertEqual(got, (1, 0, 0))


class CollectIntakeFindingsSupabaseAppendTest(unittest.TestCase):
    """FIND-1 M4a insert_items <-> findings_supabase_append wiring boundary tests."""

    _SUPABASE = ("https://example.supabase.co", "service-role-secret-token")

    def test_flag_off_default_never_calls_supabase(self) -> None:
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page", return_value=True), \
                mock.patch.object(ci, "append_intake_item_to_supabase") as append_supabase:
            got = ci.insert_items(
                "tok",
                "db",
                [_item()],
                datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                set(),
                False,
                modality_enabled=False,
            )

        self.assertEqual(got, (1, 0, 0))
        append_supabase.assert_not_called()

    def test_dry_run_never_calls_supabase(self) -> None:
        with mock.patch.object(ci, "notion_create_page") as create_page, \
                mock.patch.object(ci, "append_intake_item_to_supabase") as append_supabase:
            got = ci.insert_items(
                "tok",
                "db",
                [_item()],
                datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                set(),
                True,
                modality_enabled=False,
                findings_supabase=self._SUPABASE,
            )

        self.assertEqual(got, (1, 0, 0))
        create_page.assert_not_called()
        append_supabase.assert_not_called()

    def test_supabase_append_failure_does_not_change_notion_insert_stats(self) -> None:
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page", return_value=True), \
                mock.patch.object(ci, "append_intake_item_to_supabase", side_effect=OSError("network down")):
            got = ci.insert_items(
                "tok",
                "db",
                [_item()],
                datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                set(),
                False,
                modality_enabled=False,
                findings_supabase=self._SUPABASE,
            )

        self.assertEqual(got, (1, 0, 0))

    def test_supabase_findings_append_failure_does_not_change_notion_insert_stats(self) -> None:
        with mock.patch.object(ci.time, "sleep"), \
                mock.patch.object(ci, "notion_create_page", return_value=True), \
                mock.patch.object(
                    ci, "append_intake_item_with_findings_to_supabase", side_effect=OSError("network down")
                ):
            got = ci.insert_items(
                "tok",
                "db",
                [_item()],
                datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                set(),
                False,
                modality_enabled=False,
                findings_supabase=self._SUPABASE,
                findings_supabase_include_findings=True,
            )

        self.assertEqual(got, (1, 0, 0))

    def test_sqlite_and_supabase_both_on_calls_both(self) -> None:
        import findings_store as store_result_module  # local alias to build a stub result

        stub_result = store_result_module.RawSignalAppendResult("inserted", raw_signal_id="rawsig-x")
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "findings.sqlite3")
            with mock.patch.object(ci.time, "sleep"), \
                    mock.patch.object(ci, "notion_create_page", return_value=True), \
                    mock.patch.object(
                        ci, "append_intake_item_to_supabase", return_value=stub_result
                    ) as append_supabase:
                got = ci.insert_items(
                    "tok",
                    "db",
                    [_item()],
                    datetime(2026, 7, 8, tzinfo=timezone.utc).date(),
                    datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
                    set(),
                    False,
                    modality_enabled=False,
                    findings_sqlite_path=db,
                    findings_supabase=self._SUPABASE,
                )

            self.assertEqual(got, (1, 0, 0))
            append_supabase.assert_called_once()
            conn = sqlite3.connect(db)
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(raw_count, 1)


if __name__ == "__main__":
    unittest.main()
