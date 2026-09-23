#!/usr/bin/env python3
"""링크드인 카드뉴스 자동 생성 — 주간 브리프 JSON → 캐러셀 PDF + 게시 본문(txt), 국문·영문 두 벌.

[성장·배포 2026-09-08] 매주 월요일 발행과 함께 링크드인에 올릴 카드뉴스와 본문을 낸다.
운영 루틴: 배포 후 `/briefs/{pub}/linkedin.pdf` 를 받고 `/briefs/{pub}/linkedin.txt` 를 복사해
작성창에 붙이고 게시 버튼(사람). 완전 자동 게시는 LinkedIn API 승인이 필요해 하지 않는다.

[영문판 2026-09-15] 같은 주 소식을 영어로도 낸다(`linkedin_en.pdf`·`linkedin_en.txt`, `--lang`).
  · **무엇을 실을지는 한국어 정본으로 고른다** — 헤드라인 카드·용어·점검·경고서한 주제 집계는
    한국어 본문으로 판별하고, 언어에 따라 갈리는 것은 화면에 나가는 글자뿐이다. 두 덱이 서로
    다른 소식을 말하지 않게 하려는 것이다.
  · 사실은 카드 JSON 의 `en` 블록과 용어사전 `*_en` 에서 **그대로** 가져온다(LLM 슬롯 0).
    영문 블록이 없는 카드는 싣지 않고, 소식 장이 하나도 없으면 그 언어를 경고 후 건너뛴다.
  · ★한국 업체명을 로마자로 지어내지 않는다 — 이름을 못 쓰면 그 줄을 빼고 건수로만 남긴다
    (식약처 실사 묶음 장이 영문 덱에서 빠지는 이유다).

[마케팅 2026-09-23 계획 L-02] 게시 본문(캡션)은 두 형식 — `--caption one`(기본, "이번 주 한
건": 헤드라인 카드 1건의 사실·시사점·점검 2개만) · `--caption summary`(종전 전체 요약형). 슬라이드
는 두 형식이 동일하고, 파일 이름(`linkedin.txt` 등)도 그대로다 — 월요일 태스크가 이 이름에
의존한다. 본문 안 URL 에만 UTM 을 붙인다(`deck["caption_url"]`, `web/utm.py`) — 슬라이드 URL
(`deck["url"]`)은 깨끗하게 둔다.

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
from collections import Counter
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
SITE_BASE_URL = "https://grm-solutions.com"

# [마케팅 2026-09-23] 게시 본문 URL 에만 UTM 을 붙인다 — 슬라이드 안의 URL(`deck["url"]`)은
# 깨끗하게 두고 캡션 전용 `deck["caption_url"]`만 태그를 단다. 이 파일은 보통 스크립트로 직접
# 실행돼(`python web/linkedin_cards.py`) sys.path[0] 이 이미 web/ 디렉터리지만, render.py 와
# 같은 방어적 삽입을 둬 다른 실행 컨텍스트(테스트 등)에서도 같은 디렉터리의 utm.py 를 찾는다.
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))
from utm import LINKEDIN_WEEKLY, linkedin_weekly_campaign, with_utm  # noqa: E402
# [마케팅 2026-09-23 L-06] 월간 결산 덱의 마무리 장은 아카이브 페이지로 링크한다. 영문
# 아카이브(`/en/archive/`)는 **영문으로 낼 수 있는 호가 하나라도 있을 때만** 존재한다
# (`render.py` `brief_has_english()` + `en_paths.add("archive/")`, 4880줄 부근) — 그 계약을
# 다시 베끼지 않고 함수를 그대로 재사용한다(정본 하나). render.py 최상위는 상수·함수 정의뿐이라
# import 부작용 0(자신도 grm_findings·grm_i18n 을 같은 방식으로 재사용 — 010 계열 검증됨).
import render  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# 텍스트 유틸(결정론)
# ──────────────────────────────────────────────────────────────────────────────

_CJK = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u3000-\u303f\uff00-\uffef\u4e00-\u9fff]")


def nfmt(tmpl: str, n: int, **kw) -> str:
    """`{n}` 과 복수 표지 `{s}` 를 함께 채운다.

    영문은 수에 따라 명사가 변한다 — 문구표에 `s` 를 박아 두면 1건일 때 "1 inspection
    results" 가 나간다(2026-09-21 링크드인 영문 캡션에서 실제로 그랬다). 한국어 문구표는
    `{s}` 를 쓰지 않으므로 같은 호출로 안전하다(str.format 은 안 쓰는 인자를 무시한다).
    """
    return tmpl.format(n=n, s="" if n == 1 else "s", **kw)


def plural_s(n: int) -> str:
    """`nfmt` 와 같은 규칙(1건은 무표지)의 단독 판정 — [마케팅 2026-09-23 L-06] 월간 덱의
    본문 한 줄에 카드 수·호 수 **두 개**가 함께 들어가면(`{c} card{cs} · {k} issue{ks}`)
    `nfmt` 는 `{n}`/`{s}` 자리가 하나뿐이라 못 쓴다 — 그 자리에 이 함수로 직접 채운다."""
    return "" if n == 1 else "s"


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


def _wrap_cuts(t: str, max_width: float) -> list[tuple[int, float]]:
    """`t` 안에서 끊을 수 있는 모든 자리 — (그 자리까지 문자열의 컷 오프셋, 그 왼쪽 부분의 폭).
    자리는 공백·'·'·',' **뒤**뿐(단어 중간 금지). 끝(`len(t)`)도 하나의 '컷'으로 넣는다."""
    cuts: list[tuple[int, float]] = []
    for i, ch in enumerate(t):
        if ch in " ·,":
            cut = i if ch == " " else i + 1
            left = t[:cut].rstrip()
            if left:
                cuts.append((cut, text_width(left)))
    cuts.append((len(t), text_width(t)))
    return cuts


def _greedy_wrap(t: str, cuts: list[tuple[int, float]], max_width: float) -> list[int] | None:
    """줄마다 상한 안에서 최대한 채우는 그리디 컷 — 임의의 최대폭 줄바꿈 문제에서 **줄 수를
    최소화하는** 고전적 방법이다(균형 배분의 목표 줄 수 N 을 여기서 얻는다). 한 조각이 폭을
    넘겨 못 끊으면 None."""
    chosen: list[int] = []
    pos_off, pos_w = 0, 0.0
    i, n = 0, len(cuts)
    while pos_off < len(t):
        best = None
        while i < n and cuts[i][1] - pos_w <= max_width:
            best = cuts[i]
            i += 1
        if best is None:
            return None
        chosen.append(best[0])
        pos_off, pos_w = best
    return chosen


def wrap_width(text: str, max_width: float = 26.0) -> list[str]:
    """`text` 를 폭 max_width(text_width 단위) 안에 들어오는 여러 줄로 **균형 있게** 감싼다
    (`split_two` 의 다줄 버전, [마케팅 2026-09-23 개정] '한눈에 한 줄' 피드백 3연속 반려 — 앞줄을
    상한까지 욱여넣고 뒷줄에 짧은 나머지를 남기는 그리디 줄바꿈이 "문장을 잘못 끊는다"는 지적의
    실제 원인이었다). 끊는 자리는 공백·'·'·',' 뒤뿐이라 단어 중간을 자르지 않고, 어떤 글자도
    지어내거나 버리지 않는다.

    방법: ①그리디로 최소 줄 수 N 을 구한다(줄 수 자체는 이 값을 넘기지 않는다) ②경계마다
    "전체 폭 × k/N"에 가장 가까운 컷을 고른다(상한은 계속 지킨다) — 앞줄이 다 채우고 마지막
    줄만 짧게 남는 대신, N 줄이 고르게 나뉜다. 균형 배분이 어느 경계에서 막히면(드묾) 그리디로
    물러선다 — 상한을 넘기는 일은 없다. 끊을 자리가 아예 없는 한 덩어리(예: 긴 라틴 단어)는
    그 줄만 상한을 넘긴 채로 낸다 — 없는 공백을 만들 수는 없다."""
    t = " ".join(text.split())
    if not t:
        return []
    total_w = text_width(t)
    if total_w <= max_width:
        return [t]

    cuts = _wrap_cuts(t, max_width)
    greedy = _greedy_wrap(t, cuts, max_width)
    if greedy is None:
        return [t]                          # 끊을 자리가 없다 — 한 덩어리가 폭을 넘는다
    n_lines = len(greedy)
    if n_lines <= 1:
        return [t]

    offsets = greedy
    pos_off, pos_w = 0, 0.0
    boundaries: list[int] = []
    for k in range(1, n_lines):
        target = total_w * k / n_lines
        window = [c for c in cuts if c[0] > pos_off and c[1] - pos_w <= max_width]
        if not window:
            boundaries = []
            break
        chosen = min(window, key=lambda c: abs(c[1] - target))
        boundaries.append(chosen[0])
        pos_off, pos_w = chosen
    if boundaries and total_w - pos_w <= max_width:
        offsets = boundaries + [len(t)]

    lines: list[str] = []
    prev = 0
    for cut in offsets:
        seg = t[prev:cut].strip()
        if seg:
            lines.append(seg)
        prev = cut
    return lines


def fit_size(lines: list[str], base_px: int, max_px: float = 890.0, min_px: int = 48) -> int:
    """줄 폭이 카드 안폭(max_px)을 넘지 않는 가장 큰 글자 크기(px)."""
    widest = max((text_width(ln) for ln in lines), default=1.0) * 0.96
    size = int(min(base_px, max_px / max(widest, 0.1)))
    return max(min_px, size)


def first_sentence(text: str, lang: str = "ko") -> str:
    """첫 문장. 한국어는 '…다.', 영어는 마침표 뒤 공백+대문자로 끊는다(숫자·조항 표기의
    마침표 '211.84(d)' 는 대문자가 뒤따르지 않아 걸리지 않는다). 못 끊으면 전체."""
    t = " ".join((text or "").split())
    if lang == "ko":
        m = re.match(r"^(.*?다\.)(\s|$)", t)
    else:
        m = re.match(r"^(.*?[a-z0-9)\]”\"]\.)\s+(?=[A-Z])", t)
    return m.group(1) if m else t


def parse_fact(s: str, max_label: int = 12) -> tuple[str, str] | None:
    """'라벨: 값' 형태의 key_facts 한 줄 → (라벨, 값). 형태가 아니면 None.
    ★라벨 길이 상한은 언어마다 다르다 — 한국어 '관찰사항 1'(7자)과 달리 영어는
    'Observation 1'(13자)이라 12자 상한에 걸려 **표가 통째로 비었다**(2026-09-15)."""
    m = re.match(r"^\s*([^:：]{1,%d})\s*[:：]\s*(.+)$" % int(max_label), s or "")
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
AGENCY_LABEL_EN = dict(AGENCY_LABEL, MFDS="MFDS")
SOURCE_NOTE = {"FDA": "출처: FDA 공식 공고", "MFDS": "출처: 식약처 공식 공고"}
CLASS1 = re.compile(r"\bClass\s*I\b")

# 경고서한 공통 지적 버킷 — (표시 라벨, 짧은 라벨, 키워드, 영문 표시 라벨, 영문 짧은 라벨)
# ★키워드는 **한국어 정본 본문**에 대고 센다 — 영문 덱도 같은 집계를 쓴다(두 덱이 같은 주제를
#   말해야 한다). 언어에 따라 갈리는 건 표시 라벨뿐이다.
WL_THEMES = [
    ("무균공정 · 오염 방지", "무균공정", ("무균", "멸균", "오염방지", "오염 방지", "insanitary", "비위생"),
     "Aseptic processing · contamination", "aseptic processing"),
    ("일탈 · OOS 조사", "일탈", ("일탈", "OOS", "편차", "규격 부적합"),
     "Deviations · OOS investigations", "deviations"),
    ("시험기록 · 원데이터", "시험기록", ("시험기록", "원데이터", "성적서", "데이터 완전성"),
     "Test records · raw data", "test records"),
    ("품질부서 책임", "품질부서", ("품질관리부서", "품질부서", "QC"),
     "Quality unit responsibilities", "the quality unit"),
    ("세척 · 시설 관리", "세척", ("세척", "건물", "유지관리", "시설관리", "시설 관리"),
     "Cleaning · facility upkeep", "cleaning"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 덱 고정 문구 — 언어별 한 벌
# ──────────────────────────────────────────────────────────────────────────────
# [영문판 2026-09-15] 사실(카드 본문·용어 정의)은 브리프 JSON 의 `en` 블록과 용어사전의 `*_en`
# 에서 **그대로** 가져오고(LLM 슬롯 0), 덱이 직접 쓰는 고정 문구만 여기서 고른다.
# ★한국 업체명은 영문 덱에서 **로마자로 지어내지 않는다** — 이름을 못 쓰면 그 자리를 비우고
#   개수로만 남긴다(`_firm_for_lang`). 식약처 묶음 장은 업체명 목록이 본체라 영문에서는 빼고,
#   대신 본문에 건수만 적는다.
LANGS = ("ko", "en")
_EN_MONTH = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_EN_NUM = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}

STR: dict[str, dict[str, Any]] = {
    "ko": {
        "cover_eyebrow": "주간 규제 소식 · {m}월 {wk}주차",
        "cover_h1": ["이번 주", "규제 소식"],
        "tile_cards": "규제 신호 카드", "tile_class1": "FDA Class I 회수", "tile_wl": "FDA 경고서한",
        "tile_mfds_insp": "식약처 실사 결과 공개", "tile_mfds_act": "식약처 행정처분", "tile_recall": "회수 신호",
        "source_other": "출처: {label} 공식 발표",
        "chip_class1": "Class I 회수", "chip_recall": "회수", "chip_wl": "경고서한", "chip_483": "FDA 483",
        "chip_mfds_stop": "제조업무정지", "chip_mfds_act": "행정처분", "chip_mfds_insp": "GMP 실사 결과",
        "row_company": "업체", "impl_label": "시사점",
        "wl_eyebrow": "FDA 경고서한", "wl_h1_a": "경고서한 {n}건,", "wl_h1_b": "공통점은 {theme}",
        "wl_total": "{n}건 중", "wl_bars_head": "겹치는 지적",
        "mfds_eyebrow": "식약처 사후 GMP 실사", "mfds_h1_a": "실사 결과 {n}곳,", "mfds_h1_b": "보완 분야는",
        "gl_eyebrow": "이번 주 용어", "gl_h1": "이번 주 소식에 나온 용어 {num}",
        "gl_cap": "정의는 GRM 용어사전에서 그대로 가져왔습니다.",
        "ck_eyebrow": "이번 주 점검 포인트", "ck_h1": "우리 현장에서 확인할 것",
        "ck_cap": "각 항목은 이번 주 카드의 '점검' 칸에서 가져왔습니다.",
        "cl_h1_a": "전체 {n}건,", "cl_h1_b": "원문 링크와 함께",
        "cl_body": "요약은 한국어로, 출처는 각 기관의 공식 공고입니다.",
        "cl_note": "메일로 받아보고 싶다면, 사이트에서 뉴스레터를 구독하세요.",
        "cl_ft": "매주 월요일 · 무료",
        "ai_note": "이미지는 AI 도구로 생성되었습니다",
        "doc_title": "{m}월 {wk}주차 규제 소식",
        "cap_head": "이번 주 규제 소식, 카드 {n}장.",
        "cap_class1": "· Class I 회수 {n}건", "cap_wl": "· 경고서한 {n}건 — {themes}",
        "cap_mfds": "· 식약처 — {bits}", "cap_mfds_act": "행정처분 {n}건", "cap_mfds_insp": "실사 결과 {n}곳",
        "cap_terms": "용어 {n}개", "cap_checks": "점검 포인트 {n}개",
        "cap_link": "전체 {n}건과 원문 링크",
        "cap_cta": ["어떤 항목이 제일 신경 쓰이시나요?", "댓글로 남겨 주시면 다음 주에 다룹니다."],
        "cap_tags": ["#GMP #제약 #바이오 #규제 #품질관리", "#QA #FDA #식약처 #경고서한 #제약바이오"],
        # [마케팅 2026-09-23] '이번 주 한 건' 캡션(기본, `--caption one`) 전용 고정 문구.
        "one_checks_head": "현장 점검 포인트",
        "one_link_head": "공식 원문과 이번 주 브리프 전체",
        "one_tags": "#GMP #QA #품질보증 #FDA #제약",
        # [마케팅 2026-09-23 L-06] 월간 결산 덱("이달의 변화 5개 + 숫자 1개") 전용 고정 문구.
        "month_eyebrow": "월간 규제 결산",
        # 제목 줄바꿈은 `|` 로 **구 경계를 직접** 정한다 — split_two 는 폭으로 자르다 "꼭 봐야 /
        # 할 변화" 처럼 구 중간을 끊었다(2026-09-23 검수 · 줄바꿈 반려 이력 3회).
        "month_h1": "{m}월에 꼭 볼|변화 {n}가지",
        "month_meta": "{k}호 · 카드 {c}장",
        "month_num_eyebrow": "이번 달 숫자 하나",
        "month_num_h1": "가장 많이 나온 소식 유형",
        "month_num_bars_head": "카드 유형별 건수",
        "month_num_bars_total": "카드 {c}장 중",
        "month_num_cap": "{m}월 발행 카드 {c}장 기준",
        "month_doc_title": "{m}월 규제 결산",
        "month_cap_head": "{m}월 규제 결산, 꼭 볼 변화 {n}가지.",
        "month_count": "이번 달 카드 {c}장 · 브리프 {k}호",
        "month_link_head": "지난 호 전체 보기",
    },
    "en": {
        "cover_eyebrow": "Weekly regulatory news · {mon}, week {wk}",
        "cover_h1": ["This week in", "regulatory news"],
        "tile_cards": "Regulatory signal cards", "tile_class1": "FDA Class I recalls",
        "tile_wl": "FDA warning letters", "tile_mfds_insp": "MFDS inspection results",
        "tile_mfds_act": "MFDS administrative actions", "tile_recall": "Recall signals",
        "source_other": "Source: {label} official announcement",
        "chip_class1": "Class I recall", "chip_recall": "Recall", "chip_wl": "Warning letter",
        "chip_483": "FDA 483", "chip_mfds_stop": "Manufacturing suspension",
        "chip_mfds_act": "Administrative action", "chip_mfds_insp": "GMP inspection result",
        "row_company": "Company", "impl_label": "What it means",
        "wl_eyebrow": "FDA warning letters", "wl_h1_a": "{n} warning letter{s},", "wl_h1_b": "most on {theme}",
        "wl_total": "of {n}", "wl_bars_head": "Overlapping findings",
        "mfds_eyebrow": "MFDS post-approval GMP inspections", "mfds_h1_a": "{n} site{s} inspected,",
        "mfds_h1_b": "gaps were in",
        "gl_eyebrow": "Terms this week", "gl_h1": "{num} terms from this week's news",
        "gl_cap": "Definitions are taken verbatim from the GRM glossary.",
        "ck_eyebrow": "Checks this week", "ck_h1": "What to check on your site",
        "ck_cap": "Each item comes from the 'checks' field of this week's cards.",
        "cl_h1_a": "All {n} item{s},", "cl_h1_b": "with links to the originals",
        "cl_body": "Summaries in English; sources are each authority's official announcement.",
        "cl_note": "Prefer email? Subscribe to the newsletter on the site.",
        "cl_ft": "Every Monday · free",
        "ai_note": "Images generated with AI tools",
        "doc_title": "Regulatory news · {mon}, week {wk}",
        "cap_head": "This week's regulatory news, {n} card{s}.",
        "cap_class1": "· {n} Class I recall{s}", "cap_wl": "· {n} warning letter{s} — {themes}",
        "cap_mfds": "· MFDS (Korea) — {bits}", "cap_mfds_act": "{n} administrative action{s}",
        "cap_mfds_insp": "{n} inspection result{s}",
        "cap_terms": "{n} term{s}", "cap_checks": "{n} check{s}",
        "cap_link": "All {n} item{s}, with links to the originals",
        "cap_cta": ["Which item would concern you most?", "Tell us in the comments and we'll cover it next week."],
        "cap_tags": ["#GMP #pharma #biotech #regulatory #qualityassurance",
                     "#QA #FDA #EMA #MHRA #MFDS"],
        "one_checks_head": "What to check on site",
        "one_link_head": "Official sources and the full weekly brief",
        "one_tags": "#GMP #QA #QualityAssurance #FDA #pharma",
        "month_eyebrow": "Monthly regulatory recap",
        "month_h1": "{mon}:|{n} change{s} worth your time",
        "month_meta": "{k} issue{ks} · {c} card{cs}",
        "month_num_eyebrow": "This month in one number",
        "month_num_h1": "Most common card types",
        "month_num_bars_head": "Cards by type",
        "month_num_bars_total": "of {c} card{s}",
        "month_num_cap": "Based on {c} card{s} published in {mon}",
        "month_doc_title": "{mon} regulatory recap",
        "month_cap_head": "{mon} recap: {n} change{s} worth your time.",
        "month_count": "{c} card{cs} this month · {k} issue{ks}",
        "month_link_head": "See all past issues",
    },
}

# 용어별 미니 다이어그램(150x88) — 소개 덱과 같은 결. 없는 용어는 일반 문서 아이콘.
# [영문판 2026-09-15] 도형은 한 벌, 글자만 언어별로 갈린다. SVG 안의 `{key}` 자리를 MINI_LABELS
# 에서 채운다 — 언어를 더해도 도형이 갈라지지 않고, 빠뜨린 라벨은 KeyError 로 즉시 드러난다
# (테스트가 두 언어 전부를 실제로 채워 본다). 라틴 라벨(HEPA·Grade A)은 두 언어가 같아 그대로 둔다.
MINI_SVG = {
    "endotoxin": """<svg class="mini" viewBox="0 0 150 88"><rect x="22" y="30" width="66" height="28" rx="14" fill="#F4E7DF" stroke="#C2603F" stroke-width="2"/><g stroke="#C2603F" stroke-width="2" stroke-linecap="round"><path d="M36 30v-9M52 30v-10M68 30v-9M82 32v-9M36 58v9M52 58v10M68 58v9M82 56v9"/></g><path d="M92 44h16" stroke="#141413" stroke-width="2" stroke-linecap="round"/><path d="M103 38l6 6-6 6" fill="none" stroke="#141413" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="147" y="48" font-size="12" fill="#BD4B36" font-weight="700" text-anchor="end">{fever}</text><text x="22" y="82" font-size="10" fill="#8E8B82">{lps}</text></svg>""",
    "aseptic-processing": """<svg class="mini" viewBox="0 0 150 88"><rect x="30" y="6" width="90" height="12" rx="3" fill="#EFE9DE" stroke="#8E8B82" stroke-width="1.5"/><text x="62" y="15" font-size="8" fill="#6C6A64" font-weight="700">HEPA</text><g stroke="#C2603F" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M45 24v22M45 46l-4-5M45 46l4-5M75 24v22M75 46l-4-5M75 46l4-5M105 24v22M105 46l-4-5M105 46l4-5"/></g><rect x="66" y="56" width="18" height="24" rx="3" fill="#fff" stroke="#141413" stroke-width="1.8"/><rect x="69" y="51" width="12" height="5" rx="1" fill="#141413"/><text x="92" y="76" font-size="10" fill="#8E8B82">Grade A</text></svg>""",
    "cross-contamination": """<svg class="mini" viewBox="0 0 150 88"><rect x="14" y="34" width="44" height="40" rx="5" fill="#fff" stroke="#141413" stroke-width="1.8"/><rect x="92" y="34" width="44" height="40" rx="5" fill="#fff" stroke="#141413" stroke-width="1.8"/><g fill="#C2603F"><circle cx="26" cy="48" r="3"/><circle cx="38" cy="60" r="3"/><circle cx="46" cy="46" r="3"/><circle cx="30" cy="66" r="3"/><circle cx="104" cy="60" r="3"/></g><path d="M60 50c10-12 20-12 30 0" fill="none" stroke="#BD4B36" stroke-width="1.8" stroke-dasharray="4 3"/><path d="M86 44l4 6-7 1" fill="none" stroke="#BD4B36" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><text x="24" y="26" font-size="10" fill="#6C6A64">{prod_a}</text><text x="102" y="26" font-size="10" fill="#6C6A64">{prod_b}</text></svg>""",
    "oos": """<svg class="mini" viewBox="0 0 150 88"><line x1="10" y1="26" x2="140" y2="26" stroke="#BD4B36" stroke-width="1.8" stroke-dasharray="5 4"/><text x="12" y="20" font-size="10" fill="#BD4B36" font-weight="700">{spec_upper}</text><polyline points="10,66 28,62 46,65 64,58 82,63 100,18 118,62 140,60" fill="none" stroke="#141413" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="100" cy="18" r="5" fill="#BD4B36"/><line x1="10" y1="76" x2="140" y2="76" stroke="#DCD3C7" stroke-width="1.5"/></svg>""",
    "recall": """<svg class="mini" viewBox="0 0 150 88"><rect x="24" y="52" width="26" height="20" rx="3" fill="#EFE9DE"/><rect x="62" y="36" width="26" height="36" rx="3" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.5"/><rect x="100" y="14" width="26" height="58" rx="3" fill="#C2603F"/><text x="30" y="84" font-size="11" fill="#6C6A64" font-weight="700">III</text><text x="70" y="84" font-size="11" fill="#6C6A64" font-weight="700">II</text><text x="110" y="84" font-size="11" fill="#BD4B36" font-weight="800">I</text><text x="4" y="18" font-size="10" fill="#8E8B82">{risk_up}</text></svg>""",
    "deviation": """<svg class="mini" viewBox="0 0 150 88"><line x1="10" y1="44" x2="140" y2="44" stroke="#8E8B82" stroke-width="1.6" stroke-dasharray="5 4"/><polyline points="10,44 50,44 74,20 98,44 140,44" fill="none" stroke="#141413" stroke-width="2.2" stroke-linejoin="round"/><circle cx="74" cy="20" r="5" fill="#BD4B36"/><text x="12" y="70" font-size="10" fill="#8E8B82">{approved}</text><text x="86" y="16" font-size="10" fill="#BD4B36" font-weight="700">{deviation}</text></svg>""",
    "process-validation": """<svg class="mini" viewBox="0 0 150 88"><rect x="14" y="28" width="122" height="30" fill="#F4E7DF"/><g stroke="#BD4B36" stroke-width="1.6" stroke-dasharray="5 4"><line x1="14" y1="28" x2="136" y2="28"/><line x1="14" y1="58" x2="136" y2="58"/></g><text x="14" y="21" font-size="10" fill="#BD4B36" font-weight="700">{spec_range}</text><g fill="#141413"><circle cx="40" cy="45" r="4.5"/><circle cx="75" cy="41" r="4.5"/><circle cx="110" cy="47" r="4.5"/></g><text x="6" y="80" font-size="10" fill="#8E8B82">{batch}</text><g font-size="10" fill="#8E8B82" text-anchor="middle"><text x="40" y="80">1</text><text x="75" y="80">2</text><text x="110" y="80">3</text></g></svg>""",
    "shelf-life": """<svg class="mini" viewBox="0 0 150 88"><line x1="14" y1="24" x2="136" y2="24" stroke="#BD4B36" stroke-width="1.6" stroke-dasharray="5 4"/><text x="14" y="18" font-size="10" fill="#BD4B36" font-weight="700">{spec_limit}</text><g stroke="#8E8B82" stroke-width="1.5"><line x1="24" y1="34" x2="24" y2="66"/><line x1="112" y1="34" x2="112" y2="66"/></g><g stroke="#C2603F" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M24 40h88"/><path d="M28 36l-4 4 4 4M108 36l4 4-4 4"/></g><text x="68" y="34" font-size="10" fill="#BD4B36" font-weight="700" text-anchor="middle">{shelf_life}</text><polyline points="24,52 46,53 68,55 90,57 112,59" fill="none" stroke="#141413" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/><line x1="14" y1="66" x2="136" y2="66" stroke="#DCD3C7" stroke-width="1.5"/><text x="14" y="82" font-size="10" fill="#8E8B82">{storage}</text></svg>""",
    "capa": """<svg class="mini" viewBox="0 0 150 88"><g fill="#fff" stroke="#141413" stroke-width="1.6"><rect x="10" y="22" width="36" height="22" rx="4"/><rect x="57" y="22" width="36" height="22" rx="4"/><rect x="104" y="22" width="36" height="22" rx="4"/></g><g font-size="10" fill="#141413" text-anchor="middle"><text x="28" y="37">{cause}</text><text x="75" y="37">{action}</text><text x="122" y="37">{verify}</text></g><g stroke="#C2603F" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M46 33h9M51 29l4 4-4 4M93 33h9M98 29l4 4-4 4"/><path d="M122 44v14H28v-6"/><path d="M24 50l4-5 4 5"/></g><text x="75" y="78" font-size="10" fill="#8E8B82" text-anchor="middle">{prevent}</text></svg>""",
    "master-standard-documents": """<svg class="mini" viewBox="0 0 150 88"><path d="M9 7v72M9 7h4M9 79h4M9 43h-5" fill="none" stroke="#C2603F" stroke-width="1.5" stroke-linecap="round"/><path d="M14 5h10l5 5v12H14z" fill="#fff" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><path d="M24 5v5h5" fill="none" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><text x="36" y="17" font-size="8" fill="#141413">{d1}</text><path d="M14 26h10l5 5v12H14z" fill="#fff" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><path d="M24 26v5h5" fill="none" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><text x="36" y="38" font-size="8" fill="#141413">{d2}</text><path d="M14 47h10l5 5v12H14z" fill="#fff" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><path d="M24 47v5h5" fill="none" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><text x="36" y="59" font-size="8" fill="#141413">{d3}</text><path d="M14 68h10l5 5v12H14z" fill="#fff" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><path d="M24 68v5h5" fill="none" stroke="#141413" stroke-width="1.3" stroke-linejoin="round"/><text x="36" y="80" font-size="8" fill="#141413">{d4}</text></svg>""",
    "certificate-of-analysis": """<svg class="mini" viewBox="0 0 150 88"><path d="M30 8h80l10 10v52H30z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M110 8v10h10" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><g stroke="#DCD3C7" stroke-width="1.2"><path d="M38 30h74M38 42h74"/></g><text x="38" y="26" font-size="9" fill="#8E8B82">{test}</text><text x="38" y="38" font-size="9" fill="#8E8B82">{crit}</text><text x="38" y="50" font-size="9" fill="#BD4B36" font-weight="700">{res}</text><path d="M84 58q6-6 11 0t11-3" fill="none" stroke="#C2603F" stroke-width="1.6" stroke-linecap="round"/><text x="75" y="84" font-size="10" fill="#8E8B82" text-anchor="middle">{signed}</text></svg>""",
    "drug-product": """<svg class="mini" viewBox="0 0 150 88"><rect x="38" y="12" width="74" height="48" rx="5" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><g fill="#fff" stroke="#141413" stroke-width="1.5"><circle cx="58" cy="36" r="7"/><circle cx="75" cy="36" r="7"/><circle cx="92" cy="36" r="7"/></g><path d="M51 36h14M68 36h14M85 36h14" stroke="#141413" stroke-width="1.2" opacity="0.5"/><text x="75" y="74" font-size="10" fill="#BD4B36" text-anchor="middle" font-weight="700">{form}</text><text x="75" y="86" font-size="8" fill="#8E8B82" text-anchor="middle">{packed}</text></svg>""",
    "impurity": """<svg class="mini" viewBox="0 0 150 88"><rect x="14" y="18" width="122" height="44" rx="8" fill="#EFE9DE" stroke="#DCD3C7" stroke-width="1.5"/><g fill="#8E8B82"><circle cx="32" cy="32" r="4"/><circle cx="52" cy="46" r="4"/><circle cx="72" cy="30" r="4"/><circle cx="96" cy="44" r="4"/><circle cx="118" cy="32" r="4"/><circle cx="40" cy="52" r="4"/></g><circle cx="86" cy="52" r="5.5" fill="#BD4B36"/><path d="M86 58v8" fill="none" stroke="#BD4B36" stroke-width="1.5"/><text x="75" y="80" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{not_intended}</text></svg>""",
    "finished-product": """<svg class="mini" viewBox="0 0 150 88"><g fill="#EFE9DE" stroke="#8E8B82" stroke-width="1.6"><rect x="6" y="22" width="36" height="26" rx="4"/></g><path d="M45 35h12M52 31l5 4-5 4" fill="none" stroke="#8E8B82" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><rect x="60" y="22" width="36" height="26" rx="4" fill="#EFE9DE" stroke="#8E8B82" stroke-width="1.6"/><rect x="70" y="27" width="16" height="16" rx="2" fill="#fff" stroke="#141413" stroke-width="1.4"/><path d="M99 35h12M106 31l5 4-5 4" fill="none" stroke="#C2603F" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><rect x="114" y="22" width="30" height="26" rx="4" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><path d="M114 32h30" stroke="#C2603F" stroke-width="1.3" stroke-dasharray="3 2"/><text x="24" y="60" font-size="8" fill="#8E8B82" text-anchor="middle">{bulk}</text><text x="78" y="60" font-size="8" fill="#8E8B82" text-anchor="middle">{fill}</text><text x="129" y="60" font-size="8" fill="#BD4B36" text-anchor="middle" font-weight="700">{pack}</text><text x="75" y="82" font-size="9" fill="#8E8B82" text-anchor="middle">{done}</text></svg>""",
    "qualification": """<svg class="mini" viewBox="0 0 150 88"><rect x="10" y="22" width="44" height="44" rx="5" fill="#EFE9DE" stroke="#141413" stroke-width="1.7"/><path d="M20 44h24M32 34v20" stroke="#8E8B82" stroke-width="1.5"/><path d="M67 28l3.5 3.5L78 23" fill="none" stroke="#C2603F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="84" y="32" font-size="9" fill="#8E8B82">{installed}</text><path d="M67 46l3.5 3.5L78 41" fill="none" stroke="#C2603F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="84" y="50" font-size="9" fill="#8E8B82">{works}</text><path d="M67 64l3.5 3.5L78 59" fill="none" stroke="#C2603F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="84" y="68" font-size="9" fill="#BD4B36" font-weight="700">{results}</text></svg>""",
    "data-integrity": """<svg class="mini" viewBox="0 0 150 88"><text x="46" y="14" font-size="8" fill="#8E8B82" text-anchor="middle">{created}</text><text x="130" y="14" font-size="8" fill="#8E8B82" text-anchor="middle">{disposed}</text><circle cx="46" cy="30" r="5.5" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><circle cx="74" cy="30" r="5.5" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><circle cx="102" cy="30" r="5.5" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><circle cx="130" cy="30" r="5.5" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><path d="M51.5 30h17" stroke="#C2603F" stroke-width="1.8"/><path d="M79.5 30h17" stroke="#C2603F" stroke-width="1.8"/><path d="M107.5 30h17" stroke="#C2603F" stroke-width="1.8"/><text x="8" y="33" font-size="8" fill="#BD4B36" font-weight="700">{kept}</text><circle cx="46" cy="60" r="5.5" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><circle cx="74" cy="60" r="5.5" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><circle cx="102" cy="60" r="5.5" fill="#fff" stroke="#8E8B82" stroke-width="1.8" stroke-dasharray="3 2"/><circle cx="130" cy="60" r="5.5" fill="#fff" stroke="#8E8B82" stroke-width="1.8" stroke-dasharray="3 2"/><path d="M51.5 60h17" stroke="#C2603F" stroke-width="1.8"/><path d="M107.5 60h17" stroke="#8E8B82" stroke-width="1.8"/><path d="M83 55 l10 10M93 55 l-10 10" stroke="#BD4B36" stroke-width="1.8" stroke-linecap="round"/><text x="8" y="63" font-size="8" fill="#8E8B82">{broken}</text><text x="75" y="82" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{cap}</text></svg>""",
    "sterility": """<svg class="mini" viewBox="0 0 150 88"><rect x="52" y="18" width="46" height="52" rx="5" fill="#fff" stroke="#141413" stroke-width="1.8"/><rect x="64" y="11" width="22" height="7" rx="2" fill="#141413"/><text x="75" y="54" font-size="30" fill="#BD4B36" text-anchor="middle" font-weight="800">0</text><text x="75" y="84" font-size="10" fill="#8E8B82" text-anchor="middle">{no_micro}</text></svg>""",
    "documentation": """<svg class="mini" viewBox="0 0 150 88"><path d="M8 22h22l8 8v32H8z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M30 22v8h8" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M14 36h18M14 43h18M14 50h18" stroke="#C2603F" stroke-width="1.8" stroke-linecap="round"/><path d="M43 42h14M52 38l5 4-5 4" fill="none" stroke="#141413" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M62 22h22l8 8v32H62z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M84 22v8h8" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M72 44l3.5 3.5L83 39" fill="none" stroke="#C2603F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M97 42h14M106 38l5 4-5 4" fill="none" stroke="#141413" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M116 22h18l8 8v32H116z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M134 22v8h8" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M121 36h15M121 43h15M121 50h15" stroke="#8E8B82" stroke-width="1.8" stroke-linecap="round"/><text x="23" y="78" font-size="9" fill="#8E8B82" text-anchor="middle">{prepare}</text><text x="77" y="78" font-size="9" fill="#8E8B82" text-anchor="middle">{approve}</text><text x="129" y="78" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{control}</text></svg>""",
    "quality-unit": """<svg class="mini" viewBox="0 0 150 88"><rect x="8" y="22" width="52" height="44" rx="5" fill="#EFE9DE" stroke="#8E8B82" stroke-width="1.6"/><line x1="70" y1="12" x2="70" y2="76" stroke="#DCD3C7" stroke-width="1.6" stroke-dasharray="5 4"/><rect x="80" y="22" width="60" height="44" rx="5" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><g fill="#fff" stroke="#C2603F" stroke-width="1.4"><rect x="87" y="34" width="22" height="18" rx="3"/><rect x="112" y="34" width="22" height="18" rx="3"/></g><text x="98" y="47" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">QA</text><text x="123" y="47" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">QC</text><text x="34" y="78" font-size="9" fill="#8E8B82" text-anchor="middle">{production}</text><text x="110" y="78" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{quality}</text></svg>""",
    "cleaning-validation": """<svg class="mini" viewBox="0 0 150 88"><line x1="10" y1="26" x2="140" y2="26" stroke="#BD4B36" stroke-width="1.6" stroke-dasharray="5 4"/><text x="10" y="20" font-size="9" fill="#BD4B36" font-weight="700">{limit}</text><rect x="16" y="34" width="38" height="30" rx="4" fill="#fff" stroke="#141413" stroke-width="1.7"/><g fill="#8E8B82"><circle cx="26" cy="44" r="3"/><circle cx="38" cy="52" r="3"/><circle cx="46" cy="42" r="3"/></g><path d="M62 48h20M77 44l5 4-5 4" fill="none" stroke="#141413" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><rect x="90" y="34" width="38" height="30" rx="4" fill="#fff" stroke="#141413" stroke-width="1.7"/><circle cx="109" cy="49" r="3" fill="#C2603F"/><text x="35" y="78" font-size="9" fill="#8E8B82" text-anchor="middle">{before}</text><text x="109" y="78" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{after}</text></svg>""",
    "master-production-record": """<svg class="mini" viewBox="0 0 150 88"><path d="M8 14h30l10 10v36H8z" fill="#F4E7DF" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M38 14v10h10" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M16 28h22M16 35h22M16 42h22" stroke="#C2603F" stroke-width="1.8" stroke-linecap="round"/><path d="M23 52l3.5 3.5L34 47" fill="none" stroke="#BD4B36" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M52 37h12" stroke="#8E8B82" stroke-width="1.5"/><path d="M64 37v-20h8M64 37h8M64 37v20h8" fill="none" stroke="#8E8B82" stroke-width="1.5"/><path d="M82 6h18l6 6v16H82z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M100 6v6h6" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M82 30h18l6 6v16H82z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M100 30v6h6" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M82 54h18l6 6v16H82z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M100 54v6h6" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><text x="28" y="78" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{master}</text><text x="94" y="84" font-size="9" fill="#8E8B82" text-anchor="middle">{each}</text></svg>""",
    "procedure": """<svg class="mini" viewBox="0 0 150 88"><path d="M40 8h60l10 10v54H40z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M100 8v10h10" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><g fill="#C2603F" font-size="9" font-weight="700"><text x="48" y="30">1</text><text x="48" y="45">2</text><text x="48" y="60">3</text></g><g stroke="#8E8B82" stroke-width="1.6" stroke-linecap="round"><path d="M58 27h42M58 42h42M58 57h30"/></g><text x="75" y="84" font-size="10" fill="#8E8B82" text-anchor="middle">{steps}</text></svg>""",
    "in-process-control": """<svg class="mini" viewBox="0 0 150 88"><path d="M10 38h130M135 34l5 4-5 4" fill="none" stroke="#8E8B82" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="62" cy="38" r="9" fill="#fff" stroke="#C2603F" stroke-width="1.8"/><path d="M57 38l3.5 3.5L68 33" fill="none" stroke="#C2603F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M62 47v12H34v-9" fill="none" stroke="#BD4B36" stroke-width="1.6" stroke-dasharray="4 3"/><path d="M30 52l4-5 4 5" fill="none" stroke="#BD4B36" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><text x="10" y="26" font-size="9" fill="#8E8B82">{during}</text><text x="70" y="64" font-size="9" fill="#BD4B36" font-weight="700">{adjust}</text><text x="140" y="26" font-size="9" fill="#8E8B82" text-anchor="end">{spec}</text></svg>""",
    "pharmaceutical-quality-system": """<svg class="mini" viewBox="0 0 150 88"><rect x="4" y="3" width="142" height="55" rx="9" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><path d="M18 15v32" stroke="#BD4B36" stroke-width="1.6" stroke-linecap="round"/><circle cx="18" cy="15" r="3.6" fill="#fff" stroke="#BD4B36" stroke-width="1.5"/><text x="29" y="18" font-size="8" fill="#141413">{n1}</text><circle cx="18" cy="31" r="3.6" fill="#fff" stroke="#BD4B36" stroke-width="1.5"/><text x="29" y="34" font-size="8" fill="#141413">{n2}</text><circle cx="18" cy="47" r="3.6" fill="#fff" stroke="#BD4B36" stroke-width="1.5"/><text x="29" y="50" font-size="8" fill="#141413">{n3}</text><path d="M8 70h134M137 66l5 4-5 4" fill="none" stroke="#8E8B82" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><text x="8" y="84" font-size="8" fill="#8E8B82">{cycle}</text><text x="142" y="84" font-size="8" fill="#BD4B36" text-anchor="end" font-weight="700">{goal}</text></svg>""",
    "quality-risk-management": """<svg class="mini" viewBox="0 0 150 88"><g fill="none" stroke="#C2603F" stroke-width="1.8"><rect x="5" y="14" width="62" height="24" rx="4"/><rect x="79" y="14" width="62" height="24" rx="4"/><rect x="79" y="50" width="62" height="24" rx="4"/><rect x="5" y="50" width="62" height="24" rx="4"/></g><g font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700"><text x="36" y="30">{assess}</text><text x="110" y="30">{control}</text><text x="110" y="66">{communicate}</text><text x="36" y="66">{review}</text></g><g fill="none" stroke="#8E8B82" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M67 26h12m-4-4 4 4-4 4"/><path d="M110 38v12m-4-4 4 4 4-4"/><path d="M79 62H67m4 4-4-4 4-4"/><path d="M36 50v-12m4 4-4-4-4 4"/></g></svg>""",
    "batch-record": """<svg class="mini" viewBox="0 0 150 88"><path d="M8 10h14l6 6v18H8z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M22 10v6h6" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><text x="33" y="25" font-size="8" fill="#141413">{made}</text><path d="M8 52h14l6 6v18H8z" fill="#fff" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><path d="M22 52v6h6" fill="none" stroke="#141413" stroke-width="1.6" stroke-linejoin="round"/><text x="33" y="67" font-size="8" fill="#141413">{quality}</text><path d="M84 22h4q4 0 4 4v10M84 64h4q4 0 4-4V40" fill="none" stroke="#8E8B82" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M92 38h6M95 34l5 4-5 4" fill="none" stroke="#C2603F" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><rect x="98" y="24" width="50" height="28" rx="4" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><text x="123" y="41" font-size="8" fill="#BD4B36" text-anchor="middle" font-weight="700">{batch}</text></svg>""",
    "identification-test": """<svg class="mini" viewBox="0 0 150 88"><rect x="16" y="26" width="26" height="36" rx="3" fill="#fff" stroke="#141413" stroke-width="1.7"/><rect x="22" y="20" width="14" height="6" rx="1.5" fill="#141413"/><rect x="16" y="46" width="26" height="16" rx="0" fill="#8E8B82" opacity="0.35"/><g stroke="#C2603F" stroke-width="2" stroke-linecap="round"><path d="M56 38h22M56 48h22"/></g><rect x="92" y="26" width="26" height="36" rx="3" fill="#fff" stroke="#8E8B82" stroke-width="1.7" stroke-dasharray="4 3"/><rect x="98" y="20" width="14" height="6" rx="1.5" fill="#8E8B82"/><path d="M128 42l3.5 3.5L139 37" fill="none" stroke="#BD4B36" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="29" y="78" font-size="9" fill="#8E8B82" text-anchor="middle">{sample}</text><text x="105" y="78" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{intended}</text></svg>""",
    "batch-number": """<svg class="mini" viewBox="0 0 150 88"><rect x="16" y="16" width="52" height="40" rx="4" fill="#EFE9DE" stroke="#141413" stroke-width="1.7"/><rect x="22" y="28" width="40" height="16" rx="2" fill="#fff" stroke="#C2603F" stroke-width="1.5"/><text x="42" y="40" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="800">LOT</text><text x="42" y="68" font-size="9" fill="#8E8B82" text-anchor="middle">{unique}</text><g fill="none" stroke="#C2603F" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M74 36h56m-6-4 6 4-6 4"/></g><g fill="#C2603F"><circle cx="88" cy="36" r="3"/><circle cx="106" cy="36" r="3"/><circle cx="124" cy="36" r="3"/></g><text x="106" y="68" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{trace}</text></svg>""",
    "starting-material": """<svg class="mini" viewBox="0 0 150 88"><g fill="#EFE9DE" stroke="#141413" stroke-width="1.7"><rect x="10" y="16" width="24" height="30" rx="4"/><rect x="40" y="16" width="24" height="30" rx="4"/></g><path d="M10 24h24M40 24h24" stroke="#8E8B82" stroke-width="1.4"/><path d="M72 31h18M85 27l5 4-5 4" fill="none" stroke="#141413" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><rect x="98" y="14" width="38" height="34" rx="5" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><text x="75" y="62" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{used}</text><g stroke="#8E8B82" stroke-width="1.5" fill="none"><rect x="30" y="70" width="16" height="13" rx="2"/><path d="M30 70l16 13M46 70l-16 13"/></g><text x="52" y="80" font-size="9" fill="#8E8B82">{excl_pack}</text></svg>""",
    "active-ingredient": """<svg class="mini" viewBox="0 0 150 88"><circle cx="40" cy="34" r="24" fill="#EFE9DE" stroke="#141413" stroke-width="1.8"/><path d="M40 10a24 24 0 0 1 0 48z" fill="#C2603F"/><path d="M40 10v48" stroke="#141413" stroke-width="1.6"/><path d="M72 34h20M87 30l5 4-5 4" fill="none" stroke="#BD4B36" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M102 24c9 0 9 10 0 10s-9 10 0 10" fill="none" stroke="#BD4B36" stroke-width="1.8" stroke-linecap="round"/><text x="114" y="56" font-size="9" fill="#BD4B36" text-anchor="middle">{effect}</text><rect x="14" y="70" width="9" height="9" rx="2" fill="#C2603F"/><text x="27" y="78" font-size="9" fill="#BD4B36" font-weight="700">{active}</text><rect x="78" y="70" width="9" height="9" rx="2" fill="#EFE9DE" stroke="#8E8B82" stroke-width="1.2"/><text x="91" y="78" font-size="9" fill="#8E8B82">{other}</text></svg>""",
    "business-operator-recall": """<svg class="mini" viewBox="0 0 150 88"><g stroke="#DCD3C7" stroke-width="1.5" fill="none"><rect x="12" y="6" width="18" height="13" rx="2"/><path d="M12 6l18 13M30 6L12 19"/></g><text x="36" y="17" font-size="9" fill="#8E8B82">{no_order}</text><rect x="10" y="32" width="36" height="32" rx="4" fill="#F4E7DF" stroke="#C2603F" stroke-width="1.8"/><text x="28" y="80" font-size="9" fill="#BD4B36" text-anchor="middle" font-weight="700">{operator}</text><g fill="none" stroke="#C2603F" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M108 48H54m6-5-6 5 6 5"/></g><g fill="#8E8B82"><rect x="112" y="30" width="10" height="13" rx="1.5"/><rect x="126" y="30" width="10" height="13" rx="1.5"/><rect x="119" y="52" width="10" height="13" rx="1.5"/></g><text x="124" y="80" font-size="9" fill="#8E8B82" text-anchor="middle">{market}</text></svg>""",
    "_generic": """<svg class="mini" viewBox="0 0 150 88"><path d="M52 14h30l16 16v44H52z" fill="#fff" stroke="#141413" stroke-width="1.8" stroke-linejoin="round"/><path d="M82 14v16h16" fill="none" stroke="#141413" stroke-width="1.8" stroke-linejoin="round"/><path d="M60 44h30M60 54h30M60 64h20" stroke="#C2603F" stroke-width="2" stroke-linecap="round"/></svg>""",
}

