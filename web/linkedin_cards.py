#!/usr/bin/env python3
"""링크드인 카드뉴스 자동 생성 — 주간 브리프 JSON → 9장 캐러셀 PDF + 게시 본문(txt).

[성장·배포 2026-09-08] 매주 월요일 발행과 함께 링크드인에 올릴 카드뉴스와 본문을 낸다.
운영 루틴: 배포 후 `/briefs/{pub}/linkedin.pdf` 를 받고 `/briefs/{pub}/linkedin.txt` 를 복사해
작성창에 붙이고 게시 버튼(사람). 완전 자동 게시는 LinkedIn API 승인이 필요해 하지 않는다.

두 층으로 나뉜다.
  · 순수 빌더(결정론·네트워크 0) — `build_deck()`: 브리프 JSON + 용어사전 → 슬라이드 스펙·본문.
    `render_html()`: 스펙 → 단일 HTML(장마다 page-break). 테스트는 이 층만 본다.
  · 렌더러 — 헤드리스 Chrome `--print-to-pdf`. Chrome 이 없으면(로컬·일부 CI) PDF 만 건너뛰고
    html/txt 는 낸다(exit 0 + 경고). 배포 워크플로에서는 비차단 스텝이다.

설계 규율(사용자 피드백 2026-09-07~08 누적):
  · 제목은 2줄 이내·짧게(`title_issue` 를 균형점에서 한 번만 끊는다), 설명은 한 줄.
  · 사실 관계는 카드 JSON 의 key_facts·implication·checks 에서 **그대로** 가져온다(LLM 슬롯 0).
    뉴스 덱이라 공식 공고의 업체명은 출처와 함께 싣는다. `--anon` 이면 가명(업체 A·B·C).
  · 기관·회사 로고 사용 금지(FDA 로고 정책) — 워드마크 칩만.
  · 모든 장 최하단에 AI 생성 고지 한 줄(광고 관례).
  · now()/난수 0 — 같은 입력이면 바이트 동일.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
SITE_BASE_URL = "https://grm-solutions.com"

# ──────────────────────────────────────────────────────────────────────────────
# 텍스트 유틸(결정론)
# ──────────────────────────────────────────────────────────────────────────────

_CJK = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u3000-\u303f\uff00-\uffef\u4e00-\u9fff]")


def text_width(s: str) -> float:
    """대략의 폭(em 단위). 한글 1.0 · 라틴/숫자 0.56 · 공백 0.3 · 문장부호 0.35."""
    w = 0.0
    for ch in s:
        if _CJK.match(ch):
            w += 1.0
        elif ch == " ":
            w += 0.3
        elif ch in ",.·-–—:;()'\"/":
            w += 0.35
        else:
            w += 0.56
    return w


def split_two(text: str, max_width: float = 11.0) -> list[str]:
    """제목을 2줄로. 폭이 max_width 이하면 한 줄. 끊는 자리는 공백·'·'·',' 뒤 중 두 줄 폭이
    가장 균형 잡히는 곳. 끊을 자리가 없으면 한 줄(폰트 크기는 fit_size 가 줄인다)."""
    t = " ".join(text.split())
    if text_width(t) <= max_width:
        return [t]
    cands: list[tuple[float, str, str]] = []
    for i, ch in enumerate(t):
        if ch in " ·,":
            left = t[: i + (1 if ch != " " else 0)].strip()
            right = t[i + 1:].strip()
            if left and right:
                cands.append((abs(text_width(left) - text_width(right)), left, right))
    if not cands:
        return [t]
    cands.sort(key=lambda c: (c[0], c[1]))
    _, left, right = cands[0]
    return [left, right]


def fit_size(lines: list[str], base_px: int, max_px: float = 890.0, min_px: int = 48) -> int:
    """줄 폭이 카드 안폭(max_px)을 넘지 않는 가장 큰 글자 크기(px)."""
    widest = max((text_width(ln) for ln in lines), default=1.0) * 0.96
    size = int(min(base_px, max_px / max(widest, 0.1)))
    return max(min_px, size)


def first_sentence(text: str) -> str:
    """'…다.' 로 끝나는 첫 문장. 없으면 전체."""
    t = " ".join((text or "").split())
    m = re.match(r"^(.*?다\.)(\s|$)", t)
    return m.group(1) if m else t


def parse_fact(s: str) -> tuple[str, str] | None:
    """'라벨: 값' 형태의 key_facts 한 줄 → (라벨, 값). 형태가 아니면 None."""
    m = re.match(r"^\s*([^:：]{1,12})\s*[:：]\s*(.+)$", s or "")
    if not m:
        return None
    label, value = m.group(1).strip(), m.group(2).strip()
    if not value:
        return None
    return label, value


_KO_NUM = {1: "하나", 2: "둘", 3: "셋", 4: "넷", 5: "다섯", 6: "여섯"}


def title_dateform(publish_date: str) -> tuple[int, int, int]:
    """publish_date → (년, 월, 주차). 주차 = (day-1)//7 + 1 (render.title_dateform 과 같은 규칙)."""
    y, m, d = (int(x) for x in publish_date.split("-"))
    return y, m, (d - 1) // 7 + 1


def window_label(window: str) -> str:
    """'2026-08-31 ~ 2026-09-07' → '2026. 8. 31 – 9. 7'. 형식 밖이면 그대로."""
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*~\s*(\d{4})-(\d{2})-(\d{2})\s*$", window or "")
    if not m:
        return window or ""
    y1, m1, d1, y2, m2, d2 = (int(x) for x in m.groups())
    right = f"{m2}. {d2}" if y1 == y2 else f"{y2}. {m2}. {d2}"
    return f"{y1}. {m1}. {d1} – {right}"


# ──────────────────────────────────────────────────────────────────────────────
# 도메인 어휘(결정론 사전)
# ──────────────────────────────────────────────────────────────────────────────

AGENCY_LABEL = {"FDA": "FDA", "MFDS": "식약처", "WHO": "WHO", "EMA": "EMA", "MHRA": "MHRA",
                "Health Canada": "Health Canada", "ECA": "ECA", "ISPE": "ISPE", "PIC/S": "PIC/S",
                "EudraGMDP": "EU GMP", "EU": "EU GMP"}
SOURCE_NOTE = {"FDA": "출처: FDA 공식 공고", "MFDS": "출처: 식약처 공식 공고"}
CLASS1 = re.compile(r"\bClass\s*I\b")

