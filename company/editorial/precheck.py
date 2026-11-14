# -*- coding: utf-8 -*-
"""편집팀 사전 점검 — 교열이 잡기 전에 코드가 셀 수 있는 것을 먼저 센다.

    python company/editorial/precheck.py terms <delta 파일>
        델타(샤드 `delta.A.json` 또는 합친 `delta.json`)의 한국어 산문을 조립 표현 검사
        (`inject_slots.lint_prose_style`)와 **같은 규칙**으로 본다 — 금지 표현, [용어·표기] 표 왼쪽 표기,
        약어 단독(CAPA·OOS·COA 가 한글 풀이 없이 나온 카드). 걸린 것이 있으면 한 줄씩 내고 exit 1.
        기자는 샤드를 `완료`로 내기 전에 돌려 0 으로 만든다. 길이 상한도 같이 본다(제목 이슈·hook_line).

    python company/editorial/precheck.py deep-source <handoff 파일> <deep_delta 파일> --out <검사용 파일>
        심층분석 델타의 각 카드에 그 주 handoff 의 `deep_analysis_input.body_full` 을 `source_text` 로 붙인
        **검사용 사본**을 쓴다. 발행 브릿지가 조립 직전에 하는 일과 같다(`delta_bridge.apply_handoff_source_text`).
        교열의 코드 검사는 이 사본을 `--deep` 에 넘긴다 — 붙이지 않으면 근거 대조가 전부 '원문에 없음'이 된다.
        사본은 실행 폴더에 두지 않고 커밋하지 않는다(반납하는 `deep_delta.json` 은 그대로).

    python company/editorial/precheck.py rework-diff <1차 delta.json> <2차 delta.json> <1차 copyedit.json>
        재작업(반려 카드만 한 번 고치기) 뒤에 다시 합친 델타가 **반려 카드만** 바뀌었는지 본다.
        카드가 늘거나 줄었거나, 반려되지 않은 카드·`tldr` 가 한 글자라도 바뀌었으면 exit 1 — 교열은 판정하지 않고 보류한다.
        통과면 반려 카드마다 바뀐 슬롯을 한 줄씩 낸다(안 바뀐 반려 카드도 적는다 — 다시 반려될 카드다).

저장소 루트에서 돌린다. 읽을 수 없으면 exit 2(메시지는 stderr).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import inject_slots  # noqa: E402


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def term_lines(delta):
    """표현 검사 줄 + 길이 상한 위반 줄(순수)."""
    out = [line for line in inject_slots.lint_prose_style(delta).lines() if not line.startswith("시사점 맺음 반복")]
    hook = inject_slots._HOOK_LINE_MAX
    title = inject_slots._MAX_TITLE_ISSUE
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    for cid, card in cards.items():
        if not isinstance(card, dict):
            continue
        en = card.get("en") if isinstance(card.get("en"), dict) else {}
        for label, text, limit in (
                ("title_issue", card.get("title_issue"), title),
                ("en.title_issue", en.get("title_issue"), title * 2),
                ("hook_line", card.get("hook_line"), hook["ko"]),
                ("en.hook_line", en.get("hook_line"), hook["en"])):
            if isinstance(text, str) and len(text) > limit:
                out.append(f"길이 {cid}.{label}: {len(text)}자 — {limit}자 이내")
    return out


def handoff_bodies(payload):
    """handoff payload → {web_card_id: body_full}. `delta_bridge.extract_handoff_body_full` 과 같은 규칙."""
    bodies = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        cid = row.get("web_card_id")
        dai = row.get("deep_analysis_input")
        body = dai.get("body_full") if isinstance(dai, dict) else None
        if isinstance(cid, str) and cid and isinstance(body, str) and body.strip() and cid not in bodies:
            bodies[cid] = body
    return bodies


def with_source_text(deep, bodies):
    """deep 델타 사본에 `source_text` 를 붙인다(순수). handoff 에 원문이 없는 카드는 그대로 둔다."""
    out, filled = {}, 0
    for cid, entry in deep.items():
        if isinstance(entry, dict) and bodies.get(cid):
            entry = dict(entry, source_text=bodies[cid])
            filled += 1
        out[cid] = entry
    return out, filled


def rejected_ids(copyedit):
    """1차 교열 기록 → 반려 카드 id 집합. `rework.cards` 와 `cards[].verdict` 를 합친다."""
    ids = set()
    rework = copyedit.get("rework") if isinstance(copyedit.get("rework"), dict) else {}
    ids.update(str(x) for x in rework.get("cards") or [] if isinstance(x, str))
    for card in copyedit.get("cards") or []:
        if isinstance(card, dict) and card.get("verdict") != "pass" and card.get("web_card_id"):
            ids.add(str(card["web_card_id"]))
    return ids


def rework_diff(old, new, rejected):
    """(문제 줄, 알림 줄) — 문제 줄이 하나라도 있으면 재작업이 범위를 넘은 것이다(순수)."""
    problems, notes = [], []
    a = old.get("cards") if isinstance(old.get("cards"), dict) else {}
    b = new.get("cards") if isinstance(new.get("cards"), dict) else {}
    if not rejected:
        problems.append("1차 교열 기록에 반려 카드가 없다")
    for cid in sorted(set(a) - set(b)):
        problems.append(f"카드가 사라졌다: {cid}")
    for cid in sorted(set(b) - set(a)):
        problems.append(f"카드가 새로 생겼다: {cid}")
    for cid in sorted(rejected - set(a)):
        problems.append(f"반려 카드가 1차 델타에 없다: {cid}")
    for cid in sorted(set(a) & set(b)):
        if a[cid] == b[cid]:
            if cid in rejected:
                notes.append(f"안 바뀜(반려 카드) {cid}")
            continue
        ca = a[cid] if isinstance(a[cid], dict) else {}
        cb = b[cid] if isinstance(b[cid], dict) else {}
        slots = sorted(k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k))
        if cid in rejected:
            notes.append(f"고침 {cid}: {', '.join(slots)}")
        else:
            problems.append(f"반려되지 않은 카드가 바뀌었다: {cid} ({', '.join(slots)})")
    for key in sorted((set(old) | set(new)) - {"cards"}):
        if old.get(key) != new.get(key):
            problems.append(f"카드 밖의 값이 바뀌었다: {key}")
    return problems, notes


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="편집팀 사전 점검")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("terms")
    t.add_argument("delta")
    d = sub.add_parser("deep-source")
    d.add_argument("handoff")
    d.add_argument("deep")
    d.add_argument("--out", required=True)
    r = sub.add_parser("rework-diff")
    r.add_argument("old")
    r.add_argument("new")
    r.add_argument("copyedit")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "terms":
            delta = _load(args.delta)
            if not isinstance(delta, dict):
                raise ValueError("델타가 객체가 아니다")
            lines = term_lines(delta)
            for line in lines:
                print(line)
            print(f"사전 점검: {len(lines)}건" + ("" if lines else " — 통과"))
            return 1 if lines else 0
        if args.cmd == "rework-diff":
            old, new, copyedit = _load(args.old), _load(args.new), _load(args.copyedit)
            if not all(isinstance(x, dict) for x in (old, new, copyedit)):
                raise ValueError("델타·교열 기록이 객체가 아니다")
            problems, notes = rework_diff(old, new, rejected_ids(copyedit))
            for line in problems + notes:
                print(line)
            print("재작업 대조: " + (f"범위를 넘음 {len(problems)}건" if problems else "통과 — 반려 카드만 바뀌었다"))
            return 1 if problems else 0
        handoff, deep = _load(args.handoff), _load(args.deep)
        if not isinstance(handoff, dict) or not isinstance(deep, dict):
            raise ValueError("handoff·deep 델타가 객체가 아니다")
        out, filled = with_source_text(deep, handoff_bodies(handoff))
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"검사용 사본: {args.out} — 원문 붙임 {filled}/{len(deep)}장")
        return 0
    except (OSError, ValueError) as e:
        print(f"사전 점검을 못 했다: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
