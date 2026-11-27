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
#: [구글 품질 2단계 2026-11-20] 국문 참고 번역 머리 안내(국문 트리 전용 — 영문 트리에는 번역 블록이 없다).
#  ICH 법적 고지: 번역본은 그 사실을 밝히고 ICH 가 승인한 것처럼 보이면 안 된다(SOURCES.md). EC 재이용 결정도 같은 취지.
#  문구는 뷰가 넘긴다 — 국문 전용 블록이라 en.json 키를 만들지 않는다(i18n lint: 템플릿에 감싸지 않은 한글 금지).
KO_LABEL = "국문 참고 번역"
KO_NOTE = {
    "ich": "GRM 이 옮긴 참고 번역입니다. ICH 가 승인한 번역이 아니며, 기준은 아래 영어 원문입니다.",
    "eu_gmp": "GRM 이 옮긴 참고 번역입니다. 유럽연합(EC)의 공식 번역이 아니며, 기준은 아래 영어 원문입니다.",
    "_": "GRM 이 옮긴 참고 번역입니다. 법적 효력은 아래 영어 원문에 있습니다.",
}


def load_sources(data_dir: "Path | None" = None,
                 translations_dir: "Path | None" = None) -> list[dict[str, Any]]:
    """원천 파일들. 없거나 깨졌거나 스키마가 다르면 그 원천만 빠진다(빌드 계속).

    [구글 품질 2단계 2026-11-20] 정본 폴더(data_dir 생략)를 읽을 때는 국문 참고 번역
    (`translations/clause_outbox/*.json`)을 원문 해시가 같은 조항에만 덧씌운다(clause_translation.apply).
    다른 폴더를 읽을 때는 translations_dir 을 줄 때만 덧씌운다."""
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
    if data_dir is None or translations_dir is not None:
        import clause_translation
        clause_translation.apply(out, clause_translation.load_outbox(translations_dir))
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
                    # [구글 품질 1단계 2026-11-19] 국문 번역(2단계가 채운다). 있으면 국문 페이지가 구글에 돌아온다.
                    "text_ko": c.get("text_ko") or "",
                    # [구글 품질 2단계 2026-11-20] 국문 참고 번역의 화면 몫 — 국문 트리 템플릿만 쓴다.
                    "title_ko": c.get("title_ko") or "",
                    "ko_paras": [p for p in (c.get("text_ko") or "").split("\n\n") if p.strip()],
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
                      "retrieved": src.get("retrieved") or "", "docs": docs,
                      "ko_label": KO_LABEL, "ko_note": KO_NOTE.get(key, KO_NOTE["_"])})
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


# [구글 품질 1단계 2026-11-19] 구글에서만 뺄 조항 페이지(googlebot noindex + sitemap.xml 제외).
#   D. 국문 트리인데 본문이 외국어 원문뿐(국문 번역 `text_ko` 없음) — 같은 영어 본문이 ko·en 두 주소에 선다.
#   E. 그 언어 페이지에 실리는 본문이 GOOGLE_MIN_CHARS 자 미만 — 원문 한 줄에 이전/다음 링크뿐인 페이지.
# 판정은 정본 데이터만 보므로, 2단계에서 `text_ko` 가 채워지면 다음 빌드에서 자동으로 돌아온다.
GOOGLE_MIN_CHARS = 300
#: [구글 품질 2단계 2026-11-20] 짧은 원문(E)이라도 이 조항을 인용한 공개 문서가 이만큼 있으면 페이지에
#: "이 조항을 인용한 지적" 블록이 서서 원문 한 줄짜리가 아니다 → E 에서 풀린다. 값은 `/findings/clause/`
#: 조항 페이지 임계(render.CLAUSE_MIN_DOCUMENTS = 3)와 같다. D(국문 미번역)는 풀지 않는다 — 국문 페이지의
#: 영어 원문 중복은 인용 블록으로 해소되지 않고, 국문 착지는 `/findings/clause/` 가 이미 맡는다.
GOOGLE_MIN_CITED_DOCS = 3


