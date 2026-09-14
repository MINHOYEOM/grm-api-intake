#!/usr/bin/env python3
"""수집 소스 **무음** 감시 — "오류는 안 났는데 계속 0건"을 표면화한다.

배경(2026-09-14 발견)
---------------------
종전 health 판정은 `stats.{prefix}_error` 가 True 인 소스만 보고했다. 그런데 실제로
GRM 을 가장 오래 갉아먹은 고장은 **오류를 내지 않는 고장**이었다:

* 피드가 HTTP 200 과 함께 빈 응답을 준다(프록시·CDN 이 중간에서 삼킨 경우 포함)
* 소스가 스키마를 바꿔 파서가 조용히 0건을 뽑는다
* 소스 쪽이 그냥 갱신을 멈춘다(사이트 개편·URL 이동)

셋 다 `*_error=False` 라 경보 경로에 한 줄도 안 올라온다. 2026-09-14 시점 실측:
PIC/S 46일 · MHRA GMP NCR 35일 · MHRA Alert 25일 · EU GMP NCR 20일 · WHO 12~16일 ·
Health Canada 13일 · ICH 60일+ 이 **경보 0건으로** 멈춰 있었고, 그 주 브리프는 이걸
"MHRA 0 · PIC/S 0 · ICH 0 · WHO 0 · HC 0 · EU NCR 0" 으로, 즉 한산한 주와 구별되지
않는 모습으로 발행했다.

판정 기준
---------
한 번의 실행에서 `fetched == 0` 인 것은 신호가 아니다 — 날짜 윈도우로 거른 뒤의 수라
저볼륨 소스는 평상시에도 0이 나온다. 그래서 **Notion Intake DB 에 그 소스 행이 마지막으로
들어온 날**을 보고, 레지스트리가 소스별로 선언한 `silence_days` 를 넘었을 때만 경고한다.

경고이지 실패가 아니다 — 무음 감시가 그 주 발행을 막으면 안 된다(`grm_health` 의
warn_only 주석과 같은 이유). 목적은 차단이 아니라 **표면화**다.

알려진 한계
-----------
감시 단위는 Notion `Source` select 값이다. MFDS 하위 수집기 6종은 같은 `Source="MFDS"`
로 들어가므로 그중 하나만 죽으면 이 감시로는 안 잡힌다 — 그쪽은 `*_error` 기반 보고가
이미 잡고 있다(이슈 #956 이 실제로 5건을 정확히 잡았다). 엔드포인트 단위 감시는 `API
Query` 속성으로 가능하지만 임계값을 URL 마다 손으로 들고 있어야 해서 여기서는 하지 않는다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, NamedTuple

import requests

from grm_common import INTAKE_SOURCE_SPECS, log, retry_after_seconds
from grm_notion import (
    NOTION_DB_QUERY_URL_TPL,
    PROP_RUN_DATE,
    PROP_SOURCE,
    notion_headers,
)

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


def query_last_seen(
    token: str,
    db_id: str,
    sources: Iterable[str],
    *,
    run_date: date,
    lookback_cap_days: int = SILENCE_LOOKBACK_CAP_DAYS,
    timeout: int = 30,
) -> tuple[dict[str, date | None], list[str]]:
    """Source 별 **마지막 Run Date** 를 1건씩 조회한다.

    Source 하나당 `page_size=1` + `Run Date` 내림차순 정렬 1회 = 소스 수만큼의 가벼운
    요청이다(윈도우 전체를 페이징하는 것보다 훨씬 싸다).

    Returns:
        (last_seen, errors) — 조회에 실패한 Source 는 `last_seen` 에 **키를 넣지 않는다**
        (판정 제외). 실패 사유는 errors 로 돌려 health 가 별도 표면화한다.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    floor = (run_date - timedelta(days=lookback_cap_days)).isoformat()
    last_seen: dict[str, date | None] = {}
    errors: list[str] = []
    for source in sources:
        body = {
            "filter": {"and": [
                {"property": PROP_SOURCE, "select": {"equals": source}},
                {"property": PROP_RUN_DATE, "date": {"on_or_after": floor}},
                {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            ]},
            "sorts": [{"property": PROP_RUN_DATE, "direction": "descending"}],
            "page_size": 1,
        }
        data: dict[str, Any] | None = None
        try:
            for attempt in range(3):
                resp = requests.post(url, json=body, headers=notion_headers(token),
                                     timeout=timeout)
                if resp.status_code == 429 and attempt < 2:
                    time.sleep(retry_after_seconds(resp, attempt, max_sleep=30))
                    continue
                if resp.status_code >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{source}: {e}")
            log("WARN", f"무음 감시 조회 실패 source={source} err={e}")
            continue
        if data is None:
            errors.append(f"{source}: empty response")
            continue
        results = data.get("results") or []
        if not results:
            last_seen[source] = None          # 룩백 구간에 행 0건 = 상한 보고
            continue
        raw = (((results[0].get("properties") or {}).get(PROP_RUN_DATE) or {})
               .get("date") or {}).get("start") or ""
        try:
            last_seen[source] = date.fromisoformat(raw[:10])
        except ValueError:
            errors.append(f"{source}: Run Date 파싱 실패 ({raw!r})")
    return last_seen, errors
