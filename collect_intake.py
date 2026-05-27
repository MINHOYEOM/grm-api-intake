#!/usr/bin/env python3
"""
GRS API Intake Collector — v15.1 Phase 2

Federal Register API + OpenFDA Drug Enforcement API + RSS 피드 (EMA · MHRA · PIC/S · ECA)
+ FDA Warning Letters 를 호출해 지난 7일 (KST 기준) 항목을 수집하고,
Notion "GRS API Intake" 데이터베이스에 raw 필드를 저장한다.

설계 원칙:
1. KST 기준 7일 윈도우 — 모든 날짜는 Asia/Seoul 로 계산
2. raw API 필드 보존 — Evidence A 조건 충족을 위해 원문 JSON 도 페이지 본문에 보관
3. graceful degradation — 한 소스 실패해도 다른 소스 계속 진행
4. 중복 제거 — source::document_id 키로 Run Date 내 중복 skip
5. QA relevance 1 차 휴리스틱만 부여, 최종 판정은 Routine 위임
6. Source Type 분류 — Official API / Official Regulatory Page / Official Regulator Blog
                      / Expert Secondary

환경 변수 (GitHub Secrets):
- NOTION_TOKEN       : Notion Integration token (secret_…)
- NOTION_DATABASE_ID : "GRS API Intake" DB ID
- OPENFDA_API_KEY    : OpenFDA 무료 API key (선택, 없으면 no-key 사용)

CLI 옵션:
- --dry-run : Notion 호출 없이 stdout 에 요약만 출력
- --window-days N : 기본 7. 백필 테스트 시 변경
- --sources : 수집 소스 선택 (기본: all). 예: --sources fr recall ema mhra pics eca wl
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

KST = ZoneInfo("Asia/Seoul")

FR_API_BASE = "https://www.federalregister.gov/api/v1/documents.json"
OPENFDA_API_BASE = "https://api.fda.gov/drug/enforcement.json"
NOTION_API_VERSION = "2022-06-28"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_DB_QUERY_URL_TPL = "https://api.notion.com/v1/databases/{db_id}/query"

# FDA Recalls/Enforcement L2 (OpenFDA 는 항목별 사용자 친화 URL 이 없음)
FDA_RECALLS_L2 = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"

# Notion 속성 이름 (스키마 가이드와 1:1 대응 — 변경 시 양쪽 모두 수정)
PROP_NAME = "Name"
PROP_SOURCE = "Source"
PROP_DOC_ID = "Document ID"
PROP_DATE = "Date"
PROP_HEADLINE = "Headline"
PROP_OFFICIAL_URL = "Official URL"
PROP_TYPE_CLASS = "Type or Class"
PROP_FIRM = "Firm"
PROP_BODY = "Body"
PROP_DISTRIBUTION = "Distribution"
PROP_COMMENTS_CLOSE = "Comments Close"
PROP_RUN_DATE = "Run Date (KST)"
PROP_COLLECTED_AT = "Collected At"
PROP_API_QUERY = "API Query"
PROP_QA_RELEVANCE = "QA Relevance"
PROP_STATUS = "Status"

SOURCE_FR = "Federal Register"
SOURCE_RECALL = "OpenFDA Recall"
# v15.1 Phase 2 — RSS / HTML 소스
SOURCE_EMA = "EMA"
SOURCE_MHRA = "MHRA Inspectorate"
SOURCE_PICS = "PIC/S"
SOURCE_ECA = "ECA Academy"
SOURCE_FDA_WL = "FDA Warning Letter"

# Source Type 분류 값 (Notion Select 옵션과 1:1 대응)
PROP_SOURCE_TYPE = "Source Type"
SRC_TYPE_OFFICIAL_API = "Official API"          # FR, OpenFDA Recall, EMA RSS
SRC_TYPE_OFFICIAL_PAGE = "Official Regulatory Page"   # PIC/S, FDA WL
SRC_TYPE_OFFICIAL_BLOG = "Official Regulator Blog"    # MHRA Inspectorate
SRC_TYPE_EXPERT_SECONDARY = "Expert Secondary"        # ECA Academy

# 소스별 Source Type 매핑
SOURCE_TYPE_MAP: dict[str, str] = {
    SOURCE_FR:      SRC_TYPE_OFFICIAL_API,
    SOURCE_RECALL:  SRC_TYPE_OFFICIAL_API,
    SOURCE_EMA:     SRC_TYPE_OFFICIAL_API,
    SOURCE_MHRA:    SRC_TYPE_OFFICIAL_BLOG,
    SOURCE_PICS:    SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_ECA:     SRC_TYPE_EXPERT_SECONDARY,
    SOURCE_FDA_WL:  SRC_TYPE_OFFICIAL_PAGE,
}

# RSS / HTML 엔드포인트 (v15.1 추가)
EMA_RSS_FEEDS: dict[str, str] = {
    # 올바른 EMA RSS URL 형식: https://www.ema.europa.eu/en/{feed}.xml
    # (https://www.ema.europa.eu/en/news-events/rss-feeds 에서 확인)
    "scientific-guidelines":  "https://www.ema.europa.eu/en/scientific-guidelines.xml",
    "inspections":            "https://www.ema.europa.eu/en/inspections.xml",
    "news":                   "https://www.ema.europa.eu/en/news.xml",
    "regulatory-guidelines":  "https://www.ema.europa.eu/en/regulatory-and-procedural-guideline.xml",
}
MHRA_RSS_URL = "https://mhrainspectorate.blog.gov.uk/feed/"   # Atom 형식
PICS_RSS_URL = "https://picscheme.org/rss/general_en.rss"
ECA_RSS_URL  = "https://app.gxp-services.net/eca_newsfeed.xml"
FDA_WL_URL   = (
    "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations"
    "/compliance-actions-and-activities/warning-letters"
)
FDA_WL_SEARCH_API = (
    "https://api.fda.gov/other/warning_letters.json"   # 미공개 엔드포인트 — 폴백용
)

# 13 개 카테고리 휴리스틱 키워드 (lowercase 비교, 단어 경계 매칭)
# 주의: 단독 약어("csv", "oos" 등)는 \b 경계 매칭으로 오탐 방지됨
QA_CATEGORY_KEYWORDS = [
    "gmp", "cgmp", "manufacturing practice",
    "pharmaceutical quality system", "pqs", "ich q10",
    "quality risk management", "qrm", "ich q9",
    "data integrity", "alcoa", "part 11", "annex 11",
    "computer system validation", "artificial intelligence",
    # "csv" 단독 제거 → "computer system validation" 으로 대체 (CSV 파일 형식 오탐 방지)
    "process validation", "cleaning validation",
    "analytical procedure", "ich q2", "ich q14",
    "post-approval", "cmc change", "ich q12",
    "continuous manufacturing",
    "stability", "ich q1", "oos", "oot",
    "deviation", "capa", "change control",
    "sterile", "annex 1",
    "supplier qualification",
    # OpenFDA Recall 특화 — 경구 고형제 failure mode (v15.1 추가)
    "dissolution", "assay failure", "out of specification",
    "particulate matter", "particulate contamination",
    "subpotent", "superpotent", "mislabeling", "mislabelled",
    "endotoxin",
    # Nitrosamine 계열 (FDA hot topic)
    "nitrosamine", "ndma", "ndea", "n-nitroso",
    # 주요 generic 제조사 (경쟁사 학습 가치)
    "alkem", "aurobindo", "lupin", "zydus",
    "dr. reddy", "dr reddy",
]

# Likely 가산 키워드 (경구 고형제 · 정제 직접 연관)
QA_LIKELY_BOOST = [
    "tablet", "capsule", "oral solid", "solid dosage",
    "warning letter", "dissolution", "uniformity of dosage",
    "data integrity", "annex 1", "cgmp",
    # Recall 고신호 failure mode (v15.1 추가)
    "dissolution failure", "failed dissolution",
    "nitrosamine impurity", "ndma impurity",
]

# 명시 제외 (medical device · 화장품 · 식품 · 백신 단독 등)
# 주의: "food safety" 는 단어 경계 매칭이므로 "food safety" + "drug GMP" 동시 포함 문서는
# 아래 강력 키워드 로직으로 Possible 로 살아남음
QA_EXCLUDE_KEYWORDS = [
    "medical device", "device only",
    "cosmetic", "cosmetics",
    "food safety", "dietary supplement label",
    "veterinary only", "animal drug only",
]

# 13 개 카테고리 통과를 위한 최소 매칭 키워드 수
QA_MIN_MATCH = 1

# OSD (경구 고형제) Relevance 분류 기준 (v15.1 추가)
PROP_OSD_RELEVANCE = "OSD Relevance"   # Notion select: Direct / Indirect / N/A
OSD_ROUTES = {"oral"}
OSD_FORMS = {
    "tablet", "capsule", "extended-release tablet", "er tablet",
    "delayed-release tablet", "chewable tablet", "orally disintegrating tablet",
    "powder for oral solution", "oral solution", "oral suspension",
}

FR_PER_PAGE = 100  # API 최대치
OPENFDA_LIMIT = 100  # no-key 한도, key 있어도 안전치
OPENFDA_MAX_TOTAL = 200  # 안전 상한 (의약품 리콜 주간 통상 < 50)

NOTION_RICH_TEXT_CHUNK = 1900  # 2000 한도, 여유 100
NOTION_CODE_BLOCK_CHUNK = 1900


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IntakeItem:
    source: str  # SOURCE_FR | SOURCE_RECALL | SOURCE_EMA | SOURCE_MHRA | SOURCE_PICS | SOURCE_ECA | SOURCE_FDA_WL
    document_id: str
    date_iso: str  # YYYY-MM-DD
    headline: str
    official_url: str
    type_or_class: str = ""
    firm: str = ""
    body: str = ""
    distribution: str = ""
    comments_close_iso: str = ""
    api_query: str = ""
    qa_relevance: str = "Pending"
    osd_relevance: str = "N/A"   # "Direct" | "Indirect" | "N/A" — Recall 전용, 기타 N/A
    source_type: str = SRC_TYPE_OFFICIAL_API  # Source Type 분류 (v15.1)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionStats:
    # ── Phase 1 (Official API) ───────────────────────────────────────────────
    fr_fetched: int = 0
    fr_inserted: int = 0
    fr_skipped_dup: int = 0
    fr_insert_failed: int = 0       # Notion 삽입 최종 실패 건수
    fr_truncated: bool = False      # FR pagination 안전 상한 초과 여부
    fr_error: bool = False
    fr_error_msg: str = ""
    recall_fetched: int = 0
    recall_inserted: int = 0
    recall_skipped_dup: int = 0
    recall_insert_failed: int = 0   # Notion 삽입 최종 실패 건수
    recall_truncated: bool = False  # OPENFDA_MAX_TOTAL 상한 초과 여부 (v15.1)
    recall_error: bool = False
    recall_error_msg: str = ""
    # ── Phase 2 (RSS / HTML) ────────────────────────────────────────────────
    ema_fetched: int = 0
    ema_inserted: int = 0
    ema_skipped_dup: int = 0
    ema_insert_failed: int = 0
    ema_error: bool = False
    ema_error_msg: str = ""
    mhra_fetched: int = 0
    mhra_inserted: int = 0
    mhra_skipped_dup: int = 0
    mhra_insert_failed: int = 0
    mhra_error: bool = False
    mhra_error_msg: str = ""
    pics_fetched: int = 0
    pics_inserted: int = 0
    pics_skipped_dup: int = 0
    pics_insert_failed: int = 0
    pics_error: bool = False
    pics_error_msg: str = ""
    eca_fetched: int = 0
    eca_inserted: int = 0
    eca_skipped_dup: int = 0
    eca_insert_failed: int = 0
    eca_error: bool = False
    eca_error_msg: str = ""
    wl_fetched: int = 0
    wl_inserted: int = 0
    wl_skipped_dup: int = 0
    wl_insert_failed: int = 0
    wl_error: bool = False
    wl_error_msg: str = ""

    def has_insert_failures(self) -> bool:
        return (
            self.fr_insert_failed > 0 or self.recall_insert_failed > 0
            or self.ema_insert_failed > 0 or self.mhra_insert_failed > 0
            or self.pics_insert_failed > 0 or self.eca_insert_failed > 0
            or self.wl_insert_failed > 0
        )

    def summary(self) -> str:
        fr_warn = " ⚠️ TRUNCATED" if self.fr_truncated else ""
        rec_warn = " ⚠️ TRUNCATED" if self.recall_truncated else ""
        lines = [
            f"FR   fetched={self.fr_fetched}  inserted={self.fr_inserted}  "
            f"skip_dup={self.fr_skipped_dup}  failed={self.fr_insert_failed}  "
            f"error={self.fr_error}{fr_warn}",
            f"REC  fetched={self.recall_fetched}  inserted={self.recall_inserted}  "
            f"skip_dup={self.recall_skipped_dup}  failed={self.recall_insert_failed}  "
            f"error={self.recall_error}{rec_warn}",
            f"EMA  fetched={self.ema_fetched}  inserted={self.ema_inserted}  "
            f"skip_dup={self.ema_skipped_dup}  failed={self.ema_insert_failed}  "
            f"error={self.ema_error}",
            f"MHRA fetched={self.mhra_fetched}  inserted={self.mhra_inserted}  "
            f"skip_dup={self.mhra_skipped_dup}  failed={self.mhra_insert_failed}  "
            f"error={self.mhra_error}",
            f"PICS fetched={self.pics_fetched}  inserted={self.pics_inserted}  "
            f"skip_dup={self.pics_skipped_dup}  failed={self.pics_insert_failed}  "
            f"error={self.pics_error}",
            f"ECA  fetched={self.eca_fetched}  inserted={self.eca_inserted}  "
            f"skip_dup={self.eca_skipped_dup}  failed={self.eca_insert_failed}  "
            f"error={self.eca_error}",
            f"WL   fetched={self.wl_fetched}  inserted={self.wl_inserted}  "
            f"skip_dup={self.wl_skipped_dup}  failed={self.wl_insert_failed}  "
            f"error={self.wl_error}",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────


def log(level: str, msg: str) -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {level} {msg}", flush=True)


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def kst_run_date(now: datetime | None = None) -> date:
    """KST 기준 '오늘' 의 자정 날짜."""
    return (now or now_kst()).astimezone(KST).date()


def date_window(run_date: date, window_days: int = 7) -> tuple[date, date]:
    start = run_date - timedelta(days=window_days)
    return start, run_date


def truncate(text: str, limit: int = NOTION_RICH_TEXT_CHUNK) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def chunk_text(text: str, size: int = NOTION_RICH_TEXT_CHUNK) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


class HTTPClientError(RuntimeError):
    """4xx HTTP 에러 전용 예외 — status_code 속성으로 정확한 판별 가능."""
    def __init__(self, status_code: int, url: str, msg: str = "") -> None:
        super().__init__(msg or f"HTTP {status_code} for {url}")
        self.status_code = status_code


def http_get_json(url: str, *, params: dict[str, Any] | None = None,
                  timeout: int = 30, retries: int = 2) -> dict[str, Any]:
    """JSON GET 요청. 4xx 는 HTTPClientError (재시도 없음), 5xx/네트워크 오류는 지수 백오프 재시도."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": "GRS-Intake/1.0 (+github-actions)"})
            if 400 <= resp.status_code < 500:
                # 4xx 는 재시도해도 동일 결과 — 즉시 HTTPClientError 발생
                raise HTTPClientError(resp.status_code, url,
                                      f"HTTP {resp.status_code} for {url}")
            resp.raise_for_status()
            return resp.json()
        except HTTPClientError:
            raise  # 재시도 없이 전파
        except requests.RequestException as e:  # noqa: PERF203
            last_err = e
            log("WARN", f"GET 실패 ({attempt + 1}/{retries + 1}) url={url} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP GET 최종 실패: {url} ({last_err})")


