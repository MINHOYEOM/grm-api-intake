# -*- coding: utf-8 -*-
"""수집 소스별 건수 — grm-intake.yml 이 매 실행 끝(always)에 부른다. 결과는 아티팩트 grm-intake-counts.

Admin '수집 현황'(admin-ops.js renderIntake)이 읽는 한 줄 기록의 원천이다.
원천은 collect_intake 가 쓰는 health JSON 의 sources[].fetched (기계가 읽는 형태). 실행 로그 끝의
`PICS=0 · ECA=10 · … · 합계=445건` 줄은 health JSON 이 없을 때의 대체 입력(parse_summary_line)이다.
건수·소스 이름·실행 번호·링크만 담는다 — 본문·수신자·비밀값 없음.
"""
import json
import os
import re
import sys

SCHEMA = "grm-intake-counts/v1"
_PAIR = re.compile(r"([A-Za-z0-9][A-Za-z0-9_-]*)=(\d+)")


def parse_summary_line(text):
    """'수집 완료: FR=1 · ECA=10 · … · 합계=445건' → {"sources": {라벨: 건수}, "total": 합계 | None}. 줄이 없으면 None."""
    for line in reversed(str(text or "").splitlines()):
        if "합계=" not in line:
            continue
        found = {}
        total = None
        for key, val in _PAIR.findall(line.replace("합계=", "TOTAL=")):
            if key == "TOTAL":
                total = int(val)
            else:
                found[key] = int(val)
        return {"sources": found, "total": total}
    return None


def from_health(health, run_id="", run_url="", created_at="", conclusion=""):
    """health JSON(grm-health/v1) → 기록 한 줄. 합계는 소스별 fetched 의 합(수집기 합계 줄과 같은 정의)."""
    rows = []
    for s in health.get("sources") or []:
        if not isinstance(s, dict) or not s.get("key"):
            continue
        rows.append({"key": s["key"], "label": s.get("label") or s["key"],
                     "fetched": int(s.get("fetched") or 0), "error": bool(s.get("error"))})
    return {
        "schema": SCHEMA,
        "run_id": int(run_id) if str(run_id).isdigit() else None,
        "url": run_url or None,
        "created_at": created_at or None,
        "conclusion": conclusion or None,
        "run_date_kst": health.get("run_date_kst"),
        "event": health.get("event_name"),
        "dry_run": bool(health.get("dry_run")),
        "active": sorted(health.get("active_sources") or []),
        "total": sum(r["fetched"] for r in rows),
        "sources": rows,
    }


def main(argv):
    if len(argv) != 3:
        print("usage: intake_counts.py <health.json> <out_dir>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            health = json.load(f)
    except (OSError, ValueError) as exc:
        print("health JSON 없음/손상 — 건수 기록 생략: %s" % type(exc).__name__)
        return 0
    repo, run_id = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    from datetime import datetime, timezone
    rec = from_health(health, run_id,
                      "https://github.com/%s/actions/runs/%s" % (repo, run_id) if repo and run_id else "",
                      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    os.makedirs(argv[2], exist_ok=True)
    with open(os.path.join(argv[2], "intake_counts.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("소스별 건수 기록: 합계 %d건 (%d개 소스)" % (rec["total"], len(rec["sources"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
