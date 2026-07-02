#!/usr/bin/env python3
"""GRM Intake — Notion API 클라이언트 층 (배치5 Phase1, collect_intake 에서 verbatim 분리).

Notion HTTP 를 만지는 저수준 래퍼 + preflight + dedupe 조회 + 페이지네이션 + 속성/children
빌드 + 인테이크 페이지 생성. `notion_api_request`·`notion_create_page` 의 재시도 로직은 통합
하지 않고 원형 그대로 이동한다(통합은 별도 배치). collect_intake 로의 역참조 없음(단방향:
collect_intake → grm_notion, grm_handoff → grm_notion). 기존 참조 경로
(collect_intake.notion_create_page 등)는 collect_intake 가 이 모듈을 재수출해 보존한다.

`IntakeItem` 은 시그니처 주석으로만 참조되며 `from __future__ import annotations` 로 지연
평가되는 문자열이라 런타임 import 가 불필요하다(순환 방지·타입 위치는 collect_intake 유지).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests

from grm_common import (
    NOTION_RICH_TEXT_CHUNK,
    SOURCE_BRAVE,
    SOURCE_ECA,
    SOURCE_EMA,
    SOURCE_EPR,
    SOURCE_FDA_WL,
    SOURCE_FR,
    SOURCE_MFDS,
    SOURCE_MHRA,
    SOURCE_PICS,
    SOURCE_RAPS,
    SOURCE_RECALL,
    chunk_text,
    env_flag,
    log,
    retry_after_seconds,
    truncate,
)
from grm_taxonomy import (
    MODALITY_BIOLOGIC,
    MODALITY_CHEMICAL,
    MODALITY_OTHER,
    compute_modality,
)


NOTION_API_VERSION = "2022-06-28"


NOTION_PAGES_URL = "https://api.notion.com/v1/pages"


NOTION_PAGE_URL_TPL = "https://api.notion.com/v1/pages/{page_id}"


NOTION_DB_QUERY_URL_TPL = "https://api.notion.com/v1/databases/{db_id}/query"


NOTION_BLOCK_CHILDREN_URL_TPL = "https://api.notion.com/v1/blocks/{block_id}/children"


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


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


class NotionDedupeQueryError(RuntimeError):
    """Notion 중복 조회 실패 전용 예외 — insert 중단 판단에 사용."""
    pass


class NotionHandoffError(RuntimeError):
    """Routine handoff 생성/갱신 실패 전용 예외."""
    pass


def notion_api_request(method: str, url: str, token: str, *,
                       body: dict[str, Any] | None = None,
                       retries: int = 2) -> dict[str, Any]:
    """Notion JSON API 호출 공통 래퍼. 429/5xx 는 짧게 재시도한다."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, json=body,
                                    headers=notion_headers(token), timeout=30)
            if resp.status_code == 429 and attempt < retries:
                sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                log("WARN", f"Notion API 429 rate-limit — {sleep_s}s 후 재시도 "
                            f"({attempt + 1}/{retries + 1})")
                time.sleep(sleep_s)
                continue
            if resp.status_code >= 500 and attempt < retries:
                log("WARN", f"Notion API {resp.status_code} — 재시도 "
                            f"({attempt + 1}/{retries + 1}) body={resp.text[:200]}")
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise NotionHandoffError(
                    f"Notion API {method} {url} 실패 ({resp.status_code}): "
                    f"{resp.text[:300]}"
                )
            if not resp.text:
                return {}
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log("WARN", f"Notion API 네트워크 오류 — 재시도 "
                            f"({attempt + 1}/{retries + 1}) err={e}")
                time.sleep(2 ** attempt)
                continue
            break
        except ValueError as e:
            raise NotionHandoffError(f"Notion API JSON 파싱 실패: {e}") from e
    raise NotionHandoffError(f"Notion API {method} {url} 실패: {last_err}")


