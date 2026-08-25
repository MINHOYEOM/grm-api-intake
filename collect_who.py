#!/usr/bin/env python3
"""GRM WHO Prequalification Collector — P1 (글로벌 확장).

ENABLE_WHO=true 또는 --sources who 일 때 collect_intake.main() 에서 호출된다.

배경 (probe 2026-06-02):
  extranet.who.int/prequal 는 Drupal 10 **서버렌더링**. ICH 와 달리 NOC/WHOPIR 목록 항목이
  HTML 에 **inline** 으로 존재한다(링크 추출이 실제로 동작). 또한 공식 **RSS** 가 있다.

수집 채널 (제조/품질 직접 관련):
  1. RSS  : https://extranet.who.int/prequal/rss.xml — PQ 뉴스/공지/가이드라인 등 (날짜 있음)
  2. WHOPIR Medicines : 제조소(FPP/API) 공개 실사보고서 PDF 목록(제조소명·국가, 페이지네이션)
  3. NOC Medicines    : Notice of Concern (제조소 GMP 비순응) — 최고 신호

설계 역할:
  - RSS 는 날짜 기반 윈도우 수집(다른 RSS 소스와 동일).
  - WHOPIR/NOC 는 목록 스냅샷 + URL 기반 dedup → 새 보고서/공지가 새 후보로 표면화.
    WHOPIR 의 date_iso 는 **실사 시작일**(목록의 inspection-dates 필드)이다 — WHO 는
    보고서 게시일을 싣지 않는다. 날짜를 단언하기 어려우면 date_iso=""(Run Date 기준 intake).
  - official_url 은 항상 WHO 공식 PDF/페이지.
  - 핵심 목록 페이지가 0건이면 침묵하지 않고 error(구조 변경/렌더 변경 = 수동 확인).

대상 사용자(QA/QC/VAL/설비/DI 등)가 폭넓게 쓰도록, 명백한 임상/기기 전용만 배제하고
제조·품질 신호는 보수적으로 포함한다(최종 판정은 Routine).
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from grm_common import env_flag, http_get_bytes, http_get_html, log
from collect_intake import (
    IntakeItem,
    SOURCE_WHO,
    SRC_TYPE_OFFICIAL_API,
    SRC_TYPE_OFFICIAL_PAGE,
    compute_relevance,
    compute_signal_tier,
    _within_window,
    _stable_doc_id,
    _rss2_items_from_root,
    _atom_entries_from_root,
    _rss_text,
    _atom_text,
    _atom_link,
    _parse_rss2_date,
    _parse_atom_date,
)
from grm_common import http_get_xml


TYPE_WHO_NEWS = "who-news"
TYPE_WHO_INSPECTION = "who-inspection"     # WHOPIR
TYPE_WHO_NOC = "who-noc"                    # Notice of Concern
LANGUAGE_EN = "EN"
REGION_WHO = "WHO (Global)"

WHO_RSS_URL = "https://extranet.who.int/prequal/rss.xml"
WHOPIR_MED_URL = ("https://extranet.who.int/prequal/inspection-services/"
                  "who-public-inspection-reports-whopirs-medicines")
NOC_MED_URL = ("https://extranet.who.int/prequal/inspection-services/"
               "notices-concern-nocs-medicines")

HTTP_RETRIES = 3
REQUEST_DELAY_SECONDS = 1.0
WHOPIR_MAX_PAGES = 8
MAX_TITLE_CHARS = 240

# WHY-1 #1: WHOPIR PDF 결함 excerpt (flag 게이트 ENABLE_WHOPIR_EXCERPT, 기본 off).
# P6(MFDS GMP)의 검증된 PDF 텍스트 엔진(_extract_pdf_text)을 재사용하고, WHOPIR 영문
# 구조에 맞는 결함 섹션 앵커만 새로 둔다. 비용·예의: per-item timeout/delay + 최신 N건 cap.
WHOPIR_EXCERPT_MAX_CHARS = 1500
WHOPIR_EXCERPT_FETCH_TIMEOUT = 20
WHOPIR_EXCERPT_DELAY_SECONDS = 0.5
WHOPIR_EXCERPT_MAX_ITEMS = 40          # fetch 비용 상한(목록 newest-first → 최신 N건 우선)
# WHOPIR 본문은 15~40쪽(실측 29K~90K자)이라 P6 기본 상한 12,000자로는 결함 구간에
# 닿기도 전에 잘린다. 텍스트 추출 상한만 WHOPIR 전용으로 올린다(발행물 길이는 아래
# SECTION/OUTCOME cap 이 따로 잡는다 — 이 값은 "읽는 범위"이지 "싣는 범위"가 아니다).
WHOPIR_TEXT_MAX_CHARS = 120_000
# 표지/개요를 건너뛰고 결함·결론 구간부터 잘라내기 위한 영문 앵커(우선순위 순).
# WHOPIR PDF는 [표지 → general info → summary of the inspection → outcome/conclusion →
# (non-)compliance/GMP deficiencies] 구조라, 인용보다 LLM 컨텍스트("왜")용으로 결함 구간을 우선.
_WHOPIR_EXCERPT_PATTERNS = (
    r"summary\s+of\s+the\s+deficiencies",
    r"summary\s+of\s+gmp\s+deficiencies",
    r"list\s+of\s+(?:gmp\s+)?deficiencies",
    r"gmp\s+deficiencies",
    r"deficiencies",
    r"non[-\s]?compliance",
    r"outcome\s+of\s+(?:the\s+)?inspection",
    r"conclusion",
    r"summary\s+of\s+(?:the\s+)?inspection",
)

# WHOPIR excerpt 관측용(dry-run 검증·운영 health). gmp_inspection.LAST_HEALTH 패턴.
LAST_HEALTH: dict[str, Any] = {}

# 임상/기기 전용 등 명백히 무관한 것만 배제 (제조·품질은 보수적으로 포함)
_WHO_EXCLUDE = [
    "vector control", "pesticide", "male circumcision",
    "in vitro diagnostic", "ivd ", "snake antivenom",
]

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# "26 January, 2026" 처럼 월-연 사이 콤마가 들어가는 Drupal 렌더가 있어 콤마를 허용한다
# (NOC 의 "(09 October 2020)" 는 그대로 매칭 — 상위집합 확장이라 기존 동작 불변).
_DATE_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\b")
_DATE_MY_RE = re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b")


class _LinkParser(HTMLParser):
    """<a href> + 앵커 텍스트 쌍 수집 (구조 비의존)."""

    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            if self._href is not None:
                self.links.append((self._href, " ".join(self._parts).strip()))
            self._href = (dict(attrs).get("href") or "").strip()
            self._parts = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._parts).strip()))
            self._href = None
            self._parts = []

    def handle_data(self, data):
        if self._href is not None:
            s = data.strip()
            if s:
                self._parts.append(s)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _excluded(blob: str) -> bool:
    b = blob.lower()
    return any(x in b for x in _WHO_EXCLUDE)


def _parse_text_date(text: str) -> str:
    """앵커 텍스트에서 'DD Month YYYY' 또는 'Month YYYY' → ISO. 실패 시 ''."""
    m = _DATE_DMY_RE.search(text or "")
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(1))).isoformat()
            except ValueError:
                pass
    m2 = _DATE_MY_RE.search(text or "")
    if m2:
        mon = _MONTHS.get(m2.group(1).lower())
        if mon:
            try:
                return date(int(m2.group(2)), mon, 1).isoformat()
            except ValueError:
                pass
    return ""


# ── [WHOPIR 실사일 결손 수리 2026-08-10] ────────────────────────────────────
# 종전 `date_iso=_parse_text_date(text)` 는 **시도는 있으나 대상이 틀린** 코드였다.
# WHO Drupal 티저의 `<a>` 앵커 텍스트는 제조소명 한 줄뿐이고(실측 168행 전건 — 날짜가
# 들어간 앵커는 0건), 날짜는 앵커 **바깥 형제 필드**
# `field--name-field-whopir-inspection-dates` 안의 `<time datetime="…">`(시작·종료 2개)에 있다.
# 그래서 WHOPIR 은 늘 date_iso="" 였고, 이 결손은 카드의 날짜 행에서 끝나지 않았다 —
# `grm_findings.RAW_SIGNAL_REQUIRED_FIELDS` 가 `published_date` 를 요구해 raw_signals
# **POST 자체가 발생하지 않았다**(WHO raw_signals 0건). 경보는 WARN 한 줄뿐이라 오래 살았다.
# 그래서 여기서는 값만 고치지 않고 ①미추출 카운터(LAST_HEALTH["whopir_dates"])
# ②전건 미추출 sentinel(= 마크업 변경 신호)까지 같이 둔다.
#
# 값의 의미는 **실사일**이지 보고서 게시일이 아니다(WHO 는 게시일을 목록에 싣지 않는다).
# 카드 라벨은 `card_scaffold` 의 `SourceSpec.date_label` 로 "실사일"로 부른다.
_WHOPIR_DATES_FIELD = "field--name-field-whopir-inspection-dates"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 유니코드 하이픈 계열(en/em-dash·minus) → ASCII '-'. 실측 표기가 섞여 있다("18 – 22 March 2024").
_DASH_MAP = {ord(c): "-" for c in "‐‑‒–—―−"}
# 범위 표기의 **앞머리**가 시작일이다. 두 형태를 구분해서 본다:
#   · 일(day)만 앞선 형태  — "26 - 28 January 2026" / "From 7 to 11 April 2025"
#   · 일+월이 앞선 형태    — "28 January - 2 February 2026"(달 넘김)
_RANGE_HEAD_DAY_RE = re.compile(r"(\d{1,2})\s*(?:-|to|through|until)\s*$", re.I)
_RANGE_HEAD_DM_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+),?\s*(?:-|to|through|until)\s*$", re.I)


def _is_whopir_pdf(href: str) -> bool:
    """WHOPIR 보고서 PDF 링크 판정.

    C3-b: ".pdf?download=1"/"#…" 꼬리가 붙어도 PDF — path 만 검사한다(endswith 는 탈락시킴).
    """
    return "/whopir_files/" in (href or "").lower() and \
        urlsplit(href or "").path.lower().endswith(".pdf")


def _iso_or_empty(year: int, month: int, day: int) -> str:
    """달력에 없는 조합(2월 30일 등)은 날조 대신 ""(호출부가 다음 후보로 넘어간다)."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _parse_inspection_dates_text(text: str) -> str:
    """실사일 표기(범위 포함) → **시작일** ISO. 못 읽으면 ""(빈 값 = 미확인, 추정 금지).

    `<time datetime>` 이 사라지는 마크업 드리프트용 폴백이자, 텍스트로만 실사일을 싣는
    다른 경로(PDF 표지의 `Dates of inspection` 등)와 형태를 공유한다. 실측·채록된 형태:
      · "26 - 28 January 2026"              (일 범위 + 월/연은 뒤에 한 번)
      · "From 7 to 11 April 2025"           (전치사형)
      · "15-17 July 2024 Bioanalytical site" (뒤에 꼬리말이 붙음)
      · "18 – 22 March 2024"                (en-dash)
      · "26 January, 2026 - 28 January, 2026" (Drupal 렌더 — 양끝 모두 완전한 날짜)
      · "22 June, 2025"                     (단일일)
      · "28 January - 2 February 2026"      (달 넘김 — 앞머리에 월이 있다)
    """
    s = _clean(text).translate(_DASH_MAP)
    if not s:
        return ""
    for m in _DATE_DMY_RE.finditer(s):
        month = _MONTHS.get(m.group(2).lower())
        if not month:
            continue                      # "15 Bioanalytical 2024" 류 우연 일치 배제
        year, day, pre = int(m.group(3)), int(m.group(1)), s[:m.start()]
        head_dm = _RANGE_HEAD_DM_RE.search(pre)
        if head_dm:
            head_month = _MONTHS.get(head_dm.group(2).lower())
            head_day = int(head_dm.group(1))
            if head_month:
                # 달 넘김 범위는 시작이 종료보다 앞서야 한다 — "28 December - 3 January 2026"
                # 의 시작은 전년도다(연도는 뒤쪽에만 적히므로 여기서 되돌린다).
                head_year = year - 1 if (head_month, head_day) > (month, day) else year
                iso = _iso_or_empty(head_year, head_month, head_day)
                if iso:
                    return iso
        head_day_only = _RANGE_HEAD_DAY_RE.search(pre)
        if head_day_only:
            iso = _iso_or_empty(year, month, int(head_day_only.group(1)))
            if iso:
                return iso
        iso = _iso_or_empty(year, month, day)
        if iso:
            return iso
    return _parse_text_date(s)             # "January 2026" 처럼 일이 없는 표기까지 최후 폴백