#: [구글 유입 2026-11-27] F. 공식 원문 그대로이고 GRM 이 덧붙인 것이 하나도 없는 조항 — 인용 지적·관련 용어·
#: 사례 모음 링크가 모두 없다. 구글 스팸 정책의 "원문을 그대로 옮긴 페이지"에 해당한다. 11-27 실측: 영문 조항
#: 1,230장 중 626장(EU GMP 320·ICH 151·21 CFR 155), 국문 식약처 고시 260장. 구글봇에만 noindex 라 빙·네이버는
#: 그대로다. 덧붙일 것이 생기면(인용 지적·용어 연결) 다음 빌드에서 저절로 돌아온다.
def has_grm_addition(cited_docs: int, terms: "list | None", cases_href: str,
                     key_terms: "list | None" = None, topic_cases: "list | None" = None) -> bool:
    """조항 페이지에 GRM 고유 블록(인용 지적·관련 용어·사례 모음 링크)이 하나라도 서는가."""
    return (cited_docs > 0 or bool(terms) or bool(cases_href)
            or bool(key_terms) or bool(topic_cases))


def google_noindex_reason(doc: dict[str, Any], clause: dict[str, Any], lang: str,
                          cited_docs: int = 0, bare: bool = False) -> str:
    """"" 이면 구글 색인 대상. 아니면 "untranslated"(D)·"short"(E)·"bare"(F).

    `cited_docs` = 그 언어 트리에서 이 조항을 인용한 공개 문서 수(`citing_findings` 의 documents).
    `bare` = `has_grm_addition` 이 거짓(원문뿐). 기본 False 라 종전 호출은 판정이 바뀌지 않는다."""
    foreign = (doc.get("source") or {}).get("lang", "en") != "ko"
    if lang == "ko" and foreign:
        if not clause.get("text_ko"):
            return "untranslated"
        n = len(clause["text_ko"])
    else:
        n = clause.get("chars") or 0
    if n < GOOGLE_MIN_CHARS and cited_docs < GOOGLE_MIN_CITED_DOCS:
        return "short"
    if bare:
        return "bare"
    return ""


# ── [구글 품질 2단계 2026-11-20] 이 조항을 인용한 지적 ──────────────────────────
# 21 CFR 조항 페이지는 공식 원문 한 문단 + 이전/다음 링크뿐이라 eCFR 사본과 다를 게 없었다.
# 우리 문서(483·경고서한)의 지적 `cfr_refs` 가 그 조항을 가리키면, 몇 건이 인용했는지와 최근 사례를
# 조항 원문 바로 아래에 싣는다 — 원문과 실제 지적을 한 화면에서 잇는 것이 이 페이지의 GRM 몫이다.
# 하위항 `(a)(1)` 은 섹션으로 접는다(조항 페이지가 섹션 단위다). 1271.x 처럼 네 자리 Part 도 받는다.
# ★`cfr_refs` 만 믿으면 안 된다(11-20 실측): 경고서한 지적의 `cfr_refs` 에는 그 단락 뒤의 권고문
# ("211.34 에 따라 컨설턴트를…")이나 다른 위반의 조항까지 섞여, 211.34 는 225건으로 세지는데 정작 그 조항을
# 지적한 문장은 0건이었다. 그래서 **본문 머리의 첫 인용**("…하지 않았다(21 CFR 211.192).")에 그 섹션이 있는
# 지적만 센다 — 지적 문장이 스스로 근거로 든 조항이다. 211.192 143→114, 211.84 194→184, 211.34 225→0.
CITE_SAMPLES = 3
_CITE_SEC = re.compile(r"^21 CFR (\d{1,4})\.(\d+)")
HEAD_MAX_CHARS = 600
_HEAD_END = re.compile(r"\)\.|\.\s|\n|$")
_SEC_NUM = re.compile(r"(?<![\d.])(\d{1,4}\.\d+)")


