#!/usr/bin/env python3
"""GRM FIND-1 F2b -- chunked historical backfill fetch (FDA 483 / FDA Warning Letter).

Background: live Supabase `findings` volume is far too small (46 rows) for the F3 trend
intelligence track, which needs thousands of findings spanning ~3 years. Two FDA sources
already expose their full history behind the same server-side DataTables AJAX mechanism
(Drupal `datatables/views/ajax`, backed by Solr):
  - FDA 483 OII FOIA Electronic Reading Room -- 2,002 records since 2016 (`length=500` OK).
  - FDA Warning Letter Solr index (`view_name=warning_letter_solr_index`) -- 3,608 letters
    since 2021-01 (`length=500` OK). This is a *different* endpoint from the daily
    collector's static HTML listing (`collect_intake.collect_fda_warning_letters`, which
    only sees the current/recent page) -- it exists only to reach historical volume.

This module fetches those two sources in caller-controlled chunks (--offset/--max-docs)
and appends new `raw_signals` rows directly to Supabase -- Notion is never touched (the
weekly brief queue must not see backfill noise). MFDS/nedrug is out of scope (robots
Disallow: /).

F2c adds an unattended `--auto` mode on top: no --source/--offset needed -- the run
scans list pages from the head (existing/gated rows require no document GET), continues
until a bounded real-document fetch budget, falls through 483 -> WL when 483 is
exhausted, and once both backlogs are
exhausted it exits cleanly after just the list requests with `auto_complete=true` -- so
leaving the daily cron enabled forever is harmless and doubles as a safety net for any
old documents that appear outside the daily collection window. See `run_auto`.

robots.txt for fda.gov specifies `Crawl-Delay: 30`. Both FDA 483 and FDA WL live on the
same host, so a single run only ever touches one --source; the GitHub Actions workflow
enforces this with a `concurrency` group so the two sources can never race the same
per-host rate budget. Every fda.gov request in this module is preceded by a `sleeper`
call (default `time.sleep`, injectable for tests) so the configured --delay (default 30s)
is honoured for the list AJAX call(s) as well as each per-document fetch.

Identity with the daily collector (the whole point of this tool): a raw_signal's
`raw_signal_id` is `sha256({schema_version, source, document_id})[:24]`
(`grm_findings.raw_signal_from_row`) -- it depends on nothing else. So byte-identical
`(source, document_id)` guarantees a byte-identical `raw_signal_id`, which is what makes
this backfill idempotent against (and mergeable with) whatever the daily collector has
already inserted or will insert later for the same document.
  - FDA 483: this module calls `collect_fda_483._to_item(nrow, excerpt, observations, "", status)`
    directly -- the exact function `collect_fda_483.collect_fda_483()` uses per row -- so
    the resulting `IntakeItem` (and therefore its `document_id=f"fda483-{media_id}"`) is
    byte-for-byte what the daily collector would have produced for that row. The only
    "new" code is the AJAX row source (offset-based instead of page-0-walk) and the
    document fetch/dedup wiring around it.
  - FDA Warning Letter: there is no single reusable "build the IntakeItem" function in
    collect_intake.py (the construction is inline in `collect_fda_warning_letters`), so
    `_wl_item_from_row` below mirrors that inline construction field-for-field, but every
    non-trivial piece of logic is still imported and called from collect_intake.py itself:
    `_stable_doc_id`, `_fda_wl_office_gate`, `_is_low_value_fda_warning_letter`,
    `compute_relevance`, `compute_signal_tier`, `_extract_wl_body_full`, `_parse_wl_date`,
    `IntakeItem`, `SOURCE_FDA_WL`, `SRC_TYPE_OFFICIAL_PAGE`, `FDA_WL_URL`. See
    tests/test_collect_fda_backfill.py for the identity assertions.

Both raw_signal record construction (`findings_store.raw_signal_from_intake_item`) and the
Supabase POST transport (`findings_supabase_append._post_rows` /
`_raw_signal_payload` / `_normalize_base_url`) are reused unmodified from the existing
FIND-1 layers -- this module only adds: chunked historical listing, pre-fetch
skip_existing/skip_gated triage, and CLI/report/exit-code plumbing.

The service-role key is never logged, printed, or included in any report field or
exception message -- only exception type names and HTTP status codes are surfaced,
mirroring findings_supabase_append.py's and findings_supabase_backfill.py's convention.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin

import requests

import collect_fda_483 as fda483
import collect_intake as ci
import findings_store
import findings_supabase_append as fsa
import grm_findings as gf
from grm_cli import header_ci as _header_ci
from grm_cli import parse_content_range as _parse_content_range
from grm_common import SOURCE_FDA_483, SOURCE_FDA_WL, http_get_html


SCHEMA_VERSION = "grm-findings-backfill-fetch/v1"

# FDA WL history lives behind a *different* DataTables view than the daily static-HTML
# listing (see module docstring). Column order is assumed to mirror the visible WL table
# (Posted Date / Letter Issue Date / Company Name(+href) / Issuing Office / Subject /
# Response Letter) -- the same convention collect_fda_483.py documents for its own
# `_COL_*` constants ("probe 채록 -- 컬럼 인덱스 고정 순서"). This has not been verified
# against a live response in this offline environment; a first --dry-run against the real
# endpoint should be reviewed before scaling up (see final report "한계").
WL_SOLR_VIEW_NAME = "warning_letter_solr_index"
_WL_COL_POSTED = 0
_WL_COL_LETTER_DATE = 1
_WL_COL_COMPANY = 2
_WL_COL_OFFICE = 3
_WL_COL_SUBJECT = 4
_WL_MIN_COLS = 5
_WL_NUM_COLUMNS = 6
_WL_HREF_RE = re.compile(r'href="([^"]+)"', re.I)

_POST_BATCH_SIZE = 10
_EXISTING_ID_PAGE_SIZE = 1000
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout -- mirrors fsa._post_rows.
_EXISTING_ID_TIMEOUT = 15


@dataclass
class BackfillFetchReport:
    schema_version: str = SCHEMA_VERSION
    source: str = ""
    offset: int = 0
    max_docs: int = 0
    listed: int = 0
    skipped_existing: int = 0
    skipped_gated: int = 0
    fetched: int = 0
    appended: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)
    next_offset: int = 0
    exhausted: bool = False
    source_total: int | None = None
    remaining: int | None = None
    # dry-run only: first 10 document_ids that *would* be fetched (populated only when
    # --dry-run; empty list otherwise so the schema stays stable either way).
    would_fetch: list[str] = field(default_factory=list)


def _default_sleep(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


Sleeper = Callable[[float], None]


# ---------------------------------------------------------------------------
# Supabase: existing document_id set (pre-fetch dedup) -- pagination pattern mirrors
# findings_supabase_backfill._get_page/_fetch_all_pages, reimplemented locally because
# that helper does not accept an extra `source=eq....` filter param alongside `select`.
# ---------------------------------------------------------------------------


def _get_existing_ids_page(
    base_url: str, service_key: str, source: str, *, offset: int, length: int, timeout: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = f"{base_url}/rest/v1/raw_signals"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Range-Unit": "items",
        "Range": f"{offset}-{offset + length - 1}",
        "Prefer": "count=exact",
    }
    params = {"select": "document_id", "source": f"eq.{source}"}
    last_err: str = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            last_err = type(exc).__name__
            if attempt < _MAX_ATTEMPTS:
                continue
            raise RuntimeError(f"existing-ids-get-failed:{last_err}") from None
        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            raise RuntimeError(f"existing-ids-get-failed:http_{resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"existing-ids-get-failed:http_{resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            data = []
        rows = data if isinstance(data, list) else []
        return rows, dict(resp.headers)
    raise RuntimeError("existing-ids-get-failed:retry_exhausted")  # unreachable safety net


def fetch_existing_document_ids(
    base_url: str,
    service_key: str,
    source: str,
    *,
    page_size: int = _EXISTING_ID_PAGE_SIZE,
    timeout: int = _EXISTING_ID_TIMEOUT,
) -> set[str]:
    """Every existing raw_signals.document_id for `source`, fully paginated.

    Raises RuntimeError (never the service key) on any transport/HTTP failure -- callers
    treat that as fatal (exit 2): without this set, skip_existing cannot be evaluated
    before the expensive per-document fetch.
    """
    ids: set[str] = set()
    offset = 0
    total: int | None = None
    while True:
        rows, headers = _get_existing_ids_page(
            base_url, service_key, source, offset=offset, length=page_size, timeout=timeout,
        )
        for row in rows:
            doc_id = str(row.get("document_id") or "")
            if doc_id:
                ids.add(doc_id)
        parsed_total = _parse_content_range(_header_ci(headers, "Content-Range"))
        if parsed_total is not None:
            total = parsed_total
        offset += page_size
        if not rows:
            break
        if total is not None and offset >= total:
            break
        if total is None and len(rows) < page_size:
            break
    return ids


def _post_raw_signals(
    base_url: str, service_key: str, records: list[dict[str, Any]], errors_out: list[str],
) -> int:
    """Batch-POST valid raw_signal records, 10 at a time, on_conflict=raw_signal_id
    (idempotent -- Prefer: resolution=ignore-duplicates is baked into
    findings_supabase_append._post_rows). Returns the count of rows PostgREST reports as
    actually inserted (duplicates already in Supabase simply return no row and are not
    counted or treated as an error).
    """
    appended = 0
    for start in range(0, len(records), _POST_BATCH_SIZE):
        chunk = records[start:start + _POST_BATCH_SIZE]
        payload = [fsa._raw_signal_payload(r) for r in chunk]
        _status, rows, err = fsa._post_rows(
            base_url, service_key, "raw_signals", payload, "raw_signal_id",
        )
        if err:
            errors_out.append(f"raw_signals-post-failed:{err}")
            continue
        appended += len(rows or [])
    return appended


# ---------------------------------------------------------------------------
# FDA 483 -- reuses collect_fda_483's own DataTables AJAX config/query/parse/build
# functions verbatim (same view: ora_foia_electronic_reading_room_solr).
# ---------------------------------------------------------------------------


def run_483(
    *,
    offset: int,
    max_docs: int,
    delay: float,
    dry_run: bool,
    base_url: str,
    service_key: str,
    sleeper: Sleeper | None = None,
    existing_ids: set[str] | None = None,
) -> tuple[BackfillFetchReport, int]:
    sleeper = sleeper or _default_sleep
    report = BackfillFetchReport(source="fda483", offset=offset, max_docs=max_docs)

    if existing_ids is None:  # --auto pre-fetches this set (to compute the offset) and passes it in.
        try:
            existing_ids = fetch_existing_document_ids(base_url, service_key, SOURCE_FDA_483)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"existing-ids-fetch-failed:{type(e).__name__}")
            return report, 2

    try:
        sleeper(delay)
        html = http_get_html(
            fda483.OII_READING_ROOM_URL, timeout=fda483.FDA_483_HTML_TIMEOUT,
            retries=fda483.HTTP_RETRIES, label="FDA483 backfill config",
        )
        config = fda483._datatable_ajax_config(html)
        if config is None:
            raise RuntimeError("fda483-ajax-config-missing")
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"list-config-failed:{type(e).__name__}")
        return report, 2

    try:
        sleeper(delay)
        data = fda483._fetch_datatable_page(config, start=offset, length=max_docs, draw=1)
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"list-page-failed:{type(e).__name__}")
        return report, 2

    raw_rows = data.get("data") if isinstance(data.get("data"), list) else []
    total = data.get("recordsFiltered") or data.get("recordsTotal")
    nrows = fda483._datatable_norm_rows(raw_rows)

    report.listed = len(nrows)
    report.next_offset = offset + len(raw_rows)
    if isinstance(total, (int, float)):
        report.source_total = int(total)
        report.remaining = max(int(total) - report.next_offset, 0)
    if len(raw_rows) < max_docs:
        report.exhausted = True
    elif isinstance(total, (int, float)) and offset + len(raw_rows) >= total:
        report.exhausted = True

    to_post: list[dict[str, Any]] = []
    for nrow in nrows:
        media_id = nrow.get("media_id", "")
        if not media_id:
            continue
        # Mirrors collect_fda_483._to_item's literal document_id formula exactly (verified
        # by tests/test_collect_fda_backfill.py's identity test) -- computed here, before
        # the expensive PDF fetch, purely for the skip_existing pre-check.
        doc_id = f"fda483-{media_id}"
        if doc_id in existing_ids:
            report.skipped_existing += 1
            continue

        if dry_run:
            if len(report.would_fetch) < 10:
                report.would_fetch.append(doc_id)
            continue

        report.fetched += 1
        try:
            sleeper(delay)
            pdf_url = fda483._pdf_url(media_id)
            text, text_status = fda483._fetch_fda483_pdf_text(pdf_url)
            excerpt = fda483._extract_fda483_excerpt(text) if text else ""
            header_hints = {
                "establishment_type": nrow.get("establishment_type", ""),
                "fei_number": nrow.get("fei", ""),
                "firm_name": nrow.get("company", ""),
            }
            observations = (
                fda483._extract_483_observations_from_text(text, header_hints) if text else []
            )
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"483-document-fetch-failed({doc_id}):{type(e).__name__}")
            continue

        # Same construction function the daily collector calls -- guarantees identical
        # IntakeItem (headline/body/raw_payload/document_id/signal_tier/etc.) for this row.
        # [결손 사유 전파 2026-07-20] `text_status` 도 넘긴다 — 일일 수집 경로와 raw_payload
        # 가 **바이트 동일**해야 하는 계약(Fda483IdentityTest)이라, 한쪽만 사유를 실으면 깨진다.
        item = fda483._to_item(nrow, excerpt, observations, "", text_status)
        if item is None:
            # Domain-excluded by the shared QA gate inside _to_item (veterinary/device/food
            # -- same drop the daily collector performs). Not an error; not appended.
            report.skipped_gated += 1
            continue

        record = findings_store.raw_signal_from_intake_item(
            item, collected_at=datetime.now(timezone.utc).isoformat(),
        )
        errors = gf.validate_raw_signal(record)
        if errors:
            report.invalid += 1
            report.errors.append(f"invalid-raw-signal({doc_id}): {'; '.join(errors)}")
            continue
        to_post.append(record)

    if not dry_run and to_post:
        report.appended = _post_raw_signals(base_url, service_key, to_post, report.errors)

    return report, 0


# ---------------------------------------------------------------------------
# FDA Warning Letter -- historical Solr-backed DataTables view. No daily-path equivalent
# exists (the daily collector only scrapes the current static HTML listing), so the AJAX
# config/query plumbing here is new, but every judgment/construction rule below is
# imported and called from collect_intake.py, not reimplemented.
# ---------------------------------------------------------------------------


def _extract_datatable_config(html_text: str, view_name: str) -> dict[str, Any] | None:
    """Same Drupal-settings-json parsing collect_fda_483._datatable_ajax_config uses,
    parameterized by view_name (483's version hardcodes its own view name).
    """
    m = fda483._DRUPAL_SETTINGS_RE.search(html_text or "")
    if not m:
        return None
    try:
        settings = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    for dt in (settings.get("datatables") or {}).values():
        if not isinstance(dt, dict):
            continue
        ajax = dt.get("ajax") or {}
        params = ajax.get("data") or {}
        if params.get("view_name") == view_name:
            return {
                "url": urljoin(fda483.FDA_MEDIA_BASE, ajax.get("url") or fda483.DATATABLE_AJAX_PATH),
                "params": dict(params),
            }
    return None


def _datatable_query_params(
    base_params: dict[str, Any], *, start: int, length: int, draw: int,
    num_columns: int, order_column: int = 0,
) -> dict[str, Any]:
    """Generic DataTables server-side protocol params -- same shape as
    collect_fda_483._datatable_query, generalized over column count/order/no fixed filter
    (483's version hardcodes 9 columns + a foia_record_type_name filter).
    """
    out = dict(base_params)
    out.update({
        "draw": str(draw), "start": str(start), "length": str(length),
        "search[value]": "", "search[regex]": "false",
        "order[0][column]": str(order_column), "order[0][dir]": "desc",
    })
    for i in range(num_columns):
        out[f"columns[{i}][data]"] = str(i)
        out[f"columns[{i}][name]"] = ""
        out[f"columns[{i}][searchable]"] = "true"
        out[f"columns[{i}][orderable]"] = "true"
        out[f"columns[{i}][search][value]"] = ""
        out[f"columns[{i}][search][regex]"] = "false"
    return out


def _wl_row_from_ajax(raw: Any) -> dict[str, str] | None:
    """One warning_letter_solr_index AJAX data row -> normalized dict, or None if the
    row is not a data row (header/malformed) or has no parseable posted/letter date.
    """
    if not isinstance(raw, list) or len(raw) < _WL_MIN_COLS:
        return None
    posted_raw = fda483._strip(raw[_WL_COL_POSTED])
    if not re.match(r"^\d", posted_raw):
        return None
    letter_date_raw = fda483._strip(raw[_WL_COL_LETTER_DATE])
    company_cell = str(raw[_WL_COL_COMPANY])
    firm = fda483._strip(company_cell)
    href_m = _WL_HREF_RE.search(company_cell)
    wl_href = href_m.group(1) if href_m else ""
    if wl_href.startswith("/"):
        wl_href = "https://www.fda.gov" + wl_href
    issuing_office = fda483._strip(raw[_WL_COL_OFFICE])
    subject = fda483._strip(raw[_WL_COL_SUBJECT])
    date_iso = ci._parse_wl_date(posted_raw) or ci._parse_wl_date(letter_date_raw)
    if not date_iso:
        return None
    return {
        "firm": firm,
        "wl_href": wl_href,
        "issuing_office": issuing_office,
        "subject": subject,
        "posted_date": posted_raw,
        "letter_date": letter_date_raw,
        "date_iso": date_iso,
    }


def _wl_item_from_row(nrow: dict[str, str], body_full: str, office_gate_verdict: str) -> Any:
    """Mirrors collect_intake.collect_fda_warning_letters' inline IntakeItem construction
    field-for-field (see module docstring) -- every non-trivial rule is imported from
    collect_intake, not reimplemented.
    """
    firm = nrow["firm"]
    wl_href = nrow["wl_href"]
    issuing_office = nrow["issuing_office"]
    subject = nrow["subject"]
    date_iso = nrow["date_iso"]
    headline = subject or firm or "FDA Warning Letter"

    doc_id = ci._stable_doc_id(SOURCE_FDA_WL, firm, wl_href or ci.FDA_WL_URL, date_iso)
    relevance = ci.compute_relevance(headline, subject, issuing_office)
    tier = ci.compute_signal_tier(
        SOURCE_FDA_WL, issuing_office or "Warning Letter", relevance, "N/A",
        headline, subject, issuing_office,
    )

    wl_raw: dict[str, Any] = {
        "firm": firm,
        "posted_date": nrow.get("posted_date", ""),
        "letter_date": nrow.get("letter_date", ""),
        "issuing_office": issuing_office,
        "subject": subject,
        "url": wl_href,
    }
    if office_gate_verdict in ("review", "unknown"):
        wl_raw["office_gate_verdict"] = office_gate_verdict
    if body_full:
        wl_raw["wl_body_full"] = body_full

    return ci.IntakeItem(
        source=SOURCE_FDA_WL,
        document_id=doc_id,
        date_iso=date_iso,
        headline=headline,
        official_url=wl_href or ci.FDA_WL_URL,
        type_or_class=issuing_office or "Warning Letter",
        firm=firm,
        body=subject,
        api_query=ci.FDA_WL_URL,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=ci.SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=tier,
        raw_payload=wl_raw,
    )


def run_wl(
    *,
    offset: int,
    max_docs: int,
    delay: float,
    dry_run: bool,
    base_url: str,
    service_key: str,
    sleeper: Sleeper | None = None,
    existing_ids: set[str] | None = None,
) -> tuple[BackfillFetchReport, int]:
    sleeper = sleeper or _default_sleep
    report = BackfillFetchReport(source="fda_wl", offset=offset, max_docs=max_docs)

    if existing_ids is None:  # --auto pre-fetches this set (to compute the offset) and passes it in.
        try:
            existing_ids = fetch_existing_document_ids(base_url, service_key, SOURCE_FDA_WL)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"existing-ids-fetch-failed:{type(e).__name__}")
            return report, 2

    try:
        sleeper(delay)
        html = http_get_html(ci.FDA_WL_URL, timeout=30, retries=3, label="FDA WL backfill config")
        config = _extract_datatable_config(html, WL_SOLR_VIEW_NAME)
        if config is None:
            raise RuntimeError("fda-wl-ajax-config-missing")
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"list-config-failed:{type(e).__name__}")
        return report, 2

    try:
        sleeper(delay)
        params = _datatable_query_params(
            config["params"], start=offset, length=max_docs, draw=1,
            num_columns=_WL_NUM_COLUMNS, order_column=_WL_COL_POSTED,
        )
        url = config["url"] + "?" + urlencode(params)
        text = http_get_html(
            url, timeout=30, retries=3,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": ci.FDA_WL_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            label="FDA WL DataTables",
        )
        data = json.loads(text)
        if not isinstance(data, dict):
            data = {}
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"list-page-failed:{type(e).__name__}")
        return report, 2

    raw_rows = data.get("data") if isinstance(data.get("data"), list) else []
    total = data.get("recordsFiltered") or data.get("recordsTotal")
    nrows = [r for r in (_wl_row_from_ajax(x) for x in raw_rows) if r]

    report.listed = len(nrows)
    report.next_offset = offset + len(raw_rows)
    if isinstance(total, (int, float)):
        report.source_total = int(total)
        report.remaining = max(int(total) - report.next_offset, 0)
    if len(raw_rows) < max_docs:
        report.exhausted = True
    elif isinstance(total, (int, float)) and offset + len(raw_rows) >= total:
        report.exhausted = True

    to_post: list[dict[str, Any]] = []
    for nrow in nrows:
        firm = nrow["firm"]
        wl_href = nrow["wl_href"]
        date_iso = nrow["date_iso"]
        doc_id = ci._stable_doc_id(SOURCE_FDA_WL, firm, wl_href or ci.FDA_WL_URL, date_iso)
        if doc_id in existing_ids:
            report.skipped_existing += 1
            continue

        headline = nrow["subject"] or firm or "FDA Warning Letter"
        verdict = ci._fda_wl_office_gate(nrow["issuing_office"], headline, nrow["subject"], firm)
        gated = verdict == "exclude" or (
            verdict == "unknown"
            and ci._is_low_value_fda_warning_letter(headline, nrow["subject"], nrow["issuing_office"], firm)
        )
        if gated:
            report.skipped_gated += 1
            continue

        if dry_run:
            if len(report.would_fetch) < 10:
                report.would_fetch.append(doc_id)
            continue

        report.fetched += 1
        body_full = ""
        if wl_href:
            try:
                sleeper(delay)
                letter_html = http_get_html(wl_href, timeout=20, retries=3, label="FDA WL backfill body")
                body_full = ci._extract_wl_body_full(letter_html)
            except Exception as e:  # noqa: BLE001
                # Graceful degrade (matches collect_intake's own WL body fetch failure
                # handling) -- item still gets appended without wl_body_full.
                report.errors.append(f"wl-document-fetch-failed({doc_id}):{type(e).__name__}")

        item = _wl_item_from_row(nrow, body_full, verdict)
        record = findings_store.raw_signal_from_intake_item(
            item, collected_at=datetime.now(timezone.utc).isoformat(),
        )
        errors = gf.validate_raw_signal(record)
        if errors:
            report.invalid += 1
            report.errors.append(f"invalid-raw-signal({doc_id}): {'; '.join(errors)}")
            continue
        to_post.append(record)

    if not dry_run and to_post:
        report.appended = _post_raw_signals(base_url, service_key, to_post, report.errors)

    return report, 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_RUNNERS: dict[str, Callable[..., tuple[BackfillFetchReport, int]]] = {
    "fda483": run_483,
    "fda_wl": run_wl,
}


# ---------------------------------------------------------------------------
# F2c: --auto mode -- unattended daily chunk (cron), no --source/--offset needed.
# ---------------------------------------------------------------------------

# 483 first, then WL -- fixed deterministic order (483 backlog is the smaller one, so it
# finishes first and the cron then spends its daily budget draining WL).
_AUTO_SOURCES: tuple[tuple[str, str], ...] = (
    ("fda483", SOURCE_FDA_483),
    ("fda_wl", SOURCE_FDA_WL),
)
# Defensive cap for malformed/non-advancing listing responses. Normal termination is
# exhaustion or the real-document fetch budget, not this cap.
_AUTO_MAX_PAGES = 100


def _auto_no_new(report: BackfillFetchReport) -> bool:
    """Nothing new found in this window: no real document fetch, nothing appended, and
    (under --dry-run) nothing that would have been fetched."""
    return report.fetched == 0 and report.appended == 0 and not report.would_fetch


def _auto_caught_up(report: BackfillFetchReport) -> bool:
    """The source's listing ran out and nothing new was found -- fully backfilled."""
    return report.exhausted and _auto_no_new(report)


def _auto_offset_overshot(report: BackfillFetchReport) -> bool:
    """listed > 0, every listed row was skip_existing, and the listing has more pages:
    the computed offset landed inside already-collected territory (e.g. today's daily
    collection inflated the existing-id count, pushing the offset past newer uncollected
    rows into old ones we already have). Advancing by max_docs moves toward the
    uncollected tail. Undershoot needs no counterpart -- skip_existing absorbs it.
    """
    return (
        report.listed > 0
        and report.skipped_existing == report.listed
        and not report.exhausted
    )


def run_auto(
    *,
    max_docs: int,
    delay: float,
    dry_run: bool,
    base_url: str,
    service_key: str,
    fetch_budget: int = 300,
    sleeper: Sleeper | None = None,
) -> tuple[dict[str, Any], int]:
    """One unattended backfill chunk (deterministic; designed for a daily cron).

    Per source (483 first, then WL):
      1. Start each source at offset zero and scan forward. Existing/gated rows need
         no document GET, so they are cheap; this avoids confusing stored-row count
         with a durable cursor.
      2. Continue through list pages until source exhaustion or the bounded real-
         document fetch budget. Every list/document request still sleeps `delay` first.
      3. Fall through 483 -> WL only after 483 exhaustion. Dry-run stops at the first
         page that would fetch documents so it remains a cheap probe.
      4. auto_complete=true only when every source was attempted and each ended
         exhausted-with-nothing-new: the whole backlog is done and this run cost only
         a couple of list requests. Leaving the cron enabled in that steady state is
         intentional -- it doubles as a safety net that picks up any newly-appearing
         old documents outside the daily collection window.

    Returns (merged report dict, exit_code). The merged dict keeps every
    BackfillFetchReport field at the top level (counters summed across attempts;
    offset/next_offset/exhausted/source from the last attempt) so existing consumers
    of the single-source schema -- e.g. the workflow's `['appended']` read -- keep
    working unchanged; per-attempt detail is nested under `auto_attempts`.
    """
    attempts: list[BackfillFetchReport] = []
    order: list[str] = []
    transitions: list[str] = []
    existing_counts: dict[str, int] = {}
    exit_code = 0
    caught_up_all = True

    for cli_name, source_const in _AUTO_SOURCES:
        order.append(cli_name)
        try:
            existing_ids = fetch_existing_document_ids(base_url, service_key, source_const)
        except Exception as e:  # noqa: BLE001
            report = BackfillFetchReport(source=cli_name, offset=0, max_docs=max_docs)
            report.errors.append(f"existing-ids-fetch-failed:{type(e).__name__}")
            attempts.append(report)
            exit_code = 2
            caught_up_all = False
            break
        existing_counts[cli_name] = len(existing_ids)

        runner = _RUNNERS[cli_name]
        # Always scan from the head. Existing/gated rows require no document GET, so
        # this costs one politely delayed list request per page and avoids treating
        # raw row count as a cursor (invalid for WL because many rows are gated out).
        offset = 0
        source_fetched = 0
        pages = 0
        while pages < _AUTO_MAX_PAGES:
            report, code = runner(
                offset=offset, max_docs=max_docs, delay=delay, dry_run=dry_run,
                base_url=base_url, service_key=service_key, sleeper=sleeper,
                existing_ids=existing_ids,
            )
            attempts.append(report)
            pages += 1
            if code != 0:
                exit_code = code
                break
            source_fetched += report.fetched
            if report.exhausted or report.next_offset <= offset:
                break
            if dry_run and report.would_fetch:
                break
            if not dry_run and source_fetched >= fetch_budget:
                transitions.append(
                    f"{cli_name}:fetch_budget_reached:{source_fetched}/{fetch_budget}"
                )
                break
            offset = report.next_offset

        if exit_code != 0:
            caught_up_all = False
            break
        last = attempts[-1]
        if not _auto_caught_up(last):
            caught_up_all = False
        if last.exhausted and cli_name != _AUTO_SOURCES[-1][0]:
            transitions.append(f"{cli_name}:exhausted->fda_wl")
        if (dry_run and any(r.would_fetch for r in attempts if r.source == cli_name)) or (
            not dry_run and source_fetched >= fetch_budget
        ):
            break

    auto_complete = caught_up_all and exit_code == 0 and len(order) == len(_AUTO_SOURCES)

    last = attempts[-1]
    merged: dict[str, Any] = {
        # Existing single-source schema, kept intact (additive changes only below).
        "schema_version": SCHEMA_VERSION,
        "source": last.source,
        "offset": last.offset,
        "max_docs": max_docs,
        "listed": sum(r.listed for r in attempts),
        "skipped_existing": sum(r.skipped_existing for r in attempts),
        "skipped_gated": sum(r.skipped_gated for r in attempts),
        "fetched": sum(r.fetched for r in attempts),
        "appended": sum(r.appended for r in attempts),
        "invalid": sum(r.invalid for r in attempts),
        "errors": [e for r in attempts for e in r.errors],
        "next_offset": last.next_offset,
        "exhausted": last.exhausted,
        "would_fetch": [d for r in attempts for d in r.would_fetch][:10],
        "source_total": last.source_total,
        "remaining": last.remaining,
        # F2c additive fields.
        "auto": True,
        "auto_source_order": order,
        "auto_complete": auto_complete,
        "auto_fetch_budget": fetch_budget,
        "auto_transitions": transitions,
        "source_progress": {
            name: {
                "existing_before": existing_counts.get(name, 0),
                "listed_through": max(
                    (r.next_offset for r in attempts if r.source == name), default=0
                ),
                "total": next(
                    (r.source_total for r in reversed(attempts)
                     if r.source == name and r.source_total is not None), None
                ),
                "remaining": next(
                    (r.remaining for r in reversed(attempts)
                     if r.source == name and r.remaining is not None), None
                ),
                "fetched": sum(r.fetched for r in attempts if r.source == name),
                "appended": sum(r.appended for r in attempts if r.source == name),
            }
            for name in order
        },
        "auto_attempts": [asdict(r) for r in attempts],
    }
    return merged, exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GRM FIND-1 F2b -- chunked backfill fetch of historical FDA 483 / "
        "Warning Letter documents directly into Supabase raw_signals (bypasses Notion; "
        "robots Crawl-Delay=30 aware; one source per run -- 483/WL share a host)."
    )
    # --source is required unless --auto (checked in main -- explicit exit-2 check,
    # matching the existing credential-validation style).
    parser.add_argument("--source", choices=("fda483", "fda_wl"))
    parser.add_argument("--offset", type=int, default=None)  # manual mode default: 0
    parser.add_argument("--max-docs", type=int, default=200)
    parser.add_argument(
        "--auto-fetch-budget", type=int, default=300,
        help="Auto mode only: maximum real document fetches per source/run; list pages "
        "continue until this budget or source exhaustion (default: 300).",
    )
    parser.add_argument(
        "--auto", action="store_true", default=False,
        help="F2c unattended mode (daily cron): computes offset per source from the "
        "already-collected id count, 483 first then WL, at most one source fetches per "
        "run; exits cleanly with auto_complete=true once both backlogs are exhausted. "
        "Mutually exclusive with --source/--offset.",
    )
    parser.add_argument("--delay", type=float, default=30)
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="List + triage only (skip_existing/skip_gated) -- never fetch documents or POST.",
    )
    parser.add_argument("--supabase-url", help="Falls back to $SUPABASE_URL")
    parser.add_argument("--service-role-key", help="Falls back to $SUPABASE_SERVICE_ROLE_KEY")
    parser.add_argument("--output", help="Optional path to also write the JSON report to.")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    if args.auto and (args.source is not None or args.offset is not None):
        print(
            "collect_fda_backfill: --auto cannot be combined with --source/--offset "
            "(auto computes both itself)",
            file=sys.stderr,
        )
        return 2
    if not args.auto and args.source is None:
        print("collect_fda_backfill: --source is required (or use --auto)", file=sys.stderr)
        return 2

    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        print(
            "collect_fda_backfill: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base = fsa._normalize_base_url(url)
    if base is None:
        print("collect_fda_backfill: SUPABASE_URL must start with https://", file=sys.stderr)
        return 2

    if args.auto:
        report_dict, exit_code = run_auto(
            max_docs=args.max_docs, delay=args.delay,
            fetch_budget=args.auto_fetch_budget,
            dry_run=args.dry_run, base_url=base, service_key=key,
        )
    else:
        runner = _RUNNERS[args.source]
        report, exit_code = runner(
            offset=args.offset if args.offset is not None else 0,
            max_docs=args.max_docs, delay=args.delay,
            dry_run=args.dry_run, base_url=base, service_key=key,
        )
        report_dict = asdict(report)

    payload = json.dumps(report_dict, ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return exit_code


__all__ = [
    "BackfillFetchReport",
    "fetch_existing_document_ids",
    "run_483",
    "run_wl",
    "run_auto",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
