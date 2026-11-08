#!/usr/bin/env python3
"""collect_fda_citations.py + 133_fda_inspection_citations.sql + grm-fda-citations.yml —
오프라인 계약 테스트(네트워크·DB 없음).

가짜 DDAPI 는 실측 동작을 따른다(08-12 교훈): start 는 1부터(0 이면 400 을 던진다),
rows 상한 5000(초과면 415 를 던진다), totalrecordcount 를 준다.
"""

from __future__ import annotations

import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_citations as mod  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_MIGRATIONS = _REPO / "web" / "migrations"
_MIGRATION = _MIGRATIONS / "133_fda_inspection_citations.sql"
_WORKFLOW = _REPO / ".github" / "workflows" / "grm-fda-citations.yml"

FY = ["2026", "2025"]
AREAS = ["Drugs", "Biologics"]


def _row(i: int, *, area: str = "Drugs", inspection: int | None = None,
         country: str = "United States", cfr: str = "21 CFR 211.192", fy: str = "2026"):
    return {
        "InspectionID": inspection if inspection is not None else 1000 + i // 3,
        "CitationID": 50000 + i,
        "FEINumber": 3000000000 + i // 3,
        "LegalName": f"Firm {i // 3}",
        "City": "X", "State": None, "StateCode": None, "ZipCode": None,
        "CountryCode": None, "CountryName": country,
        "InspectionEndDate": "2026-03-04", "FiscalYear": fy,
        "ProgramArea": area, "ActCFRNumber": cfr,
        "ShortDescription": "Investigations of discrepancies, failures",
        "LongDescription": "There is a failure to thoroughly review ...",
        "FirmProfile": "https://datadashboard.fda.gov/oii/fd/fdprofile.htm?FEIs=1",
    }


class FakeDdapi:
    """실측 API 를 흉내 낸다 — 1부터 세는 start, 5000 상한, totalrecordcount."""

    def __init__(self, rows, *, total_override=None, list_error_on_start=None):
        self.rows = rows
        self.total_override = total_override
        self.list_error_on_start = list_error_on_start
        self.calls: list[dict] = []
        self.headers: list[dict] = []

    def __call__(self, url, *, headers, body, timeout=None, retries=None):
        self.calls.append(body)
        self.headers.append(headers)
        start, rows = body["start"], body["rows"]
        if not isinstance(start, int) or start < 1:
            raise RuntimeError("HTTP 400: start parameter must be numeric and greater than 0")
        if rows > 5000:
            raise RuntimeError("statuscode 415: rows must be <= 5000")
        if start == self.list_error_on_start:
            return [{"statuscode": 413, "message": "invalid_filters"}]
        page = self.rows[start - 1:start - 1 + rows]
        out = {"statuscode": 400, "message": "Success.", "resultcount": len(page), "result": page}
        if body.get("returntotalcount"):
            out["totalrecordcount"] = (len(self.rows) if self.total_override is None
                                       else self.total_override)
        return out


def _poster():
    calls = []

    def poster(base_url, service_key, table, rows, on_conflict, *, resolution="ignore-duplicates"):
        calls.append(dict(table=table, rows=list(rows), on_conflict=on_conflict,
                          resolution=resolution))
        return 201, list(rows), ""

    poster.calls = calls
    return poster


def _no_link(base, key):
    return None, "no_supabase_credentials"


def _run(rows, **kw):
    fake = kw.pop("fake", None) or FakeDdapi(rows)
    poster = kw.pop("poster", None) or _poster()
    kw.setdefault("link_loader", _no_link)
    kw.setdefault("dry_run", True)
    report, code = mod.run(base_url="https://x.supabase.co", service_key="svc",
                           api_user="u@example.com", api_key="k", fiscal_years=FY,
                           program_areas=AREAS, http_post=fake, poster=poster, **kw)
    return report, code, fake, poster


def _mixed(n_drugs=12, n_bio=6):
    return ([_row(i) for i in range(n_drugs)]
            + [_row(100 + i, area="Biologics") for i in range(n_bio)])


# ─────────────────────────────────────────────────────────────────────────────


