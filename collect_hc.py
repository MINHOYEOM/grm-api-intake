#!/usr/bin/env python3
"""GRM Health Canada Collector — P1 (글로벌 확장).

ENABLE_HC=true 또는 --sources hc 일 때 collect_intake.main() 에서 호출된다.

채널 (probe 2026-06-02):
  Health Canada "Recalls and Safety Alerts" 오픈데이터 JSON (매일 갱신):
    https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json
  전(全) 정부 통합 피드(식품·교통·소비재·의료기기·의약품)이므로 **의약품/건강제품만 필터**한다.

  필드: NID, Title, URL(항목별 절대 링크), Organization, Product, Issue,
        "What you should do", Category, "Recall class", "Last updated"(YYYY-MM-DD), Archived
  - Organization == "Drugs and health products"  → 의약품/건강기능식품 (TC/CFIA/Medical devices 제외)
  - Recall class: 의약품은 "Type I/II/III" (Type I = 최고위험) / 기기는 "Class 1/2/3"

설계:
  - "Last updated" 기준 윈도우 필터(지연공개 대비 enforcement 윈도우 사용 — main이 전달).
  - document_id = "hc-"+NID (실행 간 안정 → dedup).
  - official_url = 항목별 공식 URL(절대). 방어적으로 urljoin.
  - Recall class → Signal Tier. 품질 키워드 가산.
  - 피드 자체를 못 읽거나 구조가 깨지면 error(0건 침묵 금지). 단, "이번 주 의약품 recall 0건"은 정상.
"""

from __future__ import annotations

import html as html_lib
import re
import time
from datetime import date
from typing import Any, Callable
from urllib.parse import urljoin

import requests

import intake_exclusion as _xl
from grm_common import DEFAULT_USER_AGENT, http_get_json, log
from collect_intake import (
    IntakeItem,
    SOURCE_HC,
    SRC_TYPE_OFFICIAL_API,
    _within_window,
)


HC_OPENDATA_URL = ("https://recalls-rappels.canada.ca/sites/default/files/"
                   "opendata-donneesouvertes/HCRSAMOpenData.json")
HC_BASE = "https://recalls-rappels.canada.ca"

# 항목별 상세 페이지(detail) 보강 — opendata 피드에는 브랜드명만 있고 유효성분(생물 원료)·
# 제조사가 없어, Hizentra 류 생물주사제가 Category=Drugs 만으로 Chemical 오분류된다.
# 상세 페이지의 구조화 셀(data-label)에서 유효성분(Strength)·제형을 best-effort 로 끌어와
# compute_modality 가 보는 텍스트에 주입한다. 모든 실패는 조용히 무시(피드 단독 폴백).
HC_DETAIL_FETCH = True                  # ops kill-switch (코드 상수, 네트워크 보강 비활성용)
HC_DETAIL_TIMEOUT_SECONDS = 30
HC_DETAIL_REQUEST_DELAY_SECONDS = 0.5
HC_DETAIL_RETRIES = 1
# 상세 페이지 셀 라벨 → 내부 키. HC 가 라벨을 바꿔도 무관 항목은 무시된다.
_DETAIL_CELL_RE = re.compile(
    r'data-label="([^"]+)"[^>]*>(.*?)</td>', re.S | re.I)
_DETAIL_TAG_RE = re.compile(r"<[^>]+>")
# 실제 회사/제조사를 뜻하는 라벨만 firm 으로 인정한다(브랜드·제품명은 회사가 아님).
_COMPANY_LABELS = ("company", "recalling firm", "manufacturer", "distributor",
                   "marketed by", "importer")

# [2026-11-03] 업체란은 상세 페이지의 별도 "Companies"(회수 업체) 칸이 1순위다. 상세표의
# Brand(s) 는 소비자 브랜드(Equate·Aleve·Claritin…)가 든 페이지가 있어 회사가 아니다 —
# 07-20 에 "원문 미기재" 거짓을 막으려고 Brand(s) 를 최후 폴백으로 썼더니 6장이 브랜드를
# 업체로 발행했다(캐나다 43장 대조). 브랜드는 업체 폴백이 아니라 **별도 값**(`brand`)이다.
_BRAND_LABELS = ("brand", "brand(s)", "brands")
_COMPANIES_KEY = "_companies"            # `_parse_detail_html` 가 data-label 키와 겹치지 않게 접두 `_`
_COMPANIES_BLOCK_RE = re.compile(
    r'field--name-companies"(.*?)(?:field--name-node-id|Published by)', re.S | re.I)
