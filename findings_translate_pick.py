#!/usr/bin/env python3
"""[적체 예방 2026-11-26] 일일 번역 배치의 대상 고르기 — 건수 대신 글자 예산, 긴 행 기아 없음.

실측: 11-24 에 원문 중앙값 7,568자인 WL 263행이 한꺼번에 들어왔다. 일일 지시문은 "최신순 100건, 품질이 어려우면
건수를 줄인다"였고, 번역 세션이 매일 짧은 행만 골라 긴 행을 다음 날로 넘겼다(#1981 '긴 원문 42건 이월').
다음 날도 같은 기준이라 긴 행은 영원히 밀리고, 백로그 모니터가 이틀 연속 319 로 빨갛게 됐다.

규칙(결정론 — 번역 세션의 판단에 맡기지 않는다):
- 예산 = 원문 글자 수(기본 40,000자)와 건수(기본 100건) 중 먼저 닿는 쪽.
- 두 줄에서 번갈아 뽑는다: A = published_date 최신순(새 문서가 빨리 공개되게),
  B = published_date 오래된 순(밀린 행이 언젠가 반드시 뽑히게). 어느 줄도 행을 건너뛰지 않는다.
- 줄의 맨 앞 행이 남은 예산보다 길어도 그날 첫 행이면 넣는다(한 행이 예산보다 커서 영원히 못 뽑히는 일 방지).
- 잔여 글자 수가 예산의 5일치를 넘으면 backlog_alert=true — 일일 배치로는 따라잡을 수 없으니 사람·번역팀이 일괄 처리할 신호.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

DEFAULT_MAX_CHARS = 40_000
DEFAULT_MAX_ROWS = 100
ALERT_DAYS = 5


def _len(item: dict[str, Any]) -> int:
    return len(item.get("finding_text") or "")


def pick(items: list[dict[str, Any]], max_chars: int = DEFAULT_MAX_CHARS,
         max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, Any]:
    """plan items → {"items": 고른 행, "remaining_rows", "remaining_chars", "backlog_alert"}. 순수 함수."""
    key = lambda x: (x.get("published_date") or "", x.get("finding_id") or "")  # noqa: E731
    newest = sorted(items, key=key, reverse=True)
    oldest = list(reversed(newest))
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    used = 0
    lanes = [newest, oldest]
    pos = [0, 0]
    blocked = [False, False]
    turn = 0
    while len(chosen) < max_rows and not all(blocked):
        lane = turn % 2
        turn += 1
        if blocked[lane]:
            continue
        seq = lanes[lane]
        while pos[lane] < len(seq) and seq[pos[lane]]["finding_id"] in seen:
            pos[lane] += 1
        if pos[lane] >= len(seq):
            blocked[lane] = True
            continue
        item = seq[pos[lane]]
        size = _len(item)
        if chosen and used + size > max_chars:
            blocked[lane] = True  # 건너뛰지 않는다 — 이 줄은 오늘 여기서 멈춘다
            continue
        chosen.append(item)
        seen.add(item["finding_id"])
        used += size
        pos[lane] += 1
    rest = [x for x in items if x["finding_id"] not in seen]
    remaining_chars = sum(_len(x) for x in rest)
    return {
        "items": chosen,
        "picked_chars": used,
        "remaining_rows": len(rest),
        "remaining_chars": remaining_chars,
        "backlog_alert": remaining_chars > max_chars * ALERT_DAYS,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", help="findings_translate.py --export 결과(plan.json) — 고치지 않는다")
    ap.add_argument("--output", required=True, help="고른 행만 담은 JSON(번역할 목록)")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    ap.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    args = ap.parse_args(argv)
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    result = pick(plan.get("items") or [], args.max_chars, args.max_rows)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    summary = {k: result[k] for k in ("picked_chars", "remaining_rows", "remaining_chars", "backlog_alert")}
    summary["picked_rows"] = len(result["items"])
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
