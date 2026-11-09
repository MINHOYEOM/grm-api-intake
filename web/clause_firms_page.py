"""[FDA 지적 조항 × 업체] 업체 페이지·조항 페이지 블록 — 로더·매칭·뷰(순수 함수).

정본: `web/data/fda_citation_firms.json`(fda_citation_firms_refresh.py, 원장
`fda_inspection_citations` 133 을 제조소(FEI) 단위로 집계한 커밋 스냅샷). 파일이 없거나 형식이
다르면 두 블록이 조용히 꺼진다(빌드 계속).

두 블록:
  · 업체 페이지 — "이 업체가 FDA 실사에서 지적받은 21 CFR 조항": 조항(FDA 표기)별 지적이 나온
    실사 수 · 최근 실사일. 매칭된 제조소의 도시·국가를 함께 적는다.
  · 조항 페이지 — "이 조항으로 FDA 실사 지적을 받은 업체": 그 섹션을 인용한 실사 수 상위 N곳 +
    전체 제조소 수. 업체 페이지가 있고 아래 규칙으로 매칭된 곳만 링크한다.
  사실(조항·횟수·실사일)만 싣는다 — 해석·평가 문구 없음.

★매칭 규칙(오매칭 금지 — 애매하면 매칭하지 않는다). 업체 페이지는 **회사명 단위**(findings
  firm_key + 별칭)이고 원장은 **제조소 단위**(FEI·업체명·국가)다. 제조소 업체명이 아래를 **모두**
  만족할 때만 그 업체 페이지에 잇는다(수입경보 1단계 #1433 과 같은 골격):
  ① 정규화 키 일치: canonical_firm_key(normalize_firm_name(제조소명)) == 업체 페이지 키
     — 업체 페이지를 묶는 것과 같은 규칙·같은 별칭 사전(grm_findings).
  ② 법인격까지 일치: 제조소명의 strict 키(법인격 접미어를 남기고 동의어만 접음 —
     LIMITED=LTD·PRIVATE=PVT·CORPORATION=CORP·INCORPORATED=INC·COMPANY=CO, 선행 THE 제거)가
     그 업체 페이지 **문서 표기명 중 하나**의 strict 키와 같다. ①만 쓰면 원장 실측(2026-11-10)에서
     "Sandoz, Inc."(문서) ↔ "Sandoz Private Limited", "GlaxoSmithKline Inc."(캐나다) ↔
     "GlaxoSmithKline LLC", "AbbVie Corporation/Inc." ↔ "AbbVie Ltd" 가 붙는다.
  ③ 같은 strict 키가 원장에서 **두 나라 이상**에 있으면 그 이름은 매칭하지 않는다(findings
     문서에는 국가가 없어 가를 수 없다 — 실측 "Delta Pharma Inc."(캐나다)↔"Delta Pharma, Inc."(미국),
     "PLZ Corp"(미국)↔"PLZ Corp."(캐나다)).
  같은 이름의 제조소가 한 나라에 여럿이면(Biomat USA 14곳 등) 모두 그 업체로 본다 — 업체 페이지가
  회사 단위이기 때문이다. 대신 화면에 제조소 수와 도시·국가를 적어 어느 사업장인지 독자가 본다.
"""
from __future__ import annotations

import html as _html
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = "grm-fda-citation-firms/v1"
DATA_FILE = Path(__file__).resolve().parent / "data" / "fda_citation_firms.json"
#: 조항 페이지에 이름을 싣는 제조소 수(나머지는 전체 수로만 말한다).
CLAUSE_TOP_N = 10
#: 업체 페이지에 싣는 조항 표기 수 상한(넘으면 "외 N개"로 말한다).
FIRM_MAX_CODES = 20

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SUFFIX_SYNONYMS = {"LIMITED": "LTD", "PRIVATE": "PVT", "CORPORATION": "CORP",
                    "INCORPORATED": "INC", "COMPANY": "CO"}
_SECTION_RE = re.compile(r"^21 CFR (\d{3}\.\d+)")


def load_snapshot(path: "Path | None" = None) -> "dict[str, Any] | None":
    """스냅샷. 없거나 깨졌거나 스키마가 다르면 None(표시 생략, 빌드 계속).
    path 기본값은 호출 시점의 DATA_FILE(테스트가 모듈 상수를 바꿔 끼울 수 있게)."""
    path = DATA_FILE if path is None else path
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("schema_version") != SCHEMA_VERSION:
        return None
    if not _DATE_RE.match(str(obj.get("as_of") or "")):
        return None
    if not isinstance(obj.get("firms"), list) or not obj["firms"]:
        return None
    return obj


def strict_key(name: str) -> str:
    """법인격 접미어를 **남긴** 비교 키(동의어만 접는다). 선행 THE 만 뗀다.
    render.import_alert_strict_key 와 같은 규칙 + 둥근 아포스트로피(’)를 곧은 것으로."""
    s = unicodedata.normalize("NFKC", _html.unescape(name or "")).replace("’", "'")
    s = s.upper().replace("&", " AND ")
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    toks = [_SUFFIX_SYNONYMS.get(t, t) for t in s.split()]
    if toks and toks[0] == "THE":
        toks = toks[1:]
    return "".join(toks)


def _valid_firm(f: Any) -> bool:
    return (isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"].strip()
            and isinstance(f.get("inspections"), int) and f["inspections"] > 0
            and _DATE_RE.match(str(f.get("last") or "")) is not None
            and isinstance(f.get("codes"), list) and isinstance(f.get("sections"), list))


def firm_id(f: dict[str, Any]) -> str:
    """제조소 식별자(FEI + 이름). FEI 가 비어도 이름으로 갈린다."""
    return f"{f.get('fei') or ''}|{f['name']}"


