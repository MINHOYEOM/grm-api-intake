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
#
# grm-finding-taxonomy/v3 change log (2026-07-12 층화 감사 -- 실질 정확도 71%, wrong 25%,
# archive/findings_classification_audit_2026-07-12.md 전문 참조):
#   1) mechanism: FindingCategory gained an optional `patterns` field -- explicit regex
#      strings (compiled case-insensitively) checked alongside `keywords` for a category.
#      This lets a category express negative lookbehind / flexible-gap / alternation rules
#      that the plain word-boundary keyword engine cannot.
#   2) documentation_records: dropped the catch-all "written procedure" keyword (36% of the
#      25 wrong cases -- this category's own strict accuracy was 0%). Added a relaxed
#      `\bbatch\b.{0,40}\brecords?\b` pattern plus "master production and control record" /
#      "master production record" keywords for the CFR phrasing that "batch record" alone
#      could not reach.
#   3) computer_system_validation: replaced the rigid "computer system" keyword with two
#      patterns (`computer(ized)?s? (or related )?systems?` and `electronic data`) and moved
#      the category ahead of documentation_records (and hence also ahead of
#      process_validation/training_personnel) in match order. 컨트롤 타워 판정(648323af
#      충돌 해소): 21 CFR 211.68(b)는 본질적으로 컴퓨터화시스템 통제 조항이고 "master
#      production and control records"는 그 통제로 변경이 방지되어야 할 '대상'일 뿐이다 --
#      컴퓨터/전자데이터 신호가 있는 텍스트에서 기록 키워드는 항상 부차적이므로 CSV 우선.
#      data_integrity 만 여전히 CSV 보다 앞이다(electronic record 신호 선점).
#   4) aseptic_sterility_assurance: replaced the bare "sterile"/"sterility" keywords with a
#      pattern covering sterile/sterility/sterilized/sterilization/sterilizing while
#      excluding "non-sterile"/"non sterile" (negative lookbehind); added a "pyrogen(ic)"
#      pattern (21 CFR 211.94).
#   5) cleaning_validation: dropped the bare "cleaning" keyword (this category's strict
#      accuracy was 0%, both wrong-sample hits were equipment-design text with an incidental
#      trailing "cleaning" mention); added "cleaning procedure" keyword plus
#      residue(s)/carry-over patterns.
#   6) complaint_recall moved ahead of deviation_capa/contamination_control in match order
#      so "field alert"/"complaint" text is no longer intercepted by the more general
#      "investigation"/"contamination" keywords.
#   7) equipment_facility: added a "building" keyword; replaced the bare "facility" keyword
#      with a pattern that excludes "outsourcing facility" (a company-type name, not a
#      finding about physical facilities).
#   8) process_validation: added a "process parameter" keyword (redacted-text CFR variant).
#   9) v1/v2-tagged records already on disk remain valid; TAXONOMY_VERSIONS now accepts all
#      three. Category codes/labels/count (20) are unchanged -- only keyword/pattern/order
#      tuning within the existing categories.
#
# grm-finding-taxonomy/v4 change log (2026-07-12 v3 사후 재감사 -- 실질 정확도 89%, wrong 9건/
# unclassifiable 2건, archive/findings_classification_audit_v3_2026-07-12.md 전문 참조).
# No category is added, removed, relabeled, or reordered in v4 -- every change below is an
# additive keyword/pattern within an existing category. §5 후보1/2/3 그대로:
#   1) OCR 오탈자 내성 (표본에서 실제 확인된 2개 혼동쌍 한정 -- 광범위 퍼지매칭 아님):
#      - quality_unit_oversight: `\bqua[lJ1i]+ty\s+units?\b` 패턴 추가(소문자 l이 대문자 J/숫자
#        1로 오인식되는 "quaJity unit" 류; audit case 0a1df74a).
#      - aseptic_sterility_assurance: `sterih\b` 패턴 추가(선행 \b 없음 -- 실측 원문이 공백
#        탈락으로 "of sterile"이 "ofsterih"로 붙어버려 앞쪽 단어경계가 존재하지 않는다; "sterile"
#        의 "le" 가 OCR 로 "h" 하나로 뭉개진 확인된 손상 케이스, audit case 1b836c8a). 두 패턴 다
#        기존 non-sterile 부정어 lookbehind 를 유지한다.
#   2) 캐치올(other_quality_system)로 떨어지는 CFR 조항 어휘 공백 보강:
#      - quality_unit_oversight: "annual product review" 키워드 + `\bannual\w*\b.{0,40}\b
#        (?:review|evaluation)\b` 패턴(211.180(e) 연차제품검토, audit case 5ab99207 방향 --
#        단 그 실제 원문은 "annually"가 "aimually"로 추가 OCR 손상돼 있어 이 패턴으로도 이 특정
#        사례는 안 잡힌다; known_limitation 유지, 아래 v4 픽스처 참조).
#      - qc_lab_controls: "reserve sample"/"evidence of deterioration" 키워드 +
#        `\breserve\s+samples?\b` 패턴(211.170 보류샘플, audit case 8327eaeb -- 실측 확인).
#      - aseptic_sterility_assurance: `\bsmoke\s+stud(?:y|ies)\b` 패턴(무균공정 스모크스터디,
#        audit case d3f0bcd1 -- 실측 확인).
#   3) material_supplier_control 어휘 확장 + CPV 어순변형:
#      - material_supplier_control: "drug substance"/"excipient" 키워드 +
#        `\bsampling\s+of\s+(?:drug\s+substances?|components?|excipients?)\b` 패턴(audit case
#        a2d14f0f, 1·2차 공통 미해결이었던 케이스 -- 실측 확인).
#      - process_validation: `\bmanufacturing\s+process(?:es)?\b.{0,60}\b(?:variability|
#        monitor|output|validate)\b` 패턴(211.110(a) CPV 어순변형, audit case 596b2bd4, 1·2차
#        공통 미해결이었던 케이스 -- 실측 확인).
#   4) 결과: wrong 9건 중 7건(1b836c8a, 0a1df74a, 596b2bd4, 5ab99207 -- 부분, 8327eaeb, a2d14f0f,
#      d3f0bcd1)이 후보1~3 규칙으로 재검증됐다 -- 단 5ab99207 은 위 §2 의 추가 OCR 손상 때문에
#      실제로는 여전히 미해결(known_limitation)이다. 나머지 2건(07dc5ab1 "cleaning process**es**
#      ... validated" 어순변형, 8d3ae393 극심한 OCR 손상)은 이번 3개 후보 어디에도 해당하지 않는
#      진짜 스코프 밖이라 손대지 않았다 -- tests/fixtures/taxonomy_v4_audit_wrong9.json 에 정직하게
#      known_limitation=true 로 고정.
#   5) v1/v2/v3-tagged records already on disk remain valid; TAXONOMY_VERSIONS now accepts all
#      four. Category codes/labels/count (20) are unchanged.
TAXONOMY_VERSION = "grm-finding-taxonomy/v4"
TAXONOMY_VERSIONS: tuple[str, ...] = (
    "grm-finding-taxonomy/v1",
    "grm-finding-taxonomy/v2",
    "grm-finding-taxonomy/v3",
    "grm-finding-taxonomy/v4",
)

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
# FIND-1 M6a: optional 국문 해석 필드 계약. schema_version(grm-finding/v1)과
# FINDING_REQUIRED_FIELDS 는 불변 -- 두 필드 모두 additive/optional 이다.
# ""(빈 문자열)는 "허용값"이자 "미번역" 기본 상태를 동시에 의미한다.
TRANSLATION_METHODS = ("", "llm_assisted", "manual")


