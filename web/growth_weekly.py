#!/usr/bin/env python3
"""GRM 주간 성장 리포트 — 마케팅 계획 M-04(2026-09-23) — growth_weekly_report(089) JSON →
한국어 HTML → Brevo 트랜잭션 메일(운영자 1인 전용).

매주 월요일 09:23 KST, 지난주(월~일, KST) 성과 한 페이지를 운영자에게 이메일로 보낸다:
구독자·방문·구독 신청(채널/구역/경로별)·검색(GSC)·회원·지난주 뉴스레터 발송 여부.

## 왜 이슈가 아니라 메일인가
이 저장소는 PUBLIC 이고 GitHub Actions 로그도 공개다. `growth_daily_report`(077)·
`funnel_touch_report`(087) 류의 숫자를 이슈 본문에 적으면 방문·구독자·전환 수치가 영구
공개 기록이 된다. `watchlist_notify_service.py`(개인 알림)·`newsletter.py`(구독자 캠페인)
와 같은 Brevo 트랜잭션 어댑터 패턴을 그대로 써서 운영자 사서함으로만 보낸다.

## 설계 불변식
  1. **숫자는 로그에 안 찍는다** — stdout/stderr 는 구조만 말한다("섹션 8개 생성 · 수신
     1명(ye***@g***.com)"). 이메일 주소는 `newsletter.mask_emails` 로만 노출.
  2. **순수 빌더 / 네트워크 분리** — `build_report_html`·`build_subject`·`pick_week_brief`
     는 순수(입력→출력, `now()` 0). RPC 호출(`fetch_payload`)·Brevo 발송(`send_email`)만
     네트워크를 쓰고, `requests` 는 그 안에서 지연 import(코어 순수성 보존 — newsletter.py
     와 같은 관례).
  3. **089 가 이미 주간 차분을 낸다** — 087(채널)·084(경로)는 누적 카운터라 이 모듈은
     차분을 직접 계산하지 않는다(SQL 이 `growth_weekly_reports` 스냅샷으로 이미 냈다).
     첫 주는 `touch_week`/`paths_week` 가 null — 0 이 아니라 "아직 직전 주가 없다".
  4. **뉴스레터 발송 여부는 Brevo 가 진실** — `newsletter._DISPATCHED_STATUSES`(같은 계약,
     N-01 freshness 감시와 동일 판정)로만 "발송됨"을 말한다. dispatch_log 는 안 본다.
  5. **정밀도는 화면이 스스로 말한다** — `precision_label`(admin.js `rumPrecision`/구역
     판정과 같은 임계값: ≤1.5=정확, 그 외=표본 배수, null/unknown=미상). 표본값을 전수인
     척 보여주지 않는다.

`web/` 모듈 관례대로 형제 모듈을 이름으로 import(`import render`, `import newsletter`) —
루트 모듈은 import 하지 않는다.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys
from datetime import datetime as _datetime, timezone as _timezone, timedelta as _timedelta
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data" / "briefs"
KST = _timezone(_timedelta(hours=9))

import render  # noqa: E402 — issue 번호/발행일 단일 파생원(뉴스레터와 동일 관례)
import newsletter  # noqa: E402 — idempotency_campaign_name·find_campaign·mask_emails 재사용


# ── 라벨 사전(★09-23 마케팅 계획 M-04 — 미등재 값은 원문 그대로 표시, 새 구역/채널이
#    생겨도 조용히 사라지지 않는다) ─────────────────────────────────────────────
ZONE_LABELS: dict[str, str] = {
    "glossary": "용어사전", "findings": "지적사항", "briefs": "주간 브리프", "home": "홈",
    "library": "자료실", "guide": "이용안내", "quiz": "퀴즈", "archive": "아카이브",
    "about": "소개", "other": "기타",
}

CHANNEL_LABELS: dict[str, str] = {
    "utm:linkedin": "링크드인(UTM)", "utm:newsletter": "뉴스레터(UTM)", "direct": "직접",
    "google": "구글", "naver": "네이버", "ai": "AI 검색", "newsletter": "뉴스레터",
    "linkedin": "링크드인(리퍼러)", "other_search": "기타 검색", "teams": "팀즈",
    "internal": "사이트 내부", "other": "기타",
}

# 8개 섹션 제목(순서 고정) — 발신 요약("섹션 N개 생성")과 heading 텍스트의 단일 원천.
SECTION_HEADINGS: list[str] = [
    "요약",
    "일별 방문",
    "채널별 구독 신청 (이번 주)",
    "구역별 전환 (2026-09-04 이후 누적)",
    "어느 페이지에서 신청했나",
    "검색(구글)",
    "회원",
    "데이터 상태",
]


# ── 순수 포맷 헬퍼 ────────────────────────────────────────────────────────────
def _fmt_int(n: Any) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_delta(n: Any) -> str:
    """+3 / −1(U+2212 마이너스 — 하이픈이 아니다) 형태의 증감 표기."""
    try:
        v = int(round(float(n)))
    except (TypeError, ValueError):
        v = 0
    if v < 0:
        return f"−{abs(v):,}"
    return f"+{v:,}"


def _fmt_pct(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_pos(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.1f}위"
    except (TypeError, ValueError):
        return "—"


def precision_label(sample_interval: Any, precision_unknown: bool = False) -> str:
    """정밀도 어휘(admin.js `rumPrecision`/구역 판정과 같은 임계값). ≤1.5=정확·null=미상."""
    if precision_unknown or sample_interval is None:
        return "미상"
    try:
        v = float(sample_interval)
    except (TypeError, ValueError):
        return "미상"
    if v <= 1.5:
        return "정확"
    return f"표본 약 {v:.1f}배"


def _zone_label(slug: str) -> str:
    return ZONE_LABELS.get(slug, slug)


def _channel_label(raw: str) -> str:
    return CHANNEL_LABELS.get(raw, raw)


def _first_week_note(total: Any) -> str:
    """087/084 첫 주(직전 스냅샷 없음) 공통 안내 문구. 0 이 아니라 "값 없음"을 말한다."""
    return (f"첫 주라 주간값이 없습니다 — 다음 리포트부터 직전 주와의 차분으로 나옵니다 "
            f"(누적 {_fmt_int(total)}건)")


def pick_week_brief(publish_dates: list[str], week_start: str, week_end: str) -> str | None:
    """[week_start, week_end] 안에서 가장 최신 발행일(순수·ISO 문자열 사전식=시간순 max)."""
    candidates = [d for d in publish_dates if d and week_start <= d <= week_end]
    return max(candidates) if candidates else None


def build_subject(payload: dict[str, Any]) -> str:
    return f"📈 GRM 주간 성장 리포트 · {payload.get('week_start', '')}~{payload.get('week_end', '')}"


# ── HTML 빌더(newsletter.build_teaser 와 같은 톤 — 인라인 스타일·한글 자간 없음) ──
_WRAP = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
         "Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif")
_TEXT = "#3D3D3A"
_HEAD = "#141413"
_ACCENT = "#A14B30"
_MUTED = "#6C6A64"
_MUTED2 = "#8E8B82"
_BORDER = "#E6DFD8"
_CARD_BORDER = "#DCD3C7"
_CARD_BG = "#FBF8F4"


def _e(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""))


def _section_open(title: str) -> str:
    return (f'<h2 style="font-size:16px;font-weight:600;color:{_HEAD};'
            f'margin:30px 0 12px;padding-top:20px;border-top:1px solid {_BORDER}">{_e(title)}</h2>')


def _tile(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div style="font-size:12.5px;color:{_MUTED};margin-top:4px">{sub}</div>' if sub else ""
    return (
        f'<div style="border:1px solid {_CARD_BORDER};border-radius:10px;padding:12px 14px;'
        f'background:{_CARD_BG}">'
        f'<div style="font-size:12px;color:{_MUTED};margin-bottom:4px">{_e(label)}</div>'
        f'<div style="font-size:19px;font-weight:700;color:{_HEAD}">{value}</div>'
        f'{sub_html}</div>'
    )


def _table(headers: list[str], rows: list[list[str]], *, empty: str = "데이터 없음") -> str:
    if not rows:
        return f'<div style="font-size:13.5px;color:{_MUTED};margin:0 0 8px">{_e(empty)}</div>'
    head = "".join(
        f'<th style="text-align:left;font-size:12px;color:{_MUTED};font-weight:600;'
        f'padding:6px 10px;border-bottom:1px solid {_BORDER}">{_e(h)}</th>' for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td style="font-size:13.5px;color:{_TEXT};padding:6px 10px;'
            f'border-bottom:1px solid {_BORDER}">{c}</td>' for c in row) + "</tr>"
        for row in rows)
    return (f'<table style="width:100%;border-collapse:collapse;margin:0 0 10px">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


def _newsletter_tile_value(status: dict[str, Any]) -> tuple[str, str]:
    verdict = status.get("verdict")
    if verdict == "sent":
        cs = status.get("campaign_status") or ""
        return f"발송됨({_e(cs)})", _e(status.get("publish_date") or "")
    if verdict == "not-sent":
        return "미발송", _e(status.get("reason") or "")
    if verdict == "no-brief":
        return "이번 주 발행 없음", ""
    return "확인 불가", _e(status.get("reason") or "")


def build_report_html(payload: dict[str, Any], newsletter_status: dict[str, Any]) -> str:
    """089 payload + 뉴스레터 발송 판정 → 운영자 전용 주간 리포트 HTML. 순수(now() 0)."""
    week_start = payload.get("week_start", "")
    week_end = payload.get("week_end", "")
    generated_at = payload.get("generated_at_kst", "")

    visits = payload.get("visits") or {}
    v_this = visits.get("this_week") or {}
    v_prev = visits.get("prev_week") or {}
    daily = visits.get("daily") or []

    subs = payload.get("subscribers") or {}
    subs_now = subs.get("now") or {}
    blacklisted = subs_now.get("blacklisted") or 0

    submits = payload.get("submits") or {}
    s_this = submits.get("this_week") or {}
    s_prev = submits.get("prev_week") or {}

    touch = payload.get("touch") or {}
    touch_week = payload.get("touch_week")

    zone = payload.get("zone") or {}
    zones = (zone.get("zones") or [])[:6]

    paths = payload.get("paths") or {}
    paths_week = payload.get("paths_week")

    gsc = payload.get("gsc") or {}
    g_this = gsc.get("this_week") or {}
    g_prev = gsc.get("prev_week") or {}
    g_gloss_this = gsc.get("glossary_this_week") or {}
    g_gloss_prev = gsc.get("glossary_prev_week") or {}
    top_pages = (gsc.get("top_pages") or [])[:5]

    members = payload.get("members") or {}

    dq = payload.get("data_quality") or {}

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width,initial-scale=1" />',
        f"<title>{_e(build_subject(payload))}</title></head>",
        f'<body style="margin:0;padding:0;background:#FAF9F5;{_WRAP}">',
        f'<div style="max-width:640px;margin:0 auto;padding:32px 24px;color:{_TEXT};'
        'font-size:15px;line-height:1.6">',
        f'<div style="font-size:12px;font-weight:600;letter-spacing:.08em;'
        f'text-transform:uppercase;color:{_ACCENT}">GRM Growth Report</div>',
        f'<h1 style="font-size:21px;line-height:1.3;color:{_HEAD};margin:10px 0 4px">'
        f'주간 성장 리포트</h1>',
        f'<div style="font-size:13px;color:{_MUTED};margin-bottom:8px">'
        f'{_e(week_start)} ~ {_e(week_end)}</div>',
    ]

    # ── 1. 요약 ───────────────────────────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[0]))
    visits_precision = precision_label(v_this.get("sample_interval_max"), bool(v_this.get("precision_unknown")))
    subs_sub = f"이번 주 {_fmt_delta(subs.get('new_this_week'))} · 지난주 {_fmt_delta(subs.get('new_prev_week'))}"
    if blacklisted:
        subs_sub += f" · 수신거부 {_fmt_int(blacklisted)}명"
    visits_sub = f"지난주 {_fmt_int(v_prev.get('visits'))} · 정밀도 {visits_precision}"
    submits_sub = (f"밴드 {_fmt_int(s_this.get('band'))} · 배너 {_fmt_int(s_this.get('cta'))} · "
                   f"지난주 {_fmt_int(s_prev.get('submits'))}건")
    nl_value, nl_sub = _newsletter_tile_value(newsletter_status)
    tiles = [
        _tile("구독자", f"{_fmt_int(subs_now.get('total'))}명", subs_sub),
        _tile("방문", _fmt_int(v_this.get("visits")), visits_sub),
        _tile("구독 신청", f"{_fmt_int(s_this.get('submits'))}건", submits_sub),
        _tile("뉴스레터", nl_value, nl_sub),
    ]
    parts.append(
        '<div style="display:table;width:100%;border-spacing:8px 0;margin:0 0 4px">'
        + "".join(f'<div style="display:table-cell;width:25%">{t}</div>' for t in tiles)
        + "</div>")

    # ── 2. 일별 방문 ──────────────────────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[1]))
    daily_rows = [
        [_e(d.get("date")), _e(d.get("weekday")), _fmt_int(d.get("visits")),
         _e(precision_label(d.get("sample_interval")))]
        for d in daily
    ]
    parts.append(_table(["날짜", "요일", "방문", "정밀도"], daily_rows))

    # ── 3. 채널별 구독 신청 (이번 주) ─────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[2]))
    if touch_week is None:
        parts.append(f'<div style="font-size:13.5px;color:{_TEXT}">{_e(_first_week_note(touch.get("total_submits")))}</div>')
    else:
        by_channel = touch_week.get("by_channel") or []
        rows = [[_e(_channel_label(c.get("channel", ""))), _fmt_int(c.get("submits"))] for c in by_channel]
        parts.append(_table(["채널", "신청"], rows))
        parts.append(
            f'<div style="font-size:12.5px;color:{_MUTED}">누적 채널 신청 {_fmt_int(touch.get("total_submits"))}건</div>')

    # ── 4. 구역별 전환 ────────────────────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[3]))
    zone_rows = []
    for z in zones:
        rate = z.get("rate_pct")
        rate_str = "—" if rate is None else _fmt_pct(rate)
        prec = precision_label(z.get("sample_interval_max"), bool(z.get("precision_unknown")))
        zone_rows.append([_e(_zone_label(z.get("zone", ""))), _fmt_int(z.get("visits")),
                          _fmt_int(z.get("submits")), _e(rate_str), _e(prec)])
    parts.append(_table(["구역", "방문", "신청", "전환율", "정밀도"], zone_rows))
    if zone.get("path_precision_note"):
        parts.append(f'<div style="font-size:12px;color:{_MUTED2}">{_e(zone.get("path_precision_note"))}</div>')

    # ── 5. 어느 페이지에서 신청했나 ───────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[4]))
    if paths_week is None:
        parts.append(
            f'<div style="font-size:13.5px;color:{_TEXT};margin-bottom:10px">'
            f'{_e(_first_week_note(paths.get("total_submits")))}</div>')
    else:
        parts.append(
            f'<div style="font-size:12.5px;font-weight:600;color:{_MUTED};margin-bottom:4px">이번 주</div>')
        week_rows = [[_e(r.get("path")), _fmt_int(r.get("submits"))] for r in (paths_week.get("rows") or [])[:5]]
        parts.append(_table(["경로", "신청"], week_rows))
    parts.append(
        f'<div style="font-size:12.5px;font-weight:600;color:{_MUTED};margin-bottom:4px">누적</div>')
    cum_rows = [[_e(r.get("path")), _fmt_int(r.get("submits"))] for r in (paths.get("paths") or [])[:5]]
    parts.append(_table(["경로", "신청"], cum_rows))

    # ── 6. 검색(구글) ─────────────────────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[5]))
    search_rows = [
        ["클릭", _fmt_int(g_this.get("clicks")), _fmt_int(g_prev.get("clicks"))],
        ["노출", _fmt_int(g_this.get("impressions")), _fmt_int(g_prev.get("impressions"))],
        ["CTR", _fmt_pct(g_this.get("ctr_pct")), _fmt_pct(g_prev.get("ctr_pct"))],
        ["평균 순위", _fmt_pos(g_this.get("avg_position")), _fmt_pos(g_prev.get("avg_position"))],
    ]
    parts.append(_table(["지표", "이번 주", "지난주"], search_rows))
    parts.append(
        f'<div style="font-size:12.5px;color:{_MUTED};margin:4px 0 8px">용어사전 CTR — 이번 주 '
        f'{_fmt_pct(g_gloss_this.get("ctr_pct"))} · 지난주 {_fmt_pct(g_gloss_prev.get("ctr_pct"))}</div>')
    page_rows = [
        [_e(p.get("page")), _fmt_int(p.get("clicks")), _fmt_int(p.get("impressions")),
         _fmt_pct(p.get("ctr_pct")), _fmt_pos(p.get("avg_position"))]
        for p in top_pages
    ]
    parts.append(_table(["페이지", "클릭", "노출", "CTR", "평균 순위"], page_rows))
    parts.append(
        f'<div style="font-size:12px;color:{_MUTED2}">확정 데이터는 2~3일 늦습니다 · 최신 '
        f'{_e(gsc.get("latest_date"))} · 지연 {_fmt_int(dq.get("gsc_lag_days"))}일</div>')

    # ── 7. 회원 ───────────────────────────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[6]))
    member_rows = [[
        _fmt_int(members.get("total")), _fmt_int(members.get("new_this_week")),
        _fmt_int(members.get("new_prev_week")), _fmt_int(members.get("signed_in_this_week")),
    ]]
    parts.append(_table(["총", "이번 주 신규", "지난주 신규", "이번 주 로그인"], member_rows))

    # ── 8. 데이터 상태 ────────────────────────────────────────────────────
    parts.append(_section_open(SECTION_HEADINGS[7]))
    parts.append(f'<div style="font-size:12.5px;color:{_MUTED};line-height:1.7">{_e(dq.get("basis"))}</div>')
    snap_rows = [
        ["뉴스레터 스냅샷", _e(dq.get("newsletter_snapshot_date"))],
        ["깔때기 스냅샷", _e(dq.get("funnel_snapshot_end"))],
        ["GSC 최신 데이터", _e(dq.get("gsc_latest_date"))],
        ["직전 주 스냅샷", "있음" if dq.get("prev_week_snapshot_present") else "없음(첫 주)"],
    ]
    parts.append(_table(["항목", "값"], snap_rows))

    parts.append(
        f'<div style="border-top:1px solid {_BORDER};margin-top:20px;padding-top:14px;'
        f'font-size:11.5px;color:{_MUTED2}">생성 {_e(generated_at)} · 원천 growth_weekly_report(089) · '
        '이 메일은 운영자에게만 발송됩니다</div>')

    parts.append("</div></body></html>")
    return "".join(parts)


# ── 네트워크(RPC·Brevo) — requests 는 지연 import(코어 순수성 보존) ─────────────
def fetch_payload(supabase_url: str, service_key: str, week_end: "str | None", persist: bool) -> dict[str, Any]:
    """`growth_weekly_report(p_week_end, p_persist)` RPC 호출. 실패 시 상태코드만 담아 raise."""
    import requests
    body: dict[str, Any] = {"p_persist": persist}
    if week_end:
        body["p_week_end"] = week_end
    url = f"{supabase_url.rstrip('/')}/rest/v1/rpc/growth_weekly_report"
    headers = {
        "apikey": service_key, "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=body, headers=headers, timeout=30)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"growth_weekly_report RPC 실패: status={r.status_code}")
    return r.json()


def newsletter_status_for_week(data_dir: Path, week_start: str, week_end: str,
                               api_key: str) -> dict[str, Any]:
    """그 주(월~일)에 발행된 브리프 + 그 호 뉴스레터 캠페인 상태(Brevo 가 진실).

    반환 verdict ∈ {sent, not-sent, no-brief, unknown}. `no-brief` 는 그 주 발행이 아예
    없던 경우(캠페인 조회 자체가 무의미 — freshness 감시의 brief-missing 과 같은 정신)."""
    briefs = render.load_briefs(data_dir)
    dates = [b.get("brief", {}).get("publish_date", "") for b in briefs]
    pub = pick_week_brief(dates, week_start, week_end)
    if pub is None:
        return {"publish_date": None, "verdict": "no-brief", "campaign_status": None,
                "reason": f"이 주({week_start}~{week_end})에 발행된 브리프가 없습니다"}
    if not api_key:
        return {"publish_date": pub, "verdict": "unknown", "campaign_status": None,
                "reason": "NEWSLETTER_API_KEY 미설정 — 캠페인 상태 조회 불가"}
    try:
        _brief_obj, issue_no = newsletter.load_issue(data_dir, pub)
        campaign_name = newsletter.idempotency_campaign_name(pub, issue_no)
        campaign = newsletter.BrevoSender(api_key).find_campaign(campaign_name)
    except Exception as exc:
        # 이 저장소는 PUBLIC — 응답 본문은 공개 로그에 남을 수 있어 클래스명만(newsletter.py 와 동일 규율).
        return {"publish_date": pub, "verdict": "unknown", "campaign_status": None,
                "reason": f"Brevo 조회 실패: {type(exc).__name__}"}
    status = str((campaign or {}).get("status", "")).lower()
    if campaign and status in newsletter._DISPATCHED_STATUSES:
        return {"publish_date": pub, "verdict": "sent", "campaign_status": campaign.get("status"),
                "reason": f"캠페인 {campaign.get('id')} 상태={campaign.get('status')}"}
    return {
        "publish_date": pub, "verdict": "not-sent",
        "campaign_status": campaign.get("status") if campaign else None,
        "reason": ("캠페인 없음(미발송)" if not campaign
                  else f"캠페인 {campaign.get('id')} 상태={campaign.get('status')}(미발송/미예약)"),
    }


def resolve_recipients() -> list[str]:
    """수신자 결정 — `GRM_GROWTH_REPORT_TO` 우선, 없으면 `GRM_NEWSLETTER_TEST_EMAILS` 의
    첫 주소(테스트 발송 목록을 재사용하되 운영자 리포트는 1인에게만). 둘 다 없으면 빈 리스트."""
    raw = _env("GRM_GROWTH_REPORT_TO")
    if raw:
        return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    raw2 = _env("GRM_NEWSLETTER_TEST_EMAILS")
    if raw2:
        first = [x.strip() for x in raw2.replace(";", ",").split(",") if x.strip()]
        return first[:1]
    return []


def send_email(api_key: str, sender_name: str, sender_email: str, to: list[str],
               subject: str, html: str) -> None:
    """Brevo 트랜잭션 이메일(`v3/smtp/email`) — watchlist_notify_service.WatchlistNotifySender
    와 같은 어댑터 형태(리스트 불필요·수신자 지정 발송)."""
    import requests
    headers = {"api-key": api_key, "accept": "application/json", "content-type": "application/json"}
    body = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": addr} for addr in to],
        "subject": subject,
        "htmlContent": html,
    }
    r = requests.post("https://api.brevo.com/v3/smtp/email", headers=headers, json=body, timeout=20)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"Brevo 발송 실패: status={r.status_code}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def main(argv: "list[str] | None" = None) -> int:
    for _stream in (sys.stdout, sys.stderr):          # Windows cp949 콘솔서도 한글 출력(newsletter.py 와 동일 관용구)
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="GRM 주간 성장 리포트 — growth_weekly_report(089) → HTML → Brevo 트랜잭션 메일(운영자 전용, M-04).")
    ap.add_argument("--mode", choices=["send", "dry-run"], default="dry-run",
                    help="send=실발송(p_persist=true) · dry-run=발송 0(p_persist=false)")
    ap.add_argument("--week-end", default=None, help="지난주 일요일(YYYY-MM-DD). 비우면 RPC 기본값(직전 일요일)")
    ap.add_argument("--payload", type=Path, default=None, help="RPC 대신 저장된 payload JSON 사용")
    ap.add_argument("--out", type=Path, default=None, help="렌더된 리포트 HTML 저장")
    ap.add_argument("--data", type=Path, default=DATA_DIR, help="브리프 JSON 디렉터리")
    args = ap.parse_args(argv)

    if args.payload is not None:
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
    else:
        supabase_url = _env("SUPABASE_URL")
        service_key = _env("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_url or not service_key:
            print("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 미설정 — RPC 호출 불가", file=sys.stderr)
            return 2
        try:
            payload = fetch_payload(supabase_url, service_key, args.week_end, args.mode == "send")
        except Exception as exc:
            print(f"growth_weekly_report RPC 실패: {type(exc).__name__}", file=sys.stderr)
            return 2

    week_start = payload.get("week_start", "")
    week_end = payload.get("week_end", "")
    api_key = _env("NEWSLETTER_API_KEY")
    status = newsletter_status_for_week(args.data, week_start, week_end, api_key)

    html = build_report_html(payload, status)
    subject = build_subject(payload)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html, encoding="utf-8")
        print(f"리포트 HTML 저장(검토용): {args.out}")
    print(f"제목: {subject}")

    if args.mode == "dry-run":
        print(f"섹션 {len(SECTION_HEADINGS)}개 생성 · dry-run — 발송 안 함.")
        return 0

    recipients = resolve_recipients()
    if not recipients:
        print("수신자 없음(GRM_GROWTH_REPORT_TO/GRM_NEWSLETTER_TEST_EMAILS 미설정) — 발송 보류", file=sys.stderr)
        return 3

    sender_name = _env("GRM_NEWSLETTER_SENDER_NAME", "Global Regulatory Monitor")
    sender_email = _env("GRM_NEWSLETTER_SENDER_EMAIL")
    if not api_key or not sender_email:
        print("NEWSLETTER_API_KEY/GRM_NEWSLETTER_SENDER_EMAIL 미설정 — 발송 불가", file=sys.stderr)
        return 2
    try:
        send_email(api_key, sender_name, sender_email, recipients, subject, html)
    except Exception as exc:
        print(f"Brevo 발송 실패: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"섹션 {len(SECTION_HEADINGS)}개 생성 · 수신 {newsletter.mask_emails(recipients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
