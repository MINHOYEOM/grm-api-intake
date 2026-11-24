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

판정은 표준 라이브러리만 쓴다(러너에 의존성 설치 불필요). tests/test_schedule_catchup.py 가
TARGETS 의 cron 과 실제 워크플로 cron 이 어긋나면 CI 를 빨갛게 만든다.
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


# 근거: 발행·데이터 경로에서 '예약이 안 돌면 조용히 빈다'고 확인된 것만. 수집(grm-intake)은 dispatch 의미가
# 예약과 달라(일요일 슬롯별 역할) 넣지 않는다. 외부 발송(grm-newsletter-send)은 자동 재기동하지 않는다.
TARGETS: tuple[Target, ...] = (
    Target("grm-fda483-body-full-backfill.yml", ("7 13 * * *",), 90, 20 * 60,
           {"apply": "true", "since": SINCE_45D, "ocr_page_budget": "1500"},
           "483 전문 소급 — 월요일 handoff 전 안전망(11-22·23 지각/누락)"),
    Target("grm-fda483-ocr-recovery.yml", ("37 23 * * *",), 90, 20 * 60,
           {"apply": "true", "mode": "recoverable", "target": "all", "ocr_page_budget": "600"},
           "483·WL 본문 회수 — 빠지면 그날 상한 밖 문서가 본문 없이 남는다"),
    Target("grm-stranded-batch-pr.yml", ("41 */6 * * *", "41 6,8,10 * * 1"), 60, 5 * 60,
           {"dry_run": "false"},
           "방치된 배치·링크드인 슬롯 PR 머지(11-23 #1879 3시간 미머지)"),
    Target("grm-delta-bridge.yml", ("7,37 22-23 * * 0", "7,37 0-4 * * 1"), 45, 4 * 60, {},
           "월요일 델타 이관(07-13·07-20 지각 실측) — 멱등, 이미 이관됐으면 skip"),
    Target("grm-publish-watchdog.yml", ("13 0,2,4 * * 1",), 60, 6 * 60, {},
           "월요일 발행 감시·자가 복구(07-20 3h28m 지각 실측)"),
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
    action: str                   # ok | dispatch | not-due | disabled | alarm-failed
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
    out: list[Decision] = []
    for t in targets:
        since = now - timedelta(minutes=t.grace_min + t.max_late_min + 60)
        try:
            d = decide(t, now, gh.runs_since(t.workflow, since), gh.workflow_state(t.workflow))
        except Exception as exc:  # noqa: BLE001 — 한 대상 실패가 나머지를 막지 않게
            d = Decision(t.workflow, "error", reason=f"조회 실패 {type(exc).__name__}: {exc}")
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
