#!/usr/bin/env python3
"""Pure helpers for legacy intake records and canonical URL deduplication."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, NamedTuple, TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from grm_common import (
    NOTION_RICH_TEXT_CHUNK,
    SOURCE_BRAVE,
    SOURCE_ECA,
    SOURCE_EMA,
    SOURCE_EPR,
    SOURCE_FDA_WL,
    SOURCE_FR,
    SOURCE_ISPE,
    SOURCE_MFDS,
    SOURCE_MHRA,
    SOURCE_PICS,
    SOURCE_RAPS,
    SOURCE_RECALL,
    chunk_text,
    env_flag,
)
from grm_taxonomy import (
    MODALITY_BIOLOGIC,
    MODALITY_CHEMICAL,
    MODALITY_OTHER,
    compute_modality,
)


if TYPE_CHECKING:
    from collect_intake import IntakeItem


PROP_NAME = "Name"


PROP_SOURCE = "Source"


PROP_DOC_ID = "Document ID"


PROP_DATE = "Date"


PROP_HEADLINE = "Headline"


PROP_OFFICIAL_URL = "Official URL"


PROP_TYPE_CLASS = "Type or Class"


PROP_FIRM = "Firm"


PROP_BODY = "Body"


PROP_DISTRIBUTION = "Distribution"


PROP_COMMENTS_CLOSE = "Comments Close"


PROP_RUN_DATE = "Run Date (KST)"


PROP_COLLECTED_AT = "Collected At"


PROP_API_QUERY = "API Query"


PROP_QA_RELEVANCE = "QA Relevance"


PROP_STATUS = "Status"


PROP_SIGNAL_TIER = "Signal Tier"


PROP_LANGUAGE = "Language"


PROP_REGION_JURISDICTION = "Region/Jurisdiction"


PROP_SITE_COUNTRY = "Site Country"


PROP_SOURCE_URL         = "Source URL"


PROP_RAW_EXCERPT        = "Raw Excerpt"


PROP_SEARCH_QUERY       = "Search Query"


PROP_EVIDENCE_CANDIDATE = "Evidence Candidate"


PROP_HANDOFF_REF = "Handoff Ref"


PROP_SOURCE_TYPE = "Source Type"


PROP_OSD_RELEVANCE = "OSD Relevance"   # Notion select: Direct / Indirect / N/A


PROP_MODALITY = "Modality"


MODALITY_OPTIONS = (MODALITY_CHEMICAL, MODALITY_BIOLOGIC, MODALITY_OTHER)


NOTION_CODE_BLOCK_CHUNK = 1900


def canonical_url_key(source: str, url: str) -> str:
    """`source::정규화URL` — **기사 단위 permalink** 를 쓰는 소스의 2차 dedup 키.

    ⚠️ 이 키는 `document_id` 키를 **대체하지 않는다**(그건 불가침이다 — 바꾸면 기존 전 row 의
    id 가 달라져 일제히 신규로 재유입된다). 같은 기사가 **날짜만 바꿔 다시 오는** 경우를 잡는
    **추가 층**일 뿐이다: `_stable_doc_id` 가 `date_iso` 를 키에 넣으므로 RSS 가 기사를 재게시하면
    doc_id 자체가 달라져 doc_id 기반 dedup 으로는 구조적으로 잡을 수 없다(2026-08-24 실측:
    ECA 126행 중 고유 기사 75개 — 중복 40.5%, 간격은 1~2일이 압도적).

    정규화는 **보수적**으로만 한다 — scheme·host 소문자화, fragment 제거, 말미 슬래시 1개 제거.
    query 는 **보존**한다(`?id=123` 처럼 query 가 문서를 가르는 사이트가 있어, 지우면 서로 다른
    문서가 한 키로 뭉쳐 조용히 유실된다). 정규화가 과하면 dedup 은 곧 데이터 손실이 된다.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return f"{source}::{raw.lower()}"
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    normalized = urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    return f"{source}::{normalized}"


class ExistingKeys(NamedTuple):
    """dedup 대조용 기존 키 집합 — 같은 조회 1패스에서 함께 모은다(추가 API 비용 0)."""

    doc_ids: set[str]   # "{source}::{document_id}"
    url_keys: set[str]  # "{source}::{정규화 official_url}"