class _WhopirRowParser(HTMLParser):
    """WHOPIR 목록 티저에서 [PDF 링크 → 실사일] 짝을 만든다(행 경계 = `<article>`).

    앵커 텍스트만 보는 `_LinkParser` 로는 못 잡는다 — 날짜가 앵커 **형제 요소**에 있다.
    1순위는 `<time datetime="2026-01-26T12:00:00Z">` 의 기계값(표시 텍스트 형식이 어떻게
    바뀌든 안전하다). 그 속성이 사라지는 드리프트를 대비해 표시 텍스트도 함께 넘겨
    호출부가 `_parse_inspection_dates_text` 로 폴백할 수 있게 한다.

    현재 마크업은 [링크 → 날짜 필드] 순서지만 pending 을 두어 순서가 뒤집혀도 같은
    `<article>` 안이면 짝지어진다 — 마크업 순서에 의존하지 않기 위해서다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, tuple[str, str]] = {}   # href → (시작일 ISO, 표기 원문)
        self._href = ""
        self._depth = 0                              # 날짜 필드 div 중첩 깊이(0 = 필드 밖)
        self._isos: list[str] = []
        self._texts: list[str] = []
        self._pending: tuple[str, str] | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if self._depth:
            if tag == "time":
                self._isos.append(str(attr.get("datetime") or "")[:10])
            elif tag == "div":
                self._depth += 1
            return
        if tag == "article":
            self._href, self._pending = "", None      # 행 경계 — 앞 행 상태를 물려주지 않는다
        elif tag == "a":
            href = str(attr.get("href") or "").strip()
            if _is_whopir_pdf(href):
                self._href = href
                if self._pending is not None:
                    self.rows.setdefault(href, self._pending)
                    self._pending = None
        elif tag == "div" and _WHOPIR_DATES_FIELD in str(attr.get("class") or ""):
            self._depth, self._isos, self._texts = 1, [], []

    def handle_endtag(self, tag):
        if not self._depth or tag != "div":
            return
        self._depth -= 1
        if self._depth:
            return
        isos = sorted(i for i in self._isos if _ISO_DATE_RE.match(i))
        value = (isos[0] if isos else "", _clean(" ".join(self._texts)))
        if self._href:
            self.rows.setdefault(self._href, value)
        else:
            self._pending = value

    def handle_data(self, data):
        if self._depth and data.strip():
            self._texts.append(data.strip())


def _whopir_row_dates(html_text: str) -> dict[str, tuple[str, str]]:
    """목록 HTML → {PDF href: (실사 시작일 ISO, 실사일 표기 원문)}. 못 읽은 행은 키 부재."""
    p = _WhopirRowParser()
    p.feed(html_text)
    return p.rows


def _get_html(url: str, *, timeout: int = 30) -> str:
    return http_get_html(url, timeout=timeout, retries=HTTP_RETRIES, label="WHO")


def _links(html_text: str) -> list[tuple[str, str]]:
    p = _LinkParser()
    p.feed(html_text)
    return p.links


# ── 1) RSS ────────────────────────────────────────────────────────────────────
def _collect_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    log("INFO", f"WHO RSS 수집: {WHO_RSS_URL}")
    try:
        root = http_get_xml(WHO_RSS_URL)
    except Exception as e:  # noqa: BLE001
        return [], f"WHO RSS 실패: {e}"

    rss_items = _rss2_items_from_root(root)
    use_atom = not rss_items
    nodes = _atom_entries_from_root(root) if use_atom else rss_items

    items: list[IntakeItem] = []
    for node in nodes:
        if use_atom:
            title = _atom_text(node, "title")
            link = _atom_link(node)
            pub = _atom_text(node, "updated") or _atom_text(node, "published")
            date_iso = _parse_atom_date(pub) if pub else ""
            desc = _atom_text(node, "summary") or _atom_text(node, "content")
        else:
            title = _rss_text(node.find("title"))
            link = _rss_text(node.find("link"))
            pub = _rss_text(node.find("pubDate")) or _rss_text(node.find("pubdate"))
            date_iso = _parse_rss2_date(pub) if pub else ""
            desc = _rss_text(node.find("description"))
            # C3-a: WHO Drupal RSS2 description 은 raw HTML(<p>/<a href=…>) —
            # exclusion/relevance/body 에 태그가 그대로 흘러 잡음·오판 소지.
            # Atom summary 는 텍스트라 RSS2 분기만 태그 제거.
            desc = re.sub(r"<[^>]+>", " ", desc)
        title = _clean(title)
        if not title or not _within_window(date_iso, start, end):
            continue
        blob = f"{title} {desc}"
        if _excluded(blob):
            continue
        relevance = compute_relevance(title, desc)
        if relevance == "Pending":
            relevance = "Possible"   # WHO PQ 항목은 제조/품질 맥락 → 보수적으로 보존
        tier = compute_signal_tier(SOURCE_WHO, TYPE_WHO_NEWS, relevance, "N/A", title, desc)
        items.append(IntakeItem(
            source=SOURCE_WHO,
            document_id=_stable_doc_id(SOURCE_WHO, title, link, date_iso),
            date_iso=date_iso,
            headline=title[:MAX_TITLE_CHARS],
            official_url=link or WHO_RSS_URL,
            type_or_class=TYPE_WHO_NEWS,
            body=_clean(desc)[:1500],
            api_query=WHO_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_API,
            signal_tier=tier,
            raw_payload={"channel": "rss", "title": title, "link": link, "pubDate": pub},
            language=LANGUAGE_EN,
            region_jurisdiction=REGION_WHO,
        ))
    log("INFO", f"WHO RSS 완료: {len(items)}건")
    return items, None


# ── 2) WHOPIR (공개 실사보고서) ────────────────────────────────────────────────
def _whopir_excerpt_enabled() -> bool:
    """ENABLE_WHOPIR_EXCERPT=true 일 때만 PDF 본문 fetch+excerpt(기본 off)."""
    return env_flag("ENABLE_WHOPIR_EXCERPT")


def _extract_whopir_excerpt(text: str) -> str:
    """WHOPIR PDF 평탄화 텍스트 → 영문 결함/결론 구간 excerpt. 앵커 미스는 ""(키 미기록).

    표지/개요 보일러플레이트가 아니라 결함·결론을 카드 컨텍스트("왜")로 올리기 위한 추출.
    P2-A: 앵커 미스 시 선두 본문 폴백을 두지 않는다 — 표지/General Information(사이트명·
    주소·날짜)이 excerpt 로 새어드는 경로라 제거. WL excerpt 와 동일한 precision 우선
    정책으로, 미스는 호출부에서 'no-excerpt' 실패로 집계돼 health warning 으로 표면화.
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    for pat in _WHOPIR_EXCERPT_PATTERNS:
        m = re.search(pat, compact, re.I)
        if m:
            return compact[m.start():][:WHOPIR_EXCERPT_MAX_CHARS].strip()
    return ""


