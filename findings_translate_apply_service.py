#!/usr/bin/env python3
"""FIND-1 M9b -- unattended CI apply service for queued translation batches.

This script is the second half of the FIND-1 translation automation pipeline
(see findings_translate.py's module docstring for the first half):

  M9c (not this file): a weekly, subscription-usage Claude Code session
      exports untranslated findings, translates them, validates the result
      with findings_translate.py --apply --source supabase --outbox-output,
      and commits the resulting outbox batch JSON under translations/outbox/
      via a PR that gets auto-merged into main.

  M9b (this file): a GitHub Actions workflow triggered by that merge runs
      this script, which is pure, deterministic Python -- no LLM, no
      judgment calls -- to PATCH each outbox item onto the live
      public.findings table using the Supabase service-role key (the same
      RLS-bypass mechanism the nightly M4 ingestion already uses safely).

This module performs no git operations whatsoever -- it only reads files
under --outbox-dir and issues HTTPS GET/PATCH requests. It never moves,
renames, or deletes outbox files, and never stages, commits, or pushes
anything. Each item is applied read-before-write, keyed on finding_id (the
findings primary key, web/migrations/002_findings.sql): a short GET fetches
the live finding_text, it is compared byte-for-byte against the outbox item
in-process, and only on a match is a PATCH issued -- also keyed on finding_id
only. This keeps the apply idempotent (re-running re-PATCHes an already-
applied row to the same values, a harmless no-op) and TOCTOU-safe (if the
source finding_text changed since the batch was built the comparison fails
and nothing is written -- counted as matched_zero, not an error), exactly as
the previous single-PATCH design did.

The reason the finding_text equality is a client-side comparison rather than
a query-string filter (as it was before 2026-07-23): finding_text can be up
to 30,000 characters (FDA warning-letter full text). Carrying it in the PATCH
URL as `finding_text=eq.<30k chars>` produced a request URI of ~35-45 KB,
past the Supabase edge's ~32 KB URL limit, which returned a bare-text HTTP
400 "Bad Request" before the request ever reached PostgREST. Under the M13b
policy that made the run go red -- and because the request was rejected the
Korean text was never written, so the four longest findings in a batch stayed
untranslated and re-failed on every run. Keying every URL on finding_id (a
~32-char PK) removes finding_text from the wire path entirely.

Leaving already-applied outbox files in place is *correct* but not *free*:
re-running re-issues the GET/PATCH pair per item. The cost is wall-clock: the
CI workflow has a 10-minute timeout, and on 2026-07-13 dozens of accumulated
already-applied batches pushed every run past it, starving the newest batch.
Two mitigations exist: (1) this script processes outbox files newest-first
(see _load_outbox_files), and (2) the M9c translation session archives
already-applied batches from translations/outbox/ to translations/applied/
in the same PR that adds a new batch, so the outbox normally holds only the
batch(es) not yet confirmed applied.

The service-role key is never included in any log line, exception message,
or report field -- only exception type names and HTTP status codes are
surfaced, mirroring findings_supabase_append.py's convention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


# [WL 절단 복원 · 2026-10-31] outbox 항목 종류. `kind` 가 없으면 기존 번역 항목 — 동작 불변.
# "restore" 는 finding_text(새 영문)·finding_text_ko·translation_method 를 **한 PATCH** 로 바꾼다.
# 라이브 finding_text 의 md5 가 finding_text_old_md5 와 같을 때만 쓴다(재실행 = matched_zero).
# 국문이 비는 순간은 없다: 공개 RLS 게이트(006)가 finding_text_ko='' 행을 즉시 숨기므로 영문만
# 먼저 바꾸는 반쪽 상태를 만들지 않는다. category_code 등 분류 열은 건드리지 않는다.
RESTORE_KIND = "restore"
_MD5_RE = re.compile(r"[0-9a-f]{32}")


def _md5_text(text: str) -> str:
    """Postgres md5(finding_text) 와 같은 값(UTF-8 바이트의 md5 hex)."""
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


DEFAULT_OUTBOX_DIR = "translations/outbox"
DEFAULT_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only

# GET 투영: 원문 대조용 finding_text + 같은 값이면 PATCH 를 건너뛰기 위한 두 칸(2026-11-20 DB 용량 3단계).
_GET_SELECT = "finding_text,finding_text_ko,translation_method"

_REQUIRED_ITEM_KEYS = ("finding_id", "finding_text", "finding_text_ko", "translation_method")


def _load_outbox_files(outbox_dir: str | Path) -> tuple[list[Path], list[str]]:
    """Return (newest-first file paths, errors). Missing directory is not an error.

    Outbox file names are date-prefixed (e.g. 2026-07-15-batch.json), so reverse
    lexicographic order processes the newest batch first. This is a starvation
    defense: the workflow has a hard 10-minute timeout, and if applied batches
    ever accumulate in the outbox again (2026-07-13 incident: 42 stale files
    re-PATCHed on every run pushed the newest batch past the timeout, so merged
    translations never reached Supabase), the newest — i.e. the only not-yet-
    applied — batch still lands before the deadline.
    """
    directory = Path(outbox_dir)
    if not directory.is_dir():
        return [], []
    paths = sorted((p for p in directory.glob("*.json") if p.is_file()), reverse=True)
    return paths, []


def _parse_outbox_file(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Parse one outbox file into a list of item dicts. Returns (items, error)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], type(exc).__name__

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], "invalid_json"

    if not isinstance(data, list):
        return [], "root_not_a_list"

    items: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not all(key in entry for key in _REQUIRED_ITEM_KEYS):
            continue
        items.append(entry)
    return items, ""


def _send_findings_request(
    verb: str,
    base_url: str,
    service_key: str,
    *,
    params: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """Issue one GET or PATCH to /rest/v1/findings and return
    (status_code, rows_or_None, error_summary).

    error_summary is "" on 2xx. On failure it is "timeout", an exception type
    name, or "http_{status}" -- never exception text, so the service-role key
    embedded in a lower-level transport error can never leak through it. On
    2xx, rows is the decoded JSON array (or [] if the body is absent/not a
    list).

    Retries once (total 2 attempts) for 5xx responses or a request timeout.
    Any other exception or 4xx status fails immediately without retry. `verb`
    is resolved to requests.get/requests.patch at call time so test doubles
    patched onto the module's `requests` are honoured.
    """
    request_fn = requests.get if verb == "get" else requests.patch
    url = f"{base_url}/rest/v1/findings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = request_fn(
                url, params=params, json=json_body, headers=headers, timeout=timeout
            )
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return resp.status_code, [], ""
        return resp.status_code, (data if isinstance(data, list) else []), ""

    return 0, None, "retry_exhausted"  # unreachable safety net


def _apply_one_finding(
    base_url: str,
    service_key: str,
    item: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    live_rows: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Read-before-write apply of one outbox item, keyed on finding_id only.

    `live_rows`(11-26): 묶음 조회(`_prefetch_live_rows`)로 이미 받은 이 finding_id 의 라이브 행(0~1개).
    주면 행별 GET 을 보내지 않는다 — 판정(원문 대조·같은 값 건너뛰기·PATCH)은 그대로다.

    Returns (outcome, error_detail) where outcome is one of "succeeded",
    "matched_zero", "unchanged" (live row already holds these values -- no
    PATCH sent, 2026-11-20), or "errored" -- the same three-way accounting the previous
    single-PATCH design produced. No request URL ever carries finding_text
    (see the module docstring: it can be 30k chars and blew past the ~32 KB
    edge URL limit). Instead a short GET fetches the live finding_text by
    primary key, it is compared in-process, and only a still-matching row is
    PATCHed by primary key. error_detail carries no service-role key -- only
    finding_id, HTTP codes, exception type names, and row counts.
    """
    finding_id = str(item.get("finding_id") or "")
    finding_text = str(item.get("finding_text") or "")
    finding_text_ko = str(item.get("finding_text_ko") or "")
    kind = str(item.get("kind") or "")
    old_md5 = str(item.get("finding_text_old_md5") or "")

    # 0) restore 항목의 형식 가드 — 어긋나면 요청을 보내지 않는다(쓰기 0).
    if kind == RESTORE_KIND:
        if not _MD5_RE.fullmatch(old_md5):
            return "errored", f"finding_id={finding_id} restore item has invalid finding_text_old_md5"
        if not finding_text.strip() or not finding_text_ko.strip():
            return "errored", f"finding_id={finding_id} restore item has empty finding_text/finding_text_ko"
        if not str(item.get("translation_method") or ""):
            return "errored", f"finding_id={finding_id} restore item has empty translation_method"
    elif kind:
        return "errored", f"finding_id={finding_id} unknown item kind"

    # 1) Fetch the live row by primary key (short URL). finding_id is the
    #    findings PK, so this returns 0 or 1 rows.
    if live_rows is not None:
        rows = live_rows
    else:
        _status, rows, err = _send_findings_request(
            "get",
            base_url,
            service_key,
            params={"finding_id": f"eq.{finding_id}", "select": _GET_SELECT},
            timeout=timeout,
        )
        if err:
            return "errored", f"finding_id={finding_id} GET failed ({err})"

    # Only a row whose live finding_text still byte-matches the outbox item is
    # eligible -- this reproduces the original finding_id+finding_text eq
    # filter as an in-process comparison instead of a URL predicate.
    # restore 는 옛(잘린) 본문 md5 로 대조한다 — 이미 복원된 행(라이브 = 새 본문)은 0건.
    if kind == RESTORE_KIND:
        live_matches = [
            r for r in (rows or []) if _md5_text(str(r.get("finding_text") or "")) == old_md5
        ]
    else:
        live_matches = [
            r for r in (rows or []) if str(r.get("finding_text") or "") == finding_text
        ]
    if not live_matches:
        # finding_id absent, or the source finding_text changed since the batch
        # was built -- a TOCTOU-safe no-op, counted as matched_zero exactly as
        # the previous eq-filter-matches-zero case.
        return "matched_zero", ""
    if len(live_matches) > 1:
        # Impossible under the finding_id primary key, but keep the anomaly
        # guard rather than silently over-writing.
        return (
            "errored",
            f"finding_id={finding_id} live rows matched {len(live_matches)} (expected 0 or 1)",
        )

    # 2) Write by primary key only (short URL). return=representation lets us
    #    confirm exactly one row was updated.
    body = {
        "finding_text_ko": finding_text_ko,
        "translation_method": str(item.get("translation_method") or ""),
    }
    if kind == RESTORE_KIND:
        body["finding_text"] = finding_text  # 영문·국문 동시 교체(한 PATCH = 한 UPDATE)

    # [DB 용량 3단계 · 2026-11-20] 라이브 행이 이미 이 값이면 PATCH 를 보내지 않는다. outbox 는 매 실행
    # 모든 배치를 다시 읽으므로(지금 5개 333항목 중 새 항목은 ~50) 나머지는 같은 값 재쓰기였다 — PostgreSQL 은
    # 같은 값 UPDATE 도 새 행 판을 써서 findings 에 빈자리가 쌓인다. GET 응답에 칸이 없으면(옛 모양) 종전대로 쓴다.
    live = live_matches[0]
    if all(k in live for k in body) and all(str(live.get(k) or "") == v for k, v in body.items()):
        return "unchanged", ""
    _status, prows, perr = _send_findings_request(
        "patch",
        base_url,
        service_key,
        # return=representation 은 행 전체(최대 3만 자 원문 포함)를 돌려준다 — 행 수 확인에는 id 면 된다.
        params={"finding_id": f"eq.{finding_id}", "select": "finding_id"},
        json_body=body,
        timeout=timeout,
    )
    if perr:
        return "errored", f"finding_id={finding_id} PATCH failed ({perr})"

    matched = len(prows or [])
    if matched == 0:
        # Row changed/removed between the GET and the PATCH -- a TOCTOU-safe
        # no-op, not an error.
        return "matched_zero", ""
    if matched == 1:
        return "succeeded", ""
    return (
        "errored",
        f"finding_id={finding_id} PATCH matched {matched} rows (expected 0 or 1)",
    )


