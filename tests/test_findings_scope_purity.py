#!/usr/bin/env python3
"""FIND-1 데이터 순도 마이그레이션 tests (010_findings_scope_purity.sql).

Offline source-text checks only -- no network, no real Postgres/sqlite connection.
Mirrors the style of test_findings_stats_rpc.py (007), test_findings_publish_gate.py
(006), and test_findings_translation_bridge.py (009).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_SCOPE_MIGRATION_PATH = _MIGRATIONS_DIR / "010_findings_scope_purity.sql"
_STATS_RPC_PATH = _MIGRATIONS_DIR / "007_findings_stats_rpc.sql"
_CATEGORY_MATRIX_PATH = _MIGRATIONS_DIR / "008_findings_category_matrix.sql"
_TRANSLATION_BRIDGE_PATH = _MIGRATIONS_DIR / "009_findings_translation_bridge.sql"
_PUBLISH_GATE_PATH = _MIGRATIONS_DIR / "006_findings_publish_gate.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


class ScopeMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_SCOPE_MIGRATION_PATH.is_file(), f"missing {_SCOPE_MIGRATION_PATH}")
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _SCOPE_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_measured_impact_counts(self) -> None:
        # The three live-measured defect counts this migration is anchored to.
        self.assertIn("477", self.sql)
        self.assertIn("49", self.sql)
        self.assertIn("229", self.sql)

    def test_documents_revert_procedure(self) -> None:
        self.assertIn("scope_status = 'ok'", self.sql)
        self.assertIn("되돌", self.sql)

    def test_documents_007_008_009_superseded(self) -> None:
        self.assertIn("supersede", self.sql.lower())
        self.assertIn("007", self.sql)
        self.assertIn("008", self.sql)
        self.assertIn("009", self.sql)


class ColumnAndConstraintTest(unittest.TestCase):
    """(A) column + check constraint, added idempotently (004-style existence guard)."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_adds_scope_status_column_if_not_exists(self) -> None:
        self.assertIn(
            "add column if not exists scope_status text not null default 'ok';",
            self.sql,
        )

    def test_constraint_allows_exactly_three_values(self) -> None:
        self.assertIn(
            "check (scope_status in ('ok', 'non_pharma', 'fragment')) not valid;",
            self.sql,
        )

    def test_constraint_guarded_by_existence_check_not_bare_add(self) -> None:
        # 004 regression class: re-running the file must not error on a duplicate
        # named constraint -- pin that the ADD CONSTRAINT is wrapped in an
        # existence guard (pg_constraint lookup), not a bare unconditional statement.
        self.assertIn("pg_constraint", self.sql)
        self.assertIn("findings_scope_status_chk", self.sql)
        guard_idx = self.sql.index("if not exists (")
        add_idx = self.sql.index("add constraint findings_scope_status_chk")
        self.assertLess(guard_idx, add_idx)

    def test_constraint_is_validated_after_add(self) -> None:
        self.assertIn("validate constraint findings_scope_status_chk;", self.sql)
        not_valid_idx = self.sql.index("not valid;")
        validate_idx = self.sql.index("validate constraint findings_scope_status_chk;")
        self.assertLess(not_valid_idx, validate_idx)