# ── [WHOPIR 구조화 상세 2026-07-27] ──────────────────────────────────────────
# WHO 공개실사보고서(WHOPIR)는 지금까지 **본문을 한 글자도 읽지 않고** 제목만 카드로
# 내보냈다("실사 결과 세부 내용은 확보하지 못해 원문 확인이 필요하다"). 그런데 실물 PDF 는
# 스캔이 아니라 완전한 텍스트이고(실측 11건: 14~19쪽·3.5~5.3만자) WHO 표준 서식이라
# 483 스캔본보다 **훨씬 쉽게** 읽어낼 수 있는 자료였다.
#
# 서식은 세 종류다(실측 11건 = findings 9 · reliance 2 + 2026-08-24 CRO/BE 1):
#   · findings — Part 2 "Summary of the findings and comments" + 번호 매긴 GMP 항목
#     (원료 15항목 / 시스템 6항목 / QC시험실 5항목 등 템플릿마다 다름 → 개수 고정 금지)
#   · reliance — Part 2 "Summary of SRA/NRA inspection evidence considered"
#     (WHO 자체 실사가 아니라 타 규제기관 실사 결과를 인용). 항목 대신 인용 실사 목록.
#   · CRO/BE(GCP·생동성시험 기관) — Part 2 뒤에 **Part 3 이 없고** 결론이 Part 4,
#     참고 가이드라인 목록이 Part 5 다(실측 ACDIMA 26쪽·항목 21개). 종전 "Part 3 필수"
#     전제가 이 서식에서 구조화 전체를 포기하게 했다 — 경계를 "Part 2 뒤 첫 마커(3 또는 4)"
#     로 일반화한다.
_WHOPIR_PART2_RE = re.compile(r"Part\s*2\b", re.I)
_WHOPIR_PART3_RE = re.compile(r"Part\s*3\b", re.I)
_WHOPIR_PART4_RE = re.compile(r"Part\s*4\b", re.I)
_WHOPIR_PART5_RE = re.compile(r"Part\s*5\b", re.I)
_WHOPIR_RELIANCE_RE = re.compile(r"SRA\s*/\s*NRA\s+inspection\s+evidence", re.I)
# 번호 구분자 `[.)]` 는 **선택**이다 — Keming(2026-08-03 실측)은 1번 표제만 "1 Quality
# management" 로 점 없이 적혀 있었고, 구분자 필수 규칙이 1번을 못 잡자 연속 번호 사슬이
# 시작조차 못 해 15개 전 항목이 유실됐다(발행 카드가 결론만 실림). 점 없는 숫자 시작 줄이
# 표제로 오인될 위험은 제목 형태 제약(대문자 시작·한 줄·≤70자)과 아래 빈 줄/본문 길이/사슬
# 연속성 가드가 막는다(9개 PDF 전수 재검에서 오탐 0 실측).
_WHOPIR_HEAD_RE = re.compile(r"^[ \t]*(\d{1,2})[.)]?\s+([A-Z][^\n]{2,70}?)\s*$", re.M)
# 표제 판별의 1순위 신호 = **앞에 빈 줄**. 항목 본문 안의 중첩 번호 목록은 앞 줄에 바로
# 붙는다(실측 Tianjin: 진짜 항목 앞은 "…WHOPIR. \n \n", 문서목록 항목 앞은
# "…Specification \n"). 번호 순서만으로는 이 둘을 못 가른다 — 중첩 "4. WMS Validation PQ
# Report" 가 진짜 "4. Laboratory Control System" 을 밀어냈다.
# 다만 이 조건을 **필수**로 걸면 빈 줄이 안 나오는 레이아웃의 보고서가 통째로 0항목이
# 된다(실측 Ecron 22→0·Pharco 15→6). 그래서 빈 줄 후보를 우선하되, 없으면 본문 길이
# 조건으로 완화 폴백한다 — 정확도를 지키면서 회수율을 잃지 않는 절충.
_WHOPIR_BLANK_BEFORE_RE = re.compile(r"\n[ \t]*\n[ \t]*$")
_WHOPIR_DATES_RE = re.compile(
    r"Dates?\s+of\s*\n?\s*inspection\s*:?\s*\n?\s*([^\n]{4,60})", re.I)