def headline_sections(text: str) -> set[str]:
    """지적 본문 **머리**(앞 HEAD_MAX_CHARS 자) 안의 첫 `21 CFR` 인용 구간(그 문장 끝까지)에 적힌 섹션 번호들.

    머리에 인용이 없으면 빈 집합 — 뒤쪽 권고문("consultant qualified as set forth in 21 CFR 211.34",
    실측 위치 698~4,508자)을 머리 인용으로 오독하지 않는다. 머리는 첫 문장이 아니라 글자 수로 끊는다 —
    FDA 는 규정 문구를 한 문장 인용한 뒤 둘째 문장 끝에 조항을 다는 일이 많다(211.84 실측: 첫 문장 끝
    104~217자, 인용 239~458자)."""
    t = text or ""
    i = t.find("21 CFR")
    if i < 0 or i > HEAD_MAX_CHARS:
        return set()
    j = _HEAD_END.search(t, i).start()
    return set(_SEC_NUM.findall(t[i:j]))


# ── [조항 인용 연결 2026-11-27] EU GMP·ICH 조항 표기 → (원천, 문서, 조항 번호) ─────────────────────────
# 정밀도가 우선이다. 지적 본문이 **문서 이름과 조항 번호를 함께** 적은 표기만 잇는다 — "Annex 1 에 부적합"
# 처럼 문서만 말한 것은 어느 조항인지 알 수 없어 잇지 않는다(11-27 실측: EU·ICH 언급 대부분이 문서 단위).
# 번호는 소수점이 있는 `N.M` 꼴만, 그 번호가 실제 조항으로 존재할 때만(citing_findings 의 _find) 센다.
_NUM = r"(\d{1,2}\.\d{1,3})(?!\d|\.\d)"
# 문서 이름과 번호 사이는 쉼표·콜론·§·"paragraph/point/section/clause" 중 하나가 있어야 한다(맨 공백만이면 "Annex 1 5.2 mg" 같은 오독).
_LEAD = r"(?:\s*[,:]\s*(?:paragraphs?|points?|sections?|clauses?|§)?\s*|\s*§\s*|\s+(?:paragraphs?|points?|sections?|clauses?)\s+)"
_EU_CH_REF = re.compile(r"\b(?:EU\s+GMP\s+(?:Part\s+I,?\s+)?|EudraLex\s+Vol(?:ume)?\.?\s*4,?\s+(?:Part\s+I,?\s+)?)Chapter\s+([1-9])\b" + _LEAD + _NUM, re.I)
_EU_AN1_REF = re.compile(r"\bAnnex\s*(?:1|I)\b(?:\s+of\s+(?:the\s+)?(?:EU\s+)?GMP(?:\s+Guidelines?)?)?" + _LEAD + _NUM, re.I)
_ICH_REF = re.compile(r"\bICH\s*Q\s*(7|10)\b" + _LEAD + _NUM, re.I)


def eu_ich_clause_refs(text: str) -> "list[tuple[str, str, str]]":
    """지적 원문 → [(원천, 문서 키, 조항 번호)]. 문서+번호가 함께 적힌 표기만; 애매하면 빈 목록."""
    out: list[tuple[str, str, str]] = []
    t = text or ""
    for m in _EU_CH_REF.finditer(t):
        if m.group(2).split(".")[0] == m.group(1):  # "Chapter 5 … 3.2" 처럼 장과 번호가 어긋나면 버린다
            out.append(("eu_gmp", f"ch{m.group(1)}", m.group(2)))
    for m in _EU_AN1_REF.finditer(t):
        out.append(("eu_gmp", "annex1", m.group(1)))
    for m in _ICH_REF.finditer(t):
        out.append(("ich", f"q{m.group(1)}", m.group(2)))
    return sorted(set(out))


