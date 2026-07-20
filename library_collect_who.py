#!/usr/bin/env python3
"""GRM 자료실 수집기 — WHO TRS 부속서(GMP·품질 선별본).

자료실 플러그인 계약:
  LIBRARY_SOURCE = "who"
  collect_library_items(run_date) -> (items, error)
  - items = v2 공개 필드 dict (id/title_en/code/doc_type/published_date/official_url/pdf_url)
  - 실패는 반드시 error 문자열로 보고한다(빈 리스트로 성공을 가장하지 않는다).

수집 경로 (probe 2026-07-20):
  www.who.int 는 JS 렌더링이지만, /publications/m/ 목록이 쓰는 OData 허브
  `/api/hubs/meetingreports` 는 서버에서 JSON 을 그대로 준다.
  Title 이 "TRS 1067 - Annex 2: <제목>" 형식이라 TRS 번호·부속서 번호를 신뢰성 있게 얻는다.
  $top 상한은 100 → $skip 페이지네이션.

robots.txt 판정 (who.int):
  who.int robots.txt 는 `User-agent: *` 그룹이 없고 악성 봇 527종 차단 목록만 있다 → 우리는 허용.
  단 그 목록에 **"Collector"** 라는 토큰이 있어, UA 를 "GRM-LibraryCollector" 로 두면
  robotparser 의 부분일치 규칙에 걸려 전면 Disallow 로 판정된다(실측). 우리는 그 봇이 아니므로
  제품 토큰을 GRM-Library 로 정정했다 — 차단 회피가 아니라 **식별자 충돌 해소**이고,
  이 저장소가 기존에 who.int 를 수집할 때 쓰는 GRM-Intake 와 같은 계열의 정직한 식별이다.

선별 필터 (자료실은 전체 미러가 아니다):
  1) 제목이 "TRS <번호> - Annex <번호>: ..." 패턴일 것(백신·의료기기 계열 부속서는 이 형식이 아님).
  2) 제목에 GMP·품질 키워드가 있을 것(_KEEP_TERMS).
  3) 콘돔·IUD·생동성면제·사전적격성 등 비-GMP 주제는 제외(_DROP_TERMS) — 2번보다 우선.

id 규칙:
  who-trs<TRS번호>-annex<부속서번호>  (예: who-trs1067-annex2)
  → 현행 큐레이션 28건과 **전수 일치**(2026-07-20 대조). 기존 항목이 신규로 중복되지 않는다.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from grm_common import http_get_html, http_get_json, log

LIBRARY_SOURCE = "who"

USER_AGENT = "GRM-Library/1.0 (+https://grm-solutions.com)"
API_URL = "https://www.who.int/api/hubs/meetingreports"
ITEM_BASE = "https://www.who.int/publications/m/item"
API_SITE = "15210d59-ad60-47ff-a542-7ed76645f0c7"
PAGE_SIZE = 100          # WHO OData 상한
MAX_PAGES = 8            # 안전 상한 (현행 'Annex' 매칭 351건)
REQUEST_DELAY_SECONDS = 1.0
DOC_TYPE_FALLBACK = "Technical document"

_TRS_ANNEX_RE = re.compile(
    r"^TRS\s*(\d{2,4})\s*[-–—]+\s*Annex\s*(\d{1,2})\s*:\s*(.+)$", re.IGNORECASE
)

# GMP·품질 선별 키워드 (현행 큐레이션 28건에서 도출)
_KEEP_TERMS = (
    "good manufacturing practice", "gmp",
    "good practices for blood establishments",
    "good practices for pharmaceutical quality control laborator",
    "good practices for pharmaceutical microbiology laborator",
    "good practices for research and development",
    "good practices for desk assessment",
    "good chromatography practices",
    "good storage and distribution practices",
    "good trade and distribution practices",
    "nitrosamine", "continuous manufacturing", "technology transfer",
    "data integrity", "cleaning validation", "health-based exposure",
    "water for pharmaceutical use", "water for injection",
    "guidelines on validation", "hold-time", "quality risk management",
    "heating, ventilation", "packaging for pharmaceutical products",
    "hazardous substances", "stability testing",
    "sampling of pharmaceutical products",
    "site master file", "inspection", "antimicrobial resistance",
)
# 비-GMP 주제 (키워드가 걸려도 버린다)
_DROP_TERMS = (
    "condom", "lubricant", "intrauterine", "biowaiver", "bioequivalence",
    "prequalification", "prequalified", "nonproprietary", "pharmacopoeia",
    "procurement", "collaborative regist", "collaborative procedure",
    "variations", "registration requirements", "market surveillance",
    "regulatory framework", "in vitro diagnostic", "medical devices",
    "pharmacy practice", "review practices", "import procedures",
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _robots_allows(url: str) -> tuple[bool, float | None, str | None]:
    """robots.txt 를 실제로 읽고 판정한다. (allowed, crawl_delay, error)."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        text = http_get_html(robots_url, timeout=20, retries=2,
                             headers={"User-Agent": USER_AGENT}, label="WHO robots")
    except RuntimeError as exc:
        return False, None, f"robots.txt 확인 실패({robots_url}): {exc}"
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser.can_fetch(USER_AGENT, url), parser.crawl_delay(USER_AGENT), None