class RequestBodyTest(unittest.TestCase):
    def test_fiscal_year_scope_filter_is_always_present(self):
        body = mod.build_request_body(1, 5000, FY, AREAS)
        self.assertEqual(body["filters"]["FiscalYear"], FY)
        self.assertEqual(body["filters"]["ProgramArea"], AREAS)

    def test_empty_fiscal_years_is_refused(self):
        with self.assertRaises(ValueError):
            mod.build_request_body(1, 5000, [], AREAS)

    def test_no_classification_only_fields_in_filters_or_columns(self):
        body = mod.build_request_body(1, 5000, FY, AREAS)
        for absent in ("ProductType", "ProjectArea", "InspectionEndDate"):
            self.assertNotIn(absent, body["filters"])
        for absent in ("ProductType", "ProjectArea", "ClassificationCode"):
            self.assertNotIn(absent, body["columns"])

    def test_requests_total_count_and_server_default_sort(self):
        body = mod.build_request_body(1, 5000, FY, AREAS)
        self.assertIs(body["returntotalcount"], True)
        self.assertEqual((body["sort"], body["sortorder"]), ("", ""))

    def test_columns_cover_expected_fields(self):
        for col in ("InspectionID", "CitationID", "FEINumber", "LegalName", "InspectionEndDate",
                    "FiscalYear", "ProgramArea", "ActCFRNumber", "ShortDescription",
                    "LongDescription", "CountryName"):
            self.assertIn(col, mod.DDAPI_COLUMNS)

    def test_endpoint_is_citations(self):
        self.assertTrue(mod.DDAPI_URL.endswith("/v1/inspections_citations"))


class PagingTest(unittest.TestCase):
    def test_first_start_is_one_and_pages_step_by_page_size(self):
        rows = [_row(i) for i in range(11)]
        fake = FakeDdapi(rows)
        res = mod.fetch_all_pages(api_user="u", api_key="k", http_post=fake, fiscal_years=FY,
                                  program_areas=AREAS, page_size=5)
        self.assertEqual([c["start"] for c in fake.calls], [1, 6, 11])
        self.assertEqual(len(res.rows), 11)
        self.assertEqual([r["CitationID"] for r in res.rows],
                         [r["CitationID"] for r in rows])   # 겹침 0 · 빠짐 0
        self.assertEqual(res.server_total, 11)

    def test_fake_rejects_zero_start_like_live_api(self):
        with self.assertRaises(RuntimeError):
            FakeDdapi([])(None, headers={}, body={"start": 0, "rows": 1})

    def test_fake_rejects_rows_over_5000_like_live_api(self):
        with self.assertRaises(RuntimeError):
            FakeDdapi([])(None, headers={}, body={"start": 1, "rows": 5001})

    def test_default_page_size_is_the_5000_cap(self):
        self.assertEqual(mod.PAGE_SIZE, 5000)

    def test_exact_multiple_needs_one_empty_page_then_stops(self):
        fake = FakeDdapi([_row(i) for i in range(10)])
        res = mod.fetch_all_pages(api_user="u", api_key="k", http_post=fake, fiscal_years=FY,
                                  program_areas=AREAS, page_size=5)
        self.assertEqual([c["start"] for c in fake.calls], [1, 6, 11])
        self.assertFalse(res.truncated)

    def test_full_pages_until_max_pages_is_truncated_and_exit_2(self):
        report, code, *_ = _run([_row(i) for i in range(20)], page_size=5, max_pages=2)
        self.assertTrue(report.truncated)
        self.assertEqual(code, 2)

    def test_auth_goes_in_headers(self):
        fake = FakeDdapi([_row(0)])
        mod.fetch_all_pages(api_user="u@x", api_key="SECRET", http_post=fake,
                            fiscal_years=FY, program_areas=AREAS)
        self.assertEqual(fake.headers[0]["Authorization-User"], "u@x")
        self.assertEqual(fake.headers[0]["Authorization-Key"], "SECRET")
        self.assertNotIn("SECRET", repr(fake.calls))

    def test_list_error_response_exit_2(self):
        fake = FakeDdapi(_mixed(), list_error_on_start=1)
        report, code, *_ = _run([], fake=fake)
        self.assertEqual(code, 2)
        self.assertTrue(report.errors[0].startswith("fetch_failed:"))

    def test_server_total_mismatch_is_loud(self):
        fake = FakeDdapi(_mixed(), total_override=999)
        report, code, _, poster = _run([], fake=fake, dry_run=False)
        self.assertEqual(code, 2)
        self.assertIn("server_total_mismatch", report.errors[0])
        self.assertEqual(poster.calls, [])


