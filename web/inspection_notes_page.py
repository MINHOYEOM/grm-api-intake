"""[실사 대비 노트 2026-11-15] 주제별로 실사에서 실제 지적된 문장을 묶은 정적 자료 페이지.

데이터: web/data/inspection_notes/<id>.json (스키마 grm-inspection-note/v1). 노트는 미리 지어
사람이 인용문을 원문과 글자 그대로 대조한 뒤 커밋한다 — 빌드는 그 파일을 읽어 그릴 뿐이다.
폴더가 없거나 비면 페이지·진입 링크가 전부 조용히 빠진다.
순수 함수만 둔다 — 네트워크·DB·now() 0(render.py 순수성 가드). 화면 문구는 템플릿 `_()` 와
render.py 의 `tr()` 에만 있다(i18n 키 수집기가 그 둘만 읽는다) — 이 모듈은 데이터만 만든다.

화면: /inspection-notes/ (목록) · /inspection-notes/<id>/ (노트 1건), 영어판은 /en/ 아래 같은 경로.
  · 패턴 5개 × 인용 2개. 인용 아래 '출처종류 · 업체명 · 날짜 · 문서 보기'.
  · 문서 링크는 그 문서 페이지가 **그 언어 트리에 실제로 있을 때만** 건다(없으면 글자만).
  · 영어판은 `*_en` 필드만 쓴다 — 언어는 뷰가 정해 넘기고 템플릿은 고르지 않는다.
  · 인용문·문안은 고치지 않는다. `trailing_ellipsis` 인 인용만 끝에 ' …' 를 붙여 보여 준다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

NOTES_DIR = Path(__file__).resolve().parent / "data" / "inspection_notes"
SCHEMA = "grm-inspection-note/v1"
ROUTE = "inspection-notes/"

PATTERNS = 5            # 확정 형식: 패턴 5개
QUOTES = 2              # 패턴마다 인용 2개
SOURCES = ("FDA Warning Letter", "FDA 483")
# 화면 이름은 '실사 대비 노트'다. 아래 낱말은 노트 어디에도 싣지 않는다(대화형 도구가 아니다).
FORBIDDEN_WORDS = ("챗봇", "chatbot")
ELLIPSIS = " …"
# 목록의 분야 순서 — 한·영이 같은 목록을 쓴다(언어별로 따로 정렬하지 않는다).
# 여기 없는 분야는 맨 끝에 이름순으로 붙는다(빌드는 깨지지 않는다).
AREA_ORDER = (
    ("무균·오염 관리", "Sterility and contamination control"),
    ("시험실", "Laboratory"),
    ("데이터와 기록", "Data and records"),
    ("품질시스템", "Quality system"),
    ("공급·제조·포장", "Supply, manufacturing and packaging"),
)
_AREA_RANK = {name: i for i, pair in enumerate(AREA_ORDER) for name in pair}

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")
_PAIR_STR = ("area", "title", "search_q", "lead")
_PAIR_LIST = ("aliases", "checks")


class NoteError(ValueError):
    """노트 형식 위반 — 깨진 노트가 조용히 나가지 않게 빌드를 멈춘다."""


def note_route(note_id: str) -> str:
    return f"{ROUTE}{note_id}/"


# ── 로드·검증 ──────────────────────────────────────────────────────────────────────────

def load_notes(notes_dir: Path = NOTES_DIR) -> "list[dict[str, Any]]":
    """노트 전부(id 순). 폴더가 없으면 빈 목록. 형식이 어긋나면 NoteError."""
    if not notes_dir.is_dir():
        return []
    notes = []
    for path in sorted(notes_dir.glob("*.json")):
        note = json.loads(path.read_text(encoding="utf-8"))
        validate(note)
        if note["id"] != path.stem:
            raise NoteError(f"{path.name}: id({note['id']!r})와 파일 이름이 다르다")
        notes.append(note)
    return notes


def _need_str(obj: dict[str, Any], key: str, where: str) -> str:
    val = obj.get(key)
    if not isinstance(val, str) or not val.strip():
        raise NoteError(f"{where}: {key} 없음")
    return val


def _need_en(obj: dict[str, Any], key: str, where: str) -> None:
    """영어 필드에는 한글이 없어야 한다 — 영어 페이지에 한국어가 조용히 남는 것을 막는다."""
    if _HANGUL.search(_need_str(obj, key, where)):
        raise NoteError(f"{where}: {key} 에 한글이 있다")


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def validate(note: Any) -> None:
    if not isinstance(note, dict) or note.get("schema") != SCHEMA:
        raise NoteError(f"노트 스키마 불일치: {note.get('schema') if isinstance(note, dict) else note!r}")
    nid = note.get("id")
    if not isinstance(nid, str) or not _ID.match(nid):
        raise NoteError(f"노트 id 형식: {nid!r}")
    if not _DATE.match(str(note.get("as_of") or "")):
        raise NoteError(f"{nid}: as_of 형식")
    for stem in _PAIR_STR:
        _need_str(note, f"{stem}_ko", nid)
        _need_en(note, f"{stem}_en", nid)
    for stem in _PAIR_LIST:
        ko, en = note.get(f"{stem}_ko"), note.get(f"{stem}_en")
        if not isinstance(ko, list) or not isinstance(en, list):
            raise NoteError(f"{nid}: {stem}_ko/{stem}_en 형식")
        if any(not isinstance(s, str) or not s.strip() for s in ko + en):
            raise NoteError(f"{nid}: {stem} 빈 항목")
        if any(_HANGUL.search(s) for s in en):
            raise NoteError(f"{nid}: {stem}_en 에 한글이 있다")
    if not note["checks_ko"] or len(note["checks_ko"]) != len(note["checks_en"]):
        raise NoteError(f"{nid}: 점검 질문 한·영 수가 다르다")

    patterns = note.get("patterns")
    if not isinstance(patterns, list) or len(patterns) != PATTERNS:
        raise NoteError(f"{nid}: 패턴은 {PATTERNS}개여야 한다")
    for i, pat in enumerate(patterns, 1):
        where = f"{nid} 패턴 {i}"
        _need_str(pat, "title_ko", where)
        _need_en(pat, "title_en", where)
        quotes = pat.get("quotes")
        if not isinstance(quotes, list) or len(quotes) != QUOTES:
            raise NoteError(f"{where}: 인용은 {QUOTES}개여야 한다")
        for q in quotes:
            for key in ("finding_id", "document_id", "firm", "quote_ko"):
                _need_str(q, key, where)
            _need_en(q, "quote_en", where)
            if q.get("source") not in SOURCES:
                raise NoteError(f"{where}: 모르는 출처종류 {q.get('source')!r}")
            if not _DATE.match(str(q.get("date") or "")):
                raise NoteError(f"{where}: date 형식")

    clauses = note.get("clauses")
    if not isinstance(clauses, list):
        raise NoteError(f"{nid}: clauses 형식")
    for c in clauses:
        where = f"{nid} 조항 {c.get('key')!r}"
        _need_str(c, "key", where)
        # 인용 문장은 있거나 없거나다(key 와 note 만 있는 조항도 있다). 있으면 한·영·출처가 한 벌.
        if ("quote_ko" in c) != ("quote_en" in c):
            raise NoteError(f"{where}: 인용 한·영 쌍이 아니다")
        if "quote_ko" in c:
            _need_str(c, "quote_ko", where)
            _need_en(c, "quote_en", where)
            _need_str(c, "firm", where)
            if not _DATE.match(str(c.get("date") or "")):
                raise NoteError(f"{where}: date 형식")
        if ("note_ko" in c) != ("note_en" in c):
            raise NoteError(f"{where}: note 한·영 쌍이 아니다")
        if "note_ko" in c:
            _need_str(c, "note_ko", where)
            _need_en(c, "note_en", where)
        if "quote_ko" not in c and "note_ko" not in c:
            raise NoteError(f"{where}: 인용도 설명도 없다")

    further = note.get("further_reading")
    if not isinstance(further, list):
        raise NoteError(f"{nid}: further_reading 형식")
    for f in further:
        _need_str(f, "label_ko", f"{nid} 더 읽기")
        _need_en(f, "label_en", f"{nid} 더 읽기")
        if not str(f.get("url") or "").startswith("https://"):
            raise NoteError(f"{nid} 더 읽기: url 은 https 여야 한다")
    for key in ("glossary_ids", "related"):
        vals = note.get(key)
        if not isinstance(vals, list) or any(not isinstance(v, str) or not _ID.match(v) for v in vals):
            raise NoteError(f"{nid}: {key} 형식")

    for s in _strings(note):
        low = s.lower()
        for word in FORBIDDEN_WORDS:
            if word in low:
                raise NoteError(f"{nid}: 금지어 {word!r}")


def routes_present(notes: "list[dict[str, Any]] | None") -> "set[str]":
    """실제로 그릴 면 — 렌더와 테스트가 같은 함수를 부른다(목록 1 + 노트 N). 없으면 빈 집합."""
    if not notes:
        return set()
    return {ROUTE} | {note_route(n["id"]) for n in notes}


# ── 뷰 ────────────────────────────────────────────────────────────────────────────────

def _shown(text: str, trailing: Any) -> str:
    return text + ELLIPSIS if trailing else text


def first_sentence(lead: str) -> str:
    """lead 의 첫 문장(목록·용어 페이지 블록용). 문장 끝이 안 보이면 전체."""
    m = re.match(r"(.+?[.?!])(?:\s|$)", lead.strip())
    return m.group(1) if m else lead.strip()


def _aliases(note: dict[str, Any], lang: str) -> "list[str]":
    """'같은 말' — 한국어판은 국문 별칭 뒤에 영문 표기를 잇고, 영어판은 영문만. 제목과 같은 말은 뺀다."""
    title = note[f"title_{lang}"].casefold()
    pool = (note["aliases_ko"] + note["aliases_en"]) if lang == "ko" else note["aliases_en"]
    out, seen = [], {title}
    for a in pool:
        if a.casefold() not in seen:
            seen.add(a.casefold())
            out.append(a)
    return out


def note_view(note: dict[str, Any], lang: str, *,
              doc_slugs: "dict[str, str]",
              notes_by_id: "dict[str, dict[str, Any]]",
              glossary_terms: "dict[str, str]",
              source_labels: "dict[str, str]",
              search_href: str,
              abs_url: "Callable[[str], str]",
              copy_labels: "dict[str, str]") -> "dict[str, Any]":
    """노트 1건의 화면 뷰.

    doc_slugs      — {document_id: slug}. **이 언어 트리에 문서 페이지가 실제로 서는 것만**.
    notes_by_id    — 이어서 보기가 가리킬 수 있는 노트(없는 id 는 그리지 않는다).
    glossary_terms — {용어 id: 이 언어의 표제어}. 없는 용어는 링크하지 않는다.
    source_labels  — {출처종류(DB 값): 이 언어의 표기}.
    search_href    — '관련 지적 전체 보기' 링크(언어 트리 루트 기준 상대, 호출부가 인코딩).
    abs_url        — 언어 트리 루트 기준 경로 → 절대 URL(복사 글 전용).
    copy_labels    — 복사 글의 고정 문구 {"name","as_of"}.
    """
    sfx = f"_{lang}"

    def quote_view(q: dict[str, Any]) -> "dict[str, Any]":
        slug = doc_slugs.get(q["document_id"])
        return {
            "text": _shown(q[f"quote{sfx}"], q.get("trailing_ellipsis")),
            "source": source_labels[q["source"]],
            "firm": q["firm"],
            "date": q["date"],
            "doc_path": f"findings/doc/{slug}/" if slug else "",
        }

    patterns = [{"n": i, "title": p[f"title{sfx}"], "quotes": [quote_view(q) for q in p["quotes"]]}
                for i, p in enumerate(note["patterns"], 1)]
    clauses = [{
        "key": c["key"],
        "quote": _shown(c[f"quote{sfx}"], c.get("trailing_ellipsis")) if f"quote{sfx}" in c else "",
        "firm": c.get("firm", "") if f"quote{sfx}" in c else "",
        "date": c.get("date", "") if f"quote{sfx}" in c else "",
        "note": c.get(f"note{sfx}", ""),
    } for c in note["clauses"]]
    related = [{"title": notes_by_id[r][f"title{sfx}"], "path": note_route(r)}
               for r in note["related"] if r in notes_by_id and r != note["id"]]
    terms = [{"label": glossary_terms[g], "path": f"glossary/{g}/"}
             for g in note["glossary_ids"] if g in glossary_terms]

    path = note_route(note["id"])
    lines = [f"{copy_labels['name']}: {note[f'title{sfx}']} ({copy_labels['as_of']} {note['as_of']})",
             abs_url(path), ""]
    for p in patterns:
        lines.append(f"{p['n']}. {p['title']}")
        for q in p["quotes"]:
            src = " · ".join(x for x in (q["source"], q["firm"], q["date"],
                                         abs_url(q["doc_path"]) if q["doc_path"] else "") if x)
            lines.append(f"- \"{q['text']}\" ({src})")
        lines.append("")
    return {
        "id": note["id"],
        "path": path,
        "as_of": note["as_of"],
        "area": note[f"area{sfx}"],
        "title": note[f"title{sfx}"],
        "aliases": _aliases(note, lang),
        "lead": note[f"lead{sfx}"],
        "patterns": patterns,
        "clauses": clauses,
        "checks": list(note[f"checks{sfx}"]),
        "further": [{"label": f[f"label{sfx}"], "url": f["url"]} for f in note["further_reading"]],
        "search_q": note[f"search_q{sfx}"],
        "search_href": search_href,
        "related": related,
        "terms": terms,
        "copy_text": "\n".join(lines).rstrip() + "\n",
    }


def index_view(notes: "list[dict[str, Any]]", lang: str) -> "list[dict[str, Any]]":
    """목록 뷰 — 영역별로 묶는다. 영역은 AREA_ORDER 순(없는 영역은 끝에 이름순), 노트는 이름순."""
    sfx = f"_{lang}"
    groups: dict[str, list[dict[str, Any]]] = {}
    for n in notes:
        groups.setdefault(n[f"area{sfx}"], []).append({
            "title": n[f"title{sfx}"], "path": note_route(n["id"]),
            "summary": first_sentence(n[f"lead{sfx}"]),
        })
    return [{"area": area, "notes": sorted(items, key=lambda x: x["title"].casefold())}
            for area, items in sorted(
                groups.items(), key=lambda kv: (_AREA_RANK.get(kv[0], len(AREA_ORDER)), kv[0]))]


def glossary_links(notes: "list[dict[str, Any]]", lang: str) -> "dict[str, list[dict[str, str]]]":
    """{용어 id: [{"title","summary","path"}]} — 용어 페이지의 '실사 대비 노트' 블록 재료."""
    sfx = f"_{lang}"
    out: dict[str, list[dict[str, str]]] = {}
    for n in notes:
        for g in n["glossary_ids"]:
            out.setdefault(g, []).append({
                "title": n[f"title{sfx}"], "summary": first_sentence(n[f"lead{sfx}"]),
                "path": note_route(n["id"]),
            })
    return out
