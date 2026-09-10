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


# [2026-09-10] Normalized rows in the shape `collect_fda_483._json_norm_rows` produces
# (2nd-tier full-JSON backbone) -- same schema as _483_NROW, `country` always "" (the
# JSON has no country field; only the HTML/DataTables paths carry it).
_483_JSON_ROW_A = {
    "record_date": "05/20/2026",
    "company": "Gamma Pharma Inc",
    "fei": "3099999999",
    "record_type": "483",
    "media_id": "555002",
    "state": "CA",
    "country": "",
    "establishment_type": "Pharmaceutical Manufacturer",
    "publish_date": "06/05/2026",          # newest of the three fixtures
}
_483_JSON_ROW_B = {
    "record_date": "05/10/2026",
    "company": "Delta Labs LLC",
    "fei": "3088888888",
    "record_type": "483",
    "media_id": "555003",
    "state": "TX",
    "country": "",
    "establishment_type": "Pharmaceutical Manufacturer",
    "publish_date": "06/01/2026",
}
_483_JSON_ROW_C = {
    "record_date": "04/28/2026",
    "company": "Epsilon Sterile Mfg",
    "fei": "3077777777",
    "record_type": "483",
    "media_id": "555004",
    "state": "NJ",
    "country": "",
    "establishment_type": "Pharmaceutical Manufacturer",
    "publish_date": "05/20/2026",           # oldest of the three fixtures
}

# Reading-room HTML that fetches fine but carries no Drupal DataTables settings at all
# (Akamai's block page shape observed 2026-09-09+ -- 200 OK, no `drupal-settings-json`).
_HTML_NO_DRUPAL_SETTINGS = "<html><body>access denied / no drupal settings here</body></html>"


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
    # [2026-09-10 JSON 2차 백본] 리딩룸 HTML 응답/2차 JSON 을 오버라이드할 수 있게 한다.
    # 기본값은 종전과 완전히 동일한 동작(1차 성공, 2차는 절대 호출되지 않음)을 유지하면서,
    # `_fetch_legacy_json_rows` 를 **항상** 목(default 빈 결과)해 둔다 -- 이 헬퍼로 짠 어떤
    # 시나리오가 실수로 1차를 실패시키더라도 실 네트워크(fda.gov)에 닿지 않는다.
    reading_room_html = overrides.pop("reading_room_html", _483_READING_ROOM_HTML)
    reading_room_side_effect = overrides.pop("reading_room_side_effect", None)
    legacy_json_rows = overrides.pop("legacy_json_rows", [])
    legacy_json_total = overrides.pop("legacy_json_total", 0)
    kwargs.update(overrides)
    sleeper = _Sleeper()

    pdf_mock = mock.MagicMock(return_value=("", "fetch-fail:test"))
    if pdf_side_effect is not None:
        pdf_mock.side_effect = pdf_side_effect

    post_kwargs = {"side_effect": post_side_effect} if post_side_effect else {"return_value": post_response}
    html_kwargs = ({"side_effect": reading_room_side_effect} if reading_room_side_effect is not None
                   else {"return_value": reading_room_html})
    with mock.patch("collect_fda_backfill.fetch_existing_document_ids", return_value=existing), \
         mock.patch("collect_fda_backfill.http_get_html", **html_kwargs), \
         mock.patch("collect_fda_483.http_get_html",
                    return_value=_483_ajax_json(ajax_rows, ajax_total)), \
         mock.patch("collect_fda_483._fetch_legacy_json_rows",
                    return_value=(legacy_json_rows, legacy_json_total)), \
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
        # [2026-09-10] 1차 DataTables 실패는 이제 2차 전수 JSON 으로 폴백한다 -- 그 백본도
        # 죽었을 때만 exit 2 다. `_fetch_legacy_json_rows` 를 명시적으로 실패시켜(실 네트워크
        # 호출 없이) 두 백본 모두 죽은 시나리오를 재현한다.
        sleeper = _Sleeper()
        with mock.patch("collect_fda_backfill.fetch_existing_document_ids", return_value=set()), \
             mock.patch("collect_fda_backfill.http_get_html",
                        side_effect=RuntimeError("HTTP GET final failure")), \
             mock.patch("collect_fda_483._fetch_legacy_json_rows", return_value=([], 0)):
            report, exit_code = backfill.run_483(
                offset=0, max_docs=10, delay=0, dry_run=True,
                base_url=_BASE_URL, service_key=_SERVICE_KEY, sleeper=sleeper,
            )
        self.assertEqual(exit_code, 2)
        self.assertTrue(any("list-config-failed" in e for e in report.errors))
        self.assertIn("json-backbone-failed", report.errors)

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
# [2026-09-10] 483 listing JSON 2nd-tier fallback -- 09-09 Akamai bot-block took out the
# reading-room DataTables config 4 scheduled runs in a row; run_483 now falls back to the
# daily collector's own 2nd-tier full-JSON backbone (collect_fda_483._fetch_legacy_json_rows).
# ---------------------------------------------------------------------------


