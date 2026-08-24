"""[WL 위반항목 국문 병기 소급 백필 2026-08-24] deep 델타의 `violations_ko` 를 **이미 발행된**
브리프 JSON(`web/data/briefs/brief_web_*.json`)에 직접 병합하는 결정론 CLI.

왜 재조립(assemble_publish_brief)이 아니라 직접 병합인가 — 재조립은 그 주 **스캐폴드**가
필요한데 과거 주차 스캐폴드는 CI 아티팩트라 만료됐고, 현행 파서·게이트로 과거 브리프를 통째
재생성하면 이번 수리(statement_ko 추가)와 무관한 드리프트가 함께 실린다. 이 CLI 는 발행본을
입력으로 받아 **병합 한 층만** 얹는다 — 병합 로직은 운영 경로와 같은 함수
(`inject_slots._merge_wl_violation_translations`, additive·번호 1:1)를 재사용하므로 별도 구현이
표류할 자리가 없다. 산출 diff = 대상 위반 행에 `statement_ko` 키 추가뿐.

재현성: 입력(브리프·deep 델타)과 로직이 전부 저장소에 커밋돼 있다 — 사람이 발행 아티팩트를
손으로 고치는 경로를 만들지 않는다(2026-07-20 미발행 브리프 유입 사고의 교훈, `_refresh_*` 와
동일 원칙).

운영 위치: 라우틴 정상 경로는 이 CLI 를 쓰지 않는다(Routine ⑤ → 브릿지 → 조립 병합).
이 CLI 는 (a) 과거 발행분 소급 병기, (b) 라우틴이 `violations_ko` 를 빠뜨려 게이트
(`_lint_wl_violation_ko`/`validate_wl_violations`)에 막혔을 때 deep 델타를 보충한 뒤 발행본을
맞추는 복구 — 두 경우 전용이다.

사용:
    python backfill_wl_violation_ko.py --brief web/data/briefs/brief_web_2026_08_24.json \
        --deep web/data/deltas/deep_2026_08_24.json [--check]

  --check: 쓰지 않고 병합 예정/잔여 결손만 보고(잔여 결손 있으면 exit 1 — 게이트와 같은 기준).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import inject_slots
from assemble_publish_brief import _write_json


def merge_brief_violations_ko(brief: dict[str, Any],
                              deep_deltas: dict[str, Any]) -> tuple[int, list[str]]:
    """브리프 카드들에 deep 델타의 `violations_ko` 를 병합. 반환 = (병합 카드 수, 잔여 결손).

    잔여 결손 = 병합 후에도 `statement_ko` 가 빈 위반 행(카드id/번호) — 발행 게이트
    (`validate_wl_violations`)와 같은 기준이라, 이 목록이 비면 게이트 통과가 보장된다."""
    report = inject_slots.InjectionReport()
    merged_cards = 0
    for card in brief.get("cards") or []:
        if not isinstance(card, dict):
            continue
        payload = deep_deltas.get(card.get("id"))
        viol_ko = payload.get("violations_ko") if isinstance(payload, dict) else None
        before = len(report.warnings)
        inject_slots._merge_wl_violation_translations(card, viol_ko, report, str(card.get("id")))
        if len(report.warnings) > before:
            merged_cards += 1
    remaining: list[str] = []
    for card in brief.get("cards") or []:
        dd = card.get("deterministic_detail") if isinstance(card, dict) else None
        if not (isinstance(dd, dict) and dd.get("type") == "wl_violations"):
            continue
        for v in dd.get("violations") or []:
            if not str(v.get("statement_ko") or "").strip():
                remaining.append(f"{card.get('id')} #{v.get('number', '?')}")
    return merged_cards, remaining


def main(argv: "list[str] | None" = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. brief_lint.py 등과 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="발행 브리프 JSON 에 deep 델타의 WL violations_ko 를 소급 병합(결정론).")
    ap.add_argument("--brief", required=True, help="발행 브리프 JSON 경로(제자리 수정)")
    ap.add_argument("--deep", required=True, help="deep 델타 JSON 경로(violations_ko 원천)")
    ap.add_argument("--check", action="store_true",
                    help="쓰지 않고 병합 예정·잔여 결손만 보고(잔여 있으면 exit 1)")
    args = ap.parse_args(argv)

    with open(args.brief, "r", encoding="utf-8") as f:
        brief = json.load(f)
    with open(args.deep, "r", encoding="utf-8") as f:
        deep_deltas = json.load(f)

    merged, remaining = merge_brief_violations_ko(brief, deep_deltas)
    if not args.check:
        _write_json(args.brief, brief)     # 쓰기를 출력보다 먼저 — 출력 실패가 유실이 안 되게
    print(f"{'[check] ' if args.check else ''}{args.brief}: "
          f"violations_ko 병합 {merged}카드 · 잔여 결손 {len(remaining)}건")
    for r in remaining:
        print(f"  잔여: {r}")
    return 1 if remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
