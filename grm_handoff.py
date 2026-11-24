#!/usr/bin/env python3
"""Build routine handoff payloads and web scaffolds from collected documents."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, TYPE_CHECKING

from grm_common import (
    SOURCE_ECA,
    SOURCE_EMA,
    SOURCE_EU_GMP_NCR,
    SOURCE_FDA_483,
    SOURCE_FDA_WL,
    SOURCE_FR,
    SOURCE_HC,
    SOURCE_ICH,
    SOURCE_ISPE,
    SOURCE_MFDS,
    SOURCE_MHRA,
    SOURCE_MHRA_GMP_NCR,
    SOURCE_PICS,
    SOURCE_RECALL,
    SOURCE_WHO,
    _env_int,
    env_flag,
    log,
)
from card_scaffold import (
    assemble_web_brief,
    build_card_scaffold,
    compute_render_plan,
    dedupe_news_cards,
    merge_recall_cards,
)


if TYPE_CHECKING:
    from collect_intake import IntakeItem


TYPE_ROUTINE_HANDOFF = "routine-handoff"


HANDOFF_SCHEMA_VERSION = "grm-routine-handoff/v1"


HANDOFF_SCHEMA_VERSION_V2 = "grm-routine-handoff/v2"  # K2 단계 D (additive)


def handoff_id_for(run_date: "date | str") -> str:
    """handoff page 의 `Document ID` 정본 산식 — `routine-handoff::{run_date}`.

    ★[2026-08-17] 종전엔 이 f-string 이 payload 빌더 두 곳과 emit 에 각각 박혀 있었다.
    쓰는 쪽만 셋이라 사본이 어긋나도 티가 안 났는데, **읽는 쪽**(`delta_bridge` 가 그 주
    handoff 에서 `deep_analysis_input.body_full` 을 되찾아 온다)이 생기면서 얘기가 달라졌다 —
    한쪽 산식만 바뀌면 조회가 예외 없이 **0건**이 되고, 브릿지는 "handoff 없음"으로 정상
    degrade 해 버려 아무 소리도 나지 않는다. 산식을 한 곳으로 모아 그 표류를 구조적으로 막는다.
    """
    day = run_date.isoformat() if isinstance(run_date, date) else str(run_date)
    return f"{TYPE_ROUTINE_HANDOFF}::{day}"


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
    """Decode a historical intake page in the original handoff row format."""
    props = page.get("properties", {})
    return {
        "page_id": page.get("id", ""),
        "page_url": page.get("url", ""),
        "title": _prop_title(props, "Name"),
        "source": _prop_select(props, "Source"),
        "document_id": _prop_rich_text(props, "Document ID"),
        "date": _prop_date(props, "Date"),
        "headline": _prop_rich_text(props, "Headline"),
        "official_url": _prop_url(props, "Official URL"),
        "source_url": _prop_url(props, "Source URL"),
        "type_or_class": _prop_select(props, "Type or Class"),
        "firm": _prop_rich_text(props, "Firm"),
        "body": _prop_rich_text(props, "Body"),
        "distribution": _prop_rich_text(props, "Distribution"),
        "comments_close": _prop_date(props, "Comments Close"),
        "run_date": _prop_date(props, "Run Date (KST)"),
        "collected_at": _prop_date(props, "Collected At"),
        "api_query": _prop_url(props, "API Query"),
        "search_query": _prop_rich_text(props, "Search Query"),
        "raw_excerpt": _prop_rich_text(props, "Raw Excerpt"),
        "qa_relevance": _prop_select(props, "QA Relevance"),
        "osd_relevance": _prop_select(props, "OSD Relevance"),
        "modality": _prop_select(props, "Modality"),
        "source_type": _prop_select(props, "Source Type"),
        "signal_tier": _prop_select(props, "Signal Tier"),
        "evidence_candidate": _prop_select(props, "Evidence Candidate"),
        "language": _prop_select(props, "Language"),
        "region_jurisdiction": _prop_select(props, "Region/Jurisdiction"),
        "site_country": _prop_rich_text(props, "Site Country"),
        "status": _prop_select(props, "Status"),
    }


_DEFAULT_HANDOFF_WINDOW_DAYS = 30


def resolve_handoff_window_days(cli_value: int | None) -> int:
    """handoff 조회 윈도우 결정 — CLI(--handoff-window-days) > GRM_HANDOFF_WINDOW_DAYS > 30."""
    if cli_value:
        return cli_value
    return _env_int("GRM_HANDOFF_WINDOW_DAYS", _DEFAULT_HANDOFF_WINDOW_DAYS)


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


def enrich_rows_with_raw(
    rows: list[dict[str, Any]], raw_by_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the stored raw payload to each deduplicated queue row."""
    enriched = _dedupe_latest_rows(rows)
    for row in enriched:
        key = f"{row.get('source', '')}::{row.get('document_id', '')}"
        raw = raw_by_key.get(key)
        row["raw"] = raw
        row["raw_fetch_ok"] = raw is not None
        if raw is None:
            row["evidence_hint"] = "B"
            row["status_hint"] = "Error"
            log("WARN", f"Missing stored raw payload for {key}")
    return enriched