class ScopeTest(unittest.TestCase):
    def test_partial_match_leakage_is_excluded_and_named(self):
        rows = _mixed() + [_row(300, area="Drugs and Biologics Misc"), _row(301, area="Devices")]
        report, code, *_ = _run(rows)
        self.assertEqual(code, 0)
        self.assertEqual(report.in_scope, 18)
        self.assertEqual(report.excluded_by_program_area,
                         {"Drugs and Biologics Misc": 1, "Devices": 1})

    def test_requested_area_with_zero_rows_is_loud(self):
        report, code, *_ = _run([_row(i) for i in range(5)])   # Biologics 0
        self.assertEqual(code, 2)
        self.assertIn("zero_rows_for_program_area", report.errors[-1])

    def test_all_wrong_labels_is_loud(self):
        report, code, *_ = _run([_row(i, area="Human Drugs") for i in range(5)])
        self.assertEqual(code, 2)
        self.assertEqual(report.excluded_by_program_area, {"Human Drugs": 5})

    def test_program_area_defaults_match_migration_check(self):
        sql = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("check (program_area in ('Drugs', 'Biologics'))", sql)
        self.assertEqual(mod.DEFAULT_PROGRAM_AREAS, ("Drugs", "Biologics"))


class NormalizeTest(unittest.TestCase):
    def test_payload_shape(self):
        p, err = mod.normalize_row(_row(0), AREAS)
        self.assertEqual(err, "")
        self.assertEqual(p["inspection_id"], "1000")
        self.assertEqual(p["citation_id"], "50000")
        self.assertEqual(p["state"], "")       # null -> ''
        self.assertEqual(p["fiscal_year"], 2026)
        self.assertNotIn("country_key", p)     # GENERATED

    def test_float_ids_match_classification_text_ids(self):
        r = _row(0)
        r["InspectionID"] = 1251303.0
        self.assertEqual(mod.normalize_row(r, AREAS)[0]["inspection_id"], "1251303")

    def test_rejections(self):
        r = _row(0); r["CitationID"] = None
        self.assertEqual(mod.normalize_row(r, AREAS)[1], "missing_citation_id")
        r = _row(0); r["FiscalYear"] = "FY26"
        self.assertTrue(mod.normalize_row(r, AREAS)[1].startswith("unparseable_fiscal_year"))

    def test_payload_columns_exist_in_migration(self):
        sql = _MIGRATION.read_text(encoding="utf-8")
        p, _ = mod.normalize_row(_row(0), AREAS)
        for col in p:
            self.assertRegex(sql, rf"\n\s+{col}\s+(text|int|date)", col)


class LinkAndSummaryTest(unittest.TestCase):
    def test_summary_counts_by_area_fy_korea_and_top_cfr(self):
        rows = ([_row(i) for i in range(6)]
                + [_row(10 + i, country="Korea (the Republic of)", cfr="21 CFR 211.22")
                   for i in range(3)]
                + [_row(20, fy="2025")]
                + [_row(100 + i, area="Biologics", cfr="21 CFR 600.14") for i in range(2)])
        report, code, *_ = _run(rows)
        self.assertEqual(code, 0)
        d = report.summary["Drugs"]
        self.assertEqual(d["total"], 10)
        self.assertEqual(d["by_fiscal_year"], {"2025": 1, "2026": 9})
        self.assertEqual(d["korea"], 3)
        self.assertEqual(d["top_cfr"][0], ["21 CFR 211.192", 7])
        self.assertLessEqual(len(d["top_cfr"]), 20)
        self.assertEqual(report.summary["Biologics"]["total"], 2)
        self.assertEqual(report.summary["_korea_like_country_names"],
                         {"Korea (the Republic of)": 3})

    def test_linked_to_classifications_by_inspection_id(self):
        rows = _mixed()   # Drugs inspections 1000..1003, Biologics 1033..1034

        def loader(base, key):
            return {"1000", "1001", "9999"}, ""

        report, code, *_ = _run(rows, link_loader=loader)
        self.assertEqual(code, 0)
        linked = report.summary["_linked_to_classifications"]
        self.assertEqual(linked["total"], 6)
        self.assertEqual(linked["inspections"], 2)
        self.assertEqual(linked["classified_inspections_total"], 3)
        self.assertEqual(linked["classified_inspections_with_citations"], 2)
        self.assertEqual(report.link_check, "ok:3")

    def test_link_unavailable_does_not_block(self):
        report, code, *_ = _run(_mixed())
        self.assertEqual(code, 0)
        self.assertEqual(report.link_check, "no_supabase_credentials")
        self.assertNotIn("_linked_to_classifications", report.summary)

    def test_link_loader_without_creds_makes_no_request(self):
        self.assertEqual(mod.load_classified_inspection_ids("", ""),
                         (None, "no_supabase_credentials"))