class Fda483JsonFallbackTest(unittest.TestCase):
    def test_config_missing_falls_back_to_json_and_computes_remaining(self) -> None:
        rows = [dict(_483_JSON_ROW_A), dict(_483_JSON_ROW_B), dict(_483_JSON_ROW_C)]
        report, exit_code, post, pdf, sleeper = _run_483(
            reading_room_html=_HTML_NO_DRUPAL_SETTINGS,
            legacy_json_rows=rows, legacy_json_total=len(rows),
            existing_ids={"fda483-555003"},   # ROW_B already collected
            max_docs=1,                        # smaller than the 2 remaining candidates
            pdf_side_effect=[("", "fetch-fail:test")],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.backbone, "legacy-json")
        self.assertTrue(report.exhausted)
        self.assertEqual(report.listed, 3)
        self.assertEqual(report.skipped_existing, 1)          # ROW_B absorbed here
        self.assertEqual(report.source_total, 3)
        self.assertEqual(report.remaining, 1)                  # 2 candidates - 1 fetched
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        # newest publish_date among the candidates (ROW_A, 06/05) is fetched first.
        self.assertEqual(posted[0]["document_id"], "fda483-555002")
        pdf.assert_called_once()
        # sleeper: config fetch + (no page fetch attempted) + legacy-json pre-call + 1 doc.
        self.assertEqual(sleeper.calls, [30, 30, 30])

    def test_reading_room_fetch_raising_also_falls_back_to_json(self) -> None:
        rows = [dict(_483_JSON_ROW_A)]
        report, exit_code, post, _pdf, _sleeper = _run_483(
            reading_room_side_effect=RuntimeError("Akamai 403"),
            legacy_json_rows=rows, legacy_json_total=len(rows),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.backbone, "legacy-json")
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["document_id"], "fda483-555002")

    def test_both_backbones_failing_is_exit_2_with_both_error_strings(self) -> None:
        report, exit_code, post, pdf, _sleeper = _run_483(
            reading_room_html=_HTML_NO_DRUPAL_SETTINGS,
            legacy_json_rows=[], legacy_json_total=0,
        )
        self.assertEqual(exit_code, 2)
        self.assertTrue(any("list-config-failed" in e for e in report.errors))
        self.assertIn("json-backbone-failed", report.errors)
        pdf.assert_not_called()
        post.assert_not_called()
        from dataclasses import asdict
        self.assertNotIn(_SERVICE_KEY, json.dumps(asdict(report)))

    def test_dry_run_in_json_mode_never_fetches_or_posts(self) -> None:
        rows = []
        for i in range(12):
            row = dict(_483_JSON_ROW_A)
            row["media_id"] = f"70{i:02d}"
            row["publish_date"] = "06/05/2026"
            rows.append(row)
        report, exit_code, post, pdf, _sleeper = _run_483(
            reading_room_html=_HTML_NO_DRUPAL_SETTINGS,
            legacy_json_rows=rows, legacy_json_total=len(rows),
            dry_run=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.backbone, "legacy-json")
        self.assertEqual(report.listed, 12)
        self.assertEqual(report.fetched, 0)
        self.assertEqual(report.appended, 0)
        self.assertEqual(len(report.would_fetch), 10)          # cap unchanged
        pdf.assert_not_called()
        post.assert_not_called()

    def test_candidates_are_fetched_newest_publish_date_first(self) -> None:
        # Rows handed to run_483 deliberately out of date order (oldest, newest, middle).
        rows = [dict(_483_JSON_ROW_C), dict(_483_JSON_ROW_A), dict(_483_JSON_ROW_B)]
        report, exit_code, _post, _pdf, _sleeper = _run_483(
            reading_room_html=_HTML_NO_DRUPAL_SETTINGS,
            legacy_json_rows=rows, legacy_json_total=len(rows),
            dry_run=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            report.would_fetch,
            ["fda483-555002", "fda483-555003", "fda483-555004"],  # newest -> oldest
        )


