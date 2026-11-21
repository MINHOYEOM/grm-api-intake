# -*- coding: utf-8 -*-
"""교열 전수 대조 — 카드마다 원문과 맞춰 본 근거를 코드가 확인한다(리허설 1121b).

리허설 1121b 의 교열은 153장을 4.5분에 `reject 0` 으로 냈는데, 스스로 "카드별 전수 정독은 아님(표본)" 이라 적었다
(원문 대조 17장). `copyedit.json` 의 카드 줄은 `{"verdict": "pass", "fails": []}` 뿐이라 읽었는지 아무도 알 수 없었다.
1121a 는 같은 153장을 16분에 전부 읽고 실제 정확성 오류를 찾았다. 그래서 카드마다 **맞춰 본 사실과 원문 조각**을 적게 하고,
그 조각이 정말 그 카드의 원문에 있는지 코드가 본다 — 조각을 지어내면 걸리고, 원문을 펴 보지 않으면 적을 수 없다.

    python company/editorial/copyedit_evidence.py show <실행 폴더> <handoff 파일> [--size 20] [--batch K] [--full] [--scaffold <파일>]
        카드를 K번째 묶음(1부터)씩 **카드 문장 + 그 카드의 원문**으로 나란히 낸다. `--batch` 를 빼면 묶음 목록만 낸다.
        원문 = handoff 행(`prose_input`·`body`·`raw_excerpt`·`quotes`)과 스캐폴드 카드(`facts`·483 관찰 `deterministic_detail`).
        심층 원문 전문(`deep_analysis_input.body_full`)은 길어서 `--full` 일 때만 낸다(근거 조각은 전문에서 따도 된다).

    python company/editorial/copyedit_evidence.py check <실행 폴더> <handoff 파일> [--copyedit <파일>] [--scaffold <파일>]
        `delta.json` 의 카드 전부가 `copyedit.json` 에 한 줄씩 있고, 줄마다 아래 근거(`checked`)가 맞는지 본다.
        하나라도 아니면 카드마다 한 줄씩 내고 exit 1 — 교열은 `완료` 를 쓰지 않고 빠진 카드를 마저 읽는다.

    python company/editorial/copyedit_evidence.py merge <copyedit.json> <조각 파일>...
        서브 에이전트가 나눠 쓴 조각(`{"cards": [...]}`)의 카드 줄을 `copyedit.json` 의 `cards` 로 합치고 `counts` 를 다시 센다
        (델타 순서 · 같은 카드가 두 조각에 있으면 exit 1). 다른 키(`status`·`code_gate` 등)는 건드리지 않는다.

근거 꼴 — `cards[]` 의 각 줄에 `checked` 를 더한다:
    {"web_card_id": "...", "verdict": "pass", "fails": [], "notes": [],
     "checked": [{"slot": "summary", "claim": "<카드 문장에서 글자 그대로 뗀 사실>", "source": "<그 카드 원문에서 글자 그대로 뗀 조각>"},
                 {"slot": "hook_line", "claim": "...", "absent": true}]}
  - `claim` 은 그 카드(국·영 슬롯 또는 심층 칸)에 글자 그대로 있어야 한다. `source` 는 그 카드 원문에 글자 그대로 있어야 한다
    (공백·줄바꿈·대소문자·따옴표 꼴은 무시하고 맞춘다). `source` 는 공백 빼고 8자 이상 400자 이하.
  - 원문에서 못 찾은 사실은 `"absent": true`(조각 없음)로 적는다 — 근거 수에 세지 않는다. 그 카드가 `pass` 면 같은 칸의
    `칸 삭제` 주의(`notes`)가 있어야 한다(없으면 원문에 없는 말을 통과시킨 것이다 — `reject` 로 바꾼다).
  - 카드마다: 서로 다른 원문 조각으로 맞춘 근거 2개 이상 · 앞면 칸(`title_issue`·`summary`·`key_facts`·`implication`·`checks`) 1개 이상 ·
    한국어 `hook_line` 이 있으면 `hook_line` 1개 · 심층(`deep_delta.json`)이 있는 카드는 `deep.<키>` 1개. `en.` 접두·`[i]` 는 같은 칸으로 본다.
  - `칸 삭제`·`심층 삭제` 주의가 가리키는 칸의 근거는 그 칸이 빠진 뒤(`blank_slots.py apply`) 카드에 없어도 실패로 보지 않는다.
    심층 삭제 카드는 `deep.<키>` 근거 요구도 없다(심층이 통째로 빠질 수 있다).
  - 원문이 정말 없는 카드(handoff·스캐폴드 어디에도 본문이 없다)는 `checked` 대신 `"source_absent": "<사유>"` — 코드가 원문이 있다고 보면 실패다.
  - `reject` 카드는 `fails` 가 비어 있으면 안 된다.

저장소 루트에서 돌린다. 네트워크·DB·Notion 을 쓰지 않는다. 읽을 수 없으면 exit 2.
"""
import argparse
import json
import os
import re
import sys
import unicodedata

