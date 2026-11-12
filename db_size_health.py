#!/usr/bin/env python3
"""db_size_health — DB 크기가 무료 플랜 한도(500MB)에 언제 닿을지 주 1회 본다.

왜: 2026-11-12 실측 366MB / 500MB(73%). 한도를 넘으면 DB 가 읽기 전용이 돼 수집이 멈춘다. 155 가 하루
한 번 크기를 기록하고(ops_db_size_daily), 이 점검이 RPC `grm_db_size_status()`(service_role 전용)를 읽어
  1) 사용률이 WARN_RATIO(80%) 이상인가
  2) 최근 하루 증가량으로 한도까지 RUNWAY_DAYS(60일) 미만인가
  3) 기록이 36시간 넘게 멈췄는가(pg_cron 정지)
중 하나라도 맞으면 exit 1 → 전용 라벨 이슈(운영 센터 infra). --status-json 으로 네트워크 없이 판정만 한다.

보안: service-role 키는 어떤 로그·report 에도 넣지 않는다(예외 타입명·HTTP status 만).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials

RPC_NAME = "grm_db_size_status"
SCHEMA_VERSION = "grm-db-size-health/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
WARN_RATIO = 0.80
RUNWAY_DAYS = 60
STALE_HOURS = 36
MB = 1024 * 1024


def _parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        ts = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def growth_per_day(status: dict[str, Any]) -> float | None:
    """가장 짧은 창의 하루 증가량(7일 → 28일 → 전체 순). 기록이 모자라면 None."""
    for key in ("growth_7d_per_day", "growth_28d_per_day", "growth_all_per_day"):
        value = status.get(key)
        if value is not None:
            return float(value)
    return None


def runway_days(status: dict[str, Any]) -> float | None:
    """한도까지 남은 일수. 증가량이 없거나 0 이하면 None(줄거나 그대로)."""
    growth = growth_per_day(status)
    if growth is None or growth <= 0:
        return None
    left = float(status.get("limit_bytes") or 0) - float(status.get("db_bytes") or 0)
    return max(left, 0.0) / growth


def evaluate(status: dict[str, Any], *, now: dt.datetime | None = None) -> list[str]:
    """상태 JSON → 문제 문장 목록(빈 목록 = 정상). 순수 함수."""
    problems: list[str] = []
    now = now or dt.datetime.now(dt.timezone.utc)
    if not status.get("db_bytes"):
        return ["DB 크기 기록이 없습니다 — 155 미적용 또는 pg_cron grm-db-size-daily 정지"]
    used = float(status.get("db_bytes") or 0)
    limit = float(status.get("limit_bytes") or 0) or 1.0
    ratio = used / limit
    if ratio >= WARN_RATIO:
        problems.append(f"사용률 {ratio:.0%}({used / MB:.0f}MB / {limit / MB:.0f}MB) — 기준 {WARN_RATIO:.0%} 이상")
    days = runway_days(status)
    if days is not None and days < RUNWAY_DAYS:
        problems.append(f"한도까지 약 {days:.0f}일(하루 {growth_per_day(status) / MB:.1f}MB 증가) — 기준 {RUNWAY_DAYS}일 미만")
    recorded = _parse_ts(status.get("recorded_at"))
    if recorded is not None and now - recorded > dt.timedelta(hours=STALE_HOURS):
        problems.append(f"마지막 기록 {recorded:%Y-%m-%d %H:%M}Z — {STALE_HOURS}시간 넘게 기록이 멈춤(pg_cron 확인)")
    return problems


def fetch_status(base_url: str, service_key: str, *, timeout: float) -> dict[str, Any]:
    base = normalize_supabase_url(base_url)
    if base is None:
        raise ValueError("SUPABASE_URL 형식 오류(https:// 필요)")
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
               "Content-Type": "application/json"}
    try:
        resp = requests.post(f"{base}/rest/v1/rpc/{RPC_NAME}", headers=headers, json={}, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"RPC 요청 실패: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise RuntimeError(f"RPC HTTP {resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("RPC 응답이 객체가 아님")
    return body


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--supabase-url", default=None)
    p.add_argument("--service-role-key", default=None)
    p.add_argument("--status-json", default=None, help="상태 JSON 파일(네트워크 없이 판정만)")
    p.add_argument("--output", default=None, help="report JSON 경로")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_arg_parser().parse_args(argv)
    try:
        if args.status_json:
            status = json.loads(Path(args.status_json).read_text(encoding="utf-8"))
        else:
            creds = resolve_supabase_service_credentials(args)
            if creds is None:
                print("[db-size] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
                return 2
            status = fetch_status(creds[0], creds[1], timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[db-size] 상태 읽기 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    problems = evaluate(status)
    days = runway_days(status)
    report = {"schema_version": SCHEMA_VERSION, "ok": not problems, "problems": problems,
              "db_mb": round(float(status.get("db_bytes") or 0) / MB, 1),
              "limit_mb": round(float(status.get("limit_bytes") or 0) / MB, 1),
              "used_ratio": status.get("used_ratio"), "rows": status.get("rows"),
              "growth_mb_per_day": (round(growth_per_day(status) / MB, 2)
                                    if growth_per_day(status) is not None else None),
              "runway_days": round(days) if days is not None else None,
              "top_tables": (status.get("top_tables") or [])[:10]}
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        for line in problems:
            print(f"[db-size] 문제: {line}")
        return 1
    print(f"[db-size] 정상 · {report['db_mb']}MB / {report['limit_mb']}MB · 한도까지 "
          f"{report['runway_days'] if report['runway_days'] is not None else '증가 없음'}일")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
