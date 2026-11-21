# -*- coding: utf-8 -*-
"""교열 '칸 삭제'·'심층 삭제' — 정확성 오류가 선택 칸이나 심층에만 있는 카드는 그 부분을 코드가 빼고 통과시킨다.

① 칸 삭제(2026-11-21 대표 결정 · `copyedit.md` 2-3) — 교열이 `copyedit.json` 의 `cards[].notes` 에
   `{"item": "칸 삭제", "slot": "<칸>", "evidence": "<사유>"}` 를 적으면, 이 스크립트가 그 칸을 기계적으로 비운다.
   LLM 이 델타를 옮겨 적거나 고쳐 쓰지 않는다. 비우기만 하므로 새 문장이 생기지 않는다.

   비울 수 있는 칸(조립 `assemble_publish_brief`·`check_en_slots`·심층 게이트가 빈 값으로 통과시키는 것만 — 리허설 1121a 실측):
     hook_line              한국어 `hook_line` 과 `en.hook_line` 을 지운다(선택 필드).
     implication            한국어·영문 시사점을 "" 로(빈 시사점은 경고만 — `_OPTIONAL_PROSE_SLOTS`).
     checks                 한국어·영문 점검 목록을 [] 로(빈 점검은 경고만).
     checks[i]              i 번째 점검을 국·영 같은 위치에서 뺀다. 남는 수가 2~3 이어야 한다(아니면 `checks` 로).
     key_facts[i]           i 번째 핵심 사실을 국·영 같은 위치에서 뺀다. 하나 이상 남아야 한다(빈 key_facts 는 조립 거부).
     deep.key_violations[i] (FDA 483 심층만) i 번째 지적을 뺀다. 하나 이상 남아야 한다.
     deep.inspectional_significance · deep.administrative_risks   (FDA 483 심층만) "" 로 — 483 의 선택 절.
   그 밖의 칸(title_issue·summary·quotes·WL/행정처분 심층 절 등)은 필수라 비울 수 없다 → 그 카드는 반려다.

② 심층 삭제(2026-11-21 대표 결정 — 리허설 1121b: 반려 21장 중 12장이 심층에서만 걸렸다) —
   반려 카드의 `fails` 가 **전부** 심층 칸(`deep.…`)이면 그 카드는 반려가 아니다. `deep-auto` 가 판정을 `pass` 로 바꾸고
   `fails` 를 `notes` `{"item": "심층 삭제", "slot": <그 칸>, "evidence": "<원래 항목>: <원래 근거>"}` 로 옮긴다(교열이 손으로 하지 않는다).
   `apply` 가 칸마다 다음처럼 뺀다 — 칸 삭제와 달리 거부하지 않고, 못 빼면 그 카드의 심층분석을 통째로 뺀다:
     deep.key_violations[i](.…)                i 번째 지적을 뺀다. 남는 지적이 없으면 심층분석 통째.
     deep.required_remediation.items[i]       i 번째 시정 항목을 뺀다. 경고서한·행정처분은 하나 이상 남아야 한다(아니면 통째).
     deep.inspectional_significance · deep.administrative_risks   483 이면 "" 로(선택 절). 경고서한·행정처분은 필수 절 → 통째.
     그 밖의 심층분석 칸(fda_evaluation·disposition_basis·deadline 등 필수 절)   심층분석 통째(`deep_analysis` 키를 지운다 —
                                              카드는 앞면과 원문 블록만으로 발행된다. 조립은 `deep_analysis` 없는 항목을 정상으로 넘긴다).
     deep.observations_ko[i](.칸) · deep.violations_ko[i](.칸) · deep.ncr_ko.<칸>
                                              원문(영문) 블록의 국문 해석이다. 원문·국문 병기 게이트(483 게이트 4·WL 6·WHOPIR 7,
                                              `render.validate_483_observations`)가 국문이 빈 칸을 발행 차단하므로 **빼지 않고**
                                              고정 안내문(`TRANSLATION_NOTICE`)으로 바꾼다 — 화면에는 원문만 읽을 거리로 남는다.
                                              심층분석을 통째로 빼도 이 칸은 남으므로(조립이 따로 병합한다) 따로 바꿔야 한다.
   국·영 짝: 심층(`deep_delta.json`)에는 영문 산문 칸이 없다 — 짝은 원문(영문 결정론 블록)↔국문 해석이고, 원문은 건드리지 않는다.

  python company/editorial/blank_slots.py deep-auto <copyedit.json> [--cap 25]
  python company/editorial/blank_slots.py apply <copyedit.json> <delta 입력> <delta 출력> [--deep-in X --deep-out Y] [--log Z]
  python company/editorial/blank_slots.py verify <copyedit.json> <brief_check.json>

`deep-auto` 는 `copyedit.json` 을 제자리에서 고친다(같은 파일에 다시 돌려도 결과가 같다) — 남은 반려 수와 재작업 상한을 낸다.
`apply` 는 입력을 읽고 출력에 쓴다 — 같은 입력으로 다시 돌려도 결과가 같다(입력은 칸 삭제 전 사본이어야 한다).
`--log` 를 주면 뺀 것을 카드·칸·처리·사유로 JSON 에 남긴다(발행본 감사용 — 실행 폴더 `slot_removals.json` 으로 커밋한다).
`verify` 는 다시 돌린 조립본에서 비운 칸이 비었는지, 칸 삭제로 심층을 건드린 카드가 심층을 잃지 않았는지 본다
(심층 게이트 실패는 조립을 막지 않고 심층만 조용히 빠지므로 exit 0 만으로는 모른다). 심층 삭제 카드는 심층이 빠져도 된다(한 줄로 알린다).
exit 0 = 통과 · 1 = 문제(아무것도 쓰지 않는다) · 2 = 입력 오류.
"""
import argparse
import json
import re
import sys

