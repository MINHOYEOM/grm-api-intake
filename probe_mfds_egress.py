#!/usr/bin/env python3
"""Probe KR-egress reachability for the blocked MFDS/nedrug/law.go.kr paths."""

from __future__ import annotations

import re
import sys

import requests

from grm_common import DEFAULT_USER_AGENT, _proxies_for


PROBES = [
    (
        "mfds-guidance-rss",
        "https://www.mfds.go.kr/www/rss/brd.do?brdId=data0011",
    ),
    (
        "nedrug-gmp-inspection-list",
        "https://nedrug.mfds.go.kr/pbp/CCBBD03/getList?page=1&limit=10",
    ),
    (
        "law-go-kr-drf",
        "https://www.law.go.kr/DRF/lawService.do",
    ),
]


def _mask_url(url: str) -> str:
    url = re.sub(r"([?&]OC=)[^&]+", r"\1***REDACTED***", url)
    return re.sub(r"([?&]serviceKey=)[^&]+", r"\1***REDACTED***", url)


def main() -> int:
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
    all_ok = True
    for label, url in PROBES:
        try:
            resp = requests.get(
                url,
                headers=headers,
                timeout=20,
                allow_redirects=True,
                proxies=_proxies_for(url),
            )
            ok = resp.status_code == 200
            all_ok = all_ok and ok
            status = "OK" if ok else "FAIL"
            print(
                f"[{status}] {label}: HTTP {resp.status_code} "
                f"bytes={len(resp.content)} final_url={_mask_url(resp.url)}"
            )
        except Exception as e:  # noqa: BLE001
            all_ok = False
            print(f"[FAIL] {label}: {e}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