_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_COMPANY_PREFIX_RE = re.compile(
    r"^(?:recalling\s+firms?|companies|company)\s*:?\s*", re.I)
# 정정이 나도 바뀌지 않는 최초 게시일 — 피드의 "Last updated" 는 정정일로 바뀐다.
_ORIGINAL_PUBLISHED_RE = re.compile(
    r"Original published date:?\s*</strong>\s*<time[^>]*>\s*(\d{4}-\d{2}-\d{2})", re.S | re.I)
_FIRST_PUBLISHED_KEY = "_first_published"

TARGET_ORG = "drugs and health products"     # Organization 필터 (소문자 비교)
# [2026-09-14] Organization 필터를 단일 값에서 집합으로. HC 는 의약품 회수·경보를 **세
# Organization** 으로 나눠 낸다:
#   · "Drugs and health products"          — 정식 회수(등급 Type I/II/III 있음)
#   · "Communications and Public Affairs Branch" — 공개 경보(public advisory). Teva-Pregabalin
#     교차오염 Type I(08-15)·Teva 아세트아미노펜 이물 혼입(09-10, Novo-Gesic Forte)이 여기로 나왔다
#   · "Marketed health products"           — 수입 허용·데이터 무결성 검토 공지(TAVNEOS 07-07)
# 종전 필터는 첫째만 받아 2026-06~09 에 의약품 7건을 **조용히** 버렸다(오류 0·경보 0). 뒤의 두
# 기관은 식품·소비재·의료기기도 같은 이름으로 내므로 Category 로 의약품/건강제품만 남긴다.
TARGET_ORGS = frozenset({
    TARGET_ORG,
    "communications and public affairs branch",
    "marketed health products",
})
# Category 는 " - " 로 복수 병기된다("Drugs - Medical devices"). 토큰 중 하나라도 이 집합에
# 있어야 의약품/건강제품으로 본다(비-주기관에만 적용 — 주기관은 종전 배제 규칙 그대로).
_HEALTH_PRODUCT_CATEGORY_TOKENS = frozenset({
    "drugs", "natural health products", "biologic or vaccine", "radiopharmaceuticals",
})
# 같은 Organization 안에도 수의약품/의료기기가 섞여 있어 Category로 추가 배제 (CODEX 검증).
_EXCLUDED_CATEGORIES = {
    "medical devices", "veterinary drugs", "drugs - veterinary drugs",
}
_EXCLUDED_CATEGORY_TOKENS = frozenset({"veterinary drugs"})


def _category_tokens(category: str) -> set[str]:
    return {t.strip().lower() for t in (category or "").split(" - ") if t.strip()}


def _record_in_scope(rec: dict[str, Any]) -> bool:
    """이 레코드가 의약품/건강제품 채널인가 — Organization + (비-주기관이면) Category.

    주기관("Drugs and health products")은 종전과 동일하게 통과시킨다(Category 배제는
    `_to_item` 의 `_EXCLUDED_CATEGORIES` 가 그대로 맡는다 — 회귀 0). 나머지 두 기관은
    식품·소비재·기기를 같은 이름으로 내므로 Category 토큰에 의약품/건강제품이 있어야 하고,
    수의약품이 섞이면 배제한다.
    """
    org = _text(rec, "Organization").lower()
    if org == TARGET_ORG:
        return True
    if org not in TARGET_ORGS:
        return False
    tokens = _category_tokens(_text(rec, "Category"))
    if tokens & _EXCLUDED_CATEGORY_TOKENS:
        return False
    return bool(tokens & _HEALTH_PRODUCT_CATEGORY_TOKENS)
