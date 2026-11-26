#!/usr/bin/env python3
"""[2026-11-26] 483 상세 재번역(stale) 대기열 일일 감시 — 읽기 전용, 알림만.

왜: 원문 상세를 다시 읽어 고치면(`backfill_483_detail_refresh` — 쪽 끝 절단 소급 #1966 등) 국문이 이미 있는
행의 원문이 바뀌어 재번역이 필요해진다. 그 대기열을 만드는 `findings_detail_export.py --mode stale` 은 예약
실행·일일 번역 Routine 어디에도 연결돼 있지 않아, 사람이 부르지 않으면 **재번역이 아무도 모르게 멈춘다**.
번역 배치를 자동으로 만들지는 않는다 — 매일 stale 행 수를 세고, 줄지 않으면 알린다.

무엇을 세나: `findings_detail_export.fetch_inputs` + `build_queue(stale_only=True)` 를 **그대로** 부른다(같은
모집단·같은 짝 규칙·같은 산문 증가 조건). 규칙을 베끼지 않는다.

경보(`evaluate_trend`, 순수 함수): 오늘 stale > 0 이고, 3일 이상 전 기록(그중 가장 최근) 대비 줄지 않았으면
breach. 기록이 3일치가 안 되면 판정하지 않는다(warming_up — 조용한 초록이 아니라 상태로 남긴다).
기록은 워크플로가 Actions 캐시로 날마다 넘겨준다(`--history`). 캐시를 잃으면 다시 쌓는다(보고서에 표시).

계약: DB 쓰기 0. service-role 키는 보고서·로그에 넣지 않는다. 종료 코드 0 정상·warming_up / 1 breach·오류 /
2 자격 증명 없음.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STALE_DAYS = 3
HISTORY_KEEP = 30


def count_stale(findings: list[dict[str, Any]], raws: list[dict[str, Any]]) -> dict[str, int]:
    """재번역 대기열 크기(순수 함수) — export 의 stale 대기열과 같은 함수."""
    import findings_detail_export as fde
    queue, stats = fde.build_queue(findings, raws, stale_only=True)
    return {"stale": len(queue), "junk_only_skipped": int(stats.get("junk_only_skipped", 0)),
            "stale_chars": int(stats.get("chars", 0)), "with_ko": len(findings)}


def merge_history(history: list[dict[str, Any]], today: date, counts: dict[str, int]) -> list[dict[str, Any]]:
    """오늘 기록을 넣은(같은 날이면 바꾼) 날짜순 기록, 최근 `HISTORY_KEEP` 일(순수 함수)."""
    day = today.isoformat()
    rows = [h for h in history if isinstance(h, dict) and str(h.get("date") or "") != day and h.get("date")]
    rows.append({"date": day, "stale": int(counts["stale"]),
                 "junk_only_skipped": int(counts.get("junk_only_skipped", 0))})
    rows.sort(key=lambda h: str(h["date"]))
    return rows[-HISTORY_KEEP:]


def evaluate_trend(history: list[dict[str, Any]], today: date, *, days: int = STALE_DAYS) -> dict[str, Any]:
    """stale 이 `days` 일 넘게 줄지 않았나(순수 함수). history 는 오늘 기록을 포함한 날짜순 목록."""
    by_day = {str(h["date"]): int(h.get("stale") or 0) for h in history if h.get("date")}
    now = by_day.get(today.isoformat())
    if now is None:
        return {"status": "error", "reason": "no-today-entry", "breaches": []}
    cutoff = (today - timedelta(days=days)).isoformat()
    older = sorted(d for d in by_day if d <= cutoff)
    if now == 0:
        return {"status": "ok", "stale": 0, "baseline": None, "breaches": []}
    if not older:
        return {"status": "warming_up", "stale": now, "baseline": None, "breaches": [],
                "reason": f"기록 {len(by_day)}일 — {days}일 전 기록이 아직 없다"}
    base_day = older[-1]
    base = by_day[base_day]
    out = {"stale": now, "baseline": {"date": base_day, "stale": base}, "breaches": []}
    if base > 0 and now >= base:
        out["status"] = "failure"
        out["breaches"].append({
            "code": "detail-stale-not-decreasing", "value": now, "baseline": base, "since": base_day,
            "message": (f"483 상세 재번역 대기(stale) {now}행이 {base_day}({base}행) 이후 {days}일 넘게 줄지 "
                        "않았습니다 — `findings_detail_export.py --mode stale` 로 대기열을 만들어 번역팀에 넘겨 주세요."),
        })
    else:
        out["status"] = "ok"
    return out


def run(findings: list[dict[str, Any]], raws: list[dict[str, Any]], history: list[dict[str, Any]],
        today: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(보고서, 새 기록) — 순수 함수(네트워크 0)."""
    counts = count_stale(findings, raws)
    new_history = merge_history(history, today, counts)
    trend = evaluate_trend(new_history, today)
    report = {"checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "date": today.isoformat(), **counts, **trend, "history_days": len(new_history)}
    return report, new_history


def _load_history(path: str | None) -> tuple[list[dict[str, Any]], bool]:
    if not path or not Path(path).is_file():
        return [], False
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    return (data if isinstance(data, list) else []), True


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="483 상세 재번역(stale) 대기열 일일 감시(읽기 전용, 알림만).")
    p.add_argument("--history", help="일별 기록 JSON(읽고 오늘 기록을 넣어 다시 쓴다).")
    p.add_argument("--output", help="보고서 JSON 경로.")
    p.add_argument("--today", help="기준일 YYYY-MM-DD(기본: 오늘 UTC).")
    p.add_argument("--export-dir", help="stale 행이 있으면 재번역 원문 묶음(R-*.json)을 이 폴더에 쓴다 — "
                                        "findings_detail_export --mode stale 과 같은 함수(아티팩트용, 2026-11-26).")
    p.add_argument("--supabase-url")
    p.add_argument("--service-role-key")
    args = p.parse_args(argv)

    from grm_cli import normalize_supabase_url as _normalize_base_url
    from grm_cli import resolve_supabase_service_credentials as _resolve_credentials
    import findings_detail_export as fde

    creds = _resolve_credentials(args)
    if creds is None:
        print("findings_detail_stale_monitor: Supabase 자격 증명 필요", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()
    history, had_history = _load_history(args.history)
    try:
        findings, raws = fde.fetch_inputs(_normalize_base_url(creds[0]), creds[1], "stale")
    except Exception as exc:  # noqa: BLE001 — 키를 싣지 않게 예외 타입만
        report = {"status": "error", "date": today.isoformat(), "breaches": [],
                  "errors": [f"fetch failed ({type(exc).__name__})"]}
        new_history = history
    else:
        report, new_history = run(findings, raws, history, today)
        report["errors"] = []
        if args.export_dir and int(report.get("stale") or 0) > 0:
            exported = fde.export_queue(findings, raws, Path(args.export_dir), "stale")
            report["export"] = {"dir": args.export_dir, "shards": exported["shards"]}
    report["history_restored"] = had_history
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.history:
        Path(args.history).write_text(json.dumps(new_history, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 1 if report["status"] in ("failure", "error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
