#!/usr/bin/env python3
"""[483 심층분석 절단 소급 병합 2026-08-25] D5b(원문 병기 절단) FAIL 로 **병합 보류된** 카드를
발행 브리프에 소급 반영하는 결정론 1회성 CLI.

배경 — 2026-07-12 발행분 FDA 483 카드 3장(`fda483-193451`·`193454`·`193455`)은 deep 델타에
4섹션 심층분석이 멀쩡히 있는데도 발행본에 안 실렸다. 원인은 병합 버그가 아니라 게이트 판정이
맞았던 것이다: `key_violations[].original` 이 결함 문장까지만 발췌되고 그 뒤 "Specifically…"
상세가 잘려, 국문 해석(observation)만 구체적이고 원문은 짧은 병기쌍 파손 상태였다(D5b FAIL
→ entry 통째 보류 → 카드가 심층분석을 통째로 잃음).

핵심은 **잘려나간 부분이 이미 델타의 `source_text` 안에 verbatim 으로 있다**는 점이다 —
LLM 을 다시 부를 필요가 없는, 붙이기만 하면 되는 결손이었다. 수리는
`verify_deep_analysis.repair_fda483_original_truncation`(원문 slice·생성 0)이 하고, 이 CLI 는
그 수리가 배선된 **운영 병합 경로**(`inject_slots.inject_deep_analysis`)를 그대로 호출한다.
별도 병합 구현을 두지 않으므로 라이브와 백필이 갈라질 자리가 없다(backfill_wl_violation_ko.py
선례와 동형).

왜 재조립(assemble_publish_brief)이 아닌가 — 과거 주차 스캐폴드는 CI 아티팩트라 만료됐고,
현행 파서·게이트로 통째 재생성하면 이번 수리와 무관한 드리프트가 함께 실린다. 이 CLI 는
발행본을 입력으로 받아 **심층분석 한 층만** 얹는다.

안전 불변:
  · 이미 `deep_analysis` 를 가진 카드는 병합 경로가 그대로 덮으므로 `--only` 로 대상을 좁힌다.
  · 게이트를 통과하지 못한 카드는 **실리지 않는다**(운영 경로와 동일 규약) — 수리해도 경계를
    확정 못 한 항목이 남으면 FAIL 이 유지되고, 그 사실이 리포트에 남는다.
  · dry-run 기본. `--apply` 없이는 파일을 쓰지 않는다.

사용:
    python backfill_deep_original_truncation.py \
        --brief web/data/briefs/brief_web_2026_07_12.json \
        --deep  web/data/deltas/deep_2026_07_12.json \
        --only fda483-193451,fda483-193454,fda483-193455 [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import inject_slots
from assemble_publish_brief import _write_json


def select_deltas(deep: dict[str, Any], only: "set[str] | None",
                  brief_ids: "set[str]") -> dict[str, Any]:
    """병합 대상 델타만 남긴다 — `--only` 지정 시 그 id, 아니면 심층분석을 가진 전건.

    브리프에 없는 id 는 여기서 떨어뜨린다(운영 경로도 경고만 남기고 무시하지만, 백필은
    "무엇을 왜 안 실었는지"가 리포트에 남아야 한다)."""
    out: dict[str, Any] = {}
    for doc_id, payload in (deep or {}).items():
        if only is not None and doc_id not in only:
            continue
        if not isinstance(payload, dict) or "deep_analysis" not in payload:
            continue
        if doc_id not in brief_ids:
            continue
        out[doc_id] = payload
    return out


def main(argv: "list[str] | None" = None) -> int:
    # [cp949 가드] 파이프로 연결된 stdout 은 Windows 기본 코드페이지(cp949)로 인코딩되고,
    # 이 리포트가 쓰는 중점(·)·화살표는 통과해도 향후 문구에 em-dash 하나만 섞이면
    # UnicodeEncodeError 로 죽는다 — 그러면 `--apply` 의 기록이 출력 실패에 끌려 유실된다.
    # 전수 가드(`tests/test_cli_stdout_encoding.py`)가 이 관용구의 부재를 잡는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="D5b 절단으로 병합 보류된 483 심층분석을 발행 브리프에 소급 병합")
    ap.add_argument("--brief", required=True, help="발행 브리프 JSON 경로")
    ap.add_argument("--deep", required=True, help="deep 델타 JSON 경로")
    ap.add_argument("--only", default="",
                    help="대상 document_id 쉼표 목록(미지정 시 심층분석 보유 전건)")
    ap.add_argument("--apply", action="store_true", help="실제 기록(미지정 시 dry-run)")
    args = ap.parse_args(argv)

    with open(args.brief, encoding="utf-8") as fh:
        brief = json.load(fh)
    with open(args.deep, encoding="utf-8") as fh:
        deep = json.load(fh)

    cards = [c for c in (brief.get("cards") or []) if isinstance(c, dict)]
    brief_ids = {c.get("id") for c in cards}
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    if only:
        missing = sorted(only - brief_ids)
        if missing:
            print(f"[FAIL] 브리프에 없는 대상 id: {missing}")
            return 1

    targets = select_deltas(deep, only, brief_ids)
    if not targets:
        print("[FAIL] 병합 대상 0건 — 대상 지정이나 델타 내용을 확인하라")
        return 1

    before = {c.get("id"): bool(c.get("deep_analysis")) for c in cards}
    report = inject_slots.inject_deep_analysis(brief, targets)
    after = {c.get("id"): bool(c.get("deep_analysis"))
             for c in (brief.get("cards") or []) if isinstance(c, dict)}

    merged = sorted(k for k in targets if after.get(k) and not before.get(k))
    blocked = sorted(k for k in targets if not after.get(k))
    for line in report.warnings:
        print(f"  [WARN] {line}")
    for line in report.errors:
        print(f"  [FAIL] {line}")
    print(f"\n대상 {len(targets)}건 · 신규 병합 {len(merged)}건 · 미병합 {len(blocked)}건")
    for k in merged:
        print(f"   병합 {k}")
    for k in blocked:
        print(f"   보류 {k} (게이트 미통과 — 실리지 않음)")

    if blocked:
        print("\n[FAIL] 게이트를 통과 못 한 카드가 있다 — 원문 경계를 확정 못 한 항목이 "
              "남았다는 뜻이므로 사람이 확인해야 한다.")
        return 1
    if not args.apply:
        print("\n(dry-run — 기록하지 않았다. 반영하려면 --apply)")
        return 0
    _write_json(args.brief, brief)
    print(f"\n기록 완료: {args.brief}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
