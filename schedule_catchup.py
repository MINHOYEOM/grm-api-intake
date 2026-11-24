#!/usr/bin/env python3
"""예약 지각 보완기 — 중요한 예약 워크플로가 '예정 시각 + 허용 지각' 안에 안 돌았으면 대신 workflow_dispatch 한다.

[2026-11-24] GitHub Actions 예약(schedule)은 best-effort 다. 11-22 부터 지각·누락이 커졌다:
  - grm-fda483-body-full-backfill(22:07 KST) 11-22 분이 7시간 늦게, 11-23 분은 이튿날 오전까지 실행 기록 없음.
  - 링크드인 해부 슬롯 PR #1879 가 워치독(grm-stranded-batch-pr) 예약 지연으로 3시간 미머지.
일어나지 않은 실행은 경보를 울리지 않는다. 이 모듈은 대상 목록(TARGETS)의 '가장 최근 예정 시각(슬롯)'마다
그 시각 이후 만들어진 실행이 있는지 보고, 없으면 예약과 같은 인자로 dispatch 한다.

깨우는 경로(grm-schedule-catchup.yml) — 보완기 자신도 GitHub 예약 하나에만 기대지 않는다:
  ① 자기 cron(30분마다) ② workflow_run: GRM CI(push·PR 이벤트로 돈다 — 예약 큐와 무관) 와
  30분·1시간 주기 운영 워크플로 3개의 완료. 클라우드 Routine 은 /actions 403, PC 예약작업은 11-14 로 백업 종료라 쓰지 않는다.

멱등:
  - 슬롯 이후 만들어진 실행(schedule·workflow_dispatch, 진행 중 포함)이 하나라도 있으면 아무것도 안 한다.
    보완 dispatch 로 생긴 실행도 그 슬롯을 덮으므로 같은 슬롯을 두 번 기동하지 않는다.
  - 워크플로 concurrency 그룹으로 보완기 실행끼리 겹치지 않는다. dispatch 뒤 새 실행이 목록에 보일 때까지 잠깐 기다린다.
  - 슬롯이 max_late 보다 오래됐으면 건드리지 않는다(다음 슬롯이 대신한다 — 하루 지난 일을 몰래 돌리지 않는다).
경보(automation-failure 이슈 하나에 슬롯별 댓글 1회):
  - dispatch API 실패 · 보완 dispatch(github-actions[bot])로 돈 실행이 실패로 끝남 · 비활성 워크플로.
  예약 실행 자체의 실패는 각 워크플로의 기존 경보 몫이라 여기서 중복으로 울리지 않는다.
fda.gov 보호(11-24): www.fda.gov 를 두드리는 대상(fda_host)은 한 틱에 1개만 dispatch 하고, FDA 계열 실행이 큐·진행
  중이면 0개 — 나머지는 deferred(다음 틱). 수집은 dispatch_cap(12시간 1건, 사람 수동 포함)으로 하루 상한을 둔다.

판정은 표준 라이브러리만 쓴다(러너에 의존성 설치 불필요). tests/test_schedule_catchup.py 가
분류표에서 빠진 예약과 TARGETS 의 cron 불일치를 막는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

BOT_LOGIN = "github-actions[bot]"
ALARM_LABEL = "automation-failure"          # web/ops_alarm_sync.py LABEL_TEAM → infra 팀 업무
ALARM_TITLE = "GRM 예약 지각 보완기 경보"
COVERING_EVENTS = ("schedule", "workflow_dispatch")
FAILED_CONCLUSIONS = ("failure", "timed_out", "startup_failure")
SINCE_45D = "{slot_minus_45d}"              # 483 전문 소급: 예약 실행은 '최근 45일'(실행 날 기준)


@dataclass(frozen=True)
class Target:
    workflow: str                 # .github/workflows/ 파일명
    crons: tuple[str, ...]        # 워크플로 on.schedule 과 같아야 한다(시험이 고정)
    grace_min: int                # 허용 지각(분) — 이 시간이 지나도 실행이 없으면 보완
    max_late_min: int             # 슬롯이 이보다 오래되면 보완하지 않는다(다음 슬롯 몫)
    inputs: dict[str, str] = field(default_factory=dict)   # 예약 실행과 같은 일을 하게 하는 dispatch 입력
    why: str = ""
    fda_host: bool = False        # www.fda.gov 를 두드린다 — 한 틱에 FDA 계열은 1개만 깨운다(시험이 워크플로에서 다시 판정)
    dispatch_cap: int = 0         # >0 이면 cap_window_min 안 dispatch 실행(사람 수동 + 보완기 합산)이 이만큼 있으면 더 깨우지 않는다
    cap_window_min: int = 0


# www.fda.gov 에 직접 요청을 보내는 모듈. 워크플로가 실행하는 진입 스크립트가 이 중 하나이거나 이것을 직접 import 하면
# FDA 계열이다(tests/test_schedule_catchup.py 가 TARGETS 의 fda_host 와 대조). 11-22 Akamai 차단은 같은 날 수동 run 이
# 몰린 직후였다(데이터품질팀 조사 11-24) — 보완기가 지각 슬롯을 모아 동시에 두드리지 않게 한다.
FDA_FETCH_MODULES = ("collect_fda_483", "collect_intake")
ACTIVE_STATUSES = ("queued", "in_progress", "waiting", "pending", "requested")


# 예약 전수 분류. 근거는 각 워크플로의 주석·실행 스크립트 머리말을 요약한다.
# A: 데이터·발행 결손 → TARGETS. B: 후속 실행이 회수. C: 감시·알림. D: 외부 발송 → 보완 금지.
CRON_AUDIT: dict[str, tuple[str, str]] = {
    # A — 시점별 기록, 발행 마감, 또는 원문 회수 경로.
    "grm-db-backup.yml": ("A", "암호화 주간 덤프. 다음 주 덤프로 빠진 주의 복구 지점을 만들 수 없다."),
    "grm-delta-bridge.yml": ("A", "월요일 발행에 필요한 해당 슬롯의 Notion 델타를 main 에 이관한다."),
    "grm-fda483-body-full-backfill.yml": ("A", "최근 45일 483 전문을 채워 월요일 handoff 전에 본문을 확보한다."),
    "grm-fda483-ocr-recovery.yml": ("A", "일일 수집이 다시 받지 않는 483·WL 본문을 정해진 회수 창과 상한 안에서 채운다."),
    "grm-intake.yml": ("A", "최근 7일을 수집하지만 월요일 발행은 해당 handoff 마감에 의존한다."),
    "grm-mfds-gmp-cert-monthly.yml": ("A", "전체 현황 재수집과 별개로 mfds_gmp_cert_snapshot.py 가 관측일별 적재 건수를 기록한다."),
    "grm-publish-watchdog.yml": ("A", "해당 월요일의 델타 누락·발행 정체를 감시하고 가능한 발행 단계를 복구한다."),
    "grm-rum-analytics.yml": ("A", "RUM 은 8일 재수집하지만 collect_newsletter_subscribers.py 는 KST 당일 구독자 수만 기록한다."),
    "grm-stranded-batch-pr.yml": ("A", "미머지 번역 배치로 생긴 적재 공백과 월요일 링크드인 초안 PR 정체를 해소한다."),
    # B — 다음 실행에서 이전 작업분을 포함하거나 최신 상태로 다시 만든다.
    "grm-deficiency-examples.yml": ("B", "날짜 증분 대신 SOURCES 원본을 다시 읽어 멱등 적재한다."),
    "grm-fda-citations-snapshot.yml": ("B", "원장에서 공개 조항 JSON 전체를 재산출한다."),
    "grm-fda-citations.yml": ("B", "활성 상태의 다음 월간 실행이 최근 7개 회계연도 전량을 다시 upsert 한다."),
    "grm-fda-inspections-backfill.yml": ("B", "8·15·22일 복구 예약이 미갱신 월을 확인하며 매번 최근 7개 회계연도 전량을 갱신한다."),
    "grm-feedback-retention.yml": ("B", "정리 시점에 완료 후 365일을 넘긴 전체 행을 대상으로 하므로 누락분도 다시 잡힌다."),
    "grm-findings-backfill-fetch.yml": ("B", "자동 백필은 적재 상태에서 다음 구간을 계산하므로 실행이 빠져도 건너뛰지 않는다."),
    "grm-findings-docs.yml": ("B", "findings_docs_refresh.py 가 문서 색인 정본을 현재 공개 데이터로 다시 만든다."),
    "grm-findings-embed.yml": ("B", "매일 신규분만 계산하되 미임베딩 행은 다음 회차에 남는다. 회차 상한은 2,000행이다."),
    "grm-findings-facets.yml": ("B", "모음 페이지의 건수·최근 사례를 현재 데이터에서 재측정한다."),
    "grm-findings-review-promote.yml": ("B", "남아 있는 needs_review 행을 다음 회차에도 조회한다."),
    "grm-findings-search-cache-sync.yml": ("B", "사전 파일 변경 push 와 금요일 백스톱이 같은 최신 검색어 목록을 동기화한다."),
    "grm-glossary-cases.yml": ("B", "다음 번에 용어별 사례 건수를 현재 데이터에서 다시 측정한다."),
    "grm-hc-inspection-backfill.yml": ("B", "주간 실행이 겹치는 최근 60일 창을 멱등 재수집한다."),
    "grm-kg-link.yml": ("B", "일일 그래프 연결은 최근 7일을 다시 처리한다."),
    "grm-library-staging.yml": ("B", "나머지 요일의 복구 예약이 격리 이슈와 자료실 신선도를 보고 다시 만든다."),
    "grm-news-refresh.yml": ("B", "raw_signals 에서 소식 정본을 매일 다시 만들고 브리프 요약도 갱신한다."),
    "grm-ops-alarm.yml": ("B", "이슈 이벤트와 시간당 재대조가 열린 경보·완료 상태를 운영 업무에 반영한다."),
    "grm-schedule-catchup.yml": ("B", "후속 30분 예약과 workflow_run 이 아직 유효한 보완 슬롯을 재검사한다."),
    "grm-supabase-keepalive.yml": ("B", "월·수·금 중 한 회를 놓쳐도 다음 ping 까지 최대 5일로 7일 휴면 기준 안이다."),
    # C — 관측·상태 보고·경보.
    "grm-brief-audit.yml": ("C", "발행된 웹 브리프의 출처 근거를 사후 검증한다."),
    "grm-checklist-examples-verify.yml": ("C", "자가점검 사례가 해당 조항의 실제 지적인지 검증한다."),
    "grm-db-size-health.yml": ("C", "별도 pg_cron 이 기록한 용량을 읽어 한도·기록 정지를 알린다."),
    "grm-findings-backlog-monitor.yml": ("C", "미번역 격차와 검수 백로그가 임계를 넘으면 이슈로 알린다."),
    "grm-findings-classification-audit.yml": ("C", "지적 분류 표류를 읽기 전용으로 점검한다."),
    "grm-findings-review-queue.yml": ("C", "검수 대기 건수와 최장 대기 시간을 재고 이슈로 알린다."),
    "grm-findings-search-cache-health.yml": ("C", "검색 캐시 적재는 별도 pg_cron 이 하고 이 잡은 신선도를 감시한다."),
    "grm-findings-taxonomy-drift.yml": ("C", "현행 분류기와 저장된 분류 차이를 조회한다."),
    "grm-gsc-index-sample.yml": ("C", "Google 색인 표본 점검 결과를 요약과 아티팩트로 남긴다."),
    "grm-library-linkcheck.yml": ("C", "library_linkcheck.py 가 기존 자료실 URL의 상태를 점검해 상태 스냅샷을 쓴다."),
    "grm-newsletter-freshness.yml": ("C", "해당 호 실발송 여부를 사후 확인하고 지연 이슈를 연다."),
    "grm-ops-measure.yml": ("C", "ops_measure_github.py 가 운영 목표의 현재 측정값과 최근 성공 시각을 갱신한다."),
    "grm-ops-status.yml": ("C", "클라우드가 읽도록 최근 Actions 실행 요약을 상태 브랜치에 복사한다."),
    "grm-quiz-freshness.yml": ("C", "별도 데스크톱 작업의 주간 퀴즈 생성 여부를 감시한다."),
    "grm-reconciliation.yml": ("C", "주간 소스별 수집량 급락과 근거 URL 오염을 감시한다."),
    "grm-site-error-watch.yml": ("C", "Cloudflare 실요청의 전날 오류 급증과 최근 배포 실패를 감시한다."),
    "grm-site-probe.yml": ("C", "공개 화면·브리프 신선도·RPC 지연을 합성 점검한다."),
    "grm-source-verification.yml": ("C", "발행된 483·WL 카드를 원문과 사후 대조해 이슈로 알린다."),
    # D — 받은편지함에 실제로 발송한다.
    "grm-newsletter-send.yml": ("D", "발행된 브리프 뉴스레터를 Brevo 로 발송한다."),
    "grm-ops-decision-mail.yml": ("D", "새 운영 결정의 결재 요청을 대표 메일함으로 발송한다."),
    "grm-watchlist-notify.yml": ("D", "관심 업체에 새 지적이 있는 사용자에게 다이제스트를 발송한다."),
}


# 근거: 발행·데이터 경로에서 '예약이 안 돌면 조용히 빈다'고 확인된 것만. 외부 발송(grm-newsletter-send)은 자동 재기동하지 않는다.
# 순서 = 같은 틱에 FDA 계열이 여럿 밀렸을 때의 우선순위(수집이 먼저).
TARGETS: tuple[Target, ...] = (
    # [11-24] 11-23 18:17Z 슬롯부터 실행 기록 없음 → 비서실 수동 dispatch. 입력 없는 dispatch = 기본값(window 7·전 소스·
    # 플래그 inherit=저장소 변수)이라 예약과 같은 수집이다. 여러 번 돌아도 handoff 는 07:00 KST 뒤 동결(grm_handoff).
    # 허용 지각 4시간 — 예약이 평소 3~6.5시간 늦게 와서(11-16 주석 실측) 짧게 잡으면 매일 두 번 돈다. 22:17Z = 07:17 KST 는
    # 편집팀이 handoff 를 읽기(07:20) 직전이다. 일요일 슬롯 3개(월 00~01 KST)는 19:07Z 쯤 보완된다.
    # 하루 상한 — 12시간 안에 dispatch 실행(사람 수동 포함)이 1건이라도 있으면 보완하지 않는다(fda.gov 요청 누적 방지).
    Target("grm-intake.yml", ("17 18 * * *", "7 15 * * 0", "43 15 * * 0", "19 16 * * 0"), 240, 18 * 60, {},
           "일일 수집 — 11-23 슬롯 누락, 월요일 handoff 의 원천", fda_host=True, dispatch_cap=1, cap_window_min=12 * 60),
    Target("grm-fda483-body-full-backfill.yml", ("7 13 * * *",), 90, 20 * 60,
           {"apply": "true", "since": SINCE_45D, "ocr_page_budget": "1500"},
           "483 전문 소급 — 월요일 handoff 전 안전망(11-22·23 지각/누락)", fda_host=True),
    Target("grm-fda483-ocr-recovery.yml", ("37 23 * * *",), 90, 20 * 60,
           {"apply": "true", "mode": "recoverable", "target": "all", "ocr_page_budget": "600"},
           "483·WL 본문 회수 — 빠지면 그날 상한 밖 문서가 본문 없이 남는다", fda_host=True),
    Target("grm-stranded-batch-pr.yml", ("41 */6 * * *", "41 6,8,10 * * 1"), 60, 5 * 60,
           {"dry_run": "false"},
           "방치된 배치·링크드인 슬롯 PR 머지(11-23 #1879 3시간 미머지)"),
    Target("grm-delta-bridge.yml", ("7,37 22-23 * * 0", "7,37 0-5 * * 1"), 45, 4 * 60, {},
           "월요일 델타 이관(07-13·07-20 지각 실측) — 멱등, 이미 이관됐으면 skip"),
    Target("grm-publish-watchdog.yml", ("13 0,2,4 * * 1",), 60, 6 * 60, {},
           "월요일 발행 감시·자가 복구(07-20 3h28m 지각 실측)"),
    Target("grm-db-backup.yml", ("37 20 * * 0",), 60, 6 * 60, {},
           "주간 암호화 백업 — 그 주의 복구 지점 확보"),
    Target("grm-mfds-gmp-cert-monthly.yml", ("40 18 1 * *",), 90, 12 * 60, {"mode": "apply"},
           "식약처 GMP 적합판정서 월간 관측 — 예약과 같은 KST 날짜에 적재"),
    Target("grm-rum-analytics.yml", ("30 4 * * *", "30 16 * * *"), 90, 6 * 60, {},
           "성장 지표 수집 — 예약과 같은 KST 날짜의 구독자 수 확보"),
)


# ── cron ─────────────────────────────────────────────────────────────────────

def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise ValueError(f"cron step: {spec}")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = int(x), int(y)
        else:
            a = int(part)
            b = hi if step != 1 else a
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron range: {spec}")
        out.update(range(a, b + 1, step))
    return out


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    doms: frozenset[int]
    months: frozenset[int]
    dows: frozenset[int]
    dom_star: bool
    dow_star: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 5칸 아님: {expr!r}")
        m, h, dom, mon, dow = parts
        dows = {d % 7 for d in _field(dow, 0, 7)}
        return cls(frozenset(_field(m, 0, 59)), frozenset(_field(h, 0, 23)),
                   frozenset(_field(dom, 1, 31)), frozenset(_field(mon, 1, 12)),
                   frozenset(dows), dom.startswith("*"), dow.startswith("*"))

    def matches(self, t: datetime) -> bool:
        if t.minute not in self.minutes or t.hour not in self.hours or t.month not in self.months:
            return False
        dom_ok = t.day in self.doms
        dow_ok = ((t.weekday() + 1) % 7) in self.dows      # cron: 0=일
        if self.dom_star or self.dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok                             # 둘 다 지정되면 OR(표준 cron)


def latest_slot(crons: Iterable[str], before: datetime, lookback_min: int) -> datetime | None:
    """before 이하의 가장 최근 예정 시각(UTC). lookback_min 분 안에 없으면 None."""
    parsed = [Cron.parse(c) for c in crons]
    t = before.astimezone(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(lookback_min + 1):
        if any(c.matches(t) for c in parsed):
            return t
        t -= timedelta(minutes=1)
    return None


# ── 판정 ─────────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    workflow: str
    action: str                   # ok | dispatch | not-due | disabled | capped | deferred | alarm-failed
    slot: datetime | None = None
    reason: str = ""
    inputs: dict[str, str] = field(default_factory=dict)
    run_url: str = ""
    alerted: bool = False         # 이번 실행에서 경보를 새로 올렸나(같은 슬롯 재경보는 False)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def resolve_inputs(inputs: dict[str, str], slot: datetime) -> dict[str, str]:
    out = {}
    for k, v in inputs.items():
        if v == SINCE_45D:
            v = (slot - timedelta(days=45)).strftime("%Y-%m-%d")
        out[k] = v
    return out


def decide(target: Target, now: datetime, runs: list[dict[str, Any]], state: str = "active") -> Decision:
    """runs = GitHub API workflow_runs(이 워크플로, 최근 것). 순수 함수 — 시험 대상."""
    slot = latest_slot(target.crons, now - timedelta(minutes=target.grace_min), target.max_late_min)
    if slot is None:
        return Decision(target.workflow, "not-due", reason="허용 지각 안이거나 보완 기한(max_late)이 지난 슬롯뿐")
    covering = [r for r in runs
                if r.get("event") in COVERING_EVENTS and r.get("created_at") and _ts(r["created_at"]) >= slot]
    if covering:
        latest = max(covering, key=lambda r: _ts(r["created_at"]))
        actor = ((latest.get("triggering_actor") or latest.get("actor") or {}).get("login") or "")
        if (latest.get("event") == "workflow_dispatch" and actor == BOT_LOGIN
                and latest.get("status") == "completed" and latest.get("conclusion") in FAILED_CONCLUSIONS):
            return Decision(target.workflow, "alarm-failed", slot,
                            f"보완 실행이 {latest.get('conclusion')} 로 끝남", run_url=str(latest.get("html_url") or ""))
        return Decision(target.workflow, "ok", slot,
                        f"슬롯 이후 실행 있음({latest.get('event')}·{latest.get('status')})",
                        run_url=str(latest.get("html_url") or ""))
    if state != "active":
        return Decision(target.workflow, "disabled", slot, f"워크플로 상태 {state} — 보완하지 않음")
    if target.dispatch_cap > 0:
        window_start = now - timedelta(minutes=target.cap_window_min)
        recent = [r for r in runs if r.get("event") == "workflow_dispatch" and r.get("created_at")
                  and _ts(r["created_at"]) >= window_start]
        if len(recent) >= target.dispatch_cap:
            return Decision(target.workflow, "capped", slot,
                            f"최근 {target.cap_window_min // 60}시간 dispatch {len(recent)}건 ≥ 상한 {target.dispatch_cap} — 보완하지 않음")
    late = int((now - slot).total_seconds() // 60)
    return Decision(target.workflow, "dispatch", slot, f"슬롯 {slot:%m-%d %H:%MZ} 후 {late}분 실행 없음",
                    inputs=resolve_inputs(target.inputs, slot))


# ── GitHub API(표준 라이브러리) ──────────────────────────────────────────────

class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com") -> None:
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        req = urllib.request.Request(f"{self.api}/repos/{self.repo}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode())
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}

    def workflow_state(self, wf: str) -> str:
        return str(self.call("GET", f"/actions/workflows/{wf}").get("state") or "unknown")

    def runs_since(self, wf: str, since: datetime) -> list[dict[str, Any]]:
        q = f"created=%3E%3D{since.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=50"
        return list(self.call("GET", f"/actions/workflows/{wf}/runs?{q}").get("workflow_runs") or [])

    def dispatch(self, wf: str, ref: str, inputs: dict[str, str]) -> None:
        self.call("POST", f"/actions/workflows/{wf}/dispatches", {"ref": ref, "inputs": inputs})

    def alarm(self, marker: str, text: str) -> str:
        """경보 이슈(하나)에 슬롯별 1회 댓글. 이미 같은 표식이 있으면 아무것도 안 한다."""
        issues = self.call("GET", f"/issues?state=open&labels={ALARM_LABEL}&per_page=100")
        issue = next((i for i in issues if i.get("title") == ALARM_TITLE and "pull_request" not in i), None)
        body = f"{marker}\n{text}"
        if issue is None:
            made = self.call("POST", "/issues", {"title": ALARM_TITLE, "labels": [ALARM_LABEL], "body":
                             "중요 예약 워크플로가 예정 시각에 돌지 않아 보완기(schedule_catchup.py)가 대신 기동했는데 "
                             "실패했거나 기동하지 못한 경우를 모은다. 슬롯마다 댓글 1회.\n\n" + body})
            return f"이슈 #{made.get('number')} 생성"
        n = issue["number"]
        comments = self.call("GET", f"/issues/{n}/comments?per_page=100")
        if any(marker in str(c.get("body") or "") for c in comments):
            return f"이슈 #{n} 이미 표기됨"
        self.call("POST", f"/issues/{n}/comments", {"body": body})
        return f"이슈 #{n} 댓글"


def run_all(gh: Any, now: datetime, *, ref: str, dry_run: bool,
            targets: Iterable[Target] = TARGETS, sleeper: Callable[[float], None] = time.sleep,
            log: Callable[[str], None] = print) -> list[Decision]:
    targets = list(targets)
    planned: list[tuple[Target, Decision]] = []
    fda_busy: list[str] = []          # 지금 큐·진행 중인 FDA 계열 실행
    for t in targets:
        since = now - timedelta(minutes=max(t.grace_min + t.max_late_min + 60, t.cap_window_min))
        try:
            runs = gh.runs_since(t.workflow, since)
            d = decide(t, now, runs, gh.workflow_state(t.workflow))
            if t.fda_host and any(r.get("status") in ACTIVE_STATUSES for r in runs):
                fda_busy.append(t.workflow)
        except Exception as exc:  # noqa: BLE001 — 한 대상 실패가 나머지를 막지 않게
            d = Decision(t.workflow, "error", reason=f"조회 실패 {type(exc).__name__}: {exc}")
        planned.append((t, d))
    return _act(gh, planned, fda_busy, now, ref=ref, dry_run=dry_run, sleeper=sleeper, log=log)


def gate_fda(planned: list[tuple[Target, Decision]], fda_busy: list[str]) -> None:
    """한 틱에 FDA 계열 dispatch 는 1개. 이미 FDA 계열이 큐·진행 중이면 0개. 밀린 것은 다음 틱(deferred)."""
    started = bool(fda_busy)
    for t, d in planned:
        if not (t.fda_host and d.action == "dispatch"):
            continue
        if started:
            d.action = "deferred"
            d.reason += " → FDA 계열 " + (f"진행 중({', '.join(fda_busy)})" if fda_busy else "이번 틱 1건 기동") + " — 다음 틱"
        else:
            started = True


def _act(gh: Any, planned: list[tuple[Target, Decision]], fda_busy: list[str], now: datetime, *, ref: str,
         dry_run: bool, sleeper: Callable[[float], None], log: Callable[[str], None]) -> list[Decision]:
    gate_fda(planned, fda_busy)
    out: list[Decision] = []
    for t, d in planned:
        marker = f"<!-- catchup:{t.workflow}:{d.slot:%Y-%m-%dT%H:%MZ} -->" if d.slot else ""
        if d.action == "dispatch" and not dry_run:
            try:
                gh.dispatch(t.workflow, ref, d.inputs)
                d.reason += " → dispatch 함"
                for _ in range(6):                         # 다음 보완기 실행이 이 실행을 보도록(멱등)
                    sleeper(5)
                    if any(r.get("event") == "workflow_dispatch" for r in gh.runs_since(t.workflow, now - timedelta(minutes=1))):
                        break
            except Exception as exc:  # noqa: BLE001
                d.action = "alarm-dispatch-failed"
                d.reason += f" → dispatch 실패 {type(exc).__name__}: {exc}"
        if d.action.startswith("alarm") or d.action == "disabled":
            text = f"- `{t.workflow}` 슬롯 {d.slot:%Y-%m-%d %H:%M} UTC · {d.action} · {d.reason} {d.run_url}".rstrip()
            if dry_run:
                d.reason += " (dry-run: 경보 생략)"
            else:
                try:
                    res = gh.alarm(marker, text)
                    d.alerted = "이미" not in res
                    d.reason += f" · {res}"
                except Exception as exc:  # noqa: BLE001
                    d.reason += f" · 경보 실패 {type(exc).__name__}"
        log(f"[catchup] {t.workflow}: {d.action} — {d.reason}")
        out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="판정만 출력(dispatch·경보 없음)")
    ap.add_argument("--ref", default=os.environ.get("CATCHUP_REF", "main"))
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print("::error::GITHUB_REPOSITORY·GH_TOKEN 필요")
        return 2
    decisions = run_all(GitHub(repo, token), datetime.now(timezone.utc), ref=args.ref, dry_run=args.dry_run)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(f"### 예약 지각 보완기{' (dry-run)' if args.dry_run else ''}\n\n| 워크플로 | 판정 | 슬롯(UTC) | 사유 |\n|---|---|---|---|\n")
            for d in decisions:
                fh.write(f"| `{d.workflow}` | {d.action} | {d.slot:%m-%d %H:%M} | {d.reason} |\n" if d.slot
                         else f"| `{d.workflow}` | {d.action} | - | {d.reason} |\n")
    # 새로 경보를 올린 실행·조회 오류만 빨강(같은 슬롯 경보가 30분마다 빨강을 반복하지 않게).
    bad = [d for d in decisions if d.alerted or d.action == "error"
           or (args.dry_run and d.action.startswith("alarm"))]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