# ─────────────────────────────────────────────────────────────────────────────
# RSS / Atom / HTML 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

_RSS_HEADERS = {
    "User-Agent": "GRS-Intake/1.1 (+github-actions)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}

# XML 네임스페이스
_NS_ATOM  = "http://www.w3.org/2005/Atom"
_NS_DC    = "http://purl.org/dc/elements/1.1/"
_NS_DCTERMS = "http://purl.org/dc/terms/"
_NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"


def http_get_xml(url: str, *, timeout: int = 30, retries: int = 2) -> ET.Element:
    """XML GET 요청 → ElementTree root 반환. 4xx=HTTPClientError, 5xx 재시도."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers=_RSS_HEADERS)
            if 400 <= resp.status_code < 500:
                raise HTTPClientError(resp.status_code, url)
            resp.raise_for_status()
            return ET.fromstring(resp.content)
        except HTTPClientError:
            raise
        except ET.ParseError as e:
            raise RuntimeError(f"XML 파싱 실패: {url} — {e}") from e
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"XML GET 실패 ({attempt + 1}/{retries + 1}) url={url} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP XML GET 최종 실패: {url} ({last_err})")


def _rss_text(el: ET.Element | None) -> str:
    """ElementTree 텍스트 안전 추출."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _rss_find(parent: ET.Element, *tags: str) -> ET.Element | None:
    """네임스페이스 없는 태그 및 네임스페이스 조합 순차 탐색."""
    for tag in tags:
        el = parent.find(tag)
        if el is not None:
            return el
    return None


def _parse_rss2_date(raw: str) -> str:
    """RFC 2822 (RSS 2.0) 날짜 → YYYY-MM-DD. 실패 시 "" 반환."""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.date().isoformat()
    except Exception:
        pass
    # 일부 피드가 ISO 8601 을 쓰는 경우 폴백
    return _parse_atom_date(raw)


def _parse_atom_date(raw: str) -> str:
    """Atom/ISO 8601 날짜 → YYYY-MM-DD. 실패 시 "" 반환."""
    if not raw:
        return ""
    # 공통 패턴: 2024-03-15T12:00:00Z / 2024-03-15T12:00:00+00:00 / 2024-03-15
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:25], fmt).date().isoformat()
        except ValueError:
            continue
    # fromisoformat (Python 3.11+) 폴백
    try:
        return datetime.fromisoformat(raw[:25].rstrip("Z")).date().isoformat()
    except ValueError:
        pass
    log("WARN", f"날짜 파싱 실패 (atom): {raw!r}")
    return ""


