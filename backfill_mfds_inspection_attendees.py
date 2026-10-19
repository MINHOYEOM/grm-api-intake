#!/usr/bin/env python3
"""저장 텍스트만 쓰는 MFDS 실사자 백필. 기본 dry-run, 원문 네트워크 재수집 없음."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
from grm_common import log
from mfds_inspection_attendees import AttendeeTable, document_inspector_names
from mfds_inspection_attendees_store import parse_record, raw_object, replace_attendees

STATUSES = ("named", "named_none", "absent", "failed")


def plan(rows: list[dict]) -> tuple[dict, list[tuple[dict, AttendeeTable]]]:
    """드라이런과 apply가 같은 파싱 결과를 사용한다. 이름·본문은 리포트에 싣지 않는다."""
    report = {"documents": len(rows), "by_format": {}, "multi_table_documents": [],
              "failures": [], "writes": 0, "write_errors": [], "baseline_mismatches": []}
    planned = []
    by_format_names: dict[str, set[str]] = {}
    for row in rows:
        if row.get("source", "MFDS") != "MFDS" or row.get("source_kind", "gmp-inspection") != "gmp-inspection":
            raise ValueError("snapshot contains a non-MFDS inspection record")
        result = parse_record(row)
        raw = raw_object(row)
        fmt = str(raw.get("attachment_file_format") or "unknown").lower()
        stats = report["by_format"].setdefault(fmt, {
            "documents": 0, "status_counts": dict.fromkeys(STATUSES, 0),
            "document_name_links_including_observers": 0, "name_keys_including_observers": 0,
            "participant_document_name_links": 0,
        })
        stats["documents"] += 1
        stats["status_counts"][result.table_status] += 1
        names = document_inspector_names(result, include_observers=True)
        stats["document_name_links_including_observers"] += len(names)
        stats["participant_document_name_links"] += len(document_inspector_names(result))
        by_format_names.setdefault(fmt, set()).update(names)
        if result.table_count > 1:
            report["multi_table_documents"].append({"document_id": row["document_id"],
                "tables": result.table_count, "document_name_links": len(names)})
        if result.table_status == "failed":
            report["failures"].append({"document_id": row["document_id"], "reason": result.reason})
        planned.append((row, result))
    for fmt, names in by_format_names.items():
        report["by_format"][fmt]["name_keys_including_observers"] = len(names)
    return report, planned


def compare_baseline(report: dict, expected: dict, prefix: str = "") -> list[str]:
    """기대 집계값을 재귀 비교한다. 불일치하면 쓰기 전에 멈춘다."""
    mismatches = []
    for key, value in expected.items():
        path = f"{prefix}.{key}" if prefix else key
        actual = report.get(key)
        if isinstance(value, dict) and isinstance(actual, dict):
            mismatches.extend(compare_baseline(actual, value, path))
        elif actual != value:
            mismatches.append(f"{path}: expected={value!r}, actual={actual!r}")
    return mismatches


def fetch_rows(base_url: str, service_key: str) -> list[dict]:
    from findings_supabase_backfill import _fetch_all_pages
    return _fetch_all_pages(base_url, service_key, "raw_signals",
        select="document_id,raw_signal_id,raw_sha256,raw_json,source,source_kind",
        page_size=100, order="document_id.asc",
        extra_params={"source": "eq.MFDS", "source_kind": "eq.gmp-inspection"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--input-json", type=Path, help="로컬 스냅샷으로 dry-run만 수행")
    ap.add_argument("--baseline-report", type=Path)
    ap.add_argument("--report-path", type=Path)
    ap.add_argument("--supabase-url", default="")
    ap.add_argument("--service-role-key", default="")
    args = ap.parse_args(argv)
    if args.input_json and args.apply:
        ap.error("--input-json cannot be used with --apply")
    credentials = resolve_supabase_service_credentials(args)
    if not args.input_json and not credentials:
        print("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY required", file=sys.stderr)
        return 2
    base = normalize_supabase_url(credentials[0]) if credentials else None
    if credentials and base is None:
        print("SUPABASE_URL must start with https://", file=sys.stderr)
        return 2
    try:
        rows = (json.loads(args.input_json.read_text(encoding="utf-8")) if args.input_json
                else fetch_rows(base, credentials[1]))
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty or invalid source snapshot")
        if len({r["document_id"] for r in rows}) != len(rows):
            raise ValueError("duplicate document_id in source snapshot")
        report, planned = plan(rows)
        report["mode"] = "apply" if args.apply else "dry_run"
        if args.baseline_report:
            expected = json.loads(args.baseline_report.read_text(encoding="utf-8"))
            report["baseline_mismatches"] = compare_baseline(report, expected)
        if args.apply and not report["baseline_mismatches"]:
            for row, result in planned:
                error = replace_attendees(base, credentials[1], row, result)
                if error:
                    report["write_errors"].append({"document_id": row["document_id"], "reason": error})
                    log("WARN", f"MFDS 실사자 백필 {row['document_id']}: {error}")
                else:
                    report["writes"] += 1
                time.sleep(1)
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.report_path:
            args.report_path.write_text(output, encoding="utf-8")
        print(output, end="")
        unexpected_failures = any(f["reason"] != "missing_text" for f in report["failures"])
        return int(bool(report["baseline_mismatches"] or report["write_errors"] or unexpected_failures))
    except Exception as exc:
        # 인증·HTTP 예외의 본문을 출력하지 않는다.
        print(f"MFDS backfill failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