# 경고서한 공통 지적 버킷 — (표시 라벨, 짧은 라벨, 키워드)
WL_THEMES = [
    ("무균공정 · 오염 방지", "무균공정", ("무균", "멸균", "오염방지", "오염 방지", "insanitary", "비위생")),
    ("일탈 · OOS 조사", "일탈", ("일탈", "OOS", "편차", "규격 부적합")),
    ("시험기록 · 원데이터", "시험기록", ("시험기록", "원데이터", "성적서", "데이터 완전성")),
    ("품질부서 책임", "품질부서", ("품질관리부서", "품질부서", "QC")),
    ("세척 · 시설 관리", "세척", ("세척", "건물", "유지관리", "시설관리", "시설 관리")),
]

# 용어별 미니 다이어그램(150x88) — 소개 덱과 같은 결. 없는 용어는 일반 문서 아이콘.
MINI = {
    "endotoxin": """<svg class="mini" viewBox="0 0 150 88"><rect x="22" y="30" width="66" height="28" rx="14" fill="#F4E7DF" stroke="#C2603F" stroke-width="2"/><g stroke="#C2603F" stroke-width="2" stroke-linecap="round"><path d="M36 30v-9M52 30v-10M68 30v-9M82 32v-9M36 58v9M52 58v10M68 58v9M82 56v9"/></g><path d="M96 44h26" stroke="#141413" stroke-width="2" stroke-linecap="round"/><path d="M117 38l6 6-6 6" fill="none" stroke="#141413" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="127" y="48" font-size="12" fill="#BD4B36" font-weight="700">발열</text><text x="22" y="82" font-size="10" fill="#8E8B82">그람음성균 세포벽 · LPS</text></svg>""",
    "aseptic-processing": """<svg class="mini" viewBox="0 0 150 88"><rect x="30" y="6" width="90" height="12" rx="3" fill="#EFE9DE" stroke="#8E8B82" stroke-width="1.5"/><text x="62" y="15" font-size="8" fill="#6C6A64" font-weight="700">HEPA</text><g stroke="#C2603F" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M45 24v22M45 46l-4-5M45 46l4-5M75 24v22M75 46l-4-5M75 46l4-5M105 24v22M105 46l-4-5M105 46l4-5"/></g><rect x="66" y="56" width="18" height="24" rx="3" fill="#fff" stroke="#141413" stroke-width="1.8"/><rect x="69" y="51" width="12" height="5" rx="1" fill="#141413"/><text x="92" y="76" font-size="10" fill="#8E8B82">Grade A</text></svg>""",
    "cross-contamination": """<svg class="mini" viewBox="0 0 150 88"><rect x="14" y="34" width="44" height="40" rx="5" fill="#fff" stroke="#141413" stroke-width="1.8"/><rect x="92" y="34" width="44" height="40" rx="5" fill="#fff" stroke="#141413" stroke-width="1.8"/><g fill="#C2603F"><circle cx="26" cy="48" r="3"/><circle cx="38" cy="60" r="3"/><circle cx="46" cy="46" r="3"/><circle cx="30" cy="66" r="3"/><circle cx="104" cy="60" r="3"/></g><path d="M60 50c10-12 20-12 30 0" fill="none" stroke="#BD4B36" stroke-width="1.8" stroke-dasharray="4 3"/><path d="M86 44l4 6-7 1" fill="none" stroke="#BD4B36" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><text x="24" y="26" font-size="10" fill="#6C6A64">제품 A</text><text x="102" y="26" font-size="10" fill="#6C6A64">제품 B</text></svg>""",
    "oos": """<svg class="mini" viewBox="0 0 150 88"><line x1="10" y1="26" x2="140" y2="26" stroke="#BD4B36" stroke-width="1.8" stroke-dasharray="5 4"/><text x="12" y="20" font-size="10" fill="#BD4B36" font-weight="700">규격 상한</text><polyline points="10,66 28,62 46,65 64,58 82,63 100,18 118,62 140,60" fill="none" stroke="#141413" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="100" cy="18" r="5" fill="#BD4B36"/><line x1="10" y1="76" x2="140" y2="76" stroke="#DCD3C7" stroke-width="1.5"/></svg>""",
    "recall": """<svg class="mini" viewBox="0 0 150 88"><rect x="24" y="52" width="26" height="20" rx="3" fill="#EFE9DE"/><rect x="62" y="36" width="26" height="36" rx="3" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.5"/><rect x="100" y="14" width="26" height="58" rx="3" fill="#C2603F"/><text x="30" y="84" font-size="11" fill="#6C6A64" font-weight="700">III</text><text x="70" y="84" font-size="11" fill="#6C6A64" font-weight="700">II</text><text x="110" y="84" font-size="11" fill="#BD4B36" font-weight="800">I</text><text x="4" y="18" font-size="10" fill="#8E8B82">위해도 ↑</text></svg>""",
    "deviation": """<svg class="mini" viewBox="0 0 150 88"><line x1="10" y1="44" x2="140" y2="44" stroke="#8E8B82" stroke-width="1.6" stroke-dasharray="5 4"/><polyline points="10,44 50,44 74,20 98,44 140,44" fill="none" stroke="#141413" stroke-width="2.2" stroke-linejoin="round"/><circle cx="74" cy="20" r="5" fill="#BD4B36"/><text x="12" y="70" font-size="10" fill="#8E8B82">승인된 지시</text><text x="86" y="16" font-size="10" fill="#BD4B36" font-weight="700">일탈</text></svg>""",
    "_generic": """<svg class="mini" viewBox="0 0 150 88"><path d="M52 14h30l16 16v44H52z" fill="#fff" stroke="#141413" stroke-width="1.8" stroke-linejoin="round"/><path d="M82 14v16h16" fill="none" stroke="#141413" stroke-width="1.8" stroke-linejoin="round"/><path d="M60 44h30M60 54h30M60 64h20" stroke="#C2603F" stroke-width="2" stroke-linecap="round"/></svg>""",
}

