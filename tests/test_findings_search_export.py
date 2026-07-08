#!/usr/bin/env python3
"""FIND-1 M2b SQLite -> static search export tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import findings_search_export as search_export
import findings_store as store
import grm_findings as gf


def _pair(
    *,
    source: str,
    document_id: str,
    date: str,
    firm: str,
    category_code: str,
    evidence_level: str,
    review_status: str,
    finding_text: str,
    site_country: str = "US",
) -> tuple[dict, dict]:
    row = {
        "source": source,
        "document_id": document_id,
        "date": date,
        "headline": f"[{source}] {firm}",
        "firm": firm,
        "type_or_class": "483" if "FDA" in source else "gmp-inspection",
        "site_country": site_country,
        "modality": "Drug",
        "source_url": f"https://example.com/{document_id}",
        "official_url": f"https://example.com/official/{document_id}",
    }
    raw = {"firm": firm, "detail": "sample raw payload"}
    raw_signal = gf.raw_signal_from_row(row, raw, collected_at="2026-07-01T00:00:00+00:00")
    finding = gf.finding_from_raw_signal(
        raw_signal,
        finding_text=finding_text,
        category_code=category_code,
        evidence_level=evidence_level,
        review_status=review_status,
    )
    return raw_signal, finding


def _sample_pairs() -> list[tuple[dict, dict]]:
    return [
        _pair(
            source="FDA 483",
            document_id="fda-1",
            date="2026-07-05",
            firm="Acme Pharma",
            category_code="data_integrity",
            evidence_level="A",
            review_status="accepted",
            finding_text="Failure to review batch records.",
        ),
        _pair(
            source="MFDS",
            document_id="mfds-1",
            date="2026-06-20",
            firm="Korea BioPharma",
            category_code="cleaning_validation",
            evidence_level="A",
            review_status="accepted",
            finding_text="세척 밸리데이션 잔류 기준 미달.",
            site_country="KR",
        ),
        _pair(
            source="WHO",
            document_id="who-1",
            date="2026-05-15",
            firm="Global Vax",
            category_code="environmental_monitoring",
            evidence_level="C",
            review_status="rejected",
            finding_text="Environmental monitoring excursion noted.",
            site_country="ZA",
        ),
    ]


def _seed_db(db_path: str, pairs: list[tuple[dict, dict]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        store.ensure_findings_schema(conn)
        for raw_signal, finding in pairs:
            result = store.append_raw_signal_with_findings(conn, raw_signal, [finding])
            assert result.findings_invalid == 0, result.errors
        conn.commit()
    finally:
        conn.close()


class BuildSearchExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        _seed_db(self.db_path, _sample_pairs())

    def test_envelope_shape_and_schema_versions(self) -> None:
        result = search_export.build_search_export(self.db_path)

        self.assertEqual(result["schema_version"], search_export.SEARCH_EXPORT_SCHEMA_VERSION)
        self.assertEqual(result["raw_signal_schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(result["finding_schema_version"], gf.FINDING_SCHEMA_VERSION)
        self.assertEqual(result["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual(
            set(result.keys()),
            {
                "schema_version",
                "raw_signal_schema_version",
                "finding_schema_version",
                "taxonomy_version",
                "source_db",
                "records",
                "facets",
                "coverage",
                "report",
            },
        )

    def test_source_db_has_file_name_only_no_absolute_path(self) -> None:
        result = search_export.build_search_export(self.db_path)

        source_db = result["source_db"]
        self.assertEqual(source_db["file_name"], "grm-findings.sqlite3")
        self.assertEqual(source_db["raw_signals"], 3)
        self.assertEqual(source_db["findings"], 3)
        self.assertNotIn(os.sep, source_db["file_name"])
        self.assertNotIn(self._tmp.name, json.dumps(source_db))

    def test_records_have_raw_signal_join_without_blob_fields(self) -> None:
        result = search_export.build_search_export(self.db_path)

        self.assertEqual(len(result["records"]), 3)
        for record in result["records"]:
            self.assertIn("raw_signal", record)
            raw_signal = record["raw_signal"]
            self.assertIsNotNone(raw_signal)
            self.assertNotIn("raw_json", raw_signal)
            self.assertNotIn("row_json", raw_signal)
            self.assertIn("title", raw_signal)
            self.assertIn("source", raw_signal)

    def test_records_sorted_by_published_date_desc(self) -> None:
        result = search_export.build_search_export(self.db_path)
        dates = [r["published_date"] for r in result["records"]]
        self.assertEqual(dates, ["2026-07-05", "2026-06-20", "2026-05-15"])

    def test_report_is_blocking_free_and_ready(self) -> None:
        result = search_export.build_search_export(self.db_path)
        report = result["report"]

        self.assertEqual(report["mode"], "search_export")
        self.assertEqual(report["records"], 3)
        self.assertEqual(report["validation_errors"], [])
        self.assertEqual(report["blocking_errors"], 0)
        self.assertTrue(report["ready_for_viewer"])

    def test_coverage_has_expected_shape(self) -> None:
        result = search_export.build_search_export(self.db_path)
        coverage = result["coverage"]

        self.assertEqual(coverage["raw_signals_total"], 3)
        self.assertEqual(coverage["raw_signals_with_findings"], 3)
        self.assertEqual(coverage["raw_signals_without_findings"], 0)
        self.assertEqual(coverage["findings_total"], 3)
        self.assertEqual(coverage["findings_by_agency"], {"FDA": 1, "MFDS": 1, "WHO": 1})

    def test_export_is_deterministic_across_repeated_calls(self) -> None:
        first = search_export.build_search_export(self.db_path)
        second = search_export.build_search_export(self.db_path)
        self.assertEqual(first, second)

    def test_missing_db_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "missing.sqlite3")
            with self.assertRaises(ValueError):
                search_export.build_search_export(missing)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        _seed_db(self.db_path, _sample_pairs())

    def test_cli_writes_output_file(self) -> None:
        out = os.path.join(self._tmp.name, "findings_search_export.json")

        rc = search_export.main(["--db-path", self.db_path, "--output", out, "--pretty"])

        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            result = json.load(f)
        self.assertEqual(result["schema_version"], search_export.SEARCH_EXPORT_SCHEMA_VERSION)
        self.assertEqual(result["report"]["ready_for_viewer"], True)

    def test_cli_missing_db_exits_2(self) -> None:
        missing = os.path.join(self._tmp.name, "missing.sqlite3")

        rc = search_export.main(["--db-path", missing])

        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
