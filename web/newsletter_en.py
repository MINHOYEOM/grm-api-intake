#!/usr/bin/env python3
"""GRM 영문 주간 메일 — 그 주에 새로 들어온 지적 기록 중심 + 영문 브리프 요약 짧게.

한국어 주간 메일(`newsletter.py`)과 **별도 리스트·별도 캠페인**이다. 왜 브리프 티저를
영어로 옮기지 않았나: 2026-10 RUM 실측에서 `/en/` 방문의 대부분이 지적사항(findings)
면으로 들어오고 영문 브리프 면은 소수였다 — 해외 독자가 실제로 보러 오는 것을 메일의
주인공으로 둔다. 브리프 요약은 뒤에 세 줄 이하로만 붙인다.

설계 불변식(한국어 메일과 같은 계약을 그대로 진다)
  1. **무변형** — 메일에는 지적 원문·인용·원문 URL 을 싣지 않는다. 문서 단위 사실
     (기관·문서 종류·업체명·국가·발행일·지적 건수·분류)만 싣고, 링크는 우리 영문 페이지뿐
     (`newsletter.gate_provenance` 를 그대로 통과해야 발송된다).
  2. **결정론** — 수집 창은 오늘이 아니라 **그 호의 발행일**에서 파생한다. 같은 스냅샷 →
     같은 subject·HTML. 네트워크는 `fetch` 모드(스냅샷 만들기)와 Brevo 발송뿐.
  3. **지어내지 않기** — 한글 업체명은 로마자로 옮기지 않는다(없는 회사를 만든다). 한글
     업체명·국가명인 기록은 이름 없이 **건수로만** 센다. 번역 사전에 없는 분류 라벨은 뺀다.
  4. **빈 호 차단** — 새 기록 0건이면 발송을 보류한다(빈 메일을 매주 보내지 않는다).
  5. **SaaS 격리** — 발송은 `newsletter.BrevoSender`(같은 인터페이스) 재사용.

수집 창(발행일 `P` 기준, KST)
  · ingested_at ∈ [P-7일 00:00, P 00:00) — 한 주씩 겹치지도 비지도 않게 자른다. 수집은 매일
    03:17 KST 라 발행일 당일 새벽분은 다음 호로 넘어간다(누락이 아니라 경계).
  · published_date ≥ P-28일 — 백필이 몇 년 전 문서를 오늘 적재해도 "새 기록"으로 세지 않는다
    (watchlist_notify_service 의 이중 시간조건과 같은 이유·같은 21일 여유).
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR))

import newsletter  # noqa: E402  (게이트·발송 어댑터·브리프 로더 재사용)
import render      # noqa: E402  (SITE_BASE_URL·영문 브리프 판정·title 파생)
from grm_i18n import MissingTranslation, Translator  # noqa: E402

KST = _timezone(_timedelta(hours=9))
SNAPSHOT_SCHEMA = "grm-newsletter-en-findings/v1"

INGESTED_WINDOW_DAYS = 7
PUBLISHED_SLACK_DAYS = 21
ROWS_PER_SECTION = 5          # 기관 구역마다 업체 줄 상한(나머지는 "외 N건")
BRIEF_TLDR_MAX = 3

_CJK = re.compile(r"[ㄱ-ㆎ가-힣一-鿿]")

# 구역 = 수집 `source` 값. 순서 = 메일 순서(무게가 큰 문서 종류부터). 여기 없는 source 는
# 버리지 않고 기관 이름 구역으로 맨 뒤에 붙인다(조용한 누락 금지).
SOURCE_SECTIONS: list[tuple[str, str]] = [
    ("FDA Warning Letter", "FDA Warning Letters"),
    ("FDA 483", "FDA Form 483 observations"),
    ("EU GMP NCR (EudraGMDP)", "EU GMP non-compliance reports"),
    ("MHRA GMP NCR", "MHRA GMP non-compliance reports"),
    ("Health Canada Inspection", "Health Canada inspections"),
    ("MFDS", "MFDS (Korea)"),
]
# MFDS 는 업체명이 한글이라 줄 목록 대신 문서 종류별 건수로만 낸다.
MFDS_DOC_TYPES = {
    "admin-action": "administrative action",
    "recall-quality": "quality recall",
    "gmp-inspection": "GMP inspection",
}

DISCLOSURE_FINDINGS_EN = (
    "Categories are assigned automatically and may be imperfect. Each record on our site "
    "links to the regulator's original document — check it before acting.")
BREVO_UNSUBSCRIBE_HTML_EN = (
    '<a href="{{ unsubscribe }}" style="color:#8E8B82">Unsubscribe</a> · '
    "You are receiving this because you subscribed to GRM Weekly (English).")


# ── 수집 창(순수) ─────────────────────────────────────────────────────────────
def window_for(publish_date: str) -> dict[str, str]:
    """발행일 → 스냅샷 창. ingested_* 는 UTC `Z` 타임스탬프, published_from 은 날짜."""
    p = _date.fromisoformat(publish_date)
    start_kst = _datetime(p.year, p.month, p.day, tzinfo=KST) - _timedelta(days=INGESTED_WINDOW_DAYS)
    end_kst = _datetime(p.year, p.month, p.day, tzinfo=KST)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "ingested_from": start_kst.astimezone(_timezone.utc).strftime(fmt),
        "ingested_to": end_kst.astimezone(_timezone.utc).strftime(fmt),
        "published_from": (start_kst.date() - _timedelta(days=PUBLISHED_SLACK_DAYS)).isoformat(),
    }


def _parse_ts(value: Any) -> "_datetime | None":
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = _datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_timezone.utc)


def rows_in_window(rows: list[dict[str, Any]], window: dict[str, str]) -> list[dict[str, Any]]:
    """서버가 같은 조건으로 걸렀더라도 여기서 다시 거른다(스냅샷 파일만으로 검증 가능한 계약)."""
    lo, hi = _parse_ts(window["ingested_from"]), _parse_ts(window["ingested_to"])
    out = []
    for r in rows:
        if str(r.get("scope_status") or "ok") != "ok":
            continue
        ts = _parse_ts(r.get("ingested_at"))
        if ts is None or not (lo <= ts < hi):
            continue
        if str(r.get("published_date") or "") < window["published_from"]:
            continue
        out.append(r)
    return out


# ── 문서 단위 집계(순수) ──────────────────────────────────────────────────────
@dataclass
class DocRow:
    document_id: str
    source: str
    document_type: str
    firm: str            # 영문 표시명. 한글이면 "" (지어내지 않기)
    country: str         # 한글 국가명이면 ""
    published_date: str
    n_findings: int
    categories: list[str] = field(default_factory=list)   # 영문 라벨(사전에 있는 것만)


def _latin_or_empty(text: Any) -> str:
    s = str(text or "").strip()
    return "" if _CJK.search(s) else s


def _category_en(label_ko: str, tr: Translator) -> str:
    if not label_ko:
        return ""
    try:
        return tr.lookup(label_ko)
    except MissingTranslation:
        return ""


def aggregate_documents(rows: list[dict[str, Any]], tr: "Translator | None" = None) -> list[DocRow]:
    """지적(행) → 문서. 분류는 그 문서에서 많이 나온 순(동률은 라벨순) 상위 2개."""
    tr = tr or Translator("en")
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        did = str(r.get("document_id") or r.get("finding_id") or "")
        if did:
            by_doc.setdefault(did, []).append(r)
    docs: list[DocRow] = []
    for did in sorted(by_doc):
        grp = by_doc[did]
        head = grp[0]
        cats: dict[str, int] = {}
        for r in grp:
            lab = _category_en(str(r.get("category_label_ko") or ""), tr)
            if lab:
                cats[lab] = cats.get(lab, 0) + 1
        top = [k for k, _ in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
        docs.append(DocRow(
            document_id=did, source=str(head.get("source") or ""),
            document_type=str(head.get("document_type") or ""),
            firm=_latin_or_empty(head.get("firm_name")),
            country=_latin_or_empty(head.get("site_country")),
            published_date=max(str(r.get("published_date") or "") for r in grp),
            n_findings=len(grp), categories=top))
    return docs


@dataclass
class FirmLine:
    """한 구역 안의 업체 한 줄 — 같은 업체의 문서 여러 건은 한 줄로 묶는다(같은 이름을
    네 번 늘어놓지 않는다)."""
    firm: str
    country: str
    latest_date: str
    docs: int
    n_findings: int
    categories: list[str]


@dataclass
class Section:
    label: str
    total: int                          # 이 구역 문서 수
    rows: list[FirmLine]                # 이름이 있는 상위 줄(상한 적용)
    counts: list[tuple[str, int]]       # MFDS 처럼 건수로만 내는 구역의 (종류, 수)

    @property
    def hidden(self) -> int:
        return self.total - sum(r.docs for r in self.rows) - sum(n for _, n in self.counts)


def _firm_lines(docs: list[DocRow]) -> list[FirmLine]:
    by_firm: dict[str, list[DocRow]] = {}
    for d in docs:
        if d.firm:
            by_firm.setdefault(d.firm, []).append(d)
    lines = []
    for firm, grp in by_firm.items():
        cats: dict[str, int] = {}
        for d in grp:
            for c in d.categories:
                cats[c] = cats.get(c, 0) + 1
        lines.append(FirmLine(
            firm=firm, country=next((d.country for d in grp if d.country), ""),
            latest_date=max(d.published_date for d in grp), docs=len(grp),
            n_findings=sum(d.n_findings for d in grp),
            categories=[k for k, _ in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]))
    # 지적 건수 많은 순 → 최근 발행 순 → 업체명 순(안정 정렬을 거꾸로 쌓는다).
    lines.sort(key=lambda x: x.firm)
    lines.sort(key=lambda x: x.latest_date, reverse=True)
    lines.sort(key=lambda x: x.n_findings, reverse=True)
    return lines


def build_sections(docs: list[DocRow], *, per_section: int = ROWS_PER_SECTION) -> list[Section]:
    known = [s for s, _ in SOURCE_SECTIONS]
    labels = dict(SOURCE_SECTIONS)
    extra = sorted({d.source for d in docs if d.source not in labels})
    sections: list[Section] = []
    for src in known + extra:
        group = [d for d in docs if d.source == src]
        if not group:
            continue
        label = labels.get(src) or src or "Other"
        if src == "MFDS":
            tally: dict[str, int] = {}
            for d in group:
                kind = MFDS_DOC_TYPES.get(d.document_type, "record")
                tally[kind] = tally.get(kind, 0) + 1
            counts = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
            sections.append(Section(label, len(group), [], counts))
            continue
        sections.append(Section(label, len(group), _firm_lines(group)[:per_section], []))
    return sections


# ── 메일 빌더(순수·결정론) ────────────────────────────────────────────────────
_WRAP = newsletter._WRAP


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _datefmt(iso: str) -> str:
    try:
        d = _date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%b} {d.day}, {d.year}"


def subject_for(total_docs: int, publish_date: str) -> str:
    return f"[GRM Weekly] {_plural(total_docs, 'new inspection record')} · {_datefmt(publish_date)}"


def en_brief_view(brief_obj: "dict[str, Any] | None") -> "dict[str, Any] | None":
    """영문 브리프 요약 — 그 호가 영어로 **전부** 나오는 호일 때만(영문 페이지가 실제로 있을 때).
    반쪽 영어 호는 링크할 영문 페이지가 없으므로 메일에서도 뺀다."""
    if not brief_obj or not render.brief_has_english(brief_obj):
        return None
    bm = brief_obj["brief"]
    tldr = render._brief_tldr(bm, "en")[:BRIEF_TLDR_MAX]
    return {"publish_date": bm.get("publish_date", ""), "tldr": tldr} if tldr else None


def build_email(sections: list[Section], *, publish_date: str, site_base_url: str,
                brief: "dict[str, Any] | None" = None,
                unsubscribe_html: str = "") -> dict[str, Any]:
    e = _html.escape
    base = site_base_url.rstrip("/")
    findings_url = f"{base}/en/findings/"
    total = sum(s.total for s in sections)
    subject = subject_for(total, publish_date)
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width,initial-scale=1" />',
        f"<title>{e(subject)}</title></head>",
        f'<body style="margin:0;padding:0;background:#FAF9F5;{_WRAP}">',
        '<div style="max-width:600px;margin:0 auto;padding:32px 24px;color:#3D3D3A;'
        'font-size:16px;line-height:1.6">',
        '<div style="font-size:12px;font-weight:600;letter-spacing:.08em;'
        'text-transform:uppercase;color:#A14B30">Global Regulatory Monitor</div>',
        '<h1 style="font-size:23px;line-height:1.3;color:#141413;margin:10px 0 4px">'
        f'{e(_plural(total, "new inspection record"))} this week</h1>',
        f'<div style="font-size:13px;color:#6C6A64;margin-bottom:22px">{e(_datefmt(publish_date))}</div>',
    ]
    for s in sections:
        parts.append(
            '<div style="border-top:1px solid #E6DFD8;padding:14px 0">'
            '<div style="font-size:13px;font-weight:600;color:#A14B30;margin-bottom:6px">'
            f'{e(s.label)} <span style="color:#6C6A64;font-weight:500">· {s.total}</span></div>')
        for d in s.rows:
            meta = [x for x in (d.country, _datefmt(d.latest_date) if d.latest_date else "") if x]
            if d.docs > 1:
                meta.append(_plural(d.docs, "record"))
            meta.append(_plural(d.n_findings, "finding"))
            # 분류 라벨 자체에 쉼표가 있어("Deviation, CAPA, and investigation") 구분자는 ';'.
            cats = (f'<div style="font-size:13px;color:#6C6A64">{e("; ".join(d.categories))}</div>'
                    if d.categories else "")
            parts.append(
                '<div style="font-size:14.5px;line-height:1.55;color:#141413;margin:8px 0 0">'
                f'<b>{e(d.firm)}</b> <span style="color:#6C6A64;font-size:13px">'
                f'{e(" · ".join(meta))}</span>{cats}</div>')
        if s.counts:
            line = " · ".join(_plural(n, kind) for kind, n in s.counts)
            parts.append(f'<div style="font-size:14px;line-height:1.6;color:#3D3D3A;margin:6px 0 0">{e(line)}</div>')
        if s.hidden > 0:
            parts.append('<div style="font-size:13px;color:#8E8B82;margin:4px 0 0">'
                         f'and {s.hidden} more</div>')
        parts.append("</div>")
    parts.append(
        f'<div style="margin:18px 0 28px"><a href="{e(findings_url)}" '
        'style="display:inline-block;background:#C2603F;color:#FAF9F5;text-decoration:none;'
        'font-weight:600;font-size:15px;padding:13px 24px;border-radius:8px">'
        'Browse the latest findings →</a></div>')
    if brief:
        brief_url = f"{base}/en/briefs/{brief['publish_date']}/"
        parts.append('<div style="border:1px solid #DCD3C7;border-radius:12px;padding:16px 18px;'
                     'margin:0 0 24px;background:#FBF8F4">'
                     '<div style="font-size:13px;font-weight:600;color:#A14B30;margin-bottom:6px">'
                     "This week's regulatory brief</div>")
        for t in brief["tldr"]:
            parts.append(f'<div style="font-size:14px;line-height:1.65;color:#3D3D3A;margin:4px 0 0">· {e(t)}</div>')
        parts.append(f'<a href="{e(brief_url)}" style="display:inline-block;margin-top:10px;color:#A14B30;'
                     'text-decoration:none;font-size:14px;font-weight:600">Read the brief →</a></div>')
    parts.append('<div style="border-top:1px solid #E6DFD8;margin-top:8px;padding-top:18px;'
                 'font-size:12px;line-height:1.7;color:#6C6A64">')
    parts.append(f'<b style="color:#3D3D3A">About this email</b> · {e(DISCLOSURE_FINDINGS_EN)}')
    parts.append(f'<div style="margin-top:6px;color:#8E8B82">{e(newsletter.DISCLOSURE_EN)}</div>')
    if unsubscribe_html:
        parts.append(f'<div style="margin-top:14px;color:#8E8B82">{unsubscribe_html}</div>')
    parts.append("</div></div></body></html>")
    return {"subject": subject, "html": "".join(parts), "total_docs": total,
            "section_count": len(sections), "has_brief": bool(brief)}


# ── 게이트 ────────────────────────────────────────────────────────────────────
def run_gates(snapshot: dict[str, Any], *, publish_date: str, site_base_url: str,
              brief_obj: "dict[str, Any] | None" = None,
              unsubscribe_html: str = "") -> "tuple[newsletter.GateReport, dict[str, Any]]":
    reasons: list[str] = []
    fails: list[str] = []
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        fails.append(f"스냅샷 schema 불일치: {snapshot.get('schema')!r}")
    if snapshot.get("publish_date") != publish_date:
        fails.append(f"스냅샷 발행일 {snapshot.get('publish_date')} ≠ 요청 발행일 {publish_date}")
    window = window_for(publish_date)
    if snapshot.get("window") != window:
        fails.append("스냅샷 수집 창이 발행일 파생 창과 다르다 — 다시 fetch 하라")
    rows = rows_in_window(list(snapshot.get("rows") or []), window)
    docs = aggregate_documents(rows)
    sections = build_sections(docs)
    brief = en_brief_view(brief_obj)
    mail = build_email(sections, publish_date=publish_date, site_base_url=site_base_url,
                       brief=brief, unsubscribe_html=unsubscribe_html)
    reasons.append(f"새 기록: 문서 {mail['total_docs']}건 · 구역 {mail['section_count']}개 · "
                   f"영문 브리프 요약 {'포함' if brief else '없음'}")
    if mail["total_docs"] == 0:
        fails.append("새 기록 0건 — 빈 메일 발송 차단")
    prov = newsletter.gate_provenance(mail, site_base_url)
    reasons.append(f"provenance(우리 페이지만·쿼리 0): {'OK' if not prov else 'FAIL'}")
    fails += prov
    reasons.extend(fails)
    return newsletter.GateReport(ok=not fails, reasons=reasons, label="영문 뉴스레터"), mail


def campaign_name(publish_date: str, issue_no: int) -> str:
    """멱등 키 — 한국어 캠페인명과 겹치지 않는다(같은 호를 두 리스트로 각각 한 번씩)."""
    return f"GRM Weekly EN — {publish_date} (No.{issue_no})"


# ── 스냅샷(네트워크) ─────────────────────────────────────────────────────────
_SELECT = ("document_id,finding_id,source,document_type,agency,firm_name,site_country,"
           "published_date,category_label_ko,ingested_at,scope_status")


def fetch_snapshot(publish_date: str, *, supabase_url: str, service_key: str,
                   session: Any = None, page_size: int = 1000, max_pages: int = 50) -> dict[str, Any]:
    """읽기 전용 GET 으로 창 안의 행을 받아 스냅샷 dict 로. 키·응답 본문은 출력하지 않는다."""
    if session is None:
        import requests
        session = requests.Session()
    window = window_for(publish_date)
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    url = f"{supabase_url.rstrip('/')}/rest/v1/findings"
    rows: list[dict[str, Any]] = []
    for page in range(max_pages):
        params = [
            ("select", _SELECT), ("scope_status", "eq.ok"),
            ("ingested_at", f"gte.{window['ingested_from']}"),
            ("ingested_at", f"lt.{window['ingested_to']}"),
            ("published_date", f"gte.{window['published_from']}"),
            ("order", "finding_id.asc"), ("limit", str(page_size)), ("offset", str(page * page_size)),
        ]
        r = session.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        batch = r.json() or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    else:
        raise RuntimeError("스냅샷 페이지 상한 도달 — 창 안 행이 비정상적으로 많다")
    rows.sort(key=lambda x: str(x.get("finding_id") or ""))
    return {"schema": SNAPSHOT_SCHEMA, "publish_date": publish_date, "window": window, "rows": rows}


# ── CLI ───────────────────────────────────────────────────────────────────────
def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def main(argv: "list[str] | None" = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="GRM 영문 주간 메일 — 새 지적 기록 중심(Brevo).")
    ap.add_argument("--publish-date", required=True)
    ap.add_argument("--mode", choices=["fetch", "validate", "precheck", "test", "send"], default="validate")
    ap.add_argument("--snapshot", type=Path, required=True, help="fetch=쓸 경로 · 그 외=읽을 경로")
    ap.add_argument("--data", type=Path, default=newsletter.DATA_DIR)
    ap.add_argument("--out", type=Path, default=None, help="렌더된 메일 HTML 저장(검토용)")
    args = ap.parse_args(argv)

    if args.mode == "fetch":
        url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            print("⚠️  SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY 미설정 — 스냅샷 불가", file=sys.stderr)
            return 2
        try:
            snap = fetch_snapshot(args.publish_date, supabase_url=url, service_key=key)
        except Exception as exc:          # 공개 로그 — 예외 타입명만
            print(f"⚠️  스냅샷 조회 실패: {type(exc).__name__}", file=sys.stderr)
            return 2
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"스냅샷: 행 {len(snap['rows'])}개 · 창 {snap['window']}")
        return 0

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    brief_obj, issue_no = newsletter.load_issue(args.data, args.publish_date)
    name = campaign_name(args.publish_date, issue_no)
    site = render.SITE_BASE_URL

    list_ids = newsletter._list_ids(_env("GRM_NEWSLETTER_LIST_ID_EN"))
    if args.mode == "precheck":
        if not list_ids:
            newsletter._emit_should_send(False, "GRM_NEWSLETTER_LIST_ID_EN 미설정 — 영문 리스트 없음(클린 skip)")
            return 0
        api_key = _env("NEWSLETTER_API_KEY")
        if not api_key:
            newsletter._emit_should_send(False, "NEWSLETTER_API_KEY 미설정 — 멱등 조회 불가(클린 skip)")
            return 0
        existing = newsletter.BrevoSender(api_key).find_campaign(name)
        if existing and existing.get("status", "").lower() in newsletter._DISPATCHED_STATUSES:
            newsletter._emit_should_send(False, f"이미 발송/예약(status={existing.get('status')}) — {name}")
        else:
            newsletter._emit_should_send(True, f"발송 필요: {name}")
        return 0

    report, mail = run_gates(snapshot, publish_date=args.publish_date, site_base_url=site,
                             brief_obj=brief_obj)
    print(report.text())
    print(f"제목: {mail['subject']}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(mail["html"].encode("utf-8"))
    if not report.ok:
        return 1
    if args.mode == "validate":
        return 0

    api_key, sender_email = _env("NEWSLETTER_API_KEY"), _env("GRM_NEWSLETTER_SENDER_EMAIL")
    sender_name = _env("GRM_NEWSLETTER_SENDER_NAME_EN") or _env("GRM_NEWSLETTER_SENDER_NAME") or "GRM Weekly"
    if not api_key or not sender_email or not list_ids:
        print("⚠️  NEWSLETTER_API_KEY·GRM_NEWSLETTER_SENDER_EMAIL·GRM_NEWSLETTER_LIST_ID_EN 중 미설정 — 발송 불가",
              file=sys.stderr)
        return 2
    _r, final = run_gates(snapshot, publish_date=args.publish_date, site_base_url=site,
                          brief_obj=brief_obj, unsubscribe_html=BREVO_UNSUBSCRIBE_HTML_EN)
    sender = newsletter.BrevoSender(api_key)
    if args.mode == "test":
        emails = [x.strip() for x in _env("GRM_NEWSLETTER_TEST_EMAILS").replace(";", ",").split(",") if x.strip()]
        if not emails:
            print("⚠️  GRM_NEWSLETTER_TEST_EMAILS 미설정", file=sys.stderr)
            return 2
        cid = sender.create_campaign(name=f"{name} [TEST]", subject=final["subject"], html=final["html"],
                                     list_ids=list_ids, sender_name=sender_name, sender_email=sender_email)
        sender.send_test(cid, emails)
        print(f"테스트 발송 완료(캠페인 {cid}) → {newsletter.mask_emails(emails)}")
        return 0
    existing = sender.find_campaign(name)
    if existing and existing.get("status", "").lower() in newsletter._DISPATCHED_STATUSES:
        print(f"멱등: 이미 발송된 호 — {name}. 재발송 안 함.")
        return 0
    cid = existing["id"] if existing else sender.create_campaign(
        name=name, subject=final["subject"], html=final["html"], list_ids=list_ids,
        sender_name=sender_name, sender_email=sender_email)
    sender.send_campaign(cid)
    print(f"발송 완료: 캠페인 {cid}({name}) → 리스트 {list_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