_WHOPIR_CONCL_LEAD_RE = re.compile(
    r"^\s*Conclusion\s*[-–—]?\s*Inspection\s+outcome\s*", re.I)
# 항목 본문 상한 — 카드는 근거를 보여주는 자리이고 전문은 공식 PDF 링크가 담당한다.
# (상한이 없으면 브리프 JSON 이 카드 1장당 2~7만자씩 불어난다 — 실측 Kaygee 68,520자.)
WHOPIR_SECTION_MAX_CHARS = 600
WHOPIR_OUTCOME_MAX_CHARS = 1200
# 표제로 인정할 최소 본문 길이. 항목 본문 안의 **중첩 번호 목록**이 표제로 오인되던 것을
# 막는다(실측 Tianjin: 문서 목록의 "4. WMS Validation PQ Report" 가 진짜 항목
# "4. Laboratory Control System" 을 밀어냈다). 진짜 항목은 뒤에 본문이 길게 따라온다.
_WHOPIR_SECTION_MIN_BODY = 300


def _normalize_ligatures(text: str) -> str:
    """483 수집기의 합자 정규화기를 재사용(엔진 부재 시 원문 그대로 — 비차단)."""
    try:
        from collect_fda_483 import normalize_pdf_ligatures
    except Exception:  # noqa: BLE001
        return text
    return normalize_pdf_ligatures(text)


def _whopir_squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _whopir_cut(text: str, limit: int) -> str:
    """상한 절단 — 문장 경계 우선(문장 중간에서 끊기지 않게), 절단 시 말줄임 표기."""
    s = _whopir_squeeze(text)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    p = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:p + 1] if p > limit * 0.5 else cut.rstrip()) + " …"


# 페이지 푸터 블록 — PDF 평탄화가 매 쪽 하단을 본문 한가운데로 밀어 넣는다(실측 Zhejiang
# 항목 11·15 가 "20, AVENUE APPIA … Page 10 of 14" 로 시작했다). 주소줄부터 쪽번호까지를
# 한 덩어리로 지운다 — 사이의 러닝헤더(업체명+실사일)까지 함께 사라진다. 줄 구조를 보존해야
# 표제 판별의 "빈 줄" 신호가 살아남으므로 빈 줄로 치환한다.
# 상한 800자 = 가장 긴 실측 변형(Ecron: 주소줄+러닝헤더+실사일+140자 구분선+고지 3줄+쪽번호
# ≈ 435자)에 여유를 둔 값. 페이지 사이 간격이 2,000자 이상이라 다음 푸터까지 번지지 않는다.
_WHOPIR_FOOTER_RE = re.compile(
    r"20,\s*AVENUE\s+APPIA.{0,800}?Page\s+\d+\s+of\s+\d+"
    r"(?:\s*Client\s+Confidential)?", re.S | re.I)
_WHOPIR_AUTH_STOP = {"not specified", "not applicable", "not stated", "none", "n/a", "-"}
_WHOPIR_AUTH_MAX_LINES = 6
_WHOPIR_AUTH_MAX_CHARS = 80


