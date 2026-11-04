#!/usr/bin/env python3
"""지식그래프 연결 품질 감시 — 최근 신규 지적 중 fei·조항·용어 연결이 비어 있는 비율을 한 줄로 낸다.

RPC `kg_link_health`(108)의 재료를 받아 비율과 임계 판정을 파이썬에서 한다(임계를 DB 에 박지 않는다).
출력은 종합상황실 점검에 그대로 붙일 수 있는 한 줄 요약이다:

  KG-LINK OK 7d | fei(483) 100.0% (1,200/1,200) | clause 99.8% (3,100/3,106) | term 47.2% (900/1,907)
  KG-LINK WARN 7d | fei(483) 96.0% (1,152/1,200) <99% | clause 99.8% ... | term 47.2% ...

임계(기본): fei 채움 >= 99% · 조항 연결 >= 99% · 용어 연결 >= 25%. 용어는 정상 상태가 약 48%(본문에
표제어가 없는 지적이 절반)라 99% 를 요구할 수 없고, 25% 미만이면 일일 연결이 멈춘 신호로 본다.
분모가 20 미만이면 표본 부족으로 판정하지 않는다(SKIP). 위반이 있으면 종료코드 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials

RPC_NAME = "kg_link_health"
MIN_DENOMINATOR = 20
DEFAULT_THRESHOLDS = {"fei": 0.99, "clause": 0.99, "term": 0.25}


def _rate(num: int, den: int) -> float | None:
    return None if den <= 0 else num / den


def summarize(payload: dict[str, Any], thresholds: dict[str, float] | None = None) -> tuple[str, list[str]]:
    """(한 줄 요약, 위반 목록). 순수 함수 — 네트워크 0."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    fei = payload.get("fei") or {}
    clause = payload.get("clause") or []
    term = payload.get("term") or []
    parts: list[str] = []
    breaches: list[str] = []

    def part(label: str, num: int, den: int, key: str) -> None:
        r = _rate(num, den)
        if r is None or den < MIN_DENOMINATOR:
            parts.append(f"{label} SKIP(표본 {den})")
            return
        txt = f"{label} {r * 100:.1f}% ({num:,}/{den:,})"
        if r < th[key]:
            txt += f" <{th[key] * 100:g}%"
            breaches.append(f"{label} {r * 100:.1f}% < {th[key] * 100:g}%")
        parts.append(txt)

    part("fei(483)", int(fei.get("filled") or 0), int(fei.get("fillable") or 0), "fei")
    part("clause", sum(int(c.get("linked") or 0) for c in clause),
         sum(int(c.get("expected") or 0) for c in clause), "clause")
    part("term", sum(int(t.get("linked") or 0) for t in term),
         sum(int(t.get("texts") or 0) for t in term), "term")
    status = "WARN" if breaches else "OK"
    line = f"KG-LINK {status} {payload.get('window_days', '?')}d | " + " | ".join(parts)
    return line, breaches


def fetch(base_url: str, key: str, days: int, grace_hours: int, timeout: float = 60.0) -> dict[str, Any] | None:
    """RPC 호출. 108 미적용(404)이면 None."""
    base = normalize_supabase_url(base_url)
    if base is None:
        raise ValueError("SUPABASE_URL 형식 오류(https:// 필요)")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(f"{base}/rest/v1/rpc/{RPC_NAME}", headers=headers, timeout=timeout,
                             json={"p_days": days, "p_grace_hours": grace_hours})
    except requests.RequestException as exc:  # 키가 담길 수 있는 메시지는 쓰지 않는다
        raise RuntimeError(f"RPC 요청 실패: {type(exc).__name__}") from None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"RPC HTTP {resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("RPC 응답이 객체가 아님")
    return body


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--grace-hours", type=int, default=36)
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--from-json", help="RPC 응답 JSON 파일로 판정만(네트워크 0)")
    a = ap.parse_args(argv)
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        creds = resolve_supabase_service_credentials(a)
        if creds is None:
            print("KG-LINK SKIP | SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
            return 2
        try:
            payload = fetch(creds[0], creds[1], a.days, a.grace_hours)
        except (RuntimeError, ValueError) as exc:
            print(f"KG-LINK ERROR | {exc}", file=sys.stderr)
            return 1
        if payload is None:
            print("KG-LINK SKIP | kg_link_health RPC 미적용(108 적용 전)")
            return 0
    line, breaches = summarize(payload)
    print(line)
    return 1 if breaches else 0


if __name__ == "__main__":
    sys.exit(main())
