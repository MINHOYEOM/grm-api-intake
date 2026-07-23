#!/usr/bin/env python3
"""Shared runtime helpers for GRM collectors."""

from __future__ import annotations

import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlencode, urlparse
from typing import Any

import requests


DEFAULT_USER_AGENT = "GRM-Intake/1.1 (+github-actions)"
DEFAULT_XML_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}
DEFAULT_JSON_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json",
}
MFDS_EGRESS_HOSTS = {"www.mfds.go.kr", "nedrug.mfds.go.kr", "www.law.go.kr"}


class HTTPClientError(RuntimeError):
    """HTTP 4xx error with status code attached."""

    def __init__(self, status_code: int, url: str, msg: str = "") -> None:
        super().__init__(msg or f"HTTP {status_code} for {url}")
        self.status_code = status_code
        self.url = url


def log(level: str, msg: str) -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {level} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


def env_flag(name: str, default: bool = False) -> bool:
    """ENABLE_* 플래그 단일 파서 — truthy = {"1","true","yes","on"} (case/공백 무시)."""
    val = (os.environ.get(name) or "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def retry_after_seconds(resp: requests.Response, attempt: int, *, max_sleep: int = 60) -> int:
    raw = resp.headers.get("Retry-After", "")
    try:
        return min(int(float(raw)), max_sleep)
    except (TypeError, ValueError):
        return min(2 ** attempt, max_sleep)


def _proxies_for(url: str) -> dict[str, str] | None:
    """Return an opt-in KR egress proxy only for MFDS/law.go.kr hosts."""
    proxy = os.environ.get("MFDS_HTTP_PROXY", "").strip()
    if not proxy:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host in MFDS_EGRESS_HOSTS:
        return {"http": proxy, "https": proxy}
    return None


def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """GET JSON with Retry-After support for 429 and exponential retry for 5xx/network."""

    last_err: Exception | None = None
    req_headers = {**DEFAULT_JSON_HEADERS, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers=req_headers,
                proxies=_proxies_for(url),
            )
            if resp.status_code == 429:
                if attempt < retries:
                    sleep_s = retry_after_seconds(resp, attempt)
                    log("WARN", f"GET 429 rate-limit url={url} sleep={sleep_s}s attempt={attempt + 1}/{retries + 1}")
                    time.sleep(sleep_s)
                    continue
                raise HTTPClientError(resp.status_code, url, f"HTTP 429 for {url}")
            if 400 <= resp.status_code < 500:
                raise HTTPClientError(resp.status_code, url, f"HTTP {resp.status_code} for {url}")
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as e:
                raise RuntimeError(f"JSON parse failed: {url} - {e}") from e
        except HTTPClientError:
            raise
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"GET failed ({attempt + 1}/{retries + 1}) url={url} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP GET final failure: {url} ({last_err})")


