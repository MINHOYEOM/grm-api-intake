#!/usr/bin/env python3
"""[483 구체 내용 · 2026-11-11] 상세 번역 대기열 → 겹치지 않는 묶음(A·B·C) 파일. 읽기 전용(DB 쓰기 없음).

대상: findings 중 FDA 483·scope_status='ok'·detail_text_ko 가 빈 행. 원문 상세는 raw_signals 의
fda_483_observations[] 에서 (raw_signal_id, 공백 접은 표제 == finding_text) 로 짝을 찾는다. 짝이
둘 이상이거나 없으면 싣지 않는다(지어내지 않는다).

출력: <out>/<묶음>/<묶음>-<번호4>.json — 각 파일은 항목 50개 배열
  {"finding_id", "finding_text_md5", "published_date", "detail_text"}
  최신 게시일부터 50개씩 끊어 A·B·C 에 번갈아 준다(세 곳 모두 최신 문서부터 진행된다).
번역자는 같은 파일에 "detail_text_ko" 를 더해 translations/detail_outbox/<같은 파일 이름> 으로 낸다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483

SHARDS = ("A", "B", "C")
PER_FILE = 50


def _md5(text: str) -> str:
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def build_queue(findings: list[dict[str, Any]], raws: list[dict[str, Any]], *, stale_only: bool = False
                ) -> tuple[list[dict[str, Any]], dict]:
    """(대기열 — 최신 먼저, 집계). 순수 함수.

    stale_only: 이미 국문이 있는 행 중 **지금 원문 상세가 저장된 detail_text 와 다른 행**만(다시 읽기로 원문이
    바뀐 것 — 2026-11-11 실측 73%). 아니면 국문이 아직 없는 행."""
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in raws:
        raw = r.get("raw_json")
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
        for o in raw.get("fda_483_observations") or []:
            if isinstance(o, dict) and str(o.get("detail") or "").strip():
                by_key[(str(r.get("raw_signal_id")), _compact(o.get("deficiency")))].append(str(o["detail"]))
    queue, stats = [], {"findings": len(findings), "queued": 0, "no_detail": 0, "ambiguous": 0, "unchanged": 0}
    for f in findings:
        hits = by_key.get((str(f.get("raw_signal_id")), _compact(f.get("finding_text"))), [])
        if not hits:
            stats["no_detail"] += 1
            continue
        if len(set(hits)) > 1:
            stats["ambiguous"] += 1
            continue
        if stale_only and hits[0] == str(f.get("detail_text") or ""):
            stats["unchanged"] += 1
            continue
        queue.append({"finding_id": str(f["finding_id"]), "finding_text_md5": _md5(f.get("finding_text") or ""),
                      "published_date": str(f.get("published_date") or ""), "detail_text": hits[0]})
    queue.sort(key=lambda x: (x["published_date"], x["finding_id"]), reverse=True)
    stats["queued"] = len(queue)
    stats["chars"] = sum(len(x["detail_text"]) for x in queue)
    return queue, stats


def write_shards(queue: list[dict[str, Any]], out: Path, shards: tuple[str, ...] = SHARDS
                 ) -> dict[str, dict[str, int]]:
    summary = {s: {"files": 0, "items": 0, "chars": 0} for s in shards}
    for i in range(0, len(queue), PER_FILE):
        chunk = queue[i:i + PER_FILE]
        shard = shards[(i // PER_FILE) % len(shards)]
        n = summary[shard]["files"] + 1
        d = out / shard
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{shard}-{n:04d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
        summary[shard]["files"] = n
        summary[shard]["items"] += len(chunk)
        summary[shard]["chars"] += sum(len(x["detail_text"]) for x in chunk)
    return summary


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="483 상세 번역 대기열을 A·B·C 묶음 파일로(읽기 전용).")
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=("pending", "stale"), default="pending",
                   help="pending = 국문 없는 행 · stale = 국문 있으나 원문이 바뀐 행(재번역).")
    p.add_argument("--prefix", default="", help="묶음 이름(쉼표). 기본 pending=A,B,C · stale=R.")
    p.add_argument("--supabase-url")
    p.add_argument("--service-role-key")
    args = p.parse_args(argv)
    creds = _resolve_credentials(args)
    if creds is None:
        print("findings_detail_export: Supabase 자격 증명 필요", file=sys.stderr)
        return 2
    base = _normalize_base_url(creds[0])
    findings = fsb._fetch_all_pages(
        base, creds[1], "findings", select="finding_id,raw_signal_id,published_date,finding_text,detail_text",
        page_size=1000, order="finding_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "scope_status": "eq.ok",
                      "detail_text_ko": "not.is.null" if args.mode == "stale" else "is.null"})
    raws = fsb._fetch_all_pages(
        base, creds[1], "raw_signals", select="raw_signal_id,raw_json", page_size=200,
        order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "raw_json": "like.*fda_483_observations*"})
    queue, stats = build_queue(findings, raws, stale_only=args.mode == "stale")
    out = Path(args.out)
    shards = tuple(x for x in args.prefix.split(",") if x) or (("R",) if args.mode == "stale" else SHARDS)
    summary = write_shards(queue, out, shards)
    report = {"mode": args.mode, "stats": stats, "shards": summary}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"summary-{'-'.join(shards)}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
