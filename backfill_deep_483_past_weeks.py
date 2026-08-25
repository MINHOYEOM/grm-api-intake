#!/usr/bin/env python3
"""[483 심층분석 과거주차 소급 2026-08-25] 2026-07-12~07-27 발행분 FDA 483 카드 69장에
심층분석을 소급 생성·병합하기 위한 결정론 양끝 도구(생성 자체는 클라우드 fan-out 이 한다).

배경 — `#453`("심층분석 fan-out 입력이 handoff 에 실리지 않던 2중 단절 수리", 2026-07-27)
이전 3주 동안 Routine 은 매주 "심층분석 대상 0건"으로 판단했다. handoff 에 `body_full` 이
안 실려 `deep_analysis_ready` 가 False 였기 때문이다. 그래서 그 3주 발행분 483 카드는
심층분석 없이 나갔다(07-12 11장·07-20 33장·07-27 25장 = **69장**. 같은 주 3·5·4장은 사람이
손으로 백필한 것이라 예외).

★그런데 **원문은 그 주차 deep 델타에 이미 남아 있다** — `source_text` 중앙값 11~12K자로,
파싱된 관찰 합계(2~3K)의 3.3~4.1배인 **원본 483 PDF 본문 전체**다. 즉 재수집·재-fetch 없이
생성만 하면 되는 결손이다.

이 도구가 채우는 두 구멍(그 외 계약은 전부 기존 것을 쓴다):

  ① `build-jobs` — 운영 `deep_analysis_fanout.build_jobs()` 는 **handoff** 를 입력으로 받는데,
     과거 주차 handoff 는 CI 아티팩트라 만료됐다. 대신 저장소에 커밋된 deep 델타의
     `source_text` 를 body_full 로 삼아 같은 Job 계약을 만든다.
     ★jobs 파일은 저장소 밖에 쓴다 — `source_text` 는 이미 델타에 있고, 같은 원문을 두 번
     커밋하면 그게 곧 표류 원천이다.

  ② `merge` — 이 69장은 브리프 카드에 `deep_analysis` **키 자체가 없다**(스캐폴드 시점에
     fan-out 대상이 아니었으므로). `inject_slots.inject_deep_analysis` 는 키 없는 카드를
     "대상 아님"으로 건너뛰므로, 병합 전에 placeholder(None)를 넣어 준다. 이것은 새 사실을
     만드는 것이 아니라 **#453 이 못 세운 표지를 뒤늦게 세우는 것**이고, 대상은 "그 주차 델타에
     원문이 실재하는 483 카드"로 좁힌다(원문 없는 카드에는 표지를 세우지 않는다).

     병합 자체는 **운영 경로를 그대로 호출**한다 — 게이트(D1~D5)와 D5b 결정론 수리가 모두
     운영과 동일하게 돈다. 게이트를 못 넘긴 카드는 실리지 않는다.

생성 절차(②는 클라우드 세션이 수행):
  1) python backfill_deep_483_past_weeks.py build-jobs --brief <brief> --deep <deep> --out <jobs.json>
  2) 클라우드 fan-out — 런북 `docs/prompts/GRM_DeepWL_fanout_실행프롬프트.md`,
     카드 유형이 전부 `fda-483` 이므로 생성 프롬프트는 `docs/prompts/GRM_Prompt_DeepFda483_v1.md`.
     산출 = `{document_id: {4섹션 + observations_ko}}` 형태의 responses.json.
  3) python backfill_deep_483_past_weeks.py merge --brief <brief> --deep <deep> \
         --responses <responses.json> [--apply]

사용 후 웹 골든 재동결이 필요할 수 있다(그 주차 페이지가 골든이면).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import inject_slots
from assemble_publish_brief import _write_json

CARD_TYPE_483 = "FDA 483 실사 관찰"
JOB_CARD_TYPE = "fda-483"          # deep_analysis_fanout.Job.card_type 계약값
_MIN_SOURCE_LEN = 500              # 이보다 짧으면 원문 확보로 보지 않는다(빈 껍데기 방지)


def select_targets(brief: dict[str, Any], deep: dict[str, Any]) -> list[dict[str, Any]]:
    """소급 대상 = 그 주차 델타에 원문이 있는데 심층분석이 없는 483 카드.

    이미 심층분석을 가진 카드(사람 백필분)는 건드리지 않는다 — 덮어쓰기는 이 도구의 일이
    아니다. 원문이 없는 카드도 제외한다(생성 근거가 없으면 만들지 않는다)."""
    cards = {c.get("id"): c for c in (brief.get("cards") or []) if isinstance(c, dict)}
    out: list[dict[str, Any]] = []
    for doc_id, payload in (deep or {}).items():
        card = cards.get(doc_id)
        if card is None or card.get("card_type") != CARD_TYPE_483:
            continue
        if card.get("deep_analysis"):
            continue                               # 이미 있음(사람 백필분)
        if not isinstance(payload, dict) or payload.get("deep_analysis"):
            continue                               # 델타가 이미 생성분을 갖고 있음
        source_text = str(payload.get("source_text") or "")
        if len(source_text) < _MIN_SOURCE_LEN:
            continue                               # 원문 미확보 — 생성 근거 없음
        out.append({"document_id": doc_id, "source_text": source_text, "card": card})
    out.sort(key=lambda t: t["document_id"])       # 결정론 순서
    return out


def build_jobs(targets: list[dict[str, Any]]) -> list[dict[str, str]]:
    """`deep_analysis_fanout.Job.to_dict()` 와 동일 형태(같은 키·같은 의미)."""
    return [{"document_id": t["document_id"], "body_full": t["source_text"],
             "card_type": JOB_CARD_TYPE} for t in targets]


def build_deltas(targets: list[dict[str, Any]],
                 responses: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """responses({document_id: 4섹션 dict}) → inject 델타. 응답 없는 대상은 사유와 함께 남긴다.

    `observations_ko`(관찰 국문 번역)는 심층분석 4섹션과 **분리**해 별도 델타 키로 싣는다 —
    운영 `assemble_deltas` 와 같은 규약이다(게이트는 4섹션만 본다)."""
    deltas: dict[str, Any] = {}
    missing: list[str] = []
    for t in targets:
        doc_id = t["document_id"]
        da = responses.get(doc_id)
        if not isinstance(da, dict):
            missing.append(doc_id)
            continue
        da = dict(da)
        obs_ko = da.pop("observations_ko", None)
        entry: dict[str, Any] = {"deep_analysis": da, "source_text": t["source_text"]}
        if isinstance(obs_ko, list) and obs_ko:
            entry["observations_ko"] = obs_ko
        deltas[doc_id] = entry
    return deltas, missing


def mark_fanout_targets(targets: list[dict[str, Any]], doc_ids: "set[str]") -> int:
    """병합 전 placeholder(`deep_analysis = None`) 삽입 — #453 이 못 세운 표지를 세운다.

    이미 키가 있으면 건드리지 않는다. 대상은 `select_targets` 가 좁힌 집합뿐이라, 원문 없는
    카드나 483 아닌 카드에는 표지가 서지 않는다."""
    marked = 0
    for t in targets:
        card = t["card"]
        if t["document_id"] in doc_ids and "deep_analysis" not in card:
            card["deep_analysis"] = None
            marked += 1
    return marked


def _cmd_build_jobs(args: argparse.Namespace) -> int:
    brief, deep = _load(args.brief), _load(args.deep)
    targets = select_targets(brief, deep)
    if not targets:
        print("[FAIL] 소급 대상 0건 — 이 주차는 이미 완료됐거나 원문이 없다")
        return 1
    jobs = build_jobs(targets)
    if os.path.abspath(args.out).startswith(os.path.abspath(os.path.dirname(__file__))):
        print("[FAIL] jobs 파일은 저장소 밖에 써라 — 같은 원문을 두 번 커밋하면 표류 원천이 된다")
        return 1
    _write_json(args.out, jobs)
    total = sum(len(j["body_full"]) for j in jobs)
    print(f"대상 {len(jobs)}건 · 원문 합계 {total:,}자 → {args.out}")
    print("다음: 클라우드 fan-out(런북 docs/prompts/GRM_DeepWL_fanout_실행프롬프트.md · "
          "생성 프롬프트 docs/prompts/GRM_Prompt_DeepFda483_v1.md)")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    brief, deep = _load(args.brief), _load(args.deep)
    responses = _load(args.responses)
    if not isinstance(responses, dict):
        print("[FAIL] responses 는 {document_id: 4섹션 dict} 객체여야 한다")
        return 1
    targets = select_targets(brief, deep)
    if not targets:
        print("[FAIL] 소급 대상 0건")
        return 1
    deltas, missing = build_deltas(targets, responses)
    if not deltas:
        print(f"[FAIL] 병합 가능한 응답 0건(대상 {len(targets)}건)")
        return 1
    marked = mark_fanout_targets(targets, set(deltas))
    report = inject_slots.inject_deep_analysis(brief, deltas)

    cards = {c.get("id"): c for c in (brief.get("cards") or []) if isinstance(c, dict)}
    merged = sorted(k for k in deltas if (cards.get(k) or {}).get("deep_analysis"))
    blocked = sorted(set(deltas) - set(merged))
    for line in report.warnings:
        print(f"  [WARN] {line}")
    for line in report.errors:
        print(f"  [FAIL] {line}")
    print(f"\n대상 {len(targets)} · 응답 {len(deltas)} · 응답없음 {len(missing)} · "
          f"표지 삽입 {marked} · 병합 {len(merged)} · 게이트 보류 {len(blocked)}")
    for k in blocked:
        print(f"   보류 {k}")
    if missing:
        print(f"   응답없음: {missing[:10]}{' ...' if len(missing) > 10 else ''}")

    # 게이트 보류 카드는 placeholder 만 남아 발행 계약이 깨진다(값 없는 키) → 되돌린다.
    reverted = 0
    for t in targets:
        card = t["card"]
        if t["document_id"] in blocked and card.get("deep_analysis") is None:
            card.pop("deep_analysis", None)
            reverted += 1
    if reverted:
        print(f"   보류분 표지 회수 {reverted}건(빈 키를 남기지 않는다)")

    if not merged:
        print("\n[FAIL] 병합 0건")
        return 1
    if not args.apply:
        print("\n(dry-run — 기록하지 않았다. 반영하려면 --apply)")
        return 0
    _write_json(args.brief, brief)
    if args.record:
        _write_json(args.record, deltas)
        print(f"근거 기록: {args.record}")
    print(f"기록 완료: {args.brief}")
    return 0


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: "list[str] | None" = None) -> int:
    # [cp949 가드] 파이프 stdout 은 Windows 기본 코드페이지로 인코딩된다 — 리포트 문구에
    # em-dash 하나만 섞여도 UnicodeEncodeError 로 죽고, 그러면 --apply 의 기록이 출력 실패에
    # 끌려 유실된다. 전수 가드(tests/test_cli_stdout_encoding.py)가 이 관용구 부재를 잡는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        description="483 심층분석 과거주차(07-12~07-27) 소급 — 작업 생성 / 응답 병합")
    sub = p.add_subparsers(dest="cmd", required=True)

    pj = sub.add_parser("build-jobs", help="발행 브리프 + deep 델타 → fan-out 작업목록")
    pj.add_argument("--brief", required=True)
    pj.add_argument("--deep", required=True)
    pj.add_argument("--out", required=True, help="jobs.json 경로(저장소 밖)")

    pm = sub.add_parser("merge", help="fan-out 응답 → 게이트 → 발행 브리프 병합")
    pm.add_argument("--brief", required=True)
    pm.add_argument("--deep", required=True)
    pm.add_argument("--responses", required=True)
    pm.add_argument("--record", default="", help="병합에 쓴 델타를 근거로 남길 경로(선택)")
    pm.add_argument("--apply", action="store_true")

    args = p.parse_args(argv)
    return _cmd_build_jobs(args) if args.cmd == "build-jobs" else _cmd_merge(args)


if __name__ == "__main__":
    sys.exit(main())
