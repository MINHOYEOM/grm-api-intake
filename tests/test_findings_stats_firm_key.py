#!/usr/bin/env python3
"""FIND-1 트렌드 업체 랭킹 정규화 migration tests (017_findings_stats_firm_key.sql).

Offline source-text checks only -- no network, no real Postgres/sqlite connection.
Mirrors the style of test_findings_scope_purity.py (010) and test_findings_firm_key.py
(013): this migration create-or-replaces findings_stats() again, changing only the
top_firms key (firm_name group by -> firm_key group by) while leaving every other key
(totals/by_agency_category/by_month/by_source/by_evidence) byte-identical to 010's body.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_STATS_FIRM_KEY_PATH = _MIGRATIONS_DIR / "017_findings_stats_firm_key.sql"
_SCOPE_PURITY_PATH = _MIGRATIONS_DIR / "010_findings_scope_purity.sql"
_FIRM_KEY_PATH = _MIGRATIONS_DIR / "013_findings_firm_key.sql"
_STATS_RPC_PATH = _MIGRATIONS_DIR / "007_findings_stats_rpc.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


def _extract_findings_stats_body(sql: str) -> str:
    match = re.search(
        r"create or replace function public\.findings_stats\(\)"
        r".*?\$\$(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match is not None, "could not locate findings_stats() body"
    return match.group(1)


def _extract_top_firms_block(body: str) -> str:
    # top_firms is the last top-level key in the jsonb_build_object(...) call -- slice
    # from its start to the closing `)` that ends the whole findings_stats() select.
    start = body.index("'top_firms'")
    return body[start:]


def _extract_other_keys_block(body: str) -> str:
    # Everything up to (but excluding) the 'top_firms' key -- this is the part that
    # must be byte-identical to 010's findings_stats() body.
    start = body.index("'top_firms'")
    return body[:start]


class MigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_STATS_FIRM_KEY_PATH.is_file(), f"missing {_STATS_FIRM_KEY_PATH}")
        self.sql = _STATS_FIRM_KEY_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _STATS_FIRM_KEY_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 15)

    def test_documents_supersede_chain_007_010_017(self) -> None:
        self.assertIn("supersede", self.sql.lower())
        self.assertIn("007", self.sql)
        self.assertIn("010", self.sql)
        self.assertIn("017", self.sql)

    def test_documents_004_009_pitfalls_not_applicable(self) -> None:
        self.assertIn("004", self.sql)
        self.assertIn("009", self.sql)
        # Only the executable SQL (comments stripped) must be free of a plpgsql DO
        # block/DECLARE section -- the prose header legitimately names "declare" to
        # explain *why* the 004 pitfall class doesn't apply here.
        self.assertNotIn("do $$", self.code)
        self.assertNotIn("declare", self.code)

    def test_no_array_slice_syntax(self) -> None:
        # 009 pitfall class does not apply -- findings_stats() takes no arguments.
        self.assertNotIn("[1:", self.sql)


class FunctionDefinedOnceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _STATS_FIRM_KEY_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_findings_stats_redefined_exactly_once(self) -> None:
        self.assertEqual(
            self.sql.count("create or replace function public.findings_stats()"), 1
        )

    def test_signature_unchanged_security_definer_stable_search_path(self) -> None:
        self.assertIn(
            "create or replace function public.findings_stats()"
            "\nreturns jsonb\nlanguage sql\nstable\nsecurity definer\nset search_path = public",
            self.sql,
        )

    def test_no_grant_or_revoke_reissued(self) -> None:
        # Signature is unchanged (findings_stats(), no args) -- create or replace
        # preserves the existing 007/010 grants, so this file must not touch them.
        self.assertNotIn("revoke all on function", self.sql)
        self.assertNotIn("grant execute on function", self.sql)

    def test_no_other_007_008_009_010_013_functions_touched(self) -> None:
        for fn in (
            "findings_firm_stats(p_firm text)",
            "findings_category_matrix()",
            "findings_translation_queue(p_limit integer default 200)",
            "findings_translation_rows(p_finding_ids text[])",
            "grm_classify_483_scope(p_est_type text, p_len integer)",
            "grm_findings_scope_status_trigger()",
            "grm_normalize_firm_name(p_name text)",
            "findings_firm_profile(p_firm_key text)",
        ):
            self.assertNotIn(
                f"create or replace function public.{fn}",
                self.sql,
                f"017 must not redefine {fn}",
            )


class TopFirmsFirmKeyGroupingTest(unittest.TestCase):
    """The whole point of this migration -- top_firms groups by firm_key, not
    firm_name, and includes both firm_key and a display firm_name per row."""

    def setUp(self) -> None:
        self.sql = _STATS_FIRM_KEY_PATH.read_text(encoding="utf-8")
        self.body = _extract_findings_stats_body(self.sql)
        self.top_firms = _extract_top_firms_block(self.body)

    def test_top_firms_key_present_exactly_once(self) -> None:
        self.assertEqual(self.body.count("'top_firms'"), 1)

    def test_groups_by_firm_key_not_firm_name(self) -> None:
        # The outer aggregation (cnt/public_cnt per firm) must group by firm_key.
        self.assertIn("group by firm_key", self.top_firms)
        # "group by firm_name" is legitimately present exactly once, but only inside
        # the `join lateral (...)` display-name picker (013 tiebreak convention) --
        # never as the grouping key that produces cnt/public_cnt.
        self.assertEqual(self.top_firms.count("group by firm_name"), 1)
        lateral_idx = self.top_firms.index("join lateral (")
        group_firm_name_idx = self.top_firms.index("group by firm_name")
        self.assertLess(
            lateral_idx,
            group_firm_name_idx,
            "group by firm_name must appear only inside the lateral display-name picker",
        )

    def test_returned_row_shape_has_firm_key_and_firm_name(self) -> None:
        for key in ("firm_key", "firm_name", "cnt", "public_cnt"):
            self.assertIn(f"'{key}'", self.top_firms)

    def test_limited_to_30_ordered_by_count_desc_then_firm_key(self) -> None:
        self.assertIn("limit 30", self.top_firms)
        self.assertIn("order by cnt desc, firm_key asc", self.top_firms)

    def test_display_name_tiebreak_matches_013_convention(self) -> None:
        # Must be the exact same tiebreak rule as 013's findings_firm_profile()
        # display_name subquery: most frequent firm_name in the firm_key group,
        # ties broken by longer string, then alphabetically.
        self.assertIn(
            "order by count(*) desc, length(firm_name) desc, firm_name asc",
            self.top_firms,
        )
        firm_key_sql = _FIRM_KEY_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "order by cnt desc, length(firm_name) desc, firm_name asc",
            firm_key_sql,
        )

    def test_scoped_to_scope_status_ok(self) -> None:
        self.assertGreaterEqual(self.top_firms.count("scope_status = 'ok'"), 2)

    def test_public_cnt_uses_006_010_gate_predicate(self) -> None:
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", self.top_firms)

    def test_uses_lateral_join_for_display_name_not_correlated_scalar_subquery(self) -> None:
        # Documents the chosen implementation shape (join lateral ... on true) so a
        # future refactor that silently drops the lateral join trips this test.
        self.assertIn("join lateral (", self.top_firms)
        self.assertIn(") dn on true", self.top_firms)


class OtherKeysUnchangedTest(unittest.TestCase):
    """totals/by_agency_category/by_month/by_source/by_evidence must be byte-identical
    to 010's findings_stats() body -- only top_firms may differ."""

    def setUp(self) -> None:
        self.new_sql = _STATS_FIRM_KEY_PATH.read_text(encoding="utf-8")
        self.old_sql = _SCOPE_PURITY_PATH.read_text(encoding="utf-8")
        self.new_body = _extract_findings_stats_body(self.new_sql)
        self.old_body = _extract_findings_stats_body(self.old_sql)

    def test_all_five_other_keys_present_in_both(self) -> None:
        for key in (
            "totals", "by_agency_category", "by_month", "by_source", "by_evidence",
        ):
            self.assertIn(f"'{key}'", self.new_body)
            self.assertIn(f"'{key}'", self.old_body)

    def test_non_top_firms_portion_is_byte_identical_to_010(self) -> None:
        new_other = _extract_other_keys_block(self.new_body)
        old_other = _extract_other_keys_block(self.old_body)
        self.assertEqual(
            new_other,
            old_other,
            "017 must not change any findings_stats() key other than top_firms",
        )

    def test_top_firms_block_itself_differs_from_010(self) -> None:
        # Sanity check the diff isn't accidentally empty (i.e. the test above isn't
        # vacuously true because nothing changed at all).
        new_top = _extract_top_firms_block(self.new_body)
        old_top = _extract_top_firms_block(self.old_body)
        self.assertNotEqual(new_top, old_top)


