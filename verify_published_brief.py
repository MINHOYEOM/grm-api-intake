#!/usr/bin/env python3
"""Audit the latest published web brief for source provenance and structure."""
from __future__ import annotations

import copy
import glob
import json
import os
import sys
from typing import Any, Iterable

import brief_lint as bl
from grm_common import env_flag

DEFAULT_AUDIT_JSON = "grm-brief-audit.json"
# 웹 발행본(현 실채널) 디렉터리 — 규약은 `web/data/briefs/README.md`(주차당 1파일·
# `brief_web_{publish_date}.json`·`grm-web-card/v1` 스키마).
DEFAULT_WEB_BRIEFS_DIR = "web/data/briefs"


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


def classify_urls(allowed: "set[str]",
                  published_urls: list[str],
                  verdict: "Any | None" = None,
                  *,
                  allowed_fetched: Iterable[str] = ()) -> tuple[list[bl.LintFinding], list[bl.LintFinding]]:
    """URL 근거 대조 코어 — 근거 집합(`allowed`)만 있으면 되고 handoff rows 형태는 몰라도 된다.

    Notion 경로(`classify`, handoff rows 에서 allowed 를 파생)와 웹 경로(`classify_web`, 카드
    `sources` 에서 allowed 를 파생)가 이 함수를 공용한다. 미근거 링크는 ALL_DOMAINS 로 전부
    검사하되, 비-MFDS 후보는 `allowed_fetched` 이거나 `verdict(url)` 가 BAD 일 때만 alert 로
    승격한다(과알림 0 — MFDS 는 예외적으로 항상 alert, W1 사고 클래스).
    """
    base = bl.lint_urls(published_urls, allowed, policy=bl.POLICY_ALL_DOMAINS,
                        allowed_fetched=allowed_fetched)
    alerts: list[bl.LintFinding] = []
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


def classify(handoff_rows: list[dict[str, Any]],
             published_urls: list[str],
             verdict: "Any | None" = None,
             *,
             allowed_fetched: Iterable[str] = (),
             published_text: "str | None" = None) -> tuple[list[bl.LintFinding], list[bl.LintFinding]]:
    """(alert_findings, info_findings) 반환 — Notion 발행 경로(레거시/폴백) 전용.

    scaffold footer 누락은 도메인·live 상태와 무관하게 alert 다. 단 `published_text` 가
    주어지면 **실제로 렌더된 카드**(document_id 가 발행본에 존재)에 한해 검사해 Tier1 Skipped
    /보류 row 의 footer 오탐을 차단한다(과알림 0). URL 근거 대조 자체는 `classify_urls` 공용.
    """
    allowed = bl.collect_allowed_urls(handoff_rows)
    alerts, info = classify_urls(allowed, published_urls, verdict=verdict,
                                 allowed_fetched=allowed_fetched)
    footer_alerts = bl.lint_scaffold_footer_integrity(
        handoff_rows, published_urls, published_text=published_text)
    return footer_alerts + alerts, info


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
      - "infra" means a valid published web brief could not be found.
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
# ─────────────────────────────────────────────────────────────────────────────
# 웹 발행본(`web/data/briefs/`) 감사 — 2026-07-07 근본 전환. 파일 기반이라 네트워크 0(brief
# 자체를 얻는 데 Notion 토큰·API 호출이 필요 없다 — 발행 PR 이 사람 승인으로 머지된 뒤에야
# CI 체크아웃에 파일이 존재하므로 "최신"도 `brief.publish_date` 로 직접 정본 판단한다. Notion
# 경로의 `fetch_latest_brief` 정렬버그(Issue #110) 클래스가 구조적으로 재발 불가).
#
# `grm-web-card/v1` 스키마는 코드-verbatim 필드(`facts`·`quotes[].original`·`sources`·`id`·
# 배지류)와 LLM 슬롯(`title_issue`·`summary`·`implication`·`key_facts`·`checks`·
# `quotes[].translation`·brief `tldr`)이 스키마 레벨로 분리돼 있고, `inject_slots.py` 가드가
# LLM 델타가 code-verbatim 필드를 건드리는 것 자체를 차단한다(주입 시 무시). 그 결과 Notion
# 감사의 5개 체크 중 3개는 웹 경로에서 **구조적으로 불필요**해 의도적으로 드롭한다:
#   · scaffold footer 유실(L17-SCAFFOLD-FOOTER-MISSING) — `sources` 는 별도 JSON 필드라
#     "본문에서 사라짐" 자체가 불가능.
#   · 표 고정 셀 전사(PL18-SCAFFOLD-CELL) — `facts`[] 도 동일 이유로 불가침.
#   · 수집건수 대조(PL15-COVERAGE-*) — `brief.coverage` 는 LLM 슬롯이 아니라
#     `assemble_publish_brief.py` 가 카드 집합에서 직접 재계산하는 코드값(LLM 이 쓰지 않음).
# 남기는 2개(모두 LLM 자유텍스트 슬롯에 여전히 유효한 위협)는 `classify_urls`/
# `lint_publish_structure` 로 그대로 이관한다:
#   · 미근거 URL 삽입(L17-MFDS-PROVENANCE/UNGROUNDED/UNVERIFIED).
#   · 잔존 슬롯 토큰·금지 문법(PL1/PL3/16) — PL10/PL14 패턴(마크다운 카드 제목·괄호 요일)은
#     웹 스키마에 대응 형태가 없어 매칭되지 않는다(무해 no-op — 오탐 유발 없음).
# ─────────────────────────────────────────────────────────────────────────────

