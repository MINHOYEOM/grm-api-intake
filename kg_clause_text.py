#!/usr/bin/env python3
"""지적 본문이 **글자 그대로** 인용한 조항을 finding_clause 키로 옮긴다(migration 113 의 파이썬 기준 구현).

원칙: 틀리게 잇느니 안 잇는다. 본문에 없는 조항(예: 캐나다 `C.02.xxx`, 483 의 암묵 조항)은 추측하지 않는다.
측정(2026-11-05): 483 본문의 `21 CFR` 18건은 cfr_refs 에 이미 모두 있음 · HC `C.02.` 0건 · GUI-0080 100건 ·
EU NCR Annex 1/11/16 인용 약 18건 · MHRA 0건. 키는 106 형식과 정확히 같다('21 CFR 211.22').
"""
from __future__ import annotations

import re

Citation = tuple[str, str, float, str]  # key, paragraph, confidence, basis

_CFR_TEXT = re.compile(r"\b21\s*CFR\s*(?:Part\s*)?(\d{1,4})\.(\d{1,4})((?:\([A-Za-z0-9]+\))*)", re.I)
# EU GMP 부속서: 본문이 Annex 1(I)·11·16 을 직접 말한 경우만. "(ANNEX 2)" 같은 허가서 부록 표기는 괄호로 거른다.
_EU_ANNEX = re.compile(r"(?<!\()\bannex\s*(1|i|11|16)\b(?!\))", re.I)
_HC_GUI = re.compile(r"\bGUI-?\s?(\d{4})\b", re.I)

EU_SOURCES = ("EU GMP NCR (EudraGMDP)", "MHRA GMP NCR")
HC_SOURCE = "Health Canada Inspection"
CFR_SOURCES = ("FDA 483", "FDA Warning Letter")


def parse_text_citations(source: str, text: str) -> list[Citation]:
    out: dict[tuple[str, str], Citation] = {}
    t = text or ""
    if source in CFR_SOURCES:
        for m in _CFR_TEXT.finditer(t):
            key = f"21 CFR {m[1]}.{m[2]}"
            out.setdefault((key, m[3].lower()), (key, m[3].lower(), 0.95, "cfr_text"))
    elif source in EU_SOURCES:
        for m in _EU_ANNEX.finditer(t):
            n = "1" if m[1].lower() == "i" else m[1]
            out.setdefault((f"EU GMP Annex {n}", ""), (f"EU GMP Annex {n}", "", 0.9, "eu_gmp_annex_text"))
    elif source == HC_SOURCE:
        for m in _HC_GUI.finditer(t):
            out.setdefault((f"Canada GUI-{m[1]}", ""), (f"Canada GUI-{m[1]}", "", 0.9, "hc_guidance_text"))
    return list(out.values())
