#!/usr/bin/env python3
"""IndexNow 제출 — 새로 생기거나 바뀐 URL 을 네이버·빙에 자동 통지한다(비용 0).

네이버 서치어드바이저 '수집 요청'은 브라우저에서 사람이 눌러야 했다. IndexNow 는 키 파일을
사이트 루트에 공개해 두고 URL 목록을 POST 하는 공식 프로토콜이라(네이버·빙 지원) 배포 직후
자동화할 수 있다. 키는 프로토콜상 공개값이므로 상수로 커밋한다(`render_site()` 가
`/{INDEXNOW_KEY}.txt` 를 dist 루트에 내보낸다).

사용:
  python indexnow_submit.py --sitemap web/dist/sitemap-full.xml --previous prev_sitemap_full.xml
  python indexnow_submit.py --sitemap web/dist/sitemap-full.xml --all      # 첫 전수 제출

[구글 품질 1단계 2026-11-19] 비교 대상은 **전체 목록 `sitemap-full.xml`** 이다. `sitemap.xml` 은 이제
구글용이라 googlebot noindex 페이지(약 6천 장)가 빠져 있다 — 그걸로 비교하면 네이버·빙이 그 페이지를
새로 받지 못하고, 둘을 섞어 비교하면(이전=구글용·지금=전체) 빠진 몫 전부가 "새 URL"로 대량 재제출된다.
그래서 증분 제출에는 상한(--max-urls, 기본 MAX_INCREMENTAL)을 두고, 넘으면 아무것도 보내지 않는다
(빙의 대량 제출 자제 권고). 정말 전수가 필요하면 사람이 --all 로 돌린다.

--previous 가 있으면 새 URL 또는 `<lastmod>` 가 바뀐 URL 만 보낸다. 제출 실패(4xx/5xx·네트워크)는
배포를 막지 않도록 경고만 남기고 exit 0 (`--strict` 일 때만 exit 1).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET

INDEXNOW_KEY = "378d11bcbbf09c19f1f4456cb3900e7f"   # 공개값(프로토콜 설계상)
HOST = "grm-solutions.com"
KEY_LOCATION = f"https://{HOST}/{INDEXNOW_KEY}.txt"
ENDPOINTS = (
    "https://searchadvisor.naver.com/indexnow",
    "https://www.bing.com/indexnow",
)
BATCH_SIZE = 10000
TIMEOUT = 30
RETRIES = 2
OK_STATUS = (200, 202)
#: 증분 제출 1회 상한. 평시 증분은 수십~수백 건이고, 가장 큰 정상 증분(11-16 조항 원문 신설)이 약 2,900건
#: 이었다. 구글용·전체 sitemap 을 섞어 비교하는 사고는 약 6천 건이라 이 선에서 막힌다.
MAX_INCREMENTAL = 5000


def parse_sitemap(text: str) -> dict[str, str]:
    """sitemap.xml 문자열 → {loc: lastmod("" 가능)}."""
    out: dict[str, str] = {}
    root = ET.fromstring(text)
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "url":
            continue
        loc = lastmod = ""
        for ch in el:
            name = ch.tag.rsplit("}", 1)[-1]
            if name == "loc":
                loc = (ch.text or "").strip()
            elif name == "lastmod":
                lastmod = (ch.text or "").strip()
        if loc:
            out[loc] = lastmod
    return out


def diff_urls(current: dict[str, str], previous: dict[str, str] | None) -> list[str]:
    """previous 가 None 이면 전부. 아니면 새 URL + lastmod 가 달라진 URL (sitemap 순서 유지)."""
    if previous is None:
        return list(current)
    return [u for u, m in current.items() if u not in previous or previous[u] != m]


def batches(urls: list[str], size: int = BATCH_SIZE):
    for i in range(0, len(urls), size):
        yield urls[i:i + size]


def build_payload(urls: list[str], *, host: str = HOST, key: str = INDEXNOW_KEY,
                  key_location: str = KEY_LOCATION) -> dict:
    return {"host": host, "key": key, "keyLocation": key_location, "urlList": urls}


def post_batch(endpoint: str, payload: dict, *, session=None) -> int | None:
    """한 배치를 POST. 200/202 면 상태코드, 아니면 마지막 상태(예외만 났으면 None). 재시도 RETRIES."""
    if session is None:
        import requests as session  # type: ignore[no-redef]
    status = None
    for attempt in range(RETRIES + 1):
        try:
            r = session.post(endpoint, data=json.dumps(payload),
                             headers={"Content-Type": "application/json; charset=utf-8"},
                             timeout=TIMEOUT)
            status = r.status_code
            if status in OK_STATUS:
                return status
        except Exception as e:  # noqa: BLE001 — 네트워크 오류도 경고로만
            print(f"::warning::IndexNow {endpoint} 예외: {e}")
            status = None
        if attempt < RETRIES:
            time.sleep(1 + attempt)
    return status


def submit(urls: list[str], endpoints=ENDPOINTS, *, session=None) -> dict[str, dict[str, int]]:
    """모든 엔드포인트에 배치 제출. 반환: {endpoint: {"ok": n, "fail": n}} (URL 수 기준)."""
    result: dict[str, dict[str, int]] = {}
    for ep in endpoints:
        res = {"ok": 0, "fail": 0}
        for chunk in batches(urls):
            st = post_batch(ep, build_payload(chunk), session=session)
            if st in OK_STATUS:
                res["ok"] += len(chunk)
            else:
                res["fail"] += len(chunk)
                print(f"::warning::IndexNow {ep} 배치 {len(chunk)}건 실패 (status={st})")
        result[ep] = res
    return result


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--sitemap", required=True, help="빌드된(또는 라이브) sitemap.xml 경로")
    ap.add_argument("--previous", help="배포 전 sitemap.xml — 있으면 새/변경 URL 만")
    ap.add_argument("--all", action="store_true", help="모든 URL 제출(첫 전수 제출)")
    ap.add_argument("--endpoint", action="append", help="엔드포인트(여러 번 가능, 기본 네이버+빙)")
    ap.add_argument("--dry-run", action="store_true", help="대상 수만 출력하고 전송 안 함")
    ap.add_argument("--strict", action="store_true", help="실패 시 exit 1")
    ap.add_argument("--max-urls", type=int, default=MAX_INCREMENTAL,
                    help="증분 제출 상한 — 넘으면 보내지 않는다(--all 에는 적용 안 함)")
    args = ap.parse_args(argv)

    current = parse_sitemap(_read(args.sitemap))
    previous = None
    if not args.all and args.previous:
        try:
            previous = parse_sitemap(_read(args.previous))
        except (OSError, ET.ParseError) as e:
            print(f"::warning::이전 sitemap 읽기 실패({e}) — 전수 대신 중단")
            return 1 if args.strict else 0
    elif not args.all:
        print("::warning::--previous 도 --all 도 없음 — 제출 안 함")
        return 1 if args.strict else 0

    urls = diff_urls(current, previous)
    print(f"IndexNow: sitemap {len(current)}건 중 제출 대상 {len(urls)}건")
    if previous is not None and len(urls) > args.max_urls:
        print(f"::warning::증분 {len(urls)}건이 상한 {args.max_urls}건을 넘어 제출하지 않음 — "
              "비교 대상 sitemap 이 서로 다른 목록인지 확인(전수는 grm-indexnow-full.yml)")
        return 1 if args.strict else 0
    if not urls or args.dry_run:
        return 0
    result = submit(urls, tuple(args.endpoint) if args.endpoint else ENDPOINTS)
    failed = 0
    for ep, r in result.items():
        print(f"IndexNow {ep}: 성공 {r['ok']} / 실패 {r['fail']}")
        failed += r["fail"]
    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
