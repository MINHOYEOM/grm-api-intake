#!/usr/bin/env python3
"""FIND-1 M12 -- offline backfill of findings for raw_signals that already live
in Supabase but never got their findings extracted/inserted.

Background: FIND-1 M4's automatic findings append only runs inside
collect_intake's insert_items, for raw_signals that a given run newly
collects. A raw_signal that already exists in Supabase (document_id dedup
skips it on later runs) never gets revisited, so if the findings-append
feature flag was off, or the extractor code hadn't shipped yet, at the time a
raw_signal was first collected, its findings are permanently missing unless
something explicitly backfills them. This module is that "something" --
a reusable, deterministic (no LLM) backfill path, not a one-off SQL script.

This module is pure transport + composition of the existing FIND-1 layers:
  - findings_extractors.findings_from_raw_signal   (record -> findings, incl. M11 WL fan-out)
  - findings_supabase_append._append_findings_batch (findings -> POST, idempotent 409 fallback)
  - findings_supabase_append._finding_payload / _normalize_base_url / _post_rows / DEFAULT_TIMEOUT_SECONDS

It does not read/write SQLite, Notion, or collect_intake, and it never
constructs or re-POSTs raw_signals -- every raw_signal this module touches
already exists in Supabase (that's the whole premise of a backfill), so only
`findings` rows are ever inserted.

Translation is explicitly out of scope: backfilled findings are inserted
untranslated (finding_text_ko/translation_method left at their extractor
defaults), matching the M9 weekly translation loop's expectations -- the
public-facing RLS gate (M9) keeps untranslated rows out of the website until
the normal weekly translation pass fills in finding_text_ko later.

The service-role key is never included in any log line, exception message,
or report field -- only exception type names and HTTP status codes are
surfaced, mirroring findings_supabase_append.py's and
findings_translate_apply_service.py's convention.

사용법 -- 문서별 dry 차이 보고서 (`--report`, 2026-11-13 가산)
  python findings_supabase_backfill.py --document-ids "fda483-195517 fda483-151431" \
      --dry-run --report _ops/reports/m12_doc_diff.md            # 쓰기 0, 보고서만
  python findings_supabase_backfill.py --document-ids "..." --report out.md
                                                                  # 보고서(반영 직전 상태) + 종전 append
  `--report` 는 `--document-ids` 와 함께만 쓴다(없으면 exit 2). 문서마다
    (1) 현재 DB findings 행(finding_id·표제 앞부분·scope_status·review_status)
    (2) 현행 추출기(findings_extractors.findings_from_raw_signal)로 새로 뽑은 행
    (3) 처리 예측: 새로 생김 / 건너뜀(finding_id 일치) / 충돌 예측(유니크 인덱스
        (raw_signal_id, md5(finding_text)) -- 다른 finding_id 인데 같은 표제면 409 로 중복 처리돼 안 들어간다)
    (4) 새 행 scope 미리보기 -- 기본은 DB 분류 함수(grm_classify_483_scope / grm_classify_wl_scope)를
        PostgREST RPC 로 호출(IMMUTABLE select 함수, 쓰기 없음). `--report-scope approx` 이거나 RPC 가
        실패하면 로컬 근사로 계산하고 '근사' 라고 적는다.
    (5) 잡문 판정(서식 안내문·주석란·표제 조각 -- 추출기 판정 함수 재사용)과 '숨김 필요(별도 조치)'.
        이 도구는 기존 행을 지우거나 숨기지 않는다.
  문서 상태: new_rows / all_present / no_observation_in_source(재추출할 관찰이 없고 기존 행도 잡문뿐 --
  원문/PDF 자체에 관찰이 없다) / conflict / not_found.
  불변: `--report` 를 안 주면 호출 순서·stdout(JSON 요약)·exit code 가 종전과 같다. 줘도 stdout 은
  같은 JSON 요약뿐이고(보고서 경로 안내는 stderr), 기본값은 여전히 '반영'(dry 아님)이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

import findings_extractors
import findings_supabase_append as fsa
from grm_cli import header_ci as _header_ci
from grm_cli import parse_content_range as _parse_content_range
from grm_cli import resolve_supabase_service_credentials as _resolve_credentials


DEFAULT_TIMEOUT_SECONDS = fsa.DEFAULT_TIMEOUT_SECONDS
_MAX_ATTEMPTS = 2  # initial try + 1 retry, for 5xx/timeout only
_DEFAULT_PAGE_SIZE = 1000


@dataclass(frozen=True)
class BackfillReport:
    raw_scanned: int = 0
    unbackfilled: int = 0
    with_findings: int = 0
    findings_extracted: int = 0
    findings_inserted: int = 0
    findings_duplicate: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _normalize_base_url(base_url: str) -> str | None:
    return fsa._normalize_base_url(base_url)


def _get_page(
    base_url: str,
    service_key: str,
    table: str,
    *,
    select: str,
    offset: int,
    limit: int,
    order: str | None = None,
    extra_params: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[dict[str, Any]] | None, dict[str, Any], str]:
    """GET one page of a PostgREST resource with the service-role key,
    paginated via the Range/Range-Unit headers (not limit/offset query
    params) so callers can read past PostgREST's default 1000-row cap.

    extra_params (optional) are added to the query string as-is -- callers use
    it for PostgREST row filters (e.g. {"review_status": "eq.needs_review"}),
    which compose with count=exact so Content-Range reports the *filtered*
    total. select/order are reserved (extra_params must not override them).

    Returns (status_code, rows_or_None, response_headers, error_summary).
    error_summary is "" on 2xx, else "timeout", an exception type name, or
    "http_{status}" -- never exception text, so the service-role key
    embedded in a lower-level transport error can never leak through it.

    Retries once (total 2 attempts) for 5xx responses or a request timeout,
    mirroring findings_supabase_append._post_rows' retry contract.
    """
    url = f"{base_url}/rest/v1/{table}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Range-Unit": "items",
        "Range": f"{offset}-{offset + limit - 1}",
        "Prefer": "count=exact",
    }
    params: dict[str, str] = dict(extra_params or {})
    params["select"] = select
    if order:
        params["order"] = order

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.Timeout:
            if attempt < _MAX_ATTEMPTS:
                continue
            return 0, None, {}, "timeout"
        except requests.exceptions.RequestException as exc:
            return 0, None, {}, type(exc).__name__

        if resp.status_code >= 500:
            if attempt < _MAX_ATTEMPTS:
                continue
            return resp.status_code, None, {}, f"http_{resp.status_code}"
        if resp.status_code >= 400:
            return resp.status_code, None, {}, f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            data = []
        rows = data if isinstance(data, list) else []
        return resp.status_code, rows, dict(resp.headers), ""

    return 0, None, {}, "retry_exhausted"  # unreachable safety net


def _fetch_all_pages(
    base_url: str,
    service_key: str,
    table: str,
    *,
    select: str,
    page_size: int,
    order: str | None = None,
    extra_params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Page through a PostgREST resource to completion via Range headers.

    extra_params (optional) are PostgREST row filters applied to every page
    (see _get_page); Content-Range then reports the filtered total so
    pagination terminates on the filtered set.

    Raises RuntimeError (never the service key) if any page fails after
    retries. Stops when Content-Range reports the exact total has been
    reached, or -- if the total can't be determined -- when a page comes
    back shorter than page_size (the last-page signal).
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while True:
        _status, page_rows, headers, err = _get_page(
            base_url, service_key, table, select=select, offset=offset,
            limit=page_size, order=order, extra_params=extra_params,
        )
        if err:
            raise RuntimeError(f"findings_supabase_backfill: GET {table} failed ({err})")

        page_rows = page_rows or []
        rows.extend(page_rows)

        parsed_total = _parse_content_range(_header_ci(headers, "Content-Range"))
        if parsed_total is not None:
            total = parsed_total

        offset += page_size

        if not page_rows:
            break
        if total is not None:
            if offset >= total or len(rows) >= total:
                break
        elif len(page_rows) < page_size:
            break

    return rows


def fetch_raw_signals(
    base_url: str,
    service_key: str,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Fetch every raw_signals row (select=*), fully paginated. raw_json/
    row_json come back as text (they're text columns) -- callers pass the
    row straight into findings_extractors.findings_from_raw_signal, which
    parses them internally via its own _json_object helper.
    """
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_supabase_backfill: SUPABASE_URL must start with https://")
    return _fetch_all_pages(
        base, service_key, "raw_signals", select="*", page_size=page_size, order="raw_signal_id.asc",
    )


