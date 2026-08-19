#!/usr/bin/env python3
"""GRM 웹 렌더러 (P2·P4) — `grm-web-card/v1` JSON → 정적 멀티페이지 사이트.

순수·결정론 빌더. `web/data/briefs/*.json`(주차별 브리프)을 읽어 `dist/` 에
랜딩(`index.html`)·아카이브(`archive/index.html`)·브리프 상세
(`briefs/{slug}/index.html`)를 생성한다. 디자인 계약 = `GRM_웹_프로토타입_v4.html`
+ 검색/네비/모션 = `GRM_웹_P4_아카이브검색_프로토타입_v2.html`
(CSS 는 `assets/grm.css` 로 동결 추출).

불변식
  1. 순수 렌더 — 사실/URL/숫자/업체명 무변형(JSON 값 그대로). 렌더러가 보유하는
     텍스트는 디자인 정적 카피(템플릿)와 면책 캐논 문안(brief.html)뿐.
  2. 결정론 — 같은 입력 JSON → 바이트 동일 HTML. `datetime.now`/난수 0,
     정렬은 입력에서만 파생, autoescape on, 출력은 항상 LF/UTF-8.
  3. 정적·$0 — 외부 fetch 0, 런타임 서버 0.
  4. 멀티페이지 — 라우트별 개별 HTML. 링크는 페이지 깊이별 상대경로(호스트 무관).

스키마 한계 2건(§1.a/§1.b)은 결정론 파생으로 처리(v1.1 후보):
  - issue 번호: data/briefs 의 publish_date 오름차순 순위(가장 오래된=1).
  - 브리프 제목: tldr[0] 있으면 사용, 없으면 publish_date 파생 "{Y}년 {M}월 {N}주차".

P4 — 아카이브 교차검색(정적·클라이언트사이드):
  - `dist/assets/search-index.json` 을 빌드시 결정론 파생(카드 1개=1엔트리 + facet
    메타 + 호 메타). 사실/URL/제목 재생성 0 — 카드 기존 값만 담는다(무변형).
  - 검색·필터는 `assets/archive.js`(정적 클라이언트사이드)가 이 인덱스로 동작.
    JS 미로드/fetch 실패 시 서버사이드로 이미 렌더된 호 목록이 그대로 보임(graceful).
  - 상세 카드 앵커는 `document_id`(=card.id) 기준(검색결과→카드 점프 안정화). 인덱스의
    href 와 상세 article id 는 같은 `_card_anchor()` 로 파생 — 항상 일치.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from xml.sax.saxutils import escape as _x

from markupsafe import Markup, escape as _escape

# ── 경로(이 파일 기준 — cwd 무관) ──────────────────────────────────────────────
WEB_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEB_DIR.parent                 # grm_findings.py 등 저장소 루트 모듈
TEMPLATES_DIR = WEB_DIR / "templates"
PARTIALS_PARENT = WEB_DIR                  # "partials/card.html" 해석용
DATA_DIR = WEB_DIR / "data" / "briefs"
LIBRARY_DIR = WEB_DIR / "data" / "library"      # [자료실] ICH/MFDS 참조 카탈로그 커밋 데이터
LIBRARY_UPDATES_FILE = WEB_DIR / "data" / "library_updates.json"  # [자료실] 주간 자동 갱신 변경 이력
GUIDE_FILE = WEB_DIR / "data" / "guide_content.md"   # [이용안내] 본문 마크다운(정본)
GLOSSARY_FILE = WEB_DIR / "data" / "glossary.json"   # [용어사전] GMP/규제 용어 커밋 데이터
GLOSSARY_CASES_FILE = WEB_DIR / "data" / "glossary_cases.json"  # [용어사전→사례] 용어별 findings 검색 건수 커밋 데이터
FINDINGS_FACETS_FILE = WEB_DIR / "data" / "findings_facets.json"  # [검색 유입] 분류·국가·기관 모음 페이지 정본(findings_facets_refresh.py)
FINDINGS_DOCS_FILE = WEB_DIR / "data" / "findings_docs.json"      # [검색 유입] 문서 단위 페이지 정본(findings_docs_refresh.py · 지적 3건 이상)
QUIZ_FILE = WEB_DIR / "data" / "quiz_bank.json"      # [주간 퀴즈] 정본 문항 뱅크(커밋 데이터)
ASSETS_DIR = WEB_DIR / "assets"
DIST_DIR = WEB_DIR / "dist"

# [브리프→업체 프로파일 브릿지] normalize_firm_name() 은 grm_findings.py 의 파이썬
# 정본(013_findings_firm_key.sql 의 SQL 복제본과 파리티가 유일한 계약)을 그대로
# import 한다 — web/tests/test_render.py 가 이미 동일 sys.path 트릭(REPO_ROOT 삽입)
# 으로 grm_findings 를 import 하고 있어(카테고리 라벨 동기화 대조용) 이 실행 컨텍스트
# (`python web/render.py ...`, repo 루트에서 실행)에서도 구조적으로 문제 없음을 확인—
# render.py 는 스크립트로 직접 실행되므로 sys.path[0] 이 web/ 디렉터리라 REPO_ROOT 를
# 명시적으로 추가해야 한다(cwd 의존 없이 __file__ 기준 — 워크플로/로컬 어디서 실행해도
# 동일). 순수 함수 재사용일 뿐 네트워크·부작용 없음(grm_findings 모듈 최상위는 상수/
# 함수 정의만 — 010 계열 검증됨).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from grm_findings import normalize_firm_name as _normalize_firm_name  # noqa: E402

# ── v4 디자인 계약에서 가져온 결정론 매핑 ──────────────────────────────────────
# 사실표에서 mono(ASCII 데이터)로 표기하는 라벨(v4 dataLabels 동결). 한글에 mono 금지.
MONO_LABELS = {"발행일", "문서번호", "실사일", "Class", "회수 등급"}
SIG_COLOR = {"High": "var(--hi)", "Med": "var(--med)", "Low": "var(--lo)"}
SECTION_ICON = {"글로벌": "ti-world", "국내": "ti-map-pin", "Recall": "ti-alert-triangle"}
_SECTION_ICON_DEFAULT = "ti-folder"
MARKS = "①②③④⑤"

# ── [브리프→업체 프로파일 브릿지] 카드 facts → firm_key 스탬프 ────────────────
# card_scaffold.py _w2_extra_*() 실측 기준 업체명을 담는 fact 라벨 4종(카드 유형별
# 배선): WL="업체/제조소", FDA 483="제조소/업체", GMP 정기실태조사="제조소",
# 그 외(행정처분/회수(질)/GMP 인증서/openFDA 회수/HC 회수)="업체". 그 외 유형
# (guidance/FR·rss-news·mfds-notice·safety-letter·legislative·regulation·WHO)은
# 업체 개념 자체가 없는 문서라 매칭 없음 — 정상(링크가 성립하지 않을 뿐).
_FIRM_FACT_LABELS = frozenset({"업체", "제조소", "제조소/업체", "업체/제조소"})
# fact 값 접미사 구분자 — 이 지점 이전까지만 업체명으로 취급한다. 카드유형별로 서로
# 다른 접미사를 붙인다: 행정처분=" (KR)" 국가코드(공백+괄호), FDA 483=" · FEI 12345"
# 식별자(공백+가운뎃점). 한글 법인 표기(예: "경방신약(주)")는 괄호가 공백 없이 바로
# 붙어 있어 오탐하지 않는다 — 013 정규화(normalize_firm_name)가 처리하는 법인접미사/
# 구두점 규칙과는 별개 계층(이 절단은 그 앞단 "카드 표시값 → 순수 업체명" 전처리다).
_FIRM_VALUE_SEPS = (" (", " · ")
_FIRM_PLACEHOLDER = "원문 미기재"


def _firm_key_for_card(card: dict[str, Any]) -> str:
    """카드 facts → firm_key(013 grm_normalize_firm_name 파리티, grm_findings.py 정본
    import). 라벨이 매칭되는 첫 fact 1개만 확인한다 — 그 fact 의 값이 비어있거나
    placeholder("원문 미기재")면 다른 fact 로 넘어가지 않고 바로 실패(빈 문자열)
    처리한다. 실패 시 card.html 이 data-firm-key 속성 자체를 생략한다.

    순수 함수(로컬 카드 JSON 값만 참조, 네트워크 0) — 빌드 결정론(골든) 계약 유지."""
    for f in (card.get("facts") or []):
        if f.get("label", "") not in _FIRM_FACT_LABELS:
            continue
        value = str(f.get("value") or "")
        cut = len(value)
        for sep in _FIRM_VALUE_SEPS:
            idx = value.find(sep)
            if idx != -1 and idx < cut:
                cut = idx
        name = value[:cut].strip()
        if not name or name == _FIRM_PLACEHOLDER:
            return ""
        return _normalize_firm_name(name)
    return ""


# ── 날짜 파생(결정론) ──────────────────────────────────────────────────────────
def _date_parts(date_str: str) -> tuple[int, int, int]:
    y, m, d = (int(x) for x in date_str.split("-"))
    return y, m, d


def title_dateform(publish_date: str) -> str:
    """publish_date → "{Y}년 {M}월 {N}주차". 주차 = (day-1)//7 + 1 (결정론)."""
    y, m, d = _date_parts(publish_date)
    week = (d - 1) // 7 + 1
    return f"{y}년 {m}월 {week}주차"


def _date_dotted(publish_date: str) -> str:
    """"2026-06-22" → "2026 · 06 · 22" (표지 .ch 라벨)."""
    return " · ".join(publish_date.split("-"))


# href 에 들어갈 수 있는 안전 스킴 화이트리스트(방어선). autoescape 는 속성 탈출만 막고
# 스킴(javascript:·data:·vbscript:)은 못 막으므로, 렌더러가 마지막 게이트로 거른다.
# 실데이터 URL 은 전부 http(s) 라 출력 byte-동일(무변형); 비허용 스킴만 ""→링크 생략.
_SAFE_URL_PREFIXES = ("https://", "http://", "/", "#")


def _safe_url(u: str) -> str:
    return u if (u or "").strip().lower().startswith(_SAFE_URL_PREFIXES) else ""


def _brief_title(brief_meta: dict[str, Any]) -> str:
    """아카이브/표지 제목 = tldr[0] 있으면 사용, 없으면 날짜 파생(§1.b)."""
    tldr = brief_meta.get("tldr") or []
    if tldr and tldr[0]:
        return tldr[0]
    return title_dateform(brief_meta.get("publish_date", ""))


def _card_anchor(card: dict[str, Any]) -> str:
    """상세 카드의 안정 앵커 = document_id(=card.id). 검색결과→카드 점프용(P4 §2.2).

    상세 article id·TOC href(brief.html)와 search-index 의 href 가 **모두** 이 함수로
    파생 → 항상 일치(드리프트 0). id 없는 적대/합성 입력은 render_order 폴백.
    """
    cid = str(card.get("id") or "").strip()
    return cid if cid else f"c{card.get('render_order')}"


# ── [소스확장 2026-07-02] 상세보기 접힘 미리보기 태그(결정론 파생 — 사실 재작성 0) ──────
def _deep_preview(da: dict[str, Any] | None) -> str:
    """분석층(deep) 접힘 summary 에 붙는 내용 힌트 — 펼치기 전에 무엇이 들었는지 스캔용.
    유형별 ②섹션명으로 구분: admin=처분근거(disposition_basis)·483=실사의미
    (inspectional_significance)·WL=대응조치(기본). 결정론(값 재생성 0)."""
    if not isinstance(da, dict):
        return ""
    kv = da.get("key_violations")
    n = len(kv) if isinstance(kv, list) else 0
    if da.get("disposition_basis"):
        mid = "처분근거"
    elif da.get("inspectional_significance"):
        mid = "실사의미"
    else:
        mid = "대응조치"
    parts = ([f"위반 {n}건"] if n else []) + [mid, "행정리스크"]
    return " · ".join(parts)


def _detail_preview(dd: dict[str, Any] | None) -> str:
    """결정론 상세(deterministic_detail) 접힘 summary 힌트. fda_483_observations 는 Observation
    건수. gmp_deficiencies 는 card.html 이 자체 '· N건' 힌트를 쓰므로 빈 문자열."""
    if not isinstance(dd, dict):
        return ""
    if dd.get("type") == "fda_483_observations":
        return f"Observation {dd.get('count') or 0}건"
    return ""


_INSPECTOR_NAMES_LIMIT = 6


