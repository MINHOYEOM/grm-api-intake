#!/usr/bin/env python3
"""성장 일보 v2 · 주간 검색 개선 리포트 — Supabase 판독 JSON → 한국어 마크다운 (135).

## 무엇을 하나
- `daily`  : `growth_daily_v2()` 결과 한 덩어리를 일보 한 장으로 — 방문 → 검색 유입 → 구독 신청 →
             확정 구독자 → 메일 클릭을 한 표로 잇고, 아래에 추세·상위 페이지·검색어·메일 링크.
- `search` : `gsc_search_opportunities()` 결과를 주 1회 검색 개선 리포트로 — 클릭률 낮은 1페이지
             노출, 순위 8~20, 정규 주소 중복, 새 자료(FDA 조항·일본·영국) 관련 검색 수요.

## 왜 렌더링을 코드로 하나
판독 규칙(정밀도 표시·급증일 제외·해외 쏠림 경고·메일 클릭 기준)을 사람이나 모델이 매번 다시
해석하면 같은 실수가 돌아온다(추정값과 정확값 비교, globalStats 클릭을 사람 수로 읽기 등).
규칙을 여기 한 곳에 두고 테스트로 고정한다. 보고 채널은 기존 클라우드 Routine 의 실행 기록이다
(`company/growth/daily-report.md`) — 새 메일 채널은 만들지 않는다.

## 숫자는 저장소·로그에 남기지 않는다
저장소가 PUBLIC 이다. 이 스크립트는 결과를 stdout(또는 `--out` 파일)으로만 낸다. Actions 에서
돌리지 않는다(로그 공개). 입력은 Supabase 커넥터 결과를 저장한 JSON 파일이거나 `--live`
(SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY 로 RPC 직접 호출 — 운영자 PC 용)다.

CLI:
  python web/growth_daily.py daily  --json growth.json
  python web/growth_daily.py search --json search.json
  python web/growth_daily.py daily  --live [--date 2026-11-07]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

WEEKDAYS = "월화수목금토일"
SPIKE_RATIO = 5          # 14일 중앙값의 5배 이상이면서
SPIKE_MIN = 100          # 100방문 이상인 날 = 급증일(봇·스크래핑 의심) — 7일 합·전환율에서 뺀다
FOREIGN_SHARE = 0.30     # 한국 외 한 나라가 하루 방문의 30% 이상 → 해외 쏠림 경고(주간 보고 지시문과 같은 규칙)
SEARCH_GROUPS = ("google", "naver", "other_search")


# ── 작은 서식 도우미 ─────────────────────────────────────────────────────────
def n(x: Any) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def signed(x: Any) -> str:
    if x is None:
        return "—"
    v = int(x)
    return f"+{v:,}" if v > 0 else (f"{v:,}" if v < 0 else "±0")


def pct(num: Any, den: Any) -> str:
    try:
        if num is None or not den:
            return "—"
        return f"{100.0 * float(num) / float(den):.1f}%"
    except (TypeError, ValueError):
        return "—"


def precision(si: Any) -> str:
    """sample_interval → 표시. 1=정확 · ≤1.5=거의 정확 · >1.5=약 N배 표본 추정 · None=추정."""
    if si is None:
        return "추정"
    v = float(si)
    if v <= 1.0:
        return "정확"
    if v <= 1.5:
        return "거의 정확"
    return f"추정(표본 약 {v:.0f}배)"


def weekday(date_str: str) -> str:
    try:
        return WEEKDAYS[_dt.date.fromisoformat(date_str).weekday()]
    except (TypeError, ValueError):
        return ""


def table(headers: "list[str]", rows: "list[list[str]]", empty: str = "데이터 없음") -> str:
    if not rows:
        return f"_{empty}_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def unwrap(payload: Any) -> "dict[str, Any]":
    """커넥터 결과 모양(`[{"r": {...}}]`·`[{"growth_daily_v2": {...}}]`)이든 원 객체든 받는다."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    while isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if isinstance(payload, dict) and len(payload) == 1:
        (only,) = payload.values()
        if isinstance(only, (dict, str)) and not {"version", "connected", "growth"} & set(payload):
            return unwrap(only)
    if not isinstance(payload, dict):
        raise ValueError("판독 JSON 이 객체가 아니다")
    return payload