def fetch_existing_finding_raw_ids(
    base_url: str,
    service_key: str,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> set[str]:
    """Fetch the set of raw_signal_id values that already have >=1 finding."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_supabase_backfill: SUPABASE_URL must start with https://")
    rows = _fetch_all_pages(
        base, service_key, "findings", select="raw_signal_id", page_size=page_size, order="raw_signal_id.asc",
    )
    return {str(row.get("raw_signal_id") or "") for row in rows if row.get("raw_signal_id")}


def select_unbackfilled(
    raw_signals: list[dict[str, Any]],
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    """raw_signals whose raw_signal_id has no finding yet."""
    return [
        rs for rs in raw_signals
        if str(rs.get("raw_signal_id") or "") not in existing_ids
    ]


def plan_backfill(
    raw_signals: list[dict[str, Any]],
    existing_ids: set[str],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Extract findings for every unbackfilled raw_signal; keep only the
    (raw_signal, findings) pairs where >=1 finding was extracted (sources
    with no extractor coverage naturally yield 0 findings and are dropped
    here rather than attempted as a no-op append).
    """
    pairs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for raw_signal in select_unbackfilled(raw_signals, existing_ids):
        findings = findings_extractors.findings_from_raw_signal(raw_signal)
        if findings:
            pairs.append((raw_signal, findings))
    return pairs


def run_backfill(
    base_url: str,
    service_key: str,
    *,
    dry_run: bool,
    limit: int | None = None,
    document_ids: list[str] | None = None,
    report_path: str | None = None,
    report_scope: str = "db",
    report_scope_fn: "ScopeFn | None" = None,
) -> BackfillReport:
    """Fetch raw_signals + existing findings, plan the backfill, and (unless
    dry_run) append the missing findings via
    findings_supabase_append._append_findings_batch -- which is idempotent
    (a 409/duplicate row is counted, not an error).

    raw_signal rows themselves are never re-POSTed here -- they already
    exist in Supabase; only their derived findings are appended.

    report_path (with document_ids) writes the per-document diff report
    (see module docstring) *before* any append, reading only (SELECT +
    IMMUTABLE classify RPC). It never changes the returned BackfillReport,
    and a report failure never stops the append (stderr notice only).
    report_scope_fn injects the scope classifier (tests / alternative paths).
    """
    base = _normalize_base_url(base_url)
    if base is None:
        return BackfillReport(errors=("SUPABASE_URL must start with https://",))

    try:
        raw_signals = fetch_raw_signals(base, service_key)
        existing_ids = fetch_existing_finding_raw_ids(base, service_key)
    except (RuntimeError, ValueError) as exc:
        return BackfillReport(errors=(str(exc),))

    if document_ids:
        # [2026-11-12] 지목한 문서는 findings 가 이미 있어도 다시 뽑아 **빠진 것만** 더한다
        # (append 는 finding_id 충돌을 중복으로 세므로 기존 행은 그대로다). 섞인 PDF 재추출
        # (backfill_483_ocr_recovery mode=named)로 관찰이 늘어난 문서용 — 195468 은 1 → 4.
        wanted = set(document_ids)
        unbackfilled = [rs for rs in raw_signals if str(rs.get("document_id") or "") in wanted]
    else:
        unbackfilled = select_unbackfilled(raw_signals, existing_ids)
    if limit is not None and limit >= 0:
        unbackfilled = unbackfilled[:limit]

    pairs = plan_backfill(unbackfilled, set())
    with_findings = len(pairs)
    findings_extracted = sum(len(findings) for _rs, findings in pairs)

    if report_path and document_ids:
        _write_document_report(
            base, service_key, report_path,
            document_ids=document_ids, raw_signals=unbackfilled, dry_run=dry_run,
            report_scope=report_scope, scope_fn=report_scope_fn,
        )

    if dry_run:
        return BackfillReport(
            raw_scanned=len(raw_signals),
            unbackfilled=len(unbackfilled),
            with_findings=with_findings,
            findings_extracted=findings_extracted,
            findings_inserted=0,
            findings_duplicate=0,
            errors=(),
        )

    findings_inserted = 0
    findings_duplicate = 0
    errors: list[str] = []
    for _raw_signal, findings in pairs:
        inserted, duplicate, _had_error, batch_errors = fsa._append_findings_batch(
            base, service_key, findings,
        )
        findings_inserted += inserted
        findings_duplicate += duplicate
        errors.extend(batch_errors)

    return BackfillReport(
        raw_scanned=len(raw_signals),
        unbackfilled=len(unbackfilled),
        with_findings=with_findings,
        findings_extracted=findings_extracted,
        findings_inserted=findings_inserted,
        findings_duplicate=findings_duplicate,
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# --report: 문서별 dry 차이 보고서 (2026-11-13 가산 -- 읽기만 한다)
# ---------------------------------------------------------------------------

STATUS_NEW_ROWS = "new_rows"
STATUS_ALL_PRESENT = "all_present"
STATUS_NO_OBSERVATION = "no_observation_in_source"
STATUS_CONFLICT = "conflict"
STATUS_NOT_FOUND = "not_found"

ACTION_NEW = "new"
ACTION_SKIP_EXISTING = "skip_existing"
ACTION_CONFLICT_EXISTING = "conflict_existing"
ACTION_CONFLICT_BATCH = "conflict_batch"

_ACTION_LABEL = {
    ACTION_NEW: "새로 생김",
    ACTION_SKIP_EXISTING: "건너뜀(finding_id 일치)",
    ACTION_CONFLICT_EXISTING: "충돌 예측 — 기존 행과 표제 같음(409 → 중복 처리, 안 들어감)",
    ACTION_CONFLICT_BATCH: "충돌 예측 — 같은 배치 앞 행과 표제 같음(409 → 중복 처리, 안 들어감)",
}

# 보고서 표에 보이는 표제 앞부분(화면용 — 저장값이 아니다).
_REPORT_TITLE_HEAD = 70
_REPORT_FINDINGS_ID_CHUNK = 50
# 표제 30자 미만 = 잡문 후보(추출기 조각 기준·DB scope 'fragment' 기준과 같은 30).
_REPORT_SHORT_TITLE = 30

# 483 양식 안내문 — 관찰이 아닌 양식 뒷면 문구가 표제 자리에 들어온 것. collect_fda_483 의
# _FORM_BOILERPLATE_RE 와 같은 문구군이지만 그 모듈은 수집 의존이 무거워 여기서 import 하지 않고,
# OCR 이 띄어쓰기를 먹은 꼴("Toassist firms…", 실측 fda483-195517)도 받도록 `\s*` 로 둔다.
# 보고서 표시 전용이다(추출·저장에는 쓰지 않는다).
_FORM_NOTICE_TITLE_RE = re.compile(
    r"^\W*to\s*assist\s+firms\s+inspected"
    r"|pursuant\s+to\s+section\s+704|section\s+704\s*\(\s*b\s*\)"
    r"|or\s+plan\s+to\s+implement\s+corrective\s+action"
    r"|this\s+document\s+lists\s+observations\s+made"
    r"|see\s+reverse\s+of\s+this\s+page"
    r"|employee\(s\)\s+signature",
    re.I,
)
_OBSERVATION_MARKER_RE = re.compile(r"\bOBSERVATION\s*\d", re.I)

# grm_classify_483_scope(web/migrations/146) 의 로컬 근사 — PG `\y` 를 `\b` 로 옮겼다. DB 함수가
# 바뀌면 어긋날 수 있으니 결과에 '근사' 를 붙인다.
_SCOPE_PHARMA_EST_RE = re.compile(
    r"(drug|pharmac|active pharmaceutical|\bapi\b|outsourcing facility|compound|biolog|vaccine|dosage"
    r"|homeopathic|heparin|anda sponsor|red cross|plasma derivative|viral vector|gene therap|cell therap"
    r"|biotherap|monoclonal|recombinant|oral solution|pharmaceutical packag)", re.I)
_SCOPE_NON_PHARMA_EST_RE = re.compile(
    r"(shell egg|egg manufacturer|cheese|peanut|sprout|pistachio|fruit processor|pet food|animal feed"
    r"|infant formula|produce manufacturer|aircraft|\bfarm\b|institutional review board|clinical investigator"
    r"|medical device|health care facility|\bfood\b|smoked fish|dietary supplement|veterinar|tissue)", re.I)
_SCOPE_OTHER_PHARMA_EST_RE = re.compile(
    r"(sterile|plasma|blood|nuclear|own label|repacker/relabeler|(control|contract) testing laborator)", re.I)
_SCOPE_PHARMA_FIRM_RE = re.compile(r"(pharmac|biolog|therapeut|biosciences)", re.I)
_SCOPE_LAW_NAME_RE = re.compile(
    r"(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra|\bFD&C\b"
    r"|department of health)", re.I)
_SCOPE_DRUG_DOC_RE = re.compile(
    r"(drug products?|drug substance|active pharmaceutical ingredient|aseptic|sterilit|\bsterile\b"
    r"|compounded?|\bUSP\b|batch record|master production|\b211\.\d|finished pharmaceutical|prescription"
    r"|\bNDC\b|\bOTC\b|potency|adverse drug|quality control unit|\bDSCSA\b|tablets?|capsules?|injectable"
    r"|vials?)", re.I)
_SCOPE_DEVICE_DOC_RE = re.compile(
    r"(\bMDR\b|medical device report|device history record|device master record|finished devices?\b"
    r"|user facility|21 CFR 820|\b820\.\d|design (input|output|history file)|marketed device)", re.I)
_SCOPE_FOOD_DOC_RE = re.compile(
    r"(food[- ]contact|\bfoods?\b|animal food|low[- ]acid canned|infant formula|\bHACCP\b|\bjuice\b"
    r"|seafood|ice cream|\bcheese\b|\bmilk\b)", re.I)
_SCOPE_FOOD_FIRM_RE = re.compile(
    r"(creamer|creamery|dairy|\bfoods?\b|\bfarms?\b|orchard|bakery|baking|tortiller|produce|beverage"
    r"|brewing|nestle|juice)", re.I)

# (raw_signal, finding) -> scope_status 문자열. 실패하면 예외를 던진다(호출측이 근사로 폴백).
ScopeFn = Callable[[dict[str, Any], dict[str, Any]], str]


def _raw_json(raw_signal: dict[str, Any]) -> dict[str, Any]:
    return findings_extractors._json_object(raw_signal.get("raw_json"))


def _scope_inputs(raw_signal: dict[str, Any], finding: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """DB 트리거(grm_findings_scope_status_trigger)가 분류 함수에 넘기는 인자를 같은 규칙으로 만든다.
    반환 (함수 이름 | "", 인자). 이름이 비면 트리거가 분기하지 않는 출처(scope_status 기본 'ok')."""
    source = str(finding.get("source") or raw_signal.get("source") or "")
    text = str(finding.get("finding_text") or "")
    firm = str(finding.get("firm_name") or "")
    raw = _raw_json(raw_signal)
    if source == "FDA 483":
        observations = raw.get("fda_483_observations")
        parts: list[str] = []
        if isinstance(observations, list):
            for obs in observations:
                if isinstance(obs, dict):
                    parts.append(f"{_pg_text(obs.get('deficiency'))} {_pg_text(obs.get('detail'))}")
        doc_text = " ".join(parts) or text
        est = str(raw.get("establishment_type") or "").strip()
        return "grm_classify_483_scope", {
            "p_est_type": est, "p_len": len(text), "p_doc_text": doc_text, "p_firm_name": firm,
        }
    if source == "FDA Warning Letter":
        doc_text = _pg_text(raw.get("wl_body_full")) or _pg_text(raw.get("wl_body_excerpt")) or text
        return "grm_classify_wl_scope", {"p_len": len(text), "p_doc_text": doc_text, "p_firm": firm}
    return "", {}


def _pg_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def approx_scope(raw_signal: dict[str, Any], finding: dict[str, Any]) -> str:
    """로컬 근사. FDA 483 만 grm_classify_483_scope 를 옮겨 계산하고, WL 은 근사가 없어 'unknown'."""
    fn, args = _scope_inputs(raw_signal, finding)
    if not fn:
        return "ok"
    if fn != "grm_classify_483_scope":
        return "unknown"
    est, length = args["p_est_type"], args["p_len"]
    doc, firm = args["p_doc_text"], args["p_firm_name"]
    frag_or_ok = "fragment" if length < 30 else "ok"
    if _SCOPE_PHARMA_EST_RE.search(est):
        return frag_or_ok
    if _SCOPE_NON_PHARMA_EST_RE.search(est):
        return "non_pharma"
    if _SCOPE_OTHER_PHARMA_EST_RE.search(est):
        return frag_or_ok
    stripped = _SCOPE_LAW_NAME_RE.sub(" ", doc)
    if (not _SCOPE_PHARMA_FIRM_RE.search(firm) and not _SCOPE_DRUG_DOC_RE.search(stripped)
            and (_SCOPE_DEVICE_DOC_RE.search(stripped) or _SCOPE_FOOD_DOC_RE.search(stripped)
                 or _SCOPE_FOOD_FIRM_RE.search(firm))):
        return "non_pharma"
    return frag_or_ok


def make_db_scope_fn(
    base_url: str,
    service_key: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> ScopeFn:
    """DB 분류 함수를 PostgREST RPC 로 부르는 ScopeFn. 두 함수 모두 `language sql immutable` 의 select
    한 줄이라 읽기 전용이다(findings/raw_signals 에 쓰지 않는다). 오류 문구엔 HTTP 상태만 싣는다."""

    def _scope(raw_signal: dict[str, Any], finding: dict[str, Any]) -> str:
        fn, args = _scope_inputs(raw_signal, finding)
        if not fn:
            return "ok"
        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(f"{base_url}/rest/v1/rpc/{fn}", headers=headers, json=args, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"rpc {fn} failed ({type(exc).__name__})") from None
        if resp.status_code >= 400:
            raise RuntimeError(f"rpc {fn} failed (http_{resp.status_code})")
        try:
            value = resp.json()
        except ValueError:
            raise RuntimeError(f"rpc {fn} failed (bad_json)") from None
        if not isinstance(value, str):
            raise RuntimeError(f"rpc {fn} failed (unexpected_shape)")
        return value

    return _scope


def fetch_document_findings(
    base_url: str,
    service_key: str,
    raw_signal_ids: list[str],
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """지목한 raw_signal 들의 현재 findings 행(SELECT 만)."""
    base = _normalize_base_url(base_url)
    if base is None:
        raise ValueError("findings_supabase_backfill: SUPABASE_URL must start with https://")
    ids = sorted({i for i in raw_signal_ids if i})
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), _REPORT_FINDINGS_ID_CHUNK):
        chunk = ids[start:start + _REPORT_FINDINGS_ID_CHUNK]
        rows.extend(_fetch_all_pages(
            base, service_key, "findings",
            select="finding_id,raw_signal_id,source,finding_text,scope_status,review_status",
            page_size=page_size, order="finding_id.asc",
            extra_params={"raw_signal_id": "in.(" + ",".join(chunk) + ")"},
        ))
    return rows


def noise_reason(text: str, source: str) -> str:
    """483 잡문 판정 -- 이유 문자열(잡문 아님이면 ""). 추출기의 판정 함수를 재사용한다
    (주석란 `_fda483_is_annotation` · 조각 표제 `_fda483_is_fragment_title`). 483 이외 출처는 판정하지 않는다."""
    if source != "FDA 483":
        return ""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return "빈 표제"
    if _FORM_NOTICE_TITLE_RE.search(t):
        return "서식 안내문"
    if findings_extractors._fda483_is_annotation(t):
        return "주석란 잡문"
    if findings_extractors._fda483_is_fragment_title(t):
        return "표제 조각"
    if len(t) < _REPORT_SHORT_TITLE:
        return "표제 30자 미만"
    return ""


def _md5(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def _body_marker(raw_signal: dict[str, Any]) -> str:
    body = str(_raw_json(raw_signal).get("fda483_body_full") or "")
    if not body:
        return "본문 없음"
    return "있음" if _OBSERVATION_MARKER_RE.search(body) else "없음"


def build_document_diffs(
    document_ids: list[str],
    raw_signals: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    *,
    scope_fn: ScopeFn | None,
) -> list[dict[str, Any]]:
    """문서별 차이(순수 함수 -- 네트워크 없음, scope_fn 만 주입). scope_fn 이 None 이거나 실패하면 근사."""
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for rs in raw_signals:
        by_doc.setdefault(str(rs.get("document_id") or ""), []).append(rs)
    existing_by_raw: dict[str, list[dict[str, Any]]] = {}
    for row in existing_rows:
        existing_by_raw.setdefault(str(row.get("raw_signal_id") or ""), []).append(row)

    diffs: list[dict[str, Any]] = []
    for doc in document_ids:
        signals = by_doc.get(doc) or []
        if not signals:
            diffs.append({"document_id": doc, "status": STATUS_NOT_FOUND, "raw_signal_id": "",
                          "source": "", "firm_name": "", "body_marker": "", "existing": [], "extracted": []})
            continue
        for rs in signals:
            raw_id = str(rs.get("raw_signal_id") or "")
            source = str(rs.get("source") or "")
            existing = []
            for row in sorted(existing_by_raw.get(raw_id, []), key=lambda r: str(r.get("finding_id") or "")):
                text = str(row.get("finding_text") or "")
                noise = noise_reason(text, str(row.get("source") or source))
                visible = (str(row.get("scope_status") or "") == "ok"
                           and str(row.get("review_status") or "") != "rejected")
                existing.append({
                    "finding_id": str(row.get("finding_id") or ""),
                    "finding_text": text,
                    "scope_status": str(row.get("scope_status") or ""),
                    "review_status": str(row.get("review_status") or ""),
                    "noise": noise,
                    "needs_hide": bool(noise) and visible,
                })
            existing_ids = {e["finding_id"] for e in existing}
            existing_md5 = {_md5(e["finding_text"]): e["finding_id"] for e in existing}

            extracted = []
            batch_md5: dict[str, str] = {}
            for finding in findings_extractors.findings_from_raw_signal(rs):
                fid = str(finding.get("finding_id") or "")
                text = str(finding.get("finding_text") or "")
                digest = _md5(text)
                clash = ""
                if fid in existing_ids:
                    action = ACTION_SKIP_EXISTING
                elif digest in existing_md5:
                    action, clash = ACTION_CONFLICT_EXISTING, existing_md5[digest]
                elif digest in batch_md5:
                    action, clash = ACTION_CONFLICT_BATCH, batch_md5[digest]
                else:
                    action = ACTION_NEW
                if action != ACTION_SKIP_EXISTING:
                    batch_md5.setdefault(digest, fid)
                scope, approx = "", False
                if action == ACTION_NEW:
                    scope, approx = _preview_scope(scope_fn, rs, finding)
                extracted.append({
                    "finding_id": fid,
                    "finding_text": text,
                    "action": action,
                    "conflicts_with": clash,
                    "scope_preview": scope,
                    "scope_approx": approx,
                    "noise": noise_reason(text, source),
                })

            substantive_existing = [e for e in existing if not e["noise"]]
            substantive_extracted = [x for x in extracted if not x["noise"]]
            if not substantive_existing and not substantive_extracted:
                status = STATUS_NO_OBSERVATION
            elif any(x["action"] in (ACTION_CONFLICT_EXISTING, ACTION_CONFLICT_BATCH) for x in extracted):
                status = STATUS_CONFLICT
            elif any(x["action"] == ACTION_NEW for x in extracted):
                status = STATUS_NEW_ROWS
            else:
                status = STATUS_ALL_PRESENT
            diffs.append({
                "document_id": doc, "status": status, "raw_signal_id": raw_id, "source": source,
                "firm_name": str(rs.get("firm_name") or ""),
                "body_marker": _body_marker(rs) if source == "FDA 483" else "",
                "existing": existing, "extracted": extracted,
            })
    return diffs


def _preview_scope(scope_fn: ScopeFn | None, raw_signal: dict[str, Any], finding: dict[str, Any]) -> tuple[str, bool]:
    if scope_fn is not None:
        try:
            return scope_fn(raw_signal, finding), False
        except Exception:  # noqa: BLE001 -- 미리보기 실패는 근사로 폴백(반영 경로와 무관)
            pass
    return approx_scope(raw_signal, finding), True


def _cell(text: Any) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip().replace("|", "\\|")
    if len(t) > _REPORT_TITLE_HEAD:
        t = t[:_REPORT_TITLE_HEAD].rstrip() + "…"
    return t


_STATUS_NOTE = {
    STATUS_NEW_ROWS: "새로 들어갈 행이 있다.",
    STATUS_ALL_PRESENT: "현행 추출 결과가 이미 모두 DB 에 있다.",
    STATUS_NO_OBSERVATION: "재추출할 관찰이 없고 기존 행도 잡문뿐이다 — 원문/PDF 자체에 관찰이 없다. "
                           "기존 잡문 행은 숨김 후보(별도 조치).",
    STATUS_CONFLICT: "유니크 인덱스 (raw_signal_id, md5(finding_text)) 충돌이 예상되는 행이 있다 — 그 행은 "
                     "append 에서 409 → 중복으로 세어 안 들어간다.",
    STATUS_NOT_FOUND: "이 document_id 의 raw_signal 이 없다(또는 --limit 로 빠졌다).",
}


def render_document_report(diffs: list[dict[str, Any]], *, dry_run: bool, scope_label: str) -> str:
    lines = [
        "# M12 문서별 차이 보고서 (`--report`)",
        "",
        f"- 모드: {'dry-run — 쓰기 없음' if dry_run else '반영 — 이 보고서는 append 직전 상태, 반영은 append-only'}",
        f"- scope 미리보기: {scope_label} ('근사' 표시 행은 로컬 근사)",
        "- 이 도구는 기존 행을 지우거나 숨기지 않는다. '숨김 필요' 는 별도 조치 대상 표시다.",
        "",
        "## 요약",
        "",
        "| document_id | 상태 | 기존 행 | 새로 뽑은 행 | 새로 생김 | 건너뜀 | 충돌 예측 | 숨김 필요 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in diffs:
        ex = d["extracted"]
        lines.append("| {doc} | `{st}` | {ne} | {nx} | {new} | {skip} | {cf} | {hide} |".format(
            doc=_cell(d["document_id"]), st=d["status"], ne=len(d["existing"]), nx=len(ex),
            new=sum(1 for x in ex if x["action"] == ACTION_NEW),
            skip=sum(1 for x in ex if x["action"] == ACTION_SKIP_EXISTING),
            cf=sum(1 for x in ex if x["action"] in (ACTION_CONFLICT_EXISTING, ACTION_CONFLICT_BATCH)),
            hide=sum(1 for e in d["existing"] if e["needs_hide"]),
        ))
    for d in diffs:
        lines += ["", f"## {_cell(d['document_id'])} — `{d['status']}`", "", f"- {_STATUS_NOTE[d['status']]}"]
        if d["status"] == STATUS_NOT_FOUND:
            continue
        lines.append(f"- raw_signal_id `{d['raw_signal_id']}` · 출처 {_cell(d['source'])} · 업체 {_cell(d['firm_name'])}")
        if d["body_marker"]:
            lines.append(f"- 본문(fda483_body_full) 'OBSERVATION n' 표지: {d['body_marker']}")
        lines += ["", f"### 현재 DB 행 ({len(d['existing'])})", ""]
        if d["existing"]:
            lines += ["| finding_id | 표제 앞부분 | scope_status | review_status | 잡문 판정 | 조치 |",
                      "|---|---|---|---|---|---|"]
            for e in d["existing"]:
                if e["needs_hide"]:
                    act = "숨김 필요(별도 조치, 이 도구는 건드리지 않음)"
                elif e["noise"]:
                    act = "이미 비공개"
                else:
                    act = ""
                lines.append(f"| `{e['finding_id']}` | {_cell(e['finding_text'])} | {_cell(e['scope_status'])} "
                             f"| {_cell(e['review_status'])} | {e['noise']} | {act} |")
        else:
            lines.append("(없음)")
        lines += ["", f"### 현행 추출기로 뽑은 행 ({len(d['extracted'])})", ""]
        if d["extracted"]:
            lines += ["| finding_id | 표제 앞부분 | 처리 예측 | scope 미리보기 | 잡문 판정 |",
                      "|---|---|---|---|---|"]
            for x in d["extracted"]:
                action = _ACTION_LABEL[x["action"]]
                if x["conflicts_with"]:
                    action += f" ↔ `{x['conflicts_with']}`"
                scope = x["scope_preview"] + (" (근사)" if x["scope_approx"] else "") if x["scope_preview"] else "—"
                lines.append(f"| `{x['finding_id']}` | {_cell(x['finding_text'])} | {action} | {scope} | {x['noise']} |")
        else:
            lines.append("(없음)")
    return "\n".join(lines) + "\n"


def _write_document_report(
    base: str,
    service_key: str,
    report_path: str,
    *,
    document_ids: list[str],
    raw_signals: list[dict[str, Any]],
    dry_run: bool,
    report_scope: str,
    scope_fn: ScopeFn | None,
) -> None:
    """보고서를 만들어 쓴다. 실패해도 예외를 밖으로 내지 않는다(반영 경로·stdout·exit code 불변)."""
    try:
        if scope_fn is not None:
            scope_label = "주입된 분류 함수"
        elif report_scope == "db":
            scope_fn = make_db_scope_fn(base, service_key)
            scope_label = "DB 함수 grm_classify_483_scope / grm_classify_wl_scope (RPC, 읽기 전용)"
        else:
            scope_label = "로컬 근사(grm_classify_483_scope 를 옮긴 것)"
        existing = fetch_document_findings(base, service_key, [str(rs.get("raw_signal_id") or "") for rs in raw_signals])
        diffs = build_document_diffs(document_ids, raw_signals, existing, scope_fn=scope_fn)
        text = render_document_report(diffs, dry_run=dry_run, scope_label=scope_label)
        parent = os.path.dirname(os.path.abspath(report_path))
        os.makedirs(parent, exist_ok=True)
        with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"findings_supabase_backfill: report written ({len(diffs)} documents) -> {report_path}",
              file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 -- 보고서 실패가 반영을 막지 않는다
        print(f"findings_supabase_backfill: report failed ({type(exc).__name__})", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="FIND-1 M12 -- backfill findings for raw_signals already in Supabase "
        "that never got findings extracted/inserted (no LLM, no git writes; READ + "
        "findings POST only)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read and plan only -- never POST any findings.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N unbackfilled raw_signals (default: all).",
    )
    parser.add_argument(
        "--document-ids", default="",
        help="Only these document_ids (space/comma separated), even if they already have "
        "findings -- missing findings are appended, existing ones count as duplicates.",
    )
    parser.add_argument(
        "--report", default=None,
        help="With --document-ids: write a per-document diff report (markdown) to this path "
        "(existing rows / re-extracted rows / new-skip-conflict / scope preview / hide candidates). "
        "Read-only; combine with --dry-run for no writes at all. Omit for the unchanged default.",
    )
    parser.add_argument(
        "--report-scope", choices=("db", "approx"), default="db",
        help="Scope preview source for --report: DB classify RPC (default, read-only) or local approximation.",
    )
    parser.add_argument(
        "--supabase-url",
        help="Supabase project URL (falls back to $SUPABASE_URL)",
    )
    parser.add_argument(
        "--service-role-key",
        help="Supabase service-role key (falls back to $SUPABASE_SERVICE_ROLE_KEY)",
    )
    args = parser.parse_args(argv)
    if args.report and not [x for x in re.split(r"[,\s]+", args.document_ids or "") if x]:
        parser.error("--report requires --document-ids")

    creds = _resolve_credentials(args)
    if creds is None:
        print(
            "findings_supabase_backfill: --supabase-url/--service-role-key or "
            "$SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY are required",
            file=sys.stderr,
        )
        return 2
    base_url, service_key = creds

    ids = [x for x in re.split(r"[,\s]+", args.document_ids or "") if x]
    extra: dict[str, Any] = {}
    if args.report:
        extra = {"report_path": args.report, "report_scope": args.report_scope}
    report = run_backfill(base_url, service_key, dry_run=args.dry_run, limit=args.limit,
                          document_ids=ids or None, **extra)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2))

    # Findings-level errors are intentionally NOT a failure exit -- appends
    # are idempotent (a duplicate 409 is not an error) and nothing here is
    # destructive, so a follow-up run naturally retries anything transient.
    # The report is the source of truth for operators; CI is not gated on it.
    return 0


__all__ = [
    "BackfillReport",
    "fetch_raw_signals",
    "fetch_existing_finding_raw_ids",
    "select_unbackfilled",
    "plan_backfill",
    "run_backfill",
    "approx_scope",
    "make_db_scope_fn",
    "fetch_document_findings",
    "noise_reason",
    "build_document_diffs",
    "render_document_report",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
