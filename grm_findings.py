#!/usr/bin/env python3
"""FIND-1 M1 schema contract for GRM Findings intelligence.

This module intentionally does not run backfill or write to Supabase.  It freezes
the deterministic record contract that later exporter/backfill/dual-write steps
must satisfy.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


RAW_SIGNAL_SCHEMA_VERSION = "grm-raw-signal/v1"
FINDING_SCHEMA_VERSION = "grm-finding/v1"
# grm-finding-taxonomy/v2 change log (keep in sync with docs/specs FIND1_M1 §분류기 한계):
#   1) matching engine: ASCII keywords now use case-insensitive \b word-boundary regex
#      (with a simple trailing `s?` for plurals) instead of raw substring matching;
#      Korean keywords keep substring matching (no word-boundary concept in Hangul).
#   2) three categories had overly broad keywords narrowed to reduce false positives:
#      documentation_records, deviation_capa, qc_lab_controls (see FINDING_TAXONOMY below).
#   3) v1-tagged records already on disk remain valid; TAXONOMY_VERSIONS accepts both.
TAXONOMY_VERSION = "grm-finding-taxonomy/v2"
TAXONOMY_VERSIONS: tuple[str, ...] = ("grm-finding-taxonomy/v1", "grm-finding-taxonomy/v2")

RAW_SIGNAL_REQUIRED_FIELDS = (
    "schema_version",
    "raw_signal_id",
    "source",
    "source_kind",
    "document_id",
    "published_date",
    "title",
    "raw_sha256",
    "raw_json",
    "row_json",
    "extraction_status",
)

FINDING_REQUIRED_FIELDS = (
    "schema_version",
    "taxonomy_version",
    "finding_id",
    "raw_signal_id",
    "source",
    "agency",
    "document_type",
    "document_id",
    "published_date",
    "firm_name",
    "category_code",
    "finding_text",
    "evidence_level",
    "evidence_url",
    "extraction_method",
    "review_status",
)

EVIDENCE_LEVELS = ("A", "B", "C")
EXTRACTION_METHODS = ("deterministic", "llm_assisted", "manual")
REVIEW_STATUSES = ("accepted", "needs_review", "rejected")


@dataclass(frozen=True)
class FindingCategory:
    code: str
    label_ko: str
    label_en: str
    keywords: tuple[str, ...]


FINDING_TAXONOMY: tuple[FindingCategory, ...] = (
    FindingCategory(
        "data_integrity",
        "데이터 완전성",
        "Data integrity",
        ("data integrity", "audit trail", "electronic record", "데이터 완전성", "감사추적"),
    ),
    FindingCategory(
        "documentation_records",
        "문서화/기록관리",
        "Documentation and records",
        (
            "batch record",
            "written procedure",
            "documentation practice",
            "recordkeeping",
            "record retention",
            "제조기록",
            "기록서",
            "문서관리",
            "기록관리",
        ),
    ),
    FindingCategory(
        "aseptic_sterility_assurance",
        "무균보증/무균공정",
        "Aseptic processing and sterility assurance",
        ("aseptic", "sterility", "sterile", "media fill", "무균", "배지충전", "주사제"),
    ),
    FindingCategory(
        "environmental_monitoring",
        "환경모니터링",
        "Environmental monitoring",
        ("environmental monitoring", "cleanroom", "청정도", "환경모니터링", "환경 모니터링"),
    ),
    FindingCategory(
        "cleaning_validation",
        "세척밸리데이션",
        "Cleaning validation",
        ("cleaning validation", "cleaning", "residue", "세척", "잔류"),
    ),
    FindingCategory(
        "deviation_capa",
        "일탈/CAPA/조사",
        "Deviation, CAPA, and investigation",
        (
            "deviation",
            "capa",
            "investigation",
            "unexplained discrepancy",
            "일탈",
            "원인조사",
            "일탈조사",
            "시정조치",
        ),
    ),
    FindingCategory(
        "quality_unit_oversight",
        "품질부서 관리감독",
        "Quality unit oversight",
        ("quality unit", "quality control unit", "qa", "품질부서", "품질보증"),
    ),
    FindingCategory(
        "qc_lab_controls",
        "시험실/품질관리",
        "Laboratory and QC controls",
        ("laboratory", "quality control", "test method", "시험실", "시험방법", "시험성적", "품질관리"),
    ),
    FindingCategory(
        "process_validation",
        "공정밸리데이션",
        "Process validation",
        ("process validation", "process control", "continued process", "공정밸리데이션", "공정 관리"),
    ),
    FindingCategory(
        "equipment_facility",
        "설비/시설",
        "Equipment and facility",
        ("equipment", "facility", "maintenance", "calibration", "설비", "시설", "교정"),
    ),
    FindingCategory(
        "material_supplier_control",
        "원자재/공급업체 관리",
        "Material and supplier control",
        ("supplier", "component", "material", "raw material", "원자재", "공급업체"),
    ),
    FindingCategory(
        "contamination_control",
        "오염/교차오염 관리",
        "Contamination control",
        ("contamination", "cross-contamination", "bioburden", "오염", "교차오염"),
    ),
    FindingCategory(
        "validation_qualification",
        "밸리데이션/적격성평가",
        "Validation and qualification",
        ("validation", "qualification", "qualified", "밸리데이션", "적격성", "검증"),
    ),
    FindingCategory(
        "complaint_recall",
        "불만/회수",
        "Complaint and recall handling",
        ("complaint", "recall", "field alert", "불만", "회수"),
    ),
    FindingCategory(
        "stability_storage",
        "안정성/보관",
        "Stability and storage",
        ("stability", "storage", "temperature", "humidity", "안정성", "보관", "온도", "습도"),
    ),
    FindingCategory(
        "computer_system_validation",
        "컴퓨터화시스템",
        "Computer system validation",
        ("computer system", "csv", "access control", "backup", "컴퓨터화", "시스템 접근"),
    ),
    FindingCategory(
        "labeling_packaging",
        "표시/포장",
        "Labeling and packaging",
        ("labeling", "packaging", "label", "표시", "포장", "라벨"),
    ),
    FindingCategory(
        "regulatory_reporting",
        "규제보고/변경관리",
        "Regulatory reporting and change control",
        ("change control", "submission", "reporting", "변경관리", "보고", "허가"),
    ),
    FindingCategory(
        "training_personnel",
        "교육/작업자",
        "Training and personnel",
        ("training", "personnel", "operator", "작업자", "교육", "훈련"),
    ),
    FindingCategory(
        "other_quality_system",
        "기타 품질시스템",
        "Other quality system",
        (),
    ),
)

CATEGORY_BY_CODE = {c.code: c for c in FINDING_TAXONOMY}
FINDING_CATEGORY_CODES = tuple(c.code for c in FINDING_TAXONOMY)


def canonical_json(data: Any) -> str:
    """Stable JSON representation for hashing and SQLite text storage."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


