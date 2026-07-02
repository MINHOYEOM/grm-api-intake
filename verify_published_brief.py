#!/usr/bin/env python3
"""GRM 발행 후 출처 링크 근거(provenance) 탐지(detective) — W1 belt-and-suspenders.

발행(주간 Routine)은 LLM+MCP 라 결정론 **차단**은 발행 직전 예방 게이트
(`brief_lint.run_publish_gate` / Publish Lint 17)에 의존한다. 이 모듈은 그 게이트를
**발행 후 독립적으로 재실행**하는 2차 방어선이다 — 최신 Weekly Brief 페이지와 그 주
handoff 를 Notion 에서 직접 받아 (1) `lint_link_provenance`(출처 근거)와 (2)
`lint_publish_structure`(구조: PL1 잔존토큰·PL3/16 금지문법·PL14 요일=날짜)를 둘 다
재판정하고, FAIL(MFDS 날조 시그니처 `brd/*/view.do?seq=` 또는 요일 불일치 등)이면 운영
경고 JSON 을 낸다. CI(`grm-brief-audit.yml`)가 이 JSON 을 기존 `GRM Intake 운영 경고`
Issue 로 띄운다. **발행 주체 CC Routine 은 커넥터가 Notion 뿐이라 인-루틴 `--structure`
게이트를 코드로 못 돌린다 → 요일류 결함의 유일 결정론 방어선이 이 발행 후 탐지다(W2).**

**과알림 0 원칙:**
- 알림은 **FAIL 만** 트리거한다(WARN·미확인은 정보로만 본문에 싣고 알림 트리거 아님).
- MFDS 미근거 링크 = FAIL(네트워크 없이 결정론 — 오탐 0, 실제 W24 사고 클래스).
- 비-MFDS 미근거 링크는 live verify 가 **명확히 나쁨**(404·오류셸·기대어 부재)일 때만 FAIL
  로 승격하고, 일시 네트워크 실패(unknown)는 알리지 않는다.
- 토큰·페이지·handoff 부재 또는 fetch 실패는 `ok:true`(건너뜀)로 처리 — false-red 금지.

순수 코어(블록 URL 추출·분류·Issue JSON)는 네트워크 없이 단위테스트된다. Notion I/O 만
lazy import(`collect_intake` 의 검증된 `notion_api_request` 재사용).
"""
from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Iterable

import brief_lint as bl
from grm_common import env_flag

# Weekly Brief(발행) DB — v16 프롬프트 [발송] 의 ID. env 로 override 가능.
DEFAULT_WEEKLY_BRIEF_DB_ID = "3653142f-dc11-8049-806d-e0a779cafd90"
# Intake/handoff DB — handoff page(Source=GRM Handoff)가 사는 곳(= NOTION_DATABASE_ID).
DEFAULT_INTAKE_DB_ID = "7784c71fb7b343749b2bee5d04db7926"

DEFAULT_AUDIT_JSON = "grm-brief-audit.json"


# ─────────────────────────────────────────────────────────────────────────────
# 순수 코어 — Notion 블록 URL 추출 (네트워크 없음, 단위테스트 대상)
# ─────────────────────────────────────────────────────────────────────────────
def iter_rich_text_urls(rich_text: Iterable[dict[str, Any]]) -> list[str]:
    """Notion rich_text 배열에서 링크 URL 을 모은다(`href` + `text.link.url`)."""
    out: list[str] = []
    for rt in rich_text or []:
        if not isinstance(rt, dict):
            continue
        href = rt.get("href")
        if href:
            out.append(href)
        link = (rt.get("text") or {}).get("link") if isinstance(rt.get("text"), dict) else None
        if isinstance(link, dict) and link.get("url"):
            out.append(link["url"])
    return out


# rich_text 를 담을 수 있는 블록 타입 payload 키들(callout·문단·제목·인용·리스트·표 셀 등).
_RICH_TEXT_KEYS = (
    "paragraph", "heading_1", "heading_2", "heading_3", "callout", "quote",
    "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "code",
)


