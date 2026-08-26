#!/usr/bin/env python3
"""FIND-1 M3a offline Supabase(Postgres) load plan generator tests."""

from __future__ import annotations

import inspect
import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import findings_store as store
import findings_supabase as fs
import grm_findings as gf


_MIGRATION_PATH = Path(__file__).resolve().parent.parent / "web" / "migrations" / "002_findings.sql"
_TAXONOMY_V2_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "004_findings_taxonomy_v2.sql"
)
_TRANSLATION_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "005_findings_translation_columns.sql"
)
_TAXONOMY_V3_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "011_findings_taxonomy_v3.sql"
)
_TAXONOMY_V4_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "012_findings_taxonomy_v4.sql"
)
_TAXONOMY_V5_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "044_findings_taxonomy_v5.sql"
)
_TAXONOMY_V9_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "050_findings_taxonomy_v9.sql"
)
_TAXONOMY_V10_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "064_findings_taxonomy_v10.sql"
)
_TAXONOMY_V8_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "049_findings_taxonomy_v8.sql"
)
_TAXONOMY_V7_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "047_findings_taxonomy_v7.sql"
)
_TAXONOMY_V6_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "045_findings_taxonomy_v6.sql"
)


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
        taxonomy_check = ", ".join(f"'{version}'" for version in gf.TAXONOMY_VERSIONS)
        self.assertIn(f"taxonomy_version in ({taxonomy_check})", self.ddl)

    def test_taxonomy_check_lists_all_versions(self) -> None:
        self.assertEqual(len(gf.TAXONOMY_VERSIONS), 10)
        for version in gf.TAXONOMY_VERSIONS:
            self.assertIn(f"'{version}'", self.ddl)
        # 002 is the fresh-install baseline (IN-list, both versions accepted from day one) --
        # it is not an equality-pinned CHECK anymore.
        self.assertNotIn(f"taxonomy_version = '{gf.TAXONOMY_VERSION}'", self.ddl)

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

    def test_translation_columns_present_with_defaults_and_check(self) -> None:
        self.assertIn("finding_text_ko text not null default ''", self.ddl)
        self.assertIn(
            "translation_method text not null default '' check (translation_method in "
            "('', 'llm_assisted', 'manual'))",
            self.ddl,
        )


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


class TaxonomyV2AlterMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V2_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V2_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V2_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V2_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1v2_in_list_check(self) -> None:
        # 004 is a frozen historical migration (v1/v2 only, superseded by 011's v1/v2/v3
        # IN-list for an already-live DB) -- it must NOT grow to reference v3 just because
        # gf.TAXONOMY_VERSIONS grew, so this asserts the v1/v2 literals directly.
        self.assertIn("findings_taxonomy_version_v1v2_check", self.sql)
        for version in ("grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2"):
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn(
            "check (taxonomy_version in ('grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2'))",
            self.sql,
        )

    def test_does_not_reclassify_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        """Regression: a plpgsql record var reused as a query table alias makes
        Postgres resolve `alias.column` as the not-yet-assigned plpgsql record
        (ERROR 55000: record "..." is not assigned yet) instead of the SQL
        alias. Live-tested failure on 2026-07-09 with `con`/`con` collision.
        """
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match, "expected a `declare <name> record;` line")
        record_var = declare_match.group(1)

        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match, "expected a `for <name> in` loop")
        self.assertEqual(loop_match.group(1), record_var, "loop variable must match declared record")

        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match, "expected `from pg_constraint <alias>`")
        constraint_alias = alias_match.group(1)

        self.assertNotEqual(
            record_var, constraint_alias,
            "plpgsql record variable must not share a name with a table alias "
            "used inside its own FOR-loop query (ambiguous `alias.column` resolution)",
        )


