#!/usr/bin/env python3
"""발행 브리프 ↔ 자료실 카탈로그 대조 — 두 경로가 어긋난 걸 사람 없이 잡는다.

2026-08-10: 8/10 브리프에는 식약처 지침 `data0010-15270` 이 실렸는데 자료실에는 없었다.
브리프는 **일일 intake**, 자료실은 **주간 갱신** — 서로 다른 실행이라 원리상 어긋날 수
있는데 둘을 대조하는 층이 없었다. 결국 발행본을 눈으로 본 사람이 탐지자였다.

판정 기준은 분류 결과(`category == "Guidance"`)가 아니라 **보드 ID** 다. 카드 id 는
`<brdId>-<seq>` 꼴이고, 자료실이 수록하는 보드는

    collect_mfds.MFDS_RSS_BOARDS  ∩  library_staging_build.MFDS_TYPES

로 **유도**된다 — 여기에 목록을 다시 적지 않는다(두 곳이 어긋나는 게 이 저장소의 단골
결함이다). 분류기 출력에 기대지 않으므로 카드가 다른 타입으로 분류돼도 잡힌다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import collect_mfds
import library_staging_build

SCHEMA_VERSION = "grm-library-brief-gap/v1"
DEFAULT_BRIEFS_DIR = Path("web/data/briefs")
DEFAULT_LIBRARY_DIR = Path("web/data/library")
BRIEF_GLOB = "brief_web_*.json"


def eligible_board_ids() -> set[str]:
    """자료실이 수록 대상으로 삼는 MFDS 게시판 id 집합(유도값)."""
    return {
        brd_id
        for brd_id, doc_type in collect_mfds.MFDS_RSS_BOARDS
        if doc_type in library_staging_build.MFDS_TYPES
    }


def latest_brief(briefs_dir: Path) -> Path | None:
    """가장 최근 발행 브리프 파일. 파일명이 날짜 정렬 가능한 형식이라 이름순 최대값."""
    candidates = sorted(briefs_dir.glob(BRIEF_GLOB))
    return candidates[-1] if candidates else None


def load_library_ids(library_dir: Path, source: str = "mfds") -> set[str]:
    path = library_dir / f"{source}.json"
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item.get("id") or "").strip()
        for item in payload.get("items", [])
        if str(item.get("id") or "").strip()
    }


def _board_of(card_id: str) -> str:
    return card_id.split("-", 1)[0] if "-" in card_id else ""


def find_gaps(
    cards: Iterable[dict[str, Any]], library_ids: set[str], boards: set[str],
) -> list[dict[str, str]]:
    """자료실 수록 대상 보드의 카드인데 카탈로그에 없는 것들."""
    gaps: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in cards:
        card_id = str(card.get("id") or "").strip()
        if not card_id or card_id in seen:
            continue
        if _board_of(card_id) not in boards:
            continue
        seen.add(card_id)
        if card_id in library_ids:
            continue
        gaps.append({
            "id": card_id,
            "board": _board_of(card_id),
            "title": str(card.get("headline_target") or card.get("title_issue") or "").strip(),
            "card_type": str(card.get("card_type") or "").strip(),
        })
    return gaps


def build_report(brief_path: Path, library_dir: Path) -> dict[str, Any]:
    payload = json.loads(brief_path.read_text(encoding="utf-8"))
    cards = payload.get("cards") or []
    brief_meta = payload.get("brief") or {}
    boards = eligible_board_ids()
    library_ids = load_library_ids(library_dir)
    gaps = find_gaps(cards, library_ids, boards)
    eligible = [c for c in cards if _board_of(str(c.get("id") or "")) in boards]
    return {
        "schema_version": SCHEMA_VERSION,
        "brief_file": brief_path.name,
        "publish_date": str(brief_meta.get("publish_date") or "").strip(),
        "eligible_boards": sorted(boards),
        "library_item_count": len(library_ids),
        "checked_card_count": len(eligible),
        "missing_count": len(gaps),
        "missing": gaps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--briefs-dir", type=Path, default=DEFAULT_BRIEFS_DIR)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--brief", type=Path, default=None,
                        help="특정 브리프 파일(기본: 가장 최근 발행본)")
    parser.add_argument("--report", type=Path, default=None, help="리포트 JSON 저장 경로")
    parser.add_argument("--fail-on-gap", action="store_true",
                        help="격차가 있으면 종료코드 1(기본은 리포트만 내고 0)")
    args = parser.parse_args(argv)

    brief_path = args.brief or latest_brief(args.briefs_dir)
    if brief_path is None or not brief_path.is_file():
        # 브리프가 없는 건 격차 0 이 아니라 **판정 불가** 다 — 조용히 통과시키지 않는다.
        print(json.dumps({"schema_version": SCHEMA_VERSION,
                          "error": f"브리프를 찾지 못했다: {args.brief or args.briefs_dir}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    report = build_report(brief_path, args.library_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if (args.fail_on_gap and report["missing_count"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
