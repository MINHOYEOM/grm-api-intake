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

# [업계 브리핑 노트 2026-07-13, v2 명칭개편 2026-07-13] 해설·교육성 2차 소스(전문 매체) —
# 이벤트 카드가 아닌 '전문지 브리핑'으로 렌더(구 '업계 브리핑 노트'). 향후 RAPS·European
# Pharmaceutical Review 등 수집 추가 시 여기에 기관명만 추가.
# [전문지 브리핑 소스확장 2026-07-13] ISPE iSpeak 추가 — card_scaffold._REGULATOR_LABEL 의
# agency="ISPE" 와 일치.
RESOURCE_AGENCIES = ("ECA", "ISPE")


@dataclass
class AssembleReport:
    """조립 결과 보고. errors 가 비어야 발행 가능(strict)."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adopted: int = 0
    dropped: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    resources: int = 0

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


def _is_content_less_483(c: dict[str, Any]) -> bool:
    """관찰 원문이 없는 FDA 483 공개 카드(스캔·비공개 PDF → 상세/심층 없음)인가.
    시설·실사일 메타만 있는 '공개 알림' 카드 — 분석된 483(deterministic_detail·deep_analysis
    보유)과 구별해 목록카드 1장으로 접는 대상(2026-07-13)."""
    is483 = (c.get("type_tag") == "483") or str(c.get("id", "")).startswith("fda483-")
    return bool(is483 and not c.get("deterministic_detail") and not c.get("deep_analysis"))


def _fda483_facility_line(c: dict[str, Any]) -> str:
    """카드 facts 에서 '제조소/업체' + 실사일을 목록 항목 1줄로(결정론·사실 재작성 0)."""
    firm = ""
    insp = ""
    for f in c.get("facts") or []:
        lab = f.get("label", "")
        if ("제조소" in lab) or ("업체" in lab):
            firm = f.get("value", "")
        elif "실사" in lab:
            insp = f.get("value", "")
    firm = firm or c.get("headline_target", "") or str(c.get("id", ""))
    return f"{firm} · 실사 {insp}" if insp else firm


def merge_fda483_disclosures(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """관찰 원문 없는 FDA 483 공개 카드 다건을 목록 카드 1장으로 접는다(순수·결정론).

    2건 미만이면 무변화. 대표 = `id` 오름차순 첫 카드 — 슬롯을 디제스트 문안으로 결정론
    재작성하고 `merged_count`/`merged_items`(시설·실사일 목록)/`merged_noun='건'` 을 실어
    렌더러의 '전체 N건' 토글이 목록을 편다. 나머지 멤버는 발행본에서 제외(그 카드들의
    Notion Status 는 Routine 이 이미 Processed 처리 — 유실 아님). 분석된 483(상세/심층 보유)
    과 회수 병합은 대상 아님(이 함수는 content-less 483 만)."""
    idxs = [i for i, c in enumerate(cards) if _is_content_less_483(c)]
    if len(idxs) < 2:
        return cards
    idxs.sort(key=lambda i: str(cards[i].get("id", "")))
    rep_src = cards[idxs[0]]
    n = len(idxs)
    facilities = [_fda483_facility_line(cards[i]) for i in idxs]
    rep_fac = _fda483_facility_line(rep_src).split(" · 실사")[0]
    rep = dict(rep_src)
    rep["title_issue"] = "483 실사기록 다건 공개"
    rep["summary"] = (
        f"FDA OII FOIA 전자열람실에 483 실사기록 {n}건이 공개됐다. 개별 관찰 원문은 "
        "스캔·비공개로 상세가 제공되지 않아 시설·실사일 목록만 확인 가능하다.")
    rep["key_facts"] = [
        f"공개 건수: {n}건 (개별 관찰 상세 미공개)",
        f"대표 시설: {rep_fac} 외 {n - 1}곳",
        "출처: FDA OII FOIA 전자열람실",
    ]
    rep["implication"] = (
        "개별 관찰 내용이 공개되지 않은 실사기록 공개는 그 자체로 조치 신호는 아니다. "
        "국내 업체는 목록에 자사 공급망 시설이 있는지 확인하고, 있으면 원문(PDF)을 직접 "
        "열람할 필요가 있다.")
    rep["checks"] = [
        "목록에 자사 공급망·수급처 시설이 있는지 확인",
        "해당 시 FDA FOIA 원문(PDF) 직접 열람",
    ]
    rep["merged_count"] = n
    rep["merged_items"] = facilities
    rep["merged_noun"] = "건"
    rep.pop("quotes_translation", None)  # 디제스트엔 부적합(대표 1건의 번역 슬롯 제거)
    drop_ids = {str(cards[i].get("id")) for i in idxs[1:]}
    rep_id = str(rep_src.get("id"))
    out: list[dict[str, Any]] = []
    for c in cards:
        cid = str(c.get("id"))
        if cid in drop_ids:
            continue
        out.append(rep if cid == rep_id else c)
    return out


def extract_resource_notes(cards: list[dict[str, Any]]
                           ) -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
    """(event_cards, resources). resource 판정 = agency ∈ RESOURCE_AGENCIES ∧
    (type_tag=='GMP News' or card_type=='규제 소식'). 순수·순서보존.

    해설·교육성 2차 소스(현재 ECA GMP News 7장 유형)를 이벤트 카드 목록에서 분리해
    브리프 하단 '전문지 브리핑' 전용 섹션으로 렌더하기 위한 결정론 변환. 카드 dict 에서
    사실 재작성 0 으로 추출(§1 자료구조) — sources 는 그대로 통과하되, 렌더는 official_url
    (실기사)만 쓰고 info_url(RSS 피드)은 쓰지 않는다(렌더 쪽 책임).

    [전문지 브리핑 v2 2026-07-13 §3 정직성 게이트] `summary` 는 카드에 본문 흡수 흔적
    (`source_excerpt_present is True` — §4 ECA 기사 excerpt fetch 성공 신호)이 있을 때만
    포함한다. 수집 RSS 가 제목만 준 얇은 입력을 LLM 이 "원문에 없다"고 오서술하는 문제(§3
    배경)를 근본 차단 — 흡수 흔적이 없으면 summary 키 자체를 note 에서 제거한다(partial 의
    `{% if r.summary %}` 게이트가 그대로 요지 줄을 생략).
    """
    events: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    for c in cards:
        is_resource = (c.get("agency") in RESOURCE_AGENCIES
                       and (c.get("type_tag") == "GMP News"
                            or c.get("card_type") == "규제 소식"))
        if is_resource:
            note = {
                "id": c["id"],
                "title": c["title_issue"],
                "original_title": c.get("headline_target", ""),
                "agency": c["agency"],
                "type_tag": c.get("type_tag", ""),
                "sources": c.get("sources") or {},
            }
            if c.get("source_excerpt_present"):
                note["summary"] = c.get("summary", "")
            resources.append(note)
        else:
            events.append(c)
    return events, resources


def assemble_publish_brief(scaffold: dict[str, Any], delta: dict[str, Any],
                           *, strict: bool = True,
                           deep_deltas: dict[str, dict[str, Any]] | None = None
                           ) -> tuple[dict[str, Any], AssembleReport]:
    """(scaffold, delta) → (발행본 브리프, 보고). 입력 불변(순수).

    strict=True(기본): 델타 카드가 스캐폴드에 없거나·채택 카드에 빈 슬롯이 남으면 AssembleError.

    deep_deltas(선택, additive): {document_id: {"deep_analysis": {...}, "source_text": str}}.
    지정 시 채택 필터 이후 inject_slots.inject_deep_analysis 로 카드별 게이트 검증 후 주입한다.
    게이트 FAIL 카드는 deep_analysis 없이 6슬롯만으로 발행(카드 단위 graceful degrade —
    이 실패는 report.errors 에 넣지 않고 report.warnings 에만 남겨 발행을 막지 않는다).
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

    # [FDA 483 공개 디제스트 2026-07-13] 관찰 원문 없는 483 공개 카드 다건 → 목록카드 1장.
    adopted_cards = merge_fda483_disclosures(adopted_cards)

    # [업계 브리핑 노트 2026-07-13] 해설·교육성 2차 소스(ECA GMP News 등) → 이벤트 카드에서
    # 분리해 브리프 하단 전용 섹션으로. 아래 render_order 재부여·빈슬롯 게이트·adopted 집계는
    # 남은 이벤트 카드에만 적용된다(resource 는 별도 브리프 메타로 실린다).
    adopted_cards, resource_notes = extract_resource_notes(adopted_cards)

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
    report.resources = len(resource_notes)

    # 브리프 메타 재계산(assemble_web_brief 규약 미러) — 채택분 기준.
    out = copy.deepcopy(injected)
    out["cards"] = adopted_cards
    brief = out.setdefault("brief", {})

    # [심층분석 fan-out 배선] deep_deltas 지정 시 채택분 카드에 한해 게이트 검증 후 주입.
    # additive·선택 — 미지정 시 기존 동작과 완전 동일. 게이트 FAIL 은 발행을 막지 않는다
    # (해당 카드는 deep_analysis=null 그대로 6슬롯만 발행 — inject_deep_analysis 자체 규약).
    if deep_deltas:
        deep_report = inject_slots.inject_deep_analysis(out, deep_deltas)
        for w in deep_report.warnings:
            report.warnings.append(f"[deep] {w}")
        for e in deep_report.errors:
            report.warnings.append(f"[deep] {e}")

    # agencies = event 카드 + resource 노트의 agency 합집합(카드 순서 우선, 중복 제거) —
    # 리소스로 빠진 소스(예: ECA)가 헤더 기관 목록에서 사라지지 않게 한다.
    agencies = _distinct_in_order(
        [c.get("agency", "") for c in adopted_cards]
        + [r.get("agency", "") for r in resource_notes])
    categories = _distinct_in_order([c.get("category", "") for c in adopted_cards])
    evidence = {"A": 0, "B": 0, "C": 0}
    for c in adopted_cards:
        lvl = c.get("evidence_level")
        if lvl in evidence:
            evidence[lvl] += 1
    brief["agencies"] = agencies
    brief["categories"] = categories
    if resource_notes:
        brief["resources"] = resource_notes

    coverage = brief.setdefault("coverage", {})
    # intake_total = 실수집 총건(진실값) 보존. rendered = 채택(이벤트) 수. evidence = 채택 재집계.
    coverage["rendered"] = len(adopted_cards)
    coverage["evidence"] = evidence
    coverage.setdefault("intake_total", scaffold.get("brief", {})
                        .get("coverage", {}).get("intake_total", len(adopted_cards)))
    if resource_notes:
        coverage["resources"] = len(resource_notes)

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
    ap.add_argument("--deep", default=None,
                    help="[선택] 심층분석 델타 JSON 경로 "
                         "({document_id:{deep_analysis,source_text}}). 미지정 시 기존 동작과 완전 동일.")
    args = ap.parse_args(argv)

    scaffold = _load_json(args.scaffold)
    delta = _load_json(args.delta)
    deep_deltas = _load_json(args.deep) if args.deep else None
    try:
        out, report = assemble_publish_brief(scaffold, delta, strict=True, deep_deltas=deep_deltas)
    except (inject_slots.SlotInjectionError, AssembleError) as e:
        print(f"조립 거부:\n{e}", file=sys.stderr)
        return 2

    for w in report.warnings:
        print(f"WARN {w}", file=sys.stderr)
    _write_json(args.out, out)
    cov = out["brief"]["coverage"]
    deep_merged = sum(1 for c in out["cards"] if c.get("deep_analysis"))
    print(f"조립 완료: 채택 {report.adopted}카드 (스킵 {report.dropped}) → {args.out}")
    print(f"  브리핑 노트 {report.resources}건")
    if args.deep:
        print(f"  deep_analysis: 병합 {deep_merged}카드 (게이트 보류는 위 WARN 참고)")
    print(f"  coverage: 수집 {cov.get('intake_total')} · 카드 {cov['rendered']} · "
          f"Evidence A{cov['evidence']['A']}/B{cov['evidence']['B']}/C{cov['evidence']['C']}")
    print(f"  agencies: {out['brief']['agencies']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
