#!/usr/bin/env python3
"""GRM ICH Collector — P1 (ICH 직접 모니터링).

ENABLE_ICH=true 또는 --sources ich 일 때 collect_intake.main() 에서 호출된다.

배경 (probe 2026-06-02, CODEX 재점검 반영):
  www.ich.org 는 JS 렌더링 SPA. admin.ich.org 는 Drupal 10 서버렌더링이지만,
  **가이드라인 페이지의 PDF·본문(아코디언 body)은 AJAX 로딩이라 서버 HTML에는 없다.**
  서버 HTML에 안정적으로 존재하는 것은 **섹션(아코디언) 제목 텍스트** 다.
  (예: "Q12 Lifecycle Management", "Q13 Continuous Manufacturing ...", "M7 Mutagenic Impurities")
  → 따라서 "문서 링크"가 아니라 **공식 섹션 제목/상태 텍스트 스냅샷**을 수집한다.

설계 역할 (사용자 합의):
  "확정 발행 자동 카드화" 가 아니라 **"공식 원문 기반 후보 감지 + Routine 검증"**.
  - admin.ich.org 페이지에서 ICH 토픽 섹션 제목을 스냅샷으로 수집(구조 비의존: 코드 패턴 기반).
  - document_id = hash(page_slug + 제목) → 새 토픽 등장/제목·범위 변경이 새 후보로 표면화.
  - Quality/Multidisciplinary 섹션 스냅샷은 정적 카탈로그이므로 Signal Tier 1 고정
    (Routine v16: 모니터링 로그/Skipped, 단독 카드화 금지).
  - official_url 은 사람이 보는 공식 공개 페이지(www.ich.org/page/<slug>), 스크래핑 출처는 admin.
  - 날짜를 단언하지 않는다 → date_iso="" (Notion Date 비움, Run Date 만 사용).
  - Step/Revision/PDF/마감일 등 AJAX 동적 정보는 Routine(WebFetch/WebSearch)이 확인한다.

수집/에러 정책:
  - Quality·Multidisciplinary 는 **고정 토픽 목록**이 항상 있어야 한다 → 0건이면 error(required).
  - Public Consultations 는 진행 건이 없을 수 있다 → 0건 정상(required=False).
  - 핵심(required) 페이지가 0건/실패면 collect_ich()가 error 를 반환한다(partial 로 묻지 않음).

1차 범위: Quality(Q-series) + Multidisciplinary(M7 변이원성 등 CMC 연관) + Public Consultations.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
import time
from datetime import date

from grm_common import http_get_html, log
from collect_intake import (
    IntakeItem,
    SOURCE_ICH,
    SRC_TYPE_OFFICIAL_PAGE,
)


TYPE_ICH_GUIDELINE = "ich-guideline"
TYPE_ICH_CONSULTATION = "ich-consultation"
LANGUAGE_EN = "EN"
REGION_ICH = "ICH (Global)"

ADMIN_BASE = "https://admin.ich.org/page/"     # 스크래핑 출처 (서버렌더)
PUBLIC_BASE = "https://www.ich.org/page/"      # 사람이 보는 공식 공개 URL

# (slug, type_or_class, required_nonzero)
ICH_PAGES: list[tuple[str, str, bool]] = [
    ("quality-guidelines", TYPE_ICH_GUIDELINE, True),
    ("multidisciplinary-guidelines", TYPE_ICH_GUIDELINE, True),
    ("public-consultations", TYPE_ICH_CONSULTATION, False),
]

HTTP_RETRIES = 3
PAGE_REQUEST_DELAY_SECONDS = 1.0
MAX_TITLE_CHARS = 240
MAX_LINE_CHARS = 140   # 이보다 길면 본문 문단으로 보고 제외(제목만 캡처)

# ICH 토픽 코드 토큰 (Q1.., M1..). 제목은 코드로 시작하거나 코드를 포함한다.
_CODE_RE = re.compile(r"\b([QM]\d{1,2}[A-Z]?)\b")
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")

# QA/CMC 관련성 신호 (ICH 한정)
_ICH_QA_TERMS = [
    "quality", "gmp", "good manufacturing", "impurit", "nitrosamin", "mutagenic",
    "stability", "validation", "analytical", "specification", "lifecycle",
    "continuous manufacturing", "drug substance", "dissolution", "bioequivalence",
    "biowaiver", "pharmaceutical", "risk management", "biopharmaceutics",
    "structured product quality",
    "q1", "q2", "q3", "q5", "q6", "q7", "q8", "q9", "q10", "q11", "q12", "q13", "q14",
    "m4", "m7", "m9", "m13", "m16",
]
# 고신호(Tier 3 후보)
_ICH_TIER3_TERMS = [
    "nitrosamin", "mutagenic", "m7", "step 4", "step 2b",
    "q1", "q12", "q13", "q14",
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _text_blocks(html_text: str) -> list[str]:
    """HTML → 블록 텍스트 라인 목록 (태그 비의존). 각 요소 텍스트가 한 줄이 되도록."""
    no_sc = _SCRIPT_STYLE_RE.sub(" ", html_text or "")
    text = _TAG_RE.sub("\n", no_sc)
    text = html_lib.unescape(text)
    lines = [_clean(ln) for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def _candidate_titles(blocks: list[str]) -> list[str]:
    """ICH 토픽 섹션 제목 후보 추출 (코드 토큰 포함 + 짧은 줄 + 설명어 존재)."""
    out: list[str] = []
    seen: set[str] = set()
    for line in blocks:
        if len(line) > MAX_LINE_CHARS:
            continue                       # 긴 줄 = 본문 문단 → 제외
        if not _CODE_RE.search(line):
            continue                       # ICH 코드 없는 줄(메뉴/로그인/intro) 제외
        if len(line.split()) < 2:
            continue                       # 코드만 있고 설명어 없으면 제외
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line[:MAX_TITLE_CHARS])
    return out


def _relevance(blob: str) -> str:
    b = blob.lower()
    return "Likely" if any(t in b for t in _ICH_QA_TERMS) else "Possible"


def _signal_tier(blob: str, relevance: str) -> str:
    b = blob.lower()
    if any(t in b for t in _ICH_TIER3_TERMS):
        return "Tier 3"
    return "Tier 2" if relevance == "Likely" else "Tier 1"


def _ich_tier(type_or_class: str, title: str, relevance: str) -> str:
    """ICH 스냅샷의 운영 tier.

    Guideline 페이지는 정적 토픽 카탈로그라 "존재" 신호일 뿐 "이번 변동"이 아니다.
    따라서 Tier 1 로 내려 Routine 카드화를 막고, 실제 Step/채택/협의 이벤트는
    WebSearch/보도자료 보강이 담당한다. Public Consultation 이 서버 HTML 에 실제 항목으로
    노출되는 경우에는 이벤트성 후보이므로 기존 신호 산정을 유지한다.
    """
    if type_or_class == TYPE_ICH_GUIDELINE:
        return "Tier 1"
    return _signal_tier(title, relevance)


def _document_id(slug: str, title: str) -> str:
    key = f"{slug}|{_clean(title).lower()}"
    return "ich-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _get_html(url: str, *, timeout: int = 30) -> str:
    return http_get_html(url, timeout=timeout, retries=HTTP_RETRIES, label="ICH")


def _collect_page(slug: str, type_or_class: str, required: bool,
                  run_date: date) -> tuple[list[IntakeItem], str | None]:
    """단일 ICH 페이지 섹션 제목 스냅샷 수집. (items, error_msg)."""
    admin_url = ADMIN_BASE + slug
    public_url = PUBLIC_BASE + slug
    log("INFO", f"ICH 수집: {type_or_class} ({admin_url})")
    time.sleep(PAGE_REQUEST_DELAY_SECONDS)
    try:
        html_text = _get_html(admin_url)
    except RuntimeError as e:
        return [], f"ICH 페이지 수집 실패({admin_url}): {e}"

    titles = _candidate_titles(_text_blocks(html_text))

    items: list[IntakeItem] = []
    for title in titles:
        relevance = _relevance(title)
        tier = _ich_tier(type_or_class, title, relevance)
        headline = (title if type_or_class == TYPE_ICH_GUIDELINE
                    else f"[Public Consultation] {title}")
        items.append(IntakeItem(
            source=SOURCE_ICH,
            document_id=_document_id(slug, title),
            date_iso="",                       # 날짜 미단언 → Run Date만 사용
            headline=headline[:MAX_TITLE_CHARS],
            official_url=public_url,            # 사람이 보는 공식 공개 페이지
            type_or_class=type_or_class,
            body=(f"ICH 공식 페이지 '{slug}' 에서 감지된 섹션/토픽 후보.\n"
                  f"섹션 제목: {title}\n"
                  f"※ Quality/Multidisciplinary 스냅샷은 정적 카탈로그이므로 "
                  f"단독 카드화하지 않는다. Step/Revision/PDF/의견마감일 등 변동 정보는 "
                  f"Routine WebSearch/공식 보도자료에서 최종 확인할 것."),
            api_query=admin_url,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier=tier,
            raw_payload={
                "source_page": admin_url,
                "public_page": public_url,
                "page_slug": slug,
                "section_title": title,
                "detection": (
                    "section-title snapshot; static guideline pages are Tier 1 "
                    "(Routine verification/event search required)"
                ),
            },
            source_url=admin_url,
            language=LANGUAGE_EN,
            region_jurisdiction=REGION_ICH,
        ))

    if not items:
        if required:
            return [], (f"ICH 핵심 페이지 섹션 0건({admin_url}) — 구조/렌더 변경 의심"
                        f"(수동 확인 필요)")
        log("INFO", f"ICH '{type_or_class}' 0건(정상 가능 — 진행 중 항목 없음) ({admin_url})")
        return [], None

    log("INFO", f"ICH '{type_or_class}' 완료: {len(items)}건 ({admin_url})")
    return items, None


def collect_ich(run_date: date) -> tuple[list[IntakeItem], str | None]:
    """ICH 수집 진입점.

    반환: (items, error_msg).
    - 핵심(required) 페이지가 0건/실패면 error_msg 를 채운다(partial 로 묻지 않음).
    - 비핵심(Public Consultations) 0건은 정상.
    - 부분 실패라도 수집된 items 는 함께 반환한다(graceful) — 단 required 실패 시 error 동반.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []
    required_failed = False
    seen: set[str] = set()

    for slug, type_or_class, required in ICH_PAGES:
        page_items, err = _collect_page(slug, type_or_class, required, run_date)
        if err:
            log("WARN", err)
            errors.append(err)
            if required:
                required_failed = True
        for it in page_items:
            if it.document_id in seen:
                continue
            seen.add(it.document_id)
            items.append(it)

    if required_failed or (errors and not items):
        return items, "; ".join(errors) or "ICH 핵심 페이지 수집 실패"

    log("INFO", f"ICH 수집 완료: {len(items)}건 (부분오류={len(errors)})")
    return items, None
