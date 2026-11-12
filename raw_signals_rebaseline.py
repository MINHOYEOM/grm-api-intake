#!/usr/bin/env python3
"""[원문 갱신 규칙 R2 2026-11-12] 지문(raw_sha256)이 내용과 어긋난 정본 행의 기준선을 다시 잡는다.

대표 결정(11-12) "다시 매김": 11-12 실측으로 FDA 483 2,217행·MFDS 168행은 수리 도구가 raw_json 만
`json.dumps` 로 PATCH 해서 지문이 내용과 맞지 않는다. 옛 판은 이미 덮어써져 되살릴 수 없다. 그래서
**지금 내용**을 정규화(canonical_json)해 지문을 다시 매기고, 보관표(raw_signal_versions)에
change_kind='baseline' 판(옛 지문만, raw_json 없음)을 남긴다. 내용은 바뀌지 않는다(DB 함수가 강제).

MFDS 실사(gmp-inspection) 행은 참석자 표(mfds_inspection_attendees)가 `source_raw_sha256` 으로 원문과
짝을 맞춘다. 지문만 바꾸면 짝이 끊겨 실사관 화면에서 그 문서가 '표 없음'으로 떨어진다. 그래서 같은
실행에서 문서마다 바로 `sync_attendees`(새 지문으로 원자적 교체)를 부른다.

기본은 dry-run: 대상 행 수(소스별), 보관표에 쌓일 크기 추정, MFDS 참석자 표를 지금 원문으로 다시 읽었을 때
기존 표와 같은지를 보고만 한다. `--apply` 일 때만 쓴다(사람 승인 뒤, migration 148 적용 뒤).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import findings_supabase_backfill as fsb
import mfds_inspection_attendees_store as mas
import raw_versioning as rv
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_common import log

DEFAULT_SOURCES = ("FDA 483", "MFDS")
_PAGE = 200
_RECORD_JSON_BYTES = 400   # 보관표 한 행(옛 지문·열 값 jsonb·메타) 추정 크기 — raw_json 은 싣지 않는다


@dataclass
class Report:
    sources: list[str] = field(default_factory=list)
    scanned: dict[str, int] = field(default_factory=dict)
    mismatched: dict[str, int] = field(default_factory=dict)
    by_kind: dict[str, int] = field(default_factory=dict)
    invalid_json: list[str] = field(default_factory=list)
    already_canonical_text: int = 0          # raw_json 은 이미 정규형인데 지문만 틀린 행
    versions_bytes_estimate: int = 0
    attendees_docs: int = 0                  # MFDS 실사 대상 문서
    attendees_same: int = 0                  # 지금 원문으로 다시 읽어도 기존 표와 같음
    attendees_differ: list[str] = field(default_factory=list)
    attendees_missing: int = 0               # 기존 표가 없는 문서
    applied: int = 0
    unchanged: int = 0
    conflict: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attendees_resynced: int = 0
    apply: bool = False


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_mismatched(rows: list[dict[str, Any]], rep: Report) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rep.scanned[r["source"]] = rep.scanned.get(r["source"], 0) + 1
        text = r.get("raw_json") or ""
        if _sha(text) == r.get("raw_sha256"):
            continue
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            rep.invalid_json.append(r["raw_signal_id"])
            continue
        if not isinstance(raw, dict):
            rep.invalid_json.append(r["raw_signal_id"])
            continue
        canon, new_sha = rv.canonical_raw(raw)
        if canon == text:
            rep.already_canonical_text += 1
        rep.mismatched[r["source"]] = rep.mismatched.get(r["source"], 0) + 1
        out.append(dict(r, _canon=canon, _new_sha=new_sha))
    return out


def _attendee_key(row: dict[str, Any]) -> tuple:
    return (str(row.get("inspector_name_norm") or ""), str(row.get("agency") or ""),
            str(row.get("dept") or ""), bool(row.get("is_observer")))


def compare_attendees(cand: list[dict[str, Any]], existing: dict[str, list[dict[str, Any]]],
                      rep: Report) -> None:
    for r in cand:
        if (r["source"], r.get("source_kind")) != ("MFDS", "gmp-inspection"):
            continue
        rep.attendees_docs += 1
        parsed = mas.parse_record({"document_id": r["document_id"], "raw_json": r["_canon"]})
        new = Counter(_attendee_key(a) for a in parsed.payload()["attendees"])
        old_rows = existing.get(r["raw_signal_id"])
        if not old_rows:
            rep.attendees_missing += 1
            continue
        old = Counter(_attendee_key(a) for a in old_rows if a.get("inspector_name_norm"))
        if new == old:
            rep.attendees_same += 1
        else:
            rep.attendees_differ.append(r["document_id"])


def run(base_url: str, service_key: str, *, sources: list[str], apply: bool, changed_by: str,
        fetch: Callable[..., list[dict[str, Any]]] = fsb._fetch_all_pages,
        apply_fn: Callable[..., dict[str, Any]] = rv.apply_version,
        sync_fn: Callable[[str, str, dict[str, Any]], None] = mas.sync_attendees) -> Report:
    rep = Report(sources=list(sources), apply=apply)
    rows: list[dict[str, Any]] = []
    for source in sources:
        rows += fetch(base_url, service_key, "raw_signals",
                      select="raw_signal_id,source,source_kind,document_id,raw_sha256,raw_json,row_json",
                      page_size=_PAGE, order="raw_signal_id.asc",
                      extra_params={"source": f"eq.{source}"})
    cand = find_mismatched(rows, rep)
    rep.by_kind = dict(Counter(f"{r['source']}|{r.get('source_kind')}" for r in cand))
    rep.versions_bytes_estimate = len(cand) * _RECORD_JSON_BYTES
    mfds_ids = [r["raw_signal_id"] for r in cand
                if (r["source"], r.get("source_kind")) == ("MFDS", "gmp-inspection")]
    existing: dict[str, list[dict[str, Any]]] = {}
    if mfds_ids:
        att = fetch(base_url, service_key, "mfds_inspection_attendees",
                    select="raw_signal_id,inspector_name_norm,agency,dept,is_observer",
                    page_size=1000, order="raw_signal_id.asc",
                    extra_params={"raw_signal_id": "in.(" + ",".join(sorted(mfds_ids)) + ")"})
        for a in att:
            existing.setdefault(a["raw_signal_id"], []).append(a)
    compare_attendees(cand, existing, rep)
    if not apply:
        return rep
    for r in cand:
        res = apply_fn(base_url, service_key, raw_signal_id=r["raw_signal_id"],
                       expected_raw_sha256=r["raw_sha256"],
                       record={"raw_json": r["_canon"], "row_json": r.get("row_json") or "{}",
                               "raw_sha256": r["_new_sha"]},
                       change_kind="baseline", changed_by=changed_by,
                       diff_summary={"reason": "sha_mismatch_rebaseline_20261112"})
        status = res.get("status")
        if status == "applied":
            rep.applied += 1
        elif status == "unchanged":
            rep.unchanged += 1
        elif status == "conflict":
            rep.conflict.append(r["raw_signal_id"])
            continue
        else:
            rep.errors.append(f"{r['raw_signal_id']}:{res.get('error') or status}")
            continue
        if (r["source"], r.get("source_kind")) == ("MFDS", "gmp-inspection"):
            sync_fn(base_url, service_key, {
                "source": r["source"], "source_kind": r.get("source_kind"),
                "document_id": r["document_id"], "raw_sha256": r["_new_sha"],
                "raw_json": r["_canon"]})
            rep.attendees_resynced += 1
    return rep


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--source", action="append", help="소스(기본: FDA 483, MFDS)")
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본 dry-run)")
    args = ap.parse_args(argv)
    base = _normalize_base_url(os.environ.get("SUPABASE_URL", "").strip())
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (base and key):
        log("ERROR", "SUPABASE_URL(https)·SUPABASE_SERVICE_ROLE_KEY 가 필요하다")
        return 2
    changed_by = "raw_signals_rebaseline:" + os.environ.get("GITHUB_RUN_ID", "local")
    try:
        rep = run(base, key, sources=args.source or list(DEFAULT_SOURCES), apply=args.apply,
                  changed_by=changed_by)
    except Exception as e:  # noqa: BLE001 — 키가 새지 않게 예외 종류만
        log("ERROR", f"실패: {type(e).__name__}")
        return 2
    print(json.dumps(asdict(rep), ensure_ascii=False, indent=2))
    log("INFO", f"[{'적용' if rep.apply else 'dry-run'}] 대상 {sum(rep.mismatched.values())} "
                f"{rep.mismatched} · 보관표 추정 {rep.versions_bytes_estimate:,}B · MFDS 참석자 문서 "
                f"{rep.attendees_docs}(같음 {rep.attendees_same} · 다름 {len(rep.attendees_differ)} · "
                f"표 없음 {rep.attendees_missing}) · 적용 {rep.applied} · 충돌 {len(rep.conflict)} · "
                f"오류 {len(rep.errors)}")
    return 1 if (rep.errors or rep.conflict) else 0


if __name__ == "__main__":
    sys.exit(main())