def http_get_xml(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> ET.Element:
    """GET XML with Retry-After support for 429 and exponential retry for 5xx/network."""

    last_err: Exception | None = None
    req_headers = {**DEFAULT_XML_HEADERS, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers=req_headers,
                proxies=_proxies_for(url),
            )
            if resp.status_code == 429:
                if attempt < retries:
                    sleep_s = retry_after_seconds(resp, attempt)
                    log("WARN", f"XML GET 429 rate-limit url={url} sleep={sleep_s}s attempt={attempt + 1}/{retries + 1}")
                    time.sleep(sleep_s)
                    continue
                raise HTTPClientError(resp.status_code, url, f"HTTP 429 for {url}")
            if 400 <= resp.status_code < 500:
                raise HTTPClientError(resp.status_code, url, f"HTTP {resp.status_code} for {url}")
            resp.raise_for_status()
            # 일부 피드(예: WHO Drupal RSS)는 XML 선언 앞에 theme debug 주석/BOM 등 잡음이 붙어
            # "XML or text declaration not at start of entity" 로 파싱 실패한다.
            # XML 시작 토큰(<?xml / <rss / <feed) 이전 바이트를 잘라낸 뒤 파싱한다.
            content = resp.content
            for marker in (b"<?xml", b"<rss", b"<feed"):
                idx = content.find(marker)
                if idx > 0:
                    content = content[idx:]
                    break
            else:
                content = content.lstrip()
            try:
                return ET.fromstring(content)
            except ET.ParseError as e:
                raise RuntimeError(f"XML parse failed: {url} - {e}") from e
        except HTTPClientError:
            raise
        except requests.RequestException as e:
            last_err = e
            log("WARN", f"XML GET failed ({attempt + 1}/{retries + 1}) url={url} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP XML GET final failure: {url} ({last_err})")


# ── data.go.kr 공통 유틸리티 ──────────────────────────────────────────────────


def parse_int_safe(value: Any, default: int = 0) -> int:
    """Safely parse an integer value, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def text_field(raw: dict[str, Any], key: str) -> str:
    """Extract a stripped string field from a dict, defaulting to ``""``."""
    return str(raw.get(key) or "").strip()


def parse_datago_date(raw: str) -> str:
    """Parse ``YYYYMMDD`` date strings used by data.go.kr APIs → ISO format."""
    raw = (raw or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        y, m, d = raw[:4], raw[4:6], raw[6:8]
        try:
            return date(int(y), int(m), int(d)).isoformat()
        except ValueError:
            return ""
    return ""


def datago_normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    """Normalize data.go.kr's ``item`` wrapper across list/dict shapes."""
    if raw_items is None:
        return []
    if isinstance(raw_items, list):
        out: list[dict[str, Any]] = []
        for item in raw_items:
            out.extend(datago_normalize_items(item))
        return out
    if isinstance(raw_items, dict):
        if "item" in raw_items:
            return datago_normalize_items(raw_items.get("item"))
        return [raw_items]
    return []


def datago_extract_items(
    data: dict[str, Any], default_page_size: int = 100,
) -> tuple[list[dict[str, Any]], int, int, int, str]:
    """Extract items and pagination from a data.go.kr JSON response."""
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    result_code = str(header.get("resultCode") or "").strip()
    result_msg = str(header.get("resultMsg") or "").strip()
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    page_no = parse_int_safe(body.get("pageNo"), 1)
    num_rows = parse_int_safe(body.get("numOfRows"), default_page_size)
    total_count = parse_int_safe(body.get("totalCount"), 0)
    items = datago_normalize_items(body.get("items"))
    return items, page_no, num_rows, total_count, f"{result_code}:{result_msg}"


def mask_service_key(url: str) -> str:
    """data.go.kr/law.go.kr URL 의 serviceKey 값을 REDACTED 로 마스킹.

    5개 수집기의 동일 구현을 단일화 — provenance(item.api_query)에 실 키가 새지 않게 한다.
    """
    return re.sub(r"([?&]serviceKey=)[^&]+", r"\1***REDACTED***", url)


class DatagoPageError(RuntimeError):
    """data.go.kr 페이지 요청 실패 — page_no·원인 첨부(수집기가 부분/치명 판정)."""

    def __init__(self, page_no: int, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.page_no = page_no
        self.cause = cause


class _DatagoPaginator:
    """data.go.kr serviceKey JSON 엔드포인트 페이지네이션 이터레이터(4개 수집기 공용 골격).

    각 페이지 ``(raw_items, masked_url)`` 를 yield. 현행 수집기 루프 의미 보존:
      · params = ``{serviceKey, pageNo, numOfRows, type:json, **extra_params}`` (원 순서 동일)
      · masked_url = ``mask_service_key(endpoint?urlencode(params))`` — item provenance 바이트 동일
      · ``http_get(endpoint, params=, timeout=, retries=)`` → ``extract(data)`` 5-튜플
        ``(raw_items, response_page, num_rows, total_count, status)``
      · status ``'00:'`` 로 시작하지 않으면 페이지 실패
      · 빈 페이지 또는 ``response_page*num_rows >= total_count`` 시 종료
      · ``pageNo > max_pages`` 소진 시 ``.truncated = True``
    페이지 실패는 ``DatagoPageError(page_no, cause)`` raise — 수집기가 items 유무로 부분(WARN)/
    치명(error) 판정한다(소스별 실패·truncated 문구·health 의미는 수집기 소유 → 로깅은 수집기가
    담당; 제너릭 on_warn 미도입). ``extract``·``http_get`` 은 수집기 네임스페이스의 것을 주입받아
    기존 단위테스트의 monkeypatch 호환을 유지한다.
    """

    def __init__(self, endpoint: str, *, service_key: str, extract, http_get,
                 max_pages: int, page_size: int = 100,
                 extra_params: dict[str, Any] | None = None,
                 timeout: int = 30, retries: int = 2) -> None:
        self.endpoint = endpoint
        self.service_key = service_key
        self.extract = extract
        self.http_get = http_get
        self.max_pages = max_pages
        self.page_size = page_size
        self.extra_params = extra_params or {}
        self.timeout = timeout
        self.retries = retries
        self.truncated = False
        self.total_count = 0

    def __iter__(self):
        page_no = 1
        while page_no <= self.max_pages:
            params = {
                "serviceKey": self.service_key,
                "pageNo": page_no,
                "numOfRows": self.page_size,
                "type": "json",
            }
            params.update(self.extra_params)
            masked_url = mask_service_key(self.endpoint + "?" + urlencode(params))
            try:
                data = self.http_get(self.endpoint, params=params,
                                     timeout=self.timeout, retries=self.retries)
                raw_items, response_page, num_rows, total_count, status = self.extract(data)
                if not status.startswith("00:"):
                    raise RuntimeError(f"API status {status}")
            except Exception as e:  # noqa: BLE001
                raise DatagoPageError(page_no, e) from e
            self.total_count = total_count
            if not raw_items:
                return
            yield raw_items, masked_url
            if total_count and response_page * num_rows >= total_count:
                return
            page_no += 1
        self.truncated = True


def datago_paginate(endpoint: str, *, service_key: str, extract, http_get,
                    max_pages: int, page_size: int = 100,
                    extra_params: dict[str, Any] | None = None,
                    timeout: int = 30, retries: int = 2) -> _DatagoPaginator:
    """``_DatagoPaginator`` 팩토리 — 반복 후 ``.truncated``·``.total_count`` 조회. 상세는 클래스 docstring."""
    return _DatagoPaginator(
        endpoint, service_key=service_key, extract=extract, http_get=http_get,
        max_pages=max_pages, page_size=page_size, extra_params=extra_params,
        timeout=timeout, retries=retries)


# ── HTML/bytes GET with retry ─────────────────────────────────────────────────


def http_get_html(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    headers: dict[str, str] | None = None,
    label: str = "",
) -> str:
    """GET HTML with 429 Retry-After and exponential retry."""
    tag = label or "HTML"
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        **(headers or {}),
    }
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers=req_headers,
                proxies=_proxies_for(url),
            )
            if resp.status_code == 429 and attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"{tag} 429 url={url} sleep={sleep_s}s")
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp.text or ""
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log("WARN", f"{tag} GET retry {attempt + 1}/{retries + 1} url={url} err={e}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP GET final failure: {url} ({last_err})") from e
    raise RuntimeError(f"HTTP GET final failure: {url} ({last_err})")


