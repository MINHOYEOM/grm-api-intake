#!/usr/bin/env python3
"""FDA 483 실사관 이름(inspector_names) 소급 백필 — 1회성, Supabase 직행.

배경(2026-07-30 실측): Supabase `raw_signals` 에 source='FDA 483' 행이 1,998건, 전부
`raw_json.pdf_url` 을 보유한다. `findings` 에는 483 findings 가 9,440건/1,546개 문서
(raw_signal_id)로 걸려 있고, `inspector_names` 는 현재 전량 `[]`(원문 전문 fda483_body_full
이 25건만 남아 있어 나머지는 PDF 재fetch 가 필요). 이 스크립트는 그 재fetch+파싱+PATCH 를
수행하는 소급 백필이다 — `collect_eu_ncr_backfill.py`(EU GMP NCR 딥 백필)와 동일한 설계
원형을 따른다: dataclass 리포트·dry-run/apply 분리·건별 실패 계속·exit code 정책.

★raw_signals 는 절대 건드리지 않는다(raw_sha256 무결성 보존). 갱신 대상은 오직
`findings.inspector_names` 한 컬럼뿐이다. 처리 단위는 raw_signal_id(문서) — 한 문서의
findings 는 모두 같은 실사관 리스트를 공유하므로, 문서당 한 번만 fetch+파싱하고 그 결과를
해당 raw_signal_id 의 findings 행 전체에 PATCH 한다.

멱등성: `inspector_names <> '[]'` 인 문서는 기본적으로 후보에서 제외한다(--force 로 재처리
허용). 재실행하면 이미 채워진 문서는 건드리지 않고 잔여만 처리하므로, 배치를 여러 번에
나눠 돌려도 안전하다(--limit 로 배치 분할).

★PDF fetch/파싱은 라이브 라인과 동일 함수를 재사용한다(신규 파싱 로직 금지) —
  - `collect_fda_483._fetch_fda483_pdf_text(pdf_url)` — fetch + 텍스트화 + 스캔 OCR 폴백.
  - `collect_fda_483._extract_483_inspectors(text)` — 실사관 이름 파서.
`_extract_483_inspectors` 는 이 워크트리 시점엔 다른 세션이 병행 작업 중이라 아직 없을 수
있다. 그래서 모듈 최상단에서 import 하지 않고, `collect_fda_483.py` 자신이 `_extract_pdf_text`
를 함수 내부에서 지연 import 하는 관례(약 636·970행)를 그대로 따라 얇은 지연 import 간접층
(`_fetch_text`/`_extract_inspectors`)을 둔다. 테스트는 이 두 모듈 속성을 직접 패치해
collect_fda_483 없이도 net-free 로 돈다.

PostgREST 경계: 자격증명/URL 정규화는 `grm_cli.py` 의 기존 헬퍼를 쓰고, 페이지네이션
GET 은 `findings_supabase_backfill._fetch_all_pages`(기존 백필이 쓰는 것과 동일 배관)를
그대로 재사용한다. PATCH 는 이 스크립트가 유일하게 손대는 새 컬럼(inspector_names)이라
직접 구현하되, `findings_review_promote_service._patch_review_status` /
`findings_translate_apply_service._send_findings_request` 와 구조적으로 동일한 재시도/에러
요약 계약을 따른다. service-role 키는 로그·리포트·예외 문자열 어디에도 싣지 않는다 —
예외 타입명·HTTP status 코드만 표면화한다.

exit code 정책:
  - 자격증명 누락 / SUPABASE_URL 형식 오류 / raw_signals·findings 조회 자체 실패
    (네트워크·인증 등 인프라 레벨) → 2.
  - 건별 실패(개별 PDF fetch/파싱/PATCH 오류)는 계속 진행하며 카운트만 한다. 다만 그
    실패가 "광범위"하면(시도한 문서 중 다수가 실패 — tesseract 미설치, fda.gov 전면 차단
    등 인프라성 신호) exit 1 로 침묵하지 않는다. 판정은 `_is_broad_failure`: 시도 3건
    미만이면 "시도 전부 실패", 3건 이상이면 "실패율 50% 이상"을 광범위 실패로 본다(이
    임계값은 이 스크립트의 판단이며, 절대적 기준이 있는 것은 아니다).
  - dry-run 은 실제 PDF fetch+파싱까지 수행하고 PATCH 만 건너뛴다 — 백필 전에 추출률을
    미리 볼 수 있어야 하기 때문이다. 그래서 dry-run 도 Supabase 자격증명이 필요하다
    (raw_signals/findings 조회 자체가 RLS 뒤에 있어 service-role 키 없이는 후보조차
    못 고른다) — `collect_eu_ncr_backfill.py`(dry-run 이 Supabase 를 아예 안 건드리는
    설계)와 이 지점만 다르다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483, log

import requests


SCHEMA_VERSION = "grm-483-inspector-backfill/v1"

DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout — mirrors fsb/fsa convention.
_DEFAULT_PAGE_SIZE = 1000
_DEFAULT_DELAY_SECONDS = 0.5
_MAX_SAMPLES = 20

# 광범위 실패 판정 임계값(문서 상단 참고) — 절대 기준이 아니라 이 스크립트의 판단.
_BROAD_FAILURE_MIN_ATTEMPTS = 3
_BROAD_FAILURE_RATIO = 0.5

Sleeper = Callable[[float], None]


@dataclass
class InspectorBackfillReport:
    schema_version: str = SCHEMA_VERSION
    mode: str = ""  # "dry_run" | "apply"
    force: bool = False
    limit: int | None = None
    delay_seconds: float = _DEFAULT_DELAY_SECONDS
    raw_signals_scanned: int = 0        # source='FDA 483' raw_signals 전체(fetch 시점)
    documents_with_findings: int = 0    # 그 중 findings 행이 >=1 있는 문서(백필 대상 우주)
    already_filled: int = 0             # inspector_names 이미 non-empty → 건너뜀(--force 없이)
    candidates: int = 0                 # 이번 실행에서 선택된 문서 수(스킵+doc-ids+limit 반영후)
    attempted: int = 0                  # 실제로 fetch+파싱을 시도한 문서 수(candidates 와 일치해야 함)
    succeeded: int = 0                  # >=1명 추출(dry-run=쓰기 예정, apply=실제 PATCH 성공)
    zero_extracted: int = 0             # fetch/파싱은 됐으나 추출 0명 — 빈 배열로 덮어쓰지 않음
    failed: int = 0                     # fetch/파싱/PATCH 실패
    patch_matched_zero: int = 0         # apply 모드 경합: PATCH 시점엔 이미 채워져 있었음(TOCTOU 안전)
    findings_rows_updated: int = 0      # 실제 PATCH 로 갱신된 findings 행 총수(apply 모드만)
    failure_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)  # 최대 20건: 문서 id → 추출 이름


def _default_sleep(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


def _bump(report: InspectorBackfillReport, reason: str) -> None:
    report.failure_reasons[reason] = report.failure_reasons.get(reason, 0) + 1


def _json_object(value: Any) -> dict[str, Any]:
    """raw_json(TEXT 컬럼) → dict. PostgREST 가 이미 dict 로 준 경우도 방어."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """inspector_names(jsonb) → list. PostgREST 는 보통 네이티브 list 를 주지만, 방어적으로
    JSON 문자열도 처리한다(findings_review_promote_service._has_legal_ref 와 동일 관례)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _parse_doc_ids(value: str) -> list[str]:
    """--doc-ids 문자열(공백/쉼표 구분) → 목록. raw_signal_id 또는 document_id 어느 쪽이든
    매칭 대상으로 받아들인다(연산자가 어느 쪽을 손에 쥐고 있는지 모르므로 둘 다 허용)."""
    if not value:
        return []
    return [p for p in re.split(r"[,\s]+", value.strip()) if p]


