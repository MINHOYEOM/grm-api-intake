#!/usr/bin/env python3
"""GRM 자료실 수집기 — EU GMP (EudraLex Volume 4).

자료실 플러그인 계약:
  LIBRARY_SOURCE = "eu_gmp"
  collect_library_items(run_date) -> (items, error)
  - 실패는 반드시 error 문자열로 보고한다(빈 리스트로 성공을 가장하지 않는다).

수집 경로 (probe 2026-07-20):
  https://health.ec.europa.eu/medicinal-products/eudralex/eudralex-volume-4_en 는 서버 렌더 HTML.
  <h3> 섹션 아래 Part I~III 는 <li> 목록, Annexes 는 표(<tr><td>Annex N</td><td><a>…)다.
  robots.txt 는 /core/·/admin/ 등만 Disallow → 본 페이지는 허용(크롤 지연 지시 없음, 자체 1초 지연).

선별 필터 (자료실은 전체 미러가 아니다):
  사람용 의약품 GMP 본문만 남긴다 — Part I / Part II / Part III / Annexes.
  제외: Introduction·Glossary·Part IV(ATMP)·GDP·법령(Legal acts)·수의용(veterinary) 섹션.
  → 현행 큐레이션 38건과 정확히 같은 범위다.

id 규칙:
  현행 큐레이션 id 는 사람이 붙인 의미 슬러그(eu-gmp-part3-hbel 등)라 규칙으로 재현할 수 없다.
  따라서 **문서 URL(EC 문서 UUID) → 기존 id** 대조표(_CURATED_IDS)를 정본으로 두고,
  표에 없는 새 문서만 결정론적 해시 id(eu-gmp-<sha1 12>)를 부여한다.
  (규칙을 새로 만들면 기존 38건이 전부 "신규"로 중복 추가된다 — 기존 데이터가 기준.)
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
import time
from datetime import date
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from grm_common import http_get_html, log

LIBRARY_SOURCE = "eu_gmp"

USER_AGENT = "GRM-Library/1.0 (+https://grm-solutions.com)"
SITE_BASE = "https://health.ec.europa.eu"
LIST_URL = f"{SITE_BASE}/medicinal-products/eudralex/eudralex-volume-4_en"
REQUEST_DELAY_SECONDS = 1.0

_TAG_RE = re.compile(r"<[^>]+>")
_H_RE = re.compile(r"<h([23])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_A_RE = re.compile(r"<a\s+[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_PART_RE = re.compile(r"^part (i|ii|iii)\b", re.IGNORECASE)
_CHAPTER_RE = re.compile(r"^chapter\s+(\d+)\s*[-–—:]\s*(.+)$", re.IGNORECASE)
_ANNEX_CODE_RE = re.compile(r"^annex\s+\d+", re.IGNORECASE)
_DOC_UUID_RE = re.compile(r"/document/download/([0-9a-f]{8}-[0-9a-f-]{27})_en")
# EUR-Lex 는 같은 문서를 여러 URL 로 준다(TXT/·TXT/PDF/·http/https) → CELEX 번호를 키로 쓴다.
_CELEX_RE = re.compile(r"CELEX(?::|%3A)([0-9A-Za-z()]+)", re.IGNORECASE)

# 문서 URL 키 → 현행 큐레이션 id (2026-07-20 web/data/library/eu_gmp.json 전수 38건 대조).
# 키 = EC 문서 UUID(재업로드로 filename 이 바뀌어도 안정), EC 외부 문서는 전체 URL.
_CURATED_IDS = {
    "e458c423-f564-4171-b344-030a461c567f": "eu-gmp-part1-ch1",
    "11f4f8e6-a6e9-4897-afe3-f21e1dc56cb8": "eu-gmp-part1-ch2",
    "18d76565-137b-41d2-a602-794527f708c1": "eu-gmp-part1-ch3",
    "104b3eb8-81a7-4858-9419-cb06562adb66": "eu-gmp-part1-ch4",
    "4a1fdb4f-6f6f-49c4-b264-8056e5bbe078": "eu-gmp-part1-ch5",
    "c74c8720-27bf-4252-808f-d65a206a90bb": "eu-gmp-part1-ch6",
    "58b5106a-cf6f-4352-9dca-1caf5d27d97e": "eu-gmp-part1-ch7",
    "b1eb2292-cb0d-4e3f-aea9-e3fe79faf6e3": "eu-gmp-part1-ch8",
    "07195808-d02e-4d7a-b8f4-f84a83278b62": "eu-gmp-part1-ch9",
    "bd537ccf-9271-4230-bca1-2d8cb655fd83": "eu-gmp-part2-active-substances",
    "95af86f8-c82d-4ad0-85cb-27c7f56531b4": "eu-gmp-part3-site-master-file",
    "d77ad692-97ae-4a5f-acfa-e853193ef6aa": "eu-gmp-part3-q9",
    "https://www.ema.europa.eu/en/ich-q10-pharmaceutical-quality-system-scientific-guideline":
        "eu-gmp-part3-q10",
    "19052fee-a596-47a1-b564-052128360d82": "eu-gmp-part3-mra-batch-certificate",
    "ab1d8ff2-fdc9-49ab-a3aa-73f80471f92b": "eu-gmp-part3-written-confirmation",
    "https://www.ema.europa.eu/en/setting-health-based-exposure-limits-use-risk-identification-"
    "manufacture-different-medicinal-products-shared-facilities-scientific-guideline":
        "eu-gmp-part3-hbel",
    "celex:52015XC0321(02)": "eu-gmp-part3-excipient-risk-assessment",
    "3b293ba6-c7f9-4a63-8121-303a18c30120": "eu-gmp-part3-imp-batch-release-template",
    "6e8ae778-73d0-4a1e-a374-a06e791152a7": "eu-gmp-part3-imp-handling-shipping",
    "c3bac13b-689e-4d01-8321-dbf088bb692a": "eu-gmp-part3-mah-reflection-paper",
    "e05af55b-38e9-42bf-8495-194bbf0b9262": "eu-gmp-annex1",
    "380fdf24-8a1e-4f65-809b-e08d990d5f9e": "eu-gmp-annex2",
    "bf281e1f-4897-469a-ba60-18d867b14a94": "eu-gmp-annex3",
    "b9dfcd18-73d2-45f8-a3a7-e4558fdc2d58": "eu-gmp-annex6",
    "fd318dd6-2404-4e67-82b0-2324825e4d90": "eu-gmp-annex7",
    "22d03c04-8512-4336-89dd-556771fca388": "eu-gmp-annex8",
    "3db7d485-61e2-4f61-a16a-82a6dbf0e914": "eu-gmp-annex9",
    "2a19f8d5-cc25-44ea-9296-d38e3a6c278c": "eu-gmp-annex10",
    "8d305550-dd22-4dad-8463-2ddb4a1345f1": "eu-gmp-annex11",
    "363e435b-23cf-4cb3-9c74-322addb81340": "eu-gmp-annex12",
    "a0b206a0-5788-406b-9e20-e0525b16e712": "eu-gmp-annex13",
    "a28a88b2-d510-40c6-a40d-c1989819e13e": "eu-gmp-annex14",
    "7c6c5b3c-4902-46ea-b7ab-7608682fb68d": "eu-gmp-annex15",
    "20c41532-33d5-4635-ae80-8735d3d09fe0": "eu-gmp-annex16",
    "78d31fc9-760f-4fe9-9ccd-95595ef48a71": "eu-gmp-annex17",
    "b9b0cf46-07a6-4243-b801-446ccfcd2d72": "eu-gmp-annex19-2005",
    "831d392e-bcc4-4552-8011-2b724b8655de": "eu-gmp-annex19-2026",
    "e2ddfe65-7b4e-4765-b71b-4681772d2949": "eu-gmp-annex21",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _robots(url: str) -> tuple[bool, float | None, str | None]:
    """robots.txt 를 실제로 읽고 판정한다. (allowed, crawl_delay, error)."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        text = http_get_html(robots_url, timeout=20, retries=2,
                             headers={"User-Agent": USER_AGENT}, label="EU robots")
    except RuntimeError as exc:
        return False, None, f"robots.txt 확인 실패({robots_url}): {exc}"
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser.can_fetch(USER_AGENT, url), parser.crawl_delay(USER_AGENT), None


