#!/usr/bin/env python3
"""GRM Intake — Routine handoff / web brief emit 층 (배치5 Phase2, collect_intake 에서 verbatim 분리).

handoff v1/v2 payload 조립·emit·STALE 가드·PL-10b reconcile·조회 윈도우(B1)·§1-B web brief
emit·W1 coverage 포맷터 + Notion intake page snapshot 리더. grm_notion(Phase1)·grm_common·
card_scaffold 에 의존한다(단방향: grm_handoff → grm_notion, 역방향 금지). 기존 참조 경로
(collect_intake.emit_routine_handoff 등)는 collect_intake 가 이 모듈을 재수출해 보존한다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Any

import requests

from grm_common import (
    SOURCE_ECA,
    SOURCE_EMA,
    SOURCE_FDA_WL,
    SOURCE_FR,
    SOURCE_HANDOFF,
    SOURCE_HC,
    SOURCE_ICH,
    SOURCE_MFDS,
    SOURCE_MHRA,
    SOURCE_PICS,
    SOURCE_RECALL,
    SOURCE_WHO,
    _env_int,
    chunk_text,
    env_flag,
    log,
    truncate,
)
from grm_notion import (
    NOTION_BLOCK_CHILDREN_URL_TPL,
    NOTION_CODE_BLOCK_CHUNK,
    NOTION_DB_QUERY_URL_TPL,
    NOTION_PAGES_URL,
    NOTION_PAGE_URL_TPL,
    NotionHandoffError,
    PROP_API_QUERY,
    PROP_BODY,
    PROP_COLLECTED_AT,
    PROP_COMMENTS_CLOSE,
    PROP_DATE,
    PROP_DISTRIBUTION,
    PROP_DOC_ID,
    PROP_EVIDENCE_CANDIDATE,
    PROP_FIRM,
    PROP_HANDOFF_REF,
    PROP_HEADLINE,
    PROP_LANGUAGE,
    PROP_MODALITY,
    PROP_NAME,
    PROP_OFFICIAL_URL,
    PROP_OSD_RELEVANCE,
    PROP_QA_RELEVANCE,
    PROP_RAW_EXCERPT,
    PROP_REGION_JURISDICTION,
    PROP_RUN_DATE,
    PROP_SEARCH_QUERY,
    PROP_SIGNAL_TIER,
    PROP_SITE_COUNTRY,
    PROP_SOURCE,
    PROP_SOURCE_TYPE,
    PROP_SOURCE_URL,
    PROP_STATUS,
    PROP_TYPE_CLASS,
    _datetime_iso,
    _rich_text,
    _select,
    notion_api_request,
)
from card_scaffold import (
    assemble_web_brief,
    build_card_scaffold,
    compute_render_plan,
    dedupe_news_cards,
    merge_recall_cards,
)


TYPE_ROUTINE_HANDOFF = "routine-handoff"


HANDOFF_SCHEMA_VERSION = "grm-routine-handoff/v1"


HANDOFF_SCHEMA_VERSION_V2 = "grm-routine-handoff/v2"  # K2 단계 D (additive)


def _plain_text(parts: list[dict[str, Any]] | None) -> str:
    if not parts:
        return ""
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _prop_title(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("title", []))


def _prop_rich_text(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("rich_text", []))


def _prop_select(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("select") or {}).get("name", "") or ""


def _prop_date(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("date") or {}).get("start", "") or ""


def _prop_url(props: dict[str, Any], name: str) -> str:
    return props.get(name, {}).get("url") or ""


def _intake_page_snapshot(page: dict[str, Any]) -> dict[str, Any]:
    props = page.get("properties", {})
    return {
        "page_id": page.get("id", ""),
        "page_url": page.get("url", ""),
        "title": _prop_title(props, PROP_NAME),
        "source": _prop_select(props, PROP_SOURCE),
        "document_id": _prop_rich_text(props, PROP_DOC_ID),
        "date": _prop_date(props, PROP_DATE),
        "headline": _prop_rich_text(props, PROP_HEADLINE),
        "official_url": _prop_url(props, PROP_OFFICIAL_URL),
        "source_url": _prop_url(props, PROP_SOURCE_URL),
        "type_or_class": _prop_select(props, PROP_TYPE_CLASS),
        "firm": _prop_rich_text(props, PROP_FIRM),
        "body": _prop_rich_text(props, PROP_BODY),
        "distribution": _prop_rich_text(props, PROP_DISTRIBUTION),
        "comments_close": _prop_date(props, PROP_COMMENTS_CLOSE),
        "run_date": _prop_date(props, PROP_RUN_DATE),
        "collected_at": _prop_date(props, PROP_COLLECTED_AT),
        "api_query": _prop_url(props, PROP_API_QUERY),
        "search_query": _prop_rich_text(props, PROP_SEARCH_QUERY),
        "raw_excerpt": _prop_rich_text(props, PROP_RAW_EXCERPT),
        "qa_relevance": _prop_select(props, PROP_QA_RELEVANCE),
        "osd_relevance": _prop_select(props, PROP_OSD_RELEVANCE),
        "modality": _prop_select(props, PROP_MODALITY),
        "source_type": _prop_select(props, PROP_SOURCE_TYPE),
        "signal_tier": _prop_select(props, PROP_SIGNAL_TIER),
        "evidence_candidate": _prop_select(props, PROP_EVIDENCE_CANDIDATE),
        "language": _prop_select(props, PROP_LANGUAGE),
        "region_jurisdiction": _prop_select(props, PROP_REGION_JURISDICTION),
        "site_country": _prop_rich_text(props, PROP_SITE_COUNTRY),
        "status": _prop_select(props, PROP_STATUS),
    }


_DEFAULT_HANDOFF_WINDOW_DAYS = 30


def resolve_handoff_window_days(cli_value: int | None) -> int:
    """handoff 조회 윈도우 결정 — CLI(--handoff-window-days) > GRM_HANDOFF_WINDOW_DAYS > 30."""
    if cli_value:
        return cli_value
    return _env_int("GRM_HANDOFF_WINDOW_DAYS", _DEFAULT_HANDOFF_WINDOW_DAYS)


def notion_query_new_intake_rows(token: str, db_id: str, run_date: date,
                                 window_days: int = 7,
                                 source_names: set[str] | None = None,
                                 doc_ids: set[str] | None = None,
                                 current_handoff_id: str | None = None,
                                 current_handoff_open: bool = True
                                 ) -> list[dict[str, Any]]:
    """Routine 에 넘길 Status=New row 를 Notion API 속성 필터로 조회한다.

    `current_handoff_id` 지정(멱등성 v2) 시 소비 자격을 날짜 윈도우가 아니라
    Handoff Ref 로 판정한다: `Status=New ∧ (Ref 비어있음 ∨ Ref=오늘 handoff)` —
    Run Date 하한 제거(PL-10b/B1 근본해결). `Ref=오늘` OR 절은 같은 날 재-emit 때
    이미 표시된 row 가 누락되지 않게 한다. 미지정(v1) 시 기존 날짜 윈도우 동작 그대로.

    `current_handoff_open=False`(Codex P1): 오늘 handoff 가 이미 CONSUMED/STALE 로
    종결된 경우 — `Ref=오늘` OR 절을 빼고 `Ref 비어있음` 만 자격으로 인정한다.
    이미 발행된 handoff 의 잔존 New row(Status 갱신 실패분)가 같은 날 재실행에서
    재유입되는 것을 차단한다(그 잔존분 마감은 reconcile 의 CONSUMED-cleanup 몫).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    window_start = (run_date - timedelta(days=window_days)).isoformat()
    if current_handoff_id:
        # v2: 날짜 하한 없음 — 상한(미래 Run Date 방어)과 ref 자격만.
        if current_handoff_open:
            ref_clause: dict[str, Any] = {"or": [
                {"property": PROP_HANDOFF_REF, "rich_text": {"is_empty": True}},
                {"property": PROP_HANDOFF_REF, "rich_text": {"equals": current_handoff_id}},
            ]}
        else:
            ref_clause = {"property": PROP_HANDOFF_REF, "rich_text": {"is_empty": True}}
        filters: list[dict[str, Any]] = [
            {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
            ref_clause,
        ]
    else:
        filters = [
            {"property": PROP_RUN_DATE, "date": {"on_or_after": window_start}},
            {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]
    body: dict[str, Any] = {
        "filter": {"and": filters},
        "page_size": 100,
    }
    snapshots: list[dict[str, Any]] = []
    start_cursor: str | None = None
    for page_no in range(50):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            if source_names and snap["source"] not in source_names:
                continue
            if doc_ids and snap["document_id"] not in doc_ids:
                continue
            if not snap["source"] or not snap["document_id"]:
                log("WARN", f"handoff 후보 row 필수 키 누락 — skip page={snap['page_id']}")
                continue
            snapshots.append(snap)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "Routine handoff New row 조회 50페이지 상한 도달 — 일부 row 누락 가능")

    if current_handoff_id:
        log("INFO", f"Routine handoff 후보 New row {len(snapshots)}건 "
                    f"(멱등성 v2 ref 기반 — Run Date ≤{run_date.isoformat()}, 날짜 하한 없음)")
    else:
        log("INFO", f"Routine handoff 후보 New row {len(snapshots)}건 "
                    f"(Run Date {window_start}~{run_date.isoformat()})")
    return snapshots


_AGED_NEW_MAX_PAGES = 5


_AGED_NEW_PAGE_SIZE = 50


def notion_count_aged_unconsumed_new(token: str, db_id: str, run_date: date,
                                     handoff_window_days: int) -> int:
    """handoff 조회 윈도우 밖에 남은 미소비 Status=New row 수(읽기전용, 하한값).

    필터: Status=New AND Run Date on_or_before (run_date − handoff_window_days − 1)
    — notion_query_new_intake_rows 하한(on_or_after run_date−window) 바로 바깥.
    handoff 페이지 자체(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아니므로
    동일 규칙으로 제외. 조회 실패는 예외를 그대로 올린다 — 호출부(main)가
    try/except 후 경고로 표면화한다(조용한 0 반환 금지).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    cutoff = (run_date - timedelta(days=handoff_window_days + 1)).isoformat()
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_RUN_DATE, "date": {"on_or_before": cutoff}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]},
        "page_size": _AGED_NEW_PAGE_SIZE,
    }
    count = 0
    start_cursor: str | None = None
    for _ in range(_AGED_NEW_MAX_PAGES):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            count += 1
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return count


_INTAKE_RAW_MAX_PAGES = 25  # children 100/page · raw 는 ≤2KB 청크라 1페이지로 충분(안전 상한)


def fetch_intake_raw_payload(token: str, page_id: str) -> dict[str, Any] | None:
    """Intake row(page_id) 본문의 JSON code block 들을 순서대로 이어붙여 raw dict 복원.

    `build_notion_children()` 이 저장한 'Raw API payload' code block(language=json,
    NOTION_CODE_BLOCK_CHUNK 청크)을 역으로 재조립한다. fetch/파싱 실패 시 None 반환
    (호출부 graceful degrade — 예외를 던지지 않아 전체 handoff 를 중단시키지 않는다).
    """
    if not page_id:
        return None
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    code_chunks: list[str] = []
    start_cursor: str | None = None
    try:
        for _ in range(_INTAKE_RAW_MAX_PAGES):
            req_url = url
            if start_cursor:
                req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}"
            data = notion_api_request("GET", req_url, token)
            for block in data.get("results", []):
                if block.get("type") != "code":
                    continue
                code = block.get("code", {})
                if (code.get("language") or "") not in ("json", "plain text", ""):
                    continue
                for rt in code.get("rich_text", []):
                    code_chunks.append(
                        rt.get("plain_text")
                        or rt.get("text", {}).get("content", "")
                        or ""
                    )
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
    except (NotionHandoffError, requests.RequestException) as e:
        log("WARN", f"K2-prep children fetch 실패 page={page_id}: {truncate(str(e), 120)}")
        return None
    if not code_chunks:
        return None
    raw_text = "".join(code_chunks)
    try:
        parsed = json.loads(raw_text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def attach_raw_to_rows(token: str, rows: list[dict[str, Any]],
                       inmemory_raw: dict[str, dict[str, Any]] | None = None,
                       sleep_s: float = 0.34) -> dict[str, int]:
    """각 row 에 raw API JSON 을 부착한다(하이브리드 — 지시문 단계 B).

    `inmemory_raw`(당일 수집분 raw_payload, key = `source::document_id`)에 있으면
    네트워크 없이 그대로 사용하고, 없으면(과거 누적 New row) page children 을 fetch 한다.
    실패 row 는 graceful degrade(card_spec §8, Codex 정정): raw=None ·
    raw_fetch_ok=False · evidence_hint='B'(A 불가) · status_hint='Error'(기존 DB
    옵션; 전용 'Needs Review' 옵션 신설은 K4 이월). 전체 중단 금지.

    ⚠️ raw 는 메모리 상 enriched row 에만 부착한다 — 최종 handoff v2 JSON 에는 넣지
    않는다(scaffold·prose_input 만; 크기 폭증·Notion children 한도 방지, 단계 B 보정).
    반환: {'ok','failed','from_memory','total'} 통계.
    """
    inmemory_raw = inmemory_raw or {}
    ok = failed = from_memory = 0
    for row in rows:
        card_id = f"{row.get('source', '')}::{row.get('document_id', '')}"
        cached = inmemory_raw.get(card_id)
        if cached is not None:
            row["raw"] = cached
            row["raw_fetch_ok"] = True
            row["raw_source"] = "memory"
            ok += 1
            from_memory += 1
            continue
        page_id = row.get("page_id", "")
        raw = fetch_intake_raw_payload(token, page_id)
        if raw is None:
            row["raw"] = None
            row["raw_fetch_ok"] = False
            row["raw_source"] = "fetch"
            row["evidence_hint"] = "B"
            row["status_hint"] = "Error"
            failed += 1
            log("WARN", "K2-prep raw 부착 실패 → graceful degrade(Evidence B·Status Error): "
                        f"{card_id} page={page_id}")
        else:
            row["raw"] = raw
            row["raw_fetch_ok"] = True
            row["raw_source"] = "fetch"
            ok += 1
        if sleep_s:
            time.sleep(sleep_s)  # 실제 fetch 한 경우만 rate-limit 대기
    log("INFO", f"K2-prep raw 부착 완료: 성공 {ok}건(메모리 {from_memory}·fetch {ok - from_memory}) "
                f"/ 실패 {failed}건 (총 {len(rows)})")
    return {"ok": ok, "failed": failed, "from_memory": from_memory, "total": len(rows)}


def enrich_rows_with_raw(token: str, rows: list[dict[str, Any]],
                         inmemory_raw: dict[str, dict[str, Any]] | None = None,
                         sleep_s: float = 0.34
                         ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """K2-prep 진입점: `_dedupe_latest_rows()` 선적용(중복 fetch 제거) → 하이브리드 raw 부착.

    handoff v2 생성 직전 단계. 반환 (deduped_rows, stats). v2 payload 빌더가 이 결과를
    소비하되 raw 는 JSON 직렬화에서 제외한다(단계 B·D 보정).
    """
    deduped = _dedupe_latest_rows(rows)
    stats = attach_raw_to_rows(token, deduped, inmemory_raw=inmemory_raw, sleep_s=sleep_s)
    return deduped, stats


def build_inmemory_raw(*item_lists: list["IntakeItem"]) -> dict[str, dict[str, Any]]:
    """당일 수집 IntakeItem 들을 `{card_id: raw_payload}` 로 모은다(K3 G2 와이어링).

    key = `source::document_id`(handoff row·`attach_raw_to_rows` 와 동일 규약). 이 dict 를
    `emit_routine_handoff(inmemory_raw=...)` 로 넘기면 당일 수집분은 children fetch 없이
    메모리에서 raw 를 부착하고, 과거 누적 New row 만 fetch 폴백한다(혼합 케이스).
    raw_payload 가 비어 있으면 제외 — `attach_raw_to_rows` 는 `get(card_id) is not None`
    으로 적중 판정하므로 빈 dict 가 들어가면 fetch 폴백을 가로채 graceful degrade 를 막는다.
    중복 card_id 는 첫 항목 우선(수집 순서 결정론).
    """
    out: dict[str, dict[str, Any]] = {}
    for items in item_lists:
        for it in items:
            if not it.raw_payload:
                continue
            out.setdefault(f"{it.source}::{it.document_id}", it.raw_payload)
    return out


def _dedupe_latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('source', '')}::{row.get('document_id', '')}"
        current = latest.get(key)
        freshness = (row.get("run_date", ""), row.get("collected_at", ""), row.get("page_id", ""))
        if current is None:
            latest[key] = row
            continue
        current_freshness = (
            current.get("run_date", ""),
            current.get("collected_at", ""),
            current.get("page_id", ""),
        )
        if freshness > current_freshness:
            latest[key] = row

    tier_order = {"Tier 3": 0, "Tier 2": 1, "Tier 1": 2}
    return sorted(
        latest.values(),
        key=lambda r: (
            tier_order.get(r.get("signal_tier", ""), 9),
            r.get("source", ""),
            r.get("document_id", ""),
        ),
    )


_KO_WEEKDAYS_FULL = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


def weekday_kst(run_date: date) -> str:
    """run_date(KST 달력일)의 한국어 요일 문자열(예: '수요일'). handoff weekday_kst 슬롯용."""
    return _KO_WEEKDAYS_FULL[run_date.weekday()]


COVERAGE_SOURCE_LABELS: tuple[tuple[str, str], ...] = (
    (SOURCE_FR, "FR"),
    (SOURCE_RECALL, "Recall"),
    (SOURCE_EMA, "EMA"),
    (SOURCE_MHRA, "MHRA"),
    (SOURCE_PICS, "PIC/S"),
    (SOURCE_ECA, "ECA"),
    (SOURCE_FDA_WL, "FDA WL"),
    (SOURCE_MFDS, "MFDS"),
    (SOURCE_ICH, "ICH"),
    (SOURCE_WHO, "WHO"),
    (SOURCE_HC, "HC"),
)


_COVERAGE_KNOWN_SOURCES = frozenset(s for s, _ in COVERAGE_SOURCE_LABELS)


def coverage_source_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """rows 에서 소스별 수집 건수 재집계 — build_routine_handoff_payload* 와 동일 산식.

    발행 후 탐지가 handoff rows(top-level source_counts 없이도)로 '수집' 정본을 독립
    복원할 때 쓴다(병합 멤버 포함 전수 카운트 — payload 의 source_counts 와 바이트 동일).
    """
    out: dict[str, int] = {}
    for row in rows or []:
        if isinstance(row, dict):
            src = row.get("source", "")
            out[src] = out.get(src, 0) + 1
    return out


def build_coverage_collected(source_counts: dict[str, int]) -> dict[str, Any]:
    """'수집' 컬럼(소스별 수집 건수 + 총계)을 결정론 산출한다(W1).

    반환 {"total": int, "items": [{"label","source","count"}...], "md": str}:
    - known 소스(COVERAGE_SOURCE_LABELS)는 고정 순서로 전부 포함(0건도 — '조용한 주' 가시화).
    - 라벨 미정의 소스(예: FDA 483)는 count>0 일 때만 원 이름으로 끝에 덧붙인다(조용한 유실 금지).
    - total = 모든 source_counts 합(= handoff row_count, 병합 멤버 포함).
    - md = 발행 callout 의 수집 세그먼트: "Intake row {total}건 ({label} {n} · ...)".
    LLM 은 md 를 그대로 삽입하고 병합·WebSearch·유효항목·Evidence·미확인 등 발행측 값만 채운다.
    """
    counts = {k: int(v) for k, v in (source_counts or {}).items()}
    items: list[dict[str, Any]] = []
    for source, label in COVERAGE_SOURCE_LABELS:
        items.append({"label": label, "source": source, "count": counts.get(source, 0)})
    for source in sorted(k for k in counts if k and k not in _COVERAGE_KNOWN_SOURCES):
        if counts[source] > 0:
            items.append({"label": source, "source": source, "count": counts[source]})
    total = sum(counts.values())
    seg = " · ".join(f"{it['label']} {it['count']}" for it in items)
    return {"total": total, "items": items, "md": f"Intake row {total}건 ({seg})"}


def build_routine_handoff_payload(rows: list[dict[str, Any]], run_date: date,
                                  window_days: int,
                                  generated_at: datetime) -> dict[str, Any]:
    start = run_date - timedelta(days=window_days)
    deduped = _dedupe_latest_rows(rows)
    source_counts: dict[str, int] = {}
    for row in deduped:
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_id": f"routine-handoff::{run_date.isoformat()}",
        "run_date_kst": run_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(deduped),
        "source_counts": source_counts,
        "rows": deduped,
    }


def _enable_handoff_v2() -> bool:
    """handoff v2 feature flag (기본 off, vars fallback 패턴). 운영 전환은 K3 와 함께."""
    return env_flag("ENABLE_HANDOFF_V2")


def _enable_web_brief_emit() -> bool:
    """빈슬롯 web brief emit flag (§1-B 영구배선, 기본 off, vars fallback 패턴).

    off = 현행과 byte 동일(파일 산출 0). on(+ENABLE_HANDOFF_V2 path) = 수집 시점에
    `brief_web_{run_date}.json`(grm-web-card/v1 빈슬롯)을 결정론 산출 → routine 델타를
    `inject_slots` 로 주입만 하면 그 주 산문 발행(1회용 파서·수기 fixture 제거). raw 가
    살아있는 handoff v2 카드 producer 를 재사용하므로 ENABLE_HANDOFF_V2 전제(아래 emit 분기).
    """
    return env_flag("ENABLE_WEB_BRIEF_EMIT")


def resolve_web_brief_dir() -> str:
    """빈슬롯 web brief 산출 디렉터리 — GRM_WEB_BRIEF_DIR > 현재 작업 디렉터리('.').

    워크플로(Option A)는 이 경로의 `brief_web_*.json` 을 artifact 로 업로드하고, 사람이
    `web/data/briefs/` 에 커밋한다(무인 라이브 0 = D5 게이트 보존). 직접 main push 금지.
    """
    return os.environ.get("GRM_WEB_BRIEF_DIR", "").strip() or "."


def _enable_handoff_idempotency_v2() -> bool:
    """PL-10b/B1 근본해결 flag (기본 off) — Handoff Ref 상태기계로 소비 자격 판정.

    off = 현행(날짜 윈도우 + K4-1 STALE) 100% 동일. on 전환은 K3 4주 관찰 종료 후
    사람 승인으로(Notion 'Handoff Ref' rich_text 속성 사전 생성 필요 — preflight 가
    부재를 감지하면 그 실행만 v1 으로 폴백). ENABLE_HANDOFF_V2(payload 스키마)와 직교.
    """
    return env_flag("ENABLE_HANDOFF_IDEMPOTENCY_V2")


_HANDOFF_V2_ROW_KEEP = (
    "page_id", "page_url", "title", "source", "document_id", "date", "headline",
    "official_url", "source_url", "type_or_class", "firm", "body", "distribution",
    "comments_close", "run_date", "collected_at", "api_query", "search_query",
    "raw_excerpt", "qa_relevance", "osd_relevance", "modality", "source_type",
    "signal_tier", "evidence_candidate", "language", "region_jurisdiction",
    "site_country", "status",
)


def build_routine_handoff_payload_v2(rows: list[dict[str, Any]], run_date: date,
                                     window_days: int,
                                     generated_at: datetime) -> dict[str, Any]:
    """handoff v2(additive) payload. 순수 함수 — 네트워크 없음(scaffold 조립만).

    `rows` 는 K2-prep(`enrich_rows_with_raw`)로 **dedupe·raw 부착**된 상태여야 한다.
    각 row 는 v1 호환 필드 whitelist 복사 + `card_scaffold`·`prose_input`·`section`·
    `card_id`·`evidence`·`recall_group_key`(해당 시)·`status_hint`(degrade 시) additive.
    **raw 전체·Stage B bookkeeping 은 제외**(크기 폭증·내부필드 누출 방지).
    recall 다품목은 `merge_recall_cards()`(§14)로 대표 1카드 + 멤버 `merged_into` 직렬화:
    멤버 row 는 v1 호환 필드 + `merged_into` 만 유지(자체 card_id 포함 v2 additive 필드 전부
    생략 → Routine 렌더 제외, page_id 보존으로 Status 갱신 목록에는 잔존, Codex R1-a).
    대표/단독 row 는 `render_order`·`group_label`(A안, R1-d)로 §7 정렬·그룹핑 결과를 받아
    Routine 이 정렬을 재현하지 않게 한다(`compute_render_plan` = assemble_brief_skeleton 공유).
    """
    start = run_date - timedelta(days=window_days)
    cards = dedupe_news_cards(
        merge_recall_cards([build_card_scaffold(row, row.get("raw")) for row in rows]))
    render_plan = compute_render_plan(cards)
    out_rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for row, card in zip(rows, cards):
        v2row = {k: row[k] for k in _HANDOFF_V2_ROW_KEEP if k in row}
        if card.merged_into:
            # §14(F)·R1-a 멤버: v1 호환 필드 + merged_into 만(자체 card_id·card_scaffold·
            # prose_input·needs_llm_slots·section·evidence·recall_group_key·render_order 생략).
            # 렌더 제외, page_id 보존으로 Status 갱신 목록에만 잔존. 그룹 식별은 merged_into 로.
            v2row["merged_into"] = card.merged_into
        else:
            v2row["card_id"] = card.card_id
            v2row["section"] = card.section
            v2row["evidence"] = card.evidence
            v2row["card_scaffold"] = card.markdown
            v2row["prose_input"] = card.prose_input
            v2row["needs_llm_slots"] = list(card.needs_llm_slots)
            plan = render_plan.get(card.card_id)
            if plan is not None:
                v2row["render_order"] = plan["render_order"]
                if plan["group_label"]:
                    v2row["group_label"] = plan["group_label"]
            if card.recall_group_key:
                v2row["recall_group_key"] = card.recall_group_key
            if card.status_hint:
                v2row["status_hint"] = card.status_hint
        out_rows.append(v2row)
        source_counts[row.get("source", "")] = source_counts.get(row.get("source", ""), 0) + 1
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION_V2,
        "handoff_id": f"routine-handoff::{run_date.isoformat()}",
        "run_date_kst": run_date.isoformat(),
        "weekday_kst": weekday_kst(run_date),  # 발행 요일 결정론 산출 — LLM 산술 금지(D-1)
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(out_rows),
        "source_counts": source_counts,
        # 수집 현황 '수집' 컬럼 결정론 산출 — LLM 재집계 금지(W1). 발행 callout 에 그대로 전사.
        "coverage_collected_md": build_coverage_collected(source_counts)["md"],
        "rows": out_rows,
    }


def build_web_brief_payload_v2(rows: list[dict[str, Any]], run_date: date,
                               window_days: int) -> dict[str, Any]:
    """빈슬롯 `grm-web-card/v1` 브리프 payload(§1-B 영구배선). 순수·결정론 — 네트워크·
    현재시각·LLM 0.

    `rows` 는 handoff v2(`build_routine_handoff_payload_v2`)와 **동일한 enriched(raw 부착)
    rows** 여야 한다 — 같은 카드 producer(`build_card_scaffold`→`merge_recall_cards`)를
    재구성하므로 두 산출의 카드 사실 셀은 byte 동일(드리프트 0). `card_scaffold.assemble_web_brief`
    가 LLM 슬롯(title_issue·summary·key_facts·implication·checks·비KO translation·tldr)을
    빈값으로 둔 브리프를 낸다 → routine 델타를 `inject_slots` 로 주입만 하면 발행(1회용 파서 제거).

    `brief_meta` = handoff 와 동일 소스: run_date·window(수집 윈도우)·intake_total(=row 수).
    `publish_date` 기본 = run_date(주차 재발행 시 사람이 커밋 단계에서 조정). tldr 은 빈슬롯([]).
    """
    start = run_date - timedelta(days=window_days)
    cards = dedupe_news_cards(
        merge_recall_cards([build_card_scaffold(row, row.get("raw")) for row in rows]))
    brief_meta = {
        "run_date_kst": run_date.isoformat(),
        "window": f"{start.isoformat()} ~ {run_date.isoformat()}",
        "publish_date": run_date.isoformat(),
        "intake_total": len(rows),
        "tldr": [],  # LLM placeholder (inject_slots 가 채움)
    }
    return assemble_web_brief(cards, brief_meta)


def web_brief_filename(run_date: date) -> str:
    """`brief_web_{YYYY_MM_DD}.json`(web/data/briefs 규약 — 날짜 구분자 '_')."""
    return f"brief_web_{run_date.isoformat().replace('-', '_')}.json"


def emit_web_brief_file(rows: list[dict[str, Any]], run_date: date, window_days: int,
                        out_dir: str) -> str:
    """빈슬롯 web brief 를 `out_dir/brief_web_{run_date}.json` 로 결정론 기록 후 경로 반환.

    실 producer 경로(`build_web_brief_payload_v2` = `assemble_web_brief`)로 산출 —
    파싱/수기 fixture 아님. data 관례(`indent=1`·`ensure_ascii=False`·LF·후행개행)로 쓴다
    (`web/render._write_json` 과 동형 → 같은 입력 byte 동일).
    """
    payload = build_web_brief_payload_v2(rows, run_date, window_days)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, web_brief_filename(run_date))
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    with open(path, "wb") as f:  # LF/UTF-8 고정 — OS 무관 결정론(Windows \r\n 차단)
        f.write(text.encode("utf-8"))
    return path


def _handoff_page_properties(payload: dict[str, Any],
                             generated_at: datetime) -> dict[str, Any]:
    run_date = payload["run_date_kst"]
    row_count = payload["row_count"]
    title = f"OPEN GRM Routine Handoff {run_date}"
    body = (
        f"New-only Routine handoff. rows={row_count}; "
        f"window={payload['window_start']}~{payload['window_end']}; "
        f"generated_at={payload['generated_at_kst']}"
    )
    return {
        PROP_NAME: {"title": _rich_text(title)},
        PROP_SOURCE: _select(SOURCE_HANDOFF),
        PROP_DOC_ID: {"rich_text": _rich_text(payload["handoff_id"])},
        PROP_DATE: {"date": {"start": run_date}},
        PROP_HEADLINE: {"rich_text": _rich_text(f"Routine New-only handoff ({row_count} rows)")},
        PROP_TYPE_CLASS: _select(TYPE_ROUTINE_HANDOFF),
        PROP_BODY: {"rich_text": _rich_text(body)},
        PROP_RUN_DATE: {"date": {"start": run_date}},
        PROP_COLLECTED_AT: _datetime_iso(generated_at),
        PROP_STATUS: _select("New"),
    }


def _handoff_blocks(payload: dict[str, Any], compact: bool = False) -> list[dict[str, Any]]:
    # v2(compact=True): sort_keys 결정론 + 공백 제거(크기 절감, §12G). v1: 기존 indent=2 유지(바이트 동일).
    if compact:
        json_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"))
    else:
        json_payload = json.dumps(payload, ensure_ascii=False, indent=2)
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich_text("GRM Routine Handoff")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(
                f"HANDOFF_ID: {payload['handoff_id']} | "
                f"SCHEMA: {payload['schema_version']} | "
                f"ROW_COUNT: {payload['row_count']}"
            )},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(
                f"WINDOW: {payload['window_start']}~{payload['window_end']} | "
                f"GENERATED_AT_KST: {payload['generated_at_kst']}"
            )},
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": _rich_text("Payload JSON")},
        },
    ]
    for chunk in chunk_text(json_payload, NOTION_CODE_BLOCK_CHUNK):
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "language": "json",
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        })
    return blocks


def notion_find_handoff_page(token: str, db_id: str,
                             handoff_id: str) -> dict[str, Any] | None:
    body = {
        "filter": {
            "property": PROP_DOC_ID,
            "rich_text": {"equals": handoff_id},
        },
        "page_size": 5,
    }
    data = notion_api_request("POST", NOTION_DB_QUERY_URL_TPL.format(db_id=db_id),
                              token, body=body)
    results = data.get("results", [])
    if not results:
        return None
    results.sort(key=lambda p: p.get("last_edited_time", ""), reverse=True)
    return results[0]


def notion_stale_prior_open_handoffs(token: str, db_id: str,
                                     keep_handoff_id: str,
                                     superseded_by: str,
                                     revert_refs: bool = False) -> int:
    """새 OPEN handoff emit 전, 직전 미소비 OPEN handoff 를 STALE 로 봉인한다(K4-1).

    Type or Class=`routine-handoff` 이고 Status=`New`(=OPEN) 인 handoff page 중
    handoff_id 가 `keep_handoff_id` 와 다른 것을 전부 찾아 Title→`STALE GRM Routine
    Handoff {날짜} (superseded by {superseded_by})`, Status→`Skipped` 로 바꾼다.
    → '항상 OPEN 1개' 불변식: 일일 emit 누적·주간 소비 오선택(6/8 근본원인) 차단.

    ⚠️ 불가침(v1, revert_refs=False 기본): handoff page **자신의** Name·Status 두
    속성만 PATCH 한다. 그 page 의 rows[] 가 가리키는 **개별 Intake row page 의
    Status 는 절대 건드리지 않는다**(handoff 의 children 은 JSON code block 일 뿐 —
    row page 가 아니다). 반환=봉인 건수.

    `revert_refs=True`(멱등성 v2, PL-10b/B1 근본해결 — K4-1 불가침의 의도적 변경):
    STALE 봉인한 handoff 의 **미발행(Status=New) row 만** `Handoff Ref` 를 비워
    다음 emit 에 재투입한다(B1 revert — 누락 0). row 의 Status 는 여기서도 불변 —
    Processed/Skipped row 는 ref 포함 일절 건드리지 않는다.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {
            "and": [
                {"property": PROP_TYPE_CLASS, "select": {"equals": TYPE_ROUTINE_HANDOFF}},
                {"property": PROP_STATUS, "select": {"equals": "New"}},
            ]
        },
        "page_size": 100,
    }
    staled = 0
    staled_ids: list[str] = []
    start_cursor: str | None = None
    for _ in range(25):  # handoff page 는 소수 — 안전 상한
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            prior_id = snap["document_id"]
            if not prior_id or prior_id == keep_handoff_id:
                continue  # 오늘 emit 본인(keep)·식별 불가 page 는 봉인 금지
            prior_date = prior_id.split("::", 1)[-1] or snap["run_date"] or "?"
            new_title = (f"STALE GRM Routine Handoff {prior_date} "
                         f"(superseded by {superseded_by})")
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=snap["page_id"]), token,
                body={"properties": {
                    PROP_NAME: {"title": _rich_text(new_title)},
                    PROP_STATUS: _select("Skipped"),
                }},
            )
            staled += 1
            staled_ids.append(prior_id)
            time.sleep(0.34)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    if staled:
        log("INFO", f"직전 미소비 OPEN handoff {staled}건 STALE 봉인 "
                    f"(keep={keep_handoff_id})")
    if revert_refs:
        for prior_id in staled_ids:
            notion_revert_refs_for_handoff(token, db_id, prior_id)
    return staled