@dataclass(frozen=True)
class FindingCategory:
    code: str
    label_ko: str
    label_en: str
    keywords: tuple[str, ...]
    # v3: optional explicit regex strings (compiled case-insensitively), checked
    # alongside `keywords`. Lets a category express negative lookbehind / flexible
    # adjacency / alternation that the plain word-boundary keyword engine cannot.
    patterns: tuple[str, ...] = ()


FINDING_TAXONOMY: tuple[FindingCategory, ...] = (
    FindingCategory(
        "data_integrity",
        "데이터 완전성",
        "Data integrity",
        ("data integrity", "audit trail", "electronic record", "데이터 완전성", "감사추적"),
    ),
    # v3 + 컨트롤 타워 판정(648323af 충돌): computer_system_validation 은
    # documentation_records 보다 앞이다 -- 21 CFR 211.68(b)류 텍스트에서
    # "computers or related systems ... master production and control records" 가
    # 나오면 기록은 통제의 '대상'일 뿐이므로 컴퓨터/전자데이터 신호가 우선한다.
    # (audit cases e1c91f60, 648323af, 51a31e62, 5c069f7f -- v2 에서는 "process
    # control"/"personnel"/기록 키워드가 이 CFR 인용을 반복적으로 가로챘다.)
    # data_integrity 만 여전히 이 카테고리보다 앞이다(electronic record 신호 선점).
    FindingCategory(
        "computer_system_validation",
        "컴퓨터화시스템",
        "Computer system validation",
        ("csv", "access control", "backup", "컴퓨터화", "시스템 접근"),
        patterns=(
            r"\bcomputer(?:ized)?s?\s+(?:or\s+related\s+)?systems?\b",
            r"\belectronic\s+data\b",
        ),
    ),
    FindingCategory(
        "documentation_records",
        "문서화/기록관리",
        "Documentation and records",
        (
            "batch record",
            "master production and control record",
            "master production record",
            "documentation practice",
            "recordkeeping",
            "record retention",
            "제조기록",
            "기록서",
            "문서관리",
            "기록관리",
        ),
        patterns=(r"\bbatch\b.{0,40}\brecords?\b",),
    ),
    FindingCategory(
        "aseptic_sterility_assurance",
        "무균보증/무균공정",
        "Aseptic processing and sterility assurance",
        ("aseptic", "media fill", "무균", "배지충전", "주사제"),
        patterns=(
            r"(?<!non-)(?<!non )\bsteril(?:e|ity|iz(?:ed|ation|ing))s?\b",
            # v4: known OCR corruption of "sterile" -- audit case 1b836c8a. The real
            # extracted text drops the space too ("of sterile" -> "ofsterih"), so there is
            # no word-boundary transition before the "s" -- only the trailing \b is used.
            # Deliberately literal (not a fuzzy class) per the "2 confirmed pairs only" scope.
            r"(?<!non-)(?<!non )sterih\b",
            r"\bpyrogen(?:ic)?s?\b",
            # v4: 211.42/211.113 스모크스터디(무균공정 기류 시각화 검증) -- audit case d3f0bcd1.
            r"\bsmoke\s+stud(?:y|ies)\b",
        ),
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
        ("cleaning validation", "cleaning procedure", "세척", "잔류"),
        patterns=(r"\bresidues?\b", r"\bcarry-?over\b"),
    ),
    # v3: complaint_recall moved ahead of deviation_capa/contamination_control so
    # "field alert"/"complaint" text is not intercepted by the more general
    # "investigation"/"contamination" keywords (audit cases 9f360506, dcf1a6bb).
    FindingCategory(
        "complaint_recall",
        "불만/회수",
        "Complaint and recall handling",
        ("complaint", "recall", "field alert", "불만", "회수"),
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
        (
            "quality unit",
            "quality control unit",
            "qa",
            "annual product review",
            "품질부서",
            "품질보증",
        ),
        patterns=(
            # v4: known OCR corruption of "quality" -- audit case 0a1df74a ("quaJity unit",
            # lowercase l misread as uppercase J; also tolerates the digit-1 confusion).
            # Scoped to the "quality unit" phrase (not bare "quality") to stay conservative.
            r"\bqua[lJ1i]+ty\s+units?\b",
            # v4: 211.180(e) 연차제품검토(annual product review) -- audit case 5ab99207
            # direction. Real-world note: the audit's own sample had a *second*, undocumented
            # OCR corruption ("annually" -> "aimually") that this literal pattern cannot
            # reach -- see the v4 change log and tests/fixtures/taxonomy_v4_audit_wrong9.json.
            r"\bannual\w*\b.{0,40}\b(?:review|evaluation)\b",
        ),
    ),
    FindingCategory(
        "qc_lab_controls",
        "시험실/품질관리",
        "Laboratory and QC controls",
        (
            "laboratory",
            "quality control",
            "test method",
            "reserve sample",
            "evidence of deterioration",
            "시험실",
            "시험방법",
            "시험성적",
            "품질관리",
        ),
        # v4: 211.170 보류샘플 프로그램 -- audit case 8327eaeb.
        patterns=(r"\breserve\s+samples?\b",),
    ),
    FindingCategory(
        "process_validation",
        "공정밸리데이션",
        "Process validation",
        (
            "process validation",
            "process control",
            "continued process",
            "process parameter",
            "공정밸리데이션",
            "공정 관리",
        ),
        # v4: 211.110(a) CPV(continued process verification) 어순변형 -- "monitor the
        # output and validate ... manufacturing processes ... variability" 처럼 동사가
        # "manufacturing process(es)"보다 앞서 나오는 문형. audit case 596b2bd4(1·2차 감사
        # 공통 미해결).
        patterns=(
            r"\bmanufacturing\s+process(?:es)?\b.{0,60}\b(?:variability|monitor|output|validate)\b",
        ),
    ),
    FindingCategory(
        "equipment_facility",
        "설비/시설",
        "Equipment and facility",
        ("equipment", "maintenance", "calibration", "building", "설비", "시설", "교정"),
        patterns=(r"(?<!outsourcing )facilit(?:y|ies)",),
    ),
    FindingCategory(
        "material_supplier_control",
        "원자재/공급업체 관리",
        "Material and supplier control",
        (
            "supplier",
            "component",
            "material",
            "raw material",
            "excipient",
            "원자재",
            "공급업체",
        ),
        # v4: "component"/"material" 동의어 부재로 반복 미스매칭됐던 성분 샘플링 조항 --
        # audit case a2d14f0f(1·2차 감사 공통 미해결). 판단: 리포트 §5 후보3은 bare "drug
        # substance" 키워드도 제안했으나, 실측 검증(known_limitation case e2e676d2 -- shelf
        # life 관련 텍스트에 "drug substance (DS)"가 우연히 등장) 결과 bare 키워드는 이 문맥과
        # 무관한 텍스트까지 material_supplier_control 로 끌어당기는 과매칭을 유발함을 확인했다.
        # "sampling of drug substance" 처럼 조항 문맥에 결합된 패턴만으로도 a2d14f0f 는 정확히
        # 해결되므로, 과매칭 위험이 있는 bare "drug substance" 키워드는 추가하지 않는다(보수적
        # 원칙 -- 표본에서 실제 확인된 것만 다룬다).
        patterns=(
            r"\bsampling\s+of\s+(?:drug\s+substances?|components?|excipients?)\b",
        ),
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
        "stability_storage",
        "안정성/보관",
        "Stability and storage",
        ("stability", "storage", "temperature", "humidity", "안정성", "보관", "온도", "습도"),
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


# [Fix A 2026-07-12 — findings evidence_url 품질 계층] `/findings/` 의 "원본 확인" 링크
# (evidence_url)가 개별 문서가 아니라 목록/데이터셋/API 엔드포인트로 가는 사고가 연속 2건
# 발생했다(사고1 PR#213=MFDS 행정처분·회수→data.go.kr 오픈API 데이터셋 안내 페이지, 사고2=
# MFDS GMP실사→목록 CCBBD03, raw 에는 결과 PDF 가 멀쩡히 있었음). 이 분류기가 단일 진실원
# 이다 -- findings_extractors._evidence_url(추출기 우선순위 필터)과 아래 validate_finding
# (최후 방어선) 양쪽이 이 함수를 통해 evidence_url 을 걸러낸다. stdlib re 만 사용(이 모듈은
# stdlib-only 계약).
#   - serviceKey= : 오픈API 인증키 포함 엔드포인트(키 유출 + 사용자 무의미)
#   - apis.data.go.kr : 오픈API 엔드포인트(문서 아님)
#   - data.go.kr .../openapi.do 등 데이터셋 안내 페이지(개별 사건 열람 불가)
EVIDENCE_URL_BLOCKLIST: tuple[tuple[str, str], ...] = (
    (r"[?&]serviceKey=", "api-key-url"),
    (r"^https?://apis\.data\.go\.kr/", "api-endpoint"),
    (r"^https?://(www\.)?data\.go\.kr/", "dataset-page"),
)


def evidence_url_quality_error(url: Any) -> str:
    """부적합 evidence_url 이면 사유 문자열, 적합하면 ''.

    빈 값은 ''(적합)을 반환한다 -- required 필드 검증은 별도(validate_finding)의 몫이라
    이 분류기와 중복시키지 않는다.
    """
    text = _text(url)
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.IGNORECASE):
        return "non-http"
    for pattern, reason in EVIDENCE_URL_BLOCKLIST:
        if re.search(pattern, text, re.IGNORECASE):
            return reason
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


_EXPLICIT_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _explicit_pattern(pattern: str) -> "re.Pattern[str]":
    """Compile (and cache) a v3 category `patterns` regex string, case-insensitive."""
    compiled = _EXPLICIT_PATTERN_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern, re.IGNORECASE)
        _EXPLICIT_PATTERN_CACHE[pattern] = compiled
    return compiled


