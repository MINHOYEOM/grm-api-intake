#!/usr/bin/env python3
"""GRM 자료실 수집기 — FDA 가이던스 문서(fda_guidance 카탈로그).

플러그인 계약:
  LIBRARY_SOURCE / collect_library_items(run_date) -> (items, error)
  items = 카탈로그 v2 공개 필드 dict 목록(id·code·title_en·doc_type·published_date·official_url).

수집 경로 (구조화 데이터 우선 — 483 백본 선례):
  FDA 가이던스 검색 페이지는 DataTables 위젯이며, 표 내용은 정적 JSON 한 건
  (files/api/datatables/static/search-for-guidance.json, 약 2,800행)에서 통째로 온다.
  HTML 스크래핑 없이 이 JSON만 1회 GET 한다 — robots.txt 의 `Crawl-Delay: 30`(User-agent: *)
  을 요청 1건으로 자연히 만족하고, `Disallow: /file/`·/search/ 등 금지 경로와도 무관하다
  (/files/ 는 별도 경로). 봇차단 우회는 하지 않는다(TLS 위장 금지 — 프로젝트 규율).

선별(keep) 기준 — 카탈로그는 전량 수집이 아니라 **GMP·품질 선별본**이다:
  1) 문서 URL 이 /search-fda-guidance-documents/ 인 가이던스 문서
  2) 발행 센터가 CDER 또는 CBER (의약품·바이오)
  3) 규제 대상(field_regulated_product_field)이 Drugs/Biologics — 값이 비어 있으면 통과
     (FDA 데이터에 결측이 있다: 예 DEG/EG 고위험 성분 시험 가이던스)
  4) 제목이 GMP·품질 핵심어(_CORE_TERMS)를 포함하거나 ICH Q 계열/M7 코드로 시작
     (Q4B 약전 조화 부속서는 GMP 문서가 아니라 약전 텍스트 동등성 평가라 제외)

id 규칙:
  현행 카탈로그 25건의 id 는 사람이 축약해 만든 슬러그(예 fda-testing-high-risk-components-deg-eg)
  라 원문에서 재현할 수 없다. 따라서 **URL 슬러그 → 기존 id** 앵커표(_ID_ANCHORS)를 고정해
  기존 항목이 신규로 중복되는 것을 막고, 앵커에 없는 신규 항목만 결정론 안정 해시
  (fda-<sha1(slug)[:12]>)를 부여한다. 앵커는 append-only — 기존 값 변경 금지.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from datetime import date

from grm_common import http_get_json, log


LIBRARY_SOURCE = "fda_guidance"

DATASET_URL = "https://www.fda.gov/files/api/datatables/static/search-for-guidance.json"
SITE_BASE = "https://www.fda.gov"
GUIDANCE_PATH = "/regulatory-information/search-fda-guidance-documents/"
HTTP_TIMEOUT_SECONDS = 90
HTTP_RETRIES = 2
MIN_EXPECTED_ROWS = 500        # 이보다 적으면 데이터셋 축소/구조 변경 의심 → error

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
# 뒤에 \b 를 쓰면 "Q9(R1)" 의 닫는 괄호에서 경계가 성립하지 않아 "Q9" 로 잘린다.
_Q_CODE_RE = re.compile(r"^(Q\d{1,2}[A-Z]?(?:\(R\d+\))?)(?![A-Za-z0-9])")
_M7_CODE_RE = re.compile(r"^(M7(?:\(R\d+\))?)(?![A-Za-z0-9])")
_Q4B_RE = re.compile(r"^Q4B\b", re.I)
_TRAILING_NOISE_RE = re.compile(
    r"(?:\s*[:;-]?\s*(?:draft\s+)?guidance\s+for\s+industry\b\.?)+\s*$", re.I)

# GMP·품질 핵심어(제목 소문자 대상 부분일치). 넓은 일반어(specifications/stability 등)는
# 넣지 않는다 — 임상·허가 제출 가이던스가 대량 유입돼 카탈로그가 범람한다.
_CORE_TERMS = (
    "good manufacturing practice", "cgmp", "current good manufacturing",
    "quality system", "quality risk management", "quality metrics",
    "process validation", "sterilization process", "parametric release",
    "aseptic processing", "sterile drug product", "sterility test",
    "microbiological quality", "media fill", "environmental monitoring",
    "data integrity", "nitrosamine", "elemental impurit",
    "out-of-specification", "out of specification",
    "container closure", "cross-contamination", "melamine",
    "pyrogen", "endotoxin", "quality agreement", "contract manufacturing arrangement",
    "process analytical technology", "pat —", "pat -", "pat--",
    "continuous manufacturing", "lifecycle management", "comparability protocol",
    "field alert report", "diethylene glycol", "high-risk drug components",
    "insanitary condition",
)

# 현행 카탈로그(수기 큐레이션) id 앵커: URL 슬러그 → 기존 id. append-only.
_ID_ANCHORS: dict[str, str] = {
    "control-nitrosamine-impurities-human-drugs": "fda-control-nitrosamine-impurities",
    "q9r1-quality-risk-management": "fda-q9-r1-quality-risk-management",
    "testing-glycerin-propylene-glycol-maltitol-solution-hydrogenated-starch-hydrolysate-sorbitol":
        "fda-testing-high-risk-components-deg-eg",
    "q13-continuous-manufacturing-drug-substances-and-drug-products":
        "fda-q13-continuous-manufacturing",
    "comparability-protocols-postapproval-changes-chemistry-manufacturing-and-controls-information-nda":
        "fda-comparability-protocols-postapproval-cmc",
    "q3dr2-guideline-elemental-impurities": "fda-q3d-r2-elemental-impurities",
    "investigating-out-specification-oos-test-results-pharmaceutical-production-level-2-revision":
        "fda-investigating-oos-test-results",
    "field-alert-report-submission-questions-and-answers-guidance-industry":
        "fda-field-alert-report-submission-qa",
    "q12-technical-and-regulatory-considerations-pharmaceutical-product-lifecycle-management-annex":
        "fda-q12-product-lifecycle-management-annex",
    "current-good-manufacturing-practice-guidance-human-drug-compounding-outsourcing-facilities-under":
        "fda-cgmp-compounding-outsourcing-facilities",
    "data-integrity-and-compliance-drug-cgmp-questions-and-answers":
        "fda-data-integrity-drug-cgmp-qa",
    "medical-gases-current-good-manufacturing-practice": "fda-medical-gases-cgmp",
    "current-good-manufacturing-practice-requirements-combination-products":
        "fda-cgmp-combination-products",
    "contract-manufacturing-arrangements-drugs-quality-agreements-guidance-industry":
        "fda-contract-manufacturing-quality-agreements",
    "q7-good-manufacturing-practice-guidance-active-pharmaceutical-ingredients-guidance-industry":
        "fda-q7-gmp-active-pharmaceutical-ingredients",
    "pyrogen-and-endotoxins-testing-questions-and-answers": "fda-pyrogen-endotoxins-testing-qa",
    "process-validation-general-principles-and-practices":
        "fda-process-validation-general-principles",
    "submission-documentation-applications-parametric-release-human-and-veterinary-drug-products":
        "fda-parametric-release-moist-heat",
    "pharmaceutical-components-risk-melamine-contamination":
        "fda-pharmaceutical-components-melamine",
    "container-and-closure-system-integrity-testing-lieu-sterility-testing-component-stability-protocol":
        "fda-container-closure-integrity-sterility-testing",
    "quality-systems-approach-pharmaceutical-current-good-manufacturing-practice-regulations":
        "fda-quality-systems-pharmaceutical-cgmp",
    "pat-framework-innovative-pharmaceutical-development-manufacturing-and-quality-assurance":
        "fda-pat-framework",
    "sterile-drug-products-produced-aseptic-processing-current-good-manufacturing-practice":
        "fda-sterile-drug-products-aseptic-processing",
    "container-closure-systems-packaging-human-drugs-and-biologics":
        "fda-container-closure-systems-packaging",
    "submission-documentation-sterilization-process-validation-applications-human-and-veterinary-drug":
        "fda-sterilization-process-validation-submission",
}


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def _link(row: dict) -> tuple[str, str]:
    """행 title 셀(<a href>제목</a>) → (url, 제목). 실패 시 ("","")."""
    m = _HREF_RE.search(row.get("title") or "")
    if not m:
        return "", ""
    href = html_lib.unescape(m.group(1)).strip()
    if href.startswith("/"):
        href = SITE_BASE + href
    return href, _text(m.group(2))


def _slug(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]


def _iso_date(raw: str) -> str:
    m = _DATE_RE.match(raw or "")
    if not m:
        return ""
    month, day, year = (int(g) for g in m.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def _clean_title(title: str) -> str:
    """FDA 표 제목의 꼬리 상용구(": Guidance for Industry" 반복)를 정리."""
    cleaned = _TRAILING_NOISE_RE.sub("", title).strip(" :;-")
    return cleaned or title


def _code(title: str) -> str:
    m = _Q_CODE_RE.match(title) or _M7_CODE_RE.match(title)
    return m.group(1) if m else ""


def _doc_type(row: dict) -> str:
    status = (row.get("field_final_guidance_1") or "").strip().lower()
    if status.startswith("draft"):
        return "Draft guidance"
    if status.startswith("final"):
        return "Final guidance"
    return "Guidance"


def _is_quality_scope(title: str) -> bool:
    if _Q4B_RE.match(title):
        return False
    lowered = title.lower()
    if any(term in lowered for term in _CORE_TERMS):
        return True
    return bool(_Q_CODE_RE.match(title) or _M7_CODE_RE.match(title))


def _keep(row: dict, url: str, title: str) -> bool:
    if not url.startswith(SITE_BASE + GUIDANCE_PATH) or not title:
        return False
    center = row.get("field_center") or ""
    if "Center for Drug Evaluation" not in center and "Center for Biologics" not in center:
        return False
    product = html_lib.unescape(row.get("field_regulated_product_field") or "").strip()
    if product and "Drugs" not in product and "Biologics" not in product:
        return False
    return _is_quality_scope(title)


def _item_id(slug: str) -> str:
    anchored = _ID_ANCHORS.get(slug)
    if anchored:
        return anchored
    return "fda-" + hashlib.sha1(slug.encode("utf-8")).hexdigest()[:12]


def build_items(rows: list[dict]) -> tuple[list[dict[str, str]], int]:
    """수집 원본 행 → (선별 items, 검사한 행 수). 네트워크 없음 — 픽스처 테스트 진입점."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url, raw_title = _link(row)
        if not _keep(row, url, raw_title):
            continue
        slug = _slug(url)
        if slug in seen:
            continue
        seen.add(slug)
        title = _clean_title(raw_title)
        item = {
            "id": _item_id(slug),
            "title_en": title,
            "doc_type": _doc_type(row),
            "official_url": url,
        }
        code = _code(title)
        if code:
            item["code"] = code
        published = _iso_date(row.get("field_issue_datetime") or "")
        if published:
            item["published_date"] = published
        items.append(item)
    return items, len(rows)


