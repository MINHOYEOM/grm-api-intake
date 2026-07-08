#!/usr/bin/env python3
"""FIND-1 M3a offline Supabase(Postgres) load plan generator for the findings sidecar.

This module never opens a network connection and never talks to Supabase. It
reads the local findings SQLite sidecar strictly read-only (via
`findings_views.open_findings_db_readonly`) and produces a self-contained SQL
plan (DDL + batched INSERT statements + verification SELECTs) that the control
tower applies through the Supabase MCP in a later, separate step.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

import findings_store
import findings_views
import grm_findings as gf


SUPABASE_LOAD_SCHEMA_VERSION = "grm-findings-supabase-load/v1"
FINDINGS_PG_MIGRATION_NAME = "findings_v1_raw_signals_findings"

_BATCH_SIZE = 10

_FINDING_JSONB_COLUMNS = ("inspector_names", "cfr_refs", "mfds_refs")
_FINDING_NUMERIC_COLUMNS = ("confidence",)


def postgres_schema_ddl() -> str:
    """Return the Postgres DDL text. Single source for web/migrations/002_findings.sql.

    002 is the fresh-install baseline (create table if not exists — a no-op replay
    against the live DB); the taxonomy_version CHECK expansion for an already-live
    DB is handled by the separate 004_findings_taxonomy_v2.sql ALTER migration.
    """
    category_check = ", ".join(f"'{code}'" for code in gf.FINDING_CATEGORY_CODES)
    taxonomy_check = ", ".join(f"'{version}'" for version in gf.TAXONOMY_VERSIONS)
    translation_method_check = ", ".join(f"'{method}'" for method in gf.TRANSLATION_METHODS)
    return f"""-- FIND-1 M3a Supabase(Postgres) 스키마 — grm-findings.sqlite3 sidecar와 컬럼/제약 의미 동치.
-- 단일 소스: findings_supabase.postgres_schema_ddl() 출력이 이 파일과 byte 일치해야 한다.
-- 이 단계(M3a)는 스키마만 생성한다. 데이터 적재는 컨트롤 타워가 Supabase MCP로 별도 실행한다.

-- raw_signals: 재추출 가능한 원본 보존층. raw_json/row_json 은 jsonb 가 아니라 text —
-- canonical JSON byte 를 그대로 보존해 raw_sha256 재검증이 항상 가능해야 한다.
create table if not exists public.raw_signals (
  schema_version text not null check (schema_version = '{gf.RAW_SIGNAL_SCHEMA_VERSION}'),
  raw_signal_id text primary key,
  source text not null,
  source_kind text not null,
  document_id text not null,
  published_date text not null,
  collected_at text,
  title text not null,
  firm_name text,
  site_name text,
  site_country text,
  modality text,
  source_url text,
  official_url text,
  raw_sha256 text not null check (char_length(raw_sha256) = 64),
  raw_json text not null,
  row_json text not null,
  extraction_status text not null,
  ingested_at timestamptz not null default now(),
  unique (source, document_id)
);

-- RLS 활성화(정책은 이 파일 하단에서 두 테이블을 함께 전면 차단한다)
alter table public.raw_signals enable row level security;

-- findings: 지적사항 분석층. inspector_names/cfr_refs/mfds_refs 는 SQLite TEXT 배열의 jsonb 강화형.
create table if not exists public.findings (
  schema_version text not null check (schema_version = '{gf.FINDING_SCHEMA_VERSION}'),
  taxonomy_version text not null check (taxonomy_version in ({taxonomy_check})),
  finding_id text primary key,
  raw_signal_id text not null references public.raw_signals (raw_signal_id) on delete cascade,
  source text not null,
  agency text not null,
  document_type text not null,
  document_id text not null,
  published_date text not null,
  firm_name text not null,
  entity_id text,
  site_name text,
  site_country text,
  product_family text,
  modality text,
  category_code text not null check (category_code in ({category_check})),
  category_label_ko text not null,
  finding_text text not null,
  finding_language text,
  evidence_level text not null check (evidence_level in ('A', 'B', 'C')),
  evidence_url text not null,
  inspector_names jsonb not null default '[]'::jsonb,
  cfr_refs jsonb not null default '[]'::jsonb,
  mfds_refs jsonb not null default '[]'::jsonb,
  extraction_method text not null check (extraction_method in ('deterministic', 'llm_assisted', 'manual')),
  confidence double precision not null check (confidence >= 0 and confidence <= 1),
  review_status text not null check (review_status in ('accepted', 'needs_review', 'rejected')),
  finding_text_ko text not null default '',
  translation_method text not null default '' check (translation_method in ({translation_method_check})),
  ingested_at timestamptz not null default now()
);

