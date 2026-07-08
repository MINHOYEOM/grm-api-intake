#!/usr/bin/env python3
"""FIND-1 M1d pure raw_signal -> grm-finding/v1 extractors.

This layer is intentionally offline and side-effect free.  It does not fetch
documents, write SQLite, or call Notion/Supabase; callers pass an already
captured grm-raw-signal/v1 record and receive deterministic findings.
"""

from __future__ import annotations

import json
import re
from typing import Any

import grm_findings as gf


MFDS_GMP_LIST_URL = "https://nedrug.mfds.go.kr/pbp/CCBBD03/getList?page=1&limit=100"
FDA_483_LIST_URL = (
    "https://www.fda.gov/about-fda/office-inspections-and-investigations/"
    "oii-foia-electronic-reading-room"
)

_CFR_RE = re.compile(r"\b21\s*CFR\s*(?:Part\s*)?\d+(?:\.\d+)?(?:\([a-z0-9]+\))*", re.I)


def findings_from_raw_signal(raw_signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract deterministic v0 findings from one grm-raw-signal/v1 record."""
    findings, _report = findings_from_raw_signal_with_report(raw_signal)
    return findings


def findings_from_raw_signal_with_report(
    raw_signal: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract findings plus a diagnostic report distinguishing "nothing to
    extract" from "extracted but dropped as invalid/duplicate".

    Report keys: extracted (attempts before dedupe/validation), kept,
    dropped_invalid, dropped_duplicate_text, invalid_errors (deduped, sorted
    validate_finding error strings).
    """
    if gf.validate_raw_signal(raw_signal):
        return [], _empty_extraction_report()

    raw = _json_object(raw_signal.get("raw_json"))
    row = _json_object(raw_signal.get("row_json"))
    if not raw:
        return [], _empty_extraction_report()

    signal = _raw_signal_with_firm_fallback(raw_signal, raw, row)
    findings: list[dict[str, Any]] = []
    findings.extend(_from_fda_483_observations(signal, raw, row))
    findings.extend(_from_mfds_gmp(signal, raw, row))
    findings.extend(_from_warning_letter(signal, raw, row))
    findings.extend(_from_whopir(signal, raw, row))
    return _dedupe_valid_findings_with_report(findings)


def _empty_extraction_report() -> dict[str, Any]:
    return {
        "extracted": 0,
        "kept": 0,
        "dropped_invalid": 0,
        "dropped_duplicate_text": 0,
        "invalid_errors": [],
    }


def _from_fda_483_observations(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    observations = _dicts(raw.get("fda_483_observations"))
    if not observations:
        return []

    evidence_url = _evidence_url(raw_signal, raw, "pdf_url", "url", fallback=FDA_483_LIST_URL)
    out: list[dict[str, Any]] = []
    for index, observation in enumerate(observations, start=1):
        deficiency = _compact(observation.get("deficiency"))
        if not deficiency:
            continue
        detail = _compact(observation.get("detail"))
        refs = _extract_cfr_refs(" ".join(part for part in (deficiency, detail) if part))
        out.append(gf.finding_from_raw_signal(
            raw_signal,
            finding_text=deficiency,
            ordinal=_positive_int(observation.get("number"), default=index),
            evidence_level="A",
            evidence_url=evidence_url,
            finding_language=_language(row, "EN"),
            cfr_refs=refs,
            confidence=0.95,
            review_status="accepted",
        ))
    return out


def _from_mfds_gmp(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    table_rows = _dicts(raw.get("gmp_deficiencies"))
    if table_rows:
        return _from_mfds_gmp_table(raw_signal, raw, row, table_rows)

    excerpt = _compact(raw.get("attachment_deficiency_excerpt"))
    if not excerpt or _compact(raw.get("attachment_deficiency_assessment")).lower() == "none":
        return []

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=excerpt,
        ordinal=1,
        evidence_level="B",
        evidence_url=_evidence_url(raw_signal, raw, "source_url", "url", fallback=MFDS_GMP_LIST_URL),
        finding_language=_language(row, "KO"),
        mfds_refs=_extract_mfds_refs(excerpt),
        confidence=0.72,
        review_status="needs_review",
    )]


def _from_mfds_gmp_table(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
    table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_url = _evidence_url(raw_signal, raw, "source_url", "url", fallback=MFDS_GMP_LIST_URL)
    out: list[dict[str, Any]] = []
    for index, item in enumerate(table_rows, start=1):
        text = _gmp_table_text(item)
        if not text:
            continue
        legal_basis = _compact(item.get("legal_basis") or item.get("basis") or item.get("law_ref"))
        refs = [legal_basis] if legal_basis else _extract_mfds_refs(text)
        out.append(gf.finding_from_raw_signal(
            raw_signal,
            finding_text=text,
            ordinal=index,
            category_code=_classify_gmp_summary(_gmp_summary_text(item)),
            evidence_level="A",
            evidence_url=evidence_url,
            finding_language=_language(row, "KO"),
            mfds_refs=refs,
            confidence=0.90,
            review_status="accepted",
        ))
    return out


def _from_warning_letter(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    text = _compact(raw.get("wl_body_excerpt")) or _compact(raw.get("wl_body_full"))
    if not text:
        return []

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=text,
        ordinal=1,
        evidence_level="B",
        evidence_url=_evidence_url(raw_signal, raw, "url", "source_url"),
        finding_language=_language(row, "EN"),
        cfr_refs=_extract_cfr_refs(text),
        confidence=0.72,
        review_status="needs_review",
    )]


def _from_whopir(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    text = _compact(raw.get("whopir_excerpt"))
    if not text:
        return []

    return [gf.finding_from_raw_signal(
        raw_signal,
        finding_text=text,
        ordinal=1,
        evidence_level="B",
        evidence_url=_evidence_url(raw_signal, raw, "pdf_url", "url", "list_page"),
        finding_language=_language(row, "EN"),
        confidence=0.72,
        review_status="needs_review",
    )]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _language(row: dict[str, Any], default: str) -> str:
    return _compact(row.get("language")) or default


def _raw_signal_with_firm_fallback(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    if _compact(raw_signal.get("firm_name")):
        return raw_signal
    signal = dict(raw_signal)
    fallback = (
        _compact(raw.get("firm"))
        or _compact(raw.get("company"))
        or _compact(raw.get("manufacturer"))
        or _compact(raw.get("anchor_text"))
        or _compact(row.get("headline"))
        or _compact(raw_signal.get("title"))
    )
    signal["firm_name"] = fallback
    if not _compact(signal.get("site_name")):
        signal["site_name"] = fallback
    return signal


def _positive_int(value: Any, *, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _evidence_url(
    raw_signal: dict[str, Any],
    raw: dict[str, Any],
    *raw_keys: str,
    fallback: str = "",
) -> str:
    for value in (raw_signal.get("official_url"), raw_signal.get("source_url")):
        text = _compact(value)
        if text:
            return text
    for key in raw_keys:
        text = _compact(raw.get(key))
        if text:
            return text
    return fallback


def _gmp_table_text(item: dict[str, Any]) -> str:
    summary = _gmp_summary_text(item)
    if not summary:
        return ""
    parts = [
        _compact(item.get("area")),
        _compact(item.get("severity")),
        _compact(item.get("legal_basis") or item.get("basis") or item.get("law_ref")),
        summary,
    ]
    return _compact(" ".join(part for part in parts if part))


def _gmp_summary_text(item: dict[str, Any]) -> str:
    return _compact(item.get("summary") or item.get("deficiency") or item.get("finding") or item.get("issue"))


def _classify_gmp_summary(text: str) -> str:
    lowered = _compact(text).lower()
    if any(token in lowered for token in ("cross-contamination", "contamination", "교차오염", "오염")):
        return "contamination_control"
    return gf.classify_finding_category(text)


def _extract_cfr_refs(text: str) -> list[str]:
    seen: set[str] = set()
    refs: list[str] = []
    for match in _CFR_RE.finditer(text or ""):
        ref = re.sub(r"\s+", " ", match.group(0)).strip()
        ref = re.sub(r"(?i)\bcfr\b", "CFR", ref)
        ref = re.sub(r"(?i)\bpart\b", "Part", ref)
        key = ref.lower()
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs


def _extract_mfds_refs(text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[별표\s*\d+(?:의\d+)?\]\s*[^,\.;\s]*(?:\s*[가-힣]목)?", text or ""):
        ref = _compact(match.group(0))
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _dedupe_valid_findings_with_report(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    dropped_invalid = 0
    dropped_duplicate_text = 0
    invalid_errors: set[str] = set()
    for finding in findings:
        key = _compact(finding.get("finding_text")).casefold()
        if not key or key in seen_texts:
            dropped_duplicate_text += 1
            continue
        errors = gf.validate_finding(finding)
        if errors:
            dropped_invalid += 1
            invalid_errors.update(errors)
            continue
        seen_texts.add(key)
        out.append(finding)
    report = {
        "extracted": len(findings),
        "kept": len(out),
        "dropped_invalid": dropped_invalid,
        "dropped_duplicate_text": dropped_duplicate_text,
        "invalid_errors": sorted(invalid_errors),
    }
    return out, report