def _pattern_matches(haystack: str, pattern: str) -> bool:
    return _explicit_pattern(pattern).search(haystack) is not None


def classify_finding_category(text: str) -> str:
    """Deterministic v4 keyword+pattern classifier.

    The order of FINDING_TAXONOMY is part of the contract.  It gives highly
    specific categories such as aseptic processing a chance to match before more
    general quality-system buckets.  A category matches if any of its `keywords`
    (word-boundary regex for ASCII, substring for Hangul) OR any of its explicit
    `patterns` (raw regex, case-insensitive) matches the haystack.  See the
    TAXONOMY_VERSION change log above for what changed between v1/v2/v3/v4.
    """
    haystack = _text(text).lower()
    if not haystack:
        return "other_quality_system"
    for category in FINDING_TAXONOMY:
        if category.code == "other_quality_system":
            continue
        if any(_keyword_matches(haystack, keyword) for keyword in category.keywords):
            return category.code
        if any(_pattern_matches(haystack, pattern) for pattern in category.patterns):
            return category.code
    return "other_quality_system"


# FIND-1 M10a: FDA 483 페이지 넘김 헤더(양식 라벨·값 인터리브) 스크럽. 483 PDF 는 페이지가
# 바뀔 때마다 DISTRICT OFFICE ADDRESS / FEI NUMBER / STREET ADDRESS / TYPE OF ESTABLISHMENT
# INSPECTED 등 표지 라벨-값 블록이 텍스트층에 반복 삽입된다. Observation 이 페이지 경계에 걸치면
# 이 블록이 finding 본문(deficiency) 앞에 접두사로 섞여 들어온다. 기존 _FDA483_FOOTER_RE
# (collect_fda_483.py)는 페이지 **하단** 서명/양식 푸터만 잡고, 이 페이지 **상단** 헤더 라벨은
# 못 잡는다 — 게다가 라이브 실측(VA San Diego Healthcare Systems, doc fda483-193454)에서는
# 푸터 마커(FORM FDA 4/DEPARTMENT OF HEAL/PAGE n OF n)가 OCR 누락으로 아예 없는 헤더 파편이
# 나왔다(푸터 절단이 발동하지 못함). 라벨(전부 대문자 — PDF 양식 고정 문구)이 하나도 없으면
# 산문 오탐을 피하기 위해 입력을 그대로 반환한다(byte 불변). 라벨이 있으면 라벨에서 시작해
# 뒤따르는 "헤더 값 토큰"(다른 라벨·날짜범위·FEI 숫자런·전화/팩스·URL·TO: 인명·미국식 주소·
# 도시/주/우편 + 옵션 힌트값)을 탐욕적으로 반복 소비한 스팬을 제거한다.
_FDA483_HEADER_LABELS: tuple[str, ...] = (
    r"DISTRICT OFFICE ADDRESS(?: AND PHONE NUMBER)?",
    r"DATE\(S\) OF INSPECTION",
    r"FEI NUMBER",
    r"NAME AND TITLE OF INDIVIDUAL TO WHOM REPORT IS(?: ISSUED)?",
    r"FIRM NAME",
    r"STREET ADDRESS",
    r"CITY, STATE AND ZIP CODE",
    r"TYPE OF ESTABLISHMENT INSPECTED",
    r"DEPARTMENT OF HEALTH AND HUMAN SERVICES",
    r"FOOD AND DRUG ADMINISTRATION",
)
_FDA483_LABEL_ALT = "(?:" + "|".join(_FDA483_HEADER_LABELS) + ")"
_FDA483_LABEL_RE = re.compile(_FDA483_LABEL_ALT)  # 대소문자 구분 -- 산문 오탐 방지