_ASCII_KEYWORD_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _ascii_keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Compile (and cache) a case-insensitive word-boundary regex for an ASCII keyword.

    A single word becomes ``\\b{word}s?\\b`` (simple plural allowed).  A multi-word
    phrase keeps internal whitespace flexible (``\\s+``) and also allows a trailing
    ``s?`` so "batch record" matches both "batch record" and "batch records".
    """
    pattern = _ASCII_KEYWORD_PATTERN_CACHE.get(keyword)
    if pattern is None:
        words = keyword.split()
        body = r"\s+".join(re.escape(word) for word in words)
        pattern = re.compile(rf"\b{body}s?\b", re.IGNORECASE)
        _ASCII_KEYWORD_PATTERN_CACHE[keyword] = pattern
    return pattern


def _keyword_matches(haystack: str, keyword: str) -> bool:
    """v2 match rule: ASCII keywords use word-boundary regex; Hangul keywords keep substring."""
    if keyword.isascii():
        return _ascii_keyword_pattern(keyword).search(haystack) is not None
    return keyword.lower() in haystack


def classify_finding_category(text: str) -> str:
    """Deterministic v2 keyword classifier.

    The order of FINDING_TAXONOMY is part of the contract.  It gives highly
    specific categories such as aseptic processing a chance to match before more
    general quality-system buckets.  See the TAXONOMY_VERSION change log above
    for what changed between v1 and v2.
    """
    haystack = _text(text).lower()
    if not haystack:
        return "other_quality_system"
    for category in FINDING_TAXONOMY:
        if category.code == "other_quality_system":
            continue
        if any(_keyword_matches(haystack, keyword) for keyword in category.keywords):
            return category.code
    return "other_quality_system"


def raw_signal_from_row(
    row: dict[str, Any],
    raw: dict[str, Any],
    *,
    collected_at: str = "",
    extraction_status: str = "captured",
) -> dict[str, Any]:
    """Build a deterministic raw_signals record from an Intake/web-card row."""
    source = _text(row.get("source"))
    document_id = _text(row.get("document_id"))
    source_kind = _text(row.get("type_or_class"))
    raw_json = canonical_json(raw or {})
    row_json = canonical_json(row or {})
    raw_signal_id = "rawsig-" + stable_hash({
        "schema_version": RAW_SIGNAL_SCHEMA_VERSION,
        "source": source,
        "document_id": document_id,
    })[:24]
    return {
        "schema_version": RAW_SIGNAL_SCHEMA_VERSION,
        "raw_signal_id": raw_signal_id,
        "source": source,
        "source_kind": source_kind,
        "document_id": document_id,
        "published_date": _text(row.get("date")),
        "collected_at": _text(collected_at),
        "title": _text(row.get("headline")),
        "firm_name": _first_text(row.get("firm"), raw.get("firm"), raw.get("company"), raw.get("manufacturer")),
        "site_name": _first_text(raw.get("manufacturer"), raw.get("company"), raw.get("firm"), row.get("firm")),
        "site_country": _first_text(row.get("site_country"), raw.get("site_country"), raw.get("country"), raw.get("Site Country")),
        "modality": _text(row.get("modality")),
        "source_url": _first_text(row.get("source_url"), row.get("api_query")),
        "official_url": _text(row.get("official_url")),
        "raw_sha256": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
        "raw_json": raw_json,
        "row_json": row_json,
        "extraction_status": _text(extraction_status) or "captured",
    }


def finding_from_raw_signal(
    raw_signal: dict[str, Any],
    *,
    finding_text: str,
    ordinal: int = 1,
    category_code: str = "",
    evidence_level: str = "A",
    evidence_url: str = "",
    extraction_method: str = "deterministic",
    review_status: str = "accepted",
    finding_language: str = "",
    inspector_names: list[str] | None = None,
    cfr_refs: list[str] | None = None,
    mfds_refs: list[str] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Build one grm-finding/v1 record from a validated raw signal."""
    text = _text(finding_text)
    category = category_code or classify_finding_category(text)
    finding_id = "finding-" + stable_hash({
        "schema_version": FINDING_SCHEMA_VERSION,
        "raw_signal_id": raw_signal.get("raw_signal_id"),
        "ordinal": int(ordinal),
        "finding_text": text,
    })[:24]
    return {
        "schema_version": FINDING_SCHEMA_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "finding_id": finding_id,
        "raw_signal_id": _text(raw_signal.get("raw_signal_id")),
        "source": _text(raw_signal.get("source")),
        "agency": agency_from_source(_text(raw_signal.get("source"))),
        "document_type": _text(raw_signal.get("source_kind")),
        "document_id": _text(raw_signal.get("document_id")),
        "published_date": _text(raw_signal.get("published_date")),
        "firm_name": _text(raw_signal.get("firm_name")),
        "entity_id": "",
        "site_name": _text(raw_signal.get("site_name")),
        "site_country": _text(raw_signal.get("site_country")),
        "product_family": _text(raw_signal.get("modality")),
        "modality": _text(raw_signal.get("modality")),
        "category_code": category,
        "category_label_ko": CATEGORY_BY_CODE.get(category, CATEGORY_BY_CODE["other_quality_system"]).label_ko,
        "finding_text": text,
        "finding_language": _text(finding_language),
        "evidence_level": _text(evidence_level),
        "evidence_url": _text(evidence_url) or _text(raw_signal.get("official_url")) or _text(raw_signal.get("source_url")),
        "inspector_names": list(inspector_names or []),
        "cfr_refs": list(cfr_refs or []),
        "mfds_refs": list(mfds_refs or []),
        "extraction_method": _text(extraction_method),
        "confidence": float(confidence),
        "review_status": _text(review_status),
    }


