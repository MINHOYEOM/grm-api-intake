#!/usr/bin/env python3
"""findings_search_cache_health — 검색 캐시 daily 갱신(086·151)이 살아 있는지 매일 본다.

왜: 2026-11-11·12 daily 갱신 잡이 이틀 연속 2분 한도에 걸려 194행 전부 롤백됐는데, 화면은 원 계산으로
폴백해 죽지 않았고(느려졌을 뿐), 사이트 합성 점검(site_probe.py)의 fail 은 다른 실패와 한 이슈
(`automation-failure`)에 섞여 묻혔다. 이 점검은 **이 잡 하나만** 보고, 전용 라벨 이슈를 연다
(→ 운영 센터 업무, web/ops_alarm_sync.py).

무엇을: RPC `findings_search_cache_job_status()`(151, service_role 전용)를 한 번 읽어
  1) 가장 최근 daily 잡 실행이 실패했는가
  2) 26시간 안에 성공한 실행이 하나도 없는가(잡이 아예 안 돌았거나 전부 실패)
  3) 신선한 daily 행이 기준(전체의 75%, 최대 150) 아래인가
셋 중 하나라도 맞으면 exit 1. --status-json 으로 파일을 주면 네트워크 없이 판정만 한다(테스트·재현용).

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

RPC_NAME = "findings_search_cache_job_status"
SCHEMA_VERSION = "grm-findings-search-cache-health/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
SUCCESS_WINDOW_H = 26          # 하루 1회(4배치) 잡 — 26시간 안에 성공이 없으면 하루를 통째로 놓친 것
FRESH_RATIO = 0.75             # 배치 하나(60행)가 실패해도 넘는 선, 둘 이상이면 걸린다
FRESH_CAP = 150                # site_probe CACHE_MIN_FRESH_ROWS 와 같은 값


def _parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        ts = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def evaluate(status: dict[str, Any]) -> list[str]:
    """상태 JSON → 문제 문장 목록(빈 목록 = 정상). 순수 함수."""
    problems: list[str] = []
    now = _parse_ts(status.get("now")) or dt.datetime.now(dt.timezone.utc)
    runs = [r for r in (status.get("runs") or []) if isinstance(r, dict)]
    runs.sort(key=lambda r: _parse_ts(r.get("start")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
              reverse=True)
    finished = [r for r in runs if str(r.get("status")) in ("succeeded", "failed")]
    if finished and finished[0].get("status") == "failed":
        msg = " ".join(str(finished[0].get("message") or "").split())[:160]
        problems.append(f"가장 최근 daily 갱신 실패({finished[0].get('start')}): {msg}")
    window = now - dt.timedelta(hours=SUCCESS_WINDOW_H)
    ok_recent = [r for r in finished if r.get("status") == "succeeded"
                 and (_parse_ts(r.get("start")) or window) > window]
    if not ok_recent:
        problems.append(f"{SUCCESS_WINDOW_H}시간 안에 성공한 daily 갱신 없음(실행 {len(runs)}건 조회)")
    rows = int(status.get("daily_rows") or 0)
    fresh = int(status.get("daily_fresh") or 0)
    need = min(FRESH_CAP, int(rows * FRESH_RATIO)) if rows else 1
    if fresh < need:
        age_h = int(status.get("daily_oldest_age_s") or 0) // 3600
        problems.append(f"신선한 daily 행 {fresh}/{rows} — 기준 {need} 미만(최고령 {age_h}시간)")
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
                print("[cache-health] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
                return 2
            status = fetch_status(creds[0], creds[1], timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[cache-health] 상태 읽기 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    problems = evaluate(status)
    report = {"schema_version": SCHEMA_VERSION, "ok": not problems, "problems": problems,
              "daily_rows": status.get("daily_rows"), "daily_fresh": status.get("daily_fresh"),
              "daily_oldest_age_s": status.get("daily_oldest_age_s"),
              "runs": (status.get("runs") or [])[:6]}
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        for line in problems:
            print(f"[cache-health] 문제: {line}")
        return 1
    print(f"[cache-health] 정상 · 신선 {status.get('daily_fresh')}/{status.get('daily_rows')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