_FDA483_DATE_CHAIN = r"\d{1,2}/\d{1,2}/\d{2,4}(?:\s*[-,]\s*\d{1,2}/\d{1,2}/\d{2,4})*"
_FDA483_FEI_RUN = r"\d{5,}"
_FDA483_PHONE = r"(?:Fax:)?\(\d{3}\)\d{3}-\d{4}"
_FDA483_URL = r"(?:Industry Information:\s*)?(?:www\.|https?://)\S+"
# TO: 인명·직함은 반드시 **다음 라벨이 뒤따를 때만** 소비한다 -- `|$` 를 허용하면 TO: 가
# 헤더 파편의 마지막 요소일 때 뒤따르는 관찰 본문 전체를 문서 끝까지 삼켜(잡음 제거가 아니라
# 데이터 손실) deficiency 가 통째로 사라진다. 라벨이 안 따라오면 "TO: ..." 는 남긴다(안전 우선).
_FDA483_TO_NAME = r"TO:\s+.*?(?=" + _FDA483_LABEL_ALT + r")"
_FDA483_STREET_ADDR = (
    r"\d+\s+(?:[A-Z][A-Za-z.]*\s+){1,5}"
    r"(?:Dr|St|Ave|Rd|Blvd|Drive|Street|Avenue|Road|Boulevard|Lane|Ln|Way|Pkwy)\.?\b"
)
_FDA483_CITY_STATE_ZIP = r"[A-Z][A-Za-z .]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?"