class TaxonomyV3AlterMigrationTest(unittest.TestCase):
    """011_findings_taxonomy_v3.sql -- same shape as 004, extended to the v1/v2/v3
    IN-list. Re-confirms both known pitfalls discovered in prior migrations: 004's
    loop-variable/table-alias name collision, and 009's array-slice parenthesization
    (not applicable here -- this migration has no array slicing at all)."""

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V3_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V3_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V3_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V3_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1v2v3_in_list_check(self) -> None:
        # 011 is a frozen historical migration (v1/v2/v3 only, superseded by 012's
        # v1/v2/v3/v4 IN-list for an already-live DB) -- it must NOT grow to reference
        # v4 just because gf.TAXONOMY_VERSIONS grew, so this asserts the v1/v2/v3
        # literals directly (same fix already applied to 004's analogous test).
        self.assertIn("findings_taxonomy_version_v1v2v3_check", self.sql)
        for version in ("grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2", "grm-finding-taxonomy/v3"):
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn(
            "check (taxonomy_version in (\n      'grm-finding-taxonomy/v1', "
            "'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3'\n    ))",
            self.sql,
        )

    def test_does_not_reclassify_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_no_array_slice_syntax_present(self) -> None:
        """009's pitfall (array slice needs the sliced expression parenthesized,
        e.g. `(coalesce(...))[1:500]`) does not apply here because this migration
        performs no array slicing at all -- assert that stays true so a future
        edit of this file doesn't quietly introduce the pattern unreviewed."""
        self.assertNotIn("[1:", self.sql)
        self.assertNotRegex(self.sql, r"\]\s*\[\s*\d*\s*:\s*\d*\s*\]")

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        """Same regression class as 004's test above: a plpgsql record var
        reused as a query table alias makes Postgres resolve `alias.column` as
        the not-yet-assigned plpgsql record instead of the SQL alias."""
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match, "expected a `declare <name> record;` line")
        record_var = declare_match.group(1)

        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match, "expected a `for <name> in` loop")
        self.assertEqual(loop_match.group(1), record_var, "loop variable must match declared record")

        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match, "expected `from pg_constraint <alias>`")
        constraint_alias = alias_match.group(1)

        self.assertNotEqual(
            record_var, constraint_alias,
            "plpgsql record variable must not share a name with a table alias "
            "used inside its own FOR-loop query (ambiguous `alias.column` resolution)",
        )

    def test_supersedes_004_by_expanding_not_narrowing(self) -> None:
        """011 must be a strict superset of 004's accepted versions -- it should
        never be possible for a migration ordering to leave v2 rows unacceptable."""
        v2_sql = _TAXONOMY_V2_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("grm-finding-taxonomy/v1", v2_sql)
        self.assertIn("grm-finding-taxonomy/v2", v2_sql)
        self.assertIn("grm-finding-taxonomy/v1", self.sql)
        self.assertIn("grm-finding-taxonomy/v2", self.sql)
        self.assertIn("grm-finding-taxonomy/v3", self.sql)


class TaxonomyV4AlterMigrationTest(unittest.TestCase):
    """012_findings_taxonomy_v4.sql -- same shape as 004/011, extended to the
    v1/v2/v3/v4 IN-list. Re-confirms both known pitfalls discovered in prior
    migrations: 004's loop-variable/table-alias name collision, and 009's
    array-slice parenthesization (not applicable here -- this migration has no
    array slicing at all)."""

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V4_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V4_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V4_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V4_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1v2v3v4_in_list_check(self) -> None:
        self.assertIn("findings_taxonomy_version_v1v2v3v4_check", self.sql)
        # 적용 완료된 마이그레이션 파일은 **불변**이다 -- 그래서 이 목록은 gf.TAXONOMY_VERSIONS
        # (현재값, v5 이후 계속 늘어난다)가 아니라 012 가 실제로 쓴 v1~v4 로 고정한다.
        # 최신 버전 수용은 그때그때의 최신 ALTER 마이그레이션(044 = v5)이 책임진다.
        for version in (
            "grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2",
            "grm-finding-taxonomy/v3", "grm-finding-taxonomy/v4",
        ):
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn(
            "check (taxonomy_version in (\n      'grm-finding-taxonomy/v1', "
            "'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3',\n      "
            "'grm-finding-taxonomy/v4'\n    ))",
            self.sql,
        )

    def test_does_not_reclassify_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_no_array_slice_syntax_present(self) -> None:
        """009's pitfall (array slice needs the sliced expression parenthesized,
        e.g. `(coalesce(...))[1:500]`) does not apply here because this migration
        performs no array slicing at all -- assert that stays true so a future
        edit of this file doesn't quietly introduce the pattern unreviewed."""
        self.assertNotIn("[1:", self.sql)
        self.assertNotRegex(self.sql, r"\]\s*\[\s*\d*\s*:\s*\d*\s*\]")

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        """Same regression class as 004/011's test above: a plpgsql record var
        reused as a query table alias makes Postgres resolve `alias.column` as
        the not-yet-assigned plpgsql record instead of the SQL alias."""
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match, "expected a `declare <name> record;` line")
        record_var = declare_match.group(1)

        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match, "expected a `for <name> in` loop")
        self.assertEqual(loop_match.group(1), record_var, "loop variable must match declared record")

        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match, "expected `from pg_constraint <alias>`")
        constraint_alias = alias_match.group(1)

        self.assertNotEqual(
            record_var, constraint_alias,
            "plpgsql record variable must not share a name with a table alias "
            "used inside its own FOR-loop query (ambiguous `alias.column` resolution)",
        )

    def test_supersedes_011_by_expanding_not_narrowing(self) -> None:
        """012 must be a strict superset of 011's accepted versions -- it should
        never be possible for a migration ordering to leave v3 rows unacceptable."""
        v3_sql = _TAXONOMY_V3_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("grm-finding-taxonomy/v1", v3_sql)
        self.assertIn("grm-finding-taxonomy/v2", v3_sql)
        self.assertIn("grm-finding-taxonomy/v3", v3_sql)
        self.assertIn("grm-finding-taxonomy/v1", self.sql)
        self.assertIn("grm-finding-taxonomy/v2", self.sql)
        self.assertIn("grm-finding-taxonomy/v3", self.sql)
        self.assertIn("grm-finding-taxonomy/v4", self.sql)


