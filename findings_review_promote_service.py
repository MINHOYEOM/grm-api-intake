#!/usr/bin/env python3
"""grm-finding-review-promote -- unattended CI 검수 자동승격 서비스(라이브 findings 테이블).

배경(2026-07-21 RCA 원인 C): FDA Warning Letter finding 은 태생부터 review_status=
'needs_review'(confidence 0.72)로 만들어지는데, needs_review→accepted 승격 파이프라인이
없어 영구히 needs_review 로 남았다(findings_reclassify_service 는 category 만 PATCH). 이
모듈이 그 승격 경로다 — **결정론·LLM 0**. findings_translate_apply_service /
findings_reclassify_service 와 동일 보안 모델을 따른다:

  - No LLM, no judgment calls. 승격 판정은 순수 결정론 신호다:
      승격(accepted) ⇔ 저장 cfr_refs(조항 인용)가 있고 **동시에** finding_text 에 위반/조건
      신호가 있으며(findings_extractors.wl_violation_signal_present — A-S1 드랍 게이트와 동일
      신호 원천) 길이 ≥ _PROMOTE_MIN_LEN. 라이브 3,144 accepted WL 대조에서 이 규칙을
      만족하는 rejected 는 0건이었다(정밀도 우선 — "애매하면 유지").
      그 외는 needs_review 그대로 둔다(사람/LLM 검수 몫 — 과잉 승격 금지).
  - service-role 키로 PostgREST 읽기(RLS 우회, M4 야간 적재·M12 백필과 동일 안전 메커니즘),
    Range 헤더 페이지네이션. 서버측 필터로 needs_review WL/483 만 가져온다.
  - review_status='needs_review' 행만, review_status **만** PATCH 한다. 가드
    review_status=eq.needs_review 로 멱등·경합 안전(이미 바뀐 행은 매칭 0). finding_text/
    finding_text_ko/scope_status/category 는 읽지도 쓰지도 않는다.
  - git 작업 전무.
  - (opt-in) --enable-reject: 명백한 오추출(조항·신호 전무 + 초단문)만 rejected. 기본 OFF —
    steady-state 에선 A-S1 파서 게이트가 신규 오추출을 애초에 방출하지 않으므로 자동 반려의
    정당 대상이 거의 없고, 저장 백로그의 미묘한 반려는 LLM/사람 몫으로 남긴다.

service-role 키는 어떤 로그·예외 메시지·report 필드에도 넣지 않는다 — 예외 타입명·HTTP
status 코드만 표면화한다(findings_reclassify_service 관례).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests

import findings_extractors as fx
import findings_supabase_backfill as fsb
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_DEFAULT_PAGE_SIZE = 1000

_TARGET_SOURCES = ("FDA Warning Letter", "FDA 483")
_PROMOTE_MIN_LEN = 60   # 조항+신호가 있어도 초단문은 승격하지 않는다(파편 방어)
_REJECT_MAX_LEN = 60    # opt-in 반려는 초단문에만(조항·신호 전무 + 이 길이 미만)

_SELECT_COLUMNS = "finding_id,finding_text,cfr_refs,source,review_status"


def _normalize_base_url(base_url: str) -> str | None:
    return fsb._normalize_base_url(base_url)


def _source_filter() -> str:
    # PostgREST in.("a","b") — 공백·특수문자 포함 값은 큰따옴표로 감싼다.
    quoted = ",".join(f'"{s}"' for s in _TARGET_SOURCES)
    return f"in.({quoted})"


def fetch_needs_review(
    base_url: str,
    service_key: str,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """scope_status='ok' AND review_status='needs_review' AND source∈타깃 행만 서버측 필터로
    가져온다(finding_id/finding_text/cfr_refs/source/review_status). finding_text/cfr_refs 는
    승격 판정 입력일 뿐 PATCH body 에는 절대 넣지 않는다."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_review_promote_service: SUPABASE_URL must start with https://")
    return fsb._fetch_all_pages(
        base, service_key, "findings",
        select=_SELECT_COLUMNS, page_size=page_size, order="finding_id.asc",
        extra_params={
            "scope_status": "eq.ok",
            "review_status": "eq.needs_review",
            "source": _source_filter(),
        },
    )


def _has_legal_ref(row: dict[str, Any]) -> bool:
    """저장 cfr_refs(조항 인용)가 하나라도 있는지. PostgREST 는 jsonb 를 list 로 반환하지만
    방어적으로 JSON 문자열도 처리한다."""
    refs = row.get("cfr_refs")
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except (ValueError, TypeError):
            return False
    return isinstance(refs, list) and len(refs) > 0


def review_verdict(row: dict[str, Any], *, enable_reject: bool) -> str | None:
    """행 하나 → 'accepted'(승격)/'rejected'(opt-in 반려)/None(유지). 결정론·순수.

    승격: 조항 인용 AND 위반/조건 신호 AND 길이≥_PROMOTE_MIN_LEN (고신뢰 규제 지적).
    반려(opt-in): 조항·신호 전무 AND 길이<_REJECT_MAX_LEN (명백한 파편/라벨 인용).
    그 외: None(needs_review 유지 — 애매하면 유지).
    """
    text = str(row.get("finding_text") or "")
    has_ref = _has_legal_ref(row)
    has_signal = fx.wl_violation_signal_present(text)

    if has_ref and has_signal and len(text) >= _PROMOTE_MIN_LEN:
        return "accepted"
    if enable_reject and not has_ref and not has_signal and len(text) < _REJECT_MAX_LEN:
        return "rejected"
    return None


