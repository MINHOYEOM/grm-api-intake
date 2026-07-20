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
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data" / "briefs"

# render.py(같은 디렉터리·순수·네트워크 0) — issue 번호/제목/섹션/SITE_BASE_URL 단일 파생원.
import render  # noqa: E402

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


# ── 티저 메일 빌더(순수·결정론·무변형) ────────────────────────────────────────
_WRAP = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
         "Arial,'Apple SD Gothic Neo','Malgun Gothic',sans-serif")


def build_teaser(brief_obj: dict[str, Any], *, site_base_url: str, issue_no: int,
                 unsubscribe_html: str = "", updates_html: str = "") -> dict[str, Any]:
    """web-card/v1 브리프 1건 → 티저 메일(subject + HTML). 순수.

    담는 것: 제목(=tldr[0] 또는 날짜파생)·발행일·호수·tldr(verbatim)·전체보기 CTA·섹션 앵커
    링크·면책 캐논. 담지 않는 것: 카드 사실/원문 인용/카드 출처 URL(무변형·provenance).
    `unsubscribe_html` = SaaS-특정 수신거부 스니펫(어댑터가 주입; 본문 빌더는 SaaS-무관).
    `updates_html` = 그 호에 얹을 "서비스 소식" 블록(`announce.render_weekly_block` 산출).
    **기본값 빈 문자열 → 출력 바이트 불변**(공지 없는 주는 기존 메일과 완전히 동일). 우리
    사이트 변화 안내를 별도 발송 없이 주간호에 태우는 경로다(2단 구성의 기본선).
    """
    bm = brief_obj["brief"]
    pub = bm.get("publish_date", "")
    dateform = render.title_dateform(pub)
    title = render._brief_title(bm)
    tldr = [t for t in (bm.get("tldr") or []) if t]
    base = site_base_url.rstrip("/")
    brief_url = brief_anchor_href(base, pub)
    subject = f"[GRM 규제뉴스] {dateform} ({pub} 발행)"

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
    if tldr:
        parts.append('<div style="font-size:13px;font-weight:600;color:#A14B30;'
                     'margin-bottom:8px">이번 주 핵심</div>')
        parts.append('<ul style="margin:0 0 24px;padding-left:20px;color:#141413">')
        for t in tldr:
            parts.append(f'<li style="margin:8px 0">{e(t)}</li>')
        parts.append("</ul>")
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
    # 서비스 소식(선택) — 규제 소식 뒤·면책 앞. 주인공은 그 주 규제 소식이므로 뒤에 붙인다.
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


def gate_provenance(teaser: dict[str, Any], site_base_url: str) -> list[str]:
    """provenance/무변형 게이트 — 메일이 우리 페이지만 링크하고 추적 파라미터를 부착하지
    않는지. 카드 출처 URL(보호 대상)은 애초에 본문에 없음 → 우리 산출 링크의 청결만 확인.
    (SaaS 가 발송 시점에 자기 도메인으로 래핑하는 것은 우리 산출물 밖 — 무변형 보존.)"""
    fails: list[str] = []
    base_host = (urlsplit(site_base_url).hostname or "").lower()
    hrefs = re.findall(r'href="([^"]*)"', teaser.get("html", ""))
    for h in hrefs:
        sp = urlsplit(h)
        host = (sp.hostname or "").lower()
        if host and host != base_host:
            fails.append(f"외부 호스트 링크(우리 페이지 아님): {h}")
        if sp.query:
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
              updates_html: str = "") -> tuple[GateReport, dict[str, Any]]:
    """발행검증(구조·provenance) + (선택)링크체크 게이트를 1회 실행하고 티저를 만든다.
    반환=(GateReport, teaser). 멱등(③)·사람승인(④)은 발송 워크플로/어댑터 레이어.

    `updates_html` 이 실린 호는 provenance 게이트가 그 블록의 링크까지 함께 훑는다(공지
    블록이 외부 호스트·추적 파라미터를 들여오면 주간 발송 자체가 보류된다)."""
    teaser = build_teaser(brief_obj, site_base_url=site_base_url, issue_no=issue_no,
                          updates_html=updates_html)
    reasons: list[str] = []
    struct_fails = gate_publishable(brief_obj, expected_date)
    reasons.append(f"구조 검증: {'OK' if not struct_fails else 'FAIL'}")
    prov_fails = gate_provenance(teaser, site_base_url)
    reasons.append(f"provenance(우리 페이지·추적 파라미터 0): {'OK' if not prov_fails else 'FAIL'}")
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
    ap.add_argument("--mode", choices=["validate", "test", "send", "latest-date", "precheck"],
                    default="validate",
                    help="validate=게이트만(네트워크는 링크체크) · test=테스트발송 · send=실발송 · "
                         "latest-date=최신 발행일만 출력(스케줄 해석) · "
                         "precheck=멱등 사전점검(should_send 방출, 발송 0)")
    ap.add_argument("--out", type=Path, default=None, help="렌더된 메일 HTML 저장(D5 사람 검토 아티팩트)")
    ap.add_argument("--no-linkcheck", action="store_true", help="링크체크 게이트 건너뜀(오프라인 검증)")
    args = ap.parse_args(argv)

    # 스케줄 해석 보조 — 최신 발행일만 결정론으로 출력(게이트·로딩·네트워크 0).
    if args.mode == "latest-date":
        print(resolve_latest_publish_date(args.data))
        return 0

    if not args.publish_date:
        ap.error("--publish-date 필요(latest-date 모드 제외)")

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

    report, teaser = run_gates(brief_obj, expected_date=args.publish_date,
                               site_base_url=site_base_url, issue_no=issue_no,
                               run_linkcheck=not args.no_linkcheck,
                               updates_html=updates_html)
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
                           unsubscribe_html=BREVO_UNSUBSCRIBE_HTML, updates_html=updates_html)

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
        print(f"테스트 발송 완료(캠페인 {cid}) → {', '.join(x.strip() for x in test_emails)}")
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