_S = 'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"'
ICO = {
    "doc": f'<svg viewBox="0 0 48 48" {_S}><path d="M12 5h16l10 10v28H12z"/><path d="M28 5v10h10M18 26h14M18 33h14"/></svg>',
    "factory": f'<svg viewBox="0 0 48 48" {_S}><path d="M6 41V20l10 6v-6l10 6v-6l10 6V10h6v31z"/><path d="M12 34h4M20 34h4M28 34h4"/></svg>',
    "book": f'<svg viewBox="0 0 48 48" {_S}><path d="M8 8h14a4 4 0 0 1 4 4v28a4 4 0 0 0-4-4H8z"/><path d="M40 8H26a4 4 0 0 0-4 4v28a4 4 0 0 1 4-4h14z"/></svg>',
    "check": f'<svg viewBox="0 0 48 48" {_S}><rect x="8" y="8" width="32" height="32" rx="6"/><path d="M16 24l6 6 11-12"/></svg>',
}

OWL_SVG = ('<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="16" fill="#C2603F"/>'
           '<path d="M14 19 L29 19 L19 6 Z" fill="#FAF6EE"/><path d="M50 19 L35 19 L45 6 Z" fill="#FAF6EE"/>'
           '<circle cx="23" cy="33" r="10" fill="#FAF6EE"/><circle cx="41" cy="33" r="10" fill="#FAF6EE"/>'
           '<circle cx="23" cy="34" r="3.6" fill="#22303F"/><circle cx="41" cy="34" r="3.6" fill="#22303F"/>'
           '<path d="M28 43 L36 43 L32 51 Z" fill="#E8B04A"/></svg>')
OWL_CREAM_SVG = OWL_SVG.replace('fill="#C2603F"/>', 'fill="#FAF9F5"/>', 1).replace("#FAF6EE", "#C2603F")
_HEX = ('<svg xmlns="http://www.w3.org/2000/svg" width="69.28" height="120" viewBox="0 0 69.28 120">'
        '<g fill="none" stroke="#FAF9F5" stroke-opacity="{op}" stroke-width="1.4">'
        '<polygon points="34.64,0 69.28,20 69.28,60 34.64,80 0,60 0,20"/>'
        '<polygon points="0,60 34.64,80 34.64,120 0,140 -34.64,120 -34.64,80"/>'
        '<polygon points="69.28,60 103.92,80 103.92,120 69.28,140 34.64,120 34.64,80"/></g></svg>')


def _data_uri(svg: str) -> str:
    from urllib.parse import quote
    return "data:image/svg+xml;utf8," + quote(svg, safe="")


AI_NOTE = "이미지는 AI 도구로 생성되었습니다"
AI_NOTE_MOCK = "화면은 설명용 예시입니다 · 이미지는 AI 도구로 생성되었습니다"

# ──────────────────────────────────────────────────────────────────────────────
# 빌더
# ──────────────────────────────────────────────────────────────────────────────


def _agency(card: dict) -> str:
    return str(card.get("agency") or "")


def _label(card: dict) -> str:
    return AGENCY_LABEL.get(_agency(card), _agency(card))


def _facts(card: dict) -> list[str]:
    return [str(x) for x in (card.get("key_facts") or []) if x]


def _text_of(card: dict) -> str:
    return " ".join([str(card.get("title_issue") or ""), str(card.get("summary") or ""),
                     " ".join(_facts(card)), str(card.get("implication") or "")])


def is_class1_recall(card: dict) -> bool:
    return str(card.get("group") or "") == "Recall" and any(CLASS1.search(f) for f in _facts(card))


def is_warning_letter(card: dict) -> bool:
    return str(card.get("category") or "") == "Warning Letter"


def is_mfds_inspection(card: dict) -> bool:
    return _agency(card) == "MFDS" and "실사 결과" in str(card.get("summary") or "")


def is_mfds_action(card: dict) -> bool:
    return _agency(card) == "MFDS" and any(k in _text_of(card) for k in ("제조업무정지", "행정처분", "판매업무정지"))


def _kind_chip(card: dict) -> str:
    if is_class1_recall(card):
        return "Class I 회수"
    if str(card.get("group") or "") == "Recall":
        return "회수"
    if is_warning_letter(card):
        return "경고서한"
    if "483" in str(card.get("id") or "") or "483" in str(card.get("type_tag") or ""):
        return "FDA 483"
    if is_mfds_action(card):
        return "제조업무정지" if "제조업무정지" in _text_of(card) else "행정처분"
    if is_mfds_inspection(card):
        return "GMP 실사 결과"
    return str(card.get("signal_label") or card.get("category") or "")


def _firm_short(name: str, limit: float = 12.0) -> str:
    """표 안에 한 줄로 들어오는 짧은 업체 표기 — 법인 표기·쉼표 뒤·괄호 부연을 떼고, 폭 상한 안쪽 어절까지.
    ★'(주)코아팜바이오' 처럼 한국 법인 표기는 **괄호로 시작**하므로 괄호 분리보다 먼저 뗀다(안 그러면 빈 문자열)."""
    n = re.sub(r"\(주\)|㈜|주식회사|유한회사", "", name or "").strip()
    n = re.split(r",|\s\(", n)[0].strip()
    n = re.sub(r"\b(Private|Limited|Ltd\.?|LLC|Inc\.?|Co\.?|Corp\.?|KGaA|AG|LLP|GmbH|S\.?A\.?)\b\.?", "", n)
    n = re.sub(r"\s{2,}", " ", n).strip(" .,&")
    if text_width(n) <= limit:
        return n
    out = ""
    for w in n.split():
        cand = (out + " " + w).strip()
        if text_width(cand) > limit:
            break
        out = cand
    return (out or n[:12]).strip(" .,&")


def _product_tokens(card: dict) -> list[str]:
    """key_facts '제품:' 줄의 라틴 단어(5자 이상) — 헤드라인 문장이 업체 대신 제품명을 부르는 경우의 매칭 키."""
    toks: list[str] = []
    for f in _facts(card):
        kv = parse_fact(f)
        if kv and kv[0].startswith(("제품", "대상")):
            toks += [w for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", kv[1]) if w.lower() not in
                     {"injection", "tablets", "capsules", "bottles", "count", "package", "multiple", "vial", "dose"}]
    return toks


def pick_headline_cards(brief: dict, cards: list[dict], n: int = 3) -> list[dict]:
    """tldr 문장에 등장한 업체(headline_target)의 카드를 순서대로. 부족하면 signal_tier 높은 순."""
    picked: list[dict] = []
    seen: set[str] = set()
    for t in brief.get("tldr") or []:
        for c in cards:
            tgt = str(c.get("headline_target") or "")
            key = str(c.get("id") or tgt)
            if not tgt or key in seen:
                continue
            probes = [p for p in (_firm_short(tgt, 40), *_product_tokens(c)) if len(p) >= 4]
            if any(p in t for p in probes):
                picked.append(c)
                seen.add(key)
                break
        if len(picked) >= n:
            break
    if len(picked) < n:
        rest = sorted((c for c in cards if str(c.get("id") or c.get("headline_target")) not in seen),
                      key=lambda c: (-int(c.get("signal_tier") or 0), int(c.get("render_order") or 0)))
        for c in rest:
            picked.append(c)
            seen.add(str(c.get("id") or c.get("headline_target")))
            if len(picked) >= n:
                break
    return picked[:n]


