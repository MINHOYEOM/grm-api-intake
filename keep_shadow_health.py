#!/usr/bin/env python3
"""수집 health JSON 의 `store`(그림자 판정 기록)를 아티팩트 폴더에 남기고 요약을 출력한다.

    python keep_shadow_health.py grm-health.json _queue_shadow >> "$GITHUB_STEP_SUMMARY"

중복 판정 그림자(`store.dedup_shadow`)·무음 감시 그림자(`store.monitor_shadow.silence`)·대기열 요약
(`store.queue`)은 health JSON 에만 남는다. 그 파일은 아티팩트로 올라가지 않아 실행이 끝나면 읽을 곳이
없었다(2026-11-13 첫 그림자 실행). 여기서 `store` 만 떼어 `<폴더>/store_health_<날짜>.json` 으로 쓴다.
파일 이름은 브랜치 사본 워크플로의 glob(`handoff_db_*`·`brief_web_*`)에 걸리지 않는다.
표준 출력은 실행 요약에 붙일 마크다운이다. 읽기만 한다 — 판정을 바꾸지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def summary_md(run_date: str, store: dict[str, Any]) -> str:
    lines = [f"## 그림자 판정 기록 — {run_date}", "", f"- 중복 판정 기준: `{store.get('dedup_mode', '?')}`"]
    dd = store.get("dedup_shadow")
    if isinstance(dd, dict):
        if dd.get("error"):
            lines.append(f"- 중복 판정 그림자: 조회 실패({dd['error']})")
        else:
            db = dd.get("db") or {}
            lines.append(
                f"- 중복 판정 그림자: 수집 {dd.get('collected', 0)} · DB 기준 새 문서 {db.get('new', 0)} · "
                f"이미 받음 {db.get('have', 0)} · 보충 {db.get('heal', 0)} · 기록 이전 {db.get('old', 0)} · "
                f"노션 기준 새 문서 {dd.get('notion_new', 0)} · 노션에서만 새 문서 {dd.get('only_notion_new', 0)} · "
                f"DB 에서만 새 문서 {dd.get('only_db_new', 0)} · 같음 {dd.get('same')}")
            for source, slot in (dd.get("heal_by_source") or {}).items():
                lines.append(f"  - 보충 {source}: {slot.get('count', 0)}건 (예: {', '.join(slot.get('sample') or [])})")
            for label, key in (("노션에서만 새 문서", "only_notion_new_sample"),
                               ("DB 에서만 새 문서", "only_db_new_sample")):
                if dd.get(key):
                    lines.append(f"  - {label} 예: {', '.join(dd[key])}")
    else:
        lines.append("- 중복 판정 그림자: 기록 없음(변수 꺼짐)")
    sil = (store.get("monitor_shadow") or {}).get("silence")
    if isinstance(sil, dict):
        if sil.get("error"):
            lines.append(f"- 무음 감시 그림자: 실패({sil['error']})")
        else:
            lines.append(
                f"- 무음 감시 그림자: 비교 {sil.get('compared', 0)}개 소스 · 노션 기준 무음 {sil.get('silent_notion')} · "
                f"DB 기준 무음 {sil.get('silent_db')} · 판정이 다른 소스 {sil.get('verdict_differs')} · "
                f"비교 안 함 {sil.get('not_compared')} · 대기열 기록 시작 {sil.get('history_start')} · 같음 {sil.get('same')}")
            for d in sil.get("date_differs") or []:
                lines.append(f"  - 마지막 수집일이 다름 {d.get('source')}: 노션 {d.get('notion')} / DB {d.get('db')}")
    else:
        lines.append("- 무음 감시 그림자: 기록 없음(변수 꺼짐)")
    q = store.get("queue")
    if isinstance(q, dict):
        lines.append("- 대기열: " + " · ".join(f"{k} {v}" for k, v in q.items()))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(argv) != 3:
        print("사용: python keep_shadow_health.py <health.json> <폴더>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            health = json.load(f)
    except (OSError, ValueError) as e:
        print(f"health JSON 을 읽을 수 없다: {type(e).__name__}", file=sys.stderr)
        return 1
    store = health.get("store") if isinstance(health, dict) else None
    if not isinstance(store, dict):
        print("health JSON 에 store 가 없다", file=sys.stderr)
        return 1
    run_date = str(health.get("run_date") or health.get("run_date_kst") or "unknown")[:10]
    os.makedirs(argv[2], exist_ok=True)
    out = os.path.join(argv[2], f"store_health_{run_date}.json")
    with open(out, "wb") as f:
        f.write((json.dumps({"run_date": run_date, "store": store}, ensure_ascii=False, indent=1)
                 + "\n").encode("utf-8"))
    print(summary_md(run_date, store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
