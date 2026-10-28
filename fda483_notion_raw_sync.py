#!/usr/bin/env python3
"""미소비 Notion 483 행의 빈 raw 를 Supabase 의 본문 있는 raw 로 채운다 — 주간 브리프용.

## 왜 필요한가 (2026-10-27 #1209)

주간 브리프 스캐폴드는 Notion Intake 의 Status=New 행에서 만들어지고, 그 행의 raw 는
페이지 본문(children)의 'Raw API payload' code block 에서 읽는다
(`grm_handoff.fetch_intake_raw_payload`). 그런데 FDA 483 본문은 **두 경로**로 들어온다:

  · 일일 수집(grm-intake)      → Notion 행 + Supabase raw_signals
  · 백필 페치(backfill-fetch)  → Supabase raw_signals 만(Notion 무접촉)

FDA 가 483 을 대량 공개하면 일일 수집은 실행당 상한(PDF 건수·OCR 예산)에 걸려 일부 문서를
본문 없이 Notion 에 넣는다. 같은 문서의 본문은 이미 Supabase 에 있거나(백필 페치가 먼저
받음), 다음 날 `grm-fda483-ocr-recovery` 예약 실행이 Supabase 에 회수해 넣는다. 그러나
Notion 행은 그대로라 브리프에서는 "관찰 본문 미확보" 묶음 카드로 접혔다 — 10-26 호에서
실사 25건이 그렇게 나갔고, 그중 13건은 우리 findings DB 에 관찰이 이미 있었다.

## 무엇을 하는가

Status=New 인 FDA 483 Notion 행마다:
  1. Notion raw 를 읽어 본문이 **없을 때만** 대상으로 삼는다(`card_scaffold._has_source_body`
     — 스캐폴드가 묶기 여부를 판정하는 바로 그 기준).
  2. 같은 document_id 의 Supabase raw_signals.raw_json 을 읽어 본문이 **있을 때만** 교체한다.
     Supabase 쪽도 비었으면 손대지 않는다(빈손을 빈손으로 덮지 않는다).
  3. 페이지 children 을 Supabase raw 로 교체한다. Status·Handoff Ref 등 속성은 건드리지 않는다.

Notion 행이 소비(Status≠New)된 뒤에는 대상이 아니다 — 이미 발행된 주는 발행본 소급 수리
(`grm-fda483-ocr-backfill.yml`) 경로다.

## FDA Warning Letter (`--source wl`, 2026-10-28)

WL 도 같은 구조다 — 편지 GET 이 실패하면 Notion 행과 Supabase 행이 함께 본문 없이 들어가고,
예약 회수(`backfill_wl_body_recovery.py`)는 Supabase 만 고친다. WL 의 "본문" 판정은
`wl_body_full`(심층분석 fan-out 의 유일한 입력)이다 — 발췌(`wl_body_excerpt`)만 있는 행은
카드 신호(`_has_source_body`)로는 참이지만 fan-out 이 돌지 않으므로 동기화 대상이다.

dry-run 이 기본이다. `--apply` 일 때만 Notion 을 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import findings_supabase_backfill as fsb
from card_scaffold import _has_source_body
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483, SOURCE_FDA_WL, log
from grm_handoff import fetch_intake_raw_payload, notion_query_new_intake_rows
from grm_notion import (
    NOTION_BLOCK_CHILDREN_URL_TPL,
    NotionHandoffError,
    build_raw_payload_children,
    notion_api_request,
)

SCHEMA_VERSION = "grm-483-notion-raw-sync/v2"


def _has_wl_body_full(raw: dict[str, Any]) -> bool:
    return bool(raw.get("wl_body_full"))


# 소스별 계약 — Notion 소스명 · "본문 있음" 판정 · 리포트에 셀 항목 키.
SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "fda483": {"source": SOURCE_FDA_483, "has_body": _has_source_body,
               "items_key": "fda_483_observations"},
    "wl": {"source": SOURCE_FDA_WL, "has_body": _has_wl_body_full,
           "items_key": "wl_violations"},
}
DEFAULT_WINDOW_DAYS = 45
_DOC_ID_BATCH = 50
_NOTION_SLEEP_SECONDS = 0.34
_NOTION_APPEND_BATCH = 90
# Notion Run Date 는 KST 날짜다. UTC 러너의 date.today() 를 쓰면 KST 아침(=UTC 전날 밤)에
# 그날 Run Date 행이 '미래'로 빠진다(조회 조건이 Run Date ≤ run_date).
KST = timezone(timedelta(hours=9))


@dataclass
class NotionRawSyncReport:
    schema_version: str = SCHEMA_VERSION
    mode: str = ""                      # "dry_run" | "apply"
    source: str = SOURCE_FDA_483
    window_days: int = DEFAULT_WINDOW_DAYS
    new_rows: int = 0                   # Status=New 인 해당 소스 Notion 행
    already_has_body: int = 0           # Notion raw 에 본문이 이미 있음 — 대상 아님
    notion_raw_unreadable: int = 0      # Notion raw 를 읽지 못함 — 손대지 않음
    candidates: int = 0                 # Notion raw 가 본문 없음
    supabase_missing: int = 0           # 같은 document_id 의 Supabase 행 없음
    supabase_also_empty: int = 0        # Supabase 도 본문 없음 — 회수 대기
    synced: int = 0                     # Notion children 교체(dry-run=교체 예정)
    observations_synced: int = 0        # 교체로 생긴 관찰(483)·위반 표제(WL) 수(브리프 카드 규모)
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fetch_supabase_raw(base_url: str, service_key: str, doc_ids: list[str],
                        source: str = SOURCE_FDA_483) -> dict[str, dict[str, Any]]:
    """document_id → raw_json(dict). 해당 소스 행만."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(doc_ids), _DOC_ID_BATCH):
        batch = doc_ids[i:i + _DOC_ID_BATCH]
        rows = fsb._fetch_all_pages(
            base_url, service_key, "raw_signals",
            select="document_id,raw_json", page_size=1000, order="document_id.asc",
            extra_params={"source": f"eq.{source}",
                          "document_id": "in.(" + ",".join(batch) + ")"},
        )
        for r in rows:
            out[str(r.get("document_id") or "")] = _json_object(r.get("raw_json"))
    return out