def theme_counts(cards: list[dict]) -> list[tuple[str, str, int]]:
    """경고서한 카드들의 공통 지적 버킷 카운트(카드 단위) — 많은 순, 동률은 선언 순.
    구조화된 칸(title_issue·key_facts)만 본다 — summary·implication 은 해설이라 주제어가 번진다."""
    out = []
    for order, (label, short, keys) in enumerate(WL_THEMES):
        n = 0
        for c in cards:
            blob = (str(c.get("title_issue") or "") + " " + " ".join(_facts(c))).lower()
            if any(k.lower() in blob for k in keys):
                n += 1
        if n:
            out.append((label, short, n, order))
    out.sort(key=lambda x: (-x[2], x[3]))
    return [(a, b, c) for a, b, c, _ in out]


GLOSSARY_STOP = {"제조", "품질", "시험", "기록", "제품", "관리", "절차", "부적합", "규격", "의약품", "시설", "설비",
                 "포장", "표시", "원료", "공정", "검사", "회사", "문서", "승인", "허가", "출하", "보관", "검체", "시방서",
                 "품질보증", "품질관리", "밸리데이션"}
# 약어가 곧 이름인 용어 중 매주 어디에나 나오는 것 — '이번 주 용어' 후보에서 뺀다
GLOSSARY_STOP_ACR = {"GMP", "QA", "QC", "SOP", "FDA", "API", "CGMP", "MFDS"}


def _term_names(t: dict) -> list[str]:
    """용어의 매칭 키 — 한글은 3자 이상 조각, 라틴은 약어(괄호) 또는 3자 이상 별칭. 일반어(GLOSSARY_STOP)는 뺀다."""
    names: list[str] = []
    for part in re.split(r"[·/]", str(t.get("term_ko") or "")):
        p = part.strip()
        if len(p) >= 3 and p not in GLOSSARY_STOP:
            names.append(p)
    for a in t.get("aliases") or []:
        a = str(a).strip()
        if len(a) >= 3 and a not in GLOSSARY_STOP:
            names.append(a)
    m = re.search(r"\(([A-Z]{2,6})\)", str(t.get("term_en") or ""))
    if m:
        names.append(m.group(1))
    return list(dict.fromkeys(names))


def _contains(text: str, name: str) -> bool:
    if name.isascii():
        return re.search(r"(?<![A-Za-z])" + re.escape(name.lower()) + r"(?![A-Za-z])", text.lower()) is not None
    return name in text


def pick_glossary_terms(glossary: list[dict], cards: list[dict], n: int = 5,
                        headline_cards: list[dict] | None = None) -> list[dict]:
    """이번 주 카드에 실제로 등장한 용어 — **문서 빈도**(등장 카드 수)로 세고 헤드라인 카드는 3배.
    같은 점수면 다이어그램 있는 용어 → 사전 순서. 일반어·2자 용어는 매칭 키에서 뺀다(제조·품질이 늘 1등이 된다)."""
    heads = {str(c.get("id") or c.get("headline_target")) for c in (headline_cards or [])}
    rows = [(str(c.get("id") or c.get("headline_target")),
             str(c.get("title_issue") or "") + " " + " ".join(_facts(c)),   # 구조화 칸(강한 신호)
             _text_of(c)) for c in cards]
    scored: list[tuple[int, int, int, dict]] = []
    for idx, t in enumerate(glossary):
        names = _term_names(t)
        if not names or any(nm in GLOSSARY_STOP_ACR for nm in names):
            continue
        score = 0
        df = 0
        for cid, strong, text in rows:
            hit_strong = any(_contains(strong, nm) for nm in names)
            hit = hit_strong or any(_contains(text, nm) for nm in names)
            if not hit:
                continue
            df += 1
            if cid in heads:
                score += 5 if hit_strong else 3
            else:
                score += 2 if hit_strong else 1
        # 카드 40% 이상에 나오는 말은 '이번 주 용어'가 아니라 늘 나오는 말 — 점수를 깎는다
        if cards and df / len(cards) > 0.4:
            score //= 3
        if score:
            scored.append((-score, 0 if t.get("id") in MINI else 1, idx, t))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [t for _, _, _, t in scored[:n]]


def pick_checks(groups: list[list[dict]], n: int = 6, max_width: float = 34.0) -> list[str]:
    """카드의 '점검' 항목 — 우선순위 그룹(헤드라인 → 경고서한 → 실사) 순서대로, 각 그룹 안에서는
    카드 순서 그대로. 한 줄에 들어오는 것(폭 34)만 먼저 채우고 모자라면 긴 것으로 보충. 중복 제거."""
    seen: set[str] = set()
    fits: list[str] = []
    longs: list[str] = []
    for grp in groups:
        for c in grp:
            for chk in c.get("checks") or []:
                s = " ".join(str(chk).split())
                if not s or s in seen:
                    continue
                seen.add(s)
                (fits if text_width(s) <= max_width else longs).append(s)
    return (fits + longs)[:n]


def anonymize(cards: list[dict]) -> dict[str, str]:
    """업체명 → '업체 A/B/C' 매핑(카드 순서 결정론)."""
    mapping: dict[str, str] = {}
    for c in cards:
        tgt = str(c.get("headline_target") or "")
        if tgt and tgt not in mapping:
            mapping[tgt] = f"업체 {chr(ord('A') + len(mapping) % 26)}"
    return mapping


