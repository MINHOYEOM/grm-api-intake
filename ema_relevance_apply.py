#!/usr/bin/env python3
"""검토한 EMA 관련도 판정표를 raw_signals에 반영한다. 기본은 dry-run이다."""
from __future__ import annotations

import argparse
import json
import string
import sys
from pathlib import Path
from typing import Any

import findings_supabase_backfill as fsb
import raw_versioning as rv
from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
from grm_common import SOURCE_EMA
from grm_findings import canonical_json

VERDICTS = {"likely": "Likely", "possible": "Possible", "unrelated": "Unrelated"}
REVIEW_KEY = "qa_relevance_review"
SELECT = "source,document_id,raw_signal_id,raw_sha256,raw_json,row_json"
MARKDOWN_ESCAPES = str.maketrans({char: f"&#{ord(char)};" for char in string.punctuation})


def load_decisions(path: Path) -> list[dict[str, str]]:
    decisions = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(decisions, list):
        raise ValueError("판정표는 JSON 배열이어야 합니다")
    for number, entry in enumerate(decisions, 1):
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(key), str) or not entry[key].strip()
            for key in ("doc", "verdict", "evidence")
        ):
            raise ValueError(f"{number}행: doc, verdict, evidence에 문자열이 필요합니다")
        if entry["verdict"] not in VERDICTS:
            raise ValueError(f"{number}행: verdict는 likely, possible, unrelated 중 하나여야 합니다")
    return decisions


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("JSON 객체가 필요합니다")
    return value


def process(
    decisions: list[dict[str, str]], rows: list[dict[str, Any]], *,
    apply: bool, base: str, key: str,
) -> tuple[list[dict[str, Any]], int]:
    ema_rows = [dict(row) for row in rows if row.get("source") == SOURCE_EMA]
    results = []
    for entry in decisions:
        doc = entry["doc"].strip()
        matches = [
            row for row in ema_rows
            if any(
                value == doc or (len(doc) == 10 and value.startswith(doc))
                for value in (row.get("document_id") or "", row.get("raw_signal_id") or "")
            )
        ]
        result: dict[str, Any] = {"doc": doc}
        results.append(result)
        if len(matches) != 1:
            result["status"] = "ambiguous" if matches else "not_found"
            result["matches"] = [row["document_id"] for row in matches]
            return results, 1

        row = matches[0]
        result.update(document_id=row["document_id"], raw_signal_id=row["raw_signal_id"])
        try:
            metadata = _json_object(row["row_json"])
            after = VERDICTS[entry["verdict"]]
            before = metadata.get("qa_relevance")
            result.update(before=before, after=after)
            if before == after:
                result["status"] = "skipped"
                continue
            raw = _json_object(row["raw_json"])
        except (KeyError, ValueError):
            result["status"] = "invalid_stored_json"
            return results, 1

        if not row.get("raw_sha256"):
            result["status"] = "missing_raw_sha256"
            return results, 1
        old_review = raw.get(REVIEW_KEY)
        review = {
            "verdict": after,
            "evidence": entry["evidence"],
            "previous_raw_sha256": row["raw_sha256"],
        }
        result.update(
            evidence_before=old_review.get("evidence") if isinstance(old_review, dict) else None,
            evidence_after=review["evidence"],
        )
        # 동일한 raw_json은 RPC가 건너뛴다. 판정을 읽은 원본 판과 함께 기록한다.
        new_raw = {**raw, REVIEW_KEY: review}
        new_metadata = canonical_json({**metadata, "qa_relevance": after})
        if apply:
            try:
                saved = rv.replace_raw(
                    base, key, row, new_raw, change_kind="repair",
                    changed_by=rv.run_label("ema_relevance_apply"),
                    record_extra={"row_json": new_metadata},
                    diff_summary={"qa_relevance": {"before": before, "after": after}},
                )
            except rv.ApplyVersionError as exc:
                result["status"] = exc.status
                return results, 1
            if saved["status"] != "applied":
                result["status"] = saved["status"]
                return results, 1
            new_sha = saved["new_raw_sha256"]
        else:
            _, new_sha = rv.canonical_raw(new_raw)
        row.update(raw_json=new_raw, row_json=new_metadata, raw_sha256=new_sha)
        result["status"] = "applied" if apply else "would_apply"
    return results, 0


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).translate(MARKDOWN_ESCAPES).replace("\r\n", "\n").replace(
        "\r", "\n"
    ).replace("\n", "<br>")


def print_markdown(results: list[dict[str, Any]], *, apply: bool) -> None:
    print("## EMA 관련도 판정 " + ("반영" if apply else "미리보기"))
    print()
    print("| 문서 ID | 결과 | 판정 전 | 판정 후 | 근거 전 | 근거 후 |")
    print("| --- | --- | --- | --- | --- | --- |")
    for result in results:
        doc = result.get("document_id", result["doc"])
        if result.get("matches"):
            doc += " → " + ", ".join(result["matches"])
        cells = [doc, result["status"]] + [
            result.get(field) for field in ("before", "after", "evidence_before", "evidence_after")
        ]
        print("| " + " | ".join(map(_markdown_cell, cells)) + " |")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("input", type=Path, help="doc, verdict, evidence를 담은 JSON 배열 파일")
    parser.add_argument("--apply", action="store_true", help="사람이 판정표를 승인한 뒤에만 지정")
    parser.add_argument("--markdown", action="store_true", help="판정과 근거 전후를 Markdown 표로 출력")
    parser.set_defaults(supabase_url="", service_role_key="")
    args = parser.parse_args(argv)
    try:
        decisions = load_decisions(args.input)
    except (OSError, ValueError) as exc:
        print(f"판정표 오류: {exc}", file=sys.stderr)
        return 2
    rows = []
    base, key = "", ""
    if decisions:
        creds = resolve_supabase_service_credentials(args)
        base = normalize_supabase_url(creds[0]) if creds else None
        if base is None:
            parser.error("SUPABASE_URL(https)과 SUPABASE_SERVICE_ROLE_KEY가 필요합니다")
        key = creds[1]
        try:
            rows = fsb._fetch_all_pages(
                base, key, "raw_signals", select=SELECT, page_size=500,
                order="raw_signal_id.asc", extra_params={"source": f"eq.{SOURCE_EMA}"},
            )
        except RuntimeError:
            print("EMA raw_signals 조회 실패", file=sys.stderr)
            return 2

    results, status = process(decisions, rows, apply=args.apply, base=base, key=key)
    if args.markdown:
        print_markdown(results, apply=args.apply)
    else:
        print(json.dumps({"dry_run": not args.apply, "rows": results}, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
