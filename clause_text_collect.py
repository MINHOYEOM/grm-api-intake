#!/usr/bin/env python3
"""핵심 기준 조항 원문 → 조항 단위 정적 데이터(`web/data/clauses/*.json`).

왜: 자료실 554건은 제목·링크뿐이고, 지적과 연결된 '근거 조항'도 번호뿐이다. "근거가 뭐냐"에
답하려면 조항 문장이 있어야 한다(GRM_DB빈칸점검_2026-11-15 §3-6·순서 3).

저장 위치는 **DB 가 아니라 레포의 정적 파일**이다 — 무료 Nano DB 는 용량·IO 여유가 작다.
원문은 **자르지 않는다**(줄이는 건 화면의 일). 국문 번역은 이 도구가 하지 않는다 — 번역 대기
목록(`translation_queue.json`)만 남기고, 번역은 구독 경로(Codex 배치)가 나중에 한다.

첫 묶음(원천 · 공개 원문 · 이용 조건은 web/data/clauses/SOURCES.md):
  · cfr21   — 21 CFR Part 210·211·600·601·606·610, eCFR 공개 API(XML). 미국 연방정부 저작물 → 공중 영역.
  · eu_gmp  — EudraLex Vol.4 Part I 1~9장 + Annex 1(PDF). EC 재이용 결정 2011/833/EU → 출처 표시 조건 재이용 허용.
  · ich     — ICH Q7·Q9(R1)·Q10(PDF). ICH 법적 고지 → 저작권 표시 조건 복제 허용, 변경·번역은 표시.

사용:
    py -3.12 -X utf8 clause_text_collect.py --download-dir <임시폴더> [--only cfr21,eu_gmp,ich]
    (네트워크: ecfr.gov · health.ec.europa.eu · database.ich.org. 비용 0.)
★파일 이름을 `library_collect_*` 로 짓지 않는다 — library_staging_build.py 가 그 접두어를 자료실 플러그인으로
자동 발견하고 LIBRARY_SOURCE 계약이 없으면 주간 자료실 갱신 전체가 예외로 멈춘다.
PDF 파싱은 PyMuPDF(requirements.txt 고정판). 파서(순수 함수)는 줄 목록만 받으므로 테스트는 PDF 없이 돈다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "web" / "data" / "clauses"
SCHEMA = "grm-clause-text/v1"
UA = "Mozilla/5.0 (compatible; GRM-clause-text/1.0)"

ECFR_DATE = "2026-11-12"  # eCFR titles.json 의 title 21 up_to_date_as_of (수집 시점에 갱신)

CFR_PARTS = {
    "210": "Current Good Manufacturing Practice in Manufacturing, Processing, Packing, or Holding of Drugs; General",
    "211": "Current Good Manufacturing Practice for Finished Pharmaceuticals",
    # [2026-11-16 Part 600 묶음] 생물의약품 — 총칙·허가·혈액 CGMP·일반 기준.
    "600": "Biological Products: General",
    "601": "Licensing",
    "606": "Current Good Manufacturing Practice for Blood and Blood Components",
    "610": "General Biological Products Standards",
}

EU_DOCS = [  # (key, code, chapter_no, title, url) — url 은 자료실 eu_gmp.json 과 같은 공식 PDF
    ("ch1", "EU GMP Part I, Chapter 1", 1, "Pharmaceutical Quality System",
     "https://health.ec.europa.eu/document/download/e458c423-f564-4171-b344-030a461c567f_en?filename=vol4-chap1_2013-01_en.pdf"),
    ("ch2", "EU GMP Part I, Chapter 2", 2, "Personnel",
     "https://health.ec.europa.eu/document/download/11f4f8e6-a6e9-4897-afe3-f21e1dc56cb8_en?filename=2014-03_chapter_2.pdf"),
    ("ch3", "EU GMP Part I, Chapter 3", 3, "Premises and Equipment",
     "https://health.ec.europa.eu/document/download/18d76565-137b-41d2-a602-794527f708c1_en?filename=chapter_3.pdf"),
    ("ch4", "EU GMP Part I, Chapter 4", 4, "Documentation",
     "https://health.ec.europa.eu/document/download/104b3eb8-81a7-4858-9419-cb06562adb66_en?filename=chapter4_01-2011_en.pdf"),
    ("ch5", "EU GMP Part I, Chapter 5", 5, "Production",
     "https://health.ec.europa.eu/document/download/4a1fdb4f-6f6f-49c4-b264-8056e5bbe078_en?filename=chapter_5.pdf"),
    ("ch6", "EU GMP Part I, Chapter 6", 6, "Quality Control",
     "https://health.ec.europa.eu/document/download/c74c8720-27bf-4252-808f-d65a206a90bb_en?filename=2014-11_vol4_chapter_6.pdf"),
    ("ch7", "EU GMP Part I, Chapter 7", 7, "Outsourced Activities",
     "https://health.ec.europa.eu/document/download/58b5106a-cf6f-4352-9dca-1caf5d27d97e_en?filename=vol4-chap7_2012-06_en.pdf"),
    ("ch8", "EU GMP Part I, Chapter 8", 8, "Complaints, Quality Defects and Product Recalls",
     "https://health.ec.europa.eu/document/download/b1eb2292-cb0d-4e3f-aea9-e3fe79faf6e3_en?filename=2014-08_gmp_chap8.pdf"),
    ("ch9", "EU GMP Part I, Chapter 9", 9, "Self Inspection",
     "https://health.ec.europa.eu/document/download/07195808-d02e-4d7a-b8f4-f84a83278b62_en?filename=cap9_en.pdf"),
    ("annex1", "EU GMP Annex 1", None, "Manufacture of Sterile Medicinal Products",
     "https://health.ec.europa.eu/document/download/e05af55b-38e9-42bf-8495-194bbf0b9262_en?filename=20220825_gmp-an1_en_0.pdf"),
]

ICH_DOCS = [  # (key, code, title, url)
    ("q7", "ICH Q7", "Good Manufacturing Practice Guide for Active Pharmaceutical Ingredients",
     "https://database.ich.org/sites/default/files/Q7%20Guideline.pdf"),
    ("q9", "ICH Q9(R1)", "Quality Risk Management",
     "https://database.ich.org/sites/default/files/ICH_Q9%28R1%29_Guideline_Step4_2025_0115_0.pdf"),
    ("q10", "ICH Q10", "Pharmaceutical Quality System",
     "https://database.ich.org/sites/default/files/Q10%20Guideline.pdf"),
]

SOURCES: dict[str, dict[str, Any]] = {
    "cfr21": {
        "title": "21 CFR Part 210·211 (CGMP) · Part 600·601·606·610 (Biologics)",
        "publisher": "U.S. Office of the Federal Register / GPO — eCFR",
        "url": "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C",
        "api": "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-21.xml?part={part}",
        "license": "U.S. federal government work — public domain (17 U.S.C. §105). "
                   "eCFR is an editorial compilation, not an official legal edition of the CFR.",
        "license_url": "https://www.ecfr.gov/reader-aids/understanding-the-ecfr/what-is-the-ecfr",
        "full_text": True,
    },
    "eu_gmp": {
        "title": "EudraLex Volume 4 — EU GMP Part I (Chapters 1–9) and Annex 1",
        "publisher": "European Commission, DG SANTE",
        "url": "https://health.ec.europa.eu/medicinal-products/eudralex/eudralex-volume-4_en",
        "license": "© European Union. Reuse is authorised provided the source is acknowledged "
                   "(Commission Decision 2011/833/EU on the reuse of Commission documents).",
        "license_url": "https://commission.europa.eu/legal-notice_en",
        "full_text": True,
    },
    "ich": {
        "title": "ICH Q7, Q9(R1), Q10",
        "publisher": "International Council for Harmonisation (ICH)",
        "url": "https://www.ich.org/page/quality-guidelines",
        "license": "© ICH. Per the legal notice in each guideline, the document may be used, reproduced "
                   "and distributed provided ICH's copyright is acknowledged; any adaptation or "
                   "translation must be labelled as such and must not imply ICH endorsement.",
        "license_url": "https://www.ich.org/page/quality-guidelines",
        "full_text": True,
    },
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


#: Symbol 글꼴 글머리(사용자 정의 영역 코드포인트) → 일반 글머리. 글자는 바꾸지 않고 기호만.
_PUA_BULLETS = str.maketrans({"": "•", "": "▪", "": "•", "": "•",
                              "": "•", "": "-", "": "•"})


def _ws(s: str) -> str:
    return re.sub(r"[ \t ]+", " ", (s or "").translate(_PUA_BULLETS)).strip()


# ── eCFR XML ────────────────────────────────────────────────────────────────


def _itertext(el: ET.Element) -> str:
    return _ws("".join(el.itertext()))


def parse_ecfr_part(xml_text: str, part: str) -> list[dict[str, Any]]:
    """eCFR versioner XML(DIV5 PART) → 섹션(DIV8) 목록. 문단은 원문 순서·문구 그대로.

    문단 요소(P·FP·NOTE·EXTRACT 안의 P 등)는 '\n\n' 으로, 출처(CITA)는 `source_note` 로 따로 둔다."""
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for sub in [root] + list(root.iter("DIV6")):
        pass
    subpart_of: dict[int, str] = {}
    for div6 in root.iter("DIV6"):
        head = div6.find("HEAD")
        label = _itertext(head) if head is not None else ""
        for d8 in div6.iter("DIV8"):
            subpart_of[id(d8)] = label
    for d8 in root.iter("DIV8"):
        num = d8.get("N") or ""
        head_el = d8.find("HEAD")
        head = _itertext(head_el) if head_el is not None else ""
        title = re.sub(r"^§\s*" + re.escape(num) + r"\s*", "", head).strip()
        paras: list[str] = []
        notes: list[str] = []
        for child in d8:
            if child.tag == "HEAD":
                continue
            if child.tag == "CITA":
                notes.append(_itertext(child))
                continue
            if child.tag in ("P", "FP", "HD1", "HD2", "HD3"):
                t = _itertext(child)
                if t:
                    paras.append(t)
                continue
            # NOTE·EXTRACT·GPOTABLE·EDNOTE 등: 안쪽 문단 단위로 원문 그대로.
            # 표 행(ROW)은 칸을 줄바꿈으로 잇는다 — 빈 칸이 만든 연속 줄바꿈은 하나로(문단 구분 '\n\n' 과
            # 섞이면 빈 문단이 생긴다). 글자는 바꾸지 않는다.
            inner = [re.sub(r"\n{2,}", "\n", _itertext(p)).strip()
                     for p in child.iter() if p.tag in ("P", "FP", "HED", "ROW", "TR")]
            inner = [t for t in inner if t]
            if not inner:
                t = _itertext(child)
                inner = [t] if t else []
            paras.extend(inner)
        text = "\n\n".join(paras)
        reserved = not text and "[Reserved]" in head
        out.append({
            "doc": part,
            "number": num,
            "code": f"21 CFR {num}",
            "title": title,
            "path": [p for p in (f"21 CFR Part {part}", subpart_of.get(id(d8), "")) if p],
            "text": text,
            "notes": notes,
            "reserved": reserved,
            "url": f"https://www.ecfr.gov/current/title-21/part-{part}/section-{num}",
        })
    return out


# ── PDF → 줄 → 조항 (순수 함수: 줄 목록만 받는다) ─────────────────────────────


@dataclass
class Line:
    page: int
    y0: float
    y1: float
    x0: float
    size: float
    text: str
    small: bool = False  # 본문보다 작은 글씨(각주 영역)
    x1: float = 0.0


def pdf_lines(path: Path) -> list[Line]:
    """PyMuPDF 로 줄을 읽는다. 같은 페이지·같은 기준선(±2pt)의 줄은 x 순서로 합친다
    (ICH Q7 은 조항 번호와 본문이 다른 텍스트 블록에 같은 높이로 놓여 있다).
    머리말·꼬리말(페이지 위아래 8% 안에서 여러 쪽에 반복되는 줄·쪽번호)은 버린다.
    위첨자 각주 번호는 `[n]` 으로 남긴다(원문 보존)."""
    import fitz  # PyMuPDF — 수집 때만 필요

    doc = fitz.open(str(path))
    raw: list[Line] = []
    for pno, page in enumerate(doc):
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            if b.get("type"):
                continue
            for ln in b["lines"]:
                spans = [s for s in ln["spans"] if s["text"]]
                if not spans:
                    continue
                main = max(spans, key=lambda s: len(s["text"].strip()))["size"]
                parts = []
                for s in spans:
                    t = s["text"]
                    if (s["flags"] & 1) and s["size"] < main * 0.85 and t.strip().isdigit():
                        t = f"[{t.strip()}]"
                    parts.append(t)
                text = "".join(parts)
                if not text.strip():
                    continue
                raw.append(Line(pno, ln["bbox"][1], ln["bbox"][3], ln["bbox"][0], main, text,
                                x1=ln["bbox"][2]))
        raw.append(Line(pno, h, h, 0, 0, "\x00PAGE_END"))  # 쪽 높이 표지
    return finalize_lines(raw)


def finalize_lines(raw: list[Line]) -> list[Line]:
    """머리말·꼬리말 제거 → 같은 기준선 병합 → 각주(작은 글씨·쪽 하단) 표시. 순수 함수."""
    heights = {l.page: l.y0 for l in raw if l.text == "\x00PAGE_END"}
    lines = [l for l in raw if l.text != "\x00PAGE_END"]
    pages = sorted({l.page for l in lines})
    n_pages = max(1, len(pages))

    def edge(l: Line) -> bool:
        h = heights.get(l.page) or 842.0
        return l.y1 < h * 0.10 or l.y0 > h * 0.90

    def norm(t: str) -> str:
        return re.sub(r"\d+", "#", _ws(t)).lower()

    rep = Counter(norm(l.text) for l in lines if edge(l))
    keep = []
    for l in lines:
        t = _ws(l.text)
        if edge(l) and (re.fullmatch(r"(page\s*)?#(\s*(of|/)\s*#)?|-?\s*#\s*-?", norm(t))
                        or (n_pages >= 3 and rep[norm(t)] >= max(2, n_pages * 0.3))):
            continue
        keep.append(l)

    # 같은 쪽·같은 기준선 병합(x 순).
    keep.sort(key=lambda l: (l.page, round(l.y0), l.x0))
    merged: list[Line] = []
    for l in keep:
        if merged and merged[-1].page == l.page and abs(merged[-1].y0 - l.y0) <= 2.0:
            m = merged[-1]
            sep = "" if m.text.endswith(" ") or l.text.startswith(" ") else " "
            merged[-1] = Line(m.page, min(m.y0, l.y0), max(m.y1, l.y1), m.x0, max(m.size, l.size),
                              m.text + sep + l.text, x1=max(m.x1, l.x1))
        else:
            merged.append(Line(l.page, l.y0, l.y1, l.x0, l.size, l.text, x1=l.x1))
    merged.sort(key=lambda l: (l.page, l.y0, l.x0))

    sizes = Counter(round(l.size, 1) for l in merged if len(l.text.strip()) > 20)
    body = sizes.most_common(1)[0][0] if sizes else 11.0
    for l in merged:
        h = heights.get(l.page) or 842.0
        l.small = l.size < body - 1.2 and l.y0 > h * 0.6
    return merged


_BULLET = re.compile(r"^(?:[•▪◦·\-–—o]\s|\(?[a-z]{1,4}\)\s|\(?[ivx]{1,5}[.)]\s|\d{1,2}\)\s)")


def paragraphs(lines: list[Line]) -> list[tuple[str, Line, bool]]:
    """줄 → 문단(text, 첫 줄, 각주 여부). 줄바꿈은 공백으로 잇고, 세로 간격·글머리·쪽 넘김 후
    문장 끝이면 새 문단. 각주(작은 글씨 하단)는 본문과 섞지 않고 따로 문단을 만든다."""
    out: list[list[Any]] = []
    prev: Line | None = None
    span: dict[int, tuple[float, float]] = {}
    for l in lines:
        if len(_ws(l.text)) > 30:
            lo, hi = span.get(l.page, (l.x0, l.x1))
            span[l.page] = (min(lo, l.x0), max(hi, l.x1))
    closed = False
    for l in lines:
        t = _ws(l.text)
        if not t:
            continue
        new = True
        if prev is not None and out and out[-1][2] == l.small and not closed:
            gap = l.y0 - prev.y1
            same_page = l.page == prev.page
            ends = out[-1][0].rstrip().endswith((".", ":", ";", "?", "!"))
            if same_page and gap < max(prev.size, l.size) * 0.55 and not _BULLET.match(t) \
                    and not NUM_START.match(t):
                new = False
            elif not same_page and not ends and not _BULLET.match(t) and not NUM_START.match(t) \
                    and not l.small and not APPENDIX.match(t):
                new = False
        if new:
            out.append([t, l, l.small])
        else:
            cur = out[-1][0]
            if re.search(r"[A-Za-z]-$", cur) and not re.search(r"\s\S{1,2}-$", cur):
                out[-1][0] = cur + t
            else:
                out[-1][0] = cur + " " + t
        prev = l
        # 번호 붙은 소제목 줄('4.1 Responsibilities')은 그 줄로 문단을 닫는다 — 본문과 섞이면
        # 제목을 잃는다. 줄 폭이 본문 폭의 60% 미만일 때만(본문 첫 줄의 줄바꿈과 가르려고).
        m = NUM_START.match(t)
        lo, hi = span.get(l.page, (0.0, 0.0))
        closed = bool(m and _rest(m) and _is_heading(_rest(m)) and hi > lo
                      and (l.x1 - l.x0) < 0.6 * (hi - lo) and out and out[-1][0] == t)
    return [(p[0], p[1], p[2]) for p in out]


# 조항 번호 머리. '9.35APS ...'처럼 번호와 본문 사이 공백이 빠진 PDF 도 있다(대문자가 바로 붙을 때만).
NUM_START = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,3})(\.?)(?:\s+(.*)|(?<=\d)(?=[A-Z])(.*))?$")
APPENDIX = re.compile(r"^(Glossary|GLOSSARY|ANNEX [IVX]+|Annex [IVX]+|ANNEX \d|Annex \d)\b")


def _num_tuple(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.split("."))


def order_key(num: tuple[int, ...], decimal: bool = False) -> tuple[int, ...]:
    """번호 비교 키. ICH Q7 은 '2.1 소제목' 아래 문단을 '2.10·2.11 … 2.20'으로 매긴다 —
    두 자리 하위 번호는 (소제목, 문단)으로 펴야 '2.22 → 2.3' 이 증가로 읽힌다."""
    if decimal and len(num) == 2 and num[1] >= 10:
        return (num[0], num[1] // 10, num[1] % 10)
    return num


def _rest(m: "re.Match[str]") -> str:
    return (m.group(3) or m.group(4) or "").strip()


_UPPER_HEAD = re.compile(r"^([A-Z0-9][A-Z0-9 ,&/()\-–’']{3,}?[A-Z)])\s+(?=[A-Z][a-z])(.*)$", re.S)


def split_upper_heading(rest: str) -> tuple[str, str]:
    """'CONTINUAL IMPROVEMENT … QUALITY This section describes …' → (대문자 제목, 본문).
    ICH 장 제목은 대문자이고, PDF 에서 바로 아래 본문과 한 문단으로 붙어 나올 때가 있다."""
    m = _UPPER_HEAD.match(rest)
    if m and (len(m.group(1).split()) >= 2 or len(m.group(1)) >= 5):
        return m.group(1).strip(), m.group(2).strip()
    return rest, ""


def accept_number(num: tuple[int, ...], rest: str, last: tuple[int, ...] | None,
                  chapter: int | None, top_mode: str = "any", dotted: bool = False,
                  decimal: bool = False) -> bool:
    """이 문단 머리 번호를 새 조항으로 받을지 — 단조 증가 + 같은/다음 장만(본문 속 목록 번호·
    참고문헌·표 숫자를 조항으로 오인하지 않게). 애매하면 받지 않는다(앞 조항 본문에 남는다)."""
    if chapter is not None and num[0] != chapter:
        return False
    if len(num) == 1:
        # 최상위 장 제목: 다음 장 번호 + 제목. 본문 속 번호 목록('3. Reviewing ...')과 가르려고
        # 문서마다 제목 꼴을 정한다 — ICH 는 대문자 제목('3. PERSONNEL'), Annex 1 은 점 없는 '3 Pharma…'.
        if top_mode == "none" or not rest or not re.match(r"[A-Za-z]", rest):
            return False
        if top_mode == "upper":
            rest = split_upper_heading(rest)[0]
        if top_mode == "upper" and (rest != rest.upper() or not _is_heading(rest)):
            return False
        if top_mode == "nodot" and (dotted or not _is_heading(rest)):
            return False
        return (last is None and num[0] == 1) or (last is not None and num[0] == last[0] + 1)
    if rest and not re.match(r"[A-Z(\"'“‘]", rest):
        return False
    if last is None:
        return num[0] in (1, chapter or 1)
    if order_key(num, decimal) <= order_key(last, decimal):
        return False
    if num[0] == last[0]:
        return True
    return num[0] == last[0] + 1 and num[1] in (0, 1)


def _is_heading(text: str) -> bool:
    t = text.strip()
    return 0 < len(t) <= 90 and not t.endswith((".", ",", ";", ":")) and not _BULLET.match(t) \
        and t[0].isupper() and not NUM_START.match(t)


def split_clauses(paras: list[tuple[str, Line, bool]], *, chapter: int | None = None,
                  body_start: int = 0, principle: bool = False,
                  top_mode: str = "any", decimal: bool = False) -> dict[str, Any]:
    """문단 → {front_matter, clauses}. 조항 = 번호가 붙은 문단부터 다음 번호 직전까지 전부.

    · 번호 문단의 나머지가 짧고 마침표가 없으면 그 조항 제목(ICH 의 '4.4 Risk Review' 꼴).
    · 번호 없는 짧은 단독 문단이 다음 조항 바로 앞에 오면 소제목(path) — 본문에서 빼고 path 로.
    · `principle=True`(EU 장): 첫 번호 전의 'Principle' 절을 조항 'Principle' 로 둔다.
    · 본문 뒤 'Glossary'·'ANNEX I' 같은 번호 없는 부록 제목은 그 이름으로 새 조항을 연다.
    · 각주는 그 쪽에 걸린 조항의 notes 로. 원문 문장은 하나도 버리지 않는다(front_matter 포함)."""
    front: list[str] = []
    clauses: list[dict[str, Any]] = []
    last: tuple[int, ...] | None = None
    heading = ""
    top_title = ""
    cur: dict[str, Any] | None = None
    seq = list(paras)
    i = 0
    appendix = False
    while i < len(seq):
        text, line, small = seq[i]
        if i < body_start:
            front.append(text)
            i += 1
            continue
        if small:
            (cur["notes"] if cur else front).append(text)
            i += 1
            continue
        m = NUM_START.match(text)
        if m:
            num = _num_tuple(m.group(1))
            rest = _rest(m)
            peeked = False
            if not rest and len(num) == 1 and i + 1 < len(seq) and not seq[i + 1][2] \
                    and _is_heading(seq[i + 1][0]):
                rest, peeked = seq[i + 1][0], True  # '3.' 다음 문단이 장 제목
            if not appendix and accept_number(num, rest, last, chapter, top_mode,
                                              bool(m.group(2)), decimal):
                last = num
                title = ""
                body = rest
                if rest and _is_heading(rest):
                    title, body = rest, ""
                elif len(num) == 1 and top_mode == "upper":
                    head, tail = split_upper_heading(rest)
                    if tail:
                        title, body = head, tail
                if peeked:
                    i += 1
                if len(num) == 1:
                    top_title = title
                    heading = ""
                cur = {"number": m.group(1), "title": title,
                       "path": [p for p in (f"{num[0]}. {top_title}" if top_title and len(num) > 1 else "",
                                            heading) if p],
                       "paras": [body] if body else [], "notes": []}
                clauses.append(cur)
                i += 1
                continue
        # 번호 없는 문단.
        if clauses and len(text) <= 90 and APPENDIX.match(text.strip()):
            appendix = True
            label = text.strip()
            word, _, ident = APPENDIX.match(label).group(1).partition(" ")
            cur = {"number": word.title() + (" " + ident.upper() if ident else ""), "title": label,
                   "path": [],
                   "paras": [], "notes": []}
            clauses.append(cur)
            i += 1
            continue
        nxt = seq[i + 1][0] if i + 1 < len(seq) else ""
        nm = NUM_START.match(nxt)
        if not appendix and _is_heading(text) and nm and accept_number(
                _num_tuple(nm.group(1)), _rest(nm), last, chapter, top_mode,
                bool(nm.group(2)), decimal):
            heading = text
            i += 1
            continue
        if principle and not clauses and text.strip().lower() == "principle":
            cur = {"number": "Principle", "title": "Principle", "path": [], "paras": [], "notes": []}
            clauses.append(cur)
            i += 1
            continue
        if cur is None:
            front.append(text)
        else:
            cur["paras"].append(text)
        i += 1
    if principle and not any(c["number"] == "Principle" for c in clauses):
        # 'Principle' 이 표지·장 제목과 한 문단으로 붙어 나온 경우('… CHAPTER 3 PREMISES … PRINCIPLE
        # Premises and …'): 마지막 표지어 뒤부터 첫 번호 조항 전까지가 Principle 이다.
        hit = max((k for k, t in enumerate(front) if _PRINCIPLE.search(t)), default=None)
        if hit is not None:
            mm = list(_PRINCIPLE.finditer(front[hit]))[-1]
            head, tail = front[hit][:mm.start()].rstrip(), front[hit][mm.end():].strip()
            body = ([tail] if tail else []) + front[hit + 1:]
            while body and _is_heading(body[-1]) and len(body) > 1:
                # 첫 조항 직전 소제목('PREMISES')은 본문이 아니라 첫 조항의 path.
                if clauses and not clauses[0]["path"]:
                    clauses[0]["path"] = [body[-1]]
                body.pop()
            front = front[:hit] + ([head] if head else [])
            clauses.insert(0, {"number": "Principle", "title": "Principle", "path": [],
                               "paras": body, "notes": []})
    for c in clauses:
        c["text"] = "\n\n".join(p for p in c.pop("paras") if p)
    return {"front_matter": "\n\n".join(front), "clauses": clauses}


_PRINCIPLE = re.compile(r"(?:^|\s)(?:PRINCIPLE|Principle)(?:\s|$)")


def find_body_start(paras: list[tuple[str, Line, bool]], first_heading: str) -> int:
    """목차를 건너뛴 본문 시작 — 첫 장 제목(예: '1. INTRODUCTION')이 **마지막으로** 나오는 문단.
    목차·문서 지도에는 같은 제목이 먼저 나온다. 그 앞 문단은 버리지 않고 front_matter 로 간다."""
    pat = re.compile(r"^1\.?\s*" + re.escape(first_heading) + r"\s*$")
    hits = [i for i, (t, _, s) in enumerate(paras) if not s and pat.match(t.strip())]
    return hits[-1] if hits else 0


# ── 조립 ─────────────────────────────────────────────────────────────────────


def _fetch(url: str, dest: Path) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "-sSL", "--compressed", "-A", UA, "-o", str(dest), url], check=True)
    if dest.stat().st_size == 0:
        raise RuntimeError(f"빈 응답: {url}")
    return dest


def _finish(clauses: list[dict[str, Any]], source: str, doc_key: str, doc_code: str,
            url: str) -> list[dict[str, Any]]:
    out = []
    for c in clauses:
        num = c["number"]
        slug = re.sub(r"[^a-z0-9]+", "-", num.lower()).strip("-")
        code = c.get("code") or (f"{doc_code} {num}" if num != "Principle" else f"{doc_code}, Principle")
        text = c.get("text") or ""
        out.append({
            "id": f"{source}/{doc_key}/{slug}",
            "source": source,
            "doc": doc_key,
            "number": num,
            "slug": slug,
            "code": code,
            "title": c.get("title") or "",
            "path": c.get("path") or [],
            "text": text,
            "notes": c.get("notes") or [],
            "chars": len(text),
            "sha": _sha(text),
            "url": c.get("url") or url,
            **({"reserved": True} if c.get("reserved") else {}),
        })
    return out


def build_cfr(dl: Path) -> dict[str, Any]:
    docs, clauses = [], []
    for part, title in CFR_PARTS.items():
        url = SOURCES["cfr21"]["api"].format(date=ECFR_DATE, part=part)
        xml = _fetch(url, dl / f"cfr{part}.xml").read_text(encoding="utf-8")
        secs = parse_ecfr_part(xml, part)
        root = ET.fromstring(xml)
        auth = root.find("AUTH")
        src = root.find("SOURCE")
        docs.append({"key": part, "code": f"21 CFR Part {part}", "title": title,
                     "url": f"https://www.ecfr.gov/current/title-21/part-{part}",
                     "version": f"eCFR, up to date as of {ECFR_DATE}",
                     "front_matter": "\n\n".join(_itertext(e) for e in (auth, src) if e is not None)})
        for s in secs:
            s["slug_override"] = True
        fin = _finish(secs, "cfr21", part, f"21 CFR Part {part}", "")
        for c, s in zip(fin, secs):
            c["slug"] = s["number"].replace(".", "-")
            c["id"] = f"cfr21/{part}/{c['slug']}"
        clauses.extend(fin)
    return {"docs": docs, "clauses": clauses}


def build_pdf_source(source: str, specs: list[tuple], dl: Path) -> dict[str, Any]:
    docs, clauses, report = [], [], []
    for spec in specs:
        if source == "eu_gmp":
            key, code, chapter, title, url = spec
        else:
            key, code, title, url = spec
            chapter = None
        pdf = _fetch(url, dl / f"{source}_{key}.pdf")
        lines = pdf_lines(pdf)
        paras = paragraphs(lines)
        first = {"q7": "INTRODUCTION", "q9": "INTRODUCTION",
                 "q10": "PHARMACEUTICAL QUALITY SYSTEM", "annex1": "Scope"}.get(key)
        body_start = find_body_start(paras, first) if first else 0
        top_mode = "upper" if source == "ich" else ("nodot" if key == "annex1" else "none")
        res = split_clauses(paras, chapter=chapter, body_start=body_start,
                            principle=(source == "eu_gmp" and chapter is not None),
                            top_mode=top_mode, decimal=(key == "q7"))
        fin = _finish(res["clauses"], source, key, code, url)
        version = _version_hint(res["front_matter"], pdf.name)
        docs.append({"key": key, "code": code, "title": title, "url": url, "version": version,
                     "front_matter": res["front_matter"]})
        clauses.extend(fin)
        report.append(f"{code}: {len(fin)}")
    return {"docs": docs, "clauses": clauses, "report": report}


def _version_hint(front: str, fname: str) -> str:
    for pat in (r"Status of the document:\s*([^\n]+)", r"Deadline for coming into operation:\s*([^\n]+)",
                r"(Current Step \d+ version[^\n]*)", r"(dated \d{1,2} \w+ \d{4})"):
        m = re.search(pat, front)
        if m:
            return _ws(m.group(0))[:160]
    return ""


def write_source(key: str, built: dict[str, Any], retrieved: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in SOURCES[key].items() if k != "api"}
    obj = {"schema": SCHEMA, "source": {"key": key, **meta, "retrieved": retrieved},
           "docs": built["docs"], "clauses": built["clauses"]}
    p = OUT_DIR / f"{key}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                 newline="\n")
    return p


def write_translation_queue(retrieved: str) -> Path:
    """번역 대기 목록 — 조항 id · 글자 수 · 원문 해시. 번역 배치가 해시로 낡은 번역을 가려낸다."""
    rows = []
    for p in sorted(OUT_DIR.glob("*.json")):
        if p.name == "translation_queue.json":
            continue
        obj = json.loads(p.read_text(encoding="utf-8"))
        if obj.get("schema") != SCHEMA or obj["source"].get("lang") == "ko":
            continue  # 국문 원문(식약처)은 국문 번역 대상이 아니다
        for c in obj["clauses"]:
            if c.get("text"):
                rows.append({"id": c["id"], "code": c["code"], "chars": c["chars"], "sha": c["sha"],
                             "status": "pending"})
    q = OUT_DIR / "translation_queue.json"
    q.write_text(json.dumps({"schema": "grm-clause-translation-queue/v1", "as_of": retrieved,
                             "target_lang": "ko", "total": len(rows),
                             "total_chars": sum(r["chars"] for r in rows), "items": rows},
                            ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                 newline="\n")
    return q


# ── 식약처 「의약품 제조 및 품질관리에 관한 규정」(국문 원문) ─────────────────────────
# 국가법령정보센터 행정규칙 화면에서 키 없이 받는다(Open API 는 OC 키가 있어야 한다 — 키가 생기면 개정 감시로).
#   · 조문: admRulLsInfoR.do?admRulSeq=… 의 HTML(조문마다 <div class="pgroup">)
#   · 별표 1~18: 같은 화면의 첨부(flDownload.do) PDF — HWP 에서 만든 PDF 라 글자 사이 공백 문자가 없어
#     글자 상자 간격으로 띄어쓰기를 되살린다(줄 끝 띄어쓰기는 알 수 없어 한글-한글 줄바꿈은 붙인다).

MFDS_RULE = {
    "name": "의약품 제조 및 품질관리에 관한 규정",
    "page": "https://www.law.go.kr/행정규칙/의약품제조및품질관리에관한규정",
    "body": "https://www.law.go.kr/admRulLsInfoR.do?admRulSeq={seq}",
}

SOURCES["mfds_gmp"] = {
    "title": "의약품 제조 및 품질관리에 관한 규정 (식품의약품안전처 고시) — 조문·별표 1~18",
    "publisher": "식품의약품안전처 · 국가법령정보센터(법제처)",
    "url": MFDS_RULE["page"],
    "license": "법령·고시는 저작권법 제7조에 따라 보호받지 않는 저작물입니다. 국가법령정보센터 게시본을 "
               "그대로 싣습니다(공식 효력은 관보·법령정보센터 원문).",
    "license_url": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=248133#0000",
    "full_text": True,
    "lang": "ko",
}


def _html_text(fragment: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def parse_mfds_articles(body_html: str) -> dict[str, Any]:
    """행정규칙 본문 HTML → {meta, articles}. 조문 = `<div class="pgroup">` 하나, 항(①②)은 <p> 하나씩.
    부칙(제·개정 이력)은 조문이 아니라 싣지 않는다 — 공식 화면 링크로 갈음."""
    meta = {}
    m = re.search(r"\[시행\s*([^\]]+)\]\s*\[([^\]]+)\]", _html_text(body_html[:20000]))
    if m:
        meta = {"effective": m.group(1).strip(), "notice": m.group(2).strip()}
    arts = []
    for part in body_html.split('<div class="pgroup">')[1:]:
        lab = re.search(r"<label[^>]*>([^<]*)</label>", part)
        if not lab:
            continue
        label = _ws(lab.group(1))
        lm = re.match(r"^(제\d+조(?:의\d+)?)\s*(?:\((.*)\))?$", label)
        if not lm:
            continue
        paras = []
        for p in re.findall(r"<p[^>]*>(.*?)</p>", part, flags=re.S):
            t = _html_text(p)
            if t.startswith(label):
                t = t[len(label):].strip()
            if t:
                paras.append(t)
        arts.append({"number": lm.group(1), "title": lm.group(2) or "", "text": "\n\n".join(paras)})
    return {"meta": meta, "articles": arts}


def kor_pdf_lines(path: Path) -> list[Line]:
    """HWP 산 PDF → 줄(글자 상자 간격으로 띄어쓰기 복원). 머리말·쪽번호 정리는 finalize_lines 와 같다."""
    import fitz

    doc = fitz.open(str(path))
    raw: list[Line] = []
    for pno, page in enumerate(doc):
        h = page.rect.height
        for b in page.get_text("rawdict")["blocks"]:
            for ln in b.get("lines", []):
                out, prev, size = "", None, 0.0
                for s in ln["spans"]:
                    size = max(size, s["size"])
                    for c in s["chars"]:
                        if prev is not None and c["c"] != " " and prev["c"] != " " \
                                and c["bbox"][0] - prev["bbox"][2] > s["size"] * 0.18:
                            out += " "
                        out += c["c"]
                        prev = c
                if out.strip():
                    raw.append(Line(pno, ln["bbox"][1], ln["bbox"][3], ln["bbox"][0], size, out,
                                    x1=ln["bbox"][2]))
        raw.append(Line(pno, h, h, 0, 0, "\x00PAGE_END"))
    return finalize_lines(raw)


_KO_NUM = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})(\.?)\s+(.+)$")
_KO_ITEM = re.compile(r"^(?:[가-하]\.|\(\d+\)|\d+\)|[①-⑳]|[-•·○◦▪])\s?")
_HANGUL = re.compile(r"[가-힣]")


def _ko_title(rest: str) -> bool:
    t = rest.strip()
    return 0 < len(t) <= 40 and not t.endswith(("다.", "다", ".", ",")) and not _KO_ITEM.match(t)


def split_kor_byl(lines: list[Line]) -> dict[str, Any]:
    """별표 줄 → 조항. 번호 제목 줄('2.1 위험관리'·'1. 원칙')이 조항을 연다(단조 증가·제목 꼴일 때만).
    번호 없는 줄은 이어 붙인다: 한글-한글 줄바꿈은 붙이고(글자 단위 줄바꿈), 그 밖은 공백.
    '가.'·'(1)'·'①' 같은 항목은 새 문단. 첫 조항 전(별표 번호·제목)은 front_matter."""
    front: list[str] = []
    clauses: list[dict[str, Any]] = []
    last: tuple[int, ...] | None = None
    cur: dict[str, Any] | None = None
    top_title = ""
    for l in lines:
        t = _ws(l.text)
        if not t:
            continue
        if l.small and cur is not None:
            cur["notes"].append(t)
            continue
        m = _KO_NUM.match(t)
        if m and _ko_title(m.group(3)):
            num = _num_tuple(m.group(1))
            ok = (len(num) == 1 and m.group(2) == "." and (
                    (last is None and num[0] == 1) or (last is not None and num[0] == last[0] + 1))) \
                or (len(num) > 1 and last is not None and num > last
                    and (num[0] == last[0] or (num[0] == last[0] + 1 and num[1] == 1)))
            if ok:
                last = num
                if len(num) == 1:
                    top_title = m.group(3).strip()
                cur = {"number": m.group(1), "title": m.group(3).strip(),
                       "path": [f"{num[0]}. {top_title}"] if len(num) > 1 and top_title else [],
                       "paras": [], "notes": []}
                clauses.append(cur)
                continue
        target = cur["paras"] if cur is not None else front
        if not target or _KO_ITEM.match(t) or _KO_NUM.match(t):
            target.append(t)
        else:
            prev = target[-1]
            glue = "" if _HANGUL.match(prev[-1:]) and _HANGUL.match(t[:1]) else " "
            target[-1] = prev + glue + t
    for c in clauses:
        c["text"] = "\n\n".join(c.pop("paras"))
    return {"front_matter": "\n\n".join(front), "clauses": clauses}


def build_mfds(dl: Path, seq: str) -> dict[str, Any]:
    name = MFDS_RULE["name"]
    body_url = MFDS_RULE["body"].format(seq=seq)
    body = _fetch(body_url, dl / "mfds_body.html").read_text(encoding="utf-8")
    parsed = parse_mfds_articles(body)
    meta = parsed["meta"]
    version = " · ".join(v for v in (meta.get("notice"), f"시행 {meta['effective']}" if meta.get("effective") else "") if v)
    docs, clauses = [], []
    docs.append({"key": "rule", "code": f"{name} 조문", "title": "", "url": MFDS_RULE["page"],
                 "version": version, "front_matter": ""})
    for a in parsed["articles"]:
        slug = a["number"].replace("제", "").replace("조의", "-").replace("조", "")
        clauses.append({"id": f"mfds_gmp/rule/{slug}", "source": "mfds_gmp", "doc": "rule",
                        "number": a["number"], "slug": slug, "code": f"{name} {a['number']}",
                        "title": a["title"], "path": [], "text": a["text"], "notes": [],
                        "chars": len(a["text"]), "sha": _sha(a["text"]), "url": MFDS_RULE["page"]})
    import html as _h
    rows = re.findall(r'href="([^"]*flSeq=\d+&amp;flNm=([^"&]*)&amp;bylClsCd=\d+)">\s*<img alt="PDF', body)
    from urllib.parse import unquote_plus
    for href, nm in rows:
        label = unquote_plus(nm)
        bm = re.match(r"\[별표 (\d+)(?:의(\d+))?\]\s*(.*)$", label)
        if not bm:
            continue
        key = f"byl{bm.group(1)}" + (f"-{bm.group(2)}" if bm.group(2) else "")
        bno = f"별표 {bm.group(1)}" + (f"의{bm.group(2)}" if bm.group(2) else "")
        url = "https://www.law.go.kr/LSW/" + _h.unescape(href)
        pdf = _fetch(url, dl / f"mfds_{key}.pdf")
        res = split_kor_byl(kor_pdf_lines(pdf))
        code = f"{name} [{bno}]"
        docs.append({"key": key, "code": code, "title": bm.group(3).strip(), "url": url,
                     "version": version, "front_matter": res["front_matter"]})
        for c in res["clauses"]:
            slug = c["number"].replace(".", "-")
            clauses.append({"id": f"mfds_gmp/{key}/{slug}", "source": "mfds_gmp", "doc": key,
                            "number": c["number"], "slug": slug, "code": f"{code} {c['number']}",
                            "title": c["title"], "path": c["path"], "text": c["text"],
                            "notes": c["notes"], "chars": len(c["text"]), "sha": _sha(c["text"]),
                            "url": url})
    return {"docs": docs, "clauses": clauses}


def main(argv: Iterable[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--download-dir", required=True, type=Path)
    ap.add_argument("--only", default="cfr21,eu_gmp,ich,mfds_gmp")
    ap.add_argument("--mfds-seq", default="2100000252026",
                    help="국가법령정보센터 행정규칙 일련번호(admRulSeq) — 개정되면 바뀐다(2026-11-16 현행: 제2024-87호)")
    ap.add_argument("--retrieved", required=True, help="수집일 YYYY-MM-DD(결정론 — now() 를 쓰지 않는다)")
    a = ap.parse_args(list(argv) if argv is not None else None)
    want = set(a.only.split(","))
    if "cfr21" in want:
        b = build_cfr(a.download_dir)
        print("cfr21", len(b["clauses"]), write_source("cfr21", b, a.retrieved))
    if "eu_gmp" in want:
        b = build_pdf_source("eu_gmp", EU_DOCS, a.download_dir)
        print("eu_gmp", len(b["clauses"]), "; ".join(b["report"]), write_source("eu_gmp", b, a.retrieved))
    if "ich" in want:
        b = build_pdf_source("ich", ICH_DOCS, a.download_dir)
        print("ich", len(b["clauses"]), "; ".join(b["report"]), write_source("ich", b, a.retrieved))
    if "mfds_gmp" in want:
        b = build_mfds(a.download_dir, a.mfds_seq)
        print("mfds_gmp", len(b["clauses"]), write_source("mfds_gmp", b, a.retrieved))
    print("queue", write_translation_queue(a.retrieved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
