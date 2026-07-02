"""GRM [WL 심층분석 fan-out] 오케스트레이션 헬퍼 — 순수·결정론(네트워크·LLM 호출 없음).

fan-out 실행모델(2026-07-01 확정 · `GRM_card_spec_v16.md` §15 · 지시문 §3.1): deep_analysis 는
카드당 **독립 Claude Code 서브에이전트 1개**가 그 카드의 `body_full` 만 보고 4섹션 JSON 을
생성한다 — 신규 GitHub Actions + Anthropic API 키(호출당 과금) 조합은 이 프로젝트에서 **배제**됐다
(기존 6슬롯 Routine 과 동일하게 MINO 의 Claude Code 세션 구독 사용량 안에서 처리 = 무과금).

이 모듈은 그 fan-out 의 **결정론 양끝**만 담당한다(가운데 "서브에이전트 호출"은 LLM 단계라
Claude Code 세션에서 이뤄진다 — `docs/prompts/GRM_DeepWL_fanout_실행프롬프트.md` 절차 참조):

  build_jobs()      — handoff(`to_dict` 카드 목록)에서 `deep_analysis_ready` 카드만 골라
                      서브에이전트 작업목록 `[{document_id, body_full}]` 산출(순회 대상).
  assemble_deltas() — 서브에이전트가 돌려준 카드별 4섹션 JSON 을 `verify_deep_analysis` 게이트에
                      통과시켜 **PASS 만** `inject_slots.inject_deep_analysis` 델타 포맷으로 모으고,
                      FAIL/누락은 사유(`GateResult.report`)와 함께 보고(카드 단위 graceful degrade —
                      FAIL 카드는 6슬롯만으로 조용히 발행, 전체 브리프는 안 막힌다).

산출 델타는 `inject_slots.py --deep-analysis-deltas <deltas.json>` 로 브리프에 병합한다.

CLI:
  python -m deep_analysis_fanout build-jobs --handoff handoff.json --out jobs.json
  python -m deep_analysis_fanout assemble  --jobs jobs.json --responses responses.json --out deltas.json
"""
from __future__ import annotations

import html
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import verify_deep_analysis as vda


# ─────────────────────────────────────────────────────────────────────────────
# build_jobs — handoff → 서브에이전트 작업목록
# ─────────────────────────────────────────────────────────────────────────────
def _cards(handoff: Any) -> list[dict[str, Any]]:
    """handoff 입력을 카드 dict 리스트로 정규화 — 리스트 그대로 / {'cards':[...]} /
    {'handoff': {'cards':[...]}} 형태를 모두 받아 결정론으로 카드 목록만 뽑는다."""
    if isinstance(handoff, list):
        return [c for c in handoff if isinstance(c, dict)]
    if isinstance(handoff, dict):
        for key in ("cards", "handoff"):
            v = handoff.get(key)
            if isinstance(v, list):
                return [c for c in v if isinstance(c, dict)]
            if isinstance(v, dict) and isinstance(v.get("cards"), list):
                return [c for c in v["cards"] if isinstance(c, dict)]
    return []


def _document_id(card: dict[str, Any]) -> str:
    """inject 델타 키(=web-card id) 도출. `to_web_card().id = row.document_id` 이고 handoff
    `to_dict.card_id = 'source::document_id'` 이므로 '::' 뒤가 곧 document_id 다(source 엔
    '::' 가 없어 first-split 이 안전). 명시 document_id 필드가 있으면 그것을 우선한다."""
    doc = card.get("document_id")
    if isinstance(doc, str) and doc:
        return doc
    cid = card.get("card_id", "")
    return cid.split("::", 1)[1] if "::" in cid else cid


@dataclass(frozen=True)
class Job:
    """서브에이전트 1건 = 카드 1건. body_full 만 컨텍스트로 준다(다른 카드와 격리).

    `card_type`(=handoff `kind`: warning-letter | admin-action | fda-483 | "")은 오케스트레이터가
    유형별 생성 프롬프트(DeepWL/DeepAdmin/DeepFda483)를 고르고, assemble 이 게이트에 카드 유형을
    넘겨 필수 섹션·D2 성격을 확정하는 데 쓴다. 빈 문자열이면 게이트가 산출물 키로 자동판별(후방호환).
    """
    document_id: str
    body_full: str
    card_type: str = ""

    def to_dict(self) -> dict[str, str]:
        d = {"document_id": self.document_id, "body_full": self.body_full}
        if self.card_type:
            d["card_type"] = self.card_type
        return d


