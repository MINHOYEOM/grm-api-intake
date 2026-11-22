#!/usr/bin/env python3
"""발행 렌더 게이트를 조립 직후에 미리 돌린다 — 교열이 통과시킨 호가 사이트 빌드에서 막히지 않게.

[2026-11-22] 리허설 1122a 를 발행 브릿지·발행 조립까지 모의로 이어 돌리자, 조립·영문 게이트는 통과했는데
`web/render.py` 의 발행 게이트(`_validate_briefs_or_raise`)가 483 카드 49장의 관찰사항 국문 병기 부재
(`MISSING_DEFICIENCY_KO`·`MISSING_DETAIL_KO`)로 빌드를 막았다. 교열 코드 검사는 이 게이트를 돌리지 않아
통과 → Notion `CONSUMED` → 백업 미발동 → 발행 워크플로가 렌더에서 멈추는 길이 열려 있었다.

판정은 render.py 의 검사 함수 네 개를 그대로 쓴다(두 벌 금지): 483 관찰 국문·WL 위반항목 국문·
WHOPIR 국문·영문 사실 칸.
exit 0 = 통과, 1 = 위반(메시지는 stdout — 첫 줄 요약, 이어서 카드 id 목록·위반 예시), 2 = 입력 오류.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "web"))
import render  # noqa: E402

CHECKS = (
    ("483 관찰 국문", "validate_483_observations"),
    ("WL 위반항목 국문", "validate_wl_violations"),
    ("WHOPIR 국문", "validate_whopir_ko"),
    ("영문 사실 칸", "validate_brief_en_facts"),
)
_CARD_RE = re.compile(r"card ([^ /]+)")


def find_violations(brief: dict) -> "list[tuple[str, str]]":
    """[(검사 이름, 위반 줄)] — 브리프 한 호({"brief":…, "cards":[…]})."""
    out: list[tuple[str, str]] = []
    for label, fn in CHECKS:
        for line in getattr(render, fn)([brief]):
            out.append((label, str(line)))
    return out


def summarize(violations: "list[tuple[str, str]]") -> str:
    if not violations:
        return "렌더 게이트 통과"
    by_label: dict[str, int] = {}
    cards: list[str] = []
    for label, line in violations:
        by_label[label] = by_label.get(label, 0) + 1
        m = _CARD_RE.search(line)
        if m and m.group(1) not in cards:
            cards.append(m.group(1))
    head = " · ".join(f"{k} {v}건" for k, v in by_label.items())
    lines = [f"렌더 게이트 위반 {len(violations)}건({head}) · 카드 {len(cards)}장 — 이대로 발행하면 사이트 빌드가 멈춘다",
             "카드: " + ", ".join(cards)]
    lines += [f"  · {label}: {line}" for label, line in violations[:10]]
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("brief", help="조립 산출물(brief_web_*.json 또는 교열 brief_check.json)")
    args = ap.parse_args(argv)
    try:
        brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"입력 오류: {e}")
        return 2
    if not isinstance(brief, dict) or not isinstance(brief.get("cards"), list):
        print("입력 오류: 브리프 모양이 아니다(cards 목록 없음)")
        return 2
    violations = find_violations(brief)
    print(summarize(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
