#!/usr/bin/env python3
"""원천 대조 감시 — "원천이 올린 문서 = 수집 + 의도적 제외 + 미뤄짐" 등식을 매일 센다.

왜 있나
  기존 감시(grm_health · source_silence · reconciliation_audit · fda_body_coverage)는 모두 **우리 과거값·
  마지막 수집일**과만 비교한다. FDA 가 483·경고서한을 한꺼번에 대량 게시해 일부가 누락돼도 "평소 대비"로는
  보이지 않는다. 이 감시는 **원천 목록 자체**를 다시 받아 문서 하나하나가 어디에 있는지 센다.

등식(소스마다, 소스별 창 안의 원천 목록 S)
  S = 수집됨 ∪ 의도적 제외 ∪ 미뤄짐   — 어디에도 없으면 **놓침**.
  * 수집됨   = Notion Intake DB 에 `source::document_id` 가 있다. **수집기 dedup 과 같은 조회**
               (`grm_notion.notion_query_existing_keys`)라서 두 곳의 "있다" 기준이 갈라질 수 없다.
  * 의도적 제외 = 제외 장부(`public.intake_exclusion`, migration 130)에 행이 있다(basis 가 metadata/text).
  * 미뤄짐   = 제외 장부에 basis=`text_missing` 행이 있다 — 본문을 못 얻은 채 메타만으로 판정해 수집기가
               "판정 끝"으로 보지 않고 다음 실행에 다시 보는 문서(하루 한도·OCR 예산 등). 같은 문서가
               `DEFERRED_MAX_DAYS` 일을 넘게 남아 있으면(first_seen 기준) 놓침으로 **승격**한다.
  * 최근 미시도 = 게시일이 오늘(KST) 기준 `DEFERRED_MAX_DAYS` 일 이내이고 수집·제외 어디에도 없는 문서(이유
               `recent_not_yet_attempted`) — 대량 게시일 하루 한도로 아직 시도 전인 것. 기한을 넘기면 놓침.
  * 장부 이전(pre_ledger) = 장부 연결 전에 게시돼 장부에 없을 수 있는 문서 — 첫 2주만 별도 칸.

★원천 목록은 수집기의 관련성 필터를 **거치지 않는다**(필터 자체가 틀렸을 때도 잡으려고). 단 HTTP·파싱
  헬퍼(User-Agent 포함 — FDA 는 헤더가 다르면 다른 응답을 준다)와 문서 키 식(`_stable_doc_id` 등)은
  수집기 것을 그대로 재사용한다. 키 식이 갈라지면 모든 문서가 놓침으로 보이므로 시험이 핀으로 잠근다.

판정(소스별) — 기존 #1274 경보가 장애·조용함·구조를 구분하지 못해 무시당한 교훈
  ok / missed(놓침 ≥1) / source_unreachable(원천 접속 불가 — 놓침으로 세지 않는다) /
  source_quiet(원천 자체가 0건) / deferred_only(전부 미뤄짐) /
  store_unreachable(우리 저장소를 못 읽어 대조 불가 — 놓침으로 세지 않는다) / disabled(수집기가 꺼진 소스).

비차단: 이상이 있어도 exit 0. 결과는 JSON·step summary 로 남기고 이슈 계획(`issues`)을 JSON 에 싣는다
(이슈를 열고 닫는 일은 워크플로의 github-script 가 한다 — 기존 감시들의 관례).
서비스키·API 키는 로그·예외 문구에 싣지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")

# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────
# 제외 장부가 수집기에 연결된 날(KST). repo 변수 ENABLE_INTAKE_EXCLUSION_LEDGER=true 를 켠 시각
# 2026-11-07T16:27Z = 2026-11-08 01:27 KST. 이 날짜 **이전에 게시된** 문서는 의도적으로 버렸어도 장부에 흔적이
# 없을 수 있다(장부는 그날부터 쌓인다). 그래서 "어디에도 없음"을 곧바로 놓침으로 단정하지 않고 첫 2주는
# `pre_ledger` 칸으로 따로 센다. 수집기는 창 안 문서를 매일 다시 판정하므로 보통 첫 며칠 안에 장부로 흡수된다.
LEDGER_ACTIVE_FROM = date(2026, 11, 8)
PRE_LEDGER_GRACE_DAYS = 14           # 이 기간이 지나면 pre_ledger 칸은 쓰지 않는다(모두 놓침으로 센다)
DEFERRED_MAX_DAYS = 3                # 미뤄짐이 이 일수를 넘어 같은 문서에 남으면 놓침으로 승격
DEFAULT_WINDOW_DAYS = 7              # collect_intake --window-days 기본값
DEFAULT_ENF_WINDOW_DAYS = 30         # MFDS_ENFORCEMENT_WINDOW_DAYS 기본값(지연공개 소스의 창)
NOTION_DEDUP_MARGIN_DAYS = 3         # Notion Run Date 조회 창 여유(창 시작 이전 수집분 흡수)
MAX_LISTED_IN_ISSUE = 40
HTTP_TIMEOUT = 30
_UA = "GRM-Intake/1.1 (+github-actions)"   # collect_intake 의 WL·FR 요청과 같은 값

V_OK = "ok"
V_MISSED = "missed"
V_UNREACHABLE = "source_unreachable"
V_QUIET = "source_quiet"
V_DEFERRED_ONLY = "deferred_only"
V_STORE_UNREACHABLE = "store_unreachable"
V_DISABLED = "disabled"
VERDICT_LABEL_KO = {
    V_OK: "정상", V_MISSED: "놓침", V_UNREACHABLE: "원천 접속 불가", V_QUIET: "원천 조용함",
    V_DEFERRED_ONLY: "전부 미뤄짐", V_STORE_UNREACHABLE: "저장소 대조 불가", V_DISABLED: "수집 꺼짐",
}
_CLOSEABLE = frozenset({V_OK, V_QUIET, V_DEFERRED_ONLY, V_DISABLED})   # 이 판정이면 열린 이슈를 닫는다

WINDOW_BASE = "base"   # 수집기 기본 창(--window-days, 7)
WINDOW_ENF = "enf"     # 지연공개 창(MFDS_ENFORCEMENT_WINDOW_DAYS, 30)

ISSUE_LABEL_FAMILY = "reconciliation"   # 기존 주간 reconciliation 과 같은 라벨 계열
ISSUE_LABEL_PREFIX = "reconcile-"       # + 소스 슬러그 → 라벨에 소스명이 드러난다(담당 팀 자동 배정용)
CRASH_ISSUE_TITLE = "[source-reconcile] 원천 대조 감시 자체가 실패했습니다"
CRASH_ISSUE_LABEL = ISSUE_LABEL_PREFIX + "monitor"


@dataclass(frozen=True)
class SourceSpec:
    key: str            # 내부 키(--sources 토큰)
    source: str         # Notion `Source` select 값 = dedup 키 앞부분 = 장부 source
    label: str          # 사람이 읽는 이름
    slug: str           # 이슈 라벨용
    window_kind: str
    flag: str = ""      # 이 ENABLE_* 가 꺼지면 수집기가 안 도는 소스 → disabled
    flag_default: bool = True


SPECS: tuple[SourceSpec, ...] = (
    SourceSpec("fda483", "FDA 483", "FDA 483 (OII 리딩룸)", "fda-483", WINDOW_ENF,
               flag="ENABLE_FDA_483"),
    SourceSpec("wl", "FDA Warning Letter", "FDA 경고서한", "fda-wl", WINDOW_BASE),
    SourceSpec("recall", "OpenFDA Recall", "OpenFDA 의약품 회수", "openfda-recall", WINDOW_ENF),
    SourceSpec("mfds_rss", "MFDS", "식약처 RSS 보드", "mfds-rss", WINDOW_BASE, flag="ENABLE_MFDS"),
    SourceSpec("fr", "Federal Register", "Federal Register (FDA)", "federal-register", WINDOW_BASE),
)
SPEC_BY_KEY = {s.key: s for s in SPECS}


# ─────────────────────────────────────────────────────────────────────────────
# 자료형
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SourceDoc:
    document_id: str
    url: str = ""
    title: str = ""
    published: str = ""     # YYYY-MM-DD


@dataclass
class FetchOutcome:
    """원천 목록 확보 결과. docs 는 창 안 문서(중복 제거). error 가 있으면 원천 접속 불가."""
    docs: list[SourceDoc] = field(default_factory=list)
    error: str = ""
    partial: bool = False                       # 백본 폴백·보드 일부 실패 등 — 목록이 전수가 아닐 수 있다
    notes: list[str] = field(default_factory=list)
    undated: int = 0                            # 게시일이 없어 창 판정이 불가능했던 항목 수
    enabled: bool = True


@dataclass(frozen=True)
class LedgerRow:
    basis: str
    reason_code: str = ""
    first_seen: str = ""
    last_seen: str = ""


class SourceFetchError(RuntimeError):
    """원천 목록을 받지 못했다(접속·파싱 실패). 놓침으로 세지 않는다."""


class StoreReadError(RuntimeError):
    """우리 저장소(Notion·장부)를 읽지 못했다. 대조 불가 — 놓침으로 세지 않는다."""


@dataclass
class SourceResult:
    key: str
    source: str
    label: str
    slug: str
    verdict: str
    window_start: str
    window_end: str
    counts: dict[str, int]
    missed: list[dict[str, str]] = field(default_factory=list)
    deferred: list[dict[str, str]] = field(default_factory=list)
    pre_ledger: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    partial: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "source": self.source, "label": self.label, "slug": self.slug,
            "verdict": self.verdict, "verdict_ko": VERDICT_LABEL_KO.get(self.verdict, self.verdict),
            "window": {"start": self.window_start, "end": self.window_end},
            "counts": dict(self.counts), "missed": list(self.missed),
            "deferred": list(self.deferred), "pre_ledger": list(self.pre_ledger),
            "error": self.error, "partial": self.partial, "notes": list(self.notes),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 순수 함수 — 등식·판정
# ─────────────────────────────────────────────────────────────────────────────
def noid_document_id(official_url: str, title: str, date_iso: str) -> str:
    """문서 키가 비어 있을 때 수집기가 합성하는 키(`collect_intake.insert_items` 의 식 그대로).

    수집기는 이 문서를 장부에 `missing_document_id` 로 남긴다(적재하지 않는다). 식이 갈라지면 같은 문서가
    장부에서 못 찾아져 놓침으로 보이므로 시험(`test_noid_matches_insert_items`)이 잠근다."""
    return "noid-" + hashlib.sha1(
        f"{official_url}|{title}|{date_iso}".encode("utf-8")).hexdigest()[:16]


def window_bounds(spec: SourceSpec, run_date: date, base_days: int, enf_days: int) -> tuple[date, date]:
    """수집기와 같은 창: start = run_date - N일, end = run_date(`date_window`·`enf_start` 와 동일)."""
    days = enf_days if spec.window_kind == WINDOW_ENF else base_days
    return run_date - timedelta(days=days), run_date


def in_window(published: str, start: date, end: date) -> bool:
    if not published:
        return False
    try:
        return start <= date.fromisoformat(published) <= end
    except ValueError:
        return False


def _days_between(later: date, earlier_iso: str) -> int | None:
    try:
        return (later - date.fromisoformat((earlier_iso or "")[:10])).days
    except ValueError:
        return None


def _is_pre_ledger(published: str, run_date: date) -> bool:
    """장부 연결 전 게시분이고 아직 유예 기간(첫 2주) 안인가."""
    if run_date >= LEDGER_ACTIVE_FROM + timedelta(days=PRE_LEDGER_GRACE_DAYS):
        return False
    try:
        return date.fromisoformat(published) < LEDGER_ACTIVE_FROM
    except ValueError:
        return False       # 게시일을 모르면 유예하지 않는다(놓침으로 센다)


def _brief(doc: SourceDoc, **extra: str) -> dict[str, str]:
    d = {"document_id": doc.document_id, "url": doc.url, "title": doc.title, "published": doc.published}
    d.update(extra)
    return d


def reconcile_source(
    spec: SourceSpec, outcome: FetchOutcome, *, run_date: date,
    window: tuple[date, date],
    collected: set[str] | None, store_error: str = "",
    ledger: dict[str, LedgerRow] | None = None, ledger_error: str = "",
) -> SourceResult:
    """소스 하나의 등식 계산과 판정. `collected` 는 `source::document_id` 집합, `ledger` 는 document_id → 행.

    collected 가 None(저장소 못 읽음)이거나 ledger 가 None(장부 못 읽음)이면 대조 불가 — 놓침 0, 판정은
    store_unreachable. 원천 접속 실패도 놓침 0(source_unreachable)."""
    counts = {"listed": 0, "collected": 0, "excluded": 0, "deferred": 0,
              "deferred_recent": 0, "deferred_overdue": 0, "pre_ledger": 0, "missed": 0}
    res = SourceResult(
        key=spec.key, source=spec.source, label=spec.label, slug=spec.slug, verdict=V_OK,
        window_start=window[0].isoformat(), window_end=window[1].isoformat(), counts=counts,
        partial=outcome.partial, notes=list(outcome.notes))
    if outcome.undated:
        res.notes.append(f"게시일 없는 항목 {outcome.undated}건은 창 판정이 불가능해 세지 않았다")
    if not outcome.enabled:
        res.verdict = V_DISABLED
        res.notes.append(f"{spec.flag} 가 꺼져 있어 수집기가 이 소스를 수집하지 않는다")
        return res
    if outcome.error:
        res.verdict = V_UNREACHABLE
        res.error = outcome.error
        return res
    counts["listed"] = len(outcome.docs)
    if not outcome.docs:
        res.verdict = V_QUIET
        return res
    if collected is None or ledger is None:
        res.verdict = V_STORE_UNREACHABLE
        res.error = store_error or ledger_error or "저장소 조회 실패"
        return res

    for doc in outcome.docs:
        if f"{spec.source}::{doc.document_id}" in collected:
            counts["collected"] += 1
            continue
        row = ledger.get(doc.document_id)
        if row is not None:
            if row.basis == "text_missing":
                age = _days_between(run_date, row.first_seen)
                if age is not None and age > DEFERRED_MAX_DAYS:
                    counts["missed"] += 1
                    counts["deferred_overdue"] += 1
                    res.missed.append(_brief(
                        doc, reason="deferred_overdue",
                        detail=f"본문 미확보로 {age}일째 미뤄짐(상한 {DEFERRED_MAX_DAYS}일) · 장부 사유 {row.reason_code}"))
                else:
                    counts["deferred"] += 1
                    res.deferred.append(_brief(doc, reason=row.reason_code, since=row.first_seen))
            else:
                counts["excluded"] += 1
            continue
        # 게시일이 DEFERRED_MAX_DAYS 이내인 문서가 수집·제외 어디에도 없으면 "아직 시도 전"이다 —
        # 대량 게시일에 하루 한도(excerpt cap)에 걸려 한 번도 시도되지 않은 문서(skipped_no_attempt)는
        # 장부에도 Notion 에도 없어 첫날부터 놓침으로 보이는데, 다음 실행들이 따라잡으면 정상이다.
        # 상한을 넘기면 아래 놓침으로 승격된다. 게시일을 모르면 보수적으로 놓침.
        pub_age = _days_between(run_date, doc.published) if doc.published else None
        if pub_age is not None and pub_age <= DEFERRED_MAX_DAYS:
            counts["deferred"] += 1
            counts["deferred_recent"] += 1
            res.deferred.append(_brief(doc, reason="recent_not_yet_attempted", since=doc.published))
            continue
        if _is_pre_ledger(doc.published, run_date):
            counts["pre_ledger"] += 1
            res.pre_ledger.append(_brief(doc))
            continue
        counts["missed"] += 1
        res.missed.append(_brief(doc, reason="not_found", detail="수집·제외·미뤄짐 어디에도 없음"))

    if counts["missed"]:
        res.verdict = V_MISSED
    elif counts["deferred"] and not counts["collected"] and not counts["excluded"]:
        res.verdict = V_DEFERRED_ONLY
    return res


# ─────────────────────────────────────────────────────────────────────────────
# 이슈 계획 — 기존 감시들의 관례(라벨·고정 제목·같은 이슈 재사용·정상 복귀 시 해소 댓글 후 닫기)
# ─────────────────────────────────────────────────────────────────────────────
def issue_title(source: str) -> str:
    return f"[source-reconcile] {source} 원천 대비 수집 누락이 있습니다"


def missed_fingerprint(missed: Iterable[dict[str, str]]) -> str:
    ids = sorted(m.get("document_id", "") for m in missed)
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()[:12]


def _md_cell(text: str, limit: int = 90) -> str:
    t = " ".join(str(text or "").split()).replace("|", "/")
    return t if len(t) <= limit else t[: limit - 1] + "…"


def issue_body(res: SourceResult, run_url: str = "") -> str:
    c = res.counts
    lines = [
        f"원천 대조 감시가 **{res.source}** 에서 수집·의도적 제외·미뤄짐 어디에도 없는 문서 **{c['missed']}건**을 찾았다.",
        "",
        f"- 창: {res.window_start} ~ {res.window_end}",
        f"- 원천 {c['listed']}건 = 수집 {c['collected']} + 제외 {c['excluded']} + 미뤄짐 {c['deferred']}"
        f" + 장부 이전 {c['pre_ledger']} + **놓침 {c['missed']}**"
        + (f" (미뤄짐 중 게시 {DEFERRED_MAX_DAYS}일 이내 미시도 {c['deferred_recent']})" if c["deferred_recent"] else "")
        + (f" (그중 미뤄짐 {DEFERRED_MAX_DAYS}일 초과 승격 {c['deferred_overdue']})" if c["deferred_overdue"] else ""),
    ]
    if res.partial:
        lines.append("- 주의: 원천 목록이 전수가 아닐 수 있다(백본 폴백·일부 보드 실패) — " + "; ".join(res.notes))
    lines += ["", "| 게시일 | document_id | 제목 | 원천 URL | 사유 |", "|---|---|---|---|---|"]
    for m in res.missed[:MAX_LISTED_IN_ISSUE]:
        lines.append(f"| {m.get('published', '')} | `{m.get('document_id', '')}` | {_md_cell(m.get('title', ''))}"
                     f" | {m.get('url', '')} | {_md_cell(m.get('detail') or m.get('reason', ''), 70)} |")
    if len(res.missed) > MAX_LISTED_IN_ISSUE:
        lines.append(f"\n…외 {len(res.missed) - MAX_LISTED_IN_ISSUE}건(전체 목록은 실행의 source-reconcile.json 아티팩트)")
    lines += [
        "",
        "**읽는 법**: 수집기는 문서마다 '수집' 아니면 '제외 장부 기록'을 남긴다. 둘 다 없다는 것은 수집기가 그 문서를 "
        "받지 못했거나(목록 절단·예산·HTTP 실패) 적재가 실패했다는 뜻이다. 원천 URL 로 문서가 실제로 존재하는지 확인하고, "
        "존재하면 해당 소스 수집기를 `window_days` 를 넓혀 수동 실행해 회수한다.",
        "",
        f"<!-- missed-fingerprint: {missed_fingerprint(res.missed)} -->",
    ]
    if run_url:
        lines += ["", f"실행: {run_url}"]
    lines += ["", "_놓침이 0이 되면 자동으로 닫힙니다._"]
    return "\n".join(lines)


def plan_issues(results: Iterable[SourceResult], *, run_url: str = "") -> list[dict[str, Any]]:
    """소스별 이슈 동작 계획.

    open  — 놓침 ≥1: 고정 제목의 열린 이슈가 있으면 본문 갱신(지문이 바뀌었을 때만 댓글), 없으면 생성.
    close — 정상·조용함·전부 미뤄짐·수집 꺼짐: 열린 이슈가 있으면 해소 댓글 후 닫는다.
    keep  — 원천 접속 불가·저장소 대조 불가: 판정을 못 한 것이므로 이슈를 열지도 닫지도 않는다
            (크래시를 정상으로 오독해 알람을 스스로 끄는 사고를 막는 기존 관례)."""
    plan: list[dict[str, Any]] = []
    for r in results:
        base = {"key": r.key, "source": r.source, "title": issue_title(r.source),
                "labels": [ISSUE_LABEL_FAMILY, ISSUE_LABEL_PREFIX + r.slug], "verdict": r.verdict}
        if r.verdict == V_MISSED:
            plan.append({**base, "action": "open", "body": issue_body(r, run_url),
                         "fingerprint": missed_fingerprint(r.missed),
                         "comment": f"다시 감지됐습니다 — 놓침 {r.counts['missed']}건"})
        elif r.verdict in _CLOSEABLE:
            plan.append({**base, "action": "close",
                         "comment": f"원천 대조가 정상으로 복귀해 자동으로 닫습니다 "
                                    f"(판정: {VERDICT_LABEL_KO[r.verdict]})."})
        else:
            plan.append({**base, "action": "keep", "reason": VERDICT_LABEL_KO.get(r.verdict, r.verdict)})
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# 원천 목록 확보 — 수집기의 필터를 거치지 않는다. HTTP·파싱 헬퍼와 키 식은 수집기 것을 재사용.
# ─────────────────────────────────────────────────────────────────────────────
_SECRET_RE = re.compile(r"(api_key=|serviceKey=|OC=|apikey=)[^&\s]+", re.I)


def safe_error(exc: BaseException) -> str:
    """예외 요약 — 키가 새지 않게 쿼리의 비밀 값을 지우고 길이를 자른다."""
    text = _SECRET_RE.sub(r"\1***", f"{type(exc).__name__}: {exc}")
    return text[:200]


def _effective_id(document_id: str, url: str, title: str, published: str) -> str:
    return document_id or noid_document_id(url, title, published)


def _dedupe(docs: Iterable[SourceDoc]) -> list[SourceDoc]:
    seen: set[str] = set()
    out: list[SourceDoc] = []
    for d in docs:
        if d.document_id in seen:
            continue
        seen.add(d.document_id)
        out.append(d)
    return out


def fetch_fda483(start: date, end: date) -> FetchOutcome:
    """OII 리딩룸 DataTables(1차) → 전수 JSON(2차) → 정적 10행(3차). `_fetch_html_rows` 를 그대로 쓴다.

    수집기의 PDF·범위 필터는 하나도 거치지 않는다 — 창 안 483 행 전부가 원천 목록이다."""
    import collect_fda_483 as m
    from collect_intake import _within_window
    rows, _total, degraded = m._fetch_html_rows(start)
    if not rows:
        raise SourceFetchError("FDA 483 목록 483 행 0 — 백본 3단 모두 실패 또는 구조 변경")
    docs: list[SourceDoc] = []
    for r in rows:
        pub = m._parse_mdy(r.get("publish_date", ""))
        if not _within_window(pub, start, end) or not r.get("media_id"):
            continue
        docs.append(SourceDoc(document_id=f"fda483-{r['media_id']}", url=m._pdf_url(r["media_id"]),
                              title=r.get("company", ""), published=pub))
    backbone = getattr(m, "_LAST_BACKBONE", "")
    notes = [f"백본={backbone}"] if backbone else []
    partial = bool(degraded) or backbone == getattr(m, "BACKBONE_STATIC_HTML", "static-html")
    if partial:
        notes.append("정적 10행 폴백 또는 JSON 동결 의심 — 원천 목록이 전수가 아닐 수 있다")
    return FetchOutcome(docs=_dedupe(docs), partial=partial, notes=notes)


def wl_docs_from_rows(rows: list[dict[str, Any]], start: date, end: date) -> list[SourceDoc]:
    """WL 목록 행 → 창 안 문서. 열 해석·URL 정규화·문서 키는 `collect_fda_warning_letters` 와 같은 식.

    수집기의 부서 게이트·저가치 키워드 필터는 **적용하지 않는다**(그 필터가 버린 문서는 제외 장부가 설명한다)."""
    import collect_intake as ci
    docs: list[SourceDoc] = []
    for row in rows:
        cols = row.get("_cols", [])
        if len(cols) < 4:
            continue
        posted_raw = cols[0].split("|HREF:")[0].strip()
        if not re.match(r"^\d", posted_raw):
            continue
        letter_date_raw = cols[1].split("|HREF:")[0].strip() if len(cols) > 1 else ""
        recipient_raw = cols[2] if len(cols) > 2 else ""
        wl_href = ""
        if "|HREF:" in recipient_raw:
            parts = recipient_raw.split("|HREF:", 1)
            recipient_raw, wl_href = parts[0].strip(), parts[1].strip()
        subject = cols[4].split("|HREF:")[0].strip() if len(cols) > 4 else ""
        date_iso = ci._parse_wl_date(posted_raw) or ci._parse_wl_date(letter_date_raw)
        if not ci._within_window(date_iso, start, end):
            continue
        if wl_href and wl_href.startswith("/"):
            wl_href = "https://www.fda.gov" + wl_href
        firm = recipient_raw
        headline = subject or firm or "FDA Warning Letter"
        doc_id = ci._stable_doc_id(ci.SOURCE_FDA_WL, firm, wl_href or ci.FDA_WL_URL, date_iso)
        docs.append(SourceDoc(document_id=doc_id, url=wl_href or ci.FDA_WL_URL,
                              title=f"{firm} - {headline}" if firm else headline, published=date_iso))
    return _dedupe(docs)


def fetch_wl(start: date, end: date) -> FetchOutcome:
    """FDA WL: 정적 페이지에서 DataTables 설정을 읽어 전수 뷰(게시일 내림차순)를 창 시작일까지 받는다."""
    import collect_intake as ci
    try:
        resp = requests.get(ci.FDA_WL_URL, timeout=HTTP_TIMEOUT,
                            headers={"User-Agent": _UA, "Accept": "text/html"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise SourceFetchError(safe_error(exc)) from None
    notes: list[str] = []
    partial = False
    try:
        rows = ci._fetch_wl_listing_datatables(resp.text, start)
        notes.append("백본=datatables")
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        parser = ci._FDAWLTableParser()
        parser.feed(resp.text)
        rows = parser.rows
        partial = True
        notes.append(f"DataTables 실패({safe_error(exc)}) — 정적 최신 10행 폴백, 목록이 전수가 아니다")
    if not rows:
        raise SourceFetchError("FDA WL 목록 행 0 — 구조 변경 또는 JS 렌더링 가능성")
    return FetchOutcome(docs=wl_docs_from_rows(rows, start, end), partial=partial, notes=notes)


def fetch_recall(start: date, end: date) -> FetchOutcome:
    """openFDA 의약품 회수(report_date 창). 수집기의 200건 상한 없이 전수 — 상한에 걸린 날을 놓침으로 드러낸다."""
    import collect_intake as ci
    search = f"report_date:[{start.strftime('%Y%m%d')}+TO+{end.strftime('%Y%m%d')}]"
    api_key = os.environ.get("OPENFDA_API_KEY", "").strip()
    docs: list[SourceDoc] = []
    skip = 0
    try:
        for _page in range(60):
            params = [("search", search), ("limit", str(ci.OPENFDA_LIMIT)), ("skip", str(skip))]
            if api_key:
                params.append(("api_key", api_key))
            url = ci.OPENFDA_API_BASE + "?" + urllib.parse.urlencode(params, safe=":[]+")
            try:
                data = ci.http_get_json(url)
            except ci.HTTPClientError as exc:
                if exc.status_code == 404:        # openFDA 는 결과 0건이면 404
                    break
                raise
            results = data.get("results", []) or []
            total = ((data.get("meta", {}) or {}).get("results", {}) or {}).get("total", 0)
            for r in results:
                it = ci._recall_to_item(r, "reconcile")
                docs.append(SourceDoc(
                    document_id=_effective_id(it.document_id, it.official_url, it.headline, it.date_iso),
                    url=it.official_url, title=it.headline, published=it.date_iso))
            skip += len(results)
            if not results or skip >= total:
                break
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 접속 불가로 표면화
        raise SourceFetchError(safe_error(exc)) from None
    return FetchOutcome(docs=_dedupe(d for d in docs if in_window(d.published, start, end)))


def fetch_fr(start: date, end: date) -> FetchOutcome:
    """Federal Register FDA 문서(게시일 창) 전수 — 페이지 상한 없이 next_page_url 을 끝까지 따른다(30쪽 방어)."""
    import collect_intake as ci
    params: dict[str, Any] = {
        "conditions[agencies][]": "food-and-drug-administration",
        "conditions[publication_date][gte]": start.isoformat(),
        "conditions[publication_date][lte]": end.isoformat(),
        "per_page": ci.FR_PER_PAGE, "order": "newest",
    }
    next_url: str | None = ci.FR_API_BASE + "?" + urllib.parse.urlencode(params, doseq=True)
    docs: list[SourceDoc] = []
    try:
        for _page in range(30):
            if not next_url:
                break
            data = ci.http_get_json(next_url)
            for r in data.get("results", []) or []:
                it = ci._fr_to_item(r, "reconcile")
                docs.append(SourceDoc(
                    document_id=_effective_id(it.document_id, it.official_url, it.headline, it.date_iso),
                    url=it.official_url, title=it.headline, published=it.date_iso))
            next_url = data.get("next_page_url")
    except Exception as exc:  # noqa: BLE001
        raise SourceFetchError(safe_error(exc)) from None
    return FetchOutcome(docs=_dedupe(docs))


def fetch_mfds_rss(start: date, end: date) -> FetchOutcome:
    """식약처 RSS 보드(수집기가 실제로 받는 보드 = `_configured_rss_boards()`) 전부. 관련성 필터는 거치지 않는다."""
    import collect_mfds as m
    from collect_intake import _atom_entries_from_root, _rss2_items_from_root
    from grm_common import http_get_xml
    docs: list[SourceDoc] = []
    notes: list[str] = []
    failed: list[str] = []
    undated = 0
    boards = m._configured_rss_boards()
    for brd_id, _type in boards:
        feed_url = f"{m.MFDS_RSS_BASE}?brdId={brd_id}"
        try:
            root = http_get_xml(feed_url)
        except Exception as exc:  # noqa: BLE001
            failed.append(brd_id)
            notes.append(f"{brd_id}: {safe_error(exc)}")
            continue
        rss_items = _rss2_items_from_root(root)
        use_atom = not rss_items
        nodes = _atom_entries_from_root(root) if use_atom else rss_items
        for node in nodes:
            title, link, date_iso, _desc = m._atom_fields(node) if use_atom else m._rss2_fields(node)
            if not date_iso:
                undated += 1
                continue
            if not in_window(date_iso, start, end):
                continue
            docs.append(SourceDoc(document_id=m._doc_id(brd_id, link, title, date_iso),
                                  url=link, title=title, published=date_iso))
    if boards and len(failed) == len(boards):
        raise SourceFetchError("; ".join(notes)[:300] or "MFDS RSS 전 보드 실패")
    partial = bool(failed)
    if partial:
        notes.insert(0, f"보드 {len(failed)}/{len(boards)} 실패({', '.join(failed)}) — 목록이 전수가 아닐 수 있다")
    return FetchOutcome(docs=_dedupe(docs), partial=partial, notes=notes, undated=undated)


FETCHERS: dict[str, Callable[[date, date], FetchOutcome]] = {
    "fda483": fetch_fda483, "wl": fetch_wl, "recall": fetch_recall,
    "mfds_rss": fetch_mfds_rss, "fr": fetch_fr,
}


def fetch_all(specs: Iterable[SourceSpec], windows: dict[str, tuple[date, date]],
              fetchers: dict[str, Callable[[date, date], FetchOutcome]] | None = None,
              env: dict[str, str] | None = None) -> dict[str, FetchOutcome]:
    """소스별로 목록을 받는다. 한 소스의 실패가 다른 소스를 막지 않는다(graceful)."""
    fetchers = fetchers or FETCHERS
    env = os.environ if env is None else env
    out: dict[str, FetchOutcome] = {}
    for spec in specs:
        if spec.flag and not _flag(env, spec.flag, spec.flag_default):
            out[spec.key] = FetchOutcome(enabled=False)
            continue
        start, end = windows[spec.key]
        try:
            out[spec.key] = fetchers[spec.key](start, end)
        except SourceFetchError as exc:
            out[spec.key] = FetchOutcome(error=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            out[spec.key] = FetchOutcome(error=safe_error(exc))
    return out


def _flag(env: Any, name: str, default: bool) -> bool:
    val = str(env.get(name) or "").strip().lower()
    return default if not val else val in ("1", "true", "yes", "on")


# ─────────────────────────────────────────────────────────────────────────────
# 저장소 읽기 — 수집됨(Notion, 수집기 dedup 과 같은 조회) · 제외 장부(Supabase, 읽기 전용)
# ─────────────────────────────────────────────────────────────────────────────
def load_collected(run_date: date, window_days: int, sources: set[str],
                   token: str, db_id: str) -> set[str]:
    """수집기 dedup 과 **같은 함수**로 `source::document_id` 집합을 만든다. 실패하면 StoreReadError.

    Run Date(수집일) 기준 창이다. 수집일은 게시일 이후이므로 게시 창을 덮는 창이면 충분하다."""
    from grm_notion import NotionDedupeQueryError, notion_query_existing_keys
    try:
        keys = notion_query_existing_keys(token, db_id, run_date, window_days=window_days,
                                          source_names=sources)
    except NotionDedupeQueryError as exc:
        raise StoreReadError(f"Notion 중복 조회 실패: {safe_error(exc)}") from None
    except Exception as exc:  # noqa: BLE001
        raise StoreReadError(f"Notion 조회 실패: {type(exc).__name__}") from None
    return keys.doc_ids


def load_ledger(base_url: str, service_key: str, sources: Iterable[str],
                session: Any = None) -> dict[str, dict[str, LedgerRow]]:
    """제외 장부 읽기(소스 → document_id → 행). 실패하면 StoreReadError(키·예외 문구는 싣지 않는다)."""
    http = session or requests
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    url = f"{base_url.rstrip('/')}/rest/v1/intake_exclusion"
    out: dict[str, dict[str, LedgerRow]] = {}
    for source in sources:
        rows_by_id: dict[str, LedgerRow] = {}
        offset = 0
        for _page in range(60):
            params = {"select": "document_id,basis,reason_code,first_seen,last_seen",
                      "source": f"eq.{source}", "order": "document_id.asc",
                      "limit": "1000", "offset": str(offset)}
            try:
                resp = http.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            except requests.exceptions.RequestException as exc:
                raise StoreReadError(f"제외 장부 조회 실패: {type(exc).__name__}") from None
            if resp.status_code >= 400:
                raise StoreReadError(f"제외 장부 조회 HTTP {resp.status_code}")
            try:
                page = resp.json()
            except ValueError:
                raise StoreReadError("제외 장부 응답 JSON 아님") from None
            page = page if isinstance(page, list) else []
            for r in page:
                if not isinstance(r, dict) or not r.get("document_id"):
                    continue
                rows_by_id[str(r["document_id"])] = LedgerRow(
                    basis=str(r.get("basis") or "text"), reason_code=str(r.get("reason_code") or ""),
                    first_seen=str(r.get("first_seen") or ""), last_seen=str(r.get("last_seen") or ""))
            if len(page) < 1000:
                break
            offset += 1000
        else:
            raise StoreReadError("제외 장부가 상한(60,000행)을 넘어 절단됐다")
        out[source] = rows_by_id
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 보고서
# ─────────────────────────────────────────────────────────────────────────────
def build_report(
    *, run_date: date, specs: Iterable[SourceSpec], outcomes: dict[str, FetchOutcome],
    windows: dict[str, tuple[date, date]], collected: set[str] | None, store_error: str,
    ledger: dict[str, dict[str, LedgerRow]] | None, ledger_error: str,
    run_url: str = "",
) -> dict[str, Any]:
    results: list[SourceResult] = []
    for spec in specs:
        results.append(reconcile_source(
            spec, outcomes[spec.key], run_date=run_date, window=windows[spec.key],
            collected=collected, store_error=store_error,
            ledger=None if ledger is None else ledger.get(spec.source, {}),
            ledger_error=ledger_error))
    return {
        "schema": "grm-source-reconcile/v1",
        "run_date": run_date.isoformat(),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "ledger_active_from": LEDGER_ACTIVE_FROM.isoformat(),
        "pre_ledger_until": (LEDGER_ACTIVE_FROM + timedelta(days=PRE_LEDGER_GRACE_DAYS)).isoformat(),
        "deferred_max_days": DEFERRED_MAX_DAYS,
        "store": {"collected_ok": collected is not None, "collected_error": store_error,
                  "ledger_ok": ledger is not None, "ledger_error": ledger_error,
                  "collected_keys": None if collected is None else len(collected)},
        "sources": [r.as_dict() for r in results],
        "totals": {"missed": sum(r.counts["missed"] for r in results),
                   "sources_missed": sum(1 for r in results if r.verdict == V_MISSED),
                   "verdicts": {r.key: r.verdict for r in results}},
        "issues": plan_issues(results, run_url=run_url),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# 원천 대조 감시 ({report['run_date']} 기준)", "",
             "원천 목록 = 수집 + 의도적 제외(제외 장부) + 미뤄짐. 어디에도 없으면 놓침.", "",
             "| 소스 | 판정 | 창 | 원천 | 수집 | 제외 | 미뤄짐 | 장부 이전 | 놓침 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in report["sources"]:
        c = s["counts"]
        lines.append(f"| {s['label']} | {s['verdict_ko']} | {s['window']['start']}~{s['window']['end']} "
                     f"| {c['listed']} | {c['collected']} | {c['excluded']} | {c['deferred']} "
                     f"| {c['pre_ledger']} | {c['missed']} |")
    st = report["store"]
    if not st["collected_ok"]:
        lines += ["", f"- 수집됨 조회 불가: {st['collected_error']}"]
    if not st["ledger_ok"]:
        lines += ["", f"- 제외 장부 조회 불가: {st['ledger_error']}"]
    for s in report["sources"]:
        extra: list[str] = []
        if s["error"]:
            extra.append(f"사유: {s['error']}")
        extra += s["notes"]
        if s["counts"]["deferred_recent"]:
            extra.append(f"미뤄짐 {s['counts']['deferred']}건 중 {s['counts']['deferred_recent']}건은 게시 {report['deferred_max_days']}일 이내 "
                         "미시도(recent_not_yet_attempted) — 기한을 넘기면 놓침으로 승격")
        if s["pre_ledger"]:
            extra.append(f"장부 이전 게시분 {len(s['pre_ledger'])}건은 {report['pre_ledger_until']} 까지 놓침으로 세지 않는다")
        if extra:
            lines += ["", f"### {s['label']}"] + [f"- {e}" for e in extra]
        if s["missed"]:
            lines += ["", f"**놓침 {len(s['missed'])}건** — {s['label']}", "",
                      "| 게시일 | document_id | 제목 | 원천 URL | 사유 |", "|---|---|---|---|---|"]
            for m in s["missed"][:MAX_LISTED_IN_ISSUE]:
                lines.append(f"| {m['published']} | `{m['document_id']}` | {_md_cell(m['title'])} | {m['url']} "
                             f"| {_md_cell(m.get('detail') or m.get('reason', ''), 70)} |")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────
def kst_today() -> date:
    return datetime.now(KST).date()


def _int_env(env: Any, name: str, default: int) -> int:
    try:
        return int(str(env.get(name) or "").strip() or default)
    except ValueError:
        return default


def compute_windows(specs: Iterable[SourceSpec], run_date: date, base_days: int,
                    enf_days: int) -> dict[str, tuple[date, date]]:
    return {s.key: window_bounds(s, run_date, base_days, enf_days) for s in specs}


def run(*, run_date: date, base_days: int, enf_days: int, specs: Iterable[SourceSpec] = SPECS,
        fetchers: dict[str, Callable[[date, date], FetchOutcome]] | None = None,
        env: dict[str, str] | None = None, use_store: bool = True,
        run_url: str = "") -> dict[str, Any]:
    env = dict(os.environ) if env is None else env
    specs = tuple(specs)
    windows = compute_windows(specs, run_date, base_days, enf_days)
    outcomes = fetch_all(specs, windows, fetchers, env)

    collected: set[str] | None = None
    ledger: dict[str, dict[str, LedgerRow]] | None = None
    store_error = ledger_error = ""
    token = str(env.get("NOTION_TOKEN") or "").strip()
    db_id = str(env.get("NOTION_DATABASE_ID") or "").strip()
    sb_url = str(env.get("SUPABASE_URL") or "").strip()
    sb_key = str(env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    names = {s.source for s in specs}
    if not use_store:
        store_error = ledger_error = "저장소 대조 생략(--no-store)"
    else:
        if not token or not db_id:
            store_error = "NOTION_TOKEN/NOTION_DATABASE_ID 미설정"
        else:
            widest = max((end - start).days for start, end in windows.values())
            try:
                collected = load_collected(run_date, widest + NOTION_DEDUP_MARGIN_DAYS, names, token, db_id)
            except StoreReadError as exc:
                store_error = str(exc)
        if not sb_url.lower().startswith("https://") or not sb_key:
            ledger_error = "SUPABASE_URL(https)/SUPABASE_SERVICE_ROLE_KEY 미설정"
        else:
            try:
                ledger = load_ledger(sb_url, sb_key, names)
            except StoreReadError as exc:
                ledger_error = str(exc)
    return build_report(run_date=run_date, specs=specs, outcomes=outcomes, windows=windows,
                        collected=collected, store_error=store_error, ledger=ledger,
                        ledger_error=ledger_error, run_url=run_url)


def _emit(line: str) -> None:
    print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="GRM 원천 대조 감시(원천 = 수집 + 제외 + 미뤄짐)")
    ap.add_argument("--window-days", type=int, default=None, choices=range(1, 91), metavar="N(1-90)",
                    help="기본 창 일수(기본 7). 지연공개 소스(483·회수)는 max(N, MFDS_ENFORCEMENT_WINDOW_DAYS=30)")
    ap.add_argument("--sources", nargs="+", choices=sorted(SPEC_BY_KEY), default=None)
    ap.add_argument("--json", default="source-reconcile.json")
    ap.add_argument("--report", default="source-reconcile.md")
    ap.add_argument("--no-store", action="store_true", help="Notion·제외 장부를 읽지 않고 원천 건수만 센다")
    ap.add_argument("--run-url", default="")
    args = ap.parse_args(argv)

    env = dict(os.environ)
    base = args.window_days or _int_env(env, "GRM_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)
    enf = max(base, _int_env(env, "MFDS_ENFORCEMENT_WINDOW_DAYS", DEFAULT_ENF_WINDOW_DAYS))
    specs = tuple(SPEC_BY_KEY[k] for k in args.sources) if args.sources else SPECS
    run_date = kst_today()
    _emit(f"원천 대조 시작 run_date(KST)={run_date} 기본창={base}일 지연공개창={enf}일 소스={[s.key for s in specs]}")
    report = run(run_date=run_date, base_days=base, enf_days=enf, specs=specs, env=env,
                 use_store=not args.no_store, run_url=args.run_url)
    md = render_markdown(report)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(md)
    _emit(md)
    for s in report["sources"]:
        if s["verdict"] == V_MISSED:
            _emit(f"::warning title=source-reconcile {s['key']}::{s['source']} 원천 대비 놓침 {s['counts']['missed']}건")
        elif s["verdict"] in (V_UNREACHABLE, V_STORE_UNREACHABLE):
            _emit(f"::notice title=source-reconcile {s['key']}::{s['source']} {s['verdict_ko']}: {s['error']}")
    return 0     # 비차단 — 판정은 JSON 과 이슈 계획이 말한다


if __name__ == "__main__":
    sys.exit(main())