def build_jobs(handoff: Any) -> list[Job]:
    """`deep_analysis_ready=True` + `body_full` 확보 카드만 서브에이전트 작업으로 변환.

    결정론(입력 순서 보존). body_full 이 비었거나 document_id 를 못 얻으면 조용히 건너뛴다
    (그 카드는 6슬롯만으로 발행). document_id 중복은 첫 건만. 카드 `kind` 를 `card_type` 으로
    실어 오케스트레이터가 유형별 프롬프트를 고르게 한다(WL·admin·483 유형무관 추출).
    """
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in _cards(handoff):
        if not card.get("deep_analysis_ready"):
            continue
        body = ((card.get("deep_analysis_input") or {}).get("body_full") or "").strip()
        doc = _document_id(card)
        if not doc or not body or doc in seen:
            continue
        seen.add(doc)
        jobs.append(Job(document_id=doc, body_full=body,
                        card_type=str(card.get("kind") or "")))
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# assemble_deltas — 서브에이전트 응답 → 게이트 → inject 델타
# ─────────────────────────────────────────────────────────────────────────────
def _as_jobs(jobs: Any) -> list[Job]:
    """Job 객체 목록 또는 jobs.json 에서 읽은 dict 목록을 Job 목록으로 정규화."""
    out: list[Job] = []
    for j in jobs or []:
        if isinstance(j, Job):
            out.append(j)
        elif isinstance(j, dict) and j.get("document_id"):
            out.append(Job(document_id=str(j["document_id"]),
                           body_full=str(j.get("body_full", "")),
                           card_type=str(j.get("card_type") or "")))
    return out


def _unescape_entities(value: Any) -> Any:
    """서브에이전트가 가끔 `&`·`<`·`>` 를 HTML 엔티티(`&amp;` 등)로 이스케이프해 산출한다
    (실검증서 관측: Intas WL → `FD&amp;C`). 그대로 두면 렌더러(Jinja) 자동 이스케이프와 겹쳐
    이중 이스케이프(`FD&amp;amp;C`)로 깨진다. 병합 전 원문자로 되돌린다 — 결정론·재귀, HTML
    이스케이프는 오직 렌더러가 담당(단일 책임). 규제 원문에 실제 `&amp;` 리터럴이 오는 일은
    없어 무손실이다(프롬프트 규칙과 이중 방어 — LLM 산출물은 프롬프트만으로 신뢰하지 않는다)."""
    if isinstance(value, str):
        return html.unescape(value)
    if isinstance(value, list):
        return [_unescape_entities(v) for v in value]
    if isinstance(value, dict):
        return {k: _unescape_entities(v) for k, v in value.items()}
    return value


# outcome status 상수
MERGED = "merged"
GATE_FAILED = "gate_failed"
MISSING_RESPONSE = "missing_response"
INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class CardOutcome:
    document_id: str
    status: str      # MERGED | GATE_FAILED | MISSING_RESPONSE | INVALID_RESPONSE
    detail: str = ""  # 게이트 report 또는 사유


@dataclass
class AssembleResult:
    deltas: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcomes: list[CardOutcome] = field(default_factory=list)

    @property
    def merged(self) -> int:
        return sum(1 for o in self.outcomes if o.status == MERGED)

    @property
    def held(self) -> int:
        return sum(1 for o in self.outcomes if o.status != MERGED)

    def report(self) -> str:
        head = f"[WL 심층분석 fan-out] 병합 {self.merged} · 보류 {self.held} (총 {len(self.outcomes)})"
        lines = [head]
        label = {MERGED: "✓ 병합", GATE_FAILED: "✖ 게이트 FAIL",
                 MISSING_RESPONSE: "· 응답 누락", INVALID_RESPONSE: "· 응답 형식오류"}
        for o in self.outcomes:
            lines.append(f"  {label.get(o.status, o.status)} — {o.document_id}")
            if o.status != MERGED and o.detail:
                for dl in o.detail.splitlines():
                    lines.append(f"      {dl}")
        return "\n".join(lines)