def notion_verify_modality_property(token: str, db_id: str) -> bool:
    """ENABLE_MODALITY_TAG=true 활성화 시 Notion 'Modality' 속성 사전 점검(preflight).

    DB 에 'Modality' 가 Select 타입으로 존재하는지 확인한다. 없거나 타입이 다르면
    첫 insert 부터 전부 실패하므로, 그 전에 깨끗하게 False 를 반환해 호출부가
    'N건 insert 실패' 대신 '스키마 불일치'로 한 번에 알리고 graceful degrade 하도록 한다.

    반환: True = 기록 진행 OK / False = 스키마 불일치(이번 실행 Modality 기록 건너뜀).
    (Select 옵션 Chemical/Biologic/Other 누락은 insert 시 자동 생성되므로 경고만.)
    """
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        data = notion_api_request("GET", url, token)
    except NotionHandoffError as e:
        log("WARN", f"Modality preflight: DB 조회 실패 — {e}")
        return False
    prop = (data.get("properties", {}) or {}).get(PROP_MODALITY)
    if not prop:
        log("ERROR", f"Modality preflight 실패: Notion DB 에 '{PROP_MODALITY}' 속성이 없습니다. "
                     f"Select 속성(옵션 {', '.join(MODALITY_OPTIONS)})을 먼저 생성하세요.")
        return False
    ptype = prop.get("type")
    if ptype != "select":
        log("ERROR", f"Modality preflight 실패: '{PROP_MODALITY}' 속성 타입이 '{ptype}' — "
                     f"'select' 여야 합니다.")
        return False
    options = {o.get("name") for o in (prop.get("select", {}).get("options") or [])}
    missing = set(MODALITY_OPTIONS) - options
    if missing:
        log("WARN", f"Modality preflight: select 옵션 {sorted(missing)} 미존재 "
                    f"— insert 시 자동 생성됨(스키마 의도 확인 권장).")
    else:
        log("INFO", f"Modality preflight OK — '{PROP_MODALITY}' select 옵션 {sorted(options)}")
    return True


def notion_verify_handoff_ref_property(token: str, db_id: str) -> bool:
    """ENABLE_HANDOFF_IDEMPOTENCY_V2=true 활성화 시 'Handoff Ref' 속성 사전 점검(preflight).

    DB 에 'Handoff Ref' 가 rich_text 타입으로 존재하는지 확인한다. 없거나 타입이 다르면
    emit 의 ref 기록·reconcile 이 전부 실패하므로, 그 전에 False 를 반환해 호출부가
    이번 실행만 v2 를 끄고 v1(날짜 윈도우+K4-1)으로 graceful degrade 하도록 한다
    (`notion_verify_modality_property` 선례와 동일 패턴).
    """
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        data = notion_api_request("GET", url, token)
    except NotionHandoffError as e:
        log("WARN", f"Handoff Ref preflight: DB 조회 실패 — {e}")
        return False
    prop = (data.get("properties", {}) or {}).get(PROP_HANDOFF_REF)
    if not prop:
        log("ERROR", f"Handoff Ref preflight 실패: Notion DB 에 '{PROP_HANDOFF_REF}' 속성이 "
                     f"없습니다. Rich text 속성을 먼저 생성하세요.")
        return False
    ptype = prop.get("type")
    if ptype != "rich_text":
        log("ERROR", f"Handoff Ref preflight 실패: '{PROP_HANDOFF_REF}' 속성 타입이 "
                     f"'{ptype}' — 'rich_text' 여야 합니다.")
        return False
    log("INFO", f"Handoff Ref preflight OK — '{PROP_HANDOFF_REF}' rich_text 확인")
    return True


