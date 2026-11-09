#!/usr/bin/env python3
"""구글 색인 표본 점검 — 얇은 문서 vs 두꺼운 문서가 구글에 실제로 색인됐는가 (2026-11-09).

왜: 11-09 Search Console 에서 "크롤링됨/발견됨 - 현재 색인 안 됨" 2,793장이 대부분 얇은 문서·업체
페이지였다. #1429 가 얇은 페이지에 googlebot 전용 noindex 를 걸었고, 2단계(해설 보강)가 뒤따른다.
그 효과를 "느낌"이 아니라 매주 같은 방식의 표본으로 본다.

무엇을: `web/data/findings_docs.json` 문서 페이지를 render.is_thin_doc_page 로 얇음/두꺼움으로 나누고,
각 묶음에서 주차 고정(결정론) 표본 N 개를 골라 **URL Inspection API**
(POST searchconsole.googleapis.com/v1/urlInspection/index:inspect)로 색인 상태를 묻는다.
하루 쿼터는 속성당 2,000건 — 기본 표본 ko·en × 얇음·두꺼움 × 15 = 60건.

결과: GitHub Job Summary 표 + `gsc_index_sample.json`(아티팩트). DB 에는 쓰지 않는다(마이그레이션 없음).
읽는 법: 두꺼운 묶음의 PASS(색인됨) 비율이 오르는지, 얇은 묶음은 "noindex 태그로 제외"로 바뀌는지.

인증·속성 선택은 collect_search_console.py 를 그대로 쓴다(서비스 계정 `GSC_SERVICE_ACCOUNT_JSON`).
미설정이면 클린 skip(exit 0).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
from collections import Counter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "web"))

INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
SITE = "https://grm-solutions.com"
DEFAULT_PER_BUCKET = 15


def week_key(today: dt.date) -> str:
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


def pick_sample(doc_ids: list[str], n: int, salt: str) -> list[str]:
    """주차 고정 결정론 표본 — 같은 주에 다시 돌려도 같은 표본(순수 함수)."""
    keyed = sorted(doc_ids, key=lambda d: hashlib.sha256((salt + d).encode()).hexdigest())
    return keyed[:n]


def doc_url(doc_id: str, lang: str) -> str:
    return f"{SITE}/{'en/' if lang == 'en' else ''}findings/doc/{doc_id}/"


def buckets(docs: list[dict[str, Any]], lang: str) -> dict[str, list[str]]:
    import render
    out: dict[str, list[str]] = {"thin": [], "thick": []}
    for d in docs:
        did = str(d.get("document_id") or "")
        if not did:
            continue
        out["thin" if render.is_thin_doc_page(d, lang) else "thick"].append(did)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """묶음별 coverageState 개수(순수 함수)."""
    agg: dict[str, Counter] = {}
    for r in rows:
        agg.setdefault(f"{r['lang']}·{r['bucket']}", Counter())[r.get("coverage") or r.get("error") or "?"] += 1
    return {k: dict(v.most_common()) for k, v in agg.items()}


def inspect(token: str, site_url: str, url: str) -> dict[str, Any]:
    import requests
    r = requests.post(INSPECT_URL, timeout=30,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                      json={"inspectionUrl": url, "siteUrl": site_url, "languageCode": "ko"})
    if r.status_code >= 300:
        return {"error": f"HTTP {r.status_code}"}
    res = ((r.json() or {}).get("inspectionResult") or {}).get("indexStatusResult") or {}
    return {"verdict": res.get("verdict"), "coverage": res.get("coverageState"),
            "indexing": res.get("indexingState"), "last_crawl": res.get("lastCrawlTime")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=DEFAULT_PER_BUCKET)
    ap.add_argument("--docs", default=str(ROOT / "web" / "data" / "findings_docs.json"))
    ap.add_argument("--out", default="gsc_index_sample.json")
    a = ap.parse_args(argv)

    raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        print("GSC_SERVICE_ACCOUNT_JSON 미설정 - skip")
        return 0
    import collect_search_console as csc
    token = csc.access_token(raw)
    site_url = os.environ.get("GSC_SITE_URL", "").strip() or csc.pick_site(
        csc.list_sites(token), "grm-solutions.com")

    docs = (json.load(open(a.docs, encoding="utf-8")) or {}).get("documents") or []
    wk = week_key(dt.date.today())
    rows: list[dict[str, Any]] = []
    for lang in ("ko", "en"):
        for bucket, ids in buckets(docs, lang).items():
            for did in pick_sample(ids, a.per_bucket, f"{wk}:{lang}:{bucket}:"):
                url = doc_url(did, lang)
                rows.append({"lang": lang, "bucket": bucket, "url": url, **inspect(token, site_url, url)})

    summary = summarize(rows)
    pathlib.Path(a.out).write_text(json.dumps({"week": wk, "summary": summary, "rows": rows},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"## 구글 색인 표본 {wk} (묶음당 {a.per_bucket}건)", "", "| 묶음 | 상태별 개수 |", "|---|---|"]
    for k, v in sorted(summary.items()):
        lines.append(f"| {k} | " + " · ".join(f"{s} {n}" for s, n in v.items()) + " |")
    text = "\n".join(lines) + "\n"
    print(text)
    gs = os.environ.get("GITHUB_STEP_SUMMARY")
    if gs:
        with open(gs, "a", encoding="utf-8") as f:
            f.write(text)
    errors = sum(1 for r in rows if r.get("error"))
    return 1 if rows and errors == len(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
