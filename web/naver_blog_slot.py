#!/usr/bin/env python3
"""네이버 블로그 '왜 중요한가' 슬롯 도구 (2026-11-26) — 편집팀 Routine 이 부른다.

  --candidate --brief web/data/briefs/brief_web_YYYY_MM_DD.json
      이번 호 블로그가 해설할 483(빌드와 같은 고르기)과 근거 자료를 JSON 으로 낸다.
      해설할 483 이 없으면 {"card_id": null} — 그 주는 슬롯을 쓰지 않는다.
  --check web/data/naver_blog/YYYY-MM-DD.json --brief web/data/briefs/brief_web_YYYY_MM_DD.json
      슬롯 게이트(render.naver_blog_slot_problems — 빌드·시험과 같은 함수). 통과 0, 실패 1.

슬롯 형식: {"schema": "grm-naver-blog-why/v1", "pub": "YYYY-MM-DD", "card_id": "...", "why": [문장 2~3]}.
네트워크·DB 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brief", required=True, type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate", action="store_true")
    g.add_argument("--check", type=Path)
    a = ap.parse_args(argv)
    brief = json.loads(a.brief.read_text(encoding="utf-8"))
    card = render.naver_blog_pick_from_brief(brief)
    if a.candidate:
        out = {"pub": (brief.get("brief") or {}).get("publish_date"), "card_id": None}
        if card is not None:
            out.update({
                "card_id": card.get("id"),
                "firm": card.get("headline_target"),
                "issue": card.get("title_issue"),
                "site_text_do_not_repeat": render.naver_blog_site_text(card),
                "material_for_numbers": render.naver_blog_slot_material(card),
                "overlap_max": render.NAVER_BLOG_OVERLAP_MAX,
            })
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    slot = json.loads(a.check.read_text(encoding="utf-8"))
    probs = render.naver_blog_slot_problems(slot, card)
    if probs:
        for pr in probs:
            print(f"FAIL {pr}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
