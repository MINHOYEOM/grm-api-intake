"""GRM glossary validator for unattended CI and scheduled sessions.

Mirrors quiz_lint.py's shape and conventions on purpose (dependency-free,
deterministic, dataclass-based report, 0/1 exit contract) so both validators
read the same way. It validates the committed glossary bank
(web/data/glossary.json) and its case-link companion
(web/data/glossary_cases.json), and prints a deterministic report to stdout.

Why this exists: web/render.py reads glossary fields with dict.get() and
silently drops anything it doesn't recognise — a misspelled field name
(detail_kr instead of detail_ko), an orphaned `related` id, or a non-http(s)
source_url all render as "this term just doesn't have that extra bit",
never as an error. The site looks fine and the existing tests pass. This
validator is the thing that is supposed to notice instead.

Exit status is 0 for a clean glossary, 1 for every validation or input error.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_GLOSSARY = REPO_ROOT / "web" / "data" / "glossary.json"
DEFAULT_GLOSSARY_CASES = REPO_ROOT / "web" / "data" / "glossary_cases.json"

# Allowed field set = the exact keys web/render.py's build_glossary_view reads
# (_term_view, web/render.py ~L1160-1196), plus `aliases` — committed data (2026-08-04,
# 58 terms / 95 synonyms) that render.py does not consume yet (search-wiring is a
# separate, not-yet-done task). It is validated now so bad values can't get committed
# silently while it waits to be wired in. Anything outside this list is a
# typo waiting to be silently ignored by the renderer.
REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "term_ko": str,
    "term_en": str,
    "easy_ko": str,
    "definition_source": str,
}
OPTIONAL_FIELDS: dict[str, type] = {
    "source_url": str,
    "related": list,
    "detail_ko": str,
    "reg_refs": list,
    "aliases": list,
}
ALLOWED_FIELDS = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)

# id doubles as the /glossary/#<id> anchor and the quiz bank's glossary
# source_ref target (see quiz_lint.py GLOSSARY_REF) — spaces/uppercase/`#`
# break links silently.
_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_ZERO_WIDTH_CHARS = ("​", "‌", "﻿")  # ZWSP, ZWNJ, BOM/ZWNBSP
# 화면에서는 보통 공백과 구분되지 않지만 값이 다른 문자들. 복붙·웹 스크랩·한글 편집기에서
# 섞여 들어오고, 들어오면 검색어 일치와 정렬이 조용히 어긋난다(공백으로 보이는데 안 맞는다).
_INVISIBLE_SPACE_CHARS = {
    " ": "U+00A0 NBSP",
    " ": "U+2007 FIGURE SPACE",
    " ": "U+202F NARROW NBSP",
    "　": "U+3000 전각 공백",
}
_WHITESPACE_RE = re.compile(r"\s")

_SAFE_URL_SCHEMES = ("http://", "https://")

# ── WARNING baselines (2026-08-04 실측, web/data/glossary.json 200개 항목 +
# web/data/glossary_cases.json 기준). 숫자는 전부 이 검증기 개발 세션에서 직접
# 실행해 확인했다 — 추측치 아님. "기준선을 넘으면 ERROR" 는 WARNING 카테고리
# 전체에 적용되는 공통 규칙(과업 지시 원문)이라 W1/W2/W3 모두 같은 방식으로
# 처리한다: 현재 실측치까지는 허용(기존 데이터를 갑자기 FAIL 시키지 않는다),
# 그 이상 늘면 ERROR로 승격(신규 위반은 통과 못 시킨다), 줄어들면 기준선을
# 낮추라는 NOTICE만 출력(통과는 유지).

# W1 — term_ko/term_en 중복 "쌍" 수. 같은 값을 공유하는 항목이 k개면 C(k,2)쌍.
#   실측: 2026-08-04 term_ko 중복 그룹 1개('정확성': accuracy, accurate) → 1쌍.
#         2026-09-02 accurate(ALCOA) 표제어를 '데이터 정확성'으로 갈라 0쌍 — 화면에 같은
#         이름의 카드가 나란히 나와 구분이 불가능했다(유입이 들어오는 페이지). 기준선 0.
#         term_en 중복 그룹 0개 → 0쌍.
BASELINE_TERM_KO_DUP_PAIRS = 0
BASELINE_TERM_EN_DUP_PAIRS = 0

# W2 — detail_ko 템플릿 복제. difflib.SequenceMatcher(autojunk=False).ratio()
#   가 0.85 이상인 서로 다른 두 용어 쌍에 하나 이상 관여하는 고유 id 집합.
#   실측(200*199/2 = 19,900쌍 전수 비교, quick_ratio 사전필터로 가속·결과는
#   필터 없는 전수계산과 동일함을 별도 확인): pairs=509, terms_involved=121.
#   threshold 0.80/0.85/0.90 전부 terms_involved=121로 동일 — 0.80~0.85 구간에
#   걸리는 쌍이 0개, 0.85~0.90 구간에 1개뿐(api-starting-material/batch-number,
#   ratio≈0.884)이라 임계값 선택이 결과를 사실상 바꾸지 않는다.
#
#   ★기준선은 "개수"가 아니라 "id 집합"이다. 개수로 두면 **상쇄가 숨는다** —
#   기존 1건을 고치고 새 1건을 만들면 121로 같아 CI가 통과해버린다. 집합으로 두면
#   새로 들어온 id 는 개수와 무관하게 잡힌다(신규=ERROR, 해소=NOTICE로 축소 안내).
#
#   2026-09-02 121 → 109: 4개 군 12어(accuracy/precision · DQ/IQ/OQ/PQ · sterilization/
#   disinfection/sterility · risk-assessment/-control/-reduction)의 detail_ko 를 정본
#   (ICH Q2(R2)·EU GMP Annex 15·Annex 1·ICH Q9(R1)) 조항으로 다시 써서 집합에서 뺐다
#   (같은 스크립트로 재측정: 복제군 121→109, 85%+ 쌍 509→394). 남은 109개는 여전히 해소
#   대상이며, 이 집합은 줄어들기만 해야 한다.
DETAIL_KO_SIMILARITY_THRESHOLD = 0.85
BASELINE_DETAIL_KO_SIMILAR_IDS = frozenset({
    'acceptance-criteria', 'action-limit', 'active-ingredient', 'airlock', 'alert-limit',
    'analytical-procedure', 'analytical-procedure-validation', 'api',
    'api-starting-material', 'aseptic-processing', 'assay', 'authorized-person', 'batch',
    'batch-number', 'batch-release', 'bioburden', 'biological-indicator', 'bulk-product',
    'calibration', 'change-control', 'change-management', 'cleaning-validation',
    'cleanroom', 'cleanroom-classification', 'continual-improvement',
    'continued-process-verification', 'contract-manufacturer', 'control-strategy',
    'corrective-action', 'depyrogenation', 'detection-limit', 'deviation', 'documentation',
    'drug-product', 'endotoxin', 'expiry-date', 'finished-product', 'hazard', 'hepa-filter',
    'identification-test', 'impurity', 'impurity-profile', 'intermediate',
    'intermediate-product', 'isolator', 'knowledge-management', 'linearity', 'manufacture',
    'manufacturer', 'marketing-authorization', 'oot', 'outsourced-activities', 'packaging',
    'packaging-material', 'pharmaceutical-quality-system', 'preventive-action', 'procedure',
    'product-complaint', 'product-quality-review', 'production', 'purity-test', 'pyrogen',
    'qualification', 'quality', 'quality-assurance', 'quality-control', 'quality-manual',
    'quality-objectives', 'quality-planning', 'quality-policy', 'quality-risk-management',
    'quality-unit', 'quantitation-limit', 'quarantine', 'rabs', 'range', 'raw-material',
    'recall', 'record', 'reference-standard', 'repeatability', 'reprocessing',
    'retest-date', 'revalidation', 'reworking', 'risk', 'risk-acceptance', 'risk-analysis',
    'risk-communication', 'risk-evaluation', 'risk-identification', 'risk-review',
    'robustness', 'root-cause-analysis', 'severity', 'sop', 'specification', 'specificity',
    'stability-testing', 'starting-material', 'state-of-control',
    'sterility-assurance-level', 'supplier-qualification', 'terminal-sterilization',
    'user-requirements-specification', 'validation-master-plan', 'validation-protocol',
    'validation-report', 'worst-case',
})

# W3 — easy_ko/detail_ko 가 공백 제거 후 10자 미만인 발생 건수(값이 있는 경우만
#   ― easy_ko가 아예 비어있으면 필수 필드라 E2 EMPTY_STRING이 이미 잡는다).
#   실측: easy_ko 최소 길이 21자, detail_ko 10자 미만 0건 → 0건.
SHORT_FIELD_MIN_LENGTH = 10
BASELINE_SHORT_FIELD_COUNT = 0

# aliases 0건 가드 — 방향이 위 W1~W3 과 반대다(저건 "늘면 ERROR", 이건 "줄면 ERROR").
# aliases 를 가진 용어가 이 하한 밑으로 떨어지면(필드가 통째로 날아가는 등) ALIAS_SELF/
# ALIAS_DUPLICATE/ALIAS_TERM_COLLISION/ALIAS_ALIAS_COLLISION 은 검사할 원소 자체가
# 없어 전부 조용히 통과해버린다 — 그 상태를 명시적으로 막는다.
# 실측(2026-08-04, python으로 web/data/glossary.json 226개 항목을 직접 순회해 카운트):
#   aliases 가 비어 있지 않은 항목 56개, alias 원소 총합 91개.
#   (처음 실측은 58개/95개였는데, 같은 날 병렬로 진행된 용어 증설(200→226어)이
#    'Quality Control Unit'·'Non-conformance'·'Written Procedures' 를 **정식 표제어로**
#    올리면서 같은 말을 동의어로도 갖고 있던 quality-unit·deviation·sop 에서 4건을
#    뺐다. 표제어와 동의어가 같은 이름을 주장하면 사용자가 틀린 카드를 본다 —
#    표제어가 이긴다.)
#
# 이 하한은 전체 코퍼스(term_count)가 이 하한 이상일 때만 적용한다(아래
# _check_alias_floor) — term_count 자체가 58 미만이면 애초에 58개를 채우는 게
# 구조적으로 불가능하고, 그런 축소 코퍼스는 다른 목적(tests/test_glossary_lint.py
# 의 합성 픽스처 등)일 뿐 aliases 유실의 신호가 아니다. 그 규모 자체의 이상은
# GLOSSARY_EMPTY(0건 가드) 등 별도 검사가 담당한다.
ALIASES_MIN_TERM_COUNT = 56


@dataclass(frozen=True)
class LintIssue:
    code: str
    location: str
    message: str


@dataclass
class LintReport:
    glossary: Path
    glossary_cases: Path
    term_count: int = 0
    cases_item_count: int = 0
    cases_excluded_count: int = 0
    term_ko_dup_pairs: int = 0
    term_en_dup_pairs: int = 0
    detail_ko_similar_terms: int = 0
    short_field_count: int = 0
    alias_term_count: int = 0
    issues: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    # 템플릿 복제 상세 — 기본 출력에서는 요약 한 줄로 접고 --verbose 에서만 전개한다.
    verbose: bool = False
    detail_ko_similar_ids: set[str] = field(default_factory=set)
    detail_ko_similar_pairs: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, code: str, location: str, message: str) -> None:
        self.issues.append(LintIssue(code, location, message))

    def warn(self, code: str, location: str, message: str) -> None:
        self.warnings.append(LintIssue(code, location, message))

    def notice(self, code: str, location: str, message: str) -> None:
        self.notices.append(f"[{code}] {location}: {message}")

    def format(self) -> str:
        lines = [
            f"glossary_lint: {'PASS' if self.ok else 'FAIL'}",
            f"glossary: {self.glossary}",
            f"glossary_cases: {self.glossary_cases}",
            f"terms: {self.term_count}",
            f"cases_items: {self.cases_item_count}",
            f"cases_excluded: {self.cases_excluded_count}",
            f"term_ko_dup_pairs: {self.term_ko_dup_pairs} (baseline {BASELINE_TERM_KO_DUP_PAIRS})",
            f"term_en_dup_pairs: {self.term_en_dup_pairs} (baseline {BASELINE_TERM_EN_DUP_PAIRS})",
            f"detail_ko_similar_terms: {self.detail_ko_similar_terms} "
            f"(baseline {len(BASELINE_DETAIL_KO_SIMILAR_IDS)}개 id 집합 · "
            f"신규 {len(self.detail_ko_similar_ids - BASELINE_DETAIL_KO_SIMILAR_IDS)} · "
            f"해소 {len(BASELINE_DETAIL_KO_SIMILAR_IDS - self.detail_ko_similar_ids)})",
            f"short_field_count: {self.short_field_count} (baseline {BASELINE_SHORT_FIELD_COUNT})",
            f"alias_term_count: {self.alias_term_count} (floor {ALIASES_MIN_TERM_COUNT})",
            f"errors: {len(self.issues)}",
            f"warnings: {len(self.warnings)}",
        ]
        lines.extend(
            f"ERROR [{issue.code}] {issue.location}: {issue.message}"
            for issue in self.issues
        )
        lines.extend(
            f"WARNING [{issue.code}] {issue.location}: {issue.message}"
            for issue in self.warnings
        )
        lines.extend(f"NOTICE {notice}" for notice in self.notices)
        return "\n".join(lines)


def _load_json(path: Path, report: LintReport, code_prefix: str) -> Any | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        report.add(f"{code_prefix}_READ", str(path), str(exc))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.add(f"{code_prefix}_JSON", str(path), str(exc))
    return None


def _item_location(index: int, item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
        return f"items[{index}]({item['id']})"
    return f"items[{index}]"


def _check_url(value: str, location: str, report: LintReport, scheme_code: str) -> None:
    """E10/E11 (scheme) + E12 (whitespace/newline) — independent checks, both can fire."""
    if not value.lower().startswith(_SAFE_URL_SCHEMES):
        report.add(scheme_code, location, f"URL은 http:// 또는 https:// 로 시작해야 합니다: {value!r}")
    if _WHITESPACE_RE.search(value):
        report.add("URL_WHITESPACE", location, f"URL에 공백/개행이 포함되어 있습니다: {value!r}")


def _check_hygiene(value: str, location: str, report: LintReport) -> None:
    """E13 — OCR/복붙 오염 5종. 화면엔 안 보이지만 검색·정렬·URL을 깨뜨린다."""
    if value != value.strip():
        report.add("STRING_HYGIENE", location, "앞뒤 공백이 있습니다")
    if "  " in value:
        report.add("STRING_HYGIENE", location, "연속된 공백(2칸 이상)이 있습니다")
    if "\t" in value:
        report.add("STRING_HYGIENE", location, "탭 문자가 있습니다")
    if "\n" in value or "\r" in value:
        report.add("STRING_HYGIENE", location, "개행 문자가 있습니다")
    if any(ch in value for ch in _ZERO_WIDTH_CHARS):
        report.add("STRING_HYGIENE", location, "제로폭 문자(U+200B/U+200C/U+FEFF)가 있습니다")
    found = sorted({name for ch, name in _INVISIBLE_SPACE_CHARS.items() if ch in value})
    if found:
        report.add(
            "STRING_HYGIENE", location,
            f"보통 공백처럼 보이지만 다른 문자가 있습니다: {', '.join(found)}",
        )


def _validate_item_schema(item: Any, index: int, report: LintReport) -> str | None:
    """항목 하나의 구조(E1~E4)를 검증하고, 유효한 id 문자열이면 그 값을 반환한다
    (없거나 무효면 None — 이후 related/중복 검사가 이 id 를 참조 대상으로 쓰지 않게)."""
    location = _item_location(index, item)
    if not isinstance(item, dict):
        report.add("ITEM_TYPE", location, "항목은 객체여야 합니다")
        return None

    missing = [name for name in REQUIRED_FIELDS if name not in item]
    for name in missing:
        report.add("REQUIRED_FIELD", location, f"필수 필드 {name!r}가 없습니다")

    for name in sorted(set(item) - ALLOWED_FIELDS):
        report.add("UNKNOWN_FIELD", location, f"허용되지 않은 필드 {name!r}")

    for name, expected in {**REQUIRED_FIELDS, **OPTIONAL_FIELDS}.items():
        if name not in item:
            continue
        value = item[name]
        if not isinstance(value, expected):
            report.add("FIELD_TYPE", location, f"{name!r}는 {expected.__name__} 타입이어야 합니다")

    for name in REQUIRED_FIELDS:
        value = item.get(name)
        if isinstance(value, str) and not value.strip():
            report.add("EMPTY_STRING", location, f"{name!r}는 비어 있을 수 없습니다")

    related = item.get("related")
    if isinstance(related, list):
        for ridx, r in enumerate(related):
            if not isinstance(r, str) or not r.strip():
                report.add(
                    "RELATED_ITEM_TYPE",
                    f"{location}.related[{ridx}]",
                    "related 항목은 비어 있지 않은 문자열이어야 합니다",
                )

    aliases = item.get("aliases")
    if isinstance(aliases, list):
        for aidx, a in enumerate(aliases):
            if not isinstance(a, str) or not a.strip():
                report.add(
                    "ALIAS_ITEM_TYPE",
                    f"{location}.aliases[{aidx}]",
                    "aliases 항목은 비어 있지 않은 문자열이어야 합니다",
                )

    reg_refs = item.get("reg_refs")
    if isinstance(reg_refs, list):
        for ridx, r in enumerate(reg_refs):
            rloc = f"{location}.reg_refs[{ridx}]"
            if isinstance(r, str):
                if not r.strip():
                    report.add("REG_REF_ITEM_TYPE", rloc, "reg_refs 문자열 항목은 비어 있을 수 없습니다")
            elif isinstance(r, dict):
                label = r.get("label")
                if not isinstance(label, str) or not label.strip():
                    # web/render.py _reg_ref_view: label 없으면 이 항목 자체가
                    # 조용히 None → 필터로 사라진다(렌더에서 통째로 안 보임).
                    report.add("REG_REF_LABEL", rloc, "reg_refs 객체 항목에는 비어 있지 않은 label이 필요합니다")
                url = r.get("url")
                if url is not None and not isinstance(url, str):
                    report.add("REG_REF_ITEM_TYPE", rloc, "reg_refs 객체 항목의 url은 문자열이어야 합니다")
            else:
                report.add("REG_REF_ITEM_TYPE", rloc, "reg_refs 항목은 문자열 또는 {label,url} 객체여야 합니다")

    id_value = item.get("id")
    return id_value if isinstance(id_value, str) and id_value.strip() else None


_LABEL_PAREN_RE = re.compile(r"\([^)]*\)")


def _label_key(value: str) -> str:
    """이름 비교용 정규화 — 소문자 + 괄호 병기 제거 + 공백/하이픈/가운뎃점/슬래시 제거.

    ★글자 그대로 비교하면 안 된다. 2026-08-04 실측으로 드러난 실패:
    동의어 'non-conformance' 와 표제어 'Non-conformance' 를 **다른 것으로 보고 통과**시켰다.
    동의어 'quality control unit' 과 표제어 'Quality Control Unit (QCU)' 도 마찬가지다.
    화면 검색은 대소문자를 무시하는 부분일치라 사용자에게는 **같은 이름**인데 검사만
    다르게 본 것이다 — 검사 기준이 사용자가 겪는 것과 어긋나면 그 검사는 헛돈다.

    괄호 병기를 지우는 이유: 이 사전의 표제어는 'Quality Unit(s)'·'Adverse Event (AE)'
    처럼 약어·복수형을 괄호로 덧붙이는 관례라, 괄호를 남기면 같은 이름이 안 맞는다.

    한계(의도적으로 안 잡는다): 복수형 차이는 흡수하지 않는다('written procedure' vs
    'Written Procedures'). 어간 추출은 규칙이 불안정해 오탐을 만든다 — 대신 그런 건
    사람이 표제어/동의어 배정을 정할 때 판단한다."""
    return re.sub(r"[\s\-·/]", "", _LABEL_PAREN_RE.sub("", value).lower())


def _build_label_owners(data: list[Any]) -> dict[str, list[tuple[str, str]]]:
    """정규화한 이름(_label_key) → 이 이름을 쓰는 (소유 id, 필드종류) 목록.
    #6/#7 교차중복 탐지에 쓴다 — id 가 없거나 무효한 항목은 제외한다(스키마
    검증에서 이미 REQUIRED_FIELD/ID_FORMAT 로 잡혔으니 여기서 또 잡을 필요가 없다)."""
    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in data:
        if not isinstance(item, dict):
            continue
        id_value = item.get("id")
        if not (isinstance(id_value, str) and id_value.strip()):
            continue
        for field_name in ("term_ko", "term_en"):
            value = item.get(field_name)
            if isinstance(value, str) and value.strip():
                owners[_label_key(value)].append((id_value, field_name))
        aliases = item.get("aliases")
        if isinstance(aliases, list):
            for a in aliases:
                if isinstance(a, str) and a.strip():
                    owners[_label_key(a)].append((id_value, "aliases"))
    return owners


def _validate_item_aliases(
    item: Any, location: str, id_value: str | None,
    label_owners: dict[str, list[tuple[str, str]]], report: LintReport,
) -> None:
    """aliases 검사 4종: 위생(#3), 자기표제어 동일 금지(#5), 한 용어 안 중복(#4),
    다른 용어의 표제어/동의어와 동일 금지(#6/#7 — 둘 다 ERROR). 비교는 '글자 그대로'
    (대소문자 구분) — 기존 term_ko/term_en 중복 검사(W1)와 같은 관례를 따른다."""
    aliases = item.get("aliases")
    if not isinstance(aliases, list):
        return
    term_ko = item.get("term_ko") if isinstance(item.get("term_ko"), str) else None
    term_en = item.get("term_en") if isinstance(item.get("term_en"), str) else None
    seen: set[str] = set()
    for aidx, a in enumerate(aliases):
        if not isinstance(a, str) or not a.strip():
            continue  # already flagged as ALIAS_ITEM_TYPE
        aloc = f"{location}.aliases[{aidx}]"
        _check_hygiene(a, aloc, report)

        # ★비교 기준이 검사마다 다르다. 헷갈리기 쉬우니 이유를 남긴다.
        #
        # 자기 표제어·같은 용어 안 중복 → **글자 그대로**.
        #   표제어가 'Back-up' 인데 동의어로 'backup' 을 두는 건 **정상이고 필요한 일**이다.
        #   화면 검색은 정규화 없는 부분일치라, 이 동의어가 없으면 "backup" 으로 아무것도
        #   못 찾는다. 정규화해서 비교하면 이런 표기 변형이 전부 "자기 자신과 동일"로
        #   걸려버려, 정작 검색을 되살리는 데이터를 못 넣게 된다.
        #
        # 다른 용어와의 충돌 → **정규화**(_label_key).
        #   여기서는 해가 반대다. 두 용어가 같은 이름을 주장하면 사용자가 틀린 카드를 본다.
        #   'non-conformance'(동의어) 와 'Non-conformance'(표제어) 는 검색상 같은 이름이다.
        if a == term_ko or a == term_en:
            report.add("ALIAS_SELF", aloc, f"자기 자신의 표제어와 동일합니다: {a!r}")

        if a in seen:
            report.add("ALIAS_DUPLICATE", aloc, f"이 용어 안에서 중복된 동의어입니다: {a!r}")
        seen.add(a)

        for owner_id, owner_field in label_owners.get(_label_key(a), []):
            if owner_id == id_value:
                continue  # 자기 자신(표제어는 ALIAS_SELF, 동의어 자체중복은 위에서 처리)
            if owner_field in ("term_ko", "term_en"):
                report.add(
                    "ALIAS_TERM_COLLISION", aloc,
                    f"다른 용어 {owner_id!r}의 표제어({owner_field})와 동일한 동의어입니다: {a!r}",
                )
            else:
                report.add(
                    "ALIAS_ALIAS_COLLISION", aloc,
                    f"다른 용어 {owner_id!r}의 동의어와 동일합니다: {a!r}",
                )


def _validate_item_refs_and_hygiene(
    item: Any, index: int, report: LintReport, ids: set[str],
    label_owners: dict[str, list[tuple[str, str]]],
) -> None:
    """related 참조(E7~E9)·URL(E10~E12)·문자열 위생(E13) — 전체 id 집합이 필요해서
    스키마 검증과 별도 패스로 돈다."""
    if not isinstance(item, dict):
        return
    location = _item_location(index, item)
    id_value = item.get("id") if isinstance(item.get("id"), str) else None

    related = item.get("related")
    if isinstance(related, list):
        seen: set[str] = set()
        for ridx, r in enumerate(related):
            if not isinstance(r, str) or not r.strip():
                continue  # already flagged as RELATED_ITEM_TYPE
            rloc = f"{location}.related[{ridx}]"
            if id_value is not None and r == id_value:
                report.add("RELATED_SELF", rloc, f"자기 자신을 참조합니다: {r!r}")
            elif r not in ids:
                report.add("RELATED_ORPHAN", rloc, f"존재하지 않는 id를 참조합니다: {r!r}")
            if r in seen:
                report.add("RELATED_DUPLICATE", rloc, f"related 안에서 중복 참조입니다: {r!r}")
            seen.add(r)
            _check_hygiene(r, rloc, report)

    _validate_item_aliases(item, location, id_value, label_owners, report)

    source_url = item.get("source_url")
    if isinstance(source_url, str) and source_url.strip():
        _check_url(source_url, f"{location}.source_url", report, "SOURCE_URL_SCHEME")

    reg_refs = item.get("reg_refs")
    if isinstance(reg_refs, list):
        for ridx, r in enumerate(reg_refs):
            rloc = f"{location}.reg_refs[{ridx}]"
            if isinstance(r, str) and r:
                _check_hygiene(r, rloc, report)
            elif isinstance(r, dict):
                label = r.get("label")
                if isinstance(label, str) and label:
                    _check_hygiene(label, f"{rloc}.label", report)
                url = r.get("url")
                if isinstance(url, str) and url.strip():
                    _check_url(url, f"{rloc}.url", report, "REG_REF_URL_SCHEME")
                if isinstance(url, str) and url:
                    _check_hygiene(url, f"{rloc}.url", report)

    for name in ("id", "term_ko", "term_en", "easy_ko", "definition_source", "source_url", "detail_ko"):
        value = item.get(name)
        if isinstance(value, str) and value:
            _check_hygiene(value, f"{location}.{name}", report)


def _apply_baseline(
    report: LintReport, label: str, count: int, baseline: int, error_code: str
) -> None:
    """WARNING 카테고리 공통 규칙: 실측치까지는 통과, 기준선 초과분은 ERROR로
    승격, 기준선 미만이면 낮추라는 NOTICE만 남긴다(통과는 유지)."""
    if count > baseline:
        report.add(
            error_code,
            "glossary",
            f"{label} {count}건이 기준선 {baseline}건을 초과했습니다 "
            f"(신규 위반을 조사하십시오 — glossary_lint.py 상단 BASELINE 상수는 실측치입니다)",
        )
    elif count < baseline:
        report.notices.append(
            f"{label} {count}건 < 기준선 {baseline}건입니다 — glossary_lint.py 상단 BASELINE "
            f"상수를 {count}로 낮추는 것을 검토하십시오"
        )


def _check_term_duplicates(data: list[Any], report: LintReport) -> None:
    """W1 — term_ko/term_en 중복."""
    by_ko: dict[str, list[str]] = defaultdict(list)
    by_en: dict[str, list[str]] = defaultdict(list)
    for item in data:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else "?"
        ko = item.get("term_ko")
        if isinstance(ko, str) and ko.strip():
            by_ko[ko].append(item_id)
        en = item.get("term_en")
        if isinstance(en, str) and en.strip():
            by_en[en].append(item_id)

    def _pairs(groups: dict[str, list[str]], code: str) -> int:
        total = 0
        for value in sorted(groups):
            ids = groups[value]
            if len(ids) < 2:
                continue
            total += len(ids) * (len(ids) - 1) // 2
            report.warn(code, f"term={value!r}", f"동일한 값을 가진 id: {', '.join(sorted(ids))}")
        return total

    report.term_ko_dup_pairs = _pairs(by_ko, "TERM_KO_DUPLICATE")
    report.term_en_dup_pairs = _pairs(by_en, "TERM_EN_DUPLICATE")
    _apply_baseline(
        report, "term_ko 중복 쌍", report.term_ko_dup_pairs,
        BASELINE_TERM_KO_DUP_PAIRS, "TERM_KO_DUPLICATE_BASELINE",
    )
    _apply_baseline(
        report, "term_en 중복 쌍", report.term_en_dup_pairs,
        BASELINE_TERM_EN_DUP_PAIRS, "TERM_EN_DUPLICATE_BASELINE",
    )


def _check_detail_ko_similarity(data: list[Any], report: LintReport) -> None:
    """W2 — detail_ko 템플릿 복제. difflib quick_ratio()/real_quick_ratio() 로 명백히
    임계값 미달인 쌍을 먼저 걸러내 O(n^2) 전수비교를 실용적인 속도로 만든다(정확한
    ratio() 는 quick 필터를 통과한 쌍에만 계산 — 결과는 필터 없이 계산한 것과 동일함을
    개발 중 200개 실데이터로 직접 대조 확인했다)."""
    terms = [
        (item.get("id"), item.get("detail_ko"))
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("id"), str) and item.get("id")
        and isinstance(item.get("detail_ko"), str) and item.get("detail_ko").strip()
    ]
    involved: set[str] = set()
    pairs: list[tuple[str, str, float]] = []
    matcher = difflib.SequenceMatcher(autojunk=False)
    threshold = DETAIL_KO_SIMILARITY_THRESHOLD
    for i in range(len(terms)):
        id1, text1 = terms[i]
        matcher.set_seq1(text1)
        for j in range(i + 1, len(terms)):
            id2, text2 = terms[j]
            matcher.set_seq2(text2)
            if matcher.real_quick_ratio() < threshold:
                continue
            if matcher.quick_ratio() < threshold:
                continue
            ratio = matcher.ratio()
            if ratio >= threshold:
                involved.add(id1)
                involved.add(id2)
                pairs.append((id1, id2, ratio))
    report.detail_ko_similar_terms = len(involved)
    report.detail_ko_similar_ids = involved

    # 기준선을 **집합**으로 비교한다 — 개수만 보면 상쇄(1건 해소 + 1건 신규)가 숨는다.
    fresh = sorted(involved - BASELINE_DETAIL_KO_SIMILAR_IDS)
    resolved = sorted(BASELINE_DETAIL_KO_SIMILAR_IDS - involved)

    if fresh:
        # 신규는 ERROR. 그 용어가 어느 용어와 겹치는지까지 찍어야 진단이 된다.
        for term_id in fresh:
            partners = sorted(
                (other, ratio)
                for a, b, ratio in pairs
                for term, other in ((a, b), (b, a))
                if term == term_id
            )
            shown = ", ".join(f"{o}({r:.3f})" for o, r in partners[:5])
            more = f" 외 {len(partners) - 5}건" if len(partners) > 5 else ""
            report.add(
                "DETAIL_KO_SIMILAR_NEW", term_id,
                f"실무 맥락(detail_ko)이 기존 용어와 유사도 {threshold} 이상입니다 — "
                f"겹치는 용어: {shown}{more}. 기존 설명을 복사하지 말고 이 용어만의 "
                f"실사 맥락을 쓰십시오(그래도 맞다면 glossary_lint.py 의 "
                f"BASELINE_DETAIL_KO_SIMILAR_IDS 에 근거와 함께 추가).",
            )
    if resolved:
        report.notice(
            "DETAIL_KO_SIMILAR_RESOLVED", "baseline",
            f"{len(resolved)}개 용어가 템플릿 복제에서 벗어났습니다 — glossary_lint.py 의 "
            f"BASELINE_DETAIL_KO_SIMILAR_IDS 에서 제거해 기준선을 좁히십시오: "
            f"{', '.join(resolved[:10])}{' …' if len(resolved) > 10 else ''}",
        )
    # 기준선 안쪽 쌍은 **한 줄로 요약**한다. 509줄을 매번 찍으면 아무도 안 읽고,
    # 그러면 진짜 신규 경고가 그 사이에 묻힌다(항상 울리는 경보는 경보가 아니다).
    # 전체 목록이 필요하면 --verbose.
    report.detail_ko_similar_pairs = pairs
    if pairs and not report.verbose:
        report.warn(
            "DETAIL_KO_SIMILAR", "요약",
            f"기준선 내 템플릿 복제 {len(involved)}개 용어 / {len(pairs)}쌍 "
            f"(개별 목록은 --verbose). 신규 {len(fresh)}건 · 해소 {len(resolved)}건.",
        )
    elif pairs:
        for id1, id2, ratio in pairs:
            report.warn(
                "DETAIL_KO_SIMILAR", f"{id1} ~ {id2}",
                f"detail_ko 유사도 {ratio:.3f} (임계값 {threshold})",
            )


def _check_short_fields(data: list[Any], report: LintReport) -> None:
    """W3 — easy_ko/detail_ko 가 비정상적으로 짧음(10자 미만, 값이 있는 경우만)."""
    count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else "?"
        for name in ("easy_ko", "detail_ko"):
            value = item.get(name)
            if isinstance(value, str) and 0 < len(value.strip()) < SHORT_FIELD_MIN_LENGTH:
                count += 1
                report.warn(
                    "SHORT_FIELD",
                    f"{item_id}.{name}",
                    f"길이 {len(value.strip())}자 (< {SHORT_FIELD_MIN_LENGTH})",
                )
    report.short_field_count = count
    _apply_baseline(
        report, "easy_ko/detail_ko 10자 미만", count,
        BASELINE_SHORT_FIELD_COUNT, "SHORT_FIELD_BASELINE",
    )


def _check_alias_floor(data: list[Any], report: LintReport) -> None:
    """#8 — aliases 0건 가드. ALIAS_SELF/ALIAS_DUPLICATE/ALIAS_TERM_COLLISION/
    ALIAS_ALIAS_COLLISION 은 전부 aliases 원소가 있어야만 검사할 것이 생긴다 —
    필드가 통째로 날아가면(리네임·직렬화 사고 등) 그 검사들은 조용히 전부 통과한다.
    보유 용어 수가 실측 하한 밑으로 떨어지면 그 자체를 ERROR 로 명시한다.

    전체 코퍼스가 하한보다 작으면(합성 테스트 픽스처 등) 애초에 하한을 채우는 게
    구조적으로 불가능하므로 건너뛴다 — ALIASES_MIN_TERM_COUNT 상단 주석 참고."""
    count = sum(
        1 for item in data
        if isinstance(item, dict)
        and isinstance(item.get("aliases"), list)
        and any(isinstance(a, str) and a.strip() for a in item.get("aliases"))
    )
    report.alias_term_count = count
    if len(data) < ALIASES_MIN_TERM_COUNT:
        return
    if count < ALIASES_MIN_TERM_COUNT:
        report.add(
            "ALIASES_COUNT_FLOOR",
            "glossary",
            f"aliases를 가진 용어가 {count}개로 하한 {ALIASES_MIN_TERM_COUNT}개 밑으로 "
            f"떨어졌습니다 (aliases 필드가 유실되지 않았는지 확인하십시오 — 이 상태에서는 "
            f"ALIAS_SELF/ALIAS_DUPLICATE/ALIAS_TERM_COLLISION/ALIAS_ALIAS_COLLISION 검사가 "
            f"조용히 전부 통과합니다)",
        )


def _validate_cases(
    path: Path, report: LintReport, glossary_ids: set[str] | None
) -> None:
    """glossary_cases.json — E14~E16.

    glossary_ids 가 None(glossary.json 자체가 구조적으로 무효/비어 있음)이면
    E14 id-집합 대조는 건너뛴다 — 대조 기준이 없는데 "전부 초과"로 오탐하지 않기
    위함. 항목 형태(E15/E16) 검사는 그와 무관하게 항상 수행한다."""
    data = _load_json(path, report, "CASES")
    if data is None:
        return
    if not isinstance(data, dict):
        report.add("CASES_TYPE", str(path), "최상위 값은 객체여야 합니다")
        return

    items = data.get("items")
    if not isinstance(items, list):
        report.add("CASES_ITEMS_TYPE", str(path), "'items'는 배열이어야 합니다")
        items = []
    excluded = data.get("excluded")
    if not isinstance(excluded, list):
        report.add("CASES_EXCLUDED_TYPE", str(path), "'excluded'는 배열이어야 합니다")
        excluded = []

    report.cases_item_count = len(items)
    report.cases_excluded_count = len(excluded)

    all_ids: list[str] = []

    for index, entry in enumerate(items):
        location = f"{path}:items[{index}]"
        if not isinstance(entry, dict):
            report.add("CASES_ITEM_TYPE", location, "items 항목은 객체여야 합니다")
            continue
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and entry_id.strip():
            all_ids.append(entry_id)
            location = f"{path}:items[{index}]({entry_id})"
        else:
            report.add("CASES_ITEM_ID", location, "비어 있지 않은 문자열 id가 필요합니다")
        q = entry.get("q")
        if not isinstance(q, str) or not q.strip():
            report.add("CASES_ITEM_Q", location, "'q'는 비어 있지 않은 문자열이어야 합니다")
        for count_field in ("findings", "documents"):
            value = entry.get(count_field)
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
            if not valid:
                report.add(
                    "CASES_ITEM_COUNT",
                    location,
                    f"'{count_field}'는 1 이상의 정수여야 합니다 (현재 {value!r})",
                )

    for index, entry in enumerate(excluded):
        location = f"{path}:excluded[{index}]"
        if not isinstance(entry, dict):
            report.add("CASES_EXCLUDED_TYPE", location, "excluded 항목은 객체여야 합니다")
            continue
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and entry_id.strip():
            all_ids.append(entry_id)
            location = f"{path}:excluded[{index}]({entry_id})"
        else:
            report.add("CASES_EXCLUDED_ID", location, "비어 있지 않은 문자열 id가 필요합니다")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            report.add("CASES_EXCLUDED_REASON", location, "'reason'은 비어 있지 않은 문자열이어야 합니다")

    id_counts = Counter(all_ids) if all_ids else Counter()
    dup_ids = sorted(cid for cid, n in id_counts.items() if n > 1)
    if dup_ids:
        report.add(
            "CASES_ID_DUPLICATE",
            str(path),
            f"items+excluded 사이에 중복 id: {', '.join(dup_ids)}",
        )

    if glossary_ids is None:
        return
    case_id_set = set(all_ids)
    missing = sorted(glossary_ids - case_id_set)
    if missing:
        report.add(
            "CASES_ID_MISSING",
            str(path),
            f"glossary.json에는 있지만 items/excluded 어디에도 없는 id {len(missing)}개: "
            f"{', '.join(missing)}",
        )
    extra = sorted(case_id_set - glossary_ids)
    if extra:
        report.add(
            "CASES_ID_EXTRA",
            str(path),
            f"glossary.json에 없는 id가 items/excluded에 {len(extra)}개: {', '.join(extra)}",
        )


def lint_glossary(
    glossary: Path = DEFAULT_GLOSSARY,
    glossary_cases: Path = DEFAULT_GLOSSARY_CASES,
    verbose: bool = False,
) -> LintReport:
    glossary = Path(glossary).resolve()
    glossary_cases = Path(glossary_cases).resolve()
    report = LintReport(glossary=glossary, glossary_cases=glossary_cases, verbose=verbose)

    data = _load_json(glossary, report, "GLOSSARY")
    if data is None:
        _validate_cases(glossary_cases, report, None)
        return report
    if not isinstance(data, list):
        report.add("GLOSSARY_TYPE", str(glossary), "최상위 값은 배열이어야 합니다")
        _validate_cases(glossary_cases, report, None)
        return report
    report.term_count = len(data)
    if not data:
        # E17 — 0건 가드. 빈 집합에 대한 검사는 전부 통과해버리므로(이 저장소에서
        # 반복된 함정) 명시적으로 막는다.
        report.add("GLOSSARY_EMPTY", str(glossary), "용어가 0개입니다")
        _validate_cases(glossary_cases, report, None)
        return report

    ids: set[str] = set()
    seen_ids: dict[str, int] = {}
    for index, item in enumerate(data):
        id_value = _validate_item_schema(item, index, report)
        if id_value is None:
            continue
        if id_value in seen_ids:
            report.add(
                "DUPLICATE_ID",
                _item_location(index, item),
                f"id {id_value!r}가 items[{seen_ids[id_value]}]에도 있습니다",
            )
        else:
            seen_ids[id_value] = index
        if not _ID_RE.fullmatch(id_value):
            report.add(
                "ID_FORMAT",
                _item_location(index, item),
                f"id {id_value!r}는 ^[a-z0-9]+(-[a-z0-9]+)*$ 형식이어야 합니다",
            )
        ids.add(id_value)

    label_owners = _build_label_owners(data)
    for index, item in enumerate(data):
        _validate_item_refs_and_hygiene(item, index, report, ids, label_owners)

    _check_term_duplicates(data, report)
    _check_detail_ko_similarity(data, report)
    _check_short_fields(data, report)
    _check_alias_floor(data, report)

    _validate_cases(glossary_cases, report, ids)
    return report


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="GRM glossary.json / glossary_cases.json 무인 검증 게이트")
    parser.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    parser.add_argument("--glossary-cases", type=Path, default=DEFAULT_GLOSSARY_CASES)
    parser.add_argument(
        "--verbose", action="store_true",
        help="기준선 안쪽 템플릿 복제 쌍을 요약 대신 전부 출력(기본은 한 줄 요약 — "
             "매번 500줄을 찍으면 진짜 신규 경고가 그 사이에 묻힌다)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except ValueError as exc:
        print("glossary_lint: FAIL")
        print("errors: 1")
        print(f"ERROR [ARGUMENTS] cli: {exc}")
        return 1

    try:
        report = lint_glossary(args.glossary, args.glossary_cases, verbose=args.verbose)
        print(report.format())
        return 0 if report.ok else 1
    except Exception as exc:  # Keep unattended sessions on the documented 0/1 contract.
        print("glossary_lint: FAIL")
        print(f"glossary: {Path(args.glossary).resolve()}")
        print("errors: 1")
        print(f"ERROR [UNEXPECTED] validator: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
