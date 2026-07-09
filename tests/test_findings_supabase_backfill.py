#!/usr/bin/env python3
"""FIND-1 M12 findings backfill tests.

All HTTP is mocked -- no real network access. Record construction itself
(findings_from_raw_signal, raw_signal_from_row) is exercised here only to
build realistic fixtures; its own correctness is covered by
test_findings_extractors.py and test_grm_findings.py.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import findings_extractors as extractors
import findings_supabase_backfill as backfill
import grm_findings as gf


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def _load_input(name: str) -> dict:
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as f:
        return json.load(f)


def _raw_signal(name: str) -> dict:
    fx = _load_input(name)
    return gf.raw_signal_from_row(fx["row"], fx["raw"])


def _wl_numbered_list_raw_signal() -> dict:
    """A WL raw_signal whose body decomposes into 2 findings (M11 fan-out)."""
    fx = _load_input("warning_letter_excerpt")
    raw = dict(fx["raw"])
    raw["wl_body_excerpt"] = (
        "During our inspection, our investigators observed specific violations "
        "including, but not limited to, the following. 1. Your firm failed to "
        "establish written procedures for cleaning and maintenance of equipment "
        "(21 CFR 211.67). Investigators observed residue on shared equipment "
        "surfaces after cleaning was documented as complete. 2. Your firm failed "
        "to thoroughly investigate a customer complaint related to product "
        "contamination (21 CFR 211.198). The complaint file did not include a "
        "documented root cause or corrective action."
    )
    raw["wl_body_full"] = ""
    return gf.raw_signal_from_row(fx["row"], raw)


def _no_extractor_raw_signal(document_id: str = "recall-9001") -> dict:
    """A raw_signal from a source with no findings extractor coverage --
    should legitimately extract to 0 findings.
    """
    row = {
        "date": "2026-07-01",
        "document_id": document_id,
        "firm": "Example Pharma",
        "headline": "[MFDS Recall] Example Pharma",
        "language": "KO",
        "modality": "Chemical",
        "official_url": "https://www.mfds.go.kr/recall/9001",
        "signal_tier": "Tier 3",
        "site_country": "Republic of Korea",
        "source": "MFDS Recall",
        "source_url": "https://www.mfds.go.kr/recall",
        "type_or_class": "recall",
    }
    raw = {"note": "no fields any extractor recognizes"}
    return gf.raw_signal_from_row(row, raw)


class _FakeGetResponse:
    def __init__(self, status_code: int, payload=None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class ParseContentRangeTest(unittest.TestCase):
    def test_exact_total(self) -> None:
        self.assertEqual(backfill._parse_content_range("0-999/1234"), 1234)

    def test_empty_result(self) -> None:
        self.assertEqual(backfill._parse_content_range("*/0"), 0)

    def test_unknown_total_returns_none(self) -> None:
        self.assertIsNone(backfill._parse_content_range("0-999/*"))

    def test_missing_slash_returns_none(self) -> None:
        self.assertIsNone(backfill._parse_content_range(""))


class FetchRawSignalsPaginationTest(unittest.TestCase):
    def test_pages_until_content_range_total_reached(self) -> None:
        page1 = _FakeGetResponse(206, [{"raw_signal_id": "a"}, {"raw_signal_id": "b"}],
                                  headers={"Content-Range": "0-1/3"})
        page2 = _FakeGetResponse(200, [{"raw_signal_id": "c"}],
                                  headers={"Content-Range": "2-2/3"})
        with mock.patch("findings_supabase_backfill.requests.get",
                         side_effect=[page1, page2]) as get:
            rows = backfill.fetch_raw_signals(_BASE_URL, _SERVICE_KEY, page_size=2)

        self.assertEqual([r["raw_signal_id"] for r in rows], ["a", "b", "c"])
        self.assertEqual(get.call_count, 2)

        first_call_headers = get.call_args_list[0].kwargs["headers"]
        self.assertEqual(first_call_headers["Range"], "0-1")
        self.assertEqual(first_call_headers["Range-Unit"], "items")
        self.assertEqual(first_call_headers["apikey"], _SERVICE_KEY)
        self.assertEqual(first_call_headers["Authorization"], f"Bearer {_SERVICE_KEY}")

        second_call_headers = get.call_args_list[1].kwargs["headers"]
        self.assertEqual(second_call_headers["Range"], "2-3")

        first_call_params = get.call_args_list[0].kwargs["params"]
        self.assertEqual(first_call_params["select"], "*")

    def test_pages_by_short_page_when_content_range_total_unavailable(self) -> None:
        page1 = _FakeGetResponse(200, [{"raw_signal_id": "a"}, {"raw_signal_id": "b"}],
                                  headers={})
        page2 = _FakeGetResponse(200, [{"raw_signal_id": "c"}], headers={})
        with mock.patch("findings_supabase_backfill.requests.get",
                         side_effect=[page1, page2]) as get:
            rows = backfill.fetch_raw_signals(_BASE_URL, _SERVICE_KEY, page_size=2)

        self.assertEqual([r["raw_signal_id"] for r in rows], ["a", "b", "c"])
        self.assertEqual(get.call_count, 2)

    def test_empty_table_returns_empty_list_after_one_call(self) -> None:
        empty = _FakeGetResponse(200, [], headers={"Content-Range": "*/0"})
        with mock.patch("findings_supabase_backfill.requests.get",
                         return_value=empty) as get:
            rows = backfill.fetch_raw_signals(_BASE_URL, _SERVICE_KEY, page_size=1000)

        self.assertEqual(rows, [])
        self.assertEqual(get.call_count, 1)

    def test_https_guard_rejects_non_https_base_url(self) -> None:
        with mock.patch("findings_supabase_backfill.requests.get") as get:
            with self.assertRaises(ValueError):
                backfill.fetch_raw_signals("http://example.supabase.co", _SERVICE_KEY)
        get.assert_not_called()

    def test_5xx_retries_once_then_succeeds(self) -> None:
        responses = [
            _FakeGetResponse(503),
            _FakeGetResponse(200, [{"raw_signal_id": "a"}], headers={"Content-Range": "0-0/1"}),
        ]
        with mock.patch("findings_supabase_backfill.requests.get", side_effect=responses) as get:
            rows = backfill.fetch_raw_signals(_BASE_URL, _SERVICE_KEY, page_size=1000)

        self.assertEqual(len(rows), 1)
        self.assertEqual(get.call_count, 2)

    def test_exhausted_retry_raises_without_leaking_service_key(self) -> None:
        with mock.patch("findings_supabase_backfill.requests.get",
                         side_effect=[_FakeGetResponse(503), _FakeGetResponse(503)]):
            with self.assertRaises(RuntimeError) as ctx:
                backfill.fetch_raw_signals(_BASE_URL, _SERVICE_KEY, page_size=1000)

        self.assertNotIn(_SERVICE_KEY, str(ctx.exception))


class FetchExistingFindingRawIdsTest(unittest.TestCase):
    def test_returns_set_of_raw_signal_ids(self) -> None:
        resp = _FakeGetResponse(
            200,
            [{"raw_signal_id": "rs-1"}, {"raw_signal_id": "rs-2"}, {"raw_signal_id": ""}],
            headers={"Content-Range": "0-2/3"},
        )
        with mock.patch("findings_supabase_backfill.requests.get", return_value=resp) as get:
            ids = backfill.fetch_existing_finding_raw_ids(_BASE_URL, _SERVICE_KEY)

        self.assertEqual(ids, {"rs-1", "rs-2"})
        _args, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["select"], "raw_signal_id")


class SelectUnbackfilledTest(unittest.TestCase):
    def test_diffs_against_existing_ids(self) -> None:
        raw_signals = [
            {"raw_signal_id": "rs-1"},
            {"raw_signal_id": "rs-2"},
            {"raw_signal_id": "rs-3"},
        ]
        unbackfilled = backfill.select_unbackfilled(raw_signals, {"rs-2"})
        self.assertEqual([r["raw_signal_id"] for r in unbackfilled], ["rs-1", "rs-3"])

    def test_empty_existing_ids_keeps_everything(self) -> None:
        raw_signals = [{"raw_signal_id": "rs-1"}, {"raw_signal_id": "rs-2"}]
        self.assertEqual(backfill.select_unbackfilled(raw_signals, set()), raw_signals)


class PlanBackfillTest(unittest.TestCase):
    def test_extracts_findings_and_skips_zero_finding_sources(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        wl = _wl_numbered_list_raw_signal()
        no_extractor = _no_extractor_raw_signal()

        raw_signals = [fda_483, wl, no_extractor]
        pairs = backfill.plan_backfill(raw_signals, set())

        self.assertEqual(len(pairs), 2)
        raw_ids_in_plan = {rs["raw_signal_id"] for rs, _findings in pairs}
        self.assertIn(fda_483["raw_signal_id"], raw_ids_in_plan)
        self.assertIn(wl["raw_signal_id"], raw_ids_in_plan)
        self.assertNotIn(no_extractor["raw_signal_id"], raw_ids_in_plan)

        by_id = {rs["raw_signal_id"]: findings for rs, findings in pairs}
        self.assertEqual(len(by_id[fda_483["raw_signal_id"]]), 2)
        self.assertEqual(len(by_id[wl["raw_signal_id"]]), 2)  # M11 WL fan-out

        for _rs, findings in pairs:
            for finding in findings:
                self.assertEqual(gf.validate_finding(finding), [])

    def test_already_backfilled_raw_signal_is_excluded(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        pairs = backfill.plan_backfill([fda_483], {fda_483["raw_signal_id"]})
        self.assertEqual(pairs, [])

    def test_matches_direct_extractor_call(self) -> None:
        # Sanity check: plan_backfill must not reimplement/duplicate extraction logic --
        # it must call findings_extractors.findings_from_raw_signal directly.
        fda_483 = _raw_signal("fda_483_observations")
        expected = extractors.findings_from_raw_signal(fda_483)
        pairs = backfill.plan_backfill([fda_483], set())
        self.assertEqual(pairs[0][1], expected)


class RunBackfillDryRunTest(unittest.TestCase):
    def test_dry_run_never_posts(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        wl = _wl_numbered_list_raw_signal()
        no_extractor = _no_extractor_raw_signal()

        with mock.patch("findings_supabase_backfill.fetch_raw_signals",
                         return_value=[fda_483, wl, no_extractor]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids",
                         return_value=set()), \
             mock.patch("findings_supabase_append.requests.post") as post:
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=True)

        post.assert_not_called()
        self.assertEqual(report.raw_scanned, 3)
        self.assertEqual(report.unbackfilled, 3)
        self.assertEqual(report.with_findings, 2)
        self.assertEqual(report.findings_extracted, 4)
        self.assertEqual(report.findings_inserted, 0)
        self.assertEqual(report.findings_duplicate, 0)
        self.assertEqual(report.errors, ())

    def test_dry_run_respects_limit_on_unbackfilled_set(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        wl = _wl_numbered_list_raw_signal()

        with mock.patch("findings_supabase_backfill.fetch_raw_signals",
                         return_value=[fda_483, wl]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids",
                         return_value=set()):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=True, limit=1)

        self.assertEqual(report.raw_scanned, 2)
        self.assertEqual(report.unbackfilled, 1)
        self.assertEqual(report.with_findings, 1)


class RunBackfillExecuteTest(unittest.TestCase):
    def test_execute_mode_calls_append_findings_batch_and_aggregates(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        wl = _wl_numbered_list_raw_signal()

        raw_resp_fda = _FakeGetResponse(201, [{"finding_id": "f1"}, {"finding_id": "f2"}])
        raw_resp_wl = _FakeGetResponse(201, [{"finding_id": "f3"}])  # only 1 of 2 "inserted"

        with mock.patch("findings_supabase_backfill.fetch_raw_signals",
                         return_value=[fda_483, wl]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids",
                         return_value=set()), \
             mock.patch("findings_supabase_append.requests.post",
                         side_effect=[raw_resp_fda, raw_resp_wl]) as post:
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(post.call_count, 2)
        self.assertEqual(report.raw_scanned, 2)
        self.assertEqual(report.unbackfilled, 2)
        self.assertEqual(report.with_findings, 2)
        self.assertEqual(report.findings_extracted, 4)
        self.assertEqual(report.findings_inserted, 3)
        self.assertEqual(report.findings_duplicate, 1)
        self.assertEqual(report.errors, ())

        # findings-only append: raw_signals table is never POSTed to (raw already exists).
        for call in post.call_args_list:
            url = call.args[0] if call.args else call.kwargs.get("url", "")
            self.assertIn("/rest/v1/findings", url)
            self.assertNotIn("/rest/v1/raw_signals", url)

    def test_execute_mode_idempotent_409_counts_as_duplicate_not_error(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        batch_conflict = _FakeGetResponse(409)
        row1 = _FakeGetResponse(201, [{"finding_id": "f1"}])
        row2 = _FakeGetResponse(409)

        with mock.patch("findings_supabase_backfill.fetch_raw_signals",
                         return_value=[fda_483]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids",
                         return_value=set()), \
             mock.patch("findings_supabase_append.requests.post",
                         side_effect=[batch_conflict, row1, row2]):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=False)

        self.assertEqual(report.findings_inserted, 1)
        self.assertEqual(report.findings_duplicate, 1)
        self.assertEqual(report.errors, ())

    def test_no_unbackfilled_raw_signals_is_a_clean_noop(self) -> None:
        with mock.patch("findings_supabase_backfill.fetch_raw_signals", return_value=[]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", return_value=set()), \
             mock.patch("findings_supabase_append.requests.post") as post:
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=False)

        post.assert_not_called()
        self.assertEqual(report, backfill.BackfillReport())


class ServiceKeySecrecyTest(unittest.TestCase):
    def test_https_guard_error_never_contains_key(self) -> None:
        report = backfill.run_backfill("http://example.supabase.co", _SERVICE_KEY, dry_run=True)
        self.assertEqual(report.raw_scanned, 0)
        for err in report.errors:
            self.assertNotIn(_SERVICE_KEY, err)

    def test_fetch_error_surfaces_without_key_in_report(self) -> None:
        import requests as _requests

        def _boom(*_args, **_kwargs):
            raise _requests.exceptions.RequestException(f"connection reset apikey={_SERVICE_KEY}")

        with mock.patch("findings_supabase_backfill.requests.get", side_effect=_boom):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=True)

        self.assertTrue(report.errors)
        for err in report.errors:
            self.assertNotIn(_SERVICE_KEY, err)
        self.assertNotIn(_SERVICE_KEY, str(report))

    def test_report_json_never_contains_key(self) -> None:
        fda_483 = _raw_signal("fda_483_observations")
        with mock.patch("findings_supabase_backfill.fetch_raw_signals", return_value=[fda_483]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", return_value=set()), \
             mock.patch("findings_supabase_append.requests.post",
                         return_value=_FakeGetResponse(201, [{"finding_id": "f1"}, {"finding_id": "f2"}])):
            report = backfill.run_backfill(_BASE_URL, _SERVICE_KEY, dry_run=False)

        from dataclasses import asdict
        self.assertNotIn(_SERVICE_KEY, json.dumps(asdict(report)))


class CliTest(unittest.TestCase):
    def test_main_exits_2_when_credentials_missing(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            exit_code = backfill.main(["--dry-run"])
        self.assertEqual(exit_code, 2)

    def test_main_dry_run_reads_credentials_from_env(self) -> None:
        env = dict(os.environ)
        env["SUPABASE_URL"] = _BASE_URL
        env["SUPABASE_SERVICE_ROLE_KEY"] = _SERVICE_KEY
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("findings_supabase_backfill.fetch_raw_signals", return_value=[]), \
             mock.patch("findings_supabase_backfill.fetch_existing_finding_raw_ids", return_value=set()):
            exit_code = backfill.main(["--dry-run"])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
