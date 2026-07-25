#!/usr/bin/env python3
"""GRM 자료실 수집기 — PMDA 실사 지적사례 ORANGE Letter(pmda 카탈로그).

플러그인 계약:
  LIBRARY_SOURCE / collect_library_items(run_date) -> (items, error)
  items = 카탈로그 v2 공개 필드 dict 목록.

수집 경로 (2026-07-25 실측):
  영문 GMP/QMS/GCTP 품질보증 페이지 1장이 전부다 — 완전 서버렌더 HTML 이고
  JS·API 경유가 없다. robots.txt 자체가 없다(404) — 금지 경로 없음.
  섹션 구분은 <h2> 하나뿐이라 h2 경계로 문서를 잘라 섹션별 doc_type 을 부여한다.
    · "GMP / GCTP Annual Report"              -> annual-report
    · "Publication of Inspectional observations" -> inspection-observation (ORANGE Letter)
  그 밖의 <h2>(Reviews and Related Services 등)는 수집 대상이 아니다.

선별(keep) 기준:
  1) 위 두 섹션 안의 `/files/<숫자>.pdf` 링크만 — 연차보고서에 딸린 .xlsx 별첨
     (List of Identified Deficiencies)은 현행 큐레이션 범위 밖이라 제외한다.
  2) 같은 파일이 두 섹션에 걸리면 먼저 나온 섹션이 이긴다(결정론).

published_date 를 내지 않는 이유:
  원문은 발행 시점을 "(April, 2022)"처럼 **월까지만** 준다. 카탈로그 published_date 는
  ISO 일자 필드라 값을 채우려면 없는 '일'을 지어내야 한다 — 없는 정밀도를 만들지 않는다
  (프로젝트 규율: 모르는 값은 채우지 않는다). 그래서 PMDA 카탈로그는 발행일 없이
  원문 게재 순서를 그대로 유지한다(registry 에 sort 미지정).

id 규칙:
  "pmda-" + 파일 번호(URL 파일명 stem). 현행 카탈로그 15건이 모두 이 규칙으로
  재현된다(15/15 일치) — 파일 번호는 PMDA 가 문서에 부여한 영속 식별자다.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import date

from grm_common import http_get_html, log


LIBRARY_SOURCE = "pmda"

SITE_BASE = "https://www.pmda.go.jp"
INDEX_URL = f"{SITE_BASE}/english/review-services/gmp-qms-gctp/0007.html"
HTTP_TIMEOUT_SECONDS = 40
HTTP_RETRIES = 2
MIN_EXPECTED_ITEMS = 10          # 이보다 적으면 페이지 구조 변경 의심

# <h2> 표제(소문자·태그제거·공백정규화) → doc_type. 부분일치 — PMDA 가 표제에 연도나
# 부기를 덧붙여도 계속 잡히게 한다. 순서 = 매칭 우선순위.
SECTION_DOC_TYPES: tuple[tuple[str, str], ...] = (
    ("publication of inspectional observations", "inspection-observation"),
    ("gmp / gctp annual report", "annual-report"),
)

_TAG_RE = re.compile(r"<[^>]+>")
_H2_SPLIT_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_PDF_LINK_RE = re.compile(r'<a[^>]+href="(/files/\d+\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
# 링크 라벨 꼬리표 — "제목[118.71KB]" / "제목 (September 2025)" 는 표시 제목이 아니다.
_SIZE_TAIL_RE = re.compile(r"\s*\[\s*[\d.,]+\s*[KMG]?B\s*\]\s*$", re.I)
_MONTH = ("January|February|March|April|May|June|July|August|September|October|"
          "November|December")
_DATE_TAIL_RE = re.compile(rf"\s*\(\s*(?:{_MONTH})\s*,?\s*\d{{4}}\s*\)\s*$", re.I)


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def _doc_type_of(heading: str) -> str:
    lowered = re.sub(r"\s+", " ", _text(heading)).lower()
    for marker, doc_type in SECTION_DOC_TYPES:
        if marker in lowered:
            return doc_type
    return ""


def _clean_title(raw_label: str) -> str:
    """링크 라벨 → 표시 제목. 파일크기·게재월 꼬리표만 떼고 본문은 손대지 않는다."""
    title = _text(raw_label)
    for _ in range(2):                       # "제목[1.08MB]" / "제목 (Sep 2025) [2.48MB]"
        stripped = _DATE_TAIL_RE.sub("", _SIZE_TAIL_RE.sub("", title)).strip()
        if stripped == title:
            break
        title = stripped
    return title


def parse_index(html_text: str) -> list[dict[str, str]]:
    """품질보증 페이지 HTML → 카탈로그 항목 목록(네트워크 없음).

    <h2> 경계로 섹션을 나눠 섹션별 doc_type 을 부여한다. 첫 <h2> 앞의 도입부는
    어느 섹션도 아니므로 버린다(표제 없는 링크에 doc_type 을 추정하지 않는다)."""
    body = html_text or ""
    segments: list[tuple[str, str]] = []
    bounds = list(_H2_SPLIT_RE.finditer(body))
    for index, match in enumerate(bounds):
        end = bounds[index + 1].start() if index + 1 < len(bounds) else len(body)
        segments.append((match.group(1), body[match.end():end]))

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for heading, segment in segments:
        doc_type = _doc_type_of(heading)
        if not doc_type:
            continue
        for href, label in _PDF_LINK_RE.findall(segment):
            item_id = f"pmda-{href.rsplit('/', 1)[-1][:-len('.pdf')]}"
            title = _clean_title(label)
            if not title or item_id in seen:
                continue
            seen.add(item_id)
            items.append({
                "id": item_id,
                "title_en": title,
                "doc_type": doc_type,
                "official_url": SITE_BASE + href,
            })
    return items


def collect_library_items(run_date: date) -> tuple[list[dict[str, str]], str | None]:
    """PMDA 카탈로그 후보 수집 진입점. 반환 (items, error)."""
    log("INFO", f"자료실 수집: {LIBRARY_SOURCE} ({INDEX_URL})")
    try:
        html_text = http_get_html(INDEX_URL, timeout=HTTP_TIMEOUT_SECONDS,
                                  retries=HTTP_RETRIES, label="PMDA")
    except Exception as exc:  # noqa: BLE001 - 경계에서 error 로 승격
        return [], f"PMDA 품질보증 페이지 수집 실패: {exc}"

    items = parse_index(html_text)
    if len(items) < MIN_EXPECTED_ITEMS:
        return [], (f"PMDA 문서 {len(items)}건(<{MIN_EXPECTED_ITEMS}) — "
                    "페이지 구조 변경 의심")

    log("INFO", f"자료실 수집 완료: {LIBRARY_SOURCE} keep {len(items)}건")
    return items, None


if __name__ == "__main__":  # 수동 점검용
    collected, err = collect_library_items(date.today())
    print(f"items={len(collected)} error={err}")
    for entry in collected:
        print(" ", entry["id"], entry["doc_type"], entry["title_en"][:70])