# LLM 이 채우는 카드 슬롯(inject_slots._STR_SLOTS/_LIST_SLOTS 와 동일 — 코드-verbatim
# 필드(facts·sources·id·quotes[].original 등)는 여기 포함하지 않는다).
_WEB_LLM_STR_SLOTS = ("title_issue", "summary", "implication")
_WEB_LLM_LIST_SLOTS = ("key_facts", "checks")


def find_latest_web_brief(briefs_dir: str = DEFAULT_WEB_BRIEFS_DIR
                          ) -> "tuple[str, dict[str, Any]] | None":
    """`{briefs_dir}/brief_web_*.json` 중 `brief.publish_date` 최댓값 1건을 (path, json)로.

    파일명·mtime 이 아니라 **JSON 내부 `publish_date`**(ISO `YYYY-MM-DD`, 문자열 비교로 정렬
    가능)로 최신을 정본 판단한다 — Notion `fetch_latest_brief` 의 `created_time` 정렬버그
    (Issue #110) 클래스가 이 경로엔 애초에 존재하지 않는다. 디렉터리 부재·파일 0건·전부 파싱
    실패 → `None`(호출자가 Notion 폴백으로 넘어간다).
    """
    try:
        paths = sorted(glob.glob(os.path.join(briefs_dir, "brief_web_*.json")))
    except OSError:
        return None
    best: "tuple[str, dict[str, Any]] | None" = None
    best_date = ""
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        date = (data.get("brief") or {}).get("publish_date")
        if not isinstance(date, str) or not date:
            continue
        if date > best_date:
            best_date = date
            best = (path, data)
    return best


def extract_web_llm_text(brief_json: dict[str, Any]) -> str:
    """웹 브리프 JSON 에서 LLM 슬롯 값만 평문 한 블록으로 이어붙인다(구조 lint·URL 스캔 공용 입력).

    `facts`·`sources`·`id`·배지류(코드-verbatim)는 제외 — inject_slots 가드로 LLM 이 못 건드리는
    필드라 조사할 이유가 없다. `deep_analysis`(있으면)는 별도 게이트(`verify_deep_analysis.py`
    — 인용 근거를 소스 원문과 대조)가 주입 시점에 이미 검증하므로 여기서 다시 다루지 않는다.
    """
    lines: list[str] = []
    brief_meta = brief_json.get("brief") or {}
    for t in brief_meta.get("tldr") or []:
        if isinstance(t, str) and t:
            lines.append(t)
    for card in brief_json.get("cards") or []:
        if not isinstance(card, dict):
            continue
        for key in _WEB_LLM_STR_SLOTS:
            v = card.get(key)
            if isinstance(v, str) and v:
                lines.append(v)
        for key in _WEB_LLM_LIST_SLOTS:
            for v in card.get(key) or []:
                if isinstance(v, str) and v:
                    lines.append(v)
        for q in card.get("quotes") or []:
            if isinstance(q, dict):
                tr = q.get("translation")
                if isinstance(tr, str) and tr:
                    lines.append(tr)
    return "\n".join(lines)