# ── 판독 규칙(순수 함수 — 테스트 대상) ─────────────────────────────────────────
def spike_dates(trend: "list[dict[str, Any]]") -> "set[str]":
    """14일 중 '급증일' — 중앙값의 SPIKE_RATIO 배 이상이고 SPIKE_MIN 이상. 성장으로 읽지 않는다."""
    vals = [int(r.get("visits") or 0) for r in trend]
    if len(vals) < 5:
        return set()
    med = statistics.median(vals)
    return {r["date"] for r in trend
            if int(r.get("visits") or 0) >= max(SPIKE_MIN, SPIKE_RATIO * max(med, 1))}


def week_visits(trend: "list[dict[str, Any]]", end: str, spikes: "set[str]") -> "dict[str, Any]":
    """end 포함 최근 7일 방문 합 — 급증일을 뺀 합과 정밀도 상태를 함께."""
    try:
        d_end = _dt.date.fromisoformat(end)
    except (TypeError, ValueError):
        return {"total": None, "clean": None, "days": 0, "excluded": [], "all_exact": False}
    rows = [r for r in trend
            if 0 <= (d_end - _dt.date.fromisoformat(r["date"])).days < 7]
    excluded = sorted(r["date"] for r in rows if r["date"] in spikes)
    total = sum(int(r.get("visits") or 0) for r in rows)
    clean = sum(int(r.get("visits") or 0) for r in rows if r["date"] not in spikes)
    exact = all(r.get("sample_interval") is not None and float(r["sample_interval"]) <= 1.5 for r in rows)
    return {"total": total, "clean": clean, "days": len(rows), "excluded": excluded, "all_exact": exact}


def foreign_skew(countries_day: "list[dict[str, Any]]", day_visits: Any) -> "dict[str, Any] | None":
    """한국 외 한 나라가 하루 방문의 FOREIGN_SHARE 이상이면 그 나라를 돌려준다."""
    try:
        total = int(day_visits or 0)
    except (TypeError, ValueError):
        return None
    if total < 10:
        return None
    for c in countries_day or []:
        if c.get("country") != "KR" and int(c.get("visits") or 0) >= FOREIGN_SHARE * total:
            return c
    return None


def search_visits(groups: "list[dict[str, Any]]") -> int:
    return sum(int(g.get("visits") or 0) for g in groups or [] if g.get("group") in SEARCH_GROUPS)


def mail_clicks(w: "dict[str, Any] | None") -> "tuple[Any, str]":
    """메일 클릭 — 목록 기준(사람에 가장 가까움) → 링크별 합 → 없음. globalStats 는 쓰지 않는다."""
    if not w:
        return None, "주간 메일 기록 없음"
    if w.get("list_unique_clicks") is not None:
        return w["list_unique_clicks"], "Brevo 목록 기준 고유 클릭"
    if w.get("link_clicks") is not None:
        return w["link_clicks"], "링크별 클릭 합(목록 기준 값은 135 적용 후 다음 수집부터)"
    return None, "클릭 데이터 없음"