def _whopir_authority_before(pre: str) -> str:
    """Part 2 표에서 `Dates of inspection` **바로 앞 셀**의 실사기관명을 복원.

    PDF 평탄화가 표 셀을 여러 줄로 쪼개 놓아서(`Korean`/`Ministry of`/`Food and Drug`/
    `Safety (MFDS`/`Korea)`) 단순 슬라이스로는 앞 행의 답변 문장 꼬리까지 딸려온다 —
    실제로 첫 구현이 `"t to last) and comments Dutch Health…"` 같은 값을 냈다(2026-07-27
    실측). 그래서 **뒤에서 앞으로** 줄을 모으되 기관명이 아닌 줄에서 멈춘다:
      · 소문자로 시작 = 앞 문장의 이어짐(`facility, was not covered)`)
      · 마침표로 끝남 = 완결된 산문
      · 고정 답변 토큰(`Not specified` 등)
    못 읽으면 빈 문자열 — 호출부가 그 항목을 통째로 버린다(쓰레기 기관명을 싣느니 뺀다).
    """
    lines = [ln.strip() for ln in pre.split("\n")]
    out: list[str] = []
    for ln in reversed(lines):
        if not ln:
            if out:                                   # 셀 사이 빈 줄 = 경계
                break
            continue                                  # 값 앞의 빈 줄은 건너뛴다
        if ln.lower().rstrip(":") in _WHOPIR_AUTH_STOP or ln.endswith("."):
            break
        core = ln.lstrip("(").strip()
        if not core or not core[0].isupper():
            break
        out.append(ln)
        if len(out) >= _WHOPIR_AUTH_MAX_LINES:
            break
    auth = _whopir_squeeze(" ".join(reversed(out)))
    return auth if 0 < len(auth) <= _WHOPIR_AUTH_MAX_CHARS else ""


def extract_whopir_report(text: str) -> "dict[str, Any] | None":
    """WHOPIR PDF 평탄화 텍스트 → 구조화 상세(순수 함수·LLM 0). 실패 시 None.

    반환 = `{"type":"whopir_report", "report_kind":"findings"|"reliance",
             "outcome": str, "sections":[{"no","title","text"}],
             "reliance":[{"authority","dates"}]}`

    Part 2 와 그 뒤의 결론 마커(Part 3 — CRO/BE 서식은 Part 4)를 못 찾으면 None —
    호출부가 키를 안 쓰고 링크 카드로 유지한다(구조를 못 읽었으면 읽은 척하지 않는다).
    """
    # PDF 서브셋 폰트 합자(ﬂow·qualiﬁed·identiﬁcation)를 먼저 되돌린다 — 483 과 같은 정규화기를
    # 공유한다. 발행물 게이트(`test_no_ligature_artifacts`)가 잡는 잔재라 파싱 전에 처리해야 한다.
    t = _WHOPIR_FOOTER_RE.sub("\n\n", _normalize_ligatures(text or ""))
    m2 = _WHOPIR_PART2_RE.search(t)
    if not m2:
        return None
    # 본문 종료 = Part 2 뒤 **첫** 마커. 표준 서식은 Part 3(결론), CRO/BE 서식은 Part 3 이
    # 없고 Part 4 가 결론이다(실측 ACDIMA — 종전 "Part 3 필수"가 항목 21개를 통째로 버렸다).
    # outcome 종료 경계도 같은 규칙으로 한 칸씩 민다(표준 = Part 4, CRO/BE = Part 5).
    m3 = _WHOPIR_PART3_RE.search(t, m2.end())
    m4 = _WHOPIR_PART4_RE.search(t, (m3.end() if m3 else m2.end()))
    concl = m3 or m4
    if not concl:
        return None
    if concl is m3:
        nxt = m4
    else:
        nxt = _WHOPIR_PART5_RE.search(t, m4.end())
    body = t[m2.end():concl.start()]
    outcome = _WHOPIR_CONCL_LEAD_RE.sub(
        "", _whopir_squeeze(t[concl.end():(nxt.start() if nxt else len(t))]))
    kind = "reliance" if _WHOPIR_RELIANCE_RE.search(body[:400]) else "findings"

    sections: list[dict[str, str]] = []
    reliance: list[dict[str, str]] = []
    if kind == "findings":
        cands = [(int(m.group(1)), m.group(2).strip(), m.start(), m.end(),
                  bool(_WHOPIR_BLANK_BEFORE_RE.search(body[:m.start()])))
                 for m in _WHOPIR_HEAD_RE.finditer(body)]

        def _body_len(i: int) -> int:
            nxt = cands[i + 1][2] if i + 1 < len(cands) else len(body)
            return nxt - cands[i][3]

        kept: list[tuple[int, str, int, int]] = []
        expect, start_at = 1, 0
        # 1번 표제가 후보에 아예 없는 서식(표제 형태 특이 등)은 최소 번호에서 사슬을 시작한다
        # — 종전엔 expect=1 고정이라 1번 미검출이 곧 **전 항목 유실**이었다(실측 Keming 15→0).
        if cands and not any(c[0] == 1 for c in cands):
            expect = min(c[0] for c in cands)
        while True:
            pool = [i for i in range(start_at, len(cands)) if cands[i][0] == expect]
            renumber = False
            if not pool:
                # 원문 오기재 관용 — WHO 가 쓴 PDF 자체가 번호를 반복하거나(실측 Chongqing:
                # 3장을 "2."로 두 번) 하나를 건너뛴 경우, 연속 번호 요구가 그 지점에서 사슬을
                # 끊어 나머지 전부를 버렸다(15→2). 빈 줄 선행(강신호) 후보에 한해 ±1 을
                # 허용한다. 반복 번호는 표시 번호를 사슬 값(expect)으로 보정한다 — 원문 그대로
                # 두면 번역 키(s<번호>)가 충돌해 국문 병기가 서로를 덮어쓴다.
                for delta in (-1, +1):
                    pool = [i for i in range(start_at, len(cands))
                            if cands[i][0] == expect + delta and cands[i][4]]
                    if pool:
                        renumber = (delta == -1)
                        if delta == +1:
                            expect += 1
                        break
            if not pool:
                break
            # ① 빈 줄이 앞선 후보 우선(중첩 목록 배제) ② 없으면 본문 길이로 폴백
            idx = next((i for i in pool if cands[i][4]), None)
            if idx is None:
                idx = next((i for i in pool
                            if _body_len(i) >= _WHOPIR_SECTION_MIN_BODY), None)
            if idx is None and len(kept) >= 2:
                # ③ 사슬이 이미 2개 이상 이어졌다면 정확한 다음 번호 자체가 강한 증거다 —
                # 빈 줄도 없고 본문도 짧은 진짜 항목(실측 ACDIMA "4. Archive facilities" 249자,
                # 21개 중 18개가 빈 줄 없음)이 여기서 끊기면 나머지 전부를 잃는다.
                idx = pool[0]
            if idx is None:
                break
            n, title, s, e, _strict = cands[idx]
            kept.append(((expect if renumber else n), title, s, e))
            expect, start_at = (expect if renumber else n) + 1, idx + 1
        for i, (n, title, _s, e) in enumerate(kept):
            end = kept[i + 1][2] if i + 1 < len(kept) else len(body)
            seg = _whopir_cut(body[e:end], WHOPIR_SECTION_MAX_CHARS)
            if seg:
                sections.append({"no": str(n), "title": title, "text": seg})
    else:
        head = _WHOPIR_RELIANCE_RE.search(body)
        scope = body[head.end():] if head else body   # 표 머리글 제거(표제 오인 차단)
        for m in _WHOPIR_DATES_RE.finditer(scope):
            auth = _whopir_authority_before(scope[:m.start()])
            if not auth:
                continue                              # 못 읽으면 안 싣는다(쓰레기 기관명 금지)
            reliance.append({"authority": auth, "dates": _whopir_squeeze(m.group(1))})

    if not (outcome or sections or reliance):
        return None
    out: dict[str, Any] = {
        "type": "whopir_report",
        "report_kind": kind,
        "outcome": _whopir_cut(outcome, WHOPIR_OUTCOME_MAX_CHARS),
    }
    if sections:
        out["sections"] = sections
    if reliance:
        out["reliance"] = reliance
    return out


