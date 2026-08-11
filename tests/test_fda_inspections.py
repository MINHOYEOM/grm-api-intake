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
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_inspections as mod  # noqa: E402  -- collector under test, added alongside 058

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_MIGRATION_PATH = _MIGRATIONS_DIR / "058_fda_inspections.sql"
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
    stands in for the live DDAPI paginated POST endpoint."""

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
        page = self.all_rows[start:start + rows]
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
        self.assertEqual([c["start"] for c in http.calls], [0, 2, 4])

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
        http = _FakePagedHttpPost(rows, page_error_on_start={2: "boom"})
        result = mod.fetch_all_pages(api_user="u", api_key="k", http_post=http,
                                     page_size=2, max_pages=10)
        self.assertIn("page_fetch_failed", result.error)
        self.assertEqual(len(result.rows), 2)   # first page's rows are kept, not discarded

    def test_error_response_shaped_as_list_stops_pagination(self) -> None:
        rows = [{"InspectionID": str(i)} for i in range(5)]
        http = _FakePagedHttpPost(rows, page_error_on_start={0: "invalid_filters"},
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


if __name__ == "__main__":
    unittest.main()