def _sanitize_inspector_names(value: Any) -> list[str]:
    """[실사관 표기 2026-07-30] FDA 483 카드 `deterministic_detail.inspectors` 방어적 정제.

    `/findings/` 검색 화면(`web/assets/findings.js` sanitizeInspectorNames())과 동일 규칙을
    복제한다 — 별도 정적 자산·언어(JS vs Python)라 코드 공유는 불가능하지만 계약(리스트가
    아니면 무시·비문자열/공백 원소 제거·strip·6개 절단)은 반드시 같아야 한다. 카드 JSON 이
    상류(card_scaffold/수집기) 버전 표류나 수기 편집으로 무엇을 담고 있어도 이 함수는
    예외를 던지지 않는다 — 순수 방어 계층."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if len(out) >= _INSPECTOR_NAMES_LIMIT:
            break
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


# ── [상세 본문 가독성 2026-07-27] 통짜 문단 → 목록 복원 ──────────────────────
# EU/UK GMP 비준수(NCR) 상세는 1,000~2,300자가 **줄바꿈 0개**로 한 덩어리다(실측:
# Technophage additional 2,282자·nature 1,902자). 그런데 원문에는 구조가 **이미 있다** —
# `•` 불릿, `A.`/`1.`/`a.` 열거, GMP 운영항목의 계층 코드(`1.1.1.4`). 지금은 그 마커가
# 글자로만 남아 한 줄로 이어져 읽을 수가 없다.
#
# 여기서 하는 일은 **표현층 분해뿐**이다 — 데이터(JSON)는 verbatim 그대로 두고, 렌더가
# 원문에 실재하는 마커에서만 끊는다. 마커가 없으면 끊지 않는다(구조를 지어내지 않는다).
# 영문 원문과 국문 번역에 같은 함수를 쓰므로 두 열의 항목 수가 어긋나지 않는다.
# `•` 는 명백한 불릿이지만 `·`(가운뎃점)는 아니다 — **한국어에서 `·` 는 낱말을 잇는 정상
# 문장부호**다("책임·권한·상호관계", "제품·서비스"). 종전 규칙(`\s*[•·]\s*`)은 공백 없는
# 가운뎃점까지 끊어서 국문 한 문장을 세 항목으로 찢었다(2026-07-27 라이브 실측). 그래서
# `·` 는 **앞뒤가 공백일 때만** 불릿으로 본다 — 실제 불릿은 " · " 형태로 떨어져 나온다.
_BULLET_SPLIT_RE = re.compile(r"\s*•\s*|\s+·\s+")
_BULLET_MARK_RE = re.compile(r"•|\s·\s")
# 문장 끝(또는 문두) 뒤에 오는 열거 마커. `A.` `B)` `1.` `2)` `a.` `i.` 를 잡되, 소수점
# 숫자(`0.22 µm`)·약어(`No.`)를 끊지 않도록 **마커 뒤 공백**을 필수로 둔다.
_ENUM_SPLIT_RE = re.compile(r"(?<=[.。:])\s+(?=(?:[A-Za-z]|[ivx]{1,4}|\d{1,2})[.)]\s)")
# GMP 운영항목 코드(`1` `1.1` `1.1.1.4`) — 코드 앞에서 끊는다.
_GMP_CODE_RE = re.compile(r"(?=(?:^|\s)(\d+(?:\.\d+)*)\s+\D)")


def split_detail_blocks(text: str) -> list[dict[str, Any]]:
    """상세 본문 → 표시 블록 목록(순수 함수). 원문에 있는 마커에서만 끊는다.

    반환: `[{"kind": "para"|"item", "text": str}, ...]`.
    마커가 하나도 없으면 `[{"kind": "para", "text": <원문>}]` 그대로(무변형).
    """
    s = " ".join((text or "").split())
    if not s:
        return []
    out: list[dict[str, Any]] = []
    # ① 불릿이 있으면 불릿 우선(불릿 앞 도입문은 문단으로 남긴다)
    if _BULLET_MARK_RE.search(s):
        parts = [p.strip() for p in _BULLET_SPLIT_RE.split(s)]
        head, items = parts[0], [p for p in parts[1:] if p]
        if head:
            out.append({"kind": "para", "text": head})
        out.extend({"kind": "item", "text": p} for p in items)
        return out
    # ② 열거 마커(A. 1. a. i.)로 끊는다 — 첫 조각은 도입문일 수 있다
    chunks = [c.strip() for c in _ENUM_SPLIT_RE.split(s) if c.strip()]
    if len(chunks) > 1:
        return [{"kind": ("item" if re.match(r"^(?:[A-Za-z]|[ivx]{1,4}|\d{1,2})[.)]\s", c)
                          else "para"), "text": c} for c in chunks]
    return [{"kind": "para", "text": s}]


def split_gmp_operations(text: str) -> list[dict[str, Any]]:
    """GMP 운영항목 문자열 → `[{"code","label","depth"}]`(순수 함수).

    `1 비준수 제조 작업 1.1 무균 제품 1.1.1 무균 조제 …` 처럼 계층 코드가 한 줄로 붙어
    나오는 필드를 코드 단위로 끊고 점(.) 개수로 들여쓰기 깊이를 준다. 코드가 하나도
    안 잡히면 빈 목록 — 호출부가 기존 문단 렌더로 폴백한다(무변형).
    """
    s = " ".join((text or "").split())
    if not s:
        return []
    pos = [m.start() for m in _GMP_CODE_RE.finditer(s)]
    if len(pos) < 2:
        return []
    rows: list[dict[str, Any]] = []
    for i, start in enumerate(pos):
        seg = s[start:(pos[i + 1] if i + 1 < len(pos) else len(s))].strip()
        m = re.match(r"^(\d+(?:\.\d+)*)\s+(.*)$", seg)
        if not m:
            continue
        code, label = m.group(1), m.group(2).strip()
        if label:
            rows.append({"code": code, "label": label, "depth": code.count(".")})
    return rows if len(rows) >= 2 else []


# ── 카드 뷰모델(표시 플래그만 산출 — 사실/URL 값은 절대 변형 금지) ─────────────
def _card_view(card: dict[str, Any]) -> dict[str, Any]:
    quotes_in = card.get("quotes") or []
    multi = len(quotes_in) > 1
    any_trans = any(q.get("translation") for q in quotes_in)  # null·"" 둘 다 falsy
    quotes: list[dict[str, Any]] = []
    for i, q in enumerate(quotes_in):
        trans = q.get("translation")
        quotes.append({
            "original": q.get("original", ""),
            "translation": trans,
            "show_translation": bool(trans),           # null/"" → 번역 줄 생략
            "mark": (MARKS[i] if (multi and i < len(MARKS)) else ""),
        })

    src = card.get("sources") or {}
    lc = src.get("link_check") or {}
    is_pdf = bool(src.get("official_is_pdf"))
    sources = {
        "info": {
            "url": _safe_url(src.get("info_url", "")),
            "state": lc.get("info", "pending"),
            "icon": "ti-database",
            "text": "data source",
        },
        "official": {
            "url": _safe_url(src.get("official_url", "")),
            "state": lc.get("official", "pending"),
            "icon": ("ti-file-type-pdf" if is_pdf else "ti-file-text"),
            "text": ("PDF 원문" if is_pdf else "공식 페이지"),
        },
    }

    # [실사관 표기 2026-07-30] `/findings/` 화면과 동일 형식("실사관: A · B")을 브리프
    # 카드에도 낸다. card_scaffold 는 raw.fda483_inspectors 를 그대로 옮기므로(무변형
    # producer 원칙), 카드 JSON 값이 무엇이든 여기서 방어적으로 정제한다 — 카드 dict 자체는
    # 복사본만 바꾸고 원본(card 인자)은 건드리지 않는다(다른 카드 뷰 파생과 동일 원칙).
    # 정제 결과가 빈 리스트면 키를 아예 지운다 → card.html 이 `{% if dd.inspectors %}` 로
    # 요소를 만들지 않는다(빈 라벨 금지) — 이 필드가 없던 기존 카드는 애초에 변형이 없다.
    detail = card.get("deterministic_detail") or None
    if isinstance(detail, dict) and detail.get("type") == "fda_483_observations":
        detail = dict(detail)
        inspectors = _sanitize_inspector_names(detail.get("inspectors"))
        if inspectors:
            detail["inspectors"] = inspectors
        else:
            detail.pop("inspectors", None)

    return {
        "render_order": card.get("render_order"),
        "anchor": _card_anchor(card),
        "group": card.get("group"),
        "group_label": card.get("group_label"),
        "group_head": None,                            # 섹션 조립 시 결정
        "is_evA": card.get("evidence_level") == "A",
        "card_type": card.get("card_type", ""),
        "agency": card.get("agency", ""),
        "headline_target": card.get("headline_target", ""),
        "title_issue": card.get("title_issue", ""),
        "toc_distinguisher": "",            # P1-1: 동명 카드 목차 구분자(annotate 단계서 채움)
        "evidence_level": card.get("evidence_level", ""),
        "signal_label": card.get("signal_label", ""),
        "signal_tier": card.get("signal_tier", ""),
        "sig_color": SIG_COLOR.get(card.get("signal_label"), "var(--lo)"),
        "modality": card.get("modality"),
        "type_tag": card.get("type_tag"),
        "summary": card.get("summary", ""),
        "facts": [{"label": f.get("label", ""),
                   "value": f.get("value", ""),
                   "mono": f.get("label", "") in MONO_LABELS}
                  for f in (card.get("facts") or [])],
        # [브리프→업체 프로파일 브릿지] 파생 키(사실 재작성 0 — facts 값에서 결정론
        # 파생만). 빈 문자열이면 card.html 이 data-firm-key 속성을 생략한다.
        "firm_key": _firm_key_for_card(card),
        "merged": (card.get("merged_count") or 1) > 1,
        "merged_count": card.get("merged_count", 1),
        "merged_items": card.get("merged_items") or [],
        # 병합 목록 단위 명사(기본 '품목' — 회수 골든 불변). 483 실사기록 다건 공개 디제스트는 '건'.
        "merged_noun": card.get("merged_noun") or "품목",
        "quotes": quotes,
        "quote_label": (("원문 및 번역" if any_trans else "원문") if quotes_in else None),
        "key_facts": card.get("key_facts") or [],
        "evidence_basis": card.get("evidence_basis", ""),
        "implication": card.get("implication", ""),
        "checks": card.get("checks") or [],
        # [WL 심층분석 fan-out 2026-07-01] 7번째·선택 슬롯 그대로 통과(사실/URL 무변형 원칙과
        # 동형 — 표시 플래그 미가공, 값 자체는 raw). 대다수 카드는 키 부재/None → card.html
        # `{% if card.deep_analysis %}` 가 False 라 기존 golden 출력 바이트 불변(additive).
        "deep_analysis": card.get("deep_analysis") or None,
        # [상세보기 결정론 승격 2026-07-02] 결정론 상세 슬롯 그대로 통과(deep_analysis 와 동형).
        # 키 부재/None → card.html `{% if card.deterministic_detail %}` False → golden 불변.
        "deterministic_detail": detail,
        # [소스확장 2026-07-02 · UI 보강] 접힘 미리보기 태그(결정론 파생 — 사실 재작성 0).
        "deep_preview": _deep_preview(card.get("deep_analysis")),
        "detail_preview": _detail_preview(card.get("deterministic_detail")),
        "sources": sources,
    }


# ── [업계 브리핑 노트 2026-07-13] resource note 뷰모델(표시 플래그만 산출) ────────
def _resource_view(r: dict[str, Any]) -> dict[str, Any]:
    """assemble_publish_brief.extract_resource_notes() 산출 dict → 렌더 뷰모델.

    사실/URL 무변형 원칙(card 뷰모델과 동형) — 유일한 파생은 official_url 스킴
    화이트리스트 게이트(_safe_url, card.html 의 sources.official 과 동일 계약).
    info_url(RSS 피드)은 렌더에 쓰지 않는다(§1 근거).
    """
    src = r.get("sources") or {}
    return {
        "id": r.get("id", ""),
        "title": r.get("title", ""),
        "original_title": r.get("original_title", ""),
        "summary": r.get("summary", ""),
        "agency": r.get("agency", ""),
        "type_tag": r.get("type_tag", ""),
        "official_url": _safe_url(src.get("official_url", "")),
    }


def _annotate_toc_distinguishers(card_views: list[dict[str, Any]]) -> None:
    """동일 headline_target 이 2장 이상이면 목차 라벨이 중복으로 보이므로(P1-1),
    그 카드들에 한해 구분자를 단다 — title_issue 우선, 없으면 anchor(=문서번호).

    목차 표시 전용(브리프 단위로 산출). 카드 본문·딥링크 앵커(anchor)는 불변 —
    값을 새로 만들지 않고 기존 카드값(title_issue/anchor)만 라벨에 덧붙인다(무변형).
    """
    counts: dict[str, int] = {}
    for cv in card_views:
        t = cv.get("headline_target", "")
        counts[t] = counts.get(t, 0) + 1
    for cv in card_views:
        if counts.get(cv.get("headline_target", ""), 0) > 1:
            cv["toc_distinguisher"] = cv.get("title_issue") or cv.get("anchor", "")


def _is_renderable(card: dict[str, Any]) -> bool:
    """렌더 제외 카드 판별(방어적 — 상류 순수성 미가정, §3.2/§3.3 렌더러 책임).

    병합 멤버(`merged_into` truthy)와 watch(비카드 영역)는 렌더하지 않는다. 스키마 v1 정상
    데이터엔 없음(상류 `assemble_web_brief` 가 이미 제외) — 적대/직접 주입에 대한 방어선.
    정렬·섹션 카운트·TOC 산출 *이전*에 적용해 제외 카드가 목차·건수에 새지 않게 한다.
    """
    if card.get("merged_into"):
        return False
    if card.get("group") == "watch" or card.get("section") == "watch":
        return False
    return True


def _build_sections(card_views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """render_order 순 카드를 group(섹션)·group_label(소제목)별로 연속 묶음.

    재정렬 금지 — 입력 순서 그대로 인접 그룹핑(v4 JS 동치). 섹션 count 는 파생.
    """
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    cur_grp: Any = object()                            # sentinel
    for cv in card_views:
        if cur is None or cv["group"] != cur["name"]:
            cur = {
                "name": cv["group"],
                "slug": cv["group"],                   # 앵커 id (HTML5 허용 — 한글 가능)
                "icon": SECTION_ICON.get(cv["group"], _SECTION_ICON_DEFAULT),
                "cards": [],
            }
            sections.append(cur)
            cur_grp = object()                         # 새 섹션 → 그룹 리셋
        gl = cv.get("group_label")
        if gl and gl != cur_grp:
            cur_grp = gl
            cv["group_head"] = gl
        else:
            cv["group_head"] = None
        cur["cards"].append(cv)
    for s in sections:
        s["count"] = len(s["cards"])
    return sections


def _norm_coverage(cov: dict[str, Any]) -> dict[str, Any]:
    ev = cov.get("evidence") or {}
    return {
        "intake_total": cov.get("intake_total", 0),
        "rendered": cov.get("rendered", 0),
        "evidence": {"A": ev.get("A", 0), "B": ev.get("B", 0), "C": ev.get("C", 0)},
    }


# ── 브리프 로드·issue 번호 부여 ───────────────────────────────────────────────
def load_briefs(data_dir: Path) -> list[dict[str, Any]]:
    """data_dir 의 *.json 을 로드. 파일명 정렬로 결정론적 순회."""
    briefs = []
    for fp in sorted(data_dir.glob("*.json")):
        briefs.append(json.loads(fp.read_text(encoding="utf-8")))
    return briefs


# ── [자료실] 카탈로그 registry — 카탈로그 1개 추가 = 데이터 파일 1개 + 아래 항목 1개 ──
# file 은 web/data/library/ 상대 파일명(v2 스키마: 평면 items[]·meta 없음 — 표시 카피는
# 전부 registry 소유). 렌더는 전 카탈로그가 공통 템플릿(library_catalog.html) 하나를 쓴다
# — 템플릿·render_site 는 추가 시 무수정. 선택 키:
#   sort="published_desc"  발행일 내림차순 뷰 정렬(무날짜 항목은 뒤, 동일 날짜는 데이터 순).
#                          미지정 = 데이터 순서 유지(ICH=코드순·EU GMP=Part/Annex 구조순).
#   link_label             항목 official_url 이 개별 문서가 아니라 카탈로그 페이지로 수렴할 때
#                          (ICH: 전 31토픽 → 공식 카탈로그 2페이지) 제목 링크 대신 그룹/항목
#                          레벨의 정직한 라벨 링크로 렌더한다(개별 문서 링크로 오인 방지).
#   groups_by_url          평면 items 를 official_url 부분일치로 계열 그룹핑(결정론 파생).
#   public_base            상단 메타의 "공식 사이트" 링크.
#   doc_type_labels        doc_type 표시층 매핑(데이터 무수정 — 뷰만). 내부 슬러그
#                          (guidance-internal 등)를 한국어 라벨로, ""로 매핑하면 칩 숨김.
#                          미등재 값은 원문 그대로 표시.
LIBRARY_REGISTRY: list[dict[str, Any]] = [
    {"slug": "ich", "short": "ICH", "file": "ich.json", "unit": "토픽", "kick": "ICH · Guidelines",
     "title": "ICH 가이드라인 카탈로그",
     "blurb": "FDA·EMA·식약처가 공통으로 채택하는 국제 조화 가이드라인. 품질(Q)·다분야(M) 계열별 토픽을 한글 명칭과 함께 정리.",
     "intro": "FDA·EMA·식약처가 공통으로 채택하는 국제 조화(ICH) 가이드라인의 토픽 카탈로그입니다. 품질(Q)·다분야(M) 계열별로 한글 명칭을 병기해 정리했으며, 현행 문서가 공개된 토픽은 공식 원문 PDF로 바로 연결됩니다. 식약처 한글 번역본이 있는 토픽은 번역본 링크를 함께 제공합니다. 최신 Step·개정 현황은 계열별 ICH 공식 카탈로그 페이지에서 확인하실 수 있습니다.",
     "desc": "ICH Q(품질)·M(다분야) 가이드라인 토픽 카탈로그 — 코드·한글 명칭 병기, 원문 PDF·식약처 번역본·ICH 공식 카탈로그 링크.",
     "public_base": "https://www.ich.org/",
     "link_label": "ICH 공식 카탈로그",
     "doc_type_labels": {"guideline-topic": ""},
     "groups_by_url": [
         {"contains": "quality-guidelines", "badge": "Q", "label": "품질", "label_en": "Quality"},
         {"contains": "multidisciplinary-guidelines", "badge": "M", "label": "다분야", "label_en": "Multidisciplinary"},
     ]},
    {"slug": "mfds", "short": "식약처", "file": "mfds.json", "unit": "건", "kick": "MFDS · Guidance",
     "title": "MFDS 지침·고시 아카이브",
     "blurb": "식약처가 공개한 지침·안내서·고시·행정예고. 주간 브리프에서 다룬 뒤에도 다시 찾아볼 수 있는 누적 목록.",
     "intro": "식약처(MFDS)가 공개한 지침·안내서·고시·행정예고를 발행일 순으로 모았습니다. 주간 브리프에서 한 번 다룬 문서도 이곳에서 다시 찾아볼 수 있습니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "식약처(MFDS) 지침·안내서·고시·행정예고 아카이브 — 제목·유형·발행일·공식 원문 링크.",
     "sort": "published_desc",
     "doc_type_labels": {"guidance-internal": "공무원 지침서", "guidance-industry": "민원인 안내서·지침",
                         "legislative-notice": "입법·행정예고", "notice-final": "고시 전문"}},
    {"slug": "eu-gmp", "short": "EU GMP", "file": "eu_gmp.json", "unit": "건", "kick": "EU · EudraLex Vol 4",
     "title": "EU GMP 기준서 (EudraLex Vol 4)",
     "blurb": "유럽연합 의약품 GMP 기준서. Part I·II·III 각 장과 부속서(Annex)를 구조 순서대로 정리.",
     "intro": "유럽연합 의약품 GMP 기준서(EudraLex Volume 4)의 문서 목록입니다. Part I(기본 요건)·Part II(원료의약품)·Part III(보조 문서)과 부속서(Annex)를 기준서 구조 순서대로 정리했으며, 각 문서의 공식 원문 PDF로 바로 연결됩니다. 법적 효력과 최신 개정본은 반드시 공식 원문에서 확인하세요.",
     "desc": "EU GMP 기준서(EudraLex Volume 4) 문서 목록 — Part I·II·III과 부속서(Annex), 공식 원문 PDF 링크."},
    {"slug": "pics", "short": "PIC/S", "file": "pics.json", "unit": "건", "kick": "PIC/S · GMP Guide",
     "title": "PIC/S GMP 가이드",
     "blurb": "의약품실사상호협력기구(PIC/S)의 GMP 가이드(PE 009)와 부속서·가이던스 문서 목록.",
     "intro": "의약품실사상호협력기구(PIC/S)가 공개한 GMP 가이드(PE 009) 각 부와 부속서, 관련 가이던스 문서를 발행일 순으로 정리했습니다. 식약처를 포함한 PIC/S 가입 규제기관의 실사 기준과 맞닿아 있는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "PIC/S GMP 가이드(PE 009)·부속서·가이던스 문서 목록 — 발행일·공식 원문 링크.",
     "sort": "published_desc"},
    {"slug": "who", "short": "WHO", "file": "who.json", "unit": "건", "kick": "WHO · TRS Annexes",
     "title": "WHO TRS 부속서 모음",
     "blurb": "WHO 전문가위원회 기술보고서(TRS) 부속서 중 GMP·품질 관련 문서 선별 목록.",
     "intro": "세계보건기구(WHO) 의약품 표준 전문가위원회 기술보고서(TRS)의 부속서 가운데 GMP·품질 관련 문서를 발행일 순으로 선별해 정리했습니다. WHO 사전적격성평가(PQ)나 국제 조달 요건을 다룰 때 기준이 되는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "WHO 기술보고서(TRS) 부속서 중 GMP·품질 문서 선별 목록 — 발행일·공식 원문 링크.",
     "sort": "published_desc"},
    {"slug": "fda-guidance", "short": "FDA", "file": "fda_guidance.json", "unit": "건", "kick": "FDA · Guidance",
     "title": "FDA 가이던스 문서",
     "blurb": "FDA가 공개한 의약품 GMP·품질 관련 가이던스 문서 선별 목록.",
     "intro": "미국 FDA가 공개한 의약품 GMP·품질 관련 가이던스 문서를 발행일 순으로 선별해 정리했습니다. 가이던스는 FDA의 현재 견해를 담은 권고 문서로, 법적 구속력이 있는 규정(CFR)과는 구분해 읽어야 합니다. 최신 개정 여부는 반드시 공식 원문에서 확인하세요.",
     "desc": "FDA 의약품 GMP·품질 가이던스 문서 선별 목록 — 발행일·유형·공식 원문 링크.",
     "sort": "published_desc"},
    # [자료실 배치 2026-08-11] 같은 관할은 붙여 둔다. 미국은 "가이던스(권고) → 21 CFR(법령)",
    #   유럽은 "EudraLex Vol 4(기준서) → EMA(가이던스)" 로 이미 쌍을 이루고 있었는데, 신규
    #   2종(cfr·mhra)을 registry 끝에 append 했더니 21 CFR 이 FDA 가이던스와 네 칸 떨어져
    #   화면에서 "왜 FDA 자료가 두 군데냐"로 읽혔다(사용자 지적). 분리 자체는 유지한다 —
    #   입도가 다르기 때문이다(가이던스 86건 = 문서 단위, 21 CFR 63건 = 조항 단위. 합치면
    #   "FDA 149건"이 무엇을 센 숫자인지 알 수 없게 되고, findings 의 cfr_refs 가 조항으로
    #   바로 갈 수 있는 조인 축도 사라진다). 배치와 상호 참조 문구로 관계를 밝힌다.
    {"slug": "cfr", "short": "21 CFR", "file": "cfr.json", "unit": "개 조항", "kick": "US · 21 CFR",
     "title": "미국 연방규정 21 CFR (GMP)",
     "blurb": "미국 연방규정(CFR) 중 의약품 GMP 조항 원문. 가이드라인이 아니라 법령 그 자체 — Part 210(총칙)·Part 211(완제의약품 CGMP) 전 조항을 조문 단위로 수록.",
     "intro": "미국 연방규정집(Code of Federal Regulations) Title 21 가운데 의약품 현행 우수제조관리기준(CGMP)을 담은 Part 210(총칙)과 Part 211(완제의약품 CGMP) 전 조항을 조문 단위로 정리했습니다. 자료실의 다른 컬렉션이 가이드라인·기준서인 것과 달리 이 컬렉션은 법적 구속력을 갖는 규정 원문 그 자체입니다. FDA가 권고 형태로 내는 문서는 바로 앞의 'FDA 가이던스 문서' 컬렉션에 있습니다. 각 조항은 공식 원문(eCFR)으로 바로 연결됩니다. 개정 이력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "미국 연방규정(21 CFR) Part 210(총칙)·Part 211(완제의약품 CGMP) 조항 목록 — 조번호·제목·공식 원문(eCFR) 링크.",
     "public_base": "https://www.ecfr.gov/current/title-21",
     "doc_type_labels": {"regulation-section": "규정 조항"},
     "groups_by_url": [
         {"contains": "/part-210/", "badge": "210", "label": "총칙", "label_en": "General Provisions"},
         {"contains": "/part-211/", "badge": "211", "label": "완제의약품 CGMP", "label_en": "Finished Pharmaceuticals"},
     ]},
    {"slug": "ema", "short": "EMA", "file": "ema.json", "unit": "건", "kick": "EMA · Guidance",
     "title": "EMA GMP·품질 가이드라인",
     "blurb": "유럽의약품청(EMA)이 공개한 GMP 관련 절차·과학 가이드라인과 질의응답(Q&A) 선별 목록.",
     "intro": "유럽의약품청(EMA)이 공개한 GMP·품질 관련 문서를 발행일 순으로 선별해 정리했습니다. 실사 당국 품질 시스템, 품질 결함 보고·신속 경보 처리 등 규제 절차 가이드라인과 과학 가이드라인, 질의응답(Q&A)을 포함합니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "EMA GMP·품질 절차·과학 가이드라인과 질의응답(Q&A) 선별 목록 — 발행일·유형·공식 원문 링크.",
     "sort": "published_desc",
     "public_base": "https://www.ema.europa.eu/",
     "doc_type_labels": {"regulatory-procedural-guideline": "규제·절차 가이드라인",
                         "scientific-guideline": "과학 가이드라인",
                         "questions-and-answers": "질의응답(Q&A)"}},
    {"slug": "mhra", "short": "MHRA", "file": "mhra.json", "unit": "건", "kick": "UK · MHRA",
     "title": "MHRA GMP·GDP 가이던스",
     # [정적 연도 제거 2026-08-12] 옛 문구는 "2019년 이후 갱신 없음"이라고 못박아 뒀다.
     # 외부 기관 상태를 정적으로 단정한 것이라, MHRA 가 새 통계를 내는 순간 조용히 거짓이
     # 된다(카탈로그는 매주 자동 갱신되는데 이 문장만 안 바뀐다). 경고는 유지하되 연도는
     # 아래 목록이 스스로 보여주게 넘긴다.
     "blurb": "영국 MHRA의 GMP·GDP 컴플라이언스 정보시트·실사 결함통계·가이던스 문서 목록.",
     "intro": "영국 의약품·의료제품규제청(MHRA)이 공개한 GMP·GDP 관련 문서를 정보시트·실사 결함통계·가이던스로 나누어 정리했습니다. 컴플라이언스 매니지먼트(Compliance Management)·규제조치(Regulatory Action) 절차를 설명하는 정보시트, 실사에서 반복 확인되는 결함 유형을 다룬 GMP 실사 결함통계, 실사 대응·분산형 제조 등 개별 주제를 다루는 가이던스·공지 문서를 포함합니다. GMP 실사 결함통계 시리즈는 발행 간격이 길어 목록의 최신 자료가 몇 해 전일 수 있습니다 — 아래 각 문서의 발행 연도를 확인하시고, 오래된 통계를 현재 실사 경향으로 그대로 참고하지 마세요. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "MHRA(영국) GMP·GDP 컴플라이언스 정보시트·실사 결함통계·가이던스 문서 목록 — 제목·유형·공식 원문 링크.",
     "doc_type_labels": {"information-sheet": "정보시트", "gmp-deficiency-statistics": "GMP 실사 결함통계",
                         "detailed_guide": "가이던스", "notice": "공지",
                         "transparency": "투명성 공개", "guidance": "가이던스 자료"}},
    {"slug": "health-canada", "short": "Health Canada", "file": "health_canada.json", "unit": "건",
     "kick": "Health Canada · GMP",
     "title": "Health Canada GMP 가이드",
     "blurb": "캐나다 보건부(Health Canada)의 GMP 가이드(GUI 시리즈) 문서 목록.",
     "intro": "캐나다 보건부(Health Canada)가 공개한 GMP 가이드(GUI 시리즈) 문서를 발행일 순으로 정리했습니다. 의약품 GMP 실사와 시설 허가(Establishment Licence) 운영의 기준이 되는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "Health Canada GMP 가이드(GUI 시리즈) 문서 목록 — 코드·발행일·공식 원문 링크.",
     "sort": "published_desc",
     "public_base": "https://www.canada.ca/en/health-canada.html",
     "doc_type_labels": {"guidance": "가이던스"}},
    {"slug": "pmda", "short": "PMDA", "file": "pmda.json", "unit": "건",
     "kick": "PMDA · Inspection Cases",
     "title": "PMDA 실사 지적사례 (ORANGE Letter)",
     "blurb": "일본 PMDA가 공개한 GMP 실사 지적사례(ORANGE Letter) 영문판과 GMP/GCTP 연차보고서 목록.",
     "intro": "일본 의약품의료기기종합기구(PMDA)가 공개한 GMP 실사 지적사례(ORANGE Letter)의 영문판과 GMP/GCTP 연차보고서를 정리했습니다. ORANGE Letter는 특정 업체가 아니라 실사에서 반복 확인되는 결함 유형(기록 부적정·CAPA 미흡·무균 환경모니터링 등)의 배경·위험·점검 포인트를 익명 케이스로 설명하는 자료입니다. 각 문서의 공식 원문 PDF로 바로 연결됩니다. 영문판은 일본어 원문 대비 시차가 있을 수 있으며, 최신 현황은 공식 원문에서 확인하세요.",
     "desc": "일본 PMDA GMP 실사 지적사례(ORANGE Letter) 영문판·GMP/GCTP 연차보고서 목록 — 제목·유형·공식 원문 PDF 링크.",
     "public_base": "https://www.pmda.go.jp/english/review-services/gmp-qms-gctp/0007.html",
     "groups_by_doc_type": [
         {"doc_type": "inspection-observation", "label": "실사 지적사례", "label_en": "Inspection Cases"},
         {"doc_type": "annual-report", "label": "연차보고서", "label_en": "Annual Reports"},
     ],
     "doc_type_labels": {"inspection-observation": "실사 지적사례", "annual-report": "연차보고서"}},
]


def _library_item_view(it: dict[str, Any]) -> dict[str, Any]:
    """카탈로그 항목 → 공통 항목 뷰 — 스키마 v2(값 무변형 통과).

    표시 제목은 한국어 우선: title_ko 가 있으면 주 제목, title_en 은 병기 줄(sub)로
    내린다(한국어 사이트 — MFDS/ICH 병기). 선택 필드(code·doc_type·published_date·
    ko_url·pdf_url)는 있으면 표시, 없으면 빈 문자열 → 템플릿이 조용히 생략. 날짜는
    **발행일(published_date)만** 노출 — 수집일 등 내부 운영 개념은 사용자 표기 금지
    (품질 기준 2026-07-18)."""
    title_en = it.get("title_en") or it.get("title") or ""
    title_ko = it.get("title_ko") or ""
    return {
        # id 는 화면에 쓰지 않는다 — 변경이력(library_updates.json)이 id 만 저장하고
        # 제목·링크는 렌더 시점에 이 뷰에서 join 하므로 조인 키로만 싣는다.
        "id": it.get("id") or "",
        "title": title_ko or title_en,
        "sub": title_en if title_ko else "",
        "code": it.get("code") or "",
        "doc_type": it.get("doc_type") or "",
        "published_date": it.get("published_date") or "",
        "official_url": _safe_url(it.get("official_url") or ""),
        "ko_url": _safe_url(it.get("ko_url") or ""),
        "pdf_url": _safe_url(it.get("pdf_url") or ""),
    }


def _catalog_view(entry: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """카탈로그 raw(v2 평면 items[]) → 공통 템플릿 뷰모델(결정론 — 데이터 파생, 창작 0).

    - sort="published_desc": 발행일 내림차순 뷰 정렬(값 무수정 — 표시 순서만). 무날짜
      항목은 뒤로, 동일 날짜는 데이터 순 유지(안정 정렬).
    - groups_by_url: official_url 부분일치로 계열 그룹핑(ICH Q/M — 결정론 파생). 그룹
      공식 링크 = 그룹 내 공유 URL. 매칭 실패 항목은 무라벨 그룹으로 뒤에 둔다.
    - Tier/QA·수집일 등 내부 운영 필드는 뷰에 올리지 않는다(사용자 노출 금지)."""
    items = [_library_item_view(it) for it in raw.get("items", [])]
    labels = entry.get("doc_type_labels") or {}
    if entry.get("sort") == "published_desc":
        items = sorted(items, key=lambda it: it["published_date"], reverse=True)
    groups: list[dict[str, Any]] = []
    if entry.get("groups_by_url"):
        rest = list(items)
        for spec in entry["groups_by_url"]:
            matched = [it for it in rest if spec["contains"] in it["official_url"]]
            rest = [it for it in rest if it not in matched]
            groups.append({
                "badge": spec.get("badge", ""),
                "label": spec.get("label", ""),
                "label_en": spec.get("label_en", ""),
                "blurb": spec.get("blurb", ""),
                "official_url": matched[0]["official_url"] if matched else "",
                "items": matched,
            })
        if rest:
            groups.append({"badge": "", "label": "", "label_en": "", "blurb": "",
                           "official_url": "", "items": rest})
    elif entry.get("groups_by_doc_type"):
        # doc_type(원 slug) 기준 계열 그룹핑 — URL 로 안 갈리는 카탈로그(PMDA 실사사례/연차보고서)용.
        # 라벨 치환 전 slug 로 매칭하므로 이 블록은 반드시 아래 doc_type 라벨 치환보다 앞에 온다.
        rest = list(items)
        for spec in entry["groups_by_doc_type"]:
            matched = [it for it in rest if it["doc_type"] == spec["doc_type"]]
            rest = [it for it in rest if it not in matched]
            groups.append({
                "badge": spec.get("badge", ""),
                "label": spec.get("label", ""),
                "label_en": spec.get("label_en", ""),
                "blurb": spec.get("blurb", ""),
                "official_url": "",
                "items": matched,
            })
        if rest:
            groups.append({"badge": "", "label": "", "label_en": "", "blurb": "",
                           "official_url": "", "items": rest})
    else:
        groups.append({"badge": "", "label": "", "label_en": "", "blurb": "",
                       "official_url": "", "items": items})
    # doc_type 표시 라벨은 그룹핑(원 slug 매칭) 이후 적용 — groups_by_doc_type 가 slug 로 나뉘도록.
    for it in items:
        it["doc_type"] = labels.get(it["doc_type"], it["doc_type"])
    dates = [it["published_date"] for it in items if it["published_date"]]
    meta = raw.get("meta", {})
    return {
        "slug": entry["slug"], "unit": entry["unit"], "kick": entry["kick"],
        # source = 카탈로그 파일 stem — 수집기 LIBRARY_SOURCE·변경이력 키와 같은 값.
        "source": entry["file"].rsplit(".", 1)[0], "short": entry.get("short", ""),
        "items_by_id": {it["id"]: it for it in items},
        "intro": entry["intro"], "blurb": entry["blurb"], "desc": entry["desc"],
        "title": entry.get("title") or meta.get("title", ""),
        "note": meta.get("note", ""),
        "public_base": _safe_url(entry.get("public_base") or meta.get("public_base", "")),
        "link_label": entry.get("link_label", ""),
        "count": len(items),
        "latest_published": max(dates) if dates else "",
        "grouped": bool(entry.get("groups_by_url") or entry.get("groups_by_doc_type")),
        "groups": groups,
    }


def load_library(library_dir: Path = LIBRARY_DIR) -> list[dict[str, Any]]:
    """[자료실] registry 순서대로 커밋 데이터를 로드해 공통 뷰 리스트로 반환 — 결정론
    (파일 byte 파생, 네트워크 0). 파일 부재 카탈로그는 조용히 건너뛴다(허브는 존재분만)."""
    views = []
    for entry in LIBRARY_REGISTRY:
        p = library_dir / entry["file"]
        if p.is_file():
            views.append(_catalog_view(entry, json.loads(p.read_text(encoding="utf-8"))))
    return views


# ── [자료실] 최근 변경 알림 — 주간 자동 갱신이 무엇을 바꿨는지 ────────────────
# 데이터(web/data/library_updates.json)는 **id 와 개수만** 갖는다(library_updates.py).
# 표시 제목·링크는 여기서 라이브 카탈로그 뷰와 join 한다 — 카탈로그가 표시 카피의
# 단일 출처라는 규칙을 지키기 위해서다. 이미 사라진 id 는 조용히 건너뛴다(이력이
# 없는 문서를 지어내지 않는다). 표시 상한을 넘기면 "외 N건"으로 정직하게 남긴다.
LIBRARY_UPDATE_ITEM_CAP = 12       # 자료실 허브 — 한 화면에 남는 분량
# 모아보기 스트립은 검색·필터 위에 오는 부차 정보 — 제목 인라인 한 줄을 넘기지 않는 값.
LIBRARY_UPDATE_ITEM_CAP_COMPACT = 3


def _library_update_view(
    entry: dict[str, Any], catalogs: list[dict[str, Any]], *, cap: int,
) -> dict[str, Any] | None:
    """이력 항목 1건 + 카탈로그 뷰 → 표시 뷰모델(결정론 — 데이터 파생, 창작 0).

    카탈로그 순서는 registry 순서를 따른다(사이트 전역 일관). 신규를 변경보다 앞에
    두고, 그 안에서는 카탈로그의 표시 순서를 그대로 쓴다.

    표시 상한(cap)은 **카탈로그마다 한 건씩 돌아가며** 배분한다. 앞선 카탈로그가 상한을
    통째로 먹으면 "FDA 3건 · EMA 3건 · PMDA 1건"이라 써놓고 제목은 FDA 것만 보이는
    화면이 된다(2026-07-25 실측) — 좁은 스트립일수록 소스가 고르게 보여야 한다."""
    collected: list[dict[str, Any]] = []
    new_total = changed_total = removed_total = 0
    for view in catalogs:                       # registry 순서 유지
        detail = (entry.get("sources") or {}).get(view["source"])
        if not detail:
            continue
        # 개수는 **카탈로그에서 실제로 해소된 항목**만 센다. 이력의 id 가 지금 카탈로그에
        # 없으면(뒤이은 큐레이션으로 정리된 항목 등) 링크를 만들 수 없는데, 그런 id 까지
        # 세면 "신규 1건"이라 써놓고 목록은 비어 있는 화면이 된다 — 읽는 사람이 확인할 수
        # 있는 것만 센다.
        rows: list[dict[str, Any]] = []
        counted = {"신규": 0, "변경": 0}
        for state, key in (("신규", "new_ids"), ("변경", "changed_ids")):
            for item_id in sorted(set(detail.get(key) or [])):
                item = view["items_by_id"].get(item_id)
                if not item:
                    continue
                counted[state] += 1
                rows.append({"title": item["title"], "sub": item["sub"],
                             "url": item["official_url"], "state": state})
        new_count, changed_count = counted["신규"], counted["변경"]
        # 내려간 항목은 카탈로그에 없어 링크가 없다 — 개수만 정직하게 남긴다.
        removed_count = len(set(detail.get("removed_ids") or []))
        if not (new_count or changed_count or removed_count):
            continue
        new_total += new_count
        changed_total += changed_count
        removed_total += removed_count
        collected.append({
            "view": view, "rows": rows, "truncated": bool(detail.get("truncated")),
            "new_count": new_count, "changed_count": changed_count,
            "removed_count": removed_count,
        })
    if not collected:
        return None

    # 라운드로빈 배분 — 한 바퀴에 카탈로그당 한 건씩, 상한이 차거나 더 줄 게 없을 때까지.
    quota = [0] * len(collected)
    remaining = cap
    while remaining > 0 and any(quota[i] < len(c["rows"])
                                for i, c in enumerate(collected)):
        for index, entry_rows in enumerate(collected):
            if remaining <= 0:
                break
            if quota[index] < len(entry_rows["rows"]):
                quota[index] += 1
                remaining -= 1

    sources: list[dict[str, Any]] = []
    for index, collected_source in enumerate(collected):
        view, rows = collected_source["view"], collected_source["rows"]
        new_count = collected_source["new_count"]
        changed_count = collected_source["changed_count"]
        removed_count = collected_source["removed_count"]
        sources.append({
            "slug": view["slug"], "short": view["short"], "title": view["title"],
            "new_count": new_count, "changed_count": changed_count,
            "removed_count": removed_count,
            "change_count": new_count + changed_count,
            "items": rows[:quota[index]],
            "hidden_count": len(rows) - quota[index],
            # truncated = 이력 저장 단계에서 id 자체가 잘린 경우(표시 상한과 별개).
            "truncated": collected_source["truncated"],
        })
    return {
        "date": entry.get("date", ""),
        "sources": sources,
        "new_count": new_total, "changed_count": changed_total,
        "removed_count": removed_total,
        "change_count": new_total + changed_total,
        "catalog_count": len(sources),
        # 표시 상한에 걸려 못 보여준 건수 — "외 N건"으로 반드시 드러낸다(조용한 절삭 금지).
        "hidden_count": sum(s["hidden_count"] for s in sources),
    }


def load_library_update_entries(updates_file: Path | None = None) -> list[dict[str, Any]]:
    """변경 이력 항목을 최신 우선으로 반환(파일 없으면 빈 리스트).

    기본 경로는 **호출 시점에** 모듈 전역에서 읽는다(기본인자로 묶으면 정의 시점 값이
    박혀 테스트의 모듈 속성 monkeypatch 가 반영되지 않는다)."""
    updates_file = updates_file or LIBRARY_UPDATES_FILE
    entries: list[dict[str, Any]] = []
    if updates_file.is_file():
        payload = json.loads(updates_file.read_text(encoding="utf-8"))
        entries = [e for e in (payload.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: str(e.get("date") or ""), reverse=True)
    return entries


def build_library_update_view(
    entry: dict[str, Any] | None, catalogs: list[dict[str, Any]], *, cap: int,
) -> dict[str, Any] | None:
    """이력 항목 1건 → 표시 뷰(공개 진입점). **표시 상한은 부르는 쪽(채널)이 정한다** —
    자료실 허브·모아보기 스트립·뉴스레터가 각자 다른 분량을 싣기 때문이다."""
    return _library_update_view(entry, catalogs, cap=cap) if entry else None


def load_library_updates(
    catalogs: list[dict[str, Any]], updates_file: Path | None = None,
) -> dict[str, Any]:
    """최근 자료실 변경 1건을 두 화면(자료실 허브·모아보기)용 뷰로 반환.

    반환 dict 는 항상 존재한다 — 이력이 없거나(첫 가동 전) 최근 변경이 0이면
    latest/compact 가 None 이고, 템플릿은 "최근 변경 없음"으로 정직하게 표시한다."""
    entries = load_library_update_entries(updates_file)
    latest = entries[0] if entries else None
    return {
        "latest": build_library_update_view(latest, catalogs,
                                            cap=LIBRARY_UPDATE_ITEM_CAP),
        "compact": build_library_update_view(latest, catalogs,
                                             cap=LIBRARY_UPDATE_ITEM_CAP_COMPACT),
    }


# ── [이용안내] 제한 마크다운 서브셋 → 결정론 HTML ──────────────────────────────
# guide_content.md 는 정확히 다음 서브셋만 쓴다(콘텐츠 실측): # / ## / ### 헤딩,
# `- ` 순서없는 목록, `N. ` 순서있는 목록, `**굵게**`, 인라인 `` `코드` ``, 그 외는 문단.
# 링크/이미지/표/인용/코드블록 0. 외부 md 라이브러리 없이 이 서브셋만 순수·결정론 변환한다
# (같은 입력 → byte 동일). 텍스트는 markupsafe 로 먼저 escape → 제한된 인라인 마커만 태그로
# 승격하므로 autoescape 계약(<,>,&,",' 무해화)을 그대로 유지한다(XSS·브레이크아웃 방어선).
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_OL_RE = re.compile(r"^\d+\. ")


def _md_inline(text: str) -> str:
    """인라인 마크다운(`code` → <code>, **bold** → <strong>) 변환.

    입력 텍스트를 먼저 escape(원문에 <,>,& 등이 있어도 무해화)한 뒤, 이스케이프가 손대지
    않는 마커(`·*)만 태그로 치환한다. code 를 먼저 처리해 코드 내부의 * 가 굵게로 오인되지
    않게 한다(콘텐츠엔 그런 중첩이 없지만 방어적). 순수·결정론."""
    esc = str(_escape(text))
    esc = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", esc)
    esc = _MD_BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", esc)
    return esc


def render_guide_html(md_text: str) -> tuple[str, list[dict[str, str]], Markup]:
    """제한 md 서브셋 → (페이지 제목, h2 목차, 본문 HTML). 순수·결정론(같은 입력 → byte 동일).

    최상위 `# ` 헤딩은 페이지 제목으로 빼고 본문에는 넣지 않는다(템플릿 page-head 가 렌더).
    `## ` 헤딩은 등장 순서 기반 안정 앵커(id="sec-N")를 부여하고 목차 리스트
    [{id, title(마커 제거 평문)}] 로도 반환한다 — 템플릿 상단 목차가 소비(결정론 파생).
    반환 본문은 Markup 이라 Jinja autoescape 가 다시 이스케이프하지 않는다 — 단, 모든
    사용자 표시 텍스트는 _md_inline 이 이미 escape 했으므로 안전(제한 태그만 raw)."""
    title = ""
    toc: list[dict[str, str]] = []
    blocks: list[str] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(f"<p>{_md_inline(' '.join(para))}</p>")
            para.clear()

    lines = md_text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip()
        if not line.strip():
            flush_para()
            i += 1
            continue
        if line.startswith("### "):
            flush_para()
            blocks.append(f"<h3>{_md_inline(line[4:])}</h3>")
            i += 1
        elif line.startswith("## "):
            flush_para()
            sec_id = f"sec-{len(toc) + 1}"
            plain = _MD_CODE_RE.sub(r"\1", _MD_BOLD_RE.sub(r"\1", line[3:])).strip()
            toc.append({"id": sec_id, "title": plain})
            blocks.append(f'<h2 id="{sec_id}">{_md_inline(line[3:])}</h2>')
            i += 1
        elif line.startswith("# "):
            flush_para()
            title = line[2:].strip()
            i += 1
        elif line.startswith("- "):
            flush_para()
            items = []
            while i < n and lines[i].rstrip().startswith("- "):
                items.append(f"<li>{_md_inline(lines[i].rstrip()[2:])}</li>")
                i += 1
            blocks.append("<ul>" + "".join(items) + "</ul>")
        elif _MD_OL_RE.match(line):
            flush_para()
            items = []
            while i < n and _MD_OL_RE.match(lines[i].rstrip()):
                items.append(f"<li>{_md_inline(_MD_OL_RE.sub('', lines[i].rstrip()))}</li>")
                i += 1
            blocks.append("<ol>" + "".join(items) + "</ol>")
        else:
            para.append(line.strip())
            i += 1
    flush_para()
    return title, toc, Markup("\n".join(blocks))


def load_guide(path: Path = GUIDE_FILE) -> str | None:
    """[이용안내] 본문 md 로드(파일 부재 시 None → 페이지 조용히 생략)."""
    return path.read_text(encoding="utf-8") if path.is_file() else None


# ── [용어사전] 초성 색인 그룹핑(결정론 — 데이터 파생, 분류 창작 0) ──────────────
_GLOSSARY_LEAD = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
# 된소리 초성은 기본 자음 버킷으로 합친다(ㄲ→ㄱ 등) — 가나다 색인 표준.
_GLOSSARY_LEAD_BASE = {"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
_GLOSSARY_LATIN = "A–Z"
_GLOSSARY_ETC = "#"
# 색인 바·그룹 정렬 순서 = 가나다(한글 초성) → 라틴(A–Z) → 기타(#). 한글 term_ko 우선.
_GLOSSARY_BUCKET_ORDER = [
    "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅅ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
    _GLOSSARY_LATIN, _GLOSSARY_ETC,
]


def _glossary_bucket(term_ko: str) -> str:
    """term_ko 첫 글자 → 초성 버킷. 한글=초성(된소리 합침), 라틴 알파벳=A–Z, 그 외=#."""
    ch = term_ko[0]
    o = ord(ch)
    if 0xAC00 <= o <= 0xD7A3:
        lead = _GLOSSARY_LEAD[(o - 0xAC00) // 588]
        return _GLOSSARY_LEAD_BASE.get(lead, lead)
    if ch.isascii() and ch.isalpha():
        return _GLOSSARY_LATIN
    return _GLOSSARY_ETC


def load_glossary(path: Path = GLOSSARY_FILE) -> list[dict[str, Any]] | None:
    """[용어사전] 용어 리스트 로드(파일 부재 시 None → 페이지 조용히 생략)."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# RFC 3986 percent-encoding, byte-identical to urllib.parse.quote(s, safe="") — 표준
# urllib 은 쓰지 않는다(WebRenderPurityTest.test_no_impure_imports 가 render.py 안의
# `urllib` 루트 import 자체를 순수성 위반으로 차단 — urllib.parse 는 네트워크 0 이지만
# 루트 이름만 보는 AST 가드라 서브모듈을 구분하지 않는다). CPython 의 `_ALWAYS_SAFE`
# 상수와 동일한 미보호 문자 집합(ASCII 영숫자 + `_.-~`)을 그대로 재현한다.
_URL_QUOTE_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~"
)


def _url_quote(value: str, safe: str = "") -> str:
    """[용어사전→사례] 검색어 → URL query 안전 인코딩(결정론·네트워크 0). UTF-8 인코딩 후
    미보호 바이트만 %XX(대문자 hex)로 치환 — urllib.parse.quote(value, safe=safe) 와
    동일 출력을 표준 라이브러리 없이 재현한다."""
    safe_set = _URL_QUOTE_UNRESERVED | set(safe)
    out: list[str] = []
    for byte in value.encode("utf-8"):
        ch = chr(byte)
        if ch in safe_set:
            out.append(ch)
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def load_glossary_cases(path: Path = GLOSSARY_CASES_FILE) -> dict[str, dict[str, Any]] | None:
    """[용어사전→사례] 용어별 findings 검색어/건수 커밋 데이터 로드(id → item 딕셔너리).

    파일 부재 시 None → 기능이 조용히 꺼진다(load_glossary 와 동일 패턴). `excluded`
    항목은 여기서 걸러진다(딕셔너리에 없는 용어 id 는 build_glossary_view 가 빈 값으로
    렌더 — 링크 미표시)."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {it["id"]: it for it in (data.get("items") or [])}


# ── [용어사전 심화 B2] 관련 조항 라벨 → 공식 원문 URL 해석기 ──────────────────────
# URL 은 새로 수집하지 않는다 — 자료실(web/data/library/*.json) 커밋 데이터를 재사용
# 한다(네트워크 0·결정론). 규칙은 R1→R7 순서로 적용, 첫 매치 채택, 매치 없으면 ""
# (무링크가 안전 — 억지 매칭보다 낫다). 라벨 prefix 가 서로 배타적이라 순서 충돌 없음.
_REG_REF_EN_DASH = "–"  # 21 CFR 범위 표기(예: 211.160–211.194) — 특정 문서 1개를 못 가리킨다.

# mfds.json 은 code 필드가 없어 title_ko 로 특정한다. "의약품 제조 및 품질관리에 관한
# 규정"(K-GMP 고시) 자체는 카탈로그에 없다(있는 건 "의료기기 제조 및 품질관리..." 변형뿐
# — 확인 완료, 08-04). 접두 일치 후 title_ko 정확 일치를 시도하되 0건이면 "" 로 남긴다.
_MFDS_GMP_REG_PREFIX = "의약품 제조 및 품질관리에 관한 규정"

# 자료실 카탈로그 중 항목 official_url 이 개별 문서가 아니라 공식 카탈로그 페이지로
# 수렴하는 것(ICH) — library_catalog.html 제목 링크와 동일 우선순위(pdf_url 우선)를 쓴다.
_REG_REF_LINK_LABEL_CATALOGS = {"ich"}


def _reg_ref_norm(s: str) -> str:
    """[용어사전 심화] 카탈로그 code 비교용 정규화 — 소문자화 + 영숫자만 남김.

    공백·쉼표·마침표 등 구두점 차이(카탈로그 "Part I, Chapter 1" vs 라벨 "Part I
    Chapter 1")를 흡수한다. 순수 함수."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _reg_ref_catalog_link(item: dict[str, Any], catalog_key: str) -> str:
    """[용어사전 심화] 매칭된 카탈로그 항목 1건 → 표시용 URL.

    자료실 페이지(library_catalog.html)가 제목 링크에 쓰는 것과 동일한 우선순위:
    link_label 있는 카탈로그(ICH)는 official_url 이 카탈로그 페이지로 수렴하므로
    pdf_url 우선(없으면 무링크 — official_url 로 대체하지 않는다, 오인 방지). 그 외
    카탈로그는 official_url 우선(이 저장소 데이터셋에서 pdf_url 과 대개 동일)."""
    if catalog_key in _REG_REF_LINK_LABEL_CATALOGS:
        return item.get("pdf_url") or ""
    return item.get("official_url") or item.get("pdf_url") or ""


def _load_reg_ref_catalogs(library_dir: Path = LIBRARY_DIR) -> dict[str, list[dict[str, Any]]]:
    """[용어사전 심화] reg_refs URL 해석에 쓰는 자료실 카탈로그 원본 items 로드.

    _catalog_view(자료실 페이지 뷰모델)를 재사용하지 않는 이유는 그쪽이 표시용 가공
    (doc_type 라벨 치환·정렬·그룹핑)을 거치기 때문 — code·official_url·pdf_url 원본이
    필요한 이 해석기는 raw items 를 직접 읽는 편이 더 안전하다. 파일 부재 카탈로그는
    빈 리스트(무매치 → 무링크, 예외 아님). 결정론(파일 byte 파생, 네트워크 0)."""
    files = {"ich": "ich.json", "eu_gmp": "eu_gmp.json", "pics": "pics.json",
             "who": "who.json", "mfds": "mfds.json"}
    catalogs: dict[str, list[dict[str, Any]]] = {}
    for key, fname in files.items():
        p = library_dir / fname
        catalogs[key] = json.loads(p.read_text(encoding="utf-8"))["items"] if p.is_file() else []
    return catalogs


def _reg_ref_url(label: str, catalogs: dict[str, list[dict[str, Any]]]) -> str:
    """[용어사전 심화 B2] 관련 조항 라벨 → 공식 원문 URL(자료실 카탈로그 재사용).

    R1 21 CFR → eCFR(trends.js ecfrHref 와 동일 형태) · R2 ICH/R3 EU GMP/R4 PIC/S/
    R5 WHO → 카탈로그 code 정확 일치(정확히 1건일 때만) · R6 국내(식약처) → title_ko
    특정 시도 · R7 그 외 → "". 매치 없거나 모호(0건·2건 이상)하면 "" — 무링크이 안전
    (틀린 링크가 무링크보다 나쁘다). 순수 함수(창작 0 — 라벨/카탈로그 값만 조회)."""
    label = (label or "").strip()
    if not label:
        return ""

    # R1: 21 CFR
    if label.startswith("21 CFR"):
        if _REG_REF_EN_DASH in label:
            return ""
        m = re.match(r"^21 CFR Part (\d+)$", label)
        if m:
            return f"https://www.ecfr.gov/current/title-21/part-{m.group(1)}"
        m = re.match(r"^21 CFR (\d+\.\d+)", label)
        if m:
            return f"https://www.ecfr.gov/current/title-21/section-{m.group(1)}"
        return ""

    # R2: ICH — 문서코드 추출("Q9(R1)"→"Q9") 후 code 정확 일치.
    if label.startswith("ICH "):
        rest = label[len("ICH "):].strip()
        first = rest.split()[0] if rest.split() else ""
        code = re.sub(r"\(R\d+\)", "", first).strip()
        matches = [it for it in catalogs.get("ich", []) if (it.get("code") or "") == code]
        if len(matches) == 1:
            return _reg_ref_catalog_link(matches[0], "ich")
        return ""

    # R3: EU GMP — "EU GMP " 접두·§ 이후 제거 후 정규화 정확 일치.
    if label.startswith("EU GMP "):
        doc_part = label[len("EU GMP "):].split("§", 1)[0].strip()
        target = _reg_ref_norm(doc_part)
        matches = [it for it in catalogs.get("eu_gmp", [])
                   if _reg_ref_norm(it.get("code") or "") == target]
        if len(matches) == 1:
            return _reg_ref_catalog_link(matches[0], "eu_gmp")
        return ""

    # R4: PIC/S — "PIC/S " 접두·§ 이후 제거 후 code 정확 일치(공백 정리만, 대소문자 유지).
    if label.startswith("PIC/S "):
        doc_part = label[len("PIC/S "):].split("§", 1)[0].strip()
        matches = [it for it in catalogs.get("pics", [])
                   if (it.get("code") or "").strip() == doc_part]
        if len(matches) == 1:
            return _reg_ref_catalog_link(matches[0], "pics")
        return ""

    # R5: WHO — "TRS <report> Annex <n>" 형태로 정규화 후 code 정규화 정확 일치.
    if label.startswith("WHO "):
        m_trs = re.search(r"TRS\s+(\d+)", label) or re.search(
            r"Technical Report Series No\.?\s*(\d+)", label)
        m_annex = re.search(r"Annex\s+(\d+)", label)
        if m_trs and m_annex:
            target = _reg_ref_norm(f"TRS {m_trs.group(1)} Annex {m_annex.group(1)}")
            matches = [it for it in catalogs.get("who", [])
                       if _reg_ref_norm(it.get("code") or "") == target]
            if len(matches) == 1:
                return _reg_ref_catalog_link(matches[0], "who")
        return ""

    # R6: 국내(식약처) — code 필드가 없어 title_ko 로 특정 시도(대괄호 부표 표기 제거 후
    # 정확 일치). 확실히 1건일 때만 링크 — 애매하면 "".
    if label.startswith(_MFDS_GMP_REG_PREFIX):
        base = re.sub(r"\s*\[[^\]]*\]\s*$", "", label).strip()
        matches = [it for it in catalogs.get("mfds", [])
                   if (it.get("title_ko") or "").strip() == base]
        if len(matches) == 1:
            return _reg_ref_catalog_link(matches[0], "mfds")
        return ""

    # R7: 그 외(MHRA 등) — 자료실에 해당 카탈로그가 없다.
    return ""


def _reg_ref_view(item: Any, catalogs: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, str] | None:
    """[용어사전 심화] reg_refs 항목 1건 → {"label","url"} 정규화(무변형·안전 URL 게이트만).

    문자열이면 label=문자열, url 은 _reg_ref_url 해석기로 채운다(B2). dict 면 label/url
    을 각각 strip/_safe_url 게이트만 거쳐 통과하되, **데이터의 url 이 비어 있을 때만**
    해석기로 보강한다(데이터가 코드를 이긴다 — 이미 명시된 url 은 그대로 우선). catalogs
    미지정(None) 은 빈 카탈로그 취급(무매치 → ""·기존 호출부 호환). label 이 빈 항목
    (빈 문자열·공백뿐)은 조용히 제외(None) — 호출부가 필터."""
    cat = catalogs or {}
    if isinstance(item, str):
        label = item.strip()
        return {"label": label, "url": _reg_ref_url(label, cat)} if label else None
    if isinstance(item, dict):
        label = (item.get("label") or "").strip()
        if not label:
            return None
        url = _safe_url(item.get("url") or "")
        if not url:
            url = _reg_ref_url(label, cat)
        return {"label": label, "url": url}
    return None


# ── [용어사전 A1] 동의어(aliases) 표시 중복판정 ─────────────────────────────────
def _glossary_alias_norm(s: str) -> str:
    """[용어사전 A1] 동의어 표시 중복판정 전용 정규화(소문자화 + 하이픈·공백 제거). 순수 함수.

    표제어가 `Back-up` 인데 동의어 `backup` 을 화면에 "다른 표현"으로 나란히 보여주면
    표기 차이(하이픈·띄어쓰기)만 있는 같은 말을 두 번 보여주는 잡음이다. 반대로
    `Retention Sample` 에 대한 `reserve sample` 은 하이픈·공백을 지워도 표제어 안에
    없는 진짜 다른 이름이라 감추면 안 된다 — FDA 문서에서 그 표현을 보고 온 사용자가
    화면에서 알아볼 단서다. 이 정규화는 감춤 판정에만 쓰며 표시 텍스트 자체(term_en/
    term_ko/aliases 원문)는 무변형으로 남는다."""
    return re.sub(r"[-\s]+", "", s.lower())


def build_glossary_view(
    terms: list[dict[str, Any]],
    reg_ref_catalogs: dict[str, list[dict[str, Any]]] | None = None,
    cases: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """용어 리스트 → 초성 그룹 뷰모델(무변형 — 값 재작성 0, 파생만).

    related 는 데이터 순서 그대로 유지하며 존재하는 id 만 term_ko 라벨과 함께 통과(고아
    참조는 조용히 제외). search 는 term_ko/term_en/easy_ko(+detail_ko 있을 때만, +aliases
    있을 때만)를 소문자 결합(클라이언트 필터 입력값 — 표시 텍스트 무변형, 검색 대상
    문자열만 별도 파생). detail_ko(실무 맥락 설명)·reg_refs(관련 조항 참조)는 병렬
    작업자가 데이터에 추가할 선택 필드 — 있으면 통과(reg_refs 는 _reg_ref_view 로
    정규화, reg_ref_catalogs 가 있으면 B2 URL 해석기가 자료실 카탈로그로 링크를 채운다),
    없으면 빈 값이라 기존 렌더와 byte 동일(search 에도 잉여 공백 미추가). reg_ref_catalogs
    미지정 시 URL 은 모두 ""(호출부 호환 — 기존 동작 그대로). 그룹·용어 정렬 결정론.

    aliases([A1] 동의어, glossary.json 정본 — 여기서 수정 안 함)는 검색용과 표시용이
    다르다. search 에는 항상 전량 포함(사용자가 FDA 표현으로 검색해도 걸리도록).
    뷰모델 "aliases" 키(표시용)는 _glossary_alias_norm 판정으로 표제어(term_ko 또는
    term_en)와 하이픈·공백 차이만 있는 표현만 골라 제외한 나머지만 담는다(원본 순서
    유지). aliases 없으면 빈 리스트라 템플릿 {% if t.aliases %} 게이트로 조용히 생략,
    search 도 기존과 byte 동일.

    cases(glossary_cases.json items, id→item)는 [C1] 사례 링크 심화 필드 — 있으면
    case_q/case_findings/case_count_label/case_href 를 채운다(id 가 cases 에 없으면
    빈 값 → 템플릿 {% if t.case_findings %} 게이트로 조용히 생략, 기존 렌더와 byte
    동일). 숫자 포맷(천단위 쉼표)은 여기서 만든다(템플릿 필터는 로케일 영향을 받을 수
    있다). URL 인코딩은 _url_quote(q, safe="")(urllib.parse.quote 와 byte-동일 출력을
    자체 구현 — 순수성 게이트가 render.py 안의 urllib import 를 막는다. 결정론 — 한글
    검색어 포함)."""
    label_by_id = {t["id"]: t["term_ko"] for t in terms}
    cases = cases or {}

    def _term_view(t: dict[str, Any]) -> dict[str, Any]:
        related = [{"id": r, "term_ko": label_by_id[r]}
                   for r in (t.get("related") or []) if r in label_by_id]
        reg_refs = [v for v in (_reg_ref_view(r, reg_ref_catalogs) for r in (t.get("reg_refs") or [])) if v]
        search_parts = [t["term_ko"], t["term_en"], t["easy_ko"]]
        detail_ko = t.get("detail_ko") or ""
        if detail_ko:
            search_parts.append(detail_ko)
        aliases = list(t.get("aliases") or [])
        if aliases:
            search_parts.extend(aliases)
        # [A1] 표시용: 표제어와 하이픈·공백 차이만 있는 동의어는 잡음이라 제외(감춤은
        # 화면뿐 — search 는 위에서 이미 전량 포함했다). 순서는 데이터 원본 유지.
        norm_ko = _glossary_alias_norm(t["term_ko"])
        norm_en = _glossary_alias_norm(t["term_en"])
        display_aliases = [a for a in aliases
                            if _glossary_alias_norm(a) not in norm_ko
                            and _glossary_alias_norm(a) not in norm_en]
        case = cases.get(t["id"]) or {}
        case_q = str(case.get("q") or "")
        try:
            case_findings = int(case.get("findings") or 0)
        except (TypeError, ValueError):
            case_findings = 0
        if case_findings < 0:
            case_findings = 0
        return {
            "id": t["id"],
            "term_ko": t["term_ko"],
            "term_en": t["term_en"],
            "easy_ko": t["easy_ko"],
            "definition_source": t["definition_source"],
            # v2: 출처 공식 링크(있으면 출처 표기를 새 탭 링크로 — 값 무변형·안전 URL 만).
            "source_url": _safe_url(t.get("source_url") or ""),
            "related": related,
            "bucket": _glossary_bucket(t["term_ko"]),
            "search": " ".join(search_parts).lower(),
            # v3(8차 웨이브 A): 심화 필드 — 부재 시 ""/[] 라 템플릿 {% if %} 게이트로 조용히 생략.
            "detail_ko": detail_ko,
            "reg_refs": reg_refs,
            # [A1] 표시용 동의어(표제어와 하이픈·공백만 다른 것 제외) — 부재/전량제외 시
            # 빈 리스트라 템플릿 {% if t.aliases %} 게이트로 조용히 생략.
            "aliases": display_aliases,
            # [C1] 용어→사례 링크: glossary_cases.json 미제공/미매칭이면 전부 빈 값.
            "case_q": case_q,
            "case_findings": case_findings,
            "case_count_label": f"{case_findings:,}" if case_findings else "",
            "case_href": (f"findings/index.html?q={_url_quote(case_q, safe='')}"
                          if case_q else ""),
        }

    views = [_term_view(t) for t in terms]
    order = {b: i for i, b in enumerate(_GLOSSARY_BUCKET_ORDER)}
    groups_map: dict[str, list[dict[str, Any]]] = {}
    for v in views:
        groups_map.setdefault(v["bucket"], []).append(v)
    groups: list[dict[str, Any]] = []
    for idx, bucket in enumerate(sorted(groups_map, key=lambda b: (order.get(b, 99), b))):
        items = sorted(groups_map[bucket], key=lambda v: v["term_ko"])
        # 그룹 앵커는 결정론 인덱스 파생(유니코드/en-dash 를 href 에 넣지 않음).
        groups.append({"bucket": bucket, "anchor": f"grp-{idx}", "terms": items})
    return {"groups": groups, "total": len(views),
            "buckets": [{"bucket": g["bucket"], "anchor": g["anchor"]} for g in groups]}


# ── [용어사전 낱개] 검색 유입용 per-term 페이지의 SEO 파생(결정론 — 값 무변형 절단·조립) ──
# 색인 페이지 1건으로는 226 어가 URL 하나에 묶여 "OOS 뜻" 같은 실제 검색어에 걸릴 대상이
# 없다. 용어당 페이지를 내고 각각에 description·JSON-LD 를 준다. 문구는 **생성하지 않고**
# 정본(easy_ko)을 자른다 — LLM 슬롯 아님.
_GLOSSARY_META_MAX = 155


def glossary_term_description(term: dict[str, Any]) -> str:
    """meta description — easy_ko 를 어절 경계에서 절단(정본 무변형·now()/난수 0).

    155 자를 넘으면 마지막 공백까지만 남기고 말줄임표를 붙인다. 공백이 없으면(붙여쓴 긴
    한 덩어리) 그냥 자른다 — 한글은 어절이 짧아 실제로는 거의 항상 공백이 있다.

    [SERP 차별화] 뷰모델 입력(term 에 case_findings>0, glossary_cases.json 실측치)이면
    사례 건수 문장을 뒤에 덧붙인다 — "X 뜻" 검색 결과에서 정의만 있는 사전류와 달리
    실제 지적사례가 연결된 사전임이 스니펫에 드러나야 클릭을 딴다(GSC 실측: 평균 순위
    9.4·CTR 2%). 정의부가 항상 앞이라 기존 접두 계약이 유지되고, 전체 길이는 접미사를
    포함해 상한을 지킨다. 원본 glossary.json(raw)에는 case_findings 가 없으므로 raw
    입력에서는 기존과 byte 동일하다. DefinedTerm JSON-LD 의 description 은 이 함수를
    쓰지 않는다 — 구조화 데이터의 정의문은 순수 정의(easy_ko)로 남아야 한다.
    """
    text = " ".join((term.get("easy_ko") or "").split())
    try:
        case_findings = int(term.get("case_findings") or 0)
    except (TypeError, ValueError):
        case_findings = 0
    suffix = (f" 실제 지적사례 {case_findings:,}건과 공식 출처를 함께 정리했습니다."
              if case_findings > 0 else "")
    budget = _GLOSSARY_META_MAX - len(suffix)
    if len(text) > budget:
        cut = text[:budget]
        head, sep, _ = cut.rpartition(" ")
        text = (head if sep else cut).rstrip(" ,·") + "…"
    return text + suffix


# [SERP 절단] 구글은 제목을 픽셀 폭으로 자른다. 초기 제목은 영문 정식명을 통째로 넣어
# `{한글}({영문 정식명}) 뜻 · GRM 규제 용어사전` 이었는데, 영문 정식명 안에 약어가 괄호로
# 들어 있는 용어가 226 중 51 개라 제목이 **이중 괄호 + 장문**이 됐다(최대 94 자·45 자 초과
# 69 개). 그 결과 사용자가 실제로 친 검색어 토큰이 절단선 뒤로 밀린다 — 예:
#   "capa 뜻" → 시정 및 예방조치(Corrective and Preventive Action (CAPA)) 뜻 · GRM 규제…
#                                                          ^^^^ 여기가 잘려 CAPA 가 안 보인다
# 그래서 제목에는 **검색어가 되는 짧은 형태**(약어가 있으면 약어)만 남긴다. 영문 정식명은
# h1·본문·JSON-LD 에 그대로 있으므로 정보 손실이 아니라 제목에서만 접는 것이다.
_GLOSSARY_ACRONYM = re.compile(r"^[A-Z][A-Za-z0-9./-]{1,9}$")


def glossary_title_en(term_ko: str, term_en: str) -> str:
    """제목 괄호에 넣을 짧은 영문 — 없으면 빈 문자열(괄호 자체를 생략).

    ① 한글 용어에 이미 괄호가 있으면(`품질관리부서(QCU)`·`공조(공기처리)`) 아무것도 붙이지
       않는다 — 붙이면 `A(B)(C)` 꼴 이중 괄호가 된다.
    ② 영문 안 괄호가 약어면(`… (CAPA)`·`… (APS, Media Fill)`) 그 약어를 쓴다. 약어 판정은
       대문자로 시작하고 **첫 글자 뒤에도 대문자가 있는** 짧은 토큰 — `(or Lot)`·
       `(Non-viable)` 같은 일반어 괄호를 배제한다.
    ③ 그 외에는 괄호구를 제거한 본문을 쓴다(`Total Particle (Non-viable) Monitoring`
       → `Total Particle Monitoring`). 뒤에 `/` 이형이 붙으면 첫 이름만 남긴다.
    """
    if "(" in term_ko:
        return ""
    for inner in re.findall(r"\(([^()]*)\)", term_en):
        head = inner.split(",")[0].strip()
        if _GLOSSARY_ACRONYM.match(head) and any(c.isupper() for c in head[1:]):
            return head
    base = re.sub(r"\s*\([^()]*\)", "", term_en).split("/")[0].strip()
    return base or term_en.strip()


# ── [용어사전 낱개 — 순위] 실제 지적사항 인용 ────────────────────────────────────
# 용어 페이지 본문이 390~530 자뿐이라 "정의 한 줄짜리 사전"과 구별되지 않는다(GSC 실측
# 2026-08-17: 용어 쿼리 평균순위 9~11). 우리에게만 있는 자산 — 실제 규제 지적 문장 —
# 은 정작 "N 건 보기" 링크의 **숫자로만** 실려 있었다. 그 문장을 본문에 싣는다.
#
# 정직성 규율(이게 이 기능의 전부다):
#   · 표시 문구는 "이 용어가 등장한 실제 지적사항" — **이 용어에 관한** 지적이라고 하지
#     않는다. 인용문 안에 토큰이 그대로 보이므로 독자가 즉시 검증할 수 있는 주장만 한다.
#   · 업체명은 싣지 않는다. 용어 페이지는 용어를 설명하는 곳이고, 업체는 링크로 잇는
#     문서 페이지의 주제다 — 실명 기록 표면을 127 장 더 만들지 않는다.
#   · 원천은 커밋된 `findings_docs.json` 이다. 렌더러는 네트워크를 타지 않는다.
#
# 후보를 그냥 상위 N 개 뽑으면 안 되는 이유(실측으로 셋 다 발생했다):
#   ① 문서 단위 검색 상위가 최신순이라 **OOS·CAPA·시정조치·가독성·GMP 다섯 용어에서
#      같은 문서가 1 위**였다 → 123 장에 같은 본문이 깔린다.
#   ② `21 CFR 211.22` 류 규제 상용구가 문서마다 반복돼 **한 용어에 똑같은 문장 3 개**가
#      뽑혔다 → 여러 문서에 반복되는 문장형을 보일러플레이트로 보고 배제한다.
#   ③ `제조, 가공, 포장 또는 보관` 열거에 걸린 설비 조항이 `포장` 사례로 뽑혔다 →
#      토큰이 **열거 안에만** 있으면 우연한 언급이라 배제한다.
_GLOSSARY_CASE_MIN = 2          # 2 건도 못 채우면 섹션 자체를 내지 않는다(빈약한 것보다 없는 게 낫다)
_GLOSSARY_CASE_MAX = 3
_GLOSSARY_QUOTE_MIN = 60
_GLOSSARY_QUOTE_MAX = 300
_GLOSSARY_BOILER_DOCS = 4       # 문장형이 이 수 이상의 문서에 반복되면 규제 상용구
_GLOSSARY_SENT_SPLIT = re.compile(r"(?<=다\.)\s*|(?<=[.!?])\s+")
_GLOSSARY_SHAPE_STRIP = re.compile(r"[\d(){}\[\]§·,．、\s]+")
_GLOSSARY_ENUM_L = re.compile(r"(?:[,·]|또는|및)\s*$")
_GLOSSARY_ENUM_R = re.compile(r"^\s*(?:[,·]|또는|및)")


def _glossary_sentences(text: str) -> list[str]:
    return [s.strip() for s in _GLOSSARY_SENT_SPLIT.split(text or "") if s.strip()]


def _glossary_shape(sentence: str) -> str:
    """상용구 판정 키 — 숫자·괄호·조문기호·공백을 지운 앞부분(번역 차이는 남는다)."""
    return _GLOSSARY_SHAPE_STRIP.sub("", sentence)[:80]


def _glossary_incidental(sentence: str, token: str) -> bool:
    """토큰이 열거(`제조, 가공, 포장 또는 보관`) 안에만 있으면 우연한 언급이다."""
    for m in re.finditer(re.escape(token), sentence):
        left = sentence[max(0, m.start() - 8):m.start()]
        right = sentence[m.end():m.end() + 8]
        if not (_GLOSSARY_ENUM_L.search(left) and _GLOSSARY_ENUM_R.match(right)):
            return False
    return True


def glossary_case_probes(term: dict[str, Any], case_q: str = "") -> list[str]:
    """인용문에서 **눈으로 확인 가능한** 토큰만 — 사람이 검수한 q, 한글 표제어 분절, 약어."""
    out: list[str] = []
    for cand in [case_q, *(term.get("term_ko") or "").split("·")]:
        cand = (cand or "").strip()
        if len(cand) >= 2 and cand not in out:
            out.append(cand)
    m = re.search(r"\(([A-Z][A-Za-z0-9/-]{1,9})\)", term.get("term_en") or "")
    if m and m.group(1) not in out:
        out.append(m.group(1))
    return out


def build_glossary_case_excerpts(
    terms: list[dict[str, Any]],
    docs_payload: "dict[str, Any] | None",
    cases: "dict[str, dict[str, Any]] | None" = None,
) -> dict[str, list[dict[str, Any]]]:
    """{term_id: [인용 …]} — 결정론(입력 순서 고정·now()/난수 0·네트워크 0).

    희소한 용어부터 배정한다. 흔한 용어가 먼저 집어가면 사례가 몇 건뿐인 용어가 빈손이
    되는데, 반대로 하면 양쪽 다 채워진다(finding 은 용어 간 중복 배정하지 않는다 —
    같은 문장이 여러 페이지에 실리면 중복 본문이다).
    """
    documents = (docs_payload or {}).get("documents") or []
    if not documents:
        return {}

    # 문장 분할은 **한 번만** 한다. 용어마다 다시 쪼개면 21,347 건 × 226 어라 렌더·테스트가
    # 폭발한다(대량 페이지가 테스트 시간을 터뜨린 전례가 있다).
    shape_docs: dict[str, set[str]] = {}
    index: list[tuple[dict[str, Any], str, list[str]]] = []   # (doc, finding_id, 문장들)
    for doc in documents:
        for finding in doc.get("findings") or []:
            sents = [s for s in _glossary_sentences(finding.get("text_ko") or "")
                     if _GLOSSARY_QUOTE_MIN <= len(s) <= _GLOSSARY_QUOTE_MAX]
            if not sents:
                continue
            for sent in sents:
                shape_docs.setdefault(_glossary_shape(sent), set()).add(doc["document_id"])
            index.append((doc, finding.get("finding_id") or "", sents))
    boiler = {k for k, v in shape_docs.items() if len(v) >= _GLOSSARY_BOILER_DOCS}
    index = [(doc, fid, [s for s in sents if _glossary_shape(s) not in boiler])
             for doc, fid, sents in index]

    def _candidates(term: dict[str, Any]) -> list[dict[str, Any]]:
        probes = glossary_case_probes(term, ((cases or {}).get(term["id"]) or {}).get("q", ""))
        found: list[dict[str, Any]] = []
        seen_docs: set[str] = set()
        for doc, fid, sents in index:              # 문서당 최대 1 건 — 한 문서로 도배 금지
            if doc["document_id"] in seen_docs:
                continue
            for sent in sents:
                tok = next((p for p in probes
                            if p in sent and not _glossary_incidental(sent, p)), None)
                if tok:
                    found.append({"quote": sent, "token": tok,
                                  "agency": doc.get("agency") or "",
                                  "source": doc.get("source") or "",
                                  "published_date": doc.get("published_date") or "",
                                  "doc_href": f"findings/doc/{doc['slug']}/",
                                  "finding_id": fid})
                    seen_docs.add(doc["document_id"])
                    break
        return found

    pool = {t["id"]: _candidates(t) for t in terms}
    used: set[str] = set()
    used_quotes: set[str] = set()      # finding 이 달라도 문장이 같을 수 있다(실측) — 문장으로도 막는다
    out: dict[str, list[dict[str, Any]]] = {}
    for tid in sorted(pool, key=lambda k: (len(pool[k]), k)):
        picked: list[dict[str, Any]] = []
        for cand in pool[tid]:
            if cand["finding_id"] in used or cand["quote"] in used_quotes:
                continue
            used.add(cand["finding_id"])
            used_quotes.add(cand["quote"])
            picked.append(cand)
            if len(picked) == _GLOSSARY_CASE_MAX:
                break
        if len(picked) >= _GLOSSARY_CASE_MIN:
            out[tid] = picked
    return out


# ── [내부 링크] 문서 본문 → 용어 페이지 자동 링크 ────────────────────────────────
# 용어 페이지 인바운드가 평균 4 개고 **45 개는 색인 페이지 1 개뿐**이다(2026-08-17 실측).
# 문서 페이지 3,202 장은 용어로 가는 링크를 하나도 갖고 있지 않았다 — 사이트에서 가장 많은
# 페이지 무리가 가장 얇은 페이지 무리로 권위를 전혀 흘려보내지 않고 있었다.
#
# 다만 본문에 링크를 뿌리면 안 된다. 규칙은 **희소한 용어 우선**이다:
#   · 페이지에 등장한 용어를 코퍼스 문서빈도(df) 오름차순으로 세워 상위 N 개만 링크한다.
#   · 그러면 `PUPSIT`·`CCIT` 같은 특수 용어가 먼저 걸리고 `품질`·`제조` 처럼 어디에나 있는
#     말은 자연히 밀린다 — 손으로 불용어 목록을 적을 필요가 없다(손목록은 반드시 낡는다).
#   · 링크가 필요한 건 인바운드 1 개짜리 롱테일 용어들인데, 희소 우선이 정확히 그 쪽이다.
#   · 한 페이지에서 한 용어는 **첫 등장 1 회만** 링크한다(같은 말에 반복 링크는 잡음).
#
# 원문 무결성: 텍스트는 한 글자도 바뀌지 않는다. escape 한 조각들 사이에 `<a>` 만 끼우고
# `Markup` 으로 돌려준다 — 태그를 벗기면 입력과 byte 동일이어야 한다(테스트가 검사).
_DOC_TERM_LINK_MAX = 8          # 한 문서 페이지에서 링크할 용어 수 상한
_DOC_TERM_MIN_LEN = 2
_LATIN_TOKEN = re.compile(r"^[A-Za-z0-9/-]+$")


def build_doc_term_link_index(terms: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """[(표면형, term_id)] — 긴 표면형 우선(`시정 및 예방조치` 가 `예방조치` 를 이긴다)."""
    surfaces: dict[str, str] = {}
    for t in terms:
        cands = [p.strip() for p in (t.get("term_ko") or "").split("·")]
        m = re.search(r"\(([A-Z][A-Za-z0-9/-]{1,9})\)", t.get("term_en") or "")
        if m:
            cands.append(m.group(1))
        for c in cands:
            if len(c) >= _DOC_TERM_MIN_LEN and c not in surfaces:
                surfaces[c] = t["id"]
    return sorted(surfaces.items(), key=lambda kv: (-len(kv[0]), kv[0]))


def build_doc_term_doc_freq(
    index: list[tuple[str, str]], documents: list[dict[str, Any]],
) -> dict[str, int]:
    """term_id → 그 용어가 등장한 문서 수. 희소도 판정의 유일한 근거(사람 목록 0)."""
    freq: dict[str, int] = {tid: 0 for _, tid in index}
    for doc in documents:
        blob = "\n".join(f.get("text_ko") or "" for f in doc.get("findings") or [])
        hit: set[str] = set()
        for surface, tid in index:
            if tid not in hit and _doc_term_find(blob, surface) >= 0:
                hit.add(tid)
        for tid in hit:
            freq[tid] += 1
    return freq


# [오탐 차단] 한글은 낱말 경계가 없어 표면형이 **명사가 아닌 자리**에도 걸린다. 27 개
# 짧은 한글 표면형을 실제 문맥과 함께 전수로 읽어 보니 위험은 두 갈래뿐이었고, 둘 다
# 손목록 없이 규칙으로 잡힌다(손목록은 반드시 낡는다):
#   ① 목적구·목적어 — `…하기 위해`(위해=harm 아님)·`…을 기록`·`…를 제조`
#   ② 동사 용법 — `기록하고`·`제조된`·`교정되지`(대부분의 한자어 명사가 `하다/되다` 를 붙는다)
# 조사 결합률로 가르려던 시도는 실패했다(`품질관리` 0.7%·`일탈` 8.4% — 복합명사라 조사가
# 안 붙는다). 앞뒤 두 글자만 보는 이 규칙이 실측 11/11 정확했다.
_DOC_TERM_PRE_VERBISH = re.compile(r"(?:기|을|를)\s$")
_DOC_TERM_POST_VERBISH = re.compile(r"^(?:하|되|한|된|할|합|됩)")


def _doc_term_is_verbish(text: str, start: int, end: int) -> bool:
    """그 자리가 명사가 아니라 목적구/동사 용법이면 True(링크하지 않는다)."""
    return bool(_DOC_TERM_PRE_VERBISH.search(text[max(0, start - 2):start])
                or _DOC_TERM_POST_VERBISH.match(text[end:end + 2]))


def _doc_term_find(text: str, surface: str, start: int = 0) -> int:
    """등장 위치. 라틴 표면형은 낱말 경계를 요구한다(`API` 가 `RAPID` 안에 걸리면 안 된다)."""
    if _LATIN_TOKEN.match(surface):
        m = re.compile(rf"(?<![A-Za-z0-9]){re.escape(surface)}(?![A-Za-z0-9])").search(
            text, start)
        return m.start() if m else -1
    pos = text.find(surface, start)
    while pos >= 0 and _doc_term_is_verbish(text, pos, pos + len(surface)):
        pos = text.find(surface, pos + 1)
    return pos


def select_doc_term_links(
    doc: dict[str, Any],
    index: list[tuple[str, str]],
    doc_freq: dict[str, int],
    limit: int = _DOC_TERM_LINK_MAX,
) -> list[tuple[str, str]]:
    """이 문서에서 링크할 [(표면형, term_id)] — 희소(df 낮은) 용어 우선, 용어당 1 개."""
    blob = "\n".join(f.get("text_ko") or "" for f in doc.get("findings") or [])
    best: dict[str, str] = {}
    for surface, tid in index:                     # index 가 긴 표면형 우선이라 첫 매치가 최장
        if tid not in best and _doc_term_find(blob, surface) >= 0:
            best[tid] = surface
    ranked = sorted(best.items(), key=lambda kv: (doc_freq.get(kv[0], 0), kv[0]))
    return [(surface, tid) for tid, surface in ranked[:limit]]


def link_terms_in_text(
    text: str, selected: list[tuple[str, str]], rel_root: str, used: set[str],
) -> Markup:
    """본문 1 조각에 용어 링크를 끼운다 — 텍스트 무변형(escape 후 `<a>` 만 삽입).

    `used` 는 **페이지 단위**로 공유한다(지적 5 건에 같은 용어가 나와도 링크는 첫 곳 하나).
    """
    spans: list[tuple[int, int, str]] = []
    for surface, tid in selected:
        if tid in used:
            continue
        pos = _doc_term_find(text, surface)
        while pos >= 0 and any(s < pos + len(surface) and pos < e for s, e, _ in spans):
            pos = _doc_term_find(text, surface, pos + 1)
        if pos >= 0:
            spans.append((pos, pos + len(surface), tid))
            used.add(tid)
    if not spans:
        return Markup(str(_escape(text)))
    spans.sort()
    out: list[str] = []
    cursor = 0
    for start, end, tid in spans:
        out.append(str(_escape(text[cursor:start])))
        out.append(f'<a class="fd-term" href="{rel_root}glossary/{tid}/">'
                   f'{_escape(text[start:end])}</a>')
        cursor = end
    out.append(str(_escape(text[cursor:])))
    return Markup("".join(out))


def glossary_term_page_title(term: dict[str, Any]) -> str:
    """`{한글}({짧은 영문}) 뜻 · GRM 용어사전` — 검색어 형태("OOS 뜻")를 앞쪽에 둔다."""
    term_ko = term.get("term_ko") or ""
    short_en = glossary_title_en(term_ko, term.get("term_en") or "")
    head = f"{term_ko}({short_en})" if short_en else term_ko
    return f"{head} 뜻 · GRM 용어사전"


# build_glossary_term_json_ld 는 SITE_BASE_URL 정의 이후(SEO 섹션)에 있다 — 기본 인자로
# 모듈 상수를 쓰기 때문에 정의 순서가 강제된다.


# ── [검색 유입] 분류·국가·기관 모음 페이지 — 커밋 데이터 로드·뷰모델 ─────────────────
# `/findings/` 는 런타임 RPC 로 결과를 불러오는 검색 앱이라 HTML 에 지적 본문이 없다.
# 그래서 공개 24,797건 전체가 검색엔진에 색인 대상 0개였다(2026-08-12 실측). 축마다 정적
# 표면을 만들어 각 축이 색인 대상이 되게 한다. 데이터는 `findings_facets_refresh.py` 가
# anon RPC(=공개 RLS 그대로)로 떠서 커밋한 정본이고, 렌더러는 네트워크를 타지 않는다.
#
# 축 메타(제목·경로·검색 파라미터)는 여기가 정본이다 — 축은 셋 고정이고 URL 경로를
# 정하는 층이 여기뿐이라, 데이터에 실으면 오히려 두 곳이 갈라진다.
FACET_AXES: dict[str, dict[str, str]] = {
    "category": {
        "path": "c",
        "kick": "By Category",
        "title": "분류별 지적사항",
        "query_key": "cat",
        "headline_suffix": "지적사항",
        "lede_prefix": "이 분류로",
        "sibling_title": "다른 분류 보기",
        "index_lede": "규제기관이 지적한 내용을 주제별로 묶었습니다. 무균공정·시험실 관리·일탈 조사처럼 실사에서 반복되는 축이라, 우리 현장의 취약 지점과 대조해 보실 수 있습니다.",
    },
    "country": {
        "path": "country",
        "kick": "By Country",
        "title": "국가별 지적사항",
        "query_key": "country",
        "headline_suffix": "제조소 지적사항",
        "lede_prefix": "이 나라에 있는 제조소에서",
        "sibling_title": "다른 국가 보기",
        "index_lede": "지적을 받은 제조소가 어느 나라에 있는지로 묶었습니다. 위탁 제조·원료 공급을 맡긴 지역의 규제 동향을 확인하실 때 쓰실 수 있습니다.",
    },
    "agency": {
        "path": "agency",
        "kick": "By Agency",
        "title": "규제기관별 지적사항",
        "query_key": "agency",
        "headline_suffix": "지적사항",
        "lede_prefix": "이 기관이",
        "sibling_title": "다른 기관 보기",
        "index_lede": "어느 규제기관이 공개한 지적인지로 묶었습니다. 기관마다 문서 형식과 지적의 결이 달라, 대응 준비도 기관 단위로 갈립니다.",
    },
}


def load_findings_facets(path: Path = FINDINGS_FACETS_FILE) -> "dict[str, Any] | None":
    """모음 페이지 정본 로드. 파일 부재 시 None → 섹션이 조용히 꺼진다(load_glossary 동형).

    스키마 버전이 다르면 **조용히 넘어가지 않고 실패**한다 — 모양이 바뀐 데이터를 옛
    템플릿으로 렌더하면 빈 페이지 36장이 라이브로 나가고, 그건 없느니만 못하다.
    """
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    got = obj.get("schema_version")
    if got != "grm-findings-facets/v1":
        raise SystemExit(f"findings_facets 스키마 불일치: {got!r} (기대: grm-findings-facets/v1)")
    return obj


def build_facet_item_view(item: dict[str, Any],
                          doc_slugs: "set[str] | None" = None) -> dict[str, Any]:
    """항목 1건의 표시용 투영 — 값 무변형, 파생은 막대 비율뿐.

    `pct` 는 그 항목 안에서 가장 큰 기관 건수를 100 으로 둔 상대값이다(전체 대비가 아니다
    — 한 기관이 압도적인 축에서 나머지가 전부 0px 로 뭉개지는 것을 막는다).
    """
    agencies = list(item.get("by_agency") or [])
    top = max((int(a.get("c") or 0) for a in agencies), default=0)
    view = dict(item)
    view["by_agency"] = [
        {**a, "pct": round(int(a.get("c") or 0) * 100 / top, 1) if top else 0}
        for a in agencies
    ]
    # 사례 → 문서 페이지 연결. `doc_slugs` 에 있는 것만 잇는다 — 지적 3건 미만 문서는
    # 페이지가 없고, 없는 페이지로 보내는 링크는 무링크보다 나쁘다.
    known = doc_slugs or set()
    view["samples"] = [
        {**s, "doc_slug": (s.get("document_id") or "")
         if (s.get("document_id") or "") in known else ""}
        for s in (item.get("samples") or [])
    ]
    return view


def load_findings_docs(path: Path = FINDINGS_DOCS_FILE) -> "dict[str, Any] | None":
    """문서 단위 페이지 정본 로드. 부재 시 None → 섹션이 조용히 꺼진다.

    스키마 버전 불일치는 실패시킨다 — 모양이 바뀐 데이터를 옛 템플릿으로 렌더하면 실명
    업체 페이지 수천 장이 빈 채로 라이브에 나간다(없느니만 못하다).
    """
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    got = obj.get("schema_version")
    if got != "grm-findings-docs/v1":
        raise SystemExit(f"findings_docs 스키마 불일치: {got!r} (기대: grm-findings-docs/v1)")
    return obj


def doc_source_label(doc: dict[str, Any]) -> str:
    """문서종류 표시값 — 문서종류가 아닌 값이면 **빈 문자열**(= 표기 생략).

    ★식약처 문서 121장은 `source` 가 기관 코드 그대로 `"MFDS"` 다. 그대로 쓰면 제목이
    "명문제약(주) — MFDS 지적사항"이 되어 실재하지 않는 문서종류를 단정하게 된다.
    없는 것을 지어내지도(예: "GMP 실사 보고서") 않고 코드를 노출하지도 않는 유일한 답은
    **말하지 않는 것**이다 — "명문제약(주) 지적사항".
    """
    src = (doc.get("source") or "").strip()
    if not src or src.upper() == (doc.get("agency") or "").strip().upper():
        return ""
    return src


def build_doc_page_titles(documents: list[dict[str, Any]]) -> dict[str, str]:
    """문서 페이지 `<title>` — 슬러그별로 **유일**하게 만든다.

    ★기본형 "{업체} {문서종류} 지적사항 ({발행일})" 은 문서를 유일하게 식별하지 못한다.
    같은 업체·같은 기관·같은 공개일로 나뉜 실사 보고서가 실재하기 때문이다(실측 362장이
    141개 군집으로 겹쳤고 최대 군집은 8장). 제목은 검색 결과의 1차 식별자라, 겹치면
    구글이 하나만 고르고 나머지를 중복으로 떨어뜨린다.

    그래서 겹칠 때만 단계적으로 넓힌다 — ①분류(사람이 읽어 뜻이 있는 구분) → ②문서번호
    (마지막 수단, 반드시 유일). 겹치지 않는 문서의 제목은 건드리지 않는다.
    """
    def base(d: dict[str, Any]) -> str:
        src = doc_source_label(d)
        head = f"{d['firm_name']} {src}".strip() if src else d["firm_name"]
        return f"{head} 지적사항 ({d['published_date']})"

    counts: dict[str, int] = {}
    for d in documents:
        counts[base(d)] = counts.get(base(d), 0) + 1

    out: dict[str, str] = {}
    second: dict[str, list[dict[str, Any]]] = {}
    for d in documents:
        key = base(d)
        if counts[key] == 1:
            out[d["slug"]] = key
            continue
        cats = d.get("categories") or []
        widened = f"{key} · {cats[0]}" if cats else key
        second.setdefault(widened, []).append(d)

    for widened, group in second.items():
        if len(group) == 1:
            out[group[0]["slug"]] = widened
            continue
        for d in group:
            # 문서번호 = document_id 의 마지막 마디(hc-insp-82408 → 82408). id 자체가
            # 유일하므로 이 단계는 반드시 충돌을 해소한다.
            out[d["slug"]] = f"{widened} · 문서 {d['document_id'].rsplit('-', 1)[-1]}"
    return out


def doc_page_description(doc: dict[str, Any], agency_labels: dict[str, str]) -> str:
    """meta description — 누가·언제·몇 건·어떤 주제. 데이터 조립뿐(문구 생성 0).

    ★날짜를 반드시 넣는다. 검색 결과 스니펫에 연도가 없으면 몇 년 전 지적이 현재 상태로
    읽힌다 — 실명 업체 페이지에서 그건 사실 왜곡이다.
    """
    agency = agency_labels.get(doc["agency"], doc["agency"])
    cats = " · ".join(doc.get("categories") or [])
    tail = f" 주요 분류: {cats}." if cats else ""
    src = doc_source_label(doc)
    subject = f"{doc['firm_name']} {src}".strip() if src else doc["firm_name"]
    return (f"{agency}가 {doc['published_date']}에 공개한 {subject} "
            f"지적사항 {len(doc['findings'])}건을 우리말로 정리했습니다.{tail}")


def facet_description(axis_key: str, item: dict[str, Any],
                      agency_labels: dict[str, str]) -> str:
    """meta description — 데이터에서 조립한다(문구 생성 0·now()/난수 0).

    "무엇이 몇 건, 어느 기관에서" 를 앞세운다. 검색 결과에 그대로 노출되는 문장이라
    수식어보다 숫자와 기관명이 클릭을 만든다.
    """
    meta = FACET_AXES[axis_key]
    names = [agency_labels.get(a["v"], a["v"]) for a in (item.get("by_agency") or [])[:3]]
    who = "·".join(n for n in names if n)
    tail = f" {who} 공개 문서 기준." if who else ""
    return (f"{item['label_ko']} {meta['headline_suffix']} {item['findings']:,}건"
            f"(문서 {item['documents']:,}건)을 우리말로 정리했습니다.{tail}")


# ── [주간 퀴즈] 문항 뱅크 로드·뷰모델(결정론 — 값 무변형, 파생은 근거 링크/라벨뿐) ────
# "이번 주" 문항 선택은 렌더러가 하지 않는다(now() 금지·결정론 불가침). 렌더러는 정본
# 뱅크 전 문항을 순서 그대로 페이지에 embed 하고, 클라이언트(assets/quiz.js)가 ISO 주차
# 키로 결정론 회전 선택한다(같은 주 = 전 직원 동일 세트). 사실/정답/해설은 무변형 통과.
_QUIZ_DIFFICULTY_LABEL = {"easy": "기본", "normal": "심화"}
# source_type → 근거 진입 라벨(어디로 가는지). glossary=자체 딥링크, brief/finding=공개 URL.
_QUIZ_SOURCE_KIND = {"glossary": "용어사전", "brief": "주간 브리프", "finding": "지적사항 검색"}
# 기본 노출 문항 수(운영설계 §2.3 — 주 4문항 기본, 운영자가 3~5 범위 조정). 이 상수만
# 바꾸면 클라이언트 회전 로직이 easy 과반·normal 1~2 구성을 자동으로 맞춘다(코드 수정 0).
WEEKLY_QUIZ_COUNT = 4


def load_quiz_bank(path: Path = QUIZ_FILE) -> list[dict[str, Any]] | None:
    """[주간 퀴즈] 정본 문항 뱅크 로드(파일 부재 시 None → 페이지 조용히 생략)."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _quiz_question_view(q: dict[str, Any]) -> dict[str, Any]:
    """문항 1건 → 렌더 뷰모델. 값(질문/선택지/정답/해설)은 무변형, 파생은 난이도 라벨과
    근거 링크 구성뿐. glossary 는 자체 용어사전 딥링크 id(무변형 통과 — 템플릿이 rel_root
    로 조립), brief/finding 은 공개 URL(_safe_url 스킴 게이트만). 순수·결정론."""
    st = q.get("source_type", "")
    ref = str(q.get("source_ref") or "")
    is_glossary = st == "glossary"
    return {
        "id": q.get("id", ""),
        "question_ko": q.get("question_ko", ""),
        "choices": list(q.get("choices") or []),
        "answer_index": q.get("answer_index"),
        "explanation_ko": q.get("explanation_ko", ""),
        "difficulty": q.get("difficulty", ""),
        "difficulty_label": _QUIZ_DIFFICULTY_LABEL.get(q.get("difficulty", ""),
                                                       q.get("difficulty", "")),
        "source_type": st,
        "source_kind": _QUIZ_SOURCE_KIND.get(st, st),
        # glossary → 용어사전 앵커 id(템플릿이 rel_root+glossary/#id 로 조립), 그 외는 "".
        "source_glossary_id": ref if is_glossary else "",
        # brief/finding → 공개 절대 URL(스킴 화이트리스트 통과분만; 비허용은 ""→링크 생략).
        "source_url": (_safe_url(ref) if not is_glossary else ""),
        # [9차 G3] week(YYYYWW) — 월 13:00 자동 생성 파이프라인이 붙이는 선택 필드. 있으면
        # data-week 로 embed(문자열 정규화만 — 값 무변형), 없으면 "" → 템플릿이 조용히 생략
        # (현 데이터 경로 = 기존 회전 그대로). 선택 로직은 클라이언트 quiz.js 소관.
        "week": str(q.get("week") or ""),
    }


def build_quiz_view(bank: list[dict[str, Any]]) -> dict[str, Any]:
    """문항 뱅크 → 렌더 뷰모델(무변형 — 값 재작성 0). 전 문항을 뱅크 순서 그대로 embed
    (클라이언트 결정론 회전용). 난이도 집계는 클라이언트 주차 회전이 easy 과반·normal 1~2
    구성을 맞추는 데 쓰는 파생 메타다."""
    questions = [_quiz_question_view(q) for q in bank]
    easy_total = sum(1 for q in questions if q["difficulty"] == "easy")
    return {
        "questions": questions,
        "total": len(questions),
        "weekly_count": WEEKLY_QUIZ_COUNT,
        "easy_total": easy_total,
        "normal_total": len(questions) - easy_total,
    }


def assign_issue_numbers(briefs: list[dict[str, Any]]) -> dict[str, int]:
    """publish_date 오름차순 순위로 issue 번호 부여(가장 오래된=1).

    계약 = "주차별 1파일"(고유 publish_date). 중복 publish_date 는 slug 충돌로 한 브리프가
    조용히 덮어써지므로(데이터 손실), 조용한 손실 대신 즉시 실패한다(verbatim 불변식 보호).
    """
    dates = [b["brief"].get("publish_date", "") for b in briefs]
    dups = sorted({d for d in dates if dates.count(d) > 1})
    if dups:
        raise SystemExit(f"중복 publish_date — 주차별 1파일 계약 위반(slug 충돌): {dups}")
    keyed = sorted(briefs, key=lambda b: b["brief"].get("publish_date", ""))
    return {b["brief"].get("publish_date", ""): i + 1 for i, b in enumerate(keyed)}


# ── 컨텍스트 빌더 ─────────────────────────────────────────────────────────────
def _brief_context(brief: dict[str, Any], issue_no: int) -> dict[str, Any]:
    bm = brief["brief"]
    # [업계 브리핑 노트 2026-07-13] resources 키 부재/빈값 → None(리스트 아님) — 템플릿의
    # `{% if brief.resources %}` 게이트가 그대로 False 라 partial 이 0바이트 렌더(하드 요구:
    # resources 없는 브리프는 바이트 불변). 값이 있을 때만 뷰모델 리스트로 변환.
    raw_resources = bm.get("resources")
    resources = [_resource_view(r) for r in raw_resources] if raw_resources else None
    return {
        "issue_no": issue_no,
        "run_date_kst": bm.get("run_date_kst", ""),
        "publish_date": bm.get("publish_date", ""),
        "window": bm.get("window", ""),
        "title_dateform": title_dateform(bm.get("publish_date", "")),
        "coverage": _norm_coverage(bm.get("coverage") or {}),
        "tldr": bm.get("tldr") or [],
        "ai_disclosure": bool(bm.get("ai_disclosure")),
        "agencies": bm.get("agencies") or [],
        "resources": resources,
    }


def _issue_row(brief: dict[str, Any], issue_no: int, latest_slug: str) -> dict[str, Any]:
    bm = brief["brief"]
    cov = _norm_coverage(bm.get("coverage") or {})
    pub = bm.get("publish_date", "")
    ev = cov["evidence"]
    agencies = list(bm.get("agencies") or [])
    return {
        "slug": pub,
        "issue_no": issue_no,
        "title": _brief_title(bm),
        "date": pub,
        "month": pub[:7],                          # YYYY-MM (publish_date 파생 — facet 기간)
        "agencies": agencies,                      # 칩(기관) per-tag 렌더(v2)
        "tags": " · ".join(agencies),              # (구) 조인 문자열 — 하위호환
        "count": cov["rendered"],
        "ev": f"A{ev['A']} · B{ev['B']}",
        "latest": pub == latest_slug,
    }


def _cover_context(brief: dict[str, Any], issue_no: int) -> dict[str, Any]:
    bm = brief["brief"]
    cov = _norm_coverage(bm.get("coverage") or {})
    pub = bm.get("publish_date", "")
    return {
        "issue_no": issue_no,
        "slug": pub,
        "publish_date": pub,
        "date_dotted": _date_dotted(pub),
        "rendered": cov["rendered"],
        "intake_total": cov["intake_total"],     # 다크밴드 바인딩(단일 파생 경로)
        "evidence": cov["evidence"],              # 다크밴드 Evidence A/B
        "title_dateform": title_dateform(pub),    # 다크밴드 "{Y}년 {M}월 {N}주차"
        "window": bm.get("window", ""),
        "title": _brief_title(bm),
        "tldr": bm.get("tldr") or [],
    }


# ── 검색 인덱스(P4 — 정적·결정론·무변형) ──────────────────────────────────────
# 인덱스는 **아카이브 페이지(`archive/index.html`, 깊이 1)** 전용 → href 는 그 페이지
# 기준 상대경로(`../`). render.py 가 페이지마다 새로 만들지 않는 단일 산출물이라 접두를
# 여기 고정한다(검색은 spec 상 아카이브에만 얹는다 — P4 §2.3).
_ARCHIVE_REL = "../"


def _card_search_text(card: dict[str, Any]) -> str:
    """클라이언트 검색 대상 결합 문자열(소문자화는 클라이언트에서).

    카드 **기존 값 verbatim 결합만** — 새 텍스트 생성 0(무변형). 순서·구성은 P4 §2.1:
    target + issue + card_type + agency + facts[].value (+ summary·key_facts 있으면).
    빈 조각은 건너뛴다(공백 중복 방지 — 각 조각은 카드값의 verbatim 부분문자열로 유지).
    """
    parts: list[str] = [
        card.get("headline_target", ""),
        card.get("title_issue", ""),
        card.get("card_type", ""),
        card.get("agency", ""),
    ]
    parts += [f.get("value", "") for f in (card.get("facts") or [])]
    if card.get("summary"):
        parts.append(card["summary"])
    parts += [k for k in (card.get("key_facts") or []) if k]
    return " ".join(p for p in parts if p)


def _card_index_entry(card: dict[str, Any], *, issue_no: int, date: str,
                      month: str, vol_title: str) -> dict[str, Any]:
    """카드 1개 → 검색 인덱스 엔트리. 전 필드 카드 기존 값 파생(무변형)."""
    return {
        "issue_no": issue_no,
        "date": date,
        "month": month,
        "vol_title": vol_title,
        "agency": card.get("agency", ""),
        "category": card.get("category", ""),
        "modality": card.get("modality"),               # null 가능(필터 미해당)
        "card_type": card.get("card_type", ""),
        "evidence_level": card.get("evidence_level", ""),
        "signal_tier": card.get("signal_tier", ""),
        "target": card.get("headline_target", ""),
        "issue": card.get("title_issue", ""),           # 빈값이면 "" (JS 가 처리)
        "summary": card.get("summary", ""),
        # 상세 카드 앵커 — 상세 article id 와 동일 함수 파생(항상 점프 일치).
        "href": f"{_ARCHIVE_REL}briefs/{date}/index.html#{_card_anchor(card)}",
        "text": _card_search_text(card),
    }


def build_search_index(briefs: list[dict[str, Any]], issue_no_by_date: dict[str, int],
                       latest_slug: str) -> dict[str, Any]:
    """전 브리프 카드 → 검색 인덱스(facet 메타 + 호 메타 + 카드 엔트리).

    정렬(결정론): 카드 = date desc, 동일 호 내 render_order asc. facet 후보는 **실제
    존재값만** 노출(데이터 파생) — agency/category/modality 알파벳, months 최신순.
    호 메타(issues)는 baseline 서버목록과 JS 검색뷰가 동일하게 쓰는 단일 파생원
    (`_issue_row`)에서 만들어 두 경로 일관성 보장.
    """
    cards_idx: list[dict[str, Any]] = []
    issues_idx: list[dict[str, Any]] = []
    agencies: set[str] = set()
    categories: set[str] = set()
    modalities: set[str] = set()
    months: set[str] = set()

    # date desc 순 브리프 순회 → 각 호 내부는 render_order asc → 결합이 곧 최종 정렬.
    for b in sorted(briefs, key=lambda b: b["brief"].get("publish_date", ""), reverse=True):
        bm = b["brief"]
        date = bm.get("publish_date", "")
        month = date[:7]
        issue_no = issue_no_by_date[date]
        vol_title = _brief_title(bm)
        renderable = [c for c in (b.get("cards") or []) if _is_renderable(c)]
        cards_sorted = sorted(renderable,
                              key=lambda c: (c.get("render_order") is None,
                                             c.get("render_order")))
        for c in cards_sorted:
            entry = _card_index_entry(c, issue_no=issue_no, date=date,
                                      month=month, vol_title=vol_title)
            cards_idx.append(entry)
            if entry["agency"]:
                agencies.add(entry["agency"])
            if entry["category"]:
                categories.add(entry["category"])
            if entry["modality"]:
                modalities.add(entry["modality"])
        if month:
            months.add(month)

        row = _issue_row(b, issue_no, latest_slug)
        issues_idx.append({
            "issue_no": row["issue_no"],
            "slug": row["slug"],
            "date": row["date"],
            "month": row["month"],
            "title": row["title"],
            "agencies": row["agencies"],
            "count": row["count"],
            "ev": row["ev"],
            "latest": row["latest"],
            "href": f"{_ARCHIVE_REL}briefs/{row['slug']}/index.html",
        })

    issues_idx.sort(key=lambda r: r["date"], reverse=True)
    return {
        "schema": "grm-search-index/v1",
        "facets": {
            "agencies": sorted(agencies),
            "categories": sorted(categories),
            "modalities": sorted(modalities),
            "months": sorted(months, reverse=True),
        },
        "issues": issues_idx,
        "cards": cards_idx,
    }


# ── 검색 노출(robots.txt + sitemap.xml — 정적·결정론·입력 publish_date 파생) ────
# 사이트 베이스 URL(+env override) — 향후 커스텀 도메인은 이 한 줄/환경변수만 교체.
SITE_BASE_URL = os.environ.get(
    "GRM_SITE_BASE_URL", "https://grm-solutions.com").rstrip("/")


def build_robots_txt(base_url: str = SITE_BASE_URL, *, disallow_admin: bool = False) -> str:
    """robots.txt — 공개 페이지 허용 + sitemap 포인터. Admin 은 비색인, /cdn-cgi/ 는 크롤 차단."""
    lines = [
        "User-agent: *",
        "Allow: /",
        # Cloudflare 가 이메일 난독화용 가상 경로(/cdn-cgi/l/email-protection)를 페이지에
        # 삽입한다 — 우리 콘텐츠가 아니고 크롤 시 404 라 차단(GSC 404 보고 소음 방지).
        "Disallow: /cdn-cgi/",
    ]
    if disallow_admin:
        lines.append("Disallow: /admin/")
    lines += [
        "",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    return "\n".join(lines) + "\n"


def build_llms_txt(briefs: list[dict[str, Any]],
                   base_url: str = SITE_BASE_URL,
                   *,
                   glossary_term_ids: "list[str] | None" = None,
                   facet_paths: "list[tuple[str, str]] | None" = None) -> str:
    """llms.txt(llmstxt.org 관례) — AI 어시스턴트·AI 검색용 사이트 안내.

    RUM 실측(2026-08 30일)에서 AI 유입(chatgpt+gemini 합 30)이 이미 네이버(20)를
    넘었다 — AI 가 서비스 구조를 이해하고 정확한 페이지를 인용할 수 있게 핵심 URL 과
    데이터 규모를 한 파일로 안내한다. 숫자는 전부 렌더 입력(briefs·glossary_term_ids·
    facet_paths)에서 파생한다 — 문장에 박은 숫자는 낡는다. facet_paths 는 문서 렌더
    스위치와 무관하게 데이터에서 파생되므로(sitemap 과 같은 원천) 테스트 빌드와
    프로덕션의 llms.txt 가 같다. 생성시각/난수 0(byte 고정).
    """
    pubs = sorted((b["brief"].get("publish_date", "") for b in briefs),
                  reverse=True)
    latest_pub = pubs[0] if pubs else ""
    n_terms = len(glossary_term_ids or [])
    n_docs = sum(1 for path, _ in (facet_paths or [])
                 if path.startswith("findings/doc/"))
    lines = [
        "# GRM · Global Regulatory Monitor",
        "",
        "> 전 세계 제약 GMP·품질 규제 소식을 매주 한국어로 정리하는 무료 규제 정보 서비스.",
        "> FDA·EMA·MHRA·Health Canada·WHO·PIC/S·식약처(MFDS) 등 1차 출처에서 매일 자동",
        "> 수집한 실사 지적사항과 규제 문서를 한국어 번역·분류와 함께 공개한다.",
        "",
        "모든 페이지는 공식 1차 출처 문서에서 AI 로 자동 생성·번역된다. 인용하거나 판단에",
        "쓸 때는 각 페이지가 링크한 공식 원문을 확인해야 하며, 답변에 인용할 때는 해당",
        "페이지 URL 을 출처로 남겨 달라. 아래 URL 은 전부 로그인 없는 공개 페이지다.",
        "",
        "## 주간 브리프",
    ]
    if latest_pub:
        lines.append(f"- [최신 브리프]({base_url}/briefs/{latest_pub}/): "
                     "이번 주 글로벌·국내 규제 동향 (매주 월요일 발행)")
    lines += [
        f"- [모아보기]({base_url}/archive/): 지금까지 발행한 주간 브리프 {len(pubs)}건",
        "",
        "## 규제 지적사항 데이터",
        f"- [지적사항 검색]({base_url}/findings/): FDA 483 · Warning Letter · EU/영국"
        " GMP 비준수 · 캐나다 실사 · 식약처 지적사항 통합 검색",
        f"- [문서로 찾기]({base_url}/findings/docs/): 실사 문서 단위 한국어 정리"
        f" {n_docs:,}건 — 기관·연도별 색인",
        f"- [트렌드]({base_url}/findings/trends/): 지적 영역·기관·연도별 자동 집계",
        f"- [자가점검 체크리스트]({base_url}/findings/checklist/): 빈발 지적 기반 자가"
        " 점검 문항",
        "",
        "## 참조 자료",
        f"- [규제 용어사전]({base_url}/glossary/): GMP·품질 용어 {n_terms}어 — 쉬운"
        " 한국어 풀이·공식 출처·실제 지적사례 연결",
        f"- [자료실]({base_url}/library/): FDA·EMA·PIC/S·ICH·WHO·식약처 지침·가이드라인"
        " 공식 원문 링크",
        f"- [이용안내]({base_url}/guide/): 서비스 활용법과 자주 묻는 질문",
        f"- [주간 퀴즈]({base_url}/quiz/): 그 주 규제 소식 기반 학습 퀴즈",
    ]
    return "\n".join(lines) + "\n"


def build_sitemap_xml(briefs: list[dict[str, Any]],
                      base_url: str = SITE_BASE_URL,
                      glossary_term_ids: "list[str] | None" = None,
                      facet_paths: "list[tuple[str, str]] | None" = None) -> str:
    """sitemap.xml — 랜딩 + 아카이브 + 각 호. canonical = 트레일링 슬래시 디렉터리형
    (`/`·`/archive/`·`/briefs/{pub}/`). lastmod = publish_date(YYYY-MM-DD)만 — 랜딩·
    아카이브는 최신 publish_date. 정렬 = publish_date desc. 생성시각/난수 0(byte 고정).

    URL·날짜는 전부 ASCII(http(s)·YYYY-MM-DD)라 XML 메타문자 부재 — 무변형 결합.
    """
    pubs = sorted((b["brief"].get("publish_date", "") for b in briefs),
                  reverse=True)
    latest_pub = pubs[0] if pubs else ""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <url><loc>{base_url}/</loc><lastmod>{latest_pub}</lastmod></url>",
        f"  <url><loc>{base_url}/archive/</loc><lastmod>{latest_pub}</lastmod></url>",
        f"  <url><loc>{base_url}/findings/</loc><lastmod>{latest_pub}</lastmod></url>",
        f"  <url><loc>{base_url}/findings/trends/</loc><lastmod>{latest_pub}</lastmod></url>",
        # 자가점검 체크리스트 — 조회 파라미터 없이 그 자체로 완결된 도구 페이지라 등록한다
        # (firm/inspector 와 달리 개인·업체 식별 정보가 URL 에 없다).
        f"  <url><loc>{base_url}/findings/checklist/</loc><lastmod>{latest_pub}</lastmod></url>",
        # 업체 프로파일(FIND-FIRM-ALIAS) — 쿼리스트링 기반 동적 조회(`?key=firm_key`)라
        # 개별 업체 URL 은 넣지 않고 베이스 경로 1건만 등록한다.
        f"  <url><loc>{base_url}/findings/firm/</loc><lastmod>{latest_pub}</lastmod></url>",
        # [자료실] 정적 참조 카탈로그(주간 발행과 무관한 독립 섹션). lastmod 는 브리프
        # publish_date 와 분리된 별개 데이터라 최신 브리프 날짜를 재사용하지 않고 생략.
        f"  <url><loc>{base_url}/library/</loc></url>",
        *(f"  <url><loc>{base_url}/library/{e['slug']}/</loc></url>"
          for e in LIBRARY_REGISTRY),
        # [이용안내·용어사전] 트랙 C 2차 웨이브 — library 와 동일하게 브리프 발행일과
        # 분리된 상설 참조 콘텐츠라 lastmod 는 생략(정적 커밋 데이터).
        f"  <url><loc>{base_url}/guide/</loc></url>",
        f"  <url><loc>{base_url}/glossary/</loc></url>",
        # [용어사전 낱개] 색인 페이지 1건만 등록하면 226 어가 URL 하나에 묶여 검색 대상이
        # 되지 못한다("OOS 뜻"·"CAPA 란"). 용어당 URL 을 등록해 각 용어가 독립 색인 대상이
        # 되게 한다. id 는 glossary.json 정본의 slug(ASCII·검증됨)라 XML 무변형 결합.
        *(f"  <url><loc>{base_url}/glossary/{tid}/</loc></url>"
          for tid in (glossary_term_ids or [])),
        # [분류·국가·기관 모음] `/findings/` 는 런타임 RPC 검색 앱이라 그 자체로는 지적
        # 본문이 색인되지 않는다. 축별 정적 표면(축 색인 3 + 항목 N)을 등록해 24,797건이
        # 주제·국가·기관 단위로는 검색 대상이 되게 한다. 경로는 렌더가 실제로 쓴 것과
        # 같은 리스트라(손으로 다시 적지 않는다) 페이지와 sitemap 이 갈라질 수 없다.
        # ★lastmod 는 **진짜 날짜가 있을 때만** 넣는다. 지어낸 수정일은 없느니만 못하다 —
        # 구글은 신뢰할 수 없는 lastmod 를 무시하기 시작한다(사이트 전체가 손해). 그래서
        # 문서·목록·모음처럼 데이터에 실제 날짜가 있는 것만 달고, 용어사전처럼 날짜 개념이
        # 없는 페이지는 비운다.
        *(f"  <url><loc>{base_url}/{path}</loc>"
          + (f"<lastmod>{mod}</lastmod>" if mod else "")
          + "</url>"
          for path, mod in (facet_paths or [])),
        # [주간 퀴즈] 트랙 C — 상설 학습 콘텐츠라 brief publish_date 와 분리(lastmod 생략).
        f"  <url><loc>{base_url}/quiz/</loc></url>",
    ]
    for pub in pubs:
        lines.append(
            f"  <url><loc>{base_url}/briefs/{pub}/</loc><lastmod>{pub}</lastmod></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"



# ── [검색 유입] RSS 피드 — 네이버 서치어드바이저는 사이트맵과 **별개 채널로 RSS 를 받는다** ─
# 사이트맵이 "우리 페이지 전부"라면 RSS 는 "새로 나온 것"이다. 주간 브리프가 정확히 그
# 성격이라(주 1회·시간순·편집된 글) 피드의 내용은 브리프로 한정한다 — 지적사항 문서는
# 시간순 발행물이 아니라 참조 자료라 sitemap 의 몫이다.
#
# 피드는 사람에게도 쓸모가 있다(피드 리더·사내 그룹웨어 RSS 위젯).
#
# ★RFC-822 날짜의 요일·월 약어는 **영어 고정**이다. `strftime("%a")` 는 로케일을 타서
#   한국어 Windows 에서 "월" 이 나올 수 있으므로 쓰지 않는다(사양이 정한 표라 낡지 않는다).
# Sakamoto 는 0=일요일을 돌려준다 — 표 순서를 그 규약에 맞춘다.
_RFC822_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_RFC822_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# 브리프는 매주 월요일 07:30 KST 에 발행된다(grm-intake.yml). now() 를 쓰지 않는 고정값이다.
_BRIEF_PUBLISH_TIME = "07:30:00 +0900"


def rfc822_date(iso_date: str) -> str:
    """`2026-08-10` → `Mon, 10 Aug 2026 07:30:00 +0900`. 로케일 무관·결정론.

    ★`datetime` 을 쓰지 않는다 — 렌더러의 순수성 가드(`test_no_impure_imports`)가 그 모듈의
      **import 자체**를 막는다. `now()` 를 가능하게 만드는 문을 아예 닫아 두는 규율이고,
      요일 하나 때문에 그 문을 열 이유가 없다. 그래서 Sakamoto 알고리즘으로 직접 센다
      (그레고리력 요일 공식 — 사양이지 손목록이 아니라서 낡지 않는다).
      독립 검증은 테스트가 `datetime` 으로 대조해 준다(테스트는 가드 대상이 아니다).
    """
    y, m, d = int(iso_date[0:4]), int(iso_date[5:7]), int(iso_date[8:10])
    if not (1 <= m <= 12 and 1 <= d <= 31):
        raise SystemExit(f"발행일 형식이 아니다: {iso_date!r}")
    shift = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)[m - 1]
    yy = y - 1 if m < 3 else y
    dow = (yy + yy // 4 - yy // 100 + yy // 400 + shift + d) % 7   # 0 = Sunday
    return (f"{_RFC822_DAYS[dow]}, {d:02d} {_RFC822_MONTHS[m - 1]} "
            f"{y} {_BRIEF_PUBLISH_TIME}")


def build_rss_xml(briefs: list[dict[str, Any]],
                  base_url: str = SITE_BASE_URL) -> str:
    """주간 브리프 RSS 2.0. 발행일 내림차순·생성시각 0(byte 고정).

    본문(tldr)은 한국어 산문이라 XML 메타문자가 실재한다 — `escape()` 로 반드시 감싼다
    (sitemap 은 URL·날짜뿐이라 무변형 결합이 성립하지만 여기는 다르다).
    """
    nums = assign_issue_numbers(briefs)
    ordered = sorted(briefs, key=lambda b: b["brief"].get("publish_date", ""),
                     reverse=True)
    latest = ordered[0]["brief"].get("publish_date", "") if ordered else ""

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{_x(RSS_TITLE)}</title>",
        f"    <link>{base_url}/</link>",
        f"    <description>{_x(RSS_DESCRIPTION)}</description>",
        "    <language>ko</language>",
        f'    <atom:link href="{base_url}/rss.xml" rel="self" type="application/rss+xml" />',
    ]
    if latest:
        lines.append(f"    <lastBuildDate>{rfc822_date(latest)}</lastBuildDate>")

    for b in ordered:
        bm = b["brief"]
        pub = bm.get("publish_date", "")
        if not pub:
            continue
        url = f"{base_url}/briefs/{pub}/"
        tldr = [t for t in (bm.get("tldr") or []) if str(t).strip()]
        # 설명은 지어내지 않는다 — 그 호의 tldr 을 그대로 잇는다(값 무변형).
        desc = " · ".join(str(t).strip() for t in tldr)
        lines += [
            "    <item>",
            f"      <title>{_x(f'GRM 주간 브리프 Vol.{nums.get(pub, 0)} ({pub})')}</title>",
            f"      <link>{url}</link>",
            f'      <guid isPermaLink="true">{url}</guid>',
            f"      <pubDate>{rfc822_date(pub)}</pubDate>",
            f"      <description>{_x(desc)}</description>",
            "    </item>",
        ]
    lines += ["  </channel>", "</rss>"]
    return "\n".join(lines) + "\n"


def build_glossary_term_json_ld(term: dict[str, Any],
                                base_url: str = SITE_BASE_URL) -> str:
    """schema.org DefinedTerm — 용어 페이지가 '사전 항목'임을 검색엔진에 명시.

    inLanguage=ko. `name` 은 한글 표제어, `alternateName` 은 영문 표제어(+동의어).
    값은 전부 정본 무변형이고 json.dumps 가 이스케이프를 책임진다(수동 문자열 결합 0).
    """
    alt = [term["term_en"], *term.get("aliases", [])]
    node: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "DefinedTerm",
        "name": term["term_ko"],
        "alternateName": [a for a in alt if a],
        "description": term.get("easy_ko") or "",
        "inLanguage": "ko",
        "url": f"{base_url}/glossary/{term['id']}/",
        "inDefinedTermSet": {
            "@type": "DefinedTermSet",
            "name": "GRM 규제 용어사전",
            "url": f"{base_url}/glossary/",
        },
    }
    return json.dumps(node, ensure_ascii=False, sort_keys=True)


# ── SEO 메타·구조화데이터(description·canonical·OG·JSON-LD — 정적·결정론·한글안전) ──
def _env_or_default(key: str, default: str) -> str:
    """env-param 읽기 — 빈 문자열/미설정 모두 기본값으로 폴백.

    GitHub Actions 는 미설정 repo Variable(`vars.*`)을 워크플로 env 에 **빈 문자열**로
    주입한다. `os.environ.get(key, default)` 는 이때 default 가 아니라 ""를 돌려주므로,
    deploy build 스텝에 인증 토큰 var 를 배선(`vars.* → env`)해도 var 미설정 시 토큰이
    사라지지 않도록(메타 비활성 회귀 방지) 빈 값을 기본값으로 흡수한다.
    """
    return (os.environ.get(key) or "").strip() or default


# 소유권 인증 토큰(GSC·네이버) — 공개값(라이브 <head> 노출). 기본값 = 라이브 토큰을 단일
# 소스로 흡수(중복 <meta> 제거 + 골든 일치). **회전은 repo var 설정만으로(코드 수정 0)**:
# `grm-web-deploy.yml` build env 에 GRM_GOOGLE_SITE_VERIFICATION·GRM_NAVER_SITE_VERIFICATION
# 배선됨 → var 설정 시 그 값, 미설정/빈 값이면 아래 기본 토큰(무회귀) → 재배포 → 콘솔 "확인".
# (빈 env 로 메타를 '비활성'하던 경로는 제거 — 라이브 SEO 사이트 비활성은 비현실적.)
GOOGLE_SITE_VERIFICATION = _env_or_default(
    "GRM_GOOGLE_SITE_VERIFICATION", "pm3IGW80AsWscJVlQzMZel18pFcjFTxCxXrTDXqcjx4")
NAVER_SITE_VERIFICATION = _env_or_default(
    "GRM_NAVER_SITE_VERIFICATION", "51283dc3591917baf9e057d220f053a91131bbe2")

# 뉴스레터 구독 폼 action(관리형 SaaS 호스팅 endpoint) — env-param. 기본값 ""(빈 문자열)이면
# 폼 블록 미출력 → 테스트/기본 빌드 골든 영향 0, 프로덕션 var 설정 시에만 노출(인증 메타와 동일
# 패턴). 폼은 브라우저가 SaaS 로 직접 POST 하므로 사이트는 100% 정적 유지(외부 fetch·런타임
# 서버 0). 더블 옵트인·수신거부·구독자 PII 는 SaaS 가 소유(우리 비복제). 운영: SaaS 호스팅
# 구독 폼 생성 → action URL 을 repo var GRM_NEWSLETTER_FORM_ACTION 로 설정(이메일 필드명이
# 'email' 이 아닌 SaaS 면 템플릿 input name 도 함께 맞춘다). 추적 파라미터는 발송 시점에 SaaS
# 가 부착 — 우리 카드 원문/공식 URL(provenance 가드 대상)과 무관한 별개 endpoint.
NEWSLETTER_FORM_ACTION = os.environ.get("GRM_NEWSLETTER_FORM_ACTION", "").strip()

# 웹 카드 반응 계층(하트·스크랩·회원, S1) — env-param(공개값). SUPABASE_URL·SUPABASE_ANON_KEY
# 둘 다 설정돼야 활성(reactions_enabled). 미설정(기본·테스트)이면 반응 블록 전체 미출력 →
# 전 페이지 골든 byte-diff 0(뉴스레터 form_action 선례 동형). anon key 는 publishable(RLS 로
# 보호)이라 클라이언트 노출 안전 — service_role 키는 절대 배선하지 않는다.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

# 서비스 캐논 카피(랜딩 description·OG·JSON-LD 공용). 한글 본문 — mono/자간/대문자 미적용.
SITE_NAME = "Global Regulatory Monitor"
SITE_DESCRIPTION = ("전 세계 제약 GMP·품질 규제 소식을 매주 한자리에 모아 "
                    "기관별 정렬·시사점·점검까지 정리하는 규제뉴스.")
ARCHIVE_DESCRIPTION = ("GRM 규제뉴스 아카이브 — 전 세계 제약 GMP·품질 규제 소식을 "
                       "주차별로 모아 기관·기간으로 검색·필터.")
FINDINGS_DESCRIPTION = ("FDA 483 Observation · Warning Letter · 캐나다 실사 · 식약처 · "
                        "EU/영국 GMP 비준수 지적사항을 원문에서 자동 추출해 검색·필터.")
TRENDS_DESCRIPTION = ("FDA 483 · Warning Letter · 캐나다 실사 · 식약처 · EU/영국 GMP 비준수 지적사항 "
                      "전량 집계 통계 — 카테고리 순위·연도별 구성비·업체 랭킹으로 보는 규제 지적 트렌드.")
FIRM_DESCRIPTION = ("특정 업체의 FDA 483·Warning Letter·캐나다 실사·식약처·EU/영국 GMP 비준수 지적사항 "
                    "누적 이력을 카테고리·연도별 추이·문서 이력으로 한 곳에서 확인하는 업체 프로파일.")
CHECKLIST_DESCRIPTION = ("규제기관이 실제로 인용한 21 CFR 조항을 인용 빈도순으로 뽑고 조항별 실제 "
                         "지적 문장을 붙인 GMP 자가점검 체크리스트 — 인쇄·엑셀 내보내기 지원.")
INSPECTOR_DESCRIPTION = ("공개된 FDA 483 문서에 서명한 실사관의 지적사항 이력을 "
                         "카테고리·연도별 추이·문서 이력으로 한 곳에서 확인하는 실사관 프로파일.")
LIBRARY_DESCRIPTION = ("FDA·EMA·식약처·PIC/S·ICH·WHO·PMDA 등 국내외 규제기관의 GMP 지침·고시·"
                       "기준서를 한곳에 모은 규제 자료실 — 공식 원문 링크와 함께 언제든 다시 찾아보세요.")
GUIDE_DESCRIPTION = ("GRM 이용 안내 — 월요일 브리프 3분 활용법, findings 검색 실전 예시, "
                     "자료실·용어사전·퀴즈 활용법과 자주 묻는 질문을 한곳에 정리했습니다.")
RSS_TITLE = "GRM 주간 브리프 · 글로벌 규제 인텔리전스"
RSS_DESCRIPTION = ("전 세계·국내 제약 GMP/품질 규제 소식을 매주 한국어로 정리해 드립니다. "
                   "FDA·EMA·식약처·캐나다 보건부 등의 공개 자료가 원천입니다.")
GLOSSARY_DESCRIPTION = ("제약 GMP·규제 용어사전 — GMP·CAPA·데이터 완전성·무균 공정·ICH 등 "
                        "핵심 용어를 쉬운 풀이와 공식 출처로 설명합니다.")
QUIZ_DESCRIPTION = ("GRM 주간 퀴즈 — 규제·품질 용어와 최근 공개 사례를 짧게 복습하는 "
                    "전 직원 학습 퀴즈. 선택 즉시 정답·해설·근거 링크를 확인하세요.")


def _abs_url(rel_path: str = "") -> str:
    """SITE_BASE_URL + 경로 → 절대 canonical(트레일링 슬래시 디렉터리형). 랜딩=베이스/."""
    return f"{SITE_BASE_URL}/{rel_path}"


def _brief_description(brief_meta: dict[str, Any]) -> str:
    """브리프 description = tldr[0] 있으면 사용, 없으면 날짜 파생 한 줄(결정론)."""
    tldr = brief_meta.get("tldr") or []
    if tldr and tldr[0]:
        return tldr[0]
    return (f"{title_dateform(brief_meta.get('publish_date', ''))} "
            "글로벌·국내 제약 GMP·품질 규제 소식.")


def build_json_ld(base_url: str = SITE_BASE_URL) -> str:
    """랜딩 JSON-LD(Organization + WebSite) — 정적·결정론. <script> 임베드 안전 직렬화.

    값은 전부 렌더 보유 정적 카피 + base_url(무변형). '<' 만 \\u003c 로 치환해 </script>
    조기종료(브레이크아웃)를 원천 차단(데이터엔 '<' 부재 — 방어선). dict 삽입순 보존.
    """
    nodes = [
        {"@context": "https://schema.org", "@type": "Organization",
         "name": SITE_NAME, "url": base_url, "description": SITE_DESCRIPTION,
         "logo": f"{base_url}/assets/favicon-512.png"},
        {"@context": "https://schema.org", "@type": "WebSite",
         "name": SITE_NAME, "url": base_url, "description": SITE_DESCRIPTION,
         "inLanguage": "ko"},
    ]
    return json.dumps(nodes, ensure_ascii=False, indent=1).replace("<", "\\u003c")


def build_site_webmanifest() -> str:
    """site.webmanifest — 정적·결정론(PWA 아이콘 메타). dict 삽입순 보존."""
    manifest = {
        "name": SITE_NAME,
        "short_name": "GRM",
        "icons": [
            {"src": "/assets/favicon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/assets/favicon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "theme_color": "#C2603F",
        "background_color": "#FAF9F5",
        "display": "standalone",
    }
    return json.dumps(manifest, ensure_ascii=False, indent=1) + "\n"


# ── 렌더 ─────────────────────────────────────────────────────────────────────
def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader([str(TEMPLATES_DIR), str(PARTIALS_PARENT)]),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    # [상세 가독성 2026-07-27] 표현층 전용 필터 — 데이터는 verbatim, 렌더만 분해한다.
    env.filters["detail_blocks"] = split_detail_blocks
    env.filters["gmp_operations"] = split_gmp_operations
    return env


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 항상 LF/UTF-8 — OS 무관 결정론(Windows 의 \r\n 변환 차단).
    path.write_bytes(text.encode("utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    """결정론 JSON 쓰기 — dict 삽입순서 보존(sort_keys 미사용), ensure_ascii=False,
    indent=1(레포 data 관례), 항상 LF/UTF-8 + 후행개행. 같은 입력 → byte 동일."""
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def render_site(data_dir: Path = DATA_DIR, out_dir: Path = DIST_DIR,
                assets_dir: Path = ASSETS_DIR,
                render_doc_pages: bool = True) -> dict[str, Any]:
    """data_dir → out_dir 정적 사이트 빌드. 산출 메타(쓴 파일 목록) 반환.

    `render_doc_pages=False` 는 **테스트 전용 속도 스위치**다. 문서 단위 페이지는 3천 장이
    넘어 한 번 렌더에 ~27초가 드는데, 테스트 스위트는 사이트를 51번 다시 짓는다(= 23분).
    그래서 대부분의 테스트는 이 부분을 건너뛰고, **전용 테스트 클래스 하나만** 켠 채로 지어
    전수 검증한다(`WebFindingsDocPageTest`).

    ★기본값은 True 다 — 프로덕션 경로(CLI)는 아무것도 넘기지 않으므로 항상 렌더한다.
      기본값을 False 로 두면 배포가 조용히 sitemap 에만 있는 유령 URL 3천 개를 광고하게 된다.
    ★끄더라도 **sitemap 에는 문서 URL 을 그대로 넣는다** — sitemap 은 데이터에서 파생되지
      렌더 결과에서 파생되지 않으므로, 켜고 끄는 것이 골든을 흔들지 않는다(테스트가 보는
      sitemap 과 프로덕션 sitemap 이 같아야 대조가 의미 있다).
    """
    env = _make_env()
    # 소유권 인증 메타(env-param) — 전 페이지 <head> 공통(미설정 시 미출력). 아래 전역들
    # (SITE_BASE_URL·NEWSLETTER_FORM_ACTION·*_SITE_VERIFICATION)은 import 시점에 os.environ
    # 에서 캡처된다. 여기서는 그 모듈 전역을 render_site() 호출 시점에 env.globals 로 주입 —
    # 테스트가 모듈 속성(render.SITE_BASE_URL 등)을 monkeypatch 하면 반영되지만 os.environ 을
    # 호출 시점에 재조회하진 않는다(monkeypatch 계약 = 모듈 속성 기준, os.environ 아님).
    env.globals["google_site_verification"] = GOOGLE_SITE_VERIFICATION
    env.globals["naver_site_verification"] = NAVER_SITE_VERIFICATION
    env.globals["og_image"] = f"{SITE_BASE_URL}/assets/og-image.png"
    # RUM 비콘 게이트(base.html)의 프로덕션 호스트 허용목록 — SITE_BASE_URL 파생(단일원천:
    # 커스텀 도메인 교체 시 SITE_BASE_URL 한 줄만 바꾸면 게이트도 따라온다).
    env.globals["site_host"] = SITE_BASE_URL.split("://", 1)[-1].split("/", 1)[0]
    # 구독 폼 action — 스킴 화이트리스트(_safe_url) 통과분만(비http(s) 오설정은 ""→폼 미출력
    # fail-safe). 빈 값이면 base.html 의 {% if %} 가 폼 블록 전체를 생략(골든 영향 0).
    env.globals["newsletter_form_action"] = _safe_url(NEWSLETTER_FORM_ACTION)
    # 자산 캐시버스팅 — grm.css/archive.js content-hash 쿼리(재배포 시 stale CSS 방지·결정론).
    def _asset_ver(name: str) -> str:
        p = assets_dir / name
        return hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.is_file() else "0"
    env.globals["css_ver"] = _asset_ver("grm.css")
    env.globals["archivejs_ver"] = _asset_ver("archive.js")
    env.globals["findingsjs_ver"] = _asset_ver("findings.js")
    env.globals["trendsjs_ver"] = _asset_ver("trends.js")
    env.globals["firmjs_ver"] = _asset_ver("firm.js")
    env.globals["checklistjs_ver"] = _asset_ver("checklist.js")
    env.globals["inspectorjs_ver"] = _asset_ver("inspector.js")
    env.globals["glossaryjs_ver"] = _asset_ver("glossary.js")
    env.globals["quizjs_ver"] = _asset_ver("quiz.js")
    env.globals["growthjs_ver"] = _asset_ver("growth.js")
    env.globals["petcss_ver"] = _asset_ver("pet.css")
    env.globals["petjs_ver"] = _asset_ver("pet.js")
    env.globals["popularjs_ver"] = _asset_ver("popular.js")
    # 반응 계층 공개 설정 주입 — url 이 https(_safe_url 통과)이고 anon key 가 있을 때만 활성.
    # 미설정이면 base.html/card.html 의 {% if reactions_enabled %} 가 반응 블록 전체 생략.
    _supa_url = _safe_url(SUPABASE_URL)
    env.globals["reactions_enabled"] = bool(_supa_url and SUPABASE_ANON_KEY)
    env.globals["admin_enabled"] = env.globals["reactions_enabled"]
    env.globals["supabase_url"] = _supa_url
    env.globals["supabase_anon_key"] = SUPABASE_ANON_KEY
    env.globals["reactionsjs_ver"] = _asset_ver("reactions.js")
    env.globals["growthsyncjs_ver"] = _asset_ver("growth-sync.js")
    env.globals["adminjs_ver"] = _asset_ver("admin.js")
    briefs = load_briefs(data_dir)
    if not briefs:
        raise SystemExit(f"입력 브리프 없음: {data_dir}")

    issue_no_by_date = assign_issue_numbers(briefs)
    latest_slug = max(b["brief"].get("publish_date", "") for b in briefs)
    latest_brief = next(b for b in briefs if b["brief"].get("publish_date", "") == latest_slug)
    latest_issue_no = issue_no_by_date[latest_slug]

    written: list[str] = []

    # 클린 빌드(이전 산출 제거 — 결정론).
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # assets 복사(byte-verbatim — CSS 디자인 동결본).
    dist_assets = out_dir / "assets"
    dist_assets.mkdir(parents=True, exist_ok=True)
    for af in sorted(assets_dir.glob("*")):
        if af.is_file():
            shutil.copyfile(af, dist_assets / af.name)
            written.append(f"assets/{af.name}")

    # 파비콘(dist 루트) — 브라우저가 /favicon.ico·/favicon.svg 를 루트에서 자동 요청.
    # 원본은 assets/ 에 두고(위 루프로 assets/ 복사됨) 루트에도 동일 바이트 복사.
    for icon_name in ("favicon.ico", "favicon.svg"):
        shutil.copyfile(assets_dir / icon_name, out_dir / icon_name)
        written.append(icon_name)

    # PWA 매니페스트(dist 루트) — 정적·결정론.
    _write(out_dir / "site.webmanifest", build_site_webmanifest())
    written.append("site.webmanifest")

    # [자료실] 카탈로그 스냅샷 + 최근 변경 이력 — 랜딩 카드·자료실 허브·아카이브 스트립이
    # 함께 쓰므로 세 렌더보다 먼저 한 번만 읽는다(같은 입력 → 같은 출력).
    catalogs = load_library()
    library_updates = load_library_updates(catalogs)
    # 랜딩 자료실 카드용 집계 — **수치를 템플릿에 박지 않는다**. 카탈로그가 늘 때마다
    # 사람이 문구를 고쳐야 하면 반드시 낡는다(이용안내가 그렇게 낡았다 — 2026-07-25).
    library_summary = {
        "catalog_count": len(catalogs),
        "item_count": sum(v["count"] for v in catalogs),
    }

    # 랜딩.
    landing_html = env.get_template("landing.html").render(
        page_title="GRM · Global Regulatory Monitor",
        rel_root="",
        nav_active="home",
        latest_slug=latest_slug,
        description=SITE_DESCRIPTION,
        canonical=_abs_url(""),
        json_ld=build_json_ld(),
        cover=_cover_context(latest_brief, latest_issue_no),
        library=library_summary,
    )
    _write(out_dir / "index.html", landing_html)
    written.append("index.html")

    # 아카이브(최신호 desc 정렬).
    issues = sorted(
        (_issue_row(b, issue_no_by_date[b["brief"].get("publish_date", "")], latest_slug)
         for b in briefs),
        key=lambda r: r["date"], reverse=True,
    )
    archive_html = env.get_template("archive.html").render(
        page_title="규제뉴스 · GRM",
        rel_root="../",
        nav_active="board",
        latest_slug=latest_slug,
        description=ARCHIVE_DESCRIPTION,
        canonical=_abs_url("archive/"),
        issues=issues,
        lib_update=library_updates["compact"],
    )
    _write(out_dir / "archive" / "index.html", archive_html)
    written.append("archive/index.html")

    # [검색 유입] 정본을 여기서 미리 읽는다 — 아래 findings 셸이 정적 표면으로 가는
    # **유일한 진입 간선**을 렌더해야 하고(홈 BFS 도달 28/3,520 이던 고립을 메운다),
    # 모음 페이지의 사례가 문서 페이지로 이어지려면 "그 문서에 페이지가 있는가"도 알아야
    # 하기 때문이다. 파일 로드일 뿐이라 렌더 비용이 아니다.
    facets = load_findings_facets()
    docs_data = load_findings_docs()
    doc_slugs: set[str] = {d["slug"] for d in (docs_data or {}).get("documents", [])}

    # 진입 카드는 **데이터가 있는 축만** 만든다 — 없는 페이지로 보내는 링크는 무링크보다
    # 나쁘다. 문서 축은 렌더 스위치가 꺼진 테스트 빌드에서 페이지가 없으므로 함께 건다.
    _axis_blurb = {
        "category": "무균공정·시험실 관리처럼 실사에서 반복되는 주제로 묶어 봅니다.",
        "country": "제조소가 어느 나라에 있는지로 묶어 봅니다.",
        "agency": "FDA·캐나다 보건부·식약처 등 기관별로 묶어 봅니다.",
    }
    browse_axes = []
    for _axis in (facets.get("axes") if facets else []) or []:
        _meta = FACET_AXES.get(_axis["axis"])
        if not _meta or not _axis.get("items"):
            continue
        browse_axes.append({"href": f"findings/{_meta['path']}/",
                            "title": _meta["title"],
                            "blurb": _axis_blurb.get(_axis["axis"], "")})
    # ★sitemap 과 같은 규칙: **데이터에서 파생**하지 렌더 결과에서 파생하지 않는다.
    # `render_doc_pages` 로 가르면 테스트 빌드의 골든이 프로덕션과 다른 것을 고정하게 되어
    # (골든에 이 카드가 없는데 라이브엔 있는 상태) 대조가 의미를 잃는다.
    if docs_data and docs_data.get("documents"):
        browse_axes.append({
            "href": "findings/docs/", "title": "문서로 찾기",
            "blurb": (f"실사 문서 {docs_data['totals']['documents']:,}건을 기관·연도로"
                      " 묶어 봅니다."),
        })

    # 지적사항 검색(FIND-1 M3c) — 라이브 데이터(Supabase PostgREST)라 빌드시 목록을 고정할
    # 수 없다. 서버는 셸(로딩 상태)만 렌더 — env 미설정이면 findings.js 가 "준비 중" 안내로
    # 조용히 종료한다(cfg data 속성은 위 reactions_enabled 와 무관하게 항상 주입).
    findings_html = env.get_template("findings.html").render(
        browse_axes=browse_axes,
        page_title="규제 지적사항 검색 · GRM",
        rel_root="../",
        nav_active="findings",
        latest_slug=latest_slug,
        description=FINDINGS_DESCRIPTION,
        canonical=_abs_url("findings/"),
    )
    _write(out_dir / "findings" / "index.html", findings_html)
    written.append("findings/index.html")

    # 트렌드 대시보드(FIND-1 F3b) — findings 와 동일 이유로 라이브 데이터는 빌드시 고정할
    # 수 없다(집계는 Supabase RPC findings_stats/findings_firm_stats 를 trends.js 가 직접
    # fetch). 서버는 셸(로딩 상태)만 렌더 — findings/index.html 한 단계 더 깊은 경로라
    # rel_root 는 "../../"(브리프 상세와 동일 깊이).
    trends_html = env.get_template("trends.html").render(
        page_title="규제 지적사항 트렌드 · GRM",
        rel_root="../../",
        nav_active="trends",
        latest_slug=latest_slug,
        description=TRENDS_DESCRIPTION,
        canonical=_abs_url("findings/trends/"),
    )
    _write(out_dir / "findings" / "trends" / "index.html", trends_html)
    written.append("findings/trends/index.html")

    # 업체 프로파일(FIND-FIRM-ALIAS 웹 절반) — findings/trends 와 동일 이유로 라이브
    # 데이터는 빌드시 고정할 수 없다(013_findings_firm_key.sql 의 findings_firm_profile
    # RPC 를 firm.js 가 URL 파라미터(?key=)로 직접 fetch). 서버는 셸(로딩 상태)만 렌더.
    # findings/firm/index.html 은 findings/trends/index.html 과 같은 깊이라 rel_root 동일.
    firm_html = env.get_template("firm.html").render(
        page_title="업체 프로파일 · GRM",
        rel_root="../../",
        nav_active="findings",
        latest_slug=latest_slug,
        description=FIRM_DESCRIPTION,
        canonical=_abs_url("findings/firm/"),
    )
    _write(out_dir / "findings" / "firm" / "index.html", firm_html)
    written.append("findings/firm/index.html")

    # 자가점검 체크리스트 — findings/trends 와 동일 이유로 라이브 데이터는 빌드시 고정할 수
    # 없다(042 findings_cfr_ranking 로 조항 순위 + 043 findings_checklist 로 사례를 받아
    # checklist.js 가 조립). 서버는 셸(설정 바 + 로딩 상태)만 렌더한다.
    # findings/checklist/index.html 은 findings/trends/index.html 과 같은 깊이라 rel_root 동일.
    checklist_html = env.get_template("checklist.html").render(
        page_title="자가점검 체크리스트 · GRM",
        rel_root="../../",
        nav_active="trends",
        latest_slug=latest_slug,
        description=CHECKLIST_DESCRIPTION,
        canonical=_abs_url("findings/checklist/"),
    )
    _write(out_dir / "findings" / "checklist" / "index.html", checklist_html)
    written.append("findings/checklist/index.html")

    # 실사관 프로파일(FDA 483 서명 실사관 집계, firm 프로파일의 미러링) — findings/firm 과
    # 동일 이유로 라이브 데이터는 빌드시 고정할 수 없다(findings_inspector_profile RPC 를
    # inspector.js 가 URL 파라미터(?key=)로 직접 fetch). 서버는 셸(로딩 상태)만 렌더.
    # findings/inspector/index.html 은 findings/firm/index.html 과 같은 깊이라 rel_root 동일.
    # ★sitemap 미등록(의도, firm 과 다름) — 실명이 적시된 개인 집계라 베이스 경로조차
    # 넣지 않는다. noindex 는 inspector.html 자체 <head> 오버라이드(meta_robots 블록)로
    # 배선하고, canonical 은 중복 URL 정리 목적으로 그대로 둔다.
    inspector_html = env.get_template("inspector.html").render(
        page_title="실사관 프로파일 · GRM",
        rel_root="../../",
        nav_active="findings",
        latest_slug=latest_slug,
        description=INSPECTOR_DESCRIPTION,
        canonical=_abs_url("findings/inspector/"),
    )
    _write(out_dir / "findings" / "inspector" / "index.html", inspector_html)
    written.append("findings/inspector/index.html")

    # 자료실(트랙 C) — findings/trends 와 달리 라이브 데이터가 아니라 커밋 스냅샷
    # (web/data/library/*.json)을 결정론 렌더한다(주간 발행 게이트와 무관한 독립 정적
    # 섹션). 데이터 파일이 없으면 해당 카탈로그·허브 항목을 조용히 건너뛴다.
    if catalogs:
        hub_catalogs = [{
            "href": f"{v['slug']}/index.html",
            "title": v["title"],
            "count": v["count"],
            "unit": v["unit"],
            "blurb": v["blurb"],
            "latest_published": v["latest_published"],
        } for v in catalogs]
        library_html = env.get_template("library.html").render(
            page_title="자료실 · GRM",
            rel_root="../",
            nav_active="library",
            latest_slug=latest_slug,
            description=LIBRARY_DESCRIPTION,
            canonical=_abs_url("library/"),
            catalogs=hub_catalogs,
            lib_update=library_updates["latest"],
        )
        _write(out_dir / "library" / "index.html", library_html)
        written.append("library/index.html")

    # 카탈로그 상세 — registry 전 항목을 공통 템플릿(library_catalog.html) 하나로 렌더.
    # 카탈로그 1개 추가 = 데이터 파일 + LIBRARY_REGISTRY 1항목(여기·템플릿 무수정).
    for v in catalogs:
        catalog_html = env.get_template("library_catalog.html").render(
            page_title=f"{v['title']} · GRM",
            rel_root="../../",
            nav_active="library",
            latest_slug=latest_slug,
            description=v["desc"],
            canonical=_abs_url(f"library/{v['slug']}/"),
            lib=v,
        )
        _write(out_dir / "library" / v["slug"] / "index.html", catalog_html)
        written.append(f"library/{v['slug']}/index.html")

    # 이용 안내(트랙 C 2차 웨이브) — guide_content.md(정본)를 제한 md 서브셋으로 결정론
    # 렌더. 라이브 데이터가 아니라 커밋 콘텐츠라 골든으로 고정된다. 파일 부재 시 조용히 생략.
    guide_md = load_guide()
    if guide_md:
        guide_title, guide_toc, guide_body = render_guide_html(guide_md)
        guide_html = env.get_template("guide.html").render(
            page_title="이용 안내 · GRM",
            rel_root="../",
            nav_active="guide",
            latest_slug=latest_slug,
            description=GUIDE_DESCRIPTION,
            canonical=_abs_url("guide/"),
            guide_title=guide_title,
            guide_toc=guide_toc,
            guide_body=guide_body,
        )
        _write(out_dir / "guide" / "index.html", guide_html)
        written.append("guide/index.html")

    # 용어사전(트랙 C 2차 웨이브) — glossary.json(정본)을 초성 색인 1페이지로 결정론 렌더.
    # 클라이언트 필터는 assets/glossary.js(신규·별도 asset). 파일 부재 시 조용히 생략.
    # nav_active="glossary"(8차 웨이브 A 2026-07-18 — nav 에 용어사전 전용 탭 신설).
    glossary_terms = load_glossary()
    glossary_term_ids: list[str] = []
    if glossary_terms:
        # B2: 관련 조항 라벨 → 공식 원문 URL — 자료실 커밋 카탈로그 재사용(신규 수집 0).
        # [C1] 용어→사례 링크: glossary_cases.json(정본, findings_search RPC 실측치).
        glossary_view = build_glossary_view(
            glossary_terms, _load_reg_ref_catalogs(), load_glossary_cases())
        glossary_html = env.get_template("glossary.html").render(
            page_title="규제 용어사전 · GRM",
            rel_root="../",
            nav_active="glossary",
            latest_slug=latest_slug,
            description=GLOSSARY_DESCRIPTION,
            canonical=_abs_url("glossary/"),
            glossary=glossary_view,
        )
        _write(out_dir / "glossary" / "index.html", glossary_html)
        written.append("glossary/index.html")

        # [용어사전 낱개] 용어당 1 페이지 — 검색 유입 트랙. 색인 페이지와 **같은 뷰모델**을
        # 재사용한다(별도 가공 0 → 두 화면이 갈라질 수 없다). 정렬은 뷰모델 순서 그대로라
        # 결정론이고, sitemap 도 이 순서를 쓴다.
        #   · title 은 `glossary_term_page_title` — 실제 검색어 형태("OOS 뜻")에 맞추고
        #     SERP 절단선 안에 들어가도록 영문은 약어로 접는다.
        #   · rel_root 는 두 단계 위(`/glossary/{id}/` → 사이트 루트).
        #   · case_excerpts 는 커밋된 문서 정본에서 파생한 실제 지적 문장(순위 트랙).
        case_excerpts = build_glossary_case_excerpts(
            glossary_terms, docs_data, load_glossary_cases())
        for group in glossary_view["groups"]:
            for term in group["terms"]:
                term_html = env.get_template("glossary_term.html").render(
                    page_title=glossary_term_page_title(term),
                    rel_root="../../",
                    nav_active="glossary",
                    latest_slug=latest_slug,
                    description=glossary_term_description(term),
                    canonical=_abs_url(f"glossary/{term['id']}/"),
                    json_ld=build_glossary_term_json_ld(term),
                    term=term,
                    case_excerpts=case_excerpts.get(term["id"]) or [],
                )
                _write(out_dir / "glossary" / term["id"] / "index.html", term_html)
                written.append(f"glossary/{term['id']}/index.html")
                glossary_term_ids.append(term["id"])

    # [검색 유입] 분류·국가·기관 모음 페이지 — 축 색인 3장 + 항목 페이지 N장.
    # 축 색인을 함께 내는 이유: 항목 페이지가 sitemap 에만 있으면 사이트 구조에서 그
    # 페이지들에 닿는 내부 링크가 없다(내부 링크가 곧 색인 경로다).
    # facets·docs_data·doc_slugs 는 findings 셸 렌더 직전에 이미 읽어 두었다(진입 간선
    # 카드가 그 데이터를 필요로 한다) — 여기서 다시 읽지 않는다.
    # (경로, lastmod) — lastmod 는 데이터에 실제 날짜가 있을 때만 채운다.
    facet_paths: list[tuple[str, str]] = []
    if facets:
        agency_labels = facets.get("agency_labels") or {}
        measured_on = facets.get("measured_on") or ""
        for axis in facets.get("axes") or []:
            axis_key = axis["axis"]
            meta = FACET_AXES[axis_key]                 # 모르는 축 = KeyError(조용한 누락 금지)
            items = [build_facet_item_view(it, doc_slugs) for it in axis.get("items") or []]
            siblings = [{"slug": it["slug"], "label_ko": it["label_ko"]} for it in items]

            index_html = env.get_template("findings_facet_index.html").render(
                page_title=f"{meta['title']} · GRM",
                rel_root="../../",
                nav_active="findings",
                latest_slug=latest_slug,
                description=meta["index_lede"],
                canonical=_abs_url(f"findings/{meta['path']}/"),
                axis=meta, items=items, excluded=axis.get("excluded") or [],
                # 문서 목록 입구 — 문서 정본이 없으면 링크를 만들지 않는다(없는 페이지로
                # 보내는 링크는 무링크보다 나쁘다).
                doc_index_total=((docs_data or {}).get("totals") or {}).get("documents", 0)
                if render_doc_pages else 0,
            )
            _write(out_dir / "findings" / meta["path"] / "index.html", index_html)
            written.append(f"findings/{meta['path']}/index.html")
            # 축 색인의 갱신일 = 그 축 항목들이 실은 가장 최근 사례의 공개일.
            axis_mod = max((s.get("published_date") or ""
                            for it in items for s in it.get("samples") or []),
                           default="")
            facet_paths.append((f"findings/{meta['path']}/", axis_mod))

            for item in items:
                page_html = env.get_template("findings_facet.html").render(
                    page_title=f"{item['label_ko']} {meta['headline_suffix']} · GRM",
                    rel_root="../../../",
                    nav_active="findings",
                    latest_slug=latest_slug,
                    description=facet_description(axis_key, item, agency_labels),
                    canonical=_abs_url(f"findings/{meta['path']}/{item['slug']}/"),
                    axis=meta, item=item, siblings=siblings,
                    agency_labels=agency_labels, measured_on=measured_on,
                )
                _write(out_dir / "findings" / meta["path"] / item["slug"] / "index.html",
                       page_html)
                written.append(f"findings/{meta['path']}/{item['slug']}/index.html")
                item_mod = max((s.get("published_date") or ""
                                for s in item.get("samples") or []), default="")
                facet_paths.append(
                    (f"findings/{meta['path']}/{item['slug']}/", item_mod))

    # [검색 유입] 문서 단위 페이지 — 실사 보고서 1건 = 1페이지(지적 3건 이상).
    # 모음 페이지는 축마다 최근 6건만 싣기 때문에 나머지 본문은 여전히 정적으로 존재하지
    # 않는다. 여기가 그 구멍을 메운다.
    #   ★분류 라벨 → 모음 페이지 슬러그는 **facets 데이터에서 파생**한다(사본 금지). facets
    #     가 없거나 그 분류가 표본 미달로 페이지가 없으면 링크를 만들지 않는다 — 없는 페이지로
    #     보내는 링크가 무링크보다 나쁘다(자료실 조항 링크와 같은 판단).
    if docs_data:
        doc_agency_labels = docs_data.get("agency_labels") or (
            facets.get("agency_labels") if facets else {}) or {}
        cat_slug_by_label: dict[str, str] = {}
        for axis in (facets.get("axes") if facets else []) or []:
            if axis["axis"] != "category":
                continue
            for it in axis.get("items") or []:
                cat_slug_by_label[it["label_ko"]] = it["slug"]

        documents = docs_data.get("documents") or []
        # 제목은 슬러그별로 유일해야 한다 — 겹치면 검색 결과에서 서로 구분되지 않는다.
        doc_titles = build_doc_page_titles(documents)

        # 본문 → 용어 페이지 자동 링크(희소 용어 우선). 용어 정본이 없으면 조용히 꺼진다.
        term_link_index = build_doc_term_link_index(glossary_terms) if glossary_terms else []
        term_doc_freq = (build_doc_term_doc_freq(term_link_index, documents)
                         if term_link_index and render_doc_pages else {})

        # ── 내부 링크 구조 ───────────────────────────────────────────────────
        # 문서 페이지 3천 장을 sitemap 에만 올려두면 사이트 구조에서 그 페이지에 닿는
        # 경로가 없다(크롤이 느리고 중요도 신호도 안 붙는다). 용어사전·모음 페이지에는
        # 축 색인을 함께 냈으면서 문서 페이지에만 빠뜨렸던 것을 메운다:
        #   ① 기관×연도 목록 `/findings/docs/{agency}/{year}/` 와 그 색인 `/findings/docs/`
        #   ② 같은 업체의 다른 기록(문서끼리 직접 연결)
        #   ③ 모음 페이지의 사례 → 그 문서 페이지(build_facet_item_view 가 doc_slug 를 채움)
        by_firm: dict[str, list[dict[str, Any]]] = {}
        for d in documents:
            key = d.get("firm_key") or ""
            if key:
                by_firm.setdefault(key, []).append(d)

        # 기관×연도 그룹. 정렬은 전부 결정론(연도 내림차순·같은 연도 안은 날짜 내림차순).
        by_ay: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for d in documents:
            by_ay.setdefault((d["agency"], d["published_date"][:4]), []).append(d)
        for bucket in by_ay.values():
            bucket.sort(key=lambda x: (x["published_date"], x["slug"]), reverse=True)

        doc_agencies = sorted({a for a, _ in by_ay},
                              key=lambda a: -sum(len(v) for (aa, _), v in by_ay.items()
                                                 if aa == a))
        index_groups = [{
            "slug": a.lower(),
            "label_ko": doc_agency_labels.get(a, a),
            "total": sum(len(v) for (aa, _), v in by_ay.items() if aa == a),
            "years": [{"year": y, "count": len(by_ay[(a, y)])}
                      for y in sorted({yy for aa, yy in by_ay if aa == a}, reverse=True)],
        } for a in doc_agencies]

        newest_doc = max((d["published_date"] for d in documents), default="")
        facet_paths.append(("findings/docs/", newest_doc))
        for g in index_groups:
            for y in g["years"]:
                bucket_mod = max(
                    (d["published_date"]
                     for d in by_ay[(g["slug"].upper(), y["year"])]), default="")
                facet_paths.append(
                    (f"findings/docs/{g['slug']}/{y['year']}/", bucket_mod))

        # ★목록·색인 21장은 **스위치와 무관하게 항상** 낸다. 비싼 것은 개별 문서 3,202장뿐
        # (한 번에 ~27초)이고, 목록을 함께 끄면 sitemap·진입 카드·404 페이지가 가리키는
        # 곳이 테스트 빌드에서만 없어져 **링크 무결성 검사가 프로덕션과 다른 것을 보게 된다**
        # (실제로 404 링크 검사가 이걸 잡았다).
        _write(out_dir / "findings" / "docs" / "index.html",
               env.get_template("findings_doc_list.html").render(
                   page_title="문서로 찾기 · GRM",
                   rel_root="../../", nav_active="findings", latest_slug=latest_slug,
                   description=("규제기관이 공개한 실사 문서를 기관과 연도로 묶어"
                                " 찾아보실 수 있습니다. 지적 3건 이상이 우리말로"
                                " 정리된 문서 "
                                f"{docs_data['totals']['documents']:,}건입니다."),
                   canonical=_abs_url("findings/docs/"),
                   mode="index", heading="문서로 찾기",
                   lede=(f"규제기관이 공개한 실사 문서 "
                         f"<b>{docs_data['totals']['documents']:,}</b>건을 기관과 연도로"
                         " 묶었습니다. 문서 하나를 열면 그 실사에서 나온 지적을 모두"
                         " 우리말로 보실 수 있습니다."),
                   groups=index_groups))
        written.append("findings/docs/index.html")

        for g in index_groups:
            for y in g["years"]:
                bucket = by_ay[(g["slug"].upper(), y["year"])]
                _write(out_dir / "findings" / "docs" / g["slug"] / y["year"] / "index.html",
                       env.get_template("findings_doc_list.html").render(
                           page_title=f"{g['label_ko']} {y['year']}년 실사 문서 · GRM",
                           rel_root="../../../../", nav_active="findings",
                           latest_slug=latest_slug,
                           description=(f"{g['label_ko']}가 {y['year']}년에 공개한 실사"
                                        f" 문서 {y['count']:,}건의 지적사항을 우리말로"
                                        " 정리했습니다."),
                           canonical=_abs_url(
                               f"findings/docs/{g['slug']}/{y['year']}/"),
                           mode="list",
                           heading=f"{g['label_ko']} · {y['year']}년",
                           lede=(f"{g['label_ko']}가 {y['year']}년에 공개한 실사 문서"
                                 f" <b>{y['count']:,}</b>건입니다. 문서를 열면 그 실사의"
                                 " 지적을 모두 보실 수 있습니다."),
                           documents=bucket, agency_slug=g["slug"],
                           agency_label=g["label_ko"], year=y["year"],
                           sibling_years=g["years"]))
                written.append(
                    f"findings/docs/{g['slug']}/{y['year']}/index.html")

        for doc in documents:
            # sitemap 은 **데이터에서** 파생한다 — 렌더를 껐다고 URL 이 빠지면 테스트가 보는
            # sitemap 과 프로덕션 sitemap 이 달라져 골든 대조가 의미를 잃는다.
            facet_paths.append(
                (f"findings/doc/{doc['slug']}/", doc["published_date"]))
            if not render_doc_pages:
                continue
            related = [{"slug": cat_slug_by_label[label], "label_ko": label}
                       for label in (doc.get("categories") or [])
                       if label in cat_slug_by_label]
            # 같은 업체의 다른 기록 — 표시명이 아니라 정규화 키로 묶는다. 최신 6건만
            # 보여준다(30건짜리 업체가 있어 전부 실으면 목록이 본문을 덮는다).
            siblings = [s for s in by_firm.get(doc.get("firm_key") or "", [])
                        if s["slug"] != doc["slug"]]
            siblings.sort(key=lambda x: (x["published_date"], x["slug"]), reverse=True)
            same_firm = [{"slug": s["slug"], "published_date": s["published_date"],
                          "agency": s["agency"], "count": len(s["findings"])}
                         for s in siblings[:6]]
            # 용어 링크는 렌더 직전에 본문 조각별로 끼운다. `used` 가 페이지 단위라 같은
            # 용어가 여러 지적에 나와도 첫 곳 하나만 링크된다.
            selected = (select_doc_term_links(doc, term_link_index, term_doc_freq)
                        if term_link_index else [])
            linked_used: set[str] = set()
            finding_bodies = [
                link_terms_in_text(f.get("text_ko") or "", selected, "../../../", linked_used)
                for f in doc.get("findings") or []
            ]
            doc_html = env.get_template("findings_doc.html").render(
                page_title=f"{doc_titles[doc['slug']]} · GRM",
                rel_root="../../../",
                nav_active="findings",
                latest_slug=latest_slug,
                description=doc_page_description(doc, doc_agency_labels),
                canonical=_abs_url(f"findings/doc/{doc['slug']}/"),
                doc=doc, agency_labels=doc_agency_labels,
                source_label=doc_source_label(doc),
                related_categories=related, same_firm=same_firm,
                finding_bodies=finding_bodies,
            )
            _write(out_dir / "findings" / "doc" / doc["slug"] / "index.html", doc_html)
            written.append(f"findings/doc/{doc['slug']}/index.html")

    # 주간 퀴즈(트랙 C) — quiz_bank.json(정본)의 전 문항을 결정론 embed. "이번 주" 선택은
    # 렌더러가 하지 않고(now() 금지) 클라이언트 assets/quiz.js 가 ISO 주차 키로 결정론 회전
    # 선택한다(같은 주 = 전 직원 동일 세트). 파일 부재 시 조용히 생략.
    quiz_bank = load_quiz_bank()
    if quiz_bank:
        quiz_html = env.get_template("quiz.html").render(
            page_title="주간 퀴즈 · GRM",
            rel_root="../",
            nav_active="guide",
            latest_slug=latest_slug,
            description=QUIZ_DESCRIPTION,
            canonical=_abs_url("quiz/"),
            quiz=build_quiz_view(quiz_bank),
        )
        _write(out_dir / "quiz" / "index.html", quiz_html)
        written.append("quiz/index.html")

    # 검색 인덱스(P4 — 정적 클라이언트사이드 검색용). assets 옆에 둔다(archive.js 가 fetch).
    search_index = build_search_index(briefs, issue_no_by_date, latest_slug)
    _write_json(dist_assets / "search-index.json", search_index)
    written.append("assets/search-index.json")

    # 마이페이지(/me) — 반응 계층 활성 시에만 생성(env-off=페이지 부재→골든 byte-diff 0).
    # 개인화 페이지라 sitemap/canonical 제외(비색인). 스크랩·관심 업체는 reactions.js 가,
    # 구름이 성장 현황은 growth.js 가 런타임 렌더(정적 셸·콘텐츠 골든 불침범).
    # nav_active="me" — nav 6탭 중 어느 것도 이 페이지를 대표하지 않으므로 아무 탭도 켜지
    # 않는다(13차 이전엔 "board"라 무관한 '모아보기'가 활성으로 보였다).
    if env.globals.get("reactions_enabled"):
        me_html = env.get_template("me.html").render(
            page_title="마이페이지 · GRM",
            rel_root="../",
            nav_active="me",
            latest_slug=latest_slug,
        )
        _write(out_dir / "me" / "index.html", me_html)
        written.append("me/index.html")
        admin_html = env.get_template("admin.html").render(
            page_title="Admin · GRM",
            rel_root="../",
            nav_active="admin",
            latest_slug=latest_slug,
            description="",
            canonical="",
            json_ld="",
            newsletter_form_action="",
            reactions_enabled=False,
        )
        _write(out_dir / "admin" / "index.html", admin_html)
        written.append("admin/index.html")

    # 브리프 상세(주차별).
    brief_tmpl = env.get_template("brief.html")
    for b in briefs:
        pub = b["brief"].get("publish_date", "")
        issue_no = issue_no_by_date[pub]
        renderable = [c for c in (b.get("cards") or []) if _is_renderable(c)]
        cards_sorted = sorted(renderable,
                              key=lambda c: (c.get("render_order") is None,
                                             c.get("render_order")))
        card_views = [_card_view(c) for c in cards_sorted]
        _annotate_toc_distinguishers(card_views)        # P1-1: 동명 카드 목차 구분자
        sections = _build_sections(card_views)
        ctx = _brief_context(b, issue_no)
        html = brief_tmpl.render(
            page_title=f"{ctx['title_dateform']} 규제뉴스 · GRM",
            rel_root="../../",
            nav_active="detail",
            latest_slug=latest_slug,
            description=_brief_description(b["brief"]),
            canonical=_abs_url(f"briefs/{pub}/"),
            brief=ctx,
            sections=sections,
        )
        _write(out_dir / "briefs" / pub / "index.html", html)
        written.append(f"briefs/{pub}/index.html")
        # [성장 3차] 링크드인/커뮤니티 공유 초안 — tldr(큐레이션된 핵심)+절대 URL 을 고정
        # 경로(briefs/{pub}/share.txt)로 낸다. 운영 루틴: 발행 후 이 URL 을 열어 복사·
        # 다듬어 게시(주 5분). 내용이 공개 브리프 요약뿐이라 공개 무해·sitemap 비등록.
        # tldr 이 비면 불릿 없이 헤더+링크만 남는다(파일 존재는 항상 — 경로 예측 가능성).
        share_lines = [f"[GRM 주간 규제뉴스 · {ctx['title_dateform']}]", ""]
        share_lines += [f"· {t}" for t in (ctx.get("tldr") or [])]
        share_lines += ["", f"이번 주 전체 보기: {_abs_url(f'briefs/{pub}/')}", "",
                        "#GMP #제약규제 #품질관리 #RegulatoryIntelligence"]
        _write(out_dir / "briefs" / pub / "share.txt",
               "\n".join(share_lines) + "\n")
        written.append(f"briefs/{pub}/share.txt")

    # 검색 노출(robots.txt + sitemap.xml) — 정적·결정론(입력 publish_date 파생).
    # [검색 유입] 404 페이지 — Cloudflare Pages 는 `/404.html` 이 **있을 때만** 매칭되지 않는
    # 경로에 404 를 돌려준다. 없으면 루트 index.html 을 **200 으로** 준다(soft 404) — 실측으로
    # `/findings/doc/zzz-does-not-exist/` 가 랜딩 페이지를 200 으로 돌려주고 있었다.
    # 문서 페이지 3천 장이 매주 재생성되며 낡은 URL 이 계속 생기는 구조라 특히 중요하다.
    # [검색 유입] RSS — 네이버 서치어드바이저가 사이트맵과 **별개로 받는 채널**이고,
    # 피드 리더·사내 그룹웨어 위젯에도 그대로 쓰인다. 내용은 주간 브리프로 한정한다
    # (지적사항 문서는 시간순 발행물이 아니라 참조 자료라 sitemap 의 몫이다).
    _write(out_dir / "rss.xml", build_rss_xml(briefs))
    written.append("rss.xml")

    _write(out_dir / "404.html", env.get_template("404.html").render(
        page_title="페이지를 찾을 수 없습니다 · GRM",
        rel_root="", nav_active="", latest_slug=latest_slug,
        description="찾으시는 페이지가 없습니다. 지적사항 검색·자료실·용어사전에서 다시 찾아보세요.",
        canonical="",
    ))
    written.append("404.html")

    _write(out_dir / "robots.txt", build_robots_txt(
        disallow_admin=bool(env.globals.get("admin_enabled"))))
    written.append("robots.txt")
    # llms.txt — AI 어시스턴트용 안내. sitemap 과 같은 입력에서 파생(결정론).
    _write(out_dir / "llms.txt",
           build_llms_txt(briefs, glossary_term_ids=glossary_term_ids,
                          facet_paths=facet_paths))
    written.append("llms.txt")
    _write(out_dir / "sitemap.xml",
           build_sitemap_xml(briefs, glossary_term_ids=glossary_term_ids,
                             facet_paths=facet_paths))
    written.append("sitemap.xml")

    return {"out_dir": str(out_dir), "written": written,
            "briefs": len(briefs), "latest": latest_slug}


class Fda483ObservationValidationError(ValueError):
    """483 Observation 발행 게이트 위반(§16) — fail-closed. main() 전용(하단 참조)."""


# ── [483 발행 게이트 2026-07-14] Observation 상세 영문전용/서명푸터 오탐 차단 ──────────
# 실사고: 7/13 발행본에서 전 카드 deficiency_ko/detail_ko 백필 누락(조용한 결손)이 그대로
# 나갔고, 한 observation 의 detail 에 서명블록 OCR 잔재(EMPt..oYEECS) SIGNATURE ... 등)가
# 남아 원문(영문)조차 아닌 깨진 텍스트가 발행됐다. render_site()/build 헬퍼(=web/tests/
# test_render.py 가 fixture 로 직접 호출)에는 절대 넣지 않는다 — 여기 넣으면 골든/픽스처
# 테스트가 이 게이트에 얽매인다. 대신 main()(=배포 워크플로가 실행하는 `python web/render.py`
# 유일 경로) 안에서만, 실제 배포 대상 데이터를 검증한다.
_FOOTER_GARBAGE_RE = re.compile(
    r"(?-i:EMP)\S{0,6}?OY"           # 서명블록 EMPLOYEE(S) 마커(OCR 변형 포함) — 대문자 EMP 고정
    r"|(?-i:SIGNATURE|SIGJ)"          # SIGNATURE / OCR 변형 SIGJ… — 대문자 고정(소문자 산문 오탐 방지)
    r"|(?-i:AMENDMENT)"               # 483 양식 하단 개정 스탬프. EMP/SIGNATURE/DATE ISSUED 가
    #   OCR 로 완전히 파괴된 서명블록("AMENDMENT 1 Et,40LOYE£ SIS G•.,-.n,,~ oi:.1e 1ssueo",
    #   "AMENDMENTl EJ·.tP!.OYEE{S) Sa'.:;!l.\'ATI..RE OA\"E SSUED" — 2026-07-20 193490 실측)은
    #   위 마커가 전부 실패해 **게이트를 통과했다**(잔재가 그대로 발행될 뻔한 침묵 결함).
    r"|\bSEE\s+REVERSE\b"
    r"|\bFORM\s+FDA\s*4"
    # [오탐 수리 2026-07-27] 종전 `\bInvestigator\b` 는 **관찰 산문 자체**를 푸터로 오인했다 —
    # 483 본문에는 "Investigator Piechocki noted materials came off loose…" 처럼 실사관 이름을
    # 앞세운 서술이 흔하다(fda483-192342 obs#5 실측: 이 오탐 하나로 그 주 브리프 전체 발행이
    # 막혔다). 서명블록의 형태는 반대다 — 이름이 앞, 직함이 뒤(`Juanelma H Palmer, Investigator`).
    # 그 어순 차이를 그대로 조건에 둔다: 쉼표 뒤에 오면서 **뒤에 사람 이름이 오지 않는**
    # Investigator 만 푸터로 본다. (쉼표 조건만으로는 부족하다 — 산문도 "Specifically,
    # Investigator Piechocki…" 로 쉼표가 앞에 온다. 결정적 차이는 **뒤**에 이름이 오느냐다.)
    r"|,\s*Investigator\b(?!\s+[A-Z][a-z])"
    r"|\bPAGE\s+\d+\s+OF\s+\d+\b",
    re.I,
)


def validate_483_observations(cards_or_briefs: list[dict[str, Any]]) -> list[str]:
    """FDA 483 Observation 카드 발행 게이트 — 브리프 리스트(각 {"brief":…, "cards":[...]}
    형태, load_briefs() 산출 그대로) 또는 카드 리스트를 받아 위반 목록(사람이 읽을 문자열)을
    돌려준다. 위반 0건이면 빈 리스트(호출측이 raise 여부 결정 — 순수 함수, 부작용 없음).

    검사 대상 = deterministic_detail.type == "fda_483_observations" 인 카드의 observations
    각 건:
      1. deficiency_ko 비어있음 → MISSING_DEFICIENCY_KO
      2. detail 비어있지 않은데 detail_ko 비어있음 → MISSING_DETAIL_KO
      3. detail 에 서명/양식 푸터 OCR 잔재(_FOOTER_GARBAGE_RE) 검출 → FOOTER_GARBAGE
    """
    violations: list[str] = []

    def _check_card(card: dict[str, Any], brief_label: str) -> None:
        dd = card.get("deterministic_detail")
        if not isinstance(dd, dict) or dd.get("type") != "fda_483_observations":
            return
        card_id = card.get("id") or card.get("render_order") or "?"
        for obs in (dd.get("observations") or []):
            num = obs.get("number", "?")
            loc = f"{brief_label} / card {card_id} / obs #{num}"
            if not (isinstance(obs.get("deficiency_ko"), str) and obs.get("deficiency_ko").strip()):
                violations.append(f"{loc}: MISSING_DEFICIENCY_KO")
            detail = obs.get("detail")
            if isinstance(detail, str) and detail.strip():
                if not (isinstance(obs.get("detail_ko"), str) and obs.get("detail_ko").strip()):
                    violations.append(f"{loc}: MISSING_DETAIL_KO")
                if _FOOTER_GARBAGE_RE.search(detail):
                    violations.append(f"{loc}: FOOTER_GARBAGE")

    for item in cards_or_briefs:
        if "brief" in item and "cards" in item:
            label = item["brief"].get("publish_date") or item["brief"].get("run_date_kst") or "?"
            for card in (item.get("cards") or []):
                _check_card(card, label)
        else:
            _check_card(item, "?")

    return violations


def _validate_briefs_or_raise(data_dir: Path) -> None:
    """main() 전용 fail-closed 게이트 호출부. 실제 배포 대상(`--data`) 브리프를 로드해
    검증하고, 위반이 하나라도 있으면 즉시 raise(빌드 전체 실패 → CI red)."""
    briefs = load_briefs(data_dir)
    violations = validate_483_observations(briefs)
    if violations:
        raise Fda483ObservationValidationError(
            "483 Observation 발행 게이트 위반 — 발행 차단(brief file / card id / "
            "observation number / fail code):\n" + "\n".join(f"  · {v}" for v in violations)
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GRM 웹 렌더러 (JSON → 정적 사이트)")
    ap.add_argument("--data", type=Path, default=DATA_DIR, help="브리프 JSON 디렉터리")
    ap.add_argument("--out", type=Path, default=DIST_DIR, help="정적 사이트 출력 디렉터리")
    args = ap.parse_args(argv)
    _validate_briefs_or_raise(args.data)  # fail-closed — 위반 시 여기서 raise, exit 0 도달 안 함
    meta = render_site(args.data, args.out)
    print(f"빌드 완료: {meta['briefs']}개 브리프 → {meta['out_dir']}  "
          f"(최신호 {meta['latest']}, {len(meta['written'])}개 파일)")
    for w in meta["written"]:
        print(f"  · {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
