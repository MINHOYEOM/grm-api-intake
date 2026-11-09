"""실사 결과 카드 — 앞면 블록(`card["inspection"]`) 결정론 산출 (2026-11-08).

배경: 대표 지적 — "의미 없는 글 나열이 아니라 483 처럼 한눈에 들어와야 한다". 실사 결과
문서(EU/MHRA GMP 비준수 · FDA 483 · FDA 경고서한 · 식약처 GMP 실사)는 지적 목록이 이미
데이터에 있는데(483 `observations`, WL `violations`, 식약처 `rows`) 접힌 상세 안에만 있었고,
EU/MHRA 비준수는 2,000~3,000자 한 덩어리(`nature`)라 앞면에는 원문 앞 250자만 나갔다.

이 모듈은 카드의 `deterministic_detail` 에서 앞면 블록을 만든다:

    {"v": 1,
     "authority": {"country": "Malta"},          # EU 비준수만 — 실제 판정한 회원국(NCA)
     "counts": [{"sev": "critical", "n": 1}, ...],  # 원문 숫자만. 못 읽으면 빈 목록
     "items": [{"no": "C1", "sev": "critical", "text": "<표시 언어 한 줄>",
                "orig": "<원문 영문>", "area": "...", "clause": "...", "note": "..."}],
     "actions": ["prohibition_of_supply", ...],   # 판정서 양식의 고정 조치 제목
     "statements": [...]}                          # 같은 실사로 묶인 판정서(조립 단계가 채움)

★원칙
- **생성 0.** 개수·조치·목록 구조는 원문 문자열에 실재하는 표지(숫자, `1)`, ` - `, 양식 제목)
  에서만 읽는다. 못 읽으면 그 칸을 비운다 — 짐작으로 채우지 않는다.
- 산문형 판정서의 목록은 Routine 이 `ncr_ko.findings_ko` 로 낸 항목을 `inject_slots` 가
  원문 대조(span 이 원문에 글자 그대로 있는가)로 걸러 `deterministic_detail.findings_ko` 에
  둔 것만 쓴다. 여기서는 등급별 개수가 원문 개수와 맞는지 한 번 더 본다.
- 블록은 **새로 조립되는 호에만** 붙는다(`assemble_publish_brief`). 렌더는 블록이 있는 카드만
  새 틀로 그리므로 이미 발행된 호의 화면은 바뀌지 않는다(소급 없음).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

SEVERITIES = ("critical", "major", "other")
_SEV_PREFIX = {"critical": "C", "major": "M", "other": "O"}

# 영어 수사(판정서는 "eleven (11) major" 처럼 쓴다).
_WORD_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_NUM_TOKEN = r"(?:\d{1,3}|" + "|".join(k for k in _WORD_NUM if k not in ("a", "an")) + r")"
# 등급어 바로 앞(같은 절 안)의 마지막 수. "nine (9) deficiencies … one (1) of them were
# classified as critical and eight (8) as major" 처럼 등급어와 수 사이에 말이 끼어도 잡는다.
_NUM_RE = re.compile(r"\b(" + _NUM_TOKEN + r")\b", re.I)
# "resulted in a critical deficiency" — 관사 단수형. 등급어+결함어가 바로 붙을 때만 1 로 읽는다.
_ARTICLE_RE = re.compile(r"\b(?:in|identified|was|were|is|of)\s+an?\s+(critical|major)\s+"
                         r"(?:deficiency|finding|observation|non-?conformit)", re.I)
_SEV_WORD_RE = re.compile(r"\b(critical|major|other)\b", re.I)
_DEFICIENCY_NOUN_RE = re.compile(r"^\W*(?:\(?\d+\)?\s*)?(?:gmp\s+)?"
                                 r"(deficienc|finding|observation|shortcoming|non-?conformit|"
                                 r"and\b|,|\.|;)", re.I)


def _to_int(tok: str) -> int | None:
    t = tok.lower()
    if t.isdigit():
        return int(t)
    return _WORD_NUM.get(t)


def ncr_counts(nature: str) -> dict[str, int]:
    """판정서 위반내용(Nature) → 등급별 결함 개수. 원문에 숫자로 적힌 것만.

    각 등급어(critical/major/other)의 **첫 등장**에서, 같은 절(마침표·쌍반점·쌍점 이전 경계)
    안에 그 앞에 놓인 마지막 수를 읽는다. 등급어 뒤가 결함 명사(deficiency·finding…)이거나
    문장 경계일 때만 센다 — "major changes" 같은 다른 뜻을 세지 않는다.
    """
    text = " ".join((nature or "").split())
    out: dict[str, int] = {}
    for m in _ARTICLE_RE.finditer(text):
        out.setdefault(m.group(1).lower(), 1)
    for m in _SEV_WORD_RE.finditer(text):
        sev = m.group(1).lower()
        if sev in out:
            continue
        tail = text[m.end():m.end() + 30]
        head = text[max(0, m.start() - 80):m.start()]
        # "classified as Major." 처럼 문장 끝에 오는 등급어도 허용한다(뒤가 경계).
        if not (_DEFICIENCY_NOUN_RE.match(tail) or re.match(r"^\s*$", tail)):
            continue
        # 같은 절로 한정 — 마지막 경계(. ; :) 뒤만 본다. 단 "(9)" 안의 수는 경계가 아니다.
        cut = max(head.rfind(". "), head.rfind("; "), head.rfind(": "))
        clause = head[cut + 1:] if cut >= 0 else head
        nums = _NUM_RE.findall(clause)
        if not nums:
            continue
        n = _to_int(nums[-1])
        if n and n <= 60:              # 연도·날짜 조각을 개수로 읽지 않는다
            out[sev] = n
    return {s: out[s] for s in SEVERITIES if s in out}


# 판정서 양식의 조치 제목(EudraGMDP·MHRA 공통). 순서 = 화면 순서(중한 것 먼저).
_ACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("prohibition_of_supply", r"prohibition of supply"),
    ("recall", r"recall of batches"),
    ("ma_suspension", r"suspension of the marketing authori[sz]ation"),
    ("ma_revocation", r"revocation of the marketing authori[sz]ation"),
    ("cep_suspension", r"suspension or voiding of cep"),
    ("certificate_withdrawal", r"withdrawal,? of (?:the )?(?:current )?(?:valid |already expired )?gmp certificate"),
    ("clinical_trial_suspension", r"suspension of clinical trials?"),
)


def ncr_actions(action: str) -> list[str]:
    """판정서 조치(Action) → 조치 코드 목록. 양식의 고정 제목만 읽는다("Others" 는 싣지 않는다)."""
    text = " ".join((action or "").split()).lower()
    return [code for code, pat in _ACTION_PATTERNS if re.search(pat, text)]


# ── 목록 구조가 원문에 실재하는 경우만 결정론으로 나눈다 ─────────────────────────
_EN_HEAD = {"critical": r"critical deficienc(?:y|ies)\s*:", "major": r"major deficienc(?:y|ies)\s*:",
            "other": r"other deficienc(?:y|ies)\s*:"}
_KO_HEAD = {"critical": r"치명(?:적)?\s*결함\s*:", "major": r"(?:주요|중대)\s*결함\s*:",
            "other": r"기타\s*결함\s*:"}
_END = r"(?=(?:critical deficienc|major deficienc|other deficienc|in conclusion|치명(?:적)?\s*결함\s*:|" \
       r"(?:주요|중대)\s*결함\s*:|기타\s*결함\s*:|결론적으로)|$)"
_NUMBERED_SPLIT = re.compile(r"(?:^|\s)\d{1,2}\)\s+")
_DASH_INTRO_EN = re.compile(r"major (?:deficiencies|findings) (?:were|are) related to\s*:\s*(.*)$", re.I | re.S)
_DASH_INTRO_KO = re.compile(r"(?:major|주요|중대)\s*(?:\(major\)\s*)?결함은 다음과 관련(?:되어 있다|된다|이 있다)\.?\s*(.*)$",
                            re.I | re.S)
# 첫 문장 경계 — 국문 "…다." / 영문 ". " (약어 "e.g." 는 뒤가 소문자라 끊지 않는다).
_FIRST_SENT_KO = re.compile(r"^(.+?다\.)\s+(\S.*)$", re.S)
_FIRST_SENT_EN = re.compile(r"^(.+?[a-z0-9)]\.)\s+([A-Z].*)$", re.S)


def split_first_sentence(text: str) -> tuple[str, str]:
    """긴 항목 → (첫 문장, 나머지). 경계가 없으면 (전체, "")."""
    t = (text or "").strip()
    m = _FIRST_SENT_KO.match(t) or _FIRST_SENT_EN.match(t)
    return (m.group(1).strip(), m.group(2).strip()) if m else (t, "")


def _split_numbered(text: str, heads: dict[str, str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for sev in SEVERITIES:
        m = re.search(heads[sev] + r"(.*?)" + _END, text, re.I | re.S)
        if not m or not re.search(r"(?:^|\s)1\)\s", m.group(1)):
            continue
        parts = [p.strip() for p in _NUMBERED_SPLIT.split(" " + m.group(1)) if p.strip()]
        out += [(sev, p) for p in parts]
    return out


def _split_dash(text: str, intro: "re.Pattern[str]") -> list[tuple[str, str]]:
    m = intro.search(text)
    if not m or " - " not in " " + m.group(1):
        return []
    parts = [p.strip(" -") for p in (" " + m.group(1)).split(" - ")]
    return [("major", p) for p in parts if p]


def ncr_structured_items(nature: str, nature_ko: str = "") -> list[dict[str, str]]:
    """원문에 번호(`1)`)·대시(` - `) 목록이 있을 때만 나눈 지적 목록. 없으면 [].

    국문(`nature_ko`)을 같은 표지로 나눠 등급별 개수가 **영문과 똑같을 때만** 국문을 짝짓는다
    (`text`). 어긋나면 국문 없이 영문만 둔다 — 조립 단계가 Routine `findings_ko` 로 보충한다.
    """
    en = " ".join((nature or "").split())
    ko = " ".join((nature_ko or "").split())
    en_items = _split_numbered(en, _EN_HEAD) or _split_dash(en, _DASH_INTRO_EN)
    if not en_items:
        return []
    ko_items = (_split_numbered(ko, _KO_HEAD) or _split_dash(ko, _DASH_INTRO_KO)) if ko else []
    pair = ([s for s, _ in en_items] == [s for s, _ in ko_items])
    out = []
    for i, (sev, txt) in enumerate(en_items):
        head, rest = split_first_sentence(txt)
        row = {"sev": sev, "orig": head}
        if rest:
            row["orig_more"] = rest
        if pair:
            kh, kr = split_first_sentence(ko_items[i][1])
            row["text"] = kh
            if kr:
                row["more"] = kr
        out.append(row)
    return out


def _number(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {s: 0 for s in SEVERITIES}
    out = []
    for it in items:
        sev = it["sev"]
        seen[sev] += 1
        out.append({**it, "no": f"{_SEV_PREFIX[sev]}{seen[sev]}"})
    return out


def _counts_match(items: list[dict[str, Any]], counts: dict[str, int]) -> bool:
    """항목 수가 원문 개수와 맞는가. 원문이 개수를 말한 등급만 비교한다.

    단 'other'(기타 결함)는 판정서가 개수만 말하고 목록은 싣지 않는 경우가 많아(NKI: 기타 14건
    목록 없음) 항목이 0이면 비교하지 않는다.
    """
    for sev, n in counts.items():
        have = sum(1 for it in items if it["sev"] == sev)
        if sev == "other" and have == 0:
            continue
        if have != n:
            return False
    return True


def _ncr_block(detail: dict[str, Any]) -> dict[str, Any] | None:
    nature = str(detail.get("nature") or "")
    counts = ncr_counts(nature)
    actions = ncr_actions(str(detail.get("action") or ""))
    items: list[dict[str, Any]] = []
    structured = ncr_structured_items(nature, str(detail.get("nature_ko") or ""))
    llm = [f for f in (detail.get("findings_ko") or []) if isinstance(f, dict)]
    if structured:
        # 국문 짝이 없으면 같은 등급 순서의 Routine 한 줄로 보충(span 이 그 항목 안에 있어야 짝).
        by_sev: dict[str, list[dict[str, Any]]] = {}
        for f in llm:
            by_sev.setdefault(f.get("sev", ""), []).append(f)
        idx = {s: 0 for s in SEVERITIES}
        for it in structured:
            if "text" not in it:
                pool = by_sev.get(it["sev"], [])
                k = idx[it["sev"]]
                whole = it["orig"] + " " + it.get("orig_more", "")
                if k < len(pool) and " ".join(pool[k].get("span", "").split()) in whole:
                    it["text"] = pool[k]["ko"]
                idx[it["sev"]] += 1
            items.append(it)
    else:
        items = [{"sev": f["sev"], "orig": f.get("span", ""), "text": f["ko"]} for f in llm]
    if items and not _counts_match(items, counts):
        items = []                       # 개수가 원문과 다르면 목록을 싣지 않는다(fail-safe)
    # 등급 순서(치명→중대→기타)로 정렬 — 원문 등장 순서는 등급 안에서 보존(안정 정렬).
    items.sort(key=lambda it: SEVERITIES.index(it["sev"]))
    if not (counts or actions or items):
        return None
    block: dict[str, Any] = {
        "v": 1,
        "counts": [{"sev": s, "n": counts[s]} for s in SEVERITIES if s in counts],
        "items": _number(items),
        "actions": actions,
    }
    if detail.get("type") == "eu_gmp_ncr_statement" and detail.get("authority_country"):
        block["authority"] = {"country": str(detail["authority_country"])}
    return block


def _fda483_block(detail: dict[str, Any]) -> dict[str, Any] | None:
    obs = [o for o in (detail.get("observations") or []) if isinstance(o, dict) and o.get("deficiency")]
    if not obs:
        return None
    items = [{"no": str(o.get("number") or i + 1), "sev": "observation",
              "orig": str(o["deficiency"]),
              **({"text": str(o["deficiency_ko"])} if o.get("deficiency_ko") else {})}
             for i, o in enumerate(obs)]
    return {"v": 1, "counts": [{"sev": "observation", "n": len(items)}], "items": items, "actions": []}


def _wl_block(detail: dict[str, Any]) -> dict[str, Any] | None:
    rows = [v for v in (detail.get("violations") or []) if isinstance(v, dict) and v.get("statement")]
    if not rows:
        return None
    items = []
    for i, v in enumerate(rows):
        it = {"no": str(v.get("number") or i + 1), "sev": "violation", "orig": str(v["statement"])}
        if v.get("statement_ko"):
            it["text"] = str(v["statement_ko"])
        if v.get("citation"):
            it["clause"] = str(v["citation"])
        items.append(it)
    return {"v": 1, "counts": [{"sev": "violation", "n": len(items)}], "items": items, "actions": []}


# 식약처 결함 등급(원문 한국어) → 공통 코드. 화면 표기는 원문 낱말을 쓴다(아래 `label_ko`).
_MFDS_SEV = {"치명": "critical", "치명적": "critical", "중요": "major", "기타": "other"}


def _mfds_block(detail: dict[str, Any]) -> dict[str, Any] | None:
    rows = [r for r in (detail.get("rows") or []) if isinstance(r, dict) and r.get("summary")]
    if not rows:
        return None
    items = []
    for i, r in enumerate(rows):
        sev = _MFDS_SEV.get(str(r.get("severity") or "").strip(), "other")
        it = {"no": str(i + 1), "sev": sev, "text": str(r["summary"])}
        if r.get("area"):
            it["area"] = str(r["area"])
        if r.get("legal_basis"):
            it["clause"] = str(r["legal_basis"])
        if r.get("followup"):
            it["note"] = str(r["followup"])
        items.append(it)
    items.sort(key=lambda it: SEVERITIES.index(it["sev"]))
    counts: dict[str, int] = {}
    for it in items:
        counts[it["sev"]] = counts.get(it["sev"], 0) + 1
    return {"v": 1, "counts": [{"sev": s, "n": counts[s]} for s in SEVERITIES if s in counts],
            "items": items, "actions": []}


_BUILDERS = {
    "eu_gmp_ncr_statement": _ncr_block,
    "mhra_gmp_ncr_statement": _ncr_block,
    "fda_483_observations": _fda483_block,
    "wl_violations": _wl_block,
    "gmp_deficiencies": _mfds_block,
}


def build_inspection_block(card: dict[str, Any]) -> dict[str, Any] | None:
    """카드 1장 → 앞면 블록(없으면 None). 병합 목록 카드(483 다건 공개 등)는 대상이 아니다."""
    if (card.get("merged_count") or 1) > 1 and not card.get("_ncr_statements"):
        return None
    detail = card.get("deterministic_detail")
    if not isinstance(detail, dict):
        return None
    fn = _BUILDERS.get(str(detail.get("type") or ""))
    return fn(detail) if fn else None


# ── 같은 실사의 비준수 판정서 묶기 ───────────────────────────────────────────────
_NCR_TYPES = ("eu_gmp_ncr_statement", "mhra_gmp_ncr_statement")


def _ncr_merge_key(card: dict[str, Any]) -> "tuple[str, str, str] | None":
    dd = card.get("deterministic_detail")
    if not isinstance(dd, dict) or dd.get("type") not in _NCR_TYPES:
        return None
    nature = " ".join(str(dd.get("nature") or "").split())
    if len(nature) < 200:          # 짧은 정형문은 서로 다른 판정서끼리 같을 수 있다 — 묶지 않는다
        return None
    firm = " ".join(str(card.get("headline_target") or "").split()).lower()
    if not firm:
        return None
    return (str(dd.get("type")), firm, hashlib.sha1(nature.encode("utf-8")).hexdigest())


def merge_same_inspection_ncr(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 업체·위반내용 원문이 **글자까지 같은** 비준수 판정서 여러 장 → 대표 1장.

    판정 당국은 한 번의 실사 결과를 범위(허가의약품/임상시험약)·소재지별로 나눠 판정서를 여러 건
    낸다(2026-10-12 NKI-AVL: 판정서 4건 = 실사 1회, nature 2,960자 동일). 독자에게는 같은 문단이
    네 번 보였다. 대표 = 처음 나온 카드. 나머지 판정서는 대표의 `_ncr_statements` 에 문서번호·
    제품범위로 남고, 렌더가 머리줄에 "판정서 n건" 으로 적는다. 키가 하나라도 다르면 묶지 않는다.
    """
    groups: dict[tuple[str, str, str], list[int]] = {}
    for i, c in enumerate(cards):
        k = _ncr_merge_key(c)
        if k:
            groups.setdefault(k, []).append(i)
    drop: set[int] = set()
    out = [dict(c) for c in cards]
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        rep = out[idxs[0]]
        stmts = []
        for i in idxs:
            dd = cards[i].get("deterministic_detail") or {}
            stmts.append({"id": str(cards[i].get("id") or ""),
                          "scope": str(dd.get("product_scope") or "")})
        rep["_ncr_statements"] = stmts
        drop.update(idxs[1:])
    return [c for i, c in enumerate(out) if i not in drop]


def attach_inspection_blocks(cards: list[dict[str, Any]]) -> int:
    """채택 카드에 앞면 블록을 붙인다(in-place). 붙인 장 수를 돌려준다."""
    n = 0
    for c in cards:
        stmts = c.pop("_ncr_statements", None)
        block = build_inspection_block({**c, "_ncr_statements": stmts})
        if block is None:
            continue
        if stmts:
            block["statements"] = stmts
        c["inspection"] = block
        n += 1
    return n
