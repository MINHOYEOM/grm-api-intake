#!/usr/bin/env python3
"""FDA 지적 조항 월간 갱신 가드 — 새 집계본을 커밋해도 되는지 판정한다(네트워크 0).

    py -3.12 -X utf8 web/clause_citations_guard.py --old <현재> --new <새로 뽑은 것> [--max-drop-pct 20]

종료 코드:
  0  정상 변경 — PR 을 만든다
  10 변경 없음 — PR 을 만들지 않는다(`as_of` 만 다른 경우 포함: as_of 는 적재 시각에서
     나오므로 내용이 같아도 매월 바뀐다. 그걸 변경으로 읽으면 매달 빈 PR 이 선다)
  1  비정상 — 실패로 알린다(PR 금지): 새 파일 검증 실패, 실사·조항·섹션 수가 직전 대비
     `max_drop_pct`% 이상 감소, 연도 창이 뒤로 가거나 min_inspections 가 달라짐
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clause_citations as cc  # noqa: E402

OK, UNCHANGED, ABNORMAL = 0, 10, 1
MAX_DROP_PCT = 20


def _metrics(d: dict[str, Any]) -> dict[str, int]:
    return {"inspections": int((d.get("inspections") or {}).get("total", 0)),
            "codes": len(d.get("codes") or []), "sections": len(d.get("sections") or [])}


def _without_as_of(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k != "as_of"}


def judge(old: "dict[str, Any] | None", new: Any, *,
          max_drop_pct: int = MAX_DROP_PCT) -> tuple[int, list[str]]:
    """(종료 코드, 사유 목록). 순수 함수."""
    if not isinstance(new, dict):
        return ABNORMAL, ["new: 객체가 아니다"]
    errs = cc.validate_snapshot(new)
    if errs:
        return ABNORMAL, [f"new: 검증 실패 {errs[:5]}"]
    if not new.get("codes"):
        return ABNORMAL, ["new: 조항 0개"]
    if old is None:
        return OK, ["old 없음 — 첫 스냅샷"]
    reasons: list[str] = []
    mo, mn = _metrics(old), _metrics(new)
    for k, before in mo.items():
        after = mn[k]
        # after <= before*(100-p)/100  <=>  (before-after)*100 >= before*p  (정수 산술)
        if before > 0 and (before - after) * 100 >= before * max_drop_pct:
            reasons.append(f"{k}: {before} -> {after} ({max_drop_pct}% 이상 감소)")
    if new.get("min_inspections") != old.get("min_inspections"):
        reasons.append(f"min_inspections: {old.get('min_inspections')} -> {new.get('min_inspections')}")
    oy, ny = old.get("years") or [], new.get("years") or []
    if oy and ny and max(ny) < max(oy):
        reasons.append(f"years: 최신 연도가 뒤로 감 {max(oy)} -> {max(ny)}")
    if reasons:
        return ABNORMAL, reasons
    if _without_as_of(old) == _without_as_of(new):
        return UNCHANGED, ["내용 변경 없음(as_of 제외)"]
    return OK, [f"실사 {mo['inspections']} -> {mn['inspections']} · 조항 {mo['codes']} -> {mn['codes']}"
                f" · 섹션 {mo['sections']} -> {mn['sections']}"]


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--max-drop-pct", type=int, default=MAX_DROP_PCT)
    a = p.parse_args(argv)
    try:
        new = json.loads(Path(a.new).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"guard: 새 파일 읽기 실패({type(e).__name__})", file=sys.stderr)
        return ABNORMAL
    op = Path(a.old)
    try:
        old = json.loads(op.read_text(encoding="utf-8")) if op.is_file() else None
    except (OSError, ValueError):
        print("guard: 기존 파일이 깨져 있다 - 사람이 본다", file=sys.stderr)
        return ABNORMAL
    code, reasons = judge(old, new, max_drop_pct=a.max_drop_pct)
    for r in reasons:
        print(f"guard{'' if code else ' ok'}: {r}", file=sys.stderr if code == ABNORMAL else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
