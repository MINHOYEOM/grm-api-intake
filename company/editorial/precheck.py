# -*- coding: utf-8 -*-
"""편집팀 사전 점검 — 교열이 잡기 전에 코드가 셀 수 있는 것을 먼저 센다.

    python company/editorial/precheck.py terms <delta 파일>
        델타(샤드 `delta.A.json` 또는 합친 `delta.json`)의 한국어 산문을 조립 표현 검사
        (`inject_slots.lint_prose_style`)와 **같은 규칙**으로 본다 — 금지 표현, [용어·표기] 표 왼쪽 표기,
        약어 단독(CAPA·OOS·COA 가 한글 풀이 없이 나온 카드). 걸린 것이 있으면 한 줄씩 내고 exit 1.
        기자는 샤드를 `완료`로 내기 전에 돌려 0 으로 만든다. 길이 상한도 같이 본다(제목 이슈·hook_line).
        **빈 필수 칸과 영문 짝**도 본다 — 합치기(`merge_parts.py`)가 호 전체를 거부하는 것과 같은 규칙이다:
        한국어 `title_issue`·`summary`·`key_facts` 는 반드시 채우고, 영문 `en` 은 그 셋 + 한국어가 채운 `implication`·`checks` 를 채운다.
        단 `규제 소식`·`지침·안내서`·`고시·개정법령` 카드는 원문에 머리말 사실이 없으면 `key_facts` 를 비워도 된다(2026-11-16).
        카드 유형은 그 주 handoff(`_handoff/handoff_<날짜>.json`, 날짜 = 델타 폴더 이름)에서 읽는다 — `--handoff` 로 직접 줄 수도 있다.
        handoff 를 못 찾으면 종전처럼 전부 필수다.
        **조립 코드 검사도 같은 함수로 돌린다**(`inject_slots.validate_injection` — 개수·길이·마크업·영문 짝·타입 등
        조립이 막는 것 전부. 리허설 1118). 스캐폴드 사본(`_scaffold/brief_web_<날짜>.json`, `--scaffold`)이 있으면 인용 번역 대조까지 본다.
        `--deep <심층 델타·샤드>` 를 주면 심층 칸 산문도 본다. 심층 칸 검사와 행정처분 요약 주어 검사는 **경고만** 낸다
        (`경고:` 로 시작하는 줄 — exit 코드에 들어가지 않는다. 처음 2개 호 실측 뒤 차단 여부를 사람이 정한다).

    python company/editorial/precheck.py deep-source <handoff 파일> <deep_delta 파일> --out <검사용 파일>
        심층분석 델타의 각 카드에 그 주 handoff 의 `deep_analysis_input.body_full` 을 `source_text` 로 붙인
        **검사용 사본**을 쓴다. 발행 브릿지가 조립 직전에 하는 일과 같다(`delta_bridge.apply_handoff_source_text`).
        교열의 코드 검사는 이 사본을 `--deep` 에 넘긴다 — 붙이지 않으면 근거 대조가 전부 '원문에 없음'이 된다.
        브릿지의 **중첩 방어**도 같이 한다: 국문 병기(`observations_ko`·`violations_ko`)가 `deep_analysis` 안에 들어 있으면
        카드 층으로 끌어올린다(안 올리면 조립이 번역을 못 보고 483 관찰 국문 없음으로 호 전체를 거부한다 — 리허설 1114b).
        사본은 실행 폴더에 두지 않고 커밋하지 않는다(반납하는 `deep_delta.json` 은 그대로).

    python company/editorial/precheck.py deep <handoff 파일> <심층 샤드 deep.S.json>
        심층 기자가 샤드를 `완료`로 내기 전에 돌린다. FDA 483 카드마다 `observations_ko` 가 **카드 층**
        (`items.<id>.observations_ko` — `deep_analysis` 안이 아니다)에 있고 항목마다 `number`·`deficiency_ko` 가 있는지,
        국문 병기가 `deep_analysis` 안에 잘못 들어가 있지 않은지 본다. 걸리면 한 줄씩 내고 exit 1.

    python company/editorial/precheck.py rework-diff <1차 delta.json> <2차 delta.json> <1차 copyedit.json>
        재작업(반려 카드만 한 번 고치기) 뒤에 다시 합친 델타가 **반려 카드만** 바뀌었는지 본다.
        카드가 늘거나 줄었거나, 반려되지 않은 카드·`tldr` 가 한 글자라도 바뀌었으면 exit 1 — 교열은 판정하지 않고 보류한다.
        통과면 반려 카드마다 바뀐 슬롯을 한 줄씩 낸다(안 바뀐 반려 카드도 적는다 — 다시 반려될 카드다).

저장소 루트에서 돌린다. 읽을 수 없으면 exit 2(메시지는 stderr).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inject_slots  # noqa: E402
import merge_parts  # noqa: E402

KO_REQUIRED = tuple(f for f in merge_parts.EN_FIELDS if f not in merge_parts.EN_OPTIONAL)   # 유형을 모를 때


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def warn_lines(delta, deep=None):
    """경고만 내는 줄(순수) — 행정처분 요약 주어(⑤)와 심층 칸 산문(④). exit 코드에 들어가지 않는다."""
    out = inject_slots.lint_prose_style(delta).warn_lines()
    if deep is not None:
        out += inject_slots.lint_deep_prose_style(deep).warn_lines()
    return out


def term_lines(delta, card_types=None):
    """표현 검사 줄 + 길이 상한 위반 줄(순수). `card_types` = {카드 id: 카드 유형}(선택)."""
    card_types = card_types or {}
    out = [line for line in inject_slots.lint_prose_style(delta).lines() if not line.startswith("시사점 맺음 반복")]
    hook = inject_slots._HOOK_LINE_MAX
    title = inject_slots._MAX_TITLE_ISSUE
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    for cid, card in cards.items():
        if not isinstance(card, dict):
            continue
        en = card.get("en") if isinstance(card.get("en"), dict) else {}
        ctype = card_types.get(cid)
        ko_gaps = [f for f in merge_parts.ko_required(ctype) if not merge_parts._filled(card.get(f))]
        if ko_gaps:
            out.append(f"빈 필수 칸 {cid}: {', '.join(ko_gaps)}")
        en_gaps = merge_parts._en_gaps(card, ctype)
        if en_gaps:
            out.append(f"영문 슬롯 미완결 {cid}: en.{', en.'.join(en_gaps)}")
        for label, text, limit in (
                ("title_issue", card.get("title_issue"), title),
                ("en.title_issue", en.get("title_issue"), title * 2),
                ("hook_line", card.get("hook_line"), hook["ko"]),
                ("en.hook_line", en.get("hook_line"), hook["en"])):
            if isinstance(text, str) and len(text) > limit:
                out.append(f"길이 {cid}.{label}: {len(text)}자 — {limit}자 이내")
    return out


def gate_lines(delta, card_types=None, scaffold=None):
    """조립 코드 검사(`inject_slots.validate_injection`)를 **그 함수 그대로** 샤드에 돌린 오류 줄(순수).

    ★리허설 1118: 조립 검사는 key_facts ≤4 를 막는데 사전 점검은 그 규칙을 따로 베껴 두지 않아,
      5개짜리 카드 6장이 기자 단계를 통과하고 교열에서 호 전체를 막았다. 규칙을 베끼면 다음 규칙에서 또 어긋나므로
      조립과 **같은 함수**를 부른다 — 조립에 가드가 늘면 사전 점검도 저절로 는다.
    `scaffold`(그 주 스캐폴드)가 있으면 실제 카드(인용·유형)로 본다. 없으면 델타 카드 id·유형으로 최소 카드를 만들어 보고,
    스캐폴드가 있어야만 맞게 셀 수 있는 인용 번역 대조(quotes_translation)는 건너뛴다(거짓 차단 방지)."""
    card_types = card_types or {}
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    ids = set(cards)
    if isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list):
        brief_cards = [c for c in scaffold["cards"] if isinstance(c, dict) and c.get("id") in ids]
        known = {c.get("id") for c in brief_cards}
        brief_cards += [{"id": cid, "card_type": card_types.get(cid)} for cid in sorted(ids - known)]
        skip_quotes = bool(ids - known)
    else:
        brief_cards = [{"id": cid, "card_type": card_types.get(cid)} for cid in sorted(ids)]
        skip_quotes = True
    report = inject_slots.validate_injection({"cards": brief_cards}, delta)
    return [f"조립 검사 {e}" for e in report.errors
            if not (skip_quotes and "quotes_translation" in e)]


def assembly_lines(delta, scaffold):
    """**조립 함수 전체**(`assemble_publish_brief.assemble_publish_brief`, strict=False)를 이 델타의 카드만으로 돌린
    차단 오류 줄(순수). 슬롯 주입 검증 뒤의 발행본 관문 — 원문 부재 단정(게이트 3)·가림 표기(게이트 8)·
    빈 슬롯·미확인 부재 라벨 등 — 까지 조립과 같은 코드로 본다.

    ★리허설 1120: 슬롯 주입 검증(gate_lines)은 통과했는데 발행본 관문 '원문을 확보했는데 implication 가 부재를 주장'
      이 호 전체를 막았다. 관문을 하나씩 따라가지 않고 조립 함수 자체를 부른다 — 관문이 늘면 저절로 따라온다.
    빼는 것은 **심층 샤드 몫**인 국문 병기 관문(483 관찰·WL 위반·WHOPIR)뿐이다 — 델타 샤드에는 그 재료가 없다.
    스캐폴드가 없으면 빈 목록(이 검사는 실제 카드가 있어야 맞게 센다)."""
    if not (isinstance(scaffold, dict) and isinstance(scaffold.get("cards"), list)):
        return []
    import copy
    import assemble_publish_brief as apb
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    sub = copy.deepcopy(scaffold)
    sub["cards"] = [c for c in sub["cards"] if isinstance(c, dict) and c.get("id") in cards]
    try:
        out, report = apb.assemble_publish_brief(sub, {"cards": cards}, strict=False)
    except (apb.AssembleError, inject_slots.SlotInjectionError, ValueError, TypeError, KeyError) as e:
        return [f"조립 검사 조립 자체가 멈춤 — {type(e).__name__}: {e}"]
    out_cards = out.get("cards") or []
    deep_only = set(apb._lint_483_observation_ko(out_cards, report.refreshed_483_ids))
    deep_only |= set(apb._lint_wl_violation_ko(out_cards)) | set(apb._lint_whopir_ko(out_cards))
    return [f"조립 검사 {e}" for e in report.errors if e not in deep_only]


def find_scaffold(delta_path, explicit=None):
    """그 주 스캐폴드 사본 경로(`_scaffold/brief_web_<YYYY_MM_DD>.json`). 없으면 None.
    날짜는 델타 폴더의 `desk.json` handoff_ref.date → run_date_kst → 폴더 이름(merge_parts.find_handoff 와 같은 순서)."""
    if explicit:
        return explicit
    folder = os.path.dirname(os.path.abspath(delta_path))
    d = ""
    try:
        desk = _load(os.path.join(folder, "desk.json"))
        if isinstance(desk, dict):
            ref = desk.get("handoff_ref") if isinstance(desk.get("handoff_ref"), dict) else {}
            d = str(ref.get("date") or desk.get("run_date_kst") or "")[:10]
    except (OSError, ValueError):
        pass
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        d = os.path.basename(os.path.normpath(folder))[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return None
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for base in (os.getcwd(), root):
        path = os.path.join(base, "_scaffold", f"brief_web_{d.replace('-', '_')}.json")
        if os.path.exists(path):
            return path
    return None


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


NESTED_TRANSLATION_KEYS = ("observations_ko", "violations_ko")   # delta_bridge 의 중첩 예치 방어와 같은 키


def lift_nested(entry):
    """`deep_analysis` 안에 실린 국문 병기를 카드 층으로 끌어올린 사본(순수). 브릿지 게이트와 같은 규칙."""
    da = entry.get("deep_analysis") if isinstance(entry, dict) else None
    if not isinstance(da, dict) or not any(k in da for k in NESTED_TRANSLATION_KEYS):
        return entry
    da = dict(da)
    entry = dict(entry, deep_analysis=da)
    for key in NESTED_TRANSLATION_KEYS:
        nested = da.pop(key, None)
        if nested and key not in entry:
            entry[key] = nested
    return entry


def with_source_text(deep, bodies):
    """deep 델타 사본에 `source_text` 를 붙이고 중첩된 국문 병기를 끌어올린다(순수). handoff 에 원문이 없는 카드는 원문만 그대로 둔다."""
    out, filled = {}, 0
    for cid, entry in deep.items():
        if isinstance(entry, dict):
            entry = lift_nested(entry)
            if bodies.get(cid):
                entry = dict(entry, source_text=bodies[cid])
                filled += 1
        out[cid] = entry
    return out, filled


def fda483_ids(payload):
    """handoff payload → 심층 대상 FDA 483 카드 id 집합."""
    return {row["web_card_id"] for row in payload.get("rows") or []
            if isinstance(row, dict) and isinstance(row.get("web_card_id"), str)
            and row.get("kind") == "fda-483"}


def deep_lines(shard, ids_483):
    """심층 샤드의 국문 병기 자리·결손 줄(순수)."""
    out = []
    items = shard.get("items") if isinstance(shard.get("items"), dict) else {}
    for cid, entry in items.items():
        if not isinstance(entry, dict):
            out.append(f"항목이 객체가 아니다: {cid}")
            continue
        da = entry.get("deep_analysis") if isinstance(entry.get("deep_analysis"), dict) else {}
        for key in NESTED_TRANSLATION_KEYS:
            if key in da:
                out.append(f"자리 틀림 {cid}: {key} 가 deep_analysis 안에 있다 — items.{cid}.{key} 로 옮긴다")
        if cid not in ids_483:
            continue
        obs = entry.get("observations_ko")
        if not isinstance(obs, list) or not obs:
            if "observations_ko" not in da:
                out.append(f"483 관찰 국문 없음 {cid}: observations_ko(★필수)")
            continue
        for i, o in enumerate(obs):
            if not (isinstance(o, dict) and str(o.get("number") or "").strip()
                    and str(o.get("deficiency_ko") or "").strip()):
                out.append(f"483 관찰 국문 불완전 {cid}: observations_ko[{i}] 에 number·deficiency_ko 가 있어야 한다")
    return out


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
    t.add_argument("--handoff", help="카드 유형을 읽을 handoff 파일(없으면 델타 폴더 날짜로 찾는다)")
    t.add_argument("--deep", help="심층 델타·샤드 — 심층 칸 산문을 경고로 본다")
    t.add_argument("--scaffold", help="그 주 스캐폴드 사본(없으면 _scaffold/brief_web_<날짜>.json 을 찾는다)")
    d = sub.add_parser("deep-source")
    d.add_argument("handoff")
    d.add_argument("deep")
    d.add_argument("--out", required=True)
    p = sub.add_parser("deep")
    p.add_argument("handoff")
    p.add_argument("shard")
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
            if args.handoff:
                card_types = merge_parts.handoff_card_types(_load(args.handoff))
            else:
                card_types = merge_parts.run_card_types(os.path.dirname(os.path.abspath(args.delta)))
            sc_path = find_scaffold(args.delta, args.scaffold)
            scaffold = _load(sc_path) if sc_path else None
            lines = term_lines(delta, card_types) + gate_lines(delta, card_types, scaffold)
            lines += [x for x in assembly_lines(delta, scaffold) if x not in lines]
            for line in lines:
                print(line)
            warns = warn_lines(delta, _load(args.deep) if args.deep else None)
            if not sc_path:
                warns.append("스캐폴드 사본(_scaffold/brief_web_<날짜>.json)이 없어 인용 번역 대조는 건너뛰었다 — 교열 코드 검사에서 본다")
            for line in warns:
                print(f"경고: {line}")
            print(f"사전 점검: {len(lines)}건" + ("" if lines else " — 통과")
                  + (f" · 경고 {len(warns)}건(막지 않음)" if warns else ""))
            return 1 if lines else 0
        if args.cmd == "deep":
            handoff, shard = _load(args.handoff), _load(args.shard)
            if not isinstance(handoff, dict) or not isinstance(shard, dict):
                raise ValueError("handoff·심층 샤드가 객체가 아니다")
            lines = deep_lines(shard, fda483_ids(handoff))
            for line in lines:
                print(line)
            warns = inject_slots.lint_deep_prose_style(shard).warn_lines()
            for line in warns:
                print(f"경고: {line}")
            print(f"심층 사전 점검: {len(lines)}건" + ("" if lines else " — 통과")
                  + (f" · 경고 {len(warns)}건(막지 않음)" if warns else ""))
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
