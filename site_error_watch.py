#!/usr/bin/env python3
"""사이트 오류 징후 일일 감시 — Cloudflare 실측(방문자가 실제로 받은 응답) + 배포 실패.

왜 필요한가: `site_probe.py`(경보 "사이트 합성 점검 실패")는 **우리가 정한 페이지 5종**을
직접 열어 본다(능동 점검). 그 바깥 — 방문자가 실제로 누른 링크가 404 였는지, 엣지가 5xx 를
줬는지 — 는 방문자가 겪어야만 알 수 있었다. 이 스크립트는 Cloudflare 존 분석(GraphQL
`httpRequestsAdaptiveGroups`)으로 **어제 하루 실제 요청**을 읽어 그 빈칸을 메운다(수동 관측).
역할 분담: 합성 점검 = "정해 둔 페이지가 지금 뜨는가", 이 감시 = "어제 방문자 요청에서 오류가
평소보다 늘었는가 + 마지막 배포가 실패했는가". 같은 이슈를 두 번 열지 않도록 라벨·제목이 다르다.

## 무엇을 보는가 (2026-10-09~11-07 30일 실측으로 정함)

Free 플랜 존이라 지연(TTFB)·리퍼러 필드는 권한이 없다(authz) → 지연은 합성 점검이 맡는다.
요청 수는 표본(sampleInterval ≈ 10)이라 10 단위 추정값이다.

1. **방문자 5xx** — 30일 합계 약 10건(표본 1개). 정적 사이트라 평소 0 이다.
   하루 `FIVEXX_MIN`(50) 이상 또는 방문자 요청의 `FIVEXX_RATE`(1%) 이상이면 경보.
   (`requestSource=earlyHintsCache` 의 504 는 방문자 응답이 아니라 Early Hints 캐시 내부라 뺀다.)
2. **사이트 경로 404 급증** — 404 는 하루 20~2,180건인데 대부분이 취약점 스캐너
   (`/.env`·`/.git/config`·`*.php`)라 총량은 신호가 아니다. 스캐너 모양을 걸러내고 우리 사이트
   구역(`SITE_SECTIONS`)에 속한 경로만 세면, 이 스크립트와 같은 15일 창(표본이 더 촘촘하다)으로
   10-23~11-07 을 날마다 다시 잰 결과 평소 하루 0~30건·경로 20개 이하였고, 10-25 하루만
   920건·경로 83개(`/en/findings/doc/…/null` — 깨진 링크)로 튀었다. 그래서:
   어제 사이트 404 ≥ `SPIKE_MIN`(150) 이면서 직전 14일 중앙값(하한 10)의 `SPIKE_RATIO`(5)배
   이상, 또는 서로 다른 404 경로 ≥ `DISTINCT_MIN`(40 — 평소 최대 20의 2배) 이면 경보.
   (경로 기준 15 는 11-06 의 27건·20경로 — 경로마다 1~2건인 잡음 — 를 경보로 만들어 올렸다.)
3. **배포 실패** — `grm-web-deploy.yml` 의 main 마지막 완료 실행이 실패면 경보(GitHub API).

## 이슈

경보면 제목 고정 이슈(`ISSUE_TITLE`, 라벨 `site-errors` → 운영 센터 개발팀)를 열거나 그 이슈에
오늘 수치를 댓글로 단다. 정상으로 돌아오면 해소 댓글을 달고 닫는다. 조회 자체가 실패하면
(토큰 권한 등) 별도 이슈(`FAIL_TITLE`, 라벨 `automation-failure`)로 낸다.

안전: 기본 dry-run(조회·판정·계획 출력만, GitHub 쓰기 0). `--apply` 이고 `ENABLE_SITE_ERROR_WATCH=true`
일 때만 이슈를 쓴다. Cloudflare 는 읽기 전용 조회만 한다. 토큰 값은 출력하지 않는다. 비용 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

GRAPHQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
DEFAULT_ZONE_TAG = "3e593e6928126fcaee3d26edf981faf1"   # grm-solutions.com — 대시보드 URL 에 보이는 값(비밀 아님)
DEPLOY_WORKFLOW = "grm-web-deploy.yml"

LABEL = "site-errors"
ISSUE_TITLE = "사이트 오류 징후 감지 (Cloudflare 실측)"
FAIL_LABEL = "automation-failure"
FAIL_TITLE = "사이트 오류 감시 실행 실패"

FIVEXX_MIN = 50
FIVEXX_RATE = 0.01
SPIKE_MIN = 150
SPIKE_RATIO = 5
BASELINE_FLOOR = 10
DISTINCT_MIN = 40
BASELINE_DAYS = 14
TOP_PATHS = 15

# 우리 사이트의 첫 경로 구역(`/en/` 접두는 벗기고 본다). 30일 2xx 실측 + web/templates 기준.
# `admin` 은 뺐다 — 실측 404 가 `/admin/utils.js`·`/admin/products` 같은 스캐너뿐이었다.
SITE_SECTIONS = frozenset({
    "findings", "glossary", "library", "briefs", "archive", "about", "guide", "quiz",
    "graph", "me", "privacy", "terms", "welcome", "assets",
})
# 스캐너·개발자 도구 모양: 점으로 시작하는 경로 조각(.env/.git/.aws), 서버 스크립트·설정 확장자,
# 워드프레스, 소스맵(.map — 브라우저 개발자 도구가 자동으로 요청한다).
SCANNER = re.compile(
    r"(^|/)\.|\.(php\d?|env|asp|aspx|jsp|cgi|bak|old|sql|ya?ml|ini|log|tfstate|swp|map)$|wp-|phpinfo|cgi-bin",
    re.I,
)

QUERY = """
query SiteErrors($zone: string!, $start: Time!, $end: Time!) {
  viewer { zones(filter: {zoneTag: $zone}) {
    totals: httpRequestsAdaptiveGroups(limit: 2000,
        filter: {datetime_geq: $start, datetime_lt: $end, requestSource: "eyeball"}) {
      count dimensions { date edgeResponseStatus } }
    notFound: httpRequestsAdaptiveGroups(limit: 10000,
        filter: {datetime_geq: $start, datetime_lt: $end, requestSource: "eyeball", edgeResponseStatus: 404}) {
      count dimensions { datetimeHour clientRequestPath } }
    serverErrors: httpRequestsAdaptiveGroups(limit: 200,
        filter: {datetime_geq: $start, datetime_lt: $end, requestSource: "eyeball", edgeResponseStatus_geq: 500}) {
      count dimensions { datetimeHour clientRequestPath edgeResponseStatus } }
  } }
}
"""


class FetchError(RuntimeError):
    """조회 실패(네트워크·권한·응답 형태). 사이트 오류와 구별해 별도 이슈로 낸다."""


# ── 순수 함수(시험 대상) ──────────────────────────────────────────────────────
def section_of(path: str) -> str:
    segs = [s for s in path.split("/") if s]
    if segs and segs[0] == "en":
        segs = segs[1:]
    return segs[0] if segs else ""


def is_site_path(path: str) -> bool:
    """스캐너가 아닌, 우리 사이트 구역의 경로인가."""
    if SCANNER.search(path):
        return False
    return section_of(path) in SITE_SECTIONS


def to_kst(hour_iso: str) -> str:
    t = dt.datetime.strptime(hour_iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%m-%d %H시")


def day_window(day: dt.date) -> tuple[str, str]:
    start = day - dt.timedelta(days=BASELINE_DAYS)
    end = day + dt.timedelta(days=1)
    return f"{start.isoformat()}T00:00:00Z", f"{end.isoformat()}T00:00:00Z"


def judge(zone: dict[str, Any], day: dt.date, deploy: dict[str, Any] | None) -> dict[str, Any]:
    """조회 결과 + 배포 상태 -> 판정. day 는 UTC 날짜(판정 대상 하루), 그 앞 14일이 평소 기준."""
    d = day.isoformat()
    total = sum(r["count"] for r in zone.get("totals") or [] if r["dimensions"]["date"] == d)
    all404 = sum(r["count"] for r in zone.get("totals") or []
                 if r["dimensions"]["date"] == d and r["dimensions"]["edgeResponseStatus"] == 404)

    per_day: dict[str, int] = {}
    today_paths: dict[str, dict[str, Any]] = {}
    for r in zone.get("notFound") or []:
        path = r["dimensions"]["clientRequestPath"]
        if not is_site_path(path):
            continue
        hour = r["dimensions"]["datetimeHour"]
        rd = hour[:10]
        per_day[rd] = per_day.get(rd, 0) + r["count"]
        if rd == d:
            p = today_paths.setdefault(path, {"path": path, "count": 0, "first": hour, "last": hour})
            p["count"] += r["count"]
            p["first"] = min(p["first"], hour)
            p["last"] = max(p["last"], hour)
    site404 = per_day.get(d, 0)
    base_days = [(day - dt.timedelta(days=i)).isoformat() for i in range(1, BASELINE_DAYS + 1)]
    baseline = statistics.median([per_day.get(x, 0) for x in base_days])
    base_eff = max(baseline, BASELINE_FLOOR)

    errs = [r for r in zone.get("serverErrors") or [] if r["dimensions"]["datetimeHour"][:10] == d]
    fivexx = sum(r["count"] for r in errs)

    reasons: list[str] = []
    if fivexx >= FIVEXX_MIN or (total and fivexx >= 2 * BASELINE_FLOOR and fivexx / total >= FIVEXX_RATE):
        reasons.append(f"방문자 5xx 약 {fivexx}건(평소 0, 기준 {FIVEXX_MIN}건 또는 {FIVEXX_RATE:.0%})")
    if site404 >= SPIKE_MIN and site404 >= SPIKE_RATIO * base_eff:
        reasons.append(f"사이트 경로 404 약 {site404}건 — 평소(14일 중앙값 {baseline:g}건)의 "
                       f"{site404 / base_eff:.0f}배")
    if len(today_paths) >= DISTINCT_MIN:
        reasons.append(f"404 난 사이트 경로 {len(today_paths)}개(기준 {DISTINCT_MIN}개 — 깨진 링크가 퍼졌을 수 있음)")
    if deploy and deploy.get("conclusion") == "failure":
        reasons.append(f"마지막 사이트 배포 실패 ({deploy.get('created_at', '')[:16]} UTC)")

    top = sorted(today_paths.values(), key=lambda p: (-p["count"], p["path"]))[:TOP_PATHS]
    top_err = sorted(errs, key=lambda r: -r["count"])[:TOP_PATHS]
    return {
        "day": d, "alarm": bool(reasons), "reasons": reasons,
        "metrics": {"requests": total, "fivexx": fivexx, "all404": all404, "site404": site404,
                    "site404_baseline": baseline, "site404_paths": len(today_paths)},
        "top404": top,
        "top5xx": [{"path": r["dimensions"]["clientRequestPath"], "status": r["dimensions"]["edgeResponseStatus"],
                    "hour": r["dimensions"]["datetimeHour"], "count": r["count"]} for r in top_err],
        "deploy": deploy,
    }


def plan_issue(alarm: bool, existing: int | None) -> str:
    """경보 여부 + 열린 같은 제목 이슈 번호 -> create | comment | close | noop."""
    if alarm:
        return "comment" if existing else "create"
    return "close" if existing else "noop"


def render_body(v: dict[str, Any], run_url: str = "") -> str:
    m = v["metrics"]
    lines = [
        f"**{v['day']} (UTC 하루) 방문자 요청에서 오류 징후가 평소보다 많습니다.**", "",
        *[f"- {r}" for r in v["reasons"]], "",
        "| 항목 | 값 |", "|---|---|",
        f"| 방문자 요청 | 약 {m['requests']:,} |",
        f"| 방문자 5xx | 약 {m['fivexx']:,} |",
        f"| 404 전체(스캐너 포함) | 약 {m['all404']:,} |",
        f"| 사이트 경로 404 | 약 {m['site404']:,} (평소 {m['site404_baseline']:g}) |",
        f"| 404 난 사이트 경로 수 | {m['site404_paths']} |",
    ]
    if v["top404"]:
        lines += ["", "상위 404 경로 (시각은 KST, 표본 추정):", "",
                  "| 경로 | 건수 | 처음 | 마지막 |", "|---|---|---|---|"]
        lines += [f"| `{p['path'][:150]}` | {p['count']} | {to_kst(p['first'])} | {to_kst(p['last'])} |"
                  for p in v["top404"]]
    if v["top5xx"]:
        lines += ["", "5xx 경로:", "", "| 경로 | 상태 | 시각(KST) | 건수 |", "|---|---|---|---|"]
        lines += [f"| `{e['path'][:150]}` | {e['status']} | {to_kst(e['hour'])} | {e['count']} |"
                  for e in v["top5xx"]]
    if v.get("deploy") and v["deploy"].get("conclusion") == "failure":
        lines += ["", f"배포 실행: {v['deploy'].get('url', '')}"]
    lines += ["", "확인 순서: ① 상위 404 경로가 어느 페이지 링크에서 나오는지(템플릿·JS) "
              "② 마지막 배포 내역 ③ 5xx 면 Cloudflare Pages 상태.",
              "요청 수는 Cloudflare 표본(약 10건에 1건)이라 10 단위 추정값입니다."]
    if run_url:
        lines += ["", f"실행: {run_url}"]
    return "\n".join(lines)


def render_summary(v: dict[str, Any]) -> str:
    m = v["metrics"]
    head = "경보" if v["alarm"] else "정상"
    out = [f"## 사이트 오류 감시 {v['day']} — {head}", "",
           f"방문자 요청 약 {m['requests']:,} · 5xx 약 {m['fivexx']} · 404 전체 약 {m['all404']:,} · "
           f"사이트 경로 404 약 {m['site404']} (평소 {m['site404_baseline']:g}, 경로 {m['site404_paths']}개)"]
    out += [f"- {r}" for r in v["reasons"]]
    if v["top404"]:
        out += ["", "상위 사이트 404:"] + [f"- {p['count']} `{p['path'][:150]}`" for p in v["top404"][:5]]
    return "\n".join(out)


# ── 입출력 ───────────────────────────────────────────────────────────────────
def fetch_zone(token: str, zone_tag: str, day: dt.date, timeout: float = 60.0) -> dict[str, Any]:
    start, end = day_window(day)
    body = json.dumps({"query": QUERY, "variables": {"zone": zone_tag, "start": start, "end": end}}).encode()
    req = urllib.request.Request(GRAPHQL_ENDPOINT, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        raise FetchError(f"Cloudflare GraphQL HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise FetchError(f"Cloudflare GraphQL 조회 실패: {type(e).__name__}") from None
    if payload.get("errors"):
        msgs = "; ".join(str(x.get("message", ""))[:160] for x in payload["errors"])
        hint = " — 토큰에 이 존(grm-solutions.com)의 Zone Analytics Read 권한이 필요하다" if "authz" in json.dumps(
            payload["errors"]) or "access" in msgs else ""
        raise FetchError(f"GraphQL 오류: {msgs}{hint}")
    zones = ((payload.get("data") or {}).get("viewer") or {}).get("zones") or []
    if not zones:
        raise FetchError("응답에 zones 가 없다 — zone tag 또는 토큰 권한 확인")
    return zones[0]


def gh(args: list[str], check: bool = True) -> str:
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True, encoding="utf-8").stdout


def latest_deploy(repo: str) -> dict[str, Any] | None:
    out = gh(["run", "list", "-R", repo, "--workflow", DEPLOY_WORKFLOW, "--branch", "main", "--limit", "10",
              "--json", "status,conclusion,createdAt,url"])
    for r in json.loads(out or "[]"):
        if r.get("status") == "completed" and r.get("conclusion") not in ("cancelled", "skipped"):
            return {"conclusion": r.get("conclusion"), "created_at": r.get("createdAt", ""), "url": r.get("url", "")}
    return None


def find_open_issue(repo: str, title: str, label: str) -> int | None:
    out = gh(["issue", "list", "-R", repo, "--state", "open", "--label", label, "--limit", "50",
              "--json", "number,title"])
    for i in json.loads(out or "[]"):
        if i.get("title") == title:
            return int(i["number"])
    return None


def ensure_label(repo: str, label: str, color: str, desc: str) -> None:
    gh(["label", "create", label, "-R", repo, "--color", color, "--description", desc, "--force"], check=False)


def apply_issue(repo: str, action: str, number: int | None, title: str, label: str, body: str) -> None:
    if action == "create":
        gh(["issue", "create", "-R", repo, "--title", title, "--label", label, "--body", body])
    elif action == "comment" and number:
        gh(["issue", "comment", str(number), "-R", repo, "--body", body])
    elif action == "close" and number:
        gh(["issue", "comment", str(number), "-R", repo, "--body", body])
        gh(["issue", "close", str(number), "-R", repo])


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--day", help="판정할 UTC 날짜(YYYY-MM-DD). 기본 어제(UTC).")
    ap.add_argument("--apply", action="store_true", help="이슈를 실제로 쓴다(ENABLE_SITE_ERROR_WATCH=true 도 필요).")
    ap.add_argument("--report", help="판정 JSON 을 쓸 경로")
    args = ap.parse_args(argv)

    day = dt.date.fromisoformat(args.day) if args.day else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    enabled = os.environ.get("ENABLE_SITE_ERROR_WATCH", "false").strip().lower() == "true"
    write = args.apply and enabled
    if args.apply and not enabled:
        print("ENABLE_SITE_ERROR_WATCH 가 true 가 아니라 이슈는 쓰지 않는다(조회·판정만).")
    repo = os.environ.get("GITHUB_REPOSITORY", "MINHOYEOM/grm-api-intake")
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/"
               f"{os.environ['GITHUB_RUN_ID']}") if os.environ.get("GITHUB_RUN_ID") else ""
    token = os.environ.get("CLOUDFLARE_ZONE_ANALYTICS_TOKEN") or os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN") or ""
    zone_tag = os.environ.get("CLOUDFLARE_ZONE_TAG") or DEFAULT_ZONE_TAG

    try:
        if not token:
            raise FetchError("Cloudflare 토큰 시크릿이 비었다(CLOUDFLARE_ZONE_ANALYTICS_TOKEN / CLOUDFLARE_ANALYTICS_TOKEN)")
        zone = fetch_zone(token, zone_tag, day)
    except FetchError as e:
        print(f"조회 실패: {e}")
        if write:
            ensure_label(repo, FAIL_LABEL, "d73a4a", "자동 작업 실패")
            num = find_open_issue(repo, FAIL_TITLE, FAIL_LABEL)
            body = f"사이트 오류 감시(site_error_watch.py)가 Cloudflare 실측을 읽지 못했습니다.\n\n- 사유: {e}\n- 실행: {run_url}"
            apply_issue(repo, "comment" if num else "create", num, FAIL_TITLE, FAIL_LABEL, body)
        return 2

    try:
        deploy = latest_deploy(repo)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        print(f"배포 상태 조회 실패(판정에서 제외): {type(e).__name__}")
        deploy = None

    v = judge(zone, day, deploy)
    print(render_summary(v))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(v, fh, ensure_ascii=False, indent=1)

    num = find_open_issue(repo, ISSUE_TITLE, LABEL) if (write or os.environ.get("GH_TOKEN")) else None
    action = plan_issue(v["alarm"], num)
    print(f"\n이슈 계획: {action}" + (f" #{num}" if num else "") + ("" if write else " (dry-run — 쓰지 않음)"))
    if write:
        if action in ("create", "comment"):
            ensure_label(repo, LABEL, "b60205", "사이트 오류 징후(Cloudflare 실측) — 운영 센터 개발팀")
            apply_issue(repo, action, num, ISSUE_TITLE, LABEL, render_body(v, run_url))
        elif action == "close":
            apply_issue(repo, action, num, ISSUE_TITLE, LABEL,
                        f"해소 확인: {v['day']} 방문자 요청에서 기준을 넘는 오류 징후가 없습니다.\n\n"
                        + render_summary(v) + (f"\n\n실행: {run_url}" if run_url else ""))
        fnum = find_open_issue(repo, FAIL_TITLE, FAIL_LABEL)
        if fnum:
            apply_issue(repo, "close", fnum, FAIL_TITLE, FAIL_LABEL, f"조회 복구 확인: {run_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
