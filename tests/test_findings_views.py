#!/usr/bin/env python3
"""FIND-1 M2a read-only SQLite view tests."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import findings_store as store
import findings_views as views
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
    inspector_names: list[str] | None = None,
    cfr_refs: list[str] | None = None,
    mfds_refs: list[str] | None = None,
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
        inspector_names=inspector_names,
        cfr_refs=cfr_refs,
        mfds_refs=mfds_refs,
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
            finding_text="Failure to review batch records for 100% compliance.",
        ),
        _pair(
            source="FDA 483",
            document_id="fda-2",
            date="2026-07-05",
            firm="Acme Pharma",
            category_code="documentation_records",
            evidence_level="B",
            review_status="needs_review",
            finding_text="Missing signature_field entries in batch record.",
            inspector_names=["Jane Doe"],
            cfr_refs=["21 CFR 211.100"],
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
            mfds_refs=["의약품 제조·품질관리기준 제3조"],
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


class OpenFindingsDbReadonlyTest(unittest.TestCase):
    def test_missing_file_raises_and_does_not_create_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "does-not-exist.sqlite3")

            with self.assertRaises(ValueError):
                views.open_findings_db_readonly(db_path)

            self.assertFalse(os.path.exists(db_path))

    def test_open_succeeds_under_directory_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spaced_dir = os.path.join(td, "Global Regulatory Sweep", "sidecar dir")
            os.makedirs(spaced_dir, exist_ok=True)
            db_path = os.path.join(spaced_dir, "grm-findings.sqlite3")
            _seed_db(db_path, _sample_pairs())

            conn = views.open_findings_db_readonly(db_path)
            try:
                summary = views.db_summary(conn)
            finally:
                conn.close()

            self.assertEqual(summary["raw_signals"], 4)
            self.assertEqual(summary["findings"], 4)

    def test_readonly_connection_rejects_insert(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "findings.sqlite3")
            _seed_db(db_path, _sample_pairs())

            conn = views.open_findings_db_readonly(db_path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute(
                        "INSERT INTO raw_signals (schema_version) VALUES (?)",
                        (gf.RAW_SIGNAL_SCHEMA_VERSION,),
                    )
            finally:
                conn.close()

    def test_missing_required_tables_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "empty.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()

            with self.assertRaises(ValueError):
                views.open_findings_db_readonly(db_path)


class QueryFindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "findings.sqlite3")
        self.pairs = _sample_pairs()
        _seed_db(self.db_path, self.pairs)
        self.conn = views.open_findings_db_readonly(self.db_path)
        self.addCleanup(self.conn.close)

    def test_no_filter_returns_all_sorted_by_date_desc(self) -> None:
        records = views.query_findings(self.conn)

        self.assertEqual(len(records), 4)
        dates = [r["published_date"] for r in records]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_filter_by_agency(self) -> None:
        records = views.query_findings(self.conn, agency=("MFDS",))
        self.assertEqual([r["document_id"] for r in records], ["mfds-1"])

    def test_filter_by_category_code_and_evidence_level_combination(self) -> None:
        records = views.query_findings(
            self.conn,
            category_code=("data_integrity", "documentation_records"),
            evidence_level=("B",),
        )
        self.assertEqual([r["document_id"] for r in records], ["fda-2"])

    def test_filter_by_source_and_review_status(self) -> None:
        records = views.query_findings(self.conn, source=("WHO",), review_status=("rejected",))
        self.assertEqual([r["document_id"] for r in records], ["who-1"])

    def test_filter_by_date_range(self) -> None:
        records = views.query_findings(self.conn, date_from="2026-06-01", date_to="2026-07-01")
        self.assertEqual([r["document_id"] for r in records], ["mfds-1"])

    def test_firm_contains_case_insensitive(self) -> None:
        records = views.query_findings(self.conn, firm_contains="acme")
        self.assertEqual({r["document_id"] for r in records}, {"fda-1", "fda-2"})

    def test_text_contains_percent_literal_is_escaped(self) -> None:
        records = views.query_findings(self.conn, text_contains="100% compliance")
        self.assertEqual([r["document_id"] for r in records], ["fda-1"])

        no_match = views.query_findings(self.conn, text_contains="100X compliance")
        self.assertEqual(no_match, [])

    def test_text_contains_underscore_literal_is_escaped(self) -> None:
        records = views.query_findings(self.conn, text_contains="signature_field")
        self.assertEqual([r["document_id"] for r in records], ["fda-2"])

        no_match = views.query_findings(self.conn, text_contains="signatureXfield")
        self.assertEqual(no_match, [])

    def test_limit(self) -> None:
        records = views.query_findings(self.conn, limit=1)
        self.assertEqual(len(records), 1)

    def test_list_fields_round_trip_from_json_text(self) -> None:
        records = views.query_findings(self.conn)
        by_doc = {r["document_id"]: r for r in records}
        self.assertEqual(by_doc["fda-2"]["inspector_names"], ["Jane Doe"])
        self.assertEqual(by_doc["fda-2"]["cfr_refs"], ["21 CFR 211.100"])
        self.assertEqual(by_doc["mfds-1"]["mfds_refs"], ["의약품 제조·품질관리기준 제3조"])
        self.assertEqual(by_doc["who-1"]["inspector_names"], [])

    def test_sort_determinism_same_published_date_tiebreak_by_finding_id(self) -> None:
        records = views.query_findings(self.conn, source=("FDA 483",))
        self.assertEqual(len(records), 2)
        finding_ids = [r["finding_id"] for r in records]
        self.assertEqual(finding_ids, sorted(finding_ids))

    def test_no_string_formatting_sql_injection_style_input_is_literal(self) -> None:
        records = views.query_findings(self.conn, firm_contains="Acme'; DROP TABLE findings; --")
        self.assertEqual(records, [])
        # table must still exist and contain rows
        still_there = views.query_findings(self.conn)
        self.assertEqual(len(still_there), 4)


class FacetCountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "findings.sqlite3")
        _seed_db(self.db_path, _sample_pairs())
        self.conn = views.open_findings_db_readonly(self.db_path)
        self.addCleanup(self.conn.close)

    def test_facet_counts_no_filter(self) -> None:
        facets = views.facet_counts(self.conn)

        self.assertEqual(facets["agency"], {"FDA": 2, "MFDS": 1, "WHO": 1})
        self.assertEqual(facets["evidence_level"], {"A": 2, "B": 1, "C": 1})
        self.assertEqual(facets["review_status"], {"accepted": 2, "needs_review": 1, "rejected": 1})
        self.assertEqual(facets["published_month"], {"2026-05": 1, "2026-06": 1, "2026-07": 2})
        self.assertEqual(list(facets.keys()), sorted(facets.keys()))

    def test_facet_counts_reflect_filters(self) -> None:
        facets = views.facet_counts(self.conn, agency=("FDA",))

        self.assertEqual(facets["agency"], {"FDA": 2})
        self.assertEqual(sum(facets["evidence_level"].values()), 2)
        self.assertEqual(facets["published_month"], {"2026-07": 2})


class RawSignalSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "findings.sqlite3")
        self.pairs = _sample_pairs()
        _seed_db(self.db_path, self.pairs)
        self.conn = views.open_findings_db_readonly(self.db_path)
        self.addCleanup(self.conn.close)

    def test_returns_expected_fields_without_blob(self) -> None:
        raw_signal_id = self.pairs[0][0]["raw_signal_id"]

        summary = views.raw_signal_summary(self.conn, raw_signal_id)

        self.assertIsNotNone(summary)
        self.assertNotIn("raw_json", summary)
        self.assertNotIn("row_json", summary)
        self.assertEqual(
            set(summary.keys()),
            {
                "title",
                "source",
                "source_kind",
                "published_date",
                "collected_at",
                "source_url",
                "official_url",
                "firm_name",
                "site_country",
                "extraction_status",
            },
        )
        self.assertEqual(summary["source"], "FDA 483")
        self.assertEqual(summary["firm_name"], "Acme Pharma")

    def test_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(views.raw_signal_summary(self.conn, "rawsig-does-not-exist"))


class DbSummaryTest(unittest.TestCase):
    def test_counts_and_distinct_versions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "findings.sqlite3")
            _seed_db(db_path, _sample_pairs())
            conn = views.open_findings_db_readonly(db_path)
            try:
                summary = views.db_summary(conn)
            finally:
                conn.close()

        self.assertEqual(summary["raw_signals"], 4)
        self.assertEqual(summary["findings"], 4)
        self.assertEqual(summary["finding_schema_versions"], [gf.FINDING_SCHEMA_VERSION])
        self.assertEqual(summary["finding_taxonomy_versions"], [gf.TAXONOMY_VERSION])


if __name__ == "__main__":
    unittest.main()
