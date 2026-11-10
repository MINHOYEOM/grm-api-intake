#!/usr/bin/env python3
"""[483 구체 내용 · 2026-11-10] 상세 국문 번역 검증 — 결정론 규칙(LLM 없음).

번역은 세션의 하위 에이전트(구독 경로)가 하고, 이 검사가 outbox 에 올리기 전과 CI 에서 같은 규칙으로 거른다.
규칙(하나라도 어기면 그 항목은 실격):
  ① 국문에 한글이 있고 한자(CJK 통합 한자)가 없다.
  ② 영문의 숫자 묶음(수치·연도·날짜·CFR 조항 번호·로트 번호)이 국문에 모두 있다.
  ③ 가림 표시 `[redacted]` 개수가 같다.
  ④ 길이 비율(국문 글자 수 / 영문 글자 수)이 0.2~1.3 — 누락·덧붙임 방어.
  ⑤ 영문의 대문자 약어·코드(예: SOP-QC-012, HEPA, CAPA)가 국문에 그대로 있다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_HANGUL_RE = re.compile(r"[가-힣]")
_HANJA_RE = re.compile(r"[一-鿿㐀-䶿]")
_NUM_RE = re.compile(r"\d+(?:[.,/:\-]\d+)*")
_CODE_RE = re.compile(r"\b(?=[A-Z0-9\-]*[A-Z]{2})[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\b")
_REDACTED = "[redacted]"
RATIO_MIN, RATIO_MAX = 0.2, 1.3
# 대문자라도 번역되는 것이 자연스러운 낱말(문장 첫머리·강조·양식 문구)
_CODE_ALLOW = {"I", "A", "B", "C", "D", "E", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
               "OBSERVATION", "SPECIFICALLY", "FOR", "THE", "AND", "OR", "OF", "NOT", "NO", "ON", "IN",
               "TO", "AT", "BY", "IS", "ARE", "WAS", "WERE", "YOUR", "FIRM", "NOTE", "NA", "N", "AM", "PM"}


def _nums(text: str) -> Counter:
    return Counter(m.group(0).rstrip(".,") for m in _NUM_RE.finditer(text or ""))


def _codes(text: str) -> set[str]:
    return {c for c in _CODE_RE.findall(text or "") if c not in _CODE_ALLOW and len(c) >= 2}


def check(en: str, ko: str) -> list[str]:
    """어긴 규칙 목록(빈 목록 = 통과). 순수 함수."""
    problems: list[str] = []
    en, ko = str(en or ""), str(ko or "")
    if not _HANGUL_RE.search(ko):
        problems.append("no_hangul")
    if _HANJA_RE.search(ko):
        problems.append("hanja")
    missing = [n for n, c in _nums(en).items() if _nums(ko)[n] < c and n not in ko]
    if missing:
        problems.append("numbers_missing:" + ",".join(missing[:5]))
    if en.count(_REDACTED) != ko.count(_REDACTED):
        problems.append(f"redacted_count:{en.count(_REDACTED)}->{ko.count(_REDACTED)}")
    if en.strip():
        ratio = len(ko) / len(en)
        if not RATIO_MIN <= ratio <= RATIO_MAX:
            problems.append(f"length_ratio:{ratio:.2f}")
    lost = sorted(c for c in _codes(en) if c not in ko)
    if lost:
        problems.append("codes_missing:" + ",".join(lost[:5]))
    return problems


def check_items(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """finding_id → 어긴 규칙(통과 항목은 빠짐)."""
    out: dict[str, list[str]] = {}
    for it in items:
        probs = check(it.get("detail_text", ""), it.get("detail_text_ko", ""))
        if probs:
            out[str(it.get("finding_id"))] = probs
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    paths = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    bad = 0
    for p in paths:
        items = json.loads(p.read_text(encoding="utf-8"))
        res = check_items(items)
        bad += len(res)
        print(f"{p.name}: {len(items)} items, {len(res)} failed")
        for fid, probs in list(res.items())[:50]:
            print(f"  {fid}: {'; '.join(probs)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
