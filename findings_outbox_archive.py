#!/usr/bin/env python3
"""[M9c 정리 수리 2026-11-26] 반영이 끝난 translations/outbox/*.json 을 translations/applied/ 로 옮긴다.

멈춘 원인(11-14~11-26 outbox 15개가 쌓임): 일일 번역 지시문 9단계가
`git log -1 --format=%H FETCH_HEAD -- translations/outbox` 로 "outbox 를 마지막으로 바꾼 main 커밋"을 골랐다.
그런데 git 의 경로 단순화는 머지 커밋을 건너뛰고 **PR 브랜치의 원래 커밋**을 돌려준다. 반영 워크플로
(`grm-findings-translate-apply.yml`, push to main)는 **머지 커밋**에서만 돌기 때문에 그 커밋에는
`Apply queued translation outbox → Supabase` 체크가 없고, 조건이 영원히 거짓이라 정리가 매일 보류됐다.
11-13 까지는 PC 백업이 정리했다.

규칙(결정론 — 지시문이 아니라 이 코드가 판정한다):
- 파일마다 main 의 **첫 부모 줄기**(`--first-parent`)에서 그 파일을 더한 커밋을 찾는다.
- 그 커밋 또는 그 뒤의 첫 부모 커밋 중 outbox 를 건드린 커밋에서 반영 체크가 success 면 반영된 것이다
  (반영 서비스는 실행마다 outbox 전체를 처리한다).
- 체크를 못 읽거나(권한·네트워크) 아직 success 가 없으면 옮기지 않는다 — 모르면 남긴다.
- `--keep` 으로 준 파일(오늘 배치 등)은 옮기지 않는다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

APPLY_CHECK = "Apply queued translation outbox → Supabase"
OUTBOX = "translations/outbox"
APPLIED = "translations/applied"


def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def outbox_commits(ref: str) -> list[str]:
    """main 첫 부모 줄기에서 outbox 를 건드린 커밋(오래된 것부터). 머지 커밋이 나온다 — 반영 체크가 붙는 곳."""
    out = _git("log", "--first-parent", "--reverse", "--format=%H", ref, "--", OUTBOX)
    return [x for x in out.split() if x]


def added_commit(ref: str, path: str) -> str | None:
    out = _git("log", "--first-parent", "--diff-filter=A", "--format=%H", "-1", ref, "--", path)
    return out.strip() or None


def decide(files: dict[str, str | None], commits: list[str], apply_ok: Callable[[str], bool | None],
           keep: set[str]) -> dict[str, str]:
    """파일 → "archive" | "keep:<이유>". 순수 함수(입력: 파일별 추가 커밋, outbox 커밋 줄, 체크 조회)."""
    order = {c: i for i, c in enumerate(commits)}
    cache: dict[str, bool | None] = {}

    def ok(c: str) -> bool | None:
        if c not in cache:
            cache[c] = apply_ok(c)
        return cache[c]

    out: dict[str, str] = {}
    for name, added in sorted(files.items()):
        if name in keep:
            out[name] = "keep:requested"
            continue
        if not added or added not in order:
            out[name] = "keep:no_main_commit"
            continue
        verdict = "keep:no_success_yet"
        for c in commits[order[added]:]:
            r = ok(c)
            if r is None:
                verdict = "keep:check_unreadable"
                break
            if r:
                verdict = "archive"
                break
        out[name] = verdict
    return out


def gh_apply_ok(repo: str) -> Callable[[str], bool | None]:
    def check(sha: str) -> bool | None:
        try:
            res = subprocess.run(["gh", "api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100"],
                                 check=True, capture_output=True, text=True, encoding="utf-8")
            runs = json.loads(res.stdout).get("check_runs") or []
        except (subprocess.CalledProcessError, OSError, ValueError):
            return None
        return any(r.get("name") == APPLY_CHECK and r.get("conclusion") == "success" for r in runs)
    return check


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="반영 끝난 outbox 배치를 applied/ 로(git mv). 모르면 남긴다.")
    ap.add_argument("--ref", default="FETCH_HEAD", help="방금 fetch 한 main (기본 FETCH_HEAD)")
    ap.add_argument("--repo", default="MINHOYEOM/grm-api-intake")
    ap.add_argument("--keep", action="append", default=[], help="옮기지 않을 파일 이름(여러 번)")
    ap.add_argument("--apply", action="store_true", help="실제로 git mv 한다(없으면 판정만)")
    args = ap.parse_args(argv)
    names = sorted(p.name for p in Path(OUTBOX).glob("*.json"))
    files = {n: added_commit(args.ref, f"{OUTBOX}/{n}") for n in names}
    verdicts = decide(files, outbox_commits(args.ref), gh_apply_ok(args.repo), set(args.keep))
    moved = []
    for name, v in verdicts.items():
        if v == "archive" and args.apply:
            subprocess.run(["git", "mv", f"{OUTBOX}/{name}", f"{APPLIED}/{name}"], check=True)
            moved.append(name)
    print(json.dumps({"verdicts": verdicts, "moved": moved}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