# ── 일보 ────────────────────────────────────────────────────────────────────
def render_daily(p: "dict[str, Any]") -> str:
    g = p.get("growth") or {}
    s = p.get("search") or {}
    mail = p.get("mail") or {}
    pages = p.get("pages") or {}
    d = str(p.get("report_date") or g.get("report_date") or "")
    day = g.get("day") or {}
    trend = g.get("trend_14d") or []
    spikes = spike_dates(trend)
    wk = week_visits(trend, d, spikes)
    best = day.get("best_visits")
    si = day.get("best_visits_sample_interval")
    funnel = g.get("funnel") or {}
    fday = funnel.get("day") or {}
    refs = g.get("referrers") or {}
    nl = g.get("newsletter") or {}
    latest = nl.get("latest") or {}
    prev = nl.get("previous") or {}
    w = mail.get("latest_weekly")
    clicks, click_basis = mail_clicks(w)
    skew = foreign_skew((g.get("countries") or {}).get("day"), best)
    dq = g.get("data_quality") or {}

    warn = []
    if d in spikes:
        warn.append(f"⚠ {d} 방문 급증(평소의 {SPIKE_RATIO}배 이상) — 봇·스크래핑 의심, 성장으로 읽지 않음")
    if skew:
        warn.append(f"⚠ 해외 쏠림: {skew.get('label') or skew.get('country')} {n(skew.get('visits'))}/{n(best)}"
                    " — 봇·스캐너 가능성")
    if dq.get("rum_present_on_date") is False:
        warn.append("⚠ 기준일 방문 데이터가 수집되지 않음(방문 0 이 아니다)")

    gday = s.get("day") or {}
    gweek = (s.get("week_compare") or {}).get("this_week") or {}
    out: "list[str]" = []
    out.append(f"# 📈 GRM 성장 일보 — {d}({weekday(d)})\n\n")
    summary = (f"방문 **{n(best)}**({precision(si)}) · 검색 유입 {n(search_visits(refs.get('day')))} · "
               f"구독 신청 {n(fday.get('submits'))} · 구독자 **{n(latest.get('total'))}**"
               f"({signed(nl.get('delta_since_previous'))}, 7일 {signed(nl.get('delta_7d'))}) · "
               f"최근 메일 클릭 {n(clicks)}/{n((w or {}).get('delivered'))}명")
    out.append(("  \n".join(warn) + "  \n" if warn else "") + summary + "\n\n")

    # 1. 이어 보기
    wk_note = ""
    if wk["excluded"]:
        wk_note = f" (급증일 {', '.join(wk['excluded'])} 제외 · 포함 시 {n(wk['total'])})"
    week_visits_clean = wk["clean"]
    out.append("## 1. 방문에서 메일 클릭까지\n\n")
    rows = [
        ["방문", n(best), f"{n(week_visits_clean)}{wk_note}",
         f"Cloudflare RUM(봇 제외·운영자 제외), {precision(si)}"],
        ["검색으로 들어온 방문", n(search_visits(refs.get("day"))), n(search_visits(refs.get("this_week"))),
         "RUM 유입 경로 중 구글·네이버·기타 검색"],
        ["└ 구글 검색 클릭", f"{n(gday.get('clicks'))} (노출 {n(gday.get('impressions'))}, {s.get('report_date') or '—'})",
         f"{n(gweek.get('clicks'))} (노출 {n(gweek.get('impressions'))})",
         "Search Console 확정치 — 2~3일 늦다. 네이버는 읽기 API 가 없어 빠진다"],
        ["구독 신청(사이트 폼)", n(fday.get("submits")), n(funnel.get("this_week_submits")),
         "사이트 신청 버튼 제출 수 — 확인 메일을 눌러야 구독자가 된다"],
        ["확정 구독자", f"{n(latest.get('total'))} ({signed(nl.get('delta_since_previous'))})",
         signed(nl.get("delta_7d")),
         f"Brevo 목록 3 스냅샷 {latest.get('snap_date') or '—'}. 해지(수신거부) {n(latest.get('blacklisted'))}명"],
        ["최근 주간 메일 클릭", f"{n(clicks)} / 수신 {n((w or {}).get('delivered'))}",
         "—", f"{click_basis} · {(w or {}).get('sent_at_kst') or '—'} 발송"],
        ["방문 → 신청 전환", pct(fday.get("submits"), best),
         pct(funnel.get("this_week_submits"), week_visits_clean), "신청 ÷ 방문(7일은 급증일 제외 방문)"],
    ]
    out.append(table(["단계", "어제", "최근 7일", "기준"], rows))
    out.append("\n_구독 증감의 출처(누가 언제)는 Brevo 연락처 History·Transactional Logs 에서 확인한다 — "
               "스냅샷 시각으로 추론하지 않는다._\n")

    # 2. 14일 흐름
    out.append("\n## 2. 방문 14일 흐름\n\n")
    trows = []
    for r in trend:
        mark = " ⚠급증" if r["date"] in spikes else ""
        v = n(r.get("visits"))
        if precision(r.get("sample_interval")).startswith("추정"):
            v = "~" + v
        trows.append([f"{r['date'][5:]}({r.get('weekday') or weekday(r['date'])})", v + mark, n(r.get("page_views"))])
    out.append(table(["날짜", "방문", "페이지뷰"], trows))
    wc = g.get("week_compare") or {}
    tw, pw = wc.get("this_week") or {}, wc.get("prev_week") or {}
    out.append(f"\n이번 7일 {n(tw.get('visits'))} vs 직전 7일 {n(pw.get('visits'))}"
               + (f" — 급증일을 빼면 이번 7일 {n(week_visits_clean)}" if wk["excluded"] else "") + ".\n")

    # 3. 어디서 왔나 · 상위 페이지
    out.append("\n## 3. 어디서 왔나 · 많이 본 페이지\n\n")
    out.append(table(["유입 묶음", "어제", "최근 7일"],
                     _merge_groups(refs.get("day") or [], refs.get("this_week") or [])))
    out.append("\n")
    prow = [[f"`{x.get('path')}`", x.get("zone") or "", n(x.get("visits"))] for x in (pages.get("day") or [])[:5]]
    out.append("어제 상위 페이지\n\n" + table(["경로", "구역", "방문"], prow))
    prow = [[f"`{x.get('path')}`", x.get("zone") or "", n(x.get("visits"))] for x in (pages.get("week") or [])[:5]]
    out.append("\n최근 7일 상위 페이지\n\n" + table(["경로", "구역", "방문"], prow))
    out.append("\n_경로 표는 차원이 커서 방문 수보다 거칠다 — 대략의 순위로만 읽는다."
               + (" 최근 7일 표와 유입 묶음에는 급증일이 섞여 있다." if wk["excluded"] else "") + "_\n")

    # 4. 검색어
    out.append(f"\n## 4. 구글 검색어 (기준일 {s.get('report_date') or '—'})\n\n")
    if not s.get("connected", False):
        out.append("구글 검색어 연동 대기.\n")
    else:
        qrows = [[x.get("query"), n(x.get("impressions")), n(x.get("clicks")), n(x.get("avg_position"))]
                 for x in (s.get("top_queries") or [])[:5]]
        out.append(table(["검색어", "노출", "클릭", "평균 순위"], qrows, empty="검색어 없음(희소 검색어는 구글이 익명화로 뺀다)"))
        rising = (s.get("rising_queries") or [])[:3]
        if rising:
            out.append("\n떠오르는 검색어: " + ", ".join(str(x.get("query")) for x in rising) + "\n")

    # 5. 메일
    out.append("\n## 5. 최근 메일\n\n")
    if w:
        lrows = [[f"`{x.get('url')}`", n(x.get("clicks"))] for x in (w.get("links_top") or []) if x.get("clicks")]
        out.append(f"**{w.get('name')}** — 링크별 클릭 상위\n\n"
                   + table(["링크", "클릭"], lrows[:5], empty="클릭 없음"))
        out.append(f"\n1클릭 피드백: 유용했어요 {n(w.get('fb_up'))} · 아쉬웠어요 {n(w.get('fb_down'))}."
                   " _메일 링크가 브리프 페이지·구역 단위라 카드별 클릭은 아직 셀 수 없다._\n")
    rrows = []
    for r in (mail.get("recent") or [])[:4]:
        c, _ = mail_clicks(r)
        rrows.append([r.get("name"), r.get("sent_at_kst") or "", n(r.get("delivered")), n(c), n(r.get("unsubscriptions"))])
    out.append("\n" + table(["캠페인", "발송", "수신", "클릭", "해지"], rrows))
    out.append(f"\n최근 14일 메일 해지 {n(mail.get('unsubscriptions_14d'))}건.\n")

    # 6. 데이터 상태
    out.append("\n## 6. 데이터 기준\n\n")
    out.append(f"- 방문: RUM {dq.get('rum_latest_date') or '—'}까지 · UTC 날짜(KST 새벽 0~9시는 전날로 잡힌다) · "
               "Cloudflare 존(zone) 수치는 봇이 섞여 쓰지 않는다. 가장 최근 날은 잠정치.\n")
    sq = s.get("data_quality") or {}
    out.append(f"- 구글 검색: Search Console {sq.get('latest_date') or '—'}까지(지연 {n(sq.get('lag_days'))}일).\n")
    out.append(f"- 구독자: Brevo 스냅샷 {latest.get('snap_date') or '—'} · 메일 통계 수집 {mail.get('latest_capture_kst') or '—'} KST.\n")
    out.append("- 메일 클릭: Brevo 의 전체 집계(globalStats)는 수신자 수보다 큰 값이 와서 사람 수로 쓰지 않는다. "
               "열람 수는 Apple 메일 개인정보 보호가 부풀려 쓰지 않는다.\n")
    return "".join(out)


