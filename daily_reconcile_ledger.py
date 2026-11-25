"""수집기 health와 정본 DB를 대조해 날짜별 장부를 만든다."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from collect_mfds_admin_action import DATASET_URL as ADMIN_DATASET_URL
from collect_mfds_admin_action import GRM_PROBLEM_PREFIX
from collect_mfds_recall import DATASET_URL as RECALL_DATASET_URL
from grm_common import SOURCE_MFDS


DATASET_URLS = {"mfds_admin": ADMIN_DATASET_URL, "mfds_recall": RECALL_DATASET_URL}
SUPPORTED_SOURCES = tuple(DATASET_URLS)


def _count(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _date(value: object) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _window(health: Mapping[str, object]) -> tuple[date, date] | None:
    start = _date(health.get("window_start"))
    end = _date(health.get("window_end"))
    return (start, end) if start and end and start <= end else None


def _source_status(health: Mapping[str, object]) -> str:
    if health.get("failed") is True or not _window(health) or not _count(health.get("pages_seen")):
        return "unavailable"
    warnings = health.get("page_warnings")
    if health.get("truncated") is not False or not isinstance(warnings, (list, tuple)) or warnings:
        return "incomplete"
    total = _count(health.get("total_count"))
    raw = _count(health.get("raw_count"))
    window = _count(health.get("window_count"))
    excluded = _count(health.get("excluded_count"))
    duplicates = _count(health.get("duplicate_count"))
    collected = _count(health.get("collected"))
    # data.go.kr 파서는 totalCount 누락도 0으로 기록한다.
    if not total or None in (raw, window, excluded, duplicates, collected):
        return "unavailable"
    if total != raw or window > raw or window != excluded + duplicates + collected:
        return "incomplete"
    return "measured"


def build_daily_rows(
    day: date,
    source_health: Mapping[str, Mapping[str, object] | None],
    db_counts: Mapping[str, int | None],
) -> list[dict[str, str | int | bool | None]]:
    """같은 수집 기간의 원천 건수와 DB 건수를 비교한다."""
    rows = []
    for source in SUPPORTED_SOURCES:
        health = source_health.get(source) or {}
        window = _window(health)
        window_count = _count(health.get("window_count"))
        excluded_count = _count(health.get("excluded_count"))
        duplicate_count = _count(health.get("duplicate_count"))
        db_count = _count(db_counts.get(source))
        status = _source_status(health)
        difference = unexplained_difference = None
        if status == "measured":
            if db_count is None:
                status = "unavailable"
            else:
                difference = window_count - db_count
                unexplained_difference = difference - excluded_count - duplicate_count
        warnings = health.get("page_warnings")
        truncated = health.get("truncated")
        rows.append({
            "day": day.isoformat(),
            "source": source,
            "window_start": window[0].isoformat() if window else None,
            "window_end": window[1].isoformat() if window else None,
            "source_total": _count(health.get("total_count")),
            "source_count": window_count,
            "db_count": db_count,
            "excluded_count": excluded_count,
            "duplicate_count": duplicate_count,
            "difference": difference,
            "unexplained_difference": unexplained_difference,
            "measurement_status": status,
            "pages_seen": _count(health.get("pages_seen")),
            "truncated": truncated if isinstance(truncated, bool) else None,
            "page_warning_count": len(warnings) if isinstance(warnings, (list, tuple)) else None,
        })
    return rows


def _db_count(base_url: str, headers: dict[str, str], source: str, window: tuple[date, date]) -> int | None:
    start, end = window
    response = requests.head(
        f"{base_url}/rest/v1/raw_signals",
        headers={**headers, "Prefer": "count=exact"},
        params={
            "select": "raw_signal_id",
            "source": f"eq.{SOURCE_MFDS}",
            "official_url": f"eq.{DATASET_URLS[source]}",
            "and": f"(published_date.gte.{start},published_date.lte.{end})",
        },
        timeout=15,
    )
    response.raise_for_status()
    total = response.headers.get("Content-Range", "").rpartition("/")[2]
    return int(total) if total.isascii() and total.isdecimal() else None


def _save(base_url: str, headers: dict[str, str], ledger: dict) -> None:
    response = requests.post(
        f"{base_url}/rest/v1/coverage_daily",
        headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "day"},
        json=ledger,
        timeout=15,
    )
    response.raise_for_status()


def _alert(day: date, mismatches: list[dict]) -> None:
    title = f"{GRM_PROBLEM_PREFIX} 식약처 일별 수집 대조 — {day}"
    issues = subprocess.run(
        ["gh", "issue", "list", "--state", "all", "--search", f'"{title}" in:title',
         "--json", "title"],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    if any(issue["title"] == title for issue in json.loads(issues.stdout)):
        return
    body = [
        f"- {row['source']} ({row['window_start']} ~ {row['window_end']}) "
        f"원천 {row['source_count']}건 / DB {row['db_count']}건 / "
        f"제외 {row['excluded_count']}건 / 중복 {row['duplicate_count']}건 / "
        f"설명되지 않은 차이 {row['unexplained_difference']}건"
        for row in mismatches
    ]
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if run_id and server and repository:
        body.append(f"\n수집 실행: {server}/{repository}/actions/runs/{run_id}")
    subprocess.run(
        ["gh", "issue", "create", "--title", title, "--body", "\n".join(body),
         "--label", "grm-intake,automation-warning"],
        check=True, timeout=15,
    )


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", type=Path, default=os.environ.get("GRM_HEALTH_JSON", "grm-health.json"))
    parser.add_argument("--collection-failed", action="store_true")
    parser.add_argument("--apply", action="store_true", help="장부 저장 및 차이 경보")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.health.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"수집 health를 읽을 수 없어 측정 불가로 기록합니다 ({type(error).__name__}).", file=sys.stderr)
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    day = _date(payload.get("run_date_kst")) or datetime.now(timezone(timedelta(hours=9))).date()
    source_health = payload.get("mfds_reconcile_health")
    if not isinstance(source_health, dict):
        source_health = {}
    run_health = payload.get("health")
    collection_failed = args.collection_failed or (
        isinstance(run_health, dict) and run_health.get("status") == "failure"
    )
    source_health = {
        source: {**health, **({"failed": True} if collection_failed else {})}
        for source, health in source_health.items() if isinstance(health, dict)
    }
    base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    configured = base_url.startswith("https://") and bool(key)
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    db_counts = {}
    lookup_failed = False
    for source in SUPPORTED_SOURCES:
        health = source_health.get(source, {})
        window = _window(health)
        if configured and window and _source_status(health) == "measured":
            try:
                db_counts[source] = _db_count(base_url, headers, source, _window(health))
            except requests.RequestException:
                db_counts[source] = None
            if db_counts[source] is None:
                lookup_failed = True
                print(f"{source}: DB 건수를 확인할 수 없어 측정 불가로 기록합니다.", file=sys.stderr)

    rows = build_daily_rows(day, source_health, db_counts)
    ledger = {"day": day.isoformat(), **{row["source"]: row for row in rows}}
    print(json.dumps(ledger, ensure_ascii=False))
    if not args.apply or payload.get("dry_run") is True:
        return 0
    if not configured:
        parser.error("--apply에는 SUPABASE_URL(HTTPS)과 SUPABASE_SERVICE_ROLE_KEY가 필요합니다.")
    try:
        _save(base_url, headers, ledger)
    except requests.RequestException as error:
        print(f"대조 장부 저장 실패 ({type(error).__name__}).", file=sys.stderr)
        return 1
    mismatches = [row for row in rows if row["unexplained_difference"] not in (None, 0)]
    if mismatches:
        _alert(day, mismatches)
    return int(lookup_failed)


if __name__ == "__main__":
    sys.exit(main())
