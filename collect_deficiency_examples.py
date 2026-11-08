#!/usr/bin/env python3
"""실사 지적 사례 모음(업체명 없음) 수집기 — 일본 PMDA 연차보고서 별첨 · 영국 MHRA GMP 지적 원자료.

무엇: 규제기관이 업체명을 지운 채 "어떤 조항으로 무엇을 지적했나"를 모아 공개한 자료를 문장(행) 단위로
`deficiency_examples`(web/migrations/134_deficiency_examples.sql)에 적재한다. 업체가 없으니 업체별
`findings` 표에는 넣지 않는다.

원천 (2026-11-08 python `requests` + 저장소 기본 UA 로 전부 HTTP 200 실측. 봇 차단 없음):
  ① PMDA GMP/GCTP Annual Report 별첨 "指摘事項一覧 / List of Identified Deficiencies" (xlsx)
     목록: https://www.pmda.go.jp/review-services/gmp-qms-gctp/gmp/0011.html (일본어)
           https://www.pmda.go.jp/english/review-services/gmp-qms-gctp/0007.html (영문)
     FY2023~FY2025 일본어·영문 각 3개. FY2022 는 별첨이 없다. 연 1회(7~11월) 새 연도가 붙는다.
     행 = 지적 1건(No.) — 시스템·지적분류·지적 문장·배경·조사 구분. 수치·로트는 ● 로 가려져 있다.
     ★일본어 셀에는 후리가나(rPh)가 숨어 있다 — 그대로 읽으면 "指摘事項シテキ" 처럼 가타카나가 붙는다.
       `read_xlsx()` 는 rPh 를 버린다(픽스처 시험이 지킨다).
     ★영문 별첨은 두 목록 페이지에 다른 파일 번호로 같은 내용이 올라 있다(sha256 동일 또는 셀 동일).
       (기관, 연도, 언어)마다 그 언어의 목록 페이지에 걸린 파일 하나만 쓴다.
  ② MHRA "Good manufacturing practice inspection deficiencies" (OGL v3.0)
     https://www.gov.uk/government/statistics/good-manufacturing-practice-inspection-deficiencies
     - 2018·2019 xlsx: 행 = (익명 실사 ID, 지적 등급 번호 Major-2 등, EU GMP 조항 하나). **지적 문장은 없다.**
       노트 시트: "Major-1 아래 참조 10개 = 함께 Major 1건으로 지적". 즉 한 지적이 여러 조항을 가진다.
     - 2015·2016 Trend PDF: 장(Chapter/Annex)별 "Examples Deficiencies" 쪽에 실제 지적 문장 예시가 있다.
       행 = 글머리표 하나. 글머리 앞 머리 문장은 context 로 남긴다.
     2019년분(2020-10 게시)이 마지막이다 — 새 흐름이 없어 1회 적재로 끝난다.
     2010~2013 PDF 는 슬라이드 형식이 제각각이라 이번 범위 밖(PR 본문 다음 단계).

번역: 하지 않는다. 일본어·영어 원문만 정확히 싣는다(lang 열). 한국어는 다음 단계.

쓰기 게이트(둘 다 참일 때만 Supabase 에 쓴다):
  - CLI `--apply`
  - 환경변수 ENABLE_DEFICIENCY_EXAMPLES=true (repo 변수, 기본 off)
그 밖에는 dry-run: 받기·파싱·조항 정규화·분류·구조 검증까지 하고 리포트(JSON)만 쓴다.

새 연도 감지(`--discover`, 월간 schedule 이 켠다): 두 PMDA 목록 페이지와 MHRA 페이지에서 xlsx 링크를
다시 읽어, 코드의 SOURCES 에 없는 파일을 report.new_files 로 남기고 exit 3 으로 알린다(워크플로가 이슈로
올린다). 새 PMDA 별첨은 연도·언어를 파일 안 제목 줄과 목록 페이지로 판정해 같은 파서로 적재할 수 있다
(`--include-new`). 자동 적재하지 않는 이유: 형식이 바뀌었을 때 사람이 한 번 보게 하려고.

exit code: 0 정상 · 2 수집/구조 검증/DB 실패 · 3 (discover) 새 파일 발견.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

PARSER_VERSION = "grm-deficiency-examples/v1"
DEFAULT_USER_AGENT = "GRM-Intake/1.1 (+github-actions)"  # grm_common.DEFAULT_USER_AGENT 와 같은 값

PMDA_INDEX = {
    "ja": "https://www.pmda.go.jp/review-services/gmp-qms-gctp/gmp/0011.html",
    "en": "https://www.pmda.go.jp/english/review-services/gmp-qms-gctp/0007.html",
}
MHRA_INDEX = ("https://www.gov.uk/government/statistics/"
              "good-manufacturing-practice-inspection-deficiencies")
PMDA_FILES = "https://www.pmda.go.jp/files/"
GOVUK = "https://assets.publishing.service.gov.uk/media/"


@dataclass(frozen=True)
class Source:
    key: str            # 적재 키 — deficiency_example_sources.source_key
    agency: str         # PMDA | MHRA
    year: int
    lang: str           # ja | en
    kind: str           # pmda_attachment | mhra_xlsx | mhra_trend_pdf
    url: str
    index_url: str
    min_rows: int       # 구조 검증 하한(2026-11-08 실측의 약 80%)
    # 같은 내용의 다른 파일 번호(목록 페이지가 둘이라 생긴 사본). discover 가 '새 파일' 로 오인하지 않게 한다.
    aliases: tuple[str, ...] = ()


# 실측(2026-11-08): PMDA 행 수 ja 2025=42·2024=42·2023=26 / en 2025=42·2024=43·2023=26
#                    MHRA xlsx 2019=5,292·2018=6,209 · PDF 예시 글머리 2015·2016 (min_rows 는 약 80%)
SOURCES: tuple[Source, ...] = (
    Source("pmda:000281968", "PMDA", 2025, "ja", "pmda_attachment", PMDA_FILES + "000281968.xlsx",
           PMDA_INDEX["ja"], 33),
    Source("pmda:000276129", "PMDA", 2024, "ja", "pmda_attachment", PMDA_FILES + "000276129.xlsx",
           PMDA_INDEX["ja"], 33),
    Source("pmda:000270781", "PMDA", 2023, "ja", "pmda_attachment", PMDA_FILES + "000270781.xlsx",
           PMDA_INDEX["ja"], 20),
    Source("pmda:000283020", "PMDA", 2025, "en", "pmda_attachment", PMDA_FILES + "000283020.xlsx",
           PMDA_INDEX["en"], 33, aliases=("000283013",)),
    Source("pmda:000276920", "PMDA", 2024, "en", "pmda_attachment", PMDA_FILES + "000276920.xlsx",
           PMDA_INDEX["en"], 34, aliases=("000277111",)),
    Source("pmda:000273638", "PMDA", 2023, "en", "pmda_attachment", PMDA_FILES + "000273638.xlsx",
           PMDA_INDEX["en"], 20, aliases=("000273633",)),
    Source("mhra:2019_deficiency_data", "MHRA", 2019, "en", "mhra_xlsx",
           GOVUK + "5f886e32e90e07415985b7fa/2019_Deficiency_Data.xlsx", MHRA_INDEX, 4200),
    Source("mhra:2018_deficiency_data", "MHRA", 2018, "en", "mhra_xlsx",
           GOVUK + "5dc2e1ed40f0b637a38efb70/2018_Deficiency_Data.xlsx", MHRA_INDEX, 4900),
    Source("mhra:trend_2016", "MHRA", 2016, "en", "mhra_trend_pdf",
           GOVUK + "5a82003de5274a2e8ab56f94/MHRA_GMP_Inspection_Deficiency_Data_Trend_2016.pdf",
           MHRA_INDEX, 200),
    Source("mhra:trend_2015", "MHRA", 2015, "en", "mhra_trend_pdf",
           GOVUK + "5a80be59e5274a2e87dbb912/MHRA_GMP_Inspection_Deficiency_Data_Trending_2015.pdf",
           MHRA_INDEX, 120),
)

# MHRA 페이지의 파일 중 일부러 싣지 않는 것 — discover 가 '새 파일' 로 울리지 않게.
MHRA_IGNORED_FILES = {
    "2019_Deficiency_Data_Top_10_s.pdf",  # xlsx 집계표(문장 없음 — xlsx 에서 다시 셀 수 있다)
    "GDP_2016_Deficiency_data.pdf",        # 유통(GDP) 지적 — GMP 범위 밖
    "Good_manufacturing_practice_inspection_deficiencies_2013.pdf",  # 2010~2013 슬라이드 — 다음 단계
    "Good_manufacturing_practice_inspection_deficiencies_April_to_June_2012.pdf",
    "Good_manufacturing_practice_inspection_deficiencies_April_2011_to_March_2012.pdf",
    "Good_manufacturing_practice_inspection_deficiencies_April_2010_to_March_2011.pdf",
}


# ── xlsx (표준 라이브러리만 — openpyxl 의존성을 늘리지 않는다) ───────────────────────────────

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _si_text(si: ET.Element) -> str:
    """sharedStrings 의 <si> 하나 → 글자. 후리가나(<rPh>) 안의 <t> 는 버린다."""
    parts: list[str] = []
    for child in si:
        if child.tag == _M + "t":
            parts.append(child.text or "")
        elif child.tag == _M + "r":
            t = child.find(_M + "t")
            if t is not None:
                parts.append(t.text or "")
        # _M+"rPh"(후리가나) · _M+"phoneticPr" 는 버린다.
    return "".join(parts)


def _col_index(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group()


def read_xlsx(data: bytes) -> list[tuple[str, list[tuple[int, dict[str, str]]]]]:
    """[(시트 이름, [(행 번호, {열: 값})])]. 빈 셀은 뺀다. 값은 원문 그대로(공백 정리 안 함)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        shared = [_si_text(si) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_M + "si")]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for sh in wb.find(_M + "sheets"):
        tgt = targets[sh.get(_REL_ID)].lstrip("/")
        tgt = tgt if tgt.startswith("xl/") else "xl/" + tgt
        root = ET.fromstring(z.read(tgt))
        rows: list[tuple[int, dict[str, str]]] = []
        for row in root.iter(_M + "row"):
            cells: dict[str, str] = {}
            for c in row.findall(_M + "c"):
                t = c.get("t")
                v = c.find(_M + "v")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    is_ = c.find(_M + "is")
                    val = _si_text(is_) if is_ is not None else ""
                else:
                    val = v.text if v is not None and v.text is not None else ""
                if val != "":
                    cells[_col_index(c.get("r"))] = val
            if cells:
                rows.append((int(row.get("r")), cells))
        out.append((sh.get("name") or "", rows))
    return out


