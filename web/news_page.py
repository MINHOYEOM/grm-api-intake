"""최근 규제 소식(/news/) — 정본 web/data/news.json 의 표시용 뷰.

정본은 `news_refresh.py` 가 공개 RPC(regulatory_news, 마이그 138)로 만든다. 여기는 순수 함수만 둔다
(네트워크·DB 0 — render.py 순수성 가드). 정본이 없으면 None → 페이지·링크·sitemap 이 조용히 빠진다.

그룹은 소스 이름에서 **데이터로** 파생한다(손목록 금지): 모르는 소스는 '규제기관 소식'으로 떨어지고,
테스트가 정본의 모든 소스가 그룹에 매핑되는지 센다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

NEWS_FILE = Path(__file__).resolve().parent / "data" / "news.json"
SCHEMA = "grm-news/v1"
ROUTE = "news/"
GROUP_ORDER: tuple[str, ...] = ("regulator", "recall", "industry", "gazette")
#: 그룹 → (한국어, 영어) 라벨
GROUP_LABELS: "dict[str, tuple[str, str]]" = {
    "regulator": ("규제기관 소식", "Regulator news"),
    "recall": ("회수·품질결함", "Recalls and defects"),
    "industry": ("업계 단체", "Industry bodies"),
    "gazette": ("관보", "Official gazette"),
}
#: 소스 → 그룹. MHRA 는 source_kind 로 갈린다(회수·결함 vs 그 밖).
_SOURCE_GROUP = {
    "OpenFDA Recall": "recall", "Health Canada": "recall",
    "ECA Academy": "industry", "ISPE": "industry",
    "Federal Register": "gazette",
}
#: 화면 표기(한국어, 영어). 목록에 없는 소스는 원문 이름 그대로.
SOURCE_LABELS: "dict[str, tuple[str, str]]" = {
    "OpenFDA Recall": ("FDA 회수", "FDA recall"),
    "Health Canada": ("캐나다 보건부 회수", "Health Canada recall"),
    "MHRA Inspectorate": ("영국 MHRA", "UK MHRA"),
    "MFDS": ("식약처", "MFDS"),
    "EMA": ("EMA", "EMA"),
    "PIC/S": ("PIC/S", "PIC/S"),
    "WHO": ("WHO", "WHO"),
    "ECA Academy": ("ECA Academy", "ECA Academy"),
    "ISPE": ("ISPE", "ISPE"),
    "Federal Register": ("미국 관보", "Federal Register"),
}
_HANGUL = re.compile("[가-힣]")
MAX_TITLE = 200


def load_news(path: Path = NEWS_FILE) -> "dict[str, Any] | None":
    """정본이 없으면 None. 스키마가 다르면 ValueError(깨진 정본이 조용히 나가지 않게)."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"news.json 스키마 불일치: {data.get('schema')!r}")
    return data


def group_of(item: "dict[str, Any]") -> str:
    g = _SOURCE_GROUP.get(item.get("source", ""))
    if g:
        return g
    if item.get("source") == "MHRA Inspectorate" and re.search(
            r"recall|defect", item.get("source_kind", ""), re.I):
        return "recall"
    return "regulator"


def _clip(title: str) -> str:
    t = " ".join(title.split())
    if len(t) <= MAX_TITLE:
        return t
    return t[:MAX_TITLE].rsplit(" ", 1)[0].rstrip(",;:. ") + "…"


def _week_start(d: str) -> str:
    y, m, dd = (int(x) for x in d.split("-"))
    dt = date(y, m, dd)
    return (dt - timedelta(days=dt.weekday())).isoformat()


def build_view(data: "dict[str, Any] | None", lang: str) -> "dict[str, Any] | None":
    """주 단위 묶음 뷰. 영어 화면은 제목이 한국어인 항목을 싣지 않고 개수만 밝힌다
    (영어 화면에 한국어 값을 띄우지 않는다 — 못 옮기면 개수로)."""
    if not data or not data.get("items"):
        return None
    hidden_ko = 0
    weeks: "dict[str, list[dict[str, str]]]" = {}
    counts: Counter = Counter()
    for it in data["items"]:
        if lang == "en" and _HANGUL.search(it["title"]):
            hidden_ko += 1
            continue
        g = group_of(it)
        counts[g] += 1
        lab = SOURCE_LABELS.get(it["source"], (it["source"], it["source"]))[1 if lang == "en" else 0]
        weeks.setdefault(_week_start(it["published_date"]), []).append({
            "date": it["published_date"], "source": lab, "kind": it.get("source_kind", ""),
            "group": g, "title": _clip(it["title"]), "url": it["official_url"],
        })
    if not weeks:
        return None
    ordered = [{"start": s, "items": weeks[s]} for s in sorted(weeks, reverse=True)]
    shown = sum(counts.values())
    return {
        "weeks": ordered, "total": shown, "hidden_ko": hidden_ko,
        "window_days": data.get("window_days", 0),
        "as_of": max(w["items"][0]["date"] for w in ordered),
        "groups": [{"code": g, "label": GROUP_LABELS[g][1 if lang == "en" else 0], "count": counts[g]}
                   for g in GROUP_ORDER if counts[g]],
    }