def _replace_page_raw(token: str, page_id: str, raw: dict[str, Any]) -> None:
    """페이지 children 을 새 raw 블록으로 교체한다.

    기존 블록 id 를 먼저 모으고 → 새 블록을 붙인 뒤 → 기존 블록을 보관(archive)한다. 중간에
    실패해도 페이지가 빈 채로 남지 않게 하려는 순서다(붙이기 전에 지우면 그 사이 raw 가 없다).
    """
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    old_ids: list[str] = []
    start_cursor: str | None = None
    while True:
        req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}" if start_cursor else url
        data = notion_api_request("GET", req_url, token)
        old_ids.extend(str(b["id"]) for b in data.get("results", []) if b.get("id"))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    blocks = build_raw_payload_children(raw)
    for i in range(0, len(blocks), _NOTION_APPEND_BATCH):
        notion_api_request("PATCH", url, token, body={"children": blocks[i:i + _NOTION_APPEND_BATCH]})
        time.sleep(_NOTION_SLEEP_SECONDS)
    for block_id in old_ids:
        notion_api_request("PATCH", f"https://api.notion.com/v1/blocks/{block_id}",
                           token, body={"archived": True})
        time.sleep(_NOTION_SLEEP_SECONDS)


def run(
    *,
    notion_token: str,
    notion_db: str,
    base_url: str,
    service_key: str,
    dry_run: bool,
    run_date: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    source_key: str = "fda483",
    query_rows: Callable[..., list[dict[str, Any]]] | None = None,
    fetch_notion_raw: Callable[[str, str], dict[str, Any] | None] | None = None,
    fetch_supabase_raw: Callable[..., dict[str, dict[str, Any]]] | None = None,
    replace_page_raw: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> tuple[NotionRawSyncReport, int]:
    query_fn = query_rows or notion_query_new_intake_rows
    notion_raw_fn = fetch_notion_raw or fetch_intake_raw_payload
    supa_fn = fetch_supabase_raw or _fetch_supabase_raw
    replace_fn = replace_page_raw or _replace_page_raw
    spec = SOURCE_SPECS[source_key]
    source = spec["source"]
    has_body = spec["has_body"]

    report = NotionRawSyncReport(mode="dry_run" if dry_run else "apply", source=source,
                                 window_days=window_days)
    base = _normalize_base_url(base_url)
    if base is None:
        report.errors.append("SUPABASE_URL must start with https://")
        return report, 2

    try:
        rows = query_fn(notion_token, notion_db, run_date, window_days=window_days,
                        source_names={source})
    except (NotionHandoffError, RuntimeError) as exc:
        report.errors.append(f"notion_query_failed: {exc}")
        return report, 2
    report.new_rows = len(rows)

    targets: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        page_id = str(row.get("page_id") or "")
        doc_id = str(row.get("document_id") or "")
        raw = notion_raw_fn(notion_token, page_id)
        if raw is None:
            report.notion_raw_unreadable += 1
            continue
        if has_body(raw):
            report.already_has_body += 1
            continue
        targets.append((row, doc_id))
    report.candidates = len(targets)
    if not targets:
        log("INFO", f"Notion {source} raw 동기화: New {report.new_rows}건 중 본문 없는 행 0건")
        return report, 0

    try:
        supa = supa_fn(base, service_key, sorted({d for _r, d in targets}), source)
    except RuntimeError as exc:
        report.errors.append(str(exc))
        return report, 2

    for row, doc_id in targets:
        page_id = str(row.get("page_id") or "")
        raw = supa.get(doc_id)
        if raw is None:
            report.supabase_missing += 1
            continue
        if not has_body(raw):
            report.supabase_also_empty += 1
            continue
        observations = raw.get(spec["items_key"]) or []
        if len(report.samples) < 20:
            report.samples.append({"document_id": doc_id, "page_id": page_id,
                                   "observations": len(observations)})
        if not dry_run:
            try:
                replace_fn(notion_token, page_id, raw)
            except (NotionHandoffError, RuntimeError) as exc:
                report.failed += 1
                report.errors.append(f"{doc_id}: {exc}")
                log("WARN", f"Notion raw 교체 실패 {doc_id} page={page_id}: {exc}")
                continue
        report.synced += 1
        report.observations_synced += len(observations)

    head = "[DRY] 교체 예정" if dry_run else "교체 완료"
    log("INFO", f"Notion {source} raw 동기화 {head} {report.synced}건(관찰 {report.observations_synced}건) "
                f"· 대상 {report.candidates}/New {report.new_rows} "
                f"· Supabase 도 빈손 {report.supabase_also_empty} · Supabase 없음 "
                f"{report.supabase_missing} · 실패 {report.failed}")
    code = 1 if report.failed and report.failed >= max(1, report.candidates // 2) else 0
    return report, code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="미소비(New) Notion FDA 483·WL 행의 빈 raw 를 Supabase 의 본문 있는 raw 로 채운다.")
    p.add_argument("--source", choices=sorted(SOURCE_SPECS), default="fda483",
                   help="대상 소스(기본 fda483). wl = FDA Warning Letter(본문 판정 = wl_body_full).")
    p.add_argument("--apply", action="store_true", default=False,
                   help="실제 Notion 쓰기. 기본은 dry-run(조회·판정만).")
    p.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                   help=f"Notion Run Date 조회 창(기본 {DEFAULT_WINDOW_DAYS}일).")
    p.add_argument("--report-path", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)
    creds = _resolve_credentials(args)
    notion_token = os.environ.get("NOTION_TOKEN", "")
    notion_db = os.environ.get("NOTION_DATABASE_ID", "")
    if creds is None or not notion_token or not notion_db:
        print("fda483_notion_raw_sync: SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 와 "
              "NOTION_TOKEN/NOTION_DATABASE_ID 가 필요하다(dry-run 도 조회에 필요).",
              file=sys.stderr)
        return 2
    base_url, service_key = creds
    report, code = run(
        notion_token=notion_token, notion_db=notion_db,
        base_url=base_url, service_key=service_key,
        dry_run=not args.apply, run_date=datetime.now(KST).date(), window_days=args.window_days,
        source_key=args.source,
    )
    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
