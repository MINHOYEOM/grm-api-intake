#!/usr/bin/env python3
"""FIND-1 M4a Supabase(PostgREST) direct append helpers for raw_signals and findings.

This module is side-effectful by design, but only when the caller explicitly
passes a Supabase base URL and a service-role key. It calls PostgREST over
HTTPS; it does not read/write SQLite and does not query Notion.

Record construction is fully delegated to the existing FIND-1 layers —
``findings_store.raw_signal_from_intake_item`` for raw_signals and
``findings_extractors.findings_from_raw_signal`` for findings — so this module
only adds the HTTP transport and the status/result vocabulary needed by
``collect_intake``. Records are validated locally with
``grm_findings.validate_raw_signal``/``validate_finding`` before any network
call; invalid records are never POSTed.

Status vocabulary (superset of findings_store's SQLite vocabulary, adding
"error" for HTTP/network failures that survive the retry budget):
  - RawSignalAppendResult (SQLite):              inserted | duplicate | invalid
  - SupabaseRawSignalAppendResult (this module):  inserted | duplicate | invalid | error
  - RawSignalWithFindingsAppendResult (SQLite):  inserted | duplicate | invalid | partial | raw_signal_inserted
  - SupabaseRawSignalWithFindingsAppendResult:    inserted | duplicate | invalid | partial | raw_signal_inserted | error

The service-role key is never included in any log line, exception message, or
result object — only exception types and HTTP status codes are summarized.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

import findings_extractors
import findings_store
import grm_findings as gf


DEFAULT_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only

_FINDING_JSONB_COLUMNS = ("inspector_names", "cfr_refs", "mfds_refs")


@dataclass(frozen=True)
class SupabaseRawSignalAppendResult:
    status: str
    raw_signal_id: str = ""
    errors: tuple[str, ...] = ()

    @property
    def inserted(self) -> bool:
        return self.status == "inserted"


@dataclass(frozen=True)
class SupabaseRawSignalWithFindingsAppendResult:
    status: str
    raw_signal_id: str = ""
    raw_signal_status: str = ""
    findings_inserted: int = 0
    findings_duplicate: int = 0
    findings_invalid: int = 0
    errors: tuple[str, ...] = ()


def _normalize_base_url(base_url: str) -> str | None:
    text = str(base_url or "").strip()
    if not text.lower().startswith("https://"):
        return None
    return text.rstrip("/")


def _raw_signal_payload(record: dict[str, Any]) -> dict[str, Any]:
    """raw_signals row for PostgREST. raw_json/row_json stay as text; no jsonb here."""
    return {
        key: record.get(key)
        for key in findings_store.RAW_SIGNAL_SQLITE_COLUMNS
        if key in record
    }


def _finding_payload(record: dict[str, Any]) -> dict[str, Any]:
    """findings row for PostgREST. inspector_names/cfr_refs/mfds_refs stay Python
    lists (jsonb columns) — never JSON-serialized to text, unlike the SQLite path.
    """
    payload: dict[str, Any] = {}
    for key in findings_store.FINDING_SQLITE_COLUMNS:
        if key not in record:
            continue
        value = record.get(key)
        if key in _FINDING_JSONB_COLUMNS:
            payload[key] = list(value) if isinstance(value, list) else []
        elif key == "confidence":
            payload[key] = float(value) if value is not None else 0.0
        else:
            payload[key] = value
    return payload


def _post_rows(
    base_url: str,
    service_key: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """POST rows to PostgREST.

    Returns (status_code, returned_rows_or_None, error_summary). error_summary
    is "" on success (2xx). On failure it is either "timeout", an exception
    type name, or "http_{status}" — never exception text, so a service-role
    key embedded in a lower-level transport error can never leak through it.

    Retries once (total 2 attempts) for 5xx responses or a request timeout.
    Any other exception or 4xx status fails immediately without retry.
    """
    url = f"{base_url}/rest/v1/{table}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=representation",
    }
    params = {"on_conflict": on_conflict} if on_conflict else None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, params=params, json=rows, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, "timeout"
        except requests.exceptions.RequestException as exc:
            # Non-timeout transport errors fail immediately — no retry.
            return 0, None, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return resp.status_code, [], ""
        return resp.status_code, (data if isinstance(data, list) else []), ""

    return 0, None, "retry_exhausted"  # unreachable safety net


def _append_raw_signal(
    base_url: str,
    service_key: str,
    record: dict[str, Any],
) -> SupabaseRawSignalAppendResult:
    errors = tuple(gf.validate_raw_signal(record))
    raw_signal_id = str(record.get("raw_signal_id") or "")
    if errors:
        return SupabaseRawSignalAppendResult("invalid", raw_signal_id=raw_signal_id, errors=errors)

    status, rows, err = _post_rows(
        base_url, service_key, "raw_signals", [_raw_signal_payload(record)], "raw_signal_id",
    )
    if err:
        return SupabaseRawSignalAppendResult(
            "error", raw_signal_id=raw_signal_id, errors=(f"raw_signals POST failed: {err}",),
        )
    if rows:
        return SupabaseRawSignalAppendResult("inserted", raw_signal_id=raw_signal_id)
    return SupabaseRawSignalAppendResult("duplicate", raw_signal_id=raw_signal_id)


def _append_findings_row_by_row(
    base_url: str,
    service_key: str,
    findings: list[dict[str, Any]],
) -> tuple[int, int, bool, list[str]]:
    inserted = 0
    duplicate = 0
    had_error = False
    errors: list[str] = []
    for finding in findings:
        status, rows, err = _post_rows(
            base_url, service_key, "findings", [_finding_payload(finding)], "finding_id",
        )
        if status == 409:
            # Row-level conflict on the md5(finding_text) unique index that the
            # batch on_conflict target (finding_id) cannot express — treat as
            # a content-level duplicate, matching the SQLite dedupe semantics.
            duplicate += 1
            continue
        if err:
            had_error = True
            errors.append(f"findings row POST failed finding_id={finding.get('finding_id', '')}: {err}")
            continue
        if rows:
            inserted += 1
        else:
            duplicate += 1
    return inserted, duplicate, had_error, errors


def _append_findings_batch(
    base_url: str,
    service_key: str,
    findings: list[dict[str, Any]],
) -> tuple[int, int, bool, list[str]]:
    if not findings:
        return 0, 0, False, []

    status, rows, err = _post_rows(
        base_url, service_key, "findings", [_finding_payload(f) for f in findings], "finding_id",
    )
    if status == 409:
        return _append_findings_row_by_row(base_url, service_key, findings)
    if err:
        return 0, 0, True, [f"findings batch POST failed: {err}"]

    inserted = len(rows or [])
    duplicate = len(findings) - inserted
    return inserted, duplicate, False, []


def _append_raw_signal_with_findings(
    base_url: str,
    service_key: str,
    raw_signal: dict[str, Any],
    findings: list[dict[str, Any]],
) -> SupabaseRawSignalWithFindingsAppendResult:
    raw_result = _append_raw_signal(base_url, service_key, raw_signal)
    raw_signal_id = raw_result.raw_signal_id or str(raw_signal.get("raw_signal_id") or "")
    if raw_result.status in ("invalid", "error"):
        return SupabaseRawSignalWithFindingsAppendResult(
            raw_result.status,
            raw_signal_id=raw_signal_id,
            raw_signal_status=raw_result.status,
            errors=raw_result.errors,
        )

    invalid = 0
    errors: list[str] = []
    valid_findings: list[dict[str, Any]] = []
    for finding in findings:
        if str(finding.get("raw_signal_id") or "") != raw_signal_id:
            invalid += 1
            errors.append("findings.raw_signal_id must match raw_signals.raw_signal_id")
            continue
        finding_errors = gf.validate_finding(finding)
        if finding_errors:
            invalid += 1
            errors.extend(finding_errors)
            continue
        valid_findings.append(finding)

    inserted, duplicate, had_error, batch_errors = _append_findings_batch(
        base_url, service_key, valid_findings,
    )
    errors.extend(batch_errors)

    if had_error and not inserted and not duplicate:
        status = "error"
    elif invalid and not (inserted or duplicate or raw_result.status == "inserted"):
        status = "invalid"
    elif invalid or had_error:
        status = "partial"
    elif inserted:
        status = "inserted"
    elif raw_result.status == "inserted":
        status = "raw_signal_inserted"
    else:
        status = "duplicate"

    return SupabaseRawSignalWithFindingsAppendResult(
        status,
        raw_signal_id=raw_signal_id,
        raw_signal_status=raw_result.status,
        findings_inserted=inserted,
        findings_duplicate=duplicate,
        findings_invalid=invalid,
        errors=tuple(errors),
    )


def append_intake_item_to_supabase(
    base_url: str,
    service_key: str,
    item: Any,
    *,
    collected_at: Any = "",
) -> SupabaseRawSignalAppendResult:
    """Append one Intake item's raw_signal only, via PostgREST."""
    base = _normalize_base_url(base_url)
    if base is None:
        return SupabaseRawSignalAppendResult(
            "invalid", errors=("SUPABASE_URL must start with https://",),
        )
    record = findings_store.raw_signal_from_intake_item(item, collected_at=collected_at)
    return _append_raw_signal(base, service_key, record)


def append_intake_item_with_findings_to_supabase(
    base_url: str,
    service_key: str,
    item: Any,
    *,
    collected_at: Any = "",
) -> SupabaseRawSignalWithFindingsAppendResult:
    """Append one Intake item's raw_signal and derived findings, via PostgREST.

    collect_intake calls this helper only behind explicit FIND-1 feature flags.
    If the raw_signal is invalid or the raw_signal POST errors out, findings
    are never attempted.
    """
    base = _normalize_base_url(base_url)
    if base is None:
        return SupabaseRawSignalWithFindingsAppendResult(
            "invalid", errors=("SUPABASE_URL must start with https://",),
        )
    record = findings_store.raw_signal_from_intake_item(item, collected_at=collected_at)
    findings = findings_extractors.findings_from_raw_signal(record)
    return _append_raw_signal_with_findings(base, service_key, record, findings)
