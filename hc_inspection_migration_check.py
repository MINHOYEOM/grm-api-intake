#!/usr/bin/env python3
"""hc_inspection_migration_check.py — HC 의약품 GMP 실사 DB 의 새 도메인 이전을 감지한다.

배경(2026-11-08 진단): 옛 서버 `www.drug-inspections.canada.ca` 는 2026-10-31 부터 국내 PC·
GitHub 러너·클라우드 세 곳 모두에서 TLS·평문 HTTP 를 0바이트로 끊는다(IP 차단이 아니다).
Health Canada 는 새 앱 `drug-inspection-results.canada.ca` 로 DSO·GCP·CTO 를 먼저 옮겼고,
**의약품 GMP 는 아직 옮기지 않았다**(`/gmp/...` 404, canada.ca 안내 링크도 옛 호스트).
그래서 주간 재수집이 실패하면 원인은 "원천 이전 중"이고, 할 일은 GMP 가 새 도메인에
생기는 순간을 알아채는 것이다. 이 스크립트가 그 점검이다(읽기 전용, 비용 0).

판정(verdict):
  old_host_ok       옛 서버가 다시 응답한다 — 기존 수집기가 그대로 돈다
  gmp_migrated      새 도메인에 GMP 흔적(링크 또는 200 경로)이 있다 — 수집기 이전 필요
  migrating         옛 서버 불가 + 새 도메인은 살아 있으나 GMP 없음 — 이전 대기
  unknown           새 도메인도 닿지 않는다 — 네트워크/측정 문제일 수 있다

★GMP 흔적이 보이면 옛 서버가 살아 있어도 gmp_migrated 를 우선한다 — 옛 서버는 곧 꺼질
  것이고, 이전을 놓치는 쪽이 비싸다.

사용:
  python hc_inspection_migration_check.py [--output report.json] [--timeout 20]

종료코드: 항상 0(진단 도구 — 이전이 안 된 것은 이 스크립트의 실패가 아니다).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import requests

OLD_HOST_PAGE = "https://www.drug-inspections.canada.ca/gmp/index-en.html"
NEW_HOST = "https://drug-inspection-results.canada.ca"
# 새 앱의 기존 DB 는 `/<db>/en/search` · `/<db>/en/result` 꼴이다(dso·gcp·cto 실측).
NEW_GMP_CANDIDATES = (
    f"{NEW_HOST}/gmp/en/search",
    f"{NEW_HOST}/gmp/en/result",
)
# 링크를 훑을 페이지 — 새 앱 메인(→ /dso/en/search 로 302)과 canada.ca 안내 페이지.
# 안내 페이지의 GMP 링크가 새 도메인으로 바뀌는 것이 가장 확실한 이전 신호다.
LINK_PAGES = (
    f"{NEW_HOST}/",
    "https://www.canada.ca/en/health-canada/services/inspecting-monitoring-drug-health-products/"
    "drug-health-product-inspections.html",
)
_NEW_GMP_LINK_RE = re.compile(
    r"""href=["'](?:https?://drug-inspection-results\.canada\.ca)?/gmp/[^"']*["']""", re.I)

VERDICT_OLD_HOST_OK = "old_host_ok"
VERDICT_GMP_MIGRATED = "gmp_migrated"
VERDICT_MIGRATING = "migrating"
VERDICT_UNKNOWN = "unknown"

_UA = "GRM-source-check/1.0 (+https://grm-solutions.com)"

# ★새 도메인은 중간 인증서를 보내지 않는다(2026-11-08 실측: 체인에 leaf 1장, openssl
#   "unable to verify the first certificate"). 브라우저·Windows 는 AIA 로 알아서 채우지만
#   requests(certifi)는 CERTIFICATE_VERIFY_FAILED 로 죽는다. 검증을 끄지 않고 leaf 의 AIA
#   가 가리키는 공개 중간 인증서를 certifi 묶음에 **더해서** 다시 검증한다 — 그 중간 인증서도
#   certifi 의 루트로 서명 검증되므로 신뢰 범위는 넓어지지 않는다.
NEW_HOST_INTERMEDIATE_URL = "http://crt.sectigo.com/EntrustOVTLSIssuingRSACA2.crt"

HttpGet = Callable[..., Any]


def _is_cert_verify_failure(e: Exception) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in str(e)


def build_ca_bundle(http_get: HttpGet, timeout: int, dest: Path) -> str:
    """certifi 루트 + 새 도메인 중간 인증서 PEM 묶음 경로. 실패하면 ""."""
    import ssl

    import certifi

    try:
        resp = http_get(NEW_HOST_INTERMEDIATE_URL, timeout=timeout, headers={"User-Agent": _UA})
    except requests.RequestException:
        return ""
    body = resp.content or b""
    if int(resp.status_code) != 200 or not body:
        return ""
    pem = body.decode("ascii", "replace") if body.lstrip().startswith(b"-----BEGIN") \
        else ssl.DER_cert_to_PEM_cert(body)
    dest.write_text(Path(certifi.where()).read_text(encoding="utf-8") + "\n" + pem,
                    encoding="utf-8")
    return str(dest)


def _get(http_get: HttpGet, url: str, timeout: int,
         verify: Any = True) -> tuple[int, str, str]:
    """(status, text, error). 연결 실패면 status=0 이고 error 에 예외 요약."""
    try:
        resp = http_get(url, timeout=timeout, headers={"User-Agent": _UA},
                        allow_redirects=True, verify=verify)
    except requests.RequestException as e:
        return 0, "", f"{type(e).__name__}: {str(e)[:200]}"
    return int(resp.status_code), resp.text or "", ""


def gmp_links_in(html: str, page_url: str) -> list[str]:
    """페이지 HTML 에서 새 도메인 GMP 링크를 찾는다.

    ★상대 경로 `/gmp/...` 는 **새 앱 페이지 안에서만** 새 도메인 링크다. canada.ca 페이지의
    상대 `/gmp/` 는 canada.ca 자기 경로라 세면 오탐이 된다.
    """
    found = []
    on_new_host = page_url.startswith(NEW_HOST)
    for m in _NEW_GMP_LINK_RE.finditer(html or ""):
        href = m.group(0)
        if "drug-inspection-results.canada.ca" in href.lower() or on_new_host:
            found.append(href[6:-1])
    return sorted(set(found))


def check(*, http_get: HttpGet | None = None, timeout: int = 20) -> dict[str, Any]:
    http_get = http_get or requests.get
    report: dict[str, Any] = {
        "old_host_url": OLD_HOST_PAGE,
        "old_host_reachable": False,
        "old_host_error": "",
        "new_host_reachable": False,
        "new_host_incomplete_chain": False,
        "gmp_evidence": [],
        "checked": [],
        "verdict": VERDICT_UNKNOWN,
    }

    status, _, err = _get(http_get, OLD_HOST_PAGE, timeout)
    report["checked"].append({"url": OLD_HOST_PAGE, "status": status, "error": err})
    report["old_host_reachable"] = 200 <= status < 400
    report["old_host_error"] = err or ("" if report["old_host_reachable"] else f"HTTP {status}")

    # 새 도메인 요청에 쓸 verify 값. 첫 요청이 체인 미완성으로 죽으면 묶음을 만들어 바꾼다.
    new_verify: Any = True
    probe_status, _, probe_err = _get(http_get, f"{NEW_HOST}/robots.txt", timeout)
    if not probe_status and "CERTIFICATE_VERIFY_FAILED" in probe_err:
        report["new_host_incomplete_chain"] = True
        bundle = build_ca_bundle(
            http_get, timeout, Path(tempfile.gettempdir()) / "grm_hc_new_host_ca.pem")
        if bundle:
            new_verify = bundle
        else:
            report["new_host_bundle_error"] = "중간 인증서를 받지 못했다"

    def _get_new(url: str) -> tuple[int, str, str]:
        return _get(http_get, url, timeout, verify=new_verify)

    for url in NEW_GMP_CANDIDATES:
        status, _, err = _get_new(url)
        report["checked"].append({"url": url, "status": status, "error": err})
        if status:
            report["new_host_reachable"] = True
        if status == 200:
            report["gmp_evidence"].append(f"200 {url}")

    for url in LINK_PAGES:
        status, text, err = _get_new(url) if url.startswith(NEW_HOST) \
            else _get(http_get, url, timeout)
        report["checked"].append({"url": url, "status": status, "error": err})
        if url.startswith(NEW_HOST) and status:
            report["new_host_reachable"] = True
        if status == 200:
            for href in gmp_links_in(text, url):
                report["gmp_evidence"].append(f"link {href} @ {url}")

    if report["gmp_evidence"]:
        report["verdict"] = VERDICT_GMP_MIGRATED
    elif report["old_host_reachable"]:
        report["verdict"] = VERDICT_OLD_HOST_OK
    elif report["new_host_reachable"]:
        report["verdict"] = VERDICT_MIGRATING
    else:
        report["verdict"] = VERDICT_UNKNOWN
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="HC 의약품 GMP 실사 DB 새 도메인 이전 감지(읽기 전용)")
    p.add_argument("--output", help="JSON 리포트를 이 경로에도 기록.")
    p.add_argument("--timeout", type=int, default=20)
    args = p.parse_args(argv)

    report = check(timeout=args.timeout)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return 0


__all__ = ["check", "gmp_links_in", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
