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
import unicodedata
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
FINDINGS_DOCS_FILE = WEB_DIR / "data" / "findings_docs.json"      # [검색 유입] 문서 단위 페이지 정본(findings_docs_refresh.py · 임계 3 + 소스 소거 면제)
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

# [다국어 2단계 2026-09-03] 문구 사전 — 키는 한국어 원문, 한국어 빌드는 항등(바이트 불변).
# `tr("…")` 은 render_site 가 언어별로 만드는 번역기, `N_("…")` 은 모듈 상수를 키로
# 표시만 하는 no-op(번역은 쓰는 자리에서 `tr(상수)`). 헬퍼 함수는 `tr=_KO` 기본값으로
# 한국어를 유지한다(테스트가 헬퍼를 직접 부르는 계약 불변). 상세는 web/grm_i18n.py.
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))
from grm_i18n import (  # noqa: E402
    KO as _KO, SUPPORTED_LANGS, Translator, build_js_catalog, noop as N_,
)

# ── 언어 트리 상수 ────────────────────────────────────────────────────────────
# ★첫 사용처(_library_item_view)보다 위에 있어야 한다 — 아래 PagePath 옆에 두었더니
#   import 시점에 NameError 였다(정의 순서가 강제되는 자리).
DEFAULT_LANG = "ko"
LANG_PREFIXES: dict[str, str] = {"ko": "", "en": "en/"}
# 언어 전환 UI·hreflang 이 쓰는 표시 이름(그 언어의 자칭 — 번역 대상이 아니다).
LANG_ENDONYM: dict[str, str] = {"ko": "한국어", "en": "English"}
# `og:locale` — 언어별 고정값(번역 대상 아님).
LANG_OG_LOCALE: dict[str, str] = {"ko": "ko_KR", "en": "en_US"}

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


def title_dateform(publish_date: str, tr: Translator = _KO) -> str:
    """publish_date → "{Y}년 {M}월 {N}주차". 주차 = (day-1)//7 + 1 (결정론)."""
    y, m, d = _date_parts(publish_date)
    week = (d - 1) // 7 + 1
    return tr("{y}년 {m}월 {week}주차", y=y, m=m, week=week)


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
def _deep_preview(da: dict[str, Any] | None, tr: Translator = _KO) -> str:
    """분석층(deep) 접힘 summary 에 붙는 내용 힌트 — 펼치기 전에 무엇이 들었는지 스캔용.
    유형별 ②섹션명으로 구분: admin=처분근거(disposition_basis)·483=실사의미
    (inspectional_significance)·WL=대응조치(기본). 결정론(값 재생성 0)."""
    if not isinstance(da, dict):
        return ""
    kv = da.get("key_violations")
    n = len(kv) if isinstance(kv, list) else 0
    if da.get("disposition_basis"):
        mid = tr("처분근거")
    elif da.get("inspectional_significance"):
        mid = tr("실사의미")
    else:
        mid = tr("대응조치")
    parts = ([tr("위반 {n}건", n=n)] if n else []) + [mid, tr("행정리스크")]
    return " · ".join(parts)


