# -*- coding: utf-8 -*-
"""리허설 결과 점검 — `REHEARSAL.md` 의 성공 판정 1~5(+4-1 교열 전수 대조 · 4-2 심층 삭제)를 코드로 본다. 읽기만 한다.

    python company/editorial/rehearsal_check.py <실행 폴더> <handoff 파일>

저장소 루트에서 돌린다(`grm_queue`·`delta_bridge` 를 불러 발행 때와 **같은 함수**로 판정한다).
전부 통과면 exit 0, 하나라도 아니면 exit 1. 네트워크·DB·Notion 을 쓰지 않는다.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import delta_bridge  # noqa: E402
import grm_queue  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blank_slots  # noqa: E402
import copyedit_evidence  # noqa: E402


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check(folder, handoff_path):
    """[(번호, 이름, 통과 여부, 설명)] — 순수 판정."""
    out = []
    handoff = _load(handoff_path)
    desk = _load(os.path.join(folder, "desk.json")) or {}
    delta = _load(os.path.join(folder, "delta.json"))
    copyedit = _load(os.path.join(folder, "copyedit.json")) or {}

    ref = grm_queue.handoff_ref(handoff) if isinstance(handoff, dict) else {}
    got = desk.get("handoff_ref") if isinstance(desk.get("handoff_ref"), dict) else {}
    ok = desk.get("status") == "완료" and bool(ref) and got.get("sha256") == ref.get("sha256")
    out.append((1, "데스크 완료 · handoff 지문 일치", ok,
                f"status={desk.get('status')!r} desk={str(got.get('sha256'))[:12]} file={str(ref.get('sha256'))[:12]}"))

    plan = desk.get("shards") if isinstance(desk.get("shards"), dict) else {}
    unfinished = []
    for kind, prefix in (("delta", "delta"), ("deep", "deep")):
        for name, ids in sorted((plan.get(kind) or {}).items()):
            part = _load(os.path.join(folder, f"{prefix}.{name}.json"))
            if (part or {}).get("status") != "완료" and (ids or kind == "delta"):
                unfinished.append(f"{prefix}.{name}")
    ok = bool(plan) and not unfinished and isinstance(delta, dict) and isinstance(delta.get("cards"), dict)
    out.append((2, "샤드 전부 완료 · delta.json 있음", ok,
                f"미완 {unfinished[:5]} · 카드 {len((delta or {}).get('cards') or {})}장"))

    gate = copyedit.get("code_gate") if isinstance(copyedit.get("code_gate"), dict) else {}
    out.append((3, "교열 코드 검사 exit 0", bool(gate.get("ran")) and gate.get("exit") == 0,
                f"ran={gate.get('ran')!r} exit={gate.get('exit')!r}"))

    rejects = [c for c in copyedit.get("cards") or [] if isinstance(c, dict) and c.get("verdict") != "pass"]
    n_reject = max(int((copyedit.get("counts") or {}).get("reject") or 0), len(rejects))
    # 재작업 1회를 거친 2차 판정(round 2)이면 기자의 재작업 기록과 1차 교열 기록이 남아 있어야 한다.
    rnd = copyedit.get("round") or 1
    rework_ok = True
    if rnd != 1:
        rework = _load(os.path.join(folder, "rework.json")) or {}
        first = _load(os.path.join(folder, "copyedit.r1.json")) or {}
        rework_ok = rnd == 2 and rework.get("status") == "완료" and first.get("status") == "재작업"
    out.append((4, "교열 reject 0", copyedit.get("status") == "완료" and n_reject == 0 and rework_ok,
                f"status={copyedit.get('status')!r} reject={n_reject} round={rnd!r}"
                + ("" if rework_ok else " — 재작업 기록(rework.json 완료 · copyedit.r1.json 재작업)이 없다")))

    # 4-1. 교열이 카드를 전부 원문과 맞춰 봤는가(리허설 1121b — 표본 정독으로 reject 0). 교열 마감 전 점검과 같은 함수.
    if copyedit.get("status") == "완료":
        lines, stats = copyedit_evidence.check(
            delta, copyedit, _load(os.path.join(folder, "deep_delta.json")), handoff,
            _load(copyedit_evidence.find_scaffold(folder, handoff) or os.path.join(folder, "scaffold.json")))
        out.append(("4-1", "교열 전수 대조 근거(카드마다 원문 조각)", not lines,
                    copyedit_evidence.summary_line(stats) + (f" — 첫 줄: {lines[0][:160]}" if lines else "")))
    else:
        out.append(("4-1", "교열 전수 대조 근거(카드마다 원문 조각)", False,
                    f"status={copyedit.get('status')!r} — 완료가 아니면 보지 않는다"))

    # 4-2. 심층에서만 걸린 반려는 코드가 심층을 빼고 통과시킨다(blank_slots.py deep-auto · 2026-11-21 대표 결정).
    #      그런 카드가 반려로 남았으면 deep-auto 를 안 돌린 것이고, 심층 삭제를 반영했으면 기록(slot_removals.json)이 있어야 한다.
    deep_only = [str(c.get("web_card_id")) for c in rejects
                 if c.get("fails") and all(str((f or {}).get("slot", "")).strip().startswith("deep.")
                                           for f in c.get("fails") if isinstance(f, dict))]
    n_deep_notes = sum(1 for c in copyedit.get("cards") or [] if isinstance(c, dict)
                       for n in c.get("notes") or [] if isinstance(n, dict) and n.get("item") == blank_slots.DEEP_ITEM)
    log = _load(os.path.join(folder, "slot_removals.json"))
    logged = len((log or {}).get("removed") or []) if isinstance(log, dict) else 0
    need_log = copyedit.get("status") == "완료" and n_deep_notes > 0
    ok = not deep_only and (not need_log or logged > 0)
    out.append(("4-2", "심층만 반려된 카드 0 · 심층 삭제 기록", ok,
                f"심층만 반려 {len(deep_only)}장{(' ' + str(deep_only[:5])) if deep_only else ''} · 심층 삭제 주의 {n_deep_notes}건"
                f" · slot_removals.json {logged}줄" + (" — 반영 기록이 없다" if need_log and not logged else "")))

    detail, ok = "", False
    if isinstance(delta, dict) and isinstance(handoff, dict):
        try:
            run = {"date": ref.get("date", ""), "branch": "(리허설)"}
            text = delta_bridge.load_run_scaffold(
                run, delta, read=lambda _d, name: _read_text(os.path.join(folder, name)))
            if text is None:
                detail = "델타에 handoff_ref 가 없다(merge_parts 가 붙이지 않음 — desk.json 의 handoff_ref 확인)"
            else:
                manifest = grm_queue.build_manifest(handoff)
                states = grm_queue.compute_states(manifest, delta, None, ref.get("date", ""))
                missing = len(manifest) - len(states)
                counts = {}
                for v in states.values():
                    counts[v["state"]] = counts.get(v["state"], 0) + 1
                ok = missing == 0 and bool(states)
                detail = f"행 {len(manifest)} · 상태 없는 행 {missing} · {counts}"
        except delta_bridge.DeltaBridgeError as e:
            detail = f"스캐폴드 거부: {e}"
    out.append((5, "스캐폴드가 그 호의 것 · 모든 행에 상태", ok, detail))
    return out


def _read_text(path):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except OSError:
        return None


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(argv) != 3:
        print("사용: python company/editorial/rehearsal_check.py <실행 폴더> <handoff 파일>", file=sys.stderr)
        return 2
    results = check(argv[1], argv[2])
    for num, name, ok, detail in results:
        print(f"{num}. {'통과' if ok else '실패'} — {name} ({detail})")
    passed = all(ok for _, _, ok, _ in results)
    print("리허설 점검: " + ("전부 통과 (6번은 사람이 확인)" if passed else "실패 항목 있음"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
