#!/usr/bin/env python3
"""GRM 자료실 수집기 — Health Canada GMP 가이드(health_canada 카탈로그).

플러그인 계약:
  LIBRARY_SOURCE / collect_library_items(run_date) -> (items, error)
  items = 카탈로그 v2 공개 필드 dict 목록.

수집 경로 (2026-07-20 실측):
  canada.ca 는 AEM 서버렌더 — 목록 페이지 HTML 에 GUI 문서 링크가 그대로 있다.
  ⚠️ curl 계열 UA 는 차단된 이력이 있다(2026-07-18). requests(기본 UA) 로는 200 정상.
  차단을 만나더라도 **우회하지 않는다**(TLS 위장 금지 — 프로젝트 규율). robots.txt 는
  대상 경로를 모두 허용한다(Disallow 는 /en/sr/*·*/search.html·메뉴 조각 등).
  각 문서 페이지의 표준 메타(dcterms.title/issued/type)를 제목·발행일·유형의 출처로 쓴다.

선별(keep) 기준 — GMP·품질 선별본:
  1) 고정된 4개 공식 목록 페이지(GMP 가이던스·시설허가 지침·GMP 랜딩·회수 랜딩)에서만 링크 수집
  2) GUI 코드가 있는 문서만(라벨의 "(GUI-0001)" 또는 URL 끝의 "-0001.html")
  3) 의료기기·약물감시(GVP)·생식세포 등 GMP 밖 문서는 제외(_EXCLUDE_TERMS)
  4) 문서 페이지 메타로 2차 검증:
     - dcterms.title 에 다른 접두 코드(FRM-0211·POL-0016 등)가 있으면 제외.
       URL 끝 4자리 숫자는 GUI 전용이 아니다 — 실측에서 FRM/POL 문서가 GUI 로 오인됐다.
     - dcterms.type 이 guidance 로 시작하는 문서만(서식·정책·투명성 문서 제외).
  코드가 여러 URL 에 걸리면 GMP 경로 > 코드가 URL 에 박힌 것 > 먼저 나온 것 순으로 결정론 선택.

id 규칙:
  현행 카탈로그 20건이 모두 "health-canada-" + 소문자 코드(health-canada-gui-0001) 이며
  원문 코드에서 그대로 재현된다 → 앵커표 없이 코드 규칙을 그대로 쓴다(20/20 일치 확인).
  코드가 문서의 안정 식별자라 URL 이 바뀌어도 같은 항목으로 수렴한다.
"""

from __future__ import annotations

import html as html_lib
import re
import time
from datetime import date

from grm_common import http_get_html, log


LIBRARY_SOURCE = "health_canada"

SITE_BASE = "https://www.canada.ca"
HC_DRUGS_PATH = "/health-canada/services/drugs-health-products/"
GMP_PATH_MARKER = "/good-manufacturing-practices"
INDEX_PATHS = (
    "/en/health-canada/services/drugs-health-products/compliance-enforcement/"
    "good-manufacturing-practices/guidance-documents.html",
    "/en/health-canada/services/drugs-health-products/compliance-enforcement/"
    "establishment-licences/directives-guidance-documents-policies.html",
    "/en/health-canada/services/drugs-health-products/compliance-enforcement/"
    "good-manufacturing-practices.html",
    "/en/health-canada/services/drugs-health-products/compliance-enforcement/recalls.html",
)
HTTP_TIMEOUT_SECONDS = 40
HTTP_RETRIES = 2
REQUEST_DELAY_SECONDS = 1.0
MIN_EXPECTED_DOCS = 10          # 이보다 적으면 목록 구조 변경 의심
DEFAULT_DOC_TYPE = "guidance"

