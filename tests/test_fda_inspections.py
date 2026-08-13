#!/usr/bin/env python3
"""058_fda_inspections.sql -- offline text-contract tests.

Mirrors the style of tests/test_firm_watchlist.py (015) and
tests/test_findings_country_key.py (055/057): the SQL migration is checked as
a text contract (table shape / CHECK-constraint scope guard / RLS lock-down
convention borrowed from raw_signals / aggregate-RPC safety contract), not
executed against a live Postgres connection (no network, no DB -- this CC
environment has no Postgres access; a live Postgres dry-run is the control
tower's job).

The real-world measurements cited here (6,417 GMP rows, 11,104 combined
ProductType=Drugs rows, 81 duplicate InspectionIDs before the ProjectArea
filter, 96 null CountryCode rows, 62 distinct CountryName values, 0 duplicate
InspectionIDs after filtering to Drug Quality Assurance) were captured from an
offline fixture during this task (scratchpad ddapi_gmp.json, 2026-08-11) --
that fixture lives outside the repo (session-scoped temp dir) and is
deliberately NOT read by this test file (it would not exist in CI or on
another machine). What is checked here is that the migration file *documents*
those measurements and that its schema/RPC shape matches the contract derived
from them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_inspections as mod  # noqa: E402  -- collector under test, added alongside 058

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "web" / "migrations"
_MIGRATION_PATH = _MIGRATIONS_DIR / "058_fda_inspections.sql"
_MIGRATION_059_PATH = _MIGRATIONS_DIR / "059_fda_inspection_stats_freshness.sql"
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"
_WORKFLOW_PATH = _WORKFLOW_DIR / "grm-fda-inspections-backfill.yml"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_COLLECTOR_FIXTURE_PATH = _FIXTURES_DIR / "fda_inspections_gmp.json"

# 실측(오프라인 픽스처 6,417건 전수, scratchpad ddapi_gmp.json, 2026-08-11 캡처) --
# 저장소 밖 파일이라 tests/fixtures/fda_inspections_gmp.json 은 그 축소판이다(파일
# 안의 "_fixture_note" 참고). 전수 분류 카운트만 별도 상수로 여기 고정해 둔다 --
# 라이브 재검증(워크플로 dry-run 리포트)이 이 값과 어긋나면 그 자체가 신호다.
MEASURED_GMP_TOTAL = 6417
MEASURED_GMP_NAI = 1409
MEASURED_GMP_VAI = 4055
MEASURED_GMP_OAI = 953
MEASURED_PRODUCT_TYPE_DRUGS_TOTAL = 11104
MEASURED_EXCLUDED_BIMO = 4651
MEASURED_EXCLUDED_UNAPPROVED = 31
MEASURED_EXCLUDED_OTC = 5


def _load_fixture_rows() -> list[dict]:
    with _COLLECTOR_FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data["rows"]


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

    def test_documents_004_009_pitfalls_not_applicable(self) -> None:
        self.assertIn("004/009 함정 해당 없음", self.sql)

    def test_no_plpgsql_do_blocks_or_declared_variables(self) -> None:
        self.assertNotIn("do $$", self.code.lower())
        self.assertNotIn("declare", self.code.lower())

    def test_no_array_slice_syntax(self) -> None:
        self.assertNotIn("[1:", self.code)

    def test_documents_measured_population_split(self) -> None:
        # Header must cite the actual measured counts that motivate the
        # project_area/product_type CHECK guard, not an assumed/rounded
        # number. Exact table-row substrings (not bare digits, which would
        # trivially match anywhere in a 200+ line file).
        for figure in (
            "Drug Quality Assurance          6,417",
            "Bioresearch Monitoring          4,651",
            "Unapproved and Misbranded Drugs    31",
            "Over-the-Counter Drug Evaluation    5",
            "14.9%",
            "8.6%",
        ):
            self.assertIn(figure, self.sql)

    def test_documents_pk_duplicate_investigation(self) -> None:
        # Header must show the "81 duplicates before filter, 0 after" check
        # that justifies InspectionID as a bare primary key.
        self.assertIn("81개", self.sql)
        self.assertIn("중복 0", self.sql)

    def test_documents_country_code_null_investigation(self) -> None:
        self.assertIn("96건", self.sql)
        self.assertIn("CountryCode", self.sql)

    def test_references_057_prerequisite(self) -> None:
        self.assertIn("057_grm_normalize_country_ddapi.sql", self.sql)


class TableShapeTest(unittest.TestCase):
    """public.fda_inspections -- column / PK / CHECK / generated-column contract."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        match = re.search(
            r"create table if not exists public\.fda_inspections \((.*?)\);",
            self.code,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate fda_inspections table body")
        self.body = match.group(1)

    def test_inspection_id_is_bare_primary_key(self) -> None:
        # Bare PK (no composite) -- the header's "GMP-only => 0 duplicate IDs"
        # investigation is exactly what licenses this (a composite key would
        # be the fallback if that investigation had failed).
        self.assertRegex(self.body, r"inspection_id\s+text primary key")

    def test_expected_columns_present(self) -> None:
        expected_cols = [
            "fei_number", "legal_name", "city", "state", "state_code",
            "zip_code", "country_name", "inspection_end_date", "fiscal_year",
            "classification", "classification_code", "project_area",
            "product_type", "posted_citations", "ingested_at", "country_key",
        ]
        for col in expected_cols:
            with self.subTest(col=col):
                self.assertRegex(self.body, rf"(?m)^\s*{re.escape(col)}\s")

    def test_no_country_code_column(self) -> None:
        # Deliberately omitted -- CountryCode is null for 96 rows (incl. all
        # 85 Korea rows) while CountryName is always populated; country_key
        # is derived from CountryName only (see migration header).
        self.assertNotRegex(self.body, r"(?m)^\s*country_code\s")

    def test_fiscal_year_is_int(self) -> None:
        self.assertRegex(self.body, r"fiscal_year\s+int not null")

    def test_inspection_end_date_is_date(self) -> None:
        self.assertRegex(self.body, r"inspection_end_date\s+date")

    def test_ingested_at_timestamptz_default_now(self) -> None:
        self.assertRegex(self.body, r"ingested_at\s+timestamptz not null default now\(\)")

    def test_classification_code_check_constrained_to_three_values(self) -> None:
        self.assertIn(
            "classification_code     text not null check (classification_code in ('NAI', 'VAI', 'OAI'))",
            self.body,
        )

    def test_project_area_check_locks_to_gmp_population(self) -> None:
        # The structural guard against the "mixed populations" failure mode
        # this repo has hit three times (052/053/054, per the header).
        self.assertIn(
            "project_area            text not null check (project_area = 'Drug Quality Assurance')",
            self.body,
        )

    def test_product_type_check_locks_to_drugs(self) -> None:
        self.assertIn(
            "product_type            text not null check (product_type = 'Drugs')",
            self.body,
        )

    def test_country_key_reuses_grm_normalize_country(self) -> None:
        # Must not define a new normalization function -- reuse 055/057's.
        self.assertIn(
            "country_key             text generated always as (\n"
            "                             public.grm_normalize_country(country_name)\n"
            "                           ) stored",
            self.body,
        )

    def test_no_new_normalize_function_defined(self) -> None:
        self.assertNotIn("create or replace function public.grm_normalize_country", self.code)
        self.assertNotIn("create function public.grm_normalize_country", self.code)

    def test_no_pii_beyond_public_regulatory_fields(self) -> None:
        # This table intentionally carries no fields beyond what DDAPI itself
        # returns for GMP inspections (business identifiers, not individual
        # PII) -- guard against scope creep (e.g. an inspector-name column,
        # which would belong to the excluded Bioresearch Monitoring population
        # anyway per the header).
        for leaked in ("ssn", "ip_address", "inspector_name", "ProjectAreaCode"):
            self.assertNotIn(leaked, self.body)


class IndexesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_four_expected_indexes(self) -> None:
        for name, col in (
            ("fda_inspections_country_key_idx", "country_key"),
            ("fda_inspections_classification_code_idx", "classification_code"),
            ("fda_inspections_inspection_end_date_idx", "inspection_end_date"),
            ("fda_inspections_fei_number_idx", "fei_number"),
        ):
            with self.subTest(name=name):
                self.assertIn(
                    f"create index if not exists {name}\n  on public.fda_inspections ({col});",
                    self.code,
                )


class RlsTest(unittest.TestCase):
    """RLS enabled, no anon/authenticated grant or policy -- raw_signals-style
    lock-down (justified in the migration header against the findings-style
    direct-read alternative)."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_rls_enabled(self) -> None:
        self.assertIn(
            "alter table public.fda_inspections enable row level security;", self.code
        )

    def test_anon_and_authenticated_revoked(self) -> None:
        self.assertIn(
            "revoke all on public.fda_inspections from anon, authenticated;", self.code
        )

    def test_no_select_policy_created_on_table(self) -> None:
        self.assertNotIn("create policy", self.code)

    def test_header_documents_raw_signals_precedent_not_findings_precedent(self) -> None:
        # The migration must show its work: it compared both existing public
        # conventions (findings direct-read vs. raw_signals lock+RPC) and
        # states which one it followed and why.
        self.assertIn("raw_signals", self.sql)
        self.assertIn("findings", self.sql)
        self.assertIn("배선 없는 슬롯 금지", self.sql)


class StatsRpcTest(unittest.TestCase):
    """public.fda_inspection_stats() -- count-only aggregate RPC (007/038
    safety contract: no raw/PII fields, ratios computed client-side)."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"create or replace function public\.fda_inspection_stats\(\)"
            r".*?\$\$(.*?)\$\$;",
            self.sql,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate fda_inspection_stats body")
        self.body = match.group(1)

    def test_signature_is_security_definer_stable_search_path_pinned(self) -> None:
        self.assertIn(
            "create or replace function public.fda_inspection_stats()\n"
            "returns jsonb\nlanguage sql\nstable\nsecurity definer\nset search_path = public",
            self.sql,
        )

    def test_no_parameters(self) -> None:
        self.assertIn("fda_inspection_stats()", self.sql)
        self.assertNotIn("fda_inspection_stats(p_", self.sql)

    def test_safety_contract_no_pii_or_identifying_fields(self) -> None:
        for field in (
            "legal_name", "fei_number", "city", "state_code", "zip_code",
            "'state'", "posted_citations",
        ):
            self.assertNotIn(field, self.body)

    def test_scope_block_present_with_required_keys(self) -> None:
        for key in (
            "'source'", "'project_area'", "'product_type'",
            "'excluded_project_areas'", "'fiscal_year_min'", "'fiscal_year_max'",
            "'unmapped_country_count'",
        ):
            self.assertIn(key, self.body)

    def test_excluded_project_areas_lists_all_three_other_populations(self) -> None:
        for area in (
            "Bioresearch Monitoring",
            "Unapproved and Misbranded Drugs",
            "Over-the-Counter Drug Evaluation",
        ):
            self.assertIn(area, self.body)

    def test_totals_block_has_raw_counts_not_ratios(self) -> None:
        self.assertIn("'inspections'", self.body)
        self.assertIn("'nai'", self.body)
        self.assertIn("'vai'", self.body)
        self.assertIn("'oai'", self.body)
        # No server-side ratio/percentage computation (007/038 convention:
        # server counts, client divides). Check the *code*, not comments --
        # a header comment legitimately spells out "31 / 5" style counts of
        # the excluded populations, which isn't a division operator.
        code_body = _strip_sql_comments(self.body)
        self.assertNotRegex(code_body, r"\boai\b[^,]*::(numeric|float|decimal)")
        self.assertNotIn("100.0", code_body)
        self.assertNotIn(" / ", code_body)

    def test_by_year_grouped_by_fiscal_year_with_three_classification_counts(self) -> None:
        self.assertIn("group by fiscal_year", self.body)
        self.assertIn("'fiscal_year'", self.body)

    def test_by_country_grouped_by_country_key_not_country_name(self) -> None:
        self.assertIn("group by country_key", self.body)
        self.assertIn("'code',", self.body)
        self.assertIn("'country',", self.body)

    def test_by_country_is_not_limited(self) -> None:
        # Deliberate deviation from the task brief's literal "상위 N" wording
        # -- see migration header for the reasoning (62 countries total, no
        # scaling risk, and truncation would break the by_country sum-equals-
        # totals invariant that 056 established as a repo-wide convention).
        # The grouping query itself (before the `left join lateral ... limit 1`
        # representative-name pick, which caps *rows within one country
        # group*, not the number of country groups) must have no LIMIT.
        group_query = self.body.split("left join lateral")[0]
        self.assertNotIn("limit 10", group_query)
        self.assertNotRegex(group_query, r"\blimit\s+\d+")
        # The only `limit` anywhere in the function must be the representative-
        # name tie-breaker's `limit 1`, not a cap on how many countries appear.
        limits = re.findall(r"\blimit\s+\d+\b", self.body)
        self.assertEqual(limits, ["limit 1"])

    def test_unmapped_country_bucket_included_not_dropped(self) -> None:
        # 043/055/056 convention: never silently drop the unmapped bucket.
        self.assertIn("country_key = ''", self.body)

    def test_documents_deliberate_top_n_deviation_in_header(self) -> None:
        self.assertIn("상위 N", self.sql)
        self.assertIn("합계 정합", self.sql)


class GrantsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")

    def test_revoke_then_grant_for_stats_rpc(self) -> None:
        self.assertIn(
            "revoke all on function public.fda_inspection_stats() from public;", self.sql
        )
        self.assertIn(
            "grant execute on function public.fda_inspection_stats() to anon, authenticated;",
            self.sql,
        )
        revoke_idx = self.sql.index("revoke all on function public.fda_inspection_stats()")
        grant_idx = self.sql.index("grant execute on function public.fda_inspection_stats()")
        self.assertLess(revoke_idx, grant_idx)

    def test_no_existing_objects_redefined(self) -> None:
        for obj in (
            "public.findings", "public.raw_signals", "findings_zone_category",
            "findings_search", "findings_country_unmapped",
        ):
            self.assertNotRegex(
                _strip_sql_comments(self.sql),
                rf"(create|alter|drop)[^;]*\b{re.escape(obj)}\b",
                f"058 must not touch {obj}",
            )


class SourceOfTruthExistsTest(unittest.TestCase):
    def test_prerequisite_migrations_exist(self) -> None:
        for name in (
            "002_findings.sql",
            "055_findings_country_key.sql",
            "057_grm_normalize_country_ddapi.sql",
        ):
            path = _MIGRATIONS_DIR / name
            self.assertTrue(path.is_file(), f"missing {path}")


class MeasuredPopulationArithmeticTest(unittest.TestCase):
    """The 058 header's four population counts must actually add up to the
    ProductType=Drugs total -- if a future edit changes one figure without the
    others, this catches the drift before it reaches the migration file."""

    def test_four_populations_sum_to_product_type_drugs_total(self) -> None:
        self.assertEqual(
            MEASURED_GMP_TOTAL + MEASURED_EXCLUDED_BIMO
            + MEASURED_EXCLUDED_UNAPPROVED + MEASURED_EXCLUDED_OTC,
            MEASURED_PRODUCT_TYPE_DRUGS_TOTAL,
        )

    def test_three_classification_codes_sum_to_gmp_total(self) -> None:
        self.assertEqual(
            MEASURED_GMP_NAI + MEASURED_GMP_VAI + MEASURED_GMP_OAI, MEASURED_GMP_TOTAL,
        )


# ============================================================================
# collect_fda_inspections.py -- offline collector tests (network 0).
#
# Fixture: tests/fixtures/fda_inspections_gmp.json ("rows" key) -- a scaled-down,
# representative slice of the pre-ProjectArea-filter population (see the fixture
# file's own "_fixture_note" for the exact reduction criteria: all 3
# classification codes, a null-CountryCode Korea row, a null-CountryCode India
# row, one representative of each of the 3 excluded ProjectAreas, a same-
# InspectionID-different-ProjectArea pair reproducing the real 81-duplicate
# pattern, and two synthetic (explicitly marked, not measured) rows designed to
# fail normalize_row.
#
# Fixture composition counted by hand (kept in sync with the file; if the
# fixture changes these constants must move with it):
#   11 rows total -- 7 with ProjectArea == 'Drug Quality Assurance' (GMP), 4
#   excluded (2 Bioresearch Monitoring, 1 Unapproved and Misbranded Drugs, 1
#   Over-the-Counter Drug Evaluation). Of the 7 GMP rows, 2 are the synthetic
#   normalize-failure cases, leaving 5 that normalize cleanly and are pairwise
#   unique on InspectionID (0 duplicates -- mirrors the real "GMP-only => 0
#   dup" invariant).
# ============================================================================


class TestBuildRequestBody(unittest.TestCase):
    def test_required_fields_all_present(self) -> None:
        body = mod.build_request_body(0, 5000)
        self.assertEqual(body["start"], 0)
        self.assertEqual(body["rows"], 5000)
        self.assertEqual(body["sort"], "InspectionID")
        self.assertEqual(body["sortorder"], "ASC")
        self.assertIn("filters", body)
        self.assertIn("columns", body)

    def test_project_area_never_in_filters(self) -> None:
        # ProjectArea is a return-only column -- putting it in filters causes a
        # live statuscode 413 (invalid_filters:["ProjectArea"]).
        body = mod.build_request_body(0, 5000)
        self.assertNotIn("ProjectArea", body["filters"])

    def test_filters_target_product_type_drugs(self) -> None:
        """★이 단언은 원래 `== {"ProductType": ["Drugs"]}` 였고, 그래서 **결함을 고정**하고
        있었다 — 회계연도 스코프가 빠진 상태를 "정답"으로 못 박은 것이다(2026-08-12 적대적
        검증이 잡았다). 필터는 ProductType **과** FiscalYear 둘 다여야 한다.
        상세 근거는 FiscalYearScopeTest 참고."""
        body = mod.build_request_body(0, 5000)
        self.assertEqual(body["filters"]["ProductType"], ["Drugs"])
        self.assertIn("FiscalYear", body["filters"])
        self.assertEqual(set(body["filters"]), {"ProductType", "FiscalYear"})

    def test_columns_include_all_persisted_and_diagnostic_fields(self) -> None:
        body = mod.build_request_body(0, 5000)
        for col in ("InspectionID", "FiscalYear", "ClassificationCode",
                    "ProjectArea", "ProductType", "CountryCode", "CountryName"):
            self.assertIn(col, body["columns"])

    def test_start_and_rows_vary_by_argument(self) -> None:
        body = mod.build_request_body(5000, 1104)
        self.assertEqual(body["start"], 5000)
        self.assertEqual(body["rows"], 1104)


class TestParseResponse(unittest.TestCase):
    def test_success_dict_with_result_array(self) -> None:
        rows, err = mod.parse_response(
            {"statuscode": 200, "message": "Success.", "result": [{"a": 1}]})
        self.assertEqual(err, "")
        self.assertEqual(rows, [{"a": 1}])

    def test_statuscode_400_with_message_success_is_still_success(self) -> None:
        """★the documented trap: a real success response carries statuscode 400.
        Success must be judged by message=="Success." + result, never statuscode."""
        rows, err = mod.parse_response(
            {"statuscode": 400, "message": "Success.", "result": [{"a": 1}, {"a": 2}]})
        self.assertEqual(err, "")
        self.assertEqual(len(rows), 2)

    def test_list_response_is_always_an_error_regardless_of_content(self) -> None:
        rows, err = mod.parse_response([{"error": "invalid_filters"}])
        self.assertEqual(rows, [])
        self.assertIn("error_response_is_list", err)

    def test_empty_list_response_is_still_an_error(self) -> None:
        rows, err = mod.parse_response([])
        self.assertEqual(rows, [])
        self.assertIn("error_response_is_list", err)

    def test_dict_with_wrong_message_is_an_error(self) -> None:
        rows, err = mod.parse_response(
            {"statuscode": 200, "message": "Something else.", "result": []})
        self.assertEqual(rows, [])
        self.assertIn("unexpected_message", err)

    def test_dict_missing_result_key_is_an_error(self) -> None:
        rows, err = mod.parse_response({"statuscode": 200, "message": "Success."})
        self.assertEqual(rows, [])
        self.assertIn("missing_result_array", err)

    def test_non_dict_non_list_is_an_error(self) -> None:
        rows, err = mod.parse_response("not json at all")
        self.assertEqual(rows, [])
        self.assertIn("unexpected_response_type", err)

    def test_non_dict_rows_inside_result_are_dropped_not_raised(self) -> None:
        rows, err = mod.parse_response(
            {"statuscode": 200, "message": "Success.", "result": [{"a": 1}, "junk", None]})
        self.assertEqual(err, "")
        self.assertEqual(rows, [{"a": 1}])