class NoRawTextFieldsInReturnedShapeTest(unittest.TestCase):
    """Safety contract, 007/010/013 lineage: aggregate counts + bibliographic metadata
    only, never finding text/translation/evidence URL/raw or row JSON payloads."""

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

    def setUp(self) -> None:
        self.sql = _STATS_FIRM_KEY_PATH.read_text(encoding="utf-8")
        self.all_quoted_keys = set(
            re.findall(r"(?:jsonb_build_object\(|,)\s*'([a-z_]+)'\s*,", self.sql)
        )

    def test_forbidden_text_fields_absent_from_object_keys(self) -> None:
        for field in self._FORBIDDEN_TEXT_FIELDS:
            self.assertNotIn(
                field,
                self.all_quoted_keys,
                f"{field!r} must not appear as a returned jsonb key -- raw text leak",
            )

    def test_forbidden_text_fields_never_appear_as_select_targets(self) -> None:
        code = _strip_sql_comments(self.sql)
        for field in self._FORBIDDEN_TEXT_FIELDS:
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
            "documents",
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
            "firm_key",
            "firm_name",
            "public_cnt",
        }
        unexpected = self.all_quoted_keys - allowed
        self.assertEqual(unexpected, set(), f"unexpected jsonb keys found: {unexpected}")


class SourceOfTruthPathsExistTest(unittest.TestCase):
    def test_referenced_migration_files_exist(self) -> None:
        for path in (_STATS_RPC_PATH, _SCOPE_PURITY_PATH, _FIRM_KEY_PATH):
            self.assertTrue(path.is_file(), f"missing {path}")


class UpstreamHeaderNoteTest(unittest.TestCase):
    """010 must carry a one-line pointer to 017 for the top_firms key, without its
    findings_stats() body being modified in place (010's own body stays the pre-017
    shape -- only 017's create or replace supersedes it live)."""

    def test_010_has_017_pointer_comment(self) -> None:
        sql = _SCOPE_PURITY_PATH.read_text(encoding="utf-8")
        self.assertIn("017_findings_stats_firm_key.sql", sql)

    def test_010_body_itself_still_groups_by_firm_name(self) -> None:
        # 010's own file text is left untouched (git history/original source stays
        # intact) -- only the added pointer comment changed, the function body text
        # in this file is that of the pre-017 top_firms shape.
        old_sql = _SCOPE_PURITY_PATH.read_text(encoding="utf-8")
        old_body = _extract_findings_stats_body(old_sql)
        old_top = _extract_top_firms_block(old_body)
        self.assertIn("group by firm_name", old_top)


if __name__ == "__main__":
    unittest.main()
