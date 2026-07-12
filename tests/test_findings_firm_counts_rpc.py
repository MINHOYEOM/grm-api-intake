#!/usr/bin/env python3
"""014_findings_firm_counts.sql -- offline text-contract tests.

Mirrors the style of tests/test_findings_firm_key.py (013) and
tests/test_findings_stats_rpc.py (007): the SQL migration is checked as a text
contract (signature/safety-contract/security-definer/search_path convention/
the 009 array-slice-parenthesization pitfall), not executed against a live
Postgres/sqlite connection (no network, no DB -- this CC environment has no
Postgres access; a live Postgres dry-run is the control tower's job).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_MIGRATION_PATH = _MIGRATIONS_DIR / "014_findings_firm_counts.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


class MigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}")
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_009_pitfall_by_name(self) -> None:
        self.assertIn("009", self.sql)
        self.assertIn("42601", self.sql)


class FirmCountsRpcShapeTest(unittest.TestCase):
    """public.findings_firm_counts(p_firm_keys text[]) -- signature/filters/safety."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create or replace function public\.findings_firm_counts\(p_firm_keys text\[\]\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate findings_firm_counts body")
        self.body = match.group(1)

    def test_signature_is_security_definer_stable_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function public.findings_firm_counts(p_firm_keys text[])"
            "\nreturns jsonb\nlanguage sql\nstable\nsecurity definer\nset search_path = public",
            self.sql,
        )

    def test_scope_status_ok_filter_used(self) -> None:
        # 010 convention continuation.
        self.assertIn("scope_status = 'ok'", self.body)

    def test_returned_keys_are_firm_key_findings_documents_only(self) -> None:
        quoted_keys = set(re.findall(r"'([a-z_]+)'\s*,", self.body))
        self.assertEqual(quoted_keys, {"firm_key", "findings", "documents"})

    def test_forbidden_text_fields_absent(self) -> None:
        for field in (
            "finding_text", "finding_text_ko", "evidence_url", "raw_json",
            "row_json", "firm_name",
        ):
            self.assertNotIn(field, self.body, f"{field!r} leaked into findings_firm_counts body")

    # ★009 함정: 배열 슬라이스는 반드시 괄호로 감싸야 한다 -- 회귀 대상.
    def test_array_slice_is_parenthesized_009_pitfall(self) -> None:
        self.assertIn(
            "firm_key = any((coalesce(p_firm_keys, '{}'::text[]))[1:200])",
            self.body,
        )

    def test_array_slice_clamped_to_200(self) -> None:
        self.assertIn("[1:200]", self.body)

    def test_no_unparenthesized_array_slice_variant(self) -> None:
        # The exact bug shape the 009 pitfall warns about: slicing directly off
        # a bare coalesce(...) call without the extra wrapping parens.
        self.assertNotRegex(self.body, r"coalesce\([^()]*\)\[1:")

    def test_group_by_firm_key(self) -> None:
        self.assertIn("group by firm_key", self.body)

    def test_missing_keys_omitted_not_zero_filled(self) -> None:
        # No LEFT JOIN against an input-keys table and no explicit zero-fill --
        # this is a straight aggregate over matching rows only (contract: absent
        # firm_key in the input array simply never appears in the output array).
        self.assertNotIn("left join", self.body.lower())

    def test_distinct_document_count_uses_raw_signal_id(self) -> None:
        self.assertIn("count(distinct raw_signal_id)", self.body)


class GrantsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")

    def test_revoke_then_grant(self) -> None:
        self.assertIn(
            "revoke all on function public.findings_firm_counts(text[]) from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_firm_counts(text[]) to anon, authenticated;",
            self.sql,
        )
        revoke_idx = self.sql.index("revoke all on function public.findings_firm_counts")
        grant_idx = self.sql.index("grant execute on function public.findings_firm_counts")
        self.assertLess(revoke_idx, grant_idx)

    def test_no_existing_007_009_010_013_functions_touched(self) -> None:
        for fn in (
            "findings_stats()",
            "findings_firm_stats(p_firm text)",
            "findings_translation_queue(p_limit integer default 200)",
            "findings_translation_rows(p_finding_ids text[])",
            "findings_firm_profile(p_firm_key text)",
            "grm_normalize_firm_name(p_name text)",
        ):
            self.assertNotIn(
                f"create or replace function public.{fn}",
                self.sql,
                f"014 must not redefine {fn}",
            )


class SourceOfTruthExistsTest(unittest.TestCase):
    def test_prerequisite_migrations_exist(self) -> None:
        for name in (
            "002_findings.sql",
            "010_findings_scope_purity.sql",
            "013_findings_firm_key.sql",
        ):
            path = _MIGRATIONS_DIR / name
            self.assertTrue(path.is_file(), f"missing {path}")


if __name__ == "__main__":
    unittest.main()
