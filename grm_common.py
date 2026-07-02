#!/usr/bin/env python3
"""Shared runtime helpers for GRM collectors."""

from __future__ import annotations

import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from urllib.parse import urlparse
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
