# -*- coding: utf-8 -*-
"""클라우드 요청 마커 검증 — grm-cloud-request.yml 이 부른다.

클라우드 세션은 /actions 가 막혀 있어 워크플로를 직접 깨우지 못한다. 대신 브랜치 `claude/request-*` 에
`company/requests/<id>.json` 을 push 하면 워크플로가 이 파일을 검증한 뒤 **허용목록의 워크플로만** 깨운다.
여기서 하나라도 어긋나면 거부한다(= 아무것도 실행하지 않는다). 허용목록은 멱등·무해한 것만:
발송·머지·발행 승인은 넣지 않는다. 쓰기가 있는 것은 intake(일일 수집 — 정기 슬롯과 같은 실행·같은 기본값,
재실행 안전·동결 규칙은 수집기 쪽) 하나이고, 그것도 오늘 handoff 가 이미 있으면 깨우지 않는다.

요청 형식: {"id": "<파일명과 같음>", "action": "<허용 동작>", "args": {<그 동작이 정한 키만>}}
예(수집): 브랜치 claude/request-2026-11-23-intake · 파일 company/requests/20261123-0500-intake.json
         {"id": "20261123-0500-intake", "action": "intake", "args": {}}
"""
import datetime
import json
import os
import re
import sys

ID_RE = re.compile(r"^[0-9]{8}-[0-9]{4}-[a-z0-9][a-z0-9-]{0,30}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
RUN_ID_RE = re.compile(r"^[0-9]{6,15}$")
MAX_BYTES = 2000
DATE_SLACK_DAYS = (-21, 1)  # 오늘 기준 이 범위의 날짜만

# action -> (워크플로 파일, {인자 이름: (형식, 필수)})
ACTIONS = {
    "ping": (None, {}),
    "ops-status-refresh": ("grm-ops-status.yml", {}),
    "delta-bridge": ("grm-delta-bridge.yml", {"publish_date": ("date", True)}),
    "web-publish": ("grm-web-publish.yml",
                    {"publish_date": ("date", True), "intake_run_id": ("run_id", False)}),
    "scaffold-mirror": ("grm-scaffold-mirror.yml", {"intake_run_id": ("run_id", True)}),
    "wl-restore-plan": ("grm-wl-truncation-restore-plan.yml", {}),
    # 일일 수집 — 월요일 크론이 몇 시간씩 늦을 때 편집팀(07:20) 전에 handoff 를 만들려고. 입력 없이 깨운다
    # (스케줄 실행과 같은 기본값: 7일 창·전체 소스·vars 상속). 오늘 handoff 가 이미 있거나 오늘 수집이
    # 돌고 있거나 성공했으면 깨우지 않는다(intake_skip_reason). 사본(grm-queue-handoff-mirror)이
    # workflow_run 으로 따라 돌아야 하므로 소유자 PAT 로 깨운다(PAT_ACTIONS).
    "intake": ("grm-intake.yml", {}),
    # 사본만 다시 — 수집 뒤 사본이 안 돌았을 때(PAT 없이 깨웠거나 사본 실패). 그 run 의 아티팩트를 복사할 뿐이다.
    "queue-handoff-mirror": ("grm-queue-handoff-mirror.yml", {"intake_run_id": ("run_id", True)}),
}

# 소유자 PAT(secrets.ADMIN_GITHUB_ACTIONS_TOKEN)로 깨우는 동작 — 깨운 실행 뒤에 workflow_run 후속이 붙는 것.
# GITHUB_TOKEN 으로 깨운 실행은 github-actions[bot] 이 일으킨 것이 되어 후속 트리거가 보장되지 않는다
# (GitHub 제약). 수동 dispatch(소유자)한 수집 뒤에는 사본이 돈다(11-17 run 44102656781 → 44105563674).
PAT_ACTIONS = frozenset({"intake"})
KST = datetime.timezone(datetime.timedelta(hours=9))
_LIVE_STATUSES = ("queued", "in_progress", "waiting", "pending", "requested")


class Rejected(ValueError):
    pass


def _check_value(kind, value, today):
    if not isinstance(value, str):
        raise Rejected("인자는 문자열이어야 한다")
    if kind == "run_id":
        if not RUN_ID_RE.match(value):
            raise Rejected("run id 형식 아님")
        return value
    if not DATE_RE.match(value):
        raise Rejected("날짜 형식 아님")
    try:
        d = datetime.date.fromisoformat(value)
    except ValueError:
        raise Rejected("없는 날짜")
    lo, hi = DATE_SLACK_DAYS
    if not (today + datetime.timedelta(days=lo) <= d <= today + datetime.timedelta(days=hi)):
        raise Rejected("날짜가 허용 범위 밖")
    return value


def validate(raw, req_id, today):
    """(워크플로 파일|None, action, {인자}) 를 돌려준다. 어긋나면 Rejected."""
    if not ID_RE.match(req_id or ""):
        raise Rejected("요청 id 형식 아님")
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise Rejected("요청이 너무 큼")
    try:
        obj = json.loads(raw)
    except ValueError:
        raise Rejected("JSON 아님")
    if not isinstance(obj, dict) or set(obj) - {"id", "action", "args"} or "action" not in obj:
        raise Rejected("키는 id·action·args 만")
    if obj.get("id") != req_id:
        raise Rejected("id 가 파일명과 다름")
    action = obj["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise Rejected("허용되지 않은 동작")
    workflow, spec = ACTIONS[action]
    args = obj.get("args", {})
    if not isinstance(args, dict) or set(args) - set(spec):
        raise Rejected("허용되지 않은 인자")
    out = {}
    for name, (kind, required) in spec.items():
        if name in args:
            out[name] = _check_value(kind, args[name], today)
        elif required:
            raise Rejected("필수 인자 없음: " + name)
    return workflow, action, out


RATE_PER_HOUR = 10
KEEP_RESULTS = 60


def check_replay(results_dir, req_id, now):
    """같은 id 를 두 번 처리하지 않고, 최근 1시간에 처리한 요청이 너무 많으면 거부한다."""
    if os.path.exists(os.path.join(results_dir, req_id + ".json")):
        raise Rejected("이미 처리된 요청 id")
    recent = 0
    if os.path.isdir(results_dir):
        for name in os.listdir(results_dir):
            try:
                with open(os.path.join(results_dir, name), encoding="utf-8") as f:
                    at = datetime.datetime.strptime(json.load(f)["at"], "%Y-%m-%dT%H:%M:%SZ")
            except (OSError, ValueError, KeyError):
                continue
            if (now.replace(tzinfo=None) - at).total_seconds() < 3600:
                recent += 1
    if recent >= RATE_PER_HOUR:
        raise Rejected("최근 1시간 요청이 너무 많음")


def write_result(results_dir, req_id, verdict, outcome, sha, now):
    """결과 파일을 쓰고 오래된 것을 정리한다. 클라우드는 이 파일로 처리 여부를 안다."""
    os.makedirs(results_dir, exist_ok=True)
    body = {"id": req_id, "outcome": outcome, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": sha[:12]}
    body.update({k: v for k, v in verdict.items() if k in ("action", "args", "reason")})
    with open(os.path.join(results_dir, req_id + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
        f.write("\n")
    names = sorted(n for n in os.listdir(results_dir) if n.endswith(".json"))
    for old in names[:-KEEP_RESULTS]:
        os.remove(os.path.join(results_dir, old))


def intake_skip_reason(runs, handoff_names, now):
    """수집을 다시 깨울 필요가 없으면 그 이유를, 깨워야 하면 None 을 돌려준다.

    runs: grm-intake.yml 의 main 실행 목록(REST `workflow_runs` 원소 — id·status·conclusion·created_at).
    handoff_names: claude/queue-handoff 브랜치의 파일 이름들. now: 시각(tz 포함).
    · 오늘(KST) handoff 사본이 이미 있다 → 건너뜀(편집팀이 읽을 것이 있다).
    · 오늘(KST) 시작된 수집이 돌고 있거나 기다리는 중이다 → 건너뜀(곧 handoff 가 생긴다 — 줄 세우지 않는다).
    · 오늘(KST) 시작된 수집이 성공으로 끝났다 → 건너뜀.
    실패로 끝난 실행만 있고 사본이 없으면 다시 깨운다(handoff 를 못 만들었다는 뜻)."""
    today = now.astimezone(KST).date().isoformat()
    if "handoff_%s.json" % today in handoff_names:
        return "오늘 handoff 사본이 이미 있음(handoff_%s.json)" % today
    for r in runs:
        try:
            created = datetime.datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, TypeError, ValueError):
            continue
        if created.replace(tzinfo=datetime.timezone.utc).astimezone(KST).date().isoformat() != today:
            continue
        if r.get("status") in _LIVE_STATUSES:
            return "오늘 시작된 수집 run %s 이 진행 중(%s)" % (r.get("id"), r.get("status"))
        if r.get("status") == "completed" and r.get("conclusion") == "success":
            return "오늘 성공한 수집 run %s 이 있음" % r.get("id")
    return None


def main(argv):
    # intake-skip <runs.json> <handoff_names.txt> -> 건너뛸 이유 한 줄(없으면 빈 출력). 읽기 실패는 빈 목록 —
    # 확인을 못 하면 깨운다(수집 재실행은 안전하고 concurrency grm-intake 로 줄을 선다).
    if len(argv) == 4 and argv[1] == "intake-skip":
        try:
            with open(argv[2], encoding="utf-8") as f:
                runs = json.load(f).get("workflow_runs") or []
        except (OSError, ValueError, AttributeError):
            runs = []
        try:
            with open(argv[3], encoding="utf-8") as f:
                names = {ln.strip() for ln in f if ln.strip()}
        except OSError:
            names = set()
        print(intake_skip_reason(runs, names, datetime.datetime.now(datetime.timezone.utc)) or "")
        return 0
    # check <request.json> <id> <results_dir> -> 판정 JSON (exit 0 통과 · 1 거부 · 3 이미 처리/과다)
    # result <results_dir> <id> <outcome> <sha>  (verdict JSON 은 stdin) -> 결과 파일 기록
    if len(argv) == 6 and argv[1] == "result":
        verdict = json.loads(sys.stdin.read() or "{}")
        write_result(argv[2], argv[3], verdict, argv[4], argv[5],
                     datetime.datetime.now(datetime.timezone.utc))
        return 0
    if len(argv) != 5 or argv[1] != "check":
        print("usage: cloud_request.py check <request.json> <id> <results_dir>", file=sys.stderr)
        return 2
    now = datetime.datetime.now(datetime.timezone.utc)
    with open(argv[2], encoding="utf-8") as f:
        raw = f.read()
    try:
        workflow, action, args = validate(raw, argv[3], now.date())
    except Rejected as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
        return 1
    try:
        check_replay(argv[4], argv[3], now)
    except Rejected as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps({"ok": True, "action": action, "workflow": workflow, "args": args,
                      "pat": action in PAT_ACTIONS}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
