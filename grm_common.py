#!/usr/bin/env python3
"""Shared runtime helpers for GRM collectors."""

from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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


def retry_after_seconds(resp: requests.Response, attempt: int, *, max_sleep: int = 60) -> int:
    raw = resp.headers.get("Retry-After", "")
    try:
        return min(int(float(raw)), max_sleep)
    except (TypeError, ValueError):
        return min(2 ** attempt, max_sleep)


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
            resp = requests.get(url, params=params, timeout=timeout, headers=req_headers)
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
            resp = requests.get(url, timeout=timeout, headers=req_headers)
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