def _fetch_whopir_excerpt(pdf_url: str) -> tuple[str, str]:
    """WHOPIR PDF fetch → 영문 결함 excerpt. 반환 (excerpt, status).

    status: 'ok' | 'no-excerpt' | 'fetch-fail:…' | PDF 엔진 status
    (pdf-encrypted/scan-no-text/pdf-parse-fail:…/pdf-parser-missing). 실패 시 excerpt=""
    → 호출부가 raw_payload 에 키를 쓰지 않고 항목은 링크 카드로 유지(graceful degrade).
    P6 PDF 엔진(_extract_pdf_text) 재사용 — MFDS 전용 Referer 가 없는 WHO PDF 라
    fetch 는 grm_common.http_get_bytes(WHO 가 이미 쓰는 클라이언트)를 직접 쓴다.
    """
    try:
        from collect_mfds_gmp_inspection import _extract_pdf_text
    except Exception as e:  # noqa: BLE001 — 임포트 실패도 graceful(키 미기록)
        return "", f"engine-missing:{type(e).__name__}"
    try:
        data = http_get_bytes(
            pdf_url, timeout=WHOPIR_EXCERPT_FETCH_TIMEOUT, retries=HTTP_RETRIES,
            headers={"Accept": "application/pdf"}, label="WHOPIR PDF",
        )
    except RuntimeError as e:
        return "", f"fetch-fail:{str(e)[:120]}"
    text, status = _extract_pdf_text(data, max_chars=WHOPIR_TEXT_MAX_CHARS)
    if not text:
        return "", status
    excerpt = _extract_whopir_excerpt(text)
    if not excerpt:
        return "", "no-excerpt"
    return excerpt, "ok"


def _fetch_whopir_detail(pdf_url: str) -> tuple[str, "dict[str, Any] | None", str]:
    """WHOPIR PDF 를 **한 번만** 받아 excerpt 와 구조화 상세를 함께 낸다.

    반환 `(excerpt, report, status)`. 같은 PDF 를 두 번 받지 않기 위해 존재한다
    (`_fetch_whopir_excerpt` 는 excerpt 단독 경로로 남겨 기존 호출·테스트 계약 유지).
    구조화가 실패해도 excerpt 는 그대로 살린다 — 두 층은 서로 독립이다.
    """
    try:
        from collect_mfds_gmp_inspection import _extract_pdf_text
    except Exception as e:  # noqa: BLE001
        return "", None, f"engine-missing:{type(e).__name__}"
    try:
        data = http_get_bytes(
            pdf_url, timeout=WHOPIR_EXCERPT_FETCH_TIMEOUT, retries=HTTP_RETRIES,
            headers={"Accept": "application/pdf"}, label="WHOPIR PDF",
        )
    except RuntimeError as e:
        return "", None, f"fetch-fail:{str(e)[:120]}"
    text, status = _extract_pdf_text(data, max_chars=WHOPIR_TEXT_MAX_CHARS)
    if not text:
        return "", None, status
    report = extract_whopir_report(text)
    excerpt = _extract_whopir_excerpt(text)
    if report:
        return excerpt, report, "ok"
    return excerpt, None, ("no-structure" if excerpt else "no-excerpt")


def whopir_raw_is_bare(raw: Any) -> bool:
    """[보강본 가림 방지 2026-08-24] 이 raw_payload 가 '보강 안 된 WHOPIR 재수집분'인지 판정.

    WHOPIR 목록은 매일 전량 재수집되지만 PDF 보강(enrich_whopir_items)은 **신규 항목에만**
    돈다. 그래서 기존 항목의 당일 raw 는 bare(channel/anchor_text/pdf_url/list_page)다 —
    이것이 `grm_handoff.build_inmemory_raw` 를 타고 inmemory 캐시에 오르면
    `attach_raw_to_rows` 의 메모리 우선 규칙이 Notion children 에 저장된 보강본
    (whopir_report·whopir_excerpt)을 가려, 주간 스캐폴드의 WHOPIR 결정론 상세가 통째로
    비었다(실측: 08-03 이후 발행 WHOPIR 카드 전수 "보고서 확인 필요" — 07-27 첫 발행 11장은
    같은 런 보강 + #475 소급 복구로만 살았다). bare 판정된 항목은 캐시에서 빼서 fetch
    폴백(=Notion 보강본)으로 흐르게 하는 것이 이 함수의 존재 이유다. 보강을 시도했지만
    구조화가 안 된 항목(excerpt 만 보유)은 bare 가 아니다 — Notion 저장본과 같으므로
    캐시해도 안전하다."""
    return (isinstance(raw, dict) and raw.get("channel") == "whopir"
            and "whopir_report" not in raw and "whopir_excerpt" not in raw)


