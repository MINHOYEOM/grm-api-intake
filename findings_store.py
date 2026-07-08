#!/usr/bin/env python3
"""FIND-1 M1c/M1e/M1g SQLite append helpers for raw_signals and findings.

This module is side-effectful by design, but only when the caller explicitly
passes a SQLite path.  It does not query Notion or Supabase.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import findings_extractors
import grm_findings as gf


DEFAULT_FINDINGS_SQLITE_PATH = "grm-findings.sqlite3"

RAW_SIGNAL_SQLITE_COLUMNS = (
    "schema_version",
    "raw_signal_id",
    "source",
    "source_kind",
    "document_id",
    "published_date",
    "collected_at",
    "title",
    "firm_name",
    "site_name",
    "site_country",
    "modality",
    "source_url",
    "official_url",
    "raw_sha256",
    "raw_json",
    "row_json",
    "extraction_status",
)

FINDING_SQLITE_COLUMNS = (
    "schema_version",
    "taxonomy_version",
    "finding_id",
    "raw_signal_id",
    "source",
    "agency",
    "document_type",
    "document_id",
    "published_date",
    "firm_name",
    "entity_id",
    "site_name",
    "site_country",
    "product_family",
    "modality",
    "category_code",
    "category_label_ko",
    "finding_text",
    "finding_language",
    "evidence_level",
    "evidence_url",
    "inspector_names",
    "cfr_refs",
    "mfds_refs",
    "extraction_method",
    "confidence",
    "review_status",
    "finding_text_ko",
    "translation_method",
)


@dataclass(frozen=True)
class RawSignalAppendResult:
    status: str
    raw_signal_id: str = ""
    errors: tuple[str, ...] = ()

    @property
    def inserted(self) -> bool:
        return self.status == "inserted"


@dataclass(frozen=True)
class RawSignalWithFindingsAppendResult:
    status: str
    raw_signal_id: str = ""
    raw_signal_status: str = ""
    findings_inserted: int = 0
    findings_duplicate: int = 0
    findings_invalid: int = 0
    errors: tuple[str, ...] = ()


def _text_attr(item: Any, name: str) -> str:
    return str(getattr(item, name, "") or "").strip()


def _collected_at_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value or "").strip()


def raw_signal_from_intake_item(item: Any, *, collected_at: Any = "") -> dict[str, Any]:
    raw = getattr(item, "raw_payload", {}) or {}
    if not isinstance(raw, dict):
        return {
            "schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
            "raw_signal_id": "",
            "source": _text_attr(item, "source"),
            "source_kind": _text_attr(item, "type_or_class"),
            "document_id": _text_attr(item, "document_id"),
            "published_date": _text_attr(item, "date_iso"),
            "title": _text_attr(item, "headline"),
            "raw_sha256": "",
            "raw_json": "",
            "row_json": "",
            "extraction_status": "invalid_raw_payload",
        }

    row = {
        "source": _text_attr(item, "source"),
        "document_id": _text_attr(item, "document_id"),
        "date": _text_attr(item, "date_iso"),
        "headline": _text_attr(item, "headline"),
        "official_url": _text_attr(item, "official_url"),
        "type_or_class": _text_attr(item, "type_or_class"),
        "firm": _text_attr(item, "firm"),
        "body": _text_attr(item, "body"),
        "distribution": _text_attr(item, "distribution"),
        "comments_close": _text_attr(item, "comments_close_iso"),
        "api_query": _text_attr(item, "api_query"),
        "qa_relevance": _text_attr(item, "qa_relevance"),
        "osd_relevance": _text_attr(item, "osd_relevance"),
        "source_type": _text_attr(item, "source_type"),
        "signal_tier": _text_attr(item, "signal_tier"),
        "source_url": _text_attr(item, "source_url"),
        "raw_excerpt": _text_attr(item, "raw_excerpt"),
        "search_query": _text_attr(item, "search_query"),
        "evidence_candidate": _text_attr(item, "evidence_candidate"),
        "language": _text_attr(item, "language"),
        "region_jurisdiction": _text_attr(item, "region_jurisdiction"),
        "site_country": _text_attr(item, "site_country"),
    }
    return gf.raw_signal_from_row(row, raw, collected_at=_collected_at_text(collected_at))


def ensure_findings_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(gf.sqlite_schema_ddl())


def _raw_signal_exists(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM raw_signals
         WHERE raw_signal_id = ?
            OR (source = ? AND document_id = ?)
         LIMIT 1
        """,
        (record.get("raw_signal_id"), record.get("source"), record.get("document_id")),
    ).fetchone()
    return row is not None