class Fda483JsonFallbackAutoModeTest(unittest.TestCase):
    """[2026-09-10] run_auto orchestration around the real run_483 (only fda_wl's runner is
    mocked, mirroring the existing run_auto tests' "WL side mocked" convention) -- proves
    the fallback's exhausted=True terminates the per-source page loop after one attempt
    instead of looping, and that the merged report surfaces backbone."""

    def test_single_fda483_attempt_no_infinite_loop_backbone_recorded(self) -> None:
        rows = [dict(_483_JSON_ROW_A)]
        rwl = mock.MagicMock(return_value=(_auto_rep("fda_wl", exhausted=True), 0))
        with mock.patch("collect_fda_backfill.fetch_existing_document_ids", return_value=set()), \
             mock.patch("collect_fda_backfill.http_get_html", return_value=_HTML_NO_DRUPAL_SETTINGS), \
             mock.patch("collect_fda_483._fetch_legacy_json_rows",
                        return_value=(rows, len(rows))), \
             mock.patch("collect_fda_483._fetch_fda483_pdf_text",
                        return_value=("", "fetch-fail:test")), \
             mock.patch("findings_supabase_append.requests.post",
                        return_value=_FakeResponse(201, [{"raw_signal_id": "x"}])), \
             mock.patch.dict(backfill._RUNNERS, {"fda_wl": rwl}):
            merged, code = backfill.run_auto(
                max_docs=500, delay=0, dry_run=False,
                base_url=_BASE_URL, service_key=_SERVICE_KEY,
            )
        self.assertEqual(code, 0)
        fda483_attempts = [a for a in merged["auto_attempts"] if a["source"] == "fda483"]
        self.assertEqual(len(fda483_attempts), 1)              # no infinite loop
        self.assertEqual(fda483_attempts[0]["backbone"], "legacy-json")
        # Top-level "backbone" mirrors the *last* attempt (fda_wl here, per the fixed
        # 483-then-WL order) -- same "last.X" convention as source/offset/next_offset.
        self.assertEqual(merged["backbone"], "datatables")
        rwl.assert_called_once()

    def test_all_existing_json_candidates_report_fda483_caught_up(self) -> None:
        rows = [dict(_483_JSON_ROW_A), dict(_483_JSON_ROW_B)]
        existing_483 = {"fda483-555002", "fda483-555003"}
        rwl = mock.MagicMock(return_value=(_auto_rep("fda_wl", exhausted=True), 0))

        def fake_existing(base, key, source, **_kw):
            return set(existing_483) if source == "FDA 483" else set()

        with mock.patch("collect_fda_backfill.fetch_existing_document_ids",
                        side_effect=fake_existing), \
             mock.patch("collect_fda_backfill.http_get_html",
                        side_effect=RuntimeError("Akamai 403")), \
             mock.patch("collect_fda_483._fetch_legacy_json_rows",
                        return_value=(rows, len(rows))), \
             mock.patch.dict(backfill._RUNNERS, {"fda_wl": rwl}):
            merged, code = backfill.run_auto(
                max_docs=500, delay=0, dry_run=False,
                base_url=_BASE_URL, service_key=_SERVICE_KEY,
            )
        self.assertEqual(code, 0)
        fda483_attempt = next(a for a in merged["auto_attempts"] if a["source"] == "fda483")
        self.assertEqual(fda483_attempt["skipped_existing"], 2)
        self.assertEqual(fda483_attempt["fetched"], 0)
        self.assertTrue(fda483_attempt["exhausted"])
        # Rebuild the per-attempt dataclass and confirm the orchestration-level predicate
        # (used by run_auto to decide auto_complete) agrees: nothing new + exhausted = caught up.
        rebuilt = backfill.BackfillFetchReport(**fda483_attempt)
        self.assertTrue(backfill._auto_caught_up(rebuilt))
        rwl.assert_called_once()   # 483 caught up -> falls through to WL in the same run


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


