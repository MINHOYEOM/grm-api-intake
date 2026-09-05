#!/usr/bin/env python3
"""Brevo 리스트 구독자 수 → Supabase 일별 스냅샷 (077 newsletter_subscribers_daily).

"구독자가 늘고 있나"의 정본은 Brevo 다(더블옵트인을 마친 사람만 리스트에 남는다).
지금까지는 사람이 Brevo 화면을 캡처해 세었다(2026-09-01 "6→8"). 이 스크립트가 하루
1회(grm-rum-analytics.yml 의 두 번째 스텝) 리스트의 구독자 수를 읽어 그 날짜 행으로
남긴다. 같은 날 재실행은 덮어쓴다(멱등).

## 어느 수를 "구독자"로 부르나
Brevo `GET /v3/contacts/lists/{id}` 는 `uniqueSubscribers`·`totalSubscribers`·
`totalBlacklisted` 를 준다. Brevo 는 `totalSubscribers`/`totalBlacklisted` 지원 중단을
예고했고 그때는 **0 으로 온다** — 0 을 그대로 저장하면 "구독자 전원 이탈"로 읽힌다(침묵
실패). 그래서 ①`uniqueSubscribers` → ②`totalSubscribers` 순으로 읽고, ③둘 다 없거나
0 이면 `GET /contacts/lists/{id}/contacts?limit=1` 의 `count` 로 교차 확인한다.

## 로그
이 저장소는 PUBLIC 이라 Actions 로그가 공개다. 구독자 **수**는 운영 사실이지 개인정보가
아니므로 찍는다(newsletter.py 의 "개수는 남긴다" 관례). 이메일·이름은 애초에 읽지 않는다.

## 클린 skip
`NEWSLETTER_API_KEY` 미설정 = exit 0 (RUM 수집기의 토큰 미설정 관례와 동형).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any

import grm_cli

BREVO_BASE = "https://api.brevo.com/v3"
TABLE = "newsletter_subscribers_daily"
KST = _dt.timezone(_dt.timedelta(hours=9))


def kst_today() -> str:
    return _dt.datetime.now(tz=KST).date().isoformat()


def _headers(api_key: str) -> "dict[str, str]":
    return {"api-key": api_key, "accept": "application/json"}


def fetch_list(api_key: str, list_id: int, *, timeout: float = 30.0) -> "dict[str, Any]":
    import requests  # 지연 import — 순수 함수만 쓰는 테스트는 네트워크 0
    r = requests.get(f"{BREVO_BASE}/contacts/lists/{int(list_id)}", timeout=timeout,
                     headers=_headers(api_key))
    r.raise_for_status()
    return r.json() or {}


def fetch_contacts_count(api_key: str, list_id: int, *, timeout: float = 30.0) -> "int | None":
    """리스트의 연락처 수(`count`). 리스트 상세의 집계 필드가 비었을 때의 교차 확인용."""
    import requests
    r = requests.get(f"{BREVO_BASE}/contacts/lists/{int(list_id)}/contacts",
                     params={"limit": 1, "offset": 0}, timeout=timeout,
                     headers=_headers(api_key))
    r.raise_for_status()
    body = r.json() or {}
    try:
        return int(body.get("count"))
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> "int | None":
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_counts(body: "dict[str, Any]", *, contacts_count: "int | None" = None) -> "dict[str, Any]":
    """리스트 상세 응답 → 저장 행의 수치 부분(순수 함수).

    `total_subscribers` 는 uniqueSubscribers → totalSubscribers → contacts_count 순.
    둘 다 0 인데 contacts_count 가 양수면 contacts_count 를 택한다(지원 중단 시 0 방어).
    """
    unique = _int_or_none(body.get("uniqueSubscribers"))
    total = _int_or_none(body.get("totalSubscribers"))
    blacklisted = _int_or_none(body.get("totalBlacklisted")) or 0
    chosen = unique if unique is not None else total
    source = "uniqueSubscribers" if unique is not None else ("totalSubscribers" if total is not None else "")
    if (chosen is None or chosen == 0) and contacts_count is not None and contacts_count > 0:
        chosen = contacts_count
        source = "contacts.count"
    if chosen is None:
        raise SystemExit("Brevo 응답에 구독자 수 필드가 없다(uniqueSubscribers/totalSubscribers/count) — "
                         f"keys={sorted(body.keys())}")
    return {"total_subscribers": int(chosen), "total_blacklisted": int(blacklisted),
            "unique_subscribers": unique, "source": source}


def build_row(counts: "dict[str, Any]", *, list_id: int, snap_date: str) -> "dict[str, Any]":
    return {"snap_date": snap_date, "list_id": int(list_id),
            "total_subscribers": counts["total_subscribers"],
            "total_blacklisted": counts["total_blacklisted"],
            "unique_subscribers": counts.get("unique_subscribers")}


def upsert_row(url: str, key: str, row: "dict[str, Any]", *, timeout: float = 30.0) -> None:
    import requests
    base = grm_cli.normalize_supabase_url(url)
    if not base:
        raise SystemExit("SUPABASE_URL 형식 오류: " + repr(url))
    r = requests.post(base + "/rest/v1/" + TABLE, data=json.dumps([row]), timeout=timeout,
                      headers={"apikey": key, "Authorization": "Bearer " + key,
                               "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=minimal"})
    if r.status_code >= 300:
        raise SystemExit(f"{TABLE} 적재 실패 {r.status_code}: {r.text[:400]}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Brevo 리스트 구독자 수 → Supabase 일별 스냅샷")
    ap.add_argument("--list-id", default=os.environ.get("GRM_NEWSLETTER_LIST_ID", ""),
                    help="Brevo 리스트 ID(기본: 환경변수 GRM_NEWSLETTER_LIST_ID)")
    ap.add_argument("--as-of", default=None, help="스냅샷 날짜(YYYY-MM-DD, 기본: KST 오늘)")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--dry-run", action="store_true", help="읽기만 하고 적재하지 않는다")
    args = ap.parse_args(argv)

    api_key = (os.environ.get("NEWSLETTER_API_KEY") or "").strip()
    if not api_key:
        print("NEWSLETTER_API_KEY 미설정 — 구독자 스냅샷 건너뜀(클린 skip).")
        return 0
    try:
        list_id = int(str(args.list_id).strip())
    except ValueError:
        print("GRM_NEWSLETTER_LIST_ID(정수) 필요", file=sys.stderr)
        return 2

    body = fetch_list(api_key, list_id)
    probe = None
    if _int_or_none(body.get("uniqueSubscribers")) in (None, 0) \
            and _int_or_none(body.get("totalSubscribers")) in (None, 0):
        probe = fetch_contacts_count(api_key, list_id)
    counts = parse_counts(body, contacts_count=probe)
    snap_date = args.as_of or kst_today()
    row = build_row(counts, list_id=list_id, snap_date=snap_date)
    # 수는 운영 사실이라 찍는다. 이메일·이름은 읽지 않았다.
    print(f"{snap_date} 리스트 {list_id}: 구독자 {row['total_subscribers']}"
          f" · 수신거부 {row['total_blacklisted']} (출처 {counts['source']})")
    if args.dry_run:
        return 0

    creds = grm_cli.resolve_supabase_service_credentials(args)
    if not creds:
        print("SUPABASE_URL/SERVICE_ROLE_KEY 미설정 — 적재 불가", file=sys.stderr)
        return 2
    url, key = creds
    upsert_row(url, key, row)
    print(f"{TABLE}: 1행 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
