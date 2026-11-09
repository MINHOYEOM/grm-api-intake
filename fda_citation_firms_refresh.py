#!/usr/bin/env python3
"""[FDA 지적 조항 × 업체] 업체 페이지·조항 페이지 블록의 정본 스냅샷을 다시 만든다.

원천: `public.fda_inspection_citations`(web/migrations/133, 수집기 collect_fda_citations.py).
표는 RLS 로 닫혀 있다(anon·authenticated 회수, service_role 전용) — 표를 열지 않고, 화면에
필요한 값만 **제조소(FEI) 단위로 집계**해 `web/data/fda_citation_firms.json` 으로 커밋한다
(fda_citations.json·import_alerts.json 과 같은 경로). 렌더(web/clause_firms_page.py)는 이 파일만
읽는다. 파일이 없으면 두 블록이 조용히 꺼진다.

싣는 값(FDA Data Dashboard 가 공개하는 것만, 그 밖은 select 하지 않는다):
  제조소 = (FEI, 업체명) 한 쌍 — fei · name · city · state · country(ISO2, country_key) ·
    country_name · inspections(지적이 나온 실사 수) · first/last(실사 종료일 범위) ·
    codes = [[21 CFR 표기, 그 표기를 인용한 실사 수, 마지막 실사일], ...] ·
    sections = [[섹션(211.192), 그 섹션을 인용한 실사 수, 마지막 실사일], ...]
  titles = 표기 → FDA 짧은 문구(최빈, 동수면 사전순)
  ★21 CFR 이 아닌 인용(FD&C Act 501 등)은 싣지 않는다 — 화면이 "21 CFR 조항"만 말한다.
  ★지적 본문(long_description)·우편번호·주소·inspection_id 는 싣지 않는다.

게이트(위반이면 파일을 건드리지 않고 exit 2):
  · 0행 · 필드 형식 위반 · 제조소 수가 직전 파일 대비 --max-drop-pct(기본 20%) 이상 감소.
  · 내용이 직전 파일과 같으면(as_of 만 다름) 쓰지 않고 exit 10.

실행(워크플로 `grm-fda-citation-firms-refresh.yml`, 또는 로컬):
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python fda_citation_firms_refresh.py
exit: 0 = 썼다 · 10 = 변경 없음 · 2 = 읽기 실패/게이트 위반(파일 무변경)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from grm_cli import normalize_supabase_url, resolve_supabase_service_credentials

SCHEMA_VERSION = "grm-fda-citation-firms/v1"
SOURCE = "FDA Data Dashboard - Inspections Citations"
DEFAULT_OUT = Path(__file__).resolve().parent / "web" / "data" / "fda_citation_firms.json"
TABLE = "fda_inspection_citations"
FIELDS = ("inspection_id", "citation_id", "fei_number", "legal_name", "city", "state_code",
          "country_name", "country_key", "inspection_end_date", "act_cfr_number",
          "short_description", "ingested_at")
PAGE_SIZE = 1000
DEFAULT_MAX_DROP_PCT = 20
EXIT_NO_CHANGE = 10

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CFR_RE = re.compile(r"^21 CFR (\d{1,4}\.\d+)")


class SnapshotError(ValueError):
    """스냅샷을 만들 수 없는 입력(형식 위반·0행)."""


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def section_of(code: str) -> str:
    """`21 CFR 211.100(a)` → `211.100`. 21 CFR 이 아니면 ""."""
    m = _CFR_RE.match(code or "")
    return m.group(1) if m else ""


def fetch_rows(base_url: str, key: str, *, http: Any = None,
               page_size: int = PAGE_SIZE) -> list[dict[str, Any]]:
    """service_role 로 필요한 열만 읽는다(지적 본문·주소 열은 select 하지 않는다).
    ★예외 본문을 출력하지 않는다(서비스 키 유출 방지) — 상태 코드만."""
    if http is None:
        import requests  # 지연 import — 테스트는 http 를 주입한다
        http = requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        r = http.get(f"{base_url}/rest/v1/{TABLE}",
                     params={"select": ",".join(FIELDS), "order": "inspection_id.asc,citation_id.asc",
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


def _top(c: Counter) -> str:
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if c else ""


def build_snapshot(rows: list[dict[str, Any]], *, as_of: str = "") -> dict[str, Any]:
    """원장 행 → 제조소 단위 집계본. 순수 함수(같은 입력이면 같은 바이트)."""
    if not rows:
        raise SnapshotError("0행 — 원장이 비었거나 읽기가 잘못됐다")
    ent: dict[tuple[str, str], dict[str, Any]] = {}
    insp_dates: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    code_insp: dict[tuple[str, str], dict[str, set]] = defaultdict(lambda: defaultdict(set))
    sec_insp: dict[tuple[str, str], dict[str, set]] = defaultdict(lambda: defaultdict(set))
    titles: dict[str, Counter] = defaultdict(Counter)
    ingested = ""
    all_insp: set = set()
    for r in rows:
        iid = _clean(r.get("inspection_id"))
        name = _clean(r.get("legal_name"))
        date = _clean(r.get("inspection_end_date"))[:10]
        if not iid:
            raise SnapshotError("inspection_id 비어 있음")
        if not name:
            raise SnapshotError(f"legal_name 비어 있음: {iid!r}")
        if not _DATE_RE.match(date):
            raise SnapshotError(f"inspection_end_date 형식: {date!r} ({iid})")
        ingested = max(ingested, _clean(r.get("ingested_at"))[:10])
        code = _clean(r.get("act_cfr_number"))
        sec = section_of(code)
        if not sec:
            continue  # 21 CFR 이 아닌 인용은 싣지 않는다
        key = (_clean(r.get("fei_number")), name)
        if key not in ent:
            ent[key] = {"fei": key[0], "name": name, "city": _clean(r.get("city")),
                        "state": _clean(r.get("state_code")),
                        "country": _clean(r.get("country_key")),
                        "country_name": _clean(r.get("country_name"))}
        insp_dates[key][iid] = date
        code_insp[key][code].add(iid)
        sec_insp[key][sec].add(iid)
        all_insp.add(iid)
        t = _clean(r.get("short_description"))
        if t:
            titles[code][t] += 1
    if not ent:
        raise SnapshotError("21 CFR 인용 0건")

    def tally(m: dict[str, set], dates: dict[str, str]) -> list[list[Any]]:
        out = [[k, len(ids), max(dates[i] for i in ids)] for k, ids in m.items()]
        out.sort(key=lambda x: (-x[1], x[0]))
        return out

    firms = []
    for key in sorted(ent):
        dates = insp_dates[key]
        firms.append({**ent[key], "inspections": len(dates),
                      "first": min(dates.values()), "last": max(dates.values()),
                      "codes": tally(code_insp[key], dates),
                      "sections": tally(sec_insp[key], dates)})
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "as_of": as_of or ingested,
        "totals": {"firms": len(firms), "inspections": len(all_insp),
                   "codes": len({c for f in firms for c, *_ in f["codes"]})},
        "titles": {c: _top(titles[c]) for c in sorted(titles)},
        "firms": firms,
    }


def validate(snap: Any) -> list[str]:
    """형식 검사. 비어 있으면 정상 — 렌더 로더도 같은 함수를 쓴다."""
    if not isinstance(snap, dict) or snap.get("schema_version") != SCHEMA_VERSION:
        return ["schema_version"]
    errs: list[str] = []
    if not _DATE_RE.match(str(snap.get("as_of") or "")):
        errs.append("as_of")
    firms = snap.get("firms")
    if not isinstance(firms, list) or not firms:
        return errs + ["firms"]
    for f in firms:
        if not isinstance(f, dict) or not str(f.get("name") or "").strip():
            errs.append("firm:name")
            continue
        n = f.get("inspections")
        if not isinstance(n, int) or n < 1:
            errs.append(f"firm:{f.get('name')}:inspections")
        for axis in ("codes", "sections"):
            for it in f.get(axis) or []:
                if (not isinstance(it, list) or len(it) != 3 or not isinstance(it[1], int)
                        or not 1 <= it[1] <= (n if isinstance(n, int) else 0)
                        or not _DATE_RE.match(str(it[2]))):
                    errs.append(f"firm:{f.get('name')}:{axis}")
                    break
    return errs


def serialize(snapshot: dict[str, Any]) -> str:
    """머리 + 제조소 한 곳 = 한 줄(주기 재생성의 diff 가 실제 변경분만 보이게)."""
    head = {k: v for k, v in snapshot.items() if k != "firms"}
    lines = [json.dumps(head, ensure_ascii=False)[:-1] + ', "firms": [']
    rows = snapshot["firms"]
    for i, r in enumerate(rows):
        lines.append(json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                     + ("," if i < len(rows) - 1 else ""))
    lines.append("]}")
    return "\n".join(lines) + "\n"


def guard(old: "dict[str, Any] | None", new: dict[str, Any], *,
          max_drop_pct: int = DEFAULT_MAX_DROP_PCT) -> "str | None":
    """쓰면 안 되는 이유(문장) 또는 None. 변경 없음은 'unchanged'."""
    errs = validate(new)
    if errs:
        return f"검증 실패 {errs[:5]}"
    if old and old.get("schema_version") == SCHEMA_VERSION:
        if old.get("firms") == new["firms"] and old.get("titles") == new["titles"]:
            return "unchanged"
        before = len(old.get("firms") or [])
        after = len(new["firms"])
        if before and (before - after) * 100 >= before * max_drop_pct:
            return f"제조소 {before} → {after} (감소 {max_drop_pct}% 이상)"
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
    ap = argparse.ArgumentParser(description="FDA 지적 조항 × 제조소 공개 스냅샷 재생성")
    ap.add_argument("--supabase-url", default="")
    ap.add_argument("--service-role-key", default="")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
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
        snap = build_snapshot(rows)
    except (RuntimeError, SnapshotError) as exc:
        print(f"스냅샷 실패(파일 무변경): {exc}", file=sys.stderr)
        return 2

    t = snap["totals"]
    print(f"원장 {len(rows):,}행 → 제조소 {t['firms']:,} · 실사 {t['inspections']:,} · "
          f"21 CFR 표기 {t['codes']:,} (기준일 {snap['as_of']})")
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
