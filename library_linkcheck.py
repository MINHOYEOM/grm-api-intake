#!/usr/bin/env python3
"""Polite, non-destructive health check for the GRM library catalogue URLs.

상태 어휘(`urls[].status`) — 네 값은 **서로 다른 사실**이고 조치도 다르다:
  ok           도달 확인. 조치 없음.
  broken       404/410 등 링크가 실제로 죽음. **이것만 운영 이슈를 연다**(워크플로가 필터).
  needs_review 일시 오류·접근 제어(5xx·타임아웃 등). 사람이 보고 판단할 것.
  blocked      **검사기가 봇 방어에 막혀 확인 자체를 못 함.** 링크가 나쁘다는 뜻이 아니다.

★`blocked` 를 2026-08-11 에 `needs_review` 에서 분리했다. 계기: 21 CFR 63건이 GitHub
  Actions 러너에서 전부 `unblock.federalregister.gov` 로 리다이렉트됐다(미국 정부가 러너
  IP 를 봇으로 차단). 그런데 사람이 브라우저로 누르면 정상이고, 수집기도 eCFR **API** 로
  63건을 정상 수집한다 — 링크는 멀쩡한데 **검사기만** 못 본다. 이걸 needs_review 에 합쳐
  두면 매주 63건이 "검토 필요"로 쌓여 진짜 신호(MFDS 5xx 같은 일시 오류 90건)를 덮는다.
  무시되는 알림은 진짜 신호를 가린다.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from grm_common import proxies_for

# v2(2026-08-11): status 어휘에 `blocked` 추가(모듈 docstring 참조). summary 에 동명 키 신설.
# 기존 키(ok/broken/needs_review)는 이름·의미 그대로 — 다만 봇차단 건이 needs_review 에서
# blocked 로 옮겨가므로 needs_review 카운트가 줄어든다(옛 스냅샷과 직접 비교 금지).
SCHEMA_VERSION = "grm-library-health/v2"
URL_FIELDS = ("official_url", "pdf_url", "ko_url")
DEFAULT_USER_AGENT = "GRM-Library-Linkcheck/1.0 (+https://github.com/MINHOYEOM/grm-api-intake)"
BOT_SENSITIVE_HOSTS = ("fda.gov", "canada.ca", "ecfr.gov")
DEFAULT_WORKERS = 12


@dataclass
class Probe:
    status: str
    http_status: int | None
    method: str
    attempts: int
    reason: str
    final_url: str


class HostPacer:
    """Reserve request-start slots at least `delay` apart per host."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def wait(self, url: str, delay: float) -> None:
        host = (urlparse(url).hostname or "").lower()
        with self._lock:
            now = self._monotonic()
            slot = max(now, self._next.get(host, now))
            self._next[host] = slot + delay
        wait_for = slot - now
        if wait_for > 0:
            self._sleeper(wait_for)


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


# ecfr.gov 실측(2026-08-11, 정찰): 봇으로 의심되는 요청은 사람용 페이지를 그대로 주는 게
# 아니라 HTTP 200 을 유지한 채 다른 호스트(unblock.federalregister.gov, "Request Access")
# 로 조용히 리다이렉트한다 — 상태코드만 보면 정상, 최종 URL/본문을 봐야 잡힌다.
_BLOCK_BODY_MARKERS = (
    "request access", "access denied", "are you a human", "captcha",
    "verify you are a human", "unusual traffic", "automated access to this website",
    "checking your browser before accessing", "bot detection",
    "please enable javascript and cookies", "pardon our interruption",
)


def _looks_like_block_page(body_sample: str) -> bool:
    lowered = (body_sample or "").lower()
    return any(marker in lowered for marker in _BLOCK_BODY_MARKERS)


def _suspected_disguised_block(url: str, final_url: str, body_sample: str) -> str | None:
    """봇 민감 호스트가 200대를 돌려줘도 위장 차단일 수 있다 — 최종 리다이렉트가
    다른 호스트로 튀었거나(예: ecfr.gov → unblock.federalregister.gov), 응답 본문에
    차단 페이지 특유의 문구가 있으면 의심한다. 둘 다 없으면 None(진짜 정상)."""
    orig_host = (urlparse(url).hostname or "").lower()
    final_host = (urlparse(final_url).hostname or "").lower() if final_url else ""
    if final_host and final_host != orig_host and not _bot_sensitive(final_url):
        return f"redirected_off_host:{final_host}"
    if _looks_like_block_page(body_sample):
        return "content_block_page"
    return None


