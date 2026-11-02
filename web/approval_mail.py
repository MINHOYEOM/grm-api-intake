#!/usr/bin/env python3
"""결재 요청 메일 — 월요일 브리프 발행 PR 이 준비되면 대표 메일함으로 1통.

설계(GRM_조직도_점검 9-1): 메일은 **코드가** 보낸다(AI 아님·토큰 0). 수신자는 저장소 변수
`GRM_APPROVAL_MAIL_TO` 한 곳 — 주소 1개가 아니면 발송을 거부한다. 본문은 3줄 + 버튼 1개이고
PR 내용·비밀값은 싣지 않는다. 링크는 관리자 사이트 홈(/admin/) 뿐이라 메일만으로는 아무것도
승인되지 않는다. 재알림 없음 — 같은 PR 에는 숨김 표식 댓글이 있으면 다시 보내지 않는다.

발송은 Brevo 트랜잭션 이메일(`v3/smtp/email`) — growth_weekly.send_email 과 같은 경로를 재사용한다.
실패는 삼키지 않는다(비 0 종료 → Actions 실패).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from typing import Any

import requests

MARKER = "<!-- grm-approval-mail:v1 -->"
BRANCH_RE = re.compile(r"^publish/brief-(\d{4})-(\d{2})-(\d{2})$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
OK_CONCLUSIONS = {"success", "skipped", "neutral"}
# 이 워크플로 자신의 체크는 '전부 success' 판정에서 뺀다(자기 자신을 기다리지 않게).
SELF_CHECK_NAMES = {"approval-mail"}


def week_label(branch: str) -> str:
    """`publish/brief-2026-11-09` → `W46` (ISO 주차)."""
    m = BRANCH_RE.match(branch)
    if not m:
        raise ValueError(f"발행 PR 브랜치가 아님: {branch!r}")
    d = dt.date(int(m[1]), int(m[2]), int(m[3]))
    return f"W{d.isocalendar()[1]}"


def resolve_recipient(raw: str) -> str:
    """수신자는 정확히 1개의 주소. 쉼표·세미콜론·공백으로 여럿을 넣으면 거부."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("GRM_APPROVAL_MAIL_TO 미설정 — 수신자 없음")
    if not EMAIL_RE.match(raw):
        raise ValueError("GRM_APPROVAL_MAIL_TO 는 주소 1개여야 합니다")
    return raw


def build_message(week: str, admin_url: str) -> tuple[str, str]:
    subject = f"[GRM 결재] {week} 브리프 발행 — 미리보기 확인"
    lines = [
        f"<b>무엇을</b> · {week} 주간 브리프 발행 PR 이 준비됐고 자동 검사가 모두 통과했습니다.",
        "<b>왜</b> · 미리보기를 확인하고 승인해야 사이트와 뉴스레터에 반영됩니다.",
        "<b>안 누르면</b> · 발행되지 않고 지난주 브리프가 그대로 유지됩니다.",
    ]
    body = "".join(f'<p style="margin:0 0 10px;font-size:15px;line-height:1.6">{x}</p>' for x in lines)
    button = (
        f'<p style="margin:18px 0 0"><a href="{admin_url}" style="background:#1f3a5f;color:#fff;'
        'padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600">관리자에서 열기</a></p>'
    )
    note = '<p style="margin:18px 0 0;color:#777;font-size:12px">이 메일의 링크만으로는 승인되지 않습니다. 관리자 사이트에 로그인해 승인하세요.</p>'
    html = f'<div style="font-family:system-ui,sans-serif;max-width:520px">{body}{button}{note}</div>'
    return subject, html


def checks_ready(check_runs: list[dict[str, Any]]) -> tuple[bool, str]:
    runs = [c for c in check_runs if c.get("name") not in SELF_CHECK_NAMES]
    if not runs:
        return False, "체크런 없음"
    pending = [c["name"] for c in runs if c.get("status") != "completed"]
    if pending:
        return False, f"진행 중 {len(pending)}건: {pending[0]}"
    bad = [c["name"] for c in runs if c.get("conclusion") not in OK_CONCLUSIONS]
    if bad:
        return False, f"실패/취소 {len(bad)}건: {bad[0]}"
    return True, f"체크 {len(runs)}건 전부 통과"