ITEM = "칸 삭제"
DEEP_ITEM = "심층 삭제"
REWORK_CAP = 25            # 재작업으로 되돌릴 수 있는 반려 카드 수(2026-11-21 대표 결정 — 10 → 25)
# 원문 블록의 국문 해석 칸을 비울 수 없을 때 넣는 고정 문장(새 사실 0 — 원문을 읽으라는 말뿐).
TRANSLATION_NOTICE = "국문 해석을 싣지 않았다 — 원문을 본다."
TRANSLATION_NOTICE_TITLE = "원문 참조"
_INDEXED = re.compile(r"^(key_facts|checks|deep\.key_violations)\[(\d+)\]$")
_DEEP_483_OPTIONAL = ("inspectional_significance", "administrative_risks")
_CHECKS_RANGE = (2, 3)
_TRANSLATION_ROOTS = ("observations_ko", "violations_ko", "ncr_ko")
_KV = re.compile(r"^key_violations\[(\d+)\](?:\..+)?$")
_REM_ITEM = re.compile(r"^required_remediation\.items\[(\d+)\]$")
_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _requests(copyedit, item):
    out = []
    for card in copyedit.get("cards") or []:
        if not isinstance(card, dict) or not card.get("web_card_id"):
            continue
        for note in card.get("notes") or []:
            if isinstance(note, dict) and str(note.get("item", "")).strip() == item:
                out.append((str(card["web_card_id"]), str(note.get("slot", "")).strip(),
                            str(note.get("evidence", ""))))
    return out


def blank_requests(copyedit):
    """copyedit.json → [(카드 id, 칸, 사유)] — `verdict` 와 무관하게 `notes` 의 '칸 삭제' 만."""
    return _requests(copyedit, ITEM)


def deep_requests(copyedit):
    """copyedit.json → [(카드 id, 칸, 사유)] — `notes` 의 '심층 삭제' 만."""
    return _requests(copyedit, DEEP_ITEM)


def _is_deep_slot(slot):
    return str(slot or "").strip().startswith("deep.")


