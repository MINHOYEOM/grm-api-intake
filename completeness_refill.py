#!/usr/bin/env python3
"""수집 완전성 N3 — 자동 메움 공통 고리(같은 날 번호 지정 재수집 → 재대조).

설계: `_ops/reports/GRM_수집완전성_상시장치_설계_2026-11-26.md` §2 N3.

    매일 수집 → ① 문서 대조(source_reconcile) + ② 칸 대조(field_contract)
              → 놓침·결손 번호(소스별)
              → 번호 지정 재수집(기존 행 교체 가드가 그대로 막는다)
              → 재대조(같은 날 2차)
              → 남은 것만 N6 일보로

한 번의 실행 = 한 회차(pass). 워크플로가 원천 대조 직후 한 번, 그 뒤 매시 한 번(23:00 KST 마감까지) 부른다.
회차 사이 상태는 `refill-state.json`(직전 실행 아티팩트)로 넘긴다. 상태에는 소스별 놓침·결손 번호와
첫 관측일이 있다 — 그 회차에 대조나 칸 대조가 없으면 상태의 번호를 그대로 이어 간다(조용히 지우지 않는다).

소스별 재수집 경로(`PATHS`)
  · run      — 이 잡 안에서 바로 돌리고 같은 회차에 재대조한다(WL `backfill_wl_body_recovery.py --ids`, #2009).
  · dispatch — 그 소스 전용 워크플로를 부른다(483 OCR 판독·nedrug KR egress 처럼 환경이 따로 필요).
               결과는 다음 회차 재대조·다음 날 칸 대조가 본다.
               ★GitHub concurrency 는 대기열이 1칸이라 같은 그룹에 두 번 넣으면 앞의 대기 실행이 취소된다.
                 그래서 소스마다 하루 한 번, 한 회차에 그룹당 하나, 그 워크플로가 대기·실행 중이면 부르지 않는다.
  · 경로 없음 — 재수집 수단이 아직 없는 소스. '사람 필요'로 남긴다.

재시도 규칙(설계 §N3)
  · 횟수 상한 없음. run 경로는 그날 남은 것이 0이 될 때까지 회차마다 다시 한다.
  · source_unreachable 이면 그 원천을 짧게 두드리지 않는다: 1h → 2h → 4h … 로 간격을 늘리고 시도마다 기록한다.
  · 가드(본문 축소·판정 후퇴·위반항목 감소)에 걸린 번호는 기계 재시도로 안 풀린다 → 그날 다시 시도하지 않고 사람 필요.
  · 날이 바뀌어도 남은 번호는 이월되고(첫 관측일 유지) 다음 날 첫 회차 대상이 된다.

쓰기: `--apply` 일 때만 재수집 도구에 apply 를 넘기고 워크플로를 부른다. 없으면 run 경로는 dry 로 돌고
dispatch 는 계획만 남긴다. 이 스크립트 자신은 DB 에 쓰지 않는다 — 쓰기는 각 재수집 도구의 기존 가드 경로가 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = "grm-completeness-refill/v1"
KST = timezone(timedelta(hours=9))
DAY_CUTOFF_HOUR = 23                       # 그날 마감(23:00 KST)
BACKOFF_BASE = timedelta(hours=1)          # 원천 불가 간격 1h → 2h → 4h …
BACKOFF_MAX = timedelta(hours=8)
ID_LIMIT = 200                             # 출력에 싣는 번호 수(상태에는 전부)

# 재수집 도구 리포트의 실패 사유 중 '기계 재시도로 안 풀리는' 것 — 가드.
GUARD_REASONS = frozenset({
    "violations_shrunk", "body_shrunk", "length_regress_blocked", "assess_regress_blocked",
    "verdict_regressed", "observations_shrunk",
})

# 원천 대조 판정 중 '판정을 못 한' 것 — 볼 수 없는 것은 받을 수도 없다. 간격을 늘려 다시 대조한다.
UNREACHABLE = frozenset({"source_unreachable", "store_unreachable"})

DONE, PENDING, HUMAN, WAIT = "done", "pending", "human", "wait"


@dataclass(frozen=True)
class RefillPath:
    kind: str                          # run | dispatch
    workflow: str = ""                 # dispatch 대상
    group: str = ""                    # 그 워크플로의 concurrency 그룹(같은 회차에 그룹당 하나)
    cap: int = 50                      # 한 번에 넘기는 번호 수(나머지는 다음 회차·다음 날)
    missed_ok: bool = True             # DB 에 행이 없는 놓침도 넣을 수 있나


# 소스 키 → 경로. 키는 source_reconcile.SPECS 의 key 를 쓰고, 칸 대조 계약 키는 CONTRACT_TO_PATH 로 옮긴다.
PATHS: dict[str, RefillPath] = {
    # WL: 놓침(DB 에 없음)은 새로 넣고, 결손(본문·위반항목)은 제자리 교체 — #2009 `--ids`.
    "wl": RefillPath("run", cap=40),
    # 483: 지목 문서를 다시 판독해 관찰이 늘 때만 교체(named). 행 자체가 없는 놓침은 못 넣는다.
    "fda483": RefillPath("dispatch", workflow="grm-fda483-ocr-recovery.yml", group="fda483-ocr-backfill",
                         cap=30, missed_ok=False),
    # 식약처 GMP 실사 첨부: KR egress 가 필요해 전용 워크플로(§4). 있는 행만.
    "mfds_gmp": RefillPath("dispatch", workflow="grm-mfds-recollect-attachment.yml", group="mfds-backfill",
                           cap=50, missed_ok=False),
    # 식약처 행정처분·회수(data.go.kr): 놓친 날짜로 좁힌 창 백필.
    "mfds_admin": RefillPath("dispatch", workflow="grm-mfds-backfill.yml", group="mfds-backfill", cap=500),
    "mfds_recall": RefillPath("dispatch", workflow="grm-mfds-backfill.yml", group="mfds-backfill", cap=500),
}

CONTRACT_TO_PATH = {"fda483": "fda483", "fda_wl": "wl", "mfds_gmp": "mfds_gmp"}
_MFDS_BACKFILL_SOURCE = {"mfds_admin": "admin-action", "mfds_recall": "recall"}
# 시간 회차에 source_reconcile 로 다시 대조할 소스(대조 키 = 경로 키).
RECONCILE_KEYS = ("fda483", "mfds_admin", "mfds_recall", "wl")


def now_kst() -> datetime:
    return datetime.now(KST)


def _iso(ts: datetime) -> str:
    return ts.astimezone(KST).isoformat(timespec="minutes")


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- 이번 회차 관측

@dataclass
class Observed:
    """한 소스에 대해 이번 회차가 본 것. None 은 '이번에 안 봄'(상태의 값을 잇는다)."""
    key: str
    missed: dict[str, dict[str, str]] | None = None     # 번호 → 원천 요약(published 등)
    incomplete: list[str] | None = None
    verdict: str = ""
    error: str = ""


def observe(reconcile: dict[str, Any] | None, fields: dict[str, Any] | None) -> dict[str, Observed]:
    out: dict[str, Observed] = {}
    for s in (reconcile or {}).get("sources") or []:
        if not isinstance(s, dict) or not s.get("key"):
            continue
        o = out.setdefault(str(s["key"]), Observed(str(s["key"])))
        o.verdict, o.error = str(s.get("verdict") or ""), str(s.get("error") or "")
        if o.verdict not in UNREACHABLE:
            o.missed = {str(m["document_id"]): {k: str(v) for k, v in m.items() if isinstance(v, str)}
                        for m in s.get("missed") or [] if isinstance(m, dict) and m.get("document_id")}
    for contract, body in ((fields or {}).get("sources") or {}).items():
        key = CONTRACT_TO_PATH.get(contract)
        if key and isinstance(body, dict):
            out.setdefault(key, Observed(key)).incomplete = [str(i) for i in body.get("incomplete_ids") or [] if i]
    return out


# --------------------------------------------------------------------------- 상태

def fresh_state(day: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "day": day, "sources": {}}


def carry_state(prev: dict[str, Any] | None, day: str) -> dict[str, Any]:
    """직전 상태를 오늘로 넘긴다. 날이 바뀌면 번호·첫 관측일은 이월하고 그날 한정 기록(가드·간격·호출)은 푼다."""
    if not prev or prev.get("schema") != SCHEMA:
        return fresh_state(day)
    state = json.loads(json.dumps(prev))
    if state.get("day") != day:
        for src in state.get("sources", {}).values():
            for k in ("guard_ids", "next_try", "dispatched_day", "attempt_log"):
                src.pop(k, None)
            src["unreachable_attempts"] = 0
        state["day"] = day
    return state


def _src(state: dict[str, Any], key: str) -> dict[str, Any]:
    src = state.setdefault("sources", {}).setdefault(key, {})
    src.setdefault("missed", {})
    src.setdefault("incomplete", [])
    src.setdefault("first_seen", {})
    src.setdefault("unreachable_attempts", 0)
    return src


def pending_ids(src: dict[str, Any]) -> list[str]:
    out = list(src.get("missed", {}))
    out += [i for i in src.get("incomplete", []) if i not in src.get("missed", {})]
    return out


def absorb(now: datetime, state: dict[str, Any], seen: dict[str, Observed]) -> None:
    """이번 회차 관측을 상태에 반영한다. 본 쪽만 바꾸고 안 본 쪽(놓침/결손)은 잇는다."""
    day = now.astimezone(KST).date().isoformat()
    for key, o in seen.items():
        src = _src(state, key)
        if o.verdict in UNREACHABLE:
            attempts = int(src.get("unreachable_attempts", 0)) + 1
            src["unreachable_attempts"] = attempts
            src["next_try"] = _iso(now + min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_MAX))
            src.setdefault("attempt_log", []).append({"at": _iso(now), "verdict": o.verdict, "error": o.error[:200]})
            src["unreachable"] = {"verdict": o.verdict, "error": o.error[:200]}
            continue
        if o.verdict:
            src["unreachable_attempts"] = 0
            src.pop("next_try", None)
            src.pop("unreachable", None)
        if o.missed is not None:
            src["missed"] = o.missed
        if o.incomplete is not None:
            src["incomplete"] = o.incomplete
        ids = set(pending_ids(src))
        first = src["first_seen"]
        for i in ids:
            first.setdefault(i, day)
        for gone in [i for i in first if i not in ids]:
            first.pop(gone)


# --------------------------------------------------------------------------- 계획

@dataclass
class Action:
    key: str
    kind: str                       # run | dispatch
    ids: list[str]
    workflow: str = ""
    inputs: dict[str, str] = field(default_factory=dict)


@dataclass
class Line:
    key: str
    state: str                      # done | pending | human | wait
    reason: str
    ids: list[str] = field(default_factory=list)


def dispatch_inputs(key: str, ids: list[str], src: dict[str, Any], apply: bool) -> dict[str, str]:
    flag = "true" if apply else "false"
    if key == "fda483":
        return {"mode": "named", "target": "fda483", "doc_ids": ",".join(ids), "apply": flag}
    if key == "mfds_gmp":
        return {"ids": ",".join(ids), "apply": flag}
    if key in _MFDS_BACKFILL_SOURCE:
        dates = sorted(d for i in ids if (d := src["missed"].get(i, {}).get("published")))
        if not dates:
            return {}
        return {"source": _MFDS_BACKFILL_SOURCE[key], "from_date": dates[0], "to_date": dates[-1],
                "dry_run": "false" if apply else "true"}
    return {}


def plan(now: datetime, state: dict[str, Any], *, apply: bool) -> tuple[list[Action], list[Line]]:
    """한 회차의 할 일과 소스별 줄(상태만 보고 정한다)."""
    day = now.astimezone(KST).date().isoformat()
    actions: list[Action] = []
    lines: list[Line] = []
    groups_used: set[str] = set()
    for key in sorted(state.get("sources", {})):
        src = state["sources"][key]
        ids = pending_ids(src)
        if src.get("unreachable"):
            u = src["unreachable"]
            lines.append(Line(key, WAIT, f"{'원천' if u['verdict'] == 'source_unreachable' else '저장소'} 불가 "
                                         f"{src.get('unreachable_attempts', 0)}회째 — 다음 대조 {src.get('next_try', '')}"
                                         + (f" · {u['error'][:120]}" if u.get("error") else ""), ids))
            continue
        if not ids:
            lines.append(Line(key, DONE, "남은 번호 없음"))
            continue
        path = PATHS.get(key)
        if path is None:
            lines.append(Line(key, HUMAN, "재수집 경로 없음 — 사람이 받아야 함", ids))
            continue
        guard = set(src.get("guard_ids", []))
        if blocked := [i for i in ids if i in guard]:
            lines.append(Line(key, HUMAN, "가드에 걸림(본문 축소·판정 후퇴) — 기계 재시도 안 함", blocked))
        todo = [i for i in ids if i not in guard]
        if not path.missed_ok:
            unfit = [i for i in todo if i in src["missed"] and i not in src["incomplete"]]
            if unfit:
                lines.append(Line(key, HUMAN, "DB 에 행이 없는 놓침 — 이 경로는 있는 행만 다시 받음", unfit))
            todo = [i for i in todo if i not in unfit]
        if not todo:
            continue
        batch, rest = todo[:path.cap], todo[path.cap:]
        if path.kind == "dispatch":
            if src.get("dispatched_day") == day:
                lines.append(Line(key, PENDING, f"오늘 {path.workflow} 재수집을 이미 요청함 — 결과는 다음 대조가 봄", todo))
                continue
            if path.group in groups_used:
                lines.append(Line(key, PENDING, f"같은 실행 그룹({path.group})이 이번 회차에 이미 쓰임 — 다음 회차", todo))
                continue
            inputs = dispatch_inputs(key, batch, src, apply)
            if not inputs:
                lines.append(Line(key, HUMAN, "재수집 입력을 만들 수 없음(놓친 날짜 없음)", batch))
                continue
            groups_used.add(path.group)
            actions.append(Action(key, "dispatch", batch, path.workflow, inputs))
        else:
            actions.append(Action(key, "run", batch))
        if rest:
            lines.append(Line(key, PENDING, f"한 번에 {path.cap}건 상한 — 남은 것은 다음 회차", rest))
    return actions, lines


# --------------------------------------------------------------------------- 실행

Runner = Callable[[list[str]], int]
Dispatcher = Callable[[str, dict[str, str]], str]          # "sent" | "busy" | "failed"
Reconciler = Callable[[list[str]], "dict[str, Any] | None"]


# 노션 은퇴 스위치 — off 면 자식 프로세스(재수집 도구·재대조)에 노션 열쇠를 넘기지 않는다. 재대조
# (source_reconcile.py)는 스스로 off 를 알지만, 열쇠 자체를 빼 두면 이 고리가 부르는 어떤 도구도 노션을 못 부른다.
NOTION_ENV_KEYS = ("NOTION_TOKEN", "NOTION_DATABASE_ID")


def child_env() -> dict[str, str]:
    from grm_queue import notion_intake_off

    env = dict(os.environ)
    if notion_intake_off():
        for k in NOTION_ENV_KEYS:
            env.pop(k, None)
    return env


def _default_runner(argv: list[str]) -> int:
    return subprocess.run([sys.executable, *argv], check=False, env=child_env()).returncode


def _gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    repo = os.environ.get("GITHUB_REPOSITORY")
    return subprocess.run(["gh", *args, *(["-R", repo] if repo else [])], check=False,
                          capture_output=True, text=True, encoding="utf-8")


def _default_dispatcher(workflow: str, inputs: dict[str, str]) -> str:
    busy = _gh(["run", "list", "--workflow", workflow, "--limit", "20", "--json", "status"])
    if busy.returncode != 0:
        return "failed"
    try:
        statuses = {r.get("status") for r in json.loads(busy.stdout or "[]")}
    except ValueError:
        return "failed"
    if statuses & {"queued", "in_progress", "waiting", "pending", "requested"}:
        return "busy"
    args = ["workflow", "run", workflow]
    for k, v in inputs.items():
        args += ["-f", f"{k}={v}"]
    return "sent" if _gh(args).returncode == 0 else "failed"


def _default_reconciler(keys: list[str]) -> dict[str, Any] | None:
    out = "refill-recheck.json"
    code = subprocess.run([sys.executable, "source_reconcile.py", "--sources", *keys,
                           "--json", out, "--report", "refill-recheck.md"], check=False,
                          env=child_env()).returncode
    return _load_json(out) if code == 0 else None


def run_action(action: Action, *, apply: bool, workdir: Path, runner: Runner) -> dict[str, Any]:
    """run 경로 하나를 돌리고 도구 리포트를 읽는다."""
    report_path = workdir / f"refill-{action.key}.json"
    if action.key != "wl":
        raise ValueError(f"run 경로 없음: {action.key}")
    argv = ["backfill_wl_body_recovery.py", "--ids", *action.ids, "--report-path", str(report_path)]
    if apply:
        argv.append("--apply")
    code = runner(argv)
    rep = _load_json(str(report_path))
    if rep is None:
        return {"exit": code, "report": False, "unresolved": list(action.ids), "guard": [], "reasons": {}}
    reasons = rep.get("failure_reasons") or {}
    return {"exit": code, "report": True, "unresolved": [str(i) for i in rep.get("unresolved_ids") or []],
            "guard": sorted(r for r in reasons if r in GUARD_REASONS), "reasons": reasons}


def execute(now: datetime, actions: list[Action], state: dict[str, Any], *, apply: bool, workdir: Path,
            runner: Runner = _default_runner, dispatcher: Dispatcher = _default_dispatcher,
            reconciler: Reconciler = _default_reconciler) -> tuple[list[dict[str, Any]], list[Line]]:
    """할 일을 돌리고, run 경로는 같은 회차에 재대조한다. 반환: 실행 기록·결과 줄."""
    day = now.astimezone(KST).date().isoformat()
    log: list[dict[str, Any]] = []
    lines: list[Line] = []
    recheck: dict[str, dict[str, Any]] = {}
    for a in actions:
        src = _src(state, a.key)
        if a.kind == "dispatch":
            outcome = dispatcher(a.workflow, a.inputs) if apply else "planned"
            if outcome == "sent":
                src["dispatched_day"] = day
            log.append({"key": a.key, "kind": "dispatch", "workflow": a.workflow, "inputs": a.inputs,
                        "ids": a.ids, "outcome": outcome})
            if outcome == "failed":
                lines.append(Line(a.key, HUMAN, f"재수집 워크플로 호출 실패({a.workflow})", a.ids))
            elif outcome == "busy":
                lines.append(Line(a.key, PENDING, f"{a.workflow} 가 대기·실행 중 — 다음 회차에 요청", a.ids))
            else:
                lines.append(Line(a.key, PENDING, f"{a.workflow} 재수집 "
                                                  + ("요청함" if outcome == "sent" else "계획(쓰기 꺼짐)"), a.ids))
            continue
        result = run_action(a, apply=apply, workdir=workdir, runner=runner)
        log.append({"key": a.key, "kind": "run", "ids": a.ids, "apply": apply, **result})
        if result["guard"]:
            # 도구는 사유를 번호별로 주지 않는다 — 남은 번호 전부를 그날 가드로 묶는다(재시도 안 함·사람 필요).
            src["guard_ids"] = sorted(set(src.get("guard_ids", [])) | set(result["unresolved"]))
            lines.append(Line(a.key, HUMAN, "가드에 걸림: " + ", ".join(result["guard"]), result["unresolved"]))
        if not result["report"]:
            lines.append(Line(a.key, HUMAN, f"재수집 도구가 리포트 없이 끝남(exit {result['exit']})", a.ids))
            continue
        if apply:
            recheck[a.key] = {**result, "ids": set(a.ids)}
        else:
            lines.append(Line(a.key, PENDING, "재수집 dry 만 돌림(쓰기 꺼짐)", a.ids))
    if recheck:
        after = reconciler(sorted(recheck))
        by_key = {s.get("key"): s for s in (after or {}).get("sources") or [] if isinstance(s, dict)}
        for key, result in sorted(recheck.items()):
            src = _src(state, key)
            s = by_key.get(key)
            if s is None:
                lines.append(Line(key, HUMAN, "재대조 실패 — 메웠는지 확인 못 함", pending_ids(src)))
                continue
            # 놓침은 재대조가, 결손(있는 행의 빈칸)은 도구가 남은 번호로 알려 준다.
            unresolved = set(result["unresolved"])
            absorb(now, state, {key: Observed(key, verdict=str(s.get("verdict") or ""), error=str(s.get("error") or ""),
                                              missed=None if s.get("verdict") in UNREACHABLE else {
                                                  str(m["document_id"]): {k: str(v) for k, v in m.items()
                                                                          if isinstance(v, str)}
                                                  for m in s.get("missed") or []
                                                  if isinstance(m, dict) and m.get("document_id")},
                                              incomplete=[i for i in src["incomplete"]
                                                          if i in unresolved or i not in result["ids"]])})
            remain = pending_ids(src)
            if src.get("unreachable"):
                lines.append(Line(key, WAIT, "재대조 때 원천 불가 — 간격을 늘려 다시", remain))
            elif remain:
                lines.append(Line(key, PENDING, f"재수집 뒤에도 {len(remain)}건 남음 — 다음 회차에 다시", remain))
            else:
                lines.append(Line(key, DONE, "재수집 → 재대조 정상"))
    return log, lines


# --------------------------------------------------------------------------- 요약

def merge_lines(*groups: Iterable[Line]) -> list[Line]:
    """소스마다 마지막 판정 줄이 앞선 진행 줄을 덮는다. 사람 필요 줄은 늘 남긴다."""
    final: dict[str, list[Line]] = {}
    for group in groups:
        for line in group:
            cur = final.setdefault(line.key, [])
            if line.state in (DONE, WAIT) or (line.state == PENDING and line.reason.startswith("재수집 뒤에도")):
                cur[:] = [l for l in cur if l.state == HUMAN]
            cur.append(line)
    return [l for key in sorted(final) for l in final[key]]


def build_payload(now: datetime, lines: list[Line], log: list[dict[str, Any]], state: dict[str, Any],
                  *, apply: bool) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "at": _iso(now), "day": state.get("day"), "apply": apply,
        "after_cutoff": now.astimezone(KST).hour >= DAY_CUTOFF_HOUR,
        "lines": [{"key": l.key, "state": l.state, "reason": l.reason, "count": len(l.ids), "ids": l.ids[:ID_LIMIT]}
                  for l in lines],
        "actions": log,
        "remaining": {k: {"count": len(ids), "oldest": min(v["first_seen"].values(), default="")}
                      for k, v in sorted(state.get("sources", {}).items()) if (ids := pending_ids(v))},
    }


_MARK = {DONE: "정상", PENDING: "메우는 중", WAIT: "원천 대기", HUMAN: "사람 필요"}


def render_markdown(payload: dict[str, Any]) -> str:
    out = [f"## 수집 완전성 자동 메움 — {payload['at']}" + ("" if payload["apply"] else " (쓰기 꺼짐: 계획·dry 만)"), ""]
    if not payload["lines"]:
        out.append("남은 번호 없음.")
    for l in payload["lines"]:
        ids = ", ".join(l["ids"][:10]) + (" …" if l["count"] > 10 else "")
        out.append(f"- **{l['key']}** — {_MARK.get(l['state'], l['state'])}"
                   + (f"({l['count']})" if l["count"] else "") + f" · {l['reason']}" + (f" · {ids}" if ids else ""))
    if payload["remaining"]:
        out += ["", "| 소스 | 남은 번호 | 가장 오래된 첫 관측 |", "|---|---:|---|"]
        out += [f"| {k} | {v['count']} | {v['oldest']} |" for k, v in payload["remaining"].items()]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- main

def due_keys(now: datetime, state: dict[str, Any]) -> list[str]:
    """시간 회차에서 다시 대조할 소스 — 남은 놓침이 있거나 원천 불가였고, 간격이 지난 것."""
    out = []
    for key in RECONCILE_KEYS:
        src = state.get("sources", {}).get(key)
        if not src or not (src.get("missed") or src.get("unreachable")):
            continue
        nxt = _parse(src.get("next_try"))
        if nxt is None or nxt <= now:
            out.append(key)
    return sorted(out)


def run_pass(now: datetime, *, reconcile: dict[str, Any] | None, fields: dict[str, Any] | None,
             prev_state: dict[str, Any] | None, apply: bool, workdir: Path,
             runner: Runner = _default_runner, dispatcher: Dispatcher = _default_dispatcher,
             reconciler: Reconciler = _default_reconciler) -> tuple[dict[str, Any], dict[str, Any]]:
    """한 회차 전체. 반환: (출력 payload, 다음 회차 상태)."""
    day = now.astimezone(KST).date().isoformat()
    state = carry_state(prev_state, day)
    if reconcile is not None and reconcile.get("run_date") != day:
        reconcile = None                       # 오늘 대조가 아니면 쓰지 않는다(N6 가 '대조 안 돎'을 따로 알린다)
    if reconcile is None:
        keys = due_keys(now, state)
        reconcile = reconciler(keys) if keys else None
    absorb(now, state, observe(reconcile, fields))
    actions, planned = plan(now, state, apply=apply)
    log, done = execute(now, actions, state, apply=apply, workdir=workdir,
                        runner=runner, dispatcher=dispatcher, reconciler=reconciler)
    return build_payload(now, merge_lines(planned, done), log, state, apply=apply), state


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="수집 완전성 N3 자동 메움 한 회차")
    ap.add_argument("--reconcile", help="원천 대조 결과(source-reconcile.json). 오늘 것이 아니거나 없으면 남은 소스만 다시 대조")
    ap.add_argument("--fields", help="칸 대조 결과(field-contract.json). 없으면 직전 결손 번호를 잇는다")
    ap.add_argument("--state", help="직전 회차 상태(refill-state.json)")
    ap.add_argument("--out-state", default="refill-state.json")
    ap.add_argument("--json", default="completeness-refill.json")
    ap.add_argument("--report", default="completeness-refill.md")
    ap.add_argument("--apply", action="store_true", help="재수집 도구에 apply 를 넘기고 워크플로를 부른다")
    args = ap.parse_args(argv)

    payload, state = run_pass(now_kst(), reconcile=_load_json(args.reconcile), fields=_load_json(args.fields),
                              prev_state=_load_json(args.state), apply=args.apply, workdir=Path("."))
    Path(args.out_state).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = render_markdown(payload)
    Path(args.report).write_text(md, encoding="utf-8")
    print(md)
    return 0      # 비차단 — 판정은 JSON·N6 일보가 말한다


if __name__ == "__main__":
    sys.exit(main())
