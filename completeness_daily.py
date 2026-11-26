#!/usr/bin/env python3
"""수집 완전성 N6 — 사람이 보는 지점. 하루 한 번(23:07 KST), 한 곳.

대표 기준: "놓치는 것은 없고, 놓친 것은 그날 안에 반드시 메워진다." 그날 마감에 남은 것만 사람에게 보인다.

입력(모두 그날 이미 만들어진 판정 — 원천은 다시 부르지 않는다)
  ① 문서 대조   source_reconcile.py 결과(그날 grm-source-reconcile 아티팩트 `source-reconcile.json`). 없으면 "대조 안 돎".
  ② 매일 장부   coverage_daily(171) 의 식약처 행정처분·회수 칸 — 원천 총건수 대비 설명되지 않은 차이.
  ③ 칸 대조     coverage_daily.incomplete(172, field_contract.py) — 오늘과 어제의 소스별 결손 수.

소스별 한 줄 = 정상 / 메우는 중(n) / **사람 필요(n)**
  · 사람 필요: 놓침(missed)·원천 불가·저장소 불가·대조 안 돎·장부 차이·측정 불가, 결손이 어제보다 줄지 않음.
  · 메우는 중: 결손이 있지만 어제보다 줄었다(또는 첫 측정일).
  · Health Canada 실사: 원천 불가(옛 서버 불통·새 도메인에 GMP 없음). 대조 대상이 아니라 **월요일마다 한 줄**로 올린다.

알림: `[GRM 문제] 수집 완전성 일보` 이슈 하나를 돌려쓴다(라벨 completeness-daily → 운영 경보 표 131 은 ops_alarm_sync
가 옮긴다). 사람 필요 항목이 다음 날에도 남아 있으면 제목에 "n일째"를 붙인다 — 항목별 처음 본 날을 이슈 본문의
숨은 표식에 적어 둔다. 정상인 날은 한 줄("전 소스 정상")만 남기고, 열린 이슈가 있으면 그 한 줄을 댓글로 달고 닫는다.
기존 성장 일보에는 붙이지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

KST = timezone(timedelta(hours=9))
LABEL = "completeness-daily"
TITLE = "[GRM 문제] 수집 완전성 일보"
STATE_RE = re.compile(r"<!-- completeness-daily (\{.*?\}) -->", re.S)
MAX_IDS = 10                 # 줄마다 보여 줄 번호(전체 수는 따로)
HC_WEEKDAY = 0               # 월요일
HTTP_TIMEOUT = 30

OK, FILLING, HUMAN = "ok", "filling", "human"
STATE_KO = {OK: "정상", FILLING: "메우는 중", HUMAN: "사람 필요"}

# source_reconcile 판정 → (상태, 이유). ok·조용함·미뤄짐만은 정상이다.
RECONCILE_VERDICT = {
    "ok": (OK, ""), "source_quiet": (OK, ""), "deferred_only": (OK, ""),
    "missed": (HUMAN, "놓침"), "source_unreachable": (HUMAN, "원천 불가"),
    "store_unreachable": (HUMAN, "저장소 조회 불가"),
}
LEDGER_SOURCES = {"mfds_admin": "식약처 행정처분(장부)", "mfds_recall": "식약처 회수(장부)"}


@dataclass
class Line:
    key: str
    label: str
    state: str
    reason: str = ""
    count: int = 0
    items: list[str] = field(default_factory=list)
    url: str = ""

    def text(self) -> str:
        head = f"{self.label} — {STATE_KO[self.state]}"
        if self.state != OK and self.count:
            head += f"({self.count})"
        if self.reason:
            head += f" · {self.reason}"
        if self.items:
            more = f" 외 {self.count - len(self.items)}건" if self.count > len(self.items) else ""
            head += " · " + ", ".join(self.items) + more
        if self.url:
            head += f" · 원천 {self.url}"
        return head


def reconcile_lines(report: Mapping[str, Any] | None, day: date) -> list[Line]:
    """문서 대조 결과 → 줄. 그날 결과가 없으면 사람 필요 한 줄(대조가 안 돈 날을 조용히 넘기지 않는다)."""
    if not report or str(report.get("run_date") or "") != day.isoformat():
        return [Line("reconcile", "문서 대조(전 소스)", HUMAN, "오늘 대조 결과 없음(안 돎·실패)")]
    lines = []
    for s in report.get("sources") or []:
        verdict = str(s.get("verdict") or "")
        if verdict == "disabled":
            continue
        state, reason = RECONCILE_VERDICT.get(verdict, (HUMAN, f"알 수 없는 판정({verdict})"))
        missed = [m for m in s.get("missed") or [] if isinstance(m, dict)]
        if state == HUMAN and s.get("error"):
            reason = f"{reason} — {str(s['error'])[:120]}"
        lines.append(Line(
            f"reconcile:{s.get('key')}", str(s.get("label") or s.get("key")), state, reason,
            count=len(missed) if missed else (1 if state == HUMAN else 0),
            items=[str(m.get("document_id") or "") for m in missed[:MAX_IDS]],
            url=str(missed[0].get("url") or "") if missed else ""))
    return lines


def ledger_lines(today: Mapping[str, Any] | None) -> list[Line]:
    """매일 장부(식약처 행정처분·회수). 장부가 꺼져 있으면(행 없음·not_measured) 참고 한 줄만 — 경보 아님."""
    lines = []
    for key, label in LEDGER_SOURCES.items():
        row = (today or {}).get(key)
        status = str((row or {}).get("measurement_status") or "")
        if not row or status == "not_measured":
            lines.append(Line(f"ledger:{key}", label, OK, "장부 미측정(GRM_RECONCILE_LEDGER 꺼짐)"))
        elif status != "measured":
            lines.append(Line(f"ledger:{key}", label, HUMAN, f"측정 불가({status})", count=1))
        elif row.get("unexplained_difference") not in (None, 0):
            diff = int(row["unexplained_difference"])
            lines.append(Line(f"ledger:{key}", label, HUMAN,
                              f"원천 {row.get('source_count')} / DB {row.get('db_count')} · 설명되지 않은 차이",
                              count=abs(diff)))
        else:
            lines.append(Line(f"ledger:{key}", label, OK))
    return lines


def field_lines(today: Mapping[str, Any] | None, yesterday: Mapping[str, Any] | None) -> list[Line]:
    """칸 대조(N2). 결손이 어제보다 줄었으면 메우는 중, 줄지 않았으면 사람 필요."""
    if not isinstance(today, Mapping) or not isinstance(today.get("sources"), Mapping):
        return [Line("field", "칸 대조(N2)", HUMAN, "오늘 칸 대조 결과 없음", count=1)]
    prev = (yesterday or {}).get("sources") if isinstance(yesterday, Mapping) else None
    lines = []
    for key, s in today["sources"].items():
        n = int(s.get("incomplete") or 0)
        label = f"{s.get('label') or key} 칸"
        if n == 0:
            lines.append(Line(f"field:{key}", label, OK))
            continue
        before = (prev or {}).get(key, {}).get("incomplete") if isinstance(prev, Mapping) else None
        fields = ", ".join(f"{k} {v}" for k, v in (s.get("by_field") or {}).items())
        ids = [str(i) for i in (s.get("incomplete_ids") or [])[:MAX_IDS]]
        if before is None:
            lines.append(Line(f"field:{key}", label, FILLING, f"첫 측정 · {fields}", n, ids))
        elif n < int(before):
            lines.append(Line(f"field:{key}", label, FILLING, f"어제 {before} → {n} · {fields}", n, ids))
        else:
            lines.append(Line(f"field:{key}", label, HUMAN, f"어제 {before} → {n}, 줄지 않음 · {fields}", n, ids))
    return lines


def hc_line(day: date) -> list[Line]:
    if day.weekday() != HC_WEEKDAY:
        return []
    return [Line("hc_inspection", "Health Canada 실사", HUMAN,
                 "원천 불가 — 옛 서버 불통(10-31~), 새 도메인에 GMP 없음. 대체 원천 결정 필요(주 1회 알림)", count=1)]


def build_lines(day: date, reconcile: Mapping[str, Any] | None,
                ledger_today: Mapping[str, Any] | None, ledger_yesterday: Mapping[str, Any] | None) -> list[Line]:
    incomplete_today = (ledger_today or {}).get("incomplete")
    incomplete_yesterday = (ledger_yesterday or {}).get("incomplete")
    return (reconcile_lines(reconcile, day) + ledger_lines(ledger_today)
            + field_lines(incomplete_today, incomplete_yesterday) + hc_line(day))


def parse_state(body: str) -> dict[str, str]:
    m = STATE_RE.search(body or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    since = data.get("since") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in since.items()} if isinstance(since, dict) else {}


def plan(day: date, lines: list[Line], prev_state: Mapping[str, str]) -> dict[str, Any]:
    """줄 → 이슈 계획. 사람 필요 항목별 처음 본 날을 이어 받아 'n일째'를 센다. 순수 함수."""
    human = [l for l in lines if l.state == HUMAN]
    if not human:
        return {"normal": True, "title": TITLE, "summary": f"{day.isoformat()} 전 소스 정상", "since": {}}
    since = {l.key: prev_state.get(l.key, day.isoformat()) for l in human}
    oldest = min(date.fromisoformat(v) for v in since.values())
    days = (day - oldest).days + 1
    title = f"{TITLE} — 사람 필요 {len(human)}곳" + (f" ({days}일째)" if days >= 2 else "")
    filling = [l for l in lines if l.state == FILLING]
    body = [f"## {day.isoformat()} 수집 완전성 일보", "", "### 사람 필요"]
    for l in human:
        age = (day - date.fromisoformat(since[l.key])).days + 1
        body.append(f"- {l.text()}" + (f" — **{age}일째**" if age >= 2 else ""))
    if filling:
        body += ["", "### 메우는 중"] + [f"- {l.text()}" for l in filling]
    ok = [l.label for l in lines if l.state == OK]
    body += ["", f"정상 {len(ok)}곳: " + (", ".join(ok) if ok else "—"),
             "", "사람이 하는 일: ① 원천 불가 복구(프록시·키·차단) ② 가드에 걸린 행 판독 ③ 근거 칸 확정.",
             "", f"<!-- completeness-daily {json.dumps({'since': since}, ensure_ascii=False)} -->"]
    return {"normal": False, "title": title, "body": "\n".join(body), "since": since,
            "summary": f"{day.isoformat()} 사람 필요 {len(human)}곳 · 메우는 중 {len(filling)}곳"}


# ── 읽기·알림 ─────────────────────────────────────────────────────────────────

def read_ledger(base: str, key: str, day: date) -> tuple[dict | None, dict | None]:
    prev = day - timedelta(days=1)
    resp = requests.get(f"{base.rstrip('/')}/rest/v1/coverage_daily",
                        headers={"apikey": key, "Authorization": f"Bearer {key}"},
                        params={"select": "*", "day": f"in.({prev.isoformat()},{day.isoformat()})"},
                        timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    rows = {str(r.get("day")): r for r in resp.json() if isinstance(r, dict)}
    return rows.get(day.isoformat()), rows.get(prev.isoformat())


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", timeout=30).stdout


def open_issue() -> dict[str, Any] | None:
    issues = json.loads(_gh(["issue", "list", "--label", LABEL, "--state", "open",
                             "--json", "number,title,body", "--limit", "5"]) or "[]")
    return min(issues, key=lambda i: i["number"]) if issues else None


def publish(result: Mapping[str, Any], existing: Mapping[str, Any] | None, run_url: str) -> str:
    suffix = f"\n\n실행: {run_url}" if run_url else ""
    if result["normal"]:
        if existing:
            _gh(["issue", "comment", str(existing["number"]), "--body", result["summary"] + suffix])
            _gh(["issue", "close", str(existing["number"])])
            return f"closed #{existing['number']}"
        return "normal"
    body = result["body"] + suffix
    if existing:
        _gh(["issue", "edit", str(existing["number"]), "--title", result["title"], "--body", body])
        _gh(["issue", "comment", str(existing["number"]), "--body", result["summary"]])
        return f"updated #{existing['number']}"
    _gh(["label", "create", LABEL, "--color", "B60205", "--force",
         "--description", "수집 완전성 일보(N6) — completeness_daily.py"])
    _gh(["issue", "create", "--title", result["title"], "--body", body,
         "--label", f"{LABEL},automation-warning"])
    return "created"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="수집 완전성 N6 일보")
    ap.add_argument("--reconcile", type=Path, default=Path("source-reconcile.json"),
                    help="그날 source_reconcile 결과(없으면 '대조 안 돎')")
    ap.add_argument("--day", default="", help="KST 날짜(기본 오늘)")
    ap.add_argument("--json", default="completeness-daily.json")
    ap.add_argument("--apply", action="store_true", help="이슈 생성·갱신·닫기")
    ap.add_argument("--run-url", default="")
    args = ap.parse_args(argv)
    day = date.fromisoformat(args.day) if args.day else datetime.now(KST).date()
    try:
        reconcile = json.loads(args.reconcile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        reconcile = None
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    ledger_today = ledger_yesterday = None
    read_error = ""
    if base.startswith("https://") and key:
        try:
            ledger_today, ledger_yesterday = read_ledger(base, key, day)
        except (requests.RequestException, ValueError) as exc:
            read_error = type(exc).__name__
    else:
        read_error = "SUPABASE 미설정"
    lines = build_lines(day, reconcile, ledger_today, ledger_yesterday)
    if read_error:
        lines.append(Line("store", "일보 저장소 읽기", HUMAN, f"coverage_daily 읽기 실패({read_error})", count=1))
    existing = open_issue() if args.apply else None
    result = plan(day, lines, parse_state(str((existing or {}).get("body") or "")))
    Path(args.json).write_text(json.dumps({"day": day.isoformat(), **result,
                                            "lines": [l.__dict__ for l in lines]},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["summary"])
    if not result["normal"]:
        print(result["body"])
    if args.apply:
        print(publish(result, existing, args.run_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