# 값 토큰 순서: 더 구체적인 패턴(날짜/전화/URL/TO:/주소)을 FEI_RUN(순수 5자리+ 숫자런)보다
# 앞에 둔다 -- FEI_RUN 이 먼저면 주소의 5자리+ 번지수(예: "12345 Main St")를 통째로 삼켜버릴 수 있다.
_FDA483_BASE_UNITS: tuple[str, ...] = (
    _FDA483_LABEL_ALT,
    _FDA483_DATE_CHAIN,
    _FDA483_PHONE,
    _FDA483_URL,
    _FDA483_TO_NAME,
    _FDA483_STREET_ADDR,
    _FDA483_CITY_STATE_ZIP,
    _FDA483_FEI_RUN,
)

_FDA483_SPAN_PATTERN_CACHE: dict[tuple[str, str, str], "re.Pattern[str]"] = {}


def _fda483_hint_unit(hint: str) -> str:
    """힌트 문자열 -> 문자 사이 \\s* 유연 매칭 유닛(공백 문자는 버리고 나머지를 \\s* 로 이음).

    OCR 이 "Producer of Sterile Drug Products"의 공백을 지우거나("ofSterile") 끼워 넣는
    변형에 대응한다. 힌트가 비어 있으면 ''(unit 리스트에서 제외).
    """
    chars = [c for c in (hint or "") if not c.isspace()]
    if not chars:
        return ""
    return r"\s*".join(re.escape(c) for c in chars)