# ---------------------------------------------------------------------------
# F2c: --auto mode (unattended daily chunk)
# ---------------------------------------------------------------------------


def _auto_rep(source: str, **kw) -> backfill.BackfillFetchReport:
    """Canned per-source report for run_auto orchestration tests."""
    defaults = dict(offset=0, max_docs=500)
    defaults.update(kw)
    return backfill.BackfillFetchReport(source=source, **defaults)


def _run_auto(*, existing_483=frozenset(), existing_wl=frozenset(),
              run_483_side=(), run_wl_side=(), dry_run=False, max_docs=500,
              fetch_budget=300):
    """run_auto with mocked existing-id fetch and mocked per-source runners;
    returns (merged_report_dict, exit_code, run_483_mock, run_wl_mock)."""
    def fake_existing(base, key, source, **_kw):
        assert base == _BASE_URL and key == _SERVICE_KEY
        return set(existing_483) if source == "FDA 483" else set(existing_wl)

    r483 = mock.MagicMock(side_effect=list(run_483_side))
    rwl = mock.MagicMock(side_effect=list(run_wl_side))
    with mock.patch("collect_fda_backfill.fetch_existing_document_ids",
                    side_effect=fake_existing), \
         mock.patch.dict(backfill._RUNNERS, {"fda483": r483, "fda_wl": rwl}):
        merged, code = backfill.run_auto(
            max_docs=max_docs, delay=0, dry_run=dry_run,
            fetch_budget=fetch_budget,
            base_url=_BASE_URL, service_key=_SERVICE_KEY,
        )
    return merged, code, r483, rwl


class AutoModeBudgetCapTest(unittest.TestCase):
    def test_483_with_new_documents_never_invokes_wl(self) -> None:
        existing = {f"fda483-{i}" for i in range(100)}
        rep = _auto_rep("fda483", offset=100, listed=500, fetched=5, appended=5,
                        next_offset=600, exhausted=False)
        merged, code, r483, rwl = _run_auto(
            existing_483=existing, run_483_side=[(rep, 0)], fetch_budget=5,
        )
        self.assertEqual(code, 0)
        self.assertEqual(r483.call_count, 1)
        kwargs = r483.call_args.kwargs
        self.assertEqual(kwargs["offset"], 0)  # raw count is not a safe WL cursor
        self.assertEqual(kwargs["max_docs"], 500)
        self.assertEqual(kwargs["existing_ids"], existing)
        rwl.assert_not_called()  # budget cap: 483 did the day's real fetching
        self.assertEqual(merged["auto_source_order"], ["fda483"])
        self.assertFalse(merged["auto_complete"])
        self.assertEqual(merged["appended"], 5)

    def test_dry_run_would_fetch_also_stops_before_wl(self) -> None:
        rep = _auto_rep("fda483", listed=500, would_fetch=["fda483-1"], exhausted=False)
        _merged, code, _r483, rwl = _run_auto(run_483_side=[(rep, 0)], dry_run=True)
        self.assertEqual(code, 0)
        rwl.assert_not_called()