def weekday_kst(run_date: date) -> str:
    """run_date(KST 달력일)의 한국어 요일 문자열(예: '수요일'). handoff weekday_kst 슬롯용."""
    return _KO_WEEKDAYS_FULL[run_date.weekday()]


# ★[2026-08-12] known 소스는 **0건이어도 항상 표시**되고(조용한 주 가시화), 미등록 소스는
# count>0 일 때만 끝에 덧붙는다(build_coverage_collected 참조). 즉 여기 빠진 소스는
# **0이 된 주에 행 자체가 사라진다** — "0"으로 남지 않으니 사람이 사라진 걸 못 알아챈다.
# 그런데 카드 수 1위인 FDA 483 을 포함해 나중에 붙인 소스 5종이 전부 빠져 있었다(손목록이
# 낡은 전형). 이제 수집 토큰 레지스트리(_SOURCE_TOKEN_TO_NOTION) 전수를 담고,
# 테스트가 두 목록의 일치를 잠근다.
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
    (SOURCE_FDA_483, "FDA 483"),
    (SOURCE_ISPE, "ISPE"),
    (SOURCE_EU_GMP_NCR, "EU NCR"),
    (SOURCE_MHRA_GMP_NCR, "UK NCR"),
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


# 무음 감시가 잡은 소스의 coverage 표식. 발행 줄에 그대로 나가므로 `brief_lint` 의 파서
# (`_COVERAGE_ITEM_RE`)와 짝이다 — 여기를 바꾸면 그쪽도 같이 바꿔야 한다.
COVERAGE_SILENT_MARK = "(점검필요)"


def build_coverage_collected(source_counts: dict[str, int],
                             silent_sources: Iterable[str] = ()) -> dict[str, Any]:
    """'수집' 컬럼(소스별 수집 건수 + 총계)을 결정론 산출한다(W1).

    반환 {"total": int, "items": [{"label","source","count","silent"}...], "md": str}:
    - known 소스(COVERAGE_SOURCE_LABELS)는 고정 순서로 전부 포함(0건도 — '조용한 주' 가시화).
    - 라벨 미정의 소스(예: FDA 483)는 count>0 일 때만 원 이름으로 끝에 덧붙인다(조용한 유실 금지).
    - total = 모든 source_counts 합(= handoff row_count, 병합 멤버 포함).
    - md = 발행 callout 의 수집 세그먼트: "Intake row {total}건 ({label} {n} · ...)".
    - `silent_sources`(무음 감시 `source_silence.evaluate_silence` 가 잡은 Notion Source 값)에
      든 소스는 `{label} {n}(점검필요)` 로 찍고 item 에 `silent=True` 를 단다 — 2026-09-14호가
      6개 소스 정지를 한산한 주와 구별되지 않는 `0` 으로 발행한 뒤 신설. 빈 집합이면 md 는
      종전과 바이트 동일(골든 불변).
    LLM 은 md 를 그대로 삽입하고 병합·WebSearch·유효항목·Evidence·미확인 등 발행측 값만 채운다.
    """
    counts = {k: int(v) for k, v in (source_counts or {}).items()}
    silent = set(silent_sources or ())
    items: list[dict[str, Any]] = []
    for source, label in COVERAGE_SOURCE_LABELS:
        items.append({"label": label, "source": source, "count": counts.get(source, 0),
                      "silent": source in silent})
    for source in sorted(k for k in counts if k and k not in _COVERAGE_KNOWN_SOURCES):
        if counts[source] > 0:
            items.append({"label": source, "source": source, "count": counts[source],
                          "silent": source in silent})
    total = sum(counts.values())
    seg = " · ".join(
        f"{it['label']} {it['count']}{COVERAGE_SILENT_MARK if it['silent'] else ''}"
        for it in items)
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
        "handoff_id": handoff_id_for(run_date),
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


