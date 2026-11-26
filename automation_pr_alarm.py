#!/usr/bin/env python3
"""주입된 열린 PR 목록에서 3일 이상 지난 automation PR을 센다."""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone


def count_stale_automation_prs(prs: Iterable[Mapping[str, str]], now: datetime) -> int:
    """`gh pr list --json headRefName,createdAt,state` 결과로 72시간 경계를 판정한다."""
    cutoff = now.astimezone(timezone.utc) - timedelta(days=3)
    return sum(
        datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00")) <= cutoff
        for pr in prs
        if pr["state"] == "OPEN" and pr["headRefName"].startswith("automation/")
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    count = count_stale_automation_prs(json.load(sys.stdin), datetime.now(timezone.utc))
    message = f"3일 이상 열린 automation/* PR {count}건"
    print(f"::warning::{message}" if count else message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