def deep_auto(copyedit, cap=REWORK_CAP):
    """반려 카드 중 `fails` 가 전부 심층 칸인 것을 `pass` + '심층 삭제' notes 로 바꾼다(제자리 · 순수).

    통과 카드(원래 `pass` 포함)의 `checked` 에 원문에 없다고 적힌 심층 칸(`absent`)이 있고 그 칸에 칸 삭제·심층 삭제 주의가
    없으면 심층 삭제 주의를 더한다 — 원문에 없는 말을 싣지 않는다(리허설 1121b fda483-196392: 반려 근거와 다른 심층 지적이
    `absent` 로만 적혀 있었다).
    돌려주는 값 = (바꾼 카드 id 목록, 남은 반려 카드 id 목록, absent 로 주의를 더한 '카드.칸' 목록).
    `counts` 를 다시 세고 `rework.cards` 에서 바꾼 카드를 뺀다. `fails` 가 빈 반려 카드는 건드리지 않는다(검사가 따로 잡는다)."""
    converted, remaining, added = [], [], []
    for card in copyedit.get("cards") or []:
        if not isinstance(card, dict):
            continue
        cid = str(card.get("web_card_id"))
        if card.get("verdict") == "reject":
            fails = [f for f in card.get("fails") or [] if isinstance(f, dict)]
            if not (fails and all(_is_deep_slot(f.get("slot")) for f in fails)):
                remaining.append(cid)
                continue
            notes = card.setdefault("notes", [])
            for f in fails:
                notes.append({"item": DEEP_ITEM, "slot": str(f.get("slot")).strip(),
                              "evidence": f"{f.get('item', '')}: {f.get('evidence', '')}".strip(": ")})
            card["fails"] = []
            card["verdict"] = "pass"
            converted.append(cid)
        if card.get("verdict") != "pass":
            continue
        notes = card.get("notes") if isinstance(card.get("notes"), list) else []
        marked = {str(n.get("slot", "")).strip() for n in notes
                  if isinstance(n, dict) and str(n.get("item", "")).strip() in (ITEM, DEEP_ITEM)}
        for c in card.get("checked") or []:
            if not (isinstance(c, dict) and c.get("absent") is True):
                continue
            slot = str(c.get("slot", "")).strip()
            if not _is_deep_slot(slot) or any(m and (m == slot or slot.startswith(m) or m.startswith(slot)) for m in marked):
                continue
            card.setdefault("notes", []).append(
                {"item": DEEP_ITEM, "slot": slot, "evidence": f"원문에 없음(checked absent): {str(c.get('claim', ''))[:80]}"})
            marked.add(slot)
            added.append(f"{cid}.{slot}")
    rework = copyedit.get("rework")
    if isinstance(rework, dict) and isinstance(rework.get("cards"), list):
        rework["cards"] = [c for c in rework["cards"] if c not in set(converted)]
    cards = [c for c in copyedit.get("cards") or [] if isinstance(c, dict)]
    counts = dict(copyedit.get("counts") or {})
    counts.update({"cards": len(cards),
                   "pass": sum(c.get("verdict") == "pass" for c in cards),
                   "reject": sum(c.get("verdict") == "reject" for c in cards),
                   "fails_total": sum(len(c.get("fails") or []) for c in cards),
                   "notes_total": sum(len(c.get("notes") or []) for c in cards)})
    copyedit["counts"] = counts
    return converted, remaining, added


def _is_483_deep(da):
    return isinstance(da, dict) and "inspectional_significance" in da and "fda_evaluation" not in da


def _is_483_kind(da):
    """심층 삭제용 유형 판별 — 483 은 선택 절을 키째 뺄 수 있어(2026-11-16) WL·행정처분 절이 없으면 483 으로 본다."""
    return isinstance(da, dict) and "fda_evaluation" not in da and "disposition_basis" not in da