def build_deck(brief_doc: dict, glossary: list[dict], *, anon: bool = False,
               base_url: str = SITE_BASE_URL) -> dict[str, Any]:
    """브리프 JSON(+용어사전) → {"pub","slides","caption","doc_title"}. 순수·결정론."""
    brief = brief_doc.get("brief") or {}
    cards = sorted(brief_doc.get("cards") or [], key=lambda c: int(c.get("render_order") or 0))
    pub = str(brief.get("publish_date") or "")
    y, m, wk = title_dateform(pub)
    url = f"{base_url}/briefs/{pub}/"

    n_cards = len(cards)
    class1 = [c for c in cards if is_class1_recall(c)]
    wls = [c for c in cards if is_warning_letter(c)]
    mfds_insp = [c for c in cards if is_mfds_inspection(c)]
    mfds_act = [c for c in cards if is_mfds_action(c) and not is_mfds_inspection(c)]
    recalls = [c for c in cards if str(c.get("group") or "") == "Recall"]
    heads = pick_headline_cards(brief, cards, 3)
    # 가명은 화면에 나오는 순서(헤드라인 → 경고서한 → 실사 → 나머지)로 A·B·C
    names = anonymize(heads + wls + mfds_insp + cards) if anon else {}

    def firm(card: dict) -> str:
        tgt = str(card.get("headline_target") or "")
        return names.get(tgt, tgt)

    # ── 01 표지: 통계 타일 4개(카드 수 + 0 이 아닌 축 셋)
    tiles = [(str(n_cards), "규제 신호 카드")]
    for cnt, lab in ((len(class1), "FDA Class I 회수"), (len(wls), "FDA 경고서한"),
                     (len(mfds_insp), "식약처 실사 결과 공개"), (len(mfds_act), "식약처 행정처분"),
                     (len(recalls), "회수 신호")):
        if cnt and len(tiles) < 4 and lab not in {t[1] for t in tiles}:
            tiles.append((str(cnt), lab))
    agencies = [AGENCY_LABEL.get(a, a) for a in (brief.get("agencies") or [])]
    slides: list[dict] = [dict(
        kind="cover", eyebrow=f"주간 규제 소식 · {m}월 {wk}주차", h1=["이번 주", "규제 소식"],
        tiles=tiles, sub=" · ".join(agencies), ft_right=window_label(str(brief.get("window") or "")),
        ai=AI_NOTE)]

    # ── 02~04 헤드라인 카드
    for c in heads:
        rows: list[tuple[str, str]] = []
        for f in _facts(c):
            kv = parse_fact(f)
            if not kv:
                continue
            label, value = kv
            if label.startswith("발행"):
                continue
            rows.append((label, value))
            if len(rows) >= 3:
                break
        rows.append(("업체", firm(c) or "—"))
        impl = first_sentence(str(c.get("implication") or ""))
        checks = [" ".join(str(x).split()) for x in (c.get("checks") or [])][:2]
        slides.append(dict(
            kind="headline", chips=[_label(c), _kind_chip(c)],
            h1=split_two(str(c.get("title_issue") or c.get("headline_target") or "")),
            rows=rows, impl=impl, checks=checks,
            ft_right=SOURCE_NOTE.get(_agency(c), f"출처: {_label(c)} 공식 발표"), ai=AI_NOTE))

    # ── 05 경고서한 묶음(2건 이상일 때)
    themes = theme_counts(wls)
    if len(wls) >= 2 and themes:
        top = themes[0]
        rows_wl = []
        for c in wls[:6]:
            chips = [p.strip() for p in re.split(r"[·,]", str(c.get("title_issue") or "")) if p.strip()][:2]
            rows_wl.append((_firm_short(firm(c), 10.0), chips))
        slides.append(dict(
            kind="themes", eyebrow="FDA 경고서한", icon="doc",
            h1=[f"경고서한 {len(wls)}건,", f"공통점은 {top[1]}"],
            bars=[(lab, cnt) for lab, _, cnt in themes[:5]], bars_total=f"{len(wls)}건 중",
            rows=rows_wl, ft_right=SOURCE_NOTE["FDA"], ai=AI_NOTE))

    # ── 06 식약처 실사 결과(2곳 이상일 때)
    if len(mfds_insp) >= 2:
        rows_mf = []
        for c in mfds_insp[:6]:
            chips = [p.strip() for p in re.split(r"[·,]", str(c.get("title_issue") or "")) if p.strip()][:3]
            rows_mf.append((_firm_short(firm(c), 10.0), chips))
        impl = first_sentence(str(mfds_insp[0].get("implication") or ""))
        slides.append(dict(
            kind="rows", eyebrow="식약처 사후 GMP 실사", icon="factory",
            h1=[f"실사 결과 {len(mfds_insp)}곳,", "보완 분야는"], rows=rows_mf, impl=impl,
            ft_right=SOURCE_NOTE["MFDS"], ai=AI_NOTE))

    # ── 07 이번 주 용어
    terms = pick_glossary_terms(glossary, cards, 5, headline_cards=heads)
    if terms:
        gl = []
        for t in terms:
            ko_parts = [p.strip() for p in re.split(r"[·/]", str(t.get("term_ko") or "")) if p.strip()]
            # 병기('무균공정·무균조작')는 이번 주 본문에 더 많이 나온 조각으로, 없으면 첫 조각
            blob = " ".join(_text_of(c) for c in cards)
            ko = max(ko_parts, key=lambda p: (blob.count(p), -ko_parts.index(p))) if ko_parts else str(t.get("term_ko") or "")
            en = str(t.get("term_en") or "")
            acr = re.search(r"\(([A-Z]{2,6})\)", en)
            small = acr.group(1) if acr else re.sub(r"\s*\(.*?\)\s*", " ", en).strip()
            gl.append(dict(ko=ko, small=small, lines=[" ".join(str(t.get("easy_ko") or "").split())],
                           mini=MINI.get(str(t.get("id") or ""), MINI["_generic"])))
        slides.append(dict(kind="glossary", eyebrow="이번 주 용어", icon="book",
                           h1=[f"이번 주 소식에 나온 용어 {_KO_NUM.get(len(gl), len(gl))}"],
                           terms=gl, cap="정의는 GRM 용어사전에서 그대로 가져왔습니다.", ai=AI_NOTE))

    # ── 08 점검 포인트
    checks = pick_checks([heads, wls, mfds_insp], 6)
    if checks:
        slides.append(dict(kind="checks", eyebrow="이번 주 점검 포인트", icon="check",
                           h1=["우리 현장에서 확인할 것"], checks=checks,
                           cap="각 항목은 이번 주 카드의 '점검' 칸에서 가져왔습니다.", ai=AI_NOTE))

    # ── 09 마무리
    slides.append(dict(kind="closing", h1=[f"전체 {n_cards}건,", "원문 링크와 함께"],
                       body="요약은 한국어로, 출처는 각 기관의 공식 공고입니다.",
                       url=url.replace("https://", "").rstrip("/"),
                       note="메일로 받아보고 싶다면, 사이트에서 뉴스레터를 구독하세요.",
                       ft_right="매주 월요일 · 무료", ai=AI_NOTE))

    # 페이지 번호
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)

    # ── 본문(한 줄에 한 뜻·모바일 폭 안쪽)
    lines = [f"이번 주 규제 소식, 카드 {len(slides)}장.", ""]
    for c in heads[:2]:
        lines.append(f"{' '.join(str(c.get('title_issue') or '').split())}.")
    lines.append("")
    if class1:
        lines.append(f"· Class I 회수 {len(class1)}건")
    if len(wls) >= 2 and themes:
        lines.append(f"· 경고서한 {len(wls)}건 — " + "·".join(s for _, s, _ in themes[:3]))
    mf_bits = []
    if mfds_act:
        mf_bits.append(f"행정처분 {len(mfds_act)}건")
    if mfds_insp:
        mf_bits.append(f"실사 결과 {len(mfds_insp)}곳")
    if mf_bits:
        lines.append("· 식약처 — " + ", ".join(mf_bits))
    tail = []
    if terms:
        tail.append(f"용어 {len(terms)}개")
    if checks:
        tail.append(f"점검 포인트 {len(checks)}개")
    if tail:
        lines.append("· " + " · ".join(tail))
    lines += ["", f"전체 {n_cards}건과 원문 링크", url, "",
              "어떤 항목이 제일 신경 쓰이시나요?", "댓글로 남겨 주시면 다음 주에 다룹니다.", "",
              "#GMP #제약 #바이오 #규제 #품질관리", "#QA #FDA #식약처 #경고서한 #제약바이오"]
    caption = "\n".join(lines) + "\n"
    return {"pub": pub, "slides": slides, "caption": caption,
            "doc_title": f"{m}월 {wk}주차 규제 소식", "url": url}