def _stable_doc_id(source: str, title: str, url: str, date_iso: str) -> str:
    """RSS 항목에 고유 document_id 가 없을 경우 콘텐츠 기반 안정 ID 생성.

    SHA-1 상위 12자리 사용 — URL+제목+날짜 조합이므로 동일 항목은 동일 ID 를 가진다.
    dedupe 목적에 충분한 고유성을 제공한다.
    """
    key = f"{source}|{url}|{title}|{date_iso}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _within_window(date_iso: str, start: date, end: date) -> bool:
    """date_iso (YYYY-MM-DD) 가 [start, end] 구간에 포함 여부."""
    if not date_iso:
        return False
    try:
        d = date.fromisoformat(date_iso)
        return start <= d <= end
    except ValueError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RSS 수집 공통 파서
# ─────────────────────────────────────────────────────────────────────────────


def _rss2_items_from_root(root: ET.Element) -> list[ET.Element]:
    """RSS 2.0 channel → item 리스트. RDF(rss/1.0) 도 처리."""
    channel = root.find("channel")
    if channel is not None:
        return channel.findall("item")
    return root.findall("item")


def _atom_entries_from_root(root: ET.Element) -> list[ET.Element]:
    """Atom feed → entry 리스트. 네임스페이스 있는 경우 처리."""
    entries = root.findall(f"{{{_NS_ATOM}}}entry")
    if entries:
        return entries
    return root.findall("entry")


def _atom_text(entry: ET.Element, tag: str) -> str:
    """Atom 네임스페이스 포함 텍스트 추출 (네임스페이스 있는 버전, 없는 버전 모두)."""
    el = entry.find(f"{{{_NS_ATOM}}}{tag}") or entry.find(tag)
    if el is None:
        return ""
    # <content> / <summary> 등의 type="html" 처리
    text = (el.text or "").strip()
    # HTML 태그 간단 제거
    return re.sub(r"<[^>]+>", " ", text).strip()


def _atom_link(entry: ET.Element) -> str:
    """Atom <link href="..."> 추출."""
    # href 속성을 가진 link 먼저
    for link in entry.findall(f"{{{_NS_ATOM}}}link"):
        href = link.get("href", "")
        if href and link.get("rel", "alternate") == "alternate":
            return href
    for link in entry.findall(f"{{{_NS_ATOM}}}link"):
        href = link.get("href", "")
        if href:
            return href
    # 네임스페이스 없는 버전
    el = entry.find("link")
    return _rss_text(el)


# ─────────────────────────────────────────────────────────────────────────────
# QA Relevance 휴리스틱
# ─────────────────────────────────────────────────────────────────────────────


def _kw_match(blob: str, keywords: list[str]) -> int:
    """단어 경계(\b) 기반 키워드 매칭 카운트.
    복합어("manufacturing practice")는 전체 구문을 단어 경계로 감쌈.
    단독 약어("oos", "oot", "pqs") 오탐 방지.
    """
    count = 0
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, blob):
            count += 1
    return count


def _kw_any(blob: str, keywords: list[str]) -> bool:
    return _kw_match(blob, keywords) > 0


def compute_relevance(*text_parts: str) -> str:
    blob = " ".join(t for t in text_parts if t).lower()
    if not blob.strip():
        return "Pending"
    if _kw_any(blob, QA_EXCLUDE_KEYWORDS):
        # 명시 제외 키워드가 있어도 Likely 가산 키워드 2개 이상이면 Possible 로 구제
        strong = _kw_match(blob, QA_LIKELY_BOOST)
        if strong >= 2:
            return "Possible"
        return "Unrelated"
    matches = _kw_match(blob, QA_CATEGORY_KEYWORDS)
    if matches < QA_MIN_MATCH:
        return "Pending"
    boosts = _kw_match(blob, QA_LIKELY_BOOST)
    if boosts >= 1:
        return "Likely"
    return "Possible"


# ─────────────────────────────────────────────────────────────────────────────
# Federal Register 수집
# ─────────────────────────────────────────────────────────────────────────────
# OSD Relevance 분류 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def _as_lower_set(value: Any) -> set[str]:
    """openfda.route / dosage_form 필드를 안전하게 소문자 set으로 변환.

    OpenFDA API 는 list[str] 를 반환하는 것이 정상이지만,
    string / None / 기타 타입이 오더라도 예외 없이 처리한다.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.lower()}
    if isinstance(value, list):
        return {str(v).lower() for v in value if v}
    return {str(value).lower()}


# 경구 고형제 판정에 사용하는 부분문자열 토큰 (exact set 매칭 대신)
OSD_SOLID_TERMS = [
    "tablet", "capsule", "oral solid", "solid dosage",
    "extended-release", "delayed-release",
    "orally disintegrating", "chewable",
]


def compute_osd_relevance(raw_payload: dict[str, Any]) -> str:
    """OpenFDA raw payload 에서 경구 고형제(OSD) 직접 관련성 판정.

    분류 기준 (v15.1 개선):
        "Direct"   — dosage_form 에 tablet/capsule/oral solid 계열 단어 포함
                     (exact match 가 아닌 부분문자열 매칭으로 복합 형태 처리)
        "Indirect" — tablet/capsule 확인 안 됐지만 route=oral 이거나
                     product_description 에 경구 단서 있음
        "N/A"      — 경구/고형제 근거 없음

    설계 의도:
        시스템 목표가 "경구 고형제(정제) 중심"이므로
        oral solution/suspension 은 route=oral 이더라도 Direct 가 아닌 Indirect 로 분류.
        Recall Tier 분류에서 Direct → Tier 2/3 후보, Indirect → 경계 항목으로 재확인.
    """
    openfda = raw_payload.get("openfda") or {}
    routes = _as_lower_set(openfda.get("route"))
    forms = _as_lower_set(openfda.get("dosage_form"))

    # 1순위: dosage_form 에 고형제 토큰 포함 여부 (부분문자열)
    if any(term in f for f in forms for term in OSD_SOLID_TERMS):
        return "Direct"

    # 2순위: route=oral 이면 경구 투여 확인 → Indirect (oral solution/suspension 포함)
    if "oral" in routes:
        return "Indirect"

    # 3순위: openfda 필드 없거나 미제공 시 product_description 에서 단서 탐색
    product = (raw_payload.get("product_description") or "").lower()
    if re.search(r"\b(tablets?|capsules?|oral)\b", product):
        return "Indirect"

    return "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# 날짜 유틸
# ─────────────────────────────────────────────────────────────────────────────


def _safe_date_iso(value: str, context: str = "") -> str:
    """날짜 문자열 검증 후 YYYY-MM-DD 반환. 실패 시 "" 반환 + WARN 로그.

    지원 포맷:
        - YYYY-MM-DD (ISO 8601)
        - YYYYMMDD   (OpenFDA report_date 형식) → 자동 변환
    """
    if not value:
        return ""
    # YYYYMMDD → YYYY-MM-DD
    if len(value) == 8 and value.isdigit():
        value = f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    try:
        datetime.fromisoformat(value)
        return value
    except ValueError:
        log("WARN", f"날짜 파싱 실패 (context={context}): {value!r} → 빈 문자열 처리")
        return ""


# ─────────────────────────────────────────────────────────────────────────────


def collect_federal_register(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Federal Register 문서 지난 7 일 전수 수집 (pagination 처리)."""
    params: dict[str, Any] = {
        "conditions[agencies][]": "food-and-drug-administration",
        "conditions[publication_date][gte]": start.isoformat(),
        "conditions[publication_date][lte]": end.isoformat(),
        "per_page": FR_PER_PAGE,
        "order": "newest",
    }
    api_query_url = FR_API_BASE + "?" + urllib.parse.urlencode(params, doseq=True)
    log("INFO", f"FR API 호출: {api_query_url}")

    items: list[IntakeItem] = []
    next_url: str | None = api_query_url
    page_count = 0
    try:
        while next_url:
            page_count += 1
            if page_count > 10:
                msg = (f"FR pagination 10페이지 상한 초과 — truncated "
                       f"(수집 {len(items)}건, 이후 항목 누락 가능)")
                log("WARN", msg)
                return items, msg   # fr_error=True 로 집계됨
            data = http_get_json(next_url)
            results = data.get("results", []) or []
            log("INFO", f"FR page {page_count}: {len(results)} 건")
            for r in results:
                items.append(_fr_to_item(r, api_query_url))
            next_url = data.get("next_page_url")
        return items, None
    except Exception as e:
        log("ERROR", f"FR 수집 실패: {e}")
        return items, str(e)


