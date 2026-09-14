#!/usr/bin/env python3
"""Probe KR-egress reachability for the blocked MFDS/nedrug/law.go.kr paths.

[2026-09-14] 첫 줄에 `[PROXY] reachable|unreachable|unconfigured` 를 찍는다 — 프록시 1대의
도달 여부와 원 서버 도달 여부를 분리해 읽기 위해서다. 프로브는 수집기와 같은
`kr_egress_get` 을 타므로 프록시 홉이 죽어 있으면 직결 폴백 결과가 찍힌다.
"""

from __future__ import annotations

import re
import sys


from grm_common import (
    DEFAULT_USER_AGENT,
    KR_EGRESS_PROXY_UNREACHABLE,
    kr_egress_get,
    probe_kr_egress_proxy,
)


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
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    # [2026-09-14] 프록시 **도달** 여부를 먼저 찍는다. 아래 프로브가 전부 실패해도 이 줄이
    # "원 서버가 막았다"와 "프록시 1대가 죽었다"를 가른다(이슈 #983/#956 은 그걸 못 갈랐다).
    proxy_status, proxy_detail = probe_kr_egress_proxy()
    print(f"[PROXY] {proxy_status}: {proxy_detail}")
    if proxy_status == KR_EGRESS_PROXY_UNREACHABLE:
        print("[PROXY] 프록시 복구(EC2 재기동 또는 Secret MFDS_HTTP_PROXY 교체)는 사람만 할 수 있다 "
              "— 아래 프로브는 직결 폴백 결과다.")

    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
    all_ok = True
    for label, url in PROBES:
        try:
            # 수집기와 같은 경로를 탄다 — 프록시 홉 실패면 직결로 1회 폴백(그 폴백이 먹혔는지는
            # 위 [PROXY] 줄과 함께 읽는다: unreachable 인데 OK 면 "오늘은 직결이 열렸다").
            resp = kr_egress_get(
                url,
                headers=headers,
                timeout=20,
                allow_redirects=True,
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
