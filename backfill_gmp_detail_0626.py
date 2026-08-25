"""[MFDS GMP 지적 표 소급 백필 2026-08-25] 표 기능 탄생(07-02) **이전에 발행된** GMP실사
카드에 `deterministic_detail`(gmp_deficiencies 표)을 발행 브리프 JSON 에 직접 병합하는
결정론 1회성 CLI.

배경 — brief_web_2026_06_26.json 의 GMP실사 카드 3장은 지적 표 추출 기능이 생기기 전에
발행돼 상세 표가 구조적으로 비었는데, 2026-08-05 재수집으로 raw_signals 에 rows 가 이미
실재한다(화순전남대병원 5행·셀릭스 5행·동아제약 1행 — 1행은 원문 대조로 전량 확인).
rows 는 국문 원천(MFDS)이라 병기 번역 짝 문제가 없다: 순수 결정론 병합이다.

왜 재조립(assemble_publish_brief)이 아니라 직접 병합인가 — backfill_wl_violation_ko.py 와
같은 이유다: 과거 주차 스캐폴드는 CI 아티팩트라 만료됐고, 현행 파서·게이트로 통째
재생성하면 이번 수리와 무관한 드리프트가 함께 실린다. 이 CLI 는 발행본을 입력으로 받아
**상세 한 층만** 얹는다 — 변환 로직은 운영 경로와 같은 함수
(`card_scaffold._detail_gmp_deficiencies`: type/count/severity_summary/rows 동형)를
재사용하므로 별도 구현이 표류할 자리가 없다. 키 삽입 위치는 `checks` 바로 뒤 — 운영
스캐폴드의 키 순서(checks 다음 deterministic_detail)와 06-26 브리프 파일의 카드 키 정렬
관례(알파벳순: checks < deterministic_detail < evidence_basis)를 동시에 만족해 diff 가
추가 블록에만 국한된다.

재현성: 입력(발행 브리프·회수 rows 델타)과 로직이 전부 저장소에 커밋돼 있다 — 사람이 발행
아티팩트를 손으로 고치는 경로를 만들지 않는다. rows 원천은
web/data/deltas/gmp_detail_backfill_2026_06_26.json (raw_signals 에서 읽기 SELECT 로 회수,
raw_signal_id·collected_at 출처 명기).

안전 불변: 이미 deterministic_detail 을 가진 카드는 건드리지 않는다(덮어쓰기 없음).
델타에 있는데 브리프에 없는 카드 id 는 결손으로 보고하고 exit 1.

사용:
    python backfill_gmp_detail_0626.py --brief web/data/briefs/brief_web_2026_06_26.json \
        --rows web/data/deltas/gmp_detail_backfill_2026_06_26.json [--apply]

  기본은 dry-run(쓰지 않고 병합 예정만 보고). --apply 일 때만 브리프를 제자리 수정한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import card_scaffold
from assemble_publish_brief import _write_json


def merge_brief_gmp_details(
    brief: dict[str, Any], rows_by_card: dict[str, Any],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str], list[str]]:
    """브리프 카드들에 회수 rows 를 detail 로 변환해 병합(제자리).

    반환 = (병합 [(카드id, detail)], 스킵 사유 목록, 결손 카드id 목록).
    결손 = 델타에 rows 가 있는데 브리프에서 카드를 못 찾았거나 유효 행 0 — 이 목록이
    비어야 백필이 완결이다(호출부가 exit 1 기준으로 쓴다)."""
    merged: list[tuple[str, dict[str, Any]]] = []
    skipped: list[str] = []
    missing: list[str] = []
    seen_ids: set[str] = set()
    for card in brief.get("cards") or []:
        if not isinstance(card, dict):
            continue
        cid = str(card.get("id"))
        payload = rows_by_card.get(cid)
        if not isinstance(payload, dict):
            continue
        seen_ids.add(cid)
        if isinstance(card.get("deterministic_detail"), dict):
            skipped.append(f"{cid}: 이미 상세 보유(덮어쓰지 않음)")
            continue
        detail = card_scaffold._detail_gmp_deficiencies(
            card, {"gmp_deficiencies": payload.get("rows") or []})
        if detail is None:
            missing.append(f"{cid}: 유효 행 0(근거법령·지적내용 모두 빈 행뿐)")
            continue
        # `checks` 바로 뒤 삽입 — dict 재구성으로 위치를 고정(카드 객체는 제자리 갱신).
        rebuilt: dict[str, Any] = {}
        inserted = False
        for k, v in card.items():
            rebuilt[k] = v
            if k == "checks":
                rebuilt["deterministic_detail"] = detail
                inserted = True
        if not inserted:                      # checks 없는 카드는 말미 추가(방어)
            rebuilt["deterministic_detail"] = detail
        card.clear()
        card.update(rebuilt)
        merged.append((cid, detail))
    for cid in rows_by_card:
        if cid not in seen_ids:
            missing.append(f"{cid}: 브리프에 해당 카드 없음")
    return merged, skipped, missing


def main(argv: "list[str] | None" = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다.
    # backfill_wl_violation_ko.py 와 동형.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="발행 브리프 JSON 에 회수된 GMP 지적 rows 를 deterministic_detail 로 "
                    "소급 병합(결정론·1회성).")
    ap.add_argument("--brief", required=True, help="발행 브리프 JSON 경로(제자리 수정)")
    ap.add_argument("--rows", required=True,
                    help="회수 rows 델타 JSON 경로(cards.{id}.rows 구조)")
    ap.add_argument("--apply", action="store_true",
                    help="실제로 쓴다(기본은 dry-run: 병합 예정만 보고)")
    args = ap.parse_args(argv)

    with open(args.brief, "r", encoding="utf-8") as f:
        brief = json.load(f)
    with open(args.rows, "r", encoding="utf-8") as f:
        delta = json.load(f)
    rows_by_card = delta.get("cards") or {}

    merged, skipped, missing = merge_brief_gmp_details(brief, rows_by_card)
    if args.apply and merged:
        _write_json(args.brief, brief)     # 쓰기를 출력보다 먼저 — 출력 실패가 유실이 안 되게
    mode = "" if args.apply else "[dry-run] "
    print(f"{mode}{args.brief}: gmp_deficiencies 상세 병합 {len(merged)}카드")
    for cid, detail in merged:
        print(f"  병합: {cid} count={detail['count']} "
              f"severity_summary={json.dumps(detail['severity_summary'], ensure_ascii=False)}")
    for s in skipped:
        print(f"  스킵: {s}")
    for m in missing:
        print(f"  결손: {m}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