def clean(text: str | None) -> str:
    """앞뒤 공백·탭만 정리. 줄바꿈·전각 공백·● 는 원문 그대로 둔다(내용은 바꾸지 않는다)."""
    if text is None:
        return ""
    lines = [ln.strip(" \t　\r") for ln in str(text).split("\n")]
    return "\n".join(lines).strip("\n").strip()


# ── 조항 정규화 ──────────────────────────────────────────────────────────────────────────
# 키 형식은 지식 그래프(106 finding_clause)와 맞춘다: '21 CFR 211.22', 'EU GMP Annex 1'.
# 일본은 'JP GMP Ordinance Article 11-2'(条の2 → 11-2), 문단은 'para 1 item 4'.

_KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def jnum(s: str) -> int | None:
    """'21' · '２１' · '二十一' · '十' → 21·21·21·10. 못 읽으면 None."""
    s = unicodedata.normalize("NFKC", s or "").strip()
    if s.isdigit():
        return int(s)
    if not s or any(ch not in _KANJI and ch not in "十百" for ch in s):
        return None
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        else:
            cur = cur * 10 + _KANJI[ch]
    return total + cur


_JN = r"[0-9０-９〇一二三四五六七八九十百]+"
# GMP省令第20条第1項第3号 · 省令第21条 · GMP省令（以下「省令」という。）第30条第2号 · GMP省令第11条の2第2項
_JA_CLAUSE = re.compile(
    rf"(?P<law>GMP|GCTP|QMS)?省令(?:（[^）]{{0,30}}）)?第(?P<art>{_JN})条(?:の(?P<sub>{_JN}))?"
    rf"(?:第(?P<para>{_JN})項)?(?:第(?P<item>{_JN})号)?")
