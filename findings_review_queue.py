#!/usr/bin/env python3
"""findings 검토 대기(needs_review) 큐 — 매일 나이 측정 + 사람 판정 적용.

## 왜 있나 (2026-11-08)

지적사항 검색 화면의 "검토 필요 18" 이 몇 주째 그대로였다. 자동승격(`findings_review_
promote_service`, 매일 05:30 KST)은 매일 초록으로 돌았지만 규칙에 안 맞는 행은 **조용히
유지**한다 — 설계상 맞는 동작이다("애매하면 유지"). 문제는 유지된 행을 아무도 다시 보지
않았다는 것. 기존 백로그 감시(`findings_backlog_monitor`)는 건수 300 초과만 보므로 18건은
영원히 초록이었다. 실제로 가장 오래된 행은 큐에 111일 있었다(non_pharma, 07-20 백필분).

그래서 건수가 아니라 **나이**를 잰다. 자동승격이 하루 한 번 돌기 때문에 자동으로 풀릴 행은
하루 안에 빠진다. N일(기본 3일 — 금요일 유입분이 주말을 넘겨도 월요일 아침엔 경보) 넘게 남은
행은 "규칙이 판정하지 못한 행"이고, 사람이 정하거나 규칙을 고쳐야 한다.

## 하위 명령

  measure (기본)  : 읽기 전용. needs_review 전 행(모든 소스·scope)의 건수, 소스·scope 별
                    분포, 가장 오래된 행의 나이(일), N일 초과 행 목록을 report 로 낸다.
                    N일 초과가 1건이라도 있으면 exit 1 → 워크플로가 이슈(findings-review-queue)
                    를 열거나 갱신한다. 0건이면 exit 0 → 해소 댓글 후 닫는다.
  decide          : 사람 판정 파일(JSON: [{"finding_id","decision","reason"}])을 적용한다.
                    **기본 dry-run**, --apply 일 때만 PATCH. review_status 만 바꾸고 가드
                    review_status=eq.needs_review 로 멱등·경합 안전(이미 바뀐 행은 0행 매칭).

나이 기준은 `ingested_at`(큐에 들어온 시각)이다. 문서 게시일(published_date)은 옛 서한
백필에서 수년 전일 수 있어 "얼마나 기다렸나"를 재지 못한다.

service-role 키는 어떤 로그·예외·report 필드에도 넣지 않는다(예외 타입명·HTTP status 만).
finding_text 는 읽지 않는다(measure) — 행 id·소스·scope·시각만 본다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import requests

import findings_supabase_backfill as fsb
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


DEFAULT_MAX_AGE_DAYS = 3
_PAGE_SIZE = 1000
_MAX_LISTED = 30          # 이슈 본문에 싣는 초과 행 상한(잘랐다는 사실은 report 에 남긴다)
_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_MAX_ATTEMPTS = 2
_MEASURE_COLUMNS = "finding_id,source,scope_status,document_id,firm_name,ingested_at"
_DECISIONS = ("accepted", "rejected")


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def summarize(
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    max_age_days: int,
) -> dict[str, Any]:
    """순수 함수 — needs_review 행 목록 → report. 나이는 소수 첫째 자리 일 단위."""
    by_source: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    aged: list[dict[str, Any]] = []
    oldest: float | None = None
    unknown_age = 0
    for row in rows:
        src = str(row.get("source") or "")
        scope = str(row.get("scope_status") or "")
        by_source[src] = by_source.get(src, 0) + 1
        by_scope[scope] = by_scope.get(scope, 0) + 1
        ts = _parse_ts(row.get("ingested_at"))
        if ts is None:
            # 시각이 없으면 나이를 모른다 — 조용히 0일로 치지 않고 초과로 본다.
            unknown_age += 1
            age = None
        else:
            age = round((now - ts).total_seconds() / 86400, 1)
            oldest = age if oldest is None else max(oldest, age)
        if age is None or age > max_age_days:
            aged.append({
                "finding_id": str(row.get("finding_id") or ""),
                "source": src,
                "scope_status": scope,
                "document_id": str(row.get("document_id") or ""),
                "firm_name": str(row.get("firm_name") or "")[:80],
                "age_days": age,
            })
    aged.sort(key=lambda r: (-(r["age_days"] if r["age_days"] is not None else 1e9), r["finding_id"]))
    return {
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "max_age_days": max_age_days,
        "needs_review": len(rows),
        "needs_review_visible": by_scope.get("ok", 0),
        "by_source": dict(sorted(by_source.items())),
        "by_scope": dict(sorted(by_scope.items())),
        "oldest_age_days": oldest,
        "unknown_age": unknown_age,
        "overdue": len(aged),
        "overdue_rows": aged[:_MAX_LISTED],
        "overdue_rows_truncated": max(0, len(aged) - _MAX_LISTED),
        "status": "overdue" if aged else "ok",
        "errors": [],
    }


def fetch_needs_review_all(base_url: str, service_key: str) -> list[dict[str, Any]]:
    base = fsb._normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_review_queue: SUPABASE_URL must start with https://")
    return fsb._fetch_all_pages(
        base, service_key, "findings",
        select=_MEASURE_COLUMNS, page_size=_PAGE_SIZE, order="finding_id.asc",
        extra_params={"review_status": "eq.needs_review"},
    )


def run_measure(
    base_url: str,
    service_key: str,
    *,
    max_age_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    try:
        rows = fetch_needs_review_all(base_url, service_key)
    except (RuntimeError, ValueError) as exc:
        report = summarize([], now=now, max_age_days=max_age_days)
        report["status"] = "error"
        report["errors"].append(str(exc))
        return report
    return summarize(rows, now=now, max_age_days=max_age_days)


# ---------------------------------------------------------------------------
# decide — 사람 판정 적용(기본 dry-run)
# ---------------------------------------------------------------------------


def load_decisions(path: str) -> list[dict[str, str]]:
    """판정 파일 검증. 형식이 틀리면 ValueError — 일부만 적용하는 일이 없게 전부 먼저 본다."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("decisions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("decisions 파일은 목록이거나 {\"decisions\": [...]} 이어야 한다")
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{i}번째 항목이 객체가 아니다")
        fid = str(item.get("finding_id") or "").strip()
        decision = str(item.get("decision") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not fid.startswith("finding-"):
            raise ValueError(f"{i}번째 항목 finding_id 형식 오류: {fid!r}")
        if decision not in _DECISIONS:
            raise ValueError(f"{fid}: decision 은 accepted/rejected 중 하나여야 한다 ({decision!r})")
        if not reason:
            raise ValueError(f"{fid}: reason(판정 근거)이 비어 있다")
        if fid in seen:
            raise ValueError(f"{fid}: 중복 항목")
        seen.add(fid)
        out.append({"finding_id": fid, "decision": decision, "reason": reason})
    return out


def _patch(base: str, service_key: str, finding_id: str, decision: str) -> tuple[int, str]:
    """(matched_rows, error). 가드 review_status=eq.needs_review — 절대 제거 금지."""
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {"finding_id": f"eq.{finding_id}", "review_status": "eq.needs_review"}
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.patch(
                f"{base}/rest/v1/findings", params=params,
                json={"review_status": decision}, headers=headers, timeout=_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, type(exc).__name__
        if resp.status_code >= 500 and attempt < _MAX_ATTEMPTS:
            continue
        if resp.status_code >= 400:
            return 0, f"http_{resp.status_code}"
        try:
            data = resp.json()
        except ValueError:
            return 0, ""
        return (len(data) if isinstance(data, list) else 0), ""
    return 0, "retry_exhausted"


def run_decide(
    base_url: str,
    service_key: str,
    decisions: list[dict[str, str]],
    *,
    apply: bool,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "planned": len(decisions),
        "planned_accepted": sum(1 for d in decisions if d["decision"] == "accepted"),
        "planned_rejected": sum(1 for d in decisions if d["decision"] == "rejected"),
        "patched": 0,
        "matched_zero": 0,
        "errors": [],
    }
    base = fsb._normalize_base_url(base_url)
    if base is None:
        report["errors"].append("SUPABASE_URL must start with https://")
        return report
    if not apply:
        return report
    for d in decisions:
        matched, err = _patch(base, service_key, d["finding_id"], d["decision"])
        if err:
            report["errors"].append(f"finding_id={d['finding_id']} PATCH failed ({err})")
        elif matched == 0:
            report["matched_zero"] += 1   # 이미 판정됨/없음 — 멱등 no-op
        elif matched == 1:
            report["patched"] += 1
        else:
            report["errors"].append(f"finding_id={d['finding_id']} PATCH matched {matched} rows")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(path: str | None, report: dict[str, Any]) -> None:
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="findings 검토 대기 큐 — 나이 측정(measure)·사람 판정 적용(decide)")
    parser.add_argument("command", nargs="?", default="measure", choices=("measure", "decide"))
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                        help=f"이 일수를 넘긴 행이 있으면 경보(기본 {DEFAULT_MAX_AGE_DAYS})")
    parser.add_argument("--decisions", help="decide: 판정 JSON 파일 경로")
    parser.add_argument("--apply", action="store_true", default=False,
                        help="decide: 실제 PATCH(없으면 dry-run)")
    parser.add_argument("--supabase-url", help="Supabase project URL (falls back to $SUPABASE_URL)")
    parser.add_argument("--service-role-key", help="(falls back to $SUPABASE_SERVICE_ROLE_KEY)")
    parser.add_argument("--output", help="report JSON 경로(기본 stdout 만)")
    args = parser.parse_args(argv)

    if args.command == "decide":
        if not args.decisions:
            print("findings_review_queue decide: --decisions 가 필요하다", file=sys.stderr)
            return 2
        try:
            decisions = load_decisions(args.decisions)
        except (OSError, ValueError) as exc:
            print(f"findings_review_queue decide: 판정 파일 오류 — {exc}", file=sys.stderr)
            return 2

    creds = _resolve_credentials(args)
    if creds is None:
        print("findings_review_queue: --supabase-url/--service-role-key 또는 "
              "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 가 필요하다", file=sys.stderr)
        return 2
    base_url, service_key = creds

    if args.command == "decide":
        report = run_decide(base_url, service_key, decisions, apply=args.apply)
        _emit(args.output, report)
        return 1 if report["errors"] else 0

    report = run_measure(base_url, service_key, max_age_days=args.max_age_days)
    _emit(args.output, report)
    return 0 if report["status"] == "ok" else 1


__all__ = ["summarize", "run_measure", "load_decisions", "run_decide", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