def _fr_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    doc_id = str(r.get("document_number") or "").strip()
    title = (r.get("title") or "").strip()
    pub = _safe_date_iso((r.get("publication_date") or "").strip(),
                         context=f"FR/{doc_id}")
    html_url = (r.get("html_url") or "").strip()
    doc_type = (r.get("type") or "").strip()
    abstract = (r.get("abstract") or "").strip()
    comments_close = _safe_date_iso((r.get("comments_close_on") or "").strip(),
                                    context=f"FR/{doc_id}/comments_close")

    relevance = compute_relevance(title, abstract, doc_type)

    return IntakeItem(
        source=SOURCE_FR,
        document_id=doc_id,
        date_iso=pub,
        headline=title,
        official_url=html_url,
        type_or_class=doc_type,
        body=abstract,
        comments_close_iso=comments_close,
        api_query=api_query_url,
        qa_relevance=relevance,
        # FR 항목은 OSD Relevance 분류 대상 아님
        osd_relevance="N/A",
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OpenFDA Recall 수집
# ─────────────────────────────────────────────────────────────────────────────


def collect_openfda_recalls(start: date, end: date,
                            api_key: str | None) -> tuple[list[IntakeItem], str | None]:
    """OpenFDA Drug Enforcement 항목 지난 7 일 전수 수집."""
    search = f"report_date:[{start.strftime('%Y%m%d')}+TO+{end.strftime('%Y%m%d')}]"
    items: list[IntakeItem] = []
    skip = 0
    api_query_url_for_log: str | None = None
    try:
        while True:
            params = [
                ("search", search),
                ("limit", str(OPENFDA_LIMIT)),
                ("skip", str(skip)),
            ]
            if api_key:
                params.append(("api_key", api_key))
            url = OPENFDA_API_BASE + "?" + urllib.parse.urlencode(params, safe=":[]+")
            if api_query_url_for_log is None:
                # api_key 는 로그·Notion 저장에서 마스킹
                api_query_url_for_log = _mask_api_key(url)
                log("INFO", f"OpenFDA 호출: {api_query_url_for_log}")
            try:
                data = http_get_json(url)
            except HTTPClientError as e:
                # OpenFDA 는 해당 기간 결과 0건일 때 404 를 반환하는 것이 관행
                if e.status_code == 404:
                    log("INFO", "OpenFDA 404 (해당 기간 결과 0건) — 정상 종료")
                    return items, None
                # 404 외 4xx (401, 403 등) 는 실제 에러로 처리
                raise RuntimeError(str(e)) from e
            results = data.get("results", []) or []
            meta_total = (data.get("meta", {}).get("results", {}) or {}).get("total", 0)
            log("INFO", f"OpenFDA skip={skip}: {len(results)} 건 (total {meta_total})")
            for r in results:
                items.append(_recall_to_item(r, api_query_url_for_log or url))
            skip += len(results)
            if not results or skip >= meta_total:
                break
            if skip >= OPENFDA_MAX_TOTAL:
                msg = (f"OpenFDA OPENFDA_MAX_TOTAL({OPENFDA_MAX_TOTAL}) 상한 초과 — truncated "
                       f"(수집 {len(items)}건, meta.total={meta_total}, 이후 항목 누락 가능)")
                log("WARN", msg)
                return items, msg   # recall_truncated=True 로 집계됨
        return items, None
    except Exception as e:
        log("ERROR", f"OpenFDA 수집 실패: {e}")
        return items, str(e)


def _mask_api_key(url: str) -> str:
    return re.sub(r"(api_key=)[^&]+", r"\1***REDACTED***", url)


def _recall_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    recall_number = str(r.get("recall_number") or "").strip()
    classification = (r.get("classification") or "").strip()
    product = (r.get("product_description") or "").strip()
    reason = (r.get("reason_for_recall") or "").strip()
    firm = (r.get("recalling_firm") or "").strip()
    distribution = (r.get("distribution_pattern") or "").strip()
    report_date_raw = (r.get("report_date") or "").strip()  # YYYYMMDD
    product_type = (r.get("product_type") or "").strip()

    # report_date YYYYMMDD → YYYY-MM-DD (_safe_date_iso 가 변환 + 검증 처리)
    date_iso = _safe_date_iso(report_date_raw, context=f"Recall/{recall_number}")

    headline = product or firm or recall_number
    relevance = compute_relevance(product, reason, firm, distribution, product_type)
    osd_rel = compute_osd_relevance(r)

    return IntakeItem(
        source=SOURCE_RECALL,
        document_id=recall_number,
        date_iso=date_iso,
        headline=headline,
        official_url=FDA_RECALLS_L2,  # OpenFDA 는 항목별 URL 부재 — L2 고정
        type_or_class=classification,
        firm=firm,
        body=reason,
        distribution=distribution,
        api_query=api_query_url,
        qa_relevance=relevance,
        osd_relevance=osd_rel,
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# EMA RSS 수집 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def collect_ema_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """EMA 공식 RSS 피드 4개 (scientific-guidelines · inspections · news ·
    regulatory-guidelines) 를 수집해 날짜 필터링 후 반환.

    Source Type: Official API (EMA 공식 RSS 피드).
    Evidence Level: A 불가 — RSS 요약이므로 B (Official direct identified) 이상.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []

    for feed_name, feed_url in EMA_RSS_FEEDS.items():
        log("INFO", f"EMA RSS 수집: {feed_name} ({feed_url})")
        try:
            root = http_get_xml(feed_url)
        except Exception as e:
            msg = f"EMA RSS '{feed_name}' 실패: {e}"
            log("WARN", msg)
            errors.append(msg)
            continue

        # RSS 2.0 형식 확인
        rss_items = _rss2_items_from_root(root)
        for el in rss_items:
            title = _rss_text(el.find("title"))
            link  = _rss_text(el.find("link"))
            # <link> 가 CDATA 로 감싸진 경우 텍스트에 없고 tail 에 있을 수 있음
            if not link:
                link_el = el.find("link")
                if link_el is not None:
                    link = (link_el.tail or "").strip()
            pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
            # EMA 피드에 따라 dc:date 를 fallback 으로 사용
            if not pub_raw:
                dc_date = el.find(f"{{{_NS_DC}}}date") or el.find(f"{{{_NS_DCTERMS}}}modified")
                pub_raw = _rss_text(dc_date)
            date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
            # dc:date 가 Atom 형식인 경우 재시도
            if not date_iso and pub_raw:
                date_iso = _parse_atom_date(pub_raw)

            description = _rss_text(el.find("description"))
            category_el = el.find("category")
            category = _rss_text(category_el)
            guid_el = el.find("guid")
            guid = _rss_text(guid_el) or link

            if not _within_window(date_iso, start, end):
                continue

            doc_id = _stable_doc_id(SOURCE_EMA, title, link, date_iso)
            relevance = compute_relevance(title, description, category)

            items.append(IntakeItem(
                source=SOURCE_EMA,
                document_id=doc_id,
                date_iso=date_iso,
                headline=title,
                official_url=link,
                type_or_class=category or feed_name,
                body=description,
                api_query=feed_url,
                qa_relevance=relevance,
                osd_relevance="N/A",
                source_type=SRC_TYPE_OFFICIAL_API,
                raw_payload={
                    "feed": feed_name,
                    "title": title,
                    "link": link,
                    "pubDate": pub_raw,
                    "description": description,
                    "category": category,
                    "guid": guid,
                },
            ))

    err_msg = "; ".join(errors) if errors else None
    # 오류가 있어도 다른 피드에서 수집한 항목은 반환 (graceful degradation)
    log("INFO", f"EMA RSS 수집 완료: {len(items)}건 (errors={len(errors)})")
    return items, err_msg if errors else None


# ─────────────────────────────────────────────────────────────────────────────
# MHRA Inspectorate RSS 수집 (v15.1) — Atom 형식
# ─────────────────────────────────────────────────────────────────────────────


def collect_mhra_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """MHRA Inspectorate Blog RSS (Atom 형식) 수집.

    Source Type: Official Regulator Blog.
    URL: https://mhrainspectorate.blog.gov.uk/feed/
    """
    log("INFO", f"MHRA RSS 수집: {MHRA_RSS_URL}")
    try:
        root = http_get_xml(MHRA_RSS_URL)
    except Exception as e:
        log("WARN", f"MHRA RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    entries = _atom_entries_from_root(root)

    for entry in entries:
        title   = _atom_text(entry, "title")
        link    = _atom_link(entry)
        # Atom: <updated> 또는 <published>
        pub_raw = (
            _rss_text(entry.find(f"{{{_NS_ATOM}}}published"))
            or _rss_text(entry.find(f"{{{_NS_ATOM}}}updated"))
            or _rss_text(entry.find("published"))
            or _rss_text(entry.find("updated"))
        )
        date_iso = _parse_atom_date(pub_raw)
        summary  = _atom_text(entry, "summary") or _atom_text(entry, "content")

        # category
        cat_el = entry.find(f"{{{_NS_ATOM}}}category") or entry.find("category")
        category = (cat_el.get("term", "") if cat_el is not None else "").strip()

        # Atom id 를 document_id 로 사용
        id_el = entry.find(f"{{{_NS_ATOM}}}id") or entry.find("id")
        guid  = _rss_text(id_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_MHRA, title, link, date_iso)
        relevance = compute_relevance(title, summary, category)

        items.append(IntakeItem(
            source=SOURCE_MHRA,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class=category or "Blog",
            body=summary,
            api_query=MHRA_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_BLOG,
            raw_payload={
                "title": title, "link": link,
                "published": pub_raw, "summary": summary,
                "category": category, "id": guid,
            },
        ))

    log("INFO", f"MHRA RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# PIC/S RSS 수집 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def collect_pics_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """PIC/S 공식 RSS 수집.

    Source Type: Official Regulatory Page.
    URL: https://picscheme.org/rss/general_en.rss
    """
    log("INFO", f"PIC/S RSS 수집: {PICS_RSS_URL}")
    try:
        root = http_get_xml(PICS_RSS_URL)
    except Exception as e:
        log("WARN", f"PIC/S RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    rss_items = _rss2_items_from_root(root)

    for el in rss_items:
        title   = _rss_text(el.find("title"))
        link    = _rss_text(el.find("link"))
        pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
        date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
        description = _rss_text(el.find("description"))
        guid_el = el.find("guid")
        guid    = _rss_text(guid_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_PICS, title, link, date_iso)
        relevance = compute_relevance(title, description)

        items.append(IntakeItem(
            source=SOURCE_PICS,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class="PIC/S",
            body=description,
            api_query=PICS_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            raw_payload={
                "title": title, "link": link,
                "pubDate": pub_raw, "description": description, "guid": guid,
            },
        ))

    log("INFO", f"PIC/S RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# ECA Academy RSS 수집 (v15.1) — Expert Secondary
# ─────────────────────────────────────────────────────────────────────────────


def collect_eca_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """ECA Academy (gmp-compliance.org) RSS 수집.

    Source Type: Expert Secondary — FDA·EMA·MHRA·TGA·PIC/S·ICH 전문 GMP 뉴스 큐레이션.
    URL: https://app.gxp-services.net/eca_newsfeed.xml
    403 발생 시 운영 경고 없이 진행 (Expert Secondary 허용 정책).
    """
    log("INFO", f"ECA RSS 수집: {ECA_RSS_URL}")
    try:
        root = http_get_xml(ECA_RSS_URL)
    except HTTPClientError as e:
        # Expert Secondary: 403/404 는 경고 없이 넘어감
        log("INFO", f"ECA RSS HTTP {e.status_code} — 건너뜀 (Expert Secondary 정책)")
        return [], None
    except Exception as e:
        log("WARN", f"ECA RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    # ECA 피드는 RSS 2.0 또는 Atom 모두 가능 — 두 방향 시도
    rss_items = _rss2_items_from_root(root)
    if not rss_items:
        rss_items = _atom_entries_from_root(root)  # type: ignore[assignment]

    for el in rss_items:
        # RSS 2.0 태그 우선, Atom 폴백
        title = (
            _rss_text(el.find("title"))
            or _atom_text(el, "title")
        )
        link = (
            _rss_text(el.find("link"))
            or _atom_link(el)
        )
        pub_raw = (
            _rss_text(el.find("pubDate"))
            or _rss_text(el.find("pubdate"))
            or _rss_text(el.find(f"{{{_NS_ATOM}}}published"))
            or _rss_text(el.find("published"))
        )
        date_iso = (
            _parse_rss2_date(pub_raw) if pub_raw
            else _parse_atom_date(pub_raw)
        )
        description = (
            _rss_text(el.find("description"))
            or _atom_text(el, "summary")
        )
        guid_el = el.find("guid")
        guid    = _rss_text(guid_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_ECA, title, link, date_iso)
        relevance = compute_relevance(title, description)

        items.append(IntakeItem(
            source=SOURCE_ECA,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class="GMP News",
            body=description,
            api_query=ECA_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_EXPERT_SECONDARY,
            raw_payload={
                "title": title, "link": link,
                "pubDate": pub_raw, "description": description, "guid": guid,
            },
        ))

    log("INFO", f"ECA RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# FDA Warning Letters 수집 (v15.1) — HTML 파싱
# ─────────────────────────────────────────────────────────────────────────────


class _FDAWLTableParser(HTMLParser):
    """FDA Warning Letters 페이지 HTML 에서 테이블 행을 파싱.

    대상 테이블은 class="table" 이며 열 순서:
      Posted Date | Recipient | Letter Issue Date | Issuing Office | Subject (or close match)
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table: bool = False
        self._in_row: bool = False
        self._in_cell: bool = False
        self._cell_depth: int = 0
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._current_href: str = ""
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table" and "table" in (attr_dict.get("class") or ""):
            self._in_table = True
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        if tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_depth = 1
            self._current_cell = []
            self._current_href = ""
        elif tag == "a" and self._in_cell:
            href = attr_dict.get("href") or ""
            if href and not self._current_href:
                self._current_href = href
        elif tag in ("td", "th") and self._in_cell:
            self._cell_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cell_depth -= 1
            if self._cell_depth <= 0:
                cell_text = " ".join(self._current_cell).strip()
                # href 와 함께 저장
                self._current_row.append(
                    f"{cell_text}|HREF:{self._current_href}" if self._current_href else cell_text
                )
                self._in_cell = False
        if tag == "tr" and self._in_row:
            self._in_row = False
            if len(self._current_row) >= 4:
                self.rows.append({"_cols": self._current_row})
        if tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell.append(stripped)


def _parse_wl_date(raw: str) -> str:
    """FDA WL 날짜 형식 (MM/DD/YYYY 또는 YYYY-MM-DD) → YYYY-MM-DD."""
    raw = raw.strip()
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        try:
            return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
        except ValueError:
            pass
    return _safe_date_iso(raw, context="FDA_WL")


def collect_fda_warning_letters(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Warning Letters 페이지 HTML 테이블 파싱 수집.

    Source Type: Official Regulatory Page.
    페이지: https://www.fda.gov/.../warning-letters

    FDA WL 페이지는 정적 HTML 테이블을 포함하므로 WebFetch 가능.
    JS-heavy 인 경우 content 부재 → 빈 결과 반환 (fail-silent).
    403/timeout 시 WARN 로그 후 빈 결과 반환.
    """
    log("INFO", f"FDA WL 수집: {FDA_WL_URL}")
    try:
        resp = requests.get(FDA_WL_URL, timeout=30, headers={
            "User-Agent": "GRS-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", "FDA WL 403 — HTML 수집 불가, 이번 주 WL 슬롯 건너뜀")
            return [], "HTTP 403"
        resp.raise_for_status()
        html_text = resp.text
    except requests.RequestException as e:
        log("WARN", f"FDA WL HTTP 실패: {e}")
        return [], str(e)

    parser = _FDAWLTableParser()
    parser.feed(html_text)

    if not parser.rows:
        log("INFO", "FDA WL HTML 테이블 미발견 — JS-rendered 가능성 (건너뜀)")
        return [], None

    items: list[IntakeItem] = []
    for row in parser.rows:
        cols = row.get("_cols", [])
        if len(cols) < 4:
            continue
        # 실제 FDA WL 테이블 열 순서 (2026년 5월 확인):
        # [0] Posted Date  [1] Letter Issue Date  [2] Company Name(+href)
        # [3] Issuing Office  [4] Subject  [5] Response Letter  [6+] 기타
        posted_raw = cols[0].split("|HREF:")[0].strip()

        # 헤더 행 건너뜀 (col[0]이 날짜 패턴이 아닌 텍스트)
        if not re.match(r"^\d", posted_raw):
            continue

        letter_date_raw = cols[1].split("|HREF:")[0].strip() if len(cols) > 1 else ""

        # Company Name — href 포함 가능
        recipient_raw = cols[2] if len(cols) > 2 else ""
        wl_href = ""
        if "|HREF:" in recipient_raw:
            parts = recipient_raw.split("|HREF:", 1)
            recipient_raw = parts[0].strip()
            wl_href = parts[1].strip()

        issuing_office = cols[3].split("|HREF:")[0].strip() if len(cols) > 3 else ""
        subject = cols[4].split("|HREF:")[0].strip() if len(cols) > 4 else ""

        # Posted date 가 주 우선 날짜
        date_iso = _parse_wl_date(posted_raw) or _parse_wl_date(letter_date_raw)

        if not _within_window(date_iso, start, end):
            continue

        # URL 정규화
        if wl_href and wl_href.startswith("/"):
            wl_href = "https://www.fda.gov" + wl_href

        firm = recipient_raw
        headline = subject or firm or "FDA Warning Letter"
        doc_id = _stable_doc_id(SOURCE_FDA_WL, firm, wl_href or FDA_WL_URL, date_iso)
        relevance = compute_relevance(headline, subject, issuing_office)

        items.append(IntakeItem(
            source=SOURCE_FDA_WL,
            document_id=doc_id,
            date_iso=date_iso,
            headline=headline,
            official_url=wl_href or FDA_WL_URL,
            type_or_class=issuing_office or "Warning Letter",
            firm=firm,
            body=subject,
            api_query=FDA_WL_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            raw_payload={
                "firm": firm, "posted_date": posted_raw,
                "letter_date": letter_date_raw,
                "issuing_office": issuing_office,
                "subject": subject, "url": wl_href,
            },
        ))

    log("INFO", f"FDA WL 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# Notion 헬퍼
# ─────────────────────────────────────────────────────────────────────────────


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


class NotionDedupeQueryError(RuntimeError):
    """Notion 중복 조회 실패 전용 예외 — insert 중단 판단에 사용."""
    pass


def notion_query_existing_doc_ids(token: str, db_id: str, run_date: date) -> set[str]:
    """오늘(KST) Run Date 와 일치하는 row 의 'source::document_id' key set 반환.

    dedupe key 형식: "{SOURCE_FR}::{doc_id}" 또는 "{SOURCE_RECALL}::{doc_id}"
    Source 를 포함해 Federal Register 와 OpenFDA Recall 간 ID 충돌을 방지한다.

    Raises:
        NotionDedupeQueryError: 조회 실패 시 — caller 가 insert 중단 여부를 결정.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    existing: set[str] = set()
    body: dict[str, Any] = {
        "filter": {
            "property": PROP_RUN_DATE,
            "date": {"equals": run_date.isoformat()},
        },
        "page_size": 100,
    }
    start_cursor: str | None = None
    try:
        for _ in range(20):  # 안전 페이지 상한
            if start_cursor:
                body["start_cursor"] = start_cursor
            elif "start_cursor" in body:
                del body["start_cursor"]
            resp = requests.post(url, json=body, headers=notion_headers(token), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            for pg in data.get("results", []):
                props = pg.get("properties", {})
                # Source
                src = (props.get(PROP_SOURCE, {}).get("select") or {}).get("name", "")
                # Document ID
                doc_id_arr = props.get(PROP_DOC_ID, {}).get("rich_text", [])
                doc_id = "".join(rt.get("plain_text", "") for rt in doc_id_arr).strip()
                if src and doc_id:
                    existing.add(f"{src}::{doc_id}")
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
    except requests.RequestException as e:
        # 중복 조회 실패 시 빈 set을 반환하면 모든 item을 신규로 판단해 대량 중복 insert 위험.
        # 안전하게 예외를 던져 caller 가 insert 중단 여부를 결정하도록 한다.
        raise NotionDedupeQueryError(
            f"Notion 중복 조회 실패 (RunDate={run_date}): {e}"
        ) from e
    log("INFO", f"Notion 기존 row {len(existing)} 건 (RunDate={run_date})")
    return existing


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Notion rich_text 배열로 분할 (각 element ≤ 2000자)."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": chunk}}
            for chunk in chunk_text(text, NOTION_RICH_TEXT_CHUNK)]


def _select(name: str) -> dict[str, Any]:
    return {"select": {"name": name}}


def _date_iso(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    return {"date": {"start": value}}


def _datetime_iso(value: datetime) -> dict[str, Any]:
    # Notion 은 ISO-8601 with offset 허용
    return {"date": {"start": value.isoformat()}}


def _url(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    return {"url": value}


def build_notion_properties(item: IntakeItem, run_date: date,
                            collected_at: datetime) -> dict[str, Any]:
    # Name 타이틀 — 소스별 프리픽스
    _prefix_map = {
        SOURCE_FR:      "FR",
        SOURCE_RECALL:  "Recall",
        SOURCE_EMA:     "EMA",
        SOURCE_MHRA:    "MHRA",
        SOURCE_PICS:    "PICS",
        SOURCE_ECA:     "ECA",
        SOURCE_FDA_WL:  "WL",
    }
    prefix = _prefix_map.get(item.source, item.source)
    if item.source in (SOURCE_RECALL, SOURCE_FDA_WL):
        name = f"{prefix} {item.document_id} — {truncate(item.firm or item.headline, 100)}"
    else:
        name = f"{prefix} {item.document_id} — {truncate(item.headline, 100)}"

    props: dict[str, Any] = {
        PROP_NAME: {"title": _rich_text(name)},
        PROP_SOURCE: _select(item.source),
        PROP_DOC_ID: {"rich_text": _rich_text(item.document_id)},
        PROP_HEADLINE: {"rich_text": _rich_text(truncate(item.headline, NOTION_RICH_TEXT_CHUNK))},
        PROP_COLLECTED_AT: _datetime_iso(collected_at),
        PROP_RUN_DATE: {"date": {"start": run_date.isoformat()}},
        PROP_QA_RELEVANCE: _select(item.qa_relevance),
        PROP_OSD_RELEVANCE: _select(item.osd_relevance),
        PROP_SOURCE_TYPE: _select(item.source_type),
        PROP_STATUS: _select("New"),
    }

    if item.date_iso:
        d = _date_iso(item.date_iso)
        if d:
            props[PROP_DATE] = d
    if item.official_url:
        u = _url(item.official_url)
        if u:
            props[PROP_OFFICIAL_URL] = u
    if item.type_or_class:
        # Select 옵션은 자동 생성됨
        props[PROP_TYPE_CLASS] = _select(item.type_or_class[:100])
    if item.firm:
        props[PROP_FIRM] = {"rich_text": _rich_text(truncate(item.firm, NOTION_RICH_TEXT_CHUNK))}
    if item.body:
        props[PROP_BODY] = {"rich_text": _rich_text(truncate(item.body, NOTION_RICH_TEXT_CHUNK))}
    if item.distribution:
        props[PROP_DISTRIBUTION] = {"rich_text": _rich_text(truncate(item.distribution, NOTION_RICH_TEXT_CHUNK))}
    if item.comments_close_iso:
        d = _date_iso(item.comments_close_iso)
        if d:
            props[PROP_COMMENTS_CLOSE] = d
    if item.api_query:
        u = _url(item.api_query)
        if u:
            props[PROP_API_QUERY] = u

    return props


def build_notion_children(item: IntakeItem) -> list[dict[str, Any]]:
    """페이지 본문에 raw API JSON 을 code block 으로 저장."""
    raw_json = json.dumps(item.raw_payload, ensure_ascii=False, indent=2)
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text",
                               "text": {"content": "Raw API payload"}}],
            },
        }
    ]
    for chunk in chunk_text(raw_json, NOTION_CODE_BLOCK_CHUNK):
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "language": "json",
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        })
    return blocks