class TaxonomyV5AlterMigrationTest(unittest.TestCase):
    """044 -- v5(역극성 수정) ALTER 마이그레이션. 012 테스트와 같은 규율을 적용한다."""

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V5_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V5_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V5_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V5_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1_through_v5_in_list_check(self) -> None:
        self.assertIn("findings_taxonomy_version_v1v2v3v4v5_check", self.sql)
        # 코드가 v6(2026-08-02 split-word 수정)로 올라가면서 이 트립와이어가 의도대로
        # 발동해 045 를 요구했다. 044 는 더 이상 **현행** 마이그레이션이 아니므로 여기서는
        # v1~v5 만 고정하고, gf.TAXONOMY_VERSIONS 대조는 TaxonomyV6AlterMigrationTest 로
        # 옮겼다(같은 트립와이어가 v7 때 다시 발동한다).
        for version in (
            "grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2",
            "grm-finding-taxonomy/v3", "grm-finding-taxonomy/v4",
            "grm-finding-taxonomy/v5",
        ):
            self.assertIn(f"'{version}'", self.sql)

    def test_does_not_reclassify_existing_rows(self) -> None:
        """재분류는 findings_reclassify_service.py 의 일이다 -- 마이그레이션은 제약만 넓힌다."""
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        """004/011/012 와 동일한 회귀 클래스(plpgsql record 변수 = 테이블 별칭 충돌)."""
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match, "expected a `declare <name> record;` line")
        record_var = declare_match.group(1)

        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match, "expected a `for <name> in` loop")
        self.assertEqual(loop_match.group(1), record_var)

        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match, "expected `from pg_constraint <alias>`")
        self.assertNotEqual(record_var, alias_match.group(1))

    def test_supersedes_012_by_expanding_not_narrowing(self) -> None:
        """044 는 012 가 받던 버전을 하나도 잃지 않는 진부분집합 확장이어야 한다."""
        v4_sql = _TAXONOMY_V4_MIGRATION_PATH.read_text(encoding="utf-8")
        for version in (
            "grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2",
            "grm-finding-taxonomy/v3", "grm-finding-taxonomy/v4",
        ):
            self.assertIn(f"'{version}'", v4_sql)
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn("'grm-finding-taxonomy/v5'", self.sql)