def enrich_whopir_items(items: "list[IntakeItem]") -> dict[str, Any]:
    """[중복 제거 후 보강 2026-07-27] 넘겨받은 WHOPIR 항목만 PDF 를 받아 raw_payload 를 채운다.

    왜 수집 루프에서 떼어냈는가 — 종전엔 목록을 훑으며 곧바로 PDF 를 받았는데, **cap 이
    중복 제거보다 먼저** 걸렸다. 그리고 WHO 목록은 최신순이 아니라 **알파벳순**이다(실측
    2026-07-27: Accutest→ADVITY→Aizant… 사이에 2023-09·2024-01 이 뒤섞여 있다). 둘이 겹치면
    fetch 예산 40건을 매일 **같은 알파벳 앞쪽 40건**이 다 써버리고, 새로 올라온 뒤쪽 보고서
    (Tianjin·Zhejiang 등)는 영원히 PDF 를 못 받는다 — 카드는 나오는데 상세는 영영 비는 형태다.
    종전 주석의 "목록 newest-first" 전제가 사실이 아니었다.

    그래서 호출부(`collect_intake`)가 **Notion 중복 제거를 마친 뒤 살아남은 신규 항목만**
    넘긴다. 정상 운영에서 신규는 하루 0~3건이라 cap 에 닿지 않고, 목록 순서와 무관하게
    전수 보강된다(덤으로 매일 165건을 다시 받던 낭비도 사라진다).

    실패는 키 미기록 + warning 누적(항목은 링크 카드로 유지) — 수집 전체 실패 금지.
    반환 = health dict(`LAST_HEALTH["whopir_excerpt"]` 에도 반영).
    """
    enabled = _whopir_excerpt_enabled()
    health: dict[str, Any] = {
        "enabled": enabled, "attempted": 0, "ok": 0, "failed": 0,
        "structured": 0, "capped": False, "warnings": [],
    }
    global LAST_HEALTH
    # 통째로 갈아끼우지 않는다 — `_collect_whopir` 가 넣어둔 다른 관측(whopir_dates)이
    # 이 단계에서 사라지면 읽는 시점에 따라 계기가 조용히 비는 계열의 결함이 된다.
    LAST_HEALTH = {**LAST_HEALTH, "whopir_excerpt": health}
    if not enabled:
        return health
    for item in items:
        raw = item.raw_payload
        if not isinstance(raw, dict) or raw.get("channel") != "whopir":
            continue
        url = str(raw.get("pdf_url") or "")
        if not url:
            continue
        if health["attempted"] >= WHOPIR_EXCERPT_MAX_ITEMS:
            health["capped"] = True
            break
        health["attempted"] += 1
        if WHOPIR_EXCERPT_DELAY_SECONDS:
            time.sleep(WHOPIR_EXCERPT_DELAY_SECONDS)
        excerpt, report, status = _fetch_whopir_detail(url)
        if report:
            raw["whopir_report"] = report
            health["structured"] += 1
        if excerpt:
            raw["whopir_excerpt"] = excerpt
            health["ok"] += 1
        else:
            health["failed"] += 1
            warn = f"WHOPIR excerpt 실패({status}): {url}"
            health["warnings"].append(warn)
            log("WARN", warn + " — 링크 카드로 유지(manual_review)")
    if health["capped"]:
        log("WARN", f"WHOPIR excerpt cap({WHOPIR_EXCERPT_MAX_ITEMS}) 도달 — "
                    f"나머지 신규 항목은 excerpt 없이 링크 카드로 유지")
    log("INFO", f"WHOPIR excerpt: attempted={health['attempted']} ok={health['ok']} "
                f"failed={health['failed']} structured={health['structured']}")
    return health


def _collect_whopir(run_date: date) -> tuple[list[IntakeItem], str | None]:
    items: list[IntakeItem] = []
    seen: set[str] = set()
    dateless: list[str] = []               # 실사일 미추출 항목(침묵 금지 — 아래 health/sentinel)
    excerpt_enabled = _whopir_excerpt_enabled()
    excerpt_health: dict[str, Any] = {
        "enabled": excerpt_enabled, "attempted": 0, "ok": 0, "failed": 0,
        "structured": 0, "capped": False, "warnings": [],
    }
    for page in range(WHOPIR_MAX_PAGES):
        url = WHOPIR_MED_URL if page == 0 else f"{WHOPIR_MED_URL}?page={page}"
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            html_text = _get_html(url)
        except RuntimeError as e:
            if items:
                log("WARN", f"WHOPIR page={page} 실패(부분 수집 유지): {e}")
                break
            return [], f"WHO WHOPIR 수집 실패: {e}"
        page_links = [(h, t) for h, t in _links(html_text) if _is_whopir_pdf(h)]
        if not page_links:
            break  # 더 이상 보고서 없음 → 페이지네이션 종료
        row_dates = _whopir_row_dates(html_text)      # href → (실사 시작일, 표기 원문)
        new_on_page = 0
        for href, text in page_links:
            abs_url = urljoin(WHOPIR_MED_URL, href)   # 상대경로 → 절대 URL (Notion URL 속성 요건)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            new_on_page += 1
            manuf = _clean(text) or abs_url.rsplit("/", 1)[-1]
            # 실사일: ①행의 <time datetime> ②같은 필드의 표시 텍스트 ③앵커 텍스트(구 경로)
            # 순으로 좁혀 내려간다. 전부 실패하면 ""(추정 금지) — 아래 dateless 로 집계된다.
            date_iso, dates_text = row_dates.get(href, ("", ""))
            if not date_iso:
                date_iso = (_parse_inspection_dates_text(dates_text)
                            or _parse_inspection_dates_text(text))
            raw_payload: dict[str, Any] = {
                "channel": "whopir", "anchor_text": _clean(text),
                "pdf_url": abs_url, "list_page": url,
            }
            if dates_text:
                # 카드에는 시작일만 싣지만(날짜 행은 단일 값), 원문 표기는 범위다 —
                # 무엇을 읽어 그 값을 냈는지 남긴다(사후 대조용 provenance).
                raw_payload["inspection_dates"] = dates_text
            if not date_iso:
                dateless.append(abs_url)
            items.append(IntakeItem(
                source=SOURCE_WHO,
                document_id="who-whopir-" + hashlib.sha1(abs_url.encode()).hexdigest()[:12],
                date_iso=date_iso,                 # = 실사 시작일(WHO 는 게시일을 싣지 않는다)
                headline=f"[WHOPIR] {manuf}"[:MAX_TITLE_CHARS],
                official_url=abs_url,              # WHO 공식 PDF (per-item, 절대 URL)
                type_or_class=TYPE_WHO_INSPECTION,
                firm=manuf[:200],
                body=("WHO 공개 실사보고서(WHOPIR) — 제조소/CRO/QCL GMP 실사. "
                      f"제조소: {manuf}\n출처: {WHOPIR_MED_URL}"),
                api_query=url,
                qa_relevance="Likely",
                osd_relevance="N/A",
                source_type=SRC_TYPE_OFFICIAL_PAGE,
                signal_tier="Tier 2",
                raw_payload=raw_payload,
                source_url=WHOPIR_MED_URL,
                language=LANGUAGE_EN,
                region_jurisdiction=REGION_WHO,
            ))
        if new_on_page == 0:
            break
    else:
        # for-else: break 없이 WHOPIR_MAX_PAGES 소진 = cap 도달(이후 페이지 누락 가능)
        log("WARN", f"WHO WHOPIR 페이지 cap({WHOPIR_MAX_PAGES}) 도달 — 이후 보고서 누락 가능")
    global LAST_HEALTH
    dates_health = {
        "total": len(items), "dated": len(items) - len(dateless),
        "dateless": len(dateless), "samples": dateless[:3],
    }
    LAST_HEALTH = {"whopir_excerpt": excerpt_health, "whopir_dates": dates_health}
    if not items:
        return [], f"WHO WHOPIR 0건({WHOPIR_MED_URL}) — 구조/렌더 변경 의심(수동 확인 필요)"
    if dateless:
        # 부분 결손은 항목을 살린다(카드는 날짜 행이 "미확인"으로 나간다). 다만 그 항목은
        # published_date 결측이라 findings raw_signals 가 만들어지지 않으므로 조용히 두지 않는다.
        log("WARN", f"WHOPIR 실사일 미추출 {len(dateless)}/{len(items)}건 — 카드 날짜는 미확인, "
                    f"raw_signals 미생성: {', '.join(dateless[:3])}")
    if len(dateless) == len(items):
        # 전건 미추출 = 값의 문제가 아니라 목록 마크업이 바뀐 것(실측 168행 전건에 날짜 있음).
        # 종전엔 이 상태가 곧 "WHO raw_signals 0건"이었는데 아무 신호도 없었다 → error 로 올린다.
        # 항목은 함께 돌려보내 링크 카드로는 살린다(수집 전체를 잃지 않는다).
        return items, (f"WHO WHOPIR 실사일 전건 미추출({len(items)}건, {WHOPIR_MED_URL}) "
                       f"— 목록 마크업 변경 의심(수동 확인 필요)")
    log("INFO", f"WHO WHOPIR 완료: {len(items)}건(실사일 {dates_health['dated']}건)")
    return items, None