def agency_from_source(source: str) -> str:
    source_norm = source.lower()
    if "fda" in source_norm:
        return "FDA"
    if "mfds" in source_norm:
        return "MFDS"
    if "who" in source_norm:
        return "WHO"
    if "health canada" in source_norm or source == "HC":
        return "HC"
    if "ich" in source_norm:
        return "ICH"
    return source


def validate_raw_signal(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in RAW_SIGNAL_REQUIRED_FIELDS:
        if not _text(record.get(key)):
            errors.append(f"raw_signals.{key} required")
    if record.get("schema_version") != RAW_SIGNAL_SCHEMA_VERSION:
        errors.append("raw_signals.schema_version must be grm-raw-signal/v1")
    for key in ("raw_json", "row_json"):
        try:
            json.loads(_text(record.get(key)))
        except json.JSONDecodeError:
            errors.append(f"raw_signals.{key} must be JSON text")
    if len(_text(record.get("raw_sha256"))) != 64:
        errors.append("raw_signals.raw_sha256 must be a sha256 hex digest")
    return errors


def validate_finding(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in FINDING_REQUIRED_FIELDS:
        if not _text(record.get(key)):
            errors.append(f"findings.{key} required")
    if record.get("schema_version") != FINDING_SCHEMA_VERSION:
        errors.append("findings.schema_version must be grm-finding/v1")
    if record.get("taxonomy_version") not in TAXONOMY_VERSIONS:
        errors.append(
            "findings.taxonomy_version must be one of " + ", ".join(TAXONOMY_VERSIONS)
        )
    if record.get("category_code") not in CATEGORY_BY_CODE:
        errors.append("findings.category_code must be in grm-finding-taxonomy/v1")
    if record.get("evidence_level") not in EVIDENCE_LEVELS:
        errors.append("findings.evidence_level must be A/B/C")
    if record.get("extraction_method") not in EXTRACTION_METHODS:
        errors.append("findings.extraction_method invalid")
    if record.get("review_status") not in REVIEW_STATUSES:
        errors.append("findings.review_status invalid")
    confidence = record.get("confidence", 0)
    if not isinstance(confidence, (int, float)) or not (0 <= float(confidence) <= 1):
        errors.append("findings.confidence must be between 0 and 1")
    for key in ("inspector_names", "cfr_refs", "mfds_refs"):
        if not isinstance(record.get(key), list):
            errors.append(f"findings.{key} must be a list")
    return errors


def sqlite_row(record: dict[str, Any]) -> dict[str, Any]:
    """Convert Python list/dict values to JSON strings for SQLite insertion."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (list, dict)):
            out[key] = canonical_json(value)
        else:
            out[key] = value
    return out


def sqlite_schema_ddl() -> str:
    category_check = ", ".join(f"'{code}'" for code in FINDING_CATEGORY_CODES)
    taxonomy_check = ", ".join(f"'{version}'" for version in TAXONOMY_VERSIONS)
    return f"""
CREATE TABLE IF NOT EXISTS raw_signals (
  schema_version TEXT NOT NULL CHECK (schema_version = '{RAW_SIGNAL_SCHEMA_VERSION}'),
  raw_signal_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  document_id TEXT NOT NULL,
  published_date TEXT NOT NULL,
  collected_at TEXT,
  title TEXT NOT NULL,
  firm_name TEXT,
  site_name TEXT,
  site_country TEXT,
  modality TEXT,
  source_url TEXT,
  official_url TEXT,
  raw_sha256 TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  row_json TEXT NOT NULL,
  extraction_status TEXT NOT NULL,
  UNIQUE (source, document_id)
);

CREATE TABLE IF NOT EXISTS findings (
  schema_version TEXT NOT NULL CHECK (schema_version = '{FINDING_SCHEMA_VERSION}'),
  taxonomy_version TEXT NOT NULL CHECK (taxonomy_version IN ({taxonomy_check})),
  finding_id TEXT PRIMARY KEY,
  raw_signal_id TEXT NOT NULL REFERENCES raw_signals(raw_signal_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  agency TEXT NOT NULL,
  document_type TEXT NOT NULL,
  document_id TEXT NOT NULL,
  published_date TEXT NOT NULL,
  firm_name TEXT NOT NULL,
  entity_id TEXT,
  site_name TEXT,
  site_country TEXT,
  product_family TEXT,
  modality TEXT,
  category_code TEXT NOT NULL CHECK (category_code IN ({category_check})),
  category_label_ko TEXT NOT NULL,
  finding_text TEXT NOT NULL,
  finding_language TEXT,
  evidence_level TEXT NOT NULL CHECK (evidence_level IN ('A', 'B', 'C')),
  evidence_url TEXT NOT NULL,
  inspector_names TEXT NOT NULL DEFAULT '[]',
  cfr_refs TEXT NOT NULL DEFAULT '[]',
  mfds_refs TEXT NOT NULL DEFAULT '[]',
  extraction_method TEXT NOT NULL CHECK (extraction_method IN ('deterministic', 'llm_assisted', 'manual')),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL CHECK (review_status IN ('accepted', 'needs_review', 'rejected')),
  UNIQUE (raw_signal_id, finding_text)
);

CREATE INDEX IF NOT EXISTS idx_findings_facets
  ON findings (agency, category_code, modality, published_date);
CREATE INDEX IF NOT EXISTS idx_findings_firm
  ON findings (firm_name, published_date);
"""