def notion_create_page(token: str, db_id: str, item: IntakeItem,
                       run_date: date, collected_at: datetime,
                       retries: int = 2) -> bool:
    """Notion 페이지 생성. 429/5xx 는 재시도, 4xx(429 제외)는 즉시 실패."""
    body = {
        "parent": {"database_id": db_id},
        "properties": build_notion_properties(item, run_date, collected_at),
        "children": build_notion_children(item),
    }
    # 재시도 불필요 상태 코드 (클라이언트 에러 — 재시도해도 동일 결과)
    _NO_RETRY_CODES = {400, 401, 403, 404, 409}

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(NOTION_PAGES_URL, json=body,
                                 headers=notion_headers(token), timeout=30)
            if resp.status_code < 400:
                return True
            if resp.status_code in _NO_RETRY_CODES:
                log("ERROR", f"Notion 페이지 생성 실패 ({resp.status_code}, 재시도 없음) "
                            f"doc={item.document_id} body={resp.text[:300]}")
                return False
            if resp.status_code == 429:
                # Retry-After 파싱: 정수/소수/빈 헤더 모두 안전 처리, 상한 30s 적용
                _MAX_RETRY_SLEEP = 30
                raw_ra = resp.headers.get("Retry-After", "")
                try:
                    retry_after = min(int(float(raw_ra)), _MAX_RETRY_SLEEP)
                except (ValueError, TypeError):
                    retry_after = min(2 ** attempt, _MAX_RETRY_SLEEP)
                # 마지막 attempt 직전에는 sleep 후 재시도해도 의미 없으므로 생략
                if attempt < retries:
                    log("WARN", f"Notion 429 rate-limit doc={item.document_id} "
                                f"— {retry_after}s 후 재시도 ({attempt + 1}/{retries + 1})")
                    time.sleep(retry_after)
                continue
            # 500/502/503/504 등 서버 에러 — 지수 백오프 재시도
            log("WARN", f"Notion 페이지 생성 실패 ({resp.status_code}) "
                        f"doc={item.document_id} attempt={attempt + 1}/{retries + 1} "
                        f"body={resp.text[:200]}")
            if attempt < retries:
                time.sleep(2 ** attempt)
        except requests.Timeout as e:
            # Timeout: Notion이 서버 측에서 이미 row를 생성했을 수 있으므로 retry 금지.
            # retry 시 duplicate row 위험. 즉시 실패 처리 후 상위에서 insert_failed 집계.
            log("ERROR", f"Notion 페이지 생성 timeout — retry 금지 (duplicate 방지) "
                         f"doc={item.document_id} err={e}")
            return False
        except requests.RequestException as e:
            # 그 외 네트워크 오류 (ConnectionError 등): 서버 미수신 가능성 높으므로 재시도
            last_err = e
            log("WARN", f"Notion 페이지 생성 네트워크 오류 doc={item.document_id} "
                        f"attempt={attempt + 1}/{retries + 1} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)

    log("ERROR", f"Notion 페이지 생성 최종 실패 doc={item.document_id} last_err={last_err}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────


def insert_items(token: str, db_id: str, items: Iterable[IntakeItem],
                 run_date: date, collected_at: datetime,
                 existing_ids: set[str], dry_run: bool) -> tuple[int, int, int]:
    """삽입 실행. 반환: (inserted, skipped, failed)"""
    inserted = 0
    skipped = 0
    failed = 0
    for item in items:
        if not item.document_id:
            log("WARN", f"document_id 없음 — skip (source={item.source})")
            continue
        # dedupe key = "source::document_id" (source 포함으로 FR/Recall ID 충돌 방지)
        dedup_key = f"{item.source}::{item.document_id}"
        if dedup_key in existing_ids:
            skipped += 1
            continue
        if dry_run:
            log("INFO", f"[DRY] insert source={item.source} id={item.document_id} "
                       f"date={item.date_iso} rel={item.qa_relevance} head={truncate(item.headline, 60)}")
            inserted += 1
            existing_ids.add(dedup_key)
            continue
        # Notion rate limit 방어: 삽입 간 최소 0.34s 지연 (≤ 3 req/s)
        time.sleep(0.34)
        ok = notion_create_page(token, db_id, item, run_date, collected_at)
        if ok:
            inserted += 1
            existing_ids.add(dedup_key)
        else:
            failed += 1
            log("WARN", f"insert 최종 실패 — 다음 항목으로 진행 doc={item.document_id}")
    return inserted, skipped, failed


_ALL_SOURCES = ["fr", "recall", "ema", "mhra", "pics", "eca", "wl"]


def main() -> int:
    parser = argparse.ArgumentParser(description="GRS API Intake Collector v15.1")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion 호출 없이 stdout 만 출력")
    parser.add_argument("--window-days", type=int, default=7,
                        choices=range(1, 91), metavar="N(1-90)",
                        help="수집 윈도우 일수 1~90 (default 7)")
    parser.add_argument("--sources", nargs="+", choices=_ALL_SOURCES,
                        default=_ALL_SOURCES,
                        help="수집할 소스 선택 (기본: all). 예: --sources fr recall ema")
    args = parser.parse_args()
    active = set(args.sources)

    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    openfda_key = os.environ.get("OPENFDA_API_KEY", "").strip() or None

    if not args.dry_run:
        if not notion_token or not notion_db:
            log("ERROR", "NOTION_TOKEN / NOTION_DATABASE_ID 환경변수 필요")
            return 2

    now_k = now_kst()
    run_date = kst_run_date(now_k)
    start, end = date_window(run_date, args.window_days)
    log("INFO", f"실행일(KST)={run_date}  window={start}~{end}  dry_run={args.dry_run}")

    stats = CollectionStats()

    # ── Phase 1: Official API ──────────────────────────────────────────────
    fr_items: list[IntakeItem] = []
    if "fr" in active:
        fr_items, fr_err = collect_federal_register(start, end)
        stats.fr_fetched = len(fr_items)
        if fr_err:
            stats.fr_error = True
            stats.fr_error_msg = fr_err
            if "truncated" in fr_err:
                stats.fr_truncated = True

    recall_items: list[IntakeItem] = []
    if "recall" in active:
        recall_items, rec_err = collect_openfda_recalls(start, end, openfda_key)
        stats.recall_fetched = len(recall_items)
        if rec_err:
            stats.recall_error = True
            stats.recall_error_msg = rec_err
            if "truncated" in rec_err:
                stats.recall_truncated = True

    # ── Phase 2: RSS / HTML (v15.1) ───────────────────────────────────────
    ema_items: list[IntakeItem] = []
    if "ema" in active:
        ema_items, ema_err = collect_ema_rss(start, end)
        stats.ema_fetched = len(ema_items)
        if ema_err:
            stats.ema_error = True
            stats.ema_error_msg = ema_err

    mhra_items: list[IntakeItem] = []
    if "mhra" in active:
        mhra_items, mhra_err = collect_mhra_rss(start, end)
        stats.mhra_fetched = len(mhra_items)
        if mhra_err:
            stats.mhra_error = True
            stats.mhra_error_msg = mhra_err

    pics_items: list[IntakeItem] = []
    if "pics" in active:
        pics_items, pics_err = collect_pics_rss(start, end)
        stats.pics_fetched = len(pics_items)
        if pics_err:
            stats.pics_error = True
            stats.pics_error_msg = pics_err

    eca_items: list[IntakeItem] = []
    if "eca" in active:
        eca_items, eca_err = collect_eca_rss(start, end)
        stats.eca_fetched = len(eca_items)
        if eca_err:
            stats.eca_error = True
            stats.eca_error_msg = eca_err

    wl_items: list[IntakeItem] = []
    if "wl" in active:
        wl_items, wl_err = collect_fda_warning_letters(start, end)
        stats.wl_fetched = len(wl_items)
        if wl_err:
            stats.wl_error = True
            stats.wl_error_msg = wl_err

    total_fetched = (stats.fr_fetched + stats.recall_fetched + stats.ema_fetched
                     + stats.mhra_fetched + stats.pics_fetched
                     + stats.eca_fetched + stats.wl_fetched)
    log("INFO", (
        f"수집 완료: FR={stats.fr_fetched} · Recall={stats.recall_fetched} · "
        f"EMA={stats.ema_fetched} · MHRA={stats.mhra_fetched} · "
        f"PICS={stats.pics_fetched} · ECA={stats.eca_fetched} · "
        f"WL={stats.wl_fetched} · 합계={total_fetched}건"
    ))

    # 3) Notion 기존 row (중복 제거)
    if args.dry_run:
        existing: set[str] = set()
    else:
        try:
            existing = notion_query_existing_doc_ids(notion_token, notion_db, run_date)
        except NotionDedupeQueryError as e:
            # 중복 조회 실패 시 빈 set으로 진행하면 대량 중복 insert 위험 → 중단
            log("ERROR", f"중복 조회 실패 — duplicate insert 방지를 위해 insert 단계 중단: {e}")
            return 1

    collected_at = now_k

    # 4) 삽입 (반환: inserted, skipped, failed)
    fr_in, fr_sk, fr_fail = insert_items(notion_token, notion_db, fr_items,
                                         run_date, collected_at, existing, args.dry_run)
    stats.fr_inserted = fr_in
    stats.fr_skipped_dup = fr_sk
    stats.fr_insert_failed = fr_fail

    rec_in, rec_sk, rec_fail = insert_items(notion_token, notion_db, recall_items,
                                            run_date, collected_at, existing, args.dry_run)
    stats.recall_inserted = rec_in
    stats.recall_skipped_dup = rec_sk
    stats.recall_insert_failed = rec_fail

    # Phase 2 삽입
    ema_in, ema_sk, ema_fail = insert_items(notion_token, notion_db, ema_items,
                                             run_date, collected_at, existing, args.dry_run)
    stats.ema_inserted = ema_in
    stats.ema_skipped_dup = ema_sk
    stats.ema_insert_failed = ema_fail

    mhra_in, mhra_sk, mhra_fail = insert_items(notion_token, notion_db, mhra_items,
                                                run_date, collected_at, existing, args.dry_run)
    stats.mhra_inserted = mhra_in
    stats.mhra_skipped_dup = mhra_sk
    stats.mhra_insert_failed = mhra_fail

    pics_in, pics_sk, pics_fail = insert_items(notion_token, notion_db, pics_items,
                                                run_date, collected_at, existing, args.dry_run)
    stats.pics_inserted = pics_in
    stats.pics_skipped_dup = pics_sk
    stats.pics_insert_failed = pics_fail

    eca_in, eca_sk, eca_fail = insert_items(notion_token, notion_db, eca_items,
                                             run_date, collected_at, existing, args.dry_run)
    stats.eca_inserted = eca_in
    stats.eca_skipped_dup = eca_sk
    stats.eca_insert_failed = eca_fail

    wl_in, wl_sk, wl_fail = insert_items(notion_token, notion_db, wl_items,
                                          run_date, collected_at, existing, args.dry_run)
    stats.wl_inserted = wl_in
    stats.wl_skipped_dup = wl_sk
    stats.wl_insert_failed = wl_fail

    log("INFO", "── Collection summary ──\n" + stats.summary())

    # GitHub Actions 가 읽을 수 있는 GITHUB_STEP_SUMMARY 출력
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## GRS Intake Collection Summary\n\n")
                f.write(f"- Run date (KST): `{run_date.isoformat()}`\n")
                f.write(f"- Window: `{start.isoformat()}` ~ `{end.isoformat()}`\n")
                def _src_line(label: str, fetched: int, inserted: int,
                              skipped: int, failed: int, error: bool,
                              err_msg: str, truncated: bool = False) -> str:
                    prefix = "⚠️ " if (error or failed > 0 or truncated) else ""
                    trunc  = " · ⚠️ TRUNCATED" if truncated else ""
                    return (f"- {prefix}{label}: fetched {fetched} · "
                            f"inserted {inserted} · skip-dup {skipped} · "
                            f"failed {failed} · error `{err_msg or 'none'}`{trunc}\n")

                f.write(_src_line("Federal Register", stats.fr_fetched, stats.fr_inserted,
                                  stats.fr_skipped_dup, stats.fr_insert_failed,
                                  stats.fr_error, stats.fr_error_msg, stats.fr_truncated))
                f.write(_src_line("OpenFDA Recall", stats.recall_fetched, stats.recall_inserted,
                                  stats.recall_skipped_dup, stats.recall_insert_failed,
                                  stats.recall_error, stats.recall_error_msg, stats.recall_truncated))
                f.write(_src_line("EMA RSS", stats.ema_fetched, stats.ema_inserted,
                                  stats.ema_skipped_dup, stats.ema_insert_failed,
                                  stats.ema_error, stats.ema_error_msg))
                f.write(_src_line("MHRA RSS", stats.mhra_fetched, stats.mhra_inserted,
                                  stats.mhra_skipped_dup, stats.mhra_insert_failed,
                                  stats.mhra_error, stats.mhra_error_msg))
                f.write(_src_line("PIC/S RSS", stats.pics_fetched, stats.pics_inserted,
                                  stats.pics_skipped_dup, stats.pics_insert_failed,
                                  stats.pics_error, stats.pics_error_msg))
                f.write(_src_line("ECA Academy RSS", stats.eca_fetched, stats.eca_inserted,
                                  stats.eca_skipped_dup, stats.eca_insert_failed,
                                  stats.eca_error, stats.eca_error_msg))
                f.write(_src_line("FDA Warning Letters", stats.wl_fetched, stats.wl_inserted,
                                  stats.wl_skipped_dup, stats.wl_insert_failed,
                                  stats.wl_error, stats.wl_error_msg))
                f.write(f"- Dry run: `{args.dry_run}`\n")
                if stats.has_insert_failures():
                    total_fail = stats.fr_insert_failed + stats.recall_insert_failed
                    f.write(f"\n> ⚠️ **Notion 삽입 실패 {total_fail}건** — "
                            f"해당 항목은 이번 주 다이제스트에서 누락될 수 있습니다. "
                            f"Actions 로그에서 doc ID 확인 후 필요 시 수동 재실행.\n")
        except OSError as e:
            log("WARN", f"STEP_SUMMARY 쓰기 실패: {e}")

    # 종료 코드:
    # - Phase 1 (FR + Recall) 모두 실패 → exit 1 (workflow fail)
    # - Phase 1 한쪽 실패 또는 Phase 2 전체 실패 → exit 0 (graceful degradation)
    # - Phase 2 는 개별 소스 실패가 있어도 다른 소스 계속 진행 (graceful)
    phase1_fr_active     = "fr" in active
    phase1_recall_active = "recall" in active
    fr_failed     = phase1_fr_active and stats.fr_error
    recall_failed = phase1_recall_active and stats.recall_error
    if fr_failed and recall_failed:
        log("ERROR", "Phase 1 두 API 모두 실패 — workflow fail")
        return 1
    if not phase1_fr_active and not phase1_recall_active:
        # Phase 1 소스가 모두 비활성화된 경우 Phase 2 결과만으로 판단
        phase2_all_error = all([
            ("ema" not in active or stats.ema_error),
            ("mhra" not in active or stats.mhra_error),
            ("pics" not in active or stats.pics_error),
            ("eca" not in active or stats.eca_error),
            ("wl" not in active or stats.wl_error),
        ])
        if phase2_all_error:
            log("ERROR", "모든 활성 소스 실패 — workflow fail")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
