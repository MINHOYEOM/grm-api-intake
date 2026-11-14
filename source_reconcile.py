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
# 2026-11 확장에서 **새로 장부를 연결한** 소스(MHRA 경보·ISPE·HC 회수·WHO RSS·식약처 행정처분)의 시작일.
# 연결 PR 이 머지돼 다음 수집이 돈 날부터 장부가 쌓이므로 그 이전 게시분은 위와 같은 유예를 받는다.
# 머지가 늦어지면 이 날짜를 머지 다음 날(KST)로 맞춘다 — 시험은 '기존 날짜 이후'만 잠근다.
LEDGER_EXPANSION_FROM = date(2026, 11, 10)
PRE_LEDGER_GRACE_DAYS = 14           # 이 기간이 지나면 pre_ledger 칸은 쓰지 않는다(모두 놓침으로 센다)
DEFERRED_MAX_DAYS = 3                # 미뤄짐이 이 일수를 넘어 같은 문서에 남으면 놓침으로 승격
DEFAULT_WINDOW_DAYS = 7              # collect_intake --window-days 기본값
DEFAULT_ENF_WINDOW_DAYS = 30         # MFDS_ENFORCEMENT_WINDOW_DAYS 기본값(지연공개 소스의 창)
NOTION_DEDUP_MARGIN_DAYS = 3         # Notion Run Date 조회 창 여유(창 시작 이전 수집분 흡수)
SNAPSHOT_DEDUP_DAYS = 1095           # collect_intake `_SNAPSHOT_DEDUP_WINDOW_DAYS`(ICH·WHO 3년 dedup)와 같은 값
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
WINDOW_SNAPSHOT = "snapshot"   # 게시일 창이 아니라 "원천 현재 전체 목록 vs 우리 전체(3년)" (ICH·WHO 실사)

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
    issue_name: str = ""          # 이슈 제목의 소스 이름 — 같은 Notion source 를 나눠 쓰는 소스(MHRA·MFDS·WHO)만
    url_dedup: bool = False       # 수집기가 정규화 URL 로도 중복을 막는 소스(ECA·ISPE) — URL 키 일치도 수집됨
    ledger_from: date | None = None   # 제외 장부가 이 소스에 연결된 날(없으면 LEDGER_ACTIVE_FROM)

    @property
    def issue_source(self) -> str:
        return self.issue_name or self.source


