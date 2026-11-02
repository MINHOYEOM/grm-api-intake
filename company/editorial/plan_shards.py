# -*- coding: utf-8 -*-
"""데스크가 샤드 계획을 계산한다 — 카드 수·483 관찰 건수로 샤드 수 N 을 정하고 카드를 나눈다.

LLM 이 나누지 않는다. 데스크는 입력 JSON 을 쓰고 이 스크립트를 돌려 나온 `shards` 를 `desk.json` 에 그대로 넣는다.
**카드를 줄이는 일은 없다** — 대표 규칙(FDA 게시 기준 전부). 모자라면 샤드가 늘 뿐이다.

    입력  {"delta": ["web_card_id", ...(handoff 순서)],
           "deep": [{"id": "web_card_id", "obs": 0}, ...]}        # obs = 483 관찰 건수(없으면 0)
    출력  {"delta": {"A": [...], ...}, "deep": {"A": [...], ...}, "tldr": "T"}

    python company/editorial/plan_shards.py input.json [--delta-cap 18] [--deep-cards 12] [--deep-obs 30] [--max 8]

· delta: handoff 순서를 지킨 연속 구간으로 균등 분할(샤드 간 카드 수 차이 ≤1). N = ceil(카드수 / delta-cap), 2~max.
· deep: 카드 수와 관찰 건수 **둘 다** 한 샤드 한도를 넘지 않도록 N = max(ceil(카드/deep-cards), ceil(관찰/deep-obs)), 2~max,
  무거운 카드부터 가장 가벼운 샤드에 넣는다(LPT). 한 카드는 정확히 한 샤드.
· 상한 max 를 넘어도 카드는 버리지 않는다 — 마지막 샤드들에 더 싣고 경고를 낸다(`warnings`).
· `tldr`: 브리프 `tldr`·`tldr_en` 은 델타 샤드가 아니라 가상 샤드 `T` 몫이다(모든 델타 샤드가 끝난 뒤 한 번).
"""
import argparse
import json
import math
import string
import sys

LETTERS = string.ascii_uppercase  # 최대 26 — 실제 상한은 --max


def _clamp(n, lo, hi):
    return max(lo, min(hi, n))


def plan_delta(ids, cap, max_n):
    if len(set(ids)) != len(ids):
        raise ValueError("delta 목록에 중복 카드")
    n = _clamp(math.ceil(len(ids) / cap) if ids else 2, 2, max_n)
    base, extra = divmod(len(ids), n)
    out, pos = {}, 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        out[LETTERS[i]] = list(ids[pos:pos + size])
        pos += size
    return out


def plan_deep(items, cap_cards, cap_obs, max_n):
    ids = [x["id"] for x in items]
    if len(set(ids)) != len(ids):
        raise ValueError("deep 목록에 중복 카드")
    total_obs = sum(int(x.get("obs") or 0) for x in items)
    need = max(math.ceil(len(items) / cap_cards), math.ceil(total_obs / cap_obs)) if items else 2
    n = _clamp(need, 2, max_n)
    bins = [{"ids": [], "cards": 0, "obs": 0} for _ in range(n)]
    # 무거운 카드(관찰 많은 순, 같으면 입력 순서)부터 가장 가벼운 샤드로
    order = sorted(range(len(items)), key=lambda i: (-int(items[i].get("obs") or 0), i))
    for i in order:
        b = min(bins, key=lambda b: (b["obs"] + b["cards"], b["cards"]))
        b["ids"].append(items[i]["id"])
        b["cards"] += 1
        b["obs"] += int(items[i].get("obs") or 0)
    pos = {x["id"]: k for k, x in enumerate(items)}
    return {LETTERS[i]: sorted(b["ids"], key=pos.get) for i, b in enumerate(bins)}


def plan(data, delta_cap=18, deep_cards=12, deep_obs=30, max_n=8):
    delta = plan_delta(list(data.get("delta") or []), delta_cap, max_n)
    deep = plan_deep(list(data.get("deep") or []), deep_cards, deep_obs, max_n)
    warnings = []
    need_delta = math.ceil(len(data.get("delta") or []) / delta_cap)
    if need_delta > max_n:
        warnings.append(f"delta 필요 샤드 {need_delta} > 상한 {max_n} — 샤드당 카드가 한도를 넘는다(카드는 줄이지 않았다)")
    items = list(data.get("deep") or [])
    need_deep = max(math.ceil(len(items) / deep_cards) if items else 0,
                    math.ceil(sum(int(x.get('obs') or 0) for x in items) / deep_obs) if items else 0)
    if need_deep > max_n:
        warnings.append(f"deep 필요 샤드 {need_deep} > 상한 {max_n} — 샤드당 분량이 한도를 넘는다(카드는 줄이지 않았다)")
    return {"delta": delta, "deep": deep, "tldr": "T"}, warnings


def main(argv):
    ap = argparse.ArgumentParser(description="편집팀 샤드 계획")
    ap.add_argument("input")
    ap.add_argument("--delta-cap", type=int, default=18)
    ap.add_argument("--deep-cards", type=int, default=12)
    ap.add_argument("--deep-obs", type=int, default=30)
    ap.add_argument("--max", type=int, default=8, dest="max_n")
    a = ap.parse_args(argv[1:])
    with open(a.input, encoding="utf-8") as f:
        data = json.load(f)
    try:
        shards, warnings = plan(data, a.delta_cap, a.deep_cards, a.deep_obs, a.max_n)
    except ValueError as exc:
        print("계획 실패:", exc)
        return 1
    for w in warnings:
        print("경고:", w, file=sys.stderr)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    json.dump(shards, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
