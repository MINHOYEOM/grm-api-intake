#!/usr/bin/env python3
"""FDA 지적 조항 화면 — 조항별 인용 빈도(`public.fda_inspection_citations`, 133) 집계·뷰.

한 가지 질문에 답한다: "이 조항으로 FDA 가 얼마나 자주 지적하나?"

  · 원천 = FDA Data Dashboard `inspections_citations`(수집기 collect_fda_citations.py).
    표는 RLS 로 닫혀 있어(anon 회수) 브라우저가 못 읽는다 → **집계본을 커밋**하고
    정적으로 렌더한다(`web/data/fda_citations.json`, 네트워크·난수·now() 0).
  · 분모 = **지적이 1건 이상 나온 실사**(FY 별). 표에는 지적이 없는 실사(NAI)가 없으므로
    "전체 실사 중 비율"이 아니다 — 문구도 그렇게 쓴다. 조항 비율 = 그 조항이 인용된 실사 수
    ÷ 지적이 나온 실사 수. 건수가 아니라 **실사 수**로 센다(한 실사가 같은 조항을 여러 줄
    인용해도 1회).
  · 법적 판단을 싣지 않는다 — "FDA 가 인용한 빈도"만.

갱신(수동, 월간 적재 뒤):
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \\
      py -3.12 -X utf8 web/clause_citations.py --out web/data/fda_citations.json
데이터를 못 읽으면 기존 파일을 건드리지 않고 exit 2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parent / "data" / "fda_citations.json"
SCHEMA = 1
#: 집계본에 싣는 최소 실사 수. 이보다 적게 인용된 조항은 비율이 한두 건에 흔들린다.
MIN_INSPECTIONS = 30
PAGE_SIZE = 1000
_SECTION_RE = re.compile(r"^21 CFR (\d{3}\.\d+)")


def section_of(code: str) -> str:
    """`21 CFR 211.100(a)` → `211.100`. 21 CFR 조항이 아니면 ""."""
    m = _SECTION_RE.match((code or "").strip())
    return m.group(1) if m else ""


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# ── 집계(순수 함수) ───────────────────────────────────────────────────────────


def build_snapshot(rows: list[dict[str, Any]], *, as_of: str,
                   min_inspections: int = MIN_INSPECTIONS) -> dict[str, Any]:
    """원장 행 → 집계본. 행 키: inspection_id, fiscal_year, act_cfr_number, short_description.

    조항 코드는 FDA 가 적은 그대로(`211.22(d)`)와 섹션(`211.22`) 두 층으로 센다."""
    years = sorted({int(r["fiscal_year"]) for r in rows})
    insp_by_year: dict[int, set] = defaultdict(set)
    code_cit: Counter = Counter()
    code_insp: dict[str, set] = defaultdict(set)
    code_insp_y: dict[tuple[str, int], set] = defaultdict(set)
    code_titles: dict[str, Counter] = defaultdict(Counter)
    sec_cit: Counter = Counter()
    sec_insp: dict[str, set] = defaultdict(set)
    sec_insp_y: dict[tuple[str, int], set] = defaultdict(set)
    all_insp: set = set()
    for r in rows:
        iid, fy = str(r["inspection_id"]), int(r["fiscal_year"])
        code = _clean(r.get("act_cfr_number"))
        if not code:
            continue
        all_insp.add(iid)
        insp_by_year[fy].add(iid)
        code_cit[code] += 1
        code_insp[code].add(iid)
        code_insp_y[(code, fy)].add(iid)
        title = _clean(r.get("short_description"))
        if title:
            code_titles[code][title] += 1
        sec = section_of(code)
        if sec:
            sec_cit[sec] += 1
            sec_insp[sec].add(iid)
            sec_insp_y[(sec, fy)].add(iid)

    def title_of(code: str) -> str:
        c = code_titles.get(code)
        if not c:
            return ""
        # 최빈 문구, 동수면 사전순 — 같은 입력이면 같은 출력.
        return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    codes = [{
        "code": code, "title": title_of(code), "citations": code_cit[code],
        "inspections": len(ids),
        "by_year": [len(code_insp_y.get((code, y), ())) for y in years],
    } for code, ids in code_insp.items() if len(ids) >= min_inspections]
    codes.sort(key=lambda c: (-c["inspections"], c["code"]))
    sections = [{
        "section": sec, "citations": sec_cit[sec], "inspections": len(ids),
        "by_year": [len(sec_insp_y.get((sec, y), ())) for y in years],
    } for sec, ids in sec_insp.items() if len(ids) >= min_inspections]
    sections.sort(key=lambda s: (-s["inspections"], s["section"]))
    return {
        "schema": SCHEMA,
        "source": "FDA Data Dashboard - Inspections Citations",
        "as_of": as_of,
        "min_inspections": min_inspections,
        "years": years,
        "inspections": {"total": len(all_insp),
                        "by_year": [len(insp_by_year.get(y, ())) for y in years]},
        "codes": codes,
        "sections": sections,
    }


def validate_snapshot(data: Any) -> list[str]:
    """스키마·일관성 검사. 비어 있으면 정상. 어긋나면 사유 목록 — 로더가 거부한다."""
    errs: list[str] = []
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return ["schema"]
    years = data.get("years") or []
    tot = (data.get("inspections") or {})
    if not years or len(tot.get("by_year") or []) != len(years) or not tot.get("total"):
        errs.append("inspections")
    for key, idkey in (("codes", "code"), ("sections", "section")):
        for it in data.get(key) or []:
            if len(it.get("by_year") or []) != len(years):
                errs.append(f"{key}:{it.get(idkey)}:by_year")
            if it.get("inspections", 0) > tot.get("total", 0):
                errs.append(f"{key}:{it.get(idkey)}:over_total")
            if sum(it.get("by_year") or []) != it.get("inspections"):
                errs.append(f"{key}:{it.get(idkey)}:year_sum")
    return errs


def load_snapshot(path: Path = DATA_FILE) -> "dict[str, Any] | None":
    """집계본 로드. 파일 부재·스키마 불일치는 None(= 화면을 만들지 않는다)."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if validate_snapshot(data):
        raise ValueError(f"fda_citations.json 검증 실패: {validate_snapshot(data)[:5]}")
    if not data.get("codes"):
        return None
    return data