# ──────────────────────────────────────────────────────────────────────────────
# HTML
# ──────────────────────────────────────────────────────────────────────────────

CSS = """
@page{size:1080px 1350px;margin:0}
*{box-sizing:border-box}
html,body{margin:0;background:#FAF9F5;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.slide{width:1080px;height:1350px;padding:84px 88px 52px;display:flex;flex-direction:column;position:relative;overflow:hidden;
  background:#FAF9F5;color:#141413;font-family:'Pretendard',-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased;
  page-break-after:always;break-after:page}
.slide:last-child{page-break-after:auto;break-after:auto}
.slide.dark{background:#1A1815 url(HEX_DARK) 0 0/69.28px 120px;color:#FAF9F5}
.slide.coral{background:#C2603F url(HEX_CREAM) 0 0/69.28px 120px;color:#FAF9F5}
.hd{display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:18px}
.brand svg{width:60px;height:60px;border-radius:14px;display:block}
.brand .w{display:flex;flex-direction:column;line-height:1.05}
.brand .w b{font-family:'Noto Serif KR',Georgia,serif;font-weight:600;font-size:36px;letter-spacing:-.005em}
.brand .w span{font-size:17px;color:#6C6A64;margin-top:6px}
.dark .brand .w span{color:#A8A299}.coral .brand .w span{color:rgba(250,249,245,.82)}
.pg{font-size:23px;color:#6C6A64;font-variant-numeric:tabular-nums;letter-spacing:.02em}
.dark .pg{color:#A8A299}.coral .pg{color:rgba(250,249,245,.82)}
.main{flex:1;display:flex;flex-direction:column;justify-content:center;padding-bottom:36px}
.eyebrow{display:flex;align-items:center;gap:16px;color:#C2603F;font-weight:700;font-size:26px;margin-bottom:36px}
.eyebrow::before{content:'';width:34px;height:4px;background:#C2603F;border-radius:2px}
.coral .eyebrow{color:#FAF9F5}.coral .eyebrow::before{background:#FAF9F5}
.eyebrow .ei{display:inline-flex;width:34px;height:34px;color:#C2603F}.eyebrow .ei svg{width:34px;height:34px}
h1{margin:0;line-height:1.16;letter-spacing:-.028em;font-weight:800;word-break:keep-all;white-space:nowrap}
.sub{margin:44px 0 0;font-size:34px;line-height:1.5;color:rgba(250,249,245,.88);word-break:keep-all}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:44px}
.stat{background:rgba(250,249,245,.12);border:1.5px solid rgba(250,249,245,.28);border-radius:18px;padding:20px 18px 16px}
.stat b{display:block;font-size:54px;line-height:1;letter-spacing:-.03em;color:#FAF9F5}
.stat span{display:block;margin-top:10px;font-size:19px;color:rgba(250,249,245,.85);word-break:keep-all;line-height:1.3}
.chips{display:flex;gap:12px;margin-bottom:40px}
.chip{display:inline-flex;align-items:center;padding:10px 24px;border-radius:9999px;font-size:25px;font-weight:700;background:#F4E7DF;color:#A14B30}
.chip.hi{background:#BD4B36;color:#FAF9F5}
.mock{background:#FFFDF9;border:1.5px solid #E6DFD8;border-radius:22px;box-shadow:0 26px 60px -34px rgba(26,24,21,.45);padding:30px 36px;margin-top:44px}
.mock.small{margin-top:20px;padding:24px 36px}
.mock .mh{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.mock .mh b{font-size:27px;color:#141413}.mock .mh span{font-size:21px;color:#6C6A64}
.kv{display:grid;grid-template-columns:minmax(72px,max-content) 1fr;gap:10px 18px;font-size:23px;line-height:1.45;color:#252523;margin:0}
.kv dt{color:#6C6A64;font-weight:600;white-space:nowrap;max-width:190px;overflow:hidden;text-overflow:ellipsis}
.kv dd{margin:0;word-break:keep-all;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.impl{margin-top:22px;padding:16px 20px;border-left:4px solid #C2603F;background:#FBF3EE;border-radius:0 12px 12px 0;font-size:23px;line-height:1.5;color:#252523;word-break:keep-all}
.impl b{color:#A14B30;margin-right:8px}
.cks{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.cks span{font-size:19px;padding:6px 13px;border-radius:9999px;border:1.5px solid #DCD3C7;color:#3D3D3A;word-break:keep-all}
.bars{display:flex;flex-direction:column;gap:14px;margin-top:6px}
.bars .br{display:grid;grid-template-columns:200px 1fr 54px;align-items:center;gap:16px;font-size:22px;color:#252523}
.bars .br .bar{height:26px;border-radius:7px;background:#F4E7DF;position:relative;overflow:hidden}
.bars .br .bar i{position:absolute;left:0;top:0;bottom:0;background:#C2603F;border-radius:7px}
.bars .br .bar.top i{background:#A14B30}
.bars .br .v{text-align:right;font-weight:700;color:#141413;font-variant-numeric:tabular-nums}
.frow{display:flex;align-items:center;gap:14px;padding:13px 0;border-top:1.5px solid #EFE9DE;font-size:22px;color:#252523}
.frow:first-child{border-top:0}
.frow b{flex:none;width:230px;font-weight:600;color:#141413;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.frow .fl{display:flex;flex-wrap:wrap;gap:6px}
.frow .fl span{font-size:18px;padding:4px 11px;border-radius:9999px;background:#F4E7DF;color:#A14B30}
.frow .fl span.g{background:#EFE9DE;color:#3D3D3A}
.gl{display:flex;flex-direction:column;gap:14px;margin-top:40px}
.gl .g{display:grid;grid-template-columns:200px 1fr 150px;gap:16px;align-items:center;padding:12px 0;border-top:1.5px solid #E6DFD8}
.gl .g:first-child{border-top:0}
.gl .g b{font-size:26px;color:#141413;letter-spacing:-.01em}
.gl .g b small{display:block;font-size:17px;color:#8E8B82;font-weight:500;margin-top:4px}
.gl .g p{margin:0;font-size:21px;line-height:1.45;color:#3D3D3A;word-break:keep-all;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.gl .g .mini{width:150px;height:88px;display:block}
.mini text{font-family:'Pretendard',sans-serif}
.check{display:flex;flex-direction:column;gap:9px;margin-top:6px}
.check div{display:flex;align-items:center;gap:14px;font-size:24px;color:#252523;padding:4px 0;word-break:keep-all}
.check div::before{content:'';flex:none;width:22px;height:22px;border:2px solid #C2603F;border-radius:6px}
.cap{margin:40px 0 0;font-size:28px;line-height:1.55;color:#3D3D3A;word-break:keep-all}
p.body{margin:46px 0 0;font-size:34px;line-height:1.62;color:#D6D0C6;word-break:keep-all;max-width:880px}
.url-sm{margin:44px 0 0;font-size:34px;font-weight:700;color:#E3936F;word-break:break-all}
.note{margin:44px 0 0;font-size:28px;line-height:1.55;color:#A8A299;word-break:keep-all}
.owl-lg{width:128px;height:128px;display:block;margin-bottom:52px}
.owl-lg svg{width:128px;height:128px;border-radius:30px}
.ft{display:flex;justify-content:space-between;align-items:center;padding-top:28px;border-top:1.5px solid #E6DFD8;font-size:24px;color:#6C6A64}
.dark .ft{border-color:rgba(255,255,255,.14);color:#A8A299}.coral .ft{border-color:rgba(250,249,245,.3);color:rgba(250,249,245,.88)}
.ai{margin-top:12px;font-size:15px;color:#A8A299;letter-spacing:.01em}
.dark .ai{color:rgba(250,249,245,.42)}.coral .ai{color:rgba(250,249,245,.62)}
"""

