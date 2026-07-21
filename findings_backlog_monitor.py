#!/usr/bin/env python3
"""FIND-1 findings 백로그 모니터 — 번역 격차·검수 백로그 임계 감시(읽기 전용).

배경(2026-07-21 RCA, 원인 B-2/C-3): 미번역 격차(findings − public_findings)나
needs_review 검수 백로그가 커져도 이를 붉게 실패시키는 소비자가 없었다. grm_health.py 는
**일일 수집 실행**만 판정하는 순수 모듈이라(네트워크·DB 접근 0) findings DB 의 격차를
읽지 않는다 — 그 순수성을 깨지 않기 위해 이 백로그 감시는 별도 모듈로 둔다.

이 모듈이 하는 일: 라이브 `public.findings_stats()` RPC(007/025, security definer·anon
집계 무해)를 PostgREST 로 한 번 POST 해서

  untranslated_gap = totals.findings − totals.public_findings   (미번역 = 공개 게이트 비공개)
  needs_review     = by_review_status[review_status='needs_review'].cnt 합
  rejected         = by_review_status[review_status='rejected'].cnt 합(관측용)

를 산출하고, 임계(기본 gap>300 · needs_review>300)를 넘으면 breach 를 report 에 싣고
exit 1(red)로 종료한다. 워크플로(grm-findings-backlog-monitor.yml)의 github-script 가 이
report JSON 을 읽어 운영 이슈를 열거나(임계 초과) 닫는다(정상 복귀).

안전/경계 계약(레포 하우스 스타일):
  - **읽기 전용.** 어떤 경로로도 findings 를 write 하지 않는다. findings_stats 는 카운트·
    서지 메타만 반환하고 finding_text/ko/evidence_url 을 노출하지 않는다(025 안전 계약).
  - service-role 키는 어떤 로그·예외 메시지·report 필드에도 넣지 않는다 — 예외 타입명과
    HTTP status 코드만 표면화한다(findings_reclassify_service 와 동형).
  - 임계는 CLI 로 조정 가능. 기본값은 "정상 일일 유입(소량)은 green, 실제 백로그만 red".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


_HTTP_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_STATS_RPC = "findings_stats"

# 기본 임계 — 정상 steady-state 일일 유입은 "소량"이므로 300 이상 적체는 정상 하루치를
# 넘어선 백로그로 본다(2026-07-21 지시문 P0: "1일치 초과 ~300건"). CLI 로 조정 가능.
DEFAULT_GAP_THRESHOLD = 300
DEFAULT_NEEDS_REVIEW_THRESHOLD = 300


def _post_stats_rpc(
    base_url: str,
    service_key: str,
    *,
    timeout: int = _HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any, str]:
    """POST rpc/findings_stats (인자 없음, body {}). service-role 키를 apikey+Bearer 로 싣되
    키는 반환 error 문자열에 절대 넣지 않는다(timeout/http_{status}/예외타입명만).
    반환: (status_code, parsed_json_or_None, error_summary). error_summary 는 2xx 에서 "".
    """
    url = f"{base_url}/rest/v1/rpc/{_STATS_RPC}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json={}, timeout=timeout)
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
            return resp.status_code, None, "invalid_response_shape"
        return resp.status_code, data, ""

    return 0, None, "retry_exhausted"  # unreachable safety net


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _review_status_count(stats: dict[str, Any], status: str) -> int:
    """by_review_status 배열에서 주어진 status 의 cnt 합(방어적으로 합산 — 정상은 1행)."""
    total = 0
    for entry in stats.get("by_review_status") or []:
        if isinstance(entry, dict) and entry.get("review_status") == status:
            total += _int(entry.get("cnt"))
    return total


def evaluate_backlog(
    stats: dict[str, Any],
    *,
    gap_threshold: int,
    needs_review_threshold: int,
) -> dict[str, Any]:
    """findings_stats 페이로드 → 지표·breach 판정(순수 함수, 네트워크 0)."""
    totals = stats.get("totals") if isinstance(stats.get("totals"), dict) else {}
    findings = _int(totals.get("findings"))
    public_findings = _int(totals.get("public_findings"))
    untranslated_gap = max(0, findings - public_findings)
    needs_review = _review_status_count(stats, "needs_review")
    rejected = _review_status_count(stats, "rejected")

    breaches: list[dict[str, Any]] = []
    if untranslated_gap > gap_threshold:
        breaches.append({
            "code": "untranslated-gap-high",
            "metric": "untranslated_gap",
            "value": untranslated_gap,
            "threshold": gap_threshold,
            "message": (
                f"미번역 격차 {untranslated_gap}건이 임계({gap_threshold})를 초과 — "
                "신규 유입이 번역 처리량을 앞질러 적체 중일 수 있습니다."
            ),
        })
    if needs_review > needs_review_threshold:
        breaches.append({
            "code": "needs-review-backlog-high",
            "metric": "needs_review",
            "value": needs_review,
            "threshold": needs_review_threshold,
            "message": (
                f"검수 대기(needs_review) {needs_review}건이 임계({needs_review_threshold})를 "
                "초과 — 검수 자동 승격이 유입을 따라가지 못하고 있을 수 있습니다."
            ),
        })

    return {
        "status": "failure" if breaches else "ok",
        "totals": {"findings": findings, "public_findings": public_findings},
        "untranslated_gap": untranslated_gap,
        "needs_review": needs_review,
        "rejected": rejected,
        "thresholds": {"gap": gap_threshold, "needs_review": needs_review_threshold},
        "breaches": breaches,
    }


def run_monitor(
    base_url: str,
    service_key: str,
    *,
    gap_threshold: int = DEFAULT_GAP_THRESHOLD,
    needs_review_threshold: int = DEFAULT_NEEDS_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    """findings_stats 를 읽어 백로그를 판정한다. 네트워크/파싱 오류는 status='error'."""
    report: dict[str, Any] = {
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "ok",
        "totals": {"findings": 0, "public_findings": 0},
        "untranslated_gap": 0,
        "needs_review": 0,
        "rejected": 0,
        "thresholds": {"gap": gap_threshold, "needs_review": needs_review_threshold},
        "breaches": [],
        "errors": [],
    }

    base = _normalize_supabase_url(base_url)
    if base is None:
        report["status"] = "error"
        report["errors"].append("SUPABASE_URL must start with https://")
        return report

    status, data, err = _post_stats_rpc(base, service_key)
    if err:
        report["status"] = "error"
        report["errors"].append(f"findings_stats RPC failed ({err})")
        return report
    if not isinstance(data, dict):
        report["status"] = "error"
        report["errors"].append("findings_stats returned a non-object payload")
        return report

    evaluated = evaluate_backlog(
        data,
        gap_threshold=gap_threshold,
        needs_review_threshold=needs_review_threshold,
    )
    report.update(evaluated)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    """report JSON 을 path 에 쓰고(있으면) 항상 stdout 에도 출력한다 — CI step summary 는
    gh CLI 로 조회 불가라 run 로그(stdout)가 지표를 실어야 한다. service-role 키는 report
    의 키·값 어디에도 없다(run_monitor/_post_stats_rpc 계약)."""
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 findings 백로그 모니터 — 미번역 격차·검수 백로그 임계 감시"
        "(READ ONLY; findings write·git write 없음). 임계 초과 시 exit 1(red)."
    )
    parser.add_argument(
        "--gap-threshold",
        type=int,
        default=DEFAULT_GAP_THRESHOLD,
        help=f"미번역 격차 임계(초과 시 breach). 기본 {DEFAULT_GAP_THRESHOLD}.",
    )
    parser.add_argument(
        "--needs-review-threshold",
        type=int,
        default=DEFAULT_NEEDS_REVIEW_THRESHOLD,
        help=f"needs_review 백로그 임계(초과 시 breach). 기본 {DEFAULT_NEEDS_REVIEW_THRESHOLD}.",
    )
    parser.add_argument("--supabase-url", help="Supabase project URL (falls back to $SUPABASE_URL)")
    parser.add_argument(
        "--service-role-key",
        help="Supabase service-role key (falls back to $SUPABASE_SERVICE_ROLE_KEY)",
    )
    parser.add_argument("--output", help="Report JSON output path (default: stdout only)")
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_backlog_monitor: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = run_monitor(
        base_url,
        service_key,
        gap_threshold=args.gap_threshold,
        needs_review_threshold=args.needs_review_threshold,
    )
    _write_report(args.output, report)

    if report["errors"] or report["breaches"]:
        return 1
    return 0


__all__ = [
    "evaluate_backlog",
    "run_monitor",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
