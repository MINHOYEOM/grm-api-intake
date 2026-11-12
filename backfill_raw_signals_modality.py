#!/usr/bin/env python3
"""raw_signals.modality 소급 — 비어 있는 행의 제품군을 수집기와 같은 함수로 다시 계산해 채운다.

배경(2026-11-12 재생 대조, `GRM_재생대조_결과_2026-11-12.md`):
  Notion 적재(`grm_notion.notion_create_page`)는 `compute_modality(raw, headline, body,
  type_or_class, firm)` 로 제품군을 붙이지만, 정본 행을 만드는 `findings_store.raw_signal_from_intake_item`
  은 그 칸을 싣지 않았다. 그래서 raw_signals 9천여 행의 99% 가 modality 빈칸이고, DB 로 만든
  handoff 의 카드 문안·묶음이 노션 것과 달라졌다. 새 행은 같은 PR 에서 수집 시점에 채운다.
  이 도구는 **이미 들어간 행**을 같은 규칙으로 채운다.

규칙:
  - 대상: modality 가 null 또는 빈 문자열인 행만. 이미 값이 있는 행은 건드리지 않는다.
  - 입력: raw_json(수집 원본) + row_json 의 headline·body·type_or_class·firm — 수집기와 같은 순서.
  - 판별 근거가 없으면 ""(MODALITY_UNKNOWN) → **쓰지 않는다**(빈칸 그대로 = "모름").
  - 바꾸는 칸은 `modality` 하나다. raw_json·row_json·raw_sha256 은 건드리지 않는다
    (원문 판 관리 R1 의 `raw_signal_apply_version` 대상이 아니다 — 원문이 아니라 파생 칸).

기본은 dry-run: 소스별·판정별 건수와 예시만 보고한다. `--apply` 일 때만 PATCH 한다.
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (값은 로그에 남기지 않는다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Callable

import findings_supabase_append as fsa
import findings_supabase_backfill as fsb
from grm_common import log
from grm_taxonomy import compute_modality

SELECT_COLUMNS = "raw_signal_id,source,document_id,modality,raw_json,row_json"
PAGE_SIZE = 200
PATCH_CHUNK = 100


def _loads(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def modality_for_row(row: dict[str, Any]) -> str:
    """raw_signals 한 행 → 제품군. 수집기(`findings_store.item_modality`)와 같은 입력 순서."""
    raw = _loads(row.get("raw_json"))
    rj = _loads(row.get("row_json"))
    parts = [str(rj.get(k) or "").strip() for k in ("headline", "body", "type_or_class", "firm")]
    return compute_modality(raw, *parts)


def plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """빈칸 행마다 제품군을 계산해 {값: [raw_signal_id…]} 와 집계를 만든다(순수 함수)."""
    by_value: dict[str, list[str]] = defaultdict(list)
    per_source: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    skipped_filled = 0
    for row in rows:
        if str(row.get("modality") or "").strip():
            skipped_filled += 1
            continue
        value = modality_for_row(row)
        source = str(row.get("source") or "")
        per_source[source][value or "(근거 없음)"] += 1
        if value:
            by_value[value].append(str(row["raw_signal_id"]))
            key = f"{source}|{value}"
            if len(examples[key]) < 3:
                examples[key].append(str(row.get("document_id") or ""))
    return {
        "scanned": len(rows),
        "skipped_already_filled": skipped_filled,
        "to_fill": {k: len(v) for k, v in sorted(by_value.items())},
        "unknown": sum(c.get("(근거 없음)", 0) for c in per_source.values()),
        "per_source": {s: dict(c) for s, c in sorted(per_source.items())},
        "examples": dict(sorted(examples.items())),
        "_ids": dict(by_value),
    }


def fetch_blank_rows(base_url: str, service_key: str, *, source: str = "",
                     limit: int = 0) -> list[dict[str, Any]]:
    params = {"or": "(modality.is.null,modality.eq.)"}
    if source:
        params["source"] = f"eq.{source}"
    rows = fsb._fetch_all_pages(base_url, service_key, "raw_signals", select=SELECT_COLUMNS,
                                page_size=PAGE_SIZE, order="raw_signal_id.asc",
                                extra_params=params)
    return rows[:limit] if limit else rows


def _patch_chunk(base_url: str, service_key: str, ids: list[str], value: str) -> None:
    import requests
    resp = requests.patch(
        f"{base_url}/rest/v1/raw_signals",
        # 빈칸 조건을 다시 건다 — 그사이 다른 경로가 채운 행은 덮지 않는다.
        params={"raw_signal_id": f"in.({','.join(ids)})", "or": "(modality.is.null,modality.eq.)"},
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={"modality": value},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"PATCH HTTP {resp.status_code}")


def apply_plan(result: dict[str, Any], *, base_url: str, service_key: str,
               patcher: Callable[[str, str, list[str], str], None] | None = None) -> dict[str, int]:
    patcher = patcher or _patch_chunk
    done: dict[str, int] = {}
    for value, ids in sorted(result["_ids"].items()):
        n = 0
        for i in range(0, len(ids), PATCH_CHUNK):
            chunk = ids[i:i + PATCH_CHUNK]
            patcher(base_url, service_key, chunk, value)
            n += len(chunk)
        done[value] = n
    return done


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows cp949 콘솔에서 한글·기호 출력 보호
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본 dry-run)")
    ap.add_argument("--source", default="", help="한 소스만(예: 'FDA 483'). 비우면 전부")
    ap.add_argument("--limit", type=int, default=0, help="처리할 최대 행 수(0=전부)")
    ap.add_argument("--report", default="modality_backfill_report.json")
    args = ap.parse_args(argv)

    base_url = fsa._normalize_base_url(os.environ.get("SUPABASE_URL", "").strip())
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base_url or not service_key:
        log("ERROR", "SUPABASE_URL(https)·SUPABASE_SERVICE_ROLE_KEY 가 필요하다")
        return 2

    rows = fetch_blank_rows(base_url, service_key, source=args.source, limit=args.limit)
    result = plan(rows)
    report = {k: v for k, v in result.items() if k != "_ids"}
    report["mode"] = "apply" if args.apply else "dry-run"
    if args.apply:
        report["patched"] = apply_plan(result, base_url=base_url, service_key=service_key)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    log("INFO", f"modality 소급 {report['mode']}: 빈칸 {report['scanned']}행 · 채움 "
                f"{report['to_fill']} · 근거 없음 {report['unknown']}"
                + (f" · 기록 {report['patched']}" if args.apply else ""))
    for source, counts in report["per_source"].items():
        log("INFO", f"  {source}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