def extract_urls_from_blocks(blocks: Iterable[dict[str, Any]]) -> list[str]:
    """Notion 블록 리스트(인라인 children 포함)에서 모든 링크 URL 을 추출.

    각 블록의 rich_text(문단·callout·표 셀 등) 링크 + 인라인 `children` 재귀.
    (network fetch 로 받은 children 도 같은 함수로 처리 — fetch 계층이 children 을
    블록에 인라인으로 붙여 넘긴다.)
    """
    urls: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        payload = block.get(btype, {}) if btype else {}
        if isinstance(payload, dict):
            if isinstance(payload.get("rich_text"), list):
                urls.extend(iter_rich_text_urls(payload["rich_text"]))
            # table_row: cells = [[rich_text...], ...]
            if isinstance(payload.get("cells"), list):
                for cell in payload["cells"]:
                    if isinstance(cell, list):
                        urls.extend(iter_rich_text_urls(cell))
            # 인라인으로 부착된 children 재귀(fetch 계층이 붙임).
            if isinstance(payload.get("children"), list):
                urls.extend(extract_urls_from_blocks(payload["children"]))
        if isinstance(block.get("children"), list):
            urls.extend(extract_urls_from_blocks(block["children"]))
    # 순서 보존 dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _rich_text_plain(rich_text: Iterable[dict[str, Any]]) -> str:
    """Notion rich_text 배열을 평문으로(annotation 무시 — `plain_text`∨`text.content`)."""
    out: list[str] = []
    for rt in rich_text or []:
        if not isinstance(rt, dict):
            continue
        t = rt.get("plain_text")
        if t is None and isinstance(rt.get("text"), dict):
            t = rt["text"].get("content")
        if t:
            out.append(t)
    return "".join(out)


def _collect_block_text(blocks: Iterable[dict[str, Any]], lines: list[str]) -> None:
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        payload = block.get(btype, {}) if btype else {}
        if isinstance(payload, dict):
            if isinstance(payload.get("rich_text"), list):
                txt = _rich_text_plain(payload["rich_text"])
                if txt:
                    lines.append(txt)
            if isinstance(payload.get("cells"), list):   # table_row
                for cell in payload["cells"]:
                    if isinstance(cell, list):
                        txt = _rich_text_plain(cell)
                        if txt:
                            lines.append(txt)
            if isinstance(payload.get("children"), list):
                _collect_block_text(payload["children"], lines)
        if isinstance(block.get("children"), list):
            _collect_block_text(block["children"], lines)


def extract_text_from_blocks(blocks: Iterable[dict[str, Any]]) -> str:
    """Notion 블록의 사람이 읽는 평문을 블록당 한 줄로 이어붙인다(구조 lint 입력용).

    `brief_lint.lint_publish_structure` 에 먹여 PL14(요일=날짜)·PL1(잔존 `{{`)·PL3/16(리터럴
    `<toggle>`·`[toc]` 등)을 발행 후 결정론 판정한다. 주의: Notion 은 굵게/제목을 마크다운
    리터럴(`**`·`###`)이 아니라 annotation 으로 저장하므로 추출 평문엔 `###`/`**` 가 없다 →
    PL10(카드 제목 `### …**이슈**`)은 이 경로에서 매칭되지 않는다(발행 전 `--structure`
    마크다운 게이트가 담당). 비괄호 푸터형 `발행일: YYYY-MM-DD 화요일` 의 요일은 평문에
    그대로 드러나 PL14 로 검출된다(06-17 dry-run D-1).
    """
    lines: list[str] = []
    _collect_block_text(blocks, lines)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 순수 코어 — 분류(과알림 0) + Issue JSON
# ─────────────────────────────────────────────────────────────────────────────
# verdict 콜백 반환값: 비-MFDS 미근거 링크의 live verify 판정.
VERDICT_OK = "ok"          # 정상(통과) — 알림 아님
VERDICT_BAD = "bad"        # 명확히 나쁨(404·오류셸·기대어 부재) — FAIL 승격
VERDICT_UNKNOWN = "unknown"  # 일시 네트워크 실패 등 — 미확인(과알림 0: 알림 아님)