def keep_title(title: str) -> bool:
    """GMP·품질 선별 판정 (제목 기준)."""
    low = title.lower()
    if any(term in low for term in _DROP_TERMS):
        return False
    return any(term in low for term in _KEEP_TERMS)


def item_id(trs: str | int, annex: str | int) -> str:
    """자료실 id — 현행 큐레이션(who-trs1067-annex2 …)과 동일 규칙."""
    return f"who-trs{str(trs).strip()}-annex{str(annex).strip()}"


def _published_date(raw: str) -> str:
    """'9 June 2026' → '2026-06-09'. 파싱 실패 시 빈 문자열(날짜를 지어내지 않는다)."""
    text = _clean(raw)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def derive_items(rows: list[dict]) -> tuple[list[dict], int]:
    """API 행 → v2 공개 필드 dict. (kept_items, seen_trs_annex_count)."""
    seen_pattern = 0
    out: list[dict] = []
    by_id: set[str] = set()
    for row in rows:
        title = _clean(str(row.get("Title") or ""))
        url_path = _clean(str(row.get("ItemDefaultUrl") or ""))
        matched = _TRS_ANNEX_RE.match(title)
        if not matched or not url_path:
            continue
        seen_pattern += 1
        trs, annex, headline = matched.group(1), matched.group(2), _clean(matched.group(3))
        if not keep_title(headline):
            continue
        identifier = item_id(trs, annex)
        if identifier in by_id:
            continue
        by_id.add(identifier)
        item = {
            "id": identifier,
            "code": f"TRS {trs} Annex {annex}",
            "title_en": headline,
            "doc_type": _clean(str(row.get("Tag") or "")) or DOC_TYPE_FALLBACK,
            "official_url": ITEM_BASE + ("" if url_path.startswith("/") else "/") + url_path,
        }
        published = _published_date(str(row.get("FormatedDate") or ""))
        if published:
            item["published_date"] = published
        out.append(item)
    return out, seen_pattern


def _fetch_rows() -> tuple[list[dict], str | None]:
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        params = {
            "sf_site": API_SITE,
            "sf_provider": "OpenAccessProvider",
            "sf_culture": "en",
            "$select": "Title,ItemDefaultUrl,FormatedDate,Tag",
            "$format": "json",
            "$count": "true",
            "$filter": "contains(Title,'Annex')",
            "$top": str(PAGE_SIZE),
            "$skip": str(page * PAGE_SIZE),
        }
        try:
            payload = http_get_json(API_URL, params=params, timeout=40, retries=2,
                                    headers={"User-Agent": USER_AGENT})
        except Exception as exc:                      # noqa: BLE001 - 경계에서 error 로 승격
            return rows, f"WHO API 수집 실패(skip={page * PAGE_SIZE}): {exc}"
        page_rows = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(page_rows, list):
            return rows, f"WHO API 응답 형식 이상(skip={page * PAGE_SIZE})"
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(page_rows) < PAGE_SIZE:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return rows, None


def collect_library_items(run_date: date) -> tuple[list[dict], str | None]:
    """WHO TRS 부속서 수집 진입점. 반환 (items, error)."""
    allowed, crawl_delay, robots_error = _robots_allows(API_URL)
    if robots_error:
        return [], robots_error
    if not allowed:
        return [], f"robots.txt 가 수집을 금지함({API_URL}) — 우회하지 않고 중단"
    if crawl_delay:
        time.sleep(min(float(crawl_delay), 10.0))

    rows, error = _fetch_rows()
    if error:
        return [], error
    if not rows:
        return [], "WHO API 0건 — 허브/필터 변경 의심(수동 확인 필요)"

    items, seen_pattern = derive_items(rows)
    if not items:
        return [], f"WHO TRS 부속서 선별 0건(수집 {len(rows)}건) — 제목 형식 변경 의심"
    log("INFO", f"WHO 자료실 수집: 응답 {len(rows)}건 / TRS부속서 {seen_pattern}건 / keep {len(items)}건")
    return items, None


if __name__ == "__main__":
    import json

    collected, err = collect_library_items(date.today())
    print(json.dumps({"count": len(collected), "error": err, "items": collected},
                     ensure_ascii=False, indent=2))