class AutoModeSourceTransitionTest(unittest.TestCase):
    def test_483_exhausted_zero_new_falls_through_to_wl_same_run(self) -> None:
        existing_wl = {f"wl-{i}" for i in range(200)}
        rep483 = _auto_rep("fda483", offset=1900, listed=0, exhausted=True, next_offset=1900)
        repwl = _auto_rep("fda_wl", offset=200, listed=500, fetched=3, appended=3,
                          next_offset=700, exhausted=False)
        merged, code, r483, rwl = _run_auto(
            existing_wl=existing_wl,
            run_483_side=[(rep483, 0)], run_wl_side=[(repwl, 0)],
            fetch_budget=3,
        )
        self.assertEqual(code, 0)
        self.assertEqual(r483.call_count, 1)
        self.assertEqual(rwl.call_count, 1)
        self.assertEqual(rwl.call_args.kwargs["offset"], 0)
        self.assertEqual(merged["auto_source_order"], ["fda483", "fda_wl"])
        self.assertFalse(merged["auto_complete"])  # WL fetched -> not complete
        self.assertEqual(merged["appended"], 3)

    def test_both_exhausted_zero_new_is_auto_complete(self) -> None:
        rep483 = _auto_rep("fda483", offset=2000, listed=0, exhausted=True, next_offset=2000)
        repwl = _auto_rep("fda_wl", offset=3600, listed=0, exhausted=True, next_offset=3600)
        merged, code, r483, rwl = _run_auto(
            run_483_side=[(rep483, 0)], run_wl_side=[(repwl, 0)],
        )
        self.assertEqual(code, 0)
        self.assertEqual(r483.call_count, 1)
        self.assertEqual(rwl.call_count, 1)
        self.assertTrue(merged["auto_complete"])
        self.assertEqual(merged["fetched"], 0)
        self.assertEqual(merged["appended"], 0)
        self.assertEqual(merged["would_fetch"], [])
        self.assertEqual(merged["auto_source_order"], ["fda483", "fda_wl"])


class AutoModeForwardScanTest(unittest.TestCase):
    def test_existing_and_gated_pages_do_not_stall_cursor(self) -> None:
        page1 = _auto_rep("fda483", offset=0, listed=500, skipped_existing=450,
                          skipped_gated=50, next_offset=500, exhausted=False)
        page2 = _auto_rep("fda483", offset=500, listed=500, skipped_gated=490,
                          fetched=10, appended=10, next_offset=1000, exhausted=False)
        merged, code, r483, rwl = _run_auto(
            run_483_side=[(page1, 0), (page2, 0)], fetch_budget=10,
        )
        self.assertEqual(code, 0)
        self.assertEqual([c.kwargs["offset"] for c in r483.call_args_list], [0, 500])
        rwl.assert_not_called()
        self.assertEqual(merged["fetched"], 10)
        self.assertIn("fda483:fetch_budget_reached:10/10", merged["auto_transitions"])

    def test_exhausted_483_logs_and_transitions_to_wl(self) -> None:
        rep483 = _auto_rep("fda483", listed=2, skipped_existing=2,
                           next_offset=2, exhausted=True, source_total=2, remaining=0)
        repwl = _auto_rep("fda_wl", listed=1, fetched=1, appended=1,
                          next_offset=1, exhausted=False, source_total=3608, remaining=3607)
        merged, code, _r483, _rwl = _run_auto(
            run_483_side=[(rep483, 0)], run_wl_side=[(repwl, 0)], fetch_budget=1,
        )
        self.assertEqual(code, 0)
        self.assertIn("fda483:exhausted->fda_wl", merged["auto_transitions"])
        self.assertEqual(merged["source_progress"]["fda_wl"]["remaining"], 3607)


class AutoCliExclusionTest(unittest.TestCase):
    def _main_expect_2_no_work(self, argv: list[str]) -> None:
        env = dict(os.environ)
        env["SUPABASE_URL"] = _BASE_URL
        env["SUPABASE_SERVICE_ROLE_KEY"] = _SERVICE_KEY
        r483, rwl = mock.MagicMock(), mock.MagicMock()
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("collect_fda_backfill.run_auto") as run_auto_mock, \
             mock.patch("collect_fda_backfill.fetch_existing_document_ids") as existing_mock, \
             mock.patch.dict(backfill._RUNNERS, {"fda483": r483, "fda_wl": rwl}):
            self.assertEqual(backfill.main(argv), 2)
        run_auto_mock.assert_not_called()
        existing_mock.assert_not_called()
        r483.assert_not_called()
        rwl.assert_not_called()

    def test_auto_with_source_is_exit_2_without_any_work(self) -> None:
        self._main_expect_2_no_work(["--auto", "--source", "fda483"])

    def test_auto_with_offset_is_exit_2_without_any_work(self) -> None:
        self._main_expect_2_no_work(["--auto", "--offset", "100"])

    def test_manual_mode_without_source_is_exit_2(self) -> None:
        self._main_expect_2_no_work(["--dry-run"])