def _merge_groups(day: "list[dict[str, Any]]", week: "list[dict[str, Any]]") -> "list[list[str]]":
    label = {"direct": "직접(주소창·북마크)", "google": "구글", "naver": "네이버", "other_search": "기타 검색",
             "ai": "AI 검색", "newsletter": "뉴스레터", "teams": "Teams", "linkedin": "링크드인", "other": "기타"}
    dmap = {x.get("group"): x.get("visits") for x in day}
    rows = []
    for x in week:
        grp = x.get("group")
        rows.append([label.get(grp, grp), n(dmap.get(grp, 0)), n(x.get("visits"))])
    return rows


# ── 주간 검색 개선 리포트 ─────────────────────────────────────────────────────
def render_search(p: "dict[str, Any]") -> str:
    if not p.get("connected"):
        return "# 🔎 GRM 검색 개선 주간 리포트\n\n구글 검색어 연동 대기.\n"
    win = p.get("window") or {}
    t = p.get("totals") or {}
    out = [f"# 🔎 GRM 검색 개선 주간 리포트 — {win.get('start')} ~ {win.get('end')} ({win.get('days')}일)\n\n"]
    imp, prev_imp = t.get("impressions") or 0, t.get("prev_impressions") or 0
    line = (f"구글 노출 **{n(imp)}** · 클릭 **{n(t.get('clicks'))}** · 클릭률 {pct(t.get('clicks'), imp)} · "
            f"평균 순위 {n(t.get('pos'))} — 직전 {win.get('days')}일 노출 {n(prev_imp)} · 클릭 {n(t.get('prev_clicks'))}")
    if prev_imp and imp < 0.5 * prev_imp:
        line = (f"⚠ 구글 노출이 직전 기간의 {100.0 * imp / prev_imp:.1f}% 로 줄었다 — "
                "검색 개선보다 원인 확인(색인·수동 조치·사이트 변경)이 먼저다.  \n") + line
    out.append(line + "\n")

    out.append("\n## 1. 1페이지에 보이는데 안 눌리는 페이지 (평균 순위 ≤10, 클릭률 <5%)\n\n")
    out.append("_할 일: 제목·설명(검색결과에 보이는 문장)을 검색한 사람의 말로 고친다._\n\n")
    out.append(table(["경로", "구역", "노출", "클릭", "평균 순위"],
                     [[f"`{x['path']}`", x.get("zone") or "", n(x.get("impressions")), n(x.get("clicks")), n(x.get("pos"))]
                      for x in (p.get("low_ctr_pages") or [])[:10]]))

    out.append("\n## 2. 조금만 올리면 1페이지 상단 (평균 순위 8~20)\n\n")
    out.append("_할 일: 본문 보강·관련 페이지에서 내부 링크._\n\n")
    out.append(table(["경로", "구역", "노출", "클릭", "평균 순위"],
                     [[f"`{x['path']}`", x.get("zone") or "", n(x.get("impressions")), n(x.get("clicks")), n(x.get("pos"))]
                      for x in (p.get("striking_pages") or [])[:10]]))
    sq = p.get("striking_queries") or []
    if sq:
        out.append("\n같은 기준의 검색어\n\n" + table(["검색어", "노출", "클릭", "평균 순위"],
                   [[x.get("query"), n(x.get("impressions")), n(x.get("clicks")), n(x.get("pos"))] for x in sq[:10]]))

    dup = p.get("duplicate_paths") or []
    if dup:
        out.append("\n## 3. 같은 페이지가 다른 주소로 노출\n\n")
        out.append("_`index.html`·쿼리 꼬리가 붙은 주소가 검색결과에 나온다 — 노출이 둘로 갈린다. "
                   "정규 주소(canonical)·내부 링크 점검 대상._\n\n")
        out.append(table(["경로", "노출", "클릭", "평균 순위"],
                         [[f"`{x['path']}`", n(x.get("impressions")), n(x.get("clicks")), n(x.get("pos"))] for x in dup]))

    out.append("\n## 4. 새 자료와 맞닿은 검색 수요\n\n")
    trows = []
    for tp in p.get("topics") or []:
        top_q = ", ".join(str(q.get("query")) for q in (tp.get("queries") or [])[:3]) or "—"
        trows.append([tp.get("label"), n(tp.get("query_impressions")), n(tp.get("page_impressions")),
                      n(tp.get("page_clicks")), top_q])
    out.append(table(["주제", "검색어 노출", "관련 페이지 노출", "관련 페이지 클릭", "대표 검색어"], trows))
    out.append("\n_검색어 노출은 구글이 익명화로 뺀 희소 검색어가 빠진 값이라 작게 나온다. 페이지 노출이 수요의 더 나은 근사다._\n")

    out.append(f"\n## 5. 데이터 기준\n\n- {p.get('basis') or ''}\n"
               f"- 검색어 {n(p.get('query_rows'))}개 · 페이지 {n(p.get('page_rows'))}개 · 최소 노출 {n(win.get('min_impressions'))}회 이상만 표에 올린다.\n")
    return "".join(out)


