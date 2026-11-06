#!/usr/bin/env python3
"""운영 센터 자동 측정 — GitHub 갈래(설계서 2026-11-06 §2-6 · PR6). grm-ops-measure.yml 이 매일 1회 부른다.

코드만(AI 호출 0 · 유료 API 0). `gh api`(워크플로의 github.token, 읽기 전용)로 실행 기록을 읽어 목표 값을 계산하고,
service_role 키로 `ops_goal_set`(125) RPC 를 부른다. 값마다 표본 수를 note 에 같이 적는다.

  dev-ci            main 의 CI(grm-ci.yml) `test` 잡 소요(분) — 최근 성공 10회 중앙값
  dev-pr-old        열린 PR 중 만든 지 3일(72시간) 넘은 건수(초안 포함)
  dev-link-streak   grm-kg-link.yml 일일 연결 연속 성공 일수 — 실행 기록에서 직접 센다(어제 값 +1 이 아니다).
                    KST 날짜별 마지막 완료 실행이 success 인 날이 끊김 없이 이어진 수. 실행이 없는 날도 끊김.
                    오늘(KST) 실행이 아직 없으면 어제부터 센다.
  infra-deploy-fail 최근 24시간 배포 워크플로(grm-web-deploy·grm-admin-backend-deploy) 실패 수
                    (failure·timed_out·startup_failure. 취소는 동시 실행 정리라 세지 않는다)
  sr-ontime         정기 작업 정시율 — 보수적 정의: 최근 7일 동안 '하루 4회 이하' 예약(cron) 워크플로의 예정 시각마다
                    예정 +60분 안에 시작한 schedule 실행이 있으면 정시. 실행이 없으면(누락) 지각과 같이 센다.
                    분모는 예정 횟수(꺼진 워크플로 제외 · 지금부터 60분 전까지의 예정만).
  edu-terms-week    이번 주(KST 월요일 00:00 이후) 용어 사전(web/data/glossary.json) 항목 증가 수
                    — 용어 정본이 DB 가 아니라 이 파일이라 GitHub 갈래에서 센다.

팀원 활동(ops_member_touch): 코드 팀원 dq-kg-link(=grm-kg-link.yml)·infra-intake(=grm-intake.yml)의 마지막 성공 시각.
  지금 기록보다 새로울 때만 쓴다(사람·세션 팀원은 건드리지 않는다).
  ★헌장(company/CHARTER.md)의 결과 파일 3줄 규약은 있지만 결과 파일(company/run/…)은 저장소에 커밋되지 않아
    읽을 것이 없다 — 그래서 워크플로 실행 기록을 쓴다.

안전: 기본 --dry-run(계산·출력만, 쓰기 0). 실패한 측정은 쓰지 않는다(이전 값 유지 → 주기×1.5 뒤 자연 강등).
  실패는 이슈를 열지 않고 단계 요약($GITHUB_STEP_SUMMARY)에만 적는다. 로그에 키를 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
NOTE_MAX = 120
CI_SAMPLE = 10
PR_OLD_HOURS = 72
ONTIME_GRACE_MIN = 60
ONTIME_WINDOW_DAYS = 7
ONTIME_MAX_PER_DAY = 4
DEPLOY_WORKFLOWS = ("grm-web-deploy.yml", "grm-admin-backend-deploy.yml")
DEPLOY_FAIL = ("failure", "timed_out", "startup_failure")
GLOSSARY = "web/data/glossary.json"
MEMBERS = {"dq-kg-link": "grm-kg-link.yml", "infra-intake": "grm-intake.yml"}
WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


class Hold(Exception):
    """측정 보류(표본 0 등) — 값을 지어내지 않고 쓰지 않는다."""


# ── 시각 ─────────────────────────────────────────────────────────────────────
def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def kst_md(t: dt.datetime) -> str:
    return t.astimezone(KST).strftime("%m-%d")


def clip(note: str, n: int = NOTE_MAX) -> str:
    return note if len(note) <= n else note[: n - 1] + "…"


# ── 순수 계산(시험 대상) ────────────────────────────────────────────────────────
def ci_median(jobs: list[dict[str, Any]]) -> tuple[float, str]:
    mins = []
    for j in jobs:
        a, b = parse_ts(j.get("started_at")), parse_ts(j.get("completed_at"))
        if a and b and b > a:
            mins.append((b - a).total_seconds() / 60)
    if not mins:
        raise Hold("CI test 잡 표본 0")
    med = round(statistics.median(mins), 1)
    return med, f"main CI test 잡 최근 {len(mins)}회 중앙값 · 범위 {min(mins):.0f}~{max(mins):.0f}분"


def old_pr_count(prs: list[dict[str, Any]], now: dt.datetime) -> tuple[int, str]:
    cut = now - dt.timedelta(hours=PR_OLD_HOURS)
    old = [p for p in prs if (parse_ts(p.get("created_at")) or now) < cut]
    return len(old), f"열린 PR {len(prs)}건 중 3일 초과 {len(old)}건(초안 포함)"


def link_streak(runs: list[dict[str, Any]], now: dt.datetime) -> tuple[int, str]:
    """KST 날짜별 마지막 완료 실행의 결론으로 연속 성공 일수를 센다."""
    last: dict[dt.date, tuple[dt.datetime, str]] = {}
    for r in runs:
        if r.get("status") != "completed":
            continue
        t = parse_ts(r.get("created_at"))
        if not t:
            continue
        d = t.astimezone(KST).date()
        if d not in last or t > last[d][0]:
            last[d] = (t, str(r.get("conclusion") or ""))
    if not last:
        raise Hold("일일 연결 완료 실행 0")
    day = now.astimezone(KST).date()
    if day not in last:
        day -= dt.timedelta(days=1)
    streak = 0
    while day in last and last[day][1] == "success":
        streak += 1
        day -= dt.timedelta(days=1)
    oldest = min(last)
    if streak and day < oldest:
        return streak, f"연속 성공 {streak}일 이상(기록 {len(last)}일 전부 성공)"
    why = "실패" if day in last else "실행 없음"
    return streak, f"연속 성공 {streak}일 · 끊긴 날 {day:%m-%d}({why}) · 기록 {len(last)}일"


def deploy_failures(runs_by_wf: dict[str, list[dict[str, Any]]], now: dt.datetime) -> tuple[int, str]:
    since = now - dt.timedelta(hours=24)
    total = fail = 0
    for runs in runs_by_wf.values():
        for r in runs:
            t = parse_ts(r.get("created_at"))
            if not t or t < since or r.get("status") != "completed":
                continue
            total += 1
            fail += r.get("conclusion") in DEPLOY_FAIL
    return fail, f"최근 24시간 배포 {total}회 중 실패 {fail}회(웹·관리자 백엔드)"


def _cron_field(spec: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = int((names or {}).get(x.lower(), x)), int((names or {}).get(y.lower(), y))
        else:
            a = int((names or {}).get(part.lower(), part))
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


_DOW = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def cron_fires(expr: str, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """UTC 기준 cron 예정 시각(start 이상 end 이하). 5필드 · 숫자·범위·목록·간격·요일 이름."""
    mi, ho, dom, mon, dow = expr.split()
    m_s, h_s = _cron_field(mi, 0, 59), _cron_field(ho, 0, 23)
    d_s, mo_s = _cron_field(dom, 1, 31), _cron_field(mon, 1, 12)
    w_s = {d % 7 for d in _cron_field(dow, 0, 7, _DOW)}
    dom_any, dow_any = dom == "*", dow == "*"
    t = start.astimezone(UTC).replace(second=0, microsecond=0)
    if t < start:
        t += dt.timedelta(minutes=1)
    out = []
    day = None
    day_ok = False
    while t <= end:
        if t.date() != day:
            day = t.date()
            wd = (t.weekday() + 1) % 7
            if dom_any and dow_any:
                day_ok = True
            elif dom_any:
                day_ok = wd in w_s
            elif dow_any:
                day_ok = t.day in d_s
            else:
                day_ok = t.day in d_s or wd in w_s
            day_ok = day_ok and t.month in mo_s
        if day_ok and t.hour in h_s and t.minute in m_s:
            out.append(t)
        t += dt.timedelta(minutes=1)
    return out


def fires_per_day(expr: str) -> int:
    mi, ho = expr.split()[:2]
    return len(_cron_field(mi, 0, 59)) * len(_cron_field(ho, 0, 23))


def ontime(schedules: dict[str, list[str]], runs_by_wf: dict[str, list[dict[str, Any]]],
           starts: dict[str, dt.datetime], now: dt.datetime) -> tuple[float, str]:
    """schedules: 워크플로 → cron 목록(하루 4회 이하만). starts: 워크플로별 창 시작(만든 시각 반영)."""
    end = now - dt.timedelta(minutes=ONTIME_GRACE_MIN)
    due = hit = 0
    for wf, crons in schedules.items():
        start = starts.get(wf, now - dt.timedelta(days=ONTIME_WINDOW_DAYS))
        fires = sorted({f for c in crons for f in cron_fires(c, start, end)})
        created = sorted(t for t in (parse_ts(r.get("created_at")) for r in runs_by_wf.get(wf, [])
                                     if r.get("event") == "schedule") if t)
        used: set[int] = set()
        for f in fires:
            due += 1
            for i, t in enumerate(created):
                if i in used:
                    continue
                if f - dt.timedelta(minutes=2) <= t <= f + dt.timedelta(minutes=ONTIME_GRACE_MIN):
                    used.add(i)
                    hit += 1
                    break
    if due == 0:
        raise Hold("최근 7일 예정 0회")
    pct = round(100.0 * hit / due, 1)
    return pct, (f"7일 예약 {due}회 중 {hit}회 예정+60분 안 시작 · 하루 4회 이하 예약 {len(schedules)}개"
                 " · 누락도 지각으로 셈")


def glossary_week(now_count: int, base_count: int | None, base_day: str | None) -> tuple[int, str]:
    if base_count is None:
        raise Hold("이번 주 기준(월요일 전) 용어 사전 없음")
    return now_count - base_count, f"용어 사전 {now_count}어 · 월요일 기준({base_day}) {base_count}어 · {GLOSSARY}"


def kst_monday(now: dt.datetime) -> dt.datetime:
    k = now.astimezone(KST)
    return (k - dt.timedelta(days=k.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def crons_in(text: str) -> list[str]:
    """워크플로 파일의 cron 식(주석 줄 제외)."""
    import re
    return [m.group(1).strip() for m in re.finditer(r"^[ ]*-?[ ]*cron:[ ]*['\"]([^'\"]+)['\"]", text, re.M)]


def touch_needed(old: str | None, new: dt.datetime) -> bool:
    o = parse_ts(old)
    return o is None or new > o


# ── 입출력 ───────────────────────────────────────────────────────────────────
def gh(path: str, raw: bool = False) -> Any:
    args = ["gh", "api", "-H", "X-GitHub-Api-Version: 2022-11-28"]
    if raw:
        args += ["-H", "Accept: application/vnd.github.raw"]
    out = subprocess.run(args + [path], check=True, capture_output=True, text=True, encoding="utf-8").stdout
    return out if raw else json.loads(out)


def gh_runs(repo: str, wf: str, query: str = "", pages: int = 1) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for p in range(1, pages + 1):
        d = gh(f"repos/{repo}/actions/workflows/{wf}/runs?per_page=100&page={p}{query}")
        batch = d.get("workflow_runs") or []
        runs += batch
        if len(batch) < 100:
            break
    return runs


class Rest:
    def __init__(self, url: str, key: str):
        import requests  # 지역 import — 순수 함수 시험은 requests 없이 돈다
        self.requests = requests
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def rpc(self, name: str, body: dict[str, Any]) -> None:
        r = self.requests.post(f"{self.base}/rpc/{name}", headers=self.h, json=body, timeout=20)
        if not r.ok:
            raise RuntimeError(f"{name} 실패: status={r.status_code}")

    def members(self, slugs: Iterable[str]) -> dict[str, str | None]:
        r = self.requests.get(f"{self.base}/ops_member", headers=self.h, timeout=20,
                              params={"select": "slug,last_active_at", "slug": f"in.({','.join(slugs)})"})
        if not r.ok:
            raise RuntimeError(f"ops_member 읽기 실패: status={r.status_code}")
        return {row["slug"]: row.get("last_active_at") for row in r.json()}


def measure_all(repo: str, now: dt.datetime, api: Callable[..., Any] = gh) -> dict[str, Any]:
    """목표별 (값, 메모) 또는 보류/실패 사유. 하나가 실패해도 나머지는 잰다."""
    results: dict[str, Any] = {}

    def run(slug: str, fn: Callable[[], tuple[Any, str]]) -> None:
        try:
            v, note = fn()
            results[slug] = {"value": v, "note": clip(note)}
        except Hold as h:
            results[slug] = {"hold": str(h)}
        except Exception as exc:  # 키가 담길 수 있는 메시지는 쓰지 않는다
            results[slug] = {"error": type(exc).__name__}

    def m_ci():
        runs = api(f"repos/{repo}/actions/workflows/grm-ci.yml/runs?branch=main&event=push&status=success"
                   f"&per_page={CI_SAMPLE}").get("workflow_runs") or []
        jobs = []
        for r in runs[:CI_SAMPLE]:
            js = api(f"repos/{repo}/actions/runs/{r['id']}/jobs?per_page=100").get("jobs") or []
            jobs += [j for j in js if j.get("name") == "test" and j.get("conclusion") == "success"]
        return ci_median(jobs)

    def m_pr():
        return old_pr_count(api(f"repos/{repo}/pulls?state=open&per_page=100"), now)

    def m_streak():
        d = api(f"repos/{repo}/actions/workflows/grm-kg-link.yml/runs?per_page=100")
        return link_streak(d.get("workflow_runs") or [], now)

    def m_deploy():
        since = (now - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        by = {wf: api(f"repos/{repo}/actions/workflows/{wf}/runs?per_page=100&created=%3E%3D{since}")
                  .get("workflow_runs") or [] for wf in DEPLOY_WORKFLOWS}
        return deploy_failures(by, now)

    def m_ontime():
        wfs = api(f"repos/{repo}/actions/workflows?per_page=100").get("workflows") or []
        since = now - dt.timedelta(days=ONTIME_WINDOW_DAYS)
        sched: dict[str, list[str]] = {}
        starts: dict[str, dt.datetime] = {}
        runs: dict[str, list[dict[str, Any]]] = {}
        for w in wfs:
            name = Path(str(w.get("path") or "")).name
            f = WORKFLOW_DIR / name
            if w.get("state") != "active" or not f.is_file():
                continue
            crons = [c for c in crons_in(f.read_text(encoding="utf-8")) if fires_per_day(c) <= ONTIME_MAX_PER_DAY]
            if not crons:
                continue
            sched[name] = crons
            starts[name] = max(since, parse_ts(w.get("created_at")) or since)
            q = f"&event=schedule&created=%3E%3D{since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            runs[name] = []
            for p in (1, 2):
                d = api(f"repos/{repo}/actions/workflows/{w['id']}/runs?per_page=100&page={p}{q}")
                batch = d.get("workflow_runs") or []
                runs[name] += batch
                if len(batch) < 100:
                    break
        return ontime(sched, runs, starts, now)

    def m_terms():
        def count(ref: str) -> int:
            data = json.loads(api(f"repos/{repo}/contents/{GLOSSARY}?ref={ref}", raw=True))
            return len(data)
        monday = kst_monday(now)
        until = monday.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = api(f"repos/{repo}/commits?path={GLOSSARY}&sha=main&until={until}&per_page=1")
        base_n = count(base[0]["sha"]) if base else None
        return glossary_week(count("main"), base_n, monday.strftime("%m-%d"))

    for slug, fn in (("dev-ci", m_ci), ("dev-pr-old", m_pr), ("dev-link-streak", m_streak),
                     ("infra-deploy-fail", m_deploy), ("sr-ontime", m_ontime), ("edu-terms-week", m_terms)):
        run(slug, fn)
    return results


def last_success(repo: str, wf: str, api: Callable[..., Any] = gh) -> dt.datetime | None:
    d = api(f"repos/{repo}/actions/workflows/{wf}/runs?status=success&per_page=1")
    runs = d.get("workflow_runs") or []
    return parse_ts(runs[0].get("updated_at")) if runs else None


def summary_lines(results: dict[str, Any], touches: list[tuple[str, str]], dry: bool) -> list[str]:
    mode = "dry-run(쓰기 0)" if dry else "쓰기"
    lines = [f"## 운영 센터 자동 측정 — {mode}", "", "| 목표 | 값 | 메모·사유 |", "|---|---|---|"]
    for slug, r in results.items():
        if "value" in r:
            lines.append(f"| {slug} | {r['value']} | {r['note']} |")
        elif "hold" in r:
            lines.append(f"| {slug} | 측정 보류 | {r['hold']} |")
        else:
            lines.append(f"| {slug} | 측정 실패 | {r['error']} (이전 값 유지) |")
    lines += ["", "| 팀원 | 마지막 활동 |", "|---|---|"]
    lines += [f"| {s} | {n} |" for s, n in touches] or ["| - | 기록 없음 |"]
    return lines


def main(argv: list[str] | None = None, now: dt.datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="계산·출력만(쓰기 0)")
    a = ap.parse_args(argv)
    now = now or dt.datetime.now(UTC)
    repo = os.environ.get("GITHUB_REPOSITORY", "MINHOYEOM/grm-api-intake")
    results = measure_all(repo, now)
    touches: list[tuple[str, str]] = []
    member_at: dict[str, dt.datetime] = {}
    for slug, wf in MEMBERS.items():
        try:
            t = last_success(repo, wf)
        except Exception as exc:
            touches.append((slug, f"읽기 실패 {type(exc).__name__}"))
            continue
        if t:
            member_at[slug] = t
    rest = None
    if not a.dry_run:
        url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            print("::warning::SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 미설정 — 쓰기 없이 끝냄")
        else:
            rest = Rest(url, key)
    at = now.isoformat()
    for slug, r in results.items():
        if "value" not in r:
            continue
        if rest is None:
            print(f"[dry-run] {slug} = {r['value']} · {r['note']}")
            continue
        try:
            rest.rpc("ops_goal_set", {"p_slug": slug, "p_value": r["value"], "p_note": r["note"], "p_at": at})
            print(f"기록 {slug} = {r['value']}")
        except Exception as exc:
            r.clear()
            r["error"] = f"기록 실패 {type(exc).__name__}"
    old: dict[str, str | None] = {}
    if rest is not None and member_at:
        try:
            old = rest.members(member_at)
        except Exception as exc:
            touches.append(("ops_member", f"읽기 실패 {type(exc).__name__}"))
            member_at = {}
    for slug, t in member_at.items():
        wf = MEMBERS[slug].removesuffix(".yml")
        note = f"{t.astimezone(KST):%m-%d %H:%M} {wf} 성공"
        if rest is None:
            touches.append((slug, f"[dry-run] {note}"))
            continue
        if not touch_needed(old.get(slug), t):
            touches.append((slug, f"그대로(기록이 더 새로움) · {note}"))
            continue
        try:
            rest.rpc("ops_member_touch", {"p_slug": slug, "p_note": note, "p_at": t.isoformat()})
            touches.append((slug, note))
        except Exception as exc:
            touches.append((slug, f"기록 실패 {type(exc).__name__}"))
    for slug, r in results.items():
        if "value" not in r:
            print(f"{slug}: {'측정 보류 ' + r['hold'] if 'hold' in r else '측정 실패 ' + r['error']}")
    text = "\n".join(summary_lines(results, touches, rest is None)) + "\n"
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
