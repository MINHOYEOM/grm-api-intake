#!/usr/bin/env python3
"""[해외 지적 사례 전체] PMDA·MHRA 실사 지적 사례 공개 스냅샷 web/data/deficiency_cases.json 을 다시 만든다.

원천: `public.deficiency_examples` + `public.deficiency_example_sources`(web/migrations/134). 두 표는 RLS 를
켜고 anon·authenticated 를 회수해 두었다(service_role 전용) — 표를 열지 않고, **공개해도 되는 필드만**
뽑아 커밋한다(import_alerts.json·fda_citations.json 과 같은 경로). 렌더는 이 파일만 읽는다
(web/deficiency_cases_page.py). 파일이 없으면 화면·블록이 조용히 꺼진다.

싣는 것(화이트리스트):
  sources        원천 파일 장부 — agency·year·year_basis·lang·kind·url (sha256·바이트 수 등은 버린다)
  cases          **영어 지적 문장이 있는 행만**(PMDA 영문 별첨 · MHRA Trend PDF 예시) —
                 id(source_key#row_no)·src·row·agency·year·no·loc·acat(기관 분류 원문)·cat(우리 분류)·
                 keys(수집기가 문장에서 뽑은 조항 키)·text(지적 원문 그대로)
                 ·text_ko(140 한국어 번역 — ko_source_hash == content_hash 일 때만)·acat_ko(기관 분류 한국어)
                 ★context(PMDA 배경 설명)·attrs·국가·제품 구분은 싣지 않는다(기존 지적 사례 화면과 같은 필드 수준).
                 ★PMDA 일본어판 행은 같은 지적의 일본어 원문 — 문장은 싣지 않고 건수만(totals.by_kind.pmda_ja),
                   화면은 원문 파일 링크로 낸다.
  clause_counts  MHRA 2018·2019 xlsx(문장 없는 행 11,501) — (EU GMP 장·부속서, 문단)별 Critical·Major·Other
                 행 수와 실사 수(insp). 익명 실사 ID 자체는 싣지 않는다.
  totals         원천 전체 행 수와 갈래별 행 수(화면의 "전체 n건" 근거).

게이트(위반이면 파일을 건드리지 않고 exit 2):
  · 0행 · 필드 형식 위반 · 전체 행이 직전 파일 대비 --max-drop-pct(기본 20%) 이상 감소.
  · 내용이 직전 파일과 같으면(measured_on 만 다름) 쓰지 않고 exit 10.

실행(워크플로 `grm-deficiency-cases-refresh.yml` dispatch, 또는 로컬):
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python deficiency_cases_refresh.py
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

_WEB = Path(__file__).resolve().parent / "web"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
import deficiency_cases_page as dcp  # noqa: E402  — 스냅샷 검증을 렌더와 같은 함수로

SCHEMA_VERSION = dcp.SCHEMA
DEFAULT_OUT = _WEB / "data" / "deficiency_cases.json"
ROW_TABLE = "deficiency_examples"
SRC_TABLE = "deficiency_example_sources"
ROW_FIELDS = ("source_key", "row_no", "agency", "year", "lang", "locator", "item_no", "statement",
              "agency_category", "grade", "site_group", "clause_keys", "clauses", "grm_category",
              # 140 한국어 칸 — ko_source_hash 가 지금 content_hash 와 같을 때만 싣는다(낡은 번역 차단).
              "statement_ko", "agency_category_ko", "ko_source_hash", "content_hash")
SRC_FIELDS = ("source_key", "agency", "year", "lang", "kind", "url")
PAGE_SIZE = 1000
DEFAULT_MAX_DROP_PCT = 20
EXIT_NO_CHANGE = 10

_SRC_KEY = re.compile(r"^(pmda|mhra):[a-z0-9_]{3,80}$")
_CAT = re.compile(r"^[a-z_]{0,60}$")


class SnapshotError(ValueError):
    """스냅샷을 만들 수 없는 입력(형식 위반·0행)."""


def _get_all(http: Any, base_url: str, key: str, table: str, fields: tuple[str, ...],
             order: str, page_size: int) -> list[dict[str, Any]]:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        r = http.get(f"{base_url}/rest/v1/{table}",
                     params={"select": ",".join(fields), "order": order,
                             "limit": str(page_size), "offset": str(offset)},
                     headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"{table} read HTTP {r.status_code}")
        page = r.json()
        if not isinstance(page, list):
            raise RuntimeError(f"{table} read: 배열이 아닌 응답")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def fetch(base_url: str, key: str, *, http: Any = None,
          page_size: int = PAGE_SIZE) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(원천 장부 행, 사례 행). 화이트리스트 열만 select 한다(context·attrs·country 는 읽지도 않는다)."""
    if http is None:
        import requests  # 지연 import — 테스트는 http 를 주입한다
        http = requests
    sources = _get_all(http, base_url, key, SRC_TABLE, SRC_FIELDS, "source_key.asc", page_size)
    rows = _get_all(http, base_url, key, ROW_TABLE, ROW_FIELDS, "source_key.asc,row_no.asc", page_size)
    return sources, rows


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def build_snapshot(sources: list[dict[str, Any]], rows: list[dict[str, Any]], *,
                   measured_on: str) -> dict[str, Any]:
    """원장 → 스냅샷(화이트리스트·정렬·집계). 순수 함수."""
    if not rows or not sources:
        raise SnapshotError("0행 — 원장이 비었거나 읽기가 잘못됐다")
    src: dict[str, dict[str, Any]] = {}
    for s in sources:
        k = _text(s.get("source_key"))
        if not _SRC_KEY.match(k):
            raise SnapshotError(f"source_key 형식: {k!r}")
        agency, url = _text(s.get("agency")), _text(s.get("url"))
        if agency not in ("PMDA", "MHRA") or not url.startswith("https://") or "pending.invalid" in url:
            raise SnapshotError(f"원천 {k}: 기관/URL")
        src[k] = {"agency": agency, "year": int(s["year"]),
                  "year_basis": "jp_fiscal" if agency == "PMDA" else "calendar",
                  "lang": _text(s.get("lang")), "kind": _text(s.get("kind")), "url": url}

    by_kind = {"pmda_en": 0, "pmda_ja": 0, "mhra_trend_pdf": 0, "mhra_xlsx": 0}
    cases: list[dict[str, Any]] = []
    counts: dict[tuple[str, str], dict[str, Any]] = {}
    insp_all: set[tuple[str, str]] = set()
    years: set[int] = set()
    no_clause = 0
    for r in rows:
        k = _text(r.get("source_key"))
        s = src.get(k)
        if s is None:
            raise SnapshotError(f"행 {k}#{r.get('row_no')}: 원천 장부에 없음")
        row_no = int(r["row_no"])
        cat = _text(r.get("grm_category"))
        if not _CAT.match(cat):
            raise SnapshotError(f"grm_category 형식: {cat!r}")
        if s["kind"] == "mhra_xlsx":
            by_kind["mhra_xlsx"] += 1
            years.add(s["year"])
            insp = (k, _text(r.get("site_group")))
            insp_all.add(insp)
            cl = (r.get("clauses") or [None])[0]
            ckey = _text(cl.get("key")) if isinstance(cl, dict) else ""
            if not dcp._EU_KEY.match(ckey):
                no_clause += 1
                continue
            para = " ".join(_text(cl.get("para")).split())[:40]
            g = counts.setdefault((ckey, para), {"critical": 0, "major": 0, "other": 0, "insp": set()})
            grade = _text(r.get("grade"))
            if grade in ("critical", "major", "other"):
                g[grade] += 1
            else:
                no_clause += 1  # 등급 없는 행 — 표의 어느 칸에도 넣지 않고 따로 센다
                continue
            g["insp"].add(insp)
            continue
        text = _text(r.get("statement"))
        if s["agency"] == "PMDA" and s["lang"] == "ja":
            by_kind["pmda_ja"] += 1
            continue
        by_kind["pmda_en" if s["agency"] == "PMDA" else "mhra_trend_pdf"] += 1
        if not text:
            continue
        keys = sorted({_text(x) for x in r.get("clause_keys") or [] if _text(x)})
        case = {
            "id": f"{k}#{row_no}", "src": k, "row": row_no, "agency": s["agency"], "year": s["year"],
            "no": _text(r.get("item_no"))[:40], "loc": _text(r.get("locator"))[:120],
            "acat": " ".join(_text(r.get("agency_category")).split())[:300],
            "cat": cat, "keys": keys, "text": text,
        }
        text_ko = _text(r.get("statement_ko"))
        if text_ko and _text(r.get("ko_source_hash")) == _text(r.get("content_hash")):
            case["text_ko"] = text_ko
        acat_ko = " ".join(_text(r.get("agency_category_ko")).split())[:300]
        if acat_ko:
            case["acat_ko"] = acat_ko
        cases.append(case)
    cases.sort(key=lambda c: (c["src"], c["row"]))
    clause_counts = [{"key": key, "para": para, "critical": g["critical"], "major": g["major"],
                      "other": g["other"], "insp": len(g["insp"])}
                     for (key, para), g in sorted(counts.items())]
    snap = {
        "schema": SCHEMA_VERSION,
        "measured_on": measured_on,
        "totals": {"rows": len(rows), "by_kind": by_kind, "cases": len(cases)},
        "mhra_data": {"years": sorted(years), "inspections": len(insp_all), "no_clause": no_clause},
        "sources": dict(sorted(src.items())),
        "clause_counts": clause_counts,
        "cases": cases,
    }
    try:
        dcp.validate(snap)
    except dcp.SnapshotError as exc:
        raise SnapshotError(str(exc)) from exc
    return snap