class TaxonomyV6AlterMigrationTest(unittest.TestCase):
    """045 -- v6(단어 중간 공백 복원) ALTER 마이그레이션. 012/044 와 같은 규율."""

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V6_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V6_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V6_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V6_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1_through_v6_in_list_check(self) -> None:
        self.assertIn("findings_taxonomy_version_v1v2v3v4v5v6_check", self.sql)
        # 이 테스트는 예고대로 v7 도입 때 깨졌고(트립와이어 작동), 그 요구대로 047 이
        # 신설됐다. 적용 완료된 마이그레이션 파일은 **불변**이므로 045 는 이제 자기가
        # 실제로 쓴 v1~v6 으로 동결한다 -- 현행 버전 추종은 아래
        # TaxonomyV7AlterMigrationTest 가 이어받는다(012/044 와 같은 규율).
        for version in (
            "grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2", "grm-finding-taxonomy/v3",
            "grm-finding-taxonomy/v4", "grm-finding-taxonomy/v5", "grm-finding-taxonomy/v6",
        ):
            self.assertIn(f"'{version}'", self.sql)

    def test_does_not_reclassify_existing_rows(self) -> None:
        """재분류는 findings_reclassify_service.py 의 일이다 -- 마이그레이션은 제약만 넓힌다."""
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_does_not_touch_finding_text(self) -> None:
        """★v6 의 핵심 계약: 복원은 분류기 haystack 한정이고 저장 텍스트는 불변이다.
        이 마이그레이션이 finding_text 를 쓰지 않는다는 것을 구조로 고정한다."""
        lowered = self.sql.lower()
        self.assertNotIn("set finding_text", lowered)
        self.assertNotIn("alter column finding_text", lowered)

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        """004/011/012/044 와 동일한 회귀 클래스(plpgsql record 변수 = 테이블 별칭 충돌)."""
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match, "expected a `declare <name> record;` line")
        record_var = declare_match.group(1)

        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match, "expected a `for <name> in` loop")
        self.assertEqual(loop_match.group(1), record_var)

        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match, "expected `from pg_constraint <alias>`")
        self.assertNotEqual(record_var, alias_match.group(1))

    def test_supersedes_044_by_expanding_not_narrowing(self) -> None:
        """045 는 044 가 받던 버전을 하나도 잃지 않는 진부분집합 확장이어야 한다."""
        v5_sql = _TAXONOMY_V5_MIGRATION_PATH.read_text(encoding="utf-8")
        for version in (
            "grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2",
            "grm-finding-taxonomy/v3", "grm-finding-taxonomy/v4",
            "grm-finding-taxonomy/v5",
        ):
            self.assertIn(f"'{version}'", v5_sql)
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn("'grm-finding-taxonomy/v6'", self.sql)


class TaxonomyV7AlterMigrationTest(unittest.TestCase):
    """047 -- v7(접착 손상, v6 의 거울상) ALTER 마이그레이션. 012/044/045 와 동일 규율.

    번호가 046 이 아닌 이유: 046 은 다른 트랙(extraction_gap_monitor, PR #602)이 먼저
    가져갔다. 마이그레이션 번호는 선착순이며 연속성 테스트
    (test_findings_search_rpc.MigrationNumberSequenceTest)가 충돌을 잡는다.
    """

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V7_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V7_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V7_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V7_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1_through_v7_in_list_check(self) -> None:
        self.assertIn("findings_taxonomy_version_v1v2v3v4v5v6v7_check", self.sql)
        # 이 테스트는 예고대로 v8 도입 때 깨졌고(트립와이어 작동), 그 요구대로 049 가
        # 신설됐다. 적용 완료된 마이그레이션 파일은 **불변**이므로 047 은 자기가 실제로
        # 쓴 v1~v7 로 동결한다 -- 현행 추종은 TaxonomyV8AlterMigrationTest 가 이어받는다.
        for version in tuple(f"grm-finding-taxonomy/v{n}" for n in range(1, 8)):
            self.assertIn(f"'{version}'", self.sql)

    def test_does_not_reclassify_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_does_not_touch_finding_text(self) -> None:
        """★v6/v7 공통 계약: 복원은 분류기 haystack 한정이고 저장 텍스트는 불변이다."""
        lowered = self.sql.lower()
        self.assertNotIn("set finding_text", lowered)
        self.assertNotIn("alter column finding_text", lowered)

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match, "expected a `declare <name> record;` line")
        record_var = declare_match.group(1)
        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match, "expected a `for <name> in` loop")
        self.assertEqual(loop_match.group(1), record_var)
        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match, "expected `from pg_constraint <alias>`")
        self.assertNotEqual(record_var, alias_match.group(1))

    def test_supersedes_045_by_expanding_not_narrowing(self) -> None:
        v6_sql = _TAXONOMY_V6_MIGRATION_PATH.read_text(encoding="utf-8")
        for version in (
            "grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2", "grm-finding-taxonomy/v3",
            "grm-finding-taxonomy/v4", "grm-finding-taxonomy/v5", "grm-finding-taxonomy/v6",
        ):
            self.assertIn(f"'{version}'", v6_sql)
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn("'grm-finding-taxonomy/v7'", self.sql)