def collect_library_items(run_date: date) -> tuple[list[dict[str, str]], str | None]:
    """FDA 가이던스 카탈로그 후보 수집 진입점. 반환 (items, error)."""
    log("INFO", f"자료실 수집: {LIBRARY_SOURCE} ({DATASET_URL})")
    try:
        payload = http_get_json(
            DATASET_URL, timeout=HTTP_TIMEOUT_SECONDS, retries=HTTP_RETRIES)
    except Exception as exc:  # noqa: BLE001 - 상위에서 error 문자열로 표면화
        return [], f"FDA 가이던스 데이터셋 수집 실패({DATASET_URL}): {exc}"

    if not isinstance(payload, list):
        return [], f"FDA 가이던스 데이터셋 형식 변경(list 아님: {type(payload).__name__})"
    if len(payload) < MIN_EXPECTED_ROWS:
        return [], (f"FDA 가이던스 데이터셋 행 수 이상({len(payload)}건 < "
                    f"{MIN_EXPECTED_ROWS}) — 구조 변경 의심")

    items, scanned = build_items(payload)
    if not items:
        return [], f"FDA 가이던스 선별 0건(수집 {scanned}행) — 선별 조건/구조 변경 의심"
    log("INFO", f"자료실 수집 완료: {LIBRARY_SOURCE} 수집 {scanned}행 / keep {len(items)}건")
    return items, None


if __name__ == "__main__":  # 수동 점검용
    collected, err = collect_library_items(date.today())
    print(f"items={len(collected)} error={err}")
    for entry in collected[:10]:
        print(" ", entry)