def http_get_bytes(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    headers: dict[str, str] | None = None,
    label: str = "",
) -> bytes:
    """GET raw bytes with 429 Retry-After and exponential retry."""
    tag = label or "BYTES"
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        **(headers or {}),
    }
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers=req_headers,
                proxies=_proxies_for(url),
            )
            if resp.status_code == 429 and attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"{tag} 429 url={url} sleep={sleep_s}s")
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp.content or b""
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log("WARN", f"{tag} GET retry {attempt + 1}/{retries + 1} url={url} err={e}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP GET final failure: {url} ({last_err})") from e
    raise RuntimeError(f"HTTP GET final failure: {url} ({last_err})")


# ── [배치5 Phase0] collect_intake 에서 relocate: 소스 식별 상수 + 공용 텍스트/환경 헬퍼 ──
SOURCE_FR = "Federal Register"
SOURCE_RECALL = "OpenFDA Recall"
SOURCE_EMA = "EMA"
SOURCE_MHRA = "MHRA Inspectorate"
SOURCE_PICS = "PIC/S"
SOURCE_ECA = "ECA Academy"
SOURCE_FDA_WL = "FDA Warning Letter"
SOURCE_MFDS = "MFDS"
SOURCE_ICH = "ICH"
SOURCE_WHO = "WHO"
SOURCE_HC = "Health Canada"
SOURCE_FDA_483 = "FDA 483"   # WHY-1 #3 — OII FOIA Reading Room 483 Observation (가장 깊은 결함 원본)
SOURCE_HANDOFF = "GRM Handoff"
SOURCE_BRAVE = "Brave Search"
SOURCE_RAPS  = "RAPS"
SOURCE_EPR   = "European Pharma Review"   # European Pharmaceutical Review
SOURCE_ISPE  = "ISPE"   # [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 블로그 RSS
SOURCE_EU_GMP_NCR = "EU GMP NCR (EudraGMDP)"   # EU/EEA 업체별 GMP 비준수 보고서(EudraGMDP)
SOURCE_MHRA_GMP_NCR = "MHRA GMP NCR"   # 영국 MHRA 업체별 GMP 비준수 성명서(GMDP 등록부)
NOTION_RICH_TEXT_CHUNK = 1900  # 2000 한도, 여유 100