def _classify(
    url: str, code: int | None, reason: str, *,
    final_url: str = "", body_sample: str = "",
) -> tuple[str, str]:
    if code is not None and 200 <= code < 400:
        # 200~399 를 무조건 ok 로 단정하지 않는다 — 봇 민감 호스트는 위장 차단(200 인데
        # 실제로는 차단 페이지)일 수 있어 최종 URL·본문을 추가로 본다.
        if _bot_sensitive(url):
            disguise = _suspected_disguised_block(url, final_url, body_sample)
            if disguise:
                return "blocked", f"suspected_bot_block:{disguise}"
        return "ok", "reachable"
    if code in (404, 410):
        return "broken", f"http_{code}"
    # ★`blocked` 는 **차단의 적극적 증거**가 있을 때만 쓴다(401/403/429, 또는 위 위장차단
    #   판정). 타임아웃·TLS 실패(code is None)는 차단의 증거가 아니라 그냥 못 닿은 것이므로
    #   needs_review 로 남긴다 — 이걸 blocked 에 넣으면 "확인 못 한 게 정상"이라는 통에
    #   진짜 네트워크 장애가 섞여 묻힌다(분리의 목적을 스스로 무너뜨린다).
    if _bot_sensitive(url) and code in (401, 403, 429):
        return "blocked", f"suspected_bot_block:http_{code}"
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
    # MFDS 계열 호스트만 KR egress 프록시를 태운다. 이 모듈은 grm_common 의 HTTP 헬퍼를
    # 쓰지 않고 requests 를 직접 잡으므로, 여기서 명시적으로 거치지 않으면 워크플로가
    # MFDS_HTTP_PROXY 를 넘겨도 **아무 효과가 없다**(env 전달 ≠ 배선).
    proxies = proxies_for(url)
    if proxies:
        kwargs["proxies"] = proxies
    return session.request(method, url, **kwargs)


def _read_body_sample(response: requests.Response) -> str:
    """GET 응답 본문 일부를 안전하게 읽는다 — 실패해도 결과는 빈 문자열(예외는 안 올린다).

    GET 요청 자체가 이미 Range: bytes=0-1023 헤더를 붙이므로 여기서 읽는 건 그 1KB 뿐."""
    try:
        return response.text or ""
    except Exception:  # noqa: BLE001 - 본문을 못 읽어도 상태코드 기반 판정으로 폴백
        return ""


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
    body_sample = ""
    sensitive = _bot_sensitive(url)
    for attempt in range(1, 3):  # initial attempt + one retry
        for method in ("HEAD", "GET"):
            last_method = method
            try:
                response = _request(
                    session, method, url, timeout=timeout, sleeper=sleeper, delay=delay,
                )
                last_code = response.status_code
                final_url = str(response.url or url)
                if method == "GET" and sensitive:
                    body_sample = _read_body_sample(response)
                response.close()
                if method == "HEAD" and last_code in (404, 410):
                    break  # definitive not-found — no fallback needed regardless of host
                if method == "HEAD" and not sensitive and 200 <= last_code < 400:
                    break  # non-sensitive host: HEAD success is enough
                # 봇 민감 호스트는 HEAD 가 200 대여도 GET 으로 넘어가 본문/리다이렉트를
                # 확인한다(HEAD 만으로는 위장 차단을 못 잡는다 — 본문이 없다).
                if method == "GET":
                    break
            except requests.RequestException as exc:
                last_code = None
                last_reason = type(exc).__name__
                # A failed HEAD still receives the required GET fallback.
                continue
        status, reason = _classify(
            url, last_code, last_reason, final_url=final_url, body_sample=body_sample,
        )
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
    max_workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    refs, source_files = collect_urls(library_dir)
    checked_at = _utc_now()
    results: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    pacer = HostPacer(sleeper=sleeper)

    def check(url: str) -> tuple[str, Probe]:
        sess = session or requests.Session()
        sess.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
        return url, probe_url(
            url, session=sess, delay=delay, timeout=timeout,
            sleeper=lambda seconds: pacer.wait(url, seconds),
        )

    completed: dict[str, Probe] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = [pool.submit(check, url) for url in sorted(refs)]
        for future in as_completed(futures):
            url, probe = future.result()
            completed[url] = probe
    for url in sorted(completed):
        probe = completed[url]
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
            "delay_scope": "per_host_request_start",
            "max_workers": max_workers,
            "bot_sensitive_hosts": list(BOT_SENSITIVE_HOSTS),
        },
        "source_files": source_files,
        "summary": {
            "unique_urls": len(results),
            "references": sum(len(value) for value in refs.values()),
            "ok": counts["ok"],
            "broken": counts["broken"],
            "needs_review": counts["needs_review"],
            # [2026-08-11] "검사기가 막혀 확인 못 함"은 "링크가 수상함"과 다른 사실이다.
            #   ★실측 계기: 21 CFR 63건이 러너에서 전부 unblock.federalregister.gov 로
            #   리다이렉트됐다(미국 정부가 GitHub Actions IP 를 봇으로 차단). 사람이
            #   브라우저로 누르면 정상이고 수집기도 eCFR **API** 로 63건을 정상 수집한다
            #   — 즉 링크는 멀쩡한데 **검사기만** 못 본다. 이걸 needs_review 에 합치면
            #   매주 63건이 "검토 필요"로 쌓여 진짜 신호(MFDS 5xx 같은 일시 오류)를 덮는다.
            #   무시되는 알림은 진짜 신호를 가린다(048 규율) → 별도 축으로 분리한다.
            "blocked": counts["blocked"],
        },
        "urls": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=Path("web/data/library"))
    parser.add_argument("--output", type=Path, default=Path("web/data/library_health.json"))
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args(argv)
    report = build_report(
        args.library_dir, delay=max(args.delay, 0), timeout=args.timeout,
        user_agent=args.user_agent, max_workers=args.max_workers,
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