# v2 row 에 보존할 v1 호환 필드(whitelist) — **`_intake_page_snapshot()` 스키마에서 파생**한다.
# blacklist 대신 whitelist 인 이유(Codex P2): raw·Stage B 부착 bookkeeping(raw_fetch_ok·
# raw_source·status_hint·evidence_hint) 같은 내부/대형 필드가 payload 로 새지 않도록 보장.
#
# ★[2026-08-12] 종전엔 같은 29키를 **손으로 다시 적어** 뒀다. 원천(`_intake_page_snapshot`)과
# 우연히 일치하고 있었을 뿐이고, 어긋나도 아무도 몰랐다 — Notion 속성을 하나 추가해
# 스냅샷에만 넣으면 **v2 payload 에서 그 필드가 경고 0건·테스트 0건으로 조용히 탈락**한다
# (v1 은 row 를 통째로 넘기므로 v1 에서는 보이고 v2 에서만 사라져 원인 추적이 어렵다).
# 게다가 이 상수의 설명 주석은 모듈 분리 때 `collect_intake.py` 에 고아로 남아, 정의 지점엔
# 한 줄도 없었다. 이제 스냅샷 스키마를 그대로 파생시킨다 — 새 속성은 자동으로 실린다.
# (빈 dict 로 호출해 **키 집합만** 얻는다. 스냅샷은 순수 dict 연산이라 부작용이 없다.)
_HANDOFF_V2_ROW_KEEP = tuple(_intake_page_snapshot({}))