SPECS: tuple[SourceSpec, ...] = (
    SourceSpec("fda483", "FDA 483", "FDA 483 (OII 리딩룸)", "fda-483", WINDOW_ENF,
               flag="ENABLE_FDA_483"),
    SourceSpec("wl", "FDA Warning Letter", "FDA 경고서한", "fda-wl", WINDOW_BASE),
    SourceSpec("recall", "OpenFDA Recall", "OpenFDA 의약품 회수", "openfda-recall", WINDOW_ENF),
    SourceSpec("mfds_rss", "MFDS", "식약처 RSS 보드", "mfds-rss", WINDOW_BASE, flag="ENABLE_MFDS"),
    SourceSpec("fr", "Federal Register", "Federal Register (FDA)", "federal-register", WINDOW_BASE),
    # ── 2026-11 확장: 유럽·국제 ───────────────────────────────────────────────────
    # 수집기 창·플래그·기본값은 grm-intake.yml / collect_intake.main 과 같게 둔다.
    SourceSpec("ema", "EMA", "EMA RSS(4개 피드)", "ema", WINDOW_BASE),
    SourceSpec("mhra_blog", "MHRA Inspectorate", "MHRA 실사관 블로그", "mhra-blog", WINDOW_BASE,
               issue_name="MHRA Inspectorate 블로그"),
    SourceSpec("mhra_alert", "MHRA Inspectorate", "MHRA 의약품 경보·회수", "mhra-alert", WINDOW_BASE,
               issue_name="MHRA 의약품 경보·회수", ledger_from=LEDGER_EXPANSION_FROM),
    SourceSpec("mhra_ncr", "MHRA GMP NCR", "MHRA GMP 부적합(GMDP)", "mhra-gmp-ncr", WINDOW_ENF,
               flag="ENABLE_MHRA_GMP_NCR", flag_default=False),
    SourceSpec("eu_ncr", "EU GMP NCR (EudraGMDP)", "EU GMP 부적합(EudraGMDP)", "eu-gmp-ncr", WINDOW_ENF,
               flag="ENABLE_EU_GMP_NCR", flag_default=False),
    SourceSpec("pics", "PIC/S", "PIC/S RSS", "pics", WINDOW_BASE),
    SourceSpec("eca", "ECA Academy", "ECA Academy RSS", "eca", WINDOW_BASE, url_dedup=True),
    SourceSpec("ispe", "ISPE", "ISPE iSpeak RSS", "ispe", WINDOW_ENF, flag="ENABLE_ISPE",
               flag_default=False, url_dedup=True, ledger_from=LEDGER_EXPANSION_FROM),
    SourceSpec("ich", "ICH", "ICH 가이드라인·의견수렴(스냅샷)", "ich", WINDOW_SNAPSHOT,
               flag="ENABLE_ICH", flag_default=False),
    SourceSpec("who_rss", "WHO", "WHO PQ RSS", "who-rss", WINDOW_BASE, flag="ENABLE_WHO",
               flag_default=False, issue_name="WHO PQ RSS", ledger_from=LEDGER_EXPANSION_FROM),
    SourceSpec("who_insp", "WHO", "WHO 실사 보고서·우려 공지(스냅샷)", "who-inspection", WINDOW_SNAPSHOT,
               flag="ENABLE_WHO", flag_default=False, issue_name="WHO WHOPIR·NOC"),
    # ── 2026-11 확장: 식약처 API·HC 회수 ─────────────────────────────────────────
    # HC 실사(Health Canada Inspection)는 넣지 않았다 — fetch_hc_recall 도크스트링 참조(원천 이전 중).
    SourceSpec("hc_recall", "Health Canada", "Health Canada 회수·경고", "hc-recall", WINDOW_ENF,
               flag="ENABLE_HC", flag_default=False, ledger_from=LEDGER_EXPANSION_FROM),
    SourceSpec("mfds_recall", "MFDS", "식약처 회수·판매중지(API)", "mfds-recall", WINDOW_ENF,
               flag="ENABLE_MFDS_RECALL", issue_name="MFDS 회수·판매중지"),
    SourceSpec("mfds_admin", "MFDS", "식약처 행정처분(API)", "mfds-admin", WINDOW_ENF,
               flag="ENABLE_MFDS_ADMIN", issue_name="MFDS 행정처분", ledger_from=LEDGER_EXPANSION_FROM),
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
    """수집기와 같은 창: start = run_date - N일, end = run_date(`date_window`·`enf_start` 와 동일).

    스냅샷 소스는 게시일 창이 없다 — 우리 쪽 조회 범위(수집기 스냅샷 dedup 3년)를 창으로 표시한다."""
    if spec.window_kind == WINDOW_SNAPSHOT:
        return run_date - timedelta(days=SNAPSHOT_DEDUP_DAYS), run_date
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


def _is_pre_ledger(published: str, run_date: date, active_from: date = LEDGER_ACTIVE_FROM) -> bool:
    """장부 연결 전 게시분이고 아직 유예 기간(첫 2주) 안인가. `active_from` 은 소스별 장부 연결일."""
    if run_date >= active_from + timedelta(days=PRE_LEDGER_GRACE_DAYS):
        return False
    try:
        return date.fromisoformat(published) < active_from
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
    collected_urls: set[str] | None = None,
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
        if spec.url_dedup and collected_urls and doc.url:
            # 같은 기사가 날짜만 바꿔 재게시되면 키가 달라진다 — 수집기는 정규화 URL 로 중복을 막는다
            # (`collect_intake._CANONICAL_URL_DEDUP_SOURCES`). 같은 기준으로 '이미 있음'을 센다.
            from grm_notion import canonical_url_key
            if canonical_url_key(spec.source, doc.url) in collected_urls:
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
        if _is_pre_ledger(doc.published, run_date, spec.ledger_from or LEDGER_ACTIVE_FROM):
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
        name = SPEC_BY_KEY[r.key].issue_source if r.key in SPEC_BY_KEY else r.source
        base = {"key": r.key, "source": r.source, "title": issue_title(name),
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


def rss_docs_from_root(feed_spec: Any, root: Any, feed_name: str, feed_url: str,
                       start: date, end: date) -> tuple[list[SourceDoc], int]:
    """RSS/Atom 루트 → 창 안 문서. 반복자·필드 추출·문서 키는 `collect_rss_feed` 와 같은 함수.

    수집기의 `keep_item`(MHRA 의료기기 경보·ISPE 홍보성 글 거르기)은 **적용하지 않는다** — 그 필터가
    버린 항목은 제외 장부(`not_medicines_alert`·`not_gmp_relevant`)가 설명한다."""
    import collect_intake as ci
    docs: list[SourceDoc] = []
    undated = 0
    for el in feed_spec.iter_items(root):
        f = feed_spec.extract(el, feed_name, feed_url)
        if not f.date_iso:
            undated += 1
            continue
        if not ci._within_window(f.date_iso, start, end):
            continue
        docs.append(SourceDoc(document_id=ci._stable_doc_id(feed_spec.source, f.title, f.link, f.date_iso),
                              url=f.link, title=f.title, published=f.date_iso))
    return docs, undated


def fetch_rss_spec(feed_spec: Any, start: date, end: date) -> FetchOutcome:
    """`RssFeedSpec` 하나(EMA·MHRA·PIC/S·ECA·ISPE)의 피드 전부. 피드 일부 실패는 partial, 전부 실패는 접속 불가.

    ECA·ISPE 수집기는 HTTP 4xx 를 조용히 건너뛰지만(Expert Secondary 정책) 대조에서는 접속 불가로 드러낸다."""
    import collect_intake as ci
    docs: list[SourceDoc] = []
    notes: list[str] = []
    failed: list[str] = []
    undated = 0
    for feed_name, feed_url in feed_spec.feeds:
        try:
            root = ci.http_get_xml(feed_url)
        except Exception as exc:  # noqa: BLE001
            failed.append(feed_name)
            notes.append(f"{feed_name}: {safe_error(exc)}")
            continue
        got, n_undated = rss_docs_from_root(feed_spec, root, feed_name, feed_url, start, end)
        docs.extend(got)
        undated += n_undated
    if len(failed) == len(feed_spec.feeds):
        raise SourceFetchError("; ".join(notes)[:300] or f"{feed_spec.label} 전 피드 실패")
    partial = bool(failed)
    if partial:
        notes.insert(0, f"피드 {len(failed)}/{len(feed_spec.feeds)} 실패({', '.join(failed)}) — 목록이 전수가 아닐 수 있다")
    return FetchOutcome(docs=_dedupe(docs), partial=partial, notes=notes, undated=undated)


def _rss_fetcher(attr: str) -> Callable[[date, date], FetchOutcome]:
    def _fetch(start: date, end: date) -> FetchOutcome:
        import collect_intake as ci
        return fetch_rss_spec(getattr(ci, attr), start, end)
    _fetch.__name__ = f"fetch_rss_{attr.strip('_').lower()}"
    return _fetch


def fetch_mhra_gmp_ncr(start: date, end: date) -> FetchOutcome:
    """MHRA GMDP 비준수 성명서 전체 목록 → 상세(발행일) → 창. 클라이언트·키 식(`report_no or slug`)은 수집기 것.

    수집기에는 관련성 필터가 없다(비준수 성명서는 전건 수집). 상세 실패는 수집기가 조용히 건너뛰는 지점이라
    여기서는 점검일로 창을 판정해 목록에 남긴다 — 수집되지 않았으면 놓침으로 드러난다."""
    from mhra_gmdp_client import MHRAGmpError, MHRAGmpNCRClient
    client = MHRAGmpNCRClient()
    try:
        records = client.list_noncompliant()
    except MHRAGmpError as exc:
        raise SourceFetchError(safe_error(exc)) from None
    docs: list[SourceDoc] = []
    notes: list[str] = []
    detail_failed = 0
    for rec in records:
        key = rec.report_no or rec.slug
        if not key:
            continue
        try:
            client.fetch_detail(rec)
        except Exception:  # noqa: BLE001 — 상세 실패는 점검일로 창 판정
            detail_failed += 1
        date_iso = rec.issue_date or rec.inspection_date or ""
        if not in_window(date_iso, start, end):
            continue
        docs.append(SourceDoc(document_id=key, url=rec.detail_url,
                              title=getattr(rec, "manufacturer", "") or key, published=date_iso))
    notes.append(f"비준수 목록 전체 {len(records)}건")
    if detail_failed:
        notes.append(f"상세 실패 {detail_failed}건은 점검일로 창 판정")
    return FetchOutcome(docs=_dedupe(docs), notes=notes)


def fetch_eu_gmp_ncr(start: date, end: date) -> FetchOutcome:
    """EudraGMDP 비준수 보고서 — 서버 측 발행일 창 검색(`list_window`) 결과 전부. 키 = doc_ref(수집기와 같다)."""
    from eudragmdp_client import EudraGMDPClient, EudraGMDPError
    try:
        records = EudraGMDPClient().list_window(start.isoformat(), end.isoformat())
    except EudraGMDPError as exc:
        raise SourceFetchError(safe_error(exc)) from None
    docs = [SourceDoc(document_id=r.doc_ref, url="", title=r.site_name or r.report_no,
                      published=r.issue_date or "")
            for r in records if r.doc_ref]
    return FetchOutcome(docs=_dedupe(docs))


def hc_docs_from_records(records: Iterable[Any], start: date, end: date) -> tuple[list[SourceDoc], int]:
    """HC 회수 오픈데이터 레코드 → 창 안 문서(피드 'Last updated' 기준 — 수집기 창과 같다).

    수집기의 기관·Category 범위 규칙과 정정 건너뛰기는 **적용하지 않는다**(제외 장부가 설명한다).
    NID 가 없는 레코드는 키가 없어 세지 않는다(수집기도 장부에 못 남긴다) — 개수만 돌려준다."""
    import collect_hc as hc
    docs: list[SourceDoc] = []
    no_nid = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        feed_date = hc._feed_date(rec)
        if not in_window(feed_date, start, end):
            continue
        nid = hc._text(rec, "NID")
        if not nid:
            no_nid += 1
            continue
        rel = hc._text(rec, "URL")
        url = urllib.parse.urljoin(hc.HC_BASE, rel) if rel else hc.HC_BASE
        docs.append(SourceDoc(document_id=f"hc-{nid}", url=url, title=hc._text(rec, "Title"),
                              published=feed_date))
    return _dedupe(docs), no_nid


def fetch_hc_recall(start: date, end: date) -> FetchOutcome:
    """Health Canada 회수·안전 경고 오픈데이터(JSON 배열 한 벌) 전체 → 창.

    ★HC **실사**(drug-inspections.canada.ca, `Health Canada Inspection`)는 대상이 아니다 — 옛 서버가
    2026-10-31 부터 누구에게도 응답하지 않고 새 도메인으로 이전 중이다(GMP 경로 미개설, PR #1373 이 이전
    감지 경보를 붙였다). 원천 목록 자체를 못 받는 동안은 대조할 수 없어 넣지 않았다. 새 경로가 열리면 추가한다."""
    import collect_hc as hc
    from grm_common import http_get_json
    try:
        data = http_get_json(hc.HC_OPENDATA_URL, timeout=60, retries=2)
    except Exception as exc:  # noqa: BLE001
        raise SourceFetchError(safe_error(exc)) from None
    if not isinstance(data, list) or not data:
        raise SourceFetchError("HC 오픈데이터 형식 이상(배열 아님/0레코드)")
    docs, no_nid = hc_docs_from_records(data, start, end)
    notes = [f"피드 전체 {len(data)}레코드"]
    if no_nid:
        notes.append(f"NID 없는 창 안 레코드 {no_nid}건은 키가 없어 세지 않았다")
    return FetchOutcome(docs=docs, notes=notes)


def fetch_ich(start: date, end: date) -> FetchOutcome:
    """ICH 가이드라인·의견수렴 페이지 3개의 **현재 전체 목록**(스냅샷). 창은 쓰지 않는다.

    ICH 는 게시일이 없는 정적 카탈로그라 '창 안 문서'가 없다. 수집기의 섹션 제목 추출
    (`_candidate_titles`)은 관련성 필터가 아니라 목록 파서 자체라 그대로 쓴다(파서를 따로 만들면 키가
    갈라진다). 우리 쪽은 수집기의 스냅샷 dedup 과 같은 3년 창으로 읽는다."""
    import collect_ich as ich
    docs: list[SourceDoc] = []
    notes: list[str] = []
    failed_required: list[str] = []
    for slug, type_or_class, required in ich.ICH_PAGES:
        try:
            html_text = ich._get_html(ich.ADMIN_BASE + slug)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{slug}: {safe_error(exc)}")
            if required:
                failed_required.append(slug)
            continue
        titles = ich._candidate_titles(ich._text_blocks(html_text))
        if required and not titles:
            failed_required.append(slug)
            notes.append(f"{slug}: 섹션 0건 — 구조 변경 의심")
        for title in titles:
            headline = title if type_or_class == ich.TYPE_ICH_GUIDELINE else f"[Public Consultation] {title}"
            docs.append(SourceDoc(document_id=ich._document_id(slug, title), url=ich.PUBLIC_BASE + slug,
                                  title=headline, published=""))
    if len(failed_required) == sum(1 for _s, _t, r in ich.ICH_PAGES if r):
        raise SourceFetchError("; ".join(notes)[:300] or "ICH 핵심 페이지 전부 실패")
    return FetchOutcome(docs=_dedupe(docs), partial=bool(failed_required), notes=notes)


def who_rss_docs_from_root(root: Any, start: date, end: date) -> tuple[list[SourceDoc], int]:
    """WHO PQ RSS 루트 → 창 안 문서. 필드 해석·키(`_stable_doc_id(WHO, 제목, 링크, 날짜)`)는 `_collect_rss` 와 같다.

    수집기의 범위 밖 프로그램 제외(`_excluded`)는 적용하지 않는다 — 제외 장부(`out_of_scope_program`)가 설명한다."""
    import collect_intake as ci
    import collect_who as who
    rss_items = ci._rss2_items_from_root(root)
    use_atom = not rss_items
    nodes = ci._atom_entries_from_root(root) if use_atom else rss_items
    docs: list[SourceDoc] = []
    undated = 0
    for node in nodes:
        if use_atom:
            title = ci._atom_text(node, "title")
            link = ci._atom_link(node)
            pub = ci._atom_text(node, "updated") or ci._atom_text(node, "published")
            date_iso = ci._parse_atom_date(pub) if pub else ""
        else:
            title = ci._rss_text(node.find("title"))
            link = ci._rss_text(node.find("link"))
            pub = ci._rss_text(node.find("pubDate")) or ci._rss_text(node.find("pubdate"))
            date_iso = ci._parse_rss2_date(pub) if pub else ""
        title = who._clean(title)
        if not title:
            continue
        if not date_iso:
            undated += 1
            continue
        if not in_window(date_iso, start, end):
            continue
        docs.append(SourceDoc(document_id=ci._stable_doc_id(ci.SOURCE_WHO, title, link, date_iso),
                              url=link, title=title, published=date_iso))
    return _dedupe(docs), undated


def fetch_who_rss(start: date, end: date) -> FetchOutcome:
    """WHO 사전적격심사(PQ) RSS — 창 안 전부."""
    import collect_who as who
    from grm_common import http_get_xml
    try:
        root = http_get_xml(who.WHO_RSS_URL)
    except Exception as exc:  # noqa: BLE001
        raise SourceFetchError(safe_error(exc)) from None
    docs, undated = who_rss_docs_from_root(root, start, end)
    return FetchOutcome(docs=docs, undated=undated)


def fetch_who_inspections(start: date, end: date) -> FetchOutcome:
    """WHO 공개 실사 보고서(WHOPIR)·우려 공지(NOC)의 **현재 전체 목록**(스냅샷) — 창은 쓰지 않는다.

    두 채널은 수집기에도 창·관련성 필터가 없다(매 실행 전체 목록을 받아 3년 dedup 으로 거른다). 그래서
    수집기의 목록 함수(`_collect_whopir`·`_collect_noc`)를 그대로 부른다 — 키 식이 갈라질 여지가 없다.
    WHOPIR PDF 본문(ENABLE_WHOPIR_EXCERPT)은 이 잡에서 켜지 않으므로 목록 페이지만 받는다."""
    import collect_who as who
    docs: list[SourceDoc] = []
    notes: list[str] = []
    errors: list[str] = []
    for label, fn in (("WHOPIR", who._collect_whopir), ("NOC", who._collect_noc)):
        try:
            items, err = fn(end)
        except Exception as exc:  # noqa: BLE001
            items, err = [], safe_error(exc)
        if err:
            errors.append(label)
            notes.append(f"{label}: {str(err)[:150]}")
        for it in items:
            docs.append(SourceDoc(document_id=it.document_id, url=it.official_url,
                                  title=it.headline, published=it.date_iso or ""))
        notes.append(f"{label} 목록 {len(items)}건")
    if len(errors) == 2:
        raise SourceFetchError("; ".join(notes)[:300])
    return FetchOutcome(docs=_dedupe(docs), partial=bool(errors), notes=notes)


def _datago_docs(module: Any, endpoint: str, start: date, end: date, *,
                 extra_params: dict[str, str] | None, title_fields: tuple[str, ...]) -> FetchOutcome:
    """data.go.kr 페이지 API(식약처 회수·행정처분) 전 페이지 → 창. 페이지 반복기·키·날짜 식은 수집기 모듈 것."""
    from grm_common import DatagoPageError, datago_paginate, http_get_json
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        raise SourceFetchError("DATA_GO_KR_SERVICE_KEY 미설정")
    paginator = datago_paginate(endpoint, service_key=key, extract=module._extract_items,
                                http_get=http_get_json, max_pages=module.MAX_PAGES,
                                page_size=module.PAGE_SIZE, extra_params=extra_params)
    docs: list[SourceDoc] = []
    notes: list[str] = []
    partial = False
    undated = 0
    try:
        for raw_items, _masked in paginator:
            for raw in raw_items:
                date_iso = module._item_date(raw)
                if not date_iso:
                    undated += 1
                    continue
                if not in_window(date_iso, start, end):
                    continue
                title = next((module._text(raw, f) for f in title_fields if module._text(raw, f)), "")
                docs.append(SourceDoc(document_id=module._document_id(raw), url=module.DATASET_URL,
                                      title=title, published=date_iso))
    except DatagoPageError as exc:
        if not docs:
            raise SourceFetchError(safe_error(exc)) from None
        partial = True
        notes.append(f"페이지 {getattr(exc, 'page_no', '?')} 실패 — 목록이 전수가 아니다")
    except Exception as exc:  # noqa: BLE001
        raise SourceFetchError(safe_error(exc)) from None
    if getattr(paginator, "truncated", False):
        partial = True
        notes.append(f"페이지 상한({module.MAX_PAGES}) 도달 — 전체 {getattr(paginator, 'total_count', '?')}건 중 일부만 봤다")
    return FetchOutcome(docs=_dedupe(docs), partial=partial, notes=notes, undated=undated)


def fetch_mfds_recall(start: date, end: date) -> FetchOutcome:
    """식약처 의약품 회수·판매중지(data.go.kr). 수집기에는 관련성 필터가 없다(품명 결측만 장부에 남긴다)."""
    import collect_mfds_recall as m
    return _datago_docs(m, m.RECALL_API_ENDPOINT, start, end, extra_params=None,
                        title_fields=("PRDUCT", "ENTRPS"))


def fetch_mfds_admin(start: date, end: date) -> FetchOutcome:
    """식약처 행정처분(data.go.kr, order=Y 는 수집기와 같다). `_is_collectable` 거부분은 제외 장부가 설명한다."""
    import collect_mfds_admin_action as m
    return _datago_docs(m, m.ADMIN_API_ENDPOINT, start, end, extra_params={"order": "Y"},
                        title_fields=("ITEM_NAME", "ADM_DISPS_NAME", "ENTP_NAME"))


FETCHERS: dict[str, Callable[[date, date], FetchOutcome]] = {
    "fda483": fetch_fda483, "wl": fetch_wl, "recall": fetch_recall,
    "mfds_rss": fetch_mfds_rss, "fr": fetch_fr,
    "ema": _rss_fetcher("_EMA_FEED_SPEC"), "mhra_blog": _rss_fetcher("_MHRA_FEED_SPEC"),
    "mhra_alert": _rss_fetcher("_MHRA_ALERT_FEED_SPEC"), "pics": _rss_fetcher("_PICS_FEED_SPEC"),
    "eca": _rss_fetcher("_ECA_FEED_SPEC"), "ispe": _rss_fetcher("_ISPE_FEED_SPEC"),
    "mhra_ncr": fetch_mhra_gmp_ncr, "eu_ncr": fetch_eu_gmp_ncr,
    "ich": fetch_ich, "who_rss": fetch_who_rss, "who_insp": fetch_who_inspections,
    "hc_recall": fetch_hc_recall, "mfds_recall": fetch_mfds_recall, "mfds_admin": fetch_mfds_admin,
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
def load_collected_keys(run_date: date, window_days: int, sources: set[str],
                        token: str, db_id: str) -> tuple[set[str], set[str]]:
    """수집기 dedup 과 **같은 함수**로 (`source::document_id` 집합, `source::정규화URL` 집합)을 만든다.

    Run Date(수집일) 기준 창이다. 수집일은 게시일 이후이므로 게시 창을 덮는 창이면 충분하다.
    실패하면 StoreReadError."""
    from grm_notion import NotionDedupeQueryError, notion_query_existing_keys
    try:
        keys = notion_query_existing_keys(token, db_id, run_date, window_days=window_days,
                                          source_names=sources)
    except NotionDedupeQueryError as exc:
        raise StoreReadError(f"Notion 중복 조회 실패: {safe_error(exc)}") from None
    except Exception as exc:  # noqa: BLE001
        raise StoreReadError(f"Notion 조회 실패: {type(exc).__name__}") from None
    return keys.doc_ids, keys.url_keys


def load_collected(run_date: date, window_days: int, sources: set[str],
                   token: str, db_id: str) -> set[str]:
    """`load_collected_keys` 의 문서 키 쪽(기존 호출부 호환)."""
    return load_collected_keys(run_date, window_days, sources, token, db_id)[0]


def load_collected_keys_supabase(base_url: str, service_key: str, sources: Iterable[str],
                                 session: Any = None) -> tuple[set[str], set[str]]:
    """[노션 은퇴 준비] '수집됨'을 정본(Supabase raw_signals)에서 읽는다 — 그림자 판정용.

    반환은 `load_collected_keys` 와 같은 모양: (`source::document_id` 집합, `source::정규화URL` 집합).
    Notion 조회와 달리 **수집일 창이 없다** — 그 소스의 정본 전체다. 창 밖(오래전)에 받은 문서도
    '수집됨'으로 세므로, Notion 기준에서 놓침으로 보이던 것이 여기서는 수집됨일 수 있다(그쪽이 맞다).
    실패하면 StoreReadError(키·예외 문구는 싣지 않는다).
    """
    from grm_notion import canonical_url_key
    http = session or requests
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    url = f"{base_url.rstrip('/')}/rest/v1/raw_signals"
    ids: set[str] = set()
    urls: set[str] = set()
    for source in sources:
        offset = 0
        for _page in range(100):
            params = {"select": "document_id,official_url", "source": f"eq.{source}",
                      "order": "raw_signal_id.asc", "limit": "1000", "offset": str(offset)}
            try:
                resp = http.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            except requests.exceptions.RequestException as exc:
                raise StoreReadError(f"정본(raw_signals) 조회 실패: {type(exc).__name__}") from None
            if resp.status_code >= 400:
                raise StoreReadError(f"정본(raw_signals) 조회 HTTP {resp.status_code}")
            try:
                page = resp.json()
            except ValueError:
                raise StoreReadError("정본(raw_signals) 응답 JSON 아님") from None
            page = page if isinstance(page, list) else []
            for r in page:
                if not isinstance(r, dict) or not r.get("document_id"):
                    continue
                ids.add(f"{source}::{r['document_id']}")
                key = canonical_url_key(source, str(r.get("official_url") or ""))
                if key:
                    urls.add(key)
            if len(page) < 1000:
                break
            offset += 1000
        else:
            raise StoreReadError("정본(raw_signals)이 상한(100,000행)을 넘어 절단됐다")
    return ids, urls


def shadow_diff(notion_report: dict[str, Any], db_report: dict[str, Any],
                db_keys: int) -> dict[str, Any]:
    """Notion 기준 보고서와 Supabase 기준 보고서의 차이(순수 함수). 판정은 바꾸지 않는다 — 기록만.

    소스마다 '놓침' 문서 집합이 같은지 본다. 다르면 어느 쪽에서만 놓침인지 document_id 를 남긴다.
    """
    db_by_key = {s["key"]: s for s in db_report.get("sources") or []}
    rows: list[dict[str, Any]] = []
    for s in notion_report.get("sources") or []:
        d = db_by_key.get(s["key"])
        if d is None:
            continue
        n_missed = {m["document_id"] for m in s.get("missed") or []}
        d_missed = {m["document_id"] for m in d.get("missed") or []}
        if s["verdict"] == d["verdict"] and n_missed == d_missed \
                and s["counts"]["collected"] == d["counts"]["collected"]:
            continue
        rows.append({
            "key": s["key"], "label": s["label"],
            "verdict_notion": s["verdict"], "verdict_supabase": d["verdict"],
            "collected_notion": s["counts"]["collected"], "collected_supabase": d["counts"]["collected"],
            "missed_notion": len(n_missed), "missed_supabase": len(d_missed),
            "missed_only_notion": sorted(n_missed - d_missed)[:MAX_LISTED_IN_ISSUE],
            "missed_only_supabase": sorted(d_missed - n_missed)[:MAX_LISTED_IN_ISSUE],
        })
    return {"ok": True, "error": "", "collected_keys": db_keys,
            "same": not rows, "sources_differ": len(rows), "differences": rows,
            "missed_total_notion": notion_report["totals"]["missed"],
            "missed_total_supabase": db_report["totals"]["missed"]}


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
    run_url: str = "", collected_urls: set[str] | None = None,
) -> dict[str, Any]:
    results: list[SourceResult] = []
    for spec in specs:
        results.append(reconcile_source(
            spec, outcomes[spec.key], run_date=run_date, window=windows[spec.key],
            collected=collected, store_error=store_error,
            ledger=None if ledger is None else ledger.get(spec.source, {}),
            ledger_error=ledger_error, collected_urls=collected_urls))
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
    sh = report.get("shadow_supabase")
    if sh is not None:
        lines += ["", "## 그림자 — 정본(Supabase) 기준으로 다시 판정 (판정·이슈는 Notion 기준 그대로)"]
        if not sh["ok"]:
            lines += [f"- 비교 못 함: {sh['error']}"]
        elif sh["same"]:
            lines += [f"- 차이 없음 (정본 키 {sh['collected_keys']}건 · 놓침 {sh['missed_total_supabase']}건 동일)"]
        else:
            lines += [f"- 차이 나는 소스 {sh['sources_differ']}개 · 놓침 합계 Notion {sh['missed_total_notion']} / "
                      f"정본 {sh['missed_total_supabase']}", "",
                      "| 소스 | 판정 Notion → 정본 | 수집 Notion / 정본 | 놓침 Notion / 정본 | Notion 에서만 놓침 | 정본에서만 놓침 |",
                      "|---|---|---:|---:|---|---|"]
            for d in sh["differences"]:
                lines.append(
                    f"| {d['label']} | {d['verdict_notion']} → {d['verdict_supabase']} "
                    f"| {d['collected_notion']} / {d['collected_supabase']} "
                    f"| {d['missed_notion']} / {d['missed_supabase']} "
                    f"| {_md_cell(', '.join(d['missed_only_notion']), 60)} "
                    f"| {_md_cell(', '.join(d['missed_only_supabase']), 60)} |")
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
    collected_urls: set[str] | None = None
    ledger: dict[str, dict[str, LedgerRow]] | None = None
    store_error = ledger_error = ""
    token = str(env.get("NOTION_TOKEN") or "").strip()
    db_id = str(env.get("NOTION_DATABASE_ID") or "").strip()
    sb_url = str(env.get("SUPABASE_URL") or "").strip()
    sb_key = str(env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    names = {s.source for s in specs}
    # [노션 끄기 2026-11-13] GRM_INTAKE_NOTION=off 면 '수집됨' 키를 노션이 아니라 정본(raw_signals)에서
    # 읽는다 — 수집기가 노션에 행을 만들지 않으므로 노션 기준이면 전부 '놓침'으로 보인다. 기본(on)은 종전.
    notion_off = str(env.get("GRM_INTAKE_NOTION") or "").strip().lower() == "off"
    if not use_store:
        store_error = ledger_error = "저장소 대조 생략(--no-store)"
    else:
        if notion_off:
            if not sb_url.lower().startswith("https://") or not sb_key:
                store_error = "SUPABASE_URL(https)/SUPABASE_SERVICE_ROLE_KEY 미설정"
            else:
                try:
                    collected, collected_urls = load_collected_keys_supabase(sb_url, sb_key, sorted(names))
                except StoreReadError as exc:
                    collected = collected_urls = None
                    store_error = str(exc)
        elif not token or not db_id:
            store_error = "NOTION_TOKEN/NOTION_DATABASE_ID 미설정"
        else:
            # 창 소스는 가장 넓은 게시 창 + 여유로 한 번, 스냅샷 소스(ICH·WHO)는 수집기 스냅샷 dedup 과
            # 같은 3년 창을 그 소스로만 한정해 한 번 더(전체를 3년으로 넓히면 Notion 페이지 상한에 걸린다).
            windowed = [s for s in specs if s.window_kind != WINDOW_SNAPSHOT]
            snap_names = {s.source for s in specs if s.window_kind == WINDOW_SNAPSHOT}
            try:
                collected, collected_urls = set(), set()
                if windowed:
                    widest = max((windows[s.key][1] - windows[s.key][0]).days for s in windowed)
                    ids, urls = load_collected_keys(run_date, widest + NOTION_DEDUP_MARGIN_DAYS,
                                                    {s.source for s in windowed}, token, db_id)
                    collected |= ids
                    collected_urls |= urls
                if snap_names:
                    ids, urls = load_collected_keys(run_date, SNAPSHOT_DEDUP_DAYS, snap_names, token, db_id)
                    collected |= ids
                    collected_urls |= urls
            except StoreReadError as exc:
                collected = collected_urls = None
                store_error = str(exc)
        if not sb_url.lower().startswith("https://") or not sb_key:
            ledger_error = "SUPABASE_URL(https)/SUPABASE_SERVICE_ROLE_KEY 미설정"
        else:
            try:
                ledger = load_ledger(sb_url, sb_key, names)
            except StoreReadError as exc:
                ledger_error = str(exc)
    report = build_report(run_date=run_date, specs=specs, outcomes=outcomes, windows=windows,
                          collected=collected, store_error=store_error, ledger=ledger,
                          ledger_error=ledger_error, run_url=run_url, collected_urls=collected_urls)
    # [노션 은퇴 준비 2026-11-13] 같은 원천 목록을 **정본(Supabase raw_signals) 기준**으로 한 번 더 판정해
    # 차이를 보고서에 남긴다(그림자). 판정·이슈는 Notion 기준 그대로다 — 위 report 를 건드리지 않는다.
    # 기본 꺼짐(ENABLE_RECONCILE_SUPABASE_SHADOW). 실패해도 본 보고서에는 영향이 없다.
    if notion_off:
        report["collected_basis"] = "supabase"      # 가산 키 — 이 보고서의 '수집됨' 근거가 정본이다
    elif use_store and _flag(env, "ENABLE_RECONCILE_SUPABASE_SHADOW", False):
        report["shadow_supabase"] = _supabase_shadow(
            report, run_date=run_date, specs=specs, outcomes=outcomes, windows=windows,
            ledger=ledger, ledger_error=ledger_error, sb_url=sb_url, sb_key=sb_key, names=names)
    return report


def _supabase_shadow(report: dict[str, Any], *, run_date: date, specs: tuple[SourceSpec, ...],
                     outcomes: dict[str, FetchOutcome], windows: dict[str, tuple[date, date]],
                     ledger: "dict[str, dict[str, LedgerRow]] | None", ledger_error: str,
                     sb_url: str, sb_key: str, names: set[str]) -> dict[str, Any]:
    fail = {"ok": False, "same": None, "sources_differ": None, "differences": []}
    if not report["store"]["collected_ok"] or ledger is None:
        return dict(fail, error="Notion 기준 대조가 성립하지 않아 비교하지 않음")
    if not sb_url.lower().startswith("https://") or not sb_key:
        return dict(fail, error="SUPABASE_URL(https)/SUPABASE_SERVICE_ROLE_KEY 미설정")
    try:
        ids, urls = load_collected_keys_supabase(sb_url, sb_key, sorted(names))
        db_report = build_report(run_date=run_date, specs=specs, outcomes=outcomes, windows=windows,
                                 collected=ids, store_error="", ledger=ledger,
                                 ledger_error=ledger_error, collected_urls=urls)
        return shadow_diff(report, db_report, len(ids))
    except StoreReadError as exc:
        return dict(fail, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — 그림자가 본 감시를 죽이면 안 된다
        return dict(fail, error=f"그림자 판정 실패: {type(exc).__name__}")


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