# ── 입력 ────────────────────────────────────────────────────────────────────
def fetch_live(rpc: str, body: "dict[str, Any]") -> Any:
    import requests  # 지연 import — 테스트는 네트워크 0
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import grm_cli
    url = grm_cli.normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY 가 필요하다(--live)")
    r = requests.post(f"{url}/rest/v1/rpc/{rpc}", json=body, timeout=60,
                      headers={"apikey": key, "Authorization": "Bearer " + key,
                               "Content-Type": "application/json"})
    if r.status_code >= 300:
        raise SystemExit(f"{rpc} 호출 실패: HTTP {r.status_code}")
    return r.json()


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # Windows cp949 콘솔에서도 한글·이모지 출력
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="성장 일보 v2 · 주간 검색 개선 리포트 렌더러")
    ap.add_argument("kind", choices=["daily", "search"])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="RPC 결과 JSON 파일(커넥터 결과 모양 그대로도 된다)")
    src.add_argument("--live", action="store_true", help="Supabase RPC 직접 호출(운영자 PC)")
    ap.add_argument("--date", default=None, help="daily 기준일 YYYY-MM-DD(기본: 어제 KST)")
    ap.add_argument("--days", type=int, default=28, help="search 창 일수(기본 28)")
    ap.add_argument("--out", default=None, help="결과 파일(기본 stdout)")
    args = ap.parse_args(argv)

    if args.json:
        raw: Any = json.loads(Path(args.json).read_text(encoding="utf-8-sig"))
    elif args.kind == "daily":
        raw = fetch_live("growth_daily_v2", {"p_date": args.date})
    else:
        raw = fetch_live("gsc_search_opportunities", {"p_days": args.days})
    payload = unwrap(raw)
    text = render_daily(payload) if args.kind == "daily" else render_search(payload)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{args.kind} 리포트 저장: {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
