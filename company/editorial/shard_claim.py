# -*- coding: utf-8 -*-
"""기자 워커가 샤드를 스스로 가져가는 규약 — 끝날 때까지 이어서 한다(self-completing).

워커는 자기 몫을 끝내고도 시간이 남으면 '가장 번호 낮은 미완 샤드'를 가져와 처리한다. 같은 샤드를 두 워커가
동시에 잡지 않게 **브랜치 안의 claim 파일 + git push 의 거절**을 잠금으로 쓴다(서버 없이 git 만으로).

    python company/editorial/shard_claim.py status <run_folder>
    python company/editorial/shard_claim.py next   <run_folder> <delta|deep>       # 가져갈 샤드 한 줄(없으면 NONE)
    python company/editorial/shard_claim.py claim  <run_folder> <delta|deep> <샤드> <워커id>   # 0=내가 잡음, 1=졌음
    python company/editorial/shard_claim.py claim  <run_folder> desk desk <데스크id>          # 데스크 잠금(RUN.md 5-2)
    python company/editorial/shard_claim.py desk   <run_folder>                              # done|held|stale|free
    python company/editorial/shard_claim.py check  <run_folder> <delta|deep> <워커id> [--until 08:55]
        # 워커가 끝내도 되는가 — exit 0 = 끝내도 됨(DONE·DEADLINE·NO_TAKEOVER), exit 1 = 끝내면 안 됨(CLAIM <S> · WAIT <S> <초>)

· 샤드 상태 — 완료(`{kind}.{S}.json` 의 status=완료) · 점유(claim 파일, 만든 지 STALE_MIN 분 이내) · 비어 있음.
  점유한 지 STALE_MIN 분이 넘도록 결과가 없으면 죽은 워커로 보고 **가로챌 수 있다**(다른 워커가 덮어쓴다).
· delta 에는 가상 샤드 `T`(브리프 tldr·tldr_en)가 있다 — 모든 델타 샤드가 완료된 뒤에만 `next` 가 내놓는다.
· claim 의 원자성: claim 파일을 커밋해 push 한다. 거절되면 pull --rebase 를 하고, 같은 파일이 이미 남의 것이면
  (add/add 충돌 또는 새 점유) **졌다**. 3번까지 재시도.
· 결과 파일(`delta.A.json` 등)은 이 스크립트가 쓰지 않는다 — 기자가 쓴다.
· check — 워커의 종료 판단은 코드가 한다(2026-11-19 리허설 1120g: 심층 워커가 빈 샤드 5개를 두고 '분량이 크다'며 스스로 끝냈다.
  사전 점검 3번: 08:40 보강은 점유 25분이 안 된 샤드를 '없음'으로 보고 바로 끝나, 08:15 뒤 잡힌 샤드가 죽으면 이어받을 워커가 없었다).
  남은 샤드가 남의 점유 중이면 그 점유가 죽은 것으로 판정되는 시각(점유+25분)까지 기다리라고 답한다 — 단 그 시각이 시한(--until)을 넘으면
  이어받을 수 없으므로 끝내도 된다. 자기 종류가 모두 완료면 곧바로 끝내도 된다(비용 0).
"""
import datetime
import json
import os
import subprocess
import sys
import time

KST = datetime.timezone(datetime.timedelta(hours=9))
WAIT_MAX_SEC = 300  # 대기는 한 번에 5분 이하(RUN.md 5-2)

STALE_MIN = 25
RETRIES = 3


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _plan(folder):
    desk = _load(os.path.join(folder, "desk.json")) or {}
    return desk.get("shards") or {}


def shard_names(plan, kind):
    names = sorted(plan.get(kind, {}))
    return names


def _result_path(folder, kind, shard):
    # 데스크 잠금(RUN.md 5-2): 데스크는 샤드가 없다 — 결과는 `desk.json` 하나, 잠금은 `claims/desk.claim`.
    if kind == "desk":
        return os.path.join(folder, "desk.json")
    return os.path.join(folder, f"{kind}.{shard}.json")


