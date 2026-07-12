#!/usr/bin/env python3
"""FIND-1 업체명 정규화(firm_key) tests (013_findings_firm_key.sql +
grm_findings.normalize_firm_name).

Offline source-text / pure-function checks only -- no network, no real
Postgres/sqlite connection. Mirrors the style of test_findings_scope_purity.py
(010) and test_findings_stats_rpc.py (007): the SQL migration is checked as a
text contract (rule shape, safety contract, security-definer/search_path
convention), while grm_findings.normalize_firm_name is checked as an ordinary
Python function against a frozen fixture of live-measured firm_name variants.
The "parity" between the two implementations is therefore pinned two ways:
(1) the Python function's *behavior* is frozen against real-world variants,
and (2) the SQL function's *source text* is checked to contain the same five
rules in the same order. A live Postgres dry-run (control tower) is the only
way to prove byte-identical runtime output; that is out of scope here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import grm_findings


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_FIRM_KEY_MIGRATION_PATH = _MIGRATIONS_DIR / "013_findings_firm_key.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


# ----------------------------------------------------------------------------
# Part 1: Python normalize_firm_name() parity fixtures -- live-measured variant
# families (SCA Pharmaceuticals / QuVa Pharma / Hospira / Blue Bell Creameries /
# One Way Drug dba), plus entity/whitespace/empty/no-suffix edge cases.
# ----------------------------------------------------------------------------

# (raw firm_name, expected normalized firm_key)
_FIXTURES: tuple[tuple[str, str], ...] = (
    # SCA Pharmaceuticals -- 6-variant family from the live sweep.
    ("SCA Pharmaceuticals", "sca pharmaceuticals"),
    ("SCA Pharmaceuticals, Inc.", "sca pharmaceuticals"),
    ("SCA Pharmaceuticals LLC", "sca pharmaceuticals"),
    ("SCA Pharmaceuticals, Inc", "sca pharmaceuticals"),
    ("SCA PHARMACEUTICALS INC.", "sca pharmaceuticals"),
    ("SCA Pharmaceuticals Co.", "sca pharmaceuticals"),
    # QuVa Pharma.
    ("QuVa Pharma", "quva pharma"),
    ("QuVa Pharma, Inc.", "quva pharma"),
    ("QuVa Pharma LLC", "quva pharma"),
    # Hospira.
    ("Hospira, Inc.", "hospira"),
    ("Hospira Inc", "hospira"),
    ("HOSPIRA, INC", "hospira"),
    # Blue Bell Creameries.
    ("Blue Bell Creameries, Inc.", "blue bell creameries"),
    ("Blue Bell Creameries, L.P.", "blue bell creameries"),
    ("Blue Bell Creameries LP", "blue bell creameries"),
    # One Way Drug -- dba token removal + multi-suffix in one string.
    ("One Way Drug Co dba One Way Pharmacy", "one way drug one way pharmacy"),
    ("One Way Drug, Inc.", "one way drug"),
    # HTML entity restore (rule 1).
    ("Johnson &amp; Johnson", "johnson & johnson"),
    ("O&#039;Brien Pharma, Inc.", "o'brien pharma"),
    # Leading/trailing + internal whitespace collapse (rule 5).
    ("  Acme   Labs  ", "acme labs"),
    # Empty / blank input.
    ("", ""),
    ("   ", ""),
    # No corporate suffix present -- normalization is a no-op past lowercasing.
    ("Coherus BioSciences, Inc.", "coherus biosciences"),
    ("Cognate BioServices, Inc.", "cognate bioservices"),
    # Additional suffix-family coverage: gmbh, srl, sa, pvt/private/limited.
    (
        "Boehringer Ingelheim Pharma GmbH & Co. KG",
        "boehringer ingelheim pharma & kg",
    ),
    ("Farmalider, S.R.L.", "farmalider"),
    ("Roche Holding SA", "roche holding"),
    ("Sun Pharmaceutical Industries Pvt. Ltd.", "sun pharmaceutical industries"),
    ("XYZ Private Limited", "xyz"),
)


class NormalizeFirmNameParityTest(unittest.TestCase):
    def test_fixture_count_at_least_20(self) -> None:
        self.assertGreaterEqual(len(_FIXTURES), 20)

    def test_fixtures_are_frozen(self) -> None:
        for raw, expected in _FIXTURES:
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_firm_name(raw), expected)

    def test_none_input_returns_empty_string(self) -> None:
        self.assertEqual(grm_findings.normalize_firm_name(None), "")

    def test_same_variants_collapse_to_one_key(self) -> None:
        # The whole point of firm_key: every SCA Pharmaceuticals variant maps to the
        # same key (this is the collapsed-855-firms behavior, at unit scale).
        variants = [
            "SCA Pharmaceuticals",
            "SCA Pharmaceuticals, Inc.",
            "SCA Pharmaceuticals LLC",
            "SCA Pharmaceuticals, Inc",
            "SCA PHARMACEUTICALS INC.",
            "SCA Pharmaceuticals Co.",
        ]
        keys = {grm_findings.normalize_firm_name(v) for v in variants}
        self.assertEqual(keys, {"sca pharmaceuticals"})

    def test_co_suffix_is_word_bounded_not_substring(self) -> None:
        # Regression target named in the task: stripping the "co" corporate suffix
        # must not corrupt names that merely start with/contain "co" as a substring
        # of a longer word (Coherus, Cognate) -- \b (Python) / \y (Postgres) word
        # boundaries make this safe.
        self.assertEqual(
            grm_findings.normalize_firm_name("Coherus BioSciences, Inc."),
            "coherus biosciences",
        )
        self.assertTrue(
            grm_findings.normalize_firm_name("Coherus BioSciences, Inc.").startswith("coherus")
        )
        self.assertEqual(
            grm_findings.normalize_firm_name("Cognate BioServices, Inc."),
            "cognate bioservices",
        )

    def test_idempotent(self) -> None:
        # Normalizing an already-normalized key must be a no-op.
        for raw, expected in _FIXTURES:
            if not expected:
                continue
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_firm_name(expected), expected)


# ----------------------------------------------------------------------------
# Part 2: 013_findings_firm_key.sql -- offline text-contract checks.
# ----------------------------------------------------------------------------


class MigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            _FIRM_KEY_MIGRATION_PATH.is_file(), f"missing {_FIRM_KEY_MIGRATION_PATH}"
        )
        self.sql = _FIRM_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _FIRM_KEY_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_measured_convergence_982_to_855(self) -> None:
        self.assertIn("982", self.sql)
        self.assertIn("855", self.sql)

    def test_documents_004_009_pitfalls_not_applicable(self) -> None:
        self.assertIn("004/009 함정 해당 없음", self.sql)


class NormalizeFunctionShapeTest(unittest.TestCase):
    """(A) public.grm_normalize_firm_name -- signature + all 5 rules present."""

    def setUp(self) -> None:
        self.sql = _FIRM_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create or replace function public\.grm_normalize_firm_name\(p_name text\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate grm_normalize_firm_name body")
        self.body = match.group(1)

    def test_signature_is_immutable_sql_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function public.grm_normalize_firm_name(p_name text)"
            "\nreturns text\nlanguage sql\nimmutable\nset search_path = public",
            self.sql,
        )

    def test_rule1_html_entity_restore(self) -> None:
        self.assertIn("'&amp;', '&'", self.body)
        self.assertIn("'&#039;'", self.body)

    def test_rule2_lowercase(self) -> None:
        self.assertIn("lower(", self.body)

    def test_rule3_removes_period_and_comma(self) -> None:
        self.assertIn("'[.,]', '', 'g'", self.body)

    def test_rule4_word_bounded_corporate_suffix_removal(self) -> None:
        self.assertIn(r"\y(", self.body)
        self.assertIn(r")\y", self.body)
        self.assertIn(", 'g'", self.body)
        for token in (
            "inc", "llc", "ltd", "co", "corp", "corporation", "company",
            "limited", "lp", "llp", "pvt", "private", "gmbh", "sa", "srl", "dba",
        ):
            self.assertIn(
                token,
                self.body,
                f"missing corporate suffix token: {token!r}",
            )

    def test_rule4_uses_postgres_y_boundary_not_bare_b(self) -> None:
        # Postgres word-boundary escape is \y, not \b (\b is backspace in POSIX
        # ARE) -- 010's \yfarm\y convention, pinned here for the suffix removal.
        self.assertIn(r"\y(inc|llc|ltd|co|corp|corporation|company|limited|lp|llp|pvt|private|gmbh|sa|srl|dba)\y", self.body)

    def test_rule5_whitespace_collapse_and_trim(self) -> None:
        self.assertIn(r"'\s+', ' ', 'g'", self.body)
        self.assertIn("trim(", self.body)

    def test_rules_applied_in_documented_order(self) -> None:
        # regexp_replace(...) nests outward: each subsequent call wraps the entire
        # previous call as its first (string) argument, so the *pattern literal* of
        # each successive call textually follows the complete previous call -- this
        # left-to-right literal order therefore does reflect true evaluation order
        # for the punct -> suffix -> whitespace chain (rules 3 -> 4 -> 5).
        idx_punct = self.body.index("'[.,]'")
        idx_suffix = self.body.index(r"\y(inc|")
        idx_ws = self.body.index(r"'\s+'")
        self.assertLess(idx_punct, idx_suffix)
        self.assertLess(idx_suffix, idx_ws)

        # Entity-restore (rule 1) and lowercase (rule 2) are nested *inside* the
        # first regexp_replace's string argument (lower(replace(replace(...)))) --
        # note lower(...) is the outer wrapper of the replace() calls, so its token
        # appears textually to the *left* of the entity literals even though it is
        # semantically applied *after* them; both must still land strictly before
        # the punctuation-removal literal, confirming they run before rule 3.
        idx_entity = self.body.index("'&amp;'")
        idx_lower_token = self.body.index("lower(")
        self.assertLess(idx_lower_token, idx_punct)
        self.assertLess(idx_entity, idx_punct)


class PythonSqlSuffixSetParityTest(unittest.TestCase):
    """The SQL suffix alternation and the Python _FIRM_SUFFIX_RE alternation must
    list the exact same 16 tokens in the exact same order -- this is the closest
    an offline test can get to pinning cross-language parity without a live DB."""

    def setUp(self) -> None:
        sql = _FIRM_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(r"\\y\(([a-z|]+)\)\\y", sql)
        self.assertIsNotNone(match, "could not find SQL suffix alternation")
        self.sql_tokens = match.group(1).split("|")

        py_source = Path(grm_findings.__file__).read_text(encoding="utf-8")
        py_match = re.search(
            r'_FIRM_SUFFIX_RE = re\.compile\(\s*r"\\b\(([a-z|]+)\)\\b"', py_source
        )
        self.assertIsNotNone(py_match, "could not find Python suffix alternation")
        self.py_tokens = py_match.group(1).split("|")

    def test_token_lists_are_identical_and_ordered_the_same(self) -> None:
        self.assertEqual(self.sql_tokens, self.py_tokens)

    def test_exactly_sixteen_tokens(self) -> None:
        self.assertEqual(len(self.sql_tokens), 16)


class GeneratedColumnTest(unittest.TestCase):
    """(B) findings.firm_key -- STORED GENERATED column, no trigger/backfill needed."""

    def setUp(self) -> None:
        self.sql = _FIRM_KEY_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_adds_generated_stored_column_idempotently(self) -> None:
        self.assertIn(
            "alter table public.findings\n"
            "  add column if not exists firm_key text generated always as (\n"
            "    public.grm_normalize_firm_name(firm_name)\n"
            "  ) stored;",
            self.sql,
        )

    def test_no_trigger_or_backfill_statements_for_firm_key(self) -> None:
        # The whole point of using a generated column: no `create trigger` and no
        # `update public.findings` backfill statement should exist in this file
        # (unlike 010's scope_status, which needed both).
        self.assertNotIn("create trigger", self.sql)
        self.assertNotIn("update public.findings", self.sql)

    def test_creates_btree_index_on_firm_key(self) -> None:
        self.assertIn(
            "create index if not exists idx_findings_firm_key\n"
            "  on public.findings (firm_key);",
            self.sql,
        )

    def test_no_plpgsql_do_blocks_or_declared_variables(self) -> None:
        # 004 regression class does not apply here -- confirm no DO block/declare.
        self.assertNotIn("do $$", self.sql)
        self.assertNotIn("declare", self.sql)

    def test_no_array_slice_syntax(self) -> None:
        # 009 regression class does not apply here -- confirm no array slicing in the
        # executable SQL (the header prose *names* 009's `[1:500]` pattern to explain
        # why it's inapplicable, so check comment-stripped code, not raw text).
        self.assertNotIn("[1:", _strip_sql_comments(self.sql))


class FirmProfileRpcShapeTest(unittest.TestCase):
    """(C) public.findings_firm_profile -- signature, filters, safety contract."""

    def setUp(self) -> None:
        self.sql = _FIRM_KEY_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create or replace function public\.findings_firm_profile\(p_firm_key text\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate findings_firm_profile body")
        self.body = match.group(1)

    def test_signature_is_security_definer_stable_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function public.findings_firm_profile(p_firm_key text)"
            "\nreturns jsonb\nlanguage sql\nstable\nsecurity definer\nset search_path = public",
            self.sql,
        )

    def test_scope_status_ok_filter_used_pervasively(self) -> None:
        # 010 convention continuation -- every findings query in this RPC must
        # exclude non_pharma/fragment rows.
        self.assertGreaterEqual(self.body.count("scope_status = 'ok'"), 8)

    def test_all_required_top_level_keys_present(self) -> None:
        for key in (
            "firm_key", "display_name", "totals", "by_category", "by_year",
            "by_source", "documents",
        ):
            self.assertIn(f"'{key}'", self.body)

    def test_totals_has_required_subkeys(self) -> None:
        totals_match = re.search(
            r"'totals', jsonb_build_object\((.*?)\),\s*\n\s*'by_category'",
            self.body,
            re.DOTALL,
        )
        self.assertIsNotNone(totals_match, "could not isolate totals object")
        totals_body = totals_match.group(1)
        for key in ("findings", "public_findings", "documents", "first_seen", "last_seen"):
            self.assertIn(f"'{key}'", totals_body)

    def test_documents_array_has_required_fields(self) -> None:
        for key in ("raw_signal_id", "published_date", "source", "obs_cnt", "public_obs_cnt"):
            self.assertIn(f"'{key}'", self.body)

    def test_documents_array_capped_at_100(self) -> None:
        self.assertIn("limit 100", self.body)

    def test_display_name_ties_broken_by_longer_string(self) -> None:
        self.assertIn("order by cnt desc, length(firm_name) desc, firm_name asc", self.body)

    def test_public_gate_predicate_matches_006_010_convention(self) -> None:
        self.assertIn("finding_text_ko <> '' or finding_language = 'KO'", self.body)

    def test_forbidden_text_fields_absent_from_object_keys(self) -> None:
        quoted_keys = set(
            re.findall(r"(?:jsonb_build_object\(|,)\s*'([a-z_]+)'\s*,", self.body)
        )
        for field in ("finding_text", "finding_text_ko", "evidence_url", "raw_json", "row_json"):
            self.assertNotIn(field, quoted_keys, f"{field!r} leaked as a returned jsonb key")

    def test_forbidden_text_fields_never_selected_as_values(self) -> None:
        # finding_text_ko legitimately appears only inside the public-gate boolean
        # predicate (`finding_text_ko <> ''`), never as a bare selected value.
        for field in ("finding_text", "raw_json", "row_json", "evidence_url"):
            self.assertNotRegex(
                self.body,
                rf"(?:select|,|jsonb_build_object\()\s*{field}\b",
                f"{field!r} appears at a select-target position",
            )
        self.assertNotRegex(
            self.body,
            r"(?:select|,|jsonb_build_object\()\s*finding_text_ko\b(?!\s*<>)",
            "finding_text_ko appears outside the gate predicate",
        )


class GrantsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _FIRM_KEY_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_revoke_then_grant_for_both_new_functions(self) -> None:
        self.assertIn(
            "revoke all on function public.grm_normalize_firm_name(text) from public;",
            self.sql,
        )
        self.assertIn(
            "revoke all on function public.findings_firm_profile(text) from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.grm_normalize_firm_name(text) to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_firm_profile(text) to anon, authenticated;",
            self.sql,
        )
        revoke_idx = self.sql.index("revoke all on function public.grm_normalize_firm_name")
        grant_idx = self.sql.index("grant execute on function public.grm_normalize_firm_name")
        self.assertLess(revoke_idx, grant_idx)

    def test_no_existing_007_008_009_010_functions_touched(self) -> None:
        for fn in (
            "findings_stats()",
            "findings_firm_stats(p_firm text)",
            "findings_category_matrix()",
            "findings_translation_queue(p_limit integer default 200)",
            "findings_translation_rows(p_finding_ids text[])",
        ):
            self.assertNotIn(
                f"create or replace function public.{fn}",
                self.sql,
                f"013 must not redefine {fn}",
            )


class SourceOfTruthExistsTest(unittest.TestCase):
    def test_prerequisite_migrations_exist(self) -> None:
        for name in ("002_findings.sql", "010_findings_scope_purity.sql"):
            path = _MIGRATIONS_DIR / name
            self.assertTrue(path.is_file(), f"missing {path}")


if __name__ == "__main__":
    unittest.main()