-- SQLite 의 UNIQUE(raw_signal_id, finding_text) 를 그대로 옮기면 finding_text 가 길 때
-- btree 인덱스 행 크기 한계(~2704B)를 넘을 수 있어, md5 해시 인덱스로 대체한다.
create unique index if not exists findings_rawsig_text_md5_uq
  on public.findings (raw_signal_id, md5(finding_text));

-- 조회 인덱스(SQLite idx_findings_facets/idx_findings_firm 과 동일 취지)
create index if not exists idx_findings_facets
  on public.findings (agency, category_code, modality, published_date);
create index if not exists idx_findings_firm
  on public.findings (firm_name, published_date);

-- RLS 활성화(정책은 바로 아래에서 두 테이블을 함께 전면 차단한다)
alter table public.findings enable row level security;

-- 정책 0개로 전면 차단 + anon/authenticated 권한 회수: M3a 는 service_role 전용 적재/조회만 허용한다.
-- 웹 공개(anon 읽기) 정책은 M3 후속 단계에서 별도로 추가한다.
revoke all on public.raw_signals, public.findings from anon, authenticated;
"""


def pg_quote_text(value: Any) -> str:
    """Quote a Python value as a Postgres text literal."""
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    text = text.replace("'", "''")
    return "'" + text + "'"


def pg_quote_jsonb(items: Iterable[Any] | None) -> str:
    """Quote a Python list as a Postgres jsonb literal cast."""
    payload = json.dumps(list(items or []), ensure_ascii=False, sort_keys=True)
    return pg_quote_text(payload) + "::jsonb"


def _raw_signal_values_sql(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for column in findings_store.RAW_SIGNAL_SQLITE_COLUMNS:
        value = record.get(column)
        parts.append("null" if value is None else pg_quote_text(str(value)))
    return "(" + ", ".join(parts) + ")"


def _finding_values_sql(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for column in findings_store.FINDING_SQLITE_COLUMNS:
        value = record.get(column)
        if column in _FINDING_JSONB_COLUMNS:
            items = value if isinstance(value, list) else []
            parts.append(pg_quote_jsonb(items))
        elif column in _FINDING_NUMERIC_COLUMNS:
            parts.append(repr(float(value if value is not None else 0.0)))
        elif value is None:
            parts.append("null")
        else:
            parts.append(pg_quote_text(str(value)))
    return "(" + ", ".join(parts) + ")"


def _batched(records: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(records), size):
        yield records[start : start + size]


def _insert_statements(
    *,
    table: str,
    columns: tuple[str, ...],
    records: list[dict[str, Any]],
    value_fn: Any,
    conflict_column: str = "",
) -> list[str]:
    columns_sql = ", ".join(columns)
    statements: list[str] = []
    conflict_sql = f"on conflict ({conflict_column}) do nothing" if conflict_column else "on conflict do nothing"
    for batch in _batched(records, _BATCH_SIZE):
        values_sql = ", ".join(value_fn(record) for record in batch)
        statements.append(
            f"insert into public.{table} ({columns_sql}) values {values_sql} "
            f"{conflict_sql};"
        )
    return statements


def _verification_sql() -> list[str]:
    return [
        "select count(*) as raw_signals_count from public.raw_signals;",
        "select count(*) as findings_count from public.findings;",
        "select distinct schema_version from public.findings order by schema_version;",
        "select distinct taxonomy_version from public.findings order by taxonomy_version;",
        (
            "select "
            "sum(case when raw_sha256 = encode(sha256(convert_to(raw_json, 'UTF8')), 'hex') "
            "then 1 else 0 end) as raw_sha256_match_count, "
            "count(*) as raw_signals_total "
            "from public.raw_signals;"
        ),
        (
            "select count(*) as orphan_findings_count "
            "from public.findings f "
            "left join public.raw_signals r on r.raw_signal_id = f.raw_signal_id "
            "where r.raw_signal_id is null;"
        ),
    ]


def _batch_count(total: int) -> int:
    if total <= 0:
        return 0
    return (total + _BATCH_SIZE - 1) // _BATCH_SIZE


def build_supabase_load_plan(db_path: str | Path) -> dict[str, Any]:
    """Build an offline Supabase(Postgres) load plan from the read-only SQLite sidecar."""
    conn = findings_views.open_findings_db_readonly(db_path)
    try:
        raw_records = [
            dict(row)
            for row in conn.execute("SELECT * FROM raw_signals ORDER BY raw_signal_id ASC").fetchall()
        ]
        finding_rows = [
            dict(row)
            for row in conn.execute("SELECT * FROM findings ORDER BY finding_id ASC").fetchall()
        ]
    finally:
        conn.close()

    validation_errors: list[dict[str, Any]] = []

    for record in raw_records:
        errors = gf.validate_raw_signal(record)
        if errors:
            validation_errors.append({
                "raw_signal_id": str(record.get("raw_signal_id") or ""),
                "errors": errors,
            })

    finding_records: list[dict[str, Any]] = []
    for row in finding_rows:
        record = dict(row)
        for key in _FINDING_JSONB_COLUMNS:
            raw_value = record.get(key)
            try:
                record[key] = json.loads(raw_value) if raw_value else []
            except json.JSONDecodeError:
                record[key] = []
        errors = gf.validate_finding(record)
        if errors:
            validation_errors.append({
                "finding_id": str(record.get("finding_id") or ""),
                "errors": errors,
            })
        finding_records.append(record)

    raw_signal_ids = {str(record.get("raw_signal_id") or "") for record in raw_records}
    for record in finding_records:
        if str(record.get("raw_signal_id") or "") not in raw_signal_ids:
            validation_errors.append({
                "finding_id": str(record.get("finding_id") or ""),
                "errors": ["findings.raw_signal_id has no matching raw_signals row"],
            })

    data_sql: list[str] = []
    data_sql.extend(_insert_statements(
        table="raw_signals",
        columns=findings_store.RAW_SIGNAL_SQLITE_COLUMNS,
        records=raw_records,
        value_fn=_raw_signal_values_sql,
        conflict_column="raw_signal_id",
    ))
    data_sql.extend(_insert_statements(
        table="findings",
        columns=findings_store.FINDING_SQLITE_COLUMNS,
        records=finding_records,
        value_fn=_finding_values_sql,
    ))

    counts = {
        "raw_signals": len(raw_records),
        "findings": len(finding_records),
        "raw_signal_batches": _batch_count(len(raw_records)),
        "finding_batches": _batch_count(len(finding_records)),
    }

    blocking_errors = len(validation_errors)

    return {
        "schema_version": SUPABASE_LOAD_SCHEMA_VERSION,
        "raw_signal_schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
        "finding_schema_version": gf.FINDING_SCHEMA_VERSION,
        "taxonomy_version": gf.TAXONOMY_VERSION,
        "migration_name": FINDINGS_PG_MIGRATION_NAME,
        "ddl_sql": postgres_schema_ddl(),
        "data_sql": data_sql,
        "verification_sql": _verification_sql(),
        "counts": counts,
        "report": {
            "mode": "supabase_load_plan",
            "validation_errors": validation_errors,
            "blocking_errors": blocking_errors,
            "ready_for_apply": blocking_errors == 0,
        },
    }


def _write_json(path: str | Path, data: dict[str, Any], *, pretty: bool) -> None:
    text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    Path(path).write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M3a findings SQLite -> offline Supabase(Postgres) load plan (no network access)"
    )
    parser.add_argument("--db-path", required=True, help="Path to the findings SQLite sidecar, opened read-only")
    parser.add_argument("--output", help="Optional load plan JSON output path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        result = build_supabase_load_plan(args.db_path)
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"findings_supabase: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result, pretty=args.pretty)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