def citing_findings(docs: "list[dict[str, Any]] | None", view: dict[str, Any], body,
                    *, doc_ok=None, max_samples: int = CITE_SAMPLES) -> dict[str, dict[str, Any]]:
    """조항 페이지 주소 → {"documents", "findings", "samples"}. 순수 함수(입력은 커밋 정본뿐).

    `body(finding)` 는 읽는 언어의 지적 본문("" 이면 그 지적은 이 언어 트리에서 세지 않는다).
    `doc_ok(doc)` 가 거짓인 문서는 세지 않는다(그 언어 트리에 문서 페이지가 없으면 링크가 깨진다).
    사례는 공개일 내림차순 → 문서 id → 지적 id 로 완전 결정론이고, 본문은 자르지 않는다."""
    rows: dict[str, list[dict[str, Any]]] = {}
    doc_ids: dict[str, set] = {}
    for d in docs or []:
        if doc_ok is not None and not doc_ok(d):
            continue
        for f in d.get("findings") or []:
            head = None
            secs = set()
            for r in f.get("cfr_refs") or []:
                m = _CITE_SEC.match(str(r or "").strip())
                if not m:
                    continue
                if head is None:  # 판정은 원문(없으면 국문) 기준 — 읽는 언어와 무관하게 같은 집합이다
                    head = headline_sections(f.get("text_orig") or f.get("text_ko") or "")
                sec = f"{m.group(1)}.{m.group(2)}"
                if sec in head:
                    secs.add((m.group(1), sec))
            targets = [("cfr21", part, sec) for part, sec in sorted(secs)]
            targets += eu_ich_clause_refs(f.get("text_orig") or "")
            if not targets:
                continue
            text = (body(f) or "").strip()
            if not text:
                continue
            for src, part, sec in targets:
                c = _find(view, src, part, sec)
                if not c or not c.get("href"):
                    continue
                rows.setdefault(c["href"], []).append({
                    "agency": d.get("agency") or "",
                    "firm_name": d.get("firm_name") or "",
                    "published_date": d.get("published_date") or "",
                    "doc_slug": d.get("slug") or "",
                    "document_id": d.get("document_id") or "",
                    "finding_id": f.get("finding_id") or "",
                    "body": text,
                })
                doc_ids.setdefault(c["href"], set()).add(d.get("document_id") or d.get("slug") or "")
    out = {}
    for href, rs in rows.items():
        rs.sort(key=lambda r: (r["published_date"], r["document_id"], r["finding_id"]), reverse=True)
        out[href] = {"documents": len(doc_ids[href]), "findings": len(rs), "samples": rs[:max_samples]}
    return out


def pages(view: dict[str, Any]):
    """(문서 뷰, 조항 뷰) — 본문이 있는 조항만(소제목뿐인 번호는 목차에만 선다)."""
    for sv in view.get("sources") or []:
        for d in sv["docs"]:
            for c in d["clauses"]:
                if c["href"]:
                    yield d, c


# ── [조항 덧붙임 2026-11-27] 핵심 용어 + 같은 주제의 실제 지적 ────────────────────────────────
# 구글이 원문 그대로 옮긴 페이지로 본 조항에 GRM 고유의 덧붙임을 만든다. 둘 다 독자가 이 조항을 읽을 때
# 실제로 쓸모가 있어야 한다 — 억지 블록 금지.
#  ① 핵심 용어: 조항 본문에 용어사전 용어가 **정확 일치**로 KEY_TERMS_MIN 개 이상 나올 때만(1~2개는 비운다).
#     흔한 용어(이 언어 트리 조항의 COMMON_TERM_RATIO 넘게 나오는 batch·quality 등)는 센 값에서 뺀다.
#  ② 같은 주제의 실제 지적: 조항 → 지적 분류 대응은 사람이 읽어 맞춘 아래 고정 표(장·Annex 단위)뿐이다.
#     표에 없으면 비운다. 조항을 직접 인용한 지적이 아니라 "같은 주제로 분류된 지적"임을 화면에 밝힌다.
KEY_TERMS_MIN = 3
KEY_TERMS_MAX = 6
COMMON_TERM_RATIO = 0.12
TOPIC_CASES_N = 3
TOPIC_BODY_MAX = 700  # 읽기 좋은 길이의 지적만 후보로 둔다(본문은 자르지 않는다)