_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r'<a[^>]+href="([^"\'<>]+?\.html)"[^>]*>(.*?)</a>', re.S)
_LABEL_CODE_RE = re.compile(r"\bGUI[\s‐-―-]?(\d{4})\b", re.I)
_URL_CODE_RE = re.compile(r"-(0\d{3})\.html$")
_ANY_CODE_RE = re.compile(r"\b([A-Z]{3})[\s‐-―-]?(\d{4})\b")
_KEPT_DOC_TYPE_PREFIX = "guidance"
_META_RE = re.compile(r'<meta[^>]+name="(dcterms\.[a-z]+)"[^>]+content="([^"]*)"', re.I)
_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_TITLE_TAIL_RE = re.compile(r"\s*[-–—]\s*summary\s*$", re.I)
_TITLE_CODE_TAIL_RE = re.compile(r"\s*\(\s*GUI[\s‐-―-]?\d{4}\s*\)\s*$", re.I)

# GMP·품질 범위 밖(의료기기·약물감시·생식세포 등) — 라벨/URL 부분일치로 제외
_EXCLUDE_TERMS = (
    "medical device", "dispositif",
    "pharmacovigilance", "(gvp)", "gvp guidelines", "gvp)",
    "donor sperm", "ova ",
)


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def _absolute(href: str) -> str:
    href = html_lib.unescape(href).split("?")[0].split("#")[0].strip()
    if href.startswith("/"):
        return SITE_BASE + href
    return href


def _code_of(label: str, url: str) -> str:
    match = _LABEL_CODE_RE.search(label) or _URL_CODE_RE.search(url)
    return f"GUI-{match.group(1)}" if match else ""


def _excluded(blob: str) -> bool:
    lowered = blob.lower()
    return any(term in lowered for term in _EXCLUDE_TERMS)


def _better_url(current: str, candidate: str, code: str) -> bool:
    """같은 코드에 URL 이 여러 개일 때 후보가 더 나은지 — 결정론 우선순위."""
    digits = code.split("-")[-1]
    current_rank = (GMP_PATH_MARKER in current, digits in current)
    candidate_rank = (GMP_PATH_MARKER in candidate, digits in candidate)
    return candidate_rank > current_rank


def parse_index(html_text: str) -> list[tuple[str, str, str, bool]]:
    """목록 페이지 HTML → [(code, url, label, label_has_code)] (네트워크 없음).

    label_has_code=False 는 코드를 URL 끝 숫자에서 추정했다는 뜻 — 문서 페이지 메타로
    반드시 2차 검증한다(keep_document)."""
    out: list[tuple[str, str, str, bool]] = []
    for match in _LINK_RE.finditer(html_text or ""):
        url = _absolute(match.group(1))
        label = _text(match.group(2))
        if not label or HC_DRUGS_PATH not in url:
            continue
        code = _code_of(label, url)
        if not code or _excluded(f"{label} {url}"):
            continue
        out.append((code, url, label, bool(_LABEL_CODE_RE.search(label))))
    return out


def select_documents(
    entries: list[tuple[str, str, str, bool]],
) -> dict[str, tuple[str, str, bool]]:
    """[(code, url, label, label_has_code)] → {code: (url, label, label_has_code)}.

    코드별 결정론 단일 선택 — GMP 경로 > 코드가 URL 에 박힌 것 > 먼저 나온 것."""
    chosen: dict[str, tuple[str, str, bool]] = {}
    for code, url, label, explicit in entries:
        if code not in chosen or _better_url(chosen[code][0], url, code):
            chosen[code] = (url, label, explicit)
    return chosen


def keep_document(code: str, page_html: str, label_has_code: bool) -> bool:
    """문서 페이지 메타 2차 검증 — 코드 접두 오인·비가이던스 문서 제외(네트워크 없음)."""
    meta = _metadata(page_html)
    title = meta.get("dcterms.title") or ""
    codes = _ANY_CODE_RE.findall(title.upper())
    if codes:
        digits = code.split("-")[-1]
        if ("GUI", digits) not in codes:
            return False
    elif not label_has_code:
        return False           # 라벨·제목 어디에도 GUI 코드 근거가 없다 → 추정 폐기
    doc_type = (meta.get("dcterms.type") or "").strip().lower()
    if doc_type:
        return doc_type.startswith(_KEPT_DOC_TYPE_PREFIX)
    return label_has_code      # 페이지를 못 읽었을 때만 목록 라벨 근거로 유지


