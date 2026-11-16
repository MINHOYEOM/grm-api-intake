"""[기준 조항 원문 2026-11-15] `/clauses/` 트리 — 로더·뷰·근거 조항 해석기(순수 함수).

정본: `web/data/clauses/{mfds_gmp,cfr21,eu_gmp,ich}.json`(clause_text_collect.py · 조항 단위 원문, 무절단).
화면 세 층:
  · `/clauses/`                         — 원천별 문서 목록(색인)
  · `/clauses/{src}/{doc}/`             — 문서 1건의 조항 목차(소제목 포함)
  · `/clauses/{src}/{doc}/{slug}/`      — 조항 1개의 원문 전체 + 공식 원문 링크 + 이 조항을 인용한 용어
본문은 원문 그대로다(해외 기준은 영어, 식약처 고시는 국문) — 해외 기준의 국문 번역은 아직 없고 지어내지 않는다.
`source.lang` 이 "ko" 인 원천(식약처 고시)은 국문 트리에만 싣는다 — 영문 트리는 `for_lang(sources, "en")` 으로 뺀다.
목차의 미리보기만 화면에서 줄인다(데이터는 자르지 않는다).

근거 조항 해석기(`ref_href`)는 용어사전 `reg_refs` 라벨·지적의 `cfr_refs` 를 이 트리의 주소로 잇는다.
규칙은 `_reg_ref_url` 과 같은 규율 — **확실할 때만** 잇고, 애매하면 "" 다(틀린 링크가 무링크보다 나쁘다):
  · 단일 조항(`21 CFR 211.192(a)`·`ICH Q10 §2.7`·`EU GMP Part I Chapter 1 §1.12`) → 그 조항 페이지
  · 여러 조항·구간(`ICH Q7 §§7–11`)·문서만(`EU GMP Part I Chapter 4`) → 그 문서 목차(첫 번호가 있으면 그 자리 앵커)
  · 이번 묶음에 없는 문서(Annex 11·15 …) → ""
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "grm-clause-text/v1"
DATA_DIR = Path(__file__).resolve().parent / "data" / "clauses"
ROUTE = "clauses/"
#: 색인 순서 = 국내 고시 → 미국 법령 → EU 기준서 → ICH. 파일이 없으면 그 원천만 빠진다.
SOURCE_ORDER = ("mfds_gmp", "cfr21", "eu_gmp", "ich")
SOURCE_SLUG = {"mfds_gmp": "mfds-gmp", "cfr21": "cfr21", "eu_gmp": "eu-gmp", "ich": "ich"}
#: 화면 머리 표지(eyebrow) — 고유명사라 번역하지 않는다.
SOURCE_KICK = {"mfds_gmp": "KR · 식약처 고시", "cfr21": "US · 21 CFR", "eu_gmp": "EU · EudraLex Vol.4", "ich": "ICH"}
PREVIEW_CHARS = 140


def load_sources(data_dir: "Path | None" = None) -> list[dict[str, Any]]:
    """원천 파일들. 없거나 깨졌거나 스키마가 다르면 그 원천만 빠진다(빌드 계속)."""
    d = DATA_DIR if data_dir is None else data_dir
    out = []
    for key in SOURCE_ORDER:
        p = d / f"{key}.json"
        if not p.is_file():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if obj.get("schema") != SCHEMA or not obj.get("clauses"):
            continue
        out.append(obj)
    return out


def source_lang(obj: dict[str, Any]) -> str:
    """원천 본문의 언어(없으면 "en")."""
    return (obj.get("source") or {}).get("lang") or "en"


def for_lang(sources: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
    """그 언어 트리에 싣는 원천. 영문 트리는 국문 본문(식약처 고시)을 싣지 않는다."""
    if lang == "ko":
        return list(sources)
    return [o for o in sources if source_lang(o) != "ko"]


def _preview(text: str, n: int = PREVIEW_CHARS) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _depth(number: str) -> int:
    return number.count(".") if re.fullmatch(r"\d+(\.\d+)*", number) else 0


def build(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """원천 → 화면 뷰. 같은 입력이면 같은 출력(순서는 데이터 순서 그대로)."""
    views = []
    index: dict[tuple[str, str], dict[str, Any]] = {}   # (src, doc) → doc view
    total = 0
    for obj in sources:
        src = obj["source"]
        key = src["key"]
        sslug = SOURCE_SLUG.get(key, key.replace("_", "-"))
        docs = []
        for d in obj["docs"]:
            dpath = f"{ROUTE}{sslug}/{d['key']}/"
            rows = [c for c in obj["clauses"] if c["doc"] == d["key"] and not c.get("reserved")]
            clauses = []
            for c in rows:
                has_text = bool(c.get("text"))
                clauses.append({
                    "id": c["id"], "number": c["number"], "slug": c["slug"], "code": c["code"],
                    "title": c.get("title") or "", "path": c.get("path") or [],
                    "paras": [p for p in (c.get("text") or "").split("\n\n") if p.strip()],
                    "notes": c.get("notes") or [],
                    "url": c.get("url") or d["url"],
                    "href": f"{dpath}{c['slug']}/" if has_text else "",
                    "anchor": f"c-{c['slug']}",
                    "preview": "" if c.get("title") else _preview(c.get("text") or ""),
                    "depth": _depth(c["number"]),
                    "chars": c.get("chars") or 0,
                })
            pages = [c for c in clauses if c["href"]]
            for i, c in enumerate(pages):
                c["prev"] = pages[i - 1] if i else None
                c["next"] = pages[i + 1] if i + 1 < len(pages) else None
            dv = {"key": d["key"], "code": d["code"], "title": d.get("title") or "",
                  "url": d["url"], "version": d.get("version") or "",
                  "front_matter": [p for p in (d.get("front_matter") or "").split("\n\n") if p.strip()],
                  "path": dpath, "clauses": clauses, "n_pages": len(pages),
                  "source_key": key}
            docs.append(dv)
            index[(key, d["key"])] = dv
            total += len(pages)
        views.append({"key": key, "slug": sslug, "lang": source_lang(obj), "kick": SOURCE_KICK.get(key, ""),
                      "title": src.get("title") or "", "publisher": src.get("publisher") or "",
                      "url": src.get("url") or "", "license": src.get("license") or "",
                      "license_url": src.get("license_url") or "",
                      "retrieved": src.get("retrieved") or "", "docs": docs})
    for sv in views:
        for d in sv["docs"]:
            d["source"] = sv
    return {"sources": views, "index": index, "total": total}


def tree_paths(view: dict[str, Any]) -> set[str]:
    """이 트리가 실제로 내는 경로(색인 + 문서 + 본문 있는 조항). sitemap·en_paths 가 이것만 본다."""
    if not view.get("sources"):
        return set()
    out = {ROUTE}
    for sv in view["sources"]:
        for d in sv["docs"]:
            out.add(d["path"])
            out |= {c["href"] for c in d["clauses"] if c["href"]}
    return out


def pages(view: dict[str, Any]):
    """(문서 뷰, 조항 뷰) — 본문이 있는 조항만(소제목뿐인 번호는 목차에만 선다)."""
    for sv in view.get("sources") or []:
        for d in sv["docs"]:
            for c in d["clauses"]:
                if c["href"]:
                    yield d, c


# ── 근거 조항 해석기 ─────────────────────────────────────────────────────────


def _find(view: dict[str, Any], src: str, doc: str, number: str) -> "dict[str, Any] | None":
    d = view.get("index", {}).get((src, doc))
    if not d:
        return None
    for c in d["clauses"]:
        if c["number"] == number:
            return c
    return None


def _doc_or_clause(view: dict[str, Any], src: str, doc: str, tail: str) -> str:
    """문서 + 꼬리('§1.12'·'§§7–11'·'Glossary'·'') → 주소."""
    d = view.get("index", {}).get((src, doc))
    if not d:
        return ""
    tail = tail.strip()
    if not tail:
        return d["path"]
    if re.fullmatch(r"Glossary", tail, re.I):
        for c in d["clauses"]:
            if c["number"] == "Glossary" or c["title"].strip().upper() == "GLOSSARY":
                return c["href"] or f"{d['path']}#{c['anchor']}"
        return d["path"]
    m = re.fullmatch(r"§\s*(\d+(?:\.\d+)*)", tail)
    if m:  # 단일 조항
        c = _find(view, src, doc, m.group(1))
        if c:
            return c["href"] or f"{d['path']}#{c['anchor']}"
        return d["path"]
    m = re.match(r"§§?\s*(\d+(?:\.\d+)*)", tail)
    if m:  # 여러 조항 — 문서 목차, 첫 번호 자리로
        c = _find(view, src, doc, m.group(1))
        return f"{d['path']}#{c['anchor']}" if c else d["path"]
    return d["path"]


_CFR_SEC = re.compile(r"^21 CFR (\d{1,4}\.\d+)(?:\([A-Za-z0-9]+\))*$")
_CFR_PART = re.compile(r"^21 CFR Part (\d{1,4})$")
_EU_CH = re.compile(r"^EU GMP Part I,? Chapter (\d)\b(.*)$")
_EU_AN1 = re.compile(r"^EU GMP Annex 1\b(?!\d)(.*)$")
_MFDS_RULE = "의약품 제조 및 품질관리에 관한 규정"
_MFDS_BYL = re.compile(r"^" + _MFDS_RULE + r" \[별표 (\d+)(?:의(\d+))?\]")
_MFDS_ART = re.compile(r"^" + _MFDS_RULE + r" 제(\d+)조(?:의(\d+))?(?![\d의])")
_ICH = re.compile(r"^ICH (Q7|Q9(?:\(R1\))?|Q10)(?=\s|$)(.*)$")


def ref_href(view: dict[str, Any], label: str) -> str:
    """근거 조항 라벨 → `/clauses/` 안의 주소(사이트 루트 기준 상대경로). 모르면 ""."""
    label = (label or "").strip()
    if not label or not view.get("index"):
        return ""
    m = _CFR_SEC.match(label)
    if m:
        sec = m.group(1)
        c = _find(view, "cfr21", sec.split(".")[0], sec)
        return (c or {}).get("href") or ""
    m = _CFR_PART.match(label)
    if m:
        d = view["index"].get(("cfr21", m.group(1)))
        return d["path"] if d else ""
    m = _EU_CH.match(label)
    if m:
        return _doc_or_clause(view, "eu_gmp", f"ch{m.group(1)}", m.group(2))
    m = _EU_AN1.match(label)
    if m:
        return _doc_or_clause(view, "eu_gmp", "annex1", m.group(1))
    m = _MFDS_BYL.match(label)
    if m:  # 별표는 문서 목차로(별표 안 번호는 라벨에 거의 없고, 있어도 표기가 제각각이다)
        doc = f"byl{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")
        d = view["index"].get(("mfds_gmp", doc))
        return d["path"] if d else ""
    m = _MFDS_ART.match(label)
    if m:
        num = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        c = _find(view, "mfds_gmp", "rule", num)
        return (c or {}).get("href") or ""
    m = _ICH.match(label)
    if m:
        doc = {"Q7": "q7", "Q9": "q9", "Q9(R1)": "q9", "Q10": "q10"}[m.group(1)]
        tail = m.group(2).strip()
        if tail.lower().startswith("glossary"):
            tail = "Glossary"
        return _doc_or_clause(view, "ich", doc, tail)
    return ""


def cfr_href(view: dict[str, Any], section: str) -> str:
    """`211.192` → 그 조항 원문 페이지(없으면 "")."""
    return ref_href(view, f"21 CFR {section}")


def terms_by_href(view: dict[str, Any], glossary_terms: "list[dict[str, Any]] | None",
                  lang: str) -> dict[str, list[dict[str, str]]]:
    """조항 페이지 주소 → 그 조항을 근거로 든 용어(상호 진입 간선). 문서 목차·앵커로 가는 라벨은 세지 않는다
    (조항 하나를 콕 집은 용어만 그 조항 페이지에 싣는다)."""
    out: dict[str, list[dict[str, str]]] = {}
    for t in glossary_terms or []:
        tid, tko = t.get("id") or "", t.get("term_ko") or ""
        if not tid or not tko:
            continue
        label = tko if lang == "ko" else (t.get("term_en") or tko)
        seen = set()
        for r in t.get("reg_refs") or []:
            lab = r if isinstance(r, str) else (r.get("label") or "")
            h = ref_href(view, lab)
            if h and "#" not in h and h.count("/") >= 4 and h not in seen:
                seen.add(h)
                out.setdefault(h, []).append({"id": tid, "label": label})
    return out
