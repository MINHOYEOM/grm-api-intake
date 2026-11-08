#!/usr/bin/env python3
"""Notion `GRM API Intake` 에만 있고 Supabase `raw_signals` 사본에 없는 행을 채운다(1회성·멱등).

배경(2026-11-07 수집 완전성 진단): 수집기는 **그 실행에 Notion 에 새로 넣은 행만** Supabase 에
append 했다. 사본 append 를 켜기 전(07-08 이전)에 들어온 행과 그날 POST 가 실패한 행은 원천에
다시 나와도 Notion 중복으로 건너뛰어 영영 사본에 없다 — ICH 0행, WHO 171건 중 20행.
앞으로는 `collect_intake.heal_supabase_copies` 가 원천에 다시 나오는 행을 매일 채운다. 원천에서
이미 내려간 옛 행은 그 경로로 못 채우므로 이 스크립트가 한 번 메운다.

동작
  1. Notion 에서 지정 소스의 행을 **날짜 제한 없이** 전부 읽는다(속성만 — 페이지당 API 1회 아님).
  2. Supabase raw_signals 에서 같은 소스의 (source, document_id) 를 전부 읽는다.
  3. 없는 행만 본문 'Raw API payload' code block 을 읽어 raw 를 복원하고, 수집기와 같은
     `grm_findings.raw_signal_from_row` 로 레코드를 만든다(raw_signal_id·raw_sha256 동일 규칙).
  4. `--apply` 일 때만 `findings_supabase_append._append_raw_signal` 로 POST 한다
     (ignore-duplicates — 기존 행은 절대 덮어쓰지 않는다. 다시 돌려도 결과가 같다).

findings(지적사항) 행은 만들지 않는다 — 채운 뒤 `findings_supabase_backfill.py` 가 맡는다.
raw 를 복원하지 못한 행은 기본으로 **건너뛰고 보고만** 한다. 빈 raw 로 넣으면 나중에 수집기가
제대로 된 raw 로 채울 기회를 막기 때문이다(append-only). `--allow-empty-raw` 로만 넣는다.

환경변수: NOTION_TOKEN, NOTION_DATABASE_ID, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
종료 코드: 0 = 정상(dry-run 포함), 1 = 일부 POST 실패, 2 = 설정 오류·조회 실패.

예)
  python backfill_raw_signals_from_notion.py --source ICH --source WHO            # dry-run
  python backfill_raw_signals_from_notion.py --source ICH --source WHO --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import findings_supabase_append as fsa
import findings_supabase_backfill as fsb
import grm_findings as gf
from grm_common import SOURCE_HANDOFF, log
from grm_handoff import _intake_page_snapshot, fetch_intake_raw_payload
from grm_notion import NOTION_DB_QUERY_URL_TPL, PROP_SOURCE, notion_api_request

# 수집기 `findings_store.raw_signal_from_intake_item` 가 row_json 에 싣는 키 — 순서·구성이 같아야
# 백필 행과 수집기 행의 row_json 모양이 같다(raw_signal_id 는 source·document_id 만으로 정해진다).
ROW_KEYS = (
    "source", "document_id", "date", "headline", "official_url", "type_or_class", "firm",
    "body", "distribution", "comments_close", "api_query", "qa_relevance", "osd_relevance",
    "source_type", "signal_tier", "source_url", "raw_excerpt", "search_query",
    "evidence_candidate", "language", "region_jurisdiction", "site_country",
)
_NOTION_MAX_PAGES = 200          # 100행 × 200 = 20,000행 — 소스 하나엔 충분한 안전 상한
_RAW_FETCH_SLEEP_S = 0.34        # Notion 3 req/s


@dataclass
class Report:
    sources: list[str] = field(default_factory=list)
    notion_rows: dict[str, int] = field(default_factory=dict)
    supabase_rows: dict[str, int] = field(default_factory=dict)
    missing: dict[str, int] = field(default_factory=dict)
    raw_unavailable: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    inserted: int = 0
    duplicate: int = 0
    failed: int = 0
    applied: bool = False


def notion_rows_for_source(token: str, db_id: str, source: str) -> list[dict[str, Any]]:
    """Notion 에서 Source=source 행 스냅샷 전부(날짜 제한 없음). 실패는 예외로 올린다."""
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {"filter": {"property": PROP_SOURCE, "select": {"equals": source}},
                            "page_size": 100}
    rows: list[dict[str, Any]] = []
    for _ in range(_NOTION_MAX_PAGES):
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or not snap["document_id"]:
                continue
            rows.append(snap)
        if not data.get("has_more"):
            return rows
        body["start_cursor"] = data.get("next_cursor")
    raise RuntimeError(f"Notion 조회 {_NOTION_MAX_PAGES}페이지 상한 — source={source} 일부 누락 가능")


def supabase_doc_ids(base_url: str, service_key: str, source: str) -> set[str]:
    rows = fsb._fetch_all_pages(
        base_url, service_key, "raw_signals", select="document_id", page_size=1000,
        order="raw_signal_id.asc", extra_params={"source": f"eq.{source}"})
    return {str(r.get("document_id") or "") for r in rows} - {""}


def record_from_snapshot(snap: dict[str, Any], raw: dict[str, Any] | None) -> dict[str, Any]:
    """Notion 스냅샷 + 복원 raw → raw_signals 레코드(수집기와 같은 빌더)."""
    row = {k: str(snap.get(k) or "").strip() for k in ROW_KEYS}
    status = "captured" if raw is not None else "notion_backfill_raw_unavailable"
    return gf.raw_signal_from_row(row, raw or {}, collected_at=str(snap.get("collected_at") or ""),
                                  extraction_status=status)


def run(sources: list[str], *, notion_token: str, notion_db: str, supabase_url: str,
        service_key: str, apply: bool, allow_empty_raw: bool = False, limit: int = 0,
        fetch_raw: Callable[[str, str], dict[str, Any] | None] = fetch_intake_raw_payload,
        sleep_s: float = _RAW_FETCH_SLEEP_S) -> Report:
    rep = Report(sources=list(sources), applied=apply)
    todo: list[dict[str, Any]] = []
    for source in sources:
        snaps = notion_rows_for_source(notion_token, notion_db, source)
        have = supabase_doc_ids(supabase_url, service_key, source)
        # 같은 document_id 의 Notion 행이 여럿이면 하나만(사본 키는 source+document_id 유일).
        seen: set[str] = set()
        miss = []
        for s in snaps:
            if s["document_id"] in have or s["document_id"] in seen:
                continue
            seen.add(s["document_id"])
            miss.append(s)
        rep.notion_rows[source] = len(snaps)
        rep.supabase_rows[source] = len(have)
        rep.missing[source] = len(miss)
        todo.extend(miss)
    if limit:
        todo = todo[:limit]
    for snap in todo:
        label = f"{snap['source']}::{snap['document_id']}"
        raw = fetch_raw(notion_token, snap["page_id"])
        if sleep_s:
            time.sleep(sleep_s)
        if raw is None:
            rep.raw_unavailable.append(label)
            if not allow_empty_raw:
                continue
        record = record_from_snapshot(snap, raw)
        errors = gf.validate_raw_signal(record)
        if errors:
            rep.invalid.append(f"{label} {errors[:2]}")
            continue
        if not apply:
            continue
        result = fsa._append_raw_signal(supabase_url, service_key, record)
        if result.status == "inserted":
            rep.inserted += 1
        elif result.status == "duplicate":
            rep.duplicate += 1
        else:
            rep.failed += 1
            log("WARN", f"POST 실패 {label}: {list(result.errors)[:2]}")
    return rep


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--source", action="append", required=True,
                    help="Notion Source 값(예: ICH, WHO). 여러 번 줄 수 있다")
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본 dry-run)")
    ap.add_argument("--allow-empty-raw", action="store_true",
                    help="raw 를 복원 못 한 행도 빈 raw 로 넣는다(기본: 건너뛰고 보고)")
    ap.add_argument("--limit", type=int, default=0, help="처리할 최대 행 수(0=전부)")
    args = ap.parse_args(argv)

    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    supabase_url = fsa._normalize_base_url(os.environ.get("SUPABASE_URL", "").strip())
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (notion_token and notion_db and supabase_url and service_key):
        log("ERROR", "NOTION_TOKEN·NOTION_DATABASE_ID·SUPABASE_URL(https)·"
                     "SUPABASE_SERVICE_ROLE_KEY 가 모두 필요하다")
        return 2
    try:
        rep = run(args.source, notion_token=notion_token, notion_db=notion_db,
                  supabase_url=supabase_url, service_key=service_key, apply=args.apply,
                  allow_empty_raw=args.allow_empty_raw, limit=args.limit)
    except Exception as e:  # noqa: BLE001 — 키가 새지 않게 예외 종류만
        log("ERROR", f"조회 실패: {type(e).__name__}")
        return 2
    print(json.dumps(asdict(rep), ensure_ascii=False, indent=2))
    mode = "적용" if rep.applied else "dry-run(쓰지 않음)"
    log("INFO", f"[{mode}] 없음 {sum(rep.missing.values())} · 넣음 {rep.inserted} · "
                f"이미 있음 {rep.duplicate} · 실패 {rep.failed} · raw 복원 불가 "
                f"{len(rep.raw_unavailable)} · 검증 실패 {len(rep.invalid)}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
