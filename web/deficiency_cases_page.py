"""[해외 지적 사례 전체 2026-11-10] PMDA·MHRA 실사 지적 사례 전체 화면 + 조항·용어 페이지 블록.

데이터: web/data/deficiency_cases.json (deficiency_cases_refresh.py — service_role 읽기 스냅샷,
원천 표 deficiency_examples 134). 파일이 없으면 페이지·블록·진입 링크가 전부 조용히 빠진다.
순수 함수만 둔다 — 네트워크·DB·now() 0(render.py 순수성 가드). 화면 문구는 템플릿 `_()` 에만 있다
(i18n 키 수집기가 템플릿·render.py 만 읽는다) — 이 모듈은 데이터만 만든다.

원천 12,093행의 세 갈래와 화면:
  · PMDA 연차보고서 별첨(영문판 111행) → /findings/examples/pmda/   지적 문장 전부
    일본어판(110행)은 같은 지적의 일본어 원문이다 — 문장을 두 번 싣지 않고 연도별 원문 파일 링크로 낸다.
  · MHRA Trend PDF 예시 글머리(371행) → /findings/examples/mhra/   지적 문장 전부
  · MHRA 2018·2019 xlsx(11,501행, **문장 없음** — 익명 실사 ID·등급·EU GMP 조항뿐)
    → /findings/examples/mhra-data/  조항(문단)별 Critical·Major·Other 횟수 집계표
    문장이 없는 행을 한 줄씩 늘어놓으면 읽을 것이 없다 — 행 전부를 빠짐없이 세어 조항 표로 낸다.

본문 규율: 원문(영어) 그대로. 번역·요약·해석을 새로 만들지 않는다(기존 지적 사례 화면과 같은 필드 —
지적 문장·기관·연도·기관 분류·원문 파일 링크). 긴 문장은 페이지 무게 때문에 CAP 에서 자르고 원문
파일 링크를 남긴다. 우리 분류(grm_category)는 수집기의 결정론 매핑 결과 그대로다.

블록 매칭(근거가 글자로 있는 것만, 애매하면 블록 없음):
  · 조항 페이지(21 CFR) — 지적 문장에 그 21 CFR 조항이 적혀 수집기가 clause_keys 로 뽑은 행만(완전 일치).
  · 용어 페이지 — 표제어(괄호 속 약어 포함)·별칭이 **낱말 단위로 그대로** 들어 있는 문장만.
    너무 흔한 낱말(일치 > MAX_TERM_MATCHES)·짧은 소문자 낱말은 주제를 가르지 못해 블록을 만들지 않는다.
    그리고 용어의 관련 조항이 'EU GMP (Part I) Chapter N / Annex N' 이면 MHRA 집계표의 그 장 횟수 한 줄.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

SNAPSHOT_FILE = Path(__file__).resolve().parent / "data" / "deficiency_cases.json"
SCHEMA = "grm-deficiency-cases/v1"

HUB_ROUTE = "findings/examples/"
ROUTES = {
    "PMDA": "findings/examples/pmda/",
    "MHRA": "findings/examples/mhra/",
    "MHRA_DATA": "findings/examples/mhra-data/",
}

# 출처·이용 조건 고지(partials/deficiency_cases_credit.html). 원문 목록 페이지는 수집기
# collect_deficiency_examples.PMDA_INDEX·MHRA_INDEX 와 같은 값이어야 한다(테스트가 대조).
CREDIT: dict[str, dict[str, Any]] = {
    "PMDA": {
        "pages": [
            {"title": "PMDA GMP (English)", "lang": "en",
             "url": "https://www.pmda.go.jp/english/review-services/gmp-qms-gctp/0007.html"},
            {"title": "PMDA GMP (日本語)", "lang": "ja",
             "url": "https://www.pmda.go.jp/review-services/gmp-qms-gctp/gmp/0011.html"},
        ],
        "license": "PDL1.0",
        "license_url": "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0",
    },
    "MHRA": {
        "pages": [
            {"title": "Good manufacturing practice inspection deficiencies", "lang": "en",
             "url": "https://www.gov.uk/government/statistics/"
                    "good-manufacturing-practice-inspection-deficiencies"},
        ],
        "license": "Open Government Licence v3.0",
        "license_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    },
}

CAP = 2000            # 페이지 한 행 본문 상한(글자). 넘으면 낱말 경계에서 자르고 원문 파일 링크.
BLOCK_CAP = 300        # 조항·용어 블록 인용 상한
BLOCK_MAX = 3          # 블록 인용 수
MAX_TERM_MATCHES = 30  # 용어 일치가 이보다 많으면 너무 흔한 낱말 — 블록 없음
MIN_WORD = 5           # 소문자 한 낱말 구절의 최소 길이(Lot·Batch 류 배제는 MAX_TERM_MATCHES 가 맡는다)

_HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")
_EU_KEY = re.compile(r"^EU GMP (Chapter|Annex) (\d{1,2})$")
_TERM_EU_REF = re.compile(r"^EU GMP (?:Part I )?(Chapter|Annex) (\d{1,2})(?:\s+§.*)?$")
_ABBR = re.compile(r"^[A-Z][A-Z0-9-]{1,7}$")


class SnapshotError(ValueError):
    """스냅샷 형식 위반 — 깨진 정본이 조용히 나가지 않게 빌드를 멈춘다."""


# ── 로드·검증 ──────────────────────────────────────────────────────────────────────────

def load_snapshot(path: Path = SNAPSHOT_FILE) -> "dict[str, Any] | None":
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise SnapshotError(f"deficiency_cases.json 스키마 불일치: {data.get('schema') if isinstance(data, dict) else data!r}")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise SnapshotError("sources 없음")
    for key, s in sources.items():
        if s.get("agency") not in ("PMDA", "MHRA") or not str(s.get("url", "")).startswith("https://"):
            raise SnapshotError(f"출처 {key} 형식")
    for c in data.get("cases") or []:
        if c.get("src") not in sources:
            raise SnapshotError(f"사례 {c.get('id')}: 출처 {c.get('src')} 없음")
        if c.get("agency") not in ("PMDA", "MHRA") or not str(c.get("text") or "").strip():
            raise SnapshotError(f"사례 {c.get('id')}: 기관/본문")
    for r in data.get("clause_counts") or []:
        if not _EU_KEY.match(str(r.get("key") or "")):
            raise SnapshotError(f"조항 키 형식: {r.get('key')!r}")


# ── 공통 ─────────────────────────────────────────────────────────────────────────────

def clip(text: str, cap: int) -> tuple[str, bool]:
    """공백 정리 후 cap 글자 안에서 낱말 경계로 자른다. (본문, 잘렸나)."""
    t = " ".join((text or "").split())
    if len(t) <= cap:
        return t, False
    cut = t[:cap]
    sp = cut.rfind(" ")
    if sp > cap * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:") + " …", True


def year_label(year: Any, basis: str) -> str:
    return f"FY{year}" if basis == "jp_fiscal" else str(year)


def _cat_labels(labels: "dict[str, tuple[str, str]]", code: str, lang: str) -> str:
    pair = labels.get(code)
    if not pair:
        return ""
    return pair[1] if lang == "en" else pair[0]


def _case_row(c: dict[str, Any], src: dict[str, Any], labels, lang: str, cap: int) -> dict[str, Any]:
    text, cut = clip(c["text"], cap)
    loc = str(c.get("loc") or "")
    if c["agency"] == "PMDA":
        ref = f"No.{c['no']}" if c.get("no") else ""
    else:
        ref = loc.split(" #", 1)[0] if loc.startswith("p.") else ""
    return {
        "id": c["id"],
        "agency": c["agency"],
        "text": text,
        "cut": cut,
        "year": year_label(c.get("year") or src.get("year"), src.get("year_basis", "calendar")),
        "ref": ref,
        "acat": " ".join(str(c.get("acat") or "").split()),
        "cat": c.get("cat") or "",
        "cat_label": _cat_labels(labels, c.get("cat") or "", lang),
        "url": src["url"],
    }


def _visible(c: dict[str, Any], lang: str) -> bool:
    """원문이 영어인 행만 싣는다. 영어 트리에 한글이 새면 안 되므로 한글 섞인 행은 양쪽 다 뺀다."""
    return not _HANGUL.search(str(c.get("text") or "") + str(c.get("acat") or ""))


def _sort_key(c: dict[str, Any]) -> tuple:
    # 최신 연도 먼저 → 원천 안 순서(행 번호). 결정론.
    return (-int(c.get("year") or 0), str(c.get("src") or ""), int(c.get("row") or 0))


# ── 전체 사례 페이지(기관별) ─────────────────────────────────────────────────────────────

def cases_view(snap: "dict[str, Any] | None", agency: str,
               labels: "dict[str, tuple[str, str]]", lang: str) -> "dict[str, Any] | None":
    """기관 하나의 사례 전체 → 화면 뷰. 사례가 0이면 None(페이지 없음)."""
    if not snap:
        return None
    sources = snap["sources"]
    rows = [_case_row(c, sources[c["src"]], labels, lang, CAP)
            for c in sorted((c for c in snap.get("cases") or []
                             if c.get("agency") == agency and _visible(c, lang)), key=_sort_key)]
    if not rows:
        return None
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["cat"]] = counts.get(r["cat"], 0) + 1
    cats = [{"code": k, "label": _cat_labels(labels, k, lang), "n": n}
            for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if k and labels.get(k)]
    years = sorted({r["year"] for r in rows}, reverse=True)
    files = [{"year": year_label(s["year"], s.get("year_basis", "calendar")), "url": s["url"],
              "lang": s.get("lang", "en"), "kind": s.get("kind", "")}
             for _, s in sorted(sources.items(), key=lambda kv: (-int(kv[1]["year"]), kv[1].get("lang", ""), kv[0]))
             if s["agency"] == agency and (agency == "PMDA" or s.get("kind") == "mhra_trend_pdf")]
    return {
        "agency": agency,
        "measured_on": str(snap.get("measured_on") or ""),
        "rows": rows,
        "total": len(rows),
        "cut": sum(1 for r in rows if r["cut"]),
        "cats": cats,
        "years": years,
        "files": files,
        "ja_rows": int(((snap.get("totals") or {}).get("by_kind") or {}).get("pmda_ja", 0))
        if agency == "PMDA" else 0,
    }


# ── MHRA 2018·2019 조항 집계 ───────────────────────────────────────────────────────────

def _eu_sort(key: str) -> tuple[int, int]:
    m = _EU_KEY.match(key)
    return (0 if m[1] == "Chapter" else 1, int(m[2])) if m else (9, 0)


def _para_sort(para: str) -> tuple:
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", para or "") if p)


def clause_counts_view(snap: "dict[str, Any] | None") -> "dict[str, Any] | None":
    """조항(장·부속서) → 문단별 등급 횟수. 횟수 = 그 문단이 지적에 인용된 행 수(원천 행 1개 = 1회)."""
    if not snap or not snap.get("clause_counts"):
        return None
    groups: dict[str, dict[str, Any]] = {}
    for r in snap["clause_counts"]:
        g = groups.setdefault(r["key"], {"key": r["key"], "label": r["key"].replace("EU GMP ", ""),
                                         "anchor": r["key"].replace("EU GMP ", "").lower().replace(" ", "-"),
                                         "critical": 0, "major": 0, "other": 0, "rows": []})
        row = {"para": r.get("para") or "", "critical": int(r.get("critical") or 0),
               "major": int(r.get("major") or 0), "other": int(r.get("other") or 0),
               "insp": int(r.get("insp") or 0)}
        row["total"] = row["critical"] + row["major"] + row["other"]
        for k in ("critical", "major", "other"):
            g[k] += row[k]
        g["rows"].append(row)
    out = []
    for key in sorted(groups, key=_eu_sort):
        g = groups[key]
        g["total"] = g["critical"] + g["major"] + g["other"]
        g["rows"].sort(key=lambda x: (-x["total"], _para_sort(x["para"])))
        out.append(g)
    meta = snap.get("mhra_data") or {}
    return {
        "measured_on": str(snap.get("measured_on") or ""),
        "groups": out,
        "rows_total": sum(g["total"] for g in out),
        "no_clause": int(meta.get("no_clause") or 0),
        "inspections": int(meta.get("inspections") or 0),
        "years": [str(y) for y in meta.get("years") or []],
        "files": [{"year": str(s["year"]), "url": s["url"]}
                  for _, s in sorted(snap["sources"].items(), key=lambda kv: -int(kv[1]["year"]))
                  if s.get("kind") == "mhra_xlsx"],
    }


def chapter_totals(snap: "dict[str, Any] | None") -> dict[str, dict[str, Any]]:
    """{'EU GMP Chapter 4': {'total', 'label', 'anchor'}} — 용어 페이지 한 줄용."""
    v = clause_counts_view(snap)
    return {g["key"]: {"total": g["total"], "label": g["label"], "anchor": g["anchor"]}
            for g in (v or {}).get("groups", [])}


# ── 허브(기존 /findings/examples/ 에 붙는 진입 블록) ─────────────────────────────────────

def hub_view(snap: "dict[str, Any] | None", lang: str) -> "dict[str, Any] | None":
    if not snap:
        return None
    pm = sum(1 for c in snap.get("cases") or [] if c.get("agency") == "PMDA" and _visible(c, lang))
    mh = sum(1 for c in snap.get("cases") or [] if c.get("agency") == "MHRA" and _visible(c, lang))
    cc = clause_counts_view(snap)
    cards = []
    if pm:
        cards.append({"key": "PMDA", "route": ROUTES["PMDA"], "n": pm})
    if mh:
        cards.append({"key": "MHRA", "route": ROUTES["MHRA"], "n": mh})
    if cc:
        cards.append({"key": "MHRA_DATA", "route": ROUTES["MHRA_DATA"], "n": cc["rows_total"]})
    if not cards:
        return None
    return {"cards": cards, "measured_on": str(snap.get("measured_on") or ""),
            "rows": int((snap.get("totals") or {}).get("rows") or 0)}


def routes_present(snap: "dict[str, Any] | None") -> list[str]:
    """이 빌드에 실제로 서는 경로(ko·en 같음 — 영문 원문만 싣는다)."""
    h = hub_view(snap, "en")
    return [c["route"] for c in (h or {}).get("cards", [])]


# ── 블록 매칭 ──────────────────────────────────────────────────────────────────────────

def _block_row(c: dict[str, Any], snap: dict[str, Any], labels, lang: str) -> dict[str, Any]:
    return _case_row(c, snap["sources"][c["src"]], labels, lang, BLOCK_CAP)


def _block_pick(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 짧은 문장 먼저(블록은 한눈에 읽혀야 한다) → id. 결정론.
    return sorted(cases, key=lambda c: (len(c["text"]), c["id"]))[:BLOCK_MAX]


def clause_block(snap: "dict[str, Any] | None", code: str,
                 labels: "dict[str, tuple[str, str]]", lang: str) -> "dict[str, Any] | None":
    """21 CFR 조항 페이지 — 지적 문장에 그 조항이 적힌 사례(수집기 clause_keys 완전 일치)."""
    if not snap or not code:
        return None
    hits = [c for c in snap.get("cases") or [] if code in (c.get("keys") or []) and _visible(c, lang)]
    if not hits:
        return None
    return {"kind": "clause", "code": code,
            "rows": [_block_row(c, snap, labels, lang) for c in _block_pick(hits)],
            "by_agency": _by_agency(hits, ""), "chapter": None}


def _by_agency(hits: list[dict[str, Any]], q: str) -> list[dict[str, Any]]:
    out = []
    for ag in ("PMDA", "MHRA"):
        n = sum(1 for c in hits if c["agency"] == ag)
        if n:
            out.append({"agency": ag, "n": n, "route": ROUTES[ag], "q": q})
    return out


def term_phrases(term: dict[str, Any]) -> list[tuple[str, bool]]:
    """용어 → [(구절, 대소문자 구분)]. 괄호 속 대문자 약어(GMP·CAPA)는 대소문자를 가린다."""
    out: list[tuple[str, bool]] = []
    head = str(term.get("term_en") or "")
    parens = re.findall(r"\(([^()]*)\)", head)
    base = re.sub(r"\([^()]*\)", " ", head)
    cands = [p.strip() for p in re.split(r"\s+/\s+|/", base) if p.strip()]
    for p in parens:
        for bit in re.split(r"[,/]", p):
            bit = bit.strip()
            if _ABBR.match(bit):
                cands.append(bit)
    cands += [str(a).strip() for a in term.get("aliases") or [] if str(a).strip()]
    seen: set[str] = set()
    for c in cands:
        c = " ".join(c.split())
        if not c or _HANGUL.search(c) or c.lower().startswith(("or ", "and ")):
            continue
        abbr = bool(_ABBR.match(c))
        if not abbr and " " not in c and len(c) < MIN_WORD:
            continue
        k = c if abbr else c.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append((c, abbr))
    return out


def _phrase_re(phrase: str, case: bool) -> "re.Pattern[str]":
    words = phrase.split()
    last = words[-1]
    if len(last) > 4 and last.endswith("s") and not last.endswith("ss"):
        words[-1] = last[:-1]  # 'Batch Records' 도 'batch record' 에 걸리게 — 복수 꼬리는 아래에서 선택
    body = r"\s+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?:s|es)?(?![A-Za-z0-9])", 0 if case else re.I)


def term_block(snap: "dict[str, Any] | None", term: dict[str, Any],
               labels: "dict[str, tuple[str, str]]", lang: str,
               chapters: "dict[str, dict[str, Any]] | None" = None) -> "dict[str, Any] | None":
    if not snap:
        return None
    phrases = term_phrases(term)
    hits: list[dict[str, Any]] = []
    first_q = ""
    if phrases:
        pats = [(p, _phrase_re(p, case)) for p, case in phrases]
        for c in snap.get("cases") or []:
            if not _visible(c, lang):
                continue
            for p, rx in pats:
                if rx.search(c["text"]):
                    hits.append(c)
                    break
        if len(hits) > MAX_TERM_MATCHES:
            hits = []
        if hits:
            # 페이지 검색어 = 일치 행이 가장 많은 구절(그 단어로 거르면 같은 행들이 나온다).
            first_q = max(phrases, key=lambda pc: (sum(1 for c in hits if _phrase_re(*pc).search(c["text"])),
                                                    -phrases.index(pc)))[0]
    chapter = None
    if chapters:
        for ref in term.get("reg_refs") or []:
            m = _TERM_EU_REF.match(" ".join(str(ref).split()))
            if m and (ch := chapters.get(f"EU GMP {m[1]} {int(m[2])}")) and ch["total"]:
                chapter = dict(ch, route=ROUTES["MHRA_DATA"])
                break
    if not hits and not chapter:
        return None
    return {"kind": "term", "rows": [_block_row(c, snap, labels, lang) for c in _block_pick(hits)],
            "by_agency": _by_agency(hits, first_q), "chapter": chapter}


# ── render.py 가 env 전역으로 거는 조회 함수 ─────────────────────────────────────────────

class Blocks:
    """템플릿이 부르는 조회기 — 스냅샷·라벨·용어 표를 한 번만 들고 페이지마다 계산한다.

    render.py 의 emit 호출(다른 PR 이 고치는 자리)을 건드리지 않으려고 env 전역 함수로 건다.
    """

    def __init__(self, snap: "dict[str, Any] | None", labels: "dict[str, tuple[str, str]]",
                 terms: Iterable[dict[str, Any]] = ()):
        self.snap = snap
        self.labels = labels
        self.terms = {t["id"]: t for t in terms if isinstance(t, dict) and t.get("id")}
        self.chapters = chapter_totals(snap)
        self._cache: dict[tuple[str, str, str], Any] = {}

    def __call__(self, kind: str, key: str, lang: str) -> "dict[str, Any] | None":
        ck = (kind, str(key), lang)
        if ck not in self._cache:
            if kind == "clause":
                v = clause_block(self.snap, str(key), self.labels, lang)
            elif kind == "term":
                t = self.terms.get(str(key))
                v = term_block(self.snap, t, self.labels, lang, self.chapters) if t else None
            elif kind == "hub":
                v = hub_view(self.snap, lang)
            else:
                v = None
            self._cache[ck] = v
        return self._cache[ck]