# ---------------------------------------------------------------------------
# 지연 import 간접층 — collect_fda_483._extract_483_inspectors 는 다른 세션이 병행 작업
# 중이라 이 워크트리 시점엔 아직 없을 수 있다. 모듈 최상단 import 를 피해 그 부재가 이
# 모듈의 import 자체를 깨지 않게 한다(collect_fda_483.py 636·970행의 기존 관례와 동일).
# ---------------------------------------------------------------------------


def _extract_inspectors(text: str) -> list[str]:
    from collect_fda_483 import _extract_483_inspectors
    return _extract_483_inspectors(text)


def _fetch_text(pdf_url: str) -> tuple[str, str]:
    from collect_fda_483 import _fetch_fda483_pdf_text
    return _fetch_fda483_pdf_text(pdf_url)


# ---------------------------------------------------------------------------
# Supabase 조회 — 페이지네이션은 findings_supabase_backfill._fetch_all_pages 그대로 재사용
# (다른 백필 스크립트가 하는 그대로: findings_review_promote_service.fetch_needs_review 참조).
# ---------------------------------------------------------------------------


def _fetch_raw_signals(
    base_url: str, service_key: str, *, page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """source='FDA 483' raw_signals 전건(raw_signal_id/document_id/raw_json 만)."""
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,raw_json",
        page_size=page_size, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}"},
    )


