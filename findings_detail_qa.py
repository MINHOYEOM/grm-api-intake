#!/usr/bin/env python3
"""[483 구체 내용 · 2026-11-10] 상세 국문 번역 검증 — 결정론 규칙(LLM 없음).

번역은 세션의 하위 에이전트(구독 경로)가 하고, 이 검사가 outbox 에 올리기 전과 CI 에서 같은 규칙으로 거른다.
규칙(하나라도 어기면 그 항목은 실격):
  ① 국문에 한글이 있고 한자(CJK 통합 한자)가 없다.
  ② 영문의 숫자 묶음(수치·연도·날짜·CFR 조항 번호·로트 번호)이 국문에 모두 있다.
  ③ 가림 표시 `[redacted]` 개수가 같다.
  ④ 길이 비율(국문 글자 수 / 영문 글자 수)이 0.2~1.3 — 누락·덧붙임 방어.
  ⑤ 영문의 대문자 약어·코드(예: SOP-QC-012, HEPA, CAPA)가 국문에 그대로 있다.

[11-15 읽기 좋음 · strict] 대표 신고("이러면 사람들이 어떻게 읽겠어") — 국문에 OCR 찌꺼기가 그대로 남았다.
  원인은 옛 지침 "판독 잡음은 원문 그대로 둔다"와 ③의 개수 일치였다. 영문 원문(detail_text)은 저장값
  그대로 두고 **국문만** 읽히게 옮긴다(지침: docs/specs/483_detail_translation_brief.md).
  strict=True 일 때 아래를 더 본다(옛 배치 파일은 옛 규칙만 — tests 의 LEGACY 목록):
  ⑥ form_block     — 483 양식 칸(DISTRICT ADDRESS·Industry Information·NAME AND TITLE…·Fax:)이 국문에 남음
  ⑦ estab_header   — 쪽 머리글의 업소 유형 줄("Japan API Manufacturer"·"producer of …")이 국문에 남음
  ⑧ redact_variant — 가림 표시가 [redacted] 가 아닌 꼴((b)(4)·b) (4)·() (4))로 남음
  ⑨ untranslated   — 영어 문장 조각(낱말 5개 이상 연속, 기능어 2개 이상)이 번역되지 않고 남음
  ⑩ ocr_token      — 낱말 속에 숫자·기호가 낀 OCR 찌꺼기(c0A·ste1ile)가 남음
  ③은 "국문 ≥ 영문"으로 완화한다(깨진 가림 표시를 [redacted] 로 바로잡으면 늘 수 있다).
  ②·⑤는 영문의 양식 칸·머리글 줄 근처에만 있던 숫자·코드와 글자 사이에 낀 숫자(c0A 의 0)를 면제한다.
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


# ── strict(읽기 좋음) 규칙 ──────────────────────────────────────────────
FORM_BLOCK_RE = re.compile(
    r"Industry\s*Info|www\.?\s*fda\.?\s*gov|DISTRICT\s+(?:OFFICE\s+)?ADD?RESS|NAME\s+AN[DO]\s+TITLE"
    r"|TO\s+WHOM\s+REPORT|FEI\s+NUMBER|DATE\(?S\)?\s+OF\s+INSPEC|\bFax\s*:|\bPhone\s*:"
    r"|ESTABLISHMENT\s+INSPECTED|STREET\s+ADDRESS|EMPLOYEE\(?S\)?\s+SIGNATURE|SEE\s+REVERSE"
    r"|INSPECTIONAL\s+OBSERVATIONS|\bPAGE\s+\d+\s+OF\s+\d+|PREVIOUS\s+EDITION|HEALTH\s+AND\s+HUMAN\s+SERVICES"
    # OCR 로 철자가 깨진 양식 칸(실측: "Sff REVERSE Of THIS PAGE"·"STATEANDZIPCODE"·"ESTABLISMENTINPECTED"·"FORM FOA 483")
    r"|REVERSE\s*O\S?\s*THIS\s*PAGE|STATE\s*AND\s*ZIP|ZIP\s*CODE|ESTABL\w*\s*IN\w{0,3}PECTED|\bFORM\s+F\S{1,2}\s+483"
    # 실측: "CHY, STATE. ZJPCOOE, COUNTRY"·"c1rv, STAT!:. ZIPCC0E"·"DATEiSJ OF INSPtiCTtON"·"STREET ACCRESS"·"TYPE ESTAB.ISMMENT"
    r"|\b\S{1,4}[,.]?\s*STAT\S{0,3}[,.]?\s*Z\S{1,2}P\s*\S{2,4}E\b|ZIP\s*\S{3,4}E[,.]?\s*COUNTRY|\bCOUNTRY\s+\S{0,2}\s*T\S?PE\b"
    r"|DATE\S{0,4}\s*OF\s*INSP\S*|STRE?E?T\s+A\S{1,3}RESS|TYPE\s+EST\S*"
    r"|,\s*(?:Senior\s+|Lead\s+)?Invest\s?igator\b|\bDATE\s+ISSUED\b",
    re.IGNORECASE)
# 대문자 낱말 3개 이상이 잇따르면 약어가 아니라 양식 칸·강조 문장이다("THIS IS A REPEAT OBSERVATION") — 번역 대상.
_CAPS_RUN_RE = re.compile(r"\b[A-Z]{2,}\b(?:[\s,.*:;/-]+\b[A-Z]{1,}\b){2,}")
ESTAB_HEADER_RE = re.compile(
    r"\b(?:API|Drug|Device|Outsourcing|Pharmaceutical|Biologics?|Sterile|Human\s+Drug|Medical\s+Device)\s+"
    r"(?:Manufacturer|Facility|Packager|Relabeler|Repacker)\b|\bproducer\s+of\b|\bCompounding\s+Pharmacy\b",
    re.IGNORECASE)
REDACT_VARIANT_RE = re.compile(r"\(b\)\s*\(\d\)|(?<!\()b\)\s*\(\d\)|\(\)\s*\(\d\)|\(b\s*\(\d\)")
_QUOTED_RE = re.compile(r"\"[^\"]{1,200}\"|“[^”]{1,200}”|‘[^’]{1,200}’|「[^」]{1,200}」")
_LATIN_RUN_RE = re.compile(r"(?:[A-Za-z][A-Za-z'’.\-]*[\s,;:/]+){4,}[A-Za-z][A-Za-z'’.\-]*")
_FUNC_WORD_RE = re.compile(
    r"(?<![A-Za-z])(?:the|of|and|for|to|in|is|was|were|are|not|with|that|by|on|from|this|which|have|has|been)"
    r"(?![A-Za-z])", re.IGNORECASE)
OCR_TOKEN_RE = re.compile(r"[A-Za-z]*(?:[a-z][0-9|!~\\][A-Za-z]|[A-Za-z][0-9|!~\\][a-z])[A-Za-z]*")
_OCR_TOKEN_ALLOW = re.compile(r"^q\d+h$", re.IGNORECASE)  # 투약 간격(q12h)
_NUM_STRICT_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,/:\-]\d+)*(?![A-Za-z])")
_REDACT_DIGITS_RE = re.compile(r"(?<!\d)(0?[67]?1{0,2}[46][1l]{0,2}[46]?[1l]{0,2})(?![\d.,/:\-]\d)")
_HEADER_LEAD = 90  # 머리글 줄은 업소 유형 앞에 도시·우편번호가 온다
_FORM_BACK = 80     # 양식 칸 표지 앞(서명·날짜)
_FORM_SPAN = 220    # 양식 칸 표지 뒤의 주소·전화·날짜


def _header_mask(en: str) -> list[tuple[int, int]]:
    spans = [(max(0, m.start() - _HEADER_LEAD), m.end()) for m in ESTAB_HEADER_RE.finditer(en)]
    spans += [(max(0, m.start() - _FORM_BACK), m.end() + _FORM_SPAN) for m in FORM_BLOCK_RE.finditer(en)]
    # 가림 표시 (b)(4) 의 4 는 수치가 아니다 — [redacted] 로 바로잡으면 사라진다.
    spans += [(m.start(), m.end()) for m in REDACT_VARIANT_RE.finditer(en)]
    # OCR 이 (b)(4)·(b)(6) 을 숫자로 읽은 꼴(실측 "forf61141"·"r 1141"·"~bf<4>") — 1·4·6·7 로만 된 숫자가
    # 4자리 이상이거나, 글자·기호에 들러붙어 있으면 수치가 아니라 가림 표시다.
    for m in _REDACT_DIGITS_RE.finditer(en):
        d = m.group(1)
        glued = bool(re.match(r"[A-Za-z()<>~|!{}'\[\]]", en[m.start(1) - 1:m.start(1)] or " ")
                     or re.match(r"[A-Za-z()<>~|!{}'\[\]]", en[m.end(1):m.end(1) + 1] or " "))
        if len(d) >= 4 or glued:
            spans.append((m.start(1), m.end(1)))
    return spans


def _outside(spans: list[tuple[int, int]], pos: int) -> bool:
    return not any(a <= pos < b for a, b in spans)


def readability(ko: str) -> list[str]:
    """국문이 읽히는 글인지 — 영문 찌꺼기 검사(strict 전용). 순수 함수."""
    plain = str(ko or "").replace(_REDACTED, " ")
    problems: list[str] = []
    m = FORM_BLOCK_RE.search(plain)
    if m:
        problems.append("form_block:" + m.group(0)[:30])
    m = ESTAB_HEADER_RE.search(plain)
    if m:
        problems.append("estab_header:" + m.group(0)[:30])
    m = REDACT_VARIANT_RE.search(plain)
    if m:
        problems.append("redact_variant:" + m.group(0))
    # 따옴표 안(SOP·양식의 공식 제목 원문)은 영어로 둘 수 있다 — 지침 규칙 2.
    for run in _LATIN_RUN_RE.finditer(_QUOTED_RE.sub(" ", plain)):
        if len(_FUNC_WORD_RE.findall(run.group(0))) >= 2:
            problems.append("untranslated:" + run.group(0)[:40])
            break
    toks = [t for t in OCR_TOKEN_RE.findall(plain) if not _OCR_TOKEN_ALLOW.match(t)]
    if toks:
        problems.append("ocr_token:" + ",".join(toks[:5]))
    return problems


def _nums(text: str) -> Counter:
    return Counter(m.group(0).rstrip(".,") for m in _NUM_RE.finditer(text or ""))


def _codes(text: str) -> set[str]:
    return {c for c in _CODE_RE.findall(text or "") if c not in _CODE_ALLOW and len(c) >= 2}


OCR_DROPPED_MAX = 10


def _check_strict(en: str, ko: str, dropped: Any = None) -> list[str]:
    problems: list[str] = []
    if not _HANGUL_RE.search(ko):
        problems.append("no_hangul")
    if _HANJA_RE.search(ko):
        problems.append("hanja")
    # ocr_dropped: 번역자가 "OCR 이 깨뜨린 원문 토큰이라 옮기지 않았다"고 밝힌 숫자·코드(실측: 201'1 의 1·LJMS→LIMS·
    # IO→10·POR→PQR). 원문에 실제로 있는 토큰만, 항목당 10개까지 — 배치 파일에 남아 사람이 검토한다.
    drop: set[str] = set()
    if dropped:
        toks = [str(t) for t in dropped] if isinstance(dropped, list) else ["<not-a-list>"]
        bad = [t for t in toks if not t or t not in en]
        if bad or len(toks) > OCR_DROPPED_MAX:
            problems.append("ocr_dropped_invalid:" + ",".join(bad[:5] or [str(len(toks))]))
        drop = {t for t in toks if t and t in en}
    mask = _header_mask(en)
    need: Counter = Counter(m.group(0).rstrip(".,") for m in _NUM_STRICT_RE.finditer(en) if _outside(mask, m.start()))
    have = _nums(ko)
    missing = [n for n, c in need.items() if have[n] < c and n not in ko and n not in drop]
    if missing:
        problems.append("numbers_missing:" + ",".join(missing[:5]))
    if ko.count(_REDACTED) < en.count(_REDACTED):
        problems.append(f"redacted_count:{en.count(_REDACTED)}->{ko.count(_REDACTED)}")
    # 길이 비율은 양식 칸·머리글을 뺀 본문 기준(머리글이 큰 항목에서 머리글을 덧붙이게 만들지 않게).
    body_len = sum(1 for i in range(len(en)) if _outside(mask, i)) if mask else len(en)
    if en.strip() and body_len:
        ratio = len(ko) / body_len
        if ratio < RATIO_MIN or len(ko) / len(en) > RATIO_MAX:
            problems.append(f"length_ratio:{ratio:.2f}")
    caps =[(m.start(), m.end()) for m in _CAPS_RUN_RE.finditer(en)]
    lost = sorted({m.group(0) for m in _CODE_RE.finditer(en)
                   if m.group(0) not in _CODE_ALLOW and len(m.group(0)) >= 2 and _outside(mask, m.start())
                   and (_outside(caps, m.start()) or re.search(r"[\d-]", m.group(0)))
                   and m.group(0) not in ko and m.group(0) not in drop})
    if lost:
        problems.append("codes_missing:" + ",".join(lost[:5]))
    problems.extend(readability(ko))
    return problems


def check(en: str, ko: str, *, strict: bool = False, ocr_dropped: Any = None) -> list[str]:
    """어긴 규칙 목록(빈 목록 = 통과). 순수 함수. strict=True 는 11-15 읽기 좋음 규칙까지 본다."""
    if strict:
        return _check_strict(str(en or ""), str(ko or ""), ocr_dropped)
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


def check_items(items: list[dict[str, Any]], *, strict: bool = False) -> dict[str, list[str]]:
    """finding_id → 어긴 규칙(통과 항목은 빠짐)."""
    out: dict[str, list[str]] = {}
    for it in items:
        probs = check(it.get("detail_text", ""), it.get("detail_text_ko", ""), strict=strict,
                      ocr_dropped=it.get("ocr_dropped"))
        if probs:
            out[str(it.get("finding_id"))] = probs
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = list(argv if argv is not None else sys.argv[1:])
    strict = "--strict" in args
    paths = [Path(a) for a in args if a != "--strict"]
    bad = 0
    for p in paths:
        items = json.loads(p.read_text(encoding="utf-8"))
        res = check_items(items, strict=strict)
        bad += len(res)
        print(f"{p.name}: {len(items)} items, {len(res)} failed")
        for fid, probs in list(res.items())[:50]:
            print(f"  {fid}: {'; '.join(probs)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
