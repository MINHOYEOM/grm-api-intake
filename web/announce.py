#!/usr/bin/env python3
"""GRM 서비스 업데이트 안내(announce) — 사이트 신규 기능 공지 + 주간 메일 삽입 블록.

주간 브리프 뉴스레터(`newsletter.py`)가 "그 주 규제 소식"을 보낸다면, 이 모듈은 **우리
사이트 자체의 변화**(자료실·퀴즈·용어사전 같은 신규 기능, 새 규제 소스 편입)를 알린다.
발송 배관(`BrevoSender`·리스트·발신자·수신거부)은 뉴스레터 것을 **그대로 재사용**하고,
다른 것은 입력뿐이다 — 브리프 JSON 이 아니라 사람이 큐레이션한 공지 데이터.

## 2단 구성(발송 피로 최소화)
  1. **주간 삽입(기본선)** — 주간 티저 메일 안에 "서비스 소식" 블록을 조건부로 얹는다.
     공지 데이터의 `weekly_publish_date` 가 그 호 발행일과 일치할 때만 렌더되고, 없으면
     출력 0(기존 메일 바이트 불변). 별도 발송 0건 → 수신 피로·표시의무 리스크 0.
  2. **별도 발송(마일스톤)** — 큰 기능군 출시에만 독립 캠페인. 스케줄 없음(수동 dispatch
     전용) — "매주 자동으로 나가는 공지"는 만들지 않는다.

## 불변식
  1. **큐레이션 원천** — 공지 문구는 사람이 쓴 JSON 이 유일 정본이다. PR 제목·커밋 로그
     자동 추출 금지(내부 개념 — Tier/registry/백로그 번호 — 이 그대로 새어나간다).
  2. **우리 페이지만** — 항목 링크는 사이트 상대경로(`path`, `/` 시작)로만 적고 절대 URL
     조립은 빌더가 한다. 외부 호스트를 애초에 표현할 수 없다(`gate_provenance` 와 이중).
  3. **결정론** — 같은 입력 → 같은 subject·HTML(`now()`/난수 0). 빌더는 순수.
  4. **게이트** — ①스키마(구조·항목 수·경로 형식) ②provenance(우리 도메인·추적 파라미터 0)
     ③링크 실존(공지한 페이지가 실제로 200 인지 — 없는 기능 홍보 차단) ④멱등(캠페인명 키).
  5. **SaaS 격리** — 발송은 `newsletter.NewsletterSender` 인터페이스 뒤(교체 가능).

순수 코어는 네트워크 import 를 최상단에서 하지 않는다(`linkcheck`/`requests` 는 게이트
호출 시점 지연 import — `newsletter.gate_linkcheck` 와 같은 패턴).
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data" / "announcements"

# 같은 디렉터리 — 발송 배관(BrevoSender·게이트·GateReport)·메일 톤 단일 파생원.
import newsletter  # noqa: E402
import render      # noqa: E402

SCHEMA_VERSION = "grm-announce/v1"

# 항목 수 상한 — 공지 메일은 "훑고 한 곳 클릭"이 목적. 6개를 넘으면 아무것도 안 읽힌다.
MAX_ITEMS = 6

# 푸터 — 이 메일이 왜 왔는지(발신 근거). 주간 클리핑 구독 동의 문안(base.html 구독 밴드)이
# "새로운 기능이 생기면 함께 안내"를 포함하므로, 같은 리스트로 나가는 근거를 여기서 밝힌다.
FOOTER_NOTICE_KO = ("본 메일은 GRM 규제 클리핑 구독자에게 보내드리는 서비스 안내입니다. "
                    "주간 규제 소식은 기존과 동일하게 매주 발송됩니다.")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 사이트 상대경로만 — 쿼리·앵커·스킴 불가. 선행 `//` 는 명시적으로 막는다(`//evil.example`
# 는 프로토콜-상대 URL 이라 메일 클라이언트가 외부 호스트로 해석할 수 있다).
_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9/_.-]*$")


# ── 로드 ──────────────────────────────────────────────────────────────────────
def load_announcement(data_dir: Path, ann_id: str) -> dict[str, Any]:
    """공지 1건 로드. 파일명(`{id}.json`)과 본문 `id` 불일치는 즉시 실패(정본 이중화 차단)."""
    path = data_dir / f"{ann_id}.json"
    if not path.exists():
        have = ", ".join(sorted(p.stem for p in data_dir.glob("*.json"))) or "(없음)"
        raise SystemExit(f"공지 없음: {path}. 보유: {have}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("id") != ann_id:
        raise SystemExit(f"파일명/본문 id 불일치: {path.name} vs id={obj.get('id')!r}")
    return obj


def load_all(data_dir: Path) -> list[dict[str, Any]]:
    """디렉터리 내 전체 공지(파일명 오름차순 — 결정론). 디렉터리 부재 시 빈 리스트."""
    if not data_dir.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(data_dir.glob("*.json"))]


def find_for_weekly(data_dir: Path, publish_date: str) -> "dict[str, Any] | None":
    """그 주간호 티저에 얹을 공지(`weekly_publish_date` 정확 일치). 없으면 None.

    2건 이상 일치하면 실패한다 — "어느 걸 실을지"를 코드가 임의로 고르면 조용한 누락이
    생긴다. 한 호에 하나만 얹는다(사람이 병합해서 쓰면 된다)."""
    hits = [a for a in load_all(data_dir)
            if (a.get("weekly_publish_date") or "") == publish_date]
    if len(hits) > 1:
        ids = ", ".join(str(a.get("id")) for a in hits)
        raise SystemExit(f"발행일 {publish_date} 에 얹을 공지가 {len(hits)}건({ids}) — 1건만 허용")
    return hits[0] if hits else None


# ── URL 조립 ──────────────────────────────────────────────────────────────────
def announce_href(site_base_url: str, path: str) -> str:
    """사이트 상대경로 → 절대 URL. 추적 파라미터 0(무변형·provenance 보존)."""
    return f"{site_base_url.rstrip('/')}{path}"


def all_paths(ann: dict[str, Any]) -> list[str]:
    """공지가 참조하는 사이트 경로 전체(항목 + CTA, 등장 순서·중복 제거)."""
    out: list[str] = []
    for it in (ann.get("items") or []):
        p = (it or {}).get("path")
        if p and p not in out:
            out.append(p)
    cta_path = ((ann.get("cta") or {}).get("path")) or ""
    if cta_path and cta_path not in out:
        out.append(cta_path)
    return out


# ── 게이트 ① 스키마 ───────────────────────────────────────────────────────────
def gate_schema(ann: dict[str, Any]) -> list[str]:
    """구조 게이트 — 사람이 손으로 쓰는 파일이므로 형식을 강하게 잡는다. 실패 사유(빈=통과)."""
    fails: list[str] = []
    if ann.get("schema_version") != SCHEMA_VERSION:
        fails.append(f"schema_version 불일치: {ann.get('schema_version')!r} ({SCHEMA_VERSION} 기대)")
    ann_id = ann.get("id") or ""
    if not _ID_RE.match(ann_id):
        fails.append(f"id 형식 오류: {ann_id!r} (소문자·숫자·하이픈 3~64자)")
    if not _DATE_RE.match(ann.get("date") or ""):
        fails.append(f"date 형식 오류: {ann.get('date')!r} (YYYY-MM-DD)")
    wpd = ann.get("weekly_publish_date")
    if wpd is not None and not _DATE_RE.match(wpd or ""):
        fails.append(f"weekly_publish_date 형식 오류: {wpd!r} (YYYY-MM-DD 또는 null)")
    if not (ann.get("title") or "").strip():
        fails.append("title 비어 있음")
    items = ann.get("items") or []
    if not items:
        fails.append("items 0건 — 알릴 게 없는 공지 차단")
    if len(items) > MAX_ITEMS:
        fails.append(f"items {len(items)}건 — 상한 {MAX_ITEMS} 초과")
    for i, it in enumerate(items):
        it = it or {}
        if not (it.get("label") or "").strip():
            fails.append(f"items[{i}].label 비어 있음")
        if not (it.get("text") or "").strip():
            fails.append(f"items[{i}].text 비어 있음")
        if not _PATH_RE.match(it.get("path") or ""):
            fails.append(f"items[{i}].path 형식 오류: {it.get('path')!r} (사이트 상대경로 `/…` 만)")
    cta = ann.get("cta")
    if cta is not None:
        if not (cta.get("text") or "").strip():
            fails.append("cta.text 비어 있음")
        if not _PATH_RE.match(cta.get("path") or ""):
            fails.append(f"cta.path 형식 오류: {cta.get('path')!r} (사이트 상대경로 `/…` 만)")
    return fails


# ── 게이트 ③ 링크 실존(공지한 페이지가 진짜 있는지) ────────────────────────────
def gate_links(ann: dict[str, Any], *, site_base_url: str,
               checker: "Callable[[str], str] | None" = None) -> tuple[list[str], dict[str, str]]:
    """공지가 가리키는 우리 페이지가 실제로 응답하는지. **없는 기능 홍보를 구조적으로 차단**
    한다(오탈자 경로·아직 배포 안 된 페이지). 반환=(실패사유, {url: status}).

    `linkcheck.make_checker` 재사용(HEAD→필요시 GET). 테스트는 fake checker 주입(네트워크 0).
    broken 만 차단하고 degraded/inconclusive(일시 오류·403)는 비차단 — 뉴스레터 게이트와 동형."""
    import linkcheck  # 지연 import(requests) — 순수 코어 import 시 네트워크 0
    own = None
    if checker is None:
        import requests
        own = requests.Session()
        own.headers.update({"User-Agent": linkcheck.USER_AGENT})
        checker = linkcheck.make_checker(own)
    try:
        statuses = {announce_href(site_base_url, p): checker(announce_href(site_base_url, p))
                    for p in all_paths(ann)}
    finally:
        if own is not None:
            own.close()
    fails = [f"링크 broken: {u} — 존재하지 않는 페이지를 공지할 수 없음"
             for u, st in statuses.items() if st == linkcheck.BROKEN]
    return fails, statuses


# ── 주간 티저 삽입 블록(순수·결정론) ──────────────────────────────────────────
def render_weekly_block(ann: dict[str, Any], *, site_base_url: str) -> str:
    """주간 티저 메일에 얹는 "서비스 소식" HTML 조각. 공지가 없으면 호출부가 빈 문자열을
    넘기므로 여기선 항상 내용이 있다고 본다. 주간 메일은 규제 소식이 주인공이므로 이 블록은
    **작게** — 라벨 + 한 줄, 상세는 사이트에서."""
    e = _html.escape
    base = site_base_url.rstrip("/")
    parts = [
        '<div style="border:1px solid #E6DFD8;border-radius:10px;padding:16px 18px;'
        'margin:0 0 26px;background:#FFFFFF">',
        '<div style="font-size:12px;font-weight:600;letter-spacing:.04em;color:#A14B30;'
        'margin-bottom:10px">서비스 소식</div>',
        f'<div style="font-size:15px;font-weight:600;color:#141413;margin-bottom:10px">'
        f'{e(ann.get("title") or "")}</div>',
    ]
    for it in (ann.get("items") or []):
        href = announce_href(base, it["path"])
        parts.append(
            '<div style="font-size:14px;line-height:1.6;color:#3D3D3A;margin:6px 0">'
            f'<a href="{e(href)}" style="color:#A14B30;text-decoration:none;font-weight:600">'
            f'{e(it["label"])}</a> — {e(it["text"])}</div>')
    parts.append("</div>")
    return "".join(parts)


# ── 별도 공지 메일 빌더(순수·결정론) ──────────────────────────────────────────
def build_announcement(ann: dict[str, Any], *, site_base_url: str,
                       unsubscribe_html: str = "") -> dict[str, Any]:
    """공지 1건 → 독립 메일(subject + HTML). 순수.

    주간 티저(`newsletter.build_teaser`)와 톤·폭을 공유하되 제목 접두는 `[GRM 안내]` 로
    구분하고(수신함에서 주간호와 헷갈리지 않게), 푸터의 AI 생성 면책은 **넣지 않는다**
    (사람이 쓴 서비스 안내라 해당 사항 없음 — 아래 푸터 주석)."""
    e = _html.escape
    base = site_base_url.rstrip("/")
    title = (ann.get("title") or "").strip()
    subject = f"[GRM 안내] {title}"
    lede = (ann.get("lede") or "").strip()

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="ko"><head><meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width,initial-scale=1" />',
        f"<title>{e(subject)}</title></head>",
        f'<body style="margin:0;padding:0;background:#FAF9F5;{newsletter._WRAP}">',
        '<div style="max-width:600px;margin:0 auto;padding:32px 24px;color:#3D3D3A;'
        'font-size:16px;line-height:1.6">',
        '<div style="font-size:12px;font-weight:600;letter-spacing:.08em;'
        'text-transform:uppercase;color:#A14B30">Global Regulatory Monitor</div>',
        f'<h1 style="font-size:23px;line-height:1.3;color:#141413;margin:10px 0 4px">{e(title)}</h1>',
        f'<div style="font-size:13px;color:#6C6A64;margin-bottom:22px">{e(ann.get("date") or "")}</div>',
    ]
    if lede:
        parts.append(f'<p style="margin:0 0 24px;color:#141413">{e(lede)}</p>')
    for it in (ann.get("items") or []):
        href = announce_href(base, it["path"])
        parts.append(
            '<div style="border-top:1px solid #E6DFD8;padding:16px 0">'
            f'<div style="font-size:16px;font-weight:600;color:#141413;margin-bottom:5px">'
            f'{e(it["label"])}</div>'
            f'<div style="font-size:15px;line-height:1.65;color:#3D3D3A;margin-bottom:9px">'
            f'{e(it["text"])}</div>'
            f'<a href="{e(href)}" style="color:#A14B30;text-decoration:none;font-size:14px;'
            f'font-weight:600">바로 가기 →</a></div>')
    cta = ann.get("cta")
    if cta:
        href = announce_href(base, cta["path"])
        parts.append(
            f'<div style="margin:26px 0 24px"><a href="{e(href)}" '
            'style="display:inline-block;background:#C2603F;color:#FAF9F5;text-decoration:none;'
            'font-weight:600;font-size:15px;padding:13px 24px;border-radius:8px">'
            f'{e(cta["text"])} →</a></div>')
    # 푸터 — **AI 생성 면책은 넣지 않는다**. 그 캐논("요약·번역·시사점은 생성형 AI가 작성")은
    # 규제 다이제스트의 내용에 대한 고지인데, 이 메일은 사람이 쓴 서비스 안내라 한 글자도
    # 해당되지 않는다. 붙이면 오히려 거짓 고지가 된다. 대신 발신 근거(이 메일이 왜 왔는지)를
    # 밝힌다 — 수신거부 스니펫은 어댑터가 주입.
    parts.append('<div style="border-top:1px solid #E6DFD8;margin-top:8px;padding-top:18px;'
                 'font-size:12px;line-height:1.7;color:#6C6A64">')
    parts.append(f'{e(FOOTER_NOTICE_KO)}')
    if unsubscribe_html:
        parts.append(f'<div style="margin-top:14px;color:#8E8B82">{unsubscribe_html}</div>')
    parts.append("</div>")
    parts.append("</div></body></html>")
    return {"subject": subject, "html": "".join(parts), "item_count": len(ann.get("items") or [])}


# ── 멱등 ──────────────────────────────────────────────────────────────────────
def idempotency_campaign_name(ann_id: str) -> str:
    """발송 멱등 키 = 캠페인명(결정론). 주간호(`GRM Weekly Brief — …`)와 네임스페이스 분리."""
    return f"GRM Update — {ann_id}"


def decide_should_send(sender: "newsletter.NewsletterSender", ann_id: str) -> tuple[bool, str]:
    """멱등 결정 — 이 공지를 지금 보내야 하나? `newsletter.decide_should_send` 와 같은 판정
    규칙(`_DISPATCHED_STATUSES`)을 공유한다."""
    name = idempotency_campaign_name(ann_id)
    existing = sender.find_campaign(name)
    if existing and existing.get("status", "").lower() in newsletter._DISPATCHED_STATUSES:
        return False, (f"이미 발송/예약(status={existing.get('status')}) — 캠페인 "
                       f"{existing['id']}({name}). 재발송 0.")
    if existing:
        return True, f"이전 미발송 draft(status={existing.get('status')}) 재사용 예정 — {name}"
    return True, f"신규 공지 — 발송 필요: {name}"


# ── 게이트 통합 ───────────────────────────────────────────────────────────────
def run_gates(ann: dict[str, Any], *, site_base_url: str,
              checker: "Callable[[str], str] | None" = None,
              run_linkcheck: bool = True) -> tuple["newsletter.GateReport", dict[str, Any]]:
    """①스키마 ②provenance ③링크 실존을 1회 실행하고 공지 메일을 만든다.
    반환=(GateReport, mail). 멱등 ④는 발송 워크플로/CLI 레이어."""
    mail = build_announcement(ann, site_base_url=site_base_url)
    reasons: list[str] = []
    schema_fails = gate_schema(ann)
    reasons.append(f"스키마 검증: {'OK' if not schema_fails else 'FAIL'}")
    # provenance 는 뉴스레터 것 그대로 재사용(우리 호스트 외 링크·쿼리 파라미터 0).
    prov_fails = newsletter.gate_provenance(mail, site_base_url)
    reasons.append(f"provenance(우리 페이지·추적 파라미터 0): {'OK' if not prov_fails else 'FAIL'}")
    fails = schema_fails + prov_fails
    if run_linkcheck and not schema_fails:      # 경로 형식이 깨졌으면 네트워크 낭비 안 함
        link_fails, statuses = gate_links(ann, site_base_url=site_base_url, checker=checker)
        fails += link_fails
        # 경로 그대로 보고한다(호스트 잘라내기 금지 — "/" 가 호스트명으로 보이던 버그).
        reasons.append("링크 실존: " + ", ".join(
            f"{p}={statuses[announce_href(site_base_url, p)]}" for p in all_paths(ann)))
    elif run_linkcheck:
        reasons.append("링크 실존: 건너뜀(스키마 FAIL 선행)")
    else:
        reasons.append("링크 실존: 건너뜀(--no-linkcheck)")
    reasons.extend(fails)
    return newsletter.GateReport(ok=not fails, reasons=reasons, label="공지"), mail


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: "list[str] | None" = None) -> int:
    for _stream in (sys.stdout, sys.stderr):          # Windows cp949 콘솔서도 한글·— 출력
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="GRM 서비스 업데이트 안내 — 공지 메일 빌드·게이트·발송(Brevo). "
                    "주간 뉴스레터와 별도 트리거(수동 dispatch 전용).")
    ap.add_argument("--id", default=None, help="공지 id(= web/data/announcements/{id}.json)")
    ap.add_argument("--data", type=Path, default=DATA_DIR, help="공지 JSON 디렉터리")
    ap.add_argument("--mode", choices=["validate", "test", "send", "precheck", "list"],
                    default="validate",
                    help="validate=게이트만 · test=테스트발송 · send=실발송 · "
                         "precheck=멱등 사전점검(should_send 방출, 발송 0) · list=보유 공지 목록")
    ap.add_argument("--out", type=Path, default=None, help="렌더된 메일 HTML 저장(사람 검토 아티팩트)")
    ap.add_argument("--no-linkcheck", action="store_true", help="링크 실존 게이트 건너뜀(오프라인 검증)")
    args = ap.parse_args(argv)

    if args.mode == "list":
        for a in load_all(args.data):
            wk = a.get("weekly_publish_date") or "-"
            print(f"{a.get('id')}\tdate={a.get('date')}\tweekly={wk}\t{a.get('title')}")
        return 0

    if not args.id:
        ap.error("--id 필요(list 모드 제외)")

    site_base_url = render.SITE_BASE_URL
    ann = load_announcement(args.data, args.id)

    if args.mode == "precheck":
        api_key = newsletter._env("NEWSLETTER_API_KEY")
        if not api_key:
            newsletter._emit_should_send(
                False, "NEWSLETTER_API_KEY 미설정 — 멱등 조회 불가 → 발송 보류(클린 skip)")
            return 0
        should, reason = decide_should_send(newsletter.BrevoSender(api_key), args.id)
        newsletter._emit_should_send(should, reason)
        return 0

    report, mail = run_gates(ann, site_base_url=site_base_url,
                             run_linkcheck=not args.no_linkcheck)
    print(report.text())
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(mail["html"].encode("utf-8"))
        print(f"메일 HTML 저장(검토용): {args.out}")
    print(f"제목: {mail['subject']}")
    if not report.ok:
        print("→ 발송 보류: 위 FAIL 을 해소한 뒤 다시 게이트를 통과시켜야 발송한다.", file=sys.stderr)
        return 1
    if args.mode == "validate":
        print("검증 모드 — 발송 안 함(게이트 PASS).")
        return 0

    api_key = newsletter._env("NEWSLETTER_API_KEY")
    sender_name = newsletter._env("GRM_NEWSLETTER_SENDER_NAME", "Global Regulatory Monitor")
    sender_email = newsletter._env("GRM_NEWSLETTER_SENDER_EMAIL")
    if not api_key or not sender_email:
        print("⚠️  NEWSLETTER_API_KEY·GRM_NEWSLETTER_SENDER_EMAIL 미설정 — 발송 불가(게이트는 PASS).",
              file=sys.stderr)
        return 2
    sender = newsletter.BrevoSender(api_key)
    name = idempotency_campaign_name(args.id)
    mail2 = build_announcement(ann, site_base_url=site_base_url,
                               unsubscribe_html=newsletter.BREVO_UNSUBSCRIBE_HTML)

    if args.mode == "test":
        test_emails = [x.strip() for x in
                       newsletter._env("GRM_NEWSLETTER_TEST_EMAILS").replace(";", ",").split(",")
                       if x.strip()]
        if not test_emails:
            print("⚠️  GRM_NEWSLETTER_TEST_EMAILS 미설정 — 테스트 발송 대상 없음.", file=sys.stderr)
            return 2
        list_ids = newsletter._list_ids(newsletter._env("GRM_NEWSLETTER_LIST_ID"))
        if not list_ids:
            print("⚠️  GRM_NEWSLETTER_LIST_ID 미설정 — Brevo 캠페인 생성에 리스트 필요(테스트도).",
                  file=sys.stderr)
            return 2
        cid = sender.create_campaign(name=f"{name} [TEST]", subject=mail2["subject"],
                                     html=mail2["html"], list_ids=list_ids,
                                     sender_name=sender_name, sender_email=sender_email)
        sender.send_test(cid, test_emails)
        print(f"테스트 발송 완료(캠페인 {cid}) → {', '.join(test_emails)}")
        return 0

    # mode == send — 멱등 후 실발송.
    existing = sender.find_campaign(name)
    if existing and existing.get("status", "").lower() in newsletter._DISPATCHED_STATUSES:
        print(f"멱등: 이미 발송/예약된 공지(status={existing.get('status')}) — 캠페인 "
              f"{existing['id']}({name}). 재발송 안 함.")
        return 0
    list_ids = newsletter._list_ids(newsletter._env("GRM_NEWSLETTER_LIST_ID"))
    if not list_ids:
        print("⚠️  GRM_NEWSLETTER_LIST_ID 미설정 — 발송 대상 리스트 없음.", file=sys.stderr)
        return 2
    if existing:                       # 이전 실패로 남은 미발송 draft → 재사용(중복 생성 방지)
        cid = existing["id"]
        print(f"이전 미발송 캠페인 재사용(status={existing.get('status')}) → sendNow: {cid}")
    else:
        cid = sender.create_campaign(name=name, subject=mail2["subject"], html=mail2["html"],
                                     list_ids=list_ids, sender_name=sender_name,
                                     sender_email=sender_email)
    sender.send_campaign(cid)
    print(f"발송 완료: 캠페인 {cid}({name}) → 리스트 {list_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