def _path(text):
    """'observations_ko[2].detail_ko' → ['observations_ko', 2, 'detail_ko'] (꼴이 틀리면 None)."""
    out, pos = [], 0
    for m in _PATH_TOKEN.finditer(text):
        if m.start() != pos and text[pos:m.start()] != ".":
            return None
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    return out if pos == len(text) and out else None


def _notice_for(key):
    return TRANSLATION_NOTICE_TITLE if "title" in str(key) else TRANSLATION_NOTICE


def _replace_translation(item, path):
    """번역 칸을 고정 안내문으로 — 바꾼 칸 수(0 = 경로가 없다)."""
    node, parent, key = item, None, None
    for tok in path:
        if isinstance(tok, int):
            if not isinstance(node, list) or tok >= len(node):
                return 0
        elif not isinstance(node, dict) or tok not in node:
            return 0
        parent, key, node = node, tok, node[tok]
    if isinstance(node, str):
        parent[key] = _notice_for(key)
        return 1

    def walk(obj):
        n = 0
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and str(k).endswith("_ko") and v.strip():
                    obj[k] = _notice_for(k)
                    n += 1
                elif isinstance(v, (dict, list)):
                    n += walk(v)
        elif isinstance(obj, list):
            for v in obj:
                n += walk(v)
        return n
    return walk(node)