def _clean_title(title: str) -> str:
    cleaned = _TITLE_TAIL_RE.sub("", title).strip()
    cleaned = _TITLE_CODE_TAIL_RE.sub("", cleaned).strip()
    return cleaned


def _metadata(page_html: str) -> dict[str, str]:
    return {key.lower(): html_lib.unescape(value).strip()
            for key, value in _META_RE.findall(page_html or "")}


def build_item(code: str, url: str, label: str, page_html: str) -> dict[str, str]:
    """문서 코드·목록 라벨·문서 페이지 HTML → 카탈로그 항목(네트워크 없음)."""
    meta = _metadata(page_html)
    title = _clean_title(meta.get("dcterms.title") or label)
    # dcterms.type 은 "guidance;recommendations" 처럼 다중값이라 첫 값만 쓴다
    # (카탈로그 표시층이 'guidance' → '가이던스'로 매핑한다).
    doc_type = (meta.get("dcterms.type") or "").split(";")[0].strip() or DEFAULT_DOC_TYPE
    item = {
        "id": f"health-canada-{code.lower()}",
        "code": code,
        "title_en": title,
        "doc_type": doc_type,
        "official_url": url,
    }
    issued = _ISO_DATE_RE.match(meta.get("dcterms.issued") or "")
    if issued:
        item["published_date"] = issued.group(1)
    return item


def _get(url: str) -> str:
    time.sleep(REQUEST_DELAY_SECONDS)
    return http_get_html(url, timeout=HTTP_TIMEOUT_SECONDS, retries=HTTP_RETRIES,
                         label="HealthCanada")


def collect_library_items(run_date: date) -> tuple[list[dict[str, str]], str | None]:
    """Health Canada 카탈로그 후보 수집 진입점. 반환 (items, error)."""
    entries: list[tuple[str, str, str]] = []
    index_errors: list[str] = []
    for path in INDEX_PATHS:
        url = SITE_BASE + path
        log("INFO", f"자료실 수집: {LIBRARY_SOURCE} ({url})")
        try:
            entries.extend(parse_index(_get(url)))
        except Exception as exc:  # noqa: BLE001
            index_errors.append(f"{path.rsplit('/', 1)[-1]}: {exc}")

    documents = select_documents(entries)
    if len(documents) < MIN_EXPECTED_DOCS:
        detail = "; ".join(index_errors) or "목록 구조 변경 의심"
        return [], (f"Health Canada GUI 문서 {len(documents)}건"
                    f"(<{MIN_EXPECTED_DOCS}) — {detail}")

    items: list[dict[str, str]] = []
    page_errors: list[str] = []
    for code in sorted(documents):
        url, label, label_has_code = documents[code]
        try:
            page_html = _get(url)
        except Exception as exc:  # noqa: BLE001
            page_errors.append(f"{code}: {exc}")
            page_html = ""
        if not keep_document(code, page_html, label_has_code):
            continue
        items.append(build_item(code, url, label, page_html))

    log("INFO", f"자료실 수집 완료: {LIBRARY_SOURCE} 수집 {len(entries)}링크 / "
                f"후보 {len(documents)}코드 / keep {len(items)}건")
    errors = index_errors + page_errors
    if errors:
        return items, "Health Canada 일부 페이지 실패: " + "; ".join(errors)
    return items, None


if __name__ == "__main__":  # 수동 점검용
    collected, err = collect_library_items(date.today())
    print(f"items={len(collected)} error={err}")
    for entry in collected:
        print(" ", entry["id"], entry.get("published_date", "-"), entry["title_en"][:70])