def _fda483_span_pattern(establishment_type: str, fei_number: str, firm_name: str) -> "re.Pattern[str]":
    key = (establishment_type or "", fei_number or "", firm_name or "")
    pattern = _FDA483_SPAN_PATTERN_CACHE.get(key)
    if pattern is not None:
        return pattern
    units = list(_FDA483_BASE_UNITS)
    for hint in (establishment_type, fei_number, firm_name):
        unit = _fda483_hint_unit(hint)
        if unit:
            units.append(unit)
    unit_alt = "(?:" + "|".join(units) + ")"
    pattern = re.compile(_FDA483_LABEL_ALT + r"(?:\s*" + unit_alt + r")*")
    _FDA483_SPAN_PATTERN_CACHE[key] = pattern
    return pattern


def strip_fda483_page_header(
    text: str,
    *,
    establishment_type: str = "",
    fei_number: str = "",
    firm_name: str = "",
) -> str:
    """FDA 483 페이지 넘김 헤더(양식 라벨-값 인터리브) 제거(순수 함수).

    483 PDF 는 페이지 경계마다 STREET ADDRESS / FEI NUMBER / TYPE OF ESTABLISHMENT INSPECTED
    등 표지 라벨-값 블록이 텍스트층에 반복돼, Observation 이 페이지에 걸치면 이 블록이 finding
    본문 앞에 섞여 들어온다(collect_fda_483._FDA483_FOOTER_RE 는 페이지 **하단** 서명/양식
    푸터만 잡고 이 **상단** 헤더는 못 잡는다 -- 게다가 푸터 마커 자체가 OCR 누락으로 빠진
    헤더-only 파편도 실재한다). 텍스트에 헤더 라벨(전부 대문자 고정 문구)이 하나도 없으면
    산문 오탐을 피해 입력을 그대로(공백 정규화도 없이) 반환한다. 라벨이 있으면 그 지점부터
    날짜범위/FEI 숫자런/전화·팩스/URL/TO: 인명/미국식 주소/도시·주·우편 + (있으면) 힌트값을
    탐욕적으로 반복 소비한 스팬을 제거하고, 공백을 재정규화해 반환한다.
    """
    if not text:
        return text
    normalized = " ".join(text.split())
    if not _FDA483_LABEL_RE.search(normalized):
        return text
    span_re = _fda483_span_pattern(establishment_type, fei_number, firm_name)
    cleaned = span_re.sub(" ", normalized)
    return " ".join(cleaned.split())


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


