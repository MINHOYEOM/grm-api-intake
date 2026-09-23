#!/usr/bin/env python3
"""Brevo 캠페인 통계(발송·열람·클릭·해지·링크별 클릭) → Supabase 일별 적재
(090 `newsletter_campaign_stats`) — 마케팅 계획 N-05(2026-09-23).

## 왜
뉴스레터가 실제로 읽히는지(열람·클릭·해지)를 볼 곳이 Brevo 화면뿐이었다. 이 스크립트가
하루 1회(`grm-rum-analytics.yml` 의 한 스텝 — 077 구독자 스냅샷 바로 다음 자리) 최근
60일 안에 발송된 캠페인의 통계를 이 표로 옮긴다. 같은 캠페인을 매일 다시 받아 upsert 로
덮는다(열람·클릭은 발송 후 며칠 동안 계속 늘어난다 — 090 마이그레이션 주석 참조).

## 1클릭 피드백(N-04)과의 관계
**이 스크립트는 새 수집 경로가 아니다.** 주간 메일 하단의 "유용했어요/아쉬웠어요"는 같은
브리프 페이지의 `#fb-up`/`#fb-down` 앵커로 가는 두 링크일 뿐이고(`web/newsletter.py
build_teaser`), 여기서 받는 `linksStats`(링크별 클릭 수)에 그 두 URL 이 이미 섞여 있다.
판독(두 수를 갈라내는 일)은 090 의 `newsletter_campaigns_report()` SQL 함수가 한다 — 이
스크립트는 있는 그대로 옮겨 담기만 한다.

## Brevo 응답 모양 — 확인 전 가정(문서 기준, 실측 미확인)
Brevo API 문서가 계정·버전에 따라 응답 위치가 갈리는 사례가 있어(077 스크립트의
`uniqueSubscribers`→`totalSubscribers` 폴백과 같은 교훈) 아래를 전부 방어적으로 읽는다.
  · 목록(`GET /emailCampaigns`) 항목의 통계는 `statistics.globalStats` 아래로 가정한다.
    없으면(구버전 응답 등) 항목 최상위·`globalStats` 키도 순서대로 본다.
  · `globalStats` 키: `sent`·`delivered`·`uniqueViews`·`uniqueClicks`·`clickers`·
    `unsubscriptions`·`hardBounces`·`softBounces`. 없는 키는 **0 으로 지어내지 않고
    None** 으로 둔다(077 스크립트의 "0 을 그대로 저장하면 침묵 실패" 교훈과 같은 결 —
    표의 각 수치 컬럼이 nullable 인 이유이기도 하다, 090 참조).
  · 발송 시각은 `sentDate` 우선, 없으면 `scheduledAt`.
  · `linksStats` 는 목록 응답에 없다고 보고(계정 사양에 링크별 클릭까지 목록에서 주는
    경우를 문서에서 확인하지 못했다) 캠페인마다 상세(`GET /emailCampaigns/{id}
    ?statistics=linksStats`)를 별도로 부른다. 상세 응답의 `linksStats` 는 `{url: clicks}`
    dict 이거나 `statistics.linksStats` 아래일 수 있어 둘 다 본다.
  · 목록에서 `globalStats` 를 못 받은 캠페인만(드묾) `?statistics=globalStats` 상세를
    한 번 더 부른다 — 매 캠페인마다 두 번씩 부르지 않는다(API 예산 절약).

## 로그(PUBLIC 저장소)
이 저장소는 PUBLIC 이고 Actions 로그도 공개다. **캠페인 통계 수치(발송·열람·클릭 등)와
이메일 주소는 절대 찍지 않는다** — 구조만(예: "캠페인 8개 적재(주간 6 · 공지 1 · 기타
1)"). 077 스크립트는 "구독자 수"라는 단일 총계는 운영 사실이라 허용했지만, 이 표는
캠페인별 세부 수치라 성격이 다르다(개별 호의 성과가 캠페인명과 함께 새는 것을 막는다).
HTTP 오류도 상태 코드만 찍는다(응답 본문에 캠페인명·수치가 섞일 수 있다).

## 클린 skip
`NEWSLETTER_API_KEY` 또는 `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` 미설정 = exit 0
(077 스크립트의 토큰 미설정 관례와 동형 — 워크플로 스텝이 실패로 보이지 않게 한다).

CLI:
  python collect_newsletter_campaigns.py            # 최근 60일 발송 캠페인 적재
  python collect_newsletter_campaigns.py --dry-run   # 구조만 출력(개수·종류), 적재 0
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from typing import Any

import grm_cli

BREVO_BASE = "https://api.brevo.com/v3"
TABLE = "newsletter_campaign_stats"
KST = _dt.timezone(_dt.timedelta(hours=9))
WINDOW_DAYS = 60           # 090 마이그레이션 주석과 같은 창 — "최근에 보낸 호"만 매일 갱신
LIST_PAGE_LIMIT = 50
LIST_MAX_PAGES = 20        # 최대 1000건 — 운영 규모(연 60여 호) 충분

# 캠페인명 → kind·publish_date. 생산자와 같은 패턴을 **읽기만** 한다(정규식을 따로
# 발명하지 않는다) — 어긋나면 조용히 kind='other' 로 접혀 판독 함수의 fb_offered 판정만
# 영향받고(그 캠페인은 weekly 로 안 잡힌다) 적재 자체는 막히지 않는다.
#   · web/newsletter.py idempotency_campaign_name: "GRM Weekly Brief — {date} (No.{n})"
#   · web/announce.py   idempotency_campaign_name: "GRM Update — {ann_id}"
_WEEKLY_NAME_RE = re.compile(r"^GRM Weekly Brief — (\d{4}-\d{2}-\d{2}) \(No\.\d+\)$")
_ANNOUNCE_NAME_PREFIX = "GRM Update"


def kst_now_iso() -> str:
    return _dt.datetime.now(tz=KST).isoformat(timespec="seconds")


def _headers(api_key: str) -> "dict[str, str]":
    return {"api-key": api_key, "accept": "application/json"}


def _int_or_none(value: Any) -> "int | None":
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── 순수 파싱(네트워크 0 — 테스트가 직접 부른다) ───────────────────────────────
def classify_name(name: str) -> "tuple[str, str | None]":
    """캠페인명 → (kind, publish_date). weekly 만 publish_date 를 준다."""
    text = str(name or "")
    m = _WEEKLY_NAME_RE.match(text)
    if m:
        return "weekly", m.group(1)
    if text.startswith(_ANNOUNCE_NAME_PREFIX):
        return "announce", None
    return "other", None


def extract_sent_at(campaign: "dict[str, Any]") -> "str | None":
    """발송 시각(ISO 문자열, 그대로) — `sentDate` 우선, 없으면 `scheduledAt`."""
    for key in ("sentDate", "scheduledAt"):
        v = campaign.get(key)
        if v:
            return str(v)
    return None


def extract_global_stats(campaign: "dict[str, Any]") -> "dict[str, Any]":
    """캠페인 객체(목록 항목 또는 상세)에서 globalStats 를 방어적으로 뽑는다.
    없는 키는 0 이 아니라 None(모듈독스트링 참조)."""
    stats = None
    top = campaign.get("statistics")
    if isinstance(top, dict) and isinstance(top.get("globalStats"), dict):
        stats = top["globalStats"]
    elif isinstance(campaign.get("globalStats"), dict):
        stats = campaign["globalStats"]
    if not isinstance(stats, dict):
        stats = {}
    return {
        "sent": _int_or_none(stats.get("sent")),
        "delivered": _int_or_none(stats.get("delivered")),
        "unique_views": _int_or_none(stats.get("uniqueViews")),
        "unique_clicks": _int_or_none(stats.get("uniqueClicks")),
        "clickers": _int_or_none(stats.get("clickers")),
        "unsubscriptions": _int_or_none(stats.get("unsubscriptions")),
        "hard_bounces": _int_or_none(stats.get("hardBounces")),
        "soft_bounces": _int_or_none(stats.get("softBounces")),
    }


def has_any_global_stats(stats: "dict[str, Any]") -> bool:
    return any(v is not None for v in stats.values())


def extract_links(campaign: "dict[str, Any]") -> "dict[str, int]":
    """linksStats → {url: clicks}(정수만, 방어적). 어느 모양도 아니면 빈 dict."""
    raw = None
    top = campaign.get("statistics")
    if isinstance(top, dict) and isinstance(top.get("linksStats"), dict):
        raw = top["linksStats"]
    elif isinstance(campaign.get("linksStats"), dict):
        raw = campaign["linksStats"]
    if not isinstance(raw, dict):
        return {}
    out: "dict[str, int]" = {}
    for url, clicks in raw.items():
        n = _int_or_none(clicks)
        if isinstance(url, str) and url and n is not None:
            out[url] = n
    return out


def within_window(sent_at: "str | None", *, now: _dt.datetime,
                  window_days: int = WINDOW_DAYS) -> bool:
    """발송 시각이 [now - window_days, now + 1일] 안인가(미래 여유는 시계 오차 흡수용).
    파싱 실패·미발송(None)은 창 밖으로 본다(적재 대상에서 빠진다 — 조용히 0 을 만들지
    않는다, 단지 이 스크립트가 다루는 범위 밖일 뿐)."""
    if not sent_at:
        return False
    text = str(sent_at).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(text)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return (now - dt) <= _dt.timedelta(days=window_days) and dt <= now + _dt.timedelta(days=1)


def build_row(*, campaign_id: int, name: str, sent_at: "str | None",
             stats: "dict[str, Any]", links: "dict[str, int]",
             captured_at: str) -> "dict[str, Any]":
    """적재 행(순수) — PostgREST 로 그대로 보낸다. `links` 는 항상 dict(빈 값도 `{}`,
    None 아님 — 컬럼이 `not null default '{}'::jsonb`)."""
    kind, publish_date = classify_name(name)
    row: "dict[str, Any]" = {
        "campaign_id": int(campaign_id), "name": str(name or ""), "kind": kind,
        "publish_date": publish_date, "sent_at": sent_at, "links": links or {},
        "captured_at": captured_at,
    }
    row.update(stats)
    return row


# ── 네트워크(지연 import — 순수 함수만 쓰는 테스트는 네트워크 0) ───────────────
def fetch_campaign_list(api_key: str, *, timeout: float = 30.0,
                        page_limit: int = LIST_PAGE_LIMIT,
                        max_pages: int = LIST_MAX_PAGES) -> "list[dict[str, Any]]":
    """`sent` 상태의 classic 캠페인 전량(페이지네이션, globalStats 내장 요청)."""
    import requests
    out: "list[dict[str, Any]]" = []
    offset = 0
    for _ in range(max_pages):
        r = requests.get(f"{BREVO_BASE}/emailCampaigns", timeout=timeout,
                         headers=_headers(api_key),
                         params={"type": "classic", "status": "sent", "limit": page_limit,
                                 "offset": offset, "sort": "desc", "statistics": "globalStats"})
        r.raise_for_status()
        body = r.json() or {}
        camps = body.get("campaigns") or []
        out.extend(camps)
        if len(camps) < page_limit:
            break
        offset += page_limit
    return out


def fetch_campaign_detail(api_key: str, campaign_id: Any, *, statistics: str,
                          timeout: float = 30.0) -> "dict[str, Any]":
    import requests
    r = requests.get(f"{BREVO_BASE}/emailCampaigns/{campaign_id}", timeout=timeout,
                     headers=_headers(api_key), params={"statistics": statistics})
    r.raise_for_status()
    return r.json() or {}


def upsert_rows(url: str, key: str, rows: "list[dict[str, Any]]", *,
                timeout: float = 30.0) -> None:
    import requests
    base = grm_cli.normalize_supabase_url(url)
    if not base:
        raise SystemExit("SUPABASE_URL 형식 오류(https:// 로 시작해야 함)")
    r = requests.post(base + "/rest/v1/" + TABLE, params={"on_conflict": "campaign_id"},
                      data=json.dumps(rows), timeout=timeout,
                      headers={"apikey": key, "Authorization": "Bearer " + key,
                               "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=minimal"})
    if r.status_code >= 300:
        # PUBLIC 저장소 — 응답 본문(캠페인명·수치가 섞일 수 있다)은 찍지 않는다.
        raise SystemExit(f"{TABLE} 적재 실패: HTTP {r.status_code}")


def _http_status(exc: Exception) -> "int | None":
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) if resp is not None else None


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="Brevo 캠페인 통계(발송·열람·클릭·해지·링크별 클릭) → Supabase 일별 적재")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="구조(개수·종류)만 출력하고 적재하지 않는다(네트워크 조회는 그대로)")
    args = ap.parse_args(argv)

    api_key = (os.environ.get("NEWSLETTER_API_KEY") or "").strip()
    if not api_key:
        print("NEWSLETTER_API_KEY 미설정 — 캠페인 통계 수집 건너뜀(클린 skip).")
        return 0

    # Supabase 자격도 여기서 먼저 확인한다 — 적재 직전이 아니라 **네트워크 조회 전**에
    # 걸러야, 크리덴셜이 없는 로컬 실행에서 Brevo API 예산을 쓰지 않는다(dry-run 은 예외
    # — 구조만 보고 싶을 때는 Supabase 자격이 없어도 돌아야 한다).
    creds = None if args.dry_run else grm_cli.resolve_supabase_service_credentials(args)
    if not args.dry_run and not creds:
        print("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 미설정 — 캠페인 통계 수집 건너뜀(클린 skip).")
        return 0

    try:
        campaigns = fetch_campaign_list(api_key)
    except Exception as exc:
        status = _http_status(exc)
        print(f"Brevo 캠페인 목록 조회 실패: {type(exc).__name__}"
              + (f" (HTTP {status})" if status else ""), file=sys.stderr)
        return 1

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    windowed = [c for c in campaigns if within_window(extract_sent_at(c), now=now)]

    kind_counts: "dict[str, int]" = {}
    for c in windowed:
        kind, _pub = classify_name(c.get("name") or "")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kind_summary = " · ".join(f"{k} {n}건" for k, n in sorted(kind_counts.items())) or "0건"
    print(f"Brevo 캠페인 {len(windowed)}개 적재 대상(최근 {WINDOW_DAYS}일) — {kind_summary}")

    if args.dry_run:
        print("dry-run — 적재 0")
        return 0
    if not windowed:
        print("적재 대상 0 — 종료")
        return 0

    captured_at = kst_now_iso()
    rows: "list[dict[str, Any]]" = []
    for c in windowed:
        campaign_id = _int_or_none(c.get("id"))
        if campaign_id is None:
            continue                          # id 없는 항목은 적재 불가(PK) — 조용히 건너뜀
        name = str(c.get("name") or "")
        sent_at = extract_sent_at(c)

        stats = extract_global_stats(c)
        if not has_any_global_stats(stats):
            # 목록 응답에 globalStats 가 안 실린 드문 경우만 상세를 한 번 더 부른다.
            try:
                detail = fetch_campaign_detail(api_key, campaign_id, statistics="globalStats")
                stats = extract_global_stats(detail)
            except Exception as exc:
                status = _http_status(exc)
                print(f"캠페인 {campaign_id} globalStats 상세 조회 실패 — 통계 없이 계속: "
                      f"{type(exc).__name__}" + (f" (HTTP {status})" if status else ""),
                      file=sys.stderr)

        links: "dict[str, int]" = {}
        try:
            link_detail = fetch_campaign_detail(api_key, campaign_id, statistics="linksStats")
            links = extract_links(link_detail)
        except Exception as exc:
            status = _http_status(exc)
            print(f"캠페인 {campaign_id} linksStats 상세 조회 실패 — 링크 통계(1클릭 피드백 "
                  f"포함) 없이 계속: {type(exc).__name__}" + (f" (HTTP {status})" if status else ""),
                  file=sys.stderr)

        rows.append(build_row(campaign_id=campaign_id, name=name, sent_at=sent_at,
                              stats=stats, links=links, captured_at=captured_at))

    if not rows:
        print("적재 대상 0(id 없는 항목만 있었음) — 종료")
        return 0

    url, key = creds
    try:
        upsert_rows(url, key, rows)
    except SystemExit:
        raise
    except Exception as exc:
        status = _http_status(exc)
        print(f"{TABLE} 적재 실패: {type(exc).__name__}"
              + (f" (HTTP {status})" if status else ""), file=sys.stderr)
        return 1
    print(f"{TABLE}: {len(rows)}행 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