def _apply_deep_card(cid, reqs, deep):
    """한 카드의 심층 요청(칸 삭제·심층 삭제 섞임) — (문제 줄, 한 일 [(칸, 처리, 사유)]). deep 을 제자리에서 고친다."""
    problems, done = [], []
    item = deep.get(cid) if isinstance(deep, dict) else None
    if not isinstance(item, dict):
        for slot, kind, why in reqs:
            if kind == ITEM:
                problems.append(f"{cid}.{slot}: FDA 483 심층만 비울 수 있다(이 카드는 심층이 없거나 WL·행정처분 — 필수 절)")
            else:
                problems.append(f"{cid}.{slot}: 심층 델타에 그 카드가 없다 — 뺄 것을 찾지 못했다(반려)")
        return problems, done
    da = item.get("deep_analysis") if isinstance(item.get("deep_analysis"), dict) else None
    kv_del, rem_del, blank_keys, drop_why = {}, {}, [], []
    for slot, kind, why in reqs:
        tag = f"{cid}.{slot}"
        sub = slot[len("deep."):]
        path = _path(sub)
        if kind == DEEP_ITEM and path and path[0] in _TRANSLATION_ROOTS:
            n = _replace_translation(item, path)
            if n:
                done.append((slot, f"국문 해석 {n}칸을 안내문으로 바꿈(원문 병기 게이트 — 뺄 수 없다)", why))
            else:
                problems.append(f"{tag}: 그런 번역 칸이 없다 — 뺄 것을 찾지 못했다(반려)")
            continue
        if kind == ITEM:
            if not _is_483_deep(da):
                problems.append(f"{tag}: FDA 483 심층만 비울 수 있다(이 카드는 심층이 없거나 WL·행정처분 — 필수 절)")
                continue
            m = _INDEXED.match(slot)
            if m:
                kv_del.setdefault(int(m.group(2)), []).append((slot, kind, why))
            elif sub in _DEEP_483_OPTIONAL:
                blank_keys.append((sub, slot, why))
            else:
                problems.append(f"{tag}: 비울 수 없는 심층 절(483 선택 절은 {', '.join(_DEEP_483_OPTIONAL)} 뿐)")
            continue
        # 심층 삭제 — 거부하지 않는다. 못 빼는 칸은 심층분석 통째로.
        if da is None:
            done.append((slot, "심층분석 없음 — 할 일 없음", why))
            continue
        mk, mr = _KV.match(sub), _REM_ITEM.match(sub)
        if mk:
            kv_del.setdefault(int(mk.group(1)), []).append((slot, kind, why))
        elif mr:
            rem_del.setdefault(int(mr.group(1)), []).append((slot, why))
        elif sub in _DEEP_483_OPTIONAL and _is_483_kind(da):
            blank_keys.append((sub, slot, why))
        else:
            drop_why.append((slot, "필수 절" if path else "칸 이름을 읽을 수 없음", why))
    if da is not None and not problems:
        kv = da.get("key_violations") if isinstance(da.get("key_violations"), list) else []
        bad = [i for i in kv_del if i >= len(kv)]
        for i in bad:
            for slot, kind, why in kv_del.pop(i):
                if kind == ITEM:
                    problems.append(f"{cid}.{slot}: 그런 번호가 없다(key_violations {len(kv)}개)")
                else:
                    drop_why.append((slot, f"그런 번호가 없다(key_violations {len(kv)}개)", why))
        if kv_del and len(kv) - len(kv_del) < 1:
            auto = [r for rs in kv_del.values() for r in rs if r[1] == DEEP_ITEM]
            if not auto:
                problems.append(f"{cid}.deep.key_violations: 지적이 하나뿐이라(또는 전부라) 뺄 수 없다(심층 필수 절)")
            else:
                drop_why.extend((s, "지적이 하나도 남지 않는다", w) for s, _k, w in auto)
                drop_why.extend((s, "지적이 하나도 남지 않는다", w)
                                for rs in kv_del.values() for s, k, w in rs if k != DEEP_ITEM)
                kv_del.clear()
        rem = da.get("required_remediation") if isinstance(da.get("required_remediation"), dict) else {}
        items = rem.get("items") if isinstance(rem.get("items"), list) else []
        for i in [i for i in rem_del if i >= len(items)]:
            drop_why.extend((s, f"그런 번호가 없다(시정 항목 {len(items)}개)", w) for s, w in rem_del.pop(i))
        if rem_del and not _is_483_kind(da) and len(items) - len(rem_del) < 1:
            drop_why.extend((s, "시정 항목이 하나도 남지 않는다(경고서한·행정처분 필수)", w)
                            for rs in rem_del.values() for s, w in rs)
            rem_del.clear()
    if problems:
        return problems, []
    if da is not None and drop_why:
        item.pop("deep_analysis", None)
        for slot, reason, why in drop_why:
            done.append((slot, f"심층분석 통째로 뺌({reason})", why))
        for rs in kv_del.values():
            for slot, _k, why in rs:
                done.append((slot, "심층분석 통째로 뺌(같은 카드의 다른 칸 때문)", why))
        for rs in rem_del.values():
            for slot, why in rs:
                done.append((slot, "심층분석 통째로 뺌(같은 카드의 다른 칸 때문)", why))
        for _k, slot, why in blank_keys:
            done.append((slot, "심층분석 통째로 뺌(같은 카드의 다른 칸 때문)", why))
        return problems, done
    if da is not None:
        kv = da.get("key_violations")
        for i in sorted(kv_del, reverse=True):         # 뒤 번호부터 — 앞 번호가 밀리지 않게
            del kv[i]
            for slot, _k, why in kv_del[i]:
                done.append((slot, f"지적 {i}번을 뺌(남은 지적 {len(kv)}개)", why))
        items = (da.get("required_remediation") or {}).get("items")
        for i in sorted(rem_del, reverse=True):
            del items[i]
            for slot, why in rem_del[i]:
                done.append((slot, f"시정 항목 {i}번을 뺌", why))
        for key, slot, why in blank_keys:
            da[key] = ""
            done.append((slot, "선택 절을 비움", why))
    return problems, done


