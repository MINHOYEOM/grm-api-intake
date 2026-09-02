#!/usr/bin/env python3
"""Cloudflare Web Analytics(RUM) → Supabase 일별 적재 (072).

운영자가 Cloudflare 대시보드를 읽지 않고도 /admin 성장·유입 탭에서 방문·유입 경로를
보게 하는 수집기. 하루 1회 워크플로(grm-rum-analytics.yml)가 호출한다.

## 왜 시간 단위로 받아 KST 로 다시 묶나
Cloudflare GraphQL 의 `date` 차원은 **UTC** 다. 그대로 담으면 09:00 KST 에서 하루가
갈려 같은 화면의 funnel_counts_daily(23:55 KST 스냅샷)와 축이 어긋난다. 그래서
`datetimeHour` 로 받아 +9h 시프트한 뒤 날짜별로 합산한다. 축은 바꾸지 말고 밝힌다 —
저장되는 snap_date 는 전부 KST 다.

## 봇 제외
`bot: 0` 필터가 대시보드의 "Exclude bots = Yes" 와 같은 모집단이다. 이 필터를 빼면
크롤러가 섞여 방문이 몇 배로 부푼다(2026-09-01 실측: 존 지표 1.4k/일 vs 실방문 60/일).

## 첫 실행 전 확인
GraphQL 스키마의 필드명은 토큰 없이 조회할 수 없어 실물 검증을 못 한 채로 작성했다.
`--probe` 로 원시 응답을 찍어 필드명을 먼저 맞춘다(GraphQL 오류는 errors[] 에 이름이
그대로 나온다). 정상 응답을 확인한 뒤에만 적재 모드로 돌린다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import grm_cli

GRAPHQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
KST_OFFSET_HOURS = 9
# 한 번에 받는 버킷 수 상한. 기본 창(7일)이면 시간 168개라 여유가 크다.
GRAPHQL_LIMIT = 10000
# 하루에 저장하는 리퍼러 호스트 상한 — 꼬리를 무한정 담지 않는다(화면은 상위만 쓴다).
REFERRER_CAP = 25

QUERY = """
query RumDaily($accountTag: string!, $siteTag: string!, $start: string!, $end: string!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      totals: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end, bot: 0}
        limit: LIMIT_PLACEHOLDER
        orderBy: [datetimeHour_ASC]
      ) {
        count
        sum { visits }
        dimensions { datetimeHour }
      }
      referrers: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end, bot: 0}
        limit: LIMIT_PLACEHOLDER
        orderBy: [datetimeHour_ASC]
      ) {
        sum { visits }
        dimensions { datetimeHour refererHost }
      }
    }
  }
}
""".replace("LIMIT_PLACEHOLDER", str(GRAPHQL_LIMIT))

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def kst_date(datetime_hour: str) -> str:
    """`2026-09-01T14:00:00Z`(UTC) → `2026-09-01`(KST 날짜).

    datetime 모듈을 쓰지 않는다 — 이 저장소의 순수성 규율(render.py 참조)과 같은 이유로
    now() 를 부르는 문을 열지 않는다. 시각은 입력에만 의존한다.
    """
    date_part, _, time_part = datetime_hour.partition("T")
    parts = date_part.split("-")
    if len(parts) != 3:
        raise SystemExit(f"시각 형식이 아니다: {datetime_hour!r}")
    y, m, d = (int(x) for x in parts)
    hour = int(time_part[:2]) if time_part[:2].isdigit() else 0
    if hour + KST_OFFSET_HOURS < 24:
        return f"{y:04d}-{m:02d}-{d:02d}"
    # 자정을 넘긴 경우만 날짜를 하루 민다(월·연 경계 포함).
    leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
    last = 29 if (m == 2 and leap) else _DAYS_IN_MONTH[m - 1]
    d += 1
    if d > last:
        d, m = 1, m + 1
        if m > 12:
            m, y = 1, y + 1
    return f"{y:04d}-{m:02d}-{d:02d}"


def fetch(token: str, account_tag: str, site_tag: str, start: str, end: str,
          *, timeout: float = 30.0) -> "dict[str, Any]":
    import requests  # 지연 import — 순수 함수만 쓰는 호출부(테스트)는 네트워크 0
    body = {"query": QUERY,
            "variables": {"accountTag": account_tag, "siteTag": site_tag,
                          "start": start, "end": end}}
    r = requests.post(GRAPHQL_ENDPOINT, json=body, timeout=timeout,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json() or {}


def parse(payload: "dict[str, Any]"):
    """GraphQL 응답 → (날짜별 지표, (날짜,호스트)별 방문). 순수.

    응답 구조가 어긋나면 조용히 빈 결과를 돌려주지 않고 즉시 실패한다 — 침묵 실패로
    빈 표가 "방문 0" 처럼 보이는 것을 막는다.
    """
    errors = payload.get("errors")
    if errors:
        raise SystemExit("Cloudflare GraphQL 오류: " + json.dumps(errors, ensure_ascii=False))
    accounts = (((payload.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    if not accounts:
        raise SystemExit("응답에 accounts 가 없다 — accountTag 또는 토큰 권한을 확인하라")
    acct = accounts[0]

    daily: "dict[str, dict[str, int]]" = {}
    for row in (acct.get("totals") or []):
        day = kst_date(str((row.get("dimensions") or {}).get("datetimeHour") or ""))
        bucket = daily.setdefault(day, {"visits": 0, "page_views": 0})
        bucket["visits"] += int(((row.get("sum") or {}).get("visits")) or 0)
        bucket["page_views"] += int(row.get("count") or 0)

    refs: "dict[tuple[str, str], int]" = {}
    for row in (acct.get("referrers") or []):
        dims = row.get("dimensions") or {}
        day = kst_date(str(dims.get("datetimeHour") or ""))
        host = str(dims.get("refererHost") or "").strip() or "(direct)"
        visits = int(((row.get("sum") or {}).get("visits")) or 0)
        if visits <= 0:
            continue
        refs[(day, host)] = refs.get((day, host), 0) + visits
    return daily, refs


def cap_referrers(refs, cap: int = REFERRER_CAP):
    """날짜별 상위 cap 개만 남긴다(방문 내림차순·동률은 호스트 이름순 = 결정론)."""
    by_day: "dict[str, list]" = {}
    for (day, host), visits in refs.items():
        by_day.setdefault(day, []).append((host, visits))
    out = []
    for day in sorted(by_day):
        top = sorted(by_day[day], key=lambda kv: (-kv[1], kv[0]))[:cap]
        out.extend({"snap_date": day, "referer_host": h, "visits": v} for h, v in top)
    return out


def probe_report(payload: "dict[str, Any]") -> str:
    """첫 실행 필드명 검증용 요약. **값은 찍지 않는다 — 구조만 찍는다.**

    ★이 저장소는 PUBLIC 이고 Actions 로그는 누구나 볼 수 있다. 원시 응답을 그대로
    쏟으면 사이트 방문자 수가 공개 로그에 남는다. 필드명 확인에 필요한 것은 값이 아니라
    **키 이름과 GraphQL 오류**뿐이므로 그 둘만 낸다(오류 문구에는 잘못 쓴 필드명이
    그대로 들어 있어 이 목적에 정확히 맞는다).
    """
    lines: "list[str]" = []
    errors = payload.get("errors")
    if errors:
        # 오류는 전문 그대로 — 여기에 우리가 틀린 필드명이 들어 있고, 트래픽 값은 없다.
        lines.append("GraphQL 오류:")
        lines.append(json.dumps(errors, ensure_ascii=False, indent=2)[:4000])
        return "\n".join(lines)
    accounts = (((payload.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    lines.append(f"top-level keys: {sorted(payload.keys())}")
    lines.append(f"accounts: {len(accounts)}")
    if not accounts:
        return "\n".join(lines)
    for group in ("totals", "referrers"):
        rows = accounts[0].get(group)
        if rows is None:
            lines.append(f"{group}: (없음 — 별칭/필드명 불일치 가능)")
            continue
        lines.append(f"{group}: {len(rows)}행")
        if rows:
            first = rows[0] or {}
            lines.append(f"  row keys: {sorted(first.keys())}")
            dims = first.get("dimensions") or {}
            lines.append(f"  dimensions keys: {sorted(dims.keys())}")
            sums = first.get("sum") or {}
            lines.append(f"  sum keys: {sorted(sums.keys())}")
    return "\n".join(lines)


def upsert(url: str, key: str, table: str, rows, on_conflict: str,
           *, timeout: float = 30.0) -> int:
    if not rows:
        return 0
    import requests
    base = grm_cli.normalize_supabase_url(url)
    if not base:
        raise SystemExit("SUPABASE_URL 형식 오류: " + repr(url))
    r = requests.post(base + "/rest/v1/" + table + "?on_conflict=" + on_conflict,
                      data=json.dumps(rows), timeout=timeout,
                      headers={"apikey": key, "Authorization": "Bearer " + key,
                               "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=minimal"})
    if r.status_code >= 300:
        raise SystemExit(f"{table} 적재 실패 {r.status_code}: {r.text[:400]}")
    return len(rows)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Cloudflare RUM → Supabase 일별 적재")
    ap.add_argument("--account-tag", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    ap.add_argument("--site-tag", default=os.environ.get("CLOUDFLARE_RUM_SITE_TAG", ""))
    ap.add_argument("--start", required=True, help="시작 시각(UTC ISO, 예 2026-08-26T00:00:00Z)")
    ap.add_argument("--end", required=True, help="끝 시각(UTC ISO)")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    ap.add_argument("--probe", action="store_true",
                    help="원시 GraphQL 응답만 출력(적재 0) — 첫 실행 필드명 검증용")
    ap.add_argument("--dry-run", action="store_true", help="파싱 결과만 출력(적재 0)")
    args = ap.parse_args(argv)

    token = (os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN") or "").strip()
    if not token:
        # 클린 skip — 토큰 배선 전에는 매일 빨간 실패가 쌓이는 대신 조용히 넘어간다
        # (newsletter precheck 의 "NEWSLETTER_API_KEY 미설정 → 보류" 관례와 동형).
        # 토큰이 있는데 잘못된 경우는 여기가 아니라 GraphQL 오류로 드러난다.
        print("CLOUDFLARE_ANALYTICS_TOKEN 미설정 — 수집 건너뜀(클린 skip). "
              "토큰 배선 후 자동으로 돌기 시작한다.")
        return 0
    if not args.account_tag or not args.site_tag:
        print("account-tag / site-tag 필요", file=sys.stderr)
        return 2

    payload = fetch(token, args.account_tag, args.site_tag, args.start, args.end)
    if args.probe:
        print(probe_report(payload))
        return 0

    daily, refs = parse(payload)
    daily_rows = [{"snap_date": d, "metric": m, "value": v}
                  for d in sorted(daily) for m, v in sorted(daily[d].items())]
    ref_rows = cap_referrers(refs)
    # ★값은 로그에 남기지 않는다 — 이 저장소는 PUBLIC 이라 Actions 로그가 공개다.
    # 숫자는 Supabase 에만 있고, 사람은 /admin 성장·유입 탭에서 본다. 여기서는 "몇 행이
    # 어느 창에 들어갔나"만 남겨 동작 여부를 판정한다(빈 응답은 0행으로 드러난다).
    span = f"{min(daily)}~{max(daily)}" if daily else "(없음)"
    print(f"파싱: {len(daily)}일({span}) · 지표행 {len(daily_rows)} · 리퍼러행 {len(ref_rows)}")
    if args.dry_run:
        return 0

    creds = grm_cli.resolve_supabase_service_credentials(args)
    if not creds:
        print("SUPABASE_URL/SERVICE_ROLE_KEY 미설정 — 적재 불가", file=sys.stderr)
        return 2
    url, key = creds
    n1 = upsert(url, key, "rum_daily", daily_rows, "snap_date,metric")
    n2 = upsert(url, key, "rum_referrer_daily", ref_rows, "snap_date,referer_host")
    print(f"적재 완료: rum_daily {n1}행 · rum_referrer_daily {n2}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