MIN_CHECKS = 2
MIN_SOURCE = 8
MAX_SOURCE = 400
MAX_CLAIM = 300
FRONT_SLOTS = ("title_issue", "summary", "key_facts", "implication", "checks")
# handoff 행에서 원문으로 보는 칸(카드 틀·검색어·메타는 뺀다 — 근거 조각이 본문에서 나오게).
ROW_SOURCE_KEYS = ("prose_input", "body", "raw_excerpt", "evidence_candidate", "title", "headline", "firm",
                   "date", "official_url", "source_url", "deep_analysis_input")
SCAFFOLD_SOURCE_KEYS = ("facts", "quotes", "deterministic_detail", "merged_items", "headline_target")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
                         "—": "-", " ": " ", "·": "·"})
_WS = re.compile(r"\s+")


def norm(text):
    """맞춰 보기용 — NFKC · 소문자 · 따옴표 꼴 통일 · 공백 전부 제거."""
    return _WS.sub("", unicodedata.normalize("NFKC", str(text)).translate(_QUOTES).casefold())


def _strings(obj):
    if isinstance(obj, str):
        if obj.strip():
            yield obj
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield str(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_opt(path):
    try:
        return _load(path)
    except (OSError, ValueError):
        return None


def deep_items(deep):
    if not isinstance(deep, dict):
        return {}
    inner = deep.get("items")
    return inner if isinstance(inner, dict) else {k: v for k, v in deep.items() if isinstance(v, dict)}


def handoff_rows(handoff):
    rows = (handoff or {}).get("rows") if isinstance(handoff, dict) else None
    return {r.get("web_card_id"): r for r in rows or [] if isinstance(r, dict) and r.get("web_card_id")}


def scaffold_cards(scaffold):
    cards = (scaffold or {}).get("cards") if isinstance(scaffold, dict) else None
    return {c.get("id"): c for c in cards or [] if isinstance(c, dict) and c.get("id")}


def find_scaffold(folder, handoff, explicit=None):
    """스캐폴드 사본: 지정 → 실행 폴더 `scaffold.json`(마감·리허설) → `_scaffold/brief_web_<날짜>.json`(교열 1번)."""
    if explicit:
        return explicit
    cand = [os.path.join(folder, "scaffold.json")]
    date = str((handoff or {}).get("run_date_kst") or "") if isinstance(handoff, dict) else ""
    if date:
        cand.append(os.path.join("_scaffold", f"brief_web_{date.replace('-', '_')}.json"))
    return next((p for p in cand if os.path.isfile(p)), None)


def source_parts(row, sc_card):
    """[(이름, 값)] — 그 카드의 원문 조각들(handoff 행 + 스캐폴드 카드)."""
    out = []
    for k in ROW_SOURCE_KEYS:
        v = (row or {}).get(k)
        if v not in (None, "", [], {}):
            out.append((f"handoff.{k}", v))
    for k in SCAFFOLD_SOURCE_KEYS:
        v = (sc_card or {}).get(k)
        if v not in (None, "", [], {}):
            out.append((f"scaffold.{k}", v))
    return out


def has_body(row, sc_card):
    """원문 본문이 있는가 — 제목·업체·날짜 같은 메타만 있으면 없는 것이다."""
    row = row or {}
    pi = row.get("prose_input") if isinstance(row.get("prose_input"), dict) else {}
    dai = row.get("deep_analysis_input") if isinstance(row.get("deep_analysis_input"), dict) else {}
    bodies = [row.get("body"), row.get("raw_excerpt"), pi.get("body_excerpt"), pi.get("issue_or_reason"),
              pi.get("quote_lines"), pi.get("w2_facts"), dai.get("body_full"),
              (sc_card or {}).get("deterministic_detail"), (sc_card or {}).get("quotes")]
    return any(any(s.strip() for s in _strings(b)) for b in bodies)


def card_text(card, deep_entry):
    return norm(" ".join(list(_strings(card)) + list(_strings(deep_entry))))


def slot_base(slot):
    s = str(slot or "").strip()
    if s.startswith("deep."):
        return "deep"
    if s.startswith("en."):
        s = s[3:]
    return re.split(r"[\[.]", s, maxsplit=1)[0]


def _blanked_slots(entry):
    """칸 삭제·심층 삭제 주의가 가리키는 칸(`blank_slots.py` 가 빼는 칸)."""
    out = set()
    for n in entry.get("notes") or []:
        item = str(n.get("item") or "") if isinstance(n, dict) else ""
        if "칸 삭제" in item or "심층 삭제" in item:
            out.add(str(n.get("slot") or "").strip())
    return out


def _deep_removed(entry):
    """심층 삭제 주의가 있는 카드 — 심층분석이 통째로 빠질 수 있다(`blank_slots.py`)."""
    return any(isinstance(n, dict) and "심층 삭제" in str(n.get("item") or "") for n in entry.get("notes") or [])


def _covered(slot, blanked):
    return any(b and (b == slot or slot.startswith(b) or b.startswith(slot)) for b in blanked)


def card_problems(cid, entry, card, deep_entry, row, sc_card):
    """한 카드의 근거 문제 — 문자열 목록(빈 목록 = 통과)."""
    probs = []
    verdict = entry.get("verdict")
    if verdict not in ("pass", "reject"):
        probs.append(f"verdict={verdict!r} (pass·reject 만)")
    if verdict == "reject" and not entry.get("fails"):
        probs.append("reject 인데 fails 가 비었다")
    body = has_body(row, sc_card)
    if entry.get("source_absent"):
        if body:
            probs.append("source_absent 인데 원문이 있다(handoff·스캐폴드) — 원문과 맞춰 checked 를 적는다")
        return probs
    if not body:
        probs.append("원문이 없는 카드 — checked 대신 source_absent 에 사유를 적는다")
        return probs
    checked = entry.get("checked")
    if not isinstance(checked, list) or not checked:
        return probs + ["checked 없음 — 원문과 맞춰 본 근거가 없다(읽지 않은 카드)"]
    ctext = card_text(card, deep_entry)
    stext = norm(" ".join(_strings([v for _, v in source_parts(row, sc_card)])))
    blanked = _blanked_slots(entry)
    deep_removed = _deep_removed(entry)
    seen_sources, bases = set(), set()
    for i, c in enumerate(checked):
        tag = f"checked[{i}]"
        if not isinstance(c, dict):
            probs.append(f"{tag}: 객체가 아니다")
            continue
        slot, claim = str(c.get("slot") or "").strip(), str(c.get("claim") or "")
        if not slot:
            probs.append(f"{tag}: slot 없음")
            continue
        nclaim = norm(claim)
        if len(nclaim) < 2 or len(claim) > MAX_CLAIM:
            probs.append(f"{tag} {slot}: claim 길이(공백 빼고 2자 이상 · {MAX_CLAIM}자 이하)")
            continue
        if nclaim not in ctext:
            # 칸 삭제·심층 삭제가 이미 반영된 뒤면 그 칸의 문장은 카드에 없다 — 뺀 칸을 가리키는 근거는 세지도 막지도 않는다.
            if _covered(slot, blanked) or (deep_removed and slot.startswith("deep.")):
                continue
            probs.append(f"{tag} {slot}: claim 이 카드 문장에 없다 — {claim[:40]!r}")
            continue
        if c.get("absent") is True:
            if verdict == "pass" and not _covered(slot, blanked):
                probs.append(f"{tag} {slot}: 원문에 없는 사실인데 pass — reject 로 바꾸거나(필수 칸) `칸 삭제` 주의를 적는다(선택 칸)")
            continue
        src = str(c.get("source") or "")
        nsrc = norm(src)
        if len(nsrc) < MIN_SOURCE or len(src) > MAX_SOURCE:
            probs.append(f"{tag} {slot}: source 길이(공백 빼고 {MIN_SOURCE}자 이상 · {MAX_SOURCE}자 이하)")
            continue
        if nsrc not in stext:
            probs.append(f"{tag} {slot}: source 가 이 카드 원문에 없다 — {src[:40]!r}")
            continue
        if nsrc in seen_sources:
            continue
        seen_sources.add(nsrc)
        bases.add(slot_base(slot))
    if len(seen_sources) < MIN_CHECKS:
        probs.append(f"원문 조각으로 맞춘 근거 {len(seen_sources)}개 — 서로 다른 조각 {MIN_CHECKS}개 이상")
    if not bases & set(FRONT_SLOTS):
        probs.append("앞면 칸(title_issue·summary·key_facts·implication·checks) 근거 없음")
    if str((card or {}).get("hook_line") or "").strip() and "hook_line" not in bases:
        probs.append("hook_line 이 있는데 hook_line 근거 없음(숫자·사실 원문 대조 — 2-1 ③)")
    if deep_entry and "deep" not in bases and not deep_removed:
        # 심층 삭제 카드는 교열이 심층을 읽고 반려 근거를 냈다(그 칸이 빠졌다) — 남은 심층 근거를 따로 요구하지 않는다.
        probs.append("심층이 있는데 deep.<키> 근거 없음")
    return probs


def check(delta, copyedit, deep=None, handoff=None, scaffold=None):
    """(문제 줄 목록, 요약 dict). 문제 줄이 비어 있으면 통과."""
    lines = []
    cards = (delta or {}).get("cards") if isinstance(delta, dict) else None
    if not isinstance(cards, dict) or not cards:
        return ["delta.json 에 카드가 없다"], {"cards": 0, "ok": 0, "bad": 0}
    entries = (copyedit or {}).get("cards") if isinstance(copyedit, dict) else None
    if not isinstance(entries, list):
        return ["copyedit.json 에 cards 목록이 없다"], {"cards": len(cards), "ok": 0, "bad": len(cards)}
    by_id, dup = {}, []
    for e in entries:
        cid = e.get("web_card_id") if isinstance(e, dict) else None
        if cid in by_id:
            dup.append(cid)
        by_id[cid] = e
    extra = [c for c in by_id if c not in cards]
    missing = [c for c in cards if c not in by_id]
    if dup:
        lines.append(f"카드 줄 중복 {len(dup)}: {', '.join(map(str, dup[:10]))}")
    if extra:
        lines.append(f"델타에 없는 카드 줄 {len(extra)}: {', '.join(map(str, extra[:10]))}")
    if missing:
        lines.append(f"판정 줄이 없는 카드 {len(missing)}: {', '.join(missing[:10])}" + (" …" if len(missing) > 10 else ""))
    rows, scs, dps = handoff_rows(handoff), scaffold_cards(scaffold), deep_items(deep)
    ok = bad = 0
    for cid, card in cards.items():
        if cid not in by_id:
            bad += 1
            continue
        probs = card_problems(cid, by_id[cid], card, dps.get(cid), rows.get(cid), scs.get(cid))
        if probs:
            bad += 1
            lines.append(f"{cid}: " + " · ".join(probs))
        else:
            ok += 1
    return lines, {"cards": len(cards), "ok": ok, "bad": bad, "missing": len(missing), "extra": len(extra)}


def summary_line(stats):
    return (f"교열 전수 대조: 카드 {stats['cards']}장 중 근거 충족 {stats['ok']}장 · 미충족 {stats['bad']}장"
            f" (판정 줄 없음 {stats.get('missing', 0)} · 델타 밖 {stats.get('extra', 0)})")


def check_folder(folder, handoff_path, copyedit_path=None, scaffold_path=None):
    handoff = _load_opt(handoff_path)
    delta = _load_opt(os.path.join(folder, "delta.json"))
    copyedit = _load_opt(copyedit_path or os.path.join(folder, "copyedit.json"))
    deep = _load_opt(os.path.join(folder, "deep_delta.json"))
    sp = find_scaffold(folder, handoff, scaffold_path)
    scaffold = _load_opt(sp) if sp else None
    lines, stats = check(delta, copyedit, deep, handoff, scaffold)
    if not isinstance(handoff, dict):
        lines.insert(0, f"handoff 를 읽지 못했다: {handoff_path}")
    return lines, stats


def recount(copyedit):
    cards = [c for c in copyedit.get("cards") or [] if isinstance(c, dict)]
    counts = dict(copyedit.get("counts") or {})
    counts.update({"cards": len(cards),
                   "pass": sum(c.get("verdict") == "pass" for c in cards),
                   "reject": sum(c.get("verdict") == "reject" for c in cards),
                   "fails_total": sum(len(c.get("fails") or []) for c in cards),
                   "notes_total": sum(len(c.get("notes") or []) for c in cards)})
    copyedit["counts"] = counts
    return copyedit


def merge(copyedit_path, part_paths, delta_path=None):
    """조각 파일의 카드 줄을 합친다 — (exit, 메시지)."""
    base = _load_opt(copyedit_path) or {}
    got, dup = {}, []
    for p in part_paths:
        part = _load(p)
        items = part.get("cards") if isinstance(part, dict) else part
        for e in items or []:
            cid = e.get("web_card_id") if isinstance(e, dict) else None
            if not cid:
                continue
            if cid in got:
                dup.append(cid)
            got[cid] = e
    if dup:
        return 1, f"같은 카드가 두 조각에 있다: {', '.join(dup[:10])}"
    delta = _load_opt(delta_path or os.path.join(os.path.dirname(copyedit_path) or ".", "delta.json"))
    order = list(((delta or {}).get("cards") or {}).keys())
    rank = {cid: i for i, cid in enumerate(order)}
    base["cards"] = sorted(got.values(), key=lambda e: rank.get(e["web_card_id"], len(rank)))
    recount(base)
    with open(copyedit_path, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return 0, f"카드 줄 {len(base['cards'])}개 합침 → {copyedit_path} (counts {base['counts']})"


def _flat(prefix, obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flat(f"{prefix}.{k}" if prefix else k, v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flat(f"{prefix}[{i}]", v, out)
    elif isinstance(obj, str) and obj.strip():
        out.append((prefix, obj))


def _compact(v, drop=("body_full",)):
    if isinstance(v, dict):
        return {k: _compact(x) for k, x in v.items() if k not in drop and x not in (None, "", [], {})}
    if isinstance(v, list):
        return [_compact(x) for x in v]
    return v


def show(folder, handoff_path, size=20, batch=None, full=False, scaffold_path=None):
    handoff = _load(handoff_path)
    delta = _load(os.path.join(folder, "delta.json"))
    deep = deep_items(_load_opt(os.path.join(folder, "deep_delta.json")))
    sp = find_scaffold(folder, handoff, scaffold_path)
    scs = scaffold_cards(_load_opt(sp) if sp else None)
    rows = handoff_rows(handoff)
    ids = list(delta.get("cards") or {})
    groups = [ids[i:i + size] for i in range(0, len(ids), size)]
    out = []
    if not batch:
        out.append(f"카드 {len(ids)}장 · 묶음 {len(groups)}개(묶음당 {size}장) · 스캐폴드 {sp or '없음(handoff 만)'}")
        for k, g in enumerate(groups, 1):
            out.append(f"  묶음 {k}: {g[0]} … {g[-1]} ({len(g)}장)")
        return "\n".join(out)
    if batch < 1 or batch > len(groups):
        raise ValueError(f"묶음 번호는 1~{len(groups)}")
    for n, cid in enumerate(groups[batch - 1], (batch - 1) * size + 1):
        row, sc = rows.get(cid) or {}, scs.get(cid) or {}
        out.append(f"=== [{n}/{len(ids)}] {cid} · {sc.get('card_type') or row.get('source') or ''} · "
                   f"{row.get('firm') or sc.get('headline_target') or ''}")
        slots = []
        _flat("", delta["cards"][cid], slots)
        out.append("[카드]")
        out += [f"  {k}: {v}" for k, v in slots]
        if cid in deep:
            dslots = []
            _flat("deep", deep[cid].get("deep_analysis") if isinstance(deep[cid].get("deep_analysis"), dict) else deep[cid], dslots)
            for k in ("observations_ko", "violations_ko"):
                if deep[cid].get(k):
                    _flat(f"deep.{k}", deep[cid][k], dslots)
            out.append("[심층]")
            out += [f"  {k}: {v}" for k, v in dslots]
        parts = source_parts(row, sc)
        out.append("[원문]" if has_body(row, sc) else "[원문] (본문 없음 — source_absent 후보)")
        for name, v in parts:
            if name == "handoff.deep_analysis_input":
                bf = (v or {}).get("body_full") if isinstance(v, dict) else None
                rest = _compact(v)
                if rest:
                    out.append(f"  {name}: {json.dumps(rest, ensure_ascii=False)}")
                if bf:
                    out.append(f"  handoff.deep_analysis_input.body_full: " + (bf if full else f"({len(bf)}자 — --full 로 보기)"))
                continue
            out.append(f"  {name}: {v if isinstance(v, str) else json.dumps(_compact(v), ensure_ascii=False)}")
        out.append("")
    return "\n".join(out)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="copyedit_evidence.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("folder")
    s.add_argument("handoff")
    s.add_argument("--size", type=int, default=20)
    s.add_argument("--batch", type=int)
    s.add_argument("--full", action="store_true")
    s.add_argument("--scaffold")
    c = sub.add_parser("check")
    c.add_argument("folder")
    c.add_argument("handoff")
    c.add_argument("--copyedit")
    c.add_argument("--scaffold")
    m = sub.add_parser("merge")
    m.add_argument("copyedit")
    m.add_argument("parts", nargs="+")
    m.add_argument("--delta")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "show":
            print(show(args.folder, args.handoff, args.size, args.batch, args.full, args.scaffold))
            return 0
        if args.cmd == "merge":
            code, msg = merge(args.copyedit, args.parts, args.delta)
            print(msg, file=sys.stderr if code else sys.stdout)
            return code
        if not os.path.isfile(os.path.join(args.folder, "delta.json")):
            print(f"delta.json 없음: {args.folder}", file=sys.stderr)
            return 2
        lines, stats = check_folder(args.folder, args.handoff, args.copyedit, args.scaffold)
        for line in lines:
            print(line)
        print(summary_line(stats))
        return 1 if lines else 0
    except (OSError, ValueError) as e:
        print(f"읽기 실패: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
