#!/usr/bin/env python3
"""GRM 자료실 수집기 — EMA GMP·품질 가이드라인(ema 카탈로그).

플러그인 계약:
  LIBRARY_SOURCE / collect_library_items(run_date) -> (items, error)
  items = 카탈로그 v2 공개 필드 dict 목록.

수집 경로 (2026-07-20 실측):
  www.ema.europa.eu 는 Drupal 서버렌더 — 목표 두 페이지가 서버 HTML 에 문서 목록을 그대로
  담고 있다(JS 렌더 아님). 공개 JSON API 는 없다(/en/search 는 401).
  1) 실사·정보교환 절차 모음(Compilation of Union procedures): 문서 블록
     (`ema-file-wrapper`)마다 문서유형 속성·제목·"First published" 날짜·PDF 링크가 들어 있다.
  2) 품질 과학 가이드라인 - 제조(Manufacturing) 하위 페이지: 가이드라인 페이지 링크 목록.
     날짜가 목록에 없어 각 가이드라인 페이지의 첫 "First published" 값을 따로 읽는다
     (현행 카탈로그 값과 3/3 일치 확인).
  robots.txt 는 두 경로 모두 허용(Disallow 는 /admin//search/ 등). Crawl-delay 지시는 없으나
  요청 간 1초 간격을 둔다. 봇차단 우회는 하지 않는다.

선별(keep) 기준 — GMP·품질 선별본:
  - 절차 모음: 문서유형이 regulatory-procedural-guideline 인 문서만(Union format 서식·보고서
    템플릿 제외). GDP·도매유통 전용 문서는 제외(GMP 병기 문서는 유지).
  - 과학 가이드라인: 품질-제조 하위 페이지 목록 전체(이미 제조 품질로 좁혀진 목록).

id 규칙:
  현행 26건의 id 는 큐레이션 일련번호(ema-gmp-001…026)라 원문에서 재현할 수 없다.
  **URL 슬러그 → 기존 id** 앵커표(_ID_ANCHORS)로 기존 항목을 고정하고, 신규 항목만
  결정론 안정 해시(ema-<sha1(slug)[:12]>)를 부여한다. 앵커는 append-only.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
import time
from datetime import date

from grm_common import http_get_html, log


LIBRARY_SOURCE = "ema"

SITE_BASE = "https://www.ema.europa.eu"
COMPILATION_URL = (
    SITE_BASE + "/en/human-regulatory-overview/research-development/"
    "compliance-research-development/good-manufacturing-practice/"
    "compilation-union-procedures-inspections-exchange-information"
)
MANUFACTURING_GUIDELINES_URL = (
    SITE_BASE + "/en/human-regulatory-overview/research-development/"
    "scientific-guidelines/quality-guidelines/quality-guidelines-manufacturing"
)
HTTP_TIMEOUT_SECONDS = 40
HTTP_RETRIES = 2
REQUEST_DELAY_SECONDS = 1.0
MIN_EXPECTED_PROCEDURES = 10   # 이보다 적으면 페이지 구조 변경 의심
MIN_EXPECTED_GUIDELINES = 5

TYPE_PROCEDURAL = "regulatory-procedural-guideline"
TYPE_SCIENTIFIC = "scientific-guideline"
TYPE_QA = "questions-and-answers"

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_SPLIT_RE = re.compile(r"ema-file-wrapper")
_DOC_TYPE_RE = re.compile(r'data-ema-document-type="([^"]+)"')
_FILE_TITLE_RE = re.compile(r'<p class="file-title[^"]*"[^>]*>(.*?)</p>', re.S)
_PDF_HREF_RE = re.compile(r'href="(/en/documents/[^"]+?\.pdf)"')
_FIRST_PUBLISHED_RE = re.compile(r"First published")
_DATETIME_RE = re.compile(r'datetime="(\d{4})-(\d{2})-(\d{2})')
_GUIDELINE_LINK_RE = re.compile(
    r'<a[^>]+href="(/en/[^"]*-scientific-guideline)"[^>]*>(.*?)</a>', re.S)
_GDP_RE = re.compile(r"good distribution|(?<![a-z])gdp(?![a-z])|wholesale", re.I)
_GMP_RE = re.compile(r"good manufacturing|(?<![a-z])gmp(?![a-z])|manufactur", re.I)

# 현행 카탈로그(수기 큐레이션) id 앵커: URL 슬러그 → 기존 id. append-only.
_ID_ANCHORS: dict[str, str] = {
    "quality-systems-framework-good-manufacturing-practice-gmp-inspectorates_en.pdf":
        "ema-gmp-001",
    "management-classification-reports-suspected-quality-defects-medicinal-products-risk-based-decision-making_en.pdf":
        "ema-gmp-002",
    "management-rapid-alerts-arising-quality-defects-risk-assessment_en.pdf": "ema-gmp-003",
    "conduct-inspections-pharmaceutical-manufacturers-or-importers_en.pdf": "ema-gmp-004",
    "outline-procedure-co-ordinating-verification-gmp-status-manufacturers-third-countries_en.pdf":
        "ema-gmp-005",
    "guideline-training-qualifications-good-manufacturing-practice-gmp-inspectors_en.pdf":
        "ema-gmp-006",
    "guidance-occasions-when-it-appropriate-competent-authorities-conduct-inspections-premises-manufacturers-importers-distributors-active-substances-manufacturers-or-importers-excipients-used-starting_en.pdf":
        "ema-gmp-007",
    "issue-update-good-manufacturing-practice-gmp-certificates_en.pdf": "ema-gmp-008",
    "model-risk-based-planning-inspections-pharmaceutical-manufacturers_en.pdf": "ema-gmp-009",
    "procedure-dealing-serious-good-manufacturing-practice-gmp-non-compliance-information-originating-third-country-authorities-or-international-organisations_en.pdf":
        "ema-gmp-010",
    "procedure-dealing-serious-good-manufacturing-practice-gmp-non-compliance-requiring-co-ordinated-measures-protect-public-or-animal-health_en.pdf":
        "ema-gmp-011",
    "co-ordinating-good-manufacturing-practice-gmp-inspections-centrally-authorised-products_en.pdf":
        "ema-gmp-012",
    "procedure-compliance-management_en.pdf": "ema-gmp-013",
    "eu-eea-programme-maintenance-equivalence-supervision-good-manufacturing-practice-compliance-pharmaceutical-companies_en.pdf":
        "ema-gmp-014",
    "interpretation-union-format-manufacturer-importer-authorisation_en.pdf": "ema-gmp-015",
    "interpretation-union-format-good-manufacturing-practice-gmp-certificate_en.pdf":
        "ema-gmp-016",
    "manufacture-finished-dosage-form-human-scientific-guideline": "ema-gmp-017",
    "process-validation-finished-products-information-data-be-provided-regulatory-submissions-scientific-guideline":
        "ema-gmp-018",
    "start-shelf-life-finished-dosage-form-annex-note-guidance-manufacture-finished-dosage-form-scientific-guideline":
        "ema-gmp-019",
    "sterilisation-medicinal-product-active-substance-excipient-primary-container-scientific-guideline":
        "ema-gmp-020",
    "use-ionizing-radiation-manufacture-medicinal-products-scientific-guideline": "ema-gmp-021",
    "setting-health-based-exposure-limits-use-risk-identification-manufacture-different-medicinal-products-shared-facilities-scientific-guideline":
        "ema-gmp-022",
    "questions-and-answers-design-space-verification_en.pdf": "ema-gmp-023",
    "questions-and-answers-improving-understanding-normal-operating-range-nor-proven-acceptable-range-par-design-space-dsp-and-normal-variability-process-parameters_en.pdf":
        "ema-gmp-024",
    "questions-and-answers-implementation-risk-based-prevention-cross-contamination-production-and-guideline-setting-health-based-exposure-limits-use-risk-identification-manufacture-different_en.pdf":
        "ema-gmp-025",
    "guidance-good-manufacturing-practice-good-distribution-practice-questions-answers":
        "ema-gmp-026",
}


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def _slug(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]


def _item_id(slug: str) -> str:
    anchored = _ID_ANCHORS.get(slug)
    if anchored:
        return anchored
    return "ema-" + hashlib.sha1(slug.encode("utf-8")).hexdigest()[:12]


def first_published(html_text: str) -> str:
    """문서 블록/페이지의 첫 "First published" 날짜(ISO). 없으면 ""."""
    match = _FIRST_PUBLISHED_RE.search(html_text or "")
    if not match:
        return ""
    window = html_text[match.end():match.end() + 400]
    stamp = _DATETIME_RE.search(window)
    return "-".join(stamp.groups()) if stamp else ""


def _doc_type_for(declared: str, title: str) -> str:
    if title.lower().startswith("questions and answers"):
        return TYPE_QA
    return declared or TYPE_PROCEDURAL


def _is_gmp_scope(blob: str) -> bool:
    """GDP·도매유통 전용 문서 제외(GMP 병기 문서는 유지)."""
    if _GDP_RE.search(blob) and not _GMP_RE.search(blob):
        return False
    return True


def parse_procedures(html_text: str) -> list[dict[str, str]]:
    """절차 모음 페이지 HTML → 항목 목록(네트워크 없음 — 픽스처 테스트 진입점)."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in _BLOCK_SPLIT_RE.split(html_text or "")[1:]:
        declared_match = _DOC_TYPE_RE.search(block)
        declared = declared_match.group(1) if declared_match else ""
        if declared != TYPE_PROCEDURAL:
            continue
        href_match = _PDF_HREF_RE.search(block)
        title_match = _FILE_TITLE_RE.search(block)
        if not href_match or not title_match:
            continue
        url = SITE_BASE + html_lib.unescape(href_match.group(1))
        title = _text(title_match.group(1))
        slug = _slug(url)
        if not title or slug in seen:
            continue
        if not _is_gmp_scope(f"{title} {slug}"):
            continue
        seen.add(slug)
        item = {
            "id": _item_id(slug),
            "title_en": title,
            "doc_type": _doc_type_for(declared, title),
            "official_url": url,
            "pdf_url": url,
        }
        published = first_published(block)
        if published:
            item["published_date"] = published
        items.append(item)
    return items