def plan_review(
    rows: list[dict[str, Any]],
    *,
    enable_reject: bool,
) -> list[dict[str, Any]]:
    """승격/반려 대상만 (finding_id, new_status) 로 계획한다. 유지(None)는 제외 —
    대기 중 아무것도 없으면 0건 PATCH(구성상 멱등)."""
    plan: list[dict[str, Any]] = []
    for row in rows:
        finding_id = str(row.get("finding_id") or "")
        if not finding_id:
            continue
        new_status = review_verdict(row, enable_reject=enable_reject)
        if new_status is None:
            continue
        plan.append({"finding_id": finding_id, "new_status": new_status})
    return plan


def _patch_review_status(
    base_url: str,
    service_key: str,
    item: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """review_status 한 행 PATCH. 가드 review_status=eq.needs_review (경합·멱등: 이미 바뀐
    행은 매칭 0). Returns (status_code, returned_rows_or_None, error_summary) —
    error_summary 는 2xx 에서 "", 그 외 'timeout'/예외타입명/'http_{status}'(키 비노출).
    5xx/timeout 은 1회 재시도(총 2회)."""
    finding_id = str(item.get("finding_id") or "")
    new_status = str(item.get("new_status") or "")

    url = f"{base_url}/rest/v1/findings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {
        "finding_id": f"eq.{finding_id}",
        "review_status": "eq.needs_review",   # ← 가드. 절대 제거 금지
    }
    body = {"review_status": new_status}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.patch(url, params=params, json=body, headers=headers, timeout=timeout)
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


def run_promote(
    base_url: str,
    service_key: str,
    *,
    dry_run: bool,
    enable_reject: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """needs_review WL/483 을 가져와 결정론 판정하고, (dry_run 아니면) review_status 를
    PATCH 한다. 멱등: 이미 승격/반려된 테이블에 재실행하면 0건 PATCH."""
    base = _normalize_base_url(base_url)
    report: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "apply",
        "enable_reject": bool(enable_reject),
        "rows_scanned": 0,
        "promotions_planned": 0,
        "rejections_planned": 0,
        "kept_needs_review": 0,
        "patched": 0,
        "matched_zero": 0,
        "errors": [],
    }
    if base is None:
        report["errors"].append("SUPABASE_URL must start with https://")
        return report

    try:
        rows = fetch_needs_review(base, service_key)
    except (RuntimeError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report

    report["rows_scanned"] = len(rows)
    plan = plan_review(rows, enable_reject=enable_reject)
    if limit is not None and limit >= 0:
        plan = plan[:limit]

    report["promotions_planned"] = sum(1 for item in plan if item["new_status"] == "accepted")
    report["rejections_planned"] = sum(1 for item in plan if item["new_status"] == "rejected")
    report["kept_needs_review"] = len(rows) - len(plan)

    if dry_run:
        return report

    for item in plan:
        status, patched_rows, err = _patch_review_status(base, service_key, item)
        finding_id = item["finding_id"]
        if err:
            report["errors"].append(f"finding_id={finding_id} PATCH failed ({err})")
            continue
        matched = len(patched_rows or [])
        if matched == 0:
            # 이미 다른 실행/동시성으로 승격됐거나(review_status 가 더 이상 needs_review 아님)
            # 삭제됨 — 둘 다 TOCTOU 안전 no-op.
            report["matched_zero"] += 1
        elif matched == 1:
            report["patched"] += 1
        else:
            report["errors"].append(
                f"finding_id={finding_id} PATCH matched {matched} rows (expected 0 or 1)"
            )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_report(path: str | None, report: dict[str, Any]) -> None:
    """report JSON 을 path 에 쓰고(있으면) 항상 stdout 에도 출력한다. service-role 키는
    report 의 키·값 어디에도 없다(run_promote/_patch_review_status 계약)."""
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="grm-finding-review-promote -- unattended CI 검수 자동승격 서비스"
        "(no LLM, no git writes; READ + review_status PATCH only). 고신뢰 needs_review 를"
        " accepted 로 승격한다(애매하면 유지)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="읽기·판정만 — 승격/반려 예정 건수만 출력하고 PATCH 안 함.",
    )
    parser.add_argument(
        "--enable-reject",
        action="store_true",
        default=False,
        help="명백한 오추출(조항·신호 전무 + 초단문)을 rejected 로도 반려(기본 OFF — 보수적).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="PATCH 를 적용할 최대 건수(기본 전체).",
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
            "findings_review_promote_service: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report = run_promote(
        base_url, service_key,
        dry_run=args.dry_run, enable_reject=args.enable_reject, limit=args.limit,
    )
    _write_report(args.output, report)

    if report["errors"]:
        return 1
    return 0


__all__ = [
    "fetch_needs_review",
    "review_verdict",
    "plan_review",
    "run_promote",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