# ── 뷰(표시용 수치) ───────────────────────────────────────────────────────────


def fmt_pct(num: int, den: int) -> str:
    """비율 문자열. 10% 이상은 정수, 미만은 소수 1자리. 분모 0 이면 '-'."""
    if den <= 0:
        return "-"
    v = num * 100 / den
    return f"{v:.0f}%" if v >= 10 else f"{v:.1f}%"


def _year_cells(by_year: list[int], totals: list[int], years: list[int]) -> list[dict[str, Any]]:
    shares = [(n / t if t else 0.0) for n, t in zip(by_year, totals)]
    top = max(shares) if shares and max(shares) > 0 else 1.0
    return [{"year": y, "n": n, "pct": fmt_pct(n, t),
             "h": round(s / top * 100)}
            for y, n, t, s in zip(years, by_year, totals, shares)]


def ranking_view(data: dict[str, Any], *, limit: int = 30) -> dict[str, Any]:
    """순위 표 뷰 — 조항(FDA 표기) 상위 `limit` 개. 막대 길이는 최상위 대비."""
    totals = data["inspections"]["by_year"]
    total = data["inspections"]["total"]
    years = data["years"]
    top = data["codes"][:limit]
    peak = top[0]["inspections"] if top else 1
    rows = []
    for rank, c in enumerate(top, 1):
        rows.append({
            "rank": rank, "code": c["code"], "title": c["title"],
            "inspections": c["inspections"], "citations": c["citations"],
            "pct": fmt_pct(c["inspections"], total),
            "bar": round(c["inspections"] / peak * 100),
            "section": section_of(c["code"]),
            "years": _year_cells(c["by_year"], totals, years),
        })
    return {"rows": rows, "total": total, "years": years,
            "totals_by_year": totals, "as_of": data["as_of"], "source": data["source"]}


