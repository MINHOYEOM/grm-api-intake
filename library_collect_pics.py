#!/usr/bin/env python3
"""GRM 자료실 수집기 — PIC/S 공식 발간물(GMP·검사 선별본).

자료실 플러그인 계약:
  LIBRARY_SOURCE = "pics"
  collect_library_items(run_date) -> (items, error)
  - 실패는 반드시 error 문자열로 보고한다(빈 리스트로 성공을 가장하지 않는다).

수집 경로 (probe 2026-07-20):
  https://picscheme.org/en/publications 는 서버 렌더 HTML 표다.
  각 행 = <a href="/docview/NNNN" title="YYYY-MM-DD">제목</a> | 참조번호 | 카테고리 | 섹션.
  robots.txt = "User-agent: * / Crawl-delay: 10" → Disallow 없음, 지연 10초 준수.

선별 필터 (자료실은 전체 미러가 아니다):
  참조번호(2열)가 공식 문서번호 형식(PE/PI NNN-N)인 행만 남긴다.
  → 초안(Draft)·컨셉페이퍼처럼 참조번호 대신 설명문이 오는 행은 제외된다.

id 규칙:
  pics-<문서번호 슬러그>  (예: "PI 056-1" → pics-pi-056-1, "PE 009-17 (Intro)" → pics-pe-009-17-intro)
  → 현행 큐레이션 32건 중 30건이 규칙으로 재현된다. 나머지 2건(Part I/Part II)은
     큐레이션이 로마숫자를 아라비아숫자로 적어 둔 것이라 _ID_ALIASES 로 기존 id 에 맞춘다
     (규칙을 바꾸면 기존 항목이 신규로 중복 추가되므로 기존 데이터가 기준).
"""

from __future__ import annotations

import html as html_lib
import re
import time
from datetime import date
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from grm_common import http_get_html, log

LIBRARY_SOURCE = "pics"

USER_AGENT = "GRM-Library/1.0 (+https://grm-solutions.com)"
SITE_BASE = "https://picscheme.org"
LIST_URL = f"{SITE_BASE}/en/publications"
DEFAULT_CRAWL_DELAY_SECONDS = 10.0

# 공식 문서번호: PE 009-17 / PI 056-1 / PE 009-17 (Intro)
_CODE_RE = re.compile(r"^(PE|PI) \d{3}-\d+( \([^)]+\))?$")
_ROW_RE = re.compile(
    r"<tr>\s*<td[^>]*>\s*<a\s+href=\"([^\"]+)\"[^>]*title=\"([^\"]*)\"[^>]*>(.*?)</a>\s*</td>"
    r"\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

# 큐레이션이 이미 쓰고 있는 id (로마숫자 → 아라비아숫자). 기존 데이터가 기준이다.
_ID_ALIASES = {
    "pics-pe-009-17-part-i": "pics-pe-009-17-part1",
    "pics-pe-009-17-part-ii": "pics-pe-009-17-part2",
}
# 섹션(4열) → 자료실 doc_type 표기
_DOC_TYPES = {
    "pic/s gmp guide": "GMP Guide",
    "guidance documents": "Guidance",
    "aide-memoires": "Aide-Memoire",
    "inspectorates": "Inspectorate procedure",
    "site master files": "Site Master File",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _robots(url: str) -> tuple[bool, float | None, str | None]:
    """robots.txt 를 실제로 읽고 판정한다. (allowed, crawl_delay, error)."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        text = http_get_html(robots_url, timeout=20, retries=2,
                             headers={"User-Agent": USER_AGENT}, label="PICS robots")
    except RuntimeError as exc:
        return False, None, f"robots.txt 확인 실패({robots_url}): {exc}"
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser.can_fetch(USER_AGENT, url), parser.crawl_delay(USER_AGENT), None


def _slug(code: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")


def item_id(code: str) -> str:
    generated = f"pics-{_slug(code)}"
    return _ID_ALIASES.get(generated, generated)


def parse_rows(html_text: str) -> list[dict[str, str]]:
    """발간물 표 → 원시 행 목록(필터 전)."""
    rows: list[dict[str, str]] = []
    for href, title_attr, title, reference, category, section in _ROW_RE.findall(html_text or ""):
        rows.append({
            "href": _clean(href),
            "date": _clean(title_attr),
            "title": _clean(title),
            "reference": _clean(reference),
            "category": _clean(category),
            "section": _clean(section),
        })
    return rows


def keep_row(row: dict[str, str]) -> bool:
    """공식 문서번호가 있는 행만 자료실 대상(초안·컨셉페이퍼 제외)."""
    return bool(_CODE_RE.match(row.get("reference", "")))


def derive_items(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not keep_row(row):
            continue
        href, title = row["href"], row["title"]
        if not href or not title:
            continue
        code = row["reference"]
        identifier = item_id(code)
        if identifier in seen:
            continue
        seen.add(identifier)
        item = {
            "id": identifier,
            "code": code,
            "title_en": title,
            "doc_type": _DOC_TYPES.get(row["section"].lower(), row["section"] or "Guidance"),
            "official_url": SITE_BASE + href if href.startswith("/") else href,
        }
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"]):
            item["published_date"] = row["date"]
        out.append(item)
    return out


def collect_library_items(run_date: date) -> tuple[list[dict], str | None]:
    """PIC/S 발간물 수집 진입점. 반환 (items, error)."""
    allowed, crawl_delay, robots_error = _robots(LIST_URL)
    if robots_error:
        return [], robots_error
    if not allowed:
        return [], f"robots.txt 가 수집을 금지함({LIST_URL}) — 우회하지 않고 중단"
    time.sleep(min(float(crawl_delay or DEFAULT_CRAWL_DELAY_SECONDS), 30.0))

    try:
        html_text = http_get_html(LIST_URL, timeout=40, retries=3,
                                  headers={"User-Agent": USER_AGENT}, label="PICS")
    except RuntimeError as exc:
        return [], f"PIC/S 발간물 페이지 수집 실패({LIST_URL}): {exc}"

    rows = parse_rows(html_text)
    if not rows:
        return [], f"PIC/S 발간물 표 0행({LIST_URL}) — 구조 변경 의심(수동 확인 필요)"
    items = derive_items(rows)
    if not items:
        return [], f"PIC/S 문서번호 매칭 0건(수집 {len(rows)}행) — 참조번호 형식 변경 의심"
    log("INFO", f"PIC/S 자료실 수집: 표 {len(rows)}행 / keep {len(items)}건")
    return items, None


if __name__ == "__main__":
    import json

    collected, err = collect_library_items(date.today())
    print(json.dumps({"count": len(collected), "error": err, "items": collected},
                     ensure_ascii=False, indent=2))
