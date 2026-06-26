#!/usr/bin/env python3
"""GRM 웹 렌더러 (P2·P4) — `grm-web-card/v1` JSON → 정적 멀티페이지 사이트.

순수·결정론 빌더. `web/data/briefs/*.json`(주차별 브리프)을 읽어 `dist/` 에
랜딩(`index.html`)·아카이브(`archive/index.html`)·브리프 상세
(`briefs/{slug}/index.html`)를 생성한다. 디자인 계약 = `GRM_웹_프로토타입_v4.html`
+ 검색/네비/모션 = `GRM_웹_P4_아카이브검색_프로토타입_v2.html`
(CSS 는 `assets/grm.css` 로 동결 추출).

불변식
  1. 순수 렌더 — 사실/URL/숫자/업체명 무변형(JSON 값 그대로). 렌더러가 보유하는
     텍스트는 디자인 정적 카피(템플릿)와 면책 캐논 문안(brief.html)뿐.
  2. 결정론 — 같은 입력 JSON → 바이트 동일 HTML. `datetime.now`/난수 0,
     정렬은 입력에서만 파생, autoescape on, 출력은 항상 LF/UTF-8.
  3. 정적·$0 — 외부 fetch 0, 런타임 서버 0.
  4. 멀티페이지 — 라우트별 개별 HTML. 링크는 페이지 깊이별 상대경로(호스트 무관).

스키마 한계 2건(§1.a/§1.b)은 결정론 파생으로 처리(v1.1 후보):
  - issue 번호: data/briefs 의 publish_date 오름차순 순위(가장 오래된=1).
  - 브리프 제목: tldr[0] 있으면 사용, 없으면 publish_date 파생 "{Y}년 {M}월 {N}주차".

P4 — 아카이브 교차검색(정적·클라이언트사이드):
  - `dist/assets/search-index.json` 을 빌드시 결정론 파생(카드 1개=1엔트리 + facet
    메타 + 호 메타). 사실/URL/제목 재생성 0 — 카드 기존 값만 담는다(무변형).
  - 검색·필터는 `assets/archive.js`(정적 클라이언트사이드)가 이 인덱스로 동작.
    JS 미로드/fetch 실패 시 서버사이드로 이미 렌더된 호 목록이 그대로 보임(graceful).
  - 상세 카드 앵커는 `document_id`(=card.id) 기준(검색결과→카드 점프 안정화). 인덱스의
    href 와 상세 article id 는 같은 `_card_anchor()` 로 파생 — 항상 일치.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# ── 경로(이 파일 기준 — cwd 무관) ──────────────────────────────────────────────
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
PARTIALS_PARENT = WEB_DIR                  # "partials/card.html" 해석용
DATA_DIR = WEB_DIR / "data" / "briefs"
ASSETS_DIR = WEB_DIR / "assets"
DIST_DIR = WEB_DIR / "dist"

# ── v4 디자인 계약에서 가져온 결정론 매핑 ──────────────────────────────────────
# 사실표에서 mono(ASCII 데이터)로 표기하는 라벨(v4 dataLabels 동결). 한글에 mono 금지.
MONO_LABELS = {"발행일", "문서번호", "실사일", "Class", "회수 등급"}
SIG_COLOR = {"High": "var(--hi)", "Med": "var(--med)", "Low": "var(--lo)"}
SECTION_ICON = {"글로벌": "ti-world", "국내": "ti-map-pin", "Recall": "ti-alert-triangle"}
_SECTION_ICON_DEFAULT = "ti-folder"
MARKS = "①②③④⑤"


# ── 날짜 파생(결정론) ──────────────────────────────────────────────────────────
def _date_parts(date_str: str) -> tuple[int, int, int]:
    y, m, d = (int(x) for x in date_str.split("-"))
    return y, m, d


def title_dateform(publish_date: str) -> str:
    """publish_date → "{Y}년 {M}월 {N}주차". 주차 = (day-1)//7 + 1 (결정론)."""
    y, m, d = _date_parts(publish_date)
    week = (d - 1) // 7 + 1
    return f"{y}년 {m}월 {week}주차"