def parse_guideline_links(html_text: str) -> list[tuple[str, str]]:
    """품질-제조 목록 페이지 HTML → [(official_url, title)] (네트워크 없음)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _GUIDELINE_LINK_RE.finditer(html_text or ""):
        href = html_lib.unescape(match.group(1))
        title = _text(match.group(2))
        if not title or href in seen:
            continue
        seen.add(href)
        out.append((SITE_BASE + href, title))
    return out


def build_guideline_item(url: str, title: str, page_html: str) -> dict[str, str]:
    """가이드라인 링크 + 상세 페이지 HTML → 카탈로그 항목(네트워크 없음)."""
    item = {
        "id": _item_id(_slug(url)),
        "title_en": title,
        "doc_type": _doc_type_for(TYPE_SCIENTIFIC, title),
        "official_url": url,
    }
    published = first_published(page_html)
    if published:
        item["published_date"] = published
    return item


def _get(url: str) -> str:
    time.sleep(REQUEST_DELAY_SECONDS)
    return http_get_html(url, timeout=HTTP_TIMEOUT_SECONDS, retries=HTTP_RETRIES, label="EMA")


def collect_library_items(run_date: date) -> tuple[list[dict[str, str]], str | None]:
    """EMA 카탈로그 후보 수집 진입점. 반환 (items, error)."""
    log("INFO", f"자료실 수집: {LIBRARY_SOURCE} ({COMPILATION_URL})")
    try:
        compilation_html = _get(COMPILATION_URL)
    except Exception as exc:  # noqa: BLE001
        return [], f"EMA 절차 모음 페이지 수집 실패({COMPILATION_URL}): {exc}"
    procedures = parse_procedures(compilation_html)
    if len(procedures) < MIN_EXPECTED_PROCEDURES:
        return [], (f"EMA 절차 문서 {len(procedures)}건(<{MIN_EXPECTED_PROCEDURES}) — "
                    f"페이지 구조 변경 의심({COMPILATION_URL})")

    log("INFO", f"자료실 수집: {LIBRARY_SOURCE} ({MANUFACTURING_GUIDELINES_URL})")
    try:
        listing_html = _get(MANUFACTURING_GUIDELINES_URL)
    except Exception as exc:  # noqa: BLE001
        return procedures, (f"EMA 품질-제조 가이드라인 목록 수집 실패"
                            f"({MANUFACTURING_GUIDELINES_URL}): {exc}")
    links = parse_guideline_links(listing_html)
    if len(links) < MIN_EXPECTED_GUIDELINES:
        return procedures, (f"EMA 과학 가이드라인 링크 {len(links)}건"
                            f"(<{MIN_EXPECTED_GUIDELINES}) — 페이지 구조 변경 의심")

    guidelines: list[dict[str, str]] = []
    errors: list[str] = []
    for url, title in links:
        try:
            page_html = _get(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{_slug(url)}: {exc}")
            page_html = ""
        guidelines.append(build_guideline_item(url, title, page_html))

    items = procedures + guidelines
    scanned = len(_BLOCK_SPLIT_RE.split(compilation_html)) - 1 + len(links)
    log("INFO", f"자료실 수집 완료: {LIBRARY_SOURCE} 수집 {scanned}건 / keep {len(items)}건"
                f" (절차 {len(procedures)} + 과학 가이드라인 {len(guidelines)})")
    if errors:
        return items, "EMA 가이드라인 상세 페이지 일부 실패: " + "; ".join(errors)
    return items, None


if __name__ == "__main__":  # 수동 점검용
    collected, err = collect_library_items(date.today())
    print(f"items={len(collected)} error={err}")
    for entry in collected:
        print(" ", entry["id"], entry.get("published_date", "-"), entry["title_en"][:70])
