# -*- coding: utf-8 -*-
"""클라우드 요청 마커 검증 — grm-cloud-request.yml 이 부른다.

클라우드 세션은 /actions 가 막혀 있어 워크플로를 직접 깨우지 못한다. 대신 브랜치 `claude/request-*` 에
`company/requests/<id>.json` 을 push 하면 워크플로가 이 파일을 검증한 뒤 **허용목록의 워크플로만** 깨운다.
여기서 하나라도 어긋나면 거부한다(= 아무것도 실행하지 않는다). 허용목록은 멱등·무해한 것만:
발송·머지·발행 승인·DB 쓰기류는 넣지 않는다.

요청 형식: {"id": "<파일명과 같음>", "action": "<허용 동작>", "args": {<그 동작이 정한 키만>}}
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
}


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


def main(argv):
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
    print(json.dumps({"ok": True, "action": action, "workflow": workflow, "args": args},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