class RunModesTest(unittest.TestCase):
    def test_dry_run_never_posts(self):
        report, code, _, poster = _run(_mixed())
        self.assertEqual(code, 0)
        self.assertEqual(poster.calls, [])
        self.assertEqual(report.would_upsert, 18)

    def test_apply_upserts_with_composite_key_and_merge(self):
        report, code, _, poster = _run(_mixed(), dry_run=False, chunk_size=7)
        self.assertEqual(code, 0)
        self.assertEqual(report.upserted, 18)
        self.assertEqual(len(poster.calls), 3)
        c = poster.calls[0]
        self.assertEqual(c["table"], "fda_inspection_citations")
        self.assertEqual(c["on_conflict"], "inspection_id,citation_id")
        self.assertEqual(c["resolution"], "merge-duplicates")

    def test_duplicate_keys_exit_1(self):
        rows = _mixed() + [_row(0)]
        report, code, *_ = _run(rows)
        self.assertEqual(code, 1)
        self.assertEqual(report.duplicate_keys, {"1000:50000": 2})


class CliTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in
                     ("FDA_API_USER", "FDA_DDAPI_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_missing_creds_exit_2(self):
        self.assertEqual(mod.main([]), 2)

    def test_apply_needs_supabase(self):
        self.assertEqual(mod.main(["--apply", "--api-user", "u", "--api-key", "k"]), 2)

    def test_default_is_dry_run(self):
        self.assertTrue(mod.build_arg_parser().parse_args([]).dry_run)

    def test_main_has_stdout_encoding_guard_first(self):
        src = (_REPO / "collect_fda_citations.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        self.assertLess(body.index("reconfigure"), body.index("parse_args"))


class MigrationContractTest(unittest.TestCase):
    sql = _MIGRATION.read_text(encoding="utf-8")

    def test_number_is_unique(self):
        prefix = _MIGRATION.name[:3]
        same = [p.name for p in _MIGRATIONS.glob(f"{prefix}_*.sql")]
        self.assertEqual(same, [_MIGRATION.name])

    def test_no_crlf(self):
        self.assertNotIn(b"\r\n", _MIGRATION.read_bytes())

    def test_rls_enabled_and_revokes_explicit(self):
        n = re.sub(r"\s+", " ", self.sql.lower())
        self.assertIn("alter table public.fda_inspection_citations enable row level security", n)
        for role in ("public", "anon", "authenticated"):
            self.assertIn(f"revoke all on public.fda_inspection_citations from {role};", n)
        self.assertIn("to service_role;", n)
        for line in n.split(";"):
            if line.strip().startswith("grant"):
                self.assertNotRegex(line, r"\b(anon|authenticated|public)\b\s*$")

    def test_no_policy_no_findings_touch(self):
        code = "\n".join(l for l in self.sql.splitlines() if not l.strip().startswith("--"))
        self.assertNotIn("create policy", code.lower())
        self.assertNotRegex(code.lower(), r"public\.findings\b")

    def test_composite_pk_and_no_fk(self):
        self.assertIn("primary key (inspection_id, citation_id)", self.sql)
        code = "\n".join(l for l in self.sql.splitlines() if not l.strip().startswith("--"))
        self.assertNotIn("references", code.lower())

    def test_country_key_reuses_normalizer(self):
        self.assertIn("public.grm_normalize_country(country_name)", self.sql)
        self.assertNotIn("create or replace function", self.sql.lower())


class WorkflowContractTest(unittest.TestCase):
    wf = _WORKFLOW.read_text(encoding="utf-8")

    def test_dispatch_defaults_to_dry_run(self):
        m = re.search(r"dry_run:\n(?:\s+.*\n)*?\s+default: (\w+)", self.wf)
        self.assertEqual(m.group(1), "true")

    def test_schedule_gated_by_repo_variable(self):
        self.assertIn("${{ vars.ENABLE_FDA_CITATIONS || 'false' }}", self.wf)
        self.assertIn("if: needs.decide.outputs.run == 'true'", self.wf)

    def test_schedule_branch_is_explicit_apply(self):
        self.assertIn('if [ "$GRM_EVENT_NAME" = "schedule" ]; then', self.wf)

    def test_runs_this_collector_with_secrets_not_inline(self):
        self.assertIn("python collect_fda_citations.py", self.wf)
        self.assertIn("FDA_DDAPI_KEY: ${{ secrets.FDA_DDAPI_KEY }}", self.wf)

    def test_actions_are_sha_pinned(self):
        for ref in re.findall(r"uses: (\S+)", self.wf):
            self.assertRegex(ref, r"@[0-9a-f]{40}$", ref)


if __name__ == "__main__":
    unittest.main()