# ★묶음 조회·시간 예산(2026-11-26). #1996 머지 뒤 M9b(run 45788934527 1차)가 timeout-minutes 10 에 걸려
#   cancelled — outbox 15개 파일 1,624항목을 **항목마다 GET 한 번씩** 다시 읽는다(대부분 이미 반영돼
#   PATCH 는 0인데도). DB CPU 가 흔들리는 날(같은 날 다른 RPC 1~19s)엔 그 왕복만으로 10분을 넘고,
#   잘린 실행은 보고서도 못 남기고 회색(cancelled)으로 끝나 반영이 덜 됐는지 알 수 없었다.
#   · 묶음 조회: 파일마다 finding_id 를 N 개씩 `in.(…)` 로 한 번에 받는다(1,624 GET → ~35 GET).
#   · 시간 예산: 넘기 전에 스스로 멈추고 incomplete·items_remaining 을 적고 **적색(exit 1)** 으로 끝낸다.
#     반영은 멱등이라(같은 값은 PATCH 안 함) 다음 실행이 남은 것부터 자연히 이어 받는다.
DEFAULT_LOOKUP_BATCH = 50


def _postgrest_in(values: list[str]) -> str:
    """PostgREST `in.(…)` — 값마다 큰따옴표로 감싼다(쉼표·괄호가 섞여도 깨지지 않게)."""
    quoted = ['"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values]
    return "in.(" + ",".join(quoted) + ")"


def _prefetch_live_rows(
    base_url: str, service_key: str, finding_ids: list[str], *, batch_size: int,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, list[dict[str, Any]]] | None, int, str]:
    """finding_id → 라이브 행 목록(없으면 빈 목록). (맵 또는 None, 보낸 GET 수, 오류)."""
    ids = list(dict.fromkeys(i for i in finding_ids if i))
    live: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    sent = 0
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        _status, rows, err = _send_findings_request(
            "get", base_url, service_key,
            params={"finding_id": _postgrest_in(chunk), "select": "finding_id," + _GET_SELECT},
            timeout=timeout,
        )
        sent += 1
        if err:
            return None, sent, f"batch GET failed ({err})"
        for r in rows or []:
            fid = str(r.get("finding_id") or "")
            if fid in live:
                live[fid].append(r)
    return live, sent, ""