# ── [배치6 Phase2] 수집 소스 레지스트리 — 소스당 1 레코드 ────────────────────────
# card_scaffold.py 의 SourceSpec(_REGISTRY, 발행측)과 대칭인 "수집측" 레지스트리.
# CollectionStats 스칼라 필드는 유지(사용자 결정)하고, 이 레지스트리가 getattr/setattr
# 로 ② main insert 루프(collect_intake)와 ③ health rows(grm_health)를 구동한다.
# ``prefix`` = CollectionStats 필드 프리픽스 = health row "key" (전 소스 동일).
# 순서는 insert 순서(=existing dedup 누적 순서)·health rows 리스트 순서와 byte 일치해야 함.
#
# 새 수집 소스 추가 절차(4A card_scaffold._REGISTRY 절차와 대칭):
#   1) IntakeSourceSpec 1건을 아래 INTAKE_SOURCE_SPECS 에 (원하는 insert/health 순서로) 추가
#   2) collect_intake.CollectionStats 에 {prefix}_fetched/_inserted/_skipped_dup/
#      _insert_failed/_error/_error_msg 6필드 추가(스칼라 유지 결정의 잔여 — item④)
#   3) collect_intake main 에 수집 블록 1개(collect 호출 → stats.{prefix}_fetched·error) +
#      _insert_items_map 에 {prefix}: items 1항 추가
#   → ② insert / ③ health row / (해당 시) 골든 3종만 갱신. coverage 라벨은 별개
#     (grm_handoff.COVERAGE_SOURCE_LABELS, SOURCE_* 키), transient 적격은 grm_health.
@dataclass(frozen=True)
class IntakeSourceSpec:
    prefix: str          # CollectionStats 필드 프리픽스 & health row key
    health_label: str    # _source_health_rows 의 "label"
    has_truncated: bool = False   # fr/recall 만 health row 에 "truncated" 노출


INTAKE_SOURCE_SPECS: tuple[IntakeSourceSpec, ...] = (
    IntakeSourceSpec("fr", "Federal Register", has_truncated=True),
    IntakeSourceSpec("recall", "OpenFDA Recall", has_truncated=True),
    IntakeSourceSpec("ema", "EMA RSS"),
    IntakeSourceSpec("mhra", "MHRA RSS"),
    IntakeSourceSpec("mhra_alert", "MHRA Drug/Device Alerts"),
    IntakeSourceSpec("pics", "PIC/S RSS"),
    IntakeSourceSpec("eca", "ECA Academy RSS"),
    IntakeSourceSpec("wl", "FDA Warning Letters"),
    IntakeSourceSpec("mfds", "MFDS RSS"),
    IntakeSourceSpec("mfds_law", "MFDS Law/Admrul"),
    IntakeSourceSpec("mfds_recall", "MFDS Recall"),
    IntakeSourceSpec("mfds_admin", "MFDS Admin"),
    IntakeSourceSpec("mfds_gmp_cert", "MFDS GMP Certificate"),
    IntakeSourceSpec("mfds_safety_letter", "MFDS Safety Letter"),
    IntakeSourceSpec("mfds_gmp_inspection", "MFDS GMP Inspection"),
    IntakeSourceSpec("ich", "ICH"),
    IntakeSourceSpec("who", "WHO"),
    IntakeSourceSpec("hc", "Health Canada"),
    IntakeSourceSpec("fda483", "FDA 483"),
    IntakeSourceSpec("ispe", "ISPE iSpeak RSS"),
    IntakeSourceSpec("search", "Brave Search"),
    IntakeSourceSpec("eu_gmp_ncr", "EU GMP NCR (EudraGMDP)"),
    IntakeSourceSpec("mhra_gmp_ncr", "MHRA GMP NCR"),
)


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


def _env_int(name: str, default: int) -> int:
    """환경변수를 정수로 안전 파싱. 비정상 값이면 WARN 후 default 사용 (graceful degradation)."""
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        log("WARN", f"{name}={raw!r} 정수 파싱 실패 — default {default} 사용")
        return default
