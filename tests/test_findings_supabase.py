#!/usr/bin/env python3
"""FIND-1 M3a offline Supabase(Postgres) load plan generator tests."""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import findings_store as store
import findings_supabase as fs
import grm_findings as gf


_MIGRATION_PATH = Path(__file__).resolve().parent.parent / "web" / "migrations" / "002_findings.sql"


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


def _bulk_pairs(count: int) -> list[tuple[dict, dict]]:
    categories = gf.FINDING_CATEGORY_CODES
    pairs: list[tuple[dict, dict]] = []
    for i in range(count):
        source = "FDA 483" if i % 2 == 0 else "MFDS"
        if i == 0:
            finding_text = "Firm 0 didn't perform the required review."
        elif i == 3:
            finding_text = "세척 밸리데이션 잔류 기준을 초과했다."
        else:
            finding_text = f"Deficiency detail number {i}."
        pairs.append(
            _pair(
                source=source,
                document_id=f"doc-{i}",
                date=f"2026-07-{(i % 27) + 1:02d}",
                firm=f"Firm {i}",
                category_code=categories[i % len(categories)],
                evidence_level="A",
                review_status="accepted",
                finding_text=finding_text,
                site_country="KR" if source == "MFDS" else "US",
                inspector_names=["Jane Doe", "John Q. Smith"] if i == 1 else None,
                cfr_refs=["21 CFR 211.100"] if i == 2 else None,
                mfds_refs=["약사법 제1조"] if i == 3 else None,
            )
        )
    return pairs


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


class ConstantsTest(unittest.TestCase):
    def test_module_constants(self) -> None:
        self.assertEqual(fs.SUPABASE_LOAD_SCHEMA_VERSION, "grm-findings-supabase-load/v1")
        self.assertEqual(fs.FINDINGS_PG_MIGRATION_NAME, "findings_v1_raw_signals_findings")


class PostgresSchemaDdlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ddl = fs.postgres_schema_ddl()

    def test_creates_both_tables_idempotently(self) -> None:
        self.assertEqual(self.ddl.count("create table if not exists public.raw_signals"), 1)
        self.assertEqual(self.ddl.count("create table if not exists public.findings"), 1)

    def test_all_taxonomy_codes_present_in_category_check(self) -> None:
        self.assertEqual(len(gf.FINDING_CATEGORY_CODES), 20)
        for code in gf.FINDING_CATEGORY_CODES:
            self.assertIn(f"'{code}'", self.ddl)

    def test_schema_version_and_taxonomy_checks_pinned(self) -> None:
        self.assertIn(f"schema_version = '{gf.RAW_SIGNAL_SCHEMA_VERSION}'", self.ddl)
        self.assertIn(f"schema_version = '{gf.FINDING_SCHEMA_VERSION}'", self.ddl)
        self.assertIn(f"taxonomy_version = '{gf.TAXONOMY_VERSION}'", self.ddl)

    def test_evidence_extraction_review_checks_present(self) -> None:
        self.assertIn("evidence_level in ('A', 'B', 'C')", self.ddl)
        self.assertIn("extraction_method in ('deterministic', 'llm_assisted', 'manual')", self.ddl)
        self.assertIn("review_status in ('accepted', 'needs_review', 'rejected')", self.ddl)
        self.assertIn("confidence >= 0 and confidence <= 1", self.ddl)

    def test_raw_sha256_length_check_present(self) -> None:
        self.assertIn("check (char_length(raw_sha256) = 64)", self.ddl)

    def test_raw_json_and_row_json_are_text_not_jsonb(self) -> None:
        self.assertIn("raw_json text not null", self.ddl)
        self.assertIn("row_json text not null", self.ddl)
        self.assertNotIn("raw_json jsonb", self.ddl)
        self.assertNotIn("row_json jsonb", self.ddl)

    def test_list_fields_are_jsonb_with_default_empty_array(self) -> None:
        for column in ("inspector_names", "cfr_refs", "mfds_refs"):
            self.assertIn(f"{column} jsonb not null default '[]'::jsonb", self.ddl)

    def test_foreign_key_cascade_delete(self) -> None:
        self.assertIn("references public.raw_signals (raw_signal_id) on delete cascade", self.ddl)

    def test_unique_source_document_id(self) -> None:
        self.assertIn("unique (source, document_id)", self.ddl)

    def test_md5_unique_index_replaces_long_text_unique_constraint(self) -> None:
        self.assertIn(
            "create unique index if not exists findings_rawsig_text_md5_uq",
            self.ddl,
        )
        self.assertIn("md5(finding_text)", self.ddl)
        # The literal SQLite-style table UNIQUE(raw_signal_id, finding_text) constraint must not appear.
        self.assertNotIn("unique (raw_signal_id, finding_text)", self.ddl)

    def test_facet_and_firm_lookup_indexes_present(self) -> None:
        self.assertIn(
            "create index if not exists idx_findings_facets\n  on public.findings (agency, category_code, modality, published_date);",
            self.ddl,
        )
        self.assertIn(
            "create index if not exists idx_findings_firm\n  on public.findings (firm_name, published_date);",
            self.ddl,
        )

    def test_rls_enabled_with_zero_policies_and_grants_revoked(self) -> None:
        self.assertEqual(self.ddl.count("enable row level security"), 2)
        self.assertNotIn("create policy", self.ddl)
        self.assertIn(
            "revoke all on public.raw_signals, public.findings from anon, authenticated;",
            self.ddl,
        )

    def test_ingested_at_infra_columns_present(self) -> None:
        self.assertEqual(self.ddl.count("ingested_at timestamptz not null default now()"), 2)

    def test_ddl_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.ddl.count("--"), 8)


