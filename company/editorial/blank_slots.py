# -*- coding: utf-8 -*-
"""교열 '칸 삭제' — 정확성 오류가 선택 칸에만 있는 카드는 그 칸을 국·영 짝으로 비우고 통과시킨다(2026-11-21 대표 결정).

교열(`copyedit.md` 2-3)이 `copyedit.json` 의 `cards[].notes` 에 `{"item": "칸 삭제", "slot": "<칸>", "evidence": "<사유>"}` 를
적으면, 이 스크립트가 그 칸을 기계적으로 비운다 — LLM 이 델타를 옮겨 적거나 고쳐 쓰지 않는다. 비우기만 하므로 새 문장이 생기지 않는다.

비울 수 있는 칸(조립 `assemble_publish_brief`·`check_en_slots`·심층 게이트가 빈 값으로 통과시키는 것만 — 리허설 1121a 실측):
  hook_line              한국어 `hook_line` 과 `en.hook_line` 을 지운다(선택 필드).
  implication            한국어·영문 시사점을 "" 로(빈 시사점은 경고만 — `_OPTIONAL_PROSE_SLOTS`).
  checks                 한국어·영문 점검 목록을 [] 로(빈 점검은 경고만).
  checks[i]              i 번째 점검을 국·영 같은 위치에서 뺀다. 남는 수가 2~3 이어야 한다(아니면 `checks` 로).
  key_facts[i]           i 번째 핵심 사실을 국·영 같은 위치에서 뺀다. 하나 이상 남아야 한다(빈 key_facts 는 조립 거부).
  deep.key_violations[i] (FDA 483 심층만) i 번째 지적을 뺀다. 하나 이상 남아야 한다.
  deep.inspectional_significance · deep.administrative_risks   (FDA 483 심층만) "" 로 — 483 의 선택 절.
그 밖의 칸(title_issue·summary·quotes·WL/행정처분 심층 절 등)은 필수라 비울 수 없다 → 그 카드는 반려다.

  python company/editorial/blank_slots.py apply <copyedit.json> <delta 입력> <delta 출력> [--deep-in X --deep-out Y]
  python company/editorial/blank_slots.py verify <copyedit.json> <brief_check.json>

`apply` 는 입력을 읽고 출력에 쓴다 — 같은 입력으로 다시 돌려도 결과가 같다(입력은 칸 삭제 전 사본이어야 한다).
`verify` 는 다시 돌린 조립본에서 비운 칸이 비었는지, 심층을 건드린 카드가 심층을 잃지 않았는지 본다
(심층 게이트 실패는 조립을 막지 않고 심층만 조용히 빠지므로 exit 0 만으로는 모른다).
exit 0 = 통과 · 1 = 문제(아무것도 쓰지 않는다) · 2 = 입력 오류.
"""
import argparse
import json
import re
import sys

ITEM = "칸 삭제"
_INDEXED = re.compile(r"^(key_facts|checks|deep\.key_violations)\[(\d+)\]$")
_DEEP_483_OPTIONAL = ("inspectional_significance", "administrative_risks")
_CHECKS_RANGE = (2, 3)


def blank_requests(copyedit):
    """copyedit.json → [(카드 id, 칸, 사유)] — `verdict` 와 무관하게 `notes` 의 '칸 삭제' 만."""
    out = []
    for card in copyedit.get("cards") or []:
        if not isinstance(card, dict) or not card.get("web_card_id"):
            continue
        for note in card.get("notes") or []:
            if isinstance(note, dict) and str(note.get("item", "")).strip() == ITEM:
                out.append((str(card["web_card_id"]), str(note.get("slot", "")).strip(),
                            str(note.get("evidence", ""))))
    return out


def _is_483_deep(da):
    return isinstance(da, dict) and "inspectional_significance" in da and "fda_evaluation" not in da


