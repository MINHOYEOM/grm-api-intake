"""[구글 품질 2단계 · 2026-11-20] 해외 기준 조항(21 CFR·EU GMP·ICH)의 국문 참고 번역 — 덧씌우기·검사(순수 함수).

왜: 국문 트리의 조항 페이지 1,230장(21 CFR 236·EU GMP 579·ICH 415)은 본문이 영어 원문뿐이라 구글에서 뺐다
(clause_text_page.google_noindex_reason 의 D). 번역이 생기면 다음 빌드에서 자동으로 돌아온다.

흐름(비용 0 — 483 상세 번역 키트와 같은 꼴):
  1. 내보내기  `clause_translate_kit.py export`  — 아직 번역이 없거나 원문 해시가 바뀐 조항 → 작업 묶음 파일
  2. 채우기    세션·Codex(구독 경로)가 지침 docs/specs/clause_translation_brief.md 대로 `text_ko` 를 채운다
  3. 검사      `clause_translate_kit.py qa <파일>` + CI(tests/test_clause_translation.py)가 아래 `check` 로 거른다
  4. 적용      `translations/clause_outbox/*.json` 이 main 에 들어가면 빌드가 `apply` 로 조항 데이터에 덧씌운다
               (DB 쓰기 없음. 원문 정본 `web/data/clauses/*.json` 은 건드리지 않는다 — 수집기가 다시 돌아도 번역이 살아 있다)

outbox 항목: {"id": 조항 id, "sha": 번역한 원문의 해시, "title_ko": 제목 번역(원문에 제목이 있을 때), "text_ko": 본문 번역}
  · 원문 해시(`sha`)가 지금 원문과 다르면 그 번역은 버린다(낡은 번역을 싣지 않는다 → 다시 큐로 간다).
  · 같은 조항이 여러 파일에 있으면 **이름순 뒤 파일이 이긴다**(재번역은 뒤에 오는 이름으로).
  · 번역은 자르지 않는다. 원문 문단(`\\n\\n`) 수와 번역 문단 수가 같아야 한다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

OUTBOX_DIR = Path(__file__).resolve().parent.parent / "translations" / "clause_outbox"

# ── 덧씌우기 ──────────────────────────────────────────────────────────────


def load_outbox(outbox_dir: "Path | None" = None) -> dict[str, dict[str, Any]]:
    """조항 id → outbox 항목(이름순 뒤 파일이 이긴다). 깨진 파일은 건너뛴다(빌드 계속 — CI 가 따로 잡는다)."""
    d = OUTBOX_DIR if outbox_dir is None else outbox_dir
    out: dict[str, dict[str, Any]] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("id") and it.get("sha") and (it.get("text_ko") or "").strip():
                out[it["id"]] = {**it, "_file": p.name}
    return out


def apply(sources: list[dict[str, Any]], overlay: dict[str, dict[str, Any]]) -> int:
    """원천(clause_text_page.load_sources 결과)의 조항에 `text_ko`·`title_ko` 를 덧씌운다(제자리). 적용 수를 돌려준다.
    국문 원천(식약처)은 대상이 아니다. 원문 해시가 다른 번역은 버린다."""
    n = 0
    for obj in sources:
        if (obj.get("source") or {}).get("lang", "en") == "ko":
            continue
        for c in obj.get("clauses") or []:
            it = overlay.get(c.get("id"))
            if not it or not c.get("text") or it.get("sha") != c.get("sha"):
                continue
            c["text_ko"] = it["text_ko"]
            if (it.get("title_ko") or "").strip():
                c["title_ko"] = it["title_ko"].strip()
            n += 1
    return n


def paragraphs(text: str) -> list[str]:
    """화면과 같은 문단 나누기(clause_text_page.build 와 같은 규칙)."""
    return [p for p in (text or "").split("\n\n") if p.strip()]


# ── 검사(결정론, LLM 없음) ─────────────────────────────────────────────────
# 하나라도 걸리면 그 항목은 실격이다. 문구는 "종류:자리:값" — 사람이 바로 고칠 수 있게.

_HANGUL_RE = re.compile(r"[가-힣]")
_HANJA_RE = re.compile(r"[一-鿿㐀-䶿]")
_NUM_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")
#: 문단 머리 번호 — (a) (1) (i) (iv) 1. 1.2 4.10 등. 번역 문단도 같은 번호로 시작해야 한다.
_MARKER_RE = re.compile(r"^\s*(\((?:[a-z]{1,2}|\d{1,3}|[ivxlc]{1,6})\)|\d+(?:\.\d+)*\.?(?=\s)|[A-Z]\.(?=\s))")
#: 인용 — 원문 글자 그대로 번역에 있어야 한다(공백만 접어 비교).
_CITE_RE = re.compile(
    r"§§?\s*\d+(?:\.\d+)*(?:\([a-z0-9]+\))*"
    r"|\b\d+\s+CFR(?:\s+(?:[Pp]art|parts)\s+\d+)?(?:\s+\d+(?:\.\d+)+)?"
    r"|\b\d+\s+U\.S\.C\.\s+\d+[a-z]?"
    r"|\b\d+\s+FR\s+\d+"
    r"|\bICH\s+Q\d+(?:\([A-Z]\d\))?"
    r"|\b(?:Annex|Chapter)\s+\d+")
#: 대문자 약어·코드(GMP·HEPA·Q9·ISO) — 번역에 그대로 남아야 한다.
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Z])(?:[A-Z]{2,6}s?|[A-Z]+[0-9][A-Z0-9-]*|[A-Z][a-z]?\d+(?:\([A-Z]\d\))?)(?![A-Za-z0-9])")
_CODE_ALLOW = {
    # 문장 첫머리·강조·소제목으로 쓰이는 대문자 낱말(약어가 아니다 — 번역 대상)
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "NOTE", "NOTES", "SCOPE", "PART",
    "ANNEX", "THE", "AND", "OR", "OF", "FOR", "TO", "IN", "ON", "BY", "AN", "A", "IS", "BE", "AS", "AT", "IF", "NOT",
    "NO", "ALL", "ANY", "USE", "USED", "ITS", "MAY", "LIST", "TABLE", "FIGURE", "STEP", "STAGE", "GRADE", "GRADES",
    # 21 CFR 606·1271 의 표시 문구 예시("FOR AUTOLOGOUS USE ONLY")·양식 칸 — 우리말로 옮겨도 된다
    "DONOR", "ONLY", "TESTED", "DAYS", "LAST", "WITHIN", "LABEL", "ATTN", "ZIP", "XX",
}
_QUOTED_RE = re.compile(r"\"[^\"]{1,300}\"|“[^”]{1,300}”|‘[^’]{1,300}’|'[^']{1,300}'|「[^」]{1,300}」")
_LATIN_RUN_RE = re.compile(r"(?:[A-Za-z][A-Za-z'’.\-]*[\s,;:/]+){4,}[A-Za-z][A-Za-z'’.\-]*")
_FUNC_WORD_RE = re.compile(
    r"(?<![A-Za-z])(?:the|of|and|for|to|in|is|was|were|are|be|shall|should|not|with|that|by|on|from|this|which|have|has)"
    r"(?![A-Za-z])", re.IGNORECASE)
#: 길이 비율(국문 글자 수 / 원문 글자 수). 실측: 법령 국문은 원문의 0.4~0.7배. 0.25 미만이면 빠뜨린 것, 1.1 넘으면 덧붙인 것.
RATIO_MIN, RATIO_MAX = 0.25, 1.1
#: 문단 단위 하한 — 원문 문단이 이만큼 길면 번역 문단이 너무 짧을 수 없다(문단 하나를 통째로 줄이는 누락 방어).
PARA_MIN_EN_CHARS, PARA_RATIO_MIN = 120, 0.2

#: 용어 일관성 — 원문에 왼쪽 낱말이 있으면 번역에 오른쪽 가운데 하나가 있어야 한다.
#  기준: 용어사전 web/data/glossary.json(term_ko) + 483 상세 번역 지침(docs/specs/483_detail_translation_brief.md).
#  뜻이 하나로 정해진 것만 둔다(여러 뜻이 갈리는 낱말은 지침에만 적고 검사하지 않는다).
TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"quality control unit", ("품질관리부서",)),
    (r"quality unit", ("품질부서", "품질관리부서")),
    (r"deviations?", ("일탈",)),
    (r"validat(?:ion|ed|e)", ("밸리데이션",)),
    (r"qualification", ("적격성평가", "적격성 평가", "자격")),
    (r"contamination", ("오염",)),
    (r"quarantine", ("격리",)),
    (r"specifications?", ("규격",)),
    (r"calibrat(?:ion|ed|e)", ("교정",)),
    (r"reprocess(?:ing|ed)", ("재가공",)),
    (r"yields?", ("수율",)),
    (r"investigat(?:ion|ed|e)", ("조사",)),
    (r"aseptic", ("무균",)),
    (r"steril(?:e|ity|ized|ization|isation|ised|izing|ising)", ("무균", "멸균")),
    (r"written procedures?", ("절차",)),
    (r"labeling|labelling", ("표시",)),
    (r"closures?", ("마개",)),
    (r"in-process", ("공정",)),
    (r"batch(?:es)?|lots?", ("배치", "제조단위", "로트")),
    (r"components?", ("원료", "성분", "구성")),
    (r"audit trails?", ("감사추적",)),
    (r"data integrity", ("데이터 완전성",)),
    (r"change control", ("변경관리", "변경 관리")),
    (r"corrective and preventive actions?|CAPA", ("CAPA", "시정 및 예방조치", "시정조치")),
    (r"risk management", ("위험관리", "위험 관리")),
    (r"stability", ("안정성",)),
    (r"complaints?", ("불만",)),
    (r"recalls?", ("회수",)),
    (r"self[- ]inspections?", ("자율점검", "자체 점검")),
    (r"outsourc(?:ed|ing)", ("위탁",)),
)
_TERM_RES = tuple((re.compile(rf"(?<![A-Za-z]){pat}(?![A-Za-z])", re.IGNORECASE), ko) for pat, ko in TERMS)


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _nums(text: str) -> Counter:
    return Counter(_NUM_RE.findall(text or ""))


def _marker(text: str) -> str:
    m = _MARKER_RE.match(text or "")
    return m.group(1) if m else ""


def _codes(text: str) -> set[str]:
    out = set()
    for m in _CODE_RE.finditer(text or ""):
        tok = m.group(0)
        base = tok[:-1] if tok.endswith("s") and tok[:-1].isupper() and len(tok) > 2 else tok
        if base in _CODE_ALLOW or tok in _CODE_ALLOW:
            continue
        out.add(base)
    return out


def untranslated_runs(ko: str) -> list[str]:
    """번역되지 않은 영어 문장 조각(낱말 5개 이상 연속·기능어 2개 이상). 따옴표 안·인용 표기는 뺀다."""
    t = _QUOTED_RE.sub(" ", ko or "")
    t = _CITE_RE.sub(" ", t)
    return [m.group(0) for m in _LATIN_RUN_RE.finditer(t) if len(_FUNC_WORD_RE.findall(m.group(0))) >= 2]


def check(text: str, text_ko: str, title: str = "", title_ko: str = "") -> list[str]:
    """원문 ↔ 국문 번역 검사. 빈 목록이면 통과."""
    probs: list[str] = []
    ko = text_ko or ""
    if not _HANGUL_RE.search(ko):
        return ["no_hangul"]
    if _HANJA_RE.search(ko + (title_ko or "")):
        probs.append("hanja")
    if title.strip():
        if not (title_ko or "").strip():
            probs.append("title_missing")
        elif not _HANGUL_RE.search(title_ko):
            probs.append("title_no_hangul")
    en_p, ko_p = paragraphs(text), paragraphs(ko)
    if len(en_p) != len(ko_p):
        probs.append(f"para_count:{len(en_p)}!={len(ko_p)}")
    for i, (e, k) in enumerate(zip(en_p, ko_p), 1):
        miss = _nums(e) - _nums(k)
        if miss:
            probs.append(f"numbers_missing:p{i}:{','.join(sorted(miss))}")
        if _marker(e) and _marker(e) != _marker(k):
            probs.append(f"marker:p{i}:{_marker(e)}!={_marker(k) or '-'}")
        kws = _ws(k)
        for cite in (_ws(m.group(0)) for m in _CITE_RE.finditer(e)):
            if cite not in kws:
                probs.append(f"citation_missing:p{i}:{cite}")
        missing_codes = sorted(c for c in _codes(e) if c not in k)
        if missing_codes:
            probs.append(f"codes_missing:p{i}:{','.join(missing_codes[:8])}")
        if len(e) >= PARA_MIN_EN_CHARS and len(k) / len(e) < PARA_RATIO_MIN:
            probs.append(f"para_too_short:p{i}:{len(k)}/{len(e)}")
    r = len(ko) / max(1, len(text))
    if not RATIO_MIN <= r <= RATIO_MAX:
        probs.append(f"ratio:{r:.2f}")
    for rx, accepted in _TERM_RES:
        if rx.search(text) and not any(a in ko for a in accepted):
            probs.append(f"term:{rx.pattern.split('(?<![A-Za-z])')[1].split('(?![A-Za-z])')[0]}→{'|'.join(accepted)}")
    runs = untranslated_runs(ko)
    if runs:
        probs.append(f"untranslated:{runs[0][:60]}")
    return probs


def check_items(items: Iterable[dict[str, Any]], clauses_by_id: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """outbox 항목들 → {id: 문제 목록}(문제 있는 것만). 원문에 없는 id·해시 불일치도 문제로 센다."""
    bad: dict[str, list[str]] = {}
    for it in items:
        cid = it.get("id") or "?"
        c = clauses_by_id.get(cid)
        if c is None:
            bad[cid] = ["unknown_id"]
            continue
        if it.get("sha") != c.get("sha"):
            bad[cid] = [f"stale_sha:{it.get('sha')}!={c.get('sha')}"]
            continue
        p = check(c.get("text") or "", it.get("text_ko") or "", c.get("title") or "", it.get("title_ko") or "")
        if p:
            bad[cid] = p
    return bad


def foreign_clauses(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """번역 대상 조항(국문 원천 제외·본문 있는 것) — id → 조항(원문 정본)."""
    out = {}
    for obj in sources:
        if (obj.get("source") or {}).get("lang", "en") == "ko":
            continue
        for c in obj.get("clauses") or []:
            if c.get("text") and not c.get("reserved"):
                out[c["id"]] = c
    return out
