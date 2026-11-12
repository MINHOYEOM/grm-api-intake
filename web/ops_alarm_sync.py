#!/usr/bin/env python3
"""경보 이슈 -> 운영 센터 업무 (131). grm-ops-alarm.yml 이 부른다.

자동 감시가 GitHub 이슈로 경보를 열어도 처리하는 사람이 없었다. 경보 라벨이 붙은 이슈 1건을 담당 팀의 업무(ops_task) 1건으로
만들고, 이슈가 닫히면 완료, 다시 열리면 되살리며, 3일 넘게 지시함에서 안 움직이면 종합상황실에 '방치 경보' 업무를 올린다.

모드(--mode):
  event    issues 이벤트 한 건(GITHUB_EVENT_PATH) -> 업무 upsert.
           ★GITHUB_TOKEN 으로 열린 이슈(자동 감시 대부분)는 이벤트를 일으키지 않는다 -> 보장은 sync 가 한다.
  sync     열린 경보 이슈 전부 upsert + 업무가 열려 있는데 이슈가 닫힌 건 닫기 + 방치 감시. 매일 1회 + 수동 실행(= 일괄 등록).
  neglect  방치 감시만.

안전: 기본 --dry-run(GitHub 읽기·계획 출력만, 쓰기 0). 메일·외부 발송 없음. 이슈 댓글은 방치 표시 한 가지뿐이고
  dry-run 에서는 달지 않는다. 비밀값은 로그에 쓰지 않는다. AI 호출 0 · 유료 API 0.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

# ── 라벨 -> 팀 (코드 한 곳의 표 · tests/test_ops_alarm.py 가 고정) ─────────────────────────
# 위에서 아래로 훑어 처음 맞는 규칙의 팀을 쓴다 -> 더 구체적인 라벨이 위에 온다.
#   감시 자체 실패(reconcile-monitor) 는 반드시 reconcile-* 접두 규칙보다 위(개발팀이 고친다).
#   발행·브리프 계열(발행 전 점검 · 발행 감시 · 체크리스트 · 뉴스레터)은 수집 계열 라벨(automation-*)과 함께 붙어도 브리프편집팀.
LABEL_TEAM: tuple[tuple[str, str], ...] = (
    ("reconcile-monitor", "dev"),
    ("site-errors", "dev"),               # 사이트 오류 징후(Cloudflare 실측 · grm-site-error-watch.yml)
    ("grm-fda-coverage", "brief"),
    ("grm-publish-watchdog", "brief"),
    ("checklist-verify", "brief"),
    ("newsletter-freshness", "brief"),
    ("quiz-freshness", "education"),
    ("growth-weekly", "growth"),
    ("findings-backlog", "translation"),
    ("findings-review-queue", "quality"),
    ("taxonomy-drift", "quality"),
    ("classification-audit", "quality"),
    ("findings-facets", "quality"),
    ("findings-search-cache-sync", "quality"),
    ("findings-search-cache-health", "infra"),  # 검색 캐시 daily 갱신 잡 실패·신선 부족(151 · grm-findings-search-cache-health.yml)
    ("db-size-health", "infra"),  # DB 크기 무료 한도 근접(153 · grm-db-size-health.yml)
    ("grm-findings", "quality"),
    ("content-completeness", "quality"),  # 내용 완전성(잘림·안 쓰이는 칸 · content_completeness.py)
    ("reconciliation", "infra"),
    ("source-verify", "infra"),
    ("grm-intake", "infra"),
    ("keepalive", "infra"),
    ("automation-failure", "infra"),
    ("automation-warning", "infra"),
)
PREFIX_TEAM: tuple[tuple[str, str], ...] = (("reconcile-", "infra"),)
# 표에 없어도 '경보'로 보는 접두(새 감시가 라벨만 붙이고 표를 안 고쳐도 경보를 놓치지 않는다) -> 종합상황실로 떨어진다.
ALARM_PREFIXES = ("automation-", "grm-", "reconcile-")
FALLBACK_TEAM = "situation-room"
NEGLECT_MARK = "<!-- ops-alarm-neglect -->"
TITLE_MAX = 120


def is_alarm(labels: list[str]) -> bool:
    known = {k for k, _ in LABEL_TEAM}
    return any(l in known or l.startswith(ALARM_PREFIXES) for l in labels)


def team_for(labels: list[str]) -> str:
    """라벨 모음 -> 팀 slug. 경보 라벨이 하나도 없으면 호출하지 않는다(is_alarm 먼저). 표에 없는 경보는 종합상황실."""
    have = set(labels)
    for label, team in LABEL_TEAM:
        if label in have:
            return team
    for prefix, team in PREFIX_TEAM:
        if any(l.startswith(prefix) for l in labels):
            return team
    return FALLBACK_TEAM


def issue_view(raw: dict[str, Any]) -> dict[str, Any]:
    """GitHub 이슈 JSON(API·이벤트 공통) -> 우리가 쓰는 필드."""
    labels = sorted(str(l.get("name") if isinstance(l, dict) else l) for l in raw.get("labels") or [])
    return {"number": int(raw["number"]), "title": str(raw.get("title") or "")[:TITLE_MAX * 2],
            "url": str(raw.get("html_url") or ""), "state": "closed" if raw.get("state") == "closed" else "open",
            "labels": labels, "is_pr": "pull_request" in raw}


def rpc_args(iss: dict[str, Any]) -> dict[str, Any]:
    return {"p_issue": iss["number"], "p_title": iss["title"], "p_url": iss["url"], "p_state": iss["state"],
            "p_labels": iss["labels"], "p_team": team_for(iss["labels"])}


def plan_sync(open_issues: list[dict[str, Any]], linked_open: set[int]) -> dict[str, list[int]]:
    """열린 경보 이슈 목록 + DB 의 '열림' 연결 -> 할 일. 순수 함수(시험 대상).
    to_upsert: 열린 경보 이슈 전부(멱등이라 기존 것도 다시 보낸다 -> 제목·팀·재오픈 동기화).
    to_close_check: DB 엔 열림인데 지금 열린 목록에 없는 이슈 -> 닫혔는지 확인해 닫는다."""
    nums = [i["number"] for i in open_issues if not i["is_pr"] and is_alarm(i["labels"])]
    return {"to_upsert": sorted(nums), "to_close_check": sorted(n for n in linked_open if n not in set(nums))}


# ── 입출력 ───────────────────────────────────────────────────────────────────
def gh_json(args: list[str]) -> Any:
    out = subprocess.run(["gh", "api", "-H", "X-GitHub-Api-Version: 2022-11-28", *args], check=True,
                         capture_output=True, text=True, encoding="utf-8").stdout
    return json.loads(out) if out.strip() else None


def list_open_issues(repo: str) -> list[dict[str, Any]]:
    out = subprocess.run(["gh", "api", "--paginate", "-H", "X-GitHub-Api-Version: 2022-11-28",
                          f"repos/{repo}/issues?state=open&per_page=100"], check=True,
                         capture_output=True, text=True, encoding="utf-8").stdout
    # --paginate 는 페이지마다 JSON 배열을 이어 붙여 출력한다 -> 배열 경계를 풀어 읽는다.
    dec, i, items = json.JSONDecoder(), 0, []
    while i < len(out):
        while i < len(out) and out[i].isspace():
            i += 1
        if i >= len(out):
            break
        obj, i = dec.raw_decode(out, i)
        items += obj if isinstance(obj, list) else [obj]
    return [issue_view(x) for x in items]


class Rest:
    def __init__(self, url: str, key: str):
        import requests  # 지역 import — 순수 함수 시험은 requests 없이 돈다
        self.requests = requests
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def rpc(self, name: str, body: dict[str, Any]) -> Any:
        r = self.requests.post(f"{self.base}/rpc/{name}", headers=self.h, json=body, timeout=30)
        if not r.ok:
            raise RuntimeError(f"{name} 실패: status={r.status_code}")
        return r.json()

    def linked_open(self) -> set[int]:
        r = self.requests.get(f"{self.base}/ops_alarm", headers=self.h, timeout=30,
                              params={"select": "issue_number", "issue_state": "eq.open", "limit": "1000"})
        if not r.ok:
            raise RuntimeError(f"ops_alarm 읽기 실패: status={r.status_code}")
        return {int(x["issue_number"]) for x in r.json()}


def neglect_comment(days: int, team: str) -> str:
    return (f"{NEGLECT_MARK}\n이 경보는 운영 센터 업무(담당: {team})로 등록된 뒤 {days}일째 지시함에서 움직이지 않았습니다. "
            "종합상황실에 '방치 경보' 업무를 올렸습니다. 담당을 정하거나 이슈를 처리해 주세요.")


def comment(repo: str, number: int, body: str) -> None:
    subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"], check=True,
                   capture_output=True, text=True, encoding="utf-8")


# ── 실행 ─────────────────────────────────────────────────────────────────────
def do_upsert(iss: dict[str, Any], rest: "Rest | None", log: list[str]) -> None:
    a = rpc_args(iss)
    if rest is None:
        log.append(f"[dry-run] #{iss['number']} {iss['state']} -> 팀 {a['p_team']} · {iss['title'][:60]}")
        return
    try:
        r = rest.rpc("ops_alarm_upsert", a)
        log.append(f"#{iss['number']} {r.get('action')} (팀 {a['p_team']})")
    except Exception as exc:  # 키가 담길 수 있는 메시지는 쓰지 않는다
        log.append(f"::warning::#{iss['number']} upsert 실패 {type(exc).__name__}")


def run_neglect(repo: str, rest: "Rest | None", log: list[str]) -> None:
    if rest is None:
        log.append("[dry-run] 방치 감시는 쓰기 모드에서만 돈다(읽기 목록은 DB 접속이 있을 때 아래에 표시)")
        return
    try:
        flagged = rest.rpc("ops_alarm_neglect_scan", {})
    except Exception as exc:
        log.append(f"::warning::방치 감시 실패 {type(exc).__name__}")
        return
    log.append(f"방치 경보 새로 올림 {len(flagged)}건")
    for f in flagged:
        try:
            comment(repo, int(f["issue"]), neglect_comment(int(f["days"]), str(f["team"])))
        except Exception as exc:
            log.append(f"::warning::#{f.get('issue')} 방치 댓글 실패 {type(exc).__name__}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("event", "sync", "neglect"), required=True)
    ap.add_argument("--dry-run", action="store_true", help="계획 출력만(쓰기 0)")
    a = ap.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "MINHOYEOM/grm-api-intake")
    rest = None
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        rest = Rest(url, key)
    elif not a.dry_run:
        print("::warning::SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 미설정 — 쓰기 없이 끝냄")
    write = rest if not a.dry_run else None
    log: list[str] = []

    if a.mode == "event":
        path = os.environ.get("GITHUB_EVENT_PATH", "")
        raw = (json.load(open(path, encoding="utf-8")) if path else {}).get("issue")
        if not raw:
            print("이슈 이벤트가 아님 — 건너뜀")
            return 0
        iss = issue_view(raw)
        if iss["is_pr"] or not is_alarm(iss["labels"]):
            print(f"#{iss['number']} 경보 라벨 없음 — 건너뜀")
            return 0
        do_upsert(iss, write, log)
    elif a.mode == "sync":
        open_issues = list_open_issues(repo)
        linked: set[int] = set()
        if rest is not None:
            try:
                linked = rest.linked_open()
            except Exception as exc:
                log.append(f"::warning::연결 읽기 실패 {type(exc).__name__} (131 적용 전이면 정상) — 닫힘 확인 건너뜀")
        by_num = {i["number"]: i for i in open_issues}
        plan = plan_sync(open_issues, linked)
        log.append(f"열린 경보 이슈 {len(plan['to_upsert'])}건 · 닫힘 확인 대상 {len(plan['to_close_check'])}건")
        for n in plan["to_upsert"]:
            do_upsert(by_num[n], write, log)
        for n in plan["to_close_check"]:
            try:
                iss = issue_view(gh_json([f"repos/{repo}/issues/{n}"]))
            except Exception as exc:
                log.append(f"::warning::#{n} 조회 실패 {type(exc).__name__}")
                continue
            do_upsert(iss, write, log)
        run_neglect(repo, write, log)
    else:
        run_neglect(repo, write, log)

    if rest is not None and a.dry_run:
        try:
            for n in rest.rpc("ops_alarm_neglected", {}):
                log.append(f"[dry-run] 방치 중: #{n['issue_number']} {n['days']}일 · 팀 {n['team']}")
        except Exception as exc:
            log.append(f"[dry-run] 방치 목록 읽기 실패 {type(exc).__name__} (131 적용 전이면 정상)")
    text = "\n".join(log) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    print(text)
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### 경보 -> 업무\n```\n" + text + "```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