def _detail_preview(dd: dict[str, Any] | None, tr: Translator = _KO) -> str:
    """결정론 상세(deterministic_detail) 접힘 summary 힌트. fda_483_observations 는 Observation
    건수. gmp_deficiencies 는 card.html 이 자체 '· N건' 힌트를 쓰므로 빈 문자열."""
    if not isinstance(dd, dict):
        return ""
    if dd.get("type") == "fda_483_observations":
        return tr("Observation {n}건", n=dd.get("count") or 0)
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
def _card_view(card: dict[str, Any], tr: Translator = _KO) -> dict[str, Any]:
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
            "text": (tr("PDF 원문") if is_pdf else tr("공식 페이지")),
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
        "merged_noun": card.get("merged_noun") or tr("품목"),
        "quotes": quotes,
        "quote_label": ((tr("원문 및 번역") if any_trans else tr("원문")) if quotes_in else None),
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
        "deep_preview": _deep_preview(card.get("deep_analysis"), tr),
        "detail_preview": _detail_preview(card.get("deterministic_detail"), tr),
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
    {"slug": "ich", "short": N_("ICH"), "file": "ich.json", "unit": N_("토픽"), "kick": "ICH · Guidelines",
     "title": N_("ICH 가이드라인 카탈로그"),
     "blurb": N_("FDA·EMA·식약처가 공통으로 채택하는 국제 조화 가이드라인. 품질(Q)·다분야(M) 계열별 토픽을 한글 명칭과 함께 정리."),
     "intro": N_("FDA·EMA·식약처가 공통으로 채택하는 국제 조화(ICH) 가이드라인의 토픽 카탈로그입니다. 품질(Q)·다분야(M) 계열별로 한글 명칭을 병기해 정리했으며, 현행 문서가 공개된 토픽은 공식 원문 PDF로 바로 연결됩니다. 식약처 한글 번역본이 있는 토픽은 번역본 링크를 함께 제공합니다. 최신 Step·개정 현황은 계열별 ICH 공식 카탈로그 페이지에서 확인하실 수 있습니다."),
     "desc": N_("ICH Q(품질)·M(다분야) 가이드라인 토픽 카탈로그 — 코드·한글 명칭 병기, 원문 PDF·식약처 번역본·ICH 공식 카탈로그 링크."),
     "public_base": "https://www.ich.org/",
     "link_label": N_("ICH 공식 카탈로그"),
     "doc_type_labels": {"guideline-topic": ""},
     "groups_by_url": [
         {"contains": "quality-guidelines", "badge": "Q", "label": N_("품질"), "label_en": "Quality"},
         {"contains": "multidisciplinary-guidelines", "badge": "M", "label": N_("다분야"), "label_en": "Multidisciplinary"},
     ]},
    {"slug": "mfds", "short": N_("식약처"), "file": "mfds.json", "unit": N_("건"), "kick": "MFDS · Guidance",
     "title": N_("MFDS 지침·고시 아카이브"),
     "blurb": N_("식약처가 공개한 지침·안내서·고시·행정예고. 주간 브리프에서 다룬 뒤에도 다시 찾아볼 수 있는 누적 목록."),
     "intro": N_("식약처(MFDS)가 공개한 지침·안내서·고시·행정예고를 발행일 순으로 모았습니다. 주간 브리프에서 한 번 다룬 문서도 이곳에서 다시 찾아볼 수 있습니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("식약처(MFDS) 지침·안내서·고시·행정예고 아카이브 — 제목·유형·발행일·공식 원문 링크."),
     "sort": "published_desc",
     "doc_type_labels": {"guidance-internal": N_("공무원 지침서"), "guidance-industry": N_("민원인 안내서·지침"),
                         "legislative-notice": N_("입법·행정예고"), "notice-final": N_("고시 전문")}},
    {"slug": "eu-gmp", "short": N_("EU GMP"), "file": "eu_gmp.json", "unit": N_("건"), "kick": "EU · EudraLex Vol 4",
     "title": N_("EU GMP 기준서 (EudraLex Vol 4)"),
     "blurb": N_("유럽연합 의약품 GMP 기준서. Part I·II·III 각 장과 부속서(Annex)를 구조 순서대로 정리."),
     "intro": N_("유럽연합 의약품 GMP 기준서(EudraLex Volume 4)의 문서 목록입니다. Part I(기본 요건)·Part II(원료의약품)·Part III(보조 문서)과 부속서(Annex)를 기준서 구조 순서대로 정리했으며, 각 문서의 공식 원문 PDF로 바로 연결됩니다. 법적 효력과 최신 개정본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("EU GMP 기준서(EudraLex Volume 4) 문서 목록 — Part I·II·III과 부속서(Annex), 공식 원문 PDF 링크.")},
    {"slug": "pics", "short": N_("PIC/S"), "file": "pics.json", "unit": N_("건"), "kick": "PIC/S · GMP Guide",
     "title": N_("PIC/S GMP 가이드"),
     "blurb": N_("의약품실사상호협력기구(PIC/S)의 GMP 가이드(PE 009)와 부속서·가이던스 문서 목록."),
     "intro": N_("의약품실사상호협력기구(PIC/S)가 공개한 GMP 가이드(PE 009) 각 부와 부속서, 관련 가이던스 문서를 발행일 순으로 정리했습니다. 식약처를 포함한 PIC/S 가입 규제기관의 실사 기준과 맞닿아 있는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("PIC/S GMP 가이드(PE 009)·부속서·가이던스 문서 목록 — 발행일·공식 원문 링크."),
     "sort": "published_desc"},
    {"slug": "who", "short": N_("WHO"), "file": "who.json", "unit": N_("건"), "kick": "WHO · TRS Annexes",
     "title": N_("WHO TRS 부속서 모음"),
     "blurb": N_("WHO 전문가위원회 기술보고서(TRS) 부속서 중 GMP·품질 관련 문서 선별 목록."),
     "intro": N_("세계보건기구(WHO) 의약품 표준 전문가위원회 기술보고서(TRS)의 부속서 가운데 GMP·품질 관련 문서를 발행일 순으로 선별해 정리했습니다. WHO 사전적격성평가(PQ)나 국제 조달 요건을 다룰 때 기준이 되는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("WHO 기술보고서(TRS) 부속서 중 GMP·품질 문서 선별 목록 — 발행일·공식 원문 링크."),
     "sort": "published_desc"},
    {"slug": "fda-guidance", "short": N_("FDA"), "file": "fda_guidance.json", "unit": N_("건"), "kick": "FDA · Guidance",
     "title": N_("FDA 가이던스 문서"),
     "blurb": N_("FDA가 공개한 의약품 GMP·품질 관련 가이던스 문서 선별 목록."),
     "intro": N_("미국 FDA가 공개한 의약품 GMP·품질 관련 가이던스 문서를 발행일 순으로 선별해 정리했습니다. 가이던스는 FDA의 현재 견해를 담은 권고 문서로, 법적 구속력이 있는 규정(CFR)과는 구분해 읽어야 합니다. 최신 개정 여부는 반드시 공식 원문에서 확인하세요."),
     "desc": N_("FDA 의약품 GMP·품질 가이던스 문서 선별 목록 — 발행일·유형·공식 원문 링크."),
     "sort": "published_desc"},
    # [자료실 배치 2026-08-11] 같은 관할은 붙여 둔다. 미국은 "가이던스(권고) → 21 CFR(법령)",
    #   유럽은 "EudraLex Vol 4(기준서) → EMA(가이던스)" 로 이미 쌍을 이루고 있었는데, 신규
    #   2종(cfr·mhra)을 registry 끝에 append 했더니 21 CFR 이 FDA 가이던스와 네 칸 떨어져
    #   화면에서 "왜 FDA 자료가 두 군데냐"로 읽혔다(사용자 지적). 분리 자체는 유지한다 —
    #   입도가 다르기 때문이다(가이던스 86건 = 문서 단위, 21 CFR 63건 = 조항 단위. 합치면
    #   "FDA 149건"이 무엇을 센 숫자인지 알 수 없게 되고, findings 의 cfr_refs 가 조항으로
    #   바로 갈 수 있는 조인 축도 사라진다). 배치와 상호 참조 문구로 관계를 밝힌다.
    {"slug": "cfr", "short": N_("21 CFR"), "file": "cfr.json", "unit": N_("개 조항"), "kick": "US · 21 CFR",
     "title": N_("미국 연방규정 21 CFR (GMP)"),
     "blurb": N_("미국 연방규정(CFR) 중 의약품 GMP 조항 원문. 가이드라인이 아니라 법령 그 자체 — Part 210(총칙)·Part 211(완제의약품 CGMP) 전 조항을 조문 단위로 수록."),
     "intro": N_("미국 연방규정집(Code of Federal Regulations) Title 21 가운데 의약품 현행 우수제조관리기준(CGMP)을 담은 Part 210(총칙)과 Part 211(완제의약품 CGMP) 전 조항을 조문 단위로 정리했습니다. 자료실의 다른 컬렉션이 가이드라인·기준서인 것과 달리 이 컬렉션은 법적 구속력을 갖는 규정 원문 그 자체입니다. FDA가 권고 형태로 내는 문서는 바로 앞의 'FDA 가이던스 문서' 컬렉션에 있습니다. 각 조항은 공식 원문(eCFR)으로 바로 연결됩니다. 개정 이력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("미국 연방규정(21 CFR) Part 210(총칙)·Part 211(완제의약품 CGMP) 조항 목록 — 조번호·제목·공식 원문(eCFR) 링크."),
     "public_base": "https://www.ecfr.gov/current/title-21",
     "doc_type_labels": {"regulation-section": N_("규정 조항")},
     "groups_by_url": [
         {"contains": "/part-210/", "badge": "210", "label": N_("총칙"), "label_en": "General Provisions"},
         {"contains": "/part-211/", "badge": "211", "label": N_("완제의약품 CGMP"), "label_en": "Finished Pharmaceuticals"},
     ]},
    {"slug": "ema", "short": N_("EMA"), "file": "ema.json", "unit": N_("건"), "kick": "EMA · Guidance",
     "title": N_("EMA GMP·품질 가이드라인"),
     "blurb": N_("유럽의약품청(EMA)이 공개한 GMP 관련 절차·과학 가이드라인과 질의응답(Q&A) 선별 목록."),
     "intro": N_("유럽의약품청(EMA)이 공개한 GMP·품질 관련 문서를 발행일 순으로 선별해 정리했습니다. 실사 당국 품질 시스템, 품질 결함 보고·신속 경보 처리 등 규제 절차 가이드라인과 과학 가이드라인, 질의응답(Q&A)을 포함합니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("EMA GMP·품질 절차·과학 가이드라인과 질의응답(Q&A) 선별 목록 — 발행일·유형·공식 원문 링크."),
     "sort": "published_desc",
     "public_base": "https://www.ema.europa.eu/",
     "doc_type_labels": {"regulatory-procedural-guideline": N_("규제·절차 가이드라인"),
                         "scientific-guideline": N_("과학 가이드라인"),
                         "questions-and-answers": N_("질의응답(Q&A)")}},
    {"slug": "mhra", "short": N_("MHRA"), "file": "mhra.json", "unit": N_("건"), "kick": "UK · MHRA",
     "title": N_("MHRA GMP·GDP 가이던스"),
     # [정적 연도 제거 2026-08-12] 옛 문구는 "2019년 이후 갱신 없음"이라고 못박아 뒀다.
     # 외부 기관 상태를 정적으로 단정한 것이라, MHRA 가 새 통계를 내는 순간 조용히 거짓이
     # 된다(카탈로그는 매주 자동 갱신되는데 이 문장만 안 바뀐다). 경고는 유지하되 연도는
     # 아래 목록이 스스로 보여주게 넘긴다.
     "blurb": N_("영국 MHRA의 GMP·GDP 컴플라이언스 정보시트·실사 결함통계·가이던스 문서 목록."),
     "intro": N_("영국 의약품·의료제품규제청(MHRA)이 공개한 GMP·GDP 관련 문서를 정보시트·실사 결함통계·가이던스로 나누어 정리했습니다. 컴플라이언스 매니지먼트(Compliance Management)·규제조치(Regulatory Action) 절차를 설명하는 정보시트, 실사에서 반복 확인되는 결함 유형을 다룬 GMP 실사 결함통계, 실사 대응·분산형 제조 등 개별 주제를 다루는 가이던스·공지 문서를 포함합니다. GMP 실사 결함통계 시리즈는 발행 간격이 길어 목록의 최신 자료가 몇 해 전일 수 있습니다 — 아래 각 문서의 발행 연도를 확인하시고, 오래된 통계를 현재 실사 경향으로 그대로 참고하지 마세요. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("MHRA(영국) GMP·GDP 컴플라이언스 정보시트·실사 결함통계·가이던스 문서 목록 — 제목·유형·공식 원문 링크."),
     "doc_type_labels": {"information-sheet": N_("정보시트"), "gmp-deficiency-statistics": N_("GMP 실사 결함통계"),
                         "detailed_guide": N_("가이던스"), "notice": N_("공지"),
                         "transparency": N_("투명성 공개"), "guidance": N_("가이던스 자료")}},
    {"slug": "health-canada", "short": N_("Health Canada"), "file": "health_canada.json", "unit": N_("건"),
     "kick": "Health Canada · GMP",
     "title": N_("Health Canada GMP 가이드"),
     "blurb": N_("캐나다 보건부(Health Canada)의 GMP 가이드(GUI 시리즈) 문서 목록."),
     "intro": N_("캐나다 보건부(Health Canada)가 공개한 GMP 가이드(GUI 시리즈) 문서를 발행일 순으로 정리했습니다. 의약품 GMP 실사와 시설 허가(Establishment Licence) 운영의 기준이 되는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요."),
     "desc": N_("Health Canada GMP 가이드(GUI 시리즈) 문서 목록 — 코드·발행일·공식 원문 링크."),
     "sort": "published_desc",
     "public_base": "https://www.canada.ca/en/health-canada.html",
     "doc_type_labels": {"guidance": N_("가이던스")}},
    {"slug": "pmda", "short": N_("PMDA"), "file": "pmda.json", "unit": N_("건"),
     "kick": "PMDA · Inspection Cases",
     "title": N_("PMDA 실사 지적사례 (ORANGE Letter)"),
     "blurb": N_("일본 PMDA가 공개한 GMP 실사 지적사례(ORANGE Letter) 영문판과 GMP/GCTP 연차보고서 목록."),
     "intro": N_("일본 의약품의료기기종합기구(PMDA)가 공개한 GMP 실사 지적사례(ORANGE Letter)의 영문판과 GMP/GCTP 연차보고서를 정리했습니다. ORANGE Letter는 특정 업체가 아니라 실사에서 반복 확인되는 결함 유형(기록 부적정·CAPA 미흡·무균 환경모니터링 등)의 배경·위험·점검 포인트를 익명 케이스로 설명하는 자료입니다. 각 문서의 공식 원문 PDF로 바로 연결됩니다. 영문판은 일본어 원문 대비 시차가 있을 수 있으며, 최신 현황은 공식 원문에서 확인하세요."),
     "desc": N_("일본 PMDA GMP 실사 지적사례(ORANGE Letter) 영문판·GMP/GCTP 연차보고서 목록 — 제목·유형·공식 원문 PDF 링크."),
     "public_base": "https://www.pmda.go.jp/english/review-services/gmp-qms-gctp/0007.html",
     "groups_by_doc_type": [
         {"doc_type": "inspection-observation", "label": N_("실사 지적사례"), "label_en": "Inspection Cases"},
         {"doc_type": "annual-report", "label": N_("연차보고서"), "label_en": "Annual Reports"},
     ],
     "doc_type_labels": {"inspection-observation": N_("실사 지적사례"), "annual-report": N_("연차보고서")}},
]


def _library_item_view(it: dict[str, Any], lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """카탈로그 항목 → 공통 항목 뷰 — 스키마 v2(값 무변형 통과).

    표시 제목은 한국어 우선: title_ko 가 있으면 주 제목, title_en 은 병기 줄(sub)로
    내린다(한국어 사이트 — MFDS/ICH 병기). 선택 필드(code·doc_type·published_date·
    ko_url·pdf_url)는 있으면 표시, 없으면 빈 문자열 → 템플릿이 조용히 생략. 날짜는
    **발행일(published_date)만** 노출 — 수집일 등 내부 운영 개념은 사용자 표기 금지
    (품질 기준 2026-07-18)."""
    title_en = it.get("title_en") or it.get("title") or ""
    title_ko = it.get("title_ko") or ""
    # [다국어 3단계] 표시 제목은 **읽는 사람의 언어를 먼저** 고른다 — 한국어판은 title_ko,
    # 영어판은 title_en, 없으면 있는 쪽으로 떨어지고 병기 줄(sub)은 반대편이 된다.
    #   ★영문 제목이 없는 항목(실측: 식약처 99건 중 40건, 나머지 10개 카탈로그는 100% 보유)은
    #     영어판에서도 한국어 제목 그대로다. 그 문서의 **실제 이름**이 한국어이기 때문이고,
    #     여기서 지어낸 영어 제목을 붙이면 원문에 없는 이름을 만드는 것이다(무변형 규율).
    if lang != DEFAULT_LANG:
        # ★영어판에는 한국어 제목을 병기하지 않는다 — 한국어판의 병기는 영문 원제를
        #   확인시켜 주는 장치인데, 그 반대는 영어 독자에게 읽을 수 없는 줄을 하나 더
        #   얹을 뿐이다. 영문 제목이 없으면 한국어 제목 하나만 남는다(그 문서의 실제 이름).
        primary, secondary = (title_en or title_ko), ""
    else:
        primary, secondary = (title_ko or title_en), (title_en if title_ko else "")
    return {
        # id 는 화면에 쓰지 않는다 — 변경이력(library_updates.json)이 id 만 저장하고
        # 제목·링크는 렌더 시점에 이 뷰에서 join 하므로 조인 키로만 싣는다.
        "id": it.get("id") or "",
        "title": primary,
        "sub": secondary,
        "code": it.get("code") or "",
        "doc_type": it.get("doc_type") or "",
        "published_date": it.get("published_date") or "",
        "official_url": _safe_url(it.get("official_url") or ""),
        "ko_url": _safe_url(it.get("ko_url") or ""),
        "pdf_url": _safe_url(it.get("pdf_url") or ""),
    }


def _catalog_view(entry: dict[str, Any], raw: dict[str, Any],
                  tr: Translator = _KO) -> dict[str, Any]:
    """카탈로그 raw(v2 평면 items[]) → 공통 템플릿 뷰모델(결정론 — 데이터 파생, 창작 0).

    [다국어 2단계] registry 의 화면 카피(title·blurb·intro·desc·unit·short·link_label·
    그룹 라벨·doc_type 라벨)는 여기서 `tr` 을 거쳐 뷰에 실린다 — 템플릿은 뷰 값을 그대로
    찍으므로 언어를 모른다. 데이터 값(항목 제목·URL)은 무변형.

    - sort="published_desc": 발행일 내림차순 뷰 정렬(값 무수정 — 표시 순서만). 무날짜
      항목은 뒤로, 동일 날짜는 데이터 순 유지(안정 정렬).
    - groups_by_url: official_url 부분일치로 계열 그룹핑(ICH Q/M — 결정론 파생). 그룹
      공식 링크 = 그룹 내 공유 URL. 매칭 실패 항목은 무라벨 그룹으로 뒤에 둔다.
    - Tier/QA·수집일 등 내부 운영 필드는 뷰에 올리지 않는다(사용자 노출 금지)."""
    items = [_library_item_view(it, tr.lang) for it in raw.get("items", [])]
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
                "label": tr(spec.get("label", "")),
                "label_en": spec.get("label_en", ""),
                "blurb": tr(spec.get("blurb", "")),
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
                "label": tr(spec.get("label", "")),
                "label_en": spec.get("label_en", ""),
                "blurb": tr(spec.get("blurb", "")),
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
        mapped = labels.get(it["doc_type"])
        it["doc_type"] = tr(mapped) if mapped is not None else it["doc_type"]
    dates = [it["published_date"] for it in items if it["published_date"]]
    meta = raw.get("meta", {})
    return {
        "slug": entry["slug"], "unit": tr(entry["unit"]), "kick": entry["kick"],
        # source = 카탈로그 파일 stem — 수집기 LIBRARY_SOURCE·변경이력 키와 같은 값.
        "source": entry["file"].rsplit(".", 1)[0], "short": tr(entry.get("short", "")),
        "items_by_id": {it["id"]: it for it in items},
        "intro": tr(entry["intro"]), "blurb": tr(entry["blurb"]), "desc": tr(entry["desc"]),
        "title": tr(entry["title"]) if entry.get("title") else meta.get("title", ""),
        "note": meta.get("note", ""),
        "public_base": _safe_url(entry.get("public_base") or meta.get("public_base", "")),
        "link_label": tr(entry.get("link_label", "")),
        "count": len(items),
        # [다국어 3단계] 공식 영문 제목이 없는 항목 수 — 영어판이 "왜 한국어 제목이 보이는지"
        # 를 밝히는 데만 쓴다(실측: 식약처 99건 중 40건, 나머지 카탈로그는 0).
        "ko_only_titles": sum(1 for it in raw.get("items", [])
                              if not (it.get("title_en") or it.get("title") or "").strip()),
        "latest_published": max(dates) if dates else "",
        "grouped": bool(entry.get("groups_by_url") or entry.get("groups_by_doc_type")),
        "groups": groups,
    }


def load_library(library_dir: Path = LIBRARY_DIR,
                 tr: Translator = _KO) -> list[dict[str, Any]]:
    """[자료실] registry 순서대로 커밋 데이터를 로드해 공통 뷰 리스트로 반환 — 결정론
    (파일 byte 파생, 네트워크 0). 파일 부재 카탈로그는 조용히 건너뛴다(허브는 존재분만)."""
    views = []
    for entry in LIBRARY_REGISTRY:
        p = library_dir / entry["file"]
        if p.is_file():
            views.append(_catalog_view(entry, json.loads(p.read_text(encoding="utf-8")), tr))
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
        # state 는 데이터 값이자 화면 라벨이다(템플릿이 `_(it.state)` 로 찍는다) — N_ 로
        # 키를 등록만 하고 값은 그대로 둔다(개수 집계·CSS 분기가 이 값을 비교한다).
        for state, key in ((N_("신규"), "new_ids"), (N_("변경"), "changed_ids")):
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


# ── [브리프 자료실 스트립 2026-08-31] 주간 창(window) 안 변경만 모아 보여준다 ──────
# 배경: 위 load_library_updates() 는 "가장 최근 이력 1건"만 본다(자료실 허브·모아보기
# 전용 — 둘 다 "지금 화면"이라 최신 스냅샷이 맞다). 브리프 상세는 그 주에 발행된
# **과거 특정 주간**을 보여주는 페이지라 "지금 최신 이력"을 붙이면 주차와 이력 날짜가
# 어긋난다(예: 이번 주 브리프에 지난달 이력이 붙는 사고). 그래서 브리프는 자기 창
# (window)에 실제로 들어오는 이력만 모아야 한다 — 여러 주 전 이력을 뒤늦게 열람해도
# 그 브리프가 커버하던 기간의 자료실 변경만 보이게.
def build_library_update_window_view(
    entries: list[dict[str, Any]],
    catalogs: list[dict[str, Any]],
    window_start_iso: str,
    window_end_iso: str,
    *,
    cap: int = LIBRARY_UPDATE_ITEM_CAP_COMPACT,
) -> dict[str, Any] | None:
    """브리프 한 주 창(window_start_iso~window_end_iso, ISO 문자열 비교·양끝 포함) 안에
    든 자료실 변경 이력만 모아 표시 뷰로 반환한다. 창 안에 이력이 0건이면 None — 브리프
    독자가 "자료실 갱신 없음"을 빈 상자로 오인하지 않도록 아예 렌더하지 않는다(빈 상자 금지).

    entries 는 load_library_update_entries() 결과(날짜 내림차순 정렬 전제) 그대로 받는다
    — 이 함수 자신은 파일을 읽지 않는다(순수·결정론).

    창에 여러 이력이 걸리면(격주 이상 지난 브리프를 뒤늦게 열람하는 경우 등) 소스별로
    new_ids/changed_ids/removed_ids 를 등장 순서를 지키며 하나로 합치고(중복 제거),
    total_count 는 그 소스를 언급한 이력 중 가장 최근 값을 쓴다(entries 가 최신순이라
    먼저 만난 값을 그대로 두면 된다). 합성 date 는 창 안 최신 이력의 date. 합쳐진 뒤의
    실제 렌더(제목 해석·라벨·round-robin 배분·hidden_count)는 기존 `_library_update_view`
    에 그대로 맡긴다 — 그 로직을 여기서 다시 구현하지 않는다(단일 출처 유지)."""
    matched = [e for e in entries
               if window_start_iso <= str(e.get("date") or "") <= window_end_iso]
    if not matched:
        return None

    merged_sources: dict[str, dict[str, Any]] = {}
    for e in matched:                                  # matched 도 최신 우선(entries 순서 상속)
        for src, detail in (e.get("sources") or {}).items():
            if not isinstance(detail, dict):
                continue
            slot = merged_sources.setdefault(src, {
                "new_ids": [], "changed_ids": [], "removed_ids": [],
                "total_count": detail.get("total_count", 0),   # 이 소스를 처음 만난(=가장 최신) 값 고정
            })
            for key in ("new_ids", "changed_ids", "removed_ids"):
                seen = set(slot[key])
                for item_id in (detail.get(key) or []):
                    if item_id not in seen:
                        slot[key].append(item_id)
                        seen.add(item_id)

    merged_entry = {"date": matched[0].get("date", ""), "sources": merged_sources}
    return _library_update_view(merged_entry, catalogs, cap=cap)


def _parse_brief_window(window: str) -> tuple[str, str] | None:
    """브리프 표시용 window 문자열("2026-08-24 ~ 2026-08-31")에서 ISO 날짜 두 개를 뽑아
    (start, end) 로 반환한다. 정확히 두 개를 못 뽑으면(형식이 깨졌거나 비어 있으면) None
    — 호출부는 조용히 스트립을 생략한다(깨진 표시보다 안 보이는 게 낫다)."""
    found = re.findall(r"\d{4}-\d{2}-\d{2}", window or "")
    if len(found) != 2:
        return None
    return found[0], found[1]


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


_REG_REF_CFR_SECTION_RE = re.compile(r"^21 CFR (\d{3}\.\d+[a-z]?)$")


def _reg_ref_cases_href(label: str, clause_slugs: "set[str] | None" = None) -> str:
    """[용어사전 조항 착지] `21 CFR 211.192` → 그 조항의 지적사례 화면 상대경로.

    [2026-09-03 2차] 착지를 **조항 정적 페이지**(`findings/clause/211-192/`)로 옮긴다.
    1차(#883)는 체크리스트에 `?section=` 을 붙여 보냈는데 그건 런타임 RPC 라 크롤러에게는
    빈 화면이고, 조항 원문 제목·관련 용어도 없다. 정적 페이지가 있으면 그쪽이 낫다.
    `clause_slugs` 는 **실제로 만들어진 페이지 집합**이다 — 없는 페이지로 보내는 링크는
    무링크보다 나쁘므로, 미지정이거나 그 조항 페이지가 없으면 "" 를 낸다(사례 3건 미만
    조항은 페이지를 만들지 않는다).

    종전에는 관련 조항이 **전부 사이트 밖으로만** 나갔다(503 건 중 469 건이 링크되는데
    도착지는 eCFR·ICH·EU 공식문서 뿐이고 내부 링크는 0). 국문 사용자가 조항을 눌러
    닿는 곳이 영문 법령이라, "이 조항으로 실제 어떤 지적이 나왔나"를 볼 길이 없었다.

    범위 계약: 단일 조항 번호 형태만(`21 CFR 211.192`). 구간 표기(`211.160–211.194`)나
    Part 표기(`21 CFR Part 211`)는 대상 조항이 하나로 정해지지 않으므로 "" — 무링크가
    틀린 링크보다 안전하다(_reg_ref_url 과 같은 규율). 순수 함수(창작 0)."""
    m = _REG_REF_CFR_SECTION_RE.match((label or "").strip())
    if not m:
        return ""
    slug = clause_slug(m.group(1))
    if clause_slugs is not None and slug not in clause_slugs:
        return ""
    return f"findings/clause/{slug}/"


def _reg_ref_view(item: Any, catalogs: dict[str, list[dict[str, Any]]] | None = None,
                  clause_slugs: "set[str] | None" = None) -> dict[str, str] | None:
    """[용어사전 심화] reg_refs 항목 1건 → {"label","url","cases_href"} 정규화.

    문자열이면 label=문자열, url 은 _reg_ref_url 해석기로 채운다(B2). dict 면 label/url
    을 각각 strip/_safe_url 게이트만 거쳐 통과하되, **데이터의 url 이 비어 있을 때만**
    해석기로 보강한다(데이터가 코드를 이긴다 — 이미 명시된 url 은 그대로 우선). catalogs
    미지정(None) 은 빈 카탈로그 취급(무매치 → ""·기존 호출부 호환). label 이 빈 항목
    (빈 문자열·공백뿐)은 조용히 제외(None) — 호출부가 필터."""
    cat = catalogs or {}
    if isinstance(item, str):
        label = item.strip()
        if not label:
            return None
        return {"label": label, "url": _reg_ref_url(label, cat),
                "cases_href": _reg_ref_cases_href(label, clause_slugs)}
    if isinstance(item, dict):
        label = (item.get("label") or "").strip()
        if not label:
            return None
        url = _safe_url(item.get("url") or "")
        if not url:
            url = _reg_ref_url(label, cat)
        return {"label": label, "url": url,
                "cases_href": _reg_ref_cases_href(label, clause_slugs)}
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


def _glossary_case_count(case: "dict[str, Any] | None") -> int:
    """glossary_cases 항목 → 사례 건수(정수). 없거나 깨졌거나 음수면 0 = 표시 생략.

    용어 자신의 사례 수와 `related` 칩에 붙는 수가 **같은 규칙**을 쓰게 하려고 함수로
    뽑았다 — 두 곳이 각자 파싱하면 한쪽만 고쳐질 때 같은 용어가 화면 두 곳에서 다른
    숫자를 갖는다.
    """
    try:
        n = int((case or {}).get("findings") or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def build_glossary_view(
    terms: list[dict[str, Any]],
    reg_ref_catalogs: dict[str, list[dict[str, Any]]] | None = None,
    cases: dict[str, dict[str, Any]] | None = None,
    clause_slugs: "set[str] | None" = None,
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
        # [C2] 관련 용어 칩에 그 용어의 사례 건수를 병기한다. ★순서는 손대지 않는다 —
        # related 는 사람이 고른 목록이고 그 순서가 정본이다(사례 있는 것을 위로 올리면
        # 큐레이션을 코드가 덮어쓴다). 사례가 있다는 **사실만** 덧붙여, 정의만 있는
        # 용어에 착지한 방문자가 실제 지적사례가 있는 쪽으로 갈 수 있게 한다.
        def _related_view(rid: str) -> "dict[str, Any]":
            n = _glossary_case_count(cases.get(rid))
            return {"id": rid, "term_ko": label_by_id[rid],
                    "case_count_label": f"{n:,}" if n else ""}

        related = [_related_view(r) for r in (t.get("related") or [])
                   if r in label_by_id]
        reg_refs = [v for v in (_reg_ref_view(r, reg_ref_catalogs, clause_slugs)
                                for r in (t.get("reg_refs") or [])) if v]
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
        case_findings = _glossary_case_count(case)
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


def glossary_term_description(term: dict[str, Any], tr: Translator = _KO) -> str:
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
    suffix = (" " + tr("실제 지적사례 {n}건과 공식 출처를 함께 정리했습니다.",
                       n=f"{case_findings:,}")
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


def glossary_term_page_title(term: dict[str, Any], tr: Translator = _KO) -> str:
    """`{한글}({짧은 영문}) 뜻 · GRM 용어사전` — 검색어 형태("OOS 뜻")를 앞쪽에 둔다."""
    term_ko = term.get("term_ko") or ""
    short_en = glossary_title_en(term_ko, term.get("term_en") or "")
    head = f"{term_ko}({short_en})" if short_en else term_ko
    return tr("{head} 뜻 · GRM 용어사전", head=head)


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
        "title": N_("분류별 지적사항"),
        "query_key": "cat",
        "headline_suffix": N_("지적사항"),
        "lede_prefix": N_("이 분류로"),
        "sibling_title": N_("다른 분류 보기"),
        "index_lede": N_("규제기관이 지적한 내용을 주제별로 묶었습니다. 무균공정·시험실 관리·일탈 조사처럼 실사에서 반복되는 축이라, 우리 현장의 취약 지점과 대조해 보실 수 있습니다."),
    },
    "country": {
        "path": "country",
        "kick": "By Country",
        "title": N_("국가별 지적사항"),
        "query_key": "country",
        "headline_suffix": N_("제조소 지적사항"),
        "lede_prefix": N_("이 나라에 있는 제조소에서"),
        "sibling_title": N_("다른 국가 보기"),
        "index_lede": N_("지적을 받은 제조소가 어느 나라에 있는지로 묶었습니다. 위탁 제조·원료 공급을 맡긴 지역의 규제 동향을 확인하실 때 쓰실 수 있습니다."),
    },
    "agency": {
        "path": "agency",
        "kick": "By Agency",
        "title": N_("규제기관별 지적사항"),
        "query_key": "agency",
        "headline_suffix": N_("지적사항"),
        "lede_prefix": N_("이 기관이"),
        "sibling_title": N_("다른 기관 보기"),
        "index_lede": N_("어느 규제기관이 공개한 지적인지로 묶었습니다. 기관마다 문서 형식과 지적의 결이 달라, 대응 준비도 기관 단위로 갈립니다."),
    },
}


_FACET_COPY_KEYS = ("title", "headline_suffix", "lede_prefix", "sibling_title", "index_lede")


def facet_meta(axis_key: str, tr: Translator = _KO) -> dict[str, str]:
    """[다국어 2단계] FACET_AXES 항목의 **번역된 사본** — 경로·쿼리 키는 그대로, 화면 카피
    5종만 `tr` 을 거친다. 템플릿은 이 사본(`axis`)을 그대로 찍으므로 언어를 모른다.
    모르는 축은 KeyError(조용한 누락 금지 — 종전 `FACET_AXES[axis_key]` 와 동일)."""
    meta = FACET_AXES[axis_key]
    return {**meta, **{k: tr(meta[k]) for k in _FACET_COPY_KEYS}}


def load_findings_facets(path: Path = FINDINGS_FACETS_FILE) -> "dict[str, Any] | None":
    """모음 페이지 정본 로드. 파일 부재 시 None → 섹션이 조용히 꺼진다(load_glossary 동형).

    스키마 버전이 다르면 **조용히 넘어가지 않고 실패**한다 — 모양이 바뀐 데이터를 옛
    템플릿으로 렌더하면 빈 페이지 36장이 라이브로 나가고, 그건 없느니만 못하다.
    """
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    got = obj.get("schema_version")
    # v2 = 분류×기관 조합(`combos`) 추가. v1 을 계속 받으면 조합 페이지가 **조용히 0장**
    # 이 되므로 받지 않는다 — 이 로더가 실패를 말하지 않으면 아무도 안 만들어진 걸 모른다.
    if got != "grm-findings-facets/v2":
        raise SystemExit(f"findings_facets 스키마 불일치: {got!r} (기대: grm-findings-facets/v2)")
    return obj


# ── [조항 페이지] 21 CFR 조항별 지적사례 — /findings/clause/{slug}/ ──────────────
# 검색 실측(2026-09-03, 13쿼리): `21 CFR 211.192` 류 쿼리는 결과가 **전부 영문 법령
# 사이트**로 국문 해설이 사실상 공백이다. 우리는 그 조항으로 지적받은 사례를 국문으로
# 갖고 있는데 검색엔진에 보이는 형태로는 없었다(#883 의 용어사전 착지는 런타임 RPC 라
# 크롤러에게 빈 화면이다). 이 페이지가 그 자리를 정적으로 채운다.
#
# 범위 계약 — 넓히지 않는다:
#   · **GMP 조항만**(자료실 cfr.json 카탈로그 교집합 = Part 210/211). 경고서한은 표시
#     (201.x)·등록(207.x)·FD&C Act(section 503 등)도 인용하는데, 그건 이 사이트의 주제가
#     아니고 국문 맥락(카탈로그 제목·용어사전)도 없어 얇은 페이지가 된다.
#   · **문서 3건 이상**(문서 페이지 임계와 같은 값). 사례 1~2건짜리 페이지는 사용자에게도
#     검색엔진에게도 빈손이다.
#   실측(2026-09-03): 원시 표기 497 → 정규화 96 섹션 → 위 두 게이트 통과 34개.
_CFR_SECTION_RE = re.compile(r"^21 CFR (\d{3}\.\d+)")
CLAUSE_MIN_DOCUMENTS = 3
CLAUSE_MAX_SAMPLES = 6


def load_cfr_catalog(library_dir: Path = LIBRARY_DIR) -> list[dict[str, Any]]:
    """자료실 21 CFR 카탈로그(조항 제목·공식 원문 링크) 원본 items.

    ★`_load_reg_ref_catalogs()` 를 쓰지 않는다 — 그쪽은 ich/eu_gmp/pics/who/mfds 만 싣고
    **cfr 은 넣지 않는다**(21 CFR 은 정규식만으로 eCFR URL 을 조립할 수 있어 카탈로그가
    필요 없었다). 모르고 `.get("cfr")` 를 쓰면 조용히 빈 리스트가 되고 조항 페이지가
    0 장이 된다 — 실제로 그렇게 한 번 걸렸다. 파일 부재는 빈 리스트(무페이지)."""
    p = library_dir / "cfr.json"
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("items") or []


def _cfr_section_of(label: Any) -> str:
    """`21 CFR 211.100(a)` → `211.100`. 조항이 아니면 "".

    하위항(a)(1) 은 섹션으로 접는다 — eCFR 도 섹션 단위로만 앵커를 주고(`_reg_ref_url`
    R1 과 같은 규칙), 사람도 "211.100"으로 검색한다. 접지 않으면 같은 조항이 페이지
    대여섯 장으로 쪼개져 전부 얇아진다."""
    m = _CFR_SECTION_RE.match(str(label or "").strip())
    return m.group(1) if m else ""


def clause_slug(section: str) -> str:
    """`211.192` → `211-192`(URL 조각). 점은 확장자로 오독될 수 있어 하이픈으로."""
    return section.replace(".", "-")


def build_clause_views(docs_data: "dict[str, Any] | None",
                       cfr_items: "list[dict[str, Any]] | None",
                       glossary_terms: "list[dict[str, Any]] | None" = None,
                       *, min_documents: int = CLAUSE_MIN_DOCUMENTS,
                       max_samples: int = CLAUSE_MAX_SAMPLES) -> list[dict[str, Any]]:
    """조항별 페이지 뷰 목록. 입력은 전부 커밋된 정본 — 네트워크·난수·now() 0.

    본문은 자르지 않는다(길이 조절은 CSS 의 일이다 — findings_facet 과 같은 규율).
    정렬은 공개일 내림차순 → 문서 id → 지적 id 로 완전 결정론이다(같은 입력이면 같은
    바이트). 실사관 이름은 싣지 않는다(037 제약: 실명 개인 집계는 목적 밖)."""
    docs = list((docs_data or {}).get("documents") or [])
    catalog = {}
    for it in (cfr_items or []):
        sec = _cfr_section_of(it.get("code"))
        if sec and sec not in catalog:
            catalog[sec] = it
    if not docs or not catalog:
        return []

    by_section: dict[str, list[dict[str, Any]]] = {}
    doc_ids: dict[str, set] = {}
    for doc in docs:
        for f in doc.get("findings") or []:
            text = (f.get("text_ko") or "").strip()
            if not text:
                continue
            for sec in {_cfr_section_of(r) for r in (f.get("cfr_refs") or [])}:
                if not sec or sec not in catalog:
                    continue
                by_section.setdefault(sec, []).append({
                    "firm_name": doc.get("firm_name") or "",
                    "agency": doc.get("agency") or "",
                    "published_date": doc.get("published_date") or "",
                    "doc_slug": doc.get("slug") or "",
                    "evidence_url": doc.get("evidence_url") or "",
                    "finding_id": f.get("finding_id") or "",
                    "document_id": doc.get("document_id") or "",
                    "text_ko": text,
                })
                doc_ids.setdefault(sec, set()).add(doc.get("document_id") or "")

    # 조항 → 이 조항을 인용하는 용어사전 표제어(국문 맥락 · 상호 진입 간선).
    terms_by_section: dict[str, list[dict[str, str]]] = {}
    for t in (glossary_terms or []):
        tid, tko = t.get("id") or "", t.get("term_ko") or ""
        if not tid or not tko:
            continue
        for sec in sorted({_cfr_section_of(r) for r in (t.get("reg_refs") or [])} - {""}):
            terms_by_section.setdefault(sec, []).append({"id": tid, "term_ko": tko})

    views: list[dict[str, Any]] = []
    for sec in sorted(by_section):
        rows = by_section[sec]
        n_docs = len(doc_ids.get(sec) or ())
        if n_docs < min_documents:
            continue
        rows.sort(key=lambda r: (r["published_date"], r["document_id"], r["finding_id"]),
                  reverse=True)
        item = catalog[sec]
        views.append({
            "section": sec,
            "code": f"21 CFR {sec}",
            "slug": clause_slug(sec),
            # 카탈로그 제목은 원문 그대로(영문) — 번역본을 지어내지 않는다.
            "title_en": (item.get("title_en") or "").strip(),
            "official_url": _safe_url(item.get("official_url") or ""),
            "documents": n_docs,
            "findings": len(rows),
            "samples": rows[:max_samples],
            "terms": terms_by_section.get(sec) or [],
        })
    return views


def clause_description(clause: dict[str, Any], tr: Translator = _KO) -> str:
    """meta description — 검색 결과에 그대로 나가는 문장. 숫자는 뷰에서 파생(사본 금지)."""
    head = tr("{code} 지적사례 {n}건", code=clause["code"], n=f"{clause['findings']:,}")
    title = clause.get("title_en") or ""
    tail = " " + tr("공개 문서 {n}건에서 뽑아 우리말로 정리했습니다."
                    " 조항 원문 링크와 관련 용어를 함께 봅니다.",
                    n=f"{clause['documents']:,}")
    return f"{head}({title}){tail}" if title else f"{head}.{tail}"


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


_FIRM_SLUG_KEEP = re.compile(r"[^a-z0-9]+")


def _firm_slug(firm_key: str) -> "str | None":
    """업체 정규화 키 → URL 안전 슬러그. 같은 키는 언제나 같은 슬러그(재실행 안정).

    `findings_docs_refresh._safe_slug` 와 같은 계약이다 — 읽을 수 있는 몸통을 남기되
    원본의 sha1 앞 8자를 접미해 충돌을 막는다. 접미가 **항상** 붙는 것이 문서 슬러그와
    다른 점인데, 업체 키는 한글·괄호가 흔해 몸통만으로는 서로 다른 업체가 같은 슬러그로
    뭉갤 수 있기 때문이다("(주)한국콜마" 와 "한국콜마" 는 다른 키다). 몸통이 통째로
    비는 한글 상호는 접미만으로 식별된다 — 읽기 좋진 않지만 **틀리지 않는다**.
    """
    key = (firm_key or "").strip().lower()
    if not key:
        return None
    body = _FIRM_SLUG_KEEP.sub("-", key).strip("-")[:60].rstrip("-")
    tail = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"{body}-{tail}" if body else f"firm-{tail}"


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


def serp_width(text: str) -> int:
    """검색 결과 표시폭 근사 — 한글·CJK 는 라틴의 약 두 배 자리를 차지한다.

    구글은 제목을 글자 수가 아니라 **픽셀 폭**으로 자른다. 글자 수로 재면 한글이 섞인
    제목이 실제보다 짧아 보여 잘림을 놓친다. 라틴 1·CJK 2 로 세고 데스크톱 절단선을
    라틴 60자 상당(= 60폭)으로 본다.
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def title_firm_name(name: str) -> str:
    """`<title>` 에 쓰는 업체명 — 화면·본문의 업체명은 **무변형**이고 여기만 손본다.

    [B2 2026-08-27] 처음에는 폭 상한을 걸어 기계 절단하려 했는데 **실측이 그 가설을
    반증했다**: 업체명 폭은 중앙값 23 으로 애초에 병목이 아니었다. 이름을 뭉개면 사람이
    검색창에 치는 바로 그 말이 잘리므로 폭 절단은 **하지 않는다**.

    남기는 것은 의미가 보존되는 트림 하나 — `X dba Y`(상호 별칭·실측 227건) → 법인명만.
    규제 기록에 실리는 이름이 법인명이고, 덤으로 같은 법인의 표기 변종 26군이 한 이름으로
    모인다(예: `Right Value Drug Stores, LLC` 에 별칭 표기 3종이 붙어 있었다).
    `D/B/A`·`O/A` 는 같은 뜻의 다른 표기라 함께 본다(실측 8건).

    ★`A / B` 이중언어 병기(캐나다 문서)도 후보였는데 **뺐다**. 전수 9건 중 1건이
    `Brookfield Medical / Surgical Supply, Inc.` — 병기가 아니라 이름 자체에 사선이 든
    경우라, 앞쪽만 취하면 실재하지 않는 회사명이 된다. 쌍둥이 감소분은 4장뿐이라
    (340 → 336) 회사명을 틀릴 값이 없다.

    같은 이름은 언제나 같은 결과다(재실행 안정). 줄인 결과가 서로 겹치면 호출부의
    유일성 로직(분류 → 문서번호)이 그대로 받아 해소한다.
    """
    s = re.split(r"\s+(?:dba|d/b/a|o/a)\s+", name or "",
                 flags=re.IGNORECASE)[0].strip().rstrip(",")
    return s or (name or "")


# 제목 전용 문서종류 라벨. **화면(h1)은 원문 라벨 그대로**이고 여기만 짧게 쓴다.
# 근거는 실측이다 — 문서 제목의 82%가 SERP 폭을 넘었는데 그 원인이 업체명이 아니라
# `Health Canada Inspection`(24폭·문서의 40%)·`EU GMP NCR (EudraGMDP)`(22폭)이었다.
# ★새 어휘를 지어내지 않는다 — 사이트가 이미 쓰는 말로만 바꾼다(`trends.js` 순위 각주와
# `/findings/coverage/` 가 "캐나다 실사", `card_scaffold` 카드 라벨이 "EU GMP 비준수").
# ★FDA 계열은 **줄이지 않는다**: "483"·"Warning Letter" 는 사람들이 검색창에 그대로
# 치는 말이라, 짧게 만드는 이득보다 그 말이 사라지는 손해가 크다(실측 — 이 둘까지 줄여
# 뒤로 미는 배치는 쌍둥이를 336→197 로 더 줄이지만 "Warning Letter" 노출을 346→126장으로
# 잃었다. 쌍둥이 139장 값으로 220장의 검색어를 파는 거래라 기각).
#
# ★저장된 `source` 문자열은 여전히 불가침이다(`raw_signal_id`/`finding_id` 해시 입력).
# 여기서 바꾸는 건 표시용 사본뿐이고 `doc_source_label` 은 손대지 않는다.
TITLE_SOURCE_SHORT = {
    "Health Canada Inspection": N_("캐나다 실사"),
    "EU GMP NCR (EudraGMDP)": N_("EU GMP 비준수"),
}


def title_source_label(doc: dict[str, Any], tr: Translator = _KO) -> str:
    """`<title>` 에 쓰는 문서종류 — 표에 있으면 짧은 말로, 없으면 화면과 같은 값."""
    short = TITLE_SOURCE_SHORT.get(doc_source_label(doc))
    return tr(short) if short else doc_source_label(doc)


def build_doc_page_titles(documents: list[dict[str, Any]],
                          tr: Translator = _KO) -> dict[str, str]:
    """문서 페이지 `<title>` — 슬러그별로 **유일**하게 만든다.

    ★기본형 "{업체} {문서종류} 지적사항 ({발행일})" 은 문서를 유일하게 식별하지 못한다.
    같은 업체·같은 기관·같은 공개일로 나뉜 실사 보고서가 실재하기 때문이다(실측 362장이
    141개 군집으로 겹쳤고 최대 군집은 8장). 제목은 검색 결과의 1차 식별자라, 겹치면
    구글이 하나만 고르고 나머지를 중복으로 떨어뜨린다.

    그래서 겹칠 때만 단계적으로 넓힌다 — ①분류(사람이 읽어 뜻이 있는 구분) → ②문서번호
    (마지막 수단, 반드시 유일). 겹치지 않는 문서의 제목은 건드리지 않는다.

    ★★[B2 2026-08-27] **문자열이 유일한 것과 화면에서 구별되는 것은 다르다.** 위 확장은
    전체 문자열이 겹칠 때만 돌고, 넓히는 방식이 꼬리에 덧붙이기다. 그런데 구글이 보여주는
    건 앞 60폭뿐이라, 문자열은 유일해졌는데 **보이는 글자는 그대로 같았다** — 수리가 되레
    변별 요소를 절단선 밖으로 밀고 있었다. 실측(전수 3,301장): 절단선까지 보이는 제목이
    다른 URL 과 똑같은 문서가 **853장**이었고, 최대 군집은 `Air Liquide Canada Inc.` 30장이
    「… Health Canada Inspection 지적사항 (2」에서 잘려 **날짜가 통째로 안 보였다**.

    그래서 날짜와 "지적사항"의 자리를 맞바꾼다 — 변별 요소를 8폭 앞으로 당기는 최소 변경.
    문서종류는 자리를 지킨다(위 TITLE_SOURCE_SHORT 주석의 거래 참조).
    덤으로 한국어 어순이 오히려 자연스러워진다 — 수식어가 머리명사 앞에 오므로
    "(2021-01-13) 지적사항"이 "지적사항 (2021-01-13)"보다 제 자리다.
    전수 재측정: 쌍둥이 853 → 336 · 날짜가 온전히 보이는 문서 1,333 → 2,852 ·
    문서종류가 온전히 보이는 문서 2,938 → 3,168. 세 축 모두 개선이고 퇴행이 없다.

    잔여 336장은 제목으로는 더 못 가른다 — 업체명 자체가 60폭을 먹거나(`Isologic
    Innovative Radiopharmaceuticals of Ontario Limited` 59폭) 같은 업체·같은 날 문서가
    여럿인 경우다. 후자의 상당수는 FDA FOIA 일괄 공개분이라 날짜가 변별력을 못 낸다
    (문서 619장이 `2024-01-17` 하나를 공유 — 실사는 2015~2019년).
    """
    def base(d: dict[str, Any]) -> str:
        src = title_source_label(d, tr)
        firm = title_firm_name(d["firm_name"])
        head = f"{firm} {src}".strip() if src else firm
        return tr("{head} ({date}) 지적사항", head=head, date=doc_display_date(d))

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
            out[d["slug"]] = tr("{title} · 문서 {no}", title=widened,
                                no=d["document_id"].rsplit("-", 1)[-1])
    return out


def doc_display_date(doc: dict[str, Any]) -> str:
    """제목·목록에 쓰는 날짜 — **문서가 다루는 날**을 우선한다.

    ★[실사일 2026-08-27] 여태 쓰던 `published_date` 는 우리가 그 문서를 확보한 날이지
    규제기관이 실사한 날이 아니다. 대개는 며칠 차이라 문제가 없는데 FDA 483 에서는
    무너진다 — FOIA 일괄 공개분 941건이 공개일 `2024-01-17` 하나를 공유하고 그 실사는
    **2015~2019년**이다(전수 평균 격차 1,524일 · 최대 6,143일). 2015년 지적이 2024년
    것으로 읽히는 건 실명 업체 페이지에서 사실 왜곡이다.

    그래서 실사일이 있으면 그걸 쓴다. 없으면(캐나다 실사·경고서한 등) 종전대로 공개일.
    ★어느 쪽인지는 **설명문이 밝힌다**(`doc_page_description`) — 제목에 "실사" 라벨을
    붙이는 안도 쟀는데 폭 5 를 먹어 제목 276장을 절단선 밖으로 밀었다. 검색 결과에서는
    제목과 설명이 함께 보이므로, 폭이 비싼 제목 대신 여유 있는 설명에서 밝히는 쪽이 낫다.
    """
    return (doc.get("inspection_date") or "").strip() or doc["published_date"]


def date_axis_verb(documents: "list[dict[str, Any]]", tr: Translator = _KO) -> str:
    """연도별 목록의 축이 '공개'인가 '실사'인가 — **데이터에서 파생한다.**

    `/findings/docs/{기관}/{연도}/` 는 `published_date` 의 연도로 묶는다. 대개 그건
    우리가 문서를 확보한 날이라 "공개한"이 맞는데, **캐나다 실사는 아니다** — 수집기가
    `inspectionStartDate` 를 `published_date` 에 넣기 때문에 그 축은 실사 연도다.
    거기에 "공개한"이라고 적으면 1,330장이 거짓을 말한다.

    기관 이름으로 분기하지 않는다(손목록은 새 소스에서 조용히 낡는다). 그 묶음의 문서가
    **전부** 두 날짜가 같으면 축은 실사일이다 — 값으로만 판정하므로 원천이 바뀌면 문구도
    저절로 따라온다. 비어 있으면 종전 표현을 쓴다(없는 것을 단정하지 않는다).
    """
    if not documents:
        return tr("공개한")
    same = all((d.get("inspection_date") or "") == (d.get("published_date") or "")
               for d in documents)
    return tr("실사한") if same else tr("공개한")


def doc_page_description(doc: dict[str, Any], agency_labels: dict[str, str],
                         tr: Translator = _KO) -> str:
    """meta description — 누가·언제·몇 건·어떤 주제. 데이터 조립뿐(문구 생성 0).

    ★날짜를 반드시 넣는다. 검색 결과 스니펫에 연도가 없으면 몇 년 전 지적이 현재 상태로
    읽힌다 — 실명 업체 페이지에서 그건 사실 왜곡이다.

    ★그리고 **어느 날짜인지 밝힌다.** 실사일이 있으면 그걸 앞세우고(문서가 다루는 날)
    공개일은 괄호로 함께 적는다 — 제목이 맨몸 날짜를 쓰기 때문에, 그 날짜의 정체를
    말해 주는 자리가 여기다. 실측 비용: 이 한 줄 때문에 문서 148장이 "주요 분류" 시작을
    절단선 밖으로 밀지만, 1,524장이 평균 4.2년 어긋난 날짜를 그만 보여준다.
    """
    label = agency_labels.get(doc["agency"])
    agency = tr(label) if label else doc["agency"]
    cats = " · ".join(tr(c) for c in (doc.get("categories") or []))
    tail = " " + tr("주요 분류: {cats}.", cats=cats) if cats else ""
    src = doc_source_label(doc)
    subject = f"{doc['firm_name']} {src}".strip() if src else doc["firm_name"]
    inspected = (doc.get("inspection_date") or "").strip()
    n = len(doc["findings"])
    if inspected and inspected != doc["published_date"]:
        return tr("{agency}가 {inspected} 실사에서 확인한 {subject} 지적사항 {n}건을 "
                  "우리말로 정리했습니다({published} 공개).",
                  agency=agency, inspected=inspected, subject=subject, n=n,
                  published=doc["published_date"]) + tail
    if inspected:
        # ★두 값이 같으면 **날짜가 하나뿐**이라는 뜻이다 — 캐나다 실사가 그렇다
        #   (수집기가 `inspectionStartDate` 를 published_date 에 넣는다). 한 날짜를
        #   두 번 적으면서 한쪽을 "공개"라고 부르면 그게 바로 고치려던 거짓말이다.
        #   소스 이름이 아니라 **값**으로 판정하므로 다른 소스에서 우연히 같아져도
        #   (실측 FDA 483 에 1건) 알아서 옳게 나온다.
        return tr("{agency}가 {inspected} 실사에서 확인한 {subject} 지적사항 {n}건을 "
                  "우리말로 정리했습니다.",
                  agency=agency, inspected=inspected, subject=subject, n=n) + tail
    return tr("{agency}가 {published}에 공개한 {subject} 지적사항 {n}건을 "
              "우리말로 정리했습니다.",
              agency=agency, published=doc["published_date"], subject=subject, n=n) + tail


_DOC_INSPECTOR_LIMIT = 3


def doc_inspector_line(doc: dict[str, Any], tr: Translator = _KO) -> str:
    """문서 상세 '실사관' 행 표시 문자열 — 최대 3명 평문 표기 + 초과분 "외 N명".

    [실사관 표기 · 정적 문서 페이지 2026-08-31] `_sanitize_inspector_names()` 로 먼저
    방어 정제한다(리스트가 아니면 무시·비문자열/공백 원소 제거·strip·6개 절단) —
    `findings_docs_refresh._collect_inspector_names()` 는 문서 단위로 순서보존 중복
    제거만 하고 값 자체는 정제하지 않으므로(그 함수 주석 참조), 정제는 표시 직전인
    여기 한 곳에서만 일어난다(FDA483 카드 경로 `_card_view()`와 동일 원칙 — 이중 정제
    금지). 6개 상한 안에서 다시 3개만 보이고 나머지는 "외 N명"으로 뭉친다 — 두 상한이
    겹치는 만큼 6명을 넘는 실사관은 정확한 초과 인원이 아니라 절삭된 하한값을 보일 수
    있으나, 그 상한은 이미 카드·검색 화면에서도 조용히 적용되는 기존 방어선이다.

    ★코호트(문서 5건 이상 서명한 실사관) 여부와 무관하게 **전원 평문**이다. 코호트는
    `findings_inspector_index` RPC 가 정하는 런타임 개념이라, 커밋된 JSON 만 보는 정적
    빌드가 이를 재현하면 코호트 정의가 RPC 판정과 정적 스냅샷 두 곳으로 갈라진다
    (드리프트) — 갈라진 채로 프로필 링크를 걸면 존재하지 않는 프로파일("없음" 페이지)로
    사용자를 보내는 경우가 생긴다. 그래서 정적 페이지는 **사실(누가 서명했다)만** 싣고,
    프로파일로 가는 링크는 코호트를 아는 동적 층(검색 카드 findings.js buildDocHead())이
    계속 담당한다 — 이 함수는 링크를 만들지 않는다.

    값이 없으면 빈 문자열 — 호출부(findings_doc.html)가 행 자체를 렌더하지 않는다
    (빈 라벨 금지).
    """
    names = _sanitize_inspector_names(doc.get("inspector_names"))
    if not names:
        return ""
    shown = names[:_DOC_INSPECTOR_LIMIT]
    extra = len(names) - len(shown)
    line = " · ".join(shown)
    if extra > 0:
        line = tr("{names} 외 {n}명", names=line, n=extra)
    return line


def combo_description(combo: dict[str, Any], tr: Translator = _KO) -> str:
    """분류 × 기관 조합 페이지의 meta description — 데이터에서 조립(문구 생성 0).

    단일 축(facet_description)은 "어느 기관들이" 를 뒤에 붙이지만, 조합은 기관이 이미
    확정돼 제목에 있다. 대신 **업체 수**를 넣는다 — 검색 결과에서 이 페이지가 목록형
    이라는 신호가 되고, 같은 분류의 다른 기관 페이지와 문장이 겹치지 않는다.

    ★조사(가/이)를 기관명에 붙이지 않는다 — 한국어 조사는 앞말의 받침으로 갈리는데
    기관명에는 영문 약어(FDA·EMA·MHRA)가 섞여 있어 규칙이 성립하지 않는다.
    """
    firms = len(combo.get("top_firms") or [])
    tail = (" " + tr("지적이 많았던 업체 {n}곳도 함께 보실 수 있습니다.", n=firms)
            if firms else "")
    return tr("{agency} 공개 문서에서 {category} 지적사항 {n}건(문서 {d}건)을 "
              "우리말로 정리했습니다.",
              agency=tr(combo["agency_label_ko"]), category=tr(combo["category_label_ko"]),
              n=f"{combo['findings']:,}", d=f"{combo['documents']:,}") + tail


def facet_description(axis_key: str, item: dict[str, Any],
                      agency_labels: dict[str, str], tr: Translator = _KO) -> str:
    """meta description — 데이터에서 조립한다(문구 생성 0·now()/난수 0).

    "무엇이 몇 건, 어느 기관에서" 를 앞세운다. 검색 결과에 그대로 노출되는 문장이라
    수식어보다 숫자와 기관명이 클릭을 만든다.
    """
    meta = facet_meta(axis_key, tr)
    names = [tr(agency_labels[a["v"]]) if a["v"] in agency_labels else a["v"]
             for a in (item.get("by_agency") or [])[:3]]
    who = "·".join(n for n in names if n)
    tail = " " + tr("{who} 공개 문서 기준.", who=who) if who else ""
    return tr("{label} {suffix} {n}건(문서 {d}건)을 우리말로 정리했습니다.",
              label=tr(item["label_ko"]), suffix=meta["headline_suffix"],
              n=f"{item['findings']:,}", d=f"{item['documents']:,}") + tail


# ── [주간 퀴즈] 문항 뱅크 로드·뷰모델(결정론 — 값 무변형, 파생은 근거 링크/라벨뿐) ────
# "이번 주" 문항 선택은 렌더러가 하지 않는다(now() 금지·결정론 불가침). 렌더러는 정본
# 뱅크 전 문항을 순서 그대로 페이지에 embed 하고, 클라이언트(assets/quiz.js)가 ISO 주차
# 키로 결정론 회전 선택한다(같은 주 = 전 직원 동일 세트). 사실/정답/해설은 무변형 통과.
_QUIZ_DIFFICULTY_LABEL = {"easy": N_("기본"), "normal": N_("심화")}
# source_type → 근거 진입 라벨(어디로 가는지). glossary=자체 딥링크, brief/finding=공개 URL.
_QUIZ_SOURCE_KIND = {"glossary": N_("용어사전"), "brief": N_("주간 브리프"),
                     "finding": N_("지적사항 검색")}
# 기본 노출 문항 수(운영설계 §2.3 — 주 4문항 기본, 운영자가 3~5 범위 조정). 이 상수만
# 바꾸면 클라이언트 회전 로직이 easy 과반·normal 1~2 구성을 자동으로 맞춘다(코드 수정 0).
WEEKLY_QUIZ_COUNT = 4


def load_quiz_bank(path: Path = QUIZ_FILE) -> list[dict[str, Any]] | None:
    """[주간 퀴즈] 정본 문항 뱅크 로드(파일 부재 시 None → 페이지 조용히 생략)."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _quiz_question_view(q: dict[str, Any], tr: Translator = _KO) -> dict[str, Any]:
    """문항 1건 → 렌더 뷰모델. 값(질문/선택지/정답/해설)은 무변형, 파생은 난이도 라벨과
    근거 링크 구성뿐. glossary 는 자체 용어사전 딥링크 id(무변형 통과 — 템플릿이 rel_root
    로 조립), brief/finding 은 공개 URL(_safe_url 스킴 게이트만). 순수·결정론."""
    st = q.get("source_type", "")
    ref = str(q.get("source_ref") or "")
    is_glossary = st == "glossary"
    difficulty = q.get("difficulty", "")
    difficulty_label = _QUIZ_DIFFICULTY_LABEL.get(difficulty)
    source_kind = _QUIZ_SOURCE_KIND.get(st)
    return {
        "id": q.get("id", ""),
        "question_ko": q.get("question_ko", ""),
        "choices": list(q.get("choices") or []),
        "answer_index": q.get("answer_index"),
        "explanation_ko": q.get("explanation_ko", ""),
        "difficulty": difficulty,
        "difficulty_label": tr(difficulty_label) if difficulty_label else difficulty,
        "source_type": st,
        "source_kind": tr(source_kind) if source_kind else st,
        # glossary → 용어사전 앵커 id(템플릿이 rel_root+glossary/#id 로 조립), 그 외는 "".
        "source_glossary_id": ref if is_glossary else "",
        # brief/finding → 공개 절대 URL(스킴 화이트리스트 통과분만; 비허용은 ""→링크 생략).
        "source_url": (_safe_url(ref) if not is_glossary else ""),
        # [9차 G3] week(YYYYWW) — 월 13:00 자동 생성 파이프라인이 붙이는 선택 필드. 있으면
        # data-week 로 embed(문자열 정규화만 — 값 무변형), 없으면 "" → 템플릿이 조용히 생략
        # (현 데이터 경로 = 기존 회전 그대로). 선택 로직은 클라이언트 quiz.js 소관.
        "week": str(q.get("week") or ""),
    }


def build_quiz_view(bank: list[dict[str, Any]], tr: Translator = _KO) -> dict[str, Any]:
    """문항 뱅크 → 렌더 뷰모델(무변형 — 값 재작성 0). 전 문항을 뱅크 순서 그대로 embed
    (클라이언트 결정론 회전용). 난이도 집계는 클라이언트 주차 회전이 easy 과반·normal 1~2
    구성을 맞추는 데 쓰는 파생 메타다."""
    questions = [_quiz_question_view(q, tr) for q in bank]
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
def _brief_context(brief: dict[str, Any], issue_no: int,
                   tr: Translator = _KO) -> dict[str, Any]:
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
        "title_dateform": title_dateform(bm.get("publish_date", ""), tr),
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


def _cover_context(brief: dict[str, Any], issue_no: int,
                   tr: Translator = _KO) -> dict[str, Any]:
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
        "title_dateform": title_dateform(pub, tr),  # 다크밴드 "{Y}년 {M}월 {N}주차"
        "window": bm.get("window", ""),
        "title": _brief_title(bm),
        "tldr": bm.get("tldr") or [],
    }


# ── 페이지 주소(단일 원천) ────────────────────────────────────────────────────
# [다국어 1단계 2026-09-03] 페이지 하나의 주소는 **사이트 루트 기준 상대 디렉터리 경로
# 하나**(`"findings/browse/"`)에서 전부 파생한다 — 출력 파일·템플릿의 rel_root·canonical·
# sitemap 경로·빵부스러기 절대 URL. 종전에는 이 넷을 렌더 호출마다 손으로 따로 적었다
# (rel_root 27곳 + 출력 경로 15곳 = 42곳). 깊이를 세는 규칙이 한 곳에 있어야 `/en/` 처럼
# 트리를 하나 더 얹을 때 42곳을 다시 세지 않는다
# (설계: docs/specs/GRM_다국어_영문판_설계_2026-09-03.md §3·§7).
#
# 언어 트리: 기본(한국어)은 접두 없음, 영어는 `en/` 접두. 같은 path 라도 lang 이 다르면
#   · site_path(출력 파일·canonical·sitemap)에는 접두가 붙고,
#   · rel_root(템플릿 내부 링크 `{{ rel_root }}findings/`)는 **그 언어 트리의 루트**를,
#   · asset_root(`assets/`·favicon 등 언어 무관 공유 자원)는 **사이트 루트**를 가리킨다.
# 한국어 트리에서는 셋이 같은 값이라 기존 산출물이 바이트 단위로 불변이다(리팩터 증명 =
# 전 파일 md5 동일). ★영어 트리를 실제로 렌더하는 것은 3단계 — 여기서는 규칙만 정한다.
# ── [다국어 3단계 2026-09-04] 영어 트리에 **실제로 내는** 페이지 ────────────────
# ★껍데기만 영어인 페이지는 만들지 않는다. 기준은 하나 — **그 페이지의 본문이 영어로
#   성립하는가**. 판정은 데이터 실측이다:
#     · `/findings/` 계열 조회 화면 = `findings_search` RPC 가 `finding_text`(원문)를
#       이미 돌려준다. 공개 지적 25,079건 중 22,974건(91.6%)이 원문 영어라, 표시 선호만
#       뒤집으면 진짜 영어 본문이 나온다(덧씌운 한국어를 걷어내는 일).
#     · 자료실 = 항목마다 `title_en` 을 갖고 있다.
#   반대로 **빼는 것**과 그 이유(수리 순서가 곧 설계 문서 §7 의 4·5단계다):
#     · 문서·모음·조항·업체 정적 페이지 = 본문이 `text_ko` 뿐이다(`findings_docs.json`·
#       `findings_facets.json` 에 영문 원문이 없다) → 4단계에서 데이터를 넣은 뒤.
#     · 용어사전 = `term_en` 은 100% 이나 풀이(`easy_ko`·`detail_ko`)가 한국어다.
#     · 이용안내·주간 퀴즈·주간 브리프/아카이브 = 한국어 산문 그 자체 → 5단계.
#     · 마이페이지·Admin = 개인화·운영자 화면(언어 트리와 무관).
# 없는 페이지로 보내는 링크는 무링크보다 나쁘다는 저장소 규율 그대로, nav·푸터·언어
# 전환·hreflang·sitemap 이 **전부 이 집합 하나**를 본다(사본 0).
EN_TREE_STATIC: tuple[str, ...] = (
    "",                        # 영어 홈(landing_en.html — 주간 브리프 히어로가 없는 별도 면)
    "findings/",               # 지적사항 검색(원문 우선) — 영어판의 본체
    "findings/trends/",
    "findings/inspections/",
    "findings/coverage/",
    "findings/checklist/",
    "findings/firm/",
    "findings/inspector/",
    "library/",
)


# ★sitemap 에서 빼는 경로 — 한국어 트리와 **같은 정책**이어야 한다. 실사관 프로파일은
#   실명이 적시된 개인 집계라 베이스 경로조차 등록하지 않는다(noindex 는 템플릿이 건다).
#   언어판이라고 정책이 느슨해지면 안 된다(영어판에서 색인되면 정책 우회가 된다).
EN_SITEMAP_EXCLUDED: frozenset[str] = frozenset({"findings/inspector/"})


def en_tree_paths(catalogs: "list[dict[str, Any]] | None" = None) -> set[str]:
    """영어 트리 경로 집합 — 정적 목록 + 실제로 로드된 자료실 카탈로그.

    카탈로그는 데이터 파일이 있는 것만 렌더되므로(`load_library`), 그 결과에서 파생해야
    "sitemap 에는 있는데 파일이 없다"가 생기지 않는다(손목록 금지 규율).
    """
    return set(EN_TREE_STATIC) | {
        f"library/{v['slug']}/" for v in (catalogs or [])}


class PagePath:
    """사이트 루트 기준 디렉터리 경로 1개(`""` = 홈, 그 외 `a/b/` 꼴) + 언어 → 모든 주소.

    값 객체(불변). 경로는 렌더가 실제로 쓰는 문자열 그대로 받으며, 디렉터리 페이지는
    `index.html` 로 끝나는 파일 하나를 낸다. 파일 이름이 다른 부속 산출물(`share.txt`·
    `404.html`)은 `file()` 로 같은 디렉터리 안의 이름을 만든다.

    ★빈 세그먼트를 거부한다 — 종전 `out_dir / "findings" / "doc" / slug / "index.html"` 은
      slug 가 "" 이면 Path 가 조용히 접어 **부모 색인을 덮어썼다**. 주소는 조용히 접히면 안 된다.
    """

    __slots__ = ("path", "lang")

    def __init__(self, path: str, lang: str = DEFAULT_LANG) -> None:
        if lang not in LANG_PREFIXES:
            raise ValueError(f"모르는 언어 코드: {lang!r} (허용: {sorted(LANG_PREFIXES)})")
        if path.startswith("/") or "\\" in path:
            raise ValueError(f"사이트 루트 기준 상대 경로여야 한다: {path!r}")
        if path and not path.endswith("/"):
            raise ValueError(f"디렉터리 경로는 '/' 로 끝나야 한다(부속 파일은 .file()): {path!r}")
        segments = path[:-1].split("/") if path else []
        if any(seg in ("", ".", "..") for seg in segments):
            raise ValueError(f"빈 세그먼트·상대 참조가 든 경로: {path!r}")
        self.path = path
        self.lang = lang

    # ── 파생값 ──
    @property
    def prefix(self) -> str:
        """언어 접두(`""` 또는 `"en/"`)."""
        return LANG_PREFIXES[self.lang]

    @property
    def site_path(self) -> str:
        """사이트 루트 기준 경로(접두 포함) — 출력·canonical·sitemap 이 쓰는 값."""
        return self.prefix + self.path

    @property
    def depth(self) -> int:
        """사이트 루트로부터의 디렉터리 깊이(홈 = 0)."""
        return self.site_path.count("/")

    @property
    def rel_root(self) -> str:
        """이 페이지에서 **언어 트리 루트**로 가는 상대 접두(`../` × 깊이). 템플릿 내부
        링크(`{{ rel_root }}findings/`)가 같은 언어 트리 안에 머물게 하는 값이다."""
        return "../" * self.path.count("/")

    @property
    def asset_root(self) -> str:
        """이 페이지에서 **사이트 루트**로 가는 상대 접두 — 언어 무관 공유 자원용."""
        return "../" * self.depth

    @property
    def out_file(self) -> str:
        """out_dir 기준 출력 파일(항상 `.../index.html`) — `written` 목록에 남는 문자열."""
        return self.site_path + "index.html"

    def file(self, name: str) -> str:
        """같은 디렉터리 안의 부속 파일 경로(`briefs/{pub}/share.txt`·`404.html`)."""
        if not name or "/" in name or name in (".", ".."):
            raise ValueError(f"파일 이름 하나여야 한다: {name!r}")
        return self.site_path + name

    @property
    def canonical(self) -> str:
        """절대 canonical URL(트레일링 슬래시 디렉터리형)."""
        return _abs_url(self.site_path)

    def alternate(self, lang: str) -> "PagePath":
        """같은 페이지의 다른 언어판(hreflang 상대) — path 는 그대로, 접두만 바뀐다."""
        return PagePath(self.path, lang)

    def breadcrumb_json_ld(self, trail: "list[tuple[str, str]]") -> str:
        """빵부스러기 JSON-LD — 절대 URL 이 이 페이지의 언어 트리 안에서 만들어진다."""
        return build_breadcrumb_json_ld(trail, lang=self.lang)

    # ── 값 객체 ──
    def __eq__(self, other: object) -> bool:
        return (isinstance(other, PagePath)
                and (self.path, self.lang) == (other.path, other.lang))

    def __hash__(self) -> int:
        return hash((self.path, self.lang))

    def __repr__(self) -> str:
        return f"PagePath({self.path!r}, lang={self.lang!r})"


# ── 검색 인덱스(P4 — 정적·결정론·무변형) ──────────────────────────────────────
# 인덱스는 **아카이브 페이지(`archive/index.html`, 깊이 1)** 전용 → href 는 그 페이지
# 기준 상대경로(`../`). render.py 가 페이지마다 새로 만들지 않는 단일 산출물이라 접두를
# 여기 고정한다(검색은 spec 상 아카이브에만 얹는다 — P4 §2.3). 값은 손으로 적지 않고
# 아카이브 페이지의 주소에서 파생한다(깊이 규칙의 단일 원천 = PagePath).
_ARCHIVE_REL = PagePath("archive/").rel_root


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
        f"- [브리프 아카이브]({base_url}/archive/): 지금까지 발행한 주간 브리프 {len(pubs)}건",
        "",
        "## 규제 지적사항 데이터",
        f"- [지적사항 검색]({base_url}/findings/): FDA 483 · Warning Letter · EU/영국"
        " GMP 비준수 · 캐나다 실사 · 식약처 지적사항 통합 검색",
        f"- [지적사항 둘러보기]({base_url}/findings/browse/): 업무별 바로가기 ·"
        " 최근 공개 문서 · 분류·국가·기관 축별 탐색",
        f"- [문서로 찾기]({base_url}/findings/docs/): 실사 문서 단위 한국어 정리"
        f" {n_docs:,}건 — 기관·연도별 색인",
        f"- [지적 경향]({base_url}/findings/trends/): 최근 12개월 지적 영역 순위 ·"
        " 많이 인용된 조항",
        f"- [FDA 실사 결과]({base_url}/findings/inspections/): FDA GMP 실사 등급"
        "(NAI·VAI·OAI) 연도별·국가별 집계",
        f"- [데이터 현황]({base_url}/findings/coverage/): 소스 구성 · 연도별 확보량 ·"
        " 수집 범위와 한계",
        f"- [업체 조회]({base_url}/findings/firm/): 업체명으로 그 업체의 지적 이력 조회",
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
                      facet_paths: "list[tuple[str, str]] | None" = None,
                      en_paths: "set[str] | None" = None) -> str:
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
        # [2면 분리 2026-08-27] 둘러보기 면 — 정적 콘텐츠 면이라 등록한다.
        f"  <url><loc>{base_url}/findings/browse/</loc><lastmod>{latest_pub}</lastmod></url>",
        f"  <url><loc>{base_url}/findings/trends/</loc><lastmod>{latest_pub}</lastmod></url>",
        # [존 재편 2026-08-26] 트렌드 존 2·3면 — 조회 파라미터 없이 그 자체로 완결된
        # 집계 페이지라 체크리스트와 같은 근거로 등록한다(개인·업체 식별 정보 없음).
        f"  <url><loc>{base_url}/findings/inspections/</loc><lastmod>{latest_pub}</lastmod></url>",
        f"  <url><loc>{base_url}/findings/coverage/</loc><lastmod>{latest_pub}</lastmod></url>",
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
        # [다국어 3단계] 영어 트리 — 본문이 영어로 성립하는 면만 등록한다(EN_TREE_STATIC).
        # 경로는 렌더가 실제로 쓴 집합 그대로라(손으로 다시 적지 않는다) 페이지와 sitemap 이
        # 갈라질 수 없다. lastmod 는 비운다 — 한국어 짝과 같은 데이터라 별도 날짜가 없다.
        *(f"  <url><loc>{base_url}/{LANG_PREFIXES['en']}{p}</loc></url>"
          for p in sorted((en_paths or set()) - EN_SITEMAP_EXCLUDED)),
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
                                base_url: str = SITE_BASE_URL,
                                tr: Translator = _KO) -> str:
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
            "name": tr("GRM 규제 용어사전"),
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
# [다국어 2단계] 아래 카피는 N_ 로 키를 등록만 한다 — 번역은 쓰는 자리(render_site)에서
# `tr(상수)`. RSS 는 한국어 채널이라 그대로 둔다(영어 피드는 별도 결정).
SITE_DESCRIPTION = N_("전 세계 제약 GMP·품질 규제 소식을 매주 한자리에 모아 "
                      "기관별 정렬·시사점·점검까지 정리하는 규제뉴스.")
ARCHIVE_DESCRIPTION = N_("GRM 주간 브리프 아카이브 — 전 세계 제약 GMP·품질 규제 소식을 "
                         "주차별로 모아 기관·기간으로 검색·필터.")
FINDINGS_DESCRIPTION = N_("FDA 483 Observation · Warning Letter · 캐나다 실사 · 식약처 · "
                          "EU/영국 GMP 비준수 지적사항을 원문에서 자동 추출한 데이터베이스. "
                          "검색과 업체 이력·실사관 조회·자가점검 체크리스트를 제공합니다.")
# [2면 분리 2026-08-27] 둘러보기 면 — 업무별 진입·최근 공개 문서·축별 탐색.
FINDINGS_BROWSE_DESCRIPTION = N_("규제 지적사항 데이터 둘러보기 — 업무별 바로가기, "
                                 "최근 공개된 실사 문서, 분류·국가·기관 축별 탐색.")
# [존 재편 2026-08-26] 트렌드 존이 세 면으로 갈리면서 이 설명도 '지적 경향' 면만 가리킨다 —
# 연도별 구성비·업체 랭킹은 데이터 현황 면으로 옮겼으므로 문안에서도 뺐다(설명이 실제
# 페이지 내용과 어긋나면 검색결과 스니펫이 먼저 거짓말을 한다).
TRENDS_DESCRIPTION = N_("식약처·FDA 중 기관을 골라 최근 12개월에 가장 많이 지적된 영역과 조항을 확인하고 "
                        "자가점검 체크리스트까지 — 기관마다 상위 지적이 다릅니다. "
                        "FDA 483·Warning Letter·식약처·캐나다 실사·EU/영국 GMP 비준수 집계.")
INSPECTIONS_DESCRIPTION = N_("FDA가 의약품 제조소를 실사하고 매긴 등급(NAI·VAI·OAI) 통계 — "
                             "연도별·국가별 중대 지적 비율로 보는 FDA GMP 실사 결과.")
COVERAGE_DESCRIPTION = N_("GRM 규제 지적사항 데이터의 수집 현황 — 기관별 소스 구성, 연도별 확보량, "
                          "연도별 지적 구성비로 트렌드 수치를 어디까지 믿을 수 있는지 밝힙니다.")
FIRM_DESCRIPTION = N_("특정 업체의 FDA 483·Warning Letter·캐나다 실사·식약처·EU/영국 GMP 비준수 지적사항 "
                      "누적 이력을 카테고리·연도별 추이·문서 이력으로 한 곳에서 확인하는 업체 프로파일.")
CHECKLIST_DESCRIPTION = N_("규제기관이 실제로 인용한 21 CFR 조항을 인용 빈도순으로 뽑고 조항별 실제 "
                           "지적 문장을 붙인 GMP 자가점검 체크리스트 — 인쇄·엑셀 내보내기 지원.")
INSPECTOR_DESCRIPTION = N_("공개된 FDA 483 문서에 서명한 실사관의 지적사항 이력을 "
                           "카테고리·연도별 추이·문서 이력으로 한 곳에서 확인하는 실사관 프로파일.")
LIBRARY_DESCRIPTION = N_("FDA·EMA·식약처·PIC/S·ICH·WHO·PMDA 등 국내외 규제기관의 GMP 지침·고시·"
                         "기준서를 한곳에 모은 규제 자료실 — 공식 원문 링크와 함께 언제든 다시 찾아보세요.")
GUIDE_DESCRIPTION = N_("GRM 이용 안내 — 월요일 브리프 3분 활용법, findings 검색 실전 예시, "
                       "자료실·용어사전·퀴즈 활용법과 자주 묻는 질문을 한곳에 정리했습니다.")
RSS_TITLE = "GRM 주간 브리프 · 글로벌 규제 인텔리전스"
RSS_DESCRIPTION = ("전 세계·국내 제약 GMP/품질 규제 소식을 매주 한국어로 정리해 드립니다. "
                   "FDA·EMA·식약처·캐나다 보건부 등의 공개 자료가 원천입니다.")
GLOSSARY_DESCRIPTION = N_("제약 GMP·규제 용어사전 — GMP·CAPA·데이터 완전성·무균 공정·ICH 등 "
                          "핵심 용어를 쉬운 풀이와 공식 출처로 설명합니다.")
QUIZ_DESCRIPTION = N_("GRM 주간 퀴즈 — 규제·품질 용어와 최근 공개 사례를 짧게 복습하는 "
                      "전 직원 학습 퀴즈. 선택 즉시 정답·해설·근거 링크를 확인하세요.")


def _abs_url(rel_path: str = "") -> str:
    """SITE_BASE_URL + 경로 → 절대 canonical(트레일링 슬래시 디렉터리형). 랜딩=베이스/."""
    return f"{SITE_BASE_URL}/{rel_path}"


def _brief_description(brief_meta: dict[str, Any], tr: Translator = _KO) -> str:
    """브리프 description = tldr[0] 있으면 사용, 없으면 날짜 파생 한 줄(결정론)."""
    tldr = brief_meta.get("tldr") or []
    if tldr and tldr[0]:
        return tldr[0]
    return tr("{date} 글로벌·국내 제약 GMP·품질 규제 소식.",
              date=title_dateform(brief_meta.get("publish_date", ""), tr))


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


def build_breadcrumb_json_ld(trail: "list[tuple[str, str]]",
                             base_url: str = SITE_BASE_URL,
                             lang: str = DEFAULT_LANG) -> str:
    """schema.org BreadcrumbList — 검색 결과의 **URL 자리**를 읽을 수 있는 경로로 바꾼다.

    [B2 2026-08-27] 문서·업체·모음 약 4천 장은 구조화 데이터가 하나도 없었고, 그래서
    검색 결과에 `grm-solutions.com/findings/doc/hc-insp-89240/` 같은 **날 슬러그**가
    그대로 노출됐다(우리 슬러그는 기관 원문 id 라 사람이 읽을 수 없다). 화면에는 이미
    빵부스러기가 있는데 마크업만 없었다 — 제목을 흔들지 않고 SERP 표시를 고치는 자리다.

    ★구글 요건: 마크업은 **화면에 보이는 빵부스러기와 같은 순서·같은 이름**이어야 한다.
    그래서 이 함수는 문자열을 지어내지 않고 템플릿이 그리는 것과 같은 trail 을 받는다.
    마지막 항목은 현재 페이지라 `item` 을 붙이지 않는다(자기 자신 링크 금지 관례).

    trail 은 (이름, **언어 트리 루트** 기준 상대경로) 목록이고, 마지막 항목의 경로는 빈 문자열.
    `lang` 이 접두를 정한다(한국어 = 접두 없음 → 종전과 동일, 영어 = `/en/...`) — 화면의
    빵부스러기 링크가 `{{ rel_root }}` 로 같은 언어 트리 안에 머무는 것과 같은 규칙이다.
    build_json_ld 와 동일한 직렬화 계약('<' 치환으로 </script> 조기종료 차단).
    """
    prefix = LANG_PREFIXES[lang]
    items = []
    for i, (name, path) in enumerate(trail, start=1):
        node: dict[str, Any] = {"@type": "ListItem", "position": i, "name": name}
        if path:
            node["item"] = f"{base_url}/{prefix}{path.lstrip('/')}"
        items.append(node)
    return json.dumps({"@context": "https://schema.org",
                       "@type": "BreadcrumbList",
                       "itemListElement": items},
                      ensure_ascii=False, indent=1).replace("<", "\\u003c")


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
def _make_env(lang: str = DEFAULT_LANG,
              translator: "Translator | None" = None) -> Environment:
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
    # [다국어 2단계] 템플릿 전역 `_("…")` — 문구 사전. 한국어는 항등(Markup 으로 그대로),
    # 영어는 카탈로그 결손 시 즉시 실패. 슬롯 `{name}` 값은 escape 된다.
    env.globals["_"] = (translator or Translator(lang)).template_gettext()
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
    # [다국어 1·2단계] 이 빌드가 그리는 언어 트리와 그 언어의 번역기. 페이지 주소는 page()
    # 한 곳에서, 화면 문구는 tr()/템플릿 `_()` 한 곳에서 나온다. 영어 트리(3단계)는 이 둘을
    # 루프 변수로 바꾸는 것으로 얹는다.
    lang = DEFAULT_LANG
    tr = Translator(lang)
    env = _make_env(lang, tr)
    # 소유권 인증 메타(env-param) — 전 페이지 <head> 공통(미설정 시 미출력). 아래 전역들
    # (SITE_BASE_URL·NEWSLETTER_FORM_ACTION·*_SITE_VERIFICATION)은 import 시점에 os.environ
    # 에서 캡처된다. 여기서는 그 모듈 전역을 render_site() 호출 시점에 env.globals 로 주입 —
    # 테스트가 모듈 속성(render.SITE_BASE_URL 등)을 monkeypatch 하면 반영되지만 os.environ 을
    # 호출 시점에 재조회하진 않는다(monkeypatch 계약 = 모듈 속성 기준, os.environ 아님).
    env.globals["google_site_verification"] = GOOGLE_SITE_VERIFICATION
    env.globals["naver_site_verification"] = NAVER_SITE_VERIFICATION
    env.globals["og_image"] = f"{SITE_BASE_URL}/assets/og-image.png"
    env.globals["og_locale"] = LANG_OG_LOCALE[lang]
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
    env.globals["feedbackjs_ver"] = _asset_ver("feedback.js")
    env.globals["adminjs_ver"] = _asset_ver("admin.js")
    briefs = load_briefs(data_dir)
    if not briefs:
        raise SystemExit(f"입력 브리프 없음: {data_dir}")

    issue_no_by_date = assign_issue_numbers(briefs)
    latest_slug = max(b["brief"].get("publish_date", "") for b in briefs)
    latest_brief = next(b for b in briefs if b["brief"].get("publish_date", "") == latest_slug)
    latest_issue_no = issue_no_by_date[latest_slug]

    written: list[str] = []

    # [다국어 3단계] 영어 트리에 실제로 있는 경로 — nav·푸터·언어 전환·hreflang·sitemap 이
    # 전부 이 하나를 본다. 자료실 카탈로그가 섞이므로 **카탈로그 로드 직후** 확정된다
    # (아래 `en_paths = en_tree_paths(catalogs)`). 첫 렌더(랜딩)는 그 뒤라 항상 채워져 있고,
    # 어긋나면 `WebEnTreeTest.test_emitted_en_paths_match_the_declared_set` 이 잡는다.
    en_paths: set[str] = set()

    # 페이지 주소는 전부 page() 한 곳에서 나온다 — 렌더 호출마다 rel_root·출력 경로·
    # canonical 을 손으로 적지 않는다(PagePath). 언어 트리마다 같은 헬퍼를 다시 만든다
    # (사본이 아니라 같은 공장에서 나온다 — ko/en 이 갈라질 자리가 없다).
    def _make_tree(tree_lang: str, tree_env: Environment):
        def page(path: str) -> PagePath:
            return PagePath(path, tree_lang)

        def render_page(template: str, pp: PagePath, **ctx: Any) -> str:
            """템플릿 1장 렌더 — 주소 파생값(rel_root·asset_root·lang·canonical)·언어 대체
            링크·latest_slug 를 여기서 한 번만 주입한다. ctx 가 같은 키를 주면 그쪽이
            이긴다(404·admin 처럼 canonical 을 비우는 페이지)."""
            # hreflang·언어 전환은 **양쪽에 실제로 있는 페이지만** 짝짓는다 — 없는 페이지로
            # 보내는 링크는 무링크보다 나쁘다(저장소 규율). 짝이 없으면 빈 목록이라
            # base.html 의 {% if %} 가 태그·버튼을 통째로 생략한다.
            # `href` = 절대 URL(hreflang 태그의 요건). `rel_href` = 이 페이지에서 그리로 가는
            # **상대경로** — 화면 링크는 상대경로여야 한다(README 불변식 #4 호스트 무관).
            # 절대 URL 로 걸면 도달성 BFS 가 못 따라가 영어 트리가 고립된 섬이 된다.
            alts = ([{"lang": lg, "href": PagePath(pp.path, lg).canonical,
                      "rel_href": pp.asset_root + PagePath(pp.path, lg).site_path
                      + "index.html",
                      "label": LANG_ENDONYM[lg], "current": lg == tree_lang}
                     for lg in SUPPORTED_LANGS]
                    if pp.path in en_paths else [])
            return tree_env.get_template(template).render({
                "rel_root": pp.rel_root, "asset_root": pp.asset_root, "lang": pp.lang,
                "canonical": pp.canonical, "latest_slug": latest_slug,
                "alternates": alts, "en_paths": en_paths,
                # 헤더 전환 버튼이 쓸 "다른 언어" 하나(짝이 없으면 None → 버튼 미출력).
                "alt_other": next((a for a in alts if not a["current"]), None),
                **ctx,
            })

        def emit(template: str, pp: PagePath, **ctx: Any) -> None:
            """페이지 1장 = 렌더 + 쓰기 + 산출 목록. 출력 경로와 `written` 항목이 같은
            주소에서 나오므로 둘이 어긋날 수 없다."""
            _write(out_dir / pp.out_file, render_page(template, pp, **ctx))
            written.append(pp.out_file)

        return page, render_page, emit

    page, render_page, emit = _make_tree(lang, env)

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

    # [다국어 3단계] 영어 화면 스크립트용 문구 사전 — `/en/` 페이지의 <head> 가 다른 스크립트
    # **앞에서** 이 파일을 defer 로 싣는다(문서 순서 = defer 실행 순서). 카탈로그 전량을
    # 싣는다: JS 가 쓰는 키만 골라 담으면 그 선별이 낡는 순간 영어 화면에 한국어가 조용히
    # 남는다(이 저장소가 반복해서 데인 '조용한 결손'). 여분 키는 조회 테이블의 무해한 무게다.
    _en_translator = Translator("en")
    _i18n_en_js = build_js_catalog(_en_translator.catalog, _en_translator.catalog)
    _write(dist_assets / "i18n-en.js", _i18n_en_js)
    written.append("assets/i18n-en.js")
    # 캐시버스팅은 **생성된 내용**에서 파생한다(assets_dir 에 원본이 없는 유일한 자산).
    env.globals["i18nenjs_ver"] = hashlib.sha1(
        _i18n_en_js.encode("utf-8")).hexdigest()[:8]

    # [자료실] 카탈로그 스냅샷 + 최근 변경 이력 — 랜딩 카드·자료실 허브·아카이브 스트립이
    # 함께 쓰므로 세 렌더보다 먼저 한 번만 읽는다(같은 입력 → 같은 출력).
    catalogs = load_library(tr=tr)
    # [다국어 3단계] 영어 트리 경로 확정 — 여기부터 모든 렌더가 이 집합을 본다(위 선언 참조).
    en_paths = en_tree_paths(catalogs)
    library_updates = load_library_updates(catalogs)
    # [브리프 자료실 스트립] load_library_updates() 는 "최신 1건"만 보므로 브리프
    # 상세(과거 특정 주간)에는 못 쓴다 — 이력 전체를 한 번 더 확보해 브리프 루프에서
    # 각자의 window 로 걸러 쓴다(같은 파일을 두 번 파싱하지 않도록 여기서 한 번만 로드).
    lib_entries = load_library_update_entries()
    # 랜딩 자료실 카드용 집계 — **수치를 템플릿에 박지 않는다**. 카탈로그가 늘 때마다
    # 사람이 문구를 고쳐야 하면 반드시 낡는다(이용안내가 그렇게 낡았다 — 2026-07-25).
    library_summary = {
        "catalog_count": len(catalogs),
        "item_count": sum(v["count"] for v in catalogs),
    }

    # [검색 유입] findings 정본을 랜딩보다 먼저 읽는다 — 랜딩 '데이터 존' 섹션과 findings
    # 허브 셸이 같은 스냅샷(문서·지적 건수, 최근 공개 문서)을 쓰기 때문이다. 정적 표면으로
    # 가는 **유일한 진입 간선**(홈 BFS 도달 28/3,520 이던 고립을 메운 경로)도 이 데이터로
    # 렌더한다. 파일 로드일 뿐이라 렌더 비용이 아니다.
    facets = load_findings_facets()
    docs_data = load_findings_docs()
    # [조항 페이지] 조항 뷰는 **용어사전보다 먼저** 만든다 — 용어사전의 "관련 조항"이
    # 실제로 만들어진 조항 페이지에만 링크를 걸어야 하기 때문(없는 페이지로 보내는 링크는
    # 무링크보다 나쁘다). 커밋 정본 3종에서만 파생하므로 네트워크·순서 부담 0.
    clause_views = build_clause_views(docs_data, load_cfr_catalog(), load_glossary())
    clause_slugs = {c["slug"] for c in clause_views}
    doc_slugs: set[str] = {d["slug"] for d in (docs_data or {}).get("documents", [])}
    # [발견 허브 2026-08-26] 랜딩·findings 허브 공용 요약 — 수치를 템플릿에 박지 않는다
    # (자료실 카드와 같은 계약: 손으로 적은 수치는 반드시 낡는다). 데이터가 없으면 None
    # → 해당 섹션이 조용히 꺼진다(load_findings_facets 관례 동형).
    findings_zone = None
    if facets:
        findings_zone = {
            "documents": f"{facets['totals']['documents']:,}",
            "findings": f"{facets['totals']['findings']:,}",
        }

    # 랜딩.
    emit("landing.html", page(""),
        page_title="GRM · Global Regulatory Monitor",
        nav_active="home",
        description=tr(SITE_DESCRIPTION),
        json_ld=build_json_ld(),
        cover=_cover_context(latest_brief, latest_issue_no, tr),
        library=library_summary,
        findings_zone=findings_zone,
    )

    # 아카이브(최신호 desc 정렬).
    issues = sorted(
        (_issue_row(b, issue_no_by_date[b["brief"].get("publish_date", "")], latest_slug)
         for b in briefs),
        key=lambda r: r["date"], reverse=True,
    )
    emit("archive.html", page("archive/"),
        # [네이밍 2026-08-27] nav 탭·h1(주간 브리프 아카이브)과 정합 — 제목만 옛
        # 이름이면 검색 결과와 탭 사이에서 페이지 정체가 갈라진다.
        page_title=tr("주간 브리프 아카이브 · GRM"),
        nav_active="board",
        description=tr(ARCHIVE_DESCRIPTION),
        issues=issues,
        lib_update=library_updates["compact"],
    )

    # facets/docs_data/doc_slugs 는 랜딩 직전에 이미 로드됐다(랜딩 '데이터 존' 섹션과
    # 공용). 모음 페이지의 사례가 문서 페이지로 이어지려면 "그 문서에 페이지가 있는가"
    # (doc_slugs)도 필요하다.

    # 진입 카드는 **데이터가 있는 축만** 만든다 — 없는 페이지로 보내는 링크는 무링크보다
    # 나쁘다. 문서 축은 렌더 스위치가 꺼진 테스트 빌드에서 페이지가 없으므로 함께 건다.
    _axis_blurb = {
        "category": tr("무균공정·시험실 관리처럼 실사에서 반복되는 주제로 묶어 봅니다."),
        "country": tr("제조소가 어느 나라에 있는지로 묶어 봅니다."),
        "agency": tr("FDA·캐나다 보건부·식약처 등 기관별로 묶어 봅니다."),
    }
    browse_axes = []
    for _axis in (facets.get("axes") if facets else []) or []:
        _meta = FACET_AXES.get(_axis["axis"])
        if not _meta or not _axis.get("items"):
            continue
        browse_axes.append({"href": f"findings/{_meta['path']}/",
                            "title": tr(_meta["title"]),
                            "blurb": _axis_blurb.get(_axis["axis"], "")})
    # ★sitemap 과 같은 규칙: **데이터에서 파생**하지 렌더 결과에서 파생하지 않는다.
    # `render_doc_pages` 로 가르면 테스트 빌드의 골든이 프로덕션과 다른 것을 고정하게 되어
    # (골든에 이 카드가 없는데 라이브엔 있는 상태) 대조가 의미를 잃는다.
    if docs_data and docs_data.get("documents"):
        browse_axes.append({
            "href": "findings/docs/", "title": tr("문서로 찾기"),
            "blurb": tr("실사 문서 {n}건을 기관·연도로 묶어 봅니다.",
                        n=f"{docs_data['totals']['documents']:,}"),
        })

    # [발견 허브] 최근 공개 문서 — 문서 페이지 정본(findings_docs.json)에서 공개일
    # 내림차순 5건(동일 날짜는 slug 로 갈라 결정론 유지). 검색 결과와 달리 fetch 없이
    # 첫 화면에서 바로 보이는 정적 미리보기라, 스냅샷 기준일(measured_on)을 함께 적는다.
    recent_docs = []
    if docs_data:
        for d in sorted(docs_data.get("documents", []),
                        key=lambda x: (x.get("published_date", ""), x.get("slug", "")),
                        reverse=True)[:5]:
            first = (d.get("findings") or [{}])[0]
            snippet = " ".join(str(first.get("text_ko", "")).split())
            if len(snippet) > 92:
                snippet = snippet[:92].rstrip() + "…"
            agency = d.get("agency", "")
            agency_label = ((facets or {}).get("agency_labels") or {}).get(agency, agency)
            recent_docs.append({
                "date": d.get("published_date", ""),
                "src_label": doc_source_label(d) or agency_label,
                "firm": d.get("firm_name", ""),
                "slug": d.get("slug", ""),
                "cats": (d.get("categories") or [])[:2],
                "snippet": snippet,
            })

    # 지적사항 검색(FIND-1 M3c) — 라이브 데이터(Supabase PostgREST)라 빌드시 목록을 고정할
    # 수 없다. 서버는 셸(로딩 상태)만 렌더 — env 미설정이면 findings.js 가 "준비 중" 안내로
    # 조용히 종료한다(cfg data 속성은 위 reactions_enabled 와 무관하게 항상 주입).
    # [2면 분리 2026-08-27] 발견 허브(#811)가 쌓은 세 섹션은 둘러보기 면으로 이동 —
    # 사용자 피드백("너무 많은 정보가 한 페이지에"). 이 면은 검색 도구 전용이다.
    emit("findings.html", page("findings/"),
        zone_totals=findings_zone,
        page_title=tr("지적사항 검색 · GRM"),
        nav_active="findings",
        description=tr(FINDINGS_DESCRIPTION),
    )

    # [2면 분리] 둘러보기 면 — 정적 렌더 전용(fetch 0, 커밋된 스냅샷에서 나옴).
    emit("findings_browse.html", page("findings/browse/"),
        browse_axes=browse_axes,
        zone_totals=findings_zone,
        recent_docs=recent_docs,
        recent_asof=(docs_data or {}).get("measured_on", ""),
        has_docs=bool(docs_data and docs_data.get("documents")),
        page_title=tr("지적사항 둘러보기 · GRM"),
        nav_active="findings",
        description=tr(FINDINGS_BROWSE_DESCRIPTION),
    )

    # 트렌드 대시보드(FIND-1 F3b) — findings 와 동일 이유로 라이브 데이터는 빌드시 고정할
    # 수 없다(집계는 Supabase RPC findings_stats/findings_firm_stats 를 trends.js 가 직접
    # fetch). 서버는 셸(로딩 상태)만 렌더.
    # [존 재편 2026-08-26] 트렌드 존은 이제 세 면이다(지적 경향 / 실사 결과 / 데이터 현황).
    # nav 탭은 '트렌드' 하나로 유지하고(저장소 'nav 과밀 금지' 원칙) 면 전환은
    # partials/trends_seg.html 이 맡는다 — seg_active 가 그 파셜의 활성 탭을 정한다.
    # 세 면 모두 trends.html 계열 셸이라 nav_active 는 동일하고, 셸의
    # cfg data-page 로 trends.js 가 "이 면이 그릴 수 있는 것"만 fetch 한다.
    emit("trends.html", page("findings/trends/"),
        page_title=tr("규제 지적사항 트렌드 · GRM"),
        nav_active="trends",
        seg_active="trends",
        description=tr(TRENDS_DESCRIPTION),
    )

    # 실사 결과(트렌드 존 2면) — 058/059 fda_inspection_stats() 전용. findings 계열과
    # **단위가 다르다**(실사 건 vs 지적 문장). 재편 전에는 지적사항 페이지에 얹혀 있어
    # "두 수치를 서로 나누지 마세요"라는 경고문이 필요했는데, 면을 가르면 그 경고가
    # 필요 없어진다 — 분모가 다른 것을 같은 페이지에 두지 않는 것이 이 재편의 핵심이다.
    emit("inspections.html", page("findings/inspections/"),
        page_title=tr("FDA 실사 결과 · GRM"),
        nav_active="trends",
        seg_active="inspections",
        description=tr(INSPECTIONS_DESCRIPTION),
    )

    # 데이터 현황 — 소스 구성·연도별 공개량·연도별 구성비·수집량 상위 업체 +
    # (컨셉 재정의로 넘어온) 전 기간 누적 순위·해외vs미국. 전부 "규제가 어떻게 변하나"가
    # 아니라 **"우리가 무엇을 얼마나 모았나"**에 답하는 블록이다.
    # ★[컨셉 재정의 2026-08-26] **세그먼트에서 내렸다**(seg_active 를 넘기지 않는다) —
    #   이 면이 답하는 것은 사용자가 하려는 일이 아니라 우리가 신뢰를 얻으려는 일이라
    #   nav 여섯 탭 어느 job 에도 속하지 않는다. 라우트·sitemap·footer 도구 열은 그대로
    #   두고, 두 면의 꼬리 각주가 이 페이지를 연다(숫자를 의심하는 사람만 마주친다).
    emit("coverage.html", page("findings/coverage/"),
        page_title=tr("데이터 현황 · GRM"),
        nav_active="trends",
        seg_active="",
        description=tr(COVERAGE_DESCRIPTION),
    )

    # 업체 프로파일(FIND-FIRM-ALIAS 웹 절반) — findings/trends 와 동일 이유로 라이브
    # 데이터는 빌드시 고정할 수 없다(013_findings_firm_key.sql 의 findings_firm_profile
    # RPC 를 firm.js 가 URL 파라미터(?key=)로 직접 fetch). 서버는 셸(로딩 상태)만 렌더.
    emit("firm.html", page("findings/firm/"),
        page_title=tr("업체 프로파일 · GRM"),
        nav_active="findings",
        description=tr(FIRM_DESCRIPTION),
    )

    # 자가점검 체크리스트 — findings/trends 와 동일 이유로 라이브 데이터는 빌드시 고정할 수
    # 없다(042 findings_cfr_ranking 로 조항 순위 + 043 findings_checklist 로 사례를 받아
    # checklist.js 가 조립). 서버는 셸(설정 바 + 로딩 상태)만 렌더한다.
    emit("checklist.html", page("findings/checklist/"),
        page_title=tr("자가점검 체크리스트 · GRM"),
        nav_active="trends",
        description=tr(CHECKLIST_DESCRIPTION),
    )

    # 실사관 프로파일(FDA 483 서명 실사관 집계, firm 프로파일의 미러링) — findings/firm 과
    # 동일 이유로 라이브 데이터는 빌드시 고정할 수 없다(findings_inspector_profile RPC 를
    # inspector.js 가 URL 파라미터(?key=)로 직접 fetch). 서버는 셸(로딩 상태)만 렌더.
    # ★sitemap 미등록(의도, firm 과 다름) — 실명이 적시된 개인 집계라 베이스 경로조차
    # 넣지 않는다. noindex 는 inspector.html 자체 <head> 오버라이드(meta_robots 블록)로
    # 배선하고, canonical 은 중복 URL 정리 목적으로 그대로 둔다.
    emit("inspector.html", page("findings/inspector/"),
        page_title=tr("실사관 프로파일 · GRM"),
        nav_active="findings",
        description=tr(INSPECTOR_DESCRIPTION),
    )

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
        emit("library.html", page("library/"),
            page_title=tr("자료실 · GRM"),
            nav_active="library",
            description=tr(LIBRARY_DESCRIPTION),
            catalogs=hub_catalogs,
            lib_update=library_updates["latest"],
        )

    # 카탈로그 상세 — registry 전 항목을 공통 템플릿(library_catalog.html) 하나로 렌더.
    # 카탈로그 1개 추가 = 데이터 파일 + LIBRARY_REGISTRY 1항목(여기·템플릿 무수정).
    for v in catalogs:
        emit("library_catalog.html", page(f"library/{v['slug']}/"),
            page_title=tr("{title} · GRM", title=v["title"]),
            nav_active="library",
            description=v["desc"],
            lib=v,
        )

    # 이용 안내(트랙 C 2차 웨이브) — guide_content.md(정본)를 제한 md 서브셋으로 결정론
    # 렌더. 라이브 데이터가 아니라 커밋 콘텐츠라 골든으로 고정된다. 파일 부재 시 조용히 생략.
    guide_md = load_guide()
    if guide_md:
        guide_title, guide_toc, guide_body = render_guide_html(guide_md)
        emit("guide.html", page("guide/"),
            page_title=tr("이용 안내 · GRM"),
            nav_active="guide",
            description=tr(GUIDE_DESCRIPTION),
            guide_title=guide_title,
            guide_toc=guide_toc,
            guide_body=guide_body,
        )

    # 용어사전(트랙 C 2차 웨이브) — glossary.json(정본)을 초성 색인 1페이지로 결정론 렌더.
    # 클라이언트 필터는 assets/glossary.js(신규·별도 asset). 파일 부재 시 조용히 생략.
    # nav_active="glossary"(8차 웨이브 A 2026-07-18 — nav 에 용어사전 전용 탭 신설).
    glossary_terms = load_glossary()
    glossary_term_ids: list[str] = []
    if glossary_terms:
        # B2: 관련 조항 라벨 → 공식 원문 URL — 자료실 커밋 카탈로그 재사용(신규 수집 0).
        # [C1] 용어→사례 링크: glossary_cases.json(정본, findings_search RPC 실측치).
        glossary_view = build_glossary_view(
            glossary_terms, _load_reg_ref_catalogs(), load_glossary_cases(),
            clause_slugs)
        emit("glossary.html", page("glossary/"),
            page_title=tr("규제 용어사전 · GRM"),
            nav_active="glossary",
            description=tr(GLOSSARY_DESCRIPTION),
            glossary=glossary_view,
        )

        # [용어사전 낱개] 용어당 1 페이지 — 검색 유입 트랙. 색인 페이지와 **같은 뷰모델**을
        # 재사용한다(별도 가공 0 → 두 화면이 갈라질 수 없다). 정렬은 뷰모델 순서 그대로라
        # 결정론이고, sitemap 도 이 순서를 쓴다.
        #   · title 은 `glossary_term_page_title` — 실제 검색어 형태("OOS 뜻")에 맞추고
        #     SERP 절단선 안에 들어가도록 영문은 약어로 접는다.
        #   · case_excerpts 는 커밋된 문서 정본에서 파생한 실제 지적 문장(순위 트랙).
        case_excerpts = build_glossary_case_excerpts(
            glossary_terms, docs_data, load_glossary_cases())
        for group in glossary_view["groups"]:
            for term in group["terms"]:
                emit("glossary_term.html", page(f"glossary/{term['id']}/"),
                    page_title=glossary_term_page_title(term, tr),
                    nav_active="glossary",
                    description=glossary_term_description(term, tr),
                    json_ld=build_glossary_term_json_ld(term, tr=tr),
                    term=term,
                    case_excerpts=case_excerpts.get(term["id"]) or [],
                )
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
        # 분류 슬러그 → 그 분류의 조합(기관) 목록. 분류 페이지가 진입 간선을 걸 때와
        # 조합 페이지를 쓸 때 같은 원천을 본다(두 곳에서 따로 세면 갈라진다).
        combos_by_category: dict[str, list[dict[str, Any]]] = {}
        for combo in ((facets.get("combos") or {}).get("items") or []):
            combos_by_category.setdefault(combo["category_slug"], []).append(combo)
        for axis in facets.get("axes") or []:
            axis_key = axis["axis"]
            meta = facet_meta(axis_key, tr)            # 모르는 축 = KeyError(조용한 누락 금지)
            items = [build_facet_item_view(it, doc_slugs) for it in axis.get("items") or []]
            siblings = [{"slug": it["slug"], "label_ko": it["label_ko"]} for it in items]

            axis_page = page(f"findings/{meta['path']}/")
            emit("findings_facet_index.html", axis_page,
                page_title=tr("{title} · GRM", title=meta["title"]),
                nav_active="findings",
                description=meta["index_lede"],
                axis=meta, items=items, excluded=axis.get("excluded") or [],
                # [B2] 화면 빵부스러기와 동일한 순서·이름(findings_facet_index.html 참조).
                json_ld=axis_page.breadcrumb_json_ld([
                    (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"), (meta["title"], "")]),
                # 문서 목록 입구 — 문서 정본이 없으면 링크를 만들지 않는다(없는 페이지로
                # 보내는 링크는 무링크보다 나쁘다).
                doc_index_total=((docs_data or {}).get("totals") or {}).get("documents", 0)
                if render_doc_pages else 0,
            )
            # 축 색인의 갱신일 = 그 축 항목들이 실은 가장 최근 사례의 공개일.
            axis_mod = max((s.get("published_date") or ""
                            for it in items for s in it.get("samples") or []),
                           default="")
            facet_paths.append((axis_page.site_path, axis_mod))

            for item in items:
                # 조합 페이지(분류 × 기관)로 가는 진입 간선 — 분류 축에서만, 그리고 그
                # 분류에 실제로 만들어진 조합에만 건다(없는 페이지로 보내는 링크 금지).
                narrow = [
                    {"href": f"findings/{meta['path']}/{item['slug']}/{c['slug']}/",
                     "label": c["agency_label_ko"], "findings": c["findings"]}
                    for c in combos_by_category.get(item["slug"], [])
                ] if axis_key == "category" else []
                item_page = page(f"findings/{meta['path']}/{item['slug']}/")
                item_label = tr(item["label_ko"])
                emit("findings_facet.html", item_page,
                    page_title=tr("{label} {suffix} · GRM", label=item_label,
                                  suffix=meta["headline_suffix"]),
                    nav_active="findings",
                    description=facet_description(axis_key, item, agency_labels, tr),
                    axis=meta, item=item, siblings=siblings,
                    agency_labels=agency_labels, measured_on=measured_on,
                    crumb_mid=[{"href": f"findings/{meta['path']}/",
                                "label": meta["title"]}],
                    crumb_last=item_label,
                    # [B2] 위 crumb_mid/crumb_last 와 **같은 값**으로 만든다.
                    json_ld=item_page.breadcrumb_json_ld([
                        (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"),
                        (meta["title"], f"findings/{meta['path']}/"),
                        (item_label, "")]),
                    headline=tr("{label} {suffix}", label=item_label,
                                suffix=meta["headline_suffix"]),
                    lede_prefix=meta["lede_prefix"],
                    # 값 인코딩은 템플릿의 `| urlencode` 가 한다 — 렌더러는 urllib 을
                    # import 할 수 없다(순수성 가드가 비결정/네트워크 모듈의 **문 자체**를
                    # 닫아 둔다). 조립만 여기서 하고 인코딩 규칙은 옮기지 않는다.
                    cta_params=[(meta["query_key"], item["key"])],
                    cta_label=tr("{label} 지적사항 전체 보기", label=item_label),
                    sibling_title=meta["sibling_title"],
                    sibling_base=f"findings/{meta['path']}/",
                    narrow_links=narrow,
                )
                item_mod = max((s.get("published_date") or ""
                                for s in item.get("samples") or []), default="")
                facet_paths.append((item_page.site_path, item_mod))

        # ── [검색 유입 2차] 분류 × 기관 조합 페이지 ─────────────────────────────
        # 사람들이 치는 말은 주제 하나가 아니라 "기관 + 주제"다("FDA 무균 지적사항").
        # 부모는 언제나 분류 페이지 하나뿐이라 URL 도 그 밑에 둔다 — 위 루프가 그
        # 부모에 진입 간선을 이미 걸었으므로 이 페이지들은 고립되지 않는다.
        cat_meta = facet_meta("category", tr)
        for cat_slug, combo_items in combos_by_category.items():
            sibs = [{"slug": c["slug"], "label_ko": c["agency_label_ko"]}
                    for c in combo_items]
            for combo in combo_items:
                view = build_facet_item_view(combo, doc_slugs)
                # by_agency 막대는 조합에선 뜻이 없다(기관이 하나뿐이라 100% 한 줄) —
                # 데이터에 아예 넣지 않아 템플릿의 {% if %} 가 섹션을 지운다.
                view["by_agency"] = []
                agency_label = tr(combo["agency_label_ko"])
                category_label = tr(combo["category_label_ko"])
                label = tr("{agency} {category}", agency=agency_label, category=category_label)
                base = f"findings/{cat_meta['path']}/{cat_slug}/"
                combo_page = page(f"{base}{combo['slug']}/")
                emit("findings_facet.html", combo_page,
                    page_title=tr("{label} 지적사항 · GRM", label=label),
                    nav_active="findings",
                    description=combo_description(combo, tr),
                    axis=cat_meta, item=view, siblings=sibs,
                    agency_labels=agency_labels, measured_on=measured_on,
                    crumb_mid=[{"href": f"findings/{cat_meta['path']}/",
                                "label": cat_meta["title"]},
                               {"href": base, "label": category_label}],
                    crumb_last=agency_label,
                    # [B2] 위 crumb_mid/crumb_last 와 **같은 값**으로 만든다.
                    json_ld=combo_page.breadcrumb_json_ld([
                        (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"),
                        (cat_meta["title"], f"findings/{cat_meta['path']}/"),
                        (category_label, base),
                        (agency_label, "")]),
                    headline=tr("{label} 지적사항", label=label),
                    # 기관명에 조사를 붙이지 않는다 — 한국어 조사는 앞말의 받침에
                    # 따라 갈리는데 기관명은 영문 약어가 섞여 있어(FDA·EMA·MHRA)
                    # 규칙이 성립하지 않는다. 이름은 제목이 이미 말하고 있다.
                    lede_prefix=tr("이 기관이 이 분류로"),
                    cta_params=[(cat_meta["query_key"], combo["category_key"]),
                                ("agency", combo["agency_key"])],
                    cta_label=tr("{label} 지적사항 전체 보기", label=label),
                    sibling_title=tr("다른 기관 보기"),
                    sibling_base=base,
                    narrow_links=[],
                )
                combo_mod = max((s.get("published_date") or ""
                                 for s in view.get("samples") or []), default="")
                facet_paths.append((combo_page.site_path, combo_mod))

    # [조항 페이지] 21 CFR 조항별 지적사례 — 색인 1장 + 조항 34장.
    # 검색 실측(2026-09-03): `21 CFR 211.192` 류 쿼리는 결과가 전부 영문 법령 사이트라
    # 국문 해설이 공백이다. 데이터는 커밋 정본 셋(findings_docs·library/cfr·glossary)에서만
    # 나오므로 facets 유무와 무관하게 만들어진다.
    if clause_views:
        clause_agency_labels = (docs_data or {}).get("agency_labels") or (
            facets.get("agency_labels") if facets else {}) or {}
        clause_index = page("findings/clause/")
        emit("findings_clause_index.html", clause_index,
            page_title=tr("21 CFR 조항별 지적사례 · GRM"),
            nav_active="findings",
            description=tr("미국 GMP 규정(21 CFR Part 210·211) 조항별로 실제 지적사항을 "
                           "우리말로 모았습니다. 조항 {n}개.", n=len(clause_views)),
            json_ld=clause_index.breadcrumb_json_ld([
                (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"), (tr("조항별"), "")]),
            clauses=clause_views, min_documents=CLAUSE_MIN_DOCUMENTS,
        )
        index_mod = max((s.get("published_date") or ""
                         for c in clause_views for s in c["samples"]), default="")
        facet_paths.append((clause_index.site_path, index_mod))

        # 형제 목록은 전 조항 공통(같은 값 재사용 — 페이지마다 다시 만들지 않는다).
        sibs = [{"slug": c["slug"], "code": c["code"]} for c in clause_views]
        for clause in clause_views:
            clause_page = page(f"findings/clause/{clause['slug']}/")
            emit("findings_clause.html", clause_page,
                page_title=tr("{code} 지적사례 · GRM", code=clause["code"]),
                nav_active="findings",
                description=clause_description(clause, tr),
                json_ld=clause_page.breadcrumb_json_ld([
                    (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"),
                    (tr("조항별"), "findings/clause/"), (clause["code"], "")]),
                clause=clause, siblings=sibs, agency_labels=clause_agency_labels,
            )
            clause_mod = max((s.get("published_date") or ""
                              for s in clause["samples"]), default="")
            facet_paths.append((clause_page.site_path, clause_mod))

    # [검색 유입] 문서 단위 페이지 — 실사 보고서 1건 = 1페이지(임계 3 + 소스 소거 면제).
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
        doc_titles = build_doc_page_titles(documents, tr)

        # [실사관 프로파일 문서목록 멤버십 2026-08-31] 실사관 프로파일 페이지(런타임
        # RPC 화면)가 "이 실사관이 서명한 문서" 목록에서 정적 문서 페이지(findings/doc/
        # {slug}/)로 링크하려면, 그 페이지가 **실제로 존재하는지** 먼저 알아야 한다 —
        # 정적 페이지는 두께 임계(apply_thickness_gate, 문서당 지적 3건 등)를 넘긴
        # 문서만 있어서, 확인 없이 링크하면 그 중 일부가 404 다(실측 약 16%). `documents`
        # 는 이미 그 게이트를 통과한 것들이므로, inspector_names 를 가진 문서의
        # document_id 를 사전순으로 나열하면 그대로 "존재 증명" 멤버십 집합이 된다.
        # ★렌더 스위치(render_doc_pages)와 무관하게 항상 쓴다 — sitemap·목록·색인과 같은
        # 이유다: 이 값은 documents 데이터에서만 파생하고 개별 HTML 3천 장을 실제로
        # 찍어내는 비용(약 27초)과 무관하므로, 테스트 빌드에서 스위치로 꺼도 이 파일은
        # 프로덕션과 같아야 한다(꺼진 채로 빠지면 멤버십 검사가 프로덕션과 달라진다).
        inspector_doc_ids = sorted(
            d["document_id"] for d in documents if d.get("inspector_names"))
        _write_json(dist_assets / "inspector-doc-pages.json", {
            "schema": "grm-inspector-doc-pages/v1",
            "document_ids": inspector_doc_ids,
        })
        written.append("assets/inspector-doc-pages.json")

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

        # ── [B1 색인 표면] 업체별 정적 페이지 ────────────────────────────────
        # 사람들이 실제로 검색창에 치는 말은 "업체명 + 483" 인데, 그 질문에 답하는 화면
        # (/findings/firm/?key=)은 **실행 시점에 데이터를 불러오는 페이지**라 검색엔진에
        # 잡히지 않는다. 반면 문서 상세는 이미 업체 실명으로 색인되고 있으므로, 같은
        # 정본에서 업체 단위 정적 페이지를 내는 것은 공개 범위를 넓히는 게 아니라
        # **이미 공개된 것에 닿는 길을 내는 것**이다(실사관 면은 사람에 대한 페이지라
        # 정책이 달라 noindex 유지 — 여기 대상이 아니다).
        #
        # 문서 2건 이상만 만든다. 1건짜리는 그 문서 상세와 내용이 사실상 겹쳐(얇은
        # 중복 페이지) 색인에 해롭다 — 2건부터는 문서 상세 어디에도 없는 것(여러 실사에
        # 걸친 구성·기간)이 생긴다. 임계는 조합 페이지의 복제본 방벽과 같은 취지다.
        FIRM_PAGE_MIN_DOCS = 2
        firm_pages: list[dict[str, Any]] = []
        firm_page_slug_by_key: dict[str, str] = {}
        for key in sorted(by_firm):
            group = by_firm[key]
            if len(group) < FIRM_PAGE_MIN_DOCS:
                continue
            slug = _firm_slug(key)
            if not slug or slug in firm_page_slug_by_key.values():
                continue  # 슬러그를 못 만들거나 충돌하면 페이지를 만들지 않는다
            rows = sorted(group, key=lambda x: (x["published_date"], x["slug"]),
                          reverse=True)
            cat_counts: dict[str, int] = {}
            for d in rows:
                for label in (d.get("categories") or []):
                    cat_counts[label] = cat_counts.get(label, 0) + 1
            firm_page_slug_by_key[key] = slug
            firm_pages.append({
                "key": key, "slug": slug,
                # 표시명은 그 업체의 문서에서 가장 많이 쓰인 표기(동률이면 긴 쪽) —
                # firm_key 는 정규화값이라 화면에 그대로 쓰면 "(주)" 가 사라진 형태가 된다.
                "name": max(
                    sorted({d.get("firm_name", "") for d in rows if d.get("firm_name")}),
                    key=lambda n: (sum(1 for d in rows if d.get("firm_name") == n), len(n)),
                    default=key),
                "documents": rows,
                "doc_count": len(rows),
                "finding_count": sum(len(d.get("findings") or []) for d in rows),
                "first_seen": min(d["published_date"] for d in rows),
                "last_seen": max(d["published_date"] for d in rows),
                "agencies": sorted({d["agency"] for d in rows}),
                "categories": [c for c, _ in sorted(cat_counts.items(),
                                                    key=lambda kv: (-kv[1], kv[0]))],
            })

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
            "label_ko": tr(doc_agency_labels[a]) if a in doc_agency_labels else a,
            "total": sum(len(v) for (aa, _), v in by_ay.items() if aa == a),
            "years": [{"year": y, "count": len(by_ay[(a, y)])}
                      for y in sorted({yy for aa, yy in by_ay if aa == a}, reverse=True)],
        } for a in doc_agencies]

        newest_doc = max((d["published_date"] for d in documents), default="")
        docs_index = page("findings/docs/")
        facet_paths.append((docs_index.site_path, newest_doc))
        for g in index_groups:
            for y in g["years"]:
                bucket_mod = max(
                    (d["published_date"]
                     for d in by_ay[(g["slug"].upper(), y["year"])]), default="")
                facet_paths.append(
                    (page(f"findings/docs/{g['slug']}/{y['year']}/").site_path, bucket_mod))

        # ★목록·색인 21장은 **스위치와 무관하게 항상** 낸다. 비싼 것은 개별 문서 3,202장뿐
        # (한 번에 ~27초)이고, 목록을 함께 끄면 sitemap·진입 카드·404 페이지가 가리키는
        # 곳이 테스트 빌드에서만 없어져 **링크 무결성 검사가 프로덕션과 다른 것을 보게 된다**
        # (실제로 404 링크 검사가 이걸 잡았다).
        docs_total = f"{docs_data['totals']['documents']:,}"
        emit("findings_doc_list.html", docs_index,
             page_title=tr("문서로 찾기 · GRM"),
             nav_active="findings",
             # [P1.5 잔재 수리 2026-08-27] "지적 3건 이상" 임계 문구는 면제
             # 규칙 도입으로 더 이상 사실이 아니다 — 화면 고지는 고쳤는데
             # meta description 이 낡은 채 남아 있었다(검색 스니펫이 먼저 거짓말).
             description=tr("규제기관이 공개한 실사 문서 {n}건을 기관·연도로"
                            " 정리했습니다. FDA 483·Warning Letter·캐나다 실사·"
                            "식약처·EU/영국 GMP 비준수 — 문서를 열면 지적 전체를"
                            " 우리말로 볼 수 있습니다.", n=docs_total),
             # [B2] 화면 빵부스러기와 동일(findings_doc_list.html index 모드).
             json_ld=docs_index.breadcrumb_json_ld([
                 (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"), (tr("문서로 찾기"), "")]),
             mode="index", heading=tr("문서로 찾기"),
             lede=tr("규제기관이 공개한 실사 문서 <b>{n}</b>건을 기관과 연도로"
                     " 묶었습니다. 문서 하나를 열면 그 실사에서 나온 지적을 모두"
                     " 우리말로 보실 수 있습니다.", n=docs_total),
             groups=index_groups)

        for g in index_groups:
            for y in g["years"]:
                bucket = by_ay[(g["slug"].upper(), y["year"])]
                list_page = page(f"findings/docs/{g['slug']}/{y['year']}/")
                verb = date_axis_verb(bucket, tr)
                heading = tr("{agency} · {year}년", agency=g["label_ko"], year=y["year"])
                emit("findings_doc_list.html", list_page,
                     page_title=tr("{agency} {year}년 실사 문서 · GRM",
                                   agency=g["label_ko"], year=y["year"]),
                     nav_active="findings",
                     description=tr("{agency}가 {year}년에 {verb} 실사 문서 {n}건의"
                                    " 지적사항을 우리말로 정리했습니다.",
                                    agency=g["label_ko"], year=y["year"], verb=verb,
                                    n=f"{y['count']:,}"),
                     # [B2] 화면 빵부스러기와 동일(list 모드는 '문서로 찾기'가 낀다).
                     json_ld=list_page.breadcrumb_json_ld([
                         (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"),
                         (tr("문서로 찾기"), "findings/docs/"),
                         (heading, "")]),
                     mode="list",
                     heading=heading,
                     lede=tr("{agency}가 {year}년에 {verb} 실사 문서 <b>{n}</b>건입니다."
                             " 문서를 열면 그 실사의 지적을 모두 보실 수 있습니다.",
                             agency=g["label_ko"], year=y["year"], verb=verb,
                             n=f"{y['count']:,}"),
                     documents=bucket, agency_slug=g["slug"],
                     agency_label=g["label_ko"], year=y["year"],
                     sibling_years=g["years"])

        for doc in documents:
            # sitemap 은 **데이터에서** 파생한다 — 렌더를 껐다고 URL 이 빠지면 테스트가 보는
            # sitemap 과 프로덕션 sitemap 이 달라져 골든 대조가 의미를 잃는다.
            doc_page = page(f"findings/doc/{doc['slug']}/")
            facet_paths.append((doc_page.site_path, doc["published_date"]))
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
            # 형제 링크의 날짜는 **그 링크가 여는 페이지의 제목과 같은 날짜**여야 한다 —
            # 목록에서 "2024-01-17"을 보고 눌렀는데 도착한 문서 제목이 "(2015-07-10)"이면
            # 같은 문서인지 의심하게 된다. 그래서 doc_display_date 를 그대로 쓴다.
            # (문서 목록 페이지 `/findings/docs/{기관}/{연도}/` 는 반대다 — 거기는 공개
            #  연도로 묶인 축이라 공개일이 맞고, 페이지 문구도 "…년에 공개한"이다.)
            same_firm = [{"slug": s["slug"], "published_date": doc_display_date(s),
                          "agency": s["agency"], "count": len(s["findings"])}
                         for s in siblings[:6]]
            # 용어 링크는 렌더 직전에 본문 조각별로 끼운다. `used` 가 페이지 단위라 같은
            # 용어가 여러 지적에 나와도 첫 곳 하나만 링크된다.
            selected = (select_doc_term_links(doc, term_link_index, term_doc_freq)
                        if term_link_index else [])
            linked_used: set[str] = set()
            finding_bodies = [
                link_terms_in_text(f.get("text_ko") or "", selected, doc_page.rel_root,
                                   linked_used)
                for f in doc.get("findings") or []
            ]
            emit("findings_doc.html", doc_page,
                page_title=tr("{title} · GRM", title=doc_titles[doc["slug"]]),
                nav_active="findings",
                description=doc_page_description(doc, doc_agency_labels, tr),
                # [B2] 화면 빵부스러기와 동일(findings_doc.html: 홈 › 지적사항 검색 ›
                # 규제기관별 › 업체명). 이 4천 장이 SERP 에서 날 슬러그를 보이던 자리다.
                json_ld=doc_page.breadcrumb_json_ld([
                    (tr("홈"), "/"), (tr("지적사항 검색"), "findings/"),
                    (tr("규제기관별"), "findings/agency/"), (doc["firm_name"], "")]),
                doc=doc, agency_labels=doc_agency_labels,
                source_label=doc_source_label(doc),
                # [실사관 표기 · 정적 문서 페이지 2026-08-31] 최대 3명 + "외 N명"
                # 조립된 표시 문자열(빈 값이면 템플릿이 행 자체를 렌더하지 않는다).
                inspector_line=doc_inspector_line(doc, tr),
                related_categories=related, same_firm=same_firm,
                finding_bodies=finding_bodies,
                # [B1] 이 업체의 정적 페이지가 있으면 그리로(색인 가능·팔로우),
                # 없으면 종전대로 조회 화면으로(nofollow) — 템플릿이 가른다.
                firm_page_slug=firm_page_slug_by_key.get(doc.get("firm_key") or ""),
            )

        # [B1] 업체 페이지. sitemap 은 **데이터에서** 파생하고(렌더 스위치와 무관),
        # 실제 렌더는 문서 페이지와 같은 스위치를 탄다 — 이 페이지로 들어오는 링크가
        # 문서 상세에 있으므로, 문서 페이지를 끈 빌드에서 이것만 쓰면 고아가 된다.
        for fp in firm_pages:
            facet_paths.append((page(f"findings/firm/{fp['slug']}/").site_path,
                                fp["last_seen"]))
        if render_doc_pages:
            for fp in firm_pages:
                firm_page = page(f"findings/firm/{fp['slug']}/")
                emit("findings_firm_page.html", firm_page,
                     page_title=tr("{name} 지적사항 이력 · GRM", name=fp["name"]),
                     nav_active="findings",
                     description=tr(
                         "{name}의 공개 실사 문서 {d}건에서 확인된 지적 {n}건을 우리말로"
                         " 정리했습니다({y1}~{y2}).",
                         name=fp["name"], d=f"{fp['doc_count']:,}",
                         n=f"{fp['finding_count']:,}",
                         y1=fp["first_seen"][:4], y2=fp["last_seen"][:4]),
                     # [B2] 화면 빵부스러기와 동일(findings_firm_page.html).
                     json_ld=firm_page.breadcrumb_json_ld([
                         (tr("홈"), "/"), (tr("지적사항"), "findings/"),
                         (tr("문서로 찾기"), "findings/docs/"), (fp["name"], "")]),
                     firm=fp, agency_labels=doc_agency_labels,
                     measured_on=docs_data.get("measured_on", ""),
                     min_findings=docs_data.get("min_findings"))

    # 주간 퀴즈(트랙 C) — quiz_bank.json(정본)의 전 문항을 결정론 embed. "이번 주" 선택은
    # 렌더러가 하지 않고(now() 금지) 클라이언트 assets/quiz.js 가 ISO 주차 키로 결정론 회전
    # 선택한다(같은 주 = 전 직원 동일 세트). 파일 부재 시 조용히 생략.
    quiz_bank = load_quiz_bank()
    if quiz_bank:
        emit("quiz.html", page("quiz/"),
            page_title=tr("주간 퀴즈 · GRM"),
            nav_active="guide",
            description=tr(QUIZ_DESCRIPTION),
            quiz=build_quiz_view(quiz_bank, tr),
        )

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
        # [P2 관심 범위 · 067] 선택지 어휘는 **렌더가 정본에서 심는다**. reactions.js 는
        # 전 페이지에 실리는 전역 스크립트라 20개짜리 분류 사전을 복제하면 사이트 전체가
        # 무거워지고, 사본이 하나 더 늘면 파리티 검사도 하나 더 필요해진다(firm.js·
        # inspector.js 가 이미 그 값을 치르고 있다). 여기서는 facets 정본을 그대로 심어
        # JS 가 읽게 한다 — 사본 0·전역 무게 0·정본과 어긋날 자리 0.
        interest_vocab = []
        for _axis in (facets.get("axes") if facets else []) or []:
            for _item in _axis.get("items") or []:
                interest_vocab.append({"kind": _axis["axis"],
                                       "value": _item["key"],
                                       "label": _item["label_ko"]})
        # 개인화 페이지 — canonical 을 템플릿에 넘기지 않던 종전 계약 그대로(비색인).
        emit("me.html", page("me/"),
            page_title=tr("마이페이지 · GRM"),
            nav_active="me",
            canonical="",
            interest_vocab=interest_vocab,
        )
        emit("admin.html", page("admin/"),
            page_title="Admin · GRM",
            nav_active="admin",
            description="",
            canonical="",
            json_ld="",
            newsletter_form_action="",
            reactions_enabled=False,
        )

    # 브리프 상세(주차별).
    brief_tmpl = env.get_template("brief.html")
    for b in briefs:
        pub = b["brief"].get("publish_date", "")
        issue_no = issue_no_by_date[pub]
        renderable = [c for c in (b.get("cards") or []) if _is_renderable(c)]
        cards_sorted = sorted(renderable,
                              key=lambda c: (c.get("render_order") is None,
                                             c.get("render_order")))
        card_views = [_card_view(c, tr) for c in cards_sorted]
        _annotate_toc_distinguishers(card_views)        # P1-1: 동명 카드 목차 구분자
        sections = _build_sections(card_views)
        ctx = _brief_context(b, issue_no, tr)
        # [브리프 자료실 스트립] 이 브리프가 커버하는 주간(window)에 실제로 든 자료실
        # 변경만 싣는다 — window 파싱이 깨지면(형식 밖 표시 문자열 등) 조용히 생략한다
        # (깨진 스트립보다 무렌더가 낫다. 빈 상자 금지 원칙과 같은 결).
        _win = _parse_brief_window(ctx.get("window", ""))
        lib_update_week = (
            build_library_update_window_view(lib_entries, catalogs, _win[0], _win[1])
            if _win else None
        )
        brief_page = page(f"briefs/{pub}/")
        emit("brief.html", brief_page,
            page_title=tr("{date} 규제뉴스 · GRM", date=ctx["title_dateform"]),
            nav_active="detail",
            description=_brief_description(b["brief"], tr),
            brief=ctx,
            sections=sections,
            lib_update_week=lib_update_week,
        )
        # [성장 3차] 링크드인/커뮤니티 공유 초안 — tldr(큐레이션된 핵심)+절대 URL 을 고정
        # 경로(briefs/{pub}/share.txt)로 낸다. 운영 루틴: 발행 후 이 URL 을 열어 복사·
        # 다듬어 게시(주 5분). 내용이 공개 브리프 요약뿐이라 공개 무해·sitemap 비등록.
        # tldr 이 비면 불릿 없이 헤더+링크만 남는다(파일 존재는 항상 — 경로 예측 가능성).
        share_lines = [tr("[GRM 주간 규제뉴스 · {date}]", date=ctx["title_dateform"]), ""]
        share_lines += [f"· {t}" for t in (ctx.get("tldr") or [])]
        share_lines += ["", tr("이번 주 전체 보기: {url}", url=brief_page.canonical), "",
                        tr("#GMP #제약규제 #품질관리 #RegulatoryIntelligence")]
        share_file = brief_page.file("share.txt")
        _write(out_dir / share_file, "\n".join(share_lines) + "\n")
        written.append(share_file)

    # ── [다국어 3단계 2026-09-04] 영어 트리 `/en/` ────────────────────────────────
    # 한국어 트리를 다 그린 뒤, **본문이 영어로 성립하는 면만** 같은 템플릿으로 한 번 더
    # 그린다(EN_TREE_STATIC 주석에 무엇을 왜 넣고 뺐는지 적어 두었다). 페이지 주소는 같은
    # PagePath 공장에서, 문구는 같은 사전에서 나오므로 두 트리가 갈라질 자리가 없다.
    #
    # 화면 스크립트용 사전(`assets/i18n-en.js`)은 자산 복사 직후에 이미 냈다(위 참조).
    en_tr = _en_translator
    en_env = _make_env("en", en_tr)
    for _k, _v in env.globals.items():          # 자산 해시·인증 메타·반응 게이트 공유
        en_env.globals.setdefault(_k, _v)
    en_env.globals["_"] = en_tr.template_gettext()
    en_env.globals["og_locale"] = LANG_OG_LOCALE["en"]
    en_page, _en_render, en_emit = _make_tree("en", en_env)

    # 자료실은 카탈로그 카피(제목·소개·유형 라벨)가 사전을 타므로 영어로 다시 읽는다.
    en_catalogs = load_library(tr=en_tr)
    en_library_updates = load_library_updates(en_catalogs)

    en_emit("landing_en.html", en_page(""),
        # ★`en_tr(...)` 는 추출기의 함수 목록(tr/N_)에 없어 키로 잡히지 않는다 — 영어 전용
        #   문구는 `N_()` 로 등록해야 카탈로그 결손 검사가 본다(안 그러면 렌더 시점에야 터진다).
        page_title=en_tr(N_("GRM · 글로벌 규제 인텔리전스 · 영문판")),
        nav_active="home",
        description=en_tr(SITE_DESCRIPTION),
        json_ld=build_json_ld(),
        findings_zone=findings_zone,
        library={"catalog_count": len(en_catalogs),
                 "item_count": sum(v["count"] for v in en_catalogs)},
    )
    en_emit("findings.html", en_page("findings/"),
        zone_totals=findings_zone,
        page_title=en_tr("지적사항 검색 · GRM"),
        nav_active="findings",
        description=en_tr(FINDINGS_DESCRIPTION),
    )
    en_emit("trends.html", en_page("findings/trends/"),
        page_title=en_tr("규제 지적사항 트렌드 · GRM"),
        nav_active="trends", seg_active="trends",
        description=en_tr(TRENDS_DESCRIPTION),
    )
    en_emit("inspections.html", en_page("findings/inspections/"),
        page_title=en_tr("FDA 실사 결과 · GRM"),
        nav_active="trends", seg_active="inspections",
        description=en_tr(INSPECTIONS_DESCRIPTION),
    )
    en_emit("coverage.html", en_page("findings/coverage/"),
        page_title=en_tr("데이터 현황 · GRM"),
        nav_active="trends", seg_active="",
        description=en_tr(COVERAGE_DESCRIPTION),
    )
    en_emit("checklist.html", en_page("findings/checklist/"),
        page_title=en_tr("자가점검 체크리스트 · GRM"),
        nav_active="trends",
        description=en_tr(CHECKLIST_DESCRIPTION),
    )
    en_emit("firm.html", en_page("findings/firm/"),
        page_title=en_tr("업체 프로파일 · GRM"),
        nav_active="findings",
        description=en_tr(FIRM_DESCRIPTION),
    )
    en_emit("inspector.html", en_page("findings/inspector/"),
        page_title=en_tr("실사관 프로파일 · GRM"),
        nav_active="findings",
        description=en_tr(INSPECTOR_DESCRIPTION),
    )
    if en_catalogs:
        en_emit("library.html", en_page("library/"),
            page_title=en_tr("자료실 · GRM"),
            nav_active="library",
            description=en_tr(LIBRARY_DESCRIPTION),
            catalogs=[{"href": f"{v['slug']}/index.html", "title": v["title"],
                       "count": v["count"], "unit": v["unit"], "blurb": v["blurb"],
                       "latest_published": v["latest_published"]} for v in en_catalogs],
            lib_update=en_library_updates["latest"],
        )
    for v in en_catalogs:
        en_emit("library_catalog.html", en_page(f"library/{v['slug']}/"),
            page_title=en_tr("{title} · GRM", title=v["title"]),
            nav_active="library",
            description=v["desc"],
            lib=v,
        )

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

    # 404 는 루트의 파일 하나(`/404.html`)라 홈과 같은 깊이로 그린다(rel_root = 루트).
    not_found = page("").file("404.html")
    _write(out_dir / not_found, render_page("404.html", page(""),
        page_title=tr("페이지를 찾을 수 없습니다 · GRM"),
        nav_active="",
        description=tr("찾으시는 페이지가 없습니다. 지적사항 검색·자료실·용어사전에서 다시 찾아보세요."),
        canonical="",
        # [다국어 3단계] 404 는 홈과 같은 `PagePath("")` 로 그리지만 **홈이 아니다** —
        # 언어판 짝(`/en/404.html`)이 없고(Cloudflare Pages 는 루트 `/404.html` 만 본다)
        # noindex 라 hreflang·언어 전환을 달지 않는다. 안 그러면 홈의 짝을 물려받는다.
        alternates=[], alt_other=None,
    ))
    written.append(not_found)

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
                             facet_paths=facet_paths, en_paths=en_paths))
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


class WlViolationValidationError(ValueError):
    """WL 위반항목 국문 병기 게이트 위반 — fail-closed. main() 전용(하단 참조)."""


def validate_wl_violations(cards_or_briefs: list[dict[str, Any]]) -> list[str]:
    """WL 위반항목 블록 발행 게이트 — `validate_483_observations` 의 WL 판(순수 함수).

    [2026-08-24] WL 은 템플릿이 영문 단독으로 조용히 degrade 해서, 국문 병기 결손이 5주를
    살았다(병합층 #670 신설 후에도 라우틴 미생산 — 08-24 발행분 5카드 14표제문 전건 영문).
    483 과 같은 2겹으로 끌어올린다: 조립 선행검출(`assemble_publish_brief._lint_wl_violation_ko`)
    + 여기 배포 fail-closed. 과거 발행분은 2026-08-24 소급 백필로 전건 병기 완료 상태라
    전 브리프 무조건 검사가 안전하다.

    검사 대상 = deterministic_detail.type == "wl_violations" 인 카드의 violations 각 건:
      1. statement_ko 비어있음 → MISSING_STATEMENT_KO
    """
    violations: list[str] = []

    def _check_card(card: dict[str, Any], brief_label: str) -> None:
        dd = card.get("deterministic_detail")
        if not isinstance(dd, dict) or dd.get("type") != "wl_violations":
            return
        card_id = card.get("id") or card.get("render_order") or "?"
        for v in (dd.get("violations") or []):
            num = v.get("number", "?")
            if not (isinstance(v.get("statement_ko"), str) and v.get("statement_ko").strip()):
                violations.append(
                    f"{brief_label} / card {card_id} / violation #{num}: MISSING_STATEMENT_KO")

    for item in cards_or_briefs:
        if "brief" in item and "cards" in item:
            label = item["brief"].get("publish_date") or item["brief"].get("run_date_kst") or "?"
            for card in (item.get("cards") or []):
                _check_card(card, label)
        else:
            _check_card(item, "?")

    return violations


class WhopirKoValidationError(ValueError):
    """WHOPIR 상세 국문 병기 게이트 위반 — fail-closed. main() 전용(하단 참조)."""


def validate_whopir_ko(cards_or_briefs: list[dict[str, Any]]) -> list[str]:
    """WHOPIR 상세 블록 발행 게이트 — `validate_wl_violations` 의 WHOPIR 판(순수 함수).

    [2026-08-25] WHOPIR 는 상세를 확보하고도 두 계층에서 조용히 빈약해질 수 있었다:
    수집 유실(#777 inmemory 가림·#784 섹션 회수율)과 **번역 미생산**(ncr_ko 는 프롬프트
    지시뿐, 빠지면 영문 단독으로 degrade). 483 `observations_ko`·WL `statement_ko` 와 같은
    2겹으로 끌어올린다: 조립 선행검출(`assemble_publish_brief._lint_whopir_ko` 게이트 7)
    + 여기 배포 fail-closed. 과거 발행분(07-27 #475 · 08-03 #779 · 08-24 #778/#784)은
    전건 병기 완료 실측(20카드 missing 0)이라 전 브리프 무조건 검사가 안전하다.

    검사 대상 = deterministic_detail.type == "whopir_report" 인 카드:
      1. outcome 이 있는데 outcome_ko 없음 → MISSING_OUTCOME_KO
      2. 섹션 text 가 있는데 text_ko 없음 → MISSING_SECTION_TEXT_KO
      3. 섹션 title 이 있는데 title_ko 없음 → MISSING_SECTION_TITLE_KO
    (reliance 인용 행은 기관명·일자 verbatim 설계라 국문 요구가 없다. 상세 블록이 없는
    링크 카드는 검사 대상이 아니다 — 구조화 실패의 정직한 degrade 는 허용.)
    """
    violations: list[str] = []

    def _ok(v: Any) -> bool:
        return isinstance(v, str) and bool(v.strip())

    def _check_card(card: dict[str, Any], brief_label: str) -> None:
        dd = card.get("deterministic_detail")
        if not isinstance(dd, dict) or dd.get("type") != "whopir_report":
            return
        card_id = card.get("id") or card.get("render_order") or "?"
        if _ok(dd.get("outcome")) and not _ok(dd.get("outcome_ko")):
            violations.append(f"{brief_label} / card {card_id} / outcome: MISSING_OUTCOME_KO")
        for s in (dd.get("sections") or []):
            no = s.get("no", "?")
            if _ok(s.get("text")) and not _ok(s.get("text_ko")):
                violations.append(
                    f"{brief_label} / card {card_id} / section #{no}: MISSING_SECTION_TEXT_KO")
            if _ok(s.get("title")) and not _ok(s.get("title_ko")):
                violations.append(
                    f"{brief_label} / card {card_id} / section #{no}: MISSING_SECTION_TITLE_KO")

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
    wl_violations = validate_wl_violations(briefs)
    if wl_violations:
        raise WlViolationValidationError(
            "WL 위반항목 국문 병기 게이트 위반 — 발행 차단(brief file / card id / "
            "violation number / fail code):\n" + "\n".join(f"  · {v}" for v in wl_violations)
        )
    whopir_missing = validate_whopir_ko(briefs)
    if whopir_missing:
        raise WhopirKoValidationError(
            "WHOPIR 상세 국문 병기 게이트 위반 — 발행 차단(brief file / card id / "
            "field / fail code):\n" + "\n".join(f"  · {v}" for v in whopir_missing)
        )


def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

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
