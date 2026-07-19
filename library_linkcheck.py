#!/usr/bin/env python3
"""Polite, non-destructive health check for the GRM library catalogue URLs."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

SCHEMA_VERSION = "grm-library-health/v1"
URL_FIELDS = ("official_url", "pdf_url", "ko_url")
DEFAULT_USER_AGENT = "GRM-Library-Linkcheck/1.0 (+https://github.com/MINHOYEOM/grm-api-intake)"
BOT_SENSITIVE_HOSTS = ("fda.gov", "canada.ca")


@dataclass
class Probe:
    status: str
    http_status: int | None
    method: str
    attempts: int
    reason: str
    final_url: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def collect_urls(library_dir: Path) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    refs: dict[str, list[dict[str, str]]] = {}
    source_files: list[str] = []
    for path in sorted(library_dir.glob("*.json")):
        source_files.append(path.name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload if isinstance(payload, list) else payload.get("items", [])
        if not isinstance(items, list):
            raise ValueError(f"{path}: items must be a list")
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            for field in URL_FIELDS:
                url = str(item.get(field) or "").strip()
                if not url:
                    continue
                refs.setdefault(url, []).append({
                    "file": path.name, "item_id": item_id, "field": field,
                })
    return refs, source_files


def _bot_sensitive(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in BOT_SENSITIVE_HOSTS)


def _classify(url: str, code: int | None, reason: str) -> tuple[str, str]:
    if code is not None and 200 <= code < 400:
        return "ok", "reachable"
    if code in (404, 410):
        return "broken", f"http_{code}"
    if _bot_sensitive(url) and (code in (None, 401, 403, 429)):
        return "needs_review", f"suspected_bot_block:{reason or ('http_' + str(code))}"
    if code is None:
        return "needs_review", f"network_or_tls:{reason or 'unknown'}"
    if code >= 500 or code in (401, 403, 405, 408, 429):
        return "needs_review", f"transient_or_access_control:http_{code}"
    return "broken", f"http_{code}"


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float,
    sleeper: Callable[[float], None],
    delay: float,
) -> requests.Response:
    sleeper(delay)
    kwargs: dict[str, Any] = {"allow_redirects": True, "timeout": timeout}
    if method == "GET":
        kwargs.update({"stream": True, "headers": {"Range": "bytes=0-1023"}})
    return session.request(method, url, **kwargs)


def probe_url(
    url: str,
    *,
    session: requests.Session,
    delay: float,
    timeout: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> Probe:
    last_code: int | None = None
    last_method = "HEAD"
    last_reason = ""
    final_url = url
    for attempt in range(1, 3):  # initial attempt + one retry
        for method in ("HEAD", "GET"):
            last_method = method
            try:
                response = _request(
                    session, method, url, timeout=timeout, sleeper=sleeper, delay=delay,
                )
                last_code = response.status_code
                final_url = str(response.url or url)
                response.close()
                # HEAD success and definitive not-found do not need GET fallback.
                if method == "HEAD" and (200 <= last_code < 400 or last_code in (404, 410)):
                    break
                if method == "GET":
                    break
            except requests.RequestException as exc:
                last_code = None
                last_reason = type(exc).__name__
                # A failed HEAD still receives the required GET fallback.
                continue
        status, reason = _classify(url, last_code, last_reason)
        if status == "ok" or (status == "broken" and last_code in (404, 410)):
            return Probe(status, last_code, last_method, attempt, reason, final_url)
        if attempt == 2:
            return Probe(status, last_code, last_method, attempt, reason, final_url)
    raise AssertionError("unreachable")


def build_report(
    library_dir: Path,
    *,
    delay: float,
    timeout: float,
    user_agent: str = DEFAULT_USER_AGENT,
    session: requests.Session | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    refs, source_files = collect_urls(library_dir)
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
    checked_at = _utc_now()
    results: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    for url in sorted(refs):
        probe = probe_url(
            url, session=sess, delay=delay, timeout=timeout, sleeper=sleeper,
        )
        counts[probe.status] += 1
        results[url] = {
            "status": probe.status,
            "http_status": probe.http_status,
            "method": probe.method,
            "attempts": probe.attempts,
            "reason": probe.reason,
            "final_url": probe.final_url,
            "references": refs[url],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked_at,
        "user_agent": user_agent,
        "policy": {
            "head_first": True, "get_fallback": True, "failure_retries": 1,
            "request_delay_seconds": delay,
            "bot_sensitive_hosts": list(BOT_SENSITIVE_HOSTS),
        },
        "source_files": source_files,
        "summary": {
            "unique_urls": len(results),
            "references": sum(len(value) for value in refs.values()),
            "ok": counts["ok"],
            "broken": counts["broken"],
            "needs_review": counts["needs_review"],
        },
        "urls": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=Path("web/data/library"))
    parser.add_argument("--output", type=Path, default=Path("web/data/library_health.json"))
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args(argv)
    report = build_report(
        args.library_dir, delay=max(args.delay, 0), timeout=args.timeout,
        user_agent=args.user_agent,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