TYPE_HC_RECALL = "hc-recall"
LANGUAGE_EN = "EN"
REGION_HC = "Canada (Health Canada)"

# 제조/품질 고신호 키워드 (Tier 가산)
_HC_QUALITY_TERMS = [
    "gmp", "manufacturing", "contamination", "impurit", "nitrosamin",
    "sterile", "out of specification", "dissolution", "stability",
    "data integrity", "foreign", "subpotent", "potency", "assay",
    "mislabel", "cross-contamination", "endotoxin", "particulate",
    "good manufacturing",
]
_OSD_TERMS = ["tablet", "capsule", "caplet"]


def _text(rec: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = rec.get(k)
        if v:
            return str(v).strip()
    return ""


def _parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            return ""
    return ""


def _signal_tier(recall_class: str, blob: str) -> str:
    rc = recall_class.lower()
    if "type i" in rc and "type ii" not in rc and "type iii" not in rc:
        return "Tier 3"          # Type I = 최고위험
    if "type ii" in rc and "type iii" not in rc:
        return "Tier 2"
    if "type iii" in rc:
        return "Tier 2"
    # 등급 미표기 → 품질 키워드로 판정
    if any(t in blob for t in _HC_QUALITY_TERMS):
        return "Tier 2"
    return "Tier 1"


def _osd_relevance(product: str) -> str:
    p = product.lower()
    if any(t in p for t in _OSD_TERMS):
        return "Direct"
    if "oral" in p:
        return "Indirect"
    return "N/A"


def _parse_detail_html(html: str) -> dict[str, str]:
    """상세 페이지의 구조화 셀(data-label)을 {라벨소문자: 값} 으로 추출(순수 함수).

    HC recall 상세 페이지 표는 Brand / Product Name / Strength(유효성분·함량) /
    Dosage Form / Lot / Market Authorization / (가끔) Company 셀을 노출한다.
    """
    out: dict[str, str] = {}
    for label, raw_val in _DETAIL_CELL_RE.findall(html or ""):
        val = _DETAIL_TAG_RE.sub(" ", raw_val)
        val = re.sub(r"\s+", " ", val).replace("&nbsp;", " ").strip()
        key = label.strip().lower()
        if val and key not in out:        # 첫 값 우선(동일 라벨 다중 행 방지)
            out[key] = val
    companies = _parse_companies(html)
    if companies:
        out[_COMPANIES_KEY] = companies
    m = _ORIGINAL_PUBLISHED_RE.search(html or "")
    if m and _parse_date(m.group(1)):
        out[_FIRST_PUBLISHED_KEY] = m.group(1)
    return out


def _parse_companies(html: str) -> str:
    """상세 페이지 "Companies"(회수 업체) 칸의 **첫 회사명**(순수 함수). 없으면 "".

    칸 안은 `<p>` 줄 묶음이다(첫 줄=상호, 이어서 주소). 엑셀에서 붙여 넣은 표 잔해가 섞여 있어
    태그를 걷어 낸 뒤 줄 단위로 읽는다. "Recalling Firm:" 같은 머리말은 떼고, 머리말만 있는
    줄은 건너뛴다."""
    m = _COMPANIES_BLOCK_RE.search(html or "")
    if not m:
        return ""
    block = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", m.group(1), flags=re.S | re.I)
    block = block.split('field--label">Companies</div>', 1)[-1]
    block = re.sub(r"<[^>]*$", "", block)          # 끝 표지(node-id) 앞에서 잘린 열린 태그
    paras = _PARA_RE.findall(block) or _BR_RE.split(block)
    for para in paras:
        line = _DETAIL_TAG_RE.sub(" ", para)
        line = html_lib.unescape(line).replace("\xa0", " ")
        line = re.sub(r"\s+", " ", line).strip()
        line = _COMPANY_PREFIX_RE.sub("", line).strip(" ,;")
        # 상호와 주소가 한 줄로 붙어 오는 페이지가 많다("Apotex Inc. 150 Signet Drive …") —
        # 첫 "공백+숫자" 앞까지가 상호다(상호 자체가 숫자로 시작하는 경우는 선행 숫자라 걸리지 않는다).
        line = re.split(r"(?<=\S)\s+\d", line, maxsplit=1)[0].strip(" ,;")
        if line and re.search(r"[A-Za-z]", line):
            return line
    return ""


def _detail_company(detail: dict[str, str]) -> str:
    """상세에서 회수 업체를 뽑는다. 1순위 "Companies" 칸, 다음 '실제 회사/제조사' 라벨
    (_COMPANY_LABELS). **Brand(s) 는 쓰지 않는다**(브랜드는 회사가 아니다 — 위 상수 주석).
    없으면 ""(→ 카드에서 값 부재로 표기)."""
    if detail.get(_COMPANIES_KEY):
        return detail[_COMPANIES_KEY]
    for key in _COMPANY_LABELS:
        if detail.get(key):
            return detail[key]
    return ""


def _detail_brand(detail: dict[str, str]) -> str:
    """상세표의 Brand(s) — 업체가 아니라 별도 값."""
    for key in _BRAND_LABELS:
        if detail.get(key):
            return detail[key]
    return ""


def _fetch_recall_detail(url: str, stats: dict[str, int] | None = None) -> dict[str, str]:
    """항목별 상세 페이지를 best-effort 로 가져와 구조화 셀을 파싱. 실패는 {} (조용히).

    stats(선택): {"attempted","failed"} 카운터를 in-place 증가시켜 collect_hc 가 run 말미에
    집계 요약(대량 실패 침묵 방지)을 낼 수 있게 한다. 미전달 시 카운트 없음(직접 호출·테스트
    후방호환). 보강 비활성/URL 부재는 '시도 아님'이라 집계에서 제외한다.
    """
    if not HC_DETAIL_FETCH or not url or url == HC_BASE:
        return {}
    if stats is not None:
        stats["attempted"] = stats.get("attempted", 0) + 1
    last_err: Exception | None = None
    for attempt in range(HC_DETAIL_RETRIES + 1):
        try:
            time.sleep(HC_DETAIL_REQUEST_DELAY_SECONDS)
            resp = requests.get(
                url,
                timeout=HC_DETAIL_TIMEOUT_SECONDS,
                headers={"User-Agent": DEFAULT_USER_AGENT,
                         "Accept": "text/html,application/xhtml+xml"},
            )
            resp.raise_for_status()
            return _parse_detail_html(resp.content.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001 — 보강은 best-effort, 실패해도 피드 단독 폴백
            last_err = e
    if stats is not None:
        stats["failed"] = stats.get("failed", 0) + 1
    log("INFO", f"HC 상세 보강 건너뜀(피드 단독 폴백) url={url} err={last_err}")
    return {}


# ── 제외 장부(intake_exclusion) 연결 ─────────────────────────────────────────────
# 창 안에서 범위 규칙으로 버린 레코드만 남긴다(피드는 전체 회수 DB 라 창 밖까지 쓰면 매일 수천 행).
# 원천 대조 감시(source_reconcile.py)는 창 안 레코드 = 수집 + 이 장부 + 미뤄짐 으로 센다.
_RULE_VERSION_CACHE: list[str] = []


def exclusion_rule_version() -> str:
    """HC 범위 규칙(기관·Category·정정 판정)의 AST 지문."""
    if not _RULE_VERSION_CACHE:
        _RULE_VERSION_CACHE.append(_xl.rule_fingerprint(
            ("_record_in_scope", "_EXCLUDED_CATEGORIES", "_FIRST_PUBLISHED_KEY"), ("collect_hc",)))
    return _RULE_VERSION_CACHE[0]


def _feed_date(rec: dict[str, Any]) -> str:
    return _parse_date(_text(rec, "Last updated", "Date published"))


def _record_exclusion(rec: dict[str, Any], reason: str, *, rule_version: str = "",
                      basis: str = _xl.BASIS_METADATA) -> None:
    nid = _text(rec, "NID")
    if not nid:
        return      # 키가 없으면 대조 감시도 세지 않는다(같은 기준)
    url = urljoin(HC_BASE, _text(rec, "URL")) if _text(rec, "URL") else HC_BASE
    _xl.record(source=SOURCE_HC, document_id=f"hc-{nid}", official_url=url,
               title=_text(rec, "Title"), published_date=_feed_date(rec), reason_code=reason,
               rule_version=rule_version or exclusion_rule_version(), basis=basis)


def _to_item(
    rec: dict[str, Any],
    start: date,
    end: date,
    detail_fetcher: Callable[[str], dict[str, str]] | None = None,
) -> IntakeItem | None:
    nid = _text(rec, "NID")
    title = _text(rec, "Title")
    # 윈도우 판정은 피드의 "Last updated"(정정 포함 최신일)로 한다 — 정정도 일단 눈에 띄게.
    # 카드에 찍는 발행일은 상세 페이지의 "Original published date" 가 있으면 그것이다(아래).
    feed_date = _feed_date(rec)
    if not nid or not title:
        if _within_window(feed_date, start, end):
            _record_exclusion(rec, "missing_required_field", rule_version="static-1")
        return None
    if not _within_window(feed_date, start, end):
        return None

    category = _text(rec, "Category")
    if category.lower() in _EXCLUDED_CATEGORIES:   # 수의약품/의료기기 배제
        _record_exclusion(rec, "excluded_category")
        return None
    product = _text(rec, "Product")
    issue = _text(rec, "Issue")
    recall_class = _text(rec, "Recall class")
    url = urljoin(HC_BASE, _text(rec, "URL")) if _text(rec, "URL") else HC_BASE

    # 상세 페이지 보강(필터 통과 항목에만 — 네트워크 절약). 실패 시 {} (피드 단독).
    detail = detail_fetcher(url) if detail_fetcher else {}
    ingredient = detail.get("strength", "")        # 유효성분·함량(생물 원료 단서)
    dosage_form = detail.get("dosage form", "")   # 파서 키 = label.lower() — 공백 유지
    firm = _detail_company(detail)                 # "Companies" 칸 우선 → 회사 라벨; Brand(s)·Organization 은 회사 아님
    brand = _detail_brand(detail)
    first_published = detail.get(_FIRST_PUBLISHED_KEY, "")
    if first_published and feed_date and first_published < start.isoformat():
        # 같은 NID 의 **정정**이다 — 최초 게시는 이전 윈도우에서 이미 카드가 됐다. 새 카드를 또
        # 만들면 같은 회수가 다음 호에 두 번 나간다(PMS-Fampridine 10-05호·11-02호). 조용히 버리지
        # 않고 로그로 남긴다(기존 카드 쪽 갱신은 별도 작업).
        log("WARN", f"HC 정정 건너뜀(최초 게시 {first_published} < 윈도우 시작 {start}, 정정 {feed_date}) "
                    f"nid={nid} — 기존 카드가 있다")
        _record_exclusion(rec, "correction_of_earlier_notice", basis=_xl.BASIS_TEXT)
        return None
    date_iso = first_published or feed_date

    blob = f"{title} {product} {issue} {ingredient}".lower()
    tier = _signal_tier(recall_class, blob)
    relevance = "Likely" if category.lower() == "drugs" else "Possible"
    if any(t in blob for t in _HC_QUALITY_TERMS):
        relevance = "Likely"

    body_parts = [
        f"분류: {category}" if category else "",
        f"등급(Recall class): {recall_class}" if recall_class else "",
        f"제품: {product}" if product else "",
        # 유효성분/제형은 상세 페이지에서만 옴 — compute_modality 가 생물 원료를
        # 보도록 body(text_part)에 주입(브랜드명만으로는 생물주사제 식별 불가).
        f"유효성분/함량: {ingredient}" if ingredient else "",
        f"제형: {dosage_form}" if dosage_form else "",
        f"사유(Issue): {issue}" if issue else "",
        f"조치(What you should do): {_text(rec, 'What you should do')}"
        if _text(rec, "What you should do") else "",
    ]
    raw_payload: dict[str, Any] = {
        "api": "HC RSAM OpenData", "nid": nid, **rec,
        # product_type/product_description 를 정규화해 compute_modality(제품군 분류)가
        # HC drug recall 을 인식하도록 한다(Category=Drugs → Chemical, 생물 원료 → Biologic).
        "product_type": category, "product_description": product,
    }
    if ingredient:
        raw_payload["medicinal_ingredient"] = ingredient
    if dosage_form:
        raw_payload["dosage_form_detail"] = dosage_form
    if firm:
        raw_payload["company"] = firm
    if brand:
        raw_payload["brand"] = brand           # 업체 폴백이 아니라 별도 행(card_scaffold 가 표기)
    if first_published:
        raw_payload["first_published"] = first_published
        if feed_date and feed_date > first_published:
            raw_payload["correction_date"] = feed_date    # 정정일 — 발행일과 다를 때만

    return IntakeItem(
        source=SOURCE_HC,
        document_id=f"hc-{nid}",
        date_iso=date_iso,
        headline=f"[HC] {title}"[:240],
        official_url=url,                       # 항목별 공식 URL(절대)
        type_or_class=TYPE_HC_RECALL,
        firm=firm,                              # 회수 업체("Companies" 칸 우선); 없으면 "" → 카드에서 값 부재로 표기
        body="\n".join(p for p in body_parts if p),
        api_query=HC_OPENDATA_URL,
        qa_relevance=relevance,
        osd_relevance=_osd_relevance(product),
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier=tier,
        raw_payload=raw_payload,
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_HC,
    )


def collect_hc(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """Health Canada 의약품/건강제품 recall·advisory 수집. (items, error_msg).

    - 피드 fetch 실패/비배열/0레코드(구조 깨짐) → error.
    - 의약품 레코드는 있으나 윈도우 내 0건 → 정상(빈 리스트, error 없음).
    """
    log("INFO", f"HC 수집: {HC_OPENDATA_URL}")
    try:
        data = http_get_json(HC_OPENDATA_URL, timeout=60, retries=2)
    except Exception as e:  # noqa: BLE001
        return [], f"HC 오픈데이터 수집 실패: {e}"

    if not isinstance(data, list) or not data:
        return [], "HC 오픈데이터 형식 이상(배열 아님/0레코드) — 구조 변경 의심(수동 확인 필요)"

    total = len(data)
    org_total = 0
    items: list[IntakeItem] = []
    seen: set[str] = set()
    # 상세 보강 시도/실패를 run 단위로 집계(건별 INFO 는 흩어져 대량 실패가 묻힘).
    detail_stats: dict[str, int] = {"attempted": 0, "failed": 0}

    def _fetcher(u: str) -> dict[str, str]:
        return _fetch_recall_detail(u, detail_stats)

    for rec in data:
        if not isinstance(rec, dict):
            continue
        if not _record_in_scope(rec):
            if _within_window(_feed_date(rec), start, end):
                _record_exclusion(rec, "out_of_scope_org")
            continue
        org_total += 1
        item = _to_item(rec, start, end, detail_fetcher=_fetcher)
        if item is None or item.document_id in seen:
            continue
        seen.add(item.document_id)
        items.append(item)

    if org_total == 0:
        # 전체 피드에 의약품/건강제품 채널(TARGET_ORGS) 레코드가 하나도 없음 = 필드/값 변경 의심
        return [], (f"HC 피드에서 의약품/건강제품 Organization({', '.join(sorted(TARGET_ORGS))}) "
                    f"레코드 0건(total={total}) — 필드/값 변경 의심(수동 확인 필요)")

    # 상세 보강 집계 요약 — 실패는 error 승격 아님(피드 단독 폴백 = 정상 수집). N>0 이면 WARN.
    attempted, failed = detail_stats["attempted"], detail_stats["failed"]
    if attempted:
        log("WARN" if failed else "INFO",
            f"HC 상세 보강 집계: 실패 {failed}/시도 {attempted}건"
            + (" — 실패분은 피드 단독 폴백(수집 자체는 정상)" if failed else ""))
    log("INFO", f"HC 수집 완료: {len(items)}건 (의약품/건강제품 {org_total}건 중 윈도우내, 전체 {total})")
    return items, None
