# -*- coding: utf-8 -*-
"""기자 샤드 N개(가변)의 결과를 delta.json · deep_delta.json 으로 합친다 (교열이 첫 단계로 돌린다).

LLM 이 옮겨 적지 않는다 — 딕셔너리 합집합만 한다. 데스크의 `shards` 계획과 한 장도 어긋나면 exit 1.

    python company/editorial/merge_parts.py company/run/2026-11-02
"""
import json
import os
import sys

# 샤드 이름은 데스크 계획(desk.json shards)의 키 — A, B, C … (plan_shards.py). 고정이 아니다.


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


# 카드 영문 갈래 필수 다섯 — web/render.py CARD_NARRATIVE_FIELDS 와 같다(tests 가 일치를 확인한다).
EN_FIELDS = ("title_issue", "summary", "implication", "key_facts", "checks")


def _en_gaps(card):
    """카드 영문 갈래에서 비었거나 빠진 필드 목록. 완결이면 []."""
    en = card.get("en") if isinstance(card, dict) else None
    en = en if isinstance(en, dict) else {}
    gaps = []
    for f in EN_FIELDS:
        v = en.get(f)
        ok = bool([x for x in v if str(x).strip()]) if isinstance(v, list) else bool(str(v or "").strip())
        if not ok:
            gaps.append(f)
    return gaps


def merge(folder):
    problems = []
    desk = _load(os.path.join(folder, "desk.json"))
    if desk.get("status") != "완료":
        return None, None, [f"desk.json status={desk.get('status')!r}"]
    plan = desk.get("shards")
    if not plan:
        return None, None, ["desk.json 에 shards 계획이 없다"]

    cards, tldr, tldr_en, deep = {}, None, None, {}
    delta_shards = sorted(plan.get("delta", {}))
    deep_shards = sorted(plan.get("deep", {}))
    for s in delta_shards:
        planned = plan["delta"].get(s, [])
        part = _part(folder, f"delta.{s}.json", planned, problems)
        if part is None:
            continue
        got = part.get("cards", {})
        _diff(f"delta.{s}.json", got, planned, problems)
        cards.update(got)
        if "tldr" in part:
            tldr, tldr_en = part["tldr"], part.get("tldr_en")
    # tldr·tldr_en 은 가상 샤드 T(delta.T.json) 몫. 옛 형식(델타 샤드 안에 tldr)도 받는다.
    t_name = plan.get("tldr") or "T"
    t_path = os.path.join(folder, f"delta.{t_name}.json")
    if os.path.exists(t_path):
        t_part = _load(t_path)
        if t_part.get("status") == "완료":
            tldr, tldr_en = t_part.get("tldr"), t_part.get("tldr_en")
        else:
            problems.append(f"delta.{t_name}.json: status={t_part.get('status')!r} (완료 아님)")
    if not tldr or not tldr_en:
        problems.append("tldr · tldr_en 이 없다 (delta.T.json 의 몫)")

    en_bad = {cid: _en_gaps(c) for cid, c in cards.items() if _en_gaps(c)}
    if en_bad:
        ex = ", ".join(f"{cid}({'/'.join(g)})" for cid, g in list(en_bad.items())[:3])
        problems.append(f"영문 슬롯 미완결 {len(en_bad)}/{len(cards)}장 — 이 호는 영어로 나가지 않는다 (예: {ex})")

    for s in deep_shards:
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