def _fetch_findings_inspector_state(
    base_url: str, service_key: str, *, page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """source='FDA 483' findings 전건의 (raw_signal_id, inspector_names) — 문서별로 이미
    채워졌는지 판정하는 용도라 그 외 컬럼(finding_text 등)은 읽지 않는다."""
    return fsb._fetch_all_pages(
        base_url, service_key, "findings",
        select="raw_signal_id,inspector_names",
        page_size=page_size, order="raw_signal_id.asc",
        extra_params={"source": f"eq.{SOURCE_FDA_483}"},
    )


def _patch_inspector_names(
    base_url: str,
    service_key: str,
    raw_signal_id: str,
    names: list[str],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """raw_signal_id 로 묶인 findings 행 전체의 inspector_names 를 PATCH.

    Returns (status_code, returned_rows_or_None, error_summary) — error_summary 는 2xx 에서
    "", 그 외 'timeout'/예외타입명/'http_{status}'(서비스키 비노출). 5xx/timeout 은 1회
    재시도(총 2회) — findings_review_promote_service._patch_review_status 와 동일 계약.
    return=representation 이라 성공 시 rows 길이가 실제 매칭/갱신 행 수다.
    """
    url = f"{base_url}/rest/v1/findings"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {"raw_signal_id": f"eq.{raw_signal_id}"}
    body = {"inspector_names": names}

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


def _is_broad_failure(report: InspectorBackfillReport) -> bool:
    """시도한 문서 대비 실패가 "광범위"한지(인프라성 신호) 판정 — 개별 PDF 결함은 정상
    노이즈로 취급하되, tesseract 미설치/fda.gov 전면 차단 같은 시스템적 실패는 침묵하지
    않는다. 절대 기준이 아니라 이 스크립트가 정한 임계값(문서 상단 참고)."""
    if report.attempted == 0:
        return False
    if report.attempted < _BROAD_FAILURE_MIN_ATTEMPTS:
        return report.failed == report.attempted
    return (report.failed / report.attempted) >= _BROAD_FAILURE_RATIO


def run(
    *,
    base_url: str,
    service_key: str,
    dry_run: bool,
    force: bool = False,
    limit: int | None = None,
    delay_seconds: float = _DEFAULT_DELAY_SECONDS,
    doc_ids: list[str] | None = None,
    fetch_raw_signals: Callable[..., list[dict[str, Any]]] | None = None,
    fetch_findings: Callable[..., list[dict[str, Any]]] | None = None,
    fetch_text: Callable[[str], tuple[str, str]] | None = None,
    extract_inspectors: Callable[[str], list[str]] | None = None,
    patch_inspectors: Callable[..., tuple[int, list[dict[str, Any]] | None, str]] | None = None,
    sleeper: Sleeper | None = None,
) -> tuple[InspectorBackfillReport, int]:
    """collect_eu_ncr_backfill.run 과 동일한 지연 바인딩 설계: 기본값을 def 시점이 아닌
    호출 시점의 모듈 전역으로 해석해, 테스트가 이 모듈의 속성을 패치하면 그대로 반영된다.
    """
    fetch_raw = fetch_raw_signals or _fetch_raw_signals
    fetch_find = fetch_findings or _fetch_findings_inspector_state
    text_fn = fetch_text or _fetch_text
    extract_fn = extract_inspectors or _extract_inspectors
    patch_fn = patch_inspectors or _patch_inspector_names
    sleep_fn = sleeper or _default_sleep

    report = InspectorBackfillReport(
        mode="dry_run" if dry_run else "apply", force=force, limit=limit,
        delay_seconds=delay_seconds,
    )

    base = _normalize_base_url(base_url)
    if base is None:
        report.errors.append("SUPABASE_URL must start with https://")
        return report, 2

    try:
        raw_rows = fetch_raw(base, service_key)
        finding_rows = fetch_find(base, service_key)
    except (RuntimeError, ValueError) as exc:
        # 조회 자체 실패(네트워크/인증) — 후보조차 못 고르므로 인프라 레벨 실패(exit 2).
        report.errors.append(str(exc))
        log("ERROR", f"483 실사관 백필 조회 실패: {exc}")
        return report, 2

    report.raw_signals_scanned = len(raw_rows)

    filled_ids: set[str] = set()
    ids_with_findings: set[str] = set()
    for f in finding_rows:
        rid = str(f.get("raw_signal_id") or "")
        if not rid:
            continue
        ids_with_findings.add(rid)
        if _as_list(f.get("inspector_names")):
            filled_ids.add(rid)

    # findings 행이 하나도 없는 raw_signal(추출 자체가 안 된 문서)은 이 스크립트의 대상이
    # 아니다 — 갱신할 findings 행이 없다(1,998 raw_signals 대비 1,546 문서 갭의 정체).
    docs = [r for r in raw_rows if str(r.get("raw_signal_id") or "") in ids_with_findings]
    report.documents_with_findings = len(docs)

    if doc_ids:
        wanted = set(doc_ids)
        docs = [
            r for r in docs
            if str(r.get("raw_signal_id") or "") in wanted
            or str(r.get("document_id") or "") in wanted
        ]

    selected: list[dict[str, Any]] = []
    for r in docs:
        rid = str(r.get("raw_signal_id") or "")
        if rid in filled_ids and not force:
            report.already_filled += 1
            continue
        selected.append(r)

    # 결정론 순서(raw_signal_id 오름차순) — --limit 로 배치를 나눠도 재실행마다 같은 순서로
    # 잔여를 처리한다(이미 채워진 앞 배치는 already_filled 로 자연스럽게 건너뛴다).
    selected.sort(key=lambda r: str(r.get("raw_signal_id") or ""))
    # [2026-07-30 실사고] `limit=0` 은 **전건**을 뜻한다(0 이하 = 상한 없음).
    #   종전 `limit >= 0` 은 0 을 "0건 처리"로 받아 apply 모드에서 아무것도 안 하고
    #   **성공으로 끝났다**(실측: 1,546 문서 대상 실행이 candidates=0·exit 0 으로 종료).
    #   형제 워크플로 `grm-fda483-ocr-backfill.yml` 이 `0=전건` 규약이라 호출자가 그대로
    #   0 을 넘긴 것이 원인 — 규약을 형제와 통일해 이 침묵 무동작을 원천 제거한다.
    #   배치를 나누려면 1 이상을 준다.
    if limit is not None and limit > 0:
        selected = selected[:limit]
    report.candidates = len(selected)

    for r in selected:
        rid = str(r.get("raw_signal_id") or "")
        doc_id = str(r.get("document_id") or "")
        label = doc_id or rid
        raw = _json_object(r.get("raw_json"))
        pdf_url = str(raw.get("pdf_url") or "").strip()

        report.attempted += 1

        if not pdf_url:
            report.failed += 1
            _bump(report, "missing_pdf_url")
            report.errors.append(f"{label}: missing_pdf_url")
            continue

        sleep_fn(delay_seconds)
        try:
            text, status = text_fn(pdf_url)
        except Exception as exc:  # noqa: BLE001 — 건별 실패는 계속(멱등 재실행 가능)
            report.failed += 1
            reason = f"fetch_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            log("WARN", f"483 PDF fetch 예외 {label}: {type(exc).__name__}")
            continue

        if not text:
            report.failed += 1
            reason = status or "empty_text"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        try:
            names = extract_fn(text)
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            reason = f"extract_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            log("WARN", f"483 실사관 파서 예외 {label}: {type(exc).__name__}")
            continue

        names = [n for n in (names or []) if isinstance(n, str) and n.strip()]

        if len(report.samples) < _MAX_SAMPLES:
            report.samples.append({
                "raw_signal_id": rid,
                "document_id": doc_id,
                "pdf_status": status,
                "inspector_names": names,
            })

        if not names:
            # 추출 0명 — 빈 배열로 덮어쓰지 않는다(멱등 계약: 다음 실행에서 파서가 개선되면
            # 다시 시도할 수 있어야 하므로 already_filled 로 잠그지 않는다).
            report.zero_extracted += 1
            continue

        if dry_run:
            report.succeeded += 1
            continue

        try:
            _status, rows, err = patch_fn(base, service_key, rid, names)
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            reason = f"patch_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        if err:
            report.failed += 1
            reason = f"patch_failed:{err}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            log("WARN", f"483 실사관 PATCH 실패 {label}: {err}")
            continue

        matched = len(rows or [])
        if matched == 0:
            # 이 실행의 select 시점과 PATCH 시점 사이에 이미 채워졌다(동시 실행/수동 개입) —
            # TOCTOU 안전 no-op, 에러 아님.
            report.patch_matched_zero += 1
            continue
        report.succeeded += 1
        report.findings_rows_updated += matched

    if dry_run:
        log("INFO", f"[DRY] 후보 {report.candidates}건 중 추출 성공 {report.succeeded}건 "
                    f"· 0명 {report.zero_extracted}건 · 실패 {report.failed}건")
    else:
        log("INFO", f"PATCH {report.succeeded}건({report.findings_rows_updated} findings 행) "
                    f"· 0명 {report.zero_extracted}건 · 실패 {report.failed}건")

    exit_code = 1 if _is_broad_failure(report) else 0
    return report, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FDA 483 실사관 이름(inspector_names) 소급 백필 — Supabase 직행"
        "(raw_signals 는 읽기 전용, findings.inspector_names 만 PATCH). 1회성 dispatch 전용.",
    )
    p.add_argument(
        "--apply", action="store_true", default=False,
        help="실제 PATCH 수행. 기본은 dry-run(조회+PDF fetch+파싱까지 하되 쓰기는 생략).",
    )
    p.add_argument(
        "--force", action="store_true", default=False,
        help="inspector_names 가 이미 채워진 문서도 재처리한다(기본은 건너뜀).",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="이번 실행에서 처리할 문서 수 상한(배치 분할용). 미지정 시 전건.",
    )
    p.add_argument(
        "--delay-seconds", type=float, default=_DEFAULT_DELAY_SECONDS,
        help=f"PDF 요청 간 대기(초) — fda.gov 예의(기본 {_DEFAULT_DELAY_SECONDS}).",
    )
    p.add_argument(
        "--doc-ids", default="",
        help="선택: 처리할 raw_signal_id 또는 document_id 목록(공백/쉼표 구분). "
        "지정 시 이 문서만 후보로 삼는다(scaffold 대조 없이 직접 지정).",
    )
    p.add_argument("--report-path", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--supabase-url", help="미지정 시 $SUPABASE_URL")
    p.add_argument("--service-role-key", help="미지정 시 $SUPABASE_SERVICE_ROLE_KEY")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "backfill_483_inspectors: --supabase-url/--service-role-key 또는 "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(dry-run 도 조회에 필요).",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report, exit_code = run(
        base_url=base_url,
        service_key=service_key,
        dry_run=not args.apply,
        force=args.force,
        limit=args.limit,
        delay_seconds=args.delay_seconds,
        doc_ids=_parse_doc_ids(args.doc_ids),
    )

    payload = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.report_path:
        Path(args.report_path).write_text(payload + "\n", encoding="utf-8")
    return exit_code


__all__ = [
    "InspectorBackfillReport",
    "run",
    "main",
    "build_arg_parser",
]


if __name__ == "__main__":
    raise SystemExit(main())