def _choose_evidence_url(evidence_url: str, raw_signal: dict[str, Any]) -> str:
    """[Fix A 2026-07-12] 근본 원인 #2 수리: raw_signal.official_url/source_url 무조건
    폴백을 품질 필터 통과 후보만 채택으로 교체한다. official_url/source_url 의 의미는
    소스마다 다르다(483/WL=개별 문서라 대개 적합, MFDS 계열=목록/데이터셋/serviceKey API
    엔드포인트라 부적합) -- serviceKey/API 엔드포인트가 조용히 evidence_url 로 승격되는
    경로를 막는다. 전부 부적합이면 ''(침묵 승격 대신 validate_finding required 에러로
    표면화된다).
    """
    candidates = (_text(evidence_url), _text(raw_signal.get("official_url")), _text(raw_signal.get("source_url")))
    return next((c for c in candidates if c and not evidence_url_quality_error(c)), "")


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
    finding_text_ko: str = "",
    translation_method: str = "",
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
        "evidence_url": _choose_evidence_url(evidence_url, raw_signal),
        "inspector_names": list(inspector_names or []),
        "cfr_refs": list(cfr_refs or []),
        "mfds_refs": list(mfds_refs or []),
        "extraction_method": _text(extraction_method),
        "confidence": float(confidence),
        "review_status": _text(review_status),
        "finding_text_ko": _text(finding_text_ko),
        "translation_method": _text(translation_method),
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
    if "eudragmdp" in source_norm or "eu gmp ncr" in source_norm:
        return "EMA"
    if "mhra" in source_norm:
        return "MHRA"
    return source


_FIRM_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|co|corp|corporation|company|limited|lp|llp|pvt|private|gmbh|sa|srl|dba)\b",
    re.IGNORECASE,
)
_FIRM_PUNCT_RE = re.compile(r"[.,]")
_FIRM_WHITESPACE_RE = re.compile(r"\s+")