_QMS = ["기타 품질시스템", "일탈/CAPA/조사"]
_ANNEX1_SECS = {
    "3": ["기타 품질시스템", "무균보증/무균공정"], "4": ["설비/시설", "무균보증/무균공정"],
    "5": ["설비/시설"], "6": ["설비/시설"], "7": ["교육/작업자"],
    "8": ["무균보증/무균공정", "오염/교차오염 관리"], "9": ["환경모니터링"],
    "10": ["시험실/품질관리", "무균보증/무균공정"],
}
_API_SECS = {
    "2": ["기타 품질시스템", "품질부서 관리감독"], "3": ["교육/작업자"], "4": ["설비/시설"],
    "5": ["설비/시설"], "6": ["문서화/기록관리"], "7": ["원자재/공급업체 관리"], "9": ["표시/포장"],
    "10": ["안정성/보관"], "11": ["시험실/품질관리"], "12": ["공정밸리데이션", "밸리데이션/적격성평가"],
    "13": ["규제보고/변경관리"],
}
_FPP_SECS = {
    "1": _QMS, "2": ["교육/작업자"], "3": ["설비/시설"], "4": ["문서화/기록관리"],
    "6": ["시험실/품질관리"], "7": ["원자재/공급업체 관리"], "8": ["불만/회수"],
}
#: (원천, 문서 키) → {최상위 번호 | "*": [지적 분류(category_label_ko)]}
TOPIC_TABLE: dict[tuple[str, str], dict[str, list[str]]] = {
    ("eu_gmp", "ch1"): {"*": _QMS},
    ("eu_gmp", "ch2"): {"*": ["교육/작업자"]},
    ("eu_gmp", "ch3"): {"*": ["설비/시설", "오염/교차오염 관리"]},
    ("eu_gmp", "ch4"): {"*": ["문서화/기록관리", "데이터 완전성"]},
    ("eu_gmp", "ch5"): {"*": ["오염/교차오염 관리", "원자재/공급업체 관리"]},
    ("eu_gmp", "ch6"): {"*": ["시험실/품질관리"]},
    ("eu_gmp", "ch7"): {"*": ["원자재/공급업체 관리"]},
    ("eu_gmp", "ch8"): {"*": ["불만/회수"]},
    ("eu_gmp", "annex1"): _ANNEX1_SECS,
    ("ich", "q7"): {**_API_SECS, "15": ["불만/회수"], "16": ["원자재/공급업체 관리"]},
    ("ich", "q10"): {"2": _QMS, "3": _QMS},
    ("mfds_gmp", "byl1"): _ANNEX1_SECS,
    ("mfds_gmp", "byl9"): {"*": ["컴퓨터화시스템", "데이터 완전성"]},
    ("mfds_gmp", "byl13"): {"5": ["밸리데이션/적격성평가"], "6": ["밸리데이션/적격성평가"],
                            "7": ["공정밸리데이션"], "9": ["공정밸리데이션"], "11": ["시험실/품질관리"],
                            "12": ["세척밸리데이션"], "13": ["규제보고/변경관리"]},
    ("mfds_gmp", "byl15"): _API_SECS,
    ("mfds_gmp", "byl17"): _FPP_SECS,
}
_HANGUL = re.compile(r"[가-힣]")


def topic_labels(src: str, doc: str, number: str) -> list[str]:
    """조항 → 같은 주제 지적 분류(고정 표). 표에 없으면 []."""
    t = TOPIC_TABLE.get((src, doc))
    if not t:
        return []
    top = str(number).split(".")[0]
    return list(t.get(top) or t.get("*") or [])