class AutoReportSchemaTest(unittest.TestCase):
    # Every pre-F2c consumer-visible field of the single-source report, which the
    # merged auto report must keep at the top level with the same types.
    # `skipped_ocr_unavailable`(2026-07-30)은 순수 추가 필드다 — 기존 키·타입은 전부
    # 그대로 두고, 병합 리포트도 같은 이름으로 합산해 워크플로가 두 모드를 분기 없이 읽는다.
    # `backbone`(2026-09-10)도 같은 방식의 순수 추가 필드다 -- 483 백필이 JSON 2차
    # 백본으로 폴백했는지 관측하는 용도.
    # SCHEMA_VERSION 은 **올리지 않았다**: raw_signal_id = sha256({schema_version, source,
    # document_id})[:24] 라 버전을 바꾸면 기존 적재분과 dedup 동일성이 깨진다.
    _EXISTING_FIELDS = {
        "schema_version": str, "source": str, "offset": int, "max_docs": int,
        "listed": int, "skipped_existing": int, "skipped_gated": int,
        "skipped_ocr_unavailable": int,
        "fetched": int, "appended": int, "invalid": int, "errors": list,
        "next_offset": int, "exhausted": bool, "backbone": str, "would_fetch": list,
        "source_total": (int, type(None)), "remaining": (int, type(None)),
    }

    def test_merged_report_keeps_existing_fields_and_adds_auto_fields(self) -> None:
        rep483 = _auto_rep("fda483", offset=2000, listed=0, exhausted=True, next_offset=2000)
        repwl = _auto_rep("fda_wl", offset=3600, listed=0, exhausted=True, next_offset=3600)
        merged, _code, _r483, _rwl = _run_auto(
            run_483_side=[(rep483, 0)], run_wl_side=[(repwl, 0)],
        )
        for name, typ in self._EXISTING_FIELDS.items():
            self.assertIn(name, merged, f"missing existing field: {name}")
            self.assertIsInstance(merged[name], typ, f"field type changed: {name}")
        self.assertEqual(merged["schema_version"], "grm-findings-backfill-fetch/v1")
        self.assertIs(merged["auto"], True)
        self.assertEqual(merged["auto_source_order"], ["fda483", "fda_wl"])
        self.assertIs(merged["auto_complete"], True)
        # Per-attempt detail nests the untouched single-source reports.
        self.assertEqual([a["source"] for a in merged["auto_attempts"]],
                         ["fda483", "fda_wl"])
        for attempt in merged["auto_attempts"]:
            self.assertEqual(set(attempt), set(self._EXISTING_FIELDS))

    def test_manual_mode_report_shape_is_unchanged(self) -> None:
        from dataclasses import asdict
        report = backfill.BackfillFetchReport(source="fda483")
        self.assertEqual(set(asdict(report)), set(self._EXISTING_FIELDS))

    def test_cli_auto_writes_merged_report(self) -> None:
        import tempfile
        env = dict(os.environ)
        env["SUPABASE_URL"] = _BASE_URL
        env["SUPABASE_SERVICE_ROLE_KEY"] = _SERVICE_KEY
        fake = {"schema_version": "grm-findings-backfill-fetch/v1", "appended": 0,
                "auto": True, "auto_source_order": ["fda483", "fda_wl"],
                "auto_complete": True}
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "report.json")
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch("collect_fda_backfill.run_auto",
                            return_value=(fake, 0)) as run_auto_mock:
                exit_code = backfill.main(["--auto", "--output", out_path])
            self.assertEqual(exit_code, 0)
            run_auto_mock.assert_called_once()
            self.assertEqual(run_auto_mock.call_args.kwargs["max_docs"], 200)
            self.assertEqual(run_auto_mock.call_args.kwargs["fetch_budget"], 300)
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertIs(data["auto"], True)
        self.assertIs(data["auto_complete"], True)
        self.assertIn("appended", data)  # workflow summary/M12 chain reads this key
        self.assertNotIn(_SERVICE_KEY, json.dumps(data))