def notion_query_existing_doc_ids(token: str, db_id: str, run_date: date,
                                  window_days: int = 7,
                                  source_names: set[str] | None = None) -> set[str]:
    """최근 window_days 일(KST Run Date 기준) row 의 'source::document_id' key set 반환.

    daily 수집 전환(Phase 1)으로 dedupe 윈도우를 '당일' → '최근 window_days 일'로 확장.
    동일 항목이 윈도우 내 여러 daily run 에서 재삽입되는 것을 방지한다.

    dedupe key 형식: "{source}::{doc_id}"
    예) "Federal Register::{doc_id}", "OpenFDA Recall::{doc_id}",
        "Brave Search::{sha1(url)[:12]}" (Phase 2a 신규)
    Source 를 포함해 소스 간 ID 충돌을 방지한다.

    Raises:
        NotionDedupeQueryError: 조회 실패 시 — caller 가 insert 중단 여부를 결정.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    existing: set[str] = set()
    window_start = (run_date - timedelta(days=window_days)).isoformat()
    and_filters: list[dict[str, Any]] = [
        {"property": PROP_RUN_DATE, "date": {"on_or_after": window_start}},
        {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
    ]
    # source_names 지정 시 Source 한정(snapshot 소스 long-horizon dedup용).
    if source_names:
        and_filters.append({
            "or": [{"property": PROP_SOURCE, "select": {"equals": s}}
                   for s in sorted(source_names)]
        })
    body: dict[str, Any] = {
        "filter": {"and": and_filters},
        "page_size": 100,
    }
    start_cursor: str | None = None
    page_count = 0
    # P2 개선: dedup 윈도우가 enforcement(최대 30일)×전 소스로 넓어졌으므로 상한을 상향한다.
    # 100p × 100 = 10,000 row 헤드룸. 그래도 초과하면 partial 반환 대신 예외(아래 for-else).
    _DEDUP_MAX_PAGES = 100
    try:
        for _ in range(_DEDUP_MAX_PAGES):  # 안전 페이지 상한
            page_count += 1
            if start_cursor:
                body["start_cursor"] = start_cursor
            elif "start_cursor" in body:
                del body["start_cursor"]
            data: dict[str, Any] | None = None
            for attempt in range(3):
                resp = requests.post(url, json=body, headers=notion_headers(token), timeout=30)
                if resp.status_code == 429 and attempt < 2:
                    sleep_s = retry_after_seconds(resp, attempt, max_sleep=30)
                    log("WARN", f"Notion dedupe 429 rate-limit — {sleep_s}s 후 재시도 "
                                f"({attempt + 1}/3)")
                    time.sleep(sleep_s)
                    continue
                if resp.status_code >= 500 and attempt < 2:
                    log("WARN", f"Notion dedupe 조회 실패 ({resp.status_code}) "
                                f"attempt={attempt + 1}/3 body={resp.text[:200]}")
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            if data is None:
                raise NotionDedupeQueryError(
                    f"Notion 중복 조회 실패 (RunDate={run_date}): empty response"
                )
            for pg in data.get("results", []):
                props = pg.get("properties", {})
                # Source
                src = (props.get(PROP_SOURCE, {}).get("select") or {}).get("name", "")
                # Document ID
                doc_id_arr = props.get(PROP_DOC_ID, {}).get("rich_text", [])
                doc_id = "".join(rt.get("plain_text", "") for rt in doc_id_arr).strip()
                if src and doc_id:
                    existing.add(f"{src}::{doc_id}")
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
        else:
            # for-else: 상한을 모두 소진했는데 break 되지 않음 = has_more 잔존 = 상한 도달.
            # P2 개선: 일부 기존 row를 놓친 채 진행하면 중복 삽입 방어가 깨지므로,
            # WARN 후 partial 반환 대신 예외를 던져 caller(main)가 insert를 중단하게 한다.
            raise NotionDedupeQueryError(
                f"Notion 중복 조회 {_DEDUP_MAX_PAGES}페이지 상한 도달 — "
                f"dedup set 불완전(existing={len(existing)}건), 중복 삽입 방지 위해 중단"
            )
    except (requests.RequestException, ValueError) as e:
        # 중복 조회 실패 시 빈 set을 반환하면 모든 item을 신규로 판단해 대량 중복 insert 위험.
        # 안전하게 예외를 던져 caller 가 insert 중단 여부를 결정하도록 한다.
        raise NotionDedupeQueryError(
            f"Notion 중복 조회 실패 (RunDate={run_date}): {e}"
        ) from e
    log("INFO", f"Notion 기존 row {len(existing)} 건 (최근 {window_days}일, ~{run_date})")
    return existing


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Notion rich_text 배열로 분할 (각 element ≤ 2000자)."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": chunk}}
            for chunk in chunk_text(text, NOTION_RICH_TEXT_CHUNK)]


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
                            collected_at: datetime) -> dict[str, Any]:
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
    }
    prefix = _prefix_map.get(item.source, item.source)
    if item.source in (SOURCE_RECALL, SOURCE_FDA_WL):
        name = f"{prefix} {item.document_id} — {truncate(item.firm or item.headline, 100)}"
    else:
        name = f"{prefix} {item.document_id} — {truncate(item.headline, 100)}"

    props: dict[str, Any] = {
        PROP_NAME: {"title": _rich_text(name)},
        PROP_SOURCE: _select(item.source),
        PROP_DOC_ID: {"rich_text": _rich_text(item.document_id)},
        PROP_HEADLINE: {"rich_text": _rich_text(truncate(item.headline, NOTION_RICH_TEXT_CHUNK))},
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
    if env_flag("ENABLE_MODALITY_TAG"):
        modality = compute_modality(
            item.raw_payload, item.headline, item.body,
            item.type_or_class, item.firm,
        )
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
        props[PROP_FIRM] = {"rich_text": _rich_text(truncate(item.firm, NOTION_RICH_TEXT_CHUNK))}
    if item.body:
        props[PROP_BODY] = {"rich_text": _rich_text(truncate(item.body, NOTION_RICH_TEXT_CHUNK))}
    if item.distribution:
        props[PROP_DISTRIBUTION] = {"rich_text": _rich_text(truncate(item.distribution, NOTION_RICH_TEXT_CHUNK))}
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
            "rich_text": _rich_text(truncate(item.raw_excerpt, 200))
        }
    if item.search_query:
        props[PROP_SEARCH_QUERY] = {
            "rich_text": _rich_text(truncate(item.search_query, NOTION_RICH_TEXT_CHUNK))
        }
    if item.evidence_candidate:
        props[PROP_EVIDENCE_CANDIDATE] = _select(item.evidence_candidate)
    if item.language:
        props[PROP_LANGUAGE] = _select(item.language)
    if item.region_jurisdiction:
        props[PROP_REGION_JURISDICTION] = _select(item.region_jurisdiction)
    if item.site_country:
        props[PROP_SITE_COUNTRY] = {"rich_text": _rich_text(truncate(item.site_country, NOTION_RICH_TEXT_CHUNK))}

    return props


def build_notion_children(item: IntakeItem) -> list[dict[str, Any]]:
    """페이지 본문에 raw API JSON 을 code block 으로 저장."""
    raw_json = json.dumps(item.raw_payload, ensure_ascii=False, indent=2)
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


def notion_create_page(token: str, db_id: str, item: IntakeItem,
                       run_date: date, collected_at: datetime,
                       retries: int = 2) -> bool:
    """Notion 페이지 생성. 429/5xx 는 재시도, 4xx(429 제외)는 즉시 실패."""
    body = {
        "parent": {"database_id": db_id},
        "properties": build_notion_properties(item, run_date, collected_at),
        "children": build_notion_children(item),
    }
    # 재시도 불필요 상태 코드 (클라이언트 에러 — 재시도해도 동일 결과)
    _NO_RETRY_CODES = {400, 401, 403, 404, 409}

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(NOTION_PAGES_URL, json=body,
                                 headers=notion_headers(token), timeout=30)
            if resp.status_code < 400:
                return True
            if resp.status_code in _NO_RETRY_CODES:
                log("ERROR", f"Notion 페이지 생성 실패 ({resp.status_code}, 재시도 없음) "
                            f"doc={item.document_id} body={resp.text[:300]}")
                return False
            if resp.status_code == 429:
                retry_after = retry_after_seconds(resp, attempt, max_sleep=30)
                # 마지막 attempt 직전에는 sleep 후 재시도해도 의미 없으므로 생략
                if attempt < retries:
                    log("WARN", f"Notion 429 rate-limit doc={item.document_id} "
                                f"— {retry_after}s 후 재시도 ({attempt + 1}/{retries + 1})")
                    time.sleep(retry_after)
                continue
            # 500/502/503/504 등 서버 에러 — 지수 백오프 재시도
            log("WARN", f"Notion 페이지 생성 실패 ({resp.status_code}) "
                        f"doc={item.document_id} attempt={attempt + 1}/{retries + 1} "
                        f"body={resp.text[:200]}")
            if attempt < retries:
                time.sleep(2 ** attempt)
        except requests.Timeout as e:
            # Timeout: Notion이 서버 측에서 이미 row를 생성했을 수 있으므로 retry 금지.
            # retry 시 duplicate row 위험. 즉시 실패 처리 후 상위에서 insert_failed 집계.
            log("ERROR", f"Notion 페이지 생성 timeout — retry 금지 (duplicate 방지) "
                         f"doc={item.document_id} err={e}")
            return False
        except requests.RequestException as e:
            # 그 외 네트워크 오류 (ConnectionError 등): 서버 미수신 가능성 높으므로 재시도
            last_err = e
            log("WARN", f"Notion 페이지 생성 네트워크 오류 doc={item.document_id} "
                        f"attempt={attempt + 1}/{retries + 1} err={e}")
            if attempt < retries:
                time.sleep(2 ** attempt)

    log("ERROR", f"Notion 페이지 생성 최종 실패 doc={item.document_id} last_err={last_err}")
    return False
