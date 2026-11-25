#!/usr/bin/env python3
"""지정한 MHRA 경보의 본문을 gov.uk에서 다시 조회하고 저장 전후를 비교한다.

기본 dry-run은 읽기만 한다. 담당자 승인 후 --apply를 지정하면 원문 버전 이력을 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from functools import partial
from typing import Any

import findings_supabase_backfill as fsb
import raw_versioning as rv
from backfill_blank_excerpts import MHRA_ALERT_PREFIX
from collect_intake import _read_mhra_alert_body
from grm_cli import normalize_supabase_url
from grm_common import SOURCE_MHRA

_BODY_FIELD = "mhra_alert_body"
_RAW_ID = re.compile(r"(?:[0-9a-f]{12}|rawsig-[0-9a-f]{24})")


def _parse_id(value: str) -> str:
    if not _RAW_ID.fullmatch(value):
        raise argparse.ArgumentTypeError("12자리 문서 ID 또는 전체 rawsig- ID가 필요합니다")
    return value


def _fetch_rows(base: str, key: str, ids: list[str]) -> list[dict[str, Any]]:
    filters = [f"{_id_column(rid)}.eq.{rid}" for rid in ids]
    return fsb._fetch_all_pages(
        base, key, "raw_signals",
        select="raw_signal_id,document_id,source,official_url,raw_sha256,raw_json,row_json",
        page_size=100, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_MHRA}", "or": "(" + ",".join(filters) + ")"},
    )


def _id_column(rid: str) -> str:
    return "raw_signal_id" if rid.startswith("rawsig-") else "document_id"


def _compare(
    row: dict[str, Any], fetch_body: Callable[[str], tuple[str, str]],
    write: Callable[..., dict[str, Any]] | None,
) -> tuple[str | None, str | None, str, bool]:
    try:
        raw = json.loads(row["raw_json"])
    except (KeyError, TypeError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        return None, None, "저장된 raw_json 형식 오류", False

    before = raw.get(_BODY_FIELD)
    if before is None:
        before = ""
    if not isinstance(before, str):
        return None, None, "저장된 본문 형식 오류", False
    if row.get("source") != SOURCE_MHRA:
        return before, None, "MHRA 원문 행이 아님", False

    url = next((value.strip() for value in (row.get("official_url"), raw.get("link"))
                if isinstance(value, str) and value.strip().startswith(MHRA_ALERT_PREFIX)
                and value.strip() != MHRA_ALERT_PREFIX), "")
    if not url:
        return before, None, "gov.uk MHRA 경보 URL 없음", False

    body, reason = fetch_body(url)
    if reason or not body.strip():
        return before, None, reason or "본문 비어 있음", False
    if body == before:
        return before, body, "변경 없음", True
    if write is None:
        return before, body, "변경 예정", True

    try:
        result = write(row, {**raw, _BODY_FIELD: body}, change_kind="repair",
                       changed_by=rv.run_label("backfill_mhra_alert_body"),
                       diff_summary={"changed_fields": [_BODY_FIELD]})
    except rv.ApplyVersionError as exc:
        return before, body, f"저장 실패 ({exc.status})", False
    return before, body, "저장 완료" if result["status"] == "applied" else "변경 없음", True


def _print_row(rid: str, before: str | None, after: str | None, status: str) -> None:
    cells = [rid]
    for body in (before, after):
        if body is None:
            cells.extend(["—", "—"])
        else:
            preview = json.dumps(body[:200], ensure_ascii=False).replace("|", r"\|")
            cells.extend([str(len(body)), preview])
    print("| " + " | ".join([*cells, status]) + " |")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--ids", nargs="+", required=True, type=_parse_id, metavar="ID",
                        help="12자리 문서 ID 또는 전체 rawsig- ID")
    parser.add_argument("--apply", action="store_true", help="담당자 승인 후 원문 변경 저장")
    args = parser.parse_args(argv)
    base = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        parser.error("SUPABASE_URL(https)과 SUPABASE_SERVICE_ROLE_KEY가 필요합니다")

    ids = list(dict.fromkeys(args.ids))
    try:
        rows = _fetch_rows(base, key, ids)
    except RuntimeError:
        print("raw_signals 조회 실패", file=sys.stderr)
        return 2

    write = partial(rv.replace_raw, base, key) if args.apply else None
    print("모드: apply" if args.apply else "모드: dry-run")
    print("| 입력 ID | 이전 길이 | 이전 앞 200자 | 재조회 길이 | 재조회 앞 200자 | 결과 |")
    print("| --- | ---: | --- | ---: | --- | --- |")
    compared = {}
    failed = False
    for rid in ids:
        matches = [row for row in rows if row.get(_id_column(rid)) == rid]
        if len(matches) != 1:
            status = "일치하는 행 없음" if not matches else "ID가 여러 행과 일치"
            _print_row(rid, None, None, status)
            failed = True
            continue
        row = matches[0]
        full_id = row["raw_signal_id"]
        if full_id not in compared:
            compared[full_id] = _compare(row, _read_mhra_alert_body, write)
        before, after, status, ok = compared[full_id]
        _print_row(rid, before, after, status)
        failed |= not ok
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
