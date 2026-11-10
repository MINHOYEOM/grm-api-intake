#!/usr/bin/env python3
"""[483 구체 내용 · 2026-11-10] translations/detail_outbox/*.json → findings.detail_text·detail_text_ko PATCH.

지식 그래프 방향 전환 1단계. 483 관찰의 상세 원문(raw_signals fda_483_observations[].detail)과
구독 경로로 만든 국문을 findings 의 두 칸(migration 140)에 **한 PATCH 로 함께** 쓴다.

왜 기존 outbox(translations/outbox · findings_translate_apply_service.py)를 쓰지 않나:
  그 경로는 finding_text_ko 를 쓰는 일일 번역·WL 복원이 함께 쓰는 길이다. 상세는 칸이 다르고
  양이 많아(관찰 1만여 건) 한 길에 섞으면 그쪽 실행 시간(10분 제한)과 검증 규칙을 흔든다.

항목 모양(배열의 각 원소):
  {"finding_id", "finding_text_md5", "detail_text", "detail_text_ko"}
  - finding_text_md5 = 배치를 만들 때의 findings.finding_text md5. 라이브 표제가 바뀌었으면
    (재추출·복원) 그 관찰과의 대응이 깨졌을 수 있으므로 쓰지 않는다(matched_zero).
  - detail_text·detail_text_ko 는 둘 다 비어 있지 않아야 한다(반쪽 행을 만들지 않는다).

멱등: 같은 배치를 다시 적용하면 같은 값을 다시 쓴다. git 쓰기 없음.
exit code: 자격증명/URL 오류 또는 항목 오류(errored>0) → 1, 그 외 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from findings_translate_apply_service import _send_findings_request
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials

DEFAULT_OUTBOX_DIR = "translations/detail_outbox"
ITEM_KEYS = ("finding_id", "finding_text_md5", "detail_text", "detail_text_ko")
_MD5_RE = re.compile(r"[0-9a-f]{32}")
_HANGUL_RE = re.compile(r"[가-힣]")


def md5_text(text: str) -> str:
    """Postgres md5(text) 와 같은 값(UTF-8 바이트의 md5 hex)."""
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def validate_item(item: Any) -> str:
    """형식 오류 사유(없으면 ""). 순수 함수 — 요청 전에 거른다."""
    if not isinstance(item, dict) or not all(k in item for k in ITEM_KEYS):
        return "missing_keys"
    if not str(item["finding_id"]).strip():
        return "empty_finding_id"
    if not _MD5_RE.fullmatch(str(item["finding_text_md5"])):
        return "invalid_md5"
    if not str(item["detail_text"]).strip() or not str(item["detail_text_ko"]).strip():
        return "empty_detail"
    if not _HANGUL_RE.search(str(item["detail_text_ko"])):
        return "ko_has_no_hangul"
    return ""


def load_items(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}")
            continue
        if not isinstance(data, list):
            errors.append(f"{path.name}: root_not_a_list")
            continue
        items.extend(x for x in data if isinstance(x, dict))
    return items, errors


Sender = Callable[..., tuple[int, list[dict[str, Any]] | None, str]]


def apply_item(base: str, key: str, item: dict[str, Any], *, send: Sender = _send_findings_request
               ) -> tuple[str, str]:
    """(outcome, detail) — outcome ∈ succeeded·matched_zero·errored."""
    fid = str(item["finding_id"])
    _s, rows, err = send("get", base, key, params={"finding_id": f"eq.{fid}", "select": "finding_text"})
    if err:
        return "errored", f"{fid}: GET {err}"
    live = [r for r in (rows or []) if md5_text(str(r.get("finding_text") or "")) == item["finding_text_md5"]]
    if not live:
        return "matched_zero", ""
    body = {"detail_text": str(item["detail_text"]), "detail_text_ko": str(item["detail_text_ko"])}
    _s, prows, perr = send("patch", base, key, params={"finding_id": f"eq.{fid}"}, json_body=body)
    if perr:
        return "errored", f"{fid}: PATCH {perr}"
    n = len(prows or [])
    if n == 1:
        return "succeeded", ""
    if n == 0:
        return "matched_zero", ""
    return "errored", f"{fid}: PATCH matched {n} rows"


def run(paths: list[Path], base: str, key: str, *, dry_run: bool = False,
        send: Sender = _send_findings_request) -> dict[str, Any]:
    items, errors = load_items(paths)
    report: dict[str, Any] = {"mode": "dry_run" if dry_run else "apply", "files": [p.name for p in paths],
                              "items_total": len(items), "invalid": 0, "succeeded": 0,
                              "matched_zero": 0, "errored": 0, "errors": errors}
    for item in items:
        why = validate_item(item)
        if why:
            report["invalid"] += 1
            report["errors"].append(f"{item.get('finding_id', '?')}: {why}")
            continue
        if dry_run:
            continue
        outcome, detail = apply_item(base, key, item, send=send)
        report[outcome] += 1
        if detail:
            report["errors"].append(detail)
    report["errors"] = report["errors"][:200]
    return report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="483 상세 원문·국문 → findings.detail_text·detail_text_ko PATCH.")
    p.add_argument("--outbox-dir", default=DEFAULT_OUTBOX_DIR)
    p.add_argument("--files", nargs="*", default=None,
                   help="이 파일들만(워크플로가 이번 push 로 바뀐 배치만 넘긴다). 없으면 폴더 전체.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--output", help="리포트 JSON 경로")
    p.add_argument("--supabase-url")
    p.add_argument("--service-role-key")
    args = p.parse_args(argv)
    if args.files is not None:
        paths = [Path(f) for f in args.files if f.endswith(".json") and Path(f).is_file()]
    else:
        paths = sorted(Path(args.outbox_dir).glob("*.json"))
    creds = _resolve_credentials(args)
    if creds is None:
        print("findings_detail_apply: Supabase 자격 증명 필요", file=sys.stderr)
        return 1
    base = _normalize_base_url(creds[0])
    if base is None:
        print("findings_detail_apply: SUPABASE_URL must start with https://", file=sys.stderr)
        return 1
    report = run(paths, base, creds[1], dry_run=args.dry_run)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    return 1 if (report["errored"] or report["invalid"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