FONT_LINKS = ('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard.css">'
              '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@600&display=swap">')


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _h1(lines: list[str], base: int) -> str:
    size = fit_size(lines, base)
    return f'<h1 style="font-size:{size}px">' + "<br>".join(_e(l) for l in lines) + "</h1>"


def _eyebrow(s: dict) -> str:
    ico = f'<span class="ei">{ICO[s["icon"]]}</span>' if s.get("icon") in ICO else ""
    return f'<div class="eyebrow">{ico}{_e(s["eyebrow"])}</div>' if s.get("eyebrow") else ""


def _slide_html(s: dict) -> str:
    kind = s["kind"]
    cls = {"cover": " coral", "closing": " dark"}.get(kind, "")
    parts: list[str] = []
    if kind == "cover":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 96))
        parts.append('<div class="stats">' + "".join(
            f'<div class="stat"><b>{_e(n)}</b><span>{_e(l)}</span></div>' for n, l in s["tiles"]) + "</div>")
        if s.get("sub"):
            parts.append(f'<p class="sub">{_e(s["sub"])}</p>')
    elif kind == "headline":
        parts.append('<div class="chips">' + "".join(
            f'<span class="chip{" hi" if i else ""}">{_e(c)}</span>' for i, c in enumerate(s["chips"]) if c) + "</div>")
        parts.append(_h1(s["h1"], 78))
        kv = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in s["rows"])
        impl = f'<div class="impl"><b>시사점</b>{_e(s["impl"])}</div>' if s.get("impl") else ""
        cks = ('<div class="cks">' + "".join(f"<span>{_e(c)}</span>" for c in s["checks"]) + "</div>") if s.get("checks") else ""
        parts.append(f'<div class="mock"><dl class="kv">{kv}</dl>{impl}{cks}</div>')
    elif kind == "themes":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 78))
        mx = max((c for _, c in s["bars"]), default=1)
        bars = "".join(
            f'<div class="br"><span>{_e(l)}</span><span class="bar{" top" if i == 0 else ""}"><i style="width:{int(100 * c / mx)}%"></i></span><span class="v">{c}</span></div>'
            for i, (l, c) in enumerate(s["bars"]))
        parts.append(f'<div class="mock"><div class="mh"><b>겹치는 지적</b><span>{_e(s["bars_total"])}</span></div><div class="bars">{bars}</div></div>')
        rows = "".join(f'<div class="frow"><b>{_e(f)}</b><span class="fl">' + "".join(
            f'<span{"" if j == 0 else " class=\"g\""}>{_e(ch)}</span>' for j, ch in enumerate(chips)) + "</span></div>"
            for f, chips in s["rows"])
        parts.append(f'<div class="mock small">{rows}</div>')
    elif kind == "rows":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 78))
        rows = "".join(f'<div class="frow"><b>{_e(f)}</b><span class="fl">' + "".join(
            f"<span>{_e(ch)}</span>" for ch in chips) + "</span></div>" for f, chips in s["rows"])
        parts.append(f'<div class="mock">{rows}</div>')
        if s.get("impl"):
            parts.append(f'<div class="impl"><b>시사점</b>{_e(s["impl"])}</div>')
    elif kind == "glossary":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 64))
        rows = "".join(
            f'<div class="g"><b>{_e(t["ko"])}<small>{_e(t["small"])}</small></b><p>{"<br>".join(_e(l) for l in t["lines"])}</p>{t["mini"]}</div>'
            for t in s["terms"])
        parts.append(f'<div class="gl">{rows}</div>')
        parts.append(f'<p class="cap">{_e(s["cap"])}</p>')
    elif kind == "checks":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 72))
        items = "".join(f"<div>{_e(c)}</div>" for c in s["checks"])
        parts.append(f'<div class="mock"><div class="check">{items}</div></div>')
        parts.append(f'<p class="cap">{_e(s["cap"])}</p>')
    elif kind == "closing":
        parts.append(f'<div class="owl-lg">{OWL_SVG}</div>')
        parts.append(_h1(s["h1"], 84))
        parts.append(f'<p class="body">{_e(s["body"])}</p>')
        parts.append(f'<div class="url-sm">{_e(s["url"])}</div>')
        parts.append(f'<p class="note">{_e(s["note"])}</p>')
    brand_icon = OWL_CREAM_SVG if kind == "cover" else OWL_SVG
    ai = f'<div class="ai">{_e(s["ai"])}</div>' if s.get("ai") else ""
    return (f'<section class="slide{cls}"><div class="hd"><div class="brand">{brand_icon}'
            f'<span class="w"><b>GRM</b><span>Global Regulatory Monitor</span></span></div>'
            f'<div class="pg">{s["idx"]:02d} / {s["total"]:02d}</div></div>'
            f'<div class="main">{"".join(parts)}</div>'
            f'<div class="ft"><span>grm-solutions.com</span><span>{_e(s.get("ft_right", ""))}</span></div>{ai}</section>')


