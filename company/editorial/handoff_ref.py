# -*- coding: utf-8 -*-
"""handoff 파일의 날짜·지문·행 수를 한 줄 JSON 으로 낸다 — `desk.json` 의 `handoff_ref` 값.

    python company/editorial/handoff_ref.py _handoff/handoff_2026-11-23.json
    → {"date": "2026-11-23", "sha256": "…", "rows": 123}

표준 라이브러리만 쓴다(클라우드 직무가 의존성 설치 없이 돌린다). 지문 산식은 수집기가 DB 에 기록하는
`grm_queue.payload_sha256` 과 **같아야 한다** — 생성 시각(`generated_at_kst`)을 뺀 payload 를 키 정렬·
공백 없는 JSON 으로 만들어 sha256. `tests/test_grm_queue.py` 가 두 산식이 같은지 잠근다.
읽을 수 없으면 exit 1(메시지는 stderr).
"""
import hashlib
import json
import sys


def handoff_ref(payload):
    body = {k: v for k, v in payload.items() if k != "generated_at_kst"}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"date": str(payload.get("run_date_kst") or ""),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "rows": len(payload.get("rows") or [])}


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(argv) != 2:
        print("사용: python company/editorial/handoff_ref.py <handoff 파일>", file=sys.stderr)
        return 1
    try:
        with open(argv[1], encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        print(f"handoff 파일을 읽을 수 없다: {argv[1]} ({type(e).__name__})", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list) \
            or not payload.get("run_date_kst"):
        print(f"handoff payload 가 아니다: {argv[1]}", file=sys.stderr)
        return 1
    print(json.dumps(handoff_ref(payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
