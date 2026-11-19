# -*- coding: utf-8 -*-
"""데스크가 샤드 계획을 계산한다 — 카드 수·483 관찰 건수로 샤드 수 N 을 정하고 카드를 나눈다.

LLM 이 나누지 않는다. 데스크는 입력 JSON 을 쓰고 이 스크립트를 돌려 나온 `shards` 를 `desk.json` 에 그대로 넣는다.
**카드를 줄이는 일은 없다** — 대표 규칙(FDA 게시 기준 전부). 모자라면 샤드가 늘 뿐이다.

    입력  {"delta": ["web_card_id", ...(handoff 순서)],
           "deep": [{"id": "web_card_id", "obs": 0}, ...]}        # obs = 483 관찰 건수(없으면 0) · chars = 원문 글자 수(선택)
    출력  {"delta": {"A": [...], ...}, "deep": {"A": [...], ...}, "tldr": "T"}

    python company/editorial/plan_shards.py input.json [--handoff handoff.json] [--delta-cap 18] [--deep-cards 12] [--deep-obs 30]
                                            [--deep-chars 60000] [--max 8] [--deep-max 16]

· delta: handoff 순서를 지킨 연속 구간으로 균등 분할(샤드 간 카드 수 차이 ≤1). N = ceil(카드수 / delta-cap), 2~max.
· deep: 카드 수·관찰 건수·**원문 글자 수** 셋 다 한 샤드 한도를 넘지 않도록
  N = max(ceil(카드/deep-cards), ceil(관찰/deep-obs), ceil(글자/deep-chars)), 2~deep-max,
  무거운 카드(글자 많은 순)부터 가장 가벼운 샤드에 넣는다(LPT). 한 카드는 정확히 한 샤드.
  글자 수는 `--handoff` 를 주면 handoff 행의 `deep_analysis_input` 문자열 길이 합으로 채운다(입력에 `chars` 가 있으면 그 값).
  (2026-11-19 리허설 1120g: 카드 12장·관찰 30건 한도 안인데 원문이 샤드당 5~13만 자라 심층 워커가 7개 중 2개만 하고 끝냈다.
   실측 12.5만 자 샤드 약 8분 — 6만 자 한도는 점유 25분 안에 넉넉히 끝나는 크기다.)
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


def _chars(x):
    return int(x.get("chars") or 0)


def _text_len(v):
    if isinstance(v, str):
        return len(v)
    if isinstance(v, dict):
        return sum(_text_len(x) for x in v.values())
    if isinstance(v, list):
        return sum(_text_len(x) for x in v)
    return 0


def handoff_chars(handoff):
    """{web_card_id: 원문 글자 수} — 심층 기자가 읽는 `deep_analysis_input`(483 은 body_full) 문자열 길이 합."""
    out = {}
    for r in (handoff or {}).get("rows") or []:
        cid = r.get("web_card_id")
        if cid:
            out[cid] = _text_len(r.get("deep_analysis_input") or {})
    return out


def deep_need(items, cap_cards, cap_obs, cap_chars):
    if not items:
        return 0
    return max(math.ceil(len(items) / cap_cards),
               math.ceil(sum(int(x.get("obs") or 0) for x in items) / cap_obs),
               math.ceil(sum(_chars(x) for x in items) / cap_chars) if cap_chars else 0)


def plan_deep(items, cap_cards, cap_obs, max_n, cap_chars=0):
    ids = [x["id"] for x in items]
    if len(set(ids)) != len(ids):
        raise ValueError("deep 목록에 중복 카드")
    need = deep_need(items, cap_cards, cap_obs, cap_chars) if items else 2
    n = _clamp(need, 2, max_n)
    bins = [{"ids": [], "cards": 0, "obs": 0, "chars": 0} for _ in range(n)]
    # 무거운 카드(원문 글자 많은 순 → 관찰 많은 순, 같으면 입력 순서)부터 가장 가벼운 샤드로.
    # 카드 한도가 찬 샤드는 다른 샤드가 남아 있는 한 건너뛴다(글자만 맞추다 작은 카드가 한 샤드에 몰리지 않게).
    order = sorted(range(len(items)), key=lambda i: (-_chars(items[i]), -int(items[i].get("obs") or 0), i))
    for i in order:
        room = [b for b in bins if b["cards"] < cap_cards] or bins
        b = min(room, key=lambda b: (b["chars"], b["obs"] + b["cards"], b["cards"]))
        b["ids"].append(items[i]["id"])
        b["cards"] += 1
        b["obs"] += int(items[i].get("obs") or 0)
        b["chars"] += _chars(items[i])
    pos = {x["id"]: k for k, x in enumerate(items)}
    return {LETTERS[i]: sorted(b["ids"], key=pos.get) for i, b in enumerate(bins)}


def plan(data, delta_cap=18, deep_cards=12, deep_obs=30, max_n=8, deep_chars=60000, deep_max=16, handoff=None):
    delta = plan_delta(list(data.get("delta") or []), delta_cap, max_n)
    items = [dict(x) for x in (data.get("deep") or [])]
    if handoff is not None:
        sizes = handoff_chars(handoff)
        for x in items:
            if "chars" not in x:
                x["chars"] = sizes.get(x["id"], 0)
    deep = plan_deep(items, deep_cards, deep_obs, deep_max, deep_chars)
    warnings = []
    need_delta = math.ceil(len(data.get("delta") or []) / delta_cap)
    if need_delta > max_n:
        warnings.append(f"delta 필요 샤드 {need_delta} > 상한 {max_n} — 샤드당 카드가 한도를 넘는다(카드는 줄이지 않았다)")
    need_deep = deep_need(items, deep_cards, deep_obs, deep_chars)
    if need_deep > deep_max:
        warnings.append(f"deep 필요 샤드 {need_deep} > 상한 {deep_max} — 샤드당 분량이 한도를 넘는다(카드는 줄이지 않았다)")
    for x in items:
        if deep_chars and _chars(x) > deep_chars:
            warnings.append(f"deep 카드 {x['id']} 원문 {_chars(x)}자 > 샤드 한도 {deep_chars} — 그 카드 혼자 한 샤드가 넘친다")
    return {"delta": delta, "deep": deep, "tldr": "T"}, warnings


def main(argv):
    ap = argparse.ArgumentParser(description="편집팀 샤드 계획")
    ap.add_argument("input")
    ap.add_argument("--delta-cap", type=int, default=18)
    ap.add_argument("--deep-cards", type=int, default=12)
    ap.add_argument("--deep-obs", type=int, default=30)
    ap.add_argument("--deep-chars", type=int, default=60000)
    ap.add_argument("--max", type=int, default=8, dest="max_n")
    ap.add_argument("--deep-max", type=int, default=16)
    ap.add_argument("--handoff", default=None, help="handoff 파일 — 심층 카드 원문 글자 수를 채운다")
    a = ap.parse_args(argv[1:])
    with open(a.input, encoding="utf-8") as f:
        data = json.load(f)
    handoff = None
    if a.handoff:
        with open(a.handoff, encoding="utf-8") as f:
            handoff = json.load(f)
    try:
        shards, warnings = plan(data, a.delta_cap, a.deep_cards, a.deep_obs, a.max_n, a.deep_chars, a.deep_max, handoff)
    except ValueError as exc:
        print("계획 실패:", exc)
        return 1
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    for w in warnings:
        print("경고:", w, file=sys.stderr)
    json.dump(shards, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