class ClassifyFunctionTest(unittest.TestCase):
    """(B) grm_classify_483_scope -- rule order and the non_pharma regex contract."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create or replace function public\.grm_classify_483_scope\(.*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate grm_classify_483_scope body")
        self.body = match.group(1)

    def test_function_signature(self) -> None:
        self.assertIn(
            "create or replace function public.grm_classify_483_scope"
            "(p_est_type text, p_len integer)",
            self.sql,
        )
        self.assertIn("returns text", self.sql)
        self.assertIn("language sql", self.sql)
        self.assertIn("immutable", self.code)

    def test_fragment_threshold_is_30(self) -> None:
        self.assertIn("< 30", self.body)

    def test_non_pharma_checked_before_fragment(self) -> None:
        non_pharma_idx = self.body.index("'non_pharma'")
        fragment_idx = self.body.index("'fragment'")
        self.assertLess(non_pharma_idx, fragment_idx)

    def test_non_pharma_regex_contains_key_food_ag_tokens(self) -> None:
        for token in (
            "shell egg",
            "egg manufacturer",
            "cheese",
            "peanut",
            "sprout",
            "pistachio",
            "fruit processor",
            "pet food",
            "animal feed",
            "infant formula",
            "produce manufacturer",
            "aircraft",
        ):
            self.assertIn(token, self.body, f"missing non_pharma token: {token!r}")

    def test_non_pharma_regex_contains_key_gcp_tokens(self) -> None:
        for token in (
            "institutional review board",
            "clinical investigator",
            "bioanalytical",
            "^sponsor$",
        ):
            self.assertIn(token, self.body, f"missing GCP token: {token!r}")

    def test_farm_token_is_word_bounded(self) -> None:
        # \yfarm\y (Postgres word-boundary), not a bare substring match that would
        # false-positive on e.g. "pharmaceutical".
        self.assertIn(r"\yfarm\y", self.body)

    def test_uses_case_insensitive_match_operator(self) -> None:
        self.assertIn("~*", self.body)

    def test_result_is_one_of_three_allowed_values(self) -> None:
        for value in ("'ok'", "'non_pharma'", "'fragment'"):
            self.assertIn(value, self.body)


class BackfillUpdateTest(unittest.TestCase):
    """(C) backfill UPDATE -- scoped strictly to source='FDA 483', joined via
    raw_signal_id, using the same coalesce/nullif/trim extraction as the trigger."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_update_targets_findings_scoped_to_fda_483(self) -> None:
        self.assertIn("update public.findings f", self.sql)
        self.assertIn("and f.source = 'FDA 483';", self.sql)

    def test_update_joins_raw_signals_on_raw_signal_id(self) -> None:
        self.assertIn("from public.raw_signals rs", self.sql)
        self.assertIn("where rs.raw_signal_id = f.raw_signal_id", self.sql)

    def test_update_extracts_establishment_type_via_jsonb_cast(self) -> None:
        self.assertIn("(rs.raw_json::jsonb) ->> 'establishment_type'", self.sql)

    def test_update_uses_classify_function(self) -> None:
        self.assertIn("public.grm_classify_483_scope(", self.sql)
        self.assertIn("length(f.finding_text)", self.sql)


class TriggerTest(unittest.TestCase):
    """(D) BEFORE INSERT trigger -- self-maintaining classification for future rows,
    483-only, defaults to 'ok' when the raw_signal row is not (yet) visible."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"create or replace function public\.grm_findings_scope_status_trigger\(\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate trigger function body")
        self.body = match.group(1)

    def test_trigger_function_returns_trigger_plpgsql(self) -> None:
        self.assertIn("returns trigger", self.sql)
        self.assertIn("language plpgsql", self.sql)

    def test_trigger_function_pins_search_path(self) -> None:
        self.assertIn(
            "create or replace function public.grm_findings_scope_status_trigger()"
            "\nreturns trigger\nlanguage plpgsql\nset search_path = public",
            self.sql,
        )

    def test_trigger_only_acts_on_fda_483_source(self) -> None:
        self.assertIn("if new.source = 'FDA 483' then", self.body)

    def test_trigger_defaults_to_ok_when_raw_signal_not_found(self) -> None:
        self.assertIn("if v_est_type is null then", self.body)
        # The assignment immediately following the null-check must be the literal 'ok'.
        null_branch = self.body[self.body.index("if v_est_type is null then"):]
        null_branch = null_branch[: null_branch.index("else")]
        self.assertIn("new.scope_status := 'ok';", null_branch)

    def test_trigger_uses_classify_function_for_found_raw_signal(self) -> None:
        self.assertIn(
            "new.scope_status := public.grm_classify_483_scope("
            "v_est_type, length(new.finding_text));",
            self.body,
        )

    def test_declared_variable_does_not_collide_with_any_findings_column(self) -> None:
        # 004 regression class: a plpgsql declared variable must never share a name
        # with a table/column referenced in the same query (ambiguous resolution).
        declare_match = re.search(r"declare\s+(\w+)\s+text;", self.body)
        self.assertIsNotNone(declare_match, "expected a single declared text variable")
        var_name = declare_match.group(1)
        findings_columns = {
            "schema_version", "taxonomy_version", "finding_id", "raw_signal_id",
            "source", "agency", "document_type", "document_id", "published_date",
            "firm_name", "entity_id", "site_name", "site_country", "product_family",
            "modality", "category_code", "category_label_ko", "finding_text",
            "finding_language", "evidence_level", "evidence_url", "inspector_names",
            "cfr_refs", "mfds_refs", "extraction_method", "confidence",
            "review_status", "finding_text_ko", "translation_method", "ingested_at",
            "scope_status",
        }
        self.assertNotIn(var_name, findings_columns)

    def test_trigger_created_before_insert_idempotently(self) -> None:
        self.assertIn(
            "drop trigger if exists findings_scope_status_biu on public.findings;",
            self.sql,
        )
        self.assertIn(
            "create trigger findings_scope_status_biu\nbefore insert on public.findings",
            self.sql,
        )
        drop_idx = self.sql.index("drop trigger if exists findings_scope_status_biu")
        create_idx = self.sql.index("create trigger findings_scope_status_biu")
        self.assertLess(drop_idx, create_idx)


class PublishGatePolicyUpdateTest(unittest.TestCase):
    """(E) 006 policy replaced with an added scope_status='ok' AND clause."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_drops_then_recreates_named_policy_idempotently(self) -> None:
        self.assertEqual(
            self.sql.count("drop policy if exists findings_public_read on public.findings;"),
            1,
        )
        self.assertEqual(self.sql.count("create policy findings_public_read"), 1)
        drop_idx = self.sql.index("drop policy if exists findings_public_read")
        create_idx = self.sql.index("create policy findings_public_read")
        self.assertLess(drop_idx, create_idx)

    def test_policy_preserves_original_translation_or_ko_condition(self) -> None:
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", self.sql)

    def test_policy_adds_scope_status_ok_condition(self) -> None:
        self.assertIn("and scope_status = 'ok'", self.sql)

    def test_006_gate_predicate_present_for_reference(self) -> None:
        gate_sql = _PUBLISH_GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", gate_sql)