class TestSplitByProjectArea(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = _load_fixture_rows()

    def test_gmp_only_filters_out_bimo(self) -> None:
        # BIMO would deflate the OAI ratio 14.9% -> 8.6% if merged in (058 header).
        included, excluded = mod.split_by_project_area(self.rows)
        self.assertTrue(all(r["ProjectArea"] == "Drug Quality Assurance" for r in included))
        self.assertNotIn("Bioresearch Monitoring", [r.get("ProjectArea") for r in included])

    def test_fixture_composition_matches_hand_count(self) -> None:
        included, excluded = mod.split_by_project_area(self.rows)
        self.assertEqual(len(self.rows), 11)
        self.assertEqual(len(included), 7)
        self.assertEqual(excluded, {
            "Bioresearch Monitoring": 2,
            "Unapproved and Misbranded Drugs": 1,
            "Over-the-Counter Drug Evaluation": 1,
        })

    def test_unknown_project_area_is_named_not_silently_dropped(self) -> None:
        rows = [{"InspectionID": "9", "ProjectArea": "Some New Population Type"}]
        included, excluded = mod.split_by_project_area(rows)
        self.assertEqual(included, [])
        self.assertEqual(excluded, {"Some New Population Type": 1})


class TestFindDuplicateInspectionIds(unittest.TestCase):
    def test_gmp_subset_of_fixture_has_zero_duplicates(self) -> None:
        # Mirrors the 058 header's real-world invariant: GMP-only => 0 dup IDs.
        # The fixture's synthetic dual-classified pair is BIMO+GMP, so once
        # BIMO is filtered out only one copy of that ID remains in the GMP set.
        included, _excluded = mod.split_by_project_area(_load_fixture_rows())
        self.assertEqual(mod.find_duplicate_inspection_ids(included), {})

    def test_detector_actually_fires_on_a_real_duplicate(self) -> None:
        rows = [
            {"InspectionID": "1"}, {"InspectionID": "2"}, {"InspectionID": "1"},
        ]
        self.assertEqual(mod.find_duplicate_inspection_ids(rows), {"1": 2})

    def test_pre_filter_dual_classified_pair_would_have_counted_as_duplicate(self) -> None:
        # Show the *reason* the filter matters: run the detector on the raw
        # (unfiltered) fixture rows and confirm the pair is visible there.
        rows = _load_fixture_rows()
        dups = mod.find_duplicate_inspection_ids(rows)
        self.assertEqual(dups.get("1199001"), 2)


class TestNormalizeRow(unittest.TestCase):
    def _gmp_row(self, **over):
        row = {
            "InspectionID": "1106400", "FEINumber": "3004333526",
            "LegalName": "Hollywood Health Products, Inc.", "City": "Sunrise",
            "State": "Florida", "StateCode": "FL", "ZipCode": "33313",
            "CountryCode": "US", "CountryName": "United States",
            "InspectionEndDate": "2019-10-11", "FiscalYear": "2020",
            "Classification": "No Action Indicated (NAI)", "ClassificationCode": "NAI",
            "ProjectArea": "Drug Quality Assurance", "ProductType": "Drugs",
            "PostedCitations": "Yes",
        }
        row.update(over)
        return row

    def test_valid_row_normalizes_cleanly(self) -> None:
        payload, err = mod.normalize_row(self._gmp_row())
        self.assertEqual(err, "")
        self.assertEqual(payload["inspection_id"], "1106400")
        self.assertEqual(payload["fiscal_year"], 2020)
        self.assertIsInstance(payload["fiscal_year"], int)
        self.assertEqual(payload["classification_code"], "NAI")
        self.assertEqual(payload["inspection_end_date"], "2019-10-11")

    def test_country_key_is_never_in_the_payload(self) -> None:
        # ★the generated-column trap: sending it makes the whole insert fail.
        payload, err = mod.normalize_row(self._gmp_row())
        self.assertEqual(err, "")
        self.assertNotIn("country_key", payload)
        self.assertNotIn("CountryCode", payload)   # not a 058 column at all

    def test_null_state_fields_coalesce_to_empty_string_not_none(self) -> None:
        # 058's state/state_code/zip_code are NOT NULL -- sending None fails the insert.
        payload, err = mod.normalize_row(self._gmp_row(
            State=None, StateCode=None, ZipCode=None,
            CountryCode=None, CountryName="Korea (the Republic of)"))
        self.assertEqual(err, "")
        self.assertEqual(payload["state"], "")
        self.assertEqual(payload["state_code"], "")
        self.assertEqual(payload["zip_code"], "")
        self.assertIsInstance(payload["state"], str)
        self.assertEqual(payload["country_name"], "Korea (the Republic of)")

    def test_null_inspection_end_date_becomes_none_not_empty_string(self) -> None:
        # inspection_end_date is a nullable `date` column (058) -- '' is not a
        # valid date literal, so the empty case must become Python None.
        payload, err = mod.normalize_row(self._gmp_row(InspectionEndDate=None))
        self.assertEqual(err, "")
        self.assertIsNone(payload["inspection_end_date"])

    def test_missing_inspection_id_is_rejected(self) -> None:
        payload, err = mod.normalize_row(self._gmp_row(InspectionID=None))
        self.assertEqual(payload, {})
        self.assertIn("missing_inspection_id", err)

    def test_unparseable_fiscal_year_is_rejected_not_defaulted(self) -> None:
        payload, err = mod.normalize_row(self._gmp_row(FiscalYear="N/A"))
        self.assertEqual(payload, {})
        self.assertIn("unparseable_fiscal_year", err)

    def test_unexpected_classification_code_is_rejected(self) -> None:
        payload, err = mod.normalize_row(self._gmp_row(
            ClassificationCode="PENDING", Classification="Pending Classification"))
        self.assertEqual(payload, {})
        self.assertIn("unexpected_classification_code", err)

    def test_non_gmp_project_area_is_rejected_defensively(self) -> None:
        # Belt-and-suspenders: run() should never reach here with a non-GMP row
        # (split_by_project_area filters first), but normalize_row must not
        # silently accept one if it somehow did.
        payload, err = mod.normalize_row(self._gmp_row(ProjectArea="Bioresearch Monitoring"))
        self.assertEqual(payload, {})
        self.assertIn("unexpected_project_area", err)

    def test_wrong_product_type_is_rejected_defensively(self) -> None:
        payload, err = mod.normalize_row(self._gmp_row(ProductType="Biologics"))
        self.assertEqual(payload, {})
        self.assertIn("unexpected_product_type", err)


class _FakePagedHttpPost:
    """Slices an in-memory row list by (start, rows) from the POST body --
    stands in for the live DDAPI paginated POST endpoint.

    ★`start` is **1-based**, and this fake enforces that, because the live API
    does. Measured 2026-08-12 against api-datadashboard.fda.gov:

        start=0 -> HTTP 400 "start parameter must be numeric and greater than 0"
        start=1, rows=2 -> ['1251303', '1267171']
        start=3, rows=2 -> ['1267356', '1269601']
        start=5, rows=2 -> ['1277530', '1278916']

    This fake used to slice `all_rows[start:start+rows]` -- i.e. 0-based -- and
    so it **reproduced the production bug rather than catching it**: the
    collector started at 0, the fake happily returned page 1, every test passed,
    and the first live run died on its first request. A fake that is wrong in
    the same direction as the code under test proves nothing. Same failure
    shape as the FiscalYear defect earlier in this file: the test froze the bug.
    """

    def __init__(self, all_rows: list[dict], *, page_error_on_start: dict | None = None,
                errors_are_list: bool = False):
        self.all_rows = all_rows
        self.page_error_on_start = page_error_on_start or {}
        self.errors_are_list = errors_are_list
        self.calls: list[dict] = []

    def __call__(self, url, *, headers, body, timeout=None, retries=None):
        self.calls.append(body)
        start, rows = body["start"], body["rows"]
        if start in self.page_error_on_start:
            if self.errors_are_list:
                return [{"error": self.page_error_on_start[start]}]
            raise RuntimeError(self.page_error_on_start[start])
        if not isinstance(start, int) or start < 1:
            # Mirrors the live 400 -- a 0-based caller must fail here, loudly.
            raise RuntimeError(
                "HTTP 400: Request body start parameter must be numeric and "
                f"greater than 0. (got start={start!r})")
        page = self.all_rows[start - 1:start - 1 + rows]
        return {"statuscode": 200, "message": "Success.", "result": page}


class TestFetchAllPages(unittest.TestCase):
    def test_single_partial_page_stops_immediately(self) -> None:
        rows = [{"InspectionID": str(i)} for i in range(3)]
        http = _FakePagedHttpPost(rows)
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http, page_size=10)
        self.assertEqual(result.error, "")
        self.assertFalse(result.truncated)
        self.assertEqual(result.pages_fetched, 1)
        self.assertEqual(len(result.rows), 3)

    def test_paginates_across_the_5000_style_boundary(self) -> None:
        rows = [{"InspectionID": str(i)} for i in range(5)]
        http = _FakePagedHttpPost(rows)
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http,
                                     page_size=2, max_pages=10)
        self.assertEqual(result.error, "")
        self.assertFalse(result.truncated)
        # 2 + 2 + 1 = 3 pages, mirrors the real 5000+5000+1104 shape.
        self.assertEqual(result.pages_fetched, 3)
        self.assertEqual(len(result.rows), 5)
        # 1-based: page N starts at 1 + (N-1)*page_size. No overlap, no gap --
        # verified live (see _FakePagedHttpPost docstring).
        self.assertEqual([c["start"] for c in http.calls], [1, 3, 5])

    def test_first_page_start_is_one_not_zero(self) -> None:
        """★회귀 가드: 첫 페이지 start 가 0 이면 라이브에서 HTTP 400 으로 **첫 요청부터**
        죽는다(2026-08-12 실장애). 이 단언이 없으면 0-기반 픽스처와 0-기반 구현이
        서로를 통과시킨다."""
        rows = [{"InspectionID": str(i)} for i in range(3)]
        http = _FakePagedHttpPost(rows)
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http, page_size=10)
        self.assertEqual(result.error, "")
        self.assertEqual(http.calls[0]["start"], 1)

    def test_fake_rejects_zero_start_like_the_live_api(self) -> None:
        """픽스처 자신의 가드 — 픽스처가 0 을 조용히 받아 주면 위 가드가 무의미해진다."""
        http = _FakePagedHttpPost([{"InspectionID": "1"}])
        with self.assertRaises(RuntimeError) as ctx:
            http("u", headers={}, body={"start": 0, "rows": 2})
        self.assertIn("greater than 0", str(ctx.exception))

    def test_pages_do_not_overlap_or_skip_rows(self) -> None:
        """경계 산술 자체를 검사한다 — 겹치면 중복 적재, 비면 조용한 결손이다."""
        rows = [{"InspectionID": str(i)} for i in range(7)]
        http = _FakePagedHttpPost(rows)
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http,
                                     page_size=3, max_pages=10)
        self.assertEqual(result.error, "")
        got = [r["InspectionID"] for r in result.rows]
        self.assertEqual(got, [str(i) for i in range(7)])
        self.assertEqual(len(set(got)), 7)

    def test_max_pages_exhausted_with_full_last_page_is_truncated(self) -> None:
        # Exactly 4 rows at page_size=2 means both pages come back full --
        # nothing here tells the caller whether row 5 exists or not, so it
        # must be reported as truncated rather than silently accepted as done.
        rows = [{"InspectionID": str(i)} for i in range(4)]
        http = _FakePagedHttpPost(rows)
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http,
                                     page_size=2, max_pages=2)
        self.assertTrue(result.truncated)
        self.assertEqual(result.error, "")
        self.assertEqual(result.pages_fetched, 2)

    def test_page_level_transport_exception_is_surfaced_not_swallowed(self) -> None:
        rows = [{"InspectionID": str(i)} for i in range(5)]
        # start=3 is the **second** page at page_size=2 under 1-based paging
        # (pages start at 1, 3, 5...) -- so page 1 succeeds and page 2 blows up.
        http = _FakePagedHttpPost(rows, page_error_on_start={3: "boom"})
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http,
                                     page_size=2, max_pages=10)
        self.assertIn("page_fetch_failed", result.error)
        self.assertEqual(len(result.rows), 2)   # first page's rows are kept, not discarded

    def test_error_response_shaped_as_list_stops_pagination(self) -> None:
        rows = [{"InspectionID": str(i)} for i in range(5)]
        # start=1 is the first page under 1-based paging.
        http = _FakePagedHttpPost(rows, page_error_on_start={1: "invalid_filters"},
                                  errors_are_list=True)
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http,
                                     page_size=2, max_pages=10)
        self.assertIn("page_error", result.error)
        self.assertIn("error_response_is_list", result.error)

    def test_stable_sort_params_sent_on_every_page(self) -> None:
        rows = [{"InspectionID": str(i)} for i in range(3)]
        http = _FakePagedHttpPost(rows)
        mod.fetch_all_pages(api_user="u", api_key="k", http_post=http, page_size=10)
        self.assertEqual(http.calls[0]["sort"], "InspectionID")
        self.assertEqual(http.calls[0]["sortorder"], "ASC")

    def test_auth_headers_are_sent_not_query_params(self) -> None:
        seen_headers = {}

        def capture(url, *, headers, body, timeout=None, retries=None):
            seen_headers.update(headers)
            return {"statuscode": 200, "message": "Success.", "result": []}

        mod.fetch_all_pages(api_user="the-user", api_key="the-key",
                            http_post=capture, page_size=10)
        self.assertEqual(seen_headers["Authorization-User"], "the-user")
        self.assertEqual(seen_headers["Authorization-Key"], "the-key")