# BAD 로 단정할 status — "확실히 없어진" 것만. 403/401/429/5xx/timeout 은 WAF·일시장애일
# 수 있어 BAD 로 보지 않는다(과알림 0). 감사 결과 FDA 등은 비브라우저 fetch 를 WAF 로
# 차단(UNVERIFIABLE)하므로, 살아있는 정당 링크가 CI 에서 403/abuse 로 보일 수 있다 →
# 그걸 FAIL 로 올리면 오탐. "지어낸 URL" 차단의 본진은 발행 전 예방 게이트(allowed_fetched).
_GONE_STATUSES = (404, 410)


def definitive_verdict(url: str, **kwargs: Any) -> str:
    """`verify_url_live` 결과를 ok/bad/unknown 으로 보수적으로 환원(과알림 0).

    - ok → OK.
    - status 404/410(확실히 없어짐) 또는 200+오류셸(nedrug) → BAD(승격).
    - 그 외(403/401/429/5xx·timeout·길이부족·WAF abuse) → UNKNOWN(알림 아님 — 일시·WAF
      차단일 수 있어 정당 링크 오탐 방지). 지어낸 URL 차단은 예방 게이트(fetch 화이트리스트)가
      1차로 막는다.
    """
    r = bl.verify_url_live(url, **kwargs)
    if r.get("ok"):
        return VERDICT_OK
    status = r.get("status") or 0
    if status in _GONE_STATUSES:
        return VERDICT_BAD
    if status == 200 and r.get("is_error_page"):  # nedrug 오류셸(방어적; MFDS 는 별경로)
        return VERDICT_BAD
    return VERDICT_UNKNOWN


def classify(handoff_rows: list[dict[str, Any]],
             published_urls: list[str],
             verdict: "Any | None" = None,
             *,
             allowed_fetched: Iterable[str] = (),
             published_text: "str | None" = None) -> tuple[list[bl.LintFinding], list[bl.LintFinding]]:
    """(alert_findings, info_findings) 반환.

    scaffold footer 누락은 도메인·live 상태와 무관하게 alert 다. 단 `published_text` 가
    주어지면 **실제로 렌더된 카드**(document_id 가 발행본에 존재)에 한해 검사해 Tier1 Skipped
    /보류 row 의 footer 오탐을 차단한다(과알림 0). 그 밖의 미근거 링크는 ALL_DOMAINS 로 전부
    검사하되, 비-MFDS 검색 카드 후보는 `allowed_fetched` 이거나 `verdict(url)` 가 BAD 일 때만
    alert 로 승격한다.
    """
    allowed = bl.collect_allowed_urls(handoff_rows)
    base = bl.lint_urls(published_urls, allowed, policy=bl.POLICY_ALL_DOMAINS,
                        allowed_fetched=allowed_fetched)
    alerts: list[bl.LintFinding] = bl.lint_scaffold_footer_integrity(
        handoff_rows, published_urls, published_text=published_text)
    info: list[bl.LintFinding] = []
    for f in base:
        if f.code == "L17-MFDS-PROVENANCE":    # MFDS 미근거 — 결정론 alert
            alerts.append(f)
            continue
        if f.severity != bl.SEV_FAIL:
            info.append(f)
            continue
        # 비-MFDS 미근거(ALL_DOMAINS FAIL 후보): 검색 카드일 수 있어 live verdict 로 보수 환원.
        v = verdict(f.url) if verdict is not None else VERDICT_UNKNOWN
        if v == VERDICT_BAD:
            alerts.append(bl.LintFinding(
                bl.SEV_FAIL, "L17-UNGROUNDED", f.url,
                "handoff 근거 없는 외부 링크가 live verify 에서 명확히 나쁨(404·오류셸·"
                "기대어 부재) — 지어낸/죽은 링크 의심."))
        else:
            info.append(bl.LintFinding(
                bl.SEV_WARN, "L17-UNVERIFIED", f.url,
                "handoff 근거 없는 외부 링크이나 live verify 가 OK/UNKNOWN 이라 알림으로 "
                "승격하지 않음(검색 카드·WAF 가능성)."))
    return alerts, info