def assemble_deltas(jobs: Any, responses: dict[str, Any] | None) -> AssembleResult:
    """jobs(build_jobs 산출) + responses({document_id: deep_analysis dict}) → inject 델타.

    각 카드의 deep_analysis 를 그 카드 `body_full` 로 `verify_deep_analysis` 게이트에 통과시켜
    **PASS 만** 델타에 싣는다. 델타 포맷 = `inject_slots.inject_deep_analysis` 계약과 동일:
    `{document_id: {"deep_analysis": {...}, "source_text": body_full}}`. FAIL/누락/형식오류는
    outcome 에 사유(게이트 report)를 남긴다 — 비차단(그 카드는 6슬롯만으로 발행).
    """
    responses = responses or {}
    result = AssembleResult()
    for job in _as_jobs(jobs):
        doc = job.document_id
        da = responses.get(doc)
        if da is None:
            result.outcomes.append(CardOutcome(doc, MISSING_RESPONSE,
                                                "서브에이전트 응답 없음 — 6슬롯만으로 발행"))
            continue
        if not isinstance(da, dict):
            result.outcomes.append(CardOutcome(doc, INVALID_RESPONSE,
                                                "응답이 JSON 객체가 아님 — 6슬롯만으로 발행"))
            continue
        da = _unescape_entities(da)  # LLM 이 이스케이프한 &amp;/&lt; 등 → 원문자(이중 이스케이프 방지)
        # card_type 을 넘겨 필수 섹션·D2 성격을 확정(483=CFR 인용 WARN). 빈값이면 게이트가
        # 산출물 키로 자동판별(WL·admin 후방호환 — Job.card_type 미설정 옛 jobs.json 도 안전).
        gate = vda.run_deep_analysis_gate(da, job.body_full, card_type=job.card_type or None)
        if gate.ok:
            result.deltas[doc] = {"deep_analysis": da, "source_text": job.body_full}
            result.outcomes.append(CardOutcome(doc, MERGED, gate.report))
        else:
            result.outcomes.append(CardOutcome(doc, GATE_FAILED, gate.report))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        prog="deep_analysis_fanout",
        description="GRM WL 심층분석 fan-out 오케스트레이션(순수) — build-jobs / assemble.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pj = sub.add_parser("build-jobs", help="handoff → 서브에이전트 작업목록 [{document_id, body_full}]")
    pj.add_argument("--handoff", required=True, help="handoff JSON(to_dict 카드 목록 또는 {'cards':[...]})")
    pj.add_argument("--out", required=True, help="작업목록 출력 경로(jobs.json)")

    pa = sub.add_parser("assemble", help="서브에이전트 응답 → 게이트 → inject 델타")
    pa.add_argument("--jobs", required=True, help="build-jobs 산출 jobs.json")
    pa.add_argument("--responses", required=True,
                    help="서브에이전트 응답 JSON({document_id: deep_analysis 4섹션 dict})")
    pa.add_argument("--out", required=True, help="inject 델타 출력 경로(deep-analysis-deltas)")
    args = p.parse_args(argv)

    if args.cmd == "build-jobs":
        jobs = build_jobs(_load(args.handoff))
        _dump([j.to_dict() for j in jobs], args.out)
        print(f"[build-jobs] deep_analysis_ready 카드 {len(jobs)}건 → {args.out}", file=sys.stderr)
        return 0

    # assemble
    result = assemble_deltas(_load(args.jobs), _load(args.responses))
    _dump(result.deltas, args.out)
    print(result.report(), file=sys.stderr)
    print(f"[assemble] 델타 {len(result.deltas)}건 → {args.out} "
          f"(다음: inject_slots.py --deep-analysis-deltas {args.out})", file=sys.stderr)
    return 0  # 항상 0 — fan-out FAIL 은 비차단(카드 단위 graceful degrade)


if __name__ == "__main__":
    raise SystemExit(main())