class AutoModeExistingIdsFailureTest(unittest.TestCase):
    def test_existing_ids_failure_is_exit_2_without_key_leak(self) -> None:
        r483, rwl = mock.MagicMock(), mock.MagicMock()
        with mock.patch("collect_fda_backfill.fetch_existing_document_ids",
                        side_effect=RuntimeError(f"boom apikey={_SERVICE_KEY}")), \
             mock.patch.dict(backfill._RUNNERS, {"fda483": r483, "fda_wl": rwl}):
            merged, code = backfill.run_auto(
                max_docs=500, delay=0, dry_run=False,
                base_url=_BASE_URL, service_key=_SERVICE_KEY,
            )
        self.assertEqual(code, 2)
        r483.assert_not_called()
        rwl.assert_not_called()
        self.assertFalse(merged["auto_complete"])
        self.assertTrue(any("existing-ids-fetch-failed" in e for e in merged["errors"]))
        self.assertNotIn(_SERVICE_KEY, json.dumps(merged))


class InspectorWiringTest(unittest.TestCase):
    """[실사관 배선 2026-07-31] `run_483` 이 이미 받아둔 원시 PDF text 에서
    `collect_fda_483._extract_483_inspectors` 를 호출해 `_to_item(..., inspectors=...)` 로
    넘기는지 -- 안 넘기면 이 경로로 들어오는 483 문서만 raw_payload 에
    `fda483_inspectors` 가 영구히 빠진다(daily collector 와의 바이트 동일성 계약 위반).
    """

    _SIGNATURE_TEXT = (
        "SEE REVERSE| DATE ISSUED\nJose F Velez,\nInvestigator\n2/27/2026\nOF THIS PAGE"
    )
    _NO_SIGNATURE_TEXT = "Cover page only, no findings section, no signature block."

    def test_inspectors_present_when_signature_block_found(self) -> None:
        report, exit_code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[(self._SIGNATURE_TEXT, "ok")],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.invalid, 0)
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        raw = json.loads(posted[0]["raw_json"])
        self.assertEqual(raw.get("fda483_inspectors"), ["Jose F Velez"])

    def test_inspectors_key_absent_when_none_found(self) -> None:
        report, exit_code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[(self._NO_SIGNATURE_TEXT, "ok")],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.invalid, 0)
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        raw = json.loads(posted[0]["raw_json"])
        # 빈 리스트로 채우지 않는다 -- 키 자체가 없어야 한다(다른 조건부 raw 필드와 동일 관례).
        self.assertNotIn("fda483_inspectors", raw)

    def test_text_fetch_failure_proceeds_without_exception(self) -> None:
        # PDF 텍스트를 아예 못 얻은 경우("scan-no-text" 는 원문 사정 -- 엔진 부재가 아니다)에도
        # run_483 이 예외 없이 진행하고 문서를 적재한다. inspectors 는 당연히 비어 키가 없다.
        report, exit_code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[("", "scan-no-text")],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.errors, [])
        posted = _posted_records(post)
        self.assertEqual(len(posted), 1)
        raw = json.loads(posted[0]["raw_json"])
        self.assertNotIn("fda483_inspectors", raw)

    def test_identical_to_daily_collector_inspectors_field(self) -> None:
        # 일일 수집 경로와 동일 원시 text 를 넣었을 때 fda483_inspectors 값이 바이트 동일한지
        # (Fda483IdentityTest 의 전면 동일성 계약을 이 필드에 한해 직접 재확인).
        with mock.patch("collect_fda_483._fetch_html_rows",
                        return_value=([dict(_483_NROW)], 1, False)), \
             mock.patch("collect_fda_483._fetch_fda483_pdf_text",
                        return_value=(self._SIGNATURE_TEXT, "ok")), \
             mock.patch("collect_fda_483.time.sleep"):
            items, err = fda483.collect_fda_483(date(2026, 5, 1), date(2026, 6, 30))
        self.assertIsNone(err)
        daily_record = findings_store.raw_signal_from_intake_item(items[0])
        daily_raw = json.loads(daily_record["raw_json"])

        _report, exit_code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[(self._SIGNATURE_TEXT, "ok")],
        )
        self.assertEqual(exit_code, 0)
        backfill_raw = json.loads(_posted_records(post)[0]["raw_json"])
        self.assertEqual(backfill_raw.get("fda483_inspectors"),
                          daily_raw.get("fda483_inspectors"))
        self.assertEqual(backfill_raw.get("fda483_inspectors"), ["Jose F Velez"])