def url_key(url: str) -> str:
    """id 대조용 안정 키 — EC 문서는 UUID, EUR-Lex 는 CELEX 번호, 그 외는 전체 URL."""
    matched = _DOC_UUID_RE.search(url or "")
    if matched:
        return matched.group(1)
    celex = _CELEX_RE.search(url or "")
    if celex:
        return "celex:" + celex.group(1)
    return url or ""


def item_id(url: str) -> str:
    key = url_key(url)
    curated = _CURATED_IDS.get(key)
    if curated:
        return curated
    return "eu-gmp-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _sections(html_text: str) -> list[tuple[str, str]]:
    """(h3 제목, 섹션 HTML) 목록. 수의용(veterinary) H2 이후는 잘라낸다."""
    body = html_text or ""
    start = body.find("<article")
    if start >= 0:
        end = body.find("</article>", start)
        body = body[start:end if end > start else len(body)]
    for match in _H_RE.finditer(body):
        if match.group(1) == "2" and "veterinary" in _clean(match.group(2)).lower():
            body = body[:match.start()]
            break
    out: list[tuple[str, str]] = []
    headings = [m for m in _H_RE.finditer(body) if m.group(1) == "3"]
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        out.append((_clean(match.group(2)), body[match.end():end]))
    return out


def _part_items(label: str, section_html: str) -> list[dict[str, str]]:
    """Part I~III 섹션: <li> 당 첫 링크 1건(부가 'here' 링크 등은 제외)."""
    part = label.split("-")[0].split("–")[0].strip().rstrip(",")
    out: list[dict[str, str]] = []
    for block in _LI_RE.findall(section_html):
        link = _A_RE.search(block)
        if not link:
            continue
        url = urljoin(SITE_BASE, html_lib.unescape(link.group(1).strip()))
        title = _clean(link.group(2))
        if not title:
            continue
        code = part
        chapter = _CHAPTER_RE.match(title)
        if chapter:
            code = f"{part}, Chapter {chapter.group(1)}"
            title = _clean(chapter.group(2))
        item = {"id": item_id(url), "code": code, "title_en": title,
                "doc_type": part, "official_url": url}
        if ".pdf" in url.lower():
            item["pdf_url"] = url
        out.append(item)
    return out