_HANDOFF_REF_ROWS_MAX_PAGES = 10  # ref 잔존 New row 는 소수(미마감 handoff 분량) — 안전 상한


def _query_new_rows_with_ref(token: str, db_id: str,
                             ref_filter: dict[str, Any]) -> list[tuple[str, str]]:
    """Status=New ∧ ref_filter 인 Intake row 의 (page_id, handoff_ref) 목록.

    handoff page 자체(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아니므로 제외.
    handoff_ref 는 snapshot 스키마에 넣지 않고 여기서만 읽는다 — snapshot 은 v1 handoff
    payload rows 로 그대로 직렬화되므로 키 추가 = flag off 경로 바이트 변경(금지).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_STATUS, "select": {"equals": "New"}},
            ref_filter,
        ]},
        "page_size": 100,
    }
    out: list[tuple[str, str]] = []
    start_cursor: str | None = None
    for _ in range(_HANDOFF_REF_ROWS_MAX_PAGES):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            ref = _prop_rich_text(page.get("properties", {}), PROP_HANDOFF_REF)
            out.append((snap["page_id"], ref))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "Handoff Ref row 조회 페이지 상한 도달 — 일부 row 는 다음 emit 에서 처리")
    return out


def notion_revert_refs_for_handoff(token: str, db_id: str, handoff_id: str) -> int:
    """STALE handoff 의 미발행 row 재투입(B1 revert) — Ref=handoff_id ∧ Status=New
    row 의 `Handoff Ref` 를 비운다. Status 는 불변. per-row 실패는 경고 후 계속 —
    남은 ref 는 다음 emit 의 reconcile sweep 이 다시 처리한다(자기치유). 반환=비운 건수.
    """
    rows = _query_new_rows_with_ref(
        token, db_id,
        {"property": PROP_HANDOFF_REF, "rich_text": {"equals": handoff_id}})
    reverted = 0
    for page_id, _ref in rows:
        try:
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                body={"properties": {PROP_HANDOFF_REF: {"rich_text": []}}})
            reverted += 1
        except NotionHandoffError as e:
            log("WARN", f"Handoff Ref revert 실패(다음 emit 재시도) page={page_id}: "
                        f"{truncate(str(e), 120)}")
        time.sleep(0.34)
    if rows:
        log("INFO", f"STALE handoff {handoff_id} 의 미발행 row {reverted}/{len(rows)}건 "
                    f"재투입(Handoff Ref 비움)")
    return reverted


def notion_reconcile_handoff_refs(token: str, db_id: str,
                                  current_handoff_id: str) -> dict[str, int]:
    """emit 시 reconcile sweep(멱등성 v2) — `Status=New ∧ Handoff Ref 비어있지 않음`
    row 전수를 ref 가 가리키는 handoff page 상태로 마감한다. 신뢰 신호 = handoff
    page Status(발행 종료 시 Routine 의 단일 쓰기 — per-row Status 갱신보다 견고).

      - CONSUMED(Processed) → row Status→Processed (PL-10b cleanup: 발행됐으나
        per-row Status 갱신 실패/지연분 마감 — 재유입 0). ref 는 추적성 위해 유지.
        **ref=오늘(current)이라도 동일**(Codex P1: 오늘 handoff 가 이미 CONSUMED 인
        같은 날 재실행 — 잔존 New 는 발행분이므로 마감).
      - STALE(Skipped) → ref 비움 (B1 revert — 직전 revert 의 per-row 실패/크래시
        잔존분 보정; 다음 소비 쿼리에 재투입).
      - OPEN(New) ∧ ref=오늘(current) → 불변(같은 날 재-emit; 소비 쿼리 OR 절이 포함).
      - OPEN(New) ∧ ref≠오늘 → 경고만(STALE 가드가 선행 실행되므로 비정상 상태).
      - handoff page 미발견·기타 상태 → ref 비움 + 경고 (고아 ref — 재투입이 침묵
        누락보다 안전. 중복은 v16 프롬프트 PL-10b 가드가 2차 방어).

    idempotent — 같은 입력에 재적용해도 결과 동일. per-row 실패는 경고 후 계속.
    반환: {"cleaned","reverted","orphaned","kept"} 건수.
    """
    rows = _query_new_rows_with_ref(
        token, db_id,
        {"property": PROP_HANDOFF_REF, "rich_text": {"is_not_empty": True}})
    stats = {"cleaned": 0, "reverted": 0, "orphaned": 0, "kept": 0}
    handoff_status_cache: dict[str, str | None] = {}
    for page_id, ref in rows:
        if ref not in handoff_status_cache:
            handoff_page = notion_find_handoff_page(token, db_id, ref)
            handoff_status_cache[ref] = (
                _intake_page_snapshot(handoff_page)["status"] if handoff_page else None)
        handoff_status = handoff_status_cache[ref]
        try:
            if handoff_status == "Processed":
                # CONSUMED — 발행 완료 신호. row 마감(Status 지연분 cleanup).
                notion_api_request(
                    "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                    body={"properties": {PROP_STATUS: _select("Processed")}})
                stats["cleaned"] += 1
            elif handoff_status == "New":
                if ref != current_handoff_id:
                    log("WARN", f"reconcile: row {page_id} 의 ref={ref} 가 여전히 OPEN — "
                                f"STALE 가드 선행 후 비정상 상태, 이번 emit 은 보류")
                stats["kept"] += 1  # ref=오늘 ∧ OPEN = 같은 날 재-emit(정상) → 불변
            else:
                # STALE(Skipped)·미발견·기타 — 재투입(ref 비움).
                if handoff_status is None:
                    log("WARN", f"reconcile: row {page_id} 의 ref={ref} handoff page "
                                f"미발견(고아) — ref 비우고 재투입")
                    stats["orphaned"] += 1
                else:
                    stats["reverted"] += 1
                notion_api_request(
                    "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                    body={"properties": {PROP_HANDOFF_REF: {"rich_text": []}}})
        except NotionHandoffError as e:
            log("WARN", f"reconcile PATCH 실패(다음 emit 재시도) page={page_id}: "
                        f"{truncate(str(e), 120)}")
        time.sleep(0.34)
    if any(stats.values()):
        log("INFO", "Handoff Ref reconcile: "
                    f"CONSUMED 마감 {stats['cleaned']}건 · STALE 재투입 {stats['reverted']}건 · "
                    f"고아 재투입 {stats['orphaned']}건 · 유지 {stats['kept']}건")
    return stats


def notion_mark_rows_handoff_ref(token: str, rows: list[dict[str, Any]],
                                 handoff_ref: str) -> tuple[int, int]:
    """emit 표시(멱등성 v2) — handoff 에 포함된 row 에 `Handoff Ref` 를 기록한다.

    Status 는 New 유지(상태기계 §3.2). dedupe 전 전체 row 대상 — dedup 으로 payload
    에서 빠진 중복 row 도 이 handoff 가 '가져간' 것이므로 함께 표시한다(CONSUMED 시
    reconcile 이 함께 마감 — 중복 row 의 영구 New 잔존 방지). per-row 실패는 경고 후
    계속(그 row 는 ref 없음 = v1 동작 폴백 — 다음 emit 재포함, 누락 없음).
    반환=(성공, 실패) 건수.
    """
    ok = failed = 0
    for row in rows:
        page_id = row.get("page_id", "")
        if not page_id:
            failed += 1
            continue
        try:
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                body={"properties": {
                    PROP_HANDOFF_REF: {"rich_text": _rich_text(handoff_ref)},
                }})
            ok += 1
        except NotionHandoffError as e:
            failed += 1
            log("WARN", f"Handoff Ref 기록 실패(해당 row 는 v1 동작 폴백) "
                        f"page={page_id}: {truncate(str(e), 120)}")
        time.sleep(0.34)
    if failed:
        log("WARN", f"Handoff Ref 기록: 성공 {ok}건 / 실패 {failed}건 (ref={handoff_ref})")
    elif ok:
        log("INFO", f"Handoff Ref 기록 완료: {ok}건 (ref={handoff_ref})")
    return ok, failed


def notion_archive_page_children(token: str, page_id: str) -> None:
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    start_cursor: str | None = None
    archived = 0
    while True:
        req_url = url
        if start_cursor:
            req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}"
        data = notion_api_request("GET", req_url, token)
        for block in data.get("results", []):
            block_id = block.get("id")
            if not block_id:
                continue
            notion_api_request("PATCH", f"https://api.notion.com/v1/blocks/{block_id}",
                               token, body={"archived": True})
            archived += 1
            time.sleep(0.34)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    log("INFO", f"Routine handoff 기존 blocks archive 완료: {archived}개")


def notion_append_page_children(token: str, page_id: str,
                                blocks: list[dict[str, Any]]) -> None:
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    for i in range(0, len(blocks), 90):
        notion_api_request("PATCH", url, token, body={"children": blocks[i:i + 90]})
        time.sleep(0.34)


_NOTION_CHILDREN_CREATE_LIMIT = 90  # 요청당 100 한도 방어(단계 D, Codex P2)


def notion_upsert_routine_handoff(token: str, db_id: str,
                                  payload: dict[str, Any],
                                  generated_at: datetime,
                                  compact: bool = False) -> tuple[str, str]:
    """New-only handoff page 를 생성/갱신하고 (page_id, page_url) 반환.

    compact=True(v2) 면 payload JSON 을 compact 직렬화한다. children 이 한도(90)를
    넘으면 페이지 생성 후 append chunk 경로로 분할 전송(create 한 번에 100 초과 방지).
    """
    props = _handoff_page_properties(payload, generated_at)
    blocks = _handoff_blocks(payload, compact=compact)
    # K4-1: 새 OPEN 생성/갱신 전, 직전 미소비 OPEN handoff 를 STALE 봉인('항상 OPEN 1개').
    # 개별 Intake row Status 는 불변 — handoff page 자신의 Name·Status 만 바꾼다.
    notion_stale_prior_open_handoffs(
        token, db_id,
        keep_handoff_id=payload["handoff_id"],
        superseded_by=payload.get("run_date_kst") or payload["handoff_id"].split("::", 1)[-1],
    )
    existing = notion_find_handoff_page(token, db_id, payload["handoff_id"])
    if existing:
        # Codex P1 revive 가드(멱등성 v2 한정): 이미 CONSUMED(Processed)/STALE(Skipped)
        # 로 종결된 handoff page 를 재패치하면 Status 가 New 로 부활해 재소비(중복
        # 발행) 경로가 열린다. emit 진입 시 종결 확인 후 도달했으므로 여기 걸리면
        # 그 사이 Routine 이 소비한 경합 — 조용히 덮지 않고 실패로 표면화한다
        # (row ref 미기록 상태로 중단 → 다음 emit 이 자동 정상화). v1(flag off)은
        # 기존 재패치 동작 그대로(현행 운영 불변).
        if _enable_handoff_idempotency_v2():
            existing_status = _intake_page_snapshot(existing)["status"]
            if existing_status in ("Processed", "Skipped"):
                raise NotionHandoffError(
                    f"P1 revive 가드: handoff {payload['handoff_id']} 가 이미 "
                    f"'{existing_status}' 종결 — 재기록(부활) 금지. emit 중 Routine "
                    f"소비 경합 의심, 다음 emit 에서 자동 정상화됩니다.")
        page_id = existing["id"]
        notion_archive_page_children(token, page_id)
        notion_append_page_children(token, page_id, blocks)  # 이미 90 단위 분할
        notion_api_request("PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id),
                           token, body={"properties": props})
        page_url = existing.get("url", "")
        log("INFO", f"Routine handoff 갱신 완료: {page_url or page_id}")
        return page_id, page_url

    # 생성: children ≤90 이면 한 번에(v1 기존 동작 유지), >90 이면 첫 90 + 나머지 append.
    head = blocks[:_NOTION_CHILDREN_CREATE_LIMIT]
    tail = blocks[_NOTION_CHILDREN_CREATE_LIMIT:]
    body = {"parent": {"database_id": db_id}, "properties": props, "children": head}
    created = notion_api_request("POST", NOTION_PAGES_URL, token, body=body)
    page_id = created.get("id", "")
    page_url = created.get("url", "")
    if tail:
        notion_append_page_children(token, page_id, tail)
    log("INFO", f"Routine handoff 생성 완료: {page_url or page_id} (blocks={len(blocks)})")
    return page_id, page_url


def emit_routine_handoff(token: str, db_id: str, run_date: date,
                         window_days: int,
                         generated_at: datetime,
                         source_names: set[str] | None = None,
                         doc_ids: set[str] | None = None,
                         inmemory_raw: dict[str, dict[str, Any]] | None = None,
                         display_window_days: int | None = None,
                         web_brief_dir: str | None = None
                         ) -> tuple[int, str]:
    # B1 조회/표시 분리: window_days(조회 lookback, 기본 30 — 미소비 New 누락 방지
    # 안전망)와 payload 의 window_start~window_end 는 역할이 다르다. 후자는 v16
    # 프롬프트가 발행 브리프의 "검색 기간" 속성으로 그대로 렌더하므로 발행 cadence
    # (수집 윈도우, 주간 7일)를 유지해야 한다 — 프롬프트의 "지난 7일" 문구와 정합.
    # display_window_days 미지정 시 window_days 사용(기존 호출 호환).
    payload_window_days = (display_window_days if display_window_days is not None
                           else window_days)
    # 멱등성 v2(PL-10b/B1 근본해결): 소비 쿼리 **전에** ① 직전 OPEN handoff 를 STALE
    # 봉인하며 그 미발행 row 의 ref 를 비우고(B1 revert — 이번 emit 에 즉시 재투입)
    # ② reconcile sweep 으로 CONSUMED 마감/잔존 ref 를 정리한다. 순서가 뒤면 STALE
    # 된 handoff 의 row 가 하루 늦게 재투입된다. upsert 내부의 K4-1 가드는 그대로
    # 두되(여기서 이미 봉인됐으므로 no-op), v1(flag off)은 이 블록 전체를 건너뛴다.
    idem_v2 = _enable_handoff_idempotency_v2()
    handoff_id = f"routine-handoff::{run_date.isoformat()}"
    current_handoff_open = True
    if idem_v2:
        notion_stale_prior_open_handoffs(
            token, db_id, keep_handoff_id=handoff_id,
            superseded_by=run_date.isoformat(), revert_refs=True)
        # Codex P1: 오늘 handoff 의 종결 여부를 소비 쿼리 전에 확인 — 이미
        # CONSUMED(Processed)/STALE(Skipped)면 잔존 New(ref=오늘)는 reconcile 이
        # 마감/재투입하고, page 재기록·재유입·ref 기록은 전부 생략한다(아래).
        current_page = notion_find_handoff_page(token, db_id, handoff_id)
        current_status = (_intake_page_snapshot(current_page)["status"]
                          if current_page else None)
        current_handoff_open = current_status in (None, "New")
        try:
            notion_reconcile_handoff_refs(token, db_id,
                                          current_handoff_id=handoff_id)
        except NotionHandoffError as e:
            # reconcile 은 위생 단계 — 실패해도 중복/누락이 생기지 않는다(ref 가 남은
            # row 는 소비 쿼리에서 제외된 채 다음 emit 의 sweep 이 재처리). emit 계속.
            log("WARN", f"Handoff Ref reconcile 실패(다음 emit 재시도): "
                        f"{truncate(str(e), 160)}")
        if not current_handoff_open:
            # P1: 같은 날 재실행인데 오늘 handoff 가 이미 종결 — 발행 기록(payload)
            # 보존을 위해 page 를 다시 쓰지 않는다(부활 금지). 이번 실행의 신규 row 는
            # ref 미기록(=비어있음)으로 남아 다음 emit 의 handoff 에 정상 합류한다.
            # (여기서 ref 를 기록하면 다음 reconcile 이 미발행분을 CONSUMED 마감해
            # 침묵 누락이 되므로 기록하지 않는 것이 정확하다.)
            log("INFO", f"오늘 handoff({handoff_id})가 이미 '{current_status}' 종결 — "
                        f"재기록/재유입 없이 종료(신규 row 는 다음 emit 대기)")
            return 0, (current_page or {}).get("url", "")
    rows = notion_query_new_intake_rows(token, db_id, run_date, window_days,
                                        source_names=source_names,
                                        doc_ids=doc_ids,
                                        current_handoff_id=(handoff_id if idem_v2
                                                            else None),
                                        current_handoff_open=current_handoff_open)
    if _enable_handoff_v2():
        # K2-prep: dedupe → 하이브리드 raw 부착(메모리 우선) → scaffold v2 payload.
        # inmemory_raw 는 main() 가 당일 수집 IntakeItem.raw_payload 로 전달(K3 G2 와이어링).
        # 당일분은 메모리 적중(fetch 0), 과거 누적 New row 만 page children fetch 폴백.
        enriched, _stats = enrich_rows_with_raw(token, rows, inmemory_raw=inmemory_raw)
        payload = build_routine_handoff_payload_v2(enriched, run_date,
                                                   payload_window_days, generated_at)
        _pid, page_url = notion_upsert_routine_handoff(token, db_id, payload,
                                                       generated_at, compact=True)
        log("INFO", f"Routine handoff v2 생성(ENABLE_HANDOFF_V2): rows={payload['row_count']}")
        # §1-B 영구배선: raw 가 살아있는 이 지점(enriched)에서 빈슬롯 web brief 를 결정론
        # 산출한다(handoff 와 동일 cards·소스). 비파괴·비차단 — 실패해도 handoff/수집은 계속.
        if web_brief_dir:
            try:
                web_path = emit_web_brief_file(enriched, run_date,
                                               payload_window_days, web_brief_dir)
                log("INFO", f"빈슬롯 web brief 산출(§1-B): {web_path}")
            except Exception as e:  # noqa: BLE001 — web brief 실패가 수집을 죽이면 안 됨
                log("WARN", f"빈슬롯 web brief 산출 실패(handoff 계속): "
                            f"{truncate(str(e), 160)}")
    else:
        # 기존 v1 경로 — scheduled 운영 기본. 바이트 동일 보장(변경 없음).
        payload = build_routine_handoff_payload(rows, run_date,
                                                payload_window_days, generated_at)
        _pid, page_url = notion_upsert_routine_handoff(token, db_id, payload, generated_at)
    if idem_v2:
        # emit 표시는 handoff page 확정(upsert 성공) **후** — page 없는 ref 가 생기지
        # 않게 한다. 대상은 dedupe 전 전체 rows(중복 row 도 이 handoff 가 가져감).
        notion_mark_rows_handoff_ref(token, rows, handoff_id)
    return payload["row_count"], page_url