def apply_blanks(delta, deep, requests):
    """(문제 줄, 한 일 줄) — 순수. delta·deep 을 제자리에서 고친다(문제가 있으면 호출자가 버린다)."""
    problems, done = [], []
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    # 같은 목록에서 여러 항목을 빼면 뒤 번호부터 빼야 앞 번호가 밀리지 않는다.
    indexed = sorted((r for r in requests if _INDEXED.match(r[1])),
                     key=lambda r: (r[0], _INDEXED.match(r[1]).group(1), -int(_INDEXED.match(r[1]).group(2))))
    plain = [r for r in requests if not _INDEXED.match(r[1])]
    for cid, slot, _why in plain + indexed:
        tag = f"{cid}.{slot}"
        card = cards.get(cid)
        m = _INDEXED.match(slot)
        if slot.startswith("deep."):
            item = (deep or {}).get(cid) if isinstance(deep, dict) else None
            da = item.get("deep_analysis") if isinstance(item, dict) else None
            if not _is_483_deep(da):
                problems.append(f"{tag}: FDA 483 심층만 비울 수 있다(이 카드는 심층이 없거나 WL·행정처분 — 필수 절)")
                continue
            if m:
                kv = da.get("key_violations")
                i = int(m.group(2))
                if not isinstance(kv, list) or i >= len(kv):
                    problems.append(f"{tag}: 그런 번호가 없다(key_violations {len(kv or [])}개)")
                elif len(kv) <= 1:
                    problems.append(f"{tag}: 지적이 하나뿐이라 뺄 수 없다(심층 필수 절)")
                else:
                    del kv[i]
                    done.append(tag)
                continue
            key = slot[len("deep."):]
            if key not in _DEEP_483_OPTIONAL:
                problems.append(f"{tag}: 비울 수 없는 심층 절(483 선택 절은 {', '.join(_DEEP_483_OPTIONAL)} 뿐)")
                continue
            da[key] = ""
            done.append(tag)
            continue
        if not isinstance(card, dict):
            problems.append(f"{tag}: 델타에 그 카드가 없다")
            continue
        en = card.get("en") if isinstance(card.get("en"), dict) else None
        if slot == "hook_line":
            card.pop("hook_line", None)
            if en is not None:
                en.pop("hook_line", None)
            done.append(tag)
        elif slot in ("implication", "checks"):
            empty = "" if slot == "implication" else []
            card[slot] = empty
            if en is not None:
                en[slot] = "" if slot == "implication" else []
            done.append(tag)
        elif m and m.group(1) in ("key_facts", "checks"):
            key, i = m.group(1), int(m.group(2))
            ko_list = card.get(key)
            en_list = en.get(key) if en is not None else None
            if not isinstance(ko_list, list) or i >= len(ko_list):
                problems.append(f"{tag}: 그런 번호가 없다({key} {len(ko_list or [])}개)")
                continue
            if en is not None and (not isinstance(en_list, list) or len(en_list) != len(ko_list)):
                problems.append(f"{tag}: 국·영 {key} 개수가 달라 같은 위치를 뺄 수 없다 — 반려")
                continue
            left = len(ko_list) - 1
            if key == "key_facts" and left < 1:
                problems.append(f"{tag}: 핵심 사실이 하나뿐이라 뺄 수 없다(빈 key_facts 는 조립 거부) — 반려")
                continue
            if key == "checks" and not (_CHECKS_RANGE[0] <= left <= _CHECKS_RANGE[1]):
                problems.append(f"{tag}: 빼면 점검이 {left}개 — 2~3개여야 한다(전부 비우려면 칸 `checks`)")
                continue
            del ko_list[i]
            if en_list is not None:
                del en_list[i]
            done.append(tag)
        else:
            problems.append(f"{tag}: 비울 수 없는 칸(필수 칸 — 그 카드는 반려다)")
    return problems, done


def verify_blanks(brief, requests):
    """다시 돌린 조립본에서 칸 삭제가 반영됐는지(문제 줄, 순수)."""
    problems = []
    by_id = {c.get("id"): c for c in brief.get("cards") or [] if isinstance(c, dict)}
    for cid, slot, _why in requests:
        card = by_id.get(cid)
        if card is None:
            continue          # 채택 밖·합쳐진 카드 — 칸 삭제와 무관하게 발행본에 없다
        en = card.get("en") if isinstance(card.get("en"), dict) else {}
        if slot.startswith("deep.") and not card.get("deep_analysis"):
            problems.append(f"{cid}.{slot}: 심층을 비운 뒤 심층 게이트를 못 넘어 심층이 통째로 빠졌다 — 그 카드는 반려")
        elif slot == "hook_line" and (card.get("hook_line") or en.get("hook_line")):
            problems.append(f"{cid}.hook_line: 조립본에 아직 있다")
        elif slot == "implication" and (card.get("implication") or en.get("implication")):
            problems.append(f"{cid}.implication: 조립본에 아직 있다")
    return problems


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("copyedit")
    a.add_argument("delta_in")
    a.add_argument("delta_out")
    a.add_argument("--deep-in")
    a.add_argument("--deep-out")
    v = sub.add_parser("verify")
    v.add_argument("copyedit")
    v.add_argument("brief")
    args = ap.parse_args(argv)
    try:
        copyedit = _load(args.copyedit)
        if args.cmd == "verify":
            brief = _load(args.brief)
        else:
            delta = _load(args.delta_in)
            deep = _load(args.deep_in) if args.deep_in else None
    except (OSError, ValueError) as e:
        print(f"입력 오류: {e}")
        return 2
    requests = blank_requests(copyedit if isinstance(copyedit, dict) else {})
    if args.cmd == "verify":
        problems = verify_blanks(brief if isinstance(brief, dict) else {}, requests)
        for p in problems:
            print(f"문제: {p}")
        print(f"칸 삭제 확인 {len(requests)}건 · 문제 {len(problems)}건")
        return 1 if problems else 0
    if not requests:
        print("칸 삭제 0건 — 쓰지 않았다")
        return 0
    if any(s.startswith("deep.") for _, s, _ in requests) and not (args.deep_in and args.deep_out):
        print("입력 오류: 심층 칸 삭제가 있는데 --deep-in/--deep-out 이 없다")
        return 2
    problems, done = apply_blanks(delta, deep, requests)
    for p in problems:
        print(f"문제: {p}")
    if problems:
        print(f"칸 삭제 {len(requests)}건 중 문제 {len(problems)}건 — 아무것도 쓰지 않았다")
        return 1
    _dump(delta, args.delta_out)
    if args.deep_out and deep is not None:
        _dump(deep, args.deep_out)
    for d in done:
        print(f"비움: {d}")
    print(f"칸 삭제 {len(done)}건 반영")
    return 0


if __name__ == "__main__":
    sys.exit(main())