def _term_surfaces(t: dict[str, Any], lang: str) -> set[str]:
    names = [t.get("term_ko") if lang == "ko" else t.get("term_en")]
    names += [a for a in t.get("aliases") or [] if bool(_HANGUL.search(a)) == (lang == "ko")]
    out: set[str] = set()
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        base = re.sub(r"\s*\([^)]*\)", "", n).strip()
        for inner in re.findall(r"\(([^)]*)\)", n):
            out.update(y.strip() for y in re.split(r",|\bor\b|/", inner))
        out.update(y.strip() for y in re.split(r"\s/\s", base))
        out.add(n)
    return {s for s in out if len(s) >= 3 and not re.fullmatch(r"[\d\W]+", s)}


def _clause_plain(d: dict[str, Any], c: dict[str, Any], lang: str) -> str:
    """그 언어 트리에서 독자가 실제로 읽는 본문."""
    foreign = (d.get("source") or {}).get("lang", "en") != "ko"
    if lang == "ko" and foreign:
        return " ".join(c.get("ko_paras") or [])
    return " ".join(c.get("paras") or [])


def _gist(t: dict[str, Any], lang: str) -> str:
    s = ((t.get("easy_ko") if lang == "ko" else t.get("easy_en")) or "").strip()
    if len(s) <= 160:
        return s
    cut = re.search(r"[.。]\s", s)
    return s[:cut.start() + 1] if cut else s


def key_terms_by_href(view: dict[str, Any], glossary_terms: "list[dict[str, Any]] | None", lang: str,
                      term_ids: "set[str] | None" = None) -> dict[str, list[dict[str, str]]]:
    """조항 페이지 주소 → 본문에 정확 일치로 나온 핵심 용어(KEY_TERMS_MIN 이상일 때만). 순수·결정론.

    용어 순서는 조항 본문 속 첫 등장 위치. `term_ids` 가 있으면 용어 페이지가 있는 용어만 쓴다(링크 깨짐 방지)."""
    pats = []
    for t in glossary_terms or []:
        tid = t.get("id") or ""
        if not tid or (term_ids is not None and tid not in term_ids):
            continue
        ss = _term_surfaces(t, lang)
        if not ss or not (t.get("easy_ko") if lang == "ko" else t.get("easy_en")):
            continue
        rx = re.compile("|".join(r"(?<![A-Za-z0-9가-힣])" + re.escape(s) + r"(?![A-Za-z0-9가-힣])"
                                 for s in sorted(ss, key=len, reverse=True)), 0 if lang == "ko" else re.I)
        pats.append((t, rx))
    rows = []
    df: dict[str, int] = {}
    for d, c in pages(view):
        txt = _clause_plain(d, c, lang)
        if not txt:
            continue
        found = {}
        for t, rx in pats:
            m = rx.search(txt)
            if m:
                found[t["id"]] = (m.start(), t)
        for tid in found:
            df[tid] = df.get(tid, 0) + 1
        rows.append((c["href"], found))
    n = max(len(rows), 1)
    common = {tid for tid, k in df.items() if k > COMMON_TERM_RATIO * n}
    out: dict[str, list[dict[str, str]]] = {}
    for href, found in rows:
        keep = sorted(((pos, t) for tid, (pos, t) in found.items() if tid not in common),
                      key=lambda x: (x[0], x[1]["id"]))
        if len(keep) < KEY_TERMS_MIN:
            continue
        out[href] = [{"id": t["id"],
                      "label": (t.get("term_ko") if lang == "ko" else t.get("term_en")) or t["id"],
                      "gist": _gist(t, lang)} for _, t in keep[:KEY_TERMS_MAX]]
    return out