def _result_done(folder, kind, shard):
    part = _load(_result_path(folder, kind, shard))
    return bool(part) and part.get("status") == "완료"


def _claim_path(folder, kind, shard):
    if kind == "desk":
        return os.path.join(folder, "claims", "desk.claim")
    return os.path.join(folder, "claims", f"{kind}.{shard}.claim")


def _claim_state(folder, kind, shard, now):
    c = _load(_claim_path(folder, kind, shard))
    if not c:
        return "free", None
    age_min = (now - float(c.get("at", 0))) / 60.0
    return ("held" if age_min <= STALE_MIN else "stale"), c


def status(folder, now=None):
    """[(kind, shard, state, claim)] — state: done|held|stale|free. delta 끝에 T 가 붙는다."""
    now = time.time() if now is None else now
    plan = _plan(folder)
    rows = []
    for kind in ("delta", "deep"):
        for s in shard_names(plan, kind):
            if not (plan.get(kind) or {}).get(s) and kind == "deep":
                rows.append((kind, s, "done", None))  # 계획 0건 = 할 일 없음
                continue
            if _result_done(folder, kind, s):
                rows.append((kind, s, "done", None))
            else:
                st, c = _claim_state(folder, kind, s, now)
                rows.append((kind, s, st, c))
    if plan.get("tldr"):
        t = plan["tldr"]
        if _result_done(folder, "delta", t):
            rows.append(("delta", t, "done", None))
        else:
            st, c = _claim_state(folder, "delta", t, now)
            rows.append(("delta", t, st, c))
    return rows


def next_shard(folder, kind, now=None):
    """이 종류에서 가져갈 가장 번호 낮은 샤드. 없으면 None. T 는 델타 샤드가 전부 끝난 뒤에만."""
    rows = status(folder, now)
    plan = _plan(folder)
    tldr = plan.get("tldr")
    mine = [r for r in rows if r[0] == kind]
    body = [r for r in mine if r[1] != tldr]
    for _, s, st, _c in body:
        if st in ("free", "stale"):
            return s
    if kind == "delta" and tldr and all(r[2] == "done" for r in body):
        for _, s, st, _c in mine:
            if s == tldr and st in ("free", "stale"):
                return s
    return None


def until_ts(hhmm, now):
    """'08:55' → now 와 같은 KST 날짜의 그 시각(epoch 초). 없으면 None(리허설 — 시한 없음)."""
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    day = datetime.datetime.fromtimestamp(now, KST)
    return day.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()


def exit_check(folder, kind, worker=None, until=None, now=None):
    """워커가 끝내도 되는가 → (끝내도 됨?, 판정 한 줄).

    DONE            자기 종류 샤드(+T)가 모두 완료
    DEADLINE        시한(until, epoch 초)을 넘었다 — 새 샤드를 잡지 않는다
    CLAIM <S>       빈 샤드·죽은 점유·내 점유가 있다 → 끝내지 말고 가져간다
    WAIT <S> <초>   남이 점유 중 — 그 점유가 죽은 것으로 판정될 수 있는 시각까지(한 번에 5분 이하) 기다렸다 다시 확인
    NO_TAKEOVER     남은 점유가 죽은 것으로 판정되는 시각이 시한 뒤라 이어받을 수 없다
    """
    now = time.time() if now is None else now
    mine = [r for r in status(folder, now) if r[0] == kind]
    if all(r[2] == "done" for r in mine):
        return True, "DONE"
    if until is not None and now >= until:
        return True, "DEADLINE"
    if worker:
        for _, s, st, c in mine:
            # 내가 잡고 결과를 아직 안 낸 샤드 — 내 일이다(결과를 `보류` 로 냈으면 남의 점유처럼 25분 뒤에 다시 본다)
            if st == "held" and (c or {}).get("worker") == worker and _load(_result_path(folder, kind, s)) is None:
                return False, f"CLAIM {s}"
    s = next_shard(folder, kind, now)
    if s:
        return False, f"CLAIM {s}"
    held = [(float((c or {}).get("at", 0)) + STALE_MIN * 60, s) for _, s, st, c in mine if st == "held"]
    if not held:
        # 남은 것이 T 뿐이고 아직 델타 본 샤드가 덜 끝난 경우 등 — 점유가 없으면 곧 next 가 답한다
        return False, f"WAIT - {WAIT_MAX_SEC}"
    stale_at, s = min(held)
    if until is not None and stale_at > until:
        return True, f"NO_TAKEOVER {s}"
    wait = min(WAIT_MAX_SEC, int(stale_at - now) + 1)
    if until is not None:
        wait = min(wait, int(until - now) + 1)
    wait = max(1, wait)
    return False, f"WAIT {s} {wait}"