# EN: "Article 21 of GMP ministreal ordinance" · "item 4, paragraph 1, Article 11-2 of the Ordinance"
#     "Article 11, Paragraph 1, Item 6 of the GMP Ministerial Ordinance"
_EN_ART = re.compile(
    r"(?:(?:item\s+(?P<item0>\d+)\s*,\s*)?paragraph\s+(?P<para0>\d+)(?:-(?P<item2>\d+))?\s*,\s*)?"
    r"Art[iu]cle\s+(?P<art>\d+)(?:-(?P<sub>\d+))?"
    r"(?:\s*,?\s*Paragraph\s+(?P<para1>\d+)(?:-(?P<item3>\d+))?)?(?:\s*,\s*Item\s+(?P<item1>\d+))?"
    r"(?P<tail>[^.()]{0,60})", re.I)
# 원문 오타까지(2026-11-08 실측: Ministreal Audinance · Orinance · ordicance · ordinence · Artucle).
# 또는 "(violation of … Article N …)" / "【violation of …】" 문맥이면 법령 이름이 빠져도 GMP 省令로 본다.
_EN_ORD = re.compile(r"\b(?:or|au)d?i[nc]\w*?ance\b|\borinance\b|ministe?r[ie]?al|violation\s+of", re.I)
_EN_LAW = re.compile(r"\b(GCTP|QMS)\b", re.I)
_CFR = re.compile(r"\b21\s*CFR\s*(?:Part\s*)?(\d{1,4})\.(\d{1,4})((?:\([A-Za-z0-9]+\))*)", re.I)


def _jp_key(law: str | None, art: int, sub: int | None) -> str:
    name = {"GCTP": "JP GCTP Ordinance", "QMS": "JP QMS Ordinance"}.get((law or "").upper(), "JP GMP Ordinance")
    return f"{name} Article {art}" + (f"-{sub}" if sub else "")


def _para(para: int | None, item: int | None) -> str:
    bits = []
    if para:
        bits.append(f"para {para}")
    if item:
        bits.append(f"item {item}")
    return " ".join(bits)


