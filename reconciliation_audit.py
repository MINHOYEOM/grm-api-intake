#!/usr/bin/env python3
"""주간 커버리지 reconciliation 러너 — Supabase raw_signals 에서 소스별 주간 수집량을
집계해 reconciliation_service 로 이상 낙차를 판정하고 리포트한다(비차단).

[신뢰성 게이트 2026-07-12] 발행 파이프라인과 완전히 분리된 관측 전용 잡. 이상치가
있어도 exit 0(비차단) — GitHub Actions 주석(::warning::)과 STEP_SUMMARY 로만 표면화한다.
서비스키는 로그·예외 메시지에 절대 싣지 않는다(findings_supabase_append 관례 계승).

집계 기준 = ingested_at(우리가 실제로 수집한 시각). published_date 는 지연공개 때문에
"이번 주에 받았나"를 못 나타낸다. 주(週) 버킷은 실행 시각 기준 7일 롤링 윈도우.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from reconciliation_service import detect_coverage_anomalies

_TIMEOUT = 30
_PAGE = 1000
_LOOKBACK_WEEKS = 8


def _fetch_recent_rows(base_url: str, service_key: str, since_iso: str) -> list[dict[str, Any]]:
    """raw_signals 에서 since 이후 (source, ingested_at) 만 페이지네이션으로 전량 취득."""
    url = f"{base_url}/rest/v1/raw_signals"
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "select": "source,ingested_at",
            "ingested_at": f"gte.{since_iso}",
            "order": "ingested_at.asc",
            "limit": str(_PAGE),
            "offset": str(offset),
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"raw_signals 조회 실패: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise RuntimeError(f"raw_signals 조회 HTTP {resp.status_code}")
        batch = resp.json() or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE
    return rows


def _bucket_by_week(rows: list[dict[str, Any]], now: datetime
                    ) -> tuple[dict[str, int], dict[str, list[int]]]:
    """(current, history) 반환. current=최근 7일, history[source]=[직전주, 그전주, ...]."""
    # week_index 0 = 최근 7일, 1 = 그 전 7일 ...
    current: dict[str, int] = {}
    per_week: dict[str, dict[int, int]] = {}
    for r in rows:
        source = (r.get("source") or "").strip()
        ts_raw = (r.get("ingested_at") or "").strip()
        if not source or not ts_raw:
            continue
        ts = _parse_iso(ts_raw)
        if ts is None:
            continue
        days_ago = (now - ts).days
        if days_ago < 0:
            days_ago = 0
        week_index = days_ago // 7
        if week_index == 0:
            current[source] = current.get(source, 0) + 1
        elif 1 <= week_index <= _LOOKBACK_WEEKS:
            weeks = per_week.setdefault(source, {})
            weeks[week_index] = weeks.get(week_index, 0) + 1
    history: dict[str, list[int]] = {}
    for source, weeks in per_week.items():
        history[source] = [weeks[i] for i in sorted(weeks)]
    return current, history


def _parse_iso(value: str) -> datetime | None:
    v = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        # 날짜만 있는 경우 등 방어
        try:
            dt = datetime.fromisoformat(v[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _emit(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base_url or not service_key:
        _emit("::warning title=reconciliation::SUPABASE_URL/KEY 미설정 — reconciliation 건너뜀")
        return 0  # 비차단

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7 * (_LOOKBACK_WEEKS + 1))).isoformat()

    try:
        rows = _fetch_recent_rows(base_url, service_key, since)
    except RuntimeError as exc:
        # 조회 실패 자체도 비차단(관측 잡이 파이프라인을 막으면 안 됨) — 단, 표면화.
        _emit(f"::warning title=reconciliation::{exc}")
        return 0

    current, history = _bucket_by_week(rows, now)
    anomalies = detect_coverage_anomalies(current, history)

    _emit(f"# 수집 reconciliation ({now.date()} 기준, 최근 7일 vs 과거 {_LOOKBACK_WEEKS}주)")
    _emit(f"- raw_signals 조회 행수: {len(rows)}")
    monitored = sorted(set(current) | set(history))
    for src in monitored:
        base = history.get(src, [])
        _emit(f"- {src}: 이번주 {current.get(src, 0)}건 (과거주 {base})")

    if not anomalies:
        _emit("\n✅ 이상 낙차 없음 — 감시 대상 소스 정상 수집.")
        return 0

    _emit(f"\n⚠️ 이상 낙차 {len(anomalies)}건:")
    for a in anomalies:
        _emit(f"::warning title=coverage-{a.severity}::{a.message}")
        _emit(f"  - [{a.severity}] {a.message}")
    # 비차단: 이상치가 있어도 exit 0. 경보는 위 ::warning:: 주석으로 Actions UI 에 노출.
    return 0


if __name__ == "__main__":
    sys.exit(main())