def _enable_brief_autofix() -> bool:
    """발행 후 URL self-heal 옵션. 기본 off, 사람 게이트 후에만 활성화한다."""
    return env_flag("ENABLE_BRIEF_AUTOFIX")


def _url_tokens(url: str) -> set[str]:
    import re
    parts = [p.lower() for p in re.split(r"[^A-Za-z0-9]+", url) if len(p) >= 3]
    return set(parts)


def _same_url_family(expected: str, candidate: str) -> bool:
    """self-heal 후보 매칭: 숫자 식별자 공유 또는 slug 토큰 대부분 공유일 때만."""
    import re
    nums_expected = set(re.findall(r"\d{5,}", expected))
    nums_candidate = set(re.findall(r"\d{5,}", candidate))
    if nums_expected and nums_candidate and nums_expected & nums_candidate:
        return True
    e_tokens = _url_tokens(expected)
    c_tokens = _url_tokens(candidate)
    if not e_tokens or not c_tokens:
        return False
    return len(e_tokens & c_tokens) / max(len(e_tokens), len(c_tokens)) >= 0.6


def build_autofix_replacements(handoff_rows: list[dict[str, Any]],
                               published_urls: list[str],
                               *,
                               published_text: "str | None" = None) -> dict[str, str]:
    """미근거 URL→scaffold URL 자동치환 후보를 보수적으로 만든다(모호하면 제외).

    `published_text` 로 렌더된 카드만 대상(classify 와 동일 스코프 — 생략 카드 footer 는
    self-heal 대상이 아니다)."""
    missing = [f.url for f in bl.lint_scaffold_footer_integrity(
        handoff_rows, published_urls, published_text=published_text)]
    if not missing:
        return {}
    allowed = bl.collect_allowed_urls(handoff_rows)
    ungrounded = [f.url for f in bl.lint_urls(
        published_urls, allowed, policy=bl.POLICY_ALL_DOMAINS)]
    replacements: dict[str, str] = {}
    used_candidates: set[str] = set()
    for expected in missing:
        candidates = [u for u in ungrounded
                      if u not in used_candidates and _same_url_family(expected, u)]
        if len(candidates) == 1:
            candidate = candidates[0]
            replacements[candidate] = expected
            used_candidates.add(candidate)
    return replacements


def _patchable_rich_text(rt: dict[str, Any], replacements: dict[str, str]) -> tuple[dict[str, Any], bool]:
    out: dict[str, Any] = {"type": rt.get("type", "text")}
    changed = False
    if out["type"] == "text":
        text = copy.deepcopy(rt.get("text") or {})
        text.setdefault("content", rt.get("plain_text", ""))
        link = text.get("link")
        if isinstance(link, dict) and link.get("url") in replacements:
            link["url"] = replacements[link["url"]]
            changed = True
        out["text"] = text
    else:
        payload = rt.get(out["type"])
        if isinstance(payload, dict):
            out[out["type"]] = copy.deepcopy(payload)
    if "annotations" in rt:
        out["annotations"] = copy.deepcopy(rt["annotations"])
    return out, changed