def _make_poster(*, err="", status=201):
    calls = []

    def poster(base_url, service_key, table, rows, on_conflict, *, resolution="ignore-duplicates"):
        calls.append(dict(base_url=base_url, service_key=service_key, table=table,
                          rows=rows, on_conflict=on_conflict, resolution=resolution))
        return status, (None if err else list(rows)), err

    poster.calls = calls
    return poster


class TestRunDryRun(unittest.TestCase):
    def test_dry_run_never_calls_poster(self) -> None:
        http = _FakePagedHttpPost(_load_fixture_rows())
        poster = _make_poster()
        report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            page_size=100, http_post=http, poster=poster)
        # code 1, not 0: the fixture bakes in 2 synthetic normalize-failure rows
        # on purpose (see the fixture file's own note) -- normalize_failed > 0
        # must be loud per the exit-code contract, even in dry-run.
        self.assertEqual(code, 1)
        self.assertEqual(report.received_total, 11)
        self.assertEqual(report.gmp_candidates, 7)
        self.assertEqual(report.normalize_failed, 2)
        self.assertEqual(report.would_upsert, 5)
        self.assertEqual(report.upserted, 0)
        self.assertEqual(poster.calls, [])
        self.assertEqual(report.excluded_by_project_area, {
            "Bioresearch Monitoring": 2,
            "Unapproved and Misbranded Drugs": 1,
            "Over-the-Counter Drug Evaluation": 1,
        })


class TestRunZeroRowFloor(unittest.TestCase):
    """★0건 하한 — 무인 월간 크론으로 승격하면서 신설(적대적 검증 HIGH).

    수동 실행에서는 사람이 리포트를 읽고 6,417 과 대조하니 0건이 무해했지만,
    크론에서는 그 안전장치가 없다. 그리고 이 소스에는 **0건으로 조용히 끝나는**
    경로가 둘 있고, 둘 다 초록으로 끝나면 실패 이슈가 안 열릴 뿐 아니라
    워크플로가 **열려 있던 실패 이슈까지 '복구됨'으로 닫아** 알람을 스스로 끈다.
    """

    def test_empty_ddapi_response_is_loud_not_green(self) -> None:
        """① DDAPI 가 200 + `result: []` 를 주는 경우."""
        http = _FakePagedHttpPost([])
        poster = _make_poster()
        report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            page_size=100, http_post=http, poster=poster)
        self.assertEqual(report.received_total, 0)
        self.assertEqual(report.gmp_candidates, 0)
        self.assertEqual(code, 2, "빈 응답이 초록으로 끝나면 조용한 열화가 된다")
        self.assertTrue(any("zero_gmp_rows" in e for e in report.errors))

    def test_upstream_project_area_rename_is_loud_not_green(self) -> None:
        """② FDA 가 ProjectArea 라벨을 바꿔 전량이 제외 버킷으로 가는 경우.

        `split_by_project_area` 는 설계상 "모르는 값은 제외"라 소리가 나지 않는다 —
        그래서 상류 문자열 하나가 바뀌면 6,417건 전량이 조용히 사라진다.
        """
        rows = _load_fixture_rows()
        renamed = []
        for r in rows:
            r = dict(r)
            if r.get("ProjectArea") == "Drug Quality Assurance":
                r["ProjectArea"] = "Drug Quality Assurance Program"
            renamed.append(r)
        http = _FakePagedHttpPost(renamed)
        poster = _make_poster()
        report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            page_size=100, http_post=http, poster=poster)
        self.assertGreater(report.received_total, 0, "행은 받았는데")
        self.assertEqual(report.gmp_candidates, 0, "적재 대상이 0이 됐는데")
        self.assertEqual(code, 2, "그런데도 초록이면 아무도 모른다")
        self.assertEqual(poster.calls, [], "0건이면 적재를 시도조차 하지 않는다")

    def test_escape_hatch_allows_zero_but_is_off_by_default(self) -> None:
        """탈출구는 있되 **기본이 꺼져 있어야** 한다 — 기본이 켜져 있으면 하한이 없는 것과 같다."""
        http = _FakePagedHttpPost([])
        _report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            page_size=100, http_post=http, poster=_make_poster(),
            allow_zero_rows=True)
        self.assertEqual(code, 0)
        parser = mod.build_arg_parser()
        self.assertIs(parser.parse_args([]).allow_zero_rows, False)

    def test_cli_default_keeps_the_floor_on(self) -> None:
        """워크플로는 이 플래그를 넘기지 않는다 — 넘기면 하한이 무력화된다."""
        text = _WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("--allow-zero-rows", text)


class TestRunApply(unittest.TestCase):
    def test_apply_upserts_normalized_rows_with_merge_duplicates(self) -> None:
        http = _FakePagedHttpPost(_load_fixture_rows())
        poster = _make_poster()
        report, code = mod.run(
            dry_run=False, base_url="https://x.supabase.co", service_key="svc",
            api_user="u", api_key="k", page_size=100, http_post=http, poster=poster)
        # code 1, not 0: same 2 synthetic normalize failures as above -- the
        # 5 clean rows still upsert successfully in the same run.
        self.assertEqual(code, 1)
        self.assertEqual(report.upserted, 5)
        self.assertEqual(report.upsert_chunk_failed, 0)
        self.assertEqual(len(poster.calls), 1)
        call = poster.calls[0]
        self.assertEqual(call["table"], "fda_inspections")
        self.assertEqual(call["on_conflict"], "inspection_id")
        self.assertEqual(call["resolution"], "merge-duplicates")
        self.assertEqual(len(call["rows"]), 5)
        for row in call["rows"]:
            self.assertNotIn("country_key", row)

    def test_chunking_splits_across_multiple_poster_calls(self) -> None:
        http = _FakePagedHttpPost(_load_fixture_rows())
        poster = _make_poster()
        report, code = mod.run(
            dry_run=False, base_url="https://x.supabase.co", service_key="svc",
            api_user="u", api_key="k", page_size=100, chunk_size=2,
            http_post=http, poster=poster)
        self.assertEqual(code, 1)   # same 2 synthetic normalize failures as above
        self.assertEqual(report.upserted, 5)
        # 5 valid rows / chunk_size 2 -> 3 POSTs (2, 2, 1)
        self.assertEqual(len(poster.calls), 3)
        self.assertEqual([len(c["rows"]) for c in poster.calls], [2, 2, 1])

    def test_chunk_failure_is_counted_and_does_not_abort_remaining_chunks(self) -> None:
        http = _FakePagedHttpPost(_load_fixture_rows())
        calls = []

        def flaky_poster(base_url, service_key, table, rows, on_conflict, *,
                         resolution="ignore-duplicates"):
            calls.append(rows)
            if len(calls) == 1:
                return 500, None, "http_500"
            return 201, list(rows), ""

        report, code = mod.run(
            dry_run=False, base_url="https://x.supabase.co", service_key="svc",
            api_user="u", api_key="k", page_size=100, chunk_size=2,
            http_post=http, poster=flaky_poster)
        self.assertEqual(code, 1)               # 조용한 부분 실패 금지
        self.assertEqual(report.upsert_chunk_failed, 1)
        self.assertEqual(report.upserted, 3)     # 나머지 두 청크(2+1)는 계속 적재
        self.assertEqual(len(calls), 3)


class TestRunDuplicateIdsAreLoud(unittest.TestCase):
    def test_duplicate_gmp_inspection_ids_fail_the_run_even_if_upsert_succeeds(self) -> None:
        rows = [
            {"InspectionID": "1", "FEINumber": "1", "LegalName": "A", "City": "X",
             "State": "TX", "StateCode": "TX", "ZipCode": "1", "CountryCode": "US",
             "CountryName": "United States", "InspectionEndDate": "2020-01-01",
             "FiscalYear": "2020", "Classification": "No Action Indicated (NAI)",
             "ClassificationCode": "NAI", "ProjectArea": "Drug Quality Assurance",
             "ProductType": "Drugs", "PostedCitations": "Yes"},
        ]
        dup = dict(rows[0])
        http = _FakePagedHttpPost(rows + [dup])
        poster = _make_poster()
        report, code = mod.run(
            dry_run=False, base_url="https://x.supabase.co", service_key="svc",
            api_user="u", api_key="k", page_size=100, http_post=http, poster=poster)
        self.assertEqual(code, 1)
        self.assertEqual(report.duplicate_inspection_ids, {"1": 2})
        # Upsert still runs -- merge-duplicates naturally collapses same-PK rows;
        # this is a loud data-quality flag, not a hard stop.
        self.assertEqual(report.upserted, 2)


class TestRunFetchFailures(unittest.TestCase):
    def test_fetch_error_exits_2_and_never_calls_poster(self) -> None:
        def boom(url, *, headers, body, timeout=None, retries=None):
            raise RuntimeError("network down")
        poster = _make_poster()
        report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            http_post=boom, poster=poster)
        self.assertEqual(code, 2)
        self.assertTrue(any("fetch_failed" in e for e in report.errors))
        self.assertEqual(poster.calls, [])

    def test_error_response_as_list_exits_2(self) -> None:
        def bad(url, *, headers, body, timeout=None, retries=None):
            return [{"error": "invalid_filters"}]
        report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            http_post=bad)
        self.assertEqual(code, 2)
        self.assertTrue(any("error_response_is_list" in e for e in report.errors))

    def test_truncated_pagination_exits_2_not_silently_accepted(self) -> None:
        rows = [{"InspectionID": str(i), "ProjectArea": "Drug Quality Assurance"}
                for i in range(4)]
        http = _FakePagedHttpPost(rows)
        report, code = mod.run(
            dry_run=True, base_url="", service_key="", api_user="u", api_key="k",
            page_size=2, max_pages=2, http_post=http)
        self.assertEqual(code, 2)
        self.assertTrue(report.truncated)
        self.assertTrue(any("truncated" in e for e in report.errors))