def normalize_firm_name(name: str) -> str:
    """FIND-1 업체명 정규화(firm_key) — 단일 정의.

    실측(컨트롤 타워, 2026-07): findings.firm_name 982 고유 표기 -> 정규화 시 855 실업체
    (충돌 100그룹, 표본 오병합 0건) — 같은 회사가 구두점/법인접미사 변형(예: "SCA
    Pharmaceuticals" / "SCA Pharmaceuticals, Inc." / "SCA Pharmaceuticals LLC")으로
    흩어져 있던 것을 하나로 묶는다.

    규칙(순서 고정 — web/migrations/013_findings_firm_key.sql 의
    public.grm_normalize_firm_name 이 이 함수의 SQL 복제본이며 반드시 동일 결과를 내야
    한다. 파리티는 tests/test_findings_firm_key.py 로 고정):
      1) HTML 엔티티 복원: `&amp;` -> `&`, `&#039;` -> `'`
      2) lowercase
      3) `[.,]` 제거
      4) 단어경계 법인접미사 제거: inc|llc|ltd|co|corp|corporation|company|limited|lp|llp|
         pvt|private|gmbh|sa|srl|dba (예: "Coherus" 는 안전 -- \\b 가 "co" 를 단어 전체로만
         매치하므로 "coherus" 내부의 "co" 는 매치되지 않는다)
      5) 연속 공백을 1개로 축약 후 trim

    빈 입력(None/공백)은 빈 문자열을 반환한다.
    """
    text = _text(name)
    if not text:
        return ""
    text = text.replace("&amp;", "&").replace("&#039;", "'")
    text = text.lower()
    text = _FIRM_PUNCT_RE.sub("", text)
    text = _FIRM_SUFFIX_RE.sub("", text)
    text = _FIRM_WHITESPACE_RE.sub(" ", text).strip()
    return text


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
    # [Fix A 2026-07-12] 최후 방어선 — 어느 경로로 만들어졌든(추출기/수동/백필) evidence_url
    # 이 목록/API/serviceKey 클래스면 POST 전에 반드시 거부되고 extraction report
    # invalid_errors 로 관측된다. 빈 값은 위 required 루프의 몫이라 여기선 다루지 않는다
    # (evidence_url_quality_error 자체도 빈 값엔 ''을 반환해 중복 에러를 안 만든다).
    evidence_url_reason = evidence_url_quality_error(record.get("evidence_url"))
    if evidence_url_reason:
        errors.append(
            f"findings.evidence_url {evidence_url_reason}: {_text(record.get('evidence_url'))[:80]}"
        )
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
    errors.extend(_validate_translation_fields(record))
    return errors


def _validate_translation_fields(record: dict[str, Any]) -> list[str]:
    """FIND-1 M6a optional 국문 해석 필드 계약.

    Both fields are additive/optional -- a record that has neither key at all
    (a pre-M6a record) passes untouched. Once either key is present, the pair
    must agree: a non-empty finding_text_ko requires a non-empty
    translation_method, and vice versa; translation_method (when present)
    must be one of TRANSLATION_METHODS.
    """
    errors: list[str] = []
    has_ko_key = "finding_text_ko" in record
    has_method_key = "translation_method" in record
    if not has_ko_key and not has_method_key:
        return errors

    ko = _text(record.get("finding_text_ko")) if has_ko_key else ""
    method = _text(record.get("translation_method")) if has_method_key else ""

    if has_method_key and method not in TRANSLATION_METHODS:
        errors.append(
            "findings.translation_method must be one of " + ", ".join(TRANSLATION_METHODS)
        )
    if ko and not method:
        errors.append("findings.translation_method required when finding_text_ko is set")
    if method and not ko:
        errors.append("findings.finding_text_ko required when translation_method is set")
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
    translation_method_check = ", ".join(f"'{method}'" for method in TRANSLATION_METHODS)
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
  finding_text_ko TEXT NOT NULL DEFAULT '',
  translation_method TEXT NOT NULL DEFAULT '' CHECK (translation_method IN ({translation_method_check})),
  UNIQUE (raw_signal_id, finding_text)
);

CREATE INDEX IF NOT EXISTS idx_findings_facets
  ON findings (agency, category_code, modality, published_date);
CREATE INDEX IF NOT EXISTS idx_findings_firm
  ON findings (firm_name, published_date);
"""
