#!/usr/bin/env python3
"""GRM MFDS GMP certificate/status collector."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from grm_common import (
    DatagoPageError,
    datago_extract_items,
    datago_paginate,
    http_get_json,
    log,
    text_field,
)
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_API,
)


GMP_CERT_API_ENDPOINT = (
    "https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService"
    "/getDrugGmpStbltJgmtIssuStusInq"
)
DATASET_URL = "https://www.data.go.kr/data/15097207/openapi.do"

TYPE_GMP_CERTIFICATE = "gmp-certificate"
LANGUAGE_KO = "KO"
REGION_MFDS = "Korea (MFDS)"

PAGE_SIZE = 100
MAX_PAGES = 10


def _parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])).isoformat()
        except ValueError:
            return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return ""
    return ""


def _document_id(raw: dict[str, Any]) -> str:
    key = "|".join(
        [
            text_field(raw, "BSSH_NM"),
            text_field(raw, "FCTR_ADDR"),
            text_field(raw, "KGMP_BGMP_NAME"),
            text_field(raw, "GMP_INGR_MM_GROUP_NAME"),
            text_field(raw, "VLD_PRD_YMD"),
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"gmpcert-{digest}"


def _body(raw: dict[str, Any]) -> str:
    parts = [
        f"업체명: {text_field(raw, 'BSSH_NM')}" if text_field(raw, "BSSH_NM") else "",
        f"공장소재지: {text_field(raw, 'FCTR_ADDR')}" if text_field(raw, "FCTR_ADDR") else "",
        f"완제/원료 구분: {text_field(raw, 'KGMP_BGMP_NAME')}" if text_field(raw, "KGMP_BGMP_NAME") else "",
        f"제형군/제조방법: {text_field(raw, 'GMP_INGR_MM_GROUP_NAME')}" if text_field(raw, "GMP_INGR_MM_GROUP_NAME") else "",
        f"유효기한: {text_field(raw, 'VLD_PRD_YMD')}" if text_field(raw, "VLD_PRD_YMD") else "",
    ]
    return "\n".join(part for part in parts if part)


def _to_item(raw: dict[str, Any], api_query_url: str) -> IntakeItem | None:
    firm = text_field(raw, "BSSH_NM")
    address = text_field(raw, "FCTR_ADDR")
    group = text_field(raw, "GMP_INGR_MM_GROUP_NAME")
    valid_until = _parse_date(text_field(raw, "VLD_PRD_YMD"))
    if not firm or not (group or address):
        return None

    headline = f"[GMP적합판정] {firm}"
    if group:
        headline += f" — {group}"
    date_iso = valid_until or date.today().isoformat()
    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=_document_id(raw),
        date_iso=date_iso,
        headline=headline,
        official_url=DATASET_URL,
        type_or_class=TYPE_GMP_CERTIFICATE,
        firm=firm,
        body=_body(raw),
        api_query=api_query_url,
        qa_relevance="Possible",
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier="Tier 1",
        raw_payload={
            "api": "data.go.kr 15097207",
            "endpoint": GMP_CERT_API_ENDPOINT,
            **raw,
        },
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
    )


def collect_mfds_gmp_certs(
    start: date,
    end: date,
    service_key: str,
) -> tuple[list[IntakeItem], str | None]:
    """Collect MFDS GMP certificate/status rows.

    The API is a current-status table and does not expose a publication date.
    ``start``/``end`` are accepted for the shared collector signature; dedupe
    controls repeated status rows.
    """
    del start, end
    if not service_key:
        return [], "DATA_GO_KR_SERVICE_KEY 환경변수 필요"

    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    paginator = datago_paginate(
        GMP_CERT_API_ENDPOINT, service_key=service_key, extract=datago_extract_items,
        http_get=http_get_json, max_pages=MAX_PAGES, page_size=PAGE_SIZE)
    try:
        for raw_items, masked_url in paginator:
            for raw in raw_items:
                item = _to_item(raw, masked_url)
                if item is None or item.document_id in seen_ids:
                    continue
                seen_ids.add(item.document_id)
                items.append(item)
    except DatagoPageError as e:
        msg = f"MFDS GMP certificate API page={e.page_no} 실패: {e.cause}"
        if items:
            log("WARN", msg)
            return items, None
        return [], msg

    total_count = paginator.total_count
    truncated_msg: str | None = None
    if paginator.truncated:
        truncated_msg = (f"MFDS GMP certificate API max_pages={MAX_PAGES} 도달 — "
                         f"truncated (수집 {len(items)}건, totalCount={total_count})")
        log("WARN", truncated_msg)
    log("INFO", f"MFDS GMP certificate 수집 완료: {len(items)}건 (totalCount={total_count})")
    return items, truncated_msg