# 그림 라벨 — 언어별. 영어가 한국어보다 길어 150 폭을 넘기 쉬우니 **짧은 쪽**을 고른다
# (덱 그림은 카드에서 작게 나와 긴 문장은 어차피 읽히지 않는다).
MINI_LABELS = {
    "endotoxin": {"ko": {"fever": "발열", "lps": "그람음성균 세포벽 · LPS"},
                  "en": {"fever": "Fever", "lps": "Gram-negative wall · LPS"}},
    "aseptic-processing": {"ko": {}, "en": {}},
    "cross-contamination": {"ko": {"prod_a": "제품 A", "prod_b": "제품 B"},
                            "en": {"prod_a": "Product A", "prod_b": "Product B"}},
    "oos": {"ko": {"spec_upper": "규격 상한"}, "en": {"spec_upper": "Spec limit"}},
    "recall": {"ko": {"risk_up": "위해도 ↑"}, "en": {"risk_up": "Risk ↑"}},
    "deviation": {"ko": {"approved": "승인된 지시", "deviation": "일탈"},
                  "en": {"approved": "Approved", "deviation": "Deviation"}},
    "process-validation": {"ko": {"spec_range": "규격 범위", "batch": "배치"},
                           "en": {"spec_range": "Spec range", "batch": "Batch"}},
    "shelf-life": {"ko": {"spec_limit": "규격 한계", "shelf_life": "유효기간", "storage": "표시된 보관조건"},
                   "en": {"spec_limit": "Spec limit", "shelf_life": "Shelf life", "storage": "Labelled storage"}},
    "capa": {"ko": {"cause": "원인", "action": "조치", "verify": "확인", "prevent": "재발 방지"},
             "en": {"cause": "Cause", "action": "Action", "verify": "Verify", "prevent": "Prevents recurrence"}},
    "master-standard-documents": {"ko": {"d1": "제품표준서", "d2": "제조관리기준서", "d3": "품질관리기준서", "d4": "제조위생관리기준서"},
                                 "en": {"d1": "Product standard", "d2": "Manufacturing control", "d3": "Quality control", "d4": "Manufacturing hygiene"}},
    "certificate-of-analysis": {"ko": {"test": "시험", "crit": "판정 기준", "res": "결과", "signed": "품질부서 날짜·서명"},
                               "en": {"test": "Test", "crit": "Criteria", "res": "Result", "signed": "Dated and signed by QU"}},
    "drug-product": {"ko": {"form": "투여형태", "packed": "최종 포장까지"},
                    "en": {"form": "Dosage form", "packed": "Through final packaging"}},
    "impurity": {"ko": {"not_intended": "의도하지 않은 성분"},
                "en": {"not_intended": "Unintended component"}},
    "finished-product": {"ko": {"bulk": "벌크", "fill": "최종 용기 충전", "pack": "포장", "done": "모든 생산 단계 완료"},
                        "en": {"bulk": "Bulk", "fill": "Filling", "pack": "Packaging", "done": "All stages complete"}},
    "qualification": {"ko": {"installed": "설치", "works": "작동", "results": "기대한 결과"},
                     "en": {"installed": "Installed", "works": "Operates", "results": "As expected"}},
    "data-integrity": {"ko": {"created": "생성", "disposed": "폐기", "kept": "유지", "broken": "끊김", "cap": "끊김 없이 유지되는 정도"},
                      "en": {"created": "Creation", "disposed": "Disposal", "kept": "Kept", "broken": "Broken", "cap": "The degree it stays unbroken"}},
    "sterility": {"ko": {"no_micro": "살아 있는 미생물 없음"},
                 "en": {"no_micro": "No viable microorganisms"}},
    "documentation": {"ko": {"prepare": "작성", "approve": "승인", "control": "관리"},
                     "en": {"prepare": "Prepare", "approve": "Approve", "control": "Control"}},
    "quality-unit": {"ko": {"production": "생산", "quality": "품질부서"},
                    "en": {"production": "Production", "quality": "Quality unit"}},
    "cleaning-validation": {"ko": {"limit": "잔류 한계", "before": "세척 전", "after": "세척 뒤"},
                           "en": {"limit": "Residue limit", "before": "Before", "after": "After cleaning"}},
    "master-production-record": {"ko": {"master": "승인된 원본", "each": "배치 기록"},
                                "en": {"master": "Approved", "each": "Batch records"}},
    "procedure": {"ko": {"steps": "작업·주의사항·조치"},
                 "en": {"steps": "Operations and precautions"}},
    "in-process-control": {"ko": {"during": "제조 중", "adjust": "조정", "spec": "규격"},
                          "en": {"during": "In process", "adjust": "Adjust", "spec": "Spec"}},
    "pharmaceutical-quality-system": {"ko": {"n1": "조직·책임", "n2": "절차·공정", "n3": "자원", "cycle": "전 생애주기", "goal": "품질목표"},
                                     "en": {"n1": "Organisation & roles", "n2": "Procedures & processes", "n3": "Resources", "cycle": "Product lifecycle", "goal": "Quality goals"}},
    "quality-risk-management": {"ko": {"assess": "평가", "control": "통제", "communicate": "소통", "review": "검토"},
                               "en": {"assess": "Assess", "control": "Control", "communicate": "Communicate", "review": "Review"}},
    "batch-record": {"ko": {"made": "제조 과정", "quality": "최종 품질", "batch": "한 배치"},
                    "en": {"made": "Manufacture", "quality": "Final quality", "batch": "One batch"}},
    "identification-test": {"ko": {"sample": "검체", "intended": "의도한 물질"},
                           "en": {"sample": "Sample", "intended": "Intended substance"}},
    "batch-number": {"ko": {"unique": "고유 번호", "trace": "제조·유통 이력"},
                    "en": {"unique": "Unique number", "trace": "Traceable"}},
    "starting-material": {"ko": {"used": "생산에 쓰는 물질", "excl_pack": "포장자재 제외"},
                         "en": {"used": "Used in production", "excl_pack": "Packaging excluded"}},
    "active-ingredient": {"ko": {"other": "그 밖의 성분", "active": "유효성분", "effect": "약리작용"},
                         "en": {"other": "Other", "active": "Active", "effect": "Action"}},
    "business-operator-recall": {"ko": {"operator": "영업자", "market": "유통품", "no_order": "명령을 기다리지 않고"},
                                "en": {"operator": "The operator", "market": "Distributed", "no_order": "Without an order"}},
    "_generic": {"ko": {}, "en": {}},
}


