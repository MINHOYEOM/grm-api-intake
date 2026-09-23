#!/usr/bin/env python3
"""GRM 뉴스레터 발송 (T1.3) — 티저 메일 빌더 + 발송 게이트 + SaaS-무관 어댑터(Brevo).

수집(`grm-intake.yml`)·웹배포(`grm-web-deploy.yml`)와 **완전 별도**(D8). 발행된 주차
web-card JSON(`web/data/briefs/brief_web_{date}.json`) 1건을 입력으로 짧은 **티저 메일**
(tldr + "이번 호 전체 보기" + 섹션 앵커 링크 + 면책 캐논)을 만들고, 관리형 SaaS(Brevo)로
캠페인을 생성·발송한다. 풍부한 카드는 웹 브리프에서 본다(이메일 전용 풀카드 X).

설계 불변식
  1. **무변형** — 메일은 `tldr`(verbatim)·섹션명·**우리 사이트 링크**만 담는다. 카드 사실·
     원문 인용·카드 출처 URL(provenance 보호 대상)은 메일에 **들어가지 않는다** — 딥링크는
     `SITE_BASE_URL` 의 우리 페이지와 `#sec-{그룹}` 앵커뿐. 클릭 추적은 SaaS 가 발송 시점에
     자기 도메인으로 링크를 래핑 → 우리 산출 URL·JSON 불변.
  2. **결정론** — 같은 입력 → 같은 subject·HTML(`now()`/난수 0). 본문 빌더는 순수.
  3. **발송 게이트 3겹**(워크플로 `grm-newsletter-send.yml`): ① 발행검증(구조·provenance,
     `run_gates`) ② 링크체크(`web/linkcheck.py` broken→발송 보류) ③ 멱등(`publish_date` 파생
     캠페인명 키 — 이미 발송된 호 재발송 0). 스케줄 발송은 무승인 자동(2026-07-05 d92b301 —
     Admin 콘솔 dispatch_log 중복 차단이 운영 경계).
  4. **SaaS 격리** — 발송 API 는 `NewsletterSender` 인터페이스 뒤. `BrevoSender`(Campaigns
     API) 교체 가능(MailerLite·Mailchimp 등은 같은 인터페이스 구현만 추가).
  5. **정적·$0 보존** — 본 모듈은 발송 워크플로(별도 파일·스케줄 자동+수동)에서만 호출. 사이트는 정적
     유지, 수집/렌더와 무관. 네트워크는 `BrevoSender`(지연 import requests)·링크체크에만.

순수 코어(본문 빌더·구조/provenance 게이트)는 네트워크 import 를 모듈 최상단에서 하지 않는다
(`requests` 는 `BrevoSender` 안에서 지연 import — `verify_published_brief` 패턴). `linkcheck`
도 게이트 호출 시점 지연 import(테스트는 fake checker 주입으로 네트워크 0).

issue 번호·제목·섹션 그룹·SITE_BASE_URL 은 `render` 와 **같은 파생원** 재사용(드리프트 0).
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
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlsplit

WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data" / "briefs"

# 발송 누락 감시(freshness 모드, N-01 2026-09-23) — quiz_freshness_check.py 와 같은 관용구.
KST = _timezone(_timedelta(hours=9))

# render.py(같은 디렉터리·순수·네트워크 0) — issue 번호/제목/섹션/SITE_BASE_URL 단일 파생원.
import render  # noqa: E402
# utm.py(같은 디렉터리) — 전달 링크(newsletter/forward/brief_{date}) 태그 부착 전용
# (2026-09-23 마케팅 N-03). 주간 메일 본문의 다른 링크는 여전히 이 헬퍼를 쓰지 않는다.
import utm  # noqa: E402

# ── 면책 캐논(brief.html 상단 배지와 **동일 문안** — drift 가드 테스트가 일치 강제) ──
DISCLOSURE_KO = ("요약·번역·시사점·점검·심층분석은 생성형 AI가 작성하였으며, "
                 "수치·원문 인용·링크·기계 추출 표는 원문을 그대로 제공합니다. "
                 "본 내용은 참고자료이며, 의사결정 전 공식 원문을 확인하십시오.")
DISCLOSURE_EN = ("This digest is AI-generated from primary sources; interpretations are not "
                 "official or legal advice — verify against the original before acting.")


# ── 섹션 그룹(렌더 순서 보존 distinct) ────────────────────────────────────────
def section_groups(brief_obj: dict[str, Any]) -> list[str]:
    """렌더 순서 보존 distinct 섹션 그룹(글로벌/국내/Recall …). `render._is_renderable` 동형
    제외(병합 멤버·watch). 상세 페이지 `id="sec-{그룹}"` 와 1:1(앵커 점프 일치)."""
    seen: set[str] = set()
    out: list[str] = []
    cards = sorted((c for c in (brief_obj.get("cards") or []) if render._is_renderable(c)),
                   key=lambda c: (c.get("render_order") is None, c.get("render_order")))
    for c in cards:
        g = c.get("group")
        if g and g not in seen:
            seen.add(g)
            out.append(g)
    return out


def brief_anchor_href(base_url: str, publish_date: str, group: str | None = None) -> str:
    """우리 상세 페이지(+섹션 앵커) 절대 URL. 한글 그룹은 percent-encode(이메일 안전).
    추적 파라미터 0(쿼리스트링 없음) — provenance/무변형 보존."""
    base = base_url.rstrip("/")
    url = f"{base}/briefs/{publish_date}/"
    if group:
        url += f"#sec-{quote(group, safe='')}"
    return url


# ── 자료실 업데이트 블록(그 주에 자료실로 새로 들어온 규제기관 자료) ──────────
# 웹(§자료실 허브·모아보기 스트립)과 **같은 이력 파일**(web/data/library_updates.json)을
# 읽어 같은 사실을 메일에도 싣는다. 다만 두 가지가 웹과 다르다.
#   1. **링크는 우리 페이지만.** gate_provenance 가 메일 내 외부 호스트 링크를 전면 차단한다
#      (설계 불변식 ①). 그래서 문서 제목은 텍스트로 두고 링크는 우리 카탈로그 페이지로 건다
#      — 원문 링크는 그 카탈로그 페이지에 이미 있다.
#   2. **신선도 창.** 이력의 최신 항목이 그 호 발행일 기준 오래됐으면 싣지 않는다. 안 그러면
#      자료실이 몇 주 조용할 때 같은 소식을 매주 반복 발송하게 된다.
LIBRARY_UPDATE_MAX_AGE_DAYS = 7      # 주간 발행 주기 = 창 크기
LIBRARY_UPDATE_ITEM_CAP = 6          # 메일에 싣는 문서 제목 상한(카탈로그 라운드로빈 배분)


def _days_between(earlier: str, later: str) -> int | None:
    """YYYY-MM-DD 두 날짜의 일수 차(later - earlier). 파싱 실패 시 None."""
    try:
        a = _date.fromisoformat(earlier)
        b = _date.fromisoformat(later)
    except ValueError:
        return None
    return (b - a).days


def library_update_for_issue(
    publish_date: str, *, catalogs: list[dict[str, Any]] | None = None,
    updates_file: Path | None = None, cap: int = LIBRARY_UPDATE_ITEM_CAP,
    max_age_days: int = LIBRARY_UPDATE_MAX_AGE_DAYS,
) -> dict[str, Any] | None:
    """그 호에 실을 자료실 변경 뷰(없으면 None). 순수(파일 읽기만·now() 0).

    신선도 판정은 **그 호의 발행일 기준**이다(오늘 기준이 아니다) — 지난 호를 다시 빌드해도
    같은 결과가 나와야 하고(결정론), 재발송이 나중의 변경 소식을 소급해 싣지도 않는다."""
    entries = render.load_library_update_entries(updates_file)
    if not entries:
        return None
    age = _days_between(str(entries[0].get("date") or ""), publish_date)
    if age is None or not (0 <= age <= max_age_days):
        return None
    return render.build_library_update_view(
        entries[0], catalogs if catalogs is not None else render.load_library(), cap=cap)


def render_library_block(update: dict[str, Any], *, site_base_url: str) -> str:
    """자료실 변경 뷰 → 주간 티저에 얹는 "자료실 업데이트" HTML 조각.

    호출부는 update 가 None 이면 빈 문자열을 넘기므로 여기선 내용이 있다고 본다. 문서 제목은
    **텍스트**(외부 원문 링크 금지 — gate_provenance), 링크는 우리 카탈로그·허브 페이지뿐."""
    e = _html.escape
    base = site_base_url.rstrip("/")
    parts = [
        '<div style="border:1px solid #E6DFD8;border-radius:10px;padding:16px 18px;'
        'margin:0 0 26px;background:#FFFFFF">',
        '<div style="font-size:12px;font-weight:600;letter-spacing:.04em;color:#A14B30;'
        'margin-bottom:10px">자료실 업데이트</div>',
        '<div style="font-size:15px;font-weight:600;color:#141413;margin-bottom:10px">'
        f'규제기관·전문기관 자료 {update["change_count"]}건이 새로 들어왔습니다</div>',
    ]
    for s in update["sources"]:
        href = f"{base}/library/{quote(s['slug'])}/"
        counts = " · ".join(f"{label} {n}건" for label, n in
                            (("신규", s["new_count"]), ("변경", s["changed_count"]),
                             ("내림", s["removed_count"])) if n)
        parts.append(
            '<div style="font-size:14px;line-height:1.6;color:#3D3D3A;margin:8px 0 0">'
            f'<a href="{e(href)}" style="color:#A14B30;text-decoration:none;font-weight:600">'
            f'{e(s["short"])}</a> <span style="color:#6C6A64;font-size:13px">{e(counts)}</span></div>')
        for it in s["items"]:
            parts.append(
                '<div style="font-size:13.5px;line-height:1.55;color:#3D3D3A;'
                f'margin:3px 0 0;padding-left:10px">· {e(it["title"])}</div>')
        if s["hidden_count"]:
            parts.append(
                '<div style="font-size:13px;line-height:1.55;color:#8E8B82;'
                f'margin:3px 0 0;padding-left:10px">· 외 {s["hidden_count"]}건</div>')
    parts.append(
        f'<div style="margin:14px 0 0"><a href="{e(base)}/library/" '
        'style="color:#A14B30;text-decoration:none;font-size:14px;font-weight:600">'
        '자료실에서 원문 보기 →</a></div>')
    parts.append("</div>")
    return "".join(parts)


# ── 티저 메일 빌더(순수·결정론·무변형) ────────────────────────────────────────
_WRAP = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
         "Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif")

# ── 포맷 실험(2026-09) — 공지형 제목·항목 블록 ────────────────────────────────
# 실측 근거: 브리프형 No.9(08-18) 오픈 1·클릭 0 vs 공지형 서비스 안내(08-12) 오픈 5·
# 클릭 25. 공지형이 이긴 두 장치를 주간호에 이식한다 —
#   ① 제목: 매주 똑같던 "{N}주차 ({날짜} 발행)" 대신 그 주의 헤드라인(tldr[0] verbatim,
#      60자 초과 시 말줄임). tldr 없는 호는 종전 주차형 폴백(결정론 유지).
#   ② 핵심 항목: 링크 없는 <ul> 불릿 대신 항목마다 "자세히 보기 →" 링크가 달린 블록
#      (announce.build_announcement 항목 골격과 동형·링크는 우리 브리프 페이지뿐).
# 판정: 3개 호(오픈·클릭을 Brevo 캠페인 통계로 비교) 뒤 유지/revert 결정. 되돌릴 땐
# 이 실험 커밋 revert 하나로 충분하다(멱등 캠페인명은 제목과 무관해 불변).
SUBJECT_HEADLINE_MAX = 60


def teaser_subject(brief_meta: dict[str, Any]) -> str:
    """주간호 제목 — 헤드라인형(포맷 실험). 순수·결정론.

    tldr[0] 이 있으면 그 문장(60자 초과 시 앞 59자 + …)을, 없으면 종전 주차형을 쓴다.
    발행일 표기는 제목에서 뺀다 — 모바일 수신함 절단선(~40자) 안에 헤드라인이 들어가야
    하고, 날짜는 본문 첫 줄에 이미 있다."""
    pub = brief_meta.get("publish_date", "")
    tldr = [t for t in (brief_meta.get("tldr") or []) if t]
    if not tldr:
        return f"[GRM 규제뉴스] {render.title_dateform(pub)} ({pub} 발행)"
    head = tldr[0]
    if len(head) > SUBJECT_HEADLINE_MAX:
        head = head[:SUBJECT_HEADLINE_MAX - 1].rstrip() + "…"
    return f"[GRM 규제뉴스] {head}"


def build_teaser(brief_obj: dict[str, Any], *, site_base_url: str, issue_no: int,
                 unsubscribe_html: str = "", updates_html: str = "",
                 library_html: str = "") -> dict[str, Any]:
    """web-card/v1 브리프 1건 → 티저 메일(subject + HTML). 순수.

    담는 것: 제목(=tldr[0] 또는 날짜파생)·발행일·호수·tldr(verbatim)·전체보기 CTA·섹션 앵커
    링크·면책 캐논. 담지 않는 것: 카드 사실/원문 인용/카드 출처 URL(무변형·provenance).
    `unsubscribe_html` = SaaS-특정 수신거부 스니펫(어댑터가 주입; 본문 빌더는 SaaS-무관).
    `library_html` = 그 호에 얹을 "자료실 업데이트" 블록(`render_library_block` 산출).
    `updates_html` = 그 호에 얹을 "서비스 소식" 블록(`announce.render_weekly_block` 산출).
    **둘 다 기본값 빈 문자열 → 출력 바이트 불변**(실을 게 없는 주는 기존 메일과 완전히
    동일). 우리 사이트 변화·자료 유입을 별도 발송 없이 주간호에 태우는 경로다.
    """
    bm = brief_obj["brief"]
    pub = bm.get("publish_date", "")
    dateform = render.title_dateform(pub)
    title = render._brief_title(bm)
    tldr = [t for t in (bm.get("tldr") or []) if t]
    base = site_base_url.rstrip("/")
    brief_url = brief_anchor_href(base, pub)
    subject = teaser_subject(bm)

    e = _html.escape
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width,initial-scale=1" />',
        f"<title>{e(subject)}</title></head>",
        f'<body style="margin:0;padding:0;background:#FAF9F5;{_WRAP}">',
        '<div style="max-width:600px;margin:0 auto;padding:32px 24px;color:#3D3D3A;'
        'font-size:16px;line-height:1.6">',
        '<div style="font-size:12px;font-weight:600;letter-spacing:.08em;'
        'text-transform:uppercase;color:#A14B30">Global Regulatory Monitor</div>',
        f'<h1 style="font-size:23px;line-height:1.3;color:#141413;margin:10px 0 4px">{e(title)}</h1>',
        f'<div style="font-size:13px;color:#6C6A64;margin-bottom:22px">'
        f'{e(dateform)} · 발행 {e(pub)}</div>',
    ]
    # 핵심 항목 블록(포맷 실험 ②) — tldr[0] 은 이미 h1 제목이므로 나머지를 항목 블록으로.
    # 항목 골격은 announce 의 것과 동형(경계선 + 문장 + 링크)이되, 딥링크 대상이 브리프
    # 페이지 하나뿐이므로 링크도 그 페이지다(무변형 계약: tldr verbatim·우리 링크만).
    rest = tldr[1:]
    if rest:
        parts.append('<div style="font-size:13px;font-weight:600;color:#A14B30;'
                     'margin-bottom:4px">이번 주 핵심</div>')
        for t in rest:
            parts.append(
                '<div style="border-top:1px solid #E6DFD8;padding:14px 0">'
                f'<div style="font-size:15px;line-height:1.65;color:#141413;'
                f'margin-bottom:8px">{e(t)}</div>'
                f'<a href="{e(brief_url)}" style="color:#A14B30;text-decoration:none;'
                'font-size:14px;font-weight:600">자세히 보기 →</a></div>')
        parts.append('<div style="margin:0 0 10px"></div>')
    # 1차 CTA — 이번 호 전체(웹 브리프).
    parts.append(
        f'<div style="margin:0 0 24px"><a href="{e(brief_url)}" '
        'style="display:inline-block;background:#C2603F;color:#FAF9F5;text-decoration:none;'
        'font-weight:600;font-size:15px;padding:13px 24px;border-radius:8px">'
        '이번 주 소식 전체 보기 →</a></div>')
    # 섹션 앵커 링크 — "관심 주제 클릭 신호"(우리 페이지 #sec-{그룹}).
    groups = section_groups(brief_obj)
    if groups:
        parts.append('<div style="font-size:13px;font-weight:600;color:#6C6A64;'
                     'margin-bottom:8px">주제별 바로가기</div>')
        parts.append('<div style="margin:0 0 28px">')
        for g in groups:
            href = brief_anchor_href(base, pub, g)
            parts.append(
                f'<a href="{e(href)}" style="display:inline-block;color:#A14B30;'
                'text-decoration:none;font-size:14px;font-weight:500;border:1px solid #DCD3C7;'
                f'border-radius:9999px;padding:7px 15px;margin:0 8px 8px 0">{e(g)} →</a>')
        parts.append("</div>")
    # 자료실 업데이트 → 서비스 소식(둘 다 선택) — 규제 소식 뒤·면책 앞. 주인공은 그 주 규제
    # 소식이므로 뒤에 붙이고, 그 안에서는 콘텐츠(자료 유입)가 제품 공지보다 앞이다.
    if library_html:
        parts.append(library_html)
    # 주간 퀴즈 진입점 — 전 직원에게 매주 닿는 채널은 이 메일뿐인데 여기 링크가 없었다
    # (랜딩 CTA·푸터·펫 위젯은 모두 "이미 사이트에 들어온 사람"만 본다). 정적 1줄·데이터
    # 바인딩 0 이라 문항 수·주차를 여기서 단정하지 않는다(뱅크와 어긋날 여지 0).
    parts.append(
        '<div style="border:1px solid #DCD3C7;border-radius:12px;padding:16px 18px;'
        'margin:0 0 24px;background:#FBF8F4">'
        '<div style="font-size:13px;font-weight:600;color:#A14B30;letter-spacing:.02em;'
        'margin-bottom:6px">이번 주 퀴즈</div>'
        '<div style="font-size:14px;line-height:1.7;color:#3D3D3A">'
        '이번 주 소식과 규제·품질 용어로 만든 짧은 퀴즈예요. 3분이면 충분합니다.</div>'
        f'<a href="{e(base)}/quiz/" style="display:inline-block;margin-top:10px;color:#A14B30;'
        'text-decoration:none;font-size:14px;font-weight:500;border:1px solid #DCD3C7;'
        'border-radius:9999px;padding:7px 15px">퀴즈 풀어보기 →</a></div>')
    # 구독→회원 사다리(성장 3차) — 매주 구독자 전원에게 닿는 채널은 이 메일뿐인데,
    # 회원 간판 기능(관심 업체 주간 알림)의 진입점이 없었다(랜딩 배너는 사이트에 이미
    # 들어온 사람만 본다). 정적 카피·데이터 바인딩 0. 링크는 랜딩 배너 앵커(#watchlist)
    # 하나뿐 — 추적 파라미터 0 정책 준수. 배너는 reactions 게이트라 env-off 배포에선
    # 앵커가 없지만 랜딩 상단으로 자연 폴백(무해). 퀴즈 카드와 동일 골격, 한글에 자간 없음.
    parts.append(
        '<div style="border:1px solid #DCD3C7;border-radius:12px;padding:16px 18px;'
        'margin:0 0 24px;background:#FBF8F4">'
        '<div style="font-size:13px;font-weight:600;color:#A14B30;'
        'margin-bottom:6px">관심 업체 알림</div>'
        '<div style="font-size:14px;line-height:1.7;color:#3D3D3A">'
        '협력사·경쟁사를 관심 업체로 등록해 두면, 새 지적 기록이 공개될 때 메일로 모아 '
        '알려드려요. 무료 회원이면 바로 쓸 수 있습니다.</div>'
        f'<a href="{e(base)}/#watchlist" style="display:inline-block;margin-top:10px;'
        'color:#A14B30;text-decoration:none;font-size:14px;font-weight:500;'
        'border:1px solid #DCD3C7;border-radius:9999px;padding:7px 15px">'
        '관심 업체 등록하기 →</a></div>')
    # 팀 동료에게 전달(성장 3차 N-03, 2026-09-23) — 매주 구독자 전원에게 닿는 채널은 이
    # 메일뿐인데 "전달해 주세요" 요청·전달받은 사람의 구독 경로가 없었다. 구독 링크에만
    # UTM(newsletter/forward/brief_{date})을 붙인다 — gate_provenance 가 우리 호스트의
    # utm_* 만 허용하도록 열어 뒀으므로(무변형 불변식과 충돌 없음), 전달받은 사람이 구독하면
    # 사이트 첫 진입 퍼널(087)이 이 채널로 귀속한다. 퀴즈·워치리스트 카드와 동일 골격,
    # 한글에 자간 없음.
    # share_href 는 e() 로 감싸지 않는다 — `&`(쿼리 구분자)를 `&amp;` 로 바꾸면
    # `gate_provenance`(naive `href="..."` 정규식 파서)와 여기 값 자체가 어긋난다. base_url
    # 은 SITE_BASE_URL(고정)이고 태그 값은 utm._TAG_RE(`^[a-z0-9._-]{1,60}$`)로 이미
    # 검증돼 attribute 를 깨뜨릴 문자(따옴표·꺾쇠)가 애초에 나올 수 없다.
    share_href = utm.with_utm(brief_url, "newsletter", "forward", f"brief_{pub}")
    parts.append(
        '<div style="border:1px solid #DCD3C7;border-radius:12px;padding:16px 18px;'
        'margin:0 0 24px;background:#FBF8F4">'
        '<div style="font-size:13px;font-weight:600;color:#A14B30;'
        'margin-bottom:6px">팀 동료에게 전달</div>'
        '<div style="font-size:14px;line-height:1.7;color:#3D3D3A">'
        '이번 호가 도움이 됐다면 팀 동료에게 전달해 주세요. 전달받은 분은 아래 링크에서 '
        '바로 구독할 수 있습니다.</div>'
        f'<a href="{share_href}" style="display:inline-block;margin-top:10px;'
        'color:#A14B30;text-decoration:none;font-size:14px;font-weight:500;'
        'border:1px solid #DCD3C7;border-radius:9999px;padding:7px 15px">'
        '이번 주 브리프 보고 구독하기 →</a></div>')
    # 1클릭 피드백(마케팅 계획 N-04, 2026-09-23) — "팀 동료에게 전달" 카드 바로 뒤·
    # updates_html 앞. **새 수신 엔드포인트를 만들지 않는다**: 두 선택지는 같은 브리프
    # 페이지의 앵커만 다른 링크(`#fb-up`/`#fb-down`)이고, Brevo 가 캠페인 링크별 클릭 수
    # (linksStats)를 이미 센다 — N-05 수집기(`collect_newsletter_campaigns.py`)가 그 수를
    # 앵커로 갈라 `newsletter_campaign_stats.links` 에 쌓는다. 쿼리 파라미터가 아니라
    # **앵커**를 쓰는 이유: `gate_provenance` 는 쿼리 문자열만 검사하므로(앵커는 서버에
    # 전송되지도 않는다) 발송 게이트가 그대로 통과하고, 개인 식별자·클릭한 사람도 전혀
    # 남지 않는다(집계뿐). 카드가 아니라 박스 없는 가운데 정렬 한 줄 — 퀴즈·워치리스트·
    # 전달 카드처럼 매번 세 개를 늘어놓으면 메일이 길어져서다. 같은 폰트·색상 재사용,
    # 한글에 자간 없음.
    fb_up_href = e(f"{brief_url}#fb-up")
    fb_down_href = e(f"{brief_url}#fb-down")
    parts.append(
        '<div style="text-align:center;margin:0 0 24px;font-size:13px;color:#6C6A64">'
        '이번 호, 유용했나요? '
        f'<a href="{fb_up_href}" style="display:inline-block;margin:0 4px;color:#A14B30;'
        'text-decoration:none;font-weight:600;font-size:13px;border:1px solid #DCD3C7;'
        'border-radius:9999px;padding:5px 12px">👍 유용했어요</a>'
        f'<a href="{fb_down_href}" style="display:inline-block;margin:0 4px;color:#A14B30;'
        'text-decoration:none;font-weight:600;font-size:13px;border:1px solid #DCD3C7;'
        'border-radius:9999px;padding:5px 12px">👎 아쉬웠어요</a></div>')
    if updates_html:
        parts.append(updates_html)
    # 면책 캐논(brief.html 과 동일) + 수신거부(SaaS 주입).
    parts.append('<div style="border-top:1px solid #E6DFD8;margin-top:8px;padding-top:18px;'
                 'font-size:12px;line-height:1.7;color:#6C6A64">')
    parts.append(f'<b style="color:#3D3D3A">AI 자동 생성 안내</b> · {e(DISCLOSURE_KO)}')
    parts.append(f'<div style="margin-top:6px;color:#8E8B82">{e(DISCLOSURE_EN)}</div>')
    if unsubscribe_html:
        parts.append(f'<div style="margin-top:14px;color:#8E8B82">{unsubscribe_html}</div>')
    parts.append("</div>")
    parts.append("</div></body></html>")
    return {"subject": subject, "html": "".join(parts), "brief_url": brief_url,
            "section_count": len(groups)}


# ── 로그 마스킹 ────────────────────────────────────────────────────────────────
# 이 저장소는 PUBLIC 이고 GitHub Actions 로그도 공개다. 테스트 수신 주소는
# `vars.GRM_NEWSLETTER_TEST_EMAILS`(secret 이 아니라 **변수**)라 GitHub 자동 마스킹이
# 걸리지 않는다 — 그대로 찍으면 이메일 주소가 공개 기록으로 남는다.
# watchlist_notify_service.mask_email 과 같은 규칙이되, web/ 모듈은 루트 모듈을 import
# 하지 않는 구조라 여기 따로 둔다(두 곳 다 "가린다"는 계약만 지키면 되고, 표기가 조금
# 달라져도 안전성은 변하지 않는다).
def mask_email(addr: str) -> str:
    """`ab***@d***.com` 형태로 마스킹. `@` 가 없거나 빈 값이면 `***`."""
    text = str(addr or "").strip()
    if "@" not in text:
        return "***"
    local, _, domain = text.partition("@")
    local_mask = (local[:2] if len(local) >= 2 else local) + "***"
    if "." in domain:
        head, _, rest = domain.partition(".")
        tld = rest.rsplit(".", 1)[-1] if rest else ""
        domain_mask = (head[:1] if head else "") + "***" + (f".{tld}" if tld else "")
    else:
        domain_mask = (domain[:1] if domain else "") + "***"
    return f"{local_mask}@{domain_mask}"


def mask_emails(addrs) -> str:
    """주소 목록 → 로그용 마스킹 문자열. 개수도 함께 밝힌다(발송 대상 수는 사실이고
    개인정보가 아니다 — "몇 명에게 갔나"는 운영 판단에 필요하다)."""
    masked = [mask_email(a) for a in addrs if str(a or "").strip()]
    return f"{len(masked)}명({', '.join(masked)})" if masked else "0명"


# ── 멱등 키(발송 기록 = 캠페인명) ─────────────────────────────────────────────
def idempotency_campaign_name(publish_date: str, issue_no: int) -> str:
    """발송 멱등 키 = 캠페인명(결정론). SaaS 에 같은 이름 캠페인이 이미 있으면 재발송 0
    (PL-10 멱등 정신). Actions 재시도·재실행 안전."""
    return f"GRM Weekly Brief — {publish_date} (No.{issue_no})"


# ── 발송 게이트(①발행검증/구조·provenance ②링크체크) ──────────────────────────
@dataclass
class GateReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)   # 통과·실패 사유(사람 읽기)
    # 어느 발송 경로의 게이트인지(로그 식별용). 공지(`announce.py`)가 같은 리포트를 재사용한다.
    label: str = "뉴스레터"

    def text(self) -> str:
        head = (f"[PASS] {self.label} 발송 게이트" if self.ok
                else f"[FAIL] {self.label} 발송 게이트 — 발송 보류")
        return "\n".join([head, *(f"  · {r}" for r in self.reasons)])


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def gate_publishable(brief_obj: dict[str, Any], expected_date: str) -> list[str]:
    """구조 게이트 — 정상 발행본인지(스키마·발행일·카드·면책). 실패 사유 리스트(빈=통과).
    무거운 발행 게이트(Brief Lint·handoff provenance)는 발행 시점(Routine)에 이미 실행 —
    여기선 web-card 산출물의 구조 무결성을 발송 직전 재확인한다."""
    fails: list[str] = []
    if brief_obj.get("schema_version") != "grm-web-card/v1":
        fails.append(f"schema_version 불일치: {brief_obj.get('schema_version')!r} (grm-web-card/v1 기대)")
    bm = brief_obj.get("brief") or {}
    pub = bm.get("publish_date", "")
    if not _DATE_RE.match(pub or ""):
        fails.append(f"publish_date 형식 오류: {pub!r}")
    elif pub != expected_date:
        fails.append(f"publish_date {pub} ≠ 요청 발행일 {expected_date}")
    renderable = [c for c in (brief_obj.get("cards") or []) if render._is_renderable(c)]
    if not renderable:
        fails.append("렌더 가능한 카드 0 — 빈 호 발송 차단")
    if not bm.get("ai_disclosure"):
        fails.append("ai_disclosure=false — 면책 고지 누락 호 발송 차단")
    return fails


# provenance 게이트가 쿼리 문자열을 허용하는 유일한 키 집합(값 형식은 utm._TAG_RE 재사용
# — 정본은 web/utm.py 하나, 여기서 따로 정규식을 복제하지 않는다).
_UTM_ALLOWED_KEYS = {"utm_source", "utm_medium", "utm_campaign"}


def _is_clean_utm_query(query: str) -> bool:
    """쿼리 문자열이 `utm_source`/`utm_medium`/`utm_campaign` 만으로 이뤄지고 각 값이
    `web/utm.py` 태그 문법(`^[a-z0-9._-]{1,60}$`)을 지키는지. 빈 쿼리는 호출부에서 먼저
    걸러지므로 여기선 "파라미터가 있는데 전부 규약을 지키는가"만 본다."""
    params = parse_qsl(query, keep_blank_values=True)
    return bool(params) and all(
        k in _UTM_ALLOWED_KEYS and bool(utm._TAG_RE.match(v)) for k, v in params)


def gate_provenance(teaser: dict[str, Any], site_base_url: str) -> list[str]:
    """provenance/무변형 게이트 — 메일이 우리 페이지만 링크하고, 쿼리 문자열은 **우리 자체
    호스트의 utm_* 태그**(전달 링크)만 허용하는지 확인한다. 카드 출처 URL(보호 대상)은
    애초에 본문에 없음 → 우리 산출 링크의 청결만 확인.
    (SaaS 가 발송 시점에 자기 도메인으로 래핑하는 것은 우리 산출물 밖 — 무변형 보존.)

    종전 규칙은 "추적 파라미터 부착 링크"를 무조건 차단했다 — 독자 개개인을 우리 서버가
    되읽는 트래커를 막기 위해서다. 2026-09-23 마케팅 계획 N-03 은 그 규칙과 충돌하지 않는
    좁은 예외를 연다: **우리 구독 랜딩 링크**(`with_utm(brief_url, "newsletter", "forward",
    f"brief_{date}")`)에 `utm_source`/`utm_medium`/`utm_campaign` 만 붙이는 것은 "누가 이
    링크로 들어왔는지"를 세는 **집계용 채널 귀속**이지 개인 추적이 아니다(PII 0·쿠키 0).
    `web/utm.py` 모듈독스트링대로 **RUM 은 쿼리 문자열을 아예 읽지 않고**, 이 태그를 실제로
    소비하는 소비자는 사이트 최초 진입(first-touch) 퍼널 카운터(087 마이그레이션)뿐이며
    그마저 규약 밖 형식은 전부 `other` 로 접어 오분류를 만들지 않는다. Brevo 캠페인 링크는
    발송 시점에 어차피 자기 도메인으로 다시 래핑하므로 우리 산출 URL 은 여기서도 불변이다.
    **외부 호스트 링크와 utm_* 이외의 파라미터는 종전대로 전면 차단한다.**"""
    fails: list[str] = []
    base_host = (urlsplit(site_base_url).hostname or "").lower()
    hrefs = re.findall(r'href="([^"]*)"', teaser.get("html", ""))
    for h in hrefs:
        sp = urlsplit(h)
        host = (sp.hostname or "").lower()
        if host and host != base_host:
            fails.append(f"외부 호스트 링크(우리 페이지 아님): {h}")
            continue
        if not sp.query:
            continue
        if host != base_host or not _is_clean_utm_query(sp.query):
            fails.append(f"추적/쿼리 파라미터 부착 링크(무변형 위반): {h}")
    return fails


def gate_linkcheck(brief_obj: dict[str, Any], *,
                   checker: Callable[[str], str] | None = None) -> tuple[list[str], dict[str, int]]:
    """링크체크 게이트(발송 게이트로 승격) — 그 호 카드 링크에 broken 이 있으면 발송 보류.
    `web/linkcheck.py` 재사용(in-place enrich 의 사본에 검사). checker 미지정 시 실네트워크
    checker 생성(워크플로), 테스트는 fake checker 주입(네트워크 0). 반환=(실패사유, tally)."""
    import copy
    import linkcheck  # 지연 import(requests) — 순수 코어 import 시 네트워크 0
    own = None
    if checker is None:
        import requests
        own = requests.Session()
        own.headers.update({"User-Agent": linkcheck.USER_AGENT})
        checker = linkcheck.make_checker(own)
    try:
        tally = linkcheck.enrich_brief(copy.deepcopy(brief_obj), checker)
    finally:
        if own is not None:
            own.close()
    fails: list[str] = []
    broken = tally.get(linkcheck.BROKEN, 0)
    if broken:
        fails.append(f"링크체크 broken {broken}건 — 깨진 공식/정보 링크 든 메일 발송 보류")
    return fails, tally


def run_gates(brief_obj: dict[str, Any], *, expected_date: str, site_base_url: str,
              issue_no: int, checker: Callable[[str], str] | None = None,
              run_linkcheck: bool = True,
              updates_html: str = "",
              library_html: str = "") -> tuple[GateReport, dict[str, Any]]:
    """발행검증(구조·provenance) + (선택)링크체크 게이트를 1회 실행하고 티저를 만든다.
    반환=(GateReport, teaser). 멱등(③)·사람승인(④)은 발송 워크플로/어댑터 레이어.

    `updates_html`·`library_html` 이 실린 호는 provenance 게이트가 그 블록의 링크까지 함께
    훑는다(붙임 블록이 외부 호스트·추적 파라미터를 들여오면 주간 발송 자체가 보류된다)."""
    teaser = build_teaser(brief_obj, site_base_url=site_base_url, issue_no=issue_no,
                          updates_html=updates_html, library_html=library_html)
    reasons: list[str] = []
    struct_fails = gate_publishable(brief_obj, expected_date)
    reasons.append(f"구조 검증: {'OK' if not struct_fails else 'FAIL'}")
    prov_fails = gate_provenance(teaser, site_base_url)
    reasons.append(f"provenance(우리 페이지·전달 링크 외 추적 파라미터 0): {'OK' if not prov_fails else 'FAIL'}")
    fails = struct_fails + prov_fails
    if run_linkcheck:
        lc_fails, tally = gate_linkcheck(brief_obj, checker=checker)
        fails += lc_fails
        reasons.append(f"링크체크: broken={tally.get('broken', 0)} degraded={tally.get('degraded', 0)} "
                       f"ok={tally.get('ok', 0)}")
    else:
        reasons.append("링크체크: 건너뜀(--no-linkcheck)")
    reasons.extend(fails)
    return GateReport(ok=not fails, reasons=reasons), teaser


# ── SaaS-무관 발송 인터페이스 + Brevo 어댑터 ───────────────────────────────────
class NewsletterSender:
    """SaaS-무관 발송 인터페이스(교체 가능). 구현은 캠페인 생성/발송/테스트발송/멱등조회만.
    본문·게이트·워크플로는 이 인터페이스에만 의존 — Brevo→타 SaaS 교체 시 구현만 추가."""

    def find_campaign(self, name: str) -> "dict | None":
        """이름 일치 캠페인 {'id','status'} 또는 None. status 로 '이미 발송' vs '미발송 draft'
        구분(create 성공 후 sendNow 실패한 잔여 draft 를 false-skip 하지 않기 위함)."""
        raise NotImplementedError

    def create_campaign(self, *, name: str, subject: str, html: str, list_ids: list[int],
                        sender_name: str, sender_email: str) -> str:
        raise NotImplementedError

    def send_campaign(self, campaign_id: str) -> None:
        raise NotImplementedError

    def send_test(self, campaign_id: str, emails: list[str]) -> None:
        raise NotImplementedError


# Brevo 캠페인은 수신거부 링크를 자동 처리하나, 본문에 태그를 명시해 위치를 고정한다.
BREVO_UNSUBSCRIBE_HTML = (
    '<a href="{{ unsubscribe }}" style="color:#8E8B82">수신거부</a> · '
    '본 메일은 GRM 규제뉴스 구독자에게 발송되었습니다.')

# 멱등(③): 이 status 면 '이미 발송/예약' → 재발송 0. draft 등 그 외는 미발송으로 보고 재사용.
_DISPATCHED_STATUSES = {"sent", "queued", "inprocess", "in_process", "suspended", "archive"}


class BrevoSender(NewsletterSender):
    """Brevo(구 Sendinblue) Campaigns API v3 어댑터. 발송=리스트 대상 classic 캠페인 생성
    후 sendNow(트랜잭션 API 는 보조). `requests` 는 여기서만 지연 import(코어 순수성 보존)."""

    def __init__(self, api_key: str, *, base_url: str = "https://api.brevo.com/v3",
                 session: Any = None, timeout: float = 20.0):
        if not api_key:
            raise ValueError("NEWSLETTER_API_KEY 필요")
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests
            session = requests.Session()
        session.headers.update({"api-key": api_key, "accept": "application/json",
                                "content-type": "application/json"})
        self.s = session

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def find_campaign(self, name: str) -> "dict | None":
        """이름 일치 캠페인 {'id','status'} 또는 None. 페이지네이션 순회(최신 우선)."""
        offset, limit = 0, 100
        for _ in range(20):                              # 최대 2000건 — 운영 규모 충분
            r = self.s.get(self._url("/emailCampaigns"),
                           params={"type": "classic", "limit": limit, "offset": offset,
                                   "sort": "desc"}, timeout=self.timeout)
            r.raise_for_status()
            data = r.json() or {}
            camps = data.get("campaigns") or []
            for c in camps:
                if c.get("name") == name:
                    return {"id": str(c.get("id")), "status": str(c.get("status") or "")}
            if len(camps) < limit:
                break
            offset += limit
        return None

    def create_campaign(self, *, name: str, subject: str, html: str, list_ids: list[int],
                        sender_name: str, sender_email: str) -> str:
        body = {
            "name": name, "subject": subject, "type": "classic",
            "sender": {"name": sender_name, "email": sender_email},
            "htmlContent": html, "recipients": {"listIds": list_ids},
            "inlineImageActivation": False,
        }
        r = self.s.post(self._url("/emailCampaigns"), data=json.dumps(body), timeout=self.timeout)
        r.raise_for_status()
        return str((r.json() or {}).get("id"))

    def send_campaign(self, campaign_id: str) -> None:
        r = self.s.post(self._url(f"/emailCampaigns/{campaign_id}/sendNow"), timeout=self.timeout)
        r.raise_for_status()

    def send_test(self, campaign_id: str, emails: list[str]) -> None:
        r = self.s.post(self._url(f"/emailCampaigns/{campaign_id}/sendTest"),
                        data=json.dumps({"emailTo": emails}), timeout=self.timeout)
        r.raise_for_status()


# ── 로드 헬퍼 ─────────────────────────────────────────────────────────────────
def load_issue(data_dir: Path, publish_date: str) -> tuple[dict[str, Any], int]:
    """data_dir 전체에서 publish_date 호를 찾고, issue 번호(render 와 동일 파생)를 부여."""
    briefs = render.load_briefs(data_dir)
    if not briefs:
        raise SystemExit(f"입력 브리프 없음: {data_dir}")
    issue_no_by_date = render.assign_issue_numbers(briefs)
    match = [b for b in briefs if b["brief"].get("publish_date", "") == publish_date]
    if not match:
        have = ", ".join(sorted(issue_no_by_date)) or "(없음)"
        raise SystemExit(f"발행일 {publish_date} 호 없음. 보유: {have}")
    return match[0], issue_no_by_date[publish_date]


def resolve_latest_publish_date(data_dir: Path) -> str:
    """data_dir 내 발행 브리프 중 가장 최근 publish_date(ISO `YYYY-MM-DD` 문자열의 사전식
    max = 시간순 max). 스케줄 트리거가 '최신 호'를 결정론으로 고르는 진입점(하드코딩 금지)."""
    briefs = render.load_briefs(data_dir)
    dates = sorted(b["brief"].get("publish_date", "") for b in briefs
                   if _DATE_RE.match(b["brief"].get("publish_date", "") or ""))
    if not dates:
        raise SystemExit(f"발행 브리프 없음: {data_dir} — 최신 발행일 결정 불가")
    return dates[-1]


def decide_should_send(sender: "NewsletterSender", publish_date: str,
                       issue_no: int) -> "tuple[bool, str]":
    """멱등(③) 결정 — 이 호를 지금 보내야 하나? `sender` 로 발송 기록(캠페인명)을 조회한다.
    이미 발송/예약된 호면 (False, 사유), 신규·미발송 draft 면 (True, 사유).

    발송 워크플로의 사전점검(precheck)이 이 결과로 send 잡 게이트를 연다 — **보낼 게 없으면
    send 잡(=사람 승인 요청)에 아예 도달하지 않는다**(스케줄 무해성: 새 호 없으면 조용히 skip).
    `main`(mode=send) 의 인라인 멱등과 같은 판정 규칙(`_DISPATCHED_STATUSES`)을 공유한다."""
    name = idempotency_campaign_name(publish_date, issue_no)
    existing = sender.find_campaign(name)
    if existing and existing.get("status", "").lower() in _DISPATCHED_STATUSES:
        return False, (f"이미 발송/예약(status={existing.get('status')}) — 캠페인 "
                       f"{existing['id']}({name}). 재발송 0.")
    if existing:
        return True, f"이전 미발송 draft(status={existing.get('status')}) 재사용 예정 — {name}"
    return True, f"신규 호 — 발송 필요: {name}"


# ── 발송 누락 감시(freshness, N-01 2026-09-23) ────────────────────────────────
# "이번 주 뉴스레터가 실제로 나갔나"를 클라우드에서 판정한다(§CLI --mode freshness,
# `grm-newsletter-freshness.yml`). 스케줄 크론(`grm-newsletter-send.yml`)은 보통
# 스킵한다 — 그 주 브리프 PR 이 크론 시각 이후 사람이 머지하기 때문이라 스케줄이 돌
# 때 아직 "새 호"가 없다. 실제 발송은 월요일 오후 Admin 콘솔 `workflow_dispatch` 다 —
# 그래서 "run success"≠"sent". `newsletter_dispatch_log`(Supabase)는 Admin dispatch
# 기록만 남기고 상태를 갱신하지 않아 "발송됐다"의 증거가 못 된다 — Brevo 캠페인 상태
# (`_DISPATCHED_STATUSES`)만이 진실이다.
def decide_freshness(latest_publish_date: "str | None", as_of: _date,
                     campaign: "dict | None") -> "tuple[str, str]":
    """순수 판정(네트워크 0). verdict ∈ {brief-missing, not-sent, ok}.

    brief-missing — 이번 주 호가 아직 없음(latest 가 없거나, 이번 주 월요일(as_of 파생)
      보다 하루 넘게 오래됨). 브리프는 월요일 발행이 원칙이나 과거 한 호가 일요일
      발행 이력이 있어 1일 허용을 둔다. 캠페인 조회가 필요 없는 판정 — 호출부(CLI)는
      이 경우 Brevo 호출 자체를 건너뛴다.
    not-sent — 이번 주 호는 있으나 캠페인이 없거나(Admin 발송 전) 상태가
      `_DISPATCHED_STATUSES` 밖(예: draft — create 후 sendNow 실패 잔여).
    ok — 이번 주 호가 있고 캠페인이 발송/예약 상태.
    """
    monday = as_of - _timedelta(days=as_of.weekday())
    threshold = monday - _timedelta(days=1)        # 일요일 발행 이력 1일 허용
    if not latest_publish_date or not _DATE_RE.match(latest_publish_date):
        return "brief-missing", f"발행된 브리프가 없습니다(as_of={as_of.isoformat()})"
    if latest_publish_date < threshold.isoformat():
        return "brief-missing", (
            f"최신 브리프 {latest_publish_date} 가 이번 주(월요일 {monday.isoformat()}) "
            f"발행분보다 오래됨 — 이번 주 호 미발행(as_of={as_of.isoformat()})")
    if campaign is None:
        return "not-sent", f"브리프 {latest_publish_date} 는 있으나 Brevo 캠페인이 없습니다(미발송)"
    status = str(campaign.get("status", "")).lower()
    if status not in _DISPATCHED_STATUSES:
        return "not-sent", (
            f"브리프 {latest_publish_date} 캠페인 {campaign.get('id')} 상태={status!r} "
            f"— 발송/예약 상태가 아닙니다")
    return "ok", (
        f"브리프 {latest_publish_date} 캠페인 {campaign.get('id')} 상태={status!r} — 발송 확인")


# ── CLI ───────────────────────────────────────────────────────────────────────
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _emit_should_send(value: bool, reason: str) -> None:
    """precheck(③) 결정을 사람 로그 + GitHub Actions step output(`should_send`)으로 방출.
    `GITHUB_OUTPUT` 미설정(로컬·테스트)이면 stdout 만 — 순수 판정은 `decide_should_send` 담당.
    워크플로는 이 `should_send` 로 send 잡(사람 승인 게이트)에 도달할지 결정한다."""
    val = "true" if value else "false"
    print(f"멱등 사전점검: should_send={val} — {reason}")
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"should_send={val}\n")


def main(argv: "list[str] | None" = None) -> int:
    for _stream in (sys.stdout, sys.stderr):          # Windows cp949 콘솔서도 한글·— 출력
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="GRM 뉴스레터 — 티저 메일 빌드·게이트·발송(Brevo). 수집/배포와 별도(D8).")
    ap.add_argument("--publish-date", default=None,
                    help="발송할 호의 발행일(YYYY-MM-DD). latest-date 모드는 불필요(최신 자동 선택).")
    ap.add_argument("--data", type=Path, default=DATA_DIR, help="브리프 JSON 디렉터리")
    ap.add_argument("--mode", choices=["validate", "test", "send", "latest-date", "precheck", "freshness"],
                    default="validate",
                    help="validate=게이트만(네트워크는 링크체크) · test=테스트발송 · send=실발송 · "
                         "latest-date=최신 발행일만 출력(스케줄 해석) · "
                         "precheck=멱등 사전점검(should_send 방출, 발송 0) · "
                         "freshness=발송 누락 감시(Brevo 캠페인 상태로 판정, 발송 0)")
    ap.add_argument("--out", type=Path, default=None, help="렌더된 메일 HTML 저장(D5 사람 검토 아티팩트)")
    ap.add_argument("--no-linkcheck", action="store_true", help="링크체크 게이트 건너뜀(오프라인 검증)")
    ap.add_argument("--as-of", default="", help="freshness 모드 전용 — KST 기준 검사일(YYYY-MM-DD). 비우면 오늘")
    ap.add_argument("--output", type=Path, default=None, help="freshness 모드 전용 — 판정 리포트 JSON 저장 경로")
    args = ap.parse_args(argv)

    # 스케줄 해석 보조 — 최신 발행일만 결정론으로 출력(게이트·로딩·네트워크 0).
    if args.mode == "latest-date":
        print(resolve_latest_publish_date(args.data))
        return 0

    # 발송 누락 감시(N-01) — publish_date 불필요(최신 호를 스스로 찾는다). 발송 0.
    if args.mode == "freshness":
        as_of = _date.fromisoformat(args.as_of) if args.as_of else _datetime.now(KST).date()
        try:
            latest = resolve_latest_publish_date(args.data)
        except SystemExit:
            latest = None           # 브리프 디렉터리 없음/빈 디렉터리 → brief-missing 판정으로 흡수

        # brief-missing 은 API 없이 판정된다(설계: 이번 주 호가 없으면 캠페인 조회가 무의미) —
        # 여기서 먼저 걸러 불필요한 Brevo 호출을 피한다.
        verdict, reason = decide_freshness(latest, as_of, None)
        campaign_name = None
        campaign_status = None
        if verdict != "brief-missing":
            _brief_obj, issue_no = load_issue(args.data, latest)
            campaign_name = idempotency_campaign_name(latest, issue_no)
            api_key = _env("NEWSLETTER_API_KEY")
            if not api_key:
                verdict, reason = "api-error", "NEWSLETTER_API_KEY 미설정 — 캠페인 상태 조회 불가"
            else:
                try:
                    campaign = BrevoSender(api_key).find_campaign(campaign_name)
                except Exception as exc:
                    # 이 저장소는 PUBLIC — 응답 본문(메시지)은 공개 로그에 남을 수 있어 클래스명만.
                    verdict, reason = "api-error", f"Brevo 조회 실패: {type(exc).__name__}"
                else:
                    verdict, reason = decide_freshness(latest, as_of, campaign)
                    campaign_status = campaign.get("status") if campaign else None

        report = {
            "verdict": verdict, "reason": reason, "as_of": as_of.isoformat(),
            "latest_publish_date": latest, "campaign_name": campaign_name,
            "campaign_status": campaign_status,
            "checked_at_kst": _datetime.now(KST).isoformat(timespec="seconds"),
        }
        print(f"뉴스레터 신선도: {verdict} — {reason}")
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0 if verdict == "ok" else (2 if verdict == "api-error" else 1)

    if not args.publish_date:
        ap.error("--publish-date 필요(latest-date·freshness 모드 제외)")

    site_base_url = render.SITE_BASE_URL
    brief_obj, issue_no = load_issue(args.data, args.publish_date)

    # 멱등 사전점검(③) — send 경로 전용(발송 0). send 잡(=사람 승인 게이트)에 도달할지만 결정.
    # 게이트 ①②(구조·provenance·링크체크)는 워크플로의 validate 스텝이 이미 실행(여기선 재실행 X).
    if args.mode == "precheck":
        api_key = _env("NEWSLETTER_API_KEY")
        if not api_key:
            _emit_should_send(False, "NEWSLETTER_API_KEY 미설정 — 멱등 조회 불가 → 발송 보류(클린 skip)")
            return 0
        sender = BrevoSender(api_key)
        should, reason = decide_should_send(sender, args.publish_date, issue_no)
        _emit_should_send(should, reason)
        return 0

    # 그 호에 얹을 "서비스 소식"(있을 때만). announce 는 newsletter 를 import 하므로 여기서
    # 지연 import 로 순환을 끊는다 — 공지가 없으면 updates_html="" → 메일 바이트 불변.
    import announce
    ann = announce.find_for_weekly(announce.DATA_DIR, args.publish_date)
    updates_html = ""
    if ann is not None:
        ann_fails = announce.gate_schema(ann)
        if ann_fails:                  # 깨진 공지가 주간 발송을 오염시키지 않도록 즉시 차단
            print("[FAIL] 주간호에 얹을 공지 스키마 오류 — 발송 보류", file=sys.stderr)
            for f in ann_fails:
                print(f"  · {f}", file=sys.stderr)
            return 1
        updates_html = announce.render_weekly_block(ann, site_base_url=site_base_url)
        print(f"서비스 소식 블록 삽입: {ann['id']} (항목 {len(ann.get('items') or [])}건)")

    # 그 주 자료실 유입(있을 때만) — 신선도 창 밖이거나 변경 0이면 library_html="" → 바이트 불변.
    lib_update = library_update_for_issue(args.publish_date)
    library_html = ""
    if lib_update is not None:
        library_html = render_library_block(lib_update, site_base_url=site_base_url)
        print(f"자료실 업데이트 블록 삽입: {lib_update['date']} "
              f"({lib_update['change_count']}건 / 카탈로그 {lib_update['catalog_count']}종)")

    report, teaser = run_gates(brief_obj, expected_date=args.publish_date,
                               site_base_url=site_base_url, issue_no=issue_no,
                               run_linkcheck=not args.no_linkcheck,
                               updates_html=updates_html, library_html=library_html)
    print(report.text())
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(teaser["html"].encode("utf-8"))
        print(f"메일 HTML 저장(검토용): {args.out}")
    print(f"제목: {teaser['subject']}")
    if not report.ok:
        print("→ 발송 보류: 위 FAIL 을 해소한 뒤 다시 게이트를 통과시켜야 발송한다.", file=sys.stderr)
        return 1
    if args.mode == "validate":
        print("검증 모드 — 발송 안 함(게이트 PASS).")
        return 0

    # 발송(test/send) — SaaS 자격·대상 확인.
    api_key = _env("NEWSLETTER_API_KEY")
    sender_name = _env("GRM_NEWSLETTER_SENDER_NAME", "Global Regulatory Monitor")
    sender_email = _env("GRM_NEWSLETTER_SENDER_EMAIL")
    if not api_key or not sender_email:
        print("⚠️  NEWSLETTER_API_KEY·GRM_NEWSLETTER_SENDER_EMAIL 미설정 — 발송 불가(게이트는 PASS).",
              file=sys.stderr)
        return 2
    sender = BrevoSender(api_key)
    name = idempotency_campaign_name(args.publish_date, issue_no)
    # 발송본은 수신거부 스니펫(SaaS-특정·어댑터 책임)을 넣어 재빌드. 게이트는 정본(무-수신거부)
    # 티저로 통과했고, 수신거부 추가는 무변형/provenance 와 무관(우리 카드 URL 불변).
    teaser2 = build_teaser(brief_obj, site_base_url=site_base_url, issue_no=issue_no,
                           unsubscribe_html=BREVO_UNSUBSCRIBE_HTML,
                           updates_html=updates_html, library_html=library_html)

    if args.mode == "test":
        test_emails = [x for x in _env("GRM_NEWSLETTER_TEST_EMAILS").replace(";", ",").split(",")
                       if x.strip()]
        if not test_emails:
            print("⚠️  GRM_NEWSLETTER_TEST_EMAILS 미설정 — 테스트 발송 대상 없음.", file=sys.stderr)
            return 2
        list_ids = _list_ids(_env("GRM_NEWSLETTER_LIST_ID"))
        if not list_ids:
            print("⚠️  GRM_NEWSLETTER_LIST_ID 미설정 — Brevo 캠페인 생성에 리스트 필요(테스트도).",
                  file=sys.stderr)
            return 2
        cid = sender.create_campaign(name=f"{name} [TEST]", subject=teaser2["subject"],
                                     html=teaser2["html"], list_ids=list_ids,
                                     sender_name=sender_name, sender_email=sender_email)
        sender.send_test(cid, [x.strip() for x in test_emails])
        print(f"테스트 발송 완료(캠페인 {cid}) → {mask_emails(x.strip() for x in test_emails)}")
        return 0

    # mode == send — 멱등(③) 후 실발송.
    existing = sender.find_campaign(name)
    if existing and existing.get("status", "").lower() in _DISPATCHED_STATUSES:
        print(f"멱등: 이미 발송/예약된 호(status={existing.get('status')}) — 캠페인 "
              f"{existing['id']}({name}). 재발송 안 함.")
        return 0
    list_ids = _list_ids(_env("GRM_NEWSLETTER_LIST_ID"))
    if not list_ids:
        print("⚠️  GRM_NEWSLETTER_LIST_ID 미설정 — 발송 대상 리스트 없음.", file=sys.stderr)
        return 2
    if existing:                       # 이전 실패로 남은 미발송 draft → 재사용(중복 생성 방지)
        cid = existing["id"]
        print(f"이전 미발송 캠페인 재사용(status={existing.get('status')}) → sendNow: {cid}")
    else:
        cid = sender.create_campaign(name=name, subject=teaser2["subject"], html=teaser2["html"],
                                     list_ids=list_ids, sender_name=sender_name,
                                     sender_email=sender_email)
    sender.send_campaign(cid)
    print(f"발송 완료: 캠페인 {cid}({name}) → 리스트 {list_ids}")
    return 0


def _list_ids(raw: str) -> list[int]:
    """쉼표 구분 Brevo 리스트 id 문자열 → int 리스트(빈값 무시)."""
    out: list[int] = []
    for tok in (raw or "").replace(";", ",").split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.append(int(tok))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