def build_routine_handoff_payload_v2(rows: list[dict[str, Any]], run_date: date,
                                     window_days: int,
                                     generated_at: datetime,
                                     silent_sources: Iterable[str] = ()) -> dict[str, Any]:
    """handoff v2(additive) payload. 순수 함수 — 네트워크 없음(scaffold 조립만).

    `silent_sources` = 무음 감시가 잡은 Notion Source 값(2026-09-14 §3). coverage 줄에
    `{label} 0(점검필요)` 로 찍히고, 비어 있지 않을 때만 `coverage_silent_sources` 키가
    additive 로 붙는다(빈 집합이면 payload 바이트 불변 — 골든 보존).

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
            # [키 회귀 근원 수리 2026-07-27] `card_id` 는 `source::document_id` 인데 발행
            # 델타의 카드 키는 **bare document_id** 여야 한다. 이름이 `card_id` 라 Routine 이
            # 눈앞의 그 값을 델타 키로 쓰는 독법이 자연스럽고(프롬프트의 `card.id` 도 모호),
            # 실제로 2026-07-13·07-27 두 번 그렇게 예치돼 발행이 전건 거부됐다.
            # 모호하지 않은 이름으로 **같은 값을 한 번 더** 노출한다 — 프롬프트가 이 필드명을
            # 그대로 지시하면 해석의 여지가 없다(값 중복 비용 = row 당 수십 바이트).
            v2row["web_card_id"] = row.get("document_id", "")
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
            # [심층분석 fan-out 입력 2026-07-27] 대상 카드에만 3키 추가(비대상은 빈 dict =
            # 완전 무영향). 방출 정본은 `CardScaffold.deep_fields()` 하나 — `to_dict()` 와
            # 이 빌더가 **같은 함수**를 부르므로 한쪽만 갱신되는 표류가 불가능하다.
            # 이 줄이 없던 동안 클라우드 Routine 은 deep 대상을 영영 0건으로 봤다(§B [2단계]가
            # `deep_analysis_ready` 를 찾는데 handoff 에 그 키가 아예 없었다).
            v2row.update(card.deep_fields())
            # [NCR 국문 병기 2026-07-27] 번역 입력도 같은 단일 방출점을 통해 싣는다 —
            # 대상(EU/MHRA GMP 비준수) 카드에만 3키 추가, 비대상은 빈 dict = 완전 무영향.
            v2row.update(card.translation_fields())
        out_rows.append(v2row)
        source_counts[row.get("source", "")] = source_counts.get(row.get("source", ""), 0) + 1
    silent = sorted(set(silent_sources or ()))
    payload: dict[str, Any] = {
        "schema_version": HANDOFF_SCHEMA_VERSION_V2,
        "handoff_id": handoff_id_for(run_date),
        "run_date_kst": run_date.isoformat(),
        "weekday_kst": weekday_kst(run_date),  # 발행 요일 결정론 산출 — LLM 산술 금지(D-1)
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(out_rows),
        "source_counts": source_counts,
        # 수집 현황 '수집' 컬럼 결정론 산출 — LLM 재집계 금지(W1). 발행 callout 에 그대로 전사.
        # 무음 소스는 `0(점검필요)` 로 찍힌다 — 한산한 주와 구별(§3 2026-09-14).
        "coverage_collected_md": build_coverage_collected(source_counts, silent)["md"],
        "rows": out_rows,
    }
    if silent:
        payload["coverage_silent_sources"] = silent
    return payload


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


PUBLISH_WEEKDAY = 0


def unconsumed_publish_handoffs(sealed_dates: Iterable[str],
                                *, publish_weekday: int = PUBLISH_WEEKDAY) -> list[str]:
    """STALE 봉인된 handoff 날짜 중 **발행일(월)** 것만 — 그 주 Routine 이 마감을 안 했다.

    배경(2026-09-14 실측): 그 주 브리프는 정상 발행됐는데(델타 커밋 + web-publish 성공)
    handoff page 는 CONSUMED 가 아니라 `STALE ... (superseded by 2026-09-15)` / Skipped
    로 끝나 있었다. 다른 월요일(08-17·08-24·08-31·09-07·09-21)은 전부 CONSUMED 였으니
    **"월요일 handoff 가 STALE 됐다" 는 것 자체가 정확한 고장 신호**다.

    왜 위험한가: `notion_reconcile_handoff_refs` 에서 CONSUMED 는 라우틴이 못 찍은 row 를
    **마감**하는 경로인데, STALE 은 반대로 미발행 row 의 ref 를 비워 **재투입**한다.
    2026-09-14 에는 재투입될 New row 가 없어 피해가 0 이었지만(카드 중복 0건·잔존 New 0건),
    구조적으로는 다음 주 중복 카드가 되는 자리다. 그리고 그때 **아무것도 경보하지 않았다** —
    publish 워치독은 "델타 파일이 있나"만 보지 "handoff 가 닫혔나"는 보지 않는다.

    경고이지 실패가 아니다: 이미 지나간 주의 기록 문제라 이번 수집·발행을 막을 이유가 없다.

    날짜 파싱 불가(`"?"` 등)는 조용히 버린다 — 판정 근거가 없는 것을 고장이라 부르지 않는다.
    """
    out: list[str] = []
    for raw in sealed_dates:
        try:
            when = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if when.weekday() == publish_weekday:
            out.append(when.isoformat())
    return sorted(set(out))


# ── 발행일(월) handoff 동결 (2026-11-16) ─────────────────────────────────────────
# 11-16 수집이 07:52 KST 에야 떠 편집팀이 handoff 를 못 찾았다. 그래서 월요일 수집을 여러 번(자정 직후
# 추가 크론·PC dispatch) 돌리게 했는데, 그러면 **같은 날 재실행이 handoff 를 다시 쓰는 일**이 잦아진다.
# 편집팀은 07:20 부터 handoff 를 읽고(데스크는 지문 `handoff_ref` 를 적는다) 09:30 까지 그 내용으로 일한다 —
# 그 사이 페이지가 다시 쓰이면 기자·교열이 데스크와 다른 handoff 를 보고 보류된다.
# 규칙: 발행일 07:00 KST 이후, 그날 handoff 페이지가 **이미 있으면**(OPEN·CONSUMED·STALE 무엇이든)
# 다시 쓰지 않는다 — 앞선 STALE 봉인·reconcile·행 조회도 하지 않고 바로 끝낸다(이번 실행의 새 행은
# ref 없는 New 로 남아 화요일 handoff 에 들어간다). 페이지가 아직 없으면 평소대로 만든다(늦게 온 첫 수집).
# 07:00 전의 재실행은 종전대로 갱신한다(483 회수 뒤 재생성 `grm-fda-coverage-gate.yml` 이 이 길이다).
# DB handoff 의 동결(`grm_queue.record_handoff`)과 같은 시각·같은 스위치다:
# 07:00 뒤에 일부러 다시 만들 때만 저장소 변수 GRM_QUEUE_HANDOFF_REFRESH=true → 수집 재실행 → 변수 끄기.
PUBLISH_DAY_FREEZE_HOUR_KST = 7
HANDOFF_REFRESH_ENV = "GRM_QUEUE_HANDOFF_REFRESH"
_KST = timezone(timedelta(hours=9))


def publish_day_handoff_frozen(run_date: date, now: datetime | None = None) -> bool:
    """이 실행이 발행일(월) handoff 동결 시각(07:00 KST)을 지났나 — 지났으면 기존 페이지를 다시 쓰지 않는다.

    `now` 는 **쓰기 직전의 실제 시각**이다(실행 시작 시각이 아니다 — 06:50 에 시작해 07:10 에 쓰는 실행도 동결).
    naive datetime 은 KST 로 본다. 스위치 GRM_QUEUE_HANDOFF_REFRESH=true 면 동결하지 않는다.
    """
    if run_date.weekday() != PUBLISH_WEEKDAY:
        return False
    if env_flag(HANDOFF_REFRESH_ENV):
        return False
    now = now or datetime.now(_KST)
    now_k = now.replace(tzinfo=_KST) if now.tzinfo is None else now.astimezone(_KST)
    days = (now_k.date() - run_date).days
    if days == 1:
        # 월요일 밤 늦게 시작해 자정을 넘겨 쓰는 실행 — 이미 발행이 끝난 호다.
        return True
    return days == 0 and now_k.hour >= PUBLISH_DAY_FREEZE_HOUR_KST
