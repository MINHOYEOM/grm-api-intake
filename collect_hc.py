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

from datetime import date
from typing import Any
from urllib.parse import urljoin

from grm_common import http_get_json, log
from collect_intake import (
    IntakeItem,
    SOURCE_HC,
    SRC_TYPE_OFFICIAL_API,
    _within_window,
)


HC_OPENDATA_URL = ("https://recalls-rappels.canada.ca/sites/default/files/"
                   "opendata-donneesouvertes/HCRSAMOpenData.json")
HC_BASE = "https://recalls-rappels.canada.ca"

TARGET_ORG = "drugs and health products"     # Organization 필터 (소문자 비교)
# 같은 Organization 안에도 수의약품/의료기기가 섞여 있어 Category로 추가 배제 (CODEX 검증).
_EXCLUDED_CATEGORIES = {
    "medical devices", "veterinary drugs", "drugs - veterinary drugs",
}
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


def _to_item(rec: dict[str, Any], start: date, end: date) -> IntakeItem | None:
    nid = _text(rec, "NID")
    title = _text(rec, "Title")
    if not nid or not title:
        return None
    date_iso = _parse_date(_text(rec, "Last updated", "Date published"))
    if not _within_window(date_iso, start, end):
        return None

    category = _text(rec, "Category")
    if category.lower() in _EXCLUDED_CATEGORIES:   # 수의약품/의료기기 배제
        return None
    product = _text(rec, "Product")
    issue = _text(rec, "Issue")
    recall_class = _text(rec, "Recall class")
    url = urljoin(HC_BASE, _text(rec, "URL")) if _text(rec, "URL") else HC_BASE

    blob = f"{title} {product} {issue}".lower()
    tier = _signal_tier(recall_class, blob)
    relevance = "Likely" if category.lower() == "drugs" else "Possible"
    if any(t in blob for t in _HC_QUALITY_TERMS):
        relevance = "Likely"

    body_parts = [
        f"분류: {category}" if category else "",
        f"등급(Recall class): {recall_class}" if recall_class else "",
        f"제품: {product}" if product else "",
        f"사유(Issue): {issue}" if issue else "",
        f"조치(What you should do): {_text(rec, 'What you should do')}"
        if _text(rec, "What you should do") else "",
    ]
    return IntakeItem(
        source=SOURCE_HC,
        document_id=f"hc-{nid}",
        date_iso=date_iso,
        headline=f"[HC] {title}"[:240],
        official_url=url,                       # 항목별 공식 URL(절대)
        type_or_class=TYPE_HC_RECALL,
        body="\n".join(p for p in body_parts if p),
        api_query=HC_OPENDATA_URL,
        qa_relevance=relevance,
        osd_relevance=_osd_relevance(product),
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier=tier,
        # product_type/product_description 를 정규화해 compute_modality(제품군 분류)가
        # HC drug recall 을 인식하도록 한다(Category=Drugs → Chemical, 제품명 생물단서 → Biologic).
        raw_payload={
            "api": "HC RSAM OpenData", "nid": nid, **rec,
            "product_type": category, "product_description": product,
        },
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
    for rec in data:
        if not isinstance(rec, dict):
            continue
        if _text(rec, "Organization").lower() != TARGET_ORG:
            continue
        org_total += 1
        item = _to_item(rec, start, end)
        if item is None or item.document_id in seen:
            continue
        seen.add(item.document_id)
        items.append(item)

    if org_total == 0:
        # 전체 피드에 "Drugs and health products" Organization이 하나도 없음 = 필드/값 변경 의심
        return [], (f"HC 피드에서 Organization='{TARGET_ORG}' 레코드 0건(total={total}) "
                    f"— 필드/값 변경 의심(수동 확인 필요)")

    log("INFO", f"HC 수집 완료: {len(items)}건 (의약품/건강제품 {org_total}건 중 윈도우내, 전체 {total})")
    return items, None
