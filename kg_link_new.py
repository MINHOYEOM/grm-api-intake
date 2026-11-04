#!/usr/bin/env python3
"""새로 들어온 지적을 지식그래프에 자동 연결한다(일 1회, 멱등).

1) RPC `grm_link_new_findings(p_days)`(108): 최근 지적의 fei·조항 엣지를 DB 안에서 채운다.
   — 백필과 같은 SQL 한 벌(`*_since`)이라 규칙이 어긋나지 않는다.
2) 용어 엣지: 한국어 본문이 있는 최근 지적을 읽어 `kg_edges.build_term_edges`(카드/문서 용어 링크와 같은
   엔진, 백필과 같은 코드)로 계산하고 `finding_term` 에 `ignore-duplicates` 로 넣는다. 번역 지연으로
   본문이 나중에 생기는 지적도 창(기본 7일) 안에서 다시 잡힌다.

RPC/표가 아직 없으면(108·107 적용 전) 그 단계만 건너뛰고 0 으로 끝난다. 수집 경로는 건드리지 않는다.
서비스 키는 로그·예외 메시지에 넣지 않는다(예외 타입명·HTTP status 만).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

import kg_edges
from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials

ROOT = Path(__file__).resolve().parent
PAGE = 1000
BATCH = 500


def _headers(key: str, **extra: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", **extra}


def _call(fn):
    try:
        return fn()
    except requests.RequestException as exc:
        raise RuntimeError(f"요청 실패: {type(exc).__name__}") from None


def link_db(base: str, key: str, days: int, timeout: float = 120.0) -> dict[str, Any] | None:
    """fei·조항 단계. 108 미적용이면 None."""
    resp = _call(lambda: requests.post(f"{base}/rest/v1/rpc/grm_link_new_findings", headers=_headers(key),
                                       json={"p_days": days}, timeout=timeout))
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"grm_link_new_findings HTTP {resp.status_code}")
    return resp.json()


def fetch_recent_texts(base: str, key: str, since_iso: str, timeout: float = 120.0) -> list[dict[str, str]]:
    """창 안에 들어온, 한국어 본문이 있는 지적 [{finding_id, text_ko}]."""
    rows: list[dict[str, str]] = []
    offset = 0
    while True:
        resp = _call(lambda: requests.get(
            f"{base}/rest/v1/findings", headers=_headers(key, Range=f"{offset}-{offset + PAGE - 1}"), timeout=timeout,
            params={"select": "finding_id,finding_text_ko", "ingested_at": f"gte.{since_iso}",
                    "finding_text_ko": "neq.", "order": "finding_id.asc"}))
        if resp.status_code not in (200, 206):
            raise RuntimeError(f"findings HTTP {resp.status_code}")
        page = resp.json()
        rows.extend({"finding_id": r["finding_id"], "text_ko": r["finding_text_ko"]} for r in page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def write_term_edges(base: str, key: str, edges: list[dict[str, Any]], timeout: float = 120.0) -> int | None:
    """finding_term 에 멱등 삽입. 표 미적용(404)이면 None."""
    sent = 0
    for i in range(0, len(edges), BATCH):
        chunk = [{"finding_id": e["finding_id"], "term_id": e["term_id"], "surface": e["surface"],
                  "confidence": e["confidence"]} for e in edges[i:i + BATCH]]
        resp = _call(lambda: requests.post(
            f"{base}/rest/v1/finding_term",
            headers=_headers(key, Prefer="resolution=ignore-duplicates,return=minimal"),
            params={"on_conflict": "finding_id,term_id"}, json=chunk, timeout=timeout))
        if resp.status_code == 404:
            return None
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"finding_term HTTP {resp.status_code}")
        sent += len(chunk)
    return sent


def compute_edges(rows: list[dict[str, str]], terms: list[dict]) -> tuple[list[dict], dict]:
    """백필(kg_edges terms CLI)과 같은 함수 — 입력만 최근 지적이다."""
    return kg_edges.build_term_edges([{"findings": rows}], terms)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7, help="이 창 안에 들어온 지적을 (재)연결한다")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--dry-run", action="store_true", help="네트워크 0")
    ap.add_argument("--glossary", default=str(ROOT / "web" / "data" / "glossary.json"))
    a = ap.parse_args(argv)
    if a.dry_run:
        print(f"[kg-link] dry-run · days={a.days} · 네트워크 0")
        return 0
    creds = resolve_supabase_service_credentials(a)
    if creds is None:
        print("[kg-link] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
        return 2
    base = normalize_supabase_url(creds[0])
    if base is None:
        print("[kg-link] SUPABASE_URL 형식 오류", file=sys.stderr)
        return 2
    key = creds[1]
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        db = link_db(base, key, a.days)
        print("[kg-link] fei·조항: " + (json.dumps(db, ensure_ascii=False) if db is not None else "SKIP(108 미적용)"))
        terms = json.loads(Path(a.glossary).read_text(encoding="utf-8"))
        rows = fetch_recent_texts(base, key, since)
        edges, _stats = compute_edges(rows, terms)
        sent = write_term_edges(base, key, edges)
        print(f"[kg-link] 용어: 지적 {len(rows):,}건 스캔 · 엣지 {len(edges):,} · "
              + (f"전송 {sent:,}" if sent is not None else "SKIP(107 미적용)"))
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"[kg-link] 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