class TaxonomyV8AlterMigrationTest(unittest.TestCase):
    """049 -- v8(어휘 공백) ALTER 마이그레이션. 012/044/045/047 과 동일 규율.

    번호가 048 이 아닌 이유: 048 은 다른 트랙(extraction_gap_source_says_none)이 먼저
    가져갔다. 마이그레이션 번호는 선착순이며 연속성 테스트가 충돌을 잡는다.
    """

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V8_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V8_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V8_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V8_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1_through_v8_in_list_check(self) -> None:
        self.assertIn("findings_taxonomy_version_v1v2v3v4v5v6v7v8_check", self.sql)
        # 트립와이어가 예고대로 v9 도입 때 발동했고 그 요구대로 050 이 신설됐다.
        # 적용 완료된 마이그레이션 파일은 **불변**이므로 049 는 자기가 쓴 v1~v8 로
        # 동결한다 -- 현행 추종은 TaxonomyV9AlterMigrationTest 가 이어받는다.
        for version in tuple(f"grm-finding-taxonomy/v{n}" for n in range(1, 9)):
            self.assertIn(f"'{version}'", self.sql)

    def test_does_not_reclassify_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_does_not_touch_finding_text(self) -> None:
        lowered = self.sql.lower()
        self.assertNotIn("set finding_text", lowered)
        self.assertNotIn("alter column finding_text", lowered)

    def test_loop_variable_does_not_shadow_query_table_alias(self) -> None:
        declare_match = re.search(r"declare\s+(\w+)\s+record;", self.sql)
        self.assertIsNotNone(declare_match)
        record_var = declare_match.group(1)
        loop_match = re.search(r"for\s+(\w+)\s+in", self.sql)
        self.assertIsNotNone(loop_match)
        self.assertEqual(loop_match.group(1), record_var)
        alias_match = re.search(r"from\s+pg_constraint\s+(\w+)", self.sql)
        self.assertIsNotNone(alias_match)
        self.assertNotEqual(record_var, alias_match.group(1))

    def test_supersedes_047_by_expanding_not_narrowing(self) -> None:
        v7_sql = _TAXONOMY_V7_MIGRATION_PATH.read_text(encoding="utf-8")
        for version in tuple(f"grm-finding-taxonomy/v{n}" for n in range(1, 8)):
            self.assertIn(f"'{version}'", v7_sql)
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn("'grm-finding-taxonomy/v8'", self.sql)


class TaxonomyV9AlterMigrationTest(unittest.TestCase):
    """050 -- v9(503B 용기 표시정보) ALTER 마이그레이션. 012/044/045/047/049 와 동일 규율."""

    def setUp(self) -> None:
        self.assertTrue(
            _TAXONOMY_V9_MIGRATION_PATH.is_file(), f"missing {_TAXONOMY_V9_MIGRATION_PATH}"
        )
        self.sql = _TAXONOMY_V9_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TAXONOMY_V9_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_adds_named_v1_through_v9_in_list_check(self) -> None:
        """050 은 더 이상 현행이 아니다 -- v9 시점의 목록을 그대로 고정한다.

        현행 목록(gf.TAXONOMY_VERSIONS) 대조는 아래 TaxonomyV10AlterMigrationTest 가
        064 에 대해 이어받는다. 지난 마이그레이션에 미래 버전을 요구하면 그 파일이
        영원히 고쳐져야 하고, 그건 provenance 를 무너뜨린다."""
        self.assertIn("findings_taxonomy_version_v1v2v3v4v5v6v7v8v9_check", self.sql)
        for n in range(1, 10):
            self.assertIn(f"'grm-finding-taxonomy/v{n}'", self.sql)
        self.assertNotIn("'grm-finding-taxonomy/v10'", self.sql)

    def test_does_not_reclassify_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())

    def test_does_not_touch_finding_text(self) -> None:
        lowered = self.sql.lower()
        self.assertNotIn("set finding_text", lowered)
        self.assertNotIn("alter column finding_text", lowered)

    def test_supersedes_049_by_expanding_not_narrowing(self) -> None:
        v8_sql = _TAXONOMY_V8_MIGRATION_PATH.read_text(encoding="utf-8")
        for version in tuple(f"grm-finding-taxonomy/v{n}" for n in range(1, 9)):
            self.assertIn(f"'{version}'", v8_sql)
            self.assertIn(f"'{version}'", self.sql)
        self.assertIn("'grm-finding-taxonomy/v9'", self.sql)


