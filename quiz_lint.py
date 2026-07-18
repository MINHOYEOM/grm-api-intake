"""GRM quiz bank validator for unattended CI and scheduled sessions.

The validator is intentionally dependency-free.  It validates the committed quiz
bank, resolves local glossary/brief references, and prints a deterministic report
to stdout.  Exit status is 0 for a clean bank and 1 for every validation or input
error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_QUIZ_BANK = REPO_ROOT / "web" / "data" / "quiz_bank.json"
DEFAULT_GLOSSARY = REPO_ROOT / "web" / "data" / "glossary.json"
DEFAULT_BRIEFS_DIR = REPO_ROOT / "web" / "data" / "briefs"

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "question_ko": str,
    "choices": list,
    "answer_index": int,
    "explanation_ko": str,
    "source_type": str,
    "source_ref": str,
    "difficulty": str,
}
OPTIONAL_FIELDS: dict[str, type] = {"week": str}
ALLOWED_SOURCE_TYPES = frozenset({"glossary", "brief", "finding", "external"})
ALLOWED_DIFFICULTIES = frozenset({"easy", "normal"})

# Machine-readable schema kept beside the dependency-free validator.  The manual
# checks below implement this contract without adding jsonschema to requirements.
QUIZ_BANK_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": list(REQUIRED_FIELDS),
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "question_ko": {"type": "string", "minLength": 1},
            "choices": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1},
            },
            "answer_index": {"type": "integer", "minimum": 0, "maximum": 3},
            "explanation_ko": {"type": "string", "minLength": 1},
            "source_type": {"enum": sorted(ALLOWED_SOURCE_TYPES)},
            "source_ref": {"type": "string", "minLength": 1},
            "difficulty": {"enum": sorted(ALLOWED_DIFFICULTIES)},
            "week": {"type": "string", "pattern": r"^[0-9]{4}(0[1-9]|[1-4][0-9]|5[0-3])$"},
        },
    },
}

# Public quiz copy must teach regulatory/quality facts, not GRM implementation
# mechanics.  Patterns are deliberately explicit to avoid broad false positives
# such as banning ordinary uses of "근거", "카드", or "데이터".
INTERNAL_CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("GRM", r"(?<![A-Za-z0-9])GRM(?![A-Za-z0-9])"),
    ("Evidence Level", r"(?<![A-Za-z0-9])evidence[\s_-]*level(?![A-Za-z0-9])|근거\s*(?:수준|등급)"),
    ("Signal Tier", r"(?<![A-Za-z0-9])signal[\s_-]*tier(?![A-Za-z0-9])|신호\s*(?:우선순위|등급)"),
    ("Tier 1/2/3", r"(?<![A-Za-z0-9])tier\s*[123](?![A-Za-z0-9])"),
    ("dual links", r"(?<![A-Za-z0-9])dual[\s_-]*links?(?![A-Za-z0-9])|듀얼\s*링크"),
    ("handoff", r"(?<![A-Za-z0-9])handoff(?![A-Za-z0-9])|핸드오프"),
    ("intake", r"(?<![A-Za-z0-9])intake(?![A-Za-z0-9])|인테이크"),
    ("scaffold", r"(?<![A-Za-z0-9])(?:card[\s_-]*)?scaffold(?![A-Za-z0-9])|스캐폴드"),
    ("delta bridge", r"(?<![A-Za-z0-9])delta[\s_-]*bridge(?![A-Za-z0-9])|델타\s*브릿지"),
    ("Notion", r"(?<![A-Za-z0-9])notion(?![A-Za-z0-9])|노션"),
    ("Supabase", r"(?<![A-Za-z0-9])supabase(?![A-Za-z0-9])|수파베이스"),
    ("PostgREST/RLS", r"(?<![A-Za-z0-9])postgrest(?![A-Za-z0-9])|(?<![A-Za-z0-9])RLS(?![A-Za-z0-9])"),
    ("quiz schema field", r"(?<![A-Za-z0-9])(?:source_ref|answer_index|render_order|quiz_bank)(?![A-Za-z0-9])"),
    ("publish lint code", r"(?<![A-Za-z0-9])PL(?:18|19)(?![A-Za-z0-9])"),
)
_INTERNAL_REGEXES = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in INTERNAL_CONCEPT_PATTERNS
)

_WEEK_RE = re.compile(r"^(?P<year>[0-9]{4})(?P<week>0[1-9]|[1-4][0-9]|5[0-3])$")
_BRIEF_PATH_RE = re.compile(r"^/briefs/(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})/?$")
_BRIEF_HOSTS = frozenset({"grm-solutions.com", "www.grm-solutions.com"})


@dataclass(frozen=True)
class LintIssue:
    code: str
    location: str
    message: str


@dataclass
class LintReport:
    quiz_bank: Path
    item_count: int = 0
    source_counts: Counter[str] = field(default_factory=Counter)
    difficulty_counts: Counter[str] = field(default_factory=Counter)
    week_counts: Counter[str] = field(default_factory=Counter)
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, code: str, location: str, message: str) -> None:
        self.issues.append(LintIssue(code, location, message))

    @staticmethod
    def _counts(values: Counter[str]) -> str:
        if not values:
            return "none"
        return ", ".join(f"{key}={values[key]}" for key in sorted(values))

    def format(self) -> str:
        lines = [
            f"quiz_lint: {'PASS' if self.ok else 'FAIL'}",
            f"quiz_bank: {self.quiz_bank}",
            f"items: {self.item_count}",
            f"sources: {self._counts(self.source_counts)}",
            f"difficulty: {self._counts(self.difficulty_counts)}",
            f"weeks: {self._counts(self.week_counts)}",
            f"errors: {len(self.issues)}",
        ]
        lines.extend(
            f"ERROR [{issue.code}] {issue.location}: {issue.message}"
            for issue in self.issues
        )
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


def _normalise_choice(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split())


def _valid_week(value: str) -> bool:
    match = _WEEK_RE.fullmatch(value)
    if not match:
        return False
    try:
        dt.date.fromisocalendar(int(match.group("year")), int(match.group("week")), 1)
    except ValueError:
        return False
    return True


def _url_parts(value: str) -> tuple[Any | None, str | None]:
    if not value or any(char.isspace() for char in value):
        return None, "URL은 비어 있지 않아야 하며 공백을 포함할 수 없습니다"
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"}:
            return None, "URL scheme은 http 또는 https여야 합니다"
        if not parts.hostname:
            return None, "URL host가 없습니다"
        # Accessing port validates malformed/non-numeric/out-of-range ports.
        _ = parts.port
    except ValueError as exc:
        return None, f"잘못된 URL입니다 ({exc})"
    return parts, None


def _load_glossary_ids(path: Path, report: LintReport) -> set[str]:
    data = _load_json(path, report, "GLOSSARY")
    if data is None:
        return set()
    if not isinstance(data, list):
        report.add("GLOSSARY_SCHEMA", str(path), "최상위 값은 배열이어야 합니다")
        return set()

    ids: set[str] = set()
    for index, entry in enumerate(data):
        location = f"{path}[{index}]"
        if not isinstance(entry, dict):
            report.add("GLOSSARY_SCHEMA", location, "항목은 객체여야 합니다")
            continue
        glossary_id = entry.get("id")
        if not isinstance(glossary_id, str) or not glossary_id.strip():
            report.add("GLOSSARY_ID", location, "비어 있지 않은 문자열 id가 필요합니다")
            continue
        if glossary_id in ids:
            report.add("GLOSSARY_DUPLICATE_ID", location, f"중복 id {glossary_id!r}")
        ids.add(glossary_id)
    return ids


def _load_brief_index(path: Path, report: LintReport) -> dict[str, set[str]]:
    if not path.is_dir():
        report.add("BRIEFS_READ", str(path), "브리프 디렉터리가 없습니다")
        return {}
    files = sorted(path.glob("*.json"), key=lambda item: item.name)
    if not files:
        report.add("BRIEFS_EMPTY", str(path), "검증할 브리프 JSON이 없습니다")
        return {}

    index: dict[str, set[str]] = {}
    date_files: dict[str, Path] = {}
    for brief_path in files:
        data = _load_json(brief_path, report, "BRIEF")
        if data is None:
            continue
        if not isinstance(data, dict):
            report.add("BRIEF_SCHEMA", str(brief_path), "최상위 값은 객체여야 합니다")
            continue
        meta = data.get("brief")
        cards = data.get("cards")
        publish_date = meta.get("publish_date") if isinstance(meta, dict) else None
        if not isinstance(publish_date, str):
            report.add("BRIEF_SCHEMA", str(brief_path), "brief.publish_date 문자열이 필요합니다")
            continue
        try:
            dt.date.fromisoformat(publish_date)
        except ValueError:
            report.add("BRIEF_SCHEMA", str(brief_path), f"잘못된 publish_date {publish_date!r}")
            continue
        if publish_date in date_files:
            report.add(
                "BRIEF_DUPLICATE_DATE",
                str(brief_path),
                f"{publish_date}가 {date_files[publish_date]}에도 있습니다",
            )
        else:
            date_files[publish_date] = brief_path
        if not isinstance(cards, list):
            report.add("BRIEF_SCHEMA", str(brief_path), "cards 배열이 필요합니다")
            continue

        card_ids = index.setdefault(publish_date, set())
        for card_index, card in enumerate(cards):
            location = f"{brief_path}:cards[{card_index}]"
            card_id = card.get("id") if isinstance(card, dict) else None
            if not isinstance(card_id, str) or not card_id.strip():
                report.add("BRIEF_CARD_ID", location, "비어 있지 않은 문자열 id가 필요합니다")
                continue
            if card_id in card_ids:
                report.add("BRIEF_DUPLICATE_CARD_ID", location, f"중복 카드 id {card_id!r}")
            card_ids.add(card_id)
    return index


def _item_location(index: int, item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
        return f"items[{index}]({item['id']})"
    return f"items[{index}]"


def _validate_item_schema(item: Any, index: int, report: LintReport) -> bool:
    location = _item_location(index, item)
    if not isinstance(item, dict):
        report.add("ITEM_TYPE", location, "항목은 객체여야 합니다")
        return False

    missing = [name for name in REQUIRED_FIELDS if name not in item]
    for name in missing:
        report.add("REQUIRED_FIELD", location, f"필수 필드 {name!r}가 없습니다")

    allowed = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    for name in sorted(set(item) - allowed):
        report.add("UNKNOWN_FIELD", location, f"허용되지 않은 필드 {name!r}")

    for name, expected in {**REQUIRED_FIELDS, **OPTIONAL_FIELDS}.items():
        if name not in item:
            continue
        value = item[name]
        valid_type = type(value) is int if expected is int else isinstance(value, expected)
        if not valid_type:
            report.add("FIELD_TYPE", location, f"{name!r}는 {expected.__name__} 타입이어야 합니다")

    for name in ("id", "question_ko", "explanation_ko", "source_ref"):
        value = item.get(name)
        if isinstance(value, str) and not value.strip():
            report.add("EMPTY_STRING", location, f"{name!r}는 비어 있을 수 없습니다")

    choices = item.get("choices")
    if isinstance(choices, list):
        if len(choices) != 4:
            report.add("CHOICES_COUNT", location, f"choices는 정확히 4개여야 합니다 (현재 {len(choices)}개)")
        normalised: list[str] = []
        for choice_index, choice in enumerate(choices):
            if not isinstance(choice, str):
                report.add("CHOICE_TYPE", location, f"choices[{choice_index}]는 문자열이어야 합니다")
            elif not choice.strip():
                report.add("CHOICE_EMPTY", location, f"choices[{choice_index}]는 비어 있을 수 없습니다")
            else:
                normalised.append(_normalise_choice(choice))
        if len(normalised) != len(set(normalised)):
            report.add("CHOICES_DUPLICATE", location, "정규화했을 때 같은 선택지가 있습니다")

    answer_index = item.get("answer_index")
    if type(answer_index) is int and not 0 <= answer_index <= 3:
        report.add("ANSWER_INDEX", location, "answer_index는 0부터 3 사이여야 합니다")

    difficulty = item.get("difficulty")
    if isinstance(difficulty, str) and difficulty not in ALLOWED_DIFFICULTIES:
        report.add(
            "DIFFICULTY",
            location,
            f"difficulty는 {', '.join(sorted(ALLOWED_DIFFICULTIES))} 중 하나여야 합니다",
        )

    source_type = item.get("source_type")
    if isinstance(source_type, str) and source_type not in ALLOWED_SOURCE_TYPES:
        report.add(
            "SOURCE_TYPE",
            location,
            f"source_type은 {', '.join(sorted(ALLOWED_SOURCE_TYPES))} 중 하나여야 합니다",
        )

    week = item.get("week")
    if isinstance(week, str) and not _valid_week(week):
        report.add("WEEK", location, "week는 실제 ISO 주차를 나타내는 YYYYWW 형식이어야 합니다")

    return not missing


def _validate_internal_concepts(item: dict[str, Any], index: int, report: LintReport) -> None:
    exposed: list[str] = []
    for name in ("question_ko", "explanation_ko"):
        if isinstance(item.get(name), str):
            exposed.append(item[name])
    choices = item.get("choices")
    if isinstance(choices, list):
        exposed.extend(choice for choice in choices if isinstance(choice, str))
    text = unicodedata.normalize("NFKC", "\n".join(exposed))
    for label, pattern in _INTERNAL_REGEXES:
        if pattern.search(text):
            report.add(
                "INTERNAL_CONCEPT",
                _item_location(index, item),
                f"공개 퀴즈 금지 내부 개념이 포함되어 있습니다: {label}",
            )


def _validate_source(
    item: dict[str, Any],
    index: int,
    report: LintReport,
    glossary_ids: set[str],
    brief_index: dict[str, set[str]],
) -> None:
    source_type = item.get("source_type")
    source_ref = item.get("source_ref")
    if source_type not in ALLOWED_SOURCE_TYPES or not isinstance(source_ref, str) or not source_ref.strip():
        return
    location = _item_location(index, item)

    if source_type == "glossary":
        if source_ref not in glossary_ids:
            report.add("GLOSSARY_REF", location, f"glossary.json에 id {source_ref!r}가 없습니다")
        return

    parts, error = _url_parts(source_ref)
    if error:
        report.add("SOURCE_URL", location, error)
        return
    if source_type != "brief":
        return  # finding/external URLs are syntax-only by contract.

    assert parts is not None
    if parts.hostname.lower() not in _BRIEF_HOSTS:
        report.add("BRIEF_HOST", location, f"GRM 브리프 host가 아닙니다: {parts.hostname!r}")
        return
    match = _BRIEF_PATH_RE.fullmatch(parts.path)
    if not match:
        report.add("BRIEF_PATH", location, "브리프 URL path는 /briefs/YYYY-MM-DD/ 형식이어야 합니다")
        return
    publish_date = match.group("date")
    try:
        dt.date.fromisoformat(publish_date)
    except ValueError:
        report.add("BRIEF_PATH", location, f"잘못된 브리프 날짜 {publish_date!r}")
        return
    anchor = unquote(parts.fragment)
    if not anchor:
        report.add("BRIEF_ANCHOR", location, "브리프 딥링크에 #카드-id 앵커가 없습니다")
        return
    if publish_date not in brief_index:
        report.add("BRIEF_DATE", location, f"web/data/briefs에 {publish_date} 브리프가 없습니다")
        return
    if anchor not in brief_index[publish_date]:
        report.add("BRIEF_ANCHOR", location, f"{publish_date} 브리프에 카드 id {anchor!r}가 없습니다")


def lint_quiz_bank(
    quiz_bank: Path = DEFAULT_QUIZ_BANK,
    glossary: Path = DEFAULT_GLOSSARY,
    briefs_dir: Path = DEFAULT_BRIEFS_DIR,
) -> LintReport:
    quiz_bank = Path(quiz_bank).resolve()
    glossary = Path(glossary).resolve()
    briefs_dir = Path(briefs_dir).resolve()
    report = LintReport(quiz_bank=quiz_bank)
    data = _load_json(quiz_bank, report, "QUIZ")
    if data is None:
        return report
    if not isinstance(data, list):
        report.add("BANK_TYPE", str(quiz_bank), "최상위 값은 배열이어야 합니다")
        return report
    report.item_count = len(data)
    if not data:
        report.add("BANK_EMPTY", str(quiz_bank), "퀴즈 뱅크가 비어 있습니다")
        return report

    needs_glossary = any(isinstance(item, dict) and item.get("source_type") == "glossary" for item in data)
    needs_briefs = any(isinstance(item, dict) and item.get("source_type") == "brief" for item in data)
    glossary_ids = _load_glossary_ids(glossary, report) if needs_glossary else set()
    brief_index = _load_brief_index(briefs_dir, report) if needs_briefs else {}

    seen_ids: dict[str, int] = {}
    for index, item in enumerate(data):
        _validate_item_schema(item, index, report)
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id.strip():
            if item_id in seen_ids:
                report.add(
                    "DUPLICATE_ID",
                    _item_location(index, item),
                    f"id {item_id!r}가 items[{seen_ids[item_id]}]에도 있습니다",
                )
            else:
                seen_ids[item_id] = index
        source_type = item.get("source_type")
        if isinstance(source_type, str):
            report.source_counts[source_type] += 1
        difficulty = item.get("difficulty")
        if isinstance(difficulty, str):
            report.difficulty_counts[difficulty] += 1
        week = item.get("week")
        if isinstance(week, str):
            report.week_counts[week] += 1
        _validate_internal_concepts(item, index, report)
        _validate_source(item, index, report, glossary_ids, brief_index)
    return report


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="GRM quiz_bank.json 무인 검증 게이트")
    parser.add_argument("--quiz-bank", type=Path, default=DEFAULT_QUIZ_BANK)
    parser.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    parser.add_argument("--briefs-dir", type=Path, default=DEFAULT_BRIEFS_DIR)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except ValueError as exc:
        print("quiz_lint: FAIL")
        print("errors: 1")
        print(f"ERROR [ARGUMENTS] cli: {exc}")
        return 1

    try:
        report = lint_quiz_bank(args.quiz_bank, args.glossary, args.briefs_dir)
        print(report.format())
        return 0 if report.ok else 1
    except Exception as exc:  # Keep unattended sessions on the documented 0/1 contract.
        print("quiz_lint: FAIL")
        print(f"quiz_bank: {Path(args.quiz_bank).resolve()}")
        print("errors: 1")
        print(f"ERROR [UNEXPECTED] validator: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
