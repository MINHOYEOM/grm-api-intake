# -*- coding: utf-8 -*-
"""백업 v17 판정 — 편집팀이 이번 호를 끝내지 못했는지, 백업이 지금 시작해도 되는지를 코드로 정한다.

    python company/editorial/backup_gate.py <D>                 # 시작 판정(월 09:30 슬롯이 처음에, 기다리는 동안 5분마다)
    python company/editorial/backup_gate.py <D> --final         # 반납 push 직전 재확인
    python company/editorial/backup_gate.py <D> --rehearsal <회차>   # 리허설(시각·main 무시, 리허설 브랜치·폴더)

첫 줄은 한 단어 — `RUN`(시작·반납해도 된다) · `WAIT`(5분 뒤 다시) · `SKIP`(할 일 없음, 정상) · `STOP`(못 한다, 대표 결정).
둘째 줄은 근거 JSON. exit 0=RUN · 3=WAIT · 4=SKIP/STOP · 2=읽기 실패.

판정의 기준은 **발행 브릿지와 같은 함수**다(`delta_bridge.load_git_run`·`load_run_scaffold`). 브릿지가 편집팀 반납을
가져갈 수 있으면 백업은 하지 않는다 — 그래서 한 호에 두 반납이 발행 길에 오르지 않는다. 그래도 둘이 겹치면 브릿지가
편집팀 쪽을 먼저 보고, main 에는 먼저 기록된 하나만 남는다(`delta_bridge._existing_conflicts`).

시각(KST, 월요일 실행) — 근거는 `RUN.md` 5항(교열 마감 09:25)과 발행 브릿지 크론(월 13:37 KST 가 마지막 회차):
  · 09:40 전: 편집팀이 끝났다는 표시(교열 `보류`·`실패`, 또는 교열 없이 데스크 `보류`·`실패`)가 없으면 WAIT.
  · 09:40 부터: 편집팀 반납을 브릿지가 쓸 수 없으면 RUN(교열 `재작업`·미착수도 포함 — 09:25 시한이 지났다).
  · 11:00 까지 시작하지 못하면 STOP(백업이 끝나기 전에 브릿지 마지막 회차가 지난다).
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

EDITORIAL_GRACE = "09:40"   # 교열 마감(09:25) + 여유. 이 전에는 편집팀이 끝났다는 표시가 있어야 시작한다
START_DEADLINE = "11:00"    # 이 뒤에는 시작하지 않는다
FINISH_DEADLINE = "13:15"   # 백업이 이때까지 반납하지 못하면 `실패` 로 끝낸다(브릿지 마지막 회차 13:37)
TERMINAL = ("보류", "실패")


def _status(obj):
    return obj.get("status") if isinstance(obj, dict) else None


def editorial_finished(desk, copyedit):
    """편집팀이 이번 호를 스스로 끝냈다(더 기다릴 것이 없다)는 표시가 있는가."""
    if _status(copyedit) in TERMINAL:
        return True
    return copyedit is None and _status(desk) in TERMINAL


def decide(state, now_hm, final=False, rehearsal=False):
    """순수 판정. state 키:
      main_delta(bool) · handoff(bool) · editorial_usable(bool) · editorial_reason(str)
      desk(dict|None) · copyedit(dict|None) · backup_usable(bool)
    반환 (결정, 사유)."""
    if state.get("main_delta") and not rehearsal:
        return "SKIP", "main 에 이번 호 델타가 이미 있다 — 발행 길에 들어간 호"
    if state.get("editorial_usable"):
        return "SKIP", "편집팀 반납을 브릿지가 쓸 수 있다 — 백업 불필요"
    if final:
        return "RUN", "반납해도 된다(main 델타 없음 · 편집팀 반납 채택 불가)"
    if state.get("backup_usable"):
        return "SKIP", "백업 반납이 이미 있다(채택 조건 충족)"
    late = not rehearsal and now_hm >= START_DEADLINE
    if not state.get("handoff"):
        if rehearsal or late:
            return "STOP", "이번 주 handoff 파일이 없다(claude/queue-handoff)"
        return "WAIT", "handoff 파일을 아직 읽을 수 없다"
    if late:
        return "STOP", f"시작 시한 {START_DEADLINE} KST 를 넘겼다 — 백업이 브릿지 마지막 회차(13:37) 전에 끝나지 못한다"
    reason = state.get("editorial_reason") or ""
    if editorial_finished(state.get("desk"), state.get("copyedit")):
        return "RUN", f"편집팀이 마감하지 못하고 끝냈다 — {reason}"
    if rehearsal or now_hm >= EDITORIAL_GRACE:
        return "RUN", f"교열 시한(09:25)이 지났는데 쓸 수 있는 편집팀 반납이 없다 — {reason}"
    return "WAIT", f"편집팀이 아직 일하는 중일 수 있다({EDITORIAL_GRACE} KST 까지 기다린다) — {reason}"


# ── git 에서 상태 모으기 ───────────────────────────────────────────────────────────────

def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, timeout=120)


def _fetch(branch, ref):
    r = _git("fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/{branch}:{ref}")
    return r.returncode == 0


def _show(ref, path):
    r = _git("show", f"{ref}:{path}")
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def _json(text):
    try:
        obj = json.loads(text) if text is not None else None
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def collect(date, branch, folder, rehearsal=False):
    """원격에서 판정 재료를 모은다(읽기만 한다 — 작업 폴더·현재 브랜치를 바꾸지 않는다)."""
    import delta_bridge

    under = date.replace("-", "_")
    state = {"main_delta": False, "handoff": False}
    if not rehearsal:
        if not _fetch("main", "refs/backup-gate/main"):
            raise RuntimeError("main 을 받지 못했다")
        state["main_delta"] = _show("refs/backup-gate/main", f"web/data/deltas/delta_{under}.json") is not None
    if _fetch("claude/queue-handoff", "refs/backup-gate/queue-handoff"):
        h = _show("refs/backup-gate/queue-handoff", f"handoff_{date}.json")
        s = _show("refs/backup-gate/queue-handoff", f"brief_web_{under}.json")
        state["handoff"] = bool(h and h.strip()) and bool(s and s.strip())
    run_ref = "refs/backup-gate/run"
    have_run = _fetch(branch, run_ref)

    def read(_d, name):
        return _show(run_ref, f"{folder}/{name}") if have_run else None

    state["desk"] = _json(read(date, "desk.json"))
    state["copyedit"] = _json(read(date, "copyedit.json"))
    state["editorial_usable"], state["editorial_reason"] = usable(delta_bridge, date, read, "")
    state["backup_usable"], state["backup_reason"] = usable(delta_bridge, date, read, delta_bridge.BACKUP_SUBDIR)
    return state


def usable(delta_bridge, date, read, sub):
    """브릿지가 이 반납을 가져갈 수 있는가 — 채택 조건 + 스캐폴드(브릿지와 같은 함수). (bool, 사유)."""
    run = delta_bridge.load_git_run(date, read=read, sub=sub)
    if not run["adoptable"]:
        return False, run["reason"]
    try:
        delta_bridge.load_run_scaffold(run, run["delta"] or {}, read=read)
    except delta_bridge.DeltaBridgeError as e:
        return False, f"채택 조건은 채웠으나 스캐폴드를 쓸 수 없다: {e}"
    return True, run["reason"]


def kst_now_hm():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="백업 v17 시작·반납 판정")
    ap.add_argument("date", help="이번 호 날짜(YYYY-MM-DD) — 리허설이면 재료 날짜")
    ap.add_argument("--final", action="store_true", help="반납 push 직전 재확인")
    ap.add_argument("--rehearsal", metavar="회차", help="리허설 회차 — 브랜치 claude/company-rehearsal-<회차>")
    ap.add_argument("--now", help="시각 HH:MM(KST) — 시험용")
    args = ap.parse_args(argv)
    if args.rehearsal:
        branch = f"claude/company-rehearsal-{args.rehearsal}"
        folder = f"company/run/rehearsal-{args.rehearsal}"
    else:
        branch = f"claude/company-{args.date}"
        folder = f"company/run/{args.date}"
    try:
        state = collect(args.date, branch, folder, rehearsal=bool(args.rehearsal))
    except Exception as e:  # noqa: BLE001 — 판정을 못 하면 시작하지 않는다
        print(f"읽기 실패: {e}", file=sys.stderr)
        return 2
    now = args.now or kst_now_hm()
    decision, reason = decide(state, now, final=args.final, rehearsal=bool(args.rehearsal))
    print(decision)
    print(json.dumps({"decision": decision, "reason": reason, "now_kst": now, "branch": branch,
                      "folder": folder, "main_delta": state.get("main_delta"), "handoff": state.get("handoff"),
                      "editorial_desk": _status(state.get("desk")),
                      "editorial_copyedit": _status(state.get("copyedit")),
                      "editorial_usable": state.get("editorial_usable"),
                      "backup_usable": state.get("backup_usable"),
                      "backup_reason": state.get("backup_reason")}, ensure_ascii=False))
    return {"RUN": 0, "WAIT": 3}.get(decision, 4)


if __name__ == "__main__":
    raise SystemExit(main())