def apply_blanks(delta, deep, requests, deep_reqs=None, log=None):
    """(문제 줄, 한 일 줄) — 순수. delta·deep 을 제자리에서 고친다(문제가 있으면 호출자가 버린다).

    `requests` = 칸 삭제, `deep_reqs` = 심층 삭제(없으면 빈 목록). `log` 리스트를 주면 한 일을 dict 로 더한다."""
    problems, done = [], []
    cards = delta.get("cards") if isinstance(delta.get("cards"), dict) else {}
    tagged = [(cid, slot, why, ITEM) for cid, slot, why in requests]
    tagged += [(cid, slot, why, DEEP_ITEM) for cid, slot, why in (deep_reqs or [])]
    by_card_deep = {}
    for cid, slot, why, kind in tagged:
        if slot.startswith("deep."):
            by_card_deep.setdefault(cid, []).append((slot, kind, why))
    for cid, reqs in by_card_deep.items():
        p, d = _apply_deep_card(cid, reqs, deep)
        problems.extend(p)
        for slot, action, why in d:
            done.append(f"{cid}.{slot}: {action}")
            if log is not None:
                kind = next((k for s, k, _w in reqs if s == slot), DEEP_ITEM)
                log.append({"web_card_id": cid, "slot": slot, "item": kind, "action": action, "evidence": why})
    front = [r for r in requests if not r[1].startswith("deep.")]
    front += [r for r in (deep_reqs or []) if not r[1].startswith("deep.")]
    # 같은 목록에서 여러 항목을 빼면 뒤 번호부터 빼야 앞 번호가 밀리지 않는다.
    indexed = sorted((r for r in front if _INDEXED.match(r[1])),
                     key=lambda r: (r[0], _INDEXED.match(r[1]).group(1), -int(_INDEXED.match(r[1]).group(2))))
    plain = [r for r in front if not _INDEXED.match(r[1])]
    for cid, slot, why in plain + indexed:
        tag = f"{cid}.{slot}"
        card = cards.get(cid)
        m = _INDEXED.match(slot)
        if not isinstance(card, dict):
            problems.append(f"{tag}: 델타에 그 카드가 없다")
            continue
        en = card.get("en") if isinstance(card.get("en"), dict) else None
        action = None
        if slot == "hook_line":
            card.pop("hook_line", None)
            if en is not None:
                en.pop("hook_line", None)
            action = "국·영 hook_line 지움"
        elif slot in ("implication", "checks"):
            card[slot] = "" if slot == "implication" else []
            if en is not None:
                en[slot] = "" if slot == "implication" else []
            action = f"국·영 {slot} 비움"
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
            action = f"국·영 {key} {i}번 뺌"
        else:
            problems.append(f"{tag}: 비울 수 없는 칸(필수 칸 — 그 카드는 반려다)")
            continue
        done.append(tag if action is None else f"{tag}: {action}")
        if log is not None:
            log.append({"web_card_id": cid, "slot": slot, "item": ITEM, "action": action, "evidence": why})
    return problems, done


def verify_blanks(brief, requests, deep_reqs=None):
    """다시 돌린 조립본에서 칸 삭제가 반영됐는지(문제 줄, 순수). 심층 삭제 카드는 심층이 빠져도 문제가 아니다."""
    problems = []
    by_id = {c.get("id"): c for c in brief.get("cards") or [] if isinstance(c, dict)}
    deep_ok = {cid for cid, _s, _w in (deep_reqs or [])}
    for cid, slot, _why in requests:
        card = by_id.get(cid)
        if card is None:
            continue          # 채택 밖·합쳐진 카드 — 칸 삭제와 무관하게 발행본에 없다
        en = card.get("en") if isinstance(card.get("en"), dict) else {}
        if slot.startswith("deep.") and not card.get("deep_analysis"):
            if cid not in deep_ok:
                problems.append(f"{cid}.{slot}: 심층을 비운 뒤 심층 게이트를 못 넘어 심층이 통째로 빠졌다 — 그 카드는 반려")
        elif slot == "hook_line" and (card.get("hook_line") or en.get("hook_line")):
            problems.append(f"{cid}.hook_line: 조립본에 아직 있다")
        elif slot == "implication" and (card.get("implication") or en.get("implication")):
            problems.append(f"{cid}.implication: 조립본에 아직 있다")
    return problems