class MigrationFileMatchesDdlTest(unittest.TestCase):
    def test_migration_file_exists(self) -> None:
        self.assertTrue(_MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}")

    def test_migration_file_byte_matches_function_output(self) -> None:
        on_disk = _MIGRATION_PATH.read_bytes()
        expected = fs.postgres_schema_ddl().encode("utf-8")
        self.assertEqual(on_disk, expected)

    def test_migration_file_has_no_crlf(self) -> None:
        on_disk = _MIGRATION_PATH.read_bytes()
        self.assertNotIn(b"\r\n", on_disk)


class PgQuoteTextTest(unittest.TestCase):
    def test_single_quote_escaped(self) -> None:
        self.assertEqual(fs.pg_quote_text("O'Brien"), "'O''Brien'")

    def test_korean_text_preserved(self) -> None:
        self.assertEqual(fs.pg_quote_text("한글 텍스트"), "'한글 텍스트'")

    def test_newline_preserved(self) -> None:
        self.assertEqual(fs.pg_quote_text("line1\nline2"), "'line1\nline2'")

    def test_nul_byte_removed(self) -> None:
        self.assertEqual(fs.pg_quote_text("a\x00b"), "'ab'")

    def test_empty_string(self) -> None:
        self.assertEqual(fs.pg_quote_text(""), "''")

    def test_backslash_kept_as_is(self) -> None:
        self.assertEqual(fs.pg_quote_text("a\\b"), "'a\\b'")


class PgQuoteJsonbTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual(fs.pg_quote_jsonb([]), "'[]'::jsonb")

    def test_none_treated_as_empty_list(self) -> None:
        self.assertEqual(fs.pg_quote_jsonb(None), "'[]'::jsonb")

    def test_list_round_trips_through_json_dumps(self) -> None:
        items = ["21 CFR 211.100", "Jane Doe"]
        expected_payload = json.dumps(items, ensure_ascii=False, sort_keys=True)
        self.assertEqual(fs.pg_quote_jsonb(items), fs.pg_quote_text(expected_payload) + "::jsonb")

    def test_ends_with_jsonb_cast(self) -> None:
        self.assertTrue(fs.pg_quote_jsonb(["x"]).endswith("::jsonb"))


class BuildSupabaseLoadPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        self.pair_count = 12
        _seed_db(self.db_path, _bulk_pairs(self.pair_count))
        self.plan = fs.build_supabase_load_plan(self.db_path)

    def test_envelope_shape(self) -> None:
        self.assertEqual(
            set(self.plan.keys()),
            {
                "schema_version",
                "raw_signal_schema_version",
                "finding_schema_version",
                "taxonomy_version",
                "migration_name",
                "ddl_sql",
                "data_sql",
                "verification_sql",
                "counts",
                "report",
            },
        )
        self.assertEqual(self.plan["schema_version"], fs.SUPABASE_LOAD_SCHEMA_VERSION)
        self.assertEqual(self.plan["raw_signal_schema_version"], gf.RAW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(self.plan["finding_schema_version"], gf.FINDING_SCHEMA_VERSION)
        self.assertEqual(self.plan["taxonomy_version"], gf.TAXONOMY_VERSION)
        self.assertEqual(self.plan["migration_name"], fs.FINDINGS_PG_MIGRATION_NAME)
        self.assertEqual(self.plan["ddl_sql"], fs.postgres_schema_ddl())

    def test_counts_match_data_sql_row_totals_and_batch_size(self) -> None:
        counts = self.plan["counts"]
        self.assertEqual(counts["raw_signals"], self.pair_count)
        self.assertEqual(counts["findings"], self.pair_count)
        self.assertEqual(counts["raw_signal_batches"], 2)
        self.assertEqual(counts["finding_batches"], 2)

        raw_stmts = [s for s in self.plan["data_sql"] if s.startswith("insert into public.raw_signals")]
        finding_stmts = [s for s in self.plan["data_sql"] if s.startswith("insert into public.findings")]
        self.assertEqual(len(raw_stmts), counts["raw_signal_batches"])
        self.assertEqual(len(finding_stmts), counts["finding_batches"])

        raw_row_counts = [stmt.count("'grm-raw-signal/v1'") for stmt in raw_stmts]
        finding_row_counts = [stmt.count("'grm-finding/v1'") for stmt in finding_stmts]
        self.assertEqual(sum(raw_row_counts), counts["raw_signals"])
        self.assertEqual(sum(finding_row_counts), counts["findings"])
        self.assertTrue(all(1 <= n <= 10 for n in raw_row_counts))
        self.assertTrue(all(1 <= n <= 10 for n in finding_row_counts))

    def test_on_conflict_do_nothing_present(self) -> None:
        for stmt in self.plan["data_sql"]:
            if stmt.startswith("insert into public.raw_signals"):
                self.assertTrue(stmt.endswith("on conflict (raw_signal_id) do nothing;"))
            elif stmt.startswith("insert into public.findings"):
                self.assertTrue(stmt.endswith("on conflict (finding_id) do nothing;"))
            else:
                self.fail(f"unexpected data_sql statement: {stmt[:60]!r}")

    def test_plan_is_deterministic_across_repeated_calls(self) -> None:
        second = fs.build_supabase_load_plan(self.db_path)
        self.assertEqual(self.plan, second)

    def test_finding_text_apostrophe_is_escaped(self) -> None:
        joined = "\n".join(self.plan["data_sql"])
        self.assertIn("Firm 0 didn''t perform the required review.", joined)
        self.assertNotIn("Firm 0 didn't perform the required review.", joined)

    def test_jsonb_cast_present_in_findings_inserts(self) -> None:
        finding_stmts = [s for s in self.plan["data_sql"] if s.startswith("insert into public.findings")]
        joined = "\n".join(finding_stmts)
        self.assertIn("::jsonb", joined)

    def test_verification_sql_covers_counts_versions_integrity_and_orphans(self) -> None:
        verification = " ".join(self.plan["verification_sql"])
        self.assertIn("count(*)", verification)
        self.assertIn("raw_sha256", verification)
        self.assertIn("sha256(convert_to(raw_json", verification)
        self.assertIn("orphan_findings_count", verification)
        self.assertIn("distinct schema_version", verification)
        self.assertIn("distinct taxonomy_version", verification)

    def test_report_has_zero_blocking_errors_and_is_ready(self) -> None:
        report = self.plan["report"]
        self.assertEqual(report["mode"], "supabase_load_plan")
        self.assertEqual(report["validation_errors"], [])
        self.assertEqual(report["blocking_errors"], 0)
        self.assertTrue(report["ready_for_apply"])


class EmptyDbPlanTest(unittest.TestCase):
    def test_empty_db_produces_zero_counts_and_no_data_sql(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "grm-findings.sqlite3")
            conn = sqlite3.connect(db_path)
            try:
                store.ensure_findings_schema(conn)
                conn.commit()
            finally:
                conn.close()

            plan = fs.build_supabase_load_plan(db_path)
            self.assertEqual(plan["counts"], {
                "raw_signals": 0,
                "findings": 0,
                "raw_signal_batches": 0,
                "finding_batches": 0,
            })
            self.assertEqual(plan["data_sql"], [])
            self.assertTrue(plan["report"]["ready_for_apply"])


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "grm-findings.sqlite3")
        _seed_db(self.db_path, _bulk_pairs(3))

    def test_cli_writes_output_file(self) -> None:
        out = os.path.join(self._tmp.name, "findings_supabase_load.json")

        rc = fs.main(["--db-path", self.db_path, "--output", out, "--pretty"])

        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as handle:
            result = json.load(handle)
        self.assertEqual(result["schema_version"], fs.SUPABASE_LOAD_SCHEMA_VERSION)
        self.assertTrue(result["report"]["ready_for_apply"])

    def test_cli_missing_db_exits_2(self) -> None:
        missing = os.path.join(self._tmp.name, "missing.sqlite3")

        rc = fs.main(["--db-path", missing])

        self.assertEqual(rc, 2)


class NoNetworkOrRealSqliteAccessTest(unittest.TestCase):
    def test_module_source_has_no_network_or_db_driver_imports(self) -> None:
        source = inspect.getsource(fs)
        forbidden = (
            "import requests",
            "import urllib.request",
            "import socket",
            "import http.client",
            "import psycopg",
            "import supabase",
            "create_client(",
        )
        for token in forbidden:
            self.assertNotIn(token, source, f"unexpected token found: {token}")

    def test_build_plan_has_no_default_db_path(self) -> None:
        signature = inspect.signature(fs.build_supabase_load_plan)
        self.assertEqual(signature.parameters["db_path"].default, inspect.Parameter.empty)

    def test_missing_db_path_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "missing.sqlite3")
            with self.assertRaises(ValueError):
                fs.build_supabase_load_plan(missing)


if __name__ == "__main__":
    unittest.main()
