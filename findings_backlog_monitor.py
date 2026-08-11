#!/usr/bin/env python3
"""FIND-1 findings 백로그 모니터 — 번역 격차·검수 백로그·**추출 격차** 임계 감시(읽기 전용).

세 번째 검사(추출 격차, 2026-08-01 추가)는 앞의 둘과 성격이 다르다. 번역·검수 백로그는
"할 일이 쌓였다"를 세지만, 추출 격차는 **"산출물이 아예 안 나온 입력"** 을 센다. 추출
실패는 예외를 던지지 않고 정상 종료하며 빈손을 남기기 때문에, 실패 카운터·에러 로그·
CI 초록 어디에도 흔적이 없다. 오직 입력 대비 산출물을 세야만 보인다.


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

# ★추출 격차 임계(2026-08-01 RCA 산물). "문서는 적재됐는데 findings 가 0건"인 비율.
#   왜 필요한가: 추출 실패는 **예외를 던지지 않는다.** 정상 종료하고 빈손을 남긴다. 그래서
#   실패 카운터·에러 로그로는 영원히 안 잡히고, 오직 "산출물이 0인 입력"을 세야 보인다.
#   이 감시가 없어서 FDA 483 이 444건까지 조용히 쌓인 뒤에야 사람이 손으로 물어 발견됐고,
#   원인을 세 번 연속 OCR 로 오진했다. 처음 돌리자마자 아무도 몰랐던 식약처 29건(25.7%)이
#   같이 드러났다.
#   임계 설계: 비율만 보면 소량 소스가 시끄럽고(6건 중 1건=16.7%), 절대수만 보면 대형
#   소스의 만성 결손을 놓친다 → **둘 다 넘을 때만** breach.
DEFAULT_EXTRACTION_GAP_PCT = 5.0
DEFAULT_EXTRACTION_GAP_MIN_DOCS = 10
_EXTRACTION_RPC = "extraction_gap_by_source"

# ★국가 미매핑 감시(2026-08-11, 055 국가 축의 짝). 055 의 grm_normalize_country() 는
#   **그날 실측한 84개 원문 변종 / 47개 코드로 고정된 사전**이다. 사전은 반드시 낡는다 —
#   실제로 trends.js 의 국가 라벨이 "23종 verbatim"으로 박혀 있다가 실측 84종 대비 낡은
#   전적이 있다. 새 소스나 새 표기(`Korea, Republic of` 같은)가 들어오면 매핑에 없어서
#   country_key='' 로 **조용히** 떨어지고, 화면에서는 그냥 "미확인"에 섞여 구분되지 않는다.
#   그게 이 저장소가 반복해 당한 침묵 실패다.
#   055 는 그 대비로 findings_country_unmapped() 를 함께 신설했지만 **호출자가 0건**이었다
#   — 만들어만 두고 아무도 안 보는 슬롯이었다(2026-08-11 검증에서 발견). 여기에 배선한다.
#   임계 설계: 추출 격차와 달리 **비율이 아니라 존재 자체가 신호**다. 미매핑 변종이 하나라도
#   있으면 그건 "국가 축에서 통째로 빠지는 나라가 있다"는 뜻이라 1건부터 본다. 다만 변종이
#   대량으로 쏟아질 때 breach 를 무한정 만들지 않도록 상위 N개만 싣고 **잘랐다는 사실을
#   함께 적는다**(조용한 절단 금지).
DEFAULT_COUNTRY_UNMAPPED_MIN_ROWS = 1
_MAX_COUNTRY_UNMAPPED_BREACHES = 10
_COUNTRY_UNMAPPED_RPC = "findings_country_unmapped"


def _post_stats_rpc(
    base_url: str,
    service_key: str,
    *,
    rpc: str = _STATS_RPC,
    timeout: int = _HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any, str]:
    """POST rpc/<rpc> (인자 없음, body {}). service-role 키를 apikey+Bearer 로 싣되
    키는 반환 error 문자열에 절대 넣지 않는다(timeout/http_{status}/예외타입명만).
    반환: (status_code, parsed_json_or_None, error_summary). error_summary 는 2xx 에서 "".
    """
    url = f"{base_url}/rest/v1/rpc/{rpc}"
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


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate_extraction_gap(
    payload: dict[str, Any],
    *,
    pct_threshold: float,
    min_docs: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """extraction_gap_by_source 페이로드 → (breaches, by_source 요약). 순수 함수.

    한 소스라도 임계를 넘으면 breach 를 하나씩 낸다 — 합산하지 않는다. 합산은 이 사고의
    원인이었던 바로 그 실수다(서로 다른 원인을 한 숫자에 넣으면 진단이 불가능해진다).
    """
    rows = payload.get("by_source")
    summary: list[dict[str, Any]] = []
    breaches: list[dict[str, Any]] = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        docs = _int(entry.get("docs"))
        zero = _int(entry.get("zero_findings"))
        stored = _int(entry.get("zero_with_stored_text"))
        pct = _float(entry.get("zero_pct"))
        summary.append({
            "source": source, "docs": docs, "zero_findings": zero,
            "zero_with_stored_text": stored, "zero_pct": pct,
        })
        if zero >= min_docs and pct > pct_threshold:
            hint = (
                f" 그중 {stored}건은 본문으로 보이는 텍스트를 저장하고도 0건이므로 "
                "수집·OCR 이 아니라 **추출 로직**을 먼저 보십시오."
                if stored else
                " 저장된 본문이 없어 수집 단계부터 확인이 필요합니다."
            )
            breaches.append({
                "code": "extraction-gap-high",
                "metric": "zero_findings",
                "source": source,
                "value": zero,
                "threshold": min_docs,
                "zero_pct": pct,
                "pct_threshold": pct_threshold,
                "message": (
                    f"{source}: 적재 {docs}건 중 {zero}건({pct}%)이 지적사항 0건 — "
                    f"임계({min_docs}건 & {pct_threshold}%) 초과.{hint}"
                ),
            })
    return breaches, summary


def evaluate_country_unmapped(
    payload: Any,
    *,
    min_rows: int,
    max_breaches: int = _MAX_COUNTRY_UNMAPPED_BREACHES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """findings_country_unmapped 페이로드 → (breaches, 변종 요약). 순수 함수.

    RPC 는 `[{site_country, findings}]` 배열을 그대로 돌려준다(055 (D)). 한 변종이
    breach 하나다 — 합산하지 않는다(extraction_gap 과 같은 규율: 서로 다른 원인을 한
    숫자에 넣으면 진단이 불가능해진다). 각 변종은 "어느 나라를 코드로 못 바꿨는가"라는
    서로 다른 사실이고, 수리도 변종별로 매핑을 추가하는 일이다.

    ★상위 max_breaches 개만 breach 로 싣되, 잘라낸 개수를 마지막 breach 메시지에 적는다.
      조용한 절단은 "전부 봤다"는 착시를 만든다.
    """
    rows = payload if isinstance(payload, list) else []
    summary: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("site_country") or "")
        count = _int(entry.get("findings"))
        if not raw:
            # site_country 가 빈 값인 행은 RPC 가 애초에 제외한다(055). 방어적으로 한 번 더.
            continue
        summary.append({"site_country": raw, "findings": count})

    summary.sort(key=lambda item: (-item["findings"], item["site_country"]))
    over = [item for item in summary if item["findings"] >= min_rows]

    breaches: list[dict[str, Any]] = []
    for item in over[:max_breaches]:
        breaches.append({
            "code": "country-unmapped",
            "metric": "unmapped_site_country",
            "site_country": item["site_country"],
            "value": item["findings"],
            "threshold": min_rows,
            "message": (
                f"국가 미매핑: site_country={item['site_country']!r} {item['findings']}건이 "
                "ISO 코드로 정규화되지 않아 '미확인'으로 떨어졌습니다. "
                "web/migrations 의 grm_normalize_country() 와 grm_findings._COUNTRY_CODE_MAP "
                "양쪽에 이 표기를 추가하십시오(두 구현의 파리티는 "
                "tests/test_findings_country_key.py 가 고정합니다)."
            ),
        })
    dropped = len(over) - len(breaches)
    if dropped > 0 and breaches:
        breaches[-1]["message"] += f" (이 외 미매핑 변종 {dropped}종이 더 있습니다 — 전체는 report.country_unmapped 참조)"
    return breaches, summary


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
    extraction_pct_threshold: float = DEFAULT_EXTRACTION_GAP_PCT,
    extraction_min_docs: int = DEFAULT_EXTRACTION_GAP_MIN_DOCS,
    country_unmapped_min_rows: int = DEFAULT_COUNTRY_UNMAPPED_MIN_ROWS,
) -> dict[str, Any]:
    """findings_stats + extraction_gap_by_source + findings_country_unmapped 판정.

    오류는 status='error'.
    """
    report: dict[str, Any] = {
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "ok",
        "totals": {"findings": 0, "public_findings": 0},
        "untranslated_gap": 0,
        "needs_review": 0,
        "rejected": 0,
        "extraction_gap": [],
        "country_unmapped": [],
        "thresholds": {
            "gap": gap_threshold, "needs_review": needs_review_threshold,
            "extraction_pct": extraction_pct_threshold,
            "extraction_min_docs": extraction_min_docs,
            "country_unmapped_min_rows": country_unmapped_min_rows,
        },
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
    report.setdefault("errors", [])
    report["thresholds"] = {
        "gap": gap_threshold, "needs_review": needs_review_threshold,
        "extraction_pct": extraction_pct_threshold,
        "extraction_min_docs": extraction_min_docs,
        "country_unmapped_min_rows": country_unmapped_min_rows,
    }

    # ★추출 격차는 별도 RPC. 이 호출이 실패하면 **조용히 넘어가지 않는다** — 감시가 꺼진
    #   줄 모르고 초록을 믿는 것이 이 저장소가 이미 두 번 당한 함정이다(CI shim 표류).
    ex_status, ex_data, ex_err = _post_stats_rpc(base, service_key, rpc=_EXTRACTION_RPC)
    if ex_err:
        report["status"] = "error"
        report["errors"].append(f"{_EXTRACTION_RPC} RPC failed ({ex_err})")
        return report
    if not isinstance(ex_data, dict):
        report["status"] = "error"
        report["errors"].append(f"{_EXTRACTION_RPC} returned a non-object payload")
        return report

    ex_breaches, ex_summary = evaluate_extraction_gap(
        ex_data,
        pct_threshold=extraction_pct_threshold,
        min_docs=extraction_min_docs,
    )
    report["extraction_gap"] = ex_summary
    if ex_breaches:
        report["breaches"] = list(report.get("breaches") or []) + ex_breaches
        report["status"] = "failure"

    # ★국가 미매핑도 별도 RPC. 추출 격차와 같은 이유로 **실패를 조용히 넘기지 않는다** —
    #   감시가 꺼진 줄 모르고 초록을 믿는 것이 이 저장소가 이미 두 번 당한 함정이다.
    #   반환은 배열(`[{site_country, findings}]`)이라 dict 가 아니다 — 형 검사를 다르게 한다.
    cu_status, cu_data, cu_err = _post_stats_rpc(base, service_key, rpc=_COUNTRY_UNMAPPED_RPC)
    if cu_err:
        report["status"] = "error"
        report["errors"].append(f"{_COUNTRY_UNMAPPED_RPC} RPC failed ({cu_err})")
        return report
    if not isinstance(cu_data, list):
        report["status"] = "error"
        report["errors"].append(f"{_COUNTRY_UNMAPPED_RPC} returned a non-array payload")
        return report

    cu_breaches, cu_summary = evaluate_country_unmapped(
        cu_data, min_rows=country_unmapped_min_rows)
    report["country_unmapped"] = cu_summary
    if cu_breaches:
        report["breaches"] = list(report.get("breaches") or []) + cu_breaches
        report["status"] = "failure"
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
    parser.add_argument(
        "--extraction-pct-threshold",
        type=float,
        default=DEFAULT_EXTRACTION_GAP_PCT,
        help=("소스별 '지적사항 0건' 비율 임계(%%). 절대건수 임계와 **둘 다** 넘어야 breach. "
              f"기본 {DEFAULT_EXTRACTION_GAP_PCT}."),
    )
    parser.add_argument(
        "--extraction-min-docs",
        type=int,
        default=DEFAULT_EXTRACTION_GAP_MIN_DOCS,
        help=("소스별 '지적사항 0건' 최소 건수 임계. 소량 소스의 잡음을 막는다. "
              f"기본 {DEFAULT_EXTRACTION_GAP_MIN_DOCS}."),
    )
    parser.add_argument(
        "--country-unmapped-min-rows",
        type=int,
        default=DEFAULT_COUNTRY_UNMAPPED_MIN_ROWS,
        help=("국가 미매핑 변종의 최소 건수 임계(이상이면 breach). 추출 격차와 달리 "
              "**존재 자체가 신호**라 기본값이 1이다 — 미매핑 변종 1건은 그 나라가 국가 축에서 "
              f"통째로 빠진다는 뜻이다. 기본 {DEFAULT_COUNTRY_UNMAPPED_MIN_ROWS}."),
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
        extraction_pct_threshold=args.extraction_pct_threshold,
        extraction_min_docs=args.extraction_min_docs,
        country_unmapped_min_rows=args.country_unmapped_min_rows,
    )
    _write_report(args.output, report)

    if report["errors"] or report["breaches"]:
        return 1
    return 0


__all__ = [
    "evaluate_backlog",
    "evaluate_extraction_gap",
    "run_monitor",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
