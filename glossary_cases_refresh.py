#!/usr/bin/env python3
"""용어사전 "사례 건수" 주간 재측정 — `web/data/glossary_cases.json` 의 findings/documents
**숫자만** 다시 센다.

배경: 용어사전 각 용어 카드 아래 "이 용어로 검색되는 지적사례 N건 보기 →" 링크가 달린다(C1/C2).
그 숫자는 `public.findings_search` RPC(030, `/findings/?q=...` 화면이 쓰는 것과 **동일 함수**)를
호출해 얻는다. 지적사례는 매일 늘어나므로 화면 숫자가 몇 달 전 것이면 거짓말이 된다 — 자료실
(`library_staging_build.py` + `grm-library-staging.yml`)은 이미 주 1회 자동 갱신되는데 용어사전
에는 그런 장치가 없었다. 이 모듈이 그 격차를 메운다.

**불가침 계약**:
  · `q`(검색어)는 **절대 바꾸지 않는다** — 사람이 실제 지적 문장을 읽고 판정한 값이다.
  · `excluded` 배열도 건드리지 않는다 — 링크를 안 다는 이유가 이미 데이터에 적혀 있다.
  · 이 스크립트가 고치는 건 `items[].findings` / `items[].documents` / 최상위 `measured_on`
    뿐이다. 그 외 키(`schema`·`source`·`note`·`curated_on`·`corpus_note`·항목별 `note`)는
    무변형 통과.

**안전 게이트(자료실 갱신기 `library_staging_build.py` 와 같은 사상 — 부분 실패는 그 항목만
격리, 전면 중단은 실행 환경 문제일 때만)**:
  · 재측정값이 **0건**이 되면 → 그 항목은 **종전 값을 그대로 유지**한다(0건 링크를 만들지
    않는다는 화면 규칙 때문). 리포트에 표시하고 종료코드는 성공(0) — "그 소스만 격리, 나머지는
    정상 진행"과 동형이다.
  · **±50%(기본) 초과 변동**은 리포트에 표시만 하고 **값은 반영한다** — 저N 항목(예: 사례 1건
    짜리)은 정수 하나 차이로도 퍼센트가 커지므로 이건 "이상 신호"가 아니라 "봐 두라"는 표시다.
  · 호출 실패(타임아웃·5xx·형태 불량)는 **항목별로 격리**한다 — 하나 실패했다고 전체를 버리지
    않는다. 실패 항목은 종전 값을 유지한다.
  · 그러나 **전체 항목의 20%(기본) 이상이 실패**하면 이건 개별 검색어 문제가 아니라 실행
    환경(네트워크·RPC 자체) 문제로 보고 **아무것도 쓰지 않고 실패(exit 1)로 끝낸다**
    (`library_staging_build.py` 의 "과반 실패 시 전면 중단"과 동형, 다만 임계는 지시서대로 20%).

**접속 방법**: `findings_search` 는 030 마이그레이션에서 `security invoker` + RLS(010)로
`anon`·`authenticated` 에게 execute 가 부여돼 있다 — service-role 키가 필요 없다(오히려
service-role 은 RLS 를 우회해 비공개 행까지 셀 위험이 있어 쓰면 안 된다). 그래서 이 모듈은
`grm_cli.resolve_supabase_service_credentials`(service-role 전용) 대신 `findings_translate.py`
의 `--source supabase` 경로가 쓰는 것과 같은 **anon 키 자격증명 해석**을 따른다(패턴 재사용 —
새 방식 발명 금지). PostgREST POST 계약(재시도 1회·에러 요약에 키 미포함)은
`findings_backlog_monitor._post_stats_rpc` / `findings_translate._supabase_post_rpc` 와 동형.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests

from grm_cli import normalize_supabase_url as _normalize_supabase_url

SCHEMA_VERSION = "grm-glossary-cases/v1"
REPORT_SCHEMA_VERSION = "grm-glossary-cases-refresh-report/v1"
RPC_NAME = "findings_search"
DEFAULT_PATH = Path("web/data/glossary_cases.json")

_HTTP_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only

DEFAULT_LARGE_CHANGE_PCT = 50.0
DEFAULT_FAIL_ABORT_PCT = 20.0

FetchFn = Callable[[str], "tuple[int | None, int | None, str]"]


# ---------------------------------------------------------------------------
# PostgREST 호출(anon 키) — findings_backlog_monitor._post_stats_rpc 동형
# ---------------------------------------------------------------------------


def _post_findings_search(
    base_url: str,
    anon_key: str,
    q: str,
    *,
    timeout: int = _HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any, str]:
    """POST rpc/findings_search(p_q=q, p_page=1, p_docs_per_page=1) — anon 키.

    `/findings/?q=...` 화면이 호출하는 것과 **같은 함수·같은 게이트**(030: security invoker
    + RLS 010)를 쓴다. p_docs_per_page=1 은 응답을 작게 유지하려는 것뿐이고 totals 는 문서/
    페이지 크기와 무관하게 필터링된 전체 모집단 기준이라 정확하다.

    반환: (status_code, parsed_json_or_None, error_summary). error_summary 는 2xx+정상
    shape 에서 "". 키는 공개값(030 계약)이지만 그래도 에러 문자열에는 절대 넣지 않는다(레포
    관례 — findings_backlog_monitor/findings_translate 와 동형).
    """
    url = f"{base_url}/rest/v1/rpc/{RPC_NAME}"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    body = {"p_q": q, "p_page": 1, "p_docs_per_page": 1}
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
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


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_counts(
    base_url: str, anon_key: str, q: str, *, timeout: int = _HTTP_TIMEOUT_SECONDS,
) -> tuple[int | None, int | None, str]:
    """findings_search(q) 호출 → (findings, documents, error). error 는 정상 시 ""."""
    _status, data, err = _post_findings_search(base_url, anon_key, q, timeout=timeout)
    if err:
        return None, None, err
    if not isinstance(data, dict):
        return None, None, "invalid_response_shape"
    totals = data.get("totals")
    if not isinstance(totals, dict):
        return None, None, "invalid_response_shape"
    findings = _int(totals.get("findings"))
    documents = _int(totals.get("documents"))
    if findings is None or documents is None:
        return None, None, "invalid_response_shape"
    return findings, documents, ""


# ---------------------------------------------------------------------------
# 순수 함수 — 항목 1건 판정 / 리포트 집계(네트워크 0, 단위테스트 대상)
# ---------------------------------------------------------------------------


def evaluate_item(
    item: dict[str, Any],
    findings: int | None,
    documents: int | None,
    error: str,
    *,
    large_change_pct: float = DEFAULT_LARGE_CHANGE_PCT,
) -> dict[str, Any]:
    """항목 1건의 재측정 결과 → 적용값·플래그. 순수 함수(네트워크 0).

    변동률은 화면에 실제로 노출되는 숫자인 findings(지적사례 건수) 기준으로만 계산한다 —
    documents 는 참고로 같이 반영하되 게이트 판정에는 쓰지 않는다.
    """
    prev_findings = int(item.get("findings") or 0)
    prev_documents = int(item.get("documents") or 0)

    record: dict[str, Any] = {
        "id": item.get("id"),
        "q": item.get("q"),
        "previous_findings": prev_findings,
        "previous_documents": prev_documents,
        "fetched_findings": findings,
        "fetched_documents": documents,
        "error": error,
        "zero": False,
        "large_change": False,
        "failed": False,
        "change_pct": None,
    }

    if error:
        record["failed"] = True
        record["applied_findings"] = prev_findings
        record["applied_documents"] = prev_documents
        record["updated"] = False
        return record

    if findings == 0:
        # 0건 링크를 만들지 않는다는 화면 규칙 — 종전 값을 유지한다(실패가 아니라 정상 응답).
        record["zero"] = True
        record["applied_findings"] = prev_findings
        record["applied_documents"] = prev_documents
        record["updated"] = False
        return record

    if prev_findings > 0:
        pct = abs(findings - prev_findings) / prev_findings * 100.0
        record["change_pct"] = round(pct, 2)
        if pct > large_change_pct:
            record["large_change"] = True
    elif findings != prev_findings:
        # 종전 값이 0(정상적으로는 드묾)이었는데 새 값이 생기면 %는 정의 불가 — 표시만 한다.
        record["large_change"] = True

    record["applied_findings"] = findings
    record["applied_documents"] = documents if documents is not None else prev_documents
    record["updated"] = (
        record["applied_findings"] != prev_findings
        or record["applied_documents"] != prev_documents
    )
    return record


def build_report(
    results: list[dict[str, Any]],
    *,
    large_change_pct: float,
    fail_abort_pct: float,
    run_date: str,
) -> dict[str, Any]:
    """항목별 판정을 모아 리포트+게이트 판정. 순수 함수(네트워크 0).

    updated/zero_kept/failed 는 evaluate_item 의 세 반환 경로에 정확히 대응해 상호배타적으로
    전체를 분할한다(large_change 는 updated 의 부분집합인 플래그일 뿐 별도 파티션이 아니다) —
    따라서 unchanged = total - updated - zero_kept - failed 로 이중 차감 없이 계산된다.
    """
    total = len(results)
    failed = [r for r in results if r["failed"]]
    zero_kept = [r for r in results if r["zero"]]
    large_change = [r for r in results if r["large_change"]]
    updated = [r for r in results if r["updated"]]

    fail_rate_pct = (len(failed) / total * 100.0) if total else 0.0
    aborted = total > 0 and fail_rate_pct >= fail_abort_pct

    warnings: list[str] = []
    for r in failed:
        warnings.append(f"[실패] {r['id']} ({r['q']}): {r['error']} — 종전 값 유지")
    for r in zero_kept:
        warnings.append(
            f"[0건] {r['id']} ({r['q']}): 재측정 0건 — 종전 값({r['previous_findings']}) 유지"
        )
    for r in large_change:
        warnings.append(
            f"[변동 큼] {r['id']} ({r['q']}): {r['previous_findings']} → "
            f"{r['fetched_findings']} ({r['change_pct']}%)"
        )

    review_reasons: list[str] = []
    if aborted:
        review_reasons.append(
            f"실패율 {fail_rate_pct:.1f}% >= {fail_abort_pct}% — 전체 중단, 파일 미기록"
        )
    else:
        if failed:
            review_reasons.append(f"항목 {len(failed)}건 재측정 실패(종전 값 유지)")
        if zero_kept:
            review_reasons.append(f"항목 {len(zero_kept)}건 0건 전환(종전 값 유지)")
        if large_change:
            review_reasons.append(f"항목 {len(large_change)}건 ±{large_change_pct}% 초과 변동")

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_at": run_date,
        "total_items": total,
        "counts": {
            "updated": len(updated),
            "unchanged": total - len(updated) - len(zero_kept) - len(failed),
            "zero_kept": len(zero_kept),
            "large_change": len(large_change),
            "failed": len(failed),
        },
        "thresholds": {
            "large_change_pct": large_change_pct,
            "fail_abort_pct": fail_abort_pct,
        },
        "fail_rate_pct": round(fail_rate_pct, 2),
        "aborted": aborted,
        # gate 구조는 library_staging_build.evaluate_gates 와 같은 모양(automatic_merge_allowed
        # + review_reasons) — 워크플로가 같은 방식으로 소비한다(자동 머지 여부 판정).
        "gate": {
            "automatic_merge_allowed": (
                not aborted and not failed and not zero_kept and not large_change
            ),
            "review_reasons": review_reasons,
        },
        "warnings": warnings,
        "results": results,
    }


def run_refresh(
    payload: dict[str, Any],
    fetch: FetchFn,
    *,
    large_change_pct: float = DEFAULT_LARGE_CHANGE_PCT,
    fail_abort_pct: float = DEFAULT_FAIL_ABORT_PCT,
    run_date: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """payload(파일 그대로 로드한 dict)를 재측정한다.

    반환 (new_payload, report). aborted 이면 new_payload 는 None(쓸 것이 없다는 뜻 — 파일 write
    여부는 호출자가 aborted 를 보고 결정한다). q·excluded·기타 최상위 키는 전부 무변형 통과.
    """
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("payload.items must be a list")

    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("q") or "").strip():
            raise ValueError(f"malformed item (missing q): {item!r}")
        findings, documents, error = fetch(item["q"])
        results.append(
            evaluate_item(item, findings, documents, error, large_change_pct=large_change_pct)
        )

    report = build_report(
        results,
        large_change_pct=large_change_pct,
        fail_abort_pct=fail_abort_pct,
        run_date=run_date or date.today().isoformat(),
    )

    if report["aborted"]:
        return None, report

    new_items = []
    for item, result in zip(items, results):
        new_item = dict(item)  # 항목별 note 등 다른 필드는 무변형 통과
        new_item["findings"] = result["applied_findings"]
        new_item["documents"] = result["applied_documents"]
        new_items.append(new_item)

    new_payload = dict(payload)  # schema/source/note/curated_on/corpus_note/excluded 무변형
    new_payload["items"] = new_items
    new_payload["measured_on"] = report["run_at"]
    return new_payload, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    """anon 키 자격증명 해석 — findings_translate.py `--source supabase` 경로와 동형.

    (grm_cli.resolve_supabase_service_credentials 는 **service-role** 전용이라 여기 쓰지
    않는다 — findings_search 는 anon 실행 권한이 이미 있고 RLS 로 게이트되므로 service-role
    은 오히려 과잉권한이다.)
    """
    url = (args.supabase_url or os.environ.get("SUPABASE_URL") or "").strip()
    key = (args.supabase_anon_key or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return url, key


def _load_payload(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unexpected schema {data.get('schema')!r} (expected {SCHEMA_VERSION!r})"
        )
    return data


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    # 원본 파일 스타일 그대로(indent=2·ensure_ascii=False·키 순서 보존) — grm_cli.write_json
    # 은 sort_keys=True 라 최상위 키(schema/source/measured_on/...) 순서가 매번 재배열돼
    # 실제 변경과 무관한 거대 diff 를 만든다. 여기선 dict 삽입 순서를 그대로 직렬화한다.
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _print_summary(report: dict[str, Any]) -> None:
    counts = report["counts"]
    print(
        f"glossary_cases_refresh: 총 {report['total_items']}건 — "
        f"갱신 {counts['updated']} · 무변동 {counts['unchanged']} · "
        f"0건유지 {counts['zero_kept']} · 변동큼(>±{report['thresholds']['large_change_pct']}%) "
        f"{counts['large_change']} · 실패 {counts['failed']}"
    )
    for line in report["warnings"]:
        print(f"  - {line}")
    if report["aborted"]:
        print(
            f"glossary_cases_refresh: 실패율 {report['fail_rate_pct']}%가 임계"
            f"({report['thresholds']['fail_abort_pct']}%) 이상 — 파일 미기록, 실패 종료",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", type=Path, default=DEFAULT_PATH,
        help=f"glossary_cases.json 경로(읽고 씀, 기본 {DEFAULT_PATH})",
    )
    parser.add_argument("--supabase-url", help="Supabase project URL (falls back to $SUPABASE_URL)")
    parser.add_argument(
        "--supabase-anon-key",
        help="Supabase anon key (falls back to $SUPABASE_ANON_KEY) — findings_search 는 anon "
        "실행 권한이 있어 service-role 불필요(030)",
    )
    parser.add_argument("--large-change-pct", type=float, default=DEFAULT_LARGE_CHANGE_PCT)
    parser.add_argument("--fail-abort-pct", type=float, default=DEFAULT_FAIL_ABORT_PCT)
    parser.add_argument("--timeout", type=int, default=_HTTP_TIMEOUT_SECONDS)
    parser.add_argument(
        "--report", type=Path, help="진단 리포트 JSON 출력 경로(옵션 — stdout 에는 항상 요약)"
    )
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    args = parser.parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "glossary_cases_refresh: --supabase-url/--supabase-anon-key or "
            "$SUPABASE_URL/$SUPABASE_ANON_KEY are required",
            file=sys.stderr,
        )
        return 2
    raw_base_url, anon_key = creds
    base_url = _normalize_supabase_url(raw_base_url)
    if base_url is None:
        print("glossary_cases_refresh: --supabase-url must start with https://", file=sys.stderr)
        return 2

    try:
        payload = _load_payload(args.path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"glossary_cases_refresh: {exc}", file=sys.stderr)
        return 2

    def _fetch(q: str) -> tuple[int | None, int | None, str]:
        return fetch_counts(base_url, anon_key, q, timeout=args.timeout)

    try:
        new_payload, report = run_refresh(
            payload, _fetch,
            large_change_pct=args.large_change_pct,
            fail_abort_pct=args.fail_abort_pct,
        )
    except ValueError as exc:
        print(f"glossary_cases_refresh: {exc}", file=sys.stderr)
        return 2

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    _print_summary(report)

    if report["aborted"]:
        return 1

    if args.dry_run:
        print("glossary_cases_refresh: --dry-run — 파일 미기록")
        return 0

    assert new_payload is not None  # aborted=False 이면 run_refresh 계약상 항상 있음
    _write_payload(args.path, new_payload)
    print(f"glossary_cases_refresh: {args.path} 갱신 완료(measured_on={new_payload['measured_on']})")
    return 0


__all__ = [
    "evaluate_item",
    "build_report",
    "run_refresh",
    "fetch_counts",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