def apply_outbox(
    outbox_dir: str | Path,
    base_url: str,
    service_key: str,
    *,
    dry_run: bool = False,
    batch_size: int = 0,
    time_budget_seconds: float = 0,
    clock: Any = None,
) -> dict[str, Any]:
    """Read every *.json batch under outbox_dir and PATCH each item to Supabase.

    batch_size>0: 파일마다 라이브 행을 묶음으로 미리 받는다(행별 GET 없음). 0 이면 종전 행별 GET.
    time_budget_seconds>0: 예산을 넘기면 남은 항목을 처리하지 않고 incomplete=True 로 끝낸다(errors 에도
    한 줄 — CLI 는 exit 1).

    Pure orchestration: no git operations, no file mutation of outbox_dir
    (files are read-only inputs and are never moved/deleted/rewritten). In
    dry_run mode files are still read and counted but no PATCH request is
    issued.
    """
    base = _normalize_base_url(base_url)
    report: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "apply",
        "files_scanned": 0,
        "items_total": 0,
        "items_succeeded": 0,
        "items_matched_zero": 0,
        "items_unchanged": 0,   # 2026-11-20: 라이브가 이미 같은 값 — PATCH 안 보냄(쓰기 0)
        "items_errored": 0,
        "lookup_requests": 0,
        "incomplete": False,
        "items_remaining": 0,
        "errors": [],
    }
    clock = clock or time.monotonic
    started = clock()
    if base is None:
        report["errors"].append("SUPABASE_URL must start with https://")
        return report

    paths, _load_errors = _load_outbox_files(outbox_dir)
    report["files_scanned"] = len(paths)

    for path in paths:
        items, parse_err = _parse_outbox_file(path)
        if parse_err:
            report["items_errored"] += 1
            report["errors"].append(f"{path.name}: failed to parse outbox file ({parse_err})")
            continue
        if report["incomplete"]:
            report["items_remaining"] += len(items)
            continue

        live: dict[str, list[dict[str, Any]]] | None = None
        if batch_size > 0 and not dry_run and items:
            live, sent, perr = _prefetch_live_rows(
                base, service_key, [str(i.get("finding_id") or "") for i in items], batch_size=batch_size)
            report["lookup_requests"] += sent
            if perr:
                live = None                 # 묶음 조회 실패 — 이 파일은 종전 행별 GET 으로
                report["errors"].append(f"{path.name}: {perr} — fell back to per-item GET")

        for idx, item in enumerate(items):
            if time_budget_seconds > 0 and clock() - started > time_budget_seconds:
                report["incomplete"] = True
                report["items_remaining"] += len(items) - idx
                break
            report["items_total"] += 1

            if dry_run:
                report["items_succeeded"] += 1
                continue

            # Read-before-write apply keyed on finding_id only (see
            # _apply_one_finding). The three outcomes map 1:1 onto the report
            # counters the previous single-PATCH design produced:
            #   - matched_zero: already-applied no-op, or the live
            #     finding_text no longer matches the outbox item (source row
            #     changed/removed since the batch was built). Both are
            #     TOCTOU-safe -- the outbox file is left in place and the next
            #     scheduled run naturally revisits it.
            #   - errored: a transport/HTTP failure or an anomalous row count.
            #   - succeeded: exactly one row updated.
            rows = None if live is None else live.get(str(item.get("finding_id") or ""), [])
            outcome, detail = _apply_one_finding(base, service_key, item, live_rows=rows)
            if outcome == "succeeded":
                report["items_succeeded"] += 1
            elif outcome == "matched_zero":
                report["items_matched_zero"] += 1
            elif outcome == "unchanged":
                report["items_unchanged"] += 1
            else:
                report["items_errored"] += 1
                report["errors"].append(f"{path.name}: {detail}")

    if report["incomplete"]:
        report["errors"].append(
            f"time budget {time_budget_seconds:.0f}s reached — {report['items_remaining']} items not applied "
            "this run (idempotent: the next run resumes them)")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M9b -- unattended CI apply service for queued translation "
        "outbox batches (no git operations; PATCH only)"
    )
    parser.add_argument(
        "--outbox-dir",
        default=DEFAULT_OUTBOX_DIR,
        help=f"Directory of translation outbox batch JSON files (default: {DEFAULT_OUTBOX_DIR})",
    )
    parser.add_argument(
        "--supabase-url",
        help="Supabase project URL (falls back to $SUPABASE_URL)",
    )
    parser.add_argument(
        "--service-role-key",
        help="Supabase service-role key (falls back to $SUPABASE_SERVICE_ROLE_KEY)",
    )
    parser.add_argument("--output", help="Report JSON output path (default: stdout)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and count outbox items without issuing any PATCH requests",
    )
    parser.add_argument(
        "--batch-size", type=int, default=0,
        help="Look up live rows N finding_ids per GET (0 = legacy one GET per item). "
             f"CI uses {DEFAULT_LOOKUP_BATCH}.",
    )
    parser.add_argument(
        "--time-budget-seconds", type=float, default=0,
        help="Stop before this many seconds, report incomplete and exit 1 (0 = no budget).",
    )
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_translate_apply_service: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = apply_outbox(args.outbox_dir, base_url, service_key, dry_run=args.dry_run,
                          batch_size=args.batch_size, time_budget_seconds=args.time_budget_seconds)
    # Report JSON is always printed/written before we decide the exit code --
    # even a red (exit 1) run leaves the report behind for the workflow's
    # step summary to surface.
    _write_report(args.output, report)

    # FIND-1 M13b: PATCH is still idempotent and outbox files are still never
    # removed, so a future scheduled run naturally retries anything that
    # failed this time -- that retry design is unchanged. What changes here
    # is *visibility*: retryability and CI gating are orthogonal, so errors
    # (items_errored, e.g. failed PATCHes/parses/anomalous row counts, or any
    # other entry landing in report["errors"], such as a malformed
    # SUPABASE_URL) now surface as exit 1 instead of a silently-green run
    # that only a human reading the report JSON would ever notice. An empty
    # outbox (0 items processed) is a normal steady state, not an error, and
    # keeps exiting 0.
    if report["errors"]:
        return 1
    return 0


__all__ = ["apply_outbox", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
