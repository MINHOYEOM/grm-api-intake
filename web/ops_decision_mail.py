#!/usr/bin/env python3
"""결정 대기 알림 메일 — 운영 센터에 새 결정이 올라오면 대표 메일함으로 알린다(설계서 §4-4, D2).

메일은 **코드가** 보낸다(AI 아님·토큰 0). `approval_mail.py`(월요일 발행 결재 메일)와 같은 안전 장치를 쓴다:
  - 수신자는 저장소 변수 `GRM_APPROVAL_MAIL_TO` 한 곳(주소 1개가 아니면 거부) — 대표 본인.
  - 본문은 3줄(무엇을·권장·기한) + 버튼 1개('관리자에서 열기', /admin/ 뿐). 메일로는 승인되지 않는다.
  - 기본은 dry-run. 저장소 변수 `GRM_OPS_DECISION_MAIL_ENABLED=true` 일 때만 실발송(대표 확인 뒤 비서실이 켠다).

보내는 규칙:
  - 대상 = `state='pending' and mail_sent_at is null` 인 결정(service_role 로 읽는다). 1건 1통.
  - 하루(KST) 3통 상한. 넘치는 건은 다음 날 08:00 이후 첫 실행에서 '묶음 1통'으로 보낸다.
    (어제 이전에 올라와 아직 못 보낸 건 = 묶음. 묶음도 하루 3통에 들어간다.)
  - 조용한 시간 KST 22:00~08:00 에는 보내지 않는다(그 사이 올라온 건은 아침 묶음으로).
  - 보류 후 다시 올라온 결정은 보내지 않는다(이미 mail_sent_at 이 있다 — 재알림 없음, 10-31 확정).
  - 발송 성공 뒤에만 mail_sent_at 을 쓴다(실패하면 다음 실행이 다시 시도). 같은 묶음은 같은 시각을 쓴다
    → '오늘 보낸 통 수' = 오늘 mail_sent_at 의 서로 다른 값 수.
  - 로그에는 건수·id 만 남긴다(결정 제목·수신자·키는 쓰지 않는다 — 저장소 Actions 로그는 공개될 수 있다).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from approval_mail import resolve_recipient, send_email  # noqa: E402  같은 폴더 — 발송·수신자 검사 재사용

KST = dt.timezone(dt.timedelta(hours=9))
DAILY_CAP = 3
QUIET_FROM, QUIET_TO = 22, 8      # KST 시각 [22, 24) ∪ [0, 8) 은 보내지 않는다
WEEKDAY = "월화수목금토일"


@dataclass
class Mail:
    ids: list[int]
    subject: str
    html: str
    digest: bool = False
    rows: list[dict[str, Any]] = field(default_factory=list)


def _parse(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _day(ts: str | None) -> str:
    d = _parse(ts)
    if not d:
        return "기한 없음"
    k = d.astimezone(KST)
    return f"{k:%m-%d}({WEEKDAY[k.weekday()]})"


def _team(row: dict[str, Any]) -> str:
    t = row.get("ops_team")
    return (t or {}).get("name", "") if isinstance(t, dict) else ""


def is_quiet(now: dt.datetime) -> bool:
    h = now.astimezone(KST).hour
    return h >= QUIET_FROM or h < QUIET_TO


def kst_day_start(now: dt.datetime) -> dt.datetime:
    k = now.astimezone(KST)
    return dt.datetime(k.year, k.month, k.day, tzinfo=KST)


def batches_sent_today(sent_times: list[str], now: dt.datetime) -> int:
    start = kst_day_start(now)
    return len({t for t in sent_times if (_parse(t) or start - dt.timedelta(days=1)) >= start})


def _wrap(lines: list[str], admin_url: str) -> str:
    body = "".join(f'<p style="margin:0 0 10px;font-size:15px;line-height:1.6">{x}</p>' for x in lines)
    button = (f'<p style="margin:18px 0 0"><a href="{html.escape(admin_url, quote=True)}" style="background:#1f3a5f;'
              'color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600">관리자에서 열기</a></p>')
    note = ('<p style="margin:18px 0 0;color:#777;font-size:12px">메일로는 승인되지 않습니다. '
            '관리자 사이트에 로그인해 결재 서류에서 승인하거나 보류하세요.</p>')
    return f'<div style="font-family:system-ui,sans-serif;max-width:520px">{body}{button}{note}</div>'


def build_single(row: dict[str, Any], admin_url: str) -> Mail:
    title = str(row.get("title") or "")
    team = _team(row)
    lines = [
        f"<b>무엇을</b> · {html.escape((team + ' — ') if team else '')}{html.escape(title)}",
        f"<b>권장</b> · {html.escape(str(row.get('recommendation') or '비서실 권장 없음'))}",
        f"<b>기한</b> · {html.escape(_day(row.get('due_at')))}",
    ]
    short = title if len(title) <= 40 else title[:39] + "…"
    return Mail([int(row["id"])], f"[GRM 결재] {short}", _wrap(lines, admin_url), rows=[row])


def build_digest(rows: list[dict[str, Any]], admin_url: str) -> Mail:
    titles = ", ".join(html.escape(str(r.get("title") or "")) for r in rows[:3])
    more = f" 외 {len(rows) - 3}건" if len(rows) > 3 else ""
    dues = sorted(d for d in (_parse(r.get("due_at")) for r in rows) if d)
    lines = [
        f"<b>무엇을</b> · 결재 서류 {len(rows)}건이 기다립니다: {titles}{more}",
        "<b>권장</b> · 서류마다 비서실 권장이 관리자 홈에 있습니다.",
        f"<b>기한</b> · 가장 이른 기한 {html.escape(_day(dues[0].isoformat())) if dues else '없음'}",
    ]
    return Mail([int(r["id"]) for r in rows], f"[GRM 결재] 결재 서류 {len(rows)}건", _wrap(lines, admin_url),
                digest=True, rows=rows)


def plan(pending: list[dict[str, Any]], sent_times: list[str], now: dt.datetime, admin_url: str) -> list[Mail]:
    """보낼 메일 목록. pending 은 state=pending·mail_sent_at=null 행(생성 순)."""
    if is_quiet(now) or not pending:
        return []
    remaining = DAILY_CAP - batches_sent_today(sent_times, now)
    if remaining <= 0:
        return []
    start = kst_day_start(now)
    rows = sorted(pending, key=lambda r: (r.get("created_at") or "", r.get("id") or 0))
    carried = [r for r in rows if (_parse(r.get("created_at")) or start) < start]
    fresh = [r for r in rows if r not in carried]
    mails: list[Mail] = []
    if carried:
        mails.append(build_single(carried[0], admin_url) if len(carried) == 1 else build_digest(carried, admin_url))
        remaining -= 1
    for r in fresh:
        if remaining <= 0:
            break
        mails.append(build_single(r, admin_url))
        remaining -= 1
    return mails


class Rest:
    def __init__(self, url: str, key: str):
        import requests  # 지역 import — 순수 함수 시험은 requests 없이 돈다
        self.requests = requests
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        r = self.requests.get(self.base + path, headers=self.h, params=params, timeout=20)
        if not r.ok:
            raise RuntimeError(f"Supabase 읽기 실패: status={r.status_code}")
        return r.json()

    def mark_sent(self, ids: list[int], at: dt.datetime) -> None:
        r = self.requests.patch(self.base + "/ops_decision", headers={**self.h, "Prefer": "return=minimal"},
                                params={"id": f"in.({','.join(str(i) for i in ids)})", "mail_sent_at": "is.null"},
                                json={"mail_sent_at": at.isoformat()}, timeout=20)
        if not r.ok:
            raise RuntimeError(f"mail_sent_at 기록 실패: status={r.status_code}")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def main(argv: list[str] | None = None, now: dt.datetime | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="보낼 건수·id 만 출력(발송·기록 0)")
    a = ap.parse_args(argv)
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    now = now or dt.datetime.now(dt.timezone.utc)
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("::error::SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 미설정", file=sys.stderr)
        return 1
    admin_url = _env("GRM_SITE_BASE_URL", "https://grm-solutions.com").rstrip("/") + "/admin/"
    rest = Rest(url, key)
    pending = rest.get("/ops_decision", {
        "select": "id,title,recommendation,due_at,created_at,ops_team(name)",
        "state": "eq.pending", "mail_sent_at": "is.null", "order": "created_at.asc,id.asc"})
    since = kst_day_start(now).astimezone(dt.timezone.utc).isoformat()
    sent = [r["mail_sent_at"] for r in rest.get("/ops_decision", {"select": "mail_sent_at", "mail_sent_at": f"gte.{since}"})]
    mails = plan(pending, sent, now, admin_url)
    print(f"대기 {len(pending)}건 · 오늘 보낸 통 {batches_sent_today(sent, now)}/{DAILY_CAP} · "
          f"이번에 보낼 통 {len(mails)}" + (" · 조용한 시간" if is_quiet(now) else ""))
    if a.dry_run:
        for m in mails:
            print(f"[dry-run] {'묶음' if m.digest else '1건'} id={m.ids} (제목·수신자는 로그에 쓰지 않음)")
        to = _env("GRM_APPROVAL_MAIL_TO")
        print(f"[dry-run] 수신자 설정: {'있음' if to else '없음'}")
        if to:
            resolve_recipient(to)
        return 0
    to = resolve_recipient(_env("GRM_APPROVAL_MAIL_TO"))
    api_key, sender = _env("NEWSLETTER_API_KEY"), _env("GRM_NEWSLETTER_SENDER_EMAIL")
    if mails and (not api_key or not sender):
        print("::error::NEWSLETTER_API_KEY/GRM_NEWSLETTER_SENDER_EMAIL 미설정 — 발송 불가", file=sys.stderr)
        return 1
    for m in mails:
        send_email(api_key, _env("GRM_NEWSLETTER_SENDER_NAME", "Global Regulatory Monitor"), sender, to, m.subject, m.html)
        rest.mark_sent(m.ids, dt.datetime.now(dt.timezone.utc))
        print(f"발송 완료 — {'묶음' if m.digest else '1건'} id={m.ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