def _finding_exists(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM findings
         WHERE finding_id = ?
            OR (raw_signal_id = ? AND finding_text = ?)
         LIMIT 1
        """,
        (record.get("finding_id"), record.get("raw_signal_id"), record.get("finding_text")),
    ).fetchone()
    return row is not None


def append_raw_signal(conn: sqlite3.Connection, record: dict[str, Any]) -> RawSignalAppendResult:
    errors = tuple(gf.validate_raw_signal(record))
    if errors:
        return RawSignalAppendResult(
            "invalid",
            raw_signal_id=str(record.get("raw_signal_id") or ""),
            errors=errors,
        )

    row = gf.sqlite_row({key: record.get(key) for key in RAW_SIGNAL_SQLITE_COLUMNS if key in record})
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    try:
        conn.execute(
            f"INSERT INTO raw_signals ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
    except sqlite3.IntegrityError:
        if _raw_signal_exists(conn, record):
            return RawSignalAppendResult("duplicate", raw_signal_id=record["raw_signal_id"])
        raise
    return RawSignalAppendResult("inserted", raw_signal_id=record["raw_signal_id"])


def append_finding(conn: sqlite3.Connection, record: dict[str, Any]) -> RawSignalAppendResult:
    errors = tuple(gf.validate_finding(record))
    if errors:
        return RawSignalAppendResult(
            "invalid",
            raw_signal_id=str(record.get("raw_signal_id") or ""),
            errors=errors,
        )

    row = gf.sqlite_row({key: record.get(key) for key in FINDING_SQLITE_COLUMNS if key in record})
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    try:
        conn.execute(
            f"INSERT INTO findings ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
    except sqlite3.IntegrityError:
        if _finding_exists(conn, record):
            return RawSignalAppendResult("duplicate", raw_signal_id=record["raw_signal_id"])
        raise
    return RawSignalAppendResult("inserted", raw_signal_id=record["raw_signal_id"])


def append_raw_signal_with_findings(
    conn: sqlite3.Connection,
    raw_signal: dict[str, Any],
    findings: list[dict[str, Any]],
) -> RawSignalWithFindingsAppendResult:
    raw_result = append_raw_signal(conn, raw_signal)
    if raw_result.status == "invalid":
        return RawSignalWithFindingsAppendResult(
            "invalid",
            raw_signal_id=raw_result.raw_signal_id,
            raw_signal_status=raw_result.status,
            errors=raw_result.errors,
        )

    inserted = 0
    duplicate = 0
    invalid = 0
    errors: list[str] = []
    raw_signal_id = str(raw_signal.get("raw_signal_id") or "")
    for finding in findings:
        if str(finding.get("raw_signal_id") or "") != raw_signal_id:
            invalid += 1
            errors.append("findings.raw_signal_id must match raw_signals.raw_signal_id")
            continue
        result = append_finding(conn, finding)
        if result.status == "inserted":
            inserted += 1
        elif result.status == "duplicate":
            duplicate += 1
        else:
            invalid += 1
            errors.extend(result.errors)

    if invalid:
        status = "partial" if inserted or duplicate or raw_result.status == "inserted" else "invalid"
    elif inserted:
        status = "inserted"
    elif raw_result.status == "inserted":
        status = "raw_signal_inserted"
    else:
        status = "duplicate"
    return RawSignalWithFindingsAppendResult(
        status,
        raw_signal_id=raw_signal_id,
        raw_signal_status=raw_result.status,
        findings_inserted=inserted,
        findings_duplicate=duplicate,
        findings_invalid=invalid,
        errors=tuple(errors),
    )


def append_intake_item_to_sqlite(
    db_path: str | Path,
    item: Any,
    *,
    collected_at: Any = "",
) -> RawSignalAppendResult:
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    record = raw_signal_from_intake_item(item, collected_at=collected_at)
    conn = sqlite3.connect(path)
    try:
        ensure_findings_schema(conn)
        result = append_raw_signal(conn, record)
        conn.commit()
        return result
    finally:
        conn.close()


def append_intake_item_with_findings_to_sqlite(
    db_path: str | Path,
    item: Any,
    *,
    collected_at: Any = "",
) -> RawSignalWithFindingsAppendResult:
    """Append one Intake item and generated findings in a single SQLite transaction.

    collect_intake calls this helper only behind explicit FIND-1 feature flags.
    """
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    raw_signal = raw_signal_from_intake_item(item, collected_at=collected_at)
    findings = findings_extractors.findings_from_raw_signal(raw_signal)
    conn = sqlite3.connect(path)
    try:
        ensure_findings_schema(conn)
        result = append_raw_signal_with_findings(conn, raw_signal, findings)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
