#!/usr/bin/env python3
"""발행 브리프의 영문 슬롯 완결성 게이트 — 발행 PR 을 만들기 전에 빠진 것을 한 줄로 드러낸다.

[2026-11-02] 11-02 발행 PR(#1281)이 카드 126장 전부 `en` 없이 만들어져, PR 이 열린 뒤 CI test 3건
(영문 홈·페이지군 구조 대칭·링크드인 월간)이 실패하고서야 드러났다. 웹 렌더러는 영문 갈래가 하나라도 빠진 카드가
있으면 **그 호 전체를 영어로 내지 않는다**(`render.brief_has_english`) — 조용한 생략이라 원인이 안 보인다.
이 게이트는 같은 판정을 조립 직후에 하고, 빠진 카드·슬롯과 tldr_en 부재를 목록으로 낸다.

판정 기준은 render.py 의 `card_has_english`·`CARD_NARRATIVE_FIELDS` 를 그대로 쓴다(두 벌 금지).
exit 0 = 완결, 1 = 빠진 것 있음(메시지는 stdout), 2 = 입력 오류. `--warn-only` 면 항상 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "web"))
import render  # noqa: E402


def _filled(value) -> bool:
    if isinstance(value, list):
        return bool([v for v in value if str(v).strip()])
    return bool(str(value or "").strip())


def find_problems(doc: dict) -> list[str]:
    """빠진 것 목록. 비어 있으면 완결."""
    cards = doc.get("cards") or []
    if not cards:
        return ["카드가 0장"]
    out: list[str] = []
    missing_all: list[str] = []
    partial: list[str] = []
    for c in cards:
        if render.card_has_english(c):
            continue
        en = c.get("en")
        cid = str(c.get("id") or "?")
        if not isinstance(en, dict) or not any(_filled(en.get(f)) for f in render.CARD_NARRATIVE_FIELDS):
            missing_all.append(cid)
        else:
            gaps = [f for f in render.CARD_NARRATIVE_FIELDS if not _filled(en.get(f))]
            partial.append(f"{cid}(빠짐: {','.join(gaps)})")
    if missing_all:
        out.append(f"en 없음 {len(missing_all)}/{len(cards)}장 (예: {', '.join(missing_all[:5])})")
    if partial:
        out.append(f"en 부분 제공 {len(partial)}장 (예: {', '.join(partial[:5])})")
    tldr = ((doc.get("brief") or {}).get("en") or {}).get("tldr")
    if not (isinstance(tldr, list) and any(str(t).strip() for t in tldr)):
        out.append("brief.en.tldr 없음 (delta.tldr_en)")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="발행 브리프 영문 슬롯 완결성 게이트")
    ap.add_argument("brief", help="web/data/briefs/brief_web_*.json")
    ap.add_argument("--warn-only", action="store_true", help="문제가 있어도 exit 0 (경고만)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 에서 em-dash 로 죽지 않게
    try:
        doc = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"입력 오류: {args.brief} ({type(exc).__name__})")
        return 2
    problems = find_problems(doc)
    if not problems:
        print("영문 슬롯 완결")
        return 0
    print("영문 슬롯 미완결 — 이 호는 영어로 나가지 않고 영문 CI 3건이 실패한다: " + " · ".join(problems))
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
