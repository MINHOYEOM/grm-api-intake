#!/usr/bin/env python3
"""FIND-1 F3a stats RPC migration tests (007_findings_stats_rpc.sql).

Offline source-text checks only -- no network, no real Postgres/sqlite connection.
Mirrors the style of the 003/004/005/006 migration tests in test_findings_supabase.py
and test_findings_publish_gate.py.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_RPC_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "web" / "migrations" / "007_findings_stats_rpc.sql"
)


def _strip_sql_comments(sql: str) -> str:
    """Drop full-line `--` comments so code-shape assertions aren't tripped up by the
    prose header comment (which legitimately names forbidden fields/keywords for
    documentation purposes -- the safety contract is about the executable SQL, not
    about avoiding those words in prose)."""
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)

# Fields that must never appear inside a jsonb_build_object(...) key list -- the whole
# point of these RPCs is aggregate-only, no raw text/URL payloads.
_FORBIDDEN_TEXT_FIELDS = (
    "finding_text",
    "finding_text_ko",
    "evidence_url",
    "raw_json",
    "row_json",
    "inspector_names",
    "cfr_refs",
    "mfds_refs",
)


class RpcMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_RPC_MIGRATION_PATH.is_file(), f"missing {_RPC_MIGRATION_PATH}")
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _RPC_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 8)


class FunctionDefinitionShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_both_functions_defined_idempotently(self) -> None:
        self.assertEqual(
            self.sql.count("create or replace function public.findings_stats()"), 1
        )
        self.assertEqual(
            self.sql.count("create or replace function public.findings_firm_stats(p_firm text)"),
            1,
        )

    def test_both_functions_return_jsonb(self) -> None:
        self.assertEqual(self.code.count("returns jsonb"), 2)

    def test_both_functions_are_security_definer(self) -> None:
        self.assertEqual(self.code.count("security definer"), 2)

    def test_both_functions_pin_search_path_to_public(self) -> None:
        # Mutable search_path is a Supabase advisors lint warning for security-definer
        # functions -- both RPCs must pin it explicitly.
        self.assertEqual(self.code.count("set search_path = public"), 2)

    def test_both_functions_are_stable_sql_language(self) -> None:
        self.assertEqual(self.code.count("language sql"), 2)
        self.assertEqual(self.code.count("\nstable\n"), 2)


class GrantRevokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_revokes_public_execute_before_granting(self) -> None:
        self.assertIn("revoke all on function public.findings_stats() from public;", self.sql)
        self.assertIn(
            "revoke all on function public.findings_firm_stats(text) from public;", self.sql
        )
        revoke_idx = self.sql.index("revoke all on function public.findings_stats()")
        grant_idx = self.sql.index("grant execute on function public.findings_stats()")
        self.assertLess(revoke_idx, grant_idx, "revoke must precede grant")

    def test_grants_execute_to_anon_and_authenticated(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_stats() to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_firm_stats(text) to anon, authenticated;",
            self.sql,
        )


class TopFirmsLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_top_firms_key_present(self) -> None:
        self.assertIn("'top_firms'", self.sql)

    def test_top_firms_limited_to_30_ordered_by_count_desc_then_name(self) -> None:
        self.assertIn("limit 30", self.sql)
        self.assertIn("order by cnt desc, firm_name asc", self.sql)


class NoRawTextFieldsInReturnedShapeTest(unittest.TestCase):
    """The safety contract: aggregate counts + bibliographic metadata only, never the
    finding text, translation, evidence URL, or raw/row JSON payloads."""

    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")
        # Extract every jsonb_build_object(...) key literal ('key', ...) across the file --
        # this is the actual returned-shape surface of both functions.
        self.jsonb_keys = set(re.findall(r"jsonb_build_object\(\s*'([a-z_]+)'", self.sql))
        # jsonb_build_object calls with more than one key pair on one physical line/call
        # also need their subsequent 'key' occurrences captured -- scan all quoted keys
        # that appear as an object-key position (immediately preceded by `(` or `,` and
        # followed by `,`).
        self.all_quoted_keys = set(
            re.findall(r"(?:jsonb_build_object\(|,)\s*'([a-z_]+)'\s*,", self.sql)
        )

    def test_forbidden_text_fields_absent_from_object_keys(self) -> None:
        for field in _FORBIDDEN_TEXT_FIELDS:
            self.assertNotIn(
                field,
                self.all_quoted_keys,
                f"{field!r} must not appear as a returned jsonb key -- raw text leak",
            )

    def test_forbidden_text_fields_never_appear_as_select_targets(self) -> None:
        # Belt-and-suspenders: none of the forbidden fields may appear as a selected
        # value (bare column, `select x`/`, x` position, or inside jsonb_build_object).
        # finding_text_ko legitimately appears inside WHERE-clause gate predicates
        # (finding_text_ko <> '' or finding_language = 'KO') -- that is a boolean
        # filter, not a returned value, so it is intentionally exempt here (covered
        # separately by GateConsistencyTest, which pins that exact predicate).
        code = _strip_sql_comments(self.sql)
        for field in _FORBIDDEN_TEXT_FIELDS:
            self.assertNotRegex(
                code,
                rf"(?:select|,|jsonb_build_object\()\s*{field}\b(?!\s*<>)",
                f"{field!r} appears at a select-target position in RPC SQL",
            )

    def test_only_expected_bibliographic_and_count_keys_present(self) -> None:
        allowed = {
            "totals",
            "findings",
            "public_findings",
            "raw_signals",
            "firms",
            "by_agency_category",
            "agency",
            "category_code",
            "cnt",
            "by_month",
            "month",
            "by_source",
            "source",
            "by_evidence",
            "evidence_level",
            "top_firms",
            "firm_name",
            "public_cnt",
            "by_category",
            "first_seen",
            "last_seen",
        }
        unexpected = self.all_quoted_keys - allowed
        self.assertEqual(unexpected, set(), f"unexpected jsonb keys found: {unexpected}")


class EmptyResultCoalesceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_array_fields_are_coalesced_to_empty_array(self) -> None:
        # 6 array-shaped fields across both functions: by_agency_category, by_month,
        # by_source, by_evidence, top_firms (findings_stats) + by_category, by_month,
        # by_source (findings_firm_stats) = 8 total coalesce(..., '[]'::jsonb) sites.
        self.assertEqual(self.sql.count("'[]'::jsonb"), 8)


class GateConsistencyTest(unittest.TestCase):
    """public_findings / public_cnt must use the exact same predicate as the 006 RLS
    policy -- any drift would make the aggregate lie relative to what anon can actually
    see via row-level reads."""

    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_uses_same_predicate_as_publish_gate_policy(self) -> None:
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", self.sql)
        gate_migration = (
            Path(__file__).resolve().parent.parent
            / "web"
            / "migrations"
            / "006_findings_publish_gate.sql"
        )
        gate_sql = gate_migration.read_text(encoding="utf-8")
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", gate_sql)


class VariableAliasCollisionTest(unittest.TestCase):
    """004 regression class: a plpgsql declared variable/parameter must never collide
    with a table/column name used inside its own query (ambiguous resolution). These
    RPCs are pure `language sql` (no DO block, no plpgsql record variables), so this
    test instead pins that the one declared parameter (p_firm) does not collide with
    the firm_name column or any table alias in the file."""

    def setUp(self) -> None:
        self.sql = _RPC_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_plpgsql_do_block_or_record_declarations(self) -> None:
        self.assertNotIn("do $$", self.sql)
        self.assertNotIn("declare", self.sql)

    def test_function_parameter_name_does_not_collide_with_columns(self) -> None:
        param_match = re.search(r"findings_firm_stats\((\w+) text\)", self.sql)
        self.assertIsNotNone(param_match, "expected findings_firm_stats(<param> text)")
        param_name = param_match.group(1)
        self.assertNotEqual(param_name, "firm_name")
        self.assertEqual(param_name, "p_firm")
        # The subquery alias `t` used throughout must also not equal the parameter name.
        table_aliases = set(re.findall(r"\)\s+(\w+)\s*$", self.sql, flags=re.MULTILINE))
        self.assertNotIn(param_name, table_aliases)


if __name__ == "__main__":
    unittest.main()
