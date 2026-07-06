"""라우틴 델타 + 빈슬롯 스캐폴드 → 발행본(채택분) 조립. 순수·결정론.

한 주치 웹 발행 파이프의 **코드 계약**(2026-07-06 수동 조립 사고의 코드화):

    scaffold(grm-web-card/v1, §1-B 이미터가 낸 전 intake 카드·빈 슬롯)
      + delta({"cards": {card.id: {슬롯}}, "tldr": [...]})
        1) inject_slots.inject_llm_slots  — LLM 슬롯 주입 + 코드 가드(마크업·길이·정합)
        2) 채택 필터              — 델타에 없는 카드(=Routine 이 Tier1/Skipped 처리) 제거
        3) render_order 재배열     — 채택분을 0..N-1 로 연속 재부여(라이브 06-26 규약)
        4) 브리프 메타 재계산       — agencies·categories·coverage.evidence 를 채택분으로
                                    재집계(assemble_web_brief 규약 미러). coverage.intake_total
                                    (실수집 총건)은 진실값으로 **보존**, rendered=채택 수.
        → 발행본 brief_web_{publish_date}.json

**왜 필요한가**: §1-B 컬렉터는 tier 를 모르므로 스캐폴드에 전 intake 카드(예: 89)를 방출한다.
Routine 이 그중 일부(예: 61)만 채택하고 나머지(Tier1)를 Skipped 한다. 따라서 발행 직전
"채택분만 남기고 메타를 재계산"하는 결정론 단계가 반드시 필요하다 — 이 단계가 지금까지
어떤 코드에도 없어 매주 수작업이었다(그리고 2026-07-06 에 89 vs 61 로 표면화됐다).

**불변식**:
- 코드 verbatim 필드(facts·quotes[].original·sources·headline_target·배지·id·group 등)는 절대
  변경하지 않는다(inject_slots 계승 — 이 모듈은 슬롯 주입을 inject_slots 에 위임하고, 그 외에는
  카드 부분집합 선택 + render_order 재부여 + 브리프 메타 재계산만 한다).
- 순수·결정론: 같은 (scaffold, delta) → 바이트 동일 출력(네트워크·현재시각·난수 0).
- data 관례(indent=1·ensure_ascii=False·LF·후행개행) = web/render._write_json / emit_web_brief_file 동형.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import inject_slots

# 발행 카드가 반드시 채워야 하는 LLM 슬롯(빈값이면 발행 불가).
_REQUIRED_STR_SLOTS = ("title_issue", "summary", "implication")
_REQUIRED_LIST_SLOTS = ("key_facts", "checks")


@dataclass
class AssembleReport:
    """조립 결과 보고. errors 가 비어야 발행 가능(strict)."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adopted: int = 0
    dropped: int = 0
    dropped_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class AssembleError(ValueError):
    """조립 검증 실패(코드 가드 위반)."""


