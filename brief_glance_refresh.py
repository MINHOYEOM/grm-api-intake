#!/usr/bin/env python3
"""[브리프 주간 현황] `web/data/brief_glance.json` 정본을 다시 만든다.

## 왜

주간 브리프 화면의 '주간 현황'은 FDA 483·경고서한 카드가 어느 국가 제조소에 몰렸는지,
어떤 분류 지적이 많았는지를 한 줄로 보여 준다. 국가·분류는 **DB 값**(`findings.country_key`·
`category_code`)이어야 한다 — 업체 이름으로 국가를 추정하지 않는다(대표 확정 UI/UX 0절 5번).
렌더러는 네트워크를 타지 않으므로, 카드 원문 URL → (국가, 분류) 표를 이 스크립트가 미리
받아 커밋 정본으로 둔다. 화면은 이 표로 정적으로 그린다(브리프 페이지가 따로 묻지 않는다).

## 권한

`findings` 표의 공개 읽기 정책(`findings_public_read`, anon)으로 세 컬럼만 읽는다.
service-role 금지 — 비공개 행이 공개 페이지로 새는 사고를 막는다(news_refresh.py 와 같은 규율).

## 게이트

  · 대상 URL 이 있는데 한 행도 못 받으면 DB·정책 장애로 보고 아무것도 쓰지 않고 실패.
  · 파일은 결정론적이다(정렬 고정·생성 시각 없음 — `measured_on` 만).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url

SCHEMA_VERSION = "grm-brief-glance/v1"
ROOT = Path(__file__).resolve().parent
DEFAULT_BRIEFS = ROOT / "web" / "data" / "briefs"
DEFAULT_OUT = ROOT / "web" / "data" / "brief_glance.json"
#: 국가·분류를 세는 카드 종류 — 문서 한 건이 곧 한 제조소인 종류만.
DOC_TYPES = ("FDA 483 실사 관찰", "Warning Letter")
BATCH = 15


def collect_urls(briefs_dir: Path) -> list[str]:
    urls: set[str] = set()
    for fp in sorted(briefs_dir.glob("*.json")):
        data = json.loads(fp.read_text(encoding="utf-8"))
        for card in data.get("cards") or []:
            if card.get("card_type") not in DOC_TYPES:
                continue
            u = ((card.get("sources") or {}).get("official_url") or "").strip()
            if u.startswith("http"):
                urls.add(u)
    return sorted(urls)


def fetch_rows(base_url: str, anon_key: str, urls: list[str], *,
               timeout: int = 60) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(0, len(urls), BATCH):
        chunk = urls[i:i + BATCH]
        flt = "in.(" + ",".join('"' + u.replace('"', "") + '"' for u in chunk) + ")"
        resp = requests.get(
            f"{base_url}/rest/v1/findings",
            headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
            params={"select": "evidence_url,country_key,category_code", "evidence_url": flt},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"findings 응답이 배열이 아님: {type(data).__name__}")
        rows.extend(data)
    return rows


def build_items(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = r.get("evidence_url")
        if not isinstance(u, str) or not u:
            continue
        e = items.setdefault(u, {"c": "", "k": set()})
        ck = r.get("country_key")
        if not e["c"] and isinstance(ck, str) and ck:
            e["c"] = ck
        cc = r.get("category_code")
        if isinstance(cc, str) and cc:
            e["k"].add(cc)
    return {u: {"c": e["c"], "k": sorted(e["k"])} for u, e in sorted(items.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--supabase-url", required=True)
    ap.add_argument("--supabase-anon-key", required=True)
    ap.add_argument("--briefs", type=Path, default=DEFAULT_BRIEFS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    urls = collect_urls(args.briefs)
    rows = fetch_rows(_normalize_supabase_url(args.supabase_url), args.supabase_anon_key, urls)
    if urls and not rows:
        print(f"대상 URL {len(urls)}개에 행 0 — DB·정책 장애로 보고 쓰지 않는다", file=sys.stderr)
        return 1
    items = build_items(rows)
    out = {"schema": SCHEMA_VERSION, "measured_on": date.today().isoformat(),
           "doc_types": list(DOC_TYPES), "items": items}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"brief_glance: 대상 URL {len(urls)} · 일치 {len(items)} · 행 {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