class RpcFilterTest(unittest.TestCase):
    """(F) 007/008/009 RPCs redefined with scope_status='ok' filters (except
    findings_translation_rows, which is intentionally untouched)."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_findings_stats_redefined_with_documents_key(self) -> None:
        self.assertEqual(
            self.sql.count("create or replace function public.findings_stats()"), 1
        )
        self.assertIn("'documents', (", self.sql)
        self.assertIn(
            "select count(distinct raw_signal_id) from public.findings where scope_status = 'ok'",
            self.sql,
        )

    def test_findings_stats_totals_findings_and_public_findings_filtered(self) -> None:
        match = re.search(
            r"create or replace function public\.findings_stats\(\).*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        totals_match = re.search(
            r"'totals', jsonb_build_object\((.*?)\),\s*\n\s*'by_agency_category'",
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(totals_match, "could not isolate totals object")
        totals_body = totals_match.group(1)
        self.assertIn(
            "select count(*) from public.findings where scope_status = 'ok'", totals_body
        )
        self.assertIn("'firms', (", totals_body)

    def test_findings_stats_other_keys_preserved(self) -> None:
        # Every original 007 top-level key must still be present -- this migration
        # only adds a filter + the new 'documents' key, it must not drop anything.
        original_sql = _STATS_RPC_PATH.read_text(encoding="utf-8")
        original_keys = set(re.findall(r"jsonb_build_object\(\s*'([a-z_]+)'", original_sql))
        new_keys = set(re.findall(r"jsonb_build_object\(\s*'([a-z_]+)'", self.sql))
        missing = original_keys - new_keys
        self.assertEqual(missing, set(), f"010 dropped keys that 007 defined: {missing}")

    def test_findings_firm_stats_redefined_and_filtered(self) -> None:
        self.assertEqual(
            self.sql.count(
                "create or replace function public.findings_firm_stats(p_firm text)"
            ),
            1,
        )
        match = re.search(
            r"create or replace function public\.findings_firm_stats\(p_firm text\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        # Every WHERE clause filtering on firm_name must also carry the scope filter
        # (the clause may wrap onto a following line, e.g. the public_findings
        # sub-clause, so capture up to the next `)` or blank-ish boundary rather than
        # just the rest of the physical line).
        firm_clauses = re.findall(r"where firm_name = p_firm.*?(?=\)|\n\s*(?:group|order|from|select))", body, re.DOTALL)
        self.assertTrue(firm_clauses, "expected at least one firm_name filter clause")
        for clause in firm_clauses:
            self.assertIn("scope_status = 'ok'", clause)

    def test_findings_category_matrix_redefined_and_filtered(self) -> None:
        self.assertEqual(
            self.sql.count("create or replace function public.findings_category_matrix()"),
            1,
        )
        match = re.search(
            r"create or replace function public\.findings_category_matrix\(\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertEqual(body.count("scope_status = 'ok'"), 3)

    def test_findings_translation_queue_redefined_and_filtered_both_sides(self) -> None:
        self.assertEqual(
            self.sql.count(
                "create or replace function public.findings_translation_queue"
                "(p_limit integer default 200)"
            ),
            1,
        )
        match = re.search(
            r"create or replace function public\.findings_translation_queue"
            r"\(p_limit integer default 200\).*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        # untranslated_total AND items[] both must carry the scope filter.
        self.assertEqual(body.count("coalesce(finding_text_ko, '') = '' and scope_status = 'ok'"), 2)

    def test_findings_translation_rows_redefined_but_unfiltered(self) -> None:
        self.assertEqual(
            self.sql.count(
                "create or replace function public.findings_translation_rows"
                "(p_finding_ids text[])"
            ),
            1,
        )
        match = re.search(
            r"create or replace function public\.findings_translation_rows"
            r"\(p_finding_ids text\[\]\).*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertNotIn("scope_status", body)

    def test_no_grant_or_revoke_statements_reissued_for_rpcs(self) -> None:
        # Grants must be inherited unchanged from 007/008/009 -- this migration must
        # not touch them at all.
        self.assertNotIn("revoke all on function", self.sql)
        self.assertNotIn("grant execute on function", self.sql)


class ArraySliceRequiresParensRegressionTest(unittest.TestCase):
    """The 009 live-DB-only syntax trap (control-tower verified for PR #170: a bare
    `)[1:500]` slice on a function-call result is a Postgres syntax error) must not
    regress when findings_translation_rows is re-declared in 010."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_array_slice_wraps_coalesce_call_in_parens(self) -> None:
        self.assertIn(
            "(coalesce(p_finding_ids, '{}'::text[]))[1:500]",
            self.sql,
        )

    def test_unparenthesized_slice_form_is_absent(self) -> None:
        self.assertNotIn(
            "coalesce(p_finding_ids, '{}'::text[])[1:500]",
            self.sql,
        )

    def test_matches_009_original_shape(self) -> None:
        bridge_sql = _TRANSLATION_BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn("(coalesce(p_finding_ids, '{}'::text[]))[1:500]", bridge_sql)


