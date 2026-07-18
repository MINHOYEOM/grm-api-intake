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
from markupsafe import Markup, escape as _escape

# ── 경로(이 파일 기준 — cwd 무관) ──────────────────────────────────────────────
WEB_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEB_DIR.parent                 # grm_findings.py 등 저장소 루트 모듈
TEMPLATES_DIR = WEB_DIR / "templates"
PARTIALS_PARENT = WEB_DIR                  # "partials/card.html" 해석용
DATA_DIR = WEB_DIR / "data" / "briefs"
LIBRARY_DIR = WEB_DIR / "data" / "library"      # [자료실] ICH/MFDS 참조 카탈로그 커밋 데이터
GUIDE_FILE = WEB_DIR / "data" / "guide_content.md"   # [이용안내] 본문 마크다운(정본)
GLOSSARY_FILE = WEB_DIR / "data" / "glossary.json"   # [용어사전] GMP/규제 용어 커밋 데이터
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
        "deterministic_detail": card.get("deterministic_detail") or None,
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
    {"slug": "ich", "file": "ich.json", "unit": "토픽", "kick": "ICH · Guidelines",
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
    {"slug": "mfds", "file": "mfds.json", "unit": "건", "kick": "MFDS · Guidance",
     "title": "MFDS 지침·고시 아카이브",
     "blurb": "식약처가 공개한 지침·안내서·고시·행정예고. 주간 브리프에서 다룬 뒤에도 다시 찾아볼 수 있는 누적 목록.",
     "intro": "식약처(MFDS)가 공개한 지침·안내서·고시·행정예고를 발행일 순으로 모았습니다. 주간 브리프에서 한 번 다룬 문서도 이곳에서 다시 찾아볼 수 있습니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "식약처(MFDS) 지침·안내서·고시·행정예고 아카이브 — 제목·유형·발행일·공식 원문 링크.",
     "sort": "published_desc",
     "doc_type_labels": {"guidance-internal": "공무원 지침서", "guidance-industry": "민원인 안내서·지침",
                         "legislative-notice": "입법·행정예고", "notice-final": "고시 전문"}},
    {"slug": "eu-gmp", "file": "eu_gmp.json", "unit": "건", "kick": "EU · EudraLex Vol 4",
     "title": "EU GMP 기준서 (EudraLex Vol 4)",
     "blurb": "유럽연합 의약품 GMP 기준서. Part I·II·III 각 장과 부속서(Annex)를 구조 순서대로 정리.",
     "intro": "유럽연합 의약품 GMP 기준서(EudraLex Volume 4)의 문서 목록입니다. Part I(기본 요건)·Part II(원료의약품)·Part III(보조 문서)과 부속서(Annex)를 기준서 구조 순서대로 정리했으며, 각 문서의 공식 원문 PDF로 바로 연결됩니다. 법적 효력과 최신 개정본은 반드시 공식 원문에서 확인하세요.",
     "desc": "EU GMP 기준서(EudraLex Volume 4) 문서 목록 — Part I·II·III과 부속서(Annex), 공식 원문 PDF 링크."},
    {"slug": "pics", "file": "pics.json", "unit": "건", "kick": "PIC/S · GMP Guide",
     "title": "PIC/S GMP 가이드",
     "blurb": "의약품실사상호협력기구(PIC/S)의 GMP 가이드(PE 009)와 부속서·가이던스 문서 목록.",
     "intro": "의약품실사상호협력기구(PIC/S)가 공개한 GMP 가이드(PE 009) 각 부와 부속서, 관련 가이던스 문서를 발행일 순으로 정리했습니다. 식약처를 포함한 PIC/S 가입 규제기관의 실사 기준과 맞닿아 있는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "PIC/S GMP 가이드(PE 009)·부속서·가이던스 문서 목록 — 발행일·공식 원문 링크.",
     "sort": "published_desc"},
    {"slug": "who", "file": "who.json", "unit": "건", "kick": "WHO · TRS Annexes",
     "title": "WHO TRS 부속서 모음",
     "blurb": "WHO 전문가위원회 기술보고서(TRS) 부속서 중 GMP·품질 관련 문서 선별 목록.",
     "intro": "세계보건기구(WHO) 의약품 표준 전문가위원회 기술보고서(TRS)의 부속서 가운데 GMP·품질 관련 문서를 발행일 순으로 선별해 정리했습니다. WHO 사전적격성평가(PQ)나 국제 조달 요건을 다룰 때 기준이 되는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "WHO 기술보고서(TRS) 부속서 중 GMP·품질 문서 선별 목록 — 발행일·공식 원문 링크.",
     "sort": "published_desc"},
    {"slug": "fda-guidance", "file": "fda_guidance.json", "unit": "건", "kick": "FDA · Guidance",
     "title": "FDA 가이던스 문서",
     "blurb": "FDA가 공개한 의약품 GMP·품질 관련 가이던스 문서 선별 목록.",
     "intro": "미국 FDA가 공개한 의약품 GMP·품질 관련 가이던스 문서를 발행일 순으로 선별해 정리했습니다. 가이던스는 FDA의 현재 견해를 담은 권고 문서로, 법적 구속력이 있는 규정(CFR)과는 구분해 읽어야 합니다. 최신 개정 여부는 반드시 공식 원문에서 확인하세요.",
     "desc": "FDA 의약품 GMP·품질 가이던스 문서 선별 목록 — 발행일·유형·공식 원문 링크.",
     "sort": "published_desc"},
    {"slug": "ema", "file": "ema.json", "unit": "건", "kick": "EMA · Guidance",
     "title": "EMA GMP·품질 가이드라인",
     "blurb": "유럽의약품청(EMA)이 공개한 GMP 관련 절차·과학 가이드라인과 질의응답(Q&A) 선별 목록.",
     "intro": "유럽의약품청(EMA)이 공개한 GMP·품질 관련 문서를 발행일 순으로 선별해 정리했습니다. 실사 당국 품질 시스템, 품질 결함 보고·신속 경보 처리 등 규제 절차 가이드라인과 과학 가이드라인, 질의응답(Q&A)을 포함합니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "EMA GMP·품질 절차·과학 가이드라인과 질의응답(Q&A) 선별 목록 — 발행일·유형·공식 원문 링크.",
     "sort": "published_desc",
     "public_base": "https://www.ema.europa.eu/",
     "doc_type_labels": {"regulatory-procedural-guideline": "규제·절차 가이드라인",
                         "scientific-guideline": "과학 가이드라인",
                         "questions-and-answers": "질의응답(Q&A)"}},
    {"slug": "health-canada", "file": "health_canada.json", "unit": "건",
     "kick": "Health Canada · GMP",
     "title": "Health Canada GMP 가이드",
     "blurb": "캐나다 보건부(Health Canada)의 GMP 가이드(GUI 시리즈) 문서 목록.",
     "intro": "캐나다 보건부(Health Canada)가 공개한 GMP 가이드(GUI 시리즈) 문서를 발행일 순으로 정리했습니다. 의약품 GMP 실사와 시설 허가(Establishment Licence) 운영의 기준이 되는 문서들입니다. 법적 효력과 최신본은 반드시 공식 원문에서 확인하세요.",
     "desc": "Health Canada GMP 가이드(GUI 시리즈) 문서 목록 — 코드·발행일·공식 원문 링크.",
     "sort": "published_desc",
     "public_base": "https://www.canada.ca/en/health-canada.html",
     "doc_type_labels": {"guidance": "가이던스"}},
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
    for it in items:
        it["doc_type"] = labels.get(it["doc_type"], it["doc_type"])
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
    else:
        groups.append({"badge": "", "label": "", "label_en": "", "blurb": "",
                       "official_url": "", "items": items})
    dates = [it["published_date"] for it in items if it["published_date"]]
    meta = raw.get("meta", {})
    return {
        "slug": entry["slug"], "unit": entry["unit"], "kick": entry["kick"],
        "intro": entry["intro"], "blurb": entry["blurb"], "desc": entry["desc"],
        "title": entry.get("title") or meta.get("title", ""),
        "note": meta.get("note", ""),
        "public_base": _safe_url(entry.get("public_base") or meta.get("public_base", "")),
        "link_label": entry.get("link_label", ""),
        "count": len(items),
        "latest_published": max(dates) if dates else "",
        "grouped": bool(entry.get("groups_by_url")),
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


def _reg_ref_view(item: Any) -> dict[str, str] | None:
    """[용어사전 심화] reg_refs 항목 1건 → {"label","url"} 정규화(무변형·안전 URL 게이트만).

    문자열이면 label=문자열·url="". dict 면 label/url 을 각각 strip/_safe_url 게이트만
    거쳐 통과. label 이 빈 항목(빈 문자열·공백뿐)은 조용히 제외(None) — 호출부가 필터."""
    if isinstance(item, str):
        label = item.strip()
        return {"label": label, "url": ""} if label else None
    if isinstance(item, dict):
        label = (item.get("label") or "").strip()
        if not label:
            return None
        return {"label": label, "url": _safe_url(item.get("url") or "")}
    return None


def build_glossary_view(terms: list[dict[str, Any]]) -> dict[str, Any]:
    """용어 리스트 → 초성 그룹 뷰모델(무변형 — 값 재작성 0, 파생만).

    related 는 데이터 순서 그대로 유지하며 존재하는 id 만 term_ko 라벨과 함께 통과(고아
    참조는 조용히 제외). search 는 term_ko/term_en/easy_ko(+detail_ko 있을 때만)를 소문자
    결합(클라이언트 필터 입력값 — 표시 텍스트 무변형, 검색 대상 문자열만 별도 파생).
    detail_ko(실무 맥락 설명)·reg_refs(관련 조항 참조)는 병렬 작업자가 데이터에 추가할
    선택 필드 — 있으면 통과(reg_refs 는 _reg_ref_view 로 정규화), 없으면 빈 값이라
    기존 렌더와 byte 동일(search 에도 잉여 공백 미추가). 그룹·용어 정렬 결정론."""
    label_by_id = {t["id"]: t["term_ko"] for t in terms}

    def _term_view(t: dict[str, Any]) -> dict[str, Any]:
        related = [{"id": r, "term_ko": label_by_id[r]}
                   for r in (t.get("related") or []) if r in label_by_id]
        reg_refs = [v for v in (_reg_ref_view(r) for r in (t.get("reg_refs") or [])) if v]
        search_parts = [t["term_ko"], t["term_en"], t["easy_ko"]]
        detail_ko = t.get("detail_ko") or ""
        if detail_ko:
            search_parts.append(detail_ko)
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
    """robots.txt — 공개 페이지 허용 + sitemap 포인터. Admin 은 비색인."""
    lines = [
        "User-agent: *",
        "Allow: /",
    ]
    if disallow_admin:
        lines.append("Disallow: /admin/")
    lines += [
        "",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    return "\n".join(lines) + "\n"


def build_sitemap_xml(briefs: list[dict[str, Any]],
                      base_url: str = SITE_BASE_URL) -> str:
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
        # [주간 퀴즈] 트랙 C — 상설 학습 콘텐츠라 brief publish_date 와 분리(lastmod 생략).
        f"  <url><loc>{base_url}/quiz/</loc></url>",
    ]
    for pub in pubs:
        lines.append(
            f"  <url><loc>{base_url}/briefs/{pub}/</loc><lastmod>{pub}</lastmod></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


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
FINDINGS_DESCRIPTION = ("FDA 483 Observation · Warning Letter · 식약처 GMP 실태조사 "
                        "지적사항을 원문에서 자동 추출해 검색·필터.")
TRENDS_DESCRIPTION = ("FDA 483 · Warning Letter · 식약처 GMP 지적사항 전량 집계 통계 — "
                      "카테고리 순위·연도별 추이·업체 랭킹으로 보는 규제 지적 트렌드.")
FIRM_DESCRIPTION = ("특정 업체의 FDA 483·Warning Letter·식약처 GMP 지적사항 누적 이력을 "
                    "카테고리·연도별 추이·문서 이력으로 한 곳에서 확인하는 업체 프로파일.")
LIBRARY_DESCRIPTION = ("ICH 가이드라인 카탈로그와 식약처 지침·고시 아카이브를 한곳에 모은 "
                       "규제 자료실 — 공식 원문 링크와 함께 언제든 다시 찾아보세요.")
GUIDE_DESCRIPTION = ("GRM 이용 안내 — 월요일 브리프 3분 활용법, findings 검색 실전 예시, "
                     "자료실·용어사전·퀴즈 활용법과 자주 묻는 질문을 한곳에 정리했습니다.")
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
    return Environment(
        loader=FileSystemLoader([str(TEMPLATES_DIR), str(PARTIALS_PARENT)]),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 항상 LF/UTF-8 — OS 무관 결정론(Windows 의 \r\n 변환 차단).
    path.write_bytes(text.encode("utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    """결정론 JSON 쓰기 — dict 삽입순서 보존(sort_keys 미사용), ensure_ascii=False,
    indent=1(레포 data 관례), 항상 LF/UTF-8 + 후행개행. 같은 입력 → byte 동일."""
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def render_site(data_dir: Path = DATA_DIR, out_dir: Path = DIST_DIR,
                assets_dir: Path = ASSETS_DIR) -> dict[str, Any]:
    """data_dir → out_dir 정적 사이트 빌드. 산출 메타(쓴 파일 목록) 반환."""
    env = _make_env()
    # 소유권 인증 메타(env-param) — 전 페이지 <head> 공통(미설정 시 미출력). 아래 전역들
    # (SITE_BASE_URL·NEWSLETTER_FORM_ACTION·*_SITE_VERIFICATION)은 import 시점에 os.environ
    # 에서 캡처된다. 여기서는 그 모듈 전역을 render_site() 호출 시점에 env.globals 로 주입 —
    # 테스트가 모듈 속성(render.SITE_BASE_URL 등)을 monkeypatch 하면 반영되지만 os.environ 을
    # 호출 시점에 재조회하진 않는다(monkeypatch 계약 = 모듈 속성 기준, os.environ 아님).
    env.globals["google_site_verification"] = GOOGLE_SITE_VERIFICATION
    env.globals["naver_site_verification"] = NAVER_SITE_VERIFICATION
    env.globals["og_image"] = f"{SITE_BASE_URL}/assets/og-image.png"
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
    env.globals["glossaryjs_ver"] = _asset_ver("glossary.js")
    env.globals["quizjs_ver"] = _asset_ver("quiz.js")
    env.globals["growthjs_ver"] = _asset_ver("growth.js")
    env.globals["popularjs_ver"] = _asset_ver("popular.js")
    # 반응 계층 공개 설정 주입 — url 이 https(_safe_url 통과)이고 anon key 가 있을 때만 활성.
    # 미설정이면 base.html/card.html 의 {% if reactions_enabled %} 가 반응 블록 전체 생략.
    _supa_url = _safe_url(SUPABASE_URL)
    env.globals["reactions_enabled"] = bool(_supa_url and SUPABASE_ANON_KEY)
    env.globals["admin_enabled"] = env.globals["reactions_enabled"]
    env.globals["supabase_url"] = _supa_url
    env.globals["supabase_anon_key"] = SUPABASE_ANON_KEY
    env.globals["reactionsjs_ver"] = _asset_ver("reactions.js")
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
    )
    _write(out_dir / "archive" / "index.html", archive_html)
    written.append("archive/index.html")

    # 지적사항 검색(FIND-1 M3c) — 라이브 데이터(Supabase PostgREST)라 빌드시 목록을 고정할
    # 수 없다. 서버는 셸(로딩 상태)만 렌더 — env 미설정이면 findings.js 가 "준비 중" 안내로
    # 조용히 종료한다(cfg data 속성은 위 reactions_enabled 와 무관하게 항상 주입).
    findings_html = env.get_template("findings.html").render(
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

    # 자료실(트랙 C) — findings/trends 와 달리 라이브 데이터가 아니라 커밋 스냅샷
    # (web/data/library/*.json)을 결정론 렌더한다(주간 발행 게이트와 무관한 독립 정적
    # 섹션). 데이터 파일이 없으면 해당 카탈로그·허브 항목을 조용히 건너뛴다.
    catalogs = load_library()
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
    if glossary_terms:
        glossary_html = env.get_template("glossary.html").render(
            page_title="규제 용어사전 · GRM",
            rel_root="../",
            nav_active="glossary",
            latest_slug=latest_slug,
            description=GLOSSARY_DESCRIPTION,
            canonical=_abs_url("glossary/"),
            glossary=build_glossary_view(glossary_terms),
        )
        _write(out_dir / "glossary" / "index.html", glossary_html)
        written.append("glossary/index.html")

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

    # 내 스크랩(마이페이지) — 반응 계층 활성 시에만 생성(env-off=페이지 부재→골든 byte-diff 0).
    # 로그인 게이트·개인화라 sitemap/canonical 제외(비색인). 목록은 런타임에 reactions.js 가
    # Supabase 스크랩 + search-index.json 으로 렌더(정적 셸·콘텐츠 골든 불침범).
    if env.globals.get("reactions_enabled"):
        me_html = env.get_template("me.html").render(
            page_title="내 스크랩 · GRM",
            rel_root="../",
            nav_active="board",
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

    # 검색 노출(robots.txt + sitemap.xml) — 정적·결정론(입력 publish_date 파생).
    _write(out_dir / "robots.txt", build_robots_txt(
        disallow_admin=bool(env.globals.get("admin_enabled"))))
    written.append("robots.txt")
    _write(out_dir / "sitemap.xml", build_sitemap_xml(briefs))
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
    r"|\bSEE\s+REVERSE\b"
    r"|\bFORM\s+FDA\s*4"
    r"|\bInvestigator\b"
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
