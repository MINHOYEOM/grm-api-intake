#!/usr/bin/env python3
"""Evaluate source silence using the latest stored intake queue dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, NamedTuple

import requests

from grm_common import INTAKE_SOURCE_SPECS

# 마지막 수집일을 되짚어 보는 상한. 이보다 오래된 소스는 "≥{cap}일"로 보고한다.
# 상한을 두는 이유는 비용이 아니라 의미다 — 120일 넘게 무음이면 며칠인지는 더 이상
# 판단을 바꾸지 않는다.
SILENCE_LOOKBACK_CAP_DAYS = 120


class WatchedSource(NamedTuple):
    """감시 단위 — Notion `Source` select 값 1개."""

    notion_source: str
    silence_days: int      # 기여 spec 중 **가장 엄격한**(작은) 임계
    prefixes: tuple[str, ...]   # 이 Source 로 들어가는 수집기 prefix 들


@dataclass(frozen=True)
class SilenceFinding:
    notion_source: str
    days: int
    threshold: int
    capped: bool       # True = 룩백 상한에 걸림("≥{days}일")

    @property
    def days_text(self) -> str:
        return f"≥{self.days}일" if self.capped else f"{self.days}일"


def watched_sources(specs: Iterable[Any] = INTAKE_SOURCE_SPECS) -> list[WatchedSource]:
    """레지스트리에서 감시 단위를 유도한다(손목록 금지 — 레지스트리 파생)."""
    merged: dict[str, tuple[int, list[str]]] = {}
    for spec in specs:
        if not spec.notion_source or spec.silence_days <= 0:
            continue
        cur = merged.get(spec.notion_source)
        if cur is None:
            merged[spec.notion_source] = (spec.silence_days, [spec.prefix])
        else:
            # 한 Source 에 여러 수집기가 붙으면 더 엄격한 임계를 쓴다.
            merged[spec.notion_source] = (min(cur[0], spec.silence_days),
                                          cur[1] + [spec.prefix])
    return [WatchedSource(src, days, tuple(prefixes))
            for src, (days, prefixes) in sorted(merged.items())]


def evaluate_silence(
    *,
    last_seen: dict[str, date | None],
    run_date: date,
    source_enabled: dict[str, bool],
    specs: Iterable[Any] = INTAKE_SOURCE_SPECS,
    lookback_cap_days: int = SILENCE_LOOKBACK_CAP_DAYS,
) -> list[SilenceFinding]:
    """순수 판정 — 네트워크를 타지 않는다(테스트가 여기를 못박는다).

    `last_seen` 에 키가 없는 Source 는 **조회하지 못한 것**이므로 판정하지 않는다
    (조회 실패를 무음으로 오보하면 감시 자체를 못 믿게 된다). 키는 있는데 값이 None 이면
    룩백 구간에 행이 하나도 없다는 뜻이라 상한으로 보고한다.
    """
    findings: list[SilenceFinding] = []
    for watched in watched_sources(specs):
        # 이 Source 로 들어가는 수집기가 이번 실행에 하나도 안 켜졌으면 판정 대상이 아니다.
        # (기본 True — 레지스트리에 넣고 매핑을 빠뜨리면 조용히 빠지는 대신 보고된다.)
        if not any(source_enabled.get(p, True) for p in watched.prefixes):
            continue
        if watched.notion_source not in last_seen:
            continue
        seen = last_seen[watched.notion_source]
        if seen is None:
            days, capped = lookback_cap_days, True
        else:
            days, capped = (run_date - seen).days, False
        if days > watched.silence_days:
            findings.append(SilenceFinding(watched.notion_source, days,
                                           watched.silence_days, capped))
    return findings


# [노션 은퇴 준비 2026-11-13] 같은 판정을 DB 대기열(Supabase `intake_queue`) 기준으로 — 그림자
# ─────────────────────────────────────────────────────────────────────────────
# 노션 Intake 행 = 일일 수집이 대기열에 넣은 문서 = `intake_queue` 행(migration 149). 그래서
# "그 소스의 마지막 Run Date" 의 DB 쪽 값은 `intake_queue.first_seen_run_date` 의 최댓값이다.
# (`raw_signals` 로 재면 안 된다 — 과거분 백필 수집기도 거기에 쓰므로 일일 수집이 멎은 것을 가린다.)
# 판정은 아직 노션 기준이다. 여기서는 두 기준의 차이만 만들어 health 에 남긴다.

def query_last_seen_supabase(
    base_url: str,
    service_key: str,
    sources: Iterable[str],
    *,
    run_date: date,
    lookback_cap_days: int = SILENCE_LOOKBACK_CAP_DAYS,
    timeout: int = 30,
    session: Any = None,
) -> tuple[dict[str, date | None], list[str], date | None]:
    """소스별 마지막 수집일을 `intake_queue` 에서 읽는다.

    Returns:
        (last_seen, errors, history_start)
        - last_seen: `query_last_seen` 과 같은 뜻. 조회 실패한 소스는 키를 넣지 않는다.
          룩백 구간에 행이 없으면 None — **단, 대기열 기록이 룩백 구간보다 짧으면 키를 넣지 않는다**
          (기록이 시작되기 전에 마지막으로 들어온 소스를 '무음'으로 오보하지 않는다).
        - history_start: 대기열에서 가장 이른 수집일(없으면 None).
    """
    http = session or requests
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    url = f"{base_url.rstrip('/')}/rest/v1/intake_queue"
    floor = run_date - timedelta(days=lookback_cap_days)
    errors: list[str] = []

    def _one(params: dict[str, str], label: str) -> "list[dict[str, Any]] | None":
        try:
            resp = http.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            errors.append(f"{label}: {type(e).__name__}")
            return None
        if resp.status_code >= 400:
            errors.append(f"{label}: http_{resp.status_code}")
            return None
        try:
            data = resp.json()
        except ValueError:
            errors.append(f"{label}: 응답 JSON 아님")
            return None
        return data if isinstance(data, list) else []

    def _day(rows: "list[dict[str, Any]]") -> date | None:
        try:
            return date.fromisoformat(str(rows[0].get("first_seen_run_date") or "")[:10])
        except (IndexError, ValueError):
            return None

    first = _one({"select": "first_seen_run_date", "order": "first_seen_run_date.asc", "limit": "1"},
                 "history")
    history_start = _day(first) if first else None
    history_covers_lookback = history_start is not None and history_start <= floor
    last_seen: dict[str, date | None] = {}
    if first is None:
        return last_seen, errors, None
    for source in sources:
        rows = _one({"select": "first_seen_run_date", "source": f"eq.{source}",
                     "first_seen_run_date": f"gte.{floor.isoformat()}",
                     "and": f"(first_seen_run_date.lte.{run_date.isoformat()})",
                     "order": "first_seen_run_date.desc", "limit": "1"}, source)
        if rows is None:
            continue
        if rows:
            seen = _day(rows)
            if seen is not None:
                last_seen[source] = seen
            else:
                errors.append(f"{source}: 수집일 파싱 실패")
        elif history_covers_lookback:
            last_seen[source] = None
    return last_seen, errors, history_start


def silence_shadow(
    *,
    notion_last_seen: dict[str, date | None],
    db_last_seen: dict[str, date | None],
    run_date: date,
    source_enabled: dict[str, bool],
    db_errors: Iterable[str] = (),
    history_start: date | None = None,
    specs: Iterable[Any] = INTAKE_SOURCE_SPECS,
) -> dict[str, Any]:
    """노션 기준과 DB 기준의 무음 판정을 나란히 계산해 차이를 돌려준다(순수 함수 — health JSON 용).

    두 기준 모두에서 조회된 소스만 비교한다(한쪽이 못 읽은 소스는 `not_compared` 로 센다).
    `verdict_differs` 가 전환 판단의 재료다: 한쪽에서만 무음인 소스.
    """
    specs = tuple(specs)
    both = sorted(set(notion_last_seen) & set(db_last_seen))

    def _silent(seen: dict[str, date | None]) -> set[str]:
        return {f.notion_source for f in evaluate_silence(
            last_seen={k: seen[k] for k in both}, run_date=run_date,
            source_enabled=source_enabled, specs=specs)}

    n_silent, d_silent = _silent(notion_last_seen), _silent(db_last_seen)
    date_differs = [
        {"source": k,
         "notion": notion_last_seen[k].isoformat() if notion_last_seen[k] else None,
         "db": db_last_seen[k].isoformat() if db_last_seen[k] else None}
        for k in both if notion_last_seen[k] != db_last_seen[k]]
    watched = {w.notion_source for w in watched_sources(specs)}
    return {
        "compared": len(both),
        "not_compared": sorted((set(notion_last_seen) | set(db_last_seen) | watched) - set(both)),
        "history_start": history_start.isoformat() if history_start else None,
        "silent_notion": sorted(n_silent),
        "silent_db": sorted(d_silent),
        "verdict_differs": sorted(n_silent ^ d_silent),
        "date_differs": date_differs,
        "db_errors": len(list(db_errors)),
        "same": not (n_silent ^ d_silent) and not date_differs,
    }
