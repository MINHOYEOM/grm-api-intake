#!/usr/bin/env python3
"""FIND-1 F2b backfill fetch collector tests.

All HTTP and all sleeps are mocked/injected -- no real network access, no real
time.sleep. The load-bearing contract here is IDENTITY: a raw_signal built by the
backfill path must carry the exact same raw_signal_id (source + document_id hash,
grm_findings.raw_signal_from_row) as the one the daily collector path builds for
the same document.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import date
from unittest import mock

import requests as _requests

import collect_fda_483 as fda483
import collect_fda_backfill as backfill
import collect_intake as ci
import findings_store
import findings_supabase_append as fsa


_BASE_URL = "https://example.supabase.co"
_SERVICE_KEY = "service-role-secret-token"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_483_READING_ROOM_HTML = (
    '<html><script data-drupal-selector="drupal-settings-json">'
    + json.dumps({
        "datatables": {
            "view-x": {
                "ajax": {
                    "url": "/datatables/views/ajax",
                    "data": {
                        "view_name": "ora_foia_electronic_reading_room_solr",
                        "view_display_id": "block_1",
                        "total_items": "2002",
                    },
                },
            },
        },
    })
    + "</script></html>"
)

# 9-column DataTables AJAX row (collect_fda_483._COL_* order).
_483_AJAX_ROW = [
    "05/27/2026",                                        # record date
    "BPI Labs, LLC",                                     # company
    "3012345678",                                        # FEI
    '<a href="/media/555001/download">483</a>',          # record type + media href
    "FL",                                                # state
    "",                                                  # country
    "Pharmaceutical Manufacturer",                       # establishment type
    "06/01/2026",                                        # publish date
    "",
]

# Normalized-row equivalent of _483_AJAX_ROW (what _datatable_norm_rows produces),
# used to drive the daily collector path directly.
_483_NROW = {
    "record_date": "05/27/2026",
    "company": "BPI Labs, LLC",
    "fei": "3012345678",
    "record_type": "483",
    "media_id": "555001",
    "state": "FL",
    "country": "",
    "establishment_type": "Pharmaceutical Manufacturer",
    "publish_date": "06/01/2026",
}


def _483_ajax_json(rows: list, total: int) -> str:
    return json.dumps({"data": rows, "recordsFiltered": total, "recordsTotal": total})


_WL_PAGE_HTML = (
    '<html><script data-drupal-selector="drupal-settings-json">'
    + json.dumps({
        "datatables": {
            "view-y": {
                "ajax": {
                    "url": "/datatables/views/ajax",
                    "data": {
                        "view_name": "warning_letter_solr_index",
                        "view_display_id": "block_2",
                    },
                },
            },
        },
    })
    + "</script></html>"
)

_WL_HREF = "/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/acme-pharma-llc-123456"

# 6-column WL solr AJAX row: Posted / Letter Issue / Company(+href) / Office / Subject / Response.
_WL_AJAX_ROW = [
    "06/15/2024",
    "06/10/2024",
    f'<a href="{_WL_HREF}">Acme Pharma LLC</a>',
    "Center for Drug Evaluation and Research",
    "CGMP/Finished Pharmaceuticals/Adulterated",
    "",
]

_WL_AJAX_ROW_CVM = [
    "06/15/2024",
    "06/10/2024",
    '<a href="/warning-letters/vet-feeds-inc-999">Vet Feeds Inc</a>',
    "Center for Veterinary Medicine",
    "Medicated Feeds/Adulterated",
    "",
]


def _wl_ajax_json(rows: list, total: int) -> str:
    return json.dumps({"data": rows, "recordsFiltered": total, "recordsTotal": total})


# Same document as _WL_AJAX_ROW, rendered as the daily collector's static HTML table.
_WL_DAILY_HTML = f"""
<html><body>
<table class="table">
<tr><th>Posted Date</th><th>Letter Issue Date</th><th>Company Name</th>
<th>Issuing Office</th><th>Subject</th><th>Response Letter</th></tr>
<tr>
<td>06/15/2024</td>
<td>06/10/2024</td>
<td><a href="{_WL_HREF}">Acme Pharma LLC</a></td>
<td>Center for Drug Evaluation and Research</td>
<td>CGMP/Finished Pharmaceuticals/Adulterated</td>
<td></td>
</tr>
</table>
</body></html>
"""

_WL_LETTER_HTML_NO_ANCHOR = "<html><body><p>plain page without any narrative markers</p></body></html>"
_WL_LETTER_HTML_WITH_BODY = (
    "<html><body><p>During our inspection of your firm, investigators observed "
    "significant CGMP violations in aseptic processing areas.</p></body></html>"
)


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _requests.exceptions.HTTPError(f"HTTP {self.status_code}")


class _Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _run_483(**overrides):
    """run_483 with all-network mocks; returns (report, exit_code, post_mock, pdf_mock, sleeper)."""
    kwargs = dict(
        offset=0, max_docs=200, delay=30, dry_run=False,
        base_url=_BASE_URL, service_key=_SERVICE_KEY,
    )
    existing = overrides.pop("existing_ids", set())
    ajax_rows = overrides.pop("ajax_rows", [list(_483_AJAX_ROW)])
    ajax_total = overrides.pop("ajax_total", len(ajax_rows))
    pdf_side_effect = overrides.pop("pdf_side_effect", None)
    post_response = overrides.pop("post_response", _FakeResponse(201, [{"raw_signal_id": "x"}]))
    post_side_effect = overrides.pop("post_side_effect", None)
    kwargs.update(overrides)
    sleeper = _Sleeper()

    pdf_mock = mock.MagicMock(return_value=("", "fetch-fail:test"))
    if pdf_side_effect is not None:
        pdf_mock.side_effect = pdf_side_effect

    post_kwargs = {"side_effect": post_side_effect} if post_side_effect else {"return_value": post_response}
    with mock.patch("collect_fda_backfill.fetch_existing_document_ids", return_value=existing), \
         mock.patch("collect_fda_backfill.http_get_html", return_value=_483_READING_ROOM_HTML), \
         mock.patch("collect_fda_483.http_get_html",
                    return_value=_483_ajax_json(ajax_rows, ajax_total)), \
         mock.patch("collect_fda_483._fetch_fda483_pdf_text", pdf_mock), \
         mock.patch("findings_supabase_append.requests.post", **post_kwargs) as post:
        report, exit_code = backfill.run_483(sleeper=sleeper, **kwargs)
    return report, exit_code, post, pdf_mock, sleeper


def _run_wl(**overrides):
    """run_wl with all-network mocks; returns (report, exit_code, post_mock, html_mock, sleeper)."""
    kwargs = dict(
        offset=0, max_docs=200, delay=30, dry_run=False,
        base_url=_BASE_URL, service_key=_SERVICE_KEY,
    )
    existing = overrides.pop("existing_ids", set())
    ajax_rows = overrides.pop("ajax_rows", [list(_WL_AJAX_ROW)])
    ajax_total = overrides.pop("ajax_total", len(ajax_rows))
    letter_html = overrides.pop("letter_html", _WL_LETTER_HTML_NO_ANCHOR)
    post_response = overrides.pop("post_response", _FakeResponse(201, [{"raw_signal_id": "x"}]))
    kwargs.update(overrides)
    sleeper = _Sleeper()

    # http_get_html call order in run_wl: WL page (config) -> AJAX -> letter per doc.
    responses = [_WL_PAGE_HTML, _wl_ajax_json(ajax_rows, ajax_total)] + [letter_html] * 20
    html_mock = mock.MagicMock(side_effect=responses)
    with mock.patch("collect_fda_backfill.fetch_existing_document_ids", return_value=existing), \
         mock.patch("collect_fda_backfill.http_get_html", html_mock), \
         mock.patch("findings_supabase_append.requests.post", return_value=post_response) as post:
        report, exit_code = backfill.run_wl(sleeper=sleeper, **kwargs)
    return report, exit_code, post, html_mock, sleeper


def _posted_records(post_mock) -> list[dict]:
    records: list[dict] = []
    for call in post_mock.call_args_list:
        records.extend(call.kwargs["json"])
    return records


# ---------------------------------------------------------------------------
# Identity with the daily collector path (the core F2b contract)
# ---------------------------------------------------------------------------


class Fda483IdentityTest(unittest.TestCase):
    def test_backfill_raw_signal_identical_to_daily_collector_path(self) -> None:
        # Daily path: drive collect_fda_483.collect_fda_483 itself (its row source
        # mocked to the same normalized row; PDF text mocked to the same failure).
        with mock.patch("collect_fda_483._fetch_html_rows",
                        return_value=([dict(_483_NROW)], 1, False)), \
             mock.patch("collect_fda_483._fetch_fda483_pdf_text",
                        return_value=("", "fetch-fail:test")), \
             mock.patch("collect_fda_483.time.sleep"):
            items, err = fda483.collect_fda_483(date(2026, 5, 1), date(2026, 6, 30))
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        daily_record = findings_store.raw_signal_from_intake_item(items[0])

        # Backfill path: same document via the AJAX listing.
        report, exit_code, post, _pdf, _sleeper = _run_483()
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.invalid, 0)
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        backfill_record = posted[0]

        self.assertEqual(backfill_record["document_id"], "fda483-555001")
        self.assertEqual(backfill_record["raw_signal_id"], daily_record["raw_signal_id"])
        # Full-record identity (collected_at is the only allowed difference).
        for key, value in daily_record.items():
            if key == "collected_at":
                continue
            self.assertEqual(backfill_record.get(key), value, f"field mismatch: {key}")


class FdaWlIdentityTest(unittest.TestCase):
    def _daily_record(self) -> dict:
        # Daily path: drive collect_intake.collect_fda_warning_letters itself against
        # the same document rendered as its static HTML table.
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENABLE_WL_BODY", "ENABLE_WL_BODY_FULL")}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("collect_intake.requests.get",
                        return_value=_FakeResponse(200, text=_WL_DAILY_HTML)):
            items, err = ci.collect_fda_warning_letters(date(2024, 6, 1), date(2024, 6, 30))
        assert err is None
        assert len(items) == 1
        return findings_store.raw_signal_from_intake_item(items[0])

    def test_backfill_raw_signal_identical_to_daily_collector_path(self) -> None:
        daily_record = self._daily_record()

        report, exit_code, post, _html, _sleeper = _run_wl(letter_html=_WL_LETTER_HTML_NO_ANCHOR)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.invalid, 0)
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        backfill_record = posted[0]

        self.assertEqual(backfill_record["raw_signal_id"], daily_record["raw_signal_id"])
        self.assertEqual(backfill_record["document_id"], daily_record["document_id"])
        self.assertEqual(backfill_record["source"], "FDA Warning Letter")
        # With no wl_body_full extracted, the records must be fully identical
        # (collected_at is the only allowed difference).
        for key, value in daily_record.items():
            if key == "collected_at":
                continue
            self.assertEqual(backfill_record.get(key), value, f"field mismatch: {key}")

    def test_raw_signal_id_stable_even_when_backfill_adds_wl_body_full(self) -> None:
        daily_record = self._daily_record()
        _report, _code, post, _html, _sleeper = _run_wl(letter_html=_WL_LETTER_HTML_WITH_BODY)
        backfill_record = _posted_records(post)[0]
        self.assertIn("wl_body_full", json.loads(backfill_record["raw_json"]))
        self.assertEqual(backfill_record["raw_signal_id"], daily_record["raw_signal_id"])


# ---------------------------------------------------------------------------
# Skip triage
# ---------------------------------------------------------------------------


class SkipExistingTest(unittest.TestCase):
    def test_existing_document_skips_before_any_document_fetch(self) -> None:
        report, exit_code, post, pdf, _sleeper = _run_483(existing_ids={"fda483-555001"})
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.skipped_existing, 1)
        self.assertEqual(report.fetched, 0)
        self.assertEqual(report.appended, 0)
        pdf.assert_not_called()
        post.assert_not_called()

    def test_wl_existing_document_skips_before_letter_fetch(self) -> None:
        doc_id = ci._stable_doc_id(
            "FDA Warning Letter", "Acme Pharma LLC",
            "https://www.fda.gov" + _WL_HREF, "2024-06-15",
        )
        report, exit_code, post, html, _sleeper = _run_wl(existing_ids={doc_id})
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.skipped_existing, 1)
        self.assertEqual(report.fetched, 0)
        # Only the WL page (config) + AJAX listing -- never the letter page.
        self.assertEqual(html.call_count, 2)
        post.assert_not_called()


class WlOfficeGateTest(unittest.TestCase):
    def test_cvm_letter_is_gated_and_never_fetched(self) -> None:
        report, exit_code, post, html, _sleeper = _run_wl(
            ajax_rows=[list(_WL_AJAX_ROW_CVM)],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.skipped_gated, 1)
        self.assertEqual(report.fetched, 0)
        self.assertEqual(html.call_count, 2)  # config + AJAX only
        post.assert_not_called()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class DryRunTest(unittest.TestCase):
    def test_483_dry_run_never_fetches_documents_or_posts(self) -> None:
        report, exit_code, post, pdf, _sleeper = _run_483(dry_run=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.listed, 1)
        self.assertEqual(report.fetched, 0)
        self.assertEqual(report.appended, 0)
        self.assertEqual(report.would_fetch, ["fda483-555001"])
        pdf.assert_not_called()
        post.assert_not_called()

    def test_wl_dry_run_never_fetches_letters_or_posts(self) -> None:
        report, exit_code, post, html, _sleeper = _run_wl(dry_run=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.fetched, 0)
        self.assertEqual(len(report.would_fetch), 1)
        self.assertEqual(html.call_count, 2)  # config + AJAX only, no letter fetch
        post.assert_not_called()

    def test_dry_run_would_fetch_caps_at_10(self) -> None:
        rows = []
        for i in range(12):
            row = list(_483_AJAX_ROW)
            row[3] = f'<a href="/media/60{i:02d}/download">483</a>'
            rows.append(row)
        report, _code, _post, _pdf, _sleeper = _run_483(dry_run=True, ajax_rows=rows, ajax_total=500)
        self.assertEqual(report.listed, 12)
        self.assertEqual(len(report.would_fetch), 10)


# ---------------------------------------------------------------------------
# POST transport (idempotent parameters, batching)
# ---------------------------------------------------------------------------


class PostContractTest(unittest.TestCase):
    def test_post_uses_on_conflict_and_ignore_duplicates(self) -> None:
        _report, _code, post, _pdf, _sleeper = _run_483()
        post.assert_called_once()
        call = post.call_args
        url = call.args[0] if call.args else call.kwargs["url"]
        self.assertTrue(url.endswith("/rest/v1/raw_signals"))
        self.assertEqual(call.kwargs["params"], {"on_conflict": "raw_signal_id"})
        self.assertIn("resolution=ignore-duplicates", call.kwargs["headers"]["Prefer"])

    def test_batches_of_10(self) -> None:
        records = [{"raw_signal_id": f"rawsig-{i}", "source": "FDA 483"} for i in range(25)]
        errors: list[str] = []
        with mock.patch("findings_supabase_append._post_rows",
                        return_value=(201, [{}], "")) as post_rows:
            appended = backfill._post_raw_signals(_BASE_URL, _SERVICE_KEY, records, errors)
        self.assertEqual(post_rows.call_count, 3)  # 10 + 10 + 5
        sizes = [len(c.args[3]) for c in post_rows.call_args_list]
        self.assertEqual(sizes, [10, 10, 5])
        self.assertEqual(appended, 3)  # one representation row per mocked batch
        self.assertEqual(errors, [])

    def test_duplicates_returning_no_rows_are_not_errors(self) -> None:
        _report, _code, post, _pdf, _sleeper = _run_483(
            post_response=_FakeResponse(200, []),  # ignore-duplicates: nothing inserted
        )
        self.assertEqual(_report.appended, 0)
        self.assertEqual(_report.errors, [])
        post.assert_called_once()


# ---------------------------------------------------------------------------
# Delay (robots Crawl-Delay) via injected sleeper
# ---------------------------------------------------------------------------


class DelayTest(unittest.TestCase):
    def test_483_sleeps_before_every_fda_request(self) -> None:
        _report, _code, _post, _pdf, sleeper = _run_483(delay=30)
        # config page + AJAX list + 1 document PDF
        self.assertEqual(sleeper.calls, [30, 30, 30])

    def test_483_dry_run_sleeps_only_for_listing(self) -> None:
        _report, _code, _post, _pdf, sleeper = _run_483(dry_run=True, delay=30)
        self.assertEqual(sleeper.calls, [30, 30])

    def test_wl_sleeps_before_every_fda_request(self) -> None:
        _report, _code, _post, _html, sleeper = _run_wl(delay=7)
        # config page + AJAX list + 1 letter page
        self.assertEqual(sleeper.calls, [7, 7, 7])


# ---------------------------------------------------------------------------
# Listing pagination: next_offset / exhausted
# ---------------------------------------------------------------------------


class PaginationTest(unittest.TestCase):
    def test_full_page_not_exhausted(self) -> None:
        rows = []
        for i in range(2):
            row = list(_483_AJAX_ROW)
            row[3] = f'<a href="/media/70{i}/download">483</a>'
            rows.append(row)
        report, _code, _post, _pdf, _sleeper = _run_483(
            dry_run=True, offset=100, max_docs=2, ajax_rows=rows, ajax_total=2002,
        )
        self.assertEqual(report.next_offset, 102)
        self.assertFalse(report.exhausted)

    def test_short_page_is_exhausted(self) -> None:
        report, _code, _post, _pdf, _sleeper = _run_483(
            dry_run=True, offset=2000, max_docs=200, ajax_rows=[list(_483_AJAX_ROW)],
            ajax_total=2001,
        )
        self.assertEqual(report.next_offset, 2001)
        self.assertTrue(report.exhausted)

    def test_full_page_reaching_total_is_exhausted(self) -> None:
        rows = []
        for i in range(2):
            row = list(_483_AJAX_ROW)
            row[3] = f'<a href="/media/71{i}/download">483</a>'
            rows.append(row)
        report, _code, _post, _pdf, _sleeper = _run_483(
            dry_run=True, offset=2000, max_docs=2, ajax_rows=rows, ajax_total=2002,
        )
        self.assertTrue(report.exhausted)


# ---------------------------------------------------------------------------
# Errors and exit codes
# ---------------------------------------------------------------------------


class ErrorHandlingTest(unittest.TestCase):
    def test_document_fetch_error_is_recorded_but_run_stays_exit_0(self) -> None:
        report, exit_code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=RuntimeError(f"boom apikey={_SERVICE_KEY}"),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("483-document-fetch-failed", report.errors[0])
        self.assertIn("RuntimeError", report.errors[0])
        self.assertNotIn(_SERVICE_KEY, report.errors[0])
        post.assert_not_called()

    def test_post_error_is_recorded_but_run_stays_exit_0(self) -> None:
        report, exit_code, _post, _pdf, _sleeper = _run_483(
            post_side_effect=_requests.exceptions.RequestException("reset"),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.appended, 0)
        self.assertTrue(any("raw_signals-post-failed" in e for e in report.errors))

    def test_listing_failure_is_exit_2(self) -> None:
        sleeper = _Sleeper()
        with mock.patch("collect_fda_backfill.fetch_existing_document_ids", return_value=set()), \
             mock.patch("collect_fda_backfill.http_get_html",
                        side_effect=RuntimeError("HTTP GET final failure")):
            report, exit_code = backfill.run_483(
                offset=0, max_docs=10, delay=0, dry_run=True,
                base_url=_BASE_URL, service_key=_SERVICE_KEY, sleeper=sleeper,
            )
        self.assertEqual(exit_code, 2)
        self.assertTrue(any("list-config-failed" in e for e in report.errors))

    def test_existing_ids_failure_is_exit_2_without_key_leak(self) -> None:
        sleeper = _Sleeper()
        with mock.patch(
            "collect_fda_backfill.requests.get",
            side_effect=_requests.exceptions.RequestException(f"reset apikey={_SERVICE_KEY}"),
        ):
            report, exit_code = backfill.run_483(
                offset=0, max_docs=10, delay=0, dry_run=True,
                base_url=_BASE_URL, service_key=_SERVICE_KEY, sleeper=sleeper,
            )
        self.assertEqual(exit_code, 2)
        self.assertTrue(any("existing-ids-fetch-failed" in e for e in report.errors))
        for err in report.errors:
            self.assertNotIn(_SERVICE_KEY, err)


class ServiceKeySecrecyTest(unittest.TestCase):
    def test_report_json_never_contains_key(self) -> None:
        report, _code, _post, _pdf, _sleeper = _run_483(
            pdf_side_effect=RuntimeError(f"apikey={_SERVICE_KEY}"),
        )
        from dataclasses import asdict
        self.assertNotIn(_SERVICE_KEY, json.dumps(asdict(report)))


# ---------------------------------------------------------------------------
# Existing document_id pre-fetch (Supabase GET pagination)
# ---------------------------------------------------------------------------


class FetchExistingDocumentIdsTest(unittest.TestCase):
    def test_paginates_with_range_headers_and_source_filter(self) -> None:
        page1 = _FakeResponse(206, [{"document_id": "fda483-1"}, {"document_id": "fda483-2"}],
                              headers={"Content-Range": "0-1/3"})
        page2 = _FakeResponse(200, [{"document_id": "fda483-3"}],
                              headers={"Content-Range": "2-2/3"})
        with mock.patch("collect_fda_backfill.requests.get", side_effect=[page1, page2]) as get:
            ids = backfill.fetch_existing_document_ids(
                _BASE_URL, _SERVICE_KEY, "FDA 483", page_size=2,
            )
        self.assertEqual(ids, {"fda483-1", "fda483-2", "fda483-3"})
        self.assertEqual(get.call_count, 2)
        first = get.call_args_list[0].kwargs
        self.assertEqual(first["params"], {"select": "document_id", "source": "eq.FDA 483"})
        self.assertEqual(first["headers"]["Range"], "0-1")
        self.assertEqual(first["headers"]["Range-Unit"], "items")
        second = get.call_args_list[1].kwargs
        self.assertEqual(second["headers"]["Range"], "2-3")

    def test_5xx_retries_once_then_succeeds(self) -> None:
        responses = [
            _FakeResponse(503),
            _FakeResponse(200, [{"document_id": "d1"}], headers={"Content-Range": "0-0/1"}),
        ]
        with mock.patch("collect_fda_backfill.requests.get", side_effect=responses) as get:
            ids = backfill.fetch_existing_document_ids(_BASE_URL, _SERVICE_KEY, "FDA 483")
        self.assertEqual(ids, {"d1"})
        self.assertEqual(get.call_count, 2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class CliTest(unittest.TestCase):
    def test_missing_credentials_is_exit_2(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(backfill.main(["--source", "fda483", "--dry-run"]), 2)

    def test_non_https_url_is_exit_2(self) -> None:
        env = dict(os.environ)
        env["SUPABASE_URL"] = "http://example.supabase.co"
        env["SUPABASE_SERVICE_ROLE_KEY"] = _SERVICE_KEY
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(backfill.main(["--source", "fda_wl", "--dry-run"]), 2)

    def test_report_printed_and_written_to_output(self) -> None:
        import tempfile
        env = dict(os.environ)
        env["SUPABASE_URL"] = _BASE_URL
        env["SUPABASE_SERVICE_ROLE_KEY"] = _SERVICE_KEY

        fake_report = backfill.BackfillFetchReport(source="fda483", next_offset=200)
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "report.json")
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.dict(backfill._RUNNERS,
                                 {"fda483": mock.MagicMock(return_value=(fake_report, 0))}):
                exit_code = backfill.main(
                    ["--source", "fda483", "--dry-run", "--output", out_path],
                )
            self.assertEqual(exit_code, 0)
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["schema_version"], "grm-findings-backfill-fetch/v1")
        self.assertEqual(data["next_offset"], 200)
        self.assertNotIn(_SERVICE_KEY, json.dumps(data))


if __name__ == "__main__":
    unittest.main()