class SecurityDefinerAndSearchPathTest(unittest.TestCase):
    """The 5 redefined RPCs keep the 007/008/009 security-definer/search_path
    convention; the classify function and trigger function pin search_path too even
    though only the RPCs are security definer (advisors-lint hygiene)."""

    def setUp(self) -> None:
        self.sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_five_rpcs_are_security_definer_stable_sql(self) -> None:
        self.assertEqual(self.code.count("security definer"), 5)
        # findings_stats/firm_stats/category_matrix/translation_queue/translation_rows
        self.assertEqual(
            self.code.count("language sql\nstable\nsecurity definer\nset search_path = public"),
            5,
        )

    def test_classify_and_trigger_functions_pin_search_path(self) -> None:
        # grm_classify_483_scope + grm_findings_scope_status_trigger = 2 additional
        # `set search_path = public` occurrences beyond the 5 RPCs.
        self.assertEqual(self.code.count("set search_path = public"), 7)


class SourceOfTruthPathsExistTest(unittest.TestCase):
    def test_referenced_migration_files_exist(self) -> None:
        for path in (
            _STATS_RPC_PATH,
            _CATEGORY_MATRIX_PATH,
            _TRANSLATION_BRIDGE_PATH,
            _PUBLISH_GATE_PATH,
        ):
            self.assertTrue(path.is_file(), f"missing {path}")


class UpstreamHeaderNoteTest(unittest.TestCase):
    """007/008/009 must each carry a one-line pointer to 010 as the production
    source of truth, without their function bodies being modified."""

    def test_007_008_009_have_010_pointer_comment(self) -> None:
        for path in (_STATS_RPC_PATH, _CATEGORY_MATRIX_PATH, _TRANSLATION_BRIDGE_PATH):
            sql = path.read_text(encoding="utf-8")
            self.assertIn("010_findings_scope_purity.sql", sql, f"{path} missing 010 pointer")


if __name__ == "__main__":
    unittest.main()
