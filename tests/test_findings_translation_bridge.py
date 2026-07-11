#!/usr/bin/env python3
"""FIND-1 findings translation RLS-bridge migration tests
(009_findings_translation_bridge.sql).

Offline source-text checks only -- no network, no real Postgres/sqlite connection.
Mirrors the style of test_findings_stats_rpc.py (007) and test_findings_publish_gate.py
(006).

Unlike 007/008 (whose safety contract is "never return finding_text/finding_text_ko"),
this migration's contract is the deliberate opposite: it exists specifically to return
finding_text (and finding_text_ko) to anon for rows the 006 gate would otherwise hide,
so this file's tests pin the *presence* of those fields plus the documented security
exception, rather than their absence.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_BRIDGE_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "migrations"
    / "009_findings_translation_bridge.sql"
)

# findings_translate.py's Supabase export column contract (findings_translate._EXPORT_COLUMNS_SUPABASE).
_EXPORT_COLUMNS_SUPABASE = (
    "finding_id",
    "source",
    "agency",
    "category_code",
    "category_label_ko",
    "published_date",
    "firm_name",
    "finding_text",
    "finding_text_ko",
    "translation_method",
)


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


class BridgeMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_BRIDGE_MIGRATION_PATH.is_file(), f"missing {_BRIDGE_MIGRATION_PATH}")
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _BRIDGE_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 8)


class FunctionDefinitionShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_both_functions_defined_idempotently(self) -> None:
        self.assertEqual(
            self.sql.count(
                "create or replace function public.findings_translation_queue"
                "(p_limit integer default 200)"
            ),
            1,
        )
        self.assertEqual(
            self.sql.count(
                "create or replace function public.findings_translation_rows"
                "(p_finding_ids text[])"
            ),
            1,
        )

    def test_both_functions_return_jsonb(self) -> None:
        self.assertEqual(self.code.count("returns jsonb"), 2)

    def test_both_functions_are_security_definer(self) -> None:
        self.assertEqual(self.code.count("security definer"), 2)

    def test_both_functions_pin_search_path_to_public(self) -> None:
        # Mutable search_path is a Supabase advisors lint warning for security-definer
        # functions -- both RPCs must pin it explicitly (007/008/001 convention).
        self.assertEqual(self.code.count("set search_path = public"), 2)

    def test_both_functions_are_stable_sql_language(self) -> None:
        self.assertEqual(self.code.count("language sql"), 2)
        self.assertEqual(self.code.count("\nstable\n"), 2)


class GrantRevokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_revokes_public_execute_before_granting(self) -> None:
        self.assertIn(
            "revoke all on function public.findings_translation_queue(integer) from public;",
            self.sql,
        )
        self.assertIn(
            "revoke all on function public.findings_translation_rows(text[]) from public;",
            self.sql,
        )
        revoke_idx = self.sql.index(
            "revoke all on function public.findings_translation_queue(integer)"
        )
        grant_idx = self.sql.index(
            "grant execute on function public.findings_translation_queue(integer)"
        )
        self.assertLess(revoke_idx, grant_idx, "revoke must precede grant")

    def test_grants_execute_to_anon_and_authenticated(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_translation_queue(integer) "
            "to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_translation_rows(text[]) "
            "to anon, authenticated;",
            self.sql,
        )


class QueueLimitClampTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_p_limit_clamped_between_1_and_500_default_200(self) -> None:
        self.assertIn("p_limit integer default 200", self.sql)
        self.assertIn("greatest(1, least(coalesce(p_limit, 200), 500))", self.sql)

    def test_rows_finding_ids_clamped_to_first_500(self) -> None:
        self.assertIn("[1:500]", self.sql)


class QueueItemColumnsMatchExportContractTest(unittest.TestCase):
    """items[] returned by findings_translation_queue must be exactly
    findings_translate._EXPORT_COLUMNS_SUPABASE -- any drift breaks the Python
    envelope parser silently (extra/missing keys) since the RPC is the sole producer
    of this shape once deployed."""

    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")
        # jsonb_build_object('key', ...) call that builds each queue item -- extract
        # the first jsonb_build_object(...) whose key list is the item shape (the one
        # immediately following 'items' in the queue function, before 'untranslated_total').
        match = re.search(
            r"'items',.*?jsonb_agg\(\s*jsonb_build_object\((.*?)\)\s*order by",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate the queue item jsonb_build_object(...)")
        self.item_keys = re.findall(r"'([a-z_]+)'\s*,", match.group(1))

    def test_queue_item_keys_match_export_columns_supabase_exactly(self) -> None:
        self.assertEqual(tuple(self.item_keys), _EXPORT_COLUMNS_SUPABASE)


class RowsFunctionColumnsTest(unittest.TestCase):
    """findings_translation_rows must return exactly the 3 columns the --apply live
    validation reads (finding_id/finding_text/finding_text_ko), no more, no less."""

    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_rows_function_body_returns_exactly_three_columns(self) -> None:
        match = re.search(
            r"findings_translation_rows\(p_finding_ids text\[\]\).*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate findings_translation_rows body")
        body = match.group(1)
        keys = re.findall(r"'([a-z_]+)'\s*,", body)
        self.assertEqual(tuple(keys), ("finding_id", "finding_text", "finding_text_ko"))


class SecurityExceptionDocumentedTest(unittest.TestCase):
    """The whole point of this migration is a deliberate, narrow exception to the
    007/008 "never return raw text" safety contract -- that exception must be
    documented in-file, not just asserted in a PR description."""

    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_documents_deliberate_exception_to_007_008_contract(self) -> None:
        self.assertIn("보안 예외", self.sql)
        self.assertIn("007/008", self.sql)

    def test_documents_gate_is_display_quality_not_confidentiality(self) -> None:
        # The rationale hinges on 006 being a "web display quality" gate, not a
        # confidentiality boundary -- this must be spelled out, not assumed.
        self.assertIn("웹 열람 품질", self.sql)

    def test_mentions_the_measured_defect_it_fixes(self) -> None:
        # Anchors the migration to the concrete, control-tower-verified defect
        # (anon export sees 0 untranslated rows) rather than a hypothetical.
        self.assertIn("0건", self.sql)


class GateReferenceTest(unittest.TestCase):
    """This migration's rationale depends on the exact 006 gate predicate -- pin that
    it references the same table/gate this bridge is working around (structural
    consistency check, mirrors test_findings_stats_rpc.py's GateConsistencyTest)."""

    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_references_006_publish_gate(self) -> None:
        self.assertIn("006", self.sql)
        gate_migration = (
            Path(__file__).resolve().parent.parent
            / "web"
            / "migrations"
            / "006_findings_publish_gate.sql"
        )
        self.assertTrue(gate_migration.is_file())

    def test_queue_filters_on_empty_finding_text_ko(self) -> None:
        self.assertIn("coalesce(finding_text_ko, '') = ''", self.sql)


class VariableAliasCollisionTest(unittest.TestCase):
    """004 regression class: a plpgsql declared variable/parameter must never collide
    with a table/column name used inside its own query (ambiguous resolution). Both
    RPCs here are pure `language sql` (no DO block, no plpgsql record variables), so
    this test instead pins that there is no such block and that the declared
    parameters (p_limit/p_finding_ids) don't collide with any column name."""

    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_no_plpgsql_do_block_or_record_declarations(self) -> None:
        self.assertNotIn("do $$", self.sql)
        self.assertNotIn("declare", self.sql)

    def test_parameter_names_do_not_collide_with_columns(self) -> None:
        self.assertIn("p_limit integer default 200", self.sql)
        self.assertIn("p_finding_ids text[]", self.sql)
        for column in _EXPORT_COLUMNS_SUPABASE:
            self.assertNotEqual("p_limit", column)
            self.assertNotEqual("p_finding_ids", column)


class ArraySliceRequiresParensRegressionTest(unittest.TestCase):
    """Live-DB-only SQL trap regression -- same class as the 004 alias-collision
    regression (a defect that offline text/shape checks cannot catch on their own,
    only a real Postgres session surfaces it; control-tower verified against the live
    DB for PR #170: `ERROR: 42601: syntax error at or near "["`).

    Postgres rejects an array slice `[lo:hi]` applied directly to a function call's
    result -- `coalesce(p_finding_ids, '{}'::text[])[1:500]` is a syntax error. The
    call must be wrapped in its own parens before slicing:
    `(coalesce(p_finding_ids, '{}'::text[]))[1:500]`. This pins that shape so the
    unparenthesized form can never silently regress back in.
    """

    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_array_slice_wraps_coalesce_call_in_parens(self) -> None:
        self.assertIn(
            "(coalesce(p_finding_ids, '{}'::text[]))[1:500]",
            self.sql,
        )

    def test_every_array_slice_site_is_immediately_preceded_by_double_close_paren(
        self,
    ) -> None:
        # Every `[lo:hi]` slice in this file must be sliced off a parenthesized
        # expression, i.e. `))[1:500]` -- one `)` closing coalesce(...), one closing
        # the wrapping paren around it. A bare `)[1:500]` (single close paren, the
        # syntax-error form) must never appear.
        slice_positions = [
            m.start() for m in re.finditer(re.escape("[1:500]"), self.sql)
        ]
        self.assertTrue(slice_positions, "expected at least one [1:500] array slice")
        for pos in slice_positions:
            self.assertEqual(
                self.sql[pos - 2 : pos],
                "))",
                f"array slice at offset {pos} is not preceded by '))' -- "
                "the function call result must be wrapped in parens before slicing",
            )

    def test_unparenthesized_slice_form_is_absent(self) -> None:
        # The exact syntax-error form this migration originally shipped with.
        self.assertNotIn(
            "coalesce(p_finding_ids, '{}'::text[])[1:500]",
            self.sql,
        )


class EmptyResultCoalesceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _BRIDGE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_array_fields_are_coalesced_to_empty_array(self) -> None:
        # 'items' in findings_translation_queue + the top-level jsonb_agg in
        # findings_translation_rows = 2 coalesce(..., '[]'::jsonb) sites.
        self.assertEqual(self.sql.count("'[]'::jsonb"), 2)


if __name__ == "__main__":
    unittest.main()