def topic_cases_by_href(docs: "list[dict[str, Any]] | None", view: dict[str, Any], body,
                        *, doc_ok=None, n: int = TOPIC_CASES_N) -> dict[str, list[dict[str, Any]]]:
    """조항 페이지 주소 → 같은 주제로 분류된 지적 사례 n건(고정 표 TOPIC_TABLE 에 있는 조항만).

    후보 = 표가 가리키는 분류의 공개 지적 가운데 읽는 언어 본문이 있고 TOPIC_BODY_MAX 자 이하인 것.
    조항마다 sha1(주소+지적 id) 순으로 서로 다른 업체 n건을 고른다 — 결정론이면서 조항마다 다른 사례가 선다."""
    import hashlib
    pool: dict[str, list[dict[str, Any]]] = {}
    for d in docs or []:
        if doc_ok is not None and not doc_ok(d):
            continue
        for f in d.get("findings") or []:
            lab = f.get("category_label_ko") or ""
            text = (body(f) or "").strip()
            if not lab or not text or len(text) > TOPIC_BODY_MAX:
                continue
            pool.setdefault(lab, []).append({
                "agency": d.get("agency") or "", "firm_name": d.get("firm_name") or "",
                "published_date": d.get("published_date") or "", "doc_slug": d.get("slug") or "",
                "document_id": d.get("document_id") or "", "finding_id": f.get("finding_id") or "",
                "body": text})
    out: dict[str, list[dict[str, Any]]] = {}
    for dv, c in pages(view):
        labs = topic_labels(dv["source_key"], dv["key"], c["number"])
        cand = [r for lab in labs for r in pool.get(lab, [])]
        if not cand:
            continue
        cand.sort(key=lambda r: hashlib.sha1((c["href"] + "|" + r["finding_id"]).encode()).hexdigest())
        picked, firms = [], set()
        for r in cand:
            fk = r["firm_name"] or r["finding_id"]
            if fk in firms:
                continue
            firms.add(fk)
            picked.append(r)
            if len(picked) == n:
                break
        if len(picked) == n:
            out[c["href"]] = sorted(picked, key=lambda r: (r["published_date"], r["finding_id"]), reverse=True)
    return out


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


def href_labels(view: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """주소 → (코드, 제목). 문서 목차 주소와 조항 페이지 주소 모두(문서 페이지 '인용한 기준 원문' 표시용)."""
    out: dict[str, tuple[str, str]] = {}
    for d in (view.get("index") or {}).values():
        out.setdefault(d["path"], (d["code"], d["title"]))
        for c in d["clauses"]:
            if c["href"]:
                out[c["href"]] = (c["code"], c["title"])
    return out


_ANNEX1_RE = re.compile(r"\bAnnex\s+1(?!\d)")
_GMP_RE = re.compile(r"\b(?:EU\s+)?GMP\b|EudraLex", re.I)
_ICH_TEXT_RE = re.compile(r"\bICH\s+(Q7|Q9|Q10)\b(?!\s*\(R1\)\s*§)")
_CFR_TEXT_RE = re.compile(r"\b21\s*CFR\s*(?:§\s*)?(\d{1,4}\.\d+)")
_CFR_LABEL_RE =re.compile(r"^(21 CFR \d{1,4}\.\d+)(?:\([A-Za-z0-9]+\))*$")


def cited_standard_labels(doc: dict[str, Any]) -> list[str]:
    """문서의 지적 본문이 **글자 그대로 적은** 기준 라벨(정규화). 추측하지 않는다.
    · 21 CFR 섹션: 지적의 cfr_refs
    · ICH Q7/Q9/Q10, EU GMP Annex 1: 영문 원문에 이름이 적힌 것(Annex 1 은 같은 지적에 GMP 가 함께 있을 때만)
    식약처 '[별표 N] 제X호' 는 일부러 뺀다 — 점검 평가표의 번호이지 고시 별표 번호가 아니다."""
    out: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        if label not in seen:
            seen.add(label)
            out.append(label)

    for f in doc.get("findings") or []:
        for r in f.get("cfr_refs") or []:
            m = _CFR_LABEL_RE.match((r or "").strip())
            if m:
                add(m.group(1))
        text = f.get("text_orig") or ""
        for m in _CFR_TEXT_RE.finditer(text):
            add(f"21 CFR {m.group(1)}")
        for m in _ICH_TEXT_RE.finditer(text):
            add(f"ICH {m.group(1)}")
        if _ANNEX1_RE.search(text) and _GMP_RE.search(text):
            add("EU GMP Annex 1")
    return out


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
