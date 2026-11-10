"""[FDA 지적 조항 × 업체 전체 목록 2026-11-10] /findings/citations/firms/ 뷰(순수 함수).

데이터: web/data/fda_citation_firms.json(clause_firms_page.load_snapshot) — FDA Data Dashboard 실사
지적을 제조소(FEI) 단위로 집계한 커밋 스냅샷. 업체 페이지·조항 페이지에는 일부만 나오던 것을
**전 제조소**(수집된 데이터가 안 보이는 곳이 없게) 한 장에 싣는다. 네트워크·DB·now() 0.

사실(제조소·실사 수·최근 실사일·인용 조항·조항별 실사 수)만 싣는다 — 해석·평가 문구 없음.
링크 규율(없는 페이지로 보내는 링크는 무링크보다 나쁘다):
  · 제조소명 → 업체 페이지: clause_firms_page 매칭 결과(`site_slugs` = firm_id → slug)에서, 그 언어
    트리에 실제로 선 페이지만. 새 매칭을 만들지 않는다.
  · 조항 코드 → 조항 페이지: 그 언어 트리에 선 조항 slug(`clause_slugs`)에 섹션이 있을 때만.
"""
from __future__ import annotations

import re
from typing import Any, Callable

import clause_firms_page as _cf
import import_alerts_page as _iap

ROUTE = "findings/citations/firms/"

_SECTION_RE = re.compile(r"^21 CFR (\d{3}\.\d+)")
_PREFIX = "21 CFR "


def country_key(f: dict[str, Any]) -> str:
    c = str(f.get("country") or "").strip().upper()
    return c or "x-unknown"


def list_view(snapshot: "dict[str, Any] | None", *, tr: Callable[..., str], lang: str,
              site_slugs: "dict[str, str] | None" = None,
              clause_slugs: "set[str] | None" = None,
              clause_slug: "Callable[[str], str] | None" = None) -> "dict[str, Any] | None":
    """스냅샷 → 화면 뷰. 스냅샷이 없으면 None. 정렬: 실사 수 ↓ → 최근 실사일 ↓ → 이름."""
    if not snapshot:
        return None
    slugs = site_slugs or {}
    cslugs = clause_slugs or set()
    slug_of = clause_slug or (lambda s: s.replace(".", "-"))
    titles = snapshot.get("titles") or {}
    rows: list[dict[str, Any]] = []
    legend: dict[str, str] = {}   # 표에 실제 나온 코드 → FDA 짧은 제목(행마다 반복하지 않고 한 번만 싣는다)
    for f in snapshot.get("firms") or []:
        if not _cf._valid_firm(f):
            continue
        ckey = country_key(f)
        label_ko = _iap.COUNTRY_LABELS_KO.get(ckey)
        country_name = str(f.get("country_name") or f.get("country") or "").strip()
        codes = []
        for code, n, _last in sorted(f["codes"], key=lambda c: (-c[1], c[0])):
            m = _SECTION_RE.match(code)
            sec = m.group(1) if m else ""
            href = slug_of(sec) if sec and slug_of(sec) in cslugs else ""
            codes.append({"label": code[len(_PREFIX):] if code.startswith(_PREFIX) else code,
                          "n": n, "href": href})
            if code in titles:
                legend[code] = titles[code]
        city = str(f.get("city") or "").strip()
        state = str(f.get("state") or "").strip() if ckey == "US" else ""
        rows.append({
            "name": f["name"].strip(),
            "place": ", ".join(x for x in (city, state) if x),
            "country_key": ckey,
            "country_label": tr(label_ko) if label_ko else country_name,
            "inspections": f["inspections"],
            "last": f["last"],
            "slug": slugs.get(_cf.firm_id(f), ""),
            "codes": codes,
        })
    if not rows:
        return None
    rows.sort(key=lambda r: (-r["inspections"], _cf._neg_date(r["last"]), r["name"].casefold(),
                             r["place"]))
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for r in rows:
        counts[r["country_key"]] = counts.get(r["country_key"], 0) + 1
        labels.setdefault(r["country_key"], r["country_label"])
    countries = [{"key": k, "label": labels[k], "n": n}
                 for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], labels[kv[0]]))]
    return {
        "as_of": str(snapshot.get("as_of") or ""),
        "source": str(snapshot.get("source") or ""),
        "rows": rows,
        "total": len(rows),
        "n_countries": len(countries),
        "countries": countries,
        "legend": [{"label": c[len(_PREFIX):] if c.startswith(_PREFIX) else c, "title": t}
                   for c, t in sorted(legend.items())],
        "linked": sum(1 for r in rows if r["slug"]),
        "clause_links": sum(1 for r in rows for c in r["codes"] if c["href"]),
    }