# Notion API 한도: rich_text 배열은 최대 100 요소. 저장 단계 절단 금지 원칙의 유일한 예외이며,
# 전문은 Supabase raw_json/row_json 에 그대로 남는다.
NOTION_RICH_TEXT_MAX_ELEMENTS = 100
# create/append 요청 하나가 받는 children 블록 최대 100개 (append 는 여유를 두고 90개씩).
NOTION_CREATE_CHILDREN_MAX = 100
NOTION_APPEND_BATCH = 90


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Notion rich_text 배열로 분할 (각 element ≤ 2000자, 배열 ≤ 100 요소)."""
    if not text:
        return []
    chunks = chunk_text(text, NOTION_RICH_TEXT_CHUNK)[:NOTION_RICH_TEXT_MAX_ELEMENTS]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _select(name: str) -> dict[str, Any]:
    return {"select": {"name": name}}


def _date_iso(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    return {"date": {"start": value}}


def _datetime_iso(value: datetime) -> dict[str, Any]:
    # Notion 은 ISO-8601 with offset 허용
    return {"date": {"start": value.isoformat()}}


def _url(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    return {"url": value}


def build_notion_properties(item: IntakeItem, run_date: date,
                            collected_at: datetime, *,
                            modality_enabled: bool | None = None) -> dict[str, Any]:
    # Name 타이틀 — 소스별 프리픽스
    _prefix_map = {
        SOURCE_FR:      "FR",
        SOURCE_RECALL:  "Recall",
        SOURCE_EMA:     "EMA",
        SOURCE_MHRA:    "MHRA",
        SOURCE_PICS:    "PICS",
        SOURCE_ECA:     "ECA",
        SOURCE_FDA_WL:  "WL",
        SOURCE_MFDS:    "MFDS",
        # Phase 2a 신규 ("SRC" 아님 — 모호함 방지)
        SOURCE_BRAVE:   "BRV",
        SOURCE_RAPS:    "RAPS",
        SOURCE_EPR:     "EPR",
        # [전문지 브리핑 소스확장 2026-07-13]
        SOURCE_ISPE:    "ISPE",
    }
    prefix = _prefix_map.get(item.source, item.source)
    if item.source in (SOURCE_RECALL, SOURCE_FDA_WL):
        name = f"{prefix} {item.document_id} — {item.firm or item.headline}"
    else:
        name = f"{prefix} {item.document_id} — {item.headline}"

    props: dict[str, Any] = {
        PROP_NAME: {"title": _rich_text(name)},
        PROP_SOURCE: _select(item.source),
        PROP_DOC_ID: {"rich_text": _rich_text(item.document_id)},
        PROP_HEADLINE: {"rich_text": _rich_text(item.headline)},
        PROP_COLLECTED_AT: _datetime_iso(collected_at),
        PROP_RUN_DATE: {"date": {"start": run_date.isoformat()}},
        PROP_QA_RELEVANCE: _select(item.qa_relevance),
        PROP_OSD_RELEVANCE: _select(item.osd_relevance),
        PROP_SOURCE_TYPE: _select(item.source_type),
        PROP_SIGNAL_TIER: _select(item.signal_tier),
        PROP_STATUS: _select("New"),
    }

    # ── 제품군(Modality) 태그 (제품군 확장) ─────────────────────────────────────
    # ENABLE_MODALITY_TAG=true 이고 Notion 에 'Modality' select 속성이 있을 때만 기록.
    # (기본 false — 속성 미생성 상태로 운영에 머지돼도 insert 가 깨지지 않도록 안전 게이트)
    # [배치6 Phase2] modality_enabled 미지정(=직접호출/테스트) 시 env 폴백으로 하위호환.
    # main 은 preflight 로 결정된 effective 값을 insert_items→notion_create_page 로 전달해
    # (os.environ 변조 없이) row 당 env 재독해를 제거한다.
    if modality_enabled is None:
        modality_enabled = env_flag("ENABLE_MODALITY_TAG")
    if modality_enabled:
        modality = compute_modality(
            item.raw_payload, item.headline, item.body,
            item.type_or_class, item.firm,
        )
        # [2026-09-21] 판별 근거가 없으면 MODALITY_UNKNOWN("") 이 온다. Notion select 는
        # 빈 이름을 거부하므로 속성 자체를 싣지 않는다(= Modality 미지정 → 배지 미표시).
        if modality:
            props[PROP_MODALITY] = _select(modality)

    if item.date_iso:
        d = _date_iso(item.date_iso)
        if d:
            props[PROP_DATE] = d
    if item.official_url:
        u = _url(item.official_url)
        if u:
            props[PROP_OFFICIAL_URL] = u
    if item.type_or_class:
        # Select 옵션은 자동 생성됨
        props[PROP_TYPE_CLASS] = _select(item.type_or_class[:100])
    if item.firm:
        props[PROP_FIRM] = {"rich_text": _rich_text(item.firm)}
    if item.body:
        props[PROP_BODY] = {"rich_text": _rich_text(item.body)}
    if item.distribution:
        props[PROP_DISTRIBUTION] = {"rich_text": _rich_text(item.distribution)}
    if item.comments_close_iso:
        d = _date_iso(item.comments_close_iso)
        if d:
            props[PROP_COMMENTS_CLOSE] = d
    if item.api_query:
        u = _url(item.api_query)
        if u:
            props[PROP_API_QUERY] = u

    # ── Phase 2a 신규 필드 매핑 ─────────────────────────────────────────────
    if item.source_url:
        u = _url(item.source_url)
        if u:
            props[PROP_SOURCE_URL] = u
    if item.raw_excerpt:
        props[PROP_RAW_EXCERPT] = {
            "rich_text": _rich_text(item.raw_excerpt)
        }
    if item.search_query:
        props[PROP_SEARCH_QUERY] = {
            "rich_text": _rich_text(item.search_query)
        }
    if item.evidence_candidate:
        props[PROP_EVIDENCE_CANDIDATE] = _select(item.evidence_candidate)
    if item.language:
        props[PROP_LANGUAGE] = _select(item.language)
    if item.region_jurisdiction:
        props[PROP_REGION_JURISDICTION] = _select(item.region_jurisdiction)
    if item.site_country:
        props[PROP_SITE_COUNTRY] = {"rich_text": _rich_text(item.site_country)}

    return props


def build_notion_children(item: IntakeItem) -> list[dict[str, Any]]:
    """페이지 본문에 raw API JSON 을 code block 으로 저장."""
    return build_raw_payload_children(item.raw_payload)


def build_raw_payload_children(raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """raw dict → 'Raw API payload' 블록들(`grm_handoff.fetch_intake_raw_payload` 의 역)."""
    raw_json = json.dumps(raw_payload, ensure_ascii=False, indent=2)
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text",
                               "text": {"content": "Raw API payload"}}],
            },
        }
    ]
    for chunk in chunk_text(raw_json, NOTION_CODE_BLOCK_CHUNK):
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "language": "json",
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        })
    return blocks