class TestMainCli(unittest.TestCase):
    def _clean_env(self):
        return {k: v for k, v in os.environ.items()
                if k not in ("FDA_API_USER", "FDA_DDAPI_KEY",
                             "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}

    def test_missing_ddapi_creds_exits_2_even_in_dry_run(self) -> None:
        # ★dry-run still calls the live DDAPI (only the Supabase write is
        # skipped) -- so DDAPI creds are required even here.
        with mock.patch.dict(os.environ, self._clean_env(), clear=True):
            self.assertEqual(mod.main([]), 2)

    def test_apply_requires_supabase_creds_too(self) -> None:
        env = self._clean_env()
        env["FDA_API_USER"] = "u"
        env["FDA_DDAPI_KEY"] = "k"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mod.main(["--apply"]), 2)

    def test_dry_run_with_creds_calls_ddapi_and_reports(self) -> None:
        http = _FakePagedHttpPost(_load_fixture_rows())
        env = self._clean_env()
        env["FDA_API_USER"] = "u"
        env["FDA_DDAPI_KEY"] = "k"
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(mod, "ddapi_post", http):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(["--page-size", "100"])
        # code 1, not 0: fixture bakes in 2 synthetic normalize-failure rows.
        self.assertEqual(code, 1)
        self.assertIn('"would_upsert": 5', buf.getvalue())

    def test_dry_run_and_apply_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            mod.build_arg_parser().parse_args(["--dry-run", "--apply"])


class FiscalYearScopeTest(unittest.TestCase):
    """★2026-08-12 적대적 검증이 잡은 결함의 회귀 방지.

    사용자는 세 선택지 중 **의약품 최근 7년(11,104건)** 을 골랐다. 전체 기간은 39,783건으로
    3.5배다. 그런데 수집기 초판의 `build_request_body` 에 `FiscalYear` 필터가 **아예 없어**
    라이브로 돌리면 승인 범위를 3.5배 넘겨 담을 참이었다. 게다가 모든 실측 기준값
    (6,417 / NAI 1,409 / VAI 4,055 / OAI 953)이 7년 기준이라 재현이 안 돼
    "뭔가 깨졌다"고 오판했을 것이다. 필터가 다시 빠지면 여기서 즉시 걸린다.
    """

    def test_request_body_always_scopes_fiscal_year(self):
        body = mod.build_request_body(0, 5000, ["2026", "2025"])
        self.assertIn("FiscalYear", body["filters"], "회계연도 스코프가 빠졌다")
        self.assertEqual(body["filters"]["FiscalYear"], ["2026", "2025"])
        self.assertEqual(body["filters"]["ProductType"], [mod.TARGET_PRODUCT_TYPE])

    def test_request_body_defaults_to_the_approved_window(self):
        """연도를 안 넘겨도 승인 범위(7년)로 좁혀야 한다 — 기본값이 '전체 기간'이면 안 된다."""
        body = mod.build_request_body(0, 5000)
        self.assertEqual(len(body["filters"]["FiscalYear"]), mod.DEFAULT_FISCAL_YEARS)

    def test_project_area_is_never_sent_as_a_filter(self):
        """ProjectArea 는 반환 전용 — filters 에 넣으면 statuscode 413 으로 전량 실패한다."""
        body = mod.build_request_body(0, 5000, ["2026"])
        self.assertNotIn("ProjectArea", body["filters"])

    def test_fiscal_year_boundary_is_october_first(self):
        """FDA 회계연도는 10/1 시작 — 9/30 과 10/1 이 서로 다른 FY 여야 한다."""
        self.assertEqual(mod.fiscal_year_of(date(2026, 9, 30)), 2026)
        self.assertEqual(mod.fiscal_year_of(date(2025, 10, 1)), 2026)
        self.assertEqual(mod.fiscal_year_of(date(2026, 1, 1)), 2026)

    def test_window_is_computed_not_hardcoded(self):
        """★연도를 상수로 박으면 내년 FY2027 유입분이 조용히 빠진다. 실행일에서 계산한다."""
        self.assertEqual(mod.fiscal_year_window(date(2026, 8, 12), 7),
                         ["2026", "2025", "2024", "2023", "2022", "2021", "2020"])
        # 한 해 뒤에는 창이 저절로 밀린다(하드코딩이면 이 단언이 깨진다).
        self.assertEqual(mod.fiscal_year_window(date(2027, 8, 12), 7)[0], "2027")
        self.assertNotIn("2020", mod.fiscal_year_window(date(2027, 8, 12), 7))

    def test_window_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            mod.fiscal_year_window(date(2026, 8, 12), 0)

    def test_fetch_passes_the_window_through_to_every_page(self):
        """배선 확인 — run/fetch 를 거쳐 실제 요청 바디까지 연도가 도달하는가."""
        seen: list[dict] = []

        def fake_post(url, *, headers, body, timeout, retries):
            seen.append(body)
            return {"message": "Success.", "statuscode": 400, "result": []}

        mod.fetch_all_pages(api_user="u", api_key="k", http_post=fake_post,
                            page_size=5000, max_pages=2,
                            fiscal_years=["2026", "2025"])
        self.assertTrue(seen, "요청이 한 번도 안 나갔다")
        for body in seen:
            self.assertEqual(body["filters"]["FiscalYear"], ["2026", "2025"])


# ============================================================================
# 059_fda_inspection_stats_freshness.sql -- 신선도 2키 추가 계약(2026-08-12).
#
# ★이 아래의 단언은 "지금 코드가 무엇을 하는가"가 아니라 **"무엇이 옳은가"** 를 적는다.
#   이 파일은 이미 두 번 반대로 틀린 이력이 있다(FiscalYear 필터 누락을 정답으로 못 박은
#   assertEqual · 0-기반 페이징 픽스처가 0-기반 구현을 통과시킴). 그래서
#   - 크론은 "존재한다"가 아니라 **정각이 아니고 다른 워크플로와 겹치지 않는다**를,
#   - 스케줄 시맨틱은 "코드에 --apply 문자열이 있다"가 아니라 **실제로 셸을 실행해
#     argv 에 --apply 가 들어가고 --dry-run 이 들어가지 않는다**를 검사한다.
# ============================================================================

_BASH = shutil.which("bash")


def _function_code(path: Path) -> str:
    """마이그레이션 파일에서 fda_inspection_stats() 본문(주석 제외)만 꺼낸다."""
    sql = path.read_text(encoding="utf-8")
    start = sql.index("as $$") + len("as $$")
    end = sql.index("$$;", start)
    return _strip_sql_comments(sql[start:end])


# 059 가 058 에 **더한** 두 조각. 이 두 조각만 되돌리면 058 과 바이트 동일해야 한다.
_ADDED_BASE_PROJECTION = (
    "    select inspection_id, classification_code, fiscal_year, country_key, country_name,\n"
    "           inspection_end_date, ingested_at\n"
)
_ORIGINAL_BASE_PROJECTION = (
    "    select inspection_id, classification_code, fiscal_year, country_key, country_name\n"
)
_ADDED_SCOPE_KEYS = (
    "      'unmapped_country_count', (select count(*) from base where country_key = ''),\n"
    "      'last_ingested_date_kst',\n"
    "        (select (max(ingested_at) at time zone 'Asia/Seoul')::date from base),\n"
    "      'latest_inspection_end_date', (select max(inspection_end_date) from base)\n"
)
_ORIGINAL_SCOPE_TAIL = (
    "      'unmapped_country_count', (select count(*) from base where country_key = '')\n"
)

# 058 이 이미 내보내던 scope 키 -- 059 는 **하나도 지우거나 이름을 바꾸면 안 된다**
# (캐시된 구버전 trends.js 가 그 자리를 통째로 잃는다 -- 055 226~231행의 전례).
_INHERITED_SCOPE_KEYS = (
    "'source'", "'project_area'", "'product_type'", "'excluded_project_areas'",
    "'fiscal_year_min'", "'fiscal_year_max'", "'unmapped_country_count'",
)
_NEW_SCOPE_KEYS = ("'last_ingested_date_kst'", "'latest_inspection_end_date'")


class Migration059FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_MIGRATION_059_PATH.is_file(), f"missing {_MIGRATION_059_PATH}")
        self.sql = _MIGRATION_059_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _MIGRATION_059_PATH.read_bytes())

    def test_documents_004_009_pitfalls_not_applicable(self) -> None:
        self.assertIn("004/009 함정 해당 없음", self.sql)

    def test_no_plpgsql_do_blocks_or_declared_variables(self) -> None:
        self.assertNotIn("do $$", self.code.lower())
        self.assertNotIn("declare", self.code.lower())

    def test_declares_supersede_chain_from_058(self) -> None:
        self.assertIn("supersede 체인: fda_inspection_stats = 058", self.sql)

    def test_058_carries_a_back_note_pointing_here(self) -> None:
        """★supersede 당한 파일에 역방향 표식이 없으면 058 만 읽은 사람이 그것을 현행으로
        오독한다(054→056 에 그 표식이 없어 실제로 그렇게 읽힌다 -- 055 31~36행이 선례)."""
        self.assertIn("059_fda_inspection_stats_freshness.sql",
                      _MIGRATION_PATH.read_text(encoding="utf-8"))

    def test_does_not_touch_the_table_or_rls(self) -> None:
        """059 는 함수 하나만 갱신한다 -- 표 DDL/RLS 를 건드리면 적용 순서 위험이 생긴다."""
        for forbidden in ("create table", "alter table", "drop table",
                          "create policy", "row level security", "create index"):
            self.assertNotIn(forbidden, self.code.lower(), f"059 must not contain {forbidden!r}")

    def test_cites_measured_values_not_estimates(self) -> None:
        """헤더는 실측치를 인용해야 한다(054~058 양식). 추정치로 적으면 검증이 불가능하다."""
        for figure in ("6,417", "2026-08-11 23:59:11.887371+00", "2026-07-16"):
            self.assertIn(figure, self.sql)