class ToItemPositionalContractTest(unittest.TestCase):
    """회귀 가드 -- `_to_item` 의 기존 위치인자 호출 계약(inspectors 없이 5개 위치인자)이
    이번 수리로 깨지지 않았는지. 이 계약이 깨지면 `inspectors` 를 모르는 다른 호출자
    (또는 이 계약을 가정하는 테스트/코드)가 조용히 잘못된 인자를 받게 된다.
    """

    def test_to_item_positional_call_without_inspectors_still_works(self) -> None:
        item = fda483._to_item(dict(_483_NROW), "some excerpt", [], "", "ok")
        self.assertIsNotNone(item)
        self.assertNotIn("fda483_inspectors", item.raw_payload)

    def test_to_item_positional_call_with_inspectors_as_sixth_positional(self) -> None:
        # inspectors 는 키워드 전용으로 배선하고 있지만(collect_fda_backfill.run_483),
        # 시그니처 자체는 여섯 번째 위치인자로도 받는다 -- 이 계약이 깨지지 않았는지 확인.
        item = fda483._to_item(dict(_483_NROW), "some excerpt", [], "", "ok",
                                ["Jose F Velez"])
        self.assertIsNotNone(item)
        self.assertEqual(item.raw_payload.get("fda483_inspectors"), ["Jose F Velez"])


class OcrEngineUnavailableTest(unittest.TestCase):
    """[2026-07-30 실장애] OCR 엔진이 없는 러너에서 스캔 483 을 **빈 본문으로 적재하지 않는다**.

    이 워크플로에는 tesseract 설치 스텝이 없었고(PR #456 이 grm-intake 에만 넣었다) 하루 3회
    무인으로 돌면서 31건을 본문 없이 raw_signals 에 넣었다. raw_signals 는 append-only +
    document_id dedup 이라 **한 번 빈손으로 들어간 문서는 영구히 빈손**이다 — 일일 수집처럼
    조립 시점에 재추출할 기회조차 없다. 보류는 결손이 아니라 '아직 안 함'이다.
    """

    _ENGINE_MISSING = ("", "scan-ocr-unavailable:No tessdata specified and "
                           "Tesseract is not installed")

    def test_engine_missing_scan_483_is_not_appended(self) -> None:
        report, code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[self._ENGINE_MISSING])
        self.assertEqual(code, 0)                       # 잡을 죽이지 않는다(WL 백필 보존)
        self.assertEqual(report.skipped_ocr_unavailable, 1)
        self.assertEqual(report.appended, 0)
        post.assert_not_called()                        # 빈 본문이 DB 로 나가지 않았다
        self.assertEqual(report.errors, [])             # 오류가 아니라 보류다

    def test_skipped_document_stays_collectable(self) -> None:
        """보류한 문서는 적재되지 않았으므로 다음 실행의 existing_ids 에 없다 —
        엔진이 붙으면 같은 문서를 다시 집는다(영구 결손이 아니다)."""
        report, _code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[self._ENGINE_MISSING])
        self.assertEqual(report.appended, 0)
        post.assert_not_called()
        self.assertEqual(report.skipped_existing, 0)

    def test_genuine_source_absence_is_still_appended(self) -> None:
        """`scan-no-text` 는 **원문의 사정**이다(우리 환경 문제가 아니다) — 종전대로 적재한다.

        이 구분이 무너지면 정상 결손까지 영원히 보류돼 백필이 전진하지 못한다.
        """
        report, _code, post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[("", "scan-no-text")])
        self.assertEqual(report.skipped_ocr_unavailable, 0)
        self.assertEqual(report.appended, 1)
        post.assert_called_once()

    def test_report_exposes_skip_count(self) -> None:
        """보류 건수가 리포트 스키마에 있다 — 워크플로가 이 키로 주석을 띄운다."""
        from dataclasses import asdict
        report, _code, _post, _pdf, _sleeper = _run_483(
            pdf_side_effect=[self._ENGINE_MISSING])
        self.assertIn("skipped_ocr_unavailable", asdict(report))


if __name__ == "__main__":
    unittest.main()
