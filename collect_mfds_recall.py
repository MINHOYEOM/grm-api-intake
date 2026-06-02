#!/usr/bin/env python3
"""GRM MFDS Recall Collector - Phase 2c.

Collects data.go.kr service 15059114 (MFDS medicinal recall/sales-stop
records) as high-signal intake rows.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from datetime import date
from typing import Any

from grm_common import http_get_json, log
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_API,
    _within_window,
)


RECALL_API_ENDPOINT = (
    "https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04"
    "/getMdcinRtrvlSleStpgelList03"
)
DATASET_URL = "https://www.data.go.kr/data/15059114/openapi.do"

TYPE_RECALL_QUALITY = "recall-quality"
LANGUAGE_KO = "KO"
REGION_MFDS = "Korea (MFDS)"

PAGE_SIZE = 100
MAX_PAGES = 25


def _mask_service_key(url: str) -> str:
    return re.sub(r"([?&]serviceKey=)[^&]+", r"\1***REDACTED***", url)


def _request_url(params: dict[str, Any]) -> str:
    return RECALL_API_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _api_query(params: dict[str, Any]) -> str:
    return _mask_service_key(_request_url(params))


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(raw: dict[str, Any], key: str) -> str:
    return str(raw.get(key) or "").strip()


def _parse_api_date(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        y, m, d = raw[:4], raw[4:6], raw[6:8]
        try:
            return date(int(y), int(m), int(d)).isoformat()
        except ValueError:
            return ""
    return ""


def _item_date(raw: dict[str, Any]) -> str:
    return _parse_api_date(_text(raw, "RECALL_COMMAND_DATE")) or _parse_api_date(
        _text(raw, "RTRVL_CMMND_DT")
    )


def _normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    """Normalize data.go.kr's item wrapper.

    Actual JSON for this service is usually:
      body.items = [{"item": {...}}, {"item": {...}}]
    but data.go.kr services can return a single dict when one result exists.
    """
    if raw_items is None:
        return []
    if isinstance(raw_items, list):
        out: list[dict[str, Any]] = []
        for item in raw_items:
            out.extend(_normalize_items(item))
        return out
    if isinstance(raw_items, dict):
        if "item" in raw_items:
            return _normalize_items(raw_items.get("item"))
        return [raw_items]
    return []


def _extract_items(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int, int, str]:
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    result_code = str(header.get("resultCode") or "").strip()
    result_msg = str(header.get("resultMsg") or "").strip()
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    page_no = _parse_int(body.get("pageNo"), 1)
    num_rows = _parse_int(body.get("numOfRows"), PAGE_SIZE)
    total_count = _parse_int(body.get("totalCount"), 0)
    items = _normalize_items(body.get("items"))
    return items, page_no, num_rows, total_count, f"{result_code}:{result_msg}"


def _document_id(raw: dict[str, Any]) -> str:
    product = _text(raw, "PRDUCT")
    firm = _text(raw, "ENTRPS")
    recall_date = _text(raw, "RECALL_COMMAND_DATE") or _text(raw, "RTRVL_CMMND_DT")[:8]
    reason = _text(raw, "RTRVL_RESN")
    key = f"{product}|{firm}|{recall_date}|{reason}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"recall-{digest}"


def _body(raw: dict[str, Any]) -> str:
    parts = [
        _text(raw, "RTRVL_RESN"),
        f"강제여부: {_text(raw, 'ENFRC_YN')}" if _text(raw, "ENFRC_YN") else "",
        f"회수명령일자: {_text(raw, 'RECALL_COMMAND_DATE')}" if _text(raw, "RECALL_COMMAND_DATE") else "",
        f"승인일자: {_text(raw, 'RTRVL_CMMND_DT')}" if _text(raw, "RTRVL_CMMND_DT") else "",
        f"품목기준코드: {_text(raw, 'ITEM_SEQ')}" if _text(raw, "ITEM_SEQ") else "",
        f"표준코드: {_text(raw, 'STD_CD')}" if _text(raw, "STD_CD") else "",
        f"사업자등록번호: {_text(raw, 'BIZRNO')}" if _text(raw, "BIZRNO") else "",
    ]
    return "\n".join(part for part in parts if part)


def _to_item(raw: dict[str, Any], api_query_url: str) -> IntakeItem | None:
    product = _text(raw, "PRDUCT")
    firm = _text(raw, "ENTRPS")
    reason = _text(raw, "RTRVL_RESN")
    date_iso = _item_date(raw)
    if not product or not date_iso:
        return None

    headline = f"[회수·판매중지] {product}"
    if firm:
        headline = f"{headline} — {firm}"

    # P0 개선: 듀얼 링크 추적성 보강. data.go.kr 회수 API는 항목별 공식 URL이 없어
    # official_url은 데이터셋 페이지(L2)로 유지하되, 품목기준코드(ITEM_SEQ)가 있으면
    # nedrug 품목 상세 '후보(미검증)' URL을 raw에 남겨 Routine이 검증·인용할 수 있게 한다.
    # (검증 안 된 링크를 official_url로 단언하지 않는다 — 신뢰성 원칙)
    raw_payload: dict[str, Any] = {
        "api": "data.go.kr 15059114",
        "endpoint": RECALL_API_ENDPOINT,
        **raw,
    }
    item_seq = _text(raw, "ITEM_SEQ")
    if item_seq:
        raw_payload["nedrug_item_candidate_url"] = (
            "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail"
            f"?itemSeq={urllib.parse.quote(item_seq, safe='')}"
        )
        raw_payload["nedrug_item_candidate_note"] = "Routine 검증 후 인용 (미검증 후보 URL)"

    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=_document_id(raw),
        date_iso=date_iso,
        headline=headline,
        official_url=DATASET_URL,
        type_or_class=TYPE_RECALL_QUALITY,
        firm=firm,
        body=_body(raw),
        api_query=api_query_url,
        qa_relevance="Likely",
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier="Tier 3",
        raw_payload=raw_payload,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
    )


def collect_mfds_recall(
    start: date,
    end: date,
    service_key: str,
) -> tuple[list[IntakeItem], str | None]:
    """Collect MFDS medicinal recall/sales-stop records.

    Returns (items, error_msg). A page-level failure is fatal only if no item was
    collected; otherwise the partial collection is returned with a warning log.
    """
    if not service_key:
        return [], "DATA_GO_KR_SERVICE_KEY 환경변수 필요"

    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    page_no = 1
    total_count = 0

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": PAGE_SIZE,
            "type": "json",
        }
        masked_url = _api_query(params)
        try:
            data = http_get_json(RECALL_API_ENDPOINT, params=params, timeout=30, retries=2)
            raw_items, response_page, num_rows, total_count, status = _extract_items(data)
            if not status.startswith("00:"):
                raise RuntimeError(f"API status {status}")
        except Exception as e:  # noqa: BLE001
            msg = f"MFDS recall API page={page_no} 실패: {e}"
            if items:
                log("WARN", msg)
                return items, None
            return [], msg

        if not raw_items:
            break

        page_dates: list[date] = []
        for raw in raw_items:
            date_iso = _item_date(raw)
            if date_iso:
                try:
                    page_dates.append(date.fromisoformat(date_iso))
                except ValueError:
                    pass
            if not _within_window(date_iso, start, end):
                continue
            item = _to_item(raw, masked_url)
            if item is None or item.document_id in seen_ids:
                continue
            seen_ids.add(item.document_id)
            items.append(item)

        if page_dates and max(page_dates) < start:
            break
        if total_count and response_page * num_rows >= total_count:
            break
        page_no += 1

    # P2 개선: page cap 도달은 WARN-only로 묻지 않고 truncated 에러로 올려
    # collect_intake summary/error에 드러나게 한다(scheduled run이 green으로 끝나는 것 방지).
    truncated_msg: str | None = None
    if page_no > MAX_PAGES:
        truncated_msg = (f"MFDS recall API max_pages={MAX_PAGES} 도달 — truncated "
                         f"(수집 {len(items)}건, totalCount={total_count}, 이후 항목 누락 가능)")
        log("WARN", truncated_msg)

    log(
        "INFO",
        "MFDS recall 수집 완료: "
        f"{len(items)}건 (totalCount={total_count})",
    )
    return items, truncated_msg
