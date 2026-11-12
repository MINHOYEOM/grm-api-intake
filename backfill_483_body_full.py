#!/usr/bin/env python3
"""FDA 483 전문(fda483_body_full) 소급 — 정본 raw_signals 에 전문 없이 굳은 최근 483 을 채운다.

배경(2026-11-12 재생 대조, `GRM_재생대조_결과_2026-11-12.md`):
  과거분 백필(collect_fda_backfill --auto)이 새 483 을 전문 없이 먼저 raw_signals 에 넣었고,
  몇 시간 뒤 일일 수집이 가져온 전문 판은 document_id dedup 으로 버려졌다(#1590 이 앞으로를 막음).
  그래서 DB 로 handoff 를 만들면 심층분석 입력이 빠진다(10-26 호 70장·11-02 호 17장·11-09 호 13장).
  11-12 조회: 게시 06-01 이후·범위 안·전문 없음 281행(관찰사항은 있는데 전문만 없음 270).

전문을 얻는 길(행마다, 앞에서 얻으면 멈춘다):
  ① Notion 원문: 같은 Document ID 의 Notion 행 본문(raw JSON 코드 블록)에 `fda483_body_full` 이 있으면
     그 값. 일일 수집이 그 주에 보존한 바로 그 판이다.
  ② PDF 다시 읽기: 일일 수집과 같은 규칙 — `_fetch_fda483_pdf_text` 결과에서 관찰사항이 1개 이상
     파싱될 때만 원문 텍스트 그대로를 전문으로 삼는다(`collect_fda_483` deep 경로와 같음).

쓰기(--apply 일 때만):
  새 판 = **지금 DB raw 그대로 + `fda483_body_full` 키 하나 추가**. 다른 키(관찰사항·실사관 등)는
  덮지 않는다. 원문 갱신 함수 `raw_signal_apply_version`(148, `raw_versioning.apply_version`)으로만 쓴다
  — change_kind='repair', expected = 읽을 때의 raw_sha256. 그사이 바뀌었으면 'conflict' 로 건너뛴다.
  row_json 은 그대로다. findings 는 바뀌지 않는다(관찰사항 불변) — 심층분석 대상만 늘어난다.

대상에서 빼는 것: 이미 전문 있음 · 483 범위 밖(`fda483_text_status=out-of-scope`) · 수집 제외 장부
(`intake_exclusion`)에 있는 문서.

기본은 dry-run: 대상 수와 길별(노션·PDF 필요) 건수만 센다. dry 에서는 PDF 를 받지 않는다(--probe-pdf 로 켬).
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, NOTION_TOKEN, NOTION_DATABASE_ID (값은 로그에 남기지 않는다).
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
import raw_versioning as rv
from grm_common import SOURCE_FDA_483, log

BODY_KEY = "fda483_body_full"
OUT_OF_SCOPE = "out-of-scope"
SELECT_COLUMNS = "raw_signal_id,document_id,published_date,raw_json,row_json,raw_sha256"
CHANGED_BY = "backfill_483_body_full"
_PDF_DELAY_S = 30.0          # fda.gov robots Crawl-Delay
_NOTION_SLEEP_S = 0.34       # Notion 3 req/s


@dataclass
class Report:
    mode: str = "dry-run"
    since: str = ""
    scanned: int = 0
    skipped_out_of_scope: int = 0
    skipped_ledger: int = 0
    targets: int = 0
    via_notion: int = 0
    notion_missing: int = 0          # Notion 에 행이 없거나 전문이 없음 → PDF 필요
    via_pdf: int = 0
    pdf_no_observations: int = 0     # 받았지만 관찰사항이 파싱되지 않음 → 일일 규칙상 전문 아님
    pdf_failed: int = 0
    pdf_not_probed: int = 0          # dry-run 에서 PDF 를 받지 않은 수
    applied: int = 0
    unchanged: int = 0
    conflict: int = 0
    errors: list[str] = field(default_factory=list)
    examples: dict[str, list[str]] = field(default_factory=dict)

    def note(self, key: str, doc_id: str) -> None:
        bucket = self.examples.setdefault(key, [])
        if len(bucket) < 5:
            bucket.append(doc_id)


def _loads(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ── 대상 ─────────────────────────────────────────────────────────────────────

def fetch_candidates(base_url: str, service_key: str, since: str) -> list[dict[str, Any]]:
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals", select=SELECT_COLUMNS, page_size=200,
        order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}", "published_date": f"gte.{since}",
                      "raw_json": f"not.like.*{BODY_KEY}*"})


def fetch_ledger_ids(base_url: str, service_key: str) -> set[str]:
    rows = fsb._fetch_all_pages(
        base_url, service_key, "intake_exclusion", select="document_id", page_size=1000,
        order="document_id.asc", extra_params={"source": f"eq.{SOURCE_FDA_483}"})
    return {str(r.get("document_id") or "") for r in rows} - {""}


def select_targets(rows: list[dict[str, Any]], ledger: set[str], rep: Report) -> list[dict[str, Any]]:
    """전문 없음 ∧ 범위 안 ∧ 제외 장부 밖. (서버 like 필터가 놓친 행도 여기서 다시 거른다.)"""
    out = []
    for row in rows:
        rep.scanned += 1
        raw = _loads(row.get("raw_json"))
        if raw.get(BODY_KEY):
            continue
        doc = str(row.get("document_id") or "")
        if str(raw.get("fda483_text_status") or "") == OUT_OF_SCOPE:
            rep.skipped_out_of_scope += 1
            continue
        if doc in ledger:
            rep.skipped_ledger += 1
            continue
        out.append(row)
    rep.targets = len(out)
    return out


# ── 전문 얻기 ────────────────────────────────────────────────────────────────

def notion_page_index(token: str, db_id: str) -> dict[str, str]:
    """Document ID → Notion page_id (FDA 483 행). 같은 문서가 여럿이면 처음 본 것."""
    from backfill_raw_signals_from_notion import notion_rows_for_source
    index: dict[str, str] = {}
    for snap in notion_rows_for_source(token, db_id, SOURCE_FDA_483):
        index.setdefault(snap["document_id"], snap["page_id"])
    return index


def body_from_notion(token: str, page_id: str) -> str:
    from grm_handoff import fetch_intake_raw_payload
    raw = fetch_intake_raw_payload(token, page_id) or {}
    return str(raw.get(BODY_KEY) or "")


def body_from_pdf(raw: dict[str, Any], doc_id: str) -> tuple[str, str]:
    """일일 수집 deep 경로와 같은 규칙: (전문, 상태). 관찰사항이 파싱될 때만 원문 그대로를 전문으로."""
    import collect_fda_483 as fda483
    media_id = str(raw.get("media_id") or doc_id.removeprefix("fda483-"))
    text, status = fda483._fetch_fda483_pdf_text(fda483._pdf_url(media_id))
    if not text:
        return "", status or "no-text"
    hints = {"establishment_type": str(raw.get("establishment_type") or ""),
             "fei_number": str(raw.get("fei_number") or ""),
             "firm_name": str(raw.get("firm") or "")}
    if not fda483._extract_483_observations_from_text(text, hints):
        return "", "no-observations"
    return text, "ok"


def new_record(row: dict[str, Any], body: str) -> tuple[dict[str, Any], str]:
    raw = _loads(row.get("raw_json"))
    raw[BODY_KEY] = body
    raw_text, sha = rv.canonical_raw(raw)
    return {"raw_json": raw_text, "raw_sha256": sha, "row_json": str(row.get("row_json") or "")}, sha


# ── 실행 ─────────────────────────────────────────────────────────────────────

def run(rows: list[dict[str, Any]], ledger: set[str], *, apply: bool, probe_pdf: bool,
        notion_index: dict[str, str], since: str,
        notion_body: Callable[[str], str],
        pdf_body: Callable[[dict[str, Any], str], tuple[str, str]],
        applier: Callable[..., dict[str, Any]] | None,
        sleeper: Callable[[float], None] = time.sleep) -> Report:
    rep = Report(mode="apply" if apply else "dry-run", since=since)
    for row in select_targets(rows, ledger, rep):
        doc = str(row.get("document_id") or "")
        raw = _loads(row.get("raw_json"))
        body, via = "", ""
        page_id = notion_index.get(doc, "")
        if page_id:
            sleeper(_NOTION_SLEEP_S)
            try:
                body = notion_body(page_id)
            except Exception as exc:  # noqa: BLE001 — 한 행 실패가 전체를 멈추지 않게
                rep.errors.append(f"notion({doc}):{type(exc).__name__}")
            if body:
                via = "notion"
                rep.via_notion += 1
        if not body:
            rep.notion_missing += 1
            rep.note("notion_missing", doc)
            if not (apply or probe_pdf):
                rep.pdf_not_probed += 1
                continue
            sleeper(_PDF_DELAY_S)
            try:
                body, status = pdf_body(raw, doc)
            except Exception as exc:  # noqa: BLE001
                rep.pdf_failed += 1
                rep.errors.append(f"pdf({doc}):{type(exc).__name__}")
                continue
            if not body:
                if status == "no-observations":
                    rep.pdf_no_observations += 1
                    rep.note("pdf_no_observations", doc)
                else:
                    rep.pdf_failed += 1
                    rep.note(f"pdf_failed:{status}", doc)
                continue
            via = "pdf"
            rep.via_pdf += 1
        if not apply:
            continue
        record, _sha = new_record(row, body)
        result = applier(raw_signal_id=str(row["raw_signal_id"]),
                         expected_raw_sha256=str(row.get("raw_sha256") or ""),
                         record=record, change_kind="repair", changed_by=CHANGED_BY,
                         diff_summary={"added": [BODY_KEY], "via": via, "chars": len(body)})
        status = str(result.get("status") or "error")
        if status == "applied":
            rep.applied += 1
        elif status == "unchanged":
            rep.unchanged += 1
        elif status == "conflict":
            rep.conflict += 1
            rep.note("conflict", doc)
        else:
            rep.errors.append(f"apply({doc}):{result.get('error') or status}")
    return rep


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows cp949 콘솔에서 한글·기호 출력 보호
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본 dry-run)")
    ap.add_argument("--probe-pdf", action="store_true",
                    help="dry-run 에서도 Notion 에 없는 행의 PDF 를 받아 판정만 해 본다(쓰지 않음)")
    ap.add_argument("--since", default="2026-06-01", help="게시일 하한(YYYY-MM-DD)")
    ap.add_argument("--limit", type=int, default=0, help="처리할 최대 대상 수(0=전부)")
    ap.add_argument("--report", default="fda483_body_full_report.json")
    args = ap.parse_args(argv)

    base_url = fsa._normalize_base_url(os.environ.get("SUPABASE_URL", "").strip())
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    token = os.environ.get("NOTION_TOKEN", "").strip()
    db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not (base_url and service_key and token and db_id):
        log("ERROR", "SUPABASE_URL(https)·SUPABASE_SERVICE_ROLE_KEY·NOTION_TOKEN·NOTION_DATABASE_ID 가 필요하다")
        return 2

    rows = fetch_candidates(base_url, service_key, args.since)
    if args.limit:
        rows = rows[:args.limit]
    ledger = fetch_ledger_ids(base_url, service_key)
    index = notion_page_index(token, db_id)

    def _apply(**kw: Any) -> dict[str, Any]:
        return rv.apply_version(base_url, service_key, **kw)

    rep = run(rows, ledger, apply=args.apply, probe_pdf=args.probe_pdf, notion_index=index,
              since=args.since, notion_body=lambda pid: body_from_notion(token, pid),
              pdf_body=body_from_pdf, applier=_apply if args.apply else None)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(asdict(rep), fh, ensure_ascii=False, indent=1)
    log("INFO", f"483 전문 소급 {rep.mode}: 대상 {rep.targets} · 노션 {rep.via_notion} · PDF {rep.via_pdf} "
                f"· 노션 없음 {rep.notion_missing}(PDF 미확인 {rep.pdf_not_probed}) "
                f"· 관찰 파싱 안 됨 {rep.pdf_no_observations} · PDF 실패 {rep.pdf_failed} "
                f"· 적용 {rep.applied} · 같음 {rep.unchanged} · 충돌 {rep.conflict} · 오류 {len(rep.errors)}")
    return 1 if rep.errors and args.apply else 0


if __name__ == "__main__":
    sys.exit(main())