def apply_autofix_replacements(token: str, blocks: list[dict[str, Any]],
                               replacements: dict[str, str]) -> int:
    """Notion rich_text 링크를 replacement map 으로 PATCH 한다. 호출자는 flag 를 확인한다."""
    if not replacements:
        return 0
    ci = _ci()
    patched = 0

    def _walk(block_list: Iterable[dict[str, Any]]) -> None:
        nonlocal patched
        for block in block_list or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            payload = block.get(btype, {}) if btype else {}
            if isinstance(payload, dict) and isinstance(payload.get("rich_text"), list):
                rich_text = []
                changed = False
                for rt in payload["rich_text"]:
                    if isinstance(rt, dict):
                        new_rt, rt_changed = _patchable_rich_text(rt, replacements)
                        rich_text.append(new_rt)
                        changed = changed or rt_changed
                    else:
                        rich_text.append(rt)
                if changed and block.get("id"):
                    ci.notion_api_request(
                        "PATCH",
                        f"https://api.notion.com/v1/blocks/{block['id']}",
                        token,
                        body={btype: {"rich_text": rich_text}},
                    )
                    patched += 1
            if isinstance(payload, dict) and isinstance(payload.get("children"), list):
                _walk(payload["children"])
            if isinstance(block.get("children"), list):
                _walk(block["children"])

    _walk(blocks)
    return patched


def build_audit_json(alerts: list[bl.LintFinding],
                     info: list[bl.LintFinding],
                     *,
                     run_date_kst: str = "",
                     brief_title: str = "",
                     brief_url: str = "",
                     note: str = "") -> dict[str, Any]:
    """CI(grm-brief-audit.yml)가 읽어 `GRM Intake 운영 경고` Issue 로 띄울 결과 JSON.

    `ok=False`(alert≥1)일 때만 Issue 가 열린다(과알림 0 — info/WARN 단독은 알림 아님).
    """
    def _ser(f: bl.LintFinding) -> dict[str, str]:
        return {"severity": f.severity, "code": f.code, "url": f.url, "message": f.message}

    return {
        "ok": not alerts,
        "run_date_kst": run_date_kst,
        "brief": {"title": brief_title, "url": brief_url},
        "fail_count": len(alerts),
        "info_count": len(info),
        "alerts": [_ser(f) for f in alerts],
        "info": [_ser(f) for f in info],
        "note": note,
    }


def skipped_json(note: str, *, run_date_kst: str = "", skip_class: str = "") -> dict[str, Any]:
    """대조 불가(토큰·brief·handoff 부재·fetch 실패) → ok:true 건너뜀(false-red 금지).

    `skip_class` 로 skip 을 두 클래스로 나눈다(과알림 0 유지 — CI 가 이 값으로 분기):
      - ``"infra"``   = 탐지선(detective) 자체가 죽음(NOTION_TOKEN 부재·Notion 조회/본문
                        fetch 실패). 발행 후 2차 방어선이 무력화된 상태 → CI(grm-brief-audit)
                        가 **스케줄 실행에서만** 운영 경고로 표면화한다(토큰 만료 시 탐지선이
                        무기한 죽어도 신호 0 이던 침묵 실패를 차단).
      - ``"content"`` = 탐지선은 살아있고 대조 **대상**이 없음(Weekly Brief 페이지 없음·
                        CONSUMED handoff 미발견). 발행 부재는 주간 리뷰·health 가 다른 경로로
                        커버 → 무알림 유지(과알림 0).
    기본값 ``""`` = 미분류(정상 경로는 skip 이 아니므로 이 값을 쓰지 않는다). 기존 스키마에
    키만 추가하는 additive 변경 — 기존 소비자(정상 audit JSON 필드) 무영향.
    """
    return {"ok": True, "run_date_kst": run_date_kst, "skip_class": skip_class,
            "brief": {"title": "", "url": ""},
            "fail_count": 0, "info_count": 0, "alerts": [], "info": [], "note": note}