def serialize(snapshot: dict[str, Any]) -> str:
    """머리 + 한 행 = 한 줄(재생성 diff 가 실제 변경분만 보이게)."""
    head = {k: v for k, v in snapshot.items() if k not in ("cases", "clause_counts")}
    out = [json.dumps(head, ensure_ascii=False)[:-1] + ","]
    for name in ("clause_counts", "cases"):
        rows = snapshot[name]
        out.append(f'"{name}": [')
        for i, r in enumerate(rows):
            out.append(json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                       + ("," if i < len(rows) - 1 else ""))
        out.append("]," if name == "clause_counts" else "]}")
    return "\n".join(out) + "\n"


def guard(old: "dict[str, Any] | None", new: dict[str, Any], *,
          max_drop_pct: int = DEFAULT_MAX_DROP_PCT) -> "str | None":
    """쓰면 안 되는 이유(문장) 또는 None. 변경 없음은 'unchanged'."""
    if old and old.get("schema") == SCHEMA_VERSION:
        same = all(old.get(k) == new.get(k) for k in ("totals", "mhra_data", "sources",
                                                       "clause_counts", "cases"))
        if same:
            return "unchanged"
        before = int((old.get("totals") or {}).get("rows") or 0)
        after = new["totals"]["rows"]
        if before and (before - after) * 100 >= before * max_drop_pct:
            return f"rows {before} → {after} (감소 {max_drop_pct}% 이상)"
    return None


def _load_old(path: Path) -> "dict[str, Any] | None":
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main(argv: "list[str] | None" = None, *,
         fetch_fn: "Callable[[str, str], tuple[list, list]] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="PMDA·MHRA 지적 사례 공개 스냅샷 재생성")
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
        sources, rows = (fetch_fn or fetch)(base, creds[1])
        snap = build_snapshot(sources, rows, measured_on=args.measured_on or date.today().isoformat())
    except (RuntimeError, SnapshotError, KeyError, TypeError, ValueError) as exc:
        print(f"스냅샷 실패(파일 무변경): {exc}", file=sys.stderr)
        return 2

    t = snap["totals"]
    bk = t["by_kind"]
    print(f"행 {t['rows']:,} · 문장 사례 {t['cases']:,} (PMDA 영문 {bk['pmda_en']:,} · MHRA 예시 "
          f"{bk['mhra_trend_pdf']:,}) · PMDA 일본어 {bk['pmda_ja']:,} · MHRA 조항 행 {bk['mhra_xlsx']:,} "
          f"→ 조항 문단 {len(snap['clause_counts']):,}")
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