def mini(fig_id: str, lang: str = "ko") -> str:
    """용어 id → 미니 다이어그램 SVG(라벨은 lang). 그림이 없는 용어는 일반 문서 아이콘."""
    key = fig_id if fig_id in MINI_SVG else "_generic"
    return MINI_SVG[key].format(**MINI_LABELS[key][lang])

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


def _label(card: dict, lang: str = "ko") -> str:
    table = AGENCY_LABEL if lang == "ko" else AGENCY_LABEL_EN
    return table.get(_agency(card), _agency(card))


def _card_text(card: dict, key: str, lang: str = "ko") -> str:
    """카드의 표시 문구 한 칸. 영문은 `en` 블록에서만 가져온다 — 없으면 빈 문자열이고,
    지어내거나 한국어를 그대로 싣지 않는다([[grm-en-korean-value-show-nothing-count-it]] 규율)."""
    if lang == "ko":
        return str(card.get(key) or "")
    return str(((card.get("en") or {}).get(key)) or "")


def _card_list(card: dict, key: str, lang: str = "ko") -> list[str]:
    src = card.get(key) if lang == "ko" else (card.get("en") or {}).get(key)
    return [str(x) for x in (src or []) if x]


def has_en(card: dict) -> bool:
    """영문 덱에 실을 수 있는 카드 — 제목과 요약이 영문 블록에 있어야 한다."""
    en = card.get("en") or {}
    return bool(str(en.get("title_issue") or "").strip() and str(en.get("summary") or "").strip())