def format_audit_report(result: dict[str, Any]) -> str:
    """audit JSON 을 사람이 읽는 텍스트로(CI 로그·Issue 본문 공용)."""
    if result.get("note") and not result.get("alerts"):
        return f"[SKIP] 발행 후 탐지(출처 근거+구조) — {result['note']}"
    alerts = result.get("alerts", [])
    if not alerts:
        return ("[PASS] 발행 후 탐지(출처 근거+구조) — FAIL 0"
                f" (info {result.get('info_count', 0)})")
    lines = [f"[FAIL] 발행 후 탐지(출처 근거+구조) — FAIL {len(alerts)}"
             f" · brief={result.get('brief', {}).get('title') or '?'}"]
    for a in alerts:
        loc = f" {a['url']}" if a.get("url") else ""
        lines.append(f"  ✖ [{a['code']}]{loc} — {a['message']}")
    lines.append("→ 발행물에 결정론 위반(미근거 링크 / 구조: 요일·잔존토큰·금지문법)이 있다. "
                 "운영자 확인·정정 필요.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Notion I/O — lazy import(collect_intake 재사용). 단위테스트는 stub 으로 대체.
# ─────────────────────────────────────────────────────────────────────────────
def _ci():  # lazy — 순수 코어/테스트는 collect_intake(=requests) 를 import 하지 않는다.
    import collect_intake as ci
    return ci


_BRIEF_MAX_BLOCK_PAGES = 40   # children 100/page · 브리프 한 페이지 분량 안전 상한
_BRIEF_MAX_DEPTH = 4          # callout>table>row 정도. 무한 재귀 방지.


def fetch_latest_brief(token: str, db_id: str) -> dict[str, Any] | None:
    """Weekly Brief DB 에서 가장 최근 발행 페이지 1건(메타) 조회."""
    ci = _ci()
    body = {"page_size": 1, "sorts": [{"timestamp": "created_time", "direction": "descending"}]}
    data = ci.notion_api_request(
        "POST", ci.NOTION_DB_QUERY_URL_TPL.format(db_id=db_id), token, body=body)
    results = data.get("results", [])
    if not results:
        return None
    page = results[0]
    title = ""
    props = page.get("properties", {})
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
            break
    return {"id": page.get("id", ""), "url": page.get("url", ""), "title": title}


def _fetch_block_children(token: str, block_id: str, depth: int) -> list[dict[str, Any]]:
    ci = _ci()
    url_tpl = ci.NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=block_id)
    import urllib.parse
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(_BRIEF_MAX_BLOCK_PAGES):
        req = url_tpl + (f"?start_cursor={urllib.parse.quote(cursor)}" if cursor else "")
        data = ci.notion_api_request("GET", req, token)
        for block in data.get("results", []):
            if depth < _BRIEF_MAX_DEPTH and block.get("has_children"):
                kids = _fetch_block_children(token, block.get("id", ""), depth + 1)
                block.setdefault("children", []).extend(kids)
            out.append(block)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def fetch_brief_blocks(token: str, page_id: str) -> list[dict[str, Any]]:
    """발행 페이지 본문(중첩 블록 재귀) 블록 리스트 — URL·평문 추출 공용 입력."""
    return _fetch_block_children(token, page_id, 0)


def fetch_brief_urls(token: str, page_id: str) -> list[str]:
    """발행 페이지 본문(중첩 블록 재귀)에서 모든 링크 URL 추출."""
    return extract_urls_from_blocks(fetch_brief_blocks(token, page_id))


def fetch_latest_consumed_handoff_rows(token: str, db_id: str) -> list[dict[str, Any]]:
    """Intake DB 에서 가장 최근 CONSUMED handoff(Status=Processed)의 rows[] 복원."""
    ci = _ci()
    body = {
        "filter": {"and": [
            {"property": ci.PROP_TYPE_CLASS, "select": {"equals": "routine-handoff"}},
            {"property": ci.PROP_STATUS, "select": {"equals": "Processed"}},
        ]},
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        "page_size": 1,
    }
    data = ci.notion_api_request(
        "POST", ci.NOTION_DB_QUERY_URL_TPL.format(db_id=db_id), token, body=body)
    results = data.get("results", [])
    if not results:
        return []
    page_id = results[0].get("id", "")
    text = _fetch_page_code_json(token, page_id)
    if not text:
        return []
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return []
    return bl.extract_handoff_rows(obj)