class Migration059AdditiveTest(unittest.TestCase):
    """★핵심 계약: 059 는 058 의 함수 정의에 **두 조각만 더한** 것이어야 한다.

    두 조각을 되돌리면 058 과 바이트 동일 -- 이 단언이 깨진다는 것은 기존 키/집계가
    어딘가 바뀌었다는 뜻이고, 그건 캐시된 구버전 trends.js 가 화면을 잃는 사고다.
    (036 이 라이브에서 md5 로 증명한 "순수 가산"을 오프라인 텍스트 층에서 고정한다.)
    """

    def setUp(self) -> None:
        self.code058 = _function_code(_MIGRATION_PATH)
        self.code059 = _function_code(_MIGRATION_059_PATH)

    def test_reverting_the_two_additions_reproduces_058_byte_for_byte(self) -> None:
        self.assertEqual(self.code059.count(_ADDED_BASE_PROJECTION), 1)
        self.assertEqual(self.code059.count(_ADDED_SCOPE_KEYS), 1)
        reverted = self.code059.replace(
            _ADDED_BASE_PROJECTION, _ORIGINAL_BASE_PROJECTION, 1
        ).replace(_ADDED_SCOPE_KEYS, _ORIGINAL_SCOPE_TAIL, 1)
        self.assertEqual(reverted, self.code058,
                         "059 가 058 정의를 '추가만' 하지 않았다(기존 정의가 바뀌었다)")

    def test_every_inherited_scope_key_survives(self) -> None:
        for key in _INHERITED_SCOPE_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, self.code059)

    def test_totals_by_year_by_country_blocks_are_untouched(self) -> None:
        for block in ("'totals'", "'by_year'", "'by_country'"):
            tail058 = self.code058[self.code058.index(block):]
            tail059 = self.code059[self.code059.index(block):]
            with self.subTest(block=block):
                self.assertEqual(tail059, tail058)

    def test_signature_unchanged_no_new_parameter(self) -> None:
        """★파라미터를 하나라도 붙이면 새 오버로드가 생겨 (1) 무인자 호출이 PostgREST
        404 로 깨지고 (2) 새 함수가 PUBLIC EXECUTE 로 태어난다(058 의 revoke 를 물려받지
        못한다). 무인자 유지가 유일하게 안전한 경로다."""
        sql = _MIGRATION_059_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "create or replace function public.fda_inspection_stats()\n"
            "returns jsonb\nlanguage sql\nstable\nsecurity definer\n"
            "set search_path = public",
            sql,
        )
        self.assertNotIn("fda_inspection_stats(p_", sql)
        self.assertNotRegex(_strip_sql_comments(sql),
                            r"fda_inspection_stats\(\s*\w")

    def test_no_drop_function(self) -> None:
        """시그니처가 같으므로 옛 정의를 지울 것이 없다 -- drop 이 있다면 그 순간
        anon EXECUTE 권한과 pg_proc OID 가 함께 사라진다."""
        self.assertNotIn("drop function", self.code059.lower())


class Migration059FreshnessKeysTest(unittest.TestCase):
    """신선도 2키 자체의 계약 -- 이름·프레임·null 안전."""

    def setUp(self) -> None:
        self.sql = _MIGRATION_059_PATH.read_text(encoding="utf-8")
        self.code = _function_code(_MIGRATION_059_PATH)
        scope = self.code[self.code.index("'scope'"):self.code.index("'totals'")]
        self.new_block = scope[scope.index("'last_ingested_date_kst'"):]

    def test_both_new_keys_present(self) -> None:
        for key in _NEW_SCOPE_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, self.code)

    def test_the_two_keys_have_distinct_names_and_distinct_sources(self) -> None:
        """★두 값은 서로 다른 질문에 답한다(우리가 언제 받아왔나 vs 자료가 언제까지인가).
        한 키로 뭉치면 이 저장소가 반복해 겪은 '계기판 합산 오진'이 그대로 재현된다."""
        self.assertIn("max(ingested_at)", self.new_block)
        self.assertIn("max(inspection_end_date)", self.new_block)
        self.assertNotEqual(_NEW_SCOPE_KEYS[0], _NEW_SCOPE_KEYS[1])

    def test_ingested_key_pins_its_timezone_frame(self) -> None:
        """★timestamptz 를 그냥 jsonb 에 넣으면 문자열이 **세션 TimeZone GUC** 에 의존한다
        (PostgREST anon 요청의 GUC 는 우리가 통제하지 않는다). 명시 변환이어야 하고,
        어느 프레임인지가 키 이름에 드러나야 한다."""
        self.assertIn("at time zone 'Asia/Seoul'", self.new_block)
        self.assertTrue(_NEW_SCOPE_KEYS[0].strip("'").endswith("_kst"))

    def test_no_fabricated_fallback_when_the_table_is_empty(self) -> None:
        """★없는 날짜를 지어내지 않는다. 빈 집합의 max() 는 null 이고 null 로 나가야 한다 --
        coalesce(now())/current_date 로 메우면 화면이 '오늘 기준'이라고 거짓말한다."""
        for forbidden in ("now()", "current_date", "current_timestamp", "coalesce"):
            self.assertNotIn(forbidden, self.new_block.lower(),
                             f"신선도 키가 {forbidden!r} 로 값을 지어내고 있다")

    def test_no_threshold_judgement_on_the_server(self) -> None:
        """'어느 달까지 완전한가' 같은 판정을 서버가 하지 않는다 -- 임계는 반드시 낡는다
        (007/038/058 계약: 서버는 센다, 판단·나누기는 클라이언트가 한다)."""
        self.assertNotIn("interval", self.new_block.lower())
        self.assertNotIn(">=", self.new_block)

    def test_revoke_then_grant_reissued_in_that_order(self) -> None:
        """★재발행 자체는 멱등이라 무해하고, 058 미적용 DB 에 이 파일이 먼저 적용되면
        함수가 **PUBLIC EXECUTE 로 태어나는** 것을 막는다. 순서가 뒤집히면 방금 준 권한을
        되돌린다."""
        revoke = "revoke all on function public.fda_inspection_stats() from public;"
        grant = ("grant execute on function public.fda_inspection_stats() "
                 "to anon, authenticated;")
        self.assertIn(revoke, self.sql)
        self.assertIn(grant, self.sql)
        self.assertLess(self.sql.index(revoke), self.sql.index(grant))

    def test_ships_an_md5_equivalence_verification_block(self) -> None:
        """036 관례 -- 적용 전 md5 와 '신설 키만 제거한 적용 후 md5' 대조 절차를 파일이
        직접 들고 있어야 한다(사람이 라이브에서 순수 가산을 재확인할 수 있게)."""
        self.assertIn("md5(public.fda_inspection_stats()::text)", self.sql)
        self.assertIn("#- '{scope,last_ingested_date_kst}'", self.sql)
        self.assertIn("#- '{scope,latest_inspection_end_date}'", self.sql)


class Migration059PrerequisiteTest(unittest.TestCase):
    def test_058_exists_and_is_declared_as_the_prerequisite(self) -> None:
        self.assertTrue(_MIGRATION_PATH.is_file())
        self.assertIn("058_fda_inspections.sql",
                      _MIGRATION_059_PATH.read_text(encoding="utf-8"))


# ============================================================================
# grm-fda-inspections-backfill.yml -- 월 1회 자동 갱신(2026-08-12 신설).
# ============================================================================