def _firm_for_lang(name: str, lang: str) -> str:
    """영문 덱에서 한글 업체명은 **빈 문자열**. 로마자로 옮기면 없는 회사를 만든다 —
    이름을 못 쓰면 그 줄을 빼고, 건수로만 남긴다."""
    if lang != "ko" and _CJK.search(name or ""):
        return ""
    return name or ""


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


def _kind_chip(card: dict, lang: str = "ko") -> str:
    """칩 문구. 판별은 늘 한국어 정본으로 하고 `lang` 은 라벨만 고른다."""
    t = STR[lang]
    if is_class1_recall(card):
        return t["chip_class1"]
    if str(card.get("group") or "") == "Recall":
        return t["chip_recall"]
    if is_warning_letter(card):
        return t["chip_wl"]
    if "483" in str(card.get("id") or "") or "483" in str(card.get("type_tag") or ""):
        return t["chip_483"]
    if is_mfds_action(card):
        return t["chip_mfds_stop"] if "제조업무정지" in _text_of(card) else t["chip_mfds_act"]
    if is_mfds_inspection(card):
        return t["chip_mfds_insp"]
    # 마지막 폴백은 카드 데이터의 라벨 — 영문 덱에서 한글이면 카테고리(라틴)로 물러선다
    fallback = str(card.get("signal_label") or card.get("category") or "")
    if lang != "ko" and _CJK.search(fallback):
        return str(card.get("category") or "")
    return fallback


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