def section_view(data: "dict[str, Any] | None", section: str) -> "dict[str, Any] | None":
    """조항 페이지(`211.192`)용 블록 뷰. 집계본에 없으면 None(= 블록을 내지 않는다).

    `subs` = 그 섹션 안에서 FDA 가 하위항까지 적어 인용한 조항(`211.100(a)`, `(b)`)."""
    if not data or not section:
        return None
    sec = next((s for s in data["sections"] if s["section"] == section), None)
    if not sec:
        return None
    totals = data["inspections"]["by_year"]
    total = data["inspections"]["total"]
    prefix = f"21 CFR {section}"
    subs = [{"code": c["code"], "title": c["title"], "inspections": c["inspections"],
             "pct": fmt_pct(c["inspections"], total)}
            for c in data["codes"]
            if c["code"] == prefix or c["code"].startswith(prefix + "(")]
    rank = 1 + sum(1 for s in data["sections"] if s["inspections"] > sec["inspections"])
    return {"section": section, "code": prefix, "inspections": sec["inspections"],
            "citations": sec["citations"], "pct": fmt_pct(sec["inspections"], total),
            "rank": rank, "n_sections": len(data["sections"]),
            "years": _year_cells(sec["by_year"], totals, data["years"]),
            "subs": subs if len(subs) > 1 or (subs and subs[0]["code"] != prefix) else [],
            "total": total, "as_of": data["as_of"], "source": data["source"]}


# ── 갱신 CLI ─────────────────────────────────────────────────────────────────


def fetch_rows(base_url: str, key: str, *, timeout: int = 60) -> list[dict[str, Any]]:
    """PostgREST 로 원장 전량(≈11k행, 필요한 5개 열만). 실패하면 예외 — 부분 집계 금지.
    ★예외 본문을 출력하지 않는다(서비스 키 유출 방지)."""
    import requests
    url = f"{base_url.rstrip('/')}/rest/v1/fda_inspection_citations"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = requests.get(url, headers=headers, timeout=timeout, params={
            "select": "inspection_id,citation_id,fiscal_year,act_cfr_number,short_description,ingested_at",
            "order": "inspection_id,citation_id", "limit": PAGE_SIZE, "offset": offset})
        if resp.status_code != 200:
            raise RuntimeError(f"http_{resp.status_code}")
        batch = resp.json()
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(DATA_FILE))
    p.add_argument("--min-inspections", type=int, default=MIN_INSPECTIONS)
    args = p.parse_args(argv)
    base, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get(
        "SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (base and key):
        print("clause_citations: $SUPABASE_URL/$SUPABASE_SERVICE_ROLE_KEY 필요", file=sys.stderr)
        return 2
    try:
        rows = fetch_rows(base, key)
    except Exception as e:  # noqa: BLE001 - 본문 비노출
        print(f"clause_citations: 읽기 실패({type(e).__name__})", file=sys.stderr)
        return 2
    if not rows:
        print("clause_citations: 0행 — 파일을 건드리지 않는다", file=sys.stderr)
        return 2
    as_of = max(str(r.get("ingested_at") or "")[:10] for r in rows)
    snap = build_snapshot(rows, as_of=as_of, min_inspections=args.min_inspections)
    errs = validate_snapshot(snap)
    if errs:
        print(f"clause_citations: 검증 실패 {errs[:5]}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8", newline="\n")
    print(f"clause_citations: {len(snap['codes'])}조항·{len(snap['sections'])}섹션 "
          f"(실사 {snap['inspections']['total']:,}) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
