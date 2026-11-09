#!/usr/bin/env python3
"""[수입경보 1단계] 업체 페이지 "FDA 수입경보 대상" 표시의 정본 스냅샷을 다시 만든다.

원천: `public.import_alert_listing`(web/migrations/132_fda_import_alert.sql). 표는 RLS 를 켜고
anon·authenticated 를 회수해 두었다(service_role 전용) — 표를 anon 에 열지 않고, **공개해도 되는
필드만** 뽑아 `web/data/import_alerts.json` 으로 커밋한다(fda_citations.json 과 같은 경로).
렌더(web/render.py)는 이 파일만 읽는다. 파일이 없으면 표시가 조용히 꺼진다.

싣는 필드(화이트리스트, 그 밖은 버린다):
  alert_no · firm_name · name_norm · firm_key · country · city · firm_published ·
  product_codes · active · first_seen
  ★주소(address·addresses)·품목 설명(products)·해제일 추정값은 싣지 않는다.
  name_norm·firm_key 는 업체 이름에서 파생한 정규화 키라(새 정보 없음) 매칭용으로 싣는다.

게이트(위반이면 파일을 건드리지 않고 exit 2):
  · 0행 · 필드 형식 위반 · active 행이 직전 파일 대비 --max-drop-pct(기본 20%) 이상 감소.
  · 내용이 직전 파일과 같으면(measured_on 만 다름) 쓰지 않고 exit 10.

실행(워크플로 `grm-import-alerts-refresh.yml` dispatch, 또는 로컬):
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python import_alerts_refresh.py
exit: 0 = 썼다 · 10 = 변경 없음 · 2 = 읽기 실패/게이트 위반(파일 무변경)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials
import collect_fda_import_alert as ia

SCHEMA_VERSION = "grm-import-alerts/v1"
DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "import_alerts.json"
TABLE = "import_alert_listing"
FIELDS = ("alert_no", "firm_name", "name_norm", "firm_key", "country", "city",
          "firm_published", "product_codes", "active", "first_seen")
PAGE_SIZE = 1000
DEFAULT_MAX_DROP_PCT = 20
EXIT_NO_CHANGE = 10

_ALERT_RE = re.compile(r"^\d{2}-\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SnapshotError(ValueError):
    """스냅샷을 만들 수 없는 입력(형식 위반·0행)."""


def fetch_rows(base_url: str, key: str, *, http: Any = None,
               page_size: int = PAGE_SIZE) -> list[dict[str, Any]]:
    """service_role 로 화이트리스트 열만 읽는다(select 에 주소 열을 넣지 않는다)."""
    if http is None:
        import requests  # 지연 import — 테스트는 http 를 주입한다
        http = requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        r = http.get(f"{base_url}/rest/v1/{TABLE}",
                     params={"select": ",".join(FIELDS), "order": "alert_no.asc,firm_key.asc",
                             "limit": str(page_size), "offset": str(offset)},
                     headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"{TABLE} read HTTP {r.status_code}")
        page = r.json()
        if not isinstance(page, list):
            raise RuntimeError(f"{TABLE} read: 배열이 아닌 응답")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def _clean_row(r: dict[str, Any]) -> dict[str, Any]:
    def text(k: str, *, required: bool = True) -> str:
        v = r.get(k)
        v = "" if v is None else str(v).strip()
        if required and not v:
            raise SnapshotError(f"{k} 비어 있음: {r.get('firm_key')!r}")
        return v

    alert = text("alert_no")
    if not _ALERT_RE.match(alert):
        raise SnapshotError(f"alert_no 형식: {alert!r}")
    published = text("firm_published", required=False)
    if published and not _DATE_RE.match(published[:10]):
        raise SnapshotError(f"firm_published 형식: {published!r}")
    first_seen = text("first_seen")
    if not _DATE_RE.match(first_seen[:10]):
        raise SnapshotError(f"first_seen 형식: {first_seen!r}")
    codes = r.get("product_codes") or []
    if not isinstance(codes, list):
        raise SnapshotError("product_codes 가 배열이 아님")
    if not isinstance(r.get("active"), bool):
        raise SnapshotError("active 가 bool 이 아님")
    return {
        "alert_no": alert,
        "firm_key": text("firm_key"),
        "firm_name": text("firm_name"),
        "name_norm": text("name_norm"),
        "country": text("country"),
        "city": text("city", required=False),
        "firm_published": published[:10],
        "product_codes": sorted({str(c).strip() for c in codes if str(c).strip()}),
        "active": r["active"],
        "first_seen": first_seen[:10],
    }


def build_snapshot(rows: list[dict[str, Any]], *, measured_on: str) -> dict[str, Any]:
    """원장 행 → 스냅샷(화이트리스트·정렬·집계). 순수 함수."""
    if not rows:
        raise SnapshotError("0행 — 원장이 비었거나 읽기가 잘못됐다")
    listings = sorted((_clean_row(r) for r in rows), key=lambda x: (x["alert_no"], x["firm_key"]))
    active = [x for x in listings if x["active"]]
    by_alert: dict[str, int] = {}
    for x in active:
        by_alert[x["alert_no"]] = by_alert.get(x["alert_no"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "measured_on": measured_on,
        "totals": {
            "listings": len(listings),
            "active": len(active),
            "active_korea": sum(1 for x in active if ia.is_korea(x["country"])),
            "active_by_alert": [{"alert_no": a, "n": n} for a, n in sorted(by_alert.items())],
        },
        "listings": listings,
    }


def serialize(snapshot: dict[str, Any]) -> str:
    """머리 + 한 행 = 한 줄(주기 재생성의 diff 가 실제 변경분만 보이게)."""
    head = {k: v for k, v in snapshot.items() if k != "listings"}
    lines = [json.dumps(head, ensure_ascii=False)[:-1] + ', "listings": [']
    rows = snapshot["listings"]
    for i, r in enumerate(rows):
        lines.append(json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                     + ("," if i < len(rows) - 1 else ""))
    lines.append("]}")
    return "\n".join(lines) + "\n"


def guard(old: "dict[str, Any] | None", new: dict[str, Any], *,
          max_drop_pct: int = DEFAULT_MAX_DROP_PCT) -> "str | None":
    """쓰면 안 되는 이유(문장) 또는 None. 변경 없음은 'unchanged'."""
    if old and old.get("schema_version") == SCHEMA_VERSION:
        if old.get("listings") == new["listings"]:
            return "unchanged"
        before = sum(1 for x in old.get("listings") or [] if x.get("active"))
        after = new["totals"]["active"]
        if before and (before - after) * 100 >= before * max_drop_pct:
            return f"active {before} → {after} (감소 {max_drop_pct}% 이상)"
    return None


def _load_old(path: Path) -> "dict[str, Any] | None":
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main(argv: "list[str] | None" = None, *,
         fetch: "Callable[[str, str], list[dict[str, Any]]] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="FDA 수입경보 공개 스냅샷 재생성")
    ap.add_argument("--supabase-url", default="")
    ap.add_argument("--service-role-key", default="")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--measured-on", default="")
    ap.add_argument("--max-drop-pct", type=int, default=DEFAULT_MAX_DROP_PCT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    creds = resolve_supabase_service_credentials(args)
    if creds is None:
        print("SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY 가 필요합니다.", file=sys.stderr)
        return 2
    base = normalize_supabase_url(creds[0]) or ""
    try:
        rows = (fetch or fetch_rows)(base, creds[1])
        snap = build_snapshot(rows, measured_on=args.measured_on or date.today().isoformat())
    except (RuntimeError, SnapshotError) as exc:
        print(f"스냅샷 실패(파일 무변경): {exc}", file=sys.stderr)
        return 2

    t = snap["totals"]
    print(f"행 {t['listings']:,} · 현재 등재 {t['active']:,} · 한국 {t['active_korea']:,} · "
          + ", ".join(f"{a['alert_no']} {a['n']:,}" for a in t["active_by_alert"]))
    reason = guard(_load_old(args.out), snap, max_drop_pct=args.max_drop_pct)
    if reason == "unchanged":
        print("변경 없음 — 쓰지 않습니다.")
        return EXIT_NO_CHANGE
    if reason:
        print(f"게이트 위반(파일 무변경): {reason}", file=sys.stderr)
        return 2
    if args.dry_run:
        print("dry-run — 쓰지 않습니다.")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialize(snap), encoding="utf-8", newline="\n")
    print(f"기록: {args.out}")
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
