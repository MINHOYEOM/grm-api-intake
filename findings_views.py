#!/usr/bin/env python3
"""FIND-1 M2a read-only SQLite query layer for raw_signals/findings.

This module never writes to SQLite, Notion, or Supabase. It only opens the
findings sidecar database in SQLite's `mode=ro` URI mode and exposes
parameter-bound read queries for the search/dashboard layers built on top of
the M1 schema contract in `grm_findings.py`.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Iterable


REQUIRED_TABLES = ("raw_signals", "findings")

_LIST_FIELDS = ("inspector_names", "cfr_refs", "mfds_refs")

_FACET_COLUMNS = ("agency", "category_code", "source", "evidence_level", "review_status")

_RAW_SIGNAL_SUMMARY_COLUMNS = (
    "title",
    "source",
    "source_kind",
    "published_date",
    "collected_at",
    "source_url",
    "official_url",
    "firm_name",
    "site_country",
    "extraction_status",
)


def _like_escape(text: str) -> str:
    """Escape `\\`, `%`, and `_` so LIKE treats them literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def open_findings_db_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the findings SQLite file strictly read-only.

    Uses SQLite's `mode=ro` URI so a missing file raises instead of being
    silently created. The resolved path is percent-encoded so directories
    containing spaces (e.g. "Global Regulatory Sweep") still open correctly.
    """
    path = Path(db_path)
    if not path.is_file():
        raise ValueError(f"findings_views: database file not found: {path}")

    quoted = urllib.parse.quote(path.resolve().as_posix(), safe="/:")
    uri = f"file:{quoted}?mode=ro"

    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"findings_views: failed to open read-only connection: {exc}") from exc

    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
            REQUIRED_TABLES,
        ).fetchall()
    except sqlite3.Error as exc:
        conn.close()
        raise ValueError(f"findings_views: failed to inspect schema: {exc}") from exc

    found = {row["name"] for row in rows}
    missing = [name for name in REQUIRED_TABLES if name not in found]
    if missing:
        conn.close()
        raise ValueError(f"findings_views: missing required tables: {missing}")

    return conn


def _row_to_dict(row: Any, columns: Iterable[str]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return dict(zip(columns, row))


def _build_where(
    *,
    agency: tuple[str, ...] = (),
    category_code: tuple[str, ...] = (),
    source: tuple[str, ...] = (),
    review_status: tuple[str, ...] = (),
    evidence_level: tuple[str, ...] = (),
    firm_contains: str = "",
    text_contains: str = "",
    date_from: str = "",
    date_to: str = "",
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def _add_in(column: str, values: Iterable[Any]) -> None:
        cleaned = [str(v).strip() for v in values if str(v).strip()]
        if not cleaned:
            return
        placeholders = ", ".join("?" for _ in cleaned)
        clauses.append(f"{column} IN ({placeholders})")
        params.extend(cleaned)

    _add_in("agency", agency)
    _add_in("category_code", category_code)
    _add_in("source", source)
    _add_in("review_status", review_status)
    _add_in("evidence_level", evidence_level)

    if str(firm_contains).strip():
        clauses.append("LOWER(firm_name) LIKE LOWER(?) ESCAPE '\\'")
        params.append(f"%{_like_escape(str(firm_contains).strip())}%")

    if str(text_contains).strip():
        clauses.append("LOWER(finding_text) LIKE LOWER(?) ESCAPE '\\'")
        params.append(f"%{_like_escape(str(text_contains).strip())}%")

    if str(date_from).strip():
        clauses.append("published_date >= ?")
        params.append(str(date_from).strip())

    if str(date_to).strip():
        clauses.append("published_date <= ?")
        params.append(str(date_to).strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def query_findings(
    conn: sqlite3.Connection,
    *,
    agency: tuple[str, ...] = (),
    category_code: tuple[str, ...] = (),
    source: tuple[str, ...] = (),
    review_status: tuple[str, ...] = (),
    evidence_level: tuple[str, ...] = (),
    firm_contains: str = "",
    text_contains: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Query findings with parameter-bound filters, sorted deterministically."""
    where_sql, params = _build_where(
        agency=agency,
        category_code=category_code,
        source=source,
        review_status=review_status,
        evidence_level=evidence_level,
        firm_contains=firm_contains,
        text_contains=text_contains,
        date_from=date_from,
        date_to=date_to,
    )
    sql = f"SELECT * FROM findings {where_sql} ORDER BY published_date DESC, finding_id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    cursor = conn.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    records = [_row_to_dict(row, columns) for row in cursor.fetchall()]
    for record in records:
        for key in _LIST_FIELDS:
            if key in record:
                raw_value = record[key]
                record[key] = json.loads(raw_value) if raw_value else []
    return records


def facet_counts(
    conn: sqlite3.Connection,
    *,
    agency: tuple[str, ...] = (),
    category_code: tuple[str, ...] = (),
    source: tuple[str, ...] = (),
    review_status: tuple[str, ...] = (),
    evidence_level: tuple[str, ...] = (),
    firm_contains: str = "",
    text_contains: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, dict[str, int]]:
    """Count findings per facet bucket after applying the same filters as query_findings."""
    where_sql, params = _build_where(
        agency=agency,
        category_code=category_code,
        source=source,
        review_status=review_status,
        evidence_level=evidence_level,
        firm_contains=firm_contains,
        text_contains=text_contains,
        date_from=date_from,
        date_to=date_to,
    )

    facets: dict[str, dict[str, int]] = {}
    for column in _FACET_COLUMNS:
        sql = f"SELECT {column} AS bucket, COUNT(*) AS n FROM findings {where_sql} GROUP BY {column}"
        rows = conn.execute(sql, params).fetchall()
        counts = {str(row[0]): int(row[1]) for row in rows}
        facets[column] = dict(sorted(counts.items()))

    month_sql = (
        f"SELECT substr(published_date, 1, 7) AS bucket, COUNT(*) AS n "
        f"FROM findings {where_sql} GROUP BY bucket"
    )
    month_rows = conn.execute(month_sql, params).fetchall()
    month_counts = {str(row[0]): int(row[1]) for row in month_rows}
    facets["published_month"] = dict(sorted(month_counts.items()))

    return dict(sorted(facets.items()))


def raw_signal_summary(conn: sqlite3.Connection, raw_signal_id: str) -> dict[str, Any] | None:
    """Return a blob-free raw_signals summary, or None if the id does not exist."""
    columns_sql = ", ".join(_RAW_SIGNAL_SUMMARY_COLUMNS)
    cursor = conn.execute(
        f"SELECT {columns_sql} FROM raw_signals WHERE raw_signal_id = ?",
        (raw_signal_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row, _RAW_SIGNAL_SUMMARY_COLUMNS)


def db_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return row counts plus distinct findings schema/taxonomy versions."""
    raw_signals_count = int(conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0])
    findings_count = int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0])
    schema_versions = sorted(
        str(row[0]) for row in conn.execute("SELECT DISTINCT schema_version FROM findings").fetchall()
    )
    taxonomy_versions = sorted(
        str(row[0]) for row in conn.execute("SELECT DISTINCT taxonomy_version FROM findings").fetchall()
    )
    return {
        "raw_signals": raw_signals_count,
        "findings": findings_count,
        "finding_schema_versions": schema_versions,
        "finding_taxonomy_versions": taxonomy_versions,
    }


__all__ = [
    "open_findings_db_readonly",
    "query_findings",
    "facet_counts",
    "raw_signal_summary",
    "db_summary",
]