def _annex_items(section_html: str) -> list[dict[str, str]]:
    """Annexes 표: 1열 = 'Annex N', 2열의 모든 문서 링크(개정판 병기 대응)."""
    out: list[dict[str, str]] = []
    for row in _TR_RE.findall(section_html):
        cells = _TD_RE.findall(row)
        if len(cells) < 2:
            continue
        code = _clean(cells[0])
        if not _ANNEX_CODE_RE.match(code):
            continue
        for href, text in _A_RE.findall(cells[1]):
            href = html_lib.unescape(href.strip())
            if "/document/download/" not in href:
                continue
            url = urljoin(SITE_BASE, href)
            title = _clean(text)
            if not title:
                continue
            item = {"id": item_id(url), "code": code, "title_en": title,
                    "doc_type": "Annex", "official_url": url}
            if ".pdf" in url.lower():
                item["pdf_url"] = url
            out.append(item)
    return out


def derive_items(html_text: str) -> tuple[list[dict[str, str]], int]:
    """페이지 → v2 공개 필드 dict. (kept_items, 훑은 섹션 링크 수)."""
    seen_links = 0
    items: list[dict[str, str]] = []
    by_id: set[str] = set()
    for label, section_html in _sections(html_text):
        seen_links += len(_A_RE.findall(section_html))
        if _PART_RE.match(label):
            derived = _part_items(label, section_html)
        elif label.strip().lower() == "annexes":
            derived = _annex_items(section_html)
        else:
            continue
        for item in derived:
            if item["id"] in by_id:
                continue
            by_id.add(item["id"])
            items.append(item)
    return items, seen_links


def collect_library_items(run_date: date) -> tuple[list[dict], str | None]:
    """EudraLex Volume 4 수집 진입점. 반환 (items, error)."""
    allowed, crawl_delay, robots_error = _robots(LIST_URL)
    if robots_error:
        return [], robots_error
    if not allowed:
        return [], f"robots.txt 가 수집을 금지함({LIST_URL}) — 우회하지 않고 중단"
    time.sleep(min(float(crawl_delay or REQUEST_DELAY_SECONDS), 30.0))

    try:
        html_text = http_get_html(LIST_URL, timeout=40, retries=3,
                                  headers={"User-Agent": USER_AGENT}, label="EU GMP")
    except RuntimeError as exc:
        return [], f"EudraLex Volume 4 페이지 수집 실패({LIST_URL}): {exc}"

    items, seen_links = derive_items(html_text)
    if not items:
        return [], (f"EudraLex Volume 4 선별 0건(페이지 링크 {seen_links}건) — "
                    f"섹션 구조 변경 의심(수동 확인 필요)")
    log("INFO", f"EU GMP 자료실 수집: 섹션 링크 {seen_links}건 / keep {len(items)}건")
    return items, None


if __name__ == "__main__":
    import json

    collected, err = collect_library_items(date.today())
    print(json.dumps({"count": len(collected), "error": err, "items": collected},
                     ensure_ascii=False, indent=2))