def build_index(snapshot: "dict[str, Any] | None",
                loose_key: Callable[[str], str]) -> dict[str, list[dict[str, Any]]]:
    """loose 키(업체 페이지 키 규칙) → 매칭 후보 제조소. 규칙 ③(두 나라 이상인 strict 키)은
    여기서 통째로 뺀다. `loose_key` = canonical_firm_key(normalize_firm_name(·))."""
    if not snapshot:
        return {}
    by_strict: dict[str, list[dict[str, Any]]] = {}
    for f in snapshot.get("firms") or []:
        if not _valid_firm(f):
            continue
        k = strict_key(f["name"])
        if k:
            by_strict.setdefault(k, []).append(f)
    idx: dict[str, list[dict[str, Any]]] = {}
    for k, fs in by_strict.items():
        if len({(f.get("country") or "").strip().upper() for f in fs}) != 1:
            continue
        for f in fs:
            lk = loose_key(f["name"])
            if lk:
                idx.setdefault(lk, []).append(f)
    return idx


def match_firm(page_key: str, doc_names: Iterable[str],
               index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """업체 페이지(정본 키 + 문서 표기명들) → 매칭된 제조소(규칙 ①②). 순서 결정론."""
    strict = {strict_key(n) for n in doc_names if n}
    strict.discard("")
    out = [f for f in index.get(page_key, []) if strict_key(f["name"]) in strict]
    out.sort(key=lambda f: (-f["inspections"], firm_id(f)))
    return out


def link_firm_pages(snapshot: "dict[str, Any] | None",
                    names_by_key: dict[str, list[str]],
                    loose_key: Callable[[str], str]) -> dict[str, list[dict[str, Any]]]:
    """업체 페이지 키 → 매칭된 제조소(빈 결과는 싣지 않는다). 스냅샷이 없으면 {}."""
    index = build_index(snapshot, loose_key)
    if not index:
        return {}
    out = {}
    for key in sorted(names_by_key):
        sites = match_firm(key, names_by_key[key], index)
        if sites:
            out[key] = sites
    return out


def slug_if(slug: "str | None", allowed: "set[str] | None") -> "str | None":
    """그 언어 트리에 실제로 있는 업체 페이지일 때만 슬러그(allowed=None 이면 제한 없음)."""
    if not slug:
        return None
    return slug if allowed is None or slug in allowed else None


def _place(f: dict[str, Any]) -> str:
    city = (f.get("city") or "").strip()
    region = (f.get("state") or "").strip() if (f.get("country") or "") == "US" else ""
    country = (f.get("country_name") or f.get("country") or "").strip()
    return ", ".join(x for x in (city, region, country) if x)


def firm_view(sites: list[dict[str, Any]], snapshot: dict[str, Any], *,
              max_codes: int = FIRM_MAX_CODES) -> "dict[str, Any] | None":
    """업체 페이지 블록 뷰. 제조소마다 실사가 다르므로 조항별 실사 수는 합산이 정확하다."""
    if not sites:
        return None
    agg: dict[str, list[Any]] = {}
    for f in sites:
        for code, n, last in f["codes"]:
            a = agg.setdefault(code, [0, ""])
            a[0] += n
            a[1] = max(a[1], last)
    titles = snapshot.get("titles") or {}
    codes = sorted(({"code": c, "title": titles.get(c, ""), "inspections": n, "last": last,
                     "section": (_SECTION_RE.match(c).group(1) if _SECTION_RE.match(c) else "")}
                    for c, (n, last) in agg.items()),
                   key=lambda r: (-r["inspections"], r["code"]))
    first = min((f["first"] for f in sites if f.get("first")), default="")
    last = max(f["last"] for f in sites)
    return {
        "period": last if not first or first == last else f"{first}~{last}",
        "codes": codes[:max_codes],
        "more": max(0, len(codes) - max_codes),
        "n_codes": len(codes),
        "inspections": sum(f["inspections"] for f in sites),
        "first": first, "last": last,
        "sites": [{"name": f["name"], "place": _place(f), "fei": f.get("fei") or "",
                   "inspections": f["inspections"]} for f in sites],
        "as_of": snapshot["as_of"], "source": snapshot.get("source") or "",
    }


def clause_view(snapshot: "dict[str, Any] | None", section: str,
                slug_for: Callable[[dict[str, Any]], "str | None"], *,
                top_n: int = CLAUSE_TOP_N) -> "dict[str, Any] | None":
    """조항 페이지 블록 뷰 — 그 섹션을 인용한 제조소. 정렬: 실사 수 ↓, 최근 실사일 ↓, 이름.
    `slug_for(firm)` 은 매칭된 업체 페이지 슬러그(그 언어 트리에 있을 때만) 또는 None."""
    if not snapshot or not section:
        return None
    rows = []
    for f in snapshot.get("firms") or []:
        if not _valid_firm(f):
            continue
        hit = next((s for s in f["sections"] if s[0] == section), None)
        if hit:
            rows.append((f, hit[1], hit[2]))
    if not rows:
        return None
    rows.sort(key=lambda r: (-r[1], _neg_date(r[2]), r[0]["name"], firm_id(r[0])))
    top = [{"name": f["name"], "place": _place(f), "inspections": n, "last": last,
            "slug": slug_for(f) or ""} for f, n, last in rows[:top_n]]
    return {"section": section, "rows": top, "total": len(rows),
            "linked": sum(1 for r in top if r["slug"]),
            "as_of": snapshot["as_of"], "source": snapshot.get("source") or ""}


def _neg_date(d: str) -> str:
    """내림차순 정렬용 — 날짜 글자를 뒤집은 키(YYYY-MM-DD 고정폭)."""
    return "".join(chr(0x7F - ord(c)) for c in d)
