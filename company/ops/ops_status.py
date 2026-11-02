# -*- coding: utf-8 -*-
"""Actions 실행 요약 — grm-ops-status.yml 이 부른다. 결과는 `claude/ops-status` 브랜치 status.json.

클라우드 세션은 /actions 가 막혀 있다. 이 스크립트가 대신 읽어 브랜치 파일로 내려 준다.
비밀값·로그 본문·수신자 정보는 담지 않는다(실행 번호·상태·시각·제목만).
"""
import datetime
import json
import subprocess
import sys

WATCH = [
    "grm-intake.yml", "grm-delta-bridge.yml", "grm-web-publish.yml", "grm-web-deploy.yml",
    "grm-scaffold-mirror.yml", "grm-watchlist-notify.yml", "grm-newsletter-send.yml",
    "grm-findings-translate-apply.yml", "grm-findings-backfill-fetch.yml",
    "grm-wl-truncation-restore-plan.yml", "grm-stranded-batch-pr.yml", "grm-cloud-request.yml",
]
KEEP_RUNS = 5
INTAKE_PROBE = 6  # 아티팩트 보유 여부를 확인할 최근 intake 성공 run 수
FIELDS = "databaseId,status,conclusion,event,createdAt,updatedAt,headBranch,displayTitle,url"


def gh(args):
    return subprocess.run(["gh"] + args, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def collect(run=gh, repo="", now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "workflows": {}, "intake_artifacts": []}
    for wf in WATCH:
        try:
            runs = json.loads(run(["run", "list", "--workflow", wf, "-L", str(KEEP_RUNS),
                                   "--json", FIELDS]))
        except Exception as exc:  # 한 워크플로의 실패가 전체를 막지 않는다
            out["workflows"][wf] = {"error": type(exc).__name__}
            continue
        out["workflows"][wf] = [{k: r.get(k) for k in FIELDS.split(",")} for r in runs]
    ok = [r for r in (out["workflows"].get("grm-intake.yml") or []) if isinstance(r, dict)
          and r.get("conclusion") == "success"][:INTAKE_PROBE]
    for r in ok:
        try:
            arts = json.loads(run(["api", "repos/%s/actions/runs/%s/artifacts" % (repo, r["databaseId"]),
                                   "--jq", "[.artifacts[]|{name,expired,size_in_bytes}]"]))
        except Exception as exc:
            arts = [{"error": type(exc).__name__}]
        web = [a for a in arts if a.get("name") == "grm-web-brief"]
        out["intake_artifacts"].append({
            "run_id": r["databaseId"], "created_at": r.get("createdAt"),
            "has_web_brief": bool(web) and not web[0].get("expired"),
        })
    return out


def same_ignoring_time(a, b):
    strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}
    return strip(a) == strip(b)


def main(argv):
    import os
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if len(argv) != 2:
        print("usage: ops_status.py <status.json>", file=sys.stderr)
        return 2
    new = collect(repo=repo)
    try:
        with open(argv[1], encoding="utf-8") as f:
            old = json.load(f)
        if same_ignoring_time(old, new):
            print("변경 없음")
            return 0
    except (OSError, ValueError):
        pass
    with open(argv[1], "w", encoding="utf-8", newline="\n") as f:
        json.dump(new, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("status.json 갱신")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