def jp_clauses(text: str, lang: str) -> list[dict[str, Any]]:
    """지적 문장이 글자로 인용한 일본 GMP 省令 조항. 문장에 없는 조항은 만들지 않는다."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    t = text or ""
    if lang == "ja":
        for m in _JA_CLAUSE.finditer(t):
            art = jnum(m["art"])
            if not art:
                continue
            sub = jnum(m["sub"]) if m["sub"] else None
            key = _jp_key(m["law"], art, sub)
            para = _para(jnum(m["para"]) if m["para"] else None, jnum(m["item"]) if m["item"] else None)
            out.setdefault((key, para), {"key": key, "para": para, "raw": m.group(0),
                                         "basis": "jp_ordinance_text", "confidence": 0.95})
    else:
        for m in _EN_ART.finditer(t):
            window = t[max(0, m.start() - 40):m.end() + 20]
            if not _EN_ORD.search(m["tail"] or "") and not _EN_ORD.search(window):
                continue
            if re.match(r"\s*of\s+(?:the\s+)?(?:PMD\s+|PMDA?\s+)?(?:Act|Law)\b", m["tail"] or "", re.I):
                continue  # 法(薬機法) 조항 — 省令 키로 만들지 않는다
            law = (_EN_LAW.search(window) or [None])[0]
            key = _jp_key(law, int(m["art"]), int(m["sub"]) if m["sub"] else None)
            para = _para(int(m["para0"] or m["para1"] or 0) or None,
                         int(m["item0"] or m["item1"] or m["item2"] or m["item3"] or 0) or None)
            raw = re.sub(r"\s+", " ", (m.group(0)[:-len(m["tail"])] if m["tail"] else m.group(0))).strip()
            # ★영문판 조항 표기는 일본어 원문과 자주 어긋난다(2026-11-08 대조: FY2025 42건 중 8건, FY2024 는
            #   영문만 43건이라 번호부터 밀린다. 第3条の2 → "paragraph 2, Article 3", 第10条第4号 → "Article 10-4").
            #   글자 그대로 옮기되 신뢰도를 낮춘다 — 지식 그래프 연결은 일본어판(confidence 0.95) 키를 쓴다.
            out.setdefault((key, para), {"key": key, "para": para, "raw": raw, "basis": "jp_ordinance_text_en",
                                         "confidence": 0.6})
    for m in _CFR.finditer(t):
        key = f"21 CFR {m[1]}.{m[2]}"
        out.setdefault((key, m[3].lower()), {"key": key, "para": m[3].lower(), "raw": m.group(0),
                                             "basis": "cfr_text", "confidence": 0.95})
    return list(out.values())


_EU_REF = re.compile(r"^\s*(?P<kind>[CA])(?P<num>\d{1,2})\.(?P<para>\S.*?)\s*$", re.I)
_EU_REF_WORD = re.compile(r"^\s*(?P<kind>Chapter|Annex)\s*(?P<num>\d{1,2})\s*(?P<para>.*?)\s*$", re.I)
_EU_CHAPTER = re.compile(r"^\s*(Chapter|Annex)\s*(\d{1,2})\s*$", re.I)


def eu_clause(reference: str, chapter: str = "") -> dict[str, Any] | None:
    """MHRA REFERENCE 'C1.8(iii)' → {'key': 'EU GMP Chapter 1', 'para': '1.8(iii)'}.
    'A15.3.14(ii)' → Annex 15 / '3.14(ii)'. 'Chapter 2 Principle' → Chapter 2 / 'Principle'.
    REFERENCE 가 비면 CHAPTER 열('Chapter 4' 등)로 장 단위만. 둘 다 못 읽으면 None."""
    ref = (reference or "").strip()
    if m := _EU_REF.match(ref):
        kind = "Chapter" if m["kind"].upper() == "C" else "Annex"
        para = m["para"]
        if kind == "Chapter":
            para = f"{m['num']}.{para}"
        return {"key": f"EU GMP {kind} {int(m['num'])}", "para": para, "raw": ref, "basis": "mhra_reference",
                "confidence": 1.0}
    if m := _EU_REF_WORD.match(ref):
        return {"key": f"EU GMP {m['kind'].title()} {int(m['num'])}", "para": m["para"], "raw": ref,
                "basis": "mhra_reference", "confidence": 1.0}
    if m := _EU_CHAPTER.match(chapter or ""):
        return {"key": f"EU GMP {m[1].title()} {int(m[2])}", "para": "", "raw": ref or chapter,
                "basis": "mhra_chapter", "confidence": 0.9}
    return None


# ── 우리 공통 분류(grm_findings.FINDING_TAXONOMY 의 code) 매핑 ────────────────────────────────
# 기관 분류는 원문 그대로(agency_category) 두고, 매핑은 따로(grm_category + basis). 사전에 없으면
# 문장(영문)으로 grm_findings.classify_finding_category 를 돌리고, 그래도 없으면 빈칸이다.

# PMDA 지적분류(일본어·영문) — 앞부분 일치. 순서 = 우선순위(구체적인 것 먼저).
PMDA_CATEGORY_MAP: tuple[tuple[str, str], ...] = (
    # 데이터 신뢰성 그 자체가 분류 이름인 것만. '文書及び記録の管理（DIを含む）' 처럼 괄호 안 부기는
    # map_pmda_category() 가 먼저 떼고 본다 — 부기만으로 data_integrity 가 되지 않는다.
    ("DI関連", "data_integrity"), ("生データ", "data_integrity"), ("データの信頼性", "data_integrity"),
    ("DI-related", "data_integrity"), ("raw data", "data_integrity"), ("data integrity", "data_integrity"),
    ("無菌", "aseptic_sterility_assurance"), ("APS", "aseptic_sterility_assurance"),
    ("steril", "aseptic_sterility_assurance"), ("aseptic", "aseptic_sterility_assurance"),
    ("環境モニタリング", "environmental_monitoring"), ("environmental monitoring", "environmental_monitoring"),
    ("洗浄", "cleaning_validation"), ("cleaning", "cleaning_validation"),
    ("異物", "contamination_control"), ("汚染", "contamination_control"), ("衛生", "contamination_control"),
    ("contamination", "contamination_control"), ("foreign matter", "contamination_control"),
    ("hygi", "contamination_control"),
    # 시험실 이상·OOS 는 '일탈' 보다 먼저(영문 'Deviation control in laboratory, OOS/OOT' 가 일본어 試験室異常 과 같다)
    ("試験室異常", "qc_lab_controls"), ("OOS", "qc_lab_controls"), ("laborator", "qc_lab_controls"),
    ("逸脱", "deviation_capa"), ("CAPA", "deviation_capa"), ("deviation", "deviation_capa"),
    ("安定性", "stability_storage"), ("stability", "stability_storage"),
    ("参考品", "qc_lab_controls"), ("保存品", "qc_lab_controls"), ("サンプリング", "qc_lab_controls"),
    ("試験", "qc_lab_controls"), ("reserve sample", "qc_lab_controls"), ("retain sample", "qc_lab_controls"),
    ("retention sample", "qc_lab_controls"), ("sampl", "qc_lab_controls"), ("test record", "qc_lab_controls"),
    ("test instruction", "qc_lab_controls"),
    # 변경관리는 공통 분류에서 regulatory_reporting('Regulatory reporting and change control')이다.
    ("変更", "regulatory_reporting"), ("change control", "regulatory_reporting"),
    ("薬事", "regulatory_reporting"), ("承認", "regulatory_reporting"),
    ("regulatory", "regulatory_reporting"), ("pharmaceutical affairs", "regulatory_reporting"),
    ("苦情", "complaint_recall"), ("回収", "complaint_recall"), ("complaint", "complaint_recall"),
    ("recall", "complaint_recall"),
    ("バリデーション（プロセス）", "process_validation"), ("process validation", "process_validation"),
    ("validation (process)", "process_validation"),
    ("適格性", "validation_qualification"), ("バリデーション", "validation_qualification"),
    ("qualification", "validation_qualification"), ("validation", "validation_qualification"),
    ("コンピュータ", "computer_system_validation"), ("computer", "computer_system_validation"),
    ("製品品質の照査", "quality_unit_oversight"), ("product quality review", "quality_unit_oversight"),
    ("文書", "documentation_records"), ("記録", "documentation_records"), ("指図", "documentation_records"),
    ("document", "documentation_records"), ("record", "documentation_records"),
    ("manufacturing instruction", "documentation_records"),
    ("教育訓練", "training_personnel"), ("training", "training_personnel"), ("人員", "training_personnel"),
    ("personnel", "training_personnel"),
    ("供給者", "material_supplier_control"), ("外部委託", "material_supplier_control"),
    ("supplier", "material_supplier_control"), ("supplyer", "material_supplier_control"),
    ("contractor", "material_supplier_control"), ("原料", "material_supplier_control"),
    ("raw material", "material_supplier_control"),
    ("表示", "labeling_packaging"), ("包装", "labeling_packaging"), ("label", "labeling_packaging"),
    ("packag", "labeling_packaging"),
    ("構造設備", "equipment_facility"), ("設備", "equipment_facility"), ("施設", "equipment_facility"),
    ("facilit", "equipment_facility"), ("equipment", "equipment_facility"), ("premi", "equipment_facility"),
    ("出荷", "quality_unit_oversight"), ("release", "quality_unit_oversight"),
    ("品質マネ", "quality_unit_oversight"), ("組織", "quality_unit_oversight"),
    ("quality management", "quality_unit_oversight"), ("organization", "quality_unit_oversight"),
    ("自己点検", "quality_unit_oversight"), ("self", "quality_unit_oversight"),
    ("不合格品", "other_quality_system"), ("nonconforming", "other_quality_system"),
    ("rejected", "other_quality_system"),
)

# EU GMP 장/부속서 → 공통 분류(장 단위의 거친 매핑 — basis='eu_gmp_part_map' 로 구분된다).
EU_PART_MAP = {
    "EU GMP Chapter 1": "quality_unit_oversight",       # PQS(편차·CAPA·변경도 여기에 있다 — 거친 매핑)
    "EU GMP Chapter 2": "training_personnel",
    "EU GMP Chapter 3": "equipment_facility",
    "EU GMP Chapter 4": "documentation_records",
    "EU GMP Chapter 5": "other_quality_system",          # Production — 공통 분류에 1:1 짝이 없다
    "EU GMP Chapter 6": "qc_lab_controls",
    "EU GMP Chapter 7": "material_supplier_control",     # Outsourced activities
    "EU GMP Chapter 8": "complaint_recall",
    "EU GMP Chapter 9": "quality_unit_oversight",       # Self inspection
    "EU GMP Annex 1": "aseptic_sterility_assurance",
    "EU GMP Annex 11": "computer_system_validation",
    "EU GMP Annex 15": "validation_qualification",
    "EU GMP Annex 16": "quality_unit_oversight",        # QP 인증·출하
    "EU GMP Annex 8": "material_supplier_control",      # 원료·자재 검체 채취
    "EU GMP Annex 19": "qc_lab_controls",               # 참고품·보존품
    "EU GMP Annex 13": "labeling_packaging",            # 임상시험용 의약품(표시가 대부분)
}


_QUALIFIER = re.compile(r"[（(][^（）()]*(?:DI|data\s*integrity)[^（）()]*[)）]", re.I)


def map_pmda_category(label: str) -> str:
    """PMDA 지적분류 → 공통 분류 code. '(DIを含む)·(including data integrity)' 부기를 떼고 먼저 본다."""
    for cand in (_QUALIFIER.sub("", label or ""), label or ""):
        low = cand.lower()
        if not low.strip():
            continue
        for needle, code in PMDA_CATEGORY_MAP:
            if needle.lower() in low:
                return code
    return ""


_CLASSIFIER: Callable[[str], str] | None = None


def text_category(text: str) -> str:
    """영문 문장 → 공통 분류 code(grm_findings.classify_finding_category). 모듈을 못 읽으면 빈칸."""
    global _CLASSIFIER
    if _CLASSIFIER is None:
        try:
            import grm_findings as gf  # 무거운 의존성 없음 — 결정론 키워드 분류기
            _CLASSIFIER = gf.classify_finding_category
        except Exception:  # noqa: BLE001 — 분류가 없어도 적재는 한다
            _CLASSIFIER = lambda _t: ""  # noqa: E731
    try:
        return _CLASSIFIER(text or "") or ""
    except Exception:  # noqa: BLE001
        return ""


# ── 행 ─────────────────────────────────────────────────────────────────────────────────

@dataclass
class Row:
    row_no: int                     # 원천 안 위치(xlsx 행 번호 / PDF 는 파일 안 순번) — 멱등 키의 절반
    locator: str                    # 사람이 원천에서 찾아가는 자리: 'Sheet1!R12' · 'p.7 #2'
    item_no: str = ""               # PMDA No. / MHRA Blinded ID
    statement: str = ""             # 지적 원문(MHRA xlsx 는 비어 있다)
    context: str = ""               # PMDA 指摘に至った背景 / MHRA PDF 머리 문장
    agency_system: str = ""         # PMDA システム / System
    agency_category: str = ""       # PMDA 指摘分類 / MHRA 장(Chapter/Annex)
    product_scope: str = ""         # PMDA 調査の区分·許可区分 / MHRA SITE_TYPE
    grade: str = ""                 # critical | major | other (MHRA)
    deficiency_label: str = ""      # MHRA 'Major-2' 원문
    site_group: str = ""            # 같은 제조소(PMDA 각주) / 같은 실사(MHRA Blinded ID)
    country: str = ""
    clauses: list[dict[str, Any]] = field(default_factory=list)
    grm_category: str = ""
    grm_category_basis: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        d = {k: v for k, v in self.__dict__.items()}
        return hashlib.sha256(json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["clause_keys"] = sorted({c["key"] for c in self.clauses})
        d["content_hash"] = self.content_hash()
        return d


class StructureError(RuntimeError):
    """원천 형식이 기대와 다르다 — 쓰지 않는다."""


# ── PMDA 별첨 ────────────────────────────────────────────────────────────────────────────

_PMDA_HEADERS = {
    # 열 의미 → 머리글에 들어 있는 낱말(앞의 것이 먼저). 일본어·영문 둘 다.
    "no": ("no.", "no"),
    "system": ("システム", "system"),
    "category": ("指摘分類", "指定分類", "category of deficiency", "deficiency classification"),
    "statement": ("指摘事項", "description of deficiency", "deficiency"),
    "context": ("背景", "background"),
    "scope": ("調査の区分", "許可", "category of inspected product", "category of product", "license"),
}
_YEAR_IN_TITLE = re.compile(r"(?:FY|Report)\s*(20\d\d)", re.I)
# ※No.7～9、No.10～12 … / No. 2 to 4 and No. 15 and 16 / observations No.3～4、No.5～7
_GROUP_RANGE = re.compile(r"No\.?\s*(\d+)\s*(?:[～〜~\-–]|to)\s*(?:No\.?\s*)?(\d+)", re.I)
_GROUP_PAIR = re.compile(r"No\.?\s*(\d+)\s+and\s+(\d+)(?!\s*(?:[～〜~\-–]|to))", re.I)
_GROUP_NOTE = re.compile(r"同一の製造所|same manufacturing site", re.I)


def _match_header(cells: dict[str, str]) -> dict[str, str] | None:
    norm = {col: clean(v).lower() for col, v in cells.items()}
    if not any(v in ("no.", "no") for v in norm.values()):
        return None
    found: dict[str, str] = {}
    for col, v in norm.items():
        for name, needles in _PMDA_HEADERS.items():
            if name in found:
                continue
            if name == "no":
                if v in needles:
                    found[name] = col
                    break
            elif any(n.lower() in v for n in needles):
                # '指摘事項の背景' 은 statement 가 아니라 context — 배경을 먼저 본다.
                if name == "statement" and ("背景" in v or "background" in v):
                    continue
                found[name] = col
                break
    return found


def site_groups(notes: Iterable[str]) -> dict[int, int]:
    """각주의 '같은 제조소' 묶음 → {No: 묶음 첫 No}. 각주가 없거나 못 읽으면 빈 dict."""
    out: dict[int, int] = {}
    for note in notes:
        if not _GROUP_NOTE.search(note):
            continue
        spans = [(int(a), int(b)) for a, b in _GROUP_RANGE.findall(note)]
        spans += [(int(a), int(b)) for a, b in _GROUP_PAIR.findall(note)]
        for a, b in spans:
            if a < b <= a + 30:
                for n in range(a, b + 1):
                    out.setdefault(n, a)
    return out


def parse_pmda_attachment(data: bytes, *, lang: str) -> tuple[int | None, list[Row]]:
    """(문서 제목의 연도, 행 목록). 머리글 줄을 못 찾거나 필수 열이 없으면 StructureError."""
    sheets = read_xlsx(data)
    if not sheets:
        raise StructureError("pmda: 시트 없음")
    sheet_name, rows = sheets[0]
    year = None
    header: dict[str, str] | None = None
    header_row = 0
    for r, cells in rows[:8]:
        for v in cells.values():
            if year is None and (m := _YEAR_IN_TITLE.search(v)):
                year = int(m[1])
        if header is None and (h := _match_header(cells)):
            header, header_row = h, r
    if not header or not {"no", "category", "statement"} <= set(header):
        raise StructureError(f"pmda: 머리글 열을 못 찾음 ({header})")
    notes = [clean(" ".join(c.values())) for r, c in rows if r > header_row and not
             clean(c.get(header["no"], "")).isdigit()]
    groups = site_groups(notes)
    out: list[Row] = []
    for r, cells in rows:
        if r <= header_row:
            continue
        no = clean(cells.get(header["no"], ""))
        if not no.isdigit():
            continue
        get = lambda k: clean(cells.get(header[k], "")) if k in header else ""  # noqa: E731
        statement = get("statement")
        if not statement:
            continue
        row = Row(row_no=r, locator=f"{sheet_name}!R{r}", item_no=no, statement=statement,
                  context=get("context"), agency_system=get("system"),
                  agency_category=get("category"), product_scope=get("scope"))
        if int(no) in groups:
            row.site_group = f"no{groups[int(no)]}"
        row.clauses = jp_clauses(statement, lang)
        code = map_pmda_category(row.agency_category)
        if code:
            row.grm_category, row.grm_category_basis = code, "agency_category_map"
        elif lang == "en" and (code := text_category(statement)):
            row.grm_category, row.grm_category_basis = code, "text_classifier"
        out.append(row)
    return year, out


# ── MHRA xlsx ────────────────────────────────────────────────────────────────────────────

_GRADE = re.compile(r"^\s*(Critical|Major|Other)\s*-\s*(\d+)\s*$", re.I)
_MHRA_FLAGS = ("TSE", "DISTRIBUTOR", "NON_STERILE_PRODUCTS", "PACKAGING_ONLY", "STERILE_PRODUCTS",
               "BIOLOGICAL_ACTIVES", "IMPORTER", "STERILE_NON_STERILE_MATERIAL", "ASSEMBLY")


def parse_mhra_xlsx(data: bytes) -> list[Row]:
    sheets = read_xlsx(data)
    data_sheet = next(((n, r) for n, r in sheets if n.lower().startswith("deficiency data")), None)
    if data_sheet is None:
        raise StructureError(f"mhra: 'Deficiency Data' 시트 없음 ({[n for n, _ in sheets]})")
    sheet_name, rows = data_sheet
    if not rows:
        raise StructureError("mhra: 빈 시트")
    head_r, head = rows[0]
    cols = {clean(v).upper(): col for col, v in head.items()}
    need = {"BLINDED ID", "COUNTRY", "CHAPTER", "REFERENCE", "CRITICAL / MAJOR / OTHER"}
    if not need <= set(cols):
        raise StructureError(f"mhra: 필수 열 없음 {sorted(need - set(cols))}")
    out: list[Row] = []
    for r, cells in rows[1:]:
        g = lambda k: clean(cells.get(cols.get(k, ""), ""))  # noqa: E731
        bid = g("BLINDED ID")
        if not bid:
            continue
        label = g("CRITICAL / MAJOR / OTHER")
        gm = _GRADE.match(label)
        chapter = g("CHAPTER")
        ref = g("REFERENCE")
        row = Row(row_no=r, locator=f"{sheet_name}!R{r}", item_no=bid,
                  agency_category=chapter, product_scope=g("SITE_TYPE"),
                  grade=gm[1].lower() if gm else "", deficiency_label=label,
                  site_group=bid, country=g("COUNTRY"))
        row.attrs = {"reference": ref, "deficiency_no": int(gm[2]) if gm else None,
                     "flags": {f.lower(): g(f) == "Yes" for f in _MHRA_FLAGS if f in cols}}
        if c := eu_clause(ref, chapter):
            row.clauses = [c]
            code = EU_PART_MAP.get(c["key"], "")
            if code:
                row.grm_category, row.grm_category_basis = code, "eu_gmp_part_map"
        out.append(row)
    return out


# ── MHRA Trend PDF ─────────────────────────────────────────────────────────────────────────

# 2015: "Examples Deficiencies – Chapter 1" / 2016: "Chapter 1 - Deficiency examples"
_PDF_TITLE = re.compile(
    r"^\s*(?:Examples?\s+Deficienc(?:y|ies)\s*[–\-—]\s*(?P<a>Chapter|Annex)\s*(?P<an>\d{1,2})"
    r"|(?P<b>Chapter|Annex)\s*(?P<bn>\d{1,2})\s*[–\-—]\s*Deficienc(?:y|ies)\s+examples?)\s*$", re.I)
_BULLET = re.compile(r"^\s*[•●▪]\s*")


def _join(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(ln.strip() for ln in lines)).strip()


def parse_trend_page(text: str) -> tuple[str, str, list[str]] | None:
    """한 쪽 → (장 키 'EU GMP Chapter 1', 머리 문장, [글머리 문장]). 예시 쪽이 아니면 None."""
    lines = [ln for ln in (text or "").splitlines()]
    title_i = None
    for i, ln in enumerate(lines[:4]):
        if m := _PDF_TITLE.match(ln):
            kind, num = (m["a"] or m["b"]).title(), int(m["an"] or m["bn"])
            title_i = i
            break
    if title_i is None:
        return None
    body = lines[title_i + 1:]
    lead: list[str] = []
    bullets: list[list[str]] = []
    for ln in body:
        if _BULLET.match(ln):
            rest = _BULLET.sub("", ln)
            bullets.append([rest] if rest.strip() else [])
        elif bullets:
            bullets[-1].append(ln)
        else:
            lead.append(ln)
    lead_s = _join(lead)
    items = [s for s in (_join(b) for b in bullets) if s]
    if not items and lead_s:
        # 글머리가 없는 쪽: "Deficiency related to X:" 머리줄 + 본문 한 덩어리.
        m = re.match(r"^(Deficienc(?:y|ies) (?:related to|on)[^:]{0,120}:)\s*(.+)$", lead_s, re.I)
        if m:
            lead_s, items = m[1], [m[2]]
        else:
            lead_s, items = "", [lead_s]
    return f"EU GMP {kind} {num}", lead_s, items


def parse_mhra_trend_pdf(data: bytes) -> list[Row]:
    import pymupdf  # requirements.txt 고정(PyMuPDF==1.28.2) — xlsx 만 다룰 때는 필요 없다

    doc = pymupdf.open(stream=data, filetype="pdf")
    out: list[Row] = []
    seq = 0
    for pno, page in enumerate(doc, 1):
        parsed = parse_trend_page(page.get_text())
        if not parsed:
            continue
        key, lead, items = parsed
        for k, s in enumerate(items, 1):
            seq += 1
            row = Row(row_no=seq, locator=f"p.{pno} #{k}", statement=s, context=lead,
                      agency_category=key.replace("EU GMP ", ""))
            row.clauses = [{"key": key, "para": "", "raw": key.replace("EU GMP ", ""),
                            "basis": "mhra_page_heading", "confidence": 0.9}]
            row.clauses += [c for c in jp_clauses(s, "en") if c["basis"] == "cfr_text"]
            code = text_category(" ".join([lead, s])) or ""
            if code and code != "other_quality_system":
                row.grm_category, row.grm_category_basis = code, "text_classifier"
            elif EU_PART_MAP.get(key):
                row.grm_category, row.grm_category_basis = EU_PART_MAP[key], "eu_gmp_part_map"
            out.append(row)
    return out


# ── 원천 받기 ────────────────────────────────────────────────────────────────────────────

def http_get(url: str, *, timeout: int = 60, retries: int = 2) -> tuple[bytes, dict[str, str]]:
    import requests

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=timeout)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} {url}")
            return r.content, {k.lower(): v for k, v in r.headers.items()}
        except Exception as exc:  # noqa: BLE001 — 재시도 후 실패로 보고(우회하지 않는다)
            last = exc
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last}")


def parse_source(src: Source, data: bytes) -> tuple[list[Row], dict[str, Any]]:
    meta: dict[str, Any] = {}
    if src.kind == "pmda_attachment":
        year, rows = parse_pmda_attachment(data, lang=src.lang)
        meta["title_year"] = year
        if year is not None and year != src.year:
            raise StructureError(f"{src.key}: 문서 제목 연도 {year} ≠ 기대 {src.year}")
    elif src.kind == "mhra_xlsx":
        rows = parse_mhra_xlsx(data)
    elif src.kind == "mhra_trend_pdf":
        rows = parse_mhra_trend_pdf(data)
    else:
        raise StructureError(f"알 수 없는 kind {src.kind}")
    if len(rows) < src.min_rows:
        raise StructureError(f"{src.key}: 행 {len(rows)} < 하한 {src.min_rows}")
    keys = [r.row_no for r in rows]
    if len(keys) != len(set(keys)):
        raise StructureError(f"{src.key}: row_no 중복")
    return rows, meta


def summarize(rows: list[Row]) -> dict[str, Any]:
    from collections import Counter

    n = len(rows)
    with_clause = sum(1 for r in rows if r.clauses)
    stated = sum(1 for r in rows if r.statement)
    keys = Counter(c["key"] for r in rows for c in r.clauses)
    return {
        "rows": n,
        "with_statement": stated,
        "with_clause": with_clause,
        "clause_rate": round(with_clause / n, 3) if n else 0.0,
        "with_grm_category": sum(1 for r in rows if r.grm_category),
        "grm_category": dict(Counter(r.grm_category or "(none)" for r in rows).most_common()),
        "grm_category_basis": dict(Counter(r.grm_category_basis or "(none)" for r in rows).most_common()),
        "agency_category_top": dict(Counter(r.agency_category or "(none)" for r in rows).most_common(25)),
        "clause_keys_top": dict(keys.most_common(15)),
        "grade": dict(Counter(r.grade for r in rows if r.grade).most_common()),
        "site_groups": len({r.site_group for r in rows if r.site_group}),
    }


# ── 새 파일 감지 ──────────────────────────────────────────────────────────────────────────

_XLSX_LINK = re.compile(r'href="(?P<href>[^"]+?\.xlsx)"[^>]*>(?P<label>.*?)</a>', re.S | re.I)


def discover(fetch: Callable[[str], tuple[bytes, dict[str, str]]]) -> dict[str, Any]:
    """목록 페이지의 xlsx 링크 중 SOURCES(+사본·무시 목록)에 없는 것. 페이지를 못 읽으면 failures."""
    known = {s.url.rsplit("/", 1)[1] for s in SOURCES}
    known |= {f"{a}.xlsx" for s in SOURCES for a in s.aliases}
    found: list[dict[str, str]] = []
    failures: list[str] = []
    pages = [("PMDA", "ja", PMDA_INDEX["ja"]), ("PMDA", "en", PMDA_INDEX["en"]), ("MHRA", "en", MHRA_INDEX)]
    for agency, lang, url in pages:
        try:
            body, _ = fetch(url)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"index:{agency}:{lang}:{exc}")
            continue
        page = body.decode("utf-8", "replace")
        links = list(_XLSX_LINK.finditer(page))
        if not links:
            failures.append(f"index:{agency}:{lang}:xlsx 링크 0개 — 페이지 형식 변경 의심")
        for m in links:
            name = m["href"].rsplit("/", 1)[1]
            if name in known or name in MHRA_IGNORED_FILES:
                continue
            label = re.sub(r"<[^>]+>|\s+", " ", m["label"]).strip()
            href = m["href"] if m["href"].startswith("http") else "https://www.pmda.go.jp" + m["href"]
            found.append({"agency": agency, "lang": lang, "url": href, "label": label[:200]})
    return {"new_files": found, "failures": failures}


# ── Supabase(PostgREST RPC) ─────────────────────────────────────────────────────────────

class Db:
    def __init__(self, base_url: str, key: str, *, http: Any = None) -> None:
        if http is None:
            import requests
            http = requests
        self.http = http
        self.base = base_url.rstrip("/")
        self.key = key

    def _h(self) -> dict[str, str]:
        return {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json", "Accept": "application/json"}

    def known_sha(self) -> dict[str, str]:
        r = self.http.get(f"{self.base}/rest/v1/deficiency_example_sources",
                          params={"select": "source_key,sha256,rows_loaded"}, headers=self._h(), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"sources read HTTP {r.status_code}")
        return {d["source_key"]: d["sha256"] for d in r.json() if d.get("rows_loaded")}

    def rpc(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(f"{self.base}/rest/v1/rpc/{name}", json=payload, headers=self._h(), timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"rpc {name} HTTP {r.status_code}: {str(getattr(r, 'text', ''))[:300]}")
        return r.json()


CHUNK = 800


def source_payload(src: Source, sha: str, size: int, headers: dict[str, str], n: int) -> dict[str, Any]:
    return {"source_key": src.key, "agency": src.agency, "year": src.year, "lang": src.lang,
            "kind": src.kind, "url": src.url, "index_url": src.index_url, "sha256": sha, "bytes": size,
            "last_modified": headers.get("last-modified", "")[:80], "rows_parsed": n,
            "parser_version": PARSER_VERSION}


def write_source(db: Db, src: Source, rows: list[Row], meta: dict[str, Any]) -> dict[str, Any]:
    """chunk 단위 upsert → finalize(이번에 없는 row_no 삭제 + 원천 장부 갱신). 같은 입력 재실행 = 변화 0."""
    totals = {"inserted": 0, "updated": 0, "unchanged": 0}
    payload_rows = [r.to_payload() for r in rows]
    for i in range(0, len(payload_rows), CHUNK):
        res = db.rpc("deficiency_examples_upsert",
                     {"p_source_key": src.key, "p_agency": src.agency, "p_year": src.year,
                      "p_lang": src.lang, "p_rows": payload_rows[i:i + CHUNK]})
        for k in totals:
            totals[k] += int(res.get(k, 0))
    fin = db.rpc("deficiency_examples_finalize",
                 {"p_source": meta["source"], "p_keep": [r.row_no for r in rows]})
    totals["deleted"] = int(fin.get("deleted", 0))
    return totals


# ── 실행 ───────────────────────────────────────────────────────────────────────────────

def env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def run(*, apply: bool, only: set[str] | None, fetch: Callable[[str], tuple[bytes, dict[str, str]]],
        db: Db | None, do_discover: bool, raw_dir: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"mode": "apply" if apply else "dry-run", "parser_version": PARSER_VERSION,
                              "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                              "sources": [], "failures": [], "failure_kind": ""}
    known: dict[str, str] = {}
    if apply and db is not None:
        try:
            known = db.known_sha()
        except Exception as exc:  # noqa: BLE001
            report["failures"].append(f"db:known_sha:{exc}")
            report["failure_kind"] = "db"
            return report
    for src in SOURCES:
        if only and src.key not in only and src.agency.lower() not in only:
            continue
        ent: dict[str, Any] = {"key": src.key, "agency": src.agency, "year": src.year, "lang": src.lang,
                               "kind": src.kind, "url": src.url}
        report["sources"].append(ent)
        try:
            data, headers = fetch(src.url)
        except Exception as exc:  # noqa: BLE001
            ent["error"] = str(exc)
            report["failures"].append(f"fetch:{src.key}")
            report["failure_kind"] = report["failure_kind"] or "fetch"
            continue
        sha = hashlib.sha256(data).hexdigest()
        ent.update({"sha256": sha, "bytes": len(data), "last_modified": headers.get("last-modified", "")})
        if raw_dir:
            os.makedirs(raw_dir, exist_ok=True)
            with open(os.path.join(raw_dir, src.url.rsplit("/", 1)[1]), "wb") as fh:
                fh.write(data)
        try:
            rows, meta = parse_source(src, data)
        except (StructureError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
            ent["error"] = f"structure: {exc}"
            report["failures"].append(f"structure:{src.key}")
            report["failure_kind"] = "structure"
            continue
        ent["summary"] = summarize(rows)
        ent["sample"] = [{"locator": r.locator, "statement": r.statement[:160], "clauses": r.clauses[:3],
                          "agency_category": r.agency_category, "grm_category": r.grm_category}
                         for r in rows[:2]]
        if not apply or db is None:
            continue
        if known.get(src.key) == sha:
            ent["write"] = "skipped (sha 같음 — 이미 적재)"
            continue
        meta["source"] = source_payload(src, sha, len(data), headers, len(rows))
        try:
            ent["write"] = write_source(db, src, rows, meta)
        except Exception as exc:  # noqa: BLE001
            ent["error"] = f"db: {exc}"
            report["failures"].append(f"db:{src.key}")
            report["failure_kind"] = "db"
    if do_discover:
        d = discover(fetch)
        report["discover"] = d
        if d["failures"]:
            report["failures"] += d["failures"]
            report["failure_kind"] = report["failure_kind"] or "structure"
    return report


def totals(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in report["sources"]:
        s = e.get("summary") or {}
        out.append({"key": e["key"], "agency": e["agency"], "year": e["year"], "lang": e["lang"],
                    "rows": s.get("rows"), "with_statement": s.get("with_statement"),
                    "clause_rate": s.get("clause_rate"), "error": e.get("error", "")})
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows cp949 콘솔에서 일본어·● 가 죽지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="Supabase 에 쓴다(ENABLE_DEFICIENCY_EXAMPLES=true 일 때만 실제로 쓴다)")
    ap.add_argument("--only", default="", help="쉼표 목록: source_key 또는 기관(pmda,mhra)")
    ap.add_argument("--discover", action="store_true", help="목록 페이지에서 새 xlsx 를 찾는다(exit 3)")
    ap.add_argument("--output", default="", help="리포트 JSON 경로")
    ap.add_argument("--raw-dir", default="", help="받은 원본 파일을 둘 폴더(아티팩트용)")
    args = ap.parse_args(argv)

    enabled = env_flag("ENABLE_DEFICIENCY_EXAMPLES")
    apply = bool(args.apply and enabled)
    if args.apply and not enabled:
        print("--apply 지만 ENABLE_DEFICIENCY_EXAMPLES 가 off — dry-run 으로 돈다", file=sys.stderr)
    db = None
    if apply:
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            print("--apply + ENABLE_DEFICIENCY_EXAMPLES=true 인데 SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 가 없다",
                  file=sys.stderr)
            return 2
        db = Db(url, key)
    only = {s.strip().lower() for s in args.only.split(",") if s.strip()} or None
    report = run(apply=apply, only=only, fetch=http_get, db=db, do_discover=args.discover,
                 raw_dir=args.raw_dir or None)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    for t in totals(report):
        print(f"{t['key']:<28} {t['agency']} {t['year']} {t['lang']}  rows={t['rows']}  "
              f"stmt={t['with_statement']}  clause_rate={t['clause_rate']}  {t['error']}")
    if report.get("discover", {}).get("new_files"):
        print("새 파일:", json.dumps(report["discover"]["new_files"], ensure_ascii=False))
    print(f"mode={report['mode']} failures={report['failures']}")
    if report["failures"]:
        return 2
    if report.get("discover", {}).get("new_files"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