def collect_web_allowed_urls(brief_json: dict[str, Any]) -> set[str]:
    """카드 `sources.info_url`/`official_url`(코드-verbatim) = 이 발행본의 근거 URL 집합.

    Notion 경로의 `collect_allowed_urls(handoff_rows)`에 대응하지만, 웹 JSON 은 카드 자신의
    `sources` 가 이미 정본이라 별도 handoff 재조회(Notion 등)가 필요 없다.
    """
    allowed: set[str] = set()
    for card in brief_json.get("cards") or []:
        if not isinstance(card, dict):
            continue
        sources = card.get("sources") or {}
        if not isinstance(sources, dict):
            continue
        for key in ("info_url", "official_url"):
            u = sources.get(key)
            if isinstance(u, str) and u.strip():
                allowed.add(bl.normalize_url(u))
    allowed.discard("")
    return allowed


def classify_web(brief_json: dict[str, Any], *,
                 verify: bool = True) -> tuple[list[bl.LintFinding], list[bl.LintFinding]]:
    """웹 발행본 1건 → (alert_findings, info_findings). §웹 발행본 감사 스코프 참조."""
    allowed = collect_web_allowed_urls(brief_json)
    llm_text = extract_web_llm_text(brief_json)
    urls = bl.extract_markdown_links(llm_text)
    verdict = (lambda u: definitive_verdict(u)) if verify else None
    alerts, info = classify_urls(allowed, urls, verdict=verdict)
    struct_alerts = [f for f in bl.lint_publish_structure(llm_text) if f.severity == bl.SEV_FAIL]
    return struct_alerts + alerts, info


def _web_brief_title(brief_json: dict[str, Any]) -> str:
    """브리프 제목 파생 — 스키마에 전용 필드 없음(web/README §스키마 v1 한계와 동일 규칙)."""
    brief_meta = brief_json.get("brief") or {}
    tldr = brief_meta.get("tldr") or []
    if tldr and isinstance(tldr[0], str) and tldr[0]:
        return tldr[0]
    date = brief_meta.get("publish_date", "")
    return f"GRM 웹 발행 — {date}" if date else "GRM 웹 발행"


def run_web(brief_path: str, brief_json: dict[str, Any], *,
           verify: bool = True) -> dict[str, Any]:
    """웹 발행본 1건 탐지 실행 → audit JSON. `run()`(Notion) 과 동일 스키마 산출."""
    alerts, info = classify_web(brief_json, verify=verify)
    brief_meta = brief_json.get("brief") or {}
    return build_audit_json(alerts, info,
                            run_date_kst=brief_meta.get("publish_date", "") or "",
                            brief_title=_web_brief_title(brief_json),
                            brief_url=brief_path)


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────
def run_audit(*, verify: bool = True,
              web_briefs_dir: str = DEFAULT_WEB_BRIEFS_DIR) -> dict[str, Any]:
    """Audit the latest valid web brief."""
    found = find_latest_web_brief(web_briefs_dir)
    if found is not None:
        path, data = found
        return run_web(path, data, verify=verify)
    return skipped_json("No valid published web brief found", skip_class="infra")


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
    p.add_argument("--web-briefs-dir",
                   default=os.environ.get("GRM_WEB_BRIEFS_DIR", DEFAULT_WEB_BRIEFS_DIR),
                   help="Directory containing published brief_web_*.json files.")
    p.add_argument("--no-verify", action="store_true",
                   help="비-MFDS 미근거 링크 live verify 생략(MFDS 결정론 FAIL 만).")
    p.add_argument("--exit-nonzero-on-fail", action="store_true",
                   help="(로컬용) alert 발생 시 exit 1. CI 는 JSON 으로 Issue 처리(기본 exit 0).")
    args = p.parse_args(argv)

    result = run_audit(verify=not args.no_verify, web_briefs_dir=args.web_briefs_dir)
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
