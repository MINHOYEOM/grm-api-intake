#!/usr/bin/env python3
"""DB 백업 검증 — 덤프 안의 표별 행 수를 라이브와 대조한다 (복원 없이, 추가 서비스 없이).

[2026-09-10] `.github/workflows/grm-db-backup.yml` 이 쓴다. 왜 복원 시험이 아니라 행 수 대조인가:
Supabase 덤프는 `auth.*`·확장(vector 등)을 참조해 바닐라 Postgres 에는 그대로 복원되지 않고,
복원용 컨테이너를 매주 띄우는 것은 이 저장소의 "운영 비용 0" 원칙에 맞지 않는다. 대신
(a) PostgREST 로 **정확한** 표별 행 수(`Prefer: count=exact`)를 덤프 직전에 받고,
(b) `pg_dump --data-only --use-copy` 산출(data.sql)의 `COPY … FROM stdin;` ~ `\\.` 블록을 표별로
세어 (c) 둘을 대조한다. 덤프가 잘렸거나 표가 빠졌으면 여기서 빨강이 된다. 진짜 복원 시험은
분기 1회 사람이 한다(docs/ops_runbook.md "DB 백업·복원").

서브커맨드:
  counts --schema schema.sql --out counts.json
      env SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 필요. schema.sql 의 `CREATE TABLE public.x`
      목록을 표 목록으로 쓴다(뷰는 COPY 블록이 없으므로 OpenAPI 정의가 아니라 스키마 덤프가 정본).
      서비스키는 헤더로만 나가고 어떤 출력에도 찍히지 않는다.
  verify --dump data.sql --counts counts.json --report report.md
         [--min-ratio 0.99] [--require findings,raw_signals] [--min-tables 20]
      exit 0 = 통과 · 1 = 위반(리포트에 이유) · 2 = 사용 오류.

표 이름은 `\\w+` 로 한정한다(이 저장소의 public 표는 전부 소문자·밑줄). 따옴표 유무·
스키마 접두 유무(`COPY public.x` / `COPY "public"."x"`)를 모두 받는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Callable

_CREATE_TABLE_RE = re.compile(
    r'^CREATE TABLE(?: IF NOT EXISTS)?\s+(?:"?public"?\.)?"?(\w+)"?\s*\(', re.IGNORECASE)
_COPY_RE = re.compile(r'^COPY\s+(?:"?public"?\.)?"?(\w+)"?\s*(?:\(|FROM\s)', re.IGNORECASE)
_COPY_END = "\\."


def tables_from_schema(schema_sql: str) -> list[str]:
    """schema.sql 의 CREATE TABLE 문에서 public 표 이름을 순서대로(중복 없이) 뽑는다."""
    seen: list[str] = []
    for line in schema_sql.splitlines():
        m = _CREATE_TABLE_RE.match(line.strip())
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def copy_row_counts(data_sql_lines) -> dict[str, int]:
    """data.sql 을 한 줄씩 훑어 표별 COPY 데이터 행 수를 센다(파일 전체를 메모리에 올리지 않는다)."""
    counts: dict[str, int] = {}
    current: str | None = None
    for raw in data_sql_lines:
        line = raw.rstrip("\r\n")
        if current is None:
            m = _COPY_RE.match(line)
            if m:
                current = m.group(1)
                counts[current] = counts.get(current, 0)
            continue
        if line == _COPY_END:
            current = None
            continue
        counts[current] += 1
    if current is not None:
        # 종료 마커 없이 파일이 끝났다 = 덤프가 잘렸다. 호출자가 위반으로 다루도록 표식을 남긴다.
        counts["__truncated__"] = 1
    return counts


# 추정치(count=planned) 표의 완화 비율. 플래너 추정은 통계 갱신 시점에 따라 흔들리므로
# 정밀 비율(0.99)을 그대로 대면 가짜 빨강이 난다 — 대신 대폭 절단(절반 미만)만 잡는다.
APPROX_MIN_RATIO = 0.5


def compare(live: dict[str, int], dumped: dict[str, int], *, min_ratio: float,
            require: list[str], min_tables: int,
            approx: "set[str] | frozenset[str]" = frozenset(),
            ) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """(통과 여부, 표별 행, 위반 사유 목록).

    `approx` = 라이브 행 수가 정확 count 가 아니라 플래너 추정치인 표(#997). 그 표는
    0행·대폭 절단(`APPROX_MIN_RATIO`)만 판정하고 정밀 비율은 건너뛴다.
    """
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    approx = set(approx or ())
    if dumped.pop("__truncated__", 0):
        problems.append("data.sql 이 COPY 종료 마커(\\.) 없이 끝났다 — 덤프 절단")
    if len(live) < min_tables:
        problems.append(f"라이브 표 {len(live)}개 < 최소 {min_tables} — 스키마 덤프/표 목록 이상")
    for name in sorted(live):
        live_n = int(live[name])
        if name not in dumped:
            rows.append({"table": name, "live": live_n, "dumped": None, "ok": False})
            problems.append(f"{name}: 덤프에 COPY 블록 없음")
            continue
        dump_n = int(dumped[name])
        ok = True
        is_approx = name in approx
        ratio = APPROX_MIN_RATIO if is_approx else min_ratio
        if live_n > 0 and dump_n == 0:
            ok = False
            problems.append(f"{name}: 라이브 {'~' if is_approx else ''}{live_n}행인데 덤프 0행")
        elif live_n > 0 and dump_n < live_n * ratio:
            ok = False
            problems.append(f"{name}: 덤프 {dump_n} < 라이브 {'~' if is_approx else ''}{live_n} × {ratio}"
                            + (" (추정치 기준 완화 비율)" if is_approx else ""))
        rows.append({"table": name, "live": live_n, "dumped": dump_n, "ok": ok,
                     "approx": is_approx})
    for name in require:
        if name not in dumped or int(dumped.get(name, 0)) <= 0:
            problems.append(f"{name}: 필수 표인데 덤프 행 0")
    extra = sorted(set(dumped) - set(live))
    for name in extra:
        rows.append({"table": name, "live": None, "dumped": int(dumped[name]), "ok": True})
    return (not problems), rows, problems


def render_report(rows: list[dict[str, Any]], problems: list[str], *, ok: bool) -> str:
    out = ["### DB 백업 행 수 대조", "",
           f"결과: {'통과' if ok else '위반'} · 표 {len(rows)}개", "",
           "| 표 | 라이브 | 덤프 | 판정 |", "|---|---:|---:|---|"]
    for r in rows:
        live = "-" if r["live"] is None else f"{'~' if r.get('approx') else ''}{r['live']}"
        out.append(f"| {r['table']} | {live} | "
                   f"{r['dumped'] if r['dumped'] is not None else '없음'} | {'ok' if r['ok'] else 'FAIL'} |")
    if any(r.get("approx") for r in rows):
        out += ["", "`~` = 정확 count 가 타임아웃이라 플래너 추정치(`count=planned`)로 받은 표 — "
                    f"절단 판정만 완화 비율({APPROX_MIN_RATIO})로 본다(#997)."]
    if problems:
        out += ["", "위반:"] + [f"- {p}" for p in problems]
    return "\n".join(out) + "\n"


# ── counts (PostgREST, 정확 count → 5xx/타임아웃이면 재시도 → 그래도 안 되면 추정치) ────
# [2026-09-14 #997] admin_audit_log 처럼 커지는 표는 정확 count(`count=exact`)가 먼저 무너진다
# (PostgREST 504). 덤프 자체는 성공했는데 검증 **준비** 단계가 예외로 죽어 백업 전체가 실패로
# 기록됐고 아티팩트도 안 남았다. 정확 count 는 검증의 정밀도를 위한 것이지 백업의 전제가
# 아니다 — 재시도 뒤에도 안 되면 플래너 추정치(`count=planned`)로 받고 `__approx__` 에 표
# 이름을 적어, compare 가 그 표만 완화 비율로 본다(정밀 판정은 나머지 표에서 그대로).
COUNT_RETRIES = 3
APPROX_KEY = "__approx__"      # counts.json 예약 키 — 추정치로 받은 표 이름 목록


def _count_once(get: Callable[..., Any], url: str, headers: dict[str, str],
                timeout: int) -> tuple[int | None, str]:
    """1회 count 요청 → (행 수, 오류 문자열). 키는 어떤 문자열에도 싣지 않는다."""
    try:
        resp = get(url, headers=headers, params={"select": "*"}, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — 진단 문자열이 곧 결과다(타임아웃 포함)
        return None, f"exc:{type(e).__name__}"
    if resp.status_code not in (200, 206):
        return None, f"http_{resp.status_code}"
    cr = resp.headers.get("Content-Range", "")
    total = cr.rsplit("/", 1)[-1] if "/" in cr else ""
    if not total.isdigit():
        return None, f"content-range={cr!r}"
    return int(total), ""


def _retryable(err: str) -> bool:
    return err.startswith("http_5") or err.startswith("exc:")


def live_counts(base_url: str, service_key: str, tables: list[str], *,
                retries: int = COUNT_RETRIES, timeout: int = 60,
                get: Callable[..., Any] | None = None,
                sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """표별 라이브 행 수. 반환 dict 에 `__approx__`(추정치 표 목록)가 붙을 수 있다.

    정확 count 가 5xx/타임아웃이면 지수 백오프로 재시도하고, 그래도 안 되면 `count=planned`
    로 1회 더 받아 추정치로 적는다. 추정치조차 못 받으면 그때만 예외(count-failed).
    """
    if get is None:
        import requests  # 지연 import — verify 서브커맨드·테스트는 requests 없이 돈다.
        get = requests.get
    base = base_url.rstrip("/") + "/rest/v1/"
    exact = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
             "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"}
    planned = dict(exact, Prefer="count=planned")
    out: dict[str, Any] = {}
    approx: list[str] = []
    for name in tables:
        url = base + name
        n: int | None = None
        err = ""
        for attempt in range(max(1, retries)):
            n, err = _count_once(get, url, exact, timeout)
            if n is not None or not _retryable(err):
                break
            if attempt < retries - 1:
                sleep(2 ** attempt)
        if n is None and _retryable(err):
            n, err2 = _count_once(get, url, planned, timeout)
            if n is not None:
                approx.append(name)
                print(f"count 폴백: {name} 정확 count {err} → 추정치(count=planned) {n}", file=sys.stderr)
            else:
                err = f"{err};planned:{err2}"
        if n is None:
            raise RuntimeError(f"count-failed:{name}:{err}")  # 키는 싣지 않는다
        out[name] = n
    if approx:
        out[APPROX_KEY] = sorted(approx)
    return out


def main(argv: list[str] | None = None) -> int:
    # 저장소 관례(tests/test_cli_stdout_encoding.py): 한국어 리포트가 cp949 콘솔에서 죽지 않게.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("counts")
    c.add_argument("--schema", required=True)
    c.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--dump", required=True)
    v.add_argument("--counts", required=True)
    v.add_argument("--report", required=True)
    v.add_argument("--min-ratio", type=float, default=0.99)
    v.add_argument("--require", default="findings,raw_signals")
    v.add_argument("--min-tables", type=int, default=20)
    a = ap.parse_args(argv)

    if a.cmd == "counts":
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url.startswith("https://") or not key:
            print("SUPABASE_URL(https)·SUPABASE_SERVICE_ROLE_KEY 필요", file=sys.stderr)
            return 2
        with open(a.schema, encoding="utf-8") as fh:
            tables = tables_from_schema(fh.read())
        if not tables:
            print("schema.sql 에서 CREATE TABLE 을 찾지 못했다", file=sys.stderr)
            return 1
        counts = live_counts(url, key, tables)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(counts, fh, ensure_ascii=False, indent=1, sort_keys=True)
        approx = counts.get(APPROX_KEY, [])
        n_tables = len([k for k in counts if k != APPROX_KEY])
        n_rows = sum(v for k, v in counts.items() if k != APPROX_KEY)
        print(f"live tables={n_tables} rows={n_rows}"
              + (f" approx={','.join(approx)}" if approx else ""))
        return 0

    with open(a.counts, encoding="utf-8") as fh:
        live = json.load(fh)
    approx = set(live.pop(APPROX_KEY, None) or [])
    with open(a.dump, encoding="utf-8", errors="replace") as fh:
        dumped = copy_row_counts(fh)
    require = [s for s in a.require.split(",") if s]
    ok, rows, problems = compare(live, dumped, min_ratio=a.min_ratio, require=require,
                                 min_tables=a.min_tables, approx=approx)
    report = render_report(rows, problems, ok=ok)
    with open(a.report, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