def _date_dotted(publish_date: str) -> str:
    """"2026-06-22" → "2026 · 06 · 22" (표지 .ch 라벨)."""
    return " · ".join(publish_date.split("-"))


# href 에 들어갈 수 있는 안전 스킴 화이트리스트(방어선). autoescape 는 속성 탈출만 막고
# 스킴(javascript:·data:·vbscript:)은 못 막으므로, 렌더러가 마지막 게이트로 거른다.
# 실데이터 URL 은 전부 http(s) 라 출력 byte-동일(무변형); 비허용 스킴만 ""→링크 생략.
_SAFE_URL_PREFIXES = ("https://", "http://", "/", "#")


def _safe_url(u: str) -> str:
    return u if (u or "").strip().lower().startswith(_SAFE_URL_PREFIXES) else ""


def _brief_title(brief_meta: dict[str, Any]) -> str:
    """아카이브/표지 제목 = tldr[0] 있으면 사용, 없으면 날짜 파생(§1.b)."""
    tldr = brief_meta.get("tldr") or []
    if tldr and tldr[0]:
        return tldr[0]
    return title_dateform(brief_meta.get("publish_date", ""))


def _card_anchor(card: dict[str, Any]) -> str:
    """상세 카드의 안정 앵커 = document_id(=card.id). 검색결과→카드 점프용(P4 §2.2).

    상세 article id·TOC href(brief.html)와 search-index 의 href 가 **모두** 이 함수로
    파생 → 항상 일치(드리프트 0). id 없는 적대/합성 입력은 render_order 폴백.
    """
    cid = str(card.get("id") or "").strip()
    return cid if cid else f"c{card.get('render_order')}"


# ── 카드 뷰모델(표시 플래그만 산출 — 사실/URL 값은 절대 변형 금지) ─────────────
def _card_view(card: dict[str, Any]) -> dict[str, Any]:
    quotes_in = card.get("quotes") or []
    multi = len(quotes_in) > 1
    any_trans = any(q.get("translation") for q in quotes_in)  # null·"" 둘 다 falsy
    quotes: list[dict[str, Any]] = []
    for i, q in enumerate(quotes_in):
        trans = q.get("translation")
        quotes.append({
            "original": q.get("original", ""),
            "translation": trans,
            "show_translation": bool(trans),           # null/"" → 번역 줄 생략
            "mark": (MARKS[i] if (multi and i < len(MARKS)) else ""),
        })

    src = card.get("sources") or {}
    lc = src.get("link_check") or {}
    is_pdf = bool(src.get("official_is_pdf"))
    sources = {
        "info": {
            "url": _safe_url(src.get("info_url", "")),
            "state": lc.get("info", "pending"),
            "icon": "ti-database",
            "text": "data source",
        },
        "official": {
            "url": _safe_url(src.get("official_url", "")),
            "state": lc.get("official", "pending"),
            "icon": ("ti-file-type-pdf" if is_pdf else "ti-file-text"),
            "text": ("PDF 원문" if is_pdf else "공식 페이지"),
        },
    }

    return {
        "render_order": card.get("render_order"),
        "anchor": _card_anchor(card),
        "group": card.get("group"),
        "group_label": card.get("group_label"),
        "group_head": None,                            # 섹션 조립 시 결정
        "is_evA": card.get("evidence_level") == "A",
        "card_type": card.get("card_type", ""),
        "agency": card.get("agency", ""),
        "headline_target": card.get("headline_target", ""),
        "title_issue": card.get("title_issue", ""),
        "evidence_level": card.get("evidence_level", ""),
        "signal_label": card.get("signal_label", ""),
        "signal_tier": card.get("signal_tier", ""),
        "sig_color": SIG_COLOR.get(card.get("signal_label"), "var(--lo)"),
        "modality": card.get("modality"),
        "type_tag": card.get("type_tag"),
        "summary": card.get("summary", ""),
        "facts": [{"label": f.get("label", ""),
                   "value": f.get("value", ""),
                   "mono": f.get("label", "") in MONO_LABELS}
                  for f in (card.get("facts") or [])],
        "merged": (card.get("merged_count") or 1) > 1,
        "merged_count": card.get("merged_count", 1),
        "merged_items": card.get("merged_items") or [],
        "quotes": quotes,
        "quote_label": (("원문 및 번역" if any_trans else "원문") if quotes_in else None),
        "key_facts": card.get("key_facts") or [],
        "evidence_basis": card.get("evidence_basis", ""),
        "implication": card.get("implication", ""),
        "checks": card.get("checks") or [],
        "sources": sources,
    }


