# -*- coding: utf-8 -*-
"""기자 샤드 4개의 결과를 delta.json · deep_delta.json 으로 합친다 (교열이 첫 단계로 돌린다).

LLM 이 옮겨 적지 않는다 — 딕셔너리 합집합만 한다. 데스크의 `shards` 계획과 한 장도 어긋나면 exit 1.

    python company/editorial/merge_parts.py company/run/2026-11-02
"""
import json
import os
import sys

DELTA_SHARDS = ("A", "B")
DEEP_SHARDS = ("A", "B")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _part(folder, name, planned_ids, problems):
    """샤드 파일 하나를 읽는다. 계획이 비어 있으면 파일이 없어도 된다."""
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        if planned_ids:
            problems.append(f"{name}: 파일 없음 (계획 {len(planned_ids)}건)")
        return None
    part = _load(path)
    if part.get("status") != "완료":
        problems.append(f"{name}: status={part.get('status')!r} (완료 아님)")
        return None
    return part


def _diff(name, got, planned, problems):
    missing = sorted(set(planned) - set(got))
    extra = sorted(set(got) - set(planned))
    if missing:
        problems.append(f"{name}: 계획에 있는데 없는 카드 {len(missing)}건 (예: {missing[:3]})")
    if extra:
        problems.append(f"{name}: 계획에 없는 카드 {len(extra)}건 (예: {extra[:3]})")


def merge(folder):
    problems = []
    desk = _load(os.path.join(folder, "desk.json"))
    if desk.get("status") != "완료":
        return None, None, [f"desk.json status={desk.get('status')!r}"]
    plan = desk.get("shards")
    if not plan:
        return None, None, ["desk.json 에 shards 계획이 없다"]

    cards, tldr, tldr_en, deep = {}, None, None, {}
    for s in DELTA_SHARDS:
        planned = plan["delta"].get(s, [])
        part = _part(folder, f"delta.{s}.json", planned, problems)
        if part is None:
            continue
        got = part.get("cards", {})
        _diff(f"delta.{s}.json", got, planned, problems)
        cards.update(got)
        if "tldr" in part:
            tldr, tldr_en = part["tldr"], part.get("tldr_en")
    if not tldr or not tldr_en:
        problems.append("tldr · tldr_en 이 없다 (delta.B.json 의 몫)")

    for s in DEEP_SHARDS:
        planned = plan["deep"].get(s, [])
        part = _part(folder, f"deep.{s}.json", planned, problems)
        if part is None:
            continue
        got = part.get("items", {})
        _diff(f"deep.{s}.json", got, planned, problems)
        for cid, item in got.items():
            inner = item.get("deep_analysis") or {}
            if cid.startswith("fda483-") and not (item.get("observations_ko") or inner.get("observations_ko")):
                problems.append(f"deep.{s}.json: {cid} observations_ko 없음 — 브리프 전체가 막힌다")
        deep.update(got)

    if problems:
        return None, None, problems
    delta = {"cards": cards, "tldr": tldr, "tldr_en": tldr_en}
    return delta, deep, []


def main(argv):
    folder = argv[1]
    delta, deep, problems = merge(folder)
    if problems:
        for p in problems:
            print("합치기 실패:", p)
        return 1
    with open(os.path.join(folder, "delta.json"), "w", encoding="utf-8") as f:
        json.dump(delta, f, ensure_ascii=False, separators=(",", ":"))
    if deep:
        with open(os.path.join(folder, "deep_delta.json"), "w", encoding="utf-8") as f:
            json.dump(deep, f, ensure_ascii=False, separators=(",", ":"))
    print(f"delta.json: 카드 {len(delta['cards'])}장 · deep_delta.json: {len(deep)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