class TaxonomyV10AlterMigrationTest(unittest.TestCase):
    """064 는 **현행** taxonomy ALTER 다 -- gf.TAXONOMY_VERSIONS 와 일치해야 한다.

    코드가 v11 으로 올라가면 이 테스트가 새 ALTER 를 요구하며 깨진다(의도). 050 이
    v9 때 하던 역할을 그대로 물려받았고, 050 쪽은 그 시점 목록으로 얼렸다 --
    지난 마이그레이션에 미래 버전을 요구하면 provenance 가 무너진다.
    """

    def setUp(self) -> None:
        self.sql = _TAXONOMY_V10_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_migration_file_exists_without_crlf(self) -> None:
        self.assertTrue(_TAXONOMY_V10_MIGRATION_PATH.is_file(),
                        f"missing {_TAXONOMY_V10_MIGRATION_PATH}")
        self.assertNotIn(b"\r\n", _TAXONOMY_V10_MIGRATION_PATH.read_bytes())

    def test_targets_public_findings_taxonomy_version_column(self) -> None:
        self.assertIn("public.findings", self.sql)
        self.assertIn("taxonomy_version", self.sql)

    def test_uses_do_block_and_pg_constraint_lookup(self) -> None:
        self.assertIn("do $$", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_named_check_lists_exactly_the_current_versions(self) -> None:
        self.assertIn("findings_taxonomy_version_v1v2v3v4v5v6v7v8v9v10_check", self.sql)
        for version in gf.TAXONOMY_VERSIONS:
            self.assertIn(f"'{version}'", self.sql)

    def test_does_not_reclassify_existing_rows(self) -> None:
        """★CHECK 확장과 재분류는 다른 일이다 -- 이 파일은 기존 행을 건드리지 않는다.

        (재분류는 findings_reclassify_service.py 를 사람이 dispatch 해서 한다.)"""
        lowered = self.sql.lower()
        self.assertNotIn("update public.findings", lowered)
        self.assertNotIn("set category_code", lowered)

    def test_does_not_touch_finding_text(self) -> None:
        self.assertNotIn("set finding_text", self.sql.lower())


class TranslationColumnsMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            _TRANSLATION_MIGRATION_PATH.is_file(), f"missing {_TRANSLATION_MIGRATION_PATH}"
        )
        self.sql = _TRANSLATION_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TRANSLATION_MIGRATION_PATH.read_bytes())

    def test_adds_both_columns_idempotently(self) -> None:
        self.assertIn(
            "add column if not exists finding_text_ko text not null default ''", self.sql
        )
        self.assertIn(
            "add column if not exists translation_method text not null default ''", self.sql
        )

    def test_check_constraint_is_drop_then_add_named(self) -> None:
        self.assertIn(
            "drop constraint if exists findings_translation_method_check", self.sql
        )
        self.assertIn("add constraint", self.sql)
        self.assertIn("findings_translation_method_check", self.sql)
        for value in gf.TRANSLATION_METHODS:
            self.assertIn(f"'{value}'", self.sql)
        self.assertIn(
            "check (translation_method in ('', 'llm_assisted', 'manual'));", self.sql
        )

    def test_does_not_touch_finding_text_or_existing_rows(self) -> None:
        self.assertNotIn("update public.findings", self.sql.lower())
        self.assertNotIn("finding_text ", self.sql)

    def test_no_do_block_needed(self) -> None:
        self.assertNotIn("do $$", self.sql)


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
                self.assertTrue(stmt.endswith("on conflict do nothing;"))
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