def _is_renderable(card: dict[str, Any]) -> bool:
    """렌더 제외 카드 판별(방어적 — 상류 순수성 미가정, §3.2/§3.3 렌더러 책임).

    병합 멤버(`merged_into` truthy)와 watch(비카드 영역)는 렌더하지 않는다. 스키마 v1 정상
    데이터엔 없음(상류 `assemble_web_brief` 가 이미 제외) — 적대/직접 주입에 대한 방어선.
    정렬·섹션 카운트·TOC 산출 *이전*에 적용해 제외 카드가 목차·건수에 새지 않게 한다.
    """
    if card.get("merged_into"):
        return False
    if card.get("group") == "watch" or card.get("section") == "watch":
        return False
    return True


def _build_sections(card_views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """render_order 순 카드를 group(섹션)·group_label(소제목)별로 연속 묶음.

    재정렬 금지 — 입력 순서 그대로 인접 그룹핑(v4 JS 동치). 섹션 count 는 파생.
    """
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    cur_grp: Any = object()                            # sentinel
    for cv in card_views:
        if cur is None or cv["group"] != cur["name"]:
            cur = {
                "name": cv["group"],
                "slug": cv["group"],                   # 앵커 id (HTML5 허용 — 한글 가능)
                "icon": SECTION_ICON.get(cv["group"], _SECTION_ICON_DEFAULT),
                "cards": [],
            }
            sections.append(cur)
            cur_grp = object()                         # 새 섹션 → 그룹 리셋
        gl = cv.get("group_label")
        if gl and gl != cur_grp:
            cur_grp = gl
            cv["group_head"] = gl
        else:
            cv["group_head"] = None
        cur["cards"].append(cv)
    for s in sections:
        s["count"] = len(s["cards"])
    return sections


def _norm_coverage(cov: dict[str, Any]) -> dict[str, Any]:
    ev = cov.get("evidence") or {}
    return {
        "intake_total": cov.get("intake_total", 0),
        "rendered": cov.get("rendered", 0),
        "evidence": {"A": ev.get("A", 0), "B": ev.get("B", 0), "C": ev.get("C", 0)},
    }


# ── 브리프 로드·issue 번호 부여 ───────────────────────────────────────────────
def load_briefs(data_dir: Path) -> list[dict[str, Any]]:
    """data_dir 의 *.json 을 로드. 파일명 정렬로 결정론적 순회."""
    briefs = []
    for fp in sorted(data_dir.glob("*.json")):
        briefs.append(json.loads(fp.read_text(encoding="utf-8")))
    return briefs


def assign_issue_numbers(briefs: list[dict[str, Any]]) -> dict[str, int]:
    """publish_date 오름차순 순위로 issue 번호 부여(가장 오래된=1).

    계약 = "주차별 1파일"(고유 publish_date). 중복 publish_date 는 slug 충돌로 한 브리프가
    조용히 덮어써지므로(데이터 손실), 조용한 손실 대신 즉시 실패한다(verbatim 불변식 보호).
    """
    dates = [b["brief"].get("publish_date", "") for b in briefs]
    dups = sorted({d for d in dates if dates.count(d) > 1})
    if dups:
        raise SystemExit(f"중복 publish_date — 주차별 1파일 계약 위반(slug 충돌): {dups}")
    keyed = sorted(briefs, key=lambda b: b["brief"].get("publish_date", ""))
    return {b["brief"].get("publish_date", ""): i + 1 for i, b in enumerate(keyed)}


# ── 컨텍스트 빌더 ─────────────────────────────────────────────────────────────
def _brief_context(brief: dict[str, Any], issue_no: int) -> dict[str, Any]:
    bm = brief["brief"]
    return {
        "issue_no": issue_no,
        "run_date_kst": bm.get("run_date_kst", ""),
        "publish_date": bm.get("publish_date", ""),
        "window": bm.get("window", ""),
        "title_dateform": title_dateform(bm.get("publish_date", "")),
        "coverage": _norm_coverage(bm.get("coverage") or {}),
        "tldr": bm.get("tldr") or [],
        "ai_disclosure": bool(bm.get("ai_disclosure")),
        "agencies": bm.get("agencies") or [],
    }


def _issue_row(brief: dict[str, Any], issue_no: int, latest_slug: str) -> dict[str, Any]:
    bm = brief["brief"]
    cov = _norm_coverage(bm.get("coverage") or {})
    pub = bm.get("publish_date", "")
    ev = cov["evidence"]
    agencies = list(bm.get("agencies") or [])
    return {
        "slug": pub,
        "issue_no": issue_no,
        "title": _brief_title(bm),
        "date": pub,
        "month": pub[:7],                          # YYYY-MM (publish_date 파생 — facet 기간)
        "agencies": agencies,                      # 칩(기관) per-tag 렌더(v2)
        "tags": " · ".join(agencies),              # (구) 조인 문자열 — 하위호환
        "count": cov["rendered"],
        "ev": f"A{ev['A']} · B{ev['B']}",
        "latest": pub == latest_slug,
    }


def _cover_context(brief: dict[str, Any], issue_no: int) -> dict[str, Any]:
    bm = brief["brief"]
    cov = _norm_coverage(bm.get("coverage") or {})
    pub = bm.get("publish_date", "")
    return {
        "issue_no": issue_no,
        "slug": pub,
        "publish_date": pub,
        "date_dotted": _date_dotted(pub),
        "rendered": cov["rendered"],
        "intake_total": cov["intake_total"],     # 다크밴드 바인딩(단일 파생 경로)
        "evidence": cov["evidence"],              # 다크밴드 Evidence A/B
        "title_dateform": title_dateform(pub),    # 다크밴드 "{Y}년 {M}월 {N}주차"
        "window": bm.get("window", ""),
        "title": _brief_title(bm),
        "tldr": bm.get("tldr") or [],
    }


# ── 검색 인덱스(P4 — 정적·결정론·무변형) ──────────────────────────────────────
# 인덱스는 **아카이브 페이지(`archive/index.html`, 깊이 1)** 전용 → href 는 그 페이지
# 기준 상대경로(`../`). render.py 가 페이지마다 새로 만들지 않는 단일 산출물이라 접두를
# 여기 고정한다(검색은 spec 상 아카이브에만 얹는다 — P4 §2.3).
_ARCHIVE_REL = "../"


def _card_search_text(card: dict[str, Any]) -> str:
    """클라이언트 검색 대상 결합 문자열(소문자화는 클라이언트에서).

    카드 **기존 값 verbatim 결합만** — 새 텍스트 생성 0(무변형). 순서·구성은 P4 §2.1:
    target + issue + card_type + agency + facts[].value (+ summary·key_facts 있으면).
    빈 조각은 건너뛴다(공백 중복 방지 — 각 조각은 카드값의 verbatim 부분문자열로 유지).
    """
    parts: list[str] = [
        card.get("headline_target", ""),
        card.get("title_issue", ""),
        card.get("card_type", ""),
        card.get("agency", ""),
    ]
    parts += [f.get("value", "") for f in (card.get("facts") or [])]
    if card.get("summary"):
        parts.append(card["summary"])
    parts += [k for k in (card.get("key_facts") or []) if k]
    return " ".join(p for p in parts if p)


def _card_index_entry(card: dict[str, Any], *, issue_no: int, date: str,
                      month: str, vol_title: str) -> dict[str, Any]:
    """카드 1개 → 검색 인덱스 엔트리. 전 필드 카드 기존 값 파생(무변형)."""
    return {
        "issue_no": issue_no,
        "date": date,
        "month": month,
        "vol_title": vol_title,
        "agency": card.get("agency", ""),
        "category": card.get("category", ""),
        "modality": card.get("modality"),               # null 가능(필터 미해당)
        "card_type": card.get("card_type", ""),
        "evidence_level": card.get("evidence_level", ""),
        "signal_tier": card.get("signal_tier", ""),
        "target": card.get("headline_target", ""),
        "issue": card.get("title_issue", ""),           # 빈값이면 "" (JS 가 처리)
        "summary": card.get("summary", ""),
        # 상세 카드 앵커 — 상세 article id 와 동일 함수 파생(항상 점프 일치).
        "href": f"{_ARCHIVE_REL}briefs/{date}/index.html#{_card_anchor(card)}",
        "text": _card_search_text(card),
    }


def build_search_index(briefs: list[dict[str, Any]], issue_no_by_date: dict[str, int],
                       latest_slug: str) -> dict[str, Any]:
    """전 브리프 카드 → 검색 인덱스(facet 메타 + 호 메타 + 카드 엔트리).

    정렬(결정론): 카드 = date desc, 동일 호 내 render_order asc. facet 후보는 **실제
    존재값만** 노출(데이터 파생) — agency/category/modality 알파벳, months 최신순.
    호 메타(issues)는 baseline 서버목록과 JS 검색뷰가 동일하게 쓰는 단일 파생원
    (`_issue_row`)에서 만들어 두 경로 일관성 보장.
    """
    cards_idx: list[dict[str, Any]] = []
    issues_idx: list[dict[str, Any]] = []
    agencies: set[str] = set()
    categories: set[str] = set()
    modalities: set[str] = set()
    months: set[str] = set()

    # date desc 순 브리프 순회 → 각 호 내부는 render_order asc → 결합이 곧 최종 정렬.
    for b in sorted(briefs, key=lambda b: b["brief"].get("publish_date", ""), reverse=True):
        bm = b["brief"]
        date = bm.get("publish_date", "")
        month = date[:7]
        issue_no = issue_no_by_date[date]
        vol_title = _brief_title(bm)
        renderable = [c for c in (b.get("cards") or []) if _is_renderable(c)]
        cards_sorted = sorted(renderable,
                              key=lambda c: (c.get("render_order") is None,
                                             c.get("render_order")))
        for c in cards_sorted:
            entry = _card_index_entry(c, issue_no=issue_no, date=date,
                                      month=month, vol_title=vol_title)
            cards_idx.append(entry)
            if entry["agency"]:
                agencies.add(entry["agency"])
            if entry["category"]:
                categories.add(entry["category"])
            if entry["modality"]:
                modalities.add(entry["modality"])
        if month:
            months.add(month)

        row = _issue_row(b, issue_no, latest_slug)
        issues_idx.append({
            "issue_no": row["issue_no"],
            "slug": row["slug"],
            "date": row["date"],
            "month": row["month"],
            "title": row["title"],
            "agencies": row["agencies"],
            "count": row["count"],
            "ev": row["ev"],
            "latest": row["latest"],
            "href": f"{_ARCHIVE_REL}briefs/{row['slug']}/index.html",
        })

    issues_idx.sort(key=lambda r: r["date"], reverse=True)
    return {
        "schema": "grm-search-index/v1",
        "facets": {
            "agencies": sorted(agencies),
            "categories": sorted(categories),
            "modalities": sorted(modalities),
            "months": sorted(months, reverse=True),
        },
        "issues": issues_idx,
        "cards": cards_idx,
    }


# ── 렌더 ─────────────────────────────────────────────────────────────────────
def _make_env() -> Environment:
    return Environment(
        loader=FileSystemLoader([str(TEMPLATES_DIR), str(PARTIALS_PARENT)]),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 항상 LF/UTF-8 — OS 무관 결정론(Windows 의 \r\n 변환 차단).
    path.write_bytes(text.encode("utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    """결정론 JSON 쓰기 — dict 삽입순서 보존(sort_keys 미사용), ensure_ascii=False,
    indent=1(레포 data 관례), 항상 LF/UTF-8 + 후행개행. 같은 입력 → byte 동일."""
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def render_site(data_dir: Path = DATA_DIR, out_dir: Path = DIST_DIR,
                assets_dir: Path = ASSETS_DIR) -> dict[str, Any]:
    """data_dir → out_dir 정적 사이트 빌드. 산출 메타(쓴 파일 목록) 반환."""
    env = _make_env()
    briefs = load_briefs(data_dir)
    if not briefs:
        raise SystemExit(f"입력 브리프 없음: {data_dir}")

    issue_no_by_date = assign_issue_numbers(briefs)
    latest_slug = max(b["brief"].get("publish_date", "") for b in briefs)
    latest_brief = next(b for b in briefs if b["brief"].get("publish_date", "") == latest_slug)
    latest_issue_no = issue_no_by_date[latest_slug]

    written: list[str] = []

    # 클린 빌드(이전 산출 제거 — 결정론).
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # assets 복사(byte-verbatim — CSS 디자인 동결본).
    dist_assets = out_dir / "assets"
    dist_assets.mkdir(parents=True, exist_ok=True)
    for af in sorted(assets_dir.glob("*")):
        if af.is_file():
            shutil.copyfile(af, dist_assets / af.name)
            written.append(f"assets/{af.name}")

    # 랜딩.
    landing_html = env.get_template("landing.html").render(
        page_title="GRM · Global Regulatory Monitor",
        rel_root="",
        nav_active="home",
        latest_slug=latest_slug,
        cover=_cover_context(latest_brief, latest_issue_no),
    )
    _write(out_dir / "index.html", landing_html)
    written.append("index.html")

    # 아카이브(최신호 desc 정렬).
    issues = sorted(
        (_issue_row(b, issue_no_by_date[b["brief"].get("publish_date", "")], latest_slug)
         for b in briefs),
        key=lambda r: r["date"], reverse=True,
    )
    archive_html = env.get_template("archive.html").render(
        page_title="주간 브리프 · GRM",
        rel_root="../",
        nav_active="board",
        latest_slug=latest_slug,
        issues=issues,
    )
    _write(out_dir / "archive" / "index.html", archive_html)
    written.append("archive/index.html")

    # 검색 인덱스(P4 — 정적 클라이언트사이드 검색용). assets 옆에 둔다(archive.js 가 fetch).
    search_index = build_search_index(briefs, issue_no_by_date, latest_slug)
    _write_json(dist_assets / "search-index.json", search_index)
    written.append("assets/search-index.json")

    # 브리프 상세(주차별).
    brief_tmpl = env.get_template("brief.html")
    for b in briefs:
        pub = b["brief"].get("publish_date", "")
        issue_no = issue_no_by_date[pub]
        renderable = [c for c in (b.get("cards") or []) if _is_renderable(c)]
        cards_sorted = sorted(renderable,
                              key=lambda c: (c.get("render_order") is None,
                                             c.get("render_order")))
        card_views = [_card_view(c) for c in cards_sorted]
        sections = _build_sections(card_views)
        ctx = _brief_context(b, issue_no)
        html = brief_tmpl.render(
            page_title=f"{ctx['title_dateform']} 브리프 · GRM",
            rel_root="../../",
            nav_active="detail",
            latest_slug=latest_slug,
            brief=ctx,
            sections=sections,
        )
        _write(out_dir / "briefs" / pub / "index.html", html)
        written.append(f"briefs/{pub}/index.html")

    return {"out_dir": str(out_dir), "written": written,
            "briefs": len(briefs), "latest": latest_slug}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GRM 웹 렌더러 (JSON → 정적 사이트)")
    ap.add_argument("--data", type=Path, default=DATA_DIR, help="브리프 JSON 디렉터리")
    ap.add_argument("--out", type=Path, default=DIST_DIR, help="정적 사이트 출력 디렉터리")
    args = ap.parse_args(argv)
    meta = render_site(args.data, args.out)
    print(f"빌드 완료: {meta['briefs']}개 브리프 → {meta['out_dir']}  "
          f"(최신호 {meta['latest']}, {len(meta['written'])}개 파일)")
    for w in meta["written"]:
        print(f"  · {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