# ── 3) NOC (Notice of Concern) ────────────────────────────────────────────────
_NODE_RE = re.compile(r"/prequal/node/\d+")
# B4: Drupal 이 /node/N 대신 path alias 를 쓰게 되는 드리프트 대비 — 'notice' 를
# 포함한 /prequal/ 경로도 후보로 수용. nav 의 'Notice of Concern' 메뉴류는 연도
# 게이트(항목 텍스트의 연도)가 걸러주므로 과수집 위험 낮음(2026-06-10 라이브 확인:
# 연도 텍스트 앵커 = NOC 엔트리뿐, nav 'notice' 링크들은 전부 연도 없음).
_NOC_ALIAS_RE = re.compile(r"/prequal/[^\s\"'<>]*notice", re.I)
_YEAR_TEXT_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _collect_noc(run_date: date) -> tuple[list[IntakeItem], str | None]:
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        html_text = _get_html(NOC_MED_URL)
    except RuntimeError as e:
        return [], f"WHO NOC 수집 실패: {e}"

    items: list[IntakeItem] = []
    seen: set[str] = set()
    seen_texts: set[str] = set()
    links = _links(html_text)
    for href, text in links:
        if not (_NODE_RE.search(href) or _NOC_ALIAS_RE.search(href)):
            continue
        t = _clean(text)
        if not t or not _YEAR_TEXT_RE.search(t):   # NOC 항목은 텍스트에 연도 포함(nav 메뉴 배제)
            continue
        abs_url = urljoin(NOC_MED_URL, href)          # 상대경로 → 절대 URL
        if abs_url in seen or t in seen_texts:        # node+alias 가 같은 NOC 가리킴 대비
            continue
        seen.add(abs_url)
        seen_texts.add(t)
        items.append(IntakeItem(
            source=SOURCE_WHO,
            document_id="who-noc-" + hashlib.sha1((abs_url + "|" + t).encode()).hexdigest()[:12],
            date_iso=_parse_text_date(t),
            headline=f"[WHO NOC] {t}"[:MAX_TITLE_CHARS],
            official_url=abs_url,
            type_or_class=TYPE_WHO_NOC,
            firm=t[:200],
            body=("WHO Notice of Concern — 제조소/CRO/QCL 의 중대 GMP 비순응 미해결 공지. "
                  f"대상: {t}\n출처: {NOC_MED_URL}"),
            api_query=NOC_MED_URL,
            qa_relevance="Likely",
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier="Tier 3",            # GMP 비순응 = 최고 신호
            raw_payload={"channel": "noc", "anchor_text": t, "node_url": abs_url},
            source_url=NOC_MED_URL,
            language=LANGUAGE_EN,
            region_jurisdiction=REGION_WHO,
        ))
    if not items:
        # B4 구조 sentinel: '선택자 전건 탈락'과 '진짜 빈 목록'을 구분해 침묵 0건 금지.
        # NOC = Tier 3 최고신호(GMP 비순응)라 조용한 누락이 가장 위험하다.
        prequal_hrefs = [h for h, _ in links if "/prequal/" in h]
        if not prequal_hrefs:
            return [], (f"WHO NOC 페이지 렌더 이상(prequal 앵커 0, {NOC_MED_URL}) "
                        "— 구조/렌더 변경 의심(수동 확인 필요)")
        stray_year_anchors = [
            h for h, t in links
            if ("/prequal/" in h or "node/" in h.lower())
            and _YEAR_TEXT_RE.search(_clean(t))
        ]
        if stray_year_anchors:
            return [], (f"WHO NOC 선택자 0건 — 연도 텍스트 콘텐츠 앵커 "
                        f"{len(stray_year_anchors)}건이 패턴(/prequal/node/N·notice 별칭) "
                        f"밖({NOC_MED_URL}) — URL 스킴 변경 의심(수동 확인 필요)")
        # 페이지 정상 렌더 + 연도 콘텐츠 앵커 자체가 없음 = 진짜 빈 목록 → 0건 정상.
    log("INFO", f"WHO NOC 완료: {len(items)}건")
    return items, None


def collect_who(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """WHO 수집 진입점. (items, error_msg).

    - WHOPIR(핵심) 0건/실패 또는 RSS 실패는 error 로 올린다.
    - NOC 진짜 0건(빈 목록)은 정상. 단 페이지 실패·렌더 이상·선택자 전건 탈락은
      sentinel 이 error 로 올린다(B4) — NOC 도 core: Tier 3 최고신호의 침묵 누락 금지.
      네트워크성 블립은 health 단계에서 transient warning 강등(T1)이라 core 승격이
      일시 오류로 run 을 red 로 만들지 않는다.
    - 부분 실패라도 수집분은 반환(graceful), 단 핵심 실패 시 error 동반.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []
    core_failed = False
    seen: set[str] = set()

    for fn, core in ((_collect_rss, True), (lambda s, e: _collect_whopir(end), True),
                     (lambda s, e: _collect_noc(end), True)):
        try:
            part, err = fn(start, end)
        except Exception as e:  # noqa: BLE001
            part, err = [], str(e)
        if err:
            log("WARN", f"WHO 부분 오류: {err}")
            errors.append(err)
            if core:
                core_failed = True
        for it in part:
            if it.document_id in seen:
                continue
            seen.add(it.document_id)
            items.append(it)

    if core_failed or (errors and not items):
        return items, "; ".join(errors) or "WHO 핵심 채널 수집 실패"
    log("INFO", f"WHO 수집 완료: {len(items)}건 (부분오류={len(errors)})")
    return items, None