class GH:
    def __init__(self, repo: str, token: str):
        self.base = f"https://api.github.com/repos/{repo}"
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def get(self, path: str, **params: Any) -> Any:
        r = requests.get(self.base + path, headers=self.h, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def paged(self, path: str, key: str | None = None) -> list[Any]:
        out: list[Any] = []
        for page in range(1, 11):
            data = self.get(path, per_page=100, page=page)
            items = data[key] if key else data
            out += items
            if len(items) < 100:
                break
        return out

    def comment(self, number: int, text: str) -> None:
        r = requests.post(f"{self.base}/issues/{number}/comments", headers=self.h, json={"body": text}, timeout=20)
        r.raise_for_status()


def send_email(api_key: str, sender_name: str, sender_email: str, to: str, subject: str, html: str) -> None:
    headers = {"api-key": api_key, "accept": "application/json", "content-type": "application/json"}
    body = {"sender": {"name": sender_name, "email": sender_email}, "to": [{"email": to}],
            "subject": subject, "htmlContent": html}
    r = requests.post("https://api.brevo.com/v3/smtp/email", headers=headers, json=body, timeout=20)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"Brevo 발송 실패: status={r.status_code}")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", required=True, help="발행 PR head 브랜치(publish/brief-YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="조건 판정·메일 본문 출력만(발송·표식 0)")
    a = ap.parse_args(argv)

    if not BRANCH_RE.match(a.branch):
        print(f"::notice::발행 PR 브랜치가 아니어서 건너뜀: {a.branch}")
        return 0
    week = week_label(a.branch)
    admin_url = _env("GRM_SITE_BASE_URL", "https://grm-solutions.com").rstrip("/") + "/admin/"
    subject, html = build_message(week, admin_url)

    gh = GH(_env("GITHUB_REPOSITORY"), _env("GITHUB_TOKEN"))
    owner = _env("GITHUB_REPOSITORY").split("/")[0]
    prs = gh.get("/pulls", head=f"{owner}:{a.branch}", state="open")
    if not prs:
        print("::notice::열린 발행 PR 없음 — 발송 안 함")
        return 0
    pr = prs[0]
    ok, why = checks_ready(gh.paged(f"/commits/{pr['head']['sha']}/check-runs", "check_runs"))
    if not ok:
        print(f"::notice::아직 준비 안 됨 — {why}")
        return 0
    if any(MARKER in (c.get("body") or "") for c in gh.paged(f"/issues/{pr['number']}/comments")):
        print(f"::notice::PR #{pr['number']} 은 이미 발송함(표식 있음) — 중복 발송 0")
        return 0

    if a.dry_run:
        to = _env("GRM_APPROVAL_MAIL_TO")
        print(f"[dry-run] PR #{pr['number']} {why}\n  수신자 설정: {'있음' if to else '없음'} (값은 로그에 쓰지 않음)\n  제목: {subject}\n  링크: {admin_url}")
        if to:
            resolve_recipient(to)  # 형식 오류는 dry-run 에서도 드러나게
        return 0

    to = resolve_recipient(_env("GRM_APPROVAL_MAIL_TO"))
    api_key, sender = _env("NEWSLETTER_API_KEY"), _env("GRM_NEWSLETTER_SENDER_EMAIL")
    if not api_key or not sender:
        print("::error::NEWSLETTER_API_KEY/GRM_NEWSLETTER_SENDER_EMAIL 미설정 — 발송 불가", file=sys.stderr)
        return 1
    send_email(api_key, _env("GRM_NEWSLETTER_SENDER_NAME", "Global Regulatory Monitor"), sender, to, subject, html)
    gh.comment(pr["number"], f"{MARKER}\n결재 요청 메일을 대표 메일함으로 발송했습니다({week}). 재알림은 없습니다.")
    print(f"결재 요청 메일 발송 완료 — PR #{pr['number']} {week}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