def _workflow_run_block(step_id: str, path: Path = _WORKFLOW_PATH) -> str:
    """워크플로에서 특정 스텝의 run 본문을 그대로 꺼낸다(복사본을 두지 않는다).

    ★PyYAML 을 쓰지 않는다 -- **CI 테스트 환경에 PyYAML 이 없다**(requirements.txt 미포함).
    로컬에는 있어서 통과하고 CI 에서만 ImportError 로 터진다. 자체 파서를 쓰는 선례는
    tests/test_library_staging_decide_gate.py · tests/test_workflow_flag_resolution.py 이고,
    아래 Workflow059PyYamlParityTest 가 PyYAML 이 있는 환경에서 결과를 대조한다.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        anchor = next(i for i, ln in enumerate(lines) if ln.strip() == f"id: {step_id}")
        start = next(i for i in range(anchor, len(lines))
                     if lines[i].strip() in ("run: |", "run: |-"))
    except StopIteration:  # pragma: no cover - 구조가 바뀌면 즉시 실패시킨다
        raise AssertionError(f"{step_id} 스텝의 run 블록을 찾지 못했다") from None
    key_indent = len(lines[start]) - len(lines[start].lstrip())
    body: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip():
            body.append("")
            continue
        if len(line) - len(line.lstrip()) <= key_indent:
            break
        body.append(line)
    widths = [len(ln) - len(ln.lstrip()) for ln in body if ln.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(ln[cut:] if ln.strip() else "" for ln in body)


def _crons(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]", text, re.M)


class WorkflowScheduleShapeTest(unittest.TestCase):
    """크론 표현식 자체의 계약 -- 실행 없이도 고정한다."""

    def setUp(self) -> None:
        self.assertTrue(_WORKFLOW_PATH.is_file(), f"missing {_WORKFLOW_PATH}")
        self.text = _WORKFLOW_PATH.read_text(encoding="utf-8")
        self.crons = _crons(_WORKFLOW_PATH)

    def test_has_a_monthly_schedule_at_all(self) -> None:
        self.assertTrue(self.crons, "schedule 이 없다 -- 월간 자동 갱신이 안 붙었다")

    def test_every_cron_restricts_the_day_of_month(self) -> None:
        """★월 1회 갱신이므로 day-of-month 필드가 '*' 이면 안 된다(그러면 매일 돈다)."""
        for expr in self.crons:
            fields = expr.split()
            with self.subTest(cron=expr):
                self.assertEqual(len(fields), 5, "cron 필드는 5개여야 한다")
                self.assertNotEqual(fields[2], "*", "day-of-month 가 '*' 면 월 1회가 아니다")

    def test_no_cron_fires_on_the_hour(self) -> None:
        """★이 저장소의 크론은 정각 부하 피크에서 상시 지각한 이력이 있다
        (grm-findings-classification-audit.yml 17~20행에 근거가 적혀 있다)."""
        for expr in self.crons:
            with self.subTest(cron=expr):
                self.assertNotEqual(expr.split()[0], "0", "정각(분=0) 크론은 지각한다")

    def test_schedule_time_collides_with_no_other_workflow(self) -> None:
        """★같은 시각에 몰리면 러너 경합·지각이 커진다. (분, 시) 쌍이 이 저장소에서
        유일해야 한다."""
        mine = {tuple(e.split()[:2]) for e in self.crons}
        others = set()
        for path in sorted(_WORKFLOW_DIR.glob("*.yml")):
            if path.name == _WORKFLOW_PATH.name:
                continue
            for expr in _crons(path):
                others.add(tuple(expr.split()[:2]))
        self.assertTrue(others, "다른 워크플로 크론 스캔이 비었다 -- 파서가 낡았다")
        self.assertEqual(mine & others, set(),
                         f"다른 워크플로와 같은 시각에 예약됐다: {mine & others}")

    def test_kst_conversion_is_written_next_to_the_cron(self) -> None:
        """운영 기준은 KST 다 -- 환산을 적어두지 않으면 다음 사람이 UTC 를 KST 로 읽는다."""
        self.assertIn("KST", self.text.split("workflow_dispatch:")[0])

    def test_recovery_cron_exists_because_monthly_has_no_absorption(self) -> None:
        """★월간 크론은 지각/실패를 흡수할 기회가 없다 -- 한 번 실패하면 한 달이 빈다.
        정기 1줄 + 복구 1줄 구조여야 하고, 복구는 정기와 다른 날짜여야 한다."""
        self.assertGreaterEqual(len(self.crons), 2, "복구용 크론이 없다")
        doms = [e.split()[2] for e in self.crons]
        self.assertEqual(len(set(doms)), len(doms), "정기와 복구가 같은 날짜다")

    def test_idempotency_is_documented(self) -> None:
        """전량 재실행이 안전하다는 사실(merge-duplicates)이 파일에 적혀 있어야 한다 --
        적혀 있지 않으면 다음 사람이 '부분 재실행' 상태 저장을 만들려 든다."""
        self.assertIn("merge-duplicates", self.text)

    def test_concurrency_group_unchanged(self) -> None:
        self.assertIn("group: fda-inspections-backfill", self.text)

    def test_issue_permission_present_for_failure_alerting(self) -> None:
        self.assertRegex(self.text, r"(?m)^\s*issues:\s*write")

    def test_failure_issue_title_is_defined_exactly_once(self) -> None:
        """★제목을 양쪽에 따로 적으면 한쪽만 고쳐져 복구 모드가 조용히 안 도는 상태가
        된다(grm-library-staging.yml 30~33행의 교훈)."""
        self.assertEqual(self.text.count("FAILURE_ISSUE_TITLE: "), 1)
        self.assertIn("$FAILURE_ISSUE_TITLE", self.text)            # decide 셸
        self.assertIn("process.env.FAILURE_ISSUE_TITLE", self.text)  # github-script

    def test_no_dispatch_inputs_interpolated_into_shell_bodies(self) -> None:
        """★두 가지를 한꺼번에 막는다: (1) 저장소 규약 W2(셸 본문 직접 삽입 금지),
        (2) 스케줄에서 빈 문자열이 그대로 인자로 흘러 argparse 를 죽이는 것."""
        for step_id in ("check", "backfill"):
            with self.subTest(step=step_id):
                self.assertNotIn("${{", _workflow_run_block(step_id))


class CollectorCliRejectsEmptyNumericArgsTest(unittest.TestCase):
    """★워크플로 테스트가 지키려는 것이 실제 위험인지 먼저 증명한다.

    빈 문자열을 --max-pages 로 넘기면 argparse 가 exit 2 로 죽는다. 이 사실이 참이라야
    "스케줄 경로가 빈 인자를 넘기지 않는다"는 아래 단언이 의미를 갖는다(픽스처가 코드와
    같은 방향으로 틀려 결함을 고정하는 것을 막는 자기검사).
    """

    def test_empty_string_max_pages_is_a_hard_parse_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            mod.build_arg_parser().parse_args(["--dry-run", "--max-pages", ""])
        self.assertEqual(ctx.exception.code, 2)

    def test_empty_string_chunk_size_is_a_hard_parse_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            mod.build_arg_parser().parse_args(["--dry-run", "--chunk-size", ""])
        self.assertEqual(ctx.exception.code, 2)

    def test_defaults_match_what_the_schedule_path_relies_on(self) -> None:
        """★스케줄 경로는 상한을 **넘기지 않고** 수집기 기본값에 맡긴다. 그 기본값이
        dispatch 기본값(10 / 1000)과 같다는 사실이 이 선택의 전제다 -- 어긋나면 여기서
        걸린다(워크플로에 같은 숫자를 또 적는 대신)."""
        args = mod.build_arg_parser().parse_args([])
        self.assertEqual(args.max_pages, 10)
        self.assertEqual(args.chunk_size, 1000)
        text = _WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("default: '10'", text)
        self.assertIn("default: '1000'", text)


@unittest.skipUnless(_BASH, "bash 필요")
class WorkflowScheduleSemanticsTest(unittest.TestCase):
    """★★임무의 핵심 계약: **스케줄 실행이 dry-run 으로 떨어지지 않는다.**

    문자열 검사가 아니라 YAML 안의 셸을 꺼내 **실제로 실행**해 argv 를 본다. 스케줄
    이벤트에서 dispatch 입력은 전부 빈 문자열이므로, 그 상태 그대로 넣고 돌린다.
    """

    def _run_step(self, *, event: str, dry_run: str = "", max_pages: str = "",
                  chunk_size: str = "") -> tuple[list[str], dict[str, str], int]:
        script = _workflow_run_block("backfill")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binp = root / "bin"
            binp.mkdir()
            argv_file = root / "argv.txt"
            # python 스텁 -- 인자를 한 줄에 하나씩 기록한다(빈 인자는 빈 줄로 남아
            # 그대로 검출된다).
            (binp / "python").write_text(
                "#!/bin/sh\n"
                ': > "$ARGV_FILE"\n'
                'for a in "$@"; do printf \'%s\\n\' "$a" >> "$ARGV_FILE"; done\n',
                encoding="utf-8")
            (binp / "python").chmod(0o755)
            out = root / "out"
            out.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "PATH": f"{binp}{os.pathsep}{os.environ.get('PATH', '')}",
                "GITHUB_OUTPUT": str(out),
                "ARGV_FILE": str(argv_file),
                "GRM_EVENT_NAME": event,
                "IN_DRY_RUN": dry_run,
                "IN_MAX_PAGES": max_pages,
                "IN_CHUNK_SIZE": chunk_size,
            }
            proc = subprocess.run([_BASH, "-c", script], env=env, check=False,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            argv = (argv_file.read_text(encoding="utf-8").split("\n")[:-1]
                    if argv_file.exists() else [])
            outputs = dict(
                line.split("=", 1)
                for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
        return argv, outputs, proc.returncode

    def test_the_stub_actually_ran(self) -> None:
        """자기검사 -- 스텁이 안 불렸는데 아래 단언들이 통과하면 가드가 무력하다."""
        argv, _outputs, code = self._run_step(event="schedule")
        self.assertEqual(code, 0)
        self.assertEqual(argv[0], "collect_fda_inspections.py")

    def test_schedule_applies_and_is_never_a_dry_run(self) -> None:
        """★★스케줄은 실제 적재여야 한다. dispatch 의 dry_run 기본값(true)이 남아
        있어도, 빈 문자열이 어느 쪽으로 떨어지든, 여기서는 --apply 여야 한다."""
        argv, outputs, code = self._run_step(event="schedule")
        self.assertEqual(code, 0)
        self.assertIn("--apply", argv)
        self.assertNotIn("--dry-run", argv)
        self.assertEqual(outputs.get("mode"), "apply")

    def test_schedule_decision_does_not_depend_on_the_dispatch_input(self) -> None:
        """★★이 단언이 "명시적으로 갈랐는가"를 실제로 가르는 유일한 검사다.

        스케줄에서 inputs 가 빈 문자열이라는 사실만 놓고 보면, 분기를 아예 지워도
        `[ "" = "true" ]` 가 거짓이라 else 로 빠져 --apply 가 된다 -- 즉 "빈 문자열이
        우연히 옳은 쪽으로 떨어진" 상태와 "이벤트로 명시 판정한" 상태가 구별되지 않는다
        (2026-08-12 음성 검사에서 실제로 그랬다: 분기를 지워도 다른 단언은 전부 초록).
        그래서 **판정이 dispatch 입력값과 무관한지**를 직접 잰다 -- IN_DRY_RUN 이 무엇이든
        스케줄은 적재여야 한다. 분기가 사라지면 여기서 즉시 빨개진다.
        """
        for injected in ("true", "false", ""):
            with self.subTest(IN_DRY_RUN=injected):
                argv, outputs, _code = self._run_step(event="schedule", dry_run=injected)
                self.assertIn("--apply", argv)
                self.assertNotIn("--dry-run", argv)
                self.assertEqual(outputs.get("mode"), "apply")

    def test_schedule_ignores_dispatch_limits_entirely(self) -> None:
        """같은 이유로 상한도 이벤트 판정 소관이다 -- 스케줄 경로는 dispatch 입력을
        읽지 않고 수집기 기본값에 맡긴다."""
        argv, _outputs, _code = self._run_step(
            event="schedule", max_pages="9999", chunk_size="7")
        self.assertNotIn("--max-pages", argv)
        self.assertNotIn("--chunk-size", argv)
        self.assertNotIn("9999", argv)

    def test_schedule_passes_no_empty_arguments(self) -> None:
        """★빈 문자열 인자 하나면 argparse 가 exit 2 로 죽는다(위 CollectorCli... 참고).
        스케줄 경로는 상한 인자를 아예 넘기지 않아야 한다."""
        argv, _outputs, _code = self._run_step(event="schedule")
        self.assertNotIn("", argv, f"빈 문자열 인자가 넘어갔다: {argv}")
        self.assertNotIn("--max-pages", argv)
        self.assertNotIn("--chunk-size", argv)

    def test_manual_dispatch_keeps_dry_run_default(self) -> None:
        """수동 실행은 종전대로 dry_run 기본 true 를 유지한다(사람 게이트)."""
        argv, outputs, code = self._run_step(
            event="workflow_dispatch", dry_run="true", max_pages="10", chunk_size="1000")
        self.assertEqual(code, 0)
        self.assertIn("--dry-run", argv)
        self.assertNotIn("--apply", argv)
        self.assertEqual(outputs.get("mode"), "dry-run")
        self.assertEqual(argv[argv.index("--max-pages") + 1], "10")
        self.assertEqual(argv[argv.index("--chunk-size") + 1], "1000")

    def test_manual_dispatch_with_dry_run_false_applies(self) -> None:
        argv, outputs, _code = self._run_step(
            event="workflow_dispatch", dry_run="false", max_pages="10", chunk_size="1000")
        self.assertIn("--apply", argv)
        self.assertNotIn("--dry-run", argv)
        self.assertEqual(outputs.get("mode"), "apply")

    def test_manual_dispatch_with_cleared_limits_sends_no_empty_arguments(self) -> None:
        """사람이 입력칸을 비운 채 돌려도 argparse 를 죽이면 안 된다."""
        argv, _outputs, code = self._run_step(
            event="workflow_dispatch", dry_run="true", max_pages="", chunk_size="")
        self.assertEqual(code, 0)
        self.assertNotIn("", argv)
        self.assertNotIn("--max-pages", argv)
        self.assertNotIn("--chunk-size", argv)


@unittest.skipUnless(_BASH, "bash 필요")
class WorkflowDecideGateTest(unittest.TestCase):
    """복구 게이트 -- 정기일은 무조건 통과, 복구일은 실패 이슈가 열렸을 때만 통과."""

    #: 워크플로 env 의 REGULAR_CRON 과 같아야 한다(아래 전용 테스트가 대조한다).
    REGULAR_CRON = "47 15 1 * *"
    RECOVERY_CRON = "47 15 8,15,22 * *"

    def _decide(self, *, event: str = "schedule", schedule: str | None = None,
                today: str = "2026-09-08", open_issues: int = 0,
                gh_fails: bool = False, last_ingested: str | None = "2026-09-02",
                rpc_fails: bool = False) -> tuple[str, str]:
        """decide 스텝의 셸을 실제 bash 로 돌린다.

        ★`dom` 인자를 없앴다. 종전 하네스는 `date -u +%d` 를 스텁해 "실행 시각이 며칠인가"를
        흉내 냈는데, 그건 **구현이 하던 일**이지 **옳은 일**이 아니었다. 크론이 지각하면
        그 날짜가 밀려 정기 실행을 통째로 놓친다(적대적 검증 HIGH). 이제 판정은
        `github.event.schedule`(발화한 크론 문자열) 로만 하므로 하네스도 그것을 준다.
        """
        script = _workflow_run_block("check")
        if schedule is None:
            schedule = self.REGULAR_CRON if event == "schedule" else ""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binp = root / "bin"
            binp.mkdir()
            # date 는 이제 "이번 달 1일"을 만드는 데만 쓴다(결손 판정 기준선).
            (binp / "date").write_text(
                "#!/bin/sh\n"
                'printf \'%s\\n\' "$FAKE_MONTH_START"\n', encoding="utf-8")
            (binp / "gh").write_text(
                "#!/bin/sh\n"
                '[ "$FAKE_GH_FAILS" = "1" ] && { echo "gh: HTTP 502" >&2; exit 1; }\n'
                'printf \'%s\\n\' "$FAKE_OPEN"\n', encoding="utf-8")
            # curl 스텁 -- RPC 응답(scope.last_ingested_date_kst)을 흉내 낸다.
            (binp / "curl").write_text(
                "#!/bin/sh\n"
                '[ "$FAKE_RPC_FAILS" = "1" ] && { echo "curl: (28) timeout" >&2; exit 28; }\n'
                '[ -z "$FAKE_LAST" ] && { printf \'{"scope":{}}\\n\'; exit 0; }\n'
                'printf \'{"scope":{"last_ingested_date_kst":"%s"}}\\n\' "$FAKE_LAST"\n',
                encoding="utf-8")
            for name in ("date", "gh", "curl"):
                (binp / name).chmod(0o755)
            out = root / "out"
            out.write_text("", encoding="utf-8")
            month_start = today[:8] + "01"
            env = {
                **os.environ,
                "PATH": f"{binp}{os.pathsep}{os.environ.get('PATH', '')}",
                "GITHUB_OUTPUT": str(out),
                "GRM_EVENT_NAME": event,
                "GRM_SCHEDULE": schedule,
                "GRM_REPO": "owner/repo",
                "FAILURE_ISSUE_TITLE": "GRM FDA 실사등급 월간 갱신 실패",
                "REGULAR_CRON": self.REGULAR_CRON,
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "svc-key",
                "FAKE_MONTH_START": month_start,
                "FAKE_OPEN": str(open_issues),
                "FAKE_GH_FAILS": "1" if gh_fails else "0",
                "FAKE_LAST": last_ingested or "",
                "FAKE_RPC_FAILS": "1" if rpc_fails else "0",
            }
            subprocess.run([_BASH, "-c", script], env=env, check=False,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
            parsed = dict(
                line.split("=", 1)
                for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
        return parsed.get("run", ""), parsed.get("reason", "")

    def test_manual_dispatch_always_runs(self) -> None:
        run, reason = self._decide(event="workflow_dispatch")
        self.assertEqual(run, "true")
        self.assertIn("수동", reason)

    def test_regular_cron_runs_regardless_of_issue_state(self) -> None:
        run, reason = self._decide(schedule=self.REGULAR_CRON, open_issues=0)
        self.assertEqual(run, "true")
        self.assertIn("월간 정기", reason)

    def test_regular_cron_runs_even_when_hours_late(self) -> None:
        """★★회귀 가드(적대적 검증 HIGH) -- 정기 크론이 지각해도 정기로 판정돼야 한다.

        종전 구현은 `date -u +%d` 로 실행 시각에서 날짜를 되짚었다. 정기 크론이
        `47 15 1 * *` 이므로 **8시간 13분** 넘게 밀리면 UTC 날짜가 02 로 넘어가 정기
        분기를 놓치고, 실패 이슈도 없으니 복구도 건너뛰어 **한 달이 통째로 조용히
        빈다**(실행은 전부 초록). 이 저장소는 grm-library-staging.yml 에서 같은
        함정을 이미 겪었다. 아래 today 는 '2일로 넘어간 뒤'를 뜻한다.
        """
        run, reason = self._decide(schedule=self.REGULAR_CRON, today="2026-09-02",
                                   open_issues=0, last_ingested="2026-08-02")
        self.assertEqual(run, "true", "지각한 정기 크론이 건너뛰어졌다 -- 한 달 결손")
        self.assertIn("월간 정기", reason)

    def test_recovery_cron_skips_when_healthy(self) -> None:
        """★평상시 복구일에 적재까지 가면 월 4회 도는 셈이 된다 -- 판정만 하고 끝나야 한다."""
        run, _reason = self._decide(schedule=self.RECOVERY_CRON, open_issues=0,
                                    today="2026-09-08", last_ingested="2026-09-02")
        self.assertEqual(run, "false")

    def test_recovery_cron_runs_while_the_failure_issue_is_open(self) -> None:
        run, reason = self._decide(schedule=self.RECOVERY_CRON, open_issues=1)
        self.assertEqual(run, "true")
        self.assertIn("복구", reason)

    def test_recovery_cron_runs_when_the_table_is_from_a_previous_month(self) -> None:
        """★★회귀 가드(적대적 검증 HIGH) -- '실행이 아예 없었다'를 잡는 유일한 신호.

        실패 이슈만 보면 **정기 실행이 뜨지 않은 달**을 영원히 못 잡는다(실패한 적이
        없으니 이슈가 없다). grm-library-staging.yml 이 세 번째 분기를 추가한 이유가
        정확히 이것이고, 그 파일도 데이터에서 마지막 갱신일을 읽어 판정한다.
        """
        run, reason = self._decide(schedule=self.RECOVERY_CRON, open_issues=0,
                                   today="2026-09-15", last_ingested="2026-08-02")
        self.assertEqual(run, "true", "표가 지난달 것인데 복구가 안 돌았다")
        self.assertIn("이전", reason)

    def test_unreadable_issue_list_falls_through_to_the_data_check(self) -> None:
        """★gh 조회 실패가 데이터 판정까지 막으면 안 된다 -- 두 신호는 독립이다."""
        run, reason = self._decide(schedule=self.RECOVERY_CRON, gh_fails=True,
                                   today="2026-09-08", last_ingested="2026-08-02")
        self.assertEqual(run, "true")
        self.assertIn("이전", reason)

    def test_unreadable_rpc_skips_loudly_instead_of_guessing(self) -> None:
        """RPC 를 못 읽으면(059 미적용·네트워크) 없는 값으로 단정하지 않고 건너뛴다."""
        run, reason = self._decide(schedule=self.RECOVERY_CRON, open_issues=0,
                                   rpc_fails=True)
        self.assertEqual(run, "false")
        self.assertIn("판정 불가", reason)

    def test_missing_freshness_key_skips_instead_of_crashing(self) -> None:
        """059 미적용 라이브는 키가 아예 없다 -- 빈 문자열로 떨어져 건너뛰어야 한다."""
        run, reason = self._decide(schedule=self.RECOVERY_CRON, open_issues=0,
                                   last_ingested=None)
        self.assertEqual(run, "false")
        self.assertIn("판정 불가", reason)

    def test_regular_cron_does_not_depend_on_github_or_supabase(self) -> None:
        """정기일 판정은 gh 도 curl 도 부르지 않아야 한다 -- 부르면 외부 장애가
        월간 갱신 자체를 막는다."""
        run, reason = self._decide(schedule=self.REGULAR_CRON, gh_fails=True,
                                   rpc_fails=True)
        self.assertEqual(run, "true")
        self.assertIn("월간 정기", reason)

    def test_gate_reads_the_fired_cron_not_the_wall_clock_day(self) -> None:
        """★구현이 다시 `date -u +%d` 로 돌아가지 못하게 못 박는다.

        ★주석은 걷어내고 본다 -- 헤더가 "종전에는 `date -u +%d` 였다"고 **설명**하는
        문장까지 위반으로 잡으면, 가드를 통과시키려고 그 설명을 지우게 된다(왜 이렇게
        됐는지가 사라진다). 검사 대상은 실행되는 줄뿐이다.
        """
        script = _workflow_run_block("check")
        code = "\n".join(ln for ln in script.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertIn("$GRM_SCHEDULE", code)
        self.assertIn("$REGULAR_CRON", code)
        self.assertNotIn("+%d", code,
                         "실행 시각에서 날짜를 되짚으면 지각한 크론이 조용히 건너뛰어진다")
        # 결손 판정선(이번 달 1일)을 만드는 용도의 date 는 남아 있어야 한다.
        self.assertIn("+%Y-%m-01", code)

    def test_regular_cron_constant_matches_the_schedule_block(self) -> None:
        """env.REGULAR_CRON 과 schedule: 첫 항목이 어긋나면 정기 실행이 통째로
        복구 분기로 떨어진다 -- YAML 안의 두 문자열을 직접 대조한다."""
        text = _WORKFLOW_PATH.read_text(encoding="utf-8")
        crons = re.findall(r"-\s*cron:\s*'([^']+)'", text)
        self.assertTrue(crons, "cron 항목을 찾지 못했다")
        self.assertEqual(crons[0], self.REGULAR_CRON)
        m = re.search(r"REGULAR_CRON:\s*'([^']+)'", text)
        self.assertIsNotNone(m, "env.REGULAR_CRON 이 없다")
        self.assertEqual(m.group(1), crons[0],
                         "env.REGULAR_CRON 과 schedule: 첫 크론이 다르다")

    def test_gate_has_no_external_jq_dependency(self) -> None:
        script = _workflow_run_block("check")
        self.assertIn("--jq", script)
        self.assertNotIn("| jq", script)


class WorkflowGuardHygieneTest(unittest.TestCase):
    """가드 자신이 조용히 꺼지지 않는지 검사한다."""

    def test_no_module_level_pyyaml_dependency(self) -> None:
        """★CI 에는 PyYAML 이 없다. 모듈 최상단에서 import 하면 **CI 에서만** 터진다."""
        src = Path(__file__).read_text(encoding="utf-8")
        top_level = [ln for ln in src.splitlines()
                     if re.match(r"^(import yaml|from yaml\b)", ln)]
        self.assertEqual(top_level, [], "모듈 최상단 PyYAML import 금지")

    def test_shell_execution_suites_are_not_silently_skipped_on_linux(self) -> None:
        """★skip 은 초록으로 보인다. CI(ubuntu)에서 셸 실행 스위트가 통째로 건너뛰어지면
        스케줄 시맨틱 커버리지가 0인데 아무도 모른다."""
        if os.name != "posix":
            self.skipTest("POSIX 아님 -- CI(ubuntu)에서 강제된다")
        self.assertTrue(_BASH, "리눅스인데 bash 를 못 찾았다")


class Workflow059PyYamlParityTest(unittest.TestCase):
    """자체 파서가 PyYAML 과 같은 결과를 내는지 -- PyYAML 이 있는 환경에서만 돈다.

    CI 는 PyYAML 이 없어 자체 파서 단독으로 검사한다. 그래서 **파서가 틀리면 위 가드
    전체가 조용히 무력해진다**. 개발 환경(로컬)에는 PyYAML 이 있으므로 여기서 붙잡는다.
    """

    def setUp(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML 없음(CI) -- 자체 파서 단독 동작")

    def _data(self):
        import yaml
        return yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_cron_list_matches_pyyaml(self) -> None:
        data = self._data()
        # PyYAML 은 YAML 1.1 이라 `on:` 키를 boolean True 로 읽는다(알려진 함정).
        schedule = (data.get("on") or data.get(True))["schedule"]
        self.assertEqual(_crons(_WORKFLOW_PATH), [e["cron"] for e in schedule])

    def test_run_blocks_match_pyyaml(self) -> None:
        data = self._data()
        steps = [s for job in data["jobs"].values() for s in job["steps"]]
        for step_id in ("check", "backfill"):
            step = next(s for s in steps if s.get("id") == step_id)
            with self.subTest(step=step_id):
                self.assertEqual(_workflow_run_block(step_id).strip(),
                                 str(step["run"]).strip())


if __name__ == "__main__":
    unittest.main()