def theme_counts(cards: list[dict], lang: str = "ko") -> list[tuple[str, str, int]]:
    """경고서한 카드들의 공통 지적 버킷 카운트(카드 단위) — 많은 순, 동률은 선언 순.
    구조화된 칸(title_issue·key_facts)만 본다 — summary·implication 은 해설이라 주제어가 번진다.
    집계는 늘 한국어 정본으로 하고 `lang` 은 표시 라벨만 고른다."""
    out = []
    for order, (label_ko, short_ko, keys, label_en, short_en) in enumerate(WL_THEMES):
        label, short = (label_ko, short_ko) if lang == "ko" else (label_en, short_en)
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
    같은 점수면 **헤드라인 구조화 칸에 뜬 용어** → 다이어그램 있는 용어 → 사전 순서.
    일반어·2자 용어는 매칭 키에서 뺀다(제조·품질이 늘 1등이 된다).

    ★헤드라인 강신호를 동점 판정에 먼저 넣은 이유(2026-09-21): 그 전 규칙은 동점이면
    **사전 등재 순서**로 갈렸다. 등재 순서는 편집과 아무 상관이 없어서, 그림을 22개 추가하자
    '그림 있음' 묶음의 구성원이 바뀌며 그 주 헤드라인 용어(무균공정)가 사전 앞쪽 용어(완제품)에
    밀려났다. 같은 점수라면 **그 주 헤드라인이 실제로 다룬 말**이 '이번 주 용어'다."""
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
        head_strong = 0          # 헤드라인 카드의 구조화 칸에 뜬 횟수 — 동점 판정용
        for cid, strong, text in rows:
            hit_strong = any(_contains(strong, nm) for nm in names)
            hit = hit_strong or any(_contains(text, nm) for nm in names)
            if not hit:
                continue
            df += 1
            if cid in heads:
                score += 5 if hit_strong else 3
                if hit_strong:
                    head_strong += 1
            else:
                score += 2 if hit_strong else 1
        # 카드 40% 이상에 나오는 말은 '이번 주 용어'가 아니라 늘 나오는 말 — 점수를 깎는다
        if cards and df / len(cards) > 0.4:
            score //= 3
        if score:
            scored.append((-score, -head_strong, 0 if t.get("id") in MINI_SVG else 1, idx, t))
    scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [t for _, _, _, _, t in scored[:n]]


def pick_checks(groups: list[list[dict]], n: int = 6, max_width: float = 34.0,
                lang: str = "ko") -> list[str]:
    """카드의 '점검' 항목 — 우선순위 그룹(헤드라인 → 경고서한 → 실사) 순서대로, 각 그룹 안에서는
    카드 순서 그대로. 한 줄에 들어오는 것(폭 34)만 먼저 채우고 모자라면 긴 것으로 보충. 중복 제거."""
    seen: set[str] = set()
    fits: list[str] = []
    longs: list[str] = []
    for grp in groups:
        for c in grp:
            for chk in _card_list(c, "checks", lang):
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


# [마케팅 2026-09-23 개정] 헤드라인 표 rows 빌드(위 build_deck 의 facts 루프)가 이미 이 라벨을
# 인식해 표에서는 빼는 그 자리 — 캡션 헤더 줄에서는 값(날짜)으로 쓴다. 재정의하지 않게 상수로 뺀다.
_DATE_FACT_PREFIXES = ("발행", "Published", "Posted")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")


def _card_date(card: dict, lang: str) -> str:
    """카드 key_facts 의 '발행 부서/일자'류 사실에서 날짜만 뽑는다. 값에 발행 부서명이 섞여
    있으면(FDA '…(CDER) · 08/18/2026') 날짜 조각만 취하고, 그런 라벨 자체가 없는 카드
    (회수 등)는 빈 문자열 — 지어내지 않는다."""
    for f in _card_list(card, "key_facts", lang):
        kv = parse_fact(f)
        if kv and kv[0].startswith(_DATE_FACT_PREFIXES):
            m = _DATE_RE.search(kv[1])
            return m.group(0) if m else ""
    return ""


def _fact_header(card: dict, lang: str, firm_fn) -> str:
    """`{기관} · {문서 유형} · {업체} · {날짜}` 한 줄 — '지적 1/지적 2' 처럼 여러 줄로 감싸야
    하던 사실 대신, 감싸지 않고 한 줄로 들어오게 **구성 단계에서** 짧게 짓는다(2026-09-23 반려
    피드백: 긴 사실을 wrap 하면 "충분한 / 크기의" 처럼 문장 중간이 끊긴다 — 짧은 헤더는 애초에
    끊을 일이 없다). 기관·문서유형은 필수, 업체·날짜는 폭이 넘치면 순서대로(업체 먼저) 뺀다."""
    agency = _label(card, lang)          # 국문 덱은 '식약처', 영문 덱은 'MFDS' — 덱 본문과 같은 표기
    kind = _kind_chip(card, lang)
    firm_name = _firm_short(firm_fn(card))
    date = _card_date(card, lang)
    mandatory = [p for p in (agency, kind) if p]
    for extra in ([firm_name, date], [date], []):
        parts = mandatory + [p for p in extra if p]
        line = " · ".join(parts)
        if text_width(line) <= 26.0 or not extra:
            return line
    return " · ".join(mandatory)   # 도달 불가(위 루프의 extra=[] 가지가 항상 먼저 반환) — 방어용


def _one_extra_fact(card: dict, lang: str) -> str:
    """key_facts 중 줄바꿈 없이 폭 안에 들어오는 **첫 한 줄**('라벨: 값') — 없으면 빈 문자열.
    긴 지적문(citation·observation)은 폭을 넘으므로 애초에 고르지 않는다(감싸지 않는다 — 감싸면
    또 문장 중간이 끊긴다). 헤더가 이미 쓴 날짜 사실은 중복이라 건너뛴다."""
    for f in _card_list(card, "key_facts", lang):
        kv = parse_fact(f)
        if not kv or kv[0].startswith(_DATE_FACT_PREFIXES):
            continue
        line = f"{kv[0]}: {kv[1]}"
        if text_width(line) <= 26.0:
            return line
    return ""


def _caption_one(heads: list[dict], caption_url: str, lang: str, firm_fn) -> str | None:
    """'이번 주 한 건' 캡션([마케팅 2026-09-23 계획 L-02, 09-23 개정] 게시 본문 기본형,
    `--caption one`).

    헤드라인 카드([`pick_headline_cards`][pick_headline_cards]) 중 **시사점과 점검을 모두 가진
    첫 카드**를 고른다 — 없으면 시사점만 있는 첫 카드로 점검 칸을 생략하고, 그마저 없으면
    `None`(호출부가 종전 요약형 캡션으로 물러선다). 사실 관계는 카드 JSON 에서 **그대로**
    (LLM 슬롯 0). 줄바꿈은 `wrap_width`(모바일 폭 상한 26, 균형 배분) — 2줄을 넘으면
    `first_sentence` 로 줄인 뒤 다시 감싼다. 불릿("• ")·들여쓰기("  ")도 폭에 넣어야 붙인 뒤에도
    26 안쪽이다. ★'무슨 일' 블록은 감싼 사실 여러 줄 대신 **한 줄 헤더**(`_fact_header`) +
    선택적 한 줄(`_one_extra_fact`)이다 — 긴 지적문을 wrap 하면 문장 중간이 끊긴다는 반려
    피드백을 감싸기가 아니라 구성으로 없앤다."""
    t = STR[lang]
    bullet_w = text_width("• ")

    def item(text: str, max_width: float = 26.0) -> list[str]:
        lines = wrap_width(text, max_width)
        if len(lines) > 2:
            # 2줄에 안 들어오면 먼저 문장을 줄인다(first_sentence) — 그래도 안 들어오면
            # **문장 중간을 자르지 않고** 그대로 낸다(2줄은 목표이지 상한이 아니다).
            lines = wrap_width(first_sentence(text, lang), max_width)
        return lines

    card: dict | None = None
    checks: list[str] = []
    for c in heads:
        impl = _card_text(c, "implication", lang).strip()
        if not impl:
            continue
        if card is None:      # 시사점 있는 첫 카드 — 점검 없는 카드만 만나면 이 폴백을 쓴다
            card = c
        cks = [" ".join(str(x).split()) for x in _card_list(c, "checks", lang) if str(x).strip()]
        if cks:
            card, checks = c, cks[:2]
            break
    if card is None:
        return None

    lines: list[str] = list(item(_card_text(card, "title_issue", lang)))
    lines.append("")
    lines.append(_fact_header(card, lang, firm_fn))
    extra = _one_extra_fact(card, lang)
    if extra:
        lines.append(extra)
    lines += item(first_sentence(_card_text(card, "implication", lang), lang))
    lines.append("")
    if checks:
        lines.append(t["one_checks_head"])
        for chk in checks:
            wrapped = item(chk, 26.0 - bullet_w)
            if not wrapped:
                continue
            lines.append(f"• {wrapped[0]}")
            lines += [f"  {ln}" for ln in wrapped[1:]]
        lines.append("")
    lines.append(t["one_link_head"])
    lines.append(caption_url)
    lines.append("")
    lines.append(t["one_tags"])
    return "\n".join(lines) + "\n"


def build_deck(brief_doc: dict, glossary: list[dict], *, anon: bool = False,
               base_url: str = SITE_BASE_URL, lang: str = "ko",
               caption_style: str = "one") -> dict[str, Any]:
    """브리프 JSON(+용어사전) → {"pub","slides","caption","caption_url","doc_title","url"}. 순수·결정론.

    `lang="en"` 이면 **같은 항목을 영어로** 낸다 — 무엇을 실을지(헤드라인 카드·용어·점검·주제
    집계)는 한국어 정본으로 고르고, 화면에 나가는 글자만 영문 블록(`card["en"]`·`*_en`)에서
    가져온다. 두 덱이 서로 다른 소식을 말하지 않게 하려는 것이다.

    `caption_style`([마케팅 2026-09-23 계획] `"one"` 기본 · `"summary"` 종전형) 은 게시 본문
    (`caption`)만 가른다 — 슬라이드는 어느 쪽이든 동일하다."""
    if lang not in STR:
        raise ValueError(f"지원하지 않는 언어: {lang!r} (가능: {', '.join(LANGS)})")
    if caption_style not in ("one", "summary"):
        raise ValueError(f"지원하지 않는 캡션 형식: {caption_style!r} (가능: one, summary)")
    t = STR[lang]
    brief = brief_doc.get("brief") or {}
    cards = sorted(brief_doc.get("cards") or [], key=lambda c: int(c.get("render_order") or 0))
    pub = str(brief.get("publish_date") or "")
    y, m, wk = title_dateform(pub)
    mon = _EN_MONTH[m] if 1 <= m <= 12 else str(m)
    url = f"{base_url}/briefs/{pub}/" if lang == "ko" else f"{base_url}/en/briefs/{pub}/"

    n_cards = len(cards)
    class1 = [c for c in cards if is_class1_recall(c)]
    wls = [c for c in cards if is_warning_letter(c)]
    mfds_insp = [c for c in cards if is_mfds_inspection(c)]
    mfds_act = [c for c in cards if is_mfds_action(c) and not is_mfds_inspection(c)]
    recalls = [c for c in cards if str(c.get("group") or "") == "Recall"]
    heads = pick_headline_cards(brief, cards, 3)
    if lang != "ko":
        # 영문 블록이 없는 카드는 영문 덱에 실을 수 없다 — 지어내지 않고 뺀다.
        heads = [c for c in heads if has_en(c)]
    # 가명은 화면에 나오는 순서(헤드라인 → 경고서한 → 실사 → 나머지)로 A·B·C
    names = anonymize(heads + wls + mfds_insp + cards) if anon else {}

    def firm(card: dict) -> str:
        tgt = str(card.get("headline_target") or "")
        return _firm_for_lang(names.get(tgt, tgt), lang)

    # ── 01 표지: 통계 타일 4개(카드 수 + 0 이 아닌 축 셋)
    tiles = [(str(n_cards), t["tile_cards"])]
    for cnt, lab in ((len(class1), t["tile_class1"]), (len(wls), t["tile_wl"]),
                     (len(mfds_insp), t["tile_mfds_insp"]), (len(mfds_act), t["tile_mfds_act"]),
                     (len(recalls), t["tile_recall"])):
        if cnt and len(tiles) < 4 and lab not in {x[1] for x in tiles}:
            tiles.append((str(cnt), lab))
    ag_table = AGENCY_LABEL if lang == "ko" else AGENCY_LABEL_EN
    agencies = [ag_table.get(a, a) for a in (brief.get("agencies") or [])]
    ai_note = t["ai_note"]
    slides: list[dict] = [dict(
        kind="cover", eyebrow=t["cover_eyebrow"].format(m=m, wk=wk, mon=mon), h1=list(t["cover_h1"]),
        tiles=tiles, sub=" · ".join(agencies), ft_right=window_label(str(brief.get("window") or "")),
        ai=ai_note)]

    # ── 02~04 헤드라인 카드
    for c in heads:
        rows: list[tuple[str, str]] = []
        for f in _card_list(c, "key_facts", lang):
            kv = parse_fact(f, 12 if lang == "ko" else 26)
            if not kv:
                continue
            label, value = kv
            if label.startswith(("발행", "Published", "Posted")):
                continue
            rows.append((label, value))
            if len(rows) >= 3:
                break
        company = firm(c)
        if lang == "ko":
            rows.append((t["row_company"], company or "—"))
        elif company:   # 이름을 못 쓰는 영문 카드는 줄 자체를 뺀다(빈칸·로마자 날조 대신)
            rows.append((t["row_company"], company))
        impl = first_sentence(_card_text(c, "implication", lang), lang)
        checks = [" ".join(x.split()) for x in _card_list(c, "checks", lang)][:2]
        title = _card_text(c, "title_issue", lang) or (firm(c) if lang == "ko" else "")
        if lang == "ko":
            src = SOURCE_NOTE.get(_agency(c), t["source_other"].format(label=_label(c, lang)))
        else:
            src = t["source_other"].format(label=_label(c, lang))
        slides.append(dict(
            kind="headline", chips=[_label(c, lang), _kind_chip(c, lang)],
            h1=split_two(title), impl_label=t["impl_label"],
            rows=rows, impl=impl, checks=checks,
            ft_right=src, ai=ai_note))

    # ── 05 경고서한 묶음(2건 이상일 때)
    themes = theme_counts(wls, lang)
    if len(wls) >= 2 and themes:
        top = themes[0]
        rows_wl = []
        for c in wls[:6]:
            if lang != "ko" and not has_en(c):
                continue
            name = _firm_short(firm(c), 10.0)
            if not name:      # 이름을 못 쓰면 그 줄을 뺀다
                continue
            chips = [p.strip() for p in re.split(r"[·,]", _card_text(c, "title_issue", lang)) if p.strip()][:2]
            rows_wl.append((name, chips))
        slides.append(dict(
            kind="themes", eyebrow=t["wl_eyebrow"], icon="doc",
            h1=[nfmt(t["wl_h1_a"], len(wls)), t["wl_h1_b"].format(theme=top[1])],
            bars=[(lab, cnt) for lab, _, cnt in themes[:5]], bars_total=nfmt(t["wl_total"], len(wls)),
            bars_head=t["wl_bars_head"],
            rows=rows_wl, ft_right=SOURCE_NOTE["FDA"] if lang == "ko"
            else t["source_other"].format(label="FDA"), ai=ai_note))

    # ── 06 식약처 실사 결과(2곳 이상일 때) — 업체명 목록이 본체라 **영문 덱에서는 뺀다**.
    #     한국 업체명을 로마자로 지어낼 수 없고, 이름 없는 표는 표가 아니다. 건수는 본문에 남는다.
    if len(mfds_insp) >= 2 and lang == "ko":
        rows_mf = []
        for c in mfds_insp[:6]:
            chips = [p.strip() for p in re.split(r"[·,]", str(c.get("title_issue") or "")) if p.strip()][:3]
            rows_mf.append((_firm_short(firm(c), 10.0), chips))
        impl = first_sentence(str(mfds_insp[0].get("implication") or ""))
        slides.append(dict(
            kind="rows", eyebrow=t["mfds_eyebrow"], icon="factory", impl_label=t["impl_label"],
            h1=[nfmt(t["mfds_h1_a"], len(mfds_insp)), t["mfds_h1_b"]], rows=rows_mf, impl=impl,
            ft_right=SOURCE_NOTE["MFDS"], ai=ai_note))

    # ── 07 이번 주 용어
    terms = pick_glossary_terms(glossary, cards, 5, headline_cards=heads)
    if terms:
        gl = []
        for term in terms:   # ★루프 변수는 term — `t` 는 이 함수에서 문구표다
            ko_parts = [p.strip() for p in re.split(r"[·/]", str(term.get("term_ko") or "")) if p.strip()]
            # 병기('무균공정·무균조작')는 이번 주 본문에 더 많이 나온 조각으로, 없으면 첫 조각
            blob = " ".join(_text_of(c) for c in cards)
            ko = max(ko_parts, key=lambda p: (blob.count(p), -ko_parts.index(p))) if ko_parts else str(term.get("term_ko") or "")
            en = str(term.get("term_en") or "")
            acr = re.search(r"\(([A-Z]{2,6})\)", en)
            small = acr.group(1) if acr else re.sub(r"\s*\(.*?\)\s*", " ", en).strip()
            head = ko if lang == "ko" else re.sub(r"\s*\(.*?\)\s*", " ", str(term.get("term_en") or "")).strip()
            easy = " ".join(str(term.get("easy_ko" if lang == "ko" else "easy_en") or "").split())
            if not head or not easy:      # 영문 정의가 없는 용어는 영문 덱에서 뺀다
                continue
            gl.append(dict(ko=head, small=small if lang == "ko" else (acr.group(1) if acr else ""),
                           lines=[easy], mini=mini(str(term.get("id") or ""), lang)))
        num = (_KO_NUM if lang == "ko" else _EN_NUM).get(len(gl), len(gl))
        slides.append(dict(kind="glossary", eyebrow=t["gl_eyebrow"], icon="book",
                           h1=[t["gl_h1"].format(num=num)],
                           terms=gl, cap=t["gl_cap"], ai=ai_note))

    # ── 08 점검 포인트
    checks = pick_checks([heads, wls, mfds_insp], 6, lang=lang)
    if checks:
        slides.append(dict(kind="checks", eyebrow=t["ck_eyebrow"], icon="check",
                           h1=[t["ck_h1"]], checks=checks, cap=t["ck_cap"], ai=ai_note))

    # ── 09 마무리
    slides.append(dict(kind="closing", h1=[nfmt(t["cl_h1_a"], n_cards), t["cl_h1_b"]],
                       body=t["cl_body"],
                       url=url.replace("https://", "").rstrip("/"),
                       note=t["cl_note"],
                       ft_right=t["cl_ft"], ai=ai_note))

    # 페이지 번호
    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)

    # ── 본문 URL — [마케팅 2026-09-23] 슬라이드 URL(위 `url`)은 깨끗하게 두고, 캡션에만 UTM 을
    # 붙인다(`web/utm.py`). RUM 은 쿼리를 안 읽으므로 이 태그의 소비자는 사이트 first-touch
    # 계측뿐 — 슬라이드 안 표시용 URL 을 더럽히지 않는다.
    caption_url = with_utm(url, *LINKEDIN_WEEKLY, linkedin_weekly_campaign(pub))

    # ── 본문 A: 요약형(종전 기본, `--caption summary`) — 한 줄에 한 뜻·모바일 폭 안쪽
    lines = [nfmt(t["cap_head"], len(slides)), ""]
    for c in heads[:2]:
        headline = " ".join(_card_text(c, "title_issue", lang).split())
        if headline or lang == "ko":
            lines.append(f"{headline}.")
    lines.append("")
    if class1:
        lines.append(nfmt(t["cap_class1"], len(class1)))
    if len(wls) >= 2 and themes:
        lines.append(nfmt(t["cap_wl"], len(wls), themes="·".join(x for _, x, _ in themes[:3])))
    mf_bits = []
    if mfds_act:
        mf_bits.append(nfmt(t["cap_mfds_act"], len(mfds_act)))
    if mfds_insp:
        mf_bits.append(nfmt(t["cap_mfds_insp"], len(mfds_insp)))
    if mf_bits:
        # 영문 덱은 식약처 장을 빼므로(업체명) 본문의 이 줄이 유일한 자리다 — 건수로 남긴다
        lines.append(t["cap_mfds"].format(bits=", ".join(mf_bits)))
    tail = []
    if terms:
        tail.append(nfmt(t["cap_terms"], len(terms)))
    if checks:
        tail.append(nfmt(t["cap_checks"], len(checks)))
    if tail:
        lines.append("· " + " · ".join(tail))
    lines += ["", nfmt(t["cap_link"], n_cards), caption_url, "",
              *t["cap_cta"], "", *t["cap_tags"]]
    summary_caption = "\n".join(lines) + "\n"

    # ── 본문 B: '이번 주 한 건'(기본, `--caption one`) — 적합한 카드가 없으면 A 로 물러선다.
    if caption_style == "summary":
        caption = summary_caption
    else:
        caption = _caption_one(heads, caption_url, lang, firm) or summary_caption

    return {"pub": pub, "lang": lang, "slides": slides, "caption": caption,
            "doc_title": t["doc_title"].format(m=m, wk=wk, mon=mon), "url": url,
            "caption_url": caption_url}


# ──────────────────────────────────────────────────────────────────────────────
# 월간 결산 덱 — "이달의 변화 5개 + 숫자 1개" ([마케팅 2026-09-23 계획 L-06])
# ──────────────────────────────────────────────────────────────────────────────
# 매주 덱(위 build_deck)과 같은 규율 — 순수·결정론·LLM 0. 다른 점은 입력 단위뿐이다: 브리프
# 한 호가 아니라 그 달에 발행된 **여러 호**를 묶어, 호마다 헤드라인 1건씩(모자라면 다음
# 헤드라인으로 채움)과 카드 유형 집계 한 장을 낸다.


# 숫자 장의 유형 표시 이름(2026-09-23 검수). `card_type` 값은 수집 채널 이름이라 한 장에
# "Recall" 과 "회수·판매중지" 가 나란히 서면 둘 다 회수인데 무엇이 다른지(어느 기관인지)가 안
# 보인다. 값 자체는 바꾸지 않고 **표시할 때만** 기관을 붙인다. 닫힌 어휘(카드 유형 13종)의
# 표시 이름이지 사실을 옮기는 번역이 아니다 — 영문 덱도 이 표로 한글 유형을 잃지 않는다.
# 표에 없는 유형은 종전 규칙 그대로(국문=원래 값, 영문=한글이 섞였으면 뺀다).
_MONTH_TYPE_LABEL = {
    "ko": {
        "Recall": "회수(FDA)", "Recall(HC)": "회수(캐나다)", "Recall(UK)": "회수(영국)",
        "회수·판매중지": "회수(식약처)", "Warning Letter": "경고서한(FDA)",
        "FDA 483 실사 관찰": "FDA 483", "GMP실사": "GMP 실사(식약처)",
        "행정처분": "행정처분(식약처)", "EU GMP 비준수": "GMP 비준수(EU)",
        "UK GMP 비준수": "GMP 비준수(영국)",
    },
    "en": {
        "Recall": "FDA recall", "Recall(HC)": "Health Canada recall", "Recall(UK)": "MHRA recall",
        "회수·판매중지": "MFDS recall", "Warning Letter": "FDA warning letter",
        "FDA 483 실사 관찰": "FDA Form 483", "GMP실사": "MFDS GMP inspection",
        "행정처분": "MFDS administrative action", "EU GMP 비준수": "EU GMP non-compliance",
        "UK GMP 비준수": "UK GMP non-compliance", "지침·안내서": "Guidance",
        "규제 소식": "Regulatory news", "WHO": "WHO",
    },
}


def _month_card_type_counts(month_briefs: list[dict], lang: str) -> list[tuple[str, int]]:
    """그 달 카드의 `card_type` 값별 건수 — 값은 **그대로**(재라벨 0), 많은 순·동률은 라벨
    오름차순(결정론). ★영문 덱에서는 한글이 섞인 유형('GMP실사'·'회수·판매중지' 등)을
    **뺀다** — 옮기면 지어낸 번역이고 놔두면 '영문 덱에 한글 0' 규율을 깬다. 이름을 못 쓰는
    유형은 이 집계에서 세지 않는다(카드 자체는 다른 장의 총 건수에는 그대로 남는다)."""
    counts: Counter[str] = Counter()
    for b in month_briefs:
        for c in (b.get("cards") or []):
            raw = str(c.get("card_type") or "").strip()
            if not raw:
                continue
            label = _MONTH_TYPE_LABEL.get(lang, {}).get(raw, raw)
            if lang != "ko" and _CJK.search(label):
                continue
            counts[label] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def pick_month_items(month_briefs: list[dict], lang: str, limit: int = 5) -> list[tuple[dict, str, int]]:
    """그 달 헤드라인 항목 — 호마다(오래된 순) 첫 헤드라인 1건, 모자라면(호 수 < limit)
    **최신 호부터 옛 호 순으로** 그 호의 다음 헤드라인을 라운드로빈 채운다(2번째 헤드라인을
    한 바퀴, 그래도 모자라면 3번째). 같은 카드 id·같은 제목은 건너뛴다(중복 0).

    반환은 `(card, publish_date, rank)` — rank 는 그 호 안에서 몇 번째 헤드라인이었는지
    (0=첫 번째)로, 같은 호에서 두 항목이 나오는 드문 경우의 결정론 정렬 키다.
    `month_briefs` 는 이미 그 달로 걸러 오래된 순으로 정렬돼 있다고 가정한다
    (`build_month_deck` 이 그렇게 넘긴다)."""

    def pub_of(b: dict) -> str:
        return str((b.get("brief") or {}).get("publish_date") or "")

    def card_id(c: dict) -> str:
        return str(c.get("id") or c.get("headline_target") or "")

    def card_title(c: dict) -> str:
        return " ".join(str(c.get("title_issue") or "").split())

    per_brief_heads: dict[str, list[dict]] = {}
    for b in month_briefs:
        cards_b = sorted(b.get("cards") or [], key=lambda c: int(c.get("render_order") or 0))
        heads3 = pick_headline_cards(b.get("brief") or {}, cards_b, 3)
        if lang != "ko":
            heads3 = [c for c in heads3 if has_en(c)]
        per_brief_heads[pub_of(b)] = heads3

    # 중복 판정은 **id 나 제목 어느 한쪽만 같아도** 스킵한다(둘 다 같아야 하는 AND 가 아니다) —
    # 같은 사건을 다른 카드 id 로 다시 실었거나(병합 전 잔여), 다른 사건인데 제목 문구가
    # 우연히 겹치는 두 경우 모두 같은 항목을 두 번 보여주는 것으로 친다.
    items: list[tuple[dict, str, int]] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()

    def is_dup(c: dict) -> bool:
        cid, title = card_id(c), card_title(c)
        return (bool(cid) and cid in seen_ids) or (bool(title) and title in seen_titles)

    def mark_seen(c: dict) -> None:
        cid, title = card_id(c), card_title(c)
        if cid:
            seen_ids.add(cid)
        if title:
            seen_titles.add(title)

    for b in month_briefs:
        heads = per_brief_heads[pub_of(b)]
        if heads and not is_dup(heads[0]):
            items.append((heads[0], pub_of(b), 0))
            mark_seen(heads[0])
    if len(items) < limit:
        for rank in (1, 2):
            if len(items) >= limit:
                break
            for b in reversed(month_briefs):
                if len(items) >= limit:
                    break
                heads = per_brief_heads[pub_of(b)]
                if rank < len(heads) and not is_dup(heads[rank]):
                    items.append((heads[rank], pub_of(b), rank))
                    mark_seen(heads[rank])
    items.sort(key=lambda it: (it[1], it[2]))   # 발행일 오름차순, 같은 호는 헤드라인 순위로
    return items[:limit]


def _month_archive_url(briefs: list[dict], base_url: str, lang: str) -> str:
    """월간 덱 마무리 장의 링크. 국문은 늘 아카이브. 영문은 `/en/archive/` 가 **실제로
    존재할 때만** 쓴다 — render.py 의 계약(`brief_has_english()` 인 호가 하나라도 있으면
    그 페이지를 낸다, 4880줄 부근)과 같은 기준을 그 함수로 그대로 물어본다(베끼지 않는다).
    없으면 없는 페이지로 보내지 않고, 카드 단위로라도 영문이 있는 가장 최근 브리프로
    대신한다(둘 다 없으면 영문 항목 자체가 0 이라 이 함수를 부르는 쪽에서 그 언어를 이미
    걸렀을 상황 — `/en/` 최후 폴백은 사실상 도달하지 않는다)."""
    if lang == "ko":
        return f"{base_url}/archive/"
    full_en_pubs = sorted(str((b.get("brief") or {}).get("publish_date") or "")
                          for b in briefs if render.brief_has_english(b))
    if full_en_pubs:
        return f"{base_url}/en/archive/"
    partial_en_pubs = sorted(str((b.get("brief") or {}).get("publish_date") or "")
                             for b in briefs
                             if any(has_en(c) for c in (b.get("cards") or [])))
    if partial_en_pubs:
        return f"{base_url}/en/briefs/{partial_en_pubs[-1]}/"
    return f"{base_url}/en/"


def build_month_deck(briefs: list[dict], glossary: list[dict], month: str, *, anon: bool = False,
                     base_url: str = SITE_BASE_URL, lang: str = "ko") -> dict[str, Any]:
    """달력 월(`month`, `"YYYY-MM"`) 안에 발행된 브리프 전부 → 월간 결산 덱. 순수·결정론.

    `briefs` 는 **그 달로 미리 거르지 않은** 전체 브리프 문서 목록이다 — 이 함수가 안에서
    `publish_date` 접두사로 거른다(호출부가 CLI 든 테스트든 그냥 로드한 전부를 넘기면 된다).
    영문 아카이브 존재 여부 판정(`_month_archive_url`)도 **그 달 밖의** 호를 봐야 하므로
    같은 이유로 전체 목록이 필요하다.

    `glossary` 는 현재 슬라이드에 쓰지 않지만(월간 덱은 용어 장이 없다) 매주 덱과 같은
    서명을 유지해 호출부가 분기하지 않게 한다."""
    if lang not in STR:
        raise ValueError(f"지원하지 않는 언어: {lang!r} (가능: {', '.join(LANGS)})")
    if not re.match(r"^\d{4}-\d{2}$", month or ""):
        raise ValueError(f"월 형식 오류: {month!r} (예: '2026-09')")
    t = STR[lang]

    month_briefs = sorted(
        (b for b in briefs if str((b.get("brief") or {}).get("publish_date") or "").startswith(month + "-")),
        key=lambda b: str((b.get("brief") or {}).get("publish_date") or ""))
    if not month_briefs:
        raise ValueError(f"발행본 없음: {month!r}")

    y, m = int(month[:4]), int(month[5:7])
    mon = _EN_MONTH[m] if 1 <= m <= 12 else str(m)
    k_issues = len(month_briefs)
    n_cards_month = sum(len(b.get("cards") or []) for b in month_briefs)

    items = pick_month_items(month_briefs, lang, 5)
    n = len(items)

    names = anonymize([c for c, _, _ in items]) if anon else {}

    def firm(card: dict) -> str:
        tgt = str(card.get("headline_target") or "")
        return _firm_for_lang(names.get(tgt, tgt), lang)

    archive_url = _month_archive_url(briefs, base_url, lang)
    ai_note = t["ai_note"]

    # ── 01 표지 — eyebrow + 제목 + '{k}호 · 카드 {c}장' 한 줄. 통계 타일은 두지 않는다
    # (그 자리는 (n+2) 숫자 장이 맡는다 — 표지에서 또 세면 같은 수를 두 번 보여준다).
    cover_h1 = nfmt(t["month_h1"], n, m=m, mon=mon)
    meta_line = t["month_meta"].format(k=k_issues, c=n_cards_month,
                                       ks=plural_s(k_issues), cs=plural_s(n_cards_month))
    slides: list[dict] = [dict(
        kind="cover", eyebrow=t["month_eyebrow"], h1=cover_h1.split("|"),
        tiles=[], sub=meta_line, ft_right="", ai=ai_note)]

    # ── 02..n+1 항목 — 매주 덱의 'headline' 장을 그대로 재사용(사실 표·시사점·점검 칩 동형),
    # 그 호를 밝히는 작은 날짜 칩만 더한다(연도는 뺀다 — 표지가 이미 그 달을 말한다).
    for card, pub, _rank in items:
        rows: list[tuple[str, str]] = []
        for f in _card_list(card, "key_facts", lang):
            kv = parse_fact(f, 12 if lang == "ko" else 26)
            if not kv:
                continue
            label, value = kv
            if label.startswith(("발행", "Published", "Posted")):
                continue
            rows.append((label, value))
            if len(rows) >= 3:
                break
        company = firm(card)
        if lang == "ko":
            rows.append((t["row_company"], company or "—"))
        elif company:
            rows.append((t["row_company"], company))
        impl = first_sentence(_card_text(card, "implication", lang), lang)
        checks = [" ".join(x.split()) for x in _card_list(card, "checks", lang)][:2]
        title = _card_text(card, "title_issue", lang) or (firm(card) if lang == "ko" else "")
        if lang == "ko":
            src = SOURCE_NOTE.get(_agency(card), t["source_other"].format(label=_label(card, lang)))
        else:
            src = t["source_other"].format(label=_label(card, lang))
        m_d = re.match(r"^\d{4}-(\d{2})-(\d{2})$", pub)
        date_chip = f"{int(m_d.group(1))}/{int(m_d.group(2))}" if m_d else pub
        slides.append(dict(
            kind="headline", chips=[c for c in (_label(card, lang), _kind_chip(card, lang), date_chip) if c],
            h1=split_two(title), impl_label=t["impl_label"],
            rows=rows, impl=impl, checks=checks,
            ft_right=src, ai=ai_note))

    # ── n+2 숫자 1개 — 그 달 카드를 유형별로 세어 상위 4개만 막대로.
    type_counts = _month_card_type_counts(month_briefs, lang)[:4]
    slides.append(dict(
        kind="numbers", eyebrow=t["month_num_eyebrow"], h1=[t["month_num_h1"]],
        bars=type_counts, bars_head=t["month_num_bars_head"],
        bars_total=t["month_num_bars_total"].format(c=n_cards_month, s=plural_s(n_cards_month)),
        cap=t["month_num_cap"].format(m=m, mon=mon, c=n_cards_month, s=plural_s(n_cards_month)),
        ai=ai_note))

    # ── 마지막 마무리 — 매주 덱과 같은 장이지만 URL 은 이 달 링크가 아니라 아카이브.
    slides.append(dict(kind="closing", h1=[nfmt(t["cl_h1_a"], n_cards_month), t["cl_h1_b"]],
                       body=t["cl_body"],
                       url=archive_url.replace("https://", "").rstrip("/"),
                       note=t["cl_note"],
                       ft_right=t["cl_ft"], ai=ai_note))

    for i, s in enumerate(slides, 1):
        s["idx"], s["total"] = i, len(slides)

    caption_url = with_utm(archive_url, "linkedin", "social", f"monthly_{month}")

    # ── 게시 본문 — '이번 주 한 건'과 같은 결(항목마다 한 줄+들여쓰기 이어짐), 항목이 여럿이라
    # 카드 하나 전체가 아니라 제목 한 줄(첫 문장)만 싣는다.
    bullet_w = text_width("· ")
    lines: list[str] = [nfmt(t["month_cap_head"], n, m=m, mon=mon), ""]
    for card, _pub, _rank in items:
        title = first_sentence(_card_text(card, "title_issue", lang), lang)
        wrapped = wrap_width(title, 26.0 - bullet_w)
        if not wrapped:
            continue
        lines.append(f"· {wrapped[0]}")
        lines += [f"  {ln}" for ln in wrapped[1:]]
    lines.append("")
    lines.append(t["month_count"].format(c=n_cards_month, k=k_issues,
                                         cs=plural_s(n_cards_month), ks=plural_s(k_issues)))
    lines.append("")
    lines.append(t["month_link_head"])
    lines.append(caption_url)
    lines.append("")
    lines.append(t["one_tags"])
    caption = "\n".join(lines) + "\n"

    return {"pub": month, "lang": lang, "slides": slides, "caption": caption,
            "doc_title": t["month_doc_title"].format(m=m, mon=mon), "url": archive_url,
            "caption_url": caption_url}


# ──────────────────────────────────────────────────────────────────────────────
# HTML
# ──────────────────────────────────────────────────────────────────────────────

# `.lang-en` 규칙: 영문은 같은 사실을 쓰는 데 줄이 더 들어 국문 기준 상한이면 숫자 한가운데서
# 잘린다("about 5…"). 용어 정의는 "그대로 가져왔다"고 적어 두고 끊기면 말이 안 되니 넉넉히 준다.
# ★2026-09-22 4→5줄: 4줄에서 `business-operator-recall` 이 문장 한가운데서 끊겼다
# ("…without waiting for an order from the…"). 브라우저 실측으로 확인한 사실 둘 —
#   ⓐ 정의 242개 중 4줄을 넘는 것은 2개뿐이다(business-operator-recall·reconciliation).
#      국문(3줄 상한)은 넘는 것이 0개다. 영문만의 문제다.
#   ⓑ 5줄로 올려도 **최악의 경우(다섯 줄 전부 5줄)에 60px 남기고 들어간다** — 슬라이드가
#      `overflow:hidden` 이라 넘치면 조용히 잘리는데, 넘치지 않는다는 것을 재서 확인했다.
# ★스타일시트 안에는 한글 주석을 넣지 않는다 — 영문 덱 HTML 에 그대로
# 실려 "영문에 한글 0" 가드를 속인다.
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
.lang-en .kv dd{-webkit-line-clamp:3}
.lang-en .kv dt{max-width:260px}
.lang-en .gl .g p{-webkit-line-clamp:5}
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


def _bars_block(s: dict) -> str:
    """막대 목록 하나(`.mock`) — 경고서한 '겹치는 지적'(themes)과 월간 덱 '숫자 1개'
    (numbers) 두 장이 같은 모양을 쓴다([마케팅 2026-09-23 L-06] 중복 제거). `bars_total`
    은 선택([마케팅 2026-09-23 이전] themes 는 늘 채워 왔으므로 그쪽 출력은 바이트 불변)."""
    mx = max((c for _, c in s["bars"]), default=1)
    bars = "".join(
        f'<div class="br"><span>{_e(l)}</span><span class="bar{" top" if i == 0 else ""}"><i style="width:{int(100 * c / mx)}%"></i></span><span class="v">{c}</span></div>'
        for i, (l, c) in enumerate(s["bars"]))
    head = _e(s.get("bars_head") or "겹치는 지적")
    total = f'<span>{_e(s["bars_total"])}</span>' if s.get("bars_total") else ""
    return f'<div class="mock"><div class="mh"><b>{head}</b>{total}</div><div class="bars">{bars}</div></div>'


def _slide_html(s: dict) -> str:
    kind = s["kind"]
    cls = {"cover": " coral", "closing": " dark"}.get(kind, "")
    parts: list[str] = []
    if kind == "cover":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 96))
        if s.get("tiles"):
            parts.append('<div class="stats">' + "".join(
                f'<div class="stat"><b>{_e(n)}</b><span>{_e(l)}</span></div>' for n, l in s["tiles"]) + "</div>")
        if s.get("sub"):
            parts.append(f'<p class="sub">{_e(s["sub"])}</p>')
    elif kind == "headline":
        parts.append('<div class="chips">' + "".join(
            f'<span class="chip{" hi" if i else ""}">{_e(c)}</span>' for i, c in enumerate(s["chips"]) if c) + "</div>")
        parts.append(_h1(s["h1"], 78))
        kv = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in s["rows"])
        impl = (f'<div class="impl"><b>{_e(s.get("impl_label") or "시사점")}</b>'
                f'{_e(s["impl"])}</div>') if s.get("impl") else ""
        cks = ('<div class="cks">' + "".join(f"<span>{_e(c)}</span>" for c in s["checks"]) + "</div>") if s.get("checks") else ""
        parts.append(f'<div class="mock"><dl class="kv">{kv}</dl>{impl}{cks}</div>')
    elif kind == "themes":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 78))
        parts.append(_bars_block(s))
        rows = "".join(f'<div class="frow"><b>{_e(f)}</b><span class="fl">' + "".join(
            f'<span{"" if j == 0 else " class=\"g\""}>{_e(ch)}</span>' for j, ch in enumerate(chips)) + "</span></div>"
            for f, chips in s["rows"])
        parts.append(f'<div class="mock small">{rows}</div>')
    elif kind == "numbers":
        # [마케팅 2026-09-23 L-06] 월간 덱 '숫자 1개' 장 — 카드 유형별 상위 4개를 막대로.
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 78))
        parts.append(_bars_block(s))
        parts.append(f'<p class="cap">{_e(s["cap"])}</p>')
    elif kind == "rows":
        parts.append(_eyebrow(s))
        parts.append(_h1(s["h1"], 78))
        rows = "".join(f'<div class="frow"><b>{_e(f)}</b><span class="fl">' + "".join(
            f"<span>{_e(ch)}</span>" for ch in chips) + "</span></div>" for f, chips in s["rows"])
        parts.append(f'<div class="mock">{rows}</div>')
        if s.get("impl"):
            parts.append(f'<div class="impl"><b>{_e(s.get("impl_label") or "시사점")}</b>'
                         f'{_e(s["impl"])}</div>')
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
    lang = str(deck.get("lang") or "ko")
    return (f'<!doctype html><html lang="{_e(lang)}"><head><meta charset="utf-8">'
            f'<title>{_e(deck["doc_title"])}</title>'
            f'{FONT_LINKS if font_links else ""}<style>{css}</style></head>'
            f'<body class="lang-{_e(lang)}">{body}</body></html>')


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


def load_all_briefs(data_dir: Path) -> list[dict]:
    """[마케팅 2026-09-23 L-06] `--months auto`·월간 덱이 필요로 하는 전체 브리프 목록
    (파일명 = 발행일 오름차순). 파일 하나가 깨져 있어도(JSON 파싱 실패) 그 한 파일만
    건너뛴다 — 한 파일 사고로 나머지 달까지 못 내지 않는다."""
    docs: list[dict] = []
    for p in sorted(Path(data_dir).glob("brief_web_*.json")):
        try:
            docs.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"::warning::{p.name} 파싱 실패({type(exc).__name__}) — 건너뜀", file=sys.stderr)
    return docs


def auto_months(briefs: list[dict]) -> list[str]:
    """`--months auto` → [최신 호가 속한 달의 **바로 전 달력 월**, 최신 호가 속한 달] —
    둘 다 데이터(최신 발행일)에서만 결정론 파생된다. 전 달에 실제 발행본이 없어도(연초·
    서비스 첫 달 등) 여기서는 걸러내지 않는다 — 호출부(main)가 그 달 발행본 유무를 따로
    확인해 없으면 경고 후 건너뛴다."""
    pubs = sorted(p for p in (str((b.get("brief") or {}).get("publish_date") or "") for b in briefs) if p)
    if not pubs:
        return []
    latest = pubs[-1]
    y, m = int(latest[:4]), int(latest[5:7])
    cur = f"{y:04d}-{m:02d}"
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    prev = f"{py:04d}-{pm:02d}"
    return [prev, cur]


def _emit_deck(deck: dict, stem: str, out_dir: Path, chrome: str | None, keep_html: bool) -> None:
    """덱 하나를 txt(+html)로 쓰고, Chrome 이 있으면 pdf 도 낸다 — 매주 덱·월간 덱
    ([마케팅 2026-09-23 L-06]) 공용(산출 로직 한 벌). PDF 실패는 비차단(배포는 카드
    없이도 진행)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.txt").write_text(deck["caption"], encoding="utf-8")
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(render_html(deck), encoding="utf-8")
    print(f"{stem}: {deck['pub']} · {len(deck['slides'])}장 · 본문 {len(deck['caption'])}자 → {out_dir}")
    if chrome:
        try:
            render_pdf(html_path, out_dir / f"{stem}.pdf", chrome)
            print(f"{stem}.pdf {(out_dir / f'{stem}.pdf').stat().st_size:,} bytes")
        except Exception as exc:  # 비차단 — 배포는 카드 없이도 진행한다
            print(f"::warning::{stem}.pdf 렌더 실패({type(exc).__name__}) — html/txt 만 출력",
                  file=sys.stderr)
    if not keep_html:
        html_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(cp949) 방어 — 저장소 관용구(tests/test_cli_stdout_encoding.py). '·'·'→' 는 되지만
    # '—' 같은 문자가 산출 로그를 통째로 날린다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="링크드인 카드뉴스(PDF+본문) 생성")
    ap.add_argument("--data", default=str(WEB_DIR / "data" / "briefs"), help="브리프 JSON 디렉터리")
    ap.add_argument("--brief", help="특정 브리프 JSON 파일(미지정 시 최신)")
    ap.add_argument("--glossary", default=str(WEB_DIR / "data" / "glossary.json"))
    ap.add_argument("--out", required=True, help="출력 루트(dist) — briefs/{pub}/linkedin.{pdf,txt} 를 낸다")
    ap.add_argument("--anon", action="store_true", help="업체명을 가명(업체 A/B/C)으로")
    ap.add_argument("--html", action="store_true", help="linkedin.html 도 출력에 남긴다")
    ap.add_argument("--no-pdf", action="store_true", help="Chrome 렌더를 건너뛴다(html/txt 만)")
    ap.add_argument("--lang", default=",".join(LANGS),
                    help=f"낼 언어(쉼표) — 기본 {','.join(LANGS)}. ko=linkedin.*, en=linkedin_en.*")
    ap.add_argument("--caption", choices=("one", "summary"), default="one",
                    help="게시 본문 형식 — 기본 one('이번 주 한 건', 마케팅 2026-09-23 계획). "
                         "summary 는 종전 요약형. 파일 이름(linkedin.txt 등)은 그대로다.")
    ap.add_argument("--month", help="YYYY-MM — 그 달의 월간 결산 덱만 낸다(마케팅 2026-09-23 L-06, 주간 대신)")
    ap.add_argument("--months", choices=("auto",),
                    help="'auto' — 최신 호가 속한 달 + 그 전 달, 둘 다 월간 결산 덱(주간 대신)")
    args = ap.parse_args(argv)

    glossary = json.loads(Path(args.glossary).read_text(encoding="utf-8")) if Path(args.glossary).exists() else []
    langs = [x.strip() for x in str(args.lang).split(",") if x.strip()]
    unknown = [x for x in langs if x not in STR]
    if unknown:
        print(f"::warning::모르는 언어 {unknown} — 무시", file=sys.stderr)
        langs = [x for x in langs if x in STR]
    chrome = None if args.no_pdf else find_chrome()
    if not args.no_pdf and not chrome:
        print("::warning::Chrome 미발견 — linkedin.pdf 건너뜀(html/txt 만 출력)", file=sys.stderr)

    # ── 월간 결산 덱([마케팅 2026-09-23 L-06]) — --month/--months 가 있으면 주간 대신 이것만
    # 낸다(둘 다 없으면 아래 종전 주간 경로 — "neither flag" 는 바이트 불변).
    if args.month or args.months:
        all_briefs = load_all_briefs(Path(args.data))
        if not all_briefs:
            print("::warning::브리프 JSON 없음 — 월간 덱 생성 건너뜀", file=sys.stderr)
            return 0
        months: list[str] = []
        if args.month:
            months.append(args.month)
        if args.months == "auto":
            for mo in auto_months(all_briefs):
                if mo not in months:
                    months.append(mo)
        for month in months:
            if not any(str((b.get("brief") or {}).get("publish_date") or "").startswith(month + "-")
                      for b in all_briefs):
                print(f"::warning::{month} 건너뜀 — 그 달 발행본 없음", file=sys.stderr)
                continue
            for lang in langs:
                stem = "linkedin" if lang == "ko" else f"linkedin_{lang}"
                try:
                    deck = build_month_deck(all_briefs, glossary, month, anon=args.anon, lang=lang)
                except ValueError as exc:
                    print(f"::warning::{month}/{lang} 월간 덱 생성 실패({exc}) — 건너뜀", file=sys.stderr)
                    continue
                # 영문 항목이 0 인 달(카드에 en 블록이 없음)은 조용히 내보내지 않는다.
                if not any(s["kind"] == "headline" for s in deck["slides"]):
                    print(f"::warning::{stem} 건너뜀 — {month} 에 실을 항목이 0({lang} 본문 없음)",
                          file=sys.stderr)
                    continue
                _emit_deck(deck, stem, Path(args.out) / "monthly" / month, chrome, args.html)
        return 0

    brief_path = Path(args.brief) if args.brief else latest_brief_path(Path(args.data))
    if not brief_path or not brief_path.exists():
        print("::warning::브리프 JSON 없음 — 카드 생성 건너뜀", file=sys.stderr)
        return 0
    brief_doc = json.loads(brief_path.read_text(encoding="utf-8"))

    for lang in langs:
        # 파일 이름: 한국어는 기존 그대로(linkedin.*), 영어는 접미(linkedin_en.*).
        # 기존 링크·운영 루틴을 건드리지 않으려고 가산만 한다.
        stem = "linkedin" if lang == "ko" else f"linkedin_{lang}"
        deck = build_deck(brief_doc, glossary, anon=args.anon, lang=lang, caption_style=args.caption)
        # 영문 블록이 없는 옛 브리프는 소식 장이 거의 없는 껍데기가 된다 — 조용히 내보내지 않고
        # 경고 후 그 언어만 건너뛴다(빈 덱이 배포되면 아무도 모른다).
        if not any(s["kind"] == "headline" for s in deck["slides"]):
            print(f"::warning::{stem} 건너뜀 — 실을 소식 장이 0(브리프 카드에 {lang} 본문 없음)",
                  file=sys.stderr)
            continue
        _emit_deck(deck, stem, Path(args.out) / "briefs" / deck["pub"], chrome, args.html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
