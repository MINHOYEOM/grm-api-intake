#!/usr/bin/env python3
"""OCR 엔진 부재로 **빈 본문 적재된** 스캔 483 소급 복구 — 1회성, Supabase 직행.

배경(2026-07-30 실측 · PR #487 진단): `collect_fda_483` 에 OCR 경로를 넣은 PR #456 이
tesseract 설치 스텝을 `grm-intake.yml` 에만 추가한 탓에, 같은 PDF 경로를 쓰는
`grm-findings-backfill-fetch.yml`(하루 3회 무인)이 엔진 없이 돌면서 스캔 483 을
`raw_json.fda483_text_status = "scan-ocr-unavailable:…"` 상태로 `raw_signals` 에 적재했다.
`raw_signals` 는 append-only + `document_id` dedup 이라 **한 번 빈손으로 들어간 문서는
영구히 빈손**이다 — 그 문서들은 observations 가 없어 findings 도 0건이다(공개 영향은
잘못된 내용 노출이 아니라 **누락**). PR #487 이 유입은 막았고, 이 스크립트가 이미 들어온
분을 되찾는다.

★왜 `grm-fda483-ocr-backfill.yml` 이 아닌가: 그 워크플로는 **발행본 브리프**(publish_date +
스캐폴드 아티팩트)를 기준으로 deep 델타 패치 **아티팩트**를 만들 뿐 `raw_signals` 를
갱신하지 않는다. 이번 대상은 브리프에 발행된 적 없는 백필 행이라 그 경로로는 닿지 않는다.

설계 — 부분 패치가 아니라 **행 전체 재구성**
  raw_json 만 손으로 기워 넣으면 `raw_sha256`(= sha256(canonical_json(raw_json)))·`row_json`·
  `body` 가 서로 어긋날 수 있다. 대신 라이브 수집과 **같은 함수**로 행을 다시 만든다:
      nrow(복원) → collect_fda_483._to_item(...) → findings_store.raw_signal_from_intake_item
  `raw_signal_id = "rawsig-" + stable_hash({schema_version, source, document_id})[:24]` 라
  **내용이 아니라 document_id 에만** 의존한다 → 재구성해도 id 가 같다. 그래서 DELETE 없이
  `Prefer: resolution=merge-duplicates` upsert 한 번으로 제자리 교체가 된다.
  DELETE 를 쓰지 않는 이유: `findings.raw_signal_id` 가 ON DELETE CASCADE 라, 지웠다가 삽입에
  실패하면 그 사이 findings 까지 사라진다. upsert 는 그 창이 없다.

  nrow 복원의 근거: `_to_item` 이 읽는 nrow 키는 9개(record_type·media_id·company·fei·
  state·country·establishment_type·record_date·publish_date)뿐이고, 그 9개가 전부
  raw_payload 에 그대로 저장된다(publish_date 는 원문 MM/DD/YYYY 그대로). 즉 무손실이다 —
  `tests/test_483_ocr_recovery.py` 의 왕복 동일성 테스트가 이 전제를 고정한다.

불가침 안전장치
  · **내용을 얻지 못하면 아무것도 쓰지 않는다.** OCR 이 여전히 실패하면 기존 행을 그대로
    둔다(빈 본문을 다른 빈 본문으로 덮어쓰지 않는다).
  · 재구성된 `raw_signal_id` 가 기존과 다르면 그 행은 **건너뛴다** — 떠돌이 행을 만들지 않는다.
  · `_to_item` 이 None(수의/기기/식품 도메인 게이트 드롭)이면 건너뛴다.
  · `collected_at` 은 **원본 값을 보존**한다. 문서가 수집된 시점은 그때가 맞고, 여기서
    갱신하면 하류의 시간축 질의가 소급해 흔들린다. 복구의 흔적은 raw_json 자체다
    (`fda483_text_status` 가 사라지고 observations 가 생긴다).
  · 멱등: 대상 선정 기준이 "`fda483_text_status` 가 scan-ocr-unavailable" 이므로, 복구에
    성공한 행은 다음 실행의 후보에서 자연히 빠진다.

findings 재생성은 이 스크립트가 하지 않는다 — upsert 로 observations 가 채워지면 기존
`findings_supabase_backfill.py`(M12)가 "findings 없는 raw_signal" 로 보고 변환한다.
워크플로가 그 체인을 잇는다(grm-findings-backfill-fetch.yml 의 체인과 동일 배관).

exit code 정책(`backfill_483_inspectors.py` 와 동일 계약):
  - 자격증명 누락 / SUPABASE_URL 형식 오류 / raw_signals 조회 자체 실패 → 2.
  - 건별 실패는 계속 진행하며 카운트만 한다. 다만 "광범위"하면(tesseract 미설치·fda.gov
    전면 차단 등 인프라성 신호) exit 1 로 침묵하지 않는다 — `_is_broad_failure`.
  - dry-run 은 PDF fetch + 재구성까지 수행하고 **쓰기만** 건너뛴다. 무엇이 얼마나 복구될지
    미리 보기 위해서다. 그래서 dry-run 도 Supabase 자격증명이 필요하다(후보 선정이 RLS 뒤).
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

import findings_store
import findings_supabase_append as fsa
import findings_supabase_backfill as fsb
from grm_cli import normalize_supabase_url as _normalize_base_url
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
from grm_common import SOURCE_FDA_483, log


SCHEMA_VERSION = "grm-483-ocr-recovery/v1"

DEFAULT_TIMEOUT_SECONDS = fsb.DEFAULT_TIMEOUT_SECONDS
_DEFAULT_PAGE_SIZE = 1000
_DEFAULT_DELAY_SECONDS = 0.5
_MAX_SAMPLES = 20

# 광범위 실패 판정 임계값 — backfill_483_inspectors 와 동일 규약(절대 기준 아님).
_BROAD_FAILURE_MIN_ATTEMPTS = 3
_BROAD_FAILURE_RATIO = 0.5

# 복구 대상 표식. collect_fda_483.is_ocr_engine_unavailable 과 같은 접두사를 쓰되, 이
# 스크립트는 collect_fda_483 를 지연 import 하므로(아래 간접층 참조) 상수를 여기 둔다.
# 정합성은 tests 가 두 값이 같음을 고정한다.
OCR_UNAVAILABLE_PREFIX = "scan-ocr-unavailable"

Sleeper = Callable[[float], None]


@dataclass
class OcrRecoveryReport:
    schema_version: str = SCHEMA_VERSION
    mode: str = ""                      # "dry_run" | "apply"
    limit: int | None = None
    delay_seconds: float = _DEFAULT_DELAY_SECONDS
    scanned: int = 0                    # source='FDA 483' raw_signals 전체(fetch 시점)
    marked: int = 0                     # 그 중 fda483_text_status 가 엔진부재인 행
    candidates: int = 0                 # 이번 실행에서 선택된 문서 수(doc-ids/limit 반영후)
    attempted: int = 0                  # 실제 PDF fetch 를 시도한 문서 수
    recovered: int = 0                  # 본문 확보(dry-run=교체 예정, apply=upsert 성공)
    still_empty: int = 0                # OCR 재시도에도 본문 0 — 기존 행 그대로 둠
    gate_dropped: int = 0               # _to_item None(도메인 게이트) — 손대지 않음
    id_mismatch: int = 0                # 재구성 id != 기존 id — 건너뜀(0 이어야 정상)
    failed: int = 0                     # fetch/재구성/upsert 실패
    observations_recovered: int = 0     # 되찾은 관찰 항목 총수(findings 예상 규모)
    failure_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def _default_sleep(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


def _bump(report: OcrRecoveryReport, reason: str) -> None:
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


def _parse_doc_ids(value: str) -> list[str]:
    if not value:
        return []
    return [p for p in re.split(r"[,\s]+", value.strip()) if p]


def is_ocr_unavailable_row(raw: dict[str, Any]) -> bool:
    """이 raw_payload 가 "엔진이 없어 본문을 못 받은" 행인가(순수 함수).

    `scan-no-text`(원문에 텍스트층 없음·OCR 비활성) · `scan-ocr-empty`(OCR 했으나 글자 0) ·
    `scan-ocr-budget`(예산 소진)은 **대상이 아니다** — 엔진을 붙여도 결과가 같거나(전자),
    별개 사유(후자)라 이 복구의 범위를 넘는다. 되찾을 수 있는 것만 손댄다.
    """
    status = str(raw.get("fda483_text_status") or "")
    return status.startswith(OCR_UNAVAILABLE_PREFIX)


def nrow_from_raw_payload(raw: dict[str, Any]) -> dict[str, str]:
    """저장된 raw_payload → `_to_item` 이 먹는 정규화 행(nrow) 복원.

    `_to_item` 이 읽는 키는 아래 9개뿐이고 전부 raw_payload 에 무손실로 남아 있다.
    (publish_date 는 `_to_item` 이 nrow 값을 **원문 MM/DD/YYYY 그대로** 실어 두므로 그대로
    되돌려주면 된다 — 여기서 ISO 로 바꾸면 안 된다.)
    이 매핑이 낡으면 왕복 동일성 테스트가 즉시 깨진다.
    """
    def _s(key: str) -> str:
        return str(raw.get(key) or "")

    return {
        "record_type": _s("record_type"),
        "media_id": _s("media_id"),
        "company": _s("firm"),
        "fei": _s("fei_number"),
        "state": _s("site_state"),
        "country": _s("country"),
        "establishment_type": _s("establishment_type"),
        "record_date": _s("record_date"),
        "publish_date": _s("publish_date"),
    }


# ---------------------------------------------------------------------------
# 지연 import 간접층 — collect_fda_483 는 import 시 환경 플래그를 읽으므로, 테스트가
# 이 모듈 속성만 패치해 net-free 로 돌 수 있게 얇은 간접층을 둔다
# (backfill_483_inspectors._fetch_text 와 동일 관례).
# ---------------------------------------------------------------------------


def _fetch_text(pdf_url: str) -> tuple[str, str]:
    from collect_fda_483 import _fetch_fda483_pdf_text
    return _fetch_fda483_pdf_text(pdf_url)


def _rebuild_item(nrow: dict[str, str], text: str, status: str) -> Any:
    """라이브 수집과 **같은 함수**로 IntakeItem 재구성. None 이면 도메인 게이트 드롭."""
    from collect_fda_483 import (
        _extract_483_observations_from_text,
        _extract_fda483_excerpt,
        _to_item,
    )
    excerpt = _extract_fda483_excerpt(text) if text else ""
    header_hints = {
        "establishment_type": nrow.get("establishment_type", ""),
        "fei_number": nrow.get("fei", ""),
        "firm_name": nrow.get("company", ""),
    }
    observations = _extract_483_observations_from_text(text, header_hints) if text else []
    # body_full="" — deep 전문 보존은 ENABLE_FDA_483_DEEP 게이트 산출물이고, 이 복구의
    # 목표는 findings 를 만드는 결정론 층(excerpt/observations)이다. 원 적재 경로
    # (collect_fda_backfill.run_483)도 body_full 을 "" 로 넘긴다 — 그 계약을 그대로 지킨다.
    return _to_item(nrow, excerpt, observations, "", status)


# ---------------------------------------------------------------------------
# Supabase 조회 / 쓰기
# ---------------------------------------------------------------------------


def _fetch_raw_signals(
    base_url: str, service_key: str, *, page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """source='FDA 483' raw_signals 중 **엔진 부재 표식이 있는** 행만.

    서버에서 `raw_json`(TEXT) 부분일치로 좁히고, 호출부가 파싱해 상태값을 정확히 재확인한다
    (like 는 좁히기용 · 판정은 `is_ocr_unavailable_row`). collected_at 은 upsert 시 원본을
    보존해야 하므로 함께 읽는다.
    """
    return fsb._fetch_all_pages(
        base_url, service_key, "raw_signals",
        select="raw_signal_id,document_id,collected_at,raw_json",
        page_size=page_size, order="raw_signal_id.asc",
        extra_params={
            "source": f"eq.{SOURCE_FDA_483}",
            "raw_json": f"like.*{OCR_UNAVAILABLE_PREFIX}*",
        },
    )


def _upsert_raw_signal(
    base_url: str,
    service_key: str,
    record: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, str]:
    """재구성된 raw_signals 행을 제자리 교체(upsert).

    `resolution=merge-duplicates` — 기본 적재 경로(`ignore-duplicates`)와 의도적으로 다르다.
    일상 수집은 "이미 있으면 두기"가 옳지만, 여기서는 **있는 것을 고치는 게** 목적이다.
    """
    return fsa._post_rows(
        base_url, service_key, "raw_signals",
        [fsa._raw_signal_payload(record)], "raw_signal_id",
        timeout=timeout, resolution="merge-duplicates",
    )


def _is_broad_failure(report: OcrRecoveryReport) -> bool:
    """실패가 인프라성인지 판정 — tesseract 미설치로 전건 실패하는 상황을 침묵시키지 않는다.
    (이 스크립트가 정확히 그 결함을 고치러 왔으므로, 같은 결함으로 조용히 끝나면 안 된다.)"""
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
    limit: int | None = None,
    delay_seconds: float = _DEFAULT_DELAY_SECONDS,
    doc_ids: list[str] | None = None,
    fetch_raw_signals: Callable[..., list[dict[str, Any]]] | None = None,
    fetch_text: Callable[[str], tuple[str, str]] | None = None,
    rebuild_item: Callable[..., Any] | None = None,
    upsert_raw_signal: Callable[..., tuple[int, list[dict[str, Any]] | None, str]] | None = None,
    sleeper: Sleeper | None = None,
) -> tuple[OcrRecoveryReport, int]:
    """지연 바인딩 설계(collect_eu_ncr_backfill.run·backfill_483_inspectors.run 과 동일):
    기본값을 def 시점이 아니라 호출 시점에 해석해, 테스트가 모듈 속성을 패치하면 반영된다."""
    fetch_raw = fetch_raw_signals or _fetch_raw_signals
    text_fn = fetch_text or _fetch_text
    rebuild_fn = rebuild_item or _rebuild_item
    upsert_fn = upsert_raw_signal or _upsert_raw_signal
    sleep_fn = sleeper or _default_sleep

    report = OcrRecoveryReport(
        mode="dry_run" if dry_run else "apply", limit=limit, delay_seconds=delay_seconds,
    )

    base = _normalize_base_url(base_url)
    if base is None:
        report.errors.append("SUPABASE_URL must start with https://")
        return report, 2

    try:
        rows = fetch_raw(base, service_key)
    except (RuntimeError, ValueError) as exc:
        report.errors.append(str(exc))
        log("ERROR", f"483 OCR 복구 조회 실패: {exc}")
        return report, 2

    report.scanned = len(rows)

    # 서버 like 는 좁히기용 — 상태값을 실제로 파싱해 대상만 남긴다.
    marked = [r for r in rows if is_ocr_unavailable_row(_json_object(r.get("raw_json")))]
    report.marked = len(marked)

    if doc_ids:
        wanted = set(doc_ids)
        marked = [
            r for r in marked
            if str(r.get("raw_signal_id") or "") in wanted
            or str(r.get("document_id") or "") in wanted
        ]

    marked.sort(key=lambda r: str(r.get("raw_signal_id") or ""))
    # `limit=0` 은 **전건**(0 이하 = 상한 없음) — 형제 워크플로들과 통일된 규약이다.
    # (2026-07-30 실사고: limit>=0 해석이 0 을 "0건 처리"로 받아 apply 가 조용히 무동작했다.)
    if limit is not None and limit > 0:
        marked = marked[:limit]
    report.candidates = len(marked)

    for r in marked:
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
            continue

        if not text:
            # OCR 을 다시 돌려도 본문이 없다 — 기존 행을 그대로 둔다. 빈손을 다른 빈손으로
            # 덮어쓰지 않는다(엔진이 여전히 없다면 failure_reasons 에 그 사유가 쌓인다).
            report.still_empty += 1
            _bump(report, status or "empty_text")
            continue

        nrow = nrow_from_raw_payload(raw)
        try:
            item = rebuild_fn(nrow, text, status)
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            reason = f"rebuild_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        if item is None:
            # 도메인 게이트(수의/기기/식품) 드롭 — 애초에 적재되지 말았어야 할 행일 수 있으나
            # 이 스크립트는 삭제하지 않는다(범위 밖). 세어서 드러내기만 한다.
            report.gate_dropped += 1
            continue

        record = findings_store.raw_signal_from_intake_item(
            item, collected_at=str(r.get("collected_at") or ""),   # ★원본 시점 보존
        )
        if str(record.get("raw_signal_id") or "") != rid:
            # 재구성이 다른 문서를 가리킨다 — 떠돌이 행을 만들지 않는다(정상이면 0건).
            report.id_mismatch += 1
            _bump(report, "raw_signal_id_mismatch")
            report.errors.append(f"{label}: raw_signal_id_mismatch")
            continue

        new_raw = _json_object(record.get("raw_json"))
        observations = new_raw.get("fda_483_observations") or []
        if not (new_raw.get("fda483_excerpt") or observations):
            # 텍스트는 받았지만 결정론 층이 아무것도 못 뽑았다 — 교체 이득이 없다.
            report.still_empty += 1
            _bump(report, "no_excerpt_or_observations")
            continue

        if len(report.samples) < _MAX_SAMPLES:
            report.samples.append({
                "raw_signal_id": rid,
                "document_id": doc_id,
                "pdf_status": status,
                "observations": len(observations),
                "excerpt_chars": len(str(new_raw.get("fda483_excerpt") or "")),
            })

        if dry_run:
            report.recovered += 1
            report.observations_recovered += len(observations)
            continue

        try:
            _status, returned, err = upsert_fn(base, service_key, record)
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            reason = f"upsert_raised:{type(exc).__name__}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            continue

        if err:
            report.failed += 1
            reason = f"upsert_failed:{err}"
            _bump(report, reason)
            report.errors.append(f"{label}: {reason}")
            log("WARN", f"483 raw_signals upsert 실패 {label}: {err}")
            continue

        report.recovered += 1
        report.observations_recovered += len(observations)

    if dry_run:
        log("INFO", f"[DRY] 대상 {report.candidates}건 중 복구 가능 {report.recovered}건"
                    f"(관찰 {report.observations_recovered}건) · 여전히 빈손 {report.still_empty}건 "
                    f"· 게이트드롭 {report.gate_dropped}건 · 실패 {report.failed}건")
    else:
        log("INFO", f"복구 upsert {report.recovered}건(관찰 {report.observations_recovered}건) "
                    f"· 여전히 빈손 {report.still_empty}건 · 게이트드롭 {report.gate_dropped}건 "
                    f"· 실패 {report.failed}건")

    exit_code = 1 if _is_broad_failure(report) else 0
    return report, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OCR 엔진 부재로 빈 본문 적재된 스캔 483 소급 복구 — raw_signals 행을 "
        "라이브 수집과 같은 함수로 재구성해 제자리 upsert. 1회성 dispatch 전용.",
    )
    p.add_argument(
        "--apply", action="store_true", default=False,
        help="실제 upsert 수행. 기본은 dry-run(조회+PDF fetch+재구성까지 하되 쓰기는 생략).",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="이번 실행에서 처리할 문서 수 상한(배치 분할용). 0 또는 미지정이면 전건.",
    )
    p.add_argument(
        "--delay-seconds", type=float, default=_DEFAULT_DELAY_SECONDS,
        help=f"PDF 요청 간 대기(초) — fda.gov 예의(기본 {_DEFAULT_DELAY_SECONDS}).",
    )
    p.add_argument(
        "--doc-ids", default="",
        help="선택: 처리할 raw_signal_id 또는 document_id 목록(공백/쉼표 구분).",
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
            "backfill_483_ocr_recovery: --supabase-url/--service-role-key 또는 "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요(dry-run 도 조회에 필요).",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    report, exit_code = run(
        base_url=base_url,
        service_key=service_key,
        dry_run=not args.apply,
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
    "OcrRecoveryReport",
    "run",
    "main",
    "build_arg_parser",
    "is_ocr_unavailable_row",
    "nrow_from_raw_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
