#!/usr/bin/env python3
"""GRM MFDS safety-letter collector via data.go.kr."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from grm_common import (
    DatagoPageError,
    datago_extract_items,
    datago_paginate,
    http_get_json,
    log,
    parse_datago_date,
    text_field,
)
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_API,
    _within_window,
)
from collect_mfds import (
    LANGUAGE_KO,
    REGION_MFDS,
    TYPE_SAFETY_LETTER,
    _mfds_relevance,
    _mfds_tier,
)


SAFETY_LETTER_API_ENDPOINT = (
    "https://apis.data.go.kr/1471000/DrugSafeLetterService02"
    "/getDrugSafeLetterList02"
)
DATASET_URL = "https://www.data.go.kr/data/15059182/openapi.do"

PAGE_SIZE = 100
MAX_PAGES = 20


def _item_date(raw: dict[str, Any]) -> str:
    return (
        parse_datago_date(text_field(raw, "PBANC_YMD").replace("-", ""))
        or parse_datago_date(text_field(raw, "RLS_BGNG_YMD").replace("-", ""))
    )


def _document_id(raw: dict[str, Any]) -> str:
    seq = text_field(raw, "SAFT_LETT_NO")
    if seq:
        return f"safety-{seq}"
    key = "|".join([
        text_field(raw, "TITLE"),
        text_field(raw, "PBANC_NO"),
        text_field(raw, "PBANC_YMD"),
    ])
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"safety-{digest}"


def _official_url(raw: dict[str, Any]) -> str:
    attachment = text_field(raw, "ATTACH_FILE_URL")
    if attachment.startswith("http://") or attachment.startswith("https://"):
        return attachment
    return DATASET_URL


def _body(raw: dict[str, Any]) -> str:
    parts = [
        text_field(raw, "SUMRY_CONT"),
        text_field(raw, "PBANC_CONT"),
        f"조치사항: {text_field(raw, 'ACTN_MTTR_CONT')}" if text_field(raw, "ACTN_MTTR_CONT") else "",
        f"공고번호: {text_field(raw, 'PBANC_NO')}" if text_field(raw, "PBANC_NO") else "",
        f"공고구분: {text_field(raw, 'PBANC_DIVS_NM')}" if text_field(raw, "PBANC_DIVS_NM") else "",
        f"공고일자: {text_field(raw, 'PBANC_YMD')}" if text_field(raw, "PBANC_YMD") else "",
        f"공개시작일: {text_field(raw, 'RLS_BGNG_YMD')}" if text_field(raw, "RLS_BGNG_YMD") else "",
        f"담당부서: {text_field(raw, 'CHRG_DEP')}" if text_field(raw, "CHRG_DEP") else "",
        f"첨부파일: {text_field(raw, 'ATTACH_FILE_URL')}" if text_field(raw, "ATTACH_FILE_URL") else "",
    ]
    return "\n".join(part for part in parts if part)


def _to_item(raw: dict[str, Any], api_query_url: str) -> IntakeItem | None:
    title = text_field(raw, "TITLE")
    date_iso = _item_date(raw)
    if not title or not date_iso:
        return None
    body = _body(raw)
    relevance = _mfds_relevance(title, body)
    if relevance == "Pending":
        relevance = "Possible"
    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=_document_id(raw),
        date_iso=date_iso,
        headline=f"[안전성서한] {title}" if "안전성" not in title else title,
        official_url=_official_url(raw),
        type_or_class=TYPE_SAFETY_LETTER,
        body=body,
        api_query=api_query_url,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier=_mfds_tier(TYPE_SAFETY_LETTER, relevance, title, body),
        raw_payload={
            "api": "data.go.kr 15059182",
            "endpoint": SAFETY_LETTER_API_ENDPOINT,
            **raw,
        },
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
    )


def collect_mfds_safety_letters(
    start: date,
    end: date,
    service_key: str,
) -> tuple[list[IntakeItem], str | None]:
    """Collect MFDS medicinal safety letters."""
    if not service_key:
        return [], "DATA_GO_KR_SERVICE_KEY 환경변수 필요"

    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    paginator = datago_paginate(
        SAFETY_LETTER_API_ENDPOINT, service_key=service_key, extract=datago_extract_items,
        http_get=http_get_json, max_pages=MAX_PAGES, page_size=PAGE_SIZE)
    try:
        for raw_items, masked_url in paginator:
            for raw in raw_items:
                date_iso = _item_date(raw)
                if not _within_window(date_iso, start, end):
                    continue
                item = _to_item(raw, masked_url)
                if item is None or item.document_id in seen_ids:
                    continue
                seen_ids.add(item.document_id)
                items.append(item)
    except DatagoPageError as e:
        msg = f"MFDS safety-letter API page={e.page_no} 실패: {e.cause}"
        if items:
            log("WARN", msg)
            return items, None
        return [], msg

    total_count = paginator.total_count
    truncated_msg: str | None = None
    if paginator.truncated:
        truncated_msg = (f"MFDS safety-letter API max_pages={MAX_PAGES} 도달 — truncated "
                         f"(수집 {len(items)}건, totalCount={total_count})")
        log("WARN", truncated_msg)
    log("INFO", f"MFDS safety-letter 수집 완료: {len(items)}건 (totalCount={total_count})")
    return items, truncated_msg