def _git(root, *args, check=True):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def _repo_root(folder):
    out = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         encoding="utf-8", check=True)
    return out.stdout.strip()


def claim(folder, kind, shard, worker, now=None, remote="origin"):
    """claim 파일을 커밋·push 한다. True = 내가 잡음, False = 졌음(남이 잡았거나 이미 완료)."""
    root = _repo_root(folder)
    rel = os.path.relpath(_claim_path(os.path.abspath(folder), kind, shard), root).replace(os.sep, "/")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for _ in range(RETRIES):
        _git(root, "pull", "--rebase", remote, branch, check=False)
        now_t = time.time() if now is None else now
        if _result_done(folder, kind, shard):
            return False
        st, c = _claim_state(folder, kind, shard, now_t)
        if st == "held" and (c or {}).get("worker") != worker:
            return False
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"worker": worker, "at": now_t, "kind": kind, "shard": shard}, f)
            f.write("\n")
        _git(root, "add", rel)
        _git(root, "commit", "-m", f"claim {kind}.{shard} by {worker}", "--only", rel, check=False)
        pushed = _git(root, "push", remote, f"HEAD:{branch}", check=False)
        if pushed.returncode == 0:
            return True
        rb = _git(root, "pull", "--rebase", remote, branch, check=False)
        if rb.returncode != 0:  # add/add 충돌 = 남이 같은 파일을 먼저 올렸다
            _git(root, "rebase", "--abort", check=False)
            _git(root, "reset", "--hard", f"{remote}/{branch}", check=False)
            return False
        # 충돌 없이 얹혔다면 다시 push 를 시도하기 전에 남의 점유가 생겼는지 루프 머리에서 확인한다
    return False


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, folder = argv[1], argv[2]
    if cmd == "status":
        for kind, s, st, c in status(folder):
            print(f"{kind}.{s}\t{st}\t{(c or {}).get('worker', '')}")
        return 0
    if cmd == "next":
        s = next_shard(folder, argv[3])
        print(s or "NONE")
        return 0
    if cmd == "desk":
        if _result_done(folder, "desk", "desk"):
            print("done")
        else:
            print(_claim_state(folder, "desk", "desk", time.time())[0])
        return 0
    if cmd == "claim":
        return 0 if claim(folder, argv[3], argv[4], argv[5]) else 1
    if cmd == "check":
        rest = argv[3:]
        hhmm = None
        if "--until" in rest:
            k = rest.index("--until")
            hhmm = rest[k + 1]
            rest = rest[:k] + rest[k + 2:]
        kind, worker = rest[0], (rest[1] if len(rest) > 1 else None)
        try:  # 남의 결과·점유를 새로 받는다(실패해도 로컬 상태로 판단)
            root = _repo_root(folder)
            branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            _git(root, "pull", "--rebase", "origin", branch, check=False)
        except (OSError, subprocess.CalledProcessError):
            pass
        now = time.time()
        ok, line = exit_check(folder, kind, worker, until_ts(hhmm, now), now)
        print(line)
        return 0 if ok else 1
    print("모르는 명령:", cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
