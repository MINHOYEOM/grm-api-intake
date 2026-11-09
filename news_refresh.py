#!/usr/bin/env python3
"""[최근 규제 소식] `/news/` 페이지의 정본(`web/data/news.json`)을 다시 만든다.

## 왜

raw_signals 에는 주간 브리프에 실리지 않는 비-findings 소식(ECA·ISPE·EMA·관보·OpenFDA/HC/MHRA
회수·PIC/S·WHO·MFDS 지침/공고)이 쌓이는데, 브리프 밖에서는 어디에도 보이지 않았다. 월요일 사이의
공백을 메우는 상설 페이지가 이 정본을 읽는다.

## 권한 — 공개 RPC `regulatory_news` (마이그 138) 를 anon 키로 부른다

raw_signals 는 anon 에 전면 차단이라 직접 못 읽는다. RPC 가 **공개 안전 5개 컬럼**(source·
source_kind·published_date·title·official_url)만 돌려주고, 범위 밖(qa_relevance=Unrelated)·
findings 연결분·허용목록 밖 소스는 서버에서 거른다. service-role 금지 — 비공개 행이 공개
페이지로 새는 사고를 막는다(findings_docs_refresh.py 와 같은 규율).

## 게이트

  · 0건 가드 — RPC 장애를 "소식이 없다"로 커밋하면 페이지가 비어 버린다 → 아무것도 쓰지 않고 실패.
  · 형식 위반(제목 공백·http(s) 아닌 링크·날짜 깨짐)은 건너뛰고 사유별로 센다.
  · 같은 (원문 링크, 제목) 은 한 건으로 접는다.
  · 표시용 그룹은 소스에서 **데이터로** 파생한다(`web/news_page.group_of`) — 이 스크립트는 그룹을 모른다.
  · 파일은 결정론적이다(정렬 고정·생성 시각 없음 — `measured_on` 만) → 소식이 안 바뀐 날은 diff 0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url

SCHEMA_VERSION = "grm-news/v1"
RPC_NAME = "regulatory_news"
DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "news.json"
DEFAULT_DAYS = 60
#: 공개 안전 컬럼 — 이 집합 밖의 키는 RPC 가 실수로 더 줘도 정본에 싣지 않는다.
PUBLIC_FIELDS = ("source", "source_kind", "published_date", "title", "official_url")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fetch_rows(base_url: str, anon_key: str, days: int, *, timeout: int = 60) -> list[dict[str, Any]]:
    resp = requests.post(
        f"{base_url}/rest/v1/rpc/{RPC_NAME}",
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}",
                 "Content-Type": "application/json"},
        json={"p_days": days}, timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"{RPC_NAME} 응답이 배열이 아님: {type(data).__name__}")
    return data


def build_snapshot(rows: "list[dict[str, Any]]", *, today: str, days: int) -> "tuple[dict[str, Any], Counter]":
    """RPC 행 → 정본. 순수 함수(네트워크 0). 반환 = (정본, 제외 사유 카운터)."""
    reject: Counter = Counter()
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, str]] = []
    for r in rows:
        item = {k: " ".join(str(r.get(k) or "").split()) for k in PUBLIC_FIELDS}
        if not item["title"]:
            reject["제목 없음"] += 1
            continue
        if not re.match(r"^https?://", item["official_url"], re.I):
            reject["원문 링크 없음"] += 1
            continue
        if not _DATE_RE.match(item["published_date"]):
            reject["날짜 형식"] += 1
            continue
        key = (item["official_url"], item["title"])
        if key in seen:
            reject["중복"] += 1
            continue
        seen.add(key)
        items.append(item)
    # 최신순 — 같은 날은 소스·제목 오름차순(결정론)
    items.sort(key=lambda i: (i["source"], i["source_kind"], i["title"], i["official_url"]))
    items.sort(key=lambda i: i["published_date"], reverse=True)
    by_source = Counter(i["source"] for i in items)
    snap = {
        "schema": SCHEMA_VERSION,
        "measured_on": today,
        "window_days": days,
        "totals": {"items": len(items)},
        "by_source": [{"v": k, "c": c} for k, c in sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))],
        "items": items,
    }
    return snap, reject


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--supabase-url", required=True)
    ap.add_argument("--supabase-anon-key", required=True)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--from-json", type=Path, default=None,
                    help="RPC 응답 JSON 파일(배열)로 대신 만든다 — 시드·오프라인 검증용")
    args = ap.parse_args(argv)

    today = date.today().isoformat()
    if args.from_json:
        rows = json.loads(args.from_json.read_text(encoding="utf-8"))
    else:
        base = _normalize_supabase_url(args.supabase_url)
        if not base:
            print("SUPABASE_URL 이 https:// 로 시작해야 합니다.", file=sys.stderr)
            return 2
        rows = fetch_rows(base, args.supabase_anon_key, args.days)

    snap, reject = build_snapshot(rows, today=today, days=args.days)
    if not snap["items"]:
        print("0건 — 쓰지 않습니다(RPC 장애를 '소식 없음'으로 확정하지 않는다).", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"소식 {snap['totals']['items']}건 → {args.out}")
    for k, c in reject.items():
        print(f"  제외 {k}: {c}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