def _fetch_page_code_json(token: str, page_id: str) -> str:
    """페이지 본문의 JSON code block 들을 순서대로 이어붙여 반환(handoff payload 복원)."""
    ci = _ci()
    import urllib.parse
    url_tpl = ci.NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    chunks: list[str] = []
    cursor: str | None = None
    for _ in range(ci._INTAKE_RAW_MAX_PAGES):
        req = url_tpl + (f"?start_cursor={urllib.parse.quote(cursor)}" if cursor else "")
        data = ci.notion_api_request("GET", req, token)
        for block in data.get("results", []):
            if block.get("type") != "code":
                continue
            code = block.get("code", {})
            if (code.get("language") or "") not in ("json", "plain text", ""):
                continue
            for rt in code.get("rich_text", []):
                chunks.append(rt.get("plain_text")
                              or rt.get("text", {}).get("content", "") or "")
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return "".join(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────
def run(token: str, *, weekly_db_id: str, intake_db_id: str,
        verify: bool = True) -> dict[str, Any]:
    """탐지 1회 실행 → audit JSON(출처 근거 + 구조). 대조 불가/오류는 ok:true 건너뜀(false-red 금지).

    URL 근거 대조(handoff 필요)와 별개로, 발행 본문 평문에 `lint_publish_structure` 를 돌려
    구조 위반(요일·잔존토큰·금지문법)도 alert 로 집계한다 — 구조 검사는 handoff 없이도
    가능하지만 본 함수는 근거 대조와 한 번에 묶어 실행한다(handoff 미발견 시 전체 건너뜀).
    """
    if not token:
        # infra: 토큰 부재 = 탐지선 자체가 못 뜬다(만료 시 무기한 침묵 방지 → 스케줄 경보).
        return skipped_json("NOTION_TOKEN 부재 — 발행 후 탐지 건너뜀", skip_class="infra")
    try:
        brief = fetch_latest_brief(token, weekly_db_id)
    except Exception as exc:  # noqa: BLE001
        # infra: Notion 조회 실패(만료·권한·네트워크) = 탐지선 죽음.
        return skipped_json(f"Weekly Brief 조회 실패(건너뜀): {str(exc)[:160]}",
                            skip_class="infra")
    if not brief:
        # content: 탐지선은 살아있고 발행 대상만 없음 → 무알림(발행 부재는 다른 경로가 커버).
        return skipped_json("Weekly Brief 페이지 없음 — 건너뜀", skip_class="content")
    try:
        rows = fetch_latest_consumed_handoff_rows(token, intake_db_id)
    except Exception as exc:  # noqa: BLE001
        # infra: handoff 조회 실패 = 탐지선 죽음.
        return skipped_json(f"handoff 조회 실패(건너뜀): {str(exc)[:160]}",
                            skip_class="infra")
    if not rows:
        # content: 근거 집합이 없으면 모든 링크가 미근거로 보여 과알림 → 대조 불가로 건너뜀
        # (탐지선은 살아있고 대조 대상 handoff 만 없음 → 무알림).
        return skipped_json("CONSUMED handoff 미발견 — 근거 대조 불가, 건너뜀",
                            skip_class="content")
    try:
        blocks = fetch_brief_blocks(token, brief["id"])
    except Exception as exc:  # noqa: BLE001
        # infra: 본문 fetch 실패 = 탐지선 죽음.
        return skipped_json(f"브리프 본문 fetch 실패(건너뜀): {str(exc)[:160]}",
                            skip_class="infra")
    urls = extract_urls_from_blocks(blocks)
    text = extract_text_from_blocks(blocks)

    verdict = (lambda u: definitive_verdict(u)) if verify else None
    alerts, info = classify(rows, urls, verdict=verdict, published_text=text)
    # 구조 위반(PL1 잔존토큰·PL3/16 금지문법·PL14 요일=날짜)도 alert 로 포함한다 —
    # 발행 직전 `--structure` 게이트와 동등한 결정론 검사(네트워크 0 → 과알림 0). MCP 전용
    # Routine 은 인-루틴 게이트를 코드로 못 돌리므로 이 탐지가 요일류 결함의 유일 결정론 방어선.
    struct_alerts = [f for f in bl.lint_publish_structure(text) if f.severity == bl.SEV_FAIL]
    # scaffold 고정 셀 전사 무결성(PL18) — handoff card_scaffold 의 W2 표 고정 셀(FEI·발행일·
    # 시설유형·Class·문서번호 등)이 발행본에 글자그대로 보존됐는지 결정론 대조(06-22 FDA 483/
    # Lancora 사고 클래스). 렌더된 카드만·과알림 0. 전부 FAIL 만 반환하므로 그대로 alert 에 합산.
    scaffold_cell_alerts = bl.lint_scaffold_fixed_cells(rows, text)
    # 수집 현황 '수집' 숫자(총계+소스별)를 handoff 정본과 대조한다(W2) — 발행물 LLM 집계가
    # 수집기 산출과 어긋나면 FAIL(요일 PL14 와 동형 결정론 검사·네트워크 0 → 과알림 0). 정본은
    # handoff rows 로 독립 재집계(build_coverage_collected = W1 이 handoff 에 싣는 값과 동일 산식).
    try:
        expected_cov = _ci().build_coverage_collected(_ci().coverage_source_counts(rows))
    except Exception:  # noqa: BLE001 — 정본 산출 실패는 대조 생략(false-red 금지)
        expected_cov = None
    cov_findings = bl.lint_coverage_counts(expected_cov, text)
    cov_alerts = [f for f in cov_findings if f.severity == bl.SEV_FAIL]
    info = info + [f for f in cov_findings if f.severity != bl.SEV_FAIL]
    alerts = struct_alerts + cov_alerts + scaffold_cell_alerts + alerts
    note = ""
    if alerts and _enable_brief_autofix():
        replacements = build_autofix_replacements(rows, urls, published_text=text)
        if replacements:
            try:
                patched = apply_autofix_replacements(token, blocks, replacements)
                note = f"ENABLE_BRIEF_AUTOFIX=true — URL self-heal PATCH {patched} block(s)."
            except Exception as exc:  # noqa: BLE001
                note = f"ENABLE_BRIEF_AUTOFIX=true — URL self-heal 실패: {str(exc)[:160]}"
        else:
            note = "ENABLE_BRIEF_AUTOFIX=true — unambiguous URL replacement 없음(알림만)."
    return build_audit_json(alerts, info, brief_title=brief.get("title", ""),
                            brief_url=brief.get("url", ""), note=note)


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        prog="verify_published_brief",
        description="발행 후 출처 링크 근거(provenance) 탐지 — FAIL 시 운영 경고 JSON 생성.")
    p.add_argument("--out", default=os.environ.get("GRM_BRIEF_AUDIT_JSON", DEFAULT_AUDIT_JSON),
                   help="audit 결과 JSON 출력 경로.")
    p.add_argument("--no-verify", action="store_true",
                   help="비-MFDS 미근거 링크 live verify 생략(MFDS 결정론 FAIL 만).")
    p.add_argument("--exit-nonzero-on-fail", action="store_true",
                   help="(로컬용) alert 발생 시 exit 1. CI 는 JSON 으로 Issue 처리(기본 exit 0).")
    args = p.parse_args(argv)

    token = os.environ.get("NOTION_TOKEN", "").strip()
    weekly_db = os.environ.get("GRM_WEEKLY_BRIEF_DB_ID", DEFAULT_WEEKLY_BRIEF_DB_ID).strip()
    intake_db = (os.environ.get("NOTION_DATABASE_ID")
                 or os.environ.get("GRM_INTAKE_DB_ID")
                 or DEFAULT_INTAKE_DB_ID).strip()

    result = run(token, weekly_db_id=weekly_db, intake_db_id=intake_db,
                 verify=not args.no_verify)
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[WARN] audit JSON 쓰기 실패: {exc}", file=sys.stderr)
    print(format_audit_report(result))
    if args.exit_nonzero_on_fail and not result.get("ok", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