def deep_lost(brief, deep_reqs):
    """심층분석 칸을 심층 삭제한 카드 중 조립본에 심층분석이 없는 카드 id(정보 — 문제가 아니다).
    번역 칸만 바꾼 카드(WHOPIR 등 원래 심층분석이 없는 카드 포함)는 세지 않는다."""
    by_id = {c.get("id"): c for c in brief.get("cards") or [] if isinstance(c, dict)}
    out = []
    analysis = [cid for cid, s, _w in deep_reqs or []
                if not any(s.startswith(f"deep.{r}") for r in _TRANSLATION_ROOTS)]
    for cid in dict.fromkeys(analysis):
        card = by_id.get(cid)
        if card is not None and not card.get("deep_analysis"):
            out.append(cid)
    return out


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    da = sub.add_parser("deep-auto")
    da.add_argument("copyedit")
    da.add_argument("--cap", type=int, default=REWORK_CAP)
    a = sub.add_parser("apply")
    a.add_argument("copyedit")
    a.add_argument("delta_in")
    a.add_argument("delta_out")
    a.add_argument("--deep-in")
    a.add_argument("--deep-out")
    a.add_argument("--log")
    v = sub.add_parser("verify")
    v.add_argument("copyedit")
    v.add_argument("brief")
    args = ap.parse_args(argv)
    try:
        copyedit = _load(args.copyedit)
        if args.cmd == "verify":
            brief = _load(args.brief)
        elif args.cmd == "apply":
            delta = _load(args.delta_in)
            deep = _load(args.deep_in) if args.deep_in else None
    except (OSError, ValueError) as e:
        print(f"입력 오류: {e}")
        return 2
    copyedit = copyedit if isinstance(copyedit, dict) else {}
    if args.cmd == "deep-auto":
        converted, remaining, added = deep_auto(copyedit, args.cap)
        if converted or added:
            _dump(copyedit, args.copyedit)
        for cid in converted:
            print(f"심층만 반려 → 통과(심층 삭제): {cid}")
        for tag in added:
            print(f"원문에 없는 심층 칸 → 심층 삭제: {tag}")
        verdict = "재작업 가능" if len(remaining) <= args.cap else f"재작업 상한 {args.cap}장 초과 — 보류"
        print(f"심층 삭제로 통과 {len(converted)}장 · 남은 반려 {len(remaining)}장({verdict})")
        return 0
    requests, dreqs = blank_requests(copyedit), deep_requests(copyedit)
    if args.cmd == "verify":
        problems = verify_blanks(brief if isinstance(brief, dict) else {}, requests, dreqs)
        for cid in deep_lost(brief if isinstance(brief, dict) else {}, dreqs):
            print(f"심층 없이 발행: {cid} (심층 삭제 — 허용)")
        for p in problems:
            print(f"문제: {p}")
        print(f"칸 삭제 확인 {len(requests)}건 · 심층 삭제 {len(dreqs)}건 · 문제 {len(problems)}건")
        return 1 if problems else 0
    if not requests and not dreqs:
        print("칸 삭제 0건 — 쓰지 않았다")
        return 0
    if any(s.startswith("deep.") for _, s, _ in requests + dreqs) and not (args.deep_in and args.deep_out):
        print("입력 오류: 심층 칸 삭제가 있는데 --deep-in/--deep-out 이 없다")
        return 2
    log = []
    problems, done = apply_blanks(delta, deep, requests, dreqs, log)
    for p in problems:
        print(f"문제: {p}")
    if problems:
        print(f"칸 삭제 {len(requests)}건 · 심층 삭제 {len(dreqs)}건 중 문제 {len(problems)}건 — 아무것도 쓰지 않았다")
        return 1
    _dump(delta, args.delta_out)
    if args.deep_out and deep is not None:
        _dump(deep, args.deep_out)
    if args.log:
        _dump({"removed": log}, args.log)
    for d in done:
        print(f"비움: {d}")
    print(f"칸 삭제·심층 삭제 {len(done)}건 반영" + (f" · 기록 {args.log}" if args.log else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