def _distinct_in_order(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return seen


def assemble_publish_brief(scaffold: dict[str, Any], delta: dict[str, Any],
                           *, strict: bool = True) -> tuple[dict[str, Any], AssembleReport]:
    """(scaffold, delta) → (발행본 브리프, 보고). 입력 불변(순수).

    strict=True(기본): 델타 카드가 스캐폴드에 없거나·채택 카드에 빈 슬롯이 남으면 AssembleError.
    """
    report = AssembleReport()
    delta_cards = delta.get("cards") or {}
    if not isinstance(delta_cards, dict):
        raise AssembleError("delta.cards 는 {card.id: {슬롯}} 객체여야 함")
    adopted_ids = set(delta_cards)

    scaffold_cards = scaffold.get("cards") or []
    scaffold_ids = {c.get("id") for c in scaffold_cards}

    # 게이트 1: 델타의 모든 카드 id 가 스캐폴드에 존재해야 한다(다른 run 스캐폴드 감지).
    ghost = sorted(cid for cid in adopted_ids if cid not in scaffold_ids)
    if ghost:
        report.errors.append(
            f"델타 카드 id {len(ghost)}건이 스캐폴드에 없음(스캐폴드가 다른 intake run?): "
            + ", ".join(ghost[:8]) + (" …" if len(ghost) > 8 else ""))

    # 슬롯 주입(코드 가드는 inject_slots 가 수행). strict 는 상위로 위임.
    injected = inject_slots.inject_llm_slots(scaffold, delta, strict=strict)

    # 채택 필터 + render_order 정렬 보존.
    all_cards = injected.get("cards") or []
    ordered = sorted(all_cards, key=lambda c: c.get("render_order", 0))
    adopted_cards: list[dict[str, Any]] = []
    for c in ordered:
        if c.get("id") in adopted_ids:
            adopted_cards.append(c)
        else:
            report.dropped += 1
            report.dropped_ids.append(c.get("id"))

    # render_order 0..N-1 연속 재부여(원 상대순서 보존).
    for i, c in enumerate(adopted_cards):
        c["render_order"] = i

    # 게이트 2: 채택 카드에 빈 필수 슬롯 0.
    for c in adopted_cards:
        for k in _REQUIRED_STR_SLOTS:
            if not c.get(k):
                report.errors.append(f"채택 카드 {c.get('id')!r}: 빈 슬롯 {k}")
        for k in _REQUIRED_LIST_SLOTS:
            if not c.get(k):
                report.errors.append(f"채택 카드 {c.get('id')!r}: 빈 슬롯 {k}")

    report.adopted = len(adopted_cards)

    # 브리프 메타 재계산(assemble_web_brief 규약 미러) — 채택분 기준.
    out = copy.deepcopy(injected)
    out["cards"] = adopted_cards
    brief = out.setdefault("brief", {})

    agencies = _distinct_in_order([c.get("agency", "") for c in adopted_cards])
    categories = _distinct_in_order([c.get("category", "") for c in adopted_cards])
    evidence = {"A": 0, "B": 0, "C": 0}
    for c in adopted_cards:
        lvl = c.get("evidence_level")
        if lvl in evidence:
            evidence[lvl] += 1
    brief["agencies"] = agencies
    brief["categories"] = categories

    coverage = brief.setdefault("coverage", {})
    # intake_total = 실수집 총건(진실값) 보존. rendered = 채택 수. evidence = 채택 재집계.
    coverage["rendered"] = len(adopted_cards)
    coverage["evidence"] = evidence
    coverage.setdefault("intake_total", scaffold.get("brief", {})
                        .get("coverage", {}).get("intake_total", len(adopted_cards)))

    if strict and report.errors:
        raise AssembleError("발행본 조립 검증 실패:\n  - " + "\n  - ".join(report.errors))
    return out, report


def _write_json(path: str, payload: dict[str, Any]) -> None:
    """emit_web_brief_file / web.render._write_json 과 동형(indent=1·LF·후행개행·UTF-8)."""
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="빈슬롯 스캐폴드 + 라우틴 델타 → 발행본(채택분) 조립(순수·결정론).")
    ap.add_argument("--scaffold", required=True,
                    help="§1-B 이미터 스캐폴드 JSON(전 intake 카드·빈 슬롯, grm-web-card/v1)")
    ap.add_argument("--delta", required=True,
                    help="라우틴 델타 JSON({cards:{id:{슬롯}}, tldr:[]})")
    ap.add_argument("--out", required=True,
                    help="발행본 출력 경로(web/data/briefs/brief_web_{date}.json)")
    args = ap.parse_args(argv)

    scaffold = _load_json(args.scaffold)
    delta = _load_json(args.delta)
    try:
        out, report = assemble_publish_brief(scaffold, delta, strict=True)
    except (inject_slots.SlotInjectionError, AssembleError) as e:
        print(f"조립 거부:\n{e}", file=sys.stderr)
        return 2

    for w in report.warnings:
        print(f"WARN {w}", file=sys.stderr)
    _write_json(args.out, out)
    cov = out["brief"]["coverage"]
    print(f"조립 완료: 채택 {report.adopted}카드 (스킵 {report.dropped}) → {args.out}")
    print(f"  coverage: 수집 {cov.get('intake_total')} · 카드 {cov['rendered']} · "
          f"Evidence A{cov['evidence']['A']}/B{cov['evidence']['B']}/C{cov['evidence']['C']}")
    print(f"  agencies: {out['brief']['agencies']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
