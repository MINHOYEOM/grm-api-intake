#!/usr/bin/env python3
"""
GRS API Intake Collector — v15.0 Phase 1

Federal Register API + OpenFDA Drug Enforcement API 를 호출해 지난 7일 (KST 기준)
항목을 수집하고, Notion "GRS API Intake" 데이터베이스에 raw API 필드를 그대로 저장한다.

설계 원칙:
1. KST 기준 7일 윈도우 — 모든 날짜는 Asia/Seoul 로 계산
2. raw API 필드 보존 — Evidence A 조건 충족을 위해 원문 JSON 도 페이지 본문에 보관
3. graceful degradation — 한쪽 API 실패해도 다른 쪽 계속 진행
4. 중복 제거 — 같은 Run Date + Document ID 는 skip
5. QA relevance 1 차 휴리스틱만 부여, 최종 판정은 Routine 위임

환경 변수 (GitHub Secrets):
- NOTION_TOKEN       : Notion Integration token (secret_…)
- NOTION_DATABASE_ID : "GRS API Intake" DB ID
- OPENFDA_API_KEY    : OpenFDA 무료 API key (선택, 없으면 no-key 사용)

CLI 옵션:
- --dry-run : Notion 호출 없이 stdout 에 요약만 출력
- --window-days N : 기본 7. 백필 테스트 시 변경
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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

# 13 개 카테고리 휴리스틱 키워드 (lowercase 비교)
QA_CATEGORY_KEYWORDS = [
    "gmp", "cgmp", "manufacturing practice",
    "pharmaceutical quality system", "pqs", "ich q10",
    "quality risk management", "qrm", "ich q9",
    "data integrity", "alcoa", "part 11", "annex 11",
    "computer system validation", "csv", "artificial intelligence",
    "process validation", "cleaning validation",
    "analytical procedure", "ich q2", "ich q14",
    "post-approval", "cmc change", "ich q12",
    "continuous manufacturing",
    "stability", "ich q1", "oos", "oot",
    "deviation", "capa", "change control",
    "sterile", "annex 1",
    "supplier qualification",
]

# Likely 가산 키워드 (경구 고형제 · 정제 직접 연관)
QA_LIKELY_BOOST = [
    "tablet", "capsule", "oral solid", "solid dosage",
    "warning letter", "dissolution", "uniformity of dosage",
    "data integrity", "annex 1", "cgmp",
]

# 명시 제외 (medical device · 화장품 · 식품 · 백신 단독 등)
QA_EXCLUDE_KEYWORDS = [
    "medical device", "device only",
    "cosmetic", "cosmetics",
    "food safety", "dietary supplement label",
    "veterinary only", "animal drug only",
]

# 13 개 카테고리 통과를 위한 최소 매칭 키워드 수
QA_MIN_MATCH = 1

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
    source: str  # SOURCE_FR | SOURCE_RECALL
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
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionStats:
    fr_fetched: int = 0
    fr_inserted: int = 0
    fr_skipped_dup: int = 0
    fr_failed: int = 0  # Notion insert failures (Codex must-fix #2)
    fr_error: bool = False
    fr_error_msg: str = ""
    recall_fetched: int = 0
    recall_inserted: int = 0
    recall_skipped_dup: int = 0
    recall_failed: int = 0  # Notion insert failures (Codex must-fix #2)
    recall_error: bool = False
    recall_error_msg: str = ""

    def summary(self) -> str:
        return (
            f"FR  fetched={self.fr_fetched}  inserted={self.fr_inserted}  "
            f"skip_dup={self.fr_skipped_dup}  failed={self.fr_failed}  "
            f"error={self.fr_error}\n"
            f"REC fetched={self.recall_fetched}  inserted={self.recall_inserted}  "
            f"skip_dup={self.recall_skipped_dup}  failed={self.recall_failed}  "
            f"error={self.recall_error}"
        )


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


def http_get_json(url: str, *, params: dict[str, Any] | None = None,
                  timeout: int = 30, retries: int = 2) -> dict[str, Any]:
    """
    GET JSON with masked-URL logging and retry policy split by status class.

    Codex review fixes:
    - Always log a redacted URL (mask api_key) on every code path, including
      retries and final failure. The original code logged the raw URL on retry,
      leaking the OpenFDA key when present.
    - Retry only 408, 429, and 5xx. 4xx (400/401/403/404/etc.) are permanent and
      raise immediately as requests.HTTPError so callers can branch on
      response.status_code.
    - Never include str(exception) in log lines because requests embeds the raw
      URL there.
    """
    safe_url = _mask_api_key(url)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": "GRS-Intake/1.0 (+github-actions)"})
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            last_err = e
            # Permanent 4xx (except retryable 408/429) — raise immediately.
            # Reconstruct HTTPError with masked URL message and suppress the
            # original chain ("from None") so the raw URL does not leak via
            # __cause__ traceback rendering.
            if status is not None and 400 <= status < 500 and status not in (408, 429):
                masked = requests.HTTPError(
                    f"HTTP {status} for {safe_url}", response=e.response,
                )
                raise masked from None
            log("WARN", f"GET 실패 ({attempt + 1}/{retries + 1}) "
                       f"url={safe_url} status={status} (retryable)")
        except requests.RequestException as e:
            # Network errors (timeout, connection refused, DNS, etc.).
            # Do NOT include str(e) — requests embeds the raw URL there.
            last_err = e
            log("WARN", f"GET 실패 ({attempt + 1}/{retries + 1}) "
                       f"url={safe_url} err={type(e).__name__}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP GET 최종 실패: {safe_url} "
                       f"(last_error_type={type(last_err).__name__ if last_err else 'None'})")


# ─────────────────────────────────────────────────────────────────────────────
# QA Relevance 휴리스틱
# ─────────────────────────────────────────────────────────────────────────────


def compute_relevance(*text_parts: str) -> str:
    blob = " ".join(t for t in text_parts if t).lower()
    if not blob.strip():
        return "Pending"
    if any(ex in blob for ex in QA_EXCLUDE_KEYWORDS):
        # 단, 명시 제외 키워드가 있어도 GMP/CGMP/data integrity 가 강하게 들어있으면 Possible
        strong = sum(1 for kw in QA_LIKELY_BOOST if kw in blob)
        if strong >= 2:
            return "Possible"
        return "Unrelated"
    matches = sum(1 for kw in QA_CATEGORY_KEYWORDS if kw in blob)
    if matches < QA_MIN_MATCH:
        return "Pending"
    boosts = sum(1 for kw in QA_LIKELY_BOOST if kw in blob)
    if boosts >= 1:
        return "Likely"
    return "Possible"


# ─────────────────────────────────────────────────────────────────────────────
# Federal Register 수집
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
                # Hitting the safety cap with results still pending is partial-success
                # but should not be silent: surface as an error so the workflow's stats
                # callout reflects it (Codex review nice-to-have #14).
                log("WARN", "FR pagination 10 페이지 초과 — 안전 중단")
                return items, ("FR pagination cap (10 pages) reached "
                               "with next_page_url still set — possible partial data")
            data = http_get_json(next_url)
            results = data.get("results", []) or []
            log("INFO", f"FR page {page_count}: {len(results)} 건")
            for r in results:
                items.append(_fr_to_item(r, api_query_url))
            next_url = data.get("next_page_url")
        return items, None
    except Exception as e:
        safe_msg = _mask_api_key(str(e))
        log("ERROR", f"FR 수집 실패: {type(e).__name__}: {safe_msg}")
        return items, f"{type(e).__name__}: {safe_msg}"


def _fr_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    doc_id = str(r.get("document_number") or "").strip()
    title = (r.get("title") or "").strip()
    pub = (r.get("publication_date") or "").strip()
    html_url = (r.get("html_url") or "").strip()
    doc_type = (r.get("type") or "").strip()
    abstract = (r.get("abstract") or "").strip()
    comments_close = (r.get("comments_close_on") or "").strip()

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
            except requests.HTTPError as e:
                # OpenFDA returns HTTP 404 when the result set is empty
                # (documented at open.fda.gov/apis/responses/).
                # Treat as a successful zero-row response.
                if e.response is not None and e.response.status_code == 404:
                    log("INFO", "OpenFDA 404 (해당 기간 결과 0건) — 정상 종료")
                    return items, None
                raise
            results = data.get("results", []) or []
            meta_total = (data.get("meta", {}).get("results", {}) or {}).get("total", 0)
            log("INFO", f"OpenFDA skip={skip}: {len(results)} 건 (total {meta_total})")
            for r in results:
                items.append(_recall_to_item(r, api_query_url_for_log or _mask_api_key(url)))
            skip += len(results)
            if not results or skip >= meta_total or skip >= OPENFDA_MAX_TOTAL:
                break
        return items, None
    except Exception as e:
        # Mask any URL embedded in the exception string before logging / surfacing.
        safe_msg = _mask_api_key(str(e))
        log("ERROR", f"OpenFDA 수집 실패: {type(e).__name__}: {safe_msg}")
        return items, f"{type(e).__name__}: {safe_msg}"


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

    # report_date YYYYMMDD → YYYY-MM-DD
    date_iso = ""
    if len(report_date_raw) == 8 and report_date_raw.isdigit():
        date_iso = f"{report_date_raw[0:4]}-{report_date_raw[4:6]}-{report_date_raw[6:8]}"
    else:
        date_iso = report_date_raw  # fallback

    headline = product or firm or recall_number
    relevance = compute_relevance(product, reason, firm, distribution, product_type)

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
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Notion 헬퍼
# ─────────────────────────────────────────────────────────────────────────────


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def notion_query_existing_doc_ids(token: str, db_id: str, run_date: date) -> set[str]:
    """오늘(KST) 분 Run Date 와 일치하는 row 의 Document ID set 반환 — 중복 방지."""
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
                doc_id_arr = props.get(PROP_DOC_ID, {}).get("rich_text", [])
                if doc_id_arr:
                    txt = "".join(rt.get("plain_text", "") for rt in doc_id_arr).strip()
                    if txt:
                        existing.add(txt)
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
    except requests.RequestException as e:
        # Codex must-fix: fail closed instead of returning an empty set.
        # If we proceed without knowing existing Document IDs, we may insert
        # duplicates for every row on the same Run Date, polluting the DB.
        # Raise so main() exits non-zero and the workflow's failure handler runs.
        raise RuntimeError(
            f"Notion dedup query failed ({type(e).__name__}) — aborting to "
            f"prevent duplicate inserts. Re-run after Notion connectivity is restored."
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
    # Name 타이틀
    if item.source == SOURCE_FR:
        name = f"FR {item.document_id} — {truncate(item.headline, 100)}"
    else:
        name = f"Recall {item.document_id} — {truncate(item.firm or item.headline, 100)}"

    props: dict[str, Any] = {
        PROP_NAME: {"title": _rich_text(name)},
        PROP_SOURCE: _select(item.source),
        PROP_DOC_ID: {"rich_text": _rich_text(item.document_id)},
        PROP_HEADLINE: {"rich_text": _rich_text(truncate(item.headline, NOTION_RICH_TEXT_CHUNK))},
        PROP_COLLECTED_AT: _datetime_iso(collected_at),
        PROP_RUN_DATE: {"date": {"start": run_date.isoformat()}},
        PROP_QA_RELEVANCE: _select(item.qa_relevance),
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
                       run_date: date, collected_at: datetime) -> bool:
    body = {
        "parent": {"database_id": db_id},
        "properties": build_notion_properties(item, run_date, collected_at),
        "children": build_notion_children(item),
    }
    try:
        resp = requests.post(NOTION_PAGES_URL, json=body,
                             headers=notion_headers(token), timeout=30)
        if resp.status_code >= 400:
            log("ERROR", f"Notion 페이지 생성 실패 ({resp.status_code}) "
                        f"doc={item.document_id} body={resp.text[:300]}")
            return False
        return True
    except requests.RequestException as e:
        log("ERROR", f"Notion 페이지 생성 예외 doc={item.document_id} err={e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────


def insert_items(token: str, db_id: str, items: Iterable[IntakeItem],
                 run_date: date, collected_at: datetime,
                 existing_ids: set[str], dry_run: bool) -> tuple[int, int, int]:
    """
    Returns (inserted, skipped_dup, failed).

    Codex must-fix #2: track Notion insert failures separately so main() can
    fail the workflow if every insert fails.
    """
    inserted = 0
    skipped = 0
    failed = 0
    for item in items:
        if not item.document_id:
            log("WARN", f"document_id 없음 — skip (source={item.source})")
            continue
        if item.document_id in existing_ids:
            skipped += 1
            continue
        if dry_run:
            log("INFO", f"[DRY] insert source={item.source} id={item.document_id} "
                       f"date={item.date_iso} rel={item.qa_relevance} head={truncate(item.headline, 60)}")
            inserted += 1
            existing_ids.add(item.document_id)
            continue
        ok = notion_create_page(token, db_id, item, run_date, collected_at)
        if ok:
            inserted += 1
            existing_ids.add(item.document_id)
        else:
            failed += 1
            log("WARN", f"insert 실패 — 다음 항목으로 진행 doc={item.document_id}")
    return inserted, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="GRS API Intake Collector v15.0")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion 호출 없이 stdout 만 출력")
    parser.add_argument("--window-days", type=int, default=7,
                        help="수집 윈도우 (default 7)")
    args = parser.parse_args()

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

    # 1) Federal Register
    fr_items, fr_err = collect_federal_register(start, end)
    stats.fr_fetched = len(fr_items)
    if fr_err:
        stats.fr_error = True
        stats.fr_error_msg = fr_err

    # 2) OpenFDA Recall
    recall_items, rec_err = collect_openfda_recalls(start, end, openfda_key)
    stats.recall_fetched = len(recall_items)
    if rec_err:
        stats.recall_error = True
        stats.recall_error_msg = rec_err

    log("INFO", f"수집 완료: FR={stats.fr_fetched}건 · Recall={stats.recall_fetched}건")

    # 3) Notion 기존 row (중복 제거).
    #    Codex must-fix #3: notion_query_existing_doc_ids now fails closed.
    #    If it raises, we MUST stop before any insert so we don't duplicate rows.
    if args.dry_run:
        existing: set[str] = set()
    else:
        try:
            existing = notion_query_existing_doc_ids(notion_token, notion_db, run_date)
        except RuntimeError as e:
            log("ERROR", str(e))
            return 1

    collected_at = now_k

    # 4) 삽입
    fr_in, fr_sk, fr_fa = insert_items(notion_token, notion_db, fr_items,
                                       run_date, collected_at, existing, args.dry_run)
    stats.fr_inserted = fr_in
    stats.fr_skipped_dup = fr_sk
    stats.fr_failed = fr_fa

    rec_in, rec_sk, rec_fa = insert_items(notion_token, notion_db, recall_items,
                                          run_date, collected_at, existing, args.dry_run)
    stats.recall_inserted = rec_in
    stats.recall_skipped_dup = rec_sk
    stats.recall_failed = rec_fa

    log("INFO", "── Collection summary ──\n" + stats.summary())

    # GitHub Actions 가 읽을 수 있는 GITHUB_STEP_SUMMARY 출력
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## GRS Intake Collection Summary\n\n")
                f.write(f"- Run date (KST): `{run_date.isoformat()}`\n")
                f.write(f"- Window: `{start.isoformat()}` ~ `{end.isoformat()}`\n")
                f.write(f"- Federal Register: fetched {stats.fr_fetched} · "
                        f"inserted {stats.fr_inserted} · skip-dup {stats.fr_skipped_dup} · "
                        f"failed {stats.fr_failed} · "
                        f"error `{stats.fr_error_msg or 'none'}`\n")
                f.write(f"- OpenFDA Recall: fetched {stats.recall_fetched} · "
                        f"inserted {stats.recall_inserted} · skip-dup {stats.recall_skipped_dup} · "
                        f"failed {stats.recall_failed} · "
                        f"error `{stats.recall_error_msg or 'none'}`\n")
                f.write(f"- Dry run: `{args.dry_run}`\n")
        except OSError as e:
            log("WARN", f"STEP_SUMMARY 쓰기 실패: {e}")

    # 종료 코드 (Codex review 반영):
    #
    # - 두 소스 API 모두 실패 → exit 1
    # - Notion sink 가 시도된 모든 insert 에서 실패 (즉 sink 100% down) → exit 1
    #   (must-fix #2: 이전엔 한쪽 API 만 성공해도 sink 전부 실패해도 exit 0)
    # - 그 외 (부분 성공, 결과 0건 포함) → exit 0
    if stats.fr_error and stats.recall_error:
        log("ERROR", "두 소스 API 모두 실패 — workflow fail")
        return 1

    total_attempts = fr_in + fr_fa + rec_in + rec_fa
    total_failed = fr_fa + rec_fa
    if not args.dry_run and total_attempts > 0 and total_failed == total_attempts:
        log("ERROR", f"Notion 적재 100% 실패 ({total_failed} attempts, 0 success) — workflow fail")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