def render_html(deck: dict, *, font_links: bool = True) -> str:
    css = CSS.replace("HEX_DARK", _data_uri(_HEX.format(op="0.07"))).replace("HEX_CREAM", _data_uri(_HEX.format(op="0.16")))
    body = "".join(_slide_html(s) for s in deck["slides"])
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{_e(deck["doc_title"])}</title>'
            f'{FONT_LINKS if font_links else ""}<style>{css}</style></head><body>{body}</body></html>')


# ──────────────────────────────────────────────────────────────────────────────
# 렌더러(Chrome)
# ──────────────────────────────────────────────────────────────────────────────

CHROME_CANDIDATES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
                     r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                     r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def find_chrome() -> str | None:
    env = os.environ.get("GRM_CHROME") or os.environ.get("CHROME_BIN")
    if env and (shutil.which(env) or Path(env).exists()):
        return env
    for c in CHROME_CANDIDATES:
        if shutil.which(c):
            return shutil.which(c)
        if Path(c).exists():
            return c
    return None


def render_pdf(html_path: Path, pdf_path: Path, chrome: str, timeout: int = 120) -> None:
    """헤드리스 Chrome 으로 PDF. 폰트가 CDN 이라 virtual-time-budget 으로 로드를 기다린다."""
    profile = Path(tempfile.mkdtemp(prefix="grm-linkedin-chrome-"))
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", f"--user-data-dir={profile}", "--no-pdf-header-footer",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=15000",
           # ★절대경로 — Chrome 은 상대경로를 자기 CWD 기준으로 풀어 엉뚱한 곳에 쓴다(실측: 파일 없음 → RuntimeError)
           f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()]
    subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    shutil.rmtree(profile, ignore_errors=True)
    if not pdf_path.exists() or pdf_path.stat().st_size < 1000:
        raise RuntimeError(f"PDF 생성 실패: {pdf_path}")


def latest_brief_path(data_dir: Path) -> Path | None:
    files = sorted(data_dir.glob("brief_web_*.json"))
    return files[-1] if files else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="링크드인 카드뉴스(PDF+본문) 생성")
    ap.add_argument("--data", default=str(WEB_DIR / "data" / "briefs"), help="브리프 JSON 디렉터리")
    ap.add_argument("--brief", help="특정 브리프 JSON 파일(미지정 시 최신)")
    ap.add_argument("--glossary", default=str(WEB_DIR / "data" / "glossary.json"))
    ap.add_argument("--out", required=True, help="출력 루트(dist) — briefs/{pub}/linkedin.{pdf,txt} 를 낸다")
    ap.add_argument("--anon", action="store_true", help="업체명을 가명(업체 A/B/C)으로")
    ap.add_argument("--html", action="store_true", help="linkedin.html 도 출력에 남긴다")
    ap.add_argument("--no-pdf", action="store_true", help="Chrome 렌더를 건너뛴다(html/txt 만)")
    args = ap.parse_args(argv)

    brief_path = Path(args.brief) if args.brief else latest_brief_path(Path(args.data))
    if not brief_path or not brief_path.exists():
        print("::warning::브리프 JSON 없음 — 카드 생성 건너뜀", file=sys.stderr)
        return 0
    brief_doc = json.loads(brief_path.read_text(encoding="utf-8"))
    glossary = json.loads(Path(args.glossary).read_text(encoding="utf-8")) if Path(args.glossary).exists() else []
    deck = build_deck(brief_doc, glossary, anon=args.anon)
    out_dir = Path(args.out) / "briefs" / deck["pub"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "linkedin.txt").write_text(deck["caption"], encoding="utf-8")
    html_text = render_html(deck)
    html_path = out_dir / "linkedin.html"
    html_path.write_text(html_text, encoding="utf-8")
    print(f"linkedin: {deck['pub']} · {len(deck['slides'])}장 · 본문 {len(deck['caption'])}자 → {out_dir}")
    if args.no_pdf:
        return 0
    chrome = find_chrome()
    if not chrome:
        print("::warning::Chrome 미발견 — linkedin.pdf 건너뜀(html/txt 만 출력)", file=sys.stderr)
        if not args.html:
            html_path.unlink(missing_ok=True)
        return 0
    try:
        render_pdf(html_path, out_dir / "linkedin.pdf", chrome)
        print(f"linkedin.pdf {(out_dir / 'linkedin.pdf').stat().st_size:,} bytes")
    except Exception as exc:  # 비차단 — 배포는 카드 없이도 진행한다
        print(f"::warning::linkedin.pdf 렌더 실패({type(exc).__name__}) — html/txt 만 출력", file=sys.stderr)
    finally:
        if not args.html:
            html_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
